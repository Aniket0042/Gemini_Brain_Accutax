# Gemini Brain — End-to-End Flow by Intent & Data Source Reference

**Version:** 1.0
**Date:** 2026-08-20
**Companion docs:** [`ROBUSTNESS_AND_GRACEFUL_DEGRADATION_SPEC.md`](./ROBUSTNESS_AND_GRACEFUL_DEGRADATION_SPEC.md) (error handling) ·
[`API_TOOLCALLING_ROBUSTNESS_ASSESSMENT.md`](./API_TOOLCALLING_ROBUSTNESS_ASSESSMENT.md) (coverage) ·
[`DATABASE_DEEP_DIVE.md`](./DATABASE_DEEP_DIVE.md) (what the data actually contains)

---

## 0. How to read this document

This document answers one question precisely: **when a user types a question, what actually
happens, step by step, and where does the answer come from?**

It is organized in three layers:

- **§1–2** — the shared prelude every request goes through, and the three data sources the system
  can draw from, before intent even matters.
- **§3** — one section per intent type (1–7), each with: how it's detected, how it's routed, which
  data source answers it, a concrete worked example, and the failure modes specific to that intent.
- **§4–6** — cross-cutting scenarios that apply across intents: caching, multi-turn conversations,
  streaming vs. sync, and what session memory actually does (and doesn't do).

Every code reference is `file:line`. Where a claim about runtime behaviour is testable without
external credentials, it was verified by executing the relevant function in this session — those
are marked **[verified]**.

---

## 1. The shared prelude (runs before intent is even known)

Every call to `POST /api/v1/query` or `/query/stream` passes through the same five gates before
`GeminiBrainRunner` looks at *what* was asked:

```
1. JWT authentication         auth.py: get_current_user()
2. Session ID validation      gemini_brain_runner.py:439  (is_valid_uuid)
3. PII redaction               gemini_brain_runner.py:445  (redact_pii)
4. Tenant isolation            gemini_brain_runner.py:451  (_enforce_tenant_isolation)
5. Fast-router pre-step        gemini_brain_runner.py:475  (fast_route — 0 LLM calls)
```

### 1.1 Authentication

`routes.py:170` depends on `get_current_user`, which decodes the JWT (`auth.py:296`) and returns a
`CurrentUser(user_id, email, allowed_org_ids)`. `allowed_org_ids` comes straight from the token's
`allowed_org_ids` claim — it is **not re-checked against the database on every request**; whatever
was baked in at login time is trusted for the token's full 60-minute lifetime.

### 1.2 PII redaction

`redact_pii(query)` (`pii/redactor.py`) runs a regex/NER-style pass over the raw query text before
anything else sees it — including the fast router and the classifier. Redaction counts are tracked
and surfaced in `pii_redacted` / `pii_redactions` on the response, and the **redacted** query (not
the original) is what every downstream stage — router, classifier, endpoint selector, SQL engine —
actually reasons over. The original raw query is kept only for session-memory storage
(`raw_query`, saved verbatim so the user sees their own words back in chat history).

### 1.3 Tenant isolation — the four resolution paths

`_enforce_tenant_isolation` (`gemini_brain_runner.py:326`) resolves the final `organization_id`
through one of four paths, in this priority order:

| # | Condition | Resolution | Code |
|---|---|---|---|
| 1 | `session_id` given | Verify the session belongs to `user_id` (`verify_session_ownership`) or reject | `:335` |
| 2 | `allowed_org_ids == []` | Reject — authenticated user with zero assigned tenants | `:341` |
| 3 | `allowed_org_ids is None` (no-auth / internal test path) | `organization_id` from body, else dynamically resolved from query text via `resolve_organization()` (a Gemini call + DB lookup), else reject | `:344–357` |
| 4 | `allowed_org_ids` present | Body `organization_id` must be in the list → else resolve from query text and verify membership → else if exactly one allowed org, default to it → else reject as ambiguous | `:363–426` |

**`resolve_organization`** (`tenant/org_resolver.py`) is its own small LLM round-trip: it asks
Gemini *"does this query name a specific organization?"*, and if yes, looks the name/ID up in the
`organizations` table (exact match on numeric ID, `ILIKE '%name%'` fuzzy match otherwise). This
call happens **before** intent classification and is completely independent of it — a query like
*"switch to organization 44 and show revenue"* triggers two separate Gemini calls (org resolution,
then intent classification) that never see each other's output.

### 1.4 Fast-router pre-step — the zero-LLM shortcut

Before any LLM is called for routing, `fast_route(query, organization_id, user_id)`
(`router/fast_router.py:187`) tries to match the query against **18 hardcoded regex rules**
(`FAST_ROUTER_RULES`, `:70–158`). If a rule matches:

- Zero Gemini calls are spent on classification or endpoint selection.
- The `intent` (1–7) is read straight off the matched rule's tuple.
- Query parameters (dates, limits, org id) are built deterministically using `router/dates.py`'s
  phrase resolver (§1.5) — no LLM involved in date math either.
- A `CONCEPT_GUARD` regex (`:167`) runs first and **vetoes** the match if the query looks like a
  definitional/how-to question (*"what is the difference between…"*, *"how do I record…"*), sending
  it to the LLM classifier instead — this is what keeps "AR aging" (data) and "what is AR aging"
  (concept) from colliding.

This is why the same phrase can take two completely different paths depending on wording: *"AR
aging"* → fast router, zero LLM calls, sub-100ms. *"What is AR aging?"* → concept guard fires →
full LLM classification → Type 6 → Gemini direct answer.

### 1.5 Deterministic date resolution

`router/dates.py:resolve(phrase, anchor)` turns phrases like `"this month"`, `"last quarter"`,
`"Q1 2026"`, `"last 6 months"`, `"ytd"` into a concrete `[date_from, date_to]` window, anchored to
**Asia/Dubai** time (`ORG_TZ`, `:16`) regardless of where the server or the user physically is.
`PERIOD_REGEX` (`fast_router.py:23`) extracts the phrase from the raw query text; if nothing
matches, `resolve()` silently defaults to **"this year"** (`dates.py:59-62`). This deterministic
resolver is used by the fast router; the Gemini-driven endpoint selector (§3.3–3.5) computes dates
itself, inside the LLM call, using a different mechanism — see §3.3's caveat.

---

## 2. The three data sources — what actually answers a question

Every answer in this system comes from exactly one of three places. Knowing which one is live for
a given intent is the single most useful fact for predicting whether an answer will be accurate.

| Source | What it is | Reached via | Freshness | Ground truth for |
|---|---|---|---|---|
| **A — Accutax Live REST API** | The production Accutax backend, `settings.accutax_base_url` (`13.127.157.108:8081`) | `call_api()` (`api_client/accutax_client.py`) | Real-time | Types 3, 4, 5 — when an endpoint match succeeds |
| **B — PostgreSQL `accutax_bk_1_5`** | The same underlying database the API itself is backed by, queried **directly** — bypassing the API entirely | SQL fallback engine (`sql_fallback/sql_engine.py`) → external `coordinator_agent` tool loop, or `execute_sql_function()` for the 3 `fn_*` analytical functions | Real-time (same DB, no replica lag) | Types 3, 4, 5 — when no endpoint matches, or the API call fails |
| **C — Gemini/Claude's own trained knowledge** | No tenant data at all — the model answers from general accounting/product knowledge | `_call_gemini()` direct, with fallback to `BedrockAdapter` | N/A — not tenant data | Types 1, 2, 6, 7 |

**Critical nuance:** Sources A and B are **not tiers of the same data** — they are two different
*access paths to the same physical database*. The REST API (A) is a separate backend service that
happens to read from the same Postgres instance the SQL fallback engine (B) queries directly. This
means:

- A and B can disagree if the API layer applies business logic (status filtering, calculated
  fields) that the SQL fallback's hand-written queries don't replicate exactly.
- A going down does **not** mean the data is unavailable — it means the *convenient, pre-aggregated*
  access path is unavailable, and the system falls back to raw SQL against the same records.
- As of this session, **the shipped `ACCUTAX_AUTH_TOKEN` in `.env` decodes to an `exp` claim of
  2026-08-20 11:29 UTC** — issued for organization_id `5`, which per `DATABASE_DEEP_DIVE.md §10`
  **does not exist** as a live organization. Practically, this means Source A is either already
  unreachable or within hours of becoming so at any given moment "today" is read, and **every
  Type 3/4/5 query is currently being answered by Source B (direct SQL), not Source A**, regardless
  of which endpoint the selector picks. See `API_TOOLCALLING_ROBUSTNESS_ASSESSMENT.md §2.6` for the
  full implication.

---

## 3. Per-intent flow

### Type 1 — FAQ / How-to

> *"How do I create a recurring invoice?"* · *"How do I record a journal entry?"*

**Detection:** `ROUTER_SYSTEM_PROMPT` (`classification/intent_classifier.py:16`) — Gemini classifies
based on the definition *"General usage questions"*. Not covered by the fast router or its
`CONCEPT_GUARD` (that guard routes definitional *concept* questions to Type 6, not Type 1 — the
distinction between "how do I…" (1) and "what is…" (6) is entirely the classifier's judgment call,
with no regex backstop).

**Routing:** `qtype in LEFT_PATH_TYPES` (`{1, 2, 6, 7}`, `constants.py:44`) → **LEFT PATH**
(`gemini_brain_runner.py:494`).

**Data source:** **C only.** No API call, no SQL query, no `organization_id` even used past the
tenant-isolation gate. The answer is Gemini's (or Bedrock's, on fallback) knowledge of Accutax's UI
and general accounting workflow, shaped by `DIRECT_ANSWER_SYSTEM_PROMPT`
(`gemini_brain_runner.py:64`) plus, if `session_id` is set, any uploaded project-knowledge documents
via `get_project_context_by_session()`.

**Worked example — *"How do I create a recurring invoice in Accutax?"***

```
1. Auth, PII redact, tenant isolation (org resolved but unused downstream)
2. fast_route() → CONCEPT_GUARD does not match "how do I create" pattern
   for invoices specifically → falls through to fast-router table, no rule
   matches "recurring invoice" → returns None
3. classify_intent() → Gemini call, ROUTER_SYSTEM_PROMPT
   → {"type": 1, "reason": "How-to question about recurring invoices"}
4. LEFT PATH: _call_gemini(DIRECT_ANSWER_SYSTEM_PROMPT, query, max_tokens=1500)
5. If session_id: prepend project KB docs + cross-chat history to system prompt
6. Answer returned. results=[], sql=None, routing_info.path="gemini_direct"
```

**Failure modes specific to this intent:**
- If Gemini fails 3× across the 3-model retry ladder (`_call_gemini`, `:139-160`) **and** the
  Bedrock fallback also fails, `answer` can be `""` (see the resilience spec §2.2/§8.6) — this is
  the one intent where the LLM *is* the entire answer, so a total LLM outage means a total answer
  outage, with no data-layer fallback of any kind to fall back to.
- The model can confidently describe a UI flow that doesn't match the actual current Accutax UI —
  there is no verification against real product documentation; this is pure trained-knowledge
  recall, occasionally augmented by project KB files if the user uploaded any.

---

### Type 2 — App Guidance

> *"Where is the expense module?"* · *"How do I find the VAT settings?"*

**Detection & routing:** Identical mechanics to Type 1 — same `LEFT_PATH_TYPES` membership, same
`DIRECT_ANSWER_SYSTEM_PROMPT`. The classifier's prompt distinguishes Type 1 (*"how do I perform an
action"*) from Type 2 (*"where do I find a screen"*) but both terminate in the exact same code path
at `gemini_brain_runner.py:494`. There is no functional difference between Type 1 and Type 2 once
routing completes — the distinction exists purely for the `routing_info.type_label` shown to the
user and in the trace, and for analytics on which kind of question is more common.

**Data source:** **C only** — identical to Type 1.

**Failure modes:** Identical to Type 1.

---

### Type 3 — Report Generation

> *"Show P&L for this year"* · *"Balance sheet as of today"* · *"Trial balance"*

**Detection:** Fast-router rules `profit_loss`, `balance_sheet`, `cash_flow`, plus
`dashboard_overview` (health-check phrasing) all carry `intent=3` (`fast_router.py:79-96,124-129`).
If none match, `classify_intent` distinguishes Type 3 from Type 4 by *"Structured financial
reports"* vs. *"Live data lookups"* — a genuinely fuzzy line the prompt itself doesn't fully resolve
(e.g. *"show me revenue by category"* could reasonably be either).

**Routing:** `qtype in RIGHT_PATH_TYPES` (`{3, 4, 5}`) → **RIGHT PATH**
(`gemini_brain_runner.py:659` onward). If the fast router didn't already produce a `sel`
(endpoint+params dict), `select_endpoint()` (`endpoints/endpoint_selector.py:66`) makes a Gemini call
against the full 70-endpoint `API_CATALOG` text (`config/api_catalog.py`) to choose one.

**Data source:** **A, falling back to B.**

1. `result_cache.get_sync()` — 300s TTL cache keyed by `(org_id, endpoint, params, data_version)`.
2. If no cache hit and endpoint starts with `fn_` → **B**, via `execute_sql_function()` — this
   applies only to the three analytical Postgres functions (`fn_project_expense_rollup`,
   `fn_inventory_movement`, `fn_gl_profitability`), which are **not** REST endpoints at all; they
   are SQL functions the fast router and endpoint selector can select directly.
3. Otherwise → **A**, via `call_api()`. On `ok=False` (any non-200, timeout, or transport error) →
   `data=None` → falls through to the **Type 3/4/5 shared SQL fallback** (source **B**, full
   tool-calling loop — see §3.4's fallback description, which is shared verbatim by Types 3, 4, 5).

**Formatting:** report-type tools (`profit_loss`, `balance_sheet`, `cash_flow`) use the
`financial_statement` formatter (`tools/formatters.py:110`) and are narrated by Claude via
`reason_over_data()` unless `narrate=False` was requested.

**Worked example — *"Show me the P&L statement for 2026"***

```
1. fast_route(): PERIOD_REGEX extracts "2026" → dates.resolve("2026") →
   Window(2026-01-01, 2026-12-31)
   Rule "profit_loss" matches → endpoint=/report/profit-loss, intent=3
   0 Gemini calls spent on routing.
2. result_cache miss (first call for this org+period)
3. call_api("/report/profit-loss", {}, {organization_id, start_date:"2026-01-01",
   end_date:"2026-12-31"})
   → (ok=True, raw={"success": true, "data": {...}}) typically
   → extract_data() unwraps to the P&L dict
4. tool_spec lookup by endpoint → REGISTRY["profit_loss"] → formatter="financial_statement"
5. render("financial_statement", data) → markdown key-value table
6. reason_over_data(query, data, endpoint, intent=3, ...) → model_selector.pick_model(3, data)
   → intent 3 is not in (5,7) and payload is small → Haiku 4.5 narrates
7. Response: answer (narration) + results (raw dict wrapped in list) +
   routing_info.path="api_then_anthropic"
```

**Failure modes specific to this intent:**
- `financial_statement` formatter returns `None` for **list-shaped** payloads (some P&L variants
  return an itemised list rather than a keyed summary) — see resilience spec §2.3. This is the
  single most consequential formatter bug because it sits directly on the three most-used report
  endpoints.
- Per `DATABASE_DEEP_DIVE.md §9`, P&L, balance sheet, trial balance, and GL are all **✅ Works**
  against the real data — this is the intent category with the *best* underlying data support.
  Cash flow and VAT-adjacent reports are weaker (VAT amounts are structurally zero for nearly every
  tenant — see §9 row "VAT liability").

---

### Type 4 — Data Query

> *"Total revenue this year"* · *"Top 5 customers"* · *"List unpaid invoices"* · *"Who owes us money"*

This is the **largest, busiest, and most heterogeneous** intent. It covers everything from a single
aggregate number to a itemised list to a multi-table analytical rollup.

**Detection:** 12 of the 18 fast-router rules carry `intent=4` — by far the majority. Everything the
classifier can't confidently place as a report (3), forecast (5), or a left-path type defaults to 4
(`classify_intent`'s own fallback: `d.get("type", 4)` and the `except` branch both default to 4 —
Type 4 is the system's catch-all).

**Routing:** Same RIGHT PATH mechanics as Type 3. The practical difference from Type 3 is which
`ToolSpec` gets matched and which formatter/narrate flag it carries — many Type 4 tools
(`invoice_list`, `bill_list`, `item_list`, `bank_accounts`, `contact_search`, `audit_logs`, and 8
others) have `narrate=False` (`tools/registry.py`), meaning they skip the Claude reasoning call
entirely and return the deterministic `render()` markdown table as the `answer` directly — a
**zero-LLM-after-retrieval** response. This is the fastest and cheapest path in the whole system:
one fast-router regex match (or one Gemini classify + one Gemini endpoint-select call) plus one API
call, with no Bedrock spend at all.

**Data source:** **A, falling back to B** — identical mechanics to Type 3, including the same
`fn_*` SQL-function shortcut for `project_expense_rollup`, `inventory_movement`, and
`gl_profitability`.

**Worked example — *"What is accounts receivable aging?"* vs. *"AR aging"***

These two phrasings diverge sharply, illustrating the concept-guard boundary:

```
"AR aging"
  → fast_route(): CONCEPT_GUARD does not match (no "what is" prefix)
  → rule "ar_aging" matches → /report/ar-aging-summary, intent=4
  → 0 LLM calls for routing

"What is accounts receivable aging?"
  → fast_route(): CONCEPT_GUARD matches `what\s+(is|are)\s+(a\s+|an\s+)?(...|accounts?\s+receivable...)`
  → returns None (vetoed)
  → classify_intent() → Gemini call → {"type": 6, "reason": "..."}
  → LEFT PATH → Gemini direct definitional answer (Source C, no data)
```

The **same underlying question** ("tell me about AR aging") produces either a live numbers-backed
answer or a textbook definition depending purely on phrasing. This is by design — Type 6 exists
precisely to catch definitional intent — but it means a user who types the more natural, grammatical
version of their question gets no data at all.

**Worked example — *"Total revenue this year"* full trace with SQL fallback:**

```
1. fast_route(): rule "income_total" matches → /income/total,
   params={organization_id, user_id, filter_year:"2026", filter_type:"YEARLY"}
2. cache miss
3. call_api("/income/total", {}, {...}) → suppose token expired → (ok=False, "HTTP 401: ...")
4. data=None → falls to Type 3/4/5 SQL FALLBACK:
   a. _db_fallback() → BedrockAdapter(HAIKU45_ID) → sql_engine.run()
   b. try_fast_path(): checks 12 regex patterns in fast_path.py — "total revenue" does not
      match any (fast_path.py's own separate pattern list does not include income_total —
      see API_TOOLCALLING_ROBUSTNESS_ASSESSMENT.md §2.3 for this specific gap)
   c. Falls to the full Bedrock tool-calling loop:
      _safe_build_system_prompt() injects hard tenant-isolation instructions
      adapter.converse_with_tools() with TOOL_DEFINITIONS (finance_agent, schema_agent,
        tax_agent, reasoning_agent — 4 Bedrock tools, 44 finance_agent task enum values)
      Model likely calls finance_agent(task="get_invoice_total" or "aggregate_metric", ...)
      handler executes against accutax_bk_1_5 directly (Source B)
   d. Up to 5 iterations / 90s budget (ENGINE_MAX_ITERATIONS, ENGINE_TIME_BUDGET_SECONDS)
   e. Final answer synthesized, tenant-isolation regex applied to any raw SQL
      (enforce_tenant_isolation_sql)
5. Response: routing_info.path="db_fallback", sql=<the executed query>,
   agent_trace includes both the failed API step and the full SQL tool-loop trace
```

**Failure modes specific to this intent:**
- The widest intent category means the widest exposure to every failure mode catalogued in the
  resilience spec — empty payloads (§2.1), error envelopes (§2.4), and cache poisoning (§2.11) all
  concentrate here because this is where the highest query volume lands.
- Per `DATABASE_DEEP_DIVE.md §9`: revenue, expenses, customers, vendors, invoices, items, GL — all
  ✅. But "unpaid/overdue invoices" and AR aging are **⚠️ partial** (computed from `status_type_id`
  only, because `amount_paid` is always `0.00` database-wide), and anything about bank balances,
  customer/supplier payments, or uncategorized transactions is **❌ structurally empty** for every
  tenant — no amount of routing improvement fixes those answers, because the data doesn't exist.

---

### Type 5 — Forecast

> *"Forecast cash flow for next quarter"* · *"Expected cash position"* · *"Cash runway"*

**Detection:** Fast-router rule `cash_forecast` (`fast_router.py:104`) is the only forecast-specific
rule; everything else routing to Type 5 comes through `classify_intent`'s *"Future-looking
predictions"* category.

**Routing:** RIGHT PATH, same mechanics as Types 3/4. The distinguishing behaviour is in **model
selection**: `pick_model()` (`reasoning/model_selector.py:16`) explicitly routes intent `5` (and `7`)
to the larger **Claude 3.5 Sonnet** rather than the default Haiku 4.5, on the theory that
forecasting narration needs more analytical depth than a data lookup. This is a deterministic,
zero-LLM-call routing decision (a pure function on `intent` + payload token size), not itself an
LLM judgment.

**Data source:** **A (`/report/cash-forecast`), falling back to B.** There is exactly one dedicated
forecast endpoint in the entire catalog. Any other forecast-flavoured question (*"predict next
year's revenue"*, *"what will Q4 expenses look like"*) has no matching REST endpoint at all and
routes straight past cache/API to the SQL fallback, where `finance_agent`'s `payment_forecast` task
is the only genuinely predictive tool available (a deterministic near-term AR/AP due-date
projection, not a statistical forecast) — anything beyond that is Claude reasoning over historical
data without a real forecasting model behind it.

**Worked example — *"What's our cash forecast for the next 3 months?"***

```
1. fast_route(): rule "cash_forecast" matches → /report/cash-forecast, intent=5
   params={organization_id, months: 6}   ← NOTE: fast_router hardcodes 6 months
   regardless of "3 months" in the query text — the fast router extracts the
   PERIOD phrase for date-range endpoints but does NOT parse a numeric month
   count out of forecast-specific phrasing. "next 3 months" and "next 12 months"
   produce the identical params={"months": 6} today.
2. call_api("/report/cash-forecast", {}, {organization_id, months:6})
3. On success: model_selector.pick_model(5, data) → Sonnet 3.5 (higher-cost path)
4. Narration via Sonnet, formatter="kv_summary"
```

**Failure modes specific to this intent:**
- The month-count mismatch above is a genuine, reproducible parameter bug — verified by reading the
  code path (`fast_router.py:262-266`): the `elif endpoint == "/report/cash-forecast"` branch
  ignores `window` entirely and hardcodes `"months": 6`.
- `keyword_endpoint_fallback`'s own cash-forecast branch (`endpoints/keyword_fallback.py:41-64`)
  computes a **different** date range again — `today` to `min(today.month+3, 12)` — a third,
  independent, and internally buggy calculation (`min(month+3, 12)` clamps at December instead of
  rolling into the next year, so a forecast requested in November produces a 1-month window instead
  of 3). Three code paths (fast router, keyword fallback, and whatever Gemini itself computes when
  it picks this endpoint via the catalog prompt) can each produce a different date range for the
  identical phrase depending on which layer happens to catch it.
- Per `DATABASE_DEEP_DIVE.md §9`, cash/bank data is structurally empty for nearly every tenant, so
  even a perfectly-routed forecast query is likely to return an empty or near-empty payload for most
  organizations.

---

### Type 6 — Accounting Concept

> *"What is accounts receivable?"* · *"Explain accrual vs cash basis"* · *"What is depreciation?"*

**Detection:** `CONCEPT_GUARD` (`fast_router.py:167-170`) is the load-bearing mechanism here — it is
checked **first**, before any of the 18 fast-router data rules, specifically to prevent conceptual
questions from being misrouted to a live data lookup. Its pattern list is explicit and finite:
`difference between`, `explain how/what/why`, `define`, `what does`, `how do i record/create/
make/post/file`, `where do i`, and a closed set of named terms (`accounts? receivable`, `accounts?
payable`, `vat`, `trn`, `depreciation`, `accrual`, `debit`, `credit`, `journal entry`). Anything
conceptual that doesn't match this specific list falls through to the fast-router data rules and
`classify_intent` as the second line of defense.

**Routing:** LEFT PATH, identical mechanics to Types 1/2.

**Data source:** **C only.**

**Failure modes:** Same LLM-outage exposure as Types 1/2. Additionally, the finite `CONCEPT_GUARD`
term list means a conceptual question phrased around a term *not* in that list (e.g. *"what is a
contra account"*, *"what does accrued liability mean"*) depends entirely on `classify_intent`
correctly recognising it as Type 6 rather than defaulting to Type 4 and attempting (and likely
failing) to find live data for it.

---

### Type 7 — Summary & Advice

> *"Give me a business health check"* · *"What should I focus on this quarter?"*

**Detection:** Fast-router rule `dashboard_overview` (`fast_router.py:99-102`) matches health-check
phrasing but — notably — points at `/report/profit-loss`, **not** a dedicated dashboard/summary
endpoint, with `intent=7`. `classify_intent`'s *"Strategic summaries"* category is the general-case
path.

**Routing:** This is the one intent that **can go either way** depending on which layer catches it:
- If the fast router's `dashboard_overview` rule matches → RIGHT PATH (P&L data + Sonnet narration,
  because `pick_model` also routes intent 7 to Sonnet, same as intent 5).
- If the classifier alone assigns Type 7 → **LEFT PATH** (`7 ∈ LEFT_PATH_TYPES`), meaning strategic
  advice is answered by Gemini/Bedrock **with no live data at all**, purely from general knowledge
  of what a "business health check" should cover.

This is a real architectural inconsistency: the same intent number is wired to both the LEFT and
RIGHT execution paths depending on which router layer produces it, and a user asking a genuinely
data-dependent strategic question (*"how healthy is our cash position right now"*) may receive a
generic, non-numeric answer if the fast router doesn't happen to catch the exact phrasing.

**Data source:** **C**, or **A/B** only via the one fast-router shortcut described above.

**Worked example — the divergence:**

```
"How are we doing?"           → fast_route(): matches "how are we doing" pattern in
                                  dashboard_overview rule → RIGHT PATH → real P&L data,
                                  narrated by Sonnet.

"Give me strategic advice
 for next quarter"             → fast_route(): no rule matches → classify_intent() →
                                  {"type": 7, ...} → LEFT PATH → Gemini/Bedrock answers
                                  from general knowledge, organization_id and all real
                                  financial figures are completely unused.
```

**Failure modes specific to this intent:** the LEFT/RIGHT split above is the dominant one — from
the user's perspective, "why did my last health-check question show real numbers and this one
didn't" has no visible explanation in the UI (both look like a normal narrated answer; nothing in
the response signals that one path used live data and the other didn't, beyond the low-visibility
`routing_info.path` field which the UI does not currently surface to the user).

---

## 4. Cross-cutting scenarios

### 4.1 Caching

`result_cache` (`cache/result_cache.py`) is a single in-process, thread-safe TTL dict, keyed by
`f"{org_id}:{endpoint}:{sha256(sorted_params)[:16]}:{data_version}"`. Every successful API or
`fn_*` call is cached for **300 seconds** regardless of intent. Consequences:

- Two users in the same organization asking the same canonical question within 5 minutes share one
  API round trip — good for cost, good for latency.
- The cache is **process-local** — restarting the server, or running multiple worker processes,
  gives each process its own independent cache with no shared invalidation.
- `get_data_version(org_id)` (`cache/versions.py`) is the only invalidation lever; nothing in the
  write path of this system bumps it, so cache entries only ever expire by TTL, never by data
  change.
- As documented in the resilience spec §2.11: today, `[]`, `{}`, `None`, and error-envelope strings
  are cached with the same 300s TTL as genuine data — a transient failure is indistinguishable from
  a real empty result for the cache's purposes.

### 4.2 Streaming vs. synchronous

`run()` and `run_stream()` (`gemini_brain_runner.py`) are two independently-maintained,
near-duplicate implementations of the identical routing logic — every branch described in §3 above
exists twice in the source file (once per function). They diverge in exactly three ways:

1. `run_stream()` yields `{"status": ...}` progress events at each stage boundary (understanding
   request → determining data source → retrieving data → analyzing → generating → finalizing).
2. `run_stream()` streams the Gemini direct-answer path token-by-token via
   `generate_content_stream()`, and the Bedrock reasoning path via `converse_stream()` — the
   synchronous `run()` always waits for the complete response.
3. `run_stream()` emits a `data_table` event (`:1154`) containing the pre-rendered markdown table
   *before* narration begins, intended to let the frontend paint the table while the model is still
   generating the summary — see the resilience spec §2.8 for why this specific event is currently
   dropped by the UI.

Both entry points converge on identical `token_usage`, `agent_trace`, and `routing_info` shapes —
the response schema does not differ between streaming and sync, only the delivery mechanism does.

### 4.3 Multi-turn conversations & session memory — what's stored vs. what's used

This is worth stating precisely, because the system does more *recording* than *using*.

**What gets stored per session** (`memory/session_memory.py`, `memory/state_extractor.py`):
- Every user/assistant message pair (`save_message_by_session`).
- A hybrid heuristic+LLM-extracted state object: `active_year`, `bank_account`, `contact_name`,
  `last_executed_task` (`state_extractor.py:22-25`), updated after every turn.
- Project-level knowledge base documents and cross-chat history, if the session belongs to a
  "project" grouping (`get_project_context_by_session`).

**What actually gets read back in:**

| Consumer | Reads session history? | Reads extracted state? |
|---|---|---|
| `fast_route()` | No | No |
| `classify_intent()` | No | No |
| `select_endpoint()` | No | No |
| `resolve_organization()` | No | No |
| `_call_gemini` for LEFT-path answers | Project KB docs + cross-chat history only | No |
| `reason_over_data()` narration | Project KB docs + cross-chat history only | No |

**The extracted state (`active_year`, `bank_account`, `contact_name`, `last_executed_task`) is
written every turn and never read by anything** — confirmed by searching every call site of
`get_state_by_session` in the codebase; its only caller is `state_extractor.py` itself, immediately
before it recomputes and overwrites the same value. There is no code path that feeds this state back
into routing or reasoning for a subsequent turn.

**Practical consequence:** a follow-up question that omits the subject established in a prior turn
has no mechanism to resolve it at the routing layer. *"Show P&L for Q1"* → *"what about Q2?"* — the
second query reaches `fast_route()` and `select_endpoint()` as the bare string `"what about Q2?"`,
with no visibility into the fact that P&L was just discussed. The **project KB / cross-chat-history**
context does reach the *narration* stage (so Claude, narrating the Q2 P&L data, might reference the
Q1 conversation if it happens to be in the injected history text) — but routing itself, which
decides *what data to fetch in the first place*, is always a cold start. Whether *"what about Q2?"*
resolves correctly depends entirely on whether a fast-router rule or the Gemini endpoint selector can
independently infer "P&L" from that fragment alone — which, for the fast router's `profit_loss`
regex (`\b(p&l|profit and loss|net profit|income statement)\b`), it cannot.

### 4.4 PII inside a data query

Redaction happens once, at the very top of `run()`/`run_stream()`, before intent classification.
This means a PII-bearing query like *"what's the balance for John Smith, national ID 784-1990-
1234567-1"* has its ID redacted before the fast router or classifier ever see it — good for
consistency, but it also means the redacted placeholder text (not the original) is what
`select_endpoint` reasons over when deciding which endpoint/params to build, so a redaction that
happens to remove a name needed for a `search` query parameter will degrade that specific lookup.

### 4.5 Tenant resolution edge cases

| Scenario | Path taken | Data source impact |
|---|---|---|
| User has exactly 1 allowed org, doesn't specify one | Auto-defaults (`:390`) | Normal |
| User has 2+ allowed orgs, doesn't specify one, query doesn't name one | `ValueError` — 400 (or now, per the resilience spec, a `TENANT_AMBIGUOUS` degraded response) | No retrieval attempted at all |
| Query names an org by name/number not in the user's allow-list | `ValueError` — rejected before any retrieval | No retrieval attempted |
| No JWT at all (`allowed_org_ids is None`, internal/dev path) | `organization_id` from body, or dynamically resolved from query text with **no allow-list check whatsoever** | Full access to any organization named in the query text |

---

## 5. Data source matrix — quick reference

| Intent | Label | Path | Primary source | Fallback source | Narration model |
|---|---|---|---|---|---|
| 1 | FAQ/How-to | LEFT | C (Gemini knowledge) | C (Bedrock Haiku) | — |
| 2 | App Guidance | LEFT | C | C (Bedrock Haiku) | — |
| 3 | Report Generation | RIGHT | A (REST API) | B (direct SQL) | Haiku 4.5 (usually) |
| 4 | Data Query | RIGHT | A (REST API) | B (direct SQL) | Haiku 4.5, or none if `narrate=False` tool |
| 5 | Forecast | RIGHT | A (`/report/cash-forecast` only) | B | Sonnet 3.5 |
| 6 | Accounting Concept | LEFT | C | C (Bedrock Haiku) | — |
| 7 | Summary & Advice | **Both** — depends on which router layer catches it | A (P&L, if fast-router hit) or C (if classifier-only) | B, or none | Sonnet 3.5 (RIGHT) / — (LEFT) |

---

## 6. Appendix — file:line index for this document's claims

| Claim | File:line |
|---|---|
| Prelude order (auth → PII → tenant → fast router) | `gemini_brain_runner.py:439-479` |
| `_enforce_tenant_isolation` four-path resolution | `gemini_brain_runner.py:326-427` |
| `resolve_organization` LLM+DB lookup | `tenant/org_resolver.py:29-79` |
| `CONCEPT_GUARD` regex | `router/fast_router.py:167-170` |
| `FAST_ROUTER_RULES` (18 entries) | `router/fast_router.py:70-158` |
| `dates.resolve()` phrase table | `router/dates.py:35-179` |
| `LEFT_PATH_TYPES` / `RIGHT_PATH_TYPES` | `config/constants.py:44,47` |
| `DIRECT_ANSWER_SYSTEM_PROMPT` | `gemini_brain_runner.py:64-75` |
| `pick_model()` intent→model rule | `reasoning/model_selector.py:16-49` |
| `ToolSpec.narrate=False` zero-LLM tools | `tools/registry.py` (13 tools, e.g. `invoice_list`, `bill_list`, `item_list`) |
| Cash-forecast month-count bug | `router/fast_router.py:262-266` |
| Keyword-fallback cash-forecast date bug | `endpoints/keyword_fallback.py:41-64` |
| Extracted session state written, never read | `memory/state_extractor.py` (sole caller of `get_state_by_session`) |
| `result_cache` key structure and TTL | `cache/result_cache.py:21-32` |
| `.env` token expiry / org mismatch | `.env:23` (`ACCUTAX_AUTH_TOKEN`, decoded `exp=1787225340`, `organization_id=5`); `DATABASE_DEEP_DIVE.md §10` |
| Real data coverage per question type | `DATABASE_DEEP_DIVE.md §9` |
