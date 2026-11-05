-- =============================================================================
-- cube_reader on EC2: the read-only database role Cube connects as.
-- For the DBA. Run as a superuser or CREATEROLE role on accutax_db (13.127.157.108).
-- accutax_user cannot run this: it has no CREATEROLE.
--
-- Same role as cube_reader.sql + cube_reader_grants_*.sql on the VM, in one file,
-- plus retention_releases, which the v2 open balances read (ACCUTAX_SCHEMA=v2).
-- All 26 tables exist on accutax_db (checked 27 Oct 2026). The role reads only;
-- it changes nothing in the database.
-- =============================================================================

-- Generate the password (for example: openssl rand -base64 24) and replace <generated>.
-- Send it only to the person setting up Cube; it goes only into
-- /opt/accutax-ai/semantic/cube.env on the EC2 server.
CREATE ROLE cube_reader LOGIN PASSWORD '<generated>' NOINHERIT CONNECTION LIMIT 20;

ALTER ROLE cube_reader SET default_transaction_read_only = on;
ALTER ROLE cube_reader SET statement_timeout = '20s';
ALTER ROLE cube_reader SET idle_in_transaction_session_timeout = '30s';
-- Required. Cube compares time dimensions as (column::timestamptz AT TIME ZONE 'UTC');
-- any other session time zone moves dates near midnight into the wrong day or month.
ALTER ROLE cube_reader SET timezone = 'UTC';

GRANT CONNECT ON DATABASE accutax_db TO cube_reader;
GRANT USAGE ON SCHEMA public TO cube_reader;

GRANT SELECT ON
      -- ledger and documents
      public.journal_entries, public.journal_entry_lines, public.chart_of_accounts,
      public.income, public.income_items, public.expense, public.expense_items,
      public.status_type, public.contacts, public.organizations, public.projects,
      public.cost_centers, public.expense_category_type,
      -- inventory and bank
      public.items, public.inventory_quantities, public.warehouses,
      public.bank_accounts, public.bank_transactions, public.branches,
      -- payments
      public.customer_payment, public.customer_payment_items,
      public.supplier_payments, public.supplier_payment_items,
      -- VAT
      public.tax_rates, public.vat_configs,
      -- v2 only: retention still held on invoices and bills
      public.retention_releases
   TO cube_reader;

-- If pg_hba.conf lists users explicitly, add a line letting cube_reader connect from
-- the EC2 server (13.205.100.89) to accutax_db, then reload Postgres.

-- Verify, connected as cube_reader:
--   SHOW timezone;                                  -- UTC
--   SHOW default_transaction_read_only;             -- on
--   SELECT count(*) FROM income WHERE organization_id = 1;            -- works
--   SELECT count(*) FROM retention_releases;                          -- works
--   SELECT count(*) FROM users;                     -- permission denied (not granted)
--   CREATE TABLE t (i int);                         -- fails: read-only transaction
--
-- Rollback:
--   REVOKE ALL ON ALL TABLES IN SCHEMA public FROM cube_reader;
--   REVOKE USAGE ON SCHEMA public FROM cube_reader;
--   REVOKE CONNECT ON DATABASE accutax_db FROM cube_reader;
--   DROP ROLE cube_reader;
