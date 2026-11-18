# Reverse Charge Mechanism (RCM) & VAT 201 — Evaluation Questions

These evaluation questions expand the VAT evaluation set from 21 questions to 29 questions. They test the end-to-end behavior of the agent across the new RCM detection rules, statutory Form 201 draft compilation, Article 53 recovery knowledge, Excel export, and autonomous bot monitoring.

To be executed on the VM after Phase A is deployed and Cube reader grants are applied.

---

## Evaluation Question Set

| # | Question | Mode / Target Tool | Expected Behavior & Grounding |
|---|---|---|---|
| **1** | *"Do any of my bills this quarter need reverse charge?"* | `review_vat` + `search_vat_kb` | Calls `review_vat` with rules `rcm_candidate` and `foreign_vat_charged`. Accurately identifies suppliers with country codes outside the UAE (`AE`) and zero VAT recorded. Citations ground in UAE Federal Decree-Law No. 8 of 2017, Article 48. States clearly that bills require human confirmation before self-assessing. |
| **2** | *"Draft our VAT 201 return for last quarter"* | `vat_201_draft` | Calls `vat_201_draft`. Renders the interactive VAT 201 card (`vat_201`) with Sections 1, 2, and 3. Response text is short and concise without repeating tables. Includes disclaimer: *"Draft prepared by Accutax from your books. Not an FTA document."* |
| **3** | *"What goes in Box 3 for us this quarter?"* | `vat_201_draft` | Reports Box 3 Net as `0.00` and VAT as `0.00` with status `candidates_only`. Separately lists candidate bills in the card and provides the `"if confirmed"` impact summary without prematurely altering statutory ledger figures. |
| **4** | *"When does reverse charge apply to imported services?"* | `search_vat_kb` (Law route) | Router classifies as VAT law (`is_vat_law_question` = True). Retrieves from FTA statutes (Article 48 of Decree-Law No. 8 of 2017). Explains conditions: supplier outside UAE, recipient VAT-registered in UAE, goods/services used for business. No books or ledger queried. |
| **5** | *"We paid a UK supplier who charged 20% UK VAT. Can we claim it?"* | `search_vat_kb` + AP guidance | Clarifies that foreign tax is strictly non-recoverable as UAE input tax in Box 9. Advises checking if supply was imported services subject to UAE reverse charge, and seeking a refund/zero-rated invoice from the UK supplier with proof of UAE export/business establishment. |
| **6** | *"Is VAT on client entertainment from an overseas supplier recoverable?"* | `search_vat_kb` (Article 53) | Explains that while 5% Output VAT is mandatory in Box 3 under reverse charge (Article 48), Input VAT recovery in Box 10 is **blocked** under Executive Regulation Article 53 (entertainment provided to non-employees / clients). Results in actual net payable cash tax to FTA. |
| **7** | *"Export the VAT 201 draft to Excel"* | Report / Artifact generation (`xlsx`) | Generates a valid `.xlsx` workbook containing the VAT 201 statutory boxes with columns: `box`, `label`, `net`, `vat`, `status`. Download link / artifact rendered. |
| **8** | *"Set up a weekly check for missed reverse charge"* | Bot Agent (`agents_vat.py`) | Configures a scheduled or inbox watch for the VAT Agent with `reverse_charge_missed` enabled (`rcm_candidate` rule on purchases). |

---

## Verification Criteria

1. **No Hallucinated Figures:** All numbers in Form 201 boxes, candidate lists, and summaries must match Cube data and exact Python arithmetic.
2. **Clear Grounding & Disclaimers:** Every Form 201 response must state that it is a draft from the books and not a filed return.
3. **Article 53 Caveats:** Whenever reverse charge input recovery is mentioned, warnings regarding blocked input tax (Article 53) must be cited.
