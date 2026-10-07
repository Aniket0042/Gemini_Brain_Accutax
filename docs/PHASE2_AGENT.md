# Phase 2: the tool-using agent

Date: 7 October 2026
Status: **in shadow on the VM** (`AGENT_MODE=shadow` since 7 Oct; users still get the current answer path). Law routing and latency work are done (section 6) and evaluated, but not yet deployed.
Context: [`ai-architecture-review-2026-10.md`](./ai-architecture-review-2026-10.md) sections 6.2–6.3 and 7, and [`CUBE_CORE_INTEGRATION_GUIDE_V2.md`](./CUBE_CORE_INTEGRATION_GUIDE_V2.md) sections 10 and 12.1.

---

## 1. What was built

One model (Claude Sonnet 5, `in.` profile) plans, calls tools and writes the answer. It works within hard limits: 6 tool calls and 45 seconds per question. Tenant scope comes from the request. The model can narrow the selected organizations (`organization_ids`) but never widen them.

| Tool | Answers | Source |
|---|---|---|
| `query_metrics` | Totals, comparisons, rankings, thresholds, trends (new `granularity`: week, month, quarter, year) | Cube views (ledger P&L and balance sheet; documents for sales, purchases, VAT, receivables, payables) |
| `list_documents` (new) | Which invoices or bills: largest, oldest, overdue more than N days, by weekday | The same Cube views, one row per document |
| `search_vat_kb` | UAE VAT and e-invoicing law, with numbered FTA citations | The existing VAT knowledge base |
| `app_guide` | How to do something in the Accutax app | The existing user guide; "no match" means the agent says so |

| File | Purpose |
|---|---|
| [`agent/loop.py`](../backend/src/gemini_brain/agent/loop.py) | The bounded loop. If an answer comes back cut off, the model is asked once for a shorter one. The verifier checks every figure against the tool results (reported, not yet enforced) |
| [`agent/tools.py`](../backend/src/gemini_brain/agent/tools.py) | Tool definitions, scope narrowing, error handling. A bad call goes back to the model; a Cube failure becomes "figures are temporarily unavailable"; nothing raises |
| [`agent/prompt.py`](../backend/src/gemini_brain/agent/prompt.py) | Rules: figures only from tools, shown with their sign; no period means year to date; plain refusals for what the data does not hold; law only from the knowledge base; tables of at most 15 rows |
| [`agent/shadow.py`](../backend/src/gemini_brain/agent/shadow.py) | `AGENT_MODE=shadow`: after the current answer is final, the agent answers the same question in the background, and both answers go to `logs/agent_shadow.jsonl`. At most two run at once, and organization names are looked up off the request path |
| [`semantic/list_documents.py`](../backend/src/gemini_brain/semantic/list_documents.py) | Document lists over the governed views |
| Cube model | `document_number` and `document_weekday` added to the `sales` and `purchases` views |
| [`scripts/eval/replay_real_questions.py`](../backend/scripts/eval/replay_real_questions.py) | Replays the real-question set through either path (`--path current` or `--path agent`) |

Settings (`config/settings.py`): `AGENT_MODE`, `AGENT_MODEL_ID`, `AGENT_MAX_TOOL_CALLS`, `AGENT_DEADLINE_SECONDS`, `AGENT_SHADOW_LOG`.

---

## 2. How it was measured

- **Questions:** [`tests/data/real_questions.json`](../backend/tests/data/real_questions.json) has 89 cases. Each is one distinct question from the chat history (27 Sep – 6 Oct 2026, test organizations 24–33), with its follow-up context, a question type, and reference Cube queries. Unit-test traffic (org 69) and near-duplicate wordings are excluded.
- **Runs:** both paths ran on the VM against the same database, Cube and Bedrock, on 7 Oct 2026. Follow-ups replay inside their conversation. Thread memory is kept in the process, so nothing is written to the chat tables.
- **Grading:** every answer was read and graded by hand against the reference figures.
  - **Pass:** answers the question that was asked, with correct figures, period and scope. For a question the data cannot answer, it says so.
  - **Partial:** right data, but part of the question is missing or the agent asked instead of answering.
  - **Fail:** wrong figures, wrong question answered, an invented answer, or no answer.
  - A figure counts as correct on either basis: documents for the current path, the ledger for the agent. Which basis is official is decision 1 in [`PHASE1_SIGNOFF.md`](./PHASE1_SIGNOFF.md).

---

## 3. Results (VM, 7 Oct 2026)

| | Current path | Agent, run 1 | Agent, final run |
|---|---|---|---|
| Pass | 60 / 89 (67%) | 83 / 89 (93%) | **84 / 89 (94%)** |
| Partial | 9 | 4 | 2 |
| Fail | 20 | 2 | 3 |
| Data questions only (70) | 41 pass | 64 pass | 67 pass |
| VAT law questions (19) | 19 pass | 19 pass | 17 pass |
| Median / 95th-percentile time | 0.8 s / 11 s | 10 s / 32 s | 10 s / 36 s |
| Cost per question | not measured (the harness does not see all of the current path's model calls) | about $0.047 | about $0.051 |

Between the two runs the prompt gained four rules: show signs as returned, no period means year to date, law facts only from the knowledge base, and tables of at most 15 rows. Trends also got a default of 300 rows, and a cut-off answer is now asked once for a shorter one.

### What the agent fixes (26 cases the current path got wrong)

- **Figures the current path cannot give:** gross margin, working capital, fixed assets, current liabilities and other income. The current path says "not available"; the ledger has them.
- **Single-organization answers:** "List invoices for this year" (org 27) now gives 1,386 invoices and AED 12.53M; the current path said "this year (2025)" and found one zero-value invoice. Monthly P&L with cost of sales now works.
- **Periods:** "growth vs last year" compared 2025 with 2024; "quarter on quarter" compared 1–7 Oct with the period before. The agent compares 2026 YTD with the same days of 2025, and Q3 with Q2.
- **Follow-ups:** "show the overdue invoices count", "why is the second one so low?" and "drop Org1 and redo" now keep their context. The agent explains a low figure with the monthly postings.
- **Wrong question answered:** "Compare VAT payable for Q2 2026" got a law explanation; it now gets the figures. "Any org missing a TRN?" got invented app steps; the agent says TRNs are not in its data. "Org A vs Org B" now asks which organizations.
- **Record questions:** weekend invoices, top bills and invoices overdue more than 180 days now come from `list_documents`, with counts and totals that match Cube.
- **Mixed questions:** revenue plus overdue count; "summary of bills and invoices"; "summarize each company in 3 bullets"; "are any companies in trouble" now names the eight organizations with negative equity and a current ratio below 1.

### What is still wrong

- **Run-to-run variation.** The cases that fail differ between runs. The total stays at 83–84, but **2 of the 19 VAT law answers changed** between runs:
  - r128 (company phones) said "No" in run 1 and "Yes, provided..." in the final run.
  - r131 (AED 8,000 error) opened with "a voluntary disclosure is required" before correcting itself.
  - The current path answered both correctly in its one run.
- **Latency.** The 95th percentile is 36 s against a target of 10 s. Every answer needs at least two Sonnet 5 calls, plus a third for long tables. Law answers take 7–40 s.
- **Data, not the agent:**
  - Ledger revenue is negative for the seed organizations 26–33. The agent shows it as returned and flags it, but a client would read it as broken (decision 6).
  - Org 24 shows a 100% margin because it has no expense postings.
- **Fixed after the final run, re-checked on the affected cases only:**
  - "Weekend" is now Saturday–Sunday; the model had assumed Friday–Saturday.
  - The model is told to answer, not call more tools, when its time or tool budget runs low. This fixes the payments question (r090), which had timed out.

### Exit criteria (guide section 12.1, Phase 2)

| Criterion | State |
|---|---|
| Better than the current path on accuracy | **Met** on this set: 84 vs 60 of 89 |
| Better on 95th-percentile time | **Not met**: 36 s vs 11 s. After section 6: 17.6 s |
| Better on cost | Not measured for the current path. Agent: $0.051 a question, $0.021 after section 6 |
| Zero cross-tenant results | Held: scope is set by code, narrowing is tested, and Cube's tenant guard applies (isolation suite 17/17) |
| Zero silently dropped conditions | Held on this set ("margin above 20%", "older than 180 days", "lowest first", "weekends") |
| At least 95% exact figures | 94% overall on the final run; 97% after section 6 (86 pass, 3 partial, 0 fail) |

---

## 4. Recommendation

1. **Run the agent in shadow on the VM** (`AGENT_MODE=shadow`) for a week next to the current path, as for Cube. Review `logs/agent_shadow.jsonl` the same way.
2. ~~Keep VAT law questions on the current knowledge-base path~~ Done (section 6).
3. ~~Latency work~~ Partly done (section 6): 95th percentile 36 s down to 17.6 s. Still open: stream the answer to the UI, so the first words show after about 3 s.
4. **Run the replay three times per change** and count a case as passing only when all three runs pass. Add expected answers for the two law cases that flipped.
5. **Switch users over only after** the Phase 1 sign-offs (ledger vs documents, cancelled invoices, seed data) and a clean shadow week.

---

## 6. Law routing and latency work (7 Oct 2026)

### What changed

| Change | Why |
|---|---|
| **Law route** ([`agent/law.py`](../backend/src/gemini_brain/agent/law.py)): a UAE VAT law question gets the existing knowledge-base answer, with the same detector, the same confident-match search and the same prompt as the current path. That is one model call and no tools. A question about the organizations' own figures ("VAT for each organization", "compare tax payable") never takes this route | Law answers varied between agent runs and took 7–40 s |
| **Prompt caching** of the system prompt and tool definitions (about 6,700 tokens), inside the agent only. The app-wide model list does not name Sonnet 5 or Haiku 4.5, so the current path is unchanged | Every model call re-read the same 6,700 tokens |
| **Parallel tool calls**: one turn's calls run at the same time, and the prompt asks for every needed call in one turn | A second round of tool calls costs a whole extra model call |
| **Figures worked out in code**: `query_metrics` returns `totals` (single currency, not for capped results) and, for trends, each organization's total, best and worst period. The prompt says to copy them and to write "n/m" for growth on a zero or negative base | Sums and best/worst picks were done by the model |
| **Shorter answers**: at most 120 words outside tables, at most two notes | About 2 s per 100 output tokens |
| **Answer model option** `AGENT_ANSWER_MODEL_ID` (default: the planner, Sonnet 5) | Tested with Haiku 4.5; see below |

### Results (89 real questions, VM)

| Run | Pass | Median | 95th percentile | Slowest | Cost (89 questions) |
|---|---|---|---|---|---|
| Before (final run, section 3) | 84 | 10.2 s | 36.2 s | 46.8 s | $4.58 |
| A: Sonnet plans and writes | not graded in full | 7.5 s | 25.6 s | 48.5 s | $1.95 |
| B: Sonnet plans, Haiku writes | about 83 (data answers skim-graded) | 5.3 s | 10.8 s | 12.9 s | $1.22 |
| C: as B, plus figures worked out in code | about 81 (skim-graded) | 5.4 s | 10.8 s | 14.1 s | $1.26 |
| **D: as A, plus figures worked out in code** | **86** (3 partial, 0 fail) | **7.5 s** | **17.6 s** | 35.2 s | **$1.90** |

- **Law answers:** 19 of 19 correct in every run since the change. They take 3.2 s typically and 5–6 s at the 95th percentile. The two that varied before (r128, r131) now give the correct answer each time.
- **Haiku as the writer** halves the time but gets reasoning wrong. Examples:
  - "six companies in trouble" when the data shows eight;
  - "both show positive growth" for revenue down 38% and 41%;
  - counts read off a capped 100-row list (1,881 overdue invoices became 28);
  - a combined cash total off by AED 385k in run B, fixed once totals came from code.

  It stays off.
- **Run D's three partials:**
  - r047 asks which fiscal year "FY 2025-26" means instead of stating an assumption.
  - r088 claims no duplicate invoice numbers from a capped list.
  - r032 quoted a total of a capped result. That one is fixed after the run: no totals are returned for capped results.

### Still open

- **Streaming.** The answer arrives only when complete. Streaming the writing call would put the first words on screen at about 3 s for every question.
- **The slowest questions are analysis questions** that need four tool calls and long answers ("are any of my companies in trouble", "summarise each company"): 20–35 s.
- **Re-run with three repeats** before cutover, as recommended in section 4.

---

## 7. Closing the coverage gaps (8 Oct 2026)

The current path covers some topics with Accutax REST endpoints, and the agent could not
answer them yet. Each gap is now handled as follows.

| Topic | How the agent answers it | State |
|---|---|---|
| General ledger, journal entries, account activity | New Cube view `ledger` (debit, credit, net movement per account, source or journal). New `list_documents` type `journal_lines`. | Built. Needs the Cube model deployed (`semantic/deploy/install_rootless.sh --start`). |
| Cash forecast | New tool `cash_forecast`. Code computes the weekly closing cash: opening ledger cash, plus open invoices, minus open bills, each in the week it falls due. Overdue amounts, undated amounts and amounts due after the forecast period are reported separately. | Built. Tested live against Cube on the VM. |
| PDF, Excel, CSV and charts | Agent answers build files with the same `attach_delivery` as the current path, from the largest figure result. The prompt tells the model the file is attached. | Built. Tested live: chart, table, PDF and CSV. |
| Projects and cost centres | Already in the `sales` and `purchases` views. | No change needed. |
| Inventory, bank accounts and transactions, branch names | Cube model written in `semantic/model_pending/`. | Waiting on the DBA. The `cube_reader` role cannot read the tables yet; the grant script is `semantic/dba/cube_reader_grants_inventory_bank.sql`. The steps after the grant are in `semantic/model_pending/README.md`. |

The REST endpoints were tested and not used:

- Accutax takes the organization from the user's token before the `organization_id`
  parameter. In a multi-organization chat, a REST call could return one organization's
  data under another organization's name.
- Several endpoints did not answer within 8–15 s:
  - `/report/cash-forecast`;
  - `/report/profit-loss-by-branch`, `-by-cost-center` and `-by-project`;
  - `/report/cash-flow-indirect`.
- `/accounting/general-ledger` returned nothing for test organizations that do have
  ledger postings.

---

## 5. How to run

```bash
# from backend/, on a host that reaches Cube (the VM); the current path, then the agent
python scripts/eval/replay_real_questions.py --cases tests/data/real_questions.json --out /tmp/replay
python scripts/eval/replay_real_questions.py --path agent --cases tests/data/real_questions.json --out /tmp/replay
```

Each run writes a JSON report and a Markdown sheet with every answer, the tool calls, and the reference figures from Cube.

---

## Appendix: grades per case

Pass unless shown otherwise. "Current path" is the replay of the answer users get today, run on 7 Oct 2026 after the Phase 0 fixes.

| Case | Type | Question | Current path | Agent (final run) |
|---|---|---|---|---|
| r006 | metric | Compare revenue this year | PASS | PASS |
| r007 | followup | what about last quarter | PASS | PASS |
| r008 | analysis | can you compare organization Professional & Consulting Services_User1_Org4  and Profess... | PASS | PASS |
| r011 | record_list | List invoices for this year | PASS | PASS |
| r012 | record_list | List invoices for this year | **FAIL**: single org: says 'this year (2025)', finds one zero-value invoice; Cube has 1,386 invoices, 12.53M | PASS |
| r013 | metric | Compare total revenue this year | PASS | PASS |
| r018 | record_list | List vendors with overdue bills in each organization | PASS | PASS |
| r019 | metric | Compare total revenue for this year | PASS | PASS |
| r020 | metric | Which organization has higher outstanding receivables? | PASS | PASS |
| r025 | metric | Rank the organizations by total expenses | PASS | PASS |
| r026 | metric | Which organization has the highest cash balance? | PASS | PASS |
| r030 | metric | rank organization by total expenses | PASS | PASS |
| r032 | record_list | list vendors with overdue invoices in each organizations | PASS | PASS |
| r034 | metric | rank the top 5 orgnanization by thier revenue and also tell me the overdue invoices eac... | **PARTIAL**: overdue amount shown, overdue count asked but missing | PASS |
| r035 | followup | what about revenue you are not showing the revenue | PASS | PASS |
| r036 | metric | rank top 5 org based on their revenue | PASS | PASS |
| r037 | metric | Compare revenue, expenses and cash this year | PASS | PASS |
| r038 | metric | rank top 5 organization based on their revenue and also show me the overdue invoices co... | **PARTIAL**: overdue count asked but missing | PASS |
| r039 | followup | show me the ooverdue invoices count | **FAIL**: follow-up 'show the overdue invoices count': recomputed revenue for January 2026, still no count | PASS |
| r040 | analysis | give me summary for each organizations with respect to their bills and invoices | **FAIL**: asked for bills and invoices; got revenue/profit/growth/equity, no bill or invoice figures | PASS |
| r041 | metric | Compare the balance sheet | PASS | PASS |
| r042 | metric | Which org has the best profit margin? | PASS | PASS |
| r043 | growth | "Revenue growth vs last year" | **FAIL**: 'growth vs last year' compared 2025 with 2024 instead of 2026 YTD with 2025 | PASS |
| r044 | series | revenue by month for each organization | PASS | PASS |
| r045 | series | Quarterly profit trend | PASS | PASS |
| r046 | metric | Compare revenue, expenses, net profit and cash for all selected orgs this year | PASS | PASS |
| r047 | metric | Show me the P&L for each entity for FY 2025-26 | PASS | PASS |
| r048 | series | Generate our profit and loss statement for this fiscal year, broken down by month, incl... | **FAIL**: single org: 'monthly breakdown is not available'; no cost of sales | PASS |
| r049 | metric | What is the combined cash position across all entities? | PASS | PASS |
| r050 | growth | Compare revenue this year vs last year for each org | PASS | PASS |
| r051 | metric | Which entity burns the most cash? Rank by expenses last quarter, lowest first | PASS | PASS |
| r053 | growth | Revenue and profit growth quarter on quarter | **FAIL**: 'quarter on quarter' compared 1-7 Oct with the previous period: -100% for five orgs | PASS |
| r054 | series | Monthly net profit trend for each org since January 2025 | PASS | PASS |
| r056 | metric | Compare gross margin | **FAIL**: says gross margin is not available; the ledger has cost-of-sales accounts (Cube answers) | PASS |
| r057 | unsupported | Compare EBITDA | PASS | PASS |
| r058 | metric | Compare working capital | **FAIL**: says working capital is not available (Cube answers) | PASS |
| r059 | metric | Compare other income | **FAIL**: says other income is not available (Cube answers) | PASS |
| r060 | metric | Compare fixed assets | **FAIL**: says fixed assets are not available (Cube answers) | PASS |
| r061 | metric | Compare current liabilities | **FAIL**: says current liabilities are not available (Cube answers) | PASS |
| r062 | unsupported | Cash flow comparison this year | PASS | PASS |
| r063 | analysis | Which of my companies is performing best overall? | **PARTIAL**: 7-metric dump, no criteria or verdict | PASS |
| r064 | metric | Rank top 5 organizations by revenue and also show overdue invoices count and amount | **PARTIAL**: overdue count missing | PASS |
| r065 | analysis | Which business should I invest more in based on growth and margin? | PASS | PASS |
| r066 | analysis | Summarize each company in 3 bullets | **PARTIAL**: 7-metric dump instead of 3 bullets per company | PASS |
| r067 | analysis | Are any of my companies in trouble? | **PARTIAL**: metric dump with warning signs; no direct answer | PASS |
| r068 | metric | Compare net worth | PASS | PASS |
| r069 | metric | Who is most profitable and who has the most overdue receivables? | PASS | PASS |
| r071 | metric | Compare VAT payable for Q2 2026 | **FAIL**: 'Compare VAT payable for Q2 2026' answered with VAT law, no figures | PASS |
| r072 | metric | What is VAT for each organization? | **FAIL**: 'VAT for each organization' answered with VAT law, no figures | PASS |
| r073 | kb | What is VAT? | PASS | PASS |
| r074 | metric | Compare tax payable | **PARTIAL**: net VAT payable incl. drafts; does not say corporate tax is not tracked | PASS |
| r075 | metric | Input VAT vs output VAT per entity this year | PASS | PASS |
| r076 | unsupported | Which entities have VAT return due this month? | **FAIL**: answered net VAT payable for 1-7 Oct ('nothing recorded') instead of saying due dates are not held | PASS |
| r077 | org_info | Any org missing a TRN? | **FAIL**: app how-to steps that look invented (Contacts > Organizations > TRN filter) | PASS |
| r078 | unsupported | Compare corporate tax liability FY 2025 | PASS | PASS |
| r079 | clarify | Explain why Org A margin lower than Org B | **FAIL**: should ask which orgs; gave a margin ranking | PASS |
| r080 | metric | Compare revenue this year | PASS | PASS |
| r081 | followup | and last quarter ? | PASS | PASS |
| r082 | followup | now only show the top 2 | PASS | PASS |
| r083 | followup | why is the second one so low? | **FAIL**: 'why is the second one so low?': answered July revenue for one org, no explanation | PASS |
| r084 | followup | drop  Agriculture & Forestry_User1_Org1 and redo | **FAIL**: 'drop Org1 and redo': period changed to July, different ranking | PASS |
| r085 | record_list | Vendors common to all orgs paid more than 100,000 AED | PASS | PASS |
| r086 | record_list | Top 10 largest bills overall | PASS | PASS |
| r087 | record_list | List every unpaid invoice older than 180 days per org | PASS | PASS |
| r088 | record_list | Duplicate invoice numbers across organizations | **PARTIAL**: honest 'not available', but the data can answer it | **PARTIAL**: claims no duplicates from a 100-row sample |
| r089 | record_list | List invoices posted on weekends in each org | **PARTIAL**: lists all invoices; admits at the end it did not filter weekends | **PARTIAL**: took Friday and Saturday as the UAE weekend |
| r090 | unsupported | Which vendors got paid by more than one entity in the same week? | PASS | **FAIL**: tried to rebuild payments from many document lists; ran out of time |
| r091 | metric | Which orgs have bank accounts in more than one currency? | **FAIL**: 'no records found for any organization'; every org has AED bank accounts | PASS |
| r092 | unsupported | Which entity has the most employees / payroll cost? | PASS | PASS |
| r100 | series | Generate our profit and loss statement for this fiscal year, broken down by month, incl... | **FAIL**: single org: no monthly breakdown | PASS |
| r103 | kb | What changed in UAE VAT from 1 January 2026 under Federal Decree-Law No. 16 of 2025? | PASS | PASS |
| r104 | kb | What checks must a business carry out on its suppliers so its input tax is not denied? | PASS | PASS |
| r105 | kb | How do I value a deemed supply of services? | PASS | PASS |
| r112 | kb | What are the current late payment penalties for VAT? | PASS | PASS |
| r121 | kb | We receive payment in USDT. How do we convert it to AED for VAT? | PASS | PASS |
| r122 | kb | A company leaves our VAT tax group mid-year. What output tax and input tax adjustments ... | PASS | PASS |
| r124 | kb | A mainland company buys goods from another company in a designated zone and has them sh... | PASS | PASS |
| r125 | kb | We sell used cars that we bought from individuals. Can we use the profit margin scheme?... | PASS | PASS |
| r126 | kb | A UAE national builds a villa and rents one floor out. Can they still claim the new-res... | PASS | PASS |
| r127 | kb | We bought an entire business, including staff and stock. Do we charge or reclaim VAT on... | PASS | PASS |
| r128 | kb | Our employees get company mobile phones with unlimited data, and they also use them per... | PASS | **FAIL**: 'Yes, provided...': opposite of the first agent run and the current path |
| r129 | kb | Taxable supplies AED 6,000,000, exempt supplies AED 4,000,000, residual input tax AED 1... | PASS | PASS |
| r130 | kb | An invoice for AED 105,000 including VAT; the customer paid 40%, and the invoice is now... | PASS | PASS |
| r131 | kb | We found an error in last quarter's return that understated tax by AED 8,000. Voluntary... | PASS | **FAIL**: headline says a voluntary disclosure is required (AED 8,000 is under the threshold) |
| r133 | kb | When does e-invoicing become mandatory for a business with AED 60 million revenue, and ... | PASS | PASS |
| r134 | kb | What is the penalty for not issuing an e-invoice? | PASS | PASS |
| r135 | kb | what is the criteria for e-invoicing currently | PASS | PASS |
| r136 | kb | what is the flow of e-invoicing in UAE | PASS | PASS |
| r177 | metric | which of the organization have profit margin above 20% | PASS | PASS |
