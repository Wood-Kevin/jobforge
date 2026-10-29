"""Layer B: verdict rules without a model or a database (ARCHITECTURE 6.5, 6.6 B; SPEC D1, D6, D7, D15).

Table cases live in tests/fixtures/verdict/ (layer_b.yaml: assessments and recommendation;
facts.yaml: eligibility facts matched by code). The property test generates random requirement
and match sets and checks that SKIP only ever comes from rules 1, 2 or 5, each backed by
positive evidence.
"""
import itertools
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from conftest import NOW
from jobforge_agent.evaluation import assess, facts, match, validate
from jobforge_agent.evaluation.snapshot import Item, Snapshot
from jobforge_agent.filtering.rules import FilterOutcome
from jobforge_agent.filtering.salary import Salary

FIXTURES = Path(__file__).parent / "fixtures" / "verdict"
RULES = assess.load_rules("v1")
PROFILE_RULES = {"salary": {"floor_annual": 50000},
                 "opportunity": {"pay_target_annual": 80000,
                                 "work_modes": {"remote": "favorable", "hybrid": "neutral", "onsite": "unfavorable"},
                                 "role_families": {"favorable": ["noc"]}, "seniority": {"unfavorable": ["senior"]}}}
JOB = {"title": "NOC Engineer", "work_mode": "remote", "remote_eligibility": "US_WIDE", "failed": [], "salary": None,
       "posted_days_ago": 3}


def req(id_, cls="negotiable", status="confirmed", origin="model", type_=None, quote=None, cue_polarity="affirmative",
        min_years=None, flags=(), conflict=None):
    fl = tuple({"flag": f} for f in flags)
    if conflict is not None:
        fl += ({"flag": "conflict", "fields": conflict},)
    return facts.Req(id_, quote or f"requirement {id_}", quote or f"requirement {id_}", None, type_, cls, status, origin,
                     cue_polarity, min_years, "requirements", fl)


def vmatch(r, mt, basis="verified", materiality=None, matched_by="model", check=None, cited=True, flags=()):
    flags = ([check] if check else []) + list(flags)
    return validate.Validated(r.id, matched_by, mt, basis if mt != "unknown" else "none",
                              [1] if mt != "unknown" and cited else [],
                              gap_materiality=materiality or ("material" if mt == "gap" else "none"), flags=flags)


_OUTCOMES = {"direct": ("direct", "verified", None), "direct_self": ("direct", "self_reported", None),
             "transferable": ("transferable", "verified", None), "gap_material": ("gap", "verified", "material"),
             "gap_minor": ("gap", "verified", "minor"), "unknown": ("unknown", "none", None)}


def build(case):
    ids = itertools.count(1)
    reqs, matches = [], {}
    for cls in ("negotiable", "preferred"):
        for kind, n in (case.get(cls) or {}).items():
            for _ in range(n):
                i = next(ids)
                if kind == "unverifiable":
                    reqs.append(req(i, cls, "unverifiable"))
                    continue
                r = req(i, cls, "conflict" if kind == "conflict" else "confirmed")
                reqs.append(r)
                if kind == "gap_uncited":  # the model says "lacks it", cites nothing
                    matches[i] = vmatch(r, "gap", "none", "material", cited=False)
                elif kind == "gap_cert_list":  # absence confirmed by cert_list_complete (validate.py)
                    matches[i] = vmatch(r, "gap", "none", "material", cited=False,
                                        flags=[{"flag": "absence_confirmed", "by": "cert_list_complete"}])
                else:
                    mt, basis, mat = _OUTCOMES.get(kind, ("direct", "verified", None))
                    matches[i] = vmatch(r, mt, basis, mat)
    alts = {}
    for h in case.get("hard") or []:
        i = next(ids)
        conflict = None
        if h["status"] == "conflict":
            # Default: a disagreement on importance. model_type: the type is the only disagreement.
            conflict = ([{"field": "eligibility_type", "model": h["model_type"], "cue": [h["type"]]}] if "model_type" in h
                        else [{"field": "importance", "model": "required", "cue": "preferred"}])
        r = req(i, h.get("class", "hard"), h["status"], h.get("origin", "model"), h["type"], h.get("quote"),
                h.get("cue_polarity", "affirmative"), conflict=conflict)
        if "alt" in h:
            alts[i] = facts.FactCheck(h["alt"]["outcome"], h["alt"]["basis"])
        reqs.append(r)
        check = {"flag": "eligibility_check", "outcome": h["outcome"]}
        if h.get("question"):
            check["question"] = h["question"]
        mt = {"met": "direct", "fails": "gap", "unanswered": "unknown"}[h["outcome"]]
        matches[i] = validate.Validated(i, "eligibility_fact", mt, h.get("basis", "none"), [], flags=[check])
    job = {**JOB, **(case.get("job") or {})}
    days = job["posted_days_ago"]
    posted = None if days is None else (datetime.fromtimestamp(NOW / 1000, tz=timezone.utc) - timedelta(days=days)).date().isoformat()
    s = job["salary"]
    outcome = FilterOutcome(passed=not job["failed"], primary=None, failed=job["failed"], work_mode=job["work_mode"],
                            remote_eligibility=job["remote_eligibility"],
                            salary=Salary(s["min"], s["max"], s["period"], "structured") if s else None)
    profile = {**PROFILE_RULES, **(case.get("profile") or {})}
    build.alt_check = lambda r, other: alts.get(r.id, facts.FactCheck("unanswered", "none"))
    return reqs, matches, assess.JobFacts(job["title"], posted, outcome), profile


CASES = yaml.safe_load((FIXTURES / "layer_b.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_layer_b(case):
    reqs, matches, job, profile = build(case)
    a = assess.assess(reqs, matches, job, profile, assess.load_rules(case.get("rules", "v1")), NOW, build.alt_check)
    exp = case["expect"]
    got = {"eligibility": a.eligibility, "fit": a.fit, "opportunity": a.opportunity, "recommendation": a.recommendation,
           "rule": a.decisive_reasons[0]["rule"]}
    assert {k: got[k] for k in exp if k in got} == {k: v for k, v in exp.items() if k in got}, (a.fit_counts["rule"], a.eligibility_reasons)
    if "question" in exp:
        assert exp["question"] in [q["question"] for q in a.decisive_reasons[0]["questions"]]
    if "detail_contains" in exp:
        assert exp["detail_contains"] in a.decisive_reasons[0]["detail"]


FACT_CASES = yaml.safe_load((FIXTURES / "facts.yaml").read_text(encoding="utf-8"))


def fact_items(spec: dict) -> list[Item]:
    out = []
    for n, (kind, attrs) in enumerate(spec.items(), 100):
        attrs = dict(attrs)
        status = attrs.pop("item_status", "verified")
        out.append(Item(n, "eligibility", kind, status, {"type": kind, **attrs}, None, None, None))
    return out


@pytest.mark.parametrize("case", FACT_CASES, ids=[c["name"] for c in FACT_CASES])
def test_eligibility_fact_matching(case):
    r = req(1, "hard", type_=case["type"], quote=case["quote"], flags=case.get("flags", ()))
    siblings = [req(2 + n, "negotiable", type_=s["type"], quote=s["quote"], flags=s.get("flags", ()))
                for n, s in enumerate(case.get("siblings") or [])]
    fc = facts.check(r, fact_items(case["facts"]), case.get("completeness") or {}, [r, *siblings])
    exp = case["expect"]
    assert fc.outcome == exp["outcome"], fc.detail
    if "basis" in exp:
        assert fc.basis == exp["basis"]
    if "reason" in exp:
        assert fc.detail.get("reason") == exp["reason"], fc.detail
    if exp.get("has_question"):
        assert fc.question
    if "question_contains" in exp:
        assert exp["question_contains"] in (fc.question or ""), fc.question


def test_fact_types_cover_every_e2_eligibility_type():
    """A type the E2 lexicon can assign but facts.py cannot check would always be unanswered. A fact type
    awaiting its cues (AWAITING_CUES: stored, never assigned to a line yet) needs no check until it gets them."""
    from jobforge_agent.analysis.cues import ELIGIBILITY_TYPES
    from jobforge_agent.profile.items import AWAITING_CUES, ELIGIBILITY
    assert set(facts._CHECKS) == set(ELIGIBILITY_TYPES) == set(ELIGIBILITY) - AWAITING_CUES


# ----- E4 validation -----

def _m(mt, cited=("P1",), bridge=None, limits=None, detail=None, rid="R1"):
    return match._Match(requirement=rid, match_type=mt, cited_items=list(cited), bridge=bridge, transfer_limits=limits, gap_detail=detail)


SNAP = Snapshot(1, {1: Item(1, "role", "NOC Tier I", "verified", {}, None, "2024-10", "2025-09"),
                    2: Item(2, "role", "IT Specialist", "self_reported", {}, None, "2025-09", "2025-12"),
                    3: Item(3, "skill", "Linux", "self_reported", {}, 3.0, None, None),
                    4: Item(4, "eligibility", "citizen", "verified", {"type": "work_authorization"}, None, None, None)}, {})


def test_transferable_with_empty_bridge_is_unknown():
    v = validate.model_match(req(1), _m("transferable", bridge="  "), SNAP, {}, RULES, NOW)
    assert (v.match_type, v.basis, v.citations) == ("unknown", "none", [])
    assert v.has_flag("transferable_without_bridge") and v.has_flag("downgraded")


def test_transferable_with_bridge_stands_and_keeps_both_texts():
    v = validate.model_match(req(1), _m("transferable", bridge="NOC monitoring", limits="no SIEM tuning"), SNAP, {}, RULES, NOW)
    assert (v.match_type, v.bridge, v.transfer_limits, v.basis) == ("transferable", "NOC monitoring", "no SIEM tuning", "verified")


def test_direct_without_citation_is_unknown():
    v = validate.model_match(req(1), _m("direct", cited=()), SNAP, {}, RULES, NOW)
    assert v.match_type == "unknown" and v.has_flag("direct_without_citation")


def test_basis_is_verified_only_through_a_verified_item():
    assert validate.model_match(req(1), _m("direct", cited=("P3",)), SNAP, {}, RULES, NOW).basis == "self_reported"
    assert validate.model_match(req(1), _m("direct", cited=("P3", "P1")), SNAP, {}, RULES, NOW).basis == "verified"


def test_rejected_citation_leaves_the_rest():
    v = validate.model_match(req(1), _m("direct", cited=("P99", "P3")), SNAP, {}, RULES, NOW, frozenset({99}))
    assert (v.match_type, v.citations) == ("direct", [3]) and v.has_flag("citation_rejected")
    v = validate.model_match(req(1), _m("direct", cited=("P99",)), SNAP, {}, RULES, NOW, frozenset({99}))
    assert v.match_type == "unknown"


def test_years_merge_overlapping_roles_and_override_a_model_direct():
    # 2024-10..2025-09 and 2025-09..2025-12 overlap one month: 15 months, not 16.
    assert validate.years_shown([SNAP.items[1], SNAP.items[2]], NOW) == 1.25
    v = validate.model_match(req(1, min_years=5), _m("direct", cited=("P1", "P2")), SNAP, {}, RULES, NOW)
    assert v.match_type == "gap" and v.has_flag("years_short_override")
    assert v.gap_size["years_short"] == 3.75 and v.gap_materiality == "material"


def test_years_short_by_1_of_5_is_minor_by_4_of_5_material():
    assert validate.materiality(req(1, min_years=5), {"years_needed": 5, "years_short": 1}, RULES) == "minor"
    assert validate.materiality(req(1, min_years=5), {"years_needed": 5, "years_short": 4}, RULES) == "material"
    assert validate.materiality(req(1, min_years=10), {"years_needed": 10, "years_short": 3}, RULES) == "minor"  # 30% <= 40%
    assert validate.materiality(req(1, "preferred"), {"years_needed": 5, "years_short": 5}, RULES) == "minor"
    assert validate.materiality(req(1), None, RULES) == "material"


def test_a_line_answered_i_dont_have_this_is_an_evidenced_gap_whatever_the_model_said():
    """0012: the same line (any case, spacing or end punctuation) is a gap; closer wording is not."""
    said = {"lacking": ["Experience with SQL or PostgreSQL"]}
    for m in (_m("direct"), _m("transferable", bridge="queries"), _m("unknown", cited=()), None):
        v = validate.model_match(req(1, quote="experience with  SQL or PostgreSQL."), m, SNAP, said, RULES, NOW)
        assert (v.match_type, v.basis, v.citations, v.gap_materiality) == ("gap", "none", [], "material")
        assert v.has_flag("absence_confirmed")
    assert validate.model_match(req(1, "preferred", quote="Experience with SQL or PostgreSQL"), None, SNAP, said, RULES,
                                NOW).gap_materiality == "minor"
    assert validate.model_match(req(1, quote="Experience with SQL"), _m("direct"), SNAP, said, RULES, NOW).match_type == "direct"
    assert validate.model_match(req(1, quote="Experience with SQL or PostgreSQL"), _m("direct"), SNAP, {}, RULES, NOW).match_type == "direct"


def test_certification_absence_is_not_a_gap_unless_the_cert_list_is_complete():
    r = req(1, quote="CCNA certification required")
    v = validate.model_match(r, _m("gap", cited=(), detail="no CCNA in the profile"), SNAP, {}, RULES, NOW)
    assert v.match_type == "unknown" and v.has_flag("absence_not_evidence")
    v = validate.model_match(r, _m("gap", cited=(), detail="no CCNA in the profile"), SNAP, {"cert_list_complete": True}, RULES, NOW)
    assert (v.match_type, v.gap_materiality, v.gap_size) == ("gap", "material", {"detail": "no CCNA in the profile"})


def test_gap_without_any_reason_is_unknown():
    v = validate.model_match(req(1), _m("gap", cited=()), SNAP, {}, RULES, NOW)
    assert v.match_type == "unknown" and v.has_flag("gap_without_reason")


def test_omitted_requirement_is_unknown():
    v = validate.model_match(req(1), None, SNAP, {}, RULES, NOW)
    assert v.match_type == "unknown" and v.has_flag("model_omitted")


# ----- property test: SKIP only from positive evidence -----

def _random_case(rng: random.Random):
    ids = itertools.count(1)
    reqs, matches = [], {}
    for _ in range(rng.randint(0, 12)):
        i = next(ids)
        cls = rng.choice(["negotiable", "negotiable", "preferred"])
        status = rng.choice(["confirmed", "confirmed", "conflict", "unverifiable"])
        r = req(i, cls, status)
        reqs.append(r)
        if status != "unverifiable":
            mt = rng.choice(["direct", "transferable", "gap", "unknown"])
            matches[i] = vmatch(r, mt, rng.choice(["verified", "self_reported"]), rng.choice(["material", "minor"]) if mt == "gap" else None)
    for _ in range(rng.randint(0, 3)):
        i = next(ids)
        origin = rng.choice(["model", "model", "unextracted_eligibility"])
        status = "unverifiable" if origin != "model" else rng.choice(["confirmed", "conflict"])
        conflict = None
        if status == "conflict":
            conflict = rng.choice([[{"field": "eligibility_type", "model": rng.choice(["work_authorization", "none", "residency"]),
                                     "cue": ["residency"]}], [{"field": "subject", "model": "candidate", "cue": "company"}]])
        r = req(i, "hard", status, origin, rng.choice(list(facts._CHECKS)), cue_polarity=rng.choice(["affirmative", "negated"]),
                conflict=conflict)
        reqs.append(r)
        outcome = rng.choice(["met", "fails", "unanswered"])
        mt = {"met": "direct", "fails": "gap", "unanswered": "unknown"}[outcome]
        matches[i] = validate.Validated(i, "eligibility_fact", mt, rng.choice(["verified", "self_reported", "none"]), [],
                                        flags=[{"flag": "eligibility_check", "outcome": outcome}])
    job = {**JOB, "work_mode": rng.choice(["remote", "hybrid", "onsite", "unknown"]),
           "posted_days_ago": rng.choice([None, 1, 20, 90]),
           "remote_eligibility": rng.choice(["US_WIDE", "HOME_OK", "UNKNOWN", "RESTRICTED_EXCLUDES_HOME"])}
    return reqs, matches, job


@pytest.mark.parametrize("version", ["v1", "v2", "v3", "v4"])
def test_skip_only_from_positive_evidence(version):
    rng = random.Random(7)
    rules = assess.load_rules(version)
    seen = {"SKIP": 0}

    def alt_check(r, other):  # the second reading of a type-only conflict: any outcome
        return facts.FactCheck(rng.choice(["met", "fails", "unanswered"]), rng.choice(["verified", "self_reported", "none"]))

    for _ in range(3000):
        reqs, matches, job = _random_case(rng)
        _, _, jf, profile = build({"job": job})
        a = assess.assess(reqs, matches, jf, profile, rules, NOW, alt_check)
        rule = a.decisive_reasons[0]["rule"]
        if a.recommendation != "SKIP":
            continue
        seen["SKIP"] += 1
        assert rule in (1, 2, 5)
        if rule == 1:
            # A block needs a confirmed, model-extracted hard requirement failed by a verified fact.
            blockers = [r for r in reqs if r.origin == "model" and r.final_class == "hard" and r.interpretation_status == "confirmed"
                        and matches[r.id].match_type == "gap" and matches[r.id].basis == "verified"]
            assert blockers
        else:
            # Weak fit or a low-value stretch: judged on enough evidence (never INSUFFICIENT).
            assert a.fit in ("WEAK", "STRETCH") and a.fit_counts["judgeable"] >= RULES["fit"]["min_judgeable"]
            if rule == 5:
                assert a.opportunity == "LOW"
                assert not a.fit_counts.get("unshown")  # v4: a STRETCH from unknowns is never skipped
    assert seen["SKIP"] > 50  # the generator actually reaches SKIP


def test_on_call_never_lowers_opportunity_value():
    """Kevin, 2026-10-24: on-call participation is an availability fact, not a negative job attribute; pay and
    working conditions are separate. Opportunity value reads only the title, posting date and the filter's
    reading of location, work mode and salary, never a posting's duties, so an on-call line cannot move it."""
    import dataclasses
    from jobforge_agent.evaluation.assess import JobFacts
    assert {f.name for f in dataclasses.fields(JobFacts)} == {"title", "posted_at", "outcome"}



def test_v4_years_tolerance_is_one_year_or_a_quarter():
    v3, v4 = assess.load_rules("v3"), assess.load_rules("v4")
    short = {"years_needed": 3, "years_short": 1.1}  # 1.9 years against "3-5 years" (a real job, 2026-10-25)
    assert validate.materiality(req(1, min_years=3), short, v3) == "minor"
    assert validate.materiality(req(1, min_years=3), short, v4) == "material"
    assert validate.materiality(req(1, min_years=2), {"years_needed": 2, "years_short": 0.1}, v4) == "minor"
    assert validate.materiality(req(1, min_years=10), {"years_needed": 10, "years_short": 2.5}, v4) == "minor"  # 25%
    assert validate.materiality(req(1, min_years=5), {"years_needed": 5, "years_short": 3.1}, v4) == "material"


def test_v4_years_count_only_what_the_model_cites():
    # E4 counts years from the cited items only, so v4's tighter tolerance depends on E3 citing every item
    # that shows the experience (prompt m2-v2). Under m2-v1 the model cited the two latest roles and left
    # out the longest one: 0.92 of about 1.9 real years, a material gap on "2+ years" (a real job, 2026-10-26).
    v4, r = assess.load_rules("v4"), req(1, min_years=2)
    one = validate.model_match(r, _m("direct", cited=("P2",)), SNAP, {}, v4, NOW)
    both = validate.model_match(r, _m("direct", cited=("P1", "P2")), SNAP, {}, v4, NOW)
    assert (one.gap_size["years_shown"], one.gap_materiality) == (0.33, "material")
    assert (both.gap_size["years_shown"], both.gap_materiality) == (1.25, "minor")
