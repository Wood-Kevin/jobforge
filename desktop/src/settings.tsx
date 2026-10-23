// The pieces of setup that Setup (first run) and Preferences (later) share: what you are looking
// for, which companies to watch, and the AI model check. Plain fields only; no JSON, no patterns.
import { useEffect, useState, type ChangeEvent } from "react";
import { api, type SearchForm as Form } from "./api";
import { Card, ConfirmButton, ErrorBox, age, useAction, useLive } from "./ui";

const lines = (v: string[] | undefined) => (v ?? []).join("\n");
const unlines = (s: string) => s.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);

async function readFile(e: ChangeEvent<HTMLInputElement>): Promise<string | null> {
  const f = e.target.files?.[0];
  e.target.value = "";
  return f ? await f.text() : null;
}

// ----- what you are looking for -----

export function SearchFormCard({ onSaved }: { onSaved?: () => void }) {
  const loaded = useLive(api.searchForm, [], ["search_profile_saved"]);
  const act = useAction();
  const [f, setF] = useState<Form | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => { if (loaded.data) setF(loaded.data); }, [loaded.data]);
  if (!f) return <Card title="What you're looking for"><ErrorBox error={loaded.error} /><p className="muted">Loading...</p></Card>;
  const set = (patch: Partial<Form>) => setF({ ...f, ...patch });
  const save = () => act.run(async () => {
    const r = await api.saveSearchForm(f);
    setSaved(`Saved. Every job found so far is being checked against it again (version ${r.version}).`);
    onSaved?.();
  });
  const importScanner = (e: ChangeEvent<HTMLInputElement>) => act.run(async () => {
    const text = await readFile(e);
    if (text === null) return;
    const r = await api.importScannerConfig(text);
    setSaved(`Imported your job-scanner settings (version ${r.version}).`);
    loaded.reload();
    onSaved?.();
  });
  const mode = (m: string) => (
    <select value={f.work_modes[m] ?? ""} onChange={(e) => set({ work_modes: { ...f.work_modes, [m]: e.target.value } })}>
      <option value="">no preference</option><option value="favorable">I'd like this</option>
      <option value="neutral">fine</option><option value="unfavorable">I'd rather not</option>
    </select>);
  return (
    <Card title="What you're looking for">
      <div className="form-grid">
        <label className="wide">Job titles you want <span className="muted">(one per line, e.g. Help Desk, NOC Technician)</span>
          <textarea rows={5} value={lines(f.titles)} onChange={(e) => set({ titles: unlines(e.target.value) })} /></label>
        <label>Skip titles with these words <span className="muted">(seniority)</span>
          <textarea rows={4} value={lines(f.skip_seniority)} onChange={(e) => set({ skip_seniority: unlines(e.target.value) })} /></label>
        <label>Skip jobs in these fields
          <textarea rows={4} value={lines(f.skip_fields)} onChange={(e) => set({ skip_fields: unlines(e.target.value) })} /></label>
        <label>Home state <span className="muted">(two letters)</span>
          <input value={f.home_state} maxLength={2} placeholder="FL" onChange={(e) => set({ home_state: e.target.value.toUpperCase() })} /></label>
        <label>Towns you could commute to <span className="muted">(one per line; on-site jobs elsewhere are skipped)</span>
          <textarea rows={3} value={lines(f.home_towns)} onChange={(e) => set({ home_towns: unlines(e.target.value) })} /></label>
        <label>Lowest yearly pay you'd accept
          <input type="number" value={f.salary_floor ?? ""} placeholder="e.g. 55000" onChange={(e) => set({ salary_floor: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Yearly pay you're aiming for
          <input type="number" value={f.pay_target ?? ""} placeholder="e.g. 70000" onChange={(e) => set({ pay_target: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Remote {mode("remote")}</label>
        <label>Hybrid {mode("hybrid")}</label>
        <label>On-site {mode("onsite")}</label>
        <label>Ignore postings older than <span className="muted">(days; empty = no limit)</span>
          <input type="number" value={f.max_age_days ?? ""} onChange={(e) => set({ max_age_days: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Titles you'd especially like <span className="muted">(ranked higher)</span>
          <textarea rows={3} value={lines(f.favorite_titles)} onChange={(e) => set({ favorite_titles: unlines(e.target.value) })} /></label>
        <label>Levels you'd especially like <span className="muted">(e.g. tier 1, junior)</span>
          <textarea rows={3} value={lines(f.favorite_levels)} onChange={(e) => set({ favorite_levels: unlines(e.target.value) })} /></label>
      </div>
      {f.kept_patterns && Object.keys(f.kept_patterns).length > 0 && (
        <details className="muted kept">
          <summary>Also kept from your imported settings: {Object.values(f.kept_patterns).flat().length} advanced patterns (not shown above, never changed by this form)</summary>
          <ul className="compact">{Object.entries(f.kept_patterns).flatMap(([k, ps]) => ps.map((p) => <li key={k + p}><code>{p}</code></li>))}</ul>
        </details>)}
      <div className="row">
        <button className="primary" disabled={act.busy} onClick={save}>Save</button>
        {saved && <span className="ok-text">{saved}</span>}
        <span className="spacer" />
        <label className="file-button">Use my job-scanner settings instead
          <input type="file" accept=".yaml,.yml" onChange={importScanner} /></label>
      </div>
      <ErrorBox error={act.error} />
    </Card>
  );
}

// ----- companies to watch -----

export function CompaniesCard({ compact = false }: { compact?: boolean }) {
  const status = useLive(api.discoveryStatus, [], ["employer_enabled", "employer_disabled", "posting_new"], 30_000);
  const act = useAction();
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const report = (r: { added: number; existing: number; skipped_unsupported: string[]; not_understood?: string[] }) => {
    const parts = [`${r.added} added`];
    if (r.existing) parts.push(`${r.existing} already watched`);
    if (r.skipped_unsupported.length) parts.push(`${r.skipped_unsupported.length} on job boards JobForge can't read yet (${r.skipped_unsupported.slice(0, 3).join(", ")}${r.skipped_unsupported.length > 3 ? "..." : ""})`);
    if (r.not_understood?.length) parts.push(`not recognized: ${r.not_understood.join(", ")}`);
    setMsg(parts.join("; ") + ".");
    status.reload();
  };
  const emps = status.data?.employers ?? [];
  const shown = showAll ? emps : emps.slice(0, compact ? 0 : 12);
  return (
    <Card title={`Companies to watch (${emps.filter((e) => e.enabled).length})`}>
      <p className="muted">Paste careers-page links, one per line. JobForge reads boards on Greenhouse
        (boards.greenhouse.io/...), Lever (jobs.lever.co/...) and Ashby (jobs.ashbyhq.com/...).</p>
      <textarea rows={compact ? 4 : 3} value={text} placeholder={"https://boards.greenhouse.io/cloudflare\nhttps://jobs.lever.co/plaid"} onChange={(e) => setText(e.target.value)} />
      <div className="row">
        <button className="primary" disabled={act.busy || !text.trim()} onClick={() => act.run(async () => { report(await api.addEmployers(text)); setText(""); })}>Add companies</button>
        <span className="spacer" />
        <label className="file-button">Use my job-scanner watchlist
          <input type="file" accept=".yaml,.yml" onChange={(e) => act.run(async () => { const t = await readFile(e); if (t !== null) report(await api.importWatchlist(t)); })} /></label>
      </div>
      {msg && <p className="ok-text">{msg}</p>}
      <ErrorBox error={act.error ?? status.error} />
      {emps.length > 0 && (
        <>
          {shown.length > 0 && <table><tbody>{shown.map((e) => (
            <tr key={e.id} className={e.enabled ? "" : "muted"}>
              <td>{e.name}</td><td className="muted">{e.source}</td>
              <td className="muted">{!e.enabled ? "not watched" : e.status === "dead" ? "board not found - check the link"
                : e.status === "blocked" ? "board refused access" : e.last_success_age_s === null ? "first check pending" : `checked ${age(e.last_success_age_s)} ago`}</td>
              <td>{e.enabled ? <ConfirmButton label="Stop watching" disabled={act.busy} onConfirm={() => act.run(async () => { await api.disableEmployer(e.id); status.reload(); })} />
                : <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.enableEmployer(e.id); status.reload(); })}>Watch again</button>}</td>
            </tr>))}</tbody></table>}
          {emps.length > shown.length && <button className="small" onClick={() => setShowAll(true)}>Show all {emps.length}</button>}
        </>)}
    </Card>
  );
}

// ----- AI model -----

export function ModelCard({ onReady }: { onReady?: () => void }) {
  const cfgs = useLive(api.modelConfigs, [], ["model_config_tested", "model_config_enabled", "model_config_disabled"]);
  const act = useAction();
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const c = (cfgs.data ?? []).find((x) => x.default) ?? cfgs.data?.[0];
  if (!c) return <Card title="AI model"><ErrorBox error={cfgs.error} /><p className="muted">Loading...</p></Card>;
  const check = () => act.run(async () => {
    setResult({ ok: true, text: "Checking... the first check loads the model and can take a minute." });
    const r = await api.testModelConfig(c.id);
    if (!r.ok) {
      setResult({ ok: false, text: friendlyModelError(r.error ?? "", c.model_id) });
      return;
    }
    if (!c.enabled) await api.enableModelConfig(c.id);
    setResult({ ok: true, text: `Working (answered in ${r.seconds ?? "?"} s). JobForge will now read postings with it.` });
    cfgs.reload();
    onReady?.();
  });
  return (
    <Card title="AI model">
      <p className="muted">JobForge reads each posting with a model running on this PC in LM Studio ({c.model_id}). Nothing is sent
        anywhere else.</p>
      <p>{c.enabled ? <span className="ok-text">Turned on{c.tested_at_ms ? `, last checked ${age(Math.round((Date.now() - c.tested_at_ms) / 1000))} ago` : ""}.</span>
        : <span className="warn">Not turned on yet.</span>}</p>
      <div className="row"><button className="primary" disabled={act.busy} onClick={check}>{c.enabled ? "Check again" : "Check and turn on"}</button>
        {c.enabled && <button disabled={act.busy} onClick={() => act.run(async () => { await api.disableModelConfig(c.id); cfgs.reload(); })}>Turn off</button>}</div>
      {result && <p className={result.ok ? "ok-text" : "error"}>{result.text}</p>}
      <ErrorBox error={act.error} />
    </Card>
  );
}

function friendlyModelError(err: string, model: string): string {
  if (/unavailable|ConnectError|refused|not reachable/i.test(err))
    return "LM Studio is not answering. Open LM Studio, start its local server (Developer tab), then check again.";
  if (/not listed|not in|model_not_listed/i.test(err))
    return `LM Studio is running but the model ${model} is not loaded. Download and load it in LM Studio, then check again.`;
  if (/context/i.test(err)) return "The model's context length is too small. Load it with at least 16,000 tokens of context.";
  return `The model did not pass the check: ${err}`;
}
