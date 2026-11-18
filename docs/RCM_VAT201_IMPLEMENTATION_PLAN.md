# Reverse charge and VAT 201: implementation plan

* **Written:** 17 Nov 2026, from the code at commit `f28b0ad` (Gemini_Brain_Accutax) and the shared database.
* **Spec:** `docs/REVERSE_CHARGE_AND_VAT_201_AI_SPECIFICATION.md` (version 2). Read sections 2, 3 and 6 before starting.
* **Who this is for:** the agent that builds it. Each task says which files to change, what to write, how to test it and what "done" means. A reviewer checks each phase against section 7 before the next phase starts.

---

## 0. Rules for the implementing agent

Read these first. Breaking one of them fails the review.

1. **Do only the task in front of you.** Don't refactor, rename or reformat code a task doesn't name.
2. **Never run SQL that changes the database.** No `INSERT`, `UPDATE`, `ALTER`, `CREATE`, `GRANT` on the shared database. The database is shared with the live app. Grant and migration files are written for the DBA to run; you only write them.
3. **Never deploy.** Don't restart Cube or the backend on the VM, don't copy files to the VM. The reviewer deploys.
4. **Never commit or push** unless the user tells you to.
5. **Unit tests must not reach the live database or Cube.** Patch `cube_client.load` and the catalog the way the existing tests do (see `backend/tests/unit/test_vat_anomalies.py`, the `_no_cube_catalog` fixture).
6. **No figures from the model.** Every number comes from Cube or from Python. The model only words the answer.
7. **Plain English** in every user-facing sentence: short sentences, no jargon, cite the law where the rule rests on it. Follow the tone of the existing sentences in `vat_review/anomalies.py`.
8. **Phase B is a different repository** (`accutax_bk_backend`, `accutax_bk_frontend`) and needs the user's go-ahead before any work starts. Don't start phase B on your own.
9. When something in this plan doesn't match the code you find, **stop and report it**. Don't guess.

### How to run the tests

From `Gemini_Brain_Accutax/backend`:

```bash
.venv/Scripts/python -m pytest tests/unit -q
```

**Baseline on 17 Nov 2026:** in the four VAT/Cube test files, 170 passed, 2 skipped, and **3 failed before any change**:

```
tests/unit/test_cube_model.py::test_no_stray_braces_in_cube_sql[open_payables]
tests/unit/test_cube_model.py::test_no_stray_braces_in_cube_sql[open_receivables]
tests/unit/test_cube_model.py::test_no_stray_braces_in_cube_sql[payment_lines]
```

These three are not yours to fix. Every other test must pass after each task. Run the whole `tests/unit` folder once at the start and write down its result, so you can tell your failures from old ones.

---

## 1. Facts the plan relies on

Checked on 17 Nov 2026. If one of these is no longer true, stop and report.

| Fact | Where |
|---|---|
| Bills are `public.expense` + `public.expense_items`; sales are `public.income` + `public.income_items` | `semantic/model/cubes/purchase_lines.yml`, `sales_lines.yml` |
| Neither bills nor sales have a currency or exchange-rate column | database |
| Supplier country: `contacts.country_id` → `public.country_type` (`id`, `code`, `value`, `country_code`; 10 rows; `code = 'AE'` is the UAE) | database |
| `contacts.tax_treatment` values: `vatregistered`, `nonvatregistered`, `vatregistereddesignatedzone`, `nonvatregistereddesignatedzone`. There is no "overseas" value | database, `accutax_bk_frontend/src/pages/CreateNewVendor.jsx` |
| Sales lines carry a tax code: `income_items.tax_rate_id` → `public.tax_rates.tax_type` (`standard`, `zero_rated`, `exempt`, `no_vat`, `out_of_scope`) | database |
| Bill lines almost never carry a tax code (21.33M of 21.34M lines have none) | database |
| `organizations.emirate` holds the 7 emirate names (plus 17 junk values such as `string`) | database |
| Cube connects as `cube_reader`, which can read only the tables granted in `semantic/dba/*.sql`. **`country_type` is not granted.** `tax_rates` is in `semantic/dba/cube_reader_grants_vat.sql`, whose header says "NOT YET RUN" | `semantic/dba/` |
| The AI's own DB user (`gemini_brain_ro`) can read `country_type`. Cube can't, until the grant is run | probe on 17 Nov 2026 |
| A bill finding's rule name is listed in several places (see task A2) | `grep -rn "vat_zero"` |
| Tool results with a `rows` list go through the existing Excel/PDF/CSV export path | `docs/VAT_REVIEW_PLAN.md`, phase 3 result |

---

## 2. Phase overview

| Phase | Goal | Repository | Needs |
|---|---|---|---|
| **A** | AI flags bills that may need reverse charge; AI drafts a VAT 201 with the gaps marked | `Gemini_Brain_Accutax` | One DBA **grant** (read access, no schema change) before Cube is deployed |
| **B** | The books can record reverse charge: currency, rate, reverse-charge tax type, both journal entries | `accutax_bk_backend`, `accutax_bk_frontend` | User go-ahead + DBA for migrations |
| **C** | VAT 201 from the books, ready to file; PDF and Excel pack; ledger check | `Gemini_Brain_Accutax` | Phase B live |

Order inside phase A: A0 → A1 → A2 → A3 → A4 → A5 → A6 → A7 → A8. A2 depends on A1. A4 depends on A1 and A2. A5 depends on A4.

---

## 3. Phase A — AI only

### A0. Grant file for the DBA

**Goal:** Cube can read the supplier's country and the tax codes.

**Create** `semantic/dba/cube_reader_grants_rcm.sql`. Copy the header style of `cube_reader_grants_vat.sql`. Content:

```sql
-- =============================================================================
-- cube_reader: read access for the reverse-charge review and the VAT 201 draft.
-- For the DBA. Run as a superuser or the table owner on the AccuTax database.
-- NOT YET RUN. Review first; see docs/RCM_VAT201_IMPLEMENTATION_PLAN.md.
--
--   country_type  the country of a contact (contacts.country_id). A lookup table of 10 rows with no
--                 organization data. Used to tell UAE suppliers from suppliers outside the UAE.
--
-- Also needed, if not run yet: semantic/dba/cube_reader_grants_vat.sql (tax_rates, vat_configs).
-- This only adds read access. No data, no other grant and no setting changes.
-- =============================================================================

GRANT SELECT ON public.country_type TO cube_reader;
```

**Done when:** the file exists. **Do not run it.**

---

### A1. Supplier country in the purchases view, VAT category and emirate in the sales view

**Goal:** a bill row can say where its supplier is; a sales row can say its VAT category; both views can give the organization's emirate.

**1. `semantic/model/cubes/purchase_lines.yml`**
- In the `sql`, add after the `contacts` join:
  `LEFT JOIN public.country_type ctry ON ctry.id = c.country_id`
- Add to the `SELECT` list, after `vendor_trn`:
  `UPPER(NULLIF(TRIM(ctry.code), '')) AS vendor_country_code,`
- Add a dimension after `vendor_trn`:
  ```yaml
  - name: vendor_country_code
    description: >-
      Two-letter country code of the supplier as stored on the contact (AE is the UAE). Null when the contact has no
      country. Used to find suppliers outside the UAE, whose bills may need reverse charge.
    sql: vendor_country_code
    type: string
  ```

**2. `semantic/model/views/purchases.yml`:** add `vendor_country_code` to the `purchase_lines` includes, after `vendor_trn`.

**3. `semantic/model/cubes/sales_lines.yml`**
- Add a join: `LEFT JOIN public.tax_rates tr ON tr.id = ii.tax_rate_id AND tr.organization_id = inc.organization_id`
- Add to the `SELECT` list: `COALESCE(NULLIF(TRIM(tr.tax_type), ''), 'unknown') AS vat_category,`
- Add a dimension `vat_category` (type string), with the description: "VAT category of the line from its tax code: standard, zero_rated, exempt, no_vat, out_of_scope, or unknown when the line has no tax code."

**4. `semantic/model/views/sales.yml`:** include `vat_category`.

**5. `semantic/model/cubes/organizations.yml`:** add a dimension `emirate` (`sql: emirate`, type string). In `views/sales.yml`, include it from the `organizations` join path in the same way `currency` is included.

**Rules**
- No curly braces inside SQL string literals (see the comment at the top of `purchase_lines.yml`).
- Keep every existing column and join exactly as it is.

**Tests**
- `tests/unit/test_cube_model.py` must pass, apart from the 3 old failures.
- Add a test in `test_cube_model.py` checking that `purchases` exposes `vendor_country_code` and `sales` exposes `vat_category` and `emirate`. Follow the style of `test_inventory_bank_and_branch_views_are_queryable`.

**Done when:** tests pass. **The reviewer deploys Cube only after the DBA confirms A0 and `cube_reader_grants_vat.sql` have run.** Until then, every query on these views would fail with "permission denied". Say this in your report.

---

### A2. Two new review rules: `rcm_candidate` and `foreign_vat_charged`

**Goal:** the VAT review finds bills that may need reverse charge, and bills where a foreign supplier charged its own VAT.

#### The logic (purchases side only)

For each bill with a non-zero net amount, look at `vendor_country_code`:

| `vendor_country_code` | VAT on the bill | Supplier TRN | Result |
|---|---|---|---|
| missing (key absent or null) | any | any | Old behaviour, unchanged |
| `AE` | any | any | Old behaviour, unchanged |
| not `AE` | about 0 (`abs(vat) < 0.005`) | any | **`rcm_candidate`** instead of `vat_zero` |
| not `AE` | > 0 | not a valid 15-digit TRN | **`foreign_vat_charged`** instead of `vat_rate_not_standard` and `supplier_trn_invalid` |
| not `AE` | > 0 | valid 15-digit TRN | Old behaviour (the supplier is registered in the UAE and may charge UAE VAT) |

On the **sales** side neither rule runs. A sale to a foreign customer is a zero-rating question, not reverse charge.

#### Files and changes

**1. `backend/src/gemini_brain/vat_review/scan.py`**
- Add `"vendor_country_code"` to the purchases entry of `SIDES` (`"vendor_country_code": "vendor_country_code"`). Don't add it to sales.
- Give `_query` and `scan_bills` a new keyword argument `with_country: bool = False`. When it's true and the side is purchases, add `member(side, "vendor_country_code")` to the dimensions. Default false, so existing callers don't change.

**2. `backend/src/gemini_brain/vat_review/tool.py`**
- Add `_has_country(organization_ids, subject, deadline)`, built like `_has_trn`, checking that `purchases.vendor_country_code` is in the view's dimensions. It returns false on any error.
- In `run`: `with_country = want_odd and side == "purchases" and _has_country(...)`. Pass it to `scan_bills` and `_future_dated` (add the parameter there too).
- Add to `RULE_TEXT`:
  - `"rcm_candidate": "supplier outside the UAE and no VAT: the bill may need reverse charge"`
  - `"foreign_vat_charged": "supplier outside the UAE charged VAT, and has no UAE TRN"`
- Add to `LAW_QUESTIONS`:
  - `"rcm_candidate": "When must a UAE VAT registrant account for VAT under the reverse charge on imported services, and in which boxes of the VAT return is it reported?"`
  - `"foreign_vat_charged": "Can a UAE VAT registrant recover VAT charged by a supplier outside the UAE as input tax, and how does the reverse charge apply to imported services?"`
- In the unit-test fixture style, `_has_country` must be patched to `False` in existing tests that patch `_has_trn` (add it to the `_no_cube_catalog` fixtures), so old tests keep their behaviour.

**3. `backend/src/gemini_brain/vat_review/anomalies.py`**
- Add both rules to the docstring table and to `SEVERITY`: `"rcm_candidate": "medium"`, `"foreign_vat_charged": "medium"`.
- `find_anomalies(bills, today, side="purchases")`: add the logic above. Suggested shape:
  - a helper `_foreign(b) -> bool`: true when `side == "purchases"`, the key `vendor_country_code` is present, its value isn't empty and isn't `"AE"`.
  - in the VAT loop: when `_foreign(b)` and the VAT is about 0, add to a new list `rcm`, not to `zero_vat`.
  - when `_foreign(b)` and the VAT is > 0.005 and the TRN (digits only) doesn't have 15 digits: add `foreign_vat_charged` and skip the rate check for that bill. Exclude that bill from the `supplier_trn_invalid` check too.
- Finding sentences (purchase wording):
  - `rcm_candidate`: `f"The supplier is outside the UAE ({code}) and no VAT is recorded on {_money(net)}. If this is a service or goods imported for the business, the business must account for the VAT itself under the reverse charge: about {_money(round(net * t.STANDARD_RATE, 2))} in Box 3, recoverable in Box 10 if the cost is fully recoverable."` with details `net=net, country=code, self_assessed_vat=round(net * t.STANDARD_RATE, 2)`.
  - `foreign_vat_charged`: `f"The supplier is outside the UAE ({code}) and has no UAE TRN, but VAT of {_money(vat)} is recorded. Foreign VAT is not UAE input VAT and can't be claimed in Box 9. The purchase may need reverse charge instead."` with details `net=net, vat=vat, country=code`.
- Pattern: when `len(rcm) / total >= t.ZERO_VAT_SYSTEMIC_SHARE`, add one pattern `{"rule": "rcm_candidate", "bills": len(rcm), "of": total, "share_percent": ..., "text": f"{len(rcm):,} of {total:,} bills ({..:.0f}%) are from suppliers outside the UAE with no VAT recorded. If these are imported services, reverse charge is probably not being recorded."}`. Otherwise add the findings. Same structure as the `vat_zero` pattern.
- Add a comment line above the new block citing the law: Decree-Law No. 8 of 2017, Article 48; Executive Regulation Article 59(5) is not used here.

**4. Every other place that lists rule names.** Run:

```bash
grep -rn "vat_zero" backend/src frontend/src --include=*.py --include=*.js --include=*.jsx
```

Add both new rules next to `vat_zero` wherever a dict or list names every purchase rule. On 17 Nov 2026 these were:

| File | What to add |
|---|---|
| `vat_review/blocks.py` `TITLES` | `"rcm_candidate": "Supplier outside the UAE, no VAT: reverse charge?"`, `"foreign_vat_charged": "Foreign VAT recorded as input VAT"` |
| `bots/agents_vat.py` `RULE_TITLES` | `"rcm_candidate": "May need reverse charge"`, `"foreign_vat_charged": "Foreign VAT recorded"` |
| `frontend/src/components/bots/botText.js` (purchase labels near line 70) | same titles as `RULE_TITLES` |
| `bots/monitor.py` line ~128 | read the code; add the rules only if it lists purchase rules by name. Report what you did |

Don't add the new rules to sales-only lists (`sales_rule_titles`, the sales labels in `botText.js`, `wording.py`).

**Tests:** new file `backend/tests/unit/test_vat_rcm_rules.py`. Use the `bill()` helper pattern of `test_vat_anomalies.py`, with an extra `country` argument that sets `vendor_country_code`. Required cases:
1. UAE supplier (`AE`), no VAT → `vat_zero`, no `rcm_candidate`.
2. Supplier `IN`, no VAT → `rcm_candidate` with `self_assessed_vat == round(net * 0.05, 2)`; no `vat_zero`.
3. Supplier `IN`, 5% VAT, no TRN → `foreign_vat_charged`; no `vat_rate_not_standard`, no `supplier_trn_invalid`.
4. Supplier `IN`, 5% VAT, valid 15-digit TRN → no `foreign_vat_charged`.
5. Bill with no `vendor_country_code` key → exactly the old results (compare with a run without the key).
6. Vendor credit (negative net), supplier `IN`, no VAT → `rcm_candidate` with a negative amount; no crash.
7. 9 of 10 bills foreign with no VAT → one `rcm_candidate` pattern, no per-bill findings.
8. `side="sales"` with `vendor_country_code = "IN"` and no VAT → no `rcm_candidate`.
9. `tool.run` on purchases with `_has_country` patched true passes `with_country=True` to `scan_bills` (patch `scan_bills` and check its arguments).

Also run every test file that the grep above listed. They must still pass.

**Done when:** all of the above pass; the old 3 failures are unchanged.

---

### A3. Watch: "Foreign bills that may need reverse charge"

**File:** `backend/src/gemini_brain/bots/agents_vat.py`

- Add to `WATCHES`, after `unusual_bill`:
  ```python
  Watch(
      id="reverse_charge_missed",
      label="A bill from outside the UAE may need reverse charge",
      side="purchases",
      rules=("rcm_candidate", "foreign_vat_charged"),
      default_on=False,
      desc="Supplier outside the UAE with no VAT, or foreign VAT recorded as input VAT.",
      bar="Reverse charge?",
  ),
  ```
  `default_on=False` until the pilot.
- Add `"reverse_charge_missed": "reverse charge"` to `COPY["watch_short"]`.
- Check the prompt text around line 132 that tells the model which watch to pick. Add: `"reverse charge", "imported services", "foreign suppliers" is reverse_charge_missed.`
- `frontend/src/components/bots/botText.js`: it keeps its own copy of the watches (the `watches` list near line 16) and of `watch_short` (near line 51). Add `reverse_charge_missed` to both, with the same id, side, kind, `default_on: false`, label, desc and bar as the Python entry.

**Tests:** extend `backend/tests/unit/test_bot_watches.py`: the watch exists, it's purchases, it's off by default, and every rule it names is in `anomalies.RULES`. Run `test_bot_agents.py`, `test_bot_preview.py`, `test_bot_inbox.py` too.

**Done when:** tests pass.

---

### A4. New tool `vat_201_draft`

**Goal:** a draft of every VAT 201 box for a tax period, from the books, each box marked with where its figure comes from.

**New file:** `backend/src/gemini_brain/vat_return/form201.py`

#### Step 1: share the period logic

`vat_return/tool.py` `run()` works out frequency, period, due date per organization inside its loop. Move only the period part into a function in `tool.py`:

```python
def resolve_period(params, org, today, known, group) -> Dict[str, Any]:
    """{"frequency", "frequency_source", "start", "end", "is_tax_period", "due"} for one organization."""
```

`run()` must call it and return **exactly the same output as before**. `tests/unit/test_vat_return.py` must pass unchanged. Move the code; don't rewrite it.

#### Step 2: the boxes

`form201.run(params, *, organization_ids, subject, deadline)` takes the same `params` as `vat_return` (`which`, `period`, `frequency`, `quarter_group`) and validates them the same way (call the same checks, raise `ToolInputError` the same way).

For each organization:

| Box | Query | Rule |
|---|---|---|
| 1a–1g | Cube `sales` view: dimensions `sales.vat_category`, `sales.emirate`; measures `sales.net_sales`, `sales.output_vat`; the period as a time dimension on `sales.document_date` | Lines with category `standard`, plus lines with category `unknown` and output VAT ≠ 0. All of it goes to the box of the organization's emirate (see the map below). Status `from_books`. If the emirate isn't one of the 7, put the figures in an `unassigned` entry with status `needs_emirate` and add a note |
| 2 | — | net/vat `None`, status `not_in_books` |
| 3 | — | net 0, vat 0, status `candidates_only`; candidates are listed separately (see below) |
| 4 | same query | category `zero_rated`; net only. Status `from_books` |
| 5 | same query | category `exempt`; net only. Status `from_books` |
| 6, 7 | — | status `not_in_books`, note: "Box 6 is pre-filled by the FTA from customs data. Check it in EmaraTax." |
| 8 | sum | VAT of boxes 1a–1g + 3. Status `approximate` if any box it adds isn't `from_books`, else `from_books` |
| 9 | the bills from `vat_review.scan.scan_bills(..., with_trn=True, with_country=True)` for the period | Bills with input VAT > 0.005, **excluding** bills that `anomalies.find_anomalies` reports as `foreign_vat_charged`. Net and VAT. Status `approximate` (bills have no VAT category). If the scan is incomplete, status `incomplete` and a note with `why_incomplete` |
| 10 | — | as Box 3 |
| 11 | sum | 9 + 10 |
| 12, 13, 14 | sums | 12 = 8, 13 = 11, 14 = 12 − 13. Status: the weakest status of the boxes they add |

Lines with category `out_of_scope` or `no_vat` aren't in any box. Lines `unknown` with no VAT: add their net total to `unclassified_sales` with a note ("Sales lines with no tax code and no VAT: zero-rated, exempt or out of scope can't be told apart").

Emirate map (exact names in the database, case-insensitive, trimmed):

```python
EMIRATE_BOX = {"abu dhabi": "1a", "dubai": "1b", "sharjah": "1c", "ajman": "1d",
               "umm al quwain": "1e", "ras al khaimah": "1f", "fujairah": "1g"}
```

**Reverse-charge candidates:** run `anomalies.find_anomalies(bills, today, side="purchases")` on the same bills and keep findings and patterns with rule `rcm_candidate`. Return:

```python
"reverse_charge": {
    "candidates": [ {document_number, document_id, document_date, vendor_name, country, net, self_assessed_vat} ... ],
    "candidate_count": int,
    "if_confirmed": {"box3_net": X, "box3_vat": Y, "box10_net": X, "box10_vat": Y, "net_change": 0.0},
    "note": "These bills are not in Box 3 or Box 10 yet. Each needs a person to confirm it is an imported service "
            "or goods for the business. If the VAT is blocked (Executive Regulation Article 53), Box 10 is lower "
            "and the net change is not zero.",
}
```

`if_confirmed.net_change` is 0 because it assumes full recovery; the note says so.

**Rounding:** round every money figure to 2 decimals with `round(x, 2)` at the end, not in between.

#### Step 3: output shape

```python
{
  "today": "...",
  "organizations": [{
     "organization_id": 24, "emirate": "Dubai",
     "period": {"start": "...", "end": "..."}, "frequency": "...", "frequency_source": "...",
     "due_date": "...",
     "boxes": [ {"box": "1b", "label": "Standard-rated supplies in Dubai", "net": 1000.0, "vat": 50.0,
                 "status": "from_books", "source": "Sales lines with a standard tax code"} , ... ],
     "reverse_charge": {...}, "unclassified_sales": 0.0, "notes": [...],
  }],
  "rows": [ one row per box: {"organization_id", "box", "label", "net", "vat", "status"} ],
  "row_count": n,
  "notes": ["This is a draft prepared by Accutax from the books. It is not a filed return and not an FTA document. ..."],
}
```

`rows` is what the Excel/PDF export path uses. Keep the column names fixed.

Box labels: use the FTA wording from the spec section 3 table ("Standard-rated supplies in Abu Dhabi", "Tax refunds provided to tourists", "Supplies subject to the reverse charge provisions", "Zero-rated supplies", "Exempt supplies", "Goods imported into the UAE", "Adjustments to goods imported into the UAE", "Totals", "Standard-rated expenses", "Supplies subject to the reverse charge provisions", "Totals", "Total value of due tax for the period", "Total value of recoverable tax for the period", "Payable tax for the period").

#### Step 4: register the tool

- `form201.tool_spec()`: name `vat_201_draft`. Description: "A draft of the UAE VAT 201 return, box by box, for the organizations selected in this chat and a tax period, from the books. Each box says whether its figure comes from the books, is approximate, or is not in the books. Lists bills that may need reverse charge (Box 3 and Box 10) without adding them. Use for 'draft our VAT return', 'VAT 201', 'fill the VAT return boxes', 'what goes in Box 3'. Never a filed return." Input schema: same as `vat_return`.
- `backend/src/gemini_brain/agent/tools.py`: register it next to `vat_return` (the import near line 25, the dict near line 40, the spec list near line 169). Mirror exactly how `vat_return` is done.
- `backend/src/gemini_brain/agent/preview.py` `_STEP` dict (near line 29, next to `"vat_return"`): `"vat_201_draft": "Drafting the VAT 201 return"`.
- In `tools.specs()`, offer the spec only when both `"sales" in catalog.views` and `"purchases" in catalog.views`, the same way `vat_return` is offered when `"vat"` is there.

**Tests:** new file `backend/tests/unit/test_vat_201_draft.py`. Patch `cube_client.load` to return fixed rows per query and patch `scan_bills`. Required cases:
1. Dubai organization: standard sales go to 1b; 4 and 5 from zero_rated/exempt; 8/11/12/13/14 add up; 14 = 12 − 13.
2. Emirate `"string"` → figures in `unassigned`, status `needs_emirate`, a note.
3. `unknown` category with VAT → in Box 1; `unknown` without VAT → `unclassified_sales`.
4. `out_of_scope` lines are in no box.
5. Bills: a foreign bill with VAT and no TRN is not in Box 9; a UAE bill with VAT is.
6. Two `rcm_candidate` bills → `candidate_count == 2`, `if_confirmed` sums match, Box 3 and Box 10 stay 0.
7. Incomplete scan → Box 9 status `incomplete`, a note.
8. Boxes 2, 6, 7 are `not_in_books`.
9. Bad `which` → `ToolInputError`.
10. `rows` has one row per box with the fixed column names.
11. `test_vat_return.py` still passes unchanged (step 1).

**Done when:** tests pass.

---

### A5. VAT 201 card in chat

**Goal:** under the answer, a card with every box, a copy button per figure, the status of each box, and the candidate list.

**Backend:** new file `backend/src/gemini_brain/vat_return/blocks.py`
- `form201_blocks(data) -> List[Dict]`: for each `vat_201_draft` result in the turn's `data` (items look like `{"tool": "...", "result": {...}}`: check how `vat_review/blocks.py` `review_blocks` reads them and do the same), return one block:
  `{"type": "vat_201", "title": "VAT 201 draft — <period>", "organizations": [...boxes, reverse_charge, notes...], "disclaimer": "Draft prepared by Accutax from your books. Not an FTA document. Check every figure before filing."}`
- Build it only from the tool result. No model text.

**Wire it:** in `agent/preview.py`, add `_form201_cards(data)` next to `_review_cards` (same try/except, never raises) and add its result to `blocks` next to `review_cards`. When a 201 card is present, strip the model's tables from the answer the same way (`_without_tables`).

**Frontend:** new file `frontend/src/components/blocks/Vat201Block.jsx`
- Register it in `frontend/src/components/blocks/BlockRenderer.jsx` as `vat_201: Vat201Block`, the same way `vat_review: VatReviewBlock` is.
- Reuse `formatAmount` and `formatDay` from `VatReviewBlock.jsx` (import them; don't copy).
- Layout: three sections (Output tax: 1a–8; Input tax: 9–11; Net: 12–14). Each row: box, label, net, VAT, a status tag ("From books", "Approximate", "Not in books", "To confirm"), and a copy button for each figure (`navigator.clipboard.writeText` with the plain number, two decimals, no commas).
- Below: "Bills that may need reverse charge (n)" as a collapsible list with links to the bills (use the same link pattern as the review card), and the `if_confirmed` line.
- The disclaimer at the bottom, always visible. No FTA logo or FTA styling.
- Match the look of `VatReviewBlock.jsx` (same CSS classes and theme variables). Must work at phone width.

**Tests:**
- Backend: `backend/tests/unit/test_vat_201_blocks.py`: block built from a sample result; no block when the tool didn't run; a broken result doesn't raise.
- Frontend: if the frontend has tests for `VatReviewBlock`, add the same kind for `Vat201Block`. If not, say so in your report.

**Done when:** tests pass and `npm run build` in `frontend` succeeds.

---

### A6. Chat routing and the VAT agent

**1. `backend/src/gemini_brain/agent/profiles.py`, VAT agent (`key="vat"`):**
- Add `"vat_201_draft"` to `tools`.
- Add to `instructions`: "For 'draft the VAT return', 'VAT 201' or the return boxes, call vat_201_draft. For reverse charge on the user's own bills, call review_vat with rules rcm_candidate and foreign_vat_charged, then search_vat_kb for the rule. Never add reverse-charge candidates to Box 3 or Box 10 yourself: say they need confirming."
- Add to `plan`: `("vat_201_draft", "Draft the VAT 201 boxes", False)`.
- Add a suggestion: `"Do any of my bills need reverse charge?"` (keep three or four suggestions in total; replace the least useful one only if there's a limit test).
- Check the default profile (`tools=None` means all tools): nothing to change.

**2. `backend/src/gemini_brain/vat_kb/detector.py`:** questions about the user's own reverse charge must go to the books, not only to the law search. Add test cases first in `tests/unit/test_vat_kb_integration.py`, next to the existing `is_vat_law_question` tests:
- `is_vat_law_question("Do any of my bills need reverse charge?")` → `False`
- `is_vat_law_question("How much reverse charge do we owe this quarter?")` → `False`
- `is_vat_law_question("Draft our VAT 201 return for Q3")` → `False`
- `is_vat_law_question("When does reverse charge apply to imported services?")` → `True`

Then change the regexes (`_OWN_DATA` most likely) until these pass without breaking any existing case.

**Done when:** tests pass.

---

### A7. Export

`vat_201_draft` returns `rows`, so "export the VAT 201 draft to Excel" should already work through the existing export path. Check it in a unit test if the export path has one (search `tests/unit` for the export tests used by the VAT review). If the export needs a title or column labels per tool, add them for `vat_201_draft` the same way `review_vat` has them.

**Done when:** a test shows the 201 rows become an Excel sheet with the columns `organization_id, box, label, net, vat, status`; or the report says why it can't be tested locally.

---

### A8. Eval questions

**1. Local:** add reverse-charge cases to `backend/tests/unit/test_vat_review_eval.py` in its existing style (planted bills, expected findings).

**2. For the VM replay (the reviewer runs it, once, after phase A is deployed):** add these questions to the 21-question VAT eval set. Write them in a new file `docs/RCM_EVAL_QUESTIONS.md` with the expected behaviour of each:

| # | Question | Expected |
|---|---|---|
| 1 | Do any of my bills this quarter need reverse charge? | Calls review_vat with the new rules; lists candidates or says none; cites Article 48 |
| 2 | Draft our VAT 201 return for last quarter | Calls vat_201_draft; card shows; answer short; says it's a draft |
| 3 | What goes in Box 3 for us this quarter? | Box 3 = 0 with candidates listed, "if confirmed" figure |
| 4 | When does reverse charge apply to imported services? | Law answer from search_vat_kb, no books |
| 5 | We paid a UK supplier who charged 20% UK VAT. Can we claim it? | No; foreign VAT isn't UAE input tax; mentions reverse charge |
| 6 | Is VAT on client entertainment from an overseas supplier recoverable? | Article 53: blocked for non-employees; Box 3 still due |
| 7 | Export the VAT 201 draft to Excel | `.xlsx` with box rows |
| 8 | Set up a weekly check for missed reverse charge | Creates a bot with watch reverse_charge_missed |

---

## 4. Phase B — backend and DBA (needs the user's go-ahead)

Don't start until the user says so. Write migrations; never run them on the shared database. The DBA runs them.

| # | Task | Files | Done when |
|---|---|---|---|
| B1 | Currency on documents: add `currency_code` (varchar 3, null = AED), `exchange_rate` (decimal 18,6, null), `exchange_rate_date` (date, null) to `expense` and `income` | new migration in `accutax_bk_backend/src/migrations/` named after the latest (`1794100000000-…`), e.g. `1794200000000-AddCurrencyToExpenseAndIncome.ts`; entities `modules/expense/entity/expense.entity.ts` and the income entity; create/update DTOs and services | Migration has `up` and `down`; existing documents unchanged (all null); create/update accept the fields; AED amounts are still what `line_amount` holds |
| B2 | Reverse-charge tax type: add `REVERSE_CHARGE = 'reverse_charge'` to `TaxType` in `modules/tax-rate/entity/tax-rate.entity.ts`; seed one "Reverse Charge 5%" rate per organization the same way the other default rates are created | tax-rate entity, the code that seeds default rates (find it with `grep -rn "ZERO_RATED" modules`) | New organizations get the rate; existing ones get it through a data migration the DBA runs |
| B3 | Booking: in `modules/expense/expense.service.ts` `createAutomaticJournalEntry` (around line 1534), for lines whose tax rate is `reverse_charge`: compute VAT = 5% of `line_amount`; post Dr "Input VAT Recoverable" (recoverable part) and Cr "Output VAT Payable"; the supplier is credited with the net only | `expense.service.ts` | Journal balances; payable = net; a unit test per case (full recovery, blocked) |
| B4 | Blocked VAT: allow `is_recoverable = false` and a recovery percentage on a tax rate; B3 posts the blocked part to the expense account instead of Input VAT | tax-rate entity, `expense.service.ts` | Test: blocked line debits the expense, not Input VAT |
| B5 | Fix `modules/report/tax-report.service.ts` (around line 726): reverse-charge filters (they require `entry_type` to be two values at once), box numbers to match the FTA form, reverse charge included in total output VAT | `tax-report.service.ts` | Unit test with one reverse-charge entry gives Box 3 and Box 10 |
| B6 | Supplier treatment: add an "Overseas (outside the UAE)" option to the vendor tax treatment | `accutax_bk_frontend/src/pages/CreateNewVendor.jsx` (around lines 752 and 869); contact DTO accepts it | Saved and shown on the vendor |
| B7 | Exchange rates by date: a table of Central Bank rates (currency, date, rate) kept by finance, used to fill `exchange_rate` on new foreign bills | new migration + module | A USD bill dated D gets the rate for D |

**Check before B3:** the bill posting uses `findAccountByCode("2110")` as a fallback for Accounts Payable, but in `modules/chart-of-accounts/uae-chart-of-accounts.constant.ts` code `2110` is "Output VAT Payable", and the VAT fallback `"1140"` differs from the UAE chart's `1210` "Input VAT Recoverable". Report this to the user before changing anything near it. It may be a live bug.

---

## 5. Phase C — return from the books (after phase B is live)

| # | Task | Files |
|---|---|---|
| C1 | Cube: `purchase_lines` gets `currency_code`, `exchange_rate`, and `vat_category` from `expense_items.tax_rate_id`; `vat_lines` gets reverse-charge rows (direction `RC_OUTPUT` / `RC_INPUT`) and measures `reverse_charge_net`, `reverse_charge_vat` | `semantic/model/cubes/*.yml`, views |
| C2 | `vat_201_draft`: Box 3/10 from the reverse-charge tax code, status `from_books`; candidates only for bills without a code; Box 9 from `standard` bill lines | `vat_return/form201.py` |
| C3 | Rule `rcm_candidate` also uses currency ≠ AED as a signal; new rule `fx_rate_missing` for foreign-currency bills without a rate | `vat_review/anomalies.py` |
| C4 | Ledger check: Box 8 and Box 11 against the VAT accounts, reusing `vat_return.tool._ledger`; mismatch note | `form201.py` |
| C5 | Excel pack with tabs: summary, Box 1 by emirate, reverse-charge schedule, Box 9 bills, ledger check. PDF draft with the disclaimer, no FTA branding | `artifacts/report_spec.py` |
| C6 | Watch `reverse_charge_missed` on by default | `bots/agents_vat.py` |

Each C task gets the same kind of test list as phase A, written when phase B's final field names are known.

---

## 6. What not to build

- No filing to EmaraTax. The user files.
- No FTA logo or FTA-styled document.
- No vendor-name list of "known SaaS suppliers" in code. If finance wants one later, it's configuration.
- No automatic 0% recovery from keywords. Article 53 is a question to the user (`uploads/invoice_rules.claim_hint` already does this for uploads).
- No Saudi Arabia (ZATCA).
- No reliance on the supplier TRN alone: only 8 of about 2 million contacts have a 15-digit TRN.

---

## 7. Review checklist (for the reviewer)

### After phase A, before deploying

1. `git diff --stat`: only files named in this plan changed.
2. Unit tests: `.venv/Scripts/python -m pytest tests/unit -q`. Only the 3 old failures.
3. Read `anomalies.py` changes: a bill without `vendor_country_code` behaves exactly as before.
4. Read `form201.py`: no figure comes from the model; every box has a status; Box 3/10 are 0 with candidates listed.
5. `frontend`: `npm run build` passes; the card has the disclaimer; copy buttons copy plain numbers.
6. `semantic/dba/cube_reader_grants_rcm.sql` exists and was not run by the agent.

### Deploying phase A (reviewer)

1. Ask the user to have the DBA run `cube_reader_grants_vat.sql` (if not done) and `cube_reader_grants_rcm.sql`. Wait for confirmation.
2. Deploy Cube, then the AI backend and frontend on the VM, following the VM deploy notes (AI on port 3535). Keep the rollback copies.
3. Smoke test in the UI with a test organization (orgs 24–33): "Do any of my bills need reverse charge?" and "Draft our VAT 201 return for last quarter". Check that the card shows and the figures match a hand query.
4. One paid VM eval run with the 8 questions in `docs/RCM_EVAL_QUESTIONS.md` plus the existing 21. Not one run per task.
5. Expect many `rcm_candidate` findings in the test organizations: about half the seed contacts are in India. That's seed data, not a bug. The real test is the pilot organization.

### After phase B

1. Migrations reviewed by the DBA; `down` works on a copy, never on the shared database.
2. A test bill with the reverse-charge rate: journal balances, Input VAT and Output VAT both posted.
3. The backend VAT report shows Box 3 and Box 10.

### After phase C

1. Draft 201 for the pilot organization compared box by box with the return they actually filed.
2. Ledger check agrees, or the difference is explained.
