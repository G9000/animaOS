use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Cursor;
use std::path::PathBuf;

use crate::catalog::{
    CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, CatalogObject, ObjectLifecycle,
};
use crate::crypto::{derive_corefs_subkeys, FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes};
use crate::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use crate::folders::{FolderOwner, FolderRole, PortableName};
use crate::id::OpaqueId;
use crate::policy::{AnimaAccess, LocalAnimaAccess, LocalFolderPolicy};
use crate::transaction::{CoreCommitCoordinator, PreparedObjectRevision, ValidationSnapshot};

use super::executor::CoreFsShadowMutator;
use super::{
    ContentFormatValidator, ContentValidationError, ConverterMutationAuthority, ConverterPrincipal,
    CoreFsMutationFacade, LogicalMutation, MutationError, MutationStamp, MutationTarget,
    PatchAddFormat, PublicMutationError, ValidatedContent, CORE_FS_MIGRATION_WRITE_FROZEN,
};

const CORE_ID: &str = "mutation-core";
const ROOT_ID: &str = "01J10000000000000000000000";
const NOTES_ID: &str = "01J10000000000000000000001";
const TRASH_ID: &str = "01J10000000000000000000002";
const EXISTING_ID: &str = "01J10000000000000000000003";
const VAULT_ID: &str = "01J10000000000000000000004";

struct IdentityValidator;

impl ContentFormatValidator for IdentityValidator {
    fn validate(
        &self,
        _kind: ObjectKind,
        content_type: &str,
        bytes: &[u8],
    ) -> Result<ValidatedContent, ContentValidationError> {
        ValidatedContent::new(bytes.to_vec(), content_type, BodyEncoding::Utf8)
    }
}

struct RejectingValidator;

impl ContentFormatValidator for RejectingValidator {
    fn validate(
        &self,
        _kind: ObjectKind,
        _content_type: &str,
        _bytes: &[u8],
    ) -> Result<ValidatedContent, ContentValidationError> {
        Err(ContentValidationError::Rejected("test_rejected"))
    }
}

struct Fixture {
    root: PathBuf,
    coordinator: CoreCommitCoordinator,
    keys: FrkSubkeys,
    selected: ValidationSnapshot,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn public_mutation_facade_is_frozen_before_touching_storage() {
    let root = temporary_root("public-frozen");
    let facade = CoreFsMutationFacade;
    let operations = all_operations();

    for operation in operations {
        assert_eq!(
            facade.execute(operation),
            Err(PublicMutationError::MigrationWriteFrozen)
        );
    }

    assert_eq!(
        PublicMutationError::MigrationWriteFrozen.code(),
        CORE_FS_MIGRATION_WRITE_FROZEN
    );
    assert!(!root.exists());
}

#[test]
fn mkdir_create_write_and_move_each_advance_exactly_one_validation_generation() {
    let fixture = fixture("basic", AnimaAccess::Manage);
    let authority = ConverterMutationAuthority::new();
    let mutator = CoreFsShadowMutator::new(&authority, &fixture.coordinator, &fixture.keys);
    let validator = IdentityValidator;

    let mkdir = mutator
        .execute(
            ConverterPrincipal::User,
            &fixture.selected,
            LogicalMutation::Mkdir {
                path: "Notes/Projects".to_string(),
                reserved_role: Some("core.projects".to_string()),
            },
            stamp(),
            &validator,
        )
        .unwrap();
    assert!(mkdir.atomic);
    assert_eq!(mkdir.generation, 2);
    assert_eq!(mkdir.changes.len(), 1);
    assert_eq!(mkdir.changes[0].revision, None);
    assert!(mkdir.changes[0].content_hash.is_none());
    assert_authority_unchanged(&fixture);

    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    let create = mutator
        .execute(
            ConverterPrincipal::Anima,
            &selected,
            LogicalMutation::Create {
                path: "Notes/Projects/one.md".to_string(),
                kind: ObjectKind::Note,
                content_type: "text/markdown".to_string(),
                bytes: b"one\n".to_vec(),
            },
            stamp(),
            &validator,
        )
        .unwrap();
    assert_eq!(create.generation, 3);
    assert_eq!(create.changes[0].revision, Some(1));
    assert!(create.changes[0].content_hash.is_some());
    let created_id = create.changes[0].stable_id.clone();

    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    let write = mutator
        .execute(
            ConverterPrincipal::Anima,
            &selected,
            LogicalMutation::Write {
                target: MutationTarget::StableId(created_id.clone()),
                expected_revision: 1,
                content_type: "text/markdown".to_string(),
                bytes: b"two\n".to_vec(),
            },
            stamp(),
            &validator,
        )
        .unwrap();
    assert_eq!(write.generation, 4);
    assert_eq!(write.changes[0].stable_id, created_id);
    assert_eq!(write.changes[0].revision, Some(2));

    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    let move_result = mutator
        .execute(
            ConverterPrincipal::Anima,
            &selected,
            LogicalMutation::Move {
                source: MutationTarget::StableId(created_id.clone()),
                destination: "Notes/moved.md".to_string(),
                expected_revision: Some(2),
            },
            stamp(),
            &validator,
        )
        .unwrap();
    assert_eq!(move_result.generation, 5);
    assert_eq!(move_result.changes[0].stable_id, created_id);
    assert_eq!(move_result.changes[0].revision, Some(2));
    assert_authority_unchanged(&fixture);
}

#[test]
fn object_and_folder_trash_restore_preserve_identity_and_reject_stale_or_colliding_state() {
    let fixture = fixture("trash-restore", AnimaAccess::Manage);
    let authority = ConverterMutationAuthority::new();
    let mutator = CoreFsShadowMutator::new(&authority, &fixture.coordinator, &fixture.keys);
    let validator = IdentityValidator;

    let trashed = mutator
        .execute(
            ConverterPrincipal::User,
            &fixture.selected,
            LogicalMutation::Trash {
                target: MutationTarget::Path("Notes/existing.md".to_string()),
                trash_folder: MutationTarget::StableId(TRASH_ID.to_string()),
                expected_revision: Some(1),
            },
            stamp(),
            &validator,
        )
        .unwrap();
    assert_eq!(trashed.changes[0].stable_id, EXISTING_ID);

    let stale = mutator.execute(
        ConverterPrincipal::User,
        &fixture.selected,
        LogicalMutation::Restore {
            target: MutationTarget::StableId(EXISTING_ID.to_string()),
            destination: None,
            expected_revision: Some(1),
        },
        stamp(),
        &validator,
    );
    assert!(matches!(stale, Err(MutationError::OptimisticConflict)));

    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    let restored = mutator
        .execute(
            ConverterPrincipal::User,
            &selected,
            LogicalMutation::Restore {
                target: MutationTarget::StableId(EXISTING_ID.to_string()),
                destination: Some("Notes/restored.md".to_string()),
                expected_revision: Some(1),
            },
            stamp(),
            &validator,
        )
        .unwrap();
    assert_eq!(restored.changes[0].stable_id, EXISTING_ID);

    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    let mkdir = mutator
        .execute(
            ConverterPrincipal::User,
            &selected,
            LogicalMutation::Mkdir {
                path: "Notes/Folder".to_string(),
                reserved_role: None,
            },
            stamp(),
            &validator,
        )
        .unwrap();
    let folder_id = mkdir.changes[0].stable_id.clone();
    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    mutator
        .execute(
            ConverterPrincipal::User,
            &selected,
            LogicalMutation::Trash {
                target: MutationTarget::StableId(folder_id.clone()),
                trash_folder: MutationTarget::StableId(TRASH_ID.to_string()),
                expected_revision: None,
            },
            stamp(),
            &validator,
        )
        .unwrap();
    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    let restored = mutator
        .execute(
            ConverterPrincipal::User,
            &selected,
            LogicalMutation::Restore {
                target: MutationTarget::StableId(folder_id.clone()),
                destination: None,
                expected_revision: None,
            },
            stamp(),
            &validator,
        )
        .unwrap();
    assert_eq!(restored.changes[0].stable_id, folder_id);
    assert_authority_unchanged(&fixture);
}

#[test]
fn shared_patch_plan_commits_all_files_once_and_deletes_to_recoverable_trash() {
    let fixture = fixture("patch", AnimaAccess::Manage);
    let authority = ConverterMutationAuthority::new();
    let mutator = CoreFsShadowMutator::new(&authority, &fixture.coordinator, &fixture.keys);
    let mut expected_revisions = BTreeMap::new();
    expected_revisions.insert("Notes/existing.md".to_string(), 1);
    let mut add_formats = BTreeMap::new();
    add_formats.insert(
        "Notes/new.md".to_string(),
        PatchAddFormat {
            kind: ObjectKind::Note,
            content_type: "text/markdown".to_string(),
        },
    );
    let result = mutator
            .execute(
                ConverterPrincipal::User,
                &fixture.selected,
                LogicalMutation::ApplyPatch {
                    patch: "*** Begin Patch\n*** Update File: Notes/existing.md\n@@\n-old\n+updated\n*** Add File: Notes/new.md\n+new\n*** Delete File: Notes/existing.md\n*** End Patch".to_string(),
                    expected_revisions,
                    add_formats,
                    trash_folder: MutationTarget::StableId(TRASH_ID.to_string()),
                },
                stamp(),
                &IdentityValidator,
            )
            .unwrap();

    assert!(result.atomic);
    assert_eq!(result.generation, 2);
    assert_eq!(result.changes.len(), 2);
    assert_eq!(
        result
            .changes
            .iter()
            .map(|change| change.stable_id.as_str())
            .collect::<BTreeSet<_>>()
            .len(),
        2
    );
    assert_authority_unchanged(&fixture);
}

#[test]
fn preflight_rejects_policy_role_format_collision_stale_and_unsafe_inputs_without_advancing() {
    let fixture = fixture("preflight", AnimaAccess::Write);
    let authority = ConverterMutationAuthority::new();
    let mutator = CoreFsShadowMutator::new(&authority, &fixture.coordinator, &fixture.keys);
    let cases = [
        LogicalMutation::Move {
            source: MutationTarget::Path("Notes/existing.md".to_string()),
            destination: "moved.md".to_string(),
            expected_revision: Some(1),
        },
        LogicalMutation::Mkdir {
            path: "../escape".to_string(),
            reserved_role: None,
        },
        LogicalMutation::Mkdir {
            path: "Notes/existing.md".to_string(),
            reserved_role: None,
        },
        LogicalMutation::Write {
            target: MutationTarget::Path("Notes/existing.md".to_string()),
            expected_revision: 99,
            content_type: "text/markdown".to_string(),
            bytes: b"x".to_vec(),
        },
    ];
    for operation in cases {
        assert!(mutator
            .execute(
                ConverterPrincipal::Anima,
                &fixture.selected,
                operation,
                stamp(),
                &IdentityValidator,
            )
            .is_err());
    }
    assert!(mutator
        .execute(
            ConverterPrincipal::User,
            &fixture.selected,
            LogicalMutation::Mkdir {
                path: "Notes/Role".to_string(),
                reserved_role: Some("core.notes".to_string()),
            },
            stamp(),
            &IdentityValidator,
        )
        .is_err());
    assert!(mutator
        .execute(
            ConverterPrincipal::User,
            &fixture.selected,
            LogicalMutation::Write {
                target: MutationTarget::Path("Notes/existing.md".to_string()),
                expected_revision: 1,
                content_type: "text/markdown".to_string(),
                bytes: b"x".to_vec(),
            },
            stamp(),
            &RejectingValidator,
        )
        .is_err());
    assert_eq!(
        fixture
            .coordinator
            .load_validation_snapshot(&fixture.keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        1
    );
    assert_authority_unchanged(&fixture);
}

#[test]
fn cross_policy_moves_restores_and_patch_moves_fail_before_advancing_or_preparing() {
    let fixture = policy_boundary_fixture("policy-boundary");
    let authority = ConverterMutationAuthority::new();
    let mutator = CoreFsShadowMutator::new(&authority, &fixture.coordinator, &fixture.keys);

    let moved = mutator.execute(
        ConverterPrincipal::User,
        &fixture.selected,
        LogicalMutation::Move {
            source: MutationTarget::Path("Notes/existing.md".to_string()),
            destination: "Vault/existing.md".to_string(),
            expected_revision: Some(1),
        },
        stamp(),
        &IdentityValidator,
    );
    assert!(matches!(moved, Err(MutationError::PolicyBoundaryMismatch)));

    let object_count_before_patch = object_file_count(&fixture);
    let mut expected_revisions = BTreeMap::new();
    expected_revisions.insert("Notes/existing.md".to_string(), 1);
    let patched = mutator.execute(
        ConverterPrincipal::User,
        &fixture.selected,
        LogicalMutation::ApplyPatch {
            patch: "*** Begin Patch\n*** Update File: Notes/existing.md\n*** Move to: Vault/existing.md\n@@\n-old\n+new\n*** End Patch".to_string(),
            expected_revisions,
            add_formats: BTreeMap::new(),
            trash_folder: MutationTarget::StableId(TRASH_ID.to_string()),
        },
        stamp(),
        &IdentityValidator,
    );
    assert!(matches!(
        patched,
        Err(MutationError::PolicyBoundaryMismatch)
    ));
    assert_eq!(object_file_count(&fixture), object_count_before_patch);
    assert_eq!(
        fixture
            .coordinator
            .load_validation_snapshot(&fixture.keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        1
    );

    let trashed = mutator
        .execute(
            ConverterPrincipal::User,
            &fixture.selected,
            LogicalMutation::Trash {
                target: MutationTarget::Path("Notes/existing.md".to_string()),
                trash_folder: MutationTarget::StableId(TRASH_ID.to_string()),
                expected_revision: Some(1),
            },
            stamp(),
            &IdentityValidator,
        )
        .unwrap();
    assert_eq!(trashed.generation, 2);
    let selected = fixture
        .coordinator
        .load_validation_snapshot(&fixture.keys)
        .unwrap()
        .unwrap();
    let restored = mutator.execute(
        ConverterPrincipal::User,
        &selected,
        LogicalMutation::Restore {
            target: MutationTarget::StableId(EXISTING_ID.to_string()),
            destination: Some("Vault/restored.md".to_string()),
            expected_revision: Some(1),
        },
        stamp(),
        &IdentityValidator,
    );
    assert!(matches!(
        restored,
        Err(MutationError::PolicyBoundaryMismatch)
    ));
    assert_eq!(
        fixture
            .coordinator
            .load_validation_snapshot(&fixture.keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        2
    );
    assert_authority_unchanged(&fixture);
}

fn all_operations() -> Vec<LogicalMutation> {
    vec![
        LogicalMutation::Mkdir {
            path: "x".into(),
            reserved_role: None,
        },
        LogicalMutation::Create {
            path: "x".into(),
            kind: ObjectKind::Note,
            content_type: "text/plain".into(),
            bytes: vec![],
        },
        LogicalMutation::Write {
            target: MutationTarget::Path("x".into()),
            expected_revision: 1,
            content_type: "text/plain".into(),
            bytes: vec![],
        },
        LogicalMutation::ApplyPatch {
            patch: String::new(),
            expected_revisions: BTreeMap::new(),
            add_formats: BTreeMap::new(),
            trash_folder: MutationTarget::Path("Trash".into()),
        },
        LogicalMutation::Move {
            source: MutationTarget::Path("x".into()),
            destination: "y".into(),
            expected_revision: Some(1),
        },
        LogicalMutation::Trash {
            target: MutationTarget::Path("x".into()),
            trash_folder: MutationTarget::Path("Trash".into()),
            expected_revision: Some(1),
        },
        LogicalMutation::Restore {
            target: MutationTarget::StableId(EXISTING_ID.into()),
            destination: None,
            expected_revision: Some(1),
        },
    ]
}

fn fixture(name: &str, access: AnimaAccess) -> Fixture {
    let root = temporary_root(name);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x71; 32]).unwrap(), 1).unwrap();
    let prepared = prepare(&coordinator, &keys, EXISTING_ID, b"old\n");
    let selected = coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&prepared), |generation| {
            CatalogGeneration::new(
                generation,
                vec![
                    CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core", access)),
                    CatalogGenerationEntry::folder(
                        common(NOTES_ID, Some(ROOT_ID), "Notes", access)
                            .with_role_for_internal_mutation(
                                FolderRole::parse_existing("core.notes").unwrap(),
                            ),
                    ),
                    CatalogGenerationEntry::folder(common(
                        TRASH_ID,
                        Some(ROOT_ID),
                        "Trash",
                        access,
                    )),
                    CatalogGenerationEntry::object(
                        common(EXISTING_ID, Some(NOTES_ID), "existing.md", access),
                        object(&prepared),
                    ),
                ],
            )
        })
        .unwrap();
    Fixture {
        root,
        coordinator,
        keys,
        selected,
    }
}

fn policy_boundary_fixture(name: &str) -> Fixture {
    let root = temporary_root(name);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x71; 32]).unwrap(), 1).unwrap();
    let prepared = prepare(&coordinator, &keys, EXISTING_ID, b"old\n");
    let selected = coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&prepared), |generation| {
            let vault = common(VAULT_ID, Some(ROOT_ID), "Vault", AnimaAccess::Manage)
                .with_policy_override_for_internal_mutation(LocalFolderPolicy::new(
                    None,
                    LocalAnimaAccess::Allow(AnimaAccess::Manage),
                ));
            CatalogGeneration::new(
                generation,
                vec![
                    CatalogGenerationEntry::folder(common(
                        ROOT_ID,
                        None,
                        "Core",
                        AnimaAccess::Write,
                    )),
                    CatalogGenerationEntry::folder(common(
                        NOTES_ID,
                        Some(ROOT_ID),
                        "Notes",
                        AnimaAccess::Write,
                    )),
                    CatalogGenerationEntry::folder(common(
                        TRASH_ID,
                        Some(ROOT_ID),
                        "Trash",
                        AnimaAccess::Write,
                    )),
                    CatalogGenerationEntry::folder(vault),
                    CatalogGenerationEntry::object(
                        common(
                            EXISTING_ID,
                            Some(NOTES_ID),
                            "existing.md",
                            AnimaAccess::Write,
                        ),
                        object(&prepared),
                    ),
                ],
            )
        })
        .unwrap();
    Fixture {
        root,
        coordinator,
        keys,
        selected,
    }
}

fn prepare(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    object_id: &str,
    body: &[u8],
) -> PreparedObjectRevision {
    let object_key = SecretBytes::new(vec![0x72; 32]).unwrap();
    let aad =
        ObjectBaseAad::new(CORE_ID, object_id, ObjectKind::Note, ENVELOPE_VERSION, 1, 1).unwrap();
    let metadata = EnvelopeMetadata::for_body(
        ObjectKind::Note.as_str(),
        object_id,
        1,
        "2026-07-17T00:00:00Z",
        "2026-07-17T00:00:00Z",
        "text/markdown",
        BTreeMap::new(),
        BodyEncoding::Utf8,
        body,
    )
    .unwrap();
    let encoded = encode_envelope(&object_key, &aad, &metadata, body).unwrap();
    coordinator
        .prepare_object_revision(keys, &object_key, &aad, &mut Cursor::new(encoded))
        .unwrap()
}

fn object(prepared: &PreparedObjectRevision) -> CatalogObject {
    CatalogObject::new(
        prepared.revision(),
        prepared.physical_name().clone(),
        prepared.content_hash().clone(),
        ObjectKind::Note,
        prepared.wrapped_dek().clone(),
        ObjectLifecycle::Live,
    )
    .unwrap()
}

fn common(id: &str, parent: Option<&str>, name: &str, access: AnimaAccess) -> CatalogEntryCommon {
    CatalogEntryCommon::new(
        OpaqueId::parse(id).unwrap(),
        parent.map(|id| OpaqueId::parse(id).unwrap()),
        PortableName::parse(name).unwrap(),
        FolderOwner::User,
        access,
    )
    .with_policy_override_for_internal_mutation(LocalFolderPolicy::new(
        if parent.is_none() {
            Some(FolderOwner::User)
        } else {
            None
        },
        if parent.is_none() {
            LocalAnimaAccess::Allow(access)
        } else {
            LocalAnimaAccess::Inherit
        },
    ))
}

fn stamp() -> MutationStamp {
    MutationStamp::new(1_700_000_000_000, "2026-07-17T00:00:00Z").unwrap()
}

fn assert_authority_unchanged(fixture: &Fixture) {
    assert!(!fixture.coordinator.head_path().exists());
    assert!(!fixture.coordinator.cutover_receipt_path().exists());
    assert!(!fixture.coordinator.cutover_complete_path().exists());
}

fn object_file_count(fixture: &Fixture) -> usize {
    fs::read_dir(fixture.coordinator.objects_path())
        .unwrap()
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_type()
                .map(|kind| kind.is_file())
                .unwrap_or(false)
        })
        .count()
}

fn temporary_root(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "anima-corefs-mutation-{}-{name}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&path);
    path
}
