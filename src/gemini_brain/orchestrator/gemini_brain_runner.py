"""
gemini_brain_runner.py — Main orchestration runner for the Gemini Brain subsystem.

Decomposed from gemini_brain_adapter.py (lines 146-966).
Orchestrates:
  1. Intent classification via Gemini 2.5 Flash (7 types)
  2. LEFT PATH: Direct answer via Gemini Flash for types 1, 2, 6, 7
  3. RIGHT PATH: API endpoint selection via Gemini Flash for types 3, 4, 5
  4. Live REST API call against Accutax backend
  5. Complexity judging via Gemini Flash (SIMPLE, MEDIUM, COMPLEX)
  6. Claude reasoning over live API data using AWS Bedrock
  7. SQL fallback engine execution when API endpoints are missing or fail
  8. Session state persistence and titling
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Generator, Optional, Tuple

from gemini_brain.api_client.accutax_client import call_api, extract_data
from gemini_brain.classification.intent_classifier import classify_intent
from gemini_brain.config.constants import (
    ENDPOINT_DESCRIPTIONS,
    GEMINI_MODEL,
    HAIKU45_ID,
    LEFT_PATH_TYPES,
    RIGHT_PATH_TYPES,
    TYPE_LABELS,
)
from gemini_brain.config.pricing import gemini_brain_cost
from gemini_brain.config.settings import settings
from gemini_brain.endpoints.endpoint_selector import select_endpoint
from gemini_brain.memory.session_memory import (
    get_project_context_by_session,
    is_valid_uuid,
    maybe_auto_title,
    save_message_by_session,
)
from gemini_brain.memory.state_extractor import (
    update_conversation_state_hybrid_by_session,
)
from gemini_brain.pii.redactor import redact_pii
from gemini_brain.reasoning.bedrock_client import BedrockAdapter
from gemini_brain.reasoning.claude_reasoner import reason_over_data
from gemini_brain.reasoning.complexity_judge import judge_complexity
from gemini_brain.sql_fallback import db_connection, sql_engine
from gemini_brain.tenant.org_resolver import resolve_organization
from gemini_brain.utils.json_parser import extract_json

logger = logging.getLogger("gemini_brain.orchestrator.runner")

# ── Verbatim Direct Answer Prompt ────────────────────────────────────────────
DIRECT_ANSWER_SYSTEM_PROMPT: str = """You are a knowledgeable assistant for Accutax, a cloud-based accounting application used in the Middle East (UAE, AED currency, 5% VAT).
Help with how-to questions, UI guidance, accounting concepts, and strategic advice.
Be concise and practical. Use numbered steps for procedures. Do not make up specific database numbers."""


class GeminiBrainRunner:
    """GeminiBrain runner: Gemini orchestrates, API is source of truth, Anthropic reasons."""

    def __init__(
        self,
        api_key: str = "",
        adapter_resolver: Optional[Callable[[str], Any]] = None,
    ):
        self.api_key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self._client: Optional[Any] = None
        self.adapter_resolver = adapter_resolver

    def _get_client(self) -> Any:
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is required and was not provided in settings, parameters, or environment."
            )
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _call_gemini(
        self, system: str, user_text: str, max_tokens: int = 2000
    ) -> Tuple[str, int, int]:
        """Call Google Gemini 2.5 Flash and track prompt/candidate token usage."""
        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.0,
                max_output_tokens=max_tokens,
            ),
        )
        text = response.text or ""
        usage = getattr(response, "usage_metadata", None)
        inp = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        out = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        return text, inp, out

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
    def _cost(
        g_in: int, g_out: int, b_in: int, b_out: int, model_id: str
    ) -> float:
        return gemini_brain_cost(g_in, g_out, b_in, b_out, model_id)

    def _err(self, msg: str, t0: float, gi: int = 0, go: int = 0) -> Dict[str, Any]:
        return {
            "answer": f"Error: {msg}",
            "sql": None,
            "results": [],
            "error": msg,
            "token_usage": {
                "input_tokens": gi,
                "output_tokens": go,
                "llm_calls": 0,
                "cost_usd": 0.0,
                "elapsed_seconds": round(time.time() - t0, 2),
            },
            "agent_trace": [],
            "routing_info": None,
        }

    def _resolve_organization(self, query: str, db_name: str) -> Optional[int]:
        return resolve_organization(
            query=query,
            call_gemini=self._call_gemini,
            parse_json=self._parse_json,
            get_connection=db_connection.get_connection,
            db_name=db_name,
        )

    def _db_fallback(
        self,
        query: str,
        organization_id: int,
        user_id: int = 18,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        raw_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        adapter = BedrockAdapter(model_id=HAIKU45_ID, label="Claude Haiku 4.5")
        er = sql_engine.run(
            query,
            adapter,
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            raw_user_question=raw_query or query,
            save_message_by_session=save_message_by_session,
            update_conversation_state_hybrid_by_session=update_conversation_state_hybrid_by_session,
            maybe_auto_title=lambda sid, q: maybe_auto_title(sid, q, self._call_gemini),
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

            from gemini_brain.reasoning.claude_reasoner import ANALYST_SYSTEM_PROMPT

            system = ANALYST_SYSTEM_PROMPT.format(today=datetime.date.today().isoformat())
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
                max_tokens=1500,
            )
            er["answer"] = answer.strip()

            tu = selected_adapter.get_token_usage()
            er["token_usage"]["input_tokens"] += tu.get("input_tokens", 0)
            er["token_usage"]["output_tokens"] += tu.get("output_tokens", 0)
            er["token_usage"]["llm_calls"] += 1

        return er

    def _db_fallback_stream(
        self,
        query: str,
        organization_id: int,
        user_id: int = 18,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        raw_query: Optional[str] = None,
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
                maybe_auto_title=lambda sid, q: maybe_auto_title(sid, q, self._call_gemini),
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
            save_message_by_session=save_message_by_session,
            update_conversation_state_hybrid_by_session=update_conversation_state_hybrid_by_session,
            maybe_auto_title=lambda sid, q: maybe_auto_title(sid, q, self._call_gemini),
        )

        if er.get("success", True) and er.get("results"):
            yield {"status": "Analyzing database results", "type": "analysis"}

            data_str = json.dumps(er["results"][:40], default=str, ensure_ascii=False)
            if len(data_str) > 5000:
                data_str = data_str[:5000] + "\n... (truncated)"

            from gemini_brain.reasoning.claude_reasoner import ANALYST_SYSTEM_PROMPT

            system = ANALYST_SYSTEM_PROMPT.format(today=datetime.date.today().isoformat())
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
                max_tokens=1500,
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
        user_id: int = 18,
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

        # Internal / no-token / unconstrained test paths (allowed_org_ids is None or empty list)
        if not allowed_org_ids:
            dynamic_org_id = self._resolve_organization(query, db_name)
            if dynamic_org_id is not None:
                logger.info(
                    "Dynamically resolved organization ID %d from query '%s' (no-auth context)",
                    dynamic_org_id,
                    query,
                )
                organization_id = dynamic_org_id

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

        # 2. Check prompt-text-named org
        dynamic_org_id = self._resolve_organization(query, db_name)
        if dynamic_org_id is not None:
            if dynamic_org_id not in allowed_org_ids:
                raise ValueError(
                    f"Access denied: Resolved Organization ID {dynamic_org_id} from prompt is not in user's allowed organizations."
                )
            logger.info(
                "Dynamically resolved organization ID %d from query '%s' (verified against allow-list)",
                dynamic_org_id,
                query,
            )
            organization_id = dynamic_org_id

        # 3. Default resolution if org is not specified in body or prompt
        if organization_id is None:
            if len(allowed_org_ids) == 1:
                organization_id = allowed_org_ids[0]
                logger.info("Auto-defaulted single allowed organization ID %d", organization_id)
            else:
                raise ValueError(
                    "Multiple organizations available. Please explicitly specify organization_id in request."
                )

        return organization_id

    def run(
        self,
        query: str,
        organization_id: Optional[int] = None,
        db_name: str = "accutax_bk",
        use_api: bool = True,
        user_id: int = 18,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        allowed_org_ids: Optional[list[int]] = None,
    ) -> Dict[str, Any]:
        """Run query through Gemini Brain pipeline synchronously."""
        t0 = time.time()

        if session_id and not is_valid_uuid(session_id):
            session_id = None
        if selected_model_key and selected_model_key.strip().lower() in ("string", "null", "none", ""):
            selected_model_key = None

        raw_query = query
        redacted_query, redaction_counts = redact_pii(query)
        is_redacted = sum(redaction_counts.values()) > 0
        if is_redacted:
            logger.info("PII redacted from query. Counts: %s", redaction_counts)
        query = redacted_query

        organization_id = self._enforce_tenant_isolation(
            organization_id=organization_id,
            query=query,
            db_name=db_name,
            allowed_org_ids=allowed_org_ids,
            user_id=user_id,
            session_id=session_id,
        )

        gi = go = bi = bo = llm_calls = 0
        bedrock_model_id = HAIKU45_ID

        # 1. Classify intent
        routing, ri, ro = classify_intent(query, self._call_gemini, self._parse_json)
        gi += int(ri or 0)
        go += int(ro or 0)
        llm_calls += 1
        qtype = routing.get("type", 4)
        reason = routing.get("reason", "")
        type_lbl = self._type_label(qtype)
        logger.info("GeminiBrain type=%d (%s) — %s", qtype, type_lbl, reason)

        # 2a. LEFT PATH: Gemini direct
        if qtype in LEFT_PATH_TYPES:
            try:
                system = DIRECT_ANSWER_SYSTEM_PROMPT
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

                if (
                    selected_model_key
                    and selected_model_key != "gemini_brain"
                    and self.adapter_resolver is not None
                ):
                    selected_adapter = self.adapter_resolver(selected_model_key)
                    answer = selected_adapter.converse(
                        system_prompt=system,
                        messages=[{"role": "user", "content": [{"text": query}]}],
                        temperature=0.0,
                        max_tokens=1500,
                    )
                    tu = selected_adapter.get_token_usage()
                    ai, ao = tu.get("input_tokens", 0), tu.get("output_tokens", 0)
                else:
                    answer, ai, ao = self._call_gemini(system, query, max_tokens=1500)
                gi += int(ai or 0)
                go += int(ao or 0)
                llm_calls += 1
            except Exception as e:
                return self._err(f"Direct answer failed: {e}", t0)

            elapsed = round(time.time() - t0, 2)
            trace = []
            if is_redacted:
                trace.append({
                    "step": "pii_redactor",
                    "status": "redacted",
                    "counts": redaction_counts,
                })
            trace.extend([
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "gemini_direct",
                    "reason": reason,
                },
                {
                    "step": "gemini_answer",
                    "model": GEMINI_MODEL,
                    "tokens_in": ai,
                    "tokens_out": ao,
                },
            ])
            if session_id:
                save_message_by_session(session_id, "user", raw_query)
                save_message_by_session(session_id, "assistant", answer)
                update_conversation_state_hybrid_by_session(
                    session_id=session_id,
                    user_id=user_id,
                    query=query,
                    response=answer,
                    agent_trace=trace,
                    api_key=self.api_key,
                )
                maybe_auto_title(session_id, query, self._call_gemini)

            return {
                "answer": answer,
                "sql": None,
                "results": [],
                "error": None,
                "pii_redacted": is_redacted,
                "pii_redactions": redaction_counts,
                "token_usage": {
                    "input_tokens": gi,
                    "output_tokens": go,
                    "llm_calls": llm_calls,
                    "cost_usd": self._cost(gi, go, 0, 0, ""),
                    "elapsed_seconds": elapsed,
                },
                "agent_trace": trace,
                "routing_info": {
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "gemini_direct",
                    "reason": reason,
                },
            }

        # 2b. RIGHT PATH: API → Anthropic
        sel, ri, ro = select_endpoint(
            query,
            organization_id,
            self._call_gemini,
            self._parse_json,
            user_id=str(user_id),
        )
        gi += int(ri or 0)
        go += int(ro or 0)
        llm_calls += 1

        data = None
        endpoint = None

        if use_api and sel and sel.get("endpoint"):
            ok, raw_data = call_api(
                sel["endpoint"],
                sel.get("path_params", {}),
                sel.get("query_params", {}),
            )
            if ok:
                endpoint = sel["endpoint"]
                data = extract_data(raw_data)
            else:
                data = None

        if data is not None:
            complexity, ri, ro = judge_complexity(query, data, self._call_gemini)
            gi += int(ri or 0)
            go += int(ro or 0)
            llm_calls += 1

            try:
                answer, b_label, bi_new, bo_new = reason_over_data(
                    query=query,
                    data=data,
                    endpoint=endpoint,
                    complexity=complexity,
                    session_id=session_id,
                    selected_model_key=selected_model_key,
                    adapter_resolver=self.adapter_resolver,
                    get_project_context_by_session=get_project_context_by_session,
                )
                bi += int(bi_new or 0)
                bo += int(bo_new or 0)
                llm_calls += 1
            except Exception as e:
                return self._err(f"Anthropic reasoning failed: {e}", t0, gi, go)

            elapsed = round(time.time() - t0, 2)
            trace = []
            if is_redacted:
                trace.append({
                    "step": "pii_redactor",
                    "status": "redacted",
                    "counts": redaction_counts,
                })
            trace.extend([
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "reason": reason,
                },
                {"step": "gemini_api_selector", "endpoint": endpoint},
                {"step": "rest_api_call", "endpoint": endpoint, "status": "success"},
                {"step": "gemini_complexity", "complexity": complexity},
                {
                    "step": "anthropic_reasoning",
                    "model": b_label,
                    "tokens_in": bi_new,
                    "tokens_out": bo_new,
                },
            ])

            if session_id:
                save_message_by_session(session_id, "user", raw_query)
                save_message_by_session(session_id, "assistant", answer)
                update_conversation_state_hybrid_by_session(
                    session_id=session_id,
                    user_id=user_id,
                    query=query,
                    response=answer,
                    agent_trace=trace,
                    api_key=self.api_key,
                )
                maybe_auto_title(session_id, query, self._call_gemini)

            return {
                "answer": answer,
                "sql": None,
                "results": [],
                "error": None,
                "pii_redacted": is_redacted,
                "pii_redactions": redaction_counts,
                "token_usage": {
                    "input_tokens": gi + bi,
                    "output_tokens": go + bo,
                    "llm_calls": llm_calls,
                    "cost_usd": self._cost(gi, go, bi, bo, bedrock_model_id),
                    "elapsed_seconds": elapsed,
                },
                "agent_trace": trace,
                "routing_info": {
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "api_endpoint": endpoint,
                    "complexity": complexity,
                    "bedrock_model": b_label,
                    "reason": reason,
                },
            }

        # 2c. FALLBACK: DB engine
        logger.info("GeminiBrain DB fallback for: %.80s", query)
        try:
            er = self._db_fallback(
                query,
                organization_id,
                user_id=user_id,
                session_id=session_id,
                selected_model_key=selected_model_key,
                raw_query=raw_query,
            )
        except Exception as e:
            return self._err(f"DB fallback failed: {e}", t0, gi, go)

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

        return {
            "answer": er.get("answer", "No answer available"),
            "sql": er.get("sql"),
            "results": er.get("results", []),
            "error": er.get("error"),
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
        }

    def run_stream(
        self,
        query: str,
        organization_id: Optional[int] = None,
        db_name: str = "accutax_bk",
        use_api: bool = True,
        user_id: int = 18,
        session_id: Optional[str] = None,
        selected_model_key: Optional[str] = None,
        allowed_org_ids: Optional[list[int]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Run query through Gemini Brain pipeline with streaming status updates."""
        if session_id and not is_valid_uuid(session_id):
            session_id = None
        if selected_model_key and selected_model_key.strip().lower() in ("string", "null", "none", ""):
            selected_model_key = None

        raw_query = query
        redacted_query, redaction_counts = redact_pii(query)
        if sum(redaction_counts.values()) > 0:
            logger.info("PII redacted from streaming query. Counts: %s", redaction_counts)
        query = redacted_query

        organization_id = self._enforce_tenant_isolation(
            organization_id=organization_id,
            query=query,
            db_name=db_name,
            allowed_org_ids=allowed_org_ids,
            user_id=user_id,
            session_id=session_id,
        )

        t0 = time.time()
        gi = go = bi = bo = llm_calls = 0
        bedrock_model_id = HAIKU45_ID

        # 1. Classify
        yield {"status": "Understanding request", "type": "classification"}
        routing, ri, ro = classify_intent(query, self._call_gemini, self._parse_json)
        gi += int(ri or 0)
        go += int(ro or 0)
        llm_calls += 1
        qtype = routing.get("type", 4)
        reason = routing.get("reason", "")
        type_lbl = self._type_label(qtype)
        logger.info("GeminiBrain type=%d (%s) — %s", qtype, type_lbl, reason)

        # 2a. LEFT PATH: Gemini direct
        if qtype in LEFT_PATH_TYPES:
            yield {"status": "Generating response", "type": "generation"}
            try:
                system = DIRECT_ANSWER_SYSTEM_PROMPT
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

                if (
                    selected_model_key
                    and selected_model_key != "gemini_brain"
                    and self.adapter_resolver is not None
                ):
                    selected_adapter = self.adapter_resolver(selected_model_key)
                    answer = selected_adapter.converse(
                        system_prompt=system,
                        messages=[{"role": "user", "content": [{"text": query}]}],
                        temperature=0.0,
                        max_tokens=1500,
                    )
                    tu = selected_adapter.get_token_usage()
                    ai, ao = tu.get("input_tokens", 0), tu.get("output_tokens", 0)
                else:
                    answer, ai, ao = self._call_gemini(system, query, max_tokens=1500)
                gi += int(ai or 0)
                go += int(ao or 0)
                llm_calls += 1
            except Exception as e:
                yield {
                    "status": f"Direct answer generation failed: {str(e)}",
                    "type": "error",
                }
                yield {"final_result": self._err(f"Direct answer failed: {e}", t0)}
                return

            yield {"status": "Finalizing response", "type": "finalization"}
            elapsed = round(time.time() - t0, 2)
            trace = [
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "gemini_direct",
                    "reason": reason,
                },
                {
                    "step": "gemini_answer",
                    "model": GEMINI_MODEL,
                    "tokens_in": ai,
                    "tokens_out": ao,
                },
            ]
            if session_id:
                save_message_by_session(session_id, "user", raw_query)
                save_message_by_session(session_id, "assistant", answer)
                update_conversation_state_hybrid_by_session(
                    session_id=session_id,
                    user_id=user_id,
                    query=query,
                    response=answer,
                    agent_trace=trace,
                    api_key=self.api_key,
                )
                maybe_auto_title(session_id, query, self._call_gemini)

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
                    "agent_trace": trace,
                    "routing_info": {
                        "type": qtype,
                        "type_label": type_lbl,
                        "path": "gemini_direct",
                        "reason": reason,
                    },
                }
            }
            return

        # 2b. RIGHT PATH: API → Anthropic
        yield {"status": "Determining data source", "type": "classification"}
        sel, ri, ro = select_endpoint(
            query,
            organization_id,
            self._call_gemini,
            self._parse_json,
            user_id=str(user_id),
        )
        gi += int(ri or 0)
        go += int(ro or 0)
        llm_calls += 1

        data = None
        endpoint = None

        if use_api and sel and sel.get("endpoint"):
            friendly_title = ENDPOINT_DESCRIPTIONS.get(
                sel["endpoint"], "Retrieving financial data"
            )
            yield {"status": friendly_title, "type": "retrieval"}
            ok, raw_data = call_api(
                sel["endpoint"],
                sel.get("path_params", {}),
                sel.get("query_params", {}),
            )
            if ok:
                endpoint = sel["endpoint"]
                data = extract_data(raw_data)
            else:
                data = None

        if data is not None:
            yield {"status": "Analyzing retrieved information", "type": "analysis"}
            complexity, ri, ro = judge_complexity(query, data, self._call_gemini)
            gi += int(ri or 0)
            go += int(ro or 0)
            llm_calls += 1

            try:
                answer, b_label, bi_new, bo_new = reason_over_data(
                    query=query,
                    data=data,
                    endpoint=endpoint,
                    complexity=complexity,
                    session_id=session_id,
                    selected_model_key=selected_model_key,
                    adapter_resolver=self.adapter_resolver,
                    get_project_context_by_session=get_project_context_by_session,
                )
                bi += int(bi_new or 0)
                bo += int(bo_new or 0)
                llm_calls += 1
            except Exception as e:
                yield {
                    "status": f"Analysis reasoning failed: {str(e)}",
                    "type": "error",
                }
                yield {
                    "final_result": self._err(
                        f"Anthropic reasoning failed: {e}", t0, gi, go
                    )
                }
                return

            yield {"status": "Finalizing response", "type": "finalization"}
            elapsed = round(time.time() - t0, 2)
            trace = [
                {
                    "step": "gemini_router",
                    "type": qtype,
                    "type_label": type_lbl,
                    "path": "api_then_anthropic",
                    "reason": reason,
                },
                {"step": "gemini_api_selector", "endpoint": endpoint},
                {"step": "rest_api_call", "endpoint": endpoint, "status": "success"},
                {"step": "gemini_complexity", "complexity": complexity},
                {
                    "step": "anthropic_reasoning",
                    "model": b_label,
                    "tokens_in": bi_new,
                    "tokens_out": bo_new,
                },
            ]

            if session_id:
                save_message_by_session(session_id, "user", raw_query)
                save_message_by_session(session_id, "assistant", answer)
                update_conversation_state_hybrid_by_session(
                    session_id=session_id,
                    user_id=user_id,
                    query=query,
                    response=answer,
                    agent_trace=trace,
                    api_key=self.api_key,
                )
                maybe_auto_title(session_id, query, self._call_gemini)

            yield {
                "final_result": {
                    "answer": answer,
                    "sql": None,
                    "results": [],
                    "error": None,
                    "token_usage": {
                        "input_tokens": gi + bi,
                        "output_tokens": go + bo,
                        "llm_calls": llm_calls,
                        "cost_usd": self._cost(gi, go, bi, bo, bedrock_model_id),
                        "elapsed_seconds": elapsed,
                    },
                    "agent_trace": trace,
                    "routing_info": {
                        "type": qtype,
                        "type_label": type_lbl,
                        "path": "api_then_anthropic",
                        "api_endpoint": endpoint,
                        "complexity": complexity,
                        "bedrock_model": b_label,
                        "reason": reason,
                    },
                }
            }
            return

        # 2c. FALLBACK: DB engine
        logger.info("GeminiBrain DB fallback for: %.80s", query)
        yield {"status": "Routing to local database engine", "type": "retrieval"}

        for chunk in self._db_fallback_stream(
            query,
            organization_id,
            user_id=user_id,
            session_id=session_id,
            selected_model_key=selected_model_key,
            raw_query=raw_query,
        ):
            yield chunk

