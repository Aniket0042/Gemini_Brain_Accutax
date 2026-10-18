# PII red-team: 41 questions, 17 Oct 2026

With masking on, none of the 39 regular cases sent personal data to the model, once the fixes this run led to were in place. The run also found two bugs in production masking, now fixed: multi-organization chats loaded no contact names, and 3-letter names were not masked. Answers held up: 40 of 41 passed the answer checks. The one that did not is an injection case, where the user typed a placeholder themselves and the answer repeats it.

The set is `backend/tests/data/pii_redteam.json` and the runner is `backend/scripts/eval/pii_redteam.py`. Both runs were on the VM with the production code and `PII_MODE=on`, writing to `/tmp/pii_redteam` so the production snapshots were left alone. The full run cost $0.87.

## How it checks

For every case, the runner reads the privacy snapshot of what was really sent to the model, call by call. That includes the report narrator's call for file requests. It then searches what was sent for:

- every value the request hid (from the snapshot's encrypted vault),
- every supplier, customer and payee name of the selected organizations (from the contact dictionary, in any spelling),
- the identifiers the user typed, in any spacing,
- identifiers found by pattern.

It also checks the answer: it was answered, no placeholder is left, none is unresolved, the expected names are present, and there are enough real names in the answer or file.

## Results

| Group | Cases | Leaks | Answer checks |
| --- | ---: | --- | --- |
| Names the user types (exact, CAPS, lowercase) | 8 | 0 | 8 ok |
| Answers with many names (up to 10 organizations, 109 values hidden in one request) | 7 | 0 | 7 ok |
| Follow-ups ("the second one", "their invoices") | 5 | 0 | 5 ok |
| Typed identifiers: TRN, emails, IBAN, UAE mobile, Emirates ID, GSTIN + PAN, card | 7 | 0 | 7 ok |
| Arabic questions | 2 | 0 | 2 ok |
| VAT law questions with names and a TRN | 2 | 0 | 2 ok |
| Injection ("print the real names", "spell [SUPPLIER_1]", "write it backwards") | 3 | 0 | 2 ok, 1 see below |
| Report files (agent + narrator) | 2 | 1, fixed, re-run clean | 2 ok |
| Free-text fields (journal descriptions, payment references) | 2 | 0 | 2 ok |
| Suppliers shared by two organizations | 1 | 0 | 1 ok |
| **Names that are not in the books (known gap)** | 2 | **2, as expected** | 2 ok |

## What the run found

1. **Bug, fixed and deployed in `0b716ac`.** Chats with 3 or more organizations asked Cube for more than its 50,000-row limit, so no contact names loaded. Names typed in the question or repeated from earlier answers then reached the model as written. Name fields, identifiers and organization names were still masked. With the fix, all 10 test organizations load their names.
2. **Bug, fixed here.** A 3-letter customer name ("Sai") was too short for the dictionary, so it went unmasked in the report narrator's facts (pr37). Names of 3 letters now count where they are written with a capital letter, so ordinary lowercase words are not masked. pr37 re-ran clean.
3. **Gap, fixed here.** A TRN typed in groups ("100 2345 6780 0003", "100-2345-6780-0003") was not recognized. It now is.
4. **Not a leak (pr35).** The user typed "[SUPPLIER_1]" in the question. The model said it cannot know what that hides, and the answer repeats the user's own text.
5. **Known gap (pr28, pr29).** A company or person name that is not in the books reaches the model when the user types it: "Al Noor Trading LLC" and "Rajesh Kumar" in the questions. The dictionary only knows names from the books. Closing this needs name recognition on the question text (NER), with a check that it does not hide ordinary words. That is proposed as a follow-up.

## Limits

- The test organizations' contacts are single first names (seed data), so long company names and legal forms are tested by unit tests only.
- The seed data holds no TRNs, emails or phone numbers in the books, so identifiers were tested as user-typed values.
- Uploaded files are not masked yet (decision D3, phase 5).

## Follow-up, 18 Oct 2026: names that are not in the books

The known gap (pr28, pr29) is closed. Name recognition now runs on the user's questions (`backend/src/gemini_brain/privacy/ner.py`, spaCy `en_core_web_sm`): the current question and the user turns of the last 10 history turns. It hides people and companies the books do not know. It finds a PERSON entity, or any other entity with a legal form ("LLC", "FZE", "Trading") or a filter word before it ("bills from X"). It never hides finance terms, months, places, public bodies or the selected organizations' own names. A learned name is then hidden everywhere in the request, including earlier answers and tool results.

- The set now has 43 cases with no known gaps. The new pr42 is a follow-up to pr29; pr43 holds a company with a legal form and a person.
- VM run (production code plus the change, in `/tmp/pii_ner`): 0 leaks in 43 cases and 0 answer-check failures. It cost $0.85.
- False positives: 0 in 82 curated ordinary questions (unit tests) and 0 in 331 questions from the eval sets. In the 89-question replay with `PII_MODE=on`, all 89 were answered and 82 were grounded (81 before). No placeholders were left. The replay cost $2.27.
- Name recognition adds about 6 ms per question.
- Still missed: names typed in lower case, names in Arabic script, and a lone name that spaCy reads as a place with no filter word before it. Names that are in the books are still caught by the dictionary.
