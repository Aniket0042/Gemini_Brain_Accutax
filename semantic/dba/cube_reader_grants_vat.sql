-- =============================================================================
-- cube_reader: read access to the VAT tables, for the AccuTax AI VAT review.
-- For the DBA. Run as a superuser or the table owner on the AccuTax database.
-- NOT YET RUN. Review first; see docs/DBA_REQUEST_VAT_FIELDS.md and the discovery
-- results in docs/VAT_AGENT_COMPLETION_PLAN.md.
--
-- What each table is for:
--   tax_rates    the rate, code, type and recoverability behind income_items.tax_rate_id (nearly every sales line has one)
--                and the few expense_items.tax_rate_id (VAT category of a line: standard, zero rated, exempt, out of scope)
--   vat_configs  VAT filing frequency (monthly or quarterly), scheme and penalty settings, for due dates and penalties
-- Both carry organization_id, so Cube applies the same per-organization filter as for the other views.
-- vat_returns is NOT requested: it held no rows on 2026-10-15 (see docs/VAT_AGENT_COMPLETION_PLAN.md). Ask for it when
-- the application starts storing returns.
-- contacts (trn_number, tax_treatment) and organizations are already readable by cube_reader.
--
-- This only adds read access. No data, no other grant and no setting changes.
-- =============================================================================

GRANT SELECT ON public.tax_rates, public.vat_configs TO cube_reader;

-- Verify, connected as cube_reader:
--   SELECT count(*) FROM public.tax_rates WHERE organization_id = 25;     -- works
--   SELECT count(*) FROM public.vat_configs WHERE organization_id = 25;  -- works
--   UPDATE public.tax_rates SET name = name WHERE false;                  -- fails: read-only role
--
-- Rollback:
--   REVOKE SELECT ON public.tax_rates, public.vat_configs FROM cube_reader;
