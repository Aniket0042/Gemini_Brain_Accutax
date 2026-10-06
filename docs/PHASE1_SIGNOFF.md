# Phase 1 sign-off: governed metrics (Cube)

Date: 7 October 2026
For: finance (decisions 1–5), the Accutax backend owner (decision 3), the seed/DBA owner (decision 6)
Context: [`CUBE_CORE_INTEGRATION_GUIDE_V2.md`](./CUBE_CORE_INTEGRATION_GUIDE_V2.md), section 4 (metric contract)

Everything engineering can verify for Phase 1 is done and passing (section 1). Cube still runs in shadow mode: users see the existing answers. Cube starts answering (Phase 1b) only after the decisions below are signed and a week of real shadow logs reviews clean.

---

## 1. What is verified (on the VM, 7 Oct 2026, test orgs 24–33)

| Check | Result |
|---|---|
| Tenant isolation suite (tokens, raw cubes, filters, every view) | 17/17 pass |
| P&L vs Accutax `/report/profit-loss-with-accounts`, 12 windows × 10 orgs × 4 figures | 0 differences |
| Balance sheet balances (assets = liabilities + equity) | 0 differences |
| Sales, purchases, VAT, receivables, payables vs independently written SQL (YTD, last year, this month) | 0 differences |
| Golden tool calls (thresholds, rankings, periods, as-of dates, refusals) | 13/13 pass |
| Demo set, 30 questions, current answer path | 30/30 pass, slowest 5% within 2.7 s |
| Shadow comparison, 520 SQL-vs-Cube records | 0 unexplained (section 3) |

---

## 2. Decisions needed

### Decision 1 — Which source is official for revenue, expenses and profit?

| Option | What it means | Matches |
|---|---|---|
| **A. Posted ledger** (recommended, with decision 3) | Revenue = credits on Revenue accounts of posted journal entries; cost of sales = Expense accounts with sub-type "Cost of Sales" | The Accutax Profit & Loss report |
| B. Invoices and bills | Revenue = invoice lines before VAT; expenses = bill lines | What the AI answers today |

Evidence, org 24, 1 Jan – 7 Oct 2026:

| | AED |
|---|---|
| Ledger revenue | 5,809,352 |
| Invoiced sales (not cancelled, voided or draft) | 3,915,794 |
| Revenue still posted for **264 cancelled invoices**, never reversed | 2,018,323 |
| Ledger revenue on accepted and received invoices | 3,824,282 |

With cancelled invoices taken out, the two sources are within about 2% (3,824,282 vs 3,915,794). The remaining gap has not been reconciled line by line; dating (journal date vs invoice date) is the likely cause. Choosing A is safe only if cancellation reverses the ledger (decision 3). Otherwise the ledger, and the Accutax P&L report, overstate revenue.

### Decision 2 — What do ACCEPTED and RECEIVED mean on an invoice?

24,219 invoices in the test orgs have one of these statuses. Today they count as sales, but **not** as money owed (receivables count only PENDING and PARTIALLY_PAID). Please confirm:
- [ ] They are settled (paid): current treatment is right.
- [ ] They are still owed: receivables must include them.

### Decision 3 — Cancelled invoices that were already posted (Accutax owner)

Voiding an invoice posts a reversal (`void-invoice.service.ts`). No cancel path that reverses the ledger was found, and in the test orgs cancelled invoices keep their revenue in the ledger. Choose one:
- [ ] A posted invoice cannot be cancelled, only voided (block cancel after posting).
- [ ] Cancelling a posted invoice posts a reversal, like void.

### Decision 4 — Document rules (guide section 4.1)

| Rule | Proposed | Effect seen |
|---|---|---|
| Sales documents | INVOICE, CASH_INVOICE, INCOME (+), CREDIT_NOTE (−) | Only INVOICE exists today |
| Purchase documents | BILL, EXPENSE, CASH_EXPENSE (+), VENDOR_CREDIT (−) | |
| Drafts | Excluded everywhere | The current answer path counts them: org 24 VAT is overstated by 47,386.70 (draft invoices) |
| Void / cancel | Excluded (status CANCELLED or VOIDED, or voided_at set) | |
| Document date | Invoice date; for bills the reception date, else the created date | |

- [ ] Approved as proposed  - [ ] Changes: ________________

### Decision 5 — Defaults

- [ ] A figure asked without a period means **year to date**, in the organization's time zone (Asia/Dubai for nearly all).
- [ ] "Today" for receivables and payables is the date in the organization's time zone; documents dated after today are not yet owed.
- [ ] Amounts are never added across organizations with different currencies.

### Decision 6 — Test data (seed/DBA owner)

In test orgs 26–33, the journal entries created from invoices and bills post to unrelated accounts (credits to expense accounts, debits to equity, bills debiting revenue). Their ledger revenue is negative. Orgs 24–25 are posted correctly. Before any demo of ledger figures:
- [ ] Re-post orgs 26–33 from their documents through Accutax's own posting, or
- [ ] Demo only on orgs with clean ledgers.

---

## 3. Shadow review, 7 Oct 2026

Every SQL-vs-Cube difference in `logs/metrics_shadow.jsonl` (reviewed by `scripts/eval/shadow_review.py`):

| Category | Records | Meaning |
|---|---|---|
| basis_change | 306 | Revenue, expenses, profit, margin: ledger vs documents (decision 1) |
| match | 132 | Same figure |
| same_empty | 68 | "Nothing recorded" on one side, zero on the other |
| rule_drafts | 8 | VAT: the gap equals, to the fils, the draft invoices the current path counts (decision 4) |
| fixed_day_boundary | 6 | Receivables before 7 Oct: Cube used the UTC date; fixed to each organization's time zone |
| unexplained | 0 | |

Phase 1 closes when decisions 1–5 are signed, decision 3 has an owner and date, and seven days of real shadow logs review with nothing unexplained.

---

## 4. Sign-off

| Role | Name | Decisions | Date |
|---|---|---|---|
| Finance | | 1, 2, 4, 5 | |
| Accutax backend owner | | 3 | |
| Seed / DBA owner | | 6 | |
