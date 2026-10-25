"""Experience items, eligibility facts, verification, completeness and the inferred-claims inbox
(SPEC D2; ARCHITECTURE 4.2, 6.3-6.5).

Every function here runs on the user-action connection inside the caller's transaction.
Rules:
- Items are created self-reported. `verify_item` is the only code that sets `verified`
  (pinned by a source scan in tests/test_profile.py). Editing what a verified item says
  clears the verification: Kevin confirmed the old text, not the new one.
- Eligibility facts (kind 'eligibility') carry typed attributes per eligibility type, the
  same types the E2 lexicon assigns, so P1.7 can match hard requirements by code.
- Accepting an inferred claim creates a self-reported item; it never arrives verified.
"""
from __future__ import annotations

import json
import re
import sqlite3

KINDS = ("role", "skill", "cert", "education", "achievement", "eligibility")
ITEM_FIELDS = ("kind", "statement", "attributes", "start_date", "end_date", "years", "source")
CONTENT_FIELDS = ("kind", "statement", "attributes", "start_date", "end_date", "years")
_DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?$")

# type -> {field: (validator, required)}. One fact per type except licenses.
_BOOL = ("bool", lambda v: isinstance(v, bool))
_STR = ("text", lambda v: isinstance(v, str) and 0 < len(v.strip()) <= 200)


def _enum(*values):
    return (" | ".join(values), lambda v: v in values)


def _num(lo, hi):
    return (f"number {lo}-{hi}", lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi)


ELIGIBILITY = {
    "work_authorization": {"us_citizen": (_BOOL, True), "authorized_to_work_us": (_BOOL, True)},
    "sponsorship": {"needs_sponsorship": (_BOOL, True)},
    "clearance": {"level": (_enum("none", "confidential", "secret", "top_secret", "ts_sci"), True),
                  "status": (_enum("active", "current", "inactive", "expired", "none"), True),
                  "polygraph": (_enum("none", "ci", "full_scope"), False),
                  "last_active_year": (_num(1950, 2100), False)},
    # time_zone (2026-10-24): a state can be split between zones (Florida's panhandle is Central), so
    # "located in the Eastern timezone" cannot be settled from the state; the person's own answer can.
    "residency": {"country": (_STR, True), "state": (_STR, False), "city": (_STR, False),
                  "willing_to_relocate": (_BOOL, False),
                  "time_zone": (_enum("eastern", "central", "mountain", "pacific", "alaska", "hawaii"), False)},
    "physical": {"max_lift_lbs": (_num(0, 500), False), "can_stand_hours": (_num(0, 24), False)},
    "travel": {"max_travel_percent": (_num(0, 100), True)},
    "license": {"name": (_STR, True), "active": (_BOOL, True)},
    "program_status": {"active_duty": (_BOOL, False), "veteran": (_BOOL, False), "student": (_BOOL, False)},
    # Kevin, 2026-10-24: hours, shifts and on-call lines ("Friday - Tuesday, 8A - 5P Eastern", "24x7x365, shift work,
    # weekends and holidays", "on-call rotation") had nowhere to be answered. The facts are stored now; no posting
    # line is typed "availability" until the reading fix in Evaluation calibration 1 (cases E1-E2) is approved,
    # so cues.ELIGIBILITY_TYPES does not list it yet (AWAITING_CUES).
    "availability": {"on_call": (_BOOL, False), "weekends": (_BOOL, False), "holidays": (_BOOL, False),
                     "night_shifts": (_BOOL, False), "rotating_shifts": (_BOOL, False),
                     "other_time_zone_hours": (_BOOL, False)},
}
# Fact types whose posting lines the E2 cues do not recognize yet (see "availability" above).
AWAITING_CUES: set[str] = set()  # availability got its cues in interp-v3
MULTIPLE_ALLOWED = {"license"}


class ProfileInputError(ValueError):
    """Bad input; names the field. Nothing is written."""


class NotFound(LookupError):
    pass


class Conflict(Exception):
    pass


def _validate_eligibility(attrs: dict) -> None:
    kind = attrs.get("type")
    if kind not in ELIGIBILITY:
        raise ProfileInputError(f"attributes.type must be one of {', '.join(ELIGIBILITY)}")
    spec = ELIGIBILITY[kind]
    allowed = set(spec) | {"type", "verify_note"}
    extra = set(attrs) - allowed
    if extra:
        raise ProfileInputError(f"attributes for {kind} do not take {', '.join(sorted(extra))}")
    for field, ((desc, ok), required) in spec.items():
        if field not in attrs:
            if required:
                raise ProfileInputError(f"attributes.{field} is required for {kind} ({desc})")
            continue
        if not ok(attrs[field]):
            raise ProfileInputError(f"attributes.{field} must be {desc}")
    if not any(f in attrs for f in spec):
        raise ProfileInputError(f"{kind} needs at least one of {', '.join(spec)}")


def validate(fields: dict, *, partial: bool = False) -> dict:
    """Checked copy of the writable fields. partial: only the fields given (an edit)."""
    unknown = set(fields) - set(ITEM_FIELDS)
    if unknown:
        raise ProfileInputError(f"unknown field(s): {', '.join(sorted(unknown))}")
    out = dict(fields)
    if not partial or "kind" in out:
        if out.get("kind") not in KINDS:
            raise ProfileInputError(f"kind must be one of {', '.join(KINDS)}")
    if not partial or "statement" in out:
        s = out.get("statement")
        if not isinstance(s, str) or not s.strip() or len(s) > 2000:
            raise ProfileInputError("statement must be 1-2000 characters")
        out["statement"] = s.strip()
    if "attributes" in out and not isinstance(out["attributes"], dict):
        raise ProfileInputError("attributes must be an object")
    for d in ("start_date", "end_date"):
        if out.get(d) is not None and not (isinstance(out[d], str) and _DATE.match(out[d])):
            raise ProfileInputError(f"{d} must be YYYY-MM or YYYY-MM-DD")
    if out.get("years") is not None and not (isinstance(out["years"], (int, float)) and not isinstance(out["years"], bool)
                                             and 0 <= out["years"] <= 60):
        raise ProfileInputError("years must be a number 0-60")
    if "source" in out and not (isinstance(out["source"], str) and 0 < len(out["source"]) <= 100):
        raise ProfileInputError("source must be 1-100 characters")
    return out


def _check_eligibility(conn, kind: str, attrs: dict, item_id: int | None) -> None:
    if kind != "eligibility":
        if "type" in attrs and attrs["type"] in ELIGIBILITY:
            raise ProfileInputError(f"attributes.type {attrs['type']!r} is an eligibility fact: use kind 'eligibility'")
        return
    _validate_eligibility(attrs)
    if attrs["type"] not in MULTIPLE_ALLOWED:
        other = conn.execute("SELECT id FROM experience_items WHERE kind = 'eligibility' AND json_extract(attributes, '$.type') = ? "
                             "AND id IS NOT ?", (attrs["type"], item_id)).fetchone()
        if other:
            raise Conflict(f"an eligibility fact of type {attrs['type']} already exists (item {other[0]}); edit it instead")


def _row(r) -> dict:
    return {"id": r[0], "kind": r[1], "statement": r[2], "attributes": json.loads(r[3]), "start_date": r[4],
            "end_date": r[5], "years": r[6], "status": r[7], "source": r[8], "verified_at_ms": r[9]}


_SELECT = ("SELECT id, kind, statement, attributes, start_date, end_date, years, status, source, verified_at_ms "
           "FROM experience_items")


def list_items(conn: sqlite3.Connection) -> list[dict]:
    return [_row(r) for r in conn.execute(_SELECT + " ORDER BY kind, id")]


def get_item(conn: sqlite3.Connection, item_id: int) -> dict:
    r = conn.execute(_SELECT + " WHERE id = ?", (item_id,)).fetchone()
    if r is None:
        raise NotFound(f"experience item {item_id}")
    return _row(r)


def create_item(conn: sqlite3.Connection, fields: dict) -> int:
    f = validate(fields)
    attrs = f.get("attributes") or {}
    _check_eligibility(conn, f["kind"], attrs, None)
    return conn.execute(
        "INSERT INTO experience_items (kind, statement, attributes, start_date, end_date, years, status, source) "
        "VALUES (?, ?, ?, ?, ?, ?, 'self_reported', ?) RETURNING id",
        (f["kind"], f["statement"], json.dumps(attrs), f.get("start_date"), f.get("end_date"), f.get("years"),
         f.get("source") or "user")).fetchone()[0]


def update_item(conn: sqlite3.Connection, item_id: int, fields: dict) -> dict:
    """Edit; returns {"verification_cleared": bool}. Changing what a verified item says clears it."""
    current = get_item(conn, item_id)
    f = validate(fields, partial=True)
    merged = {**current, **f}
    if "attributes" in f and current["attributes"].get("verify_note") and "verify_note" not in f["attributes"]:
        merged["attributes"] = {**f["attributes"], "verify_note": current["attributes"]["verify_note"]}
    _check_eligibility(conn, merged["kind"], merged["attributes"], item_id)
    changed = any(merged[k] != current[k] for k in CONTENT_FIELDS)
    clear = changed and current["status"] != "self_reported"
    conn.execute(
        "UPDATE experience_items SET kind = ?, statement = ?, attributes = ?, start_date = ?, end_date = ?, years = ?, source = ? "
        "WHERE id = ?",
        (merged["kind"], merged["statement"], json.dumps(merged["attributes"]), merged["start_date"], merged["end_date"],
         merged["years"], merged["source"], item_id))
    if clear:
        unverify_item(conn, item_id)
    return {"verification_cleared": clear}


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    get_item(conn, item_id)
    if conn.execute("SELECT 1 FROM inferred_claims WHERE accepted_as_item_id = ?", (item_id,)).fetchone():
        raise Conflict(f"item {item_id} was accepted from an inferred claim, which keeps a link to it; edit the item instead")
    conn.execute("DELETE FROM experience_items WHERE id = ?", (item_id,))


def verify_item(conn: sqlite3.Connection, item_id: int, note: str | None, now: int) -> None:
    """The one place an item becomes verified: Kevin's explicit confirmation (Q6), optional note."""
    item = get_item(conn, item_id)
    if note is not None and not (isinstance(note, str) and len(note) <= 500):
        raise ProfileInputError("note must be at most 500 characters")
    attrs = dict(item["attributes"])
    if note:
        attrs["verify_note"] = note
    conn.execute("UPDATE experience_items SET status = 'verified', verified_at_ms = ?, attributes = ? WHERE id = ?",
                 (now, json.dumps(attrs), item_id))


def unverify_item(conn: sqlite3.Connection, item_id: int) -> None:
    get_item(conn, item_id)
    conn.execute("UPDATE experience_items SET status = 'self_reported', verified_at_ms = NULL WHERE id = ?", (item_id,))


# ----- completeness -----

COMPLETENESS_FIELDS = ("cert_list_complete", "eligibility_facts_complete")


def get_completeness(conn: sqlite3.Connection) -> dict:
    r = conn.execute("SELECT cert_list_complete, eligibility_facts_complete, updated_at_ms FROM profile_completeness WHERE id = 1").fetchone()
    if r is None:
        return {"cert_list_complete": False, "eligibility_facts_complete": False, "updated_at_ms": None}
    return {"cert_list_complete": bool(r[0]), "eligibility_facts_complete": bool(r[1]), "updated_at_ms": r[2]}


def set_completeness(conn: sqlite3.Connection, fields: dict, now: int) -> dict:
    """A flag set means "if it is not in my profile, I do not have it": a missing cert can then be
    read as a gap instead of unknown (P1.7). Unset (the default), absence stays unknown."""
    unknown = set(fields) - set(COMPLETENESS_FIELDS)
    if unknown or not fields:
        raise ProfileInputError(f"give one or more of {', '.join(COMPLETENESS_FIELDS)}")
    for k, v in fields.items():
        if not isinstance(v, bool):
            raise ProfileInputError(f"{k} must be true or false")
    cur = get_completeness(conn)
    new = {k: fields.get(k, cur[k]) for k in COMPLETENESS_FIELDS}
    conn.execute(
        "INSERT INTO profile_completeness (id, cert_list_complete, eligibility_facts_complete, updated_at_ms) VALUES (1, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET cert_list_complete = excluded.cert_list_complete, "
        "eligibility_facts_complete = excluded.eligibility_facts_complete, updated_at_ms = excluded.updated_at_ms",
        (int(new["cert_list_complete"]), int(new["eligibility_facts_complete"]), now))
    return get_completeness(conn)


# ----- inferred-claims inbox -----

def list_claims(conn: sqlite3.Connection, state: str | None) -> list[dict]:
    if state is not None and state not in ("open", "accepted", "rejected"):
        raise ProfileInputError("state must be open, accepted or rejected")
    rows = conn.execute("SELECT id, statement, kind, rationale, evidence, proposed_by_evaluation_id, state, accepted_as_item_id, "
                        "created_at_ms, proposed_by_resume_id, proposed_fields FROM inferred_claims "
                        "WHERE ? IS NULL OR state = ? ORDER BY id", (state, state))
    return [{"id": r[0], "statement": r[1], "kind": r[2], "rationale": r[3], "evidence": json.loads(r[4]),
             "proposed_by_evaluation_id": r[5], "state": r[6], "accepted_as_item_id": r[7], "created_at_ms": r[8],
             "proposed_by_resume_id": r[9], "proposed_fields": json.loads(r[10])} for r in rows]


def _open_claim(conn, claim_id: int):
    r = conn.execute("SELECT statement, kind, state, proposed_by_resume_id, proposed_fields FROM inferred_claims WHERE id = ?",
                     (claim_id,)).fetchone()
    if r is None:
        raise NotFound(f"inferred claim {claim_id}")
    if r[2] != "open":
        raise Conflict(f"inferred claim {claim_id} is already {r[2]}")
    return r


def accept_claim(conn: sqlite3.Connection, claim_id: int, edits: dict) -> int:
    """Accept, optionally reworded: becomes a self-reported item (never verified by accepting).
    A suggestion read from a resume brings its dates (proposed_fields) and is sourced to that resume.
    One that replaces a role already in the profile (evidence.replaces_item_id, suggest.store) updates
    that item, so the role is not listed twice; its verification is cleared, since the text changed."""
    statement, kind, _, resume_id, proposed = _open_claim(conn, claim_id)
    fields = {"kind": kind if kind in KINDS and kind != "eligibility" else "skill", "statement": statement,
              **json.loads(proposed), **edits,
              "source": f"resume:{resume_id}:claim:{claim_id}" if resume_id is not None else f"inferred_claim:{claim_id}"}
    if fields["kind"] == "eligibility":
        raise ProfileInputError("eligibility facts are entered directly, not accepted from inferences")
    evidence = json.loads(conn.execute("SELECT evidence FROM inferred_claims WHERE id = ?", (claim_id,)).fetchone()[0])
    replaces = evidence.get("replaces_item_id") if isinstance(evidence, dict) else None  # the column defaults to []
    if replaces is not None and conn.execute("SELECT 1 FROM experience_items WHERE id = ? AND kind = 'role'",
                                             (replaces,)).fetchone():
        update_item(conn, replaces, fields)
        item_id = replaces
    else:  # nothing to replace, or that item was deleted since: added as a new one
        item_id = create_item(conn, fields)
    conn.execute("UPDATE inferred_claims SET state = 'accepted', accepted_as_item_id = ? WHERE id = ?", (item_id, claim_id))
    return item_id


def reject_claim(conn: sqlite3.Connection, claim_id: int) -> None:
    _open_claim(conn, claim_id)
    conn.execute("UPDATE inferred_claims SET state = 'rejected' WHERE id = ?", (claim_id,))
