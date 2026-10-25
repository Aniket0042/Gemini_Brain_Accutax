# Accutax agent catalog

Readiness key:
- **Ready:** existing tools cover it. Needs only a profile and a routine.
- **New tool:** the data is there, but a tool must be built.
- **Blocked:** needs new DB fields, a new data source, or an integration.

## Tax and compliance

| Agent | Job | Proactive routine | Readiness |
|---|---|---|---|
| **VAT Agent** | VAT law Q&A, bill checks, books review, return position, penalties | Weekly review digest; return due at 14 and 7 days | Ready (`review_vat`, `vat_return`, `vat_penalty`, `search_vat_kb`) |
| **VAT Return Prep Agent** | Builds the return worksheet for each period and explains output vs input VAT | At period end: draft worksheet plus a list of likely errors | Ready for figures; full return box mapping is Blocked (no VAT category or reverse-charge fields) |
| **Sales VAT Agent** | Duplicate and odd sales invoices, output VAT checks | Weekly sales review | New tool (package 4 in the VAT completion plan) |
| **Supplier TRN Agent** | Checks supplier TRNs are present and valid; flags input VAT claimed without a TRN | On new supplier or new bill | Blocked (no TRN field on bills) |
| **Corporate Tax Agent** | UAE CT 9% estimate from P&L, small business relief check, explains non-deductible items, CT law Q&A | Quarterly CT estimate; registration and filing deadlines | New tool plus a CT knowledge base; P&L data exists |
| **E-invoicing Readiness Agent** | Checks invoices against the fields the UAE e-invoicing mandate requires | On invoice create; readiness score each month | Blocked (needs the mandate field list and invoice fields) |
| **Compliance Calendar Agent** | All deadlines: VAT, CT, trade licence, audit | Reminders at 30, 14 and 7 days | New tool (needs each org's filing period and licence dates) |
| **FTA Penalty Agent** | Late filing or payment penalty if action is delayed; voluntary disclosure guidance | When a deadline is missed or near | Ready (`vat_penalty`) |

## Bookkeeping and payables

| Agent | Job | Proactive routine | Readiness |
|---|---|---|---|
| **AP / Bills Agent** | Duplicate bills; checks on uploaded bills | On upload: check the bill; weekly duplicate scan | Ready (`review_vat` duplicates mode, `read_document`) |
| **Document Intake Agent** | Reads uploaded invoices and receipts, extracts fields, suggests a draft bill | On upload | Partial (extraction is built; creating a bill is a write action, so it needs approval in phase 2) |
| **Expense Categorisation Agent** | Suggests an expense category, cost centre or project; flags odd coding | On new bill; monthly coding review | New tool (category, project and cost centre fields exist in the cube) |
| **Anomaly / Fraud Agent** | Unusual amounts, new supplier with a large first bill, round numbers, split bills | Weekly | Partial (10 anomaly rules exist in `review_vat`; supplier-pattern rules would be new) |
| **Bank Reconciliation Agent** | Matches bank lines to bills and invoices; lists what is unmatched | Daily | Blocked unless bank feed data is reachable (needs checking) |
| **Month-End Close Agent** | Close checklist: unposted drafts, missing accruals, unreconciled items, then a close summary | 3 days before and on period close | New tool (draft and posted status exist; checklist logic is new) |

## Cash and receivables

| Agent | Job | Proactive routine | Readiness |
|---|---|---|---|
| **Cash Agent** | Cash position and forecast | Weekly cash outlook; alert when the forecast goes below a set floor | Ready (`cash_forecast`) |
| **Collections / AR Agent** | Overdue customer invoices, ageing, drafts of reminder emails for approval | Weekly ageing; reminder drafts | New tool; due dates on sales need checking; sending email needs approval |
| **Payables Planning Agent** | Which bills to pay this week given cash | Weekly pay-run proposal | Blocked (bills have no due date) |

## Reporting and analysis

| Agent | Job | Proactive routine | Readiness |
|---|---|---|---|
| **Reporting Agent** | Figures and reports on request | Month-end pack after close | Ready (`query_metrics`, report artifacts) |
| **CFO / Insights Agent** | Margin, cost trends, branch comparison; explains changes between months | Monthly "what changed and why" | Ready for figures (`query_metrics`); narrative quality needs an eval |
| **Budget vs Actual Agent** | Variance against budget; flags overspend | Monthly; alert on overspend | Blocked unless budgets are stored (needs checking) |
| **Multi-org / Group Agent** | Compares orgs a user manages; consolidated view | Monthly group summary | Ready (multi-org scope exists); real consolidation with eliminations is new |

## Firm and product

| Agent | Job | Proactive routine | Readiness |
|---|---|---|---|
| **Accountant's Practice Agent** | For firms with many client orgs: which clients have deadlines, findings or missing data | Daily triage list across clients | New tool (builds on other agents' findings) |
| **Audit Prep Agent** | Gathers the documents and schedules auditors ask for; flags gaps | Before year end | New tool |
| **App Guide / Onboarding Agent** | How to use Accutax; setup checklist for new orgs | On new org: setup gaps | Ready (`app_guide`) |
| **Data Quality Agent** | Missing fields, drafts left open, odd seed-like data | Weekly | New tool |


