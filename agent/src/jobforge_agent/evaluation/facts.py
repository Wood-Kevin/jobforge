"""Eligibility requirements matched against declared facts by code, never by the model (ARCHITECTURE 6.4 E3).

Every requirement with a code-assigned eligibility type (E2 check 6) is checked here, hard or
not: a model reading free text could "cover" citizenship with a résumé line, which is exactly
what typed facts exist to prevent (P1.5). Outcomes:

- met: the fact satisfies the requirement as written.
- fails: the fact definitively does not (Kevin in Florida, posting requires Pacific time).
- unanswered: no fact of that type, a missing field, or wording code cannot read safely
  ("travel as needed", "may sponsor existing visa transfers"). Each carries a question.

Only a fails on a *verified* fact can block (6.5); everything else here is at most UNKNOWN.
Completeness: with `eligibility_facts_complete` on, a missing clearance, license or program fact
means "I do not have it" (fails, basis none). Basis none never blocks, so for a hard requirement
this is still UNKNOWN; it turns a negotiable eligibility row into a gap instead of unknown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobforge_agent.analysis.segment import norm
from jobforge_agent.evaluation.snapshot import Item

STATES = {
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
_NAME_TO_CODE = {v: k for k, v in STATES.items()}
# US time zones: states entirely in a zone, and states with any part in it. A state only partly
# in the zone (Florida's panhandle is Central) cannot be decided without the city: unanswered.
_ZONES = {
    "pacific": ({"CA", "WA", "NV"}, {"CA", "WA", "NV", "OR", "ID"}),
    "mountain": ({"CO", "MT", "NM", "UT", "WY", "AZ"}, {"CO", "MT", "NM", "UT", "WY", "AZ", "ID", "OR", "NE", "KS", "ND", "SD", "TX", "NV"}),
    "central": ({"AL", "AR", "IL", "IA", "LA", "MN", "MS", "MO", "OK", "WI"},
                {"AL", "AR", "IL", "IA", "LA", "MN", "MS", "MO", "OK", "WI", "FL", "IN", "KS", "KY", "MI", "NE", "ND", "SD", "TN", "TX"}),
    "eastern": ({"CT", "DE", "GA", "ME", "MD", "MA", "NH", "NJ", "NY", "NC", "OH", "PA", "RI", "SC", "VT", "VA", "WV", "DC"},
                {"CT", "DE", "GA", "ME", "MD", "MA", "NH", "NJ", "NY", "NC", "OH", "PA", "RI", "SC", "VT", "VA", "WV", "DC",
                 "FL", "IN", "KY", "MI", "TN"}),
}
# "us" alone is usually the pronoun ("join us"): it counts only in country phrases.
_US = re.compile(r"\bu\.s\.(a\.)?|\busa\b|\bunited states\b|\bamerica\b|\b(in|within|across|throughout|the) us\b"
                 r"|\bus[- ](based|citizens?|persons?|only|residents?|time zones?)\b")
_OTHER_COUNTRY = re.compile(r"\b(canada|(?<!new )mexico|united kingdom|u\.?k\.?|england|ireland|germany|france|spain|portugal|netherlands"
                            r"|poland|india|philippines|brazil|argentina|colombia|australia|new zealand|japan|singapore|israel"
                            r"|latam|emea|apac|europe|eu)\b")
# A time zone is a place only when the wording says to be located or live in it; "work Eastern
# Time hours" is a schedule (independent P1.7 review).
_ZONE = re.compile(r"\b(?:located|reside|residing|resident|based|live|living)\s+(?:(?:in|within)\s+)?(?:the\s+)?"
                   r"(?:(pacific|mountain|central|eastern) (?:standard )?time(?: zone)?|(pst|pt|mst|mt|cst|ct|est|et) (?:time )?zone)\b")
_ZONE_ABBR = {"pst": "pacific", "pt": "pacific", "mst": "mountain", "mt": "mountain", "cst": "central", "ct": "central",
              "est": "eastern", "et": "eastern"}
# "El Dorado Hills, CA": case-sensitive on the original quote. In lower-cased text only codes
# that are not English words count ("skills, in" is not Indiana).
_CITY_STATE = re.compile(r"\b[A-Z][A-Za-z .'-]*, ([A-Z]{2})\b")
_CITY_STATE_LOWER = re.compile(r"\b[a-z][a-z .'-]*, ([a-z]{2})\b")
_WORD_CODES = {"IN", "OR", "ME", "HI", "OK", "OH", "ID", "AL", "LA", "DE", "CO", "PA", "MA", "MT", "MD"}
_EXCLUSION = re.compile(r"\b(excluding|except|exclud\w*|other than|outside|not (?:open|available|eligible) (?:to|for|in)"
                        r"|not (?:located|based|residing|living) in"
                        r"|unable to (?:hire|employ|consider|accept)|cannot (?:hire|employ|consider|accept)|can't (?:hire|employ)"
                        r"|do(?:es)? not (?:hire|employ|consider|accept))\b")
_LEVELS = ["none", "confidential", "secret", "top_secret", "ts_sci"]
_NEGATIVE = re.compile(r"\b(no|not|unable to|cannot|can't|won't|will not|do not|does not|don't|doesn't|aren't|isn't|without)\b"
                       r"[^.;]{0,30}\b(sponsor\w*|visas?)\b|\bsponsorship (is |are )?(not |un)(available|offered|provided|possible)\b"
                       r"|\bno (visa )?sponsorship\b")
_INELIGIBLE = re.compile(r"\b(sponsor\w*|visas?)\b[^.;]{0,40}\b(ineligible|not eligible|not be considered|disqualif\w*)\b"
                         r"|\bmust not (?:need|require)\b[^.;]{0,30}\b(sponsor\w*|visas?)\b")
# An offer has the employer as its subject ("we do sponsor visas!"); "you will require
# sponsorship" is the candidate's need, not an offer.
_SPONSORS = re.compile(r"\bwe\b[^.;]{0,20}\bsponsor\w*|\b(?:offers?|provides?) (?:visa )?sponsorship\b"
                       r"|\bsponsorship (?:is )?(?:available|offered|provided|possible)\b|\bvisa support\b")
_HEDGE = re.compile(r"\b(every|all roles|some|may|might|discretion|generally|case[- ]by[- ]case|existing|transfers?)\b")
_POUNDS = re.compile(r"\b(\d{1,3})\s*(?:lbs?|pounds)\b")
_STAND = re.compile(r"\bstand(?:ing)? for (?:up to )?(\d{1,2}) ?(?:\+ )?hours?\b")
_PERCENT = re.compile(r"\btravel\w*\b[^.;%]{0,30}?\b(\d{1,3})(?: ?%)?(?: ?(?:-|to) ?(\d{1,3}))? ?%"
                      r"|\b(\d{1,3})(?: ?%)?(?: ?(?:-|to) ?(\d{1,3}))? ?% (?:of the time )?(?:travel|on the road)")
_OCCASIONAL = re.compile(r"\b(occasional(ly)?|infrequent(ly)?|minimal|periodic(ally)?|rare(ly)?|as needed)\b")
_GENERIC_LICENSE_WORDS = {"license", "licence", "licensed", "valid", "state", "must", "have", "hold", "current", "active", "required"}


@dataclass
class FactCheck:
    outcome: str                      # met / fails / unanswered
    basis: str                        # verified / self_reported / none (of the fact items used)
    item_ids: list[int] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    question: str | None = None

    def to_flag(self) -> dict:
        return {"flag": "eligibility_check", "outcome": self.outcome, **self.detail}


@dataclass(frozen=True)
class Req:
    """The requirement fields E3-E6 read (one `requirements` row)."""
    id: int
    text: str
    jd_quote: str
    sentence: str | None
    eligibility_type: str | None
    final_class: str
    interpretation_status: str
    origin: str
    cue_polarity: str | None
    min_years: float | None
    section_label: str | None
    flags: tuple = ()

    def has_flag(self, name: str) -> bool:
        return any(f.get("flag") == name for f in self.flags)


def _basis(items: list[Item]) -> str:
    if not items:
        return "none"
    return "verified" if all(i.status == "verified" for i in items) else "self_reported"


def _state_code(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if v.upper() in STATES:
        return v.upper()
    return _NAME_TO_CODE.get(v.lower())


def _is_us(country: str | None) -> bool:
    # Free text: "US", "U.S", "USA", "United States of America", "United States (US)", "America".
    letters = re.sub(r"[^a-z]", "", (country or "").lower())
    return letters in ("us", "usa", "america") or letters.startswith("unitedstates")


def _facts_of(facts: list[Item], kind: str) -> list[Item]:
    return [f for f in facts if f.attributes.get("type") == kind]


def _text(req: Req) -> str:
    # The model's quote can be cut short ("...Maryland, Massachu"); the sentence has the full list.
    return norm(f"{req.jd_quote} {req.sentence or ''}")


def _absent(kind: str, completeness: dict, question: str) -> FactCheck:
    if completeness.get("eligibility_facts_complete") and kind in ("clearance", "license", "program_status"):
        return FactCheck("fails", "none", [], {"reason": "no_fact_and_profile_complete"}, question)
    return FactCheck("unanswered", "none", [], {"reason": f"no_{kind}_fact"}, question)


def check(req: Req, facts: list[Item], completeness: dict, siblings: list[Req] = ()) -> FactCheck:
    """Match one eligibility requirement against the declared facts. `siblings` are the other
    requirements of the same posting (an "ability to obtain" alternative elsewhere means a held-
    clearance requirement is not a definitive fail)."""
    kind = req.eligibility_type
    fn = _CHECKS.get(kind or "")
    if fn is None:
        return FactCheck("unanswered", "none", [], {"reason": "no_eligibility_type"}, None)
    return fn(req, facts, completeness, list(siblings))


def _work_authorization(req, facts, completeness, siblings):
    t = _text(req)
    q = "Are you a US citizen / authorized to work in the US? Add a work_authorization fact."
    if _OTHER_COUNTRY.search(t) and not _US.search(t):
        return FactCheck("unanswered", "none", [], {"reason": "non_us_work_authorization"},
                         f"This posting asks about work authorization outside the US: \"{req.jd_quote[:120]}\". Does it apply to you?")
    fs = _facts_of(facts, "work_authorization")
    if not fs:
        return _absent("work_authorization", completeness, q)
    f = fs[0]
    citizen_only = "citizen" in t and not re.search(r"permanent residen|green card|lawful|authori[sz]ed", t)
    field_ = "us_citizen" if citizen_only else "authorized_to_work_us"
    ok = bool(f.attributes.get(field_)) or (field_ == "authorized_to_work_us" and bool(f.attributes.get("us_citizen")))
    return FactCheck("met" if ok else "fails", _basis([f]), [f.id], {"needs": field_, "has": bool(f.attributes.get(field_))},
                     None if ok else q)


def _sponsorship(req, facts, completeness, siblings):
    q = "Will you need visa sponsorship now or in the future? Add a sponsorship fact."
    fs = _facts_of(facts, "sponsorship")
    if not fs:
        return _absent("sponsorship", completeness, q)
    f = fs[0]
    if not f.attributes.get("needs_sponsorship"):
        return FactCheck("met", _basis([f]), [f.id], {"needs_sponsorship": False})
    t = _text(req)
    if _HEDGE.search(t):
        return FactCheck("unanswered", _basis([f]), [f.id], {"needs_sponsorship": True, "reason": "hedged_wording"},
                         f"You need sponsorship; the posting hedges: \"{req.jd_quote[:120]}\". Ask the employer.")
    if _NEGATIVE.search(t) or _INELIGIBLE.search(t):
        return FactCheck("fails", _basis([f]), [f.id], {"needs_sponsorship": True, "posting": "no_sponsorship"})
    if _SPONSORS.search(t):
        return FactCheck("met", _basis([f]), [f.id], {"needs_sponsorship": True, "posting": "sponsors"})
    # Neither an offer nor a refusal ("Please indicate whether you will require sponsorship"):
    # a question, never a default "met" (Codex adversarial review, P1.7).
    return FactCheck("unanswered", _basis([f]), [f.id], {"needs_sponsorship": True, "reason": "wording_not_readable"},
                     f"You need sponsorship; does this posting offer it? \"{req.jd_quote[:120]}\"")


def _required_level(t: str) -> str:
    if re.search(r"\bts ?/ ?sci\b|\bts-sci\b|\bsci\b", t):
        return "ts_sci"
    if "top secret" in t or re.search(r"\bts\b", t):
        return "top_secret"
    if re.search(r"\bsecret\b", t):
        return "secret"
    if "confidential" in t:
        return "confidential"
    return "confidential"  # "active clearance required" with no level: any clearance


def _clearance(req, facts, completeness, siblings):
    t = _text(req)
    if "public trust" in t and not re.search(r"\b(secret|ts|sci|confidential)\b", t):
        return FactCheck("unanswered", "none", [], {"reason": "public_trust_not_a_clearance_level"},
                         "The posting asks for a Public Trust determination. Do you hold or can you obtain one?")
    need = _required_level(t)
    ability = req.has_flag("ability_to_obtain")
    q = (f"Can you obtain a {need.replace('_', ' ')} clearance?" if ability else
         f"Do you hold an active {need.replace('_', ' ')} clearance? Add or verify your clearance fact.")
    fs = _facts_of(facts, "clearance")
    if not fs:
        if ability:  # completeness means "I hold none", which does not answer "can you obtain one"
            return FactCheck("unanswered", "none", [], {"reason": "no_clearance_fact", "ability_to_obtain": True}, q)
        return _absent("clearance", completeness, q)
    f = fs[0]
    level, status = f.attributes.get("level", "none"), f.attributes.get("status", "none")
    level_ok = _LEVELS.index(level) >= _LEVELS.index(need) if level in _LEVELS else False
    detail = {"needs_level": need, "has_level": level, "has_status": status, "ability_to_obtain": ability}
    if ability:
        # Held at that level before (any status) is the strongest evidence of obtainability.
        if level_ok:
            return FactCheck("met", _basis([f]), [f.id], detail)
        return FactCheck("unanswered", _basis([f]), [f.id], {**detail, "reason": "obtainability_not_declared"}, q)
    poly = re.search(r"\bpolygraph\b|\bpoly\b", t)
    if poly:
        need_poly = "full_scope" if re.search(r"full[- ]scope|\bfsp\b|lifestyle", t) else "ci"
        has_poly = f.attributes.get("polygraph")
        detail.update(needs_polygraph=need_poly, has_polygraph=has_poly)
        if has_poly is None:
            return FactCheck("unanswered", _basis([f]), [f.id], {**detail, "reason": "polygraph_not_declared"},
                             "The posting requires a polygraph. Add it to your clearance fact.")
        if has_poly == "none" or (need_poly == "full_scope" and has_poly != "full_scope"):
            return FactCheck("fails", _basis([f]), [f.id], detail, q)
    held = status in ("active", "current")
    if held and level_ok:
        return FactCheck("met", _basis([f]), [f.id], detail)
    if any(s.id != req.id and s.eligibility_type == "clearance" and s.has_flag("ability_to_obtain") for s in siblings):
        # The posting also accepts "ability to obtain": a lapsed clearance is not a definitive fail.
        return FactCheck("unanswered", _basis([f]), [f.id], {**detail, "reason": "posting_also_accepts_ability_to_obtain"}, q)
    return FactCheck("fails", _basis([f]), [f.id], detail, q)


def _places(t: str, original: str) -> dict:
    states = set()
    rest = t.replace("washington, dc", " ").replace("washington dc", " ")
    for code, name in sorted(STATES.items(), key=lambda kv: -len(kv[1])):  # "west virginia" before "virginia"
        if re.search(rf"\b{name}\b", rest):
            states.add(code)
            rest = re.sub(rf"\b{name}\b", " ", rest)
    for m in _CITY_STATE.finditer(original):
        if m.group(1) in STATES:
            states.add(m.group(1))
    for m in _CITY_STATE_LOWER.finditer(t):
        if m.group(1).upper() in STATES and m.group(1).upper() not in _WORD_CODES:
            states.add(m.group(1).upper())
    zone = None
    z = _ZONE.search(t)
    if z:
        zone = z.group(1) or _ZONE_ABBR.get(z.group(2) or "")
    return {"states": sorted(states), "zone": zone, "us": bool(_US.search(t)), "other_country": bool(_OTHER_COUNTRY.search(t)),
            "exclusion": bool(_EXCLUSION.search(t))}


def _residency(req, facts, completeness, siblings):
    t = _text(req)
    q = f"Where do you live, and would you relocate? The posting says: \"{req.jd_quote[:120]}\". Add a residency fact."
    fs = _facts_of(facts, "residency")
    if not fs:
        return _absent("residency", completeness, q)
    f = fs[0]
    home_us = _is_us(f.attributes.get("country"))
    home_state = _state_code(f.attributes.get("state"))
    p = _places(t, req.jd_quote)
    detail = {"posting": p, "has_country_us": home_us, "has_state": home_state}

    def result(ok: bool | None, why: str) -> FactCheck:
        if ok is None:
            return FactCheck("unanswered", _basis([f]), [f.id], {**detail, "reason": why}, q)
        if not ok and f.attributes.get("willing_to_relocate"):
            return FactCheck("unanswered", _basis([f]), [f.id], {**detail, "reason": "fails_but_willing_to_relocate"},
                             f"Would you relocate for this role? \"{req.jd_quote[:120]}\"")
        return FactCheck("met" if ok else "fails", _basis([f]), [f.id], {**detail, "reason": why}, None if ok else q)

    if p["zone"]:
        if not home_us or home_state is None:
            return result(None, "home_state_unknown")
        fully, partly = _ZONES[p["zone"]]
        if home_state in fully:
            return result(True, "time_zone")
        if home_state not in partly:
            return result(False, "time_zone")
        return result(None, "state_partly_in_time_zone")
    if p["states"]:
        if p["exclusion"]:
            # "US, excluding Alaska and Hawaii" / "not open to residents of ...": the list names
            # where the role is NOT open. Asked, not parsed: a wrong fails here is a false SKIP.
            return result(None, "exclusion_wording")
        if not home_us or home_state is None:
            return result(None, "home_state_unknown")
        return result(home_state in p["states"], "state_list")
    if p["other_country"] and not p["us"]:
        # Only places outside the US are named: a US resident fails; anyone else needs a reading.
        return result(False, "country") if home_us else result(None, "country_not_compared")
    if p["us"]:
        return result(home_us, "country")
    return result(None, "place_not_readable")


def _physical(req, facts, completeness, siblings):
    t = _text(req)
    q = f"Can you meet this physical requirement? \"{req.jd_quote[:120]}\" Add a physical fact."
    fs = _facts_of(facts, "physical")
    lbs, stand = _POUNDS.search(t), _STAND.search(t)
    if not fs:
        return _absent("physical", completeness, q)
    f = fs[0]
    checks = []
    if lbs:
        has = f.attributes.get("max_lift_lbs")
        checks.append(None if has is None else has >= int(lbs.group(1)))
    if stand:
        has = f.attributes.get("can_stand_hours")
        checks.append(None if has is None else has >= int(stand.group(1)))
    detail = {"needs_lift_lbs": int(lbs.group(1)) if lbs else None, "needs_stand_hours": int(stand.group(1)) if stand else None,
              "has_lift_lbs": f.attributes.get("max_lift_lbs"), "has_stand_hours": f.attributes.get("can_stand_hours")}
    if not checks or None in checks:
        if False in checks:
            return FactCheck("fails", _basis([f]), [f.id], detail, q)
        return FactCheck("unanswered", _basis([f]), [f.id], {**detail, "reason": "not_readable_or_not_declared"}, q)
    return FactCheck("met" if all(checks) else "fails", _basis([f]), [f.id], detail, None if all(checks) else q)


def _travel(req, facts, completeness, siblings):
    t = _text(req)
    q = f"How much can you travel? The posting says: \"{req.jd_quote[:120]}\". Add a travel fact."
    fs = _facts_of(facts, "travel")
    if not fs:
        return _absent("travel", completeness, q)
    f = fs[0]
    has = f.attributes.get("max_travel_percent")
    pct = _PERCENT.search(t)
    if pct:
        g = [int(x) for x in pct.groups() if x is not None]
        low, high = g[0], g[-1]
        detail = {"needs_percent": [low, high] if high != low else high, "has_percent": has}
        if has >= high:
            return FactCheck("met", _basis([f]), [f.id], detail)
        if has < low:
            return FactCheck("fails", _basis([f]), [f.id], detail, q)
        return FactCheck("unanswered", _basis([f]), [f.id], {**detail, "reason": "range_spans_fact"}, q)
    if _OCCASIONAL.search(t):
        # "Occasional" names no number: 10% is the lowest that clearly allows it; 0% clearly does not.
        if has >= 10:
            return FactCheck("met", _basis([f]), [f.id], {"needs": "occasional", "has_percent": has})
        if has == 0:
            return FactCheck("fails", _basis([f]), [f.id], {"needs": "occasional", "has_percent": has}, q)
    return FactCheck("unanswered", _basis([f]), [f.id], {"has_percent": has, "reason": "amount_not_readable"}, q)


def _license(req, facts, completeness, siblings):
    t = _text(req)
    q = f"Do you hold this license? \"{req.jd_quote[:120]}\" Add a license fact."
    fs = _facts_of(facts, "license")
    asked = _license_words(req.jd_quote)
    for f in fs:
        have = _license_words(f.attributes.get("name", ""))
        # Either the fact's name is fully in the posting ("Driver's license" in "valid driver's
        # license and reliable transportation") or the posting's license words are all in the
        # fact's name ("driver's" in "Florida driver's license"). One shared word ("public" in
        # "public adjuster" vs "Certified Public Accountant") is not a match (Codex review, P1.7).
        if have and (have <= set(_license_words(t)) or (asked and asked <= have)):
            ok = bool(f.attributes.get("active"))
            return FactCheck("met" if ok else "fails", _basis([f]), [f.id], {"license": f.attributes.get("name"), "active": ok},
                             None if ok else q)
    return _absent("license", completeness, q)


def _license_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", norm(text)) if len(w) >= 3 and w not in _GENERIC_LICENSE_WORDS
            and w not in _LICENSE_STOPWORDS}


_LICENSE_STOPWORDS = {"and", "the", "for", "with", "you", "your", "our", "this", "that", "all", "any", "candidates", "applicants",
                      "position", "role", "reliable", "transportation", "able", "ability", "possess", "need", "needs", "requires"}


def _program_status(req, facts, completeness, siblings):
    t = _text(req)
    if re.search(r"active[- ]duty|skillbridge|transitioning (service ?members?|military)", t):
        need = "active_duty"
    elif re.search(r"\bstudent\b|\benrolled\b", t):
        need = "student"
    elif "veteran" in t:
        need = "veteran"
    else:
        return FactCheck("unanswered", "none", [], {"reason": "program_not_readable"},
                         f"Does this program condition apply to you? \"{req.jd_quote[:120]}\"")
    q = f"This posting is limited to {need.replace('_', ' ')} candidates. Add a program_status fact."
    fs = _facts_of(facts, "program_status")
    if not fs:
        return _absent("program_status", completeness, q)
    f = fs[0]
    has = f.attributes.get(need)
    if has is None:
        return FactCheck("unanswered", _basis([f]), [f.id], {"needs": need, "reason": "field_not_declared"}, q)
    return FactCheck("met" if has else "fails", _basis([f]), [f.id], {"needs": need, "has": has}, None if has else q)


_CHECKS = {"work_authorization": _work_authorization, "sponsorship": _sponsorship, "clearance": _clearance,
           "residency": _residency, "physical": _physical, "travel": _travel, "license": _license,
           "program_status": _program_status}
