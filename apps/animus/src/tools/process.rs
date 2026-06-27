#![allow(dead_code)]

use std::collections::HashMap;
use std::process::{ExitStatus, Stdio};
use std::sync::{Arc, Mutex};

use serde_json::Value;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Child;

use crate::permissions::{PermissionDecision, PermissionPolicy};
use crate::tools::redaction::redact_text;
use crate::tools::shell::shell_command;
use crate::tools::ToolOutput;

const MAX_BUFFERED_OUTPUT_LINES: usize = 1_000;
const TRUNCATED_OUTPUT_MARKER: &str = "[older background output truncated]";

type SharedOutputBuffer = Arc<Mutex<OutputBuffer>>;

#[derive(Default)]
struct OutputBuffer {
    lines: Vec<String>,
    dropped: usize,
}

#[derive(Default)]
pub struct ProcessRegistry {
    next_id: u64,
    entries: HashMap<String, ProcessEntry>,
}

struct ProcessEntry {
    command: String,
    child: Child,
    stdout: SharedOutputBuffer,
    stderr: SharedOutputBuffer,
    stdout_cursor: usize,
    stderr_cursor: usize,
    exit_status: Option<String>,
    exit_reported: bool,
}

impl ProcessRegistry {
    pub async fn start(&mut self, args: &Value, policy: &PermissionPolicy) -> ToolOutput {
        let Some(command) = args.get("command").and_then(Value::as_str) else {
            return ToolOutput::error("bg_start requires command");
        };
        match policy.check_shell(command) {
            PermissionDecision::Allow => {}
            PermissionDecision::Ask { reason } | PermissionDecision::Deny { reason } => {
                return ToolOutput::error(reason);
            }
        }

        let mut child_command = shell_command(command);
        child_command
            .current_dir(policy.workspace())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let mut child = match child_command.spawn() {
            Ok(child) => child,
            Err(err) => return ToolOutput::error(format!("failed to start process: {err}")),
        };

        let stdout_lines = Arc::new(Mutex::new(OutputBuffer::default()));
        let stderr_lines = Arc::new(Mutex::new(OutputBuffer::default()));
        if let Some(stdout) = child.stdout.take() {
            spawn_reader(stdout_lines.clone(), stdout);
        }
        if let Some(stderr) = child.stderr.take() {
            spawn_reader(stderr_lines.clone(), stderr);
        }

        self.next_id += 1;
        let id = format!("bg-{}", self.next_id);
        self.entries.insert(
            id.clone(),
            ProcessEntry {
                command: command.to_string(),
                child,
                stdout: stdout_lines,
                stderr: stderr_lines,
                stdout_cursor: 0,
                stderr_cursor: 0,
                exit_status: None,
                exit_reported: false,
            },
        );
        ToolOutput::success(id)
    }

    pub fn output(&mut self, args: &Value) -> ToolOutput {
        let Some(id) = args.get("id").and_then(Value::as_str) else {
            return ToolOutput::error("bg_output requires id");
        };
        let all = args.get("all").and_then(Value::as_bool).unwrap_or(false);
        let Some(entry) = self.entries.get_mut(id) else {
            return ToolOutput::error(format!("unknown background process: {id}"));
        };
        let stdout = entry.stdout.clone();
        let stderr = entry.stderr.clone();
        let mut lines = Vec::new();
        lines.extend(read_buffered_lines(&stdout, &mut entry.stdout_cursor, all));
        lines.extend(read_buffered_lines(&stderr, &mut entry.stderr_cursor, all));
        if let Some(exit_status) = refresh_exit_status(entry) {
            if !entry.exit_reported {
                lines.push(exit_status);
                entry.exit_reported = true;
            }
        }
        ToolOutput::success(redact_text(&lines.join("\n")))
    }

    pub async fn stop(&mut self, args: &Value, _policy: &PermissionPolicy) -> ToolOutput {
        let Some(id) = args.get("id").and_then(Value::as_str) else {
            return ToolOutput::error("bg_stop requires id");
        };
        let Some(mut entry) = self.entries.remove(id) else {
            return ToolOutput::error(format!("unknown background process: {id}"));
        };
        match entry.child.try_wait() {
            Ok(Some(_)) => ToolOutput::success(format!("stopped {id}")),
            Ok(None) => match entry.child.kill().await {
                Ok(()) => ToolOutput::success(format!("stopped {id}")),
                Err(err) => ToolOutput::error(format!("failed to stop {id}: {err}")),
            },
            Err(err) => ToolOutput::error(format!("failed to inspect {id}: {err}")),
        }
    }

    pub fn list(&mut self) -> ToolOutput {
        let mut rows = self
            .entries
            .iter_mut()
            .map(|(id, entry)| {
                let status = refresh_exit_status(entry).unwrap_or_else(|| "running".to_string());
                format!("{id} [{status}]: {}", entry.command)
            })
            .collect::<Vec<_>>();
        rows.sort();
        ToolOutput::success(rows.join("\n"))
    }
}

impl Drop for ProcessRegistry {
    fn drop(&mut self) {
        for entry in self.entries.values_mut() {
            let _ = entry.child.start_kill();
        }
    }
}

fn spawn_reader<R>(lines: SharedOutputBuffer, reader: R)
where
    R: tokio::io::AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        let mut reader = BufReader::new(reader).lines();
        while let Ok(Some(line)) = reader.next_line().await {
            push_buffered_line(lines.clone(), line);
        }
    });
}

fn push_buffered_line(lines: SharedOutputBuffer, line: String) {
    let Ok(mut locked) = lines.lock() else {
        return;
    };
    locked.lines.push(line);
    if locked.lines.len() > MAX_BUFFERED_OUTPUT_LINES {
        let overflow = locked.lines.len() - MAX_BUFFERED_OUTPUT_LINES;
        locked.lines.drain(0..overflow);
        locked.dropped += overflow;
        if !locked.lines.is_empty() {
            locked.lines[0] = TRUNCATED_OUTPUT_MARKER.to_string();
        }
    }
}

fn read_buffered_lines(lines: &SharedOutputBuffer, cursor: &mut usize, all: bool) -> Vec<String> {
    let Ok(locked) = lines.lock() else {
        return Vec::new();
    };
    let output = if all {
        locked.lines.clone()
    } else {
        let start = cursor
            .saturating_sub(locked.dropped)
            .min(locked.lines.len());
        locked.lines[start..].to_vec()
    };
    *cursor = locked.dropped + locked.lines.len();
    output
}

fn refresh_exit_status(entry: &mut ProcessEntry) -> Option<String> {
    if entry.exit_status.is_none() {
        if let Ok(Some(status)) = entry.child.try_wait() {
            entry.exit_status = Some(format_exit_status(status));
        }
    }
    entry.exit_status.clone()
}

fn format_exit_status(status: ExitStatus) -> String {
    match status.code() {
        Some(code) => format!("exited({code})"),
        None => "exited(signal)".to_string(),
    }
}

fn clone_lines(lines: &SharedOutputBuffer) -> Vec<String> {
    lines
        .lock()
        .map(|buffer| buffer.lines.clone())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::permissions::{PermissionPolicy, ShellPermissionMode};

    #[tokio::test]
    async fn background_process_registry_starts_lists_outputs_and_stops() {
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);
        let mut registry = ProcessRegistry::default();
        let command = if cfg!(windows) {
            "Write-Output bg-ready"
        } else {
            "printf 'bg-ready\\n'"
        };

        let start = registry.start(&json!({"command": command}), &policy).await;
        assert!(!start.is_error);
        let id = start.content.trim().to_string();

        let mut output = String::new();
        for _ in 0..20 {
            output = registry
                .output(&json!({"id": id.clone(), "all": true}))
                .content;
            if output.contains("bg-ready") {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }

        assert!(registry.list().content.contains(&id));
        assert!(output.contains("bg-ready"));
        assert!(!registry.stop(&json!({"id": id}), &policy).await.is_error);
    }

    #[tokio::test]
    async fn background_process_output_defaults_to_unread_lines() {
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);
        let mut registry = ProcessRegistry::default();
        let command = if cfg!(windows) {
            "Write-Output bg-ready"
        } else {
            "printf 'bg-ready\\n'"
        };

        let start = registry.start(&json!({"command": command}), &policy).await;
        assert!(!start.is_error);
        let id = start.content.trim().to_string();

        let mut first = String::new();
        for _ in 0..20 {
            first = registry.output(&json!({"id": id.clone()})).content;
            if first.contains("bg-ready") {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }

        let second = registry.output(&json!({"id": id.clone()})).content;
        let all = registry
            .output(&json!({"id": id.clone(), "all": true}))
            .content;

        assert!(first.contains("bg-ready"));
        assert_eq!(second, "");
        assert!(all.contains("bg-ready"));
        assert!(!registry.stop(&json!({"id": id}), &policy).await.is_error);
    }

    #[tokio::test]
    async fn background_process_reports_exit_after_output_is_consumed() {
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);
        let mut registry = ProcessRegistry::default();
        let command = if cfg!(windows) {
            "Write-Output bg-ready; Start-Sleep -Milliseconds 250; exit 7"
        } else {
            "printf 'bg-ready\\n'; sleep 0.25; exit 7"
        };

        let start = registry.start(&json!({"command": command}), &policy).await;
        assert!(!start.is_error);
        let id = start.content.trim().to_string();

        let mut first = String::new();
        for _ in 0..20 {
            first = registry.output(&json!({"id": id.clone()})).content;
            if first.contains("bg-ready") {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }

        let mut exit_output = String::new();
        for _ in 0..20 {
            exit_output = registry.output(&json!({"id": id.clone()})).content;
            if exit_output.contains("exited(7)") {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        let list = registry.list().content;

        assert!(first.contains("bg-ready"));
        assert!(exit_output.contains("exited(7)"));
        assert!(list.contains("exited(7)"));
        assert!(!registry.stop(&json!({"id": id}), &policy).await.is_error);
    }

    #[tokio::test]
    async fn dropping_registry_stops_background_children() {
        let root = std::env::temp_dir().join(format!("animus-bg-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let marker = root.join("late-bg-marker.txt");
        let policy = PermissionPolicy::workspace_write(root.clone())
            .with_shell_mode(ShellPermissionMode::Allow);
        let command = if cfg!(windows) {
            format!(
                "Start-Sleep -Milliseconds 300; Set-Content -LiteralPath '{}' -Value done",
                marker.display()
            )
        } else {
            format!("sleep 0.3; touch '{}'", marker.display())
        };
        let mut registry = ProcessRegistry::default();

        let start = registry.start(&json!({"command": command}), &policy).await;
        assert!(!start.is_error);
        drop(registry);
        tokio::time::sleep(std::time::Duration::from_millis(700)).await;

        assert!(!marker.exists());
    }

    #[test]
    fn buffered_background_output_keeps_bounded_recent_window() {
        let lines = Arc::new(Mutex::new(OutputBuffer::default()));

        for index in 0..(MAX_BUFFERED_OUTPUT_LINES + 5) {
            push_buffered_line(lines.clone(), format!("line-{index}"));
        }

        let output = clone_lines(&lines);
        let expected_last = format!("line-{}", MAX_BUFFERED_OUTPUT_LINES + 4);

        assert_eq!(output.len(), MAX_BUFFERED_OUTPUT_LINES);
        assert_eq!(
            output.first().map(String::as_str),
            Some(TRUNCATED_OUTPUT_MARKER)
        );
        assert_eq!(
            output.last().map(String::as_str),
            Some(expected_last.as_str())
        );
    }
}
