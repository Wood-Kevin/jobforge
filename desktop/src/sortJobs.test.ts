import { describe, expect, it } from "vitest";
import type { JobRow } from "./api";
import { sortJobs } from "./sortJobs";

function job(id: number, title: string, found: number, rec?: [string, string, string, number]): JobRow {
  return { id, title, company: "Co", location: null, work_mode: null, posted_at: null, first_seen_at_ms: found,
    filter_verdict: "pass", filter_primary: null, override: false, application_status: null, copies: 1, skipped: null,
    recommendation: rec ? { recommendation: rec[0], eligibility: "ELIGIBLE", fit: rec[1], opportunity: rec[2], rank_key: rec[3],
      current: true, newer_evaluation_pending: false } as JobRow["recommendation"] : null };
}

const rows = [
  job(1, "NOC Engineer", 100, ["REVIEW", "INSUFFICIENT", "MEDIUM", 5]),
  job(2, "Help Desk", 300, ["APPLY", "COMPETITIVE", "MEDIUM", 1]),
  job(3, "Sysadmin", 200, ["APPLY", "STRONG", "HIGH", 9]),
  job(4, "Being read", 400),
];
const ids = (r: JobRow[]) => r.map((j) => j.id);

describe("sortJobs", () => {
  it("sorts by found, newest first, and back", () => {
    expect(ids(sortJobs(rows, { key: "found", dir: "desc" }))).toEqual([4, 2, 3, 1]);
    expect(ids(sortJobs(rows, { key: "found", dir: "asc" }))).toEqual([1, 3, 2, 4]);
  });
  it("orders recommendations by category, then the agent's ranking; rows without one stay last", () => {
    expect(ids(sortJobs(rows, { key: "recommendation", dir: "asc" }))).toEqual([3, 2, 1, 4]);
    expect(ids(sortJobs(rows, { key: "recommendation", dir: "desc" }))).toEqual([1, 2, 3, 4]);
  });
  it("orders fit and opportunity best first, and titles alphabetically", () => {
    expect(ids(sortJobs(rows, { key: "fit", dir: "asc" }))).toEqual([3, 2, 1, 4]);
    expect(ids(sortJobs(rows, { key: "opportunity", dir: "asc" }))).toEqual([3, 1, 2, 4]);
    expect(ids(sortJobs(rows, { key: "job", dir: "asc" }))).toEqual([4, 2, 1, 3]);
  });
  it("does not change the rows it was given", () => {
    sortJobs(rows, { key: "job", dir: "asc" });
    expect(ids(rows)).toEqual([1, 2, 3, 4]);
  });
});
