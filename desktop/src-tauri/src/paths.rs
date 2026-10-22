//! Per-user locations shared with the agent (ARCHITECTURE 4.1).

use std::io::Write;
use std::path::PathBuf;

use windows_sys::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::UI::Shell::{FOLDERID_LocalAppData, SHGetKnownFolderPath};

/// Must match `jobforge_agent.paths.IDENTIFIER` and the bundle identifier in tauri.conf.json.
pub const IDENTIFIER: &str = "dev.kevin.jobforge";

pub fn data_root() -> PathBuf {
    if let Ok(dir) = std::env::var("JOBFORGE_DATA_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    local_app_data().join(IDENTIFIER)
}

pub fn ipc_file() -> PathBuf {
    data_root().join("run").join("ipc.json")
}

pub fn logs_dir() -> PathBuf {
    data_root().join("logs")
}

/// FOLDERID_LocalAppData for the user this process runs as; the environment string is only a fallback.
fn local_app_data() -> PathBuf {
    unsafe {
        let mut raw: windows_sys::core::PWSTR = std::ptr::null_mut();
        if SHGetKnownFolderPath(&FOLDERID_LocalAppData, 0, std::ptr::null_mut(), &mut raw) == 0 && !raw.is_null() {
            let path = PathBuf::from(crate::wide::from_pwstr(raw));
            CoTaskMemFree(raw as *const core::ffi::c_void);
            return path;
        }
    }
    PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_default())
}

/// One line per host event in logs\host.log. Never contains the IPC token.
pub fn host_log(msg: &str) {
    let dir = logs_dir();
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("host.log")) {
        let ms = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0);
        let _ = writeln!(f, "{ms} pid={} {msg}", std::process::id());
    }
}
