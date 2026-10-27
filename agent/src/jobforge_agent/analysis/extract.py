"""E1: the extraction prompt and its output schema (ARCHITECTURE 6.4, 6.8). Prompt version e1-v2.

e1-v2 (2026-10-27): subject "duty" for the role's tasks, and one item per bullet (no splitting). GPT-5.6-Luna
listed duties as candidate requirements (477 lines on 20 postings against Gemma's 313), each one a line to match.
Asking models to leave duties out dropped conditions of the job worded as tasks ("support a 24x7x365 operation")
and real skills (measured twice); labelling them keeps every line and lets code decide (interpret._read).

Derived from spike prompt e1-draft-2 (spikes/p1_0/extract_spike.py), which Gemma 4 12B answered
with valid JSON on 6/6 postings and verbatim quotes on 97/97 items. Changes from the draft, each
from a spike finding (ARCHITECTURE Appendix A):
- `program_status` added to eligibility_type (no value fit "active-duty SkillBridge participant");
- quote the bullet or sentence itself, never a list heading joined to it (gpt-oss joined
  headings on 7 of 91 items, which then fail quote location);
- the model's eligibility_type and importance are hints: E2 decides the class, so the prompt
  does not need to be perfect, only consistent.

Changing SYSTEM, SCHEMA or the user message format changes behavior: bump
EXTRACTION_PROMPT_VERSION (analysis/versions.py) so new analyses are created.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobforge_agent.analysis.interpret import ModelItem

IMPORTANCE = ("hard", "required", "preferred")
# Never "required" here: sharing it with importance made three models label ~95% of items
# negated (spike P1.0b draft 1). The schema lint test pins "no enum value in two fields".
POLARITY = ("affirmative", "negated")
SUBJECT = ("candidate", "duty", "company", "team", "benefit")
# The model's type list is fixed with prompt e1-v1, not taken from the E2 lexicon: E2 can learn a type
# (availability, interp-v3) without changing the E1 schema, its pinned hash, or re-running E1 on every
# posting. The model never names the newer types; E2 assigns them from the text.
E1_ELIGIBILITY_TYPES = ("work_authorization", "sponsorship", "clearance", "residency", "physical", "travel", "license",
                        "program_status")
ELIGIBILITY = ("none", *E1_ELIGIBILITY_TYPES)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirements"],
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "jd_quote", "importance", "polarity", "subject", "eligibility_type", "min_years"],
                "properties": {
                    "text": {"type": "string"},
                    "jd_quote": {"type": "string"},
                    "importance": {"type": "string", "enum": list(IMPORTANCE)},
                    "polarity": {"type": "string", "enum": list(POLARITY)},
                    "subject": {"type": "string", "enum": list(SUBJECT)},
                    "eligibility_type": {"type": "string", "enum": list(ELIGIBILITY)},
                    "min_years": {"type": ["number", "null"]},
                },
            },
        }
    },
}

SYSTEM = """You extract candidate requirements from a job posting.
Rules:
- One item per distinct requirement or qualification the posting states about the CANDIDATE, and one per duty of the role (labelled "duty", below).
- One item per bullet or sentence: when a line lists several skills ("Linux familiarity and basic GCP or AWS IAM"), keep it as ONE item; do not split it.
- jd_quote: copy a short contiguous span EXACTLY as it appears in the posting (same words, same order). Do not paraphrase, merge, or fix typos. Quote the bullet or sentence itself; never join a list heading or lead-in line to it.
- importance: "hard" only for eligibility conditions stated as mandatory (work authorization, sponsorship, holding a clearance, a legally required license, where you must live, travel or physical demands, a required program status such as active duty or enrolled student). "required" for other must-haves. "preferred" for nice-to-haves, pluses, bonuses.
- polarity: "affirmative" for anything the posting asks for or prefers; "negated" only when the posting says something is NOT required (e.g. "no degree required").
- subject: "candidate" for what the applicant must already have, know, be or accept (experience, skills, knowledge, certifications, education, and conditions such as location, hours, shifts, on-call, travel, authorization); "duty" for a task the person will perform in the role ("Administer Azure Virtual Desktop", "Escalate issues to tier 2", "Maintain documentation"), wherever it appears; "company"/"team" for statements describing the company, product, customers, or stack; "benefit" for perks.
- eligibility_type: the eligibility category, or "none".
- min_years: minimum years of experience if stated for this item, else null.
Return JSON only."""


def user_message(title: str, text: str) -> str:
    return f"Job title: {title}\n\nPosting:\n{text}"


def messages(title: str, text: str, structured_output_mode: str) -> list[dict]:
    system = SYSTEM
    if structured_output_mode == "prompt_only":
        system += "\nThe JSON must match this schema:\n" + json.dumps(SCHEMA)
    return [{"role": "system", "content": system}, {"role": "user", "content": user_message(title, text)}]


def response_format(structured_output_mode: str) -> dict | None:
    if structured_output_mode == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": "e1", "strict": True, "schema": SCHEMA}}
    if structured_output_mode == "json_object":
        return {"type": "json_object"}
    return None


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    jd_quote: str
    importance: Literal["hard", "required", "preferred"]
    polarity: Literal["affirmative", "negated"]
    subject: Literal["candidate", "duty", "company", "team", "benefit"]
    eligibility_type: Literal[ELIGIBILITY]  # type: ignore[valid-type]
    min_years: float | None


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[_Item]


class InvalidOutput(ValueError):
    pass


def parse(content: str) -> list[ModelItem]:
    """Validate the model's JSON (always, whatever the server's structured-output mode claims)."""
    try:
        out = _Output.model_validate_json(content)
    except ValidationError as exc:
        raise InvalidOutput(f"{exc.error_count()} schema errors, first: {exc.errors()[0]['type']} at "
                            f"{'.'.join(str(p) for p in exc.errors()[0]['loc'])}") from None
    return [ModelItem(i.text.strip(), i.jd_quote.strip(), i.importance, i.polarity, i.subject, i.eligibility_type, i.min_years)
            for i in out.requirements]
