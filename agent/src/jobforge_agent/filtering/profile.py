"""Search profile rules: shape, validation, compilation, and import from the scanner's config.yaml.

A search profile is versioned and insert-only (search_profiles). Its `rules` JSON keeps the
scanner's config.yaml structure so tuned rules carry over unchanged. Two things the
scanner hard-coded become profile fields so the engine is not tied to one user:
  location.jax_metro           -> location.home_metro
  FL/GA codes, florida/georgia -> location.home_state_codes / home_state_names
  florida_markers              -> location.home_markers
  florida_excluded_markers     -> location.home_excluded_markers
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TITLE_KEYS = ("include", "exclude_seniority", "seniority_exceptions", "exclude_non_it")
LOCATION_LIST_KEYS = ("home_metro", "us_markers", "us_state_markers", "home_markers", "non_us_location_markers",
                      "description_restricted_markers", "home_excluded_markers", "residency_markers")


class ProfileError(ValueError):
    pass


def norm(text: str | None) -> str:
    """Lower case; every run of non-alphanumerics becomes one space (scanner text.norm, edd1528)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def compile_terms(patterns: list[str] | None, where: str) -> list[re.Pattern]:
    """Each pattern as a whole word/phrase, case-insensitive (scanner config.compile_terms)."""
    out = []
    for p in patterns or []:
        try:
            out.append(re.compile(rf"(?<!\w)(?:{p})(?!\w)", re.I))
        except re.error as e:
            raise ProfileError(f"bad regex in {where}: {p!r}: {e}") from e
    return out


@dataclass
class CompiledProfile:
    title_include: list[re.Pattern] = field(default_factory=list)
    title_seniority: list[re.Pattern] = field(default_factory=list)
    title_exceptions: list[re.Pattern] = field(default_factory=list)
    title_non_it: list[re.Pattern] = field(default_factory=list)
    home_metro: list[str] = field(default_factory=list)  # normalized phrases
    home_state_codes: frozenset[str] = frozenset()
    home_state_names: frozenset[str] = frozenset()
    us_markers: list[re.Pattern] = field(default_factory=list)
    us_states: list[re.Pattern] = field(default_factory=list)
    home_markers: list[re.Pattern] = field(default_factory=list)
    non_us_markers: list[re.Pattern] = field(default_factory=list)
    desc_restricted: list[re.Pattern] = field(default_factory=list)
    home_excluded: list[re.Pattern] = field(default_factory=list)
    residency: list[re.Pattern] = field(default_factory=list)
    floor_annual: float = 0.0
    max_age_days: int | None = None


def compile_profile(rules: dict) -> CompiledProfile:
    """Validate and compile. Raises ProfileError naming the bad field, so a typo fails at save time."""
    if not isinstance(rules, dict):
        raise ProfileError("rules must be an object")
    t, loc = rules.get("title") or {}, rules.get("location") or {}
    for name, section in (("title", t), ("location", loc), ("salary", rules.get("salary") or {}),
                          ("freshness", rules.get("freshness") or {})):
        if not isinstance(section, dict):
            raise ProfileError(f"{name} must be an object")
    age = (rules.get("freshness") or {}).get("max_age_days")
    if age is not None and (not isinstance(age, int) or isinstance(age, bool) or age < 1):
        raise ProfileError("freshness.max_age_days must be a positive whole number or null")
    if not t.get("include"):
        raise ProfileError("title.include must list at least one pattern")
    for k in TITLE_KEYS:
        if not isinstance(t.get(k, []), list):
            raise ProfileError(f"title.{k} must be a list")
    for k in LOCATION_LIST_KEYS:
        if not isinstance(loc.get(k, []), list):
            raise ProfileError(f"location.{k} must be a list")
    for p in loc.get("residency_markers") or []:
        if "(?P<place>" not in p:
            raise ProfileError(f"location.residency_markers pattern needs a (?P<place>...) group: {p!r}")
    validate_opportunity(rules.get("opportunity"))
    floor = (rules.get("salary") or {}).get("floor_annual", 0)
    try:
        floor = float(floor or 0)
    except (TypeError, ValueError) as e:
        raise ProfileError("salary.floor_annual must be a number") from e
    return CompiledProfile(
        title_include=compile_terms(t.get("include"), "title.include"),
        title_seniority=compile_terms(t.get("exclude_seniority"), "title.exclude_seniority"),
        title_exceptions=compile_terms(t.get("seniority_exceptions"), "title.seniority_exceptions"),
        title_non_it=compile_terms(t.get("exclude_non_it"), "title.exclude_non_it"),
        home_metro=[norm(m) for m in loc.get("home_metro", []) if norm(m)],
        home_state_codes=frozenset(str(c).upper() for c in loc.get("home_state_codes", [])),
        home_state_names=frozenset(str(n).lower() for n in loc.get("home_state_names", [])),
        us_markers=compile_terms(loc.get("us_markers"), "location.us_markers"),
        us_states=compile_terms(loc.get("us_state_markers"), "location.us_state_markers"),
        home_markers=compile_terms(loc.get("home_markers"), "location.home_markers"),
        non_us_markers=compile_terms(loc.get("non_us_location_markers"), "location.non_us_location_markers"),
        desc_restricted=compile_terms(loc.get("description_restricted_markers"), "location.description_restricted_markers"),
        home_excluded=compile_terms(loc.get("home_excluded_markers"), "location.home_excluded_markers"),
        residency=compile_terms(loc.get("residency_markers"), "location.residency_markers"),
        floor_annual=floor,
        max_age_days=(rules.get("freshness") or {}).get("max_age_days"),
    )


OPPORTUNITY_VALUES = ("favorable", "neutral", "unfavorable")


def validate_opportunity(o) -> None:
    """Optional `opportunity` section read by opportunity value (ARCHITECTURE 6.2, 6.5; Q8). The
    filter ignores it. Missing parts make that factor `unknown`, never a guess:
      pay_target_annual: number
      work_modes: {remote|hybrid|onsite: favorable|neutral|unfavorable}
      seniority, role_families: {favorable: [title patterns], unfavorable: [title patterns]}"""
    if o is None:
        return
    if not isinstance(o, dict):
        raise ProfileError("opportunity must be an object")
    unknown = set(o) - {"pay_target_annual", "work_modes", "seniority", "role_families"}
    if unknown:
        raise ProfileError(f"opportunity does not take {', '.join(sorted(unknown))}")
    t = o.get("pay_target_annual")
    if t is not None and (isinstance(t, bool) or not isinstance(t, (int, float)) or t <= 0):
        raise ProfileError("opportunity.pay_target_annual must be a positive number")
    modes = o.get("work_modes") or {}
    if not isinstance(modes, dict) or set(modes) - {"remote", "hybrid", "onsite"} or any(v not in OPPORTUNITY_VALUES for v in modes.values()):
        raise ProfileError("opportunity.work_modes maps remote/hybrid/onsite to favorable/neutral/unfavorable")
    for key in ("seniority", "role_families"):
        spec = o.get(key) or {}
        if not isinstance(spec, dict) or set(spec) - {"favorable", "unfavorable"}:
            raise ProfileError(f"opportunity.{key} takes favorable and unfavorable pattern lists")
        for side, patterns in spec.items():
            if not isinstance(patterns, list):
                raise ProfileError(f"opportunity.{key}.{side} must be a list")
            compile_terms(patterns, f"opportunity.{key}.{side}")


def from_scanner_config(raw: dict) -> dict:
    """Convert job-scanner's config.yaml (edd1528) into profile rules. The scanner hard-coded
    Florida/Georgia as the home states; they are written out explicitly here."""
    t, loc = raw.get("title") or {}, raw.get("location") or {}
    return {
        "title": {k: list(t.get(k) or []) for k in TITLE_KEYS},
        "location": {
            "home_metro": list(loc.get("jax_metro") or []),
            "home_state_codes": ["FL", "GA"],
            "home_state_names": ["florida", "georgia"],
            "us_markers": list(loc.get("us_markers") or []),
            "us_state_markers": list(loc.get("us_state_markers") or []),
            "home_markers": list(loc.get("florida_markers") or []),
            "non_us_location_markers": list(loc.get("non_us_location_markers") or []),
            "description_restricted_markers": list(loc.get("description_restricted_markers") or []),
            "home_excluded_markers": list(loc.get("florida_excluded_markers") or []),
            "residency_markers": list(loc.get("residency_markers") or []),
        },
        "salary": {"floor_annual": float((raw.get("salary") or {}).get("floor_annual", 0) or 0)},
        "freshness": {"max_age_days": (raw.get("freshness") or {}).get("max_age_days")},
        "source": "imported from job-scanner config.yaml",
    }
