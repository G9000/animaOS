use anima_file_tools::{
    glob as shared_glob, grep as shared_grep, parse_patch, plan_patch, read_text_lines,
    BackendKind, BackendPath, FileBackend, GlobRequest, GrepMode, GrepRequest, MutationAtomicity,
    OperationControl, OperationLimits, PatchSnapshot, TextReadRequest, MAX_PATCH_OPERATIONS,
    MAX_RESPONSE_BYTES,
};
use serde_json::Value;

use super::backend::HostFsBackend;
use super::output;
use crate::permissions::PermissionPolicy;
use crate::tools::ToolOutput;

const DEFAULT_RESULT_LIMIT: usize = 200;
const DEFAULT_READ_LINES: usize = 2_000;
const DEFAULT_MAX_LINE_BYTES: usize = 64 * 1024;

pub(super) fn read_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(raw_path) = path_arg(args, &["file_path", "path"]) else {
        return ToolOutput::error("missing path argument: file_path or path");
    };
    let backend = HostFsBackend::new(policy.clone());
    let path = match backend_path(&backend, raw_path) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let limits = OperationLimits::default()
        .validate()
        .expect("default limits are valid");
    let request = TextReadRequest {
        path,
        offset_lines: number_arg(args, "offset").unwrap_or(0),
        max_lines: number_arg(args, "limit").unwrap_or(DEFAULT_READ_LINES),
        max_line_bytes: DEFAULT_MAX_LINE_BYTES,
    };
    match read_text_lines(&backend, request, limits, OperationControl::default()) {
        Ok(page) => ToolOutput::success(output::text(page)),
        Err(error) => ToolOutput::error(error.to_string()),
    }
}

pub(super) fn write_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(raw_path) = path_arg(args, &["file_path", "path"]) else {
        return ToolOutput::error("missing path argument: file_path or path");
    };
    let Some(content) = string_arg(args, "content") else {
        return ToolOutput::error("write_file requires content");
    };
    let backend = HostFsBackend::new(policy.clone());
    match backend.write_text(raw_path, content) {
        Ok(path) => ToolOutput::success(format!("wrote {}", output::workspace_path(policy, &path))),
        Err(error) => ToolOutput::error(error.to_string()),
    }
}

pub(super) fn edit_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(raw_path) = path_arg(args, &["file_path", "path"]) else {
        return ToolOutput::error("missing path argument: file_path or path");
    };
    let Some(old_string) = string_arg(args, "old_string") else {
        return ToolOutput::error("edit_file requires old_string");
    };
    let Some(new_string) = string_arg(args, "new_string") else {
        return ToolOutput::error("edit_file requires new_string");
    };
    if let Err(output) =
        validate_edit_value(old_string).and_then(|_| validate_edit_value(new_string))
    {
        return output;
    }
    let backend = HostFsBackend::new(policy.clone());
    let write_path = match backend.resolve_write(raw_path) {
        Ok(path) => path,
        Err(error) => return ToolOutput::error(error.to_string()),
    };
    let raw = match read_edit_buffer(&backend, raw_path) {
        Ok(raw) => raw,
        Err(output) => return output,
    };
    let old_string = normalize_edit_string_for_file(old_string, &raw);
    let new_string = normalize_edit_string_for_file(new_string, &raw);
    if let Err(output) = require_unique_match(&raw, &old_string) {
        return output;
    }
    let edited = raw.replacen(&old_string, &new_string, 1);
    match backend.write_text(raw_path, &edited) {
        Ok(_) => ToolOutput::success(format!(
            "edited {}",
            output::workspace_path(policy, &write_path)
        )),
        Err(error) => ToolOutput::error(error.to_string()),
    }
}

pub(super) fn multi_edit(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(raw_path) = path_arg(args, &["file_path", "path"]) else {
        return ToolOutput::error("missing path argument: file_path or path");
    };
    let Some(edits) = args.get("edits").and_then(Value::as_array) else {
        return ToolOutput::error("multi_edit requires edits");
    };
    if edits.is_empty() || edits.len() > MAX_PATCH_OPERATIONS {
        return ToolOutput::error(format!(
            "multi_edit requires between 1 and {MAX_PATCH_OPERATIONS} edits"
        ));
    }
    let backend = HostFsBackend::new(policy.clone());
    let write_path = match backend.resolve_write(raw_path) {
        Ok(path) => path,
        Err(error) => return ToolOutput::error(error.to_string()),
    };
    let mut edited = match read_edit_buffer(&backend, raw_path) {
        Ok(raw) => raw,
        Err(output) => return output,
    };
    for edit in edits {
        let Some(old_string) = string_arg(edit, "old_string") else {
            return ToolOutput::error("each edit requires old_string");
        };
        let Some(new_string) = string_arg(edit, "new_string") else {
            return ToolOutput::error("each edit requires new_string");
        };
        if let Err(output) =
            validate_edit_value(old_string).and_then(|_| validate_edit_value(new_string))
        {
            return output;
        }
        let old_string = normalize_edit_string_for_file(old_string, &edited);
        let new_string = normalize_edit_string_for_file(new_string, &edited);
        if let Err(output) = require_unique_match(&edited, &old_string) {
            return output;
        }
        edited = edited.replacen(&old_string, &new_string, 1);
        if edited.len() > MAX_RESPONSE_BYTES {
            return ToolOutput::error(format!(
                "edited content exceeds the {MAX_RESPONSE_BYTES}-byte limit"
            ));
        }
    }
    match backend.write_text(raw_path, &edited) {
        Ok(_) => ToolOutput::success(format!(
            "edited {}",
            output::workspace_path(policy, &write_path)
        )),
        Err(error) => ToolOutput::error(error.to_string()),
    }
}

pub(super) fn list_dir(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(raw_path) = path_arg(args, &["path", "file_path"]) else {
        return ToolOutput::error("missing path argument: path or file_path");
    };
    let backend = HostFsBackend::new(policy.clone());
    let path = match backend_path(&backend, raw_path) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let limit = number_arg(args, "limit")
        .unwrap_or(DEFAULT_RESULT_LIMIT)
        .clamp(1, OperationLimits::default().walk_entries);
    match backend.read_directory_page(path.as_str(), limit) {
        Ok(listing) => ToolOutput::success(output::directory(listing, limit)),
        Err(error) => ToolOutput::error(error.to_string()),
    }
}

pub(super) fn grep(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(pattern) = string_arg(args, "pattern") else {
        return ToolOutput::error("grep requires pattern");
    };
    let limit = number_arg(args, "limit")
        .unwrap_or(DEFAULT_RESULT_LIMIT)
        .max(1);
    let raw_root =
        string_arg(args, "path").unwrap_or_else(|| policy.workspace().to_str().unwrap_or("."));
    let backend = HostFsBackend::new(policy.clone());
    let root = match backend_path(&backend, raw_root) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let mode = match string_arg(args, "mode") {
        None | Some("literal") => GrepMode::Literal,
        Some("regex") => GrepMode::Regex,
        Some(other) => return ToolOutput::error(format!("unsupported grep mode: {other}")),
    };
    let limits = OperationLimits::default()
        .validate()
        .expect("default limits are valid");
    let request = GrepRequest {
        root,
        query: pattern.to_string(),
        mode,
        cursor: None,
        max_files: limits.walk_entries(),
        max_matches: limit,
        max_line_bytes: DEFAULT_MAX_LINE_BYTES,
    };
    match shared_grep(&backend, request, limits, OperationControl::default()) {
        Ok(page) => ToolOutput::success(output::grep(page, policy, limit)),
        Err(error) => ToolOutput::error(error.to_string()),
    }
}

pub(super) fn glob(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(pattern) = string_arg(args, "pattern") else {
        return ToolOutput::error("glob requires pattern");
    };
    let limit = number_arg(args, "limit")
        .unwrap_or(DEFAULT_RESULT_LIMIT)
        .max(1);
    let raw_root =
        string_arg(args, "path").unwrap_or_else(|| policy.workspace().to_str().unwrap_or("."));
    let backend = HostFsBackend::new(policy.clone());
    let root = match backend_path(&backend, raw_root) {
        Ok(path) => path,
        Err(output) => return output,
    };
    let limits = OperationLimits::default()
        .validate()
        .expect("default limits are valid");
    let request = GlobRequest {
        root,
        pattern: pattern.to_string(),
        cursor: None,
        max_results: limit,
    };
    match shared_glob(&backend, request, limits, OperationControl::default()) {
        Ok(page) => ToolOutput::success(output::glob(page, policy, limit)),
        Err(error) => ToolOutput::error(error.to_string()),
    }
}

pub(super) fn apply_patch(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    let Some(body) = string_arg(args, "patch") else {
        return ToolOutput::error("apply_patch requires patch");
    };
    let patch = match parse_patch(body) {
        Ok(patch) => patch,
        Err(error) => return ToolOutput::error(error.to_string()),
    };
    let backend = HostFsBackend::new(policy.clone());
    let atomicity = backend.capabilities().mutation_atomicity();
    debug_assert_eq!(atomicity, MutationAtomicity::BestEffort);
    let plan = match plan_patch(&backend, &patch, atomicity) {
        Ok(plan) => plan,
        Err(error) => return ToolOutput::error(error.to_string()),
    };
    let mutation_count = plan.mutations.len();
    if let Err(error) = backend.apply_plan(&plan) {
        return ToolOutput::error(error.to_string());
    }
    ToolOutput::success(format!(
        "applied {mutation_count} file operations\nbackend: hostfs\natomic: false"
    ))
}

fn backend_path(backend: &HostFsBackend, raw: &str) -> Result<BackendPath, ToolOutput> {
    let resolved = backend
        .resolve_read(raw)
        .map_err(|error| ToolOutput::error(error.to_string()))?;
    BackendPath::new(BackendKind::HostFs, resolved.to_string_lossy())
        .map_err(|error| ToolOutput::error(error.to_string()))
}

fn path_arg<'a>(args: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter().find_map(|key| string_arg(args, key))
}

fn string_arg<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str)
}

fn number_arg(args: &Value, key: &str) -> Option<usize> {
    args.get(key)
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
}

fn read_edit_buffer(backend: &HostFsBackend, path: &str) -> Result<String, ToolOutput> {
    match backend.read_text(path) {
        Ok(Some(raw)) => Ok(raw),
        Ok(None) => Err(ToolOutput::error(format!("file does not exist: {path}"))),
        Err(error) => Err(ToolOutput::error(error.to_string())),
    }
}

fn validate_edit_value(value: &str) -> Result<(), ToolOutput> {
    if value.len() > MAX_RESPONSE_BYTES {
        return Err(ToolOutput::error(format!(
            "edit value exceeds the {MAX_RESPONSE_BYTES}-byte limit"
        )));
    }
    Ok(())
}

fn require_unique_match(raw: &str, old_string: &str) -> Result<(), ToolOutput> {
    if old_string.is_empty() {
        return Err(ToolOutput::error("old_string must not be empty"));
    }
    match raw.matches(old_string).count() {
        0 => Err(ToolOutput::error("old_string was not found")),
        1 => Ok(()),
        _ => Err(ToolOutput::error("old_string is not unique")),
    }
}

fn normalize_edit_string_for_file(value: &str, file_contents: &str) -> String {
    let normalized = value.replace("\r\n", "\n").replace('\r', "\n");
    if file_contents.contains("\r\n") {
        normalized.replace('\n', "\r\n")
    } else {
        normalized
    }
}
