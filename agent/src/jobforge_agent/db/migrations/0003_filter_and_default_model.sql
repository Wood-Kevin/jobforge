-- 0003_filter_and_default_model: the default model config analyses are queued against (P1.4).

-- Q4 (Kevin, 2026-10-21): Gemma 4 12B in LM Studio, reasoning disabled. Parameters from spike
-- P1.0b (ARCHITECTURE Appendix A): JSON schema output, temperature 0, fixed seed, 16k context,
-- max_tokens bounded so a runaway answer fails as budget_exhausted instead of filling the context.
-- Disabled until the P1.6 load-and-snippet test sets tested_at_ms (CHECK in 0001).
INSERT INTO model_configs (name, provider_label, base_url, model_id, params, structured_output_mode, enabled)
VALUES ('Gemma 4 12B (LM Studio, reasoning off)', 'LM Studio', 'http://127.0.0.1:1234/v1', 'google/gemma-4-12b-qat',
        '{"reasoning_effort": "none", "temperature": 0, "seed": 7, "max_tokens": 4000, "context_length": 16384}',
        'json_schema', 0);

INSERT INTO settings (key, value)
VALUES ('default_model_config_id', (SELECT CAST(id AS TEXT) FROM model_configs WHERE model_id = 'google/gemma-4-12b-qat'));
