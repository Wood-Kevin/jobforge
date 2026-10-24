//! Agent supervision (ARCHITECTURE 3.2): attach or spawn, watchdog, exit-code handling, exit.
//!
//! Rules from spike P1.0d, each covered by a unit test or the lifecycle script:
//! - never spawn a second agent while the last child is still running (it is hung, not dead);
//! - exit code 3 = another agent holds the lock: attach, do not respawn;
//! - exit code 4 = database newer than this agent: stop and say so, do not respawn;
//! - exit code 5 = agent refused to run elevated: stop and say so, do not respawn;
//! - anything else: respawn, at most 3 times in 5 minutes, then report failing.

use std::fs::OpenOptions;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

use crate::paths::{data_root, host_log, ipc_file, logs_dir};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
pub const HEALTH_INTERVAL: Duration = Duration::from_secs(5);
const STARTUP_WAIT: Duration = Duration::from_secs(20);
const RESTART_WINDOW: Duration = Duration::from_secs(300);
const MAX_RESTARTS: usize = 3;
/// Must match jobforge_agent.PROTOCOL_VERSION.
pub const PROTOCOL_VERSION: u64 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Starting,
    Running,
    NotResponding,
    StoppedDbNewer,
    StoppedElevated,
    Failing,
    Exiting,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Status {
    pub state: State,
    pub detail: Option<String>,
    pub agent_pid: Option<u32>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum ExitAction {
    AlreadyRunning,
    DbNewer,
    Elevated,
    Respawn,
}

pub fn classify_exit(code: Option<i32>) -> ExitAction {
    match code {
        Some(3) => ExitAction::AlreadyRunning,
        Some(4) => ExitAction::DbNewer,
        Some(5) => ExitAction::Elevated,
        _ => ExitAction::Respawn,
    }
}

#[derive(Debug, Serialize)]
pub struct BackupFile {
    pub name: String,
    pub bytes: u64,
    pub modified_ms: u64,
    /// Database schema version (`PRAGMA user_version`), from the file header.
    pub schema: Option<u32>,
}

fn backups_dir() -> PathBuf {
    data_root().join("backups")
}

/// SQLite keeps `PRAGMA user_version` big-endian at byte offset 60 of the file header. The agent
/// checks again before restoring; this is only for the list the window shows.
pub fn sqlite_user_version(path: &std::path::Path) -> Option<u32> {
    use std::io::Read;
    let mut head = [0u8; 64];
    std::fs::File::open(path).ok()?.read_exact(&mut head).ok()?;
    if &head[..16] != b"SQLite format 3\0" {
        return None;
    }
    Some(u32::from_be_bytes([head[60], head[61], head[62], head[63]]))
}

/// Schema version of the live database (None if it does not exist yet).
pub fn current_schema() -> Option<u32> {
    sqlite_user_version(&data_root().join("data").join("jobforge.db"))
}

/// Backup files, newest first.
pub fn list_backups() -> Vec<BackupFile> {
    let mut out: Vec<BackupFile> = std::fs::read_dir(backups_dir())
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter(|e| e.path().extension().is_some_and(|x| x == "db"))
                .filter_map(|e| {
                    let meta = e.metadata().ok()?;
                    let modified_ms = meta.modified().ok()?.duration_since(std::time::UNIX_EPOCH).ok()?.as_millis() as u64;
                    Some(BackupFile { name: e.file_name().to_string_lossy().into_owned(), bytes: meta.len(), modified_ms, schema: sqlite_user_version(&e.path()) })
                })
                .collect()
        })
        .unwrap_or_default();
    out.sort_by(|a, b| b.modified_ms.cmp(&a.modified_ms));
    out
}

#[derive(Deserialize)]
struct IpcInfo {
    port: u16,
    token: String,
    #[serde(default)]
    pid: Option<u64>,
}

fn read_ipc() -> Result<IpcInfo, String> {
    let raw = std::fs::read_to_string(ipc_file()).map_err(|e| format!("ipc.json: {e}"))?;
    serde_json::from_str(&raw).map_err(|e| format!("ipc.json: {e}"))
}

/// Whether a health answer comes from the agent that wrote ipc.json.
fn pid_matches(ipc_pid: Option<u64>, health_pid: Option<u64>) -> bool {
    matches!((ipc_pid, health_pid), (Some(a), Some(b)) if a == b)
}

/// One authenticated request to the agent. The token is read fresh each time because a
/// restarted agent writes a new one.
pub fn agent_request(method: &str, path: &str, timeout: Duration) -> Result<serde_json::Value, String> {
    agent_send(method, path, Body::None, timeout)
}

/// What a request carries: nothing, a JSON value, or raw bytes (resume upload: the agent stores
/// exactly what it receives, P1.5).
pub enum Body {
    None,
    Json(serde_json::Value),
    Bytes(Vec<u8>),
}

/// One request to the agent on 127.0.0.1 (the only host this process ever contacts). A 4xx/5xx
/// answer comes back as `Err` holding `{"status": code, "error": <agent's message>}` as JSON text,
/// so the window can show the agent's own validation message instead of "status code 422".
pub fn agent_send(method: &str, path: &str, body: Body, timeout: Duration) -> Result<serde_json::Value, String> {
    let info = read_ipc()?;
    let url = format!("http://127.0.0.1:{}{}", info.port, path);
    let agent = ureq::AgentBuilder::new().timeout(timeout).build();
    let auth = format!("Bearer {}", info.token);
    if !matches!(method, "GET" | "POST" | "PUT" | "PATCH" | "DELETE") {
        return Err(format!("method {method} not allowed"));
    }
    let req = agent.request(method, &url).set("Authorization", &auth);
    let resp = match body {
        Body::None if method == "GET" || method == "DELETE" => req.call(),
        Body::None => req.send_string(""),
        Body::Json(v) => req.send_json(v),
        Body::Bytes(b) => req.set("Content-Type", "application/octet-stream").send_bytes(&b),
    };
    match resp {
        Ok(r) => r.into_json::<serde_json::Value>().map_err(|e| e.to_string()),
        Err(ureq::Error::Status(code, r)) => {
            let text = r.into_string().unwrap_or_default();
            let error = serde_json::from_str::<serde_json::Value>(&text)
                .ok()
                .and_then(|v| v.get("error").cloned())
                .unwrap_or(serde_json::Value::String(text));
            Err(serde_json::json!({"status": code, "error": error}).to_string())
        }
        Err(e) => Err(serde_json::json!({"status": 0, "error": e.to_string()}).to_string()),
    }
}

/// Returns the open SSE response for /v1/events, resuming after `last_id` when given.
pub fn open_event_stream(last_id: Option<u64>) -> Result<ureq::Response, String> {
    let info = read_ipc()?;
    let url = format!("http://127.0.0.1:{}/v1/events", info.port);
    // The agent sends a keepalive every 15 s; 40 s without bytes means the agent is gone.
    let agent = ureq::AgentBuilder::new().timeout_connect(Duration::from_secs(3)).timeout_read(Duration::from_secs(40)).build();
    let mut req = agent.get(&url).set("Authorization", &format!("Bearer {}", info.token));
    if let Some(id) = last_id {
        req = req.set("Last-Event-ID", &id.to_string());
    }
    req.call().map_err(|e| e.to_string())
}

fn health() -> Result<serde_json::Value, String> {
    agent_request("GET", "/v1/health", Duration::from_secs(3))
}

pub struct Supervisor {
    app: AppHandle,
    child: Mutex<Option<Child>>,
    status: Mutex<Status>,
    restarts: Mutex<Vec<Instant>>,
    exiting: AtomicBool,
    on_status: Mutex<Option<Box<dyn Fn(&Status) + Send>>>,
}

impl Supervisor {
    pub fn new(app: AppHandle) -> Self {
        Self {
            app,
            child: Mutex::new(None),
            status: Mutex::new(Status { state: State::Starting, detail: None, agent_pid: None }),
            restarts: Mutex::new(Vec::new()),
            exiting: AtomicBool::new(false),
            on_status: Mutex::new(None),
        }
    }

    pub fn status(&self) -> Status {
        self.status.lock().unwrap().clone()
    }

    pub fn is_exiting(&self) -> bool {
        self.exiting.load(Ordering::SeqCst)
    }

    /// Tray text follows status changes.
    pub fn set_status_listener(&self, f: Box<dyn Fn(&Status) + Send>) {
        *self.on_status.lock().unwrap() = Some(f);
    }

    fn set_status(&self, state: State, detail: Option<String>, agent_pid: Option<u32>) {
        let new = Status { state, detail, agent_pid };
        let changed = {
            let mut cur = self.status.lock().unwrap();
            let changed = *cur != new;
            *cur = new.clone();
            changed
        };
        if changed {
            host_log(&format!("status {:?} detail={:?} pid={:?}", new.state, new.detail, new.agent_pid));
            let _ = self.app.emit("supervisor", &new);
            if let Some(f) = self.on_status.lock().unwrap().as_ref() {
                f(&new);
            }
        }
    }

    fn agent_command(&self) -> Result<Command, String> {
        // Development override: e.g. JOBFORGE_AGENT_EXE=<venv>\python.exe, JOBFORGE_AGENT_ARGS="-m jobforge_agent".
        if let Ok(exe) = std::env::var("JOBFORGE_AGENT_EXE") {
            let mut cmd = Command::new(exe);
            if let Ok(args) = std::env::var("JOBFORGE_AGENT_ARGS") {
                cmd.args(args.split_whitespace());
            }
            return Ok(cmd);
        }
        let exe: PathBuf = self.app.path().resource_dir().map_err(|e| e.to_string())?.join("agent").join("jobforge-agent.exe");
        Ok(Command::new(exe))
    }

    /// Start a detached agent unless the last child is still alive.
    fn spawn(&self) -> Result<(), String> {
        let mut slot = self.child.lock().unwrap();
        if let Some(child) = slot.as_mut() {
            if let Ok(None) = child.try_wait() {
                return Err(format!("agent pid {} still running but not answering; not starting another", child.id()));
            }
        }
        let _ = std::fs::create_dir_all(logs_dir());
        let console_log = || -> Stdio {
            OpenOptions::new().create(true).append(true).open(logs_dir().join("agent-console.log")).map(Stdio::from).unwrap_or_else(|_| Stdio::null())
        };
        let flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
        // Breakaway lets the agent outlive a host started inside a job object; some launchers forbid it.
        let child = self
            .agent_command()?
            .creation_flags(flags | CREATE_BREAKAWAY_FROM_JOB)
            .stdout(console_log())
            .stderr(console_log())
            .spawn()
            .or_else(|_| self.agent_command().and_then(|mut c| c.creation_flags(flags).stdout(console_log()).stderr(console_log()).spawn().map_err(|e| e.to_string())))
            .map_err(|e| format!("spawn failed: {e}"))?;
        host_log(&format!("spawned agent pid={} data_root={}", child.id(), data_root().display()));
        *slot = Some(child);
        Ok(())
    }

    /// Exit code of our last child, if it has exited.
    fn child_exit(&self) -> Option<Option<i32>> {
        let mut slot = self.child.lock().unwrap();
        let child = slot.as_mut()?;
        match child.try_wait() {
            Ok(Some(status)) => Some(status.code()),
            _ => None,
        }
    }

    fn child_running(&self) -> bool {
        let mut slot = self.child.lock().unwrap();
        matches!(slot.as_mut().map(|c| c.try_wait()), Some(Ok(None)))
    }

    fn running_from_health(&self, h: &serde_json::Value) -> bool {
        // ipc.json survives an agent crash; the freed port can then be bound by another process.
        // Our agent reports the pid it wrote into ipc.json (readable only by this user), so a
        // different pid means the port is not our agent's: never show it as running or send it
        // anything more (audit 2026-10-24).
        let expected = read_ipc().ok().and_then(|i| i.pid);
        if !pid_matches(expected, h["pid"].as_u64()) {
            host_log(&format!("health pid {:?} != ipc.json pid {expected:?}; not our agent", h["pid"].as_u64()));
            self.set_status(State::Starting, Some("the agent's port answers for another process".into()), None);
            return false;
        }
        let protocol = h["protocol_version"].as_u64().unwrap_or(0);
        if protocol != PROTOCOL_VERSION {
            // A stale agent from an older install: ask it to stop; the watchdog starts ours.
            host_log(&format!("agent protocol {protocol} != {PROTOCOL_VERSION}; asking it to stop"));
            let _ = agent_request("POST", "/v1/shutdown?reason=protocol_mismatch", Duration::from_secs(3));
            self.set_status(State::Starting, Some("replacing an agent from another version".into()), None);
            return false;
        }
        self.set_status(State::Running, None, h["pid"].as_u64().map(|p| p as u32));
        true
    }

    /// Attach to a running agent, or start one and wait for it.
    pub fn ensure(&self) {
        if let Ok(h) = health() {
            if self.running_from_health(&h) {
                host_log("attached to running agent");
                return;
            }
        }
        self.start_and_wait();
    }

    fn start_and_wait(&self) {
        self.set_status(State::Starting, None, None);
        if let Err(e) = self.spawn() {
            host_log(&e);
            self.set_status(State::NotResponding, Some(e), None);
            return;
        }
        let deadline = Instant::now() + STARTUP_WAIT;
        while Instant::now() < deadline && !self.is_exiting() {
            std::thread::sleep(Duration::from_millis(300));
            if let Ok(h) = health() {
                if self.running_from_health(&h) {
                    return;
                }
            }
            if let Some(code) = self.child_exit() {
                self.handle_exit(code);
                return;
            }
        }
        self.set_status(State::NotResponding, Some("agent did not answer within 20 s".into()), None);
    }

    fn handle_exit(&self, code: Option<i32>) {
        host_log(&format!("agent exited with code {code:?}"));
        match classify_exit(code) {
            ExitAction::AlreadyRunning => {
                // Another agent holds the lock; it should answer once its ipc.json is written.
                self.set_status(State::Starting, Some("another agent is starting".into()), None);
            }
            ExitAction::DbNewer => self.set_status(State::StoppedDbNewer, None, None),
            ExitAction::Elevated => self.set_status(State::StoppedElevated, None, None),
            ExitAction::Respawn => self.set_status(State::NotResponding, Some(format!("agent exited ({code:?})")), None),
        }
    }

    fn restart_allowed(&self) -> bool {
        let mut r = self.restarts.lock().unwrap();
        r.retain(|t| t.elapsed() < RESTART_WINDOW);
        if r.len() >= MAX_RESTARTS {
            return false;
        }
        r.push(Instant::now());
        true
    }

    /// Watchdog loop; runs on its own thread until exit.
    pub fn watch(&self) {
        self.ensure();
        let mut failures = 0u32;
        while !self.is_exiting() {
            std::thread::sleep(HEALTH_INTERVAL);
            if self.is_exiting() {
                break;
            }
            match health() {
                Ok(h) => {
                    failures = 0;
                    self.running_from_health(&h);
                }
                Err(_) => {
                    failures += 1;
                    if failures < 2 {
                        continue;
                    }
                    failures = 0;
                    if matches!(self.status().state, State::StoppedDbNewer | State::StoppedElevated) {
                        continue; // terminal until the user acts; never respawn
                    }
                    if self.child_running() {
                        self.set_status(State::NotResponding, Some("agent alive but not answering".into()), None);
                        continue;
                    }
                    if let Some(code) = self.child_exit() {
                        if matches!(classify_exit(code), ExitAction::DbNewer | ExitAction::Elevated) {
                            self.handle_exit(code);
                            continue;
                        }
                    }
                    if self.restart_allowed() {
                        host_log("watchdog restarting agent");
                        self.start_and_wait();
                    } else {
                        self.set_status(State::Failing, Some("3 restarts in 5 minutes".into()), None);
                    }
                }
            }
        }
    }

    /// The restore screen (ARCHITECTURE 7.2): offered only while the agent is stopped because the
    /// database is newer than this version. Runs the agent's own `--restore`, which first keeps the
    /// current database as `before-restore-*.db` and refuses a backup newer than itself, then starts
    /// the agent again. Returns the agent's output, or its error text.
    pub fn restore_backup(&self, name: &str) -> Result<String, String> {
        if self.status().state != State::StoppedDbNewer {
            return Err("Restoring a backup is only offered while JobForge is stopped because its data is from a newer version.".into());
        }
        if name.contains(['/', '\\', ':']) || name.contains("..") || !name.ends_with(".db") {
            return Err("not a backup file name".into());
        }
        let path = backups_dir().join(name);
        if !path.is_file() {
            return Err(format!("no backup named {name}"));
        }
        let out = self
            .agent_command()?
            .arg("--restore")
            .arg(&path)
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map_err(|e| format!("could not run the restore: {e}"))?;
        let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr)).trim().to_string();
        host_log(&format!("restore {name}: exit {:?}", out.status.code()));
        if !out.status.success() {
            return Err(format!("Restore failed: {text}"));
        }
        self.restarts.lock().unwrap().clear();
        self.start_and_wait();
        Ok(text)
    }

    /// Tray Exit and `JobForge.exe --exit`: stop the agent gracefully, then the host.
    pub fn shutdown_agent(&self) {
        self.exiting.store(true, Ordering::SeqCst);
        self.set_status(State::Exiting, None, None);
        let sent = agent_request("POST", "/v1/shutdown?reason=user_exit", Duration::from_secs(3)).is_ok();
        host_log(&format!("exit requested, shutdown sent ok={sent}"));
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline && health().is_ok() {
            std::thread::sleep(Duration::from_millis(250));
        }
        host_log(&format!("agent still answering after wait: {}", health().is_ok()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_agent_that_wrote_ipc_json_counts_as_running() {
        assert!(pid_matches(Some(4242), Some(4242)));
        assert!(!pid_matches(Some(4242), Some(9999))); // another process bound the freed port
        assert!(!pid_matches(Some(4242), None));
        assert!(!pid_matches(None, Some(4242)));
    }

    #[test]
    fn reads_user_version_from_the_sqlite_header() {
        let dir = std::env::temp_dir().join(format!("jf_hdr_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("x.db");
        let mut head = b"SQLite format 3\0".to_vec();
        head.resize(100, 0);
        head[60..64].copy_from_slice(&8u32.to_be_bytes());
        std::fs::write(&f, &head).unwrap();
        assert_eq!(sqlite_user_version(&f), Some(8));
        std::fs::write(&f, [b'x'; 100]).unwrap();
        assert_eq!(sqlite_user_version(&f), None);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn exit_codes_map_to_actions() {
        assert_eq!(classify_exit(Some(3)), ExitAction::AlreadyRunning);
        assert_eq!(classify_exit(Some(4)), ExitAction::DbNewer);
        assert_eq!(classify_exit(Some(5)), ExitAction::Elevated);
        assert_eq!(classify_exit(Some(0)), ExitAction::Respawn);
        assert_eq!(classify_exit(Some(1)), ExitAction::Respawn);
        assert_eq!(classify_exit(None), ExitAction::Respawn); // killed
    }

    #[test]
    fn status_serializes_snake_case_for_the_ui() {
        let s = Status { state: State::StoppedDbNewer, detail: None, agent_pid: Some(7) };
        assert_eq!(serde_json::to_value(&s).unwrap()["state"], "stopped_db_newer");
    }
}
