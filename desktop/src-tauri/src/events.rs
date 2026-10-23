//! Relays the agent's Server-Sent Events to the webview as `agent-event` (ARCHITECTURE 5).
//!
//! Reconnects after any error, resuming from the last event id, so an agent restart or a
//! dropped connection loses nothing. The id is also kept in `<data root>\host\event_cursor`,
//! so a host restart resumes from it too (P1.8: before, a restarted host started from "now" and
//! events committed while it was down were never delivered). The agent keeps 7 days of events.

use std::io::{BufRead, BufReader};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Emitter};

use crate::paths::{event_cursor_file, host_log};
use crate::supervisor::{open_event_stream, Supervisor};

/// Parses one SSE stream; calls `on_event(id, data)` per complete event. Returns at end of stream.
pub fn parse_sse<R: BufRead>(reader: R, mut on_event: impl FnMut(Option<u64>, &str)) -> std::io::Result<()> {
    let mut id: Option<u64> = None;
    let mut data = String::new();
    for line in reader.lines() {
        let line = line?;
        if line.is_empty() {
            if !data.is_empty() {
                on_event(id, &data);
                data.clear();
            }
            continue;
        }
        if let Some(v) = line.strip_prefix("id: ") {
            id = v.trim().parse().ok();
        } else if let Some(v) = line.strip_prefix("data: ") {
            if !data.is_empty() {
                data.push('\n');
            }
            data.push_str(v);
        }
        // ": comment" lines (connected, keepalive) are ignored.
    }
    Ok(())
}

pub fn read_cursor(path: &Path) -> Option<u64> {
    std::fs::read_to_string(path).ok()?.trim().parse().ok()
}

/// Atomic replace, so a crash mid-write never leaves a half-written cursor.
pub fn write_cursor(path: &Path, id: u64) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, id.to_string())?;
    std::fs::rename(&tmp, path)
}

pub fn relay(app: AppHandle, sup: Arc<Supervisor>) {
    let cursor = event_cursor_file();
    let mut last_id: Option<u64> = read_cursor(&cursor);
    while !sup.is_exiting() {
        match open_event_stream(last_id) {
            Ok(resp) => {
                host_log(&format!("event_stream_connected after={}", last_id.map(|i| i.to_string()).unwrap_or_else(|| "now".into())));
                let mut first = true;
                let reader = BufReader::new(resp.into_reader());
                let result = parse_sse(reader, |id, data| {
                    if let Some(i) = id {
                        if first {
                            host_log(&format!("event_stream_first_event id={i}"));
                            first = false;
                        }
                        last_id = Some(i);
                        if let Err(e) = write_cursor(&cursor, i) {
                            host_log(&format!("event_cursor_write_failed: {e}"));
                        }
                    }
                    if let Ok(value) = serde_json::from_str::<serde_json::Value>(data) {
                        let _ = app.emit("agent-event", value);
                    }
                });
                if let Err(e) = result {
                    host_log(&format!("event stream ended: {e}"));
                }
            }
            Err(_) => {} // agent not up yet; the watchdog reports that
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cursor_survives_a_restart() {
        let dir = std::env::temp_dir().join(format!("jf_cursor_{}", std::process::id()));
        let path = dir.join("host").join("event_cursor");
        assert_eq!(read_cursor(&path), None);
        write_cursor(&path, 41).unwrap();
        write_cursor(&path, 42).unwrap();
        assert_eq!(read_cursor(&path), Some(42));
        std::fs::write(&path, "garbage").unwrap();
        assert_eq!(read_cursor(&path), None); // unreadable cursor: start from now, never crash
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn parses_events_ignores_comments_and_tracks_ids() {
        let stream = ": connected\n\nid: 7\nevent: change\ndata: {\"id\":7,\"type\":\"discovery_paused\"}\n\n: keepalive\n\nid: 8\ndata: {\"id\":8}\n\n";
        let mut got = Vec::new();
        parse_sse(stream.as_bytes(), |id, data| got.push((id, data.to_string()))).unwrap();
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].0, Some(7));
        assert!(got[0].1.contains("discovery_paused"));
        assert_eq!(got[1], (Some(8), "{\"id\":8}".to_string()));
    }
}
