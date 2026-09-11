# Accutax Database — Deep Data Analysis

**Database:** `accutax_bk_1_5` · PostgreSQL 13.23 (x86_64-redhat-linux)
**Host:** reached over SSH tunnel `ssh -L 5435:localhost:5432 root@106.51.80.81 -p 7676` → `127.0.0.1:5435`
**Total size:** 33 GB · **133 base tables** in a single `public` schema
**Analysis date:** 2026-08-19 · **Method:** live read-only inspection (`pg_catalog`, `pg_stats`, per-tenant aggregation)

> This document describes what is *actually in the data*, not what the schema allows. Every number below
> was measured against the live database. Where a column exists but is unusable, that is called out
> explicitly — those cases are the main reason most tenants cannot answer real user questions.

---

## Contents

| # | Section | What you get |
| :-- | :--- | :--- |
| 1 | [Why this analysis exists](#1-why-this-analysis-exists) | The problem in three bullets |
| 2 | [Connecting](#2-connecting) | Tunnel + psql command |
| 3 | [The domain model](#3-the-domain-model) | Entity map and the three joins that matter |
| 4 | [Physical inventory](#4-physical-inventory--where-the-33-gb-lives) | Where the 33 GB lives; what is empty |
| 5 | [Core table anatomy](#5-core-table-anatomy) | Column-by-column reality for every major table |
| 6 | [Reference data](#6-reference-data-the-lookup-tables) | Status, contact, payment, tax lookups |
| 7 | [Traps](#7-traps-for-anyone-writing-sql-against-this-database) | Ten ways to get a confidently wrong answer |
| 8 | [Tenant coverage](#8-tenant-coverage--the-core-problem) | The master-data vs transactions split |
| 9 | [Capability matrix](#9-what-gemini-brain-can-and-cannot-answer-from-this-data) | What can and cannot be answered |
| 10 | [Config mismatches](#10-configuration-mismatches-found-while-analysing) | Where the repo disagrees with the database |
| 11 | [Canonical SQL](#11-canonical-sql-for-this-schema) | Copy-paste queries that respect every trap |
| **12** | **[The organizations worth using](#12-the-organizations-worth-using)** | **The five tenants to pick, with trade-offs** |
| 13 | [Method](#13-how-this-analysis-was-produced) | How it was measured, and a 92× query speedup |
| 14 | [Recommendations](#14-recommendations) | What to fix, in the repo and the database |

**In a hurry?** Jump to [§12](#12-the-organizations-worth-using) for the organizations, and
[§7](#7-traps-for-anyone-writing-sql-against-this-database) for the SQL traps.

**Visual summary:** the same findings as charts —
<https://claude.ai/code/artifact/2399916a-a934-416b-849d-1b578a429335>

---

## 1. Why this analysis exists

Gemini Brain answers natural-language financial questions by either calling the Accutax REST API or
falling back to NL-to-SQL against this database. Both paths assume the tenant actually *has* data.
In practice the database is a synthetic, seeded multi-tenant corpus in which:

- **10,028 organizations exist, but only ~41% have a single financial transaction.**
- Master data (contacts, items, chart of accounts) was seeded for nearly *every* org, which makes an
  org look populated while it has no invoices, bills, or journal entries at all.
- Several whole modules (banking, customer payments, VAT returns, reconciliation) are empty database-wide.

So "pick a good organization" is not cosmetic — it decides whether a demo answers or returns *"no data found"*.

---

## 2. Connecting

```bash
# 1. open the tunnel (keep this shell running)
ssh -L 5435:localhost:5432 root@106.51.80.81 -p 7676

# 2. connect
psql "host=127.0.0.1 port=5435 dbname=accutax_bk_1_5 user=postgres password=12345678"
```

Credentials come from `.env` (`DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`). The repo also references a
read-only role `gemini_brain_ro` (commented out) — preferable for analysis work.

---

## 3. The domain model

Accutax is a GCC/UAE-oriented bookkeeping platform. The data model is a fairly standard double-entry
accounting core with a sales ledger, a purchase ledger, and a general ledger, plus inventory and audit.

```
                                  organizations (10,028)
                                  the tenant boundary — organization_id
                                              │
        ┌──────────────────┬──────────────────┼──────────────────┬───────────────────┐
        │                  │                  │                  │                   │
   SALES LEDGER      PURCHASE LEDGER     GENERAL LEDGER      MASTER DATA          AUDIT
        │                  │                  │                  │                   │
   income  5.6M       expense  5.5M     journal_entries    contacts   1.9M    audit_trails 6.3M
      │ 1:N              │ 1:N             10.8M              items     2.0M   invoice_history 10.6M
      ▼                  ▼                   │ 1:N          chart_of_accounts 770K
  income_items       expense_items           ▼               tax_rates  200K
     10.6M              8.0M          journal_entry_lines    branches   199K
                                            29.5M            projects   202K
                                                             cost_centers 189K
```

**The three joins that matter most**

| Purpose | Join |
| :--- | :--- |
| Invoice value | `income.id = income_items.income_id` → `SUM(income_items.line_amount)` |
| Bill value | `expense.id = expense_items.expense_id` → `SUM(expense_items.line_amount)` |
| GL / trial balance | `journal_entries.id = journal_entry_lines.journal_entry_id` → `SUM(debit_amount)`, `SUM(credit_amount)` |

`income` and `expense` carry **no line-derived total of their own** except `income.amount_due`, which is
the **gross, tax-inclusive** total:

```
income.amount_due  =  SUM(income_items.line_amount) + SUM(income_items.tax_amount)
```

Verified row-by-row: for a tax-bearing tenant (org 25) there are **zero** invoices where this does not hold.
Because 99.7% of line items have `tax_amount = 0`, `amount_due` *looks* identical to `SUM(line_amount)` for
most tenants — but on the few tenants that do carry tax the two differ, so **decide explicitly whether you
want net or gross revenue.** `expense` has no equivalent column at all; bill totals *must* be aggregated
from `expense_items`.

---

## 4. Physical inventory — where the 33 GB lives

| Table | Rows | Size | Role |
| :--- | ---: | ---: | :--- |
| `journal_entry_lines` | 29.5M | 7,472 MB | GL postings (debit/credit legs) |
| `income` | 5.7M | 4,832 MB | Sales invoices |
| `journal_entries` | 10.8M | 4,358 MB | GL entry headers |
| `invoice_history` | 10.6M | 2,811 MB | Invoice change log |
| `income_items` | 10.6M | 2,416 MB | Invoice lines |
| `audit_trails` | 6.3M | 2,411 MB | Transaction audit |
| `expense` | 5.5M | 2,098 MB | Purchase bills |
| `expense_items` | 8.0M | 1,640 MB | Bill lines |
| `contacts` | 1.9M | 1,141 MB | Customers + vendors |
| `items` | 2.0M | 1,044 MB | Products/services |
| `inventory_movements` | 1.9M | 771 MB | Stock movements |
| `job_failures` | 308K | 626 MB | Background job errors |
| `audit_logs_2026_08` | 1.3M | 588 MB | Audit log (current partition) |
| `inventory_quantities` | 2.1M | 422 MB | Stock on hand |
| `sub_contacts` | 2.0M | 376 MB | Contact persons |
| `chart_of_accounts` | 770K | 179 MB | CoA per tenant |
| `tax_rates` | 200K | 46 MB | Tax rate master |
| `branches` | 199K | 44 MB | Branches |
| `projects` | 202K | 28 MB | Projects |
| `cost_centers` | 189K | 26 MB | Cost centres |
| `organizations` | 10,028 | 12 MB | Tenants |
| `users` | 1,018 | 792 kB | Login accounts |

**64 of the 133 base tables are completely empty** (verified with exact `COUNT(*)`, not `reltuples`).
38 of those are unused monthly `audit_logs_YYYY_MM` partitions — only `audit_logs_2026_08` holds data.
The other 26 are whole features that were never populated:

```
banking      bank_transactions · bank_transaction_matches · bank_transaction_rules · reconciliations
payments     customer_payment · customer_payment_items
inventory    warehouses · inventory_transfers · inventory_transfer_lines · inventory_ledger
             inventory_fifo_layers · inventory_config
tax          vat_returns · tax_rule_conditions
documents    delivery_notes · delivery_note_lines · document_templates · email_uploads · email_attachments
reference    city_type · province_type · role_type
other        collaborators · contact_reporting_tags · audit_log_archives · model_arena_project_files
```

A further 20-odd tables are technically non-empty but hold only a handful of rows — `tax_calculations` (8),
`tax_rules` (16), `vat_configs` (16), `tax_entries` (2), `tax_adjustments` (6), `inventory_adjustments` (1),
`documents` (16), `bank_accounts` (5), `user_organizations` (7). None of these is usable for analysis.

---

## 5. Core table anatomy

### 5.1 `organizations` — the tenant root (10,028 rows)

27 columns. Every tenant is a UAE "inland" company billing in AED.

| Field | Reality |
| :--- | :--- |
| `name` | Synthetic pattern `"<Industry>_User<N>_Org<M>"`, e.g. `Professional & Consulting Services_User1_Org4` |
| `currency` | `AED` for 10,027 of 10,028; one stray `STR` |
| `company_type` | `inland` for **100%** (enum also allows `free_zone` — never used) |
| `country_id` | `1` (UAE) for all |
| `emirate` | Evenly spread over the 7 emirates (~1,400 each) + 14 rows literally containing `"string"` |
| `industry_type_id` | Evenly spread over all 11 industries (845–975 orgs each) |
| `vat_registeration` | `true` for 10,026 of 10,028 |
| `trn_registeration_number` | Populated for 10,010 |
| `id` range | **14 – 10,120** (there is no organization 1–13) |

Ownership: 1,004 users own orgs; **1,000 users own exactly 10 orgs each** — the corpus was generated as
1,000 × 10. Note `user_organizations` is **empty**, so tenancy is expressed solely by
`organizations.user_id` and by `organization_id` columns on the fact tables.

### 5.2 `income` — sales invoices (5,641,954 rows across 4,150 orgs)

44 columns, but only about half carry information.

| Column | Value in practice |
| :--- | :--- |
| `organization_id` | tenant key — indexed |
| `invoice_date`, `due_date`, `start_date`, `end_date` | **`VARCHAR`, not `date`** — hold full ISO timestamps like `2022-03-17T10:19:04` |
| `amount_due` | gross invoice total = `SUM(line_amount) + SUM(tax_amount)`. 3.25% are `0.00` |
| `status_type_id` | genuinely varied — see §6 |
| `contact_id` | the customer |
| `journal_entry_id` | set for ~91% of rows (8.7% never posted) |
| `invoice_number`, `reference`, `purchase_order` | unique-ish text |
| `branch_id` | NULL for 21% overall — but **bimodal**: the high-volume orgs 24/25/27/28 have it NULL for *every* row, while others (e.g. org 874) populate ~70% |
| `project_id` | NULL for 0.15% |

**Dead columns in `income` — 100% NULL:**
`voided_at`, `voided_by`, `void_reason`, `reversal_journal_entry_id`, `fulfilled_at`, `fulfilled_by`,
`account_id`, `warehouse_id`, `next_invoice_date`, `last_generated_at`, `generation_idempotency_key`.

**Single-value columns in `income` — present but carry no signal:**

| Column | The one value it ever holds |
| :--- | :--- |
| `amount_paid` | `0.00` (100%) |
| `income_type` | `INVOICE` |
| `fulfillment_status` | `PENDING` |
| `reference_id` | `0` |
| `terms_and_conditions` | `"Terms & Conditions"` |
| `start_date`, `end_date` | `""` (empty string) |
| `is_recurring`, `never_expires`, `repeat_frequency_type_id` | constant |
| `is_draft` | effectively all `false` (drafts < 0.05%) |

### 5.3 `expense` — purchase bills (5.5M rows across 4,150 orgs)

29 columns. Same shape as `income`, with its own gaps.

| Column | Reality |
| :--- | :--- |
| `reception_date` | `VARCHAR` ISO timestamp — the bill date |
| `expense_category_type_id` | 10 of 13 categories used — genuinely varied |
| `status_type_id` | 6 values, near-uniform |
| `journal_entry_id` | set for ~97.5% |
| `amount_paid` | `0.00` (100%) — same dead column as income |
| `due_date` | **99.93% NULL** |
| `account_id` | **99.97% NULL** |
| `voided_at/by`, `void_reason`, `reversal_journal_entry_id`, `warehouse_id` | 100% NULL |
| `expense_type` | `EXPENSE` for 99.9% (`BILL` and `CASH_EXPENSE` exist but are negligible) |
| `is_draft` | effectively all `false` (drafts < 0.05%) |

Expense has **no `amount_due`** — totals only exist in `expense_items.line_amount`.

### 5.4 `income_items` / `expense_items` — the money

| Column | `income_items` | `expense_items` |
| :--- | :--- | :--- |
| `line_amount` | the real value, median ≈ 3,458 AED | the real value |
| `unit_price` / `unit_cost` | populated, median ≈ 1,860 | populated |
| `quantity` | only ever 1, 2 or 3 | 1 for 99.9% |
| `tax_amount` | **0.00 for 99.73% of rows** | **0.00 for 99.88%** |
| `tax_rate_id` | populated | **99.85% NULL** |
| `discount_percent`, `discount_amount` | `0.00` (100%) | `0.00` (100%) |
| `cost_center_id` | **100% NULL** | **100% NULL** |

This is the single most consequential finding in the dataset: **VAT is effectively absent.** The product is
positioned around UAE 5% VAT, but virtually no line item carries tax, and discounts do not exist at all.

Measured across the whole table: of **11,240,243** income lines, exactly **27,212 (0.24%)** carry tax,
totalling **AED 11,244,262.74**. Those 27,212 lines are not spread across tenants —

```
organization 24   AED  5,595,641.23
organization 25   AED  5,648,621.51
                  ─────────────────
total             AED 11,244,262.74   ← identical to the database-wide total
```

**Organizations 24 and 25 hold 100% of the income tax data in this database.** Every other tenant — all
10,026 of them — has zero tax on every line.

The purchase side is the same story: of **8,600,337** expense lines only **6,429 (0.07%)** carry tax,
totalling **AED 2,711,360.58** — and since org 24 has no bills at all, org 25 is effectively the sole holder
of input tax too. Any VAT question asked of any other organization returns `0.00`.

### 5.5 `journal_entries` / `journal_entry_lines` — the general ledger

`journal_entries` (10.8M rows, 4,162 orgs) is well populated: `transaction_date` is a real `DATE`,
`total_debit` and `total_credit` are populated, `source_type` is `EXPENSE` (52%) / `INCOME` (48%) with a
negligible tail of `PAYMENT`. `MANUAL`, `BANK_TRANSACTION` and `ADJUSTMENT` are defined in the enum but
never used — **there are no manual journals in this data.**

`is_posted` is `true` for 100%, `is_reversal` `false` for 100%, and `reversed_entry_id` / `reversal_reason`
are 100% NULL — so there are no reversals to analyse.

`journal_entry_lines` (29.5M rows) is the healthiest table in the database: no NULLs, `debit_amount`,
`credit_amount`, `account_id` and `line_order` (1–5) all populated. **This is the reliable source for any
balance-sheet, P&L or trial-balance question.**

### 5.6 `chart_of_accounts` — 83-account UAE template (770K rows, all 10,028 orgs)

Each tenant gets the same well-formed 83-account chart, correctly typed across
`Asset` / `Liability` / `Equity` / `Revenue` / `Expense` and sub-typed (`Current Asset`, `Fixed Asset`,
`Operating Expense`, `Cost of Sales`, …), with `cash_flow_type` of `Operating` (79%) / `Financing` (11%) /
`Investing` (10%). It includes proper UAE VAT accounts:

```
1210  Input VAT Recoverable        2110  Output VAT Payable      2115  VAT Payable
4010  Sales - Standard Rated (5% VAT)   4020  Sales - Zero Rated   4030  Sales - Exempt
```

> **`chart_of_accounts.balance` is `0.00` for 100% of rows.** The column exists and looks authoritative,
> but it is never maintained. Any account balance must be computed from `journal_entry_lines`.
> `is_active` is `true` everywhere; `created_by`/`updated_by`/`version` are constants.

### 5.7 `contacts` — customers and vendors (1.9M rows, 10,002 orgs)

57 columns and, unusually for this database, almost all of them are populated — name, email, phone,
TRN, bank details, IBAN, SWIFT, and full billing *and* shipping addresses. `contact_type_id` splits
almost exactly 50/50 between **4 = Customer** and **5 = Vendor**. `is_deleted` is `false` everywhere and
`currency` is 100% NULL.

Two oddities worth knowing before quoting this data back to a user:

- `city` is **`Riyadh` for 49.6%** of contacts (Saudi Arabia) even though every organization is UAE;
  the remainder are spread over Dubai/Sharjah/Abu Dhabi/Ajman/RAK.
- `bank_name` follows the same 49.6% skew (`Emirates NBD`), and `tax_treatment` is `vatregistered` for all.

### 5.8 `items` — products and services (2.0M rows, 10,002 orgs)

Split ~50/50 between `PRODUCT` and `SERVICE`. `unit_cost`, `unit_price`, `average_unit_cost`,
`quantity_on_hand` and `sku` are populated; `expense_account_id` / `revenue_account_id` link to the CoA.
`unit_of_measure` is `pcs` for 100%, and `is_active` / `is_tracked_inventory` are `true` for 100%.

**100% NULL:** `default_warehouse_id`, `warehouse_id`, `photo`, `inventory_account_id`, `cogs_account_id`,
`tax_authority_metadata`, `attachments`.

### 5.9 Audit tables — present but hollow

`audit_trails` holds 6.3M rows but covers only **869 organizations**, and its analytical columns are empty:

```
amount / debit_amount / credit_amount ....... 0.00 for every row
account_code / account_name ................. 100% NULL
old_values / new_values / changed_fields .... 100% NULL
transaction_number / transaction_date ....... 90% NULL
```

What survives is `action_type` (`CREATE`), `transaction_type` (`INVOICE`/`EXPENSE`/…), a generic
`description` ("Expense transaction"), `user_name` and `created_at`. So "who changed what, and from what
to what" **cannot be answered** — only "something of this type was created".

`invoice_history` (10.6M rows, 4,161 orgs) is richer — it has a `changes` JSONB populated on 48% of rows —
but every row was written between **2026-06-19 and 2026-08-06**. It is not a historical record; it is an
artefact of the seeding run.

The 38 monthly `audit_logs_YYYY_MM` partitions are all empty except `audit_logs_2026_08` (1.26M rows).

### 5.10 Inventory — opening balances only

`inventory_movements` (1.9M rows, 10,022 orgs) looks substantial but:

- `movement_type` is **`RECEIPT` for 100%**
- `reference_type` is **`OPENING_BALANCE` for 100%**
- `warehouse_id`, `from_state`, `to_state`, `reversed_by_movement_id` are 100% NULL
- every row was created in a five-day window in June 2026

There are **no issues, transfers, or adjustments** — so stock movement, consumption and turnover questions
have no data to work from. `warehouses`, `inventory_adjustments`, `inventory_transfers` and
`inventory_fifo_layers` are all empty. `inventory_quantities` (2.1M rows) holds only the opening position.

### 5.11 Banking — effectively non-existent

| Table | Rows |
| :--- | ---: |
| `bank_accounts` | **5** |
| `bank_transactions` | 0 |
| `bank_transaction_matches` | 0 |
| `bank_transaction_rules` | 0 |
| `reconciliations` | 0 |
| `customer_payment` | 0 |
| `supplier_payments` | 1,786 — **all belonging to organization 25** |

The five `bank_accounts` rows belong to `organization_id` 1 and 10100, are denominated in USD/INR, and
three of them contain literal placeholder text (`'string'`). Note that **organization 1 does not exist**
(org ids start at 14), so those rows are orphans.

**Consequence:** any question about cash position, bank balance, uncategorized transactions, bank rules,
reconciliation, or customer payments will return nothing for every realistic tenant. The
`bank_balances`, and payment-related fast paths in `sql_fallback/fast_path.py` cannot succeed.

---

## 6. Reference data (the lookup tables)

These are small, complete, and shared by all tenants — worth memorising when writing SQL.

**`status_type`** — used by both `income.status_type_id` and `expense.status_type_id`

| id | code |
| :-- | :--- |
| 1 | ACCEPTED |
| 2 | PAID |
| 3 | CANCELLED |
| 4 | PENDING |
| 5 | RECEIVED |
| 6 | VOIDED |
| 7 | PARTIALLY_PAID |

Observed distribution on `income`: RECEIVED 20.3%, VOIDED 20.3%, PENDING 20.1%, ACCEPTED 19.7%,
PAID 19.6%, CANCELLED 0.1%. **Only ~20% of invoices are `PAID`** — and `PARTIALLY_PAID` never occurs.

**`contact_type`** — **only two values exist**

| id | code |
| :-- | :--- |
| 4 | CUSTOMER |
| 5 | VENDOR |

> ⚠️ `src/gemini_brain/config/api_catalog.py` documents `contact_type_id (4=customer, 1=vendor, 2=vendor, 3=vendor)`.
> That is wrong against this database — **vendors are `5`**, and ids 1–3 do not exist in `contact_type`.
> Any vendor query built from the catalog description returns zero rows.

**`payment_type`** — 1 BANK_TRANSFER, 2 CASH, 3 STRIPE, 4 CHEQUE, 5 CREDIT_CARD, 6 PAYPAL
(only 1, 2, 3 appear in the data, ~33% each).

**`expense_category_type`** — 13 categories (RENT, SALARIES, MARKETING, UTILITIES, TRANSPORT,
SOFTWARE_SAAS, REPAIRS_MAINTENANCE, TRAVEL, PROFESSIONAL_SERVICES, OFFICE_SUPPLIES, INSURANCE,
MISCELLANEOUS, TAXES_LICENSES); 10 are used.

**`industry_type`** — 11 industries. **`item_type`** — 1 PRODUCT, 2 SERVICE.
**`repeat_frequency_type`** — MONTHLY/WEEKLY/QUARTERLY/DAILY/YEARLY. **`roles`** — 12 roles with JSONB permissions.

**`tax_rates`** (200K rows, 10,017 orgs — the widest coverage of any table)

Every org gets tax rate master rows, but they describe the **wrong tax regime**:

```
tax_rate      : 12.00, 0.00, 18.00, 5.00, 10.00
tax_type      : no_vat, zero_rated, out_of_scope, standard, exempt
tax_authority : CBIC          ← India's Central Board of Indirect Taxes & Customs
country_id    : 100% NULL
```

12% / 18% are **Indian GST** brackets, not UAE VAT. Combined with §5.4 (99.7% of line items carry no tax
at all), tax and VAT reporting is the weakest area of this dataset.

---

## 7. Traps for anyone writing SQL against this database

These are the mistakes that produce a confident, wrong answer.

**1. Dates are `VARCHAR`, and they hold timestamps.**
`income.invoice_date`, `income.due_date`, `expense.reception_date` are `character varying` containing
`2022-03-17T10:19:04`. Only `journal_entries.transaction_date` is a real `DATE`.

```sql
-- WRONG: misses everything, because the stored value has a time component
WHERE invoice_date = '2026-03-17'
-- WRONG: 'YYYY-MM-DD' compares fine, but BETWEEN on the upper bound silently drops the last day
WHERE invoice_date BETWEEN '2026-01-01' AND '2026-12-31'
-- RIGHT: half-open range on the string (works because ISO-8601 sorts lexicographically)
WHERE invoice_date >= '2026-01-01' AND invoice_date < '2027-01-01'
-- RIGHT: or cast explicitly
WHERE CAST(invoice_date AS date) BETWEEN DATE '2026-01-01' AND DATE '2026-12-31'
```

**2. `amount_paid` is always `0.00`.** On both `income` and `expense`. Anything phrased as "how much have we
collected / paid", "outstanding balance", or AR/AP ageing computed as `amount_due - amount_paid` will report
that *every* invoice is fully unpaid. Use `status_type_id` instead (`2 = PAID`).

**3. `chart_of_accounts.balance` is always `0.00`.** Compute balances from `journal_entry_lines`:

```sql
SELECT a.account_code, a.account_name, a.account_type,
       SUM(l.debit_amount) - SUM(l.credit_amount) AS balance
FROM chart_of_accounts a
JOIN journal_entry_lines l ON l.account_id = a.id
JOIN journal_entries je    ON je.id = l.journal_entry_id
WHERE a.organization_id = :org AND je.organization_id = :org
GROUP BY a.account_code, a.account_name, a.account_type
ORDER BY a.account_code;
```

**4. Expense has no total column.** `income.amount_due` exists; there is no `expense.amount_due`.
Always aggregate `expense_items.line_amount`.

**5. Tax is not there.** 99.7%+ of line items have `tax_amount = 0.00`, and `expense_items.tax_rate_id`
is 99.85% NULL. A "net VAT liability" answer will be `0` — which is worse than an error, because it looks valid.

**6. Referential integrity is not enforced.** Only 60 foreign keys exist in the whole database, and the
transactional core has almost none: `income.organization_id`, `income.contact_id`, `income_items.income_id`,
`expense.organization_id` and `chart_of_accounts.organization_id` have **no FK constraint**. Only
`journal_entry_lines.journal_entry_id` and `items.organization_id` are protected. Orphans are possible and
must be checked, not assumed.

**7. `VOIDED` invoices have no void metadata.** 20.3% of invoices carry `status_type_id = 6` (VOIDED) while
`voided_at`, `voided_by` and `void_reason` are 100% NULL. Decide deliberately whether to exclude status 6
from revenue — the data gives no other signal.

**8. The data runs into the future.** Transactions span **2021-01-01 to 2026-12-31**. With today at
2026-08-19, "this year" includes ~4 months of future-dated invoices. Any "revenue this year" figure is
inflated unless the query caps at the current date.

**9. Tenant filtering must be explicit on every table.** `income_items`, `expense_items` and
`journal_entry_lines` have **no `organization_id` column** — they can only be scoped by joining their parent.
`sql_fallback/sql_engine.py`'s `TENANT_TABLES` set does not include `journal_entries`, `income_items`,
`expense_items` or `journal_entry_lines`, so its auto-injected `organization_id` guard does not cover them.

**10. `contacts` and `chart_of_accounts` have no index on `organization_id`.**
Per-tenant queries against them sequential-scan 1.1 GB and 179 MB respectively. `income`, `expense`,
`journal_entries`, `items`, `tax_rates`, `branches`, `projects`, `cost_centers`, `inventory_*`,
`audit_trails` and `invoice_history` *are* indexed on `organization_id`.

> **Practical note — ~2 GB of duplicate indexes.** Three tables carry leftover `_ccnew` indexes from an
> interrupted `REINDEX CONCURRENTLY`:
>
> | Table | Duplicate indexes | of total | Wasted |
> | :--- | ---: | ---: | ---: |
> | `income` | 22 | 40 | 1,079 MB |
> | `journal_entry_lines` | 12 | 16 | 630 MB |
> | `journal_entries` | 18 | 24 | 316 MB |
> | **Total** | **52** | | **~2,025 MB** |
>
> `income` alone has three copies of its primary key. Dropping these reclaims ~2 GB and improves write
> throughput.

### Integrity checks actually run

I ran these against the five candidate organizations rather than assuming. Results are reassuring on the
accounting core and revealing at the edges.

| Check | Result |
| :--- | :--- |
| `journal_entries.total_debit = total_credit` | ✅ **0 unbalanced** on every org tested |
| Journal *lines* balance per entry | ✅ **0 unbalanced** |
| `income.amount_due` = `SUM(line_amount + tax_amount)` | ✅ **0 mismatches** |
| Invoices with no line items | ✅ 0 |
| Bills with no line items | ⚠️ 7 (org 27), 1 (org 28), 0 elsewhere |
| `income.contact_id` pointing at a missing contact | ✅ 0 orphans |
| Invoice referencing a **contact of another tenant** | ✅ 0 — no cross-tenant leakage |
| Line item referencing **another tenant's GL account** | ✅ 0 |
| Journal line referencing **another tenant's GL account** | ✅ 0 |
| Invoices flagged `VOIDED` with no `voided_at` | ❌ 2,670 (org 27), 1,037 (org 28) — see §7.7 |
| Invoices dated in the future | ⚠️ 985 (org 27), 193 (org 28), 0 on orgs 24/25 |

**Tenant isolation holds in the data**, which matters given that the schema does not enforce it: despite
`income.contact_id`, `income_items.account_id` and `journal_entry_lines.account_id` having no foreign keys,
not one row crosses a tenant boundary in the orgs tested.

**But dangling `organization_id`s do exist**, because `chart_of_accounts.organization_id` and
`contacts.organization_id` have no FK either:

- `chart_of_accounts` references **10,119** distinct org ids, but only **10,028** organizations exist —
  **91 orphaned tenants**, including ids 1–13 which were deleted.
- `contacts` references 15 org ids that no longer exist.
- Notably, **organization 5 — the one in the `.env` JWT — has leftover `chart_of_accounts` and `contacts`
  rows but no row in `organizations`.** It is a deleted tenant, which is why queries against it return
  master data but no company.
- Every one of the 10,028 live organizations has a chart of accounts; 26 have no contacts at all.

---

## 8. Tenant coverage — the core problem

How many of the 10,028 organizations have data in each module? Exact `COUNT(DISTINCT organization_id)`:

```
                        orgs with data                                   % of 10,028
MASTER DATA
chart_of_accounts    10,028  ████████████████████████████████████████████  100.0%
sub_contacts         10,024  ████████████████████████████████████████████   99.96%
inventory_quantities 10,023  ████████████████████████████████████████████   99.95%
inventory_movements  10,022  ████████████████████████████████████████████   99.94%
branches             10,011  ████████████████████████████████████████████   99.83%
projects             10,011  ████████████████████████████████████████████   99.83%
cost_centers         10,011  ████████████████████████████████████████████   99.83%
tax_rates            10,006  ████████████████████████████████████████████   99.78%
contacts             10,002  ████████████████████████████████████████████   99.74%
items                10,002  ████████████████████████████████████████████   99.74%
────────────────────────────────────────────────────────────────────────────────────
TRANSACTIONS
journal_entries       4,162  ██████████████████                             41.50%
invoice_history       4,161  ██████████████████                             41.49%
income                4,150  ██████████████████                             41.38%
expense               4,150  ██████████████████                             41.38%
audit_trails            869  ███                                             8.67%
supplier_payments         1                                                  0.01%
bank_accounts             2                                                  0.02%
customer_payment          0                                                  0.00%
```

*Every figure is an exact `COUNT(DISTINCT organization_id)` — no estimates. Worth noting for anyone
re-running this: `pg_stats.n_distinct` was **low on every single table** it was checked against, by 1%
(`income`) to 10% (`inventory_movements`), and it understated `journal_entries` by 8.4%. Use it to
triage, never to report.*

**This is the shape of the problem, and it is a remarkably clean split.** Master data was seeded for
*essentially every tenant* — all 10,028 have a full 82-account chart of accounts, and ~99.8% have contacts,
items, tax rates, branches, projects and cost centres. So every organization looks alive.

But only **~41.5% have any transaction at all**, and the four transactional tables agree almost exactly:
4,162 orgs have journal entries, 4,161 have invoice history, and 4,150 have invoices and bills alike. This
is one coherent cohort of roughly 4,160 "active" tenants, not a cascade of shrinking subsets — a tenant
either got the full transactional seed or got none of it. (Membership still differs at the edges: org 24
has 9,587 invoices and zero bills.)

Pick an organization at random and there is a **58.6% chance that every financial question returns
"no data"** while every master-data question answers perfectly — the failure mode that makes this dataset
deceptive to work with.

### Transaction depth among the 4,150 orgs that *do* have income

Measured exactly across all 4,150 organizations:

```
invoices per org      orgs        share
      1 –    99          3   0.1%  ▏
    100 –   499          0   0.0%
    500 –   999        494  11.9%  ██████
  1,000 – 1,999      3,537  85.2%  ███████████████████████████████████████████
  2,000 – 2,999         95   2.3%  █
  3,000 – 4,999         17   0.4%  ▏
  5,000 +                4   0.1%  ▏
```

Median **1,353** invoices · mean 1,360 · min 1 · **max 12,911**.

The corpus is deliberately uniform: 85% of active tenants sit in a narrow 1,000–2,000 invoice band, and they
are largely interchangeable. What separates tenants is not volume but **which modules were seeded** — and on
that dimension they differ sharply.

Only **four organizations exceed 5,000 invoices — 27, 24, 25 and 28** — and they are the only ones with
ledgers deep enough for multi-year trend and comparison questions. The next tier (3,000–4,999) is
17 orgs led by 874, 924, 909, 891, 900 and 1053.

---

## 9. What Gemini Brain can and cannot answer from this data

Mapping the seven intent types and the documented API/SQL surface onto what the data actually supports.

| User question | Data path | Verdict |
| :--- | :--- | :--- |
| "Total revenue for 2026" | `income` → `income_items.line_amount` | ✅ Works |
| "Revenue by month / by year / vs last year" | `income.invoice_date` (2021–2026) | ✅ Works — 6 years of history |
| "Top 10 customers by revenue" | `income` → `contacts` (type 4) | ✅ Works |
| "Top vendors / suppliers" | `expense` → `contacts` (type **5**) | ✅ Works — but only with the correct type id |
| "Total expenses / spend by category" | `expense.expense_category_type_id` → `expense_items` | ✅ Works — 10 categories |
| "Profit & loss", "net margin" | `journal_entry_lines` + `chart_of_accounts.account_type` | ✅ Works |
| "Trial balance", "balance sheet", "account balances" | `journal_entry_lines` aggregation | ✅ Works — **never** `coa.balance` |
| "General ledger for account X" | `journal_entry_lines` → `chart_of_accounts` | ✅ Works |
| "List / search invoices, bills, contacts, items" | direct table reads | ✅ Works |
| "Invoice status summary" | `income.status_type_id` | ✅ Works (5 statuses in play) |
| "Revenue by branch / project / cost centre" | `income.branch_id`, `income.project_id` | ⚠️ Partial — `branch_id` NULL for 21%; `*_items.cost_center_id` is 100% NULL |
| "Unpaid / overdue invoices", "AR ageing", "who owes us" | needs `amount_paid` | ⚠️ Only via `status_type_id`; `amount_paid` is always 0 |
| "AP ageing", "when are bills due" | `expense.due_date` | ❌ 99.93% NULL |
| "VAT liability", "input vs output VAT", "tax collected" | `*_items.tax_amount` | ❌ zero for every tenant **except orgs 24 and 25** — elsewhere answers `0`, misleadingly |
| "Cash / bank balance", "cash flow position" | `bank_accounts`, `bank_transactions` | ❌ 5 junk rows / empty |
| "Uncategorized bank transactions", "bank rules", "reconciliation" | banking tables | ❌ Empty |
| "Customer payments received" | `customer_payment` | ❌ Empty (0 rows) |
| "Supplier payments made" | `supplier_payments` | ❌ Only org 25 has any |
| "Stock movement / consumption / turnover" | `inventory_movements` | ❌ 100% `RECEIPT` / `OPENING_BALANCE` |
| "Inventory on hand" | `inventory_quantities`, `items.quantity_on_hand` | ✅ Opening position only |
| "Who changed this invoice, and what changed?" | `audit_trails` | ❌ `old_values`/`new_values`/`changed_fields` 100% NULL |
| "Recent activity on invoices" | `invoice_history` | ⚠️ Only 2026-06-19 → 2026-08-06 |
| "Manual journal entries" | `journal_entries.source_type='MANUAL'` | ❌ Never occurs — only INCOME/EXPENSE |
| "Voided / reversed transactions" | `voided_at`, `is_reversal` | ❌ Status says VOIDED; all metadata NULL |
| FAQ / how-to / accounting concepts (types 1, 2, 6) | no data needed | ✅ Unaffected |

**Summary:** the sales ledger, purchase ledger, general ledger and master data are solid and will support
the majority of realistic finance questions. **Banking, payments, VAT/tax, inventory movement and audit
forensics are not answerable from this database for any tenant.**

---

## 10. Configuration mismatches found while analysing

Three things in the repo do not line up with the database as it stands:

1. **`.env` points at an organization that does not exist.** `ACCUTAX_AUTH_TOKEN` decodes to
   `{"userId": 18, "organization_id": 5, ...}`, but organization ids start at **14** — there is no org 5.
   User 18 (`testuser12@test.com`) actually owns organizations **154–163**, none of which is org 27 used
   throughout `README.md` and the API examples. So the shipped token, the shipped user id, and the
   documented example org are three mutually inconsistent things.

2. **`api_catalog.py` has the wrong vendor type id** (§6): it claims `1/2/3 = vendor` when the database
   only defines `4 = CUSTOMER` and `5 = VENDOR`.

3. **`sql_engine.TENANT_TABLES` omits the ledger tables.** `journal_entries` carries `organization_id` but
   is not in the set, and `income_items` / `expense_items` / `journal_entry_lines` have no
   `organization_id` at all — the regex-based tenant guard cannot protect them. Tenant scoping for those
   tables has to come from an explicit join to the parent.

Also worth noting operationally: the token in `.env` carries `exp: 1787225340` (2026-08-18), so it is
already expired — API-path calls will fail over to the SQL fallback engine.

---

## 11. Canonical SQL for this schema

Copy-paste starting points that respect every trap in §7. Replace `:org`.

**Revenue for a year (excluding voided/cancelled, capped at today)**

```sql
SELECT COALESCE(SUM(ii.line_amount), 0) AS revenue,
       COALESCE(SUM(ii.tax_amount), 0)  AS vat,
       COUNT(DISTINCT i.id)             AS invoices
FROM income i
JOIN income_items ii ON ii.income_id = i.id
WHERE i.organization_id = :org
  AND i.invoice_date >= '2026-01-01'
  AND i.invoice_date <  '2027-01-01'
  AND i.status_type_id NOT IN (3, 6);   -- exclude CANCELLED, VOIDED
```

**Monthly revenue trend**

```sql
SELECT LEFT(i.invoice_date, 7) AS month, SUM(ii.line_amount) AS revenue
FROM income i
JOIN income_items ii ON ii.income_id = i.id
WHERE i.organization_id = :org
  AND i.invoice_date >= '2026-01-01' AND i.invoice_date < '2027-01-01'
GROUP BY 1 ORDER BY 1;
```

**Top 10 customers by revenue**

```sql
SELECT c.name, c.organization_name, SUM(ii.line_amount) AS revenue, COUNT(DISTINCT i.id) AS invoices
FROM income i
JOIN income_items ii ON ii.income_id = i.id
JOIN contacts c      ON c.id = i.contact_id AND c.contact_type_id = 4   -- 4 = CUSTOMER
WHERE i.organization_id = :org
GROUP BY c.id, c.name, c.organization_name
ORDER BY revenue DESC
LIMIT 10;
```

**Top 10 vendors by spend** — identical shape but `contact_type_id = 5` and `expense`/`expense_items`.

**Expense by category**

```sql
SELECT ect.value AS category, SUM(ei.line_amount) AS spend, COUNT(DISTINCT e.id) AS bills
FROM expense e
JOIN expense_items ei          ON ei.expense_id = e.id
JOIN expense_category_type ect ON ect.id = e.expense_category_type_id
WHERE e.organization_id = :org
GROUP BY 1 ORDER BY spend DESC;
```

**Trial balance / account balances (the only correct way)**

```sql
SELECT a.account_code, a.account_name, a.account_type,
       SUM(l.debit_amount)  AS debits,
       SUM(l.credit_amount) AS credits,
       SUM(l.debit_amount) - SUM(l.credit_amount) AS balance
FROM journal_entries je
JOIN journal_entry_lines l ON l.journal_entry_id = je.id
JOIN chart_of_accounts a   ON a.id = l.account_id
WHERE je.organization_id = :org
  AND je.transaction_date BETWEEN DATE '2026-01-01' AND DATE '2026-12-31'
GROUP BY a.account_code, a.account_name, a.account_type
ORDER BY a.account_code;
```

**P&L summary**

```sql
SELECT a.account_type,
       SUM(l.credit_amount - l.debit_amount) AS net
FROM journal_entries je
JOIN journal_entry_lines l ON l.journal_entry_id = je.id
JOIN chart_of_accounts a   ON a.id = l.account_id
WHERE je.organization_id = :org
  AND a.account_type IN ('Revenue', 'Expense')
  AND je.transaction_date BETWEEN DATE '2026-01-01' AND DATE '2026-12-31'
GROUP BY a.account_type;
```

**Invoice status summary (the substitute for AR ageing)**

```sql
SELECT st.code AS status, COUNT(*) AS invoices, SUM(i.amount_due) AS value
FROM income i
JOIN status_type st ON st.id = i.status_type_id
WHERE i.organization_id = :org
GROUP BY st.code ORDER BY value DESC;
```

Three helper functions are already deployed in the database and are worth preferring where they fit:
`fn_gl_profitability(p_org, p_from, p_to)`, `fn_project_expense_rollup(...)`, `fn_inventory_movement(...)`.

---

## 12. The organizations worth using

Nine candidates were profiled end-to-end. Every figure below is measured per tenant. **No single
organization has everything** — the seeding process gave different orgs different modules — so the choice
depends on what the demo needs to show.

### Full comparison

| Metric | **27** | **28** | **154** | **25** | **874** | **924** | **24** | 909 | 1053 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Invoices | **12,911** | 5,973 | 1,482 | 9,451 | 4,438 | 3,975 | 9,587 | 3,760 | 3,678 |
| Invoice value (AED M) | **94.0** | 44.8 | 11.1 | 77.0 | 23.2 | 22.9 | 75.8 | 18.6 | 4.1 |
| Invoice lines | **26,297** | 11,892 | 3,033 | 19,038 | 8,797 | 7,459 | 19,229 | 6,789 | 7,303 |
| Bills | **6,206** | 4,754 | 1,234 | 4,476 | 3,767 | 3,912 | **0** | 1,523 | 1,466 |
| Bill value (AED M) | 17.8 | 13.1 | 3.5 | **33.8** | 9.6 | 10.4 | — | 4.2 | 3.8 |
| Expense categories | **10** | **10** | **10** | **1** | **10** | 10 | — | **10** | **10** |
| Journal entries | **18,802** | 10,579 | 2,538 | 12,892 | 5,668 | 6,074 | 8,587 | 3,518 | 1,439 |
| Journal lines | **54,175** | 29,107 | 7,145 | 44,269 | 15,020 | 16,191 | 33,308 | 9,773 | 3,713 |
| **Invoices posted to GL** | **98.7%** | **98.4%** | **96.7%** | 89.0% | 41.8% | 53.1% | 89.5% | 56.2% | **6.1%** |
| Bills posted to GL | 95.2% | **96.4%** | 87.1% | 60.1% | 94.7% | 93.3% | — | 87.5% | 77.8% |
| Years covered | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| Latest date | 2026-12-31 | 2026-12-31 | 2026-12-31 | 2026-06-22 | 2026-12-29 | 2026-12-23 | 2026-06-19 | 2026-12-31 | 2026-12-18 |
| 2026 invoices | **2,363** | 622 | 189 | 923 | 415 | 848 | 898 | 736 | 532 |
| Statuses in play | 5 | 5 | 5 | 4 | 5 | 5 | 4 | 5 | 5 |
| **Tax / VAT** | — | — | — | **70.9%** | — | — | **71.3%** | — | — |
| Tax collected (AED M) | 0 | 0 | 0 | **5.65** | 0 | 0 | **5.60** | 0 | 0 |
| Branch attribution | 0% | 0% | 0% | 0% | **70.4%** | **70.4%** | 0% | 50.0% | **100%** |
| **Audit trail rows** | 0 | 0 | **12,781** | 0 | **29,526** | **19,904** | **29,511** | 0 | 0 |
| Invoice history rows | **25,656** | 11,840 | 2,915 | 17,867 | 4,690 | 5,025 | 18,174 | 4,480 | 685 |

Master data is *uniform* across every candidate and is therefore not a differentiator:
**100 customers + 100 vendors, 82 GL accounts, 20 tax rates, 20 branches, 20 projects, 20 cost centres,
200 inventory quantities and 200 inventory movements each.**

### The five to use

**🥇 Organization 27 — `Professional & Consulting Services_User1_Org4`**
*The default. Best for anything financial.*

The deepest, most internally consistent tenant in the database: 12,911 invoices worth AED 94 M, 6,206 bills
spread across all 10 expense categories, 18,802 journal entries with 54,175 lines, and the **highest GL
linkage of any organization (98.7% of invoices, 95.2% of bills posted)**. Six full years, all five invoice
statuses, and by far the most 2026 activity (2,363 invoices). Revenue, expenses, P&L, trial balance, GL,
top customers, top vendors, trends and comparisons all work.
*Missing:* VAT (zero tax), audit trails, branch attribution.

**🥈 Organization 28 — `Construction & Real Estate_User1_Org5`**
*The clean second tenant.*

Structurally identical to 27 at roughly half the volume — 5,973 invoices (AED 44.8 M), 4,754 bills across
all 10 categories, 98.4% / 96.4% GL linkage, full 2021–2026 range. Because it behaves exactly like 27 on
different data, it is the natural partner for **cross-organization comparison and tenant-isolation testing**.
*Missing:* the same three things as 27.

**🥉 Organization 154 — `Healthcare & Pharmaceuticals_User12_Org1`**
*The most complete module coverage — and the one your `.env` user actually owns.*

The only candidate that is strong on **every** populated module at once: 1,482 invoices *and* 1,234 bills
across all 10 expense categories *and* 96.7% / 87.1% GL linkage *and* 12,781 audit-trail rows *and*
2,915 invoice-history rows. Nothing else combines a healthy ledger with an audit trail.

It is also owned by **user 18 (`testuser12@test.com`) — the `ACCUTAX_USER_ID` already configured in
`.env`** — so it is reachable through the authenticated API path without changing credentials.
*Trade-off:* volume is mid-range (AED 11.1 M, 189 invoices in 2026), so trend charts are thinner than org 27's.
*Missing:* VAT, branch attribution.

**4. Organization 25 — `Construction & Real Estate_User1_Org2`**
*The only tenant that can demonstrate tax.*

Orgs 24 and 25 are the **only two tenants in the database carrying any tax at all** (§5.4), and 25 is the
only one of the pair that also has a purchase ledger. 70.9% of its invoice lines are taxed (AED 5.65 M), its
bill value is the highest of any tenant (AED 33.8 M), and it is the **sole owner of all 1,786
`supplier_payments` rows in the database**.
*Weaknesses:* all 4,476 bills sit in a **single** expense category (`RENT`), so "spend by category" returns
one bar; bill GL linkage is only 60.1%; data stops at 2026-06-22.

**5. Organization 874 — `Education & Training_User84_Org1`**
*Audit trail plus branch-level reporting.*

29,526 audit-trail rows, 4,438 invoices and 3,767 bills across all 10 categories, and **70.4% branch
attribution** — one of the few orgs where "revenue by branch" returns anything. Org **924** is an almost
identical alternative (19,904 audit rows, 3,912 bills, 70.4% branch).
*Weakness:* only **41.8%** of invoices reach the GL, so ledger-derived P&L and balance sheets will be
materially understated. Use 27/28/154 for accounting-accurate answers.

*Runner-up:* **Organization 24** — 9,587 invoices (AED 75.8 M), 71.3% taxed, 29,511 audit rows, but
**zero bills**, so no profit, margin or cost analysis is possible. Revenue- and VAT-only.

### Choosing quickly

| If the demo is about… | Use |
| :--- | :--- |
| Revenue, expenses, P&L, GL, trial balance, trends, customers, vendors | **27** (deepest) or **154** (broadest) |
| Two tenants side by side / tenant-isolation testing | **27 + 28** |
| Everything at once, through the configured `.env` user | **154** |
| VAT, tax collected, supplier payments | **25** |
| Audit trail, branch-level reporting | **874** or **924** |
| Revenue-only or VAT-only at high volume | **24** |
| Cash, bank, reconciliation, AR/AP ageing, stock movement | *no organization works* — see §9 |

### Organizations to avoid

- **Organization 5** — the org in the `.env` JWT. It **does not exist** in `organizations` (ids start at 14);
  only orphaned `chart_of_accounts` and `contacts` rows remain from a deleted tenant.
- **Organization 1053** — only **6.1%** of its invoices reach the general ledger; ledger answers will be wrong.
- **5,878 organizations (58.6%)** have no invoices at all despite having a full chart of accounts, 100
  customers, 100 vendors and 100+ items. They answer master-data questions and fail every financial one —
  which is exactly what makes picking a tenant at random so misleading.

## 13. How this analysis was produced

Everything above is measured, not inferred from the schema. The techniques are worth reusing.

| Question | Technique |
| :--- | :--- |
| Which columns are empty? | `pg_stats.null_frac` — ANALYZE has already computed this; zero I/O cost |
| Which columns are constant? | `pg_stats.n_distinct = 1`, confirmed with `most_common_vals` / `most_common_freqs` |
| Date ranges | `pg_stats.histogram_bounds` — min/median/max without touching the table |
| Tenants per table | `pg_stats.n_distinct` on `organization_id` for an estimate; exact `GROUP BY` where it mattered |
| Per-tenant depth | indexed `WHERE organization_id = N` probes |

**A performance note that matters here.** A plain `SELECT organization_id, count(*) FROM income GROUP BY 1`
takes **555 s** (parallel seq scan over 4.8 GB). The same query with `SET enable_seqscan = off`, using
`idx_income_organization_id` as an index-only scan, takes **6 s** — a 92× speedup. The tables are far larger
than their live data warrants (`income` is 4.8 GB for 5.6 M rows ≈ 860 bytes/row) and carry duplicate
`_ccnew` indexes, so index-only paths are dramatically cheaper than heap scans.

The same trick backfires on `expense`, whose only `organization_id` index also carries a `VARCHAR` date;
there the index-only scan degrades into random heap fetches and is *slower* than the sequential scan.

---

## 14. Recommendations

**For running demos and evaluations**

1. Use the organizations in §12 — not org 5 (does not exist) and not a random tenant.
2. Constrain demo questions to the ✅ rows of §9: revenue, expenses, P&L, GL, customers, vendors, invoices,
   items. Avoid VAT, cash/bank, payments, ageing and audit-forensics questions — they are not answerable.
3. Cap date ranges at the current date, or expect future-dated invoices to inflate "this year" figures.

**Fixes worth making in the repo**

- Correct the vendor `contact_type_id` to `5` in `config/api_catalog.py`.
- Refresh `ACCUTAX_AUTH_TOKEN` (expired) and align `ACCUTAX_USER_ID` / example org so the user actually owns
  the organization being queried.
- Add `journal_entries` to `sql_engine.TENANT_TABLES`, and handle the three line-item tables explicitly.

**Fixes worth making in the database**

- Add `CREATE INDEX ON contacts (organization_id)` and `ON chart_of_accounts (organization_id)` — both are
  queried per tenant on every request and both currently sequential-scan.
- Drop the duplicated `_ccnew` indexes on `income`, `journal_entries` and `journal_entry_lines`.
- `VACUUM ANALYZE` the transactional tables; the bloat is what makes index-only scans fall back to heap reads.
- If VAT behaviour is ever to be demonstrated, `income_items.tax_amount` / `expense_items.tax_rate_id` need
  to be seeded, and `tax_rates` re-seeded with UAE FTA rates instead of Indian CBIC/GST brackets.
