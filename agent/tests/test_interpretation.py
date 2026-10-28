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




# ----- interp-v4: duties under a duties heading are context; seg-v2 headings -----

DUTIES = ("Key Responsibilities:\n- Administer Azure Virtual Desktop host pools and session hosts.\n"
          "- Maintain runbooks and troubleshooting guides for the support team.\n"
          "- Support a 24x7x365 operation, including shift work, weekends, and holidays as required.\n"
          "- Leverage your experience administering Intune to manage endpoints.\n"
          # Lines like these, from real postings, were set aside before the JOB_CONDITION and "looking for" guards
          # (2026-10-27 measurement on 20 postings): a location, a schedule, a requirement worded as "we're looking for".
          "- Remote Global - Non U.S.\n"
          "- Dedicated support hire in the United States.\n"
          "- Work a hybrid or remote schedule depending on proximity to an office; travel as needed.\n"
          "- We're looking for people that went into the internals of OLAP databases.\n\n"
          "Requirements:\n- 2+ years supporting Windows endpoints.\n- Administer Azure Virtual Desktop host pools and session hosts.")


def _read(text, quote, years=None):
    (r,) = [x for x in interpret(segment(text), [ModelItem(quote, quote, "required", "affirmative", "candidate", "none", years)])
            if x.origin == "model"]
    return r


def test_interp_v4_a_task_under_a_duties_heading_is_context():
    r = _read(DUTIES.split("\n\nRequirements")[0], "Maintain runbooks and troubleshooting guides for the support team.")
    assert (r.final_class, r.interpretation_status) == ("context", "confirmed")
    assert any(f["flag"] == "duty_by_section" for f in r.flags)


@pytest.mark.parametrize("quote", [
    # A condition worded as a task: still checked against the availability fact (the recall scan skips hours).
    "Support a 24x7x365 operation, including shift work, weekends, and holidays as required.",
    # A line under the duties heading that asks for prior experience.
    "Leverage your experience administering Intune to manage endpoints.",
    "Remote Global - Non U.S.",
    "Dedicated support hire in the United States.",
    "Work a hybrid or remote schedule depending on proximity to an office; travel as needed.",
    "We're looking for people that went into the internals of OLAP databases.",
])
def test_interp_v4_a_condition_or_a_qualification_under_a_duties_heading_stays(quote):
    r = _read(DUTIES, quote)
    assert r.final_class in ("hard", "negotiable") and not any(f["flag"] == "duty_by_section" for f in r.flags)


def test_interp_v4_the_same_words_under_requirements_are_a_requirement():
    r = _read(DUTIES, "Administer Azure Virtual Desktop host pools and session hosts.")
    assert (r.final_class, r.interpretation_status, r.section_label) == ("negotiable", "confirmed", "requirements")
    assert {"also_listed_as_duty", "ambiguous_location"} <= {f["flag"] for f in r.flags}


@pytest.mark.parametrize("heading,label", [
    ("Position Responsibilities", "responsibilities"), ("Duties and Responsibilities", "responsibilities"),
    ("Essential Functions", "responsibilities"), ("Required:", "requirements"),
    ("Required Skills and Qualifications", "requirements"),
])
def test_seg_v2_headings(heading, label):
    assert heading_label(heading) == label


# ----- interp-v5: parts of one line listed separately are one requirement -----

SPLIT = ("Requirements:\n- Linux familiarity and basic GCP or AWS IAM.\n"
         "- PowerShell scripting experience; familiarity with Terraform.\n"
         "- 3+ years in IT operations, including 1+ years at a startup.\n"
         "- Must be authorized to work in the US and comfortable with Jira.\n"
         "- Python scripting is required, Go is a plus.")


def _rows(*parts, years=None):
    items = [ModelItem(q, q, imp, "affirmative", "candidate", "none", (years or {}).get(q)) for q, imp in parts]
    return [r for r in interpret(segment(SPLIT), items) if r.origin == "model"]


def test_interp_v5_parts_of_one_line_become_one_requirement():
    (r,) = _rows(("Linux familiarity", "required"), ("basic GCP or AWS IAM", "required"))
    assert r.jd_quote == "Linux familiarity and basic GCP or AWS IAM" and r.final_class == "negotiable"
    assert next(f for f in r.flags if f["flag"] == "merged_split_line")["parts"] == ["Linux familiarity", "basic GCP or AWS IAM"]


def test_interp_v5_the_same_quote_twice_is_one_requirement():
    q = "3+ years in IT operations, including 1+ years at a startup"
    rows = _rows((q, "required"), (q, "required"), years={q: 3})
    assert len(rows) == 1 and rows[0].min_years == 3


@pytest.mark.parametrize("parts,years", [
    # A semicolon lists separate requirements.
    ((("PowerShell scripting experience", "required"), ("familiarity with Terraform", "required")), None),
    # Two parts each with their own years.
    ((("3+ years in IT operations", "required"), ("1+ years at a startup", "required")),
     {"3+ years in IT operations": 3, "1+ years at a startup": 1}),
    # A condition of the job is checked on its own.
    ((("Must be authorized to work in the US", "required"), ("comfortable with Jira", "required")), None),
    # A required part and a nice-to-have part.
    ((("Python scripting is required", "required"), ("Go is a plus", "preferred")), None),
])
def test_interp_v5_parts_that_stay_separate(parts, years):
    assert len(_rows(*parts, years=years)) == 2


# ----- interp-v6: a duty done with named tools keeps the tools -----

TOOL_DUTIES = ("Responsibilities:\n- Investigate application behavior using Go, TypeScript, and SQL.\n"
               "- Develop and maintain automation using Terraform, Ansible, Python.\n"
               "- Administer Azure Virtual Desktop host pools and session hosts.\n"
               "- Communicate findings using clear, structured updates.\n"
               "- Partner cross-functionally with Engineering, Customer Success, and Account teams.\n"
               "- Respond to customer inquiries regarding Acme products and services.\n"
               "- Automate recurring administrative tasks.\n"
               "- Validate findings produced by AI-assisted systems before using them to resolve a case.")


@pytest.mark.parametrize("quote", [
    "Investigate application behavior using Go, TypeScript, and SQL",
    "Develop and maintain automation using Terraform, Ansible, Python",
    "Administer Azure Virtual Desktop host pools and session hosts",
])
def test_interp_v6_a_duty_done_with_named_tools_keeps_them_as_a_nice_to_have(quote):
    r = _read(TOOL_DUTIES, quote)
    assert (r.final_class, r.interpretation_status) == ("preferred", "confirmed")
    assert any(f["flag"] == "duty_names_tools" for f in r.flags) and not any(f["flag"] == "duty_by_section" for f in r.flags)


@pytest.mark.parametrize("quote", [
    # A tool word with nothing named (lowercase prose).
    "Communicate findings using clear, structured updates",
    # Names, but of teams and the company, with no tool word.
    "Partner cross-functionally with Engineering, Customer Success, and Account teams",
    "Respond to customer inquiries regarding Acme products and services",
    # "administrative" is not administering something.
    "Automate recurring administrative tasks",
    # "using them" points back at the findings, not at a tool.
    "Validate findings produced by AI-assisted systems before using them to resolve a case",
])
def test_interp_v6_other_duties_are_still_set_aside(quote):
    r = _read(TOOL_DUTIES, quote)
    assert r.final_class == "context" and any(f["flag"] == "duty_by_section" for f in r.flags)


def test_interp_v6_named_tools_outside_a_duties_heading_are_unchanged():
    # A company's stack under About stays context (layer A fixture); the rule reads only duties.
    r = _read("About the team:\nOur stack is managed using Terraform, Ansible, Datadog.",
              "Our stack is managed using Terraform, Ansible, Datadog")
    assert r.final_class == "context" and not any(f["flag"] == "duty_names_tools" for f in r.flags)


# ----- interp-v7: a background check is a step of the hiring process -----

STEPS = ("Requirements:\n- 3+ years of IT support experience.\n- Successful completion of a background check.\n"
         "- Must pass any and all required background checks.\n"
         "- Upon conditional offer of employment, candidates are required to complete comprehensive criminal background checks.\n"
         "- Ability to obtain a Public Trust clearance, which requires a background investigation.\n"
         "- 2+ years of experience conducting background investigations.")


@pytest.mark.parametrize("quote", [
    "Successful completion of a background check",
    "Must pass any and all required background checks",
    "Upon conditional offer of employment, candidates are required to complete comprehensive criminal background checks",
])
def test_interp_v7_a_background_check_is_context(quote):
    r = _read(STEPS, quote)
    assert (r.final_class, r.interpretation_status) == ("context", "confirmed")
    assert any(f["flag"] == "hiring_step" for f in r.flags)


@pytest.mark.parametrize("quote,cls", [
    # A clearance keeps its eligibility type and its check against the facts.
    ("Ability to obtain a Public Trust clearance, which requires a background investigation", "negotiable"),
    # Years of doing background investigations is experience asked of the candidate.
    ("2+ years of experience conducting background investigations", "negotiable"),
    ("3+ years of IT support experience", "negotiable"),
])
def test_interp_v7_other_lines_are_unchanged(quote, cls):
    r = _read(STEPS, quote, years=2 if quote.startswith("2+") else 3 if quote.startswith("3+") else None)
    assert r.final_class == cls and not any(f["flag"] == "hiring_step" for f in r.flags)
