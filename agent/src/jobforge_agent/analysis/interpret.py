"""E2: check the model's reading of each requirement against the posting (ARCHITECTURE 6.4; D6, D8, D15).

A found quote is not a correct reading. For every E1 item this module locates the quote, reads
the surrounding text with code (section, negation, modality, equivalence, subject, eligibility
type) and stores that reading beside the model's. Where they agree the requirement is
`confirmed`; where they disagree it is `conflict` and the more permissive reading decides the
class (a possible preferred is not treated as required). A quote that is not in the text is
`unverifiable` and never `hard`. Then the eligibility recall scan turns every eligibility-cue
sentence the model did not cover into an `unextracted_eligibility` row.

Only code decides `final_class`; `hard` needs a code-assigned eligibility type, candidate
subject, affirmative polarity, required modality, no equivalence clause and, for a clearance,
no "ability to obtain".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobforge_agent.analysis import cues
from jobforge_agent.analysis.segment import Segmented, Sentence, norm, norm_quote

# Sections whose text is about the company, not the candidate.
CONTEXT_SECTIONS = {"about": "company", "benefits": "benefit", "eeo": "company", "privacy": "company"}
# Legal boilerplate: its eligibility words ("regardless of citizenship", "work eligibility" in a
# privacy notice) never condition the candidate. Only these are skipped by the recall scan and
# override an eligibility cue. Benefits and about text are NOT exempt: real postings put
# residency rules there (P1.6 live: Company U's state list under "Compensation", Company Y's "must be
# based in the US" under "Benefits:").
LEGAL_SECTIONS = {"eeo", "privacy"}
RECALL_EXCLUDED = LEGAL_SECTIONS
SECTION_MODALITY = {"requirements": "required", "preferred": "preferred"}
# Most requirement-like first: used when one quote occurs in several sections that agree.
SECTION_RANK = ["requirements", "preferred", "responsibilities", "unknown", "about", "benefits", "eeo", "privacy"]
_CLAUSE = re.compile(r";|\s(?:but|however|although|though|whereas)\b|,\s(?:while|but)\b")
MIN_QUOTE = 3


@dataclass(frozen=True)
class ModelItem:
    """One E1 requirement as the model returned it (already schema-validated)."""
    text: str
    jd_quote: str
    importance: str          # hard / required / preferred
    polarity: str            # affirmative / negated
    subject: str             # candidate / company / team / benefit
    eligibility_type: str    # one of cues.ELIGIBILITY_TYPES or "none"
    min_years: float | None


@dataclass
class Requirement:
    """One `requirements` row."""
    text: str
    jd_quote: str
    quote_found: bool
    quote_occurrences: int
    section_label: str | None
    sentence: str | None
    model_importance: str | None
    model_polarity: str | None
    model_subject: str | None
    model_eligibility_type: str | None
    eligibility_type: str | None
    min_years: float | None
    cue_importance: str | None
    cue_polarity: str | None
    cue_subject: str | None
    final_class: str
    interpretation_status: str
    origin: str = "model"
    flags: list[dict] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)  # quote occurrences in Segmented.text (not stored)


@dataclass
class _Reading:
    section: str
    sentence: str
    cue_importance: str | None
    cue_polarity: str
    cue_subject: str
    eligibility_type: str | None
    min_years: float | None
    final_class: str
    status: str
    flags: list[dict]


def _scope(text: str, sent_start: int, sent_end: int, q_start: int, q_end: int) -> str:
    """The clause(s) of the sentence that the quote overlaps: negation and modality are read
    there, so "Clearance not required, but must be able to obtain a Secret clearance" reads the
    second clause as affirmative."""
    bounds =[sent_start] + [m.start() for m in _CLAUSE.finditer(text, sent_start, sent_end)] + [sent_end]
    clauses = list(zip(bounds, bounds[1:]))
    hit = [(a, b) for a, b in clauses if a < q_end and q_start < b] or clauses
    return text[min(hit[0][0], q_start):max(hit[-1][1], q_end)]


def _subject(sentence: str, section: str, has_eligibility: bool) -> tuple[str, list[dict]]:
    if section in LEGAL_SECTIONS:
        return CONTEXT_SECTIONS[section], [{"flag": "context_section", "section": section}]
    if cues.EEO.search(sentence):
        return "company", [{"flag": "eeo_text"}]
    if cues.PRIVACY.search(sentence):
        return "company", [{"flag": "privacy_text"}]
    if section in CONTEXT_SECTIONS and not has_eligibility:
        return CONTEXT_SECTIONS[section], [{"flag": "context_section", "section": section}]
    if section in CONTEXT_SECTIONS:
        flag = [{"flag": "eligibility_in_context_section", "section": section}]
        return "candidate", flag
    if cues.OTHERS_YEARS.search(sentence):
        return "team", [{"flag": "years_about_others"}]
    # "We are unable to sponsor visas" is about the company's policy but conditions the
    # candidate, so eligibility cues keep the candidate subject.
    if cues.COMPANY_SUBJECT.match(sentence) and not cues.CANDIDATE_LEAD_IN.match(sentence) and not has_eligibility:
        return "company", [{"flag": "company_statement"}]
    return "candidate", []


def _cue_modality(scope: str, section: str, negated: bool) -> tuple[str | None, bool, list[dict]]:
    """(cue importance, internal conflict, flags). Wording beats a section heading when it
    downgrades ("X is a plus" under Qualifications); a "must" inside a Preferred section is a
    conflict (ARCHITECTURE 6.4 check 3, as corrected in P1.6)."""
    flags: list[dict] = []
    section_mod = SECTION_MODALITY.get(section)
    if negated:
        return None, False, flags
    plain = cues.strip_parentheticals(scope)
    req, pref = bool(cues.REQUIRED.search(plain)), bool(cues.PREFERRED.search(plain))
    wording = None
    if req and pref:
        flags.append({"flag": "modality_mixed"})
    elif req:
        wording = "required"
    elif pref:
        wording = "preferred"
    if wording == "required" and section_mod == "preferred":
        flags.append({"flag": "required_wording_in_preferred_section"})
        return "preferred", True, flags
    if wording == "preferred" and section_mod == "required":
        flags.append({"flag": "wording_overrides_section", "section": section})
        return "preferred", False, flags
    return wording or section_mod, False, flags


def _read(seg: Segmented, item: ModelItem, quote: str, start: int, end: int, lead: str = "") -> _Reading:
    sents = seg.sentences_overlapping(start, end)
    body = [s for s in sents if not s.heading] or sents
    section = body[0].label
    sentence = " ".join(s.text for s in body)
    scope = _scope(seg.text, body[0].start, body[-1].end, start, end)
    if not lead and body[0].lead is not None and seg.sentences[body[0].lead].start < start:
        # A bullet under "Familiarity with several of the following is preferred:" carries
        # the lead-in's wording (P1.6 live: Company Z, 18 bullets).
        lead = seg.sentences[body[0].lead].text
    if lead:  # "Must have experience with:" + a bullet: the lead-in's wording counts too
        scope = f"{lead} {scope}"
    flags: list[dict] = []
    conflicts: list[dict] = []

    # Check 2: negation.
    negated = bool(cues.NEGATION.search(scope)) and not cues.NEGATION_EXCEPT.search(scope)
    cue_polarity = "negated" if negated else "affirmative"
    if cue_polarity != item.polarity:
        conflicts.append({"field": "polarity", "model": item.polarity, "cue": cue_polarity})
    is_negated = negated or item.polarity == "negated"

    # Check 6: eligibility type from the lexicon over the quote; the model's type is a hint.
    types = cues.eligibility_types(quote)
    model_type = None if item.eligibility_type == "none" else item.eligibility_type
    if types:
        elig = model_type if model_type in types else types[0]
        if model_type is None:
            conflicts.append({"field": "eligibility_type", "model": item.eligibility_type, "cue": types})
        elif model_type not in types:
            # interp-v8: both readings call it a condition and only the kind differs; the wording shows the code's kind
            # and none of the model's, so the code's is checked ("based in India", model work_authorization). Before,
            # the conflict made eligibility UNKNOWN whatever the facts said. A model reading of "no condition" is
            # still a conflict.
            flags.append({"flag": "model_type_without_cue", "model": model_type, "cue": types})
    else:
        elig = None
        if model_type:
            flags.append({"flag": "model_eligibility_hint_only", "model": model_type})

    # Check 4: subject.
    cue_subject, subj_flags = _subject(sentence, section, bool(types))
    flags += subj_flags
    model_candidate, cue_candidate = item.subject == "candidate", cue_subject == "candidate"
    candidate = model_candidate and cue_candidate
    if model_candidate != cue_candidate:
        conflicts.append({"field": "subject", "model": item.subject, "cue": cue_subject})
        if elig and section not in RECALL_EXCLUDED and not any(f["flag"] in ("eeo_text", "privacy_text") for f in subj_flags):
            # An eligibility condition is not waved away as context on a disputed subject: it
            # stays a requirement whose conflict status makes eligibility UNKNOWN, never a
            # block. EEO and privacy text are excluded, the same text the recall scan skips.
            candidate = True
            flags.append({"flag": "subject_conflict_kept_for_eligibility"})

    # Check 3: importance (section + wording) and equivalence.
    cue_importance, internal, mod_flags = _cue_modality(scope, section, negated)
    flags += mod_flags
    model_imp = "preferred" if item.importance == "preferred" else "required"
    if internal:
        conflicts.append({"field": "importance", "model": item.importance, "cue": "section preferred, wording required"})
        importance = "preferred"
    elif cue_importance and cue_importance != model_imp:
        conflicts.append({"field": "importance", "model": item.importance, "cue": cue_importance})
        importance = "preferred"
    else:
        importance = model_imp
    equivalence = bool(cues.EQUIVALENCE.search(scope))
    if equivalence:
        flags.append({"flag": "equivalence"})

    # Check 5: ability to obtain a clearance.
    ability = elig == "clearance" and bool(cues.ABILITY_TO_OBTAIN.search(scope))
    if ability:
        flags.append({"flag": "ability_to_obtain"})

    # Years: every branch is recorded; the column keeps the model's value when the text names it.
    found_years = cues.years(scope)
    min_years = item.min_years
    if found_years:
        flags.append({"flag": "years", "values": found_years})
        if item.min_years not in found_years:
            flags.append({"flag": "years_disagree", "model": item.min_years, "cue": found_years})
            min_years = min(found_years)

    # A duty of the role (interp-v4): a line under a duties heading that asks nothing the candidate must already have
    # and states no condition of the job. Context, not a requirement to match: GPT-5.6-Luna listed such lines as
    # requirements (2026-10-26: 97 of its 477 lines on 20 postings), each one more "not in your profile".
    duty = (candidate and section == "responsibilities" and not types and not found_years
            and not cues.ASKS_OF_CANDIDATE.search(scope) and not cues.JOB_CONDITION.search(scope))
    # interp-v6: a duty done with named tools keeps them as a nice-to-have (the posting uses them, it does not state
    # them as required); set aside with the duty, an Azure administrator posting never asked for Azure (2026-10-27).
    tools = duty and bool(cues.TOOL_USE.search(scope)) and bool(cues.TOOL_NAME.search(item.jd_quote))
    if tools:
        duty = False
        flags.append({"flag": "duty_names_tools"})
    if duty:
        flags.append({"flag": "duty_by_section"})
    # interp-v7: a background check (interp-v8: a LinkedIn profile) is a step of the hiring process, context like a
    # duty. A clearance's background investigation keeps its eligibility type, and a line with years asks for
    # experience too.
    step = bool(cues.HIRING_STEP.search(quote)) and not types and not found_years
    if step:
        flags.append({"flag": "hiring_step"})
    # interp-v8: an attitude ("a desire to support students") and a work arrangement chosen by where you live ("fully
    # remote for candidates who reside outside the 30 mile radius") are context: no profile item or fact answers them.
    # A named technology keeps the line ("Interest in AI technologies such as Microsoft Copilot and Claude"): a profile
    # can show those, as with interp-v6's duty tools.
    attitude = (bool(cues.ATTITUDE.search(quote)) and not types and not found_years
                and not cues.TOOL_NAME.search(item.jd_quote))
    if attitude:
        flags.append({"flag": "attitude"})
    arrangement = location_arrangement(sentence)
    if arrangement:
        flags.append({"flag": "location_arrangement"})
    if not candidate or duty or step or attitude or arrangement:
        final = "context"
    elif is_negated:
        final = "negated"
    elif importance == "preferred" or tools:
        final = "preferred"
    elif elig and not equivalence and not ability:
        final = "hard"
        if elig == "clearance":
            flags.append({"flag": "clearance_held"})
    else:
        final = "negotiable"
    if item.importance == "hard" and not elig:
        flags.append({"flag": "model_hard_without_eligibility_type"})
    if not model_candidate and not cue_candidate and conflicts:
        # Both readings say this is not about the candidate; disagreeing on its importance or
        # polarity changes nothing, so it is not a conflict (P1.6 live: benefit lines).
        flags.append({"flag": "disagreement_ignored_for_context", "fields": conflicts})
        conflicts = []
    if conflicts:
        flags.append({"flag": "conflict", "fields": conflicts})
    return _Reading(section, sentence, cue_importance, cue_polarity, cue_subject, elig, min_years, final,
                    "conflict" if conflicts else "confirmed", flags)


def _model_only_class(item: ModelItem) -> str:
    """Class of an unverifiable item, from the model alone, never hard (it is excluded from
    assessments anyway; the class is for display)."""
    if item.subject != "candidate":
        return "context"
    if item.polarity == "negated":
        return "negated"
    return "preferred" if item.importance == "preferred" else "negotiable"


def _base(item: ModelItem, found: bool, occurrences: int) -> dict:
    return {"text": item.text, "jd_quote": item.jd_quote, "quote_found": found, "quote_occurrences": occurrences,
            "model_importance": item.importance, "model_polarity": item.polarity, "model_subject": item.subject,
            "model_eligibility_type": item.eligibility_type}


def _joined_quote(seg: Segmented, raw_quote: str) -> tuple[str, str, list[tuple[int, int]]] | None:
    """A quote made of a list lead-in and one of its bullets ("Demonstrated understanding of:" +
    "- Core networking concepts"). Gemma did this on 7 of 15 items of one real posting in P1.6
    despite the prompt rule, and gpt-oss in spike P1.0b. Located only if the lead-in is a line of
    its own and the bullet is under it: the nearest preceding line ending in ':' in the same
    section is that lead-in. Returns (lead, bullet, bullet spans)."""
    q = norm(raw_quote)
    i = q.find(": ")
    if i < MIN_QUOTE:
        return None
    lead, tail = q[:i + 1], norm_quote(q[i + 2:])
    if len(tail) < MIN_QUOTE:
        return None
    spans = []
    for m in re.finditer(re.escape(tail), seg.text):
        before = [s for s in seg.sentences if s.end <= m.start() and s.text.endswith(":")]
        here = seg.sentences_overlapping(m.start(), m.start() + len(tail))
        if before and here and before[-1].text == lead and before[-1].section == here[0].section:
            spans.append((m.start(), m.start() + len(tail)))
    return (lead, tail, spans) if spans else None


def interpret_item(seg: Segmented, item: ModelItem) -> Requirement:
    quote = norm_quote(item.jd_quote)
    starts = [m.start() for m in re.finditer(re.escape(quote), seg.text)] if len(quote) >= MIN_QUOTE else []
    spans = [(s, s + len(quote)) for s in starts]
    joined = None if starts else _joined_quote(seg, item.jd_quote)
    if joined:
        lead, tail, spans = joined
        r = _read(seg, item, f"{lead} {tail}", *spans[0], lead=lead)
        return Requirement(**_base(item, True, len(spans)), section_label=r.section, sentence=r.sentence,
                           eligibility_type=r.eligibility_type, min_years=r.min_years, cue_importance=r.cue_importance,
                           cue_polarity=r.cue_polarity, cue_subject=r.cue_subject, final_class=r.final_class,
                           interpretation_status=r.status, flags=r.flags + [{"flag": "heading_joined_quote", "lead": lead}],
                           spans=spans)
    if not starts:
        # Check 1: kept for display, excluded from assessments except as an uncertainty count.
        return Requirement(**_base(item, False, 0), section_label=None, sentence=None, eligibility_type=None,
                           min_years=item.min_years, cue_importance=None, cue_polarity=None, cue_subject=None,
                           final_class=_model_only_class(item), interpretation_status="unverifiable",
                           flags=[{"flag": "quote_not_found"}])
    readings = [_read(seg, item, quote, s, e) for s, e in spans]
    flags_extra: list[dict] = []
    # The same words under Requirements and under a duties heading: the duty reading (interp-v4) does not make the
    # location ambiguous; the line is asked of the candidate where it is listed as a requirement. Likewise a duty
    # whose tools are kept (interp-v6): its class there is only a nice-to-have.
    sections = [r.section for r in readings]
    asked = [r for r in readings if not any(f["flag"] in ("duty_by_section", "duty_names_tools") for f in r.flags)]
    if asked and len(asked) < len(readings):
        readings = asked
        flags_extra.append({"flag": "also_listed_as_duty"})
    chosen = readings[0]
    status = chosen.status
    if len(set(sections)) > 1:
        agree = len({(r.final_class, r.status, r.eligibility_type) for r in readings}) == 1
        flags_extra.append({"flag": "ambiguous_location", "sections": sections, "readings_agree": agree})
        if agree:
            chosen = min(readings, key=lambda r: SECTION_RANK.index(r.section))
            status = chosen.status
        else:
            status = "unverifiable"
    final = chosen.final_class if status != "unverifiable" else _model_only_class(item)
    return Requirement(**_base(item, True, len(starts)), section_label=chosen.section, sentence=chosen.sentence,
                       eligibility_type=chosen.eligibility_type, min_years=chosen.min_years,
                       cue_importance=chosen.cue_importance, cue_polarity=chosen.cue_polarity, cue_subject=chosen.cue_subject,
                       final_class=final, interpretation_status=status, flags=chosen.flags + flags_extra, spans=spans)


# Types the recall scan does not raise from unextracted sentences (interp-v3): hours and on-call wording
# in prose ("our ticketing, on-call and incident setup", "night shift bonus") is rarely a condition, and a
# recall row is a REVIEW question. A schedule the model extracted is still typed and checked.
RECALL_SKIPS = {"availability"}


def location_arrangement(text: str) -> bool:
    """interp-v8: where the candidate lives chooses remote or hybrid; nobody is excluded."""
    return bool(cues.LOCATION_ARRANGEMENT.search(text) and cues.ARRANGEMENT_MODE.search(text) and not cues.EXCLUDES.search(text))


def company_self(text: str) -> bool:
    """interp-v8: the company describing itself, requiring nothing of the candidate."""
    return bool(cues.COMPANY_SELF.search(text)) and not cues.REQUIRED.search(text)


def recall_scan(seg: Segmented, extracted: list[Requirement]) -> list[Requirement]:
    """Check 7 (D15): every eligibility-cue sentence outside EEO/privacy text whose types
    no extracted requirement covers becomes an `unextracted_eligibility` row (unverifiable), so a
    model miss is visible as a REVIEW question instead of a silent ELIGIBLE."""
    rows = []
    for s in seg.sentences:
        if s.heading or s.label in RECALL_EXCLUDED or cues.EEO.search(s.text) or cues.PRIVACY.search(s.text):
            continue
        if location_arrangement(s.text) or company_self(s.text):  # interp-v8
            continue
        types = [t for t in cues.eligibility_types(s.text) if t not in RECALL_SKIPS]
        if not types:
            continue
        covering = [r for r in extracted if r.quote_found and any(a < s.end and s.start < b for a, b in r.spans)]
        missing = [t for t in types if not any(r.eligibility_type == t for r in covering)]
        if missing:
            rows.append(_recall_row(seg, s, missing))
    return rows


def _recall_row(seg: Segmented, s: Sentence, missing: list[str]) -> Requirement:
    # The code's own reading of the sentence, recorded for the REVIEW question; P1.7 decides what
    # counts (a "No clearance required" sentence carries cue_polarity negated).
    item = ModelItem(s.text, s.text, "required", "affirmative", "candidate", missing[0], None)
    r = _read(seg, item, s.text, s.start, s.end)
    return Requirement(text=s.text, jd_quote=s.text, quote_found=True, quote_occurrences=1, section_label=s.label,
                       sentence=s.text, model_importance=None, model_polarity=None, model_subject=None,
                       model_eligibility_type=None, eligibility_type=missing[0], min_years=min(cues.years(s.text), default=None),
                       cue_importance=r.cue_importance, cue_polarity=r.cue_polarity, cue_subject=r.cue_subject,
                       final_class=r.final_class, interpretation_status="unverifiable", origin="unextracted_eligibility",
                       flags=[{"flag": "unextracted_eligibility", "types": missing}] + [f for f in r.flags if f["flag"] != "conflict"],
                       spans=[(s.start, s.end)])


MERGEABLE = ("negotiable", "preferred")


def merge_split_lines(seg: Segmented, rows: list[Requirement]) -> list[Requirement]:
    """interp-v5: parts of one posting sentence that a model listed separately ("Linux familiarity" and "basic GCP or
    AWS IAM" from "Linux familiarity and basic GCP or AWS IAM") become one requirement, as most models read them and
    as E3 judges a multi-part line (by its core, the rest in transfer_limits). GPT-5.6-Luna split 50 lines more than
    Gemma's 8 on 20 real postings (2026-10-27), each part one more line to match. Merged only when every part is a
    confirmed, unambiguous, same-class experience line with no eligibility type, no part but one names years, and no
    ';' separates them (a semicolon lists separate requirements). The merged quote is the parts with the posting's
    own text between them, so it stays a contiguous span (quote_found); the parts are kept in the flag."""
    def mergeable(r: Requirement) -> bool:
        return (r.origin == "model" and r.quote_found and r.quote_occurrences == 1 and len(r.spans) == 1
                and r.interpretation_status == "confirmed" and r.final_class in MERGEABLE and r.eligibility_type is None
                and not any(f["flag"] in ("heading_joined_quote", "duty_by_section") for f in r.flags))
    groups: dict[tuple[int, str], list[Requirement]] = {}
    for r in rows:
        if mergeable(r):
            sent = seg.sentences_overlapping(*r.spans[0])
            if len(sent) == 1:
                groups.setdefault((sent[0].index, r.final_class), []).append(r)
    drop: set[int] = set()
    merged: dict[int, Requirement] = {}
    for parts in groups.values():
        parts.sort(key=lambda r: r.spans[0])
        # The same span listed twice is one requirement, whatever else it names (Luna repeated a "3-5 years" line).
        seen: dict[tuple[int, int], Requirement] = {}
        for r in list(parts):
            if r.spans[0] in seen:
                drop.add(id(r))
                parts.remove(r)
            else:
                seen[r.spans[0]] = r
        runs, run = [], [parts[0]]
        for r in parts[1:]:
            gap = seg.text[run[-1].spans[0][1]:r.spans[0][0]]
            if ";" in gap:
                runs.append(run)
                run = [r]
            else:
                run.append(r)
        runs.append(run)
        for run in runs:
            if len(run) < 2 or sum(r.min_years is not None for r in run) > 1:
                continue
            quote, end = run[0].jd_quote, run[0].spans[0][1]
            for r in run[1:]:
                start, stop = r.spans[0]
                if start >= end:  # a later part; one that overlaps (a duplicate) adds nothing
                    quote += seg.text[end:start] + r.jd_quote
                    end = stop
                elif stop > end:
                    quote += seg.text[end:stop]
                    end = stop
            first = run[0]
            years = next((r.min_years for r in run if r.min_years is not None), None)
            merged[id(first)] = Requirement(
                **{**first.__dict__, "text": "; ".join(dict.fromkeys(r.text for r in run)), "jd_quote": quote,
                   "min_years": years, "spans": [(first.spans[0][0], end)],
                   "flags": first.flags + [{"flag": "merged_split_line", "parts": [r.jd_quote for r in run]}]})
            drop.update(id(r) for r in run[1:])
    return [merged.get(id(r), r) for r in rows if id(r) not in drop]


def interpret(seg: Segmented, items: list[ModelItem]) -> list[Requirement]:
    extracted = merge_split_lines(seg, [interpret_item(seg, it) for it in items])
    return extracted + recall_scan(seg, extracted)
