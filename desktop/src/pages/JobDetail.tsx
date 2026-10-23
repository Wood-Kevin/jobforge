// One job (P1.8): the recommendation with its three assessments and decisive reasons, the evidence
// table (every requirement, both readings' outcome, the match, cited profile items as the
// evaluation saw them), the application, and the posting itself, sanitized.
import { useMemo, useState, type MouseEvent } from "react";
import { openUrl } from "../agent";
import { api, type EvidenceRow, type JobDetail as Detail } from "../api";
import { sanitizePosting } from "../sanitize";
import { Badge, Card, ErrorBox, ago, useAction, useLive } from "../ui";

const STATUSES = ["saved", "applied", "interviewing", "offer", "rejected", "withdrawn"];

export default function JobDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const job = useLive(() => api.job(id), [id], [], 0);
  const d = job.data;
  return (
    <div className="page">
      <p><button onClick={onBack}>&larr; Back</button></p>
      <ErrorBox error={job.error} />
      {d && <DetailBody d={d} reload={job.reload} />}
    </div>
  );
}

function DetailBody({ d, reload }: { d: Detail; reload: () => void }) {
  const act = useAction();
  const html = useMemo(() => sanitizePosting(d.description_html ?? d.description_text), [d.description_html, d.description_text]);
  const ev = d.evaluation;
  const rec = ev?.recommendation;
  const link = d.apply_url || d.listing_url;
  const onPostingClick = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest("a");
    if (!a) return;
    e.preventDefault(); // never navigate the webview; the host opens http(s) links in the browser
    const href = a.getAttribute("href");
    if (href) act.run(() => openUrl(href));
  };

  return (
    <>
      <Card title={d.title} actions={link && <button onClick={() => act.run(() => openUrl(link))}>Open posting</button>}>
        <p className="muted">{d.company} · {d.location ?? "location not stated"}{d.work_mode ? ` · ${d.work_mode}` : ""}
          {d.salary && ` · ${fmtSalary(d.salary)}`} · first seen {ago(d.first_seen_at_ms)} ago
          {d.posting_versions > 1 && ` · ${d.posting_versions} versions`}{d.closed_at_ms && " · CLOSED"}</p>
        <Application d={d} reload={reload} />
        {d.filter?.verdict === "reject" && (
          <p className="warn">Rejected by your filter: {d.filter.reasons.primary}
            {!d.override && <> <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.evaluateAnyway(d.id, "from the job view"); reload(); })}>Evaluate anyway</button></>}
            {d.override && " (evaluate anyway requested)"}</p>)}
        <ErrorBox error={act.error} />
      </Card>

      {rec ? (
        <Card title={<>Recommendation <Badge value={rec.recommendation} /></>} actions={!ev!.current &&
          <span className="warn">From an earlier profile version; re-evaluation {ev!.newer_evaluation_pending ? "pending" : "not queued"}.</span>}>
          <div className="three">
            <div><h3>Eligibility</h3><Badge value={rec.eligibility} /></div>
            <div><h3>Fit</h3><Badge value={rec.fit} /><p className="muted">{rec.fit_counts.rule}</p></div>
            <div><h3>Opportunity</h3><Badge value={rec.opportunity} />
              <ul className="compact">{rec.opportunity_factors.map((f) => <li key={f.factor}><Badge value={f.value} /> {f.factor} <span className="muted">{f.detail}</span></li>)}</ul></div>
          </div>
          {rec.decisive_reasons.map((r, i) => (
            <div key={i} className="reason"><strong>Why:</strong> {r.detail}
              {r.questions && r.questions.length > 0 && <ul>{r.questions.map((q, k) => <li key={k}>{q.question}</li>)}</ul>}</div>))}
          {rec.eligibility_reasons.length > 0 && (
            <>
              <h3>Eligibility checks</h3>
              <table><thead><tr><th>Requirement</th><th>Type</th><th>Outcome</th><th>Basis</th><th>Effect</th></tr></thead>
                <tbody>{rec.eligibility_reasons.map((x) => (
                  <tr key={x.requirement_id}><td>{x.quote}<div className="muted">{x.why}</div></td><td>{x.type}</td>
                    <td>{x.outcome}</td><td><Badge value={x.basis} /></td><td>{x.effect}</td></tr>))}</tbody></table>
            </>)}
          <p className="muted">Rules {rec.verdict_rule_version} · model {ev!.evaluation.model_id_reported ?? "not called"} · {ev!.evaluation.match_prompt_version} · profile snapshot {ev!.evaluation.profile_snapshot_id}</p>
        </Card>
      ) : (
        <Card title="Recommendation"><p className="muted">
          {d.analysis ? `Posting analysis: ${d.analysis.status}${d.analysis.last_error ? ` (${d.analysis.last_error})` : ""}.` : "Not analyzed (filter reject or not queued)."}
          {d.analysis?.status === "done" && " Evaluation pending."}</p></Card>
      )}

      {ev && <Evidence rows={ev.evidence} cited={ev.cited_items} />}

      <Card title="Posting">
        <div className="posting" onClick={onPostingClick} dangerouslySetInnerHTML={{ __html: html }} />
      </Card>
    </>
  );
}

function Evidence({ rows, cited }: { rows: EvidenceRow[]; cited: Record<string, { kind: string; statement: string; status: string }> }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.filter((r) => r.class !== "context" && r.class !== "negated");
  return (
    <Card title="Evidence" actions={<label className="muted"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> include context and negated lines</label>}>
      <table className="evidence">
        <thead><tr><th>Requirement (posting quote)</th><th>Class</th><th>Reading</th><th>Match</th><th>Evidence from your profile</th></tr></thead>
        <tbody>{shown.map((r) => {
          const m = r.match;
          const flags = m?.flags.map((f) => f.flag).filter((f) => f !== "eligibility_check") ?? [];
          return (
            <tr key={r.requirement_id}>
              <td>{r.quote}{r.eligibility_type && <div className="muted">eligibility: {r.eligibility_type}</div>}</td>
              <td><Badge value={r.class} /></td>
              <td><Badge value={r.status} />{r.origin !== "model" && <div className="muted">found by the recall scan</div>}</td>
              <td>{m ? <><Badge value={m.match_type} /> {m.match_type === "gap" && <Badge value={m.gap_materiality} />}
                <div className="muted">{m.matched_by === "eligibility_fact" ? "checked against your facts" : "model, checked by code"}</div></> : <span className="muted">not matched</span>}</td>
              <td>
                {m?.citations.map((c) => cited[String(c)] && (
                  <div key={c}><Badge value={cited[String(c)].status} /> {cited[String(c)].statement}</div>))}
                {m?.bridge && <div><em>Transfers:</em> {m.bridge}</div>}
                {m?.transfer_limits && <div className="muted"><em>Limits:</em> {m.transfer_limits}</div>}
                {m?.gap_size && <div className="muted">{fmtGap(m.gap_size)}</div>}
                {flags.length > 0 && <div className="muted">{flags.join(", ")}</div>}
              </td>
            </tr>);
        })}</tbody>
      </table>
    </Card>
  );
}

function Application({ d, reload }: { d: Detail; reload: () => void }) {
  const act = useAction();
  const a = d.application;
  const [notes, setNotes] = useState(a?.notes ?? "");
  return (
    <div className="application">
      <label>Application:{" "}
        <select value={a?.status ?? ""} disabled={act.busy}
          onChange={(e) => e.target.value && act.run(async () => { await api.setApplication(d.id, { status: e.target.value }); reload(); })}>
          <option value="" disabled>not tracked</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select></label>
      {a && (
        <>
          <input className="notes" placeholder="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          <button className="small" disabled={act.busy || notes === (a.notes ?? "")} onClick={() => act.run(async () => { await api.setApplication(d.id, { notes }); reload(); })}>Save notes</button>
          <span className="muted"> {a.events.map((e) => `${e.to_status} ${ago(e.at_ms)} ago`).join(" ← ")}</span>
        </>)}
      <ErrorBox error={act.error} />
    </div>
  );
}

function fmtSalary(s: { min: number | null; max: number | null; period: string }): string {
  const f = (n: number | null) => (n === null ? "?" : n >= 1000 ? `$${Math.round(n / 1000)}k` : `$${n}`);
  return `${f(s.min)}-${f(s.max)} / ${s.period.toLowerCase()}`;
}

function fmtGap(g: Record<string, unknown>): string {
  if ("years_needed" in g) return `${g.years_shown ?? 0} of ${g.years_needed} years shown`;
  if ("eligibility" in g) return "fails on your declared fact";
  return typeof g.detail === "string" ? g.detail : "";
}
