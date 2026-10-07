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
    assert included["organization_id"][1] == "organization_id"
    assert included["organization_id"][0] != "organizations", "organization_id must come from the fact cube"


@pytest.mark.parametrize("name", sorted(VIEWS))
def test_every_view_member_exists_in_its_cube(name):
    for exposed, (cube, member) in _included(VIEWS[name]).items():
        assert member in _members(CUBES[cube]), f"{name}.{exposed}: {cube}.{member} does not exist"


@pytest.mark.parametrize("name", sorted(VIEWS))
def test_every_view_declares_how_time_applies(name):
    meta = VIEWS[name].get("meta") or {}
    assert meta.get("kind") in KINDS
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
