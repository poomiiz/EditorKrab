//! Model Context Protocol framing for the agent endpoint at `/mcp`, plus the stdio bridge
//! (`editorkrab --mcp`) for agents that only launch local commands.
//! The tools themselves run inside the app; this module only speaks JSON-RPC.
use serde_json::{Value, json};
use std::io::{self, BufRead, Read, Write};
use std::net::{Ipv4Addr, TcpStream};
use std::time::Duration;

const VERSIONS: [&str; 3] = ["2025-06-18", "2025-03-26", "2024-11-05"];

pub enum Message {
    Request {
        id: Value,
        method: String,
        params: Value,
    },
    Notification,
    Invalid(Value),
}

pub fn parse(body: &Value) -> Message {
    let method = body["method"].as_str();
    match (body.get("id"), method) {
        _ if body["jsonrpc"] != "2.0" => {
            Message::Invalid(body.get("id").cloned().unwrap_or(Value::Null))
        }
        (None, Some(_)) => Message::Notification,
        (Some(id), Some(method)) if id.is_string() || id.is_number() => Message::Request {
            id: id.clone(),
            method: method.to_owned(),
            params: body.get("params").cloned().unwrap_or_else(|| json!({})),
        },
        (id, _) => Message::Invalid(id.cloned().unwrap_or(Value::Null)),
    }
}

pub fn result(id: &Value, result: Value) -> Value {
    json!({"jsonrpc":"2.0","id":id,"result":result})
}

pub fn error(id: &Value, code: i64, message: &str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}

/// Agrees on the client's protocol version when the app knows it, otherwise offers the newest.
pub fn initialize(params: &Value) -> Value {
    let asked = params["protocolVersion"].as_str().unwrap_or("");
    let version = VERSIONS
        .iter()
        .find(|v| **v == asked)
        .unwrap_or(&VERSIONS[0]);
    json!({
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "editorkrab", "version": env!("CARGO_PKG_VERSION")},
        "instructions": "EditorKrab cuts talking videos into short 9:16 clips with subtitles. \
    Flow: import_videos -> transcribe -> poll get_project until every file's transcription is done -> \
    get_transcript -> set_clips (pick segment_ids per clip) -> export_clips -> poll get_project until export.state is done; \
    export.files lists the finished clips. The person can open open_in_app to see and adjust the same project."
    })
}

/// A tool's answer as MCP text content; failures stay tool results so the agent can read and recover.
pub fn tool_result(value: &Value, failed: bool) -> Value {
    json!({
        "content": [{"type": "text", "text": serde_json::to_string_pretty(value).unwrap_or_default()}],
        "isError": failed
    })
}

fn project() -> Value {
    json!({"type":"string","description":"Project id returned by import_videos (also the ?project= value in the app address)."})
}

pub fn tools() -> Value {
    json!([
        {
            "name": "import_videos",
            "description": "Add video or audio files (or folders of them) on this computer to a project. Creates a new project when project is omitted.",
            "inputSchema": {"type":"object","properties":{
                "paths": {"type":"array","items":{"type":"string"},"minItems":1,"description":"Absolute file or folder paths."},
                "project": project(),
                "title": {"type":"string","maxLength":120},
                "language": {"type":"string","enum":["auto","th","en"],"description":"Spoken language; auto by default."}
            },"required":["paths"]}
        },
        {
            "name": "transcribe",
            "description": "Start speech-to-text for the project's files. Runs in the background; poll get_project.",
            "inputSchema": {"type":"object","properties":{
                "project": project(),
                "source_ids": {"type":"array","items":{"type":"string"},"description":"Only these files; all files by default."}
            },"required":["project"]}
        },
        {
            "name": "get_project",
            "description": "Files, transcription progress, chosen clips and export progress of a project.",
            "inputSchema": {"type":"object","properties":{"project": project()},"required":["project"]}
        },
        {
            "name": "get_transcript",
            "description": "Timed spoken segments per file. Clips are built only from these segment ids.",
            "inputSchema": {"type":"object","properties":{"project": project()},"required":["project"]}
        },
        {
            "name": "set_clips",
            "description": "Choose the clips: each clip is a title plus segment ids in playing order. Replaces earlier choices.",
            "inputSchema": {"type":"object","properties":{
                "project": project(),
                "clips": {"type":"array","minItems":1,"items":{"type":"object","properties":{
                    "title": {"type":"string"},
                    "segment_ids": {"type":"array","items":{"type":"string"},"minItems":1}
                },"required":["title","segment_ids"]}}
            },"required":["project","clips"]}
        },
        {
            "name": "export_clips",
            "description": "Render the chosen clips as 9:16 videos with subtitles into the Videos/EditorKrab folder. Runs in the background; poll get_project.",
            "inputSchema": {"type":"object","properties":{"project": project()},"required":["project"]}
        }
    ])
}

/// Relays newline-delimited JSON-RPC between stdin/stdout and the running app.
pub fn bridge(port: u16) -> io::Result<()> {
    let mut out = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let id = serde_json::from_str::<Value>(&line)
            .ok()
            .and_then(|v| v.get("id").cloned());
        let reply = match post(port, &line) {
            Ok((200, body)) if !body.trim().is_empty() => body.trim().to_owned(),
            Ok((202, _)) => continue,
            Ok((status, body)) => match id {
                Some(id) => error(
                    &id,
                    -32000,
                    &format!("EditorKrab answered HTTP {status}: {}", body.trim()),
                )
                .to_string(),
                None => continue,
            },
            Err(e) => match id {
                Some(id) => error(
                    &id,
                    -32000,
                    &format!(
                        "EditorKrab is not running on 127.0.0.1:{port}; open the app first ({e})"
                    ),
                )
                .to_string(),
                None => continue,
            },
        };
        writeln!(out, "{reply}")?;
        out.flush()?;
    }
    Ok(())
}

fn post(port: u16, body: &str) -> io::Result<(u16, String)> {
    let mut stream = TcpStream::connect((Ipv4Addr::LOCALHOST, port))?;
    stream.set_read_timeout(Some(Duration::from_secs(600)))?;
    write!(
        stream,
        "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n\
Accept: application/json, text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw)?;
    let text = String::from_utf8_lossy(&raw);
    let (head, body) = text
        .split_once("\r\n\r\n")
        .ok_or_else(|| io::Error::other("incomplete HTTP response"))?;
    let status = head
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| io::Error::other("invalid HTTP status"))?;
    Ok((status, body.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requests_and_notifications_are_told_apart() {
        let request = json!({"jsonrpc":"2.0","id":7,"method":"tools/list"});
        assert!(
            matches!(parse(&request), Message::Request { ref method, .. } if method == "tools/list")
        );
        let note = json!({"jsonrpc":"2.0","method":"notifications/initialized"});
        assert!(matches!(parse(&note), Message::Notification));
        assert!(matches!(
            parse(&json!({"id":1,"method":"x"})),
            Message::Invalid(_)
        ));
        assert!(matches!(
            parse(&json!({"jsonrpc":"2.0","id":{},"method":"x"})),
            Message::Invalid(_)
        ));
    }

    #[test]
    fn initialize_keeps_a_known_client_version() {
        assert_eq!(
            initialize(&json!({"protocolVersion":"2025-03-26"}))["protocolVersion"],
            "2025-03-26"
        );
        assert_eq!(
            initialize(&json!({"protocolVersion":"1999-01-01"}))["protocolVersion"],
            VERSIONS[0]
        );
    }

    #[test]
    fn every_tool_has_an_object_schema() {
        let tools = tools();
        let names: Vec<_> = tools
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        assert_eq!(
            names,
            [
                "import_videos",
                "transcribe",
                "get_project",
                "get_transcript",
                "set_clips",
                "export_clips"
            ]
        );
        assert!(
            tools
                .as_array()
                .unwrap()
                .iter()
                .all(|t| t["inputSchema"]["type"] == "object")
        );
    }

    #[test]
    fn bridge_reports_a_closed_app() {
        let port = std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port();
        assert!(post(port, "{}").is_err());
    }
}
