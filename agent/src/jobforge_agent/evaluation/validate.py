"""E4: code decides what the model's matches are worth (ARCHITECTURE 6.3, 6.4 E4).

Pure functions; persistence (store.py) inserts the citations through `match_citations`, whose FK
to `profile_snapshot_items` rejects any id outside the evaluated snapshot (an inferred claim, an
invented id). A rejected citation is reported back here as `rejected` and the match is validated
again without it, so "covered only by an inferred claim" ends as unknown (6.6 layer B).

- direct needs >= 1 citation; transferable needs >= 1 citation and a non-empty bridge; else unknown.
- A gap needs a reason (gap_detail or cited items). A certification gap with no citation needs
  `cert_list_complete`: otherwise absence from the profile is not evidence (unknown).
- Years: when the requirement names a minimum and the cited items state time, code computes the
  years they show (date intervals merged, so overlapping roles are not double-counted). Fewer than
  the minimum turns a model "direct"/"transferable" into a gap.
- "I don't have this" (0012): a requirement whose line the person answered that way is a gap whatever
  the model said, evidenced (flag `absence_confirmed`, by "you_said"), like a missing cert on a complete list.
- Materiality: a gap on a preferred requirement is minor; on a negotiable one it is material
  unless it is a years shortfall within tolerance (verdict rules; v4: 1 year or 25%).
- basis: verified if a cited item is verified, else self_reported; none without citations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jobforge_agent.evaluation.facts import FactCheck, Req
from jobforge_agent.evaluation.match import item_id
from jobforge_agent.evaluation.snapshot import Item, Snapshot, line_key

CERT = re.compile(r"\bcertif\w*|\bccn[ap]\b|\bcissp\b|\bcism\b|\bcisa\b|\bsecurity\s?\+|\bnetwork\s?\+|\bcysa\s?\+?|\bcomptia\b"
                  r"|\bgsec\b|\bgcih\b|\bceh\b|\boscp\b|\bitil\b|\bpmp\b|\brhcsa\b|\brhce\b|\bmcsa\b|\bjncia\b", re.I)


@dataclass
class Validated:
    requirement_id: int
    matched_by: str                    # model / eligibility_fact
    match_type: str                    # direct / transferable / gap / unknown
    basis: str                         # verified / self_reported / none
    citations: list[int] = field(default_factory=list)
    bridge: str | None = None
    transfer_limits: str | None = None
    gap_size: dict | None = None
    gap_materiality: str = "none"
    flags: list[dict] = field(default_factory=list)

    def has_flag(self, name: str) -> bool:
        return any(f.get("flag") == name for f in self.flags)


def _month(d: str) -> int:
    y, m = int(d[:4]), int(d[5:7])
    return y * 12 + m - 1


def years_shown(items: list[Item], now_ms: int) -> float | None:
    """Years the items state: dated items as merged month intervals (start and end month both
    count), undated items by their `years`. None when no item states any time."""
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    now_m = now.year * 12 + now.month - 1
    spans = sorted((_month(i.start_date), _month(i.end_date) if i.end_date else now_m)
                   for i in items if i.start_date)
    merged: list[list[int]] = []
    for a, b in spans:
        if b < a:
            continue
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    dated = sum(b - a + 1 for a, b in merged) / 12 if merged else None
    undated = [i.years for i in items if not i.start_date and i.years is not None]
    values = ([dated] if dated is not None else []) + undated
    return round(max(values), 2) if values else None


def materiality(req: Req, gap_size: dict | None, rules: dict) -> str:
    if req.final_class == "preferred":
        return "minor"
    if gap_size and gap_size.get("years_short") is not None:
        need, short = gap_size["years_needed"], gap_size["years_short"]
        g = rules["gap"]
        if short <= g["years_tolerance_abs"] or short <= g["years_tolerance_share"] * need:
            return "minor"
    return "material"


def _basis(items: list[Item]) -> str:
    if not items:
        return "none"
    return "verified" if any(i.status == "verified" for i in items) else "self_reported"


def model_match(req: Req, m, snap: Snapshot, completeness: dict, rules: dict, now_ms: int,
                rejected: frozenset[int] = frozenset()) -> Validated:
    """Validate one E3 match (`m` is a match.parse() item, or None if the model omitted it)."""
    if any(line_key(req.jd_quote) == line_key(x) for x in completeness.get("lacking") or []):
        # The person answered "I don't have this" to this line (0012). Their word outranks whatever the model
        # read in their profile, direct included: they answered about the whole line, alternatives and all.
        return Validated(req.id, "model", "gap", "none", gap_size={"detail": "You said you don't have this"},
                         gap_materiality=materiality(req, None, rules),
                         flags=[{"flag": "absence_confirmed", "by": "you_said", "model": m.match_type if m else None}])
    if m is None:
        return Validated(req.id, "model", "unknown", "none", flags=[{"flag": "model_omitted"}])
    flags: list[dict] = []
    cited_ids = list(dict.fromkeys(item_id(c) for c in m.cited_items))
    if rejected:
        flags.append({"flag": "citation_rejected", "items": sorted(rejected), "why": "not in the evaluated profile snapshot"})
    # Not filtered against the snapshot here: the FK on match_citations is the guard, and what
    # it rejects comes back as `rejected` (store.py). `items` (for basis and years) are the
    # cited ids the snapshot has.
    cited_ids = [i for i in cited_ids if i not in rejected]
    # Not citations: an eligibility fact (in the snapshot, so the FK accepts it, but never evidence of
    # experience; the prompt does not list them) and ids SQLite cannot store (independent P1.7 review).
    facts_cited = [i for i in cited_ids if i in snap.items and snap.items[i].kind == "eligibility"]
    too_big = [i for i in cited_ids if i >= 2 ** 63]
    if facts_cited:
        flags.append({"flag": "cites_eligibility_fact", "items": facts_cited, "why": "not counted"})
    if too_big:
        flags.append({"flag": "citation_rejected", "items": [str(i) for i in too_big], "why": "id out of range"})
    cited_ids = [i for i in cited_ids if i not in facts_cited and i not in too_big]
    items = [snap.items[i] for i in cited_ids if i in snap.items]
    cited = bool(cited_ids)
    model_type = mt = m.match_type
    bridge = (m.bridge or "").strip() or None
    limits = (m.transfer_limits or "").strip() or None
    detail = (m.gap_detail or "").strip() or None

    if mt == "direct" and not cited:
        mt = "unknown"
        flags.append({"flag": "direct_without_citation"})
    elif mt == "transferable" and (not cited or not bridge):
        mt = "unknown"
        flags.append({"flag": "transferable_without_citation" if not cited else "transferable_without_bridge"})
    elif mt == "gap" and not cited:
        if not detail:
            mt = "unknown"
            flags.append({"flag": "gap_without_reason"})
        elif CERT.search(f"{req.text} {req.jd_quote}"):
            if completeness.get("cert_list_complete"):
                # Kevin marked his cert list complete: absence from it is evidence (E5 counts this gap).
                flags.append({"flag": "absence_confirmed", "by": "cert_list_complete"})
            else:
                mt = "unknown"
                flags.append({"flag": "absence_not_evidence", "why": "certification not in profile and cert list not marked complete"})

    gap_size = None
    # Only the cited items count, so a model that cites one role of several under-counts years; the E3
    # prompt (m2-v2) asks for every item that shows the experience (test_v4_years_count_only_what_the_model_cites).
    if req.min_years is not None and mt in ("direct", "transferable", "gap") and items:
        have = years_shown(items, now_ms)
        if have is None:
            flags.append({"flag": "years_not_checkable", "why": "cited items state no dates or years"})
        elif have < req.min_years:
            gap_size = {"years_needed": req.min_years, "years_shown": have, "years_short": round(req.min_years - have, 2)}
            if mt != "gap":
                flags.append({"flag": "years_short_override", "model": mt})
                mt = "gap"
        elif mt == "gap":
            flags.append({"flag": "years_met_model_gap", "years_shown": have})
    if mt == "gap":
        gap_size = {**(gap_size or {}), **({"detail": detail} if detail else {})} or None
    if mt != model_type:
        flags.append({"flag": "downgraded", "model": model_type, "final": mt})
    return Validated(req.id, "model", mt, _basis(items) if mt != "unknown" else "none",
                     citations=cited_ids if mt != "unknown" else [],
                     bridge=bridge if mt == "transferable" else None, transfer_limits=limits if mt == "transferable" else None,
                     gap_size=gap_size if mt == "gap" else None,
                     gap_materiality=materiality(req, gap_size, rules) if mt == "gap" else "none", flags=flags)


def not_sent(req: Req, why: str) -> Validated:
    """A fit requirement the model was not asked about (empty profile)."""
    return Validated(req.id, "model", "unknown", "none", flags=[{"flag": why}])


def fact_match(req: Req, fc: FactCheck, rules: dict) -> Validated:
    mt = {"met": "direct", "fails": "gap", "unanswered": "unknown"}[fc.outcome]
    flag = fc.to_flag()
    if fc.question:
        flag["question"] = fc.question
    return Validated(req.id, "eligibility_fact", mt, fc.basis, citations=list(fc.item_ids),
                     gap_size={"eligibility": fc.detail} if mt == "gap" else None,
                     gap_materiality=materiality(req, None, rules) if mt == "gap" else "none", flags=[flag])
