use std::sync::Arc;
use std::thread;

use crate::catalog::{
    encrypt_catalog_generation, CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry,
    ContentHash, ObjectPhysicalName, WrappedObjectDekRecord,
};
use crate::crypto::{
    derive_corefs_subkeys, FrkSubkeys, ObjectKind, SecretBytes, OBJECT_KEY_ENVELOPE_VERSION,
    OBJECT_WRAP_ALGORITHM,
};
use crate::folders::{FolderOwner, PortableName};
use crate::head::HeadRecord;
use crate::id::OpaqueId;
use crate::policy::AnimaAccess;
use crate::rotation::FrkKeyring;

use super::cache::{
    AuthenticatedCommitSnapshot, CacheError, CacheLookupKey, CommitCache, PointerSet,
    ValidatedObjectBinding, ValidatedObjectState,
};
use super::{CatalogLoadProbe, CatalogLoadStage, CoreCommitCoordinator};

const CORE_ID: &str = "cache-core";
const ROOT_ID: &str = "01J00000000000000000000000";
const FIRST_OBJECT_ID: &str = "01J00000000000000000000001";
const SECOND_OBJECT_ID: &str = "01J00000000000000000000002";

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
fn unlocked_exact_hit_rereads_pointers_but_not_catalog_bytes_or_crypto() {
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
    assert_eq!(probe.catalog_file_reads, 0);
    assert_eq!(probe.catalog_decrypts, 0);
    assert_eq!(probe.catalog_encodes, 0);
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
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
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
fn locked_exact_hit_rereads_pointers_but_not_catalog_bytes_or_crypto() {
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
    assert_eq!(probe.catalog_file_reads, 0);
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
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = keys(0x11, 1);
    seed_committed(&coordinator, &keys);
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
