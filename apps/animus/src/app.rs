#![allow(dead_code)]

use crate::approvals::{ApprovalDecision, ApprovalOutcome, ApprovalState, PendingApproval};
use crate::commands::{CommandEffect, CommandInvocation, SlashCommand};
use crate::config::{AnimusConfig, DEFAULT_SERVER_URL};
use crate::protocol::ServerFrame;
use crate::spawns::SpawnState;
use crate::transcript::{append_assistant_token, finish_streaming_assistant, TranscriptItem};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectionState {
    Disconnected,
    Connecting,
    Authenticating,
    Connected,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RunState {
    pub current_run_id: Option<i64>,
    pub thread_id: Option<i64>,
}

#[derive(Debug, Clone)]
pub struct AppState {
    pub config: AnimusConfig,
    pub connection: ConnectionState,
    pub run: RunState,
    pub transcript: Vec<TranscriptItem>,
    pub input: String,
    pub errors: Vec<String>,
    pub should_quit: bool,
    pub permission_mode: String,
    pub approval_mode: String,
    pub spawn_count: usize,
    pub background_process_count: usize,
    pub approvals: ApprovalState,
    pub spawns: SpawnState,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AppEvent {
    ConnectionChanged(ConnectionState),
    UserSubmitted(String),
    ServerFrame(ServerFrame),
    InputChanged(String),
    Notice(String),
    Quit,
}

impl AppState {
    pub fn new(config: AnimusConfig) -> Self {
        Self {
            config,
            connection: ConnectionState::Disconnected,
            run: RunState::default(),
            transcript: Vec::new(),
            input: String::new(),
            errors: Vec::new(),
            should_quit: false,
            permission_mode: "workspace-write".to_string(),
            approval_mode: "manual".to_string(),
            spawn_count: 0,
            background_process_count: 0,
            approvals: ApprovalState::default(),
            spawns: SpawnState::default(),
        }
    }

    pub fn for_test() -> Self {
        Self::new(AnimusConfig {
            server_url: DEFAULT_SERVER_URL.to_string(),
            workspace: ".".into(),
            unlock_token: None,
            username: None,
            password: None,
        })
    }

    pub fn apply(&mut self, event: AppEvent) {
        match event {
            AppEvent::ConnectionChanged(state) => {
                self.connection = state;
            }
            AppEvent::InputChanged(input) => {
                self.input = input;
            }
            AppEvent::UserSubmitted(content) => {
                self.transcript.push(TranscriptItem::User { content });
                self.input.clear();
            }
            AppEvent::Notice(message) => {
                self.transcript.push(TranscriptItem::Notice { message });
            }
            AppEvent::Quit => {
                self.should_quit = true;
            }
            AppEvent::ServerFrame(frame) => self.apply_server_frame(frame),
        }
    }

    fn apply_server_frame(&mut self, frame: ServerFrame) {
        match frame {
            ServerFrame::AuthOk { user } => {
                self.connection = ConnectionState::Connected;
                self.transcript.push(TranscriptItem::Session {
                    message: format!("authenticated as {}", user.username),
                });
            }
            ServerFrame::RunStarted { run_id, thread_id } => {
                self.run.current_run_id = Some(run_id);
                self.run.thread_id = thread_id;
                self.transcript.push(TranscriptItem::Session {
                    message: format!("run {run_id} started"),
                });
            }
            ServerFrame::StreamToken { token } => {
                append_assistant_token(&mut self.transcript, &token);
            }
            ServerFrame::Reasoning { content } => {
                self.transcript.push(TranscriptItem::Reasoning { content });
            }
            ServerFrame::ToolExecute {
                tool_call_id,
                tool_name,
                args,
            }
            | ServerFrame::ToolCall {
                tool_call_id,
                tool_name,
                args,
            } => {
                self.transcript.push(TranscriptItem::ToolCall {
                    tool_call_id,
                    tool_name,
                    args,
                });
            }
            ServerFrame::ToolReturn {
                tool_call_id,
                tool_name,
                result,
                is_error,
            } => {
                self.transcript.push(TranscriptItem::ToolReturn {
                    tool_call_id,
                    tool_name,
                    result,
                    is_error: is_error.unwrap_or(false),
                });
            }
            ServerFrame::ApprovalRequired {
                run_id,
                tool_call_id,
                tool_name,
                args,
            } => {
                self.approvals.set_pending(PendingApproval::new(
                    run_id,
                    tool_call_id.clone(),
                    tool_name.clone(),
                    args.clone(),
                ));
                self.approval_mode = "pending".to_string();
                self.transcript.push(TranscriptItem::Approval {
                    run_id,
                    tool_call_id,
                    tool_name,
                    args,
                });
            }
            ServerFrame::Cancelled { run_id } => {
                if self.run.current_run_id == Some(run_id) {
                    self.run.current_run_id = None;
                }
                self.transcript.push(TranscriptItem::Session {
                    message: format!("run {run_id} cancelled"),
                });
            }
            ServerFrame::TurnComplete {
                response,
                model,
                provider,
                tools_used,
            } => {
                if !response.is_empty() {
                    self.transcript.push(TranscriptItem::Assistant {
                        content: response,
                        streaming: false,
                    });
                } else {
                    finish_streaming_assistant(&mut self.transcript);
                }
                self.run.current_run_id = None;
                self.transcript.push(TranscriptItem::Session {
                    message: format!(
                        "turn complete via {provider}/{model}; tools: {}",
                        tools_used.join(", ")
                    ),
                });
            }
            ServerFrame::SpawnEvent { spawn } => {
                self.spawns.apply_frame(spawn.clone());
                self.spawn_count = self.spawns.active_count();
                self.transcript.push(TranscriptItem::Notice {
                    message: format!(
                        "background process {} [{}]",
                        spawn.id,
                        spawn.status.as_str()
                    ),
                });
            }
            ServerFrame::Error { message, code } => {
                self.errors.push(format!("{code}: {message}"));
                self.transcript
                    .push(TranscriptItem::Error { code, message });
            }
            ServerFrame::Unknown => {
                self.transcript.push(TranscriptItem::Notice {
                    message: "ignored unknown server frame".to_string(),
                });
            }
        }
    }

    pub fn decide_approval(&mut self, decision: ApprovalDecision) -> Option<ApprovalOutcome> {
        let outcome = self.approvals.decide(decision)?;
        if self.approvals.pending().is_none() {
            self.approval_mode = "manual".to_string();
        }
        if let Some(remembered) = &outcome.remembered {
            self.transcript.push(TranscriptItem::Notice {
                message: format!("remembered approval for session: {remembered:?}"),
            });
        }
        Some(outcome)
    }

    pub fn handle_command(&mut self, invocation: CommandInvocation) -> CommandEffect {
        match invocation.command {
            SlashCommand::Help => {
                self.transcript.push(TranscriptItem::Notice {
                    message: "commands: /help /clear /cancel /reconnect /permissions /status /diff /spawns /cancel-spawn /quit".to_string(),
                });
                CommandEffect::ShowHelp
            }
            SlashCommand::Clear => {
                self.transcript.clear();
                CommandEffect::ClearTranscript
            }
            SlashCommand::Cancel => match self.run.current_run_id {
                Some(run_id) => CommandEffect::CancelRun { run_id },
                None => {
                    self.transcript.push(TranscriptItem::Notice {
                        message: "no active run to cancel".to_string(),
                    });
                    CommandEffect::None
                }
            },
            SlashCommand::Reconnect => CommandEffect::Reconnect,
            SlashCommand::Permissions => {
                if !invocation.args.is_empty() {
                    self.permission_mode = invocation.args.clone();
                }
                CommandEffect::SetPermissions(self.permission_mode.clone())
            }
            SlashCommand::Status => {
                self.transcript.push(TranscriptItem::Notice {
                    message: format!(
                        "status: connection={:?} run={:?}",
                        self.connection, self.run.current_run_id
                    ),
                });
                CommandEffect::ShowStatus
            }
            SlashCommand::Diff => CommandEffect::ShowDiff,
            SlashCommand::Spawns => {
                self.transcript.push(TranscriptItem::Notice {
                    message: self.spawns.render_list(),
                });
                CommandEffect::ShowSpawns
            }
            SlashCommand::CancelSpawn => {
                let id = invocation.args;
                match self.spawns.cancel_spawn(&id) {
                    Ok(()) => CommandEffect::CancelSpawn { id },
                    Err(message) => {
                        self.transcript.push(TranscriptItem::Notice { message });
                        CommandEffect::CancelSpawnUnsupported { id }
                    }
                }
            }
            SlashCommand::Quit => {
                self.should_quit = true;
                CommandEffect::Quit
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::protocol::ServerFrame;
    use crate::transcript::TranscriptItem;

    #[test]
    fn reducer_tracks_run_and_streams_assistant_tokens() {
        let mut app = AppState::for_test();

        app.apply(AppEvent::ServerFrame(ServerFrame::RunStarted {
            run_id: 42,
            thread_id: Some(9),
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::StreamToken {
            token: "hel".to_string(),
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::StreamToken {
            token: "lo".to_string(),
        }));

        assert_eq!(app.run.current_run_id, Some(42));
        assert_eq!(app.run.thread_id, Some(9));
        assert!(matches!(
            app.transcript.last(),
            Some(TranscriptItem::Assistant { content, streaming }) if content == "hello" && *streaming
        ));
    }

    #[test]
    fn reducer_records_reasoning_tools_errors_cancel_and_completion() {
        let mut app = AppState::for_test();

        app.apply(AppEvent::ServerFrame(ServerFrame::RunStarted {
            run_id: 7,
            thread_id: None,
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::Reasoning {
            content: "checking".to_string(),
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::ToolCall {
            tool_call_id: "call-1".to_string(),
            tool_name: "bash".to_string(),
            args: json!({"command":"pwd"}),
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::ToolReturn {
            tool_call_id: "call-1".to_string(),
            tool_name: "bash".to_string(),
            result: "/repo".to_string(),
            is_error: Some(false),
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::Error {
            message: "boom".to_string(),
            code: "AGENT_ERROR".to_string(),
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::Cancelled { run_id: 7 }));
        app.apply(AppEvent::ServerFrame(ServerFrame::TurnComplete {
            response: String::new(),
            model: "model-a".to_string(),
            provider: "provider-a".to_string(),
            tools_used: vec!["bash".to_string()],
        }));

        assert_eq!(app.run.current_run_id, None);
        assert!(app
            .transcript
            .iter()
            .any(|item| item.render_plain().contains("reasoning: checking")));
        assert!(app
            .transcript
            .iter()
            .any(|item| item.render_plain().contains("tool bash call-1")));
        assert!(app
            .transcript
            .iter()
            .any(|item| item.render_plain().contains("error AGENT_ERROR: boom")));
        assert!(app
            .transcript
            .iter()
            .any(|item| item.render_plain().contains("run 7 cancelled")));
    }

    #[test]
    fn user_submission_adds_user_transcript_and_busy_guard() {
        let mut app = AppState::for_test();

        app.apply(AppEvent::UserSubmitted("hello anima".to_string()));

        assert_eq!(
            app.transcript.first(),
            Some(&TranscriptItem::User {
                content: "hello anima".to_string()
            })
        );
        assert_eq!(app.input, "");
    }

    #[test]
    fn reducer_handles_connection_input_and_notice_events() {
        let mut app = AppState::for_test();

        app.apply(AppEvent::ConnectionChanged(ConnectionState::Connecting));
        assert_eq!(app.connection, ConnectionState::Connecting);

        app.apply(AppEvent::ConnectionChanged(ConnectionState::Authenticating));
        assert_eq!(app.connection, ConnectionState::Authenticating);

        app.apply(AppEvent::InputChanged("draft".to_string()));
        app.apply(AppEvent::Notice("ready".to_string()));

        assert_eq!(app.input, "draft");
        assert!(app
            .transcript
            .iter()
            .any(|item| item.render_plain() == "notice: ready"));
    }

    #[test]
    fn command_routing_clears_cancels_and_quits() {
        let mut app = AppState::for_test();
        app.apply(AppEvent::UserSubmitted("hello".to_string()));
        app.apply(AppEvent::ServerFrame(ServerFrame::RunStarted {
            run_id: 42,
            thread_id: None,
        }));

        assert_eq!(
            app.handle_command(crate::commands::parse_command("/cancel").unwrap()),
            crate::commands::CommandEffect::CancelRun { run_id: 42 }
        );
        assert_eq!(
            app.handle_command(crate::commands::parse_command("/clear").unwrap()),
            crate::commands::CommandEffect::ClearTranscript
        );
        assert!(app.transcript.is_empty());
        assert_eq!(
            app.handle_command(crate::commands::parse_command("/quit").unwrap()),
            crate::commands::CommandEffect::Quit
        );
        assert!(app.should_quit);
    }

    #[test]
    fn approval_required_sets_pending_state_and_decision_frame() {
        let mut app = AppState::for_test();
        app.apply(AppEvent::ServerFrame(ServerFrame::ApprovalRequired {
            run_id: 42,
            tool_call_id: "call-1".to_string(),
            tool_name: "bash".to_string(),
            args: serde_json::json!({"command":"git status"}),
        }));

        assert_eq!(app.approval_mode, "pending");
        assert!(app.approvals.pending().is_some());
        assert!(app
            .transcript
            .iter()
            .any(|item| item.render_plain().contains("approval 42 call-1 bash")));

        let frame = app
            .decide_approval(crate::approvals::ApprovalDecision::Approve)
            .unwrap()
            .frame;

        assert_eq!(
            frame,
            crate::protocol::ClientFrame::ApprovalResponse {
                run_id: 42,
                tool_call_id: "call-1".to_string(),
                approved: true,
                reason: None,
            }
        );
        assert_eq!(app.approval_mode, "manual");
    }

    #[test]
    fn spawn_events_update_background_process_state_and_commands_render_list() {
        let mut app = AppState::for_test();
        app.apply(AppEvent::ServerFrame(ServerFrame::SpawnEvent {
            spawn: crate::protocol::SpawnFrame {
                id: "spawn-1".to_string(),
                status: crate::protocol::SpawnStatus::Running,
                task: Some("scan files".to_string()),
                started_at: None,
                completed_at: None,
                extra: Default::default(),
            },
        }));

        assert_eq!(app.spawn_count, 1);
        assert_eq!(
            app.handle_command(crate::commands::parse_command("/spawns").unwrap()),
            crate::commands::CommandEffect::ShowSpawns
        );
        assert!(app
            .transcript
            .iter()
            .any(|item| item.render_plain().contains("background process spawn-1")));
        assert_eq!(
            app.handle_command(crate::commands::parse_command("/cancel-spawn spawn-1").unwrap()),
            crate::commands::CommandEffect::CancelSpawnUnsupported {
                id: "spawn-1".to_string(),
            }
        );
    }
}
