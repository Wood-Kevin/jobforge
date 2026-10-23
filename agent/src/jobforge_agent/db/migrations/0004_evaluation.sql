-- 0004_evaluation: what P1.7 (E3-E6) needs beyond 0001.

-- evaluations gains job_id (denormalized, like posting_analyses) and a 'superseded' status, so a
-- profile edit or a new posting version replaces a still-pending evaluation instead of leaving
-- stale work in the queue. SQLite cannot change a CHECK in place, so the table is rebuilt. No
-- earlier phase wrote evaluations, so the copy is empty on every real database; the INSERT keeps
-- it correct anyway. Child tables reference the name "evaluations", which the rename restores.
CREATE TABLE evaluations_new (
    id                   INTEGER PRIMARY KEY,
    posting_analysis_id  INTEGER NOT NULL REFERENCES posting_analyses(id),
    job_id               INTEGER NOT NULL REFERENCES jobs(id),
    profile_snapshot_id  INTEGER NOT NULL REFERENCES profile_snapshots(id),
    model_config_id      INTEGER NOT NULL REFERENCES model_configs(id),
    model_id_reported    TEXT,
    match_prompt_version TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed', 'superseded')),
    attempts             INTEGER NOT NULL DEFAULT 0,
    lease_until_ms       INTEGER,
    last_error           TEXT,
    raw_response         TEXT CHECK (raw_response IS NULL OR json_valid(raw_response)),
    requested_by         TEXT NOT NULL CHECK (requested_by IN ('pipeline', 'user', 'override')),
    created_at_ms        INTEGER NOT NULL,
    UNIQUE (posting_analysis_id, profile_snapshot_id, model_config_id, match_prompt_version)
) STRICT;
INSERT INTO evaluations_new (id, posting_analysis_id, job_id, profile_snapshot_id, model_config_id, model_id_reported,
                             match_prompt_version, status, attempts, lease_until_ms, last_error, raw_response, requested_by,
                             created_at_ms)
SELECT e.id, e.posting_analysis_id, pa.job_id, e.profile_snapshot_id, e.model_config_id, e.model_id_reported,
       e.match_prompt_version, e.status, e.attempts, e.lease_until_ms, e.last_error, e.raw_response, e.requested_by,
       e.created_at_ms
FROM evaluations e JOIN posting_analyses pa ON pa.id = e.posting_analysis_id;
DROP TABLE evaluations;
ALTER TABLE evaluations_new RENAME TO evaluations;
-- At most one pending evaluation per job (same supersession rule as posting_analyses).
CREATE UNIQUE INDEX evaluations_one_pending_per_job ON evaluations (job_id) WHERE status = 'pending';
CREATE INDEX evaluations_by_job ON evaluations (job_id, status);

-- E4 computes years from the evaluated snapshot, so the snapshot keeps what the item said about
-- time. ADD COLUMN leaves the insert-only triggers in place (checked after every migration).
ALTER TABLE profile_snapshot_items ADD COLUMN years REAL;
ALTER TABLE profile_snapshot_items ADD COLUMN start_date TEXT;
ALTER TABLE profile_snapshot_items ADD COLUMN end_date TEXT;

CREATE INDEX requirement_matches_by_evaluation ON requirement_matches (evaluation_id);
CREATE INDEX recommendations_by_evaluation ON recommendations (evaluation_id);
