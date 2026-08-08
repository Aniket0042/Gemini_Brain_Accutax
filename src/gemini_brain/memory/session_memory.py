"""
session_memory.py — Session history, conversation state, thread auto-titling, and project context.

Extracted from memory.py lines 617-639 (rename_thread), 670-784 (get_history_by_session, save_message_by_session, get_state_by_session, update_state_by_session),
890-931 (get_thread_name, maybe_auto_title), and 1145-1229 (get_project_context_by_session).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable, Dict, List, Optional

import psycopg2.extras

from gemini_brain.config.settings import settings
from gemini_brain.sql_fallback.db_connection import get_connection

logger = logging.getLogger("gemini_brain.memory.session_memory")


def is_valid_uuid(val: Any) -> bool:
    """Check if a value is a valid UUID string."""
    if not val or not isinstance(val, str):
        return False
    v = val.strip()
    if v.lower() in ("string", "null", "none", ""):
        return False
    try:
        uuid.UUID(v)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def verify_session_ownership(
    session_id: str,
    user_id: int,
    db_name: str = "",
) -> bool:
    """Verify if a chat session belongs to the given user_id."""
    if not is_valid_uuid(session_id):
        return True
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM public.model_arena_chat_sessions WHERE id::text = %s;",
            (str(session_id),),
        )
        row = cur.fetchone()
        if not row:
            return True  # New session ID, allowed to be claimed by user
        return int(row[0]) == int(user_id)
    except Exception as e:
        logger.warning("Session ownership verification skipped due to DB connection error for session %s: %s", session_id, e)
        return True
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def save_message_by_session(
    session_id: str,
    role: str,
    content: str,
    db_name: str = "",
) -> None:
    """Save a user or assistant message for a specific session."""
    if not is_valid_uuid(session_id):
        return
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO public.model_arena_chat_messages (session_id, role, content) 
            VALUES (%s, %s, %s)
        """,
            (session_id, role, content),
        )
        cur.execute(
            """
            UPDATE public.model_arena_chat_sessions 
            SET updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """,
            (session_id,),
        )
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to save message for session %s: %s", session_id, e)
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_history_by_session(
    session_id: str,
    limit: int = 10,
    db_name: str = "",
) -> List[Dict[str, Any]]:
    """Retrieve the last N messages for a specific session in chronological order."""
    if not is_valid_uuid(session_id):
        return []
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT role, content 
            FROM (
                SELECT role, content, created_at 
                FROM public.model_arena_chat_messages 
                WHERE session_id = %s 
                ORDER BY created_at DESC 
                LIMIT %s
            ) AS recent 
            ORDER BY created_at ASC
        """,
            (session_id, limit),
        )
        rows = cur.fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]
    except Exception as e:
        logger.error("Failed to get chat history for session %s: %s", session_id, e)
        return []
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_state_by_session(session_id: str, db_name: str = "") -> Dict[str, Any]:
    """Get current structured conversation state for a specific session."""
    if not is_valid_uuid(session_id):
        return {}
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT conversation_state 
            FROM public.model_arena_chat_sessions 
            WHERE id = %s
        """,
            (session_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
        return {}
    except Exception as e:
        logger.error("Failed to get state for session %s: %s", session_id, e)
        return {}
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def update_state_by_session(
    session_id: str,
    new_state: Dict[str, Any],
    db_name: str = "",
) -> None:
    """Update conversation state for a specific session."""
    if not is_valid_uuid(session_id):
        return
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE public.model_arena_chat_sessions 
            SET conversation_state = %s, updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """,
            (psycopg2.extras.Json(new_state), session_id),
        )
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to update state for session %s: %s", session_id, e)
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_thread_name(session_id: str, db_name: str = "") -> Optional[str]:
    """Retrieve the name of a specific session."""
    if not is_valid_uuid(session_id):
        return None
    conn = get_connection(db_name)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT name FROM public.model_arena_chat_sessions WHERE id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error("Failed to get thread name: %s", e)
        return None
    finally:
        cur.close()
        conn.close()


def rename_thread(session_id: str, new_name: str, db_name: str = "") -> None:
    """Rename a specific chat thread."""
    if not is_valid_uuid(session_id):
        return
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE public.model_arena_chat_sessions 
            SET name = %s, updated_at = CURRENT_TIMESTAMP 
            WHERE id = %s
        """,
            (new_name, session_id),
        )
        conn.commit()
        logger.info("Renamed thread id=%s to '%s'", session_id, new_name)
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to rename thread: %s", e)
    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def maybe_auto_title(
    session_id: str,
    user_question: str,
    call_gemini: Optional[Callable[[str, str, int], Tuple[str, int, int]]] = None,
    db_name: str = "",
) -> None:
    """Auto-title session using Gemini 2.5 Flash if name is 'New Chat'."""
    if not is_valid_uuid(session_id):
        return
    try:
        current_name = get_thread_name(session_id, db_name)
        if current_name == "New Chat":
            prompt = f'Summarize this financial query in 3 words or less (no punctuation, start directly): "{user_question}"'
            if call_gemini is not None:
                title_text, _, _ = call_gemini("You are a title summarizer.", prompt, 100)
            else:
                from google import genai
                from google.genai import types

                api_key = settings.gemini_api_key or os.getenv(
                    "GEMINI_API_KEY", ""
                )
                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                        temperature=0.0,
                        max_output_tokens=100,
                    ),
                )
                title_text = resp.text if resp else ""

            title = title_text.strip().replace('"', "").replace("'", "") if title_text else None
            if title:
                rename_thread(session_id, title, db_name)
    except Exception as e:
        logger.warning("Auto-titling failed for session %s: %s", session_id, e)


def get_project_context_by_session(
    session_id: str,
    db_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Retrieve project context (instructions, files, cross-chat history) for a session."""
    if not is_valid_uuid(session_id):
        return None
    conn = get_connection(db_name)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT project_id 
            FROM public.model_arena_chat_sessions 
            WHERE id = %s
        """,
            (session_id,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return None

        project_id = row[0]

        cur.execute(
            """
            SELECT name, instructions 
            FROM public.model_arena_projects 
            WHERE id = %s
        """,
            (project_id,),
        )
        proj_row = cur.fetchone()
        if not proj_row:
            return None

        proj_name, instructions = proj_row

        cur.execute(
            """
            SELECT filename, content 
            FROM public.model_arena_project_files 
            WHERE project_id = %s
        """,
            (project_id,),
        )
        file_rows = cur.fetchall()
        files = [{"filename": r[0], "content": r[1]} for r in file_rows]

        cur.execute(
            """
            SELECT id, name 
            FROM public.model_arena_chat_sessions 
            WHERE project_id = %s AND id != %s
        """,
            (project_id, session_id),
        )
        other_sessions = cur.fetchall()

        cross_chat_history = []
        for s_id, s_name in other_sessions:
            cur.execute(
                """
                SELECT role, content 
                FROM public.model_arena_chat_messages 
                WHERE session_id = %s 
                ORDER BY created_at DESC 
                LIMIT 5
            """,
                (s_id,),
            )
            msg_rows = cur.fetchall()
            messages = [{"role": r[0], "content": r[1]} for r in reversed(msg_rows)]
            if messages:
                cross_chat_history.append(
                    {"id": str(s_id), "name": s_name, "messages": messages}
                )

        return {
            "project_id": str(project_id),
            "project_name": proj_name,
            "instructions": instructions,
            "files": files,
            "cross_chat_history": cross_chat_history,
        }
    except Exception as e:
        logger.error("Failed to get project context by session %s: %s", session_id, e)
        return None
    finally:
        cur.close()
        conn.close()
