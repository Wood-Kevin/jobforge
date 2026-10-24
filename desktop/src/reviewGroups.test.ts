import { describe, expect, it } from "vitest";
import type { ReviewGroup } from "./api";
import { byJob } from "./reviewGroups";

const job = (job_id: number, question: string) => ({ job_id, title: `Job ${job_id}`, company: "Co", question, current: true });
const groups: ReviewGroup[] = [
  { kind: "eligibility", type: "residency", example: "Where do you live?", jobs: [job(1, "Where do you live?"), job(2, "Where do you live?")] },
  { kind: "experience", type: null, example: "Kubernetes?", jobs: [job(1, "Kubernetes?")] },
  { kind: "experience", type: null, example: "Go?", jobs: [job(1, "Go?")] },
  { kind: "experience", type: null, example: "SQL?", jobs: [job(3, "SQL?")] },
];

describe("byJob", () => {
  it("counts jobs, not question groups, and lists shared questions apart", () => {
    const { shared, jobs } = byJob(groups);
    expect(shared.map((g) => g.example)).toEqual(["Where do you live?"]);
    expect(jobs.map((j) => [j.job_id, j.questions.length])).toEqual([[2, 0], [3, 1], [1, 2]]);
  });
  it("keeps a shared question out of each job's own list", () => {
    expect(byJob(groups).jobs.find((j) => j.job_id === 1)!.questions.map((q) => q.question)).toEqual(["Kubernetes?", "Go?"]);
  });
});

describe("byJob with reading problems", () => {
  const rp = (quote: string, outcome: "met" | "fails" | "unanswered" = "fails") => ({ outcome, quote, status: "conflict", origin: "model" });
  const withProblems: ReviewGroup[] = [
    { kind: "eligibility", type: "residency", example: "Add a residency fact.", jobs: [
      { ...job(1, "Add a residency fact."), reading_problem: rp("based in India") },
      { ...job(2, "Add a residency fact."), reading_problem: rp("local to the El Dorado Hills area", "unanswered") },
      job(3, "Add a residency fact.")] },
    { kind: "experience", type: null, example: "Kubernetes?", jobs: [job(2, "Kubernetes?")] },
  ];
  it("lists them apart and never counts a job that only has them (Kevin, 2026-10-24)", () => {
    const { shared, jobs, unsettled } = byJob(withProblems);
    expect(shared).toEqual([]);  // the residency group is left with one answerable job
    expect(jobs.map((j) => j.job_id).sort()).toEqual([2, 3]);  // job 1 has nothing you can answer
    expect(unsettled.map((u) => [u.job_id, u.lines.map((l) => [l.type, l.outcome])])).toEqual(
      [[1, [["residency", "fails"]]], [2, [["residency", "unanswered"]]]]);
  });
});
