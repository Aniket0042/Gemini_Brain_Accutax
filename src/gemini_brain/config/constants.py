"""
constants.py — Model identifiers, timeouts, caps, and static defaults.

Extracted from:
  - gemini_brain_adapter.py  lines 44-50  (model IDs, user ID)
  - engine.py                lines 49-50  (MAX_ITERATIONS, TIME_BUDGET)
  - api_agent.py             line 43      (HTTP timeout)
  - executor.py              line 71      (SQL timeout)
  - bedrock_adapter.py       lines 29-48  (cache/cross-region config)

All values are preserved exactly as found in the original source.
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────
# Gemini model
# ─────────────────────────────────────────────────────────────
GEMINI_MODEL: str = "gemini-3.5-flash"

# ─────────────────────────────────────────────────────────────
# Bedrock / Claude model identifiers
# ─────────────────────────────────────────────────────────────
HAIKU3_ID: str = "anthropic.claude-3-haiku-20240307-v1:0"
HAIKU45_ID: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET35_ID: str = "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"

# ─────────────────────────────────────────────────────────────
# Complexity → model mapping
# ─────────────────────────────────────────────────────────────
COMPLEXITY_MODEL_MAP: dict[str, tuple[str, str]] = {
    "SIMPLE":  (HAIKU3_ID,   "Claude 3 Haiku"),
    "MEDIUM":  (HAIKU45_ID,  "Claude Haiku 4.5"),
    "COMPLEX": (SONNET35_ID, "Claude 3.5 Sonnet"),
}

# ─────────────────────────────────────────────────────────────
# Type labels for the 7-type intent classification
# ─────────────────────────────────────────────────────────────
TYPE_LABELS: dict[int, str] = {
    1: "FAQ/How-to",
    2: "App Guidance",
    3: "Report Generation",
    4: "Data Query",
    5: "Forecast",
    6: "Accounting Concept",
    7: "Summary & Advice",
}

#: Intent types routed to the LEFT path (Gemini direct answer).
LEFT_PATH_TYPES: frozenset[int] = frozenset({1, 2, 6, 7})

#: Intent types routed to the RIGHT path (API → Claude reasoning).
RIGHT_PATH_TYPES: frozenset[int] = frozenset({3, 4, 5})

# ─────────────────────────────────────────────────────────────
# Truncation / capping limits
# ─────────────────────────────────────────────────────────────
#: Max chars of data preview sent to the complexity judge.
COMPLEXITY_PREVIEW_MAX_CHARS: int = 800

#: Max number of items/list entries sent to Claude for reasoning.
REASONING_MAX_ITEMS: int = 40

#: A payload with at least this many rows (or a breakdown with this many entries)
#: is narrated with the detailed prompt and budget instead of the 120-word one.
#: Below it, a terse answer is the right answer — "revenue is AED X" needs no
#: room to breathe, and the extra output tokens would be pure latency.
NARRATION_TABULAR_MIN_ROWS: int = 3

#: Output-token ceilings per payload shape. Output tokens dominate generation
#: time, so these are the main latency lever in the narration stage — raise the
#: tabular one only with a measurement to back it.
NARRATION_MAX_TOKENS_SCALAR: int = 400
NARRATION_MAX_TOKENS_TABULAR: int = 1200

#: Max chars of serialised data sent to Claude for reasoning.
REASONING_MAX_CHARS: int = 5000

# ─────────────────────────────────────────────────────────────
# Timeouts & limits
# ─────────────────────────────────────────────────────────────
#: HTTP timeout for Accutax REST API calls (seconds).
HTTP_TIMEOUT: float = 8.0

#: PostgreSQL statement_timeout (milliseconds string, passed to SET).
SQL_TIMEOUT_MS: str = "20000"

#: Engine tool-calling loop limits.
ENGINE_MAX_ITERATIONS: int = 5
ENGINE_TIME_BUDGET_SECONDS: int = 90

#: Tool results older than the last exchange are truncated to this many characters
#: before the next iteration. The full result was already read by the model on the
#: turn it arrived; keeping it verbatim only re-sends it as input tokens on every
#: subsequent iteration. Key aggregates survive because compact_tool_result puts
#: PERIOD/SUMMARY first (see hoisting in sql_engine).
ENGINE_TOOL_RESULT_TRUNCATE_CHARS: int = 600

#: Number of trailing messages left untruncated — the current exchange the model
#: is actively reasoning over.
ENGINE_TRUNCATION_KEEP_RECENT: int = 2

#: Extra wall-clock allowance, on top of ENGINE_TIME_BUDGET_SECONDS, for turning
#: whatever the loop retrieved into a real answer after it exits without one.
#: Deliberately small: recovery only ever runs on a query that already spent its
#: whole budget, so an unbounded retry is how a 90s query becomes a 120s one.
#: Past this deadline the engine stops calling the LLM and renders the rows it
#: has with the deterministic formatter instead.
ENGINE_RECOVERY_BUDGET_SECONDS: int = 8

# ─────────────────────────────────────────────────────────────
# Bedrock prompt-caching configuration
# ─────────────────────────────────────────────────────────────
#: Claude model substrings known to support Bedrock prompt caching.
CACHE_SUPPORTED_MODELS: tuple[str, ...] = (
    "claude-3-5-sonnet",
    "claude-3-5-haiku",
    "claude-3-7-sonnet",
    "claude-3-7",
)

#: Cross-region inference profile prefixes.
CROSS_REGION_PREFIXES: tuple[str, ...] = ("apac.", "us.", "eu.", "global.")

#: Passthrough tool config required for cross-region converse calls.
PASS_THROUGH_TOOL_CONFIG: dict = {
    "tools": [{
        "toolSpec": {
            "name": "passthrough",
            "description": "Fallback tool. Do not call this — always respond with plain text.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                    "required": [],
                }
            },
        }
    }],
}

# ─────────────────────────────────────────────────────────────
# Shared guardrail — appended to every system prompt that can
# produce user-facing text (direct answers, narration, SQL-fallback
# narration). One rule, reused everywhere, so it can't be missing
# from whichever path happens to generate a given response.
# ─────────────────────────────────────────────────────────────
NEVER_EXPOSE_BACKEND_RULE: str = """
CRITICAL — you are speaking to a business owner, never a developer:
- Any table, column, schema, SQL, query, or raw error text you use internally to find an answer is fine — but never carry it into what you actually say to the user. Translate every technical result into plain business language.
- Never describe your own architecture, data sources, or internal limitations in technical terms (e.g. don't say "I query a database" or "I'm not designed for X") — just answer helpfully, or say plainly what you don't have.
- Any reference material given to you (documents, prior conversation history, retrieved records) is background for your understanding only — never quote it verbatim, name its source, or reveal its structure to the user."""

# ─────────────────────────────────────────────────────────────
# Streaming endpoint descriptions
# ─────────────────────────────────────────────────────────────
ENDPOINT_DESCRIPTIONS: dict[str, str] = {
    "/income/total": "Retrieving total income data",
    "/expense/total": "Retrieving total expense data",
    "/report/cash-forecast": "Retrieving cash flow forecast data",
    "/report/customer-balance-summary": "Retrieving customer balance details",
    "/report/ar-aging-summary": "Retrieving accounts receivable aging report",
    "/report/profit-loss": "Retrieving Profit and Loss statement",
    "/report/balance-sheet": "Retrieving Balance Sheet",
    "/report/sales-by-customer": "Retrieving customer sales analysis",
    "/report/expense-by-category": "Retrieving category expense breakdown",
    "/bank/manual/accounts": "Retrieving bank account details",
}
