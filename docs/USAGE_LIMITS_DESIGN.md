# Usage limits and session context for Accutax AI (agent path)

Status: proposed, 9 Oct 2026. Not built.
Scope: the agent path only (`agent/loop.py`, `agent/preview.py`). The SQL fallback and the old orchestrator are being retired, so they get no limits.

## 1. What we measured

All figures come from our own runs on the VM.

| Source | Questions | Cost per question | Model calls | Tool calls |
|---|---|---|---|---|
| Replay run D (real questions, Sonnet 5 plans and writes) | 89 | median $0.0185, mean $0.0213, 90th percentile $0.0331, worst $0.089 | median 2, worst 4 | median 1, worst 4 |
| Live answer log, 9 Oct | 4 | about $0.023 | 2 | 1 |

A live question uses about 5,400 uncached input tokens and about 460 output tokens, plus the cached system prompt and tool definitions (about 6,700 tokens).
Sonnet 5 costs $2 per million input tokens and $10 per million output tokens. Cache writes cost 1.25 times input and cache reads 0.1 times input (`config/pricing.py`, `bedrock_client.py`).

## 2. The current context meter answers the wrong question

- The meter shows the largest single model call of the latest answer, out of 200,000 tokens (`SESSION_CONTEXT_WINDOW_TOKENS`).
- The agent sends only the last 6 messages, which is 3 turns (`MAX_HISTORY_MESSAGES` in `agent/loop.py`, `HISTORY_MESSAGES` in `agent/preview.py`).
- So the meter sits around 5–15% and never fills. Meanwhile turn 4 and older are dropped without telling the user.
- Claude's meter means "this chat is getting long; quality and cost go up; start a new one". Ours should mean the same thing.

## 3. Recommendation: session context

Count tokens, not messages, and make the meter show what the model really holds.

| Setting | Value | Why |
|---|---|---|
| Session context limit (meter 100%) | **64,000 tokens** | Sonnet 5's window is 200K, but quality drops as context grows ("context rot"), and every extra 10K uncached tokens per call adds about $0.02, which doubles the cost of a typical question. 64K leaves room for several turns plus a large tool result. |
| Fixed part | system prompt and tool definitions, about 6,700 tokens (cached) | Already measured. |
| History budget | **24,000 tokens** of earlier turns, newest first | Replaces the 6-message rule. Short answers let more turns fit; long tables push older turns out sooner. |
| Tool result cap | **8,000 tokens per tool result** | One large list (100 rows) must not take over the window. Row caps already exist; this adds a token cap. |
| Answer | 3,000 tokens (`MAX_ANSWER_TOKENS`, unchanged) | |
| Compaction | at **80%** of the history budget, summarize the oldest turns into one running summary of at most 1,500 tokens | The same idea as Claude's auto-compact. Keep orgs, periods, filters and the topic of each turn. **Keep no figures in the summary**: figures always come from tools again, so a bad summary cannot put a wrong number into an answer. |
| At 100% after compaction | Show "This chat is long. Start a new chat for best results." Do not block. | Rare once compaction works. |

Put a prompt-cache point after the history as well, so earlier turns are read from cache (0.1 times the price) on the second model call of each question.

Log `context_tokens` in the answer log. After a week, check that the 99th percentile stays under about 40K. If it does not, raise the limit or lower the tool cap.

## 4. Recommendation: daily and weekly limits

### 4.1 Unit: credits, based on cost

Count **credits**, where 1 credit = $0.001 of model cost (`usage.cost_usd × 1000`). Do not count raw tokens or messages.

- One output token costs 5 input tokens. A cache read costs one tenth of an input token. A token count hides this.
- A message count makes a one-line law question cost the same as a 4-call multi-org report.
- Bedrock counts its own quota the same way: input plus cache writes plus output times 5.
- Cursor moved from request counts to a cost-based pool in June 2025 for the same reason.
- `cost_usd` already exists on every agent answer, so no new calculation is needed.

Show users **percent bars only** (Claude does the same). Show credits only on the admin page. Then we can change prices or models without changing what users see.

A typical question is about 21 credits. A heavy question is 33–90 credits.

### 4.2 Limits per user

| Limit | Value | About how many questions | Purpose |
|---|---|---|---|
| Questions in flight | **1 per user** | | Stops double submits and parallel overspend. Use a Postgres advisory lock on the user id (no new table). |
| Rate | **5 questions per minute** | | Stops scripts and retry loops. |
| **Daily** | **1,000 credits ($1.00)** | about 45 typical, at least 11 of the heaviest | Resets at 00:00 in the organization's time zone (`report_timezone`, Asia/Dubai). A calendar day is easier for finance users to understand than a rolling 5-hour window. |
| **Weekly** | **3,000 credits ($3.00)** | about 140 typical | Resets Monday 00:00 in the same time zone. A heavy user can have one or two big days but not five. |

What this costs us per seat each month:
- A typical user (10–15 questions a day, 22 working days): **$5–7**.
- The worst user possible under these limits: about **$13**.

Rule for sizing: the worst case per seat should stay under 15–20% of the seat price. All numbers scale linearly, so if the seat price changes, scale the limits by the same factor.

How this compares:
- QuickBooks gives 25 AI prompts a month on small-business plans, 200 on Accountant Suite Core and 1,000 on Accelerate.
- Our daily limit allows about 1,000 questions a month, the same as Intuit's top accountant tier.
- That is generous on purpose while usage is low. The ledger (section 5) shows real use, and then we tighten if needed.

### 4.3 Limits per organization and for the platform

| Limit | Value | Action |
|---|---|---|
| Organization monthly pool | **10,000 credits per paid seat**, pooled | Alert the Accutax admin at 80%. Block at 100% until the next month or a top-up. |
| Platform daily spend | alert at **$25**, pause the AI at **$100** | Protects against a bug or abuse that gets past the per-user limits. |
| Bedrock quota | check the "cross-region tokens per minute" quota for `in.` Sonnet 5 in Service Quotas | This is the hard physical ceiling for all users together. |

### 4.4 Behaviour

- **Check before, charge after.** Before calling the model, block if the user is already at 100%. After the answer, add its real cost. One question can go over the limit by at most about 90 credits. Claude also finishes the message in progress.
- **Warn at 80%**: "You have used 80% of today's AI usage. It resets at 00:00."
- **At 100%**, return a notice card with code `USAGE_LIMIT` and the reset time. Make no model call.
- **Do not charge** answers that fail with `status = error` (our fault). Charge `deadline` and `partial` answers, because the model work was done.
- **Exempt list**: `USAGE_EXEMPT_USERS` for internal testers and demos.

## 5. Storage

Use an append-only ledger table. Do not keep counters that need resetting.

```sql
CREATE TABLE public.ai_usage_events (
    id               bigserial PRIMARY KEY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    user_id          bigint      NOT NULL,
    organization_ids bigint[]    NOT NULL,
    session_id       uuid,
    route            text        NOT NULL,   -- tools | law
    status           text        NOT NULL,
    model_id         text,
    input_tokens     int, output_tokens int, cache_read_tokens int, cache_write_tokens int,
    context_tokens   int,
    credits          numeric(10,3) NOT NULL
);
CREATE INDEX ai_usage_events_user_time ON public.ai_usage_events (user_id, created_at);
```

- Daily use is `SUM(credits) WHERE user_id = $1 AND created_at >= <start of today in org time zone>`. Weekly use is the same with the start of the week. Nothing needs to reset, because the window is in the query.
- The same rows give the admin page, the alerts and billing later.
- This is a schema change on the shared database, so the DBA runs it. `gemini_brain_ro` cannot run DDL.
- Until the table exists, run in log-only mode from `logs/agent_answers.jsonl`.

## 6. Code changes (agent path only)

| File | Change |
|---|---|
| new `usage/limits.py` | `check(user_id, org_ids)` returns allow/warn/block, percents and reset times. `charge(...)` inserts one ledger row. |
| `agent/preview.py` `answer()` | Call `check` before `run_agent`. Call `charge` after it. Return a `usage` snapshot with the response, as `context_window` is returned today. |
| `agent/loop.py` `_messages()` | Token-budgeted history, tool result cap, cache point after the history. |
| new `agent/compact.py` | Running summary of old turns, stored in `conversation_state` (the same column the meter uses). |
| `config/settings.py` | `USAGE_ENFORCE=off\|log\|soft\|hard`, `USAGE_DAILY_CREDITS=1000`, `USAGE_WEEKLY_CREDITS=3000`, `USAGE_RPM=5`, `USAGE_ORG_MONTHLY_CREDITS_PER_SEAT=10000`, `USAGE_EXEMPT_USERS`, `SESSION_CONTEXT_WINDOW_TOKENS=64000`. |
| `resilience/messages.py` | `USAGE_LIMIT` and `USAGE_WARNING` notices. |
| `api/routes.py` | `GET /api/v1/usage` for the popover when a page loads. |
| `frontend/.../ContextWindowMeter.jsx` | Three rows, as in Claude's popover: Context window, Daily limit (resets in Xh), Weekly limit (resets Mon). |

## 7. Rollout

Roll out the same way as Cube and the agent: shadow first.

1. **Log** (1 week): write the ledger and enforce nothing. Measure each user's daily credits at the median, 95th and 99th percentile.
2. **Soft** (1 week): show the bars and the 80% warnings, and block nothing. Watch for complaints.
3. **Hard**: block at 100%. Set the daily limit to about 1.5 times the 99th percentile of a real user's day, if that is lower than 1,000 credits.

Rollback at any stage: `USAGE_ENFORCE=off` and restart `accutax-ai-api`.

## 8. Open decisions

1. The Accutax seat price, and whether there are plan tiers. This sets the real limit numbers (rule in 4.2).
2. A calendar day or a rolling 24 hours. Recommended: calendar day in the organization's time zone.
3. Whether the organization pool is billable (top-ups) or only a safety cap.
4. DBA go-ahead for `ai_usage_events`.

## 9. Reuse instead of build: LiteLLM Proxy

Added 9 Oct 2026 after a survey of open-source tools.

**Pick: LiteLLM Proxy (MIT core) in Bedrock pass-through mode.**

It covers sections 4.2 and 4.3 with built-in features:

| Our limit | LiteLLM feature |
|---|---|
| Daily and weekly per user | one virtual key per user, with `budget_limits` set to `[{24h, $1}, {7d, $3}]` |
| 5 a minute, 1 in flight | `rpm_limit`, `max_parallel_requests` on the key |
| Organization pool | one team per organization, `max_budget` with `budget_duration: 30d` |
| Platform cap | global `max_budget` with `budget_duration: 24h` |
| Ledger, admin page | spend logs in its own Postgres, plus an admin UI |

- **Our code barely changes.** Pass-through takes the native Bedrock Converse body. `BedrockAdapter` keeps boto3, the tools and the prompt cache points. It only gets `endpoint_url=<proxy>/bedrock` and the user's key as `AWS_BEARER_TOKEN_BEDROCK`.
- **Session context (section 3) stays ours.** No tool handles it: it is agent-loop logic.

Gaps, to check in a one-day spike before adopting it:

- **Pass-through budgets are not documented.** The pass-through docs list cost tracking for `/converse` but do not say whether key budgets are enforced there. "End-user tracking" is listed as not supported, which is why we use one key per user.
- **It blocks per model call, not per question.** A question can be cut off between its 2–4 calls. Keep our pre-check (read the key's spend from `/key/info`) and set LiteLLM's limit about 100 credits above ours, as a backstop.
- **Resets are on fixed UTC times.** The 24-hour window resets at 00:00 UTC, which is 04:00 in Dubai. The docs disagree on the weekly reset day (Sunday or Monday).
- **Pricing.** LiteLLM's price table must know `in.anthropic.claude-sonnet-5` and its cache read and cache write rates. Otherwise set custom prices in its config. Compare its spend with our `cost_usd`.
- **Footprint.** It adds a LiteLLM container and its own small Postgres, both rootless Podman on 127.0.0.1. The shared database needs no DDL. Check the VM's free memory next to Cube first.
- **Supply chain.** LiteLLM 1.82.7 and 1.82.8 on PyPI were malicious, on 24 Mar 2026, and stole credentials. Use the official container image pinned by digest. Never `pip install litellm` into our venv. The proxy holds the AWS credentials, so expose it on localhost only.
- **Availability.** It is a new hop on every call. If it is down, the AI is down. A `LLM_GATEWAY=off` setting should fall back to direct Bedrock, with no limits.

Tools rejected:

| Tool | License | Why not now |
|---|---|---|
| OpenMeter | Apache-2.0 | Real metering and entitlements, but it needs Kafka, ClickHouse and Postgres: too heavy for this VM. Kong bought it in Sep 2025 and is folding it into Konnect. Revisit if we sell usage-based plans. |
| Lago, Flexprice | AGPL-3.0 | Billing and invoicing platforms. More than we need. |
| Unkey | AGPL-3.0 | API keys and request rate limits. No cost-based credits. |
| Portkey, Helicone | open-source core | Mostly observability. Budget features sit mainly in their cloud plans, and self-hosting Helicone is heavy. |
| Envoy AI Gateway, agentgateway | Apache-2.0 | Built for Kubernetes first. |

## Sources

- [Intuit: Introducing Intuit Intelligence (25 prompts a month)](https://quickbooks.intuit.com/learn-support/en-sg/help-article/intuit-ai/introducing-intuit-intelligence/L189976Da_SG_en_SG)
- [Intuit Accountant Suite tiers](https://quickbooks.intuit.com/learn-support/en-global/help-article/product-setup/learn-intuit-accountant-suite/L86IyeoWN_ROW_en)
- [Claude usage limits: 5-hour and weekly windows](https://www.ai-toolbox.co/claude-management-and-productivity/claude-usage-limits-2026)
- [Cursor's move to a cost-based credit pool](https://vantage.sh/blog/cursor-pricing-explained)
- [Bedrock quota: output tokens count 5 times (AWS blog)](https://aws.amazon.com/ko/blogs/tech/bedrock-usage)
- [Anthropic cookbook: context engineering (compaction, tool-result clearing, memory)](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)
- [Zuplo: token-based rate limiting for AI agents](https://zuplo.com/learning-center/token-based-rate-limiting-ai-agents.md)
- [Gravitee: token budgets for agents](https://www.gravitee.io/token-budgets.html)
- [Yotta Labs: setting token budgets](https://www.yottalabs.ai/post/set-token-budgets-for-an-ai-product)
- [LiteLLM budgets and rate limits](https://docs.litellm.ai/docs/proxy/users)
- [LiteLLM Bedrock pass-through](https://docs.litellm.ai/docs/pass_through/bedrock)
- [LiteLLM multiple budget windows (docs mirror)](https://litellm.docs.miniasp.com/en/docs/proxy/users)
- [LiteLLM PyPI compromise, 24 Mar 2026](https://futuresearch.ai/blog/litellm-pypi-supply-chain-attack/)
- [OpenMeter entitlements](https://openmeter.io/docs/billing/entitlements/overview)
- [Kong acquires OpenMeter](https://konghq.com/blog/news/kong-acquires-openmeter)
- [Lago vs Flexprice](https://openalternative.co/compare/flexprice/vs/lago)
- [Unkey rate limiting](https://www.unkey.com/docs/ratelimiting)
