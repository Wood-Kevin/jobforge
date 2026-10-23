//! JobForge desktop host. Tray, window, agent supervision, IPC forwarding; no business logic (SPEC D3).

mod autostart;
mod elevation;
mod events;
mod links;
mod paths;
mod supervisor;
mod wide;

use std::sync::Arc;
use std::time::Duration;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, State as TauriState, WebviewUrl, WebviewWindowBuilder};

use paths::host_log;
use supervisor::{agent_request, agent_send, Body, State, Status, Supervisor};

const WINDOW_LABEL: &str = "main";

fn open_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(WINDOW_LABEL) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        return;
    }
    // Closing the window destroys it (frees WebView2); it is rebuilt on demand.
    if let Err(e) = WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::App("index.html".into()))
        .title("JobForge")
        .inner_size(1180.0, 820.0)
        .min_inner_size(640.0, 480.0)
        .build()
    {
        host_log(&format!("window build failed: {e}"));
    }
}

/// `--autostart on|off`: same registry function as the tray item, for scripts and tests.
fn autostart_arg(argv: &[String]) -> Option<bool> {
    let i = argv.iter().position(|a| a == "--autostart")?;
    match argv.get(i + 1).map(String::as_str) {
        Some("on") => Some(true),
        Some("off") => Some(false),
        _ => None,
    }
}

fn apply_autostart(want: bool) {
    let r = std::env::current_exe().and_then(|exe| autostart::set(want, &exe));
    host_log(&format!("autostart -> {want} ok={} (command line)", r.is_ok()));
}

fn quit(app: AppHandle, sup: Arc<Supervisor>) {
    std::thread::spawn(move || {
        sup.shutdown_agent();
        app.exit(0);
    });
}

/// The webview's only path to the agent. The host adds the token; the webview never sees it.
#[tauri::command]
async fn agent_call(method: String, path: String, body: Option<serde_json::Value>) -> Result<serde_json::Value, String> {
    window_path_allowed(&path)?;
    // A model-config test runs a real model call (up to the 120 s inference timeout); reading a
    // resume for suggestions allows the model 300 s (agent profile/suggest.py TIMEOUT_S).
    let timeout = if path.ends_with("/test") {
        Duration::from_secs(180)
    } else if path.ends_with("/suggest-items") {
        Duration::from_secs(330)
    } else {
        Duration::from_secs(15)
    };
    let body = body.map(Body::Json).unwrap_or(Body::None);
    tauri::async_runtime::spawn_blocking(move || agent_send(&method, &path, body, timeout))
        .await
        .map_err(|e| e.to_string())?
}

/// Resume upload: the file's bytes, unchanged, as the request body (P1.5).
#[tauri::command]
async fn agent_upload(path: String, bytes: Vec<u8>) -> Result<serde_json::Value, String> {
    window_path_allowed(&path)?;
    if !path.starts_with("/v1/resumes?") {
        return Err("uploads go to /v1/resumes only".into());
    }
    tauri::async_runtime::spawn_blocking(move || agent_send("POST", &path, Body::Bytes(bytes), Duration::from_secs(30)))
        .await
        .map_err(|e| e.to_string())?
}

/// Opens a link from a job posting in the system browser. Only http(s) URLs without credentials
/// pass (ARCHITECTURE 8): a posting's `javascript:`, `file:` or `ms-settings:` link never reaches
/// the shell.
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    let url = links::checked(&url)?;
    host_log("open_url");
    links::open(&url)
}

fn window_path_allowed(path: &str) -> Result<(), String> {
    if !path.starts_with("/v1/") {
        return Err("path must start with /v1/".into());
    }
    // Stopping the agent is a host action (tray Exit); the event stream is relayed by the host.
    if path.starts_with("/v1/shutdown") || path.starts_with("/v1/events") {
        return Err(format!("{path} is not available to the window"));
    }
    Ok(())
}

#[tauri::command]
fn supervisor_status(sup: TauriState<'_, Arc<Supervisor>>) -> Status {
    sup.status()
}

/// Backups for the restore screen, with the live database's schema so the window can tell which
/// backups this version can open (ARCHITECTURE 7.2).
#[tauri::command]
fn list_backups() -> serde_json::Value {
    serde_json::json!({ "current_schema": supervisor::current_schema(), "backups": supervisor::list_backups() })
}

#[tauri::command]
async fn restore_backup(sup: TauriState<'_, Arc<Supervisor>>, name: String) -> Result<String, String> {
    let sup = Arc::clone(&sup);
    tauri::async_runtime::spawn_blocking(move || sup.restore_backup(&name)).await.map_err(|e| e.to_string())?
}

fn status_text(s: &Status) -> String {
    match s.state {
        State::Starting => "Agent: starting".into(),
        State::Running => format!("Agent: running (pid {})", s.agent_pid.map(|p| p.to_string()).unwrap_or_else(|| "?".into())),
        State::NotResponding => "Agent: not responding".into(),
        State::StoppedDbNewer => "Agent stopped: database is newer than this version".into(),
        State::StoppedElevated => "Agent stopped: must run without administrator rights".into(),
        State::Failing => "Agent keeps failing: see logs".into(),
        State::Exiting => "Exiting...".into(),
    }
}

pub fn run() {
    // SPEC D16, before any window, tray, or agent exists.
    match elevation::decide(elevation::is_elevated(), elevation::current_user_sid().as_deref(), elevation::shell_user_sid().as_deref()) {
        elevation::Decision::Proceed => {}
        elevation::Decision::Relaunch => {
            match elevation::relaunch_unelevated() {
                Ok(()) => host_log("relaunched_unelevated"),
                Err(e) => {
                    host_log(&format!("refused_elevated: relaunch failed: {e}"));
                    elevation::message_box(elevation::REFUSAL_TEXT);
                }
            }
            return;
        }
        elevation::Decision::Refuse(why) => {
            // Logged only: creating files under another user's profile is exactly what D16 avoids.
            eprintln!("refused_elevated: {why}");
            elevation::message_box(elevation::REFUSAL_TEXT);
            return;
        }
    }

    let args: Vec<String> = std::env::args().collect();
    let background = args.iter().any(|a| a == "--background");
    let exit_only = args.iter().any(|a| a == "--exit");
    let autostart_only = autostart_arg(&args);

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // A second launch: forward to this instance.
            if argv.iter().any(|a| a == "--exit") {
                let sup = app.state::<Arc<Supervisor>>().inner().clone();
                quit(app.clone(), sup);
            } else if let Some(want) = autostart_arg(&argv) {
                apply_autostart(want);
            } else if !argv.iter().any(|a| a == "--background") {
                open_window(app);
            }
        }))
        .invoke_handler(tauri::generate_handler![agent_call, agent_upload, open_url, supervisor_status, list_backups, restore_backup])
        .setup(move |app| {
            let handle = app.handle().clone();
            if exit_only {
                // --exit with no JobForge running: nothing to stop.
                std::process::exit(0);
            }
            if let Some(want) = autostart_only {
                apply_autostart(want);
                std::process::exit(0);
            }
            host_log(&format!("host start background={background} version={}", app.package_info().version));
            let sup = Arc::new(Supervisor::new(handle.clone()));
            app.manage(sup.clone());

            let status = MenuItem::with_id(app, "status", "Agent: starting", false, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "Pause discovery", true, None::<&str>)?;
            let autostart_item = CheckMenuItem::with_id(app, "autostart", "Start at sign-in", true, autostart::is_enabled(), None::<&str>)?;
            let open = MenuItem::with_id(app, "open", "Open JobForge", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Exit JobForge", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(app)?;
            let menu = Menu::with_items(app, &[&open, &status, &pause, &autostart_item, &sep, &quit_item])?;

            let tray = TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().cloned().expect("bundle icon"))
                .tooltip("JobForge")
                .menu(&menu)
                .on_menu_event({
                    let sup = sup.clone();
                    let pause = pause.clone();
                    let autostart_item = autostart_item.clone();
                    move |app, event| match event.id.as_ref() {
                        "open" => open_window(app),
                        "pause" => {
                            let pause = pause.clone();
                            std::thread::spawn(move || {
                                let paused = agent_request("GET", "/v1/health", Duration::from_secs(3))
                                    .ok()
                                    .and_then(|h| h["discovery_paused"].as_bool())
                                    .unwrap_or(false);
                                let path = if paused { "/v1/discovery/resume" } else { "/v1/discovery/pause" };
                                let r = agent_request("POST", path, Duration::from_secs(3));
                                host_log(&format!("tray {path} ok={}", r.is_ok()));
                                if r.is_ok() {
                                    let _ = pause.set_text(if paused { "Pause discovery" } else { "Resume discovery" });
                                }
                            });
                        }
                        "autostart" => {
                            let want = !autostart::is_enabled();
                            let r = std::env::current_exe().and_then(|exe| autostart::set(want, &exe));
                            host_log(&format!("autostart -> {want} ok={}", r.is_ok()));
                            let _ = autostart_item.set_checked(autostart::is_enabled());
                        }
                        "quit" => quit(app.clone(), sup.clone()),
                        _ => {}
                    }
                })
                .build(app)?;

            sup.set_status_listener(Box::new({
                let status = status.clone();
                let tray = tray.clone();
                move |s: &Status| {
                    let text = status_text(s);
                    let _ = status.set_text(&text);
                    let _ = tray.set_tooltip(Some(format!("JobForge - {text}")));
                }
            }));

            // Keep the Pause item honest even when pausing happens in the window.
            std::thread::spawn({
                let sup = sup.clone();
                let pause = pause.clone();
                move || {
                    while !sup.is_exiting() {
                        if let Ok(h) = agent_request("GET", "/v1/health", Duration::from_secs(3)) {
                            let paused = h["discovery_paused"].as_bool().unwrap_or(false);
                            let _ = pause.set_text(if paused { "Resume discovery" } else { "Pause discovery" });
                        }
                        std::thread::sleep(supervisor::HEALTH_INTERVAL);
                    }
                }
            });
            std::thread::spawn({
                let sup = sup.clone();
                move || sup.watch()
            });
            std::thread::spawn({
                let sup = sup.clone();
                let handle = handle.clone();
                move || events::relay(handle, sup)
            });

            if !background {
                open_window(&handle);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build JobForge")
        .run(|app, event| {
            // Closing the last window must not end JobForge: the tray stays (Q26).
            if let RunEvent::ExitRequested { api, code, .. } = event {
                let exiting = app.try_state::<Arc<Supervisor>>().map(|s| s.is_exiting()).unwrap_or(true);
                if code.is_none() && !exiting {
                    api.prevent_exit();
                }
            }
        });
}
