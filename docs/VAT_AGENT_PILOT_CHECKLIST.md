# VAT agent pilot: checklist

Package 7 of `docs/VAT_AGENT_COMPLETION_PLAN.md`. One week with a few real users, on the VM (`106.51.80.81`, chat on port 3535). The aim is to see the VAT agent on real questions and real books before it is opened to everyone, and to get the thresholds signed off.

What is in the pilot: document upload and the checks on one bill (`docs/DOCUMENT_UPLOAD_PLAN.md`); the review of bills and sales invoices for duplicates and odd entries; UAE VAT law answers with FTA sources, in English and Arabic; the VAT return position, due dates and penalties.

## 1. Before day 1

| # | Task | Who | How | Done |
|---|---|---|---|---|
| 1 | Reload Cube with the latest model (customer TRN on sales, VAT settings view, updated access rules) | Owner | On the VM: `cp -a /home/cube/semantic/model /home/cube/semantic/model.bak-pre-pilot`, then `cd /opt/accutax-ai && semantic/deploy/install_rootless.sh --start`. Undo: copy the backup back and restart `cube-api` | |
| 2 | Choose 3 to 5 pilot users: at least one accountant who files VAT returns, one person who uploads bills, and Alex Morgan | Owner | Names and the organizations each one works on | |
| 3 | Pilot organizations should have **real books**, not the seed test organizations 24 to 33 (their VAT, TRNs and duplicates are made up and give misleading findings) | Owner | List of organization ids | |
| 4 | Add the pilot users to the allowlists. Today both lists hold only the test user | Owner | In `/opt/accutax-ai/.env`: add each user's email to `AGENT_PREVIEW_USERS` and `UPLOAD_USERS` (comma separated), then `systemctl restart accutax-ai-api` | |
| 5 | Exchange rates for the currencies the pilot organizations' suppliers bill in (only USD ships) | Finance | Add each currency to `backend/src/gemini_brain/config/aed_rates.json` with the Central Bank of the UAE rate and its date; push and deploy | |
| 6 | Book Alex Morgan's review of the thresholds for day 3 | Owner | `backend/src/gemini_brain/vat_review/thresholds.py`: 3 days for a likely duplicate, 3 times a supplier's usual bill, round amounts from AED 5,000, 80% for a pattern | |
| 7 | Check the logs are written | Owner | On the VM: `ls -l /opt/accutax-ai/backend/logs/agent_answers.jsonl /opt/accutax-ai/backend/logs/agent_audit.jsonl` | |
| 8 | Check the monthly knowledge-base refresh is scheduled | Owner | `systemctl list-timers vat-kb-refresh.timer` (next run 1 November 2026, 02:30) | |
| 9 | Space for uploads | Owner | `/var/lib/accutax-uploads` uses 15 MB; 61 GB free on the disk. Check `du -sh /var/lib/accutax-uploads` each day (no automatic alert yet) | |
| 10 | Send the users the question sheet (section 3) and the known limits (section 6) | Owner | Email or chat | |

## 2. Each day of the pilot

| # | Task | How |
|---|---|---|
| 1 | Run the daily report | On the VM: `cd /opt/accutax-ai/backend && sudo -u geminibrain /opt/accutax-ai/venv/bin/python scripts/pilot_report.py --days 1` (add `--users 12,15` for the pilot users only) |
| 2 | Read every answer the report lists under `need_reading` (failed, out of time, figures not grounded, tool error) | Find the full answer in `logs/agent_answers.jsonl` by its time |
| 3 | Look at `guard_events` | `injection_in_file` or `injection_in_results`: open the file or record and see whether it was an attack or an honest phrase. `unchecked_books_claim`: the agent spoke about the books without looking. `canary_hit`: the prompt leaked; read that answer. `law_answer_citations`: a law answer without a source |
| 4 | Collect the users' feedback (section 4) and sort it: wrong answer, right but unclear, slow, missing feature | One list for the week |
| 5 | Note the cost and time | The report's `cost_usd` and `seconds` |
| 6 | Fix nothing during the day unless an answer is wrong about money or the law. Collect the rest and fix it at the end of the week, with one evaluation run (see `memory`: one paid run per batch) | |

## 3. Question sheet for the pilot users

Ask in your own words; these are examples. Try Arabic for the law questions.

**Upload a bill** (PDF, photo, Word, Excel or CSV; up to 3 files of 10 MB)
- Read this bill. Is it a valid tax invoice? Can I claim the VAT?
- Is this bill already recorded in our books?
- Does this supplier statement match our books? List every difference.
- Compare these bills in one table. Export them to Excel.

**Review the books**
- Are there any duplicate bills this quarter?
- Is the VAT on my purchase bills right this year?
- Do my sales invoices show valid customer TRNs?
- Check my sales invoices for problems. Export the findings to Excel.

**VAT law**
- Can we recover input VAT on entertaining customers?
- Is the first sale of a new residential building subject to VAT?
- ما هو حد التسجيل الإلزامي لضريبة القيمة المضافة؟

**VAT return and penalties**
- How much VAT do we owe for the last quarter, and when is it due?
- When is our next VAT deadline?
- We paid AED 12,000 of VAT due on 28 July 2026 on 16 October 2026. What is the penalty?

## 4. Feedback from each user

For each answer worth a remark: the question, the time, thumbs up or down, and one line on why (wrong figure, wrong law, missed a problem, flagged something that is fine, too slow, unclear). A screenshot helps. Collect it in one shared sheet.

## 5. Exit criteria (end of the week)

| Criterion | Target |
|---|---|
| Answers rated right by the pilot users | 90% or more |
| A wrong claim that a bill is or is not in the books | none |
| Data of an organization shown to a user outside it | none |
| Law answers that cite an FTA source | all |
| Findings the users marked as false alarms | under 30%, and Alex Morgan has signed off the thresholds (adjusted if needed) |
| Answer time | median under 15 s; the slowest 5% under 45 s |
| Cost | median under USD 0.06 per answer |
| Prompt leak (`canary_hit`) | none |

If they are met: widen the allowlists to all users. If not: fix, run the evaluations (`scripts/eval/vat_review_eval.py`, `vat_law_eval.py`, `upload_eval.py`) and run a second pilot week.

## 6. Known limits to tell the users

- The VAT return figures come from the documents in the books, not from a filed return: the books hold no filed returns.
- Quarterly tax periods are assumed to be calendar quarters unless you say your quarter group (the FTA sets it on the registration certificate).
- Reverse-charge amounts are not recorded in the books, so the agent cannot report them.
- A finding (a likely duplicate, an odd amount) is a suggestion to check, not a fact.
- Penalties are calculated for due dates from 14 April 2026, under the current rules only.
- A bill in a currency other than USD is not converted to AED until finance adds its rate.
- Reminders before a due date are not sent yet; ask the agent.

## 7. Rollback

| What | How |
|---|---|
| Stop the pilot for one user | Remove the email from `AGENT_PREVIEW_USERS` and `UPLOAD_USERS`; restart `accutax-ai-api` |
| Turn uploads off for everyone | `UPLOADS_ENABLED=false`; restart |
| Undo the latest code | On the VM: `git log --oneline` in `/opt/accutax-ai`, `git checkout <previous commit>`, restart |
| Undo the Cube reload | Copy `/home/cube/semantic/model.bak-pre-pilot` back and restart `cube-api` |
| Knowledge base | The previous builds stay; write the earlier build's name into `CURRENT` in `VAT_KB_DIR` |
