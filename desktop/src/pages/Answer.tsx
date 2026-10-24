// Answer a question on "Needs your input" instead of going to Profile (Kevin, 2026-10-24). An
// experience question opens an empty item form under the posting's requirement: you describe what you
// did in your own words. It is never prefilled with the posting's sentence, which would
// put the employer's wording in your profile (item 55 began as "experience with AI tools" and was matched
// to requirements it did not show). An eligibility question opens that fact's typed form, or offers to
// confirm the fact you already declared. Saving adds a not-confirmed item; Confirm is your own click.
// There is no "No" answer: recording an absence would change how matching treats the requirement,
// which waits for Evaluation calibration 1 (DEFERRED_COMPLEXITY.md).
import { useState } from "react";
import { api, type Item, type Schema } from "../api";
import { ErrorBox, useAction } from "../ui";
import { FactForm, ItemForm } from "./Profile";

const KINDS: [string, string][] = [["skill", "Skill"], ["role", "Job or role"], ["achievement", "Achievement"], ["cert", "Certification"],
  ["education", "Education"]];

export type Question = { kind: string; type?: string; requirement_id?: number; question: string };

export function Answer({ q, schema, items }: { q: Question; schema: Schema | null; items: Item[] }) {
  const act = useAction();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("skill");
  const [saved, setSaved] = useState<number | null>(null);
  const fact = q.kind === "eligibility" && q.type ? items.find((i) => i.kind === "eligibility" && i.attributes.type === q.type) : undefined;
  const fields = q.kind === "eligibility" && q.type ? schema?.eligibility[q.type] : undefined;
  const confirm = (id: number) => act.run(async () => { await api.verifyItem(id, null); setSaved(null); setOpen(false); });

  if (q.kind !== "experience" && q.kind !== "eligibility") return null; // a reading conflict has nothing to answer
  if (saved !== null) return (
    <div className="answer"><span>Added to your profile (not confirmed). JobForge re-checks your jobs with it; this page
      updates when that's done.</span> <button className="small primary" disabled={act.busy} onClick={() => confirm(saved)}>Confirm it</button>
      <ErrorBox error={act.error} /></div>);
  // A declared but unconfirmed fact only needs your confirmation.
  if (fact && fact.status !== "verified" && !open) return (
    <div className="answer"><span className="muted">You declared: {fact.statement}.</span>{" "}
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
