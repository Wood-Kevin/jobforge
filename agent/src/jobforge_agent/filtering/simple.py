"""The search profile as a plain form (P1.8b): titles, skipped words, home state and towns, pay,
work mode, posting age. No regular expressions or JSON in front of the user.

`from_form` merges the form into the existing rules and keeps every field the form does not own,
so patterns tuned by hand (or imported from job-scanner's config.yaml) survive an edit. A title a
user types is matched as a phrase (its regex characters are escaped); a title already in the rules
is kept exactly as it was. Rejected alternative: rebuilding the rules from the form each time,
which would silently drop tuned patterns.
"""
from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

import yaml

from jobforge_agent.filtering.profile import ProfileError, compile_profile

STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine",
    "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
    "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee",
    "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}
WORK_MODE_VALUES = ("favorable", "neutral", "unfavorable")


@lru_cache(maxsize=1)
def defaults() -> dict:
    text = (resources.files("jobforge_agent.filtering") / "defaults.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _phrase(text: str) -> str:
    """A title the user typed, as a pattern that matches that phrase (case and spacing tolerant)."""
    words = [re.escape(w) for w in text.lower().split()]
    return r"\s+".join(words)


def to_plain(pattern: str) -> str | None:
    """The plain words a simple pattern stands for ("sr\\.?" -> "sr", "help\\s+desk" -> "help desk"),
    or None for a pattern with real regex logic (alternatives, groups, optional letters), which the
    form keeps as it is but does not show as editable text."""
    s = pattern.replace(r"\s+", " ").replace(r"\s*", " ").replace(r"\.?", "")
    s = re.sub(r"\\([.\-+&/' #])", r"\1", s)
    s = " ".join(s.split())
    return s if s and re.fullmatch(r"[A-Za-z0-9 .&'/+#-]+", s) else None


def _split(patterns: list[str]) -> tuple[list[str], list[str]]:
    plain, kept = [], []
    for p in patterns:
        t = to_plain(p)
        (plain if t is not None else kept).append(t if t is not None else p)
    return plain, kept


def _merge_plain(typed: list[str], existing: list[str]) -> list[str]:
    """Typed plain phrases back to patterns: one that matches an existing pattern's plain form keeps
    that pattern; a new one becomes a phrase pattern. Patterns the form cannot show are kept."""
    by_plain = {to_plain(p).lower(): p for p in existing if to_plain(p) is not None}
    kept = [p for p in existing if to_plain(p) is None]
    return list(dict.fromkeys([by_plain.get(t.lower(), _phrase(t)) for t in typed] + kept))


def _clean_list(values, field: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ProfileError(f"{field} must be a list of words or phrases")
    return [v.strip() for v in values if v.strip()]


def to_form(rules: dict | None) -> dict:
    """The form's view of a search profile (or of an empty one, with defaults filled in)."""
    r = rules or {}
    t, loc, opp = r.get("title") or {}, r.get("location") or {}, r.get("opportunity") or {}
    d = defaults()
    codes = loc.get("home_state_codes") or []
    lists = {
        "titles": list(t.get("include") or []),
        "skip_seniority": list(t.get("exclude_seniority") if "exclude_seniority" in t else d["exclude_seniority"]),
        "skip_fields": list(t.get("exclude_non_it") if "exclude_non_it" in t else d["exclude_non_it"]),
        "favorite_titles": list((opp.get("role_families") or {}).get("favorable") or []),
        "favorite_levels": list((opp.get("seniority") or {}).get("favorable") or []),
    }
    shown = {k: _split(v) for k, v in lists.items()}
    return {
        **{k: plain for k, (plain, _) in shown.items()},
        # Patterns with regex logic (e.g. from job-scanner's config.yaml): kept on save, shown read-only.
        "kept_patterns": {k: kept for k, (_, kept) in shown.items() if kept},
        # The first code is where you live; the rest are other states your towns are in.
        "home_state": codes[0] if codes and loc.get("home_markers") else "",
        "nearby_states": list(codes[1:] if codes and loc.get("home_markers") else codes),
        "home_towns": list(loc.get("home_metro") or []),
        "salary_floor": (r.get("salary") or {}).get("floor_annual") or None,
        "pay_target": opp.get("pay_target_annual"),
        "work_modes": dict(opp.get("work_modes") or {}),
        "max_age_days": (r.get("freshness") or {}).get("max_age_days"),
        "has_profile": rules is not None,
    }


def from_form(form: dict, base: dict | None) -> dict:
    """New rules: the form's fields over `base` (or over the generic defaults for a first profile)."""
    if not isinstance(form, dict):
        raise ProfileError("the form must be an object")
    d = defaults()
    rules = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (base or {}).items()}
    t = dict(rules.get("title") or {})
    loc = dict(rules.get("location") or {})
    opp = dict(rules.get("opportunity") or {})

    titles = _clean_list(form.get("titles"), "Job titles")
    if not titles and not [p for p in (t.get("include") or []) if to_plain(p) is None]:
        raise ProfileError("Add at least one job title you are looking for")
    t["include"] = _merge_plain(titles, list(t.get("include") or []))
    t["exclude_seniority"] = _merge_plain(_clean_list(form.get("skip_seniority"), "Seniority words to skip"),
                                          list(t["exclude_seniority"] if "exclude_seniority" in t else d["exclude_seniority"]))
    t["exclude_non_it"] = _merge_plain(_clean_list(form.get("skip_fields"), "Fields to skip"),
                                       list(t["exclude_non_it"] if "exclude_non_it" in t else d["exclude_non_it"]))
    t.setdefault("seniority_exceptions", [])

    state = str(form.get("home_state") or "").strip().upper()
    if state and state not in STATE_NAMES:
        raise ProfileError("Home state must be a two-letter US state code, like FL")
    # Other states the home towns are in (Live Oak's area reaches Valdosta, GA). They only keep "Valdosta, GA"
    # from reading as another state; residence ("Remote - Florida", "cannot hire in Florida") stays the home
    # state alone. Rejected: one list of home states, which would make "Remote - Georgia" count as home.
    nearby = [s.upper() for s in _clean_list(form.get("nearby_states"), "Other states your towns are in")]
    if any(s not in STATE_NAMES for s in nearby):
        raise ProfileError("Other states your towns are in must be two-letter US state codes, like GA")
    codes = list(dict.fromkeys(([state] if state else []) + nearby))
    towns = _clean_list(form.get("home_towns"), "Home towns")
    for k in ("us_markers", "us_state_markers", "non_us_location_markers", "description_restricted_markers", "residency_markers"):
        loc.setdefault(k, list(d[k]))
    loc["home_metro"] = towns
    loc["home_state_codes"] = codes
    loc["home_state_names"] = [STATE_NAMES[c] for c in codes]
    if state:
        name = STATE_NAMES[state]
        loc["home_markers"] = [re.escape(name), state.lower()]
        loc["home_excluded_markers"] = [
            r"(excluding|except|not (able to )?hire in|cannot hire in|unable to hire in|not available (to residents of|in))"
            rf"[^.]{{0,80}}{re.escape(name)}"]
    else:
        loc["home_markers"], loc["home_excluded_markers"] = [], []

    floor = form.get("salary_floor")
    rules["salary"] = {"floor_annual": float(floor) if floor not in (None, "") else 0.0}
    age = form.get("max_age_days")
    rules["freshness"] = {"max_age_days": int(age) if age not in (None, "", 0) else None}

    target = form.get("pay_target")
    if target not in (None, ""):
        opp["pay_target_annual"] = float(target)
    else:
        opp.pop("pay_target_annual", None)
    modes = form.get("work_modes") or {}
    if not isinstance(modes, dict) or any(k not in ("remote", "hybrid", "onsite") or v not in WORK_MODE_VALUES
                                          for k, v in modes.items() if v):
        raise ProfileError("Work modes must each be liked, fine or disliked")
    opp["work_modes"] = {k: v for k, v in modes.items() if v}
    fav_titles = _clean_list(form.get("favorite_titles"), "Favorite job titles")
    fav_levels = _clean_list(form.get("favorite_levels"), "Favorite levels")
    opp["role_families"] = {**(opp.get("role_families") or {}), "favorable": _merge_plain(
        fav_titles, list((opp.get("role_families") or {}).get("favorable") or []))}
    opp["seniority"] = {"favorable": _merge_plain(fav_levels, list((opp.get("seniority") or {}).get("favorable") or [])),
                        "unfavorable": list(t["exclude_seniority"])}

    rules["title"], rules["location"], rules["opportunity"] = t, loc, opp
    rules["source"] = rules.get("source") or "first-run setup"
    compile_profile(rules)  # raises ProfileError naming the field
    return rules
