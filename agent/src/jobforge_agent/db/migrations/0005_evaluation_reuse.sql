-- 0005_evaluation_reuse: an E3 model answer is reused when the exact same request was already
-- answered by the same model config (P1.7 follow-up). The hash covers the whole request body
-- (prompt, schema, model id, parameters), so a changed prompt or profile item never matches.
-- A verify, an eligibility-fact edit or an E4/E5 version bump sends the same request: the stored
-- answer is replayed through the current code with no model call.
ALTER TABLE evaluations ADD COLUMN request_sha256 TEXT;
CREATE INDEX evaluations_by_request ON evaluations (request_sha256, model_config_id) WHERE status = 'done';
