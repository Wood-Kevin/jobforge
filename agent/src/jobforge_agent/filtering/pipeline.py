"""Filter stage and analysis queueing (ARCHITECTURE 3, 4.2; SPEC D9, D10).

- Every filtered posting gets a filter_results row keyed by (raw posting, profile version,
  ruleset version): re-running is idempotent, and a new profile or ruleset adds rows instead of
  rewriting history.
- A pass (or a user "evaluate anyway" override) queues a pending posting_analyses row for the
  job's current raw posting. Older pending analyses of the same job are superseded first, so
  there is at most one pending analysis per job (unique partial index).
- An analysis that already exists for the same posting version and analysis versions is never
  requeued: posting analysis depends only on the posting text (D10).
- Closed jobs are filtered for the record but not queued.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from jobforge_agent.analysis.versions import EXTRACTION_PROMPT_VERSION, INTERPRETER_VERSION, SEGMENTER_VERSION
from jobforge_agent.filtering import rules
from jobforge_agent.filtering.profile import CompiledProfile, compile_profile
from jobforge_agent.filtering.salary import Salary

# Keyed by the rules text, not the row id: ids repeat across databases (tests, restores).
_compiled: dict[str, CompiledProfile] = {}


@dataclass(frozen=True)
class ActiveProfile:
    id: int
    version: int
    compiled: CompiledProfile


def active_profile(conn: sqlite3.Connection) -> ActiveProfile | None:
    row = conn.execute("SELECT id, version, rules FROM search_profiles ORDER BY version DESC LIMIT 1").fetchone()
    if row is None:
        return None
    if row[2] not in _compiled:
        _compiled[row[2]] = compile_profile(json.loads(row[2]))
    return ActiveProfile(row[0], row[1], _compiled[row[2]])


def default_model_config_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM settings WHERE key = 'default_model_config_id'").fetchone()
    return int(row[0]) if row and row[0].isdigit() else None


def job_view(conn: sqlite3.Connection, job_id: int) -> tuple[rules.JobView, int, bool] | None:
    row = conn.execute(
        "SELECT j.title, j.location_text, coalesce(rp.description_text, unpack_text(rp.description_text_z)), j.work_mode, j.salary_min, j.salary_max, j.salary_period, "
        "j.posted_at, j.current_raw_posting_id, j.closed_at_ms IS NOT NULL FROM jobs j "
        "JOIN raw_postings rp ON rp.id = j.current_raw_posting_id WHERE j.id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    salary = Salary(row[4], row[5], row[6], "structured") if row[6] and (row[4] is not None or row[5] is not None) else None
    view = rules.JobView(title=row[0], location_text=row[1] or "", description=row[2] or "", work_mode=row[3],
                         salary=salary, posted_at=row[7])
    return view, row[8], bool(row[9])


def queue_analysis(conn: sqlite3.Connection, job_id: int, raw_id: int, model_config_id: int, now: int) -> bool:
    """Queue analysis of the job's current posting version; True if a new pending row was created."""
    exists = conn.execute(
        "SELECT 1 FROM posting_analyses WHERE raw_posting_id = ? AND model_config_id = ? AND extraction_prompt_version = ? "
        "AND segmenter_version = ? AND interpreter_version = ?",
        (raw_id, model_config_id, EXTRACTION_PROMPT_VERSION, SEGMENTER_VERSION, INTERPRETER_VERSION)).fetchone()
    if exists:
        return False
    conn.execute("UPDATE posting_analyses SET status = 'superseded' WHERE job_id = ? AND status = 'pending'", (job_id,))
    conn.execute(
        "INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, segmenter_version, "
        "interpreter_version, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (raw_id, job_id, model_config_id, EXTRACTION_PROMPT_VERSION, SEGMENTER_VERSION, INTERPRETER_VERSION, now))
    return True


@dataclass
class FilterStats:
    filtered: int = 0
    already_filtered: int = 0
    passed: int = 0
    rejected: int = 0
    queued: int = 0
    overrides_queued: int = 0
    skipped_no_model: int = 0


def filter_job(conn: sqlite3.Connection, job_id: int, profile: ActiveProfile, now: int, stats: FilterStats,
               model_config_id: int | None, emit_event: bool) -> rules.FilterOutcome | None:
    """Filter one job's current posting. The caller holds the write transaction."""
    got = job_view(conn, job_id)
    if got is None:
        return None
    view, raw_id, closed = got
    outcome = rules.evaluate(view, profile.compiled)
    cur = conn.execute(
        "INSERT INTO filter_results (job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons, created_at_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (raw_posting_id, search_profile_id, ruleset_version) DO NOTHING",
        (job_id, raw_id, profile.id, rules.RULESET_VERSION, "pass" if outcome.passed else "reject",
         json.dumps(outcome.reasons_json()), now))
    if cur.rowcount:
        stats.filtered += 1
    else:
        stats.already_filtered += 1
    stats.passed += outcome.passed
    stats.rejected += not outcome.passed
    override = conn.execute("SELECT 1 FROM filter_overrides WHERE job_id = ?", (job_id,)).fetchone() is not None
    if (outcome.passed or override) and not closed:
        if model_config_id is None:
            stats.skipped_no_model += 1
        elif queue_analysis(conn, job_id, raw_id, model_config_id, now):
            stats.queued += 1
            stats.overrides_queued += (override and not outcome.passed)
            if emit_event:
                conn.execute("INSERT INTO change_events (type, job_id, created_at_ms) VALUES ('job_queued_for_analysis', ?, ?)",
                             (job_id, now))
    return outcome


def filter_jobs(conn: sqlite3.Connection, job_ids: list[int], now: int, *, emit_events: bool = True) -> FilterStats | None:
    """Filter jobs with the active profile; None if no profile exists yet (postings wait, re-filter later)."""
    profile = active_profile(conn)
    if profile is None:
        return None
    stats = FilterStats()
    model_id = default_model_config_id(conn)
    for job_id in job_ids:
        filter_job(conn, job_id, profile, now, stats, model_id, emit_events)
    return stats


def all_job_ids(conn: sqlite3.Connection) -> list[int]:
    """Every job, open or closed: a re-filter gives each stored posting a verdict under the new
    profile (closed jobs for the record; filter_job never queues them)."""
    return [r[0] for r in conn.execute("SELECT id FROM jobs ORDER BY id")]
