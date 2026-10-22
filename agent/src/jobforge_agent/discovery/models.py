"""Plain data shapes (ported from ats_scanner/models.py, edd1528).

Added for JobForge: `item` (the verbatim API object, stored as raw_postings.payload) and
`description_raw` (the source's original description markup).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Period = Literal["YEAR", "HOUR", "MONTH", "WEEK"]


@dataclass(frozen=True)
class Employer:
    id: int
    kind: str  # source kind: greenhouse | lever | ashby
    slug: str
    name: str


@dataclass(frozen=True)
class Salary:
    min: float | None
    max: float | None
    period: Period
    text: str


@dataclass
class RawJob:
    """One posting as an adapter saw it, before any filtering."""
    ats: str
    slug: str
    native_id: str
    company: str
    title: str
    location_text: str
    listing_url: str
    apply_url: str
    description: str = ""          # plain text
    description_raw: str = ""      # original markup from the source
    posted_at: str | None = None   # YYYY-MM-DD
    workplace: str | None = None   # remote / hybrid / onsite
    salary: Salary | None = None   # structured salary from the API only
    item: dict = field(default_factory=dict)

    @property
    def source_job_id(self) -> str:
        # Stable across runs and independent of URL shape (scanner, 2026-10-13 collapse).
        return f"{self.ats}:{self.slug}:{self.native_id}"
