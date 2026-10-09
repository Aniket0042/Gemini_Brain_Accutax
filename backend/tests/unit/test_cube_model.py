"""Static checks on the Cube data model (Gemini_Brain_Accutax/semantic/model).

These guard tenant isolation and the AI surface without running Cube:
every cube is hidden and carries organization_id, every view exposes the
organization members the client and cube.js rely on, and the views match the
allow-list in cube.js.
"""
import re
from pathlib import Path

import pytest
import yaml

from gemini_brain.semantic.catalog import AUTO_DIMENSIONS, KINDS

SEMANTIC = Path(__file__).resolve().parents[3] / "semantic"
CUBES = {c["name"]: c for f in sorted((SEMANTIC / "model" / "cubes").glob("*.yml"))
         for c in yaml.safe_load(f.read_text(encoding="utf-8"))["cubes"]}
VIEWS = {v["name"]: v for f in sorted((SEMANTIC / "model" / "views").glob("*.yml"))
         for v in yaml.safe_load(f.read_text(encoding="utf-8"))["views"]}


def _queryable_views_in_cube_js() -> set:
    text = (SEMANTIC / "cube.js").read_text(encoding="utf-8")
    block = re.search(r"QUERYABLE_VIEWS = new Set\(\[(.*?)\]\)", text, re.S).group(1)
    return set(re.findall(r"'([a-z_]+)'", block))


def _included(view: dict) -> dict:
    """{member name as exposed by the view: (cube, member)}"""
    out = {}
    for part in view["cubes"]:
        cube = part["join_path"].split(".")[-1]
        for inc in part["includes"]:
            name = inc if isinstance(inc, str) else inc.get("alias") or inc["name"]
            member = inc if isinstance(inc, str) else inc["name"]
            out[name] = (cube, member)
    return out


def _members(cube: dict) -> set:
    return {m["name"] for m in cube.get("dimensions", []) + cube.get("measures", [])}


def test_views_match_the_cube_js_allow_list():
    assert set(VIEWS) == _queryable_views_in_cube_js()


@pytest.mark.parametrize("name", sorted(CUBES))
def test_every_cube_is_hidden(name):
    assert CUBES[name].get("public") is False


@pytest.mark.parametrize("name", sorted(c for c in CUBES if c != "organizations"))
def test_every_fact_cube_has_organization_id_and_the_org_join(name):
    cube = CUBES[name]
    assert "organization_id" in {d["name"] for d in cube["dimensions"]}
    assert any(j["name"] == "organizations" and j["relationship"] == "many_to_one" for j in cube.get("joins", []))
    assert any(d.get("primary_key") for d in cube["dimensions"])


@pytest.mark.parametrize("name", sorted(VIEWS))
def test_every_view_exposes_the_organization_members(name):
    included = _included(VIEWS[name])
    for member in AUTO_DIMENSIONS:
        assert member in included, f"{name} must include {member}"
    if (VIEWS[name].get("meta") or {}).get("kind") == "lookup":
        assert included["organization_id"] == ("organizations", "id")
        return
    assert included["organization_id"][1] == "organization_id"
    assert included["organization_id"][0] != "organizations", "organization_id must come from the fact cube"


@pytest.mark.parametrize("name", sorted(VIEWS))
def test_every_view_member_exists_in_its_cube(name):
    for exposed, (cube, member) in _included(VIEWS[name]).items():
        assert member in _members(CUBES[cube]), f"{name}.{exposed}: {cube}.{member} does not exist"


@pytest.mark.parametrize("name", sorted(VIEWS))
def test_every_view_declares_how_time_applies(name):
    meta = VIEWS[name].get("meta") or {}
    assert meta.get("kind") in (*KINDS, "lookup")
    if meta["kind"] in ("flow", "balance"):
        dimension = meta.get("time_dimension", "")
        assert dimension.startswith(f"{name}.") and dimension.split(".", 1)[1] in _included(VIEWS[name])
    for member in meta.get("always_group_by") or []:
        assert member.startswith(f"{name}.") and member.split(".", 1)[1] in _included(VIEWS[name])


#: {member}, {CUBE}, {CUBE.member}, {cube.member} and {cube.sql()} are Cube references.
_REFERENCE = re.compile(r"\{(CUBE|[a-z_]+)(\.[a-z_]+(\(\))?)?\}")


@pytest.mark.parametrize("name", sorted(CUBES))
def test_no_stray_braces_in_cube_sql(name):
    """Cube's YAML reads {...} as a reference, so a regex like [0-9]{4} would break silently."""
    cube = CUBES[name]
    texts = [cube.get("sql", "")] + [m.get("sql", "") for m in cube.get("measures", []) + cube.get("dimensions", [])]
    texts += [f.get("sql", "") for m in cube.get("measures", []) for f in m.get("filters", [])]
    texts += [j.get("sql", "") for j in cube.get("joins", [])]
    for text in texts:
        leftover = _REFERENCE.sub("", str(text))
        assert "{" not in leftover and "}" not in leftover, f"{name}: stray brace in {text!r}"



def test_inventory_bank_and_branch_views_are_queryable():
    assert {"inventory", "bank_accounts", "bank_transactions", "ledger"} <= set(VIEWS)
    assert "branch_name" in _included(VIEWS["sales"]) and "branch_name" in _included(VIEWS["purchases"])


def test_the_organization_directory_is_a_lookup_the_agent_never_sees():
    from gemini_brain.semantic.catalog import parse_meta
    view = VIEWS["organization_directory"]
    assert view["meta"]["kind"] == "lookup"
    meta = {"cubes": [{"name": "organization_directory", "type": "view", "meta": view["meta"],
                       "measures": [], "dimensions": [{"name": "organization_directory.organization_id"}]}]}
    assert parse_meta(meta).views == {}


def test_sales_and_purchases_break_down_by_product():
    for view, quantity in (("sales", "quantity_sold"), ("purchases", "quantity_purchased")):
        included = _included(VIEWS[view])
        assert {"item_name", "item_sku", quantity} <= set(included), view


PENDING = SEMANTIC / "model_pending"
PENDING_CUBES = {c["name"]: c for f in sorted(PENDING.glob("cubes/*.yml"))
                 for c in yaml.safe_load(f.read_text(encoding="utf-8"))["cubes"]}
PENDING_VIEWS = {v["name"]: v for f in sorted(PENDING.glob("views/*.yml"))
                 for v in yaml.safe_load(f.read_text(encoding="utf-8"))["views"]}


def test_pending_views_are_not_loaded_or_queryable_yet():
    assert not set(PENDING_VIEWS) & set(VIEWS)
    assert not set(PENDING_VIEWS) & _queryable_views_in_cube_js()


@pytest.mark.parametrize("name", sorted(PENDING_CUBES))
def test_pending_cubes_follow_the_same_rules(name):
    cube = PENDING_CUBES[name]
    assert cube.get("public") is False
    assert "organization_id" in {d["name"] for d in cube["dimensions"]}
    assert any(j["name"] == "organizations" for j in cube.get("joins", []))
    assert any(d.get("primary_key") for d in cube["dimensions"])


@pytest.mark.parametrize("name", sorted(PENDING_VIEWS))
def test_pending_views_follow_the_same_rules(name):
    view = PENDING_VIEWS[name]
    included = _included(view)
    for member in AUTO_DIMENSIONS:
        assert member in included, f"{name} must include {member}"
    cubes = {**CUBES, **PENDING_CUBES}
    for exposed, (cube, member) in included.items():
        assert member in _members(cubes[cube]), f"{name}.{exposed}: {cube}.{member} does not exist"
    assert (view.get("meta") or {}).get("kind") in KINDS


def test_payment_views_are_queryable():
    assert {"payments", "payment_settlements"} <= set(VIEWS) & _queryable_views_in_cube_js()
