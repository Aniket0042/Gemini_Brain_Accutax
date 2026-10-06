-- =============================================================================
-- cube_reader: the read-only database role Cube connects as.
-- For the DBA. Run as a superuser or CREATEROLE role on accutax_bk_1_5.
-- See docs/CUBE_CORE_INTEGRATION_GUIDE_V2.md section 6.
--
-- Checked on 2026-10-06 (read-only): row-level security is off on these tables,
-- so no RLS policy is needed; idx_journal_entries_org_id (organization_id, id)
-- already exists, so no new index is needed for Phase 1.
-- =============================================================================

-- Generate the password; store it only in semantic/cube.env on the VM.
CREATE ROLE cube_reader LOGIN PASSWORD '<generated>' NOINHERIT CONNECTION LIMIT 20;

ALTER ROLE cube_reader SET default_transaction_read_only = on;
ALTER ROLE cube_reader SET statement_timeout = '20s';
ALTER ROLE cube_reader SET idle_in_transaction_session_timeout = '30s';
-- Required. Cube compares time dimensions as (column::timestamptz AT TIME ZONE 'UTC').
-- The server default is Asia/Kolkata, which would turn the DATE 2026-01-01 into
-- 2025-12-31 18:30 UTC and put New Year's Day entries into December.
ALTER ROLE cube_reader SET timezone = 'UTC';

GRANT CONNECT ON DATABASE accutax_bk_1_5 TO cube_reader;
GRANT USAGE ON SCHEMA public TO cube_reader;
GRANT SELECT ON public.journal_entries, public.journal_entry_lines, public.chart_of_accounts,
                public.income, public.income_items, public.expense, public.expense_items,
                public.status_type, public.contacts, public.organizations, public.projects,
                public.cost_centers, public.expense_category_type
      TO cube_reader;

-- Verify, connected as cube_reader:
--   SHOW timezone;                              -- UTC
--   SHOW default_transaction_read_only;         -- on
--   SELECT count(*) FROM journal_entries WHERE organization_id = 24;   -- works
--   SELECT count(*) FROM bank_accounts;         -- permission denied (not granted)
--   CREATE TABLE t (i int);                     -- fails: read-only transaction
--
-- Rollback:
--   REVOKE ALL ON ALL TABLES IN SCHEMA public FROM cube_reader;
--   REVOKE USAGE ON SCHEMA public FROM cube_reader;
--   REVOKE CONNECT ON DATABASE accutax_bk_1_5 FROM cube_reader;
--   DROP ROLE cube_reader;
