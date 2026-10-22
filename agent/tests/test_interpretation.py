"""ARCHITECTURE 6.6 layer A: E0 segmenter + E2 interpreter against table-driven fixtures, no model.

Fixtures: tests/fixtures/interpretation/layer_a.yaml (format described at its top).
"""
from pathlib import Path

import pytest
import yaml

from jobforge_agent.analysis import cues
from jobforge_agent.analysis.interpret import ModelItem, interpret
from jobforge_agent.analysis.segment import heading_label, segment
from jobforge_agent.profile import items as profile_items

CASES = yaml.safe_load((Path(__file__).parent / "fixtures" / "interpretation" / "layer_a.yaml").read_text(encoding="utf-8"))


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
    ("Annual Salary:", "benefits"), ("How GitLab Supports Full-Time Employees", "benefits"), ("Vultr Cares", "benefits"),
    ("About Tailscale", "about"), ("WHO WE ARE", "about"), ("About the team", "about"),
    ("Equal Opportunity Employer", "eeo"), ("Inclusion & Privacy", "eeo"),
    ("Voluntary Diversity and Equal Opportunity Information", "eeo"), ("Keeper Candidate Privacy Notice", "privacy"),
    # Not in the lexicon: these become `unknown` sections (Join Vultr is that posting's role description).
    ("Join Vultr", None), ("Logistics", None), ("Timezones & Logistics", None),
])
def test_heading_lexicon_on_real_headings(heading, label):
    """Headings seen in the 42 queued real postings of the P1.4 evidence database."""
    assert heading_label(heading) == label


def test_field_lines_and_amounts_are_not_headings():
    seg = segment("Benefits\n\nLocation: Remote, US\n\n$95,200—$160,800 USD\n\nClassification: Exempt")
    assert [s.label for s in seg.sections] == ["benefits"]


def test_eligibility_types_match_the_profile_fact_types():
    """Dependent system (P1.5): a hard requirement's type picks the declared eligibility fact it is
    matched against in P1.7, so the lexicon and the profile must name the same types."""
    assert set(cues.ELIGIBILITY_TYPES) == set(profile_items.ELIGIBILITY)


@pytest.mark.parametrize("text,want", [
    ("5+ years of Linux", [5]), ("3-5 years of experience preferred", [3]), ("Four or more years", [4]),
    ("5 years with a degree or 7 without", [5, 7]), ("at least 2 yrs", [2]), ("10% travel", []),
])
def test_years(text, want):
    assert cues.years(text.lower()) == want
