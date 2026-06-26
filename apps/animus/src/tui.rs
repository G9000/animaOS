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

use crate::app::{AppEvent, AppState};
use crate::commands::parse_command;

type AnimusTerminal = Terminal<CrosstermBackend<Stdout>>;

pub struct TerminalSession {
    terminal: AnimusTerminal,
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

    loop {
        session.terminal.draw(|frame| draw_app(frame, &app))?;

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                handle_key(&mut app, key);
            }
        }

        if app.should_quit {
            break;
        }
    }

    Ok(())
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
    let input = Paragraph::new(format!("{status}\ninput: {}", app.input))
        .block(Block::default().borders(Borders::ALL));
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

fn handle_key(app: &mut AppState, key: KeyEvent) {
    if is_quit_key(key) {
        app.apply(AppEvent::Quit);
        return;
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
                        app.handle_command(command);
                        app.input.clear();
                    }
                    Err(err) => {
                        app.apply(AppEvent::Notice(format!("command error: {err:?}")));
                        app.input.clear();
                    }
                }
            } else {
                app.apply(AppEvent::UserSubmitted(message));
            }
        }
        _ => {}
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
}
