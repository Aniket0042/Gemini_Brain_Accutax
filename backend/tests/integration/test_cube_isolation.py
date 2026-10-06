"""
Tenant isolation suite against a running Cube (guide section 11.2).

Must pass before any deploy. Skipped unless CUBE_ISOLATION_TESTS=1 and the
CUBE_API_URL / CUBE_API_SECRET settings point at the Cube under test. Uses the
test orgs 24 and 25; prints no figures.

    CUBE_ISOLATION_TESTS=1 pytest tests/integration/test_cube_isolation.py -v
"""
import json
import os
import time
import uuid

import httpx
import jwt
import pytest

from gemini_brain.config.settings import settings
from gemini_brain.semantic import cube_client

pytestmark = pytest.mark.skipif(os.getenv("CUBE_ISOLATION_TESTS") != "1",
                                reason="needs a running Cube (CUBE_ISOLATION_TESTS=1)")

ORG_A, ORG_B = 24, 25
VIEWS_FILE_KINDS = {"flow", "balance", "current"}


def _deadline():
    return time.monotonic() + 30


def _load(query, orgs):
    return cube_client.load(query, organization_ids=orgs, subject="isolation-test", deadline=_deadline())


def _token(claims=None, secret=None, **overrides):
    now = int(time.time())
    payload = {"organization_ids": [ORG_A], "iss": settings.cube_jwt_issuer, "aud": settings.cube_jwt_audience,
               "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex, "sub": "isolation-test"}
    payload.update(overrides)
    for key, value in (claims or {}).items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return jwt.encode(payload, secret or settings.cube_api_secret, algorithm="HS256")


def _raw_load(token, query):
    resp = httpx.post(f"{settings.cube_api_url}/cubejs-api/v1/load", json={"query": query},
                      headers={"Authorization": token}, timeout=30)
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return resp.status_code, body


def _orgs(rows, view):
    return {int(r[f"{view}.organization_id"]) for r in rows}


# T1
def test_rows_only_for_the_token_org():
    rows = _load({"measures": ["pnl.net_profit"], "dimensions": ["pnl.organization_id"]}, [ORG_A]).rows
    assert _orgs(rows, "pnl") <= {ORG_A}


# T2
def test_naming_another_org_returns_nothing():
    other = _load({"measures": ["pnl.revenue"], "dimensions": ["pnl.organization_name"]}, [ORG_B]).rows
    assert other, "org B needs ledger data for this test"
    name = other[0]["pnl.organization_name"]
    rows = _load({"measures": ["pnl.revenue"], "dimensions": ["pnl.organization_name"],
                  "filters": [{"member": "pnl.organization_name", "operator": "equals", "values": [name]}]}, [ORG_A]).rows
    assert rows == []


# T3
def test_or_filter_cannot_widen_scope():
    rows = _load({"measures": ["pnl.net_profit"], "dimensions": ["pnl.organization_id"],
                  "filters": [{"or": [{"member": "pnl.organization_id", "operator": "set"},
                                      {"member": "pnl.net_profit", "operator": "lt", "values": ["0"]}]}]},
                 [ORG_A]).rows
    assert _orgs(rows, "pnl") <= {ORG_A}


# T4
@pytest.mark.parametrize("kind,member", [
    ("measures", "gl_lines.revenue"),
    ("dimensions", "organizations.name"),
    ("measures", "sales_lines.gross_sales"),
])
def test_raw_cubes_are_refused(kind, member):
    status, body = _raw_load(_token(), {kind: [member]})
    assert status != 200 or "error" in body


# T6
def test_every_view_and_measure_is_scoped():
    meta = cube_client.meta(organization_ids=[ORG_A], subject="isolation-test", deadline=_deadline())
    views = [c for c in meta["cubes"] if c.get("type") == "view"]
    assert views, "no views in /v1/meta"
    for view in views:
        name = view["name"]
        kind = (view.get("meta") or {}).get("kind")
        assert kind in VIEWS_FILE_KINDS, f"{name} has no meta.kind"
        for measure in view["measures"]:
            query = {"measures": [measure["name"]], "dimensions": [f"{name}.organization_id"]}
            rows = _load(query, [ORG_A]).rows
            assert _orgs(rows, name) <= {ORG_A}, measure["name"]
        resp = httpx.get(f"{settings.cube_api_url}/cubejs-api/v1/sql",
                         params={"query": json.dumps({"measures": [view["measures"][0]["name"]]})},
                         headers={"Authorization": _token()}, timeout=30)
        assert resp.status_code == 200
        sql = json.dumps(resp.json())
        assert "organization_id" in sql and str(ORG_A) in sql, f"{name}: no tenant filter in compiled SQL"


# T7, T8, T9
@pytest.mark.parametrize("token_kwargs", [
    {"claims": {"organization_ids": []}},
    {"claims": {"organization_ids": None}},
    {"claims": {"organization_ids": [str(ORG_A)]}},
    {"claims": {"exp": int(time.time()) + 3600}},
    {"claims": {"exp": int(time.time()) - 10, "iat": int(time.time()) - 70}},
    {"claims": {"aud": "someone-else"}},
    {"claims": {"iss": "someone-else"}},
    {"secret": "y" * 40},
])
def test_bad_tokens_are_refused(token_kwargs):
    status, body = _raw_load(_token(**token_kwargs), {"measures": ["pnl.revenue"]})
    assert status != 200 or "error" in body


def test_no_token_is_refused():
    resp = httpx.post(f"{settings.cube_api_url}/cubejs-api/v1/load",
                      json={"query": {"measures": ["pnl.revenue"]}}, timeout=30)
    assert resp.status_code in (401, 403)


# T10
def test_two_orgs_return_only_those_two():
    rows = _load({"measures": ["pnl.net_profit"], "dimensions": ["pnl.organization_id"]}, [ORG_A, ORG_B]).rows
    assert _orgs(rows, "pnl") <= {ORG_A, ORG_B}
