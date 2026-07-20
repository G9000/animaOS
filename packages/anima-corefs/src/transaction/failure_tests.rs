use std::collections::BTreeMap;
use std::io::Cursor;
use std::path::Path;
use std::process::Command;
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::Duration;

use crate::catalog::{
    CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, CatalogObject, ObjectLifecycle,
};
use crate::crypto::{derive_corefs_subkeys, FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes};
use crate::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use crate::folders::{FolderOwner, PortableName};
use crate::id::OpaqueId;
use crate::policy::AnimaAccess;
use crate::rotation::{FrkKeyring, RotationError};

use super::cache::{AuthenticatedCommitSnapshot, CacheLookupKey, PointerSet};
use super::{
    CatalogPrecondition, CommitCallbacks, CommitError, CommitFailurePoint, CommitMode,
    CoreCommitCoordinator, CoreCommitLock, PreparedObjectRevision, PublicationTarget,
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

fn pending_keys() -> FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![0x43; 32]).unwrap(), 2).unwrap()
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
fn pre_head_failure_keeps_only_the_prior_snapshot() {
    let root = reset_root("pre-head-keeps-prior-cache");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let initial = prepare(&coordinator, &keys, 1, b"initial");
    coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
    let initial_precondition = CatalogPrecondition::object(
        &catalog(1, &initial),
        &OpaqueId::parse(OBJECT_ID).unwrap(),
        1,
    )
    .unwrap();
    let committed = prepare(&coordinator, &keys, 2, b"committed");
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            std::slice::from_ref(&committed),
            &[initial_precondition],
            |_, generation| Ok(catalog(generation, &committed)),
            |_| Ok(()),
        )
        .unwrap();
    let prior = coordinator
        .cache
        .inner
        .lock()
        .unwrap()
        .as_ref()
        .cloned()
        .expect("successful first mutation should cache its authoritative snapshot");
    assert_eq!(prior.catalog().generation(), 2);
    let precondition =
        CatalogPrecondition::object(prior.catalog(), &OpaqueId::parse(OBJECT_ID).unwrap(), 2)
            .unwrap();
    let next = prepare(&coordinator, &keys, 3, b"next");
    let failure_point = CommitFailurePoint::Publication {
        target: PublicationTarget::AuthoritativeHead,
        phase: PublicationPhase::TemporaryCreated,
    };

    let error = coordinator
        .commit_internal_with_hook(
            &keys,
            std::slice::from_ref(&next),
            &[precondition],
            CommitMode::Normal,
            |_, generation| Ok(catalog(generation, &next)),
            CommitCallbacks {
                invalidate: |_| Ok(()),
                hook: &mut |point| {
                    if point == failure_point {
                        return Err(std::io::Error::other("injected pre-HEAD failure"));
                    }
                    Ok(())
                },
            },
        )
        .unwrap_err();
    match error {
        CommitError::Io(error) => {
            assert_eq!(error.to_string(), "injected pre-HEAD failure");
        }
        error => panic!("expected original injected I/O error, got {error}"),
    }

    let after = coordinator
        .cache
        .inner
        .lock()
        .unwrap()
        .as_ref()
        .cloned()
        .expect("pre-HEAD failure should retain the prior snapshot");
    assert!(Arc::ptr_eq(&prior, &after));
    assert_eq!(after.catalog().generation(), 2);

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn post_rename_pre_sync_failure_preserves_the_prior_snapshot() {
    let root = reset_root("post-rename-pre-sync-keeps-prior-cache");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let initial = prepare(&coordinator, &keys, 1, b"initial");
    coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
    let initial_precondition = CatalogPrecondition::object(
        &catalog(1, &initial),
        &OpaqueId::parse(OBJECT_ID).unwrap(),
        1,
    )
    .unwrap();
    let committed = prepare(&coordinator, &keys, 2, b"committed");
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            std::slice::from_ref(&committed),
            &[initial_precondition],
            |_, generation| Ok(catalog(generation, &committed)),
            |_| Ok(()),
        )
        .unwrap();
    let prior = coordinator
        .cache
        .current()
        .expect("successful first mutation should cache its authoritative snapshot");
    assert_eq!(prior.catalog().generation(), 2);
    let precondition =
        CatalogPrecondition::object(prior.catalog(), &OpaqueId::parse(OBJECT_ID).unwrap(), 2)
            .unwrap();
    let next = prepare(&coordinator, &keys, 3, b"next");
    let failure_point = CommitFailurePoint::Publication {
        target: PublicationTarget::AuthoritativeHead,
        phase: PublicationPhase::DestinationPublished,
    };

    let error = coordinator
        .commit_internal_with_hook(
            &keys,
            std::slice::from_ref(&next),
            &[precondition],
            CommitMode::Normal,
            |_, generation| Ok(catalog(generation, &next)),
            CommitCallbacks {
                invalidate: |_| Ok(()),
                hook: &mut |point| {
                    if point == failure_point {
                        return Err(std::io::Error::other("injected pre-durable HEAD failure"));
                    }
                    Ok(())
                },
            },
        )
        .unwrap_err();
    match error {
        CommitError::Io(error) => {
            assert_eq!(error.to_string(), "injected pre-durable HEAD failure");
        }
        error => panic!("expected original injected I/O error, got {error}"),
    }

    let after = coordinator
        .cache
        .current()
        .expect("pre-durable HEAD failure should retain the prior snapshot");
    assert!(Arc::ptr_eq(&prior, &after));
    assert_eq!(after.catalog().generation(), 2);

    let observer = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    assert_eq!(
        observer
            .load_committed(&keys)
            .unwrap()
            .expect("the rename should already be visible")
            .head()
            .generation(),
        3
    );

    drop(observer);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn post_head_failure_reconciles_cache_to_durable_authority() {
    let root = reset_root("post-head-reconciles-cache");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let initial = prepare(&coordinator, &keys, 1, b"initial");
    coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
    let initial_precondition = CatalogPrecondition::object(
        &catalog(1, &initial),
        &OpaqueId::parse(OBJECT_ID).unwrap(),
        1,
    )
    .unwrap();
    let committed = prepare(&coordinator, &keys, 2, b"committed");
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            std::slice::from_ref(&committed),
            &[initial_precondition],
            |_, generation| Ok(catalog(generation, &committed)),
            |_| Ok(()),
        )
        .unwrap();
    coordinator.load_committed(&keys).unwrap().unwrap();
    let prior = coordinator
        .cache
        .inner
        .lock()
        .unwrap()
        .as_ref()
        .cloned()
        .unwrap();
    let precondition =
        CatalogPrecondition::object(prior.catalog(), &OpaqueId::parse(OBJECT_ID).unwrap(), 2)
            .unwrap();
    let next = prepare(&coordinator, &keys, 3, b"next");
    let failure_point = CommitFailurePoint::Publication {
        target: PublicationTarget::AuthoritativeHead,
        phase: PublicationPhase::DestinationSynced,
    };

    let error = coordinator
        .commit_internal_with_hook(
            &keys,
            std::slice::from_ref(&next),
            &[precondition],
            CommitMode::Normal,
            |_, generation| Ok(catalog(generation, &next)),
            CommitCallbacks {
                invalidate: |_| Ok(()),
                hook: &mut |point| {
                    if point == failure_point {
                        return Err(std::io::Error::other("injected post-HEAD failure"));
                    }
                    Ok(())
                },
            },
        )
        .unwrap_err();
    match error {
        CommitError::Io(error) => {
            assert_eq!(error.to_string(), "injected post-HEAD failure");
        }
        error => panic!("expected original injected I/O error, got {error}"),
    }

    let after = coordinator
        .cache
        .inner
        .lock()
        .unwrap()
        .as_ref()
        .cloned()
        .expect("durable post-HEAD authority should remain cached");
    assert!(!Arc::ptr_eq(&prior, &after));
    assert_eq!(after.catalog().generation(), 3);
    let loaded = coordinator.load_committed(&keys).unwrap().unwrap();
    assert_eq!(loaded.head().generation(), 3);
    assert!(std::ptr::eq(loaded.catalog(), after.catalog().as_ref()));

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn post_head_recovery_pending_clears_the_cache() {
    let root = reset_root("post-head-recovery-pending-clears-cache");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let initial = prepare(&coordinator, &keys, 1, b"initial");
    let validation = coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
    assert!(
        coordinator.cache.current().is_none(),
        "validation-only initialization must not publish authoritative cache state"
    );
    let keyring = FrkKeyring::single(&keys);
    let stale_key = CacheLookupKey::derive(
        PointerSet {
            head: None,
            receipt: None,
            complete: None,
        },
        CORE_ID,
        &keyring,
        &keys,
    )
    .unwrap();
    coordinator
        .cache
        .replace(Arc::new(AuthenticatedCommitSnapshot::new(
            &stale_key,
            Arc::new(validation.catalog().clone()),
            None,
        )));
    let precondition = CatalogPrecondition::object(
        validation.catalog(),
        &OpaqueId::parse(OBJECT_ID).unwrap(),
        1,
    )
    .unwrap();
    let next = prepare(&coordinator, &keys, 2, b"next");
    let failure_point = CommitFailurePoint::Publication {
        target: PublicationTarget::CutoverReceipt,
        phase: PublicationPhase::TemporaryCreated,
    };

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
                        return Err(std::io::Error::other("injected post-HEAD failure"));
                    }
                    Ok(())
                },
            },
        )
        .unwrap();

    assert!(outcome.recovery_pending());
    assert!(coordinator.cache.inner.lock().unwrap().is_none());

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn normal_success_clears_cache_when_retained_receipt_changes_after_head_sync() {
    let root = reset_root("normal-success-retained-receipt-mismatch");
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    let initial = prepare(&coordinator, &keys, 1, b"initial");
    coordinator
        .initialize_validation_snapshot(&keys, std::slice::from_ref(&initial), |generation| {
            Ok(catalog(generation, &initial))
        })
        .unwrap();
    let initial_precondition = CatalogPrecondition::object(
        &catalog(1, &initial),
        &OpaqueId::parse(OBJECT_ID).unwrap(),
        1,
    )
    .unwrap();
    let committed = prepare(&coordinator, &keys, 2, b"committed");
    coordinator
        .commit_first_mutation(
            &keys,
            1,
            std::slice::from_ref(&committed),
            &[initial_precondition],
            |_, generation| Ok(catalog(generation, &committed)),
            |_| Ok(()),
        )
        .unwrap();
    let prior = coordinator
        .cache
        .current()
        .expect("first mutation should cache the authenticated retained markers");
    let precondition =
        CatalogPrecondition::object(prior.catalog(), &OpaqueId::parse(OBJECT_ID).unwrap(), 2)
            .unwrap();
    let next = prepare(&coordinator, &keys, 3, b"next");
    let mutate_after_head_sync = CommitFailurePoint::Publication {
        target: PublicationTarget::AuthoritativeHead,
        phase: PublicationPhase::DestinationSynced,
    };

    let outcome = coordinator
        .commit_internal_with_hook(
            &keys,
            std::slice::from_ref(&next),
            &[precondition],
            CommitMode::Normal,
            |_, generation| Ok(catalog(generation, &next)),
            CommitCallbacks {
                invalidate: |_| Ok(()),
                hook: &mut |point| {
                    if point == mutate_after_head_sync {
                        std::fs::remove_file(coordinator.cutover_receipt_path())?;
                    }
                    Ok(())
                },
            },
        )
        .unwrap();

    assert_eq!(outcome.generation(), 3);
    assert!(
        coordinator.cache.current().is_none(),
        "a malformed retained marker tuple must not become exact cache authority"
    );
    assert!(matches!(
        coordinator.load_committed(&keys),
        Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt)
    ));

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
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

#[test]
fn receipt_without_head_bypasses_cache_and_runs_recovery() {
    let root = reset_root("receipt-without-head-cache-miss");
    seed_committed(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    coordinator.load_committed(&keys).unwrap().unwrap();
    let prior = coordinator.cache.current().unwrap();
    std::fs::remove_file(coordinator.head_path()).unwrap();
    std::fs::remove_file(coordinator.cutover_complete_path()).unwrap();

    let committed = coordinator.load_committed(&keys).unwrap().unwrap();

    assert_eq!(committed.head().generation(), 2);
    assert!(coordinator.head_path().is_file());
    assert!(coordinator.cutover_complete_path().is_file());
    assert!(!Arc::ptr_eq(&prior, &coordinator.cache.current().unwrap()));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn missing_head_with_completion_bypasses_cache_and_runs_recovery() {
    let root = reset_root("missing-head-with-completion-cache-miss");
    seed_committed(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    coordinator.load_committed(&keys).unwrap().unwrap();
    std::fs::remove_file(coordinator.head_path()).unwrap();

    assert!(matches!(
        coordinator.load_committed(&keys),
        Err(CommitError::AuthoritativeHeadMissingAfterCutover)
    ));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn divergent_receipt_and_completion_bypass_cache() {
    let root = reset_root("divergent-receipt-completion-cache-miss");
    seed_committed(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys();
    coordinator.load_committed(&keys).unwrap().unwrap();
    std::fs::copy(
        coordinator.validation_head_path(),
        coordinator.cutover_complete_path(),
    )
    .unwrap();

    assert!(matches!(
        coordinator.load_committed(&keys),
        Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt)
    ));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

fn seed_mixed_frk_completion_gap(root: &Path) {
    seed_committed(root);
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
    let old_keys = keys();
    let active_keys = pending_keys();
    let keyring = FrkKeyring::new([&old_keys, &active_keys]).unwrap();
    coordinator
        .rotate_frk(&keyring, &active_keys, 2, |_| Ok(()))
        .unwrap();
    std::fs::remove_file(coordinator.cutover_complete_path()).unwrap();
}

#[test]
fn mixed_frk_recovery_derives_every_required_catalog_key_identity() {
    let root = reset_root("mixed-frk-recovery-identities");
    seed_mixed_frk_completion_gap(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys();
    let active_keys = pending_keys();
    let keyring = FrkKeyring::new([&old_keys, &active_keys]).unwrap();
    let commit_lock =
        CoreCommitLock::acquire_in(&coordinator.root_dir, &coordinator.fs_dir).unwrap();

    let committed = coordinator
        .load_committed_recovering_with_keyring(&commit_lock, &keyring)
        .unwrap()
        .unwrap();
    drop(commit_lock);

    assert_eq!(committed.head().generation(), 3);
    assert_eq!(committed.head().required_frk_version(), 2);
    let snapshot = coordinator.cache.current().unwrap();
    let exact =
        CacheLookupKey::derive(snapshot.pointers.clone(), CORE_ID, &keyring, &active_keys).unwrap();
    assert_eq!(exact.required_catalog_versions(), &[1, 2]);
    assert!(Arc::ptr_eq(
        &snapshot,
        &coordinator.cache.get(&exact).unwrap()
    ));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn cutover_recovery_replaces_cache_only_after_verified_completion() {
    let root = reset_root("mixed-frk-recovery-cache-publication");
    seed_committed(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys();
    let active_keys = pending_keys();
    let keyring = FrkKeyring::new([&old_keys, &active_keys]).unwrap();
    coordinator.load_committed(&old_keys).unwrap().unwrap();
    let prior = coordinator.cache.current().unwrap();
    coordinator
        .rotate_frk(&keyring, &active_keys, 2, |_| Ok(()))
        .unwrap();
    std::fs::remove_file(coordinator.cutover_complete_path()).unwrap();
    let commit_lock =
        CoreCommitLock::acquire_in(&coordinator.root_dir, &coordinator.fs_dir).unwrap();
    let mut saw_durable_completion = false;

    let committed = coordinator
        .load_committed_recovering_with_keyring_and_hook_inner(
            &commit_lock,
            &keyring,
            &mut |point| {
                if let CommitFailurePoint::Publication {
                    target: PublicationTarget::CutoverComplete,
                    phase,
                } = point
                {
                    assert!(Arc::ptr_eq(&prior, &coordinator.cache.current().unwrap()));
                    if phase == PublicationPhase::DestinationSynced {
                        saw_durable_completion = true;
                    }
                }
                Ok(())
            },
            None,
        )
        .unwrap()
        .unwrap();
    drop(commit_lock);

    assert!(saw_durable_completion);
    assert_eq!(committed.head().generation(), 3);
    let replacement = coordinator.cache.current().unwrap();
    assert!(!Arc::ptr_eq(&prior, &replacement));
    assert_eq!(replacement.catalog().generation(), 3);
    assert_eq!(replacement.pointers.head.as_ref(), Some(committed.head()));
    assert_eq!(replacement.pointers.receipt, replacement.pointers.complete);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

fn seed_single_version_completion_gap(root: &Path) {
    seed_validation(root);
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
                    if point
                        == (CommitFailurePoint::Publication {
                            target: PublicationTarget::CutoverComplete,
                            phase: PublicationPhase::TemporaryCreated,
                        })
                    {
                        return Err(std::io::Error::other("leave completion pending"));
                    }
                    Ok(())
                },
            },
        )
        .unwrap();
    assert!(outcome.recovery_pending());
}

#[test]
fn concurrent_unlocked_load_recovery_and_commit_do_not_invert_locks() {
    let root = reset_root("concurrent-load-recovery-and-commit");
    seed_single_version_completion_gap(&root);
    let timeout = Duration::from_secs(5);
    let (recovery_entered_tx, recovery_entered_rx) = mpsc::channel();
    let (release_recovery_tx, release_recovery_rx) = mpsc::channel();
    let (recovery_done_tx, recovery_done_rx) = mpsc::channel();
    let recovery_root = root.clone();
    let recovery_thread = thread::spawn(move || {
        let coordinator = CoreCommitCoordinator::new(&recovery_root, CORE_ID).unwrap();
        let result = coordinator.load_committed_with_hook(&keys(), &mut |point| {
            if point
                == (CommitFailurePoint::Publication {
                    target: PublicationTarget::CutoverComplete,
                    phase: PublicationPhase::TemporaryCreated,
                })
            {
                recovery_entered_tx.send(()).unwrap();
                release_recovery_rx.recv_timeout(timeout).unwrap();
            }
            Ok(())
        });
        recovery_done_tx
            .send(result.map(|value| value.unwrap().head().generation()))
            .unwrap();
    });

    recovery_entered_rx.recv_timeout(timeout).unwrap();
    let commit_root = root.clone();
    let (commit_done_tx, commit_done_rx) = mpsc::channel();
    let commit_thread = thread::spawn(move || {
        let coordinator = CoreCommitCoordinator::new(&commit_root, CORE_ID).unwrap();
        let active_keys = keys();
        let result = coordinator.commit(
            &active_keys,
            &[],
            &[],
            |current, generation| {
                CatalogGeneration::new(generation, current.unwrap().entries().to_vec())
            },
            |_| Ok(()),
        );
        commit_done_tx
            .send(result.map(|outcome| outcome.generation()))
            .unwrap();
    });
    assert!(matches!(
        commit_done_rx.recv_timeout(timeout).unwrap(),
        Err(CommitError::LockBusy)
    ));
    release_recovery_tx.send(()).unwrap();
    assert_eq!(recovery_done_rx.recv_timeout(timeout).unwrap().unwrap(), 2);
    recovery_thread.join().unwrap();
    commit_thread.join().unwrap();

    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let active_keys = keys();
    let outcome = coordinator
        .commit(
            &active_keys,
            &[],
            &[],
            |current, generation| {
                CatalogGeneration::new(generation, current.unwrap().entries().to_vec())
            },
            |_| Ok(()),
        )
        .unwrap();
    assert_eq!(outcome.generation(), 3);
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
fn rotation_during_keyring_load_retries_the_current_head_under_lock() {
    let root = reset_root("torn-rotation-observation");
    seed_committed(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys();
    let pending_keys = pending_keys();
    let keyring = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();

    let committed = coordinator
        .load_committed_with_keyring_observation_hook(&keyring, || {
            coordinator
                .rotate_frk(&keyring, &pending_keys, 2, |_| Ok(()))
                .unwrap();
        })
        .unwrap()
        .unwrap();

    assert_eq!(committed.head().generation(), 3);
    assert_eq!(committed.head().required_frk_version(), 2);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn frk_rotation_recovers_pending_first_cutover_before_rotating() {
    let root = reset_root("rotation-after-pending-cutover");
    seed_validation(&root);
    run_crashing_child(
        &root,
        "first",
        CommitFailurePoint::Publication {
            target: PublicationTarget::CutoverComplete,
            phase: PublicationPhase::TemporaryCreated,
        },
    );

    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys();
    let pending_keys = pending_keys();
    let keyring = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();
    let outcome = coordinator
        .rotate_frk(&keyring, &pending_keys, 2, |_| Ok(()))
        .unwrap();

    assert_eq!(outcome.generation(), 3);
    assert!(coordinator.cutover_complete_path().is_file());
    let committed = coordinator
        .load_committed_with_keyring(&keyring)
        .unwrap()
        .unwrap();
    assert_eq!(committed.head().generation(), 3);
    assert_eq!(committed.head().required_frk_version(), 2);

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn frk_rotation_rejects_pending_material_reused_from_retained_version() {
    let root = reset_root("rotation-reused-retained-material");
    seed_committed(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let retained_keys = keys();
    let active_keys = pending_keys();
    let first_keyring = FrkKeyring::new([&retained_keys, &active_keys]).unwrap();
    coordinator
        .rotate_frk(&first_keyring, &active_keys, 2, |_| Ok(()))
        .unwrap();

    let reused_pending_keys =
        derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), 3).unwrap();
    let keyring = FrkKeyring::new([&retained_keys, &active_keys, &reused_pending_keys]).unwrap();
    let error = coordinator
        .rotate_frk(&keyring, &reused_pending_keys, 3, |_| Ok(()))
        .unwrap_err();

    assert!(matches!(
        error,
        CommitError::Rotation(RotationError::PendingKeyMaterialReused)
    ));
    let committed = coordinator
        .load_committed_with_keyring(&keyring)
        .unwrap()
        .unwrap();
    assert_eq!(committed.head().generation(), 3);
    assert_eq!(committed.head().required_frk_version(), 2);

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

fn assert_rotation_generation(root: &Path, generation: u64) {
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
    let old_keys = keys();
    let pending_keys = pending_keys();
    let keyring = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();
    let committed = coordinator
        .load_committed_with_keyring(&keyring)
        .unwrap()
        .unwrap();
    assert_eq!(committed.head().generation(), generation);
    assert_eq!(
        committed.head().required_frk_version(),
        if generation == 2 { 1 } else { 2 }
    );
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
fn validation_advancement_crashes_preserve_prior_or_complete_next_shadow_generation() {
    let mut index = 0;
    for point in publication_points(PublicationTarget::Catalog, true) {
        let root = reset_root(&format!("validation-advance-catalog-{index}"));
        seed_validation(&root);
        run_crashing_child(&root, "advance", point);
        assert_no_committed_generation(&root, Some(1));
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for (phase_index, point) in publication_points(PublicationTarget::ValidationHead, false)
        .into_iter()
        .enumerate()
    {
        let root = reset_root(&format!("validation-advance-head-{index}"));
        seed_validation(&root);
        run_crashing_child(&root, "advance", point);
        assert_no_committed_generation(&root, Some(if phase_index >= 3 { 2 } else { 1 }));
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
fn targeted_object_rotation_crashes_leave_the_prior_catalog_authoritative() {
    for (index, point) in publication_points(PublicationTarget::Object, true)
        .into_iter()
        .enumerate()
    {
        let root = reset_root(&format!("object-rotation-{index}"));
        seed_committed(&root);
        run_crashing_child(&root, "object-rotation", point);
        assert_committed_generation(&root, 2);
        std::fs::remove_dir_all(root).unwrap();
    }
}

#[test]
fn frk_rotation_crashes_preserve_old_head_or_recover_pending_head() {
    let mut index = 0;
    for point in publication_points(PublicationTarget::Catalog, true) {
        let root = reset_root(&format!("rotation-catalog-{index}"));
        seed_committed(&root);
        run_crashing_child(&root, "rotation", point);
        assert_rotation_generation(&root, 2);
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for (phase_index, point) in publication_points(PublicationTarget::AuthoritativeHead, false)
        .into_iter()
        .enumerate()
    {
        let root = reset_root(&format!("rotation-head-{index}"));
        seed_committed(&root);
        run_crashing_child(&root, "rotation", point);
        assert_rotation_generation(&root, if phase_index >= 3 { 3 } else { 2 });
        std::fs::remove_dir_all(root).unwrap();
        index += 1;
    }
    for point in [
        CommitFailurePoint::BeforeInvalidation,
        CommitFailurePoint::AfterInvalidation,
    ] {
        let root = reset_root(&format!("rotation-invalidation-{index}"));
        seed_committed(&root);
        run_crashing_child(&root, "rotation", point);
        assert_rotation_generation(&root, 3);
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
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID).unwrap();
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
        "object-rotation" => {
            let current = coordinator
                .load_committed(&keys)
                .unwrap()
                .unwrap()
                .catalog()
                .clone();
            let object_key = SecretBytes::new(vec![0x71; 32]).unwrap();
            coordinator
                .prepare_object_key_rotation_with_hook(
                    &keys,
                    &current,
                    &OpaqueId::parse(OBJECT_ID).unwrap(),
                    &object_key,
                    "2026-07-17T02:00:00Z",
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
        "advance" => {
            let selected = coordinator
                .load_validation_snapshot(&keys)
                .unwrap()
                .unwrap();
            let precondition = CatalogPrecondition::object(
                selected.catalog(),
                &OpaqueId::parse(OBJECT_ID).unwrap(),
                1,
            )
            .unwrap();
            let next = prepare(&coordinator, &keys, 2, b"next");
            coordinator
                .advance_validation_snapshot_with_hook(
                    &keys,
                    &selected,
                    std::slice::from_ref(&next),
                    &[precondition],
                    |_, generation| Ok(catalog(generation, &next)),
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
        "rotation" => {
            let pending_keys = pending_keys();
            let keyring = FrkKeyring::new([&keys, &pending_keys]).unwrap();
            coordinator
                .rotate_frk_with_hook(&keyring, &pending_keys, 2, |_| Ok(()), &mut hook)
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
