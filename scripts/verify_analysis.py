"""Posting-analysis evidence from a JobForge database (SPEC section 6 analysis rows). Prints raw
numbers and rows, never a verdict. Opens the database read-only.

Usage: python scripts/verify_analysis.py [DB_PATH] [--examples N]

Posting text is public job-board text; nothing from the profile tables is read.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

args = sys.argv[1:]
examples = 12
if "--examples" in args:
    i = args.index("--examples")
    examples = int(args[i + 1])
    del args[i:i + 2]
db = Path(args[0]) if args else Path(os.environ["LOCALAPPDATA"]) / "dev.kevin.jobforge" / "data" / "jobforge.db"
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
sys.stdout.reconfigure(encoding="utf-8")


def q(sql, *a):
    return conn.execute(sql, a).fetchall()


def section(title):
    print(f"\n== {title}")


def flags(r):
    return json.loads(r["flags"])


print(f"database: {db}")
section("Model configs (enabled ones must have a test time)")
for r in q("SELECT id, name, model_id, enabled, tested_at_ms, params FROM model_configs ORDER BY id"):
    print(f"  {r['id']}: {r['name']} | {r['model_id']} | enabled={r['enabled']} tested_at_ms={r['tested_at_ms']} | {r['params']}")
print("  enabled without a test:", q("SELECT count(*) FROM model_configs WHERE enabled = 1 AND tested_at_ms IS NULL")[0][0])

section("Analyses by status and version")
for r in q("SELECT status, extraction_prompt_version, segmenter_version, interpreter_version, count(*) n FROM posting_analyses "
           "GROUP BY 1, 2, 3, 4 ORDER BY 1"):
    print(f"  {r['status']:<10} {r['extraction_prompt_version']}/{r['segmenter_version']}/{r['interpreter_version']}: {r['n']}")
print("  jobs with > 1 pending:", q("SELECT count(*) FROM (SELECT job_id FROM posting_analyses WHERE status = 'pending' "
                                      "GROUP BY job_id HAVING count(*) > 1)")[0][0])
print("  failures by kind:", dict(Counter((r[0] or "").split(":")[0] for r in q("SELECT last_error FROM posting_analyses WHERE status = 'failed'"))))

section("Per finished analysis")
print("   id company        title                                    head attempts reqs recall confirmed conflict unverif hard  prompt_tok")
for a in q("SELECT pa.id, pa.sections, pa.raw_response, j.company, j.title FROM posting_analyses pa JOIN jobs j ON j.id = pa.job_id "
           "WHERE pa.status = 'done' ORDER BY pa.id"):
    rs = q("SELECT origin, interpretation_status, final_class FROM requirements WHERE posting_analysis_id = ?", a["id"])
    c = Counter(r["interpretation_status"] for r in rs if r["origin"] == "model")
    att = json.loads(a["raw_response"])["attempts"]
    print(f"  {a['id']:>3} {a['company'][:14]:<14} {a['title'][:40]:<40} {json.loads(a['sections'])['headings']:>4} {len(att):>8} "
          f"{sum(r['origin'] == 'model' for r in rs):>4} {sum(r['origin'] != 'model' for r in rs):>6} {c['confirmed']:>9} {c['conflict']:>8} "
          f"{c['unverifiable']:>7} {sum(r['final_class'] == 'hard' for r in rs if r['origin'] == 'model'):>4}  "
          f"{(att[-1].get('usage') or {}).get('prompt_tokens')}")

model = q("SELECT * FROM requirements WHERE origin = 'model'")
section(f"Model requirements: {len(model)}")
print("  quotes found:", sum(r["quote_found"] for r in model), "/", len(model))
for col in ("interpretation_status", "final_class", "section_label", "eligibility_type"):
    print(f"  {col}:", dict(Counter(r[col] for r in model).most_common()))
print("  conflicts by field:", dict(Counter(f["field"] for r in model for fl in flags(r) if fl["flag"] == "conflict" for f in fl["fields"])))
print("  flags:", dict(Counter(fl["flag"] for r in model for fl in flags(r)).most_common()))
print("  model importance 'hard' -> final class:", dict(Counter(r["final_class"] for r in model if r["model_importance"] == "hard")))
print("  conflict rows missing a reading:", sum(1 for r in model if r["interpretation_status"] == "conflict" and
                                               (r["cue_polarity"] is None or r["model_polarity"] is None
                                                or not any(fl["flag"] == "conflict" for fl in flags(r)))))

JOIN = "FROM requirements r JOIN posting_analyses pa ON pa.id = r.posting_analysis_id JOIN jobs j ON j.id = pa.job_id"
section("Hard requirements (class decided by code), model reading beside it")
for r in q(f"SELECT r.*, j.company {JOIN} WHERE r.final_class = 'hard' ORDER BY r.id"):
    print(f"  [{r['company'][:12]}] {r['origin'][:6]} {r['interpretation_status']:<12} type={r['eligibility_type']:<18} "
          f"model={r['model_importance']}/{r['model_eligibility_type']} | {r['jd_quote'][:100]}")

section("Model type != lexicon type")
for r in q(f"SELECT r.*, j.company {JOIN} WHERE r.origin = 'model' AND r.eligibility_type IS NOT NULL "
           "AND r.model_eligibility_type <> r.eligibility_type ORDER BY r.id"):
    print(f"  [{r['company'][:12]}] {r['interpretation_status']:<9} class={r['final_class']:<10} model={r['model_eligibility_type']} "
          f"code={r['eligibility_type']} | {r['jd_quote'][:90]}")

section(f"Conflicts, first {examples}: model vs code reading")
for r in q(f"SELECT r.*, j.company {JOIN} WHERE r.interpretation_status = 'conflict' ORDER BY r.id LIMIT ?", examples):
    c = [fl for fl in flags(r) if fl["flag"] == "conflict"][0]["fields"]
    print(f"  [{r['company'][:12]}] [{r['section_label']}] -> {r['final_class']}: "
          + "; ".join(f"{x['field']} model={x['model']} code={x['cue']}" for x in c) + f"\n      | {r['jd_quote'][:120]}")

section("Unverifiable model quotes")
for r in q(f"SELECT r.*, j.company {JOIN} WHERE r.origin = 'model' AND r.interpretation_status = 'unverifiable' ORDER BY r.id"):
    print(f"  [{r['company'][:12]}] {[f['flag'] for f in flags(r)]} | {r['jd_quote'][:120]}")

section("Unextracted eligibility rows (recall scan, D15)")
for r in q(f"SELECT r.*, j.company {JOIN} WHERE r.origin = 'unextracted_eligibility' ORDER BY r.id"):
    print(f"  [{r['company'][:12]}] {r['eligibility_type']:<18} [{r['section_label']}] {r['cue_polarity']} | {r['sentence'][:110]}")

section("Change events")
print(" ", dict(q("SELECT type, count(*) FROM change_events WHERE type LIKE 'analysis_%' OR type LIKE 'model_config_%' GROUP BY type")))
