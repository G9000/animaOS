#![allow(dead_code)]

use std::collections::HashMap;
use std::process::Stdio;
use std::sync::{Arc, Mutex};

use serde_json::Value;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Child;

use crate::permissions::{PermissionDecision, PermissionPolicy};
use crate::tools::shell::shell_command;
use crate::tools::ToolOutput;

#[derive(Default)]
pub struct ProcessRegistry {
    next_id: u64,
    entries: HashMap<String, ProcessEntry>,
}

struct ProcessEntry {
    command: String,
    child: Child,
    stdout: Arc<Mutex<Vec<String>>>,
    stderr: Arc<Mutex<Vec<String>>>,
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
            .stderr(Stdio::piped());
        let mut child = match child_command.spawn() {
            Ok(child) => child,
            Err(err) => return ToolOutput::error(format!("failed to start process: {err}")),
        };

        let stdout_lines = Arc::new(Mutex::new(Vec::new()));
        let stderr_lines = Arc::new(Mutex::new(Vec::new()));
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
            },
        );
        ToolOutput::success(id)
    }

    pub fn output(&self, args: &Value) -> ToolOutput {
        let Some(id) = args.get("id").and_then(Value::as_str) else {
            return ToolOutput::error("bg_output requires id");
        };
        let Some(entry) = self.entries.get(id) else {
            return ToolOutput::error(format!("unknown background process: {id}"));
        };
        let mut lines = Vec::new();
        lines.extend(clone_lines(&entry.stdout));
        lines.extend(clone_lines(&entry.stderr));
        ToolOutput::success(lines.join("\n"))
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

    pub fn list(&self) -> ToolOutput {
        let mut rows = self
            .entries
            .iter()
            .map(|(id, entry)| format!("{id}: {}", entry.command))
            .collect::<Vec<_>>();
        rows.sort();
        ToolOutput::success(rows.join("\n"))
    }
}

fn spawn_reader<R>(lines: Arc<Mutex<Vec<String>>>, reader: R)
where
    R: tokio::io::AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        let mut reader = BufReader::new(reader).lines();
        while let Ok(Some(line)) = reader.next_line().await {
            if let Ok(mut locked) = lines.lock() {
                locked.push(line);
            }
        }
    });
}

fn clone_lines(lines: &Arc<Mutex<Vec<String>>>) -> Vec<String> {
    lines.lock().map(|lines| lines.clone()).unwrap_or_default()
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
}
