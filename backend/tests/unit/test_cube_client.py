"""cube_client: polling, errors, tenant scope and number typing, with a mocked Cube."""
import json
import time
from decimal import Decimal

import httpx
import jwt
import pytest

from gemini_brain.semantic import cube_client

SECRET = "x" * 40
Q = {"measures": ["pnl.net_profit"], "dimensions": []}
ANNOTATION = {"measures": {"pnl.net_profit": {"type": "number"}},
              "dimensions": {"pnl.organization_id": {"type": "number"}}}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(cube_client.settings, "cube_api_secret", SECRET)
    yield
    cube_client.set_http_client(None)


def _serve(responses, seen=None):
    calls = iter(responses)

    def handler(request):
        if seen is not None:
            seen.append(request)
        status, body = next(calls)
        return httpx.Response(status, json=body)

    cube_client.set_http_client(httpx.Client(base_url="http://cube", transport=httpx.MockTransport(handler)))


def _load(orgs=(24,), seconds=5.0, query=Q):
    return cube_client.load(query, organization_ids=list(orgs), subject="test", deadline=time.monotonic() + seconds)


def _ok(rows):
    return 200, {"data": rows, "annotation": ANNOTATION, "lastRefreshTime": "2026-10-06T10:00:00.000Z"}


def test_continue_wait_is_retried_not_treated_as_empty():
    _serve([(200, {"error": "Continue wait"}), _ok([{"pnl.organization_id": 24, "pnl.net_profit": "-10.50"}])])
    result = _load()
    assert result.rows == [{"pnl.organization_id": Decimal("24"), "pnl.net_profit": Decimal("-10.50")}]
    assert result.last_refresh_time == "2026-10-06T10:00:00.000Z"


def test_continue_wait_past_deadline_is_an_error():
    _serve([(200, {"error": "Continue wait"})] * 100)
    with pytest.raises(cube_client.CubeError, match="deadline"):
        _load(seconds=0.6)


@pytest.mark.parametrize("status,body", [
    (400, {"error": "'pnl.foo' not found"}),
    (403, {"error": "Invalid token"}),
    (500, {"error": "Internal error"}),
    (200, {"error": "Forbidden: token must be short-lived"}),
    (200, {"no": "data"}),
])
def test_any_error_is_an_error_never_an_empty_result(status, body):
    _serve([(status, body)])
    with pytest.raises(cube_client.CubeError):
        _load()


def test_non_json_is_an_error():
    cube_client.set_http_client(httpx.Client(
        base_url="http://cube", transport=httpx.MockTransport(lambda r: httpx.Response(502, text="Bad gateway"))))
    with pytest.raises(cube_client.CubeError, match="non-JSON"):
        _load()


def test_unreachable_is_an_error():
    def boom(request):
        raise httpx.ConnectError("refused")

    cube_client.set_http_client(httpx.Client(base_url="http://cube", transport=httpx.MockTransport(boom)))
    with pytest.raises(cube_client.CubeError, match="unreachable"):
        _load()


@pytest.mark.parametrize("org", [99, None, "abc"])
def test_row_outside_the_token_is_a_scope_violation(org, caplog):
    _serve([_ok([{"pnl.organization_id": 24, "pnl.net_profit": "1"}, {"pnl.organization_id": org, "pnl.net_profit": "1"}])])
    with pytest.raises(cube_client.CubeScopeViolation):
        _load()
    assert any("cube_scope_violation" in r.getMessage() for r in caplog.records)


def test_org_grouping_is_always_added_and_token_is_scoped_and_short_lived():
    seen = []
    _serve([_ok([])], seen)
    _load(orgs=(25, 24, 25))
    body = json.loads(seen[0].read())
    assert body["query"]["dimensions"][0] == "pnl.organization_id"
    claims = jwt.decode(seen[0].headers["Authorization"], SECRET, algorithms=["HS256"],
                        audience="accutax-cube", issuer="gemini-brain")
    assert claims["organization_ids"] == [24, 25]
    assert claims["exp"] - claims["iat"] == cube_client.TOKEN_TTL_SECONDS
    assert claims["sub"] == "test"


def test_a_query_must_use_exactly_one_view():
    with pytest.raises(cube_client.CubeError, match="exactly one view"):
        _load(query={"measures": ["pnl.revenue", "sales.net_sales"]})


def test_no_organizations_refuses():
    with pytest.raises(cube_client.CubeError, match="organization scope"):
        _load(orgs=())


@pytest.mark.parametrize("secret", ["", "short"])
def test_missing_or_weak_secret_refuses(monkeypatch, secret):
    monkeypatch.setattr(cube_client.settings, "cube_api_secret", secret)
    with pytest.raises(cube_client.CubeError, match="CUBE_API_SECRET"):
        _load()


def test_null_measure_stays_none():
    _serve([_ok([{"pnl.organization_id": "26", "pnl.net_profit": None}])])
    assert _load(orgs=(26,)).rows[0]["pnl.net_profit"] is None


def test_meta_error_is_an_error():
    _serve([(403, {"error": "Invalid token"})])
    with pytest.raises(cube_client.CubeError):
        cube_client.meta(organization_ids=[24], subject="t", deadline=time.monotonic() + 5)
