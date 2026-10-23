// Shared hooks and small components for the window.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AgentError, onAgentEvent, onSupervisorChange, type ChangeEvent } from "./agent";
import { plain } from "./labels";

/** Loads data, and loads it again when an agent event of one of `types` arrives (live refresh,
 * ARCHITECTURE 5: the host relays the agent's events; nothing polls the database). `types` empty =
 * any event. A burst of events (a re-filter) causes one reload, not hundreds. */
export function useLive<T>(load: () => Promise<T>, deps: unknown[], types: string[] = [], intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timer = useRef<number | null>(null);
  const run = useCallback(async () => {
    try {
      setData(await load());
      setError(null);
    } catch (e) {
      setError(e instanceof AgentError ? e.message : String(e));
    } finally {
      setLoading(false);
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
  APPLY: "ok", STRETCH: "info", REVIEW: "warn", SKIP: "bad",
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

export function Badge({ value, title }: { value: string | null | undefined; title?: string }) {
  if (!value) return null;
  return <span className={`badge ${TONE[value] ?? "muted"}`} title={title}>{plain(value)}</span>;
}

export function ago(ms: number | null | undefined): string {
  if (!ms) return "-";
  const s = Math.round((Date.now() - ms) / 1000);
  return age(s);
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
