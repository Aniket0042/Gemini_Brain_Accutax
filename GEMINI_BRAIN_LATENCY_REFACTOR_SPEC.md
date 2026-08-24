# Gemini Brain — Latency Refactor Implementation Spec

> **How to use this file:** drop it in the repo root and reference it from Claude Code
> (`@GEMINI_BRAIN_LATENCY_REFACTOR_SPEC.md`). Implement **one phase per PR**. Do not
> start a phase until the previous one is merged and its acceptance criteria pass.

---

## Mission

Reduce end-to-end query latency in `Gemini_Brain` from 60–120s (complex) to under 5s,
and from 5–15s (simple lookups) to under 1s — without reducing answer quality.

The existing NL-to-SQL engine (`sql_fallback/`) is **retained** as a last-resort fallback
for genuinely novel questions, but is demoted: it becomes unreachable from the normal path,
is hard-budgeted, and runs under database-enforced tenant isolation.

## Root causes (already diagnosed — do not re-investigate)

1. **5–6 sequential LLM round trips per query.** `intent_classifier` → `endpoint_selector`
   → `param_normalizer` → `complexity_judge` → `claude_reasoner`, each a separate network call.
2. **NL-to-SQL generation + retry loops.** `sql_fallback/` generates SQL at request time,
   often retries on error. Slowest and least reliable path.
3. **The LLM does arithmetic.** Large raw payloads are handed to Bedrock for aggregation.
   Slow (input tokens), expensive, and a correctness risk on financial data.
4. **`endpoint_selector.py` swallows transient errors** and returns `None`, which the
   orchestrator interprets as "no endpoint exists" and escalates into the slow SQL path.
5. **Sync `requests` client inside async FastAPI**, blocking the event loop.
6. **Gemini 2.5 Flash thinking enabled by default** on a pure classification task.
7. **Prompt caching defeated** — variable fields interpolated above the stable `API_CATALOG` block.

## Non-negotiables

- **`organization_id` and `user_id` are NEVER LLM-supplied.** They come from the JWT via
  `RequestCtx`. No tool param schema may contain them. This is the tenant isolation model.
- **The LLM never computes, sums, averages, or derives a number.** Aggregation happens in
  the API or in SQL. The LLM only narrates finished figures.
- **Payload into Bedrock is hard-capped at 2000 tokens.** Truncate with an explicit note.
- **Every phase is independently shippable** and behind a feature flag where it changes routing.
- **`sql_fallback/` is kept, not deleted.** But it must become reachable by exactly one
  route: an explicit `unsupported` classification. It must never be entered because of a
  transient error, a timeout, or a failed API call.
- Preserve existing behaviour for: PII redaction (`pii/redactor.py`), JWT auth (`api/auth.py`),
  session memory (`memory/`), health checks (`health/`), and the SSE event contract consumed
  by `ui/src/services/api.js`. New SSE event types may be **added**; existing ones must not
  change shape.

---

## Phase 0 — Instrumentation (do this first, it gates everything)

**Goal:** know where the time actually goes before changing behaviour.

### Changes

Create `src/gemini_brain/observability/timing.py`:

```python
@dataclass
class StageTiming:
    stage: str
    duration_ms: float
    meta: dict

class QueryTrace:
    """Collects per-stage timings for one query. Attach to RequestCtx."""
    def __init__(self, query_id: str, org_id: int):
        self.query_id, self.org_id = query_id, org_id
        self.stages: list[StageTiming] = []
        self.t0 = time.perf_counter()

    @contextmanager
    def stage(self, name: str, **meta):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.stages.append(StageTiming(
                name, (time.perf_counter() - t) * 1000, meta))

    def emit(self):
        logger.info("query_trace", extra={"query_id": self.query_id,
            "org_id": self.org_id, "total_ms": (time.perf_counter()-self.t0)*1000,
            "stages": [asdict(s) for s in self.stages]})
```

Wrap every stage in `orchestrator/gemini_brain_runner.py`:
`classification`, `endpoint_selection`, `param_normalization`, `api_call`,
`complexity_judge`, `bedrock_reasoning`, `sql_fallback`, `pii_redaction`, `memory_write`.

Add counters: `router_transient_failures`, `sql_fallback_entered`, `api_call_failed`.

### Acceptance criteria

- [ ] Every query emits one structured `query_trace` log line with per-stage ms.
- [ ] Run the 14 example queries from `docs/PRD.md` and record p50/p95 per stage in
      `docs/BASELINE_LATENCY.md`. This is the number every later phase is measured against.
- [ ] `sql_fallback_entered` count is visible. Note how many were caused by
      `endpoint_selection` returning `None` from an exception rather than a genuine no-match.

---

## Phase 1 — Collapse the LLM calls (biggest single win)

**Target: 60–120s → 8–15s.** No new infrastructure. Highest value per line changed.

### 1a. Kill the complexity judge LLM call

Delete `reasoning/complexity_judge.py`. Replace with a pure function in
`reasoning/model_selector.py`:

```python
SONNET = settings.bedrock_model_id
HAIKU  = settings.bedrock_model_id_fast

def pick_model(intent: int, payload_tokens: int) -> str:
    if intent in (5, 7) or payload_tokens > 1200:
        return SONNET
    return HAIKU
```

Estimate `payload_tokens` as `len(json.dumps(payload)) // 4`. Do not call a model to
decide which model to call.

### 1b. Disable Gemini thinking on all routing/classification calls

In every Gemini call used for classification, endpoint selection, or titling:

```python
config=GenerateContentConfig(
    temperature=0,
    max_output_tokens=200,          # was 400
    thinking_config=ThinkingConfig(thinking_budget=0),
)
```

Thinking is on by default on 2.5 Flash and adds meaningful latency to what is a
classification task. Leave thinking enabled only for `answer_directly` conversational
generation if quality regresses without it.

### 1c. Fix the exception handler in `endpoints/endpoint_selector.py`

The current `except Exception: return None, 0, 0` escalates transient failures into the
NL-SQL path. Replace with:

```python
except Exception as e:
    logger.warning("endpoint selection transient failure: %s", e,
                   extra={"query": query})
    METRICS.router_transient.inc()
    fb = keyword_endpoint_fallback(query, org_id, today, user_id=uid)
    if fb:
        return normalize_endpoint_params(fb, org_id, today, user_id=uid), 0, 0
    return None, 0, 0     # genuine no-match only
```

A routing failure must never be indistinguishable from "no endpoint exists."

### 1d. Restructure the selector prompt for prefix caching

In `API_SELECTOR_SYSTEM_PROMPT`, move `{catalog}` (the large stable block) to the **top**,
and all variable fields (`{today}`, `{org_id}`, `{user_id}`, date anchors, `{question}`)
to the **bottom**. Also remove the duplicate question — it is currently interpolated into
the prompt *and* passed as `user_text` to `call_gemini`. Pass it once, as user content.

### 1e. Stream Bedrock output

In `reasoning/bedrock_client.py`, add `invoke_model_with_response_stream` alongside the
existing `invoke_model`. Wire the streaming variant into the SSE path in `api/routes.py`,
emitting `token` events. Keep `invoke_model` for the synchronous `POST /api/v1/query`.

### 1f. Cap the narration

In `reasoning/claude_reasoner.py`:

- Truncate the payload to 2000 tokens before it enters the prompt. If truncated, append
  `"[payload truncated — N of M rows shown]"` so the model knows not to claim completeness.
- Replace `ANALYST_SYSTEM_PROMPT` with the version in **Appendix B** of this spec.
- Set `max_tokens` to 400.

### Acceptance criteria

- [ ] LLM call count per Right-Path query drops from 5–6 to 3 (classify, select, narrate).
- [ ] `docs/BASELINE_LATENCY.md` re-run shows complex-query p95 under 20s.
- [ ] Time-to-first-token on `/api/v1/query/stream` under 2.5s.
- [ ] `router_transient` failures no longer correlate with `sql_fallback_entered`.
- [ ] All existing tests in `tests/unit/` pass unchanged.

---

## Phase 2 — Fast router (remove the LLM from common queries)

**Target: simple queries under 1s.**

### Create `src/gemini_brain/router/dates.py`

Deterministic, timezone-aware date resolution. **Use `Asia/Dubai`, not server-local time** —
the server runs in `ap-south-1` (UTC+5:30) while orgs are UAE (UTC+4), so `date.today()`
resolves the wrong month around midnight.

```python
ORG_TZ = ZoneInfo("Asia/Dubai")

@dataclass(frozen=True)
class Window:
    date_from: date
    date_to: date

def today(tz=ORG_TZ) -> date: ...
def resolve(phrase: str | None, anchor: date | None = None) -> Window: ...
```

Support: `this month`, `last month`, `this quarter`, `last quarter`, `this year`, `ytd`,
`last year`, `last N months`, `last N days`, a bare 4-digit year, `Q1..Q4 YYYY`.
Default to `this year` on anything unrecognised. Unit-test every phrase.

### Create `src/gemini_brain/router/fast_router.py`

Promote the QUICK REFERENCE block from `endpoint_selector.py` and the rules in
`keyword_fallback.py` into an ordered regex table that runs **before** any LLM call.

```python
RULES: list[tuple[re.Pattern, str, dict]] = [
    (re.compile(r"\b(total (sales|revenue|income)|how much (income|revenue))\b", re.I), "income_total", {}),
    (re.compile(r"\b(total (expenses?|spending)|total bills)\b", re.I), "expense_total", {}),
    (re.compile(r"\b(p&l|profit and loss|net profit|income statement)\b", re.I), "profit_loss", {}),
    (re.compile(r"\b(balance sheet|assets and liabilities)\b", re.I), "balance_sheet", {}),
    (re.compile(r"\b(cash flow statement)\b", re.I), "cash_flow", {}),
    (re.compile(r"\b(cash forecast|projected cash|cash runway)\b", re.I), "cash_forecast", {}),
    (re.compile(r"\b(who owes us|overdue invoices?|aging report|receivables?)\b", re.I), "ar_aging", {}),
    (re.compile(r"\b(customer balances?|outstanding (customer|receivable))\b", re.I), "customer_balance_summary", {}),
    (re.compile(r"\b(top customers?|sales by customer)\b", re.I), "sales_by_customer", {}),
    (re.compile(r"\b(expenses? by category|spending breakdown)\b", re.I), "expense_by_category", {}),
    (re.compile(r"\b(cash balance|bank balance|how much cash)\b", re.I), "bank_accounts", {}),
    (re.compile(r"\b(uncategori[sz]ed)\b", re.I), "uncategorized_transactions", {}),
    (re.compile(r"\b(business health|health check|how are we doing)\b", re.I), "dashboard_overview", {}),
]

PERIOD = re.compile(
    r"\b(this|last|previous|current)\s+(month|quarter|year)\b|"
    r"\b(ytd|mtd|qtd)\b|\blast\s+(\d+)\s+months?\b|\b(20\d{2})\b", re.I)

def match(query: str) -> ToolCall | None: ...
```

Wire it in as a pre-step in `orchestrator/gemini_brain_runner.py`. On a hit, skip both
`intent_classifier` and `endpoint_selector` entirely.

Log `router_source` as `fast` / `llm` / `fallback` on every query.

### Acceptance criteria

- [ ] `fast_router` hit rate above 40% on the PRD example query set.
- [ ] A fast-router hit makes zero Gemini calls before the data fetch.
- [ ] `router/dates.py` has unit tests covering every supported phrase, including the
      timezone boundary case (23:30 IST on the last day of a month).

---

## Phase 3 — Tool registry, formatters, async client, cache

**Target: complex queries under 8s; repeat queries near-instant.**

### 3a. Async HTTP client

Rewrite `api_client/accutax_client.py` from `requests` to a module-level
`httpx.AsyncClient` with connection pooling:

```python
_client = httpx.AsyncClient(
    base_url=settings.accutax_base_url,
    timeout=httpx.Timeout(6.0, connect=2.0),
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    headers={"Authorization": f"Bearer {settings.accutax_auth_token}"},
)
```

Keep `extract_data()` envelope unwrapping exactly as-is. Close the client on FastAPI
shutdown. Lower `HTTP_TIMEOUT` from 8.0s to 6.0s.

### 3b. Tool registry

Create `src/gemini_brain/tools/`:

```
tools/
  registry.py      # ToolSpec dataclass, REGISTRY dict, gemini_declarations()
  schemas.py       # Pydantic param models — one per tool
  handlers.py      # async handler per tool
  formatters.py    # deterministic renderers for narrate=False tools
```

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str                  # what Gemini sees — see Appendix A guidance
    params: type[BaseModel]
    handler: Callable[..., Awaitable[dict]]
    narrate: bool                     # False = formatter only, no Bedrock call
    formatter: str
    intent: int                       # for pick_model()
    cache_ttl: int = 300
    timeout: float = 6.0
```

Every param model implements `to_query(ctx: RequestCtx) -> dict`. **All the camelCase /
string-type quirks currently living in `param_normalizer.py` and in the RULES block of the
selector prompt move into these methods.** Delete them from the prompt.

```python
class IncomeTotalParams(BaseModel):
    period: str = "this year"
    filter_type: Literal["YEARLY","QUARTERLY","MONTHLY"] = "YEARLY"
    def to_query(self, ctx):
        w = dates.resolve(self.period)
        return {"user_id": str(ctx.user_id),        # snake_case string
                "filter_year": str(w.date_to.year), # 4-digit string
                "filter_type": self.filter_type}

class InvoiceListParams(BaseModel):
    period: str = "this year"
    status: Literal["paid","unpaid","overdue","all"] = "all"
    limit: int = Field(20, ge=1, le=100)
    def to_query(self, ctx):
        w = dates.resolve(self.period)
        q = {"userId": ctx.user_id,                 # camelCase for /income/list
             "limit": self.limit,
             "start_date": w.date_from.isoformat(),
             "end_date": w.date_to.isoformat()}
        if self.status != "all": q["status"] = self.status
        return q

class JournalEntryParams(BaseModel):
    period: str = "this year"
    limit: int = Field(20, ge=1, le=100)
    def to_query(self, ctx):
        return {"userId": ctx.user_id,              # both camelCase here
                "organizationId": ctx.org_id,
                "limit": self.limit}

class ItemListParams(BaseModel):
    search: str | None = None
    sort_by: Literal["price","name","created"] | None = None
    order: Literal["asc","desc"] = "desc"
    limit: int = Field(20, ge=1, le=100)
    def to_query(self, ctx):
        q = {"user_id": str(ctx.user_id), "limit": self.limit}  # MUST be string
        if self.search: q["search"] = self.search
        if self.sort_by: q["sort_by"], q["order"] = self.sort_by, self.order
        return q
```

**No param model contains `org_id` or `user_id`.** They are injected from `ctx` inside
`to_query()`. This is what makes cross-tenant access inexpressible by the LLM.

### 3c. Register the tools

Full list in **Appendix A**. Mark `narrate=False` on every pure-lookup tool
(`contact_search`, `contact_count`, `item_list`, `item_search`, `bank_accounts`,
`uncategorized_transactions`, `invoice_list`, `projects_list`) — these render a table and
return without touching Bedrock.

### 3d. Formatters

`formatters.py` renders markdown tables from API payloads. One renderer per shape:
`kv_summary`, `row_table`, `aging_buckets`, `account_tree`. Format amounts as
`AED 1,234,567.00`. This is the entire response path for `narrate=False` tools.

### 3e. Result cache

Create `cache/result_cache.py` and `cache/versions.py`.

Key: `f"{org_id}:{tool}:{sha256(sorted_params_json)}:{data_version}"`.

`data_version` is a per-org counter in Redis. Since you cannot yet hook Accutax writes,
start with TTL-based expiry (`cache_ttl` on the ToolSpec, default 300s) and structure the
key so a real `data_version` drops in later without a rewrite. Request it from the backend
team in parallel.

### 3f. New pipeline in `orchestrator/gemini_brain_runner.py`

```python
async def run(query: str, ctx: RequestCtx) -> AsyncIterator[Event]:
    with ctx.trace.stage("fast_route"):
        call = fast_router.match(query)

    if call is None:
        with ctx.trace.stage("llm_route"):
            call = await llm_router.route(query, ctx)
        yield Event("classification", {"tool": call.name})

    if call.name == "unsupported":
        yield Event("final_result", {"answer": CAPABILITY_MESSAGE}); return
    if call.name == "answer_directly":
        async for tok in gemini_direct(query, ctx): yield Event("token", tok)
        return

    spec = REGISTRY[call.name]
    params = spec.params(**call.params)               # Pydantic gate

    key = cache_key(ctx.org_id, spec.name, params)
    data = await result_cache.get(key)
    if data is None:
        with ctx.trace.stage("api_call", tool=spec.name):
            data = await asyncio.wait_for(spec.handler(params, ctx),
                                          timeout=spec.timeout)
        await result_cache.set(key, data, spec.cache_ttl)

    table = formatters.render(spec.formatter, data)
    yield Event("data_table", table)                  # UI paints immediately

    if not spec.narrate:
        yield Event("final_result", {"answer": table}); return

    payload = compact(data, max_tokens=2000)
    model = pick_model(spec.intent, estimate_tokens(payload))
    with ctx.trace.stage("narration", model=model):
        async for tok in bedrock.stream(NARRATION_PROMPT, query, payload, model):
            yield Event("token", tok)
```

Note `data_table` is emitted **before** narration begins. Update `ui/src/components/
ResponseView.jsx` to render it on arrival rather than waiting for `final_result`.

### 3g. LLM router

Create `router/llm_router.py` using Gemini **function calling**, not JSON-in-prompt.
System prompt in **Appendix B**. Register `answer_directly` and `unsupported` as real
tools so every response is a function call — this lets you delete `utils/json_parser.py`
from the routing path entirely.

Set `tool_config=ToolConfig(function_calling_config=FunctionCallingConfig(mode="ANY"))`.

Put behind flag `USE_TOOL_ROUTER` (default `false`). Ship in shadow mode first: run both
the new router and the legacy selector, log disagreements to
`docs/ROUTER_DISAGREEMENTS.md`, serve the legacy result. Flip to the new router only once
disagreements are reviewed and clean.

### Acceptance criteria

- [ ] `narrate=False` tools complete in under 1s with zero Bedrock calls.
- [ ] Complex-query p95 under 8s.
- [ ] Cache hit returns in under 200ms.
- [ ] No param schema anywhere contains `org_id` or `user_id` — assert this in a test that
      iterates `REGISTRY` and inspects `model_fields`.
- [ ] `tests/test_tenant_isolation_api.py` still passes.
- [ ] Shadow-mode disagreement rate under 5% before flipping `USE_TOOL_ROUTER`.

---

## Phase 4 — Analytical SQL functions

**Target: the three complex queries with no API coverage, under 5s.**

Three of the five complex PRD queries have no REST endpoint. Write them as Postgres
functions — hand-written, reviewed, `EXPLAIN ANALYZE`-tuned. Not generated.

Create `sql/functions/` with migration files:

1. `fn_project_expense_rollup(p_org int, p_from date, p_to date)`
   → project name, vendor contact name, bank account used, txn count, total spend
2. `fn_inventory_movement(p_org int, p_from date, p_to date)`
   → item, warehouse, units sold from income lines, units dispatched from delivery notes
3. `fn_gl_profitability(p_org int, p_from date, p_to date)`
   → chart_of_accounts account_type joined to income and expense totals, net margin

Requirements for each:

- `LANGUAGE sql STABLE PARALLEL SAFE`
- `p_org` as a **function parameter**, never string-interpolated
- `LIMIT` enforced inside the function
- `EXPLAIN ANALYZE` output committed alongside, plus any index added to support it
- Register as tools in `REGISTRY` with `narrate=True`

### Database hardening

- Create role `ai_reader NOLOGIN`, grant `SELECT` only on the tables these functions touch.
- Enable RLS on every tenant table listed in `sql_engine.py`'s isolation list
  (`contacts`, `income`, `expense`, `items`, `bank_accounts`, `chart_of_accounts`,
  `customer_payment`, `supplier_payments`, `projects`, `organizations`) with a policy of
  `USING (organization_id = current_setting('app.current_org')::int)`.
- Per request: `SET LOCAL app.current_org = ...; SET LOCAL statement_timeout = '10s';`
  inside a read-only transaction.
- Point the connection at a **read replica**, not the OLTP primary.

Tenant isolation must be enforced by the database, not by SQL rewriting.

### Acceptance criteria

- [ ] All three functions return in under 2s on production-scale data.
- [ ] A test asserts that querying as `ai_reader` with `app.current_org = 44` returns zero
      rows belonging to any other organization.
- [ ] `EXPLAIN ANALYZE` shows index usage, no sequential scan on the large tables.

---

## Phase 5 — Demote and harden NL-to-SQL

Only after Phases 1–4 are merged and stable in production for one week.

`sql_fallback/` is **kept**. It stops being a silent escalation path and becomes an
explicit, budgeted, database-isolated last resort. The engine itself
(`sql_engine.py`, `sql_safety.py`, `fast_path.py`, `answer_cleaner.py`,
`cost_optimizer.py`, `db_connection.py`) is retained as-is except for the changes below.

### 5a. One entry point only

Today NL-SQL is entered whenever endpoint selection returns `None` — including when it
returned `None` because of a timeout. That is the single biggest cause of the slow tail.
After this phase, there is exactly one route in:

```python
if call.name == "unsupported":
    if not settings.ENABLE_SQL_FALLBACK:
        yield Event("final_result", {"answer": CAPABILITY_MESSAGE}); return

    yield Event("sql_fallback", {"status": "no_tool_matched"})
    yield Event("status", {"message": "This one needs a deeper lookup — one moment."})
    try:
        with ctx.trace.stage("sql_fallback"):
            result = await asyncio.wait_for(
                sql_fallback.run(query, ctx), timeout=settings.SQL_FALLBACK_BUDGET_S)
    except (asyncio.TimeoutError, SQLFallbackError):
        METRICS.sql_fallback_failed.inc()
        yield Event("final_result", {"answer": CAPABILITY_MESSAGE}); return
```

Explicitly **not** entry points any more: a Gemini transient failure, an API 4xx/5xx, an
API timeout, a Pydantic validation error, an empty result set from a matched tool. Each of
those returns its own error or empty-state message. An empty result is a real answer —
"no unpaid invoices" is correct, not a reason to go generate SQL.

### 5b. Hard budget, no retry loop

- `SQL_FALLBACK_BUDGET_S = 20` covering the whole fallback including generation, execution,
  and synthesis.
- **At most one regeneration attempt** on a SQL error, inside that same budget. The current
  loop is the reason single queries reach 120s. If attempt two fails, return the capability
  message.
- `SET LOCAL statement_timeout = '10s'` on the execution itself.
- Emit an SSE `status` event before starting, so the UI can show that this path is slower.

### 5c. Run it under the Phase 4 hardening

This is what makes keeping the engine acceptable. Reuse the Phase 4 infrastructure:

- Execute as the `ai_reader` role, `SELECT`-only grants.
- RLS active on every tenant table, `SET LOCAL app.current_org = ctx.org_id` per request.
- Read-only transaction, read replica, never the OLTP primary.

`enforce_tenant_isolation_sql()` in `sql_engine.py` **stays**, but is now defense-in-depth
rather than the sole protection. The database refuses cross-tenant reads regardless of
whether the AST rewriter handled a given CTE, subquery, or lateral join correctly. Add a
test that a deliberately hostile query (`SELECT * FROM income WHERE organization_id = 27`
issued under `app.current_org = 44`) returns zero rows even with the rewriter disabled.

### 5d. Cache and cap

- Cache fallback results through the Phase 3 `result_cache` keyed on the normalized query
  text, so a repeated novel question costs 20s once, not every time.
- Cap returned rows at 200 inside the engine, before synthesis.
- Route synthesis through the same 2000-token payload cap and narration prompt as every
  other tool. No separate prompt path.

### 5e. Circuit breaker and backlog

- Track `sql_fallback_rate` per day. If it exceeds 15% of queries, alert — that means tool
  coverage has a real gap, not that the fallback is doing its job.
- If `sql_fallback_failed` exceeds 40% of fallback attempts over an hour, trip a breaker
  and serve the capability message directly until it resets. A path that mostly fails
  slowly is worse than one that fails fast.
- Log every fallback invocation with the raw query to a table. **This log is the tool
  backlog** — cluster it weekly and turn the top cluster into a registered tool. The
  fallback rate should trend down over time; if it doesn't, the backlog isn't being worked.

### 5f. Cleanup that still happens

The router replacements are superseded and should go:

```
DELETE src/gemini_brain/endpoints/endpoint_selector.py
DELETE src/gemini_brain/endpoints/keyword_fallback.py
DELETE src/gemini_brain/endpoints/param_normalizer.py
DELETE src/gemini_brain/reasoning/complexity_judge.py
DELETE src/gemini_brain/config/api_catalog.py     (superseded by REGISTRY)
```

Keep the `sql_fallback` SSE event in `api/routes.py` and `ui/src/components/
ResponseView.jsx` — it now signals the slow path to the user, which is useful.

Keep `ENABLE_SQL_FALLBACK` as a runtime kill switch, defaulting to `true`. If a client
raises a concern during a security review, it can be turned off without a deploy.

### Acceptance criteria

- [ ] `sql_fallback` is entered only on `unsupported`. Add a test that a Gemini timeout,
      an API 500, and an empty result set each return their own message and never reach it.
- [ ] No fallback query exceeds 20s wall clock, including regeneration.
- [ ] Hostile-query test passes with the AST rewriter disabled — RLS alone blocks it.
- [ ] `sql_fallback_rate` under 15% on a week of production traffic.
- [ ] Circuit breaker trips and recovers correctly under a simulated failure burst.
- [ ] Final `docs/BASELINE_LATENCY.md` re-run: lookups <1s, reports <2.5s, complex <5s,
      fallback <20s.

---

## Appendix A — Tool registry

`org_id` and `user_id` are omitted from every schema by design; they come from `ctx`.

| Tool | Endpoint | Params | Narrate | Intent |
|---|---|---|---|---|
| `profit_loss` | `/report/profit-loss` | period | yes | 3 |
| `balance_sheet` | `/report/balance-sheet` | as_of | yes | 3 |
| `cash_flow` | `/report/cash-flow` | period | yes | 3 |
| `cash_forecast` | `/report/cash-forecast` | months | yes | 5 |
| `ar_aging` | `/report/ar-aging-summary` | as_of | yes | 3 |
| `ap_aging` | `/report/ap-aging-summary` | as_of | yes | 3 |
| `customer_balance_summary` | `/report/customer-balance-summary` | limit, sort | yes | 4 |
| `sales_by_customer` | `/report/sales-by-customer` | period, limit | yes | 4 |
| `expense_by_category` | `/report/expense-by-category` | period | yes | 4 |
| `dashboard_overview` | `/dashboard/web/v3` | — | yes | 7 |
| `income_total` | `/income/total` | period, filter_type | yes | 4 |
| `expense_total` | `/expense/total` | period, filter_type | yes | 4 |
| `invoice_list` | `/income/list` | period, status, limit | **no** | 4 |
| `invoice_find` | `/income/find` | invoice_id | **no** | 4 |
| `bill_list` | `/expense/list` | period, status, limit | **no** | 4 |
| `bill_find` | `/expense/find` | bill_id | **no** | 4 |
| `customer_payments` | `/income/customer-payment/list` | period, limit | yes | 4 |
| `supplier_payments` | `/expense/supplier-payment/list` | period, limit | yes | 4 |
| `contact_search` | `/contact/find` | name, contact_type | **no** | 4 |
| `contact_count` | `/contact/list` | contact_type | **no** | 4 |
| `item_list` | `/item/list` | sort_by, order, limit | **no** | 4 |
| `item_search` | `/item/list` | search | **no** | 4 |
| `bank_accounts` | `/bank/manual/accounts` | — | **no** | 4 |
| `uncategorized_transactions` | `/bank/transactions/uncategorized` | limit | **no** | 4 |
| `chart_of_accounts` | `/chart-of-accounts` | — | **no** | 4 |
| `journal_entries` | `/accounting/journal-entries` | period, limit | yes | 4 |
| `general_ledger` | `/accounting/general-ledger` | period, account_id | yes | 4 |
| `audit_logs` | `/audit-logs` | period, limit, action | **no** | 4 |
| `projects_list` | `/projects/list` | — | **no** | 4 |
| `project_expense_rollup` | SQL fn (Phase 4) | period | yes | 4 |
| `inventory_movement` | SQL fn (Phase 4) | period | yes | 4 |
| `gl_profitability` | SQL fn (Phase 4) | period | yes | 4 |
| `answer_directly` | — (Gemini direct) | — | — | 1,2,6 |
| `unsupported` | — | reason | — | — |

**Write tool descriptions like documentation for a junior developer.** Include what it
returns, three example phrasings, and — most importantly — a negative case, since most
routing errors are neighbour-tool confusion:

```python
description=(
    "Per-customer financial summary: total invoiced revenue, total payments "
    "collected, and net outstanding balance, sorted by revenue or amount owed. "
    "Use for: 'customer breakdown', 'who are our biggest customers', "
    "'which customers owe us money'. "
    "Do NOT use for a single named customer — use contact_search instead."
)
```

---

## Appendix B — Prompts

### Router system prompt (`router/llm_router.py`)

Keep short. Tools go in the `tools` parameter as function declarations, never in the
prompt body. This block is identical on every request, so it caches.

```
You route questions for Accutax, a bookkeeping platform for UAE/GCC businesses
(currency AED, VAT 5%).

Call exactly one tool. Never answer from your own knowledge when a tool exists.

For date ranges, pass the user's phrase verbatim as `period`: "this month",
"last quarter", "last 6 months", "2025". Do not compute dates yourself.

Never supply an organization id or user id — the system injects those.

If the question is about how to use the app, where to find a screen, or an
accounting definition, call answer_directly.
If nothing fits, call unsupported with a one-line reason.

Emit only the tool call.
```

### Narration prompt (`reasoning/claude_reasoner.py`)

```
You are a financial analyst for Accutax, reporting to a business owner in the UAE.
Currency is AED. VAT is 5%.

The DATA block is authoritative and already fully aggregated by the system.

- Never recompute, re-sum, re-average, or re-derive any figure. Quote the numbers
  exactly as given.
- If a figure the user asked for is not present in DATA, say it is not available.
  Never estimate or infer it.
- If DATA is marked truncated, say the figures cover only the rows shown.
- A table of this data is already displayed above your response. Do not reproduce it.
- Open with the direct answer in one sentence.
- Then at most three short bullets: what stands out, what changed, what to watch.
- Format amounts as AED 1,234,567.00.
- Maximum 120 words.
```

The word cap is a real latency lever — output tokens dominate generation time.

---

## Appendix C — Capability message for `unsupported`

```
I can't answer that one yet. Here's what I can pull for you right now:

**Reports** — P&L, balance sheet, cash flow, cash forecast, AR/AP aging
**Customers** — revenue breakdown, outstanding balances, payment history
**Expenses** — by category, by project, supplier payment audit
**Inventory** — item lookup, stock levels, sales and dispatch movement
**Banking** — account balances, uncategorized transactions

Want any of those?
```

---

## Appendix D — Open items for the backend team

Small changes on their side, disproportionate gains on ours. Raise in parallel with Phase 1.

1. `limit`, `sort_by`, `order` on `/item/list` and `/contact/list` — turns "top 5 by price"
   from a full-catalog fetch into one call.
2. A `status` filter on `/income/total` — makes "total unpaid across all invoices" a single
   aggregate instead of paginating every invoice.
3. Confirm whether `/report/customer-balance-summary` returns contact email/phone. If yes,
   PRD complex query #1 is a single endpoint and needs no SQL function.
4. A per-organization `data_version` or `updated_at` marker, for correct cache invalidation.
5. A read-replica connection string for the Phase 4 functions.

## Appendix E — Things not to do

- Do not let the LLM emit SQL on the normal path. SQL generation happens only inside
  `sql_fallback/`, only on an explicit `unsupported` classification, and only under the
  Phase 4 role/RLS/replica hardening.
- Do not widen the fallback's entry conditions "temporarily" to cover a failing tool. Fix
  the tool, or add one.
- Do not let the LLM emit `organization_id` or `user_id` in any form.
- Do not add a retry loop around a model call inside the request path — fail to the
  fast router or to `unsupported`.
- Do not pass more than 2000 tokens of payload to Bedrock, however tempting.
- Do not chain two tools to combine their results arithmetically. If a question needs two
  datasets joined, that is a signal to write a third tool.
- Do not change the shape of existing SSE events; only add new ones.
- Do not skip Phase 0. Every later claim depends on that baseline.
