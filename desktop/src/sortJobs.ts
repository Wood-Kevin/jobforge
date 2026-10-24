// Column sorting for the job list. The agent already sends each tab in its default order (For you by
// recommendation and opportunity value, the others newest first); clicking a header re-sorts the rows
// the window holds. Rows without a value (no recommendation yet) stay last in either direction.
import type { JobRow } from "./api";

export type SortKey = "job" | "recommendation" | "reason" | "fit" | "opportunity" | "found";
export type Sort = { key: SortKey; dir: "asc" | "desc" };

const REC = ["APPLY", "STRETCH", "REVIEW", "SKIP"];
const FIT = ["STRONG", "COMPETITIVE", "STRETCH", "WEAK", "INSUFFICIENT"];
const OPP = ["HIGH", "MEDIUM", "LOW", "UNKNOWN"];

// What a first click on each header does: best or newest first.
export const FIRST_DIR: Record<SortKey, Sort["dir"]> = {
  job: "asc", recommendation: "asc", reason: "asc", fit: "asc", opportunity: "asc", found: "desc" };

function value(j: JobRow, key: SortKey, reason: (j: JobRow) => string): number | string | null {
  const r = j.recommendation;
  const rank = (list: string[], v: string | undefined) => (v && list.includes(v) ? list.indexOf(v) : null);
  switch (key) {
    case "job": return `${j.title} ${j.company}`.toLowerCase();
    // Within a category, the agent's ranking (higher rank_key first) decides, as on the default view.
    case "recommendation": return r ? rank(REC, r.recommendation)! * 1e6 - (r.rank_key ?? 0) : null;
    case "reason": return reason(j).toLowerCase();
    case "fit": return rank(FIT, r?.fit);
    case "opportunity": return rank(OPP, r?.opportunity);
    case "found": return j.first_seen_at_ms;
  }
}

export function sortJobs(rows: JobRow[], sort: Sort, reason: (j: JobRow) => string = () => ""): JobRow[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return rows.map((j) => ({ j, v: value(j, sort.key, reason) })).sort((a, b) => {
    if (a.v === null || b.v === null) return a.v === b.v ? a.j.id - b.j.id : a.v === null ? 1 : -1;
    const c = a.v < b.v ? -1 : a.v > b.v ? 1 : 0;
    return c !== 0 ? c * sign : a.j.id - b.j.id;
  }).map((x) => x.j);
}
