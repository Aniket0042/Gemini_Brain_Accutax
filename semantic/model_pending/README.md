# Pending Cube model: payments

These files are ready, but Cube does not load them yet. The `cube_reader` role cannot
read the payment tables. If the files were in `model/`, every query on them would fail.

| File | View | Tables it reads |
|---|---|---|
| `cubes/payment_lines.yml`, `views/payments.yml` | `payments`: money received and paid, per payment | customer_payment, supplier_payments, contacts |
| same files | `payment_settlements`: which invoice or bill each payment settled, days to pay, paid on time | customer_payment_items, supplier_payment_items, income, expense, contacts |

The rules follow the Accutax entities:

- Cancelled payments are left out.
- A customer payment's refunded amount is taken off what was received.
- Supplier payment dates are stored as text. Values that are not dates drop out of
  period queries, as invoice dates do in `sales`.

## Enable

1. Ask the DBA to run `semantic/dba/cube_reader_grants_payments.sql`. The verify query
   must list 4 tables.
2. Move the files into the model:
   `git mv semantic/model_pending/cubes/* semantic/model/cubes/`, and the same for
   `semantic/model_pending/views/*` into `semantic/model/views/`.
3. Add `'payments', 'payment_settlements'` to `QUERYABLE_VIEWS` in `semantic/cube.js`.
4. Run `pytest tests/unit/test_cube_model.py`.
5. Deploy with `semantic/deploy/install_rootless.sh --start`.
6. Run these checks for one organization:
   - `payments.amount_received` for a month matches the Customer Payments screen.
   - `payments.amount_paid` matches the Supplier Payments screen.
   - The settlements for one customer payment add up to the payment's amount, unless part
     of it was an advance.

The agent picks up the new views from Cube's `/v1/meta` on its own.
