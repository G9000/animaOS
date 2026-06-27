#![allow(dead_code)]

#[cfg(windows)]
use std::ffi::OsString;
use std::fs;
#[cfg(windows)]
use std::path::Prefix;
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
    Deny,
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
    pub fn from_mode(workspace: PathBuf, mode: &str) -> Result<Self, String> {
        match mode {
            "read-only" => Ok(Self::read_only(workspace)),
            "workspace-write" => Ok(Self::workspace_write(workspace)),
            "workspace-write-shell" => Ok(Self::workspace_write_shell(workspace)),
            _ => Err(format!("unsupported permission mode: {mode}")),
        }
    }

    pub fn is_supported_mode(mode: &str) -> bool {
        matches!(
            mode,
            "read-only" | "workspace-write" | "workspace-write-shell"
        )
    }

    pub fn read_only(workspace: PathBuf) -> Self {
        Self {
            workspace: workspace_root(workspace),
            file_mode: FilePermissionMode::ReadOnly,
            shell_mode: ShellPermissionMode::Deny,
        }
    }

    pub fn workspace_write(workspace: PathBuf) -> Self {
        Self {
            workspace: workspace_root(workspace),
            file_mode: FilePermissionMode::WorkspaceWrite,
            shell_mode: ShellPermissionMode::Ask,
        }
    }

    pub fn workspace_write_shell(workspace: PathBuf) -> Self {
        Self {
            workspace: workspace_root(workspace),
            file_mode: FilePermissionMode::WorkspaceWrite,
            shell_mode: ShellPermissionMode::Allow,
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
        let resolved = resolve_for_containment(joined)?;
        if resolved.starts_with(&self.workspace) {
            Ok(resolved)
        } else {
            Err(format!(
                "path {} escapes workspace {}",
                resolved.display(),
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
            ShellPermissionMode::Deny => PermissionDecision::Deny {
                reason: "shell commands are disabled in read-only mode".to_string(),
            },
            ShellPermissionMode::Allow => PermissionDecision::Allow,
            ShellPermissionMode::Ask => PermissionDecision::Ask {
                reason: format!(
                    "shell command requires explicit /permissions workspace-write-shell: {command}"
                ),
            },
        }
    }
}

fn workspace_root(workspace: PathBuf) -> PathBuf {
    let absolute = if workspace.is_absolute() {
        workspace
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(workspace)
    };
    fs::canonicalize(&absolute)
        .map(normalize_path)
        .unwrap_or_else(|_| normalize_path(absolute))
}

fn resolve_for_containment(path: PathBuf) -> Result<PathBuf, String> {
    let normalized = normalize_path(path);
    if normalized.exists() {
        return Ok(fs::canonicalize(&normalized)
            .map(normalize_path)
            .unwrap_or(normalized));
    }

    let mut existing = normalized.as_path();
    let mut missing = Vec::new();
    while !existing.exists() {
        if fs::symlink_metadata(existing)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            return Err(format!("path {} is a dangling symlink", existing.display()));
        }
        let Some(parent) = existing.parent() else {
            return Ok(normalized);
        };
        if let Some(name) = existing.file_name() {
            missing.push(name.to_owned());
        }
        existing = parent;
    }

    let mut resolved = fs::canonicalize(existing)
        .map(normalize_path)
        .unwrap_or_else(|_| existing.to_path_buf());
    for part in missing.iter().rev() {
        resolved.push(part);
    }
    Ok(normalize_path(resolved))
}

fn normalize_path(path: PathBuf) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::Prefix(prefix) => push_normalized_prefix(&mut normalized, prefix),
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

#[cfg(windows)]
fn push_normalized_prefix(normalized: &mut PathBuf, prefix: std::path::PrefixComponent<'_>) {
    match prefix.kind() {
        Prefix::Disk(drive) | Prefix::VerbatimDisk(drive) => {
            normalized.push(format!("{}:", char::from(drive)));
        }
        Prefix::UNC(server, share) | Prefix::VerbatimUNC(server, share) => {
            let mut unc = OsString::from(r"\\");
            unc.push(server);
            unc.push(r"\");
            unc.push(share);
            normalized.push(unc);
        }
        _ => normalized.push(prefix.as_os_str()),
    }
}

#[cfg(not(windows))]
fn push_normalized_prefix(normalized: &mut PathBuf, prefix: std::path::PrefixComponent<'_>) {
    normalized.push(prefix.as_os_str());
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
    use std::fs;
    use std::path::PathBuf;

    use super::*;

    fn test_workspace() -> PathBuf {
        let workspace =
            std::env::temp_dir().join(format!("animus-permissions-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&workspace).unwrap();
        workspace
    }

    #[test]
    fn read_only_allows_reads_but_denies_writes() {
        let workspace = test_workspace();
        let policy = PermissionPolicy::read_only(workspace.clone());

        assert_eq!(
            policy.check_file_read(workspace.join("src/main.rs")),
            PermissionDecision::Allow
        );
        assert!(matches!(
            policy.check_file_write(workspace.join("src/main.rs")),
            PermissionDecision::Deny { .. }
        ));
    }

    #[test]
    fn workspace_write_allows_inside_and_denies_escape_paths() {
        let workspace = test_workspace();
        let outside = workspace
            .join("..")
            .join(format!("outside-{}.txt", uuid::Uuid::new_v4()));
        let policy = PermissionPolicy::workspace_write(workspace.clone());

        assert_eq!(
            policy.check_file_write(workspace.join("src/main.rs")),
            PermissionDecision::Allow
        );
        assert!(matches!(
            policy.check_file_write(outside),
            PermissionDecision::Deny { .. }
        ));
    }

    #[test]
    fn shell_policy_distinguishes_ask_allow_and_dangerous_commands() {
        let workspace = test_workspace();
        let ask = PermissionPolicy::workspace_write(workspace.clone())
            .with_shell_mode(ShellPermissionMode::Ask);
        let allow = PermissionPolicy::workspace_write(workspace.clone())
            .with_shell_mode(ShellPermissionMode::Allow);
        let deny =
            PermissionPolicy::workspace_write(workspace).with_shell_mode(ShellPermissionMode::Deny);

        assert!(matches!(
            ask.check_shell("npm test"),
            PermissionDecision::Ask { .. }
        ));
        assert_eq!(allow.check_shell("git status"), PermissionDecision::Allow);
        assert!(matches!(
            deny.check_shell("git status"),
            PermissionDecision::Deny { .. }
        ));
        assert!(matches!(
            allow.check_shell("rm -rf /"),
            PermissionDecision::Deny { .. }
        ));
    }

    #[test]
    fn workspace_write_requires_shell_approval_by_default() {
        let workspace = test_workspace();
        let policy = PermissionPolicy::workspace_write(workspace.clone());

        assert!(matches!(
            policy.check_shell("git status"),
            PermissionDecision::Ask { .. }
        ));
        assert_eq!(
            PermissionPolicy::from_mode(workspace.clone(), "workspace-write-shell")
                .unwrap()
                .check_shell("git status"),
            PermissionDecision::Allow
        );
        assert!(PermissionPolicy::is_supported_mode("workspace-write-shell"));
    }

    #[test]
    fn normalizes_relative_paths_within_workspace() {
        let workspace = test_workspace();
        let policy = PermissionPolicy::workspace_write(workspace.clone());

        assert_eq!(
            policy
                .normalize_workspace_path("src/../Cargo.toml")
                .unwrap(),
            policy.workspace().join("Cargo.toml")
        );
    }

    #[test]
    fn relative_workspace_roots_do_not_allow_absolute_path_escape() {
        let policy = PermissionPolicy::workspace_write(PathBuf::from("."));
        let outside = std::env::current_dir()
            .unwrap()
            .parent()
            .unwrap()
            .join("outside.txt");

        assert!(policy.workspace().is_absolute());
        assert!(matches!(
            policy.check_file_write(outside),
            PermissionDecision::Deny { .. }
        ));
    }

    #[test]
    fn workspace_write_denies_dangling_symlink_to_outside_target() {
        let workspace = test_workspace();
        let outside = workspace
            .parent()
            .unwrap()
            .join(format!("outside-{}.txt", uuid::Uuid::new_v4()));
        let link = workspace.join("link.txt");
        if let Err(err) = create_file_symlink(&outside, &link) {
            eprintln!("skipping symlink test because symlink creation failed: {err}");
            return;
        }
        let policy = PermissionPolicy::workspace_write(workspace.clone());

        assert!(!outside.exists());
        assert!(matches!(
            policy.check_file_write(link),
            PermissionDecision::Deny { .. }
        ));
    }

    #[cfg(unix)]
    fn create_file_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
        std::os::unix::fs::symlink(target, link)
    }

    #[cfg(windows)]
    fn create_file_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
        if std::os::windows::fs::symlink_file(target, link).is_ok() {
            return Ok(());
        }
        create_wsl_file_symlink(target, link)
    }

    #[cfg(windows)]
    fn create_wsl_file_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
        use std::process::Command;

        fn wsl_path(path: &Path) -> std::io::Result<String> {
            let output = Command::new("wsl.exe")
                .arg("wslpath")
                .arg("-a")
                .arg(path.to_string_lossy().replace('\\', "/"))
                .output()?;
            if !output.status.success() {
                return Err(std::io::Error::other("wslpath failed"));
            }
            Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
        }

        let target = wsl_path(target)?;
        let link = wsl_path(link)?;
        let output = Command::new("wsl.exe")
            .arg("-e")
            .arg("ln")
            .arg("-s")
            .arg(target)
            .arg(link)
            .output()?;
        if output.status.success() {
            Ok(())
        } else {
            Err(std::io::Error::other("wsl ln failed"))
        }
    }
}
