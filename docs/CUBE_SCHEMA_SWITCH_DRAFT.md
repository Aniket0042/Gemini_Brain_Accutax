# One Cube model for two Accutax schemas (draft)

Status: draft for review, 27 Oct 2026. Nothing is built or deployed.

## 1. Goal

Run the same Cube model files on two databases:

| Environment | Database | Backend version | Setting |
|---|---|---|---|
| VM (106.51.80.81) | `accutax_bk_1_5` | gitaticara/accutax-backend-testing 1e33cd4 (older, 70 migrations) | `ACCUTAX_SCHEMA=v1` |
| EC2 (zereco.ai) | `accutax_db` | Calamity010/accutax_bk_backend `origin/main` (139 migrations) | `ACCUTAX_SCHEMA=v2` |

There is one file per cube, as today. No file is copied. The few places that differ have an
`{% if schema_v2() %} ... {% else %} ... {% endif %}` block inside the same file. The only per-environment
difference is one line in `cube.env`.

When the VM backend is upgraded to the EC2 version, set `v2` on the VM too, then delete the `v1` branches.

## 2. What the newer EC2 schema means for Cube

Read from the EC2 backend migrations and entities, and checked against EC2 rows (read-only, 27 Oct).

| EC2 feature | Where it lives | Effect on the current Cube model |
|---|---|---|
| Foreign currency | `income/expense.currency_code`, `exchange_rate`; `*_items.foreign_line_amount` | **None.** `line_amount` and `tax_amount` hold base-currency (AED) amounts. Example: invoice 1132 is USD 1,000 at 3.6725; `line_amount` = 3,672.50. |
| Discount on total | `discount_on_total_*` on the header; `gross_line_amount` on lines | **None.** The discount is already spread into `line_amount`; `gross_line_amount` keeps the figure before it. Example: invoice 1108 gross 290, discount 50, `line_amount` 240. |
| Tax-inclusive entry | `income/expense.amounts_are` | **None expected.** It records how the user typed amounts; stored lines are net. To confirm on one inclusive invoice when one exists (EC2 has only one inclusive quote). |
| Retention | `retention_*` on the header; `retention_releases` table | **Open balances are wrong.** Held retention is not due until released. Example: invoice 1136 total 5,000, retention 250, `amount_due` 4,750. Cube shows 5,000. |
| Credit note applied to an invoice | `credit_note_applications` table | **No change needed.** EC2 adds applications into `income.amount_paid`, which Cube already subtracts. EC2 has 0 rows today. |
| Foreign-currency payment | `customer_payment/supplier_payments.base_amount`, `exchange_rate` | **Payments wrong for foreign payments.** `amount` is in the payment currency; `base_amount` is what the bank moved in AED. EC2 has 0 such payments today. |
| Supplier refund | `supplier_payments.payment_type` = `REFUND` | **Payments wrong for refunds.** A refund is money coming in, but Cube counts it as paid out. EC2 has 0 refunds today. |

Document types are the same on both databases (INVOICE, CASH_INVOICE, CREDIT_NOTE, QUOTE, PROFORMA, DELIVERY_NOTE;
BILL, EXPENSE, CASH_EXPENSE, VENDOR_CREDIT, PURCHASE_ORDER).

## 3. Changes per file

### 3.1 New: `semantic/model/globals.py`

```python
import os

from cube import TemplateContext

template = TemplateContext()


@template.function("schema_v2")
def schema_v2() -> bool:
    """True on databases migrated to the Calamity010 backend (EC2). Set ACCUTAX_SCHEMA in cube.env."""
    return os.getenv("ACCUTAX_SCHEMA", "v1").strip().lower() == "v2"
```

### 3.2 `cube.env` / `cube.env.example`

```
# v1 = VM accutax_bk_1_5 (older backend); v2 = EC2 accutax_db (Calamity010 main)
ACCUTAX_SCHEMA=v1
```

The Quadlet unit and docker-compose already pass `cube.env` to the container, so no unit change is needed.

### 3.3 `open_receivables.yml` and `open_payables.yml` (built 27 Oct)

How the EC2 backend keeps these figures (Calamity010 `income.service.ts`, `expense.service.ts`):

- `amount_paid` is recomputed on every payment: payment items applied, plus credit notes applied to the invoice
  (`credit_note_applications`). So credit note applications need no extra handling in Cube.
- `amount_due` is **not** lowered by payments. It is only the total less held retention, set when the invoice is
  saved, so Cube must not use it.
- Held retention (invoices and bills only) = `retention_amount` less `retention_releases`, never below 0. It is not due
  until released.
- Partly paid bills get the status `PARTIAL_PAID`, not `PARTIALLY_PAID`. The current filter misses them.

v2 changes, each inside `{% if schema_v2() %}` with today's line as the `{% else %}`:

- `outstanding` = lines total incl. VAT − held retention − `amount_paid`;
- a `CROSS JOIN LATERAL` over `retention_releases` (matched on document, type and organization) giving `held`;
- the status filter also accepts `PARTIAL_PAID`.

### 3.4 `payment_lines.yml` (built 27 Oct)

- Customer payments: `received` = `COALESCE(base_amount, amount)` − `amount_refunded`. `base_amount` is set only on
  payments of foreign-currency invoices (what the bank moved in the base currency). Refund payments are not part of
  that plan, so `amount_refunded` is already in the base currency.
- Supplier payments: `REFUND` rows count as received, other rows as paid, both at `COALESCE(base_amount, amount)`;
  `payment_type` shows `SUPPLIER_REFUND` for refunds. The member description still says `SUPPLIER_PAYMENT` only, to
  keep `/meta` identical on the VM.
- `payment_applications` uses `amount_applied`, which is already in the base currency: no change.

### 3.4a Test record (27 Oct, throwaway containers on the VM, removed afterwards; live Cube untouched)

- v1 against the live VM Cube: setting absent and `v1` both gave 0 differences (243 queries + `/meta`).
- v2 (EC2 DB) against the live VM Cube: `/meta` identical; SQL differs only in `receivables`, `payables` and
  `payments` (16 queries each), as intended.
- Figures on EC2, v1 vs v2 vs the EC2 backend's own formula run directly in SQL:

  | | v1 | v2 | direct SQL |
  |---|---|---|---|
  | Org 1 receivables | 89,108.05 (20) | 88,858.05 (20) | 88,858.05 (20) |
  | Invoice 1136 (retention 250) | 5,000.00 | 4,750.00 | 4,750 (`amount_due`) |
  | Org 2 receivables | 26,847.50 (4) | 26,847.50 (4) | 26,847.50 (4) |
  | Org 1 payables | 189.00 (1) | 189.00 (1) | 189.00 (1) |
  | Org 1 received / paid | 2,857,203.21 / 2,272,943.90 | same | same |
  | Org 2 received / paid | 3,695,208.32 / 577,095.75 | same | same |

  Payments match on both because EC2 has no foreign-currency payments or supplier refunds yet; those branches are
  checked only by reading the SQL until such rows exist.

### 3.5 Optional on v2: new dimensions

On `sales_lines` and `purchase_lines`, so the agent can answer "how much did we invoice in USD":

- `currency_code` (`COALESCE(inc.currency_code, org base currency)`) and `foreign_line_amount` as a measure.
- `discount_on_total` and `retention_amount` as header measures.

On v1 the same members return the base currency and 0, so views and agent prompts stay the same on both.

### 3.6 No change

`sales_lines`, `purchase_lines`, `vat_lines`, `gl_lines`, `bank_lines`, `inventory_stock`, `branches`,
`organizations`, `vat_config_rows`, all views, `cube.js`.

## 4. Database grants on EC2

`cube_reader` must exist on `accutax_db` with `SELECT` on the tables in `semantic/dba/*.sql`, plus
`retention_releases` for v2. `accutax_user` cannot create roles, so this needs the
database superuser.

## 5. Risks

- **Jinja and Python in our image: proven 27 Oct.** Throwaway containers of `cubejs/cube:v1.7.50` on the VM (ports
  4097-4099, removed afterwards; the live Cube on 4000 was not touched) loaded a test cube using the `globals.py` above:
  - `v1` on `accutax_bk_1_5`: v1 SQL compiled and ran (13,868,671 income rows, so a full-table test query is slow there).
  - `v2` on `accutax_db`: v2 SQL ran and read `retention_amount` (total 250, invoice 1136).
  - `v2` on `accutax_bk_1_5`: failed with `column inc.retention_amount does not exist`, as expected. A wrong setting
    fails loudly instead of returning wrong figures.
  Notes for the real deploy: mount the model with `:z` (shared SELinux label) if two containers share it; a container
  without Cube Store needs `CUBEJS_CACHE_AND_QUEUE_DRIVER=memory`.
- **Curly braces.** Our cubes already avoid `{...}` in SQL literals. Jinja uses `{% %}` and `{{ }}`, which Cube treats
  as template syntax, not member references. Keep literals free of braces as before.
- **The VM cannot test v2.** VM data has no foreign-currency, retention or discount documents. Test v2 against EC2
  (read-only, through Cube on a spare port) with invoices 1106-1136.
- **Pre-aggregations.** None are used today. If added later, the switch value must be part of the rollup key.

## 6. Order of work

1. Prove Jinja + `globals.py` on the VM in a throwaway Cube container (no live change).
2. Add `globals.py` and `ACCUTAX_SCHEMA=v1`; deploy to the VM; run the golden set. Scores must not change.
   Built 27 Oct (not committed, not deployed). `semantic/tests/compare_compiled.py` compares two running Cubes:
   `/meta` plus the SQL for every view member (243 queries over 15 views). Results on the VM, against the live Cube:
   - live vs live: 0 differences (the script itself is sound);
   - live vs new model, setting absent: 0 differences; with `ACCUTAX_SCHEMA=v1`: 0 differences;
   - negative control (`line_amount * 1` in `sales_lines`): 35 differences, exit 1. The test catches a one-word change.
   Run the same comparison before every later model deploy.
3. Write the v2 branches (3.3, 3.4); unit-test the compiled SQL for both values. Built and tested 27 Oct (3.4a).
4. Run a second Cube on the VM against EC2 with `ACCUTAX_SCHEMA=v2`; compare open balances and payments with the
   zereco.ai dashboard for invoices 1106-1136.
5. Optional dimensions (3.5).
