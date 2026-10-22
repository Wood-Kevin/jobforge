"""Greenhouse, Lever and Ashby list endpoints (parsers ported from ats_scanner/sources, edd1528).

Fetching is not here: the poller owns requests (conditional headers, limits, backoff). An
adapter only knows its URL and how to turn a decoded listing into RawJobs, which keeps the
parsers pure and testable against saved responses.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from jobforge_agent.discovery.models import Employer, RawJob, Salary
from jobforge_agent.discovery.text import html_to_text

ADAPTER_VERSION = {"greenhouse": "gh-1", "lever": "lever-1", "ashby": "ashby-1"}


class ParseError(Exception):
    pass


def list_url(kind: str, slug: str) -> str:
    if kind == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    if kind == "lever":
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    if kind == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    raise ValueError(f"unknown source kind {kind!r}")


def parse_listing(employer: Employer, data) -> list[RawJob]:
    if employer.kind == "greenhouse":
        if not isinstance(data, dict) or "jobs" not in data:
            raise ParseError("Greenhouse response has no 'jobs' key")
        return [_greenhouse(employer, j) for j in data["jobs"]]
    if employer.kind == "lever":
        if not isinstance(data, list):
            raise ParseError("Lever response is not a list")
        return [_lever(employer, j) for j in data]
    if employer.kind == "ashby":
        if not isinstance(data, dict) or "jobs" not in data:
            raise ParseError("Ashby response has no 'jobs' key")
        return [_ashby(employer, j) for j in data["jobs"] if j.get("isListed", True)]
    raise ValueError(f"unknown source kind {employer.kind!r}")


def iso_date(value) -> str | None:
    """Accept ISO strings or epoch milliseconds; return YYYY-MM-DD."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return str(value)[:10]


def join_locations(*parts) -> str:
    """Unique, non-empty location strings joined with '; ' in original order."""
    seen: dict[str, None] = {}
    for p in parts:
        for item in (p if isinstance(p, (list, tuple)) else [p]):
            if item and str(item).strip():
                seen.setdefault(str(item).strip(), None)
    return "; ".join(seen)


def _greenhouse(e: Employer, j: dict) -> RawJob:
    url = j.get("absolute_url") or ""
    # gh_jid in the URL is Greenhouse's public job id; fall back to "id".
    native = (parse_qs(urlsplit(url).query).get("gh_jid") or [str(j["id"])])[0]
    # Some boards put only "Hybrid" in location.name and the city in a custom metadata field.
    meta_locs = []
    for m in j.get("metadata") or []:
        if "location" in str(m.get("name", "")).lower() and m.get("value"):
            v = m["value"]
            meta_locs.extend(v if isinstance(v, list) else [v])
    raw = j.get("content") or ""
    return RawJob(
        ats="greenhouse", slug=e.slug, native_id=str(native),
        company=j.get("company_name") or e.name, title=(j.get("title") or "").strip(),
        location_text=join_locations((j.get("location") or {}).get("name"), meta_locs),
        listing_url=url, apply_url=url, description=html_to_text(raw), description_raw=raw,
        posted_at=iso_date(j.get("first_published") or j.get("updated_at")), item=j,
    )


_LEVER_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}
_LEVER_INTERVAL = {"per-year-salary": "YEAR", "per-hour-wage": "HOUR", "per-month-salary": "MONTH", "per-week-salary": "WEEK"}


def _lever(e: Employer, j: dict) -> RawJob:
    cats = j.get("categories") or {}
    # Lever splits the posting into an intro, titled lists and a closing section; stitch them in order.
    parts = [j.get("descriptionPlain") or ""]
    raw_parts = [j.get("description") or ""]
    for block in j.get("lists") or []:
        parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content'))}")
        raw_parts.append(f"<h3>{block.get('text', '')}</h3>{block.get('content') or ''}")
    parts.append(j.get("additionalPlain") or "")
    raw_parts.append(j.get("additional") or "")
    return RawJob(
        ats="lever", slug=e.slug, native_id=str(j["id"]), company=e.name, title=(j.get("text") or "").strip(),
        location_text=join_locations(cats.get("location"), cats.get("allLocations") or []),
        listing_url=j.get("hostedUrl") or "", apply_url=j.get("applyUrl") or j.get("hostedUrl") or "",
        description="\n\n".join(p for p in parts if p.strip()), description_raw="".join(raw_parts),
        posted_at=iso_date(j.get("createdAt")),
        workplace=_LEVER_WORKPLACE.get(str(j.get("workplaceType") or "").lower()),
        salary=_lever_salary(j.get("salaryRange")), item=j,
    )


def _lever_salary(r: dict | None) -> Salary | None:
    # Non-USD ranges are not comparable to a USD floor; the job keeps "salary unknown".
    if not r or (r.get("currency") or "USD").upper() != "USD":
        return None
    lo, hi = r.get("min"), r.get("max")
    if lo is None and hi is None:
        return None
    period = _LEVER_INTERVAL.get(str(r.get("interval") or ""), "YEAR")
    text = " - ".join(f"${v:,}" for v in (lo, hi) if v is not None) + f" ({r.get('interval')})"
    return Salary(lo, hi, period, text)


_ASHBY_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}
_ASHBY_INTERVAL = {"1 YEAR": "YEAR", "1 HOUR": "HOUR", "1 MONTH": "MONTH", "1 WEEK": "WEEK"}


def _ashby(e: Employer, j: dict) -> RawJob:
    secondary = [s.get("location") for s in j.get("secondaryLocations") or []]
    workplace = _ASHBY_WORKPLACE.get(str(j.get("workplaceType") or "").lower())
    if workplace is None and j.get("isRemote"):
        workplace = "remote"
    return RawJob(
        ats="ashby", slug=e.slug, native_id=str(j["id"]), company=e.name, title=(j.get("title") or "").strip(),
        location_text=join_locations(j.get("location"), secondary),
        listing_url=j.get("jobUrl") or "", apply_url=j.get("applyUrl") or j.get("jobUrl") or "",
        description=j.get("descriptionPlain") or "", description_raw=j.get("descriptionHtml") or "",
        posted_at=iso_date(j.get("publishedAt")), workplace=workplace,
        salary=_ashby_salary(j.get("compensation") or {}), item=j,
    )


def _ashby_salary(comp: dict) -> Salary | None:
    salaries = [c for c in comp.get("summaryComponents") or []
                if c.get("compensationType") == "Salary" and (c.get("currencyCode") or "USD") == "USD"]
    if not salaries:
        return None
    c = salaries[0]
    return Salary(c.get("minValue"), c.get("maxValue"), _ASHBY_INTERVAL.get(c.get("interval") or "", "YEAR"),
                  comp.get("scrapeableCompensationSalarySummary") or c.get("summary") or "")
