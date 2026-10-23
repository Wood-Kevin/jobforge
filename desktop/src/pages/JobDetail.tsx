// One job (P1.8b: "too much on screen"). A short summary first: the recommendation in a sentence,
// the three assessments in plain words, what you have, what's missing, and any question for you.
// The full requirement-by-requirement evidence, factor details, technical versions and the posting
// itself are one click away. Nothing is hidden for good: every row the agent computed is reachable.
import { useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { openUrl } from "../agent";
import { api, type Category, type EvidenceRow, type JobDetail as Detail } from "../api";
import { ELIGIBILITY_TEXT, FIT_TEXT, OPPORTUNITY_TEXT, matchNote, plain } from "../labels";
import { sanitizePosting } from "../sanitize";
import { Badge, Card, ConfirmButton, ErrorBox, ago, useAction, useLive } from "../ui";

const STATUSES = ["saved", "applied", "interviewing", "offer", "rejected", "withdrawn"];

export default function JobDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const job = useLive(() => api.job(id), [id], ["recommendation_computed", "application_changed", "analysis_done"], 0);
  return (
    <div className="page">
      <p><button onClick={onBack}>&larr; Back</button></p>
      <ErrorBox error={job.error} />
      {job.data && <DetailBody d={job.data} reload={job.reload} />}
    </div>
  );
}

function Section({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  return <details className="card section" open={open}><summary><h2>{title}</h2></summary>{children}</details>;
}

function DetailBody({ d, reload }: { d: Detail; reload: () => void }) {
  const act = useAction();
  const ev = d.evaluation;
  const rec = ev?.recommendation;
  const link = d.apply_url || d.listing_url;
  const rows = ev?.evidence.filter((r) => r.class !== "context" && r.class !== "negated") ?? [];
  const have = rows.filter((r) => r.match && ["direct", "transferable"].includes(r.match.match_type) && r.class !== "preferred");
  const missing = rows.filter((r) => r.match && (r.match.match_type === "gap" || r.match.match_type === "unknown") && r.class !== "preferred");
  const questions = rec?.decisive_reasons.flatMap((r) => r.questions ?? []) ?? [];

  return (
    <>
      <Card title={d.title} actions={link && <button onClick={() => act.run(() => openUrl(link))}>Open job posting</button>}>
        <p className="muted">{d.company} · {d.location ?? "location not stated"}{d.salary && ` · ${fmtSalary(d.salary)}`}
          {` · found ${ago(d.first_seen_at_ms)} ago`}{d.closed_at_ms && " · no longer listed"}</p>
        <Application d={d} reload={reload} />
        {d.filter?.verdict === "reject" && (
          <p className="warn">This job doesn't match your search ({plainFilterReason(d.filter.reasons.primary)}).
            {!d.override && <> <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.evaluateAnyway(d.id, "from the job view"); reload(); })}>Assess it anyway</button></>}
            {d.override && " Assessment requested."}</p>)}
        <ErrorBox error={act.error} />
      </Card>

      {rec ? (
        <Card title={<>Recommendation: <Badge value={rec.recommendation} /></>}>
          {!ev!.current && <p className="warn">Based on your profile before your latest edit; an update is {ev!.newer_evaluation_pending ? "on its way" : "not queued yet"}.</p>}
          <p className="lead">{rec.decisive_reasons[0] ? plainReason(rec.recommendation, rec.decisive_reasons[0].detail) : ""}</p>
          <div className="summary">
            <div><Badge value={rec.eligibility} /> <span>{ELIGIBILITY_TEXT[rec.eligibility]}</span></div>
            <div><Badge value={rec.fit} /> <span>{FIT_TEXT[rec.fit]}</span></div>
            <div><Badge value={rec.opportunity} /> <span>{OPPORTUNITY_TEXT[rec.opportunity]}</span></div>
          </div>
          {questions.length > 0 && (
            <div className="reason"><strong>JobForge needs your answer:</strong>
              <ul>{questions.slice(0, 6).map((q, k) => <li key={k}>{q.question}</li>)}</ul>
              {questions.length > 6 && <p className="muted">and {questions.length - 6} more in "Needs your input".</p>}
            </div>)}
          <div className="two">
            <div><h3>What you have ({have.length})</h3>
              <ul className="compact">{have.slice(0, 5).map((r) => <li key={r.requirement_id}>{short(r.quote)} {r.match!.match_type === "transferable" && <span className="muted">(related experience)</span>}</li>)}</ul>
              {have.length > 5 && <p className="muted">+{have.length - 5} more below</p>}</div>
            <div><h3>Missing or not in your profile ({missing.length})</h3>
              <ul className="compact">{missing.slice(0, 5).map((r) => <li key={r.requirement_id}>{short(r.quote)}{" "}
                <span className="muted">({r.match!.match_type === "gap" ? (r.match!.gap_materiality === "minor" ? "small gap" : "missing") : "not in your profile"})</span></li>)}</ul>
              {missing.length > 5 && <p className="muted">+{missing.length - 5} more below</p>}</div>
          </div>
        </Card>
      ) : (
        <Card title="Recommendation"><p className="muted">{waiting(d)}</p></Card>
      )}

      <YourCall d={d} reload={reload} />
      {ev && <Section title={`Every requirement (${rows.length})`}><Evidence rows={ev.evidence} cited={ev.cited_items} /></Section>}
      {rec && rec.eligibility_reasons.length > 0 && (
        <Section title="Eligibility checks">
          <ul className="items">{rec.eligibility_reasons.map((x) => (
            <li key={x.requirement_id}><div className="item-line"><Badge value={x.effect === "blocks" ? "BLOCKED" : x.effect === "ok" ? "ELIGIBLE" : x.effect === "ignored" ? "neutral" : "UNKNOWN"} />
              <span className="statement">"{short(x.quote, 160)}"</span><span className="muted">{plain(x.type)}</span></div>
              <div className="muted">{x.question ?? eligibilityWhy(x)}</div></li>))}</ul>
        </Section>)}
      {rec && (
        <Section title="Pay, location and level">
          <ul className="items">{rec.opportunity_factors.map((f) => (
            <li key={f.factor}><div className="item-line"><Badge value={f.value} /><span className="statement">{plain(f.factor)}</span><span className="muted">{f.detail}</span></div></li>))}</ul>
        </Section>)}
      <Section title="Job posting"><Posting d={d} /></Section>
      {ev && (
        <Section title="Technical details">
          <p className="muted">Rules {rec?.verdict_rule_version ?? "-"} · model {ev.evaluation.model_id_reported ?? "not called"} · prompt {ev.evaluation.match_prompt_version}
            {" "}· profile version {ev.evaluation.profile_snapshot_id}{rec && ` · ${rec.fit_counts.rule}`}</p>
        </Section>)}
    </>
  );
}

function Posting({ d }: { d: Detail }) {
  const act = useAction();
  const html = useMemo(() => sanitizePosting(d.description_html ?? d.description_text), [d.description_html, d.description_text]);
  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest("a");
    if (!a) return;
    e.preventDefault(); // never navigate the window; the host opens http(s) links in the browser
    const href = a.getAttribute("href");
    if (href) act.run(() => openUrl(href));
  };
  return <><div className="posting" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} /><ErrorBox error={act.error} /></>;
}

function Evidence({ rows, cited }: { rows: EvidenceRow[]; cited: Record<string, { kind: string; statement: string; status: string }> }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.filter((r) => r.class !== "context" && r.class !== "negated");
  return (
    <>
      <label className="muted"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> also show lines about the company and things marked "not required"</label>
      <table className="evidence">
        <thead><tr><th>The posting says</th><th>Kind</th><th>You</th><th>From your profile</th></tr></thead>
        <tbody>{shown.map((r) => {
          const m = r.match;
          const note = m ? matchNote(m) : null;
          return (
            <tr key={r.requirement_id}>
              <td>{r.quote}{r.status === "conflict" && <div className="muted">The AI and JobForge's rules read this line differently.</div>}
                {r.status === "unverifiable" && <div className="muted">{r.origin === "model" ? "The AI quoted text that isn't in the posting; ignored." : "Possible eligibility condition the AI didn't list."}</div>}</td>
              <td><Badge value={r.class} /></td>
              <td>{m ? <>{<Badge value={m.match_type} />}{m.match_type === "gap" && m.gap_materiality === "minor" && <div className="muted">small gap</div>}</> : <span className="muted">-</span>}</td>
              <td>
                {m?.citations.map((c) => cited[String(c)] && <div key={c}>{cited[String(c)].statement} {cited[String(c)].status !== "verified" && <span className="muted">(not confirmed)</span>}</div>)}
                {m?.bridge && <div><em>Why it counts:</em> {m.bridge}</div>}
                {m?.transfer_limits && <div className="muted"><em>What's different:</em> {m.transfer_limits}</div>}
                {note && <div className="muted">{note}</div>}
                {m?.matched_by === "eligibility_fact" && <div className="muted">Checked against your eligibility facts.</div>}
              </td>
            </tr>);
        })}</tbody>
      </table>
    </>
  );
}

function Application({ d, reload }: { d: Detail; reload: () => void }) {
  const act = useAction();
  const a = d.application;
  const [notes, setNotes] = useState(a?.notes ?? "");
  return (
    <div className="application">
      <label>Your status:{" "}
        <select value={a?.status ?? ""} disabled={act.busy}
          onChange={(e) => e.target.value && act.run(async () => { await api.setApplication(d.id, { status: e.target.value }); reload(); })}>
          <option value="" disabled>not tracked</option>
          {STATUSES.map((s) => <option key={s} value={s}>{plain(s)}</option>)}
        </select></label>
      {a && (
        <>
          <input className="notes" placeholder="Notes (recruiter, dates...)" value={notes} onChange={(e) => setNotes(e.target.value)} />
          <button className="small" disabled={act.busy || notes === (a.notes ?? "")} onClick={() => act.run(async () => { await api.setApplication(d.id, { notes }); reload(); })}>Save notes</button>
          <span className="muted"> {a.events.map((e) => `${plain(e.to_status)} ${ago(e.at_ms)} ago`).join(" ← ")}</span>
        </>)}
      <ErrorBox error={act.error} />
    </div>
  );
}

// ----- your call (the golden set, ARCHITECTURE 6.7) -----

const CALLS: { value: Category; text: string }[] = [
  { value: "APPLY", text: "I'd apply" }, { value: "STRETCH", text: "A stretch, but worth it" },
  { value: "REVIEW", text: "Can't tell without more info" }, { value: "SKIP", text: "I'd skip it" }];

function YourCall({ d, reload }: { d: Detail; reload: () => void }) {
  const act = useAction();
  const cur = d.golden_label;
  const [editing, setEditing] = useState(false);
  const [call, setCall] = useState<Category | null>(cur?.recommendation ?? null);
  const [obvious, setObvious] = useState(cur?.obvious ?? false);
  const [elig, setElig] = useState<string>(cur?.eligibility ?? "");
  const [misread, setMisread] = useState<number[]>(cur?.misread_requirement_ids ?? []);
  const [note, setNote] = useState(cur?.note ?? "");
  const reqs = d.evaluation?.evidence.filter((r) => r.class !== "context") ?? [];
  const save = () => act.run(async () => {
    await api.setGoldenLabel(d.id, { recommendation: call!, obvious, eligibility: elig || null, misread_requirement_ids: misread, note: note || null });
    setEditing(false);
    reload();
  });
  if (cur && !editing)
    return (
      <Card title="Your call">
        <p>You said: <strong>{CALLS.find((c) => c.value === cur.recommendation)?.text}</strong>
          {cur.obvious && " (clear without more info)"}{cur.misread_requirement_ids.length > 0 && `; ${cur.misread_requirement_ids.length} requirement(s) misread`}
          <span className="muted"> · {ago(cur.created_at_ms)} ago</span></p>
        <div className="row"><button className="small" onClick={() => setEditing(true)}>Change</button>
          <ConfirmButton label="Remove" disabled={act.busy} onConfirm={() => act.run(async () => { await api.withdrawGoldenLabel(d.id); reload(); })} /></div>
        <ErrorBox error={act.error} />
      </Card>);
  return (
    <Section title="Your call (helps measure JobForge)" open={editing}>
      <p className="muted">What would you do with this job? Your answers are compared with JobForge's to see where it is wrong. They
        don't change this job's recommendation.</p>
      <div className="row">{CALLS.map((c) => (
        <label key={c.value} className="choice"><input type="radio" name={`call-${d.id}`} checked={call === c.value} onChange={() => setCall(c.value)} /> {c.text}</label>))}</div>
      <label className="choice"><input type="checkbox" checked={obvious} onChange={(e) => setObvious(e.target.checked)} /> I could tell without more information</label>
      <label>Can you work here at all? <span className="muted">(optional)</span>{" "}
        <select value={elig} onChange={(e) => setElig(e.target.value)}>
          <option value="">not answered</option><option value="ELIGIBLE">yes</option><option value="BLOCKED">no (location, clearance, ...)</option>
          <option value="UNKNOWN">not sure</option></select></label>
      {reqs.length > 0 && (
        <details><summary>Requirements JobForge read wrong ({misread.length})</summary>
          <ul className="compact">{reqs.map((r) => (
            <li key={r.requirement_id}><label className="choice"><input type="checkbox" checked={misread.includes(r.requirement_id)}
              onChange={(e) => setMisread(e.target.checked ? [...misread, r.requirement_id] : misread.filter((x) => x !== r.requirement_id))} />
              {" "}{short(r.quote, 140)} <span className="muted">({plain(r.class)})</span></label></li>))}</ul></details>)}
      <input className="notes" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="row"><button className="primary" disabled={act.busy || !call} onClick={save}>Save my call</button>
        {editing && <button onClick={() => setEditing(false)}>Cancel</button>}</div>
      <ErrorBox error={act.error} />
    </Section>);
}

function short(s: string, n = 110): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}

function plainReason(rec: string, detail: string): string {
  if (rec === "APPLY") return detail.includes("self-reported")
    ? "A good match. Some of it rests on experience you haven't confirmed yet in Profile."
    : "A good match for your experience.";
  if (rec === "STRETCH") return "Worth a try: you cover most of it, with one or two important gaps.";
  if (rec === "SKIP") {
    if (detail.startsWith("blocker:")) return `Skip: a requirement rules you out (${short(detail.slice(8).trim(), 90)}).`;
    if (detail.includes("material gaps")) return "Skip: several important requirements are missing from your experience.";
    return "Skip: a stretch, and the pay, location or level don't suit you.";
  }
  return "JobForge can't decide this one without your input.";
}

function eligibilityWhy(x: { effect: string; outcome: string; basis: string; why: string }): string {
  if (x.effect === "ok") return "You meet this.";
  if (x.effect === "blocks") return "A fact you confirmed doesn't meet this.";
  if (x.effect === "ignored") return "The posting says this isn't required.";
  if (x.basis === "self_reported") return "Confirm your fact in Profile to settle this.";
  return "Your profile doesn't answer this yet.";
}

function plainFilterReason(primary: string | null): string {
  const r = (primary ?? "").split(":")[0];
  const text: Record<string, string> = {
    TITLE_NO_MATCH: "the title isn't one you're looking for", TITLE_SENIORITY: "the level is higher than you want",
    TITLE_NON_IT: "it's in a field you skip", LOCATION_NOT_ELIGIBLE: "it's on-site somewhere you can't commute to",
    REMOTE_EXCLUDES_HOME: "it's remote but not open where you live", REMOTE_ANCHORED: "it's tied to an office elsewhere",
    STALE_POSTING: "the posting is too old", SALARY_BELOW_FLOOR: "the pay is below your minimum",
  };
  return text[r] ?? "it doesn't match your search";
}

function waiting(d: Detail): string {
  if (!d.analysis) return d.filter?.verdict === "reject" ? "Not assessed because it doesn't match your search." : "Waiting to be read.";
  if (d.analysis.status === "pending" || d.analysis.status === "running") return "Waiting for the AI model to read this posting.";
  if (d.analysis.status === "failed") return "The AI model couldn't read this posting.";
  return "Read; matching it against your experience next.";
}

function fmtSalary(s: { min: number | null; max: number | null; period: string }): string {
  const f = (n: number | null) => (n === null ? "?" : n >= 1000 ? `$${Math.round(n / 1000)}k` : `$${n}`);
  return `${f(s.min)}-${f(s.max)} / ${s.period.toLowerCase()}`;
}
