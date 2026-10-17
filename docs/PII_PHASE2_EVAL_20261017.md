# PII masking, phase 2: eval on 17 Oct 2026

With `PII_MODE=on`, 1 of 163 model calls carried personal or customer data, down from 144 of 162 with masking off. That one value is an email address quoted in the FTA law passages, not customer data. Answer quality held: all 89 questions were answered, 81 answers were grounded against 79 before, and no placeholder was left in any answer.

Both runs used the same harness (`scripts/eval/replay_real_questions.py`) and the same 89 real questions from test orgs 24–33, on the VM, a few hours apart. The baseline run had masking off and the sentinel on (see `PII_LEAK_REPORT_20261017.md`).

## Leaks

| | Masking off | Masking on |
| --- | ---: | ---: |
| Model calls | 162 | 163 |
| Calls carrying PII | 144 (89%) | 1 (1%) |
| Supplier and customer names (occurrences) | 849 | 0 |
| Organization names (occurrences) | 3,650 | 0 |
| Email | 1 (FTA passage) | 1 (same FTA passage) |

The one remaining hit is in the law route's system prompt, in the official FTA passages. It is a public address, and phase 3 adds it to an allowlist.

## Quality

| | Masking off | Masking on |
| --- | ---: | ---: |
| Answered (status ok) | 89 | 89 |
| Figures grounded (verifier) | 79 | 81 |
| Placeholders left in answers | – | 0 |
| Median / p95 time | 8.7 s / 27.3 s | 8.0 s / 28.1 s |
| Bedrock cost | $2.47 | $2.50 |

By the figure screen, 76 cases scored the same, 6 better and 7 worse. I read all 7 worse cases (r040, r042, r050, r062, r065, r067, r085). None of the drops comes from masking. In each, the model took a different approach in that run:

- In r050 and r062, it added change columns. These are computed figures, which the report-only verifier cannot match.
- In r040, it skipped the AR/AP query, so the answer lists fewer ledger figures.
- In r042, the answer was shorter.
- In r085, the answer is more thorough and correct.

Real organization names appear correctly in every answer. This is the same run-to-run wording variance seen in the 10 Oct before/after runs.

## Limits of this eval

- The seed data has few supplier or customer names in answers: no overdue bills, and supplier payments in one org only. Name-heavy answers are therefore lightly tested. The planned 40-question PII red-team set covers them.
- Uploads keep the current reader (decision D3) and were not exercised. The report narrator is phase 3.

Raw outputs on the VM: `/tmp/pii_eval/out` (masking off) and `/tmp/pii_eval2/out` (masking on).
