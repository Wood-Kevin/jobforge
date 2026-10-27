// The pieces of setup that Setup (first run) and Preferences (later) share: what you are looking
// for, which companies to watch, and the AI model check. Plain fields only; no JSON, no patterns.
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { openUrl } from "./agent";
import { getDraft, setDraft } from "./draft";
import { api, type EmployerAdd, type ModelStep, type SearchForm as Form } from "./api";
import { Card, ConfirmButton, ErrorBox, age, useAction, useLive } from "./ui";


async function readFile(e: ChangeEvent<HTMLInputElement>): Promise<string | null> {
  const f = e.target.files?.[0];
  e.target.value = "";
  return f ? await f.text() : null;
}

// ----- what you are looking for -----

// The 50 states and DC, for the home-state menu (it was a two-letter text box).
const STATES: [string, string][] = [["AL", "Alabama"], ["AK", "Alaska"], ["AZ", "Arizona"], ["AR", "Arkansas"], ["CA", "California"],
  ["CO", "Colorado"], ["CT", "Connecticut"], ["DE", "Delaware"], ["DC", "District of Columbia"], ["FL", "Florida"], ["GA", "Georgia"],
  ["HI", "Hawaii"], ["ID", "Idaho"], ["IL", "Illinois"], ["IN", "Indiana"], ["IA", "Iowa"], ["KS", "Kansas"], ["KY", "Kentucky"],
  ["LA", "Louisiana"], ["ME", "Maine"], ["MD", "Maryland"], ["MA", "Massachusetts"], ["MI", "Michigan"], ["MN", "Minnesota"],
  ["MS", "Mississippi"], ["MO", "Missouri"], ["MT", "Montana"], ["NE", "Nebraska"], ["NV", "Nevada"], ["NH", "New Hampshire"],
  ["NJ", "New Jersey"], ["NM", "New Mexico"], ["NY", "New York"], ["NC", "North Carolina"], ["ND", "North Dakota"], ["OH", "Ohio"],
  ["OK", "Oklahoma"], ["OR", "Oregon"], ["PA", "Pennsylvania"], ["RI", "Rhode Island"], ["SC", "South Carolina"], ["SD", "South Dakota"],
  ["TN", "Tennessee"], ["TX", "Texas"], ["UT", "Utah"], ["VT", "Vermont"], ["VA", "Virginia"], ["WA", "Washington"],
  ["WV", "West Virginia"], ["WI", "Wisconsin"], ["WY", "Wyoming"]];

/** A list typed as tags (UX redesign step 5; was a "one per line" box): Enter adds, × or Backspace on an empty
 * box removes, and a pasted list (one per line) adds every line. Commas stay part of a tag: a title can have one. */
export function TagInput({ label, hint, values, onChange, upper = false, placeholder }: { label: string; hint?: string; values: string[];
  onChange: (v: string[]) => void; upper?: boolean; placeholder?: string }) {
  const [text, setText] = useState("");
  const add = (raw: string) => {
    const next = [...values];
    for (const part of raw.split(/\r?\n/).map((x) => x.trim()).filter(Boolean).map((x) => (upper ? x.toUpperCase() : x)))
      if (!next.some((v) => v.toLowerCase() === part.toLowerCase())) next.push(part);
    onChange(next);
    setText("");
  };
  return (
    <div className="tag-field">
      <span className="tag-label">{label}{hint && <span className="muted"> {hint}</span>}</span>
      <div className="tags">
        {values.map((v) => (
          <span key={v} className="tag">{v}<button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>×</button></span>))}
        <input aria-label={label} value={text} placeholder={values.length ? "Add another…" : placeholder ?? "Type and press Enter"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); if (text.trim()) add(text); }
            else if (e.key === "Backspace" && !text && values.length) onChange(values.slice(0, -1));
          }}
          onBlur={() => { if (text.trim()) add(text); }}
          onPaste={(e) => { const t = e.clipboardData.getData("text"); if (/\n/.test(t)) { e.preventDefault(); add(t); } }} />
      </div>
    </div>
  );
}

// Essentials first; the rest folded under "Fine-tune" (UX redesign step 5: thirteen fields at one level). Edits
// are kept as a draft (draft.ts) until saved or undone, and a bar at the bottom says they aren't saved yet.
export function SearchFormCard({ onSaved }: { onSaved?: () => void }) {
  const loaded = useLive(api.searchForm, [], ["search_profile_saved"]);
  const act = useAction();
  const [f, setF] = useState<Form | null>(() => getDraft());
  const [saved, setSaved] = useState<string | null>(null);
  // After a save the agent's answer becomes the form (it may tidy what was typed), not a new draft.
  const adopt = useRef(false);
  useEffect(() => {
    if (loaded.data && (adopt.current || !getDraft())) { setF(loaded.data); adopt.current = false; }
  }, [loaded.data]);
  const dirty = f !== null && loaded.data !== null && JSON.stringify(f) !== JSON.stringify(loaded.data);
  useEffect(() => { if (!adopt.current && loaded.data) setDraft(dirty ? f : null); }, [f, loaded.data, dirty]);
  if (!f) return <Card title="Your search"><ErrorBox error={loaded.error} /><p className="muted">Loading...</p></Card>;
  const set = (patch: Partial<Form>) => { setSaved(null); setF({ ...f, ...patch }); };
  const save = () => act.run(async () => {
    const r = await api.saveSearchForm(f);
    adopt.current = true;
    setDraft(null);
    setSaved(`Saved. Every job found so far is being checked against it again (version ${r.version}).`);
    loaded.reload();
    onSaved?.();
  });
  const undo = () => { setDraft(null); setF(loaded.data); setSaved(null); };
  const importScanner = (e: ChangeEvent<HTMLInputElement>) => act.run(async () => {
    const text = await readFile(e);
    if (text === null) return;
    const r = await api.importScannerConfig(text);
    adopt.current = true;
    setDraft(null);
    setSaved(`Imported your job-scanner settings (version ${r.version}).`);
    loaded.reload();
    onSaved?.();
  });
  const mode = (m: string) => (
    <select value={f.work_modes[m] ?? ""} onChange={(e) => set({ work_modes: { ...f.work_modes, [m]: e.target.value } })}>
      <option value="">No preference</option><option value="favorable">I'd like this</option>
      <option value="neutral">Fine</option><option value="unfavorable">I'd rather not</option>
    </select>);
  return (
    <Card title="Your search">
      <div className="form-grid">
        <div className="wide"><TagInput label="Job titles you want" hint="(e.g. Help Desk, NOC Technician)" values={f.titles}
          onChange={(v) => set({ titles: v })} /></div>
        {/* No preselected state: an empty home state makes "Jacksonville, FL" count as another state. */}
        <label>State you live in
          <select value={f.home_state} onChange={(e) => set({ home_state: e.target.value })}>
            <option value="">Choose your state…</option>
            {STATES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
          {!f.home_state && <span className="warn">Not set: on-site jobs in your towns are skipped as out of state, and
            jobs that can't hire in your state aren't caught.</span>}</label>
        <TagInput label="Towns you could commute to" hint="(on-site jobs elsewhere are skipped)" values={f.home_towns}
          onChange={(v) => set({ home_towns: v })} />
        <TagInput label="Other states your towns are in" hint="(two letters, e.g. GA; leave empty if none)" upper values={f.nearby_states}
          onChange={(v) => set({ nearby_states: v })} />
        <label>Lowest yearly pay you'd accept
          <input type="number" value={f.salary_floor ?? ""} placeholder="e.g. 55000" onChange={(e) => set({ salary_floor: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Yearly pay you're aiming for
          <input type="number" value={f.pay_target ?? ""} placeholder="e.g. 70000" onChange={(e) => set({ pay_target: e.target.value ? Number(e.target.value) : null })} /></label>
        <label>Remote {mode("remote")}</label>
        <label>Hybrid {mode("hybrid")}</label>
        <label>On-site {mode("onsite")}</label>
      </div>
      <details className="fine-tune">
        <summary>Fine-tune your search <span className="muted">words and fields to skip, favorite titles and levels, posting age</span></summary>
        <div className="form-grid">
          <TagInput label="Skip titles with these words" hint="(seniority, e.g. senior)" values={f.skip_seniority} onChange={(v) => set({ skip_seniority: v })} />
          <TagInput label="Skip jobs in these fields" hint="(e.g. sales)" values={f.skip_fields} onChange={(v) => set({ skip_fields: v })} />
          <TagInput label="Titles you'd especially like" hint="(ranked higher)" values={f.favorite_titles} onChange={(v) => set({ favorite_titles: v })} />
          <TagInput label="Levels you'd especially like" hint="(e.g. tier 1, junior)" values={f.favorite_levels} onChange={(v) => set({ favorite_levels: v })} />
          <label>Ignore postings older than <span className="muted">(days; empty = no limit)</span>
            <input type="number" value={f.max_age_days ?? ""} onChange={(e) => set({ max_age_days: e.target.value ? Number(e.target.value) : null })} /></label>
        </div>
        {f.kept_patterns && Object.keys(f.kept_patterns).length > 0 && (
          <details className="muted kept">
            <summary>Also kept from your imported settings: {Object.values(f.kept_patterns).flat().length} advanced patterns (not shown above, never changed by this form)</summary>
            <ul className="compact">{Object.entries(f.kept_patterns).flatMap(([k, ps]) => ps.map((p) => <li key={k + p}><code>{p}</code></li>))}</ul>
          </details>)}
      </details>
      <div className={dirty ? "row save-bar dirty" : "row save-bar"}>
        {/* Saving unchanged defaults still counts on first run (setup waits for a saved search). */}
        <button className="primary" disabled={act.busy || (!dirty && loaded.data?.has_profile !== false)} onClick={save}>Save</button>
        {dirty && <><span>You have unsaved changes.</span><button className="quiet" disabled={act.busy} onClick={undo}>Undo changes</button></>}
        {!dirty && saved && <span className="ok-text">{saved}</span>}
        <span className="spacer" />
        <label className="file-button">Use my job-scanner settings instead
          <input className="visually-hidden" type="file" accept=".yaml,.yml" onChange={importScanner} /></label>
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
  const report = (r: EmployerAdd) => {
    const parts = [`${r.added} added`];
    if (r.existing) parts.push(`${r.existing} already watched`);
    if (r.watched_again) parts.push(`${r.watched_again} watched again`);
    if (r.not_found?.length) parts.push(`not found on Greenhouse, Lever or Ashby: ${r.not_found.join(", ")} (paste their careers link if they use one of those)`);
    if (r.check_failed?.length) parts.push(`couldn't check right now, try again: ${r.check_failed.join("; ")}`);
    if (r.not_checked?.length) parts.push(`not checked (10 names at a time): ${r.not_checked.join(", ")}`);
    if (r.skipped_unsupported.length) parts.push(`${r.skipped_unsupported.length} on job boards JobForge can't read yet (${r.skipped_unsupported.slice(0, 3).join(", ")}${r.skipped_unsupported.length > 3 ? "..." : ""})`);
    if (r.not_understood?.length) parts.push(`not recognized: ${r.not_understood.join(", ")}`);
    setMsg(parts.join("; ") + ".");
    status.reload();
  };
  const emps = status.data?.employers ?? [];
  const b = status.data?.built_in;
  const shown = showAll ? emps : emps.slice(0, compact ? 0 : 12);
  const watched = emps.filter((e) => e.enabled).length + (b?.enabled ?? 0);
  return (
    <Card title={`Companies to watch (${watched})`}>
      {b && b.total > 0 && (
        <div className="row">
          <p className="muted">{b.watching
            ? <>JobForge already watches <strong>{b.enabled}</strong> companies from its built-in list, each checked about once a
              day{b.checked_once < b.enabled ? ` (${b.checked_once} checked so far; the rest during the first day)` : ""}.</>
            : <>The built-in list of {b.total} companies is turned off. Only the companies you add are watched.</>}</p>
          <span className="spacer" />
          {b.watching
            ? <ConfirmButton label="Turn off the built-in list" disabled={act.busy} onConfirm={() => act.run(async () => { await api.setBuiltInWatch(false); status.reload(); })} />
            : <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.setBuiltInWatch(true); status.reload(); })}>Turn the built-in list back on</button>}
        </div>)}
      <p className="muted">To add others, type company names or paste careers-page links, one per line. JobForge reads
        Greenhouse, Lever and Ashby job boards and finds each company's board itself.</p>
      <textarea rows={3} value={text} placeholder={"Stripe\nDatadog\nhttps://jobs.lever.co/plaid"} onChange={(e) => setText(e.target.value)} />
      <div className="row">
        <button className="primary" disabled={act.busy || !text.trim()} onClick={() => act.run(async () => { setMsg("Looking them up..."); report(await api.addEmployers(text)); setText(""); })}>Add companies</button>
        <span className="spacer" />
        <label className="file-button">Use my job-scanner watchlist
          <input className="visually-hidden" type="file" accept=".yaml,.yml" onChange={(e) => act.run(async () => { const t = await readFile(e); if (t !== null) report(await api.importWatchlist(t)); })} /></label>
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
  // The model on this PC: the fallback step's config (SPEC D18), which every step uses unless handed to ChatGPT.
  const c = (cfgs.data ?? []).find((x) => x.steps.includes("fallback")) ?? cfgs.data?.find((x) => x.provider_kind === "openai_compatible");
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
      <p className="muted">JobForge reads each posting with a model running on this PC in LM Studio ({c.model_id}). Unless you
        choose ChatGPT in Preferences, nothing is sent anywhere else.</p>
      {/* Setting up LM Studio, step by step (UX redesign step 5): open until the model works, then folded away. */}
      <details className="checklist" open={!c.enabled}>
        <summary>How to set up LM Studio</summary>
        <ol>
          <li>Install LM Studio from <a href="https://lmstudio.ai" onClick={(e) => { e.preventDefault(); act.run(() => openUrl("https://lmstudio.ai")); }}>lmstudio.ai</a> and open it.</li>
          <li>In its search (the magnifier on the left), find and download <strong>{c.model_id}</strong>.</li>
          <li>Load the model with a context length of at least 16,000.</li>
          <li>Open the Developer tab and start the local server.</li>
          <li>Come back here and press {c.enabled ? "Check again" : "Check and turn on"}.</li>
        </ol>
      </details>
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

// ----- ChatGPT (SPEC D18) -----

const STEP_LABELS: [ModelStep, string, string][] = [
  ["e3", "Matching you to jobs", "Requirement lines and your profile items"],
  ["e1", "Reading postings", "Each posting's title and text"],
  ["resume", "Resume suggestions", "Your resume's text, when you ask for suggestions"],
];

/** Optional: the person's ChatGPT plan for some steps, with the model on this PC as the fallback. Sign-in happens
 * in the system browser (the agent's loopback callback); this card only starts it and watches for the result. */
export function ChatGPTCard() {
  const st = useLive(api.chatgpt, [], ["model_step_changed", "model_config_enabled", "model_config_disabled", "model_config_tested"]);
  const act = useAction();
  const [models, setModels] = useState<{ slug: string; display_name: string }[] | null>(null);
  const [pick, setPick] = useState<string>("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const s = st.data;
  const waiting = s?.sign_in.state === "waiting";
  useEffect(() => {
    if (!waiting) return;
    const t = window.setInterval(st.reload, 2000);
    return () => window.clearInterval(t);
  }, [waiting, st.reload]);
  useEffect(() => {
    if (!s?.signed_in || models) return;
    act.run(async () => {
      const m = await api.chatgptModels();
      setModels(m);
      const used = s.configs.find((c) => c.enabled)?.model_id;
      setPick(used ?? (m.some((x) => x.slug === s.default_model) ? s.default_model : m[0]?.slug ?? ""));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s?.signed_in]);
  if (!s) return <Card title="ChatGPT (optional)"><ErrorBox error={st.error} /><p className="muted">Loading...</p></Card>;
  const cloud = s.configs.find((c) => c.enabled && c.model_id === pick) ?? s.configs.find((c) => c.enabled);
  const out = s.configs.find((c) => c.out && Object.values(s.steps).includes(c.id));
  const local = s.steps.fallback;
  const signIn = () => act.run(async () => {
    setMsg(null);
    const r = await api.chatgptSignIn();
    await openUrl(r.authorize_url);
    st.reload();
  });
  const useModel = () => act.run(async () => {
    setMsg({ ok: true, text: "Checking the model with one small request on your plan..." });
    const r = await api.useChatgptModel(pick);
    setMsg(r.test.ok ? { ok: true, text: `Working (answered in ${r.test.seconds ?? "?"} s). Choose below which steps use it.` }
      : { ok: false, text: `The model did not pass the check: ${r.test.error ?? "no reason given"}` });
    st.reload();
  });
  const setStep = (step: ModelStep, id: number) => act.run(async () => { await api.setModelStep(step, id); st.reload(); });
  return (
    <Card title="ChatGPT (optional)">
      <p className="muted">Use your ChatGPT plan instead of the model on this PC: it judges jobs more accurately. It counts against your
        plan's usage, and JobForge goes back to the model on this PC by itself while your plan is at its limit or ChatGPT can't be reached.</p>
      {!s.signed_in ? (<>
        <details className="checklist" open>
          <summary>What is sent to OpenAI</summary>
          <ul className="compact">{STEP_LABELS.map(([k, label, what]) => <li key={k}><strong>{label}:</strong> {what}.</li>)}</ul>
          <p className="muted">Only for the steps you choose. Your answers about citizenship, clearance, location and similar are never
            sent: JobForge checks those itself. Whether OpenAI may keep or learn from these requests follows your ChatGPT data
            controls; check them in ChatGPT's settings.</p>
        </details>
        <div className="row"><button className="primary" disabled={act.busy || waiting} onClick={signIn}>Sign in with ChatGPT</button>
          {waiting && <span className="muted">Finish signing in in your browser...</span>}</div>
        {s.sign_in.state === "failed" && <p className="error">Sign-in didn't finish: {s.sign_in.error}</p>}
      </>) : (<>
        <div className="row"><span>Signed in{s.email ? <> as <strong>{s.email}</strong></> : ""}.</span><span className="spacer" />
          <ConfirmButton label="Sign out" disabled={act.busy} onConfirm={() => act.run(async () => { await api.chatgptSignOut(); setModels(null); st.reload(); })} /></div>
        {out && <p className="warn">{out.out === "capped" ? "Your ChatGPT plan is at its usage limit" : "ChatGPT isn't answering right now"}:
          JobForge is using the model on this PC until it answers again, then checks those jobs again with ChatGPT.</p>}
        <div className="row">
          <label>Model <select value={pick} onChange={(e) => setPick(e.target.value)}>
            {(models ?? []).map((m) => <option key={m.slug} value={m.slug}>{m.display_name}{m.slug === s.default_model ? " (suggested)" : ""}</option>)}
          </select></label>
          <button disabled={act.busy || !pick || s.configs.some((c) => c.enabled && c.model_id === pick)} onClick={useModel}>Check and turn on</button>
        </div>
        <table><tbody>{STEP_LABELS.map(([k, label, what]) => {
          const current = s.steps[k];
          const onCloud = s.configs.some((c) => c.id === current);
          return (
            <tr key={k}><td>{label}<div className="muted">{what}</div></td>
              <td><label className="choice"><input type="radio" name={`step-${k}`} checked={!onCloud} disabled={act.busy || local === null}
                onChange={() => local !== null && setStep(k, local)} />This PC</label>
                <label className="choice"><input type="radio" name={`step-${k}`} checked={onCloud} disabled={act.busy || !cloud}
                  onChange={() => cloud && setStep(k, cloud.id)} />ChatGPT</label></td></tr>);
        })}</tbody></table>
        {!cloud && <p className="muted">Check and turn on a model first.</p>}
        <p className="muted">Moving matching to another model checks every job you have again (one request each on your plan).</p>
      </>)}
      {msg && <p className={msg.ok ? "ok-text" : "error"}>{msg.text}</p>}
      <ErrorBox error={act.error ?? st.error} />
    </Card>
  );
}
