//! Links from job postings open in the system browser, never in the webview (ARCHITECTURE 8).
//!
//! Allow-list: `http` and `https` only, a host present, no user:password part, at most 2048
//! characters, no control characters. Anything else (`javascript:`, `file:`, `ms-settings:`,
//! UNC paths) is refused before it can reach ShellExecute.

use crate::wide::to_wide;

pub fn checked(url: &str) -> Result<String, String> {
    let u = url.trim();
    if u.len() > 2048 || u.chars().any(|c| c.is_control()) {
        return Err("link refused: too long or contains control characters".into());
    }
    let lower = u.to_ascii_lowercase();
    let rest = if let Some(r) = lower.strip_prefix("https://") {
        r
    } else if let Some(r) = lower.strip_prefix("http://") {
        r
    } else {
        return Err("link refused: only http and https links open".into());
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.is_empty() || authority.contains('@') || authority.contains('\\') {
        return Err("link refused: missing host or embedded credentials".into());
    }
    // Characters a browser's command line could split on reach ShellExecute only percent-encoded:
    // a handler registered as "x.exe" "%1" would otherwise take a '"' as the end of the argument.
    let mut out = String::with_capacity(u.len());
    for c in u.chars() {
        match c {
            ' ' => out.push_str("%20"),
            '"' => out.push_str("%22"),
            '<' => out.push_str("%3C"),
            '>' => out.push_str("%3E"),
            '`' => out.push_str("%60"),
            _ => out.push(c),
        }
    }
    Ok(out)
}

pub fn open(url: &str) -> Result<(), String> {
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
    let verb = to_wide("open");
    let target = to_wide(url);
    // ShellExecuteW returns a value > 32 on success.
    let r = unsafe { ShellExecuteW(std::ptr::null_mut(), verb.as_ptr(), target.as_ptr(), std::ptr::null(), std::ptr::null(), SW_SHOWNORMAL) };
    if (r as isize) > 32 {
        Ok(())
    } else {
        Err(format!("could not open the link (ShellExecute {})", r as isize))
    }
}

#[cfg(test)]
mod tests {
    use super::checked;

    #[test]
    fn quotes_and_spaces_reach_the_shell_encoded() {
        assert_eq!(checked("https://example.com/a b\" --new-window file:///c:/x").unwrap(),
                   "https://example.com/a%20b%22%20--new-window%20file:///c:/x");
        assert_eq!(checked("https://example.com/jobs?id=1&q=<x>").unwrap(), "https://example.com/jobs?id=1&q=%3Cx%3E");
    }

    #[test]
    fn only_plain_web_links_pass() {
        assert!(checked("https://boards.greenhouse.io/acme/jobs/1").is_ok());
        assert!(checked("http://example.com").is_ok());
        for bad in [
            "javascript:alert(1)",
            "JAVASCRIPT:alert(1)",
            "file:///C:/Windows/System32/calc.exe",
            "ms-settings:privacy",
            r"\\server\share\x.exe",
            "https://user:pass@example.com/",
            "https://",
            "https://exa\u{0}mple.com",
            "vbscript:msgbox",
            "//example.com/no-scheme",
        ] {
            assert!(checked(bad).is_err(), "{bad} should be refused");
        }
        assert!(checked(&format!("https://example.com/{}", "a".repeat(3000))).is_err());
    }
}
