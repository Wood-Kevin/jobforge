// Profile (P1.8): experience items, typed eligibility facts, verify, completeness, resumes, the
// inferred-claims inbox, and import of a reviewed profile file. Everything created here is
// self-reported; "verified" only ever comes from the Confirm button (verify route; SPEC D2, Q6).
import { useState, type ChangeEvent } from "react";
import { api, type Item, type Schema } from "../api";
import { Badge, Card, ConfirmButton, ErrorBox, ago, useAction, useLive } from "../ui";

const KIND_ORDER = ["role", "achievement", "skill", "cert", "education"];

export default function Profile() {
  const items = useLive(api.items, [], ["profile_changed"]);
  const schema = useLive(api.schema, [], []);
  const comp = useLive(api.completeness, [], ["profile_changed"]);
  const list = items.data ?? [];
  return (
    <div className="page">
      <Import existing={list} onDone={items.reload} />
      {schema.data && <Facts schema={schema.data} items={list.filter((i) => i.kind === "eligibility")} reload={items.reload} />}
      <Completeness c={comp.data} reload={comp.reload} />
      {KIND_ORDER.map((k) => <Kind key={k} kind={k} items={list.filter((i) => i.kind === k)} reload={items.reload} />)}
      <Claims />
      <Resumes />
      <ErrorBox error={items.error ?? schema.error} />
    </div>
  );
}

// ----- one kind of item -----

function Kind({ kind, items, reload }: { kind: string; items: Item[]; reload: () => void }) {
  const [adding, setAdding] = useState(false);
  return (
    <Card title={`${title(kind)} (${items.length})`} actions={<button onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add"}</button>}>
      {adding && <ItemForm kind={kind} onSaved={() => { setAdding(false); reload(); }} />}
      {items.length === 0 ? <p className="muted">None yet.</p> : (
        <ul className="items">{items.map((i) => <ItemRow key={i.id} item={i} reload={reload} />)}</ul>)}
    </Card>
  );
}

function ItemRow({ item, reload }: { item: Item; reload: () => void }) {
  const act = useAction();
  const [editing, setEditing] = useState(false);
  if (editing) return <li><ItemForm kind={item.kind} item={item} onSaved={() => { setEditing(false); reload(); }} onCancel={() => setEditing(false)} /></li>;
  return (
    <li>
      <div className="item-line">
        <Badge value={item.status} />
        <span className="statement">{item.statement}</span>
        {(item.start_date || item.end_date) && <span className="muted">{item.start_date ?? "?"} to {item.end_date ?? "present"}</span>}
        {item.years !== null && <span className="muted">{item.years} yrs</span>}
        <span className="actions">
          {item.status === "verified"
            ? <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.unverifyItem(item.id); reload(); })}>Undo confirm</button>
            : <button className="small primary" disabled={act.busy} onClick={() => act.run(async () => { await api.verifyItem(item.id, null); reload(); })} title="Confirm this is accurate">Confirm</button>}
          <button className="small" onClick={() => setEditing(true)}>Edit</button>
          <ConfirmButton label="Delete" disabled={act.busy} onConfirm={() => act.run(async () => { await api.deleteItem(item.id); reload(); })} />
        </span>
      </div>
      <ErrorBox error={act.error} />
    </li>
  );
}

function ItemForm({ kind, item, onSaved, onCancel }: { kind: string; item?: Item; onSaved: () => void; onCancel?: () => void }) {
  const act = useAction();
  const [statement, setStatement] = useState(item?.statement ?? "");
  const [start, setStart] = useState(item?.start_date ?? "");
  const [end, setEnd] = useState(item?.end_date ?? "");
  const dated = kind === "role" || kind === "achievement" || kind === "education";
  const save = () => act.run(async () => {
    const fields = { statement, ...(dated ? { start_date: start || null, end_date: end || null } : {}) };
    if (item) await api.updateItem(item.id, fields);
    else await api.createItem({ kind, source: "user", ...fields });
    onSaved();
  });
  return (
    <div className="form">
      <textarea rows={2} value={statement} onChange={(e) => setStatement(e.target.value)} placeholder="What it says, in your words" />
      {dated && <div className="row">
        <input value={start} onChange={(e) => setStart(e.target.value)} placeholder="start YYYY-MM" />
        <input value={end} onChange={(e) => setEnd(e.target.value)} placeholder="end YYYY-MM (empty = present)" />
      </div>}
      {item?.status === "verified" && <p className="muted">Changing what this says removes its confirmation; confirm it again after.</p>}
      <div className="row"><button className="primary" disabled={act.busy || !statement.trim()} onClick={save}>Save</button>
        {onCancel && <button onClick={onCancel}>Cancel</button>}</div>
      <ErrorBox error={act.error} />
    </div>
  );
}

// ----- eligibility facts: typed forms from the agent's own schema -----

function Facts({ schema, items, reload }: { schema: Schema; items: Item[]; reload: () => void }) {
  return (
    <Card title="Eligibility facts">
      <p className="muted">Things like citizenship, where you live and clearance. JobForge checks job requirements against these
        itself (not the AI). A fact only settles a job once you confirm it.</p>
      <table className="facts"><tbody>
        {Object.entries(schema.eligibility).map(([type, fields]) => {
          const existing = items.filter((i) => i.attributes.type === type);
          return existing.length ? existing.map((i) => <FactRow key={i.id} type={type} fields={fields} item={i} reload={reload} />)
            : <FactRow key={type} type={type} fields={fields} reload={reload} />;
        })}
      </tbody></table>
    </Card>
  );
}

function FactRow({ type, fields, item, reload }: { type: string; fields: Schema["eligibility"][string]; item?: Item; reload: () => void }) {
  const act = useAction();
  const [editing, setEditing] = useState(false);
  const [vals, setVals] = useState<Record<string, unknown>>(() => ({ ...(item?.attributes ?? {}) }));
  const [statement, setStatement] = useState(item?.statement ?? "");
  const save = () => act.run(async () => {
    const attributes: Record<string, unknown> = { type };
    for (const f of Object.keys(fields)) if (vals[f] !== undefined && vals[f] !== "") attributes[f] = vals[f];
    const st = statement.trim() || describe(type, attributes);
    if (item) await api.updateItem(item.id, { statement: st, attributes });
    else await api.createItem({ kind: "eligibility", statement: st, attributes, source: "user" });
    setEditing(false);
    reload();
  });
  if (!editing) return (
    <tr>
      <th>{title(type)}</th>
      <td>{item ? <><Badge value={item.status} /> {item.statement} <span className="muted">{fmtAttrs(item.attributes)}</span></> : <span className="muted">not declared</span>}</td>
      <td className="actions">
        {item && item.status !== "verified" && <button className="small primary" disabled={act.busy} onClick={() => act.run(async () => { await api.verifyItem(item.id, null); reload(); })}>Confirm</button>}
        {item && item.status === "verified" && <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.unverifyItem(item.id); reload(); })}>Undo confirm</button>}
        <button className="small" onClick={() => setEditing(true)}>{item ? "Edit" : "Declare"}</button>
        <ErrorBox error={act.error} />
      </td>
    </tr>);
  return (
    <tr>
      <th>{title(type)}</th>
      <td colSpan={2}>
        <div className="fact-form">
          {Object.entries(fields).map(([f, spec]) => (
            <label key={f}>{f.replace(/_/g, " ")}{spec.required && " *"}<FieldInput spec={spec.type} value={vals[f]} onChange={(v) => setVals({ ...vals, [f]: v })} /></label>))}
          <label>description<input value={statement} onChange={(e) => setStatement(e.target.value)} placeholder="optional; generated if empty" /></label>
        </div>
        <div className="row"><button className="primary" disabled={act.busy} onClick={save}>Save</button><button onClick={() => setEditing(false)}>Cancel</button></div>
        <ErrorBox error={act.error} />
      </td>
    </tr>);
}

function FieldInput({ spec, value, onChange }: { spec: string; value: unknown; onChange: (v: unknown) => void }) {
  if (spec === "bool") return (
    <select value={value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "true")}>
      <option value="">-</option><option value="true">yes</option><option value="false">no</option></select>);
  if (spec.includes(" | ")) return (
    <select value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
      <option value="">-</option>{spec.split(" | ").map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}</select>);
  if (spec.startsWith("number")) return <input type="number" value={value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />;
  return <input value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value || undefined)} />;
}

// ----- completeness -----

function Completeness({ c, reload }: { c: { cert_list_complete: boolean; eligibility_facts_complete: boolean } | null; reload: () => void }) {
  const act = useAction();
  if (!c) return null;
  const toggle = (k: "cert_list_complete" | "eligibility_facts_complete") => act.run(async () => { await api.setCompleteness({ [k]: !c[k] }); reload(); });
  return (
    <Card title="Is your profile complete?">
      <label className="check"><input type="checkbox" checked={c.cert_list_complete} onChange={() => toggle("cert_list_complete")} disabled={act.busy} />
        My certification list is complete <span className="muted">(a cert not listed counts as a gap instead of unknown)</span></label>
      <label className="check"><input type="checkbox" checked={c.eligibility_facts_complete} onChange={() => toggle("eligibility_facts_complete")} disabled={act.busy} />
        My eligibility facts are complete <span className="muted">(no clearance, license or program fact = I have none)</span></label>
      <ErrorBox error={act.error} />
    </Card>
  );
}

// ----- import a reviewed profile file -----

type ImportItem = { kind: string; statement: string; attributes?: Record<string, unknown>; start_date?: string | null; end_date?: string | null; years?: number | null; source?: string };

function parseImport(text: string): { items: ImportItem[]; completeness?: Record<string, boolean> } {
  const v = JSON.parse(text);
  if (Array.isArray(v)) return { items: v };
  const items = [...(v.items ?? []), ...(v.eligibility_from_resumes ?? []), ...(v.eligibility_from_kevin ?? []), ...(v.eligibility ?? [])];
  const completeness = v.completeness ? Object.fromEntries(Object.entries(v.completeness).filter(([k]) => k.endsWith("_complete"))) as Record<string, boolean> : undefined;
  return { items, completeness };
}

const ITEM_FIELDS = ["kind", "statement", "attributes", "start_date", "end_date", "years", "source"] as const;

export function Import({ existing, onDone }: { existing: Item[]; onDone: () => void }) {
  const act = useAction();
  const [plan, setPlan] = useState<{ name: string; items: ImportItem[]; dupes: number; completeness?: Record<string, boolean> } | null>(null);
  const [report, setReport] = useState<string[]>([]);
  const key = (i: { kind: string; statement: string }) => `${i.kind}\u0000${i.statement.trim().toLowerCase()}`;
  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    act.clear();
    setReport([]);
    try {
      const parsed = parseImport(await f.text());
      const have = new Set(existing.map(key));
      const fresh = parsed.items.filter((i) => i && i.kind && i.statement && !have.has(key(i)));
      setPlan({ name: f.name, items: fresh, dupes: parsed.items.length - fresh.length, completeness: parsed.completeness });
    } catch (err) {
      setPlan(null);
      setReport(["This does not look like a profile file."]);
    }
  };
  const run = () => act.run(async () => {
    const out: string[] = [];
    let ok = 0;
    for (const raw of plan!.items) {
      const fields = Object.fromEntries(ITEM_FIELDS.filter((k) => raw[k] !== undefined && raw[k] !== null).map((k) => [k, raw[k]]));
      try { await api.createItem({ source: "import", ...fields }); ok += 1; }
      catch (e) { out.push(`${raw.kind}: "${raw.statement.slice(0, 60)}" - ${e instanceof Error ? e.message : String(e)}`); }
    }
    if (plan!.completeness && Object.keys(plan!.completeness).length) await api.setCompleteness(plan!.completeness);
    setReport([`Added ${ok} of ${plan!.items.length} (not confirmed yet).`, ...out]);
    setPlan(null);
    onDone();
  });
  return (
    <Card title="Load a profile file">
      <p className="muted">A profile file you reviewed (for example the one built from your resumes). Everything comes in as
        not confirmed; anything already in your profile is skipped. Confirm items afterwards.</p>
      <input type="file" accept=".json,application/json" onChange={onFile} />
      {plan && (
        <div className="row">
          <span>{plan.name}: {plan.items.length} new, {plan.dupes} already present{plan.completeness && Object.keys(plan.completeness).length ? ", plus completeness flags" : ""}.</span>
          <button className="primary" disabled={act.busy || plan.items.length === 0} onClick={run}>Import {plan.items.length}</button>
        </div>)}
      {report.length > 0 && <ul className="compact">{report.map((r, i) => <li key={i} className={i ? "warn" : ""}>{r}</li>)}</ul>}
      <ErrorBox error={act.error} />
    </Card>
  );
}

// ----- inferred claims inbox -----

function Claims() {
  const claims = useLive(() => api.claims("open"), [], ["evaluation_done", "profile_changed"]);
  const act = useAction();
  const rows = claims.data ?? [];
  return (
    <Card title={`Experience the AI thinks you may have (${rows.length})`}>
      <p className="muted">The model's guesses about experience you probably have but did not state. They never count until you
        accept one, and an accepted one still needs your confirmation.</p>
      {rows.length === 0 ? <p className="muted">Nothing suggested.</p> : (
        <ul className="items">{rows.map((c) => (
          <li key={c.id}><div className="item-line"><Badge value={c.kind} /><span className="statement">{c.statement}</span>
            <span className="actions">
              <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.acceptClaim(c.id, {}); claims.reload(); })}>Accept</button>
              <button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.rejectClaim(c.id); claims.reload(); })}>Reject</button>
            </span></div>{c.rationale && <div className="muted">{c.rationale}</div>}</li>))}</ul>)}
      <ErrorBox error={act.error ?? claims.error} />
    </Card>
  );
}

// ----- resumes -----

function Resumes() {
  const list = useLive(api.resumes, [], ["profile_changed"]);
  const act = useAction();
  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) act.run(async () => { await api.uploadResume(f.name, new Uint8Array(await f.arrayBuffer())); list.reload(); });
  };
  return (
    <Card title="Resumes" actions={<input type="file" accept=".pdf,.docx,.md,.txt" onChange={onFile} disabled={act.busy} />}>
      <p className="muted">Stored byte for byte, for your reference. They do not create profile items on their own.</p>
      {(list.data ?? []).length === 0 ? <p className="muted">No resumes uploaded.</p> : (
        <table><tbody>{list.data!.map((r) => (
          <tr key={r.id}><td>{r.original_filename}</td><td className="muted">{Math.round(r.bytes / 1024)} KB · {r.text_chars ? `${r.text_chars} chars of text` : "no text extracted"}</td>
            <td className="muted">{ago(r.uploaded_at_ms)} ago</td>
            <td><ConfirmButton label="Delete" disabled={act.busy} onConfirm={() => act.run(async () => { await api.deleteResume(r.id); list.reload(); })} /></td></tr>))}</tbody></table>)}
      <ErrorBox error={act.error ?? list.error} />
    </Card>
  );
}

function title(s: string): string {
  const t: Record<string, string> = { role: "Roles", achievement: "Achievements", skill: "Skills", cert: "Certifications", education: "Education" };
  return t[s] ?? s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function fmtAttrs(a: Record<string, unknown>): string {
  return Object.entries(a).filter(([k]) => k !== "type" && k !== "verify_note").map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(", ");
}

function describe(type: string, a: Record<string, unknown>): string {
  return `${title(type)}: ${fmtAttrs(a)}`;
}
