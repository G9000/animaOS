use std::collections::BTreeMap;
use std::fs;
use std::io::Cursor;
use std::path::Path;
use std::process::{Child, Command};
use std::thread;
use std::time::{Duration, Instant};

use anima_corefs::catalog::{
    CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, CatalogObject, ObjectLifecycle,
    WrappedObjectDekRecord,
};
use anima_corefs::crypto::{
    derive_corefs_subkeys, wrap_object_dek, FrkSubkeys, ObjectBaseAad, ObjectKeyAad, ObjectKind,
    SecretBytes,
};
use anima_corefs::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use anima_corefs::folders::{FolderOwner, PortableName};
use anima_corefs::id::OpaqueId;
use anima_corefs::policy::AnimaAccess;
use anima_corefs::transaction::{
    CatalogPrecondition, CommitConflict, CommitError, CoreCommitCoordinator, CoreCommitLock,
    PreparedObjectRevision,
};

const CORE_ID: &str = "core-a";
const ROOT_ID: &str = "01J00000000000000000000000";
const OBJECT_ID: &str = "01J00000000000000000000001";
const FOLDER_ID: &str = "01J00000000000000000000002";
const MISSING_ID: &str = "01J00000000000000000000003";

fn test_root(name: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!(
        "anima-corefs-transaction-{}-{name}",
        std::process::id()
    ))
}

fn reset_root(name: &str) -> std::path::PathBuf {
    let root = test_root(name);
    let _ = fs::remove_dir_all(&root);
    root
}

fn keys() -> FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), 1).unwrap()
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
        "2026-07-15T00:00:00Z",
        "2026-07-15T00:00:00Z",
        "text/markdown",
        BTreeMap::new(),
        BodyEncoding::Utf8,
        body,
    )
    .unwrap();
    let encoded = encode_envelope(&object_key, &aad, &metadata, body).unwrap();
    let keys = keys();
    coordinator
        .prepare_object_revision(&keys, &object_key, &aad, &mut Cursor::new(encoded))
        .unwrap()
}

fn test_object_key(fill: u8) -> SecretBytes {
    SecretBytes::new(vec![fill; 32]).unwrap()
}

fn object(prepared: &PreparedObjectRevision) -> CatalogObject {
    object_with_wrapped(prepared, prepared.wrapped_dek().clone())
}

fn object_with_key(prepared: &PreparedObjectRevision, key_fill: u8) -> CatalogObject {
    object_with_wrapped(prepared, wrapped_record(key_fill, prepared.revision()))
}

fn object_with_revision(prepared: &PreparedObjectRevision, revision: u64) -> CatalogObject {
    CatalogObject::new(
        revision,
        prepared.physical_name().clone(),
        prepared.content_hash().clone(),
        ObjectKind::Note,
        wrapped_record(0x71, revision),
        ObjectLifecycle::Live,
    )
    .unwrap()
}

fn wrapped_record(key_fill: u8, revision: u64) -> WrappedObjectDekRecord {
    let keys = keys();
    let base_aad = ObjectBaseAad::new(
        CORE_ID,
        OBJECT_ID,
        ObjectKind::Note,
        ENVELOPE_VERSION,
        1,
        revision,
    )
    .unwrap();
    let key_aad = ObjectKeyAad::from_base(base_aad, keys.frk_version()).unwrap();
    let wrapped = wrap_object_dek(&test_object_key(key_fill), &keys, &key_aad).unwrap();
    WrappedObjectDekRecord::from_parts(
        keys.frk_version(),
        1,
        wrapped.algorithm(),
        wrapped.envelope_version(),
        wrapped.nonce(),
        wrapped.ciphertext().to_vec(),
    )
    .unwrap()
}

fn object_with_wrapped(
    prepared: &PreparedObjectRevision,
    wrapped_dek: WrappedObjectDekRecord,
) -> CatalogObject {
    CatalogObject::new(
        prepared.revision(),
        prepared.physical_name().clone(),
        prepared.content_hash().clone(),
        ObjectKind::Note,
        wrapped_dek,
        ObjectLifecycle::Live,
    )
    .unwrap()
}

fn catalog(generation: u64, name: &str, prepared: &PreparedObjectRevision) -> CatalogGeneration {
    CatalogGeneration::new(
        generation,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::object(
                common(OBJECT_ID, Some(ROOT_ID), name),
                object(prepared),
            ),
        ],
    )
    .unwrap()
}

fn nested_catalog(
    generation: u64,
    folder_name: &str,
    prepared: &PreparedObjectRevision,
) -> CatalogGeneration {
    CatalogGeneration::new(
        generation,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(FOLDER_ID, Some(ROOT_ID), folder_name)),
            CatalogGenerationEntry::object(
                common(OBJECT_ID, Some(FOLDER_ID), "Note.md"),
                object(prepared),
            ),
        ],
    )
    .unwrap()
}

fn object_precondition(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    revision: u64,
) -> CatalogPrecondition {
    let catalog = active_catalog(coordinator, keys);
    CatalogPrecondition::object(&catalog, &OpaqueId::parse(OBJECT_ID).unwrap(), revision).unwrap()
}

fn vacant_precondition(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    name: &str,
) -> CatalogPrecondition {
    let catalog = active_catalog(coordinator, keys);
    CatalogPrecondition::vacant(
        &catalog,
        &OpaqueId::parse(ROOT_ID).unwrap(),
        PortableName::parse(name).unwrap(),
    )
    .unwrap()
}

fn folder_precondition(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    stable_id: &str,
) -> CatalogPrecondition {
    let catalog = active_catalog(coordinator, keys);
    CatalogPrecondition::folder(&catalog, &OpaqueId::parse(stable_id).unwrap()).unwrap()
}

fn active_catalog(coordinator: &CoreCommitCoordinator, keys: &FrkSubkeys) -> CatalogGeneration {
    if let Some(committed) = coordinator.load_committed(keys).unwrap() {
        return committed.catalog().clone();
    }
    coordinator
        .load_validation_snapshot(keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone()
}

fn commit_initial(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
) -> PreparedObjectRevision {
    let prepared = prepare(coordinator, 1, b"initial body");
    let snapshot = coordinator
        .initialize_validation_snapshot(keys, std::slice::from_ref(&prepared), |next_generation| {
            Ok(catalog(next_generation, "Note.md", &prepared))
        })
        .unwrap();
    assert_eq!(snapshot.head().generation(), 1);
    prepared
}

#[test]
fn prepares_authenticated_immutable_object_revisions_without_taking_the_commit_lock() {
    let root = reset_root("prepare-outside-lock");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let guard = CoreCommitLock::acquire(&root).unwrap();

    let prepared = prepare(&coordinator, 1, b"authenticated object body");

    assert_eq!(prepared.object_id().as_str(), OBJECT_ID);
    assert_eq!(prepared.revision(), 1);
    assert!(!prepared.physical_name().as_str().contains("Note"));
    assert!(!prepared.physical_name().as_str().contains('/'));
    assert!(!prepared.physical_name().as_str().contains('\\'));
    assert!(fs::read_dir(coordinator.objects_path())
        .unwrap()
        .all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".tmp")));

    drop(guard);
    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn preparation_rejects_malformed_or_wrongly_authenticated_envelopes() {
    let root = reset_root("prepare-authentication");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let object_key = SecretBytes::new(vec![0x71; 32]).unwrap();
    let wrong_key = SecretBytes::new(vec![0x72; 32]).unwrap();
    let aad =
        ObjectBaseAad::new(CORE_ID, OBJECT_ID, ObjectKind::Note, ENVELOPE_VERSION, 1, 1).unwrap();
    let metadata = EnvelopeMetadata::for_body(
        ObjectKind::Note.as_str(),
        OBJECT_ID,
        1,
        "2026-07-15T00:00:00Z",
        "2026-07-15T00:00:00Z",
        "text/markdown",
        BTreeMap::new(),
        BodyEncoding::Utf8,
        b"private body",
    )
    .unwrap();
    let encoded = encode_envelope(&object_key, &aad, &metadata, b"private body").unwrap();

    assert!(matches!(
        coordinator.prepare_object_revision(
            &keys,
            &wrong_key,
            &aad,
            &mut Cursor::new(encoded.clone())
        ),
        Err(CommitError::Envelope(_))
    ));
    assert!(matches!(
        coordinator.prepare_object_revision(
            &keys,
            &object_key,
            &aad,
            &mut Cursor::new(b"not-an-envelope")
        ),
        Err(CommitError::Envelope(_))
    ));
    assert_eq!(fs::read_dir(coordinator.objects_path()).unwrap().count(), 0);

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn validation_initialization_publishes_only_the_shadow_pointer() {
    let root = reset_root("publish-catalog-head");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = prepare(&coordinator, 1, b"initial body");

    let snapshot = coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&prepared), |next_generation| {
            Ok(catalog(next_generation, "Note.md", &prepared))
        })
        .unwrap();

    assert_eq!(snapshot.head().generation(), 1);
    assert!(!coordinator.head_path().exists());
    assert!(coordinator.validation_head_path().is_file());
    assert_eq!(
        fs::read_dir(coordinator.catalogs_path()).unwrap().count(),
        1
    );

    assert!(coordinator.load_committed(&keys).unwrap().is_none());
    let loaded = coordinator
        .load_validation_snapshot(&keys)
        .unwrap()
        .unwrap();
    assert_eq!(loaded.head().generation(), 1);
    assert_eq!(loaded.catalog().generation(), 1);
    assert_eq!(loaded.head().catalog_hash(), snapshot.head().catalog_hash());
    assert!(loaded.catalog().cutover_marker().is_none());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn prepared_revision_must_match_the_catalog_wrapped_object_key() {
    let root = reset_root("prepared-wrapped-key-binding");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = prepare(&coordinator, 1, b"key-bound body");

    let error = coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&prepared), |generation| {
            CatalogGeneration::new(
                generation,
                vec![
                    CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
                    CatalogGenerationEntry::object(
                        common(OBJECT_ID, Some(ROOT_ID), "Note.md"),
                        object_with_key(&prepared, 0x72),
                    ),
                ],
            )
        })
        .unwrap_err();

    assert!(matches!(
        error,
        CommitError::PreparedRevisionMismatch { .. }
    ));
    assert!(!coordinator.validation_head_path().exists());
    assert!(!coordinator.head_path().exists());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn changed_objects_require_an_exact_untampered_prepared_revision() {
    let root = reset_root("prepared-binding");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    commit_initial(&coordinator, &keys);
    let precondition = object_precondition(&coordinator, &keys, 1);

    let prepared_two = prepare(&coordinator, 2, b"revision two");
    let error = coordinator
        .commit_first_mutation(
            &keys,
            17,
            &[],
            std::slice::from_ref(&precondition),
            |_, next_generation| Ok(catalog(next_generation, "Note.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(error, CommitError::MissingPreparedRevision { .. }));

    let error = coordinator
        .commit_first_mutation(
            &keys,
            17,
            std::slice::from_ref(&prepared_two),
            std::slice::from_ref(&precondition),
            |_, next_generation| {
                CatalogGeneration::new(
                    next_generation,
                    vec![
                        CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
                        CatalogGenerationEntry::object(
                            common(OBJECT_ID, Some(ROOT_ID), "Note.md"),
                            object_with_revision(&prepared_two, 3),
                        ),
                    ],
                )
            },
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        error,
        CommitError::PreparedRevisionMismatch { .. }
    ));

    fs::write(
        coordinator
            .objects_path()
            .join(prepared_two.physical_name().as_str()),
        b"tampered",
    )
    .unwrap();
    let error = coordinator
        .commit_first_mutation(
            &keys,
            17,
            std::slice::from_ref(&prepared_two),
            std::slice::from_ref(&precondition),
            |_, next_generation| Ok(catalog(next_generation, "Note.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(error, CommitError::PreparedRevisionCorrupt { .. }));

    let prepared_two = prepare(&coordinator, 2, b"revision two retry");
    coordinator
        .commit_first_mutation(
            &keys,
            17,
            std::slice::from_ref(&prepared_two),
            std::slice::from_ref(&precondition),
            |_, next_generation| Ok(catalog(next_generation, "Note.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap();

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn every_changed_source_and_new_destination_requires_a_precondition() {
    let root = reset_root("complete-precondition-coverage");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    commit_initial(&coordinator, &keys);
    let prepared_two = prepare(&coordinator, 2, b"revision two");

    let error = coordinator
        .commit_first_mutation(
            &keys,
            17,
            std::slice::from_ref(&prepared_two),
            &[],
            |_, next_generation| Ok(catalog(next_generation, "Renamed.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        error,
        CommitError::Conflict(CommitConflict::MissingSourcePrecondition { .. })
    ));

    let source = object_precondition(&coordinator, &keys, 1);
    let error = coordinator
        .commit_first_mutation(
            &keys,
            17,
            std::slice::from_ref(&prepared_two),
            &[source],
            |_, next_generation| Ok(catalog(next_generation, "Renamed.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        error,
        CommitError::Conflict(CommitConflict::MissingDestinationPrecondition { .. })
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn stale_path_revision_and_destination_preconditions_fail_before_build() {
    let root = reset_root("stale-precondition");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    commit_initial(&coordinator, &keys);
    let stale = object_precondition(&coordinator, &keys, 1);
    let destination = vacant_precondition(&coordinator, &keys, "Renamed.md");
    let prepared_two = prepare(&coordinator, 2, b"revision two");

    coordinator
        .commit_first_mutation(
            &keys,
            1,
            std::slice::from_ref(&prepared_two),
            &[stale.clone(), destination],
            |_, next_generation| Ok(catalog(next_generation, "Renamed.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap();
    let committed_head = fs::read(coordinator.head_path()).unwrap();
    let catalog_count = fs::read_dir(coordinator.catalogs_path()).unwrap().count();

    let error = coordinator
        .commit(
            &keys,
            &[],
            std::slice::from_ref(&stale),
            |_, _| -> Result<CatalogGeneration, _> {
                panic!("a stale mutation must not build a new catalog")
            },
            |_| Ok(()),
        )
        .unwrap_err();

    assert!(matches!(
        error,
        CommitError::Conflict(CommitConflict::PathOrRevision { .. })
    ));
    assert_eq!(fs::read(coordinator.head_path()).unwrap(), committed_head);
    assert_eq!(
        fs::read_dir(coordinator.catalogs_path()).unwrap().count(),
        catalog_count
    );

    let occupied = vacant_precondition(&coordinator, &keys, "Renamed.md");
    let error = coordinator
        .commit(
            &keys,
            &[],
            &[occupied],
            |_, _| -> Result<CatalogGeneration, _> {
                panic!("an occupied destination must fail before catalog construction")
            },
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        error,
        CommitError::Conflict(CommitConflict::DestinationOccupied { .. })
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn vacant_destination_requires_a_present_folder_parent() {
    let root = reset_root("destination-parent");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    commit_initial(&coordinator, &keys);
    let catalog = active_catalog(&coordinator, &keys);

    let missing = CatalogPrecondition::vacant(
        &catalog,
        &OpaqueId::parse(MISSING_ID).unwrap(),
        PortableName::parse("Child.md").unwrap(),
    )
    .unwrap_err();
    assert!(matches!(
        missing,
        CommitConflict::InvalidDestinationParent { .. }
    ));

    let object = CatalogPrecondition::vacant(
        &catalog,
        &OpaqueId::parse(OBJECT_ID).unwrap(),
        PortableName::parse("Child.md").unwrap(),
    )
    .unwrap_err();
    assert!(matches!(
        object,
        CommitConflict::InvalidDestinationParent { .. }
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn full_ancestor_path_is_revalidated_under_the_commit_lock() {
    let root = reset_root("ancestor-path");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = prepare(&coordinator, 1, b"nested object");
    coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&prepared), |generation| {
            Ok(nested_catalog(generation, "Folder", &prepared))
        })
        .unwrap();
    let stale = object_precondition(&coordinator, &keys, 1);
    let folder = folder_precondition(&coordinator, &keys, FOLDER_ID);
    let destination = vacant_precondition(&coordinator, &keys, "Moved");

    coordinator
        .commit_first_mutation(
            &keys,
            4,
            &[],
            &[folder, destination],
            |_, generation| Ok(nested_catalog(generation, "Moved", &prepared)),
            |_| Ok(()),
        )
        .unwrap();

    let error = coordinator
        .commit(
            &keys,
            &[],
            &[stale],
            |_, _| -> Result<CatalogGeneration, _> {
                panic!("ancestor drift must fail before catalog construction")
            },
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        error,
        CommitError::Conflict(CommitConflict::PathOrRevision { .. })
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cutover_requires_an_explicit_first_mutation_and_is_irreversible() {
    let root = reset_root("cutover-continuity");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared_one = commit_initial(&coordinator, &keys);

    let error = coordinator
        .commit(
            &keys,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared_one)),
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(error, CommitError::CutoverAuthorizationRequired));

    coordinator
        .commit_first_mutation(
            &keys,
            23,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared_one)),
            |_| Ok(()),
        )
        .unwrap();

    let source = object_precondition(&coordinator, &keys, 1);
    let destination = vacant_precondition(&coordinator, &keys, "Renamed.md");
    coordinator
        .commit(
            &keys,
            &[],
            &[source, destination],
            |_, generation| Ok(catalog(generation, "Renamed.md", &prepared_one)),
            |_| Ok(()),
        )
        .unwrap();
    assert_eq!(
        coordinator
            .load_committed(&keys)
            .unwrap()
            .unwrap()
            .catalog()
            .cutover_marker()
            .unwrap()
            .epoch(),
        23
    );

    let error = coordinator
        .commit_first_mutation(
            &keys,
            24,
            &[],
            &[],
            |_, _| -> Result<CatalogGeneration, _> { unreachable!() },
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(error, CommitError::CutoverAlreadyCommitted));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cutover_receipt_prevents_missing_head_from_reactivating_the_shadow() {
    let root = reset_root("cutover-receipt");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);

    coordinator
        .commit_first_mutation(
            &keys,
            31,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();
    assert!(coordinator.cutover_receipt_path().is_file());

    let source = object_precondition(&coordinator, &keys, 1);
    let destination = vacant_precondition(&coordinator, &keys, "Renamed.md");
    coordinator
        .commit(
            &keys,
            &[],
            &[source, destination],
            |_, generation| Ok(catalog(generation, "Renamed.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();
    assert_eq!(
        coordinator
            .load_committed(&keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        3
    );

    fs::copy(coordinator.validation_head_path(), coordinator.head_path()).unwrap();
    let replay_error = coordinator.load_committed(&keys).unwrap_err();
    assert!(matches!(
        replay_error,
        CommitError::AuthoritativeHeadViolatesCutoverReceipt
    ));
    fs::remove_file(coordinator.head_path()).unwrap();

    let load_error = coordinator.load_committed(&keys).unwrap_err();
    assert!(matches!(
        load_error,
        CommitError::AuthoritativeHeadMissingAfterCutover
    ));
    let commit_error = coordinator
        .commit_first_mutation(
            &keys,
            32,
            &[],
            &[],
            |_, _| -> Result<CatalogGeneration, _> {
                panic!("a stale validation snapshot must never be promoted again")
            },
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        commit_error,
        CommitError::AuthoritativeHeadMissingAfterCutover
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn invalidation_runs_after_unlock_and_failure_does_not_rollback_commit() {
    let root = reset_root("invalidation-after-unlock");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    commit_initial(&coordinator, &keys);
    let precondition = object_precondition(&coordinator, &keys, 1);
    let prepared_two = prepare(&coordinator, 2, b"revision two");
    let invalidation_root = root.clone();

    let outcome = coordinator
        .commit_first_mutation(
            &keys,
            7,
            std::slice::from_ref(&prepared_two),
            &[precondition],
            |_, next_generation| Ok(catalog(next_generation, "Note.md", &prepared_two)),
            move |_| {
                let guard = CoreCommitLock::acquire(&invalidation_root)
                    .map_err(|error| error.to_string())?;
                drop(guard);
                Err("runtime index is offline".to_owned())
            },
        )
        .unwrap();

    assert_eq!(outcome.generation(), 2);
    assert!(!outcome.invalidation_delivered());
    assert_eq!(
        coordinator
            .load_committed(&keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        2
    );

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn pinned_layout_fails_closed_when_the_fs_directory_is_replaced() {
    let root = reset_root("pinned-directory");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let original_fs = root.join("fs-original");
    match fs::rename(root.join("fs"), &original_fs) {
        Ok(()) => {
            fs::create_dir(root.join("fs")).unwrap();
            fs::write(root.join("fs").join("sentinel"), b"outside").unwrap();
        }
        Err(error) if cfg!(windows) && matches!(error.raw_os_error(), Some(5) | Some(32)) => {
            drop(coordinator);
            fs::remove_dir_all(root).unwrap();
            return;
        }
        Err(error) => panic!("unexpected directory swap error: {error}"),
    }

    let competing_guard = CoreCommitLock::acquire(&root).unwrap();
    let prepared = prepare(&coordinator, 1, b"must not reach detached fs");
    let error = coordinator
        .initialize_validation_snapshot(&keys(), std::slice::from_ref(&prepared), |generation| {
            Ok(catalog(generation, "Note.md", &prepared))
        })
        .unwrap_err();
    assert!(matches!(error, CommitError::LockBusy));
    drop(competing_guard);

    let error = coordinator
        .initialize_validation_snapshot(&keys(), std::slice::from_ref(&prepared), |generation| {
            Ok(catalog(generation, "Note.md", &prepared))
        })
        .unwrap_err();
    assert!(matches!(error, CommitError::InvalidCoreLayout));

    assert_eq!(
        fs::read(root.join("fs").join("sentinel")).unwrap(),
        b"outside"
    );
    assert!(!original_fs.join("HEAD").exists());
    assert!(!root.join("fs").join("HEAD").exists());
    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn stale_lock_metadata_requires_process_start_identity_not_pid_alone() {
    let root = reset_root("pid-reuse");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let guard = CoreCommitLock::acquire(&root).unwrap();
    let identity = guard.owner_identity().clone();
    let metadata = guard.encoded_owner_metadata();
    drop(guard);

    fs::write(coordinator.lock_path(), &metadata).unwrap();
    let error = CoreCommitLock::acquire(&root).unwrap_err();
    assert!(matches!(
        error,
        CommitError::RecordedOwnerAlive {
            pid,
            process_start_time
        } if pid == identity.pid() && process_start_time == identity.process_start_time()
    ));

    let original = format!("\"processStartTime\":{}", identity.process_start_time());
    let replacement = format!("\"processStartTime\":{}", identity.process_start_time() + 1);
    let reused_pid_metadata = String::from_utf8(metadata)
        .unwrap()
        .replace(&original, &replacement);
    fs::write(coordinator.lock_path(), reused_pid_metadata).unwrap();

    let recovered = CoreCommitLock::acquire(&root).unwrap();
    assert_eq!(recovered.owner_identity(), &identity);
    drop(recovered);

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn kernel_lock_excludes_a_second_process() {
    let root = reset_root("interprocess-lock");
    CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let ready = root.join("helper.ready");
    let release = root.join("helper.release");
    let child = Command::new(std::env::current_exe().unwrap())
        .arg("--ignored")
        .arg("--exact")
        .arg("helper_process_holds_commit_lock")
        .arg("--nocapture")
        .env("ANIMA_COREFS_LOCK_HELPER_ROOT", &root)
        .env("ANIMA_COREFS_LOCK_HELPER_READY", &ready)
        .env("ANIMA_COREFS_LOCK_HELPER_RELEASE", &release)
        .spawn()
        .unwrap();
    let mut child = ChildGuard(child);

    let deadline = Instant::now() + Duration::from_secs(10);
    while !ready.exists() && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(10));
    }
    assert!(ready.exists(), "helper process did not acquire the lock");

    let replacement = fs::remove_file(root.join("fs").join("commit.lock"));
    if cfg!(windows) {
        assert!(
            replacement.is_err(),
            "Windows lock entry remained delete-shareable"
        );
    } else {
        replacement.unwrap();
        fs::write(root.join("fs").join("commit.lock"), b"").unwrap();
    }

    let error = CoreCommitLock::acquire(&root).unwrap_err();
    assert!(matches!(error, CommitError::LockBusy));

    fs::write(&release, b"release").unwrap();
    let status = child.0.wait().unwrap();
    assert!(status.success());
    std::mem::forget(child);
    fs::remove_dir_all(root).unwrap();
}

#[test]
#[ignore]
fn helper_process_holds_commit_lock() {
    let Some(root) = std::env::var_os("ANIMA_COREFS_LOCK_HELPER_ROOT") else {
        return;
    };
    let ready = std::env::var_os("ANIMA_COREFS_LOCK_HELPER_READY").unwrap();
    let release = std::env::var_os("ANIMA_COREFS_LOCK_HELPER_RELEASE").unwrap();
    let guard = CoreCommitLock::acquire(Path::new(&root)).unwrap();
    fs::write(ready, b"ready").unwrap();
    while !Path::new(&release).exists() {
        thread::sleep(Duration::from_millis(10));
    }
    drop(guard);
}
