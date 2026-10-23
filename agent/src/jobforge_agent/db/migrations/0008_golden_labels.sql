-- 0008_golden_labels: the golden set (ARCHITECTURE 6.7, P1.9). The user's own call on a job, entered in
-- the window. Append-only: a relabel or a withdrawal is a new row and the latest row per job is the
-- label, so the history of what was labeled when, against which recommendation, is never rewritten.
-- Provenance (Q27): labeled_by is 'user' for labels from the window; labels from anyone else are
-- provisional and need reviewed_by before the report counts them.
CREATE TABLE golden_labels (
    id                       INTEGER PRIMARY KEY,
    job_id                   INTEGER NOT NULL REFERENCES jobs(id),
    raw_posting_id           INTEGER NOT NULL REFERENCES raw_postings(id),
    state                    TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'withdrawn')),
    recommendation           TEXT CHECK (recommendation IN ('APPLY', 'STRETCH', 'REVIEW', 'SKIP')),
    eligibility              TEXT CHECK (eligibility IN ('ELIGIBLE', 'BLOCKED', 'UNKNOWN')),
    -- "I could tell without more information": counts the REVIEWs that should not have been REVIEW.
    obvious                  INTEGER NOT NULL DEFAULT 0 CHECK (obvious IN (0, 1)),
    misread_requirement_ids  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(misread_requirement_ids)),
    note                     TEXT,
    labeled_by               TEXT NOT NULL,
    reviewed_by              TEXT,
    -- What JobForge showed when the label was given (NULL if it had no recommendation yet).
    shown_recommendation_id  INTEGER REFERENCES recommendations(id),
    created_at_ms            INTEGER NOT NULL,
    CHECK (state = 'withdrawn' OR recommendation IS NOT NULL)
) STRICT;
CREATE INDEX golden_labels_job ON golden_labels (job_id, id);

CREATE TRIGGER golden_labels_no_update BEFORE UPDATE ON golden_labels
BEGIN SELECT RAISE(ABORT, 'golden_labels is append-only'); END;
CREATE TRIGGER golden_labels_no_delete BEFORE DELETE ON golden_labels
BEGIN SELECT RAISE(ABORT, 'golden_labels is append-only'); END;
