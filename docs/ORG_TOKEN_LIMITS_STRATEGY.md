# Organization-level AI usage mapping and limits

Status: strategy finalized 23 Oct 2026 (section 0), pending your sign-off on the four open decisions. Not built.
Builds on `USAGE_LIMITS_DESIGN.md` (9 Oct), which covers per-user daily and weekly limits and the session context meter. This document adds the organization layer: whose usage each AI call is, how much each organization may use, and who controls it.

## 0. Final strategy in plain words (23 Oct 2026)

Think of AI usage like a **prepaid mobile data plan for each organization**. Every organization gets a monthly bucket. Every question uses a little of it. People inside the organization can each have a smaller personal limit so one person cannot finish the bucket for everyone. When the bucket runs low, we warn. When it is empty, the AI switches to a cheaper "lite" mode, and then stops until next month or a top-up.

The research (section "Sources", added 23 Oct) agrees with the design below. The ten rules:

| # | Rule | Plain meaning | Status in code |
|---|---|---|---|
| 1 | **Meter every AI call, at one place** | Agent questions, document uploads (Claude extractor) and VAT review all cost money. Record all of them from the Bedrock client, which already computes `cost_usd` per call, not only from the chat path. | Cost is computed (`reasoning/bedrock_client.py` `get_token_usage`), logged to `logs/agent_answers.jsonl` for the agent only. Uploads and VAT review not in one ledger yet. |
| 2 | **Credits, not tokens or messages** | 1 credit = $0.001. A long hard question costs more credits than a short one, so the bucket is fair. Users see a percent bar, never token numbers. | Designed (4.1). |
| 3 | **Speed limits and money limits are two different things** | Speed limit: 1 question at a time per user, 5 a minute (stops loops and abuse). Money limit: the monthly bucket (stops overspend). Both are checked. | Designed (4.2 levels 1 and 4 vs 2 and 3). |
| 4 | **Bucket per owner account, sized per organization and seat** | All organizations of one owner share one bucket. Each organization and each paid user adds credits to it (how Miro and Notion pool per seat). Heavy and light users balance out. Auditors and accountants who work across owners are covered in 0.1. | Revised 23 Oct (0.1). Numbers set after measuring. |
| 5 | **Personal cap inside the bucket** | Default 1,000 credits a day per user. The owner can lower or raise it for a person (decided 24 Oct). | Designed (4.2 level 3). |
| 6 | **Hold, then settle** | Before a question runs, hold an estimated 35 credits. After it finishes, replace the hold with the real cost. This stops five people at once from spending past an almost-empty bucket. Failed questions are not charged. | Designed (4.5). |
| 7 | **Warn at 50% and 80%, go "lite" at 100%, then stop** | At 100% law and VAT questions continue on the cheaper model (Haiku); questions that read company data stop with a clear message and the reset date. Never cut off a question halfway. Background jobs (upload reading, VAT review) check the bucket before they start, never in the middle. | Designed (4.4). |
| 8 | **Spend less, not just limit more** | The cheapest question is the one that costs less to answer: keep prompt caching on (already saves money per call), send simple questions to the cheaper model, cap tool result size and chat history (planned in `USAGE_LIMITS_DESIGN.md`). | Caching live. Routing and history budget planned. |
| 9 | **Alarm on strange spikes** | If a user or organization spends 5 times its normal day, or the whole platform passes $25 a day, email us. Pause everything at $100 a day. Catches bugs and loops before the bill does. | Designed (4.2 level 1). Spike alert new. |
| 10 | **Measure first, enforce later** | Week 1–2: only record. Then set each plan's numbers from real usage. Then show bars and warnings. Only then block. One switch turns it off. | Rollout (7). |

### 0.1 People who work across many organizations (auditors, accountants, group owners)

Checked against the Accutax code on 23 Oct 2026. A live database count was not run (see 0.2).

**How access works in Accutax today**

| Who | How Accutax records it | Can see |
|---|---|---|
| **Owner** | `organizations.user_id` = the owner's user id. One user can own many organizations (the test data has 1,000 users owning exactly 10 each). | All organizations they own |
| **Collaborator** (accountant, auditor, staff) | A row in `collaborators` per organization, invited by that organization's owner. Has an access type (Admin, Read, Edit) and roles (Admin is the accountant role, Staff, Staff with assigned customers only, Timesheet staff). | Each organization they were invited to. These can belong to **different owners**. |
| **Super-admin** (Accutax staff) | `users.is_super_admin` | Every organization |

There is no "firm", "practice" or "subscription" table. The **owner** is the closest thing to a paying customer that exists today.

The organization list the AI already fetches from Accutax (`GET all_organizations`) returns, for each organization, its owner (`user_id`), whether the asker is a collaborator (`is_collaborator`), and their role (`collaborator_role`). So the AI can tell who pays without any new table.

**Change to decision 1 (was: charge the active organization). Agreed 23 Oct 2026.** The bucket belongs to the **owner account**, not to each organization. Every organization of one owner shares that owner's bucket. This is rule C from section 3, and it can be built now because `organizations.user_id` already exists.

**Who pays, case by case**

| Case | Example | Who pays | Personal cap |
|---|---|---|---|
| 1. Owner asks about their own organizations | Owner of a group with 10 companies compares all 10 | The owner's bucket | The owner's daily cap |
| 2. Staff or accountant asks about organizations of **one** owner | An in-house accountant compares orgs 24 to 28, all owned by the same person | That owner's bucket | The collaborator's daily cap **inside that owner's bucket**, so one person cannot empty it |
| 3. External auditor or accountant asks about organizations of **several** owners | An auditor compares client A with client B | Split evenly between the owners involved | Their cap inside each owner's bucket. If any one owner's bucket is empty, the question is refused with a message naming which client is out of AI usage |
| 4. Accountant who serves many clients and asks a lot | A practice with 40 client organizations | Each client's owner, by cases 2 and 3. Later, when a practice plan exists, the practice can pay from its own bucket instead | — |
| 5. Super-admin, demo or internal accounts | Accutax staff | An internal "unlimited" bucket | Platform limit only |
| 6. Read-only collaborator | Access type Read | Same as case 2 or 3 | A lower default cap (300 credits a day), set by the owner |

**Extra rules for multi-organization users**

- **Questions that read many organizations cost more** (more tool calls). That is already fair because we charge real cost, not a flat price per question. The existing limit on organizations per question (`max_orgs_per_query`) stays.
- **The owner sees who used their bucket**, including outside collaborators, in the per-member usage table.
- **The owner can set a cap per collaborator** or turn AI off for one collaborator, without removing that person's normal Accutax access.
- **Record everything for each question:** who asked, every organization read, each organization's owner, the asker's relationship to each organization (owner, collaborator role, super-admin) and the split. If we later add practice plans, we can re-price from history.
- **Invitations not yet accepted.** Accutax's organization list includes invitations with status INVITED as well as ACTIVE, so the AI currently allows access to an organization before the invitation is accepted. For billing, count only ACTIVE collaborators. Whether INVITED should grant access at all is a separate question for the Accutax backend team.

### 0.2 What the database shows (23 Oct 2026)

Run read-only on the VM against `accutax_bk_1_5` as `gemini_brain_ro`.

| Fact | Number |
|---|---|
| Users | 1,043 (1 super-admin) |
| Organizations | 10,054 |
| Owners | 1,006. 998 own exactly 10 organizations (generated test data). The rest own 1, 3, 4, 11, 12, 14 and 28. |
| Collaborators (`collaborators`) | 8, all on organization 10151 of owner 1030, all status **INVITED**, none accepted. 7 have access type 1 (Edit), 1 has type 3 (Read). Types come from table `account_type`: 1 Edit, 2 Admin, 3 Read. |
| Collaborators working for more than one owner (ACTIVE) | **0** |
| `user_organizations` (older membership table, role `member`, created 7 to 31 Aug 2026) | 17 rows. 16 give 11 users access to organizations they do not own. **3 of these users reach organizations of 2 different owners** (users 1038, 7483, 1013). None owns organizations. |

**What this means**

- Today almost every multi-organization user is an **owner** of all the organizations they use. The owner-pays rule covers them with no splitting.
- The auditor case (one person, several owners) exists only as test rows in the older `user_organizations` table, which the Accutax app does not read. Accutax uses `collaborators`, where no one works for two owners yet. The rule in 0.1 is ready for when this happens.
- **Access lists now agree (fixed 23 Oct 2026).** Before, when the Accutax API could not answer, the AI fell back to owned organizations plus `user_organizations`, which Accutax does not read. Now the fallback for Accutax users uses the Accutax rule: owned organizations plus `collaborators` with status ACTIVE or INVITED, matched by `accepted_by_user_id` or email (`api/auth.py` `_ACCUTAX_ACCESS_SQL`). `user_organizations` is used only for the AI's own local test logins (`SEED_TEST_USERS`). Checked on the VM database: owner 1030 gets its 4 organizations, invited collaborator 1043 gets 10151, and user 1038 (only in `user_organizations`) gets nothing through the Accutax rule. For billing, count only ACTIVE collaborators.
- Access types: the table is `account_type` (singular), not `account_types`. It exists and maps 1 Edit, 2 Admin, 3 Read.

The queries used are below, for re-running later.

```sql
-- How many organizations each user can reach (owned + active collaborations)
WITH a AS (
  SELECT user_id, id AS org_id, 'owner' AS how FROM public.organizations
  UNION
  SELECT accepted_by_user_id, organization_id, 'collaborator'
  FROM public.collaborators WHERE status = 'ACTIVE' AND accepted_by_user_id IS NOT NULL)
SELECT n_orgs, count(*) AS users FROM (
  SELECT user_id, count(DISTINCT org_id) AS n_orgs FROM a GROUP BY 1) t
GROUP BY 1 ORDER BY 1;

-- Collaborators who work for more than one owner (the auditor case)
SELECT c.accepted_by_user_id AS user_id,
       count(DISTINCT c.organization_id) AS orgs,
       count(DISTINCT o.user_id) AS distinct_owners
FROM public.collaborators c JOIN public.organizations o ON o.id = c.organization_id
WHERE c.status = 'ACTIVE'
GROUP BY 1 HAVING count(DISTINCT o.user_id) > 1
ORDER BY distinct_owners DESC;

-- Collaborators by status and access type (1 Admin, 2 Read, 3 Edit, if seeded in that order)
SELECT status, account_type_id, count(*) FROM public.collaborators GROUP BY 1, 2 ORDER BY 1, 2;
```

**Monthly bucket rules.** Unused credits do not roll over. Top-ups (bought or granted) last 12 months. Paid overage is off until Accutax has a billing system; the design keeps a place for it (`at_limit = overage`).

**Built end to end, 24 Oct 2026** (the measurement wait was skipped at the user's request; plan sizes are the defaults below until real use says otherwise).

| Part | Where | What it does |
|---|---|---|
| Tables | `backend/scripts/ops/usage_limits.sql` (rollback `usage_limits_rollback.sql`), applied to `accutax_bk_1_5` | `ai_owner_plans`, `ai_user_caps`, `ai_topups`, `ai_usage_events`, `ai_usage_charges`, `ai_usage_alerts`; `gemini_brain_ro` may read and write them |
| Plans | `usage/plans.py` | Trial 2,000 / Standard 10,000 / Pro 25,000 credits per seat a month (seats = owner + ACTIVE collaborators), default daily caps 300 / 1,000 / 2,000, Unlimited for internal and demo. No row = Standard. Lite model for 10% more after the bucket is used, then blocked. |
| Ledger | `usage/ledger.py` | Every model call metered. A question opens a running event holding 35 credits against its owners, and closes with the real cost. Super-admins are charged to owner 0 (internal). |
| Check | `usage/limits.py` | Before each question and file upload: platform day spend (alert $25, pause $100), each owner's bucket, the person's daily cap or "AI off", one question at a time and 5 a minute (checked in `ai_usage_events` under a per-person lock, so it holds across copies of the service; added 25 Oct). A failed check lets the question through. |
| Answer path | `agent/preview.py`, `api/routes.py` | Blocked: a "Usage limit" notice, no model call. Lite: the agent answers with `USAGE_LITE_MODEL` (Haiku 4.5). Each answer carries a `usage` block for the bars. |
| API | `api/usage_routes.py` | `GET /api/v1/usage` (percent bars), `/usage/owner` and `PUT /usage/owner/members/{id}` (owner raises, lowers or turns off a person's daily cap, within the bucket), `/usage/admin`, plan and top-up endpoints (super-admins and `USAGE_ADMINS`). |
| App | `UsageMeter.jsx`, `UsageModal.jsx`, `App.jsx`, `NoticeCard.jsx` | Header meter (percent), banner above the question box at 80%, lite and used up, owner page (bucket, people, daily caps), Accutax admin page (spend, owners, plan, top-ups, alerts). |
| Switches | `.env` | `USAGE_DB=on`, `USAGE_ENFORCE=off|log|soft|hard`, `USAGE_LITE_MODEL`, `USAGE_PLATFORM_ALERT_USD`, `USAGE_PLATFORM_PAUSE_USD`, `USAGE_RATE_PER_MINUTE`, `USAGE_TIMEZONE`, `USAGE_ADMINS`. Roll back: `USAGE_ENFORCE=off`, restart. |

Differences from the earlier design: owners see credit amounts on their own page (they set daily caps in credits, shown also as "about N questions"); everyone else sees percentages only. There is no weekly cap, only the daily one. Alerts (50%, 80%, 100%, lite used up, platform alert and pause, a person spending 5 times their usual day) go to `ai_usage_alerts`, the service log and the admin page; there is no email, because the AI service has no mail sender.

**Step 1 built 23 Oct 2026.** `usage/ledger.py` meters every Bedrock call in `BedrockAdapter` (and the module-level helpers). Agent answers (`agent/preview.py`) and file reads (`uploads/reader.py`) each write one line to `USAGE_LEDGER` (VM: `logs/ai_usage.jsonl`) with user, organizations, owners, the owner charged, models, tokens, cost and credits. Calls outside both (reports built outside a chat, health checks) are written as `unscoped`. Report: `python scripts/usage_report.py logs/ai_usage.jsonl [--month YYYY-MM]`. Turn off: empty `USAGE_LEDGER` and restart.

**What we build first (needs no database change).** Step 1 of the rollout can start now: a usage recorder that writes every AI call (agent, upload, VAT review) with user, active organization, all organization ids, model, tokens, estimated and actual credits to a JSONL file, plus a monthly report per organization (median, 95th, 99th percentile). The database tables in section 5 come after the DBA go-ahead.

**Decisions, 24 Oct 2026 (section 8):**

- Decision 2: AI is **included in the Accutax plan**, with a per-seat bucket (like Wafeq and Ramp, who bundle AI). No separate add-on.
- Decision 3: at 100%, go "lite" (cheaper model) and then block. No paid overage until billing exists.
- Decision 4: the owner can **lower and raise** each person's cap. Raising cannot add credits: the owner's monthly bucket still limits the total.
- Decision 5: ask the DBA only after the two-week measurement, with real numbers in hand.

## 1. What the two terms mean here

- **Token mapping (attribution).** Every model call is recorded against a user and one or more organizations, with its tokens and its cost. Without this we cannot show an organization its usage, bill it, or limit it.
- **Token limit (quota).** Each organization gets a pool of AI usage per month. Users inside it can have smaller caps. When the pool is used up, the AI stops or falls back to a cheaper mode until the reset or a top-up.

## 2. What other platforms do

### 2.1 The three named competitors

| Platform | AI features | Published AI usage limits | How they control cost |
|---|---|---|---|
| **Wafeq** (UAE accounting) | AI features exist inside plans | None. The pricing page lists Starter 69, Plus 99, Premium 249 a month (yearly-billing rates 57, 80, 200) plus Branch and other add-ons. No AI credits or caps. | Plan tiers decide which features you get. Nothing about AI volume. |
| **Alaan** (UAE spend management) | Receipt capture, VAT coding, expense analytics | None found | Their limits are on card **money** (daily limit, per-transaction limit, wallets per team), not on AI. |
| **Ramp** (US spend management) | AI reporting (Free), AP agent, auto-coding, policy reviews (Plus, $15 per user a month plus a platform fee) | None. AI is bundled into the seat price. | Ramp sells "AI token spend management" to **its customers**: budgets per agent, team, project or API key, attribution by team and model, anomaly alerts, and hard caps "even if generous". |

**Takeaway.** Wafeq, Alaan and Ramp do not show AI limits to customers. They bundle AI into plan tiers and absorb the cost. That works for them because their AI features are narrow (OCR, coding suggestions), so cost per user is small and predictable. Accutax's agent answers open questions with 2 to 4 model calls (median $0.0185, worst $0.089 per question), so cost depends on how much each user asks. We need limits even if we never show credit numbers.

Ramp's own guidance for its customers is still the best model for our design: attribute every token to a team and owner, scope quotas per team or person, alert on spikes, and always keep a hard ceiling.

### 2.2 Platforms that publish organization-level AI limits

| Platform | Pool | Levels | At the limit |
|---|---|---|---|
| **Anthropic Console** | Monthly spend limit per organization | Organization, then workspace. Each workspace can have a lower spend limit and lower rate limits. Both levels are checked on every request. | Requests stop until the 1st of next month (UTC), or until the limit is raised. |
| **GitHub Copilot** | Premium requests: 300 per user a month (Business), 1,000 (Enterprise), with paid overage | Enterprise, organization, **cost center** (a group of users with its own budget) | Admin chooses: alert only, or "stop usage when budget is reached". |
| **Notion AI** | Credits pooled for the whole workspace | Workspace default cap, then per-member or per-group caps | Premium models stop. **Included models keep working.** Custom agents pause. |
| **QuickBooks (Intuit Intelligence)** | Prompts per company per billing cycle; 200 (Accountant Suite Core) and 1,000 (Accelerate) during beta | Company | Wait for the next cycle or buy more prompts. |
| **Xero JAX** | No published cap, no extra charge now | n/a | Xero reserves the right to change pricing. |

**Patterns worth copying:**

1. **Two levels, both checked.** An organization pool, and a smaller cap per user inside it (Anthropic, Notion, GitHub).
2. **A cost-weighted unit**, not a message count (Notion credits, GitHub premium requests with model multipliers).
3. **Admin chooses what happens at the limit**: block, or allow paid overage (GitHub).
4. **Degrade instead of a hard stop** where possible (Notion keeps included models).
5. **Monthly reset aligned to the billing cycle**, plus top-ups (QuickBooks, Anthropic).
6. **Usage by member** visible to the organization admin (Notion dashboard).

## 3. The Accutax problem that others do not have

An Accutax user often works across many organizations. An accountant in a firm can ask one question that reads five client organizations ("compare VAT payable for orgs 24 to 28"). The agent receives `organization_ids` as a list (`api/auth.py` resolves `allowed_org_ids` per user).

So "which organization pays for this question" needs a rule. Options:

| Rule | Good | Bad |
|---|---|---|
| A. Charge the **active** organization (the one selected in the UI) | Simple. Users understand it. | The active organization pays for reading others. |
| B. **Split** the cost evenly across all organizations in the question | Fair for reporting. | A five-org question uses a little of five pools. Hard to explain at the limit. |
| C. Charge a **billing account** (the firm) that owns several organizations | Matches how firms pay. | Accutax has no billing-account or subscription entity today (no plan or subscription table in `accutax_bk_backend`). |

**Recommendation:** record the split (B) for reporting from day one, and enforce against the active organization (A) for now. When a billing account exists, enforce against it (C). Recording all organization ids in the ledger keeps every option open later.

## 4. Recommended design

### 4.1 Unit

Keep the unit from `USAGE_LIMITS_DESIGN.md`: **1 credit = $0.001 of model cost** (`usage.cost_usd × 1000`). It already counts output tokens at 5 times input and cache reads at a tenth. Users and organization admins see **percent bars**. Credit numbers appear only on the Accutax super-admin page.

### 4.2 Limit hierarchy

Every question is checked against each level, top to bottom. The first level that is full decides the outcome.

| Level | Default | Reset | Who sets it |
|---|---|---|---|
| 1. Platform | Alert at $25 a day, pause at $100 a day | Daily, UTC | Accutax engineering (env setting) |
| 2. **Organization pool** | From the organization's AI plan (4.3) plus top-ups | Monthly, on the organization's billing day, in its time zone | Accutax super-admin |
| 3. **Per-user cap inside the organization** | Daily 1,000 credits, weekly 3,000 (existing design) | Daily 00:00 and Monday 00:00, organization time zone | Organization admin can lower it, or raise it up to the plan's ceiling |
| 4. Per question | 1 in flight per user, 5 a minute, worst case about 90 credits | n/a | Code constant |

### 4.3 AI plans per organization

Accutax has no plan tiers yet, so start with one plan table that the super-admin assigns. Example numbers, sized with the rule from the earlier design (worst case under 15–20% of the price):

| AI plan | Monthly organization pool | Per-user daily ceiling | At 100% |
|---|---|---|---|
| Trial | 2,000 credits (about 95 typical questions) | 300 | Block |
| Standard | 10,000 credits per seat | 1,000 | Degrade, then block |
| Pro | 25,000 credits per seat | 2,000 | Degrade, then allow overage if enabled |
| Unlimited (internal, demo) | No pool | No cap | Platform limit only |

Set the real numbers after the log-only week (section 7), from each organization's 95th and 99th percentile month.

### 4.4 What happens at each threshold

| Organization pool used | Behaviour |
|---|---|
| 50% | Email the organization admin. |
| 80% | Banner for all users of the organization, plus email. |
| 100%, degrade on | Law and VAT questions continue on a cheaper model (Haiku 5.5). Data questions that need tools get the `USAGE_LIMIT` notice. |
| 100%, overage on | Continue. Overage credits are recorded for invoicing. Stop at the overage cap. |
| 100%, block | `USAGE_LIMIT` notice with the reset date and a "request more" action. No model call. |

Keep the existing rules: check before the question, charge after it; do not charge `status = error`; one question can overshoot by at most about 90 credits.

### 4.5 Race between users of the same organization

Several users of one organization can ask at the same time when the pool is nearly empty. Reserve before the call:

1. Before the model call, insert a reservation of 35 credits (the 90th percentile question) for the organization.
2. The check counts used credits plus open reservations.
3. After the answer, replace the reservation with the real cost. Reservations older than 5 minutes are ignored (crashed requests).

## 5. Data model

All tables are new in the shared database. This is a schema change, so it needs the DBA and an explicit go-ahead. Until then, run in log-only mode from `logs/agent_answers.jsonl`.

```sql
-- Which AI plan each organization is on. One row per organization.
CREATE TABLE public.org_ai_plans (
    organization_id      bigint PRIMARY KEY,
    plan                 text        NOT NULL DEFAULT 'standard',
    monthly_credits      numeric(12,3),          -- null = unlimited
    user_daily_ceiling   numeric(10,3),
    at_limit             text        NOT NULL DEFAULT 'block',   -- block | degrade | overage
    overage_cap_credits  numeric(12,3) NOT NULL DEFAULT 0,
    billing_day          smallint    NOT NULL DEFAULT 1,
    updated_by           bigint,
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- Per-user overrides set by the organization admin.
CREATE TABLE public.org_ai_user_limits (
    organization_id  bigint NOT NULL,
    user_id          bigint NOT NULL,
    daily_credits    numeric(10,3),
    weekly_credits   numeric(10,3),
    disabled         boolean NOT NULL DEFAULT false,
    PRIMARY KEY (organization_id, user_id)
);

-- Extra credits bought or granted, valid until they expire.
CREATE TABLE public.org_ai_topups (
    id               bigserial PRIMARY KEY,
    organization_id  bigint NOT NULL,
    credits          numeric(12,3) NOT NULL,
    reason           text,
    expires_at       timestamptz,
    created_by       bigint,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- The ledger from USAGE_LIMITS_DESIGN.md, plus the charged organization.
-- ai_usage_events (... organization_ids bigint[], charged_org_id bigint, credits ...)

-- How each event's credits are split across organizations (rule B, for reporting).
CREATE TABLE public.ai_usage_allocations (
    event_id         bigint NOT NULL REFERENCES public.ai_usage_events(id),
    organization_id  bigint NOT NULL,
    credits          numeric(10,3) NOT NULL,
    PRIMARY KEY (event_id, organization_id)
);
CREATE INDEX ai_usage_allocations_org ON public.ai_usage_allocations (organization_id);
CREATE INDEX ai_usage_events_charged_time ON public.ai_usage_events (charged_org_id, created_at);
```

- Organization usage this cycle is `SUM(credits) WHERE charged_org_id = $1 AND created_at >= <start of cycle>`. No counters to reset.
- If the sum gets slow, add a daily rollup table later. At current volume it will not be slow.
- The ledger also feeds the admin pages, alerts and invoices.

## 6. Where the code goes

| Part | Service | Change |
|---|---|---|
| Limit check and charge | AI service (Python) | Extend the planned `usage/limits.py`: `check(user_id, active_org_id, org_ids)` walks levels 1 to 4; `charge(...)` writes the event and the allocation rows; reservation as in 4.5. |
| Agent entry | AI service | `agent/preview.py` `answer()`: check before `run_agent`, charge after. Pick the cheaper model when the result is "degrade". |
| Plan and user caps API | Accutax backend (NestJS) | New endpoints in or next to `organization-configuration`: read and update `org_ai_plans` (super-admin only), `org_ai_user_limits` (organization admin, via `rbac`). |
| Usage API | AI service | `GET /api/v1/usage?org_id=` returns percent used for the organization and the user, and the reset dates. |
| Organization admin page | Frontend | Usage bar for the cycle, table by member (like Notion's dashboard), per-member cap editor. |
| Super-admin page | Frontend | Credits and dollars by organization, plan assignment, top-ups, overage export for invoicing. |
| Settings | AI service | `USAGE_ENFORCE=off|log|soft|hard` (existing), `USAGE_ORG_ENFORCE` separately, `USAGE_DEGRADE_MODEL`. |

**Build or reuse.** The earlier design picked LiteLLM Proxy as an option. Its "team" maps to one organization (`max_budget`, `budget_duration: 30d`), and its virtual key maps to one user. It does not solve multi-org attribution or the degrade mode, and it adds a hop on every call. Recommendation: build the organization layer in our own ledger (it is a few queries), and keep LiteLLM only as an optional backstop.

## 7. Rollout

1. **Attribution only** (1–2 weeks). Write ledger and allocation rows. Enforce nothing. Report usage per organization per month: median, 95th and 99th percentile.
2. **Set plans.** Assign every organization a plan from the measured usage. Internal and demo organizations get Unlimited.
3. **Soft.** Show organization and user bars, send 50% and 80% emails, block nothing.
4. **Hard.** Enforce the pool with the chosen action at 100%.

Rollback at any stage: `USAGE_ORG_ENFORCE=off` and restart `accutax-ai-api`.

## 8. Decisions needed from you

1. ~~Rule for multi-org questions.~~ Decided 15 Oct 2026: charge the active organization now, record the split, move to a billing account later. **Revised and decided 23 Oct 2026 (see 0.1):** charge the owner account (`organizations.user_id`). Questions across several owners are split between them. Collaborators get a cap inside each owner's bucket.
2. ~~Whether AI plans follow existing Accutax pricing, or are a separate AI add-on.~~ Decided 24 Oct 2026: included in the Accutax plan.
3. ~~Default action at 100%.~~ Decided 24 Oct 2026: degrade to the lite model, then block.
4. ~~Whether organization admins may raise user caps or only lower them.~~ Decided 24 Oct 2026: the owner may lower and raise them, within the owner's bucket.
5. DBA go-ahead for the five tables.

## Sources

- [Wafeq pricing (UAE)](https://www.wafeq.com/en-ae/pricing)
- [Wafeq on TrustRadius](https://www.trustradius.com/products/wafeq/pricing)
- [Alaan spend management](https://www.alaan.com/products/spend-management)
- [Alaan 1.0 platform overview](https://www.alaan.com/blog/alaan-spend-management-platform)
- [Alaan: best expense management software in UAE](https://www.alaan.com/blog/best-expense-management-software)
- [Ramp pricing](https://ramp.com/pricing)
- [Ramp: how to set spending controls for AI agents](https://ramp.com/blog/ai-agent-spending-controls)
- [PYMNTS: fintech finds a new category in AI's untracked costs](https://www.pymnts.com/news/artificial-intelligence/2026/fintech-finds-new-category-ai-untracked-costs/)
- [Anthropic rate limits](https://docs.anthropic.com/claude/reference/rate-limits)
- [Anthropic Workspaces announcement](https://anthropic.com/news/workspaces)
- [How to set spend limits in Claude Console](https://www.xray.tech/post/how-to-set-spend-limits-in-claude-console)
- [GitHub: manage company spending on Copilot](https://docs.github.com/en/enterprise-cloud@latest/copilot/how-tos/manage-and-track-spending/manage-company-spending)
- [GitHub Copilot premium requests](https://docs.github.com/en/billing/concepts/product-billing/github-copilot-premium-requests)
- [Notion: manage AI models and member credit spend](https://www.notion.com/de/help/manage-ai-models-and-member-credit-spend)
- [Flexprice pricing index: Notion](https://flexprice.io/pricing-index/notion)
- [Introducing Intuit Intelligence (QuickBooks)](https://quickbooks.intuit.com/learn-support/en-us/help-article/intuit-assist/introducing-intuit-intelligence/L189976Da_US_en_US)
- [Just Ask Xero (JAX)](https://www.xero.com/jax/)

Added 23 Oct 2026 (mostly vendor blogs; patterns, not standards):

- [TrueFoundry: LLM token rate limiting and budgets](https://www.truefoundry.com/blog/llm-token-rate-limiting-budgets) (rate limits protect capacity, budgets protect money)
- [Gravitee: token budgets and quotas for LLM APIs](https://www.gravitee.io/token-quotas.html)
- [MLflow: gateway LLM rate limits](https://mlflow.org/articles/rate-limiting-llm/)
- [metaCTO: LLM rate limiting, token quotas and cost control](https://www.metacto.com/blogs/llm-rate-limiting-token-quotas-production)
- [Solo.io: real-time AI cost controls with agentgateway](https://www.solo.io/blog/building-real-time-ai-cost-controls-with-agentgateway) (reserve, then settle)
- [Gravitee: quotas, budget alerts and chargeback for LLM gateways](https://gravitee.io/corpus/gen-1223/electric-on-the-eel/quotas-budget-alerts-and-chargeback-models-for-llm-gateway-cost-governance.html)
- [Tyk AI Studio budgeting](https://tyk.io/docs/ai-management/ai-studio/budgeting)
- [Metronome: why AI pricing is moving to credits](https://metronome.com/blog/why-ai-pricing-is-moving-to-credits) (per-seat credits pooled per company, Miro example)
- [Schematic: AI credits](https://schematichq.com/blog/ai-credits)
- [Solvimon: AI credit pricing models](https://www.solvimon.com/blog/ai-credit-pricing-models-how-tokens-credits-hybrid-billing-work)
- [noqta: prompt caching and model routing 2026](https://noqta.tn/blog/ai-api-cost-optimization-prompt-caching-model-routing-2026)
