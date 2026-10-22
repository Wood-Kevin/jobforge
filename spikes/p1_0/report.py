"""Render results/eval/*.result.json as Markdown: summary table plus every miss.
Run: python -I spikes/p1_0/report.py > spikes/p1_0/results/eval/REPORT.md
"""
import glob
import json
import pathlib

HERE = pathlib.Path(__file__).parent
files = sorted(glob.glob(str(HERE / "results" / "eval" / "*.result.json")))
res = [json.load(open(f, encoding="utf-8")) for f in files]

print("# P1.0b model evaluation (prompt e1-draft-2)\n")
print("| Model | Mode | Valid JSON | Quotes found | Probes found | Probes fully correct | importance | polarity | subject | eligibility_type | min_years | Traps violated | Snippets ok | Posting s (median / max) | Snippet s (median) | Reasoning tokens (median) |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in res:
    s = r["summary"]
    fa = s["field_accuracy_when_found"]
    print(f"| {s['model']} | {s['mode']} | {s['valid_json']} | {s['quotes_found']} | {s['probes_found']} | {s['probes_all_fields_correct']} | "
          f"{fa.get('importance', '-')} | {fa.get('polarity', '-')} | {fa.get('subject', '-')} | {fa.get('eligibility_type', '-')} | {fa.get('min_years', '-')} | "
          f"{s['trap_violations']} | {s['snippets_ok']} | {s['posting_seconds_median']} / {s['posting_seconds_max']} | {s['snippet_seconds_median']} | {s['reasoning_tokens_median']} |")

print("\nSnippets by area:\n")
areas = sorted({a for r in res for a in r["summary"]["snippets_by_area"]})
print("| Model | Mode | " + " | ".join(areas) + " |")
print("|---|---|" + "---|" * len(areas))
for r in res:
    s = r["summary"]
    print(f"| {s['model']} | {s['mode']} | " + " | ".join(s["snippets_by_area"].get(a, "-") for a in areas) + " |")

for r in res:
    s = r["summary"]
    print(f"\n## {s['model']} [{s['mode']}]\n")
    print(f"finish reasons: {s['finish_reasons']}\n")
    for stem, p in r["postings"].items():
        if p.get("error"):
            print(f"- **{stem}**: ERROR {p['error'][:160]}")
            continue
        misses = [pr for pr in p["probes"] if not pr["found"] or not all(pr["fields"].values())]
        traps = [t for t in p["traps"] if t["violated"]]
        nf = p.get("quotes_not_found", [])
        if not (misses or traps or nf or p.get("info")):
            continue
        print(f"- **{stem}** ({p['n_items']} items, {p['seconds']} s)")
        for pr in misses:
            if not pr["found"]:
                print(f"  - probe not extracted: \"{pr['anchor']}\"")
            else:
                wrong = [f for f, ok in pr["fields"].items() if not ok]
                print(f"  - probe \"{pr['anchor']}\": wrong {wrong}; got {pr['got']}")
        for t in traps:
            print(f"  - TRAP read as a candidate requirement: \"{t['anchor']}\" -> {t['got']}")
        for q in nf:
            print(f"  - quote not found in posting: \"{(q or '')[:90]}\"")
        for i in p.get("info", []):
            print(f"  - info \"{i['anchor']}\": {i['got']}")
    bad = [sn for sn in r["snippets"] if sn.get("ok") is False]
    info = [sn for sn in r["snippets"] if sn.get("ok") is None]
    if bad:
        print("\nSnippet misses:")
        for sn in bad:
            print(f"  - {sn['id']} ({sn['area']}): {sn.get('got') or sn.get('error')} fields={sn.get('fields')}")
    for sn in info:
        print(f"  - info {sn['id']}: {[ {k: g.get(k) for k in ('importance', 'polarity', 'subject')} for g in (sn.get('got') or [])]}")
