use std::mem::size_of;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use hkdf::Hkdf;
use sha2::{Digest, Sha256};

use crate::catalog::{
    CatalogError, CatalogGeneration, CatalogObject, ContentHash, ObjectPhysicalName,
    WrappedObjectDekRecord,
};
use crate::crypto::{FrkSubkeys, ObjectKind};
use crate::head::HeadRecord;
use crate::id::OpaqueId;
use crate::rotation::{FrkKeyring, RotationError};

use super::object_lease::ObjectValidationLease;

const CACHE_ID_DOMAIN: &[u8] = b"anima-corefs-commit-cache-key-id-v1\0";
const CATALOG_KEY_PURPOSE: &[u8] = b"catalog";
const OBJECT_WRAP_KEY_PURPOSE: &[u8] = b"object-wrap";
const OBJECT_BINDING_DOMAIN: &[u8] = b"anima-corefs-validated-object-binding-v1\0";

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct CatalogKeyCacheId([u8; 32]);

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct ObjectWrapKeyCacheId([u8; 32]);

#[derive(Clone, Debug, Eq, PartialEq)]
struct VersionedCatalogKeyCacheId {
    frk_version: u32,
    identity: CatalogKeyCacheId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct VersionedObjectWrapKeyCacheId {
    frk_version: u32,
    identity: ObjectWrapKeyCacheId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct RequiredCacheKeyIds {
    core_id: Box<str>,
    catalog: Box<[VersionedCatalogKeyCacheId]>,
    active_object_wrap: VersionedObjectWrapKeyCacheId,
}

impl RequiredCacheKeyIds {
    fn derive(
        pointers: &PointerSet,
        core_id: &str,
        keyring: &FrkKeyring<'_>,
        active_keys: &FrkSubkeys,
    ) -> Result<Self, CacheError> {
        let mut required_versions = pointers.required_frk_versions();
        required_versions.sort_unstable();
        required_versions.dedup();

        let catalog = required_versions
            .into_iter()
            .map(|frk_version| {
                let keys = keyring.require(frk_version)?;
                Ok(VersionedCatalogKeyCacheId {
                    frk_version,
                    identity: CatalogKeyCacheId(derive_key_identity(
                        keys.catalog().as_slice(),
                        core_id,
                        frk_version,
                        CATALOG_KEY_PURPOSE,
                    )?),
                })
            })
            .collect::<Result<Vec<_>, CacheError>>()?
            .into_boxed_slice();
        let active_frk_version = active_keys.frk_version();
        let active_object_wrap = VersionedObjectWrapKeyCacheId {
            frk_version: active_frk_version,
            identity: ObjectWrapKeyCacheId(derive_key_identity(
                active_keys.object_wrap().as_slice(),
                core_id,
                active_frk_version,
                OBJECT_WRAP_KEY_PURPOSE,
            )?),
        };

        Ok(Self {
            core_id: core_id.into(),
            catalog,
            active_object_wrap,
        })
    }

    fn catalog_versions(&self) -> &[VersionedCatalogKeyCacheId] {
        &self.catalog
    }
}

fn derive_key_identity(
    subkey: &[u8],
    core_id: &str,
    frk_version: u32,
    purpose: &[u8],
) -> Result<[u8; 32], CacheError> {
    let core_id_length = u32::try_from(core_id.len()).map_err(|_| CacheError::CoreIdTooLong)?;
    let purpose_length = u32::try_from(purpose.len()).map_err(|_| CacheError::KeyIdDerivation)?;
    let mut info = Vec::with_capacity(
        CACHE_ID_DOMAIN.len()
            + size_of::<u32>()
            + core_id.len()
            + size_of::<u32>()
            + size_of::<u32>()
            + purpose.len(),
    );
    info.extend_from_slice(CACHE_ID_DOMAIN);
    info.extend_from_slice(&core_id_length.to_be_bytes());
    info.extend_from_slice(core_id.as_bytes());
    info.extend_from_slice(&frk_version.to_be_bytes());
    info.extend_from_slice(&purpose_length.to_be_bytes());
    info.extend_from_slice(purpose);

    let hkdf = Hkdf::<Sha256>::new(None, subkey);
    let mut identity = [0_u8; 32];
    hkdf.expand(&info, &mut identity)
        .map_err(|_| CacheError::KeyIdDerivation)?;
    Ok(identity)
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(super) struct PointerSet {
    pub(super) head: Option<HeadRecord>,
    pub(super) receipt: Option<HeadRecord>,
    pub(super) complete: Option<HeadRecord>,
}

impl PointerSet {
    pub(super) fn is_complete_non_recovery_shape(&self) -> bool {
        match (&self.head, &self.receipt, &self.complete) {
            (Some(head), Some(receipt), Some(complete)) => {
                receipt == complete
                    && head.generation() >= receipt.generation()
                    && (head.generation() != receipt.generation() || head == receipt)
            }
            _ => false,
        }
    }

    pub(super) fn required_frk_versions(&self) -> Vec<u32> {
        [&self.head, &self.receipt, &self.complete]
            .into_iter()
            .filter_map(Option::as_ref)
            .map(HeadRecord::required_frk_version)
            .collect()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct CacheLookupKey {
    pointers: PointerSet,
    key_ids: RequiredCacheKeyIds,
}

impl CacheLookupKey {
    pub(super) fn derive(
        pointers: PointerSet,
        core_id: &str,
        keyring: &FrkKeyring<'_>,
        active_keys: &FrkSubkeys,
    ) -> Result<Self, CacheError> {
        let key_ids = RequiredCacheKeyIds::derive(&pointers, core_id, keyring, active_keys)?;
        Ok(Self { pointers, key_ids })
    }

    pub(super) fn required_catalog_versions(&self) -> Vec<u32> {
        self.key_ids
            .catalog_versions()
            .iter()
            .map(|identity| identity.frk_version)
            .collect()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct ValidatedObjectBinding {
    pub(super) object_id: OpaqueId,
    pub(super) revision: u64,
    pub(super) object_key_epoch: u32,
    pub(super) physical_name: ObjectPhysicalName,
    pub(super) content_hash: ContentHash,
    pub(super) kind: ObjectKind,
    pub(super) wrapped_dek: WrappedObjectDekRecord,
    pub(super) binding_digest: [u8; 32],
}

impl ValidatedObjectBinding {
    pub(super) fn from_catalog_object(
        object_id: &OpaqueId,
        object: &CatalogObject,
    ) -> Result<Self, CatalogError> {
        let wrapped_dek = object.wrapped_dek().clone();
        let wrapped = wrapped_dek.to_wrapped_object_dek()?;
        let mut hasher = Sha256::new();
        hasher.update(OBJECT_BINDING_DOMAIN);
        hash_binding_field(&mut hasher, object_id.as_str().as_bytes());
        hasher.update(object.revision().to_be_bytes());
        hasher.update(object.object_key_epoch().to_be_bytes());
        hash_binding_field(&mut hasher, object.physical_name().as_str().as_bytes());
        hash_binding_field(&mut hasher, object.content_hash().as_str().as_bytes());
        hash_binding_field(&mut hasher, object.kind().as_str().as_bytes());
        hasher.update(wrapped_dek.frk_version().to_be_bytes());
        hasher.update(wrapped_dek.object_key_epoch().to_be_bytes());
        hash_binding_field(&mut hasher, wrapped.algorithm().as_bytes());
        hasher.update(wrapped.envelope_version().to_be_bytes());
        hash_binding_field(&mut hasher, wrapped.nonce());
        hash_binding_field(&mut hasher, wrapped.ciphertext());

        Ok(Self {
            object_id: object_id.clone(),
            revision: object.revision(),
            object_key_epoch: object.object_key_epoch(),
            physical_name: object.physical_name().clone(),
            content_hash: object.content_hash().clone(),
            kind: object.kind(),
            wrapped_dek,
            binding_digest: hasher.finalize().into(),
        })
    }
}

fn hash_binding_field(hasher: &mut Sha256, value: &[u8]) {
    hasher.update((value.len() as u64).to_be_bytes());
    hasher.update(value);
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(super) struct ValidatedObjectState {
    by_object_id: Box<[ValidatedObjectBinding]>,
}

impl ValidatedObjectState {
    pub(super) fn empty() -> Self {
        Self::default()
    }

    pub(super) fn from_bindings(
        mut bindings: Vec<ValidatedObjectBinding>,
    ) -> Result<Self, CacheError> {
        bindings.sort_unstable_by(|left, right| left.object_id.cmp(&right.object_id));
        if let Some(duplicate) = bindings
            .windows(2)
            .find(|pair| pair[0].object_id == pair[1].object_id)
        {
            return Err(CacheError::DuplicateObjectId {
                object_id: duplicate[0].object_id.as_str().to_owned(),
            });
        }
        Ok(Self {
            by_object_id: bindings.into_boxed_slice(),
        })
    }

    pub(super) fn get(&self, object_id: &OpaqueId) -> Option<&ValidatedObjectBinding> {
        self.by_object_id
            .binary_search_by(|binding| binding.object_id.cmp(object_id))
            .ok()
            .map(|index| &self.by_object_id[index])
    }

    pub(super) fn from_catalog_bindings(
        bindings: Vec<ValidatedObjectBinding>,
    ) -> Result<Self, CatalogError> {
        Self::from_bindings(bindings).map_err(|error| match error {
            CacheError::DuplicateObjectId { object_id } => {
                CatalogError::DuplicateStableId(object_id)
            }
            CacheError::CoreIdTooLong | CacheError::KeyIdDerivation | CacheError::Rotation(_) => {
                CatalogError::InvalidFormat("invalid validated object cache state")
            }
        })
    }
}

#[derive(Debug)]
pub(super) struct AuthenticatedCommitSnapshot {
    pub(super) pointers: PointerSet,
    pub(super) key_ids: RequiredCacheKeyIds,
    catalog: Arc<CatalogGeneration>,
    pub(super) objects: Option<Arc<ValidatedObjectState>>,
    pub(super) object_lease: Option<Arc<ObjectValidationLease>>,
}

impl AuthenticatedCommitSnapshot {
    pub(super) fn new(
        key: &CacheLookupKey,
        catalog: Arc<CatalogGeneration>,
        objects: Option<Arc<ValidatedObjectState>>,
    ) -> Self {
        Self {
            pointers: key.pointers.clone(),
            key_ids: key.key_ids.clone(),
            catalog,
            objects,
            object_lease: None,
        }
    }

    pub(super) fn with_object_lease(
        mut self,
        object_lease: Option<Arc<ObjectValidationLease>>,
    ) -> Self {
        self.object_lease = object_lease;
        self
    }

    pub(super) fn catalog(&self) -> &Arc<CatalogGeneration> {
        &self.catalog
    }

    fn matches(&self, key: &CacheLookupKey) -> bool {
        self.pointers == key.pointers && self.key_ids == key.key_ids
    }
}

/// Process-local optimization state; disk pointers and cryptographic verification remain authority.
///
/// Rust 1.75 cannot clear a standard mutex's poison flag. Cache methods have no callbacks or other
/// panic-capable extension points, so one per-instance atomic records the single poison recovery:
/// exactly one caller clears the stale slot, and later callers may reuse recovered guards.
#[derive(Default)]
pub(super) struct CommitCache {
    pub(super) inner: Mutex<Option<Arc<AuthenticatedCommitSnapshot>>>,
    recovered_poison: AtomicBool,
}

impl CommitCache {
    pub(super) fn current(&self) -> Option<Arc<AuthenticatedCommitSnapshot>> {
        let mut discarded = None;
        let current = {
            let mut recovered_now = false;
            let guard = match self.inner.lock() {
                Ok(guard) => guard,
                Err(poisoned) => {
                    let mut guard = poisoned.into_inner();
                    recovered_now = self
                        .recovered_poison
                        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                        .is_ok();
                    if recovered_now {
                        discarded = guard.take();
                    }
                    guard
                }
            };
            if recovered_now {
                None
            } else {
                guard.as_ref().cloned()
            }
        };
        drop(discarded);
        current
    }

    pub(super) fn get(&self, key: &CacheLookupKey) -> Option<Arc<AuthenticatedCommitSnapshot>> {
        let mut discarded = None;
        let hit = {
            let mut recovered_now = false;
            let guard = match self.inner.lock() {
                Ok(guard) => guard,
                Err(poisoned) => {
                    let mut guard = poisoned.into_inner();
                    recovered_now = self
                        .recovered_poison
                        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                        .is_ok();
                    if recovered_now {
                        discarded = guard.take();
                    }
                    guard
                }
            };
            if recovered_now {
                None
            } else {
                guard
                    .as_ref()
                    .filter(|snapshot| snapshot.matches(key))
                    .cloned()
            }
        };
        drop(discarded);
        hit
    }

    pub(super) fn replace(&self, value: Arc<AuthenticatedCommitSnapshot>) {
        let discarded = {
            let mut guard = match self.inner.lock() {
                Ok(guard) => guard,
                Err(poisoned) => {
                    let guard = poisoned.into_inner();
                    let _ = self.recovered_poison.compare_exchange(
                        false,
                        true,
                        Ordering::AcqRel,
                        Ordering::Acquire,
                    );
                    guard
                }
            };
            guard.replace(value)
        };
        drop(discarded);
    }

    pub(super) fn clear(&self) {
        let discarded = {
            let mut guard = match self.inner.lock() {
                Ok(guard) => guard,
                Err(poisoned) => {
                    let guard = poisoned.into_inner();
                    let _ = self.recovered_poison.compare_exchange(
                        false,
                        true,
                        Ordering::AcqRel,
                        Ordering::Acquire,
                    );
                    guard
                }
            };
            guard.take()
        };
        drop(discarded);
    }
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub(super) enum CacheError {
    #[error("validated object state contains duplicate object ID {object_id}")]
    DuplicateObjectId { object_id: String },
    #[error("CoreFS cache Core ID exceeds its supported length")]
    CoreIdTooLong,
    #[error("CoreFS cache key identity derivation failed")]
    KeyIdDerivation,
    #[error("CoreFS cache key selection failed: {0}")]
    Rotation(#[from] RotationError),
}
