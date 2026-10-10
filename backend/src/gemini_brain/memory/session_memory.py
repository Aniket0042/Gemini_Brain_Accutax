"""
session_memory.py — Session history, conversation state, and project context.

Extracted from memory.py lines 670-784 (get_history_by_session, save_message_by_session, get_state_by_session, update_state_by_session),
and 1145-1229 (get_project_context_by_session).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from gemini_brain.db.connection import get_connection

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


def session_scope(rec: Optional[Dict[str, Any]]) -> List[int]:
    """The sorted organization IDs a thread belongs to; [] when it has none yet."""
    if not rec:
        return []
    ids = rec.get("organization_ids")
    if ids:
        return sorted({int(o) for o in ids})
    org = rec.get("organization_id")
    return [int(org)] if org is not None else []


def _scope_columns(scope: List[int]) -> Tuple[Optional[int], List[int]]:
    """(organization_id, organization_ids) column values for a scope."""
    return (scope[0] if len(scope) == 1 else None), scope


def ensure_session(
    session_id: str,
    user_id: int,
    organization_id: Optional[int] = None,
    db_name: str = "",
    organization_ids: Optional[List[int]] = None,
) -> bool:
    """Create the session row if it does not exist so message inserts satisfy the FK.

    Returns True when the session exists and belongs to ``user_id``. A thread's
    organization scope is fixed by its first scoped request: a later request
    for a different set of organizations is refused, and a thread with no
    scope yet takes the requested one.
    """
    if not is_valid_uuid(session_id):
        return False
    if organization_ids:
        scope: Optional[List[int]] = sorted({int(o) for o in organization_ids})
    elif organization_id is not None:
        scope = [int(organization_id)]
    else:
        scope = None
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, organization_id, organization_ids
            FROM public.model_arena_chat_sessions WHERE id = %s;
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if row:
            owner_id = int(row[0])
            if owner_id != int(user_id):
                logger.warning(
                    "ensure_session refused: session %s owned by %s not %s",
                    session_id, owner_id, user_id,
                )
                return False
            stored = session_scope({"organization_id": row[1], "organization_ids": row[2]})
            if scope is not None and stored and stored != scope:
                logger.warning(
                    "ensure_session refused: session %s is orgs %s not %s",
                    session_id, stored, scope,
                )
                return False
            if scope is not None and not stored:
                cur.execute(
                    """
                    UPDATE public.model_arena_chat_sessions
                    SET organization_id = %s, organization_ids = %s
                    WHERE id = %s;
                    """,
                    (*_scope_columns(scope), session_id),
                )
                conn.commit()
            return True

        org_col, ids_col = _scope_columns(scope or [])
        cur.execute(
            """
            INSERT INTO public.model_arena_chat_sessions (id, user_id, organization_id, organization_ids, name)
            VALUES (%s, %s, %s, %s, 'New Chat')
            ON CONFLICT (id) DO NOTHING;
            """,
            (session_id, int(user_id), org_col, ids_col or None),
        )
        conn.commit()
        return True
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to ensure session %s: %s", session_id, e)
        return False
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


def get_session_record(
    session_id: str,
    db_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Return session metadata or None."""
    if not is_valid_uuid(session_id):
        return None
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, organization_id, name, conversation_state, created_at, updated_at,
                   organization_ids
            FROM public.model_arena_chat_sessions
            WHERE id = %s;
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "user_id": int(row[1]),
            "organization_id": int(row[2]) if row[2] is not None else None,
            "name": row[3],
            "conversation_state": row[4] or {},
            "created_at": row[5].isoformat() if row[5] else None,
            "updated_at": row[6].isoformat() if row[6] else None,
            "organization_ids": session_scope({"organization_id": row[2], "organization_ids": row[7]}),
        }
    except Exception as e:
        logger.error("Failed to get session %s: %s", session_id, e)
        return None
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


def delete_session(session_id: str, user_id: int, db_name: str = "") -> bool:
    """Permanently delete a thread and its messages. Returns False if missing or not owned."""
    if not is_valid_uuid(session_id):
        return False
    if not verify_session_ownership(session_id, user_id, db_name=db_name):
        return False
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM public.model_arena_chat_sessions WHERE id = %s AND user_id = %s;",
            (session_id, int(user_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to delete session %s: %s", session_id, e)
        return False
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


def list_sessions_for_user_org(
    user_id: int,
    organization_id: Optional[int],
    limit: int = 20,
    db_name: str = "",
    organization_ids: Optional[List[int]] = None,
    allowed_org_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Most recently updated threads for this user.

    ``organization_ids`` lists threads whose scope is exactly that set;
    ``organization_id`` lists single-org threads for that org. With
    ``allowed_org_ids``, a thread is listed only when every organization in its
    scope is still allowed, so threads from revoked organizations disappear.
    Threads with no scope yet hold no organization data and are always listed.
    """
    scope_sql = (
        "COALESCE(s.organization_ids, "
        "CASE WHEN s.organization_id IS NULL THEN NULL ELSE ARRAY[s.organization_id] END)"
    )
    where = ["s.user_id = %s"]
    params: List[Any] = [int(user_id)]
    if organization_ids:
        where.append(f"{scope_sql} = %s::int[]")
        params.append(sorted({int(o) for o in organization_ids}))
    elif organization_id is not None:
        where.append("s.organization_id = %s")
        params.append(int(organization_id))
    if allowed_org_ids is not None:
        where.append(f"({scope_sql} IS NULL OR {scope_sql} <@ %s::int[])")
        params.append([int(o) for o in allowed_org_ids])
    params.append(int(limit))

    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT s.id, s.user_id, s.organization_id, s.name, s.created_at, s.updated_at,
                   COALESCE(m.cnt, 0) AS message_count, s.organization_ids
            FROM public.model_arena_chat_sessions s
            LEFT JOIN (
                SELECT session_id, COUNT(*) AS cnt
                FROM public.model_arena_chat_messages
                GROUP BY session_id
            ) m ON m.session_id = s.id
            WHERE {" AND ".join(where)}
            ORDER BY s.updated_at DESC
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = cur.fetchall()
        return [
            {
                "id": str(r[0]),
                "user_id": int(r[1]),
                "organization_id": int(r[2]) if r[2] is not None else None,
                "organization_ids": session_scope({"organization_id": r[2], "organization_ids": r[7]}),
                "name": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "updated_at": r[5].isoformat() if r[5] else None,
                "message_count": int(r[6] or 0),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error("Failed to list sessions for user %s org %s: %s", user_id, organization_id, e)
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


def count_messages_by_session(session_id: str, db_name: str = "") -> int:
    if not is_valid_uuid(session_id):
        return 0
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM public.model_arena_chat_messages WHERE session_id = %s;",
            (session_id,),
        )
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    except Exception as e:
        logger.error("Failed to count messages for session %s: %s", session_id, e)
        return 0
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


def get_transcript_by_session(
    session_id: str,
    limit: int = 50,
    db_name: str = "",
) -> List[Dict[str, Any]]:
    """Full transcript for UI restore (chronological, includes timestamps)."""
    if not is_valid_uuid(session_id):
        return []
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT role, content, created_at, blocks
                FROM (
                    SELECT role, content, created_at, blocks
                    FROM public.model_arena_chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ) AS recent
                ORDER BY created_at ASC
                """,
                (session_id, int(limit)),
            )
            rows = cur.fetchall()
            return [
                {
                    "role": r[0],
                    "content": r[1],
                    "created_at": r[2].isoformat() if r[2] else None,
                    "blocks": r[3] if len(r) > 3 else None,
                }
                for r in rows
            ]
        except Exception:
            conn.rollback()
            cur.execute(
                """
                SELECT role, content, created_at
                FROM (
                    SELECT role, content, created_at
                    FROM public.model_arena_chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ) AS recent
                ORDER BY created_at ASC
                """,
                (session_id, int(limit)),
            )
            rows = cur.fetchall()
            return [
                {
                    "role": r[0],
                    "content": r[1],
                    "created_at": r[2].isoformat() if r[2] else None,
                    "blocks": None,
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to get transcript for session %s: %s", session_id, e)
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


def verify_session_org(
    session_id: str,
    organization_id: Optional[int],
    db_name: str = "",
) -> bool:
    """True when the session has no org yet, or it matches ``organization_id``."""
    if organization_id is None or not is_valid_uuid(session_id):
        return True
    rec = get_session_record(session_id, db_name=db_name)
    if not rec:
        return True
    stored = rec.get("organization_id")
    if stored is None:
        return True
    return int(stored) == int(organization_id)


def save_message_by_session(
    session_id: str,
    role: str,
    content: str,
    db_name: str = "",
    blocks: Optional[List[Dict[str, Any]]] = None,
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
            INSERT INTO public.model_arena_chat_messages (session_id, role, content, blocks)
            VALUES (%s, %s, %s, %s)
        """,
            (
                session_id,
                role,
                content,
                psycopg2.extras.Json(blocks) if blocks is not None else None,
            ),
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


def update_last_assistant_blocks(
    session_id: str,
    blocks: List[Dict[str, Any]],
    db_name: str = "",
) -> None:
    """Stamp structured blocks onto the latest assistant turn after delivery runs."""
    if not is_valid_uuid(session_id) or not blocks:
        return
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE public.model_arena_chat_messages
            SET blocks = %s
            WHERE id = (
                SELECT id FROM public.model_arena_chat_messages
                WHERE session_id = %s AND role = 'assistant'
                ORDER BY created_at DESC
                LIMIT 1
            )
            """,
            (psycopg2.extras.Json(blocks), session_id),
        )
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to persist blocks for session %s: %s", session_id, e)
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


def get_first_user_message_by_session(session_id: str, db_name: str = "") -> str:
    """Earliest user message in the thread, used to pin 'what was my first query?'."""
    if not is_valid_uuid(session_id):
        return ""
    conn = None
    cur = None
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT content
            FROM public.model_arena_chat_messages
            WHERE session_id = %s AND lower(role) = 'user' AND content IS NOT NULL
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (session_id,),
        )
        row = cur.fetchone()
        return (row[0] or "").strip() if row else ""
    except Exception as e:
        logger.error("Failed to get first user message for session %s: %s", session_id, e)
        return ""
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
