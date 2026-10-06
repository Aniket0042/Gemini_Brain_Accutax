# Cube Core Integration Guide (v2)

Date: 6 October 2026
Supersedes: [`CUBE_CORE_INTEGRATION_GUIDE.md`](./CUBE_CORE_INTEGRATION_GUIDE.md) (v1)
Related: [`ai-architecture-review-2026-10.md`](./ai-architecture-review-2026-10.md)
Audience: the engineers building the semantic layer, the DBA, and finance (section 4 only).
Status: **Phase 1 in progress** (7 Oct 2026). Step 1 is done (section 5.1). The model, security config, client, tool, shadow hook and tests are written (section 16). Cube has not run yet: Step 2 waits on the DBA, and Step 3 (deploy on the VM) waits on Step 2 and an explicit go-ahead. Finance sign-off (section 1.3) is still needed before Cube answers users.

The code was written against the Accutax entity definitions, Gemini Brain's own SQL reports, and the Cube docs for v1.7.50. Each cube's SQL and measures have been run read-only against the live database for the test orgs (`scripts/eval/cube_model_check.py`). Cube's own compilation of the model is verified at the Step 3 smoke tests.

---

## 1. Summary

### 1.1 What we are building

Cube Core sits between Gemini Brain and PostgreSQL. Each business metric is defined once, in YAML. The AI picks a view, measures, groupings, filters and a period. It never writes SQL and never sees organization IDs. Code binds the user's authorized organizations into a 60-second token, and Cube adds the organization filter to every query. One query covers all selected organizations, and filters such as "margin above 20%" become SQL `HAVING` conditions.

Cube answers figure questions: totals, rankings, thresholds, trends, comparisons. Other questions keep their own tools:

| Question | Tool |
|---|---|
| KPIs, rankings, thresholds, trends, comparisons across orgs | `query_metrics` → Cube (this guide) |
| Document lists and lookups ("unpaid invoices for X", "INV-1042") | Existing org-scoped `rpt_invoice_list` / `rpt_document_list`, exposed as a `list_documents` tool |
| VAT law, how to use the app | Existing VAT knowledge base and app guide |
| Anything the catalog cannot answer | A plain "I can't answer that yet", logged for the model backlog |

### 1.2 What changed from v1

| v1 problem | v2 fix | Section |
|---|---|---|
| Cube SQL used columns that do not exist (`jel.organization_id`, `debit`/`credit`, `account_types.name`) | Models written from the real entities | 8 |
| No `is_posted`; cost of sales double-counted; margin 0 when revenue is 0 | Same rules as Accutax's ledger P&L; margin is empty when revenue ≤ 0 | 4, 8.2 |
| Invoices counted quotes, estimates, proformas, delivery notes, drafts and voided documents | Explicit document-type and status rules | 4.1, 8.2 |
| `CAST(invoice_date AS DATE)` fails the whole query on one bad value | Guarded parse; bad values get no date | 8.1 |
| `query.primaryCube` (not a Cube property); only one cube scoped | Allow-listed views only; every view the query touches is filtered | 7.4 |
| Hard-coded default secret; port 4000 open; tokens never expire | No default (startup fails); 127.0.0.1 only; 60 s tokens with `aud`/`iss` | 7, 9 |
| "Continue wait" treated as an empty result | Client polls until a result or the deadline | 9.2 |
| `memory` cache driver, `:latest` image, no timeouts | Cube Store, pinned v1.7.50, 20 s DB timeout | 7.2 |
| "<0.4 s" claimed with no pre-aggregations | Targets measured per phase; rollups in Phase 3 | 13 |
| `rm -rf sql_fallback/` breaks login and chat history | Move shared modules first; delete after shadow mode | 12.3 |

### 1.3 Sign-offs needed

1. **Finance:** the metric contract in section 4, especially decision D1 (ledger vs documents).
2. **DBA:** the SQL in section 6 (read-only role, role timezone, one index).
3. **Security review:** sections 3, 7.4 and 11.2. Tenant isolation must pass in CI before any deploy.

---

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | P&L and balance-sheet figures come from the **posted general ledger**. Sales, purchases, VAT, receivables and payables come from **documents**. | The Accutax P&L screen is built from the ledger (`/report/profit-loss-with-accounts`). An AI figure that differs from that screen loses the client's trust. Per-customer and per-vendor figures exist only on documents. |
| D2 | A flow metric with no period means **year-to-date** in the reporting time zone (Asia/Dubai). Every answer states its period. | Today's default is the whole calendar year. That includes future-dated documents: the seed has documents dated up to Dec 2026. |
| D3 | Results are always **per organization**. Code never adds amounts across currencies. | Organizations carry their own `currency`. A cross-org total of AED and USD is wrong. |
| D4 | The AI can query **views only**. Raw cubes are `public: false`. | Views are the governed surface, with one name and one description per metric. |
| D5 | **No free-form SQL** on the customer path, during or after the migration. | The wrong numbers came from that path. A plain refusal is better than a confident wrong figure. |
| D6 | Tenant scope is enforced in **Gemini auth, the token, Cube, and the response check**, not in the database. | Cube pools its connections and cannot set `app.current_org` per request. Section 3 explains the layers. |

---

## 3. Architecture and trust boundaries

```
Browser (chat UI)
   │  Gemini Brain JWT
   ▼
FastAPI ── api/auth.py: get_current_user → authorize_org_scope ──► org-access audit log
   │  authorized org IDs (from the request, never from the model)
   ▼
Agent / multi-org planner
   │  tool input: view, measures, group_by, filters, period    (no org IDs, no SQL)
   ▼
semantic/query_metrics.py ── checks every name against Cube /v1/meta, adds org grouping, resolves dates
   ▼
semantic/cube_client.py ──── mints a 60 s HS256 token {organization_ids, aud, iss, iat, exp}
   │  http://127.0.0.1:4000
   ▼
Cube API (cube.js) ───────── verifies token; queryRewrite: views only, AND organization_id filter, row cap
   │                                  │
   ▼                                  ▼
PostgreSQL as cube_reader          Cube Store (cache, queue, rollups)
(read-only, 20 s timeout, UTC)        ▲
                                      └── refresh worker (builds rollups from Postgres)
   │
   └─► rows back in cube_client: every row's organization_id must be in the token, or the answer is refused
```

Isolation layers, each of which must fail closed:

| Layer | Where | What it guarantees |
|---|---|---|
| 1 | [`authorize_org_scope`](../backend/src/gemini_brain/api/auth.py#L602) (exists today) | Only organizations on the user's live allow-list reach the token. The decision is audited. |
| 2 | Token (`cube_client._token`) | Organization list is signed, valid for 60 s, audience `accutax-cube`, issuer `gemini-brain`. |
| 3 | `queryRewrite` in `cube.js` | Unknown views are refused. Every view in the query gets `organization_id IN (token orgs)`, ANDed at top level, so no filter the AI writes can widen it. A missing org scope is an error, never "no filter". |
| 4 | Data model | Every view has `organization_id`. Every join also matches on organization. Raw cubes cannot be queried. |
| 5 | Response check (`cube_client._check_scope`) | A row for any organization outside the token raises `CubeScopeViolation` and a critical audit log. |
| 6 | Database role | `cube_reader` is read-only, limited to the listed tables, with a 20 s statement timeout. |
| 7 | CI | The isolation suite (11.2) runs on every change to `semantic/`. |

---

## 4. Metric contract (for finance)

### 4.1 Document rules

| Field | Included | Sign | Excluded |
|---|---|---|---|
| `income.income_type` | `INVOICE`, `CASH_INVOICE`, `INCOME` | + | `QUOTE`, `ESTIMATION`, `PROFORMA`, `DELIVERY_CHALLAN`, `DELIVERY_NOTE` |
| | `CREDIT_NOTE` | − | |
| `expense.expense_type` | `BILL`, `EXPENSE`, `CASH_EXPENSE` | + | `PURCHASE_ORDER` |
| | `VENDOR_CREDIT` | − | |
| Status | everything else | | `is_draft = true`, `voided_at` set, status `CANCELLED` or `VOIDED` |
| Document date | `income.invoice_date`; `expense.reception_date`, else `created_date` (as Accutax does) | | Text that is not a date gives no date, so the document drops out of period queries. Step 1 counts these. |

Types come from [`income-type.enum.ts`](../../accutax_bk_backend/src/modules/income/enum/income-type.enum.ts) and [`expense-type.enum.ts`](../../accutax_bk_backend/src/modules/expense/enum/expense-type.enum.ts). `INCOME` and `CASH_INVOICE` as sales, and `VENDOR_CREDIT` as negative, are proposals for finance to confirm. Today the data holds only `INVOICE` in `income`, and `EXPENSE`, `BILL`, `CASH_EXPENSE` in `expense` (section 5.1); the other rules are there for when those types appear.

**Open question for finance:** in the test orgs, 24,219 of 50,764 invoices have status `ACCEPTED` or `RECEIVED`. Neither counts as open (receivable) or as cancelled. Confirm what these mean on an invoice.

### 4.2 Metrics

**P&L** (view `pnl`): posted journal entries only, filtered by `journal_entries.transaction_date`.

| Metric | Member | Definition |
|---|---|---|
| Revenue | `pnl.revenue` | credit − debit on `account_type = 'Revenue'` |
| Cost of sales | `pnl.cost_of_sales` | debit − credit on `account_type = 'Expense'` with `LOWER(account_sub_type) = 'cost of sales'` |
| Operating expenses | `pnl.operating_expenses` | debit − credit on all other `Expense` accounts |
| Total expenses | `pnl.total_expenses` | cost of sales + operating expenses |
| Gross profit | `pnl.gross_profit` | revenue − cost of sales |
| Net profit | `pnl.net_profit` | revenue − total expenses |
| Gross / net margin % | `pnl.gross_margin_pct`, `pnl.net_margin_pct` | profit ÷ revenue × 100. **Empty when revenue ≤ 0.** |

These match `getProfitLossWithChartAccounts` exactly: `is_posted` at [report.service.ts:454](../../accutax_bk_backend/src/modules/report/report.service.ts#L454), the cost-of-sales split at [:500](../../accutax_bk_backend/src/modules/report/report.service.ts#L500), and net profit at [:523](../../accutax_bk_backend/src/modules/report/report.service.ts#L523). VAT is excluded because it posts to liability accounts; Step 1 confirms this.

**Balance sheet** (view `balance_sheet`): posted entries up to an "as of" date.

| Metric | Member | Definition |
|---|---|---|
| Assets | `balance_sheet.assets` | debit − credit on `Asset` |
| Liabilities | `balance_sheet.liabilities` | credit − debit on `Liability` |
| Booked equity | `balance_sheet.equity_booked` | credit − debit on `Equity` |
| Accumulated earnings | `balance_sheet.accumulated_earnings` | credit − debit on `Revenue` and `Expense` (not yet closed to equity) |
| Total equity | `balance_sheet.total_equity` | booked equity + accumulated earnings |
| Balance difference | `balance_sheet.balance_difference` | assets − liabilities − total equity. It should be 0; anything else is a data problem to report. |
| Cash and bank | `balance_sheet.cash_and_bank` | debit − credit on `Asset` accounts named like "cash" or "bank". This is today's `rpt_cash_balance` rule; Step 1 found no cash sub-type, so the name rule stays. |
| Current assets, fixed assets, current liabilities | `balance_sheet.current_assets`, `.fixed_assets`, `.current_liabilities` | Same signs as above, on sub-types `Current Asset`, `Fixed Asset`, `Current Liability` |
| Working capital, current ratio | `balance_sheet.working_capital`, `.current_ratio` | Current assets − current liabilities; current assets ÷ current liabilities (empty when liabilities ≤ 0) |

Step 1 confirmed the chart of accounts carries the sub-types these need (section 5.1). Gross profit, cost of sales, other income (`pnl.other_income`, sub-type `Non-Operating Revenue`), working capital and the current ratio are therefore answerable. Today `multi_org_metrics.py` lists them as unsupported; remove those entries once Cube answers.

**Sales** (`sales`) and **purchases** (`purchases`): documents, filtered by document date.

| Metric | Member | Definition |
|---|---|---|
| Gross sales | `sales.gross_sales` | line amounts before VAT, sales types |
| Credit notes | `sales.credit_notes` | line amounts before VAT, credit notes (positive figure) |
| Net sales | `sales.net_sales` | gross sales − credit notes |
| Output VAT (documents) | `sales.output_vat` | tax on sales − tax on credit notes |
| Invoice count | `sales.invoice_count` | distinct sales documents, excluding credit notes |
| Gross / net purchases, vendor credits, input VAT, bill count | `purchases.*` | same pattern on expense documents |

**VAT** (`vat`): `output_vat`, `input_vat`, `net_vat_payable` (output − input) and `standard_rated_sales` (sales lines with VAT > 0), by document date. These match `rpt_vat_input_output` once the document rules in 4.1 are applied.

**Receivables / payables** (`receivables`, `payables`): **current status only**, because payment state lives in the status and has no history.

| Metric | Member | Definition |
|---|---|---|
| Outstanding | `receivables.outstanding` | document total incl. VAT − `amount_paid`, on `PENDING` / `PARTIALLY_PAID` documents dated today or earlier |
| Overdue | `receivables.overdue_amount` | same, where due date < today |
| Counts | `open_count`, `overdue_count` | |
| Aging | dimension `aging_bucket` | Not yet due, 1–30, 31–60, 61–90, Over 90 days |

`amount_paid` stays 0 even on paid invoices ([definitions.py:611](../backend/src/gemini_brain/reports/definitions.py#L611)), so open status, not `amount_paid`, decides what is outstanding. A past "as of" date for receivables is refused with a clear message. The ledger balance of the receivables account is the honest answer for past dates, and can be added once Step 1 identifies that account.

### 4.3 Reconciliation targets

| View | Must equal | Tolerance |
|---|---|---|
| `pnl` | Accutax `GET /report/profit-loss-with-accounts`: `details.revenue.total`, `cost_of_sales.total`, `operating_expenses.total`, `net_profit_loss` | 0.01 |
| `balance_sheet` | Gemini `rpt_balance_sheet` totals; `balance_difference` = 0 | 0.01 |
| `sales`, `purchases`, `vat`, `receivables`, `payables` | Hand-written SQL in `scripts/eval/cube_reconcile.py`, reviewed by finance | 0.01 |

Use the endpoint, not the screen header: see section 15, item 1.

### 4.4 Expected differences from today's numbers

Shadow mode (12.2) will show these differences. They are corrections, not regressions:

- **Revenue, expenses, profit, margin.** Today's multi-org metrics read documents (`rpt_income_total`, `rpt_expense_total`); v2 reads the ledger (D1). In seed orgs 26–33 the ledger and the invoices disagree (see the seed notes), so the gaps there will be large.
- **Document metrics.** Today's [`income_total`](../backend/src/gemini_brain/reports/definitions.py#L518), `vat_input_output`, `_contact_totals` and `_open_balance` do not filter `income_type` or `is_draft`. Any quotes, estimates, proformas, delivery notes, drafts, and credit notes (counted as positive) are in today's totals.
- **Default period.** Year-to-date instead of the full calendar year.

---

## 5. Step 1: verify the data (read-only)

Run these as `gemini_brain_ro` and paste the results into the PR that adds the models. They are all read-only. Queries 6 and 11 are limited to the test orgs to keep them cheap.

| # | Query | What the answer changes |
|---|---|---|
| 1 | `SELECT account_type, account_sub_type, COUNT(*) FROM chart_of_accounts GROUP BY 1,2 ORDER BY 1,2;` | Confirms `Revenue` / `Expense` / `Asset` / `Liability` / `Equity` and the `Cost of Sales` spelling. If sub-types such as "Current Asset" exist, working capital and gross margin can leave the "unsupported" list in `multi_org_metrics.py`. |
| 2 | `SELECT income_type, COUNT(*) FROM income GROUP BY 1;` and the same for `expense.expense_type` | Confirms the rules in 4.1. Also settles `ESTIMATE` vs `ESTIMATION` (section 15, item 5). |
| 3 | `SELECT id, code, value FROM status_type ORDER BY id;` | Status names used in the models |
| 4 | `SELECT COUNT(*) FROM income WHERE invoice_date IS NOT NULL AND invoice_date !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}';` and the same for `due_date`, `expense.reception_date`, `expense.due_date` | How many documents drop out of period queries. If any belong to real customers, fix the data. |
| 5 | `SELECT is_posted, is_reversal, COUNT(*) FROM journal_entries GROUP BY 1,2;` | Size of the unposted set excluded from the P&L |
| 6 | `SELECT COUNT(*) FROM journal_entry_lines l JOIN journal_entries j ON j.id = l.journal_entry_id JOIN chart_of_accounts c ON c.id = l.account_id WHERE j.organization_id BETWEEN 24 AND 33 AND c.organization_id <> j.organization_id;` | Must be 0. The model's join drops cross-org postings, so a non-zero count is a data incident to report. |
| 7 | `SELECT tablename, indexdef FROM pg_indexes WHERE tablename IN ('journal_entries','journal_entry_lines','income','income_items','expense','expense_items','chart_of_accounts','contacts');` | Whether section 6's index is needed. The entity declares no index starting with `journal_entries.organization_id`. |
| 8 | `SELECT currency, COUNT(*) FROM organizations GROUP BY 1;` and `SELECT currency, COUNT(*) FROM chart_of_accounts WHERE account_type = 'Asset' GROUP BY 1;` | Whether D3 bites today. Also check, with the Accutax owner, whether journal amounts on foreign-currency accounts are stored in that currency or in the base currency. |
| 9 | `SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('income','journal_entries','chart_of_accounts','contacts');` | Whether [`005_rls_reader_role.sql`](../backend/sql/functions/005_rls_reader_role.sql) Part B is applied. If it is, section 6 needs the extra policy. |
| 10 | `SELECT version(); SHOW timezone;` | Postgres version; the server's default time zone, which the role overrides to UTC |
| 11 | `SELECT COUNT(*) FROM (SELECT DISTINCT j.organization_id, l.account_id, j.transaction_date FROM journal_entry_lines l JOIN journal_entries j ON j.id = l.journal_entry_id WHERE j.is_posted AND j.organization_id BETWEEN 24 AND 33) t;` | Row count for the daily rollup in section 13, to extrapolate its size |
| 12 | `SELECT c.account_type, c.account_name, SUM(l.credit_amount - l.debit_amount) FROM journal_entry_lines l JOIN journal_entries j ON j.id = l.journal_entry_id JOIN chart_of_accounts c ON c.id = l.account_id WHERE j.organization_id = 24 AND j.is_posted AND c.account_name ILIKE '%vat%' GROUP BY 1,2;` | Confirms VAT posts to a liability or asset account, not revenue |

### 5.1 Results (6 Oct 2026)

Run read-only as `gemini_brain_ro` over the VPN. Scans were limited to orgs 24–33; table-wide value lists came from `pg_stats`, so no full-table scans ran on the shared database.

| # | Finding | Effect on the plan |
|---|---|---|
| 1 | Account types are exactly `Asset`, `Liability`, `Equity`, `Revenue`, `Expense`. There are 13 sub-types, including `Cost of Sales`, `Operating Expense`, `Other Expense`, `Operating Revenue`, `Non-Operating Revenue`, `Current Asset`, `Fixed Asset`, `Current Liability` and `Non-Current Liability`. | Model rules confirmed. Gross profit, other income, working capital and current ratio added (4.2). |
| 2 | `income.income_type` is `INVOICE` for every row (pg_stats). `expense_type` is `EXPENSE` 99.96%, plus `BILL` and `CASH_EXPENSE`. | No quotes or credit notes today. The type rules stay for when they appear. |
| 3 | Statuses: ACCEPTED, PAID, CANCELLED, PENDING, RECEIVED, VOIDED, PARTIALLY_PAID. In the test orgs, 23% of invoices are VOIDED or CANCELLED and 1,902 are drafts. `voided_at` is set on only 6; voids are recorded in the status. | The status filter carries the void rule. Today's `rpt_income_total` counts the 1,902 drafts. ACCEPTED/RECEIVED meaning is an open question (4.1). |
| 4 | One malformed date in the test orgs (the known `due_date = "string"`). Text dates look like `2021-06-22T05:45:36.000Z`. 85% of `expense.due_date` values are NULL. | Guarded parse works. Overdue payables will mostly show "No due date". |
| 5 | All test-org journal entries are posted (7 reversals). | — |
| 6 | Cross-org postings: 0. | — |
| 7 | `idx_journal_entries_org_id (organization_id, id)` exists in the database, though the entity does not declare it. There are also about 4.95 GB of `_ccnew` index leftovers, most of them invalid (section 15). | No new index needed for Phase 1. |
| 8 | Organization currency: AED for 10,053 orgs, plus one org with `STR`. Account currency: `AED`, `USD`, and a bad value `AE`. | D3 matters for cash: group by account currency. |
| 9 | Row-level security is off on every table Cube reads. | No RLS policy needed for `cube_reader`. |
| 10 | PostgreSQL 13.23. Server time zone **Asia/Kolkata**. | Confirms `cube_reader` must use `timezone = 'UTC'` (section 6). |
| 11 | Test orgs: 232,912 posted lines collapse to 144,087 (org, account, day) rows. | Sizing input for Phase 3 rollups (section 13). |
| 12 | VAT posts to `VAT Payable` (Current Liability). Revenue accounts exclude VAT. | Ledger revenue is net of VAT. |
| 13 | 89 parent accounts in the test orgs carry their own postings. | The Accutax P&L screen drops these (section 15). The endpoint totals include them. |

The model's SQL also ran read-only against the test orgs (`scripts/eval/cube_model_check.py`). All 7 cubes compile in Postgres. The Cube-shaped P&L query for 10 orgs, year to date, took 0.2 s warm using `idx_journal_entries_org_id`; one cold run exceeded the 20 s timeout (section 13).

---

## 6. Step 2: database prerequisites (DBA)

The shared database is changed only by the DBA, on explicit approval. Hand over [`semantic/dba/cube_reader.sql`](../semantic/dba/cube_reader.sql); do not run it from Gemini Brain.

After Step 1 only the role is needed. Row-level security is off, so the policy below is not needed. `idx_journal_entries_org_id` already exists, so the index below is not needed for Phase 1; keep it in mind for Phase 3. The server time zone is Asia/Kolkata, so the role's `timezone = 'UTC'` setting is required. The original SQL follows for reference.

```sql
-- Run as a superuser or CREATEROLE role. Generate the password; store it only in semantic/cube.env on the VM.
CREATE ROLE cube_reader LOGIN PASSWORD '<generated>' NOINHERIT CONNECTION LIMIT 20;
ALTER ROLE cube_reader SET default_transaction_read_only = on;
ALTER ROLE cube_reader SET statement_timeout = '20s';
ALTER ROLE cube_reader SET idle_in_transaction_session_timeout = '30s';
-- Cube compares time dimensions as (column::timestamptz AT TIME ZONE 'UTC'). With any other
-- session time zone, a DATE such as 2026-01-01 becomes 2025-12-31 20:00 and lands in the wrong period.
ALTER ROLE cube_reader SET timezone = 'UTC';

GRANT CONNECT ON DATABASE accutax_bk_1_5 TO cube_reader;
GRANT USAGE ON SCHEMA public TO cube_reader;
GRANT SELECT ON public.journal_entries, public.journal_entry_lines, public.chart_of_accounts,
                public.income, public.income_items, public.expense, public.expense_items,
                public.status_type, public.contacts, public.organizations, public.projects,
                public.cost_centers, public.expense_category_type
      TO cube_reader;
```

If Step 1 query 9 shows RLS enabled on these tables, add the policy below. `cube_reader` does not own the tables and never sets `app.current_org`, so RLS would otherwise hide every row. Tenant scope for Cube is layers 1–5 in section 3.

```sql
-- One per table in the GRANT above that has relrowsecurity = true.
CREATE POLICY cube_reader_read ON public.income FOR SELECT TO cube_reader USING (true);
```

If Step 1 query 7 shows no index that starts with `journal_entries.organization_id`:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_journal_entries_org_date_posted
    ON public.journal_entries (organization_id, transaction_date)
    WHERE is_posted;
```

Pre-aggregations need no write access: Cube builds them from Postgres in batches and stores them in Cube Store.

---

## 7. Step 3: deploy Cube

### 7.1 Repository layout (built)

```
Gemini_Brain_Accutax/
  semantic/
    cube.js                     security config (7.4)
    cube.env.example            (7.3); the real cube.env is git-ignored by *.env
    docker-compose.cube.yml     developer machines (7.2)
    model/cubes/                organizations, gl_lines, sales_lines, purchase_lines,
                                open_receivables, open_payables, vat_lines
    model/views/                pnl, balance_sheet, sales, purchases, receivables, payables, vat
    deploy/install_rootless.sh  VM install (7.5)
    deploy/quadlet/             cube.network, cubestore.volume, cubestore.container, cube-api.container
    deploy/quadlet/phase3/      cube-refresh-worker.container (rollups, Phase 3)
    dba/cube_reader.sql         for the DBA (section 6)
    tests/query_rewrite.test.js node --test semantic/tests/*.test.js
  backend/src/gemini_brain/semantic/
    cube_client.py  periods.py  catalog.py  query_metrics.py  metric_table.py  shadow.py
```

### 7.2 Containers and settings

[`docker-compose.cube.yml`](../semantic/docker-compose.cube.yml) (developer machines) and the Quadlet units (the VM) run the same pinned images, `docker.io/cubejs/cube:v1.7.50` and `docker.io/cubejs/cubestore:v1.7.50`, with the same settings:

| Setting | Value | Why |
|---|---|---|
| `CUBEJS_DEV_MODE` | `false` | Production mode: no playground; secret required |
| `CUBEJS_CACHE_AND_QUEUE_DRIVER` | `cubestore` | The production driver; `memory` is for development |
| `CUBEJS_DEFAULT_API_SCOPES` | `meta,data,sql` | No GraphQL. `sql` lets the isolation suite read the compiled SQL |
| `CUBEJS_DB_QUERY_TIMEOUT` | `20s` | The default is 10 minutes; abandoned queries must not keep loading the DB |
| `CUBEJS_CONCURRENCY` / `CUBEJS_DB_MAX_POOL` | `4` / `12` | Explicit; the pool must exceed queue concurrency |
| `CUBEJS_JWT_ALGS` / `_AUDIENCE` / `_ISSUER` | `HS256` / `accutax-cube` / `gemini-brain` | Cube's default auth then checks signature, `aud`, `iss`, `exp` |
| `CUBEJS_TELEMETRY` | `false` | Nothing leaves the VM |
| Port | `127.0.0.1:4000` only | Gemini Brain calls it locally; nginx never proxies it |

### 7.3 `semantic/cube.env`

Copy [`cube.env.example`](../semantic/cube.env.example) to `cube.env` (git-ignored), fill in the `cube_reader` password and the secret (`openssl rand -hex 32`), and `chmod 600` it. Put the same secret in Gemini Brain's `.env` as `CUBE_API_SECRET`. `cube.js` refuses to start if the secret is missing or shorter than 32 characters; there is deliberately no default anywhere.

### 7.4 [`semantic/cube.js`](../semantic/cube.js)

The tenant guard. For every API query, `queryRewrite`:

1. refuses a token whose `exp − iat` exceeds 120 s, or that has no `iat`/`exp` (Cube's default auth has already checked signature, `aud`, `iss` and expiry);
2. refuses a token without `organization_ids`, or with an empty list, more than 25 entries, or anything other than positive integers;
3. collects every member the query references: measures, dimensions, segments, time dimensions, nested `and`/`or` filters, and both forms of `order`;
4. refuses any member outside the allow-listed views (`QUERYABLE_VIEWS`);
5. pushes `<view>.organization_id equals <token orgs>` for each view touched, ANDed at the top level, so no filter the AI writes can widen scope;
6. caps the row limit at 500.

The file throws at load time when `CUBEJS_API_SECRET` is missing or shorter than 32 characters, so Cube does not start. The refresh worker (Phase 3) skips the guard: it serves no API traffic and its port is never published. `equals` with several values compiles to `IN (...)`. When a view is added, add it to `QUERYABLE_VIEWS` and to the isolation suite; a view not on the list is refused.

### 7.5 Install on the VM (106.51.80.81)

Checked on 6 Oct 2026: CentOS Stream 9, Podman 5.8.2, no Docker, 8 CPUs, 7.5 GiB RAM (3.9 GiB available), 65 GB disk free, ports 4000 and 3030 free, NTP clock synced. Phase 1 (`cube-api` + `cubestore`) fits. Recheck memory before adding the refresh worker in Phase 3.

The VM runs rootless Podman under a dedicated `cube` user, as Quadlet units that systemd starts on boot. [`install_rootless.sh`](../semantic/deploy/install_rootless.sh) does the setup and is idempotent: user, subordinate IDs, linger, files copied to `~cube/semantic`, units in `~cube/.config/containers/systemd`. Prerequisites: the DBA has created `cube_reader` (section 6), and `semantic/cube.env` is filled in.

```bash
cd /opt/accutax-ai && git pull && cp semantic/cube.env.example semantic/cube.env && chmod 600 semantic/cube.env
```

Fill in `semantic/cube.env`, then install and start:

```bash
bash /opt/accutax-ai/semantic/deploy/install_rootless.sh --start
```

Smoke tests:

```bash
curl -s http://127.0.0.1:4000/readyz
```

```bash
curl -s -o /dev/null -w '%{http_code}
' http://127.0.0.1:4000/cubejs-api/v1/meta
```

The second must print `403`: no token, no answer. From another machine, `curl -m 5 http://106.51.80.81:4000/readyz` must fail to connect. Do not add a `/cubejs-api` location to nginx. Then, with `CUBE_API_SECRET` set in `/opt/accutax-ai/.env`, run the isolation suite (11.2) and the reconciliation (11.3) on the VM before setting `METRICS_BACKEND=shadow`.

---

## 8. Step 4: the data model

### 8.1 Modelling rules

1. **Every cube and view has `organization_id`.** Rows with a NULL organization never match a tenant filter, so they are invisible. That is the intended fail-closed behavior.
2. **Every join also matches on organization**, for example `c.id = inc.contact_id AND c.organization_id = inc.organization_id`. A mis-linked row from another tenant drops out instead of leaking a name.
3. **Fact cubes are line-grain and wide.** Attributes such as customer and project are joined inside the cube SQL, not as Cube joins. That avoids fan-out and keeps one tenant filter per query. Postgres removes the unused LEFT JOINs on primary keys.
4. **Raw cubes are `public: false`.** Only views are queryable, and they carry the descriptions the AI reads.
5. **No curly braces in SQL literals.** Cube's YAML treats `{...}` as a member reference, so a regex such as `[0-9]{4}` breaks silently. Spell out repeated character classes instead.
6. **Text dates are parsed with a guard.** Values that are not dates become NULL instead of failing the query:

```sql
CASE WHEN CAST(x AS TEXT) ~ '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
     THEN CAST(LEFT(CAST(x AS TEXT), 10) AS DATE) END
```

The first 10 characters of an ISO timestamp are its UTC date, which matches today's reports.

### 8.2 Cubes (raw, `public: false`)

The files are the source of truth; this table says what each holds.

| File | Grain | Source and rules |
|---|---|---|
| [`organizations.yml`](../semantic/model/cubes/organizations.yml) | organization | Name and base currency, joined into every view |
| [`gl_lines.yml`](../semantic/model/cubes/gl_lines.yml) | journal line | Posted entries; account join also matches organization. P&L measures (revenue, other income, cost of sales, operating expenses, gross and net profit, margins) and balance measures (assets, current and fixed assets, liabilities, current liabilities, working capital, current ratio, equity, accumulated earnings, balance difference, cash and bank) |
| [`sales_lines.yml`](../semantic/model/cubes/sales_lines.yml) | invoice line | Section 4.1 rules; customer, project and cost centre joined inside the SQL. Gross sales, credit notes, net sales, output VAT, standard-rated sales, invoice and customer counts |
| [`purchase_lines.yml`](../semantic/model/cubes/purchase_lines.yml) | bill line | Same pattern on `expense`; vendor and expense category |
| [`open_receivables.yml`](../semantic/model/cubes/open_receivables.yml) | invoice | PENDING / PARTIALLY_PAID only, dated today or earlier; total from a `LATERAL` subquery. Outstanding, overdue, counts, days overdue, aging bucket |
| [`open_payables.yml`](../semantic/model/cubes/open_payables.yml) | bill | Same on `expense` |
| [`vat_lines.yml`](../semantic/model/cubes/vat_lines.yml) | VAT line | `UNION ALL` of `{sales_lines.sql()}` and `{purchase_lines.sql()}`, so the document rules exist once |

### 8.3 Views (the only queryable surface)

[`pnl`](../semantic/model/views/pnl.yml), [`vat`](../semantic/model/views/vat.yml), [`sales`](../semantic/model/views/sales.yml) and [`purchases`](../semantic/model/views/purchases.yml) are `flow` views (they need a period). [`balance_sheet`](../semantic/model/views/balance_sheet.yml) is a `balance` view: it needs an as-of date and always groups by account currency. [`receivables`](../semantic/model/views/receivables.yml) and [`payables`](../semantic/model/views/payables.yml) are `current` views and take no date. Each view includes `organization_id` from its fact cube, and `organization_name` and `currency` from `organizations`. Each also carries the `description` and `meta.ai_context` the agent reads.

### 8.4 Checks that guard the model

- [`test_cube_model.py`](../backend/tests/unit/test_cube_model.py) (no database), 42 checks:
  - every cube is hidden;
  - every fact cube has `organization_id`, the organizations join and a primary key;
  - every view exposes the organization members and only members that exist;
  - every view declares how time applies;
  - no stray braces;
  - the views match `QUERYABLE_VIEWS` in `cube.js`.
- [`cube_model_check.py`](../backend/scripts/eval/cube_model_check.py) runs each cube's SQL and measures read-only against Postgres for given orgs, the way Cube would, and times them. Run it after every model change, for the test orgs only.

---

## 9. Step 5: the Python client

### 9.1 Settings (`config/settings.py`)

| Setting | Default | Meaning |
|---|---|---|
| `CUBE_API_URL` | `http://127.0.0.1:4000` | Loopback only |
| `CUBE_API_SECRET` | empty | Shared HS256 secret, at least 32 characters. Empty disables every Cube call |
| `CUBE_JWT_AUDIENCE` / `CUBE_JWT_ISSUER` | `accutax-cube` / `gemini-brain` | Must match Cube's `CUBEJS_JWT_*` |
| `METRICS_BACKEND` | `sql` | `sql`, `shadow` (12.2) or `cube` (Phase 1b, not wired yet) |
| `METRICS_SHADOW_LOG` | `logs/metrics_shadow.jsonl` | Shadow comparisons; organization IDs and figures, no user IDs |
| `REPORT_TIMEZONE` | `Asia/Dubai` | Turns period presets into dates |

### 9.2 [`cube_client.py`](../backend/src/gemini_brain/semantic/cube_client.py)

- `load(query, organization_ids, subject, deadline)` is the only way to Cube. The org IDs must come from `authorize_org_scope`.
- It mints a 60 s token (`organization_ids`, `aud`, `iss`, `iat`, `exp`, `jti`) and refuses to run without a 32-character secret.
- It always adds `<view>.organization_id` to the grouping and refuses a query that spans views.
- It polls through "Continue wait" until a result or the deadline. Any `error`, non-200, non-JSON or missing `data` raises `CubeError`; it never returns an empty result in place of an error.
- Every row's `organization_id` must be in the token. Otherwise it raises `CubeScopeViolation` and writes a critical line to the org-access audit log.
- Numbers come back as `Decimal`. Each call is recorded in the API trace as `cube:<view>`.

## 10. Step 6: the `query_metrics` tool

- [`periods.py`](../backend/src/gemini_brain/semantic/periods.py) turns presets (`today`, `this_month`, `last_month`, `this_quarter`, `last_quarter`, `ytd`, `last_year`, `last_12_months`) or a start/end pair into dates. No period means year-to-date (D2).
- [`catalog.py`](../backend/src/gemini_brain/semantic/catalog.py) reads the views from Cube's `/v1/meta` and caches them for ten minutes. Views with a bad `meta` are skipped. The organization members are hidden from the model.
- [`query_metrics.py`](../backend/src/gemini_brain/semantic/query_metrics.py):
  - `build_query` checks every view, measure, dimension, filter, operator and limit against the catalog, adds the organization grouping, and applies time by the view's kind;
  - `run` executes the query and returns rows, the period note, the currency note and, for margin filters, the "no revenue, no margin" note;
  - `tool_spec` builds the Bedrock Converse tool definition from the catalog.

A `ToolInputError` goes back to the model as the tool result, so it can correct itself (one retry). A `CubeError` goes to the user as "figures are temporarily unavailable". It must never become an empty answer. The agent loop that calls this tool is Phase 2.

### 10.1 Agent prompt rules for this tool

Add these to the agent's system prompt. They replace the SQL-patching instructions in [`coordinator_agent.py`](../backend/src/gemini_brain/agents/coordinator_agent.py), which go when the SQL agent is retired (12.3).

- Every figure comes from a tool result. Do not calculate new figures, except a difference or ratio you show the working for.
- Revenue, income, sales total, expenses, profit and margin questions use `pnl`. Use `sales` or `purchases` only when the user says invoiced or billed, or asks per customer, vendor, project or cost centre.
- Conditions ("above 20%", "negative profit", "more than 50,000") are filters on measures.
- If the catalog has no measure for what was asked, say so plainly and offer the closest one. Never answer under the asked name with a different measure.
- State the period and the currency. Never add amounts across organizations that use different currencies.

### 10.2 Phase 1 bridge: metric table and shadow mode (built)

- [`metric_table.py`](../backend/src/gemini_brain/semantic/metric_table.py) maps the multi-org metric keys to view members. It returns every organization from **one Cube query per view**. A missing metric means nothing was recorded, never zero. An organization whose balances span currencies gets no total.
- [`shadow.py`](../backend/src/gemini_brain/semantic/shadow.py) runs after the answer is final, in a background thread with a 30 s budget, and never raises. It compares the SQL figure each organization got with Cube's and appends one line per organization and metric to `METRICS_SHADOW_LOG`. Revenue, expenses, net profit and margin carry `basis_change: true`, because their source moves from documents to the ledger.
- The seam is `_shadow_metrics` in [`multi_org.py`](../backend/src/gemini_brain/orchestrator/multi_org.py). It runs only when `METRICS_BACKEND=shadow` and a plan exists, and changes nothing the user sees.
- Growth and series metrics are not compared yet; they arrive with the agent in Phase 2.

---

## 11. Step 7: tests

### 11.1 Unit tests (no Cube needed)

| File | What it proves |
|---|---|
| [`test_cube_client.py`](../backend/tests/unit/test_cube_client.py) | "Continue wait" is retried; every error form raises; rows outside the token raise `CubeScopeViolation`; the token is scoped and short-lived; the org grouping is always added |
| [`test_semantic_periods.py`](../backend/tests/unit/test_semantic_periods.py) | Every preset, including year ends and leap years |
| [`test_query_metrics.py`](../backend/tests/unit/test_query_metrics.py) | demo-01 builds the exact expected query; bad input is refused with a message for the model; balance and current views handle time correctly |
| [`test_metric_table_and_shadow.py`](../backend/tests/unit/test_metric_table_and_shadow.py) | One query per view; mixed currencies are never added; shadow writes one line per org and metric and survives Cube failures |
| [`test_cube_model.py`](../backend/tests/unit/test_cube_model.py) | The model invariants in 8.4 |
| [`semantic/tests/query_rewrite.test.js`](../semantic/tests/query_rewrite.test.js) | The `cube.js` tenant guard: scope added and ANDed; raw cubes refused, including inside nested filters, order and time dimensions; bad tokens refused; row cap; no start without a strong secret |

Result on 7 Oct 2026: 103 Python tests and 12 Node tests pass.

### 11.2 Tenant isolation suite (`backend/tests/integration/test_cube_isolation.py`)

This runs against a real Cube and the test orgs, and **must pass before any deploy**. Mark it `@pytest.mark.cube` and run it in CI on every change to `semantic/` or `backend/src/gemini_brain/semantic/`.

| # | Case | Expected |
|---|---|---|
| T1 | Token for org 24; `pnl.net_profit` grouped by org | Only org 24 rows |
| T2 | Token for 24; filter `pnl.organization_name equals <org 25's name>` | Zero rows |
| T3 | Token for 24; `or` filter `[organization_id set, net_profit lt 0]` | Only org 24 rows |
| T4 | Query the raw cube `gl_lines.revenue` | Refused |
| T5 | Query a view not in `QUERYABLE_VIEWS` (add a test view in the test model) | Refused |
| T6 | For every view in `/v1/meta` and every measure: token for 24 | All rows org 24; `/v1/sql` for the same query contains the `organization_id` condition |
| T7 | Token without `organization_ids`; with `[]`; with `["24"]` (string) | Refused |
| T8 | Token with `exp − iat = 3600` | Refused |
| T9 | Expired token; wrong `aud`; wrong `iss`; wrong secret | Refused (403) |
| T10 | Token for 24 and 25; group by org | Rows for both, never others |
| T11 | Port 4000 from outside the VM | Connection fails |
| T12 | Start Cube with an empty `CUBEJS_API_SECRET` | Cube does not start |

### 11.3 Reconciliation (`backend/scripts/eval/cube_reconcile.py`)

For orgs 24–33 and windows {each month of 2026, YTD, 2025}:

- `pnl`: call `api_client.call_api("/report/profit-loss-with-accounts", {}, {"organization_id": org, "start_date": s, "end_date": e}, timeout=30)` and compare `details.revenue.total`, `details.cost_of_sales.total`, `details.operating_expenses.total` and `details.net_profit_loss` with the Cube figures. That endpoint uses `organization_id`, so it is safe to call per org.
- `balance_sheet`: compare with `rpt_balance_sheet` and check `balance_difference = 0`. Seed defects such as negative equity are data issues, not failures; list them.
- Documents: compare with hand-written SQL that applies section 4.1 and was reviewed by finance.
- Date boundary: one entry dated 1 January must appear in January and not in December. This catches the role-timezone issue from section 6.

Write a CSV of every difference above 0.01. The pass rule is zero unexplained differences.

### 11.4 Golden questions

Start `backend/tests/golden/metrics_golden.yaml` with these and grow it to 150–300 real questions, as the review asks. Fix "today" in the harness so periods are stable.

| id | Question (orgs 24–33, today = 2026-10-06) | Expected tool call | Expected answer |
|---|---|---|---|
| demo-01 | Which of the organizations have profit margin above 20%? | `view=pnl, measures=[pnl.net_margin_pct, pnl.revenue, pnl.net_profit], filters=[{pnl.net_margin_pct gt ["20"]}]`, no period, so YTD | Orgs 24 and 25 (as of 6 Oct 2026 data); the margin note shown; one Cube query |
| demo-02 | List all the organizations which are making negative profit | `view=pnl, measures=[pnl.net_profit], filters=[{pnl.net_profit lt ["0"]}]` | Orgs 26, 28, 29, 30, 31, 32, 33 (as of 6 Oct 2026 data); under 10 s end to end |
| g-03 | What was our revenue last month? (one org) | `pnl.revenue`, preset `last_month` | Equals the Accutax endpoint for September 2026 |
| g-04 | Top 5 customers by sales this year | `view=sales, measures=[sales.net_sales], group_by=[sales.customer_name], order desc, limit 5` | Matches the reviewed SQL |
| g-05 | Overdue receivables older than 90 days | `view=receivables, measures=[receivables.overdue_amount], filters=[{receivables.aging_bucket equals ["Over 90 days"]}]` | Matches the reviewed SQL |
| g-06 | Gross margin by organization for 2025 | `pnl.gross_margin_pct`, custom 2025 range | Matches the endpoint; empty for orgs without cost-of-sales accounts, said plainly |
| g-07 | Net VAT payable this quarter | `vat.net_vat_payable`, `this_quarter` | Matches `rpt_vat_input_output` after 4.1 rules |
| g-08 | Cash position as of 30 June 2026 | `balance_sheet.cash_and_bank`, `as_of=2026-06-30` | Matches `rpt_cash_balance`; per account currency |
| g-09 | Receivables as of last March | — | Refuses the past date for `receivables` and explains why |
| g-10 | What is our EBITDA? | — | Says EBITDA is not available and offers net profit; no figure under the EBITDA name |
| g-11 | Total revenue across all my organizations (mixed currencies, test fixture) | `pnl.revenue` | Per-currency totals, never one mixed sum |
| g-12 | Same as demo-01 with a token for org 24 only | — | Only org 24 can appear |

Independent SQL for demo-01 and demo-02 (`tests/golden/sql/demo_01.sql`), hand-written, not generated by Cube:

```sql
SELECT j.organization_id,
       SUM(CASE WHEN c.account_type = 'Revenue' THEN l.credit_amount - l.debit_amount ELSE 0 END) AS revenue,
       SUM(CASE WHEN c.account_type = 'Expense' THEN l.debit_amount - l.credit_amount ELSE 0 END) AS expenses
FROM   journal_entry_lines l
JOIN   journal_entries j   ON j.id = l.journal_entry_id
JOIN   chart_of_accounts c ON c.id = l.account_id AND c.organization_id = j.organization_id
WHERE  j.is_posted
  AND  j.organization_id = ANY(%(orgs)s)
  AND  j.transaction_date BETWEEN %(start)s AND %(end)s
GROUP  BY j.organization_id;
-- demo-01: revenue > 0 AND 100 * (revenue - expenses) / revenue > 20
-- demo-02: revenue - expenses < 0
```

Run on 6 Oct 2026 for orgs 24–33, 1 Jan to 6 Oct 2026 (ledger, AED):

| Org | Revenue | Expenses | Net profit | Net margin | demo-01 | demo-02 |
|---|---|---|---|---|---|---|
| 24 | 5,809,352 | 0 | 5,809,352 | 100.0% | yes | |
| 25 | 6,123,198 | 1,814,502 | 4,308,696 | 70.4% | yes | |
| 26 | −187,193 | 263,832 | −451,025 | none | | yes |
| 27 | −819,325 | −1,823,966 | 1,004,641 | none | | |
| 28 | −190,408 | 373,959 | −564,367 | none | | yes |
| 29 | −151,382 | 798,656 | −950,038 | none | | yes |
| 30 | −29,520 | 264,147 | −293,667 | none | | yes |
| 31 | −143,160 | 14,742 | −157,902 | none | | yes |
| 32 | 60,637 | 599,622 | −538,985 | −888.9% | | yes |
| 33 | 76,541 | 663,504 | −586,963 | −766.9% | | yes |

**Do not demo ledger figures on this seed data.** Six test orgs have negative ledger revenue, and org 27 has negative expenses. These are the known seed defects (ledger does not match invoices in orgs 26–33), not model errors, but a client would read them as broken software. Fix the seed (owner: seed/DBA), or demo on orgs with clean ledgers, before Cube answers in front of anyone.

---

## 12. Step 8: rollout and deletion

### 12.1 Phases and exit criteria

| Phase | Work | Exit criteria |
|---|---|---|
| 0 | Review §7 Phase 0 (models, threshold filters, pool, deadlines, Stop) | As in the review |
| 1 | Steps 1–7 here; `metric_table` behind `metrics_backend=shadow` | Finance signed section 4. DBA work done. Isolation suite 100%. Reconciliation has no unexplained differences. demo-01/02 pass. One week of shadow logs with every difference categorised. |
| 1b | Switch multi-org metrics to `metrics_backend=cube` | Demo set passes on the VM build |
| 2 | Agent loop (review §6.3) with `query_metrics`, `list_documents`, `search_vat_kb`, `app_guide`; shadow-run on the golden set | Better than the current path on accuracy, p95 latency and cost. Zero cross-tenant results. Zero silently dropped conditions. Proposed bars, to agree with the team: ≥ 95% exact figures, p95 ≤ 10 s end to end. |
| 3 | Rollups, read replica, dashboards, MCP server over the same tools (section 13) | p95 targets in section 13 met for a week |

### 12.2 Shadow mode

- Shadow mode never changes what the user sees. The SQL path answers; Cube is compared.
- Every difference goes into one of four categories: rule change (4.4), data defect (seed notes), Cube model bug, or old-path bug. Only model bugs block the switch.
- Run the golden set nightly against the VM build and post pass/fail with p95.

### 12.3 Deletion order

Delete only after Phase 2's exit criteria are met. Move shared code first, or login and chat history break.

1. **Move** `sql_fallback/db_connection.py` → `gemini_brain/db/connection.py` (with the Phase 0 pool), and `sql_safety.assert_read_only` → `gemini_brain/db/safety.py`. Update the importers:
   - `api/auth.py:18`
   - `memory/schema.py:18`, `memory/session_memory.py:18`
   - `reports/engine.py:33-34`
   - `agents/executor.py:17`
   - `orchestrator/gemini_brain_runner.py:45, 85, 583`
   - `tools/formatters.py:932`, `tools/handlers.py:37`
2. **Remove the SQL agent path:** `gemini_brain_runner.py:149` (`_get_coordinator_pipeline`), `finance_agent._task_execute_sql` and the `tenant_sql_guard` import at `finance_agent.py:32`, and the SQL rules in `coordinator_agent.py`.
3. **Delete** `sql_fallback/` (`sql_engine.py`, `tenant_sql_guard.py`, `answer_cleaner.py`, `fast_path.py`, `cost_optimizer.py`, `sql_safety.py`).
4. **Remove the regex routing:**
   - `router/rules.py` (47 patterns), whose importers are `router/fast_router.py:91-92`, `endpoints/keyword_fallback.py:15` and `gemini_brain_runner.py:84`;
   - the regex parts of `multi_org_metrics.py` (42) and `multi_org_lists.py` (31).
5. **Remove** `endpoints/endpoint_selector.py`. Its importers are `gemini_brain_runner.py:47` and `multi_org_plan.py:25`.
6. **Keep** PII redaction, `authorize_org_scope`, the verifier, report artifacts, the VAT KB, the tracers, and the org-scoped `rpt_` reports used by `list_documents`.

---

## 13. Step 9: performance, freshness and scale

**Phase 1 (no rollups).** Every query reads Postgres. Measured on 6 Oct 2026: the Cube-shaped P&L query (subquery, `IN` list, UTC conversion) for 10 orgs, year to date, ran in 0.2 s warm, using `idx_journal_entries_org_id` (26,556 lines). One cold run of a wider query (all `gl_lines` measures) exceeded the 20 s timeout; the cause is not proven, most likely disk cache. That cold-start risk is why the client has a deadline and Cube has a query timeout, and why Phase 3 adds rollups. Targets to measure, not promises:

| Query | Target p95 (Phase 1) | Target p95 (Phase 3, rollups) |
|---|---|---|
| One view, one org, YTD | ≤ 2 s | ≤ 0.5 s |
| One view, 10 orgs, YTD | ≤ 4 s | ≤ 1 s |
| `query_metrics` tool incl. catalog and checks | above + 0.1 s | above + 0.1 s |

**Phase 3 rollup** for `gl_lines`. Sizing from Step 1: in the test orgs, 232,912 posted lines collapse to 144,087 (org, account, day) rows, a ratio of 0.62. If that held for all 66 million lines, a daily rollup would hold about 40 million rows. Measure the real ratio on a replica before choosing day or month granularity:

```yaml
    pre_aggregations:
      - name: daily_by_org_account
        measures:
          - CUBE.revenue
          - CUBE.cost_of_sales
          - CUBE.operating_expenses
          - CUBE.assets
          - CUBE.liabilities
          - CUBE.equity_booked
          - CUBE.accumulated_earnings
          - CUBE.cash_and_bank
        dimensions:
          - CUBE.organization_id
          - CUBE.account_code
          - CUBE.account_name
          - CUBE.account_type
          - CUBE.account_sub_type
          - CUBE.account_currency
          - organizations.name
          - organizations.currency
        time_dimension: CUBE.transaction_date
        granularity: day
        partition_granularity: month
        build_range_start:
          sql: SELECT MIN(transaction_date) FROM public.journal_entries
        build_range_end:
          sql: SELECT GREATEST(MAX(transaction_date), CURRENT_DATE) FROM public.journal_entries
        refresh_key:
          every: 10 minute
          incremental: true
          update_window: 35 day
        indexes:
          - name: by_org
            columns:
              - CUBE.organization_id
```

- **One shared rollup for all tenants.** It has `organization_id` as a dimension, and `queryRewrite` filters it per query. Per-tenant models (`contextToAppId` per org) are not practical at 10,045 organizations.
- **Day granularity** so "YTD to today" ranges hit the rollup. The calculated measures (`net_profit`, margins) are served from it because their leaf measures are additive sums. Confirm with `usedPreAggregations` in the response.
- **Freshness.** Partitions inside the last 35 days refresh every 10 minutes. A posting backdated further than that is not picked up until its partition rebuilds, so agree the window with finance (it should cover month-end close). Every answer shows "data as of" from `lastRefreshTime`.
- **Read replica.** Point the refresh worker at a replica before enabling rollups for all tenants. Otherwise it scans the same database customers use.
- **Capacity.** Plan about 1 GB RAM for the API, 0.5 GB for the refresh worker, and 1–2 GB plus disk for Cube Store. Check with `free -h` (7.5). Scale out by adding API containers; they are stateless.
- `CUBEJS_ROLLUP_ONLY` stays `false`, so queries that do not match a rollup still answer from Postgres.

---

## 14. Operations

- **Health:** monitor `/readyz` and `/livez` on 127.0.0.1:4000 and alert after two failures.
- **Logs:** Cube logs at `info` include each query's SQL and duration. Ship container logs to journald. Alert on any `cube_scope_violation` in the org-access audit log (page someone), and on a `CubeError` rate above 2% over 15 minutes.
- **Failure policy:** if Cube is down, `query_metrics` answers "figures are temporarily unavailable". There is no fallback to free-form SQL (D5). In Phase 1 the SQL path is still primary, so nothing changes for users.
- **Upgrades:** pin versions. Upgrade monthly on staging, run 11.1–11.4, then roll out. Upgrade Cube and Cube Store together.
- **Secret rotation:** a new `CUBEJS_API_SECRET` needs a restart of Cube and Gemini Brain together. Tokens live 60 s, so there is no long overlap to manage.
- **Backups:** Cube Store holds only rebuildable data. The model and config live in git.

---

## 15. Issues found outside this work

These came up while checking v1 against the code. They are not Cube tasks; they belong to their owners.

1. **Accutax P&L screen overstates net profit when cost of sales exists.** [`ProfitLossView.jsx:94-107`](../../accutax_bk_frontend/src/components/ProfitLossView.jsx#L94) totals revenue minus `operating_expenses` only. The endpoint's own `net_profit_loss` also subtracts cost of sales ([report.service.ts:521-523](../../accutax_bk_backend/src/modules/report/report.service.ts#L521)). The same view shows margin "0.0" with no revenue, and drops a parent account's own postings when it has children (check whether parents allow posting). Owner: Accutax frontend.
2. **`getProfitLossWithChartAccounts` returns all tenants when `organization_id` is missing** ([report.service.ts:436,482](../../accutax_bk_backend/src/modules/report/report.service.ts#L482)). Same class as the 11 methods in review §4. Owner: Accutax backend.
3. **`getProfitLossReport`** counts VAT inside income, sets cost of sales to 0, and adds `VENDOR_CREDIT` as an expense ([report.service.ts:326](../../accutax_bk_backend/src/modules/report/report.service.ts#L326)). Finance should say whether that report should stay.
4. **Gemini's current document reports** do not filter `income_type` or `is_draft` (4.4). Cube fixes this for metrics. The `rpt_` reports kept for `list_documents` need the same rules.
5. **`estimate_conversion`** filters `income_type = 'ESTIMATE'`, but the Accutax enum value is `ESTIMATION` ([definitions.py:376](../backend/src/gemini_brain/reports/definitions.py#L376)). Step 1 query 2 shows which one the data holds.
6. **"Cost of sales / gross profit not supported"** in `multi_org_metrics.py` `UNSUPPORTED`. Step 1 confirmed the sub-types are in use, so remove that entry (and working capital, current ratio, other income) once Cube answers.
7. **Accutax AR aging ignores payment status.** `getArAgingSummary` ([report.service.ts:1603](../../accutax_bk_backend/src/modules/report/report.service.ts#L1603)) sums every non-draft invoice, including PAID, CANCELLED and VOIDED ones, ignores `amount_paid`, and ages by `created_date` rather than the due date. The receivables it shows are overstated. Owner: Accutax backend.
8. **Parent accounts with their own postings.** In the test orgs, 89 parent accounts carry postings. The Accutax P&L screen (item 1) drops them from its totals; the endpoint's totals include them. Owner: Accutax frontend, and finance (should parent accounts allow posting?).
9. **About 4.95 GB of leftover `_ccnew` indexes** on `income`, `journal_entries` and `journal_entry_lines`, from interrupted `REINDEX CONCURRENTLY` runs. Most are invalid. Postgres still updates invalid indexes on every write, so they slow inserts on the largest tables and waste disk. List them with `SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname LIKE '%ccnew%';` and drop with `DROP INDEX CONCURRENTLY`, after checking each valid one duplicates an existing index. Owner: DBA.
10. **Bad currency codes:** one organization has currency `STR`, and some accounts have `AE`. Owner: data/Accutax validation.

---

## 16. Phase 1 status (7 Oct 2026)

| Step | State |
|---|---|
| 1. Verify the data | Done (5.1) |
| 2. DBA prerequisites | Waiting: hand [`cube_reader.sql`](../semantic/dba/cube_reader.sql) to the DBA |
| 3. Deploy Cube | Files ready (7.1, 7.5). Waiting on step 2, the secret, and a go-ahead to install on the VM |
| 4. Data model | Built; SQL checked read-only against the test orgs; Cube compilation checked at deploy |
| 5. Python client | Built and unit-tested |
| 6. `query_metrics` tool | Built and unit-tested; the agent loop is Phase 2 |
| 7. Tests | Unit tests pass (11.1). The isolation suite and reconciliation run on the VM after deploy |
| Shadow mode | Built; turn on with `METRICS_BACKEND=shadow` once 11.2 and 11.3 pass |
| Finance sign-off | Open: section 4, D1, and the ACCEPTED/RECEIVED question (4.1) |

---

## 17. References

- Cube REST API, including "Continue wait": https://docs.cube.dev/reference/core-data-apis/rest-api
- Configuration options (`queryRewrite`, `checkAuth`, `jwt`, `contextToAppId`): https://docs.cube.dev/reference/configuration/config
- Environment variables: https://docs.cube.dev/reference/configuration/environment-variables
- Cubes, views, measures, pre-aggregations: https://docs.cube.dev/reference/data-modeling/cube, `/view`, `/measures`, `/pre-aggregations`
- Data access policies (a possible extra in-Cube layer in Phase 3, once array-valued row filters are verified): https://docs.cube.dev/docs/data-modeling/data-access-policies
- Releases (v1.7.50, 2 Oct 2026): https://github.com/cube-js/cube/releases
