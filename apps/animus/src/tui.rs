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

    let status = format!(
        "conn: {:?} | run: {} | cwd: {}",
        app.connection,
        app.run
            .current_run_id
            .map(|id| id.to_string())
            .unwrap_or_else(|| "-".to_string()),
        app.config.workspace.display()
    );
    let input = Paragraph::new(format!("{status}\ninput: {}", app.input))
        .block(Block::default().borders(Borders::ALL));
    frame.render_widget(input, chunks[1]);
}

pub fn render_to_text(app: &AppState, width: u16, height: u16) -> Vec<String> {
    let max_width = usize::from(width.max(1));
    let max_rows = usize::from(height.max(1));
    let mut rows = Vec::new();
    rows.push(truncate_line(
        format!(
            "conn: {:?} | run: {} | cwd: {}",
            app.connection,
            app.run
                .current_run_id
                .map(|id| id.to_string())
                .unwrap_or_else(|| "-".to_string()),
            app.config.workspace.display()
        ),
        max_width,
    ));
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
            app.apply(AppEvent::UserSubmitted(message));
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
}
