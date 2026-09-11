"""
gemini_brain_runner.py — Main orchestration runner for the Gemini Brain subsystem.

Decomposed from gemini_brain_adapter.py (lines 146-966).
Orchestrates:
  1. Intent classification via Bedrock Claude Haiku 4.5 (7 types)
  2. LEFT PATH: Direct answer via Bedrock Claude Haiku 4.5 for types 1, 2, 6, 7
  3. RIGHT PATH: API endpoint selection via Bedrock Claude Haiku 4.5 for types 3, 4, 5
  4. Live REST API call against Accutax backend
  5. Model selection for narration (Haiku 4.5 vs Sonnet 3.5, deterministic — see model_selector.py)
  6. Claude reasoning over live API data using AWS Bedrock
  7. SQL fallback engine execution when API endpoints are missing or fail
  8. Session state persistence and titling

All LLM calls in this runner go through AWS Bedrock (Claude). Google Gemini is no
longer used here — see `_call_llm` below.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from gemini_brain.api_client.accutax_client import call_api, extract_data
from gemini_brain.classification.intent_classifier import classify_intent
from gemini_brain.cache.result_cache import make_cache_key, result_cache
from gemini_brain.config.constants import (
    ENDPOINT_DESCRIPTIONS,
    HAIKU45_ID,
    LEFT_PATH_TYPES,
    NEVER_EXPOSE_BACKEND_RULE,
    RIGHT_PATH_TYPES,
    TYPE_LABELS,
)
from gemini_brain.config.pricing import gemini_brain_cost
from gemini_brain.config.constants import DEFAULT_USER_ID
from gemini_brain.config.settings import settings
from gemini_brain.policy import choose_policy
from gemini_brain.sql_fallback.db_connection import organization_exists
from gemini_brain.policy.verifier import strictness_note, verify_answer
from gemini_brain.endpoints.endpoint_selector import select_endpoint
from gemini_brain.endpoints.window_widener import (
    describe_window,
    plan_widenings,
    widened_params,
)
from gemini_brain.memory.session_memory import (
    get_project_context_by_session,
    get_state_by_session,
    is_valid_uuid,
    maybe_auto_title,
    save_message_by_session,
)
from gemini_brain.memory.conversation_window import (
    append_memory_block,
    attach_working_memory,
    is_conversation_meta_query,
    llm_messages_from_memory,
    maybe_roll_summary,
    persist_turn_and_maybe_summarize,
)
from gemini_brain.memory.state_extractor import (
    update_conversation_state_hybrid_by_session,
)
from gemini_brain.observability import METRICS, QueryTrace
from gemini_brain.pii.redactor import redact_pii
from gemini_brain.reasoning.bedrock_client import BedrockAdapter
from gemini_brain.reasoning.claude_reasoner import (
    reason_over_data,
    reason_over_data_stream,
)
from gemini_brain.reasoning.model_selector import pick_model
from gemini_brain.router.fast_router import fast_route
from gemini_brain.router.dates import date_filters_from_request
from gemini_brain.router.rules import get_endpoint_sql_verifiers
from gemini_brain.sql_fallback import db_connection, sql_engine
from gemini_brain.resilience import (
    Outcome,
    Retrieved,
    classify_payload,
    ErrorCode,
    classify_exception,
    notice_for,
    normalize_envelope,
    new_request_id,
)
from gemini_brain.artifacts.attach import attach_delivery
from gemini_brain.tools.formatters import render, render_blocks
from gemini_brain.tools.registry import tool_spec_for_endpoint
from gemini_brain.utils.json_parser import extract_json

logger = logging.getLogger("gemini_brain.orchestrator.runner")

#: endpoint -> (sql_task, param_builder) for the ~22 report/data endpoints that
#: have a cheap, deterministic SQL equivalent. Used to cross-check an EMPTY
#: live-API result before trusting it as "confirmed zero records" — see
#: _verify_empty_via_sql() below. Built once at import time.
_EMPTY_RESULT_SQL_VERIFIERS = get_endpoint_sql_verifiers()


def _verify_empty_via_sql(
    endpoint: Optional[str],
    organization_id: Optional[int],
    raw_query: str = "",
    query_params: Optional[Dict[str, Any]] = None,
) -> Optional[Retrieved]:
    """Cross-check a live API's EMPTY result against a cheap, direct SQL query.

    Only runs for endpoints with a known sql_task mapping (see
    get_endpoint_sql_verifiers) — dashboards, static config/lookup endpoints,
    and audit logs have no cheap SQL equivalent and are left untouched. Never
    raises; returns None when verification can't run or the source data isn't
    reachable, in which case the caller should keep today's behavior (trust
    the EMPTY result as-is).

    raw_query is passed through to the param builder so a "top N" count in
    the user's own phrasing (e.g. "top 5 vendors") reaches ranked builders
    like top_vendors/top_customers instead of silently defaulting to 10.

    query_params is the window actually sent to REST. The SQL check must use
    the same dates, or an empty "posted today" is overwritten with months of
    unrelated journals.
    """
    if not endpoint or organization_id is None:
        return None
    verifier = _EMPTY_RESULT_SQL_VERIFIERS.get(endpoint)
    if not verifier:
        return None
    sql_task, builder = verifier
    date_filters: Dict[str, str] = {}
    try:
        params = builder(raw_query, organization_id)
        date_filters = date_filters_from_request(query_params, raw_query)
        if date_filters:
            filters = dict(params.get("filters") or {})
            filters.update(date_filters)
            params["filters"] = filters
        from gemini_brain.sql_fallback.sql_engine import _get_coordinator_pipeline
        _, _, agent_handlers, *_ = _get_coordinator_pipeline()
        handler = agent_handlers.get("finance_agent")
        if not handler:
            return None
        raw_result = handler(sql_task, params)
    except Exception as e:
        logger.warning("Empty-result SQL verification failed for endpoint=%s task=%s: %s", endpoint, sql_task, e)
        return None

    check = classify_payload(raw_result, tier="sql_verify", endpoint=endpoint)
    if check.outcome in (Outcome.OK, Outcome.PARTIAL):
        logger.info(
            "Empty-result OVERRIDDEN by SQL verification: endpoint=%s task=%s found %d row(s)",
            endpoint, sql_task, check.row_count,
        )
        check.tier = "sql_verified_fallback"
        check.detail = sql_task
        return check
    if date_filters:
        logger.info(
            "Empty-result SQL agrees with REST: endpoint=%s task=%s window=%s",
            endpoint, sql_task, date_filters,
        )
    return None


def _subject_for(endpoint: Optional[str], tool_spec: Any = None, query: str = "") -> str:
    try:
        from gemini_brain.formatting.empty_answer import subject_for
        return subject_for(endpoint, tool_spec, query)
    except Exception:
        if tool_spec is not None and getattr(tool_spec, "name", ""):
            return str(tool_spec.name).replace("_", " ")
        return "your records"


DIRECT_ANSWER_SYSTEM_PROMPT: str = """You are an expert, helpful AI assistant for Accutax — a cloud-based ERP and accounting platform used across the Middle East (UAE, AED currency, 5% VAT).
You directly assist users with:
1. Accutax UI & Workflow Guidance: Provide step-by-step navigation and instructions for performing actions in Accutax (e.g., recording journal entries, creating invoices, managing bank accounts, creating items, generating VAT returns, reconciling accounts).
2. Accounting & Financial Concepts: Clearly define accounting terms, differences between principles (e.g., Accounts Receivable vs Accounts Payable, Accrual vs Cash basis, Debits vs Credits, Depreciation), and UAE Federal Tax Authority (FTA) compliance regulations.
3. Business & Financial Advice: Offer best practices for cash flow management, internal controls, working capital, and audit readiness.

Guidelines:
- Always be helpful, welcoming, and directly answer the question.
- Always identify yourself the same way: "Accutax AI". Never call yourself "Accutax support",
  "the assistant", "the AI", or any other variant — the name is fixed across every response.
- Prefer short paragraphs and numbered or bulleted lists. Never a wall of prose.
- Answer only the current question. Never invent a User: follow-up or continue as a script.
- For procedures and how-tos, provide clear, numbered steps.
- For concepts and comparisons, use bullet points and clear examples.
- Do not fabricate specific company financial figures or database numbers unless provided.
- You remember this thread. Prior turns are in the conversation messages and in CONVERSATION SO FAR. If the user asks what they asked earlier, quote that turn. Never say this is the start of the conversation when prior turns are present.
""" + NEVER_EXPOSE_BACKEND_RULE


def _resolve_adapter_for_key(model_key: str) -> Any:
    """Build the adapter for a model key chosen in the UI."""
    from gemini_brain.policy.registry import build_adapter, resolve_model

    return build_adapter(resolve_model(model_key))


class GeminiBrainRunner:
    """GeminiBrain runner: Gemini orchestrates, API is source of truth, Anthropic reasons."""

    def __init__(
        self,
        api_key: str = "",
        adapter_resolver: Optional[Callable[[str], Any]] = None,
    ):
        # Still sourced from GEMINI_API_KEY: passed through to state_extractor's
        # optional Gemini semantic-fallback call, which is separate from LLM
        # routing/answering below (all of which now runs on Bedrock).
        self.api_key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        # Default resolver turns a model key from the picker into a live adapter,
        # so selecting Gemini actually answers with Gemini rather than silently
        # falling back to Bedrock.
        self.adapter_resolver = adapter_resolver or _resolve_adapter_for_key
        self._answer_brief = False

    def _call_llm(
        self,
        system: str = "",
        user_text: str = "",
        max_tokens: int = 2000,
        thinking_budget: Optional[int] = 0,
        system_prompt: Optional[str] = None,
        user_message: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Tuple[str, int, int]:
        """Call Bedrock Claude Haiku 4.5 for routing/classification/direct-answer text generation.

        Used for intent classification, endpoint selection, org resolution, session
        auto-titling, and LEFT-path direct answers — everything that used to go to
        Gemini. ``thinking_budget`` is accepted for call-site compatibility but has
        no Bedrock equivalent and is ignored. Retry/backoff on throttling is handled
        internally by ``BedrockAdapter.converse``.
        """
        sys_inst = system_prompt if system_prompt is not None else system
        u_text = user_message if user_message is not None else user_text
        chat_messages = messages or [{"role": "user", "content": [{"text": u_text}]}]

        adapter = BedrockAdapter(model_id=HAIKU45_ID, label="Claude Haiku 4.5")
        try:
            if tools:
                resp = adapter.converse_with_tools(
                    system_prompt=sys_inst,
                    messages=chat_messages,
                    tools=tools,
                    temperature=0.0,
                    max_tokens=max_tokens,
                )
                from gemini_brain.reasoning.bedrock_client import extract_tool_calls, extract_text
                t_calls = extract_tool_calls(resp)
                if t_calls:
                    text = json.dumps({"name": t_calls[0].get("name"), "parameters": t_calls[0].get("input")})
                else:
                    text = extract_text(resp)
            else:
                text = adapter.converse(
                    system_prompt=sys_inst,
                    messages=chat_messages,
                    temperature=0.0,
                    max_tokens=max_tokens,
                )
            tu = adapter.get_token_usage()
            return text, tu.get("input_tokens", 0), tu.get("output_tokens", 0)
        except Exception as e:
            logger.warning("Bedrock call failed: %s", e)
            return "", 0, 0

    @staticmethod
    def _parse_json(text: str, default: Any = None) -> Any:
        res = extract_json(text)
        if res is not None:
            return res
        return default if default is not None else {}

    @staticmethod
    def _type_label(t: int) -> str:
        return TYPE_LABELS.get(t, "Data Query")

    @staticmethod
    def _direct_answer_messages(session_state: Optional[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        return llm_messages_from_memory(session_state, query)

    def _persist_conversation(
        self,
        session_id: Optional[str],
        user_id: int,
        organization_id: Optional[int],
        raw_query: str,
        answer: str,
        agent_trace: Optional[list] = None,
        db_name: str = "",
    ) -> None:
        persist_turn_and_maybe_summarize(
            session_id,
            user_id,
            organization_id,
            raw_query,
            answer,
            agent_trace=agent_trace,
            call_llm=self._call_llm,
            db_name=db_name,
        )

    def _kick_rolling_summary(self, session_id: Optional[str], db_name: str = "") -> None:
        """SQL fallback already wrote messages; compact older turns without a second insert."""
        if not session_id:
            return
        threading.Thread(
            target=maybe_roll_summary,
            args=(session_id, self._call_llm, db_name),
            daemon=True,
            name=f"chat-summary-{(session_id or '')[:8]}",
        ).start()

    @staticmethod
    def _cost(
        g_in: int, g_out: int, b_in: int, b_out: int, model_id: str
    ) -> float:
        return gemini_brain_cost(g_in, g_out, b_in, b_out, model_id)

    def _err(
        self,
        msg: str,
        t0: float,
        gi: int = 0,
        go: int = 0,
        code: Any = None,
        subject: str = "your records",
    ) -> Dict[str, Any]:
        """Build a user-safe failure envelope. `msg` goes to logs only."""
        rid = new_request_id()
        resolved_code = code or ErrorCode.INTERNAL_ERROR
        code_str = resolved_code.value if hasattr(resolved_code, "value") else str(resolved_code)
        logger.error("[%s] runner failure (%s): %s", rid, code_str, msg)
        notice = notice_for(resolved_code, subject=subject, request_id=rid)
        return normalize_envelope({
            "answer": notice["message"],
            "sql": None,
            "results": [],
            "error": code_str,
            "status": "degraded" if notice.get("retryable", False) else "failed",
            "notice": notice,
            "data_source": None,
            "table_markdown": None,
            "request_id": rid,
            "token_usage": {
                "input_tokens": gi,
                "output_tokens": go,
                "llm_calls": 0,
                "cost_usd": 0.0,
                "elapsed_seconds": round(time.time() - t0, 2),
            },
            "agent_trace": [],
            "routing_info": None,
        })

    def _retrieve(
        self,
        sel: Dict[str, Any],
        organization_id: int,
        db_name: str,
        trace: Any,
        auth_token: str = "",
    ) -> Retrieved:
        """Single retrieval attempt: cache -> sql function -> live API. Never raises.

        `auth_token`, when provided, is passed explicitly to call_api_resilient()
        rather than relying on the active_auth_token ContextVar. The SSE streaming
        endpoint drives its generator through Starlette's per-chunk threadpool
        dispatch (anyio.to_thread.run_sync called once per yielded chunk), which
        copies a fresh context on every call — a ContextVar.set() made earlier in
        the same generator does not survive past the first yield. Passing the
        token explicitly sidesteps that entirely instead of fighting it.
        """
        from gemini_brain.api_client.accutax_client import call_api_resilient
        from gemini_brain.sql_fallback.db_connection import execute_sql_function_safe

        endpoint = sel.get("endpoint") or ""
        if not endpoint:
            return Retrieved(Outcome.INVALID, reason="no_endpoint_selected")

        cache_key = make_cache_key(organization_id, endpoint, sel.get("query_params", {}))
        cached = result_cache.get_sync(cache_key)
        if cached is not None:
            res = classify_payload(cached, tier="cache", endpoint=endpoint)
            logger.info("Result cache hit for %s (outcome=%s)", endpoint, res.outcome.value)
            return res

        if endpoint.startswith("rpt_"):
            # Deterministic SQL report — see reports/definitions.py. Same dispatch
            # shape as fn_ below, but the report owns its own parameter handling
            # rather than being squeezed into a fixed (org, from, to) signature.
            from gemini_brain.reports.engine import run_report_safe

            with trace.stage("sql_report", endpoint=endpoint):
                res = run_report_safe(
                    endpoint,
                    sel.get("query_params", {}) or {},
                    organization_id,
                    db_name=db_name,
                )
        elif endpoint.startswith("fn_"):
            with trace.stage("sql_function_call", endpoint=endpoint):
                qp = sel.get("query_params", {}) or {}
                res = execute_sql_function_safe(
                    endpoint,
                    (organization_id, qp.get("start_date", "2020-01-01"), qp.get("end_date", "2099-12-31")),
                    organization_id,
                    db_name=db_name,
                )
        else:
            from gemini_brain.config.accutax_openapi import (
                normalize_query_params,
                path_exists,
            )

            exists = path_exists(endpoint)
            query_params = dict(sel.get("query_params", {}) or {})
            if exists is False:
                logger.info(
                    "Skipping HTTP for %s — path is not in Accutax OpenAPI",
                    endpoint,
                )
                res = Retrieved(
                    Outcome.UNAVAILABLE,
                    tier="live_api",
                    endpoint=endpoint,
                    reason="not_in_accutax_openapi",
                )
            else:
                if exists is True:
                    query_params = normalize_query_params(endpoint, query_params)
                    sel = {**sel, "query_params": query_params}
                with trace.stage("api_call", endpoint=endpoint):
                    res = call_api_resilient(
                        endpoint, sel.get("path_params", {}), query_params,
                        auth_token=auth_token,
                    )
            # Prefer the live report API; only build from SQL when that API is down.
            if res.outcome not in (Outcome.OK, Outcome.PARTIAL, Outcome.EMPTY, Outcome.DENIED):
                from gemini_brain.reports.definitions import REST_TO_SQL_REPORT
                from gemini_brain.reports.engine import run_report_safe

                sql_key = REST_TO_SQL_REPORT.get(endpoint)
                if sql_key:
                    with trace.stage("sql_report_fallback", endpoint=sql_key, failed_api=endpoint):
                        fallback = run_report_safe(
                            sql_key,
                            sel.get("query_params", {}) or {},
                            organization_id,
                            db_name=db_name,
                        )
                    if fallback.usable or fallback.outcome is Outcome.EMPTY:
                        fallback.endpoint = endpoint
                        fallback.tier = "sql_report_fallback"
                        logger.info(
                            "REST %s unavailable (%s); used SQL report %s",
                            endpoint, res.outcome.value, sql_key,
                        )
                        res = fallback

        # -- Cache policy: ONLY cache genuinely usable payloads (fixes §2.11) --
        if res.outcome is Outcome.OK and res.payload is not None:
            result_cache.set_sync(cache_key, res.payload, ttl=300)
        elif res.outcome is Outcome.EMPTY:
            result_cache.set_sync(cache_key, res.payload if res.payload is not None else [], ttl=30)
        else:
            METRICS.api_call_failed.inc()
            logger.warning(
                "Retrieval %s -> %s (%s) %s",
                endpoint, res.outcome.value, res.reason, res.detail[:120],
            )
        return res

    def _retry_widened(
        self,
        sel: Dict[str, Any],
        organization_id: int,
        db_name: str,
        trace: Any,
        auth_token: str = "",
    ) -> Tuple[Optional[Retrieved], Optional[str]]:
        """Re-run an empty date-scoped query over progressively longer look-backs.

        Returns (retrieved, window_description) on the first attempt that finds
        rows, or (None, None) when widening does not apply or every attempt is
        still empty — in which case the caller keeps today's confirmed-zero answer.

        Never raises: a failure here must degrade to the normal empty result, not
        break a query that already had a valid (if unhelpful) answer.
        """
        query_params = sel.get("query_params") or {}
        try:
            plans = plan_widenings(query_params)
        except Exception as e:
            logger.warning("Window widening planning failed: %s", e)
            return None, None

        if not plans:
            return None, None

        for start, end in plans:
            widened_sel = dict(sel)
            widened_sel["query_params"] = widened_params(query_params, start, end)
            description = describe_window(start, end)
            logger.info(
                "Empty result — retrying %s over %s (%s to %s)",
                sel.get("endpoint"), description, start, end,
            )
            try:
                with trace.stage("widened_retry", window=description):
                    retried = self._retrieve(
                        widened_sel, organization_id, db_name, trace, auth_token=auth_token
                    )
            except Exception as e:
                logger.warning("Widened retry failed for %s: %s", sel.get("endpoint"), e)
                return None, None

            if retried.usable:
                logger.info(
                    "Widened retry RECOVERED %d row(s) over %s",
                    getattr(retried, "row_count", 0), description,
                )
                sel["query_params"] = widened_sel["query_params"]
                return retried, description

        return None, None

    def _narrate_or_fallback(
        self,
        *,
        query: str,
        data: Any,
        endpoint: str,
        fallback_text: str,
        intent: int,
        session_id: Optional[str],
        selected_model_key: Optional[str],
        get_project_context_by_session: Any,
        conversation_memory: Optional[str] = None,
    ) -> Tuple[str, str, int, int, bool]:
        """Narrate `data` via Bedrock. Every answer is LLM-narrated — there is no
        zero-LLM formatter-only path. `fallback_text` (the deterministic formatted
        table or notice) is used ONLY if narration fails twice in a row, so an
        LLM outage degrades the response instead of failing the whole query.

        Returns (answer, model_label, input_tokens, output_tokens, degraded).
        """
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                answer, label, bi_new, bo_new = reason_over_data(
                    query=query,
                    data=data,
                    endpoint=endpoint or "",
                    intent=intent,
                    session_id=session_id,
                    selected_model_key=selected_model_key,
                    adapter_resolver=self.adapter_resolver,
                    get_project_context_by_session=get_project_context_by_session,
                    conversation_memory=conversation_memory,
                    brief=bool(getattr(self, "_answer_brief", False)),
                )
                return answer, label, bi_new, bo_new, False
            except Exception as e:
                last_exc = e
                logger.warning("Narration attempt %d failed: %s", attempt + 1, e)
        logger.error("Narration failed after retry, falling back to formatted table: %s", last_exc)
        return fallback_text, "None (Narration Unavailable — Fallback Table)", 0, 0, True

    def _empty_result(
        self,
        *,
        query: str,
        subject: str,
        retrieved: Any,
        tool_spec: Any,
        qtype: int,
        type_lbl: str,
        reason: str,
        trace: Any,
        t0: float,
        gi: int,
        go: int,
        llm_calls: int,
        is_redacted: bool,
        redaction_counts: Dict[str, int],
        session_id: Optional[str],
        user_id: int,
        raw_query: str,
        organization_id: Optional[int] = None,
        conversation_memory: Optional[str] = None,
        db_name: str = "",
        query_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Zero rows is a confirmed answer, not a failure — but it is still LLM-narrated
        per policy (every answer goes through Bedrock). The narration payload is tiny
        (0 rows) so this stays fast; it falls back to a deterministic sentence only if
        narration itself fails twice (see _narrate_or_fallback).

        Before confirming, cross-checks endpoints we've caught being unreliable
        (see _verify_empty_via_sql / get_endpoint_sql_verifiers) against a cheap
        direct SQL query — most report endpoints work fine and this check is
        skipped for them entirely, so this adds no cost on the common path.
        """
        endpoint = getattr(retrieved, "endpoint", "")
        verified = _verify_empty_via_sql(
            endpoint, organization_id, raw_query, query_params=query_params,
        )
        if verified is not None:
            formatted_table = render("row_table", verified.payload, query=raw_query)
            formatted_blocks = render_blocks("row_table", verified.payload, query=raw_query)
            answer, b_label, bi_new, bo_new, narration_degraded = self._narrate_or_fallback(
                query=query,
                data=verified.payload,
                endpoint=endpoint,
                fallback_text=f"Here's your {subject}.",
                intent=qtype,
                session_id=session_id,
                selected_model_key=None,
                get_project_context_by_session=get_project_context_by_session,
                conversation_memory=conversation_memory,
            )
            rid = new_request_id()
            trace_summary = trace.emit()
            if session_id:
                with trace.stage("memory_write"):
                    self._persist_conversation(
                        session_id,
                        user_id,
                        organization_id,
                        raw_query,
                        answer,
                        agent_trace=[{"endpoint": endpoint, "step": "rest_api_call", "status": "empty"}],
                        db_name=db_name,
                    )
            return normalize_envelope({
                "answer": answer,
                "sql": None,
                "results": verified.rows,
                "error": None,
                "status": "partial" if narration_degraded else "ok",
                "notice": notice_for("MODEL_UNAVAILABLE", subject=subject) if narration_degraded else None,
                "data_source": verified.to_data_source(),
                "table_markdown": formatted_table,
                "blocks": formatted_blocks,
                "request_id": rid,
                "pii_redacted": is_redacted,
                "pii_redactions": redaction_counts,
                "token_usage": {
                    "input_tokens": gi + bi_new,
                    "output_tokens": go + bo_new,
                    "llm_calls": llm_calls + (0 if narration_degraded else 1),
                    "cost_usd": self._cost(gi, go, bi_new, bo_new, b_label),
                    "elapsed_seconds": round(time.time() - t0, 2),
                },
                "agent_trace": [
                    {"step": "gemini_router", "type": qtype, "type_label": type_lbl,
                     "path": "api_then_anthropic", "reason": reason},
                    {"step": "rest_api_call", "endpoint": endpoint, "status": "empty", "row_count": 0},
                    {"step": "empty_result_sql_verification", "status": "overridden",
                     "sql_task": verified.detail, "row_count": verified.row_count},
                    {"step": "anthropic_reasoning", "model": b_label,
                     "tokens_in": bi_new, "tokens_out": bo_new},
                ],
                "routing_info": {
                    "type": qtype, "type_label": type_lbl, "path": "api_then_anthropic",
                    "api_endpoint": endpoint, "reason": reason,
                },
                "query_trace": trace_summary,
            })

        fallback_answer = (
            f"I checked your {subject} and found no matching records.\n\n"
            "This is a confirmed result from your books."
        )
        try:
            from gemini_brain.formatting.empty_answer import build_empty_answer
            fallback_answer = build_empty_answer(query, subject, retrieved)
        except Exception:
            pass

        empty_data = retrieved.payload if getattr(retrieved, "payload", None) is not None else []
        answer, b_label, bi_new, bo_new, narration_degraded = self._narrate_or_fallback(
            query=query,
            data=empty_data,
            endpoint=endpoint,
            fallback_text=fallback_answer,
            intent=qtype,
            session_id=session_id,
            selected_model_key=None,
            get_project_context_by_session=get_project_context_by_session,
            conversation_memory=conversation_memory,
        )

        rid = new_request_id()
        notice = notice_for("NO_ROWS", subject=subject, request_id=rid)
        trace_summary = trace.emit()

        if session_id:
            with trace.stage("memory_write"):
                self._persist_conversation(
                    session_id,
                    user_id,
                    organization_id,
                    raw_query,
                    answer,
                    agent_trace=[{"endpoint": endpoint, "step": "rest_api_call", "status": "empty"}],
                    db_name=db_name,
                )

        return normalize_envelope({
            "answer": answer,
            "sql": None,
            "results": [],
            "error": None,
            "status": "empty",
            "notice": notice,
            "data_source": retrieved.to_data_source() if hasattr(retrieved, "to_data_source") else None,
            "table_markdown": None,
            "request_id": rid,
            "pii_redacted": is_redacted,
            "pii_redactions": redaction_counts,
            "token_usage": {
                "input_tokens": gi + bi_new,
                "output_tokens": go + bo_new,
                "llm_calls": llm_calls + (0 if narration_degraded else 1),
                "cost_usd": self._cost(gi, go, bi_new, bo_new, b_label),
                "elapsed_seconds": round(time.time() - t0, 2),
            },
            "agent_trace": [
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "reason": reason,
                },
                {
                    "step": "rest_api_call",
                    "endpoint": getattr(retrieved, "endpoint", ""),
                    "status": "empty",
                    "row_count": 0,
                },
                {"step": "empty_result_handler",
                 "status": "narration_degraded" if narration_degraded else "narrated",
                 "model": b_label},
            ],
            "routing_info": {
                "type": qtype,
                "type_label": type_lbl,
                "path": "api_then_anthropic",
                "api_endpoint": getattr(retrieved, "endpoint", ""),
                "reason": reason,
            },
            "query_trace": trace_summary,
        })

    def _degraded_result(
        self,
        code: Any,
        subject: str,
        retrieved: Any,
        *,
        query: str,
        qtype: int,
        type_lbl: str,
        reason: str,
        trace: Any,
        t0: float,
        gi: int,
        go: int,
        llm_calls: int,
        is_redacted: bool,
        redaction_counts: Dict[str, int],
        session_id: Optional[str] = None,
        raw_query: str = "",
        conversation_memory: Optional[str] = None,
        user_id: int = DEFAULT_USER_ID,
        organization_id: Optional[int] = None,
        db_name: str = "",
    ) -> Dict[str, Any]:
        rid = new_request_id()
        notice = notice_for(code, subject=subject, request_id=rid)

        answer, b_label, bi_new, bo_new, narration_degraded = self._narrate_or_fallback(
            query=query,
            data={"status": "unavailable", "detail": notice["message"]},
            endpoint=getattr(retrieved, "endpoint", "") or "",
            fallback_text=notice["message"],
            intent=qtype,
            session_id=session_id,
            selected_model_key=None,
            get_project_context_by_session=get_project_context_by_session,
            conversation_memory=conversation_memory,
        )
        trace_summary = trace.emit()
        if session_id:
            with trace.stage("memory_write"):
                self._persist_conversation(
                    session_id, user_id, organization_id, raw_query, answer, db_name=db_name
                )

        return normalize_envelope({
            "answer": answer,
            "sql": None,
            "results": [],
            "error": notice["code"],
            "status": "degraded" if notice.get("retryable", False) else "failed",
            "notice": notice,
            "data_source": retrieved.to_data_source() if hasattr(retrieved, "to_data_source") else None,
            "table_markdown": None,
            "request_id": rid,
            "pii_redacted": is_redacted,
            "pii_redactions": redaction_counts,
            "token_usage": {
                "input_tokens": gi + bi_new,
                "output_tokens": go + bo_new,
                "llm_calls": llm_calls + (0 if narration_degraded else 1),
                "cost_usd": self._cost(gi, go, bi_new, bo_new, b_label),
                "elapsed_seconds": round(time.time() - t0, 2),
            },
            "agent_trace": [
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "reason": reason,
                },
                {"step": "anthropic_reasoning", "model": b_label,
                 "tokens_in": bi_new, "tokens_out": bo_new},
            ],
            "routing_info": {
                "type": qtype,
                "type_label": type_lbl,
                "path": "api_then_anthropic",
                "api_endpoint": getattr(retrieved, "endpoint", None),
                "reason": reason,
            },
            "query_trace": trace_summary,
        })

    def _db_fallback(
        self,
        query: str,
        organization_id: int,
        user_id: int = DEFAULT_USER_ID,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        raw_query: Optional[str] = None,
        intent: Optional[int] = None,
        conversation_memory: Optional[str] = None,
    ) -> Dict[str, Any]:
        adapter = BedrockAdapter(model_id=HAIKU45_ID, label="Claude Haiku 4.5")
        er = sql_engine.run(
            query,
            adapter,
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            raw_user_question=raw_query or query,
            intent=intent,
            save_message_by_session=save_message_by_session,
            update_conversation_state_hybrid_by_session=update_conversation_state_hybrid_by_session,
            maybe_auto_title=lambda sid, q: maybe_auto_title(sid, q, self._call_llm),
            conversation_memory=conversation_memory,
        )

        if (
            selected_model_key
            and selected_model_key != "gemini_brain"
            and er.get("success", True)
            and er.get("results")
            and self.adapter_resolver is not None
        ):
            data_str = json.dumps(er["results"][:40], default=str, ensure_ascii=False)
            if len(data_str) > 5000:
                data_str = data_str[:5000] + "\n... (truncated)"

            from gemini_brain.reasoning.claude_reasoner import (
                classify_payload_shape,
                narration_budget,
            )

            # Same payload-conditional budget as the live-API narration path. This
            # site previously paired the 120-word prompt with max_tokens=1500 — the
            # room was there but the prompt forbade using it, so SQL-fallback rows
            # were collapsed just as hard as everywhere else.
            # No .format() on these prompts: they carry no placeholders, so the old
            # call was a no-op that would raise the moment anyone added a brace.
            system, narration_max_tokens = narration_budget(
                classify_payload_shape(er.get("results")),
                brief=bool(getattr(self, "_answer_brief", False)),
            )
            if conversation_memory:
                system += "\n\n" + conversation_memory
            if session_id:
                project_context = get_project_context_by_session(session_id)
                if project_context:
                    if project_context.get("files"):
                        system += "\n\nProject Knowledge Base Documents:\n"
                        for f in project_context["files"]:
                            system += f"--- Document: {f['filename']} ---\n{f['content']}\n"
                    if project_context.get("cross_chat_history"):
                        system += "\n\nContext & Data learned from other chats in this same project:\n"
                        for chat in project_context["cross_chat_history"]:
                            system += f"--- Thread: {chat['name']} ---\n"
                            for msg in chat["messages"]:
                                system += f"{msg['role'].capitalize()}: {msg['content']}\n"

            user_msg = (
                f"User question: {query}\n\n"
                f"Database query results:\n```json\n{data_str}\n```\n\n"
                f"Please answer the question based on these database results."
            )

            selected_adapter = self.adapter_resolver(selected_model_key)
            answer = selected_adapter.converse(
                system_prompt=system,
                messages=[{"role": "user", "content": [{"text": user_msg}]}],
                temperature=0.0,
                max_tokens=narration_max_tokens,
            )
            cleaned = (answer or "").strip()
            er["answer"] = cleaned or er.get("answer") or ""

            tu = selected_adapter.get_token_usage()
            er["token_usage"]["input_tokens"] += tu.get("input_tokens", 0)
            er["token_usage"]["output_tokens"] += tu.get("output_tokens", 0)
            er["token_usage"]["llm_calls"] += 1

        return er

    def _db_fallback_stream(
        self,
        query: str,
        organization_id: int,
        user_id: int = DEFAULT_USER_ID,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        raw_query: Optional[str] = None,
        intent: Optional[int] = None,
        conversation_memory: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        adapter = BedrockAdapter(model_id=HAIKU45_ID, label="Claude Haiku 4.5")

        if (
            not selected_model_key
            or selected_model_key == "gemini_brain"
            or self.adapter_resolver is None
        ):
            for chunk in sql_engine.run_stream(
                query,
                adapter,
                organization_id=organization_id,
                user_id=user_id,
                session_id=session_id,
                raw_user_question=raw_query or query,
                save_message_by_session=save_message_by_session,
                update_conversation_state_hybrid_by_session=update_conversation_state_hybrid_by_session,
                maybe_auto_title=lambda sid, q: maybe_auto_title(sid, q, self._call_llm),
                conversation_memory=conversation_memory,
            ):
                yield chunk
            return

        yield {"status": "Executing database query", "type": "retrieval"}
        er = sql_engine.run(
            query,
            adapter,
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            raw_user_question=raw_query or query,
            intent=intent,
            save_message_by_session=save_message_by_session,
            update_conversation_state_hybrid_by_session=update_conversation_state_hybrid_by_session,
            maybe_auto_title=lambda sid, q: maybe_auto_title(sid, q, self._call_llm),
            conversation_memory=conversation_memory,
        )

        if er.get("success", True) and er.get("results"):
            yield {"status": "Analyzing database results", "type": "analysis"}

            data_str = json.dumps(er["results"][:40], default=str, ensure_ascii=False)
            if len(data_str) > 5000:
                data_str = data_str[:5000] + "\n... (truncated)"

            from gemini_brain.reasoning.claude_reasoner import (
                classify_payload_shape,
                narration_budget,
            )

            # Same payload-conditional budget as the live-API narration path. This
            # site previously paired the 120-word prompt with max_tokens=1500 — the
            # room was there but the prompt forbade using it, so SQL-fallback rows
            # were collapsed just as hard as everywhere else.
            # No .format() on these prompts: they carry no placeholders, so the old
            # call was a no-op that would raise the moment anyone added a brace.
            system, narration_max_tokens = narration_budget(
                classify_payload_shape(er.get("results")),
                brief=bool(getattr(self, "_answer_brief", False)),
            )
            if conversation_memory:
                system += "\n\n" + conversation_memory
            if session_id:
                project_context = get_project_context_by_session(session_id)
                if project_context:
                    if project_context.get("files"):
                        system += "\n\nProject Knowledge Base Documents:\n"
                        for f in project_context["files"]:
                            system += f"--- Document: {f['filename']} ---\n{f['content']}\n"
                    if project_context.get("cross_chat_history"):
                        system += "\n\nContext & Data learned from other chats in this same project:\n"
                        for chat in project_context["cross_chat_history"]:
                            system += f"--- Thread: {chat['name']} ---\n"
                            for msg in chat["messages"]:
                                system += f"{msg['role'].capitalize()}: {msg['content']}\n"

            user_msg = (
                f"User question: {query}\n\n"
                f"Database query results:\n```json\n{data_str}\n```\n\n"
                f"Please answer the question based on these database results."
            )

            yield {"status": "Generating response", "type": "generation"}
            selected_adapter = self.adapter_resolver(selected_model_key)
            answer = selected_adapter.converse(
                system_prompt=system,
                messages=[{"role": "user", "content": [{"text": user_msg}]}],
                temperature=0.0,
                max_tokens=narration_max_tokens,
            )
            er["answer"] = answer.strip()

            tu = selected_adapter.get_token_usage()
            er["token_usage"]["input_tokens"] += tu.get("input_tokens", 0)
            er["token_usage"]["output_tokens"] += tu.get("output_tokens", 0)
            er["token_usage"]["llm_calls"] += 1

        yield {"final_result": er}

    def _enforce_tenant_isolation(
        self,
        organization_id: Optional[int],
        query: str,
        db_name: str,
        allowed_org_ids: Optional[list[int]],
        user_id: int = DEFAULT_USER_ID,
        session_id: Optional[str] = None,
    ) -> int:
        """Enforce tenant isolation and session ownership verification."""
        # 0. Session ownership check
        if session_id:
            from gemini_brain.memory.session_memory import verify_session_ownership
            if not verify_session_ownership(session_id=session_id, user_id=user_id, db_name=db_name):
                raise ValueError(
                    f"Access denied: Session '{session_id}' does not belong to user {user_id}."
                )

        # Explicit zero-org access check (authenticated user with 0 assigned organizations)
        if allowed_org_ids is not None and len(allowed_org_ids) == 0:
            raise ValueError("Access denied: User has no assigned organizations.")

        # Internal / no-token / unconstrained test paths (allowed_org_ids is None).
        # Every production call site (api/routes.py) always passes a real
        # allow-list from the caller's JWT, so this branch should only ever be
        # hit by internal tooling (scripts/, tests/) that calls the runner
        # directly — never by an authenticated request. Logged loudly rather
        # than silently trusting whatever organization_id was supplied, so an
        # unconstrained call is visible/auditable instead of indistinguishable
        # from a normal verified one.
        if allowed_org_ids is None:
            logger.warning(
                "_enforce_tenant_isolation called with allowed_org_ids=None "
                "(unconstrained) for query=%r, organization_id=%r — trusting "
                "the caller. This path should only be reached by internal "
                "tooling, never an authenticated API request.",
                query, organization_id,
            )
            if organization_id is None:
                raise ValueError(
                    "Organization ID is required and could not be resolved from prompt or parameters."
                )
            return organization_id

        # JWT Auth context path with assigned org IDs (e.g. [5], [5, 10])

        # 1. Check explicitly requested body organization_id
        if organization_id is not None:
            if organization_id not in allowed_org_ids:
                raise ValueError(
                    f"Access denied: Requested Organization ID {organization_id} is not in user's allowed organizations."
                )

        # 2. Default resolution if org is not specified in body
        if organization_id is None:
            if len(allowed_org_ids) >= 1:
                organization_id = allowed_org_ids[0]
                logger.info("Auto-defaulted to primary allowed organization ID %d", organization_id)
            else:
                raise ValueError(
                    "Multiple organizations available. Please explicitly specify organization_id in request."
                )

        return organization_id


    # ──────────────────────────────────────────────
    # Execution policy wrappers
    # ──────────────────────────────────────────────
    # The pipeline below predates model/effort selection, so policy is resolved
    # here and applied to the result rather than threaded through every branch.
    # When run() and run_stream() are unified this moves into the pipeline.

    @staticmethod
    def _explain_empty(result: Dict[str, Any], organization_id: Optional[int]) -> None:
        """Replace a bare "no data" with the real reason, when there is one.

        An empty result has two very different causes: the tenant genuinely has
        no matching records, or the tenant does not exist in the database this
        process is connected to (which happens when the REST API and the SQL
        engine are pointed at different datasets). Reporting the second as the
        first is the most damaging answer this system can give.
        """
        if organization_id is None or (result.get("results") or []):
            return

        # Left-path answers (FAQ, guidance, concepts) never retrieve data, so an
        # empty result set there says nothing about the tenant.
        path = (result.get("routing_info") or {}).get("path") or ""
        if path not in ("db_fallback", "api_then_anthropic"):
            return

        exists = organization_exists(organization_id)
        if exists is not False:
            return

        result["answer"] = (
            f"Organization {organization_id} does not exist in the database this "
            f"service is connected to ({settings.db_name}), so no figures can be "
            f"retrieved for it. This usually means the Accutax API and the SQL "
            f"engine are pointed at different datasets — check DB_NAME."
        )
        result["status"] = "failed"
        result["error"] = "TENANT_NOT_IN_DATABASE"

    @staticmethod
    def _payload_for_delivery(result: Dict[str, Any]) -> Any:
        results = result.get("results") or []
        if len(results) == 1 and isinstance(results[0], dict):
            return results[0]
        if results:
            return results
        return None

    def _apply_delivery(
        self,
        result: Dict[str, Any],
        *,
        query: str,
        user_id: int,
        organization_id: Optional[int],
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        """Add canvas/artifact blocks when the user asked for a graph or file."""
        if not isinstance(result, dict):
            return result
        blocks = []
        for block in result.get("blocks") or []:
            if isinstance(block, dict):
                blocks.append(block)
            elif hasattr(block, "model_dump"):
                blocks.append(block.model_dump())
        result["blocks"] = attach_delivery(
            query,
            self._payload_for_delivery(result),
            blocks,
            status=result.get("status") or "ok",
            user_id=user_id,
            organization_id=organization_id,
            session_id=session_id,
        )
        if session_id:
            try:
                from gemini_brain.memory.session_memory import update_last_assistant_blocks
                update_last_assistant_blocks(session_id, result["blocks"])
            except Exception:
                pass
        return result

    def _decorate(
        self,
        result: Dict[str, Any],
        policy: Any,
        organization_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Attach the execution policy and the numeric grounding report."""
        if not isinstance(result, dict):
            return result

        result["policy"] = policy.to_public()
        self._explain_empty(result, organization_id)

        if policy.effort.verify_grounding:
            report = verify_answer(
                result.get("answer") or "",
                result.get("results") or [],
                enforce=settings.verify_enforce,
            )
            result["verification"] = report.to_public()
            if report.checked:
                logger.info(
                    "grounding: %d/%d matched (rate=%.3f) effort=%s",
                    report.matched, report.checked, report.grounding_rate, policy.effort.name,
                )
            if settings.verify_enforce and not report.grounded:
                result["answer"] = (result.get("answer") or "") + strictness_note(report)
                if result.get("status") == "ok":
                    result["status"] = "partial"
        return result

    def run(
        self,
        query: str,
        organization_id: Optional[int] = None,
        db_name: str = "accutax_bk",
        use_api: bool = True,
        user_id: int = DEFAULT_USER_ID,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        allowed_org_ids: Optional[list[int]] = None,
        auth_token: str = "",
        model: Optional[str] = None,
        effort: Optional[str] = None,
        ui_context: Optional[dict] = None,
        brief: bool = False,
    ) -> Dict[str, Any]:
        """Resolve the execution policy, run the pipeline, verify the figures."""
        self._answer_brief = bool(brief)
        policy = choose_policy(query, requested_model=model, requested_effort=effort)
        explicit_model_key = policy.model_key if (model and model != "auto") else None
        result = self._run_inner(
            query=query,
            organization_id=organization_id,
            db_name=db_name,
            use_api=use_api,
            user_id=user_id,
            session_id=session_id,
            # Only override the model when the user actually picked one. Under
            # "auto" the pipeline keeps its own per-stage model choices, which
            # policy.model_key describes rather than dictates.
            selected_model_key=selected_model_key or explicit_model_key,
            allowed_org_ids=allowed_org_ids,
            auth_token=auth_token,
            ui_context=ui_context,
        )
        result = self._apply_delivery(
            result,
            query=query,
            user_id=user_id,
            organization_id=organization_id,
            session_id=session_id,
        )
        return self._decorate(result, policy, organization_id)

    def run_stream(
        self,
        query: str,
        organization_id: Optional[int] = None,
        db_name: str = "accutax_bk",
        use_api: bool = True,
        user_id: int = DEFAULT_USER_ID,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        allowed_org_ids: Optional[list[int]] = None,
        auth_token: str = "",
        model: Optional[str] = None,
        effort: Optional[str] = None,
        ui_context: Optional[dict] = None,
        brief: bool = False,
    ) -> Generator[Dict[str, Any], None, None]:
        """Streaming counterpart of run(); emits the policy before any work starts."""
        self._answer_brief = bool(brief)
        policy = choose_policy(query, requested_model=model, requested_effort=effort)
        explicit_model_key = policy.model_key if (model and model != "auto") else None
        yield {"policy": policy.to_public()}

        for chunk in self._run_stream_inner(
            query=query,
            organization_id=organization_id,
            db_name=db_name,
            use_api=use_api,
            user_id=user_id,
            session_id=session_id,
            # Only override the model when the user actually picked one. Under
            # "auto" the pipeline keeps its own per-stage model choices, which
            # policy.model_key describes rather than dictates.
            selected_model_key=selected_model_key or explicit_model_key,
            allowed_org_ids=allowed_org_ids,
            auth_token=auth_token,
            ui_context=ui_context,
        ):
            if isinstance(chunk, dict) and "final_result" in chunk:
                chunk["final_result"] = self._apply_delivery(
                    chunk["final_result"],
                    query=query,
                    user_id=user_id,
                    organization_id=organization_id,
                    session_id=session_id,
                )
                chunk["final_result"] = self._decorate(
                    chunk["final_result"], policy, organization_id
                )
            yield chunk

    def _run_inner(
        self,
        query: str,
        organization_id: Optional[int] = None,
        db_name: str = "accutax_bk",
        use_api: bool = True,
        user_id: int = DEFAULT_USER_ID,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        allowed_org_ids: Optional[list[int]] = None,
        auth_token: str = "",
        ui_context: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Run query through Gemini Brain pipeline synchronously.

        `auth_token`, when given, is used explicitly for every Accutax REST call
        instead of relying on the active_auth_token ContextVar (see _retrieve()).
        """
        t0 = time.time()
        trace = QueryTrace(org_id=organization_id)

        if session_id and not is_valid_uuid(session_id):
            session_id = None
        if selected_model_key and selected_model_key.strip().lower() in ("string", "null", "none", ""):
            selected_model_key = None

        with trace.stage("pii_redaction"):
            raw_query = query
            redacted_query, redaction_counts = redact_pii(query)
            is_redacted = sum(redaction_counts.values()) > 0
            if is_redacted:
                logger.info("PII redacted from query. Counts: %s", redaction_counts)
            query = redacted_query

        with trace.stage("tenant_isolation"):
            organization_id = self._enforce_tenant_isolation(
                organization_id=organization_id,
                query=query,
                db_name=db_name,
                allowed_org_ids=allowed_org_ids,
                user_id=user_id,
                session_id=session_id,
            )
            trace.set_org_id(organization_id)

        gi = go = bi = bo = llm_calls = 0
        bedrock_model_id = HAIKU45_ID

        # Phase D: Load active session context + compact conversation window
        session_state = get_state_by_session(session_id, db_name=db_name) if session_id else {}
        if ui_context:
            session_state.update({k: v for k, v in ui_context.items() if v is not None})
        session_state = attach_working_memory(
            session_id, user_id, organization_id, session_state, db_name=db_name
        )
        conversation_memory = session_state.get("_memory_block") or None

        # Phase 2: Fast Router Pre-Step (bypasses LLM classification & selection)
        router_source = "llm"
        fast_hit = None
        sel = None
        if is_conversation_meta_query(query):
            router_source = "conversation_meta"
            qtype = 1
            reason = "Question about this chat's prior messages"
            type_lbl = self._type_label(qtype)
            logger.info("GeminiBrain conversation-meta → direct answer (skip API/SQL)")
        else:
            if use_api:
                fast_hit = fast_route(query, organization_id, user_id=str(user_id), session_state=session_state)

            if fast_hit is not None:
                router_source = "fast"
                qtype = fast_hit.intent
                reason = f"Fast router matched: {fast_hit.rule_name}"
                type_lbl = self._type_label(qtype)
                sel = fast_hit.to_selection_dict()
                logger.info("GeminiBrain (FastRouter) -> endpoint=%s rule=%s (0 Gemini LLM calls)", fast_hit.endpoint, fast_hit.rule_name)
            else:
                METRICS.llm_router_calls.inc()
                # 1. Classify intent via Gemini Flash
                with trace.stage("classification"):
                    routing, ri, ro = classify_intent(query, self._call_llm, self._parse_json, session_state=session_state)
                bi += int(ri or 0)
                bo += int(ro or 0)
                llm_calls += 1
                qtype = routing.get("type", 4)
                reason = routing.get("reason", "")
                type_lbl = self._type_label(qtype)
                logger.info("GeminiBrain type=%d (%s) - %s", qtype, type_lbl, reason)

        # 2a. LEFT PATH: Gemini direct
        # Type 7 (Summary & Advice) is normally LEFT (no data), but when the
        # fast router already matched a concrete endpoint for it (e.g. the
        # dashboard_overview rule -> /report/profit-loss), that endpoint
        # selection must not be thrown away — fall through to the RIGHT path
        # below so the advice is grounded in real numbers instead of the
        # model refusing or bluffing without data.
        if qtype in LEFT_PATH_TYPES and not (qtype == 7 and sel is not None):
            try:
                system = append_memory_block(DIRECT_ANSWER_SYSTEM_PROMPT, session_state)
                if getattr(self, "_answer_brief", False):
                    system += (
                        "\nThe user asked for a brief answer. Use at most five short "
                        "markdown bullets. No long paragraphs.\n"
                    )
                answer_messages = self._direct_answer_messages(session_state, query)
                if session_id:
                    project_context = get_project_context_by_session(session_id)
                    if project_context:
                        if project_context.get("files"):
                            system += "\n\nProject Knowledge Base Documents:\n"
                            for f in project_context["files"]:
                                system += f"--- Document: {f['filename']} ---\n{f['content']}\n"
                        if project_context.get("cross_chat_history"):
                            system += "\n\nContext & Data learned from other chats in this same project:\n"
                            for chat in project_context["cross_chat_history"]:
                                system += f"--- Thread: {chat['name']} ---\n"
                                for msg in chat["messages"]:
                                    system += f"{msg['role'].capitalize()}: {msg['content']}\n"

                with trace.stage("gemini_direct", model=selected_model_key or "Claude Haiku 4.5"):
                    if (
                        selected_model_key
                        and selected_model_key != "gemini_brain"
                        and self.adapter_resolver is not None
                    ):
                        selected_adapter = self.adapter_resolver(selected_model_key)
                        answer = selected_adapter.converse(
                            system_prompt=system,
                            messages=answer_messages,
                            temperature=0.0,
                            max_tokens=1500,
                        )
                        tu = selected_adapter.get_token_usage()
                        ai, ao = tu.get("input_tokens", 0), tu.get("output_tokens", 0)
                    else:
                        answer, ai, ao = self._call_llm(system, query, max_tokens=1500, messages=answer_messages)
                        if not answer:
                            logger.info("Bedrock direct answer returned empty, retrying with a fresh adapter...")
                            bedrock = BedrockAdapter(model_id=HAIKU45_ID)
                            answer = bedrock.converse(
                                system_prompt=system,
                                messages=answer_messages,
                                temperature=0.0,
                                max_tokens=1500,
                            )
                            tu = bedrock.get_token_usage()
                            bi += tu.get("input_tokens", 0)
                            bo += tu.get("output_tokens", 0)
                bi += int(ai or 0)
                bo += int(ao or 0)
                llm_calls += 1
            except Exception as e:
                logger.warning("Bedrock direct-answer call failed, retrying: %s", e)
                try:
                    bedrock = BedrockAdapter(model_id=HAIKU45_ID)
                    answer = bedrock.converse(
                        system_prompt=system,
                        messages=answer_messages,
                        temperature=0.0,
                        max_tokens=1500,
                    )
                    tu = bedrock.get_token_usage()
                    bi += tu.get("input_tokens", 0)
                    bo += tu.get("output_tokens", 0)
                    llm_calls += 1
                except Exception as b_err:
                    trace_summary = trace.emit()
                    err_res = self._err(str(b_err), t0, gi, go, code=classify_exception(b_err))
                    err_res["query_trace"] = trace_summary
                    return err_res

            if not (answer or "").strip():
                err_res = self._err("all direct-answer providers returned empty", t0, gi, go, code=ErrorCode.MODEL_UNAVAILABLE)
                err_res["query_trace"] = trace.emit()
                return err_res

            elapsed = round(time.time() - t0, 2)
            trace_events = []
            if is_redacted:
                trace_events.append({
                    "step": "pii_redactor",
                    "status": "redacted",
                    "counts": redaction_counts,
                })
            trace_events.extend([
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "gemini_direct",
                    "reason": reason,
                },
                {
                    "step": "gemini_answer",
                    "model": selected_model_key or "Claude Haiku 4.5",
                    "tokens_in": ai,
                    "tokens_out": ao,
                },
            ])
            if session_id:
                with trace.stage("memory_write"):
                    self._persist_conversation(
                        session_id,
                        user_id,
                        organization_id,
                        raw_query,
                        answer,
                        agent_trace=trace_events,
                        db_name=db_name,
                    )

            trace_summary = trace.emit()
            return normalize_envelope({
                "answer": answer,
                "sql": None,
                "results": [],
                "error": None,
                "status": "ok",
                "notice": None,
                "data_source": None,
                "table_markdown": None,
                "request_id": new_request_id(),
                "pii_redacted": is_redacted,
                "pii_redactions": redaction_counts,
                "token_usage": {
                    "input_tokens": gi,
                    "output_tokens": go,
                    "llm_calls": llm_calls,
                    "cost_usd": self._cost(gi, go, 0, 0, ""),
                    "elapsed_seconds": elapsed,
                },
                "agent_trace": trace_events,
                "routing_info": {
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "gemini_direct",
                    "reason": reason,
                },
                "query_trace": trace_summary,
            })

        # 2b. RIGHT PATH: API → Anthropic
        if fast_hit is None:
            with trace.stage("endpoint_selection"):
                sel, ri, ro = select_endpoint(
                    query,
                    organization_id,
                    self._call_llm,
                    self._parse_json,
                    user_id=str(user_id),
                    session_state=session_state,
                )
            bi += int(ri or 0)
            bo += int(ro or 0)
            llm_calls += 1

        retrieved = self._retrieve(sel, organization_id, db_name, trace, auth_token=auth_token) if (use_api and sel) \
                    else Retrieved(Outcome.INVALID, reason="api_disabled_or_no_selection")

        # Phase E: Bounded 1-turn self-correction loop when initial endpoint fails or is invalid/unavailable
        if use_api and (not retrieved.usable) and (retrieved.outcome not in (Outcome.EMPTY, Outcome.DENIED)):
            failed_ep = getattr(retrieved, "endpoint", None) or (sel.get("endpoint") if sel else "unknown")
            feedback_msg = (
                f"Endpoint '{failed_ep}' failed (outcome={retrieved.outcome.value}, reason={retrieved.reason}). "
                "Please choose an alternative endpoint from the catalog that can answer the user's query, or return 'unsupported'."
            )
            logger.info("Phase E self-correction attempt for query '%.60s': %s", query, feedback_msg)
            with trace.stage("self_correction_retry"):
                sel_retry, ri_retry, ro_retry = select_endpoint(
                    query,
                    organization_id,
                    self._call_llm,
                    self._parse_json,
                    user_id=str(user_id),
                    session_state=session_state,
                    feedback=feedback_msg,
                )
            bi += int(ri_retry or 0)
            bo += int(ro_retry or 0)
            llm_calls += 1

            if sel_retry and sel_retry.get("endpoint") and sel_retry.get("endpoint") != failed_ep:
                retrieved_retry = self._retrieve(sel_retry, organization_id, db_name, trace, auth_token=auth_token)
                if retrieved_retry.usable or retrieved_retry.outcome in (Outcome.EMPTY, Outcome.DENIED):
                    sel = sel_retry
                    retrieved = retrieved_retry
                    logger.info("Phase E self-correction SUCCESS: recovered to endpoint=%s outcome=%s", retrieved.endpoint, retrieved.outcome.value)

        # Stage 4: an empty recent window is usually a question worth re-asking over
        # a longer look-back rather than a dead end. Runs before the usable/empty
        # dispatch below, so a recovered result flows through the normal narration
        # path and only picks up an extra notice.
        widened_note: Optional[str] = None
        if use_api and sel and retrieved.outcome is Outcome.EMPTY:
            retrieved_widened, widened_note = self._retry_widened(
                sel, organization_id, db_name, trace, auth_token=auth_token
            )
            if retrieved_widened is not None:
                retrieved = retrieved_widened

        endpoint = retrieved.endpoint or None
        tool_spec = tool_spec_for_endpoint(endpoint)
        subject = _subject_for(endpoint, tool_spec, query)

        # -- A. Usable data -> narrate --
        if retrieved.usable:
            data = retrieved.payload
            results_payload = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            formatter_name = tool_spec.formatter if tool_spec else "row_table"
            formatted_table = render(formatter_name, data, query=query)
            formatted_blocks = render_blocks(formatter_name, data, query=query)

            with trace.stage("bedrock_reasoning", intent=qtype):
                answer, b_label, bi_new, bo_new, narration_degraded = self._narrate_or_fallback(
                    query=query,
                    data=data,
                    endpoint=endpoint or "",
                    # Short prose, not the full table: `formatted_table`/`formatted_blocks`
                    # are already attached to the envelope below and rendered by the
                    # frontend on their own — falling back to the same table text here
                    # would duplicate it a second time as the narrated answer.
                    fallback_text=f"Here's your {subject}.",
                    intent=qtype,
                    session_id=session_id,
                    selected_model_key=selected_model_key,
                    get_project_context_by_session=get_project_context_by_session,
                    conversation_memory=conversation_memory,
                )
            if not narration_degraded:
                bi += int(bi_new or 0)
                bo += int(bo_new or 0)
                llm_calls += 1

            elapsed = round(time.time() - t0, 2)
            trace_events = []
            if is_redacted:
                trace_events.append({
                    "step": "pii_redactor",
                    "status": "redacted",
                    "counts": redaction_counts,
                })
            trace_events.extend([
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "reason": reason,
                },
                {"step": "gemini_api_selector", "endpoint": endpoint},
                {"step": "rest_api_call", "endpoint": endpoint, "status": "success"},
                {
                    "step": "anthropic_reasoning",
                    "model": b_label,
                    "tokens_in": bi_new,
                    "tokens_out": bo_new,
                },
            ])

            if session_id:
                with trace.stage("memory_write"):
                    self._persist_conversation(
                        session_id,
                        user_id,
                        organization_id,
                        raw_query,
                        answer,
                        agent_trace=trace_events,
                        db_name=db_name,
                    )

            if widened_note:
                # Say it in the answer itself, not only in the notice: an API
                # consumer that ignores `notice` must still be told these numbers
                # cover a different period than the one asked about.
                answer = (
                    f"There were no records for the period you asked about, so this "
                    f"covers {widened_note} instead.\n\n{answer}"
                )
                status = "partial"
                notice = notice_for("WIDENED_WINDOW", subject=subject)
            elif retrieved.outcome is Outcome.PARTIAL:
                status = "partial"
                notice = notice_for("PARTIAL_DATA", subject=subject)
            elif narration_degraded:
                status = "partial"
                notice = notice_for("MODEL_UNAVAILABLE", subject=subject)
            else:
                status = "ok"
                notice = None

            trace_summary = trace.emit()
            return normalize_envelope({
                "answer": answer,
                "sql": None,
                "results": results_payload,
                "error": None,
                "status": status,
                "notice": notice,
                "data_source": retrieved.to_data_source(),
                "table_markdown": formatted_table,
                "blocks": formatted_blocks,
                "request_id": new_request_id(),
                "pii_redacted": is_redacted,
                "pii_redactions": redaction_counts,
                "token_usage": {
                    "input_tokens": gi + bi,
                    "output_tokens": go + bo,
                    "llm_calls": llm_calls,
                    "cost_usd": self._cost(gi, go, bi, bo, bedrock_model_id),
                    "elapsed_seconds": elapsed,
                },
                "agent_trace": trace_events,
                "routing_info": {
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "api_endpoint": endpoint,
                    "bedrock_model": b_label,
                    "reason": reason,
                },
                "query_trace": trace_summary,
            })

        # -- B. Source reached, zero rows -> DETERMINISTIC answer, NO LLM --
        elif retrieved.outcome is Outcome.EMPTY:
            return self._empty_result(
                query=query,
                subject=subject,
                retrieved=retrieved,
                tool_spec=tool_spec,
                qtype=qtype,
                type_lbl=type_lbl,
                reason=reason,
                trace=trace,
                t0=t0,
                gi=gi,
                go=go,
                llm_calls=llm_calls,
                is_redacted=is_redacted,
                redaction_counts=redaction_counts,
                session_id=session_id,
                user_id=user_id,
                raw_query=raw_query,
                organization_id=organization_id,
                conversation_memory=conversation_memory,
                db_name=db_name,
                query_params=(sel or {}).get("query_params"),
            )

        # -- C. Tenant/auth rejection from upstream -> stop, do not fall through --
        elif retrieved.outcome is Outcome.DENIED:
            return self._degraded_result(
                ErrorCode.TENANT_FORBIDDEN,
                subject,
                retrieved,
                query=query,
                qtype=qtype,
                type_lbl=type_lbl,
                reason=reason,
                trace=trace,
                t0=t0,
                gi=gi,
                go=go,
                llm_calls=llm_calls,
                is_redacted=is_redacted,
                redaction_counts=redaction_counts,
                session_id=session_id,
                raw_query=raw_query,
                conversation_memory=conversation_memory,
                user_id=user_id,
                organization_id=organization_id,
                db_name=db_name,
            )

        # -- D. UNAVAILABLE / INVALID -> try the SQL fallback tier --
        # 2c. FALLBACK: DB engine
        logger.info("GeminiBrain DB fallback for: %.80s", query)
        METRICS.sql_fallback_entered.inc()
        try:
            with trace.stage("sql_fallback"):
                er = self._db_fallback(
                    query,
                    organization_id,
                    user_id=user_id,
                    session_id=session_id,
                    selected_model_key=selected_model_key,
                    raw_query=raw_query,
                    intent=qtype,
                    conversation_memory=conversation_memory,
                )
        except Exception as e:
            trace_summary = trace.emit()
            err_res = self._err(str(e), t0, gi, go, code=classify_exception(e), subject=subject)
            err_res["query_trace"] = trace_summary
            return err_res

        self._kick_rolling_summary(session_id, db_name)

        tu = er.get("token_usage", {})
        bi += int(tu.get("input_tokens", 0) or 0)
        bo += int(tu.get("output_tokens", 0) or 0)
        llm_calls += int(tu.get("llm_calls", 0) or 0)
        elapsed = round(time.time() - t0, 2)

        db_trace = []
        if is_redacted:
            db_trace.append({
                "step": "pii_redactor",
                "status": "redacted",
                "counts": redaction_counts,
            })
        db_trace.extend([
            {
                "step": "gemini_router",
                "type": qtype,
                "type_label": type_lbl,
                "path": "db_fallback",
                "reason": reason,
            },
            {
                "step": "api_selector",
                "result": "no_endpoint" if not sel else "api_failed",
            },
            {"step": "db_engine", "model": "Claude Haiku 4.5"},
        ] + er.get("agent_trace", []))

        trace_summary = trace.emit()
        return normalize_envelope({
            "answer": er.get("answer", "No answer available"),
            "sql": er.get("sql"),
            "results": er.get("results") or [],
            "error": er.get("error"),
            "status": "ok" if not er.get("error") else "degraded",
            "notice": None if not er.get("error") else notice_for(ErrorCode.SQL_FALLBACK_FAILED, subject=subject),
            "data_source": None,
            "table_markdown": None,
            "request_id": new_request_id(),
            "pii_redacted": is_redacted,
            "pii_redactions": redaction_counts,
            "token_usage": {
                "input_tokens": gi + bi,
                "output_tokens": go + bo,
                "llm_calls": llm_calls,
                "cost_usd": self._cost(gi, go, bi, bo, HAIKU45_ID),
                "elapsed_seconds": elapsed,
            },
            "agent_trace": db_trace,
            "routing_info": {
                "type": qtype,
                "type_label": type_lbl,
                "path": "db_fallback",
                "reason": reason,
            },
            "query_trace": trace_summary,
        })

    def _run_stream_inner(
        self,
        query: str,
        organization_id: Optional[int] = None,
        db_name: str = "accutax_bk",
        use_api: bool = True,
        user_id: int = DEFAULT_USER_ID,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        allowed_org_ids: Optional[list[int]] = None,
        auth_token: str = "",
        ui_context: Optional[dict] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Run query through Gemini Brain pipeline with streaming status updates.

        `auth_token`, when given, is used explicitly for every Accutax REST call
        instead of relying on the active_auth_token ContextVar. This matters much
        more here than in run(): Starlette drives this generator through
        anyio.to_thread.run_sync once per yielded chunk (see
        starlette.concurrency.iterate_in_threadpool), copying a fresh context on
        every call — a ContextVar.set() made earlier in the same generator does
        not survive past its first yield, so by the time the actual API call
        happens (several status yields later), the token would silently be gone.
        """
        trace = QueryTrace(org_id=organization_id)
        if session_id and not is_valid_uuid(session_id):
            session_id = None
        if selected_model_key and selected_model_key.strip().lower() in ("string", "null", "none", ""):
            selected_model_key = None

        with trace.stage("pii_redaction"):
            raw_query = query
            redacted_query, redaction_counts = redact_pii(query)
            is_redacted = sum(redaction_counts.values()) > 0
            if is_redacted:
                logger.info("PII redacted from streaming query. Counts: %s", redaction_counts)
            query = redacted_query

        with trace.stage("tenant_isolation"):
            organization_id = self._enforce_tenant_isolation(
                organization_id=organization_id,
                query=query,
                db_name=db_name,
                allowed_org_ids=allowed_org_ids,
                user_id=user_id,
                session_id=session_id,
            )
            trace.set_org_id(organization_id)

        t0 = time.time()
        gi = go = bi = bo = llm_calls = 0
        bedrock_model_id = HAIKU45_ID

        # Phase D: Load active session context + compact conversation window
        session_state = get_state_by_session(session_id, db_name=db_name) if session_id else {}
        if ui_context:
            session_state.update({k: v for k, v in ui_context.items() if v is not None})
        session_state = attach_working_memory(
            session_id, user_id, organization_id, session_state, db_name=db_name
        )
        conversation_memory = session_state.get("_memory_block") or None

        # Phase 2: Fast Router Pre-Step (bypasses LLM classification & selection)
        router_source = "llm"
        fast_hit = None
        sel = None
        if is_conversation_meta_query(query):
            router_source = "conversation_meta"
            qtype = 1
            reason = "Question about this chat's prior messages"
            type_lbl = self._type_label(qtype)
            logger.info("GeminiBrain streaming conversation-meta → direct answer (skip API/SQL)")
        else:
            if use_api:
                fast_hit = fast_route(query, organization_id, user_id=str(user_id), session_state=session_state)

            if fast_hit is not None:
                router_source = "fast"
                qtype = fast_hit.intent
                reason = f"Fast router matched: {fast_hit.rule_name}"
                type_lbl = self._type_label(qtype)
                sel = fast_hit.to_selection_dict()
                logger.info("GeminiBrain streaming (FastRouter) → endpoint=%s rule=%s (0 Gemini LLM calls)", fast_hit.endpoint, fast_hit.rule_name)
            else:
                METRICS.llm_router_calls.inc()
                # 1. Classify
                yield {"status": "Understanding request", "type": "classification"}
                with trace.stage("classification"):
                    routing, ri, ro = classify_intent(query, self._call_llm, self._parse_json, session_state=session_state)
                bi += int(ri or 0)
                bo += int(ro or 0)
                llm_calls += 1
                qtype = routing.get("type", 4)
                reason = routing.get("reason", "")
                type_lbl = self._type_label(qtype)
                logger.info("GeminiBrain type=%d (%s) — %s", qtype, type_lbl, reason)

        # 2a. LEFT PATH: Gemini direct
        # Type 7 (Summary & Advice) is normally LEFT (no data), but when the
        # fast router already matched a concrete endpoint for it (e.g. the
        # dashboard_overview rule -> /report/profit-loss), that endpoint
        # selection must not be thrown away — fall through to the RIGHT path
        # below so the advice is grounded in real numbers instead of the
        # model refusing or bluffing without data.
        if qtype in LEFT_PATH_TYPES and not (qtype == 7 and sel is not None):
            yield {"status": "Generating response", "type": "generation"}
            try:
                system = append_memory_block(DIRECT_ANSWER_SYSTEM_PROMPT, session_state)
                if getattr(self, "_answer_brief", False):
                    system += (
                        "\nThe user asked for a brief answer. Use at most five short "
                        "markdown bullets. No long paragraphs.\n"
                    )
                answer_messages = self._direct_answer_messages(session_state, query)
                if session_id:
                    project_context = get_project_context_by_session(session_id)
                    if project_context:
                        if project_context.get("files"):
                            system += "\n\nProject Knowledge Base Documents:\n"
                            for f in project_context["files"]:
                                system += f"--- Document: {f['filename']} ---\n{f['content']}\n"
                        if project_context.get("cross_chat_history"):
                            system += "\n\nContext & Data learned from other chats in this same project:\n"
                            for chat in project_context["cross_chat_history"]:
                                system += f"--- Thread: {chat['name']} ---\n"
                                for msg in chat["messages"]:
                                    system += f"{msg['role'].capitalize()}: {msg['content']}\n"

                with trace.stage("gemini_direct", model=selected_model_key or "Claude Haiku 4.5"):
                    if (
                        selected_model_key
                        and selected_model_key != "gemini_brain"
                        and self.adapter_resolver is not None
                    ):
                        selected_adapter = self.adapter_resolver(selected_model_key)
                        answer = selected_adapter.converse(
                            system_prompt=system,
                            messages=answer_messages,
                            temperature=0.0,
                            max_tokens=1500,
                        )
                        tu = selected_adapter.get_token_usage()
                        ai, ao = tu.get("input_tokens", 0), tu.get("output_tokens", 0)
                    else:
                        try:
                            adapter = BedrockAdapter(model_id=HAIKU45_ID, label="Claude Haiku 4.5")
                            chunks = []
                            for token in adapter.converse_stream(
                                system_prompt=system,
                                messages=answer_messages,
                                temperature=0.0,
                                max_tokens=1500,
                            ):
                                chunks.append(token)
                                yield {"type": "token", "token": token, "status": "Generating response"}
                            answer = "".join(chunks)
                            tu = adapter.get_token_usage()
                            ai, ao = tu.get("input_tokens", 0), tu.get("output_tokens", 0)
                        except Exception as b_err:
                            logger.warning("Bedrock direct streaming failed: %s", b_err)
                            answer, ai, ao = "", 0, 0
                bi += int(ai or 0)
                bo += int(ao or 0)
                llm_calls += 1
            except Exception as e:
                logger.warning("Bedrock direct streaming exception, retrying non-streamed: %s", e)
                try:
                    bedrock = BedrockAdapter(model_id=HAIKU45_ID)
                    answer = bedrock.converse(
                        system_prompt=system,
                        messages=answer_messages,
                        temperature=0.0,
                        max_tokens=1500,
                    )
                    tu = bedrock.get_token_usage()
                    bi += tu.get("input_tokens", 0)
                    bo += tu.get("output_tokens", 0)
                    llm_calls += 1
                except Exception as b_err:
                    trace_summary = trace.emit()
                    err_res = self._err(f"Direct answer failed: {b_err}", t0)
                    err_res["query_trace"] = trace_summary
                    yield {
                        "status": f"Direct answer generation failed: {str(b_err)}",
                        "type": "error",
                    }
                    yield {"final_result": err_res}
                    return

            yield {"status": "Finalizing response", "type": "finalization"}
            elapsed = round(time.time() - t0, 2)
            trace_events = [
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "gemini_direct",
                    "reason": reason,
                },
                {
                    "step": "gemini_answer",
                    "model": selected_model_key or "Claude Haiku 4.5",
                    "tokens_in": ai,
                    "tokens_out": ao,
                },
            ]
            if session_id:
                with trace.stage("memory_write"):
                    self._persist_conversation(
                        session_id,
                        user_id,
                        organization_id,
                        raw_query,
                        answer,
                        agent_trace=trace_events,
                        db_name=db_name,
                    )

            trace_summary = trace.emit()
            yield {
                "final_result": {
                    "answer": answer,
                    "sql": None,
                    "results": [],
                    "error": None,
                    "token_usage": {
                        "input_tokens": gi,
                        "output_tokens": go,
                        "llm_calls": llm_calls,
                        "cost_usd": self._cost(gi, go, 0, 0, ""),
                        "elapsed_seconds": elapsed,
                    },
                    "agent_trace": trace_events,
                    "routing_info": {
                        "type": qtype,
                        "type_label": type_lbl,
                        "path": "gemini_direct",
                        "reason": reason,
                    },
                    "query_trace": trace_summary,
                }
            }
            return

        # 2b. RIGHT PATH: API → Anthropic
        if fast_hit is None:
            yield {"status": "Determining data source", "type": "classification"}
            with trace.stage("endpoint_selection"):
                sel, ri, ro = select_endpoint(
                    query,
                    organization_id,
                    self._call_llm,
                    self._parse_json,
                    user_id=str(user_id),
                    session_state=session_state,
                )
            bi += int(ri or 0)
            bo += int(ro or 0)
            llm_calls += 1

        retrieved = self._retrieve(sel, organization_id, db_name, trace, auth_token=auth_token) if (use_api and sel) \
                    else Retrieved(Outcome.INVALID, reason="api_disabled_or_no_selection")

        # Phase E: Bounded 1-turn self-correction loop for streaming
        if use_api and (not retrieved.usable) and (retrieved.outcome not in (Outcome.EMPTY, Outcome.DENIED)):
            failed_ep = getattr(retrieved, "endpoint", None) or (sel.get("endpoint") if sel else "unknown")
            feedback_msg = (
                f"Endpoint '{failed_ep}' failed (outcome={retrieved.outcome.value}, reason={retrieved.reason}). "
                "Please choose an alternative endpoint from the catalog that can answer the user's query, or return 'unsupported'."
            )
            logger.info("Phase E streaming self-correction attempt for query '%.60s': %s", query, feedback_msg)
            yield {"status": "Retrying with alternative endpoint", "type": "classification"}
            with trace.stage("self_correction_retry"):
                sel_retry, ri_retry, ro_retry = select_endpoint(
                    query,
                    organization_id,
                    self._call_llm,
                    self._parse_json,
                    user_id=str(user_id),
                    session_state=session_state,
                    feedback=feedback_msg,
                )
            bi += int(ri_retry or 0)
            bo += int(ro_retry or 0)
            llm_calls += 1

            if sel_retry and sel_retry.get("endpoint") and sel_retry.get("endpoint") != failed_ep:
                retrieved_retry = self._retrieve(sel_retry, organization_id, db_name, trace, auth_token=auth_token)
                if retrieved_retry.usable or retrieved_retry.outcome in (Outcome.EMPTY, Outcome.DENIED):
                    sel = sel_retry
                    retrieved = retrieved_retry
                    logger.info("Phase E streaming self-correction SUCCESS: recovered to endpoint=%s outcome=%s", retrieved.endpoint, retrieved.outcome.value)

        # Stage 4: an empty recent window is usually a question worth re-asking over
        # a longer look-back rather than a dead end. Runs before the usable/empty
        # dispatch below, so a recovered result flows through the normal narration
        # path and only picks up an extra notice.
        widened_note: Optional[str] = None
        if use_api and sel and retrieved.outcome is Outcome.EMPTY:
            retrieved_widened, widened_note = self._retry_widened(
                sel, organization_id, db_name, trace, auth_token=auth_token
            )
            if retrieved_widened is not None:
                retrieved = retrieved_widened

        endpoint = retrieved.endpoint or None
        tool_spec = tool_spec_for_endpoint(endpoint)
        subject = _subject_for(endpoint, tool_spec, query)

        # -- A. Usable data -> narrate --
        if retrieved.usable:
            data = retrieved.payload
            results_payload = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            formatter_name = tool_spec.formatter if tool_spec else "row_table"
            formatted_table = render(formatter_name, data, query=query)
            formatted_blocks = render_blocks(formatter_name, data, query=query)

            # Emit data table immediately so frontend can paint table before narration starts
            yield {
                "type": "data_table",
                "table": formatted_table,
                "row_count": retrieved.row_count,
                "truncated": retrieved.truncated,
            }
            yield {"status": "Analyzing financial figures", "type": "reasoning"}
            answer = ""
            # Stream the widened-window note ahead of the narration, so the caveat
            # reaches the reader before the numbers do rather than after. It is also
            # seeded into `answer` so the saved/returned text carries it too.
            widened_prefix = ""
            if widened_note:
                widened_prefix = (
                    f"There were no records for the period you asked about, so this "
                    f"covers {widened_note} instead.\n\n"
                )
                yield {"type": "token", "token": widened_prefix, "status": "Generating response"}
            b_label = "Claude Haiku 4.5"
            bi_new = bo_new = 0
            narration_degraded = False
            last_exc: Optional[Exception] = None
            for attempt in range(2):
                full_chunks: list = []
                try:
                    with trace.stage("bedrock_reasoning", intent=qtype):
                        for chunk, meta in reason_over_data_stream(
                            query=query,
                            data=data,
                            endpoint=endpoint or "",
                            intent=qtype,
                            session_id=session_id,
                            selected_model_key=selected_model_key,
                            adapter_resolver=self.adapter_resolver,
                            get_project_context_by_session=get_project_context_by_session,
                            conversation_memory=conversation_memory,
                            brief=bool(getattr(self, "_answer_brief", False)),
                        ):
                            if meta is None:
                                full_chunks.append(chunk)
                                yield {"type": "token", "token": chunk, "status": "Generating response"}
                            else:
                                answer = chunk
                                b_label, bi_new, bo_new = meta
                    bi += int(bi_new or 0)
                    bo += int(bo_new or 0)
                    llm_calls += 1
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    logger.warning("Streaming narration attempt %d failed: %s", attempt + 1, e)
                    if full_chunks:
                        # Partial narration already streamed to the client -- keep it rather
                        # than retrying, which would duplicate/conflict with what was sent.
                        answer = "".join(full_chunks).strip()
                        b_label = "Claude Haiku 4.5 (partial)"
                        bi_new = bo_new = 0
                        last_exc = None
                        break

            if last_exc is not None:
                # Both attempts failed before any token streamed. `formatted_table`/
                # `formatted_blocks` are already sent to the frontend separately
                # (the `data_table` event above, and `blocks` in the final envelope
                # below) and rendered there on their own, so the narrated answer
                # falls back to a short line instead of repeating the same table
                # a second time as prose.
                logger.error("Streaming narration failed after retry, falling back to short notice: %s", last_exc)
                answer = f"Here's your {subject}."
                yield {"type": "token", "token": answer}
                b_label = "None (Narration Unavailable — Fallback Table)"
                bi_new = bo_new = 0
                narration_degraded = True

            yield {"status": "Finalizing response", "type": "finalization"}
            elapsed = round(time.time() - t0, 2)
            trace_events = [
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "reason": reason,
                },
                {"step": "gemini_api_selector", "endpoint": endpoint},
                {"step": "rest_api_call", "endpoint": endpoint, "status": "success"},
                {
                    "step": "anthropic_reasoning",
                    "model": b_label,
                    "tokens_in": bi_new,
                    "tokens_out": bo_new,
                },
            ]

            if session_id:
                with trace.stage("memory_write"):
                    self._persist_conversation(
                        session_id,
                        user_id,
                        organization_id,
                        raw_query,
                        answer,
                        agent_trace=trace_events,
                        db_name=db_name,
                    )

            if widened_prefix:
                # The prefix was streamed to the client; make the persisted answer match.
                answer = widened_prefix + answer
                status = "partial"
                notice = notice_for("WIDENED_WINDOW", subject=subject)
                yield {"type": "notice", "notice": notice}
            elif retrieved.outcome is Outcome.PARTIAL:
                status = "partial"
                notice = notice_for("PARTIAL_DATA", subject=subject)
                yield {"type": "notice", "notice": notice}
            elif narration_degraded:
                status = "partial"
                notice = notice_for("MODEL_UNAVAILABLE", subject=subject)
                yield {"type": "notice", "notice": notice}
            else:
                status = "ok"
                notice = None

            trace_summary = trace.emit()
            yield {
                "final_result": normalize_envelope({
                    "answer": answer,
                    "sql": None,
                    "results": results_payload,
                    "error": None,
                    "status": status,
                    "notice": notice,
                    "data_source": retrieved.to_data_source(),
                    "table_markdown": formatted_table,
                    "blocks": formatted_blocks,
                    "request_id": new_request_id(),
                    "token_usage": {
                        "input_tokens": gi + bi,
                        "output_tokens": go + bo,
                        "llm_calls": llm_calls,
                        "cost_usd": self._cost(gi, go, bi, bo, bedrock_model_id),
                        "elapsed_seconds": elapsed,
                    },
                    "agent_trace": trace_events,
                    "routing_info": {
                        "type": qtype,
                        "type_label": type_lbl,
                        "path": "api_then_anthropic",
                        "api_endpoint": endpoint,
                        "bedrock_model": b_label,
                        "reason": reason,
                    },
                    "query_trace": trace_summary,
                })
            }
            return

        # -- B. Source reached, zero rows -> DETERMINISTIC answer, NO LLM --
        elif retrieved.outcome is Outcome.EMPTY:
            empty_res = self._empty_result(
                query=query,
                subject=subject,
                retrieved=retrieved,
                tool_spec=tool_spec,
                qtype=qtype,
                type_lbl=type_lbl,
                reason=reason,
                trace=trace,
                t0=t0,
                gi=gi,
                go=go,
                llm_calls=llm_calls,
                is_redacted=is_redacted,
                redaction_counts=redaction_counts,
                session_id=session_id,
                user_id=user_id,
                raw_query=raw_query,
                organization_id=organization_id,
                conversation_memory=conversation_memory,
                db_name=db_name,
                query_params=(sel or {}).get("query_params"),
            )
            yield {"type": "notice", "notice": empty_res["notice"]}
            yield {"type": "token", "token": empty_res["answer"]}
            yield {"final_result": empty_res}
            return

        # -- C. Tenant/auth rejection from upstream -> stop, do not fall through --
        elif retrieved.outcome is Outcome.DENIED:
            degraded_res = self._degraded_result(
                ErrorCode.TENANT_FORBIDDEN,
                subject,
                retrieved,
                query=query,
                qtype=qtype,
                type_lbl=type_lbl,
                reason=reason,
                trace=trace,
                t0=t0,
                gi=gi,
                go=go,
                llm_calls=llm_calls,
                is_redacted=is_redacted,
                redaction_counts=redaction_counts,
                session_id=session_id,
                raw_query=raw_query,
                conversation_memory=conversation_memory,
                user_id=user_id,
                organization_id=organization_id,
                db_name=db_name,
            )
            yield {"type": "error", "notice": degraded_res["notice"]}
            yield {"final_result": degraded_res}
            return

        # -- D. UNAVAILABLE / INVALID -> try the SQL fallback tier --
        # 2c. FALLBACK: DB engine
        logger.info("GeminiBrain DB fallback for: %.80s", query)
        METRICS.sql_fallback_entered.inc()
        yield {"status": "Routing to local database engine", "type": "retrieval"}

        with trace.stage("sql_fallback"):
            for chunk in self._db_fallback_stream(
                query,
                organization_id,
                user_id=user_id,
                session_id=session_id,
                selected_model_key=selected_model_key,
                raw_query=raw_query,
                intent=qtype,
                conversation_memory=conversation_memory,
            ):
                if isinstance(chunk, dict) and "final_result" in chunk:
                    trace_summary = trace.emit()
                    chunk["final_result"]["query_trace"] = trace_summary
                    chunk["final_result"] = normalize_envelope(chunk["final_result"])
                yield chunk
            self._kick_rolling_summary(session_id, db_name)

