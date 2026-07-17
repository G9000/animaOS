use std::collections::BTreeMap;
use std::fs;
use std::io::Cursor;

use anima_corefs::catalog::{
    decrypt_catalog_generation, CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry,
    CatalogObject, ObjectLifecycle, TrashMetadata,
};
use anima_corefs::crypto::{
    derive_corefs_subkeys, unwrap_object_dek, FrkSubkeys, ObjectBaseAad, ObjectKeyAad, ObjectKind,
    SecretBytes,
};
use anima_corefs::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use anima_corefs::folders::{FolderOwner, PortableName};
use anima_corefs::id::OpaqueId;
use anima_corefs::policy::AnimaAccess;
use anima_corefs::rotation::{
    authorize_frk_retirement, FrkKeyring, FrkRetirementError, RotationError,
};
use anima_corefs::transaction::{
    CatalogPrecondition, CommitError, CoreCommitCoordinator, PreparedObjectRevision,
};

const CORE_ID: &str = "core-rotation";
const ROOT_ID: &str = "01J00000000000000000000000";
const OBJECT_ID: &str = "01J00000000000000000000001";
const TRASH_ID: &str = "01J00000000000000000000002";

fn reset_root(name: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-rotation-{}-{name}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&root);
    root
}

fn keys(fill: u8, version: u32) -> FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![fill; 32]).unwrap(), version).unwrap()
}

fn common(id: &str, parent_id: Option<&str>, name: &str) -> CatalogEntryCommon {
    CatalogEntryCommon::new(
        OpaqueId::parse(id).unwrap(),
        parent_id.map(|value| OpaqueId::parse(value).unwrap()),
        PortableName::parse(name).unwrap(),
        FolderOwner::User,
        AnimaAccess::Write,
    )
}

fn prepare(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    revision: u64,
    body: &[u8],
) -> PreparedObjectRevision {
    let object_key = SecretBytes::new(vec![0x71; 32]).unwrap();
    let aad = ObjectBaseAad::new(
        CORE_ID,
        OBJECT_ID,
        ObjectKind::Note,
        ENVELOPE_VERSION,
        1,
        revision,
    )
    .unwrap();
    let metadata = EnvelopeMetadata::for_body(
        ObjectKind::Note.as_str(),
        OBJECT_ID,
        revision,
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

fn catalog(generation: u64, prepared: &PreparedObjectRevision) -> CatalogGeneration {
    catalog_with_lifecycle(generation, prepared, ObjectLifecycle::Live)
}

fn catalog_with_lifecycle(
    generation: u64,
    prepared: &PreparedObjectRevision,
    lifecycle: ObjectLifecycle,
) -> CatalogGeneration {
    catalog_with_parent_and_lifecycle(generation, prepared, ROOT_ID, lifecycle)
}

fn catalog_with_parent_and_lifecycle(
    generation: u64,
    prepared: &PreparedObjectRevision,
    parent_id: &str,
    lifecycle: ObjectLifecycle,
) -> CatalogGeneration {
    CatalogGeneration::new(
        generation,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::object(
                common(OBJECT_ID, Some(parent_id), "Note.md"),
                CatalogObject::new(
                    prepared.revision(),
                    prepared.physical_name().clone(),
                    prepared.content_hash().clone(),
                    ObjectKind::Note,
                    prepared.wrapped_dek().clone(),
                    lifecycle,
                )
                .unwrap(),
            ),
            CatalogGenerationEntry::folder(common(TRASH_ID, Some(ROOT_ID), "Trash")),
        ],
    )
    .unwrap()
}

#[test]
fn tombstone_wrappers_are_rewrapped_before_the_old_frk_can_retire() {
    let root = reset_root("tombstone-rewrap");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys(0x42, 1);
    let active_keys = keys(0x43, 2);
    let prepared = seed_committed(&coordinator, &old_keys);
    let current = coordinator
        .load_committed(&old_keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let precondition =
        CatalogPrecondition::object(&current, &OpaqueId::parse(OBJECT_ID).unwrap(), 2).unwrap();
    let tombstone = ObjectLifecycle::tombstone(OpaqueId::parse(ROOT_ID).unwrap(), 1).unwrap();
    coordinator
        .commit(
            &old_keys,
            &[],
            &[precondition],
            |_, generation| Ok(catalog_with_lifecycle(generation, &prepared, tombstone)),
            |_| Ok(()),
        )
        .unwrap();

    let both = FrkKeyring::new([&old_keys, &active_keys]).unwrap();
    coordinator
        .rotate_frk(&both, &active_keys, 3, |_| Ok(()))
        .unwrap();
    let committed = coordinator
        .load_committed_with_keyring(&both)
        .unwrap()
        .unwrap();
    assert_eq!(
        committed.catalog().entries()[1]
            .object_payload()
            .unwrap()
            .wrapped_dek()
            .frk_version(),
        2
    );

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn targeted_object_rotation_generates_a_fresh_dek_and_commits_the_new_revision() {
    let root = reset_root("targeted-object");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let active_keys = keys(0x42, 1);
    seed_committed(&coordinator, &active_keys);
    let current = coordinator
        .load_committed(&active_keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let object_id = OpaqueId::parse(OBJECT_ID).unwrap();
    let old_object_key = SecretBytes::new(vec![0x71; 32]).unwrap();

    let rotated = coordinator
        .prepare_object_key_rotation(
            &active_keys,
            &current,
            &object_id,
            &old_object_key,
            "2026-07-17T02:00:00Z",
        )
        .unwrap();
    assert_eq!(rotated.revision(), 3);
    assert_eq!(rotated.object_key_epoch(), 2);
    assert_eq!(
        coordinator
            .load_committed(&active_keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        2
    );

    let precondition = CatalogPrecondition::object(&current, &object_id, 2).unwrap();
    coordinator
        .commit(
            &active_keys,
            std::slice::from_ref(&rotated),
            &[precondition],
            |_, generation| Ok(catalog(generation, &rotated)),
            |_| Ok(()),
        )
        .unwrap();
    let committed = coordinator.load_committed(&active_keys).unwrap().unwrap();
    let object = committed.catalog().entries()[1].object_payload().unwrap();
    let key_aad = ObjectKeyAad::new(
        CORE_ID,
        OBJECT_ID,
        3,
        ObjectKind::Note,
        ENVELOPE_VERSION,
        2,
        1,
    )
    .unwrap();
    let new_object_key = unwrap_object_dek(
        &active_keys,
        &object.wrapped_dek().to_wrapped_object_dek().unwrap(),
        &key_aad,
    )
    .unwrap();
    assert_ne!(new_object_key.as_slice(), old_object_key.as_slice());
    let mut encoded = fs::File::open(
        coordinator
            .objects_path()
            .join(object.physical_name().as_str()),
    )
    .unwrap();
    let object_aad =
        ObjectBaseAad::new(CORE_ID, OBJECT_ID, ObjectKind::Note, ENVELOPE_VERSION, 2, 3).unwrap();
    let mut body = Vec::new();
    let reopened = anima_corefs::envelope::read_envelope(
        &mut encoded,
        &new_object_key,
        &object_aad,
        &mut body,
    )
    .unwrap();
    assert_eq!(reopened.metadata.revision, 3);
    assert_eq!(body, b"committed");

    drop(committed);
    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn targeted_object_rotation_rejects_authenticated_content_outside_the_catalog_hash() {
    let root = reset_root("targeted-object-content-mismatch");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let active_keys = keys(0x42, 1);
    seed_committed(&coordinator, &active_keys);
    let current = coordinator
        .load_committed(&active_keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let object = current.entries()[1].object_payload().unwrap();
    let object_key = SecretBytes::new(vec![0x71; 32]).unwrap();
    let aad =
        ObjectBaseAad::new(CORE_ID, OBJECT_ID, ObjectKind::Note, ENVELOPE_VERSION, 1, 2).unwrap();
    let forged_body = b"authenticated but not cataloged";
    let forged_metadata = EnvelopeMetadata::for_body(
        ObjectKind::Note.as_str(),
        OBJECT_ID,
        2,
        "2026-07-17T00:00:00Z",
        "2026-07-17T00:00:00Z",
        "text/markdown",
        BTreeMap::new(),
        BodyEncoding::Utf8,
        forged_body,
    )
    .unwrap();
    fs::write(
        coordinator
            .objects_path()
            .join(object.physical_name().as_str()),
        encode_envelope(&object_key, &aad, &forged_metadata, forged_body).unwrap(),
    )
    .unwrap();

    assert!(matches!(
        coordinator.prepare_object_key_rotation(
            &active_keys,
            &current,
            &OpaqueId::parse(OBJECT_ID).unwrap(),
            &object_key,
            "2026-07-17T02:00:00Z",
        ),
        Err(CommitError::ObjectKeyRotationSourceMismatch { .. })
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn targeted_object_rotation_preserves_recoverable_trash_state() {
    let root = reset_root("targeted-trashed-object");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let active_keys = keys(0x42, 1);
    let prepared = seed_committed(&coordinator, &active_keys);
    let live = coordinator
        .load_committed(&active_keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let object_id = OpaqueId::parse(OBJECT_ID).unwrap();
    let trash = ObjectLifecycle::Trashed(
        TrashMetadata::new(
            OpaqueId::parse(TRASH_ID).unwrap(),
            OpaqueId::parse(ROOT_ID).unwrap(),
            PortableName::parse("Note.md").unwrap(),
            1,
        )
        .unwrap(),
    );
    let precondition = CatalogPrecondition::object(&live, &object_id, 2).unwrap();
    let trash_destination = CatalogPrecondition::vacant(
        &live,
        &OpaqueId::parse(TRASH_ID).unwrap(),
        PortableName::parse("Note.md").unwrap(),
    )
    .unwrap();
    coordinator
        .commit(
            &active_keys,
            &[],
            &[precondition, trash_destination],
            |_, generation| {
                Ok(catalog_with_parent_and_lifecycle(
                    generation,
                    &prepared,
                    TRASH_ID,
                    trash.clone(),
                ))
            },
            |_| Ok(()),
        )
        .unwrap();
    let trashed = coordinator
        .load_committed(&active_keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let old_object_key = SecretBytes::new(vec![0x71; 32]).unwrap();
    let rotated = coordinator
        .prepare_object_key_rotation(
            &active_keys,
            &trashed,
            &object_id,
            &old_object_key,
            "2026-07-17T02:00:00Z",
        )
        .unwrap();
    let precondition = CatalogPrecondition::object(&trashed, &object_id, 2).unwrap();
    coordinator
        .commit(
            &active_keys,
            std::slice::from_ref(&rotated),
            &[precondition],
            |_, generation| {
                Ok(catalog_with_parent_and_lifecycle(
                    generation,
                    &rotated,
                    TRASH_ID,
                    trash.clone(),
                ))
            },
            |_| Ok(()),
        )
        .unwrap();

    let committed = coordinator.load_committed(&active_keys).unwrap().unwrap();
    assert!(matches!(
        committed.catalog().entries()[1]
            .object_payload()
            .unwrap()
            .lifecycle(),
        ObjectLifecycle::Trashed(_)
    ));

    drop(committed);
    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

fn seed_committed(
    coordinator: &CoreCommitCoordinator,
    old_keys: &FrkSubkeys,
) -> PreparedObjectRevision {
    let initial = prepare(coordinator, old_keys, 1, b"initial");
    coordinator
        .initialize_validation_snapshot(old_keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
    let current = coordinator
        .load_validation_snapshot(old_keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let precondition =
        CatalogPrecondition::object(&current, &OpaqueId::parse(OBJECT_ID).unwrap(), 1).unwrap();
    let committed = prepare(coordinator, old_keys, 2, b"committed");
    coordinator
        .commit_first_mutation(
            old_keys,
            17,
            std::slice::from_ref(&committed),
            &[precondition],
            |_, generation| Ok(catalog(generation, &committed)),
            |_| Ok(()),
        )
        .unwrap();
    committed
}

#[test]
fn frk_rotation_rewraps_live_deks_without_rewriting_object_ciphertext() {
    let root = reset_root("rewrap");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys(0x42, 1);
    let pending_keys = keys(0x43, 2);
    let original = seed_committed(&coordinator, &old_keys);
    let old_head = coordinator
        .load_committed(&old_keys)
        .unwrap()
        .unwrap()
        .head()
        .clone();
    let old_catalog_bytes = fs::read_dir(coordinator.catalogs_path())
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| fs::read(entry.path()).unwrap())
        .find(|encoded| {
            decrypt_catalog_generation(&old_keys, CORE_ID, encoded)
                .is_ok_and(|catalog| catalog.generation() == old_head.generation())
        })
        .unwrap();

    let keyring = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();
    let outcome = coordinator
        .rotate_frk(&keyring, &pending_keys, old_head.generation(), |_| Ok(()))
        .unwrap();

    assert_eq!(outcome.generation(), old_head.generation() + 1);
    let committed = coordinator
        .load_committed_with_keyring(&keyring)
        .unwrap()
        .unwrap();
    assert_eq!(committed.head().required_frk_version(), 2);
    let object = committed.catalog().entries()[1].object_payload().unwrap();
    assert_eq!(object.revision(), original.revision());
    assert_eq!(object.physical_name(), original.physical_name());
    assert_eq!(object.content_hash(), original.content_hash());
    assert_eq!(object.object_key_epoch(), 1);
    assert_eq!(object.wrapped_dek().frk_version(), 2);

    let base = ObjectBaseAad::new(
        CORE_ID,
        OBJECT_ID,
        ObjectKind::Note,
        ENVELOPE_VERSION,
        object.object_key_epoch(),
        object.revision(),
    )
    .unwrap();
    let aad = ObjectKeyAad::from_base(base.clone(), 2).unwrap();
    unwrap_object_dek(
        &pending_keys,
        &object.wrapped_dek().to_wrapped_object_dek().unwrap(),
        &aad,
    )
    .unwrap();
    let old_aad = ObjectKeyAad::from_base(base, 1).unwrap();
    assert!(unwrap_object_dek(
        &old_keys,
        &object.wrapped_dek().to_wrapped_object_dek().unwrap(),
        &old_aad,
    )
    .is_err());
    assert_eq!(
        decrypt_catalog_generation(&old_keys, CORE_ID, &old_catalog_bytes)
            .unwrap()
            .generation(),
        old_head.generation()
    );

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn rotation_rejects_stale_generation_and_non_newer_keys_before_publication() {
    let root = reset_root("preconditions");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys(0x42, 1);
    let pending_keys = keys(0x43, 2);
    seed_committed(&coordinator, &old_keys);
    let keyring = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();

    assert!(matches!(
        coordinator.rotate_frk(&keyring, &pending_keys, 1, |_| Ok(())),
        Err(CommitError::Rotation(RotationError::GenerationMismatch {
            expected: 1,
            actual: 2
        }))
    ));
    assert!(matches!(
        coordinator.rotate_frk(&keyring, &old_keys, 2, |_| Ok(())),
        Err(CommitError::Rotation(
            RotationError::PendingVersionNotNewer {
                active: 1,
                pending: 1
            }
        ))
    ));
    let skipped_version = keys(0x44, 3);
    let skipped_keyring = FrkKeyring::new([&old_keys, &skipped_version]).unwrap();
    assert!(matches!(
        coordinator.rotate_frk(&skipped_keyring, &skipped_version, 2, |_| Ok(())),
        Err(CommitError::Rotation(
            RotationError::PendingVersionNotSuccessor {
                active: 1,
                pending: 3
            }
        ))
    ));
    let reused_material = keys(0x42, 2);
    let reused_keyring = FrkKeyring::new([&old_keys, &reused_material]).unwrap();
    assert!(matches!(
        coordinator.rotate_frk(&reused_keyring, &reused_material, 2, |_| Ok(())),
        Err(CommitError::Rotation(
            RotationError::PendingKeyMaterialReused
        ))
    ));
    assert_eq!(coordinator.required_frk_version().unwrap(), Some(1));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn keyrings_reject_empty_duplicate_and_rollback_only_inputs() {
    let old_keys = keys(0x42, 1);
    assert!(matches!(
        FrkKeyring::new(std::iter::empty::<&FrkSubkeys>()),
        Err(RotationError::EmptyKeyring)
    ));
    assert!(matches!(
        FrkKeyring::new([&old_keys, &old_keys]),
        Err(RotationError::DuplicateVersion(1))
    ));

    let root = reset_root("rollback");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let pending_keys = keys(0x43, 2);
    seed_committed(&coordinator, &old_keys);
    let both = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();
    coordinator
        .rotate_frk(&both, &pending_keys, 2, |_| Ok(()))
        .unwrap();
    fs::copy(coordinator.cutover_receipt_path(), coordinator.head_path()).unwrap();
    let active_only = FrkKeyring::new([&pending_keys]).unwrap();
    assert!(matches!(
        coordinator.load_committed_with_keyring(&active_only),
        Err(CommitError::Rotation(RotationError::MissingVersion(1)))
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn pending_and_active_keyrings_reopen_the_rotated_head() {
    let root = reset_root("pending-reopen");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys(0x42, 1);
    let pending_keys = keys(0x43, 2);
    seed_committed(&coordinator, &old_keys);
    let both = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();
    coordinator
        .rotate_frk(&both, &pending_keys, 2, |_| Ok(()))
        .unwrap();

    assert_eq!(coordinator.required_frk_version().unwrap(), Some(2));
    assert_eq!(
        coordinator
            .load_committed_with_keyring(&both)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        3
    );
    let active_only = FrkKeyring::new([&pending_keys]).unwrap();
    assert_eq!(
        coordinator
            .load_committed_with_keyring(&active_only)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        3
    );

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn ordinary_commits_continue_after_rotation_and_old_key_retirement() {
    let root = reset_root("post-rotation-commit");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys(0x42, 1);
    let active_keys = keys(0x43, 2);
    seed_committed(&coordinator, &old_keys);
    let both = FrkKeyring::new([&old_keys, &active_keys]).unwrap();
    coordinator
        .rotate_frk(&both, &active_keys, 2, |_| Ok(()))
        .unwrap();

    let mismatched_active = keys(0x55, 2);
    assert!(matches!(
        coordinator.commit_with_keyring(
            &both,
            &mismatched_active,
            &[],
            &[],
            |current, generation| CatalogGeneration::new(
                generation,
                current.unwrap().entries().to_vec(),
            ),
            |_| Ok(()),
        ),
        Err(CommitError::Rotation(
            RotationError::KeyringMaterialMismatch(2)
        ))
    ));
    assert!(matches!(
        coordinator.commit_with_keyring(
            &both,
            &old_keys,
            &[],
            &[],
            |current, generation| CatalogGeneration::new(
                generation,
                current.unwrap().entries().to_vec(),
            ),
            |_| Ok(()),
        ),
        Err(CommitError::Rotation(
            RotationError::ActiveVersionMismatch {
                expected: 2,
                actual: 1
            }
        ))
    ));

    coordinator
        .commit_with_keyring(
            &both,
            &active_keys,
            &[],
            &[],
            |current, generation| {
                CatalogGeneration::new(generation, current.unwrap().entries().to_vec())
            },
            |_| Ok(()),
        )
        .unwrap();
    let active_only = FrkKeyring::new([&active_keys]).unwrap();
    coordinator
        .commit_with_keyring(
            &active_only,
            &active_keys,
            &[],
            &[],
            |current, generation| {
                CatalogGeneration::new(generation, current.unwrap().entries().to_vec())
            },
            |_| Ok(()),
        )
        .unwrap();

    let committed = coordinator
        .load_committed_with_keyring(&active_only)
        .unwrap()
        .unwrap();
    assert_eq!(committed.head().generation(), 5);
    assert_eq!(committed.head().required_frk_version(), 2);

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn later_rotation_does_not_require_an_already_retired_cutover_key() {
    let root = reset_root("second-rotation");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let version_one = keys(0x42, 1);
    let version_two = keys(0x43, 2);
    let version_three = keys(0x44, 3);
    seed_committed(&coordinator, &version_one);
    let first_keyring = FrkKeyring::new([&version_one, &version_two]).unwrap();
    coordinator
        .rotate_frk(&first_keyring, &version_two, 2, |_| Ok(()))
        .unwrap();

    let post_retirement = FrkKeyring::new([&version_two]).unwrap();
    coordinator
        .rotate_frk(&post_retirement, &version_three, 3, |_| Ok(()))
        .unwrap();

    let active_only = FrkKeyring::new([&version_three]).unwrap();
    let committed = coordinator
        .load_committed_with_keyring(&active_only)
        .unwrap()
        .unwrap();
    assert_eq!(committed.head().generation(), 4);
    assert_eq!(committed.head().required_frk_version(), 3);
    assert_eq!(
        committed.catalog().entries()[1]
            .object_payload()
            .unwrap()
            .wrapped_dek()
            .frk_version(),
        3
    );

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn retirement_requires_pruned_catalogs_and_a_verified_active_backup() {
    let old_keys = keys(0x42, 1);
    let catalog = CatalogGeneration::new(
        1,
        vec![CatalogGenerationEntry::folder(common(
            ROOT_ID, None, "Core",
        ))],
    )
    .unwrap();
    let encrypted =
        anima_corefs::catalog::encrypt_catalog_generation(&old_keys, CORE_ID, &catalog).unwrap();
    let retained_old =
        anima_corefs::head::HeadRecord::new_for_catalog(&old_keys, CORE_ID, &encrypted, 1).unwrap();

    assert_eq!(
        authorize_frk_retirement(1, 2, &[retained_old], &[], &[2]),
        Err(FrkRetirementError::RetainedCatalogRequiresVersion(1))
    );
    assert_eq!(
        authorize_frk_retirement(1, 2, &[], &[], &[]),
        Err(FrkRetirementError::VerifiedActiveBackupRequired(2))
    );
    assert!(authorize_frk_retirement(1, 2, &[], &[], &[2]).is_ok());
    assert_eq!(
        authorize_frk_retirement(2, 2, &[], &[], &[2]),
        Err(FrkRetirementError::ActiveVersionCannotRetire(2))
    );

    let root = reset_root("retained-wrapper");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    seed_committed(&coordinator, &old_keys);
    let retained_catalog = coordinator
        .load_committed(&old_keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    assert_eq!(
        authorize_frk_retirement(1, 2, &[], &[retained_catalog], &[2]),
        Err(FrkRetirementError::RetainedCatalogRequiresVersion(1))
    );
    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}
