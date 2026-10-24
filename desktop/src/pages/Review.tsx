// REVIEW questions (ARCHITECTURE 6.5 noise control): one answer (a new fact or item in Profile) resolves
// every job it covers on the next recompute. Counted in jobs, not questions: 45 question groups were
// 8 jobs (usability pass 2026-10-24), and only one group covered more than one job. So questions that
// several jobs share come first, then each job with its own questions, fewest open first (the jobs
// closest to an answer).
import { api } from "../api";
import { byJob } from "../reviewGroups";
import { Badge, Card, ErrorBox, useLive } from "../ui";

function badge(g: { kind: string; type: string | null }) {
  return <Badge value={g.kind === "eligibility" ? g.type ?? "eligibility" : g.kind === "experience" ? "skill" : "conflict"} />;
}

export default function Review({ onOpen, onProfile }: { onOpen: (id: number) => void; onProfile: () => void }) {
  const groups = useLive(api.reviewQuestions, [], ["recommendation_computed", "profile_changed", "golden_labeled"]);
  const { shared, jobs } = byJob(groups.data ?? []);
  const link = (id: number, title: string) => <a href="#" onClick={(e) => { e.preventDefault(); onOpen(id); }}>{title}</a>;
  return (
    <div className="page">
      <Card title={`${jobs.length} job${jobs.length === 1 ? "" : "s"} need${jobs.length === 1 ? "s" : ""} your input`}
        actions={<button onClick={onProfile}>Open your profile</button>}>
        <p className="muted">These jobs can't be judged until you answer something. Answer in your profile (add the experience
          in your own words, or add and confirm a fact) and the jobs it covers update.</p>
        <ErrorBox error={groups.error} />
        {jobs.length === 0 && !groups.loading && <p className="muted">Nothing needs your input right now.</p>}
        {shared.length > 0 && (<>
          <h3>One answer covers several jobs</h3>
          {shared.map((g, i) => (
            <details key={i} className="group" open>
              <summary><strong>{g.jobs.length} jobs</strong> {badge(g)} {g.example}</summary>
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
            <ul className="compact">{j.questions.map((q, k) => <li key={k}>{badge(q)} {q.question}</li>)}</ul>
          </details>))}
      </Card>
    </div>
  );
}
