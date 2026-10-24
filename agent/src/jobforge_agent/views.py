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
from jobforge_agent.db import textpack
from jobforge_agent.evaluation import golden
from jobforge_agent.evaluation import store as evaluation_store
from jobforge_agent.evaluation.versions import VERDICT_RULE_VERSION
from jobforge_agent.filtering import pipeline as filter_pipeline
from jobforge_agent.filtering import rules as filter_rules

VIEWS = ("recommended", "rejected", "skipped", "all")
MAX_LIMIT = 500


# Exact copies: one employer posting the same job under several ids (Bridgeway's "System Administrator"
# twice, 2026-10-24). Lists show one row per copy group; the detail links every open copy. The text is
# compared where it is kept, so two different jobs that share a title and location (two teams'
# "Software Engineer, Remote") never merge; rejected postings keep no text (0010)
# and collapse on title and location. Rejected: merging at ingestion, which would lose a real posting's
# own link and closure.
_COPY_KEY = (f"j.employer_id, lower(trim(j.title)), lower(trim(coalesce(j.location_text, ''))), "
             f"CASE WHEN {textpack.KEPT} THEN {textpack.TEXT} ELSE '' END")
_POSTING_JOIN = f"JOIN raw_postings rp ON rp.id = j.current_raw_posting_id {textpack.JOIN}"


def skipped(conn: sqlite3.Connection) -> tuple[set[int], set[int]]:
    """(labeled, hidden): the jobs the user called SKIP (golden label), and those plus their open exact
    copies. A skip hides the whole copy group, or the other copy would take the skipped one's row.
    Rejected: an application status for "not interested", which needs a table rebuild for its CHECK and
    mixes a non-application into the application history; and it would not count as a label."""
    labeled = golden.skipped_job_ids(conn)
    if not labeled:
        return labeled, set()
    marks = ",".join("?" * len(labeled))
    copies = {r[0] for r in conn.execute(
        f"SELECT j.id FROM jobs j {_POSTING_JOIN} WHERE j.employer_id IN (SELECT employer_id FROM jobs WHERE id IN ({marks})) "
        f"AND ({_COPY_KEY}) IN (SELECT {_COPY_KEY} FROM jobs j {_POSTING_JOIN} WHERE j.id IN ({marks}))", (*labeled, *labeled))}
    return labeled, labeled | copies


def _ids(ids) -> str:
    return ",".join(str(int(i)) for i in ids) or "-1"


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
    labeled, hidden = skipped(conn)
    where = ["j.closed_at_ms IS NULL"]
    params: list = []
    fr_join = ""  # the grouping query needs the verdict only to pick rejected jobs
    if view == "rejected":
        fr_join = _filter_join()
        params += [profile.id, filter_rules.RULESET_VERSION]
        where.append("fr.verdict = 'reject'")
    elif view == "recommended":
        ids = [i for i in recs if i not in hidden] or [-1]
        where.append(f"j.id IN ({','.join('?' * len(ids))})")
        params += ids
    elif view == "skipped":
        where.append(f"j.id IN ({_ids(hidden)})")
    if q:
        where.append("(j.title LIKE ? OR j.company LIKE ? OR j.location_text LIKE ?)")
        params += [f"%{q}%"] * 3
    # One row per copy group: the copy with an application, else the one you skipped (so Undo works on
    # its row), else the oldest (so a status stays visible).
    # Only ids go through the sort; the page's columns are read for its rows alone.
    grouped = (
        f"SELECT j.id, j.first_seen_at_ms, count(*) OVER copy AS copies, row_number() OVER copy AS copy_rank "
        f"FROM jobs j {_POSTING_JOIN} {fr_join} WHERE {' AND '.join(where)} "
        # One sort serves both: the frame spans the whole group, which count needs and row_number ignores.
        f"WINDOW copy AS (PARTITION BY {_COPY_KEY} ORDER BY EXISTS (SELECT 1 FROM applications a WHERE a.job_id = j.id) DESC, "
        f"j.id IN ({_ids(labeled)}) DESC, j.id ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)")
    page = (f"SELECT id, first_seen_at_ms, copies, count(*) OVER () AS total FROM ({grouped}) WHERE copy_rank = 1 "
            f"ORDER BY first_seen_at_ms DESC, id DESC LIMIT ? OFFSET ?")
    rows = conn.execute(
        f"SELECT j.id, j.title, j.company, j.location_text, j.work_mode, j.posted_at, j.first_seen_at_ms, fr.verdict, fr.reasons, "
        f"EXISTS (SELECT 1 FROM filter_overrides fo WHERE fo.job_id = j.id), "
        f"(SELECT status FROM applications a WHERE a.job_id = j.id), g.copies, g.total, j.salary_min, j.salary_max, j.salary_period "
        f"FROM ({page}) g JOIN jobs j ON j.id = g.id {_filter_join()} ORDER BY g.first_seen_at_ms DESC, g.id DESC",
        params + [limit, int(offset), profile.id, filter_rules.RULESET_VERSION]).fetchall()
    # The total rides on the page rows; past the last page there are none, so count separately.
    total = rows[0][12] if rows else conn.execute(f"SELECT count(*) FROM ({grouped}) WHERE copy_rank = 1", params).fetchone()[0]
    order = {"APPLY": 0, "STRETCH": 1, "REVIEW": 2, "SKIP": 3}
    jobs = []
    for r in rows:
        reasons = json.loads(r[8]) if r[8] else {}
        rec = recs.get(r[0])
        jobs.append({"id": r[0], "title": r[1], "company": r[2], "location": r[3], "work_mode": r[4], "posted_at": r[5],
                     "first_seen_at_ms": r[6], "filter_verdict": r[7], "filter_primary": reasons.get("primary"),
                     "override": bool(r[9]), "application_status": r[10], "copies": r[11],
                     # Pay and the fit counts let a row say why it is there (UX redesign step 2); display only.
                     "salary": {"min": r[13], "max": r[14], "period": r[15]} if r[15] else None,
                     "skipped": "you" if r[0] in labeled else "copy" if r[0] in hidden else None,
                     "recommendation": None if rec is None else {
                         k: rec[k] for k in ("recommendation", "eligibility", "fit", "opportunity", "rank_key", "current",
                                             "newer_evaluation_pending", "fit_counts")}})
    if view == "recommended":
        jobs.sort(key=lambda j: (order[j["recommendation"]["recommendation"]], -(j["recommendation"]["rank_key"] or 0), j["id"]))
    return {"view": view, "total": total, "jobs": jobs, "search_profile": {"id": profile.id, "version": profile.version}}


def review_questions(conn: sqlite3.Connection) -> list[dict]:
    """The REVIEW questions without the jobs you skipped (and their copies); a question left with no
    job is dropped. Each job entry says whether its line is an eligibility condition the code did not
    recognize (`unrecognized_eligibility`), so the window does not offer an experience answer for it,
    and whether it is a reading problem only JobForge can settle (`reading_problem`, below). Display
    only: no decision reads either field."""
    _, hidden = skipped(conn)
    groups = []
    reasons: dict[int, dict[int, dict]] = {}
    for g in evaluation_store.review_questions(conn):
        jobs = [j for j in g["jobs"] if j["job_id"] not in hidden]
        for j in jobs:
            row = conn.execute("SELECT model_eligibility_type, eligibility_type FROM requirements WHERE id = ?",
                               (j.get("requirement_id"),)).fetchone() if j.get("requirement_id") else None
            j["unrecognized_eligibility"] = evaluation_store.unrecognized_eligibility(*row) if row else None
            if j["evaluation_id"] not in reasons:
                reasons[j["evaluation_id"]] = _eligibility_reasons(conn, j["evaluation_id"])
            j["reading_problem"] = _reading_problem(reasons[j["evaluation_id"]].get(j.get("requirement_id")))
        if jobs:
            groups.append({**g, "jobs": jobs})
    return sorted(groups, key=lambda g: -len(g["jobs"]))


def _eligibility_reasons(conn: sqlite3.Connection, evaluation_id: int) -> dict[int, dict]:
    row = conn.execute("SELECT eligibility_reasons FROM recommendations WHERE evaluation_id = ? AND verdict_rule_version = ? "
                       "ORDER BY id DESC LIMIT 1", (evaluation_id, VERDICT_RULE_VERSION)).fetchone()
    return {r["requirement_id"]: r for r in json.loads(row[0]) if r.get("requirement_id") is not None} if row else {}


def _reading_problem(reason: dict | None) -> dict | None:
    """An eligibility line your confirmed fact was already checked against, still "unknown" because the
    AI and the code read it differently or only the code's scan found it: those never decide a job
    (ARCHITECTURE 6.4, D15). No fact you add can settle it, so the window must not ask for one (Kevin,
    2026-10-24: residency questions shown for jobs whose residency fact was confirmed)."""
    if not reason or reason.get("basis") != "verified" or reason.get("effect") != "unknown":
        return None
    return {"outcome": reason.get("outcome"), "quote": reason.get("quote"), "status": reason.get("status"),
            "origin": reason.get("origin")}


def copies_of(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    """The other open postings that are exact copies of this job (_COPY_KEY), oldest first."""
    rows = conn.execute(
        f"SELECT j.id, coalesce(rp.listing_url, rp.apply_url), j.first_seen_at_ms FROM jobs j {_POSTING_JOIN} "
        f"WHERE j.employer_id = (SELECT employer_id FROM jobs WHERE id = ?) AND j.id != ? AND j.closed_at_ms IS NULL "
        f"AND ({_COPY_KEY}) = (SELECT {_COPY_KEY} FROM jobs j {_POSTING_JOIN} WHERE j.id = ?) ORDER BY j.id",
        (job_id, job_id, job_id)).fetchall()
    return [{"id": r[0], "url": r[1], "first_seen_at_ms": r[2]} for r in rows]


def job_detail(conn: sqlite3.Connection, job_id: int) -> dict | None:
    r = conn.execute(
        "SELECT j.id, j.title, j.company, j.location_text, j.work_mode, j.salary_min, j.salary_max, j.salary_period, j.posted_at, "
        "j.first_seen_at_ms, j.last_seen_at_ms, j.closed_at_ms, rp.id, rp.listing_url, rp.apply_url, "
        f"{textpack.HTML}, {textpack.TEXT}, e.display_name, s.kind FROM jobs j JOIN raw_postings rp ON rp.id = j.current_raw_posting_id "
        f"{textpack.JOIN} "
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
        "copies": copies_of(conn, job_id),
    }
