"""P1.0b spike: does a local model return schema-valid E1 output with real quotes?

For each sample posting in samples/ and each model given on the command line:
- send the full plain-text description with a draft E1 prompt and JSON schema
- record: valid JSON + schema, latency, token usage, requirement count
- check every jd_quote is a substring of the normalized description (code, not model)
- save the raw output for a manual read of polarity / subject / importance

Throwaway measurement code, not app code. Prints raw evidence, no verdicts.

Run: python -I spikes/p1_0/extract_spike.py <model_id> [<model_id> ...]
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import sys
import time
from html.parser import HTMLParser

import httpx

BASE = "http://127.0.0.1:1234/v1"
HERE = pathlib.Path(__file__).parent
SAMPLES = sorted((HERE / "samples").glob("*.json"))[:5]
OUT = HERE / "results" / "extract"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirements"],
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "jd_quote", "importance", "polarity", "subject", "eligibility_type", "min_years"],
                "properties": {
                    "text": {"type": "string"},
                    "jd_quote": {"type": "string"},
                    "importance": {"type": "string", "enum": ["hard", "required", "preferred"]},
                    "polarity": {"type": "string", "enum": ["affirmative", "negated"]},
                    "subject": {"type": "string", "enum": ["candidate", "company", "team", "benefit"]},
                    "eligibility_type": {"type": "string", "enum": [
                        "none", "work_authorization", "sponsorship", "clearance", "license",
                        "residency", "travel", "physical"]},
                    "min_years": {"type": ["number", "null"]},
                },
            },
        }
    },
}

SYSTEM = """You extract candidate requirements from a job posting.
Rules:
- One item per distinct requirement or qualification the posting states about the CANDIDATE.
- jd_quote: copy a short contiguous span EXACTLY as it appears in the posting (same words, same order). Do not paraphrase, merge, or fix typos.
- importance: "hard" only for eligibility conditions stated as mandatory (work authorization, sponsorship, holding a clearance, a legally required license, where you must live, travel or physical demands). "required" for other must-haves. "preferred" for nice-to-haves, pluses, bonuses.
- polarity: "affirmative" for anything the posting asks for or prefers; "negated" only when the posting says something is NOT required (e.g. "no degree required").
- subject: "candidate" for requirements on the applicant; "company"/"team" for statements describing the company, product, customers, or stack; "benefit" for perks.
- eligibility_type: the eligibility category, or "none".
- min_years: minimum years of experience if stated for this item, else null.
Return JSON only."""


class _Text(HTMLParser):
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(s: str) -> str:
    p = _Text()
    p.feed(html.unescape(s))  # Greenhouse content is entity-escaped HTML
    return re.sub(r"\n\s*\n+", "\n\n", "".join(p.parts)).strip()


def posting_text(path: pathlib.Path) -> tuple[str, str]:
    j = json.loads(path.read_text(encoding="utf-8"))
    if path.name.startswith("greenhouse"):
        return j["title"], html_to_text(j["content"])
    if path.name.startswith("lever"):
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists", []):
            parts.append(lst["text"])
            parts.append(html_to_text(lst["content"]))
        parts.append(j.get("additionalPlain", ""))
        return j["text"], "\n\n".join(p for p in parts if p)
    if path.name.startswith("ashby"):
        return j["title"], j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
    raise ValueError(path.name)


def norm(s: str) -> str:
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐-―]", "-", s)
    s = re.sub(r"^\s*[-*•]\s*", " ", s, flags=re.M)
    return re.sub(r"\s+", " ", s).strip()


def run(model: str) -> list[dict]:
    rows = []
    out_dir = OUT / model.replace("/", "_").replace("@", "_").replace("?", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=600) as client:
        for path in SAMPLES:
            title, text = posting_text(path)
            body = {
                "model": model,
                "temperature": 0,
                "seed": 7,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"Job title: {title}\n\nPosting:\n{text}"},
                ],
                "response_format": {"type": "json_schema", "json_schema": {"name": "e1", "strict": True, "schema": SCHEMA}},
            }
            t0 = time.perf_counter()
            row = {"model": model, "posting": path.stem, "chars": len(text)}
            try:
                r = client.post(f"{BASE}/chat/completions", json=body)
                row["http"] = r.status_code
                row["seconds"] = round(time.perf_counter() - t0, 1)
                resp = r.json()
                row["usage"] = resp.get("usage")
                row["model_reported"] = resp.get("model")
                content = resp["choices"][0]["message"]["content"]
                (out_dir / f"{path.stem}.json").write_text(content, encoding="utf-8")
                data = json.loads(content)
                reqs = data["requirements"]
                row["valid_json"] = True
                row["n_requirements"] = len(reqs)
                ntext = norm(text)
                found = [norm(q["jd_quote"]) in ntext for q in reqs]
                row["quotes_found"] = f"{sum(found)}/{len(reqs)}"
                row["quotes_not_found"] = [q["jd_quote"] for q, f in zip(reqs, found) if not f][:5]
                row["negated"] = [q["jd_quote"] for q in reqs if q["polarity"] == "negated"]
                row["non_candidate"] = [(q["subject"], q["jd_quote"]) for q in reqs if q["subject"] != "candidate"]
                row["hard"] = [(q["eligibility_type"], q["jd_quote"]) for q in reqs if q["importance"] == "hard"]
                row["counts"] = {k: sum(1 for q in reqs if q["importance"] == k) for k in ("hard", "required", "preferred")}
            except Exception as exc:  # spike: record the failure, keep going
                row["error"] = f"{type(exc).__name__}: {exc}"[:300]
                row.setdefault("seconds", round(time.perf_counter() - t0, 1))
            rows.append(row)
            print(json.dumps(row), flush=True)
    return rows


def main() -> int:
    models = sys.argv[1:]
    if not models:
        print("usage: extract_spike.py <model_id> ...")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "postings").mkdir(exist_ok=True)
    for path in SAMPLES:  # save the exact text sent, so quotes can be re-checked by hand
        title, text = posting_text(path)
        (OUT / "postings" / f"{path.stem}.txt").write_text(f"{title}\n\n{text}", encoding="utf-8")
    all_rows = []
    for m in models:
        all_rows += run(m)
    (OUT / f"summary_{int(time.time())}.json").write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
