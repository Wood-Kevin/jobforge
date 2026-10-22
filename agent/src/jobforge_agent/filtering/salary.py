# Ported verbatim from ats_scanner/salary.py (edd1528); only change: the Salary type is
# defined here instead of imported from the scanner's models.
"""Find a salary in free text when the API does not give one.

Strategy: collect every plausible dollar figure (ranges, "From", "Up to",
single amounts) with its pay period, then merge ranges of the same period into
the widest span. Widest, not first, because postings often list geographic
tiers ("Zone A $90K-$110K, Zone B $80K-$95K") and the floor check should only
reject a job when even its best tier is under the floor."""
from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Literal

Period = Literal["YEAR", "HOUR", "MONTH", "WEEK"]
# Same multipliers the scanner and job_lean use, so "$28/hour" means the same per year everywhere.
ANNUAL_MULTIPLIER = {"YEAR": 1, "HOUR": 2080, "MONTH": 12, "WEEK": 52}


@dataclass
class Salary:
    min: float | None
    max: float | None
    period: Period
    text: str
    open_ended: bool = False  # "From $X": no stated ceiling

    def annual_max(self) -> float | None:
        """Best case per year. The floor rejects only when even the top of the range is under it."""
        top = self.max if self.max is not None else (None if self.open_ended else self.min)
        return None if top is None else top * ANNUAL_MULTIPLIER[self.period]

# A dollar amount: "$85,000", "$85000.00", "$90K", "$ 25". The lookbehind skips
# "CA$", "A$", "C$" so other currencies are not read as USD.
_AMT = r"(?<![A-Za-z])\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*([kK])?"
# Range separators: hyphen, en dash, em dash (written as escapes so the source
# file itself stays free of dash characters), or the word "to".
_SEP = r"\s*(?:-|\u2013|\u2014|to)\s*"
_RANGE = re.compile(_AMT + _SEP + r"\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*([kK])?")
_FROM = re.compile(r"\b(?:from|starting at|starts at|minimum of|at least)\s+" + _AMT, re.I)
_UPTO = re.compile(r"\bup to\s+" + _AMT, re.I)
_SINGLE = re.compile(_AMT)

# What follows the number decides the period. Look a short distance ahead.
_HOUR = re.compile(r"^\s*(?:/\s*(?:hr|hour)|per hour|an hour|a hour|hourly|/h\b|ph\b)", re.I)
_MONTH = re.compile(r"^\s*(?:/\s*(?:mo|month)|per month|a month|monthly)", re.I)
_WEEK = re.compile(r"^\s*(?:/\s*(?:wk|week)|per week|a week|weekly)", re.I)
_YEAR = re.compile(r"^\s*(?:/\s*(?:yr|year|annum)|per year|a year|per annum|annually|annual)", re.I)
# Big numbers that are not pay: "$5 billion", "$20M Series B".
_NOT_PAY = re.compile(r"^\s*(?:billion|million|bn\b|mm\b|m\b|b\b)", re.I)


def _num(whole: str, cents: str | None, k: str | None) -> float:
    v = float(whole.replace(",", "") + ("." + cents if cents else ""))
    return v * 1000 if k else v


def _period(tail: str, value: float) -> str | None:
    """Decide the pay period from the words after the amount, else from size."""
    tail = tail[:40]
    if _NOT_PAY.match(tail):
        return None
    for rx, p in ((_HOUR, "HOUR"), (_MONTH, "MONTH"), (_WEEK, "WEEK"), (_YEAR, "YEAR")):
        if rx.match(tail):
            return p
    # No unit stated. Under $200 is an hourly wage in practice; $20k and up is
    # a yearly salary. The band in between is ambiguous, so skip it.
    if 10 <= value < 200:
        return "HOUR"
    if value >= 20000:
        return "YEAR"
    return None


def _plausible(value: float, period: str) -> bool:
    lo, hi = {"HOUR": (10, 500), "WEEK": (300, 20000), "MONTH": (1500, 60000), "YEAR": (15000, 1_000_000)}[period]
    return lo <= value <= hi


def parse_salary(text: str | None) -> Salary | None:
    if not text or "$" not in text:
        return None
    found: list[Salary] = []
    used: list[tuple[int, int]] = []

    def free(span):
        return all(span[1] <= a or span[0] >= b for a, b in used)

    for m in _RANGE.finditer(text):
        lo = _num(m.group(1), m.group(2), m.group(3))
        hi = _num(m.group(4), m.group(5), m.group(6))
        if m.group(6) and not m.group(3) and lo < 1000:
            lo *= 1000  # "$90-110K": the K applies to both ends
        p = _period(text[m.end():], hi)
        if p and hi >= lo and _plausible(lo, p) and _plausible(hi, p):
            found.append(Salary(lo, hi, p, m.group(0).strip()))
            used.append(m.span())

    for rx, kind in ((_FROM, "from"), (_UPTO, "upto")):
        for m in rx.finditer(text):
            if not free(m.span()):
                continue
            v = _num(m.group(1), m.group(2), m.group(3))
            p = _period(text[m.end():], v)
            if p and _plausible(v, p):
                found.append(Salary(v, None, p, m.group(0).strip(), open_ended=True) if kind == "from"
                             else Salary(None, v, p, m.group(0).strip()))
                used.append(m.span())

    if not found:
        for m in _SINGLE.finditer(text):
            if not free(m.span()):
                continue
            v = _num(m.group(1), m.group(2), m.group(3))
            tail = text[m.end():]
            p = _period(tail, v)
            # A lone figure only counts when a unit is written next to it;
            # otherwise "$500 home office stipend" would become a wage.
            if p and _plausible(v, p) and any(rx.match(tail[:40]) for rx in (_HOUR, _MONTH, _WEEK, _YEAR)):
                found.append(Salary(v, v, p, m.group(0).strip()))
                break

    if not found:
        return None
    # Merge everything that shares the most common period into one widest span.
    period = max({s.period for s in found}, key=lambda p: sum(s.period == p for s in found))
    same = [s for s in found if s.period == period]
    mins = [s.min for s in same if s.min is not None]
    maxs = [s.max for s in same if s.max is not None]
    open_ended = any(s.open_ended for s in same) and not maxs
    return Salary(
        min=min(mins) if mins else None,
        max=max(maxs) if maxs else None,
        period=period,
        text="; ".join(dict.fromkeys(s.text for s in same))[:200],
        open_ended=open_ended,
    )
