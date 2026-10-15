-- =============================================================================
-- VAT fields, part 2: what do the columns hold, and how often are they filled in?
-- Read-only: every statement is a SELECT. It prints counts and the kinds of values
-- (codes, rates, types, JSON key names), never a TRN, a name, an amount or a document.
-- Run after vat_fields_discovery.sql. See docs/DBA_REQUEST_VAT_FIELDS.md.
-- =============================================================================

\echo '== 1. Supplier and customer TRN: how many contacts have one, and how long is it'
SELECT count(*)                                             AS contacts,
       count(NULLIF(trim(trn_number), ''))                  AS with_trn,
       count(*) FILTER (WHERE length(regexp_replace(coalesce(trn_number, ''), '\D', '', 'g')) = 15) AS trn_has_15_digits
FROM   public.contacts;

\echo '== 2. contacts.tax_treatment values'
SELECT tax_treatment, count(*) FROM public.contacts GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo '== 3. tax_rates: the codes, rates and types that exist'
SELECT tax_code, tax_rate, rate_type, tax_type, is_recoverable, is_active, count(*) AS rows
FROM   public.tax_rates
GROUP  BY 1, 2, 3, 4, 5, 6
ORDER  BY rows DESC
LIMIT  40;

\echo '== 4. Lines with a tax_rate_id: how many, and which rates they use'
SELECT 'expense_items' AS line_table, count(*) AS lines, count(tax_rate_id) AS with_tax_rate_id FROM public.expense_items
UNION ALL
SELECT 'income_items', count(*), count(tax_rate_id) FROM public.income_items;

SELECT tr.tax_code, tr.tax_rate, tr.rate_type, count(*) AS bill_lines
FROM   public.expense_items ei
LEFT   JOIN public.tax_rates tr ON tr.id = ei.tax_rate_id
GROUP  BY 1, 2, 3
ORDER  BY bill_lines DESC
LIMIT  25;

\echo '== 5. Reverse charge: where could it be? JSON key names only'
SELECT 'vat_configs.reverse_charge' AS source, count(reverse_charge) AS rows_filled FROM public.vat_configs;
SELECT DISTINCT jsonb_object_keys(reverse_charge) AS key_in_reverse_charge
FROM   public.vat_configs WHERE jsonb_typeof(reverse_charge) = 'object' LIMIT 30;
SELECT entry_type, tax_type, count(*) FROM public.tax_entries GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
SELECT action_type, count(*) FROM public.tax_rules GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
SELECT rate_type, tax_type, count(*) FROM public.tax_rates
WHERE  tax_code ~* '(reverse|rcm)' OR name ~* '(reverse|rcm)' OR description ~* '(reverse|rcm)' GROUP BY 1, 2;

\echo '== 6. VAT returns: how many, and what status'
SELECT status, count(*) AS returns, min(period_start) AS first_period, max(period_end) AS last_period
FROM   public.vat_returns GROUP BY 1 ORDER BY 2 DESC;
SELECT count(DISTINCT organization_id) AS organizations_with_returns FROM public.vat_returns;

\echo '== 7. VAT configuration: filing frequency and scheme'
SELECT filing_frequency, scheme_type, vat_type, count(*) FROM public.vat_configs GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 15;
SELECT count(*) AS configs, count(penalties_config) AS with_penalties_config FROM public.vat_configs;

\echo '== 8. The organizations'' own VAT number: how many have one'
SELECT count(*) AS organizations, count(NULLIF(trim(vat_registeration_number), '')) AS with_vat_number,
       count(NULLIF(trim(trn_registeration_number), '')) AS with_trn_number
FROM   public.organizations;

\echo '== 9. Which of these tables can cube_reader read now'
SELECT t.table_name,
       has_table_privilege('cube_reader', 'public.' || t.table_name, 'SELECT') AS cube_reader_can_select
FROM   (VALUES ('contacts'), ('organizations'), ('expense_items'), ('income_items'), ('tax_rates'), ('vat_configs'),
               ('vat_returns'), ('tax_entries'), ('tax_calculations'), ('tax_rules'), ('tax_adjustments')) AS t(table_name);
