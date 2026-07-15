use std::collections::BTreeMap;

use anima_file_tools::{
    glob, BackendCapabilities, BackendKind, BackendPath, DirectoryEntry, DirectoryListing,
    EntryMetadata, FileBackend, FileToolError, GlobRequest, MutationAtomicity, OperationControl,
    OperationLimits, PathSemantics, WalkBackend, MAX_PATTERN_BYTES,
};

struct MemoryTree {
    children: BTreeMap<String, Vec<DirectoryEntry>>,
}

impl FileBackend for MemoryTree {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::CoreFs,
            PathSemantics::PortableNfcCaseSensitive,
            MutationAtomicity::CatalogGeneration,
        )
    }
}

impl WalkBackend for MemoryTree {
    fn metadata(&self, path: &str) -> Result<EntryMetadata, FileToolError> {
        if self.children.contains_key(path) {
            Ok(EntryMetadata::directory(false))
        } else {
            Ok(EntryMetadata::file(0))
        }
    }

    fn read_directory(&self, path: &str) -> Result<DirectoryListing, FileToolError> {
        self.children
            .get(path)
            .cloned()
            .map(Into::into)
            .ok_or_else(|| FileToolError::Backend {
                operation: "read_directory",
                path: path.to_string(),
                message: "not a directory".to_string(),
            })
    }
}

#[test]
fn glob_matches_root_relative_paths_with_real_globstar_semantics() {
    let tree = sample_tree();
    let page = glob(
        &tree,
        request("**/*.rs", 10),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(
        page.matches
            .iter()
            .map(|path| path.as_str())
            .collect::<Vec<_>>(),
        vec!["root/src/lib.rs", "root/src/nested/mod.rs"]
    );
}

#[test]
fn invalid_globs_return_a_typed_error() {
    let error = glob(
        &sample_tree(),
        request("[", 10),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::InvalidPattern { mode: "glob", .. }
    ));
}

#[test]
fn result_limit_returns_a_stable_cursor_without_duplicates() {
    let tree = sample_tree();
    let first = glob(
        &tree,
        request("**/*.rs", 1),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(first.matches[0].as_str(), "root/src/lib.rs");
    assert!(first.truncated);

    let mut second_request = request("**/*.rs", 1);
    second_request.cursor = first.next_cursor;
    let second = glob(
        &tree,
        second_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(second.matches[0].as_str(), "root/src/nested/mod.rs");
}

#[test]
fn cursor_resumes_file_preorder_without_lexicographic_filtering() {
    let mut children = BTreeMap::new();
    children.insert(
        "root".to_string(),
        vec![directory("root/a"), file("root/a.txt")],
    );
    children.insert("root/a".to_string(), vec![file("root/a/z.txt")]);
    let tree = MemoryTree { children };

    let first = glob(
        &tree,
        request("**/*.txt", 1),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(first.matches[0].as_str(), "root/a/z.txt");

    let mut second_request = request("**/*.txt", 1);
    second_request.cursor = first.next_cursor;
    let second = glob(
        &tree,
        second_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(second.matches[0].as_str(), "root/a.txt");
}

#[test]
fn hard_walk_ceiling_is_terminal_instead_of_returning_a_repeating_cursor() {
    let limits = OperationLimits {
        walk_entries: 1,
        ..OperationLimits::default()
    }
    .validate()
    .unwrap();
    let page = glob(
        &sample_tree(),
        request("**/*.rs", 1),
        limits,
        OperationControl::default(),
    )
    .unwrap();

    assert!(page.truncated);
    assert!(page.limit_reached);
    assert!(page.next_cursor.is_none());
}

#[test]
fn oversized_glob_patterns_fail_before_compilation() {
    let error = glob(
        &sample_tree(),
        request(&"*".repeat(MAX_PATTERN_BYTES + 1), 1),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(error, FileToolError::InvalidPattern { .. }));
}

fn request(pattern: &str, max_results: usize) -> GlobRequest {
    GlobRequest {
        root: BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        pattern: pattern.to_string(),
        cursor: None,
        max_results,
    }
}

fn sample_tree() -> MemoryTree {
    let mut children = BTreeMap::new();
    children.insert(
        "root".to_string(),
        vec![directory("root/src"), file("root/README.md")],
    );
    children.insert(
        "root/src".to_string(),
        vec![directory("root/src/nested"), file("root/src/lib.rs")],
    );
    children.insert(
        "root/src/nested".to_string(),
        vec![file("root/src/nested/mod.rs")],
    );
    MemoryTree { children }
}

fn file(path: &str) -> DirectoryEntry {
    DirectoryEntry::new(
        BackendPath::new(BackendKind::CoreFs, path).unwrap(),
        EntryMetadata::file(0),
    )
}

fn directory(path: &str) -> DirectoryEntry {
    DirectoryEntry::new(
        BackendPath::new(BackendKind::CoreFs, path).unwrap(),
        EntryMetadata::directory(false),
    )
}
