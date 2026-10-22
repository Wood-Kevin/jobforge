"""Startup recovery resets orphaned work (ARCHITECTURE 4.3)."""
import json

from conftest import NOW, add_analysis, seed_basics
from jobforge_agent.db.connect import open_connection
from jobforge_agent.db.recovery import MAX_ATTEMPTS, recover


def _second_raw(db, ids, n):
    return db.execute(
        "INSERT INTO raw_postings (employer_id, source_job_id, fetched_at_ms, payload, payload_sha256, material_sha256, adapter_version) "
        "VALUES (?, 'greenhouse:acme:1', ?, ?, ?, ?, 'gh-1') RETURNING id",
        (ids["employer"], NOW, json.dumps({"v": n}), f"p{n}", f"m{n}")).fetchone()[0]


def test_recovery_resets_every_kind_of_orphan(db, paths):
    ids = seed_basics(db)
    db.execute("INSERT INTO source_fetches (employer_id, started_at_ms) VALUES (?, ?)", (ids["employer"], NOW))
    db.execute("INSERT INTO employer_poll_state (employer_id, next_poll_at_ms, lease_until_ms) VALUES (?, ?, ?)", (ids["employer"], NOW, NOW + 300_000))
    running = add_analysis(db, ids, status="running", attempts=0)
    exhausted = add_analysis(db, ids, raw_id=_second_raw(db, ids, 2), status="running", attempts=MAX_ATTEMPTS - 1, version="e1-2")
    db.execute("INSERT INTO agent_runs (pid, version, elevated, started_at_ms, heartbeat_at_ms) VALUES (1, '0.1.0', 0, ?, ?)", (NOW, NOW + 5000))

    report = recover(open_connection(paths.db, "lifecycle"), NOW + 60_000)

    assert len(report.interrupted_fetches) == 1
    fetch = db.execute("SELECT outcome, finished_at_ms FROM source_fetches").fetchone()
    assert tuple(fetch) == ("INTERRUPTED", NOW + 60_000)
    assert db.execute("SELECT lease_until_ms FROM employer_poll_state").fetchone()[0] is None
    statuses = dict(db.execute("SELECT id, status FROM posting_analyses").fetchall())
    # The two running analyses belong to the same job: the older one is superseded, so the
    # one-pending-per-job index cannot be violated by recovery; the newer one hit MAX_ATTEMPTS.
    assert statuses[running] == "superseded"
    assert statuses[exhausted] == "failed"
    run = db.execute("SELECT stopped_at_ms, stop_reason FROM agent_runs").fetchone()
    assert tuple(run) == (NOW + 5000, "crash_recovered")
    assert report.crashed_runs == [1]


def test_running_analysis_returns_to_pending(db, paths):
    ids = seed_basics(db)
    a = add_analysis(db, ids, status="running", attempts=1)
    recover(open_connection(paths.db, "lifecycle"), NOW)
    row = db.execute("SELECT status, attempts, last_error FROM posting_analyses WHERE id = ?", (a,)).fetchone()
    assert tuple(row) == ("pending", 2, "interrupted")


def test_running_analysis_with_newer_pending_is_superseded(db, paths):
    ids = seed_basics(db)
    old = add_analysis(db, ids, status="running")
    new = add_analysis(db, ids, raw_id=_second_raw(db, ids, 3), status="pending", version="e1-3")
    report = recover(open_connection(paths.db, "lifecycle"), NOW)
    assert report.superseded_analyses == 1
    statuses = dict(db.execute("SELECT id, status FROM posting_analyses").fetchall())
    assert (statuses[old], statuses[new]) == ("superseded", "pending")
    assert db.execute("SELECT count(*) FROM posting_analyses WHERE status = 'pending'").fetchone()[0] == 1


def test_clean_database_reports_nothing(db, paths):
    report = recover(open_connection(paths.db, "lifecycle"), NOW)
    assert report.interrupted_fetches == [] and report.crashed_runs == []
    assert report.reset_analyses == report.reset_evaluations == 0
