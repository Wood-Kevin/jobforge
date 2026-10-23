// The UI's only bridge to the agent. The host adds the IPC token; the webview never sees it (SPEC D11).
// The window makes no network requests of its own (CSP connect-src allows only Tauri IPC; a test
// in agent/tests/test_views.py fails on fetch/XMLHttpRequest/WebSocket in desktop/src).
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type Supervisor = {
  state: "starting" | "running" | "not_responding" | "stopped_db_newer" | "stopped_elevated" | "failing" | "exiting";
  detail: string | null;
  agent_pid: number | null;
};

export type ChangeEvent = { id: number; type: string; job_id: number | null; employer_id: number | null; created_at_ms: number };

/** An error answer from the agent, with its own message (e.g. a 422 naming the bad field). */
export class AgentError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function toAgentError(e: unknown): AgentError {
  const text = String(e);
  // Before the agent has written ipc.json (startup) or while it restarts, the host cannot reach it.
  if (/ipc\.json|connection refused|actively refused/i.test(text)) return new AgentError(0, "The agent is not reachable yet (starting or restarting).");
  try {
    const v = JSON.parse(text) as { status?: number; error?: unknown };
    const msg = typeof v.error === "string" ? v.error : JSON.stringify(v.error);
    if (v.status === 0 && /ipc\.json|refused/i.test(msg)) return new AgentError(0, "The agent is not reachable yet (starting or restarting).");
    return new AgentError(v.status ?? 0, msg);
  } catch {
    return new AgentError(0, text);
  }
}

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export async function agentCall<T>(method: Method, path: string, body?: unknown): Promise<T> {
  try {
    return await invoke<T>("agent_call", { method, path, body: body === undefined ? null : body });
  } catch (e) {
    throw toAgentError(e);
  }
}

export async function agentUpload<T>(path: string, bytes: Uint8Array): Promise<T> {
  try {
    return await invoke<T>("agent_upload", { path, bytes: Array.from(bytes) });
  } catch (e) {
    throw toAgentError(e);
  }
}

/** Opens a posting link in the system browser (host checks: http/https only). */
export function openUrl(url: string): Promise<void> {
  return invoke("open_url", { url });
}

export function supervisorStatus(): Promise<Supervisor> {
  return invoke<Supervisor>("supervisor_status");
}

export function onAgentEvent(handler: (e: ChangeEvent) => void): Promise<UnlistenFn> {
  return listen<ChangeEvent>("agent-event", (msg) => handler(msg.payload));
}

export function onSupervisorChange(handler: (s: Supervisor) => void): Promise<UnlistenFn> {
  return listen<Supervisor>("supervisor", (msg) => handler(msg.payload));
}

// ----- restore screen (host commands, not agent routes: the agent is stopped when they are used) -----

export type BackupList = { current_schema: number | null; backups: { name: string; bytes: number; modified_ms: number; schema: number | null }[] };

export async function listBackups(): Promise<BackupList> {
  return await invoke<BackupList>("list_backups");
}

/** Runs the agent's --restore (which keeps today's data as a before-restore backup first), then restarts the agent. */
export async function restoreBackup(name: string): Promise<string> {
  try {
    return await invoke<string>("restore_backup", { name });
  } catch (e) {
    throw new AgentError(0, String(e));
  }
}
