# Reverse charge and the VAT 201 return in Accutax AI

* **Product:** `Gemini_Brain_Accutax` (Accutax AI)
* **Scope:** UAE VAT only (Federal Tax Authority). Saudi Arabia (ZATCA) is out of scope for now.
* **Status:** Version 2, rewritten 17 Nov 2026 after checking version 1 against the code, the backend and the shared database. Not started.
* **Version 1:** kept as `docs/REVERSE_CHARGE_AND_VAT_201_AI_SPECIFICATION.v1-original.md`. Section 5 lists what it got wrong.

---

## 1. Summary

**Can we build it? Yes, in three phases. Only the first phase can be built by the AI team alone.**

| Phase | What the user gets | Who builds it | Database change |
|---|---|---|---|
| A | AI finds bills that **may** need reverse charge, explains the rule, and drafts a VAT 201 that clearly marks what still has to be confirmed | AI team | None (Cube model change only) |
| B | The books can record reverse charge properly: currency, exchange rate, a reverse-charge tax code and both journal entries | Backend team + DBA | Yes |
| C | A VAT 201 from the books that is ready to file, with a downloadable PDF and Excel pack, reconciled to the ledger | AI team | None beyond phase B |

Why the split: the AI has read-only access to the books. The books don't hold the data a correct return needs today: no currency on bills, no reverse-charge tax code, almost no tax codes on bill lines (section 4.3). The AI can **find and explain** a missed reverse charge from what is there. It can't **book** one, and a return that doesn't match the ledger shouldn't be filed.

What makes this different (checked 17 Nov 2026 from public sources): reverse charge and a VAT 201 with Boxes 3 and 10 are **not new**. Wafeq, Zoho Books, Odoo and TallyPrime all let the user mark a bill as reverse charge by hand and then post both entries. AI that flags a missed reverse charge also exists, but in standalone or EU-focused tools (VATCalc, Spendflo, ScanCompta), not inside UAE accounting software. No public source showed Wafeq, Zoho (Zia), Xero or TallyPrime warning when the user forgets. Our angle is that combination: UAE rules, inside the books, reviewing bills already recorded, in chat. It is a differentiator, not a world first, and competitors could add it.

---

## 2. The rule in brief

All references below were checked against the FTA documents in our VAT knowledge base (`vat_kb`) on 17 Nov 2026.

### 2.1 What the law says

* **Reverse charge on imports.** "If the Taxable Person imports Concerned Goods or Concerned Services for the purposes of his Business, then he shall be treated as making a Taxable Supply to himself, and shall be responsible for accounting for the Due Tax." (Decree-Law No. 8 of 2017, Article 48(1)). The business declares the VAT as output tax and, where it is recoverable, claims it back as input tax.
* **Where it goes on the return.** Reverse-charge supplies are declared in **Box 3**, and the recoverable part is claimed in **Box 10**, not Box 9 (stated in FTA public clarification VATP047 on metal scrap; the same boxes are used for imported services).
* **Domestic reverse charge also exists.** Some supplies between UAE registrants use reverse charge too, for example metal scrap (VATP047). A rule that only looks for foreign suppliers will miss these.
* **Imported goods are different.** Output VAT on goods cleared through customs is **pre-filled in Box 6** from customs data. The business checks it against its records and corrects differences in **Box 7** (VATP045). Imported goods don't go in Box 3.
* **Exchange rate.** Foreign-currency amounts are converted to AED "according to the exchange rate approved by the Central Bank of the State at the date of supply" (Decree-Law Article 69).
* **Invoice.** A simplified tax invoice can't be used where reverse charge applies under Article 48. A full tax invoice under Article 59(1) is needed, unless the FTA has approved an administrative exception (Executive Regulation Article 59(5), as amended; VATP040).
* **Blocked input VAT.** Input VAT can't be recovered in cases including entertainment for people not employed by the business, motor vehicles available for personal use, and goods or services given free to employees for their personal benefit (Executive Regulation Article 53). Ordinary business travel isn't on that list, so a hotel bill isn't blocked automatically.
* **Box 1 by emirate.** Standard-rated sales are reported per emirate, in the emirate where the establishment most closely related to the supply is located (FTA clarification on emirates' reporting).

### 2.2 Worked example

A UAE business receives an invoice for USD 1,000 of software services from a supplier with no UAE establishment. The invoice shows no UAE VAT.

| Step | Figure |
|---|---|
| Convert at the Central Bank rate on the date of supply (USD is fixed at 3.6725) | AED 3,672.50 |
| Output VAT the business declares, 5% (Box 3) | AED 183.63 |
| Input VAT it can recover if the cost is fully recoverable (Box 10) | AED 183.63 |
| Net effect on VAT payable | AED 0.00 |

The journal: Dr Expense 3,672.50 / Cr Payable 3,672.50, and Dr Input VAT 183.63 / Cr Output VAT 183.63.

If the input VAT is blocked (for example, the service is entertainment for clients), Box 10 stays at zero and the AED 183.63 is a real cost.

### 2.3 What goes wrong in practice

1. **Reverse charge left out.** The bill shows no VAT, so the bookkeeper records it as zero-rated or out of scope. Box 3 and Box 10 stay empty. Even when the net effect is zero, this is an error on the return.
2. **Blocked VAT claimed.** Box 3 is declared but Box 10 claims VAT that Article 53 blocks.
3. **Foreign VAT claimed.** The overseas supplier charged its own country's VAT, and that is booked as UAE input VAT.
4. **Wrong invoice type.** A simplified invoice is accepted for a reverse-charge supply.
5. **Wrong rate.** A bank or card rate is used instead of the Central Bank rate on the date of supply.

---

## 3. The VAT 201 boxes, and whether Accutax holds the data

"Today" means what the AI can read from the books as of 17 Nov 2026.

| Box | What it is | Source in Accutax | Today |
|---|---|---|---|
| 1a–1g | Standard-rated supplies by emirate (amount, VAT, adjustment) | Sales lines + `organizations.emirate` (filled for all 7 emirates); `branches.state` for branch-level later | **Possible** at organization level |
| 2 | Tax refunds to tourists | Only for retailers in the tourist refund scheme; not held | **Not held**: show as not applicable unless the user says otherwise |
| 3 | Supplies subject to reverse charge (output) | No reverse-charge tax code exists | **Candidates only** (phase A); confirmed in phase C |
| 4 | Zero-rated supplies | Sales lines carry a tax code (`income_items.tax_rate_id`, filled in a 200,000-line sample) → `tax_rates.tax_type = zero_rated` | **Possible** |
| 5 | Exempt supplies | Same, `tax_type = exempt` | **Possible** |
| 6 | Goods imported into the UAE | Pre-filled by the FTA from customs | **Not ours to compute**: user enters from EmaraTax; we can only remind them to check it |
| 7 | Adjustments to Box 6 | Customs data not held | **Not held** |
| 8 | Totals of 1–7 | Sum | **Possible**, excluding boxes we don't hold |
| 9 | Standard-rated expenses (recoverable VAT, adjustments) | VAT on bills. Bill lines have almost no tax code, so this is "bills with VAT", not "standard-rated bills" | **Approximate** |
| 10 | Reverse-charge input VAT recovered | As Box 3 | **Candidates only** |
| 11 | Totals of 9–10 | Sum | **Approximate** |
| 12–14 | Due tax, recoverable tax, payable tax | From 8 and 11 | **Approximate**, labelled as a draft |

The return in Accutax is always labelled **"Draft prepared by Accutax from your books — not an FTA document"**. We don't copy FTA branding.

---

## 4. What exists today (verified 17 Nov 2026)

### 4.1 AI side (`Gemini_Brain_Accutax`)

| Asset | File | What it really does |
|---|---|---|
| VAT law answers | `vat_kb/` | Searches FTA documents, including Article 48, the reverse-charge clarifications and Article 53 |
| Uploaded bill reading | `uploads/extract.py` | Reads `reverse_charge_statement` and `currency` from an attached bill |
| Invoice rules | `uploads/invoice_rules.py` | `simplified_limit()` applies Article 59(5); `claim_hint()` already gives an Article 53 hint ("confirm who it was for"), for uploaded documents only |
| Currency conversion | `uploads/fx.py` | `rate_for(currency)` / `to_aed(amount, currency)`. The rates file has **only USD (fixed 3.6725)**. No dates, no history |
| VAT position | `vat_return/tool.py` | Output VAT, input VAT, net payable and standard-rated sales for the tax period, plus due date and penalties. Says plainly it isn't a filed return |
| VAT cube | `semantic/model/cubes/vat_lines.yml` | Output VAT from sales lines, input VAT from purchase lines. No reverse charge, no boxes |
| Bill review | `vat_review/anomalies.py` | 11 rules, including `vat_zero` (no VAT on an amount) and `supplier_trn_invalid` |
| Background watches | `bots/agents_vat.py`, `bots/watches.py` | `Watch(id, label, side, rules, default_on, desc, bar, kind)`. Existing VAT watches are `vat_rate_wrong`, `sales_vat_wrong`, digest, reminder and full watch |
| Reports | `artifacts/` (`generate.py`, `report_spec.py`) | Builds PDF/Excel/Word/PowerPoint reports |

### 4.2 Backend side (`accutax_bk_backend`)

* `vat_configs.reverse_charge` (JSON) exists. It's filled for **1 of 129** configs.
* A tax engine (`tax_calculations`, `tax_entries`) has an entry type `reverse_charge`. **Nothing outside its own module writes to it.** The database has 72 calculations and 9 entries, none of them reverse charge.
* `modules/report/tax-report.service.ts` (around line 726) builds a VAT return. It has two bugs:
  1. The reverse-charge filters require `entry_type` to equal `OUTPUT` **and** `REVERSE_CHARGE` at the same time, so those boxes are always zero.
  2. Its box numbers (1, 6, 7, 8, 11, 12) don't match the FTA form (1, 3, 4, 5, 9, 10), and total output VAT leaves out reverse charge.
* `tax_rates.tax_type` values: `standard`, `zero_rated`, `exempt`, `no_vat`, `out_of_scope`. **There is no reverse-charge type.** `is_recoverable` is `true` on all 200,354 rates, so nothing is ever marked as blocked.

### 4.3 The data (read-only queries on the shared database)

| Fact | Figure | What it means |
|---|---|---|
| Bill lines without a tax code (`expense_items.tax_rate_id` empty) | 21,334,852 of about 21.34 million | We can't tell zero-rated, exempt, out-of-scope or reverse charge apart on bills |
| Currency or exchange-rate column on `expense` (bills) or `income` (invoices) | None | Every amount is treated as AED. Foreign bills can't be detected from the bill itself |
| Contacts with a filled TRN | 2,004,394 of 2,004,403 | Almost all are filled… |
| …of which have 15 digits | **8** | …but nearly all are invalid. TRN is not usable as a signal |
| Contacts with a country (`contacts.country_id` → `country_type`) | All | About 1.0 million UAE (`AE`), 1.0 million India (`IN`). **Usable signal** |
| Contacts with a currency | 29 (all AED) | Not usable |
| Organizations with an emirate | All, spread evenly over the 7 emirates (plus 17 junk values) | Usable for Box 1 |
| Well-known overseas software vendors among bill suppliers | None | Test data has no realistic foreign suppliers. **We need a pilot organization with real data** |

Much of this is seed data (see the test organizations note). The structure findings (columns that don't exist) hold regardless.

---

## 5. What version 1 got wrong

| Version 1 said | What is true | Effect |
|---|---|---|
| Uses `aed_rate(currency, date)` with official daily Central Bank rates | The function doesn't exist; `fx.py` has `to_aed(amount, currency)` and only the fixed USD rate | Any code copied from v1 fails; non-USD bills can't be converted |
| Retrieval SQL on a `purchases` table with `currency` and `description` | No such table or columns; bills are `expense` + `expense_items`, with no currency | The query can't run |
| "TRN not 15 digits" means a foreign supplier | Only 8 of about 2 million contacts have a 15-digit TRN. A local supplier that isn't VAT-registered also has no TRN and is **not** reverse charge | Nearly every bill would be flagged |
| A fixed list of vendor names (AWS, Google, Microsoft…) means reverse charge | A name says nothing about where the supplying entity is or whether it charges UAE VAT; some large vendors bill UAE customers with UAE VAT | False positives; double-counted VAT if acted on |
| Keywords such as "hotel" or "restaurant" set recovery to 0% | Article 53 blocks specific cases (entertainment for non-employees, vehicles for personal use, free personal benefits to employees). Business travel isn't one of them | Wrongly blocked VAT. A keyword can only ask the user to confirm |
| The AI calculates Box 3 / Box 10 and the return is filed from that | The AI can't post journal entries (read-only). If the ledger doesn't hold the self-assessment, the return doesn't match the books | The return can't be reconciled to the ledger |
| Box 6 is computed by us from customs declarations | Box 6 is pre-filled by the FTA from customs; we only check it | Unneeded work, and no data for it anyway |
| Reverse charge only for foreign suppliers | Domestic reverse charge also exists (e.g. metal scrap, VATP047) | Missed cases |
| "No Article 53 validation" in the AI | `invoice_rules.claim_hint()` already gives an Article 53 hint for uploaded bills | Reuse it; don't rebuild it |
| Official FTA-branded PDF | We must not imitate FTA branding | The PDF is an Accutax draft |
| UAE and KSA (ZATCA) | Nothing in the code handles KSA | UAE only for now |
| Three weeks end to end | Phases B and C depend on backend and DBA work | Only phase A can be scheduled by the AI team |

The competitor comparison in v1 hasn't been re-checked and is left out here. It's still in the v1 file.

---

## 6. How it should work

### 6.1 Rules the design keeps

1. **The books are the source.** The AI never invents a figure. Every number comes from Cube or from a deterministic Python function, never from the model.
2. **Candidate is not confirmed.** Until the books hold a reverse-charge tax code, the AI says "may need reverse charge" and lists the evidence. It never adds candidates into Box 3/10 totals without the user confirming.
3. **Show the gap.** Where a box can't be filled from the books, the draft says so instead of showing 0.
4. **Article 53 is a question, not a verdict.** The AI asks who the cost was for or how the vehicle is used.

### 6.2 Signals for a reverse-charge candidate

| Signal | Strength | Available |
|---|---|---|
| Uploaded bill says reverse charge applies (`reverse_charge_statement = true`) | Strong | Now, for uploaded bills |
| Supplier country isn't UAE (`contacts.country_id` → `country_type.code <> 'AE'`) **and** the bill has no VAT **and** an amount | Medium | Now, after adding supplier country to the Cube |
| Bill in a foreign currency | Strong | Only after phase B adds currency |
| Supplier name on a list kept by finance | Weak, a hint only | Optional; the list is configuration, not code |
| Missing or invalid supplier TRN on its own | **Not a signal** | — |

A supplier outside the UAE that **did** charge VAT is a separate finding: "foreign VAT charged — don't claim it as UAE input VAT".

Domestic reverse-charge categories (such as metal scrap) need item or category data we don't hold reliably; they're out of scope until phase B adds a reverse-charge tax code.

---

## 7. Phase A — AI only, no database change

| # | Task | Files | Done when |
|---|---|---|---|
| A1 | Add supplier country to the purchase lines Cube (`LEFT JOIN country_type` on `contacts.country_id`; dimension `vendor_country_code`) | `semantic/model/cubes/purchase_lines.yml`, purchases view | Cube returns the country for bills; checked on one organization |
| A2 | New review rules: `rcm_candidate` (supplier outside UAE, no VAT, amount > 0) and `foreign_vat_charged` (supplier outside UAE, VAT recorded). Bills that match these no longer also fire `vat_zero`. Plain-English wording with the Article 48 and VATP047 citations | `vat_review/anomalies.py` (rule, severity, finding text), rule labels in `bots/agents_vat.py` | Unit tests cover UAE supplier with no VAT (no flag), foreign supplier with no VAT (flag), foreign with VAT (foreign-VAT finding), credit note sign |
| A3 | Watch `rcm_candidates` ("Foreign bills that may need reverse charge"), side `purchases`, rules `("rcm_candidate", "foreign_vat_charged")`, off by default until the pilot | `bots/agents_vat.py` | Shows in the agent builder and the preview |
| A4 | Draft VAT 201: `vat_return` returns a box structure. Box 1a–1g by organization emirate; Box 4/5 from sales tax codes; Box 9 from bills with VAT; Box 3/10 = 0 plus a separate candidate list with "if confirmed: Box 3 +X, Box 10 +Y, net change Z"; Box 2, 6, 7 marked "not in your books". Each box carries its source and a status (from books / approximate / not held) | `vat_return/tool.py`, `semantic/model/cubes/vat_lines.yml` (add `emirate` and a VAT category from `tax_rates.tax_type` for sales) | Unit tests on box arithmetic; figures match a hand calculation for one test organization |
| A5 | Chat: route "reverse charge" questions about the user's books to the books (not only to the law), add the draft-201 card and the candidate list. The law part keeps using `vat_kb` | `vat_kb/detector.py`, agent profile/prompt, card in the frontend | Questions such as "do we owe reverse charge this quarter?" and "draft our Q3 VAT return" use the tools and cite the law |
| A6 | Excel draft pack: summary of boxes, Box 1 by emirate, candidate list with evidence, bills with VAT. Labelled as a draft | `artifacts/report_spec.py` | Opens in Excel; totals match the card |
| A7 | Eval: add reverse-charge and VAT 201 questions to the VAT eval set; one planted test (add a foreign supplier bill with no VAT to a test organization and check it is found) | eval set on the VM | Run once at the end of the phase, not per task |

Phase A needs no database change. A1 and A4 are Cube model changes, which the AI team owns.

## 8. Phase B — backend and DBA (needs go-ahead)

1. `expense` and `income`: add `currency_code`, `exchange_rate` and `exchange_rate_date`. Store the AED amount alongside the original, as Article 69 requires.
2. `tax_rates`: add a reverse-charge tax type (or a flag) for bill lines. When a bill line uses it, post both entries: Dr Input VAT (recoverable part) and Cr Output VAT.
3. Make a tax code required on new bill lines. Existing lines stay as they are; the AI keeps using the phase A rules for them.
4. Use `is_recoverable` and add a partly-recoverable option, so blocked VAT (Article 53) is recorded, not just hinted at.
5. Fix `tax-report.service.ts`: the reverse-charge filters, the box numbers, and include reverse charge in total output VAT.
6. Exchange rates: a table of Central Bank rates by date, kept by finance or loaded from the Central Bank, replacing the USD-only file.

## 9. Phase C — return from the books (after phase B)

1. Box 3/10 from the reverse-charge tax code, not from candidates. Candidates remain only for bills without a code.
2. `vat_lines` cube gains reverse-charge output and input rows; Box 9 uses tax codes instead of "bills with VAT".
3. PDF draft return and full Excel pack, including a tab that reconciles the return to the VAT accounts in the ledger.
4. Quarter-on-quarter explanation ("net VAT rose by X, mainly because…"), computed from the figures, not written by the model.
5. Turn the `rcm_candidates` watch on by default.

---

## 10. Decisions needed

1. **Pilot organization.** Which real organization with foreign suppliers can we test on? The seed data can't prove detection works.
2. **Box 1 level.** Is organization-level emirate enough for the pilot, or do branches in different emirates need branch-level reporting from the start?
3. **Vendor name list.** Do we want a finance-kept list of known overseas suppliers as a weak hint, or country only?
4. **Phase B go-ahead.** The database is shared and changes go through the DBA. Who raises the request for the fields in section 8?
5. **Backend bug.** Who fixes `tax-report.service.ts`? It's live code returning wrong boxes today, separate from this project.

## 11. Testing

* Unit tests for every rule and for box arithmetic, with credit notes, zero amounts and foreign suppliers that did charge VAT.
* Unit tests patch Cube and the database; they must not reach the live `.env` database.
* One paid eval run at the end of phase A on the VM, not one per task.
* Manual check: user runs the draft return in the UI for the pilot organization; we compare it box by box with the return they actually filed.
