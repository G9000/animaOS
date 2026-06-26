use std::path::PathBuf;

use anyhow::Result;
use clap::Parser;

use config::{load_config, AnimusConfig, ConfigOverrides};

mod app;
mod approvals;
mod client;
mod commands;
mod config;
mod input;
mod permissions;
mod protocol;
mod tools;
mod transcript;
mod tui;

#[derive(Debug, Parser)]
#[command(name = "animus", about = "ANIMA Rust coding terminal")]
struct Cli {
    #[arg(long)]
    server_url: Option<String>,
    #[arg(long)]
    workspace: Option<PathBuf>,
    #[arg(long)]
    token: Option<String>,
    #[arg(long)]
    username: Option<String>,
    #[arg(long)]
    password: Option<String>,
    #[arg(long)]
    headless: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let config = load_config(ConfigOverrides::from(&cli))?;
    if cli.headless {
        println!("{}", startup_summary(&config));
        return Ok(());
    }

    tui::run_tui(app::AppState::new(config)).await
}

impl From<&Cli> for ConfigOverrides {
    fn from(cli: &Cli) -> Self {
        Self {
            server_url: cli.server_url.clone(),
            workspace: cli.workspace.clone(),
            unlock_token: cli.token.clone(),
            username: cli.username.clone(),
            password: cli.password.clone(),
        }
    }
}

fn startup_summary(config: &AnimusConfig) -> String {
    let auth_mode = if config.unlock_token.is_some() {
        "token"
    } else if config.username.is_some() && config.password.is_some() {
        "username/password"
    } else if config.username.is_some() {
        "username"
    } else {
        "none"
    };

    format!(
        "Animus Rust TUI\nserver_url: {}\nworkspace: {}\nauth: {}",
        config.server_url,
        config.workspace.display(),
        auth_mode
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_headless_startup_args() {
        let cli = Cli::parse_from([
            "animus",
            "--server-url",
            "http://localhost:3031",
            "--workspace",
            "C:/work/anima",
            "--token",
            "secret-token",
            "--headless",
        ]);

        assert_eq!(cli.server_url.as_deref(), Some("http://localhost:3031"));
        assert_eq!(
            cli.workspace.as_ref().unwrap().display().to_string(),
            "C:/work/anima"
        );
        assert_eq!(cli.token.as_deref(), Some("secret-token"));
        assert!(cli.headless);
    }

    #[test]
    fn headless_summary_redacts_secrets_and_reports_auth_mode() {
        let cli = Cli::parse_from([
            "animus",
            "--workspace",
            ".",
            "--username",
            "alice",
            "--password",
            "secret-password",
            "--headless",
        ]);

        let summary = startup_summary(&AnimusConfig {
            server_url: "http://127.0.0.1:3031".to_string(),
            workspace: cli.workspace.clone().unwrap(),
            unlock_token: cli.token.clone(),
            username: cli.username.clone(),
            password: cli.password.clone(),
        });

        assert!(summary.contains("Animus Rust TUI"));
        assert!(summary.contains("auth: username/password"));
        assert!(summary.contains("workspace: ."));
        assert!(!summary.contains("secret-password"));
    }
}
