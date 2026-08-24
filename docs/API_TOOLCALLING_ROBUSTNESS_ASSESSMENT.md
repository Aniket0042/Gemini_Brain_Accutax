# Is the API Tool-Calling Approach Robust Enough? — Coverage Assessment & Improvement Plan

**Version:** 1.0
**Date:** 2026-08-20
**Companion docs:** [`END_TO_END_FLOW_BY_INTENT.md`](./END_TO_END_FLOW_BY_INTENT.md) (how each intent
actually flows) · [`ROBUSTNESS_AND_GRACEFUL_DEGRADATION_SPEC.md`](./ROBUSTNESS_AND_GRACEFUL_DEGRADATION_SPEC.md)
(error handling) · [`DATABASE_DEEP_DIVE.md`](./DATABASE_DEEP_DIVE.md) (what the data actually contains)

---

## 0. tl;dr

**Question asked:** can the current endpoint-selection / tool-calling approach reliably handle
70–80% of realistic user queries on its own?

**Answer:** Not yet, and — more importantly — **nobody can currently prove it does or doesn't**,
because there is no evaluation harness measuring it. Based on a full read of the routing code (not
a live benchmark — see §4 for why), the honest estimate is:

- **On narrow, canonical phrasing that matches the system's own "USE FOR" keyword hints** (the kind
  of question a demo would ask): likely **60–75%** single-call success.
- **On realistic, organically-phrased user queries** — synonyms, compound questions, follow-ups,
  typos, questions naming a specific customer/vendor/date range in prose: likely **30–50%**.
- **A hard ceiling exists below any routing fix**: per `DATABASE_DEEP_DIVE.md`, ~58.6% of
  organizations have **zero transactional data at all**, and even active organizations have
  structurally empty banking, VAT, payments, and audit modules. No amount of routing improvement
  raises coverage above what the underlying data supports for a given tenant and question category.

The gap is not one bug — it's an architecture that grew **three independent, undocumented, and
inconsistently-overlapping routing layers**, each with its own copy of "which endpoints exist and
what do they need," with no shared validation and no automated measurement of how often any of them
actually gets it right. §5 gives a phased plan to close it, with the highest-leverage fix being one
that's *already half-written and sitting unused in the codebase*.

---

## 1. What "the API approach" actually is today

There isn't one tool-calling approach — there are **three**, operating in sequence, each built at a
different time with a different mechanism, and none of them aware of the others' failure modes.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ LAYER 1 — fast_route()          18 hardcoded regex rules, 0 LLM calls   │
│           router/fast_router.py  Deterministic, fast, narrow coverage   │
└───────────────────────────┬───────────────────────────────────────────┘
                             │ miss
┌───────────────────────────▼───────────────────────────────────────────┐
│ LAYER 2 — select_endpoint()     1 Gemini call, free-text JSON parsing  │
│           endpoint_selector.py  70-endpoint text catalog in the prompt │
│           + keyword_endpoint_fallback()  6 more hardcoded rules        │
└───────────────────────────┬───────────────────────────────────────────┘
                             │ API call fails / no endpoint / empty
┌───────────────────────────▼───────────────────────────────────────────┐
│ LAYER 3 — SQL fallback tool loop     Bedrock structured function       │
│           sql_fallback/sql_engine.py  calling, 4 tools, 44 finance     │
│           (external coordinator_agent)  tasks, up to 5 iterations      │
│           + try_fast_path()  12 MORE hardcoded regex rules (4th list!) │
└─────────────────────────────────────────────────────────────────────────┘
```

Plus a **fourth, unused implementation** sitting in the codebase, fully written, never called:
`router/llm_router.py`'s `route_with_gemini()` — proper Gemini structured function-calling using
`gemini_declarations()` built from the 30-tool `REGISTRY`. Nothing in `gemini_brain_runner.py`
imports `router.llm_router`. This matters enormously for §5 — the best fix for Layer 2's core
weakness already exists and is dead code.

### 1.1 Layer 1 — the fast router

**Mechanism:** 18 compiled regexes (`FAST_ROUTER_RULES`, `fast_router.py:70-158`), each mapping a
phrase pattern straight to an endpoint string and a hardcoded parameter-building branch. Zero LLM
involvement, zero ambiguity — either a regex matches or it doesn't.

**Strength:** for the ~18 canonical phrasings it covers, this is the most reliable layer in the
entire system — deterministic, sub-100ms, free.

**Weakness:** it is a **finite, hand-maintained list**. It has no generalization — *"total revenue"*
matches, *"how much money did we make"* does not. Coverage is exactly as wide as whoever last edited
this file thought to add.

### 1.2 Layer 2 — the Gemini endpoint selector

**Mechanism:** one Gemini call per query (`select_endpoint()`, `endpoint_selector.py:66-127`) against
a **70-endpoint plain-text catalog** (`API_CATALOG`, `config/api_catalog.py`) embedded in the system
prompt. The model is asked to return raw JSON: `{"endpoint": "...", "query_params": {...}}`. This
JSON is extracted from free-form model output using `extract_json()`
(`utils/json_parser.py:19-57`) — markdown-fence stripping, then a **first-`{`-to-last-`}` substring
extraction** as the final fallback.

This is *not* Gemini's structured function-calling / tool-use mode. It is prompt-engineered free-text
generation, parsed after the fact with string surgery. Every failure mode below stems from this one
design choice.

### 1.3 Layer 3 — the SQL fallback tool loop

**Mechanism:** when Layer 1 and 2 both come up empty (or the API call itself fails), the query goes
to an external `coordinator_agent` pipeline (imported from a hardcoded, machine-specific path —
`sql_engine.py:35`) that runs a genuine Bedrock structured tool-calling loop: 4 declared tools
(`schema_agent`, `finance_agent`, `tax_agent`, `reasoning_agent`), `finance_agent` alone exposing
**44 distinct task types** — all implemented (verified: 44 handler functions exist in
`agents/finance_agent.py`, one per declared enum value, not stubs). Up to 5 iterations, 90-second
budget (`ENGINE_MAX_ITERATIONS`, `ENGINE_TIME_BUDGET_SECONDS`).

**Strength:** this is architecturally the *best-built* of the three layers — real structured tool
calling, a genuinely broad task vocabulary, and a model that can chain multiple tool calls to answer
compound questions Layers 1–2 can't.

**Weakness:** it is also the **slowest and most expensive** path (multiple LLM round trips vs. one),
and it has its own, fourth, independent fast-path regex list (`fast_path.py`'s `_FAST_PATH`, 12
patterns) duplicating a subset of Layer 1's coverage with different wording and different endpoint
names (task names, not REST paths).

### 1.4 Why this matters for "70–80% coverage"

A query's fate depends on **which layer happens to catch it first**, and the three layers were never
designed as a coherent whole:

- The same real-world question can be phrased in a way that hits Layer 1 (instant, free, reliable),
  a slightly different way that hits Layer 2 (one LLM call, moderate reliability — see §2), or a
  third way that falls all the way to Layer 3 (multiple LLM calls, slow, but the most semantically
  flexible).
- **There is no measurement of which layer actually answers what fraction of real traffic**, so
  "70–80%" is currently an assertion, not a number anyone can check.

---

## 2. Verified fragility findings

Each finding below is either a direct code-read with a cited line, or something reproduced by
executing the actual function in this session — marked **[verified]**.

### 2.1 — Free-text JSON extraction breaks on ordinary model chatter **[verified]**

`extract_json()`'s final fallback is `raw.find("{")` to `raw.rfind("}")` — first opening brace to
**last** closing brace in the entire response. Reproduced:

```python
>>> extract_json('Sure! {"endpoint": null, "reason": "no_api_match"} '
...               'Let me know if that helps! (see docs {ref})')
None
```

The genuinely-valid JSON object is present and well-formed; the extraction fails because the model's
trailing pleasantry happens to contain an unrelated `{ref}`, and the substring from the first `{` to
that *later* `}` is not valid JSON. `select_endpoint()` catches this as an exception, falls through to
`keyword_endpoint_fallback()` (6 patterns — a much narrower net than the 70-endpoint catalog Gemini
was actually asked to choose from), and if that also misses, returns `(None, 0, 0)` — the query is
declared to have **no matching endpoint at all**, despite the model having picked one correctly.

This is not a rare edge case — it reproduces with any response where the model adds a parenthetical,
a follow-up offer, or a code-style example after the JSON. Nothing in the system prompt forbids this;
`API_SELECTOR_SYSTEM_PROMPT` says *"Return ONLY valid JSON — no markdown, no explanation"*
(`endpoint_selector.py:29`) but has no mechanism to enforce it.

### 2.2 — No endpoint-existence validation before the network call

`select_endpoint()` parses whatever endpoint string the model returns and hands it directly to
`call_api()`. There is no check against `API_CATALOG`'s actual endpoint list before the HTTP call is
made. A hallucinated-but-plausible path (`/report/revenue-summary`, `/income/summary`) is
indistinguishable, at this layer, from a real one — it simply gets a 404 from the live API and (in
the current, unpatched code) falls through to the SQL fallback with no record of *why* the API layer
failed. This wastes one full network round trip per hallucination and gives no observability into
how often it happens.

### 2.3 — Five separate, hand-maintained copies of "which phrase means what"

The same routing knowledge — *"'top N customers' means the `top_customers` task/endpoint"* — is
independently encoded in five places, with no shared source of truth and no test asserting they
agree:

| # | Location | Form |
|---|---|---|
| 1 | `fast_router.py` `FAST_ROUTER_RULES` | 18 regex → REST endpoint |
| 2 | `keyword_fallback.py` | 6 keyword-list → REST endpoint |
| 3 | `api_catalog.py` "QUICK REFERENCE" block | 10 plain-text hints inside the LLM prompt |
| 4 | `fast_path.py` `_FAST_PATH` | 12 regex → SQL-engine task name |
| 5 | `coordinator_agent.py` `TOOL_DEFINITIONS` enum description | 44 task names, free-text described |

Verified concretely: `fast_path.py`'s list does **not** include an `income_total` /
`expense_total` pattern at all (checked against its 12 entries — `top_customers`, `top_vendors`,
`bank_balances`, `ar_aging`, `ap_aging`, `overdue_invoices`, `customer_overdue_summary`,
`invoice_status_summary`, `expense_by_category`, `monthly_revenue_trend`, `chart_of_accounts` — no
revenue/income total pattern), even though this is the single most common financial question
("total revenue", "how much did we make") and **is** covered by both `fast_router.py` and
`keyword_fallback.py`. If a "total revenue" query somehow reaches Layer 3's fast-path check (e.g.
after Layers 1–2 both fail on an org where the API is down), it has to go through the full 5-iteration
tool-calling loop instead of the fast path — a correctness gap invisible unless someone reads all
five lists side by side, which this document is the first artifact to do.

### 2.4 — Parameter correctness has almost no safety net

`normalize_endpoint_params()` (`endpoints/param_normalizer.py`) patches exactly **2 of the ~70
catalog endpoints** (`/income/total`, `/expense/total`) — the two the system prompt itself calls out
as endpoints *"Gemini consistently gets wrong."* The other ~68 endpoints depend entirely on the model
getting parameter names right on the first attempt, including landmines the catalog text documents
but does not enforce:

- `/income/list` needs `userId` (camelCase) — `/item/list` needs `user_id` (snake_case, and must be
  a *string*, not an integer) — `/accounting/journal-entries` needs *both* `userId` and
  `organizationId` in camelCase.
- Per `DATABASE_DEEP_DIVE.md §10`, the catalog itself has a **wrong value baked in**: it documents
  `contact_type_id` 1/2/3 as "vendor," while the live database only defines `4 = customer` and
  `5 = vendor`. Any query routed through this catalog entry for vendor filtering will silently return
  the wrong contact type — a bug in the routing *specification*, not just the routing *logic*.

There is no test suite that calls each of the 70 catalog endpoints and asserts the parameters
actually produced are valid — the entire parameter-correctness surface is unverified except for the
2 endpoints `param_normalizer.py` patches.

### 2.5 — No shared context between classification and endpoint selection

`classify_intent()` and `select_endpoint()` are two fully independent Gemini calls
(`gemini_brain_runner.py:658-668`) — the second call does not receive the first call's `type` or
`reason` as input, and neither receives the other's confidence or any signal that they should agree.
A Type-4 classification and a "no matching endpoint" result from the selector are not cross-checked
against each other in any way; they simply both happen, and if the selector fails, the classifier's
work is discarded with no attempt to reconcile.

### 2.6 — The live-API tier is currently unreachable, and nothing alerts on it

The shipped `.env` `ACCUTAX_AUTH_TOKEN` decodes (verified in this session) to:

```json
{"userId": 18, "organization_id": 5, "iat": 1787138940, "exp": 1787225340}
```

`exp` = 2026-08-20 11:29 UTC. Given "today" for this document is 2026-08-20, this token is either
already expired or has hours left at most, at which point every single REST API call fails with a
401 and every Type 3/4/5 query silently falls through to the SQL-fallback tier for the *entire*
system, indiscriminately, until someone notices and rotates the token. `organization_id: 5` in the
token also **does not exist** as a live organization (`DATABASE_DEEP_DIVE.md §10`) — a second,
independent misconfiguration layered on top of the expiry.

There is no health check, no startup validation, and no alert tied to token expiry anywhere in the
codebase (`/api/v1/health/models` checks Gemini, Bedrock, the API's base *reachability*, and
Postgres — not whether the stored bearer token itself is valid or expiring soon). This means Source A
(§1, `END_TO_END_FLOW_BY_INTENT.md`) can go dark for hours with zero operator visibility.

### 2.7 — Two better implementations already exist and are unused

- `router/llm_router.py`: real Gemini structured function-calling via `gemini_declarations()`. Not
  imported anywhere outside its own module and its own test file.
- `sql_fallback/sql_safety.py`'s `assert_read_only()`: a keyword-based guard against
  INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE in generated SQL. Never called — `finance_agent.py`'s
  `_task_execute_sql` only adds a `LIMIT 100` if missing; it does not check the SQL is read-only at
  all before executing it. The only isolation applied to model-generated SQL is tenant-scoping
  (`enforce_tenant_isolation_sql`), which rewrites `organization_id` filters — it does not check the
  statement type.

Both are directly relevant to §5's fix list: one closes the single biggest reliability gap (§2.1,
§2.2), the other closes a real, if narrow, safety gap.

---

## 3. The two independent ceilings on coverage

"Will this handle 70–80% of queries" has to be answered against **two separate limits that don't
move together**:

```
                    ┌─────────────────────────────────┐
                    │   ROUTING CEILING                │   ← this document, §1-2
                    │   Can the system find the right  │
                    │   endpoint/task and valid params  │
                    │   for a well-formed question?     │
                    └─────────────────┬─────────────────┘
                                       │  (even a perfect router still hits this)
                    ┌─────────────────▼─────────────────┐
                    │   DATA CEILING                     │   ← DATABASE_DEEP_DIVE.md §8-9
                    │   Does the organization being       │
                    │   queried actually have the data    │
                    │   the question needs?               │
                    └─────────────────────────────────────┘
```

From `DATABASE_DEEP_DIVE.md`:

- **58.6% of organizations** picked at random have **zero transactions of any kind** — every
  financial question returns "no data," regardless of routing quality, while master-data questions
  (chart of accounts, contact lists, item catalogs) answer fine.
- Even among the ~41.5% of "active" organizations, entire question categories are **structurally
  unanswerable for all of them**: bank/cash balances, customer and supplier payments received/made,
  uncategorized bank transactions, VAT/tax liability (near-universally zero), AP due dates (99.93%
  null), inventory movement beyond opening balances, and audit-trail forensics (`old_values`/
  `new_values` 100% null).

**Implication for a 70–80% target:** if the target population is "any random organization asked any
random realistic question," the data ceiling alone likely caps achievable coverage well below 70%,
independent of routing quality — a perfectly-routed query against a module with zero rows still
correctly answers "there is nothing here," which is not the same as *satisfying* the user's
question. If the target population is instead "an organization known to have transactional data,
asked a question from the categories the data actually supports" (revenue, expenses, P&L, GL,
customers, vendors, invoices, items — the `DATABASE_DEEP_DIVE.md §9` ✅ rows), 70–80% is a
realistic and achievable routing-layer target, and the rest of this document is scoped to that
population.

---

## 4. Why this document gives a range, not a measured percentage

An honest robustness assessment has to say plainly what it can and cannot verify without running
live, costed LLM calls against production credentials:

- **What was verified:** every code path, every parsing function's actual behaviour on adversarial
  input (§2.1), every static list's contents (§2.3), the token payload (§2.6), and the full
  implementation of the SQL-fallback task set (§1.3) — all done via static reads and local,
  zero-cost function execution.
- **What was *not* measured, because it requires spending real API credits with no existing
  golden-query benchmark to run them against:** the actual success rate of `select_endpoint()`
  against a representative sample of real user phrasings; the actual JSON-malformation rate in
  practice; the actual hallucinated-endpoint rate; the actual parameter-correctness rate across all
  70 catalog endpoints in a live setting.

This is itself the top-priority finding: **the 70–80% question cannot be answered with confidence by
anyone, today, because nothing in the repository measures it.** §5's first recommended phase exists
specifically to close that gap before any further routing work is prioritized by guesswork.

---

## 5. Improvement plan — phased, ranked by leverage

### Phase A — Build the measurement you don't have (do this first)

**Why first:** every fix below is currently unverifiable. Without a baseline, "did this help" is a
guess, and effort gets spent on the wrong layer.

1. Assemble a **golden query set**: 60–100 real-sounding questions, each hand-labeled with the
   correct `(intent, endpoint_or_task, expected_params)`. Draw them from the `API_CATALOG`'s own
   "USE FOR" hint lists (§1.2) as a starting seed, then deliberately add synonyms, typos, compound
   questions, and 2-turn follow-ups the hints don't cover.
2. Run each query through the real pipeline (Layers 1→2→3) and record: which layer answered it,
   whether the endpoint/task matched the label, whether the params matched, and end-to-end latency.
3. Report one number per layer (Layer 1 hit rate, Layer 2 success | Layer-1-miss, Layer 3 success |
   Layers 1–2 miss) and one combined number. This *is* the 70–80% measurement — everything else in
   this plan is aimed at moving it.
4. Re-run this harness after every phase below. Treat a phase as done only when the number moves.

**Effort:** ~1 day to build the harness + label the query set; near-zero marginal cost to re-run.

### Phase B — Replace free-text JSON with structured function calling

**Why this is the highest-leverage code fix:** it eliminates the entire failure class in §2.1 and
§2.2 in one change, and the hard part is already written.

1. Extend `router/llm_router.py`'s `gemini_declarations()` to cover the **full 70-endpoint catalog**,
   not just the 30 tools currently in `REGISTRY` — either by adding `ToolSpec` entries for the
   missing ~40 endpoints (with at minimum a name, description, and param schema; formatter/narrate
   can default), or by generating declarations directly from `API_CATALOG`'s structured content
   (recommend converting `api_catalog.py`'s big text blob into a small structured table it's
   generated from, so both the text catalog and the tool declarations derive from one source — this
   also directly fixes §2.3).
2. Wire `route_with_gemini()` into `gemini_brain_runner.py` in place of `select_endpoint()`'s
   free-text call, using Gemini's `FunctionCallingConfig(mode='ANY')` (already referenced in
   `llm_router.py`'s own docstring) so the model is **constrained to return only a real, enumerated
   tool name** — a hallucinated endpoint becomes structurally impossible, not just unlikely.
3. Keep `keyword_endpoint_fallback()` as the last-resort safety net for the (much rarer) case of a
   genuine model/API outage, not as the primary recovery path for parse failures it no longer needs
   to cover.

**Expected effect:** removes the JSON-parsing failure mode entirely (§2.1), removes endpoint
hallucination entirely (§2.2), and — because function-calling mode returns a name from a closed
enum plus a typed args object — meaningfully reduces the parameter-shape errors in §2.4, since
Gemini's function-calling layer validates argument types against the declared JSON schema before the
call is even accepted.

### Phase C — Consolidate the five duplicated routing-pattern lists into one

1. Define one declarative rule table: `phrase_patterns → {endpoint_or_task, intent, param_builder}`.
2. Generate `FAST_ROUTER_RULES`, `keyword_endpoint_fallback`'s patterns, `fast_path.py`'s
   `_FAST_PATH`, and the `API_CATALOG` "QUICK REFERENCE" block **from that one table**, rather than
   maintaining four independent copies.
3. Add the missing `income_total`/`expense_total` fast-path entry found in §2.3 as part of this
   consolidation (a two-line fix once the table exists, versus a change that has to be remembered
   to be applied in all five places today).
4. Add a CI check that fails the build if a new endpoint is added to `API_CATALOG` without a
   corresponding entry anywhere in the consolidated table — prevents future drift.

### Phase D — Feed session context into routing, not just narration

Per `END_TO_END_FLOW_BY_INTENT.md §4.3`, extracted conversation state
(`active_year`, `bank_account`, `contact_name`, `last_executed_task`) is computed every turn and
**never read**. Wire it into both `classify_intent()` and `select_endpoint()`/`route_with_gemini()`
as short context ("the user was just discussing: task=profit_and_loss, year=2026") so a follow-up
like *"what about Q2?"* can resolve against what was just asked instead of routing cold. This
directly targets the "compound / follow-up questions" gap called out in §0's lower coverage estimate
for realistic (vs. canonical) phrasing.

### Phase E — Add a bounded self-correction loop

When `call_api()` returns a 4xx or an empty/error-shaped payload (see the resilience spec's
`Outcome` classification for the precise signal to use here), give the routing model **one** more
turn with the failure reason before dropping all the way to the separate Layer-3 tool vocabulary:
*"That endpoint returned `HTTP 404`. Pick a different endpoint from the catalog, or call
`unsupported` if none fits."* Bound this to exactly one retry to avoid trading a reliability problem
for a latency/cost one.

### Phase F — Close the two identified safety gaps

1. Wire `sql_safety.assert_read_only()` into `finance_agent.py`'s `_task_execute_sql` (or the
   equivalent point in this repo once/if that logic is internalized) as a second, independent check
   alongside the existing tenant-isolation rewrite — belt-and-suspenders, not a replacement.
2. Add a startup and periodic check that decodes `ACCUTAX_AUTH_TOKEN`'s `exp` claim and logs/alerts
   when it is within 24 hours of expiry, so §2.6's silent multi-hour tier outage becomes a visible,
   actionable warning instead of a discovery made by reading this document.

### Phase G — Align the coverage target with the data ceiling

Per §3, no routing fix moves the data ceiling. Two independent options, not mutually exclusive:

1. **For demos/evaluation:** constrain the golden query set (Phase A) and any coverage claim to the
   `DATABASE_DEEP_DIVE.md §9` ✅ categories, on the small set of organizations documented in that
   report's §12 as having deep transaction history. This is the fastest way to make a 70–80% target
   both meaningful and achievable.
2. **For a broader claim:** seed the structurally-empty modules (banking, payments, VAT line items,
   inventory movement types beyond opening balance) for a wider set of test organizations — this is
   a data-engineering task, not a routing fix, and should be tracked and estimated separately from
   this plan.

---

## 6. Expected effect per phase (reasoned, not measured — re-verify with Phase A's harness)

| Phase | Primarily fixes | Expected direction of movement |
|---|---|---|
| A | Nothing on its own — establishes the baseline | Converts every other row from a guess into a number |
| B | §2.1, §2.2, most of §2.4 | Largest single jump — removes the dominant parsing/hallucination failure class |
| C | §2.3 | Closes silent coverage gaps between layers (e.g. the missing income-total fast-path entry) |
| D | Follow-up / multi-turn questions (§0's "realistic phrasing" gap) | Raises coverage specifically on the query *shapes* canonical single-call benchmarks under-represent |
| E | Transient API failures that are routing-recoverable, not data-absent | Small but compounding — trades one wasted round trip for one extra, cheap correction attempt |
| F | Safety and operational blast radius (§2.6, §2.7's SQL-safety gap) | Does not move the coverage number; prevents a silent multi-hour outage and closes an unenforced safety check |
| G | The data ceiling (§3) | Reframes what "70–80%" is measured against, or raises the real ceiling via data seeding |

---

## 7. Test matrix / acceptance criteria for this plan

| # | Check | Pass condition |
|---|---|---|
| R01 | Golden query harness exists and runs against the live pipeline | Produces a per-layer hit-rate report on demand |
| R02 | `route_with_gemini()` wired in as the primary Layer-2 mechanism | `select_endpoint()`'s free-text JSON path is no longer on the primary call path |
| R03 | Endpoint hallucination | Feeding the golden harness 10 deliberately odd/adversarial phrasings never returns a `404`-then-fallback trace — either a valid enumerated tool or `unsupported` |
| R04 | Five-list consolidation | A single source file change (adding one pattern) updates fast router, keyword fallback, fast-path, and the catalog hint text without touching four separate files |
| R05 | `income_total`/`expense_total` fast-path gap | `try_fast_path()` matches "total revenue" style phrasing (currently does not) |
| R06 | Session-aware follow-up | *"Show P&L for Q1"* → *"what about Q2?"* resolves to `/report/profit-loss` with a Q2 date window, not a routing miss |
| R07 | Token-expiry alerting | A token within 24h of `exp` produces a logged warning on the existing `/api/v1/health/models` check or an equivalent startup check |
| R08 | `assert_read_only` wired | An LLM-generated `execute_sql` containing `DROP TABLE` or `DELETE FROM` is rejected before reaching the database |
| R09 | Coverage number, re-measured | Phase A's harness score improves after Phases B–D relative to the pre-plan baseline |

---

## Appendix — file:line index for this document's claims

| Claim | File:line |
|---|---|
| `FAST_ROUTER_RULES` (18 entries) | `router/fast_router.py:70-158` |
| `select_endpoint()` free-text JSON call | `endpoints/endpoint_selector.py:66-127` |
| `extract_json()` first-`{`-to-last-`}` fallback | `utils/json_parser.py:47-57` |
| `keyword_endpoint_fallback()` (6 patterns) | `endpoints/keyword_fallback.py` |
| `_FAST_PATH` (12 patterns, no income/expense total) | `sql_fallback/fast_path.py:24-114` |
| `TOOL_DEFINITIONS` / 44 `finance_agent` tasks | external `agents/coordinator_agent.py:267-401`; handlers verified in `agents/finance_agent.py` |
| `ENGINE_MAX_ITERATIONS` / `ENGINE_TIME_BUDGET_SECONDS` | `config/constants.py` |
| `normalize_endpoint_params()` — patches only 2 endpoints | `endpoints/param_normalizer.py:24-40` |
| Wrong `contact_type_id` for vendor in catalog | `config/api_catalog.py` (`GET /contact/list`); ground truth in `DATABASE_DEEP_DIVE.md §10` |
| `router/llm_router.py` — unused structured function-calling implementation | `router/llm_router.py` (no importers outside its own module/tests) |
| `sql_safety.assert_read_only()` — unused | `sql_fallback/sql_safety.py:12-30`; not called from `agents/finance_agent.py:_task_execute_sql` |
| `ACCUTAX_AUTH_TOKEN` expiry / org mismatch | `.env:23`; decoded `exp=1787225340`, `organization_id=5`; `DATABASE_DEEP_DIVE.md §10` |
| Data ceiling (58.6% orgs with zero transactions) | `DATABASE_DEEP_DIVE.md §8` |
| Capability matrix (what's answerable at all) | `DATABASE_DEEP_DIVE.md §9` |
