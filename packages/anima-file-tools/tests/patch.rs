use std::collections::BTreeMap;

use anima_file_tools::{
    parse_patch, plan_patch, MutationAtomicity, PatchError, PatchOperation, PatchSnapshot,
    PlannedMutation, MAX_BACKEND_PATH_BYTES, MAX_PATCH_BYTES,
};

#[test]
fn parses_add_delete_update_move_and_multiple_chunks_into_typed_operations() {
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Add File: new.txt\n\
         +hello\n\
         *** Delete File: old.txt\n\
         *** Update File: src/lib.rs\n\
         *** Move to: src/main.rs\n\
         @@\n\
         -old one\n\
         +new one\n\
         @@ tail\n\
         -old two\n\
         +new two\n\
         *** End Patch",
    )
    .unwrap();

    assert_eq!(patch.operations.len(), 3);
    assert!(matches!(patch.operations[0], PatchOperation::Add { .. }));
    assert!(matches!(patch.operations[1], PatchOperation::Delete { .. }));
    let PatchOperation::Update {
        source,
        destination,
        chunks,
    } = &patch.operations[2]
    else {
        panic!("expected typed update");
    };
    assert_eq!(source.as_str(), "src/lib.rs");
    assert_eq!(destination.as_ref().unwrap().as_str(), "src/main.rs");
    assert_eq!(chunks.len(), 2);
    assert_eq!(chunks[1].context.as_deref(), Some("tail"));
}

#[test]
fn parser_rejects_oversized_patch_bodies_and_paths_before_accumulation() {
    assert!(parse_patch(&"x".repeat(MAX_PATCH_BYTES + 1)).is_err());
    let oversized_path = "x".repeat(MAX_BACKEND_PATH_BYTES + 1);
    let patch = format!("*** Begin Patch\n*** Add File: {oversized_path}\n+content\n*** End Patch");

    assert!(matches!(
        parse_patch(&patch),
        Err(PatchError::InvalidPath { .. })
    ));
}

#[test]
fn parser_rejects_empty_absolute_parent_and_malformed_patches() {
    for body in [
        "*** Begin Patch\n*** End Patch",
        "*** Begin Patch\n*** Delete File: ../secret\n*** End Patch",
        "*** Begin Patch\n*** Delete File: src/./secret\n*** End Patch",
        "*** Begin Patch\n*** Add File: C:\\secret.txt\n+x\n*** End Patch",
        "*** Begin Patch\n*** Add File: corefs:notes/today.md\n+x\n*** End Patch",
        "*** Begin Patch\n*** Update File: file.txt\n@@\nwat\n*** End Patch",
    ] {
        assert!(
            parse_patch(body).is_err(),
            "patch unexpectedly parsed: {body}"
        );
    }
}

#[test]
fn parser_rejects_a_multibyte_update_prefix_without_panicking() {
    let error = parse_patch(
        "*** Begin Patch\n\
         *** Update File: file.txt\n\
         @@\n\
         éinvalid\n\
         *** End Patch",
    )
    .unwrap_err();

    assert!(matches!(error, PatchError::Parse { .. }));
}

#[test]
fn planner_applies_ordered_chunks_and_reports_backend_atomicity() {
    let snapshot = MemorySnapshot::new(&[("src/lib.rs", "one\ntwo\ntail\n")]);
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Update File: src/lib.rs\n\
         @@\n\
         -one\n\
         +ONE\n\
         @@ tail\n\
         +after tail\n\
         *** End Patch",
    )
    .unwrap();

    let plan = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap();

    assert_eq!(plan.atomicity, MutationAtomicity::BestEffort);
    assert_eq!(plan.mutations.len(), 1);
    let PlannedMutation::Write { content, .. } = &plan.mutations[0] else {
        panic!("expected write");
    };
    assert_eq!(content, "ONE\ntwo\ntail\nafter tail\n");
}

#[test]
fn planner_preflights_every_operation_before_returning_any_mutation() {
    let snapshot = MemorySnapshot::new(&[]);
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Add File: created.txt\n\
         +created\n\
         *** Update File: missing.txt\n\
         @@\n\
         -old\n\
         +new\n\
         *** End Patch",
    )
    .unwrap();

    let error = plan_patch(&snapshot, &patch, MutationAtomicity::CatalogGeneration).unwrap_err();

    assert!(matches!(error, PatchError::MissingPath { .. }));
}

#[test]
fn planner_rejects_destination_collisions_before_a_move() {
    let snapshot = MemorySnapshot::new(&[("old.txt", "old\n"), ("new.txt", "occupied\n")]);
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Update File: old.txt\n\
         *** Move to: new.txt\n\
         @@\n\
         -old\n\
         +new\n\
         *** End Patch",
    )
    .unwrap();

    let error = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap_err();

    assert!(matches!(error, PatchError::PathAlreadyExists { .. }));
}

#[test]
fn planner_rejects_virtual_file_and_descendant_collisions_in_either_order() {
    let snapshot = MemorySnapshot::new(&[]);

    for patch in [
        "*** Begin Patch\n\
         *** Add File: parent\n\
         +file\n\
         *** Add File: parent/child.txt\n\
         +child\n\
         *** End Patch",
        "*** Begin Patch\n\
         *** Add File: parent/child.txt\n\
         +child\n\
         *** Add File: parent\n\
         +file\n\
         *** End Patch",
    ] {
        let patch = parse_patch(patch).unwrap();
        let error = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap_err();

        assert!(matches!(error, PatchError::PathAlreadyExists { .. }));
    }
}

#[test]
fn planner_rejects_moving_a_file_beneath_itself_before_apply() {
    let snapshot = MemorySnapshot::new(&[("parent", "file\n")]);
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Update File: parent\n\
         *** Move to: parent/child.txt\n\
         *** End Patch",
    )
    .unwrap();

    let error = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap_err();

    assert!(matches!(error, PatchError::PathAlreadyExists { .. }));
}

#[test]
fn planner_tracks_a_deleted_symlink_entry_separately_from_its_target() {
    let snapshot = SymlinkSnapshot;
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Delete File: link.txt\n\
         *** Update File: target.txt\n\
         @@\n\
         -old\n\
         +new\n\
         *** End Patch",
    )
    .unwrap();

    let plan = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap();

    assert_eq!(plan.mutations.len(), 2);
}

#[test]
fn planner_recreates_a_deleted_symlink_path_as_a_regular_file() {
    let snapshot = SymlinkSnapshot;
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Delete File: link.txt\n\
         *** Add File: link.txt\n\
         +replacement\n\
         *** Update File: link.txt\n\
         @@\n\
         -replacement\n\
         +updated\n\
         *** End Patch",
    )
    .unwrap();

    let plan = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap();

    assert_eq!(plan.mutations.len(), 3);
    let PlannedMutation::Write { content, .. } = &plan.mutations[2] else {
        panic!("expected final write");
    };
    assert_eq!(content, "updated\n");
}

#[test]
fn parser_keeps_marker_like_text_when_it_is_a_prefixed_context_line() {
    let patch = parse_patch(concat!(
        "*** Begin Patch\n",
        "*** Update File: markers.txt\n",
        "@@\n",
        " *** End Patch\n",
        "-old\n",
        "+new\n",
        "*** End Patch",
    ))
    .unwrap();

    let PatchOperation::Update { chunks, .. } = &patch.operations[0] else {
        panic!("expected update");
    };
    assert_eq!(chunks[0].old_lines[0], "*** End Patch");
    assert_eq!(chunks[0].new_lines[0], "*** End Patch");
}

#[test]
fn move_only_patch_preserves_source_bytes_instead_of_normalizing_content() {
    let snapshot = MemorySnapshot::new(&[("old.txt", "no trailing newline")]);
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Update File: old.txt\n\
         *** Move to: new.txt\n\
         *** End Patch",
    )
    .unwrap();

    let plan = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap();
    let PlannedMutation::Write { content, .. } = &plan.mutations[0] else {
        panic!("expected write");
    };
    assert_eq!(content, "no trailing newline");
}

#[test]
fn update_preserves_a_missing_final_newline() {
    let snapshot = MemorySnapshot::new(&[("config.txt", "one\ntwo")]);
    let patch = parse_patch(
        "*** Begin Patch\n\
         *** Update File: config.txt\n\
         @@\n\
         -one\n\
         +ONE\n\
         *** End Patch",
    )
    .unwrap();

    let plan = plan_patch(&snapshot, &patch, MutationAtomicity::BestEffort).unwrap();
    let PlannedMutation::Write { content, .. } = &plan.mutations[0] else {
        panic!("expected write");
    };
    assert_eq!(content, "ONE\ntwo");
}

struct MemorySnapshot(BTreeMap<String, String>);

impl MemorySnapshot {
    fn new(files: &[(&str, &str)]) -> Self {
        Self(
            files
                .iter()
                .map(|(path, content)| ((*path).to_string(), (*content).to_string()))
                .collect(),
        )
    }
}

impl PatchSnapshot for MemorySnapshot {
    fn read_text(&self, path: &str) -> Result<Option<String>, PatchError> {
        Ok(self.0.get(path).cloned())
    }
}

struct SymlinkSnapshot;

impl PatchSnapshot for SymlinkSnapshot {
    fn read_text(&self, path: &str) -> Result<Option<String>, PatchError> {
        Ok(matches!(path, "link.txt" | "target.txt").then(|| "old\n".to_string()))
    }

    fn canonical_key(&self, path: &str) -> Result<String, PatchError> {
        Ok(if path == "link.txt" {
            "target.txt".to_string()
        } else {
            path.to_string()
        })
    }

    fn canonical_entry_key(&self, path: &str) -> Result<String, PatchError> {
        Ok(path.to_string())
    }
}
