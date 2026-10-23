"""E5 assess and E6 recommend (ARCHITECTURE 6.2, 6.5; SPEC D1, D7, D15). Code only, no model.

Pure functions of (requirements, validated matches, job, search profile, verdict rules), so a
rule change recomputes every recommendation with zero inference calls (D10) and layer-B tests
need no database.

Three assessments, never combined into a number:
- Eligibility from hard requirements and eligibility recall rows, matched by code (facts.py).
  BLOCKED only by a confirmed hard requirement that a verified fact definitively fails. Any
  conflict, unanswered, self-reported or recall-row outcome is UNKNOWN, never BLOCKED.
- Fit over negotiable requirements; preferred ones are counted and shown, and do not move the
  label in v1 ("only upgrade or break ties" in 6.5; no upgrade rule is defined, so none is applied).
- Opportunity value from deterministic factors against the search profile.

Recommendation, first rule that matches (each recorded as the decisive reason):
1 BLOCKED -> SKIP; 2 WEAK -> SKIP; 3 UNKNOWN or INSUFFICIENT -> REVIEW (with questions);
4 STRONG/COMPETITIVE -> APPLY; 5 STRETCH + LOW -> SKIP; 6 STRETCH -> STRETCH.
SKIP therefore only comes from rules 1, 2 or 5 (property test, D7).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Callable
from importlib import resources

import yaml

from jobforge_agent.evaluation.facts import FactCheck, Req
from jobforge_agent.evaluation.validate import Validated
from jobforge_agent.filtering.rules import FilterOutcome
from jobforge_agent.filtering.salary import ANNUAL_MULTIPLIER

FIT_STATUSES = ("confirmed", "conflict")
OPPORTUNITY_RANK = {"HIGH": 3, "MEDIUM": 2, "UNKNOWN": 1, "LOW": 0}


@lru_cache(maxsize=8)
def load_rules(version: str) -> dict:
    text = (resources.files("jobforge_agent.evaluation") / "verdict_rules" / f"{version}.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


@dataclass
class JobFacts:
    """What opportunity value reads about the job (the filter's own reading of it)."""
    title: str
    posted_at: str | None
    outcome: FilterOutcome


@dataclass
class Assessment:
    eligibility: str
    eligibility_reasons: list[dict]
    fit: str
    fit_counts: dict
    opportunity: str
    opportunity_factors: list[dict]
    recommendation: str
    decisive_reasons: list[dict]
    rank_key: float


# ----- eligibility -----

def _conflict_fields(r: Req) -> list[dict]:
    for f in r.flags:
        if f.get("flag") == "conflict":
            return f.get("fields", [])
    return []


AltCheck = Callable[[Req, str], FactCheck]


def _type_only_conflict(r: Req) -> str | None:
    """The model's eligibility type when the type is the only disagreement, else None."""
    fields = _conflict_fields(r)
    if len(fields) == 1 and fields[0].get("field") == "eligibility_type":
        return fields[0].get("model") or "none"
    return None


def _check_both(r: Req, outcome: str, basis: str, alt_check: AltCheck | None) -> dict | None:
    """v2: a type-only conflict is answered when verified facts meet it under both readings."""
    model_type = _type_only_conflict(r)
    if model_type is None or alt_check is None or not (outcome == "met" and basis == "verified"):
        return None
    if model_type in ("none", r.eligibility_type):
        return {"model_type": model_type, "model_outcome": "no condition", "model_basis": None}
    alt = alt_check(r, model_type)
    if alt.outcome == "met" and alt.basis == "verified":
        return {"model_type": model_type, "model_outcome": alt.outcome, "model_basis": alt.basis}
    return None


def eligibility(reqs: list[Req], matches: dict[int, Validated], rules: dict | None = None,
                alt_check: AltCheck | None = None) -> tuple[str, list[dict]]:
    check_both = ((rules or {}).get("eligibility") or {}).get("type_only_conflict") == "check_both"
    reasons: list[dict] = []
    for r in reqs:
        recall = r.origin == "unextracted_eligibility"
        hard = r.origin == "model" and r.final_class == "hard" and r.interpretation_status in FIT_STATUSES
        if not (recall or hard):
            continue
        m = matches.get(r.id)
        check = next((f for f in (m.flags if m else []) if f.get("flag") == "eligibility_check"), {})
        outcome, basis = check.get("outcome", "unanswered"), (m.basis if m else "none")
        base = {"requirement_id": r.id, "quote": r.jd_quote, "type": r.eligibility_type, "status": r.interpretation_status,
                "origin": r.origin, "outcome": outcome, "basis": basis}
        question = check.get("question")
        if recall:
            if r.cue_polarity == "negated":
                reasons.append({**base, "effect": "ignored", "why": "the posting says it is not required"})
            elif outcome == "met" and basis == "verified":
                reasons.append({**base, "effect": "ok", "why": "a sentence the model did not extract, met by a verified fact"})
            else:
                # D15: a model miss is a question, never a block, whatever the fact says.
                reasons.append({**base, "effect": "unknown", "why": "eligibility sentence the model did not extract",
                                "question": question or f"Does this apply to you? \"{r.jd_quote[:160]}\""})
            continue
        both = _check_both(r, outcome, basis, alt_check) if check_both and r.interpretation_status == "conflict" else None
        if both is not None:
            reasons.append({**base, "effect": "ok", "readings": _conflict_fields(r), **both,
                            "why": "type-only conflict; met by verified facts under both readings (rules v2)"})
        elif r.interpretation_status == "conflict":
            reasons.append({**base, "effect": "unknown", "why": "model and code read this requirement differently",
                            "readings": _conflict_fields(r),
                            "question": f"Is this a requirement for you? \"{r.jd_quote[:160]}\""})
        elif outcome == "fails" and basis == "verified":
            reasons.append({**base, "effect": "blocks", "why": "a verified fact fails this requirement"})
        elif outcome == "met" and basis == "verified":
            reasons.append({**base, "effect": "ok", "why": "met by a verified fact"})
        elif outcome in ("met", "fails") and basis == "self_reported":
            reasons.append({**base, "effect": "unknown", "why": f"{outcome} only on a self-reported fact",
                            "question": f"Verify your {r.eligibility_type} fact: it decides \"{r.jd_quote[:120]}\""})
        else:
            reasons.append({**base, "effect": "unknown", "why": "the profile cannot answer this",
                            "question": question or f"Does this apply to you? \"{r.jd_quote[:160]}\""})
    effects = {x["effect"] for x in reasons}
    label = "BLOCKED" if "blocks" in effects else ("UNKNOWN" if "unknown" in effects else "ELIGIBLE")
    return label, reasons


# ----- qualification fit -----

def fit(reqs: list[Req], matches: dict[int, Validated], rules: dict) -> tuple[str, dict]:
    fr = rules["fit"]
    neg = [r for r in reqs if r.origin == "model" and r.final_class == "negotiable" and r.interpretation_status in FIT_STATUSES]
    pref = [r for r in reqs if r.origin == "model" and r.final_class == "preferred" and r.interpretation_status in FIT_STATUSES]
    unverifiable = [r for r in reqs if r.origin == "model" and r.interpretation_status == "unverifiable"
                    and r.final_class not in ("context", "negated")]

    def mt(r):
        m = matches.get(r.id)
        return m.match_type if m else "unknown"

    def evidenced(r) -> bool:
        # A gap is evidence only if something backs it: cited items (the shortfall or the adjacent
        # experience), a failed fact check, or absence confirmed by a complete cert list. A model
        # that says "candidate lacks it" and cites nothing is missing evidence, which D7 says never
        # counts as a gap (Codex adversarial review, P1.7: three such gaps made a rule-2 SKIP).
        m = matches[r.id]
        return bool(m.citations) or m.matched_by == "eligibility_fact" or m.has_flag("absence_confirmed")

    unevidenced = [r for r in neg if mt(r) == "gap" and not evidenced(r)]
    types = ["unknown" if r in unevidenced else mt(r) for r in neg]
    material = [r for r in neg if mt(r) == "gap" and r not in unevidenced and matches[r.id].gap_materiality == "material"]
    minor = [r for r in neg if mt(r) == "gap" and r not in unevidenced and matches[r.id].gap_materiality != "material"]
    unknown = [r for r in neg if mt(r) == "unknown" or r in unevidenced]
    judgeable = len(neg) - len(unknown)
    conflicts = sum(r.interpretation_status == "conflict" for r in neg + pref)
    denom = len(neg) + len(unverifiable)
    uncertain_share = round((len(unknown) + len(unverifiable)) / denom, 3) if denom else 1.0
    counts = {
        "negotiable": len(neg), "judgeable": judgeable, "direct": types.count("direct"),
        "transferable": types.count("transferable"), "gap_material": len(material), "gap_minor": len(minor),
        "gap_unevidenced": len(unevidenced),
        "unknown": len(unknown), "unverifiable": len(unverifiable), "conflicts": conflicts,
        "uncertain_share": uncertain_share, "max_conflicts": fr["max_conflicts"],
        "self_reported_covering": sum(1 for r in neg if mt(r) in ("direct", "transferable") and matches[r.id].basis == "self_reported"),
        "preferred": {"total": len(pref), **{t: [mt(r) for r in pref].count(t) for t in ("direct", "transferable", "gap", "unknown")}},
        "material_gaps": [{"requirement_id": r.id, "quote": r.jd_quote} for r in material],
        "unknown_requirements": [{"requirement_id": r.id, "quote": r.jd_quote} for r in unknown],
    }
    if judgeable < fr["min_judgeable"]:
        label, why = "INSUFFICIENT", f"{judgeable} judgeable negotiable requirements (< {fr['min_judgeable']})"
    elif uncertain_share > fr["max_uncertain_share"]:
        label, why = "INSUFFICIENT", f"unknown + unverifiable = {uncertain_share:.0%} of requirements (> {fr['max_uncertain_share']:.0%})"
    elif conflicts > fr["max_conflicts"]:
        label, why = "INSUFFICIENT", f"{conflicts} interpretation conflicts (> {fr['max_conflicts']})"
    elif len(material) > fr["max_stretch_gaps"]:
        label, why = "WEAK", f"{len(material)} material gaps (> {fr['max_stretch_gaps']})"
    elif material:
        if len(unknown) <= fr["stretch_max_unknown"]:
            label, why = "STRETCH", f"{len(material)} material gap(s), the rest covered"
        else:
            label, why = "INSUFFICIENT", f"{len(material)} material gap(s) and {len(unknown)} unknown (> {fr['stretch_max_unknown']})"
    elif counts["transferable"] == 0 and len(unknown) <= fr["strong_max_unknown"]:
        label, why = "STRONG", "no material gaps, every covered requirement direct"
    elif len(unknown) <= fr["competitive_max_unknown"]:
        label, why = "COMPETITIVE", "no material gaps"
    else:
        # Not in the 6.5 list: no gaps, but more unknowns than COMPETITIVE allows. Not enough to call.
        label, why = "INSUFFICIENT", f"no material gaps but {len(unknown)} unknown (> {fr['competitive_max_unknown']})"
    counts["rule"] = why
    return label, counts


# ----- opportunity value -----

def _days_old(posted_at: str | None, now_ms: int) -> int | None:
    if not posted_at:
        return None
    try:
        d = date.fromisoformat(posted_at[:10])
    except ValueError:
        return None
    return (datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date() - d).days


def _patterns(spec: dict | None, key: str) -> list[re.Pattern]:
    return [re.compile(rf"(?<!\w)(?:{p})(?!\w)", re.I) for p in ((spec or {}).get(key) or [])]


def _title_factor(name: str, spec: dict | None, title: str) -> dict:
    if not spec:
        return {"factor": name, "value": "unknown", "detail": f"no {name} preferences in the search profile"}
    for value in ("unfavorable", "favorable"):
        hit = next((p.pattern for p in _patterns(spec, value) if p.search(title)), None)
        if hit:
            return {"factor": name, "value": value, "detail": f"title matches {value} pattern"}
    return {"factor": name, "value": "neutral", "detail": "title matches no listed pattern"}


def opportunity(job: JobFacts, profile_rules: dict, rules: dict, now_ms: int) -> tuple[str, list[dict]]:
    o = rules["opportunity"]
    prefs = profile_rules.get("opportunity") or {}
    floor = float((profile_rules.get("salary") or {}).get("floor_annual") or 0)
    target = prefs.get("pay_target_annual")
    factors = []

    s = job.outcome.salary
    if s is None:
        factors.append({"factor": "pay", "value": "unknown", "detail": "no salary in the posting"})
    else:
        top = s.annual_max()
        low = s.min * ANNUAL_MULTIPLIER[s.period] if s.min is not None else top
        if top is not None and floor and top < floor:
            v, d = "unfavorable", f"top {top:,.0f}/yr under the floor {floor:,.0f}"
        elif target and low is not None and low >= target:
            v, d = "favorable", f"from {low:,.0f}/yr, at or above the target {target:,.0f}"
        elif target:
            v, d = "neutral", f"range reaches {top:,.0f}/yr; target {target:,.0f}" if top else "no upper bound"
        else:
            v, d = "neutral", "at or above the floor; no pay target set"
        factors.append({"factor": "pay", "value": v, "detail": d})

    mode = job.outcome.work_mode
    modes = prefs.get("work_modes") or {}
    if mode == "unknown":
        factors.append({"factor": "work_mode", "value": "unknown", "detail": "work mode not stated"})
    elif not modes:
        factors.append({"factor": "work_mode", "value": "unknown", "detail": "no work-mode preferences in the search profile"})
    else:
        factors.append({"factor": "work_mode", "value": modes.get(mode, "neutral"), "detail": mode})

    location_failed = [f for f in job.outcome.failed if f.split(":", 1)[0] in
                       ("LOCATION_NOT_ELIGIBLE", "REMOTE_EXCLUDES_HOME", "REMOTE_ANCHORED")]
    elig = job.outcome.remote_eligibility
    if location_failed:
        factors.append({"factor": "location", "value": "unfavorable", "detail": location_failed[0]})
    elif elig in ("HOME_OK", "US_WIDE"):
        factors.append({"factor": "location", "value": "favorable", "detail": elig})
    else:
        factors.append({"factor": "location", "value": "unknown", "detail": elig})

    factors.append(_title_factor("seniority", prefs.get("seniority"), job.title))
    factors.append(_title_factor("role_family", prefs.get("role_families"), job.title))

    age = _days_old(job.posted_at, now_ms)
    if age is None:
        factors.append({"factor": "age", "value": "unknown", "detail": "no posting date"})
    elif age <= o["age_favorable_days"]:
        factors.append({"factor": "age", "value": "favorable", "detail": f"{age} days"})
    elif age > o["age_unfavorable_days"]:
        factors.append({"factor": "age", "value": "unfavorable", "detail": f"{age} days"})
    else:
        factors.append({"factor": "age", "value": "neutral", "detail": f"{age} days"})

    values = [f["value"] for f in factors]
    if values.count("unfavorable") >= o["low_min_unfavorable"]:
        label = "LOW"
    elif values.count("favorable") >= o["high_min_favorable"] and "unfavorable" not in values:
        label = "HIGH"
    elif values.count("unknown") >= o["unknown_min_unknown"]:
        label = "UNKNOWN"
    else:
        label = "MEDIUM"
    return label, factors


# ----- recommendation -----

def _questions(elig_reasons: list[dict], fit_counts: dict, fit_label: str) -> list[dict]:
    qs = [{"kind": "eligibility", "type": r["type"], "requirement_id": r["requirement_id"], "question": r["question"]}
          for r in elig_reasons if r["effect"] == "unknown"]
    if fit_label == "INSUFFICIENT":
        qs += [{"kind": "experience", "requirement_id": u["requirement_id"],
                "question": f"Does your profile cover this? \"{u['quote'][:160]}\" Add an item if it does."}
               for u in fit_counts["unknown_requirements"]]
        if fit_counts["conflicts"] > fit_counts["max_conflicts"]:
            qs.append({"kind": "interpretation", "question": f"{fit_counts['conflicts']} requirements were read differently by "
                                                             "the model and the code; check them in the evidence table."})
    return qs


def recommend(elig: str, elig_reasons: list[dict], fit_label: str, fit_counts: dict, opp: str,
              opp_factors: list[dict]) -> tuple[str, list[dict]]:
    if elig == "BLOCKED":
        blocks = [r for r in elig_reasons if r["effect"] == "blocks"]
        return "SKIP", [{"rule": 1, "name": "blocker", "detail": f"blocker: {b['quote'][:200]}", "requirement_id": b["requirement_id"]}
                        for b in blocks]
    if fit_label == "WEAK":
        gaps = "; ".join(g["quote"][:80] for g in fit_counts["material_gaps"])
        return "SKIP", [{"rule": 2, "name": "weak_fit", "detail": f"{fit_counts['gap_material']} material gaps: {gaps}"}]
    if elig == "UNKNOWN" or fit_label == "INSUFFICIENT":
        why = []
        if elig == "UNKNOWN":
            why.append("eligibility unknown")
        if fit_label == "INSUFFICIENT":
            why.append(f"fit insufficient ({fit_counts['rule']})")
        return "REVIEW", [{"rule": 3, "name": "review", "detail": "; ".join(why),
                           "questions": _questions(elig_reasons, fit_counts, fit_label)}]
    if fit_label in ("STRONG", "COMPETITIVE"):
        detail = f"fit {fit_label}"
        if fit_counts["self_reported_covering"]:
            # Never hidden: an APPLY carried by self-reported items says so (6.5).
            detail += f"; {fit_counts['self_reported_covering']} covered only by self-reported items"
        return "APPLY", [{"rule": 4, "name": "apply", "detail": detail}]
    if fit_label == "STRETCH" and opp == "LOW":
        bad = ", ".join(f"{f['factor']} ({f['detail']})" for f in opp_factors if f["value"] == "unfavorable")
        return "SKIP", [{"rule": 5, "name": "stretch_low_value", "detail": f"stretch, low value: {bad}"}]
    return "STRETCH", [{"rule": 6, "name": "stretch", "detail": fit_counts["rule"]}]


def assess(reqs: list[Req], matches: dict[int, Validated], job: JobFacts, profile_rules: dict, rules: dict,
           now_ms: int, alt_check: AltCheck | None = None) -> Assessment:
    """`alt_check(req, type)` re-runs the fact check under another eligibility type (code only);
    used by rules that set eligibility.type_only_conflict = check_both (v2)."""
    elig, elig_reasons = eligibility(reqs, matches, rules, alt_check)
    fit_label, counts = fit(reqs, matches, rules)
    opp, factors = opportunity(job, profile_rules, rules, now_ms)
    rec, decisive = recommend(elig, elig_reasons, fit_label, counts, opp, factors)
    values = [f["value"] for f in factors]
    # Orders the list within a category: opportunity value only, never a blend of the three.
    rank = OPPORTUNITY_RANK[opp] * 10 + values.count("favorable") - values.count("unfavorable")
    return Assessment(elig, elig_reasons, fit_label, counts, opp, factors, rec, decisive, float(rank))
