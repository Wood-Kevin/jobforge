"""E3: the model matches posting requirements to profile items (ARCHITECTURE 6.3, 6.4; prompt m2-v2).

Input: the requirements E2 left for fit (negotiable or preferred, confirmed or conflict, no
eligibility type: those are matched by code in facts.py) and the snapshot's non-eligibility
items. Ids are prefixed (R1.. for requirements, P<item id> for items) so the model cannot mix the
two lists up. The model proposes; E4 (validate.py) decides what stands: citations must be items of
the evaluated snapshot (FK), transferable needs a bridge, years and materiality are computed.

Inferred claims are experience the model thinks is likely but no item states. They go to the
inbox (`inferred_claims`) for Kevin to accept or reject and never cover a requirement (D2).
No enum value appears in two fields (schema lint, tests/test_evaluation.py).
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobforge_agent.evaluation.facts import Req
from jobforge_agent.evaluation.snapshot import Item

MATCH_TYPES = ("direct", "transferable", "gap", "unknown")
CLAIM_KINDS = ("role", "skill", "cert", "education", "achievement")
MAX_INFERRED_CLAIMS = 5

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matches", "inferred_claims"],
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requirement", "match_type", "cited_items", "bridge", "transfer_limits", "gap_detail"],
                "properties": {
                    "requirement": {"type": "string", "pattern": "^R[0-9]+$"},
                    "match_type": {"type": "string", "enum": list(MATCH_TYPES)},
                    "cited_items": {"type": "array", "items": {"type": "string", "pattern": "^P[0-9]+$"}},
                    "bridge": {"type": ["string", "null"]},
                    "transfer_limits": {"type": ["string", "null"]},
                    "gap_detail": {"type": ["string", "null"]},
                },
            },
        },
        "inferred_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "kind", "rationale", "requirements"],
                "properties": {
                    "statement": {"type": "string"},
                    "kind": {"type": "string", "enum": list(CLAIM_KINDS)},
                    "rationale": {"type": "string"},
                    "requirements": {"type": "array", "items": {"type": "string", "pattern": "^R[0-9]+$"}},
                },
            },
        },
    },
}

SYSTEM = """You compare a job posting's requirements with a candidate's profile. You do not decide whether the candidate should apply; code does that from your answer.

For EVERY requirement R<n> return exactly one match:
- "direct": one or more profile items P<id> show this experience itself, or the same thing under another name (the posting's "or similar" counts). Cite them.
- "transferable": cited items show the SAME task or responsibility, done with a different tool or in a different setting, so the candidate would only need to learn the new tool or setting. Cite them, say in "bridge" which task is the same, and in "transfer_limits" what is new.
- "gap": the profile shows the experience is absent or too short (e.g. 2 years against 5). Say why in "gap_detail"; cite the items that show the shortfall, if any.
- "unknown": the profile says nothing either way. Absence from the profile is NOT a gap unless the profile clearly covers the area and lacks this.

What is NOT transferable (answer "unknown", or "gap" when the profile clearly covers the area and lacks it):
- Working near the area, alongside people who do it, or with something that touches it. Running backups is not designing a disaster recovery program; using Linux is not kernel development.
- A broader or narrower skill than the one asked. General troubleshooting is not a named protocol or language; following a process is not running or improving it.
- A requirement that names a specific technology, protocol, language, framework or practice that no item states, or states an equivalent of.
When unsure between "transferable" and "unknown", answer "unknown".

Requirements that list several things ("A and B, including C and D"): judge the core of the requirement, which is usually what comes first. If items show the core, answer "direct" (or "transferable" by the rule above) and name the parts the profile does not show in "transfer_limits"; do not answer "gap" or "unknown" because a listed detail is missing. If items do not show the core, judge the requirement as a whole by the rules above.

Rules:
- Cite ONLY ids from the PROFILE list, exactly as written (P12). Never cite a requirement id. Never invent an id.
- Use only what an item states. Do not assume experience that no item states.
- Years: code adds up the dates and years of the items you cite. For a requirement with a minimum number of years, cite EVERY item that shows this experience, not only the most recent role. Do not cite an item that does not show it.
- Keep "bridge", "transfer_limits" and "gap_detail" to one short sentence each; null when not used.
- "inferred_claims" (optional, at most 5): experience that is probably true given the profile but not stated by any item, e.g. a role that almost certainly involved a tool. These are suggestions for the candidate to confirm; they never count as a match. Return [] if none.

Return only JSON matching the schema."""


def user_message(title: str, reqs: list[Req], items: list[Item]) -> tuple[str, dict[str, int]]:
    """(prompt text, R-label -> requirement id)."""
    labels = {f"R{n}": r.id for n, r in enumerate(reqs, 1)}
    lines = [f"JOB TITLE: {title}", "", "REQUIREMENTS:"]
    for label, r in zip(labels, reqs):
        extra = [r.final_class]
        if r.min_years is not None:
            extra.append(f"min {r.min_years:g} years")
        # The located quote, not the E1 paraphrase: it is the posting's own wording.
        lines.append(f"{label} [{', '.join(extra)}] {r.jd_quote or r.text}")
    lines += ["", "PROFILE:"]
    for i in items:
        when = []
        if i.start_date or i.end_date:
            when.append(f"{i.start_date or '?'} to {i.end_date or 'present'}")
        if i.years is not None:
            when.append(f"{i.years:g} years")
        attrs = {k: v for k, v in i.attributes.items() if k != "verify_note"}
        lines.append(f"P{i.id} [{i.kind}{'; ' + ', '.join(when) if when else ''}] {i.statement}"
                     + (f" {json.dumps(attrs, sort_keys=True)}" if attrs else ""))
    return "\n".join(lines), labels


def messages(title: str, reqs: list[Req], items: list[Item], mode: str) -> tuple[list[dict], dict[str, int]]:
    text, labels = user_message(title, reqs, items)
    system = SYSTEM
    if mode != "json_schema":
        system += "\n\nJSON schema:\n" + json.dumps(SCHEMA)
    return [{"role": "system", "content": system}, {"role": "user", "content": text}], labels


def response_format(mode: str) -> dict | None:
    if mode == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": "matches", "strict": True, "schema": SCHEMA}}
    if mode == "json_object":
        return {"type": "json_object"}
    return None


class _Match(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement: str = Field(pattern=r"^R[0-9]+$")
    match_type: str
    cited_items: list[str]
    bridge: str | None
    transfer_limits: str | None
    gap_detail: str | None


class _Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    kind: str
    rationale: str
    requirements: list[str]


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matches: list[_Match]
    inferred_claims: list[_Claim]


class InvalidOutput(ValueError):
    pass


_ITEM = re.compile(r"^P([0-9]+)$")


def parse(content: str) -> _Output:
    """Schema-validate whatever the server returned, whatever its structured-output mode claims."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?\s*|\s*```$", "", text)
    try:
        out = _Output.model_validate_json(text)
    except ValidationError as exc:
        raise InvalidOutput(f"schema: {exc.error_count()} error(s): {exc.errors()[0]['msg']}") from None
    for m in out.matches:
        if m.match_type not in MATCH_TYPES:
            raise InvalidOutput(f"match_type {m.match_type!r}")
        bad = [c for c in m.cited_items if not _ITEM.match(c)]
        if bad:
            raise InvalidOutput(f"cited item id {bad[0]!r} is not P<number>")
    for c in out.inferred_claims:
        if c.kind not in CLAIM_KINDS:
            raise InvalidOutput(f"inferred claim kind {c.kind!r}")
    return out


def item_id(label: str) -> int:
    return int(_ITEM.match(label).group(1))
