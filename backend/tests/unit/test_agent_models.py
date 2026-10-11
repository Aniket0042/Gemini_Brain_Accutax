"""Model picker (plan phase 2): which Claude models a user can pick, how the pick reaches the agent, and
prompt caching per model."""
import pytest

from gemini_brain.agent import loop, models, preview
from gemini_brain.api import routes
from gemini_brain.api.auth import CurrentUser
from gemini_brain.config.constants import HAIKU3_ID, HAIKU45_ID
from gemini_brain.config.settings import settings
from gemini_brain.reasoning import bedrock_client

ALL = "sonnet-5,haiku-4.5,haiku-3"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(loop, "_no_cache", set())
    monkeypatch.setattr(loop, "redact_pii", lambda text: (text, {}))


@pytest.fixture
def offer(monkeypatch):
    def _offer(keys=ALL, users=""):
        monkeypatch.setattr(settings, "agent_models", keys)
        monkeypatch.setattr(settings, "agent_model_users", users)
    _offer()
    return _offer


def test_only_the_default_is_offered_out_of_the_box(monkeypatch):
    monkeypatch.setattr(settings, "agent_models", "sonnet-5")
    assert [m.key for m in models.enabled()] == ["sonnet-5"]


def test_offered_models_keep_the_default_first_and_skip_unknown_keys(offer):
    offer("haiku-3, HAIKU-4.5 ,opus-5")
    assert [m.key for m in models.enabled()] == ["sonnet-5", "haiku-4.5", "haiku-3"]


def test_the_allowlist_limits_the_other_models(offer):
    offer(ALL, "Boss@Example.com, 42")
    assert [m.key for m in models.allowed(7, "someone@example.com")] == ["sonnet-5"]
    assert len(models.allowed(7, "boss@example.com")) == 3
    assert len(models.allowed(42, None)) == 3
    offer(ALL, "")
    assert len(models.allowed(7, "anyone@example.com")) == 3


@pytest.mark.parametrize("requested, saved, expected", [
    ("haiku-4.5", None, "haiku-4.5"),
    ("auto", "haiku-3", "haiku-3"),
    (None, "haiku-3", "haiku-3"),
    ("auto", None, "sonnet-5"),
    ("opus-5", None, "sonnet-5"),          # unknown key
])
def test_resolve(requested, saved, expected):
    assert models.resolve(requested, ["sonnet-5", "haiku-4.5", "haiku-3"], saved).key == expected


def test_resolve_never_returns_a_model_the_user_may_not_pick():
    assert models.resolve("haiku-4.5", ["sonnet-5"]).key == "sonnet-5"
    assert models.resolve("auto", ["sonnet-5"], saved="haiku-3").key == "sonnet-5"


def test_caching_is_per_model():
    assert models.caches(HAIKU45_ID) and not models.caches(HAIKU3_ID)
    assert loop._cached("sys", [{"toolSpec": {}}], HAIKU3_ID) == ([{"text": "sys"}], [{"toolSpec": {}}])
    sys_blocks, tools = loop._cached("sys", [{"toolSpec": {}}], HAIKU45_ID)
    assert sys_blocks[-1] == loop._CACHE_POINT and tools[-1] == loop._CACHE_POINT


def test_a_haiku_3_answer_sends_no_cache_points_and_leaves_caching_on_for_others(monkeypatch):
    sent = []

    class Adapter:
        def __init__(self, model_id, label=""):
            self.model_id = model_id

        def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
            sent.append((self.model_id, system, tools_))
            return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": "AED 1."}]}}}

        def get_token_usage(self):
            return {}

    monkeypatch.setattr(loop, "BedrockAdapter", Adapter)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "query_metrics"}}])
    monkeypatch.setattr(loop.law, "answer", lambda *a, **k: None)
    loop.run_agent("Revenue?", [24], {}, model_id=HAIKU3_ID, answer_model_id=HAIKU3_ID)
    loop.run_agent("Revenue?", [24], {}, model_id=HAIKU45_ID, answer_model_id=HAIKU45_ID)
    (m3, sys3, tools3), (m45, sys45, tools45) = sent
    assert m3 == HAIKU3_ID and loop._CACHE_POINT not in sys3 and loop._CACHE_POINT not in tools3
    assert m45 == HAIKU45_ID and sys45[-1] == loop._CACHE_POINT and tools45[-1] == loop._CACHE_POINT


def _fake_agent(monkeypatch, seen, route="tools"):
    def run(question, orgs, meta, **kw):
        seen.update(kw)
        return loop.AgentResult(answer="AED 10.", status="ok", route=route)
    monkeypatch.setattr(loop, "run_agent", run)


def test_the_picked_model_answers_and_is_shown(monkeypatch):
    seen = {}
    _fake_agent(monkeypatch, seen)
    out = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=1,
                         model_key="haiku-4.5", allowed_models=["sonnet-5", "haiku-4.5"])
    assert seen["model_id"] == seen["answer_model_id"] == HAIKU45_ID
    assert out["policy"] == {"model": "haiku-4.5", "model_label": "Claude Haiku 4.5", "auto": False}
    assert out["routing_info"]["bedrock_model"] == HAIKU45_ID


def test_the_default_keeps_the_agent_settings_and_the_product_name_without_a_picker(monkeypatch):
    seen = {}
    _fake_agent(monkeypatch, seen)
    out = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=1, model_key="haiku-4.5")
    assert "model_id" not in seen and "answer_model_id" not in seen   # not allowed: the default
    assert out["policy"]["model"] == "sonnet-5" and out["policy"]["model_label"] == preview.PRIMARY_LABEL


def test_law_answers_say_so_whatever_was_picked(monkeypatch):
    _fake_agent(monkeypatch, {}, route="law")
    out = preview.answer("VAT on exports?", [24], lambda: {}, session_id=None, user_id=1,
                         model_key="haiku-3", allowed_models=["sonnet-5", "haiku-3"])
    assert out["policy"]["model_label"] == f"{preview.PRIMARY_LABEL} · UAE VAT law"
    assert out["routing_info"]["bedrock_model"] is None


def test_auto_keeps_the_threads_last_model_and_the_turn_saves_it(monkeypatch):
    from gemini_brain.memory import session_memory as memory
    state = {preview.MODEL_STATE_KEY: "haiku-3"}
    monkeypatch.setattr(memory, "get_session_record", lambda sid, db_name="": {
        "user_id": 1, "organization_ids": [24], "conversation_state": dict(state)})
    monkeypatch.setattr(memory, "get_history_by_session", lambda *a, **k: [])
    monkeypatch.setattr(memory, "get_state_by_session", lambda *a, **k: dict(state))
    monkeypatch.setattr(memory, "update_state_by_session", lambda sid, new, db_name="": state.update(new))
    monkeypatch.setattr(memory, "ensure_session", lambda *a, **k: True)
    monkeypatch.setattr(memory, "save_message_by_session", lambda *a, **k: None)
    monkeypatch.setattr(preview, "_context_window", lambda *a, **k: None)
    seen = {}
    _fake_agent(monkeypatch, seen)
    keys = ["sonnet-5", "haiku-4.5", "haiku-3"]
    preview.answer("and last quarter?", [24], lambda: {}, session_id="s", user_id=1, model_key="auto", allowed_models=keys)
    assert seen["model_id"] == HAIKU3_ID
    preview.answer("Revenue?", [24], lambda: {}, session_id="s", user_id=1, model_key="haiku-4.5", allowed_models=keys)
    assert state[preview.MODEL_STATE_KEY] == "haiku-4.5"


def test_the_catalog_lists_what_the_user_may_pick(offer):
    user = CurrentUser(user_id=9, email="u@example.com", allowed_org_ids=[24])
    catalog = routes.list_model_catalog(current_user=user)
    assert [m.key for m in catalog.models] == ["sonnet-5", "haiku-4.5", "haiku-3"]
    assert not catalog.picker_hidden and catalog.default_model == "sonnet-5"
    assert [m.description.split(".")[0] for m in catalog.models] == ["Balanced", "Fast", "Cheapest"]
    offer(ALL, "someone-else@example.com")
    assert routes.list_model_catalog(current_user=user).picker_hidden


def test_the_routes_pass_the_pick_and_the_allowed_keys(offer):
    from gemini_brain.api.models import QueryRequest
    user = CurrentUser(user_id=9, email="u@example.com", allowed_org_ids=[24])
    choice = routes._model_choice(QueryRequest(query="x", model="haiku-3", organization_id=24), user)
    assert choice == {"model_key": "haiku-3", "allowed_models": ["sonnet-5", "haiku-4.5", "haiku-3"]}


def test_the_adapter_drops_cache_points_on_refusal_without_blanking_the_prompt(monkeypatch):
    calls = []

    class Client:
        def converse(self, **kw):
            calls.append(kw)
            if any("cachePoint" in b for b in kw["system"]):
                raise RuntimeError("AccessDeniedException: You invoked an unsupported model or your request "
                                   "did not allow prompt caching.")
            return {"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {}}

    monkeypatch.setattr(bedrock_client, "get_bedrock_client", lambda: Client())
    monkeypatch.setattr(bedrock_client, "retry_with_backoff", lambda fn: fn())
    monkeypatch.setattr(bedrock_client.BedrockAdapter, "_no_cache_models", set())
    point = {"cachePoint": {"type": "default"}}
    adapter = bedrock_client.BedrockAdapter("model-without-cache")
    adapter.converse_with_tools([{"text": "rules"}, point], [{"role": "user", "content": [{"text": "q"}]}],
                                [{"toolSpec": {"name": "t"}}, point])
    retry = calls[-1]
    assert retry["system"] == [{"text": "rules"}]
    assert retry["toolConfig"]["tools"] == [{"toolSpec": {"name": "t"}}]
    assert bedrock_client.BedrockAdapter._no_cache_models == {"model-without-cache"}
    assert "model-without-cache" not in {"other"} and bedrock_client.BedrockAdapter("other")._build_system_array("x") != []
