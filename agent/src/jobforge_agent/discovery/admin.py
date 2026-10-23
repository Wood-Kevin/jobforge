"""Discovery status (dashboard, verify) and employer import (a user action).

Status lists the companies the person added one by one; the built-in list (thousands of rows) is
summarized as counts, so the window's 15 s refresh stays small (P1.10)."""
from __future__ import annotations

import random
import sqlite3

from jobforge_agent.discovery import directory
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
            "FROM employers e JOIN sources s ON s.id = e.source_id LEFT JOIN employer_poll_state p ON p.employer_id = e.id "
            "WHERE e.origin = 'user' ORDER BY e.id"):
        employers.append({
            "id": e[0], "source": e[1], "slug": e[2], "name": e[3], "enabled": bool(e[4]), "status": e[5],
            "interval_s": effective_interval_s(e[6], e[7], e[8]),
            "next_poll_in_s": round((e[9] - now_ms) / 1000) if e[9] else None,
            "last_success_age_s": round((now_ms - e[10]) / 1000) if e[10] else None,
            "last_attempt_age_s": round((now_ms - e[11]) / 1000) if e[11] else None,
            "consecutive_failures": e[12], "last_outcome": e[13], "last_error": e[14],
        })
    d = conn.execute(
        "SELECT count(*), coalesce(sum(e.enabled), 0), coalesce(sum(e.enabled AND e.status = 'dead'), 0), "
        "coalesce(sum(e.enabled AND e.status = 'blocked'), 0), coalesce(sum(e.enabled AND p.consecutive_failures >= 3), 0), "
        "coalesce(sum(e.enabled AND p.last_success_at_ms IS NOT NULL), 0), max(CASE WHEN e.enabled THEN p.last_success_at_ms END) "
        "FROM employers e LEFT JOIN employer_poll_state p ON p.employer_id = e.id WHERE e.origin = 'directory'").fetchone()
    built_in = {"watching": directory.watching(conn), "total": d[0], "enabled": d[1], "dead": d[2], "blocked": d[3],
                "failing": d[4], "checked_once": d[5], "last_success_age_s": round((now_ms - d[6]) / 1000) if d[6] else None}
    jobs_open, jobs_total = conn.execute("SELECT coalesce(sum(closed_at_ms IS NULL), 0), count(*) FROM jobs").fetchone()
    in_flight = conn.execute("SELECT count(*) FROM source_fetches WHERE finished_at_ms IS NULL").fetchone()[0]
    paused = conn.execute("SELECT value FROM settings WHERE key = 'discovery_paused'").fetchone()
    return {"paused": bool(paused and paused[0] == "1"), "sources": sources, "employers": employers, "built_in": built_in,
            "jobs_open": jobs_open, "jobs_total": jobs_total, "polls_in_flight": in_flight}


def import_employers(conn: sqlite3.Connection, entries: list[dict], now_ms: int, rng: random.Random) -> dict:
    """Add employers the person named (links, names, a watchlist). New ones get a first poll spread
    over one interval so an import never causes a burst. An existing one is now the person's
    (origin 'user', so the built-in-list switch no longer touches it) and is watched again if it
    was not: naming a company is asking to watch it. Returns the ids it watched again."""
    added, existing, unsupported, rewatched = 0, 0, [], []
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
            row = conn.execute("SELECT id, enabled, origin FROM employers WHERE source_id = ? AND slug = ?", (source_id, slug)).fetchone()
            if row:
                if not row[1]:
                    rewatched.append(row[0])
                    conn.execute("INSERT INTO change_events (type, employer_id, created_at_ms) VALUES ('employer_enabled', ?, ?)",
                                 (row[0], now_ms))
                else:
                    existing += 1
                if not row[1] or row[2] != "user":
                    conn.execute("UPDATE employers SET enabled = 1, origin = 'user' WHERE id = ?", (row[0],))
                if row[2] != "user":
                    # A built-in list row the person named: it now polls like theirs (source default,
                    # not the list's 24 h), and its next poll comes no later than a new company's would.
                    conn.execute("UPDATE employers SET poll_interval_s = NULL WHERE id = ?", (row[0],))
                    conn.execute("UPDATE employer_poll_state SET next_poll_at_ms = min(next_poll_at_ms, ?) WHERE employer_id = ?",
                                 (respread_at(now_ms, effective_interval_s(None, default, minimum), rng), row[0]))
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
    return {"added": added, "existing": existing, "watched_again": len(rewatched), "skipped_unsupported": unsupported,
            "watched_again_ids": rewatched}


def set_built_in_watch(conn: sqlite3.Connection, watch: bool, now_ms: int) -> int:
    """The "watch the built-in list" switch: every directory row follows it (rows the person
    added are origin 'user' and untouched). Returns how many rows changed."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (directory.WATCH_SETTING, "1" if watch else "0"))
        n = conn.execute("UPDATE employers SET enabled = ? WHERE origin = 'directory' AND enabled <> ?",
                         (int(watch), int(watch))).rowcount
        conn.execute("INSERT INTO change_events (type, ref, created_at_ms) VALUES (?, ?, ?)",
                     ("employer_enabled" if watch else "employer_disabled", f'{{"built_in": {n}}}', now_ms))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return n
