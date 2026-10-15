# VAT review (duplicates and anomalies in the books): plan and phase 0 findings

Status: phase 0 done on 2026-10-15. Phases 1 to 4 not started.

## Goal

Let the agent scan an organization's bills and say which look like duplicates or have VAT problems, with the numbers behind each finding. Code finds the problems; the model only explains them. The feature is read-only and needs no new table.

## Phase 0 findings (read-only queries on the VM, organizations 24 to 33)

**What a bill row holds today** (Cube view `purchases`, `list_documents` type `bills`): document number, document id, date, weekday, document type, status, vendor name, net purchases, input VAT. Also available in the cube, not in the document list: expense category, project, cost centre.

**What it does not hold:** the supplier's TRN, a currency per document, a due date, a VAT category (standard, zero, exempt), a reverse-charge flag, the date the bill was created, or a link to the source file. The `vat` view has only output VAT, input VAT, net VAT payable and standard-rated sales.

**Row cap.** Cube returns at most 500 rows per query (`MAX_ROWS` in `semantic/cube.js`), and at most 25 organizations per token. A scan of a whole ledger must therefore split the date range until each slice returns fewer than 500 rows. A splitting scan of 4,126 bills (organization 27) took about 4 seconds in 13 splits, so this is practical.

**Test data quality.**

| Organization | Bills | Vendors | Document types | VAT on bills |
|---|---|---|---|---|
| 24 | 0 | 0 | none | none |
| 25 | 3,669 | 90 | BILL 1,881, CASH_EXPENSE 1,788 | 375 at 5%, 475 at 0, 2,819 other |
| 26 | 1,016 | 86 | EXPENSE | all 0 |
| 27 | 4,126 | 62 | EXPENSE | all 0 |
| 28 to 33 | 1,132 to 2,912 each | 73 to 83 | EXPENSE | all 0 |

- Only organization 25 has real-looking VAT amounts. The other organizations were seeded with VAT of 0 on every bill, so VAT-rate rules cannot be tested on them (this matches the earlier note that odd figures are seed data).
- No organization has two bills with the same vendor and the same document number.
- Organization 27 has 579 groups of bills with the same vendor, date and net amount. This is seed data, and it is the best set for testing the "likely duplicate" rule. Other organizations have none of these groups, but 117 to 236 groups share a vendor and an amount on different dates.
- About a quarter of bills fall on weekends in every seeded organization, so a weekend rule would flag a quarter of all bills. It needs a business-days setting or should be dropped.

## Consequences for the plan

1. **Duplicates:** build rules 1 and 2 (same vendor and number; same vendor, same total, within 3 days) on the existing fields. Test on organization 27.
2. **VAT amount rules** (VAT not 5% of net, zero VAT on a taxable supplier): test on organization 25 only. Seed organizations would flag every bill.
3. **Supplier TRN rules:** blocked until Cube has a TRN field. It needs a DBA go-ahead.
4. **Weekend rule:** drop, or make it a setting per organization.
5. **Scan by date slices:** build one reusable function that splits the range until each slice is under 500 rows.
6. **Document types:** organization 25 mixes BILL and CASH_EXPENSE. Rules must say which types they cover (a cash expense may not carry a supplier invoice number).

## Open decisions

1. Purchases only first, or sales as well (sales view has the same 500-row cap)?
2. Who signs off the thresholds?
3. Approve asking the DBA for three fields: supplier TRN, VAT category, reverse-charge flag?

## Phases

- Phase 1, duplicate detection (2 days).
- Phase 2, anomaly rules (3 days).
- Phase 3, the answer and the findings table, with Excel export (2 days); search the FTA sources by finding to vary the sources.
- Phase 4, tests on seeded problems, an evaluation set of about 20 questions, one paid run, rollout behind the allowlist (2 days).
