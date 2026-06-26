use std::path::PathBuf;

use anyhow::Result;
use clap::Parser;

#[derive(Debug, Parser)]
#[command(name = "animus", about = "ANIMA Rust coding terminal")]
struct Cli {
    #[arg(long, default_value = "http://127.0.0.1:3031")]
    server_url: String,
    #[arg(long, default_value = ".")]
    workspace: PathBuf,
    #[arg(long)]
    token: Option<String>,
    #[arg(long)]
    username: Option<String>,
    #[arg(long)]
    password: Option<String>,
    #[arg(long)]
    headless: bool,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    if cli.headless {
        println!("{}", startup_summary(&cli));
        return Ok(());
    }

    println!(
        "Animus Rust TUI scaffold is ready. Use --headless for a non-interactive startup check."
    );
    Ok(())
}

fn startup_summary(cli: &Cli) -> String {
    let auth_mode = if cli.token.is_some() {
        "token"
    } else if cli.username.is_some() && cli.password.is_some() {
        "username/password"
    } else if cli.username.is_some() {
        "username"
    } else {
        "none"
    };

    format!(
        "Animus Rust TUI\nserver_url: {}\nworkspace: {}\nauth: {}",
        cli.server_url,
        cli.workspace.display(),
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

        assert_eq!(cli.server_url, "http://localhost:3031");
        assert_eq!(cli.workspace.display().to_string(), "C:/work/anima");
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

        let summary = startup_summary(&cli);

        assert!(summary.contains("Animus Rust TUI"));
        assert!(summary.contains("auth: username/password"));
        assert!(summary.contains("workspace: ."));
        assert!(!summary.contains("secret-password"));
    }
}
