// Column filters for the job list, beside the column sort (sortJobs.ts) and the recommendation chips.
// Like the sort they narrow the rows the window loaded; an empty value means "any".
import type { JobRow } from "./api";

export type Filters = { fit: string; opportunity: string; reason: string; foundWithinDays: number | null };
export const NO_FILTERS: Filters = { fit: "", opportunity: "", reason: "", foundWithinDays: null };
export const FOUND_WITHIN: [number, string][] = [[1, "last 24 hours"], [7, "last 7 days"], [30, "last 30 days"]];

export function isFiltered(f: Filters): boolean {
  return Boolean(f.fit || f.opportunity || f.reason || f.foundWithinDays !== null);
}

// `reason` is the row's plain filter reason (the same text its column shows).
export function filterJobs(rows: JobRow[], f: Filters, now: number, reason: (j: JobRow) => string = () => ""): JobRow[] {
  const since = f.foundWithinDays === null ? null : now - f.foundWithinDays * 86_400_000;
  return rows.filter((j) =>
    (!f.fit || j.recommendation?.fit === f.fit)
    && (!f.opportunity || j.recommendation?.opportunity === f.opportunity)
    && (!f.reason || reason(j) === f.reason)
    && (since === null || j.first_seen_at_ms >= since));
}
