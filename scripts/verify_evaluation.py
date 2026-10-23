"""Evaluation and recommendation evidence from a JobForge database and its agent log (SPEC section 6
evaluation rows). Prints raw numbers and rows, never a verdict. Opens the database read-only.

Usage: python scripts/verify_evaluation.py [DATA_DIR]   (default: %LOCALAPPDATA%\\dev.kevin.jobforge)

Prints posting quotes (public job-board text), ids and counts. It never prints profile content:
no item statements, no fact values, no model bridges or gap explanations (they describe the
profile), so the output can be kept as evidence in the repo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ["LOCALAPPDATA"]) / "dev.kevin.jobforge"
db = root / "data" / "jobforge.db"
log = root / "logs" / "agent.jsonl"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
sys.stdout.reconfigure(encoding="utf-8")


def q(sql, *a):
    return conn.execute(sql, a).fetchall()


def section(title):
    print(f"\n== {title}")


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ms else None


print(f"database: {db}")
LATEST = ("SELECT e.* FROM evaluations e JOIN posting_analyses pa ON pa.id = e.posting_analysis_id JOIN jobs j ON j.id = e.job_id "
          "WHERE e.status = 'done' AND j.closed_at_ms IS NULL AND pa.raw_posting_id = j.current_raw_posting_id "
          "AND e.id = (SELECT max(e2.id) FROM evaluations e2 WHERE e2.job_id = e.job_id AND e2.status = 'done')")
profile = q("SELECT id, version FROM search_profiles ORDER BY version DESC LIMIT 1")
pid = profile[0]["id"] if profile else None
print("active search profile:", dict(profile[0]) if profile else None)

section("Evaluations by status and version")
for r in q("SELECT status, match_prompt_version, count(*) n FROM evaluations GROUP BY 1, 2 ORDER BY 1"):
    print(f"  {r['status']:<11} {r['match_prompt_version']}: {r['n']}")
print("  jobs with > 1 pending:", q("SELECT count(*) FROM (SELECT job_id FROM evaluations WHERE status = 'pending' GROUP BY job_id "
                                      "HAVING count(*) > 1)")[0][0])
print("  failures by kind:", dict(Counter((r[0] or "").split(":")[0] for r in q("SELECT last_error FROM evaluations WHERE status = 'failed'"))))
print("  profile snapshots:", q("SELECT count(*) FROM profile_snapshots")[0][0])
calls, secs, ptok, reused = [], [], [], 0
for r in q("SELECT raw_response FROM evaluations WHERE status = 'done'"):
    raw = json.loads(r[0])
    reused += "reused_from" in raw
    att = raw.get("attempts") or []
    calls.append(len(att))
    if att:
        secs.append(att[-1].get("seconds") or 0)
        ptok.append((att[-1].get("usage") or {}).get("prompt_tokens") or 0)
if secs:
    s, p = sorted(secs), sorted(ptok)
    print(f"  model calls per done evaluation: {dict(Counter(calls))} ({reused} reused a stored answer to the same request); "
          f"seconds median {s[len(s) // 2]} max {s[-1]}; "
          f"prompt tokens median {p[len(p) // 2]} max {p[-1]}")

section("Recommendation distribution (latest evaluation per open job, active profile)")
recs = q(f"SELECT rc.*, j.id AS job_id, j.company, j.title FROM ({LATEST}) le JOIN recommendations rc ON rc.evaluation_id = le.id "
         "JOIN jobs j ON j.id = le.job_id WHERE rc.search_profile_id = ? ORDER BY rc.id", pid)
print("  by recommendation:", dict(Counter(r["recommendation"] for r in recs)))
print("  by eligibility:", dict(Counter(r["eligibility"] for r in recs)))
print("  by fit:", dict(Counter(r["fit"] for r in recs)))
print("  by opportunity:", dict(Counter(r["opportunity"] for r in recs)))
print("  decisive rule:", dict(Counter(json.loads(r["decisive_reasons"])[0]["name"] for r in recs)))
print("  recommendations by rule version:", dict(q("SELECT verdict_rule_version, count(*) FROM recommendations GROUP BY 1")))
print("  recommendations by search profile:", dict(q("SELECT search_profile_id, count(*) FROM recommendations GROUP BY 1")))

section("Per job: recommendation and its three assessments")
print("   job company        title                                    rec      elig     fit          opp     rule")
for r in recs:
    print(f"  {r['job_id']:>4} {r['company'][:14]:<14} {r['title'][:40]:<40} {r['recommendation']:<8} "
          f"{r['eligibility']:<8} {r['fit']:<12} {r['opportunity']:<7} {json.loads(r['fit_counts'])['rule'][:60]}")

section("SKIP only from positive evidence: SKIPs whose decisive rule is not 1, 2 or 5")
print("  rows:", sum(1 for r in recs if r["recommendation"] == "SKIP" and json.loads(r["decisive_reasons"])[0]["rule"] not in (1, 2, 5)))
for r in recs:
    if r["recommendation"] == "SKIP":
        print(f"  [{r['company'][:14]}] {json.loads(r['decisive_reasons'])[0]['detail'][:150]}")

section("Invented quotes and conflicts excluded: blocks from a non-confirmed or recall requirement")
bad = [x for r in recs for x in json.loads(r["eligibility_reasons"]) if x["effect"] == "blocks"
       and (x["status"] != "confirmed" or x["origin"] != "model")]
print("  rows:", len(bad))
print("  eligibility reasons by effect and why:", dict(Counter((x["effect"], x["why"]) for r in recs for x in json.loads(r["eligibility_reasons"]))))

section("Missed blockers surface: unextracted_eligibility rows and the recommendation they produced")
for r in recs:
    for x in json.loads(r["eligibility_reasons"]):
        if x["origin"] == "unextracted_eligibility":
            print(f"  [{r['company'][:12]}] {r['recommendation']:<7} {x['type']:<18} effect={x['effect']:<8} outcome={x['outcome']:<10} "
                  f"basis={x['basis']:<13} | {x['quote'][:90]}")

section("REVIEW causes")
for r in recs:
    if r["recommendation"] != "REVIEW":
        continue
    unk = [x for x in json.loads(r["eligibility_reasons"]) if x["effect"] == "unknown"]
    print(f"  [{r['company'][:12]}] {r['title'][:36]:<36} fit={r['fit']:<12} eligibility unknown from: "
          + ("; ".join(f"{x['type']}/{x['why']}" for x in unk) or "-"))
# A conflict whose only disagreement is the eligibility type, where the code's type is met by a verified fact.
type_only = 0
for r in recs:
    if r["recommendation"] != "REVIEW" or r["fit"] in ("INSUFFICIENT",):
        continue
    unk = [x for x in json.loads(r["eligibility_reasons"]) if x["effect"] == "unknown"]
    if unk and all(x["why"].startswith("model and code read") and {f["field"] for f in x.get("readings", [])} == {"eligibility_type"}
                   and x["outcome"] == "met" and x["basis"] == "verified" for x in unk):
        type_only += 1
print("  REVIEWs caused only by type-only conflicts the code's type meets with a verified fact (fit judgeable):", type_only)

section("Evidence table: matches by requirement class")
rows = q(f"SELECT rq.final_class, rm.matched_by, rm.match_type, rm.basis, rm.gap_materiality, rm.validation_flags "
         f"FROM ({LATEST}) le JOIN requirement_matches rm ON rm.evaluation_id = le.id JOIN requirements rq ON rq.id = rm.requirement_id")
for cls in ("hard", "negotiable", "preferred"):
    sub = [r for r in rows if r["final_class"] == cls]
    print(f"  {cls:<10} n={len(sub):<4} type={dict(Counter(r['match_type'] for r in sub))} basis={dict(Counter(r['basis'] for r in sub))} "
          f"matched_by={dict(Counter(r['matched_by'] for r in sub))}")
print("  gap materiality:", dict(Counter(r["gap_materiality"] for r in rows if r["match_type"] == "gap")))
print("  validation flags:", dict(Counter(f["flag"] for r in rows for f in json.loads(r["validation_flags"])).most_common()))
print("  citations:", q("SELECT count(*) FROM match_citations")[0][0],
      "| citations outside their evaluation's snapshot:",
      q("SELECT count(*) FROM match_citations mc JOIN requirement_matches rm ON rm.id = mc.requirement_match_id "
        "JOIN evaluations e ON e.id = rm.evaluation_id WHERE mc.snapshot_id <> e.profile_snapshot_id")[0][0])
print("  inferred claims by state:", dict(q("SELECT state, count(*) FROM inferred_claims GROUP BY 1")))
print("  matches citing an inferred claim's id that is not a snapshot item:",
      q("SELECT count(*) FROM match_citations mc WHERE NOT EXISTS (SELECT 1 FROM profile_snapshot_items psi "
        "WHERE psi.snapshot_id = mc.snapshot_id AND psi.item_id = mc.item_id)")[0][0])

section("Last 5 recommendations: versions and provenance")
for r in q("SELECT rc.id, rc.recommendation, rc.eligibility, rc.fit, rc.opportunity, rc.verdict_rule_version, rc.search_profile_id, "
           "e.match_prompt_version, e.model_id_reported, e.profile_snapshot_id, pa.extraction_prompt_version, rc.decisive_reasons "
           "FROM recommendations rc JOIN evaluations e ON e.id = rc.evaluation_id JOIN posting_analyses pa ON pa.id = e.posting_analysis_id "
           "ORDER BY rc.id DESC LIMIT 5"):
    print(f"  rec {r['id']}: {r['recommendation']} {r['eligibility']}/{r['fit']}/{r['opportunity']} rules={r['verdict_rule_version']} "
          f"profile={r['search_profile_id']} match={r['match_prompt_version']} e1={r['extraction_prompt_version']} "
          f"model={r['model_id_reported']} snapshot={r['profile_snapshot_id']} | {json.loads(r['decisive_reasons'])[0]['detail'][:80]}")

section("Change events")
print(" ", dict(q("SELECT type, count(*) FROM change_events WHERE type LIKE 'evaluation%' OR type LIKE 'recommendation%' GROUP BY type")))

if log.exists():
    section(f"Agent log: {log}")
    keep = ("agent_start", "agent_stop", "inference_unavailable", "inference_available", "evaluations_queued",
            "recommendations_recomputed", "evaluation_failed", "evaluation_internal_error", "evaluation_sync_failed")
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    done = [x for x in lines if x.get("event") == "evaluation_done"]
    for x in lines:
        if x.get("event") in keep:
            print(f"  {x['timestamp'][:19]} {x['event']} " + json.dumps({k: v for k, v in x.items() if k not in ('event', 'timestamp', 'level')}))
    if done:
        print(f"  evaluation_done: {len(done)} lines, first {done[0]['timestamp'][:19]}, last {done[-1]['timestamp'][:19]}")
    # Discovery during inference outages: fetches and the worst employer age inside each down window.
    down = None
    for x in lines:
        if x.get("event") in ("agent_stop", "agent_start"):
            if down is not None:
                print(f"  inference down {down[:19]} -> agent {x['event'][6:]} {x['timestamp'][:19]}: window ends without "
                      "inference returning (not counted as an outage window)")
            down = None  # a window never spans an agent restart
        elif x.get("event") == "inference_unavailable" and down is None:
            down = x["timestamp"]
        elif x.get("event") == "inference_available" and down is not None:
            a = int(datetime.fromisoformat(down.replace("Z", "+00:00")).timestamp() * 1000)
            b = int(datetime.fromisoformat(x["timestamp"].replace("Z", "+00:00")).timestamp() * 1000)
            n = q("SELECT count(*), count(DISTINCT employer_id) FROM source_fetches WHERE started_at_ms BETWEEN ? AND ?", a, b)[0]
            gaps = q("SELECT employer_id, max(gap) g FROM (SELECT employer_id, started_at_ms - lag(started_at_ms) OVER "
                     "(PARTITION BY employer_id ORDER BY started_at_ms) gap FROM source_fetches WHERE started_at_ms BETWEEN ? AND ?) "
                     "GROUP BY employer_id ORDER BY g DESC LIMIT 1", a, b)
            print(f"  inference down {down[:19]} -> {x['timestamp'][:19]} ({(b - a) / 60000:.0f} min): {n[0]} fetches, {n[1]} employers; "
                  f"largest gap between one employer's fetches: {round(gaps[0]['g'] / 60000, 1) if gaps and gaps[0]['g'] else None} min")
            down = None
