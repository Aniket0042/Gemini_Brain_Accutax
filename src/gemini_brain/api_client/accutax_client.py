"""
accutax_client.py — HTTP client for calling the Accutax REST API.

Extracted from agents/api_agent.py lines 40-43 and lines 573-650 (_call_api, _extract_data).
Performs live GET requests against the Accutax backend REST API and unrolls response envelopes.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Tuple

import requests
from requests.exceptions import RequestException, Timeout

from gemini_brain.config.constants import HTTP_TIMEOUT
from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.api_client.accutax_client")


def call_api(
    endpoint: str,
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
    base_url: str = "",
    auth_token: str = "",
    timeout: float = HTTP_TIMEOUT,
) -> Tuple[bool, Any]:
    """Execute a GET call against the Accutax backend REST API.

    Parameters
    ----------
    endpoint : str
        Endpoint path, e.g. ``"/income/list"``.
    path_params : Dict[str, Any]
        Path parameter substitutions.
    query_params : Dict[str, Any]
        Query string parameter key-value mapping.
    base_url : str, optional
        Base URL override. If empty, uses ``settings.accutax_base_url``.
    auth_token : str, optional
        Bearer auth token override. If empty, uses ``settings.accutax_auth_token``.
    timeout : float, optional
        HTTP timeout in seconds (default 8.0s).

    Returns
    -------
    Tuple[bool, Any]
        ``(success, data_or_error_string)``
    """
    b_url = base_url or settings.accutax_base_url
    token = auth_token or settings.accutax_auth_token

    # Build URL (substitute path params if any)
    url_path = endpoint
    for key, val in path_params.items():
        url_path = url_path.replace(f":{key}", str(val))
        url_path = url_path.replace(f"{{{key}}}", str(val))

    url = f"{b_url.rstrip('/')}/{url_path.lstrip('/')}"

    # Build headers
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Remove None values from query params
    clean_params = {k: v for k, v in query_params.items() if v is not None}

    # Some endpoints require user_id / userId as a string, not an integer
    for key in ("user_id", "userId"):
        if key in clean_params and not isinstance(clean_params[key], str):
            clean_params[key] = str(clean_params[key])

    logger.info("API CALL: GET %s params=%s", url, clean_params)

    try:
        resp = requests.get(
            url,
            headers=headers,
            params=clean_params,
            timeout=timeout,
        )
        logger.info("API RESPONSE: status=%d url=%s", resp.status_code, url)

        if resp.status_code == 401:
            return (
                False,
                "Authentication required — ACCUTAX_AUTH_TOKEN not configured or expired.",
            )
        if resp.status_code == 404:
            return False, f"Endpoint not found: {endpoint}"
        if resp.status_code >= 400:
            return False, f"API error {resp.status_code}: {resp.text[:300]}"

        data = resp.json()
        return True, data

    except Timeout:
        logger.warning("API TIMEOUT: %s", url)
        return False, f"API request timed out after {timeout}s"
    except RequestException as e:
        logger.warning("API REQUEST ERROR: %s — %s", url, e)
        return False, f"API request failed: {str(e)}"
    except json.JSONDecodeError:
        return False, "API returned non-JSON response"


def extract_data(raw: Any) -> Any:
    """Extract payload from Accutax sendSuccessResponse envelope shapes.

    Standard envelope formats:
      - ``{"success": true, "data": {...}}``
      - ``{"data": [...]}``
      - Bare array / object
    """
    if isinstance(raw, dict):
        # Standard envelope
        if "data" in raw:
            return raw["data"]
        # Success flag with inner key
        if raw.get("success") and len(raw) == 2:
            return next(v for k, v in raw.items() if k != "success")
    return raw
