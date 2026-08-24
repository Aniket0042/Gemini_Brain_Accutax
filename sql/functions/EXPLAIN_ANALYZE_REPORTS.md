# Phase 4 — SQL Function EXPLAIN ANALYZE & Index Optimization Reports

## 1. `fn_project_expense_rollup(p_org, p_from, p_to)`
- **Query Complexity**: 4-way join across `projects`, `expense`, `contacts`, and `bank_accounts`.
- **Target Execution**: < 30ms on 100k+ expense rows.
- **Recommended Indexes**:
  - `CREATE INDEX IF NOT EXISTS idx_expense_org_project_date ON expense(organization_id, project_id, expense_date);`
  - `CREATE INDEX IF NOT EXISTS idx_projects_org ON projects(organization_id);`
  - `CREATE INDEX IF NOT EXISTS idx_contacts_id ON contacts(id);`
  - `CREATE INDEX IF NOT EXISTS idx_bank_accounts_id ON bank_accounts(id);`

---

## 2. `fn_inventory_movement(p_org, p_from, p_to)`
- **Query Complexity**: Multi-CTE aggregation over `items`, `warehouses`, `income_items`, and `delivery_notes_items`.
- **Target Execution**: < 40ms on 50k+ items and delivery note rows.
- **Recommended Indexes**:
  - `CREATE INDEX IF NOT EXISTS idx_items_org_warehouse ON items(organization_id, warehouse_id);`
  - `CREATE INDEX IF NOT EXISTS idx_income_org_date ON income(organization_id, invoice_date);`
  - `CREATE INDEX IF NOT EXISTS idx_income_items_item_id ON income_items(item_id, income_id);`
  - `CREATE INDEX IF NOT EXISTS idx_delivery_notes_org_date ON delivery_notes(organization_id, date);`
  - `CREATE INDEX IF NOT EXISTS idx_delivery_notes_items_item_id ON delivery_notes_items(item_id, delivery_notes_id);`

---

## 3. `fn_gl_profitability(p_org, p_from, p_to)`
- **Query Complexity**: Full outer join of income and expense grouped by `chart_of_accounts.account_type`.
- **Target Execution**: < 50ms.
- **Recommended Indexes**:
  - `CREATE INDEX IF NOT EXISTS idx_coa_org_type ON chart_of_accounts(organization_id, account_type);`
  - `CREATE INDEX IF NOT EXISTS idx_income_items_coa ON income_items(chart_of_account_id);`
  - `CREATE INDEX IF NOT EXISTS idx_expense_coa ON expense(chart_of_account_id);`
