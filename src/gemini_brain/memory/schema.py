"""
schema.py — Database schema DDL initialization for session memory.

Extracted from memory.py initialize_tables (lines 24-167).
Creates ONLY the 4 tables and 1 index actually touched by session memory functions:
  1. model_arena_projects
  2. model_arena_project_files
  3. model_arena_chat_sessions
  4. model_arena_chat_messages
  5. idx_model_arena_messages_session_time
Excludes rate limiting (query_logs) and user auth tables.
"""
from __future__ import annotations

import logging
from typing import Any

from gemini_brain.sql_fallback.db_connection import get_connection

logger = logging.getLogger("gemini_brain.memory.schema")


def initialize_tables(db_name: str = "") -> None:
    """Create session state, messages, projects, and project files tables if they do not exist."""
    conn = get_connection(db_name)
    cur = conn.cursor()
    try:
        # Check if table exists and matches the new UUID schema
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='model_arena_chat_sessions' AND column_name='name' AND table_schema='public'
            )
        """)
        has_new_schema = cur.fetchone()[0]

        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_name='model_arena_chat_sessions' AND table_schema='public'
            )
        """)
        table_exists = cur.fetchone()[0]

        if table_exists and not has_new_schema:
            logger.info("Old model_arena tables detected. Dropping for multi-chat UUID schema migration...")
            cur.execute("DROP TABLE IF EXISTS public.model_arena_chat_messages CASCADE;")
            cur.execute("DROP TABLE IF EXISTS public.model_arena_chat_sessions CASCADE;")

        # 1. Projects (workspace groups)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.model_arena_projects (
                id UUID PRIMARY KEY,
                user_id INT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                instructions TEXT,
                endpoint VARCHAR(50) NOT NULL DEFAULT 'local',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Project files (knowledge documents)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.model_arena_project_files (
                id UUID PRIMARY KEY,
                project_id UUID NOT NULL REFERENCES public.model_arena_projects(id) ON DELETE CASCADE,
                filename VARCHAR(255) NOT NULL,
                file_size INT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. Chat sessions (conversation state)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.model_arena_chat_sessions (
                id UUID PRIMARY KEY,
                user_id INT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL DEFAULT 'New Chat',
                conversation_state JSONB NOT NULL DEFAULT '{}'::jsonb,
                endpoint VARCHAR(50) NOT NULL DEFAULT 'local',
                project_id UUID REFERENCES public.model_arena_projects(id) ON DELETE CASCADE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Dynamic column checks if table already existed
        if table_exists:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='model_arena_chat_sessions' AND column_name='endpoint' AND table_schema='public'
                )
            """)
            has_endpoint = cur.fetchone()[0]
            if not has_endpoint:
                cur.execute("ALTER TABLE public.model_arena_chat_sessions ADD COLUMN endpoint VARCHAR(50) NOT NULL DEFAULT 'local';")

            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='model_arena_chat_sessions' AND column_name='project_id' AND table_schema='public'
                )
            """)
            has_project_id = cur.fetchone()[0]
            if not has_project_id:
                cur.execute("ALTER TABLE public.model_arena_chat_sessions ADD COLUMN project_id UUID REFERENCES public.model_arena_projects(id) ON DELETE CASCADE;")

        # 4. Chat messages
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.model_arena_chat_messages (
                id SERIAL PRIMARY KEY,
                session_id UUID NOT NULL REFERENCES public.model_arena_chat_sessions(id) ON DELETE CASCADE,
                role VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 5. Message index for fast chronological sorting
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_arena_messages_session_time 
            ON public.model_arena_chat_messages (session_id, created_at ASC);
        """)

        conn.commit()
        logger.info("Database tables initialized successfully for %s", db_name)
    except Exception as e:
        conn.rollback()
        logger.error("Failed to initialize database tables for %s: %s", db_name, e)
        raise e
    finally:
        cur.close()
        conn.close()
