-- =============================================================================
-- cube_reader: read access for the reverse-charge review and the VAT 201 draft.
-- For the DBA. Run as a superuser or the table owner on the AccuTax database.
-- NOT YET RUN. Review first; see docs/RCM_VAT201_IMPLEMENTATION_PLAN.md.
--
--   country_type  the country of a contact (contacts.country_id). A lookup table of 10 rows with no
--                 organization data. Used to tell UAE suppliers from suppliers outside the UAE.
--
-- Also needed, if not run yet: semantic/dba/cube_reader_grants_vat.sql (tax_rates, vat_configs).
-- This only adds read access. No data, no other grant and no setting changes.
-- =============================================================================

GRANT SELECT ON public.country_type TO cube_reader;

-- Verify, connected as cube_reader:
--   SELECT count(*) FROM public.country_type;            -- works: 10 rows
--   UPDATE public.country_type SET code = code WHERE false; -- fails: read-only role
--
-- Rollback:
--   REVOKE SELECT ON public.country_type FROM cube_reader;
