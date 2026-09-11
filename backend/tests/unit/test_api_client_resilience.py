"""Unit tests for Phase 1: API client resilience and safe extraction."""
from unittest.mock import MagicMock, patch
import httpx
import pytest

from gemini_brain.api_client.accutax_client import (
    call_api_resilient,
    extract_data_safe,
    extract_data,
)
from gemini_brain.resilience.outcomes import Outcome


def test_success_false_is_not_unwrapped():
    payload, note = extract_data_safe({"success": False, "message": "no records"})
    assert payload == {"success": False, "message": "no records"}
    assert note == "upstream_success_false"


def test_data_envelope_unwrapped():
    payload, note = extract_data_safe({"success": True, "data": [{"id": 1}]})
    assert payload == [{"id": 1}]
    assert note == ""


def test_results_envelope_unwrapped():
    payload, note = extract_data_safe({"results": [{"total": 500}]})
    assert payload == [{"total": 500}]
    assert note == ""


def test_named_container_envelope_unwrapped():
    payload, note = extract_data_safe({"success": True, "invoices": [{"inv": 123}]})
    assert payload == [{"inv": 123}]
    assert note == ""


def test_named_scalar_beside_success_not_unwrapped():
    payload, note = extract_data_safe({"success": True, "count": 10})
    assert payload == {"success": True, "count": 10}
    assert note == "scalar_beside_success"


def test_extract_data_backward_compat_shim():
    assert extract_data({"success": True, "data": [1, 2, 3]}) == [1, 2, 3]
    # For success=false, extract_data returns the full dict so caller sees it's not a list
    assert extract_data({"success": False, "message": "err"}) == {"success": False, "message": "err"}


@patch("gemini_brain.api_client.accutax_client.get_sync_client")
def test_call_api_resilient_200_ok(mock_get_client):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "data": [{"id": 101}]}
    mock_client.get.return_value = mock_resp
    mock_get_client.return_value = mock_client

    ret = call_api_resilient("/income/list", {}, {})
    assert ret.outcome is Outcome.OK
    assert ret.row_count == 1
    assert ret.rows == [{"id": 101}]
    assert ret.tier == "live_api"
    assert ret.http_status == 200


@patch("gemini_brain.api_client.accutax_client.get_sync_client")
def test_call_api_resilient_404_empty(mock_get_client):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_client.get.return_value = mock_resp
    mock_get_client.return_value = mock_client

    ret = call_api_resilient("/report/ar-aging-summary", {}, {})
    assert ret.outcome is Outcome.EMPTY
    assert ret.reason == "http_404"
    assert ret.http_status == 404


@patch("gemini_brain.api_client.accutax_client.get_sync_client")
def test_call_api_resilient_403_denied(mock_get_client):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden for org"
    mock_client.get.return_value = mock_resp
    mock_get_client.return_value = mock_client

    ret = call_api_resilient("/report/balance-sheet", {}, {})
    assert ret.outcome is Outcome.DENIED
    assert ret.http_status == 403


@patch("gemini_brain.api_client.accutax_client.get_sync_client")
def test_call_api_resilient_timeout(mock_get_client):
    mock_client = MagicMock()
    mock_client.get.side_effect = httpx.TimeoutException("Read timed out")
    mock_get_client.return_value = mock_client

    ret = call_api_resilient("/income/total", {}, {}, attempts=2)
    assert ret.outcome is Outcome.UNAVAILABLE
    assert ret.reason == "timeout"
    assert "exceeded" in ret.detail
