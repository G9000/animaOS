use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AuthUser {
    pub id: i64,
    pub username: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SpawnFrame {
    pub id: String,
    pub status: String,
    #[serde(default)]
    pub task: Option<String>,
    #[serde(default, rename = "startedAt")]
    pub started_at: Option<String>,
    #[serde(default, rename = "completedAt")]
    pub completed_at: Option<String>,
    #[serde(default, flatten)]
    pub extra: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ServerFrame {
    #[serde(rename = "auth_ok")]
    AuthOk { user: AuthUser },
    #[serde(rename = "run_started")]
    RunStarted {
        run_id: i64,
        #[serde(default)]
        thread_id: Option<i64>,
    },
    #[serde(rename = "stream_token")]
    StreamToken { token: String },
    #[serde(rename = "reasoning")]
    Reasoning { content: String },
    #[serde(rename = "tool_execute")]
    ToolExecute {
        tool_call_id: String,
        tool_name: String,
        args: Value,
    },
    #[serde(rename = "tool_call")]
    ToolCall {
        tool_call_id: String,
        tool_name: String,
        args: Value,
    },
    #[serde(rename = "tool_return")]
    ToolReturn {
        tool_call_id: String,
        tool_name: String,
        result: String,
        #[serde(default)]
        is_error: Option<bool>,
    },
    #[serde(rename = "approval_required")]
    ApprovalRequired {
        run_id: i64,
        tool_call_id: String,
        tool_name: String,
        args: Value,
    },
    #[serde(rename = "cancelled")]
    Cancelled { run_id: i64 },
    #[serde(rename = "turn_complete")]
    TurnComplete {
        response: String,
        model: String,
        provider: String,
        tools_used: Vec<String>,
    },
    #[serde(rename = "spawn_event")]
    SpawnEvent { spawn: SpawnFrame },
    #[serde(rename = "error")]
    Error { message: String, code: String },
    #[serde(other)]
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolSchema {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ToolStatus {
    Success,
    Error,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ClientFrame {
    #[serde(rename = "auth")]
    Auth {
        #[serde(rename = "unlockToken", skip_serializing_if = "Option::is_none")]
        unlock_token: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        username: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        password: Option<String>,
    },
    #[serde(rename = "tool_schemas")]
    ToolSchemas { tools: Vec<ToolSchema> },
    #[serde(rename = "user_message")]
    UserMessage { message: String },
    #[serde(rename = "tool_result")]
    ToolResult {
        tool_call_id: String,
        status: ToolStatus,
        result: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        stdout: Option<Vec<String>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        stderr: Option<Vec<String>>,
    },
    #[serde(rename = "approval_response")]
    ApprovalResponse {
        run_id: i64,
        tool_call_id: String,
        approved: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
    },
    #[serde(rename = "cancel")]
    Cancel { run_id: i64 },
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn deserializes_all_server_frame_shapes() {
        let frames = [
            (
                json!({"type":"auth_ok","user":{"id":5,"username":"alice"}}),
                ServerFrame::AuthOk {
                    user: AuthUser {
                        id: 5,
                        username: "alice".to_string(),
                    },
                },
            ),
            (
                json!({"type":"run_started","run_id":7,"thread_id":9}),
                ServerFrame::RunStarted {
                    run_id: 7,
                    thread_id: Some(9),
                },
            ),
            (
                json!({"type":"stream_token","token":"hi"}),
                ServerFrame::StreamToken {
                    token: "hi".to_string(),
                },
            ),
            (
                json!({"type":"reasoning","content":"thinking"}),
                ServerFrame::Reasoning {
                    content: "thinking".to_string(),
                },
            ),
            (
                json!({"type":"tool_execute","tool_call_id":"call-1","tool_name":"bash","args":{"command":"pwd"}}),
                ServerFrame::ToolExecute {
                    tool_call_id: "call-1".to_string(),
                    tool_name: "bash".to_string(),
                    args: json!({"command":"pwd"}),
                },
            ),
            (
                json!({"type":"tool_call","tool_call_id":"call-1","tool_name":"bash","args":{}}),
                ServerFrame::ToolCall {
                    tool_call_id: "call-1".to_string(),
                    tool_name: "bash".to_string(),
                    args: json!({}),
                },
            ),
            (
                json!({"type":"tool_return","tool_call_id":"call-1","tool_name":"bash","result":"ok","is_error":false}),
                ServerFrame::ToolReturn {
                    tool_call_id: "call-1".to_string(),
                    tool_name: "bash".to_string(),
                    result: "ok".to_string(),
                    is_error: Some(false),
                },
            ),
            (
                json!({"type":"approval_required","run_id":7,"tool_call_id":"call-1","tool_name":"bash","args":{}}),
                ServerFrame::ApprovalRequired {
                    run_id: 7,
                    tool_call_id: "call-1".to_string(),
                    tool_name: "bash".to_string(),
                    args: json!({}),
                },
            ),
            (
                json!({"type":"cancelled","run_id":7}),
                ServerFrame::Cancelled { run_id: 7 },
            ),
            (
                json!({"type":"turn_complete","response":"","model":"m","provider":"p","tools_used":["bash"]}),
                ServerFrame::TurnComplete {
                    response: String::new(),
                    model: "m".to_string(),
                    provider: "p".to_string(),
                    tools_used: vec!["bash".to_string()],
                },
            ),
            (
                json!({"type":"spawn_event","spawn":{"id":"spawn-1","status":"running","task":"search"}}),
                ServerFrame::SpawnEvent {
                    spawn: SpawnFrame {
                        id: "spawn-1".to_string(),
                        status: "running".to_string(),
                        task: Some("search".to_string()),
                        started_at: None,
                        completed_at: None,
                        extra: Default::default(),
                    },
                },
            ),
            (
                json!({"type":"error","message":"boom","code":"AGENT_ERROR"}),
                ServerFrame::Error {
                    message: "boom".to_string(),
                    code: "AGENT_ERROR".to_string(),
                },
            ),
        ];

        for (raw, expected) in frames {
            assert_eq!(
                serde_json::from_value::<ServerFrame>(raw).unwrap(),
                expected
            );
        }
    }

    #[test]
    fn unknown_server_frame_is_accepted() {
        let frame: ServerFrame =
            serde_json::from_value(json!({"type":"future_frame","value":1})).unwrap();

        assert_eq!(frame, ServerFrame::Unknown);
    }

    #[test]
    fn serializes_client_frames_with_server_field_names() {
        let frames = [
            (
                ClientFrame::Auth {
                    unlock_token: Some("token".to_string()),
                    username: None,
                    password: None,
                },
                json!({"type":"auth","unlockToken":"token"}),
            ),
            (
                ClientFrame::ToolSchemas {
                    tools: vec![ToolSchema {
                        name: "bash".to_string(),
                        description: "Run shell".to_string(),
                        parameters: json!({"type":"object"}),
                    }],
                },
                json!({"type":"tool_schemas","tools":[{"name":"bash","description":"Run shell","parameters":{"type":"object"}}]}),
            ),
            (
                ClientFrame::UserMessage {
                    message: "hello".to_string(),
                },
                json!({"type":"user_message","message":"hello"}),
            ),
            (
                ClientFrame::ToolResult {
                    tool_call_id: "call-1".to_string(),
                    status: ToolStatus::Success,
                    result: "ok".to_string(),
                    stdout: Some(vec!["ok".to_string()]),
                    stderr: None,
                },
                json!({"type":"tool_result","tool_call_id":"call-1","status":"success","result":"ok","stdout":["ok"]}),
            ),
            (
                ClientFrame::ApprovalResponse {
                    run_id: 7,
                    tool_call_id: "call-1".to_string(),
                    approved: false,
                    reason: Some("no".to_string()),
                },
                json!({"type":"approval_response","run_id":7,"tool_call_id":"call-1","approved":false,"reason":"no"}),
            ),
            (
                ClientFrame::Cancel { run_id: 7 },
                json!({"type":"cancel","run_id":7}),
            ),
        ];

        for (frame, expected) in frames {
            assert_eq!(serde_json::to_value(frame).unwrap(), expected);
        }
    }
}
