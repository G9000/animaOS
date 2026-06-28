use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::Result;
use serde::Deserialize;

pub const DEFAULT_SERVER_URL: &str = "http://127.0.0.1:3031";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AnimusConfig {
    pub server_url: String,
    pub workspace: PathBuf,
    pub unlock_token: Option<String>,
    pub username: Option<String>,
    pub password: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ConfigOverrides {
    pub server_url: Option<String>,
    pub workspace: Option<PathBuf>,
    pub unlock_token: Option<String>,
    pub username: Option<String>,
    pub password: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EnvConfig {
    pub server_url: Option<String>,
    pub workspace: Option<PathBuf>,
    pub unlock_token: Option<String>,
    pub username: Option<String>,
    pub password: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct LegacyConfigFile {
    #[serde(default, rename = "serverUrl")]
    pub server_url: Option<String>,
    #[serde(default, rename = "unlockToken")]
    pub unlock_token: Option<String>,
    #[serde(default)]
    pub username: Option<String>,
    #[serde(default, rename = "workspaceDir")]
    pub workspace_dir: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigSources {
    pub cli: ConfigOverrides,
    pub env: EnvConfig,
    pub legacy: Option<LegacyConfigFile>,
    pub default_workspace: PathBuf,
}

impl Default for ConfigSources {
    fn default() -> Self {
        Self {
            cli: ConfigOverrides::default(),
            env: EnvConfig::default(),
            legacy: None,
            default_workspace: PathBuf::from("."),
        }
    }
}

pub fn resolve_config(sources: ConfigSources) -> AnimusConfig {
    let legacy = sources.legacy.unwrap_or_default();
    let server_url = sources
        .cli
        .server_url
        .or(sources.env.server_url)
        .or(legacy.server_url)
        .unwrap_or_else(|| DEFAULT_SERVER_URL.to_string());
    let workspace = sources
        .cli
        .workspace
        .or(sources.env.workspace)
        .or(legacy.workspace_dir)
        .unwrap_or(sources.default_workspace);

    AnimusConfig {
        server_url,
        workspace,
        unlock_token: sources
            .cli
            .unlock_token
            .or(sources.env.unlock_token)
            .or(legacy.unlock_token),
        username: sources
            .cli
            .username
            .or(sources.env.username)
            .or(legacy.username),
        password: sources.cli.password.or(sources.env.password),
    }
}

pub fn load_config(cli: ConfigOverrides) -> Result<AnimusConfig> {
    let default_workspace = env::current_dir()?;
    let legacy = default_config_path().and_then(|path| read_legacy_config(&path).ok().flatten());
    Ok(resolve_config(ConfigSources {
        cli,
        env: read_env_config(),
        legacy,
        default_workspace,
    }))
}

pub fn parse_legacy_config(raw: &str) -> Result<LegacyConfigFile> {
    Ok(serde_json::from_str(raw)?)
}

pub fn read_legacy_config(path: &Path) -> Result<Option<LegacyConfigFile>> {
    if !path.exists() {
        return Ok(None);
    }
    let raw = fs::read_to_string(path)?;
    Ok(parse_legacy_config(&raw).ok())
}

pub fn default_config_path() -> Option<PathBuf> {
    env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(|home| PathBuf::from(home).join(".animus").join("config.json"))
}

fn read_env_config() -> EnvConfig {
    EnvConfig {
        server_url: env::var("ANIMUS_SERVER_URL").ok(),
        workspace: env::var_os("ANIMUS_WORKSPACE").map(PathBuf::from),
        unlock_token: env::var("ANIMUS_UNLOCK_TOKEN").ok(),
        username: env::var("ANIMUS_USERNAME").ok(),
        password: env::var("ANIMUS_PASSWORD").ok(),
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;

    #[test]
    fn config_precedence_is_cli_then_env_then_legacy_config_then_defaults() {
        let resolved = resolve_config(ConfigSources {
            cli: ConfigOverrides {
                server_url: Some("http://cli:3031".to_string()),
                workspace: None,
                unlock_token: None,
                username: None,
                password: Some("cli-password".to_string()),
            },
            env: EnvConfig {
                server_url: Some("http://env:3031".to_string()),
                workspace: Some(PathBuf::from("env-workspace")),
                unlock_token: Some("env-token".to_string()),
                username: None,
                password: None,
            },
            legacy: Some(LegacyConfigFile {
                server_url: Some("http://file:3031".to_string()),
                unlock_token: Some("file-token".to_string()),
                username: Some("file-user".to_string()),
                workspace_dir: Some(PathBuf::from("file-workspace")),
            }),
            default_workspace: PathBuf::from("default-workspace"),
        });

        assert_eq!(resolved.server_url, "http://cli:3031");
        assert_eq!(resolved.workspace, PathBuf::from("env-workspace"));
        assert_eq!(resolved.unlock_token.as_deref(), Some("env-token"));
        assert_eq!(resolved.username.as_deref(), Some("file-user"));
        assert_eq!(resolved.password.as_deref(), Some("cli-password"));
    }

    #[test]
    fn default_config_uses_local_server_and_current_workspace() {
        let resolved = resolve_config(ConfigSources {
            default_workspace: PathBuf::from("cwd"),
            ..ConfigSources::default()
        });

        assert_eq!(resolved.server_url, DEFAULT_SERVER_URL);
        assert_eq!(resolved.workspace, PathBuf::from("cwd"));
    }

    #[test]
    fn reads_legacy_animus_config_json() {
        let raw = r#"{
            "serverUrl": "http://localhost:3031",
            "unlockToken": "token",
            "username": "alice",
            "workspaceDir": "C:/work/anima"
        }"#;

        let config = parse_legacy_config(raw).unwrap();

        assert_eq!(config.server_url.as_deref(), Some("http://localhost:3031"));
        assert_eq!(config.unlock_token.as_deref(), Some("token"));
        assert_eq!(config.username.as_deref(), Some("alice"));
        assert_eq!(config.workspace_dir, Some(PathBuf::from("C:/work/anima")));
    }
}
