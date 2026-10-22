"""Poll scheduling (ARCHITECTURE 3.3, SPEC D5).

- Due times live in employer_poll_state.next_poll_at_ms; nothing is scheduled in memory.
- A claim is one BEGIN IMMEDIATE transaction: pick due employers, set a lease, open a
  source_fetches row. The partial unique index on unfinished fetches makes a second in-flight
  poll of an employer impossible even if this code were wrong.
- The next poll is computed from completion time, never from the old slot, so missed windows
  collapse into one poll. After a gap (restart, sleep), overdue employers are spread over one
  interval: no catch-up burst.
- All times are passed in (now_ms); the database has no clock.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass

LEASE_MS = 5 * 60 * 1000
MAX_INTERVAL_S = 24 * 3600
MAX_BACKOFF_S = 6 * 3600
JITTER = 0.10
HOST_BACKOFF_MIN_S = 60
DEAD_AFTER_404S = 3
CLAIM_BATCH = 4
HOUR_MS = 3_600_000


def effective_interval_s(override: int | None, default: int, minimum: int) -> int:
    """Employer override, else source default; never below the source's terms floor."""
    return max(minimum, min(override or default, MAX_INTERVAL_S))


def next_after_success(now_ms: int, interval_s: int, rng: random.Random) -> int:
    return now_ms + int(interval_s * 1000 * rng.uniform(1 - JITTER, 1 + JITTER))


def next_after_failure(now_ms: int, interval_s: int, failures: int, rng: random.Random) -> int:
    base = min(interval_s * (2 ** failures), MAX_BACKOFF_S)
    return now_ms + int(base * 1000 * rng.uniform(1 - JITTER, 1 + JITTER))


def respread_at(now_ms: int, interval_s: int, rng: random.Random) -> int:
    return now_ms + int(interval_s * 1000 * rng.uniform(0, 1))


@dataclass(frozen=True)
class Claim:
    fetch_id: int
    employer_id: int
    kind: str
    slug: str
    name: str
    source_id: int
    host: str
    interval_s: int
    concurrency: int
    spacing_s: float
    conditional: str
    etag: str | None
    last_modified: str | None
    consecutive_failures: int


def requests_last_hour(conn: sqlite3.Connection, source_id: int, now_ms: int) -> int:
    return conn.execute(
        "SELECT coalesce(sum(f.requests_made), 0) FROM source_fetches f JOIN employers e ON e.id = f.employer_id "
        "WHERE e.source_id = ? AND f.started_at_ms > ?", (source_id, now_ms - HOUR_MS)).fetchone()[0]


def discovery_paused(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = 'discovery_paused'").fetchone()
    return row is not None and row[0] == "1"


def claim_due(conn: sqlite3.Connection, now_ms: int, batch: int = CLAIM_BATCH) -> tuple[list[Claim], list[int]]:
    """Claim up to `batch` due employers. Returns (claims, ids of sources skipped for budget)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if discovery_paused(conn):
            conn.execute("COMMIT")
            return [], []
        sources = conn.execute(
            "SELECT id, max_requests_per_hour FROM sources WHERE enabled = 1 AND (backoff_until_ms IS NULL OR backoff_until_ms < ?)",
            (now_ms,)).fetchall()
        allowed, over_budget = [], []
        for sid, budget in sources:
            (over_budget if requests_last_hour(conn, sid, now_ms) >= budget else allowed).append(sid)
        if not allowed:
            conn.execute("COMMIT")
            return [], over_budget
        marks = ",".join("?" * len(allowed))
        rows = conn.execute(
            f"""SELECT e.id, s.kind, e.slug, e.display_name, s.id, s.host, e.poll_interval_s, s.default_interval_s,
                       s.min_interval_s, s.max_concurrency, s.request_spacing_ms, s.crawl_delay_ms, s.conditional_requests,
                       p.etag, p.last_modified, p.consecutive_failures
                FROM employer_poll_state p
                JOIN employers e ON e.id = p.employer_id
                JOIN sources s ON s.id = e.source_id
                WHERE e.enabled = 1 AND e.status = 'active' AND s.id IN ({marks})
                  AND p.next_poll_at_ms <= ?
                  AND (p.lease_until_ms IS NULL OR p.lease_until_ms < ?)
                  AND NOT EXISTS (SELECT 1 FROM source_fetches f WHERE f.employer_id = e.id AND f.finished_at_ms IS NULL)
                ORDER BY p.next_poll_at_ms
                LIMIT ?""", (*allowed, now_ms, now_ms, batch)).fetchall()
        claims = []
        for r in rows:
            conn.execute("UPDATE employer_poll_state SET lease_until_ms = ?, last_attempt_at_ms = ? WHERE employer_id = ?",
                         (now_ms + LEASE_MS, now_ms, r[0]))
            fetch_id = conn.execute("INSERT INTO source_fetches (employer_id, started_at_ms) VALUES (?, ?) RETURNING id",
                                    (r[0], now_ms)).fetchone()[0]
            spacing_ms = max(r[10], r[11] or 0)  # never below the robots Crawl-delay
            claims.append(Claim(fetch_id=fetch_id, employer_id=r[0], kind=r[1], slug=r[2], name=r[3], source_id=r[4],
                                host=r[5], interval_s=effective_interval_s(r[6], r[7], r[8]), concurrency=r[9],
                                spacing_s=spacing_ms / 1000, conditional=r[12], etag=r[13], last_modified=r[14],
                                consecutive_failures=r[15]))
        conn.execute("COMMIT")
        return claims, over_budget
    except Exception:
        conn.execute("ROLLBACK")
        raise


def respread_overdue(conn: sqlite3.Connection, now_ms: int, rng: random.Random) -> int:
    """After a restart or a detected gap: spread employers overdue by more than one interval
    across the next interval instead of polling them all at once."""
    rows = conn.execute(
        "SELECT p.employer_id, e.poll_interval_s, s.default_interval_s, s.min_interval_s, p.next_poll_at_ms "
        "FROM employer_poll_state p JOIN employers e ON e.id = p.employer_id JOIN sources s ON s.id = e.source_id "
        "WHERE e.enabled = 1 AND e.status = 'active'").fetchall()
    moved = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for employer_id, override, default, minimum, next_at in rows:
            interval = effective_interval_s(override, default, minimum)
            if next_at < now_ms - interval * 1000:
                conn.execute("UPDATE employer_poll_state SET next_poll_at_ms = ? WHERE employer_id = ?",
                             (respread_at(now_ms, interval, rng), employer_id))
                moved += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return moved


@dataclass
class FetchRecord:
    """What the poller learned; finish() writes it with the reschedule."""
    outcome: str  # OK | EMPTY | NOT_MODIFIED | ERROR | SKIPPED | INTERRUPTED
    http_status: int | None = None
    retry_after_s: int | None = None
    requests_made: int = 0
    bytes_received: int = 0
    duration_ms: int = 0
    postings_seen: int | None = None
    new_count: int = 0
    changed_count: int = 0
    closed_count: int = 0
    complete: bool = False
    error: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    next_poll_override_ms: int | None = None  # e.g. robots cache expiry


def finish(conn: sqlite3.Connection, claim: Claim, rec: FetchRecord, now_ms: int, rng: random.Random) -> int:
    """Close the fetch row and reschedule. The caller holds the write transaction. Returns next_poll_at_ms."""
    conn.execute(
        "UPDATE source_fetches SET finished_at_ms = ?, outcome = ?, http_status = ?, retry_after_s = ?, requests_made = ?, "
        "bytes_received = ?, duration_ms = ?, postings_seen = ?, new_count = ?, changed_count = ?, closed_count = ?, "
        "complete = ?, error = ? WHERE id = ?",
        (now_ms, rec.outcome, rec.http_status, rec.retry_after_s, rec.requests_made, rec.bytes_received, rec.duration_ms,
         rec.postings_seen, rec.new_count, rec.changed_count, rec.closed_count, int(rec.complete), rec.error, claim.fetch_id))
    success = rec.outcome in ("OK", "EMPTY", "NOT_MODIFIED")
    failures = 0 if success else claim.consecutive_failures + 1
    status = rec.http_status
    host_limited = status in (429, 503) and rec.retry_after_s is not None

    if rec.next_poll_override_ms is not None:
        next_at = rec.next_poll_override_ms
    elif success or rec.outcome == "SKIPPED" or host_limited:
        # Host-level limits pause the whole source (below); the employer keeps its rhythm.
        next_at = next_after_success(now_ms, claim.interval_s, rng)
        if host_limited:
            failures = claim.consecutive_failures
    else:
        next_at = next_after_failure(now_ms, claim.interval_s, failures, rng)

    if host_limited:
        until = now_ms + max(rec.retry_after_s, HOST_BACKOFF_MIN_S) * 1000
        conn.execute("UPDATE sources SET backoff_until_ms = ?, backoff_reason = ? WHERE id = ?",
                     (until, f"HTTP {status} Retry-After {rec.retry_after_s}s", claim.source_id))
    if status in (401, 403):
        conn.execute("UPDATE employers SET status = 'blocked' WHERE id = ?", (claim.employer_id,))
    elif status == 404:
        recent = [r[0] for r in conn.execute(
            "SELECT http_status FROM source_fetches WHERE employer_id = ? AND finished_at_ms IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (claim.employer_id, DEAD_AFTER_404S))]
        if len(recent) == DEAD_AFTER_404S and all(s == 404 for s in recent):
            conn.execute("UPDATE employers SET status = 'dead' WHERE id = ?", (claim.employer_id,))

    conn.execute(
        "UPDATE employer_poll_state SET next_poll_at_ms = ?, lease_until_ms = NULL, last_outcome = ?, last_error = ?, "
        "consecutive_failures = ?, last_success_at_ms = CASE WHEN ? THEN ? ELSE last_success_at_ms END, "
        "etag = coalesce(?, etag), last_modified = coalesce(?, last_modified) WHERE employer_id = ?",
        (next_at, rec.outcome, rec.error, failures, int(success), now_ms, rec.etag, rec.last_modified, claim.employer_id))
    return next_at
