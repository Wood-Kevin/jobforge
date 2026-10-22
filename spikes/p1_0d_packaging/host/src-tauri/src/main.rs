// P1.0d packaging spike: minimal Tauri host. Tray, disposable window,
// attach-or-spawn of a detached Python agent, watchdog, autostart, exit.
// No business logic lives here; everything goes to the agent over IPC.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::Write;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Deserialize;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

static EXITING: AtomicBool = AtomicBool::new(false);
static RESTARTS: Mutex<Vec<Instant>> = Mutex::new(Vec::new());
// Last agent this host started. A child that is still running but not answering is hung:
// spawning another would only pile up hung agents (seen in the first spike run).
static CHILD: Mutex<Option<std::process::Child>> = Mutex::new(None);

#[derive(Deserialize)]
struct IpcInfo {
    port: u16,
    token: String,
}

fn data_dir() -> PathBuf {
    PathBuf::from(std::env::var("LOCALAPPDATA").expect("LOCALAPPDATA")).join("JobForgeSpikeData")
}

fn host_log(msg: &str) {
    let dir = data_dir().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("host.log")) {
        let ts = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0);
        let _ = writeln!(f, "{ts} pid={} {msg}", std::process::id());
    }
}

fn agent_request(method: &str, path: &str) -> Result<serde_json::Value, String> {
    let raw = std::fs::read_to_string(data_dir().join("run").join("ipc.json")).map_err(|e| format!("ipc.json: {e}"))?;
    let info: IpcInfo = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    let url = format!("http://127.0.0.1:{}{}", info.port, path);
    let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(3)).build();
    let auth = format!("Bearer {}", info.token);
    let resp = match method {
        "POST" => agent.post(&url).set("Authorization", &auth).send_string(""),
        _ => agent.get(&url).set("Authorization", &auth).call(),
    };
    resp.map_err(|e| e.to_string())?.into_json::<serde_json::Value>().map_err(|e| e.to_string())
}

fn agent_exe(app: &AppHandle) -> PathBuf {
    app.path().resource_dir().expect("resource dir").join("agent").join("jobforge-agent.exe")
}

fn spawn_agent(app: &AppHandle) {
    if let Some(child) = CHILD.lock().unwrap().as_mut() {
        if let Ok(None) = child.try_wait() {
            host_log(&format!("agent pid={} still running but not answering; not spawning another", child.id()));
            return;
        }
    }
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
    let exe = agent_exe(app);
    let logs = data_dir().join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let out = || {
        std::fs::OpenOptions::new().create(true).append(true).open(logs.join("agent-console.log"))
            .map(std::process::Stdio::from)
            .unwrap_or_else(|_| std::process::Stdio::null())
    };
    // Console-subsystem agent with a hidden console: bootloader failures exit and land in
    // agent-console.log instead of a modal dialog. Breakaway lets the agent outlive a host
    // started inside a job object; some launchers forbid it, so retry without the flag.
    let r = Command::new(&exe)
        .creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB)
        .stdout(out()).stderr(out())
        .spawn()
        .or_else(|_| Command::new(&exe).creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP).stdout(out()).stderr(out()).spawn());
    match r {
        Ok(child) => {
            host_log(&format!("spawned agent pid={} exe={}", child.id(), exe.display()));
            *CHILD.lock().unwrap() = Some(child);
        }
        Err(e) => host_log(&format!("spawn failed: {e} exe={}", exe.display())),
    }
}

/// Attach to a running agent, or start one and wait up to 15 s for it to answer.
fn ensure_agent(app: &AppHandle) -> bool {
    if agent_request("GET", "/v1/health").is_ok() {
        host_log("attached to running agent");
        return true;
    }
    spawn_agent(app);
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(500));
        if agent_request("GET", "/v1/health").is_ok() {
            return true;
        }
    }
    host_log("agent did not answer within 15 s");
    false
}

fn open_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
        return;
    }
    let _ = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("JobForge (packaging spike)")
        .inner_size(560.0, 420.0)
        .build();
}

/// Tray "Exit JobForge", also reachable as a second launch with --exit (single-instance forwards it).
fn quit_app(app: &AppHandle) {
    EXITING.store(true, Ordering::SeqCst);
    let r = agent_request("POST", "/v1/shutdown?reason=user_exit");
    host_log(&format!("exit requested, shutdown sent ok={}", r.is_ok()));
    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline && agent_request("GET", "/v1/health").is_ok() {
        std::thread::sleep(Duration::from_millis(250));
    }
    host_log(&format!("agent still answering after wait: {}", agent_request("GET", "/v1/health").is_ok()));
    app.exit(0);
}

#[tauri::command]
fn agent_call(method: String, path: String) -> Result<serde_json::Value, String> {
    if !path.starts_with("/v1/") {
        return Err("path must start with /v1/".into());
    }
    agent_request(&method, &path)
}

fn main() {
    let background = std::env::args().any(|a| a == "--background");
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if args.iter().any(|a| a == "--exit") {
                let app = app.clone();
                std::thread::spawn(move || quit_app(&app));
            } else {
                open_window(app)
            }
        }))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--background"])))
        .invoke_handler(tauri::generate_handler![agent_call])
        .setup(move |app| {
            let handle = app.handle().clone();
            // Spike-only test hook: same plugin calls as the tray item, without clicking the tray.
            let args: Vec<String> = std::env::args().collect();
            if let Some(i) = args.iter().position(|a| a == "--autostart") {
                let al = app.autolaunch();
                let r = if args.get(i + 1).map(String::as_str) == Some("on") { al.enable() } else { al.disable() };
                host_log(&format!("cli autostart {:?} -> {:?}, enabled={:?}", args.get(i + 1), r.is_ok(), al.is_enabled()));
                std::process::exit(0);
            }
            host_log(&format!("host start background={background}"));

            let status = MenuItem::with_id(app, "status", "Status: starting", false, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "Pause discovery", true, None::<&str>)?;
            let autostart_on = app.autolaunch().is_enabled().unwrap_or(false);
            let autostart = CheckMenuItem::with_id(app, "autostart", "Start at sign-in", true, autostart_on, None::<&str>)?;
            let open = MenuItem::with_id(app, "open", "Open JobForge", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Exit JobForge", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(app)?;
            let menu = Menu::with_items(app, &[&open, &status, &pause, &autostart, &sep, &quit])?;

            let pause_item = pause.clone();
            let autostart_item = autostart.clone();
            TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("JobForge")
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "open" => open_window(app),
                    "pause" => {
                        let paused = agent_request("GET", "/v1/health").ok().and_then(|h| h["paused"].as_bool()).unwrap_or(false);
                        let path = if paused { "/v1/resume" } else { "/v1/pause" };
                        let r = agent_request("POST", path);
                        host_log(&format!("tray {path} -> {}", r.is_ok()));
                        let _ = pause_item.set_text(if paused { "Pause discovery" } else { "Resume discovery" });
                    }
                    "autostart" => {
                        let al = app.autolaunch();
                        let on = al.is_enabled().unwrap_or(false);
                        let r = if on { al.disable() } else { al.enable() };
                        host_log(&format!("autostart {} -> {:?}", !on, r.is_ok()));
                        let _ = autostart_item.set_checked(al.is_enabled().unwrap_or(false));
                    }
                    "quit" => quit_app(app),
                    _ => {}
                })
                .build(app)?;

            // Watchdog: keep an agent alive, keep the tray status current.
            let wd = handle.clone();
            std::thread::spawn(move || {
                ensure_agent(&wd);
                let mut failures = 0u32;
                while !EXITING.load(Ordering::SeqCst) {
                    match agent_request("GET", "/v1/health") {
                        Ok(h) => {
                            failures = 0;
                            let paused = h["paused"].as_bool().unwrap_or(false);
                            let _ = status.set_text(format!(
                                "Agent v{} pid {} | ticks {} | {}",
                                h["version"].as_str().unwrap_or("?"), h["pid"], h["ticks_this_run"],
                                if paused { "paused" } else { "running" }
                            ));
                            let _ = pause.set_text(if paused { "Resume discovery" } else { "Pause discovery" });
                        }
                        Err(e) => {
                            failures += 1;
                            let _ = status.set_text("Status: agent not responding");
                            host_log(&format!("health failed ({failures}): {e}"));
                            if failures >= 2 && !EXITING.load(Ordering::SeqCst) {
                                let mut r = RESTARTS.lock().unwrap();
                                r.retain(|t| t.elapsed() < Duration::from_secs(300));
                                if r.len() < 3 {
                                    r.push(Instant::now());
                                    drop(r);
                                    host_log("watchdog respawning agent");
                                    ensure_agent(&wd);
                                } else {
                                    let _ = status.set_text("Status: agent keeps failing, see logs");
                                }
                                failures = 0;
                            }
                        }
                    }
                    std::thread::sleep(Duration::from_secs(5));
                }
            });

            if !background {
                open_window(&handle);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri build")
        .run(|_app, event| {
            // Closing the last window must not end the process: the tray stays.
            if let RunEvent::ExitRequested { api, code, .. } = event {
                if code.is_none() && !EXITING.load(Ordering::SeqCst) {
                    api.prevent_exit();
                }
            }
        });
}
