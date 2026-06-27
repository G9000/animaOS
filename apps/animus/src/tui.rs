#![allow(dead_code)]

use std::collections::VecDeque;
use std::io::{self, Stdout};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::widgets::{Block, Borders, List, ListItem, Paragraph};
use ratatui::Terminal;

use tokio::sync::{mpsc, Mutex};

use crate::app::{AppEvent, AppState, ConnectionState};
use crate::approvals::ApprovalDecision;
use crate::client::{reconnect_delay, AnimaWsClient};
use crate::commands::{parse_command, CommandEffect};
use crate::input::InputBuffer;
use crate::permissions::PermissionPolicy;
use crate::protocol::{AuthUser, ClientFrame, ServerFrame};
use crate::tools::{action_tool_schemas, ToolExecutor};

type AnimusTerminal = Terminal<CrosstermBackend<Stdout>>;

pub struct TerminalSession {
    terminal: AnimusTerminal,
}

#[derive(Debug, Clone, PartialEq)]
pub enum OutboundMessage {
    Frame(ClientFrame),
    Reconnect,
    SetPermissions(String),
}

#[derive(Debug)]
enum WsEvent {
    ConnectionChanged(ConnectionState),
    Authenticated(AuthUser),
    Frame(ServerFrame),
    Disconnected(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ReconnectOutboundAction {
    RetryNow,
    Shutdown,
}

impl TerminalSession {
    pub fn enter() -> Result<Self> {
        enable_raw_mode()?;
        let mut stdout = io::stdout();
        execute!(stdout, EnterAlternateScreen)?;
        let backend = CrosstermBackend::new(stdout);
        let terminal = Terminal::new(backend)?;
        Ok(Self { terminal })
    }
}

impl Drop for TerminalSession {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let _ = execute!(self.terminal.backend_mut(), LeaveAlternateScreen);
    }
}

pub async fn run_tui(mut app: AppState) -> Result<()> {
    let mut session = TerminalSession::enter()?;
    let (ws_tx, mut ws_rx) = mpsc::unbounded_channel();
    let (outbound_tx, outbound_rx) = mpsc::unbounded_channel();
    let config = app.config.clone();
    let websocket_task = tokio::spawn(websocket_driver(config, ws_tx, outbound_rx));
    let tool_executor = Arc::new(Mutex::new(ToolExecutor::new(
        PermissionPolicy::workspace_write(app.config.workspace.clone()),
    )));
    let mut input = InputBuffer::default();

    loop {
        while let Ok(event) = ws_rx.try_recv() {
            handle_ws_event(&mut app, tool_executor.clone(), &outbound_tx, event).await;
        }

        session.terminal.draw(|frame| draw_app(frame, &app))?;

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                for outgoing in handle_key(&mut app, &mut input, key) {
                    handle_outbound_message(&mut app, &tool_executor, &outbound_tx, outgoing).await;
                }
            }
        }

        if app.should_quit {
            break;
        }
    }

    drop(outbound_tx);
    websocket_task.abort();
    Ok(())
}

async fn handle_outbound_message(
    app: &mut AppState,
    tool_executor: &Arc<Mutex<ToolExecutor>>,
    outbound_tx: &mpsc::UnboundedSender<OutboundMessage>,
    outgoing: OutboundMessage,
) {
    match outgoing {
        OutboundMessage::Frame(frame) => {
            let _ = outbound_tx.send(OutboundMessage::Frame(frame));
        }
        OutboundMessage::Reconnect => {
            let _ = outbound_tx.send(OutboundMessage::Reconnect);
        }
        OutboundMessage::SetPermissions(mode) => {
            match PermissionPolicy::from_mode(app.config.workspace.clone(), &mode) {
                Ok(policy) => {
                    tool_executor.lock().await.set_policy(policy);
                    app.apply(AppEvent::Notice(format!("permission mode: {mode}")));
                }
                Err(message) => {
                    app.apply(AppEvent::Notice(message));
                }
            }
        }
    }
}

async fn websocket_driver(
    config: crate::config::AnimusConfig,
    ui_tx: mpsc::UnboundedSender<WsEvent>,
    mut outbound_rx: mpsc::UnboundedReceiver<OutboundMessage>,
) {
    let mut attempt = 0u32;
    let mut pending_frames: VecDeque<ClientFrame> = VecDeque::new();

    loop {
        if ui_tx
            .send(WsEvent::ConnectionChanged(ConnectionState::Connecting))
            .is_err()
        {
            return;
        }

        match AnimaWsClient::connect(&config, action_tool_schemas()).await {
            Ok(mut client) => {
                attempt = 0;
                if ui_tx
                    .send(WsEvent::Authenticated(client.auth_user().clone()))
                    .is_err()
                {
                    return;
                }

                let mut flush_failed = false;
                while let Some(frame) = pending_frames.pop_front() {
                    if let Err(err) = client.send_frame(frame.clone()).await {
                        pending_frames.push_front(frame);
                        let _ = ui_tx.send(WsEvent::Disconnected(format!(
                            "websocket send failed: {err}"
                        )));
                        flush_failed = true;
                        break;
                    }
                }

                if !flush_failed {
                    loop {
                        tokio::select! {
                            outgoing = outbound_rx.recv() => {
                                match outgoing {
                                    Some(OutboundMessage::Frame(frame)) => {
                                        if let Err(err) = client.send_frame(frame.clone()).await {
                                            pending_frames.push_front(frame);
                                            let _ = ui_tx.send(WsEvent::Disconnected(format!("websocket send failed: {err}")));
                                            break;
                                        }
                                    }
                                    Some(OutboundMessage::Reconnect) => {
                                        let _ = ui_tx.send(WsEvent::Disconnected("reconnecting to ANIMA".to_string()));
                                        break;
                                    }
                                    Some(OutboundMessage::SetPermissions(_)) => {}
                                    None => return,
                                }
                            }
                            frame = client.next_frame() => {
                                match frame {
                                    Ok(Some(frame)) => {
                                        if ui_tx.send(WsEvent::Frame(frame)).is_err() {
                                            return;
                                        }
                                    }
                                    Ok(None) => {
                                        let _ = ui_tx.send(WsEvent::Disconnected("ANIMA websocket closed".to_string()));
                                        break;
                                    }
                                    Err(err) => {
                                        let _ = ui_tx.send(WsEvent::Disconnected(format!("websocket receive failed: {err}")));
                                        break;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Err(err) => {
                let _ = ui_tx.send(WsEvent::Disconnected(format!("connection failed: {err}")));
            }
        }

        attempt = attempt.saturating_add(1);
        let delay = reconnect_delay(attempt);
        tokio::select! {
            _ = tokio::time::sleep(delay) => {}
            outgoing = outbound_rx.recv() => {
                if handle_reconnect_outbound(outgoing, &mut pending_frames)
                    == ReconnectOutboundAction::Shutdown
                {
                    return;
                }
            }
        }
    }
}

fn handle_reconnect_outbound(
    outgoing: Option<OutboundMessage>,
    pending_frames: &mut VecDeque<ClientFrame>,
) -> ReconnectOutboundAction {
    match outgoing {
        Some(OutboundMessage::Frame(frame)) => {
            pending_frames.push_back(frame);
            ReconnectOutboundAction::RetryNow
        }
        Some(OutboundMessage::Reconnect) | Some(OutboundMessage::SetPermissions(_)) => {
            ReconnectOutboundAction::RetryNow
        }
        None => ReconnectOutboundAction::Shutdown,
    }
}

async fn handle_ws_event(
    app: &mut AppState,
    tool_executor: Arc<Mutex<ToolExecutor>>,
    outbound_tx: &mpsc::UnboundedSender<OutboundMessage>,
    event: WsEvent,
) {
    match event {
        WsEvent::ConnectionChanged(state) => {
            app.apply(AppEvent::ConnectionChanged(state));
        }
        WsEvent::Authenticated(user) => {
            app.apply(AppEvent::ServerFrame(ServerFrame::AuthOk { user }));
        }
        WsEvent::Frame(frame) => {
            spawn_tool_execution(tool_executor, outbound_tx.clone(), frame.clone());
            let auto_approval = app.handle_server_frame(frame);
            if let Some(frame) = auto_approval {
                let _ = outbound_tx.send(OutboundMessage::Frame(frame));
            }
        }
        WsEvent::Disconnected(message) => {
            app.apply(AppEvent::ConnectionChanged(ConnectionState::Disconnected));
            app.apply(AppEvent::Notice(message));
        }
    }
}

fn spawn_tool_execution(
    tool_executor: Arc<Mutex<ToolExecutor>>,
    outbound_tx: mpsc::UnboundedSender<OutboundMessage>,
    frame: ServerFrame,
) {
    if !matches!(frame, ServerFrame::ToolExecute { .. }) {
        return;
    }
    tokio::spawn(async move {
        let tool_result = {
            let mut tool_executor = tool_executor.lock().await;
            tool_executor.execute_frame(&frame).await
        };
        if let Some(frame) = tool_result {
            let _ = outbound_tx.send(OutboundMessage::Frame(frame));
        }
    });
}

fn draw_app(frame: &mut ratatui::Frame<'_>, app: &AppState) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Min(1),
            Constraint::Length(bottom_pane_height(app)),
        ])
        .split(frame.area());

    let visible_transcript_rows = usize::from(chunks[0].height.saturating_sub(2));
    let items: Vec<ListItem> = transcript_tail_rows(app, visible_transcript_rows)
        .into_iter()
        .map(ListItem::new)
        .collect();
    let transcript = List::new(items).block(Block::default().title("Animus").borders(Borders::ALL));
    frame.render_widget(transcript, chunks[0]);

    let status = status_line(app);
    let approval = approval_prompt_text(app);
    let body = if approval.is_empty() {
        format!("{status}\ninput: {}", app.input)
    } else {
        format!("{status}\n{approval}\ninput: {}", app.input)
    };
    let input = Paragraph::new(body).block(Block::default().borders(Borders::ALL));
    frame.render_widget(input, chunks[1]);
}

fn bottom_pane_height(app: &AppState) -> u16 {
    let content_rows = 2 + u16::try_from(approval_prompt_rows(app).len()).unwrap_or(0);
    content_rows + 2
}

pub fn render_to_text(app: &AppState, width: u16, height: u16) -> Vec<String> {
    let max_width = usize::from(width.max(1));
    let max_rows = usize::from(height.max(1));
    let mut rows = Vec::new();
    rows.push(truncate_line(status_line(app), max_width));
    if rows.len() >= max_rows {
        return rows;
    }

    let approval_rows = approval_prompt_rows(app);
    let reserved_bottom_rows = approval_rows.len() + 1;
    let transcript_capacity = (max_rows - rows.len()).saturating_sub(reserved_bottom_rows);
    rows.extend(
        transcript_tail_rows(app, transcript_capacity)
            .into_iter()
            .map(|row| truncate_line(row, max_width)),
    );

    for row in approval_rows {
        if rows.len() + 1 >= max_rows {
            break;
        }
        rows.push(truncate_line(row, max_width));
    }
    if rows.len() < max_rows {
        rows.push(truncate_line(format!("input: {}", app.input), max_width));
    }
    rows
}

fn transcript_tail_rows(app: &AppState, max_rows: usize) -> Vec<String> {
    if max_rows == 0 {
        return Vec::new();
    }
    let mut rows = Vec::new();
    for item in &app.transcript {
        rows.extend(item.render_plain().lines().map(ToString::to_string));
    }
    let skip = rows.len().saturating_sub(max_rows);
    rows.into_iter().skip(skip).collect()
}

fn truncate_line(mut line: String, max_width: usize) -> String {
    if line.len() > max_width {
        line.truncate(max_width);
    }
    line
}

pub fn status_line(app: &AppState) -> String {
    format!(
        "conn: {:?} | run: {} | thread: {} | permission: {} | approval: {} | spawns: {} | bg: {} | cwd: {}",
        app.connection,
        app.run
            .current_run_id
            .map(|id| id.to_string())
            .unwrap_or_else(|| "-".to_string()),
        app.run
            .thread_id
            .map(|id| id.to_string())
            .unwrap_or_else(|| "-".to_string()),
        app.permission_mode,
        app.approval_mode,
        app.spawn_count,
        app.background_process_count,
        app.config.workspace.display(),
    )
}

fn approval_prompt_text(app: &AppState) -> String {
    approval_prompt_rows(app).join("\n")
}

fn approval_prompt_rows(app: &AppState) -> Vec<String> {
    let Some(pending) = app.approvals.pending() else {
        return Vec::new();
    };
    vec![
        format!("approve: {}", pending.summary()),
        "[a] approve [s] session [d] deny [esc] cancel".to_string(),
    ]
}

fn handle_key(app: &mut AppState, input: &mut InputBuffer, key: KeyEvent) -> Vec<OutboundMessage> {
    if is_quit_key(key) {
        app.apply(AppEvent::Quit);
        return Vec::new();
    }

    if app.approvals.pending().is_some() {
        match key.code {
            KeyCode::Char('a') => {
                return approval_outbound(app, ApprovalDecision::Approve);
            }
            KeyCode::Char('s') => {
                return approval_outbound(app, ApprovalDecision::ApproveForSession);
            }
            KeyCode::Char('d') => {
                return approval_outbound(
                    app,
                    ApprovalDecision::Deny {
                        reason: "denied by user".to_string(),
                    },
                );
            }
            KeyCode::Esc => {
                return approval_outbound(app, ApprovalDecision::Cancel);
            }
            _ => {}
        }
    }

    match key.code {
        KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
            input.insert_char(ch);
            sync_input(app, input);
        }
        KeyCode::Backspace => {
            input.backspace();
            sync_input(app, input);
        }
        KeyCode::Delete => {
            input.delete();
            sync_input(app, input);
        }
        KeyCode::Left => {
            input.move_left();
            sync_input(app, input);
        }
        KeyCode::Right => {
            input.move_right();
            sync_input(app, input);
        }
        KeyCode::Home => {
            input.move_home();
            sync_input(app, input);
        }
        KeyCode::End => {
            input.move_end();
            sync_input(app, input);
        }
        KeyCode::Up => {
            input.history_previous();
            sync_input(app, input);
        }
        KeyCode::Down => {
            input.history_next();
            sync_input(app, input);
        }
        KeyCode::Enter if key.modifiers.contains(KeyModifiers::SHIFT) => {
            input.insert_newline();
            sync_input(app, input);
        }
        KeyCode::Enter => {
            let Some(message) = input.submit() else {
                sync_input(app, input);
                return Vec::new();
            };
            if message.trim_start().starts_with('/') {
                match parse_command(&message) {
                    Ok(command) => {
                        let effect = app.handle_command(command);
                        sync_input(app, input);
                        return command_outbound(effect);
                    }
                    Err(err) => {
                        app.apply(AppEvent::Notice(format!("command error: {err:?}")));
                        sync_input(app, input);
                    }
                }
            } else {
                app.apply(AppEvent::UserSubmitted(message.clone()));
                sync_input(app, input);
                return vec![OutboundMessage::Frame(ClientFrame::UserMessage { message })];
            }
        }
        _ => {}
    }

    Vec::new()
}

fn sync_input(app: &mut AppState, input: &InputBuffer) {
    app.apply(AppEvent::InputChanged(input.text().to_string()));
}

fn approval_outbound(app: &mut AppState, decision: ApprovalDecision) -> Vec<OutboundMessage> {
    app.decide_approval(decision)
        .map(|outcome| vec![OutboundMessage::Frame(outcome.frame)])
        .unwrap_or_default()
}

fn command_outbound(effect: CommandEffect) -> Vec<OutboundMessage> {
    match effect {
        CommandEffect::CancelRun { run_id } => {
            vec![OutboundMessage::Frame(ClientFrame::Cancel { run_id })]
        }
        CommandEffect::Reconnect => vec![OutboundMessage::Reconnect],
        CommandEffect::SetPermissions(mode) => vec![OutboundMessage::SetPermissions(mode)],
        _ => Vec::new(),
    }
}

fn is_quit_key(key: KeyEvent) -> bool {
    key.modifiers.contains(KeyModifiers::CONTROL)
        && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('d'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{AppEvent, AppState};
    use crate::protocol::ServerFrame;

    #[test]
    fn render_text_includes_transcript_status_and_input() {
        let mut app = AppState::for_test();
        app.apply(AppEvent::UserSubmitted("hello".to_string()));
        app.input = "draft".to_string();
        app.apply(AppEvent::ServerFrame(ServerFrame::RunStarted {
            run_id: 5,
            thread_id: Some(2),
        }));
        app.apply(AppEvent::ServerFrame(ServerFrame::StreamToken {
            token: "hi".to_string(),
        }));

        let rows = render_to_text(&app, 80, 12);

        assert!(rows.iter().any(|row| row.contains("you: hello")));
        assert!(rows.iter().any(|row| row.contains("anima: hi")));
        assert!(rows.iter().any(|row| row.contains("run: 5")));
        assert!(rows.iter().any(|row| row.contains("input: draft")));
    }

    #[test]
    fn status_line_includes_session_operational_state() {
        let mut app = AppState::for_test();
        app.permission_mode = "workspace-write".to_string();
        app.approval_mode = "manual".to_string();
        app.spawn_count = 2;
        app.background_process_count = 1;
        app.run.current_run_id = Some(7);

        let status = status_line(&app);

        assert!(status.contains("conn: Disconnected"));
        assert!(status.contains("permission: workspace-write"));
        assert!(status.contains("approval: manual"));
        assert!(status.contains("run: 7"));
        assert!(status.contains("spawns: 2"));
        assert!(status.contains("bg: 1"));
    }

    #[test]
    fn render_text_shows_pending_approval_controls() {
        let mut app = AppState::for_test();
        app.apply(AppEvent::ServerFrame(ServerFrame::ApprovalRequired {
            run_id: 42,
            tool_call_id: "call-1".to_string(),
            tool_name: "bash".to_string(),
            args: serde_json::json!({"command":"git status"}),
        }));

        let rows = render_to_text(&app, 120, 12);

        assert!(rows
            .iter()
            .any(|row| row.contains("approve: bash git status")));
        assert!(rows
            .iter()
            .any(|row| row.contains("[a] approve [s] session [d] deny")));
    }

    #[test]
    fn render_text_keeps_newest_transcript_rows_visible() {
        let mut app = AppState::for_test();
        for index in 0..10 {
            app.apply(AppEvent::Notice(format!("message-{index}")));
        }
        app.input = "draft".to_string();

        let rows = render_to_text(&app, 80, 6);

        assert!(rows.iter().any(|row| row.contains("message-9")));
        assert!(!rows.iter().any(|row| row.contains("message-0")));
        assert_eq!(rows.last().map(String::as_str), Some("input: draft"));
    }

    #[test]
    fn bottom_pane_reserves_visible_rows_for_input_and_approval_controls() {
        let idle = AppState::for_test();
        assert_eq!(bottom_pane_height(&idle), 4);

        let mut pending = AppState::for_test();
        pending.apply(AppEvent::ServerFrame(ServerFrame::ApprovalRequired {
            run_id: 42,
            tool_call_id: "call-1".to_string(),
            tool_name: "bash".to_string(),
            args: serde_json::json!({"command":"git status"}),
        }));

        assert_eq!(bottom_pane_height(&pending), 6);
    }

    #[test]
    fn enter_on_prompt_sends_user_message_to_anima() {
        let mut app = AppState::for_test();
        let mut input = crate::input::InputBuffer::default();
        input.insert_str("hello anima");

        let outgoing = handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        );

        assert_eq!(
            outgoing,
            vec![OutboundMessage::Frame(
                crate::protocol::ClientFrame::UserMessage {
                    message: "hello anima".to_string(),
                }
            )]
        );
    }

    #[test]
    fn command_input_sends_cancel_and_reconnect_actions() {
        let mut app = AppState::for_test();
        app.run.current_run_id = Some(42);
        let mut input = crate::input::InputBuffer::default();
        input.insert_str("/cancel");

        let cancel = handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        );

        assert_eq!(
            cancel,
            vec![OutboundMessage::Frame(
                crate::protocol::ClientFrame::Cancel { run_id: 42 }
            )]
        );

        input.insert_str("/reconnect");
        let reconnect = handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        );

        assert_eq!(reconnect, vec![OutboundMessage::Reconnect]);
    }

    #[test]
    fn permission_command_requests_executor_policy_update() {
        let mut app = AppState::for_test();
        let mut input = crate::input::InputBuffer::default();
        input.insert_str("/permissions read-only");

        let outgoing = handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        );

        assert_eq!(
            outgoing,
            vec![OutboundMessage::SetPermissions("read-only".to_string())]
        );
        assert_eq!(app.permission_mode, "read-only");
    }

    #[tokio::test]
    async fn permission_update_changes_live_tool_executor_policy() {
        let workspace = std::env::temp_dir().join(format!("animus-tui-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&workspace).unwrap();
        let target = workspace.join("blocked.txt");
        let mut app = AppState::for_test();
        app.config.workspace = workspace.clone();
        let executor = Arc::new(Mutex::new(ToolExecutor::new(
            PermissionPolicy::workspace_write(workspace.clone()),
        )));
        let (outbound_tx, _outbound_rx) = mpsc::unbounded_channel();

        handle_outbound_message(
            &mut app,
            &executor,
            &outbound_tx,
            OutboundMessage::SetPermissions("read-only".to_string()),
        )
        .await;

        let result = {
            let mut executor = executor.lock().await;
            executor
                .execute_tool_call(
                    "call-1",
                    "write_file",
                    &serde_json::json!({"file_path": "blocked.txt", "content": "blocked"}),
                )
                .await
        };

        assert!(matches!(
            result,
            crate::protocol::ClientFrame::ToolResult {
                status: crate::protocol::ToolStatus::Error,
                ..
            }
        ));
        assert!(!target.exists());
    }

    #[tokio::test]
    async fn tool_execution_runs_in_background_without_blocking_ui_event() {
        let workspace = std::env::temp_dir().join(format!("animus-tui-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&workspace).unwrap();
        let mut app = AppState::for_test();
        app.config.workspace = workspace.clone();
        let tool_executor = std::sync::Arc::new(tokio::sync::Mutex::new(ToolExecutor::new(
            PermissionPolicy::workspace_write(workspace)
                .with_shell_mode(crate::permissions::ShellPermissionMode::Allow),
        )));
        let (outbound_tx, mut outbound_rx) = mpsc::unbounded_channel();
        let command = if cfg!(windows) {
            "Start-Sleep -Milliseconds 250; Write-Output done"
        } else {
            "sleep 0.25; echo done"
        };
        let started = std::time::Instant::now();

        handle_ws_event(
            &mut app,
            tool_executor,
            &outbound_tx,
            WsEvent::Frame(ServerFrame::ToolExecute {
                tool_call_id: "call-1".to_string(),
                tool_name: "bash".to_string(),
                args: serde_json::json!({"command": command}),
            }),
        )
        .await;

        assert!(started.elapsed() < std::time::Duration::from_millis(150));
        assert!(outbound_rx.try_recv().is_err());

        let outbound = tokio::time::timeout(std::time::Duration::from_secs(2), outbound_rx.recv())
            .await
            .unwrap()
            .unwrap();
        assert!(matches!(
            outbound,
            OutboundMessage::Frame(ClientFrame::ToolResult {
                status: crate::protocol::ToolStatus::Success,
                result,
                ..
            }) if result.contains("done")
        ));
    }

    #[test]
    fn input_key_handling_uses_cursor_and_history_buffer() {
        let mut app = AppState::for_test();
        let mut input = crate::input::InputBuffer::default();
        input.push_history("previous".to_string());
        input.insert_str("ab");

        handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Left, KeyModifiers::NONE),
        );
        handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Char('X'), KeyModifiers::NONE),
        );

        assert_eq!(app.input, "aXb");

        handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Up, KeyModifiers::NONE),
        );
        assert_eq!(app.input, "previous");
    }

    #[test]
    fn approval_key_sends_approval_response_to_anima() {
        let mut app = AppState::for_test();
        let mut input = crate::input::InputBuffer::default();
        app.apply(AppEvent::ServerFrame(ServerFrame::ApprovalRequired {
            run_id: 42,
            tool_call_id: "call-1".to_string(),
            tool_name: "bash".to_string(),
            args: serde_json::json!({"command":"git status"}),
        }));

        let outgoing = handle_key(
            &mut app,
            &mut input,
            KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE),
        );

        assert_eq!(
            outgoing,
            vec![OutboundMessage::Frame(
                crate::protocol::ClientFrame::ApprovalResponse {
                    run_id: 42,
                    tool_call_id: "call-1".to_string(),
                    approved: true,
                    reason: None,
                }
            )]
        );
    }

    #[test]
    fn reconnect_wait_queues_outbound_frames_for_next_connection() {
        let frame = ClientFrame::UserMessage {
            message: "queued while reconnecting".to_string(),
        };
        let mut pending_frames = VecDeque::new();

        let action = handle_reconnect_outbound(
            Some(OutboundMessage::Frame(frame.clone())),
            &mut pending_frames,
        );

        assert_eq!(action, ReconnectOutboundAction::RetryNow);
        assert_eq!(pending_frames.pop_front(), Some(frame));
    }
}
