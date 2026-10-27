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
| Retention | `retention_*` on the header; `retention_releases` table | **Open balances are wrong.** Held retention is not due, and EC2 already takes it out of `income.amount_due`. Example: invoice 1136 total 5,000, retention 250, `amount_due` 4,750. Cube shows 5,000. |
| Credit note applied to an invoice | `credit_note_applications` table | **Open balances may be wrong.** The application lowers the invoice's amount due with no journal entry. EC2 has 0 rows today. |
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

### 3.3 `open_receivables.yml` (and the same in `open_payables.yml`)

Replace the `outstanding` line:

```yaml
             {% if schema_v2() %}
             COALESCE(inc.amount_due, t.total_incl_vat - COALESCE(inc.amount_paid, 0)) AS outstanding,
             {% else %}
             t.total_incl_vat - COALESCE(inc.amount_paid, 0) AS outstanding,
             {% endif %}
```

On v2 also add a held-retention column and measure:

```yaml
             {% if schema_v2() %}
             GREATEST(COALESCE(inc.retention_amount, 0) - COALESCE(rr.released, 0), 0) AS retention_held,
             {% else %}
             0 AS retention_held,
             {% endif %}
```

with `LEFT JOIN LATERAL (SELECT SUM(r.amount) AS released FROM public.retention_releases r WHERE r.document_type = 'INCOME' AND r.document_id = inc.id) rr ON true` inside the v2 branch.
Payables use `e.`, `expense` and `document_type = 'EXPENSE'`.

To check before building: whether EC2 also lowers `amount_due` for credit note applications and payments in every
path (invoice 1107 has `amount_due` 0 and `amount_paid` 0, so it looks maintained). If not, subtract
`credit_note_applications` in the v2 branch instead.

### 3.4 `payment_lines.yml`

Customer side, v2:

```yaml
             {% if schema_v2() %}
             COALESCE(cp.base_amount, cp.amount) - COALESCE(cp.amount_refunded, 0) * COALESCE(cp.exchange_rate, 1) AS received,
             {% else %}
             cp.amount - COALESCE(cp.amount_refunded, 0) AS received,
             {% endif %}
```

Supplier side, v2: `REFUND` rows go to `received`, other rows to `paid`, both from `COALESCE(sp.base_amount, sp.amount)`.
`payment_type` shows `SUPPLIER_REFUND` for refunds. The `payment_applications` cube uses `amount_applied`, which stays
in base currency; no change.

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
`retention_releases` and `credit_note_applications` for v2. `accutax_user` cannot create roles, so this needs the
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
3. Write the v2 branches (3.3, 3.4); unit-test the compiled SQL for both values.
4. Run a second Cube on the VM against EC2 with `ACCUTAX_SCHEMA=v2`; compare open balances and payments with the
   zereco.ai dashboard for invoices 1106-1136.
5. Optional dimensions (3.5).
