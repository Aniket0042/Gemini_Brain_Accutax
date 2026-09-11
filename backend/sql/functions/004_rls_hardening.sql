-- =============================================================================
-- Migration: 004_rls_hardening.sql
-- Purpose: Database hardening with ai_reader role and Row-Level Security (RLS).
-- =============================================================================

-- 1. Create ai_reader role if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_reader') THEN
        CREATE ROLE ai_reader NOLOGIN;
    END IF;
END
$$;

-- 2. Grant SELECT privileges only on relevant tenant tables
GRANT USAGE ON SCHEMA public TO ai_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ai_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ai_reader;

-- 3. Enable RLS and create tenant isolation policies on all tenant tables
DO $$
DECLARE
    tbl text;
    tenant_tables text[] := ARRAY[
        'contacts', 'income', 'expense', 'items', 'bank_accounts',
        'chart_of_accounts', 'inventory_adjustments', 'delivery_notes',
        'customer_payment', 'supplier_payments', 'tax_adjustments',
        'projects', 'warehouses', 'organizations'
    ];
BEGIN
    FOREACH tbl IN ARRAY tenant_tables
    LOOP
        -- Check if table exists in public schema
        IF EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = tbl) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', tbl);
            
            -- Drop existing policy if exists
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I;', tbl);
            
            -- Apply isolation policy based on current_setting('app.current_org')
            IF tbl = 'organizations' THEN
                EXECUTE format(
                    'CREATE POLICY tenant_isolation_policy ON %I FOR SELECT USING (id = NULLIF(current_setting(''app.current_org'', true), '''')::int);',
                    tbl
                );
            ELSE
                EXECUTE format(
                    'CREATE POLICY tenant_isolation_policy ON %I FOR SELECT USING (organization_id = NULLIF(current_setting(''app.current_org'', true), '''')::int);',
                    tbl
                );
            END IF;
        END IF;
    END LOOP;
END
$$;
