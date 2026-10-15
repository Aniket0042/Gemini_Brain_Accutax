# VAT agent: plan to finish it

Written 2026-10-15. Where things stand is in `docs/VAT_REVIEW_PLAN.md` (the books review, built) and `docs/DOCUMENT_UPLOAD_PLAN.md` (checks on one uploaded bill, built). This plan covers everything else needed to call the VAT agent complete, in the order to build it. Estimates are rough working days for one person.

## What "complete" means

The agent can: answer UAE VAT law questions with the right sources; check one bill; review the books for duplicates and odd entries; review sales as well as purchases; show the VAT return position for a period and what could be wrong with it; calculate penalties; and remind about deadlines. It never says something about the books without looking at them, and the people who own the rules (finance) have signed off the thresholds.

## Work packages

Packages 1 to 5 need no change to the shared database. Package 6 needs the DBA. Package 7 is the pilot.

| # | Package | What it adds | Days | Needs |
|---|---|---|---|---|
| 1 | Safety | Code guard on "recorded" claims (**done 2026-10-15**); prompt-injection guard for text from files and the web; audit trail of findings and what the user did | 4 | nothing |
| 2 | Bill checks | The full list of items a UAE tax invoice must show, as a code check; simplified-invoice rules; credit notes; blocked and partial input VAT claims (entertainment, personal use); AED value for foreign bills | 8 | a rate source for AED values |
| 3 | Law answers | Code check that a law answer cites sources; test and fix Arabic questions; a monthly refresh of the FTA documents; a 40-question law evaluation | 5 | nothing |
| 4 | Sales review | The same duplicate and odd-entry rules on sales invoices (output VAT, customer TRN where held) | 4 | nothing |
| 5 | VAT return, with the data we have | Output vs input VAT by period; what is likely wrong in a period (the review findings, summarised); penalty calculator from the FTA penalty rules; return due dates; reminders through the scheduler the weekly reports will use | 8 | the scheduler (weekly reports plan) |
| 6 | Fields the books lack | Supplier TRN check; VAT category (standard, zero-rated, exempt, out of scope); reverse-charge flag and figures; reconcile the return with the ledger | 10 | DBA go-ahead and finance input |
| 7 | Pilot and sign-off | One week with real users; finance signs the thresholds; fix what the pilot shows; open to all users | 7 | users, finance |

Total about 46 working days if packages run one after another. Packages 1 to 4 can overlap with the DBA waiting time.

## Order

1. Package 1 (started), because every later feature depends on trustworthy answers.
2. Ask the DBA for package 6 fields now; the answer takes the longest.
3. Packages 2, 3 and 4 in any order; package 2 first, because bills are what users upload.
4. Package 5.
5. Package 6 when the fields exist.
6. Package 7. The pilot can start on packages 1 to 4 before package 6 is ready.

## Request for the DBA (to send once approved)

The Cube views read from the shared database through the read-only user. Three additions are needed, each for purchases first and sales second:

1. **Supplier or customer TRN** on bills and sales invoices (a text field, null when unknown), in the `purchases` and `sales` views.
2. **VAT category** on each document line: standard rated, zero rated, exempt, out of scope.
3. **Reverse-charge flag** on each bill line, and the reverse-charge VAT amount as a measure in the `vat` view.

Without these the agent cannot check supplier TRNs, split zero-rated from exempt purchases, or report reverse-charge figures. The agent only reads these fields; it never writes to the database.

## Decisions for the owner

1. Approve the request to the DBA above.
2. Name who in finance signs the thresholds in `backend/src/gemini_brain/vat_review/thresholds.py`.
3. Purchases only, or sales as well (package 4 builds sales).
4. Which exchange-rate source to use for AED values (package 2).

## Progress

- 2026-10-15: package 1, code guard on claims about the books. An answer about an attached bill that says it is, or is not, in the books must follow a look at the books (`list_documents` or `review_vat`). Without one the agent is sent back once to check; if it still has not, the answer ends with a line saying the claim is not confirmed. Seven unit tests.
