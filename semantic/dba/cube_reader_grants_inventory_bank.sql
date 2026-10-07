-- =============================================================================
-- cube_reader: read access for the inventory, bank and branch views.
-- For the DBA. Run as a superuser or the table owner on accutax_bk_1_5,
-- after semantic/dba/cube_reader.sql. Read-only grants; no table is changed.
--
-- Used by the views inventory, bank_accounts, bank_transactions and the
-- branch_name member of sales and purchases. Run on 2026-10-08.
-- =============================================================================

GRANT SELECT ON public.items, public.inventory_quantities, public.warehouses,
                public.bank_accounts, public.bank_transactions, public.branches
      TO cube_reader;

-- Verify, connected as cube_reader:
--   SELECT count(*) FROM inventory_quantities WHERE organization_id = 24;   -- works
--   SELECT count(*) FROM bank_transactions WHERE organization_id = 24;      -- works
--   SELECT count(*) FROM branches WHERE organization_id = 24;               -- works
--
-- Rollback:
--   REVOKE SELECT ON public.items, public.inventory_quantities, public.warehouses,
--                    public.bank_accounts, public.bank_transactions, public.branches
--          FROM cube_reader;
