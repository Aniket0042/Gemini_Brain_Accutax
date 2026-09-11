"""Live Accutax Nest OpenAPI (`GET /api-json`) as the source of truth for REST paths.

The Brain used to ship a hand-written catalog that drifted from the backend
(paths like `/expense/total` and `/report/trial-balance` were never deployed).
This module loads the running Accutax spec and:

- builds the LLM catalog from real GET routes
- drops query params the spec does not declare
- tells retrieval not to HTTP-call a path that is not in the spec
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

import httpx

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.config.accutax_openapi")

OPENAPI_PATH = "/api-json"
SPEC_TTL_SECONDS = 600
_FETCH_TIMEOUT = 4.0

_lock = threading.Lock()
_spec: Optional[Dict[str, Any]] = None
_spec_loaded_at: float = 0.0
_spec_failed: bool = False

_SKIP_PATH_PREFIXES = ("/debug-", "/seed_", "/admin")
_SKIP_PATHS = frozenset({
    "/report/export/pdf",
    "/report/export-return-csv",
})


def reset_cache() -> None:
    """Test helper — drop the in-memory spec."""
    global _spec, _spec_loaded_at, _spec_failed
    with _lock:
        _spec = None
        _spec_loaded_at = 0.0
        _spec_failed = False
    try:
        from gemini_brain.tools.chat_index import reset_index
        reset_index()
    except Exception:
        pass


def spec_generation() -> float:
    """Monotonic timestamp of the last successful spec load (0 if never)."""
    with _lock:
        return _spec_loaded_at


def _stale() -> bool:
    if _spec is None:
        return True
    return (time.monotonic() - _spec_loaded_at) > SPEC_TTL_SECONDS


def cached_spec() -> Optional[Dict[str, Any]]:
    """In-memory spec only — never hits the network. None until startup/tests load it."""
    with _lock:
        return _spec


def fetch_spec(force: bool = False) -> Optional[Dict[str, Any]]:
    """Return the cached OpenAPI document, fetching `{base}/api-json` if needed.

    Returns None when Accutax is unreachable so callers can fall back to the
    static catalog / existing SQL path instead of failing the request.
    """
    global _spec, _spec_loaded_at, _spec_failed
    with _lock:
        if not force and _spec is not None and not _stale():
            return _spec
        if not force and _spec_failed and not _stale():
            return None

    base = (settings.accutax_base_url or "").rstrip("/")
    if not base:
        return None
    url = f"{base}{OPENAPI_PATH}"
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT) as client:
            response = client.get(url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "paths" not in payload:
            raise ValueError("OpenAPI document missing paths")
    except Exception as e:
        logger.warning("Could not load Accutax OpenAPI from %s: %s", url, e)
        with _lock:
            _spec_failed = True
            _spec_loaded_at = time.monotonic()
        return _spec

    with _lock:
        _spec = payload
        _spec_loaded_at = time.monotonic()
        _spec_failed = False
    logger.info(
        "Loaded Accutax OpenAPI (%s paths) from %s",
        len(payload.get("paths") or {}),
        url,
    )
    return payload


def load_spec_for_tests(document: Dict[str, Any]) -> None:
    """Inject a spec without hitting the network."""
    global _spec, _spec_loaded_at, _spec_failed
    with _lock:
        _spec = document
        _spec_loaded_at = time.monotonic()
        _spec_failed = False


def http_paths(spec: Optional[Dict[str, Any]] = None) -> Optional[Set[str]]:
    """Set of documented paths, or None if the spec is not available."""
    doc = spec if spec is not None else cached_spec()
    if not doc:
        return None
    return set((doc.get("paths") or {}).keys())


def path_exists(path: str, spec: Optional[Dict[str, Any]] = None) -> Optional[bool]:
    """True/False when the spec is loaded; None when we cannot tell."""
    paths = http_paths(spec)
    if paths is None:
        return None
    return path in paths


def _operation(spec: Dict[str, Any], path: str, method: str = "get") -> Optional[Dict[str, Any]]:
    item = (spec.get("paths") or {}).get(path) or {}
    body = item.get(method.lower())
    return body if isinstance(body, dict) else None


def _deref_param(spec: Dict[str, Any], param: Dict[str, Any]) -> Dict[str, Any]:
    ref = param.get("$ref")
    if not isinstance(ref, str):
        return param
    name = ref.split("/")[-1]
    return (spec.get("components") or {}).get("parameters", {}).get(name) or param


def query_param_names(path: str, spec: Optional[Dict[str, Any]] = None) -> Optional[Set[str]]:
    doc = spec if spec is not None else cached_spec()
    if not doc:
        return None
    op = _operation(doc, path)
    if not op:
        return None
    names: Set[str] = set()
    for raw in op.get("parameters") or []:
        if not isinstance(raw, dict):
            continue
        param = _deref_param(doc, raw)
        if param.get("in") == "query" and param.get("name"):
            names.add(str(param["name"]))
    return names


def normalize_query_params(
    path: str,
    params: Optional[Dict[str, Any]],
    spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Keep/alias query keys so they match the Accutax OpenAPI operation."""
    src = dict(params or {})
    allowed = query_param_names(path, spec)
    if not allowed:
        return src

    out = dict(src)
    if "min_date" in allowed and "min_date" not in out and out.get("start_date"):
        out["min_date"] = out["start_date"]
    if "max_date" in allowed and "max_date" not in out and (out.get("end_date") or out.get("as_of_date")):
        out["max_date"] = out.get("end_date") or out.get("as_of_date")
    if "end_date" in allowed and "end_date" not in out and out.get("as_of_date"):
        out["end_date"] = out["as_of_date"]
    if "startDate" in allowed and "startDate" not in out and out.get("start_date"):
        out["startDate"] = out["start_date"]
    if "endDate" in allowed and "endDate" not in out and out.get("end_date"):
        out["endDate"] = out["end_date"]
    if "organizationId" in allowed and "organizationId" not in out and out.get("organization_id") is not None:
        out["organizationId"] = out["organization_id"]
    if "userId" in allowed and "userId" not in out and out.get("user_id") is not None:
        out["userId"] = out["user_id"]

    return {k: v for k, v in out.items() if k in allowed and v is not None}


def _skip_path(path: str) -> bool:
    if path in _SKIP_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES)


def operation_chat_meta(op: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Parse `x-accutax-chat` from an OpenAPI operation, if present."""
    if not isinstance(op, dict):
        return None
    raw = op.get("x-accutax-chat")
    if raw is None:
        raw = (op.get("extensions") or {}).get("x-accutax-chat")
    if not isinstance(raw, dict):
        return None
    use_for = raw.get("useFor") or raw.get("use_for") or []
    do_not = raw.get("doNotUseFor") or raw.get("do_not_use_for") or []
    if not isinstance(use_for, list):
        use_for = [str(use_for)]
    if not isinstance(do_not, list):
        do_not = [str(do_not)]
    return {
        "useFor": [str(x) for x in use_for if x],
        "doNotUseFor": [str(x) for x in do_not if x],
    }


def iter_chat_operations(
    spec: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """GET operations that are chatbot-safe: tagged, or untagged `/report/*` drafts.

    Untagged report GETs are included so a new Nest report is discoverable even
    before `@ChatSafe` is added. Writes, exports, and admin routes are skipped.
    """
    doc = spec if spec is not None else cached_spec()
    if not doc:
        return []
    out: List[Dict[str, Any]] = []
    for path, item in (doc.get("paths") or {}).items():
        if _skip_path(path) or not isinstance(item, dict):
            continue
        op = item.get("get")
        if not isinstance(op, dict):
            continue
        meta = operation_chat_meta(op)
        is_report = path.startswith("/report/")
        if meta is None and not is_report:
            continue
        summary = (op.get("summary") or op.get("operationId") or "").strip()
        out.append({
            "path": path,
            "summary": summary,
            "useFor": (meta or {}).get("useFor") or [],
            "doNotUseFor": (meta or {}).get("doNotUseFor") or [],
            "tagged": meta is not None,
            "param_names": sorted(query_param_names(path, doc) or []),
        })
    return out


def _param_line(spec: Dict[str, Any], op: Dict[str, Any]) -> str:
    parts: List[str] = []
    for raw in op.get("parameters") or []:
        if not isinstance(raw, dict):
            continue
        param = _deref_param(spec, raw)
        if param.get("in") != "query" or not param.get("name"):
            continue
        name = param["name"]
        star = "*" if param.get("required") else ""
        parts.append(f"{name}{star}")
    return ", ".join(parts[:16])


def catalog_text(spec: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Compact GET-catalog generated from the live Accutax OpenAPI document."""
    doc = spec if spec is not None else (cached_spec() or fetch_spec())
    if not doc:
        return None
    paths: Dict[str, Any] = doc.get("paths") or {}
    report_lines: List[str] = []
    other_lines: List[str] = []
    for path in sorted(paths):
        if _skip_path(path):
            continue
        op = _operation(doc, path, "get")
        if not op:
            continue
        summary = (op.get("summary") or op.get("operationId") or "").strip()
        params = _param_line(doc, op)
        block = [f"GET {path}"]
        if params:
            block.append(f"  query: {params}")
        if summary:
            block.append(f"  → {summary}")
        meta = operation_chat_meta(op)
        if meta and meta.get("useFor"):
            block.append(f"  USE FOR: {'; '.join(meta['useFor'][:8])}")
        if meta and meta.get("doNotUseFor"):
            block.append(f"  DO NOT USE FOR: {'; '.join(meta['doNotUseFor'][:6])}")
        text = "\n".join(block)
        if path.startswith("/report/"):
            report_lines.append(text)
        elif path.startswith(("/income/", "/expense/", "/dashboard/", "/contact/",
                              "/bank/", "/chart-of-accounts", "/item/", "/accounting/",
                              "/audit-", "/branches", "/cost-centers", "/projects/",
                              "/currency/")):
            other_lines.append(text)

    if not report_lines and not other_lines:
        return None
    sections = ["## AVAILABLE REST API ENDPOINTS",
                "(from Accutax backend OpenAPI GET /api-json — only deployed routes)"]
    if report_lines:
        sections.append("\n### FINANCIAL REPORTS\n" + "\n\n".join(report_lines))
    if other_lines:
        sections.append("\n### OTHER GET ENDPOINTS\n" + "\n\n".join(other_lines))
    return "\n".join(sections)


def refresh_in_background() -> None:
    """Startup hook: pull the spec so the first user query is not the fetch."""
    try:
        fetch_spec(force=True)
    except Exception as e:
        logger.warning("Startup Accutax OpenAPI refresh failed: %s", e)
    try:
        from gemini_brain.tools.chat_index import get_index, reset_index
        reset_index()
        index = get_index()
        logger.info("Chat tool index ready (%s tools)", len(index.by_name))
    except Exception as e:
        logger.warning("Chat tool index warmup failed: %s", e)
