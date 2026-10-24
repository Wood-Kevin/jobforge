"""Filter evidence from a JobForge database (SPEC section 6 filter rows). Prints raw numbers, never a
verdict. Opens the database read-only.

Usage: python scripts/verify_filter.py [DB_PATH] [--scanner SCANNER_DIR]

With --scanner, also runs job-scanner's own pass2 (edd1528) on the same stored postings and prints
where it and JobForge disagree: a differential check of the port. That needs the scanner's
packages importable, so run it with the scanner's interpreter (job-scanner\\.venv\\Scripts\\python.exe).
The scanner's config loader reads its .env into this process's environment; nothing from it is printed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import zlib
from collections import Counter
from pathlib import Path

args = sys.argv[1:]
scanner_dir = None
if "--scanner" in args:
    i = args.index("--scanner")
    scanner_dir = Path(args[i + 1])
    del args[i:i + 2]
db = Path(args[0]) if args else Path(os.environ["LOCALAPPDATA"]) / "dev.kevin.jobforge" / "data" / "jobforge.db"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
# Posting text written since migration 0010 is compressed in posting_texts (agent db/textpack.py).
conn.create_function("unpack_text", 1, lambda b: None if b is None else zlib.decompress(b).decode("utf-8"), deterministic=True)


def q(sql, *a):
    return conn.execute(sql, a).fetchall()


def section(title):
    print(f"\n== {title}")


print(f"database: {db}")
profile = q("SELECT id, version, created_at_ms FROM search_profiles ORDER BY version DESC LIMIT 1")
print(f"schema: {q('PRAGMA user_version')[0][0]}  profiles: {q('SELECT count(*) FROM search_profiles')[0][0]}  "
      f"active profile: {profile[0][1] if profile else None}")
if not profile:
    sys.exit(0)
pid = profile[0][0]
rulesets = [r[0] for r in q("SELECT DISTINCT ruleset_version FROM filter_results WHERE search_profile_id = ?", pid)]
print(f"rulesets with results for the active profile: {rulesets}")

section("Coverage: open jobs with a verdict on their current posting for the active profile")
open_jobs = q("SELECT count(*) FROM jobs WHERE closed_at_ms IS NULL")[0][0]
covered = q("SELECT count(DISTINCT j.id) FROM jobs j JOIN filter_results fr ON fr.raw_posting_id = j.current_raw_posting_id "
            "AND fr.search_profile_id = ? WHERE j.closed_at_ms IS NULL", pid)[0][0]
print(f"  open jobs: {open_jobs}  with verdict: {covered}  without: {open_jobs - covered}")

section("History kept: filter_results rows per profile version (old rows are never rewritten)")
for version, n, passed in q("SELECT sp.version, count(fr.id), sum(fr.verdict = 'pass') FROM search_profiles sp "
                            "LEFT JOIN filter_results fr ON fr.search_profile_id = sp.id GROUP BY sp.id ORDER BY sp.version"):
    print(f"  profile v{version}: rows={n} pass={passed}")

rows = q("SELECT j.id, j.title, j.location_text, j.work_mode, fr.verdict, fr.reasons FROM jobs j "
         "JOIN filter_results fr ON fr.raw_posting_id = j.current_raw_posting_id AND fr.search_profile_id = ? "
         "WHERE j.closed_at_ms IS NULL ORDER BY j.id", pid)
section("Verdicts on open jobs (active profile)")
print("  " + "  ".join(f"{k}={v}" for k, v in sorted(Counter(r[4] for r in rows).items())))

section("Rejections by primary reason category, then by rule")
cats, rules_, details = Counter(), Counter(), Counter()
also = Counter()
clearance = 0
for r in rows:
    reasons = json.loads(r[5])
    clearance += bool(reasons.get("clearance_mention"))
    failed = reasons.get("failed") or []
    if failed:
        cats[failed[0]["category"]] += 1
        rules_[failed[0]["rule"]] += 1
        for f in failed[1:]:
            also[f["rule"]] += 1
for k, v in cats.most_common():
    print(f"  category {k:<14} {v}")
for k, v in rules_.most_common():
    print(f"  rule     {k:<24} {v}")
section("Secondary failures (rules failed besides the primary; recorded, not deciding)")
for k, v in also.most_common():
    print(f"  {k:<24} {v}")
print(f"\n  open jobs whose text mentions a clearance (diagnostic only, never a rejection): {clearance}")

section("Analysis queue")
for status, n in q("SELECT status, count(*) FROM posting_analyses GROUP BY status ORDER BY status"):
    print(f"  {status:<11} {n}")
print(f"  overrides: {q('SELECT count(*) FROM filter_overrides')[0][0]}")
pending_not_pass = q("SELECT count(*) FROM posting_analyses pa WHERE pa.status = 'pending' AND NOT EXISTS ("
                     "SELECT 1 FROM filter_results fr WHERE fr.raw_posting_id = pa.raw_posting_id AND fr.verdict = 'pass') "
                     "AND NOT EXISTS (SELECT 1 FROM filter_overrides o WHERE o.job_id = pa.job_id)")[0][0]
print(f"  pending analyses with neither a pass nor an override: {pending_not_pass}")

section("Refilter runs (change_events)")
for created, ref in q("SELECT created_at_ms, ref FROM change_events WHERE type IN ('refilter_done', 'refilter_failed') ORDER BY id"):
    print(f"  {created}  {ref}")

if scanner_dir is None:
    sys.exit(0)

section(f"Differential: job-scanner pass2 on the same stored postings ({scanner_dir})")
sys.path.insert(0, str(scanner_dir))
from datetime import date  # noqa: E402

from ats_scanner.config import load_config  # noqa: E402
from ats_scanner.filters import pass2  # noqa: E402
from ats_scanner.models import RawJob, Salary  # noqa: E402

cfg = load_config(scanner_dir / "config.yaml")
full = {r[0]: r for r in q(
    "SELECT j.id, j.title, j.location_text, coalesce(rp.description_text, unpack_text(pt.description_text_z)), j.work_mode, j.salary_min, j.salary_max, j.salary_period, "
    "j.posted_at FROM jobs j JOIN raw_postings rp ON rp.id = j.current_raw_posting_id "
    # Since 0010 rejected postings keep no text: compare only postings whose text is kept.
    "LEFT JOIN posting_texts pt ON pt.raw_posting_id = rp.id WHERE j.closed_at_ms IS NULL "
    "AND (rp.description_text IS NOT NULL OR pt.raw_posting_id IS NOT NULL)")}
RENAME = {"REMOTE_EXCLUDES_FL": "REMOTE_EXCLUDES_HOME"}
agree_verdict = agree_rule = 0
disagree = []
today = date.today()
for r in rows:
    j = full[r[0]]
    sal = Salary(j[5], j[6], j[7], "structured") if j[7] and (j[5] is not None or j[6] is not None) else None
    raw = RawJob("x", "x", str(j[0]), "x", j[1], j[2] or "", "", "", description=j[3] or "",
                 posted_at=j[8], workplace=j[4].upper() if j[4] else None, salary=sal)
    v = pass2(raw, cfg, today=today)
    theirs = "pass" if v.keep else RENAME.get(v.reason.split(":")[0], v.reason.split(":")[0])
    reasons = json.loads(r[5])
    ours = "pass" if r[4] == "pass" else reasons["failed"][0]["rule"]
    agree_verdict += (theirs == "pass") == (ours == "pass")
    agree_rule += theirs == ours
    if theirs != ours:
        disagree.append((r[0], r[1], r[2], ours, theirs))
print(f"  postings compared: {len(rows)}")
print(f"  same verdict: {agree_verdict}  different verdict: {len(rows) - agree_verdict}")
print(f"  same deciding rule: {agree_rule}  different: {len(rows) - agree_rule}")
for d in disagree[:40]:
    print(f"  job {d[0]}: {d[1][:60]!r} @ {(d[2] or '')[:40]!r}: jobforge={d[3]} scanner={d[4]}")
