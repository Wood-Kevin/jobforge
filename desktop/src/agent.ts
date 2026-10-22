// The UI's only bridge to the agent. The host adds the IPC token; the webview never sees it (SPEC D11).
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type Supervisor = {
  state: "starting" | "running" | "not_responding" | "stopped_db_newer" | "stopped_elevated" | "failing" | "exiting";
  detail: string | null;
  agent_pid: number | null;
};

export type Health = {
  version: string;
  protocol_version: number;
  pid: number;
  run_id: number;
  schema: number;
  elevated: boolean;
  discovery_paused: boolean;
  uptime_s: number;
  last_maintenance_error: string | null;
};

export type ChangeEvent = { id: number; type: string; job_id: number | null; employer_id: number | null; created_at_ms: number };

export function agentCall<T>(method: "GET" | "POST", path: string): Promise<T> {
  return invoke<T>("agent_call", { method, path });
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
