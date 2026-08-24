# Gemini Brain — Robustness & Graceful Degradation Specification

**Version:** 1.0
**Date:** 2026-08-20
**Status:** Ready for implementation
**Audience:** Human engineers and coding agents implementing the changes

---

## 0. How to read and use this document

This document has two halves:

- **Part A (§1–§5)** — *Diagnosis.* Every place in the codebase today where a failure, an empty
  result, or a null value reaches the user in a raw, ugly, or misleading form. Each finding has
  a file, a line reference, a reproduction, and the exact symptom the user sees.
- **Part B (§6–§13)** — *Prescription.* A phased, step-by-step implementation plan. Each phase is
  independently shippable, has explicit "files touched", "do not touch", and an acceptance test.

### Rules for the implementing agent

1. **Do the phases in order.** Phase 0 creates shared modules that later phases import. Skipping
   ahead will produce import errors.
2. **Never change routing, tenant-isolation, SQL-rewriting, or PII logic.** Those are correctness-
   and security-critical and are out of scope. The only permitted change to
   `enforce_tenant_isolation_sql` and `redact_pii` is *none*.
3. **Additive over invasive.** Prefer adding a new module + calling it, over rewriting an existing
   function body. Where a function body must change, the diff should be localised.
4. **Every phase must leave the app runnable.** Run `pytest tests/unit -q` after each phase; it must
   stay green.
5. **No new hard dependencies** without noting them in §13. The plan requires zero new Python
   packages and zero new npm packages.
6. **Preserve public response keys.** `answer`, `sql`, `results`, `error`, `token_usage`,
   `agent_trace`, `routing_info`, `query_trace` must all keep existing. We only *add* keys.

---

# PART A — DIAGNOSIS

## 1. System flow map (as-is)

```
                    POST /api/v1/query  |  POST /api/v1/query/stream
                                  │
                    routes.py  →  get_current_user (JWT)          ── raises 401
                                  │
                    GeminiBrainRunner.run() / .run_stream()
                                  │
                    ┌─────────────┴──────────────┐
                    │  redact_pii()              │
                    │  _enforce_tenant_isolation │ ── raises ValueError → 400 / SSE error chunk
                    └─────────────┬──────────────┘
                                  │
                       fast_route(query)   (regex, 0 LLM)
                          ┌───────┴────────┐
                     hit  │                │  miss
                          │        classify_intent()  (Gemini)
                          │                │
                          │        type ∈ {1,2,6,7}?  ── LEFT PATH ──► Gemini direct answer
                          │                │                            └─ fallback: Bedrock Haiku
                          │           type ∈ {3,4,5}
                          │                │
                          │        select_endpoint()  (Gemini)
                          │                └─ fallback: keyword_endpoint_fallback()
                          └───────┬────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  result_cache.get_sync()  │ ── HIT → data
                    ├───────────────────────────┤
                    │  endpoint.startswith fn_  │ ── execute_sql_function() (Postgres RLS)
                    ├───────────────────────────┤
                    │  call_api() (httpx)       │ ── (ok, raw) → extract_data(raw)
                    └─────────────┬─────────────┘
                                  │
                        ┌─────────▼─────────┐
                        │ if data is not None│   ◄── ★ THE CENTRAL DEFECT (§2.1)
                        └────┬─────────┬─────┘
                       true  │         │  false
                             │         │
              render(formatter, data)  └──► SQL FALLBACK ENGINE
              reason_over_data()             sql_engine.run()
              (Bedrock Claude)                 ├─ _get_coordinator_pipeline()  ── RuntimeError
                             │                 ├─ try_fast_path()
                             │                 ├─ tool loop (≤5 iters, ≤90s)
                             │                 └─ _graceful_no_data_answer()
                             │                              │
                             └──────────┬───────────────────┘
                                        ▼
                             QueryResponse(**result)   ◄── ★ 500 SOURCE (§2.2)
                                        │
                               UI: App.jsx → ResponseView.jsx
                                        │
                             PacedMarkdownStream → ReactMarkdown
```

### The three orthogonal failure axes

| Axis | Question | Today's behaviour |
|---|---|---|
| **Availability** | Did we reach the data source at all? | Collapsed into `data is None` |
| **Emptiness** | Did the source return zero rows? | Collapsed into `data is not None` — treated as success |
| **Trust** | Is the payload actually data, or an error envelope? | Not checked at all |

Every defect below is a consequence of collapsing three axes into one `is not None` check.

---

## 2. Defect inventory

Severity: **P0** = user sees raw error/garbage; **P1** = user sees misleading content; **P2** = polish.

### 2.1 — P0 · Empty API payload is narrated as if it were data

**File:** `src/gemini_brain/orchestrator/gemini_brain_runner.py:688` (sync) and `:1147` (stream)

```python
if data is not None:
    results_payload = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    ...
    answer, b_label, bi_new, bo_new = reason_over_data(query=query, data=data, ...)
```

`data == []` and `data == {}` and `data == {"items": []}` all satisfy `is not None`. The empty
payload is serialised into the prompt in `claude_reasoner._format_payload_and_system`:

```
Live DATA from `/report/ar-aging-summary`:
```json
[]
```
```

The `ANALYST_SYSTEM_PROMPT` then instructs: *"If a figure the user asked for is not present in
DATA, say it is not available."* Claude complies and produces exactly the output the user
complained about — **"The DATA block is empty"**, *"N/A"*, *"data is not available"* — leaking the
internal prompt vocabulary to an end user.

**Reproduction:** any org/date-range combination with no rows, e.g. AR aging for a brand-new org.

---

### 2.2 — P0 · `results: None` from the SQL engine causes a hard HTTP 500

**Files:** `src/gemini_brain/sql_fallback/sql_engine.py:438`, `gemini_brain_runner.py:840`,
`src/gemini_brain/api/models.py` (`QueryResponse.results: List[Any]`)

`sql_engine.run()` initialises `last_results: Optional[List[Dict]] = None` and returns
`"results": last_results` verbatim. When the tool loop finishes without any handler ever setting
results (a very common no-data path), the value stays `None`.

The runner then does `er.get("results", [])` — which returns `None`, because the **key exists**
with a `None` value; the default is never applied. `QueryResponse(**result)` then raises:

```
ValidationError: 1 validation error for QueryResponse
results
  Input should be a valid list [type=list_type, input_value=None, input_type=NoneType]
```

That is caught by the blanket `except Exception` in `routes.py:186` and re-raised as:

```
HTTP 500  {"detail": "Query execution failed: 1 validation error for QueryResponse\nresults\n  Input should be a valid list..."}
```

The user sees a Pydantic stack-trace fragment. **Verified against pydantic 2.13.4.**

The same class of bug exists for `answer`: `QueryResponse.answer: str` rejects `None`, and §2.3
produces a `None` answer.

---

### 2.3 — P0 · `render_financial_statement` returns `None` for non-dict input

**File:** `src/gemini_brain/tools/formatters.py:110–125`

```python
def render_financial_statement(data: Any) -> str:
    if isinstance(data, dict):
        ...
        return "\n".join(lines)
def render_project_expense_rollup(data: Any) -> str:   # ← no blank line, no fallback return
```

There is **no `return` on the non-dict branch** and no blank line separating the next function.
`render("financial_statement", [...])` returns `None`. Verified:

```
render_financial_statement([{'a':1}])  ->  None
render('financial_statement', [{'a':1}])  ->  None
```

`financial_statement` is the formatter for `profit_loss`, `balance_sheet`, and `cash_flow` — three
of the most-used endpoints. When those endpoints return a **list** (an itemised statement rather
than a keyed summary), the streaming path emits `{"type": "data_table", "table": None}` and the
`narrate=False` path sets `answer = None` → §2.2 500.

---

### 2.4 — P0 · API error envelopes are decoded as successful data

**File:** `src/gemini_brain/api_client/accutax_client.py:159–184` (`extract_data`)

```python
if "success" in raw and len(raw) == 2:
    return next(v for k, v in raw.items() if k != "success")
```

Verified behaviour:

| Upstream body (HTTP 200) | `extract_data` returns |
|---|---|
| `{"success": false, "message": "no records"}` | `'no records'` ← **a bare string treated as data** |
| `{"success": true, "data": null}` | `None` → silently drops to SQL fallback |
| `{"success": true, "data": []}` | `[]` → §2.1 |

The `success: false` case is the worst: the runner sees `data = "no records"`, which is
`not None`, so it renders `render_row_table("no records")` and feeds the literal string
`"no records"` to Claude as the authoritative DATA block.

---

### 2.5 — P0 · Every error renders as "Security Isolation Boundary Notice"

**File:** `ui/src/components/ResponseView.jsx:107,124–133`

```jsx
const isError = Boolean(responseData?.error || (responseData?.answer && responseData.answer.startsWith('Error:')));
...
{isError ? (
  <h4 style={styles.errorTitle}>Security Isolation Boundary Notice</h4>
  <p style={styles.errorSub}>{responseData.error || responseData.answer}</p>
) : ...}
```

A Bedrock throttle, a DB timeout, a Gemini quota exhaustion, and a genuine tenant-isolation denial
all render with the **same red "Security Isolation Boundary Notice" banner** and the raw exception
string in monospace. Users are told they hit a security boundary when the real cause was a network
blip.

---

### 2.6 — P0 · SSE error chunks never terminate the turn → "No response generated"

**Files:** `src/gemini_brain/api/routes.py:216–222`, `ui/src/App.jsx:141–186`,
`ui/src/components/ResponseView.jsx:135–140`

`event_generator` on `ValueError` yields **only** `{"status": "...", "type": "error"}` and returns.
It never yields a `final_result`.

`App.jsx` has no `type === 'error'` branch; the chunk falls into the generic `chunk.status` branch,
which merely updates `latestStatus`. The stream then closes, `onComplete` flips
`isStreaming: false`, and `AssistantResponseCard` finds `content === ''` and renders the fallback:

> *No response generated. Please try again.*

So **a tenant-isolation denial, the single most important error in a multi-tenant product, is
displayed as "No response generated."**

---

### 2.7 — P0 · SQL fallback dies on a hard-coded absolute developer path

**File:** `src/gemini_brain/sql_fallback/sql_engine.py:32–63`

```python
host_path = r"C:\Users\acer\Desktop\query-parser-bedrock_clean\query-parser-bedrock_clean"
...
except ImportError as e:
    raise RuntimeError("Production coordinator_agent pipeline is required for SQL fallback engine.") from e
```

On any machine without that directory, **the entire DB fallback path throws**. In `run()` the
`RuntimeError` is caught and converted to `answer = "Error: DB fallback failed: Production
coordinator_agent pipeline is required for SQL fallback engine."` — which the UI then renders under
the security banner (§2.5). This is the single most likely production 'ugly error'.

---

### 2.8 — P1 · The rendered data table is computed, streamed, and then thrown away

**Files:** `gemini_brain_runner.py:1154`, `ui/src/App.jsx:141–186`,
`claude_reasoner.py` (`ANALYST_SYSTEM_PROMPT`)

The streaming path emits:

```python
yield {"status": "Rendering financial data", "type": "data_table", "table": formatted_table}
```

`App.jsx` has no handler for `chunk.table` / `type === 'data_table'`. Because the chunk also carries
a `status` key, it is swallowed by the `else if (chunk.status)` branch and the table is discarded.

Meanwhile the analyst prompt tells Claude:

> *"A table of this data is already displayed above your response. Do not reproduce it."*

**Result:** Claude deliberately omits the numbers, referring to a table that was never rendered.
The user gets a narration about a table they cannot see. This is a direct cause of the
"formatting is inconsistent" complaint.

Related: `results_payload` is returned in the JSON response on every path and is **never rendered
by the UI at all**.

---

### 2.9 — P1 · Bullets render inline because the model emits soft line breaks / Unicode bullets

**Files:** `ui/src/components/ResponseView.jsx` (`PacedMarkdownStream`), `ui/src/index.css`

Three compounding causes:

1. **Soft line breaks.** CommonMark collapses a single `\n` into a space. When the model returns
   `Revenue: AED 1,200\nExpenses: AED 900`, ReactMarkdown renders one paragraph:
   `Revenue: AED 1,200 Expenses: AED 900`. `remark-gfm` does not change this; only `remark-breaks`
   or pre-normalisation does.
2. **Unicode bullet characters.** Models frequently emit `•`, `●`, `‣`, or `–` as list markers.
   These are **not** markdown list syntax, so every "bullet" lands in one flowing paragraph —
   exactly the reported *"inline with the dot bullet points"* symptom.
3. **Missing blank line before a list/table.** A markdown table immediately after a paragraph line
   with no blank line is not recognised as a table and renders as pipe-soup.

Additionally `PacedMarkdownStream` renders `currentSlice || text` — while `revealedLength === 0` it
renders **the whole text**, then snaps back to a slice. And slicing mid-table (`| Name | Amo`)
feeds ReactMarkdown a syntactically broken table on every animation tick, causing visible
re-layout thrash.

CSS: the global `* { margin: 0; padding: 0 }` reset strips `ul`/`ol` padding; `.markdown-body ul`
sets only `margin-left`, so `list-style-position: outside` markers sit outside the content box and
are clipped by `markdownWrapper { overflowX: 'hidden' }`.

---

### 2.10 — P1 · `format_aed(None)` prints `AED 0.00`

**File:** `src/gemini_brain/tools/formatters.py:12–20`

```python
if val is None or val == "":
    return "AED 0.00"
```

A missing value is rendered as a **real zero balance**. In a financial product, "we don't have this
figure" and "this figure is zero" are materially different statements. This silently fabricates
data.

---

### 2.11 — P1 · Failure and empty results are cached for 5 minutes

**File:** `gemini_brain_runner.py:672,683` / `:1126,1138`

```python
data = extract_data(raw_data)
result_cache.set_sync(cache_key, data, ttl=300)
```

`[]`, `{}`, `None`, and the `'no records'` string from §2.4 are all cached with a 300 s TTL. A
transient upstream blip therefore poisons the answer for five minutes, and a retry by the user
returns the identical bad answer — making the product look deterministic-ly broken.

(Also: caching `None` is a no-op, since `get_sync` cannot distinguish a cached `None` from a miss.)

---

### 2.12 — P1 · No FastAPI exception handlers → raw 404/422/500 bodies

**File:** `src/gemini_brain/api/app.py`

`create_app()` registers CORS and the router, and nothing else. Consequences:

| Situation | Response today |
|---|---|
| Unknown path (`/api/v1/quer`) | `404 {"detail":"Not Found"}` |
| Wrong method | `405 {"detail":"Method Not Allowed"}` |
| Malformed body | `422` with a Pydantic error array |
| Any unhandled exception | Starlette default `500 Internal Server Error` (plain text) |
| Unhandled exception during SSE | Connection drops mid-stream; UI spins forever |

None of these carry a `code`, a correlation id, or a user-safe message, and the CORS middleware
does not attach headers to responses generated by the default error handlers — so the browser sees
an opaque CORS failure instead of the status code.

---

### 2.13 — P1 · Blanket `except Exception` → 500 with the raw exception string

**File:** `src/gemini_brain/api/routes.py:180–190`

```python
except Exception as e:
    raise HTTPException(500, detail=f"Query execution failed: {str(e)}")
```

`str(e)` for a `psycopg2.OperationalError` contains **host, port, database name, and user**. For a
`botocore` error it contains the model ARN and region. This is both an ugly UX and an information
disclosure.

---

### 2.14 — P1 · Streaming token counts are fabricated

**File:** `gemini_brain_runner.py:1017–1018`

```python
ai = 150
ao = max(1, len(answer) // 4)
```

The Gemini streaming path hard-codes input tokens as `150` and estimates output. The UI then
displays these as authoritative "N tokens" and a `$0.00xxx` cost. Not a crash, but the metrics
strip is presenting invented numbers as facts.

---

### 2.15 — P2 · Miscellaneous

| # | File | Issue |
|---|---|---|
| a | `sql_engine.py:31` | `-> Tuple[...]` but `Tuple` is never imported. Survives only because of `from __future__ import annotations`. Any future `typing.get_type_hints()` call breaks. |
| b | `ui/vite.config.js:11` | Proxy targets `http://localhost:8001`; `server.py` defaults to port **8000** (`settings.api_port`), and `.env` sets no `API_PORT`. Mismatch → Vite returns a 500 proxy error page that the UI surfaces as an unparseable error. |
| c | `auth.py:246–252` | `get_user_by_email` returns a synthetic user with password `"TestPass123!"` for **any** unknown email when the DB is unreachable → authentication bypass. Out of scope here, but must be tracked separately. |
| d | `bedrock_client.py:44–61` | `retry_with_backoff` retries only `ThrottlingException`; `ModelTimeoutException`, `ServiceUnavailableException`, and `ModelNotReadyException` fail on the first attempt. |
| e | `accutax_client.py` | Single 6 s timeout, no retry on 502/503/504 and no jitter. One flaky upstream response = full fallback to SQL. |
| f | `db_connection.py:33` | `connect_timeout=3` but **no `statement_timeout`** on the general connection (only inside `execute_sql_function`). A slow ad-hoc query can hang for the full 90 s engine budget. |

---

## 3. Symptom → root-cause matrix

| What the user sees | Root cause |
|---|---|
| "The DATA block is empty" | §2.1 |
| "Not available", "N/A", "I don't have that figure" | §2.1, §2.4 |
| `Error: Query execution failed: 1 validation error for QueryResponse…` | §2.2 |
| `Error: DB fallback failed: Production coordinator_agent pipeline is required…` | §2.7 |
| Red **"Security Isolation Boundary Notice"** for a network error | §2.5 |
| "No response generated. Please try again." | §2.6 |
| Narration that references a table which isn't shown | §2.8 |
| Bullets running together on one line | §2.9 |
| `AED 0.00` where the real answer is "unknown" | §2.10 |
| Same wrong answer on retry, for ~5 minutes | §2.11 |
| `{"detail":"Not Found"}` / blank 500 page | §2.12 |
| Infinite spinner | §2.6, §2.12 |

---

# PART B — PRESCRIPTION

## 4. Design principles

1. **Classify, never guess.** Every data retrieval returns an explicit outcome, not a nullable
   payload. `None` is banned as a signal.
2. **The user never reads an exception.** Exception text goes to logs and to an
   operator-only `diagnostics` field. The user reads copy from a curated copy deck (§11).
3. **Empty is a first-class, *successful* answer.** "There are no overdue invoices" is good news
   stated confidently — not an error, not "N/A", not "not available".
4. **Never let an LLM narrate an empty or untrusted payload.** If there are no rows, we answer
   deterministically and skip the model entirely. This is faster *and* safer.
5. **Every request terminates in a rendered turn.** For SSE: exactly one `final_result` event on
   every path, including errors. No silent stream closes.
6. **Formatting is normalised at the boundary, not hoped for.** Markdown from the model is passed
   through a normaliser on the server *and* defensively on the client.
7. **Degrade in tiers, announce the tier.** Live API → cache → SQL → deterministic table →
   honest explanation. The response says which tier answered.

---

## 5. Target contracts

### 5.1 Retrieval outcome

```
OK          — payload present, ≥1 row / non-empty object.  Narrate.
EMPTY       — source reached successfully, zero rows.       Deterministic answer, no LLM.
PARTIAL     — payload present but truncated or partial.     Narrate + disclose.
UNAVAILABLE — source not reachable (timeout, 5xx, DNS).     Try next tier.
DENIED      — auth/tenant rejection (401/403).              Stop, explain.
INVALID     — reached, but body is unusable / error envelope.Try next tier.
```

### 5.2 Response envelope (additive to `QueryResponse`)

```jsonc
{
  "answer": "…",                    // always a non-empty string. never null.
  "results": [],                    // always a list. never null.
  "sql": null,
  "error": null,                    // null unless the turn genuinely failed
  "status": "ok",                   // ok | empty | partial | degraded | failed
  "notice": {                       // null when status == "ok"
    "kind": "empty",                // empty | degraded | denied | failed | partial
    "code": "NO_ROWS_FOR_PERIOD",
    "title": "No records in this period",
    "message": "…user-safe copy…",
    "suggestions": ["Try a wider date range", "Check a different organization"],
    "retryable": true
  },
  "data_source": {                  // provenance — drives the UI badge
    "tier": "live_api",             // live_api | cache | sql_function | sql_fallback | model_only
    "endpoint": "/report/ar-aging-summary",
    "as_of": "2026-08-20T09:41:02Z",
    "row_count": 0,
    "truncated": false
  },
  "table_markdown": null,           // pre-rendered deterministic table, or null
  "request_id": "b3f1…",            // correlation id, echoed in logs and headers
  "token_usage": { … },             // unchanged
  "agent_trace": [ … ],             // unchanged
  "routing_info": { … },            // unchanged
  "query_trace": { … }              // unchanged
}
```

`answer`, `results`, `token_usage` keep their existing types and are **never** null. Everything
else is additive and optional, so existing consumers keep working.

### 5.3 SSE event contract

Every event is `data: <json>\n\n`. Exactly one `final_result` per stream, always last.

| `type` | Payload keys | Meaning |
|---|---|---|
| `status` | `status` | Progress text for the loader |
| `token` | `token`, `status?` | Narration delta |
| `data_table` | `table` (markdown), `row_count` | Deterministic table, render immediately |
| `notice` | `notice` (§5.2 object) | Non-fatal condition (empty, degraded) |
| `error` | `notice`, `request_id` | Fatal for this turn; `final_result` still follows |
| `final_result` | full envelope (§5.2) | Terminal event |

### 5.4 Error taxonomy and HTTP mapping

| Code | HTTP | Retryable | User-facing kind |
|---|---|---|---|
| `AUTH_REQUIRED` | 401 | no | denied |
| `AUTH_EXPIRED` | 401 | no (re-login) | denied |
| `TENANT_FORBIDDEN` | 403 | no | denied |
| `TENANT_AMBIGUOUS` | 400 | no | denied |
| `VALIDATION_FAILED` | 400 | no | failed |
| `NOT_FOUND` | 404 | no | failed |
| `UPSTREAM_TIMEOUT` | 200 (degraded) | yes | degraded |
| `UPSTREAM_UNAVAILABLE` | 200 (degraded) | yes | degraded |
| `MODEL_UNAVAILABLE` | 200 (degraded) | yes | degraded |
| `MODEL_RATE_LIMITED` | 200 (degraded) | yes | degraded |
| `DB_UNAVAILABLE` | 200 (degraded) | yes | degraded |
| `QUERY_FAILED` | 200 (degraded) | yes | degraded |
| `NO_ROWS` | 200 | n/a | empty |
| `INTERNAL_ERROR` | 500 | yes | failed |

> **Key decision:** retrieval failures return **HTTP 200 with `status: "degraded"`**, not 5xx. The
> orchestrator *succeeded* — it produced the best available answer and disclosed the limitation.
> 5xx is reserved for the orchestrator itself failing. This is what removes the "404-like" raw
> errors from the UI.

---

## 6. Phase 0 — Foundations (no behaviour change)

**Goal:** create the shared vocabulary. Nothing calls these yet. Zero risk.

### 6.1 New package `src/gemini_brain/resilience/`

**`src/gemini_brain/resilience/__init__.py`**

```python
"""resilience — outcome classification, user-safe copy, and response envelopes."""
from gemini_brain.resilience.outcomes import Outcome, Retrieved, classify_payload
from gemini_brain.resilience.errors import ErrorCode, AppError, classify_exception
from gemini_brain.resilience.messages import notice_for, NOTICES
from gemini_brain.resilience.envelope import (
    build_notice, build_success, build_empty, build_degraded, normalize_envelope,
)

__all__ = [
    "Outcome", "Retrieved", "classify_payload",
    "ErrorCode", "AppError", "classify_exception",
    "notice_for", "NOTICES",
    "build_notice", "build_success", "build_empty", "build_degraded", "normalize_envelope",
]
```

**`src/gemini_brain/resilience/outcomes.py`**

```python
"""outcomes.py — Explicit retrieval outcome classification.

Replaces the ambiguous `data is not None` check with a three-axis classification:
reachability, emptiness, and payload trust.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Outcome(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    DENIED = "denied"
    INVALID = "invalid"


#: Keys commonly used by upstream envelopes to wrap the real row list.
LIST_WRAPPER_KEYS = (
    "items", "results", "data", "rows", "records",
    "invoices", "bills", "transactions", "contacts", "accounts", "entries",
)

#: Keys that are pure metadata — an object containing only these is not real data.
METADATA_ONLY_KEYS = frozenset({
    "success", "status", "message", "code", "error", "errors",
    "total", "count", "page", "page_size", "limit", "offset",
    "timestamp", "request_id",
})


@dataclass
class Retrieved:
    """The result of one retrieval attempt against one tier."""
    outcome: Outcome
    payload: Any = None
    rows: list = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    tier: str = ""                       # live_api | cache | sql_function | sql_fallback
    endpoint: str = ""
    reason: str = ""                     # short machine-ish reason, e.g. "http_503"
    detail: str = ""                     # operator-only detail; never shown to users
    http_status: Optional[int] = None

    @property
    def usable(self) -> bool:
        """True when the payload can be narrated (has at least one row/value)."""
        return self.outcome in (Outcome.OK, Outcome.PARTIAL)

    @property
    def reached_source(self) -> bool:
        """True when the source answered, even if with zero rows."""
        return self.outcome in (Outcome.OK, Outcome.PARTIAL, Outcome.EMPTY)

    def to_data_source(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "endpoint": self.endpoint,
            "row_count": self.row_count,
            "truncated": self.truncated,
        }


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _all_values_blank(d: Dict[str, Any]) -> bool:
    """A summary object of all zeros/nulls is 'empty' for reporting purposes."""
    if not d:
        return True
    for v in d.values():
        if isinstance(v, (list, dict)):
            if v:
                return False
        elif isinstance(v, bool):
            return False
        elif isinstance(v, (int, float)):
            if v != 0:
                return False
        elif not _is_blank(v):
            return False
    return True


def classify_payload(
    payload: Any,
    *,
    tier: str = "",
    endpoint: str = "",
    truncated: bool = False,
) -> Retrieved:
    """Classify a decoded payload into an Outcome. Never raises.

    Rules, in order:
      1. None / blank string          -> INVALID  (source gave us nothing usable)
      2. Explicit failure envelope    -> INVALID
      3. list                         -> EMPTY if len == 0 else OK
      4. dict with a wrapper key      -> recurse on the wrapped list
      5. dict of metadata only        -> EMPTY
      6. dict all-zero / all-null     -> EMPTY
      7. dict                         -> OK (single-object summary)
      8. scalar                       -> OK
    """
    if payload is None or (isinstance(payload, str) and not payload.strip()):
        return Retrieved(Outcome.INVALID, tier=tier, endpoint=endpoint, reason="null_payload")

    if isinstance(payload, dict):
        # 2. explicit failure envelope
        if payload.get("success") is False or payload.get("status") in ("error", "failed"):
            return Retrieved(
                Outcome.INVALID, payload=payload, tier=tier, endpoint=endpoint,
                reason="upstream_error_envelope",
                detail=str(payload.get("message") or payload.get("error") or "")[:300],
            )
        if "error" in payload and payload.get("error"):
            return Retrieved(
                Outcome.INVALID, payload=payload, tier=tier, endpoint=endpoint,
                reason="upstream_error_field", detail=str(payload["error"])[:300],
            )
        # 4. wrapper key
        for key in LIST_WRAPPER_KEYS:
            inner = payload.get(key)
            if isinstance(inner, list):
                inner_res = classify_payload(inner, tier=tier, endpoint=endpoint, truncated=truncated)
                inner_res.payload = payload          # keep the full envelope for the formatter
                return inner_res
        # 5. metadata only
        if set(payload.keys()) <= METADATA_ONLY_KEYS:
            return Retrieved(Outcome.EMPTY, payload=payload, tier=tier, endpoint=endpoint,
                             reason="metadata_only")
        # 6. all zero / all null
        if _all_values_blank(payload):
            return Retrieved(Outcome.EMPTY, payload=payload, tier=tier, endpoint=endpoint,
                             reason="all_values_zero_or_null")
        # 7. single-object summary (e.g. {"net_profit": 15000, "revenue": 90000})
        return Retrieved(
            Outcome.PARTIAL if truncated else Outcome.OK,
            payload=payload, rows=[payload], row_count=1,
            tier=tier, endpoint=endpoint, truncated=truncated,
        )

    if isinstance(payload, list):
        # 3.
        if len(payload) == 0:
            return Retrieved(Outcome.EMPTY, payload=payload, tier=tier, endpoint=endpoint,
                             reason="zero_rows")
        return Retrieved(
            Outcome.PARTIAL if truncated else Outcome.OK,
            payload=payload, rows=payload, row_count=len(payload),
            tier=tier, endpoint=endpoint, truncated=truncated,
        )

    # 8. scalar (int/float/bool/str)
    return Retrieved(Outcome.OK, payload=payload, rows=[payload], row_count=1,
                     tier=tier, endpoint=endpoint, truncated=truncated)
```

**`src/gemini_brain/resilience/errors.py`**

```python
"""errors.py — Error taxonomy and exception → code classification."""
from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    TENANT_FORBIDDEN = "TENANT_FORBIDDEN"
    TENANT_AMBIGUOUS = "TENANT_AMBIGUOUS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    QUERY_FAILED = "QUERY_FAILED"
    NO_ROWS = "NO_ROWS"
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_FOR_CODE = {
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.AUTH_EXPIRED: 401,
    ErrorCode.TENANT_FORBIDDEN: 403,
    ErrorCode.TENANT_AMBIGUOUS: 400,
    ErrorCode.VALIDATION_FAILED: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTERNAL_ERROR: 500,
}
#: Everything not listed above is a *degraded success* — HTTP 200.
DEGRADED_HTTP = 200

RETRYABLE = frozenset({
    ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE,
    ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_RATE_LIMITED,
    ErrorCode.DB_UNAVAILABLE, ErrorCode.QUERY_FAILED, ErrorCode.INTERNAL_ERROR,
})


class AppError(Exception):
    """Carries a user-safe code plus operator-only detail."""

    def __init__(self, code: ErrorCode, detail: str = "", *, cause: Optional[BaseException] = None):
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail
        self.cause = cause

    @property
    def http_status(self) -> int:
        return HTTP_FOR_CODE.get(self.code, DEGRADED_HTTP)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE


def classify_exception(exc: BaseException) -> ErrorCode:
    """Map an arbitrary exception to an ErrorCode. Never raises."""
    if isinstance(exc, AppError):
        return exc.code

    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if "timeout" in name or "timeout" in text or "timed out" in text:
        return ErrorCode.UPSTREAM_TIMEOUT
    if "throttl" in text or "429" in text or "rate limit" in text or "quota" in text or "exhausted" in text:
        return ErrorCode.MODEL_RATE_LIMITED
    if "accessdenied" in name or "403" in text or "unauthorized" in text or "forbidden" in text:
        return ErrorCode.TENANT_FORBIDDEN
    if "operationalerror" in name or "psycopg2" in text or "could not connect" in text:
        return ErrorCode.DB_UNAVAILABLE
    if "programmingerror" in name or "undefinedtable" in name or "syntax error" in text:
        return ErrorCode.QUERY_FAILED
    if "botocore" in text or "bedrock" in text or "clienterror" in name:
        return ErrorCode.MODEL_UNAVAILABLE
    if "connect" in text or "503" in text or "502" in text or "504" in text:
        return ErrorCode.UPSTREAM_UNAVAILABLE
    if "validation" in name or "pydantic" in text:
        return ErrorCode.VALIDATION_FAILED
    return ErrorCode.INTERNAL_ERROR
```

**`src/gemini_brain/resilience/messages.py`** — the single source of truth for user-facing copy.
See §11 for the full deck. Skeleton:

```python
"""messages.py — Curated, user-safe copy. NOTHING else may write user-facing error text."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from gemini_brain.resilience.errors import ErrorCode, RETRYABLE

NOTICES: Dict[str, Dict[str, Any]] = {
    "NO_ROWS": {
        "kind": "empty",
        "title": "Nothing recorded for this request",
        "message": (
            "I checked {subject} and there are no matching records yet. "
            "That is a confirmed result from your books, not a system problem."
        ),
        "suggestions": [
            "Widen the date range and ask again",
            "Confirm the records were posted to this organization",
        ],
    },
    "UPSTREAM_TIMEOUT": {
        "kind": "degraded",
        "title": "The finance service took too long to respond",
        "message": (
            "I could not retrieve live figures for {subject} in time, so I have not "
            "shown any numbers rather than showing you something unverified."
        ),
        "suggestions": ["Try again in a moment", "Narrow the date range to reduce the load"],
    },
    # … full deck in §11 …
}

_FALLBACK = {
    "kind": "failed",
    "title": "I could not complete that request",
    "message": "Something went wrong on our side. No figures were produced for this question.",
    "suggestions": ["Try again", "Rephrase the question"],
}


def notice_for(
    code: "ErrorCode | str",
    *,
    subject: str = "your records",
    request_id: str = "",
    suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a user-safe notice object. Never raises, never leaks exception text."""
    key = code.value if hasattr(code, "value") else str(code)
    tpl = NOTICES.get(key, _FALLBACK)
    try:
        message = tpl["message"].format(subject=subject)
    except Exception:
        message = tpl["message"]
    return {
        "kind": tpl["kind"],
        "code": key,
        "title": tpl["title"],
        "message": message,
        "suggestions": suggestions if suggestions is not None else list(tpl.get("suggestions", [])),
        "retryable": key in {c.value for c in RETRYABLE},
        "request_id": request_id,
    }
```

**`src/gemini_brain/resilience/envelope.py`**

```python
"""envelope.py — Guarantees the response shape. Nothing null that must not be null."""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

_EMPTY_USAGE = {
    "input_tokens": 0, "output_tokens": 0, "llm_calls": 0,
    "cost_usd": 0.0, "elapsed_seconds": 0.0,
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def normalize_envelope(result: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce any runner result into a shape QueryResponse can always validate.

    This is the last line of defence: call it immediately before QueryResponse(**result).
    It must never raise.
    """
    out = dict(result or {})

    answer = out.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        notice = out.get("notice") or {}
        answer = (notice.get("message")
                  or "I could not produce an answer for that request.")
    out["answer"] = answer

    results = out.get("results")
    if not isinstance(results, list):
        results = [] if results in (None, "", {}) else [results]
    out["results"] = results

    usage = out.get("token_usage")
    if not isinstance(usage, dict):
        usage = dict(_EMPTY_USAGE)
    for k, v in _EMPTY_USAGE.items():
        usage.setdefault(k, v)
        if usage[k] is None:
            usage[k] = v
    out["token_usage"] = usage

    if not isinstance(out.get("agent_trace"), list):
        out["agent_trace"] = []
    if not isinstance(out.get("pii_redactions"), dict):
        out["pii_redactions"] = {}
    out["pii_redacted"] = bool(out.get("pii_redacted", False))
    out.setdefault("status", "failed" if out.get("error") else "ok")
    out.setdefault("notice", None)
    out.setdefault("data_source", None)
    out.setdefault("table_markdown", None)
    out.setdefault("request_id", new_request_id())
    out.setdefault("sql", None)
    out.setdefault("error", None)
    return out
```

Add matching optional fields to `QueryResponse` in `src/gemini_brain/api/models.py`:

```python
class NoticeSchema(BaseModel):
    kind: str
    code: str
    title: str
    message: str
    suggestions: List[str] = Field(default_factory=list)
    retryable: bool = False
    request_id: str = ""


class DataSourceSchema(BaseModel):
    tier: str = ""
    endpoint: Optional[str] = None
    row_count: int = 0
    truncated: bool = False
    as_of: Optional[str] = None


# inside QueryResponse — ADD these, change nothing existing:
    status: str = Field(default="ok", description="ok | empty | partial | degraded | failed")
    notice: Optional[NoticeSchema] = Field(default=None)
    data_source: Optional[DataSourceSchema] = Field(default=None)
    table_markdown: Optional[str] = Field(default=None)
    request_id: str = Field(default="")
```

### 6.2 Acceptance for Phase 0

```python
# tests/unit/test_resilience_outcomes.py
import pytest
from gemini_brain.resilience import Outcome, classify_payload

@pytest.mark.parametrize("payload,expected", [
    (None,                                   Outcome.INVALID),
    ("",                                     Outcome.INVALID),
    ({"success": False, "message": "nope"},  Outcome.INVALID),
    ({"error": "boom"},                      Outcome.INVALID),
    ([],                                     Outcome.EMPTY),
    ({"items": []},                          Outcome.EMPTY),
    ({"success": True, "total": 0},          Outcome.EMPTY),
    ({"revenue": 0, "expenses": 0},          Outcome.EMPTY),
    ([{"id": 1}],                            Outcome.OK),
    ({"items": [{"id": 1}]},                 Outcome.OK),
    ({"net_profit": 15000},                  Outcome.OK),
    (42,                                     Outcome.OK),
])
def test_classify(payload, expected):
    assert classify_payload(payload).outcome is expected
```

`pytest tests/unit -q` green. Application behaviour unchanged (nothing imports these yet).

---

## 7. Phase 1 — Harden the retrieval boundary

**Files:** `src/gemini_brain/api_client/accutax_client.py`, `src/gemini_brain/sql_fallback/db_connection.py`
**Do not touch:** the runner (Phase 2 does that).

### 7.1 Step 1.1 — Make `call_api` return a structured outcome

Add a new function; keep `call_api` as a thin compatibility shim so nothing breaks.

```python
# accutax_client.py — ADD

import random
from gemini_brain.resilience.outcomes import Outcome, Retrieved

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


def call_api_resilient(
    endpoint: str,
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
    *,
    base_url: str = "",
    auth_token: str = "",
    timeout: float = 6.0,
    attempts: int = _MAX_ATTEMPTS,
) -> Retrieved:
    """GET with bounded retry + jitter, returning an explicit Retrieved outcome.

    Never raises. Never returns None.
    """
    client = get_sync_client(base_url, auth_token)
    url_path = _format_url_path(endpoint, path_params)
    clean_params = {k: v for k, v in query_params.items() if v is not None}

    headers = {}
    token = auth_token or settings.accutax_auth_token
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_reason, last_detail, last_status = "unknown", "", None

    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url_path, params=clean_params, headers=headers, timeout=timeout)
            last_status = resp.status_code

            if resp.status_code in (401, 403):
                return Retrieved(Outcome.DENIED, tier="live_api", endpoint=endpoint,
                                 reason=f"http_{resp.status_code}", http_status=resp.status_code,
                                 detail=resp.text[:300])

            if resp.status_code == 404:
                # Upstream says: this resource does not exist for this tenant.
                # That is EMPTY, not a crash.
                return Retrieved(Outcome.EMPTY, tier="live_api", endpoint=endpoint,
                                 reason="http_404", http_status=404)

            if resp.status_code in _RETRY_STATUS and attempt < attempts:
                delay = min(0.4 * (2 ** (attempt - 1)), 2.0) + random.uniform(0, 0.2)
                logger.warning("API %s returned %s — retry %d/%d in %.2fs",
                               url_path, resp.status_code, attempt, attempts, delay)
                time.sleep(delay)
                last_reason = f"http_{resp.status_code}"
                continue

            if resp.status_code != 200:
                return Retrieved(Outcome.UNAVAILABLE, tier="live_api", endpoint=endpoint,
                                 reason=f"http_{resp.status_code}", http_status=resp.status_code,
                                 detail=resp.text[:300])

            try:
                raw = resp.json()
            except Exception:
                body = resp.text
                if not body or not body.strip():
                    return Retrieved(Outcome.EMPTY, tier="live_api", endpoint=endpoint,
                                     reason="empty_body", http_status=200)
                return Retrieved(Outcome.INVALID, tier="live_api", endpoint=endpoint,
                                 reason="non_json_body", http_status=200, detail=body[:300])

            payload, envelope_note = extract_data_safe(raw)
            res = classify_payload(payload, tier="live_api", endpoint=endpoint)
            res.http_status = 200
            if envelope_note:
                res.detail = envelope_note
            return res

        except httpx.TimeoutException:
            last_reason, last_detail = "timeout", f"exceeded {timeout}s"
            if attempt < attempts:
                time.sleep(0.3 * attempt)
                continue
            return Retrieved(Outcome.UNAVAILABLE, tier="live_api", endpoint=endpoint,
                             reason="timeout", detail=last_detail)
        except Exception as e:
            last_reason, last_detail = "transport_error", str(e)[:300]
            if attempt < attempts:
                time.sleep(0.3 * attempt)
                continue
            return Retrieved(Outcome.UNAVAILABLE, tier="live_api", endpoint=endpoint,
                             reason="transport_error", detail=last_detail)

    return Retrieved(Outcome.UNAVAILABLE, tier="live_api", endpoint=endpoint,
                     reason=last_reason, detail=last_detail, http_status=last_status)
```

### 7.2 Step 1.2 — Replace `extract_data` with `extract_data_safe`

The `success: false` bug (§2.4) is fixed here.

```python
def extract_data_safe(raw: Any) -> Tuple[Any, str]:
    """Unwrap Accutax envelopes. Returns (payload, note).

    Unlike the old extract_data, an explicit `success: false` envelope is
    returned AS the envelope so classify_payload can mark it INVALID —
    it is never mistaken for the data itself.
    """
    if not isinstance(raw, dict):
        return raw, ""

    if raw.get("success") is False:
        return raw, "upstream_success_false"

    if "data" in raw:
        return raw["data"], ""
    if "results" in raw:
        return raw["results"], ""

    if "success" in raw and len(raw) == 2:
        for k, v in raw.items():
            if k != "success":
                # Only unwrap containers. A bare string/number beside `success`
                # is a message, not a dataset.
                if isinstance(v, (list, dict)):
                    return v, ""
                return raw, "scalar_beside_success"
    return raw, ""


def extract_data(raw: Any) -> Any:
    """DEPRECATED — kept for backwards compatibility. Use extract_data_safe."""
    payload, _ = extract_data_safe(raw)
    return payload
```

> `tools/handlers.py` imports `extract_data`; leaving the shim means that file needs no change.

### 7.3 Step 1.3 — SQL function calls return outcomes, not exceptions

```python
# db_connection.py — ADD (keep execute_sql_function untouched for existing callers)

def execute_sql_function_safe(
    func_name: str, params: tuple, org_id: int, db_name: str = "",
) -> "Retrieved":
    from gemini_brain.resilience.outcomes import Outcome, Retrieved, classify_payload
    from gemini_brain.resilience.errors import classify_exception, ErrorCode
    try:
        rows = execute_sql_function(func_name, params, org_id, db_name=db_name)
    except Exception as e:
        code = classify_exception(e)
        outcome = Outcome.UNAVAILABLE if code == ErrorCode.DB_UNAVAILABLE else Outcome.INVALID
        logger.warning("SQL function %s failed (%s): %s", func_name, code.value, e)
        return Retrieved(outcome, tier="sql_function", endpoint=func_name,
                         reason=code.value.lower(), detail=str(e)[:300])
    return classify_payload(rows, tier="sql_function", endpoint=func_name)
```

Also add a default statement timeout to `get_connection` (fixes §2.15f):

```python
    conn = psycopg2.connect(
        host=settings.db_host, port=settings.db_port, dbname=resolved,
        user=settings.db_user, password=settings.db_password,
        connect_timeout=3,
        options="-c statement_timeout=20000",   # matches constants.SQL_TIMEOUT_MS
    )
    return conn
```

### 7.4 Acceptance for Phase 1

```python
# tests/unit/test_api_client_resilience.py
from gemini_brain.api_client.accutax_client import extract_data_safe

def test_success_false_is_not_unwrapped():
    payload, note = extract_data_safe({"success": False, "message": "no records"})
    assert payload == {"success": False, "message": "no records"}
    assert note == "upstream_success_false"

def test_data_envelope_unwrapped():
    assert extract_data_safe({"success": True, "data": [{"id": 1}]})[0] == [{"id": 1}]
```

Plus a `respx`/monkeypatched-client test for 200-empty, 404, 503-retry, and timeout. Behaviour of
the running app is still unchanged — nothing calls `call_api_resilient` yet.

---

## 8. Phase 2 — Rewire the orchestrator around outcomes

**File:** `src/gemini_brain/orchestrator/gemini_brain_runner.py` (the only file in this phase)
**Do not touch:** `_enforce_tenant_isolation`, `redact_pii` call, `fast_route`, `classify_intent`,
`select_endpoint`.

### 8.1 Step 2.1 — Extract retrieval into one helper used by both `run` and `run_stream`

Today the retrieval block is duplicated at `:661–686` and `:1105–1144`. Replace both with:

```python
    def _retrieve(
        self,
        sel: Dict[str, Any],
        organization_id: int,
        db_name: str,
        trace: Any,
    ) -> "Retrieved":
        """Single retrieval attempt: cache → sql function → live API. Never raises."""
        from gemini_brain.resilience.outcomes import Outcome, Retrieved, classify_payload
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

        if endpoint.startswith("fn_"):
            with trace.stage("sql_function_call", endpoint=endpoint):
                qp = sel.get("query_params", {}) or {}
                res = execute_sql_function_safe(
                    endpoint,
                    (organization_id, qp.get("start_date", "2020-01-01"), qp.get("end_date", "2099-12-31")),
                    organization_id,
                    db_name=db_name,
                )
        else:
            with trace.stage("api_call", endpoint=endpoint):
                res = call_api_resilient(endpoint, sel.get("path_params", {}), sel.get("query_params", {}))

        # ── Cache policy: ONLY cache genuinely usable payloads (fixes §2.11) ──
        if res.outcome is Outcome.OK and res.payload is not None:
            result_cache.set_sync(cache_key, res.payload, ttl=300)
        elif res.outcome is Outcome.EMPTY:
            result_cache.set_sync(cache_key, res.payload if res.payload is not None else [], ttl=30)
        else:
            METRICS.api_call_failed.inc()
            logger.warning("Retrieval %s → %s (%s) %s",
                           endpoint, res.outcome.value, res.reason, res.detail[:120])
        return res
```

**Cache policy rationale:** `OK` → 300 s (unchanged). `EMPTY` → 30 s only, so a user who just
posted an invoice sees it within half a minute. `UNAVAILABLE`/`INVALID`/`DENIED` → never cached.

### 8.2 Step 2.2 — Replace `if data is not None:` with outcome branching

In **both** `run()` (around `:688`) and `run_stream()` (around `:1147`), the shape becomes:

```python
        retrieved = self._retrieve(sel, organization_id, db_name, trace) if (use_api and sel) \
                    else Retrieved(Outcome.INVALID, reason="api_disabled_or_no_selection")

        endpoint = retrieved.endpoint or None
        tool_spec = next((s for s in REGISTRY.values() if s.endpoint == endpoint), None)
        subject = _subject_for(endpoint, tool_spec, query)   # see 8.4

        # ── A. Usable data → narrate (current happy path, unchanged) ──────────
        if retrieved.usable:
            ...existing narration code, but pass retrieved.payload instead of `data`...
            if retrieved.outcome is Outcome.PARTIAL:
                status = "partial"
                notice = notice_for("PARTIAL_DATA", subject=subject)
            else:
                status = "ok"
                notice = None

        # ── B. Source reached, zero rows → DETERMINISTIC answer, NO LLM ───────
        elif retrieved.outcome is Outcome.EMPTY:
            return self._empty_result(
                query=query, subject=subject, retrieved=retrieved, tool_spec=tool_spec,
                qtype=qtype, type_lbl=type_lbl, reason=reason, trace=trace, t0=t0,
                gi=gi, go=go, llm_calls=llm_calls,
                is_redacted=is_redacted, redaction_counts=redaction_counts,
                session_id=session_id, user_id=user_id, raw_query=raw_query,
            )

        # ── C. Tenant/auth rejection from upstream → stop, do not fall through ─
        elif retrieved.outcome is Outcome.DENIED:
            return self._degraded_result(ErrorCode.TENANT_FORBIDDEN, subject, retrieved, ...)

        # ── D. UNAVAILABLE / INVALID → try the SQL fallback tier ──────────────
        else:
            ...existing SQL fallback block, but wrapped per 8.3...
```

**Critical:** case **B must not fall through to the SQL fallback.** Today an empty API result
(`[]`) already skips the fallback (because `[] is not None`), and an API *failure* falls through.
The new code preserves that split correctly and explicitly: EMPTY is a *successful* answer,
UNAVAILABLE/INVALID try the next tier.

### 8.3 Step 2.3 — The two new terminal builders

```python
    def _empty_result(self, *, query, subject, retrieved, tool_spec, qtype, type_lbl,
                      reason, trace, t0, gi, go, llm_calls, is_redacted, redaction_counts,
                      session_id, user_id, raw_query) -> Dict[str, Any]:
        """Zero rows is a confirmed answer, not a failure. No LLM call. Sub-second."""
        from gemini_brain.resilience import notice_for
        from gemini_brain.resilience.envelope import new_request_id

        notice = notice_for("NO_ROWS", subject=subject)
        answer = build_empty_answer(query, subject, retrieved)   # formatting/empty_answer.py, §9.3
        trace_summary = trace.emit()

        if session_id:
            with trace.stage("memory_write"):
                save_message_by_session(session_id, "user", raw_query)
                save_message_by_session(session_id, "assistant", answer)

        return {
            "answer": answer,
            "sql": None,
            "results": [],
            "error": None,
            "status": "empty",
            "notice": notice,
            "data_source": retrieved.to_data_source(),
            "table_markdown": None,
            "request_id": new_request_id(),
            "pii_redacted": is_redacted,
            "pii_redactions": redaction_counts,
            "token_usage": {
                "input_tokens": gi, "output_tokens": go, "llm_calls": llm_calls,
                "cost_usd": self._cost(gi, go, 0, 0, ""),
                "elapsed_seconds": round(time.time() - t0, 2),
            },
            "agent_trace": [
                {"step": "gemini_router", "type": qtype, "type_label": type_lbl,
                 "path": "api_then_anthropic", "reason": reason},
                {"step": "rest_api_call", "endpoint": retrieved.endpoint,
                 "status": "empty", "row_count": 0},
                {"step": "empty_result_handler", "status": "deterministic_answer"},
            ],
            "routing_info": {"type": qtype, "type_label": type_lbl,
                             "path": "api_then_anthropic", "api_endpoint": retrieved.endpoint,
                             "reason": reason},
            "query_trace": trace_summary,
        }
```

`_degraded_result` is the same shape with `status: "degraded"`, `notice = notice_for(code, …)`,
`error = notice["code"]` (a **code**, never a stack trace), and `answer = notice["message"]`.

### 8.4 Step 2.4 — Replace `_err`

`_err` (`:165`) currently produces `answer = f"Error: {msg}"` with the raw exception inside. Replace
its body (keep the name and signature so callers do not change):

```python
    def _err(self, msg: str, t0: float, gi: int = 0, go: int = 0,
             code: "ErrorCode | None" = None, subject: str = "your records") -> Dict[str, Any]:
        """Build a user-safe failure envelope. `msg` goes to logs only."""
        from gemini_brain.resilience import ErrorCode, notice_for
        from gemini_brain.resilience.envelope import new_request_id

        rid = new_request_id()
        code = code or ErrorCode.INTERNAL_ERROR
        logger.error("[%s] runner failure (%s): %s", rid, code.value, msg)
        notice = notice_for(code, subject=subject, request_id=rid)
        return {
            "answer": notice["message"],
            "sql": None,
            "results": [],
            "error": code.value,          # ← a code, not a stack trace
            "status": "degraded" if notice["retryable"] else "failed",
            "notice": notice,
            "data_source": None,
            "table_markdown": None,
            "request_id": rid,
            "token_usage": {"input_tokens": gi, "output_tokens": go, "llm_calls": 0,
                            "cost_usd": 0.0, "elapsed_seconds": round(time.time() - t0, 2)},
            "agent_trace": [],
            "routing_info": None,
        }
```

Then update the four `self._err(f"…: {e}", …)` call sites to pass a code:

| Line (approx) | Today | New |
|---|---|---|
| `:558` | `_err(f"Direct answer failed: {b_err}", t0)` | `_err(str(b_err), t0, code=classify_exception(b_err))` |
| `:747` | `_err(f"Anthropic reasoning failed: {e}", t0, gi, go)` | `_err(str(e), t0, gi, go, code=ErrorCode.MODEL_UNAVAILABLE)` |
| `:803` | `_err(f"DB fallback failed: {e}", t0, gi, go)` | `_err(str(e), t0, gi, go, code=classify_exception(e))` |
| stream equivalents | same | same |

### 8.5 Step 2.5 — Never let a `None` reach `QueryResponse`

At `:840`, change:

```python
-            "results": er.get("results", []),
+            "results": er.get("results") or [],
```

and in `sql_engine.py` at `:278`, `:322`, `:438`, change `"results": last_results` to
`"results": last_results or []`. Also `"answer": final_answer or "No answer generated."` already
guards `answer`, but `_db_fallback`'s model-arena branch (`er["answer"] = answer.strip()`) can
produce `""` — guard it:

```python
-            er["answer"] = answer.strip()
+            cleaned = (answer or "").strip()
+            er["answer"] = cleaned or er.get("answer") or ""
```

Finally, in `routes.py`, wrap the construction (Phase 4 detail, listed here for completeness):

```python
from gemini_brain.resilience.envelope import normalize_envelope
...
        return QueryResponse(**normalize_envelope(result))
```

### 8.6 Step 2.6 — Guard the whole LEFT path answer

`answer` from `_call_gemini` can be `""` when all three model attempts fail (`return "", 0, 0` at
`:145`) *and* the Bedrock fallback also returns `""`. Add after the LEFT-path try/except:

```python
            if not (answer or "").strip():
                return self._err("all direct-answer providers returned empty",
                                 t0, gi, go, code=ErrorCode.MODEL_UNAVAILABLE)
```

### 8.7 Acceptance for Phase 2

| Scenario | Expected |
|---|---|
| API returns `{"success":true,"data":[]}` | HTTP 200, `status:"empty"`, `notice.code:"NO_ROWS"`, answer is the deterministic empty copy, **zero Bedrock calls** |
| API returns `{"success":false,"message":"x"}` | falls through to SQL fallback; never narrated |
| API times out 3× | `status:"degraded"`, `notice.code:"UPSTREAM_TIMEOUT"`, SQL fallback attempted |
| API returns 403 | `status:"degraded"`, `notice.code:"TENANT_FORBIDDEN"`, no fallback |
| SQL engine raises `RuntimeError` (§2.7) | HTTP **200**, `status:"degraded"`, friendly copy, no stack trace anywhere in the body |
| SQL engine returns `results=None` | HTTP 200, `results: []` |

---

## 9. Phase 3 — Deterministic formatting & the empty answer

**Files:** `src/gemini_brain/tools/formatters.py`, new `src/gemini_brain/formatting/`

### 9.1 Step 3.1 — Fix the `render_financial_statement` `None` (§2.3)

```python
 def render_financial_statement(data: Any) -> str:
     """Render P&L or Balance Sheet statement."""
     if isinstance(data, dict):
         lines = ["| Line Item | Amount |", "|---|---|"]
         ...
         return "\n".join(lines)
+    return render_row_table(data)
+
+
 def render_project_expense_rollup(data: Any) -> str:
```

Then make `render()` structurally incapable of returning a non-string:

```python
 def render(formatter_name: str, data: Any) -> str:
     fn = FORMATTERS.get(formatter_name, render_row_table)
     try:
         out = fn(data)
     except Exception as e:
         logger.warning("Formatter %s failed: %s", formatter_name, e)
         out = None
     if not isinstance(out, str) or not out.strip():
         try:
             out = render_row_table(data)
         except Exception:
             out = ""
     return out if isinstance(out, str) else ""
```

Add a guard test that every registered formatter returns `str` for `None`, `[]`, `{}`,
`[{"a":1}]`, `{"a":1}`, `"text"`, and `5`:

```python
# tests/unit/test_formatter_totality.py
import pytest
from gemini_brain.tools.formatters import FORMATTERS, render

CASES = [None, [], {}, "", "text", 5, [{"a": 1}], {"a": 1}, [1, 2, 3], {"items": []}]

@pytest.mark.parametrize("name", sorted(FORMATTERS))
@pytest.mark.parametrize("data", CASES)
def test_formatter_always_returns_string(name, data):
    out = render(name, data)
    assert isinstance(out, str)
```

### 9.2 Step 3.2 — Stop `format_aed` inventing zeros (§2.10)

```python
+#: Rendered when a value is genuinely absent (distinct from a real zero).
+MISSING = "—"
+
 def format_aed(val: Any) -> str:
-    if val is None or val == "":
-        return "AED 0.00"
+    """Format a numeric value as AED. Missing values render as an em dash, NOT as zero."""
+    if val is None or (isinstance(val, str) and not val.strip()):
+        return MISSING
     try:
         num = float(val)
         return f"AED {num:,.2f}"
     except (ValueError, TypeError):
         return str(val)
```

> **Behavioural note to state in the PR:** rows that previously showed `AED 0.00` for a null column
> now show `—`. A *real* `0` still renders `AED 0.00`. This is intentional and is the whole point.

### 9.3 Step 3.3 — The deterministic empty answer

**New file `src/gemini_brain/formatting/empty_answer.py`:**

```python
"""empty_answer.py — Deterministic, confident answers for zero-row results.

Never calls an LLM. Never says "N/A", "not available", or "the DATA block is empty".
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

#: endpoint → (subject phrase, positive framing when the answer is genuinely zero)
_SUBJECTS = {
    "/report/ar-aging-summary":        ("outstanding customer invoices", "Nothing is currently overdue from customers."),
    "/report/ap-aging-summary":        ("outstanding supplier bills",   "Nothing is currently overdue to suppliers."),
    "/report/customer-balance-summary": ("customer balances",           "No customer is carrying an open balance."),
    "/income/total":                   ("recorded income",              "No income has been recorded for this period."),
    "/income/list":                    ("sales invoices",               "No invoices were raised in this period."),
    "/expense/total":                  ("recorded expenses",            "No expenses have been recorded for this period."),
    "/expense/list":                   ("bills",                        "No bills were recorded in this period."),
    "/report/profit-loss":             ("profit and loss activity",     "There was no income or expense activity in this period."),
    "/report/balance-sheet":           ("balance sheet entries",        "No balances have been posted as at this date."),
    "/report/expense-by-category":     ("categorised expenses",         "No expenses were categorised in this period."),
    "/report/sales-by-customer":       ("customer sales",               "No sales were recorded against any customer in this period."),
    "/bank/manual/accounts":           ("bank accounts",                "No bank accounts have been set up yet."),
    "/item/list":                      ("items",                        "No items have been added to the catalogue."),
}

_DEFAULT = ("your records", "There is nothing recorded that matches this request.")


def subject_for(endpoint: Optional[str], tool_spec: Any = None, query: str = "") -> str:
    if endpoint and endpoint in _SUBJECTS:
        return _SUBJECTS[endpoint][0]
    if tool_spec is not None and getattr(tool_spec, "name", ""):
        return str(tool_spec.name).replace("_", " ")
    return _DEFAULT[0]


def build_empty_answer(query: str, subject: str, retrieved: Any = None) -> str:
    """Compose a short, confident, correctly formatted markdown answer for zero rows."""
    endpoint = getattr(retrieved, "endpoint", "") or ""
    headline = _SUBJECTS.get(endpoint, _DEFAULT)[1]
    today = datetime.date.today().strftime("%d %b %Y")

    return (
        f"{headline}\n"
        f"\n"
        f"I checked your live {subject} for this organization and found **no matching records**. "
        f"This is a confirmed result from your books as at {today} — not a system error.\n"
        f"\n"
        f"**What you can try**\n"
        f"\n"
        f"- Widen the date range and ask again\n"
        f"- Confirm the entries were posted to this organization\n"
        f"- Ask for a related figure, for example a full-year summary\n"
    )
```

Note the deliberate blank lines: they are what make the bullets render as bullets (§2.9 cause 3).

### 9.4 Step 3.4 — Markdown normaliser (server side)

**New file `src/gemini_brain/formatting/markdown.py`:**

```python
"""markdown.py — Normalise LLM markdown so the UI renders it consistently.

Fixes the three causes of 'bullets rendered inline':
  1. Unicode bullet glyphs that are not markdown list syntax
  2. Missing blank line before a list / table / heading
  3. Soft single newlines that CommonMark collapses into spaces
"""
from __future__ import annotations

import re

_UNICODE_BULLETS = re.compile(r"^(\s*)[•●○▪◦‣·–—*]\s+", re.MULTILINE)
_NUMBERED_INLINE = re.compile(r"(?<=[.:;])\s+(?=\d{1,2}[.)]\s+[A-Z])")
_HEADING = re.compile(r"^(#{1,6})\s*(\S)", re.MULTILINE)
_LIST_LINE = re.compile(r"^\s*(?:[-+*]\s+|\d{1,2}[.)]\s+)")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE = re.compile(r"^\s*```")


def normalize_markdown(text: str) -> str:
    """Idempotent. Safe on already-correct markdown. Never raises."""
    if not text or not isinstance(text, str):
        return ""

    try:
        # 1. Unicode bullets → '-' (only at line start; keeps '•' inside prose intact)
        out = _UNICODE_BULLETS.sub(r"\1- ", text)

        # 2. Split inline enumerations that a model glued onto one line
        out = _NUMBERED_INLINE.sub("\n", out)

        # 3. Ensure a blank line before the first line of any block construct
        lines = out.split("\n")
        result: list[str] = []
        in_fence = False
        for i, line in enumerate(lines):
            if _FENCE.match(line):
                in_fence = not in_fence
                result.append(line)
                continue
            if in_fence:
                result.append(line)
                continue

            prev = result[-1] if result else ""
            prev_blank = (not prev.strip())
            starts_block = bool(
                _LIST_LINE.match(line) or _TABLE_LINE.match(line) or _HEADING.match(line)
            )
            prev_is_same_block = bool(
                _LIST_LINE.match(prev) or _TABLE_LINE.match(prev)
            )
            if starts_block and prev.strip() and not prev_is_same_block:
                result.append("")
            # a heading always gets a blank line AFTER it too
            result.append(line)
        out = "\n".join(result)

        # 4. Normalise heading spacing '##Title' -> '## Title'
        out = _HEADING.sub(r"\1 \2", out)

        # 5. Collapse 3+ blank lines to exactly one blank line
        out = re.sub(r"\n{3,}", "\n\n", out)

        # 6. Trim trailing whitespace per line, strip the whole block
        out = "\n".join(l.rstrip() for l in out.split("\n")).strip()
        return out
    except Exception:
        return text
```

Apply it at exactly two places — right before the answer leaves the runner:

- `run()`: every `return {...}` that carries an `answer`.
- `run_stream()`: only on the **`final_result`** answer (never on individual tokens, or the
  normaliser would run on partial syntax).

The cleanest way is inside `normalize_envelope`:

```python
    from gemini_brain.formatting.markdown import normalize_markdown
    out["answer"] = normalize_markdown(answer) or answer
```

### 9.5 Acceptance for Phase 3

```python
# tests/unit/test_markdown_normalizer.py
from gemini_brain.formatting.markdown import normalize_markdown

def test_unicode_bullets_become_list():
    src = "Summary:\n• Revenue AED 100\n• Costs AED 40"
    out = normalize_markdown(src)
    assert "\n- Revenue AED 100" in out
    assert "Summary:\n\n- Revenue" in out       # blank line inserted

def test_table_gets_blank_line_before():
    src = "Here are the figures:\n| A | B |\n|---|---|\n| 1 | 2 |"
    assert "figures:\n\n| A | B |" in normalize_markdown(src)

def test_idempotent():
    src = "Intro\n\n- a\n- b\n"
    assert normalize_markdown(normalize_markdown(src)) == normalize_markdown(src)

def test_code_fence_untouched():
    src = "```\n• not a bullet\n```"
    assert "• not a bullet" in normalize_markdown(src)
```

---

## 10. Phase 4 — API layer: nothing raw ever escapes

**File:** `src/gemini_brain/api/app.py`, `src/gemini_brain/api/routes.py`

### 10.1 Step 4.1 — Global exception handlers (fixes §2.12)

```python
# app.py — ADD

import uuid
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from gemini_brain.resilience import ErrorCode, notice_for, classify_exception


def _json_notice(status_code: int, code, request_id: str, **kw) -> JSONResponse:
    notice = notice_for(code, request_id=request_id, **kw)
    return JSONResponse(
        status_code=status_code,
        content={"error": notice["code"], "notice": notice, "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except Exception:
            raise
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException):
        rid = getattr(request.state, "request_id", "")
        code_map = {
            401: ErrorCode.AUTH_REQUIRED,
            403: ErrorCode.TENANT_FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.NOT_FOUND,
            422: ErrorCode.VALIDATION_FAILED,
        }
        code = code_map.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        logger.info("[%s] HTTP %s on %s: %s", rid, exc.status_code, request.url.path, exc.detail)
        return _json_notice(exc.status_code, code, rid)

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "request_id", "")
        logger.info("[%s] validation error on %s: %s", rid, request.url.path, exc.errors())
        return _json_notice(400, ErrorCode.VALIDATION_FAILED, rid)

    @app.exception_handler(Exception)
    async def unhandled_exc_handler(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "")
        logger.exception("[%s] unhandled exception on %s", rid, request.url.path)
        return _json_notice(500, ErrorCode.INTERNAL_ERROR, rid)
```

Call `register_error_handlers(app)` inside `create_app()` **after** `add_middleware(CORSMiddleware…)`
and **before** `include_router(router)`.

> CORS note: FastAPI exception handlers run inside the middleware stack, so the CORS middleware
> *does* attach headers to these JSON responses. That is precisely why we must return
> `JSONResponse` from handlers rather than let Starlette's default error responses through.

### 10.2 Step 4.2 — `/query` never returns 5xx for a retrieval failure

```python
 def run_query(payload: QueryRequest, current_user: CurrentUser = Depends(get_current_user)) -> QueryResponse:
     try:
         runner = GeminiBrainRunner()
         result = runner.run(...)
-        return QueryResponse(**result)
+        return QueryResponse(**normalize_envelope(result))
     except ValueError as ve:
-        raise HTTPException(400, detail=str(ve))
+        # Tenant / validation rejections carry a user-safe message already.
+        rid = new_request_id()
+        logger.warning("[%s] tenant/validation rejection: %s", rid, ve)
+        code = ErrorCode.TENANT_FORBIDDEN if "Access denied" in str(ve) else ErrorCode.TENANT_AMBIGUOUS
+        notice = notice_for(code, request_id=rid, suggestions=_suggestions_for_tenant(ve))
+        raise HTTPException(status_code=code_http(code),
+                            detail={"error": code.value, "notice": notice, "request_id": rid})
     except Exception as e:
-        raise HTTPException(500, detail=f"Query execution failed: {str(e)}")
+        rid = new_request_id()
+        logger.exception("[%s] unhandled query failure", rid)
+        code = classify_exception(e)
+        notice = notice_for(code, request_id=rid)
+        # Degradable failures answer with 200 + a notice, so the UI renders a card, not an error page.
+        if code not in (ErrorCode.INTERNAL_ERROR,):
+            return QueryResponse(**normalize_envelope({
+                "answer": notice["message"], "error": code.value,
+                "status": "degraded", "notice": notice, "request_id": rid,
+            }))
+        raise HTTPException(500, detail={"error": code.value, "notice": notice, "request_id": rid})
```

### 10.3 Step 4.3 — SSE always terminates with `final_result` (fixes §2.6)

```python
     def event_generator() -> Generator[str, None, None]:
         rid = new_request_id()
         emitted_final = False

         def emit(obj) -> str:
             return f"data: {json.dumps(obj, default=str)}\n\n"

         try:
             runner = GeminiBrainRunner()
             for chunk in runner.run_stream(...):
                 if isinstance(chunk, dict) and "final_result" in chunk:
                     chunk["final_result"] = normalize_envelope(chunk["final_result"])
                     chunk["final_result"]["request_id"] = rid
                     emitted_final = True
                 yield emit(chunk)
         except ValueError as ve:
             code = ErrorCode.TENANT_FORBIDDEN if "Access denied" in str(ve) else ErrorCode.TENANT_AMBIGUOUS
             notice = notice_for(code, request_id=rid)
             yield emit({"type": "error", "notice": notice, "request_id": rid})
             yield emit({"final_result": normalize_envelope({
                 "answer": notice["message"], "error": code.value,
                 "status": "failed", "notice": notice, "request_id": rid})})
             emitted_final = True
         except Exception as e:
             logger.exception("[%s] stream failure", rid)
             code = classify_exception(e)
             notice = notice_for(code, request_id=rid)
             yield emit({"type": "error", "notice": notice, "request_id": rid})
             yield emit({"final_result": normalize_envelope({
                 "answer": notice["message"], "error": code.value,
                 "status": "degraded", "notice": notice, "request_id": rid})})
             emitted_final = True
         finally:
             # Absolute guarantee: the client always gets a terminal event.
             if not emitted_final:
                 notice = notice_for(ErrorCode.INTERNAL_ERROR, request_id=rid)
                 yield emit({"final_result": normalize_envelope({
                     "answer": notice["message"], "error": ErrorCode.INTERNAL_ERROR.value,
                     "status": "failed", "notice": notice, "request_id": rid})})
             yield "data: [DONE]\n\n"
```

> `yield` inside `finally` in a generator is legal and runs on normal completion and on exception.
> It does **not** run if the client disconnects and the generator is garbage-collected — which is
> fine, because there is no client left to receive it.

Also emit an SSE heartbeat so proxies do not kill idle streams during a long Bedrock call. In
`run_stream`, before each long stage, the existing `{"status": …}` events already serve this
purpose; add one explicit comment-frame keepalive if a stage can exceed 15 s:

```python
yield ": keepalive\n\n"   # SSE comment frame — ignored by EventSource and by our parser
```

### 10.4 Step 4.4 — Emit the `data_table` and `notice` events properly

In `run_stream`, replace:

```python
-            yield {"status": "Rendering financial data", "type": "data_table", "table": formatted_table}
+            yield {
+                "type": "data_table",
+                "table": formatted_table,
+                "row_count": retrieved.row_count,
+                "truncated": retrieved.truncated,
+            }   # NOTE: no "status" key — it must not be swallowed by the status branch
```

and carry `table_markdown` into the `final_result` so the sync path and reloads have it too.

### 10.5 Acceptance for Phase 4

| Request | Expected |
|---|---|
| `GET /api/v1/nope` | `404` with `{"error":"NOT_FOUND","notice":{…},"request_id":"…"}` + CORS headers |
| `POST /api/v1/query` with `{"query": 123}` | `400 VALIDATION_FAILED`, no Pydantic internals |
| `POST /api/v1/query` no token | `401 AUTH_REQUIRED` |
| `POST /api/v1/query` expired token | `401 AUTH_EXPIRED` |
| `POST /api/v1/query` org not in allow-list | `403 TENANT_FORBIDDEN` with friendly copy |
| Runner throws `RuntimeError` | `200` `status:"degraded"` (not 500) |
| Stream where the runner throws immediately | at least 2 events, last is `final_result`, then `[DONE]` |

---

## 11. Phase 5 — The copy deck

All user-facing strings live in `resilience/messages.py`. **No other module may compose
user-facing error text.** Grep guard: `grep -rn '"Error: ' src/` must return zero hits after
implementation.

| Code | Title | Message | Suggestions |
|---|---|---|---|
| `NO_ROWS` | *Nothing recorded for this request* | "I checked {subject} and there are no matching records yet. That is a confirmed result from your books, not a system problem." | Widen the date range · Confirm the entries were posted to this organization |
| `PARTIAL_DATA` | *Showing a partial view* | "This covers the first {shown} of {total} records. Totals below reflect only the rows shown." | Narrow the date range for a complete view |
| `UPSTREAM_TIMEOUT` | *The finance service took too long* | "I could not retrieve live figures for {subject} in time, so I have not shown any numbers rather than showing you something unverified." | Try again in a moment · Narrow the date range |
| `UPSTREAM_UNAVAILABLE` | *The finance service is unreachable* | "Your accounting service did not respond. I have not produced any figures, because anything I showed would be a guess." | Try again shortly · Check the service status page |
| `MODEL_UNAVAILABLE` | *The analysis engine is unavailable* | "I retrieved your data but could not generate the written summary. The figures below are complete and correct." | Read the table below · Retry for the written summary |
| `MODEL_RATE_LIMITED` | *High demand right now* | "The analysis engine is at capacity. Your data was retrieved successfully — only the written summary is missing." | Retry in about a minute |
| `DB_UNAVAILABLE` | *The database is unreachable* | "I could not reach the reporting database, so no figures were produced for this question." | Try again shortly |
| `QUERY_FAILED` | *I could not build a reliable query* | "I understood the question but could not turn it into a query I trust. Rather than show a number that might be wrong, I have shown nothing." | Rephrase more specifically · Name the report you want |
| `TENANT_FORBIDDEN` | *That organization is outside your access* | "Your account does not have access to the organization in this request. Nothing was retrieved." | Switch to an organization you have access to · Contact your administrator |
| `TENANT_AMBIGUOUS` | *Which organization?* | "You have access to more than one organization and this question did not name one." | Pick an organization from the switcher · Name the organization in your question |
| `AUTH_REQUIRED` | *Please sign in* | "Your session is not active. Sign in to continue." | Sign in |
| `AUTH_EXPIRED` | *Your session has expired* | "For security, sessions end after a period of inactivity. Sign in again to continue — your conversation is preserved." | Sign in again |
| `NOT_FOUND` | *That page doesn't exist* | "The address you requested is not part of this service." | Return to the main screen |
| `VALIDATION_FAILED` | *I couldn't read that request* | "Part of the request was not in the expected format." | Try rephrasing the question |
| `INTERNAL_ERROR` | *Something went wrong on our side* | "An unexpected problem stopped this request. Nothing was changed in your books. Reference {request_id}." | Try again · Share the reference with support |

**Copy rules (enforce in review):**
- Never the words: `null`, `undefined`, `None`, `N/A`, `NaN`, `DATA block`, `payload`, `exception`,
  `traceback`, `500`, `404`, `stack`.
- Never blame the user.
- Always say what happened to the data ("nothing was retrieved" / "the figures are complete").
- Always end with something actionable.

---

## 12. Phase 6 — Frontend

**Files:** `ui/src/services/api.js`, `ui/src/App.jsx`, `ui/src/components/ResponseView.jsx`,
new `ui/src/components/NoticeCard.jsx`, new `ui/src/utils/markdown.js`, `ui/src/index.css`.

### 12.1 Step 6.1 — `api.js`: parse the notice envelope, never throw a bare string

```js
const parseErrorBody = async (response) => {
  const fallback = {
    kind: response.status === 401 ? 'denied' : 'failed',
    code: `HTTP_${response.status}`,
    title: 'Something went wrong',
    message: 'The request could not be completed. Please try again.',
    suggestions: ['Try again'],
    retryable: response.status >= 500 || response.status === 429,
  };
  try {
    const body = await response.json();
    // FastAPI wraps our object in `detail` for HTTPException; handlers return it flat.
    const n = body?.notice || body?.detail?.notice;
    return n || fallback;
  } catch {
    return fallback;
  }
};

export class ApiError extends Error {
  constructor(notice, status) {
    super(notice.message);
    this.notice = notice;
    this.status = status;
    this.name = 'ApiError';
  }
}

export const fetchQueryResponse = async (payload, token = '') => {
  let response;
  try {
    response = await fetch('/api/v1/query', { method: 'POST', headers, body: JSON.stringify(payload) });
  } catch (networkErr) {
    throw new ApiError({
      kind: 'degraded', code: 'NETWORK_UNREACHABLE',
      title: 'Cannot reach the server',
      message: 'Your browser could not reach Gemini Brain. Check your connection and try again.',
      suggestions: ['Check your connection', 'Try again'], retryable: true,
    }, 0);
  }
  if (!response.ok) throw new ApiError(await parseErrorBody(response), response.status);
  return await response.json();
};
```

Do the same for `loginUser` and `fetchModelHealth`. In `streamQueryResponse`, handle a non-OK
pre-stream response by synthesising a `final_result` chunk so the caller's state machine always
completes:

```js
      if (!response.ok) {
        const notice = await parseErrorBody(response);
        onChunk({ final_result: { answer: notice.message, error: notice.code,
                                  status: 'failed', notice, results: [] } });
        if (onComplete) onComplete();
        return;
      }
```

Also add a **stall watchdog** — if no chunk arrives for 45 s, synthesise a timeout notice and abort:

```js
  let stallTimer;
  const resetStall = () => {
    clearTimeout(stallTimer);
    stallTimer = setTimeout(() => {
      controller.abort();
      onChunk({ final_result: { answer: 'The response stopped arriving…', status: 'degraded',
        error: 'UPSTREAM_TIMEOUT', results: [],
        notice: { kind: 'degraded', code: 'UPSTREAM_TIMEOUT',
                  title: 'The response stopped arriving',
                  message: 'The connection went quiet before the answer finished. Nothing partial has been saved.',
                  suggestions: ['Try again'], retryable: true } } });
      if (onComplete) onComplete();
    }, 45000);
  };
```

Call `resetStall()` after each parsed chunk and `clearTimeout(stallTimer)` on completion. Ignore
SSE comment frames (`: keepalive`) and the `[DONE]` sentinel in the parser.

### 12.2 Step 6.2 — `App.jsx`: handle the full chunk contract

Add branches, in this order (order matters — `final_result` and `type` checks must come **before**
the generic `chunk.status` branch):

```js
  // 1. terminal
  if (chunk.final_result) { …existing… }
  // 2. deterministic table — render immediately, above the narration
  else if (chunk.type === 'data_table') {
    updateLastAssistant(prev => ({ ...prev, tableMarkdown: chunk.table, rowCount: chunk.row_count }));
  }
  // 3. non-fatal notice
  else if (chunk.type === 'notice') {
    updateLastAssistant(prev => ({ ...prev, notice: chunk.notice }));
  }
  // 4. fatal — record it; a final_result always follows
  else if (chunk.type === 'error') {
    updateLastAssistant(prev => ({ ...prev, notice: chunk.notice, isError: true }));
  }
  // 5. token
  else if (chunk.token || chunk.type === 'token') { …existing… }
  // 6. status
  else if (chunk.status) { …existing… }
```

Add a **401 auto-logout**: when any `ApiError` has `status === 401`, clear
`localStorage.gemini_brain_user` and set `currentUser` to null so the user lands on the login page
with the `AUTH_EXPIRED` message pre-filled, instead of seeing a red banner forever.

### 12.3 Step 6.3 — `NoticeCard.jsx` (replaces the mislabelled security banner, §2.5)

```jsx
import React from 'react';
import { Info, AlertTriangle, ShieldAlert, XCircle, RotateCw } from 'lucide-react';

const KIND = {
  empty:    { icon: Info,        color: '#38bdf8', bg: 'rgba(56,189,248,0.08)',  border: 'rgba(56,189,248,0.25)' },
  partial:  { icon: Info,        color: '#38bdf8', bg: 'rgba(56,189,248,0.08)',  border: 'rgba(56,189,248,0.25)' },
  degraded: { icon: AlertTriangle,color:'#f59e0b', bg: 'rgba(245,158,11,0.08)',  border: 'rgba(245,158,11,0.25)' },
  denied:   { icon: ShieldAlert, color: '#f43f5e', bg: 'rgba(244,63,94,0.08)',   border: 'rgba(244,63,94,0.25)' },
  failed:   { icon: XCircle,     color: '#f43f5e', bg: 'rgba(244,63,94,0.08)',   border: 'rgba(244,63,94,0.25)' },
};

export const NoticeCard = ({ notice, onRetry }) => {
  if (!notice) return null;
  const style = KIND[notice.kind] || KIND.failed;
  const Icon = style.icon;
  return (
    <div style={{ padding: '14px 16px', borderRadius: 10, background: style.bg,
                  border: `1px solid ${style.border}`, display: 'flex', gap: 12 }}>
      <Icon size={20} color={style.color} style={{ flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1 }}>
        <h4 style={{ color: style.color, fontSize: '0.95rem', marginBottom: 4 }}>{notice.title}</h4>
        <p style={{ color: '#e5e7eb', fontSize: '0.875rem', lineHeight: 1.6 }}>{notice.message}</p>
        {notice.suggestions?.length > 0 && (
          <ul style={{ margin: '8px 0 0 1.1em', color: '#9ca3af', fontSize: '0.82rem' }}>
            {notice.suggestions.map((s, i) => <li key={i} style={{ marginBottom: 2 }}>{s}</li>)}
          </ul>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
          {notice.retryable && onRetry && (
            <button onClick={onRetry} style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
              background: 'transparent', border: `1px solid ${style.border}`, color: style.color,
              padding: '5px 10px', borderRadius: 6, fontSize: '0.78rem', cursor: 'pointer' }}>
              <RotateCw size={12} /> Try again
            </button>
          )}
          {notice.request_id && (
            <span style={{ color: '#4b5563', fontSize: '0.7rem', fontFamily: 'var(--font-mono)' }}>
              ref {notice.request_id}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
```

### 12.4 Step 6.4 — `ResponseView.jsx` rework

Replace the `isError` block (`:107`, `:124–140`) with:

```jsx
  const notice   = responseData?.notice || msg.notice || null;
  const status   = responseData?.status || (notice ? notice.kind : 'ok');
  const table    = responseData?.table_markdown || msg.tableMarkdown || null;
  const rawText  = responseData?.answer ?? msg.streamingText ?? '';
  const content  = typeof rawText === 'string' ? rawText.trim() : '';
  const isFatal  = status === 'failed' || notice?.kind === 'denied';

  return (
    <div style={styles.assistantRow}>
      {/* 1. deterministic table first — it is the ground truth */}
      {table && <MarkdownBlock text={table} />}

      {/* 2. narration (skipped entirely on a fatal notice) */}
      {!isFatal && content && <PacedMarkdownStream text={content} isStreaming={isStreaming} />}

      {/* 3. notice card — informational for empty/partial, warning for degraded, error for fatal */}
      {notice && <NoticeCard notice={notice} onRetry={() => onRegenerate?.(userQuery)} />}

      {/* 4. genuinely nothing at all — should now be unreachable, kept as a backstop */}
      {!table && !content && !notice && !isStreaming && (
        <NoticeCard notice={{
          kind: 'failed', code: 'EMPTY_TURN', title: 'No answer was produced',
          message: 'The request finished without producing an answer.',
          suggestions: ['Try again'], retryable: true,
        }} onRetry={() => onRegenerate?.(userQuery)} />
      )}
      …existing inspectors / toolbar…
    </div>
  );
```

Add a small `MarkdownBlock` that runs the client-side normaliser and renders in one shot (no
pacing), used for tables and any non-streamed content.

### 12.5 Step 6.5 — Fix `PacedMarkdownStream` (§2.9)

Two changes:

```diff
-  const currentSlice = text ? text.slice(0, revealedLength) : '';
+  // Never fall back to the full text — that causes the "flash then rewind" glitch.
+  const currentSlice = text ? text.slice(0, revealedLength) : '';
+  // Do not cut inside a markdown table: extend the slice to the end of the current line.
+  const safeSlice = React.useMemo(() => sliceToBlockBoundary(text, revealedLength), [text, revealedLength]);
   return (
     <div className="markdown-body" style={styles.markdownWrapper}>
-      <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
-        {currentSlice || text}
-      </ReactMarkdown>
+      <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
+        {normalizeMarkdown(safeSlice)}
+      </ReactMarkdown>
```

`sliceToBlockBoundary(text, n)` (in `ui/src/utils/markdown.js`): if the character at `n-1` is not a
newline **and** the current line starts with `|` or the text contains an unclosed code fence,
extend `n` to the next `\n` (or return the last complete line). This eliminates half-rendered
tables.

### 12.6 Step 6.6 — `ui/src/utils/markdown.js` (client mirror of §9.4)

Port `normalize_markdown` to JS with the same rules. Purpose: defend against any markdown that
did not pass through the server normaliser (streamed slices, legacy cached answers).

```js
const UNICODE_BULLETS = /^(\s*)[•●○▪◦‣·–—*]\s+/gm;
const LIST_LINE  = /^\s*(?:[-+*]\s+|\d{1,2}[.)]\s+)/;
const TABLE_LINE = /^\s*\|.*\|\s*$/;
const HEADING    = /^(#{1,6})\s*(\S)/;

export function normalizeMarkdown(text) {
  if (!text || typeof text !== 'string') return '';
  let out = text.replace(UNICODE_BULLETS, '$1- ');
  const lines = out.split('\n');
  const res = [];
  let inFence = false;
  for (const line of lines) {
    if (/^\s*```/.test(line)) { inFence = !inFence; res.push(line); continue; }
    if (inFence) { res.push(line); continue; }
    const prev = res[res.length - 1] || '';
    const startsBlock = LIST_LINE.test(line) || TABLE_LINE.test(line) || HEADING.test(line);
    const prevSameBlock = LIST_LINE.test(prev) || TABLE_LINE.test(prev);
    if (startsBlock && prev.trim() && !prevSameBlock) res.push('');
    res.push(line);
  }
  return res.join('\n').replace(HEADING, '$1 $2').replace(/\n{3,}/g, '\n\n').trim();
}

export function sliceToBlockBoundary(text, n) {
  if (!text) return '';
  if (n >= text.length) return text;
  const slice = text.slice(0, n);
  const lastNl = slice.lastIndexOf('\n');
  const currentLine = slice.slice(lastNl + 1);
  // Never render a half table row or a half fence.
  if (currentLine.trimStart().startsWith('|') || (slice.split('```').length - 1) % 2 === 1) {
    return lastNl === -1 ? '' : slice.slice(0, lastNl);
  }
  return slice;
}
```

### 12.7 Step 6.7 — CSS: make lists render as lists (§2.9)

```css
/* Restore list markers stripped by the global reset */
.markdown-body ul,
.markdown-body ol {
  margin: 0 0 1em 0;
  padding-left: 1.5em;         /* markers live INSIDE the box → never clipped */
  list-style-position: outside;
}
.markdown-body ul { list-style-type: disc; }
.markdown-body ol { list-style-type: decimal; }
.markdown-body ul ul { list-style-type: circle; margin-bottom: 0.35em; }
.markdown-body li { margin-bottom: 0.35em; display: list-item; }
.markdown-body li > p { margin-bottom: 0.25em; }

/* Long unbroken figures/IDs must not blow out the layout */
.markdown-body code { word-break: break-word; }

/* Table cells: allow wrapping for text, keep numbers on one line */
.markdown-body td, .markdown-body th { white-space: normal; }
.markdown-body td:has(+ td), .markdown-body td { vertical-align: top; }
.markdown-body td.numeric, .markdown-body th.numeric { white-space: nowrap; text-align: right; }
```

And remove the clipping in `ResponseView.jsx`:

```diff
   markdownWrapper: {
     width: '100%',
     maxWidth: '100%',
-    overflowX: 'hidden',
+    overflowX: 'clip',       /* clip without creating a scroll container that eats markers */
```

> `white-space: nowrap` on all cells is why wide tables scroll today. Switching to `normal` plus the
> existing `.markdown-table-wrapper { overflow-x: auto }` gives readable tables that still scroll
> when genuinely wide.

### 12.8 Step 6.8 — Config alignment (§2.15b)

Set the Vite proxy target from an env var with the correct default:

```js
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
```

and add `VITE_API_TARGET=http://localhost:8001` to `ui/.env.local` if the team runs on 8001.

### 12.9 Acceptance for Phase 6

| Scenario | Expected UI |
|---|---|
| Empty result | Blue info `NoticeCard` "Nothing recorded for this request" + the confident answer paragraph. **No red banner.** |
| Upstream timeout | Amber `NoticeCard` + "Try again" button that re-submits the same query |
| Tenant denial | Red shield `NoticeCard` "That organization is outside your access" |
| Token expired mid-session | User is returned to the login page with "Your session has expired" |
| Server down (`ECONNREFUSED`) | Amber card "Cannot reach the server" — not an infinite spinner |
| Model returns `• a • b` on one line | Renders as two real bullets |
| Table streamed | Table paints once, whole, above the narration; no flicker |

---

## 13. Test matrix

Add `tests/unit/test_negative_paths.py` and `tests/integration/test_degradation.py`.

| # | Injection point | Injected condition | Assert |
|---|---|---|---|
| N01 | `call_api` | HTTP 200 `{"success":true,"data":[]}` | `status=="empty"`, `notice.code=="NO_ROWS"`, `llm_calls` unchanged |
| N02 | `call_api` | HTTP 200 `{"success":false,"message":"x"}` | falls to SQL tier; `"x"` never appears in `answer` |
| N03 | `call_api` | HTTP 200, body `""` | `status=="empty"` |
| N04 | `call_api` | HTTP 200, body `"<html>502</html>"` | `Outcome.INVALID`, SQL tier attempted |
| N05 | `call_api` | HTTP 404 | `status=="empty"` (not an error) |
| N06 | `call_api` | HTTP 403 | `status=="degraded"`, `TENANT_FORBIDDEN`, no SQL tier |
| N07 | `call_api` | `httpx.TimeoutException` ×3 | 3 attempts made, `UPSTREAM_TIMEOUT` |
| N08 | `call_api` | HTTP 503 then HTTP 200 | retry succeeds, `status=="ok"` |
| N09 | `sql_engine.run` | raises `RuntimeError` | HTTP 200, `status=="degraded"`, no `"Traceback"` in body |
| N10 | `sql_engine.run` | returns `results=None` | `results == []`, HTTP 200 |
| N11 | `sql_engine.run` | returns `answer=""` | `answer` non-empty (from notice) |
| N12 | `BedrockAdapter.converse` | raises `ThrottlingException` ×3 | `MODEL_RATE_LIMITED`, table still returned |
| N13 | `_call_gemini` | returns `("", 0, 0)` | Bedrock fallback attempted; if that fails → `MODEL_UNAVAILABLE`, not `""` |
| N14 | `get_connection` | raises `OperationalError` | `DB_UNAVAILABLE`; DSN (host/user) absent from the response body |
| N15 | `render(...)` | formatter raises | `render` returns a string; response valid |
| N16 | `render_financial_statement` | list input | returns a markdown table (not `None`) |
| N17 | Route | unknown path | `404` + notice envelope + CORS headers present |
| N18 | Route | body `{"query": null}` | `400 VALIDATION_FAILED` |
| N19 | Route | no `Authorization` | `401 AUTH_REQUIRED` |
| N20 | Route | expired JWT | `401 AUTH_EXPIRED` |
| N21 | Stream | runner raises on the first `next()` | ≥2 events; last is `final_result`; `[DONE]` sent |
| N22 | Stream | client disconnects mid-stream | no unhandled exception in logs |
| N23 | Tenant | `allowed_org_ids == []` | `403 TENANT_FORBIDDEN` |
| N24 | Tenant | 2 allowed orgs, no org in body or query | `400 TENANT_AMBIGUOUS` with the org-switcher suggestion |
| N25 | Markdown | `"Totals:\n• a\n• b"` | normaliser emits `Totals:\n\n- a\n- b` |
| N26 | Markdown | already-normalised text | idempotent |
| N27 | Cache | previous call was `EMPTY` | TTL is 30 s, not 300 s |
| N28 | Cache | previous call was `UNAVAILABLE` | nothing cached |
| N29 | Envelope | `{"answer": None, "results": None}` | `normalize_envelope` yields a valid `QueryResponse` |
| N30 | Copy | full run over all `NOTICES` | none contain `null`/`None`/`N/A`/`DATA block`/`Traceback` |

Add a repo-wide guard test:

```python
# tests/unit/test_no_raw_error_text.py
import pathlib, re
BANNED = re.compile(r'f?"Error: |DATA block|Traceback|\bN/A\b')
def test_no_raw_error_strings_in_user_paths():
    roots = ["src/gemini_brain/orchestrator", "src/gemini_brain/api",
             "src/gemini_brain/sql_fallback", "src/gemini_brain/tools"]
    hits = []
    for root in roots:
        for p in pathlib.Path(root).rglob("*.py"):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if BANNED.search(line) and "noqa: copy" not in line:
                    hits.append(f"{p}:{i}: {line.strip()}")
    assert not hits, "Raw user-facing error text found:\n" + "\n".join(hits)
```

---

## 14. Implementation order & effort

| Phase | Scope | Files | Risk | Est. |
|---|---|---|---|---|
| 0 | `resilience/` package, model fields | 5 new, 1 edited | none | 0.5 d |
| 1 | API client + DB retrieval outcomes | 2 edited | low | 0.5 d |
| 2 | Orchestrator rewiring | 1 edited | **medium** | 1.5 d |
| 3 | Formatters + empty answer + markdown normaliser | 2 edited, 2 new | low | 0.5 d |
| 4 | FastAPI handlers + SSE contract | 2 edited | low | 0.5 d |
| 5 | Copy deck | 1 edited | none | 0.25 d |
| 6 | Frontend | 5 edited, 2 new | medium | 1.5 d |
| 7 | Tests | ~6 new | none | 1 d |

**Total ≈ 6 developer-days.**

Phase 2 is the only medium-risk phase. Mitigation: it touches exactly one file, the retrieval block
it replaces is already duplicated verbatim in two places (so extracting it is a net simplification),
and every other phase is additive.

### Suggested PR split

1. `feat(resilience): outcome + error + copy foundations` — Phases 0, 5, tests
2. `fix(api-client): structured retrieval outcomes and envelope safety` — Phase 1
3. `fix(formatters): total functions, honest missing values, markdown normaliser` — Phase 3
4. `refactor(orchestrator): branch on retrieval outcome instead of null` — Phase 2
5. `feat(api): global error handlers and SSE termination guarantee` — Phase 4
6. `feat(ui): notice cards, markdown normalisation, stall watchdog` — Phase 6

---

## 15. Verification checklist (run before declaring done)

**Backend**

- [ ] `pytest tests/ -q` green
- [ ] `grep -rn '"Error: ' src/` → 0 hits
- [ ] `grep -rn 'if data is not None' src/` → 0 hits
- [ ] `grep -rn 'AED 0.00' src/` → only in tests
- [ ] `grep -rn 'C:\\\\Users' src/` → 0 hits (§2.7 path made configurable via env)
- [ ] Every `except Exception` in `orchestrator/`, `api/`, `sql_fallback/` logs with a `request_id`
- [ ] `curl -s localhost:8000/api/v1/nope | jq .notice` → a notice object
- [ ] Stop Postgres → `POST /query` still returns 200 with a `DB_UNAVAILABLE` notice
- [ ] Point `ACCUTAX_BASE_URL` at a dead host → 200 with `UPSTREAM_UNAVAILABLE`, SQL tier attempted
- [ ] Unset `GEMINI_API_KEY` → LEFT-path query returns a `MODEL_UNAVAILABLE` notice, not a 500

**Frontend**

- [ ] No response path renders "No response generated. Please try again."
- [ ] No non-security error renders "Security Isolation Boundary Notice"
- [ ] Kill the backend mid-stream → an amber card appears within 45 s
- [ ] Empty result renders a blue info card, never red
- [ ] A model answer using `•` bullets renders as real bullets
- [ ] A wide table scrolls horizontally inside its wrapper; the page body does not
- [ ] `X-Request-ID` from the response is shown as `ref …` on every error card

---

## 16. Out of scope (tracked separately)

These were found during the audit but are **not** part of this work. Do not fix them in these PRs.

| Ref | Issue | Why separate |
|---|---|---|
| §2.15c | `get_user_by_email` returns a synthetic user with a fixed password for any unknown email when the DB is down (`auth.py:246`) | Authentication bypass — needs its own security review and change-control |
| §2.14 | Fabricated token counts on the Gemini streaming path (`runner:1017`) | Billing/metrics accuracy, separate from resilience |
| §2.15a | Missing `Tuple` import in `sql_engine.py` | Trivial; fold into any passing PR |
| §2.15d | `retry_with_backoff` only retries `ThrottlingException` | Worth doing; low urgency once the orchestrator degrades gracefully |
| — | `_get_coordinator_pipeline` depends on an external repo | Should become a proper dependency or an in-repo module; Phase 2 makes its absence *survivable*, which unblocks everything else |

---

## Appendix A — File change index

| File | Phase | Change |
|---|---|---|
| `src/gemini_brain/resilience/__init__.py` | 0 | **new** |
| `src/gemini_brain/resilience/outcomes.py` | 0 | **new** — `Outcome`, `Retrieved`, `classify_payload` |
| `src/gemini_brain/resilience/errors.py` | 0 | **new** — `ErrorCode`, `AppError`, `classify_exception` |
| `src/gemini_brain/resilience/messages.py` | 0/5 | **new** — copy deck |
| `src/gemini_brain/resilience/envelope.py` | 0 | **new** — `normalize_envelope`, `new_request_id` |
| `src/gemini_brain/api/models.py` | 0 | add `NoticeSchema`, `DataSourceSchema`, 5 optional fields on `QueryResponse` |
| `src/gemini_brain/api_client/accutax_client.py` | 1 | add `call_api_resilient`, `extract_data_safe`; `extract_data` → shim |
| `src/gemini_brain/sql_fallback/db_connection.py` | 1 | add `execute_sql_function_safe`; add `statement_timeout` to `get_connection` |
| `src/gemini_brain/orchestrator/gemini_brain_runner.py` | 2 | add `_retrieve`, `_empty_result`, `_degraded_result`; rewrite `_err`; replace both `if data is not None` blocks; `results or []` |
| `src/gemini_brain/sql_fallback/sql_engine.py` | 2 | `"results": last_results or []` ×3; guard empty answer; import `Tuple` |
| `src/gemini_brain/tools/formatters.py` | 3 | fix `render_financial_statement` return; harden `render`; `format_aed` missing → `—` |
| `src/gemini_brain/formatting/__init__.py` | 3 | **new** |
| `src/gemini_brain/formatting/empty_answer.py` | 3 | **new** |
| `src/gemini_brain/formatting/markdown.py` | 3 | **new** |
| `src/gemini_brain/api/app.py` | 4 | add `register_error_handlers`, request-id middleware |
| `src/gemini_brain/api/routes.py` | 4 | `normalize_envelope` on both paths; notice-based exception mapping; SSE `finally` guarantee |
| `ui/src/services/api.js` | 6 | `ApiError`, `parseErrorBody`, stall watchdog, `[DONE]`/comment-frame handling |
| `ui/src/App.jsx` | 6 | chunk-type branches, 401 auto-logout, table/notice state |
| `ui/src/components/ResponseView.jsx` | 6 | `NoticeCard` integration, table-first render, paced-stream fixes |
| `ui/src/components/NoticeCard.jsx` | 6 | **new** |
| `ui/src/utils/markdown.js` | 6 | **new** — `normalizeMarkdown`, `sliceToBlockBoundary` |
| `ui/src/index.css` | 6 | list marker restoration, table cell wrapping |
| `ui/vite.config.js` | 6 | proxy target from env, default 8000 |

## Appendix B — Quick reference: the one-line rule per defect

```
§2.1  data is not None            →  retrieved.usable
§2.2  er.get("results", [])       →  er.get("results") or []
§2.3  render_financial_statement  →  add `return render_row_table(data)`
§2.4  extract_data                →  extract_data_safe (success:false stays wrapped)
§2.5  "Security Isolation…"       →  <NoticeCard notice={notice} />
§2.6  SSE error chunk             →  always followed by final_result
§2.7  RuntimeError from SQL tier  →  classify_exception → degraded 200
§2.8  data_table chunk dropped    →  remove "status" key + add App.jsx branch
§2.9  inline bullets              →  normalize_markdown + CSS list-style
§2.10 format_aed(None)="AED 0.00" →  "—"
§2.11 cache poisoning             →  cache OK 300s / EMPTY 30s / never on failure
§2.12 raw 404/422/500             →  register_error_handlers
§2.13 detail=str(e)               →  detail={"error": code, "notice": …}
```
