// Answer a question in place, on "Needs your input" and the job page, instead of going to Profile (Kevin,
// 2026-10-24). An experience question opens an empty item form under the posting's requirement: you
// describe what you did in your own words. It is never prefilled with the posting's sentence, which would
// put the employer's wording in your profile (item 55 began as "experience with AI tools" and was matched
// to requirements it did not show). An eligibility question opens that fact's typed form, or offers to
// confirm the fact you already declared. Saving adds a not-confirmed item; Confirm is your own click.
// There is no "No" answer: recording an absence would change how matching treats the requirement, which
// waits for Evaluation calibration 1 (DEFERRED_COMPLEXITY.md).
// A line the model read as an eligibility condition but the code did not type (`unrecognized`) is not an
// experience question (Company N, 2026-10-24): it offers the fact form instead (residency, hours and
// availability, travel...; guessed from the wording, changeable) and says the job updates only once the
// reading fix (cases E1-E2) is approved.
import { useState } from "react";
import { api, type Item, type Schema } from "../api";
import { factSummary } from "../facts";
import { plain } from "../labels";
import { ErrorBox, useAction } from "../ui";
import { FactForm, ItemForm } from "./Profile";

const KINDS: [string, string][] = [["skill", "Skill"], ["role", "Job or role"], ["achievement", "Achievement"], ["cert", "Certification"],
  ["education", "Education"]];

export type Question = { kind: string; type?: string; requirement_id?: number; question: string; unrecognized?: string | null };

const CONDITION: Record<string, string> = {
  work_authorization: "where or when you can work", residency: "where you live", travel: "travel", physical: "a physical requirement",
  sponsorship: "visa sponsorship", license: "a license", clearance: "a security clearance", program_status: "a program status",
  availability: "your hours and availability",
};

// Which fact a misread line is most likely about. The model's own type is often wrong for these (Company N's
// location lines were typed work_authorization), so the wording decides first.
const HOURS = /on-?call|weekends?|holidays?|\bshifts?\b|shift work|24\s*[x/]\s*7|overnight|business hours|working hours|\b\d{1,2}\s*(a|p)\.?m?\.?\s*[-–]/i;
const PLACE = /located|based in|resid|time ?zone|\b(eastern|central|mountain|pacific)\b|remote in/i;
export function guessFactType(quote: string, modelType: string): string {
  if (HOURS.test(quote)) return "availability";
  if (PLACE.test(quote)) return "residency";
  return modelType;
}

export function Answer({ q, schema, items }: { q: Question; schema: Schema | null; items: Item[] }) {
  const act = useAction();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("skill");
  const [saved, setSaved] = useState<number | null>(null);
  const fact = q.kind === "eligibility" && q.type ? items.find((i) => i.kind === "eligibility" && i.attributes.type === q.type) : undefined;
  const fields = q.kind === "eligibility" && q.type ? schema?.eligibility[q.type] : undefined;
  const confirm = (id: number) => act.run(async () => { await api.verifyItem(id, null); setSaved(null); setOpen(false); });

  if (q.kind !== "experience" && q.kind !== "eligibility") return null; // a reading conflict has nothing to answer
  if (q.kind === "experience" && q.unrecognized) return <Misread q={q} schema={schema} items={items} />;
  if (saved !== null) return (
    <div className="answer"><span>Added to your profile (not confirmed). JobForge re-checks your jobs with it; this page
      updates when that's done.</span> <button className="small primary" disabled={act.busy} onClick={() => confirm(saved)}>Confirm it</button>
      <ErrorBox error={act.error} /></div>);
  // A declared but unconfirmed fact only needs your confirmation.
  if (fact && fact.status !== "verified" && !open) return (
    <div className="answer"><span className="muted">You said: {factSummary(fact.attributes.type as string, fact.attributes)}.</span>{" "}
      <button className="small primary" disabled={act.busy} onClick={() => confirm(fact.id)}>Confirm it</button>{" "}
      <button className="small" onClick={() => setOpen(true)}>Change it</button><ErrorBox error={act.error} /></div>);
  if (!open) return <button className="small" onClick={() => setOpen(true)}>Answer here</button>;
  return (
    <div className="answer form">
      {q.kind === "experience" ? (<>
        <label>Add as{" "}<select value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <ItemForm key={kind} kind={kind} onSaved={setSaved} onCancel={() => setOpen(false)}
          placeholder="What you actually did that covers this, in your own words (not the posting's)" />
      </>) : fields ? (
        <FactForm type={q.type!} fields={fields} item={fact} onSaved={setSaved} onCancel={() => setOpen(false)} />
      ) : <p className="muted">Loading the form...</p>}
    </div>);
}

// A misread eligibility line: record the fact it asks about (not experience). JobForge checks this line
// against the fact only after the reading fix is approved, so the job does not change yet; the fact is
// ready for it and for every other posting. Confirm stays your own click on the fact.
function Misread({ q, schema, items }: { q: Question; schema: Schema | null; items: Item[] }) {
  const act = useAction();
  const [type, setType] = useState(() => guessFactType(q.question, q.unrecognized!));
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<number | null>(null);
  const fact = items.find((i) => i.kind === "eligibility" && i.attributes.type === type);
  const fields = schema?.eligibility[type];
  const later = "This job won't change until JobForge reads lines like this one as facts (a fix waiting for your approval).";
  if (saved !== null) return (
    <div className="answer"><span className="muted">Saved to your facts (not confirmed). {later}</span>{" "}
      <button className="small primary" disabled={act.busy} onClick={() => act.run(async () => { await api.verifyItem(saved, null); setSaved(null); })}>
        Confirm it</button><ErrorBox error={act.error} /></div>);
  return (
    <div className="answer">
      <span className="muted">This is about {CONDITION[type] ?? "eligibility"}, not experience. Answer it as a fact: </span>
      <select value={type} onChange={(e) => { setType(e.target.value); setOpen(false); }}>
        {Object.keys(schema?.eligibility ?? { [type]: {} }).map((t) => <option key={t} value={t}>{plain(t)}</option>)}</select>{" "}
      {!open && <button className="small" onClick={() => setOpen(true)}>{fact ? "Update it" : "Add it"}</button>}
      {fact && !open && <span className="muted"> You said: {factSummary(type, fact.attributes)}{fact.status !== "verified" && " (not confirmed)"}</span>}
      {open && fields && <div className="form"><FactForm key={type} type={type} fields={fields} item={fact}
        onSaved={setSaved} onCancel={() => setOpen(false)} /></div>}
      <div className="muted">{later}</div>
    </div>);
}
