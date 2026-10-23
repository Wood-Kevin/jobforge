"""Profile items suggested from an uploaded resume (P1.8c; prompt `RESUME_PROMPT_VERSION`).

The model reads the resume's extracted text and proposes items (role, skill, cert, education,
achievement), each with a quote copied from the resume. Code decides what stands:
- the quote must be found in the resume text (compared word by word, ignoring case, punctuation
  and line breaks), otherwise the suggestion is dropped and counted, never stored;
- dates must be YYYY-MM; a year-only date is not padded to a month (that would invent precision),
  it is kept as written in `attributes.dates_as_written` and both dates stay empty, because an
  empty end date means "present" to E3;
- a suggestion whose statement is already an item or an open/rejected suggestion is skipped.
What stands goes to the `inferred_claims` inbox, unconfirmed. Accepting one makes a self-reported
item; confirming it is a separate click (SPEC D2, Q6). Eligibility facts (citizenship, clearance,
work authorization) are not suggested: they are typed facts entered in their own form.

Rejected alternative: writing suggestions straight into `experience_items` as self-reported.
Faster for the user, but model output would then be part of the profile E3 matches against
without anyone having read it.
"""
from __future__ import annotations

import json
import re
import sqlite3

from pydantic import BaseModel, ConfigDict, ValidationError

# Pinned by a hash in tests/test_suggest.py: change the prompt or schema, bump this.
RESUME_PROMPT_VERSION = "r1-v1"
KINDS = ("role", "skill", "cert", "education", "achievement")
MAX_TEXT_CHARS = 30_000
MAX_SUGGESTIONS = 80
TIMEOUT_S = 300.0   # the host allows /suggest-items 330 s (desktop/src-tauri/src/lib.rs)
MAX_TOKENS = 8000

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "statement", "quote", "start_date", "end_date"],
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "statement": {"type": "string"},
                    "quote": {"type": "string"},
                    "start_date": {"type": ["string", "null"]},
                    "end_date": {"type": ["string", "null"]},
                },
            },
        },
    },
}

SYSTEM = """You turn a resume into a list of profile items for a job-matching tool. The candidate reviews every item before it is used.

Item kinds:
- "role": one job held. Statement: "<title> at <employer>" plus one short clause on the main duties.
- "skill": one tool, technology or ability the resume says the candidate used (e.g. "Active Directory user administration").
- "cert": one certification or license.
- "education": one degree, diploma or course of study.
- "achievement": one specific result the resume states (numbers kept as written).

Rules:
- Use only what the resume states. Do not add skills a role "probably" involved.
- "quote": copy the resume's own words that support the item, exactly as written (a few words to one sentence).
- Dates: "YYYY-MM" when the resume gives month and year; "YYYY" when it gives only the year; null when it gives none. For a current role, end_date is "present".
- For education, end_date is the completion (or expected completion) date; start_date is null unless the resume gives one.
- Only roles and education have dates; other kinds use null for both.
- Do not include citizenship, security clearance, work authorization or contact details; those are entered separately.
- List EVERY entry of a skills or technical-skills section as its own "skill" item, in the resume's order; do not pick a few.
- One item per role, skill, cert, degree or achievement; no duplicates.

Return only JSON matching the schema."""


def messages(resume_text: str, mode: str) -> list[dict]:
    system = SYSTEM
    if mode != "json_schema":
        system += "\n\nJSON schema:\n" + json.dumps(SCHEMA)
    return [{"role": "system", "content": system}, {"role": "user", "content": "RESUME:\n" + resume_text}]


def response_format(mode: str) -> dict | None:
    if mode == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": "resume_items", "strict": True, "schema": SCHEMA}}
    if mode == "json_object":
        return {"type": "json_object"}
    return None


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    statement: str
    quote: str
    start_date: str | None
    end_date: str | None


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[_Item]


class SuggestError(Exception):
    pass


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def norm_statement(text: str) -> str:
    return _words(text)


_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_YEAR = re.compile(r"^\d{4}$")
_PRESENT = {"present", "current", "now"}


def _dates(kind: str, start: str | None, end: str | None) -> dict:
    """Item fields for the dates the model read, never more precise than the resume."""
    if kind not in ("role", "education"):
        return {}
    s = (start or "").strip()
    e = (end or "").strip()
    if not s and not e:
        return {}
    end_ok = e.casefold() in _PRESENT or bool(_MONTH.match(e))
    if _MONTH.match(s) and end_ok:
        return {"start_date": s, "end_date": None if e.casefold() in _PRESENT else e}
    if not s and _MONTH.match(e):  # e.g. a degree's completion date
        return {"end_date": e}
    # Year-only or anything else: kept as written, no dates (an empty end date would read as "present").
    written = f"{s or '?'} to {e or '?'}"
    return {"attributes": {"dates_as_written": written[:60]}}


def parse(content: str, resume_text: str) -> tuple[list[dict], dict]:
    """(suggestions that stand, counts). Raises SuggestError if the answer is not the schema."""
    try:
        out = _Output.model_validate_json(content)
    except ValidationError as exc:
        raise SuggestError(f"the model's answer did not match the schema: {exc.errors()[0]['msg']}") from None
    words = _words(resume_text)
    kept, counts, seen = [], {"proposed": len(out.items), "quote_not_found": 0, "bad_kind": 0, "repeated": 0}, set()
    for it in out.items[:MAX_SUGGESTIONS]:
        if it.kind not in KINDS:
            counts["bad_kind"] += 1
            continue
        q = _words(it.quote)
        if not q or f" {q} " not in f" {words} ":
            counts["quote_not_found"] += 1
            continue
        key = norm_statement(it.statement)
        if not key or key in seen:
            counts["repeated"] += 1
            continue
        seen.add(key)
        kept.append({"kind": it.kind, "statement": it.statement.strip()[:500], "quote": it.quote.strip()[:500],
                     "fields": _dates(it.kind, it.start_date, it.end_date)})
    return kept, counts


def store(conn: sqlite3.Connection, resume_id: int, resume_sha256: str, suggestions: list[dict], now: int) -> dict:
    """Insert into the inbox (caller holds the transaction). Statements already in the profile or
    already suggested (open or rejected) are skipped, so a rerun does not bring back what was turned down."""
    known = {norm_statement(r[0]) for r in conn.execute("SELECT statement FROM experience_items")}
    known |= {norm_statement(r[0]) for r in conn.execute("SELECT statement FROM inferred_claims")}
    added = 0
    for s in suggestions:
        key = norm_statement(s["statement"])
        if key in known:
            continue
        known.add(key)
        conn.execute("INSERT INTO inferred_claims (statement, kind, rationale, evidence, proposed_by_resume_id, proposed_fields, "
                     "created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (s["statement"], s["kind"], None, json.dumps({"resume_id": resume_id, "resume_sha256": resume_sha256,
                                                                   "quote": s["quote"], "prompt_version": RESUME_PROMPT_VERSION}),
                      resume_id, json.dumps(s["fields"]), now))
        added += 1
    return {"added": added, "already_known": len(suggestions) - added}
