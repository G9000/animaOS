use std::collections::BTreeMap;
use std::fs;
use std::io::Cursor;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::{Child, Command};
use std::thread;
use std::time::{Duration, Instant};

use anima_corefs::catalog::{
    CatalogEntryCommon, CatalogError, CatalogGeneration, CatalogGenerationEntry, CatalogObject,
    ObjectLifecycle, WrappedObjectDekRecord,
};
use anima_corefs::crypto::{
    derive_corefs_subkeys, wrap_object_dek, CryptoError, FrkSubkeys, ObjectBaseAad, ObjectKeyAad,
    ObjectKind, SecretBytes,
};
use anima_corefs::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use anima_corefs::folders::{FolderOwner, PortableName};
use anima_corefs::head::HeadError;
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

fn seed_cached_object(
    name: &str,
) -> (
    std::path::PathBuf,
    CoreCommitCoordinator,
    FrkSubkeys,
    PreparedObjectRevision,
) {
    let root = reset_root(name);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();
    coordinator.load_committed(&keys).unwrap().unwrap();
    (root, coordinator, keys, prepared)
}

fn unchanged_cached_commit(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    prepared: &PreparedObjectRevision,
) -> Result<anima_corefs::transaction::CommitOutcome, CommitError> {
    coordinator.commit(
        keys,
        &[],
        &[],
        |_, generation| Ok(catalog(generation, "Note.md", prepared)),
        |_| Ok(()),
    )
}

#[cfg(unix)]
fn create_file_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(windows)]
fn create_file_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    if std::os::windows::fs::symlink_file(target, link).is_ok() {
        return Ok(());
    }

    fn wsl_path(path: &Path) -> std::io::Result<String> {
        let output = Command::new("wsl.exe")
            .arg("wslpath")
            .arg("-a")
            .arg(path.to_string_lossy().replace('\\', "/"))
            .output()?;
        if !output.status.success() {
            return Err(std::io::Error::other("wslpath failed"));
        }
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
    }

    let target = wsl_path(target)?;
    let link = wsl_path(link)?;
    let output = Command::new("wsl.exe")
        .arg("-e")
        .arg("ln")
        .arg("-s")
        .arg(target)
        .arg(link)
        .output()?;
    if output.status.success() {
        Ok(())
    } else {
        Err(std::io::Error::other("wsl ln failed"))
    }
}

#[test]
fn cache_hit_rejects_missing_object() {
    let (root, coordinator, keys, prepared) = seed_cached_object("cache-hit-missing-object");
    fs::remove_file(
        coordinator
            .objects_path()
            .join(prepared.physical_name().as_str()),
    )
    .unwrap();

    assert!(unchanged_cached_commit(&coordinator, &keys, &prepared).is_err());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cache_hit_rejects_empty_object() {
    let (root, coordinator, keys, prepared) = seed_cached_object("cache-hit-empty-object");
    fs::write(
        coordinator
            .objects_path()
            .join(prepared.physical_name().as_str()),
        [],
    )
    .unwrap();

    assert!(matches!(
        unchanged_cached_commit(&coordinator, &keys, &prepared),
        Err(CommitError::ReferencedObjectMissing { .. })
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cache_hit_rejects_symlinked_object() {
    let (root, coordinator, keys, prepared) = seed_cached_object("cache-hit-symlink-object");
    let object_path = coordinator
        .objects_path()
        .join(prepared.physical_name().as_str());
    let detached = coordinator.objects_path().join("detached-object.acore");
    fs::rename(&object_path, &detached).unwrap();
    create_file_symlink(&detached, &object_path).unwrap();

    assert!(unchanged_cached_commit(&coordinator, &keys, &prepared).is_err());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cache_hit_rejects_replaced_object() {
    let (root, coordinator, keys, prepared) = seed_cached_object("cache-hit-replaced-object");
    let object_path = coordinator
        .objects_path()
        .join(prepared.physical_name().as_str());
    let detached = coordinator.objects_path().join("detached-object.acore");
    fs::rename(&object_path, detached).unwrap();
    fs::create_dir(&object_path).unwrap();

    assert!(unchanged_cached_commit(&coordinator, &keys, &prepared).is_err());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cache_hit_rejects_unexpected_hard_link() {
    let (root, coordinator, keys, prepared) = seed_cached_object("cache-hit-hard-link-object");
    let object_path = coordinator
        .objects_path()
        .join(prepared.physical_name().as_str());
    fs::hard_link(
        object_path,
        coordinator.objects_path().join("unexpected-object-link"),
    )
    .unwrap();

    assert!(unchanged_cached_commit(&coordinator, &keys, &prepared).is_err());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[cfg(windows)]
#[test]
fn cache_hit_rejects_unexpected_hard_link_named_like_unix_crash_stage_on_windows() {
    let (root, coordinator, keys, prepared) =
        seed_cached_object("cache-hit-unix-crash-alias-on-windows");
    let object_path = coordinator
        .objects_path()
        .join(prepared.physical_name().as_str());
    fs::hard_link(
        object_path,
        coordinator.objects_path().join(".object.17.tmp"),
    )
    .unwrap();

    assert!(unchanged_cached_commit(&coordinator, &keys, &prepared).is_err());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn another_coordinator_advancing_head_forces_unlocked_load_miss() {
    let root = reset_root("cross-coordinator-unlocked-cache-miss");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let other = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
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
        2
    );

    other
        .commit(
            &keys,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();

    let committed = coordinator.load_committed(&keys).unwrap().unwrap();
    assert_eq!(committed.head().generation(), 3);
    assert_eq!(committed.catalog().generation(), 3);

    drop(other);
    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn another_coordinator_advance_is_observed_by_commit() {
    let root = reset_root("cross-coordinator-commit-cache-miss");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let other = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();
    coordinator.load_committed(&keys).unwrap().unwrap();

    other
        .commit(
            &keys,
            &[],
            &[],
            |current, generation| {
                assert_eq!(current.unwrap().generation(), 2);
                Ok(catalog(generation, "Note.md", &prepared))
            },
            |_| Ok(()),
        )
        .unwrap();

    let outcome = coordinator
        .commit(
            &keys,
            &[],
            &[],
            |current, generation| {
                assert_eq!(current.unwrap().generation(), 3);
                Ok(catalog(generation, "Note.md", &prepared))
            },
            |_| Ok(()),
        )
        .unwrap();

    assert_eq!(outcome.generation(), 4);
    assert_eq!(
        coordinator
            .load_committed(&keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        4
    );

    drop(other);
    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn commit_rejects_wrong_same_version_active_material_before_cache() {
    let root = reset_root("commit-wrong-same-version-active-material");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();
    coordinator.load_committed(&keys).unwrap().unwrap();
    let wrong_same_version =
        derive_corefs_subkeys(&SecretBytes::new(vec![0x43; 32]).unwrap(), 1).unwrap();

    let error = coordinator
        .commit(
            &wrong_same_version,
            &[],
            &[],
            |_, _| panic!("wrong active material must fail before the build closure"),
            |_| Ok(()),
        )
        .unwrap_err();

    assert!(matches!(
        error,
        CommitError::Head(HeadError::Catalog(CatalogError::Crypto(
            CryptoError::Authentication
        )))
    ));

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
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
fn first_mutation_bytes_include_both_cutover_marker_records() {
    let root = reset_root("first-mutation-byte-accounting");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);

    let outcome = coordinator
        .commit_first_mutation(
            &keys,
            17,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();

    let catalog_bytes = fs::read_dir(coordinator.catalogs_path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .find(|path| {
            path.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("catalog-00000000000000000002-")
        })
        .map(|path| fs::metadata(path).unwrap().len())
        .unwrap();
    let head_bytes = fs::metadata(coordinator.head_path()).unwrap().len();
    let receipt_bytes = fs::metadata(root.join("fs").join("CUTOVER_RECEIPT"))
        .unwrap()
        .len();
    let complete_bytes = fs::metadata(root.join("fs").join("CUTOVER_COMPLETE"))
        .unwrap()
        .len();

    assert_eq!(
        outcome.bytes_written(),
        catalog_bytes + head_bytes + receipt_bytes + complete_bytes
    );
    assert_eq!(receipt_bytes, head_bytes);
    assert_eq!(complete_bytes, head_bytes);

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
        CommitError::Conflict(CommitConflict::MissingSourcePrecondition { ref stable_id })
            if stable_id == OBJECT_ID
    ));
    assert_eq!(
        error.to_string(),
        format!(
            "CoreFS commit conflict: changed catalog source is missing a precondition: {OBJECT_ID}"
        )
    );

    let source = object_precondition(&coordinator, &keys, 1);
    let error = coordinator
        .commit_first_mutation(
            &keys,
            17,
            std::slice::from_ref(&prepared_two),
            std::slice::from_ref(&source),
            |_, next_generation| Ok(catalog(next_generation, "Renamed.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        error,
        CommitError::Conflict(CommitConflict::MissingDestinationPrecondition {
            ref parent_id,
            ref name,
        }) if parent_id == ROOT_ID && name == "Renamed.md"
    ));
    assert_eq!(
        error.to_string(),
        format!(
            "CoreFS commit conflict: new catalog destination is missing a vacancy precondition: parent={ROOT_ID}, name=Renamed.md"
        )
    );

    let destination = vacant_precondition(&coordinator, &keys, "Renamed.md");
    coordinator
        .commit_first_mutation(
            &keys,
            17,
            std::slice::from_ref(&prepared_two),
            &[source.clone(), source, destination.clone(), destination],
            |_, next_generation| Ok(catalog(next_generation, "Renamed.md", &prepared_two)),
            |_| Ok(()),
        )
        .unwrap();

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn a_new_subtree_needs_one_vacancy_at_its_existing_parent() {
    let root = reset_root("new-subtree-precondition");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    coordinator
        .initialize_validation_snapshot(&keys, &[], |generation| {
            CatalogGeneration::new(
                generation,
                vec![CatalogGenerationEntry::folder(common(
                    ROOT_ID, None, "Core",
                ))],
            )
        })
        .unwrap();
    let destination = vacant_precondition(&coordinator, &keys, "NewFolder");

    coordinator
        .commit_first_mutation(
            &keys,
            17,
            &[],
            &[destination],
            |_, next_generation| {
                CatalogGeneration::new(
                    next_generation,
                    vec![
                        CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
                        CatalogGenerationEntry::folder(common(
                            FOLDER_ID,
                            Some(ROOT_ID),
                            "NewFolder",
                        )),
                        CatalogGenerationEntry::folder(common(
                            MISSING_ID,
                            Some(FOLDER_ID),
                            "Nested",
                        )),
                    ],
                )
            },
            |_| Ok(()),
        )
        .unwrap();

    let committed = coordinator.load_committed(&keys).unwrap().unwrap();
    assert_eq!(committed.catalog().entries().len(), 3);

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
            &[stale.clone(), destination.clone()],
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
        CommitError::Conflict(CommitConflict::PathOrRevision { ref stable_id })
            if stable_id == OBJECT_ID
    ));
    assert_eq!(
        error.to_string(),
        format!("CoreFS commit conflict: catalog path or revision changed for {OBJECT_ID}")
    );
    assert_eq!(fs::read(coordinator.head_path()).unwrap(), committed_head);
    assert_eq!(
        fs::read_dir(coordinator.catalogs_path()).unwrap().count(),
        catalog_count
    );

    let error = coordinator
        .commit(
            &keys,
            &[],
            &[destination],
            |_, _| -> Result<CatalogGeneration, _> {
                panic!("an occupied destination must fail before catalog construction")
            },
            |_| Ok(()),
        )
        .unwrap_err();
    assert!(matches!(
        error,
        CommitError::Conflict(CommitConflict::DestinationOccupied {
            ref parent_id,
            ref name,
        })
            if parent_id == ROOT_ID && name == "Renamed.md"
    ));
    assert_eq!(
        error.to_string(),
        format!(
            "CoreFS commit conflict: catalog destination is occupied: parent={ROOT_ID}, name=Renamed.md"
        )
    );

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
fn vacancy_capture_rejects_an_already_occupied_destination() {
    let root = reset_root("occupied-destination-capture");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    commit_initial(&coordinator, &keys);
    let catalog = active_catalog(&coordinator, &keys);

    let occupied = CatalogPrecondition::vacant(
        &catalog,
        &OpaqueId::parse(ROOT_ID).unwrap(),
        PortableName::parse("Note.md").unwrap(),
    )
    .unwrap_err();

    assert!(matches!(
        occupied,
        CommitConflict::DestinationOccupied { parent_id, name }
            if parent_id == ROOT_ID && name == "Note.md"
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
fn missing_legacy_completion_marker_recovers_a_higher_authenticated_head() {
    let root = reset_root("legacy-cutover-completion-marker");
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
    coordinator
        .commit(
            &keys,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();
    fs::remove_file(coordinator.cutover_complete_path()).unwrap();

    let committed = coordinator.load_committed(&keys).unwrap().unwrap();
    assert_eq!(committed.head().generation(), 3);
    assert_eq!(committed.catalog().cutover_marker().unwrap().epoch(), 31);
    assert!(coordinator.cutover_complete_path().is_file());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn load_committed_reports_an_in_flight_cutover_receipt_then_recovers_it() {
    let root = reset_root("cutover-receipt-in-flight");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    commit_initial(&coordinator, &keys);

    let alternate_root = reset_root("cutover-receipt-in-flight-alternate");
    let alternate = CoreCommitCoordinator::new(&alternate_root, CORE_ID).unwrap();
    let alternate_prepared = commit_initial(&alternate, &keys);
    alternate
        .commit_first_mutation(
            &keys,
            51,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &alternate_prepared)),
            |_| Ok(()),
        )
        .unwrap();

    let guard = CoreCommitLock::acquire(&root).unwrap();
    for entry in fs::read_dir(alternate.catalogs_path()).unwrap() {
        let source = entry.unwrap().path();
        fs::copy(
            &source,
            coordinator
                .catalogs_path()
                .join(source.file_name().unwrap()),
        )
        .unwrap();
    }
    fs::copy(
        alternate.cutover_receipt_path(),
        coordinator.cutover_receipt_path(),
    )
    .unwrap();

    let reader_root = root.clone();
    let reader_keys = crate::keys();
    let (ready_sender, ready_receiver) = std::sync::mpsc::channel();
    let (result_sender, result_receiver) = std::sync::mpsc::channel();
    let reader = thread::spawn(move || {
        let coordinator = CoreCommitCoordinator::new(&reader_root, CORE_ID).unwrap();
        ready_sender.send(()).unwrap();
        result_sender
            .send(coordinator.load_committed(&reader_keys))
            .unwrap();
    });
    ready_receiver.recv_timeout(Duration::from_secs(5)).unwrap();
    let in_flight = match result_receiver.recv_timeout(Duration::from_millis(500)) {
        Ok(result) => result,
        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
            drop(guard);
            reader.join().unwrap();
            panic!("receipt-only reads must not block on a held commit lock");
        }
        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
            panic!("receipt-only reader disconnected")
        }
    };
    assert!(matches!(in_flight, Err(CommitError::LockBusy)));
    reader.join().unwrap();

    drop(guard);
    let committed = coordinator.load_committed(&keys).unwrap().unwrap();
    assert_eq!(committed.head().generation(), 2);
    assert_eq!(committed.catalog().cutover_marker().unwrap().epoch(), 51);
    assert!(coordinator.cutover_complete_path().is_file());

    drop(alternate);
    drop(coordinator);
    fs::remove_dir_all(alternate_root).unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cutover_receipt_rejects_a_divergent_head_at_its_generation() {
    let root = reset_root("cutover-receipt-divergent-head");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);
    coordinator
        .commit_first_mutation(
            &keys,
            41,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();

    let alternate_root = reset_root("cutover-receipt-divergent-head-alternate");
    let alternate = CoreCommitCoordinator::new(&alternate_root, CORE_ID).unwrap();
    let alternate_prepared = commit_initial(&alternate, &keys);
    alternate
        .commit_first_mutation(
            &keys,
            41,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &alternate_prepared)),
            |_| Ok(()),
        )
        .unwrap();

    let alternate_catalog = fs::read_dir(alternate.catalogs_path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .find(|path| {
            path.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("catalog-00000000000000000002-")
        })
        .unwrap();
    fs::copy(
        &alternate_catalog,
        coordinator
            .catalogs_path()
            .join(alternate_catalog.file_name().unwrap()),
    )
    .unwrap();
    fs::copy(alternate.head_path(), coordinator.head_path()).unwrap();

    let error = coordinator.load_committed(&keys).unwrap_err();
    assert!(matches!(
        error,
        CommitError::AuthoritativeHeadViolatesCutoverReceipt
    ));

    drop(alternate);
    drop(coordinator);
    fs::remove_dir_all(alternate_root).unwrap();
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

#[cfg(unix)]
#[test]
fn committed_catalog_load_tolerates_a_crash_stale_immutable_stage_link() {
    let root = reset_root("crash-stale-immutable-stage");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = commit_initial(&coordinator, &keys);
    coordinator
        .commit_first_mutation(
            &keys,
            61,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();

    let catalog_path = fs::read_dir(coordinator.catalogs_path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .find(|path| {
            path.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("catalog-00000000000000000002-")
        })
        .unwrap();
    let catalog_name = catalog_path.file_name().unwrap().to_string_lossy();
    let stale_stage = coordinator
        .catalogs_path()
        .join(format!(".{catalog_name}.17.tmp"));
    fs::hard_link(&catalog_path, &stale_stage).unwrap();

    let committed = coordinator.load_committed(&keys).unwrap().unwrap();
    assert_eq!(committed.head().generation(), 2);
    assert!(stale_stage.is_file());
    let outcome = coordinator
        .commit(
            &keys,
            &[],
            &[],
            |_, generation| Ok(catalog(generation, "Note.md", &prepared)),
            |_| Ok(()),
        )
        .unwrap();
    assert_eq!(outcome.generation(), 3);

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[cfg(unix)]
#[test]
fn prepared_object_validation_tolerates_a_crash_stale_immutable_stage_link() {
    let root = reset_root("crash-stale-object-stage");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let prepared = prepare(&coordinator, 1, b"crash-stale object");
    let object_path = coordinator
        .objects_path()
        .join(prepared.physical_name().as_str());
    let stale_stage = coordinator.objects_path().join(".object.17.tmp");
    fs::hard_link(&object_path, &stale_stage).unwrap();

    let snapshot = coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&prepared), |generation| {
            Ok(catalog(generation, "Note.md", &prepared))
        })
        .unwrap();
    assert_eq!(snapshot.head().generation(), 1);
    assert!(stale_stage.is_file());

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn pinned_layout_fails_closed_when_the_fs_directory_is_replaced() {
    let root = reset_root("pinned-directory");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let prepared = prepare(&coordinator, 1, b"must not reach detached fs");
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
fn pinned_layout_fails_closed_when_the_core_root_is_replaced() {
    let root = reset_root("pinned-root");
    let detached_root = reset_root("pinned-root-detached");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let prepared = prepare(&coordinator, 1, b"must not reach detached root");
    match fs::rename(&root, &detached_root) {
        Ok(()) => {}
        Err(error) if cfg!(windows) && matches!(error.raw_os_error(), Some(5) | Some(32)) => {
            drop(coordinator);
            fs::remove_dir_all(root).unwrap();
            return;
        }
        Err(error) => panic!("unexpected Core root swap error: {error}"),
    }
    let replacement = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let competing_guard = CoreCommitLock::acquire(&root).unwrap();

    let error = coordinator
        .initialize_validation_snapshot(&keys(), std::slice::from_ref(&prepared), |generation| {
            Ok(catalog(generation, "Note.md", &prepared))
        })
        .unwrap_err();

    assert!(matches!(error, CommitError::InvalidCoreLayout));
    assert!(!detached_root.join("fs").join("VALIDATION_HEAD").exists());
    assert!(!root.join("fs").join("VALIDATION_HEAD").exists());
    drop(competing_guard);
    drop(replacement);
    drop(coordinator);
    fs::remove_dir_all(detached_root).unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn pinned_layout_is_revalidated_after_catalog_construction() {
    let root = reset_root("pinned-root-during-build");
    let detached_root = reset_root("pinned-root-during-build-detached");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let prepared = prepare(&coordinator, 1, b"must not publish through detached root");
    let mut replacement = None;
    let mut swapped = false;

    let outcome = coordinator.initialize_validation_snapshot(
        &keys(),
        std::slice::from_ref(&prepared),
        |generation| {
            match fs::rename(&root, &detached_root) {
                Ok(()) => {
                    swapped = true;
                    replacement = Some(CoreCommitCoordinator::new(&root, CORE_ID).unwrap());
                }
                Err(error)
                    if cfg!(windows) && matches!(error.raw_os_error(), Some(5) | Some(32)) =>
                {
                    return Ok(catalog(generation, "Note.md", &prepared));
                }
                Err(error) => panic!("unexpected Core root swap error: {error}"),
            }
            Ok(catalog(generation, "Note.md", &prepared))
        },
    );

    if !swapped {
        assert!(outcome.is_ok());
        drop(replacement);
        drop(coordinator);
        fs::remove_dir_all(root).unwrap();
        return;
    }
    let error = outcome.unwrap_err();
    assert!(matches!(error, CommitError::InvalidCoreLayout));
    assert!(!detached_root.join("fs").join("VALIDATION_HEAD").exists());
    assert!(!root.join("fs").join("VALIDATION_HEAD").exists());
    drop(replacement);
    drop(coordinator);
    fs::remove_dir_all(detached_root).unwrap();
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

#[test]
fn partial_lock_metadata_is_recovered_after_the_kernel_lock_is_acquired() {
    let root = reset_root("partial-lock-metadata");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    fs::write(coordinator.lock_path(), br#"{"schemaVersion":1,"pid":"#).unwrap();

    let recovered = CoreCommitLock::acquire(&root).unwrap();
    assert_eq!(recovered.owner_identity().pid(), std::process::id());
    drop(recovered);

    drop(coordinator);
    fs::remove_dir_all(root).unwrap();
}

#[cfg(unix)]
#[test]
fn commit_lock_remains_private_with_a_permissive_umask() {
    let root = reset_root("private-commit-lock");
    let status = Command::new(std::env::current_exe().unwrap())
        .arg("--ignored")
        .arg("--exact")
        .arg("helper_process_checks_private_commit_lock")
        .arg("--nocapture")
        .env("ANIMA_COREFS_PRIVATE_LOCK_ROOT", &root)
        .status()
        .unwrap();

    assert!(status.success());
    fs::remove_dir_all(root).unwrap();
}

#[cfg(unix)]
#[test]
#[ignore]
fn helper_process_checks_private_commit_lock() {
    let Some(root) = std::env::var_os("ANIMA_COREFS_PRIVATE_LOCK_ROOT") else {
        return;
    };
    unsafe {
        libc::umask(0o022);
    }
    let root = std::path::PathBuf::from(root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let lock_path = coordinator.lock_path();

    drop(CoreCommitLock::acquire(&root).unwrap());
    assert_eq!(
        fs::metadata(lock_path).unwrap().permissions().mode() & 0o777,
        0o600
    );

    fs::set_permissions(lock_path, fs::Permissions::from_mode(0o644)).unwrap();
    drop(CoreCommitLock::acquire(&root).unwrap());
    assert_eq!(
        fs::metadata(lock_path).unwrap().permissions().mode() & 0o777,
        0o600
    );
}

#[test]
fn a_missing_relative_core_root_is_created_from_the_current_directory() {
    let root = reset_root("relative-core-root");
    fs::create_dir_all(&root).unwrap();
    let status = Command::new(std::env::current_exe().unwrap())
        .arg("--ignored")
        .arg("--exact")
        .arg("helper_process_creates_relative_core_root")
        .arg("--nocapture")
        .current_dir(&root)
        .env("ANIMA_COREFS_RELATIVE_ROOT_HELPER", "1")
        .status()
        .unwrap();

    assert!(status.success());
    assert!(root.join(".anima").join("core").join("fs").is_dir());
    assert!(root.join(".anima").join("core").join("objects").is_dir());
    fs::remove_dir_all(root).unwrap();
}

#[test]
#[ignore]
fn helper_process_creates_relative_core_root() {
    if std::env::var_os("ANIMA_COREFS_RELATIVE_ROOT_HELPER").is_none() {
        return;
    }
    CoreCommitCoordinator::new(Path::new(".anima").join("core"), CORE_ID).unwrap();
}

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[cfg(target_os = "linux")]
#[test]
fn zombie_lock_owner_is_stale_before_its_parent_reaps_it() {
    let root = reset_root("zombie-lock-owner");
    CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let child = Command::new(std::env::current_exe().unwrap())
        .arg("--ignored")
        .arg("--exact")
        .arg("helper_process_leaves_stale_lock_metadata")
        .arg("--nocapture")
        .env("ANIMA_COREFS_ZOMBIE_LOCK_ROOT", &root)
        .spawn()
        .unwrap();
    let mut child = ChildGuard(child);
    let stat_path = format!("/proc/{}/stat", child.0.id());
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut became_zombie = false;
    while Instant::now() < deadline {
        if let Ok(stat) = fs::read_to_string(&stat_path) {
            if stat
                .rfind(')')
                .and_then(|command_end| stat[command_end + 1..].split_whitespace().next())
                == Some("Z")
            {
                became_zombie = true;
                break;
            }
        }
        thread::sleep(Duration::from_millis(10));
    }
    assert!(became_zombie, "helper process did not become a zombie");

    drop(CoreCommitLock::acquire(&root).unwrap());

    let status = child.0.wait().unwrap();
    assert!(status.success());
    std::mem::forget(child);
    fs::remove_dir_all(root).unwrap();
}

#[cfg(target_os = "linux")]
#[test]
#[ignore]
fn helper_process_leaves_stale_lock_metadata() {
    let Some(root) = std::env::var_os("ANIMA_COREFS_ZOMBIE_LOCK_ROOT") else {
        return;
    };
    drop(CoreCommitLock::acquire(Path::new(&root)).unwrap());
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
