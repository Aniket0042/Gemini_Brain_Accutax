# Accutax bots: plan

Written 2026-10-28. Status: proposed. Builds on `docs/ACCUTAX_AGENTS_DESIGN.md` (phase 0 is live: four specialist profiles, the live run card, the team home and the findings inbox, all without a database change). This plan covers the eight planned agents, the two bot types, and how a user creates and configures bots the way OpenAI dots and xAI Grok Bot let them.

## What we are building

An Accutax user gets a team of bots that work for their organizations without being asked. Each bot has a name, one job, a schedule or a watch list, rules about what it may do, and a memory of what the user told it. The user can create one from a template, by describing it in a sentence, or from any chat answer ("do this every Monday"). The bot introduces itself with a first run, then reports in the inbox, by notification or by email, and asks before it changes anything.

What we take from the two products:

| From | Idea | In Accutax |
|---|---|---|
| dots | A named agent that works between conversations toward the user's goals | A bot per job, running on its own schedule or watching for events |
| dots | Proactive research with read-only tools | Background runs are read-only; code finds the problem, the model explains it |
| dots | Custom Rules: allow, require approval, block | Per bot and action: allow, ask, block |
| dots | Activity View; the agent messages you with progress and decisions | Bot activity timeline, inbox, approval cards, notifications |
| dots | Learns preferences from feedback | Memory from dismissed and accepted findings, editable by the user |
| dots | "Let it introduce itself" | The first run after creation is an introduction: what it checked, what it found, what it will watch |
| Grok Bot | Create a bot with a name, one main job and a description | Bot builder: template or one-sentence description, then an editable setup card |
| Grok Bot | Routines on a schedule or after an event | Scheduled bots and monitor bots (below) |
| Grok Bot | Skills: saved workflows with steps and approval limits | Each agent's plan of steps; later, a saved chat answer becomes a routine |
| Grok Bot | Several bots that hand work to each other | Bots hand a finding to another bot (AP asks Reconciliation whether a bill was paid) |

What we do not take: a cloud computer or browser per bot, bots sharing credentials, write actions without approval, and free-form tool access. Accutax bots act only through governed tools on the organization's own data.

## Words used in this plan

- **Agent**: one of the eight jobs, defined in code (`agent/profiles.py`): its tools, plan, checks and allowed actions. The user does not edit an agent.
- **Bot**: a user's configured copy of an agent for some organizations: name, schedule or monitors, rules, delivery and memory. A user can have several bots of one agent (for example a weekly VAT digest and a VAT monitor).
- **Scheduled bot**: runs at times the user sets and delivers a digest or a file.
- **Monitor bot**: watches for conditions (an event, a threshold, a change, something missing) and alerts when one is met.
- **Run**: one execution of a bot. **Finding**: one problem or result a run brings to the user. **Inbox**: where findings wait.

## The two bot types

### Scheduled bots (user-set triggers)

The user picks when: "every Monday at 8:00", "on the 1st of each month", "3 days before each VAT return is due", "after each period close". The bot runs its agent's plan and delivers a result: a digest in the inbox, an email, or a file (PDF, Excel).

Schedules are written in plain words and shown back as a preview: "Next runs: Mon 3 Nov 08:00, Mon 10 Nov 08:00, Mon 17 Nov 08:00 (Asia/Dubai)". Date-relative triggers (before a deadline, after a period close) are computed by code from the organization's settings.

### Monitor bots (anomaly monitoring)

The user picks what to watch from a catalog of checks for that agent, and sets thresholds with sensible defaults ("bills above AED 50,000 from a supplier we have not paid before"). Four kinds of check:

| Kind | Example | How it runs |
|---|---|---|
| Event | A new bill matches an existing one (duplicate) | Accutax enqueues an event when a bill is saved; the bot checks that one bill |
| Threshold | Cash forecast goes below AED 100,000 in the next 8 weeks | Polled on an interval (hourly or daily) with a Cube query |
| Change | Gross margin falls more than 5 points from last month | Compared with the previous period, daily |
| Absence | No sales invoice recorded for 3 working days; a challan not invoiced after 14 days | Polled daily |

Monitors are code first: each check is a Cube query or a rule over one document, so watching costs almost nothing. The model runs only when a check finds something new, to write the alert in plain words and cite the law where it applies. Each alert has a key (like today's review findings), so the same problem is never raised twice, and a dismissed one stays dismissed.

Sensitivity is a setting per monitor (more alerts or fewer), mapped to the check's threshold, so a user who finds a monitor noisy can turn it down without understanding the rule.

## The eight agents

Readiness, as of 2026-10-28: **Ready** means the tools and data exist; **Build** means the data exists but a tool or check must be written; **Check** means the data needs confirming; **Blocked** needs a database field or an integration.

| Agent | Job | Scheduled bot templates | Monitor templates | Actions it may ask to take | Readiness |
|---|---|---|---|---|---|
| VAT | Duplicates and VAT problems on bills and invoices, return position, law, penalties | Weekly review digest; return worksheet at period end; reminders 14, 7 and 1 days before the return is due | New bill with VAT not 5% or 0%; sales invoice with no output VAT; large input VAT on one bill | Export findings (no write) | Ready (`review_vat`, `vat_return`, `vat_penalty`, `search_vat_kb`). Supplier TRN check Blocked (no field) |
| Accounts Receivable | Who owes what, ageing, collections | Weekly ageing with the top overdue customers; month-end collections report | Invoice crosses 30, 60 or 90 days overdue; a customer's overdue total passes a limit; a large invoice due in 3 days | Draft a reminder email to a customer (ask) | Build: receivables view has due dates and overdue amounts; ageing and reminder tools to write; sending email needs Accutax's mail service |
| Accounts Payable | Duplicate and odd bills, bills due, uploaded and emailed bills | Weekly pay-run proposal (bills due next 2 weeks against cash); weekly duplicate scan | A new bill duplicates an existing one; bill above a limit from a new supplier; bill due in 3 days and unpaid; a bill received in the email inbox is checked on arrival | Flag a bill for review (ask) | Ready for duplicates (`review_vat`); payables view has due dates, so the pay-run is Build; email-inbox hook is Build |
| Reconciliation | Matching bank lines to invoices, bills and payments | Daily match suggestions; weekly unreconciled summary | Uncategorized bank transactions older than 7 days; bank balance differs from ledger cash; large unmatched deposit | Accept a suggested match (ask) | Build: `bank_transactions` view and the reconciliation module (bank transaction matches) exist; a match-suggestion tool is new |
| Reporting | Month-end packs and KPI flashes | Month-end P&L pack (PDF and Excel) after period close; weekly KPI flash | Margin drops more than X points; an expense category jumps more than Y% | Email the pack to chosen recipients (ask) | Ready for the pack (live); KPI flash and change monitors are Build |
| Compliance | Deadlines and filings | Monthly compliance calendar | VAT return due in 14 or 7 days; VAT settings missing; corporate tax registration or return due; trade licence expiry | None | Ready for VAT dates (`vat_return`); corporate tax needs a knowledge base and the financial year end; licence dates Blocked (no field) |
| Invoice | Sales invoices that are right before they go out | Weekly invoice quality report | New invoice missing an item a UAE tax invoice must show; numbering gap; same invoice issued twice; a recurring invoice failed (scheduler dead-letter queue) | Draft a corrected or recurring invoice (ask) | Build: sales view and the bill checks from uploads can be reused for invoices; the dead-letter queue exists in the Nest scheduler |
| Delivery Challan | Goods delivered but not billed | Weekly unbilled challans list | Challan not invoiced after N days (revenue leakage); invoiced quantity differs from the challan | Draft an invoice from a challan (ask) | Check: challans are an income type (`DELIVERY_CHALLAN`); how a challan links to its invoice must be confirmed before the leakage check can be built |

Bots hand work to each other where it helps: the AP bot asks the Reconciliation bot whether a flagged bill was already paid; the Delivery Challan bot passes an unbilled challan to the Invoice bot to draft the invoice; the Compliance bot asks the VAT bot for the return position before a deadline reminder.

## The user experience

### 1. The team page

The start screen becomes the team page (the team home already live, made permanent and shared):

- A briefing at the top: "Since yesterday: AP flagged 2 likely duplicates (AED 13,167). Cash goes below your floor in the week of 9 Nov. VAT return due in 12 days."
- A card per bot: name and avatar, status dot (working, all clear, needs you, paused), what it last did and when, next run, open findings count, Run now, Pause.
- "Create a bot" as the first card.

### 2. Creating a bot

Three ways in, all ending in the same setup card:

1. **Describe it.** The user writes one sentence: "Every Monday morning check my bills for duplicates and email me a summary." The AI fills the setup card: agent AP, scheduled, Monday 08:00, duplicate scan, deliver by email and inbox. Nothing is saved until the user confirms.
2. **Pick a template.** A gallery of the templates in the table above, grouped by agent.
3. **From a chat answer.** Under any agent answer: "Make this a bot". The question becomes the bot's task and the user picks when it runs.

The setup card shows, in this order:

| Section | What the user sets |
|---|---|
| Name and look | Name ("Bill Guard"), colour, icon |
| Job | The agent, and a one-line description in the user's words |
| Organizations | Which of their organizations it covers |
| When | Schedule in plain words with the next 3 runs shown, or the monitors with their thresholds and sensitivity |
| Deliver to | Inbox (always), notification bell, email digest; later WhatsApp and Teams. Instant or digest; quiet hours |
| Rules | For each action the agent can take: allow, ask first (default), or never |
| Notes for the bot | Extra instructions ("ignore rent bills from Al Futtaim, they repeat monthly"), saved to its memory |
| Budget | Most credits per month it may use, with the estimate for this setup |

Before turning the bot on, the user can press **Try it on last month**: the bot runs its checks on the previous month's data and shows what it would have reported. This is the moment the user sees the value, and it lets them tune thresholds before any alert reaches them.

### 3. The first run: the bot introduces itself

On creation the bot runs once and posts an introduction to the inbox: what it is for, what it checked, what it found, what it will watch and when it runs next. For example: "I'm Bill Guard. I checked 478 bills from this year: 108 likely duplicate groups (AED 1.03M counted twice if all are real). I'll check every new bill as it's saved, and send you a summary every Monday at 8:00."

### 4. The bot's own page

Tabs:

- **Overview**: status, next run, last result, open findings, Run now, Pause.
- **Activity**: every run as a timeline (the live run card, kept): when, what it checked, how long, what it cost, what it found.
- **Findings**: the inbox filtered to this bot, with the actions already built (open bills, not a problem, ask why) plus snooze and assign.
- **Chat**: a thread with this bot, which keeps its context.
- **Memory**: what it has learned ("Rent bills from Al Futtaim repeat monthly: not duplicates"; "You file VAT quarterly"), each line editable or removable.
- **Settings**: the setup card.

### 5. The inbox and notifications

One inbox for all bots, newest first, filtered by bot, organization and severity. Each finding: what, why in plain words, the evidence (bills, figures), the law where it applies, and actions. A notification bell counts what needs the user; approvals sit at the top. The email digest groups findings per bot so a user who never opens the app still gets the value.

Every alert has "Why did I get this?": the monitor, the threshold, and the data that crossed it, with a link to change the threshold.

### 6. Approvals

When a bot wants to act (send a reminder, accept a bank match, draft an invoice), an approval card shows exactly what will happen, with Approve, Edit or Reject. Nothing is written until a user with the right role approves. The approval and the user are in the audit log. Rules can later allow an action outright, but sending anything outside Accutax always asks.

### 7. Learning from feedback

Dismissals (today kept in the browser) move to the database, so they are shared across the team and devices. A dismissed pattern lowers or skips that check for that supplier or customer. "Less like this" on an alert lowers the monitor's sensitivity. Notes the user writes become memory lines. The bot uses memory in code first (skip a known pattern) and in its prompt second (a few lines of preferences).

## How it works

### Components

- **Agents** stay in code (`agent/profiles.py`), each with its tools, plan, checks catalog and actions.
- **Bots, triggers, runs, findings, memory, rules and notifications** live in new tables (below).
- **Scheduler**: the Nest backend's BullMQ, which already runs the recurring invoice scanner and notifications. One repeatable job per scheduled trigger; a polling job per monitor interval; event jobs from Accutax services.
- **Events**: Accutax enqueues an `ai-bot-event` job when a bill, invoice, challan or payment is created or changed, when bank transactions are imported, when a bill arrives in the email inbox, and when a period is closed. The AI service matches the event to the bots watching it.
- **Monitor engine** (AI service): runs the checks for a bot as code (Cube queries, document rules), compares with open findings by key, and calls the model only for new findings.
- **Run endpoint**: `POST /api/v1/bots/{id}/run`, called by the scheduler with a service token, never by the browser. Background runs are read-only by code (`ToolContext.read_only`).
- **Delivery**: findings to the inbox tables; notifications through the Nest notification module; email digests through Accutax's mail service.

### Tables (need the DBA's go-ahead)

These replace the `ai_agents` sketch in the design doc; the names change to bots because one agent can have several bots.

| Table | Holds |
|---|---|
| `ai_bots` | id, organization ids, owner, agent key, name, colour, icon, description, enabled, paused, budget, created by and when |
| `ai_bot_triggers` | bot id, kind (schedule, event, poll), schedule in plain words and as cron, timezone, event name, check key, threshold and sensitivity, enabled |
| `ai_bot_runs` | bot id, trigger id, started and finished, status (done, failed, skipped for budget, nothing new), steps, credits, summary |
| `ai_bot_findings` | bot id, run id, organization id, finding key, severity, title, evidence, status (new, seen, dismissed, snoozed, done), decided by and when, snooze until; unique on bot and key |
| `ai_bot_memory` | bot id, organization id, kind (pattern, preference, note), text, source (dismissal, user note), created by, expires |
| `ai_bot_rules` | bot id, action, decision (allow, ask, block), set by and when |
| `ai_bot_approvals` | bot id, finding id, action, payload, status (pending, approved, rejected, done, failed), decided by and when, result |
| `ai_bot_deliveries` | bot id, channel (inbox, bell, email), schedule (instant, daily digest, weekly digest), quiet hours, recipients |

Usage (credits) goes to the `ai_usage_events` table already proposed in `docs/USAGE_LIMITS_DESIGN.md`, with the bot id.

### Cost

- Monitors cost almost nothing when nothing is found: a Cube query per check per interval.
- A model call happens only for a new finding (about $0.01 to $0.03) or a scheduled digest (measured today: VAT review $0.08 to $0.09, AP review $0.15, cash $0.02 to $0.04, reporting pack $0.02 to $0.04).
- Each bot has a monthly budget; the setup card shows the estimate for its schedule and monitors before it is turned on. A run that would pass the budget is skipped and recorded, never retried in a loop.
- Digests batch findings so ten alerts cost one model call, not ten.

### Safety

- Background runs are read-only in code. Writes happen only through approval cards, by a user with the right role.
- Every run is scoped to the bot's organizations by the scheduler; the model cannot widen it.
- The PII shield, the figure verifier and the injection scan apply to every bot run, as to chat.
- Text from emails, uploaded files and bank descriptions is data, never instructions.
- Notes a user writes to a bot are checked for instructions that would widen its scope or skip approvals, and refused if they do.
- Every finding, approval and run is in the audit log.

## Phases

| Phase | What ships | Needs | Rough size |
|---|---|---|---|
| A. Bots in the database | Tables; bot builder (templates and the setup card); bot page (overview, activity, findings, settings); scheduled bots for VAT, AP, Reporting and Cash on the Nest scheduler; inbox and bell; dismissals and last runs move from the browser to the database | DBA go-ahead | 3 weeks |
| B. Monitors | Monitor engine; event hooks in Accutax (bill, invoice, challan, payment, bank import, email inbox, period close); first monitors for VAT, AP, Compliance; "Try it on last month"; email digests; "Why did I get this?" | Accutax backend changes for event hooks | 3 weeks |
| C. The new agents | Accounts Receivable (ageing, overdue monitors), Reconciliation (match suggestions), Invoice (invoice checks, numbering gaps, failed recurring invoices), Delivery Challan (unbilled challans, once the link to invoices is confirmed) | Data checks for challans; Accutax's mail service for AR | 4 weeks |
| D. Describe it, memory and approvals | Create a bot from one sentence; "Make this a bot" from chat answers; memory page; approval cards and rules for the first write actions (AR reminder draft, bank match, invoice from challan); bots handing work to each other | Write APIs in Accutax for the approved actions | 3 weeks |
| E. Channels | WhatsApp and Teams delivery; reply to approve | Provider accounts | Not sized |

Each phase ends with one paid evaluation run on the VM, not a run per change (`docs/PHASE2_AGENT.md`, batch evals rule), and with the planted test books for the new agents' checks, as for the VAT review.

## Open decisions

1. **Names in the product**: "bots", "agents", or a brand name for them.
2. **Who can create bots**: any user, or only admins and accountants; and who can approve actions.
3. **DBA go-ahead** for the eight tables, and whether they live in the shared database or the AI service's own.
4. **Event hooks in the Accutax backend**: the Accutax team adds an enqueue call where bills, invoices, challans, payments and bank imports are saved.
5. **Email**: which service sends digests and AR reminders, and from which address.
6. **Budgets and plans**: which plans include how many bots and credits; whether monitors that find nothing are free.
7. **Delivery challans**: how a challan links to the invoice made from it.
8. **Thresholds**: finance signs off the default thresholds for each monitor, as for the VAT review rules.
9. **First pilot**: which organizations and users try bots first.
