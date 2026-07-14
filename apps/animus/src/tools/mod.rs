#![allow(dead_code)]

pub mod files;
pub mod process;
pub mod redaction;
pub mod secrets;
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
    "apply_patch",
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
            parameters: tool_parameters(name),
        })
        .collect()
}

fn tool_parameters(name: &str) -> Value {
    match name {
        "bash" | "bg_start" => json!({
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run from the workspace root."
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional timeout in milliseconds."
                }
            },
            "required": ["command"],
            "additionalProperties": false
        }),
        "read_file" => json!({
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative or absolute file path."},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1}
            },
            "required": ["file_path"],
            "additionalProperties": false
        }),
        "write_file" => json!({
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative or absolute file path."},
                "content": {"type": "string", "description": "Complete file contents to write."}
            },
            "required": ["file_path", "content"],
            "additionalProperties": false
        }),
        "edit_file" => json!({
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative or absolute file path."},
                "old_string": {"type": "string", "description": "Exact text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."}
            },
            "required": ["file_path", "old_string", "new_string"],
            "additionalProperties": false
        }),
        "grep" => json!({
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or Rust regex pattern to search for."},
                "mode": {"type": "string", "enum": ["literal", "regex"], "default": "literal"},
                "path": {"type": "string", "description": "Workspace-relative directory or file to search."},
                "limit": {"type": "integer", "minimum": 1}
            },
            "required": ["pattern"],
            "additionalProperties": false
        }),
        "glob" => json!({
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern such as *.rs, src/*.ts, or **/*.md."},
                "path": {"type": "string", "description": "Workspace-relative directory to search."},
                "limit": {"type": "integer", "minimum": 1}
            },
            "required": ["pattern"],
            "additionalProperties": false
        }),
        "list_dir" => json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative or absolute directory path."},
                "limit": {"type": "integer", "minimum": 1}
            },
            "required": ["path"],
            "additionalProperties": false
        }),
        "multi_edit" => json!({
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative or absolute file path."},
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {"type": "string", "description": "Exact text to replace."},
                            "new_string": {"type": "string", "description": "Replacement text."}
                        },
                        "required": ["old_string", "new_string"],
                        "additionalProperties": false
                    }
                }
            },
            "required": ["file_path", "edits"],
            "additionalProperties": false
        }),
        "apply_patch" => json!({
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": "Typed multi-file patch bounded by *** Begin Patch and *** End Patch."
                }
            },
            "required": ["patch"],
            "additionalProperties": false
        }),
        "ask_user" => json!({
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to present to the user."}
            },
            "required": ["question"],
            "additionalProperties": false
        }),
        "todo_write" => json!({
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "Current todo list items.",
                    "items": {"type": "object"}
                }
            },
            "required": ["todos"],
            "additionalProperties": false
        }),
        "bg_output" => json!({
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Background process id returned by bg_start."},
                "all": {
                    "type": "boolean",
                    "description": "Return all retained output instead of only unread output."
                }
            },
            "required": ["id"],
            "additionalProperties": false
        }),
        "bg_stop" => json!({
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Background process id returned by bg_start."}
            },
            "required": ["id"],
            "additionalProperties": false
        }),
        "todo_read" | "bg_list" => json!({
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": false
        }),
        _ => json!({
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": false
        }),
    }
}

fn tool_description(name: &str) -> &'static str {
    match name {
        "bash" => "Execute a shell command and return its output.",
        "read_file" => "Read a file and return line-numbered contents.",
        "write_file" => "Write content to a file inside the workspace.",
        "edit_file" => "Replace an exact string in a workspace file.",
        "grep" => "Search file contents under a workspace path.",
        "glob" => "Find files matching a glob under a workspace path.",
        "list_dir" => "List directory contents.",
        "multi_edit" => "Apply multiple exact string replacements atomically.",
        "apply_patch" => "Preflight and apply a typed multi-file workspace patch.",
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

    pub fn set_policy(&mut self, policy: PermissionPolicy) {
        self.policy = policy;
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
            "apply_patch" => files::apply_patch(args, &self.policy),
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
                "apply_patch",
                "todo_write",
                "todo_read",
                "bg_start",
                "bg_output",
                "bg_stop",
                "bg_list",
            ]
        );
    }

    #[test]
    fn action_tool_schemas_publish_required_parameters() {
        let schemas = action_tool_schemas();
        let schema = |name: &str| {
            schemas
                .iter()
                .find(|schema| schema.name == name)
                .unwrap_or_else(|| panic!("missing schema: {name}"))
        };

        assert_eq!(schema("bash").parameters["required"], json!(["command"]));
        assert_eq!(
            schema("write_file").parameters["required"],
            json!(["file_path", "content"])
        );
        assert_eq!(
            schema("edit_file").parameters["required"],
            json!(["file_path", "old_string", "new_string"])
        );
        assert_eq!(
            schema("multi_edit").parameters["properties"]["edits"]["items"]["required"],
            json!(["old_string", "new_string"])
        );
        assert_eq!(
            schema("apply_patch").parameters["required"],
            json!(["patch"])
        );
        assert_eq!(
            schema("grep").parameters["properties"]["mode"]["enum"],
            json!(["literal", "regex"])
        );
        assert_eq!(
            schema("bg_start").parameters["required"],
            json!(["command"])
        );
        assert_eq!(
            schema("bg_output").parameters["properties"]["all"]["type"],
            json!("boolean")
        );
        assert_eq!(schema("bg_stop").parameters["required"], json!(["id"]));
        assert_eq!(
            schema("todo_write").parameters["required"],
            json!(["todos"])
        );
    }

    #[test]
    fn action_tool_schemas_do_not_publish_unimplemented_ask_user() {
        let names: Vec<String> = action_tool_schemas()
            .into_iter()
            .map(|schema| schema.name)
            .collect();

        assert!(!names.contains(&"ask_user".to_string()));
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

    #[tokio::test]
    async fn delegated_shell_requires_explicit_shell_allow_mode() {
        let root = test_workspace();
        let mut executor = ToolExecutor::new(PermissionPolicy::workspace_write(root));

        let result = executor
            .execute_tool_call(
                "call-1",
                "bash",
                &json!({"command": if cfg!(windows) { "Write-Output blocked" } else { "echo blocked" }}),
            )
            .await;

        assert!(matches!(
            result,
            ClientFrame::ToolResult {
                status: ToolStatus::Error,
                ..
            }
        ));
        if let ClientFrame::ToolResult { result, .. } = result {
            assert!(result.contains("requires"));
        }
    }

    #[test]
    fn tool_output_redaction_replaces_secret_values_everywhere() {
        let output = ToolOutput {
            content: "token local-secret".to_string(),
            is_error: false,
            stdout: vec!["stdout local-secret".to_string()],
            stderr: vec!["stderr local-secret".to_string()],
        };

        let redacted =
            redaction::redact_tool_output_with_values(output, &["local-secret".to_string()]);

        assert_eq!(redacted.content, "token [redacted]");
        assert_eq!(redacted.stdout, vec!["stdout [redacted]"]);
        assert_eq!(redacted.stderr, vec!["stderr [redacted]"]);
    }

    fn test_workspace() -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("animus-tools-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }
}
