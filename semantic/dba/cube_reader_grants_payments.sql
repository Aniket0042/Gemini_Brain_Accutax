-- =============================================================================
-- cube_reader: read access for the payments and payment_settlements views.
-- For the DBA. Run as a superuser or the table owner on accutax_bk_1_5,
-- after semantic/dba/cube_reader.sql. Read-only grants; no table is changed.
--
-- Used by the views payments and payment_settlements. Run on 2026-10-09.
-- =============================================================================

GRANT SELECT ON public.customer_payment, public.customer_payment_items,
                public.supplier_payments, public.supplier_payment_items
      TO cube_reader;

-- Verify (expect 4 rows):
--   SELECT table_name FROM information_schema.role_table_grants
--   WHERE grantee = 'cube_reader'
--     AND table_name IN ('customer_payment', 'customer_payment_items',
--                        'supplier_payments', 'supplier_payment_items');
--
-- Rollback:
--   REVOKE SELECT ON public.customer_payment, public.customer_payment_items,
--                    public.supplier_payments, public.supplier_payment_items
--          FROM cube_reader;
