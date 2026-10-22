"""Deterministic filter rules (ported from ats_scanner/filters.py, edd1528).

Behavior is the scanner's, rule for rule, so its tuned results carry over. Differences:
- rules come from a compiled search profile instead of config.yaml; "Jacksonville" and FL/GA
  are profile values (home_metro, home_state_codes/names);
- one call returns the primary reason (which alone decides pass or reject, as the scanner's
  pass1/pass2 did) plus every other rule the posting fails, judged independently;
- work_mode uses JobForge's lower-case values (remote / hybrid / onsite / unknown).

RULESET_VERSION names this code's behavior. Bump it whenever a rule changes meaning, so
filter_results written by the old rules stay distinguishable from new ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from jobforge_agent.filtering.profile import CompiledProfile, norm
from jobforge_agent.filtering.salary import Salary, parse_salary

RULESET_VERSION = "rules-1"

_REMOTE_WORD = re.compile(r"(?<!\w)(remote|anywhere|distributed|work from home|wfh)(?!\w)", re.I)
_HYBRID_WORD = re.compile(r"(?<!\w)hybrid(?!\w)", re.I)
# Two-letter state codes after a comma ("Jacksonville, NC"). Upper case only, so "in"/"or" never count.
_STATE_CODE = re.compile(r"(?:^|,)\s*(A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b")
_CLEARANCE = re.compile(
    r"\b(active|current|must (have|hold|possess|obtain)|requires?d?|eligib\w+ (for|to obtain))\b[^.\n]{0,60}"
    r"\b(secret|top secret|ts/sci|security clearance|public trust)\b", re.I)

# Reason prefix -> category. A reason with no entry is "other", so a new rule never vanishes from counts.
CATEGORY = {
    "TITLE_NO_MATCH": "title_family", "TITLE_NON_IT": "title_family",
    "TITLE_SENIORITY": "seniority",
    "LOCATION_NOT_ELIGIBLE": "location", "REMOTE_EXCLUDES_HOME": "location", "REMOTE_ANCHORED": "location",
    "SALARY_BELOW_FLOOR": "compensation",
    "STALE_POSTING": "other",
}


def category(reason: str) -> str:
    return CATEGORY.get(reason.split(":", 1)[0], "other")


@dataclass
class JobView:
    """What the filter reads, built from jobs + the current raw posting."""
    title: str
    location_text: str
    description: str
    work_mode: str | None = None          # from the source API when it said so
    salary: Salary | None = None          # structured salary from the source API
    posted_at: str | None = None


@dataclass
class FilterOutcome:
    passed: bool
    primary: str | None                   # the deciding rejection reason, None when passed
    failed: list[str] = field(default_factory=list)  # every rule failed, primary first
    work_mode: str = "unknown"
    remote_eligibility: str = "UNKNOWN"
    salary: Salary | None = None
    clearance_mention: bool = False

    def reasons_json(self) -> dict:
        s = self.salary
        return {
            "primary": self.primary,
            "failed": [{"rule": r.split(":", 1)[0], "category": category(r), "detail": r.split(":", 1)[1] if ":" in r else None}
                       for r in self.failed],
            "work_mode": self.work_mode,
            "remote_eligibility": self.remote_eligibility,
            "salary": {"min": s.min, "max": s.max, "period": s.period, "annual_max": s.annual_max(), "text": s.text} if s else None,
            "clearance_mention": self.clearance_mention,
        }


def _first(patterns: list[re.Pattern], text: str) -> str | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(0)
    return None


def check_title(title: str, p: CompiledProfile) -> str | None:
    if not _first(p.title_include, title):
        return "TITLE_NO_MATCH"
    hit = _first(p.title_seniority, title)
    if hit and not _first(p.title_exceptions, title):
        return f"TITLE_SENIORITY:{hit.strip().lower()}"
    hit = _first(p.title_non_it, title)
    if hit:
        return f"TITLE_NON_IT:{hit.strip().lower()}"
    return None


def work_mode(job: JobView) -> str:
    # The API's own field beats guessing from the location string.
    if job.work_mode in ("remote", "hybrid", "onsite"):
        return job.work_mode
    loc = job.location_text or ""
    if _HYBRID_WORD.search(loc):
        return "hybrid"
    if _REMOTE_WORD.search(loc):
        return "remote"
    return "onsite" if loc.strip() else "unknown"


def _names_other_state(segment: str, p: CompiledProfile) -> bool:
    if any(code not in p.home_state_codes for code in _STATE_CODE.findall(segment)):
        return True
    hit = _first(p.us_states, segment)
    return bool(hit) and hit.strip().lower() not in p.home_state_names


def in_home_metro(location: str, p: CompiledProfile) -> bool:
    """Normalized metro phrase inside a location segment, unless that segment names another state:
    "Jacksonville, NC" (Camp Lejeune) is not Jacksonville, FL."""
    for seg in re.split(r"[;|]", location or ""):
        n = f" {norm(seg)} "
        if any(f" {m} " in n for m in p.home_metro) and not _names_other_state(seg, p):
            return True
    return False


def remote_eligibility(job: JobView, p: CompiledProfile) -> tuple[str, str | None]:
    """(eligibility, evidence). Title + location decide first; the description is only searched for
    explicit phrases such as "LATAM only" or "cannot hire in Florida"."""
    loc = job.location_text or ""
    place = f"{job.title} ; {loc}"
    desc = job.description or ""
    hit = _first(p.home_excluded, loc) or _first(p.home_excluded, desc)
    if hit:
        return "RESTRICTED_EXCLUDES_HOME", hit
    # "Must Reside in Northern California" in a title or location is a hard residency rule.
    for rx in p.residency:
        m = rx.search(place)
        if m:
            where = m.group("place").strip()
            if _first(p.home_markers, where) or in_home_metro(where, p):
                return "HOME_OK", None
            if not _first(p.us_markers, where):  # "must reside in the US" is not a restriction
                return "RESTRICTED_EXCLUDES_HOME", m.group(0).strip()
    if _first(p.home_markers, loc) or in_home_metro(loc, p):
        return "HOME_OK", None
    us = _first(p.us_markers, place)
    state = _first(p.us_states, place)
    non_us = _first(p.non_us_markers, place)
    if non_us and not us and not state:
        return "RESTRICTED_EXCLUDES_HOME", non_us
    hit = _first(p.desc_restricted, desc)
    if hit and not us:
        return "RESTRICTED_EXCLUDES_HOME", hit
    if us:
        return "US_WIDE", None
    return "UNKNOWN", None


def names_specific_place(location: str, p: CompiledProfile) -> bool:
    """Something more specific than a country remains once country words are removed."""
    rest = location
    for rx in p.us_markers:
        rest = rx.sub(" ", rest)
    return bool(re.sub(r"[\W_]+", "", rest))


def check_location(job: JobView, p: CompiledProfile) -> tuple[str | None, str, str]:
    mode = work_mode(job)
    elig, evidence = remote_eligibility(job, p)
    loc = job.location_text or ""
    metro = in_home_metro(loc, p)
    if mode == "remote":
        if elig == "RESTRICTED_EXCLUDES_HOME":
            return f"REMOTE_EXCLUDES_HOME:{evidence}", mode, elig
        # Feed says remote but the location is one non-home place with no remote wording
        # (Veeva "Massachusetts - Boston"): the job is anchored there.
        if elig != "HOME_OK" and not metro and not _REMOTE_WORD.search(loc) and names_specific_place(loc, p):
            return f"REMOTE_ANCHORED:{loc[:60]}", mode, elig
        return None, mode, elig
    if metro:
        return None, mode, elig
    # Multi-location postings ("Austin, TX; Remote, US"): the remote option is the one that matters.
    if _REMOTE_WORD.search(loc) and elig != "RESTRICTED_EXCLUDES_HOME":
        return None, "remote", elig
    if mode == "unknown":
        return None, mode, elig
    return f"LOCATION_NOT_ELIGIBLE:{loc[:60] or 'none'}", mode, elig


def mentions_clearance(job: JobView) -> bool:
    """Diagnostic only: never a rejection (Kevin held a clearance; the evaluation judges it)."""
    return bool(_CLEARANCE.search(job.description or ""))


def evaluate(job: JobView, p: CompiledProfile, today: date | None = None) -> FilterOutcome:
    """Primary reason in pipeline order (title, location, staleness, salary), plus every other failure."""
    failed: list[str] = []
    t = check_title(job.title, p)
    if t:
        failed.append(t)
        if t == "TITLE_NO_MATCH":
            hit = _first(p.title_seniority, job.title)
            if hit and not _first(p.title_exceptions, job.title):
                failed.append(f"TITLE_SENIORITY:{hit.strip().lower()}")
    loc, mode, elig = check_location(job, p)
    if loc:
        failed.append(loc)
    if p.max_age_days and job.posted_at:
        try:
            posted = date.fromisoformat(job.posted_at[:10])
        except ValueError:
            posted = None
        if posted and posted < (today or date.today()) - timedelta(days=int(p.max_age_days)):
            failed.append(f"STALE_POSTING:{posted.isoformat()}")
    sal = job.salary or parse_salary(job.description)
    if sal is not None and p.floor_annual:
        top = sal.annual_max()
        if top is not None and top < p.floor_annual:
            failed.append(f"SALARY_BELOW_FLOOR:{int(top)}")
    return FilterOutcome(passed=not failed, primary=failed[0] if failed else None, failed=failed, work_mode=mode,
                         remote_eligibility=elig, salary=sal, clearance_mention=mentions_clearance(job))
