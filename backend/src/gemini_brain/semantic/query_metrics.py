"""
query_metrics.py — The agent's only path to figures.

The model names a view, measures, groupings, filters and a period. This module
checks every name against the Cube catalog, adds the organization grouping and
the date range, and runs one Cube query. Tenant scope comes from the request,
never from the model.

Phase 2 wires this into the agent loop. A ToolInputError goes back to the
model as the tool result so it can correct itself; a CubeError goes to the
user as "figures are temporarily unavailable" and never as an empty answer.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.semantic import cube_client, periods
from gemini_brain.semantic.catalog import AUTO_DIMENSIONS, Catalog, ToolInputError, View, get_catalog

OPERATORS = ("equals", "notEquals", "gt", "gte", "lt", "lte", "contains", "notContains",
             "set", "notSet", "inDateRange", "beforeDate", "afterDate")
MAX_MEASURES, MAX_GROUP_BY, MAX_FILTERS, MAX_LIMIT, DEFAULT_LIMIT = 6, 3, 5, 100, 50
#: A trend returns one row per organization and time bucket, so it may return more rows.
GRANULARITIES, MAX_SERIES_LIMIT = ("week", "month", "quarter", "year"), 300
MAX_FILTER_VALUES = 20
BEGINNING_OF_TIME = "1900-01-01"
CURRENCY_NOTE = ("Figures are per organization, in that organization's currency. "
                 "Never add amounts across currencies.")
MARGIN_NOTE = ("Organizations with no revenue in the period have no margin, "
               "so a margin filter does not list them.")


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
    catalog = get_catalog(organization_ids, subject, deadline)
    query, view, period_note = build_query(params, catalog)
    result = cube_client.load(query, organization_ids=organization_ids, subject=subject, deadline=deadline)
    notes = [period_note, CURRENCY_NOTE]
    if any(f["member"].endswith("_margin_pct") for f in query.get("filters") or []):
        notes.append(MARGIN_NOTE)
    if len(result.rows) >= query["limit"]:
        notes.append(f"Only the first {query['limit']} rows are returned; there may be more. "
                     "Ask for fewer organizations or a shorter period rather than paging.")
    out = {
        "view": view.name,
        "rows": [{k: decimal_text(v) if isinstance(v, Decimal) else v for k, v in r.items()} for r in result.rows],
        "row_count": len(result.rows),
        "data_as_of": result.last_refresh_time,
        "notes": notes,
    }
    out.update(summarize(result.rows, query, view, params.get("granularity")))
    return out


def decimal_text(value: Decimal) -> str:
    """A Decimal as plain digits. Postgres returns an average of exactly 0 as 0E-20; str() keeps that form."""
    text = str(value)
    if "E" not in text and "e" not in text:
        return text
    text = format(value, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"


def _additive(member: str) -> bool:
    """Amounts that may be added up; percentages and ratios may not."""
    return not member.endswith("_pct") and not member.endswith("_ratio")


def _dec(value: Any) -> Optional[Decimal]:
    try:
        return None if value is None else Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def summarize(rows: Sequence[Dict[str, Any]], query: Dict[str, Any], view: View,
              granularity: Optional[str]) -> Dict[str, Any]:
    """Figures worked out in code so the model never adds up rows itself.

    - totals: each amount summed over all rows, only when every row has the same currency;
    - per_organization (trends only): each amount's total, best and worst period per organization.
    """
    measures = [m for m in query["measures"] if _additive(m)]
    # A capped result is missing rows, so any sum of it would be wrong.
    if not rows or not measures or len(rows) >= query.get("limit", len(rows) + 1):
        return {}
    out: Dict[str, Any] = {}
    currencies = {r.get(f"{view.name}.currency") for r in rows}
    if len(currencies) == 1 and None not in currencies:
        out["totals"] = {
            "currency": next(iter(currencies)),
            **{m: str(sum((_dec(r.get(m)) or Decimal(0)) for r in rows)) for m in measures},
        }
    if granularity and view.time_dimension:
        bucket = f"{view.time_dimension}.{granularity}"
        per_org: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            org = str(r.get(f"{view.name}.organization_name") or r.get(f"{view.name}.organization_id"))
            period = str(r.get(bucket) or "")[:10]
            for m in measures:
                value = _dec(r.get(m))
                if value is None:
                    continue
                s = per_org.setdefault(org, {}).setdefault(m, {"total": Decimal(0), "periods_with_data": 0,
                                                               "best": None, "worst": None})
                s["total"] += value
                s["periods_with_data"] += 1
                if s["best"] is None or value > s["best"][1]:
                    s["best"] = (period, value)
                if s["worst"] is None or value < s["worst"][1]:
                    s["worst"] = (period, value)
        out["per_organization"] = {
            org: {m: {"total": str(s["total"]), "periods_with_data": s["periods_with_data"],
                      "best_period": s["best"][0], "best": str(s["best"][1]),
                      "worst_period": s["worst"][0], "worst": str(s["worst"][1])}
                  for m, s in by_measure.items()}
            for org, by_measure in per_org.items()
        }
    return out


def build_query(params: Dict[str, Any], catalog: Catalog) -> tuple[Dict[str, Any], View, str]:
    """The Cube query for a tool call, its view, and the period it covers. Raises ToolInputError."""
    if not isinstance(params, dict):
        raise ToolInputError("Tool input must be an object")
    view = catalog.view(params.get("view"))
    measures = _pick(params.get("measures"), view.measures, "measure", 1, MAX_MEASURES)
    group_by = _pick(params.get("group_by") or [], view.dimensions, "dimension", 0, MAX_GROUP_BY)
    filters = _filters(params.get("filters") or [], view)

    dims = [f"{view.name}.{d}" for d in AUTO_DIMENSIONS] + list(view.always_group_by)
    dims += [d for d in group_by if d not in dims]
    series = bool(params.get("granularity"))
    query: Dict[str, Any] = {"measures": measures, "dimensions": dims, "filters": filters,
                             "limit": _limit(params.get("limit") or (MAX_SERIES_LIMIT if series else DEFAULT_LIMIT),
                                             MAX_SERIES_LIMIT if series else MAX_LIMIT)}
    period_note = _apply_time(query, view, params)

    order = params.get("order") or {}
    if order:
        member = order.get("member") if isinstance(order, dict) else None
        if member not in measures and member not in dims:
            raise ToolInputError("order.member must be one of the chosen measures or group_by members")
        query["order"] = {member: "asc" if order.get("direction") == "asc" else "desc"}
    return query, view, period_note


def _pick(names: Any, allowed: Dict[str, str], kind: str, lo: int, hi: int) -> List[str]:
    if not isinstance(names, list):
        raise ToolInputError(f"{kind}s must be a list")
    names = list(dict.fromkeys(str(n) for n in names))
    if not lo <= len(names) <= hi:
        raise ToolInputError(f"Give between {lo} and {hi} {kind}s")
    unknown = [n for n in names if n not in allowed]
    if unknown:
        raise ToolInputError(f"Unknown {kind}(s) {unknown}. Allowed: {sorted(allowed)}")
    return names


def _filters(raw: Any, view: View) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        raise ToolInputError("filters must be a list")
    if len(raw) > MAX_FILTERS:
        raise ToolInputError(f"At most {MAX_FILTERS} filters")
    out = []
    for f in raw:
        if not isinstance(f, dict):
            raise ToolInputError("Each filter must be an object")
        member, op = f.get("member"), f.get("operator")
        # Organization members are absent from view.dimensions, so scope cannot be filtered here.
        if member not in view.measures and member not in view.dimensions:
            raise ToolInputError(f"Unknown filter member {member!r}")
        if op not in OPERATORS:
            raise ToolInputError(f"Unsupported operator {op!r}. Use one of {list(OPERATORS)}")
        values = [str(v) for v in (f.get("values") or [])]
        if len(values) > MAX_FILTER_VALUES:
            raise ToolInputError(f"At most {MAX_FILTER_VALUES} values per filter")
        if op not in ("set", "notSet") and not values:
            raise ToolInputError(f"The filter on {member} needs values")
        out.append({"member": member, "operator": op, **({"values": values} if values else {})})
    return out


def _limit(value: Any, most: int = MAX_LIMIT) -> int:
    try:
        return max(1, min(int(value or DEFAULT_LIMIT), most))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _apply_time(query: Dict[str, Any], view: View, params: Dict[str, Any]) -> str:
    today = periods.today_in(settings.report_timezone)
    granularity = params.get("granularity")
    if granularity and (view.kind != "flow" or granularity not in GRANULARITIES):
        raise ToolInputError(f"granularity is one of {list(GRANULARITIES)} and applies to period views only")
    try:
        if view.kind == "flow":
            start, end = periods.resolve(params.get("period"), today)
            time_dimension = {"dimension": view.time_dimension, "dateRange": [start.isoformat(), end.isoformat()]}
            if granularity:
                time_dimension["granularity"] = granularity
            query["timeDimensions"] = [time_dimension]
            return f"Period: {start.isoformat()} to {end.isoformat()}" + (f", by {granularity}." if granularity else ".")
        if view.kind == "balance":
            as_of = periods.parse_date(params.get("as_of")) or today
            query["timeDimensions"] = [{"dimension": view.time_dimension,
                                        "dateRange": [BEGINNING_OF_TIME, as_of.isoformat()]}]
            return f"Balances as of {as_of.isoformat()}."
    except ValueError as e:
        raise ToolInputError(str(e)) from e
    if params.get("period") or params.get("as_of"):
        raise ToolInputError(
            f"{view.name} shows current status only and has no history by date. "
            "Ask without a period, or use balance_sheet for a past date."
        )
    return f"Current status on {today.isoformat()}."


def tool_spec(catalog: Catalog) -> Dict[str, Any]:
    """Bedrock Converse toolSpec, for reasoning/bedrock_client.converse_with_tools."""
    return {"toolSpec": {
        "name": "query_metrics",
        "description": (
            "Official financial figures for the organizations selected in this chat. Choose one view, "
            "its measures, optional group_by, filters (thresholds such as pnl.net_margin_pct gt 20 or "
            "pnl.net_profit lt 0) and a period. Results are always per organization.\n\n"
            + catalog.describe()
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": sorted(catalog.views)},
                "measures": {"type": "array", "items": {"type": "string"},
                             "minItems": 1, "maxItems": MAX_MEASURES},
                "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_GROUP_BY},
                "filters": {"type": "array", "maxItems": MAX_FILTERS, "items": {
                    "type": "object",
                    "properties": {
                        "member": {"type": "string"},
                        "operator": {"type": "string", "enum": list(OPERATORS)},
                        "values": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FILTER_VALUES},
                    },
                    "required": ["member", "operator"],
                }},
                "period": {"type": "object", "properties": {
                    "preset": {"type": "string", "enum": list(periods.PRESETS)},
                    "start": {"type": "string", "description": "YYYY-MM-DD, with end, instead of a preset"},
                    "end": {"type": "string"},
                }},
                "as_of": {"type": "string", "description": "YYYY-MM-DD, balance_sheet only"},
                "granularity": {"type": "string", "enum": list(GRANULARITIES),
                                "description": "Split a period view into time buckets, for trends"},
                "order": {"type": "object", "properties": {
                    "member": {"type": "string"},
                    "direction": {"type": "string", "enum": ["asc", "desc"]},
                }},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["view", "measures"],
        }},
    }}
