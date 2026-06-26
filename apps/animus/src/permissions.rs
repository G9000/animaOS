#![allow(dead_code)]

use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PermissionDecision {
    Allow,
    Ask { reason: String },
    Deny { reason: String },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilePermissionMode {
    ReadOnly,
    WorkspaceWrite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShellPermissionMode {
    Ask,
    Allow,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PermissionPolicy {
    workspace: PathBuf,
    file_mode: FilePermissionMode,
    shell_mode: ShellPermissionMode,
}

impl PermissionPolicy {
    pub fn read_only(workspace: PathBuf) -> Self {
        Self {
            workspace: normalize_path(workspace),
            file_mode: FilePermissionMode::ReadOnly,
            shell_mode: ShellPermissionMode::Ask,
        }
    }

    pub fn workspace_write(workspace: PathBuf) -> Self {
        Self {
            workspace: normalize_path(workspace),
            file_mode: FilePermissionMode::WorkspaceWrite,
            shell_mode: ShellPermissionMode::Ask,
        }
    }

    pub fn with_shell_mode(mut self, shell_mode: ShellPermissionMode) -> Self {
        self.shell_mode = shell_mode;
        self
    }

    pub fn workspace(&self) -> &Path {
        &self.workspace
    }

    pub fn normalize_workspace_path(&self, path: impl AsRef<Path>) -> Result<PathBuf, String> {
        let path = path.as_ref();
        let joined = if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.workspace.join(path)
        };
        let normalized = normalize_path(joined);
        if normalized.starts_with(&self.workspace) {
            Ok(normalized)
        } else {
            Err(format!(
                "path {} escapes workspace {}",
                normalized.display(),
                self.workspace.display()
            ))
        }
    }

    pub fn check_file_read(&self, path: impl AsRef<Path>) -> PermissionDecision {
        match self.normalize_workspace_path(path) {
            Ok(_) => PermissionDecision::Allow,
            Err(reason) => PermissionDecision::Deny { reason },
        }
    }

    pub fn check_file_write(&self, path: impl AsRef<Path>) -> PermissionDecision {
        if self.file_mode == FilePermissionMode::ReadOnly {
            return PermissionDecision::Deny {
                reason: "permission mode is read-only".to_string(),
            };
        }
        match self.normalize_workspace_path(path) {
            Ok(_) => PermissionDecision::Allow,
            Err(reason) => PermissionDecision::Deny { reason },
        }
    }

    pub fn check_shell(&self, command: &str) -> PermissionDecision {
        if is_dangerous_shell(command) {
            return PermissionDecision::Deny {
                reason: format!("dangerous shell command requires explicit review: {command}"),
            };
        }
        match self.shell_mode {
            ShellPermissionMode::Allow => PermissionDecision::Allow,
            ShellPermissionMode::Ask => PermissionDecision::Ask {
                reason: format!("shell command requires approval: {command}"),
            },
        }
    }
}

fn normalize_path(path: PathBuf) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Prefix(prefix) => normalized.push(prefix.as_os_str()),
            Component::RootDir => normalized.push(component.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            Component::Normal(part) => normalized.push(part),
        }
    }
    normalized
}

fn is_dangerous_shell(command: &str) -> bool {
    let command = command.trim().to_ascii_lowercase();
    command.starts_with("rm ")
        || command.starts_with("rmdir ")
        || command.starts_with("sudo ")
        || command.starts_with("git reset")
        || command.starts_with("git push")
        || command.starts_with("git rebase")
        || command.starts_with("chmod ")
        || command.starts_with("chown ")
        || command.contains("| sh")
        || command.contains("| bash")
        || (command.starts_with("remove-item") && command.contains("-recurse"))
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::*;

    #[test]
    fn read_only_allows_reads_but_denies_writes() {
        let policy = PermissionPolicy::read_only(PathBuf::from("C:/repo"));

        assert_eq!(
            policy.check_file_read(PathBuf::from("C:/repo/src/main.rs")),
            PermissionDecision::Allow
        );
        assert!(matches!(
            policy.check_file_write(PathBuf::from("C:/repo/src/main.rs")),
            PermissionDecision::Deny { .. }
        ));
    }

    #[test]
    fn workspace_write_allows_inside_and_denies_escape_paths() {
        let policy = PermissionPolicy::workspace_write(PathBuf::from("C:/repo"));

        assert_eq!(
            policy.check_file_write(PathBuf::from("C:/repo/src/main.rs")),
            PermissionDecision::Allow
        );
        assert!(matches!(
            policy.check_file_write(PathBuf::from("C:/repo/../outside.txt")),
            PermissionDecision::Deny { .. }
        ));
    }

    #[test]
    fn shell_policy_distinguishes_ask_allow_and_dangerous_commands() {
        let ask = PermissionPolicy::workspace_write(PathBuf::from("C:/repo"))
            .with_shell_mode(ShellPermissionMode::Ask);
        let allow = PermissionPolicy::workspace_write(PathBuf::from("C:/repo"))
            .with_shell_mode(ShellPermissionMode::Allow);

        assert!(matches!(
            ask.check_shell("npm test"),
            PermissionDecision::Ask { .. }
        ));
        assert_eq!(allow.check_shell("git status"), PermissionDecision::Allow);
        assert!(matches!(
            allow.check_shell("rm -rf /"),
            PermissionDecision::Deny { .. }
        ));
    }

    #[test]
    fn normalizes_relative_paths_within_workspace() {
        let policy = PermissionPolicy::workspace_write(PathBuf::from("C:/repo"));

        assert_eq!(
            policy
                .normalize_workspace_path("src/../Cargo.toml")
                .unwrap(),
            PathBuf::from("C:/repo/Cargo.toml")
        );
    }
}
