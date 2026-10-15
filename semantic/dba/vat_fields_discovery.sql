-- =============================================================================
-- VAT fields discovery: read-only. Where does the database already keep the
-- supplier/customer TRN, the VAT rate or code, the VAT category and the
-- reverse-charge flag? See docs/DBA_REQUEST_VAT_FIELDS.md.
--
-- Run on accutax_bk_1_5 with any role that can read information_schema and the
-- tables below (the postgres role, or cube_reader for the tables it can read).
-- Every statement is a SELECT. Nothing here changes data, grants or settings.
-- =============================================================================

-- 1. Columns on the tables the Cube views already read whose name suggests a tax fact.
SELECT table_name, column_name, data_type
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name IN ('contacts', 'expense', 'expense_items', 'income', 'income_items', 'items', 'organizations')
  AND  column_name ~* '(trn|tax|vat|gst|reverse|rcm|registration|exempt|zero|rate|category|code)'
ORDER  BY table_name, ordinal_position;

-- 2. Any other table whose name suggests a tax table (a rate or code lookup, for example).
SELECT table_name
FROM   information_schema.tables
WHERE  table_schema = 'public'
  AND  table_name ~* '(tax|vat|trn)'
ORDER  BY table_name;

-- 3. Columns of those tables.
SELECT c.table_name, c.column_name, c.data_type
FROM   information_schema.columns c
JOIN   information_schema.tables  t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE  c.table_schema = 'public' AND t.table_name ~* '(tax|vat|trn)'
ORDER  BY c.table_name, c.ordinal_position;

-- 4. Which of those the Cube role can already read.
SELECT table_name, privilege_type
FROM   information_schema.role_table_grants
WHERE  grantee = 'cube_reader' AND table_schema = 'public' AND table_name ~* '(tax|vat|trn|contacts|expense_items|income_items)'
ORDER  BY table_name;

-- 5. After steps 1 to 3 name the columns, fill them in below and run: how often is each filled in?
--    (Replace <column> and <table>; one statement for each fact. Counts only, no values.)
-- SELECT count(*) AS rows,
--        count(<column>) AS filled,
--        round(100.0 * count(<column>) / nullif(count(*), 0), 1) AS percent_filled
-- FROM   public.<table>;
--
-- Example values for a rate or code column, most common first:
-- SELECT <column>, count(*) FROM public.<table> GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
