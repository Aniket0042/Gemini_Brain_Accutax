# Cube Core Beyond the Chatbot: Roadmap

Date: 9 October 2026
Status: proposed. Nothing in sections 5–7 is built yet unless marked as built.
For: engineering leads, the Accutax backend owner, product, finance
Replaces: the first version of this file, kept in [`archive/CUBE_CORE_FULL_POTENTIAL_BLUEPRINT_v1.md`](./archive/CUBE_CORE_FULL_POTENTIAL_BLUEPRINT_v1.md). Section 9 lists what changed.
Context:
- [`ai-architecture-review-2026-10.md`](./ai-architecture-review-2026-10.md): demo failure, root causes, target architecture
- [`CUBE_CORE_INTEGRATION_GUIDE_V2.md`](./CUBE_CORE_INTEGRATION_GUIDE_V2.md): how Cube is built and deployed, metric contract, rollup plan (section 13)
- [`PHASE1_SIGNOFF.md`](./PHASE1_SIGNOFF.md): open finance decisions
- [`PHASE2_AGENT.md`](./PHASE2_AGENT.md): the agent and its measured results

---

## 1. Summary

Cube Core already serves every figure the AI assistant gives. The next opportunity is to make it the single place where Accutax defines its business numbers, so that the AI, the dashboards and the reports cannot disagree.

That goal is right, but it is not free, and it is not only a Cube problem:

- **One definition is not the same as correct data.** Cancelled invoices keep their revenue in the ledger, and the Accutax P&L screen leaves cost of sales out of net profit. Cube will make these problems consistent everywhere until they are fixed at the source.
- **Cube is an aggregation engine.** It suits totals, trends, rankings and balances. It does not suit ledger detail, running balances or 100-page exports. Those stay in SQL.
- **Speed claims need measuring.** No rollups exist yet. Every Cube query reads Postgres today.

This document therefore proposes a sequence of workstreams. Each one starts only when the previous gate is met, and each one ends on a measured exit check, not on a date.

```
TODAY
  AI assistant ──► Gemini Brain agent ──► Cube Core ──► Postgres (shared, primary)
  Dashboards   ──► NestJS report.service.ts (5,592 lines of SQL) ──► Postgres

TARGET
  AI assistant ──► Gemini Brain agent ──┐
  Dashboards   ──► NestJS (authorises, mints token) ──┼──► Cube Core ──► Cube Store rollups
  MCP clients  ──► Accutax MCP server (our own) ──┘        │
                                                           └──► Postgres read replica
  Ledger detail, statements, exports ──► NestJS SQL (stays)
```

---

## 2. Where we are today (verified 9 Oct 2026)

| Area | State | Source |
|---|---|---|
| Cube deployment | Cube 1.7.50 on the VM, rootless Podman, bound to 127.0.0.1:4000 only | Guide section 7.5 |
| Tenant isolation | `cube.js` `queryRewrite` rejects tokens that live longer than 120 s or have no org scope, allows only listed views, adds an `organization_id` filter to every view in the query, caps results at 500 rows. Isolation suite 17/17. | [`semantic/cube.js`](../semantic/cube.js), PHASE1_SIGNOFF section 1 |
| Views live | `pnl`, `balance_sheet`, `sales`, `purchases`, `receivables`, `payables`, `vat`, `ledger`, `inventory`, `bank_accounts`, `bank_transactions`, `payments`, `payment_settlements`, `organization_directory` | `semantic/model/views/` |
| Dimensions | Sales and purchases slice by branch, project, cost centre, customer or supplier, and product | `sales.yml`, `purchases.yml` |
| Rollups | **None.** `docker-compose.cube.yml` still says "Phase 3 (rollups): uncomment". | `semantic/` |
| Query speed | Warm P&L query about 70 ms after `idx_journal_entries_org_date_posted`. A cold, wide query once exceeded the 20 s Cube timeout. | Guide section 13 |
| AI assistant | The agent answers every user since 8 Oct (`AGENT_MODE=primary`). Best measured run: 86/89 real questions, p95 17.6 s. The language model, not Cube, sets the latency. | PHASE2_AGENT section 6 |
| Cash forecast | Built: `cash_forecast.py`, 13 weeks by default (26 maximum), from cash balance, open invoices and open bills. | [`cash_forecast.py`](../backend/src/gemini_brain/semantic/cash_forecast.py) |
| Old AI path | `sql_fallback/`, the endpoint selector and the regex router are still in the code. The plan is to delete them 1–2 weeks after cutover. | Guide section 12.3 |
| Finance sign-off | **Open.** Decision 1 (ledger or documents as the official revenue source) and decision 3 (cancelled invoices) are not signed. | PHASE1_SIGNOFF section 2 |
| Accutax tenant defect | **Open.** 11 methods in `report.service.ts` ignore `organization_id`. | Review section 4 |

---

## 3. What Cube is good for here, and what it is not

| Use | Fit | Why |
|---|---|---|
| Totals, trends, rankings, comparisons across periods or organizations | Good | This is what a semantic layer is for. One query covers up to 25 organizations. |
| Point-in-time balances (balance sheet, AR, AP) | Good, if modelled carefully | Balances are semi-additive. They must be cumulative to a date, never summed across dates. `balance_sheet` already does this. |
| Ratios that mix balances and flows (DSO, DPO, quick ratio) | Good, computed in code | Take the balance as of a date and the flow over a period from two governed queries. Combine them in Python, as `query_metrics.summarize` already does for totals. |
| Dashboard cards and charts | Good | They are aggregates. They gain the most from one definition. |
| Ledger detail, statements of account, running balances | Poor | Row-level, ordered, paginated output. The 500-row cap is deliberate. |
| PDF and Excel exports of long reports | Poor | Same reason. Keep them in NestJS SQL. Cube can supply the summary totals on the first page. |
| Group consolidation | Not on its own | Showing organizations side by side is easy. Real consolidation also needs currency translation and intercompany elimination, which is accounting logic, not a query. |
| Writes, workflow, posting | No | Cube is read-only by design. |

---

## 4. Rules for every workstream

1. **Gate on measurement.** No workstream starts until the one it depends on has met its exit check. No performance or accuracy number goes into a customer or management document until it has been measured on the VM.
2. **Shadow before switch.** Any consumer moving to Cube (agent, dashboard card, report) runs both paths side by side first and logs every difference. `shadow.py` and the guide's four difference categories (guide section 12.2) are the template.
3. **Delete only after parity.** Old code is removed only after its replacement has run in production through one month-end close with no unexplained differences.
4. **Tenant scope comes from code.** No model, user or client ever sets `organization_id`. Every new consumer gets its token from a service that has already checked the user's access.
5. **Fix data at the source.** When Cube exposes a data defect, fix it in Accutax. Do not add a workaround to the Cube model.

---

## 5. Roadmap

The workstreams are listed in dependency order. A and B can run in parallel. C depends on A. D and E depend on B and C. F items depend on A and, where they read rolled-up data, on C.

### A. Finish the AI migration

**Work**
- Get finance to sign PHASE1_SIGNOFF decisions 1–6. The agent already answers from Cube, so this sign-off is overdue.
- Review the shadow logs to about 14 Oct.
- Delete the old path in the order in guide section 12.3. Move the shared database connection and read-only checks first, or login and chat history break.
- Run the 89-question replay three times per change, as PHASE2_AGENT recommends.

**Exit check:** sign-off recorded; three replay runs at or above 86/89 with zero cross-tenant results; `sql_fallback/` removed and the unit and isolation suites passing.

### B. Fix the Accutax tenant defect (not a Cube task)

**Work:** filter by `organization_id` in the 11 methods listed in review section 4 and add a test for each one. Owner: `accutax_bk_backend`. This is a security fix for the product today. It must not wait for any Cube migration.

**Exit check:** each method returns only the requested organization's data in a test with two organizations under one user and with `user_id` missing.

### C. Rollups and a read replica

Follow guide section 13, which already contains the rollup definition and sizing method.

**Work**
- Provision a Postgres read replica. Point Cube and its refresh worker at it.
- Measure the real compression ratio of `gl_lines` on the replica before choosing day or month granularity. The test orgs suggest about 40 million daily rows for all 66 million journal lines.
- Add one shared rollup per hot view (`pnl`/`gl_lines` first, then `sales`, `vat`). Each rollup must carry `organization_id` as a dimension, or the tenant filter added by `queryRewrite` stops it from matching.
- Agree the refresh window with finance so that it covers backdated postings at month-end close.
- Show "data as of" from `lastRefreshTime` in every answer.
- Fix the open item that Cube logs full JWTs on authentication errors.

**Exit check:** the guide's Phase 3 p95 targets met for one week (one view, one org, YTD ≤ 0.5 s; 10 orgs ≤ 1 s); `usedPreAggregations` confirms the hits; no load from the refresh worker on the primary.

### D. Dashboards through Cube, card by card

**Design**
- The browser never calls Cube. NestJS checks the user's role and organization access, mints a short-lived Cube token, and forwards the query. Role rules inside an organization stay in NestJS.
- Cube currently trusts one issuer (`gemini-brain`) with one HS256 secret. Adding NestJS as an issuer needs a decision: a shared secret with a distinct `iss`, or per-issuer keys with a custom `checkAuth`. Per-issuer keys are recommended, so one leaked secret does not expose both paths.
- NestJS should forward only queries from a fixed list per card, not arbitrary Cube queries from the browser.

**Work**
1. Pick two or three cards on `Dashboard.jsx` that are pure aggregates (for example revenue trend, top customers, VAT payable).
2. Decide each definition gap before migrating. Known gap: the Accutax P&L screen (`ProfitLossView.jsx`) leaves cost of sales out of net profit, so it will disagree with Cube by design.
3. Run old and new side by side, log differences, review, switch, then repeat for the next cards.

**Exit check per card:** two weeks of side-by-side data including one month-end, with every difference explained; p95 no worse than the old endpoint.

### E. Reports: move the aggregates, keep the detail

**Work:** classify every method in `report.service.ts` as aggregate (moves to Cube through NestJS) or detail (stays in SQL). Statements of account, ledger listings, running balances and long exports stay. Move one aggregate report at a time, with a parity test against the old method over 12 periods × the test orgs, as was done for P&L in Phase 1.

**Exit check per report:** parity test with zero unexplained differences, one month-end close in production, then delete the old method.

Do not set a target for lines deleted. The number of lines that move is an outcome of the classification, not a goal.

### F. Product features on top of the governed layer

These are product bets. Each one needs its own short spec with the customer problem, success measure and owner before engineering starts.

**F1. Variance explanations.** "Why did margin drop in Q3?" The agent queries the same measures for both periods, broken down by account category, cost centre or project. Code computes every difference and percentage. The model only writes the explanation and must cite the figures it was given. Most of this works today with `query_metrics`; the work is a dedicated comparison tool and tests against hand-checked cases.

**F2. Cash forecast scenarios.** The 13-week forecast is built. Next:
- What-if inputs (a one-off purchase, a recurring cost from a given week), applied in code.
- A stress case: what happens if the largest open invoices are paid late.
- State the assumptions in every answer. The forecast includes only issued invoices and bills; Accutax has no payroll or budget data, so recurring costs not yet billed are missing unless the user supplies them.

**F3. Ratios.** Working capital, quick ratio, DSO and DPO, computed in code from two governed queries each (section 3). Check definitions with finance before release.

**F4. UAE VAT return (Form VAT 201) support.** See section 6. This needs a UAE tax adviser and data checks before any build.

**F5. Proactive alerts.** A scheduled job evaluates a small set of rules per organization (spend above its rolling baseline, large overdue balances, margin below a threshold). It needs rollups (workstream C) so it does not scan the primary database nightly. Start in-app only, with a per-organization opt-in and a threshold the customer controls, then measure how many alerts are acted on before adding email or WhatsApp. Noisy alerts get switched off and damage trust.

**F6. MCP server.** See section 7.

---

## 6. VAT return support (Form VAT 201)

**Correct box structure** (confirm against the current FTA EmaraTax form with a tax adviser):

| Box | Content |
|---|---|
| 1a–1g | Standard-rated supplies, by emirate (amount and VAT) |
| 2 | Tax refunds provided to tourists |
| 3 | Supplies subject to the reverse charge |
| 4 | Zero-rated supplies |
| 5 | Exempt supplies |
| 6, 7 | Goods imported into the UAE, and adjustments to them |
| 8 | Total of output boxes |
| 9 | Standard-rated expenses |
| 10 | Supplies subject to the reverse charge (input side) |
| 11 | Total of input boxes |
| 12 | Total value of due tax |
| 13 | Total value of recoverable tax |
| 14 | Payable tax for the period (Box 12 − Box 13) |

**Data checks before building**
- The Cube model has no emirate dimension. Boxes 1a–1g need the emirate of the establishment that made each supply. Check whether Accutax records it, for example on branches. If it does not, this is an Accutax data change first.
- Check whether Accutax records reverse-charge, import and tourist-refund treatment per line. Today `vat_lines` has output VAT, input VAT, net VAT and standard-rated sales.

**Pre-filing checks to consider** (rules to confirm with the adviser):
- Tax invoices to VAT-registered customers that lack the customer's TRN, or carry a TRN that is not 15 digits.
- Input VAT claimed on expense types where recovery is blocked (for example entertainment, and motor vehicles available for personal use, under Article 53 of the Executive Regulation, Cabinet Decision No. 52 of 2017).
- The VAT control account balance in the ledger compared with the sum of VAT on documents for the period.

**Positioning:** these are checks that help the accountant find problems before filing. The product must not promise compliance or zero penalties. The filer remains responsible for the return.

---

## 7. MCP access for external AI tools

- **Cube's own MCP server is a Cube Cloud feature (Premium and Enterprise plans), not part of Cube Core.** It is not available to us on our self-hosted deployment. ([Cube docs](https://cube.dev/docs/product/apis-integrations/mcp-server), [cube-mcp-server](https://github.com/cubedevinc/cube-mcp-server))
- Community MCP servers for self-hosted Cube exist, but they expose raw Cube queries. That breaks rule 4: the client would need a Cube token directly.
- **Recommended:** build our own Accutax MCP server that exposes the same governed tools the agent uses (`query_metrics`, `list_documents`, `cash_forecast`). This is what review section 6.8 proposed. The server authenticates the user with OAuth, resolves their organizations with the same `authorize_org_scope` logic, and mints the short-lived Cube token itself.
- Before launch: a security review, per-client rate limits, an audit log of every tool call, and a way for an organization admin to revoke access.

---

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Finance sign-off stays open | The AI answers from definitions finance has not approved. Revenue may be overstated by cancelled invoices still posted in the ledger. | Treat workstream A sign-off as the first priority. |
| Data defects at the source | One definition spreads wrong numbers everywhere at once. | Rule 5. Track the cancelled-invoice and P&L-screen defects with the Accutax owner. |
| Rollups too large or too stale | High memory and disk use; month-end postings missing from answers. | Measure on the replica first; agree the refresh window with finance; show "data as of". |
| Second token issuer (NestJS) | A wider attack surface for Cube. | Per-issuer keys, fixed query lists per card, isolation suite extended to the NestJS path. |
| Shared production database | Refresh worker and dashboard traffic slow every customer. | Read replica before rollups and before dashboards move. |
| Team capacity | Seven parallel initiatives stall each other. | Run in the dependency order of section 5. Start F items only with an owner and a spec. |

---

## 9. What changed from the first version

| First version said | Why it changed |
|---|---|
| Report queries run in under 50 ms; multi-org P&L in under 0.4 s | No rollups exist. Targets now come from guide section 13 and are to be measured. |
| Query accuracy jumps to 95%+ in weeks 1–2 | The measured result is 86/89 on real questions. Accuracy is now an exit check, not a forecast. |
| Delete `sql_fallback/` completely in weeks 1–2 | Deletion now follows guide section 12.3, after parity, with shared code moved first. |
| The frontend calls Cube's REST API directly | The browser would skip NestJS role checks. Calls now go through NestJS. |
| Tenant defects in 11 endpoints are solved in Phase 4 | This is a security fix needed now, owned by the Accutax backend (workstream B). |
| 100-page ledger exports become instant | Cube caps results at 500 rows by design and suits aggregates, not detail. |
| Consolidated group P&L with one query | Side-by-side figures are not consolidation. Currency translation and intercompany elimination are out of scope until specified. |
| Box 12 = Box 1 tax + Box 3 tax − Box 9 tax | Box 12 is total due tax; payable is Box 14 = Box 12 − Box 13. Emirate data is not modelled yet. |
| Zero-penalty compliance guarantee | Removed. The product offers pre-filing checks; the filer stays responsible. |
| Enable Cube Core's native MCP server | Cube's MCP server is a Cube Cloud feature. We build our own over the governed tools. |
| Fixed weeks per phase | Replaced by dependency order and exit checks. |
| Claude 3.5 Sonnet | Outdated. Model choice follows PHASE2_AGENT (`AGENT_ANSWER_MODEL_ID`). |

---

## 10. Decisions needed

1. **Finance:** sign PHASE1_SIGNOFF decisions 1–6.
2. **Accutax backend owner:** schedule workstream B, and decide how cancelled posted invoices are handled (PHASE1_SIGNOFF decision 3).
3. **DBA / infrastructure:** approve and provision the read replica (workstream C).
4. **Security:** approve the second token issuer design (workstream D) and the MCP authentication design (section 7).
5. **Product:** choose which F items to spec first, and name an owner for each.
