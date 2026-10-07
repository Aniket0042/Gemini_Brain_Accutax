# Gemini Brain: Demo Failure Root Cause and Production Plan

Date of review: 6 October 2026
Scope: the Gemini Brain AI backend (`Gemini_Brain_Accutax/backend`) and the Accutax report endpoints it calls (`accutax_bk_backend`).
Status: diagnosis complete. No code changed yet. Phase 0 is waiting for approval.

---

## 1. Summary

Two questions failed during the live client demo:

| Question | What the client saw | Root cause in one line |
|---|---|---|
| "Which of the organizations have profit margin above 20%?" | All 10 organizations listed, including one with a negative margin | No code handles conditions such as "above 20%". The planner only supports "top N" or "bottom N". |
| "List all the organizations which are making negative profit" | No answer after 2–3 minutes | The question was routed to the wrong report. That Accutax report ignores the organization filter, so it scanned every tenant in the database. It timed out, was retried, and then fell back to a slow per-organization path. |

These are symptoms of design problems, not one-off bugs. Most questions go through the same fragile path:

- The system has run on an outdated model since 17 September.
- About 250 hand-written regex patterns decide what a question means.
- One model call picks one REST endpoint, with no check that the result answers the question.
- Profit is defined differently in different code paths.
- Nothing limits how long a request can take.

The recommended direction:

- Replace the regex routing and endpoint picking with a tool-using agent.
- Build the agent on one governed set of metric definitions.
- Have code, never the model, apply tenant scope and compute every figure.

Phase 0 contains the quick fixes needed before the next demo.

---

## 2. Root cause: "profit margin above 20%"

**What happened**

1. The multi-org planner (`orchestrator/multi_org_plan.py`) matched the words "profit margin" with a regex in `orchestrator/multi_org_metrics.py`.
2. It sent the question down the deterministic metric path with no model call. Evidence: `backend/logs/multi_org_plans.jsonl`, entry at 07:56:54 UTC on 6 Oct, shows `"source": "metric"`, `"metrics": ["profit_margin"]`, `"llm_calls": 0`.
3. The metric path computed each organization's margin correctly.
4. The only row filter on that path is `limit_rows()`. It understands "top N" and "bottom N", and nothing else. No code reads "above", "below", "more than", "less than", "negative", "positive" or "between".
5. So the condition "above 20%" was silently dropped, and all 10 organizations were listed in ranked order.

**Classification:** missing feature (threshold filters) on a regex-driven path, with no warning to the user that part of the question was ignored.

---

## 3. Root cause: "list all organizations making negative profit" (2–3 minutes)

Five problems stacked on top of each other.

### 3.1 The word "list" disabled the metric path

`match_metrics()` returns nothing when the question matches `_LIST_OR_BREAKDOWN`, which includes "list". The question was treated as "list some records" instead of "net profit per organization where net profit < 0". So it fell through to the model-based router.

### 3.2 The model picked the wrong report

The router made two model calls, intent classification and endpoint selection. We reproduced the run live on 6 Oct. The selected endpoint was:

```
/report/profit-loss-by-branch   (query_params: organization_id, start_date, end_date, limit, sort_order)
```

That report gives profit per branch, not per organization. The model doing this selection is Claude 3 Haiku (see section 4.1).

### 3.3 The Accutax report ignores `organization_id` (cross-tenant scan)

In `accutax_bk_backend/src/modules/report/report.service.ts`, `getProfitLossByBranch`:

- accepts `organization_id` in its DTO and OpenAPI spec;
- never uses it, and instead runs `organizationsRepo.findOne({ where: { user_id } })`;
- applies no tenant filter at all when `user_id` is missing.

Gemini Brain sends `organization_id` but not `user_id`. As a result, each call scanned the whole shared database:

| Table | Approx. rows |
|---|---|
| organizations | 10,045 |
| income | 13.7 million |
| income_items | 25.2 million |
| journal_entry_lines | 66.2 million |

Measured on 6 Oct (read-only):

- The same aggregation with no org filter was cancelled by the 20s statement timeout.
- The same aggregation filtered to org 24 finished in 0.64s.

### 3.4 Timeouts, retries and fallbacks multiplied the delay

For each of the 10 organizations:

1. The REST client times out after 6s and tries 3 times (`api_client/accutax_client.py`).
2. The planner retries the fetch once more (`_retrieve_with_retry`).
3. After about 37 seconds of failed attempts, the organization falls back to the full single-org pipeline (`runner.run`). That pipeline costs several more model calls:
   - intent classification;
   - endpoint selection;
   - a self-correction re-selection;
   - a SQL agent loop of up to 5 iterations;
   - narration.

Only 4 organizations run at a time (`MAX_PARALLEL_ORGS = 4`), so 10 organizations take 3 rounds. Total: 2–3 minutes.

### 3.5 Abandoned work kept loading the database

When Gemini Brain gives up on a call, the Accutax backend keeps running the all-tenant scan. Pressing Stop in the UI does not fully cancel the backend either: the thread pool waits for every organization already queued. That load slows the shared database for every customer.

---

## 4. Security issue found during the review

**This is a data-isolation defect in the Accutax backend, independent of the AI.**

11 report methods in `report.service.ts` take the organization from `user_id` and ignore `organization_id`:

- `getProfitLossByBranch`
- `getProfitLossByProject`
- `getSalesByBranch`
- `getSalesByProject`
- `getExpensesByBranch`
- `getBillsByBranch`
- `getConsolidatedPnl`
- `getConsolidatedBalanceSheet`
- `getConsolidatedCash`
- `getCashFlowIndirectMethod`
- `getSupplierStatementOfAccount`

Effects:

1. **Every tenant's data.** Any authenticated caller that omits `user_id` gets every tenant's data. The permission guard validates `organization_id`, but the service then never uses it.
2. **Wrong organization in the normal Accutax app.** `accutax_bk_frontend/src/pages/ReportDetail.jsx` sends both `user_id` and `organization_id`. The service picks the user's first organization. A user who owns several organizations and switches to another one still sees the first one's figures on these reports.
3. **Database load.** Each unscoped call scans tens of millions of rows on the shared database.

Gemini Brain had already found the same problem in `/income/total` (see the comment on `income_total` in `backend/src/gemini_brain/reports/definitions.py`). It worked around it by using its own SQL report.

**Two separate fixes:**

- **Gemini side (protects the AI only):** mark these endpoints as not org-scoped, so multi-org runs skip them and use Gemini Brain's own org-filtered SQL reports. This needs no change in Accutax.
- **Accutax side (protects the product):** filter by `organization_id` in each of the 11 methods, and add tests. This belongs to the `accutax_bk_backend` owner.

---

## 5. Why most questions behave like this (systemic causes)

### 5.1 The system runs on an outdated model

`backend/src/gemini_brain/config/constants.py`:

```python
HAIKU45_ID: str = "anthropic.claude-3-haiku-20240307-v1:0"
```

Commit `d018596` (17 Sep 2026) changed this from `global.anthropic.claude-haiku-4-5-20251001-v1:0` to work around an `InvalidSignatureException`. Since then, every intent classification, endpoint selection, SQL agent step and direct answer has run on Claude 3 Haiku (March 2024). The UI, the model picker and the cost table still say "Claude Haiku 4.5".

Checked on 6 Oct with one small call each from the dev machine (region `ap-south-1`):

| Model ID | Result |
|---|---|
| `in.anthropic.claude-haiku-4-5-20251001-v1:0` | Works (India region) |
| `in.anthropic.claude-sonnet-5` | Works (India region) |
| `global.anthropic.claude-haiku-4-5-20251001-v1:0` | Works |
| `global.anthropic.claude-sonnet-5-5` | Works |
| `apac.anthropic.claude-haiku-4-5-...` | Invalid identifier |

`InvalidSignatureException` is usually caused by a wrong clock on the calling server. Check the VM clock (`timedatectl`) before switching the deployed models.

### 5.2 Regex-first understanding

About 250 hand-written patterns decide what a question means before any model sees it. The largest sets:

- `router/rules.py`: 47
- `orchestrator/multi_org_metrics.py`: 42
- `orchestrator/multi_org_lists.py`: 31

Patterns cannot express conditions, filters, combinations or synonyms reliably. Every new phrasing becomes a new bug, and a near-miss falls through to a weaker path without telling the user.

### 5.3 One-shot endpoint picking

A single model call picks one REST endpoint from a catalog (`endpoints/endpoint_selector.py`). Nothing checks that:

- the result answers the question;
- the endpoint is scoped to the organization.

The Accutax endpoints are inconsistent about tenancy (section 4).

### 5.4 Five data paths, three definitions of profit

The same question can be answered by:

1. the Accutax REST report `/report/profit-loss` (general ledger);
2. Gemini's SQL report `rpt_profit_summary` (invoices minus bills);
3. `finance_agent._task_profit_and_loss`;
4. free-form SQL written by the SQL agent;
5. the multi-org metrics.

Different paths give different numbers for the same question. "Cost of sales" and "operating expenses" are not supported on the multi-org path at all.

### 5.5 No time budget

- There is no overall deadline per request.
- The Bedrock client uses the botocore defaults (60s read timeout, automatic retries). No timeout is configured.
- Fallbacks cascade with no total limit:
  1. REST call (with retries);
  2. SQL report;
  3. a second endpoint selection;
  4. the SQL agent (up to 5 iterations);
  5. narration.
- Stop in the UI does not fully cancel backend work.

### 5.6 No database connection pool

Every report query opens a new PostgreSQL connection. Measured from the dev machine: about 0.42s to connect plus 4 round trips of about 0.16s each. That is roughly 1 second of overhead per query, while the SQL itself is fast.

This is why the margin answer showed "20 SQL queries, 18,883 ms". Each report call took 0.7–1.8s. It will be lower on the VM, but it is still wasted time.

### 5.7 Risky prompt wording

The SQL agent's system prompt (`agents/coordinator_agent.py`) says:

> "NEVER refuse. Always answer. If ambiguous: assume most likely interpretation"

It also describes a "company using PostgreSQL with 10 years of production data". In a finance product this invites confident wrong numbers.

### 5.8 No accuracy gate before demos

The 79 unit test files mostly test the regex paths. There is no fixed set of real user questions with known correct answers, checked for accuracy and response time before a release or a demo.

---

## 6. Target architecture

**Principle:** stop adding patterns. Build a tool-using agent on top of one governed set of metric definitions.

### 6.1 Governed metric definitions (semantic layer)

- Each business metric is defined once, in general-ledger terms:
  - revenue, cost of sales, gross profit;
  - operating expenses, net profit, margins;
  - cash, receivables, payables;
  - VAT input, VAT output and VAT payable.
- Metrics can be grouped by organization, month, quarter, customer, vendor, account, branch or project.
- Metrics can be filtered by any condition, for example `net_profit < 0` or `profit_margin > 20`.
- Code compiles each request into parameterized SQL. **Code adds the organization filter, never the model.**
- Every answer path reads numbers from this layer, so the same question always gives the same number.

### 6.2 Tools the agent can call

| Tool | Purpose |
|---|---|
| `query_metrics(metrics, orgs, period, group_by, filters, sort, limit)` | Figures, comparisons, rankings and threshold filters, for one or many organizations |
| `list_documents(type, filters, sort, limit)` | Invoices, bills, payments, journal entries |
| `get_report(name, params)` | P&L, balance sheet, trial balance, aging, VAT return (general-ledger based) |
| `run_readonly_sql(sql)` | Last resort only. Read-only role, row-level security, row and time limits, org scope enforced by the database. |
| `search_vat_kb(question)` | UAE VAT law answers with FTA citations (existing knowledge base) |
| `app_guide(question)` | How-to answers for the Accutax app (existing guide) |

### 6.3 The agent

- **Models:**
  - Planner: Claude Sonnet 5 on the India-region profile (`in.anthropic.claude-sonnet-5`), using native tool use.
  - Cheap steps such as titles and summaries: Claude Haiku 4.5 (`in.` profile).
- **Hard limits:**
  - about 6 tool calls per question;
  - a 45-second total deadline;
  - a timeout on each tool.
- **Behaviour:**
  - stream status updates to the UI;
  - cancel all work when the user presses Stop or disconnects;
  - say plainly when a part of the question cannot be answered, instead of dropping it silently.

### 6.4 Multi-org in one query

Run one SQL with `organization_id = ANY(%s)` and `GROUP BY organization_id`, instead of fanning out 10 separate pipelines. Example: "organizations with profit margin above 20%" becomes

```
query_metrics(metrics=["profit_margin"], orgs=<selected>, period="ytd",
              group_by=["organization"], filters=[{"metric": "profit_margin", "op": ">", "value": 20}])
```

That is one SQL query, expected under 1 second.

### 6.5 Numbers come from tools, never from the model

- The model writes the narrative. All figures come from tool output.
- Keep the existing verifier (`policy/verifier.py`), which checks that every figure in the answer appears in the data.

### 6.6 Performance

- PostgreSQL connection pool (for example `psycopg_pool`).
- Review indexes for the metric queries, such as `organization_id` plus the date columns, and remove casts on indexed columns.
- Monthly per-organization, per-account summary tables, refreshed on a schedule or by trigger. Most questions then read a few thousand rows instead of millions.

### 6.7 Evaluation and observability

- A golden set of 150–300 real questions with expected answers worked out in SQL. Include single-org, multi-org, filters, follow-ups, VAT and how-to.
- Run it on every change. A change ships only if accuracy and 95th-percentile response time meet the agreed thresholds.
- Persist the full trace for every request, including stopped ones:
  - model calls;
  - tool calls;
  - SQL;
  - timings.

### 6.8 MCP

- Expose the same governed tools as an MCP server later. Claude Desktop, other agents or partner tools can then reuse them with the same tenant scoping.
- MCP is packaging, not the fix. The fix is the metric definitions, the bounded agent and a current model.
- Do not expose a raw "execute any SQL" MCP tool to the product. It has no tenant scoping.

### 6.9 What to keep and what to retire

**Keep:**
- PII redaction (`pii/`)
- the tenant isolation guard
- the verifier
- report artifacts (PDF, XLSX, DOCX)
- the VAT knowledge base
- the SQL, API and LLM tracers
- the chat frontend

**Retire:**
- the regex routers (`router/rules.py`, the regex parts of `multi_org_metrics.py` and `multi_org_lists.py`)
- the REST endpoint selector
- the separate intent classifier call
- `finance_agent` fixed tasks
- the coordinator's model tiers

---

## 7. Phased plan

### Phase 0: stabilise before the next demo

| # | Task | Repo | Done when |
|---|---|---|---|
| 0.1 | Switch `HAIKU45_ID` to Haiku 4.5 (`in.` or `global.` profile) and the reasoning model to Sonnet 5. Fix model labels and prices in `policy/registry.py`. Check the VM clock first. | Gemini | Bedrock calls on the VM succeed with the new IDs. The UI label matches the model actually used. |
| 0.2 | Mark the 11 non-scoped Accutax report endpoints as not org-scoped in Gemini, and route them to Gemini's own SQL reports. | Gemini | The "negative profit" question answers in seconds with per-organization figures. No unscoped calls appear in the API trace. |
| 0.3 | Fix the 11 report methods to filter by `organization_id`, and add tests. | Accutax | Calling each endpoint for org A returns only org A's data, with or without `user_id`. |
| 0.4 | Add threshold filters (above, below, more than, less than, negative, positive, between) to the metric plan. Treat "list organizations where <metric> <condition>" as a metric question. | Gemini | Both demo questions return only the matching organizations. |
| 0.5 | When part of a question cannot be applied, say so in the answer instead of dropping it. | Gemini | An unsupported condition produces a visible note. |
| 0.6 | Add a database connection pool. | Gemini | The per-query overhead drops from about 1s to the query's own time. |
| 0.7 | Add a request deadline, Bedrock client timeouts, and cancellation on Stop or disconnect. Cancel queued per-organization work too. | Gemini | No request runs past the deadline. Stop ends backend work. |
| 0.8 | Agree a 30-question demo set with expected answers. Run it before every demo. | Both | A pass/fail sheet exists for each demo build. |

**Phase 0 status, 7 Oct 2026** (built and tested locally; not yet deployed to the VM):

| # | State |
|---|---|
| 0.1 | Done. `in.` Haiku 4.5 and `in.` Sonnet 5, verified from the VM (clock NTP-synced). Labels and prices now follow the model ID (`constants.model_label`, `pricing.py`); `sonnet-3.5` stays as an alias for saved preferences. The VM's `.env` still sets the old `BEDROCK_MODEL_ID*` and must be updated at deploy. |
| 0.2 | Done. In the Accutax build running on the VM, 4 of the 11 methods now honour `organization_id` (the three consolidated reports and the supplier statement). The other 7 are in `reports/definitions.ORG_IGNORED_REST` and are never called, single-org or multi-org; the two project reports answer from org-filtered SQL. |
| 0.3 | Open (Accutax owner): fix the 7 methods listed in `ORG_IGNORED_REST`. |
| 0.4 | Done. `orchestrator/multi_org_conditions.py`: above/below/at least/at most/between, negative/positive, loss-making/profitable, amounts with k/m/AED, percentages. "List the organizations …" with a condition is a metric question. |
| 0.5 | Done. A condition that cannot be applied (on an average, a percentage on an amount, a series) is stated in the answer; organizations without a figure are listed separately. |
| 0.6 | Done. Lazy connection pool in `sql_fallback/db_connection.py` (kill switch `DB_POOL_ENABLED`). Report time halved over the VPN (0.32 s → 0.16 s per report). |
| 0.7 | Partly done. Bedrock: 45 s read timeout, 2 attempts (was 60 s × up to 5). Multi-org: 40 s fetch deadline, then late organizations are named; Stop or a dropped connection cancels queued per-org work. Not done: an overall deadline for single-organization requests. |
| 0.8 | Done. `tests/data/demo_set.json` (30 questions), run with `scripts/eval/multi_org_eval.py --cases tests/data/demo_set.json`; figures and condition matches are checked against independent SQL, and each run writes a Markdown pass/fail sheet. First full run: **30/30**, p95 5.9 s. Both demo questions: 2.0–2.5 s and 0.1 s (were 18.8 s and 2–3 minutes). |

### Phase 1: governed metric definitions

- Define the metrics and dimensions in general-ledger terms, agreed with finance (section 6.1).
- Build `query_metrics` with filters, grouping, sorting and limits. Multi-org runs as a single SQL query.
- Point the existing multi-org metric path at it, so current behaviour keeps working.
- Add the golden question set and run it on every change.

### Phase 2: the tool-using agent

- Build the agent loop with the tools in section 6.2, the limits in section 6.3 and the verifier.
- Run it in shadow mode next to the current path on the golden set. Compare accuracy, response time and cost.
- Switch over when it is better on all three. Then remove the regex routers and the endpoint selector.

**Phase 2 status, 7 Oct 2026:** the agent is built and was evaluated offline (`AGENT_MODE` is off). On 89 real questions from the chat history it passed 84, against 60 for the current path. It is not yet faster: its 95th-percentile time was 36 s, against 11 s for the current path. VAT law answers vary from run to run. Details, the recommendation and the grade for each case are in [`PHASE2_AGENT.md`](./PHASE2_AGENT.md).

### Phase 3: platform hardening

- Expose the tools as an MCP server, with tenant scope taken from the caller's token.
- Add monthly summary tables and an index review.
- Add CI evaluation gates and monitoring dashboards: accuracy, p95 latency, cost per question, error rates.

---

## 8. Decisions needed from the team

1. Approve Phase 0. Decide who fixes the 11 Accutax report methods (task 0.3).
2. Choose the planner model: Sonnet 5 on the India region (`in.`) for data residency, or Sonnet 5.5 (`global.`) for maximum capability.
3. Agree the official definition of "profit" and the other core metrics with finance. Today the code has three.
4. Decide what the client is told about the cross-tenant defect in section 4.
5. For Phase 1, build the semantic layer in-house or adopt an open-source one. Options considered so far:
   - Cube Core;
   - MetricFlow.
   No decision yet.

---

## 9. Appendix: evidence

| Item | Where |
|---|---|
| Plan log for the margin question | `Gemini_Brain_Accutax/backend/logs/multi_org_plans.jsonl`, 2026-10-06T07:56:54Z |
| Metric matcher and list exclusion | `backend/src/gemini_brain/orchestrator/multi_org_metrics.py` (`match_metrics`, `_LIST_OR_BREAKDOWN`, `limit_rows`) |
| Multi-org planner and fallback | `backend/src/gemini_brain/orchestrator/multi_org_plan.py`, `orchestrator/multi_org.py` (`MAX_PARALLEL_ORGS = 4`) |
| REST timeout and retries | `backend/src/gemini_brain/api_client/accutax_client.py` (6s timeout, `_MAX_ATTEMPTS = 3`) |
| Org-scope guard | `backend/src/gemini_brain/orchestrator/gemini_brain_runner.py` (`_rest_call_is_org_scoped`, `_KNOWN_UNSCOPED_REST`) |
| Accutax report ignoring `organization_id` | `accutax_bk_backend/src/modules/report/report.service.ts` (`getProfitLossByBranch` and 10 others) |
| Accutax UI parameters | `accutax_bk_frontend/src/pages/ReportDetail.jsx` (sends `user_id` and `organization_id`) |
| Model constant | `backend/src/gemini_brain/config/constants.py`, changed in commit `d018596` |
| Connection per query | `backend/src/gemini_brain/reports/engine.py` (`query`), `sql_fallback/db_connection.py` (`get_connection`) |
| SQL agent prompt | `backend/src/gemini_brain/agents/coordinator_agent.py` |

Measurements taken on 6 Oct 2026 from the dev machine over VPN. All were read-only queries.

| Measurement | Result |
|---|---|
| Unscoped branch P&L aggregation | Cancelled at the 20s statement timeout |
| Same aggregation, org 24 only | 0.64s |
| `rpt_profit_summary`, one org | 1.5–1.8s |
| `rpt_metric_series`, one org | 0.7–1.7s |
| New DB connection | 0.42s |
| Single round trip | 0.16s |
