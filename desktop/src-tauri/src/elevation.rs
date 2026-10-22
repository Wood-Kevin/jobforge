//! SPEC D16: explicit per-user behavior; refuse elevation when it cannot be made safe.
//!
//! Elevated and the elevated token belongs to the signed-in shell user -> relaunch through
//! Explorer (which always runs at medium integrity) and exit. Elevated as a different user
//! ("Run as different user"), or no shell to compare with, or the relaunch fails -> refuse:
//! the per-user paths would belong to the wrong account or files would be created owned by
//! Administrators.

use std::ffi::c_void;
use std::process::Command;
use std::ptr::null_mut;

use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, HANDLE};
use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
use windows_sys::Win32::Security::{GetTokenInformation, TokenElevation, TokenUser, TOKEN_ELEVATION, TOKEN_QUERY, TOKEN_USER};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcess, OpenProcessToken, PROCESS_QUERY_LIMITED_INFORMATION};
use windows_sys::Win32::UI::WindowsAndMessaging::{GetShellWindow, GetWindowThreadProcessId, MessageBoxW, MB_ICONWARNING, MB_OK};

pub const REFUSAL_TEXT: &str = "JobForge must run as you, without administrator rights.\n\nStart it from the Start menu or the tray, not with \"Run as administrator\" or as a different user.";

#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    Proceed,
    Relaunch,
    Refuse(&'static str),
}

/// Pure decision, unit-tested; the Win32 lookups feed it.
pub fn decide(elevated: bool, own_sid: Option<&str>, shell_sid: Option<&str>) -> Decision {
    if !elevated {
        return Decision::Proceed;
    }
    match (own_sid, shell_sid) {
        (Some(own), Some(shell)) if own == shell => Decision::Relaunch,
        (Some(_), Some(_)) => Decision::Refuse("elevated as a different user than the signed-in shell user"),
        _ => Decision::Refuse("cannot identify the signed-in shell user to compare with"),
    }
}

struct Token(HANDLE);

impl Drop for Token {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}

fn open_token(process: HANDLE) -> Option<Token> {
    let mut h: HANDLE = null_mut();
    unsafe {
        if OpenProcessToken(process, TOKEN_QUERY, &mut h) == 0 {
            return None;
        }
    }
    Some(Token(h))
}

pub fn is_elevated() -> bool {
    let Some(t) = open_token(unsafe { GetCurrentProcess() }) else { return false };
    let mut e = TOKEN_ELEVATION { TokenIsElevated: 0 };
    let mut len = 0u32;
    let ok = unsafe {
        GetTokenInformation(t.0, TokenElevation, &mut e as *mut _ as *mut c_void, std::mem::size_of::<TOKEN_ELEVATION>() as u32, &mut len)
    };
    ok != 0 && e.TokenIsElevated != 0
}

fn token_user_sid(t: &Token) -> Option<String> {
    unsafe {
        let mut len = 0u32;
        GetTokenInformation(t.0, TokenUser, null_mut(), 0, &mut len);
        if len == 0 {
            return None;
        }
        let mut buf = vec![0u8; len as usize];
        if GetTokenInformation(t.0, TokenUser, buf.as_mut_ptr() as *mut c_void, len, &mut len) == 0 {
            return None;
        }
        let user = &*(buf.as_ptr() as *const TOKEN_USER);
        let mut text: windows_sys::core::PWSTR = null_mut();
        if ConvertSidToStringSidW(user.User.Sid, &mut text) == 0 {
            return None;
        }
        let sid = crate::wide::from_pwstr(text);
        LocalFree(text as *mut c_void);
        Some(sid)
    }
}

pub fn current_user_sid() -> Option<String> {
    open_token(unsafe { GetCurrentProcess() }).and_then(|t| token_user_sid(&t))
}

/// Owner of the desktop shell (explorer.exe): the signed-in user.
pub fn shell_user_sid() -> Option<String> {
    unsafe {
        let hwnd = GetShellWindow();
        if hwnd.is_null() {
            return None;
        }
        let mut pid = 0u32;
        GetWindowThreadProcessId(hwnd, &mut pid);
        if pid == 0 {
            return None;
        }
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if process.is_null() {
            return None;
        }
        let sid = open_token(process).and_then(|t| token_user_sid(&t));
        CloseHandle(process);
        sid
    }
}

/// Explorer starts the program at medium integrity. It passes no arguments, so the relaunched
/// instance opens its window (an elevated start is always an interactive one).
pub fn relaunch_unelevated() -> std::io::Result<()> {
    let exe = std::env::current_exe()?;
    Command::new("explorer.exe").arg(exe).spawn().map(|_| ())
}

pub fn message_box(text: &str) {
    let body = crate::wide::to_wide(text);
    let title = crate::wide::to_wide("JobForge");
    unsafe {
        MessageBoxW(null_mut(), body.as_ptr(), title.as_ptr(), MB_OK | MB_ICONWARNING);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn not_elevated_proceeds() {
        assert_eq!(decide(false, None, None), Decision::Proceed);
    }

    #[test]
    fn elevated_same_user_relaunches() {
        assert_eq!(decide(true, Some("S-1-5-21-1"), Some("S-1-5-21-1")), Decision::Relaunch);
    }

    #[test]
    fn elevated_other_user_refuses() {
        assert!(matches!(decide(true, Some("S-1-5-21-2"), Some("S-1-5-21-1")), Decision::Refuse(_)));
    }

    #[test]
    fn elevated_without_shell_refuses() {
        assert!(matches!(decide(true, Some("S-1-5-21-1"), None), Decision::Refuse(_)));
    }

    #[test]
    fn sid_lookups_work_in_this_process() {
        let own = current_user_sid().expect("own SID");
        assert!(own.starts_with("S-1-5-"));
    }
}
