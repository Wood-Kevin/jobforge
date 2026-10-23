"""Applications: the user's own record of what they did with a job (P1.8). User actions only.

One application per job (unique index, migration 0006). Every status change, including the first,
appends an `application_events` row (append-only by trigger) in the caller's transaction, so the
history cannot be rewritten. Nothing here contacts an employer: JobForge never applies on Kevin's
behalf (SPEC section 2, out of scope).
"""
from __future__ import annotations

import sqlite3

STATUSES = ("saved", "applied", "interviewing", "offer", "rejected", "withdrawn")


class ApplicationError(ValueError):
    """Bad input; names the field. Nothing is written."""


def _row(conn: sqlite3.Connection, app_id: int) -> dict:
    r = conn.execute("SELECT id, job_id, status, notes, created_at_ms FROM applications WHERE id = ?", (app_id,)).fetchone()
    events = [{"from_status": e[0], "to_status": e[1], "at_ms": e[2], "note": e[3]} for e in conn.execute(
        "SELECT from_status, to_status, at_ms, note FROM application_events WHERE application_id = ? ORDER BY id", (app_id,))]
    return {"id": r[0], "job_id": r[1], "status": r[2], "notes": r[3], "created_at_ms": r[4], "events": events}


def for_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    r = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    return _row(conn, r[0]) if r else None


def set_for_job(conn: sqlite3.Connection, job_id: int, fields: dict, now: int) -> dict:
    """Create or update the job's application. Caller holds the user-action transaction."""
    unknown = set(fields) - {"status", "notes", "note"}
    if unknown:
        raise ApplicationError(f"unknown field(s): {', '.join(sorted(unknown))}")
    status, notes, note = fields.get("status"), fields.get("notes"), fields.get("note")
    if status is not None and status not in STATUSES:
        raise ApplicationError(f"status must be one of {', '.join(STATUSES)}")
    for name, v in (("notes", notes), ("note", note)):
        if v is not None and not (isinstance(v, str) and len(v) <= 4000):
            raise ApplicationError(f"{name} must be text of at most 4000 characters")
    if conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
        raise LookupError(f"job {job_id}")
    cur = conn.execute("SELECT id, status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    if cur is None:
        if status is None:
            raise ApplicationError("status is required for a new application")
        app_id = conn.execute("INSERT INTO applications (job_id, status, notes, created_at_ms) VALUES (?, ?, ?, ?) RETURNING id",
                              (job_id, status, notes, now)).fetchone()[0]
        conn.execute("INSERT INTO application_events (application_id, from_status, to_status, at_ms, note) VALUES (?, NULL, ?, ?, ?)",
                     (app_id, status, now, note))
        return _row(conn, app_id)
    app_id, old = cur
    if notes is not None:
        conn.execute("UPDATE applications SET notes = ? WHERE id = ?", (notes, app_id))
    if status is not None and status != old:
        conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, app_id))
        conn.execute("INSERT INTO application_events (application_id, from_status, to_status, at_ms, note) VALUES (?, ?, ?, ?, ?)",
                     (app_id, old, status, now, note))
    return _row(conn, app_id)


def listing(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT a.id, a.job_id, a.status, a.notes, a.created_at_ms, j.title, j.company, j.closed_at_ms IS NOT NULL, "
        "(SELECT max(at_ms) FROM application_events e WHERE e.application_id = a.id) "
        "FROM applications a JOIN jobs j ON j.id = a.job_id ORDER BY 9 DESC").fetchall()
    return [{"id": r[0], "job_id": r[1], "status": r[2], "notes": r[3], "created_at_ms": r[4], "title": r[5], "company": r[6],
             "job_closed": bool(r[7]), "last_change_ms": r[8]} for r in rows]
