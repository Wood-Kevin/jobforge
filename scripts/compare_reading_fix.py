"""Before/after for the reading fix (Evaluation calibration 1, cases E1-E2). Code only: zero model calls.

The same script runs twice on the same copy of the database, once with main's code (before) and once with
the branch's (after); only the imported code differs. For every open job whose current posting has a done
analysis and evaluation and whose text is kept, it re-runs E2 on the stored E1 answer and the posting text,
checks eligibility lines against the evaluated snapshot's facts, reuses the stored E3 matches for lines
the model matched (by quote), and assesses under the code's active verdict rules. Jobs whose text was
dropped cannot be re-read and are counted, not guessed. A line that becomes model-matched only in one run
has no stored match there and counts as unknown (reported).

Usage (from agent/):
  run:  python ../scripts/compare_reading_fix.py run  DB_COPY OUT.json     (PYTHONPATH picks the code)
  diff: python ../scripts/compare_reading_fix.py diff BEFORE.json AFTER.json
Prints ids, counts and posting text only; no profile statements.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from dataclasses import replace


def run(db_path: str, out_path: str) -> None:
    import jobforge_agent
    from jobforge_agent.analysis import extract
    from jobforge_agent.analysis.interpret import interpret
    from jobforge_agent.analysis.segment import segment
    from jobforge_agent.analysis.versions import INTERPRETER_VERSION
    from jobforge_agent.db import textpack
    from jobforge_agent.db.connect import open_connection
    from jobforge_agent.evaluation import assess, facts, snapshot, store, validate
    from jobforge_agent.evaluation.versions import VERDICT_RULE_VERSION
    from jobforge_agent.filtering import pipeline

    from pathlib import Path
    conn = open_connection(Path(db_path), "evaluation")
    profile = pipeline.active_profile(conn)
    profile_rules = json.loads(conn.execute("SELECT rules FROM search_profiles WHERE id = ?", (profile.id,)).fetchone()[0])
    rules = assess.load_rules(VERDICT_RULE_VERSION)
    now = int(time.time() * 1000)
    rows = conn.execute(
        "SELECT e.id, e.job_id, e.posting_analysis_id, e.profile_snapshot_id, pa.raw_response, pa.raw_posting_id, j.company, j.title "
        "FROM evaluations e JOIN posting_analyses pa ON pa.id = e.posting_analysis_id JOIN jobs j ON j.id = e.job_id "
        "WHERE e.status = 'done' AND j.closed_at_ms IS NULL AND pa.raw_posting_id = j.current_raw_posting_id "
        "AND e.id = (SELECT max(e2.id) FROM evaluations e2 WHERE e2.job_id = e.job_id AND e2.status = 'done' "
        "            AND e2.posting_analysis_id IN (SELECT id FROM posting_analyses WHERE raw_posting_id = j.current_raw_posting_id)) "
        "ORDER BY e.job_id").fetchall()
    out = {"code": jobforge_agent.__file__, "interpreter": INTERPRETER_VERSION, "rules": VERDICT_RULE_VERSION,
           "text_dropped": [], "jobs": {}}
    for eid, job, aid, sid, raw, raw_id, company, title in rows:
        text = conn.execute(f"SELECT {textpack.TEXT} FROM raw_postings rp {textpack.JOIN} WHERE rp.id = ? AND {textpack.KEPT}",
                            (raw_id,)).fetchone()
        if not text or not text[0]:
            out["text_dropped"].append(job)
            continue
        items = extract.parse(json.loads(raw)["attempts"][-1]["content"])
        stored = {(r.origin, r.jd_quote): r for r in store.load_reqs(conn, aid)}
        stored_matches = store.load_matches(conn, eid)
        reqs, fresh = [], -1
        for r in interpret(segment(text[0]), items):
            old = stored.get((r.origin, r.jd_quote))
            rid = old.id if old else fresh
            fresh -= old is None
            reqs.append(facts.Req(rid, r.text, r.jd_quote, r.sentence, r.eligibility_type, r.final_class, r.interpretation_status,
                                  r.origin, r.cue_polarity, r.min_years, r.section_label, tuple(r.flags)))
        snap = snapshot.load(conn, sid)
        by_code, by_model = store.split(reqs)
        matches = {r.id: validate.fact_match(r, facts.check(r, snap.facts(), snap.completeness, reqs), rules) for r in by_code}
        no_stored_match = 0
        for r in by_model:
            m = stored_matches.get(r.id)
            if m is not None and m.matched_by == "model":
                matches[r.id] = m
            else:
                no_stored_match += 1

        def alt_check(r, other_type):
            return facts.check(replace(r, eligibility_type=other_type), snap.facts(), snap.completeness, reqs)

        a = assess.assess(reqs, matches, store.job_facts(conn, job, profile), profile_rules, rules, now, alt_check)
        out["jobs"][str(job)] = {
            "company": company, "title": title, "evaluation_id": eid,
            "recommendation": a.recommendation, "eligibility": a.eligibility, "fit": a.fit,
            "counts": {k: a.fit_counts.get(k) for k in ("direct", "transferable", "gap_material", "gap_minor", "unknown", "negotiable")},
            "questions": sorted({q["question"] for d in a.decisive_reasons for q in d.get("questions", [])}),
            "lines": {r.jd_quote: {"type": r.eligibility_type, "class": r.final_class, "status": r.interpretation_status,
                                   "match": matches[r.id].match_type if r.id in matches else None,
                                   "by": "fact" if r in by_code else "model" if r in by_model else None}
                      for r in reqs},
            "model_lines_without_stored_match": no_stored_match,
        }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"{out['code']} | interpreter {out['interpreter']} | rules {out['rules']} | jobs {len(out['jobs'])} | "
          f"text dropped (not re-read) {len(out['text_dropped'])}")


def diff(before_path: str, after_path: str) -> None:
    b, a = (json.load(open(p, encoding="utf-8")) for p in (before_path, after_path))
    print(f"before: interpreter {b['interpreter']}, rules {b['rules']}  |  after: interpreter {a['interpreter']}, rules {a['rules']}")
    print(f"jobs re-read: {len(a['jobs'])}; not re-read (posting text dropped by the filter): {len(a['text_dropped'])}")
    jobs = sorted(set(b["jobs"]) & set(a["jobs"]), key=int)
    moves = Counter((b["jobs"][j]["recommendation"], a["jobs"][j]["recommendation"]) for j in jobs)
    print("\nrecommendation before -> after:")
    for (x, y), n in sorted(moves.items()):
        print(f"  {x:>7} -> {y:<7} {n}")
    line_moves, retyped = Counter(), []
    for j in jobs:
        for quote, la in a["jobs"][j]["lines"].items():
            lb = b["jobs"][j]["lines"].get(quote)
            if lb is None:
                line_moves[("(new line)", f"{la['by']}:{la['match']}")] += 1
                continue
            if (lb["type"], lb["match"], lb["by"]) != (la["type"], la["match"], la["by"]):
                line_moves[(f"{lb['by']}:{lb['match']}", f"{la['by']}:{la['match']}")] += 1
                retyped.append((j, quote, lb, la))
        for quote in set(b["jobs"][j]["lines"]) - set(a["jobs"][j]["lines"]):
            line_moves[(f"{b['jobs'][j]['lines'][quote]['by']}:{b['jobs'][j]['lines'][quote]['match']}", "(line gone)")] += 1
    print("\nlines whose type or result changed (matched-by:result):")
    for (x, y), n in sorted(line_moves.items(), key=lambda kv: -kv[1]):
        print(f"  {x:>22} -> {y:<22} {n}")
    print("\nper job (only jobs with a change):")
    for j in jobs:
        jb, ja = b["jobs"][j], a["jobs"][j]
        changed = [r for r in retyped if r[0] == j]
        if jb["recommendation"] == ja["recommendation"] and jb["eligibility"] == ja["eligibility"] and not changed:
            continue
        print(f"\n  job {j} {ja['company']} | {ja['title']}")
        print(f"    {jb['recommendation']}/{jb['eligibility']}/{jb['fit']}  ->  {ja['recommendation']}/{ja['eligibility']}/{ja['fit']}"
              f"   unknown {jb['counts']['unknown']} -> {ja['counts']['unknown']}")
        for _, quote, lb, la in changed:
            print(f"    [{lb['type'] or '-'} {lb['by']}:{lb['match']} -> {la['type'] or '-'} {la['by']}:{la['match']}] {quote[:100]}")
        new_q = sorted(set(ja["questions"]) - set(jb["questions"]))
        for q in new_q[:4]:
            print(f"    new question: {q[:150]}")
    asked = Counter(q for j in jobs for q in set(a["jobs"][j]["questions"]) - set(b["jobs"][j]["questions"]))
    print(f"\nquestions added across jobs: {sum(asked.values())}; most common:")
    for q, n in asked.most_common(6):
        print(f"  {n} x {q[:150]}")
    stale = sum(a["jobs"][j]["model_lines_without_stored_match"] for j in jobs)
    print(f"\nmodel-matched lines with no stored E3 answer in the after run (counted unknown; a real rollout re-asks the model): {stale}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    {"run": run, "diff": diff}[sys.argv[1]](*sys.argv[2:4])
