#![allow(dead_code)]

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::permissions::{PermissionDecision, PermissionPolicy};
use crate::tools::ToolOutput;

pub fn read_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let path = match resolve_path(args, policy, &["file_path", "path"], false) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let raw = match fs::read_to_string(&path) {
        Ok(raw) => raw,
        Err(err) => return ToolOutput::error(format!("failed to read {}: {err}", path.display())),
    };
    let offset = number_arg(args, "offset").unwrap_or(0);
    let limit = number_arg(args, "limit").unwrap_or(2_000);
    let lines = raw
        .lines()
        .enumerate()
        .skip(offset)
        .take(limit)
        .map(|(index, line)| format!("{}: {line}", index + 1))
        .collect::<Vec<_>>();
    ToolOutput::success(lines.join("\n"))
}

pub fn write_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let path = match resolve_path(args, policy, &["file_path", "path"], true) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let Some(content) = string_arg(args, "content") else {
        return ToolOutput::error("write_file requires content");
    };
    if let Some(parent) = path.parent() {
        if let Err(err) = fs::create_dir_all(parent) {
            return ToolOutput::error(format!("failed to create {}: {err}", parent.display()));
        }
    }
    match fs::write(&path, content) {
        Ok(()) => ToolOutput::success(format!("wrote {}", display_workspace_path(policy, &path))),
        Err(err) => ToolOutput::error(format!("failed to write {}: {err}", path.display())),
    }
}

pub fn edit_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let path = match resolve_path(args, policy, &["file_path", "path"], true) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let Some(old_string) = string_arg(args, "old_string") else {
        return ToolOutput::error("edit_file requires old_string");
    };
    let Some(new_string) = string_arg(args, "new_string") else {
        return ToolOutput::error("edit_file requires new_string");
    };
    let raw = match fs::read_to_string(&path) {
        Ok(raw) => raw,
        Err(err) => return ToolOutput::error(format!("failed to read {}: {err}", path.display())),
    };
    if let Err(output) = require_unique_match(&raw, old_string) {
        return output;
    }
    let edited = raw.replacen(old_string, new_string, 1);
    match fs::write(&path, edited) {
        Ok(()) => ToolOutput::success(format!("edited {}", display_workspace_path(policy, &path))),
        Err(err) => ToolOutput::error(format!("failed to write {}: {err}", path.display())),
    }
}

pub fn multi_edit(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let path = match resolve_path(args, policy, &["file_path", "path"], true) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let Some(edits) = args.get("edits").and_then(Value::as_array) else {
        return ToolOutput::error("multi_edit requires edits");
    };
    let raw = match fs::read_to_string(&path) {
        Ok(raw) => raw,
        Err(err) => return ToolOutput::error(format!("failed to read {}: {err}", path.display())),
    };
    for edit in edits {
        let Some(old_string) = string_arg(edit, "old_string") else {
            return ToolOutput::error("each edit requires old_string");
        };
        if string_arg(edit, "new_string").is_none() {
            return ToolOutput::error("each edit requires new_string");
        }
        if let Err(output) = require_unique_match(&raw, old_string) {
            return output;
        }
    }
    let mut edited = raw;
    for edit in edits {
        let old_string = string_arg(edit, "old_string").unwrap_or_default();
        let new_string = string_arg(edit, "new_string").unwrap_or_default();
        edited = edited.replacen(old_string, new_string, 1);
    }
    match fs::write(&path, edited) {
        Ok(()) => ToolOutput::success(format!("edited {}", display_workspace_path(policy, &path))),
        Err(err) => ToolOutput::error(format!("failed to write {}: {err}", path.display())),
    }
}

pub fn list_dir(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let path = match resolve_path(args, policy, &["path", "file_path"], false) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let entries = match fs::read_dir(&path) {
        Ok(entries) => entries,
        Err(err) => return ToolOutput::error(format!("failed to list {}: {err}", path.display())),
    };
    let mut lines = Vec::new();
    for entry in entries.flatten() {
        let marker = if entry.path().is_dir() { "/" } else { "" };
        lines.push(format!("{}{}", entry.file_name().to_string_lossy(), marker));
    }
    lines.sort();
    ToolOutput::success(lines.join("\n"))
}

pub fn grep(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(pattern) = string_arg(args, "pattern") else {
        return ToolOutput::error("grep requires pattern");
    };
    let path = match resolve_path(args, policy, &["path"], false) {
        Ok(path) => path,
        Err(output) if string_arg(args, "path").is_some() => return output,
        Err(_) => policy.workspace().to_path_buf(),
    };
    let mut matches = Vec::new();
    for file in walk_files(&path) {
        if !matches!(policy.check_file_read(&file), PermissionDecision::Allow) {
            continue;
        }
        let Ok(raw) = fs::read_to_string(&file) else {
            continue;
        };
        for (index, line) in raw.lines().enumerate() {
            if line.contains(pattern) {
                matches.push(format!(
                    "{}:{}:{}",
                    display_workspace_path(policy, &file),
                    index + 1,
                    line
                ));
            }
        }
    }
    ToolOutput::success(matches.join("\n"))
}

pub fn glob(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(pattern) = string_arg(args, "pattern") else {
        return ToolOutput::error("glob requires pattern");
    };
    let path = match resolve_path(args, policy, &["path"], false) {
        Ok(path) => path,
        Err(output) if string_arg(args, "path").is_some() => return output,
        Err(_) => policy.workspace().to_path_buf(),
    };
    let mut matches = walk_files(&path)
        .into_iter()
        .filter(|file| matches!(policy.check_file_read(file), PermissionDecision::Allow))
        .filter_map(|file| {
            let display_path = display_workspace_path(policy, &file);
            matches_simple_glob(&display_path, pattern).then_some(display_path)
        })
        .collect::<Vec<_>>();
    matches.sort();
    ToolOutput::success(matches.join("\n"))
}

fn resolve_path(
    args: &Value,
    policy: &PermissionPolicy,
    keys: &[&str],
    write: bool,
) -> Result<PathBuf, ToolOutput> {
    let raw = keys
        .iter()
        .find_map(|key| string_arg(args, key))
        .ok_or_else(|| {
            ToolOutput::error(format!("missing path argument: {}", keys.join(" or ")))
        })?;
    let decision = if write {
        policy.check_file_write(PathBuf::from(raw))
    } else {
        policy.check_file_read(PathBuf::from(raw))
    };
    match decision {
        PermissionDecision::Allow => policy
            .normalize_workspace_path(raw)
            .map_err(ToolOutput::error),
        PermissionDecision::Ask { reason } | PermissionDecision::Deny { reason } => {
            Err(ToolOutput::error(reason))
        }
    }
}

fn string_arg<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str)
}

fn number_arg(args: &Value, key: &str) -> Option<usize> {
    args.get(key)
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
}

fn walk_files(path: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    if fs::symlink_metadata(path)
        .map(|metadata| metadata.file_type().is_symlink())
        .unwrap_or(false)
    {
        return files;
    }
    if path.is_file() {
        files.push(path.to_path_buf());
        return files;
    }
    let Ok(entries) = fs::read_dir(path) else {
        return files;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            files.extend(walk_files(&path));
        } else {
            files.push(path);
        }
    }
    files
}

fn require_unique_match(raw: &str, old_string: &str) -> Result<(), ToolOutput> {
    if old_string.is_empty() {
        return Err(ToolOutput::error("old_string must not be empty"));
    }
    let count = raw.matches(old_string).count();
    match count {
        0 => Err(ToolOutput::error(format!(
            "old_string was not found: {old_string}"
        ))),
        1 => Ok(()),
        _ => Err(ToolOutput::error(format!(
            "old_string is not unique: {old_string}"
        ))),
    }
}

fn matches_simple_glob(path: &str, pattern: &str) -> bool {
    let candidate = path.replace('\\', "/");
    let pattern = pattern.replace('\\', "/");
    let file_name = candidate.rsplit('/').next().unwrap_or("");
    if pattern == "*" || pattern == "**/*" {
        return true;
    }
    wildcard_match(&pattern, &candidate) || wildcard_match(&pattern, file_name)
}

fn wildcard_match(pattern: &str, text: &str) -> bool {
    let pattern = pattern.as_bytes();
    let text = text.as_bytes();
    let (mut pattern_index, mut text_index) = (0usize, 0usize);
    let mut star_index = None;
    let mut star_text_index = 0usize;

    while text_index < text.len() {
        if pattern_index < pattern.len()
            && (pattern[pattern_index] == text[text_index] || pattern[pattern_index] == b'?')
        {
            pattern_index += 1;
            text_index += 1;
        } else if pattern_index < pattern.len() && pattern[pattern_index] == b'*' {
            star_index = Some(pattern_index);
            star_text_index = text_index;
            pattern_index += 1;
        } else if let Some(index) = star_index {
            pattern_index = index + 1;
            star_text_index += 1;
            text_index = star_text_index;
        } else {
            return false;
        }
    }

    while pattern_index < pattern.len() && pattern[pattern_index] == b'*' {
        pattern_index += 1;
    }
    pattern_index == pattern.len()
}

fn display_workspace_path(policy: &PermissionPolicy, path: &Path) -> String {
    path.strip_prefix(policy.workspace())
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::permissions::PermissionPolicy;

    #[test]
    fn file_tools_read_write_edit_list_and_search_within_workspace() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());

        assert!(
            !write_file(
                &json!({"file_path": "src/lib.rs", "content": "fn old() {}\n"}),
                &policy,
            )
            .is_error
        );
        assert!(
            !edit_file(
                &json!({"file_path": "src/lib.rs", "old_string": "old", "new_string": "new"}),
                &policy,
            )
            .is_error
        );
        assert_eq!(
            read_file(&json!({"file_path": "src/lib.rs"}), &policy).content,
            "1: fn new() {}"
        );
        assert!(list_dir(&json!({"path": "src"}), &policy)
            .content
            .contains("lib.rs"));
        assert!(grep(&json!({"pattern": "new", "path": "src"}), &policy)
            .content
            .contains("src/lib.rs:1:fn new() {}"));
        assert!(glob(&json!({"pattern": "*.rs", "path": "src"}), &policy)
            .content
            .contains("src/lib.rs"));
    }

    #[test]
    fn multi_edit_rejects_batch_when_any_old_string_is_missing() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "alpha beta").unwrap();

        let result = multi_edit(
            &json!({
                "file_path": "src/lib.rs",
                "edits": [
                    {"old_string": "alpha", "new_string": "one"},
                    {"old_string": "missing", "new_string": "two"}
                ]
            }),
            &policy,
        );

        assert!(result.is_error);
        assert_eq!(
            std::fs::read_to_string(root.join("src/lib.rs")).unwrap(),
            "alpha beta"
        );
    }

    #[test]
    fn edit_file_rejects_ambiguous_old_string_before_writing() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "alpha beta alpha").unwrap();

        let result = edit_file(
            &json!({"file_path": "src/lib.rs", "old_string": "alpha", "new_string": "one"}),
            &policy,
        );

        assert!(result.is_error);
        assert!(result.content.contains("not unique"));
        assert_eq!(
            std::fs::read_to_string(root.join("src/lib.rs")).unwrap(),
            "alpha beta alpha"
        );
    }

    #[test]
    fn multi_edit_rejects_ambiguous_old_string_before_writing() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "alpha beta alpha").unwrap();

        let result = multi_edit(
            &json!({
                "file_path": "src/lib.rs",
                "edits": [
                    {"old_string": "alpha", "new_string": "one"}
                ]
            }),
            &policy,
        );

        assert!(result.is_error);
        assert!(result.content.contains("not unique"));
        assert_eq!(
            std::fs::read_to_string(root.join("src/lib.rs")).unwrap(),
            "alpha beta alpha"
        );
    }

    #[test]
    fn glob_matches_workspace_relative_paths() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "fn main() {}").unwrap();

        let result = glob(&json!({"pattern": "src/*.rs"}), &policy);

        assert!(!result.is_error);
        assert!(result.content.contains("src/lib.rs"));
    }

    fn test_workspace() -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("animus-files-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }
}
