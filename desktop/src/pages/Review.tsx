// REVIEW questions (ARCHITECTURE 6.5 noise control): one answer (a new fact or item in Profile) resolves
// every job it covers on the next recompute. Counted in jobs, not questions: 45 question groups were
// 8 jobs (usability pass 2026-10-24), and only one group covered more than one job. So questions that
// several jobs share come first, then each job with its own questions, fewest open first (the jobs
// closest to an answer). Each question is answered right here (Answer.tsx), not by going to Profile.
import type { ReactNode } from "react";
import { api } from "../api";
import { plain } from "../labels";
import { byJob, type Unsettled } from "../reviewGroups";
import { Badge, Card, ErrorBox, useAction, useLive } from "../ui";
import { Answer } from "./Answer";

function badge(g: { kind: string; type: string | null }) {
  return <Badge value={g.kind === "eligibility" ? g.type ?? "eligibility" : g.kind === "experience" ? "skill" : "conflict"} />;
}

export default function Review({ onOpen, onProfile }: { onOpen: (id: number) => void; onProfile: () => void }) {
  const groups = useLive(api.reviewQuestions, [], ["recommendation_computed", "profile_changed", "golden_labeled"]);
  const { shared, jobs, unsettled } = byJob(groups.data ?? []);
  const schema = useLive(api.schema, [], []);
  const items = useLive(api.items, [], ["profile_changed"]);
  const answer = (q: { kind: string; type: string | null; question: string; unrecognized?: string | null }) => (
    <Answer q={{ kind: q.kind, type: q.type ?? undefined, question: q.question, unrecognized: q.unrecognized }} schema={schema.data ?? null} items={items.data ?? []} />);
  const link = (id: number, title: string) => <a href="#" onClick={(e) => { e.preventDefault(); onOpen(id); }}>{title}</a>;
  return (
    <div className="page">
      <Card title={groups.data === null ? "Needs your input" : `${jobs.length} job${jobs.length === 1 ? "" : "s"} need${jobs.length === 1 ? "s" : ""} your input`}
        actions={<button onClick={onProfile}>Open your profile</button>}>
        <p className="muted">These jobs can't be judged until you answer something. Use "Answer here" to add the experience in
          your own words or declare a fact; once it's saved (and confirmed), the jobs it covers update.</p>
        <ErrorBox error={groups.error ?? schema.error ?? items.error} />
        {groups.data === null && !groups.error && <p className="muted">Loading questions...</p>}
        {groups.data !== null && jobs.length === 0 && <p className="muted">Nothing needs your input right now.</p>}
        {shared.length > 0 && (<>
          <h3>One answer covers several jobs</h3>
          {shared.map((g, i) => (
            <details key={i} className="group" open>
              <summary><strong>{g.jobs.length} jobs</strong> {badge(g)} {g.example}</summary>
              {answer({ kind: g.kind, type: g.type, question: g.example, unrecognized: g.jobs[0]?.unrecognized_eligibility })}
              <ul>{g.jobs.map((j) => <li key={j.job_id}>{link(j.job_id, j.title)}<span className="muted"> · {j.company}</span>
                <div className="muted">{j.question}</div></li>)}</ul>
            </details>))}
        </>)}
        {jobs.length > 0 && <h3>{shared.length > 0 ? "Each job's own questions" : "By job"}</h3>}
        {jobs.map((j, i) => (
          <details key={j.job_id} className="group" open={i < 3}>
            <summary>{link(j.job_id, j.title)}<span className="muted"> · {j.company}{!j.current && " · updating"} · </span>
              <strong>{j.questions.length === 0 ? "only the shared questions above" : `${j.questions.length} open question${j.questions.length === 1 ? "" : "s"}`}</strong>
              {j.questions.length >= 6 && <span className="muted"> (many unknowns: maybe not worth completing)</span>}</summary>
            <ul className="compact">{j.questions.map((q, k) => <li key={k}>{badge(q)} {q.question} {answer(q)}</li>)}</ul>
          </details>))}
      </Card>
      {unsettled.length > 0 && <UnsettledCard jobs={unsettled} link={link} />}
    </div>
  );
}

// Lines your confirmed facts were already checked against that still can't decide the job: the AI and
// JobForge read them differently, or only JobForge's scan found them, and that never decides a job either
// way (ARCHITECTURE 6.4). Nothing to add, so no "Answer here": the call is yours (Kevin, 2026-10-24).
const OUTCOME: Record<string, string> = {
  fails: "your confirmed fact doesn't meet it", met: "your confirmed fact meets it",
  unanswered: "JobForge can't compare it with your confirmed fact (it can't place this wording)",
};

function UnsettledCard({ jobs, link }: { jobs: Unsettled[]; link: (id: number, title: string) => ReactNode }) {
  return (
    <Card title={`JobForge couldn't settle these (${jobs.length} job${jobs.length === 1 ? "" : "s"})`}>
      <p className="muted">Your confirmed facts were already checked against these lines, but JobForge and the AI read each one
        differently (or only JobForge found it), so it doesn't decide the job either way. There's nothing to add: decide
        yourself, or leave the job as it is.</p>
      <ul className="items">{jobs.map((j) => <UnsettledJob key={j.job_id} j={j} link={link} />)}</ul>
    </Card>);
}

function UnsettledJob({ j, link }: { j: Unsettled; link: (id: number, title: string) => ReactNode }) {
  const act = useAction();
  return (
    <li>{link(j.job_id, j.title)}<span className="muted"> · {j.company}</span>
      <ul className="compact">{j.lines.map((l, k) => (
        <li key={k}><Badge value={l.type ?? "eligibility"} /> "{l.quote}": {OUTCOME[l.outcome] ?? plain(l.outcome)}.</li>))}</ul>
      <button className="small" disabled={act.busy} title="Hides the job and counts as your call on it"
        onClick={() => act.run(() => api.setGoldenLabel(j.job_id, { recommendation: "SKIP", keep_details: true }))}>I'd skip it</button>
      <ErrorBox error={act.error} />
    </li>);
}
