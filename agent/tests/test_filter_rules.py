"""The scanner's filter regression suite (job-scanner tests/test_filters.py and test_salary.py, edd1528),
run against the ported rules with a real tuned profile.

The profile is personal data and is not in this repo: it is read from the scanner's config.yaml
(JOBFORGE_SCANNER_CONFIG, default ../../job-scanner/config.yaml next to this repo). Without it this
module is skipped and pytest prints the reason. Translation from the scanner's vocabulary:
REMOTE_EXCLUDES_FL -> REMOTE_EXCLUDES_HOME, FL_OK -> HOME_OK, upper-case work modes -> lower case.
The scanner's CLOSED rule is not ported: JobForge tracks closure on jobs.closed_at_ms instead.
"""
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from jobforge_agent.filtering import rules
from jobforge_agent.filtering.profile import compile_profile, from_scanner_config
from jobforge_agent.filtering.salary import Salary, parse_salary

SCANNER_CONFIG = Path(os.environ.get("JOBFORGE_SCANNER_CONFIG")
                      or Path(__file__).resolve().parents[2].parent / "job-scanner" / "config.yaml")

pytestmark = pytest.mark.skipif(not SCANNER_CONFIG.is_file(), reason=f"scanner config.yaml not found at {SCANNER_CONFIG}")


@pytest.fixture(scope="module")
def cfg():
    with open(SCANNER_CONFIG, encoding="utf-8") as f:
        return compile_profile(from_scanner_config(yaml.safe_load(f)))


def job(title="NOC Analyst", location="Remote - US", description="", workplace=None, salary=None, posted_at=None):
    return rules.JobView(title=title, location_text=location, description=description,
                         work_mode=workplace.lower() if workplace else None, salary=salary, posted_at=posted_at)


def location(j, p):
    """The scanner's pass1 location verdict: (kept, reason, mode, eligibility)."""
    reason, mode, elig = rules.check_location(j, p)
    return reason is None, reason or "", mode, elig


def evaluate(j, p, today=date(2026, 10, 13)):
    return rules.evaluate(j, p, today=today)


@pytest.mark.parametrize("title", [
    "NOC Analyst", "SOC Analyst I", "IT Support Specialist", "Systems Administrator",
    "Network Administrator", "Cloud Operations Engineer", "Help Desk Technician Tier 2",
    "IT Operations Analyst", "Infrastructure Support Engineer", "Desktop Support Technician",
    "Azure Cloud Support Engineer", "Internal IT Support Engineer",
])
def test_titles_kept(cfg, title):
    assert rules.check_title(title, cfg) is None


@pytest.mark.parametrize("title, reason", [
    ("Senior Systems Administrator", "TITLE_SENIORITY"),
    ("Sr. Systems Administrator", "TITLE_SENIORITY"),
    ("Staff Site Reliability Engineer, Infrastructure Operations", "TITLE_SENIORITY"),
    ("IT Support Lead", "TITLE_SENIORITY"),
    ("Manager, IT Operations", "TITLE_SENIORITY"),
    ("Head of IT", "TITLE_NO_MATCH"),
    ("Cloud Architect, Azure", "TITLE_SENIORITY"),
    ("IT Support Intern", "TITLE_SENIORITY"),
    ("Systems Engineer III", "TITLE_SENIORITY"),
    ("Sales Engineer, Azure", "TITLE_NON_IT"),
    ("Technical Recruiter, IT Infrastructure Support", "TITLE_NON_IT"),
    ("Account Executive", "TITLE_NO_MATCH"),
    ("Software Engineer, Backend", "TITLE_NO_MATCH"),
])
def test_titles_rejected(cfg, title, reason):
    got = rules.check_title(title, cfg)
    assert got is not None and got.split(":")[0] == reason, (title, got)


def test_remote_us_kept(cfg):
    kept, _, mode, elig = location(job(location="Remote - US"), cfg)
    assert kept and mode == "remote" and elig == "US_WIDE"


def test_remote_canada_rejected(cfg):
    kept, reason, _, _ = location(job(location="Remote - Canada"), cfg)
    assert not kept and reason.startswith("REMOTE_EXCLUDES_HOME")


def test_remote_us_or_canada_kept(cfg):
    kept, _, _, elig = location(job(location="Remote (United States | Canada)"), cfg)
    assert kept and elig == "US_WIDE"


def test_latam_only_in_description_rejected(cfg):
    kept, reason, _, _ = location(job(location="Remote", description="This role is LATAM-only due to time zones."), cfg)
    assert not kept and reason.startswith("REMOTE_EXCLUDES_HOME")


def test_cannot_hire_in_florida_rejected(cfg):
    d = "Remote within the US, excluding Alaska, Hawaii and Florida."
    kept, _, _, elig = location(job(location="Remote - US", description=d), cfg)
    assert not kept and elig == "RESTRICTED_EXCLUDES_HOME"


def test_description_country_words_do_not_restrict(cfg):
    assert location(job(location="Remote - US", description="We have offices in London and Austin. Join us!"), cfg)[0]


@pytest.mark.parametrize("loc", ["Jacksonville, FL", "Ponte Vedra Beach, Florida", "Orange Park, FL", "Live Oak, FL"])
def test_home_metro_onsite_kept(cfg, loc):
    kept, _, _, elig = location(job(location=loc, workplace="ONSITE"), cfg)
    assert kept and elig == "HOME_OK"


def test_onsite_elsewhere_rejected(cfg):
    kept, reason, _, _ = location(job(location="Austin, TX", workplace="ONSITE"), cfg)
    assert not kept and reason.startswith("LOCATION_NOT_ELIGIBLE")


def test_hybrid_outside_home_rejected(cfg):
    kept, _, mode, _ = location(job(location="Hybrid; Washington DC, US"), cfg)
    assert not kept and mode == "hybrid"


def test_multi_location_with_remote_option_kept(cfg):
    kept, _, mode, _ = location(job(location="Austin, TX; Remote, US"), cfg)
    assert kept and mode == "remote"


@pytest.mark.parametrize("loc", ["Massachusetts - Boston", "Ohio - Columbus", "Minnesota, United States"])
def test_api_remote_anchored_to_one_place_rejected(cfg, loc):
    kept, reason, _, _ = location(job(location=loc, workplace="REMOTE"), cfg)
    assert not kept and reason.startswith("REMOTE_ANCHORED"), (loc, reason)


@pytest.mark.parametrize("loc", ["United States", "USA", "AMER", "", "Remote - Ohio",
                                 "Tampa, Florida", "Jacksonville, FL", "San Francisco, CA; Remote (US)"])
def test_api_remote_not_anchored_kept(cfg, loc):
    kept, reason, _, _ = location(job(location=loc, workplace="REMOTE"), cfg)
    assert kept, (loc, reason)


def test_salary_below_floor_rejected(cfg):
    v = evaluate(job(description="Pay: $18 - $22 per hour"), cfg)
    assert not v.passed and v.primary == f"SALARY_BELOW_FLOOR:{22 * 2080}"


def test_hourly_above_floor_kept(cfg):
    v = evaluate(job(description="Pay: $28 - $34 per hour"), cfg)
    assert v.passed and v.salary.period == "HOUR"


def test_unknown_salary_allowed(cfg):
    assert evaluate(job(description="Competitive pay."), cfg).passed


def test_structured_salary_wins_over_text(cfg):
    s = Salary(70000, 90000, "YEAR", "$70,000 - $90,000")
    v = evaluate(job(description="Pay: $18 per hour", salary=s), cfg)
    assert v.passed and v.salary is s


@pytest.mark.parametrize("title, loc", [
    ("AI Support Engineer - Toronto", "Ontario - Remote"),
    ("Technical Support Engineer", "Remote - Estonia"),
    ("Support Engineer (EMEA - Weekends)", "Remote, Global"),
])
def test_non_us_from_title_or_location_rejected(cfg, title, loc):
    kept, reason, _, _ = location(job(title=title, location=loc, workplace="REMOTE"), cfg)
    assert not kept and reason.startswith("REMOTE_EXCLUDES_HOME"), (title, loc, reason)


@pytest.mark.parametrize("loc, elig", [
    ("Remote - New Mexico", "UNKNOWN"),
    ("Remote - Atlanta, Georgia", "UNKNOWN"),
    ("Ohio - Columbus", "UNKNOWN"),
    ("Remote, AMER", "US_WIDE"),
])
def test_us_states_are_not_foreign(cfg, loc, elig):
    _, reason, _, got = location(job(location=loc, workplace="REMOTE"), cfg)
    assert got == elig and not reason.startswith("REMOTE_EXCLUDES_HOME"), (loc, reason)


@pytest.mark.parametrize("title", [
    "Network Engineer", "Network Engineer II", "DevOps Engineer", "Site Reliability Engineer",
    "SRE, Infrastructure", "Infrastructure Automation Engineer", "Cloud Automation Engineer",
])
def test_broadened_titles_kept(cfg, title):
    assert rules.check_title(title, cfg) is None, title


@pytest.mark.parametrize("title, reason", [
    ("Distinguished Engineer, Core DevOps", "TITLE_SENIORITY"),
    ("Fellow, Site Reliability", "TITLE_SENIORITY"),
    ("Senior DevOps Engineer", "TITLE_SENIORITY"),
    ("QA Automation Engineer", "TITLE_NO_MATCH"),
    ("Test Automation Engineer", "TITLE_NO_MATCH"),
])
def test_broadened_titles_still_guarded(cfg, title, reason):
    got = rules.check_title(title, cfg)
    assert got is not None and got.split(":")[0] == reason, (title, got)


@pytest.mark.parametrize("title, loc", [
    ("Systems Engineer - Northern Germany (Hamburg, Bremen, Hannover Area)", "Germany"),
    ("Technical Support Specialist II (Backup / Restore, Networking)", "PHL Remote"),
])
def test_hunt_location_leaks_rejected(cfg, title, loc):
    kept, reason, _, _ = location(job(title=title, location=loc, workplace="REMOTE"), cfg)
    assert not kept and reason.startswith("REMOTE_EXCLUDES_HOME"), (title, reason)


@pytest.mark.parametrize("title, expect", [
    ("Site Reliability Engineer III (DBA)", "TITLE_SENIORITY"),
    ("Network Engineer IV - Data Center", "TITLE_SENIORITY"),
    ("Systems Engineer 3", "TITLE_SENIORITY"),
    ("IT Support Specialist, Tier 3 Escalations", None),
    ("Network Engineer II", None),
])
def test_level_numbers(cfg, title, expect):
    got = rules.check_title(title, cfg)
    assert (got.split(":")[0] if got else None) == expect, (title, got)


@pytest.mark.parametrize("title, loc, keep, elig", [
    ("Systems Engineer - Must Reside in Northern California", "Northern California, United States", False, "RESTRICTED_EXCLUDES_HOME"),
    ("Network Engineer (must be located in Texas)", "Remote", False, "RESTRICTED_EXCLUDES_HOME"),
    ("Systems Engineer - Must Reside in Florida", "Remote, United States", True, "HOME_OK"),
    ("Systems Engineer - must reside in Jacksonville", "Remote", True, "HOME_OK"),
    ("NOC Analyst - Must be located in the United States", "Remote", True, "US_WIDE"),
])
def test_residency_in_title(cfg, title, loc, keep, elig):
    kept, reason, _, got = location(job(title=title, location=loc, workplace="REMOTE"), cfg)
    assert (kept, got) == (keep, elig), (title, reason)


def test_residency_phrase_in_description_ignored(cfg):
    d = "You must live within commuting distance of a customer site when needed."
    kept, _, _, elig = location(job(title="Network Engineer", location="Remote - US", description=d, workplace="REMOTE"), cfg)
    assert kept and elig == "US_WIDE"


@pytest.mark.parametrize("title", [
    "Global Security Operations Center Operator", "GSOC Analyst", "SLED Systems Engineer - Minnesota",
    "Pre-Sales Systems Engineer", "Presales Network Engineer",
])
def test_non_it_precision_excludes(cfg, title):
    got = rules.check_title(title, cfg)
    assert got is not None and got.split(":")[0] in ("TITLE_NON_IT", "TITLE_NO_MATCH"), (title, got)


def test_security_operations_center_analyst_still_kept(cfg):
    assert rules.check_title("Security Operations Center Analyst", cfg) is None
    assert rules.check_title("SOC Analyst I", cfg) is None


def test_age_filter_off_by_default(cfg):
    assert cfg.max_age_days is None and evaluate(job(posted_at="2023-02-16"), cfg).passed


def test_stale_posting_rejected_when_enabled(cfg):
    p = replace(cfg, max_age_days=60)
    assert evaluate(job(), p).passed  # undated: kept
    v = evaluate(job(posted_at="2025-01-15"), p)
    assert not v.passed and v.primary == "STALE_POSTING:2025-01-15"
    assert evaluate(job(posted_at="2026-09-01"), p).passed


@pytest.mark.parametrize("loc, local", [
    ("Jacksonville, FL", True), ("Jacksonville", True), ("Jacksonville, 5201 Gate Parkway", True),
    ("St Johns, FL", True), ("Blount Island, FL", True), ("Naval Station Mayport", True),
    ("Lake City, Florida", True), ("Nocatee, FL", True), ("Kings Bay, GA", True),
    ("Thomasville, Georgia", True), ("Valdosta, GA", True),
    ("Newark; Charlotte; Jacksonville; United States of America", True),
    ("Jacksonville, NC", False), ("Jacksonville, North Carolina", False), ("Monticello, OH", False),
    ("Nassau County, NY", False), ("Jasper, NC", False), ("Valdosta, TX", False),
    ("Thomasville, PA", False), ("Austin, TX", False),
])
def test_home_area(cfg, loc, local):
    assert rules.in_home_metro(loc, cfg) is local, loc


def test_onsite_jacksonville_nc_rejected(cfg):
    kept, reason, _, _ = location(job("Systems Administrator", "Jacksonville, NC", workplace="ONSITE"), cfg)
    assert not kept and reason.startswith("LOCATION_NOT_ELIGIBLE")


@pytest.mark.parametrize("title", ["Operations Engineer", "Junior Cloud Engineer", "Cybersecurity Analyst",
                                   "Production Support Analyst"])
def test_target_titles_kept(cfg, title):
    assert rules.check_title(title, cfg) is None


@pytest.mark.parametrize("title, reason", [
    ("Snr. Site Reliability Engineer (Remote)", "TITLE_SENIORITY"),
    ("QA Testing Systems Analyst I", "TITLE_NON_IT"),
    ("Quality Assurance Testing Systems Analyst II", "TITLE_NON_IT"),
    ("Distributed Systems Engineer (Data Platform)", "TITLE_NON_IT"),
])
def test_new_title_excludes(cfg, title, reason):
    got = rules.check_title(title, cfg)
    assert got is not None and got.split(":")[0] == reason, (title, got)


def test_security_analyst_with_testing_word_not_excluded(cfg):
    assert rules.check_title("Security Analyst, Penetration Testing", cfg) is None


@pytest.mark.parametrize("title", [
    "Customer Reliability Engineer", "Implementation Engineer, Customer Support", "L2 Support Engineers",
    "Infrastructure Production Engineer", "Tech Operations Specialist", "ITSM Integration Engineer Associate",
    "Virtual Network Operator", "Infrastructure/Systems Administration", "Windows Engineer",
    "Engineer II - Virtualization (Remote)", "Onsite WAVE VoIP Engineer", "Google Cloud & Gemini Enterprise Administrator",
    "Server Onboarding Specialist", "Operating System Deployment Engineer", "MECM/MCM Engineer",
    "IT Security Administrator", "Information System Security Officer (ISSO)", "IAM Engineer",
    "Cyber Defense Engineer", "Network Security Engineer", "Cloud Security Specialist",
])
def test_off_titles_kept(cfg, title):
    assert rules.check_title(title, cfg) is None, title


@pytest.mark.parametrize("title, reason", [
    ("Embedded Linux Engineer", "TITLE_NON_IT"),
    ("Senior Windows Engineer", "TITLE_SENIORITY"),
    ("Database Administrator", "TITLE_NO_MATCH"),
    ("Solutions Consultant 2", "TITLE_NO_MATCH"),
    ("Mainframe z/OS System Programmer", "TITLE_NO_MATCH"),
    ("Field Service Engineer - Medical Imaging (Field: Cleveland, OH)", "TITLE_NO_MATCH"),
    ("RFP Administrator", "TITLE_NO_MATCH"),
    ("Security Engineer, Application Security", "TITLE_NO_MATCH"),
    ("Physical Security Application Engineer", "TITLE_NO_MATCH"),
    ("Release Train Engineer", "TITLE_NO_MATCH"),
])
def test_off_title_limits(cfg, title, reason):
    got = rules.check_title(title, cfg)
    assert got is not None and got.split(":")[0] == reason, (title, got)


# JobForge additions on top of the scanner suite.

def test_clearance_never_rejects(cfg):
    v = evaluate(job(description="Must hold an active Top Secret clearance."), cfg)
    assert v.passed and v.clearance_mention


def test_every_failed_rule_recorded_primary_first(cfg):
    v = evaluate(job(title="Senior Systems Administrator", location="Austin, TX", workplace="ONSITE",
                     description="Pay: $18 - $22 per hour"), cfg)
    assert [r.split(":")[0] for r in v.failed] == ["TITLE_SENIORITY", "LOCATION_NOT_ELIGIBLE", "SALARY_BELOW_FLOOR"]
    assert v.primary == v.failed[0]
    assert [f["category"] for f in v.reasons_json()["failed"]] == ["seniority", "location", "compensation"]


# test_salary.py, verbatim cases.

@pytest.mark.parametrize("text, lo, hi, period, open_ended", [
    ("The pay range is $85,000 - $100,000 per year.", 85000, 100000, "YEAR", False),
    ("$25 - $29 an hour", 25, 29, "HOUR", False),
    ("Pay: $25.50 to $29.75 per hour", 25.5, 29.75, "HOUR", False),
    ("From $130,000 a year", 130000, None, "YEAR", True),
    ("Up to $55,000 annually", None, 55000, "YEAR", False),
    ("Salary: $90K-$110K", 90000, 110000, "YEAR", False),
    ("Salary: $90-110K", 90000, 110000, "YEAR", False),
    ("Compensation – $72,000 – $88,000 USD", 72000, 88000, "YEAR", False),
    ("Range: $72,000—$88,000", 72000, 88000, "YEAR", False),
    ("$5,000 - $6,500 per month", 5000, 6500, "MONTH", False),
    ("Starting at $22/hr", 22, None, "HOUR", True),
    ("We pay $31 per hour.", 31, 31, "HOUR", False),
])
def test_salary_formats(text, lo, hi, period, open_ended):
    s = parse_salary(text)
    assert s is not None, text
    assert (s.min, s.max, s.period, s.open_ended) == (lo, hi, period, open_ended)


def test_salary_geo_tiers_merge_to_widest_span():
    s = parse_salary("Zone A: $95,000 - $120,000. Zone B: $80,000 - $100,000.")
    assert (s.min, s.max, s.period) == (80000, 120000, "YEAR")


@pytest.mark.parametrize("text", ["We raised $50 million in our Series B.", "Revenue of $5 billion",
                                  "$500 home office stipend", "Pay is CA$80,000 - CA$95,000",
                                  "No salary listed here.", "", None])
def test_salary_ignores_non_salary_dollars(text):
    assert parse_salary(text) is None


def test_salary_annual_max():
    assert parse_salary("$28 - $30 an hour").annual_max() == 30 * 2080
    assert parse_salary("From $50,000 per year").annual_max() is None
