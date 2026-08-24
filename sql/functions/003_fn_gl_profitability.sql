-- =============================================================================
-- Migration: 003_fn_gl_profitability.sql
-- Purpose: GL Profitability rollup joining chart_of_accounts account_type to income and expense totals, net margin.
-- Specs: LANGUAGE sql STABLE PARALLEL SAFE, parameter binding, LIMIT 500.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_gl_profitability(
    p_org INT,
    p_from DATE DEFAULT '2020-01-01',
    p_to DATE DEFAULT '2099-12-31'
)
RETURNS TABLE (
    account_type VARCHAR,
    account_count BIGINT,
    total_income NUMERIC,
    total_expense NUMERIC,
    net_margin NUMERIC
)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    WITH income_by_account AS (
        SELECT
            coa.account_type,
            COUNT(DISTINCT coa.id)::BIGINT AS acct_count,
            COALESCE(SUM(ii.line_amount), 0)::NUMERIC AS total_inc
        FROM chart_of_accounts coa
        LEFT JOIN income_items ii ON ii.account_id = coa.id
        LEFT JOIN income inc ON inc.id = ii.income_id
            AND inc.organization_id = p_org
            AND (CAST(inc.invoice_date AS DATE) >= p_from AND CAST(inc.invoice_date AS DATE) <= p_to)
        WHERE coa.organization_id = p_org
        GROUP BY coa.account_type
    ),
    expense_by_account AS (
        SELECT
            coa.account_type,
            COALESCE(SUM(e.amount_paid), 0)::NUMERIC AS total_exp
        FROM chart_of_accounts coa
        LEFT JOIN expense e ON e.account_id = coa.id
            AND e.organization_id = p_org
            AND (CAST(e.created_date AS DATE) >= p_from AND CAST(e.created_date AS DATE) <= p_to)
        WHERE coa.organization_id = p_org
        GROUP BY coa.account_type
    )
    SELECT
        COALESCE(inc_acc.account_type, exp_acc.account_type, 'General')::VARCHAR AS account_type,
        COALESCE(inc_acc.acct_count, 1)::BIGINT AS account_count,
        COALESCE(inc_acc.total_inc, 0)::NUMERIC AS total_income,
        COALESCE(exp_acc.total_exp, 0)::NUMERIC AS total_expense,
        (COALESCE(inc_acc.total_inc, 0) - COALESCE(exp_acc.total_exp, 0))::NUMERIC AS net_margin
    FROM income_by_account inc_acc
    FULL OUTER JOIN expense_by_account exp_acc ON exp_acc.account_type = inc_acc.account_type
    ORDER BY net_margin DESC
    LIMIT 500;
$$;
