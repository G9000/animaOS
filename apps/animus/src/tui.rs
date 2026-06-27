#![allow(dead_code)]

use std::io::{self, Stdout};
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

use tokio::sync::mpsc;

use crate::app::{AppEvent, AppState, ConnectionState};
use crate::approvals::ApprovalDecision;
use crate::client::{reconnect_delay, AnimaWsClient};
use crate::commands::{parse_command, CommandEffect};
use crate::permissions::{PermissionPolicy, ShellPermissionMode};
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
}

#[derive(Debug)]
enum WsEvent {
    ConnectionChanged(ConnectionState),
    Authenticated(AuthUser),
    Frame(ServerFrame),
    Disconnected(String),
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
    let mut tool_executor = ToolExecutor::new(
        PermissionPolicy::workspace_write(app.config.workspace.clone())
            .with_shell_mode(ShellPermissionMode::Ask),
    );

    loop {
        while let Ok(event) = ws_rx.try_recv() {
            handle_ws_event(&mut app, &mut tool_executor, &outbound_tx, event).await;
        }

        session.terminal.draw(|frame| draw_app(frame, &app))?;

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                for outgoing in handle_key(&mut app, key) {
                    let _ = outbound_tx.send(outgoing);
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

async fn websocket_driver(
    config: crate::config::AnimusConfig,
    ui_tx: mpsc::UnboundedSender<WsEvent>,
    mut outbound_rx: mpsc::UnboundedReceiver<OutboundMessage>,
) {
    let mut attempt = 0u32;

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

                loop {
                    tokio::select! {
                        outgoing = outbound_rx.recv() => {
                            match outgoing {
                                Some(OutboundMessage::Frame(frame)) => {
                                    if let Err(err) = client.send_frame(frame).await {
                                        let _ = ui_tx.send(WsEvent::Disconnected(format!("websocket send failed: {err}")));
                                        break;
                                    }
                                }
                                Some(OutboundMessage::Reconnect) => {
                                    let _ = ui_tx.send(WsEvent::Disconnected("reconnecting to ANIMA".to_string()));
                                    break;
                                }
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
            Err(err) => {
                let _ = ui_tx.send(WsEvent::Disconnected(format!("connection failed: {err}")));
            }
        }

        attempt = attempt.saturating_add(1);
        let delay = reconnect_delay(attempt);
        tokio::select! {
            _ = tokio::time::sleep(delay) => {}
            outgoing = outbound_rx.recv() => {
                match outgoing {
                    Some(OutboundMessage::Reconnect) => {}
                    Some(OutboundMessage::Frame(_)) => {
                        let _ = ui_tx.send(WsEvent::Disconnected("not connected; outbound frame dropped".to_string()));
                    }
                    None => return,
                }
            }
        }
    }
}

async fn handle_ws_event(
    app: &mut AppState,
    tool_executor: &mut ToolExecutor,
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
            let tool_result = tool_executor.execute_frame(&frame).await;
            app.apply(AppEvent::ServerFrame(frame));
            if let Some(frame) = tool_result {
                let _ = outbound_tx.send(OutboundMessage::Frame(frame));
            }
        }
        WsEvent::Disconnected(message) => {
            app.apply(AppEvent::ConnectionChanged(ConnectionState::Disconnected));
            app.apply(AppEvent::Notice(message));
        }
    }
}

fn draw_app(frame: &mut ratatui::Frame<'_>, app: &AppState) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(1), Constraint::Length(3)])
        .split(frame.area());

    let items: Vec<ListItem> = app
        .transcript
        .iter()
        .map(|item| ListItem::new(item.render_plain()))
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

pub fn render_to_text(app: &AppState, width: u16, height: u16) -> Vec<String> {
    let max_width = usize::from(width.max(1));
    let max_rows = usize::from(height.max(1));
    let mut rows = Vec::new();
    rows.push(truncate_line(status_line(app), max_width));
    rows.extend(
        app.transcript
            .iter()
            .map(|item| truncate_line(item.render_plain(), max_width)),
    );
    rows.extend(
        approval_prompt_rows(app)
            .into_iter()
            .map(|row| truncate_line(row, max_width)),
    );
    rows.push(truncate_line(format!("input: {}", app.input), max_width));
    rows.truncate(max_rows);
    rows
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

fn handle_key(app: &mut AppState, key: KeyEvent) -> Vec<OutboundMessage> {
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
                return approval_outbound(app, ApprovalDecision::Deny {
                    reason: "denied by user".to_string(),
                });
            }
            KeyCode::Esc => {
                return approval_outbound(app, ApprovalDecision::Cancel);
            }
            _ => {}
        }
    }

    match key.code {
        KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.input.push(ch);
        }
        KeyCode::Backspace => {
            app.input.pop();
        }
        KeyCode::Enter if !app.input.trim().is_empty() => {
            let message = app.input.clone();
            if message.trim_start().starts_with('/') {
                match parse_command(&message) {
                    Ok(command) => {
                        let effect = app.handle_command(command);
                        app.input.clear();
                        return command_outbound(effect);
                    }
                    Err(err) => {
                        app.apply(AppEvent::Notice(format!("command error: {err:?}")));
                        app.input.clear();
                    }
                }
            } else {
                app.apply(AppEvent::UserSubmitted(message.clone()));
                return vec![OutboundMessage::Frame(ClientFrame::UserMessage { message })];
            }
        }
        _ => {}
    }

    Vec::new()
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
    fn enter_on_prompt_sends_user_message_to_anima() {
        let mut app = AppState::for_test();
        app.input = "hello anima".to_string();

        let outgoing = handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        );

        assert_eq!(
            outgoing,
            vec![OutboundMessage::Frame(crate::protocol::ClientFrame::UserMessage {
                message: "hello anima".to_string(),
            })]
        );
    }

    #[test]
    fn command_input_sends_cancel_and_reconnect_actions() {
        let mut app = AppState::for_test();
        app.run.current_run_id = Some(42);
        app.input = "/cancel".to_string();

        let cancel = handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        );

        assert_eq!(
            cancel,
            vec![OutboundMessage::Frame(crate::protocol::ClientFrame::Cancel {
                run_id: 42,
            })]
        );

        app.input = "/reconnect".to_string();
        let reconnect = handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
        );

        assert_eq!(reconnect, vec![OutboundMessage::Reconnect]);
    }

    #[test]
    fn approval_key_sends_approval_response_to_anima() {
        let mut app = AppState::for_test();
        app.apply(AppEvent::ServerFrame(ServerFrame::ApprovalRequired {
            run_id: 42,
            tool_call_id: "call-1".to_string(),
            tool_name: "bash".to_string(),
            args: serde_json::json!({"command":"git status"}),
        }));

        let outgoing = handle_key(
            &mut app,
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
}
