-- Removes the AI usage limit tables (usage_limits.sql). Run as the database owner.
-- Set USAGE_ENFORCE=off and USAGE_DB=off on the AI service first. The usage history is lost.
BEGIN;
DROP TABLE IF EXISTS public.ai_usage_charges;
DROP TABLE IF EXISTS public.ai_usage_events;
DROP TABLE IF EXISTS public.ai_usage_alerts;
DROP TABLE IF EXISTS public.ai_topups;
DROP TABLE IF EXISTS public.ai_user_caps;
DROP TABLE IF EXISTS public.ai_owner_plans;
COMMIT;
