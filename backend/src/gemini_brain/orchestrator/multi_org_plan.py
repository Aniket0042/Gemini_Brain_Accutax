"""
multi_org_plan.py — Plan a multi-organization question once, then fetch it per org.

Routing each organization separately cost about three model calls per org and
let organizations drift onto different endpoints for the same question (one
org answered "cash balance" from receivables, another from bank accounts).
Here the question is routed once, against the first organization, and every
organization then runs only the data fetch for that same selection.

The selection carries over between organizations because the org is never
taken from it: `_retrieve` pins every REST call to the org it is given
(force_org_params), and SQL reports and functions receive the org as their
own argument. Each fetch still passes the runner's allow-list check first.
"""
from __future__ import annotations

import functools
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gemini_brain.config.constants import LEFT_PATH_TYPES
from gemini_brain.endpoints.endpoint_selector import select_endpoint
from gemini_brain.memory.conversation_window import is_conversation_meta_query
from gemini_brain.observability import QueryTrace
from gemini_brain.orchestrator.multi_org_metrics import (
    BY_KEY,
    METRIC_FOR_REPORT,
    SCORECARD_METRICS,
    contact_totals_selection,
    is_scorecard_question,
    match_metrics,
    match_series_metrics,
    metrics_selections,
    selection_key,
    series_selection,
    unsupported_note,
    unsupported_terms,
)
from gemini_brain.orchestrator.multi_org_lists import (
    cross_org_unsupported,
    document_list_selection,
    unapplied_filters,
    unavailable_filters,
)
from gemini_brain.orchestrator.multi_org_present import OVERLAP, choose_shape, is_list_question
from gemini_brain.orchestrator.gemini_brain_runner import _how_to_guide_section
from gemini_brain.resilience import Outcome
from gemini_brain.router.fast_router import fast_route
from gemini_brain.classification.intent_classifier import classify_intent
from gemini_brain.tools.formatters import render_blocks
from gemini_brain.tools.registry import tool_spec_for_endpoint

logger = logging.getLogger("gemini_brain.orchestrator.multi_org_plan")

#: Plan kinds.
DATA = "data"      # fetch the same selection for every organization
DIRECT = "direct"  # no data needed (how-to, FAQ, chat meta): answer once
UNSUPPORTED = "unsupported"  # only figures no report can compute: say so, fetch nothing
REPLY = "reply"  # small talk ("hi", "thanks"): a fixed reply, no data and no model call

#: Greetings and thanks with nothing else in them. They cost three model calls
#: (classify, then answer) for a one-line reply.
_SMALL_TALK = re.compile(
    r"^\s*(hi+|hello+|hey+|hiya|yo|good\s+(morning|afternoon|evening|day)|greetings|namaste|salaam|"
    r"how\s+are\s+(you|u)(\s+doing)?|how'?s\s+it\s+going|what'?s\s+up|thanks?(\s+you)?|thank\s+you(\s+so\s+much)?|"
    r"thx|ty|ok(ay)?|cool|great|nice|bye|good\s*bye)"
    r"(\s+(there|claude|team|all|guys|again|a\s+lot))?\s*[!.?,]*\s*$",
    re.IGNORECASE,
)
_THANKS = re.compile(r"\b(thanks?|thank\s+you|thx|ty)\b", re.IGNORECASE)
_BYE = re.compile(r"\b(bye|good\s*bye)\b", re.IGNORECASE)


def small_talk_reply(query: str) -> Optional[str]:
    """A fixed reply for a message that is only a greeting or thanks; None otherwise."""
    if not _SMALL_TALK.match(query or ""):
        return None
    if _THANKS.search(query):
        return "You're welcome. Ask me anything else about the selected organizations."
    if _BYE.search(query):
        return "Goodbye. Your conversation is saved in this thread."
    return ("Hi. I can compare the selected organizations, for example: \"Compare revenue this year\", "
            "\"Which company is performing best overall?\", or \"List unpaid invoices older than 90 days per org\".")

#: Wait before retrying an unavailable planned fetch once.
RETRY_DELAY_SECONDS = 0.5

_STATUS_FOR_OUTCOME = {
    Outcome.OK: "ok",
    Outcome.PARTIAL: "partial",
    Outcome.EMPTY: "empty",
}


@dataclass
class QueryPlan:
    """How to answer one question across organizations."""

    kind: str
    selection: Optional[Dict[str, Any]] = None
    intent: int = 4
    source: str = ""            # "series", "metric", "shared_contacts", "fast", "llm", "conversation_meta", "how_to_guide"
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    reason: str = ""
    metric: Optional[str] = None  # key in multi_org_metrics.BY_KEY when one figure per org is asked
    #: Every metric asked for, in question order; more than one is a multi-metric table.
    metrics: List[str] = field(default_factory=list)
    #: {"metric": key, "grain": "month" | "quarter"} for a figure over time.
    series: Optional[Dict[str, Any]] = None
    #: Every report call per organization; `selection` is the first of them.
    selections: List[Dict[str, Any]] = field(default_factory=list)
    #: Lines added to the answer, e.g. a figure in the question that cannot be computed.
    notes: List[str] = field(default_factory=list)

    def all_selections(self) -> List[Dict[str, Any]]:
        return self.selections or ([self.selection] if self.selection else [])

    def trace_step(self) -> Dict[str, Any]:
        step: Dict[str, Any] = {"step": "multi_org_plan", "kind": self.kind, "source": self.source}
        if self.selection:
            step["endpoint"] = self.selection.get("endpoint")
        if self.metric:
            step["metric"] = self.metric
        if len(self.metrics) > 1:
            step["metrics"] = list(self.metrics)
        if self.series:
            step["series"] = dict(self.series)
        return step


def _with_report_metric(plan: QueryPlan) -> QueryPlan:
    """Mark a plan whose report is one metric's source as that metric.

    The wording did not match a metric (or the plan would not be here), but a
    plan that fetches rpt_income_total per org is still the revenue
    comparison, and gets its ranked table and chart.
    """
    endpoint = (plan.selection or {}).get("endpoint")
    metric = METRIC_FOR_REPORT.get(endpoint or "")
    if metric and plan.metric is None:
        plan.metric = metric
        plan.metrics = [metric]
    return plan


_BY_PERIOD = re.compile(r"\b(?:by|per|each)\s+(?:month|quarter|week|year)\b|\b(?:monthly|quarterly|weekly)\b",
                        re.IGNORECASE)


def _metrics_plan(metrics: List[Any], query: str, source: str, notes: List[str]) -> QueryPlan:
    selections = metrics_selections(metrics, query)
    return QueryPlan(DATA, selection=selections[0], selections=selections, intent=4, source=source,
                     metric=metrics[0].key if len(metrics) == 1 else None,
                     metrics=[m.key for m in metrics], notes=notes,
                     reason="Metrics: " + ", ".join(m.label for m in metrics))


def plan_query(query: str, primary_org: int, runner: Any, user_id: int) -> Optional[QueryPlan]:
    """Route the question once. None means no plan could be made.

    `query` must already be PII-redacted: the plan call is a model call.
    """
    reply = small_talk_reply(query)
    if reply is not None:
        return QueryPlan(REPLY, source="greeting", notes=[reply], reason="Small talk")
    if is_conversation_meta_query(query):
        return QueryPlan(DIRECT, source="conversation_meta")
    if _how_to_guide_section(query) is not None:
        return QueryPlan(DIRECT, source="how_to_guide")

    # Matching across organizations that no report does ("paid by more than
    # one entity in the same week"): say so, list or not.
    cross = cross_org_unsupported(query)
    if cross:
        return QueryPlan(UNSUPPORTED, source="unsupported", notes=cross, reason="Unsupported: cross-organization match")

    # Figures no report can compute (EBITDA, gross margin, cash flow...). A
    # list question may still name them ("list fixed asset accounts"); a
    # figure by month ("other income by month") is still a figure.
    rows_wanted = is_list_question(query) and not _BY_PERIOD.search(query)
    terms = [] if rows_wanted else unsupported_terms(query)
    notes = unsupported_note(terms)

    # Figures over time per organization ("revenue by month", "P&L by month":
    # one series fetch per figure).
    series = match_series_metrics(query)
    if series is not None:
        series_metrics, grain = series
        selections = [series_selection(m, grain, query) for m in series_metrics]
        return QueryPlan(DATA, selection=selections[0], selections=selections, intent=4, source="series",
                         series={"metric": series_metrics[0].key, "metrics": [m.key for m in series_metrics],
                                 "grain": grain},
                         notes=notes,
                         reason="Series: " + ", ".join(m.label for m in series_metrics) + f" by {grain}")

    # Figures per organization (revenue, cash, receivables... or several of
    # them, or a statement): a fixed, org-filtered source per metric, and the
    # comparison is computed in code.
    metrics = match_metrics(query)
    if metrics:
        return _metrics_plan(metrics, query, "metric", notes)
    if terms:
        # Nothing computable was asked for: say so instead of letting the
        # nearest report or a model-selected endpoint answer under the asked name.
        return QueryPlan(UNSUPPORTED, source="unsupported", notes=notes,
                         reason="Unsupported: " + ", ".join(u.label for u in terms))

    # "Which company is doing best", "are any of my companies in trouble":
    # no figure named, so a fixed scorecard of them.
    if is_scorecard_question(query):
        return _metrics_plan([BY_KEY[k] for k in SCORECARD_METRICS], query, "scorecard", [])

    # Vendors or customers the organizations share: names are matched across
    # orgs, so every org needs its full per-contact totals from one source.
    if is_list_question(query) and choose_shape(query) == OVERLAP:
        selection = contact_totals_selection(query)
        if selection is not None:
            return QueryPlan(DATA, selection=selection, intent=4, source="shared_contacts",
                             reason="Shared vendors or customers")

    # Invoice or bill lists: status, amount, age, sort and count are read in
    # code and applied in SQL for every org, instead of a model filtering the
    # newest 20 rows.
    if rows_wanted:
        selection = document_list_selection(query)
        if selection is not None:
            return QueryPlan(DATA, selection=selection, selections=[selection], intent=4, source="document_list",
                             notes=unapplied_filters(query), reason=selection["reason"])
    # "Round-amount payments", "transactions with no description": the filter
    # is the question, and nothing applies it.
    blocked = unavailable_filters(query)
    if blocked:
        return QueryPlan(UNSUPPORTED, source="unsupported", notes=blocked, reason="Unsupported: list filter")

    hit = fast_route(query, primary_org, user_id=str(user_id), session_state={})
    if hit is not None:
        return _with_report_metric(QueryPlan(DATA, selection=hit.to_selection_dict(), intent=hit.intent,
                                             source="fast", reason=f"Fast router matched: {hit.rule_name}"))

    plan = QueryPlan(DATA, source="llm")
    try:
        routing, ci, co = classify_intent(
            query,
            functools.partial(runner._call_llm, purpose="classify_intent"),
            runner._parse_json,
            session_state={},
        )
        plan.input_tokens += int(ci or 0)
        plan.output_tokens += int(co or 0)
        plan.llm_calls += 1
        plan.intent = int(routing.get("type", 4) or 4)
        plan.reason = str(routing.get("reason") or "")
        if plan.intent in LEFT_PATH_TYPES:
            plan.kind = DIRECT
            return plan

        sel, si, so = select_endpoint(
            query,
            primary_org,
            functools.partial(runner._call_llm, purpose="endpoint_select"),
            runner._parse_json,
            user_id=str(user_id),
            session_state={},
        )
        plan.input_tokens += int(si or 0)
        plan.output_tokens += int(so or 0)
        plan.llm_calls += 1
    except Exception as e:
        logger.warning("multi_org_plan: planning failed, using per-org routing: %s", e)
        return None
    if not sel or not sel.get("endpoint"):
        return None
    plan.selection = sel
    return _with_report_metric(plan)


def _retrieve_with_retry(runner: Any, selection: Dict[str, Any], org_id: int, db_name: str, auth_token: str):
    retrieved = runner._retrieve(dict(selection), org_id, db_name, QueryTrace(org_id=org_id), auth_token=auth_token)
    if retrieved.outcome is Outcome.UNAVAILABLE and retrieved.reason != "not_org_scoped":
        # One retry for a timeout or a busy backend: the fallback is a full
        # pipeline run with several model calls, far costlier than a retry.
        time.sleep(RETRY_DELAY_SECONDS)
        retrieved = runner._retrieve(dict(selection), org_id, db_name, QueryTrace(org_id=org_id), auth_token=auth_token)
    return retrieved


def _fetch_several(
    plan: QueryPlan,
    selections: List[Dict[str, Any]],
    org_id: int,
    runner: Any,
    db_name: str,
    auth_token: str,
) -> Optional[Dict[str, Any]]:
    """Every report a multi-metric plan needs, for one organization.

    Each payload is kept under its selection key so each metric reads its own
    report. A report that fails leaves only its metrics empty; the org falls
    back to the full pipeline only when none of them could be fetched.
    """
    payloads: Dict[str, Any] = {}
    endpoints: List[str] = []
    for selection in selections:
        retrieved = _retrieve_with_retry(runner, selection, org_id, db_name, auth_token)
        if _STATUS_FOR_OUTCOME.get(retrieved.outcome) in ("ok", "partial") and isinstance(retrieved.payload, dict):
            payloads[selection_key(selection)] = retrieved.payload
            endpoints.append(str(retrieved.endpoint))
    if not payloads:
        return None
    return {
        "answer": "",
        "status": "ok" if len(payloads) == len(selections) else "partial",
        "results": list(payloads.values()),
        "payloads": payloads,
        "blocks": [],
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0, "cost_usd": 0.0},
        "routing_info": {
            "type": plan.intent,
            "type_label": "Data query",
            "path": "multi_org_plan",
            "api_endpoint": ", ".join(endpoints),
            "reason": plan.reason,
        },
    }


def fetch_for_org(
    plan: QueryPlan,
    org_id: int,
    runner: Any,
    *,
    query: str,
    allowed_org_ids: Optional[List[int]],
    user_id: int,
    db_name: str = "",
    auth_token: str = "",
) -> Optional[Dict[str, Any]]:
    """Fetch the planned data for one organization, with no model calls.

    Returns a per-org result in the shape merge_org_results reads, or None
    when the fetch could not produce a usable answer; the caller then runs
    the full single-org pipeline for this organization instead.
    Raises when the organization fails the allow-list check.
    """
    runner._enforce_tenant_isolation(
        organization_id=org_id,
        query=query,
        db_name=db_name,
        allowed_org_ids=allowed_org_ids,
        user_id=user_id,
        session_id=None,
    )
    # Same guard as the per-org pipeline: REST endpoints that cannot be
    # limited to one organization are skipped, and cache keys stay separate.
    runner._org_scoped_rest_only = True
    selections = plan.all_selections()
    if len(selections) > 1:
        return _fetch_several(plan, selections, org_id, runner, db_name, auth_token)
    retrieved = _retrieve_with_retry(runner, dict(plan.selection or {}), org_id, db_name, auth_token)

    status = _STATUS_FOR_OUTCOME.get(retrieved.outcome)
    if status is None:
        return None

    data = retrieved.payload
    results: List[Any] = []
    blocks: List[Dict[str, Any]] = []
    if status != "empty":
        results = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        spec = tool_spec_for_endpoint(retrieved.endpoint)
        formatter = spec.formatter if spec else "row_table"
        try:
            blocks = render_blocks(formatter, data, query=query, organization_id=org_id, db_name=db_name)
        except Exception as e:
            logger.warning("multi_org_plan: could not render blocks for org %s: %s", org_id, e)
    return {
        "answer": "",
        "status": status,
        "results": results,
        # Keyed like multi-report fetches, so a metric always reads its own report.
        "payloads": ({selection_key(plan.selection or {}): data}
                     if status != "empty" and isinstance(data, dict) and (plan.selection or {}).get("key") else {}),
        "blocks": [b for b in (blocks or []) if isinstance(b, dict)],
        "data_source": retrieved.to_data_source(),
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0, "cost_usd": 0.0},
        "routing_info": {
            "type": plan.intent,
            "type_label": "Data query",
            "path": "multi_org_plan",
            "api_endpoint": retrieved.endpoint,
            "reason": plan.reason,
        },
    }
