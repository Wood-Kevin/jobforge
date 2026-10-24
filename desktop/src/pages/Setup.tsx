// First-run setup (P1.8b): four steps in plain language. Shown on start until the first three are
// done; reachable later from Preferences. Everything here can be changed again in Preferences/Profile.
// One step at a time (UX redesign step 4): the four forms stacked on one page made a long scroll with every
// title shown twice. The step row on top shows what's done and jumps to any step; Back and Next walk them.
// It opens on the first step not done yet.
import { useState, type ReactNode } from "react";
import { api } from "../api";
import { icons } from "../icons";
import { CompaniesCard, ModelCard, SearchFormCard } from "../settings";
import { ErrorBox, PageHeader, useLive } from "../ui";
import { Claims, Import, Resumes } from "./Profile";

export default function Setup({ onDone }: { onDone: () => void }) {
  const s = useLive(api.setupStatus, [], ["search_profile_saved", "employer_enabled", "employer_disabled", "model_config_enabled",
    "model_config_tested", "profile_changed"], 15_000);
  const items = useLive(api.items, [], ["profile_changed"]);
  const st = s.data;
  const [picked, setPicked] = useState<number | null>(null);
  const steps: { title: string; done: boolean | undefined; hint: string; body: ReactNode }[] = [
    { title: "Companies to watch", done: st?.employers.done,
      hint: st?.employers.done ? `${st.employers.enabled.toLocaleString()} companies are already watched. Add any others you want.` : "Type company names or paste careers-page links.",
      body: <CompaniesCard compact /> },
    { title: "What you're looking for", done: st?.search_profile.done, hint: "Job titles, where you live, and pay.",
      body: <SearchFormCard onSaved={s.reload} /> },
    { title: "AI model", done: st?.model.done, hint: "JobForge reads postings with a model on this PC. Check that LM Studio is running with it.",
      body: <ModelCard onReady={s.reload} /> },
    { title: "Your experience", done: st?.profile.done,
      hint: st?.profile.done ? `${st.profile.items} items and ${st.profile.facts} facts so far (${st.profile.facts_verified} facts confirmed). You can add more in Your profile.`
        : "Upload your resume and press Suggest items, then check what the AI found.",
      body: <><Resumes /><Claims fromResumeOnly />
        <details className="advanced"><summary>Already have a prepared profile file?</summary>
          <Import existing={items.data ?? []} onDone={() => { items.reload(); s.reload(); }} /></details></> },
  ];
  // Until you pick a step, the first one not done (all done: the first).
  const firstOpen = steps.findIndex((x) => !x.done);
  const cur = picked ?? (st ? (firstOpen < 0 ? 0 : firstOpen) : 0);
  const step = steps[cur];
  const last = cur === steps.length - 1;

  return (
    <div className="page">
      <PageHeader title="Set up JobForge" sub="JobForge keeps checking thousands of companies' job boards, skips jobs that don't match what you want, and reads the rest against your experience. Four steps; you can change any of them later." />
      <ErrorBox error={s.error} />
      <div className="wizard" role="group" aria-label="Setup steps">
        {steps.map((x, i) => (
          <button key={x.title} className={i === cur ? "wizard-step current" : "wizard-step"} aria-current={i === cur ? "step" : undefined}
            onClick={() => setPicked(i)}>
            <span className={x.done ? "step-num done" : "step-num"}>{x.done ? icons.check : i + 1}</span>
            <span><strong>{x.title}</strong><span className="muted">{x.done ? "Done" : i === cur ? "In progress" : "To do"}</span></span>
          </button>))}
      </div>
      <p className="lead">{step.hint}</p>
      {step.body}
      <div className="row wizard-foot">
        {cur > 0 && <button onClick={() => setPicked(cur - 1)}>{icons.back}Back</button>}
        <span className="spacer" />
        {!last && <button className="primary" onClick={() => setPicked(cur + 1)}>Next: {steps[cur + 1].title}</button>}
        {last && (st?.complete
          ? <button className="primary" onClick={onDone}>Done: see your jobs</button>
          : <span className="muted">Finish the steps not marked done above to start seeing jobs.</span>)}
      </div>
    </div>
  );
}
