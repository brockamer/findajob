-- #815: rename onboarding_sessions columns to user-facing names.
--
-- These columns were introduced in the v0.10 migration arc as
-- "tester_*" but the naming is overly narrow — the same field holds
-- the key for any instance owner. The rename makes the intent generic.
--
-- SQLite 3.25+ supports ALTER TABLE … RENAME COLUMN; every active stack
-- runs SQLite 3.35+ (required by the DROP COLUMN path in _bridge_legacy_to_v1).

ALTER TABLE onboarding_sessions RENAME COLUMN tester_openrouter_key TO user_openrouter_key;
ALTER TABLE onboarding_sessions RENAME COLUMN tester_rapidapi_key TO user_rapidapi_key;
