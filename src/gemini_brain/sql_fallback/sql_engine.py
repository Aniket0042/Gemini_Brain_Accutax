"""
sql_engine.py — SQL Fallback Engine.

Extracted from engine.py.
Drives the production NL-to-SQL tool-calling loop using BedrockAdapter.
Imports coordinator_agent and sub-agents from the host production codebase.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

from gemini_brain.config.constants import (
    ENGINE_MAX_ITERATIONS,
    ENGINE_TIME_BUDGET_SECONDS,
    NEVER_EXPOSE_BACKEND_RULE,
)
from gemini_brain.reasoning.bedrock_client import extract_text, extract_tool_calls
from gemini_brain.sql_fallback.answer_cleaner import (
    clean_thinking_artifacts,
    is_garbage_answer,
)
from gemini_brain.sql_fallback.cost_optimizer import (
    compact_tool_result,
    select_tools,
)
from gemini_brain.sql_fallback.fast_path import try_fast_path
from gemini_brain.sql_fallback.sql_safety import assert_read_only

logger = logging.getLogger("gemini_brain.sql_fallback.sql_engine")


def _get_coordinator_pipeline() -> Tuple[Any, Any, Any, Any, Any, Any, Any]:
    """Helper to lazily import production coordinator pipeline components."""
    # Ensure original monolith directory is on sys.path if running outside host workspace
    host_path = r"C:\Users\acer\Desktop\query-parser-bedrock_clean\query-parser-bedrock_clean"
    if host_path not in sys.path and os.path.exists(host_path):
        sys.path.insert(0, host_path)

    try:
        from agents.coordinator_agent import (
            _build_system_prompt,
            TOOL_DEFINITIONS,
            AGENT_HANDLERS,
            _deep_serialize,
            _strip_sql_from_answer,
            _format_raw_results,
            _infer_question_type,
        )
        return (
            _build_system_prompt,
            TOOL_DEFINITIONS,
            AGENT_HANDLERS,
            _deep_serialize,
            _strip_sql_from_answer,
            _format_raw_results,
            _infer_question_type,
        )
    except ImportError as e:
        logger.error("Failed to import production agents pipeline: %s", e)
        raise RuntimeError(
            "Production coordinator_agent pipeline is required for SQL fallback engine."
        ) from e


TENANT_TABLES = {
    "contacts", "income", "expense", "items", "bank_accounts",
    "chart_of_accounts", "inventory_adjustments", "delivery_notes",
    "customer_payment", "supplier_payments", "tax_adjustments",
    "projects", "warehouses", "organizations"
}


def enforce_tenant_isolation_sql(sql: str, org_id: int) -> str:
    """Enforces strict tenant isolation on any SQL query string by rewriting organization filters."""
    if not sql or not isinstance(sql, str):
        return sql

    cleaned_sql = sql.strip()

    # 1. Rewrite explicit organization_id comparisons:
    # organization_id = <digits> -> organization_id = <org_id>
    # c.organization_id = <digits> -> c.organization_id = <org_id>
    # "organization_id" = <digits> -> "organization_id" = <org_id>
    cleaned_sql = re.sub(
        r'(\b(?:\w+\.)?(?:"?organization_id"?|"?org_id"?)\s*=\s*)\d+',
        rf'\g<1>{org_id}',
        cleaned_sql,
        flags=re.IGNORECASE,
    )

    # 2. Rewrite explicit organization_id IN clauses:
    cleaned_sql = re.sub(
        r'(\b(?:\w+\.)?(?:"?organization_id"?|"?org_id"?)\s+IN\s*\()[^)]+(\))',
        rf'\g<1>{org_id}\g<2>',
        cleaned_sql,
        flags=re.IGNORECASE,
    )

    # 3. Rewrite organizations table `id = <digits>` or `o.id = <digits>`:
    cleaned_sql = re.sub(
        r'(\b(?:organizations|org|o)\.id\s*=\s*)\d+',
        rf'\g<1>{org_id}',
        cleaned_sql,
        flags=re.IGNORECASE,
    )

    # Standalone `WHERE id = <digits>` when querying `FROM organizations`
    if re.search(r'\bFROM\s+organizations\b', cleaned_sql, re.IGNORECASE) and not re.search(r'\b(?:organizations|org|o)\.id\b', cleaned_sql, re.IGNORECASE):
        cleaned_sql = re.sub(
            r'(\bWHERE\s+id\s*=\s*)\d+',
            rf'\g<1>{org_id}',
            cleaned_sql,
            flags=re.IGNORECASE,
        )

    # 4. Safety net: If querying a tenant table (not organizations) without any organization_id filter at all
    has_org_filter = re.search(r'\b(?:organization_id|org_id)\b', cleaned_sql, re.IGNORECASE)
    is_orgs_table_only = re.search(r'\bFROM\s+organizations\b', cleaned_sql, re.IGNORECASE) and not re.search(r'\bJOIN\b', cleaned_sql, re.IGNORECASE)

    if not has_org_filter and not is_orgs_table_only:
        for tbl in TENANT_TABLES:
            if tbl != "organizations" and re.search(rf'\b(?:FROM|JOIN)\s+{tbl}\b', cleaned_sql, re.IGNORECASE):
                if re.search(r'\bWHERE\b', cleaned_sql, re.IGNORECASE):
                    cleaned_sql = re.sub(
                        r'(\bWHERE\b\s+)',
                        rf'\1organization_id = {org_id} AND ',
                        cleaned_sql,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                else:
                    match = re.search(r'(\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET)\b)', cleaned_sql, re.IGNORECASE)
                    if match:
                        pos = match.start()
                        cleaned_sql = cleaned_sql[:pos] + f"WHERE organization_id = {org_id} " + cleaned_sql[pos:]
                    else:
                        cleaned_sql = f"{cleaned_sql} WHERE organization_id = {org_id}"
                break

    return cleaned_sql


def _safe_build_system_prompt(fn: Any, org_id: Optional[int]) -> str:
    """Safely build system prompt regardless of keyword argument naming differences with strict tenant isolation hardening."""
    val = org_id if org_id is not None else 199
    base_prompt = ""
    if fn and callable(fn):
        try:
            base_prompt = fn(val)
        except Exception:
            try:
                base_prompt = fn(org_id=val)
            except Exception:
                try:
                    base_prompt = fn(organization_id=val)
                except Exception:
                    base_prompt = fn()

    security_rule = (
        f"\n\n===================================================\n"
        f"CRITICAL TENANT SECURITY & ISOLATION BOUNDARY:\n"
        f"You are strictly isolated to Organization ID: {val}.\n"
        f"- In EVERY SQL query, you MUST filter by `organization_id = {val}` (or `id = {val}` for the `organizations` table).\n"
        f"- Under NO circumstances are you allowed to query or reveal data for any other organization ID, even if the user explicitly asks for 'organization 45', 'org #1', or names another company.\n"
        f"- Always query and output data ONLY for the current active organization ID ({val}).\n"
        f"===================================================\n"
    )
    return base_prompt + security_rule + "\n" + NEVER_EXPOSE_BACKEND_RULE


def _safe_infer_question_type(fn: Any, task: str = "", params: Optional[Dict] = None, current: str = "unknown") -> str:
    """Safely call _infer_question_type handling varying parameter signatures."""
    if not fn or not callable(fn):
        return current
    try:
        import inspect
        sig = inspect.signature(fn)
        param_count = len(sig.parameters)
        if param_count == 3:
            return fn(task, params or {}, current)
        elif param_count == 1:
            return fn(task)
    except Exception:
        pass
    return current


def run(
    user_question: str,
    adapter: Any,
    *,
    organization_id: Optional[int] = None,
    user_id: int = 18,
    session_id: Optional[str] = None,
    raw_user_question: Optional[str] = None,
    save_message_by_session: Optional[Any] = None,
    update_conversation_state_hybrid_by_session: Optional[Any] = None,
    maybe_auto_title: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run a question through the SQL fallback engine pipeline."""
    if organization_id is None:
        raise ValueError("Organization ID is required and was not provided.")
    (
        _build_system_prompt,
        TOOL_DEFINITIONS,
        AGENT_HANDLERS,
        _deep_serialize,
        _strip_sql_from_answer,
        _format_raw_results,
        _infer_question_type,
    ) = _get_coordinator_pipeline()

    adapter.reset_tokens()
    start_time = time.time()
    saved_query = raw_user_question or user_question

    # Fast-path check
    fast_result = try_fast_path(user_question, organization_id, AGENT_HANDLERS)
    if fast_result is not None:
        fp_agent_result, fp_task, _fp_params = fast_result
        if isinstance(fp_agent_result, list):
            fp_agent_result = {"results": fp_agent_result}
        elif not isinstance(fp_agent_result, dict):
            fp_agent_result = {"results": [], "raw": str(fp_agent_result)}
        fp_compact = compact_tool_result(fp_agent_result, fp_task)
        try:
            fp_resp = adapter.converse_with_tools(
                system_prompt=(
                    "You are a professional financial analyst for a UAE technology company. "
                    "Convert the following database result into a complete, well-structured answer. "
                    "Use AED X,XXX.XX currency format. "
                    "Use ## headers for sections, markdown tables for ranked lists, bullet points for facts. "
                    "Show ALL groups/buckets/categories individually — never collapse into just a total. "
                    "Give a direct decisive answer — do NOT append a Data Source section."
                ),
                messages=[{"role": "user", "content": [{"text": f"Question: {user_question}\n\nData:\n{fp_compact}"}]}],
                tools=[],
                temperature=0.0,
                max_tokens=2000,
            )
            fp_answer = extract_text(fp_resp)
        except Exception:
            fp_answer = None

        final_answer = (
            _strip_sql_from_answer(fp_answer)
            if fp_answer
            else _format_raw_results(user_question, fp_agent_result.get("results", []))
        )
        token_usage = adapter.get_token_usage()
        token_usage["elapsed_seconds"] = round(time.time() - start_time, 2)
        question_type = _safe_infer_question_type(_infer_question_type, fp_task, _fp_params, "unknown")

        last_results = fp_agent_result.get("results") or []
        last_sql = fp_agent_result.get("sql", None)
        agent_trace = [{"agent": "fast_path", "task": fp_task, "success": True}]

        if session_id and save_message_by_session:
            save_message_by_session(session_id, "user", saved_query)
            save_message_by_session(session_id, "assistant", final_answer or "No answer generated.")
            if update_conversation_state_hybrid_by_session:
                update_conversation_state_hybrid_by_session(
                    session_id=session_id,
                    user_id=user_id,
                    query=user_question,
                    response=final_answer or "No answer generated.",
                    agent_trace=agent_trace,
                )
            if maybe_auto_title:
                maybe_auto_title(session_id, user_question)

        return {
            "query": user_question,
            "answer": final_answer or "No answer generated.",
            "question_type": question_type,
            "sql": last_sql,
            "results": last_results or [],
            "agent_trace": agent_trace,
            "token_usage": token_usage,
            "total_count": len(last_results) if isinstance(last_results, list) else None,
            "error": None,
        }

    # Standard tool loop
    system_prompt = _safe_build_system_prompt(_build_system_prompt, organization_id)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": [{"text": user_question}]}]
    agent_trace: List[Dict[str, Any]] = []

    last_sql: Optional[str] = None
    last_results: Optional[List[Dict[str, Any]]] = None
    final_answer: str = ""
    iteration = 0
    question_type = "unknown"

    active_tools = select_tools("COMPLEX", user_question, TOOL_DEFINITIONS)

    while iteration < ENGINE_MAX_ITERATIONS:
        iteration += 1
        elapsed = time.time() - start_time
        if elapsed > ENGINE_TIME_BUDGET_SECONDS:
            logger.warning("Engine time budget exceeded (%.1fs)", elapsed)
            break

        try:
            response = adapter.converse_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=active_tools,
                temperature=0.0,
                max_tokens=2000,
            )
        except Exception as e:
            logger.error("LLM call failed at iteration %d: %s", iteration, e)
            token_usage = adapter.get_token_usage()
            token_usage["elapsed_seconds"] = round(time.time() - start_time, 2)
            return {
                "query": user_question,
                "answer": f"Error: LLM call failed — {e}",
                "question_type": "error",
                "sql": last_sql,
                "results": last_results or [],
                "agent_trace": agent_trace,
                "token_usage": token_usage,
                "total_count": None,
                "error": str(e),
            }

        stop_reason = response.get("stopReason", "end_turn")
        tool_calls = extract_tool_calls(response)
        text_output = extract_text(response)

        if text_output:
            final_answer = text_output

        if stop_reason == "end_turn" or not tool_calls:
            break

        assistant_content = response.get("output", {}).get("message", {}).get("content", [])
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results_content: List[Dict[str, Any]] = []
        for tc in tool_calls:
            tool_use_id = tc["toolUseId"]
            tool_name = tc["name"]
            tool_input = tc.get("input", {})

            handler = AGENT_HANDLERS.get(tool_name)
            if not handler:
                agent_result = {"success": False, "error": f"Unknown tool '{tool_name}'"}
            else:
                task = tool_input.get("task", "")
                params = tool_input.get("params", {})

                if not isinstance(params, dict):
                    params = {}
                params["organization_id"] = organization_id

                if tool_name == "finance_agent" and task == "execute_sql":
                    if not params.get("sql"):
                        fallback_sql = _generate_sql_fallback(adapter, user_question, system_prompt, organization_id)
                        if fallback_sql:
                            params["sql"] = fallback_sql
                    if params.get("sql"):
                        # Phase F safety gap 1: Belt-and-suspenders read-only check
                        assert_read_only(params["sql"])
                        params["sql"] = enforce_tenant_isolation_sql(params["sql"], organization_id)

                try:
                    agent_result = handler(task, params)
                except Exception as e:
                    agent_result = {"success": False, "error": str(e)}

            if isinstance(agent_result, dict):
                if agent_result.get("sql"):
                    last_sql = agent_result["sql"]
                if agent_result.get("results") is not None:
                    last_results = agent_result["results"]

            trace_entry = {
                "iteration": iteration,
                "agent": tool_name,
                "task": tool_input.get("task"),
                "params": tool_input.get("params"),
                "success": agent_result.get("success", False) if isinstance(agent_result, dict) else False,
            }
            if isinstance(agent_result, dict) and agent_result.get("error"):
                trace_entry["error"] = agent_result["error"]
            agent_trace.append(trace_entry)

            compact_str = compact_tool_result(agent_result, tool_input.get("task", "")) if isinstance(agent_result, dict) else str(agent_result)[:2000]
            tool_results_content.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"text": compact_str}],
                }
            })

        messages.append({"role": "user", "content": tool_results_content})

    token_usage = adapter.get_token_usage()
    token_usage["elapsed_seconds"] = round(time.time() - start_time, 2)
    question_type = _safe_infer_question_type(_infer_question_type, user_question, {}, "unknown")

    if final_answer:
        final_answer = _strip_sql_from_answer(final_answer)
        final_answer = re.sub(r'<function_quality_\w+>.*?</function_quality_\w+>\s*', '', final_answer, flags=re.DOTALL).strip()
        final_answer = clean_thinking_artifacts(final_answer)

    if last_results and isinstance(last_results, list) and len(last_results) > 0:
        row_count = len(last_results)
        min_len = 500 if row_count >= 20 else 300 if row_count >= 5 else 60
        ans_too_short = bool(final_answer) and row_count >= 5 and len(final_answer) < min_len
        if not final_answer or len(final_answer) < 30 or is_garbage_answer(final_answer) or ans_too_short:
            final_answer = _force_answer(adapter, user_question, system_prompt, messages, last_results, _strip_sql_from_answer, _format_raw_results)
            if not final_answer or is_garbage_answer(final_answer):
                final_answer = _format_raw_results(user_question, last_results)
    elif is_garbage_answer(final_answer):
        final_answer = _graceful_no_data_answer(adapter, user_question, system_prompt, agent_trace)

    if session_id and save_message_by_session:
        save_message_by_session(session_id, "user", saved_query)
        save_message_by_session(session_id, "assistant", final_answer or "No answer generated.")
        if update_conversation_state_hybrid_by_session:
            update_conversation_state_hybrid_by_session(
                session_id=session_id,
                user_id=user_id,
                query=user_question,
                response=final_answer or "No answer generated.",
                agent_trace=agent_trace,
            )
        if maybe_auto_title:
            maybe_auto_title(session_id, user_question)

    return {
        "query": user_question,
        "answer": final_answer or "No answer generated.",
        "question_type": question_type,
        "sql": last_sql,
        "results": last_results or [],
        "agent_trace": agent_trace,
        "token_usage": token_usage,
        "total_count": len(last_results) if isinstance(last_results, list) and last_results else None,
        "error": None,
    }


def _generate_sql_fallback(adapter: Any, question: str, system_prompt: str, org_id: int) -> str:
    sql_prompt = (
        "Generate a single PostgreSQL SELECT query to answer the following question. "
        f"Filter by organization_id = {org_id}. "
        "Return ONLY the SQL, no explanation, no markdown fences. LIMIT 100."
    )
    try:
        result = adapter.converse(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": [{"text": f"{sql_prompt}\n\nQuestion: {question}"}]}],
            temperature=0.0,
            max_tokens=1500,
        )
        sql = result.strip()
        if sql.startswith("```"):
            sql = sql.split("\n", 1)[-1]
        if sql.endswith("```"):
            sql = sql.rsplit("```", 1)[0]
        sql = sql.strip().rstrip(";")
        if sql.lower().startswith("sql\n"):
            sql = sql[4:].strip()
        if sql.upper().startswith("SELECT"):
            return enforce_tenant_isolation_sql(sql, org_id)
    except Exception as e:
        logger.warning("Fallback SQL generation failed: %s", e)
    return ""


def _force_answer(adapter: Any, question: str, system_prompt: str, messages: List[Dict[str, Any]], last_results: List[Dict[str, Any]], strip_sql_fn: Any, format_raw_fn: Any) -> str:
    if last_results and isinstance(last_results, list) and len(last_results) > 0:
        n = len(last_results)
        try:
            data_preview = json.dumps(last_results[:25], default=str)[:4000]
            note = f" There are {n} total records — list the first 10 by name/reference and give a summary count." if n > 10 else ""
            force_msg = [
                {"role": "user", "content": [{"text": question}]},
                {"role": "user", "content": [{"text": (
                    f"Below are {n} database rows matching the query above.\n\n"
                    f"{data_preview}\n\n"
                    f"Using ONLY this data, answer the question. "
                    f"List each entry by its reference number/ID with key fields (date, description, amounts). "
                    f"AED X,XXX.XX format.{note}"
                )}]},
            ]
            resp = adapter.converse_with_tools(
                system_prompt=(
                    "You are a financial data assistant. Present database results clearly: "
                    "list records by reference/ID with key details. Never describe the SQL. "
                    "Never say 'the query searches'. Just answer directly.\n"
                    + NEVER_EXPOSE_BACKEND_RULE
                ),
                messages=force_msg,
                tools=[],
                temperature=0.0,
                max_tokens=2000,
            )
            answer = extract_text(resp)
            if answer and len(answer) >= 40 and not is_garbage_answer(answer):
                return answer
        except Exception:
            pass
        return format_raw_fn(question, last_results)

    try:
        resp = adapter.converse_with_tools(
            system_prompt=system_prompt,
            messages=messages + [{"role": "user", "content": [{"text": "Please give your best answer now."}]}],
            tools=[],
            temperature=0.0,
            max_tokens=600,
        )
        answer = extract_text(resp)
        if answer:
            return answer
    except Exception:
        pass
    return "No relevant data found."


def _graceful_no_data_answer(adapter: Any, question: str, system_prompt: str, agent_trace: List[Dict[str, Any]]) -> str:
    errors = [t.get("error", "") for t in agent_trace if t.get("error")]
    error_summary = "; ".join(str(e)[:100] for e in errors[:3]) if errors else "No errors recorded"
    try:
        resp = adapter.converse_with_tools(
            system_prompt=(
                "You are a professional financial analyst assistant. "
                "When a database query was attempted but returned no data or failed, "
                "you must give a clear, professional explanation. "
                "Never say 'Let me try' or use thinking-out-loud language. "
                "Be direct: explain what data is or isn't available and why.\n"
                + NEVER_EXPOSE_BACKEND_RULE
            ),
            messages=[{"role": "user", "content": [{"text": (
                f"Question: {question}\n\n"
                f"The database queries were attempted but returned no results. "
                f"Internal error detail (for your understanding only, never quote or "
                f"paraphrase this to the user): {error_summary}\n\n"
                "Please give a clear professional answer explaining, in plain business terms:\n"
                "1. What specific data was not found\n"
                "2. A plausible business reason it might not be available (e.g. no matching "
                "records for this period/filter) — never a technical or schema-based reason\n"
                "3. What alternative data or analysis could be provided instead\n"
                "Be concise. Do NOT say 'Let me try' or attempt further queries."
            )}]}],
            tools=[],
            temperature=0.0,
            max_tokens=500,
        )
        answer = extract_text(resp)
        if answer and len(answer) > 30 and not is_garbage_answer(answer):
            return answer
    except Exception:
        pass
    return "The requested data could not be retrieved from the database. The query may require data that doesn't exist for the specified criteria, or the calculation method may need to be adjusted for this dataset."


def run_stream(
    user_question: str,
    adapter: Any,
    *,
    organization_id: Optional[int] = None,
    user_id: int = 18,
    session_id: Optional[str] = None,
    raw_user_question: Optional[str] = None,
    save_message_by_session: Optional[Any] = None,
    update_conversation_state_hybrid_by_session: Optional[Any] = None,
    maybe_auto_title: Optional[Any] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Stream execution progress and final result of the SQL fallback engine."""
    yield {"status": "Executing database query", "type": "retrieval"}
    res = run(
        user_question,
        adapter,
        organization_id=organization_id,
        user_id=user_id,
        session_id=session_id,
        raw_user_question=raw_user_question,
        save_message_by_session=save_message_by_session,
        update_conversation_state_hybrid_by_session=update_conversation_state_hybrid_by_session,
        maybe_auto_title=maybe_auto_title,
    )
    yield {"final_result": res}

