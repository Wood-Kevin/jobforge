"""Read models for the window (P1.8): job lists and a job's full detail. Read only.

The UI never sees the database (D4); these assemble what one screen needs in one call, from the same
queries the pipeline uses (the active search profile, the evaluation store's "current evaluation"
rule), so the window cannot show a different answer than the agent computed.
"""
from __future__ import annotations

import json
import sqlite3

from jobforge_agent import applications
from jobforge_agent.analysis import store as analysis_store
from jobforge_agent.evaluation import golden
from jobforge_agent.evaluation import store as evaluation_store
from jobforge_agent.filtering import pipeline as filter_pipeline
from jobforge_agent.filtering import rules as filter_rules

VIEWS = ("recommended", "rejected", "all")
MAX_LIMIT = 500


def _filter_join() -> str:
    return ("LEFT JOIN filter_results fr ON fr.raw_posting_id = j.current_raw_posting_id AND fr.search_profile_id = ? "
            "AND fr.ruleset_version = ?")


def list_jobs(conn: sqlite3.Connection, view: str, q: str | None, limit: int, offset: int) -> dict:
    if view not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}")
    limit = max(1, min(int(limit), MAX_LIMIT))
    profile = filter_pipeline.active_profile(conn)
    if profile is None:
        return {"view": view, "total": 0, "jobs": [], "search_profile": None}
    recs = {r["job_id"]: r for r in evaluation_store.recommendation_list(conn, None, limit=100_000)}
    where = ["j.closed_at_ms IS NULL"]
    params: list = [profile.id, filter_rules.RULESET_VERSION]
    if view == "rejected":
        where.append("fr.verdict = 'reject'")
    elif view == "recommended":
        ids = list(recs) or [-1]
        where.append(f"j.id IN ({','.join('?' * len(ids))})")
        params += ids
    if q:
        where.append("(j.title LIKE ? OR j.company LIKE ? OR j.location_text LIKE ?)")
        params += [f"%{q}%"] * 3
    base = f"FROM jobs j {_filter_join()} WHERE {' AND '.join(where)}"
    total = conn.execute(f"SELECT count(*) {base}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT j.id, j.title, j.company, j.location_text, j.work_mode, j.posted_at, j.first_seen_at_ms, fr.verdict, fr.reasons, "
        f"EXISTS (SELECT 1 FROM filter_overrides fo WHERE fo.job_id = j.id), "
        f"(SELECT status FROM applications a WHERE a.job_id = j.id) {base} "
        f"ORDER BY j.first_seen_at_ms DESC, j.id DESC LIMIT ? OFFSET ?", params + [limit, int(offset)]).fetchall()
    order = {"APPLY": 0, "STRETCH": 1, "REVIEW": 2, "SKIP": 3}
    jobs = []
    for r in rows:
        reasons = json.loads(r[8]) if r[8] else {}
        rec = recs.get(r[0])
        jobs.append({"id": r[0], "title": r[1], "company": r[2], "location": r[3], "work_mode": r[4], "posted_at": r[5],
                     "first_seen_at_ms": r[6], "filter_verdict": r[7], "filter_primary": reasons.get("primary"),
                     "override": bool(r[9]), "application_status": r[10],
                     "recommendation": None if rec is None else {
                         k: rec[k] for k in ("recommendation", "eligibility", "fit", "opportunity", "rank_key", "current",
                                             "newer_evaluation_pending")}})
    if view == "recommended":
        jobs.sort(key=lambda j: (order[j["recommendation"]["recommendation"]], -(j["recommendation"]["rank_key"] or 0), j["id"]))
    return {"view": view, "total": total, "jobs": jobs, "search_profile": {"id": profile.id, "version": profile.version}}


def job_detail(conn: sqlite3.Connection, job_id: int) -> dict | None:
    r = conn.execute(
        "SELECT j.id, j.title, j.company, j.location_text, j.work_mode, j.salary_min, j.salary_max, j.salary_period, j.posted_at, "
        "j.first_seen_at_ms, j.last_seen_at_ms, j.closed_at_ms, rp.id, rp.listing_url, rp.apply_url, "
        "coalesce(rp.description_raw, unpack_text(rp.description_raw_z)), "
        "coalesce(rp.description_text, unpack_text(rp.description_text_z)), e.display_name, s.kind FROM jobs j JOIN raw_postings rp ON rp.id = j.current_raw_posting_id "
        "JOIN employers e ON e.id = j.employer_id JOIN sources s ON s.id = e.source_id WHERE j.id = ?", (job_id,)).fetchone()
    if r is None:
        return None
    profile = filter_pipeline.active_profile(conn)
    fr = None
    if profile is not None:
        row = conn.execute("SELECT verdict, reasons FROM filter_results WHERE raw_posting_id = ? AND search_profile_id = ? "
                           "AND ruleset_version = ?", (r[12], profile.id, filter_rules.RULESET_VERSION)).fetchone()
        fr = {"verdict": row[0], "reasons": json.loads(row[1])} if row else None
    versions = conn.execute("SELECT count(*) FROM raw_postings WHERE source_job_id = (SELECT source_job_id FROM jobs WHERE id = ?)",
                            (job_id,)).fetchone()[0]
    analysis = conn.execute("SELECT id, status, last_error FROM posting_analyses WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                            (job_id,)).fetchone()
    rec = evaluation_store.job_recommendation(conn, job_id)
    if rec is not None:
        cited = sorted({i for row in rec["evidence"] if row["match"] for i in row["match"]["citations"]})
        snap = rec["evaluation"]["profile_snapshot_id"]
        # The statements the evaluation actually saw (the snapshot), not today's edited items.
        rec["cited_items"] = {str(x[0]): {"kind": x[1], "statement": x[2], "status": x[3]} for x in conn.execute(
            f"SELECT item_id, kind, statement, status FROM profile_snapshot_items WHERE snapshot_id = ? "
            f"AND item_id IN ({','.join('?' * len(cited)) or 'NULL'})", (snap, *cited))}
    return {
        "id": r[0], "title": r[1], "company": r[2], "location": r[3], "work_mode": r[4],
        "salary": {"min": r[5], "max": r[6], "period": r[7]} if r[7] else None, "posted_at": r[8],
        "first_seen_at_ms": r[9], "last_seen_at_ms": r[10], "closed_at_ms": r[11], "posting_versions": versions,
        "listing_url": r[13], "apply_url": r[14], "employer": r[17], "source": r[18],
        # Untrusted employer HTML: the window sanitizes it before rendering (ARCHITECTURE 8).
        "description_html": r[15], "description_text": r[16],
        "filter": fr, "override": conn.execute("SELECT 1 FROM filter_overrides WHERE job_id = ?", (job_id,)).fetchone() is not None,
        "analysis": None if analysis is None else {"id": analysis[0], "status": analysis[1], "last_error": analysis[2],
                                                   "latest_done_id": analysis_store.latest_for_job(conn, job_id)},
        "evaluation": rec,
        "application": applications.for_job(conn, job_id),
        "golden_label": golden.current_label(conn, job_id),
    }
