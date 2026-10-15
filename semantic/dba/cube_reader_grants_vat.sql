-- =============================================================================
-- cube_reader: read access to the VAT tables, for the AccuTax AI VAT review.
-- For the DBA. Run as a superuser or the table owner on the AccuTax database.
-- NOT YET RUN. Review first; see docs/DBA_REQUEST_VAT_FIELDS.md and the discovery
-- results in docs/VAT_AGENT_COMPLETION_PLAN.md.
--
-- What each table is for:
--   tax_rates    the rate, code, type and recoverability behind expense_items.tax_rate_id and income_items.tax_rate_id
--                (VAT category of a line; zero-rated, exempt and standard rated)
--   vat_configs  VAT registration, filing frequency, reverse-charge settings, penalty settings
--   vat_returns  the VAT returns filed or drafted (output VAT, input VAT, net payable, status, due and paid dates)
-- All three carry organization_id, so Cube applies the same per-organization filter as for the other views.
-- contacts (trn_number, tax_treatment) and organizations are already readable by cube_reader.
--
-- This only adds read access. No data, no other grant and no setting changes.
-- =============================================================================

GRANT SELECT ON public.tax_rates, public.vat_configs, public.vat_returns TO cube_reader;

-- Verify, connected as cube_reader:
--   SELECT count(*) FROM public.tax_rates WHERE organization_id = 25;     -- works
--   SELECT count(*) FROM public.vat_returns WHERE organization_id = 25;   -- works
--   UPDATE public.tax_rates SET name = name WHERE false;                  -- fails: read-only role
--
-- Rollback:
--   REVOKE SELECT ON public.tax_rates, public.vat_configs, public.vat_returns FROM cube_reader;
