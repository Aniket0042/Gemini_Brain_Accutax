# Part C: the AR / Receivables bot

Status: built 2026-11-03, not yet committed or deployed. Written 2026-11-03.
Builds on `docs/BOT_AGENTS_AND_AP_PLAN.md` (Part A made bots agent-agnostic; Part B is the AP bot, deployed as
429ffcf). Read sections 0.3 to 0.5 of that plan first: the same rules, commands and words apply here.

Like the AP plan, this is written so that a developer or a smaller model can build it one task at a time. Each task
says what to change, in which file, with which names, how to test it, and when it is done.

---

## 0. Read this first

### 0.1 What the AR bot does, in one paragraph

It watches what customers owe. On a schedule it reads the open sales invoices and tells the user which invoices just
went overdue, which fall due in the next few days (time to send a reminder), which customers owe a lot of old money,
which new customers got a large first invoice (credit risk), and which invoices look issued twice. It only reads the
books. It never sends a reminder, changes an invoice or records a payment.

### 0.2 The data, checked on the VM on 2026-11-03

A read-only Cube query on the `receivables` view for test orgs 24 to 33:

| Org | Not yet due | 1-30 days | 31-60 days | 61-90 days | Over 90 days | No due date |
|---|---|---|---|---|---|---|
| 27 | 32 (AED 161,739) | 46 (243,570) | 11 (72,006) | 35 (324,318) | **2,090 (14,049,375)** | 0 |
| 28 | 1 | 0 | 7 | 15 | 1,250 | 0 |
| 26, 29 to 33 | 1 to 10 each | 0 to 7 | 0 to 9 | 0 to 17 | 275 to 599 | 0 |
| 24, 25 | no open invoices | | | | | |

What this means for the design:

1. **Unlike bills, sales invoices have due dates.** Overdue and due soon will find real items on org 27, so AR is a
   better demo than AP for the time-based checks.
2. **Most of the debt is very old.** Org 27 has 2,090 invoices more than 90 days overdue. One finding per overdue
   invoice would put about 2,200 items in the inbox on the first run. So the bot reports old debt **per customer**
   (one finding per customer), and only recent problems **per invoice** (C1).
3. **Few customers, many invoices.** Org 27 has six customers in a month; the largest, "Arjun", owes AED 1,277,895 on
   209 invoices. A per-customer finding is short and useful.
4. **The Cube row limit is 500.** The per-invoice query must be narrow (recent overdue and due soon only); the
   per-customer query must aggregate (customer by aging bucket), never list invoices.

### 0.3 What AR needs that AP did not

| | AP bot | AR bot |
|---|---|---|
| Open-document view | `payables` (`vendor_name`) | `receivables` (`customer_name`, `aging_bucket`) |
| Review side | `review_vat` on purchases | `review_vat` on sales (same rules; the wording already says "invoice") |
| Overdue findings | one per bill, any age | one per invoice only while 1 to 30 days overdue; older debt is one finding per customer |
| A finding that leaves the scan | means paid | may mean paid **or** aged past 30 days, so "fixed" must be confirmed (C4) |
| Money figure on the stake card | could be paid twice | overdue to collect; duplicates show "could be billed twice" |
| Monitor event | `bill.saved` | `invoice.saved` |

### 0.4 No database change

`ai_bots.agent`, `ai_bot_findings.rule` and `ai_bot_findings.detail` already hold everything. If a task seems to need
DDL, stop and ask (`docs/BOT_AGENTS_AND_AP_PLAN.md` 0.3 rule 1).

---

## C1. The checks (watches)

| Watch id | Label | kind | Rules | Default on |
|---|---|---|---|---|
| `invoice_overdue` | An invoice just went overdue | state | `invoice_overdue` | yes |
| `invoice_due_soon` | An invoice is due soon | state | `invoice_due_soon` | yes |
| `customer_overdue` | A customer owes a lot of old money | state | `customer_overdue` | yes |
| `new_customer` | A large first invoice to a new customer | document | `new_customer_large` | yes |
| `duplicate_invoice` | An invoice looks issued twice | document | `same_number`, `same_total_near_date`, `similar_vendor_same_day` (sales side) | yes |
| `unusual_invoice` | An invoice far above the customer's usual amount | document | `large_for_supplier`, `large_one_off_supplier` (sales side; plus `round_amount` on "more") | no |
| `invoice_missing_details` | An invoice is missing its customer or number, or is dated in the future | document | `missing_supplier`, `missing_number`, `future_date` (sales side) | no |

The review rules keep their internal names (`large_for_supplier` and so on); `review_vat` with `side: "sales"` already
words them for customers (`vat_review/wording.py`). VAT-rate checks on sales stay with the VAT bot.

**Thresholds by sensitivity** (finance must sign these off, as for VAT and AP):

| | fewer | balanced | more |
|---|---|---|---|
| Invoice overdue: at least N days past due (and at most 30) | 7 | 1 | 1 |
| Invoice due soon: due within N days | 2 | 3 | 7 |
| Customer overdue: overdue total at least | 50,000 | 10,000 | 2,000 |
| New customer: first invoice at least | 50,000 | 20,000 | 5,000 |

The 30-day window for invoice findings is fixed (`RECENT_OVERDUE_DAYS = 30`), not a sensitivity setting.

**Severity:**

- `invoice_overdue`: `medium`; `high` when the invoice is 15 or more days overdue.
- `invoice_due_soon`: `low`.
- `customer_overdue`: `high` when any of the customer's debt is over 90 days, `medium` when over 30, else `low`.
- `new_customer_large`: `medium` when at least 4 times the threshold, else `low`.

**Done when:** the table is in `bots/agents_ar.py` (C2) and its unit test checks the ids are unique and every rule is
known to the engine.

## C2. The AR agent (`bots/agents_ar.py`)

Copy the shape of `bots/agents_ap.py`. Register `BotAgent(key="ar", name="Accounts receivable", ...)` with:

- `watches` from C1, with `desc` and `bar` text (short, sentence case). Bars: "Just overdue", "Due soon", "Owes old
  money", "New customer", "Issued twice", "Unusually large", "Missing details".
- `schedule_kinds = ("daily", "weekly", "monthly")`.
- `credits_per_run = 90` (the same flat figure as AP until decision 4 of the AP plan is settled).
- `copy`:
  - default name "Collections Watch"; label "AR agent";
  - example chips: "Every Monday, tell me which customers are overdue and what is due this week", "Every morning,
    tell me about invoices that just went overdue", "Warn me about a large invoice to a new customer";
  - `stake_label` "Overdue to collect"; `stake_note` "What customers owe past the due date, from the receivables
    view."; `problem_word` "problems"; `readonly_rule` "Send reminders, change invoices or record payments";
  - `rule_titles`: `invoice_overdue` "Invoice just went overdue", `invoice_due_soon` "Invoice is due soon",
    `customer_overdue` "Customer owes old money", `new_customer_large` "Large invoice to a new customer", plus the
    sales-side review titles ("Invoice may be issued twice", and so on);
  - `dismiss_labels`: `invoice_overdue`, `invoice_due_soon` and `customer_overdue` "I'll chase it";
  - `fixed_text`: "Invoice {number} to {party} is paid ({day})"; for `customer_overdue`, "{party} has nothing
    overdue now ({day})".
- `columns`: Invoice, Date, Due, Customer, Amount, Outstanding. A `customer_overdue` finding shows its own table
  (C6).
- `summary_cards`: next run; open problems; **overdue to collect** (amount and count); **due in the next 7 days**
  (amount and count).
- `draft_prompt` and `fallback_template` (C5).

**Done when:** `GET /bots/agents` lists `ar`, and `test_bot_agents.py` passes with an AR case added.

## C3. Share the open-documents scan (`bots/open_docs_scan.py`)

`bots/payables_scan.py` already does overdue, due soon, new party and the position box for bills. Do not copy it.
Move its body into `bots/open_docs_scan.py` with a side table, and keep `payables_scan.py` as thin wrappers so AP and
its 16 tests do not change:

```python
SIDES = {
    "payables":    {"view": "payables",    "party": "vendor_name",   "flow": "purchases", "doc": "bill",
                    "flow_party": "vendor_name",   "count": "bill_count",    "net": "net_purchases", "vat": "input_vat"},
    "receivables": {"view": "receivables", "party": "customer_name", "flow": "sales",     "doc": "invoice",
                    "flow_party": "customer_name", "count": "invoice_count", "net": "net_sales",     "vat": "output_vat"},
}
```

Functions (each takes `side` first):

- `open_docs(side, org_id, today, due_from, due_by, ...)`: as `open_bills`, with a lower bound too. AR asks for
  `due_date` from today minus 30 days to today plus `due_soon_days` (filter `inDateRange`). Order by due date
  descending for AR so that, if the 500-row limit is hit, the most recent are kept, and say the scan is not complete.
- `due_items(side, ...)`: as today, with rule names from the side (`bill_overdue` or `invoice_overdue`) and the
  sentence words ("bill"/"invoice", "supplier"/"customer"). AR passes `max_overdue_days=30`.
- `new_party_items(side, ...)`: as `new_supplier_items`. On sales, `scan_bills(..., side="sales")` returns the
  customer in `vendor_name` (see `vat_review/scan.py`), so the loop is unchanged. Rule `new_customer_large`, key
  `sha256("ar|new_customer|org|customer")[:24]`.
- `position(side, ...)`: as today, plus for receivables `over_90_days` (amount and count, from `aging_bucket = 'Over
  90 days'`).

Keys stay as they are for AP (`ap|bill|...`). AR keys: `sha256("ar|invoice|org|document_id")[:24]` for invoice
findings and `sha256("ar|customer|org|customer")[:24]` for customer findings.

New for AR only, `customer_items(org_id, today, *, min_amount, ...)`: one query on `receivables`, dimensions
`customer_name`, `aging_bucket`, `currency`, `organization_name`, measures `outstanding`, `open_count`,
`overdue_amount`, `overdue_count`, filter `aging_bucket` not in ("Not yet due", "No due date"). Group the rows by
customer in code. One finding per customer whose `overdue_amount` is at least `min_amount`:

- `why`, written in code: "Arjun owes AED 1,277,895.00 on 209 overdue invoices; AED 1,201,000.00 of it is more than
  90 days old."
- `bill` holds the customer and the totals (`vendor_name`, `outstanding`, `overdue_count`), and `buckets` holds the
  amount and count per aging bucket.

If this query hits 500 rows (more than about 100 customers with overdue debt), keep the largest and say the scan is
not complete.

**Tests** (`tests/unit/test_open_docs_scan.py`, fake `cube_client.load` as in `test_ap_engine.py`):

- an invoice 3 days overdue is found, one 45 days overdue is not;
- one due in 2 days is found on "balanced", one due in 6 days only on "more";
- a customer with AED 12,000 overdue is found on "balanced" and not on "fewer";
- severity follows the oldest bucket;
- the customer query is grouped correctly when one customer has three buckets;
- every test in `test_ap_engine.py` still passes unchanged.

## C4. The engine (`bots/ar_engine.py`) and confirming "fixed"

`ArEngine` copies `ApEngine`. `calls(watch_ids, sensitivity)` returns up to four calls:

```python
{"kind": "review", "side": "sales", "mode": "all", "rules": [...]}         # duplicates, missing, unusual
{"kind": "receivables", "side": "sales", "rules": ["invoice_overdue", "invoice_due_soon"],
 "overdue_days": N, "due_soon_days": M}
{"kind": "customers", "side": "sales", "min_amount": X}
{"kind": "new_customer", "side": "sales", "min_amount": Y}
```

`run` dispatches as `ApEngine.run` does. `period` is the last 90 days (for review and new customer). `position`
calls `open_docs_scan.position("receivables", ...)`; `summarize_position` accepts `kind == "receivables"`.

`stake(rule, detail)`: duplicates, the amount plus VAT of every copy after the first ("could be billed twice"); every
other rule 0. The stake card shows the position's overdue amount instead (C6).

**Confirming "fixed".** Today `run.py` marks every finding that a complete run no longer found as `fixed`. For AR
that is wrong in one case: an invoice that ages from 30 to 31 days overdue leaves the invoice scan but is not paid.
Add an optional engine hook:

```python
def confirm_fixed(self, findings: Sequence[Dict[str, Any]], *, org_ids, subject, deadline) -> set[str]:
    """Of these findings that the run no longer found, the keys that are really fixed."""
```

- `run.py`: before marking findings fixed, call `engine.confirm_fixed(missing, ...)` when the engine has it;
  otherwise keep today's behaviour (VAT and AP unchanged).
- `ArEngine.confirm_fixed`: for `invoice_overdue` and `invoice_due_soon` findings, one `receivables` query filtered
  to their `document_id`s (from the finding's detail, at most 100 per query). An invoice still open is **not** fixed:
  the finding stays open, and its customer is usually covered by a `customer_overdue` finding too. Every other rule
  is fixed when not found.

**Tests** (`tests/unit/test_ar_engine.py`): the calls per watch and sensitivity; `exclude` drops a key; `unlimited`
passes through; position; an invoice that ages past 30 days stays open across two runs; a paid invoice becomes
`fixed`; a customer whose overdue total falls below the threshold becomes `fixed`. In `test_bot_run.py`: an engine
without `confirm_fixed` behaves as before.

## C5. Templates and the sentence to draft

| id | title | summary | draft |
|---|---|---|---|
| `ar_weekly_collections` | Weekly collections check | Every Monday: who is overdue, what falls due this week, and invoices issued twice. | name "Collections Watch"; weekly Monday 08:00; default watches; inbox + bell |
| `ar_reminder_prep` | Reminder prep | Every morning: invoices due in the next 7 days and any that just went overdue. | name "Reminder Prep"; daily 09:00; `invoice_due_soon`, `invoice_overdue`; sensitivity `more` |
| `ar_month_end_aging` | Month-end aging | On the 1st: customers with old debt and the receivables total. | name "Month-end Aging"; monthly day 1 08:00; `customer_overdue`; inbox + bell |

**Draft prompt** (`DRAFT_PROMPT` in `agents_ar.py`): the AP prompt with these mappings:

| What people say | Maps to |
|---|---|
| "overdue", "late", "unpaid", "who hasn't paid" | `invoice_overdue` and `customer_overdue` |
| "due this week", "due soon", "send reminders" | `invoice_due_soon` |
| "aging", "old debt", "who owes the most" | `customer_overdue` |
| "new customer", "credit risk" | `new_customer` |
| "duplicate", "billed twice" | `duplicate_invoice` |
| "every morning" | daily |
| "month end" | monthly, day 1 |

The same rules as AP apply: never invent an organization, never put figures in a field, ask at most three questions,
and never produce `before_deadline` or `after_period_end`.

**Done when:** each template validates with no unfixed issues (`test_bot_agents.py`).

## C6. Screens

Most of the screens already read the agent's `copy` and `columns`, so the work is the parts that are new for AR:

- **Create page:** the agent switch gets a third button, "Accounts receivable" (from `GET /bots/agents`, no code
  change). Check it still fits on one line at 375 px wide; if not, let it wrap and centre.
- **Inbox, customer finding** (`BotInbox.jsx`): when `rule === "customer_overdue"`, show the sentence and an aging
  table instead of the bill table: Bucket, Invoices, Amount (1-30, 31-60, 61-90, over 90), from `detail.buckets`.
  Amount on the card is the overdue total.
- **Inbox stake card:** for `ar`, show "Overdue to collect" from the latest run's position (`position.overdue`), not a
  sum of findings, so customer and invoice findings are not counted twice. `inbox.at_stake_by_agent` gets the AR
  amount from the position.
- **Intro in chat** (`BotIntroMessage.jsx`): when `position.kind === "receivables"`, the stat tiles are "invoices
  checked", "just overdue", "customers to chase", "due in 7 days" (amount); the box is "Receivables" with rows Due in
  7 days, Overdue, of which over 90 days, and No due date when not 0. Closing line: "I only read your books; I never
  send reminders or change an invoice."
- **Bots home** (`BotsHome.jsx`): an "Overdue to collect" stat when an AR bot exists, as the AP "Bills due in 7 days"
  one does.
- **"Make this a bot"** from an AR chat answer: works once `ar` is in the agents list (`botAgentKey`); check that the
  chat AR agent's answers carry `agent: "ar"`. If the chat profile key differs, map it in `botAgentKey`.

**Done when:** `npm run build` passes and the local preview with sample data shows the AR create page, a customer
finding, an invoice finding and the intro, next to the AP and VAT ones for comparison.

## C7. The monitor for AR

An `invoice.saved` event runs the AR bot's document watches only (`duplicate_invoice`, `unusual_invoice`,
`invoice_missing_details`, `new_customer`); `monitor.handle_document_event` already skips state watches. A `bill.saved`
event does nothing for an AR bot. Add the monitor sentences for the AR rules (the same place AP's were added).

**Tests** (`test_bot_monitor.py`): an AR bot on `invoice.saved` runs only document watches; on `bill.saved` it does
nothing.

## C8. Real data check and one paid run

1. On the VM, in process, read only, org 27: build the three templates' drafts and run `try_period` for each. Expected
   from 0.2: about 46 invoices just overdue (fewer on "fewer"), a handful due within 7 days, about six customers with
   old debt. Look at five items by eye against the Accutax app (customer "Arjun" is a good first check).
2. Time the scheduled run on org 27 and on all of orgs 24 to 33; it must finish inside the run deadline.
3. One paid evaluation: `scripts/eval/bot_sentences.py --agent ar` with `tests/data/bot_sentences_ar.json` (20
   sentences: overdue, due soon, aging, who owes the most, new customer, duplicates, daily, monthly, two watches, an
   organization named and not found, vague, three not about a bot). Target: schedule and watches right on at least 18
   of 20, no invented organization. Put the results in this document under C9.

## C9. Results

Run 2026-11-03 on the VM, in process and read only, from the uncommitted Part C code.

**Real data, org 27** (the three templates; review and new customer over the last 90 days, receivables as of today):

| Template | Invoices checked | Duplicate groups | Just overdue | Due soon | Customers with old debt | New customer | Time |
|---|---|---|---|---|---|---|---|
| `ar_weekly_collections` | 2,182 | 95 | 46 | 8 | 38 | 0 | 1.5 s |
| `ar_reminder_prep` (sensitivity "more") | 64 | | 46 | 18 | | | 0.3 s |
| `ar_month_end_aging` | 2,182 | | | | 38 | | 0.3 s |

Receivables box for org 27: AED 14,689,269 overdue on 2,182 invoices, of which AED 14,049,375 (2,090 invoices) more
than 90 days; AED 95,310 due in the next 7 days (18 invoices); no invoice without a due date. The largest customer
finding is "Arjun owes AED 1,277,895.00 on 209 overdue invoices", which matches the check in 0.2.

All of orgs 24 to 33 in one bot: 5.5 s, 389 findings (71 just overdue, 16 due soon, 302 customers) and 208 duplicate
groups, every scan complete. AP on org 27 is unchanged after the shared scan (171 bills, 41 duplicate groups).

**Sentence to draft, one paid run** (`scripts/eval/bot_sentences.py --agent ar`, 20 sentences in
`tests/data/bot_sentences_ar.json`): **20 of 20** right on schedule and checks, **0** invented organizations
(target: 18 of 20, 0).

**To look at before a demo:** the 95 duplicate groups on org 27 are mostly the seed's recurring invoices (for example
the same AED 5,226.00 to "Lakshit" every day), which "same total a few days apart" reads as issued twice. The same
rule found AP's 41 groups. Real books with recurring invoices will see this too; decision 7 below.

Built differently from the text above: the customer finding counts every overdue bucket (1-30 days included), as
C3 says; the chat has no AR agent, so "Make this a bot" does not appear under answers and "Ask AccuTax" on an AR
finding goes to the general agent; the agent switch drops its label and tightens below 480 px so three agents fit.

---

## Order, size and deploys

| Step | Tasks | Rough size | Deploy |
|---|---|---|---|
| 1 | C3 (shared scan, AP unchanged) | 0.5 day | Backend; check AP on org 27 still gives 171 bills and 41 duplicate groups |
| 2 | C1, C2, C4, C5, C7 | 1.5 days | Backend; AR drafts work through the API |
| 3 | C6 | 1 day | Frontend; AR bots can be created |
| 4 | C8 | 0.5 day | One paid run and the real-data check |

No database change in any step. Run the unit tests after every task; one paid evaluation at the end only.

## Open decisions (need the person)

| # | Decision | Default in this plan |
|---|---|---|
| 1 | Thresholds in C1 | the table in C1; finance sign-off still needed |
| 2 | Invoice findings only for the first 30 days overdue; older debt per customer | yes; change `RECENT_OVERDUE_DAYS` if finance wants 60 |
| 3 | Should a dismissed customer finding come back when that customer's overdue amount grows a lot (for example doubles)? | No: dismissed stays dismissed, as for AP; revisit after the demo |
| 4 | Credit limits: "a customer is over their credit limit" is a strong AR check, but it needs a credit-limit field on contacts | Not in this plan; check the contacts table and add to the DBA list if missing |
| 5 | Drafting reminder emails for the user to send (still read only: the bot writes text, the person sends) | Not in this plan; needs the email service and the PII rules in `docs/` (no customer contact details to models) |
| 6 | Name shown to users: "Accounts receivable" or "AR / Collections" | "Accounts receivable" |
| 7 | Recurring invoices (same customer, same amount, every day or week) look issued twice. Skip a customer's regular series in the duplicate check, or leave it to "I'll chase it"/"Not a problem"? | Unchanged for now; needs a rule change in `vat_review/duplicates.py`, shared with VAT and AP |
| 8 | An AR chat agent (profile `ar` in `agent/profiles.py`) so answers get "Make this a bot" and findings get a specialist | Not in this plan |
