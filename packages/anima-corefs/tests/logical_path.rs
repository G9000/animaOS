use anima_corefs::logical::{map_migration_component, LogicalPath};

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
fn legacy_components_map_through_the_same_native_logical_rules() {
    for source in [
        ".anima",
        "objects",
        "bad/name",
        "bad\\name",
        "ambiguous\u{ff0f}name",
    ] {
        let mapped = map_migration_component(source, "01J00000000000000000000000").unwrap();
        assert_ne!(mapped, source);
        assert!(LogicalPath::parse(&format!("Notes/{mapped}")).is_ok());
    }
    assert_eq!(
        map_migration_component("Cafe\u{301}", "01J00000000000000000000000").unwrap(),
        "Caf\u{e9}"
    );
    assert!(
        map_migration_component(&"x".repeat(300), "01J00000000000000000000000")
            .unwrap()
            .len()
            <= 255
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
        "Notes/soul",
        "Notes/SoUl",
        "Notes/CUTOVER_RECEIPT",
        "Notes/cutover_complete",
        "Notes/COMMIT.LOCK",
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
