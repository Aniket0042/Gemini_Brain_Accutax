"""Unit tests for Phase 0 resilience module: outcomes, errors, messages, envelope."""
import pytest
from gemini_brain.resilience import (
    Outcome,
    Retrieved,
    classify_payload,
    ErrorCode,
    AppError,
    classify_exception,
    notice_for,
    NOTICES,
    normalize_envelope,
    new_request_id,
)
from gemini_brain.api.models import QueryResponse, NoticeSchema, DataSourceSchema


@pytest.mark.parametrize("payload,expected", [
    (None,                                   Outcome.INVALID),
    ("",                                     Outcome.INVALID),
    ({"success": False, "message": "nope"},  Outcome.INVALID),
    ({"error": "boom"},                      Outcome.INVALID),
    ([],                                     Outcome.EMPTY),
    ({"items": []},                          Outcome.EMPTY),
    ({"success": True, "total": 0},          Outcome.EMPTY),
    ({"revenue": 0, "expenses": 0},          Outcome.EMPTY),
    ([{"id": 1}],                            Outcome.OK),
    ({"items": [{"id": 1}]},                 Outcome.OK),
    ({"net_profit": 15000},                  Outcome.OK),
    (42,                                     Outcome.OK),
])
def test_classify(payload, expected):
    res = classify_payload(payload)
    assert res.outcome is expected


def test_classify_wrapper_keys():
    res = classify_payload({"invoices": [{"inv_num": "INV-001"}]})
    assert res.outcome is Outcome.OK
    assert res.row_count == 1
    assert res.rows == [{"inv_num": "INV-001"}]
    assert isinstance(res.payload, dict)  # preserved full envelope for formatters


def test_classify_metadata_only():
    res = classify_payload({"total": 0, "page": 1, "page_size": 50, "status": "ok"})
    assert res.outcome is Outcome.EMPTY


def test_classify_all_blank():
    res = classify_payload({"total": 0, "balance": None, "note": ""})
    assert res.outcome is Outcome.EMPTY


def test_classify_exception():
    assert classify_exception(TimeoutError("Read timed out")) == ErrorCode.UPSTREAM_TIMEOUT
    assert classify_exception(Exception("429 Too Many Requests (Rate limit)")) == ErrorCode.MODEL_RATE_LIMITED
    assert classify_exception(Exception("AccessDenied: org not permitted")) == ErrorCode.TENANT_FORBIDDEN
    assert classify_exception(Exception("psycopg2.OperationalError: server closed the connection")) == ErrorCode.DB_UNAVAILABLE
    assert classify_exception(Exception("Syntax error at or near 'SELECT'")) == ErrorCode.QUERY_FAILED
    assert classify_exception(Exception("botocore.exceptions.ClientError")) == ErrorCode.MODEL_UNAVAILABLE
    assert classify_exception(Exception("503 Service Unavailable")) == ErrorCode.UPSTREAM_UNAVAILABLE
    assert classify_exception(Exception("pydantic validation error")) == ErrorCode.VALIDATION_FAILED
    assert classify_exception(Exception("random unexpected error")) == ErrorCode.INTERNAL_ERROR
    assert classify_exception(AppError(ErrorCode.AUTH_REQUIRED)) == ErrorCode.AUTH_REQUIRED


def test_notice_for():
    n = notice_for("NO_ROWS", subject="overdue invoices")
    assert n["kind"] == "empty"
    assert n["code"] == "NO_ROWS"
    assert "overdue invoices" in n["message"]
    assert not n["retryable"]

    n_timeout = notice_for(ErrorCode.UPSTREAM_TIMEOUT)
    assert n_timeout["kind"] == "degraded"
    assert n_timeout["retryable"] is True


def test_normalize_envelope_guarantees():
    # Empty input
    norm = normalize_envelope({})
    assert isinstance(norm["answer"], str) and len(norm["answer"]) > 0
    assert norm["results"] == []
    assert isinstance(norm["token_usage"], dict)
    assert norm["status"] == "ok"
    assert isinstance(norm["request_id"], str) and len(norm["request_id"]) > 0

    # Pydantic validation must succeed with normalized envelope
    resp = QueryResponse(**norm)
    assert resp.answer == norm["answer"]
    assert resp.results == []
    assert resp.status == "ok"

    # None results coerced to empty list
    norm_none_results = normalize_envelope({"answer": "hello", "results": None})
    assert norm_none_results["results"] == []
    resp2 = QueryResponse(**norm_none_results)
    assert resp2.results == []
