# Pending Cube model: inventory, bank and branches

These files are ready, but Cube does not load them yet. The `cube_reader` role cannot
read the tables they use. If they were in `model/`, every query on them would fail.

| File | View | Tables it reads |
|---|---|---|
| `cubes/inventory_stock.yml`, `views/inventory.yml` | `inventory` (today) | items, inventory_quantities, warehouses |
| `cubes/bank_lines.yml`, `views/bank.yml` | `bank_accounts` (today), `bank_transactions` (period) | bank_accounts, bank_transactions |
| `cubes/branches.yml` | `branch_name` in `sales` and `purchases` | branches |

The rules match the Accutax code:

- Low stock means quantity available is below the reorder level. This is the same rule
  as `InventoryQuantityService.getLowStockItems`.
- Bank balance is the opening balance plus credits minus debits. This is the same rule
  as `BankService.getAccounts`.
- Stock value uses average cost. Accutax's own valuation report uses FIFO layers, so
  the two can differ. The view description says this.

The Accutax REST endpoints are not used for this data. They take the organization from
the user's token before the `organization_id` parameter, so in a multi-organization
chat they could return one organization's data under another organization's name.

## Enable

1. Ask the DBA to run `semantic/dba/cube_reader_grants_inventory_bank.sql`.
2. Move the files into the model:
   `git mv semantic/model_pending/cubes/* semantic/model/cubes/`, and the same for
   `semantic/model_pending/views/*` into `semantic/model/views/`.
3. Add `'inventory', 'bank_accounts', 'bank_transactions'` to `QUERYABLE_VIEWS` in
   `semantic/cube.js`.
4. For branch names:
   - In `model/cubes/sales_lines.yml` and `model/cubes/purchase_lines.yml`, uncomment
     the `branches` join.
   - Add this to `model/views/sales.yml`:
     ```yaml
     - join_path: sales_lines.branches
       includes: [branch_name]
     ```
   - Add the same to `model/views/purchases.yml`, with `purchase_lines.branches`.
5. Run `pytest tests/unit/test_cube_model.py`.
6. Deploy with `semantic/deploy/install_rootless.sh --start`.
7. Run these checks:
   - `inventory`: compare `low_stock_count` with the Accutax low-stock screen for one
     organization.
   - `bank_accounts`: compare `current_balance` with the bank account list.
   - Stock quantities: check that item-level rows (no warehouse) and warehouse rows
     are not counted twice for the same item.

The agent picks up the new views from Cube's `/v1/meta` on its own. No backend change
is needed.
