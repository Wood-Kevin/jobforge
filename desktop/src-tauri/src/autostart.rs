//! Start at sign-in, written explicitly to HKCU (SPEC D16).
//!
//! The Tauri autostart plugin is not used: in spike P1.0d it chose the machine-wide HKLM Run key
//! when the process was elevated. This module only ever touches HKEY_CURRENT_USER.

use std::path::Path;

use winreg::enums::{HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE};
use winreg::RegKey;

pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
pub const VALUE_NAME: &str = "JobForge";

pub fn command_for(exe: &Path) -> String {
    format!("\"{}\" --background", exe.display())
}

fn run_key(write: bool) -> std::io::Result<RegKey> {
    let flags = if write { KEY_SET_VALUE | KEY_QUERY_VALUE } else { KEY_QUERY_VALUE };
    RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags(RUN_KEY, flags)
}

pub fn is_enabled() -> bool {
    run_key(false).and_then(|k| k.get_value::<String, _>(VALUE_NAME)).is_ok()
}

pub fn set(enabled: bool, exe: &Path) -> std::io::Result<()> {
    let key = run_key(true)?;
    if enabled {
        key.set_value(VALUE_NAME, &command_for(exe))
    } else {
        match key.delete_value(VALUE_NAME) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            other => other,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_quotes_the_path_and_starts_in_background() {
        let cmd = command_for(Path::new(r"C:\Users\x\AppData\Local\JobForge\JobForge.exe"));
        assert_eq!(cmd, r#""C:\Users\x\AppData\Local\JobForge\JobForge.exe" --background"#);
    }
}
