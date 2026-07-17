use anima_corefs::logical::LogicalPath;

#[test]
fn logical_paths_are_root_relative_nfc_and_case_sensitive() {
    assert_eq!(LogicalPath::parse("").unwrap().as_str(), "");
    assert_eq!(
        LogicalPath::parse("Notes/Caf\u{e9}.md").unwrap().as_str(),
        "Notes/Caf\u{e9}.md"
    );
    assert_ne!(
        LogicalPath::parse("Notes/Case.md").unwrap(),
        LogicalPath::parse("Notes/case.md").unwrap()
    );
}

#[test]
fn logical_paths_reject_unsafe_or_non_canonical_forms() {
    for path in [
        "/Notes/today.md",
        r"C:\Notes\today.md",
        r"Notes\today.md",
        "Notes/../today.md",
        "Notes//today.md",
        "Notes/today.md/",
        "Notes/Cafe\u{301}.md",
        "Notes/ambiguous\u{ff0f}name.md",
        "Notes/.anima",
        "Notes/objects",
        "notes\0hidden",
    ] {
        assert!(
            LogicalPath::parse(path).is_err(),
            "unsafe path must be rejected: {path:?}"
        );
    }
}

#[test]
fn logical_paths_reject_cross_backend_and_uri_forms() {
    for path in [
        "corefs://object/01J00000000000000000000000",
        "corefs:Notes/today.md",
        "hostfs://workspace/file.txt",
        "file:///tmp/private.txt",
        "https://example.invalid/file.txt",
    ] {
        assert!(
            LogicalPath::parse(path).is_err(),
            "foreign backend form must be rejected: {path}"
        );
    }
}
