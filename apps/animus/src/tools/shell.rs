#![allow(dead_code)]

use std::time::Duration;

use serde_json::Value;
use tokio::process::Command;

use crate::permissions::{PermissionDecision, PermissionPolicy};
use crate::tools::ToolOutput;

pub async fn run_shell(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(command) = args.get("command").and_then(Value::as_str) else {
        return ToolOutput::error("bash requires command");
    };
    match policy.check_shell(command) {
        PermissionDecision::Allow => {}
        PermissionDecision::Ask { reason } | PermissionDecision::Deny { reason } => {
            return ToolOutput::error(reason);
        }
    }
    let timeout_ms = args
        .get("timeout")
        .and_then(Value::as_u64)
        .unwrap_or(120_000);
    let mut child = shell_command(command);
    child.current_dir(policy.workspace());

    let run = child.output();
    let output = match tokio::time::timeout(Duration::from_millis(timeout_ms), run).await {
        Ok(Ok(output)) => output,
        Ok(Err(err)) => return ToolOutput::error(format!("failed to run shell command: {err}")),
        Err(_) => {
            return ToolOutput::error(format!("shell command timed out after {timeout_ms}ms"))
        }
    };

    let stdout = split_lines(&String::from_utf8_lossy(&output.stdout));
    let stderr = split_lines(&String::from_utf8_lossy(&output.stderr));
    let mut content = String::new();
    if !stdout.is_empty() {
        content.push_str(&stdout.join("\n"));
    }
    if !stderr.is_empty() {
        if !content.is_empty() {
            content.push('\n');
        }
        content.push_str(&stderr.join("\n"));
    }
    if content.is_empty() {
        content = format!("exit status: {}", output.status);
    }

    ToolOutput {
        content,
        is_error: !output.status.success(),
        stdout,
        stderr,
    }
}

pub(crate) fn shell_command(command: &str) -> Command {
    if cfg!(windows) {
        let mut cmd = Command::new("powershell");
        cmd.arg("-NoProfile").arg("-Command").arg(command);
        cmd
    } else {
        let mut cmd = Command::new("sh");
        cmd.arg("-c").arg(command);
        cmd
    }
}

fn split_lines(raw: &str) -> Vec<String> {
    raw.lines().map(ToString::to_string).collect()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::permissions::{PermissionPolicy, ShellPermissionMode};

    #[tokio::test]
    async fn shell_exec_captures_stdout_and_stderr() {
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);

        let result = run_shell(&json!({"command": "echo animus"}), &policy).await;

        assert!(!result.is_error);
        assert!(result.content.contains("animus"));
        assert!(result.stdout.iter().any(|line| line.contains("animus")));
    }

    #[tokio::test]
    async fn shell_exec_times_out() {
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);

        let command = if cfg!(windows) {
            "Start-Sleep -Milliseconds 200"
        } else {
            "sleep 0.2"
        };
        let result = run_shell(&json!({"command": command, "timeout": 10}), &policy).await;

        assert!(result.is_error);
        assert!(result.content.contains("timed out"));
    }
}
