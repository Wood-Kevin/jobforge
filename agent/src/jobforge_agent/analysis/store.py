"""Posting-analysis rows: claim, persist, release, read (ARCHITECTURE 3.4, 4.2, 4.3).

Each function that writes is one short transaction on the caller's connection ('evaluation' for
the worker). The worker claims in one transaction, commits, calls the model with no transaction
open, then persists in a second one (3.4, "No transaction spans a network call or model call").
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from jobforge_agent.analysis.interpret import Requirement
from jobforge_agent.analysis.segment import Segmented
from jobforge_agent.db import textpack

MAX_ATTEMPTS = 3  # same budget as startup recovery (db/recovery.py)


@dataclass(frozen=True)
class Claim:
    analysis_id: int
    raw_posting_id: int
    job_id: int
    model_config_id: int
    title: str
    text: str


def pending_config_ids(conn: sqlite3.Connection) -> tuple[list[int], int]:
    """(enabled configs with pending work, oldest work first; pending count waiting on a config
    that is not enabled)."""
    ready = [r[0] for r in conn.execute(
        "SELECT pa.model_config_id FROM posting_analyses pa JOIN model_configs mc ON mc.id = pa.model_config_id "
        "WHERE pa.status = 'pending' AND mc.enabled = 1 GROUP BY pa.model_config_id ORDER BY min(pa.id)")]
    waiting = conn.execute(
        "SELECT count(*) FROM posting_analyses pa JOIN model_configs mc ON mc.id = pa.model_config_id "
        "WHERE pa.status = 'pending' AND mc.enabled = 0").fetchone()[0]
    return ready, waiting


def _tx(conn: sqlite3.Connection, fn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return result


def move_pending(conn: sqlite3.Connection, from_id: int, to_id: int) -> int:
    """Hand a cloud config's pending analyses to the fallback config while the cloud is out (SPEC D18: postings
    never wait on a cap; the fallback's reading then stays). Where the fallback already has a row for the same
    posting and versions, the pending one is superseded instead, and that row is queued again if it had failed
    or was superseded (a done one is the reading). Returns how many rows were handed over."""
    def go():
        moved = 0
        for aid, raw_id, ep, sv, iv in conn.execute(
                "SELECT id, raw_posting_id, extraction_prompt_version, segmenter_version, interpreter_version "
                "FROM posting_analyses WHERE status = 'pending' AND model_config_id = ? ORDER BY id", (from_id,)).fetchall():
            other = conn.execute(
                "SELECT id, status FROM posting_analyses WHERE raw_posting_id = ? AND model_config_id = ? "
                "AND extraction_prompt_version = ? AND segmenter_version = ? AND interpreter_version = ?",
                (raw_id, to_id, ep, sv, iv)).fetchone()
            if other is None:
                conn.execute("UPDATE posting_analyses SET model_config_id = ? WHERE id = ?", (to_id, aid))
            else:
                # Superseded first: one pending analysis per job (unique index).
                conn.execute("UPDATE posting_analyses SET status = 'superseded', "
                             "last_error = 'cloud model out: the fallback model has this posting version' WHERE id = ?", (aid,))
                if other[1] in ("failed", "superseded"):
                    conn.execute("UPDATE posting_analyses SET status = 'pending', attempts = 0, last_error = NULL, "
                                 "lease_until_ms = NULL WHERE id = ?", (other[0],))
            moved += 1
        return moved
    return _tx(conn, go)


def claim(conn: sqlite3.Connection, model_config_id: int, now: int, lease_ms: int) -> Claim | None:
    """Claim the oldest pending analysis for one config (BEGIN IMMEDIATE + UPDATE ... RETURNING)."""
    def go():
        row = conn.execute(
            "UPDATE posting_analyses SET status = 'running', lease_until_ms = ? WHERE id = (SELECT id FROM posting_analyses "
            "WHERE status = 'pending' AND model_config_id = ? ORDER BY id LIMIT 1) RETURNING id, raw_posting_id, job_id",
            (now + lease_ms, model_config_id)).fetchone()
        if row is None:
            return None
        posting = conn.execute(
            f"SELECT j.title, {textpack.TEXT}, {textpack.KEPT} FROM raw_postings rp {textpack.JOIN} JOIN jobs j ON j.id = ? WHERE rp.id = ?",
            (row[2], row[1])).fetchone()
        if not posting[2]:
            # Rejecting a posting supersedes its pending analysis in the same transaction as dropping
            # its text (filtering/pipeline.py), so this is a broken invariant, not a normal case. Mark
            # it failed (counted on Status) rather than analyze an empty posting or block the queue.
            conn.execute("UPDATE posting_analyses SET status = 'failed', lease_until_ms = NULL, "
                         "last_error = 'posting text not kept' WHERE id = ?", (row[0],))
            _event(conn, "analysis_failed", row[2], {"analysis_id": row[0], "error": "text_not_kept"}, now)
            return go()
        return Claim(row[0], row[1], row[2], model_config_id, posting[0] or "", posting[1] or "")
    return _tx(conn, go)


def stored_extraction(conn: sqlite3.Connection, analysis_id: int) -> tuple[int, dict, str | None] | None:
    """A finished analysis of the same posting by the same model config, E1 prompt and segmenter, whose E1
    answer can be read again: (its id, raw_response, model reported). E1's output depends only on those and
    the posting text, so an interpreter-only change (interp-v3) re-runs E2 on it without a model call.
    Rejected: re-running E1 on every posting for an E2 change (one model call per job, and E1's own drift)."""
    row = conn.execute(
        "SELECT o.id, o.raw_response, o.model_id_reported FROM posting_analyses a JOIN posting_analyses o "
        "ON o.raw_posting_id = a.raw_posting_id AND o.model_config_id = a.model_config_id "
        "AND o.extraction_prompt_version = a.extraction_prompt_version AND o.segmenter_version = a.segmenter_version "
        "WHERE a.id = ? AND o.id <> a.id AND o.status = 'done' AND o.raw_response IS NOT NULL ORDER BY o.id DESC LIMIT 1",
        (analysis_id,)).fetchone()
    if row is None:
        return None
    raw = json.loads(row[1])
    return (row[0], raw, row[2]) if raw.get("attempts") else None


def _event(conn, type_: str, job_id: int, ref: dict, now: int) -> None:
    conn.execute("INSERT INTO change_events (type, job_id, ref, created_at_ms) VALUES (?, ?, ?, ?)",
                 (type_, job_id, json.dumps(ref), now))


def persist_done(conn: sqlite3.Connection, c: Claim, seg: Segmented, reqs: list[Requirement], raw_response: dict,
                 model_reported: str | None, now: int) -> dict:
    """Store the requirements (both readings per row) and mark the analysis done."""
    counts = {"requirements": sum(r.origin == "model" for r in reqs),
              "unextracted_eligibility": sum(r.origin == "unextracted_eligibility" for r in reqs),
              "confirmed": sum(r.interpretation_status == "confirmed" for r in reqs),
              "conflict": sum(r.interpretation_status == "conflict" for r in reqs),
              "unverifiable": sum(r.interpretation_status == "unverifiable" for r in reqs),
              "hard": sum(r.final_class == "hard" for r in reqs)}

    def go():
        updated = conn.execute(
            "UPDATE posting_analyses SET status = 'done', sections = ?, raw_response = ?, model_id_reported = ?, "
            "attempts = attempts + 1, lease_until_ms = NULL, last_error = NULL WHERE id = ? AND status = 'running'",
            (json.dumps(seg.to_json()), json.dumps(raw_response), model_reported, c.analysis_id)).rowcount
        if updated != 1:
            raise RuntimeError(f"analysis {c.analysis_id} is no longer running")
        for r in reqs:
            conn.execute(
                "INSERT INTO requirements (posting_analysis_id, text, jd_quote, quote_found, quote_occurrences, section_label, "
                "sentence, model_importance, model_polarity, model_subject, model_eligibility_type, eligibility_type, min_years, "
                "cue_importance, cue_polarity, cue_subject, final_class, interpretation_status, origin, flags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c.analysis_id, r.text, r.jd_quote, int(r.quote_found), r.quote_occurrences, r.section_label, r.sentence,
                 r.model_importance, r.model_polarity, r.model_subject, r.model_eligibility_type, r.eligibility_type, r.min_years,
                 r.cue_importance, r.cue_polarity, r.cue_subject, r.final_class, r.interpretation_status, r.origin,
                 json.dumps(r.flags)))
        _event(conn, "analysis_done", c.job_id, {"analysis_id": c.analysis_id, **counts}, now)
    _tx(conn, go)
    return counts


def persist_failed(conn: sqlite3.Connection, c: Claim, kind: str, detail: str, raw_response: dict | None, now: int) -> None:
    """A final failure: kept with its error and whatever the model returned."""
    def go():
        conn.execute(
            "UPDATE posting_analyses SET status = 'failed', attempts = attempts + 1, lease_until_ms = NULL, last_error = ?, "
            "raw_response = ? WHERE id = ?",
            (f"{kind}: {detail}"[:1000], json.dumps(raw_response) if raw_response is not None else None, c.analysis_id))
        _event(conn, "analysis_failed", c.job_id, {"analysis_id": c.analysis_id, "error": kind}, now)
    _tx(conn, go)


def release(conn: sqlite3.Connection, c: Claim, *, spend_attempt: bool, error: str | None, now: int) -> str:
    """Put a claimed analysis back. Spending an attempt can exhaust the budget (-> failed).
    Returns the new status. If the job got a newer pending analysis meanwhile, this one is
    superseded (the one-pending-per-job index allows no second pending row)."""
    def go():
        attempts = conn.execute("SELECT attempts FROM posting_analyses WHERE id = ?", (c.analysis_id,)).fetchone()[0]
        attempts += int(spend_attempt)
        newer = conn.execute("SELECT 1 FROM posting_analyses WHERE job_id = ? AND status = 'pending' AND id <> ?",
                             (c.job_id, c.analysis_id)).fetchone() is not None
        status = "superseded" if newer else ("failed" if attempts >= MAX_ATTEMPTS else "pending")
        conn.execute("UPDATE posting_analyses SET status = ?, attempts = ?, lease_until_ms = NULL, last_error = ? WHERE id = ?",
                     (status, attempts, error, c.analysis_id))
        if status == "failed":
            _event(conn, "analysis_failed", c.job_id, {"analysis_id": c.analysis_id, "error": "attempts_exhausted"}, now)
        return status
    return _tx(conn, go)


def counts(conn: sqlite3.Connection) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT status, count(*) FROM posting_analyses GROUP BY status")}


def analysis_view(conn: sqlite3.Connection, analysis_id: int) -> dict | None:
    a = conn.execute(
        "SELECT id, raw_posting_id, job_id, model_config_id, model_id_reported, extraction_prompt_version, segmenter_version, "
        "interpreter_version, sections, status, attempts, last_error, created_at_ms FROM posting_analyses WHERE id = ?",
        (analysis_id,)).fetchone()
    if a is None:
        return None
    out = dict(a)
    out["sections"] = json.loads(out["sections"]) if out["sections"] else None
    reqs = []
    for r in conn.execute("SELECT * FROM requirements WHERE posting_analysis_id = ? ORDER BY id", (analysis_id,)):
        d = dict(r)
        d["flags"] = json.loads(d["flags"])
        d["quote_found"] = bool(d["quote_found"])
        reqs.append(d)
    out["requirements"] = reqs
    return out


def latest_for_job(conn: sqlite3.Connection, job_id: int) -> int | None:
    row = conn.execute("SELECT id FROM posting_analyses WHERE job_id = ? AND status = 'done' ORDER BY id DESC LIMIT 1",
                       (job_id,)).fetchone()
    return row[0] if row else None
