"""Parsers against saved public responses (fixtures from job-scanner edd1528)."""
import json
from pathlib import Path

import pytest

from jobforge_agent.discovery import adapters
from jobforge_agent.discovery.models import Employer

FIX = Path(__file__).parent / "fixtures" / "ats"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind,slug,fixture", [
    ("greenhouse", "cloudflare", "greenhouse_cloudflare.json"),
    ("lever", "palantir", "lever_palantir.json"),
    ("ashby", "1password", "ashby_1password.json"),
])
def test_parse_saved_listing(kind, slug, fixture):
    raws = adapters.parse_listing(Employer(1, kind, slug, slug.title()), _load(fixture))
    assert len(raws) == 3
    for r in raws:
        assert r.source_job_id.startswith(f"{kind}:{slug}:") and r.native_id
        assert r.title and r.listing_url.startswith("https://")
        assert len(r.description) > 200, "plain-text description extracted"
        assert r.description_raw, "original markup kept for raw_postings"
        assert r.item and isinstance(r.item, dict), "verbatim item kept for raw_postings.payload"
        assert "<" not in r.description[:200]


def test_greenhouse_double_encoded_html_becomes_text():
    raws = adapters.parse_listing(Employer(1, "greenhouse", "cloudflare", "Cloudflare"), _load("greenhouse_cloudflare.json"))
    assert "&lt;" in raws[0].description_raw or "<" in raws[0].description_raw
    assert "&lt;" not in raws[0].description


def test_wrong_shape_is_a_parse_error():
    with pytest.raises(adapters.ParseError):
        adapters.parse_listing(Employer(1, "greenhouse", "x", "X"), [])
    with pytest.raises(adapters.ParseError):
        adapters.parse_listing(Employer(1, "lever", "x", "X"), {"jobs": []})


def test_unlisted_ashby_postings_are_ignored():
    data = _load("ashby_1password.json")
    data["jobs"][0]["isListed"] = False
    raws = adapters.parse_listing(Employer(1, "ashby", "1password", "1Password"), data)
    assert len(raws) == 2


def test_list_urls_match_the_measured_endpoints():
    assert adapters.list_url("greenhouse", "acme") == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    assert adapters.list_url("lever", "acme") == "https://api.lever.co/v0/postings/acme?mode=json"
    assert adapters.list_url("ashby", "acme") == "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
