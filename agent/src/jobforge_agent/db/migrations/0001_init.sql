-- 0001_init: Phase 1 data model (ARCHITECTURE 4.2).
-- Conventions: STRICT tables; times are UTC epoch milliseconds (*_ms); JSON is TEXT checked
-- with json_valid; enums are TEXT with CHECK. The migration runner wraps this file in one
-- transaction and sets user_version.

-- ===== Global facts (no personal data) =====

CREATE TABLE sources (
    id                    INTEGER PRIMARY KEY,
    kind                  TEXT NOT NULL CHECK (kind IN ('greenhouse', 'lever', 'ashby')),
    host                  TEXT NOT NULL UNIQUE,
    enabled               INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    terms_reviewed_on     TEXT,
    terms_note            TEXT,
    default_interval_s    INTEGER NOT NULL DEFAULT 900 CHECK (default_interval_s > 0),
    min_interval_s        INTEGER NOT NULL DEFAULT 300 CHECK (min_interval_s > 0),
    max_requests_per_hour INTEGER NOT NULL DEFAULT 120 CHECK (max_requests_per_hour > 0),
    max_concurrency       INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency > 0),
    request_spacing_ms    INTEGER NOT NULL DEFAULT 2000 CHECK (request_spacing_ms >= 0),
    crawl_delay_ms        INTEGER,
    conditional_requests  TEXT NOT NULL DEFAULT 'none' CHECK (conditional_requests IN ('none', 'etag', 'last_modified', 'updated_at')),
    backoff_until_ms      INTEGER,
    backoff_reason        TEXT,
    -- A source cannot be enabled before its terms review is recorded (ARCHITECTURE 8).
    CHECK (enabled = 0 OR terms_reviewed_on IS NOT NULL)
) STRICT;

CREATE TABLE employers (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    slug            TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'dead', 'blocked')),
    poll_interval_s INTEGER CHECK (poll_interval_s IS NULL OR poll_interval_s > 0),
    UNIQUE (source_id, slug)
) STRICT;

CREATE TABLE employer_poll_state (
    employer_id          INTEGER PRIMARY KEY REFERENCES employers(id),
    next_poll_at_ms      INTEGER NOT NULL,
    last_attempt_at_ms   INTEGER,
    last_success_at_ms   INTEGER,
    last_outcome         TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    lease_until_ms       INTEGER,
    etag                 TEXT,
    last_modified        TEXT
) STRICT;
CREATE INDEX employer_poll_state_due ON employer_poll_state (next_poll_at_ms);

CREATE TABLE robots_cache (
    host          TEXT PRIMARY KEY,
    fetched_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    http_status   INTEGER NOT NULL,
    body          TEXT
) STRICT;

CREATE TABLE source_fetches (
    id               INTEGER PRIMARY KEY,
    employer_id      INTEGER NOT NULL REFERENCES employers(id),
    started_at_ms    INTEGER NOT NULL,
    finished_at_ms   INTEGER,
    outcome          TEXT CHECK (outcome IS NULL OR outcome IN ('OK', 'EMPTY', 'NOT_MODIFIED', 'ERROR', 'SKIPPED', 'INTERRUPTED', 'BUDGET_DEFERRED')),
    http_status      INTEGER,
    retry_after_s    INTEGER,
    requests_made    INTEGER NOT NULL DEFAULT 0,
    bytes_received   INTEGER NOT NULL DEFAULT 0,
    duration_ms      INTEGER,
    postings_seen    INTEGER,
    new_count        INTEGER NOT NULL DEFAULT 0,
    changed_count    INTEGER NOT NULL DEFAULT 0,
    closed_count     INTEGER NOT NULL DEFAULT 0,
    complete         INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
    error            TEXT
) STRICT;
-- No overlapping polls for one employer, whatever the scheduler does (SPEC D5).
CREATE UNIQUE INDEX source_fetches_one_in_flight ON source_fetches (employer_id) WHERE finished_at_ms IS NULL;
CREATE INDEX source_fetches_by_employer ON source_fetches (employer_id, started_at_ms);

CREATE TABLE raw_postings (
    id               INTEGER PRIMARY KEY,
    employer_id      INTEGER NOT NULL REFERENCES employers(id),
    source_job_id    TEXT NOT NULL,
    fetched_at_ms    INTEGER NOT NULL,
    first_fetch_id   INTEGER REFERENCES source_fetches(id),
    listing_url      TEXT,
    apply_url        TEXT,
    payload          TEXT NOT NULL CHECK (json_valid(payload)),
    description_raw  TEXT,
    description_text TEXT,
    payload_sha256   TEXT NOT NULL,
    material_sha256  TEXT NOT NULL,
    adapter_version  TEXT NOT NULL,
    UNIQUE (source_job_id, material_sha256)
) STRICT;

CREATE TABLE jobs (
    id                     INTEGER PRIMARY KEY,
    source_job_id          TEXT NOT NULL UNIQUE,
    current_raw_posting_id INTEGER NOT NULL REFERENCES raw_postings(id),
    last_payload_sha256    TEXT NOT NULL,
    title                  TEXT NOT NULL,
    company                TEXT NOT NULL,
    location_text          TEXT,
    work_mode              TEXT CHECK (work_mode IS NULL OR work_mode IN ('remote', 'hybrid', 'onsite')),
    salary_min             REAL,
    salary_max             REAL,
    salary_period          TEXT CHECK (salary_period IS NULL OR salary_period IN ('YEAR', 'HOUR', 'MONTH', 'WEEK')),
    posted_at              TEXT,
    first_seen_at_ms       INTEGER NOT NULL,
    last_seen_at_ms        INTEGER NOT NULL,
    missing_polls          INTEGER NOT NULL DEFAULT 0,
    closed_at_ms           INTEGER
) STRICT;

CREATE TABLE change_events (
    id            INTEGER PRIMARY KEY,
    type          TEXT NOT NULL,
    job_id        INTEGER,
    employer_id   INTEGER,
    ref           TEXT CHECK (ref IS NULL OR json_valid(ref)),
    created_at_ms INTEGER NOT NULL
) STRICT;

-- ===== Personal data (one database file per Windows user, so no owner_id) =====

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
INSERT INTO settings (key, value) VALUES ('discovery_paused', '0'), ('autostart', '0');

CREATE TABLE model_configs (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    provider_label          TEXT NOT NULL,
    base_url                TEXT NOT NULL,
    model_id                TEXT NOT NULL,
    params                  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(params)),
    structured_output_mode  TEXT NOT NULL DEFAULT 'json_schema' CHECK (structured_output_mode IN ('json_schema', 'json_object', 'prompt_only')),
    enabled                 INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    tested_at_ms            INTEGER,
    -- A config is enabled only after the load-and-snippet test (ARCHITECTURE 6.8).
    CHECK (enabled = 0 OR tested_at_ms IS NOT NULL)
) STRICT;

CREATE TABLE posting_analyses (
    id                        INTEGER PRIMARY KEY,
    raw_posting_id            INTEGER NOT NULL REFERENCES raw_postings(id),
    job_id                    INTEGER NOT NULL REFERENCES jobs(id),
    model_config_id           INTEGER NOT NULL REFERENCES model_configs(id),
    model_id_reported         TEXT,
    extraction_prompt_version TEXT NOT NULL,
    segmenter_version         TEXT NOT NULL,
    interpreter_version       TEXT NOT NULL,
    sections                  TEXT CHECK (sections IS NULL OR json_valid(sections)),
    status                    TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed', 'superseded')),
    attempts                  INTEGER NOT NULL DEFAULT 0,
    lease_until_ms            INTEGER,
    last_error                TEXT,
    raw_response              TEXT CHECK (raw_response IS NULL OR json_valid(raw_response)),
    created_at_ms             INTEGER NOT NULL,
    UNIQUE (raw_posting_id, model_config_id, extraction_prompt_version, segmenter_version, interpreter_version)
) STRICT;
-- At most one pending analysis per job (supersession, ARCHITECTURE 3.3).
CREATE UNIQUE INDEX posting_analyses_one_pending_per_job ON posting_analyses (job_id) WHERE status = 'pending';

CREATE TABLE requirements (
    id                    INTEGER PRIMARY KEY,
    posting_analysis_id   INTEGER NOT NULL REFERENCES posting_analyses(id),
    text                  TEXT NOT NULL,
    jd_quote              TEXT NOT NULL,
    quote_found           INTEGER NOT NULL CHECK (quote_found IN (0, 1)),
    quote_occurrences     INTEGER NOT NULL DEFAULT 0,
    section_label         TEXT,
    sentence              TEXT,
    model_importance      TEXT,
    model_polarity        TEXT,
    model_subject         TEXT,
    model_eligibility_type TEXT,
    eligibility_type      TEXT,
    min_years             REAL,
    cue_importance        TEXT,
    cue_polarity          TEXT,
    cue_subject           TEXT,
    final_class           TEXT NOT NULL CHECK (final_class IN ('hard', 'negotiable', 'preferred', 'context', 'negated')),
    interpretation_status TEXT NOT NULL CHECK (interpretation_status IN ('confirmed', 'conflict', 'unverifiable')),
    origin                TEXT NOT NULL DEFAULT 'model' CHECK (origin IN ('model', 'unextracted_eligibility')),
    flags                 TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(flags))
) STRICT;

CREATE TABLE search_profiles (
    id            INTEGER PRIMARY KEY,
    version       INTEGER NOT NULL UNIQUE,
    rules         TEXT NOT NULL CHECK (json_valid(rules)),
    created_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE filter_results (
    id                INTEGER PRIMARY KEY,
    job_id            INTEGER NOT NULL REFERENCES jobs(id),
    raw_posting_id    INTEGER NOT NULL REFERENCES raw_postings(id),
    search_profile_id INTEGER NOT NULL REFERENCES search_profiles(id),
    ruleset_version   TEXT NOT NULL,
    verdict           TEXT NOT NULL CHECK (verdict IN ('pass', 'reject')),
    reasons           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(reasons)),
    created_at_ms     INTEGER NOT NULL,
    UNIQUE (raw_posting_id, search_profile_id, ruleset_version)
) STRICT;

CREATE TABLE filter_overrides (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id),
    note          TEXT,
    created_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE resumes (
    id                INTEGER PRIMARY KEY,
    label             TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    original_bytes    BLOB NOT NULL,
    mime              TEXT NOT NULL,
    extracted_text    TEXT,
    sha256            TEXT NOT NULL,
    uploaded_at_ms    INTEGER NOT NULL
) STRICT;

CREATE TABLE experience_items (
    id             INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('role', 'skill', 'cert', 'education', 'achievement', 'eligibility')),
    statement      TEXT NOT NULL,
    attributes     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes)),
    start_date     TEXT,
    end_date       TEXT,
    years          REAL,
    status         TEXT NOT NULL DEFAULT 'self_reported' CHECK (status IN ('verified', 'self_reported')),
    source         TEXT NOT NULL,
    verified_at_ms INTEGER,
    -- Verification is a timestamped user action, never a default (SPEC D2).
    CHECK (status <> 'verified' OR verified_at_ms IS NOT NULL)
) STRICT;

CREATE TABLE profile_completeness (
    id                         INTEGER PRIMARY KEY CHECK (id = 1),
    cert_list_complete         INTEGER NOT NULL DEFAULT 0 CHECK (cert_list_complete IN (0, 1)),
    eligibility_facts_complete INTEGER NOT NULL DEFAULT 0 CHECK (eligibility_facts_complete IN (0, 1)),
    updated_at_ms              INTEGER NOT NULL
) STRICT;

CREATE TABLE profile_snapshots (
    id            INTEGER PRIMARY KEY,
    sha256        TEXT NOT NULL UNIQUE,
    completeness  TEXT NOT NULL CHECK (json_valid(completeness)),
    created_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE profile_snapshot_items (
    snapshot_id INTEGER NOT NULL REFERENCES profile_snapshots(id),
    item_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    statement   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('verified', 'self_reported')),
    attributes  TEXT NOT NULL CHECK (json_valid(attributes)),
    PRIMARY KEY (snapshot_id, item_id)
) STRICT;

CREATE TABLE evaluations (
    id                   INTEGER PRIMARY KEY,
    posting_analysis_id  INTEGER NOT NULL REFERENCES posting_analyses(id),
    profile_snapshot_id  INTEGER NOT NULL REFERENCES profile_snapshots(id),
    model_config_id      INTEGER NOT NULL REFERENCES model_configs(id),
    model_id_reported    TEXT,
    match_prompt_version TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed')),
    attempts             INTEGER NOT NULL DEFAULT 0,
    lease_until_ms       INTEGER,
    last_error           TEXT,
    raw_response         TEXT CHECK (raw_response IS NULL OR json_valid(raw_response)),
    requested_by         TEXT NOT NULL CHECK (requested_by IN ('pipeline', 'user', 'override')),
    created_at_ms        INTEGER NOT NULL,
    UNIQUE (posting_analysis_id, profile_snapshot_id, model_config_id, match_prompt_version)
) STRICT;

CREATE TABLE inferred_claims (
    id                       INTEGER PRIMARY KEY,
    statement                TEXT NOT NULL,
    kind                     TEXT NOT NULL,
    rationale                TEXT,
    evidence                 TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence)),
    proposed_by_evaluation_id INTEGER REFERENCES evaluations(id),
    state                    TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'accepted', 'rejected')),
    accepted_as_item_id      INTEGER REFERENCES experience_items(id),
    created_at_ms            INTEGER NOT NULL
) STRICT;

CREATE TABLE requirement_matches (
    id               INTEGER PRIMARY KEY,
    evaluation_id    INTEGER NOT NULL REFERENCES evaluations(id),
    requirement_id   INTEGER NOT NULL REFERENCES requirements(id),
    matched_by       TEXT NOT NULL CHECK (matched_by IN ('model', 'eligibility_fact')),
    match_type       TEXT NOT NULL CHECK (match_type IN ('direct', 'transferable', 'gap', 'unknown')),
    basis            TEXT NOT NULL CHECK (basis IN ('verified', 'self_reported', 'none')),
    bridge           TEXT,
    transfer_limits  TEXT,
    gap_size         TEXT CHECK (gap_size IS NULL OR json_valid(gap_size)),
    gap_materiality  TEXT NOT NULL DEFAULT 'none' CHECK (gap_materiality IN ('material', 'minor', 'none')),
    validation_flags TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(validation_flags)),
    UNIQUE (evaluation_id, requirement_id)
) STRICT;

-- A citation can only name an item that is in the snapshot the evaluation saw. Inferred
-- claims never enter snapshots, so they cannot be cited (SPEC D2).
CREATE TABLE match_citations (
    requirement_match_id INTEGER NOT NULL REFERENCES requirement_matches(id),
    snapshot_id          INTEGER NOT NULL,
    item_id              INTEGER NOT NULL,
    PRIMARY KEY (requirement_match_id, item_id),
    FOREIGN KEY (snapshot_id, item_id) REFERENCES profile_snapshot_items(snapshot_id, item_id)
) STRICT;

CREATE TABLE recommendations (
    id                   INTEGER PRIMARY KEY,
    evaluation_id        INTEGER NOT NULL REFERENCES evaluations(id),
    search_profile_id    INTEGER NOT NULL REFERENCES search_profiles(id),
    verdict_rule_version TEXT NOT NULL,
    eligibility          TEXT NOT NULL CHECK (eligibility IN ('ELIGIBLE', 'BLOCKED', 'UNKNOWN')),
    eligibility_reasons  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(eligibility_reasons)),
    fit                  TEXT NOT NULL CHECK (fit IN ('STRONG', 'COMPETITIVE', 'STRETCH', 'WEAK', 'INSUFFICIENT')),
    fit_counts           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(fit_counts)),
    opportunity          TEXT NOT NULL CHECK (opportunity IN ('HIGH', 'MEDIUM', 'LOW', 'UNKNOWN')),
    opportunity_factors  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(opportunity_factors)),
    recommendation       TEXT NOT NULL CHECK (recommendation IN ('APPLY', 'STRETCH', 'REVIEW', 'SKIP')),
    decisive_reasons     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(decisive_reasons)),
    rank_key             REAL,
    computed_at_ms       INTEGER NOT NULL,
    UNIQUE (evaluation_id, search_profile_id, verdict_rule_version)
) STRICT;

CREATE TABLE applications (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id),
    status        TEXT NOT NULL CHECK (status IN ('saved', 'applied', 'interviewing', 'offer', 'rejected', 'withdrawn')),
    notes         TEXT,
    created_at_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE application_events (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    from_status    TEXT,
    to_status      TEXT NOT NULL,
    at_ms          INTEGER NOT NULL,
    note           TEXT
) STRICT;

CREATE TABLE agent_runs (
    id              INTEGER PRIMARY KEY,
    pid             INTEGER NOT NULL,
    version         TEXT NOT NULL,
    elevated        INTEGER NOT NULL CHECK (elevated IN (0, 1)),
    started_at_ms   INTEGER NOT NULL,
    heartbeat_at_ms INTEGER NOT NULL,
    stopped_at_ms   INTEGER,
    stop_reason     TEXT
) STRICT;

-- ===== Append-only history (SPEC D9) =====
-- Triggers apply to every connection, including code written later. A migration that has
-- to rebuild one of these tables must drop and recreate its triggers in the same file;
-- tests/test_triggers.py fails if any of them is missing after the latest migration.

CREATE TRIGGER raw_postings_no_update BEFORE UPDATE ON raw_postings
BEGIN SELECT RAISE(ABORT, 'raw_postings is append-only'); END;
CREATE TRIGGER raw_postings_no_delete BEFORE DELETE ON raw_postings
BEGIN SELECT RAISE(ABORT, 'raw_postings is append-only'); END;

CREATE TRIGGER search_profiles_no_update BEFORE UPDATE ON search_profiles
BEGIN SELECT RAISE(ABORT, 'search_profiles is append-only'); END;
CREATE TRIGGER search_profiles_no_delete BEFORE DELETE ON search_profiles
BEGIN SELECT RAISE(ABORT, 'search_profiles is append-only'); END;

CREATE TRIGGER profile_snapshots_no_update BEFORE UPDATE ON profile_snapshots
BEGIN SELECT RAISE(ABORT, 'profile_snapshots is append-only'); END;
CREATE TRIGGER profile_snapshots_no_delete BEFORE DELETE ON profile_snapshots
BEGIN SELECT RAISE(ABORT, 'profile_snapshots is append-only'); END;

CREATE TRIGGER profile_snapshot_items_no_update BEFORE UPDATE ON profile_snapshot_items
BEGIN SELECT RAISE(ABORT, 'profile_snapshot_items is append-only'); END;
CREATE TRIGGER profile_snapshot_items_no_delete BEFORE DELETE ON profile_snapshot_items
BEGIN SELECT RAISE(ABORT, 'profile_snapshot_items is append-only'); END;

CREATE TRIGGER application_events_no_update BEFORE UPDATE ON application_events
BEGIN SELECT RAISE(ABORT, 'application_events is append-only'); END;
CREATE TRIGGER application_events_no_delete BEFORE DELETE ON application_events
BEGIN SELECT RAISE(ABORT, 'application_events is append-only'); END;

-- change_events is a notification log: never edited, pruned only after 7 days.
CREATE TRIGGER change_events_no_update BEFORE UPDATE ON change_events
BEGIN SELECT RAISE(ABORT, 'change_events is append-only'); END;
CREATE TRIGGER change_events_prune_only_old BEFORE DELETE ON change_events
WHEN OLD.created_at_ms > (CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 604800000)
BEGIN SELECT RAISE(ABORT, 'change_events rows younger than 7 days cannot be deleted'); END;
