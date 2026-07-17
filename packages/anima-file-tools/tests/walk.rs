use std::collections::BTreeMap;

use anima_file_tools::{
    walk_page, BackendCapabilities, BackendKind, BackendPath, DirectoryEntry, DirectoryListing,
    EntryKind, EntryMetadata, FileBackend, FileToolError, MutationAtomicity, OperationControl,
    OperationLimits, PathSemantics, WalkBackend, WalkOptions, MAX_WALK_ERRORS,
    MAX_WALK_ERROR_MESSAGE_BYTES,
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
fn walk_pages_are_deterministic_and_resume_after_the_cursor() {
    let tree = sample_tree();
    let limits = OperationLimits::default().validate().unwrap();
    let root = BackendPath::new(BackendKind::CoreFs, "root").unwrap();

    let first = walk_page(
        &tree,
        root.clone(),
        WalkOptions {
            page_size: 2,
            cursor: None,
            include_directories: true,
        },
        limits,
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(paths(&first.entries), vec!["root/a.txt", "root/b"]);
    assert!(first.truncated);

    let second = walk_page(
        &tree,
        root,
        WalkOptions {
            page_size: 2,
            cursor: first.next_cursor,
            include_directories: true,
        },
        limits,
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(
        paths(&second.entries),
        vec!["root/b/inside.txt", "root/c.txt"]
    );
    assert!(!second.truncated);
    assert!(second.next_cursor.is_none());
}

#[test]
fn first_walk_item_that_cannot_fit_is_a_typed_error() {
    let error = walk_page(
        &sample_tree(),
        BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        WalkOptions {
            page_size: 10,
            cursor: None,
            include_directories: true,
        },
        OperationLimits {
            response_bytes: 1,
            ..OperationLimits::default()
        }
        .validate()
        .unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::ResponseItemTooLarge { kind: "walk", .. }
    ));
}

#[test]
fn later_walk_item_truncates_with_an_advancing_cursor() {
    let page = walk_page(
        &sample_tree(),
        BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        WalkOptions {
            page_size: 10,
            cursor: None,
            include_directories: true,
        },
        OperationLimits {
            response_bytes: 380,
            ..OperationLimits::default()
        }
        .validate()
        .unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(page.entries.len(), 1);
    assert!(page.truncated);
    assert_eq!(
        page.next_cursor.unwrap().as_str(),
        page.entries[0].path.as_str()
    );
}

#[test]
fn cursor_resumes_preorder_instead_of_filtering_paths_lexicographically() {
    let mut children = BTreeMap::new();
    children.insert(
        "root".to_string(),
        vec![directory("root/a"), file("root/a.txt")],
    );
    children.insert("root/a".to_string(), vec![file("root/a/z")]);
    let tree = MemoryTree { children };
    let limits = OperationLimits::default().validate().unwrap();
    let root = BackendPath::new(BackendKind::CoreFs, "root").unwrap();

    let first = walk_page(
        &tree,
        root.clone(),
        WalkOptions {
            page_size: 1,
            cursor: None,
            include_directories: false,
        },
        limits,
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(paths(&first.entries), vec!["root/a/z"]);

    let second = walk_page(
        &tree,
        root,
        WalkOptions {
            page_size: 1,
            cursor: first.next_cursor,
            include_directories: false,
        },
        limits,
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(paths(&second.entries), vec!["root/a.txt"]);
    assert!(!second.truncated);
}

#[test]
fn entry_ceiling_without_a_resumable_position_is_a_typed_error() {
    let tree = sample_tree();
    let limits = OperationLimits {
        walk_entries: 2,
        ..OperationLimits::default()
    }
    .validate()
    .unwrap();

    let error = walk_page(
        &tree,
        BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        WalkOptions {
            page_size: 20,
            cursor: None,
            include_directories: true,
        },
        limits,
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::PaginationCannotAdvance { operation: "walk" }
    ));
}

#[test]
fn directory_symlinks_are_visible_but_never_traversed() {
    let link = DirectoryEntry::new(
        BackendPath::new(BackendKind::CoreFs, "root/link").unwrap(),
        EntryMetadata::directory(true),
    );
    let mut children = BTreeMap::new();
    children.insert("root".to_string(), vec![link]);
    children.insert(
        "root/link".to_string(),
        vec![DirectoryEntry::new(
            BackendPath::new(BackendKind::CoreFs, "root/link/secret.txt").unwrap(),
            EntryMetadata::file(1),
        )],
    );
    let tree = MemoryTree { children };

    let page = walk_page(
        &tree,
        BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        WalkOptions {
            page_size: 20,
            cursor: None,
            include_directories: true,
        },
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(paths(&page.entries), vec!["root/link"]);
    assert_eq!(page.entries[0].kind, EntryKind::Directory);
    assert!(page.entries[0].is_symlink);
}

struct FailingTree {
    children: Vec<DirectoryEntry>,
}

impl FileBackend for FailingTree {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::CoreFs,
            PathSemantics::PortableNfcCaseSensitive,
            MutationAtomicity::CatalogGeneration,
        )
    }
}

impl WalkBackend for FailingTree {
    fn metadata(&self, _path: &str) -> Result<EntryMetadata, FileToolError> {
        Ok(EntryMetadata::directory(false))
    }

    fn read_directory(&self, path: &str) -> Result<DirectoryListing, FileToolError> {
        if path == "root" {
            return Ok(self.children.clone().into());
        }
        Err(FileToolError::Backend {
            operation: "read_directory",
            path: path.to_string(),
            message: "x".repeat(MAX_WALK_ERROR_MESSAGE_BYTES * 4),
        })
    }
}

#[test]
fn traversal_error_ceiling_without_a_resumable_position_is_a_typed_error() {
    let tree = FailingTree {
        children: (0..MAX_WALK_ERRORS + 5)
            .map(|index| directory(&format!("root/dir-{index:03}")))
            .collect(),
    };

    let error = walk_page(
        &tree,
        BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        WalkOptions {
            page_size: MAX_WALK_ERRORS + 10,
            cursor: None,
            include_directories: true,
        },
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::PaginationCannotAdvance { operation: "walk" }
    ));
}

struct TruncatedDirectoryTree;

impl FileBackend for TruncatedDirectoryTree {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::CoreFs,
            PathSemantics::PortableNfcCaseSensitive,
            MutationAtomicity::CatalogGeneration,
        )
    }
}

impl WalkBackend for TruncatedDirectoryTree {
    fn metadata(&self, path: &str) -> Result<EntryMetadata, FileToolError> {
        if path == "root" {
            Ok(EntryMetadata::directory(false))
        } else {
            Ok(EntryMetadata::file(0))
        }
    }

    fn read_directory(&self, _path: &str) -> Result<DirectoryListing, FileToolError> {
        Ok(DirectoryListing {
            entries: vec![file("root/first.txt")],
            truncated: true,
        })
    }
}

#[test]
fn backend_directory_truncation_without_a_resumable_position_is_a_typed_error() {
    let error = walk_page(
        &TruncatedDirectoryTree,
        BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        WalkOptions {
            page_size: 10,
            cursor: None,
            include_directories: true,
        },
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::PaginationCannotAdvance { operation: "walk" }
    ));
}

fn sample_tree() -> MemoryTree {
    let mut children = BTreeMap::new();
    children.insert(
        "root".to_string(),
        vec![file("root/c.txt"), directory("root/b"), file("root/a.txt")],
    );
    children.insert("root/b".to_string(), vec![file("root/b/inside.txt")]);
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

fn paths(entries: &[anima_file_tools::WalkEntry]) -> Vec<&str> {
    entries.iter().map(|entry| entry.path.as_str()).collect()
}
