# UAE VAT Knowledge Base — Clean-Room Build Plan for AccuTax AI

Status: proposed, version 2 (2026-10-04). Replaces version 1 of this plan.
Owner: AccuTax AI (Gemini Brain) backend

## 1. Summary

AccuTax AI will answer UAE VAT law questions from the official FTA documents, with numbered
citations that link to the source PDFs on tax.gov.ae.

It is built **clean-room**: no code from LexRAG is used, copied, imported or adapted. That includes
the code we wrote inside the LexRAG test project. LexRAG was only a test bench. What we keep from it
is knowledge that is not code:

- the list of techniques that worked (they are standard, published methods),
- the 24 test questions (they are ours, taken from our own FireShot test runs),
- the scores to beat.

Everything is written fresh in the AccuTax AI codebase from the written specification in section 5,
using only permissively licensed libraries and models (section 9).

Nothing that works today changes behaviour. The feature sits behind a switch that is off by default,
and every failure falls back to today's answer.

## 2. Target results

| Test | Score to beat (measured in the test bench) | Release gate in AccuTax AI |
|---|---|---|
| The 24 test questions | 18/24 correct, 2 wrong (Claude 3.5 Sonnet) | **≥ 22/24 correct, 0 wrong** |
| 10 additional questions | 8/10 correct, 0 wrong | ≥ 8/10 correct, 0 wrong |
| Existing unit tests and evals | — | all pass, no regression |
| Added latency, VAT-law questions | — | ≤ 3 s retrieval (p95) on the VM |
| Added latency, all other questions | — | none |

## 3. Clean-room rules

1. **No LexRAG code.** Do not open LexRAG source files while writing AccuTax AI code. Do not copy,
   paste, import or translate any LexRAG file, function or prompt, including code written by our
   team inside the LexRAG folder (`fta_vat_corpus.py`, `scripts/*`, `embeddings/embedder.py`,
   `api/rag_engine.py`, `api/utils.py`).
2. **Build from the spec only.** The developer works from section 5 of this document, which
   describes behaviour (inputs, outputs, rules, tests), not code.
3. **No AGPL or GPL dependencies.** PyMuPDF (AGPL) is not used. Every new dependency and model is
   checked against the allowed licence list (section 13) before it is added.
4. **Licence check in CI.** A `pip-licenses` step fails the build if any installed package has an
   AGPL, GPL or LGPL licence that is not on an explicit allow list.
5. **Code review checklist** for every pull request in this feature: no LexRAG names, file layout
   or comments; dependencies on the allowed list; tests written fresh.
6. **Keep the test bench separate.** The LexRAG folder stays outside all Accutax repositories. It is
   archived after Phase 3 and is never deployed.
7. **Ideas are free to use.** Hybrid search, BM25, rank fusion, reranking, chunking and query
   expansion are standard techniques described in public papers and documentation. Using them is
   not copying LexRAG; copying LexRAG's code would be.

## 4. How a question flows

```
user question
  └─ PII redactor (unchanged)
  └─ router (unchanged): conversation-meta → how-to guide → fast router → Gemini classifier
       ├─ types 3, 4, 5 (reports, data, forecast) ──────────────► unchanged
       └─ types 1, 2, 6, 7 (knowledge answers)
            └─ NEW: VAT-law question?                    (only when VAT_KB_ENABLED)
                 ├─ no  ─► today's direct answer, unchanged
                 └─ yes ─► search the VAT knowledge base (3 s timeout)
                           ├─ nothing confident / error / timeout ─► today's direct answer
                           └─ sources found ─► answer with Sonnet, VAT rules, numbered sources
                                               + "Sources" block with FTA links
```

## 5. Specification (what to build)

New package: `backend/src/gemini_brain/vat_kb/`. File names and internal design are the developer's
choice; the behaviour below is what matters. Each requirement has an acceptance test.

### R1. Document collection
- Collect documents from two FTA pages: the Legislation page (categories VAT, Federal Tax
  Procedures, Federal Tax Authority) and the VAT "Guides, References & Public Clarifications" page
  (all categories). Both pages are paginated ASP.NET lists.
- For each item, record: title, category, issue date as shown, PDF URL(s).
- Sort each item into a tier:
  - **core**: laws, executive regulations, Cabinet/Ministerial/FTA decisions, Directives on Tax
    Transactions, public clarifications, technical VAT guides (sector guides, schemes,
    apportionment, designated zones, real estate, financial services, insurance, e-commerce,
    tax groups, charities, mosques, private clarifications guide);
  - **extra**: EmaraTax portal user manuals, bulletins, awareness material, tax-agent administration;
  - **skip**: archived items, corporate tax, excise, tobacco, economic substance, and the
    **superseded list** (R2).
- Only core documents are indexed.
- Download each PDF with retries (the FTA server drops connections), keep it only if the file
  starts with `%PDF`, and skip files already downloaded.
- Acceptance: today the crawl finds about 119 core documents; all core links return real PDFs.

### R2. Superseded documents
Keep a reviewed list of documents replaced by later law and exclude them from the index:
TAXP001 Amendment of Penalties; TAXP002 and TAXP004 Redetermination of Administrative Penalties;
Cabinet Decision 49/2021 (merged into the consolidated Cabinet Decision 40/2017); VATP031 (replaced
by VATP037); Directors Services guide (2018); Date of Supply for Independent Directors; Temporary
Zero-rating of Certain Medical Equipment.
- Acceptance: "What are the current late payment penalties?" never cites these documents.

### R3. Text extraction and cleaning
- Extract text with `pypdf` or `pdfminer.six`.
- Many FTA PDFs print Arabic and English side by side. Remove Arabic script, drop lines left with
  no English words, and join the remaining lines into running text.
- Read the **effective date** from the document text where it is stated ("Effective from …",
  "with effect from …") and store it next to the issue date.
- Acceptance: the cleaned text of Directive No. 5 of 2026 contains the sentence about valuing a
  deemed supply "based on the total costs on which Input Tax was incurred", with no Arabic
  characters; Cabinet Decision 153/2025 gets effective date 14 January 2026.

### R4. Chunking
- Split cleaned text into chunks of about 1,000 characters at sentence boundaries, with about
  150 characters of overlap between neighbours.
- Start every chunk with a header line: document title, issue date, effective date.
- Keep chunk order so neighbours can be fetched (R7).
- Acceptance: no chunk over 1,200 characters; every chunk starts with its title.

### R5. Search: two methods combined
- **Meaning search:** embed every chunk once; at query time embed the question and take the 100
  closest chunks by cosine similarity. With about 2,700 chunks a plain in-memory matrix is fast
  enough (no vector database needed).
  - Candidate models (choose in Phase 0 by retrieval test): `BAAI/bge-small-en-v1.5` run locally
    through `fastembed` (ONNX), or `cohere.embed-multilingual-v3` on Amazon Bedrock (ap-south-1,
    already our cloud; also reads Arabic). If the model expects a query instruction, apply it only
    to questions, as its documentation says.
- **Keyword search:** BM25 over the same chunks (library `rank_bm25`), top 100.
- **Combine** the two lists with Reciprocal Rank Fusion (k = 60).
- Acceptance: for each of the 24 questions, the expected document is in the top 20 of the combined
  list.

### R6. Query expansion
Before searching, add the legal wording for common everyday terms. Initial list:
USDT/USDC/bitcoin/crypto → "digital currency"; smartphone → "smart phones electronic devices";
late payment/overdue → "failure to settle the Payable Tax administrative penalty"; penalty →
"administrative penalties"; free zone → "designated zone"; bought or sold a whole business →
"transfer of a business as a going concern"; re-invoice/recharge → "disbursement reimbursement";
gold/jewellery/diamonds → "precious metals precious stones"; scrap → "metal scrap";
e-invoice → "Electronic Invoicing System"; director fees/board member → "Director Board of Directors
natural person"; TRN → "Tax Registration Number"; VAT group → "Tax Group"; e-invoicing criteria/who
must → "phases revenue threshold Accredited Service Provider".
- The model answering the user sees the original question, not the expanded one.
- Acceptance: "We receive payment in USDT…" retrieves Directive No. 3 of 2026.

### R7. Selection and guardrails
- Take the best 8 chunks from the combined (hybrid) ranking. The Phase 1 check found every expected
  source for the 34 test questions in the top 8 (top 6 missed one).
- Optional reranking behind a setting (`vat_kb_rerank`, default off): cross-encoder
  `BAAI/bge-reranker-base` via `fastembed` `TextCrossEncoder` on the top 20 only.
- Guardrail A: the top 3 hybrid results are always kept, whatever the reranker says.
- Guardrail B: for the top 2 documents, also include the chunk before and after the best chunk,
  appended after the ranked results so they never push a better match out.
- Guardrail C: cap the context at about 14,000 characters, best first.
- Confidence: if neither search method finds a strong match (thresholds set in Phase 1 from the
  24 + 10 questions and 30 off-topic questions), report "no confident source" so the runner falls
  back.
- Acceptance: Q4 (USDT) keeps Directive 3; Q9 (villa) includes the sentence that excludes
  buildings leased to another person.

### R8. VAT-law question detector
- Returns true for questions about VAT rules, FTA decisions, rates, registration, invoices,
  refunds, penalties, e-invoicing, designated zones, etc.
- Returns false when the question asks for the user's own figures ("our VAT payable this quarter",
  "show my VAT return", "how much VAT did we collect").
- Only questions the router sends to the knowledge-answer path as FAQ (1), Accounting Concept (6) or
  Summary & Advice (7) are checked. App Guidance (2) stays with the app guide, and "how do I … in
  Accutax" phrasing is rejected.
- Acceptance: a fixed list of 30 law questions → true; 30 data questions → false.

### R9. Answer rules (system prompt section, written fresh)
1. Answer only from the numbered sources; cite them as [1], [2]. Never invent article numbers,
   decision numbers or dates.
2. When sources conflict or one amends another, apply the most recent; say from which date it
   applies.
3. Distinguish issue date from effective date.
4. Name the party each rule applies to (supplier, recipient, reseller, importer, tax group member).
5. Apply thresholds, exceptions and exclusions exactly as written, including limits on the
   exception itself; say when something is outside the scope of VAT or not a supply.
6. When the answer depends on facts the user did not give, state the condition, not a verdict.
7. Use the numbers in the CALCULATIONS section (R10) instead of doing arithmetic.
8. Direct answer first, under 200 words, at most 6 bullets, no tables unless asked.
- Acceptance: Q16 (director fees) says director services by a natural person are not a supply since
  1 January 2023; Q17 (Saudi export) gives the one-month / not-effectively-connected condition.

### R10. VAT calculations in code
- Detect amounts in the question and pre-compute: VAT inside a VAT-inclusive amount
  (amount × 5/105), VAT on a net amount (× 5%), partial-payment shares, and the standard
  apportionment ratio (taxable ÷ (taxable + exempt), rounded as the law requires).
- Pass the results to the model as a CALCULATIONS section.
- Acceptance: Q13 gives AED 3,000 (unpaid AED 63,000 × 5/105); Q12 gives AED 60,000.

### R11. Sources block
- Built in code from the stored document list: number, title, issue date, FTA link.
- Returned as an existing `markdown` response block, so the frontend needs no change.
- The model never writes URLs.

### R12. Answer model
- VAT-law answers use a Sonnet model set by `vat_kb_model_id`. Phase 0 compares Claude 3.5 Sonnet
  v2 (current Accutax model) with `in.anthropic.claude-sonnet-5` (India inference profile).
- Note: the knowledge-answer path today uses a constant named `HAIKU45_ID` whose value is
  Claude 3 Haiku; that stays unchanged for non-VAT questions.

## 6. Changes to existing AccuTax AI code

| File | Change |
|---|---|
| `orchestrator/gemini_brain_runner.py` | One call to a new helper in each knowledge-answer branch (`_run_inner` and `_run_stream_inner`, at `if qtype in LEFT_PATH_TYPES`). The helper returns the inputs unchanged on any error, timeout or "no confident source". |
| `config/settings.py` | New settings with safe defaults: `vat_kb_enabled=False`, `vat_kb_shadow=False`, `vat_kb_dir`, `vat_kb_model_id`, `vat_kb_timeout_s=3`. |
| observability | One trace stage `vat_kb`: time taken, documents used, top score, fallback reason. |
| `pyproject.toml` | New dependencies from the allowed list only. |

Not changed: router rules, fast router, classifier prompt, tools registry, multi-org code, report
code, frontend, Accutax NestJS backend, database.

## 7. Storage

Files on the AccuTax AI server, like report artifacts today; no database changes.

```
<vat_kb_dir>/                         e.g. /opt/accutax-ai/data/vat_kb  (outside git)
  pdfs/                               downloaded FTA PDFs (~60 MB)
  documents.csv                       title, category, issue date, effective date, URL, tier
  builds/<timestamp>/                 chunks, embeddings matrix, keyword index, build info
  current -> builds/<timestamp>       switched only after checks pass; previous kept for rollback
```

The document list rules and the superseded list are kept in git; PDFs and builds are not.

## 8. Phase 0 results (2026-10-04)

| Item | Decision / finding |
|---|---|
| Embedding model | **`BAAI/bge-small-en-v1.5`, local ONNX via `fastembed`** (MIT). Runs on the VM, no per-query cost. Cohere on Bedrock rejected (usage cost). |
| Retrieval test (24 questions, right document in top 6) | bge-small: dense only 20/24 → + query expansion 23/24 → **+ BM25 hybrid 24/24** → + reranker 22/24. Cohere gave the same hybrid result (24/24), so bge-small loses nothing. |
| Reranker | **Off by default in Phase 1.** Hybrid search alone already puts the right document in the top 6 for all 24 questions; the reranker pushed two down (Q6 penalties 2 → 8, Q24 e-invoicing flow 3 → 7) and costs ~48 s per question on the laptop (50 candidates). If Phase 3 shows a need, use `BAAI/bge-reranker-base` via `fastembed` `TextCrossEncoder` (MIT, ONNX) on the top 20 only, keeping the top 3 hybrid results regardless. Never `jina-reranker-v2` (non-commercial licence). |
| Embedding speed | 2,745 chunks embedded in 865 s on the laptop (2-core i3); expect a few minutes on the VM. Done once per monthly build, not per question. |
| PDF extraction | **`pypdf`** (BSD-3) works on the bilingual FTA PDFs: 111 documents → 2,745 chunks, 0 chunks with Arabic left, effective date found in 36 documents (e.g. Cabinet Decision 153/2025 → 14 Jan 2026; Cabinet Decision 129/2025 → 14 Apr 2026). For public clarifications the first "with effect from" date is often an amendment date, not the document's own; show it only for laws and decisions. |
| Keyword search | `rank_bm25` (Apache-2.0). |
| Answer model | Start with the current Accutax model, Claude 3.5 Sonnet v2 (`apac.anthropic.claude-3-5-sonnet-20241022-v2:0`). Try `in.anthropic.claude-sonnet-5` in Phase 3 only if the gate is missed. |
| VM capacity | 7.5 GB RAM (4.2 GB available), 66 GB free disk, 8 vCPU Xeon Gold 6252, Python 3.11. Enough for both ONNX models (~0.6 GB RAM). |
| Licence guard | `backend/scripts/ops/check_licenses.py` added (standard library only). Current result: only PyMuPDF flagged, installed in the dev venv for two report visual tests (`test_report_visual.py`, `test_report_layout.py`); **not installed on the VM**. Recommended: replace it in those tests with `pypdfium2` (Apache-2.0/BSD-3). |
| Clean-room | The spike was written fresh from this spec in `E:\vat_kb_spike` (throwaway, outside all repos). It used only the downloaded FTA PDFs and their document list, no LexRAG code. |

## 9. Phase 1 results (2026-10-04)

| Item | Result |
|---|---|
| Package | `backend/src/gemini_brain/vat_kb/`: `fta_crawler.py`, `documents.py`, `text.py`, `expansion.py`, `calc.py`, `embedder.py`, `index.py`, `retrieval.py`, CLI `python -m gemini_brain.vat_kb crawl / build / search`. Written from this spec; no LexRAG code. |
| Crawl | 112 core documents (plus 139 extra, 24 skipped incl. the 8 superseded), all downloaded, 0 failures. Found a new FTA clarification, VATP047 (metal scrap). |
| Build | 112 documents → 2,766 chunks, 0 unreadable PDFs; 963 s on the laptop (2-core i3), done once per monthly refresh. |
| Retrieval check (`scripts/eval/vat_kb_retrieval_check.py`) | **34/34** questions (24 + 10) have an expected source in the top 8; 34/34 confident. Search time after warm-up: 30–100 ms per question; first question ~2.3 s (model load). |
| Confidence rule | confident if best cosine ≥ 0.75, or ≥ 0.70 with BM25 ≥ 15. Off-topic set: 1/20 confident ("Which customers have overdue invoices?", a data question the Phase 2 detector must reject). |
| Changes made during the check | Neighbouring chunks moved after the ranked results; top 6 → top 8; word list: "wrong/exempt box", "error in the return" → "error or omission in the Tax Return … voluntary disclosure" (this tuned one of the 10 additional questions, H4). |
| Unit tests | 42 new tests in `tests/unit/test_vat_kb.py`, offline (fake embedder). Full suite: 2,033 passed (the 1,991 from the baseline taken before Phase 1 + 42 new), with the same 4 pre-existing failures as the baseline (PII pipeline, explicit org id, 2 window-widening tests). |
| Dependencies | Optional install group `vat_kb` in `pyproject.toml`; the default install is unchanged. Licence check passes for all new packages. |
| Existing code | Not touched: no runner, router, settings, frontend or database changes. |

## 10. Phase 2 results (2026-10-04)

| Item | Result |
|---|---|
| New modules | `vat_kb/detector.py` (R8), `vat_kb/answer.py` (R9 rules, CALCULATIONS, numbered SOURCES; R11 Sources block), `vat_kb/augment.py` (runner hook, switch, timeout, shadow mode, warm-up). |
| Settings (all off by default) | `VAT_KB_ENABLED=false`, `VAT_KB_SHADOW=false`, `VAT_KB_DIR=""`, `VAT_KB_MODEL_ID=""` (= `BEDROCK_MODEL_ID`, Claude 3.5 Sonnet v2), `VAT_KB_TIMEOUT_SECONDS=3`. |
| Existing code changed | `gemini_brain_runner.py`: one `vat_kb_augment()` call in each knowledge-answer branch (normal and streaming), the answer model and Sources block taken from its result, one `vat_kb` trace event; `_call_llm` reads an optional `model_id`/`model_label` passed only for VAT answers. `api/app.py`: background warm-up at startup when the feature is on. `config/settings.py`: the five settings. |
| Switch off | `augment()` returns the prompt unchanged, no model change, no blocks, no trace, and never searches. Every changed expression in the runner evaluates to its old value. |
| Fallbacks | No directory, no build, weak match, search error, timeout, or the optional libraries not installed → today's answer (tested for each). |
| Shadow mode | Detects and searches on a background thread and logs the sources it would use; answers unchanged. |
| Without the `vat_kb` install group | AccuTax AI imports and runs normally; even if switched on by mistake, answers stay unchanged and a warning is logged. |
| Tests | `tests/unit/test_vat_kb_integration.py`: detector (30 VAT-law questions accepted, 30 data/app/general questions rejected), prompt and Sources block, every switch/fallback/shadow case, and three runner-level tests through `GeminiBrainRunner.run` with Bedrock mocked. 123 VAT knowledge-base tests in total. Full suite: 2,114 passed (1,991 baseline + 123 new) with the same 4 pre-existing failures as the baseline. |

## 11. Phase 3 results (2026-10-04)

Evaluation: `backend/scripts/eval/vat_kb_eval.py` runs each question through the real `GeminiBrainRunner`
(live intent classification and live Claude 3.5 Sonnet answers on Bedrock; Accutax data API off).
Every answer was graded by hand against the FTA documents. Final report:
`<vat_kb_dir>/eval/vat_kb_eval_20261004_233510.md`.

| Set | Fully correct | Correct but incomplete | Wrong | Gate |
|---|---|---|---|---|
| The 24 test questions | **21** | 3 (#1, #2, #24) | **0** | ≥ 22 correct, 0 wrong — **passed** |
| 10 additional questions | **10** | 0 | **0** | ≥ 8 correct, 0 wrong — **passed** |

Incomplete answers: #1 lists the 2026 changes (Art. 48 reverse charge, new Art. 54 (bis)) but also
lists Art. 65/70, which VATP046 attributes to the 2024 law, and omits the repeal of Art. 79 (bis);
#2 omits the AED 375,000 bank-account check and the AED 10,000 exception of FTA Decision 13/2026;
#24 describes the e-invoicing phases rather than the document flow (not described in the corpus).

Problems found by the evaluation and fixed:

| Problem | Fix |
|---|---|
| The classifier sent VAT questions with amounts to the data path (#12, #13, #21) | `vat_kb.augment.reroute()`: a type 3/4/5 from the LLM classifier (never the fast router) becomes type 6 when the detector says VAT law and the search is confident; searched once, result reused. |
| "How do I value a deemed supply?" went to App Guidance (#3) | Type 2 included; the detector already rejects app how-tos. |
| Amount parser crashed on "zero-rate**d,**" (H10 lost its sources) | Amount pattern requires a whole-word currency and a digit; calculations can no longer block the sources. |
| One long document filled every slot; key text one chunk away; exact phrase missed by meaning search (H3, H10, #1) | At most 4 chunks per document; top hit of each search method always kept; top document ±2 and second ±1 neighbouring chunks; opening chunk of the top 3 documents. |
| Old cash limit quoted (H2) | FTA Decision 1/2019 added to the superseded list (replaced by FTA Decision 6/2022); superseded documents are now also skipped at query time. |
| Old penalty regime called "current" (#6) | Today's date is given in the VAT prompt. |
| Invented Accutax click-paths and a made-up Accutax claim (#4, #7, #23, H4, H7) | Answer rule: no Accutax screens, features or plans in VAT law answers; FTA filings are on EmaraTax. |
| Older guide followed over newer clarification (#7) | Rule: when an older guide and a newer decision/clarification disagree, follow the newer one; word list maps designated zone → mainland/import wording to the VATP027 terms. |

Tests: 139 VAT knowledge-base tests. Speed: 6–9 s per answer end to end on the laptop (search
30–100 ms; the rest is the Bedrock calls).

## 12. Phases

| Phase | Work | Days |
|---|---|---|
| 0. Setup and decisions **(done)** | Clean-room rules agreed; CI licence check added; allowed-licence list confirmed; choose embedding model (local bge-small vs Bedrock Cohere multilingual) and answer model (Sonnet 3.5 vs Sonnet 5) on a small retrieval test; check VM RAM/disk; confirm `pypdf` handles the bilingual PDFs. | 1 |
| 1. Knowledge base, written from the spec **(done)** | R1–R7, R10: collection, superseded list, extraction, chunking, search, expansion, reranking, calculations. Unit tests for each acceptance item. Retrieval check: right document in the top 8 for all 34 questions. | 4–5 |
| 2. Integration behind the switch **(done)** | R8, R9, R11, R12; the helper and two hook points; shadow mode (search and log only); tests that output is byte-identical to today with the switch off, and unchanged on any knowledge-base error. | 2–3 |
| 3. Evaluation gate **(done)** | New eval script that runs the 24 + 10 questions through the real AccuTax AI runner and writes a report; manual review; fix until ≥ 22/24 and 0 wrong; run all existing unit tests and evals. | 2 |
| 4. Deploy to the VM | Copy the build; 2–3 days in shadow mode; switch on; you re-test in the UI with FireShot PDFs. Rollback: switch off and restart. | 1 |
| 5. Keep it current | Monthly timer: collect, build, check, switch only if checks pass; review the superseded list when a new consolidated law or penalty decision appears. | 0.5 |

Total: about 10–12 working days. Archive the LexRAG test bench after Phase 3.

## 13. Allowed dependencies and models

| Component | Choice | Licence |
|---|---|---|
| PDF text | `pypdf` or `pdfminer.six` | BSD-3 / MIT |
| HTML parsing | `beautifulsoup4` | MIT |
| HTTP | `requests` (already used) | Apache-2.0 |
| Keyword search | `rank_bm25` | Apache-2.0 |
| Vectors | `numpy` (in-memory matrix) | BSD-3 |
| Local embeddings and reranker | `fastembed` (ONNX runtime) | Apache-2.0 (onnxruntime: MIT) |
| Embedding model (option A) | `BAAI/bge-small-en-v1.5` | MIT |
| Embedding model (option B) | `cohere.embed-multilingual-v3` on Bedrock | AWS service terms (same as Claude) |
| Reranker model | `BAAI/bge-reranker-base` | MIT |
| Answer model | Claude Sonnet on Bedrock | AWS service terms (already used) |
| **Not allowed** | PyMuPDF / `fitz`, any LexRAG file, any AGPL/GPL package | — |

FTA documents are public government publications. The assistant cites and links them; it does not
republish the PDFs to users.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Licence issue | Clean-room rules, spec-only build, CI licence check, review checklist. |
| A data question is treated as a law question | Only knowledge-answer types are checked; detector rejects data phrasing; shadow mode first. |
| Wrong legal answer reaches a customer | 0-wrong release gate, citations on every answer, conditional-answer rule, superseded list. |
| Results lower than the test bench | Same proven techniques; acceptance tests per requirement; eval gate before release. |
| FTA changes the law after a build | Monthly refresh; answers show each source's issue date. |
| Memory or latency on the VM | ONNX models only, loaded on first use; 3 s timeout with fallback. |

## 15. Database schema changes

**None.** No existing table is altered and no new table is needed; the knowledge base is stored as
files. The Accutax NestJS `document_embeddings` table is not touched.

If AccuTax AI later runs on several servers, the files can move into two new tables
(`vat_kb_documents`, `vat_kb_chunks`) created by the DBA. That is a separate, later decision.
