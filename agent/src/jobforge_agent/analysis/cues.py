"""Cue lexicons for E2 (ARCHITECTURE 6.4, interpretation checks 2-7). Code only, no model.

Every pattern runs on normalized text (analysis.segment.norm). The lexicons are deliberately
small and literal: a false eligibility cue costs a REVIEW question (D15), a missed one is caught
nowhere else, so recall matters more than precision for ELIGIBILITY, and the reverse for
NEGATION (a false negation would hide a real requirement).
"""
from __future__ import annotations

import re

# ----- check 6: eligibility type from code, not the model -----
# Order is priority when a span shows several types and the model named none of them.
ELIGIBILITY: list[tuple[str, re.Pattern]] = [(t, re.compile(p)) for t, p in [
    # interp-v3: "authorization to work" (job 16212, "Candidates must have authorization to work in the United States").
    ("work_authorization", r"\b(u\.?s\.? |united states )?citizen(s|ship)?\b|\bauthori[sz]ed to work\b|\bwork(ing)? authori[sz]ation\b"
                           r"|\bauthori[sz]ation to work\b"
                           r"|\b(legally )?eligible to work\b|\bright to work\b|\bpermanent residen(t|ts|cy)\b|\bgreen card\b"),
    ("sponsorship", r"\bvisas?\b|\bimmigration\b|\bwork permit\b|\bh-?1b\b|\bsponsor(ship)?\b[^.]{0,40}\b(visa|employment|work|candidates|applicants)\b"
                    r"|\b(no|not|unable to|cannot|can't|will not|won't|do not|does not|don't)\b[^.]{0,20}\bsponsor"),
    # "Secret" alone is ordinary English ("our secret weapon", P1.6 dry run on real postings):
    # it counts only next to clearance wording.
    ("clearance", r"\bclearances?\b|\btop secret\b|\b(active|current|interim|dod|final) secret\b"
                  r"|\bsecret\b(?=[^.]{0,25}\b(clearance|level|or (higher|above)|eligib\w*|sci)\b)"
                  r"|\bts ?/ ?sci\b|\bts-sci\b|\bsci\b|\bpolygraph\b|\bpublic trust\b"),
    # interp-v3: hours, shifts and on-call (Company N "Friday - Tuesday, 8A - 5P", Company F "24x7x365 ... weekends and
    # holidays", Company V "on-call rotation"), matched against the availability fact. Before residency: a line with
    # both hours and a zone ("9-5 Eastern", "Pacific Time Zone business hours", "Pacific time zone alignment")
    # is about hours. "Holidays" only as work ("weekends and holidays", "cover holidays"), not "10 paid
    # holidays". Not used by the recall scan (interpret.RECALL_SKIPS): unextracted sentences that mention
    # on-call or holidays were team and benefit prose (before/after, 2026-10-24: three APPLY became REVIEW).
    ("availability", r"\bon-?call\b|\bweekends?\b|\b(work|working|cover|covering|support|including)\b[^.;]{0,30}\bholidays?\b"
                     r"|\bovernight\b|\bnight shifts?\b|\bshift work\b"
                     r"|\brotating (shifts?|schedule)\b|\b(work|cover|covering|support)\b[^.;]{0,25}\bshifts?\b|\b24\s*[x/]\s*7\b"
                     r"|\boutside (of )?(normal |regular )?(business |working )?hours\b|\bafter[- ]hours\b"
                     r"|\b\d{1,2}(:\d\d)?\s*(a|p)\.?m?\.?\s*[-–]\s*\d{1,2}(:\d\d)?\s*(a|p)\b"
                     r"|\b(pacific|mountain|central|eastern)\b[^.;]{0,25}\b(business hours|working hours|hours|alignment|overlap)\b"),
    ("residency", r"\breside\b|\bresiding\b|\bresidency\b|\bresident of\b|\bcommut(e|ing|able) distance\b|\bcommutable\b"
                  r"|\bmust (live|be located|be based)\b|\b(live|located|based) within\b|\bwithin \d+ miles\b"
                  r"|\b(candidates|applicants) (located|based|residing|living) in\b"
                  # interp-v2 (P1.7 profile run, Company L): "be local to the El Dorado Hills area" and
                  # "on-site a minimum of 3 days per week" were read as experience. "Local to" counts only
                  # for a person ("be local to") or a place ("local to the ... area/office"), not "data
                  # local to each node"; on-site/in-office counts only with a days-per-week attendance,
                  # not "provide on-site support".
                  r"|\bbe local to\b|\blocal to (the |our )?[\w .,'-]{1,40}\b(area|office|region|metro|city|campus|site)\b"
                  r"|\b(on-?site|in[- ]office|in the office|in person)\b[^.;]{0,30}?\b(\d|one|two|three|four|five)\s*(\+\s*)?"
                  r"(days?|x)\s*(a|per|each|every)\s*week\b"
                  # interp-v3 (the Company N case, 2026-10-24): "Remote in Eastern US (required)" and "Located in US,
                  # eastern timezone (remote)" went to the matcher as experience. A time zone next to a zone word,
                  # "remote in/within <region>", and a line that starts with "located/based in" count. Not
                  # "work across time zones" (a collaboration line) and not "our office is located in".
                  r"|\b(pacific|mountain|central|eastern)\s+(standard\s+)?(time\s*zone|timezone|us|u\.s\.|united states)\b"
                  r"(?![^.;]{0,25}\b(business hours|working hours|hours|alignment|overlap)\b)"
                  r"|\b(pst|mst|cst|est|pt|mt|ct|et)\s+time\s*zone\b"
                  r"|\bremote\b[^.;]{0,10}\b(in|within|from)\s+(the\s+)?(us|u\.s\.|usa|united states|eastern|central|mountain|pacific"
                  r"|canada|uk|europe|emea|latam|americas)\b"
                  r"|^\W*(located|based|living)\s+in\b"),
    ("physical", r"\blift(ing)?\b[^.]{0,30}\b(\d+ ?(lbs?|pounds)|heavy)\b|\b\d+ ?(lbs?|pounds)\b|\bstand(ing)? for (long|extended|\d+)"
                 r"|\bphysical (demands|requirements)\b|\bclimb(ing)? ladders\b"),
    ("travel", r"\btravel\b[^.]{0,20}\b\d+ ?%|\b\d+ ?% (of the time )?travel\b|\bwilling(ness)? to travel\b|\bability to travel\b"
               r"|\btravel (is )?required\b|\brequires? (\w+ ){0,2}travel\b"),
    ("license", r"\bdriver'?s'? licen[cs]e\b|\bvalid (\w+ ){0,2}licen[cs]e\b|\blicen[cs]e (is )?required\b"
                r"|\bmust (be|hold|have) (a |an )?(\w+ )?licen[cs]ed?\b|\blicensed (as|in)\b|\blicensure\b"),
    ("program_status", r"\bactive[- ]duty\b|\bskillbridge\b|\bcurrently enrolled\b"
                       r"|\benrolled (as a |in an? )?(full-time )?(student|degree|university|college|bachelor|master)"
                       r"|\btransitioning (service ?members?|military)\b|\bmust be an? (veteran|student)\b"),
]]
ELIGIBILITY_TYPES = tuple(t for t, _ in ELIGIBILITY)


def eligibility_types(text: str) -> list[str]:
    return [t for t, rx in ELIGIBILITY if rx.search(text)]


# ----- check 5: "ability to obtain" a clearance is not a clearance held -----
ABILITY_TO_OBTAIN = re.compile(
    r"\b(ability|able|eligible|eligibility|willing(ness)?|capable)\b[^.]{0,25}\b(obtain|acquire|get|be granted|receive)\b"
    r"|\beligib(le|ility) (for|to hold) (a |an )?(security )?clearance\b|\bclearable\b|\bobtainable\b")

# ----- check 2: negation (scoped to the quote's clause) -----
NEGATION = re.compile(
    r"\bnot (strictly )?(required|necessary|needed|mandatory|expected|a (requirement|must|prerequisite))\b"
    r"|\bno (\w+[ /-]){0,5}(is |are )?(required|necessary|needed)\b"
    r"|\b(don't|do not|doesn't|does not|won't|will not|wouldn't|you'll never) (need|require|have to|expect)\b"
    r"|\bwithout (any )?(prior|previous) (experience|knowledge|background)\b|\bwe('ll| will) train\b|\bnot have to\b")
# "Candidates who do not require sponsorship" is a sponsorship condition, not a negation.
NEGATION_EXCEPT = re.compile(r"\bsponsor|\bvisa")

# ----- check 3: modality and equivalence -----
REQUIRED = re.compile(r"\bmust\b|\brequired\b|\brequires?\b|\brequirements?\b|\bminimum\b|\bmandatory\b|\bessential\b|\bat least\b")
PREFERRED = re.compile(
    r"\bpreferred\b|\bnice[- ]to[- ]have\b|\ba (strong |big |huge )?plus\b|\bbonus\b|\bideally\b|\bdesired\b|\bdesirable\b"
    r"|\badvantageous\b|\bwould be (great|nice|helpful)\b|\bhelpful\b|\bbeneficial\b|\bappreciated\b")
EQUIVALENCE = re.compile(
    r"\bor (an? )?(equivalent|comparable|similar|related|relevant)\b|\bin lieu of\b|\bor (\d+|one|two|three|four|five|six|seven|eight|nine|ten)\+? "
    r"(additional |more |years)|\bequivalent (combination|experience|practical experience)\b|\bor equivalent\b")
_PARENS = re.compile(r"\([^)]*\)")


def strip_parentheticals(text: str) -> str:
    """'(preferably Ruby or Bash)' qualifies a choice, not the requirement's importance."""
    return _PARENS.sub(" ", text)


# ----- years (both branches of "5 years with a degree or 7 without") -----
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "twelve": 12, "fifteen": 15}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(_WORDS) + r")"
YEARS = re.compile(rf"\b{_NUM}\s*\+?\s*(?:(?:-|to)\s*{_NUM}\s*\+?\s*)?(?:or more |plus )?(?:additional |more |relevant )?(?:years?|yrs?)\b")
YEARS_BRANCH = re.compile(rf"\bor {_NUM}\b(?! ?%)")


def _num(s: str) -> float:
    return float(_WORDS.get(s, s))


def years(text: str) -> list[float]:
    """Minimum years named in `text`, one per branch, in order of appearance."""
    found: list[float] = []
    for m in YEARS.finditer(text):
        found.append(_num(m.group(1)))
        tail = text[m.end():m.end() + 40]
        b = YEARS_BRANCH.search(tail)
        if b and not YEARS.match(tail[b.start(1):]):
            found.append(_num(b.group(1)))
    return found


# ----- check 4: subject -----
# A line the model labelled a duty (a task of the role, E1 e1-v2) that still asks something of the candidate:
# code reads it as a requirement (interpret._read). Prior experience, knowledge, skill or ability words.
ASKS_OF_CANDIDATE = re.compile(
    r"\b(experience[ds]?|knowledge|understanding|familiar(ity)?|proficien(t|cy)|expert(ise)?|skill(s|ed)?|ability to|able to"
    r"|comfortable (with|in|using)|background in|certifi(ed|cation)|degree|must|required|essential|you have|you bring|you've)\b")
EEO = re.compile(
    r"\b(regardless of|without regard to|discriminat\w*|equal (employment )?opportunit\w*|affirmative action|protected (veteran )?(class|status)"
    r"|e-?verify|reasonable accommodations?|veteran status|genetic information|sexual orientation|gender identity|national origin)\b")
PRIVACY = re.compile(r"\b(privacy (notice|policy)|personal (data|information)|data protection|gdpr|ccpa)\b")
COMPANY_SUBJECT = re.compile(r"^(our|we|we're|we've|we'll|at [\w.&'-]+,? we|the team|this team|the company)\b")
CANDIDATE_LEAD_IN = re.compile(
    r"^(we('re| are) (looking|seeking|hiring|searching)|we (need|want|expect|require|seek)|we'd (love|like)|we('re| are) excited)\b")
OTHERS_YEARS = re.compile(
    r"\b(mentor|lead|manage|coach|support|work(ing)? (with|alongside)|collaborate with|partner with)\b[^.]{0,60}"
    r"\b(engineers|people|colleagues|team members|experts|professionals|customers|developers|analysts)\b[^.]{0,20}\bwith "
    + _NUM + r"\+? (years|yrs)")
