// Preferences (P1.8): the search profile (every save is a new, immutable version and starts a
// re-filter; the agent validates and answers 422 naming the bad field) and the model configs
// (a config can be enabled only after its load-and-snippet test passes).
import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Card, ErrorBox, ago, useAction, useLive } from "../ui";

type Rules = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const list = (v: unknown): string => (Array.isArray(v) ? v.join(", ") : "");
const unlist = (s: string): string[] => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function Preferences() {
  return (
    <div className="page">
      <SearchProfile />
      <ModelConfigs />
    </div>
  );
}

function SearchProfile() {
  const prof = useLive(api.searchProfile, [], ["search_profile_saved"]);
  const act = useAction();
  const [rules, setRules] = useState<Rules | null>(null);
  const [json, setJson] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => {
    if (prof.data) {
      setRules(structuredClone(prof.data.rules));
      setJson(JSON.stringify(prof.data.rules, null, 2));
    }
  }, [prof.data]);
  if (prof.error) return <Card title="Search profile"><ErrorBox error={prof.error} /></Card>;
  if (!rules) return <Card title="Search profile"><p className="muted">Loading...</p></Card>;

  const opp = rules.opportunity ?? {};
  const set = (fn: (r: Rules) => void) => { const r = structuredClone(rules); fn(r); setRules(r); setJson(JSON.stringify(r, null, 2)); };
  const save = (r: Rules) => act.run(async () => { const out = await api.saveSearchProfile(r); setSaved(`Saved as version ${out.version}; re-filter started.`); prof.reload(); });

  return (
    <Card title={`Search profile (version ${prof.data!.version})`} actions={<span className="muted">saved {ago(prof.data!.created_at_ms)} ago</span>}>
      <div className="fact-form">
        <label>Salary floor (per year)<input type="number" value={rules.salary?.floor_annual ?? ""} onChange={(e) => set((r) => { (r.salary ??= {}).floor_annual = Number(e.target.value || 0); })} /></label>
        <label>Max posting age (days, empty = no limit)<input type="number" value={rules.freshness?.max_age_days ?? ""} onChange={(e) => set((r) => { (r.freshness ??= {}).max_age_days = e.target.value ? Number(e.target.value) : null; })} /></label>
        <label>Pay target (per year)<input type="number" value={opp.pay_target_annual ?? ""} onChange={(e) => set((r) => { r.opportunity ??= {}; if (e.target.value) r.opportunity.pay_target_annual = Number(e.target.value); else delete r.opportunity.pay_target_annual; })} /></label>
        {(["remote", "hybrid", "onsite"] as const).map((m) => (
          <label key={m}>{m}<select value={opp.work_modes?.[m] ?? ""} onChange={(e) => set((r) => { r.opportunity ??= {}; r.opportunity.work_modes ??= {}; if (e.target.value) r.opportunity.work_modes[m] = e.target.value; else delete r.opportunity.work_modes[m]; })}>
            <option value="">-</option><option value="favorable">favorable</option><option value="neutral">neutral</option><option value="unfavorable">unfavorable</option></select></label>))}
        {(["role_families", "seniority"] as const).map((k) => (["favorable", "unfavorable"] as const).map((side) => (
          <label key={`${k}.${side}`}>{k.replace("_", " ")}: {side} title patterns<input value={list(opp[k]?.[side])} placeholder="comma separated, e.g. soc, noc"
            onChange={(e) => set((r) => { r.opportunity ??= {}; r.opportunity[k] ??= {}; r.opportunity[k][side] = unlist(e.target.value); })} /></label>)))}
      </div>
      <div className="row"><button className="primary" disabled={act.busy} onClick={() => save(rules)}>Save new version</button>
        {saved && <span className="muted">{saved}</span>}</div>
      <details>
        <summary>All rules as JSON (title, location and the rest)</summary>
        <textarea className="code" rows={18} value={json} onChange={(e) => setJson(e.target.value)} />
        <div className="row"><button disabled={act.busy} onClick={() => {
          let parsed: Rules;
          try { parsed = JSON.parse(json); } catch (e) { act.run(() => Promise.reject(new Error(`Not valid JSON: ${e}`))); return; }
          save(parsed);
        }}>Save JSON as new version</button></div>
      </details>
      <ErrorBox error={act.error} />
    </Card>
  );
}

function ModelConfigs() {
  const cfgs = useLive(api.modelConfigs, [], ["model_config_tested", "model_config_enabled", "model_config_disabled"]);
  const act = useAction();
  const [result, setResult] = useState<string | null>(null);
  return (
    <Card title="Model configs">
      <table><thead><tr><th>Name</th><th>Model</th><th>State</th><th /></tr></thead>
        <tbody>{(cfgs.data ?? []).map((c) => (
          <tr key={c.id}><td>{c.name}{c.default && <span className="muted"> (default)</span>}</td><td className="muted">{c.model_id} @ {c.base_url}</td>
            <td><Badge value={c.enabled ? "pass" : "none"} title={c.enabled ? "enabled" : "disabled"} /> {c.tested_at_ms ? `tested ${ago(c.tested_at_ms)} ago` : "never tested"}</td>
            <td className="actions">
              <button className="small" disabled={act.busy} onClick={() => act.run(async () => { setResult("Testing (loads the model, can take a minute)..."); const r = await api.testModelConfig(c.id); setResult(r.ok ? `Test passed in ${r.seconds ?? "?"} s.` : `Test failed: ${r.error}`); cfgs.reload(); })}>Test</button>
              {c.enabled
                ? <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.disableModelConfig(c.id); cfgs.reload(); })}>Disable</button>
                : <button className="small primary" disabled={act.busy || !c.tested_at_ms} title={c.tested_at_ms ? "" : "test it first"} onClick={() => act.run(async () => { await api.enableModelConfig(c.id); cfgs.reload(); })}>Enable</button>}
            </td></tr>))}</tbody></table>
      {result && <p className="muted">{result}</p>}
      <ErrorBox error={act.error ?? cfgs.error} />
    </Card>
  );
}
