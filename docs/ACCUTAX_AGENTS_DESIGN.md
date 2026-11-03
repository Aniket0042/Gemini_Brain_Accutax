# Accutax agents: named, always-on specialist agents

Written 2026-10-22. Status: proposed, nothing built. Phase 0 needs no database change. Phases 1 and 2 need new tables, which need the DBA's go-ahead before any DDL is run.

## Goal

Today Accutax AI is one agent that answers when asked. This design turns it into a small team of named specialist agents (a VAT agent first) that:

- each have one clear job, their own instructions and their own set of tools;
- keep working between chats on a schedule or when something happens in the books, and bring findings to the user before they ask;
- learn from what the user accepts and dismisses;
- ask for approval before anything that changes data or leaves the system;
- can be reached in the chat today, and by email, Teams or WhatsApp later.

The idea comes from two products launched in 2026, OpenAI's dots and xAI's Grok Bot. We take the parts that suit an accounting product and leave out the rest (see "What we do not copy").

## What the two products do

### OpenAI dots (launched 2026-09-29)

Source: OpenAI's announcement, "Introducing dots" (openai.com/index/introducing-dots).

- A dot is a persistent agent the user names and makes their own. It runs on GPT-6 Astra and has its own cloud computer and browser, and it reaches over 4,000 apps through plugins.
- It works towards the user's goals around the clock, not only when asked. It learns the user's preferences and standards from feedback.
- **Proactive research:** when the user is not working with it, the dot looks for ways to help using the connected apps through tools restricted to read-only. OpenAI's example: a dot noticed an invoice the user had forgotten to send, prepared it, and sent it after the user approved.
- Users reach it in ChatGPT (web, desktop, mobile, voice), Slack and Teams. The dot messages back with progress, questions and decisions. Context carries across channels.
- **Control:** built-in rules for when to act and when to ask; Custom Rules to allow, require approval for, or block specific actions; auto-review of actions that affect accounts or share information; an Activity View of background work; a safety monitor that can pause the dot. Some tasks (changing a password) always stay with the user.
- **Specialist dots** for organisations have their own identity, credentials and access to systems of record, and take on defined responsibilities. OpenAI names procurement, invoice processing, email marketing, customer support and commercial contracting as early areas.
- Conversations with the dot do not count against ChatGPT usage limits. Tasks it starts in other products do.

### xAI Grok Bot (beta from 2026-08-11)

Sources: secondary write-ups (Composio, Reworked, Digital Applied, Toolradar). The official page x.ai/bot could not be fetched, so details may differ.

- The user creates several bots. Each has a short name, one main job and a description that works as its first prompt.
- **Skills:** a working process can be saved as a reusable skill with steps, decision rules, output requirements and approval limits. "Teach a task" drafts a skill from a workflow the user performs while the bot watches.
- **Routines** run a workflow on a schedule or, where supported, after an event.
- **Memory** is per bot: role, stable preferences, summaries of earlier work.
- **Teams of bots:** two to six bots can share a group chat, message each other and pass work along without the user relaying it.
- **Approvals** can be required for sending messages, publishing, deleting data, purchasing or changing production systems.
- All bots on one account share one cloud computer, so a bot is not a security boundary. Heavy multi-bot runs used up weekly limits quickly in early tests.

### The shared pattern

A named specialist with a narrow job, plus memory that improves with feedback, plus background triggers, plus an approval policy, plus several channels.

## Where Accutax AI is today

- One agent, labelled "Accutax AI": `run_agent()` in `backend/src/gemini_brain/agent/loop.py`. A bounded Bedrock tool loop on Sonnet 5 (`agent_model_id`), with at most 6 tool calls (`agent_max_tool_calls`) and a 45-second deadline (`agent_deadline_seconds`).
- Tools are built per request by `specs()` in `agent/tools.py`: `query_metrics`, `list_documents`, `cash_forecast`, `review_vat`, `vat_return`, `search_vat_kb`, `app_guide`, `vat_penalty`, `read_document`. `ToolContext.scope()` lets the model narrow the organisations but never widen them.
- What we call the "VAT agent" is the set of VAT tools inside this one agent. VAT-law questions are routed by regex (`agent/law.py`) to a single-call knowledge-base answer.
- Guards: canary check for prompt leaks, the figure verifier (`policy/verifier.py`), the injection scan of tool results (`policy/injection.py`), the PII shield (`privacy/`, `PII_MODE`).
- Memory: thread history in `model_arena_chat_sessions` and `model_arena_chat_messages`, with a 24k-token history window (`agent/history.py`). Nothing is remembered per agent or across threads.
- Background work: none on the AI side apart from the monthly VAT knowledge-base refresh timer. The Nest backend has BullMQ queues (`accutax_bk_backend/src/modules/scheduler/`) and a notification module (`src/modules/notification/`).
- Reports are stored on disk with an expiry (`artifacts/store.py`). Uploads are stored per thread (`uploads/store.py`).
- `model_arena_projects` (name, description, instructions) looks like a left-over from the earlier model arena. It is not used by the agent loop and should not be reused for agents, because its meaning is different.

## Design principles

1. **Code finds, the model explains.** This is already true of `review_vat`. Background agents run the same rule-based tools; the model writes the summary.
2. **Read-only in the background.** A routine never changes data and never sends anything outside Accutax without an approval. This follows dots' proactive research.
3. **Tenant scope is injected by code**, as today. An agent run is always tied to one organisation (or a list the user chose), and the agent cannot widen it.
4. **One loop, many profiles.** Specialists are configurations of the existing `run_agent()`, not separate services. All guards apply to every agent.
5. **Every background run has a budget** and is billed to the organisation's credit pool (`docs/USAGE_LIMITS_DESIGN.md`, `docs/ORG_TOKEN_LIMITS_STRATEGY.md`).
6. **Quiet by default.** A routine that finds nothing new sends nothing. A finding the user dismissed does not come back.

## Concepts

| Concept | What it is | Example |
|---|---|---|
| Agent profile | Name, role, instructions, allowed tools, model, default rules | VAT Agent: VAT tools only, "explain findings with the FTA source" |
| Routine | A trigger plus a task the agent runs without a chat | Every Monday 08:00 org time: review last week's bills for duplicates and VAT problems |
| Trigger | What starts a routine: a schedule or an event | Schedule; bill created; document uploaded; period closed; VAT return due in 7 days |
| Run | One execution of a routine or a chat turn by an agent, with its cost and result | Run 812: VAT Agent, org 25, 3 findings, 41 credits |
| Finding | One item a run brings to the user, with evidence and a suggested action | "Bills B-1042 and B-1047 from the same supplier, same total, 2 days apart" |
| Inbox | Where findings wait for the user; also shown as a card in the chat | "VAT Agent: 3 new findings this week" |
| Agent memory | Short notes per agent and organisation: preferences, accepted and dismissed findings | "Org 27: same-amount rent bills on consecutive days are expected" |
| Rule | Per organisation and agent: allow, ask, or block an action | Ask before marking a bill as duplicate; block sending email |
| Handoff | One agent asks another for a result | VAT Agent asks AP Agent whether a supplier has other open bills |

## The first agents

| Agent | Job | Tools | First routine |
|---|---|---|---|
| VAT Agent | VAT law, bill checks, books review, return position, penalties, deadlines | `review_vat`, `vat_return`, `vat_penalty`, `search_vat_kb`, `read_document`, `list_documents` | Weekly review digest; return deadline reminder at 14 and 7 days |
| AP Agent (bills) | Duplicate and odd bills, checks on uploaded bills | `review_vat` (duplicates mode), `read_document`, `list_documents` | On upload: check the bill |
| Cash Agent | Cash position and forecast | `cash_forecast`, `query_metrics` | Weekly cash outlook |
| Reporting Agent | Figures and reports on request | `query_metrics`, `list_documents`, report artifacts | Month-end pack after period close |

"Accutax AI" stays as the general agent the chat uses by default. It can hand a question to a specialist, and the user can address a specialist directly with an @mention.

Only the VAT Agent is in scope for phases 0 to 2. The others are listed so the design does not fit only one agent. The full list of 25 possible agents, with readiness for each, is in `docs/ACCUTAX_AGENT_CATALOG.md`.

## Phases

### Phase 0: agent profiles in code (no database change)

What it adds:

- An `AgentProfile` dataclass in a new `agent/profiles.py`: `key`, `name`, `role`, `instructions`, `tools` (allowed tool names), `model_id` (optional), `max_tool_calls`, `deadline_seconds`.
- A code registry with two profiles: `accutax` (today's behaviour, all tools) and `vat` (VAT tools only, VAT instructions).
- `run_agent(..., profile=...)`: `specs()` filters to the profile's tools; `system_prompt()` adds the profile's role and instructions after the shared `RULES`. The guards stay the same for every profile.
- Chat: `POST /api/v1/query/stream` accepts an optional `agent` field. The UI shows "@VAT Agent" as a choice in the chat box and labels answers with the agent's name. The session remembers the last agent used.
- Handoff tool `ask_specialist(agent, question)` for the general agent. It runs the specialist's loop inside the same request, with the same organisation scope and the remaining deadline, and counts against the same tool-call budget.
- Eval: the 21 VAT review cases and the VAT-law cases from the 89-question replay, run through `agent=vat`; the full replay through `agent=accutax` to show no regression. One eval run for the whole phase (see the batch-evals rule).

Done when: the VAT Agent answers through its own profile, the replay score is not lower than the current agent path, and the UI shows the agent's name.

Rough size: 4 days.

### Phase 1: routines, runs, findings and the inbox

What it adds:

- Tables `ai_agents`, `ai_agent_routines`, `ai_agent_runs`, `ai_agent_findings` (sketch below). Needs the DBA.
- A run endpoint on the AI service, called only by the Nest scheduler with a service token: `POST /api/v1/agents/{agent_key}/routines/{routine_id}/run`.
- Nest: a BullMQ repeatable job per routine (schedule triggers), and event jobs from existing places in the Nest code (bill created, document uploaded, period closed). The AI service does not get its own scheduler.
- A run uses the profile's tools in read-only mode: a `ToolContext.read_only` flag that `execute()` checks, so no future write tool can run in the background.
- Findings are stored with their evidence (document ids, figures, rule id). A finding's key (for example the rule plus the document ids) prevents the same finding from being stored twice.
- Inbox: `GET /api/v1/agents/inbox`, `POST /api/v1/agents/findings/{id}` with `accept`, `dismiss` or `snooze`. The chat shows a card ("VAT Agent: 3 new findings") using the existing `vat_review` card.
- Notifications through the Nest notification module: in-app first, email digest second.
- Cost: each run records its credits. A routine stops when the organisation's daily agent budget is spent and records why.

First routines: the weekly VAT review digest and the VAT return deadline reminder. The deadline reminder is also package 5 of `docs/VAT_AGENT_COMPLETION_PLAN.md`, so it should be built once, here.

Done when: on the VM, organisation 25 and 27 get a weekly digest with the expected findings from the planted book, a dismissed finding does not return, and run costs show in the usage table.

Rough size: 8 days, after the DBA's go-ahead.

### Phase 2: memory from feedback, and approvals

What it adds:

- Table `ai_agent_memory`: short notes per agent and organisation, written by code from user actions (dismissed findings, accepted findings, stated preferences such as "send the digest on Sunday"). The model may propose a note; code decides whether to keep it. Notes never hold figures or personal data, and they pass through the PII shield.
- Memory is used in two places: the rule engine (a dismissed pattern lowers or skips a rule for that organisation) and the profile's instructions (a few lines of preferences).
- Table `ai_agent_rules`: per organisation and agent, each action is `allow`, `ask` or `block`. Defaults: every read is `allow`; every write and every external send is `ask`; deleting data is `block`.
- Approval cards: the first write actions are "mark bill as possible duplicate" and "create a draft VAT return worksheet". The agent prepares the action; it runs only after a user with the right role approves it in the chat or the inbox. The approval and the user are written to the audit log.
- Activity view: a page listing each agent's runs, findings and approvals for the organisation, from `ai_agent_runs`.

Done when: dismissing a finding changes what the next run reports, and no write action runs without a recorded approval.

Rough size: 8 days.

### Phase 3: channels, teams of agents, custom agents

- Channels: email digest with reply-to-approve links, Microsoft Teams, WhatsApp Business. Each channel only carries summaries and links back to Accutax; figures and documents stay in the app.
- More specialists (AP, Cash, Reporting), and handoffs between them in background runs.
- Custom agents: an admin starts from a template, names the agent, and picks tools and routines from a fixed list. No free-form tool access.
- Agents with their own identity for access control, as specialist dots have: an agent user per organisation with a role in Accutax, so every action is attributable.

Rough size: not estimated; depends on which channels the business wants first.

## Table sketch (phases 1 and 2, needs the DBA)

These are a proposal for discussion with the DBA. Names follow the `ai_` prefix proposed in the usage-limits design.

```sql
-- One row per agent available to an organisation (profiles stay defined in code; this row turns one on).
CREATE TABLE ai_agents (
    id               BIGSERIAL PRIMARY KEY,
    organization_id  BIGINT NOT NULL,
    agent_key        TEXT   NOT NULL,          -- 'vat', 'ap', 'cash', 'reporting'
    display_name     TEXT   NOT NULL,          -- the name the organisation gave it
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    daily_credit_cap INTEGER,                  -- NULL = organisation default
    created_by       BIGINT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, agent_key)
);

CREATE TABLE ai_agent_routines (
    id            BIGSERIAL PRIMARY KEY,
    agent_id      BIGINT NOT NULL REFERENCES ai_agents(id),
    kind          TEXT   NOT NULL,             -- 'weekly_vat_review', 'vat_deadline', 'bill_upload_check'
    trigger_type  TEXT   NOT NULL,             -- 'schedule' | 'event'
    schedule_cron TEXT,                        -- in the organisation's time zone
    event_name    TEXT,                        -- 'bill.created', 'document.uploaded', 'period.closed'
    params        JSONB  NOT NULL DEFAULT '{}',
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ai_agent_runs (
    id            BIGSERIAL PRIMARY KEY,
    agent_id      BIGINT NOT NULL REFERENCES ai_agents(id),
    routine_id    BIGINT REFERENCES ai_agent_routines(id),  -- NULL for chat turns
    session_id    TEXT,                                     -- set for chat turns
    status        TEXT   NOT NULL,             -- 'running', 'done', 'failed', 'skipped_budget'
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    credits       INTEGER NOT NULL DEFAULT 0,
    tool_calls    INTEGER NOT NULL DEFAULT 0,
    summary       TEXT,
    error         TEXT
);

CREATE TABLE ai_agent_findings (
    id            BIGSERIAL PRIMARY KEY,
    run_id        BIGINT NOT NULL REFERENCES ai_agent_runs(id),
    organization_id BIGINT NOT NULL,
    agent_id      BIGINT NOT NULL REFERENCES ai_agents(id),
    finding_key   TEXT   NOT NULL,             -- rule id + document ids; stops repeats
    severity      TEXT   NOT NULL,             -- 'info', 'check', 'likely'
    title         TEXT   NOT NULL,
    evidence      JSONB  NOT NULL,             -- document ids, figures, rule id
    status        TEXT   NOT NULL DEFAULT 'new', -- 'new', 'accepted', 'dismissed', 'snoozed'
    decided_by    BIGINT,
    decided_at    TIMESTAMPTZ,
    snooze_until  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, agent_id, finding_key)
);

-- Phase 2
CREATE TABLE ai_agent_memory (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    agent_id        BIGINT NOT NULL REFERENCES ai_agents(id),
    kind            TEXT   NOT NULL,           -- 'preference', 'expected_pattern'
    note            TEXT   NOT NULL,           -- no figures, no personal data
    source          TEXT   NOT NULL,           -- 'dismissed_finding', 'user_said', 'admin'
    created_by      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ
);

CREATE TABLE ai_agent_rules (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    agent_id        BIGINT REFERENCES ai_agents(id),  -- NULL = all agents of the organisation
    action          TEXT   NOT NULL,           -- 'mark_duplicate', 'draft_vat_return', 'send_email'
    decision        TEXT   NOT NULL,           -- 'allow' | 'ask' | 'block'
    set_by          BIGINT NOT NULL,
    set_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, agent_id, action)
);
```

Approvals in phase 2 reuse `ai_agent_findings` (a finding with a proposed action) and the audit log; a separate approvals table is only needed if approvals outgrow findings.

## API sketch

| Method and path | Caller | Phase |
|---|---|---|
| `POST /api/v1/query/stream` with `agent` | Chat UI | 0 |
| `GET /api/v1/agents` | Chat UI: agents enabled for the selected organisation | 0 (from code), 1 (from `ai_agents`) |
| `POST /api/v1/agents/{agent_key}/routines/{routine_id}/run` | Nest scheduler only, service token | 1 |
| `GET /api/v1/agents/inbox` | Chat UI, inbox page | 1 |
| `POST /api/v1/agents/findings/{id}` (`accept`, `dismiss`, `snooze`, `approve`) | Chat UI, inbox page | 1, approve in 2 |
| `GET /api/v1/agents/{agent_key}/activity` | Activity page | 2 |
| `PUT /api/v1/agents/{agent_key}/rules` | Organisation admin | 2 |

## Safety and cost

- Background runs are read-only by code (`ToolContext.read_only`), not by prompt.
- Every run is scoped to one organisation by the scheduler; the model cannot add organisations.
- The PII shield, figure verifier and injection scan apply to background runs exactly as to chat turns. Text from uploaded files and the web stays untrusted.
- Findings name documents and rules; the model writes the explanation only.
- Budget per run (tool calls, deadline, credits), per agent per day, and per organisation per month. A routine skipped for budget is recorded with status `skipped_budget`, never retried in a loop.
- Rough cost: a weekly VAT review digest is one agent run per organisation per week. At the replay's median of about $0.02 per question and a p90 of about $0.03, a digest that makes two to four tool calls should cost a few cents per organisation per week. To be measured in phase 1.

## What we do not copy

- **A cloud computer and browser per agent.** Accutax agents work through governed tools on our own data. Screen control would bypass the tenant scope and the audit trail.
- **Write actions in the background.** Every write waits for a user's approval.
- **Bots that share credentials.** Grok Bot's shared computer means its bots are not security boundaries. Our agents share no credentials; each run gets a scoped token.
- **Free-form custom agents with any tool.** Custom agents (phase 3) choose from a fixed list of tools and routines.

## Open decisions

1. **Product name** for the agents in the UI: "Accutax Agents", "Specialists", "AI teammates", or another name.
2. **Can users rename their agent**, as dots allow, or are names fixed per specialist?
3. **First routines.** Proposed: the weekly VAT review digest and the VAT return deadline reminder. Alternatives: a check on every uploaded bill, a month-end pack.
4. **Digest day and time:** one fixed default (Monday 08:00 organisation time) or chosen per organisation.
5. **DBA go-ahead** for the six tables above, and whether they live in the shared database or the AI service's own database (see the per-environment database plan for EC2).
6. **Who pays for background runs:** the organisation's credit pool, a separate agent allowance per plan, or free for the first routines.
7. **Who can approve write actions:** any user of the organisation, or only roles such as accountant or admin.
8. **First external channel** for phase 3: email, Teams or WhatsApp.
9. **Finance sign-off** of the review thresholds (`vat_review/thresholds.py`) before findings go out unasked. A wrong finding in a chat answer is an annoyance; a wrong finding pushed to a user every week costs trust.

## Order of work

1. Phase 0 now; it needs nothing from anyone.
2. Send the table sketch to the DBA at the start of phase 0, because the answer takes the longest.
3. Phase 1 when the tables exist, with the two first routines on organisations 25 and 27.
4. Phase 2 after a two-week pilot of phase 1 shows which findings users dismiss.
5. Phase 3 after the business picks the first channel.
