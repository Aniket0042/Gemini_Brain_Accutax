-- ai_bot_tables.sql — Tables for bots: a user's configured copy of an agent (docs/VAT_BOT_IMPLEMENTATION_PLAN.md, phase 2).
--
-- Run as the table owner (postgres) on accutax_bk_1_5. Gemini Brain connects as gemini_brain_ro, which cannot run DDL,
-- so it only gets read and write rights here. Safe to run twice: every statement uses IF NOT EXISTS.
-- Needs PostgreSQL 13+ (gen_random_uuid is built in from 13).
--
-- Rollback (removes every bot, run, finding and memory line):
--   DROP TABLE IF EXISTS ai_bot_memory, ai_bot_findings, ai_bot_runs, ai_bots;

BEGIN;

-- One row per bot. `draft` is the validated BotDraft (bots/schema.py): schedules, watches, delivery, rules, notes.
CREATE TABLE IF NOT EXISTS ai_bots (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id  integer     NOT NULL,
    agent          text        NOT NULL,
    name           text        NOT NULL,
    org_ids        integer[]   NOT NULL DEFAULT '{}',
    draft          jsonb       NOT NULL,
    status         text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'error')),
    status_note    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    last_run_at    timestamptz,
    next_run_at    timestamptz
);
CREATE INDEX IF NOT EXISTS idx_ai_bots_owner ON ai_bots (owner_user_id);
CREATE INDEX IF NOT EXISTS idx_ai_bots_due ON ai_bots (status, next_run_at);

-- One row per run. `summary` holds counts only (bills checked, groups, findings, credits), never document details.
CREATE TABLE IF NOT EXISTS ai_bot_runs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id       uuid        NOT NULL REFERENCES ai_bots (id) ON DELETE CASCADE,
    trigger      text        NOT NULL CHECK (trigger IN ('schedule', 'first_run', 'manual', 'event')),
    status       text        NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed', 'skipped')),
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    credits      integer     NOT NULL DEFAULT 0,
    summary      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error        text
);
CREATE INDEX IF NOT EXISTS idx_ai_bot_runs_bot ON ai_bot_runs (bot_id, started_at DESC);

-- One row per problem a bot has seen. `key` is vat_review.tool.item_key, stable across runs, so a run can tell new
-- findings from old ones and mark the ones that went away as fixed.
CREATE TABLE IF NOT EXISTS ai_bot_findings (
    bot_id          uuid        NOT NULL REFERENCES ai_bots (id) ON DELETE CASCADE,
    key             text        NOT NULL,
    org_id          integer     NOT NULL,
    rule            text        NOT NULL,
    state           text        NOT NULL DEFAULT 'new' CHECK (state IN ('new', 'seen', 'fixed', 'dismissed')),
    amount          numeric(18, 2),
    first_seen_run  uuid        REFERENCES ai_bot_runs (id) ON DELETE SET NULL,
    last_seen_run   uuid        REFERENCES ai_bot_runs (id) ON DELETE SET NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, key)
);
CREATE INDEX IF NOT EXISTS idx_ai_bot_findings_state ON ai_bot_findings (bot_id, state);

-- What a bot has learned: the user's notes and lines made from dismissed findings. Each line is editable or removable.
CREATE TABLE IF NOT EXISTS ai_bot_memory (
    id          bigserial   PRIMARY KEY,
    bot_id      uuid        NOT NULL REFERENCES ai_bots (id) ON DELETE CASCADE,
    text        text        NOT NULL,
    source      text        NOT NULL CHECK (source IN ('note', 'dismissal')),
    finding_key text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_bot_memory_bot ON ai_bot_memory (bot_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON ai_bots, ai_bot_runs, ai_bot_findings, ai_bot_memory TO gemini_brain_ro;
GRANT USAGE, SELECT ON SEQUENCE ai_bot_memory_id_seq TO gemini_brain_ro;

COMMIT;
