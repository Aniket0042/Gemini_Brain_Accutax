# Organization-level AI usage mapping and limits

Status: proposed, 15 Oct 2026. Not built.
Builds on `USAGE_LIMITS_DESIGN.md` (9 Oct), which covers per-user daily and weekly limits and the session context meter. This document adds the organization layer: whose usage each AI call is, how much each organization may use, and who controls it.

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

1. ~~Rule for multi-org questions.~~ Decided 15 Oct 2026: charge the active organization now, record the split, move to a billing account later.
2. Whether AI plans follow existing Accutax pricing, or are a separate AI add-on (like Wafeq's add-ons).
3. Default action at 100%: block, degrade, or paid overage.
4. Whether organization admins may raise user caps or only lower them.
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
