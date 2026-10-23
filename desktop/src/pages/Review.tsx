// REVIEW as grouped questions, not a job list (ARCHITECTURE 6.5 noise control): one answer (a new
// fact or item in Profile) resolves every job in its group on the next recompute.
import { api } from "../api";
import { Badge, Card, ErrorBox, useLive } from "../ui";

export default function Review({ onOpen, onProfile }: { onOpen: (id: number) => void; onProfile: () => void }) {
  const groups = useLive(api.reviewQuestions, [], ["recommendation_computed", "profile_changed"]);
  const g = groups.data ?? [];
  return (
    <div className="page">
      <Card title={`REVIEW questions (${g.length})`} actions={<button onClick={onProfile}>Open profile</button>}>
        <p className="muted">Each group is one question across several jobs. Answer it in your profile (an item, a fact, or a
          verify) and the affected jobs are re-assessed; facts and verifies cost no model calls.</p>
        <ErrorBox error={groups.error} />
        {g.length === 0 && !groups.loading && <p className="muted">No open questions.</p>}
        {g.map((grp, i) => (
          <details key={i} className="group" open={i < 3}>
            <summary><strong>{grp.jobs.length} job{grp.jobs.length === 1 ? "" : "s"}</strong>{" "}
              <Badge value={grp.kind === "eligibility" ? grp.type ?? "eligibility" : grp.kind} /> {grp.example}</summary>
            <ul>{grp.jobs.map((j, k) => (
              <li key={k}><a href="#" onClick={(e) => { e.preventDefault(); onOpen(j.job_id); }}>{j.title}</a>
                <span className="muted"> · {j.company}{!j.current && " · updating"}</span>
                {grp.jobs.length > 1 && <div className="muted">{j.question}</div>}</li>))}</ul>
          </details>))}
      </Card>
    </div>
  );
}
