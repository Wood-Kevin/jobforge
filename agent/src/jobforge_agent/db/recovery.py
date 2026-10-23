"""Startup recovery (ARCHITECTURE 4.3, "Crash recovery").

Runs after the instance lock is taken. The lock proves no other agent is running, so every
in-flight row left in the database belongs to a dead process and is reset here, in one
transaction.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class RecoveryReport:
    interrupted_fetches: list[int]
    cleared_leases: int
    reset_analyses: int
    superseded_analyses: int
    failed_analyses: int
    reset_evaluations: int
    superseded_evaluations: int
    failed_evaluations: int
    crashed_runs: list[int]


def recover(conn: sqlite3.Connection, now_ms: int) -> RecoveryReport:
    """`conn` must be a 'lifecycle' connection."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        fetches = [r[0] for r in conn.execute(
            "UPDATE source_fetches SET finished_at_ms = ?, outcome = 'INTERRUPTED', error = 'agent stopped mid-poll' "
            "WHERE finished_at_ms IS NULL RETURNING id", (now_ms,))]
        leases = conn.execute(
            "UPDATE employer_poll_state SET lease_until_ms = NULL WHERE lease_until_ms IS NOT NULL").rowcount

        # A running analysis whose job already has a newer pending one would collide with the
        # one-pending-per-job index if reset to pending; it is superseded instead.
        # Likewise, of several running analyses for one job only the newest is kept.
        superseded = conn.execute(
            "UPDATE posting_analyses SET status = 'superseded', lease_until_ms = NULL, last_error = 'interrupted; newer version pending' "
            "WHERE status = 'running' AND EXISTS (SELECT 1 FROM posting_analyses p2 WHERE p2.job_id = posting_analyses.job_id "
            "AND (p2.status = 'pending' OR (p2.status = 'running' AND p2.id > posting_analyses.id)))"
        ).rowcount
        failed_a = conn.execute(
            "UPDATE posting_analyses SET status = 'failed', attempts = attempts + 1, lease_until_ms = NULL, "
            "last_error = 'interrupted ' || (attempts + 1) || ' times' WHERE status = 'running' AND attempts + 1 >= ?",
            (MAX_ATTEMPTS,)).rowcount
        reset_a = conn.execute(
            "UPDATE posting_analyses SET status = 'pending', attempts = attempts + 1, lease_until_ms = NULL, "
            "last_error = 'interrupted' WHERE status = 'running'").rowcount

        # Same rule for evaluations (one pending per job, migration 0004).
        superseded_e = conn.execute(
            "UPDATE evaluations SET status = 'superseded', lease_until_ms = NULL, last_error = 'interrupted; newer evaluation pending' "
            "WHERE status = 'running' AND EXISTS (SELECT 1 FROM evaluations e2 WHERE e2.job_id = evaluations.job_id "
            "AND (e2.status = 'pending' OR (e2.status = 'running' AND e2.id > evaluations.id)))"
        ).rowcount
        failed_e = conn.execute(
            "UPDATE evaluations SET status = 'failed', attempts = attempts + 1, lease_until_ms = NULL, "
            "last_error = 'interrupted ' || (attempts + 1) || ' times' WHERE status = 'running' AND attempts + 1 >= ?",
            (MAX_ATTEMPTS,)).rowcount
        reset_e = conn.execute(
            "UPDATE evaluations SET status = 'pending', attempts = attempts + 1, lease_until_ms = NULL, "
            "last_error = 'interrupted' WHERE status = 'running'").rowcount

        runs = [r[0] for r in conn.execute(
            "UPDATE agent_runs SET stopped_at_ms = heartbeat_at_ms, stop_reason = 'crash_recovered' "
            "WHERE stopped_at_ms IS NULL RETURNING id")]
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return RecoveryReport(fetches, leases, reset_a, superseded, failed_a, reset_e, superseded_e, failed_e, runs)
