// "Needs your input" in jobs, not question groups (usability pass 2026-10-24): questions several jobs
// share, then each job with only its own questions, fewest first.
import type { ReviewGroup } from "./api";

export type Q = { kind: string; type: string | null; question: string; unrecognized?: string | null };
export type JobQuestions = { job_id: number; title: string; company: string; current: boolean; questions: Q[] };

export function byJob(groups: ReviewGroup[]) {
  const shared = groups.filter((g) => g.jobs.length > 1);
  const jobs = new Map<number, JobQuestions>();
  for (const g of groups) {
    for (const j of g.jobs) {
      const e = jobs.get(j.job_id) ?? { job_id: j.job_id, title: j.title, company: j.company, current: j.current, questions: [] };
      if (g.jobs.length === 1) e.questions.push({ kind: g.kind, type: g.type, question: j.question, unrecognized: j.unrecognized_eligibility });
      jobs.set(j.job_id, e);
    }
  }
  return { shared, jobs: [...jobs.values()].sort((a, b) => a.questions.length - b.questions.length || a.job_id - b.job_id) };
}
