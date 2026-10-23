"""Side-by-side recommendations under two verdict rule versions (ARCHITECTURE 6.5: rules are tuned
by measurement, not intuition). Code only: zero model calls.

Usage (from agent/): uv run python ../scripts/compare_verdict_rules.py DATA_DIR [OLD NEW]   (default v1 v2)

The database is copied to a temporary file first (SQLite backup API); the recompute writes only
into that copy, which is deleted afterwards. Prints counts and per-job changes; posting text and
labels only, no profile content.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from jobforge_agent.db.connect import open_connection
from jobforge_agent.evaluation import store
from jobforge_agent.filtering import pipeline

sys.stdout.reconfigure(encoding="utf-8")
src = Path(sys.argv[1]) / "data" / "jobforge.db"
old, new = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else ("v1", "v2")

with tempfile.TemporaryDirectory() as tmp:
    copy = Path(tmp) / "compare.db"
    s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(copy)
    s.backup(d)
    s.close()
    d.close()  # `with sqlite3.connect()` would only commit; an open handle blocks the temp cleanup on Windows
    conn = open_connection(copy, "evaluation")
    profile = pipeline.active_profile(conn)
    now = int(time.time() * 1000)
    made = {v: store.recompute(conn, profile, now, rule_version=v) for v in (old, new)}
    rows = conn.execute(
        "SELECT j.id, j.company, j.title, rc.verdict_rule_version, rc.recommendation, rc.eligibility, rc.fit "
        "FROM (" + store._LATEST_DONE + ") le JOIN recommendations rc ON rc.evaluation_id = le.id "
        "JOIN jobs j ON j.id = le.job_id WHERE rc.search_profile_id = ? AND rc.verdict_rule_version IN (?, ?)",
        (*store.latest_done_params(conn, profile), profile.id, old, new)).fetchall()
    conn.close()

by = {}
for job_id, company, title, version, rec, elig, fit in rows:
    by.setdefault(job_id, {"company": company, "title": title})[version] = (rec, elig, fit)
print(f"database: {src} (compared in a temporary copy; newly computed: {made})")
for v in (old, new):
    print(f"  {v}: recommendation {dict(Counter(x[v][0] for x in by.values() if v in x))}; "
          f"eligibility {dict(Counter(x[v][1] for x in by.values() if v in x))}")
changed = [(k, x) for k, x in sorted(by.items()) if old in x and new in x and x[old] != x[new]]
print(f"\n  jobs whose result changed: {len(changed)} of {len(by)}")
for k, x in changed:
    print(f"  {k:>5} {x['company'][:14]:<14} {x['title'][:40]:<40} {old}: {'/'.join(x[old])}  ->  {new}: {'/'.join(x[new])}")
