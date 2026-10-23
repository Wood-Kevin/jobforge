"""Profile snapshots (ARCHITECTURE 4.2): the exact profile an evaluation saw.

A snapshot is content-addressed: the sha256 of every experience item (id, kind, statement,
status, attributes, years, dates) plus the completeness flags. An unchanged profile maps to the
same snapshot, so a no-op edit queues no model calls; any change, including a verify, is a new
snapshot and new evaluations (D10). Snapshots and their items are insert-only (triggers), and
`match_citations` can only name an item of the evaluated snapshot (FK): inferred claims are
never in one, so they can never be cited (D2).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    id: int
    kind: str
    statement: str
    status: str
    attributes: dict
    years: float | None
    start_date: str | None
    end_date: str | None


@dataclass(frozen=True)
class Snapshot:
    id: int
    items: dict[int, Item]
    completeness: dict

    def facts(self) -> list[Item]:
        return [i for i in self.items.values() if i.kind == "eligibility"]

    def experience(self) -> list[Item]:
        return [i for i in self.items.values() if i.kind != "eligibility"]


def _current(conn: sqlite3.Connection) -> tuple[list[dict], dict]:
    items = [{"id": r[0], "kind": r[1], "statement": r[2], "status": r[3], "attributes": json.loads(r[4]), "years": r[5],
              "start_date": r[6], "end_date": r[7]}
             for r in conn.execute("SELECT id, kind, statement, status, attributes, years, start_date, end_date "
                                   "FROM experience_items ORDER BY id")]
    c = conn.execute("SELECT cert_list_complete, eligibility_facts_complete FROM profile_completeness WHERE id = 1").fetchone()
    completeness = {"cert_list_complete": bool(c[0]) if c else False, "eligibility_facts_complete": bool(c[1]) if c else False}
    return items, completeness


def digest(items: list[dict], completeness: dict) -> str:
    blob = json.dumps({"items": items, "completeness": completeness}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def ensure(conn: sqlite3.Connection, now: int) -> tuple[int, bool]:
    """(snapshot id of the current profile, created). Call inside the caller's transaction."""
    items, completeness = _current(conn)
    sha = digest(items, completeness)
    row = conn.execute("SELECT id FROM profile_snapshots WHERE sha256 = ?", (sha,)).fetchone()
    if row:
        return row[0], False
    sid = conn.execute("INSERT INTO profile_snapshots (sha256, completeness, created_at_ms) VALUES (?, ?, ?) RETURNING id",
                       (sha, json.dumps(completeness), now)).fetchone()[0]
    for i in items:
        conn.execute("INSERT INTO profile_snapshot_items (snapshot_id, item_id, kind, statement, status, attributes, years, "
                     "start_date, end_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, i["id"], i["kind"], i["statement"], i["status"], json.dumps(i["attributes"]), i["years"],
                      i["start_date"], i["end_date"]))
    return sid, True


def load(conn: sqlite3.Connection, snapshot_id: int) -> Snapshot:
    c = conn.execute("SELECT completeness FROM profile_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    items = {r[0]: Item(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5], r[6], r[7])
             for r in conn.execute("SELECT item_id, kind, statement, status, attributes, years, start_date, end_date "
                                   "FROM profile_snapshot_items WHERE snapshot_id = ? ORDER BY item_id", (snapshot_id,))}
    return Snapshot(snapshot_id, items, json.loads(c[0]))
