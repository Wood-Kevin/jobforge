// "Needs your input" in jobs, not question groups (usability pass 2026-10-24): questions several jobs
// share, then each job with only its own questions, fewest first. Lines that are JobForge's own reading
// problem (your confirmed fact was already checked; no answer settles them) are listed apart as
// `unsettled` and never count as needing your input (Kevin, 2026-10-24).
import type { ReadingProblem, ReviewGroup } from "./api";

export type Q = { kind: string; type: string | null; question: string; requirement_id?: number; unrecognized?: string | null };
export type JobQuestions = { job_id: number; title: string; company: string; current: boolean; questions: Q[] };
export type Unsettled = { job_id: number; title: string; company: string; lines: (ReadingProblem & { type: string | null })[] };

export function byJob(all: ReviewGroup[]) {
  const unsettled = new Map<number, Unsettled>();
  for (const g of all) for (const j of g.jobs) if (j.reading_problem) {
    const u = unsettled.get(j.job_id) ?? { job_id: j.job_id, title: j.title, company: j.company, lines: [] };
    u.lines.push({ ...j.reading_problem, type: g.type });
    unsettled.set(j.job_id, u);
  }
  const groups = all.map((g) => ({ ...g, jobs: g.jobs.filter((j) => !j.reading_problem) })).filter((g) => g.jobs.length > 0);
  const shared = groups.filter((g) => g.jobs.length > 1);
  const jobs = new Map<number, JobQuestions>();
  for (const g of groups) {
    for (const j of g.jobs) {
      const e = jobs.get(j.job_id) ?? { job_id: j.job_id, title: j.title, company: j.company, current: j.current, questions: [] };
      if (g.jobs.length === 1) e.questions.push({ kind: g.kind, type: g.type, question: j.question, requirement_id: j.requirement_id,
        unrecognized: j.unrecognized_eligibility });
      jobs.set(j.job_id, e);
    }
  }
  return { shared, jobs: [...jobs.values()].sort((a, b) => a.questions.length - b.questions.length || a.job_id - b.job_id),
    unsettled: [...unsettled.values()].sort((a, b) => a.job_id - b.job_id) };
}
