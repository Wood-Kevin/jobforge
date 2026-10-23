// First-run setup (P1.8b): four steps in plain language. Shown on start until the first three are
// done; reachable later from the menu. Everything here can be changed again in Preferences/Profile.
import { api } from "../api";
import { CompaniesCard, ModelCard, SearchFormCard } from "../settings";
import { useLive } from "../ui";
import { Claims, Import, Resumes } from "./Profile";

export default function Setup({ onDone }: { onDone: () => void }) {
  const s = useLive(api.setupStatus, [], ["search_profile_saved", "employer_enabled", "employer_disabled", "model_config_enabled",
    "model_config_tested", "profile_changed"], 15_000);
  const items = useLive(api.items, [], ["profile_changed"]);
  const st = s.data;
  const step = (n: number, title: string, done: boolean | undefined, hint: string) => (
    <div className="step"><span className={done ? "step-num done" : "step-num"}>{done ? "✓" : n}</span>
      <div><strong>{title}</strong><div className="muted">{hint}</div></div></div>);
  return (
    <div className="page">
      <div className="card hero">
        <h2>Set up JobForge</h2>
        <p>JobForge keeps checking the companies you choose, skips jobs that don't match what you want, and reads the rest
          against your experience. Four steps; you can change any of them later.</p>
        {st?.complete && <p className="ok-text">All set. <button className="primary" onClick={onDone}>See your jobs</button></p>}
      </div>
      {step(1, "Companies to watch", st?.employers.done, st?.employers.done ? `${st.employers.enabled} companies` : "Paste careers-page links or use your job-scanner watchlist.")}
      <CompaniesCard compact />
      {step(2, "What you're looking for", st?.search_profile.done, st?.search_profile.done ? "Saved" : "Job titles, where you live, and pay.")}
      <SearchFormCard onSaved={s.reload} />
      {step(3, "AI model", st?.model.done, st?.model.done ? "Turned on" : "Check that LM Studio is running with the model.")}
      <ModelCard onReady={s.reload} />
      {step(4, "Your experience", st?.profile.done, st?.profile.done
        ? `${st.profile.items} items, ${st.profile.facts} eligibility facts (${st.profile.facts_verified} confirmed). Confirm them in Profile.`
        : "Upload your resume and press Suggest items, then check what the AI found.")}
      <Resumes />
      <Claims fromResumeOnly />
      <details className="muted"><summary>Already have a prepared profile file?</summary>
        <Import existing={items.data ?? []} onDone={() => { items.reload(); s.reload(); }} /></details>
      {st?.complete && <p><button className="primary" onClick={onDone}>Done: see your jobs</button></p>}
    </div>
  );
}
