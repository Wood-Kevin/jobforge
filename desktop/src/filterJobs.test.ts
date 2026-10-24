import { describe, expect, it } from "vitest";
import type { JobRow } from "./api";
import { NO_FILTERS, filterJobs, isFiltered } from "./filterJobs";

const DAY = 86_400_000;
const NOW = 100 * DAY;

function job(id: number, foundDaysAgo: number, fit?: string, opportunity?: string, primary: string | null = null): JobRow {
  return { id, title: `Job ${id}`, company: "Co", location: null, work_mode: null, posted_at: null, first_seen_at_ms: NOW - foundDaysAgo * DAY,
    filter_verdict: fit ? "pass" : "reject", filter_primary: primary, override: false, application_status: null, copies: 1, skipped: null,
    recommendation: fit ? { recommendation: "APPLY", eligibility: "ELIGIBLE", fit, opportunity: opportunity!, rank_key: 0,
      current: true, newer_evaluation_pending: false } as JobRow["recommendation"] : null };
}

const rows = [job(1, 0.5, "STRONG", "HIGH"), job(2, 3, "COMPETITIVE", "MEDIUM"), job(3, 20, "STRONG", "MEDIUM"), job(4, 2)];
const ids = (r: JobRow[]) => r.map((j) => j.id);

describe("filterJobs", () => {
  it("keeps everything with no filter set", () => {
    expect(ids(filterJobs(rows, NO_FILTERS, NOW))).toEqual([1, 2, 3, 4]);
    expect(isFiltered(NO_FILTERS)).toBe(false);
  });
  it("filters by fit and opportunity together; rows without a recommendation drop out", () => {
    expect(ids(filterJobs(rows, { ...NO_FILTERS, fit: "STRONG" }, NOW))).toEqual([1, 3]);
    expect(ids(filterJobs(rows, { ...NO_FILTERS, fit: "STRONG", opportunity: "MEDIUM" }, NOW))).toEqual([3]);
  });
  it("filters by when the job was found", () => {
    expect(ids(filterJobs(rows, { ...NO_FILTERS, foundWithinDays: 1 }, NOW))).toEqual([1]);
    expect(ids(filterJobs(rows, { ...NO_FILTERS, foundWithinDays: 7 }, NOW))).toEqual([1, 2, 4]);
  });
  it("filters by the reason text the column shows", () => {
    const r = [job(5, 1, undefined, undefined, "TITLE_NO_MATCH"), job(6, 1, undefined, undefined, "STALE_POSTING")];
    const reason = (j: JobRow) => (j.filter_primary === "STALE_POSTING" ? "Too old" : "Title not on your list");
    expect(ids(filterJobs(r, { ...NO_FILTERS, reason: "Too old" }, NOW, reason))).toEqual([6]);
  });
});
