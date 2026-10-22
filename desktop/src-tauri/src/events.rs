//! Relays the agent's Server-Sent Events to the webview as `agent-event` (ARCHITECTURE 5).
//!
//! Reconnects after any error, resuming from the last event id, so an agent restart or a
//! dropped connection loses nothing.

use std::io::{BufRead, BufReader};
use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Emitter};

use crate::paths::host_log;
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

pub fn relay(app: AppHandle, sup: Arc<Supervisor>) {
    let mut last_id: Option<u64> = None;
    while !sup.is_exiting() {
        match open_event_stream(last_id) {
            Ok(resp) => {
                let reader = BufReader::new(resp.into_reader());
                let result = parse_sse(reader, |id, data| {
                    if let Some(i) = id {
                        last_id = Some(i);
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
