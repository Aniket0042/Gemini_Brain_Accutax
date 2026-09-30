# Accutax seed scripts (fixed copies)

Fixed copies of four scripts from `accutax_bk_backend/scripts/`. They fix the seed-data defects found in the multi-org test orgs 24–33 (see the "Seed data defects: multi-org test orgs 24–33" doc).

The Accutax backend is not changed. Three of the scripts only call its HTTP API. The backfill script loads the backend's own services to post journal entries.

**Nothing here has been run yet.** Run against staging or a copy of the database first, never the shared `accutax_bk_1_5` first.

## What was wrong, and what changed

| Script | Defect in the original | Change in this copy |
| --- | --- | --- |
| `seed-org-5yr-data.ts` | Statuses fell back to hardcoded ids 1/3/5, which are ACCEPTED/CANCELLED/RECEIVED in `accutax_bk_1_5`, not PENDING/PAID/PARTIALLY_PAID. A third of "paid" invoices became CANCELLED. | Statuses are looked up by name only. A missing status stops the run. |
| `seed-org-5yr-data.ts` | "Paid" invoices only had `amount_paid` set; no payment was recorded, so cash was never debited (Org2 cash −13.65M). | Documents are created PENDING, then paid through `income/customer-payment/create` and `expense/supplier-payment/create`. The app sets PAID / PARTIALLY_PAID and posts the cash journal itself. |
| `seed-org-5yr-data.ts` | Rejected creates were only counted, so a run could drop every bill silently (Org1 has no bills). | Every rejection is logged with its reason; the run stops above `--max-reject-pct` (default 2%). |
| `seed-org-5yr-data.ts` | — | New `--number-prefix` (default `SEEDV2`) tells a new run from the old `SEED5Y` data. Nothing is dated after "now". |
| `seed-5yr-invoices.ts` | Hardcoded `[1, 3, 5]` statuses; hardcoded login and a database password fallback. | PENDING by name, nothing paid (this script records no payments); credentials only from `.env`; no future dates. |
| `stress-income-create.ts` | Same status fallback; a quarter of documents got a random status (CANCELLED, VOIDED…). | PENDING by name, nothing paid. It stays a load test of `income/create`. |
| `backfill-journal-entries.ts` | Invoices only; would post revenue for CANCELLED and VOIDED invoices (~2,900 per org in orgs 24 and 25); wrote by default. | Dry run by default (`--apply` to write); skips CANCELLED and VOIDED; covers bills too; needs `--org-id` or `--all-orgs`. |

What these scripts cannot fix:
- Orgs 26–33 were seeded by a generator that is not in any repo on this machine (`INV-BULK-*`, `EXP-BULK-*`).
- The backfill only adds missing journal entries. It never rewrites an entry that is wrong, so the mismatched ledger of orgs 26–33 needs those orgs re-seeded, not backfilled.
- Org 24's `due_date = "string"` (income id 15) is a single bad row. It needs a one-line fix by whoever owns the database.

## Setup

```bash
cd Gemini_Brain_Accutax/backend/scripts/accutax_seed
npm install
cp .env.example .env   # then fill it in; .env is git-ignored
npm run typecheck
```

## Running (in this order, on staging first)

**1. Preview.** `--dry-run` builds the plan and makes no create or payment calls:

```bash
npx ts-node seed-org-5yr-data.ts --confirm-seed --email <user> --password <pass> --org-id <new test org> --order invoices-first --dry-run
```

**2. Seed.** Seed one new test org at a time:

```bash
npx ts-node seed-org-5yr-data.ts --confirm-seed --email <user> --password <pass> --org-id <new test org> --order invoices-first --drain-queue-between-orgs
```

**3. Backfill.** Run it only for documents created before this fix. It runs from inside `accutax_bk_backend`, because it uses that app's services, `node_modules` and `.env`:

```bash
cd accutax_bk_backend
npx ts-node -r tsconfig-paths/register ../Gemini_Brain_Accutax/backend/scripts/accutax_seed/backfill-journal-entries.ts --org-id <id>
```

It reports what it would post. Add `--apply` to write.

**4. Verify.** Re-run the read-only seed diagnosis and check the confirmation list in the defect doc.

## Open points to verify on the first staging run

- **Invoice ids.** The payment step reads invoice ids, totals and amounts due from `income/customer-payment/unpaid-invoices/:customerId`, whose response shape was checked in the backend code, and matches them by invoice number. Invoices are saved by a queue worker, so check on a small org (`--min-per-day 1 --max-per-day 1 --years 1`) that the log shows "Invoice payments: N/N recorded, 0 not found yet".
- **Credit notes.** `customer-payment/create` is called with `payment_type: INVOICE_PAYMENT` and one invoice per payment. Confirm the app does not also expect `credit_note_ids`.
- **Payment account.** Payments post to the org's first cash/bank account (`cashAccountId`, the same account the original used for cash expenses).
