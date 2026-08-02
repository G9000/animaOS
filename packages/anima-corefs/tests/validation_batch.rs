use std::fs;

use anima_corefs::crypto::{derive_corefs_subkeys, ObjectKind, SecretBytes};
use anima_corefs::envelope::BodyEncoding;
use anima_corefs::id::OpaqueId;
use anima_corefs::transaction::{
    CoreCommitCoordinator, ValidationBatch, ValidationBatchError, ValidationBatchFolder,
    ValidationBatchMode, ValidationBatchObject, ValidationBatchPolicy, MAX_WRITING_DOCUMENT_BYTES,
};
use serde_json::json;

const CORE_ID: &str = "core-writing-converter";

fn native_id(domain: &str, value: &str) -> String {
    OpaqueId::derive_migration(domain, value.as_bytes())
        .unwrap()
        .as_str()
        .to_owned()
}

fn fixture(name: &str) -> (std::path::PathBuf, CoreCommitCoordinator) {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-validation-batch-{}-{name}-{}",
        std::process::id(),
        native_id("fixture", name)
    ));
    let _ = fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    (root, coordinator)
}

fn keys() -> anima_corefs::crypto::FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![0x77; 32]).unwrap(), 1).unwrap()
}

fn folders() -> Vec<ValidationBatchFolder> {
    let root = native_id("folder", "root");
    vec![
        ValidationBatchFolder {
            stable_id: root.clone(),
            parent_id: None,
            name: "Core".into(),
            role: None,
            policy: ValidationBatchPolicy::UserWrite,
            metadata: Default::default(),
        },
        ValidationBatchFolder {
            stable_id: native_id("folder", "journal"),
            parent_id: Some(root.clone()),
            name: "Journal".into(),
            role: Some("core.journal".into()),
            policy: ValidationBatchPolicy::UserWrite,
            metadata: Default::default(),
        },
        ValidationBatchFolder {
            stable_id: native_id("folder", "notes"),
            parent_id: Some(root),
            name: "Notes".into(),
            role: Some("core.notes".into()),
            policy: ValidationBatchPolicy::UserWrite,
            metadata: Default::default(),
        },
    ]
}

fn initial_batch() -> ValidationBatch {
    let attachment = native_id("attachment", "1");
    ValidationBatch {
        mode: ValidationBatchMode::Initialize,
        folders: folders(),
        objects: vec![
            ValidationBatchObject {
                stable_id: attachment.clone(),
                parent_id: native_id("folder", "journal"),
                name: "cover.png".into(),
                kind: ObjectKind::Attachment,
                content_type: "image/png".into(),
                body_encoding: BodyEncoding::Binary,
                content: b"png".to_vec(),
                created_at: "2026-08-02T00:00:00Z".into(),
                updated_at: "2026-08-02T00:00:00Z".into(),
                expected_revision: None,
                references: vec![],
                policy: ValidationBatchPolicy::Inherit,
                metadata: Default::default(),
            },
            ValidationBatchObject {
                stable_id: native_id("diary", "1"),
                parent_id: native_id("folder", "journal"),
                name: "2026-08-02.diary.json".into(),
                kind: ObjectKind::Diary,
                content_type: "application/vnd.anima.diary+json;version=1".into(),
                body_encoding: BodyEncoding::Utf8,
                content: br#"{"format":"anima.diary","version":1}"#.to_vec(),
                created_at: "2026-08-02T00:00:00Z".into(),
                updated_at: "2026-08-02T00:00:00Z".into(),
                expected_revision: None,
                references: vec![attachment],
                policy: ValidationBatchPolicy::Deny,
                metadata: Default::default(),
            },
            ValidationBatchObject {
                stable_id: native_id("draft", "1"),
                parent_id: native_id("folder", "journal"),
                name: "working.draft.json".into(),
                kind: ObjectKind::Draft,
                content_type: "application/vnd.anima.draft+json;version=1".into(),
                body_encoding: BodyEncoding::Utf8,
                content: br#"{"format":"anima.draft","version":1}"#.to_vec(),
                created_at: "2026-08-02T00:00:00Z".into(),
                updated_at: "2026-08-02T00:00:00Z".into(),
                expected_revision: None,
                references: vec![],
                policy: ValidationBatchPolicy::Inherit,
                metadata: Default::default(),
            },
            ValidationBatchObject {
                stable_id: native_id("note", "1"),
                parent_id: native_id("folder", "notes"),
                name: "note.note.json".into(),
                kind: ObjectKind::Note,
                content_type: "application/vnd.anima.note+json;version=1".into(),
                body_encoding: BodyEncoding::Utf8,
                content: br#"{"format":"anima.note","version":1}"#.to_vec(),
                created_at: "2026-08-02T00:00:00Z".into(),
                updated_at: "2026-08-02T00:00:00Z".into(),
                expected_revision: None,
                references: vec![],
                policy: ValidationBatchPolicy::Deny,
                metadata: Default::default(),
            },
        ],
    }
}

#[test]
fn malformed_or_wrong_head_batches_never_change_validation_head() {
    let (root, coordinator) = fixture("atomic");
    let keys = keys();
    let mut malformed = initial_batch();
    malformed
        .folders
        .retain(|folder| folder.role.as_deref() != Some("core.notes"));
    assert!(matches!(
        coordinator.apply_validation_batch(&keys, malformed),
        Err(ValidationBatchError::Invalid(_))
    ));
    assert!(coordinator
        .load_validation_snapshot(&keys)
        .unwrap()
        .is_none());
    assert!(!root.join("fs").join("VALIDATION_HEAD").exists());

    let first = coordinator
        .apply_validation_batch(&keys, initial_batch())
        .unwrap();
    assert!(first.published());
    let before = first.snapshot().head().clone();
    let mut wrong = initial_batch();
    wrong.mode = ValidationBatchMode::Expect {
        generation: before.generation() + 1,
        catalog_hash: before.catalog_hash().to_owned(),
    };
    assert!(matches!(
        coordinator.apply_validation_batch(&keys, wrong),
        Err(ValidationBatchError::HeadMismatch)
    ));
    assert_eq!(
        coordinator
            .load_validation_snapshot(&keys)
            .unwrap()
            .unwrap()
            .head(),
        &before
    );
}

#[test]
fn identical_rerun_is_idempotent_and_roles_survive_reopen() {
    let (root, coordinator) = fixture("idempotent-role");
    let keys = keys();
    let first = coordinator
        .apply_validation_batch(&keys, initial_batch())
        .unwrap();
    let first_head = first.snapshot().head().clone();
    let mut rerun = initial_batch();
    rerun.mode = ValidationBatchMode::Expect {
        generation: first_head.generation(),
        catalog_hash: first_head.catalog_hash().to_owned(),
    };
    for object in &mut rerun.objects {
        object.expected_revision = Some(1);
    }
    let repeated = coordinator
        .apply_validation_batch(&keys, rerun.clone())
        .unwrap();
    assert!(!repeated.published());
    assert_eq!(repeated.snapshot().head(), &first_head);

    let mut moved = rerun;
    moved.folders[1].name = "Renamed Journal".into();
    moved.folders[2].parent_id = Some(native_id("folder", "journal"));
    let moved = coordinator.apply_validation_batch(&keys, moved).unwrap();
    assert!(moved.published());
    assert_eq!(moved.snapshot().head().generation(), 2);

    drop(coordinator);
    let reopened = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let journal = reopened
        .resolve_validation_role(&keys, "core.journal")
        .unwrap()
        .unwrap();
    let notes = reopened
        .resolve_validation_role(&keys, "core.notes")
        .unwrap()
        .unwrap();
    assert_eq!(journal.stable_id, native_id("folder", "journal"));
    assert_eq!(notes.stable_id, native_id("folder", "notes"));
    assert_eq!(journal.generation, 2);
}

#[test]
fn changed_object_requires_revision_precondition_and_publishes_once() {
    let (_root, coordinator) = fixture("revision");
    let keys = keys();
    let first = coordinator
        .apply_validation_batch(&keys, initial_batch())
        .unwrap();
    let head = first.snapshot().head().clone();
    let mut changed = initial_batch();
    changed.mode = ValidationBatchMode::Expect {
        generation: head.generation(),
        catalog_hash: head.catalog_hash().to_owned(),
    };
    for object in &mut changed.objects {
        object.expected_revision = Some(1);
    }
    changed.objects[1].content = b"changed diary".to_vec();
    let next = coordinator.apply_validation_batch(&keys, changed).unwrap();
    assert!(next.published());
    assert_eq!(next.snapshot().head().generation(), head.generation() + 1);
    let diary = next
        .snapshot()
        .catalog()
        .entries()
        .iter()
        .find(|entry| entry.stable_id().as_str() == native_id("diary", "1"))
        .unwrap();
    assert_eq!(diary.object_payload().unwrap().revision(), 2);
}

#[test]
fn same_body_envelope_field_changes_create_revisions_and_exact_reruns_are_noops() {
    type Mutation = fn(&mut ValidationBatchObject);

    let cases: [(&str, Mutation); 4] = [
        ("content-type", |object| {
            object.content_type = "image/webp".into();
        }),
        ("created-at", |object| {
            object.created_at = "2026-08-01T23:59:59Z".into();
        }),
        ("updated-at", |object| {
            object.updated_at = "2026-08-02T00:00:01Z".into();
        }),
        ("metadata", |object| {
            object.metadata.insert("source".into(), json!("legacy"));
        }),
    ];

    for (case, mutate) in cases {
        let (_root, coordinator) = fixture(case);
        let keys = keys();
        let first = coordinator
            .apply_validation_batch(&keys, initial_batch())
            .unwrap();
        let first_head = first.snapshot().head().clone();
        let mut changed = initial_batch();
        changed.mode = ValidationBatchMode::Expect {
            generation: first_head.generation(),
            catalog_hash: first_head.catalog_hash().to_owned(),
        };
        for object in &mut changed.objects {
            object.expected_revision = Some(1);
        }
        mutate(&mut changed.objects[0]);

        let revised = coordinator
            .apply_validation_batch(&keys, changed.clone())
            .unwrap();
        assert!(revised.published(), "{case} change was treated as a no-op");
        let revised_head = revised.snapshot().head().clone();
        let attachment = revised
            .snapshot()
            .catalog()
            .entries()
            .iter()
            .find(|entry| entry.stable_id().as_str() == native_id("attachment", "1"))
            .unwrap();
        assert_eq!(attachment.object_payload().unwrap().revision(), 2, "{case}");

        changed.mode = ValidationBatchMode::Expect {
            generation: revised_head.generation(),
            catalog_hash: revised_head.catalog_hash().to_owned(),
        };
        for object in &mut changed.objects {
            object.expected_revision = Some(if object.stable_id == native_id("attachment", "1") {
                2
            } else {
                1
            });
        }
        let repeated = coordinator.apply_validation_batch(&keys, changed).unwrap();
        assert!(!repeated.published(), "identical {case} rerun published");
        assert_eq!(repeated.snapshot().head(), &revised_head, "{case}");
    }
}

#[test]
fn publication_contains_encrypted_note_and_draft_revisions() {
    let (_root, coordinator) = fixture("note-draft");
    let keys = keys();
    let outcome = coordinator
        .apply_validation_batch(&keys, initial_batch())
        .unwrap();
    let entries = outcome.snapshot().catalog().entries();
    for (domain, value, kind) in [
        ("draft", "1", ObjectKind::Draft),
        ("note", "1", ObjectKind::Note),
    ] {
        let entry = entries
            .iter()
            .find(|entry| entry.stable_id().as_str() == native_id(domain, value))
            .unwrap();
        let object = entry.object_payload().unwrap();
        assert_eq!(object.kind(), kind);
        assert_eq!(object.revision(), 1);
        assert!(!object.physical_name().as_str().is_empty());
    }
}

#[test]
fn metadata_limits_and_exact_head_cas_fail_without_publication() {
    let (root, coordinator) = fixture("limits-cas");
    let keys = keys();

    let mut oversized = initial_batch();
    oversized.objects[0].content_type = "x".repeat(256);
    assert!(matches!(
        coordinator.apply_validation_batch(&keys, oversized),
        Err(ValidationBatchError::Invalid(_))
    ));
    assert!(!root.join("fs").join("VALIDATION_HEAD").exists());

    let mut oversized_document = initial_batch();
    let diary = oversized_document
        .objects
        .iter_mut()
        .find(|object| object.kind == ObjectKind::Diary)
        .unwrap();
    diary.content = vec![b'x'; MAX_WRITING_DOCUMENT_BYTES + 1];
    assert!(matches!(
        coordinator.apply_validation_batch(&keys, oversized_document),
        Err(ValidationBatchError::Invalid(_))
    ));
    assert!(!root.join("fs").join("VALIDATION_HEAD").exists());

    let first = coordinator
        .apply_validation_batch(&keys, initial_batch())
        .unwrap();
    let head = first.snapshot().head().clone();
    let before = fs::read(root.join("fs").join("VALIDATION_HEAD")).unwrap();
    let mut wrong_hash = initial_batch();
    wrong_hash.mode = ValidationBatchMode::Expect {
        generation: head.generation(),
        catalog_hash: format!("{}0", head.catalog_hash()),
    };
    assert!(matches!(
        coordinator.apply_validation_batch(&keys, wrong_hash),
        Err(ValidationBatchError::HeadMismatch)
    ));
    assert_eq!(
        fs::read(root.join("fs").join("VALIDATION_HEAD")).unwrap(),
        before
    );
}
