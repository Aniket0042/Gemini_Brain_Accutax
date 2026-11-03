-- AI usage limits per owner account (docs/ORG_TOKEN_LIMITS_STRATEGY.md).
-- Run as the database owner. Idempotent. Rollback: usage_limits_rollback.sql.
-- 1 credit = $0.001 of model cost. An owner is organizations.user_id; one bucket covers all of an owner's
-- organizations. owner_user_id 0 means "internal" (Accutax staff) or "not known".

BEGIN;

-- The plan of an owner. No row: the "standard" plan.
CREATE TABLE IF NOT EXISTS public.ai_owner_plans (
    owner_user_id       bigint PRIMARY KEY,
    plan                text          NOT NULL DEFAULT 'standard',   -- trial | standard | pro | unlimited
    monthly_credits     numeric(12,3),                               -- overrides the plan's per-seat size; null = plan
    user_daily_credits  numeric(10,3),                               -- default personal cap; null = plan
    updated_by          bigint,
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ai_owner_plans_plan CHECK (plan IN ('trial', 'standard', 'pro', 'unlimited'))
);

-- A person's cap inside one owner's bucket, set by that owner.
CREATE TABLE IF NOT EXISTS public.ai_user_caps (
    owner_user_id  bigint        NOT NULL,
    user_id        bigint        NOT NULL,
    daily_credits  numeric(10,3),                -- null = the plan's default
    disabled       boolean       NOT NULL DEFAULT false,
    updated_by     bigint,
    updated_at     timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_user_id, user_id)
);

-- Extra credits granted to an owner, usable until they expire.
CREATE TABLE IF NOT EXISTS public.ai_topups (
    id             bigserial     PRIMARY KEY,
    owner_user_id  bigint        NOT NULL,
    credits        numeric(12,3) NOT NULL CHECK (credits > 0),
    reason         text,
    expires_at     timestamptz,
    created_by     bigint,
    created_at     timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ai_topups_owner ON public.ai_topups (owner_user_id, expires_at);

-- One row per piece of AI spend (an agent answer, a file read, a model call outside both).
-- status 'running' with reserved credits is the hold taken before a question; it is replaced by the real cost.
CREATE TABLE IF NOT EXISTS public.ai_usage_events (
    id                  bigserial     PRIMARY KEY,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    kind                text          NOT NULL,
    status              text          NOT NULL,
    user_id             bigint,
    session_id          text,
    organization_ids    bigint[]      NOT NULL DEFAULT '{}',
    model_mode          text          NOT NULL DEFAULT 'full',          -- full | lite
    calls               integer       NOT NULL DEFAULT 0,
    input_tokens        bigint        NOT NULL DEFAULT 0,
    output_tokens       bigint        NOT NULL DEFAULT 0,
    cache_read_tokens   bigint        NOT NULL DEFAULT 0,
    cache_write_tokens  bigint        NOT NULL DEFAULT 0,
    cost_usd            numeric(12,6) NOT NULL DEFAULT 0,
    credits             numeric(12,3) NOT NULL DEFAULT 0,
    reserved_credits    numeric(10,3) NOT NULL DEFAULT 0,
    models              jsonb
);
CREATE INDEX IF NOT EXISTS ai_usage_events_created ON public.ai_usage_events (created_at);
CREATE INDEX IF NOT EXISTS ai_usage_events_running ON public.ai_usage_events (created_at) WHERE status = 'running';
-- The speed limits: a person's questions running now and asked in the last minute.
CREATE INDEX IF NOT EXISTS ai_usage_events_user_time ON public.ai_usage_events (user_id, created_at);

-- Who pays for each event: equal parts across the owners of the organizations it read.
CREATE TABLE IF NOT EXISTS public.ai_usage_charges (
    event_id       bigint        NOT NULL REFERENCES public.ai_usage_events(id) ON DELETE CASCADE,
    owner_user_id  bigint        NOT NULL,
    user_id        bigint,
    credits        numeric(12,3) NOT NULL,
    created_at     timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, owner_user_id)
);
CREATE INDEX IF NOT EXISTS ai_usage_charges_owner_time ON public.ai_usage_charges (owner_user_id, created_at);
CREATE INDEX IF NOT EXISTS ai_usage_charges_user_time ON public.ai_usage_charges (user_id, owner_user_id, created_at);

-- Alerts already raised, so each threshold fires once per cycle (owner 0: the platform's daily spend).
CREATE TABLE IF NOT EXISTS public.ai_usage_alerts (
    owner_user_id  bigint      NOT NULL,
    cycle          text        NOT NULL,      -- 2026-10 for an owner's month, 2026-10-24 for a platform day
    level          text        NOT NULL,      -- 50 | 80 | 100 | lite_end | platform_alert | platform_pause | spike:<user>
    detail         jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_user_id, cycle, level)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON
    public.ai_owner_plans, public.ai_user_caps, public.ai_topups,
    public.ai_usage_events, public.ai_usage_charges, public.ai_usage_alerts
TO gemini_brain_ro;
GRANT USAGE, SELECT ON SEQUENCE public.ai_topups_id_seq, public.ai_usage_events_id_seq TO gemini_brain_ro;

COMMIT;
