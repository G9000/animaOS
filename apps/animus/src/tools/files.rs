#![allow(dead_code)]

mod backend;
mod handlers;
mod output;

use serde_json::Value;

use crate::permissions::PermissionPolicy;
use crate::tools::ToolOutput;

pub fn read_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::read_file(args, policy)
}

pub fn write_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::write_file(args, policy)
}

pub fn edit_file(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::edit_file(args, policy)
}

pub fn multi_edit(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::multi_edit(args, policy)
}

pub fn list_dir(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::list_dir(args, policy)
}

pub fn grep(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::grep(args, policy)
}

pub fn glob(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::glob(args, policy)
}

pub fn apply_patch(args: &Value, policy: &PermissionPolicy) -> ToolOutput {
    handlers::apply_patch(args, policy)
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
    fn edit_file_matches_lf_old_string_against_crlf_file() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "alpha\r\nbeta\r\ngamma\r\n").unwrap();

        let result = edit_file(
            &json!({
                "file_path": "src/lib.rs",
                "old_string": "alpha\nbeta",
                "new_string": "one\ntwo"
            }),
            &policy,
        );

        assert!(!result.is_error);
        assert_eq!(
            std::fs::read_to_string(root.join("src/lib.rs")).unwrap(),
            "one\r\ntwo\r\ngamma\r\n"
        );
    }

    #[test]
    fn multi_edit_validates_against_progressively_edited_buffer() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "alpha").unwrap();

        let result = multi_edit(
            &json!({
                "file_path": "src/lib.rs",
                "edits": [
                    {"old_string": "alpha", "new_string": "beta"},
                    {"old_string": "beta", "new_string": "gamma"}
                ]
            }),
            &policy,
        );

        assert!(!result.is_error);
        assert_eq!(
            std::fs::read_to_string(root.join("src/lib.rs")).unwrap(),
            "gamma"
        );
    }

    #[test]
    fn multi_edit_rejects_later_edits_that_no_longer_match() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "alpha").unwrap();

        let result = multi_edit(
            &json!({
                "file_path": "src/lib.rs",
                "edits": [
                    {"old_string": "alpha", "new_string": "beta"},
                    {"old_string": "alpha", "new_string": "gamma"}
                ]
            }),
            &policy,
        );

        assert!(result.is_error);
        assert_eq!(
            std::fs::read_to_string(root.join("src/lib.rs")).unwrap(),
            "alpha"
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

    #[test]
    fn glob_honors_limit_and_reports_truncation() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        for index in 0..3 {
            std::fs::write(root.join("src").join(format!("file-{index}.rs")), "").unwrap();
        }

        let result = glob(&json!({"pattern": "src/*.rs", "limit": 2}), &policy);
        let lines = result.content.lines().collect::<Vec<_>>();

        assert!(!result.is_error);
        assert_eq!(lines.len(), 3);
        assert!(lines.contains(&"src/file-0.rs"));
        assert!(lines.contains(&"src/file-1.rs"));
        assert!(!lines.contains(&"src/file-2.rs"));
        assert!(result.content.contains("truncated after 2 matches"));
    }

    #[test]
    fn list_dir_honors_limit_and_reports_truncation() {
        let root = test_workspace();
        let policy = PermissionPolicy::read_only(root.clone());
        for index in (0..5).rev() {
            std::fs::write(root.join(format!("file-{index}.txt")), "").unwrap();
        }

        let result = list_dir(&json!({"path": ".", "limit": 2}), &policy);
        let lines = result.content.lines().collect::<Vec<_>>();

        assert!(!result.is_error);
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0], "file-0.txt");
        assert_eq!(lines[1], "file-1.txt");
        assert_eq!(lines[2], "... truncated after 2 entries");
    }

    #[test]
    fn grep_honors_limit_and_reports_truncation() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(
            root.join("src/lib.rs"),
            "needle one\nneedle two\nneedle three\n",
        )
        .unwrap();

        let result = grep(
            &json!({"pattern": "needle", "path": "src", "limit": 2}),
            &policy,
        );

        assert!(!result.is_error);
        assert!(result.content.contains("src/lib.rs:1:needle one"));
        assert!(result.content.contains("src/lib.rs:2:needle two"));
        assert!(!result.content.contains("src/lib.rs:3:needle three"));
        assert!(result.content.contains("truncated after 2 matches"));
    }

    #[test]
    fn grep_accepts_an_explicit_single_file_path() {
        let root = test_workspace();
        let policy = PermissionPolicy::read_only(root.clone());
        std::fs::write(root.join("notes.txt"), "needle in one file\n").unwrap();

        let result = grep(&json!({"pattern": "needle", "path": "notes.txt"}), &policy);

        assert!(!result.is_error);
        assert!(result.content.contains("notes.txt:1:needle in one file"));
    }

    #[test]
    fn grep_supports_explicit_regex_mode_and_rejects_invalid_regex() {
        let root = test_workspace();
        let policy = PermissionPolicy::read_only(root.clone());
        std::fs::write(root.join("notes.txt"), "alpha 123\nbeta\n").unwrap();

        let result = grep(
            &json!({"pattern": r"alpha\s+\d+", "mode": "regex"}),
            &policy,
        );
        assert!(!result.is_error);
        assert!(result.content.contains("notes.txt:1:alpha 123"));

        let invalid = grep(&json!({"pattern": "[", "mode": "regex"}), &policy);
        assert!(invalid.is_error);
        assert!(invalid.content.contains("invalid regex pattern"));
    }

    #[test]
    fn read_file_rejects_binary_content_without_lossy_conversion() {
        let root = test_workspace();
        let policy = PermissionPolicy::read_only(root.clone());
        std::fs::write(root.join("binary.bin"), b"text\0binary").unwrap();

        let result = read_file(&json!({"file_path": "binary.bin"}), &policy);

        assert!(result.is_error);
        assert!(result.content.contains("binary content"));
    }

    #[test]
    fn grep_reports_non_text_files_as_explicit_skips() {
        let root = test_workspace();
        let policy = PermissionPolicy::read_only(root.clone());
        std::fs::write(root.join("binary.bin"), b"needle\0binary").unwrap();

        let result = grep(&json!({"pattern": "needle"}), &policy);

        assert!(!result.is_error);
        assert!(result
            .content
            .contains("skipped binary.bin: binary content"));
    }

    #[test]
    fn write_file_rejects_model_supplied_content_above_the_shared_response_ceiling() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        let oversized = "x".repeat(anima_file_tools::MAX_RESPONSE_BYTES + 1);

        let result = write_file(
            &json!({"file_path": "oversized.txt", "content": oversized}),
            &policy,
        );

        assert!(result.is_error);
        assert!(!root.join("oversized.txt").exists());
    }

    #[test]
    fn glob_rejects_invalid_patterns() {
        let root = test_workspace();
        let policy = PermissionPolicy::read_only(root);

        let result = glob(&json!({"pattern": "["}), &policy);

        assert!(result.is_error);
        assert!(result.content.contains("invalid glob pattern"));
    }

    #[test]
    fn apply_patch_preflights_then_applies_a_mixed_hostfs_patch() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::write(root.join("update.txt"), "old\n").unwrap();
        std::fs::write(root.join("delete.txt"), "delete\n").unwrap();
        let patch = "*** Begin Patch\n\
                     *** Add File: added.txt\n\
                     +added\n\
                     *** Update File: update.txt\n\
                     @@\n\
                     -old\n\
                     +new\n\
                     *** Delete File: delete.txt\n\
                     *** End Patch";

        let result = apply_patch(&json!({"patch": patch}), &policy);

        assert!(!result.is_error, "{}", result.content);
        assert!(result.content.contains("atomic: false"));
        assert_eq!(
            std::fs::read_to_string(root.join("added.txt")).unwrap(),
            "added\n"
        );
        assert_eq!(
            std::fs::read_to_string(root.join("update.txt")).unwrap(),
            "new\n"
        );
        assert!(!root.join("delete.txt").exists());
    }

    #[test]
    fn apply_patch_does_not_write_when_later_preflight_fails() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        let patch = "*** Begin Patch\n\
                     *** Add File: should-not-exist.txt\n\
                     +created\n\
                     *** Update File: missing.txt\n\
                     @@\n\
                     -old\n\
                     +new\n\
                     *** End Patch";

        let result = apply_patch(&json!({"patch": patch}), &policy);

        assert!(result.is_error);
        assert!(!root.join("should-not-exist.txt").exists());
    }

    #[test]
    fn apply_patch_deletes_binary_files_without_decoding_their_contents() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::write(root.join("binary.bin"), [0, 1, 2, 3]).unwrap();
        let patch = "*** Begin Patch\n*** Delete File: binary.bin\n*** End Patch";

        let result = apply_patch(&json!({"patch": patch}), &policy);

        assert!(!result.is_error, "{}", result.content);
        assert!(!root.join("binary.bin").exists());
    }

    #[test]
    fn apply_patch_rejects_directory_deletes_before_prior_mutations_apply() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        std::fs::create_dir(root.join("directory")).unwrap();
        let patch = "*** Begin Patch\n\
                     *** Add File: should-not-exist.txt\n\
                     +created\n\
                     *** Delete File: directory\n\
                     *** End Patch";

        let result = apply_patch(&json!({"patch": patch}), &policy);

        assert!(result.is_error);
        assert!(!root.join("should-not-exist.txt").exists());
        assert!(root.join("directory").is_dir());
    }

    #[cfg(windows)]
    #[test]
    fn apply_patch_preflights_case_insensitive_host_path_collisions() {
        let root = test_workspace();
        let policy = PermissionPolicy::workspace_write(root.clone());
        let patch = "*** Begin Patch\n\
                     *** Add File: Case.txt\n\
                     +one\n\
                     *** Add File: case.txt\n\
                     +two\n\
                     *** End Patch";

        let result = apply_patch(&json!({"patch": patch}), &policy);

        assert!(result.is_error);
        assert!(!root.join("Case.txt").exists());
        assert!(!root.join("case.txt").exists());
    }

    fn test_workspace() -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("animus-files-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }
}
