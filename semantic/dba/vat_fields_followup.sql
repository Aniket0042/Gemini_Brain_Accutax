-- =============================================================================
-- VAT fields, part 3: the shape of the TRN column, and the test organizations.
-- Read-only: SELECT only. Counts and lengths, never a TRN value. Run after
-- vat_fields_values.sql. See docs/VAT_AGENT_COMPLETION_PLAN.md.
-- =============================================================================

\echo '== 1. contacts.trn_number: what do the values look like (length and character classes, no values)'
SELECT length(trim(trn_number)) AS length, count(*) AS contacts,
       count(*) FILTER (WHERE trim(trn_number) ~ '^[0-9]+$')      AS digits_only,
       count(*) FILTER (WHERE trim(trn_number) ~ '^[0-9A-Za-z]+$') AS letters_and_digits
FROM   public.contacts WHERE trn_number IS NOT NULL AND trim(trn_number) <> ''
GROUP  BY 1 ORDER BY 2 DESC LIMIT 12;

\echo '== 2. The same for the test organizations 24 to 33'
SELECT organization_id, count(*) AS contacts, count(NULLIF(trim(trn_number), '')) AS with_trn,
       count(*) FILTER (WHERE length(regexp_replace(coalesce(trn_number, ''), '\D', '', 'g')) = 15) AS trn_15_digits,
       count(*) FILTER (WHERE trim(coalesce(trn_number, '')) ~ '^[0-9]{15}$') AS exactly_15_digits
FROM   public.contacts WHERE organization_id BETWEEN 24 AND 33 GROUP BY 1 ORDER BY 1;

\echo '== 3. Purchase lines with a tax_rate_id, by organization (test organizations and the top ten)'
SELECT e.organization_id, count(*) AS lines, count(ei.tax_rate_id) AS with_tax_rate_id
FROM   public.expense_items ei JOIN public.expense e ON e.id = ei.expense_id
WHERE  e.organization_id BETWEEN 24 AND 33 GROUP BY 1 ORDER BY 1;

SELECT e.organization_id, count(ei.tax_rate_id) AS lines_with_tax_rate_id
FROM   public.expense_items ei JOIN public.expense e ON e.id = ei.expense_id
WHERE  ei.tax_rate_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

\echo '== 4. Sales lines: which VAT types do they use (needs tax_rates, which the postgres role can read)'
SELECT tr.tax_type, tr.rate_type, count(*) AS sales_lines
FROM   public.income_items ii LEFT JOIN public.tax_rates tr ON tr.id = ii.tax_rate_id
GROUP  BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo '== 5. Purchase lines: tax_amount against the amount, for the test organizations (5%, zero, other)'
SELECT e.organization_id,
       count(*) FILTER (WHERE coalesce(ei.tax_amount, 0) = 0)                                     AS no_tax,
       count(*) FILTER (WHERE ei.line_amount > 0 AND abs(ei.tax_amount - ei.line_amount * 0.05) <= 0.05 + 0.002 * ei.line_amount) AS five_percent,
       count(*) FILTER (WHERE ei.tax_amount <> 0 AND NOT (ei.line_amount > 0 AND abs(ei.tax_amount - ei.line_amount * 0.05) <= 0.05 + 0.002 * ei.line_amount)) AS other
FROM   public.expense_items ei JOIN public.expense e ON e.id = ei.expense_id
WHERE  e.organization_id BETWEEN 24 AND 33 GROUP BY 1 ORDER BY 1;
