"""E0: segment a posting into labeled sections and sentences (ARCHITECTURE 6.4). Code only.

Input is the stored plain text (`raw_postings.description_text`, the same text E1 sees), whose
headings are short standalone lines. A heading whose wording is in the lexicon gives its section
a label; any other heading-shaped line starts an `unknown` section, so text after a heading the
lexicon does not know ("Logistics", which can hold visa and office rules) is never swallowed by
the section before it. Exception: inside an eeo or privacy section only a lexicon heading ends
it, because legal notices carry their own sub-headings ("Information you provide:").

Labels: requirements, preferred, responsibilities, benefits, about, eeo, privacy, unknown.
Context labels (benefits, about, eeo, privacy) are assigned only for unambiguous headings: a
real requirements section labeled `benefits` would hide its requirements, while an `unknown`
label only costs the section signal.

All positions refer to `Segmented.text`, the normalized text quotes are matched against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobforge_agent.analysis.versions import SEGMENTER_VERSION

LABELS = ("requirements", "preferred", "responsibilities", "benefits", "about", "eeo", "privacy", "unknown")

# Checked in order; the first match labels the heading. Patterns see the normalized heading
# (lowercase, straight quotes, no trailing colon).
_HEADINGS: list[tuple[str, str]] = [
    ("preferred", r"preferred( qualifications| skills| experience| requirements)?|nice[- ]to[- ]haves?|bonus( points)?( if you have)?"
                  r"|pluses|it would be great if you (had|have)|strong candidates may also( have)?|extra credit"
                  r"|desired (skills|qualifications|experience)|additional (preferred )?qualifications|ways to stand out"),
    ("requirements", r"(minimum |basic |required |key |core )?(requirements|qualifications)( and (qualifications|requirements|experience))?"
                     r"|requirement|required|required (skills|experience|qualifications)( and (skills|experience|qualifications))?|what you('ll| will)? (need|bring)( to [\w .&'-]+)?|what we('re| are) looking for"
                     r"|who you are|you (are|have|bring)|about you|you may be a good fit if you( have)?|you might be a good fit if you( have)?"
                     r"|skills( and | & )(experience|qualifications)|experience|(technical |required )?skills|must[- ]haves?"
                     r"|your (background|experience|skills)|(the )?ideal candidate|what makes you a (great|good) fit"
                     r"|what you should have|is this role a fit"),
    ("responsibilities", r"(key |core |primary |position |job |role )?responsibilities|(essential |key )?(duties|functions)( (and|&) responsibilities)?|what you('ll| will) do|what you('ll| will) be (doing|responsible for)"
                         r"|in this role,? you will|you will|the role|about the (role|job|position|opportunity)|about this (role|job|position)"
                         r"|an overview of this role|(job|role|position) (summary|overview|description)|overview|the opportunity"
                         r"|your impact|day[- ]to[- ]day|(job )?description|what to expect|representative projects"
                         # seg-v3: "Expectations during live (synchronous) sessions" lists what the job asks of you
                         # day to day ("Attend all scheduled meetings with your camera on").
                         r"|([\w-]+ )?expectations( (during|for|of) [\w ()&'-]+)?"),
    ("eeo", r"equal (employment )?opportunit(y|ies)( employer| statement)?|eeo( statement)?|.*equal opportunity.*"
            r"|diversity( and | & )(inclusion|belonging)|inclusion( and | & )privacy|accommodations?|our commitment to (diversity|inclusion)"),
    ("privacy", r".*privacy.*"),
    ("benefits", r"benefits|perks|what we offer|compensation|(annual |base )?(salary|pay)( range)?( information)?|.*(salary|pay) range.*"
                 r"|compensation( and | & )benefits|total rewards|our benefits|a few of the benefits we offer|why (join|work (with|at|for)) [\w .&'-]+"
                 r"|a few reasons to work with us|how [\w .&'-]+ supports? [\w .&'-]+|[\w&'-]+ cares|come work with us!?"),
    ("about", r"about (us|the company|the team|[\w.&'-]+)|who we are|our (mission|story|team|values|company)"
              r"|company (background|overview|description)|how we('re| are) different|what we do|the team|we live and breathe.*"),
]
_HEADING_RES = [(label, re.compile(rf"^(?:{pat})$")) for label, pat in _HEADINGS]

_STICKY = {"eeo", "privacy"}

# Sentence ends: terminal punctuation followed by a capital, digit, quote or bracket. Common
# abbreviations do not end a sentence.
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_ABBREV = re.compile(r"\b(?:e\.g|i\.e|etc|vs|inc|sr|jr|no|approx|incl|u\.s|u\.k|st|dept)\.$", re.I)


def norm(s: str) -> str:
    """The comparison form for quotes and text: lowercase, straight quotes, plain hyphens, no
    bullet markers, whitespace collapsed (spike P1.0b: 100% of Gemma quotes matched this way)."""
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"^\s*[-*•●▪]\s+", " ", s, flags=re.M)
    return re.sub(r"\s+", " ", s).strip()


def norm_quote(s: str) -> str:
    """A quote's comparison form; leading bullets and trailing punctuation are not evidence."""
    return re.sub(r"^[-*•\s]+|[\s.;,:]+$", "", norm(s))


@dataclass(frozen=True)
class Sentence:
    index: int
    section: int
    label: str
    text: str          # normalized
    start: int         # offsets into Segmented.text
    end: int
    heading: bool = False
    lead: int | None = None  # index of the list lead-in line ("... is preferred:") this bullet sits under


@dataclass(frozen=True)
class Section:
    index: int
    label: str
    heading: str | None  # original heading line, None for the text before the first heading


@dataclass
class Segmented:
    text: str
    sections: list[Section] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)

    @property
    def headings(self) -> int:
        return sum(1 for s in self.sections if s.heading is not None)

    def sentences_overlapping(self, start: int, end: int) -> list[Sentence]:
        return [s for s in self.sentences if s.start < end and start < s.end]

    def to_json(self) -> dict:
        counts: dict[int, int] = {}
        for s in self.sentences:
            if not s.heading:
                counts[s.section] = counts.get(s.section, 0) + 1
        return {"version": SEGMENTER_VERSION, "headings": self.headings,
                "sections": [{"label": s.label, "heading": s.heading, "sentences": counts.get(s.index, 0)} for s in self.sections]}


def heading_label(line: str) -> str | None:
    """Lexicon label for a heading line, or None if its wording is not in the lexicon."""
    h = norm(line).rstrip(":").strip()
    h = re.sub(r"\s+", " ", h)
    for label, rx in _HEADING_RES:
        if rx.match(h):
            return label
    return None


_LEAD_IN_END = re.compile(r"\b(of|in|with|to|for|and|or|on|at|by|including|like|such as|following|from)$", re.I)


def _lead_in(line: str) -> bool:
    """"Demonstrated understanding of:" introduces a list inside a section; it does not start one."""
    s = line.strip()
    return s.endswith(":") and (len(s.split()) > 4 or bool(_LEAD_IN_END.search(s[:-1].strip())))


_BULLET = re.compile(r"^\s*[-*\u2022\u25cf\u25aa]\s+")


def _heading_shaped(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 70 or not s[0].isalpha() or s.startswith(("- ", "* ")):
        return False
    if len(s.split()) > 8 or s[-1] in ".!?,;":
        return False
    body = s[:-1] if s.endswith(":") else s
    return ":" not in body  # "Location: Remote, US" is a field, not a heading


def _split_sentences(line: str) -> list[str]:
    parts, cur = [], ""
    for piece in _SENT_END.split(line):
        cur = f"{cur} {piece}" if cur else piece
        if not _ABBREV.search(cur):
            parts.append(cur)
            cur = ""
    if cur:
        parts.append(cur)
    return parts


def segment(text: str | None) -> Segmented:
    seg = Segmented(text="")
    seg.sections.append(Section(0, "unknown", None))
    pieces: list[str] = []
    pos = 0
    current = seg.sections[0]

    lead: int | None = None  # the open list lead-in, if the current line is one of its bullets

    def add(raw: str, heading: bool, lead_of: int | None = None) -> None:
        nonlocal pos
        n = norm(raw)
        if not n:
            return
        if pieces:
            pos += 1  # the joining space
        seg.sentences.append(Sentence(len(seg.sentences), current.index, current.label, n, pos, pos + len(n), heading, lead_of))
        pieces.append(n)
        pos += len(n)

    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        label = heading_label(line) if _heading_shaped(line) else None
        generic = label is None and _heading_shaped(line) and prev_blank and i + 1 < len(lines) and not _lead_in(line)
        if label is not None or (generic and current.label not in _STICKY):
            current = Section(len(seg.sections), label or "unknown", line.strip())
            seg.sections.append(current)
            add(line, heading=True)
            lead = None
            continue
        bullet = bool(_BULLET.match(line))
        if not bullet:
            lead = None
        for sent in _split_sentences(line.strip()):
            add(sent, heading=False, lead_of=lead if bullet else None)
        if not bullet and line.strip().endswith(":"):
            lead = len(seg.sentences) - 1  # bullets that follow belong to this lead-in
    seg.text = " ".join(pieces)
    if seg.sections[0].heading is None and not any(s.section == 0 for s in seg.sentences):
        # Text began with a heading: drop the empty leading section and renumber.
        seg.sections = [Section(i, s.label, s.heading) for i, s in enumerate(seg.sections[1:])]
        seg.sentences = [Sentence(s.index, s.section - 1, s.label, s.text, s.start, s.end, s.heading, s.lead) for s in seg.sentences]
    return seg
