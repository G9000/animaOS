use std::cell::Cell;
use std::collections::{BTreeMap, HashMap};
use std::ffi::OsStr;
use std::io::Cursor;
use std::path::PathBuf;
use std::sync::Arc;
use std::thread;

use cap_std::{ambient_authority, fs::Dir};

use crate::catalog::{
    encrypt_catalog_generation, CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry,
    CatalogObject, ContentHash, ObjectLifecycle, ObjectPhysicalName, WrappedObjectDekRecord,
};
use crate::crypto::{
    derive_corefs_subkeys, FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes,
    OBJECT_KEY_ENVELOPE_VERSION, OBJECT_WRAP_ALGORITHM,
};
use crate::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use crate::folders::{FolderOwner, PortableName};
use crate::head::{HeadError, HeadRecord};
use crate::id::OpaqueId;
use crate::policy::AnimaAccess;
use crate::rotation::FrkKeyring;

use super::cache::{
    AuthenticatedCommitSnapshot, CacheError, CacheLookupKey, CommitCache, PointerSet,
    ValidatedObjectBinding, ValidatedObjectState,
};
use super::{
    precondition_covers_source, validate_opened_regular_file, CatalogLoadProbe, CatalogLoadStage,
    CatalogPrecondition, CommitCallbacks, CommitConflict, CommitError, CommitFailurePoint,
    CommitMode, CommitProbe, CommitStage, CoreCommitCoordinator, CoreCommitLock,
    PreparedObjectRevision, PublicationTarget, RotationProbe, RotationStage,
};
use crate::publication::PublicationPhase;

const CORE_ID: &str = "cache-core";
const ROOT_ID: &str = "01J00000000000000000000000";
const FIRST_OBJECT_ID: &str = "01J00000000000000000000001";
const SECOND_OBJECT_ID: &str = "01J00000000000000000000002";
const THIRD_OBJECT_ID: &str = "01J00000000000000000000003";
const FOURTH_OBJECT_ID: &str = "01J00000000000000000000004";
const FIFTH_OBJECT_ID: &str = "01J00000000000000000000005";

fn keys(fill: u8, version: u32) -> FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![fill; 32]).unwrap(), version).unwrap()
}

fn catalog(generation: u64) -> Arc<CatalogGeneration> {
    Arc::new(
        CatalogGeneration::new(
            generation,
            vec![CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                OpaqueId::parse(ROOT_ID).unwrap(),
                None,
                PortableName::parse("Core").unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            ))],
        )
        .unwrap(),
    )
}

fn head(keys: &FrkSubkeys, generation: u64) -> HeadRecord {
    let catalog = catalog(generation);
    let encrypted = encrypt_catalog_generation(keys, CORE_ID, &catalog).unwrap();
    HeadRecord::new_for_catalog(keys, CORE_ID, &encrypted, keys.frk_version()).unwrap()
}

fn lookup_key(
    pointers: PointerSet,
    keyring: &FrkKeyring<'_>,
    active_keys: &FrkSubkeys,
) -> CacheLookupKey {
    CacheLookupKey::derive(pointers, CORE_ID, keyring, active_keys).unwrap()
}

fn snapshot(key: &CacheLookupKey, generation: u64) -> Arc<AuthenticatedCommitSnapshot> {
    Arc::new(AuthenticatedCommitSnapshot::new(
        key,
        catalog(generation),
        Some(Arc::new(ValidatedObjectState::empty())),
    ))
}

fn seed_committed(coordinator: &CoreCommitCoordinator, keys: &FrkSubkeys) {
    coordinator
        .initialize_validation_snapshot(keys, &[], |generation| Ok((*catalog(generation)).clone()))
        .unwrap();
    coordinator
        .commit_first_mutation(
            keys,
            1,
            &[],
            &[],
            |_, generation| Ok((*catalog(generation)).clone()),
            |_| Ok(()),
        )
        .unwrap();
}

fn authoritative_catalog_path(coordinator: &CoreCommitCoordinator) -> PathBuf {
    pointer_catalog_path(coordinator, super::HEAD_FILE)
}

fn pointer_catalog_path(coordinator: &CoreCommitCoordinator, pointer_name: &str) -> PathBuf {
    let head = coordinator
        .load_pointer_head(pointer_name)
        .unwrap()
        .unwrap();
    coordinator.catalogs_path().join(format!(
        "catalog-{:020}-{}.acore",
        head.generation(),
        head.catalog_hash()
    ))
}

fn prepare_cached_object(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
) -> PreparedObjectRevision {
    let object_key = SecretBytes::new(vec![0x71; 32]).unwrap();
    let aad = ObjectBaseAad::new(
        CORE_ID,
        FIRST_OBJECT_ID,
        ObjectKind::Note,
        ENVELOPE_VERSION,
        1,
        1,
    )
    .unwrap();
    let body = b"cached object body";
    let metadata = EnvelopeMetadata::for_body(
        ObjectKind::Note.as_str(),
        FIRST_OBJECT_ID,
        1,
        "2026-07-22T00:00:00Z",
        "2026-07-22T00:00:00Z",
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

fn cached_object_catalog(
    generation: u64,
    prepared: &PreparedObjectRevision,
    wrapped_dek: WrappedObjectDekRecord,
) -> CatalogGeneration {
    CatalogGeneration::new(
        generation,
        vec![
            CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                OpaqueId::parse(ROOT_ID).unwrap(),
                None,
                PortableName::parse("Core").unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            )),
            CatalogGenerationEntry::object(
                CatalogEntryCommon::new(
                    OpaqueId::parse(FIRST_OBJECT_ID).unwrap(),
                    Some(OpaqueId::parse(ROOT_ID).unwrap()),
                    PortableName::parse("Note.md").unwrap(),
                    FolderOwner::User,
                    AnimaAccess::Write,
                ),
                CatalogObject::new(
                    prepared.revision(),
                    prepared.physical_name().clone(),
                    prepared.content_hash().clone(),
                    ObjectKind::Note,
                    wrapped_dek,
                    ObjectLifecycle::Live,
                )
                .unwrap(),
            ),
        ],
    )
    .unwrap()
}

fn seed_committed_object(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
) -> PreparedObjectRevision {
    let prepared = prepare_cached_object(coordinator, keys);
    coordinator
        .initialize_validation_snapshot(keys, std::slice::from_ref(&prepared), |generation| {
            Ok(cached_object_catalog(
                generation,
                &prepared,
                prepared.wrapped_dek().clone(),
            ))
        })
        .unwrap();
    coordinator
        .commit_first_mutation(
            keys,
            1,
            &[],
            &[],
            |_, generation| {
                Ok(cached_object_catalog(
                    generation,
                    &prepared,
                    prepared.wrapped_dek().clone(),
                ))
            },
            |_| Ok(()),
        )
        .unwrap();
    prepared
}

fn object_precondition(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
) -> CatalogPrecondition {
    let committed = coordinator.load_committed(keys).unwrap().unwrap();
    CatalogPrecondition::object(
        committed.catalog(),
        &OpaqueId::parse(FIRST_OBJECT_ID).unwrap(),
        1,
    )
    .unwrap()
}

fn binding(object_id: &str, fill: u8) -> ValidatedObjectBinding {
    ValidatedObjectBinding {
        object_id: OpaqueId::parse(object_id).unwrap(),
        revision: u64::from(fill),
        object_key_epoch: u32::from(fill),
        physical_name: ObjectPhysicalName::parse(&format!(
            "object-{}.acore",
            format!("{fill:02x}").repeat(16)
        ))
        .unwrap(),
        content_hash: ContentHash::parse(&format!("{fill:02x}").repeat(32)).unwrap(),
        kind: ObjectKind::Note,
        wrapped_dek: WrappedObjectDekRecord::from_parts(
            1,
            u32::from(fill),
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[fill; 12],
            vec![fill; 48],
        )
        .unwrap(),
        binding_digest: [fill; 32],
    }
}

fn coverage_common(stable_id: &str, parent_id: Option<&str>, name: &str) -> CatalogEntryCommon {
    CatalogEntryCommon::new(
        OpaqueId::parse(stable_id).unwrap(),
        parent_id.map(|value| OpaqueId::parse(value).unwrap()),
        PortableName::parse(name).unwrap(),
        FolderOwner::User,
        AnimaAccess::Write,
    )
}

fn coverage_folder(stable_id: &str, parent_id: Option<&str>, name: &str) -> CatalogGenerationEntry {
    CatalogGenerationEntry::folder(coverage_common(stable_id, parent_id, name))
}

fn coverage_object(
    stable_id: &str,
    parent_id: &str,
    name: &str,
    revision: u64,
    fill: u8,
    lifecycle: ObjectLifecycle,
) -> CatalogGenerationEntry {
    CatalogGenerationEntry::object(
        coverage_common(stable_id, Some(parent_id), name),
        CatalogObject::new(
            revision,
            ObjectPhysicalName::parse(&format!(
                "object-{}.acore",
                format!("{fill:02x}").repeat(16)
            ))
            .unwrap(),
            ContentHash::parse(&format!("{fill:02x}").repeat(32)).unwrap(),
            ObjectKind::Note,
            WrappedObjectDekRecord::from_parts(
                1,
                u32::from(fill),
                OBJECT_WRAP_ALGORITHM,
                OBJECT_KEY_ENVELOPE_VERSION,
                &[fill; 12],
                vec![fill; 48],
            )
            .unwrap(),
            lifecycle,
        )
        .unwrap(),
    )
}

fn coverage_catalog(generation: u64, entries: Vec<CatalogGenerationEntry>) -> CatalogGeneration {
    CatalogGeneration::new(generation, entries).unwrap()
}

fn source_precondition(catalog: &CatalogGeneration, stable_id: &str) -> CatalogPrecondition {
    let stable_id = OpaqueId::parse(stable_id).unwrap();
    let entry = catalog
        .entries()
        .iter()
        .find(|entry| entry.stable_id() == &stable_id)
        .unwrap();
    if let Some(object) = entry.object_payload() {
        CatalogPrecondition::object(catalog, &stable_id, object.revision()).unwrap()
    } else {
        CatalogPrecondition::folder(catalog, &stable_id).unwrap()
    }
}

fn destination_precondition(
    catalog: &CatalogGeneration,
    parent_id: &str,
    name: &str,
) -> CatalogPrecondition {
    CatalogPrecondition::vacant(
        catalog,
        &OpaqueId::parse(parent_id).unwrap(),
        PortableName::parse(name).unwrap(),
    )
    .unwrap()
}

fn reference_precondition_coverage(
    current: &CatalogGeneration,
    next: &CatalogGeneration,
    preconditions: &[CatalogPrecondition],
) -> Result<(), CommitError> {
    let current_by_id: HashMap<_, _> = current
        .entries()
        .iter()
        .map(|entry| (entry.stable_id().as_str(), entry))
        .collect();
    let next_by_id: HashMap<_, _> = next
        .entries()
        .iter()
        .map(|entry| (entry.stable_id().as_str(), entry))
        .collect();

    for entry in current.entries() {
        if next_by_id
            .get(entry.stable_id().as_str())
            .is_some_and(|next_entry| *next_entry == entry)
        {
            continue;
        }
        if !preconditions
            .iter()
            .any(|precondition| precondition_covers_source(precondition, entry))
        {
            return Err(CommitConflict::MissingSourcePrecondition {
                stable_id: entry.stable_id().as_str().to_owned(),
            }
            .into());
        }
    }

    for entry in next.entries() {
        let moved_or_created =
            current_by_id
                .get(entry.stable_id().as_str())
                .map_or(true, |current_entry| {
                    current_entry.parent_id() != entry.parent_id()
                        || current_entry.name() != entry.name()
                });
        if !moved_or_created {
            continue;
        }
        let Some(parent_id) = entry.parent_id() else {
            continue;
        };
        if !current_by_id.contains_key(parent_id.as_str()) {
            continue;
        }
        if !preconditions.iter().any(|precondition| {
            matches!(precondition,
                CatalogPrecondition::Vacant(expected)
                    if expected.parent_path.stable_id() == parent_id
                        && expected.name == *entry.name()
            )
        }) {
            return Err(CommitConflict::MissingDestinationPrecondition {
                parent_id: parent_id.as_str().to_owned(),
                name: entry.name().as_str().to_owned(),
            }
            .into());
        }
    }
    Ok(())
}

fn coverage_outcome(result: Result<(), CommitError>) -> Result<(), (String, String)> {
    result.map_err(|error| (format!("{error:?}"), error.to_string()))
}

#[test]
fn ordered_coverage_matches_changed_created_moved_deleted_and_parent_cases() {
    struct Case {
        name: &'static str,
        current: CatalogGeneration,
        next: CatalogGeneration,
        preconditions: Vec<CatalogPrecondition>,
    }

    let unchanged = coverage_catalog(
        1,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_object(
                FIRST_OBJECT_ID,
                ROOT_ID,
                "Note.md",
                1,
                1,
                ObjectLifecycle::Live,
            ),
        ],
    );

    let changed = coverage_catalog(
        2,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_object(
                FIRST_OBJECT_ID,
                ROOT_ID,
                "Note.md",
                2,
                2,
                ObjectLifecycle::Live,
            ),
        ],
    );
    let changed_source = source_precondition(&unchanged, FIRST_OBJECT_ID);

    let moved_current = coverage_catalog(
        1,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(FIRST_OBJECT_ID, Some(ROOT_ID), "Source"),
            coverage_folder(SECOND_OBJECT_ID, Some(ROOT_ID), "Destination"),
            coverage_object(
                FOURTH_OBJECT_ID,
                FIRST_OBJECT_ID,
                "Note.md",
                1,
                3,
                ObjectLifecycle::Live,
            ),
        ],
    );
    let moved_next = coverage_catalog(
        2,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(FIRST_OBJECT_ID, Some(ROOT_ID), "Source"),
            coverage_folder(SECOND_OBJECT_ID, Some(ROOT_ID), "Destination"),
            coverage_object(
                FOURTH_OBJECT_ID,
                SECOND_OBJECT_ID,
                "Moved.md",
                1,
                3,
                ObjectLifecycle::Live,
            ),
        ],
    );

    let created_current = coverage_catalog(1, vec![coverage_folder(ROOT_ID, None, "Core")]);
    let created_next = coverage_catalog(
        2,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_object(
                FIRST_OBJECT_ID,
                ROOT_ID,
                "Created.md",
                1,
                4,
                ObjectLifecycle::Live,
            ),
        ],
    );

    let deleted_current = unchanged.clone();
    let deleted_next = coverage_catalog(2, vec![coverage_folder(ROOT_ID, None, "Core")]);

    let tombstone_current = coverage_catalog(
        1,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(THIRD_OBJECT_ID, Some(ROOT_ID), "Trash"),
            coverage_object(
                FOURTH_OBJECT_ID,
                ROOT_ID,
                "Deleted.md",
                1,
                5,
                ObjectLifecycle::Live,
            ),
        ],
    );
    let tombstone_next = coverage_catalog(
        2,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(THIRD_OBJECT_ID, Some(ROOT_ID), "Trash"),
            coverage_object(
                FOURTH_OBJECT_ID,
                THIRD_OBJECT_ID,
                "Deleted.md",
                1,
                5,
                ObjectLifecycle::tombstone(OpaqueId::parse(THIRD_OBJECT_ID).unwrap(), 1).unwrap(),
            ),
        ],
    );

    let parent_current = coverage_catalog(
        1,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(FIRST_OBJECT_ID, Some(ROOT_ID), "Source"),
            coverage_folder(SECOND_OBJECT_ID, Some(ROOT_ID), "Destination"),
        ],
    );
    let parent_next = coverage_catalog(
        2,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(FIRST_OBJECT_ID, Some(SECOND_OBJECT_ID), "MovedSource"),
            coverage_folder(SECOND_OBJECT_ID, Some(ROOT_ID), "Destination"),
        ],
    );

    let subtree_current = coverage_catalog(1, vec![coverage_folder(ROOT_ID, None, "Core")]);
    let subtree_next = coverage_catalog(
        2,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(FIRST_OBJECT_ID, Some(ROOT_ID), "NewFolder"),
            coverage_object(
                SECOND_OBJECT_ID,
                FIRST_OBJECT_ID,
                "Nested.md",
                1,
                6,
                ObjectLifecycle::Live,
            ),
        ],
    );

    let boundary_current = coverage_catalog(
        1,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(FIFTH_OBJECT_ID, Some(ROOT_ID), "High"),
        ],
    );
    let boundary_next = coverage_catalog(
        2,
        vec![
            coverage_folder(ROOT_ID, None, "Core"),
            coverage_folder(FIRST_OBJECT_ID, Some(ROOT_ID), "Low"),
        ],
    );

    let stale_catalog = changed.clone();
    let stale_source = source_precondition(&stale_catalog, FIRST_OBJECT_ID);
    let duplicate_destination = destination_precondition(&created_current, ROOT_ID, "Created.md");

    let cases = vec![
        Case {
            name: "unchanged",
            current: unchanged.clone(),
            next: unchanged.clone(),
            preconditions: vec![],
        },
        Case {
            name: "content change",
            current: unchanged.clone(),
            next: changed.clone(),
            preconditions: vec![changed_source.clone()],
        },
        Case {
            name: "rename and move",
            current: moved_current.clone(),
            next: moved_next,
            preconditions: vec![
                source_precondition(&moved_current, FOURTH_OBJECT_ID),
                destination_precondition(&moved_current, SECOND_OBJECT_ID, "Moved.md"),
            ],
        },
        Case {
            name: "create",
            current: created_current.clone(),
            next: created_next.clone(),
            preconditions: vec![destination_precondition(
                &created_current,
                ROOT_ID,
                "Created.md",
            )],
        },
        Case {
            name: "delete",
            current: deleted_current.clone(),
            next: deleted_next,
            preconditions: vec![source_precondition(&deleted_current, FIRST_OBJECT_ID)],
        },
        Case {
            name: "tombstone",
            current: tombstone_current.clone(),
            next: tombstone_next,
            preconditions: vec![
                source_precondition(&tombstone_current, FOURTH_OBJECT_ID),
                destination_precondition(&tombstone_current, THIRD_OBJECT_ID, "Deleted.md"),
            ],
        },
        Case {
            name: "source parent change",
            current: parent_current.clone(),
            next: parent_next,
            preconditions: vec![
                source_precondition(&parent_current, FIRST_OBJECT_ID),
                destination_precondition(&parent_current, SECOND_OBJECT_ID, "MovedSource"),
            ],
        },
        Case {
            name: "new subtree only needs its existing parent",
            current: subtree_current.clone(),
            next: subtree_next,
            preconditions: vec![destination_precondition(
                &subtree_current,
                ROOT_ID,
                "NewFolder",
            )],
        },
        Case {
            name: "duplicate source preconditions",
            current: unchanged.clone(),
            next: changed.clone(),
            preconditions: vec![changed_source.clone(), changed_source],
        },
        Case {
            name: "duplicate destination preconditions",
            current: created_current.clone(),
            next: created_next.clone(),
            preconditions: vec![duplicate_destination.clone(), duplicate_destination],
        },
        Case {
            name: "stale revision still covers the source at the coverage layer",
            current: unchanged.clone(),
            next: changed,
            preconditions: vec![stale_source],
        },
        Case {
            name: "missing source",
            current: unchanged,
            next: coverage_catalog(2, vec![coverage_folder(ROOT_ID, None, "Core")]),
            preconditions: vec![],
        },
        Case {
            name: "missing destination under an existing parent",
            current: created_current.clone(),
            next: created_next,
            preconditions: vec![],
        },
        Case {
            name: "stable ID boundary preserves source-before-destination error precedence",
            current: boundary_current.clone(),
            next: boundary_next.clone(),
            preconditions: vec![],
        },
        Case {
            name: "stable ID boundary ordering succeeds with both preconditions",
            current: boundary_current.clone(),
            next: boundary_next,
            preconditions: vec![
                source_precondition(&boundary_current, FIFTH_OBJECT_ID),
                destination_precondition(&boundary_current, ROOT_ID, "Low"),
            ],
        },
    ];

    for case in cases {
        let expected = coverage_outcome(reference_precondition_coverage(
            &case.current,
            &case.next,
            &case.preconditions,
        ));
        let actual = coverage_outcome(super::validate_precondition_coverage_ordered(
            &case.current,
            &case.next,
            &case.preconditions,
        ));
        assert_eq!(actual, expected, "{}", case.name);
    }
}

#[test]
fn same_version_different_catalog_or_object_wrap_material_misses() {
    let first = keys(0x11, 1);
    let second = keys(0x22, 1);
    let pointers = PointerSet {
        head: Some(head(&first, 1)),
        receipt: None,
        complete: None,
    };
    let first_keyring = FrkKeyring::new([&first]).unwrap();
    let second_keyring = FrkKeyring::new([&second]).unwrap();
    let exact = lookup_key(pointers.clone(), &first_keyring, &first);
    let wrong_catalog = lookup_key(pointers.clone(), &second_keyring, &first);
    let wrong_object_wrap = lookup_key(pointers, &first_keyring, &second);
    let cached = snapshot(&exact, 1);
    let cache = CommitCache::default();
    cache.replace(Arc::clone(&cached));

    assert!(cache.get(&wrong_catalog).is_none());
    assert!(cache.get(&wrong_object_wrap).is_none());
}

#[test]
fn exact_pointer_and_key_identity_returns_the_arc_snapshot() {
    let first = keys(0x11, 1);
    let second = keys(0x22, 2);
    let first_head = head(&first, 1);
    let second_head = head(&second, 2);
    let pointers = PointerSet {
        head: Some(second_head.clone()),
        receipt: Some(first_head.clone()),
        complete: Some(second_head),
    };
    let keyring = FrkKeyring::new([&second, &first]).unwrap();
    let exact = lookup_key(pointers.clone(), &keyring, &second);
    assert_eq!(exact.required_catalog_versions(), &[1, 2]);

    let cached = snapshot(&exact, 2);
    let cache = CommitCache::default();
    cache.replace(Arc::clone(&cached));

    let hit = cache.get(&exact).expect("exact cache key should hit");
    assert!(Arc::ptr_eq(&hit, &cached));

    let pointer_mismatch = lookup_key(
        PointerSet {
            head: pointers.head.clone(),
            receipt: None,
            complete: pointers.complete.clone(),
        },
        &keyring,
        &second,
    );
    assert!(cache.get(&pointer_mismatch).is_none());

    let other_core = CacheLookupKey::derive(pointers, "other-core", &keyring, &second).unwrap();
    assert!(cache.get(&other_core).is_none());
}

#[test]
fn poisoned_cache_is_cleared_and_treated_as_a_miss() {
    let keys = keys(0x11, 1);
    let keyring = FrkKeyring::new([&keys]).unwrap();
    let key = lookup_key(
        PointerSet {
            head: Some(head(&keys, 1)),
            receipt: None,
            complete: None,
        },
        &keyring,
        &keys,
    );
    let stale = snapshot(&key, 1);
    let replacement = snapshot(&key, 1);
    let cache = Arc::new(CommitCache::default());
    cache.replace(stale);

    let poisoner = Arc::clone(&cache);
    assert!(thread::spawn(move || {
        let _guard = poisoner.inner.lock().unwrap();
        panic!("poison the cache mutex");
    })
    .join()
    .is_err());

    assert!(cache.get(&key).is_none());
    cache.replace(Arc::clone(&replacement));
    let hit = cache.get(&key).expect("cache should remain usable");
    assert!(Arc::ptr_eq(&hit, &replacement));
    cache.clear();
    assert!(cache.get(&key).is_none());
}

#[test]
fn cache_guard_is_released_before_external_probe() {
    let keys = keys(0x11, 1);
    let keyring = FrkKeyring::new([&keys]).unwrap();
    let key = lookup_key(
        PointerSet {
            head: Some(head(&keys, 1)),
            receipt: None,
            complete: None,
        },
        &keyring,
        &keys,
    );
    let cache = CommitCache::default();
    cache.replace(snapshot(&key, 1));
    assert!(cache.inner.try_lock().is_ok());
    assert!(cache.get(&key).is_some());
    assert!(cache.inner.try_lock().is_ok());
    cache.clear();
    assert!(cache.inner.try_lock().is_ok());

    let root =
        std::env::temp_dir().join(format!("anima-corefs-cache-field-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    assert!(coordinator.cache.inner.try_lock().is_ok());
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn unlocked_second_head_change_discards_the_candidate_hit() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-second-head-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    assert_eq!(
        coordinator
            .load_committed(&keys)
            .unwrap()
            .unwrap()
            .head()
            .generation(),
        2
    );
    let keyring = FrkKeyring::single(&keys);
    let candidate = coordinator
        .cache
        .inner
        .lock()
        .unwrap()
        .as_ref()
        .cloned()
        .expect("the warmed exact snapshot should be a candidate");
    assert_eq!(candidate.catalog().generation(), 2);

    let committed = coordinator
        .load_committed_with_keyring_observation_hook(&keyring, || {
            let other = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
            other
                .commit(
                    &keys,
                    &[],
                    &[],
                    |_, generation| Ok((*catalog(generation)).clone()),
                    |_| Ok(()),
                )
                .unwrap();
        })
        .unwrap()
        .unwrap();

    assert_eq!(committed.head().generation(), 3);
    assert_eq!(committed.catalog().generation(), 3);
    assert!(!std::ptr::eq(
        committed.catalog(),
        candidate.catalog().as_ref()
    ));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn unlocked_exact_hit_reauthenticates_catalog_bytes_without_crypto() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-unlocked-hit-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    coordinator
        .load_committed_with_probe(&keys, &mut CatalogLoadProbe::default())
        .unwrap()
        .unwrap();
    let mut probe = CatalogLoadProbe::default();

    let committed = coordinator
        .load_committed_with_probe(&keys, &mut probe)
        .unwrap()
        .unwrap();

    assert_eq!(committed.head().generation(), 2);
    assert_eq!(probe.pointer_reads, 4);
    assert_eq!(probe.catalog_file_reads, 1);
    assert_eq!(probe.catalog_decrypts, 0);
    assert_eq!(probe.catalog_encodes, 0);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn unlocked_cache_hit_rejects_missing_catalog_bytes() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-unlocked-missing-catalog-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    coordinator.load_committed(&keys).unwrap().unwrap();
    std::fs::remove_file(authoritative_catalog_path(&coordinator)).unwrap();

    assert!(matches!(
        coordinator.load_committed(&keys),
        Err(CommitError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound
    ));

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn unlocked_cache_hit_rejects_changed_catalog_bytes() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-unlocked-changed-catalog-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    coordinator.load_committed(&keys).unwrap().unwrap();
    let catalog_path = authoritative_catalog_path(&coordinator);
    let mut changed = std::fs::read(&catalog_path).unwrap();
    changed[0] ^= 0xff;
    std::fs::write(catalog_path, changed).unwrap();

    assert!(matches!(
        coordinator.load_committed(&keys),
        Err(CommitError::Head(HeadError::CatalogMismatch("hash")))
    ));

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn unlocked_cache_hit_rejects_missing_retained_cutover_catalog_bytes() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-unlocked-missing-retained-catalog-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    coordinator
        .commit(
            &keys,
            &[],
            &[],
            |_, generation| Ok((*catalog(generation)).clone()),
            |_| Ok(()),
        )
        .unwrap();
    let head_catalog = authoritative_catalog_path(&coordinator);
    let retained_catalog = pointer_catalog_path(&coordinator, super::CUTOVER_RECEIPT_FILE);
    assert_ne!(head_catalog, retained_catalog);
    let mut probe = CatalogLoadProbe::default();
    coordinator
        .load_committed_with_probe(&keys, &mut probe)
        .unwrap()
        .unwrap();
    assert_eq!(probe.catalog_file_reads, 2);
    assert_eq!(probe.catalog_decrypts, 0);
    assert_eq!(probe.catalog_encodes, 0);
    std::fs::remove_file(retained_catalog).unwrap();

    assert!(matches!(
        coordinator.load_committed(&keys),
        Err(CommitError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound
    ));

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn commit_cache_hit_rejects_missing_catalog_bytes_before_build() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-commit-missing-catalog-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    coordinator.load_committed(&keys).unwrap().unwrap();
    std::fs::remove_file(authoritative_catalog_path(&coordinator)).unwrap();
    let build_called = Cell::new(false);

    let result = coordinator.commit(
        &keys,
        &[],
        &[],
        |_, generation| {
            build_called.set(true);
            Ok((*catalog(generation)).clone())
        },
        |_| Ok(()),
    );

    assert!(matches!(
        result,
        Err(CommitError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound
    ));
    assert!(!build_called.get());

    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn unlocked_load_holds_no_cache_guard_during_pointer_io_or_crypto() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-unlocked-guard-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let keys = keys(0x11, 1);
    let seeder = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    seed_committed(&seeder, &keys);
    drop(seeder);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let mut stages = Vec::new();
    let mut assert_cache_free = |stage| {
        assert!(
            coordinator.cache.inner.try_lock().is_ok(),
            "cache mutex held during {stage:?}"
        );
        stages.push(stage);
    };
    let mut probe = CatalogLoadProbe::observed(&mut assert_cache_free);

    coordinator
        .load_committed_with_probe(&keys, &mut probe)
        .unwrap()
        .unwrap();

    assert!(stages.contains(&CatalogLoadStage::PointerIo));
    assert!(stages.contains(&CatalogLoadStage::KeyDerivation));
    assert!(stages.contains(&CatalogLoadStage::CacheAccess));
    assert!(stages.contains(&CatalogLoadStage::CatalogFileIo));
    assert!(stages.contains(&CatalogLoadStage::CatalogCrypto));
    assert!(stages.contains(&CatalogLoadStage::SecondHeadRead));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn locked_exact_hit_reauthenticates_catalog_bytes_without_crypto() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-locked-hit-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    coordinator.load_committed(&keys).unwrap().unwrap();
    let mut probe = CatalogLoadProbe::default();

    let committed = coordinator
        .load_committed_locked_with_probe(&FrkKeyring::single(&keys), &mut probe)
        .unwrap()
        .unwrap();

    assert_eq!(committed.head().generation(), 2);
    assert_eq!(probe.pointer_reads, 3);
    assert_eq!(probe.catalog_file_reads, 1);
    assert_eq!(probe.catalog_decrypts, 0);
    assert_eq!(probe.catalog_encodes, 0);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn locked_load_acquires_kernel_lock_before_cache_and_releases_cache_before_io() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-locked-order-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let keys = keys(0x11, 1);
    let seeder = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    seed_committed(&seeder, &keys);
    drop(seeder);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let mut stages = Vec::new();
    let mut assert_cache_free = |stage| {
        assert!(
            coordinator.cache.inner.try_lock().is_ok(),
            "cache mutex held during {stage:?}"
        );
        stages.push(stage);
    };
    let mut probe = CatalogLoadProbe::observed(&mut assert_cache_free);

    coordinator
        .load_committed_locked_with_probe(&FrkKeyring::single(&keys), &mut probe)
        .unwrap()
        .unwrap();

    assert_eq!(stages.first(), Some(&CatalogLoadStage::KernelLock));
    let last_pointer = stages
        .iter()
        .rposition(|stage| *stage == CatalogLoadStage::PointerIo)
        .unwrap();
    let key_derivation = stages
        .iter()
        .position(|stage| *stage == CatalogLoadStage::KeyDerivation)
        .unwrap();
    let cache_access = stages
        .iter()
        .position(|stage| *stage == CatalogLoadStage::CacheAccess)
        .unwrap();
    let catalog_file_io = stages
        .iter()
        .position(|stage| *stage == CatalogLoadStage::CatalogFileIo)
        .unwrap();
    let catalog_crypto = stages
        .iter()
        .position(|stage| *stage == CatalogLoadStage::CatalogCrypto)
        .unwrap();
    assert!(last_pointer < key_derivation);
    assert!(key_derivation < cache_access);
    assert!(cache_access < catalog_file_io);
    assert!(catalog_file_io < catalog_crypto);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn same_coordinator_commit_reuses_only_the_exact_authenticated_head() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-same-coordinator-commit-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
    let mut probe = CommitProbe::default();

    let outcome = coordinator
        .commit_with_probe(
            &keys,
            &[],
            &[],
            |_, generation| Ok((*catalog(generation)).clone()),
            |_| Ok(()),
            &mut probe,
        )
        .unwrap();

    assert_eq!(outcome.generation(), 3);
    assert_eq!(probe.pointer_reads, 6);
    assert_eq!(probe.catalog_file_reads, 1);
    assert_eq!(probe.catalog_decrypts, 0);
    assert_eq!(probe.catalog_encodes, 0);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn commit_holds_no_cache_guard_during_kernel_lock_io_crypto_build_hooks_or_invalidation() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-commit-guard-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let seeder = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&seeder, &keys);
    drop(seeder);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let mut stages = Vec::new();
    let mut observe_stage = |stage| {
        if matches!(
            stage,
            CommitStage::KernelLock
                | CommitStage::PointerIo
                | CommitStage::KeyDerivation
                | CommitStage::CatalogIoCrypto
        ) {
            assert!(
                coordinator.cache.inner.try_lock().is_ok(),
                "cache mutex held at immediate internal stage {stage:?}"
            );
        }
        stages.push(stage);
    };
    let mut probe = CommitProbe::observed(&mut observe_stage);
    let keyring = FrkKeyring::single(&keys);
    let mut hook = |_| {
        assert!(
            coordinator.cache.inner.try_lock().is_ok(),
            "cache mutex held inside publication/failure hook"
        );
        Ok(())
    };

    let outcome = coordinator
        .commit_internal_with_keyring_and_hook(
            &keyring,
            &keys,
            &[],
            &[],
            CommitMode::Normal,
            |_, generation| {
                assert!(
                    coordinator.cache.inner.try_lock().is_ok(),
                    "cache mutex held inside catalog build callback"
                );
                Ok((*catalog(generation)).clone())
            },
            CommitCallbacks {
                invalidate: |_| {
                    assert!(
                        coordinator.cache.inner.try_lock().is_ok(),
                        "cache mutex held inside invalidation callback"
                    );
                    Err("runtime index is offline".to_owned())
                },
                hook: &mut hook,
            },
            Some(&mut probe),
        )
        .unwrap();

    assert!(!outcome.invalidation_delivered());
    assert_eq!(
        coordinator
            .cache
            .current()
            .expect("durable authority must survive invalidation failure")
            .catalog()
            .generation(),
        3
    );

    for expected in [
        CommitStage::KernelLock,
        CommitStage::PointerIo,
        CommitStage::KeyDerivation,
        CommitStage::CatalogIoCrypto,
        CommitStage::PreconditionAndBuild,
        CommitStage::EncryptionAndPublication,
        CommitStage::FailureHook,
        CommitStage::InvalidationCallback,
    ] {
        assert!(
            stages.contains(&expected),
            "missing commit stage {expected:?}"
        );
    }
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn recovery_holds_no_cache_guard_during_lock_io_crypto_or_hooks() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-recovery-guard-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    coordinator
        .initialize_validation_snapshot(&keys, &[], |generation| Ok((*catalog(generation)).clone()))
        .unwrap();
    let outcome = coordinator
        .commit_internal_with_hook(
            &keys,
            &[],
            &[],
            CommitMode::FirstMutation { cutover_epoch: 1 },
            |_, generation| Ok((*catalog(generation)).clone()),
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

    let stages = std::cell::RefCell::new(Vec::new());
    let commit_lock = CoreCommitLock::acquire_in_with_post_kernel_lock_hook(
        &coordinator.root_dir,
        &coordinator.fs_dir,
        || {
            assert!(coordinator.cache.inner.try_lock().is_ok());
            stages.borrow_mut().push(CatalogLoadStage::KernelLock);
        },
    )
    .unwrap();
    let mut observe_stage = |stage| {
        assert!(
            coordinator.cache.inner.try_lock().is_ok(),
            "cache mutex held during recovery stage {stage:?}"
        );
        stages.borrow_mut().push(stage);
    };
    let mut probe = CatalogLoadProbe::observed(&mut observe_stage);
    let committed = coordinator
        .load_committed_recovering_with_keyring_and_hook_inner(
            &commit_lock,
            &FrkKeyring::single(&keys),
            &mut |_| {
                assert!(
                    coordinator.cache.inner.try_lock().is_ok(),
                    "cache mutex held inside recovery publication hook"
                );
                Ok(())
            },
            Some(&mut probe),
        )
        .unwrap()
        .unwrap();
    drop(commit_lock);

    assert_eq!(committed.head().generation(), 2);
    assert!(
        probe.pointer_reads > 3,
        "recovery pointer I/O was not observed through the scoped probe"
    );
    assert!(
        probe.catalog_decrypts > 1,
        "recovery catalog crypto was not observed through the scoped probe"
    );
    assert!(stages.borrow().contains(&CatalogLoadStage::KernelLock));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn rotation_holds_no_cache_guard_during_lock_io_crypto_or_hooks() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-rotation-guard-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys(0x11, 1);
    let pending_keys = keys(0x22, 2);
    seed_committed(&coordinator, &old_keys);
    let keyring = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();
    let mut stages = Vec::new();
    let mut observe_stage = |stage| {
        assert!(
            coordinator.cache.inner.try_lock().is_ok(),
            "cache mutex held during rotation stage {stage:?}"
        );
        stages.push(stage);
    };
    let mut probe = RotationProbe::observed(&mut observe_stage);
    let mut hook = |_| {
        assert!(
            coordinator.cache.inner.try_lock().is_ok(),
            "cache mutex held inside rotation publication hook"
        );
        Ok(())
    };

    coordinator
        .rotate_frk_with_hook(
            &keyring,
            &pending_keys,
            2,
            |_| {
                assert!(
                    coordinator.cache.inner.try_lock().is_ok(),
                    "cache mutex held inside rotation invalidation callback"
                );
                Ok(())
            },
            &mut hook,
            Some(&mut probe),
        )
        .unwrap();

    for expected in [
        RotationStage::KernelLock,
        RotationStage::PointerIo,
        RotationStage::KeyDerivation,
        RotationStage::CatalogIoCrypto,
        RotationStage::ObjectRewrap,
        RotationStage::EncryptionAndPublication,
        RotationStage::FailureHook,
        RotationStage::InvalidationCallback,
    ] {
        assert!(
            stages.contains(&expected),
            "missing rotation stage {expected:?}"
        );
    }
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn rotation_rejects_replaced_retained_tuple_before_cache_publication() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-rotation-replaced-tuple-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let old_keys = keys(0x11, 1);
    let pending_keys = keys(0x22, 2);
    seed_committed(&coordinator, &old_keys);
    assert_eq!(
        coordinator.cache.current().unwrap().catalog().generation(),
        2
    );
    let keyring = FrkKeyring::new([&old_keys, &pending_keys]).unwrap();
    let mut pointer_reads = 0;
    let mut pointers_replaced = false;
    let mut replace_before_rotation_tuple_read = |stage| {
        if stage == RotationStage::PointerIo {
            pointer_reads += 1;
            if pointer_reads == 4 {
                std::fs::remove_file(coordinator.cutover_receipt_path()).unwrap();
                std::fs::copy(
                    coordinator.validation_head_path(),
                    coordinator.cutover_receipt_path(),
                )
                .unwrap();
                std::fs::remove_file(coordinator.cutover_complete_path()).unwrap();
                std::fs::copy(
                    coordinator.validation_head_path(),
                    coordinator.cutover_complete_path(),
                )
                .unwrap();
                pointers_replaced = true;
            }
        }
    };
    let result = {
        let mut probe = RotationProbe::observed(&mut replace_before_rotation_tuple_read);
        coordinator.rotate_frk_with_hook(
            &keyring,
            &pending_keys,
            2,
            |_| Ok(()),
            &mut |_| Ok(()),
            Some(&mut probe),
        )
    };

    assert!(pointers_replaced, "the replacement seam was not reached");
    assert!(
        matches!(
            &result,
            Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt)
        ),
        "rotation accepted retained pointers that were not authenticated with its catalog: {result:?}"
    );
    assert!(
        coordinator.cache.current().is_none(),
        "rotation cached authority under unauthenticated retained pointers"
    );
    assert!(matches!(
        coordinator.load_committed(&old_keys),
        Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt)
    ));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn empty_validated_object_state_is_buildable_and_searchable() {
    let first_id = OpaqueId::parse(FIRST_OBJECT_ID).unwrap();
    let empty = ValidatedObjectState::empty();
    assert!(empty.get(&first_id).is_none());

    let first = binding(FIRST_OBJECT_ID, 1);
    let second = binding(SECOND_OBJECT_ID, 2);
    let state = ValidatedObjectState::from_bindings(vec![second.clone(), first.clone()]).unwrap();
    assert_eq!(state.get(&first_id), Some(&first));
    assert_eq!(
        state.get(&OpaqueId::parse(SECOND_OBJECT_ID).unwrap()),
        Some(&second)
    );

    let error = ValidatedObjectState::from_bindings(vec![first.clone(), first]).unwrap_err();
    assert_eq!(
        error,
        CacheError::DuplicateObjectId {
            object_id: FIRST_OBJECT_ID.to_owned()
        }
    );
}

#[test]
fn exact_cached_object_tuple_skips_repeated_dek_unwrap() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-object-binding-hit-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    let prepared = seed_committed_object(&coordinator, &keys);
    let mut probe = CommitProbe::default();

    coordinator
        .commit_with_probe(
            &keys,
            &[],
            &[],
            |_, generation| {
                Ok(cached_object_catalog(
                    generation,
                    &prepared,
                    prepared.wrapped_dek().clone(),
                ))
            },
            |_| Ok(()),
            &mut probe,
        )
        .unwrap();

    assert_eq!(probe.object_dek_unwraps, 0);
    assert!(coordinator
        .cache
        .current()
        .unwrap()
        .objects
        .as_ref()
        .is_some_and(|objects| objects
            .get(&OpaqueId::parse(FIRST_OBJECT_ID).unwrap())
            .is_some()));
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn first_mutation_after_all_authoritative_pointers_disappear_never_reuses_stale_object_bindings() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-stale-first-mutation-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    let prepared = seed_committed_object(&coordinator, &keys);
    assert!(coordinator.cache.current().unwrap().objects.is_some());
    std::fs::remove_file(&coordinator.head_path).unwrap();
    std::fs::remove_file(&coordinator.cutover_receipt_path).unwrap();
    std::fs::remove_file(&coordinator.cutover_complete_path).unwrap();
    assert!(coordinator.validation_head_path.exists());

    let keyring = FrkKeyring::single(&keys);
    let mut probe = CommitProbe::default();
    let mut hook = |_| Ok(());
    coordinator
        .commit_internal_with_keyring_and_hook(
            &keyring,
            &keys,
            &[],
            &[],
            CommitMode::FirstMutation { cutover_epoch: 1 },
            |_, generation| {
                Ok(cached_object_catalog(
                    generation,
                    &prepared,
                    prepared.wrapped_dek().clone(),
                ))
            },
            CommitCallbacks {
                invalidate: |_| Ok(()),
                hook: &mut hook,
            },
            Some(&mut probe),
        )
        .unwrap();

    assert_eq!(probe.object_dek_unwraps, 1);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn cache_hit_rejects_opened_linked_identity_mismatch() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-opened-linked-mismatch-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let object_path = root.join("object.acore");
    let detached_path = root.join("detached-object.acore");
    std::fs::write(&object_path, b"original object").unwrap();
    let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
    let opened = dir.open("object.acore").unwrap().into_std();
    std::fs::rename(&object_path, detached_path).unwrap();
    std::fs::write(&object_path, b"replacement object").unwrap();

    assert!(matches!(
        validate_opened_regular_file(&dir, OsStr::new("object.acore"), &opened),
        Err(CommitError::InvalidCoreLayout)
    ));

    drop(opened);
    drop(dir);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn changed_wrapped_dek_never_reuses_binding() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-changed-wrapper-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    let prepared = seed_committed_object(&coordinator, &keys);
    assert!(coordinator.cache.current().unwrap().objects.is_some());
    let precondition = object_precondition(&coordinator, &keys);
    let record = prepared.wrapped_dek();
    let wrapped = record.to_wrapped_object_dek().unwrap();
    let mut ciphertext = wrapped.ciphertext().to_vec();
    ciphertext[0] ^= 0x80;
    let changed = WrappedObjectDekRecord::from_parts(
        record.frk_version(),
        record.object_key_epoch(),
        wrapped.algorithm(),
        wrapped.envelope_version(),
        wrapped.nonce(),
        ciphertext,
    )
    .unwrap();
    let mut probe = CommitProbe::default();

    let result = coordinator.commit_with_probe(
        &keys,
        &[],
        &[precondition],
        |_, generation| Ok(cached_object_catalog(generation, &prepared, changed)),
        |_| Ok(()),
        &mut probe,
    );

    assert!(matches!(result, Err(CommitError::Crypto(_))));
    assert_eq!(probe.object_dek_unwraps, 1);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn wrong_object_wrap_key_identity_never_reuses_binding() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-wrong-object-wrap-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let first = keys(0x11, 1);
    let other_material = keys(0x22, 1);
    let prepared = seed_committed_object(&coordinator, &first);
    let snapshot = coordinator.cache.current().unwrap();
    assert!(snapshot.objects.is_some());
    let exact_keyring = FrkKeyring::single(&first);
    let wrong = lookup_key(snapshot.pointers.clone(), &exact_keyring, &other_material);
    assert!(coordinator.cache.get(&wrong).is_none());

    let mut probe = CommitProbe::default();
    let mut hook = |_| Ok(());
    let result = coordinator.commit_internal_with_keyring_and_hook(
        &exact_keyring,
        &other_material,
        &[],
        &[],
        CommitMode::Normal,
        |_, generation| {
            Ok(cached_object_catalog(
                generation,
                &prepared,
                prepared.wrapped_dek().clone(),
            ))
        },
        CommitCallbacks {
            invalidate: |_| Ok(()),
            hook: &mut hook,
        },
        Some(&mut probe),
    );

    assert!(matches!(result, Err(CommitError::Crypto(_))));
    assert_eq!(probe.object_dek_unwraps, 1);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn changed_object_key_epoch_never_reuses_binding() {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-cache-changed-object-epoch-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    let prepared = seed_committed_object(&coordinator, &keys);
    assert!(coordinator.cache.current().unwrap().objects.is_some());
    let precondition = object_precondition(&coordinator, &keys);
    let record = prepared.wrapped_dek();
    let wrapped = record.to_wrapped_object_dek().unwrap();
    let changed_epoch = WrappedObjectDekRecord::from_parts(
        record.frk_version(),
        record.object_key_epoch() + 1,
        wrapped.algorithm(),
        wrapped.envelope_version(),
        wrapped.nonce(),
        wrapped.ciphertext().to_vec(),
    )
    .unwrap();
    let mut probe = CommitProbe::default();

    let result = coordinator.commit_with_probe(
        &keys,
        &[],
        &[precondition],
        |_, generation| Ok(cached_object_catalog(generation, &prepared, changed_epoch)),
        |_| Ok(()),
        &mut probe,
    );

    assert!(matches!(result, Err(CommitError::Crypto(_))));
    assert_eq!(probe.object_dek_unwraps, 1);
    drop(coordinator);
    std::fs::remove_dir_all(root).unwrap();
}

mod lease_candidate_tests {
    use std::collections::VecDeque;
    use std::fmt;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::publication::PublicationPhase;
    use crate::transaction::object_lease::{
        FenceOutcome, LeaseAttemptDecision, LeaseBudget, LeaseMonitorResource, LeasePermitBundle,
        LeaseResourceFactory, LeaseResourcePlan, MonitorStateCell, ValidationAnchor,
        MAX_OBJECT_LEASE_ENTRIES,
    };

    #[derive(Debug)]
    struct CandidateMonitor {
        outcomes: Arc<Mutex<VecDeque<FenceOutcome>>>,
        fence_attempts: Arc<AtomicUsize>,
        drops: Arc<AtomicUsize>,
    }

    impl LeaseMonitorResource for CandidateMonitor {
        fn fence(&self) -> FenceOutcome {
            self.fence_attempts.fetch_add(1, Ordering::SeqCst);
            self.outcomes
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .pop_front()
                .unwrap_or(FenceOutcome::Clean)
        }
    }

    impl Drop for CandidateMonitor {
        fn drop(&mut self) {
            self.drops.fetch_add(1, Ordering::SeqCst);
        }
    }

    struct CandidateFactory {
        supported: bool,
        fail_anchor_at: Option<usize>,
        outcomes: Arc<Mutex<VecDeque<FenceOutcome>>>,
        plan_attempts: Arc<AtomicUsize>,
        monitor_attempts: Arc<AtomicUsize>,
        fence_attempts: Arc<AtomicUsize>,
        anchor_attempts: Arc<AtomicUsize>,
        monitor_drops: Arc<AtomicUsize>,
        deny_retry_with_budget: Option<LeaseBudget>,
        held_competitor: Arc<Mutex<Option<LeasePermitBundle>>>,
    }

    impl fmt::Debug for CandidateFactory {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("CandidateFactory")
                .field("supported", &self.supported)
                .field("fail_anchor_at", &self.fail_anchor_at)
                .finish_non_exhaustive()
        }
    }

    impl CandidateFactory {
        fn new(
            supported: bool,
            fail_anchor_at: Option<usize>,
            outcomes: impl IntoIterator<Item = FenceOutcome>,
        ) -> Arc<Self> {
            Arc::new(Self {
                supported,
                fail_anchor_at,
                outcomes: Arc::new(Mutex::new(outcomes.into_iter().collect())),
                plan_attempts: Arc::new(AtomicUsize::new(0)),
                monitor_attempts: Arc::new(AtomicUsize::new(0)),
                fence_attempts: Arc::new(AtomicUsize::new(0)),
                anchor_attempts: Arc::new(AtomicUsize::new(0)),
                monitor_drops: Arc::new(AtomicUsize::new(0)),
                deny_retry_with_budget: None,
                held_competitor: Arc::new(Mutex::new(None)),
            })
        }

        fn supported(outcomes: impl IntoIterator<Item = FenceOutcome>) -> Arc<Self> {
            Self::new(true, None, outcomes)
        }

        fn unsupported() -> Arc<Self> {
            Self::new(false, None, [])
        }

        fn failing_anchor(index: usize) -> Arc<Self> {
            Self::new(true, Some(index), [])
        }

        fn denying_retry(budget: LeaseBudget) -> Arc<Self> {
            let mut factory = Arc::try_unwrap(Self::supported([FenceOutcome::DirtyAll]))
                .expect("new candidate factory has one owner");
            factory.deny_retry_with_budget = Some(budget);
            Arc::new(factory)
        }

        fn release_competitor(&self) {
            let competitor = self
                .held_competitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .take();
            drop(competitor);
        }
    }

    impl LeaseResourceFactory for CandidateFactory {
        fn resource_plan(&self) -> LeaseResourcePlan {
            let attempt = self.plan_attempts.fetch_add(1, Ordering::SeqCst);
            if attempt == 1 {
                if let Some(budget) = &self.deny_retry_with_budget {
                    let permits = budget
                        .try_reserve_exact(MAX_OBJECT_LEASE_ENTRIES, 0)
                        .expect("retry contention must fill the released entry budget");
                    *self
                        .held_competitor
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(permits);
                }
            }
            if self.supported {
                LeaseResourcePlan::supported(1)
            } else {
                LeaseResourcePlan::unsupported()
            }
        }

        fn create_monitor(
            &self,
            _plan: LeaseResourcePlan,
            _state: Arc<MonitorStateCell>,
        ) -> Result<Box<dyn LeaseMonitorResource>, ()> {
            self.monitor_attempts.fetch_add(1, Ordering::SeqCst);
            Ok(Box::new(CandidateMonitor {
                outcomes: Arc::clone(&self.outcomes),
                fence_attempts: Arc::clone(&self.fence_attempts),
                drops: Arc::clone(&self.monitor_drops),
            }))
        }

        fn create_anchor(
            &self,
            _index: usize,
            _binding: &ValidatedObjectBinding,
        ) -> Result<ValidationAnchor, ()> {
            panic!("commit candidate attempted to construct an anchor without its validated file")
        }

        fn create_anchor_from_validated_file(
            &self,
            index: usize,
            _binding: &ValidatedObjectBinding,
            file: std::fs::File,
        ) -> Result<ValidationAnchor, ()> {
            assert!(file.metadata().unwrap().len() > 0);
            self.anchor_attempts.fetch_add(1, Ordering::SeqCst);
            if self.fail_anchor_at == Some(index) {
                Err(())
            } else {
                Ok(ValidationAnchor::test(index as u64))
            }
        }
    }

    fn setup(
        label: &str,
    ) -> (
        PathBuf,
        CoreCommitCoordinator,
        FrkSubkeys,
        PreparedObjectRevision,
    ) {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-lease-candidate-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
        let keys = keys(0x51, 1);
        let prepared = seed_committed_object(&coordinator, &keys);
        (root, coordinator, keys, prepared)
    }

    fn prepare_second_object(
        coordinator: &CoreCommitCoordinator,
        keys: &FrkSubkeys,
    ) -> PreparedObjectRevision {
        let object_key = SecretBytes::new(vec![0x72; 32]).unwrap();
        let aad = ObjectBaseAad::new(
            CORE_ID,
            SECOND_OBJECT_ID,
            ObjectKind::Note,
            ENVELOPE_VERSION,
            1,
            1,
        )
        .unwrap();
        let body = b"second cached object body";
        let metadata = EnvelopeMetadata::for_body(
            ObjectKind::Note.as_str(),
            SECOND_OBJECT_ID,
            1,
            "2026-07-23T00:00:00Z",
            "2026-07-23T00:00:00Z",
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

    fn two_object_catalog(
        generation: u64,
        first: &PreparedObjectRevision,
        second: &PreparedObjectRevision,
    ) -> CatalogGeneration {
        let object_entry = |stable_id: &str, name: &str, prepared: &PreparedObjectRevision| {
            CatalogGenerationEntry::object(
                CatalogEntryCommon::new(
                    OpaqueId::parse(stable_id).unwrap(),
                    Some(OpaqueId::parse(ROOT_ID).unwrap()),
                    PortableName::parse(name).unwrap(),
                    FolderOwner::User,
                    AnimaAccess::Write,
                ),
                CatalogObject::new(
                    prepared.revision(),
                    prepared.physical_name().clone(),
                    prepared.content_hash().clone(),
                    ObjectKind::Note,
                    prepared.wrapped_dek().clone(),
                    ObjectLifecycle::Live,
                )
                .unwrap(),
            )
        };
        CatalogGeneration::new(
            generation,
            vec![
                CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                    OpaqueId::parse(ROOT_ID).unwrap(),
                    None,
                    PortableName::parse("Core").unwrap(),
                    FolderOwner::User,
                    AnimaAccess::Write,
                )),
                object_entry(FIRST_OBJECT_ID, "First.md", first),
                object_entry(SECOND_OBJECT_ID, "Second.md", second),
            ],
        )
        .unwrap()
    }

    fn setup_two_objects(
        label: &str,
    ) -> (
        PathBuf,
        CoreCommitCoordinator,
        FrkSubkeys,
        PreparedObjectRevision,
        PreparedObjectRevision,
    ) {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-lease-candidate-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
        let keys = keys(0x52, 1);
        let first = prepare_cached_object(&coordinator, &keys);
        let second = prepare_second_object(&coordinator, &keys);
        coordinator
            .initialize_validation_snapshot(&keys, &[first.clone(), second.clone()], |generation| {
                Ok(two_object_catalog(generation, &first, &second))
            })
            .unwrap();
        coordinator
            .commit_first_mutation(
                &keys,
                1,
                &[],
                &[],
                |_, generation| Ok(two_object_catalog(generation, &first, &second)),
                |_| Ok(()),
            )
            .unwrap();
        (root, coordinator, keys, first, second)
    }

    fn commit_unchanged(
        coordinator: &CoreCommitCoordinator,
        keys: &FrkSubkeys,
        prepared: &PreparedObjectRevision,
        probe: &mut CommitProbe<'_>,
    ) {
        coordinator
            .commit_with_probe(
                keys,
                &[],
                &[],
                |_, generation| {
                    Ok(cached_object_catalog(
                        generation,
                        prepared,
                        prepared.wrapped_dek().clone(),
                    ))
                },
                |_| Ok(()),
                probe,
            )
            .unwrap();
    }

    #[test]
    fn candidate_monitor_arms_before_initial_object_scan() {
        let (root, coordinator, keys, prepared) = setup("monitor-before-scan");
        let factory = CandidateFactory::supported([]);
        coordinator.set_lease_factory_for_test(factory);
        let mut stages = Vec::new();
        let mut observer = |stage| stages.push(stage);
        let mut probe = CommitProbe::observed(&mut observer);

        commit_unchanged(&coordinator, &keys, &prepared, &mut probe);

        let monitor = stages
            .iter()
            .position(|stage| *stage == CommitStage::LeaseMonitorArmed)
            .expect("candidate monitor was never armed");
        let scan = stages
            .iter()
            .position(|stage| *stage == CommitStage::LeaseObjectValidated)
            .expect("candidate safe-open scan was never observed");
        assert!(monitor < scan);
        let lease = coordinator
            .cache
            .current()
            .unwrap()
            .object_lease
            .clone()
            .expect("durable snapshot omitted its clean candidate");
        assert_eq!(lease.object_tuple().len(), 1);
        assert_ne!(
            lease.directory_identity(),
            crate::transaction::object_lease::DirectoryIdentity::default()
        );
        assert!(lease.monitor_generation() > 0);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn candidate_revalidates_layout_after_monitor_start() {
        let (root, coordinator, keys, prepared) = setup("layout-after-monitor");
        coordinator.set_lease_factory_for_test(CandidateFactory::supported([]));
        let mut stages = Vec::new();
        let mut observer = |stage| stages.push(stage);
        let mut probe = CommitProbe::observed(&mut observer);

        commit_unchanged(&coordinator, &keys, &prepared, &mut probe);

        let monitor = stages
            .iter()
            .position(|stage| *stage == CommitStage::LeaseMonitorArmed)
            .expect("candidate monitor was never armed");
        let layout = stages
            .iter()
            .position(|stage| *stage == CommitStage::LeaseLayoutRevalidated)
            .expect("layout was not revalidated after monitor start");
        let scan = stages
            .iter()
            .position(|stage| *stage == CommitStage::LeaseObjectValidated)
            .expect("safe-open scan was not observed");
        assert!(monitor < layout && layout < scan);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn candidate_fence_covers_the_complete_safe_open_scan() {
        let (root, coordinator, keys, prepared) = setup("fence-after-scan");
        let factory = CandidateFactory::supported([]);
        coordinator.set_lease_factory_for_test(factory.clone());
        let mut stages = Vec::new();
        let mut observer = |stage| stages.push(stage);
        let mut probe = CommitProbe::observed(&mut observer);

        commit_unchanged(&coordinator, &keys, &prepared, &mut probe);

        let scan = stages
            .iter()
            .rposition(|stage| *stage == CommitStage::LeaseObjectValidated)
            .expect("safe-open scan was not observed");
        let fence = stages
            .iter()
            .position(|stage| *stage == CommitStage::LeaseFence)
            .expect("candidate fence was not observed");
        assert!(scan < fence);
        assert_eq!(factory.fence_attempts.load(Ordering::SeqCst), 1);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn event_during_scan_retries_once_then_falls_back_without_lease() {
        let (root, coordinator, keys, prepared) = setup("scan-event-retry");
        let factory = CandidateFactory::supported([FenceOutcome::DirtyAll, FenceOutcome::DirtyAll]);
        coordinator.set_lease_factory_for_test(factory.clone());
        let mut probe = CommitProbe::default();

        commit_unchanged(&coordinator, &keys, &prepared, &mut probe);

        assert_eq!(factory.monitor_attempts.load(Ordering::SeqCst), 2);
        assert_eq!(factory.fence_attempts.load(Ordering::SeqCst), 2);
        assert_eq!(factory.anchor_attempts.load(Ordering::SeqCst), 2);
        assert!(coordinator.cache.current().unwrap().object_lease.is_none());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn candidate_retry_records_budget_denial_at_current_epoch() {
        let (root, coordinator, keys, prepared) = setup("retry-budget-epoch");
        let factory = CandidateFactory::denying_retry(coordinator.lease_budget.clone());
        coordinator.set_lease_factory_for_test(factory.clone());
        let mut probe = CommitProbe::default();

        commit_unchanged(&coordinator, &keys, &prepared, &mut probe);

        assert_eq!(factory.plan_attempts.load(Ordering::SeqCst), 2);
        assert_eq!(factory.monitor_attempts.load(Ordering::SeqCst), 1);
        assert!(coordinator.cache.current().unwrap().object_lease.is_none());
        let requested = crate::transaction::catalog_object_bindings(&cached_object_catalog(
            3,
            &prepared,
            prepared.wrapped_dek().clone(),
        ))
        .unwrap();
        let fingerprint = crate::transaction::object_lease::object_set_fingerprint(&requested);
        let denied_epoch = coordinator.lease_budget.epoch();
        assert_eq!(
            coordinator
                .lease_attempt_policy
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .decision(fingerprint, requested.len(), denied_epoch),
            LeaseAttemptDecision::BudgetDeniedSuppressed,
            "retry denial was recorded against a stale pre-drop budget epoch"
        );

        let mut second_probe = CommitProbe::default();
        commit_unchanged(&coordinator, &keys, &prepared, &mut second_probe);
        assert_eq!(
            factory.plan_attempts.load(Ordering::SeqCst),
            2,
            "unchanged commit retried a denial at the current epoch"
        );

        factory.release_competitor();
        let released_epoch = coordinator.lease_budget.epoch();
        assert_ne!(released_epoch, denied_epoch);
        let mut third_probe = CommitProbe::default();
        commit_unchanged(&coordinator, &keys, &prepared, &mut third_probe);
        assert_eq!(factory.plan_attempts.load(Ordering::SeqCst), 3);
        assert_eq!(factory.monitor_attempts.load(Ordering::SeqCst), 2);
        assert!(coordinator.cache.current().unwrap().object_lease.is_some());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn partial_anchor_failure_drops_monitor_anchors_and_permits() {
        let (root, coordinator, keys, first, second) = setup_two_objects("partial-anchor");
        let factory = CandidateFactory::failing_anchor(1);
        coordinator.set_lease_factory_for_test(factory.clone());
        let mut probe = CommitProbe::default();

        coordinator
            .commit_with_probe(
                &keys,
                &[],
                &[],
                |_, generation| Ok(two_object_catalog(generation, &first, &second)),
                |_| Ok(()),
                &mut probe,
            )
            .unwrap();

        assert_eq!(factory.anchor_attempts.load(Ordering::SeqCst), 2);
        assert_eq!(factory.monitor_drops.load(Ordering::SeqCst), 1);
        let usage = coordinator.lease_budget.usage();
        assert_eq!(usage.entries, 0);
        assert_eq!(usage.leases, 0);
        assert_eq!(usage.monitor_resources, 0);
        assert!(usage.epoch > 0);
        assert!(coordinator.cache.current().unwrap().object_lease.is_none());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn pre_head_failure_never_publishes_candidate_lease() {
        let (root, coordinator, keys, prepared) = setup("pre-head-failure");
        let factory = CandidateFactory::supported([]);
        coordinator.set_lease_factory_for_test(factory.clone());
        let mut hook = |point| {
            if point
                == (CommitFailurePoint::Publication {
                    target: PublicationTarget::AuthoritativeHead,
                    phase: PublicationPhase::TemporaryCreated,
                })
            {
                Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "injected pre-HEAD failure",
                ))
            } else {
                Ok(())
            }
        };

        let result = coordinator.commit_internal_with_hook(
            &keys,
            &[],
            &[],
            CommitMode::Normal,
            |_, generation| {
                Ok(cached_object_catalog(
                    generation,
                    &prepared,
                    prepared.wrapped_dek().clone(),
                ))
            },
            CommitCallbacks {
                invalidate: |_| Ok(()),
                hook: &mut hook,
            },
        );

        assert!(result.is_err());
        assert_eq!(factory.monitor_attempts.load(Ordering::SeqCst), 1);
        assert_eq!(factory.monitor_drops.load(Ordering::SeqCst), 1);
        assert!(coordinator.cache.current().unwrap().object_lease.is_none());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn candidate_reauthenticates_final_catalog_before_attachment() {
        let (root, coordinator, keys, prepared) = setup("final-catalog-reauth");
        let factory = CandidateFactory::supported([]);
        coordinator.set_lease_factory_for_test(factory.clone());
        let mut hook = |point| {
            if point
                == (CommitFailurePoint::Publication {
                    target: PublicationTarget::AuthoritativeHead,
                    phase: PublicationPhase::DestinationSynced,
                })
            {
                std::fs::remove_file(authoritative_catalog_path(&coordinator))?;
            }
            Ok(())
        };

        let outcome = coordinator
            .commit_internal_with_hook(
                &keys,
                &[],
                &[],
                CommitMode::Normal,
                |_, generation| {
                    Ok(cached_object_catalog(
                        generation,
                        &prepared,
                        prepared.wrapped_dek().clone(),
                    ))
                },
                CommitCallbacks {
                    invalidate: |_| Ok(()),
                    hook: &mut hook,
                },
            )
            .unwrap();

        assert_eq!(outcome.generation(), 3);
        assert!(
            coordinator.cache.current().is_none(),
            "missing final catalog bytes retained candidate cache authority"
        );
        assert_eq!(factory.monitor_drops.load(Ordering::SeqCst), 1);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unsupported_platform_commits_through_safe_open_without_lease() {
        let (root, coordinator, keys, prepared) = setup("unsupported");
        let factory = CandidateFactory::unsupported();
        coordinator.set_lease_factory_for_test(factory.clone());
        let mut stages = Vec::new();
        let mut observer = |stage| stages.push(stage);
        let mut probe = CommitProbe::observed(&mut observer);

        commit_unchanged(&coordinator, &keys, &prepared, &mut probe);

        assert!(
            stages
                .iter()
                .any(|stage| *stage == CommitStage::LeaseObjectValidated),
            "unsupported platform skipped the complete safe-open scan"
        );
        assert_eq!(factory.monitor_attempts.load(Ordering::SeqCst), 0);
        assert_eq!(factory.anchor_attempts.load(Ordering::SeqCst), 0);
        assert!(coordinator.cache.current().unwrap().object_lease.is_none());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }
}
