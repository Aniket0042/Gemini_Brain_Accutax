"""Chat-safe tool index: curated REGISTRY + live Accutax OpenAPI.

The LLM router never sees all ~400 Nest paths. This module builds a compact
catalog of tools the chatbot may call:

- Every REGISTRY tool that is still valid (SQL tools always; HTTP tools only
  when the live spec does not say the path is missing).
- Extra GET operations from OpenAPI that are `@ChatSafe` tagged, or untagged
  `/report/*` drafts, when they are not already in REGISTRY.

`x-accutax-chat` useFor / doNotUseFor is merged into the description used for
retrieval and function-calling.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Type

from pydantic import BaseModel

from gemini_brain.config.accutax_openapi import (
    iter_chat_operations,
    path_exists,
    spec_generation,
)
from gemini_brain.tools.handlers import make_api_handler
from gemini_brain.tools.registry import REGISTRY, ToolSpec
from gemini_brain.tools.schemas import ReportAsOfParams, ReportPeriodParams

logger = logging.getLogger("gemini_brain.tools.chat_index")

_lock = threading.Lock()
_index: Optional["ChatIndex"] = None
_index_gen: float = -1.0


@dataclass(frozen=True)
class IndexedTool:
    spec: ToolSpec
    use_for: Tuple[str, ...]
    do_not_use_for: Tuple[str, ...]
    search_text: str
    source: str  # registry | openapi
    tagged: bool


@dataclass
class ChatIndex:
    by_name: Dict[str, IndexedTool]
    by_endpoint: Dict[str, IndexedTool]


def reset_index() -> None:
    global _index, _index_gen
    with _lock:
        _index = None
        _index_gen = -1.0


def _http_endpoint(endpoint: str) -> bool:
    return endpoint.startswith("/")


def _path_to_name(path: str) -> str:
    slug = path.strip("/").replace("/", "_").replace("-", "_")
    slug = re.sub(r"[^a-zA-Z0-9_]", "", slug)
    return slug or "report_unknown"


def _params_for_names(param_names: Sequence[str]) -> Type[BaseModel]:
    names = set(param_names)
    if "as_of_date" in names and "start_date" not in names and "min_date" not in names:
        return ReportAsOfParams
    return ReportPeriodParams


def _compose_description(
    base: str,
    use_for: Sequence[str],
    do_not: Sequence[str],
    summary: str = "",
) -> str:
    parts = [base.strip() or summary.strip()]
    if use_for:
        parts.append("Use for: " + "; ".join(use_for) + ".")
    if do_not:
        parts.append("Do NOT use for: " + "; ".join(do_not) + ".")
    return " ".join(p for p in parts if p)


def _search_blob(
    name: str,
    endpoint: str,
    description: str,
    use_for: Sequence[str],
    do_not: Sequence[str],
) -> str:
    path_words = endpoint.replace("/", " ").replace("-", " ").replace("_", " ")
    name_words = name.replace("_", " ")
    return " ".join(
        [
            name,
            name_words,
            endpoint,
            path_words,
            description,
            " ".join(use_for),
            " ".join(do_not),
        ]
    ).lower()


def _registry_visible(spec: ToolSpec) -> bool:
    """Drop HTTP tools the live spec says do not exist. SQL tools always stay."""
    if not _http_endpoint(spec.endpoint):
        return True
    exists = path_exists(spec.endpoint)
    if exists is False:
        return False
    return True


def _build_index() -> ChatIndex:
    by_name: Dict[str, IndexedTool] = {}
    by_endpoint: Dict[str, IndexedTool] = {}

    for spec in REGISTRY.values():
        if not _registry_visible(spec):
            logger.debug("Hiding registry tool %s — path not in OpenAPI", spec.name)
            continue
        item = IndexedTool(
            spec=spec,
            use_for=(),
            do_not_use_for=(),
            search_text=_search_blob(spec.name, spec.endpoint, spec.description, (), ()),
            source="registry",
            tagged=True,
        )
        by_name[spec.name] = item
        if spec.endpoint:
            by_endpoint[spec.endpoint] = item

    for op in iter_chat_operations():
        path = op["path"]
        use_for = tuple(op.get("useFor") or ())
        do_not = tuple(op.get("doNotUseFor") or ())
        summary = op.get("summary") or ""
        tagged = bool(op.get("tagged"))
        existing = by_endpoint.get(path)
        if existing:
            desc = _compose_description(existing.spec.description, use_for, do_not)
            merged_spec = existing.spec
            if use_for or do_not:
                merged_spec = ToolSpec(
                    name=existing.spec.name,
                    description=desc,
                    endpoint=existing.spec.endpoint,
                    params=existing.spec.params,
                    handler=existing.spec.handler,
                    formatter=existing.spec.formatter,
                    intent=existing.spec.intent,
                    cache_ttl=existing.spec.cache_ttl,
                    timeout=existing.spec.timeout,
                )
            item = IndexedTool(
                spec=merged_spec,
                use_for=use_for,
                do_not_use_for=do_not,
                search_text=_search_blob(
                    merged_spec.name, path, merged_spec.description, use_for, do_not
                ),
                source="registry",
                tagged=tagged or existing.tagged,
            )
            by_name[merged_spec.name] = item
            by_endpoint[path] = item
            continue

        name = _path_to_name(path)
        if name in by_name:
            name = f"openapi_{name}"
        desc = _compose_description(summary or f"GET {path}", use_for, do_not, summary)
        spec = ToolSpec(
            name=name,
            description=desc,
            endpoint=path,
            params=_params_for_names(op.get("param_names") or []),
            handler=make_api_handler(path),
            formatter="row_table",
            intent=3,
        )
        item = IndexedTool(
            spec=spec,
            use_for=use_for,
            do_not_use_for=do_not,
            search_text=_search_blob(name, path, desc, use_for, do_not),
            source="openapi",
            tagged=tagged,
        )
        by_name[name] = item
        by_endpoint[path] = item
        logger.info("Indexed OpenAPI chat tool %s → %s", name, path)

    return ChatIndex(by_name=by_name, by_endpoint=by_endpoint)


def get_index() -> ChatIndex:
    global _index, _index_gen
    gen = spec_generation()
    with _lock:
        if _index is not None and _index_gen == gen:
            return _index
        built = _build_index()
        _index = built
        _index_gen = gen
        return built


def resolve_tool(name: str) -> Optional[ToolSpec]:
    item = get_index().by_name.get(name)
    if item:
        return item.spec
    return REGISTRY.get(name)


def tool_by_endpoint(endpoint: str) -> Optional[ToolSpec]:
    item = get_index().by_endpoint.get(endpoint)
    return item.spec if item else None


def all_indexed_specs() -> Dict[str, ToolSpec]:
    return {name: item.spec for name, item in get_index().by_name.items()}


def declaration_for(spec: ToolSpec, flavor: str = "bedrock") -> Dict[str, Any]:
    schema = spec.params.model_json_schema()
    props: Dict[str, Any] = {}
    for prop_name, prop_def in schema.get("properties", {}).items():
        entry: Dict[str, Any] = {
            "type": prop_def.get("type", "string"),
            "description": prop_def.get("description", ""),
        }
        if "enum" in prop_def:
            entry["enum"] = prop_def["enum"]
        if flavor == "gemini":
            entry["type"] = str(entry["type"]).upper()
        props[prop_name] = entry

    if flavor == "gemini":
        return {
            "name": spec.name,
            "description": spec.description,
            "parameters": {
                "type": "OBJECT",
                "properties": props,
                "required": schema.get("required", []),
            },
        }
    return {
        "toolSpec": {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": props,
                    "required": schema.get("required", []),
                }
            },
        }
    }


def declarations_for(
    names: Optional[Iterable[str]] = None,
    flavor: str = "bedrock",
) -> List[Dict[str, Any]]:
    index = get_index()
    if names is None:
        specs = [item.spec for item in index.by_name.values()]
    else:
        specs = []
        seen = set()
        for name in names:
            if name in seen:
                continue
            item = index.by_name.get(name)
            spec = item.spec if item else REGISTRY.get(name)
            if spec is None:
                continue
            seen.add(name)
            specs.append(spec)
    return [declaration_for(spec, flavor=flavor) for spec in specs]
