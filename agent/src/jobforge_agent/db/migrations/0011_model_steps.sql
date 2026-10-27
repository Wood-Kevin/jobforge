-- 0011_model_steps: one model config per model step, and a provider kind per config (SPEC D18, ARCHITECTURE 6.9).
-- - provider_kind picks the request format: 'openai_compatible' (LM Studio, every existing row) or 'chatgpt'
--   (the person's ChatGPT plan through Sign in with ChatGPT, Responses API). provider_label stays display text.
-- - The single default_model_config_id becomes four step settings: model.e1 (posting reading), model.e3
--   (matching), model.resume (resume suggestions) and model.fallback (used by a step whose own config is a cloud
--   config that is capped or unavailable). All four start at the old default, so nothing changes until the
--   person points a step at another config.
ALTER TABLE model_configs ADD COLUMN provider_kind TEXT NOT NULL DEFAULT 'openai_compatible'
    CHECK (provider_kind IN ('openai_compatible', 'chatgpt'));

INSERT INTO settings (key, value)
SELECT k.key, s.value FROM settings s
JOIN (SELECT 'model.e1' AS key UNION ALL SELECT 'model.e3' UNION ALL SELECT 'model.resume' UNION ALL SELECT 'model.fallback') k
WHERE s.key = 'default_model_config_id';

DELETE FROM settings WHERE key = 'default_model_config_id';
