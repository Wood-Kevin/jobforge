"""Fetch a handful of representative postings (jobs the existing scanner kept on
2026-10-21) for the model evaluation spike. One request per Greenhouse job, one
board request for Ashby, 2 s apart. Saves raw JSON to samples_rep/.

Run: python -I spikes/p1_0/fetch_representative.py
"""
from __future__ import annotations

import json
import pathlib
import time

import httpx

UA = "jobforge-spike/0.1 (personal job search tool; model evaluation sample)"
OUT = pathlib.Path(__file__).parent / "samples_rep"

GREENHOUSE = [
    ("gitlab", "8859063002"),        # Support Engineer, U.S. Government Support
    ("keepersecurity", "4459433009"),  # IT Support Specialist, Tier 2
    ("huntress", "7743188003"),      # Security Operations Analyst - West Coast Shift
    ("axonius", "8009643003"),       # Technical Support Engineer (SkillBridge)
    ("tailscale", "4710785005"),     # Windows Engineer
]
ASHBY = [("vultr", "24c3445f-27b2-40fb-870e-e59b71305787")]  # Linux Systems Administrator


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": UA}, timeout=60) as c:
        for slug, jid in GREENHOUSE:
            r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}")
            print(slug, jid, r.status_code, len(r.content))
            if r.status_code == 200:
                (OUT / f"greenhouse_{slug}_{jid}.json").write_text(r.text, encoding="utf-8")
            time.sleep(2)
        for slug, jid in ASHBY:
            r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            print(slug, "board", r.status_code, len(r.content))
            jobs = {j["id"]: j for j in r.json().get("jobs", [])}
            if jid in jobs:
                (OUT / f"ashby_{slug}_{jid}.json").write_text(json.dumps(jobs[jid], indent=2), encoding="utf-8")
            else:
                print("  not found on board:", jid)
            time.sleep(2)


if __name__ == "__main__":
    main()
