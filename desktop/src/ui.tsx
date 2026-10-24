// Shared hooks and small components for the window.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AgentError, onAgentEvent, onSupervisorChange, type ChangeEvent } from "./agent";
import { icons } from "./icons";
import { fitSegments, plain, whyLine } from "./labels";
import { latestGate } from "./latest";

/** Loads data, and loads it again when an agent event of one of `types` arrives (live refresh,
 * ARCHITECTURE 5: the host relays the agent's events; nothing polls the database). `types` empty =
 * any event. A burst of events (a re-filter) causes one reload, not hundreds. */
export function useLive<T>(load: () => Promise<T>, deps: unknown[], types: string[] = [], intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timer = useRef<number | null>(null);
  const gate = useRef(latestGate()).current; // an older, slower answer never replaces a newer one
  const run = useCallback(async () => {
    const ticket = gate.begin();
    try {
      const value = await load();
      if (!gate.isCurrent(ticket)) return;
      setData(value);
      setError(null);
    } catch (e) {
      if (!gate.isCurrent(ticket)) return;
      setError(e instanceof AgentError ? e.message : String(e));
    } finally {
      if (gate.isCurrent(ticket)) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    setLoading(true);
    run();
    const unlisten = onAgentEvent((e: ChangeEvent) => {
      if (types.length && !types.includes(e.type)) return;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(run, 400);
    });
    // The window can open before the agent answers (startup, restart): load again once it runs.
    const unSup = onSupervisorChange((s) => { if (s.state === "running") run(); });
    const poll = intervalMs ? window.setInterval(run, intervalMs) : null;
    return () => {
      unlisten.then((fn) => fn());
      unSup.then((fn) => fn());
      if (poll !== null) window.clearInterval(poll);
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run]);
  return { data, error, loading, reload: run };
}

/** Runs a user action and keeps its error visible (no silent failures). */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof AgentError ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, clear: () => setError(null) };
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

/** A page's title, one line on what the page is for, and the page's own actions (search, a link). */
export function PageHeader({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function Card({ title, children, actions }: { title?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

const TONE: Record<string, string> = {
  // Stretch is amber and Needs your input blue (visual foundation 2026-10-24): a blue Stretch read like a link.
  APPLY: "ok", STRETCH: "stretch", REVIEW: "info", SKIP: "bad",
  ELIGIBLE: "ok", UNKNOWN: "warn", BLOCKED: "bad",
  STRONG: "ok", COMPETITIVE: "ok", WEAK: "bad", INSUFFICIENT: "warn",
  HIGH: "ok", MEDIUM: "info", LOW: "bad",
  direct: "ok", transferable: "info", gap: "bad", unknown: "warn",
  verified: "ok", self_reported: "muted", none: "muted",
  confirmed: "ok", conflict: "warn", unverifiable: "bad",
  favorable: "ok", neutral: "muted", unfavorable: "bad",
  pass: "ok", reject: "bad", material: "bad", minor: "warn", hard: "bad", negotiable: "info", preferred: "muted",
  saved: "info", applied: "ok", interviewing: "ok", offer: "ok", rejected: "bad", withdrawn: "muted",
};

/** The badge tone of a value ("ok", "stretch", "info", "warn", "bad", "muted"). */
export const tone = (value: string | null | undefined) => (value && TONE[value]) || "muted";

const REC_ICON: Record<string, ReactNode> = { APPLY: icons.check, STRETCH: icons.stretch, REVIEW: icons.question, SKIP: icons.cross };

/** A recommendation with its icon, so it doesn't rest on color alone. `large` on the job page. */
export function RecBadge({ value, large = false }: { value: string | null | undefined; large?: boolean }) {
  if (!value) return null;
  return <span className={`badge rec ${tone(value)}${large ? " large" : ""}`}>{REC_ICON[value]}{plain(value)}</span>;
}

/** The fit counts as a bar (matched, related, gaps, unanswered); its label reads the same counts. */
export function FitBar({ counts, large = false }: { counts: Record<string, unknown> | null | undefined; large?: boolean }) {
  const segs = fitSegments(counts);
  if (!segs.length) return null;
  return (
    <span className={large ? "fitbar large" : "fitbar"} role="img" aria-label={whyLine(counts)}>
      {segs.map((s) => <span key={s.key} className={`seg ${s.key}`} style={{ flexGrow: s.n }} />)}
    </span>
  );
}

/** A short message at the bottom of the window, with an optional action (Undo). */
export function Toast({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const t = window.setTimeout(onClose, 8000);
    return () => window.clearTimeout(t);
  }, [onClose]);
  return <div className="toast" role="status">{children}</div>;
}

// `text` replaces the plain wording where one value means different things (opportunity factors).
export function Badge({ value, title, text }: { value: string | null | undefined; title?: string; text?: string }) {
  if (!value) return null;
  return <span className={`badge ${TONE[value] ?? "muted"}`} title={title}>{text ?? plain(value)}</span>;
}

export function ago(ms: number | null | undefined): string {
  if (!ms) return "-";
  const s = Math.round((Date.now() - ms) / 1000);
  return age(s);
}

/** "5m ago", or "unknown" without a time (the callers used to append " ago" to ago()'s "-"). */
export function since(ms: number | null | undefined): string {
  return ms ? `${ago(ms)} ago` : "unknown";
}

export function age(s: number | null | undefined): string {
  if (s === null || s === undefined) return "-";
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/** A destructive action needs a second click (no browser dialogs: the window has no dialog plugin). */
export function ConfirmButton({ label, onConfirm, disabled }: { label: string; onConfirm: () => void; disabled?: boolean }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(t);
  }, [armed]);
  return armed
    ? <button className="small danger" disabled={disabled} onClick={() => { setArmed(false); onConfirm(); }}>Confirm {label.toLowerCase()}</button>
    : <button className="small" disabled={disabled} onClick={() => setArmed(true)}>{label}</button>;
}
