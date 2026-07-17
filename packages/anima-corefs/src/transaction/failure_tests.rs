use std::collections::BTreeMap;
use std::io::Cursor;
use std::path::Path;
use std::process::Command;

use crate::catalog::{
    CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, CatalogObject, ObjectLifecycle,
};
use crate::crypto::{derive_corefs_subkeys, FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes};
use crate::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use crate::folders::{FolderOwner, PortableName};
use crate::id::OpaqueId;
use crate::policy::AnimaAccess;

use super::{
    CatalogPrecondition, CommitCallbacks, CommitFailurePoint, CommitMode, CoreCommitCoordinator,
    PreparedObjectRevision, PublicationTarget,
};
use crate::publication::PublicationPhase;

const CORE_ID: &str = "core-failure-injection";
const ROOT_ID: &str = "01J00000000000000000000000";
const OBJECT_ID: &str = "01J00000000000000000000001";
const CRASH_EXIT_CODE: i32 = 86;
const CRASH_HELPER_SCENARIO: &str = "ANIMA_COREFS_CRASH_HELPER_SCENARIO";
const CRASH_HELPER_ROOT: &str = "ANIMA_COREFS_CRASH_HELPER_ROOT";
const CRASH_HELPER_POINT: &str = "ANIMA_COREFS_CRASH_HELPER_POINT";

fn reset_root(name: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-failure-injection-{}-{name}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    root
}

fn keys() -> FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), 1).unwrap()
}

fn prepare(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    revision: u64,
    body: &[u8],
) -> PreparedObjectRevision {
    let (object_key, aad, encoded) = encoded_revision(revision, body);
    coordinator
        .prepare_object_revision(keys, &object_key, &aad, &mut Cursor::new(encoded))
        .unwrap()
}

fn encoded_revision(revision: u64, body: &[u8]) -> (SecretBytes, ObjectBaseAad, Vec<u8>) {
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
        "2026-07-16T00:00:00Z",
        "2026-07-16T00:00:00Z",
        "text/markdown",
        BTreeMap::new(),
        BodyEncoding::Utf8,
        body,
    )
    .unwrap();
    let encoded = encode_envelope(&object_key, &aad, &metadata, body).unwrap();
    (object_key, aad, encoded)
}

fn catalog(generation: u64, prepared: &PreparedObjectRevision) -> CatalogGeneration {
    let root = CatalogEntryCommon::new(
        OpaqueId::parse(ROOT_ID).unwrap(),
        None,
        PortableName::parse("Core").unwrap(),
        FolderOwner::User,
        AnimaAccess::Write,
    );
    let object_common = CatalogEntryCommon::new(
        OpaqueId::parse(OBJECT_ID).unwrap(),
        Some(OpaqueId::parse(ROOT_ID).unwrap()),
        PortableName::parse("Note.md").unwrap(),
        FolderOwner::User,
        AnimaAccess::Write,
    );
    let object = CatalogObject::new(
        prepared.revision(),
        prepared.physical_name().clone(),
        prepared.content_hash().clone(),
        ObjectKind::Note,
        prepared.wrapped_dek().clone(),
        ObjectLifecycle::Live,
    )
    .unwrap();
    CatalogGeneration::new(
        generation,
        vec![
            CatalogGenerationEntry::folder(root),
            CatalogGenerationEntry::object(object_common, object),
        ],
    )
    .unwrap()
}

#[test]
fn invalidation_failure_points_follow_authoritative_head_publication() {
    let root = reset_root("invalidation-order");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let initial = prepare(&coordinator, &keys, 1, b"initial");
    coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
    let current = coordinator
        .load_validation_snapshot(&keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let precondition =
        CatalogPrecondition::object(&current, &OpaqueId::parse(OBJECT_ID).unwrap(), 1).unwrap();
    let next = prepare(&coordinator, &keys, 2, b"next");
    let mut observed = Vec::new();

    coordinator
        .commit_internal_with_hook(
            &keys,
            std::slice::from_ref(&next),
            &[precondition],
            CommitMode::FirstMutation { cutover_epoch: 1 },
            |_, generation| Ok(catalog(generation, &next)),
            CommitCallbacks {
                invalidate: |_| {
                    assert!(coordinator.head_path().is_file());
                    Ok(())
                },
                hook: &mut |point| {
                    observed.push(point);
                    Ok(())
                },
            },
        )
        .unwrap();

    let before = observed
        .iter()
        .position(|point| *point == CommitFailurePoint::BeforeInvalidation)
        .unwrap();
    let after = observed
        .iter()
        .position(|point| *point == CommitFailurePoint::AfterInvalidation)
        .unwrap();
    let head_synced = observed
        .iter()
        .position(|point| {
            *point
                == CommitFailurePoint::Publication {
                    target: PublicationTarget::AuthoritativeHead,
                    phase: PublicationPhase::DestinationSynced,
                }
        })
        .unwrap();
    let receipt_started = observed
        .iter()
        .position(|point| {
            *point
                == CommitFailurePoint::Publication {
                    target: PublicationTarget::CutoverReceipt,
                    phase: PublicationPhase::TemporaryCreated,
                }
        })
        .unwrap();
    assert!(head_synced < receipt_started);
    assert!(before < after);
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
    std::fs::remove_dir_all(root).unwrap();
}

fn publication_points(target: PublicationTarget, immutable: bool) -> Vec<CommitFailurePoint> {
    let base = [
        PublicationPhase::TemporaryCreated,
        PublicationPhase::PayloadWritten,
        PublicationPhase::PayloadSynced,
        PublicationPhase::DestinationPublished,
        PublicationPhase::DestinationSynced,
    ];
    base.into_iter()
        .chain(immutable_cleanup_phases(immutable).iter().copied())
        .map(|phase| CommitFailurePoint::Publication { target, phase })
        .collect()
}

#[cfg(windows)]
fn immutable_cleanup_phases(_immutable: bool) -> &'static [PublicationPhase] {
    &[]
}

#[cfg(not(windows))]
fn immutable_cleanup_phases(immutable: bool) -> &'static [PublicationPhase] {
    if immutable {
        &[
            PublicationPhase::StagingRemoved,
            PublicationPhase::CleanupSynced,
        ]
    } else {
        &[]
    }
}

fn seed_validation(root: &Path) {
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
    let keys = keys();
    let initial = prepare(&coordinator, &keys, 1, b"initial");
    coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
}

fn seed_committed(root: &Path) {
    seed_validation(root);
    commit_seeded_first(root);
}

fn commit_seeded_first(root: &Path) {
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
    let keys = keys();
    let current = coordinator
        .load_validation_snapshot(&keys)
        .unwrap()
        .unwrap()
        .catalog()
        .clone();
    let precondition =
        CatalogPrecondition::object(&current, &OpaqueId::parse(OBJECT_ID).unwrap(), 1).unwrap();
    let next = prepare(&coordinator, &keys, 2, b"committed");
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            std::slice::from_ref(&next),
            &[precondition],
            |_, generation| Ok(catalog(generation, &next)),
            |_| Ok(()),
        )
        .unwrap();
}

fn seed_legacy_receipt_only(root: &Path) {
    seed_validation(root);
    let alternate_root = reset_root("legacy-receipt-source");
    seed_committed(&alternate_root);
    let destination = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
    let source = CoreCommitCoordinator::new(&alternate_root, CORE_ID).unwrap();
    for entry in std::fs::read_dir(source.catalogs_path()).unwrap() {
        let source_path = entry.unwrap().path();
        std::fs::copy(
            &source_path,
            destination
                .catalogs_path()
                .join(source_path.file_name().unwrap()),
        )
        .unwrap();
    }
    std::fs::copy(
        source.cutover_receipt_path(),
        destination.cutover_receipt_path(),
    )
    .unwrap();
    drop(source);
    drop(destination);
    std::fs::remove_dir_all(alternate_root).unwrap();
}

#[test]
fn post_head_cutover_marker_failures_report_recovery_pending_success() {
    for (index, failure_point) in [
        CommitFailurePoint::Publication {
            target: PublicationTarget::CutoverReceipt,
            phase: PublicationPhase::TemporaryCreated,
        },
        CommitFailurePoint::Publication {
            target: PublicationTarget::CutoverComplete,
            phase: PublicationPhase::TemporaryCreated,
        },
    ]
    .into_iter()
    .enumerate()
    {
        let root = reset_root(&format!("post-head-marker-error-{index}"));
        seed_validation(&root);
        let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
        let keys = keys();
        let current = coordinator
            .load_validation_snapshot(&keys)
            .unwrap()
            .unwrap()
            .catalog()
            .clone();
        let precondition =
            CatalogPrecondition::object(&current, &OpaqueId::parse(OBJECT_ID).unwrap(), 1).unwrap();
        let next = prepare(&coordinator, &keys, 2, b"next");
        let outcome = coordinator
            .commit_internal_with_hook(
                &keys,
                std::slice::from_ref(&next),
                &[precondition],
                CommitMode::FirstMutation { cutover_epoch: 1 },
                |_, generation| Ok(catalog(generation, &next)),
                CommitCallbacks {
                    invalidate: |_| Ok(()),
                    hook: &mut |point| {
                        if point == failure_point {
                            return Err(std::io::Error::other("injected marker failure"));
                        }
                        Ok(())
                    },
                },
            )
            .unwrap();

        assert_eq!(outcome.generation(), 2);
        assert!(outcome.recovery_pending());
        assert_committed_generation(&root, 2);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }
}

#[test]
fn torn_unlocked_cutover_observation_is_retried_under_the_commit_lock() {
    let root = reset_root("torn-cutover-observation");
    seed_validation(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();

    let committed = coordinator
        .load_committed_with_observation_hook(&keys, &mut |_| Ok(()), || commit_seeded_first(&root))
        .unwrap()
        .unwrap();

    assert_eq!(committed.head().generation(), 2);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

fn run_crashing_child(root: &Path, scenario: &str, point: CommitFailurePoint) {
    let status = Command::new(std::env::current_exe().unwrap())
        .arg("--ignored")
        .arg("--exact")
        .arg("transaction::failure_tests::helper_process_crashes_at_failure_point")
        .env(CRASH_HELPER_SCENARIO, scenario)
        .env(CRASH_HELPER_ROOT, root)
        .env(CRASH_HELPER_POINT, format!("{point:?}"))
        .status()
        .unwrap();
    assert_eq!(
        status.code(),
        Some(CRASH_EXIT_CODE),
        "helper did not reach {scenario} failure point {point:?}: {status}"
    );
}

fn assert_no_committed_generation(root: &Path, validation_generation: Option<u64>) {
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
    let keys = keys();
    assert!(coordinator.load_committed(&keys).unwrap().is_none());
    assert_eq!(
        coordinator
            .load_validation_snapshot(&keys)
            .unwrap()
            .map(|snapshot| snapshot.head().generation()),
        validation_generation
    );
}

fn assert_committed_generation(root: &Path, generation: u64) {
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
    let committed = coordinator.load_committed(&keys()).unwrap().unwrap();
    assert_eq!(committed.head().generation(), generation);
    assert_eq!(committed.catalog().generation(), generation);
}

#[test]
fn object_publication_crashes_leave_only_unreferenced_data() {
    for (index, point) in publication_points(PublicationTarget::Object, true)
        .into_iter()
        .enumerate()
    {
        let root = reset_root(&format!("object-{index}"));
        run_crashing_child(&root, "prepare", point);
        assert_no_committed_generation(&root, None);
        std::fs::remove_dir_all(root).unwrap();
    }
}

#[test]
fn validation_publication_crashes_are_absent_or_complete() {
    let mut index = 0;
    for point in publication_points(PublicationTarget::Catalog, true) {
        let root = reset_root(&format!("validation-catalog-{index}"));
        run_crashing_child(&root, "initialize", point);
        assert_no_committed_generation(&root, None);
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for (phase_index, point) in publication_points(PublicationTarget::ValidationHead, false)
        .into_iter()
        .enumerate()
    {
        let root = reset_root(&format!("validation-head-{index}"));
        run_crashing_child(&root, "initialize", point);
        assert_no_committed_generation(&root, if phase_index >= 3 { Some(1) } else { None });
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
}

#[test]
fn first_commit_crashes_preserve_validation_or_recover_complete_cutover() {
    let mut index = 0;
    for point in publication_points(PublicationTarget::Catalog, true) {
        let root = reset_root(&format!("first-catalog-{index}"));
        seed_validation(&root);
        run_crashing_child(&root, "first", point);
        assert_no_committed_generation(&root, Some(1));
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for (phase_index, point) in publication_points(PublicationTarget::AuthoritativeHead, false)
        .into_iter()
        .enumerate()
    {
        let root = reset_root(&format!("first-head-{index}"));
        seed_validation(&root);
        run_crashing_child(&root, "first", point);
        if phase_index >= 3 {
            assert_committed_generation(&root, 2);
        } else {
            assert_no_committed_generation(&root, Some(1));
        }
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for target in [
        PublicationTarget::CutoverReceipt,
        PublicationTarget::CutoverComplete,
    ] {
        for point in publication_points(target, target == PublicationTarget::CutoverComplete) {
            let root = reset_root(&format!("first-late-{index}"));
            seed_validation(&root);
            run_crashing_child(&root, "first", point);
            assert_committed_generation(&root, 2);
            std::fs::remove_dir_all(root).unwrap();
            index += 1;
        }
    }
    for point in [
        CommitFailurePoint::BeforeInvalidation,
        CommitFailurePoint::AfterInvalidation,
    ] {
        let root = reset_root(&format!("first-invalidation-{index}"));
        seed_validation(&root);
        run_crashing_child(&root, "first", point);
        assert_committed_generation(&root, 2);
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
}

#[test]
fn later_commit_crashes_preserve_prior_or_complete_next_generation() {
    let mut index = 0;
    for point in publication_points(PublicationTarget::Catalog, true) {
        let root = reset_root(&format!("normal-catalog-{index}"));
        seed_committed(&root);
        run_crashing_child(&root, "normal", point);
        assert_committed_generation(&root, 2);
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for (phase_index, point) in publication_points(PublicationTarget::AuthoritativeHead, false)
        .into_iter()
        .enumerate()
    {
        let root = reset_root(&format!("normal-head-{index}"));
        seed_committed(&root);
        run_crashing_child(&root, "normal", point);
        assert_committed_generation(&root, if phase_index >= 3 { 3 } else { 2 });
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for point in [
        CommitFailurePoint::BeforeInvalidation,
        CommitFailurePoint::AfterInvalidation,
    ] {
        let root = reset_root(&format!("normal-invalidation-{index}"));
        seed_committed(&root);
        run_crashing_child(&root, "normal", point);
        assert_committed_generation(&root, 3);
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
}

#[test]
fn cutover_recovery_is_retryable_at_every_publication_boundary() {
    let head_published = CommitFailurePoint::Publication {
        target: PublicationTarget::AuthoritativeHead,
        phase: PublicationPhase::DestinationPublished,
    };
    let mut index = 0;
    for target in [
        PublicationTarget::AuthoritativeHead,
        PublicationTarget::CutoverComplete,
    ] {
        for point in publication_points(target, target == PublicationTarget::CutoverComplete) {
            let root = reset_root(&format!("legacy-recovery-{index}"));
            seed_legacy_receipt_only(&root);
            run_crashing_child(&root, "recovery", point);
            assert_committed_generation(&root, 2);
            std::fs::remove_dir_all(root).unwrap();
            index += 1;
        }
    }
    for target in [
        PublicationTarget::CutoverReceipt,
        PublicationTarget::CutoverComplete,
    ] {
        for point in publication_points(target, true) {
            let root = reset_root(&format!("head-recovery-{index}"));
            seed_validation(&root);
            run_crashing_child(&root, "first", head_published);
            run_crashing_child(&root, "recovery", point);
            assert_committed_generation(&root, 2);
            std::fs::remove_dir_all(root).unwrap();
            index += 1;
        }
    }
}

#[test]
#[ignore = "subprocess crash helper"]
fn helper_process_crashes_at_failure_point() {
    let Some(scenario) = std::env::var_os(CRASH_HELPER_SCENARIO) else {
        return;
    };
    let root = std::path::PathBuf::from(std::env::var_os(CRASH_HELPER_ROOT).unwrap());
    let requested = std::env::var(CRASH_HELPER_POINT).unwrap();
    let mut hook = |point: CommitFailurePoint| {
        if format!("{point:?}") == requested {
            std::process::exit(CRASH_EXIT_CODE);
        }
        Ok(())
    };
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    match scenario.to_str().unwrap() {
        "prepare" => {
            let (object_key, aad, encoded) = encoded_revision(1, b"prepared");
            coordinator
                .prepare_object_revision_with_hook(
                    &keys,
                    &object_key,
                    &aad,
                    &mut Cursor::new(encoded),
                    &mut hook,
                )
                .unwrap();
        }
        "initialize" => {
            let initial = prepare(&coordinator, &keys, 1, b"initial");
            coordinator
                .initialize_validation_snapshot_with_hook(
                    &keys,
                    std::slice::from_ref(&initial),
                    |generation| Ok(catalog(generation, &initial)),
                    &mut hook,
                )
                .unwrap();
        }
        "first" => {
            let current = coordinator
                .load_validation_snapshot(&keys)
                .unwrap()
                .unwrap()
                .catalog()
                .clone();
            let precondition =
                CatalogPrecondition::object(&current, &OpaqueId::parse(OBJECT_ID).unwrap(), 1)
                    .unwrap();
            let next = prepare(&coordinator, &keys, 2, b"next");
            coordinator
                .commit_internal_with_hook(
                    &keys,
                    std::slice::from_ref(&next),
                    &[precondition],
                    CommitMode::FirstMutation { cutover_epoch: 1 },
                    |_, generation| Ok(catalog(generation, &next)),
                    CommitCallbacks {
                        invalidate: |_| Ok(()),
                        hook: &mut hook,
                    },
                )
                .unwrap();
        }
        "normal" => {
            let current = coordinator
                .load_committed(&keys)
                .unwrap()
                .unwrap()
                .catalog()
                .clone();
            let precondition =
                CatalogPrecondition::object(&current, &OpaqueId::parse(OBJECT_ID).unwrap(), 2)
                    .unwrap();
            let next = prepare(&coordinator, &keys, 3, b"later");
            coordinator
                .commit_internal_with_hook(
                    &keys,
                    std::slice::from_ref(&next),
                    &[precondition],
                    CommitMode::Normal,
                    |_, generation| Ok(catalog(generation, &next)),
                    CommitCallbacks {
                        invalidate: |_| Ok(()),
                        hook: &mut hook,
                    },
                )
                .unwrap();
        }
        "recovery" => {
            coordinator
                .load_committed_with_hook(&keys, &mut hook)
                .unwrap();
        }
        other => panic!("unknown crash helper scenario: {other}"),
    }
    panic!("crash helper did not observe requested failure point {requested}");
}
