"""First-run setup (P1.8b): careers links, watchlist files, the plain search-profile form, setup status."""
import json

import pytest

from conftest import NOW
from jobforge_agent import setup
from jobforge_agent.filtering import rules as filter_rules
from jobforge_agent.filtering import simple
from jobforge_agent.filtering.profile import ProfileError, compile_profile
from jobforge_agent.db.connect import open_connection
from test_analysis import FakeServer, _run_with_fake
from test_ipc import AUTH


def test_careers_links_become_employers():
    text = """
    https://boards.greenhouse.io/cloudflare
    https://job-boards.greenhouse.io/datadog/jobs/123?gh_jid=123
    https://jobs.lever.co/extremenetworks/f99032c4-6048
    https://jobs.ashbyhq.com/vultr
    lever:plaid
    https://boards.greenhouse.io/cloudflare/
    https://www.example.com/careers
    # a comment
    """
    found, bad = setup.parse_board_lines(text)
    assert [(e["source"], e["slug"]) for e in found] == [
        ("greenhouse", "cloudflare"), ("greenhouse", "datadog"), ("lever", "extremenetworks"), ("ashby", "vultr"), ("lever", "plaid")]
    assert bad == ["https://www.example.com/careers"]


def test_scanner_watchlist_format():
    entries = setup.parse_watchlist("employers:\n  - {name: Cloudflare, ats: greenhouse, slug: cloudflare}\n  - {name: X, ats: workday, slug: a/b/c}\n")
    assert [e["slug"] for e in entries] == ["cloudflare", "a/b/c"]
    with pytest.raises(ValueError):
        setup.parse_watchlist("just: text")


FORM = {"titles": ["Help Desk", "NOC Technician", "C++ Support"], "skip_seniority": ["senior", "principal"],
        "skip_fields": ["sales"], "home_state": "fl", "home_towns": ["Jacksonville", "Live Oak"], "salary_floor": 55000,
        "pay_target": 70000, "work_modes": {"remote": "favorable", "onsite": "unfavorable", "hybrid": ""},
        "favorite_titles": ["noc"], "favorite_levels": ["tier 1"], "max_age_days": ""}


def _job(title, location="Remote - US", desc=""):
    return filter_rules.JobView(title=title, location_text=location, description=desc)


def test_form_builds_working_rules_from_nothing():
    rules = simple.from_form(FORM, None)
    p = compile_profile(rules)
    assert filter_rules.evaluate(_job("Help Desk Analyst II"), p).passed
    assert filter_rules.evaluate(_job("C++ Support Engineer"), p).passed  # typed text is a phrase, not a regex
    assert not filter_rules.evaluate(_job("Senior NOC Technician"), p).passed
    assert not filter_rules.evaluate(_job("Sales Engineer"), p).passed
    onsite = filter_rules.evaluate(_job("NOC Technician", location="Jacksonville, FL"), p)
    assert onsite.passed and onsite.remote_eligibility == "HOME_OK"
    assert not filter_rules.evaluate(_job("NOC Technician", location="Toronto, Canada"), p).passed
    assert rules["opportunity"]["work_modes"] == {"remote": "favorable", "onsite": "unfavorable"}
    assert rules["freshness"]["max_age_days"] is None and rules["salary"]["floor_annual"] == 55000


def test_form_edit_keeps_tuned_patterns():
    base = simple.from_form(FORM, None)
    base["title"]["include"].append("systems? (engineer|analyst)")  # tuned by hand / from the scanner
    base["location"]["non_us_location_markers"] = ["only-this-marker"]
    form = simple.to_form(base)
    # Shown as plain words where possible; a pattern with real regex logic is kept but not editable text.
    assert form["titles"] == ["help desk", "noc technician", "c++ support"] and form["home_state"] == "FL"
    assert form["kept_patterns"] == {"titles": ["systems? (engineer|analyst)"]}
    form["titles"].append("Service Desk")
    again = simple.from_form(form, base)
    assert "systems? (engineer|analyst)" in again["title"]["include"]  # kept exactly
    assert again["location"]["non_us_location_markers"] == ["only-this-marker"]  # field the form does not own
    assert r"service\s+desk" in again["title"]["include"]


@pytest.mark.parametrize("bad,msg", [({"titles": []}, "at least one job title"), ({"home_state": "Florida"}, "two-letter"),
                                     ({"work_modes": {"remote": "love it"}}, "liked, fine or disliked")])
def test_form_errors_read_like_sentences(bad, msg):
    with pytest.raises(ProfileError, match=msg):
        simple.from_form({**FORM, **bad}, None)


def test_setup_routes_and_status(paths):
    async def check(client, runtime):
        s = (await client.get("/v1/setup/status", headers=AUTH)).json()
        assert s["complete"] is False and s["employers"]["done"] is False and s["search_profile"]["done"] is False
        form = (await client.get("/v1/search-profile/form", headers=AUTH)).json()
        assert form["has_profile"] is False and "senior" in form["skip_seniority"]
        r = await client.post("/v1/search-profile/form", headers=AUTH, json=FORM)
        assert r.status_code == 200 and r.json()["version"] == 1
        assert (await client.post("/v1/search-profile/form", headers=AUTH, json={**FORM, "titles": []})).status_code == 422
        added = (await client.post("/v1/employers/add", headers=AUTH, json={"text": "https://jobs.lever.co/acme\nhttps://acme.wd1.myworkdayjobs.com/careers"})).json()
        assert added["added"] == 1 and added["not_understood"] == ["https://acme.wd1.myworkdayjobs.com/careers"]
        wl = (await client.post("/v1/employers/import-watchlist", headers=AUTH,
                                json={"text": "employers:\n  - {name: Beta, ats: ashby, slug: beta}\n"})).json()
        assert wl["added"] == 1
        sc = await client.post("/v1/search-profile/import-scanner", headers=AUTH,
                               json={"text": "title:\n  include: [noc]\nlocation: {}\nsalary: {floor_annual: 50000}\n"})
        assert sc.status_code == 200 and sc.json()["version"] == 2
        assert (await client.post("/v1/search-profile/import-scanner", headers=AUTH, json={"text": "nope: 1"})).status_code == 422
        eid = runtime.reader.execute("SELECT id FROM employers WHERE slug = 'acme'").fetchone()[0]
        assert (await client.post(f"/v1/employers/{eid}/disable", headers=AUTH)).json() == {"enabled": False}
        assert (await client.post("/v1/employers/9999/enable", headers=AUTH)).status_code == 404
        s = (await client.get("/v1/setup/status", headers=AUTH)).json()
        assert s["employers"] == {"done": True, "enabled": 1} and s["search_profile"]["version"] == 2

    _run_with_fake(paths, FakeServer(), check)


def test_scanner_style_rules_read_as_plain_words():
    """Rules imported from job-scanner's config.yaml contain regex; the form shows plain words where
    a pattern is simple and keeps the rest unchanged on save (Kevin: users do not read regex/JSON)."""
    from jobforge_agent.filtering.profile import from_scanner_config
    rules = from_scanner_config({"title": {"include": ["noc", r"help ?desk", "systems? administrator", "operations engineer"],
                                           "exclude_seniority": ["senior", r"sr\.?", "intern|internship"],
                                           "exclude_non_it": ["sales", "recruit(er|ers|ing|ment)?"]},
                                 "location": {"jax_metro": ["jacksonville"]}, "salary": {"floor_annual": 60000}})
    form = simple.to_form(rules)
    assert form["titles"] == ["noc", "operations engineer"]
    assert form["skip_seniority"] == ["senior", "sr"]
    assert form["kept_patterns"]["titles"] == [r"help ?desk", "systems? administrator"]
    saved = simple.from_form({**form, "titles": form["titles"] + ["Help Desk"]}, rules)
    assert saved["title"]["include"] == ["noc", "operations engineer", r"help\s+desk", r"help ?desk", "systems? administrator"]
    assert saved["title"]["exclude_seniority"] == ["senior", r"sr\.?", "intern|internship"]  # "sr" maps back to the original


def test_defaults_shown_to_users_are_plain_words():
    d = simple.defaults()
    for field in ("exclude_seniority", "exclude_non_it"):
        assert all(simple.to_plain(p) is not None for p in d[field]), field
