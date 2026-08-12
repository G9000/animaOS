//! Catalog-bound Filesystem Root Key rotation and retirement contracts.

use std::collections::{BTreeMap, HashSet};

use crate::catalog::CatalogGeneration;
use crate::crypto::FrkSubkeys;
use crate::head::HeadRecord;

/// In-memory FRK subkeys indexed by their manifest version.
///
/// The keyring borrows session-owned key material and never serializes it.
pub struct FrkKeyring<'a> {
    by_version: BTreeMap<u32, &'a FrkSubkeys>,
}

impl<'a> FrkKeyring<'a> {
    /// Builds a non-empty keyring and rejects ambiguous duplicate versions.
    pub fn new<I>(keys: I) -> Result<Self, RotationError>
    where
        I: IntoIterator<Item = &'a FrkSubkeys>,
    {
        let mut by_version = BTreeMap::new();
        for key in keys {
            let version = key.frk_version();
            if by_version.insert(version, key).is_some() {
                return Err(RotationError::DuplicateVersion(version));
            }
        }
        if by_version.is_empty() {
            return Err(RotationError::EmptyKeyring);
        }
        Ok(Self { by_version })
    }

    pub(crate) fn single(key: &'a FrkSubkeys) -> Self {
        Self {
            by_version: BTreeMap::from([(key.frk_version(), key)]),
        }
    }

    /// Reports whether one FRK version is available in the current unlock session.
    pub fn contains(&self, version: u32) -> bool {
        self.by_version.contains_key(&version)
    }

    pub(crate) fn require(&self, version: u32) -> Result<&'a FrkSubkeys, RotationError> {
        self.by_version
            .get(&version)
            .copied()
            .ok_or(RotationError::MissingVersion(version))
    }

    pub(crate) fn require_matching(
        &self,
        keys: &FrkSubkeys,
    ) -> Result<&'a FrkSubkeys, RotationError> {
        let selected = self.require(keys.frk_version())?;
        if selected.object_wrap().as_slice() != keys.object_wrap().as_slice()
            || selected.catalog().as_slice() != keys.catalog().as_slice()
            || selected.search().as_slice() != keys.search().as_slice()
            || selected.preparation().as_slice() != keys.preparation().as_slice()
        {
            return Err(RotationError::KeyringMaterialMismatch(keys.frk_version()));
        }
        Ok(selected)
    }

    pub(crate) fn reuses_material_from_other_version(&self, keys: &FrkSubkeys) -> bool {
        self.by_version.iter().any(|(version, existing)| {
            *version != keys.frk_version()
                && (existing.object_wrap().as_slice() == keys.object_wrap().as_slice()
                    || existing.catalog().as_slice() == keys.catalog().as_slice()
                    || existing.search().as_slice() == keys.search().as_slice()
                    || existing.preparation().as_slice() == keys.preparation().as_slice())
        })
    }

    pub(crate) fn versions(&self) -> Vec<u32> {
        self.by_version.keys().copied().collect()
    }
}

/// Failures while selecting or activating FRK material.
#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum RotationError {
    #[error("FRK keyring cannot be empty")]
    EmptyKeyring,
    #[error("FRK keyring contains duplicate version {0}")]
    DuplicateVersion(u32),
    #[error("FRK keyring does not contain required version {0}")]
    MissingVersion(u32),
    #[error("FRK keyring material does not match version {0}")]
    KeyringMaterialMismatch(u32),
    #[error("active FRK version mismatch: expected {expected}, got {actual}")]
    ActiveVersionMismatch { expected: u32, actual: u32 },
    #[error("pending FRK version {pending} must be newer than active version {active}")]
    PendingVersionNotNewer { active: u32, pending: u32 },
    #[error(
        "pending FRK version {pending} must be the direct successor of active version {active}"
    )]
    PendingVersionNotSuccessor { active: u32, pending: u32 },
    #[error("pending FRK reuses existing derived key material")]
    PendingKeyMaterialReused,
    #[error("FRK version is exhausted")]
    VersionExhausted,
    #[error("FRK rotation expected generation {expected}, found {actual}")]
    GenerationMismatch { expected: u64, actual: u64 },
    #[error("CoreFS generation is exhausted during FRK rotation")]
    GenerationExhausted,
    #[error("an active or unauthenticatable CoreFS preparation blocks FRK rotation")]
    PreparationActive,
    #[error("quarantined preparation state still requires FRK version {0}")]
    QuarantinedPreparationRequiresVersion(u32),
    #[error("quarantined preparation retention state is corrupt")]
    QuarantinedPreparationCorrupt,
}

/// Reasons an explicit old-FRK retirement request is not yet safe.
#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum FrkRetirementError {
    #[error("active FRK version {0} cannot be retired")]
    ActiveVersionCannotRetire(u32),
    #[error("retained catalog still requires FRK version {0}")]
    RetainedCatalogRequiresVersion(u32),
    #[error("retained preparation state still requires FRK version {0}")]
    RetainedPreparationRequiresVersion(u32),
    #[error("a verified backup using active FRK version {0} is required")]
    VerifiedActiveBackupRequired(u32),
}

/// Checks the non-destructive gates that must pass before an old FRK is retired.
///
/// This function does not prune catalogs, delete keyslots, or verify backup
/// contents itself. Its inputs must come from the separately authenticated
/// retention and backup workflows.
pub fn authorize_frk_retirement(
    retiring_version: u32,
    active_version: u32,
    retained_catalog_heads: &[HeadRecord],
    retained_catalogs: &[CatalogGeneration],
    verified_backup_versions: &[u32],
) -> Result<(), FrkRetirementError> {
    authorize_frk_retirement_with_preparation_retention(
        retiring_version,
        active_version,
        retained_catalog_heads,
        retained_catalogs,
        &[],
        verified_backup_versions,
    )
}

/// Extends the catalog/backup retirement gate with authenticated preparation
/// retention inventory. Quarantined state remains conservative until a later
/// recovery or GC workflow proves that its captured key versions are safe.
pub fn authorize_frk_retirement_with_preparation_retention(
    retiring_version: u32,
    active_version: u32,
    retained_catalog_heads: &[HeadRecord],
    retained_catalogs: &[CatalogGeneration],
    retained_preparation_frk_versions: &[u32],
    verified_backup_versions: &[u32],
) -> Result<(), FrkRetirementError> {
    if retiring_version == active_version {
        return Err(FrkRetirementError::ActiveVersionCannotRetire(
            active_version,
        ));
    }
    if retained_catalog_heads
        .iter()
        .any(|head| head.required_frk_version() == retiring_version)
    {
        return Err(FrkRetirementError::RetainedCatalogRequiresVersion(
            retiring_version,
        ));
    }
    if retained_catalogs.iter().any(|catalog| {
        catalog.entries().iter().any(|entry| {
            entry
                .object_payload()
                .is_some_and(|object| object.wrapped_dek().frk_version() == retiring_version)
        })
    }) {
        return Err(FrkRetirementError::RetainedCatalogRequiresVersion(
            retiring_version,
        ));
    }
    if retained_preparation_frk_versions.contains(&retiring_version) {
        return Err(FrkRetirementError::RetainedPreparationRequiresVersion(
            retiring_version,
        ));
    }
    let verified: HashSet<_> = verified_backup_versions.iter().copied().collect();
    if !verified.contains(&active_version) {
        return Err(FrkRetirementError::VerifiedActiveBackupRequired(
            active_version,
        ));
    }
    Ok(())
}
