"""org_directory: organization names and currencies for the agent come from Cube."""
from gemini_brain.semantic import org_directory
from gemini_brain.semantic.cube_client import CubeResult


def _cube(rows, seen):
    def load(query, *, organization_ids, subject, deadline):
        seen.update(query=query, orgs=list(organization_ids), subject=subject)
        return CubeResult(rows=[{f"organization_directory.{k}": v for k, v in r.items()} for r in rows], query=query,
                          elapsed_ms=1)
    return load


def test_names_and_currencies_come_from_cube(monkeypatch):
    seen = {}
    monkeypatch.setattr(org_directory.cube_client, "load", _cube(
        [{"organization_id": 24, "organization_name": "Arjun Trading", "currency": "AED"}], seen))
    assert org_directory.lookup([24], subject="agent-org-meta:7") == {24: {"name": "Arjun Trading", "currency": "AED"}}
    assert seen["orgs"] == [24] and seen["query"]["dimensions"][0] == "organization_directory.organization_id"


def test_a_shared_name_gets_the_id_and_a_missing_org_no_currency(monkeypatch):
    monkeypatch.setattr(org_directory.cube_client, "load", _cube(
        [{"organization_id": 27, "organization_name": "Org", "currency": "AED"},
         {"organization_id": 29, "organization_name": "Org", "currency": "USD"}], {}))
    assert org_directory.lookup([27, 29, 31], subject="s") == {
        27: {"name": "Org (ID 27)", "currency": "AED"},
        29: {"name": "Org (ID 29)", "currency": "USD"},
        31: {"name": "Organization 31", "currency": ""},
    }


def test_no_organizations_needs_no_query(monkeypatch):
    monkeypatch.setattr(org_directory.cube_client, "load", lambda *a, **k: 1 / 0)
    assert org_directory.lookup([], subject="s") == {}


def test_the_agent_routes_look_names_up_in_cube_not_accutax(monkeypatch):
    from gemini_brain.api import routes
    from gemini_brain.api.auth import CurrentUser
    calls = []
    monkeypatch.setattr(org_directory, "lookup", lambda orgs, subject: calls.append((orgs, subject)) or {})
    monkeypatch.setattr(routes, "fetch_accutax_accessible_orgs", lambda *a: 1 / 0)
    meta = routes._agent_org_meta([24], CurrentUser(user_id=5, email="", allowed_org_ids=[24]))
    assert calls == []          # nothing looked up until the agent asks
    assert meta() == {} and calls == [([24], "agent-org-meta:5")]
