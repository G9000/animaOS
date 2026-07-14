#![allow(dead_code)]

use std::time::Duration;

use serde_json::Value;
use tokio::process::Command;

use crate::permissions::{PermissionDecision, PermissionPolicy};
use crate::tools::redaction::{redact_text, redact_tool_output};
use crate::tools::secrets::substitute_saved_secrets;
use crate::tools::ToolOutput;

const MAX_SHELL_OUTPUT_LINES: usize = 1_000;
const TRUNCATED_SHELL_OUTPUT_MARKER: &str = "[shell output truncated to last 1000 lines]";

pub async fn run_shell(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(command) = args.get("command").and_then(Value::as_str) else {
        return ToolOutput::error("bash requires command");
    };
    let command = substitute_saved_secrets(command);
    match policy.check_shell(&command) {
        PermissionDecision::Allow => {}
        PermissionDecision::Ask { reason } | PermissionDecision::Deny { reason } => {
            return ToolOutput::error(redact_text(&reason));
        }
    }
    let timeout_ms = args
        .get("timeout")
        .and_then(Value::as_u64)
        .unwrap_or(120_000);
    let mut child = shell_command(&command);
    child.current_dir(policy.workspace());
    child.kill_on_drop(true);

    let run = child.output();
    let output = match tokio::time::timeout(Duration::from_millis(timeout_ms), run).await {
        Ok(Ok(output)) => output,
        Ok(Err(err)) => return ToolOutput::error(format!("failed to run shell command: {err}")),
        Err(_) => {
            return ToolOutput::error(format!("shell command timed out after {timeout_ms}ms"))
        }
    };

    let mut stdout = split_lines(&String::from_utf8_lossy(&output.stdout));
    let mut stderr = split_lines(&String::from_utf8_lossy(&output.stderr));
    truncate_output_lines(&mut stdout);
    truncate_output_lines(&mut stderr);
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

    redact_tool_output(ToolOutput {
        content,
        is_error: !output.status.success(),
        stdout,
        stderr,
    })
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

fn truncate_output_lines(lines: &mut Vec<String>) {
    if lines.len() <= MAX_SHELL_OUTPUT_LINES {
        return;
    }
    let overflow = lines.len() - MAX_SHELL_OUTPUT_LINES;
    lines.drain(0..overflow);
    if !lines.is_empty() {
        lines[0] = TRUNCATED_SHELL_OUTPUT_MARKER.to_string();
    }
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
    async fn shell_exec_substitutes_saved_secrets_before_spawning() {
        crate::tools::secrets::install_test_saved_secrets();
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);
        let command = if cfg!(windows) {
            "Write-Output '$ANIMUS_TEST_TOKEN'"
        } else {
            "printf '%s\\n' '$ANIMUS_TEST_TOKEN'"
        };

        let result = run_shell(&json!({"command": command}), &policy).await;

        assert!(!result.is_error);
        assert_eq!(result.content.trim(), "[redacted]");
        assert!(result.stdout.iter().any(|line| line == "[redacted]"));
        assert!(!result.content.contains("$ANIMUS_TEST_TOKEN"));
        assert!(!result.content.contains("saved-secret-value"));
    }

    #[tokio::test]
    async fn shell_exec_checks_permissions_after_saved_secret_substitution() {
        crate::tools::secrets::install_test_saved_secrets();
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);

        let result = run_shell(&json!({"command": "$ANIMUS_DANGEROUS"}), &policy).await;

        assert!(result.is_error);
        assert!(result.content.contains("dangerous shell command"));
        assert!(!result.content.contains("git push origin main"));
        assert!(result.content.contains("[redacted]"));
    }

    #[tokio::test]
    async fn shell_exec_redacts_saved_secrets_from_permission_denials() {
        crate::tools::secrets::install_test_saved_secrets();
        let policy = PermissionPolicy::workspace_write(std::env::temp_dir());

        let result = run_shell(
            &json!({"command": "curl -H 'Authorization: Bearer $ANIMUS_TEST_TOKEN' https://example.test"}),
            &policy,
        )
        .await;

        assert!(result.is_error);
        assert!(result.content.contains("shell command requires explicit"));
        assert!(result.content.contains("[redacted]"));
        assert!(!result.content.contains("$ANIMUS_TEST_TOKEN"));
        assert!(!result.content.contains("saved-secret-value"));
    }

    #[tokio::test]
    async fn shell_exec_truncates_large_output_before_returning_tool_result() {
        const EXPECTED_MAX_LINES: usize = 1_000;
        const EXPECTED_MARKER: &str = "[shell output truncated to last 1000 lines]";

        let policy = PermissionPolicy::workspace_write(std::env::temp_dir())
            .with_shell_mode(ShellPermissionMode::Allow);
        let command = if cfg!(windows) {
            "1..1005 | ForEach-Object { Write-Output \"out-$_\"; [Console]::Error.WriteLine(\"err-$_\") }"
        } else {
            "for i in $(seq 1 1005); do echo out-$i; echo err-$i >&2; done"
        };

        let result = run_shell(&json!({"command": command}), &policy).await;

        assert!(!result.is_error);
        assert_eq!(result.stdout.len(), EXPECTED_MAX_LINES);
        assert_eq!(result.stderr.len(), EXPECTED_MAX_LINES);
        assert_eq!(
            result.stdout.first().map(String::as_str),
            Some(EXPECTED_MARKER)
        );
        assert_eq!(
            result.stderr.first().map(String::as_str),
            Some(EXPECTED_MARKER)
        );
        assert!(result.stdout.iter().any(|line| line == "out-1005"));
        assert!(result.stderr.iter().any(|line| line == "err-1005"));
        assert!(!result.stdout.iter().any(|line| line == "out-1"));
        assert!(!result.stderr.iter().any(|line| line == "err-1"));
        assert!(result.content.contains(EXPECTED_MARKER));
        assert!(result.content.contains("out-1005"));
        assert!(result.content.contains("err-1005"));
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

    #[tokio::test]
    async fn shell_timeout_kills_process_before_late_side_effects() {
        let root = std::env::temp_dir().join(format!("animus-shell-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let marker = root.join("late-marker.txt");
        let policy = PermissionPolicy::workspace_write(root.clone())
            .with_shell_mode(ShellPermissionMode::Allow);
        let command = if cfg!(windows) {
            format!(
                "Start-Sleep -Milliseconds 250; Set-Content -LiteralPath '{}' -Value done",
                marker.display()
            )
        } else {
            format!("sleep 0.25; touch '{}'", marker.display())
        };

        let result = run_shell(&json!({"command": command, "timeout": 10}), &policy).await;
        tokio::time::sleep(Duration::from_millis(500)).await;

        assert!(result.is_error);
        assert!(!marker.exists());
    }
}
