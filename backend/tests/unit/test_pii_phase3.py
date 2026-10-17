"""PII plan phase 3: the report narrator masked like the agent, and public FTA contacts left visible."""
import json

import pytest

from gemini_brain.artifacts import narrator
from gemini_brain.privacy import dictionary, masking, policy, sentinel
from gemini_brain.privacy.scrubber import Scrubber
from gemini_brain.privacy.vault import Vault

CUSTOMER = "Zenith Foods FZE"
ORG = "Acme Holdings Org"


@pytest.fixture(autouse=True)
def _fresh_request():
    yield
    masking._current.set(None)                      # each test is its own request


@pytest.fixture
def names(monkeypatch):
    d = dictionary.Dictionary([dictionary.Entry(CUSTOMER, "CUSTOMER", policy.normalize_name(CUSTOMER)),
                               dictionary.Entry(ORG, "ORG", policy.normalize_name(ORG))])
    monkeypatch.setattr(dictionary, "for_orgs", lambda *a, **k: d)
    return d


def _brief():
    b = narrator.Brief(kind="general", context=["Report: Top customers", f"Entity: {ORG}"])
    b.add(f"{CUSTOMER} is the largest customer at AED 1,250,000.00, 41% of the top 5.")
    b.add("Revenue was AED 3,050,000.00.")
    return b


def _reply(text):
    return json.dumps({"headline": text, "summary": [text + " [F1]"], "drivers": ["Revenue held up [F2]."],
                       "risks": [], "actions": ["Review exposure to [CUSTOMER_1] [F1]."]})


def test_public_government_emails_stay_and_private_ones_are_masked():
    assert policy.public_contact("EMAIL", "info@tax.gov.ae") and policy.public_contact("EMAIL", "x@mof.gov.ae")
    assert not policy.public_contact("EMAIL", "ahmed@alnoor.ae") and not policy.public_contact("EMAIL", "a@gov.ae.evil.com")
    s = Scrubber(Vault())
    assert s.text("Write to info@tax.gov.ae or ahmed@alnoor.ae.") == "Write to info@tax.gov.ae or [EMAIL_1]."
    found = sentinel.scan("Contact the FTA at info@tax.gov.ae.", [])["found"]
    assert found == {}


def test_narrator_on_sends_placeholders_and_reads_back_real_names(monkeypatch, names):
    from gemini_brain.config.settings import settings
    monkeypatch.setattr(settings, "pii_mode", "on")
    masking.Masking.start("on", [24], {24: {"name": ORG}}, subject="t")      # what the agent did earlier
    sent = {}

    def model(system, user):
        sent.update(system=system, user=user)
        return _reply('[CUSTOMER_1] leads [ORG_1] sales, a "key" account')

    monkeypatch.setattr(narrator, "_model_call", model)
    section = narrator._narrate_with_model(_brief(), f"top customers of {ORG}", timeout=5)
    assert CUSTOMER not in sent["user"] and ORG not in sent["user"]
    assert "[CUSTOMER_1]" in sent["user"] and "[ORG_1]" in sent["user"] and "Privacy placeholders" in sent["system"]
    assert "AED 1,250,000.00" in sent["user"]                                    # figures stay (D1)
    assert section.headline == f'{CUSTOMER} leads {ORG} sales, a "key" account'   # JSON escaping kept intact
    assert section.actions == [f"Review exposure to {CUSTOMER}."]


def test_narrator_reuses_the_agents_placeholders(monkeypatch, names):
    from gemini_brain.config.settings import settings
    monkeypatch.setattr(settings, "pii_mode", "on")
    m = masking.Masking.start("on", [24], {24: {"name": ORG}}, subject="t")
    m.text(f"Earlier the agent saw {ORG} and {CUSTOMER}")                         # ORG_1, CUSTOMER_1 already given
    assert masking.current("on") is m


def test_narrator_shadow_sends_originals_and_reports_the_masked_prompt(monkeypatch, names, tmp_path):
    from gemini_brain.config.settings import settings
    monkeypatch.setattr(settings, "pii_mode", "shadow")
    log = tmp_path / "s.jsonl"
    monkeypatch.setattr(sentinel.settings, "pii_sentinel", "report")
    monkeypatch.setattr(sentinel.settings, "pii_sentinel_log", str(log))
    masking.Masking.start("shadow", [24], {24: {"name": ORG}}, subject="t")
    sent = {}
    monkeypatch.setattr(narrator, "_model_call", lambda system, user: sent.update(user=user) or _reply("Fine"))
    narrator._narrate_with_model(_brief(), "top customers", timeout=5)
    assert CUSTOMER in sent["user"]
    [entry] = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert entry["purpose"] == "report_narration:masked" and entry["found"] == {}


def test_narrator_on_without_masking_sends_nothing(monkeypatch):
    from gemini_brain.config.settings import settings
    monkeypatch.setattr(settings, "pii_mode", "on")

    def boom(*a, **k):
        raise RuntimeError("masking broke")

    monkeypatch.setattr(masking, "current", boom)
    monkeypatch.setattr(narrator, "_model_call", lambda *a: pytest.fail("nothing may be sent unmasked"))
    assert narrator._narrate_with_model(_brief(), "q", timeout=5) is None         # the template is used instead


def test_off_leaves_the_narrator_as_it_was(monkeypatch):
    from gemini_brain.config.settings import settings
    monkeypatch.setattr(settings, "pii_mode", "off")
    sent = {}
    monkeypatch.setattr(narrator, "_model_call", lambda system, user: sent.update(user=user, system=system)
                        or _reply("Fine"))
    narrator._narrate_with_model(_brief(), "q", timeout=5)
    assert CUSTOMER in sent["user"] and "Privacy placeholders" not in sent["system"]


def test_a_new_request_does_not_inherit_the_previous_masking():
    masking.Masking.start("on", [], {}, subject="t")
    masking.Masking.start("off", [], {}, subject="t")
    assert masking._current.get() is None
