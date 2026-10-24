// One job (P1.8b: "too much on screen"). A short summary first: the recommendation in a sentence,
// the three assessments in plain words, what you have, what's missing, and any question for you.
// The full requirement-by-requirement evidence, factor details, technical versions and the posting
// itself are one click away. Nothing is hidden for good: every row the agent computed is reachable.
import { useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { openUrl } from "../agent";
import { api, type Category, type EvidenceRow, type JobDetail as Detail } from "../api";
import { ELIGIBILITY_TEXT, FIT_TEXT, OPPORTUNITY_SHORT, OPPORTUNITY_TEXT, VERDICT_TITLE, evidenceSentence, factorText, fitSegments, fmtSalary, matchNote, plain } from "../labels";
import { sanitizePosting } from "../sanitize";
import { Answer } from "./Answer";
import { Badge, Card, ConfirmButton, ErrorBox, FitBar, RecBadge, since, tone, useAction, useLive } from "../ui";
import { icons } from "../icons";

const STATUSES = ["saved", "applied", "interviewing", "offer", "rejected", "withdrawn"];

export default function JobDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const job = useLive(() => api.job(id), [id], ["recommendation_computed", "application_changed", "analysis_done", "profile_changed"], 0);
  return (
    <div className="page">
      <button className="back" onClick={onBack}>{icons.back}Back</button>
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
  // "JobForge needs your answer" is answered in place, as on Needs your input (Answer.tsx).
  const schema = useLive(api.schema, [], []);
  const items = useLive(api.items, [], ["profile_changed"]);
  const ev = d.evaluation;
  const rec = ev?.recommendation;
  // The post itself; Lever's and Ashby's apply_url is the application form (.../apply, .../application).
  const link = d.listing_url || d.apply_url;
  const rows = ev?.evidence.filter((r) => r.class !== "context" && r.class !== "negated") ?? [];
  // Required experience only, as the fit counts and the headline count it: lines settled by your eligibility
  // facts (where you live, hours) are the "Location, hours and other checks" section, not experience
  // (UX redesign step 2: Nash's "What you have (9)" listed four location lines beside five skills).
  const required = rows.filter((r) => r.match && r.class !== "preferred" && r.match.matched_by !== "eligibility_fact");
  const have = required.filter((r) => ["direct", "transferable"].includes(r.match!.match_type));
  const missing = required.filter((r) => r.match!.match_type === "gap" || r.match!.match_type === "unknown");
  const nice = rows.filter((r) => r.match && r.class === "preferred");
  const questions = rec?.decisive_reasons.flatMap((r) => r.questions ?? []) ?? [];
  // The most consequential open point, shown in the verdict: an important gap, else a small gap,
  // else an unanswered requirement (required items only).
  const open = missing.find((r) => r.match!.match_type === "gap" && r.match!.gap_materiality === "material")
    ?? missing.find((r) => r.match!.match_type === "gap") ?? missing.find((r) => r.match!.match_type === "unknown");
  const good = rec !== null && rec !== undefined && ["APPLY", "STRETCH"].includes(rec.recommendation);
  const openNote = open?.match ? matchNote(open.match) : null;

  return (
    <>
      {/* One header (UX redesign step 2): what the job is, the one clear action (Open job posting), "I'd skip it"
          quieter (Kevin, v0.3.5: one stated action), and your progress in place of Save job plus a status menu. */}
      <section className="card job-head">
        <h1>{d.title}</h1>
        <div className="meta">
          <strong>{d.company}</strong><span>{d.location ?? "Location not stated"}</span>
          {d.salary && <strong>{fmtSalary(d.salary)}</strong>}
          <span>Found {since(d.first_seen_at_ms)}</span>
          {d.closed_at_ms && <span className="warn">No longer listed</span>}
        </div>
        <div className="row">
          {link && <button className="primary big" onClick={() => act.run(() => openUrl(link))}>Open job posting{icons.external}</button>}
          <span className="spacer" />
          {d.golden_label?.recommendation === "SKIP"
            ? <><span className="muted">Skipped by you</span><button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.withdrawGoldenLabel(d.id); reload(); })}>Undo</button></>
            : <button className="quiet" disabled={act.busy} title="Hides the job and counts as your call on it"
                onClick={() => act.run(async () => { await api.setGoldenLabel(d.id, { recommendation: "SKIP", keep_details: true }); reload(); })}>{icons.hide}I'd skip it</button>}
        </div>
        {d.copies.length > 0 && (
          <p className="muted">The company also posted this exact job {d.copies.length === 1 ? "once more" : `${d.copies.length} more times`}:{" "}
            {d.copies.map((c, i) => c.url && <button key={c.id} className="small" onClick={() => act.run(() => openUrl(c.url!))}>Other listing {i + 1}</button>)}</p>)}
        {d.filter?.verdict === "reject" && (
          <p className="warn">This job doesn't match your search ({plainFilterReason(d.filter.reasons.primary)}).
            {!d.override && <> <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.evaluateAnyway(d.id, "from the job view"); reload(); })}>Assess it anyway</button></>}
            {d.override && (d.description_html === null && d.description_text === null
              ? " Assessment requested. Its description wasn't kept, so JobForge fetches it again first."
              : " Assessment requested.")}</p>)}
        <ErrorBox error={act.error} />
        <Progress d={d} reload={reload} />
      </section>

      {rec ? (
        <section className="card verdict">
          <div className="verdict-head"><RecBadge value={rec.recommendation} large /><h2>{VERDICT_TITLE[rec.recommendation]}</h2></div>
          {!ev!.current && <p className="warn">Based on your profile before your latest edit; an update is {ev!.newer_evaluation_pending ? "on its way" : "not queued yet"}.</p>}
          <p className="lead">{good ? leadSentence(rec.fit_counts)
            : rec.decisive_reasons[0] ? plainReason(rec.recommendation, rec.decisive_reasons[0].detail) : ""}</p>
          {good && <><FitBar counts={rec.fit_counts} large />
            <div className="legend">{fitSegments(rec.fit_counts).map((s) => <span key={s.key}><span className={`swatch seg ${s.key}`} />{s.text}</span>)}</div></>}
          {open && good && (
            <div className={open.match!.gap_materiality === "material" && open.match!.match_type === "gap" ? "callout warn" : "callout"}>
              <div className="callout-label">{open.match!.match_type === "gap" ? (open.match!.gap_materiality === "material" ? "The biggest gap" : "The one thing to know: a small gap") : "The one thing to know: unanswered"}</div>
              <div>{short(open.quote, 220)}</div>
              {openNote && <div className="muted">{openNote}</div>}
            </div>)}
          {questions.length > 0 && (
            <div className="reason"><strong>JobForge needs your answer:</strong>
              <ul>{questions.slice(0, 6).map((q, k) => (
                <li key={k}>{q.question} <Answer q={{ ...q, unrecognized: ev!.evidence.find((r) => r.requirement_id === q.requirement_id)?.unrecognized_eligibility }}
                  schema={schema.data ?? null} items={items.data ?? []} /></li>))}</ul>
              {questions.length > 6 && <p className="muted">and {questions.length - 6} more in "Needs your input".</p>}
              <ErrorBox error={schema.error ?? items.error} />
            </div>)}
          <div className="tiles">
            <Tile value={rec.eligibility} title={plain(rec.eligibility)} text={ELIGIBILITY_TEXT[rec.eligibility]} />
            <Tile value={rec.fit} title={plain(rec.fit)} text={FIT_TEXT[rec.fit]} />
            <Tile value={rec.opportunity} title={OPPORTUNITY_SHORT[rec.opportunity] ?? plain(rec.opportunity)} text={OPPORTUNITY_TEXT[rec.opportunity]} />
          </div>
        </section>
      ) : (
        <Card title="Recommendation"><p className="muted">{waiting(d)}</p></Card>
      )}

      {rec && required.length + nice.length > 0 && (
        <section className="card two reqs">
          <div>
            <h2>What you bring <span className="muted">{have.length} of {required.length} required</span></h2>
            {have.length === 0 ? <p className="muted">Nothing yet from your profile.</p>
              : <ul className="checks">{have.slice(0, 8).map((r) => (
                <li key={r.requirement_id}><span className={r.match!.match_type === "direct" ? "mark ok" : "mark related"}>{icons.check}</span>
                  <span>{short(r.quote, 140)}{r.match!.match_type === "transferable" && <span className="muted"> · related experience</span>}</span></li>))}</ul>}
            {have.length > 8 && <p className="muted">and {have.length - 8} more under "Every requirement".</p>}
          </div>
          <div>
            <h2>Still open <span className="muted">{missing.length}</span></h2>
            {missing.length === 0 ? <p className="muted">Nothing: every required line is covered.</p>
              : <ul className="checks">{missing.slice(0, 6).map((r) => (
                <li key={r.requirement_id}><span className={r.match!.match_type === "gap" && r.match!.gap_materiality === "material" ? "mark bad" : "mark open"}>–</span>
                  <span>{short(r.quote, 140)} <span className="muted">· {r.match!.match_type === "gap" ? (r.match!.gap_materiality === "minor" ? "small gap" : "missing") : "not in your profile"}</span></span></li>))}</ul>}
            {missing.length > 6 && <p className="muted">and {missing.length - 6} more under "Every requirement".</p>}
            {nice.length > 0 && <>
              <h2 className="spaced">Nice to have <span className="muted">{nice.filter((r) => ["direct", "transferable"].includes(r.match!.match_type)).length} of {nice.length}</span></h2>
              <ul className="checks quiet">{nice.slice(0, 6).map((r) => {
                const ok = ["direct", "transferable"].includes(r.match!.match_type);
                return <li key={r.requirement_id}><span className={ok ? "mark ok" : "mark none"}>{ok ? icons.check : "○"}</span>
                  <span>{short(r.quote, 120)}{!ok && <span className="muted"> · not in your profile</span>}</span></li>;
              })}</ul></>}
          </div>
        </section>)}

      {ev && <Section title={`Every requirement, line by line (${rows.length})`}><Evidence rows={ev.evidence} cited={ev.cited_items} /></Section>}
      {rec && rec.eligibility_reasons.length > 0 && (
        <Section title={`Location, hours and other checks (${rec.eligibility_reasons.length})`}>
          <ul className="items">{rec.eligibility_reasons.map((x) => (
            <li key={x.requirement_id}><div className="item-line"><Badge value={x.effect === "blocks" ? "BLOCKED" : x.effect === "ok" ? "ELIGIBLE" : x.effect === "ignored" ? "neutral" : "UNKNOWN"} />
              <span className="statement">"{short(x.quote, 160)}"</span><span className="muted">{plain(x.type)}</span></div>
              <div className="muted">{x.question ?? eligibilityWhy(x)}</div></li>))}</ul>
        </Section>)}
      {rec && (
        <Section title="Pay, location and level">
          <ul className="items">{rec.opportunity_factors.map((f) => {
            const t = factorText(f);
            return <li key={f.factor}><div className="item-line"><Badge value={f.value} text={t.label} /><span className="statement">{plain(f.factor)}</span>
              <span className="muted">{t.note}</span></div></li>;
          })}</ul>
        </Section>)}
      <Section title="The full job posting"><Posting d={d} /></Section>
      <YourCall d={d} reload={reload} />
      {ev && (
        <Section title="Technical details">
          <p className="muted">Rules {rec?.verdict_rule_version ?? "-"} · model {ev.evaluation.model_id_reported ?? "not called"} · prompt {ev.evaluation.match_prompt_version}
            {" "}· profile version {ev.evaluation.profile_snapshot_id}{rec && ` · ${rec.fit_counts.rule}`}</p>
        </Section>)}
    </>
  );
}

// One of the three assessments, with a mark that matches its badge tone.
function Tile({ value, title, text }: { value: string; title: string; text: string | undefined }) {
  const t = tone(value);
  return (
    <div className="tile">
      <div className="tile-title"><span className={`mark ${t}`}>{t === "ok" ? icons.check : t === "bad" ? icons.cross : "!"}</span>{title}</div>
      <div className="muted">{text}</div>
    </div>
  );
}

function leadSentence(c: Record<string, unknown>): string {
  const unconfirmed = Number(c["self_reported_covering"] ?? 0) > 0 ? " Some of it rests on experience you haven't confirmed in your profile." : "";
  return `${evidenceSentence(c)}${unconfirmed}`;
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
  if (d.description_html === null && d.description_text === null) {
    // Postings that don't match the search keep no description (storage); the link still works.
    return <p className="muted">The description isn't kept for jobs that don't match your search. Use "Open job posting" to read it.</p>;
  }
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

// Your progress (UX redesign step 2): the four steps of an application as one row of buttons, and the two
// ways it ends in a menu beside them. The status itself is unchanged: one value with an append-only history
// (application_events), set through the same route as the old status menu.
const STEPS = ["saved", "applied", "interviewing", "offer"];
const ENDED = ["rejected", "withdrawn"];

function Progress({ d, reload }: { d: Detail; reload: () => void }) {
  const act = useAction();
  const a = d.application;
  const cur = a?.status ?? null;
  const reached = cur ? STEPS.indexOf(cur) : -1;
  const [notes, setNotes] = useState(a?.notes ?? "");
  const setStatus = (status: string) => act.run(async () => { await api.setApplication(d.id, { status }); reload(); });
  const last = a?.events[a.events.length - 1];
  return (
    <div className="progress">
      <div className="progress-head">
        <h2>Your progress</h2>
        <span className="muted">{!a ? "Not tracked yet. Pick a step to start." : `${plain(cur)} ${since(last?.at_ms ?? a.created_at_ms)}`}</span>
      </div>
      <div className="steps" role="group" aria-label="Your progress">
        {STEPS.map((s, i) => (
          <button key={s} className={cur === s ? "step-btn current" : i <= reached ? "step-btn done" : "step-btn"} aria-pressed={cur === s}
            disabled={act.busy || cur === s} onClick={() => setStatus(s)}>
            <span className="step-dot">{i <= reached ? icons.check : i + 1}</span>{plain(s)}</button>))}
        <select aria-label="Didn't work out" value={cur && ENDED.includes(cur) ? cur : ""} disabled={act.busy}
          onChange={(e) => e.target.value && setStatus(e.target.value)}>
          <option value="" disabled>Didn't work out…</option>
          {ENDED.map((s) => <option key={s} value={s}>{plain(s)}</option>)}
        </select>
      </div>
      {a && (
        <div className="row">
          <input className="notes" aria-label="Notes" placeholder="Notes: recruiter, dates, what's next…" value={notes} onChange={(e) => setNotes(e.target.value)} />
          <button className="small" disabled={act.busy || notes === (a.notes ?? "")} onClick={() => act.run(async () => { await api.setApplication(d.id, { notes }); reload(); })}>Save notes</button>
        </div>)}
      {a && a.events.length > 1 && <p className="muted">{a.events.map((e) => `${plain(e.to_status)} ${since(e.at_ms)}`).join(" ← ")}</p>}
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
          <span className="muted"> · {since(cur.created_at_ms)}</span></p>
        <div className="row"><button className="small" onClick={() => setEditing(true)}>Change</button>
          <ConfirmButton label="Remove" disabled={act.busy} onConfirm={() => act.run(async () => { await api.withdrawGoldenLabel(d.id); reload(); })} /></div>
        <ErrorBox error={act.error} />
      </Card>);
  return (
    <Section title="Help improve JobForge: your call on this job" open={editing}>
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
  // APPLY and STRETCH lead with the evidence counts (leadSentence).
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
