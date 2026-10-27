"""ARCHITECTURE 6.6 layer A: E0 segmenter + E2 interpreter against table-driven fixtures, no model.

Fixtures: jobforge_agent/analysis/fixtures/layer_a.yaml (format described at its top; package data since P1.9
because `jobforge-agent golden --suite interpretation` runs the same cases against a real model).
"""
from importlib import resources

import pytest
import yaml

from jobforge_agent.analysis import cues
from jobforge_agent.analysis.interpret import ModelItem, interpret
from jobforge_agent.analysis.segment import heading_label, norm, segment
from jobforge_agent.profile import items as profile_items

CASES = yaml.safe_load((resources.files("jobforge_agent.analysis") / "fixtures" / "layer_a.yaml").read_text(encoding="utf-8"))


def _item(m: dict) -> ModelItem:
    return ModelItem(text=m.get("text", m["quote"]), jd_quote=m["quote"], importance=m.get("importance", "required"),
                     polarity=m.get("polarity", "affirmative"), subject=m.get("subject", "candidate"),
                     eligibility_type=m.get("eligibility_type", "none"), min_years=m.get("min_years"))


def test_fixture_ids_are_unique_and_cover_every_6_6_area():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    assert {c["area"] for c in CASES} >= {"negation", "importance", "years", "subject", "eeo", "eligibility", "quote", "recall"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_layer_a(case):
    seg = segment(case["text"])
    rows = interpret(seg, [_item(m) for m in case.get("model", [])])
    by_quote = {r.jd_quote: r for r in rows if r.origin == "model"}
    for quote, want in (case.get("expect") or {}).items():
        r = by_quote[quote]
        names = [f["flag"] for f in r.flags]
        for key, value in want.items():
            if key == "flags":
                assert set(value) <= set(names), f"{quote!r}: flags {names} lack {set(value) - set(names)}"
            elif key == "no_flags":
                assert not set(value) & set(names), f"{quote!r}: unexpected flags {set(value) & set(names)}"
            elif key == "years":
                assert [f for f in r.flags if f["flag"] == "years"][0]["values"] == value
            else:
                assert getattr(r, key) == value, f"{quote!r}: {key} = {getattr(r, key)!r}, expected {value!r} (flags {r.flags})"
        # Both readings are always stored for a located quote.
        assert r.model_importance is not None and r.model_polarity is not None and r.model_subject is not None
        if r.quote_found:
            assert r.cue_polarity is not None and r.cue_subject is not None
    if "recall" in case:
        recall = [r for r in rows if r.origin == "unextracted_eligibility"]
        assert [r.eligibility_type for r in recall] == case["recall"], [(r.sentence, r.flags) for r in recall]
        assert all(r.interpretation_status == "unverifiable" and r.quote_found for r in recall)
    if "headings" in case:
        assert seg.headings == case["headings"]


def test_conflict_rows_keep_both_readings_in_flags():
    seg = segment("Nice to have:\n- Must have AWS experience.")
    (r,) = interpret(seg, [_item({"quote": "Must have AWS experience"})])
    conflict = [f for f in r.flags if f["flag"] == "conflict"][0]
    assert conflict["fields"][0]["field"] == "importance"
    assert r.model_importance == "required" and r.final_class == "preferred"


def test_unverifiable_rows_are_never_hard():
    """Property over the whole fixture table: no unverifiable row is hard, and every hard row has
    a code-assigned eligibility type (D6)."""
    for case in CASES:
        for r in interpret(segment(case["text"]), [_item(m) for m in case.get("model", [])]):
            if r.final_class == "hard":
                assert r.eligibility_type in cues.ELIGIBILITY_TYPES and r.interpretation_status != "unverifiable" or r.origin != "model"
            if r.interpretation_status == "unverifiable" and r.origin == "model":
                assert r.final_class != "hard"


def test_segment_offsets_point_into_the_normalized_text():
    seg = segment("WHO WE ARE\n\nWe build clouds.\n\nRequirements:\n- 3+ years of Linux. Must be a US citizen.\n- Bash")
    for s in seg.sentences:
        assert seg.text[s.start:s.end] == s.text
    assert [(s.label, s.heading) for s in seg.sections] == [("about", "WHO WE ARE"), ("requirements", "Requirements:")]
    assert [s.text for s in seg.sentences if s.label == "requirements" and not s.heading] == [
        "3+ years of linux.", "must be a us citizen.", "bash"]


@pytest.mark.parametrize("heading,label", [
    ("Key Responsibilities", "responsibilities"), ("What You’ll Do", "responsibilities"), ("About the Role", "responsibilities"),
    ("Qualifications", "requirements"), ("What You’ll Bring", "requirements"), ("You may be a good fit if you", "requirements"),
    ("What we are looking for", "requirements"), ("Preferred Qualifications", "preferred"), ("Nice to have", "preferred"),
    ("Strong candidates may also have", "preferred"), ("It would be great if you had", "preferred"),
    ("What We Offer", "benefits"), ("United States Salary Range", "benefits"), ("US pay range", "benefits"),
    ("Annual Salary:", "benefits"), ("How Company X Supports Full-Time Employees", "benefits"), ("Acme Cares", "benefits"),
    ("About Acme", "about"), ("WHO WE ARE", "about"), ("About the team", "about"),
    ("Equal Opportunity Employer", "eeo"), ("Inclusion & Privacy", "eeo"),
    ("Voluntary Diversity and Equal Opportunity Information", "eeo"), ("Company L Candidate Privacy Notice", "privacy"),
    # Not in the lexicon: these become `unknown` sections (Join Company U is that posting's role description).
    ("Join Company U", None), ("Logistics", None), ("Timezones & Logistics", None),
])
def test_heading_lexicon_on_real_headings(heading, label):
    """Headings seen in the 42 queued real postings of the P1.4 evidence database."""
    assert heading_label(heading) == label


def test_field_lines_and_amounts_are_not_headings():
    seg = segment("Benefits\n\nLocation: Remote, US\n\n$95,200—$160,800 USD\n\nClassification: Exempt")
    assert [s.label for s in seg.sections] == ["benefits"]


def test_eligibility_types_match_the_profile_fact_types():
    """Dependent system (P1.5): a hard requirement's type picks the declared eligibility fact it is
    matched against in P1.7, so the lexicon and the profile must name the same types. A fact type may
    exist before its cues only when listed in AWAITING_CUES (availability, 2026-10-24: stored, not yet
    matched); the E1 schema's type list is the lexicon's, so it is unchanged by that."""
    assert set(cues.ELIGIBILITY_TYPES) == set(profile_items.ELIGIBILITY) - profile_items.AWAITING_CUES
    assert not profile_items.AWAITING_CUES & set(cues.ELIGIBILITY_TYPES)


@pytest.mark.parametrize("text,want", [
    ("5+ years of Linux", [5]), ("3-5 years of experience preferred", [3]), ("Four or more years", [4]),
    ("5 years with a degree or 7 without", [5, 7]), ("at least 2 yrs", [2]), ("10% travel", []),
])
def test_years(text, want):
    assert cues.years(text.lower()) == want


@pytest.mark.parametrize("quote,want", [
    # Evaluation calibration 1, cases E1-E2 (the Company N case, 2026-10-24): these went to the matcher as experience.
    ("Remote in Eastern US (required)", ["residency"]),
    ("Located in US, eastern timezone (remote)", ["residency"]),
    ("US Time (Eastern): Friday - Tuesday, 8A - 5P", ["availability"]),
    ("Participate in an on-call rotation as needed.", ["availability"]),
    ("Support a 24x7x365 operation, including shift work, weekends, and holidays as required.", ["availability"]),
    ("Candidates must have authorization to work in the United States", ["work_authorization"]),
    # Not eligibility: a collaboration line, an office address, working a zone's hours from anywhere.
    ("Collaborate with teammates across time zones", []),
    ("Our office is located in Austin, Texas", []),
    ("Comfortable working with distributed teams in Eastern and Pacific time", []),
    # Before/after round 1 (2026-10-24): a zone with hours or alignment is about hours, not where you live;
    # benefit holidays are not a schedule.
    ("Ability to support customers and collaborate with colleagues during Pacific Time Zone business hours.", ["availability"]),
    ("Opportunity to work remotely within the United States, with a focus on Pacific Time Zone alignment.", ["availability"]),
    ("Flexible paid time off, plus 10 U.S. company holidays", []),
    ("Weekends and holidays as required by the support schedule", ["availability"]),
])
def test_interp_v3_eligibility_cues(quote, want):
    assert cues.eligibility_types(norm(quote)) == want


def test_recall_scan_does_not_raise_hours_from_prose():
    """Before/after round 1: Company W's "our ticketing, on-call, and incident communication setup" became an
    eligibility question and turned an APPLY into REVIEW."""
    seg = segment("Requirements\nWe run our ticketing, on-call, and incident communication setup on Pylon.\n")
    assert interpret(seg, []) == []


# ----- interp-v4: duties (E1 e1-v2) -----

DUTIES = ("Responsibilities:\n- Administer Azure Virtual Desktop host pools and session hosts.\n"
          "- Support a 24x7x365 operation, including shift work, weekends, and holidays as required.\n"
          "- Leverage your experience administering Intune to manage endpoints.\n\n"
          "Requirements:\n- 2+ years supporting Windows endpoints.")


@pytest.mark.parametrize("quote,want_class,want_flag", [
    # A task of the role: context, not matched against the profile.
    ("Administer Azure Virtual Desktop host pools and session hosts.", "context", "duty"),
    # A condition worded as a task stays a requirement, checked against the availability fact like any other
    # (the recall scan skips hours, so dropping it would lose it).
    ("Support a 24x7x365 operation, including shift work, weekends, and holidays as required.", "hard", "duty_with_condition"),
    # A "duty" that asks for prior experience is a requirement: code overrules the label.
    ("Leverage your experience administering Intune to manage endpoints.", "negotiable", "duty_asks_of_candidate"),
])
def test_interp_v4_duty_is_context_unless_code_reads_a_condition_or_a_qualification(quote, want_class, want_flag):
    def read(subject):
        (r,) = [x for x in interpret(segment(DUTIES), [ModelItem(quote, quote, "required", "affirmative", subject, "none", None)])
                if x.origin == "model"]
        return r
    r = read("duty")
    assert r.final_class == want_class and any(f["flag"] == want_flag for f in r.flags), r.flags
    if want_flag != "duty":  # kept: read exactly as the same line labelled "candidate" (class and status)
        c = read("candidate")
        assert (r.final_class, r.interpretation_status) == (c.final_class, c.interpretation_status)
    else:
        assert r.interpretation_status == "confirmed"  # code agrees it is not about the candidate: no conflict


def test_interp_v4_candidate_lines_are_unchanged():
    q = "2+ years supporting Windows endpoints."
    (r,) = [x for x in interpret(segment(DUTIES), [ModelItem(q, q, "required", "affirmative", "candidate", "none", 2)])
            if x.origin == "model"]
    assert (r.final_class, r.interpretation_status) == ("negotiable", "confirmed")
    assert not any(f["flag"].startswith("duty") for f in r.flags)
