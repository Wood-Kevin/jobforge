"""Discovery evidence from a JobForge database (SPEC section 6 discovery rows). Prints raw numbers,
never a verdict. Opens the database read-only.

Usage: python scripts/verify_discovery.py [DB_PATH]   (default: %LOCALAPPDATA%\\dev.kevin.jobforge\\data\\jobforge.db)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

db = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ["LOCALAPPDATA"]) / "dev.kevin.jobforge" / "data" / "jobforge.db"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
now = int(time.time() * 1000)
HOUR = 3_600_000


def q(sql, *args):
    return conn.execute(sql, args).fetchall()


def section(title):
    print(f"\n== {title}")


print(f"database: {db}")
paused = q("SELECT value FROM settings WHERE key = 'discovery_paused'")[0][0]
schema = q("PRAGMA user_version")[0][0]
print(f"now: {time.strftime('%Y-%m-%d %H:%M:%S')}  schema: {schema}  paused: {paused}")

section("Discovery is continuous: per source, enabled employers and max age of last success (s)")
for kind, host, n, max_age, never, interval in q(
        "SELECT s.kind, s.host, count(e.id), max(? - p.last_success_at_ms) / 1000, sum(p.last_success_at_ms IS NULL), s.default_interval_s "
        "FROM sources s LEFT JOIN employers e ON e.source_id = s.id AND e.enabled = 1 AND e.status = 'active' "
        "LEFT JOIN employer_poll_state p ON p.employer_id = e.id GROUP BY s.id", now):
    print(f"  {kind:<11} {host:<26} employers={n:<4} max_success_age_s={max_age}  never_succeeded={never}  interval_s={interval}")

section("Employers not active or failing (status, consecutive failures, last outcome, last error)")
rows = q("SELECT e.display_name, s.kind, e.status, p.consecutive_failures, p.last_outcome, p.last_error FROM employers e "
         "JOIN sources s ON s.id = e.source_id LEFT JOIN employer_poll_state p ON p.employer_id = e.id "
         "WHERE e.status <> 'active' OR p.consecutive_failures > 0 ORDER BY e.status, p.consecutive_failures DESC")
for r in rows:
    print(f"  {r[0]:<28} {r[1]:<10} {r[2]:<8} failures={r[3]} last={r[4]} {(r[5] or '')[:90]}")
if not rows:
    print("  (none)")

section("Outcomes per source, last 24 h")
for kind, outcome, n in q("SELECT s.kind, f.outcome, count(*) FROM source_fetches f JOIN employers e ON e.id = f.employer_id "
                          "JOIN sources s ON s.id = e.source_id WHERE f.started_at_ms > ? GROUP BY 1, 2 ORDER BY 1, 3 DESC", now - 24 * HOUR):
    print(f"  {kind:<11} {outcome or 'IN_FLIGHT':<15} {n}")

section("Request volume per host: last hour and last 24 h (requests, bytes on the wire) vs hourly budget")
for kind, budget, rh, bh, rd, bd in q(
        "SELECT s.kind, s.max_requests_per_hour, "
        "coalesce(sum(CASE WHEN f.started_at_ms > ? THEN f.requests_made END), 0), coalesce(sum(CASE WHEN f.started_at_ms > ? THEN f.bytes_received END), 0), "
        "coalesce(sum(f.requests_made), 0), coalesce(sum(f.bytes_received), 0) "
        "FROM sources s LEFT JOIN employers e ON e.source_id = s.id LEFT JOIN source_fetches f ON f.employer_id = e.id AND f.started_at_ms > ? "
        "GROUP BY s.id", now - HOUR, now - HOUR, now - 24 * HOUR):
    print(f"  {kind:<11} last_hour: {rh} req, {bh / 1e6:.2f} MB (budget {budget}/h)   last_24h: {rd} req, {bd / 1e6:.2f} MB")

section("Intervals as configured: median gap between successive fetch starts per employer (s), last 24 h")
gaps = []
for (emp,) in q("SELECT DISTINCT employer_id FROM source_fetches WHERE started_at_ms > ?", now - 24 * HOUR):
    starts = [r[0] for r in q("SELECT started_at_ms FROM source_fetches WHERE employer_id = ? AND started_at_ms > ? ORDER BY started_at_ms", emp, now - 24 * HOUR)]
    gaps += [(b - a) / 1000 for a, b in zip(starts, starts[1:])]
if gaps:
    gaps.sort()
    print(f"  gaps={len(gaps)}  min={gaps[0]:.0f}  median={gaps[len(gaps) // 2]:.0f}  max={gaps[-1]:.0f}")
else:
    print("  (fewer than two polls per employer so far)")

section("No overlapping polls: pairs of fetches for one employer whose [start, finish] overlap")
overlaps = q("SELECT a.employer_id, a.id, b.id FROM source_fetches a JOIN source_fetches b ON a.employer_id = b.employer_id AND a.id < b.id "
             "WHERE b.started_at_ms < coalesce(a.finished_at_ms, ?)", now)
print(f"  overlapping pairs: {len(overlaps)}")
print(f"  unfinished fetches now: {q('SELECT count(*) FROM source_fetches WHERE finished_at_ms IS NULL')[0][0]}")

section("Retry-After honored: rows with retry_after_s and the next request to that host")
ra = q("SELECT f.id, s.kind, f.started_at_ms, f.retry_after_s FROM source_fetches f JOIN employers e ON e.id = f.employer_id "
       "JOIN sources s ON s.id = e.source_id WHERE f.retry_after_s IS NOT NULL ORDER BY f.id DESC LIMIT 10")
for fid, kind, started, after in ra:
    nxt = q("SELECT min(f.started_at_ms) FROM source_fetches f JOIN employers e ON e.id = f.employer_id JOIN sources s ON s.id = e.source_id "
            "WHERE s.kind = ? AND f.started_at_ms > ?", kind, started)[0][0]
    print(f"  fetch {fid} {kind} retry_after={after}s next_request_after={(nxt - started) / 1000 if nxt else None}s")
if not ra:
    print("  (no Retry-After seen)")

section("Jitter, no bursts: fetch starts per minute per host, last 2 h (minutes with any fetch)")
per_min = Counter()
for kind, started in q("SELECT s.kind, f.started_at_ms FROM source_fetches f JOIN employers e ON e.id = f.employer_id "
                       "JOIN sources s ON s.id = e.source_id WHERE f.started_at_ms > ?", now - 2 * HOUR):
    per_min[(kind, started // 60_000)] += 1
for kind in sorted({k for k, _ in per_min}):
    counts = sorted(c for (k, _), c in per_min.items() if k == kind)
    print(f"  {kind:<11} minutes_active={len(counts)}  max_per_minute={counts[-1]}  distribution={dict(Counter(counts))}")

section("Change detection, last 24 h")
for t, n in q("SELECT type, count(*) FROM change_events WHERE created_at_ms > ? GROUP BY type", now - 24 * HOUR):
    print(f"  {t:<16} {n}")
jobs_open, jobs_all, raws = q("SELECT (SELECT count(*) FROM jobs WHERE closed_at_ms IS NULL), (SELECT count(*) FROM jobs), (SELECT count(*) FROM raw_postings)")[0]
fetch_ok = q("SELECT count(*) FROM source_fetches WHERE outcome IN ('OK', 'EMPTY')")[0][0]
not_mod = q("SELECT count(*) FROM source_fetches WHERE outcome = 'NOT_MODIFIED'")[0][0]
print(f"  jobs open/total: {jobs_open}/{jobs_all}   raw_postings: {raws}   200 polls: {fetch_ok}   304 polls: {not_mod}")

section("Recovery: INTERRUPTED fetches and whether each was followed by another fetch")
for fid, emp, started in q("SELECT id, employer_id, started_at_ms FROM source_fetches WHERE outcome = 'INTERRUPTED' ORDER BY id DESC LIMIT 10"):
    later = q("SELECT count(*) FROM source_fetches WHERE employer_id = ? AND started_at_ms > ?", emp, started)[0][0]
    print(f"  fetch {fid} employer {emp}: later fetches={later}")
