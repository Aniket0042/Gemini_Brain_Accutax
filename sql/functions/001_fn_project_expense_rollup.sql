-- =============================================================================
-- Migration: 001_fn_project_expense_rollup.sql
-- Purpose: Project expense rollup by project, vendor contact, and bank account.
-- Specs: LANGUAGE sql STABLE PARALLEL SAFE, parameter binding, LIMIT 500.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_project_expense_rollup(
    p_org INT,
    p_from DATE DEFAULT '2020-01-01',
    p_to DATE DEFAULT '2099-12-31'
)
RETURNS TABLE (
    project_id INT,
    project_name VARCHAR,
    vendor_contact_name VARCHAR,
    bank_account_name VARCHAR,
    transaction_count BIGINT,
    total_spend NUMERIC
)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    SELECT
        p.id AS project_id,
        COALESCE(p.project_name, 'Unassigned Project')::VARCHAR AS project_name,
        COALESCE(c.name, 'Unknown Vendor')::VARCHAR AS vendor_contact_name,
        COALESCE(b.account_name, b.bank_name, 'Unknown Account')::VARCHAR AS bank_account_name,
        COUNT(e.id)::BIGINT AS transaction_count,
        COALESCE(SUM(e.amount_paid), 0)::NUMERIC AS total_spend
    FROM projects p
    LEFT JOIN expense e ON e.project_id = p.id
        AND e.organization_id = p_org
        AND (CAST(e.created_date AS DATE) >= p_from AND CAST(e.created_date AS DATE) <= p_to)
    LEFT JOIN contacts c ON c.id = e.contact_id
    LEFT JOIN bank_accounts b ON b.id = e.account_id
    WHERE p.organization_id = p_org
    GROUP BY p.id, p.project_name, c.name, b.account_name, b.bank_name
    ORDER BY total_spend DESC, p.project_name ASC
    LIMIT 500;
$$;
