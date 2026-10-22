"""P1.0b model evaluation spike.

For each model: load it in LM Studio at 16k context, run the draft E1 prompt on
6 representative postings and the 22 interpretation snippets, score against
reference.py, unload. Writes raw outputs, a JSON summary and a Markdown report.
Prints evidence; the report has numbers and lists, no pass/fail verdicts.

Run: python -I spikes/p1_0/eval_spike.py google/gemma-4-12b-qat qwen3.5-9b-tuned ...
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import time

import httpx

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import extract_spike as e1  # noqa: E402  (draft prompt, schema, text helpers)
import reference as ref  # noqa: E402

BASE = "http://127.0.0.1:1234/v1"
SAMPLES = sorted((HERE / "samples_rep").glob("*.json"))
OUT = HERE / "results" / "eval"
LMS = str(pathlib.Path.home() / ".lmstudio" / "bin" / "lms.exe")
PROMPT_VERSION = "e1-draft-2"
CONTEXT = 16384


def sh(*args: str) -> str:
    r = subprocess.run([LMS, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout + r.stderr).strip()


# fast: reasoning off where the server allows it ("none"); gpt-oss has no off switch, so "low".
# think: the model's default reasoning, with a larger output budget.
MODES = {"fast": {"max_tokens": 4000}, "think": {"max_tokens": 12000}}


def reasoning_args(model: str, mode: str) -> dict:
    if mode == "think":
        return {}
    return {"reasoning_effort": "low" if "gpt-oss" in model else "none"}


def call(client: httpx.Client, model: str, mode: str, title: str, text: str) -> dict:
    body = {
        "model": model, "temperature": 0, "seed": 7, "max_tokens": MODES[mode]["max_tokens"],
        "messages": [{"role": "system", "content": e1.SYSTEM},
                     {"role": "user", "content": f"Job title: {title}\n\nPosting:\n{text}"}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "e1", "strict": True, "schema": e1.SCHEMA}},
        **reasoning_args(model, mode),
    }
    t0 = time.perf_counter()
    out = {"seconds": None, "usage": None, "items": None, "error": None, "raw": None, "finish_reason": None}
    try:
        r = client.post(f"{BASE}/chat/completions", json=body)
        out["seconds"] = round(time.perf_counter() - t0, 2)
        resp = r.json()
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}: {json.dumps(resp)[:300]}"
            return out
        out["usage"] = resp.get("usage")
        out["finish_reason"] = resp["choices"][0].get("finish_reason")
        content = resp["choices"][0]["message"].get("content") or ""
        out["raw"] = content or json.dumps(resp)[:4000]  # keep the whole response when content is empty
        out["items"] = json.loads(content)["requirements"]
    except Exception as exc:  # spike: keep going, record it
        out["seconds"] = out["seconds"] or round(time.perf_counter() - t0, 2)
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return out


def span(ntext: str, needle: str) -> tuple[int, int] | None:
    n = e1.norm(needle)
    i = ntext.find(n) if n else -1
    return (i, i + len(n)) if i >= 0 else None


def overlapping(items: list[dict], ntext: str, anchor: str) -> list[dict]:
    a = span(ntext, anchor)
    na = e1.norm(anchor)
    hits = []
    for it in items:
        q = it.get("jd_quote", "")
        s = span(ntext, q)
        if a and s and s[0] < a[1] and a[0] < s[1]:
            hits.append(it)
        elif na and (na in e1.norm(q) or (len(e1.norm(q)) >= 12 and e1.norm(q) in na)):
            hits.append(it)
    return hits


def field_ok(item: dict, field: str, want) -> bool:
    v = item.get(field)
    if isinstance(want, set):
        return v in want
    return v == want


def best_match(hits: list[dict], expect: dict) -> tuple[dict | None, dict]:
    best, best_res = None, {}
    for it in hits:
        res = {f: field_ok(it, f, w) for f, w in expect.items()}
        if best is None or sum(res.values()) > sum(best_res.values()):
            best, best_res = it, res
    return best, best_res


def is_candidate_req(it: dict) -> bool:
    return it.get("subject") == "candidate" and it.get("polarity") == "affirmative" and it.get("importance") in ref.HARD_OR_REQ


def score_posting(stem: str, text: str, items: list[dict]) -> dict:
    spec = ref.PROBES[stem]
    ntext = e1.norm(text)
    probes, traps = [], []
    for p in spec["probes"]:
        hits = overlapping(items, ntext, p["anchor"])
        item, res = best_match(hits, p["expect"])
        probes.append({"anchor": p["anchor"], "found": bool(hits), "fields": res,
                       "got": {k: item.get(k) for k in ("importance", "polarity", "subject", "eligibility_type", "min_years")} if item else None})
    for t in spec["traps"]:
        bad = [it for it in overlapping(items, ntext, t) if is_candidate_req(it)]
        traps.append({"anchor": t, "violated": bool(bad), "got": [{k: b.get(k) for k in ("jd_quote", "importance", "eligibility_type")} for b in bad]})
    info = []
    for a in spec.get("info", []):
        info.append({"anchor": a, "got": [{k: it.get(k) for k in ("jd_quote", "importance", "eligibility_type", "subject")} for it in overlapping(items, ntext, a)]})
    found_q = [span(ntext, it.get("jd_quote", "")) is not None for it in items]
    return {"probes": probes, "traps": traps, "info": info,
            "quotes_found": sum(found_q), "n_items": len(items),
            "quotes_not_found": [it.get("jd_quote") for it, f in zip(items, found_q) if not f]}


def score_snippet(sn: tuple, items: list[dict]) -> dict:
    sid, area, text, kind, anchor, expect = sn
    ntext = e1.norm(text)
    hits = overlapping(items, ntext, anchor)
    if kind == "target":
        item, res = best_match(hits, expect)
        ok = bool(hits) and all(res.values())
        return {"id": sid, "area": area, "kind": kind, "ok": ok, "found": bool(hits), "fields": res,
                "got": {k: item.get(k) for k in ("importance", "polarity", "subject", "eligibility_type", "min_years")} if item else None}
    if kind == "not_requirement":
        bad = [it for it in hits if is_candidate_req(it)]
        return {"id": sid, "area": area, "kind": kind, "ok": not bad, "got": bad}
    if kind == "not_requirement_years":
        bad = [it for it in items if it.get("subject") == "candidate" and it.get("min_years") == 10]
        return {"id": sid, "area": area, "kind": kind, "ok": not bad, "got": bad}
    return {"id": sid, "area": area, "kind": kind, "ok": None, "got": hits}


def run_model(model: str, mode: str) -> dict:
    mdir = OUT / f"{re.sub(r'[^A-Za-z0-9._-]', '_', model)}__{mode}"
    mdir.mkdir(parents=True, exist_ok=True)
    print(f"\n### {model} [{mode}]", flush=True)
    print(sh("unload", "--all"), flush=True)
    t0 = time.perf_counter()
    load_log = sh("load", model, "--context-length", str(CONTEXT), "-y")
    load_s = round(time.perf_counter() - t0, 1)
    ps = sh("ps")
    print(load_log.splitlines()[-3:] if load_log else "", ps, sep="\n", flush=True)
    result = {"model": model, "mode": mode, "request_extras": reasoning_args(model, mode), "max_tokens": MODES[mode]["max_tokens"],
              "prompt_version": PROMPT_VERSION, "context": CONTEXT, "load_seconds": load_s,
              "lms_ps": ps, "postings": {}, "snippets": []}
    with httpx.Client(timeout=1200) as client:
        for p in SAMPLES:
            title, text = e1.posting_text(p)
            r = call(client, model, mode, title, text)
            (mdir / f"{p.stem}.json").write_text(r["raw"] or (r["error"] or ""), encoding="utf-8")
            entry = {"seconds": r["seconds"], "usage": r["usage"], "error": r["error"], "chars": len(text), "finish_reason": r["finish_reason"]}
            if r["items"] is not None:
                entry.update(score_posting(p.stem, text, r["items"]))
            result["postings"][p.stem] = entry
            print(p.stem, entry["seconds"], "s", "err" if r["error"] else f"{entry.get('n_items')} items", flush=True)
        for sn in ref.SNIPPETS:
            r = call(client, model, mode, "Snippet", sn[2])
            s = score_snippet(sn, r["items"]) if r["items"] is not None else {"id": sn[0], "area": sn[1], "kind": sn[3], "ok": False, "error": r["error"]}
            s["seconds"] = r["seconds"]
            result["snippets"].append(s)
        (mdir / "snippets_raw.json").write_text(json.dumps(result["snippets"], indent=2), encoding="utf-8")
    print(sh("unload", "--all"), flush=True)
    return result


def summarize(res: dict) -> dict:
    ps = res["postings"].values()
    probes = [pr for p in ps for pr in p.get("probes", [])]
    fields = {}
    for pr in probes:
        for f, ok in pr["fields"].items():
            a = fields.setdefault(f, [0, 0])
            a[0] += ok
            a[1] += 1
    traps = [t for p in ps for t in p.get("traps", [])]
    secs = [p["seconds"] for p in ps if p["seconds"] is not None and not p["error"]]
    toks = [(p["usage"] or {}).get("completion_tokens") for p in ps if p.get("usage")]
    reasoning = [((p["usage"] or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") or 0 for p in ps if p.get("usage")]
    sn = [s for s in res["snippets"] if s.get("ok") is not None]
    sn_secs = sorted(s["seconds"] for s in res["snippets"] if s.get("seconds") is not None)
    by_area = {}
    for s in sn:
        a = by_area.setdefault(s["area"], [0, 0])
        a[0] += bool(s["ok"])
        a[1] += 1
    return {
        "model": res["model"],
        "mode": res["mode"],
        "finish_reasons": [p.get("finish_reason") for p in ps],
        "reasoning_tokens_median": sorted(reasoning)[len(reasoning) // 2] if reasoning else None,
        "snippet_seconds_median": sn_secs[len(sn_secs) // 2] if sn_secs else None,
        "valid_json": f"{sum(1 for p in ps if not p['error'])}/{len(res['postings'])}",
        "quotes_found": f"{sum(p.get('quotes_found', 0) for p in ps)}/{sum(p.get('n_items', 0) for p in ps)}",
        "probes_found": f"{sum(pr['found'] for pr in probes)}/{len(probes)}",
        "probes_all_fields_correct": f"{sum(pr['found'] and all(pr['fields'].values()) for pr in probes)}/{len(probes)}",
        "field_accuracy_when_found": {f: f"{a[0]}/{a[1]}" for f, a in fields.items()},
        "trap_violations": f"{sum(t['violated'] for t in traps)}/{len(traps)}",
        "snippets_ok": f"{sum(bool(s['ok']) for s in sn)}/{len(sn)}",
        "snippets_by_area": {k: f"{v[0]}/{v[1]}" for k, v in by_area.items()},
        "posting_seconds_median": sorted(secs)[len(secs) // 2] if secs else None,
        "posting_seconds_max": max(secs) if secs else None,
        "completion_tokens_median": sorted(t for t in toks if t)[len([t for t in toks if t]) // 2] if any(toks) else None,
        "load_seconds": res["load_seconds"],
    }


def main() -> int:
    # args: model:mode ...  e.g. google/gemma-4-12b-qat:fast
    runs = [a.rsplit(":", 1) for a in sys.argv[1:]]
    OUT.mkdir(parents=True, exist_ok=True)
    allres = []
    for m, mode in runs:
        res = run_model(m, mode)
        res["summary"] = summarize(res)
        allres.append(res)
        (OUT / f"{re.sub(r'[^A-Za-z0-9._-]', '_', m)}__{mode}.result.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
        (OUT / "summary.json").write_text(json.dumps([r["summary"] for r in allres], indent=2), encoding="utf-8")
        print(json.dumps(res["summary"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
