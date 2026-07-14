use std::collections::BTreeMap;
use std::io::Cursor;

use anima_file_tools::{
    grep, BackendCapabilities, BackendKind, BackendPath, DirectoryEntry, DirectoryListing,
    EntryMetadata, FileBackend, FileToolError, GrepMode, GrepRequest, MutationAtomicity,
    OperationControl, OperationLimits, PathSemantics, ReadBackend, ReadSeek, SkipReason,
    WalkBackend, MAX_PATTERN_BYTES,
};

struct SearchBackend {
    children: BTreeMap<String, Vec<DirectoryEntry>>,
    files: BTreeMap<String, Vec<u8>>,
}

impl FileBackend for SearchBackend {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::CoreFs,
            PathSemantics::PortableNfcCaseSensitive,
            MutationAtomicity::CatalogGeneration,
        )
    }
}

impl ReadBackend for SearchBackend {
    fn open_read(&self, path: &str) -> Result<Box<dyn ReadSeek + Send>, FileToolError> {
        self.files
            .get(path)
            .cloned()
            .map(|bytes| Box::new(Cursor::new(bytes)) as Box<dyn ReadSeek + Send>)
            .ok_or_else(|| backend_error("open_read", path, "not found"))
    }
}

impl WalkBackend for SearchBackend {
    fn metadata(&self, path: &str) -> Result<EntryMetadata, FileToolError> {
        if self.children.contains_key(path) {
            Ok(EntryMetadata::directory(false))
        } else if let Some(bytes) = self.files.get(path) {
            Ok(EntryMetadata::file(bytes.len() as u64))
        } else {
            Err(backend_error("metadata", path, "not found"))
        }
    }

    fn read_directory(&self, path: &str) -> Result<DirectoryListing, FileToolError> {
        self.children
            .get(path)
            .cloned()
            .map(Into::into)
            .ok_or_else(|| backend_error("read_directory", path, "not a directory"))
    }
}

#[test]
fn literal_grep_reports_stable_line_and_utf8_byte_offsets() {
    let backend =
        backend_with_files(&[("root/notes.md", "é\nneedle one\nneedle two\n".as_bytes())]);

    let page = grep(
        &backend,
        request("needle", GrepMode::Literal),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(page.matches.len(), 2);
    assert_eq!(page.matches[0].path.as_str(), "root/notes.md");
    assert_eq!(page.matches[0].line_number, 2);
    assert_eq!(page.matches[0].byte_offset, 3);
    assert_eq!(page.matches[0].excerpt, "needle one");
    assert_eq!(page.matches[1].line_number, 3);
    assert_eq!(page.matches[1].byte_offset, 14);
}

#[test]
fn grep_searches_a_file_root_without_requiring_a_directory_walk() {
    let backend = backend_with_files(&[("root/notes.md", b"needle in one file\n")]);
    let mut single_file = request("needle", GrepMode::Literal);
    single_file.root = BackendPath::new(BackendKind::CoreFs, "root/notes.md").unwrap();

    let page = grep(
        &backend,
        single_file,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(page.matches.len(), 1);
    assert_eq!(page.matches[0].path.as_str(), "root/notes.md");
    assert_eq!(page.matches[0].line_number, 1);
}

#[test]
fn regex_mode_uses_rust_linear_time_syntax_and_rejects_lookaround() {
    let backend = backend_with_files(&[("root/notes.md", b"alpha 123\nbeta\n")]);
    let mut valid = request(r"alpha\s+\d+", GrepMode::Regex);
    valid.max_matches = 10;
    assert_eq!(
        grep(
            &backend,
            valid,
            OperationLimits::default().validate().unwrap(),
            OperationControl::default(),
        )
        .unwrap()
        .matches
        .len(),
        1
    );

    let error = grep(
        &backend,
        request(r"alpha(?=\s)", GrepMode::Regex),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();
    assert!(matches!(error, FileToolError::InvalidPattern { .. }));
}

#[test]
fn binary_and_invalid_utf8_files_are_skipped_with_typed_reasons() {
    let backend = backend_with_files(&[
        ("root/binary.bin", b"needle\0binary"),
        (
            "root/invalid.txt",
            &[0xff, b'n', b'e', b'e', b'd', b'l', b'e'],
        ),
    ]);

    let page = grep(
        &backend,
        request("needle", GrepMode::Literal),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert!(page.matches.is_empty());
    assert_eq!(page.skipped.len(), 2);
    assert_eq!(page.skipped[0].reason, SkipReason::BinaryContent);
    assert_eq!(page.skipped[1].reason, SkipReason::InvalidUtf8);
}

#[test]
fn a_late_binary_marker_discards_earlier_text_matches_from_that_file() {
    let backend = backend_with_files(&[(
        "root/mixed.bin",
        b"needle looked textual\nbinary arrives later\0",
    )]);

    let page = grep(
        &backend,
        request("needle", GrepMode::Literal),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert!(page.matches.is_empty());
    assert_eq!(page.skipped.len(), 1);
    assert_eq!(page.skipped[0].reason, SkipReason::BinaryContent);
}

#[test]
fn match_limit_does_not_leak_text_from_a_file_with_a_late_binary_marker() {
    let backend = backend_with_files(&[(
        "root/mixed.bin",
        b"needle one\nneedle two\nbinary arrives later\0",
    )]);
    let mut limited = request("needle", GrepMode::Literal);
    limited.max_matches = 1;

    let page = grep(
        &backend,
        limited,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert!(page.matches.is_empty());
    assert_eq!(page.skipped.len(), 1);
    assert_eq!(page.skipped[0].reason, SkipReason::BinaryContent);
}

#[test]
fn file_limit_cursor_advances_to_later_files_even_when_first_page_has_no_matches() {
    let backend = backend_with_files(&[
        ("root/a.txt", b"nothing here"),
        ("root/b.txt", b"needle later"),
    ]);
    let mut first_request = request("needle", GrepMode::Literal);
    first_request.max_files = 1;

    let first = grep(
        &backend,
        first_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();
    assert!(first.matches.is_empty());
    assert!(first.truncated);
    assert!(first.next_cursor.is_some());

    let mut second_request = request("needle", GrepMode::Literal);
    second_request.max_files = 1;
    second_request.cursor = first.next_cursor;
    let second = grep(
        &backend,
        second_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(second.matches.len(), 1);
    assert_eq!(second.matches[0].path.as_str(), "root/b.txt");
}

#[test]
fn repeated_oversized_lines_produce_one_bounded_skip_record_per_file() {
    let backend = backend_with_files(&[("root/large.txt", b"123456789\nabcdefghi\n")]);
    let mut bounded = request("needle", GrepMode::Literal);
    bounded.max_line_bytes = 4;

    let page = grep(
        &backend,
        bounded,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(page.skipped.len(), 1);
    assert_eq!(page.skipped[0].reason, SkipReason::LineTooLong);
}

#[test]
fn oversized_search_patterns_fail_before_regex_or_matcher_compilation() {
    let backend = backend_with_files(&[]);
    let oversized = "x".repeat(MAX_PATTERN_BYTES + 1);

    let error = grep(
        &backend,
        request(&oversized, GrepMode::Regex),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(error, FileToolError::InvalidPattern { .. }));
}

#[test]
fn match_limit_returns_a_cursor_and_resumes_without_duplicate_matches() {
    let backend =
        backend_with_files(&[("root/notes.md", b"needle one\nneedle two\nneedle three\n")]);
    let mut first_request = request("needle", GrepMode::Literal);
    first_request.max_matches = 2;

    let first = grep(
        &backend,
        first_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(first.matches.len(), 2);
    assert!(first.truncated);

    let mut second_request = request("needle", GrepMode::Literal);
    second_request.max_matches = 2;
    second_request.cursor = first.next_cursor;
    let second = grep(
        &backend,
        second_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(second.matches.len(), 1);
    assert_eq!(second.matches[0].line_number, 3);
    assert!(!second.truncated);
}

#[test]
fn match_cursor_resumes_file_preorder_instead_of_comparing_paths_lexicographically() {
    let nested_path = "root/a/z";
    let sibling_path = "root/a.txt";
    let mut files = BTreeMap::new();
    files.insert(nested_path.to_string(), b"needle nested\n".to_vec());
    files.insert(sibling_path.to_string(), b"needle sibling\n".to_vec());
    let mut children = BTreeMap::new();
    children.insert(
        "root".to_string(),
        vec![
            DirectoryEntry::new(
                BackendPath::new(BackendKind::CoreFs, "root/a").unwrap(),
                EntryMetadata::directory(false),
            ),
            DirectoryEntry::new(
                BackendPath::new(BackendKind::CoreFs, sibling_path).unwrap(),
                EntryMetadata::file(15),
            ),
        ],
    );
    children.insert(
        "root/a".to_string(),
        vec![DirectoryEntry::new(
            BackendPath::new(BackendKind::CoreFs, nested_path).unwrap(),
            EntryMetadata::file(14),
        )],
    );
    let backend = SearchBackend { children, files };
    let mut first_request = request("needle", GrepMode::Literal);
    first_request.max_matches = 1;

    let first = grep(
        &backend,
        first_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();
    assert_eq!(first.matches[0].path.as_str(), nested_path);

    let mut second_request = request("needle", GrepMode::Literal);
    second_request.max_matches = 1;
    second_request.cursor = first.next_cursor;
    let second = grep(
        &backend,
        second_request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(second.matches.len(), 1);
    assert_eq!(second.matches[0].path.as_str(), sibling_path);
}

fn request(query: &str, mode: GrepMode) -> GrepRequest {
    GrepRequest {
        root: BackendPath::new(BackendKind::CoreFs, "root").unwrap(),
        query: query.to_string(),
        mode,
        cursor: None,
        max_files: 100,
        max_matches: 100,
        max_line_bytes: 64 * 1024,
    }
}

fn backend_with_files(files: &[(&str, &[u8])]) -> SearchBackend {
    let mut file_map = BTreeMap::new();
    let mut entries = Vec::new();
    for (path, bytes) in files {
        file_map.insert((*path).to_string(), bytes.to_vec());
        entries.push(DirectoryEntry::new(
            BackendPath::new(BackendKind::CoreFs, *path).unwrap(),
            EntryMetadata::file(bytes.len() as u64),
        ));
    }
    let mut children = BTreeMap::new();
    children.insert("root".to_string(), entries);
    SearchBackend {
        children,
        files: file_map,
    }
}

fn backend_error(operation: &'static str, path: &str, message: &str) -> FileToolError {
    FileToolError::Backend {
        operation,
        path: path.to_string(),
        message: message.to_string(),
    }
}
