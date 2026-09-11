"""
handlers.py — Asynchronous tool execution handlers for Gemini Brain.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from gemini_brain.api_client.accutax_client import call_api_async, extract_data
from gemini_brain.tools.context import RequestCtx

logger = logging.getLogger("gemini_brain.tools.handlers")


def make_api_handler(endpoint: str) -> Callable[[Any, RequestCtx], Any]:
    """Factory creating an asynchronous API handler for a given endpoint."""
    async def handler(params: Any, ctx: RequestCtx) -> Any:
        path_params = params.to_path_params(ctx) if hasattr(params, "to_path_params") else {}
        query_params = params.to_query(ctx) if hasattr(params, "to_query") else {}

        ok, raw = await call_api_async(
            endpoint=endpoint,
            path_params=path_params,
            query_params=query_params,
        )
        if not ok:
            raise RuntimeError(f"Accutax API error from {endpoint}: {raw}")
        return extract_data(raw)

    return handler


def make_sql_function_handler(func_name: str) -> Callable[[Any, RequestCtx], Any]:
    """Factory creating an asynchronous database handler for an analytical PostgreSQL function."""
    async def handler(params: Any, ctx: RequestCtx) -> Any:
        import asyncio
        from gemini_brain.sql_fallback.db_connection import execute_sql_function

        args = params.to_sql_args(ctx) if hasattr(params, "to_sql_args") else (ctx.org_id,)
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(
            None,
            execute_sql_function,
            func_name,
            args,
            ctx.org_id,
            ctx.db_name,
        )
        return rows

    return handler

