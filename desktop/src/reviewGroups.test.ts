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
