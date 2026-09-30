"""
multi_org.py — Answer one question across several organizations.

Each organization runs through the ordinary single-organization pipeline, in
parallel, with every tenant-isolation layer it already has: the allow-list
check in _enforce_tenant_isolation, Postgres RLS via app.current_org, the SQL
tenant guard, the per-org result cache, and the verified org overwrite on every
REST call. Nothing here widens any of those to a set of organizations. The
per-org results are merged afterwards, and one model call writes the
comparison from them.

The caller must already have authorized every org ID (authorize_org_scope).
"""
from __future__ import annotations

import contextvars
import functools
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generator, List, Optional

from gemini_brain.config.constants import HAIKU45_ID
from gemini_brain.config.pricing import gemini_brain_cost
from gemini_brain.orchestrator.multi_org_metrics import BY_KEY as METRICS_BY_KEY
from gemini_brain.orchestrator.multi_org_metrics import (
    build_comparison,
    build_multi_comparison,
    build_series,
    computed_answer,
    computed_view,
    limit_rows,
)
from gemini_brain.orchestrator import multi_org_followup as followup
from gemini_brain.orchestrator.multi_org_log import record_plan
from gemini_brain.orchestrator.multi_org_plan import DIRECT, UNSUPPORTED, fetch_for_org
from gemini_brain.orchestrator.multi_org_present import (
    build_presentation,
    collapsed_org_sections,
    is_list_question,
)
from gemini_brain.policy.verifier import unverified_note, verify_answer, verify_attributed
from gemini_brain.resilience import new_request_id, normalize_envelope, notice_for

logger = logging.getLogger("gemini_brain.orchestrator.multi_org")

# A per-org result with one of these statuses carries an answer worth comparing.
# "empty" is a real answer: the source was reached and holds no rows.
_ANSWERED = ("ok", "partial", "empty")

#: Organizations fetched at the same time.
MAX_PARALLEL_ORGS = 4

# Bounds on what each org contributes to the comparison prompt.
_MAX_ROWS_PER_ORG = 30
_MAX_DATA_CHARS_PER_ORG = 6000
_MAX_ANSWER_CHARS_PER_ORG = 1500
#: The comparison asks for at most 8 bullets (about 400 tokens). 2000 let a
#: looping model write the same bullets eight times before it was cut off.
COMPARE_MAX_TOKENS = 900

COMPARE_SYSTEM_PROMPT = """You are a finance assistant comparing several organizations for one user.
You receive the user's question and, for each organization, its name, currency, status, a short
answer and the data it was based on. Write one answer that responds to the question across all of them.

Rules:
- Use only the figures given. Never invent or estimate a number.
- Name the organization next to every figure.
- Show every amount with its currency code.
- The message states whether the organizations share one currency. Trust that statement; never claim currencies differ when it says they are the same.
- Never add, subtract or rank amounts in different currencies as if they were one unit. When the statement says currencies differ, show those amounts side by side and say so.
- If an organization has no data or could not be retrieved, say so plainly. Do not fill the gap.
- Prefer a compact markdown table when comparing the same metric across organizations.
"""


STANDALONE_SYSTEM_PROMPT = """Rewrite the user's latest message as one standalone question.
Use the earlier conversation only to resolve references such as "that", "same for", "last quarter" or
"compare them". Keep every metric, period and name explicit. Keep the earlier question's period (for
example "this year") unless the latest message names a different one. Do not answer the question. Do not
add organizations, figures or topics the user did not ask about. Return only the question.
"""

# Bounds on the conversation sent to the standalone-question rewrite.
_HISTORY_MESSAGES = 6
_HISTORY_CHARS_PER_MESSAGE = 500
_MAX_STANDALONE_CHARS = 500


def _load_history(session_id: str, db_name: str) -> List[Dict[str, Any]]:
    from gemini_brain.memory.session_memory import get_history_by_session

    return get_history_by_session(session_id, limit=_HISTORY_MESSAGES, db_name=db_name)


#: Openings and references that only make sense with the earlier conversation.
_FOLLOW_UP = re.compile(
    r"^\s*(and|also|but|so|then|now|what\s+about|how\s+about|same|ok(ay)?)\b"
    r"|\b(them|those|these|that\s+one|the\s+same|same\s+(for|as)|previous(ly)?|above|instead|again)\b"
    r"|\b(first|second|third|last|top|bottom|other)\s+ones?\b|\b(it|its|they)\b",
    re.IGNORECASE,
)


def needs_rewrite(query: str) -> bool:
    """Whether a question needs the conversation to be understood.

    A question that already names a metric or a list ("compare total revenue
    this year") is planned as written: rewriting it only rephrases it, and a
    different phrasing can take a different path. Questions that lean on the
    earlier turns ("and Q2?", "same for payables") are rewritten.
    """
    if _FOLLOW_UP.search(query or ""):
        return True
    from gemini_brain.orchestrator.multi_org_metrics import (
        is_concept_question,
        is_scorecard_question,
        match_metric,
        unsupported_terms,
    )
    from gemini_brain.orchestrator.multi_org_present import is_list_question

    # A scorecard question ("are any of my companies in trouble") took the data
    # path only when a rewrite happened to add a figure: same question, two
    # answers. "What is VAT?" was rewritten into a VAT figures question; "why
    # is ..." still leans on the answer before it.
    definition = is_concept_question(query) and bool(_DEFINITION.search(query))
    return not (match_metric(query) or is_list_question(query) or definition
                or is_scorecard_question(query) or unsupported_terms(query))


_DEFINITION = re.compile(r"^\s*(what\s+(is|are|does)|define|explain|meaning\s+of)\b|\bdifference\s+between\b",
                         re.IGNORECASE)


def _standalone_question(query: str, history: List[Dict[str, Any]], runner: Any) -> tuple[str, int, int]:
    """The follow-up as a self-contained question, plus the tokens the rewrite used.

    Per-org runs have no conversation memory of their own, so a follow-up like
    "and last quarter?" must carry its context in the question itself. The
    history comes from this thread only, whose organization scope the route
    has already matched to this request. Falls back to the original query.
    """
    if not history:
        return query, 0, 0
    lines = [
        f"{str(m.get('role') or '').capitalize()}: {str(m.get('content') or '')[:_HISTORY_CHARS_PER_MESSAGE]}"
        for m in history[-_HISTORY_MESSAGES:]
    ]
    user_text = "Conversation so far:\n" + "\n".join(lines) + f"\n\nLatest message: {query}"
    try:
        text, ti, to = runner._call_llm(
            STANDALONE_SYSTEM_PROMPT, user_text, max_tokens=300, purpose="multi_org_standalone",
        )
    except Exception as e:
        logger.warning("multi_org: standalone rewrite failed, using the query as asked: %s", e)
        return query, 0, 0
    text = (text or "").strip()
    if not text or len(text) > _MAX_STANDALONE_CHARS:
        return query, int(ti or 0), int(to or 0)
    return text, int(ti or 0), int(to or 0)


def _persist_turn(
    session_id: str,
    user_id: int,
    org_ids: List[int],
    query: str,
    final: Dict[str, Any],
    runner: Any,
    db_name: str,
) -> None:
    """Save the merged turn to the thread. Never fails the answer."""
    from gemini_brain.memory.conversation_window import persist_turn_and_maybe_summarize
    from gemini_brain.memory.session_memory import ensure_session, update_last_assistant_blocks

    try:
        # Fixes the thread's scope to this org set on its first turn, and
        # refuses to write into a thread scoped to a different set.
        if not ensure_session(session_id, user_id, organization_ids=org_ids, db_name=db_name):
            logger.warning("multi_org: session %s refused org set %s; turn not saved", session_id, org_ids)
            return
        persist_turn_and_maybe_summarize(
            session_id,
            user_id,
            None,
            query,
            final.get("answer") or "",
            agent_trace=final.get("agent_trace") or [],
            call_llm=functools.partial(runner._call_llm, purpose="conversation_summary"),
            db_name=db_name,
        )
        update_last_assistant_blocks(session_id, final.get("blocks") or [])
    except Exception as e:
        logger.warning("multi_org: could not save turn to session %s: %s", session_id, e)


@dataclass
class OrgRun:
    """One organization's part of a multi-org answer."""

    org_id: int
    name: str
    currency: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def answered(self) -> bool:
        return self.result is not None and (self.result.get("status") or "ok") in _ANSWERED

    @property
    def status(self) -> str:
        if self.result is None:
            return "failed"
        return self.result.get("status") or "ok"

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.org_id,
            "name": self.name,
            "currency": self.currency,
            "status": self.status,
        }


def run_multi_org_stream(
    query: str,
    org_ids: List[int],
    org_meta: Dict[int, Dict[str, Any]],
    *,
    runner_factory: Callable[[], Any],
    run_kwargs: Dict[str, Any],
    planner: Optional[Callable[..., Any]] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Fan the question out to each organization, then yield the merged result.

    Yields progress chunks as each organization finishes, and finally
    ``{"final_result": ...}``. ``run_kwargs`` go to each per-org
    ``runner.run`` call; ``allowed_org_ids`` in them keeps the runner's own
    allow-list check in force for every org.

    With a ``planner`` (see multi_org_plan.plan_query), the question is routed
    once: data questions fetch the same selection for every org with no
    per-org model calls, and questions that need no data are answered once.
    An org whose planned fetch is unusable, or a question no plan fits, falls
    back to the full per-org pipeline.
    """
    t0 = time.time()
    # The thread belongs to the whole org set, so per-org runs neither read nor
    # write it: they would each see, and save, only their own slice. The
    # thread's history reaches them through the standalone question instead,
    # and the merged turn is saved once at the end.
    session_id = run_kwargs.get("session_id")
    db_name = run_kwargs.get("db_name") or ""
    kwargs = {k: v for k, v in run_kwargs.items() if k != "session_id"}
    compare_runner = runner_factory()

    question, rewrite_in, rewrite_out = query, 0, 0
    thread_org_ids = list(org_ids)
    history: List[Dict[str, Any]] = []
    if session_id:
        try:
            history = _load_history(session_id, db_name)
        except Exception as e:
            logger.warning("multi_org: could not load history for session %s: %s", session_id, e)
    resolved = None
    if history and needs_rewrite(query):
        # Period, top-N, new-figure and drop-an-org follow-ups are folded in
        # code; anything else goes to the model rewrite, which keeps the period.
        resolved = followup.resolve(query, history, org_meta, thread_org_ids,
                                    stands_alone=lambda text: not needs_rewrite(text))
        if resolved is None:
            question, rewrite_in, rewrite_out = _standalone_question(query, history, compare_runner)
            question = followup.keep_period(question, history)
    if resolved is None:
        # A question that names selected orgs ("revenue without Org A") narrows the set itself.
        mods = followup.parse(question, org_meta, thread_org_ids)
        resolved = followup.Resolved(question=followup.strip_org_names(question, org_meta, thread_org_ids)[0],
                                     exclude=set(mods.exclude), only=set(mods.only) or None)
    question = " ".join(resolved.question.split()) or query
    # Only a subset of the thread's (already authorized) orgs, never more.
    org_ids = resolved.org_ids(thread_org_ids)
    scope_notes = []
    left_out = [str((org_meta.get(o) or {}).get("name") or f"Organization {o}")
                for o in thread_org_ids if o not in org_ids]
    if left_out:
        scope_notes.append("Left out as asked: " + ", ".join(left_out) + ".")

    runs = {
        oid: OrgRun(
            org_id=oid,
            name=str((org_meta.get(oid) or {}).get("name") or f"Organization {oid}"),
            currency=str((org_meta.get(oid) or {}).get("currency") or ""),
        )
        for oid in org_ids
    }

    # Route the question once for the whole org set (planner given), instead
    # of once per org. The plan call sees the redacted question only.
    plan = None
    if planner is not None:
        from gemini_brain.pii.redactor import redact_pii

        safe_question, _ = redact_pii(question)
        plan = planner(safe_question, org_ids[0], compare_runner, int(run_kwargs.get("user_id") or 0))

    if plan is not None and plan.kind == DIRECT:
        # No data needed: the answer does not depend on the organization.
        yield {"status": "Answering once for all organizations", "type": "multi_org"}
        final = _answer_once(question, org_ids[0], compare_runner, kwargs, plan)
        if session_id:
            _persist_turn(session_id, int(run_kwargs.get("user_id") or 0), thread_org_ids, query, final, compare_runner, db_name)
        record_plan(question, plan, final, len(org_ids))
        yield {"final_result": final}
        return

    if plan is not None and plan.kind == UNSUPPORTED:
        final = _unsupported_answer(plan, [runs[oid] for oid in org_ids], t0)
        if session_id:
            _persist_turn(session_id, int(run_kwargs.get("user_id") or 0), thread_org_ids, query, final, compare_runner, db_name)
        record_plan(question, plan, final, len(org_ids))
        yield {"final_result": final}
        return

    def _run_one(oid: int) -> Dict[str, Any]:
        # One runner per thread: the runner keeps per-call state on itself.
        runner = runner_factory()
        if plan is not None:
            fetched = fetch_for_org(
                plan, oid, runner,
                query=question,
                allowed_org_ids=kwargs.get("allowed_org_ids"),
                user_id=int(kwargs.get("user_id") or 0),
                db_name=db_name,
                auth_token=kwargs.get("auth_token") or "",
            )
            if fetched is not None:
                return fetched
            logger.info("multi_org: planned fetch unusable for org %s; running full pipeline", oid)
        # org_scoped_rest_only: a REST endpoint that returns every org's rows
        # would put sibling orgs' data under this org's label.
        return runner.run(
            query=question, organization_id=oid, session_id=None,
            org_scoped_rest_only=True, **kwargs,
        )

    yield {"status": f"Querying {len(org_ids)} organizations", "type": "multi_org"}

    # Bounded: ten simultaneous report calls overload the Accutax backend and
    # time out, which pushes orgs onto the slow full-pipeline fallback.
    with ThreadPoolExecutor(max_workers=min(len(org_ids), MAX_PARALLEL_ORGS), thread_name_prefix="multi-org") as pool:
        # Each task gets its own copy of the request context, so the auth token
        # and trace ids set by the route reach every worker thread.
        futures = {
            pool.submit(contextvars.copy_context().run, _run_one, oid): oid
            for oid in org_ids
        }
        for future in as_completed(futures):
            oid = futures[future]
            run = runs[oid]
            try:
                run.result = future.result()
            except Exception as e:
                # The message stays in the log; the user sees only the org name.
                logger.warning("multi_org: organization %s failed: %s", oid, e)
                run.error = type(e).__name__
            yield {
                "status": f"{run.name}: {'done' if run.answered else 'no result'}",
                "type": "multi_org",
                "organization_id": oid,
            }

    ordered = [runs[oid] for oid in org_ids]
    comparison = _computed_comparison(plan, ordered, question)
    final = merge_org_results(question, ordered, compare_runner, t0=t0, comparison=comparison,
                              notes=((plan.notes if plan is not None else []) + scope_notes) or None,
                              title=_list_title(plan))
    _add_usage(final, rewrite_in, rewrite_out, 1 if (rewrite_in or rewrite_out) else 0)
    if plan is not None:
        _add_usage(final, plan.input_tokens, plan.output_tokens, plan.llm_calls)
        final["agent_trace"].insert(0, plan.trace_step())
    if question != query:
        final["agent_trace"].insert(0, {"step": "multi_org_standalone_question", "question": question})
    if session_id:
        _persist_turn(session_id, int(run_kwargs.get("user_id") or 0), thread_org_ids, query, final, compare_runner, db_name)
    record_plan(question, plan, final, len(org_ids))
    yield {"final_result": final}


def _computed_comparison(plan: Any, runs: List["OrgRun"], question: str) -> Optional[Dict[str, Any]]:
    """The comparison computed in code, cut to the question's top/bottom N; None when no plan computes one."""
    comparison = _build_comparison(plan, runs, question)
    return limit_rows(comparison, question) if comparison is not None else None


def _build_comparison(plan: Any, runs: List["OrgRun"], question: str) -> Optional[Dict[str, Any]]:
    """The comparison computed in code for a metric, multi-metric or series plan; None otherwise."""
    if plan is None:
        return None
    if plan.series and plan.series.get("metric") in METRICS_BY_KEY:
        return build_series(METRICS_BY_KEY[plan.series["metric"]], plan.series["grain"], runs,
                            period=(plan.selection or {}).get("query_params"))
    keys = [k for k in (plan.metrics or ([plan.metric] if plan.metric else [])) if k in METRICS_BY_KEY]
    if len(keys) > 1:
        metrics = [METRICS_BY_KEY[k] for k in keys]
        periods = {m.key: next((s.get("query_params") for s in plan.all_selections()
                                if s.get("key") == m.fetch_key), None) for m in metrics}
        return build_multi_comparison(metrics, runs, question, periods)
    if keys:
        metric = METRICS_BY_KEY[keys[0]]
        period = next((s.get("query_params") for s in plan.all_selections() if s.get("key") == metric.fetch_key),
                      (plan.selection or {}).get("query_params"))
        return build_comparison(metric, runs, question, period=period)
    return None


def _list_title(plan: Any) -> str:
    """The filters of a planned document list in words, e.g. "Unpaid invoices, dated more than 180 days ago"."""
    if plan is None or getattr(plan, "source", "") != "document_list":
        return ""
    from gemini_brain.orchestrator.multi_org_lists import describe

    text = describe((plan.selection or {}).get("query_params") or {})
    return text[:1].upper() + text[1:]


def _unsupported_answer(plan: Any, runs: List["OrgRun"], t0: float) -> Dict[str, Any]:
    """The question asks only for figures no report can compute: say which, and what is available."""
    answer = "\n".join(f"- {line}" for line in plan.notes)
    final = normalize_envelope({
        "answer": answer,
        "status": "ok",
        "results": [],
        "blocks": [],
        "request_id": new_request_id(),
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0, "cost_usd": 0.0,
                        "elapsed_seconds": round(time.time() - t0, 2)},
        "agent_trace": [plan.trace_step()],
        "routing_info": {"type": plan.intent, "type_label": "Data query", "reason": plan.reason,
                         "path": "multi_org", "layout": "unsupported"},
        "organizations": [r.public() for r in runs],
    })
    return final


def _add_usage(final: Dict[str, Any], in_tokens: int, out_tokens: int, calls: int) -> None:
    """Fold the tokens of a call made outside the per-org runs into the envelope."""
    if not (in_tokens or out_tokens or calls):
        return
    usage = final["token_usage"]
    usage["input_tokens"] += int(in_tokens or 0)
    usage["output_tokens"] += int(out_tokens or 0)
    usage["llm_calls"] += int(calls or 0)
    usage["cost_usd"] = round(
        usage["cost_usd"] + gemini_brain_cost(0, 0, int(in_tokens or 0), int(out_tokens or 0), HAIKU45_ID), 6
    )


def _answer_once(
    question: str,
    org_id: int,
    runner: Any,
    kwargs: Dict[str, Any],
    plan: Any,
) -> Dict[str, Any]:
    """A question that needs no data (how-to, FAQ): one pipeline run, not one per org."""
    result = runner.run(
        query=question, organization_id=org_id, session_id=None,
        org_scoped_rest_only=True, **kwargs,
    )
    final = normalize_envelope(dict(result or {}))
    routing = dict(final.get("routing_info") or {"type": plan.intent, "type_label": "General", "reason": ""})
    routing["path"] = "multi_org_direct"
    final["routing_info"] = routing
    final.setdefault("token_usage", {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0, "cost_usd": 0.0})
    final.setdefault("agent_trace", [])
    final["agent_trace"].insert(0, plan.trace_step())
    _add_usage(final, plan.input_tokens, plan.output_tokens, plan.llm_calls)
    return final


def run_multi_org(
    query: str,
    org_ids: List[int],
    org_meta: Dict[int, Dict[str, Any]],
    *,
    runner_factory: Callable[[], Any],
    run_kwargs: Dict[str, Any],
    planner: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Blocking form of run_multi_org_stream: only the merged result."""
    final: Dict[str, Any] = {}
    for chunk in run_multi_org_stream(
        query, org_ids, org_meta, runner_factory=runner_factory, run_kwargs=run_kwargs,
        planner=planner,
    ):
        if "final_result" in chunk:
            final = chunk["final_result"]
    return final


def _tagged_rows(run: OrgRun) -> List[Dict[str, Any]]:
    """The org's result rows, each labelled with the organization it came from.

    The label is applied last so a row can never claim another organization.
    It uses its own keys so a row's own fields (a per-invoice currency, say)
    are left untouched.
    """
    tag = {
        "organization": run.name,
        "organization_id": run.org_id,
        "organization_currency": run.currency,
    }
    rows = []
    for row in (run.result or {}).get("results") or []:
        rows.append({**row, **tag} if isinstance(row, dict) else {**tag, "value": row})
    return rows


def _org_prompt_section(run: OrgRun) -> str:
    head = f"## {run.name} (currency: {run.currency or 'unknown'}, status: {run.status})"
    if not run.answered:
        return f"{head}\nNo data could be retrieved for this organization."
    if run.status == "empty":
        # An empty source was being written up as "0 AED" for every org.
        return (f"{head}\nNo records were found for this organization. Say that no records were "
                "found; never state 0 or any other figure for it.")
    result = run.result or {}
    answer = (result.get("answer") or "")[:_MAX_ANSWER_CHARS_PER_ORG]
    rows = (result.get("results") or [])[:_MAX_ROWS_PER_ORG]
    data = json.dumps(rows, default=str)[:_MAX_DATA_CHARS_PER_ORG]
    # A planned fetch carries data only; a full per-org run also has its answer.
    answer_line = f"\nAnswer: {answer}" if answer else ""
    return f"{head}{answer_line}\nData: {data}"


def currency_note(runs: List[OrgRun]) -> str:
    """One computed statement about currencies, so the model never has to infer it.

    Left to itself the model applied the "different currencies" rule to
    organizations that all report in AED.
    """
    known = {r.currency for r in runs if r.currency}
    unknown = [r.name for r in runs if not r.currency]
    if unknown:
        return (
            "Currency note: the currency is unknown for " + ", ".join(unknown)
            + ". Do not add or rank their amounts with any other organization's."
        )
    if len(known) == 1:
        (only,) = known
        return (
            f"Currency note: all organizations report in {only}. "
            "Their amounts are in the same unit and may be compared or added."
        )
    listing = ", ".join(f"{r.name} ({r.currency})" for r in runs)
    return f"Currency note: currencies differ: {listing}. Do not add or rank amounts across currencies."


#: A line claiming an organization has nothing to show.
_NO_DATA_CLAIM = re.compile(
    r"\bno\s+(data|information|figures?|records?)\b|\bnot\s+(available|provided|reported)\b"
    r"|\bcould\s+not\s+be\s+(retrieved|determined|included|found)\b|\bmissing\b|\bunknown\b",
    re.IGNORECASE,
)


def _drop_false_gaps(answer: str, runs: List[OrgRun]) -> str:
    """Remove lines that say an organization has no data when it has.

    The model regularly listed organizations shown in the table as "no data".
    coverage_note restates the real gaps from the statuses afterwards.
    """
    # "(?!\w)": the name "..._Org1" must not match inside "..._Org10".
    with_data = [re.compile(re.escape(r.name) + r"(?!\w)") for r in runs if r.answered and r.status != "empty"]
    kept = [
        line for line in answer.splitlines()
        if not (_NO_DATA_CLAIM.search(line) and any(name.search(line) for name in with_data))
    ]
    return "\n".join(kept).strip()


def coverage_note(runs: List[OrgRun]) -> str:
    """Which organizations had no records or failed, stated from their statuses."""
    empty = [r.name for r in runs if r.status == "empty"]
    failed = [r.name for r in runs if not r.answered]
    parts = []
    if empty:
        parts.append("No records found: " + ", ".join(empty) + ".")
    if failed:
        parts.append("Could not be retrieved: " + ", ".join(failed) + ".")
    return ("\n\n" + " ".join(f"_{p}_" for p in parts)) if parts else ""


def _fallback_answer(runs: List[OrgRun]) -> str:
    """Per-org answers under headings, for when the comparison call fails."""
    parts = []
    for run in runs:
        if not run.answered:
            body = "No data could be retrieved."
        elif run.status == "empty":
            body = "No records found."
        else:
            body = (run.result or {}).get("answer") or "Data retrieved; see the table below."
        parts.append(f"### {run.name}\n{body}")
    return "\n\n".join(parts)


def merge_org_results(
    query: str,
    runs: List[OrgRun],
    compare_runner: Any,
    *,
    t0: Optional[float] = None,
    comparison: Optional[Dict[str, Any]] = None,
    notes: Optional[List[str]] = None,
    title: str = "",
) -> Dict[str, Any]:
    """Combine per-org results into one envelope with a single comparison answer.

    With a `comparison` (one metric per org, computed in code), that table is
    the answer's data: it is shown first, the summary call reads only it, and
    the per-org blocks, which would repeat the same figures, are left out.
    """
    t0 = t0 if t0 is not None else time.time()
    answered = [r for r in runs if r.answered]
    missing = [r for r in runs if not r.answered]

    in_tokens = out_tokens = llm_calls = 0
    cost = 0.0
    for r in runs:
        usage = (r.result or {}).get("token_usage") or {}
        in_tokens += int(usage.get("input_tokens") or 0)
        out_tokens += int(usage.get("output_tokens") or 0)
        llm_calls += int(usage.get("llm_calls") or 0)
        cost += float(usage.get("cost_usd") or 0.0)

    results: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    if comparison is not None:
        computed_blocks, _prompt, results = computed_view(comparison)
        blocks.extend(computed_blocks)
    presentation = None
    if comparison is None:
        for r in runs:
            if r.answered:
                results.extend(_tagged_rows(r))
        # List questions get rows reduced in code; anything else keeps each
        # org's own blocks, but closed by default so the answer stays short.
        if is_list_question(query):
            presentation = build_presentation(query, runs)
            blocks.extend(presentation["blocks"])
        else:
            blocks.append(collapsed_org_sections(runs))

    model_written = False
    if not answered:
        answer = "I could not retrieve figures for any of the selected organizations."
        status = "failed"
    elif comparison is not None:
        # Written from the computed figures alone: no model call, so the answer
        # cannot contradict the table or name figures and orgs that are not in it.
        answer = computed_answer(comparison)
        status = "partial" if missing else "ok"
    elif presentation is not None and presentation.get("answer"):
        # Lists counted and sorted by a report, and shared vendors/customers:
        # the answer is written from the reduced rows too. The model used to
        # invent round amounts for shared vendors and repeat its own bullets.
        answer = (f"**{title}**\n\n" if title else "") + presentation["answer"]
        status = "partial" if missing else "ok"
    else:
        if presentation is not None:
            user_text = (
                f"Question: {query}\n\n{currency_note(runs)}\n\n{presentation['prompt']}\n\n"
                "Tables with these rows are shown to the user below your answer; do not copy them "
                "row by row. Answer the question directly in up to 8 bullet points, naming the "
                "specific items and amounts that answer it for each organization."
            )
        else:
            user_text = (
                f"Question: {query}\n\n{currency_note(runs)}\n\n"
                + "\n\n".join(_org_prompt_section(r) for r in runs)
                + "\n\nEach organization's tables are available to the user below your answer. "
                "Do not reproduce them. Answer the question directly and briefly: at most "
                "6 bullet points or one compact comparison table."
            )
        try:
            answer, ci, co = compare_runner._call_llm(
                COMPARE_SYSTEM_PROMPT, user_text, max_tokens=COMPARE_MAX_TOKENS, purpose="multi_org_compare",
            )
            in_tokens += int(ci or 0)
            out_tokens += int(co or 0)
            llm_calls += 1
            cost += gemini_brain_cost(0, 0, int(ci or 0), int(co or 0), HAIKU45_ID)
        except Exception as e:
            logger.warning("multi_org: comparison call failed, returning per-org answers: %s", e)
            answer = ""
        answer = _drop_false_gaps(answer or "", runs)
        answer = (answer + coverage_note(runs)) if answer else _fallback_answer(runs)
        status = "partial" if missing else "ok"
        model_written = True
    if notes and answered:
        # A figure in the question that cannot be computed, stated after the ones that could.
        answer += "\n\n" + "\n".join(f"- {line}" for line in notes)

    verification = None
    if answered:
        if model_written:
            # Each figure must come from the organization its sentence names;
            # what cannot be traced is flagged under the answer, not only in a chip.
            report = verify_attributed(answer, {r.name: r.result for r in answered})
            answer += unverified_note(report)
        else:
            # Written in code from the data: the computed comparison carries the
            # totals and gaps the answer quotes.
            report = verify_answer(answer, [results, comparison] if comparison is not None else results)
            report.method = "computed"
        verification = report.to_public()

    notice = None
    if missing:
        notice = notice_for("PARTIAL_ORGS", subject=", ".join(r.name for r in missing))

    first = next((r.result for r in answered), None) or {}
    routing = dict(first.get("routing_info") or {"type": 4, "type_label": "Data query", "reason": ""})
    routing["path"] = "multi_org"
    # How the answer was laid out: one metric table, a reduced list, or collapsed org sections.
    routing["layout"] = (comparison.get("kind", "metric") if comparison is not None
                         else presentation["shape"] if presentation is not None else "collapsed")

    envelope: Dict[str, Any] = {
        "answer": answer,
        "sql": None,
        "results": results,
        "error": None if answered else "multi_org_all_failed",
        "status": status,
        "notice": notice,
        "data_source": None,
        "table_markdown": None,
        "blocks": blocks,
        "request_id": new_request_id(),
        "pii_redacted": any((r.result or {}).get("pii_redacted") for r in runs),
        "token_usage": {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "llm_calls": llm_calls,
            "cost_usd": round(cost, 6),
            "elapsed_seconds": round(time.time() - t0, 2),
        },
        "agent_trace": [
            {"step": "multi_org_fan_out", "organizations": [r.public() for r in runs]},
            *[
                {
                    "step": "org_result",
                    "organization_id": r.org_id,
                    "status": r.status,
                    "path": ((r.result or {}).get("routing_info") or {}).get("path"),
                }
                for r in runs
            ],
        ],
        "routing_info": routing,
        "organizations": [r.public() for r in runs],
    }
    if comparison is not None:
        envelope["comparison"] = comparison
    if first.get("policy"):
        envelope["policy"] = first["policy"]
    if verification is not None:
        envelope["verification"] = verification
    return normalize_envelope(envelope)
