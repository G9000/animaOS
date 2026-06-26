#![allow(dead_code)]

pub mod files;
pub mod process;
pub mod shell;

use serde_json::{json, Value};

use crate::permissions::PermissionPolicy;
use crate::protocol::{ClientFrame, ServerFrame, ToolSchema, ToolStatus};
use crate::tools::process::ProcessRegistry;

pub const ACTION_TOOL_NAMES: &[&str] = &[
    "bash",
    "read_file",
    "write_file",
    "edit_file",
    "grep",
    "glob",
    "list_dir",
    "multi_edit",
    "ask_user",
    "todo_write",
    "todo_read",
    "bg_start",
    "bg_output",
    "bg_stop",
    "bg_list",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolOutput {
    pub content: String,
    pub is_error: bool,
    pub stdout: Vec<String>,
    pub stderr: Vec<String>,
}

impl ToolOutput {
    pub fn success(content: impl Into<String>) -> Self {
        Self {
            content: content.into(),
            is_error: false,
            stdout: Vec::new(),
            stderr: Vec::new(),
        }
    }

    pub fn error(content: impl Into<String>) -> Self {
        Self {
            content: content.into(),
            is_error: true,
            stdout: Vec::new(),
            stderr: Vec::new(),
        }
    }
}

pub fn action_tool_schemas() -> Vec<ToolSchema> {
    ACTION_TOOL_NAMES
        .iter()
        .map(|name| ToolSchema {
            name: (*name).to_string(),
            description: tool_description(name).to_string(),
            parameters: json!({"type":"object"}),
        })
        .collect()
}

fn tool_description(name: &str) -> &'static str {
    match name {
        "bash" => "Execute a shell command and return its output.",
        "read_file" => "Read a file and return line-numbered contents.",
        "write_file" => "Write content to a file inside the workspace.",
        "edit_file" => "Replace an exact string in a workspace file.",
        "grep" => "Search file contents under a workspace path.",
        "glob" => "Find files matching a simple glob under a workspace path.",
        "list_dir" => "List directory contents.",
        "multi_edit" => "Apply multiple exact string replacements atomically.",
        "ask_user" => "Ask the user a question.",
        "todo_write" => "Replace the current local todo snapshot.",
        "todo_read" => "Read the current local todo snapshot.",
        "bg_start" => "Start a background shell process.",
        "bg_output" => "Read background process output.",
        "bg_stop" => "Stop a background process.",
        "bg_list" => "List background processes.",
        _ => "Animus action tool.",
    }
}

pub struct ToolExecutor {
    policy: PermissionPolicy,
    processes: ProcessRegistry,
    todos: Value,
}

impl ToolExecutor {
    pub fn new(policy: PermissionPolicy) -> Self {
        Self {
            policy,
            processes: ProcessRegistry::default(),
            todos: json!([]),
        }
    }

    pub async fn execute_frame(&mut self, frame: &ServerFrame) -> Option<ClientFrame> {
        match frame {
            ServerFrame::ToolExecute {
                tool_call_id,
                tool_name,
                args,
            } => Some(self.execute_tool_call(tool_call_id, tool_name, args).await),
            _ => None,
        }
    }

    pub async fn execute_tool_call(
        &mut self,
        tool_call_id: &str,
        tool_name: &str,
        args: &Value,
    ) -> ClientFrame {
        let output = self.dispatch(tool_name, args).await;
        ClientFrame::ToolResult {
            tool_call_id: tool_call_id.to_string(),
            status: if output.is_error {
                ToolStatus::Error
            } else {
                ToolStatus::Success
            },
            result: output.content,
            stdout: if output.stdout.is_empty() {
                None
            } else {
                Some(output.stdout)
            },
            stderr: if output.stderr.is_empty() {
                None
            } else {
                Some(output.stderr)
            },
        }
    }

    async fn dispatch(&mut self, tool_name: &str, args: &Value) -> ToolOutput {
        match tool_name {
            "bash" => shell::run_shell(args, &self.policy).await,
            "read_file" => files::read_file(args, &self.policy),
            "write_file" => files::write_file(args, &self.policy),
            "edit_file" => files::edit_file(args, &self.policy),
            "grep" => files::grep(args, &self.policy),
            "glob" => files::glob(args, &self.policy),
            "list_dir" => files::list_dir(args, &self.policy),
            "multi_edit" => files::multi_edit(args, &self.policy),
            "ask_user" => ToolOutput::error("ask_user requires interactive UI support"),
            "todo_write" => {
                self.todos = args.get("todos").cloned().unwrap_or_else(|| json!([]));
                ToolOutput::success("todos updated")
            }
            "todo_read" => ToolOutput::success(self.todos.to_string()),
            "bg_start" => self.processes.start(args, &self.policy).await,
            "bg_output" => self.processes.output(args),
            "bg_stop" => self.processes.stop(args, &self.policy).await,
            "bg_list" => self.processes.list(),
            _ => ToolOutput::error(format!("unknown tool: {tool_name}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::permissions::PermissionPolicy;
    use crate::protocol::{ClientFrame, ServerFrame, ToolStatus};

    #[test]
    fn schemas_include_legacy_action_tool_names() {
        let names: Vec<String> = action_tool_schemas()
            .into_iter()
            .map(|schema| schema.name)
            .collect();

        assert_eq!(
            names,
            vec![
                "bash",
                "read_file",
                "write_file",
                "edit_file",
                "grep",
                "glob",
                "list_dir",
                "multi_edit",
                "ask_user",
                "todo_write",
                "todo_read",
                "bg_start",
                "bg_output",
                "bg_stop",
                "bg_list",
            ]
        );
    }

    #[tokio::test]
    async fn tool_execute_frame_returns_tool_result_frame() {
        let root = test_workspace();
        std::fs::write(root.join("note.txt"), "hello").unwrap();
        let mut executor = ToolExecutor::new(PermissionPolicy::workspace_write(root.clone()));

        let result = executor
            .execute_frame(&ServerFrame::ToolExecute {
                tool_call_id: "call-1".to_string(),
                tool_name: "read_file".to_string(),
                args: json!({"file_path": root.join("note.txt")}),
            })
            .await
            .unwrap();

        assert_eq!(
            result,
            ClientFrame::ToolResult {
                tool_call_id: "call-1".to_string(),
                status: ToolStatus::Success,
                result: "1: hello".to_string(),
                stdout: None,
                stderr: None,
            }
        );
    }

    fn test_workspace() -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("animus-tools-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }
}
