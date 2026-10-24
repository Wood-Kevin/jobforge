"""New / changed / unchanged / closed detection (ARCHITECTURE 3.3, "Change detection").

Called inside the poll's write transaction, so a poll's changes land together or not at all.
- material_sha256 covers what a reader of the posting would care about; a change writes a
  new raw_postings row and moves jobs.current_raw_posting_id. Old rows and anything that
  referenced them stay (append-only history).
- payload_sha256 covers the verbatim item; a change that is not material only updates
  jobs.last_payload_sha256 (no new row, no GPU work).
- A job closes only after 2 consecutive successful complete listings without it; failed,
  partial and 304 polls never close jobs. Reappearance reopens it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobforge_agent.db import textpack
from jobforge_agent.discovery.adapters import ADAPTER_VERSION
from jobforge_agent.discovery.models import Employer, RawJob
from jobforge_agent.discovery.text import collapse_ws
from jobforge_agent.filtering.pipeline import WANTS_TEXT
from jobforge_agent.filtering.rules import RULESET_VERSION

CLOSE_AFTER_MISSING = 2
_TRACKING_PARAMS = {"gh_src", "source", "src", "ref", "lever-source", "lever-origin", "utm_id"}


class CollisionError(Exception):
    """One listing returned different postings under the same source_job_id (scanner guard)."""


@dataclass
class ChangeCounts:
    touched: list = None  # job ids that are new, materially changed or reopened: they get filtered
    new: int = 0
    changed: int = 0
    payload_only: int = 0
    unchanged: int = 0
    closed: int = 0
    reopened: int = 0
    text_restored: int = 0  # unchanged postings whose dropped text was wanted again (0010)

    @property
    def events(self) -> int:
        return self.new + self.changed + self.closed + self.reopened


def strip_tracking(url: str) -> str:
    parts = urlsplit(url or "")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.startswith("utm_") and k not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def material_sha256(r: RawJob) -> str:
    material = {
        "title": collapse_ws(r.title),
        "company": collapse_ws(r.company),
        "location": collapse_ws(r.location_text),
        "workplace": r.workplace,
        "salary": [r.salary.min, r.salary.max, r.salary.period] if r.salary else None,
        "apply_url": strip_tracking(r.apply_url),
        "description": collapse_ws(r.description),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def payload_sha256(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _dedupe(raws: list[RawJob]) -> dict[str, RawJob]:
    by_id: dict[str, RawJob] = {}
    for r in raws:
        prior = by_id.get(r.source_job_id)
        if prior is not None and material_sha256(prior) != material_sha256(r):
            raise CollisionError(f"{r.source_job_id} appears twice with different content "
                                 f"({prior.title!r} vs {r.title!r})")
        by_id[r.source_job_id] = r
    return by_id


def _event(conn, type_: str, job_id: int, employer_id: int, now: int) -> None:
    conn.execute("INSERT INTO change_events (type, job_id, employer_id, created_at_ms) VALUES (?, ?, ?, ?)",
                 (type_, job_id, employer_id, now))


def _insert_raw(conn, employer: Employer, r: RawJob, fetch_id: int, now: int, material: str, payload: str) -> int:
    existing = conn.execute("SELECT id FROM raw_postings WHERE source_job_id = ? AND material_sha256 = ?",
                            (r.source_job_id, material)).fetchone()
    if existing:
        raw_id = existing[0]  # content reverted to a version already on record (A -> B -> A)
    else:
        # Since 0010 the API object is not stored (never read; payload_sha256 records it) and the
        # description goes to posting_texts, compressed; the filter step drops it again if the
        # posting is rejected (db/textpack.py). P1.10 measured 30 KB per posting before.
        raw_id = conn.execute(
            "INSERT INTO raw_postings (employer_id, source_job_id, fetched_at_ms, first_fetch_id, listing_url, apply_url, "
            "payload, payload_sha256, material_sha256, adapter_version) VALUES (?, ?, ?, ?, ?, ?, 'null', ?, ?, ?) RETURNING id",
            (employer.id, r.source_job_id, now, fetch_id, r.listing_url, r.apply_url, payload, material,
             ADAPTER_VERSION[employer.kind])).fetchone()[0]
    textpack.store(conn, raw_id, r.description_raw, r.description)
    return raw_id


def _wants_text(conn, job_id: int) -> int | None:
    """The current raw posting id if its text was dropped but is wanted again (pipeline.WANTS_TEXT)."""
    row = conn.execute(
        f"SELECT rp.id FROM jobs j JOIN raw_postings rp ON rp.id = j.current_raw_posting_id {textpack.JOIN} "
        f"WHERE j.id = ? AND {WANTS_TEXT}", (job_id, RULESET_VERSION)).fetchone()
    return row[0] if row else None


def _job_fields(r: RawJob) -> tuple:
    s = r.salary
    return (r.title, r.company, r.location_text, r.workplace,
            s.min if s else None, s.max if s else None, s.period if s else None, r.posted_at)


def apply_listing(conn: sqlite3.Connection, employer: Employer, raws: list[RawJob], fetch_id: int, now: int) -> ChangeCounts:
    """Apply one successful, complete listing. The caller holds the write transaction."""
    listing = _dedupe(raws)
    counts = ChangeCounts(touched=[])
    for sid, r in listing.items():
        material, payload = material_sha256(r), payload_sha256(r.item)
        row = conn.execute(
            "SELECT j.id, j.last_payload_sha256, j.closed_at_ms, rp.material_sha256 FROM jobs j "
            "JOIN raw_postings rp ON rp.id = j.current_raw_posting_id WHERE j.source_job_id = ?", (sid,)).fetchone()
        if row is None:
            raw_id = _insert_raw(conn, employer, r, fetch_id, now, material, payload)
            job_id = conn.execute(
                "INSERT INTO jobs (source_job_id, employer_id, current_raw_posting_id, last_payload_sha256, title, company, "
                "location_text, work_mode, salary_min, salary_max, salary_period, posted_at, first_seen_at_ms, last_seen_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (sid, employer.id, raw_id, payload, *_job_fields(r), now, now)).fetchone()[0]
            _event(conn, "posting_new", job_id, employer.id, now)
            counts.new += 1
            counts.touched.append(job_id)
            continue
        job_id, last_payload, closed_at, current_material = row[0], row[1], row[2], row[3]
        if closed_at is not None:
            _event(conn, "job_reopened", job_id, employer.id, now)
            counts.reopened += 1
            counts.touched.append(job_id)
        if current_material != material:
            raw_id = _insert_raw(conn, employer, r, fetch_id, now, material, payload)
            conn.execute(
                "UPDATE jobs SET current_raw_posting_id = ?, last_payload_sha256 = ?, title = ?, company = ?, location_text = ?, "
                "work_mode = ?, salary_min = ?, salary_max = ?, salary_period = ?, posted_at = ?, last_seen_at_ms = ?, "
                "missing_polls = 0, closed_at_ms = NULL WHERE id = ?",
                (raw_id, payload, *_job_fields(r), now, job_id))
            _event(conn, "posting_changed", job_id, employer.id, now)
            counts.changed += 1
            if job_id not in counts.touched:
                counts.touched.append(job_id)
        else:
            if last_payload != payload:
                conn.execute("UPDATE jobs SET last_payload_sha256 = ?, last_seen_at_ms = ?, missing_polls = 0, closed_at_ms = NULL WHERE id = ?",
                             (payload, now, job_id))
                counts.payload_only += 1
            else:
                conn.execute("UPDATE jobs SET last_seen_at_ms = ?, missing_polls = 0, closed_at_ms = NULL WHERE id = ?", (now, job_id))
                counts.unchanged += 1
            raw_id = _wants_text(conn, job_id)
            if raw_id is not None:  # dropped as rejected, wanted again: keep it and let the filter decide
                textpack.store(conn, raw_id, r.description_raw, r.description)
                counts.text_restored += 1
                if job_id not in counts.touched:
                    counts.touched.append(job_id)

    # Open jobs of this employer that the complete listing no longer contains.
    open_ids = conn.execute("SELECT id, source_job_id, missing_polls FROM jobs WHERE employer_id = ? AND closed_at_ms IS NULL",
                            (employer.id,)).fetchall()
    for job_id, sid, missing in open_ids:
        if sid in listing:
            continue
        missing += 1
        if missing >= CLOSE_AFTER_MISSING:
            conn.execute("UPDATE jobs SET missing_polls = ?, closed_at_ms = ? WHERE id = ?", (missing, now, job_id))
            _event(conn, "job_closed", job_id, employer.id, now)
            counts.closed += 1
        else:
            conn.execute("UPDATE jobs SET missing_polls = ? WHERE id = ?", (missing, job_id))
    return counts


def touch_unchanged_board(conn: sqlite3.Connection, employer_id: int, now: int) -> int:
    """A 304 means the board is unchanged: its open jobs were seen now. Never closes anything."""
    return conn.execute("UPDATE jobs SET last_seen_at_ms = ? WHERE employer_id = ? AND closed_at_ms IS NULL",
                        (now, employer_id)).rowcount
