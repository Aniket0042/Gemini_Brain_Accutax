-- =============================================================================
-- Migration: 005_rls_reader_role.sql
-- Purpose:   Constrain Gemini Brain to one tenant at the database level, without
--            changing behaviour for the Accutax application.
--
-- Supersedes 004_rls_hardening.sql, which could not work as written:
--
--   1. It created `ai_reader` as NOLOGIN, so nothing could ever connect as it.
--   2. Nothing switched DB_USER over, so the app kept connecting as the table
--      OWNER -- and owners bypass RLS. The policies would have been inert.
--   3. Forcing RLS to get around (2) would have applied to the owner too, and
--      the Accutax application does not set app.current_org. Every query it
--      makes would return zero rows. That takes the product down.
--   4. It listed 14 tenant tables. This database has 85 with organization_id.
--
-- The approach here: a separate least-privilege LOGIN role that does NOT own
-- the tables. The owner keeps bypassing RLS, so the application is untouched.
-- Gemini Brain connects as the new role and is constrained by policy.
--
-- ORDER OF OPERATIONS -- part A needs superuser, part B does not.
-- =============================================================================


-- =============================================================================
-- PART A -- requires a SUPERUSER (or a role with CREATEROLE).
--            `accutax_user` cannot run this; a DBA must.
-- =============================================================================

-- A1. The reader role. LOGIN, unlike 004's NOLOGIN, because Gemini Brain
--     authenticates as it. Replace the password before running.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_reader') THEN
        CREATE ROLE ai_reader LOGIN PASSWORD 'CHANGE_ME_BEFORE_RUNNING';
    END IF;
END
$$;

-- A2. Read-only, and explicitly nothing else. NOINHERIT keeps it from picking
--     up privileges via role membership later.
ALTER ROLE ai_reader NOINHERIT;
ALTER ROLE ai_reader SET statement_timeout = '20s';
ALTER ROLE ai_reader SET default_transaction_read_only = on;

GRANT CONNECT ON DATABASE accutax_db TO ai_reader;
GRANT USAGE ON SCHEMA public TO ai_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ai_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ai_reader;

-- Revoke the write paths a default PUBLIC grant might otherwise leave open.
REVOKE CREATE ON SCHEMA public FROM ai_reader;


-- =============================================================================
-- PART B -- runs as the table owner (`accutax_user`). Safe to apply on its own.
--
-- Enables RLS WITHOUT FORCE. The owner continues to bypass it, so the Accutax
-- application sees no change whatsoever. The policies only take effect for
-- non-owner roles -- i.e. ai_reader, once Part A has run.
-- =============================================================================

-- B1. Direct tenant tables: every table carrying an organization_id column.
--     Discovered dynamically rather than hardcoded, so a new table added later
--     is picked up by re-running this migration.
DO $$
DECLARE
    tbl  text;
    kind "char";
BEGIN
    FOR tbl, kind IN
        SELECT c.relname, c.relkind
        FROM   pg_class c
        JOIN   pg_namespace n ON n.oid = c.relnamespace
        JOIN   pg_attribute a ON a.attrelid = c.oid
        WHERE  n.nspname = 'public'
          AND  c.relkind IN ('r', 'p')          -- ordinary and partitioned
          AND  a.attname = 'organization_id'
          AND  a.attnum > 0
          AND  NOT a.attisdropped
          -- Partitions inherit the parent's policy; skip them individually.
          AND  NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
        ORDER BY c.relname
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', tbl);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON public.%I;', tbl);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation_policy ON public.%I
            FOR SELECT USING (
                organization_id = NULLIF(current_setting('app.current_org', true), '')::int
            );
        $f$, tbl);
        RAISE NOTICE 'RLS enabled on %', tbl;
    END LOOP;
END
$$;

-- B2. The `organizations` table keys on id, not organization_id.
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_policy ON public.organizations;
CREATE POLICY tenant_isolation_policy ON public.organizations
    FOR SELECT USING (
        id = NULLIF(current_setting('app.current_org', true), '')::int
    );

-- B3. Line-item tables carry no organization_id of their own -- they are only
--     tenant-scoped through their parent. A query that joins the parent is
--     already constrained by B1, but one that selects from the child alone
--     (SELECT SUM(line_amount) FROM income_items) would not be. These policies
--     close that gap.
DO $$
DECLARE
    child  text;
    parent text;
    fk     text;
BEGIN
    FOR child, parent, fk IN
        SELECT * FROM (VALUES
            ('income_items',           'income',           'income_id'),
            ('expense_items',          'expense',          'expense_id'),
            ('journal_entry_lines',    'journal_entries',  'journal_entry_id'),
            ('customer_payment_items', 'customer_payment', 'customer_payment_id'),
            ('delivery_note_lines',    'delivery_notes',   'delivery_note_id')
        ) AS t(child, parent, fk)
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = child AND column_name = fk
        ) THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', child);
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON public.%I;', child);
            EXECUTE format($f$
                CREATE POLICY tenant_isolation_policy ON public.%I
                FOR SELECT USING (
                    EXISTS (
                        SELECT 1 FROM public.%I p
                        WHERE p.id = public.%I.%I
                          AND p.organization_id
                              = NULLIF(current_setting('app.current_org', true), '')::int
                    )
                );
            $f$, child, parent, child, fk);
            RAISE NOTICE 'RLS enabled on % (via %)', child, parent;
        END IF;
    END LOOP;
END
$$;


-- =============================================================================
-- VERIFICATION -- run as ai_reader once Part A is applied.
-- =============================================================================
--   SET app.current_org = '5';
--   SELECT count(*) FROM income;              -- only org 5
--   RESET app.current_org;
--   SELECT count(*) FROM income;              -- 0 rows: unset org sees nothing
--   INSERT INTO income (id) VALUES (1);       -- must fail: read-only role
--
-- And as the owner, to confirm the application is unaffected:
--   SELECT count(*) FROM income;              -- unchanged, RLS bypassed
--
-- =============================================================================
-- ROLLBACK
-- =============================================================================
--   DO $$ DECLARE t text; BEGIN
--     FOR t IN SELECT c.relname FROM pg_class c
--              JOIN pg_namespace n ON n.oid = c.relnamespace
--              WHERE n.nspname='public' AND c.relrowsecurity
--     LOOP
--       EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY;', t);
--       EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON public.%I;', t);
--     END LOOP;
--   END $$;
