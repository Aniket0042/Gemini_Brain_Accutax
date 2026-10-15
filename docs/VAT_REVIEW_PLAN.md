# VAT review (duplicates and anomalies in the books): plan and phase 0 findings

Status: phases 0 and 1 done on 2026-10-15 (phase 1 deployed to the VM). Phases 2 to 4 not started.

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

## Phase 1 result: duplicate detection (built and deployed 2026-10-15)

Code: `backend/src/gemini_brain/vat_review/` (`scan.py`, `duplicates.py`, `tool.py`) and the `review_vat` agent tool (`agent/tools.py`), with a prompt rule and document links in `agent/prompt.py` and `agent/links.py`. Tests: `backend/tests/unit/test_vat_review.py` (30 tests; the full suite passes).

**Rules.** Sure: same supplier and same document number. Likely: same supplier and same amount, dated within 3 days of the group's first bill. Possible: alike supplier names, same amount, same date. Voided, cancelled, rejected and draft bills are left out. A bill with no amount is only checked by its number.

**On real data (VM, read-only):**

| Organization | Bills scanned | Time | Groups found | Note |
|---|---|---|---|---|
| 25 (real-looking) | 3,669 | 2.9 s | 0 | Correct: no duplicates were found, and the answer says that does not prove there are none |
| 26 | 1,016 | 0.5 s | 237 likely | Seed data: the same amount on consecutive days |
| 27 | 4,126 | 3.3 s | 117 likely over all years; 168 for 2023 alone after the clustering fix | Seed data, as above |

The agent answered all three questions with only the `review_vat` tool, said the period and the number of bills checked, called the groups "likely" and asked for a person to confirm them. Cost was $0.003 to $0.043 per answer.

**Two fixes made while testing.** Groups first chained day after day into one group of 38 bills; a group is now limited to 3 days from its first bill. Cube returns ids as Decimal, which broke the call to the model; scan rows now go through the same text conversion as `list_documents`.

**Not yet proven.** The "sure" rule has no real example, because no test organization has two bills with the same supplier and number; it is covered by unit tests only. Test by planting known duplicates (phase 4). On organization 25 one answer was marked "not grounded" by the verifier (shadow mode); the cause is not yet looked at.
