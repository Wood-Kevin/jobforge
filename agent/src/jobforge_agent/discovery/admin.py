"""Discovery status (dashboard, verify) and employer import (a user action)."""
from __future__ import annotations

import random
import sqlite3

from jobforge_agent.discovery.scheduler import HOUR_MS, effective_interval_s, respread_at, requests_last_hour

SUPPORTED = ("greenhouse", "lever", "ashby")


def status(conn: sqlite3.Connection, now_ms: int) -> dict:
    sources = []
    for s in conn.execute("SELECT id, kind, host, enabled, backoff_until_ms, backoff_reason, max_requests_per_hour FROM sources ORDER BY id"):
        sid = s[0]
        bytes_h, polls_h, not_mod_h = conn.execute(
            "SELECT coalesce(sum(f.bytes_received), 0), count(*), coalesce(sum(f.outcome = 'NOT_MODIFIED'), 0) "
            "FROM source_fetches f JOIN employers e ON e.id = f.employer_id WHERE e.source_id = ? AND f.started_at_ms > ?",
            (sid, now_ms - HOUR_MS)).fetchone()
        sources.append({
            "kind": s[1], "host": s[2], "enabled": bool(s[3]),
            "backoff_until_ms": s[4] if s[4] and s[4] > now_ms else None, "backoff_reason": s[5] if s[4] and s[4] > now_ms else None,
            "requests_last_hour": requests_last_hour(conn, sid, now_ms), "max_requests_per_hour": s[6],
            "bytes_last_hour": bytes_h, "polls_last_hour": polls_h, "not_modified_last_hour": not_mod_h,
        })
    employers = []
    for e in conn.execute(
            "SELECT e.id, s.kind, e.slug, e.display_name, e.enabled, e.status, e.poll_interval_s, s.default_interval_s, s.min_interval_s, "
            "p.next_poll_at_ms, p.last_success_at_ms, p.last_attempt_at_ms, p.consecutive_failures, p.last_outcome, p.last_error "
            "FROM employers e JOIN sources s ON s.id = e.source_id LEFT JOIN employer_poll_state p ON p.employer_id = e.id ORDER BY e.id"):
        employers.append({
            "id": e[0], "source": e[1], "slug": e[2], "name": e[3], "enabled": bool(e[4]), "status": e[5],
            "interval_s": effective_interval_s(e[6], e[7], e[8]),
            "next_poll_in_s": round((e[9] - now_ms) / 1000) if e[9] else None,
            "last_success_age_s": round((now_ms - e[10]) / 1000) if e[10] else None,
            "last_attempt_age_s": round((now_ms - e[11]) / 1000) if e[11] else None,
            "consecutive_failures": e[12], "last_outcome": e[13], "last_error": e[14],
        })
    jobs_open, jobs_total = conn.execute("SELECT coalesce(sum(closed_at_ms IS NULL), 0), count(*) FROM jobs").fetchone()
    in_flight = conn.execute("SELECT count(*) FROM source_fetches WHERE finished_at_ms IS NULL").fetchone()[0]
    paused = conn.execute("SELECT value FROM settings WHERE key = 'discovery_paused'").fetchone()
    return {"paused": bool(paused and paused[0] == "1"), "sources": sources, "employers": employers,
            "jobs_open": jobs_open, "jobs_total": jobs_total, "polls_in_flight": in_flight}


def import_employers(conn: sqlite3.Connection, entries: list[dict], now_ms: int, rng: random.Random) -> dict:
    """Add employers from a watchlist. Existing (source, slug) pairs are left alone; new ones get
    a first poll spread over one interval so an import never causes a burst."""
    added, existing, unsupported = 0, 0, []
    sources = {r[1]: (r[0], r[2], r[3]) for r in conn.execute("SELECT id, kind, default_interval_s, min_interval_s FROM sources")}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for entry in entries:
            kind = str(entry.get("source") or entry.get("ats") or "").lower()
            slug = str(entry.get("slug") or "").strip()
            name = str(entry.get("name") or slug).strip()
            if kind not in sources or not slug:
                unsupported.append(f"{kind or '?'}:{slug or '?'}")
                continue
            source_id, default, minimum = sources[kind]
            row = conn.execute("SELECT id FROM employers WHERE source_id = ? AND slug = ?", (source_id, slug)).fetchone()
            if row:
                existing += 1
                continue
            emp_id = conn.execute("INSERT INTO employers (source_id, slug, display_name) VALUES (?, ?, ?) RETURNING id",
                                  (source_id, slug, name)).fetchone()[0]
            conn.execute("INSERT INTO employer_poll_state (employer_id, next_poll_at_ms) VALUES (?, ?)",
                         (emp_id, respread_at(now_ms, effective_interval_s(None, default, minimum), rng)))
            added += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"added": added, "existing": existing, "skipped_unsupported": unsupported}
