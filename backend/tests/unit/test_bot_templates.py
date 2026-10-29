import pytest

from gemini_brain.bots.templates import all_templates, get
from gemini_brain.bots.watches import all_watches
from gemini_brain.bots.schema import BotDraft


def test_all_templates_valid_and_unique():
    templates = all_templates("vat")
    assert len(templates) == 3

    ids = [t.id for t in templates]
    assert len(ids) == len(set(ids)), "Template IDs must be unique"

    watch_ids = {w.id for w in all_watches()}
    for t in templates:
        assert isinstance(t.draft, BotDraft)
        assert t.draft.agent == "vat"
        assert t.draft.source == f"template:{t.id}"
        assert len(t.draft.watches) > 0
        for wid in t.draft.watches:
            assert wid in watch_ids, f"Watch id {wid} in template {t.id} not found in watches"


def test_get_template():
    t = get("vat_weekly_digest")
    assert t.id == "vat_weekly_digest"
    assert t.title == "Weekly VAT digest"

    with pytest.raises(KeyError):
        get("non_existent_template")


def test_templates_non_vat():
    assert all_templates("ap") == []
