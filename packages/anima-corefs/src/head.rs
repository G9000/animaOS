//! Canonical `fs/HEAD` pointer records for authoritative catalog generations.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::bounded::{json_to_vec as bounded_json_to_vec, BoundedJsonError};
use crate::catalog::{
    decrypt_catalog_generation, format_catalog_generation_physical_name,
    inspect_catalog_generation_envelope, CatalogError, CatalogGeneration, CatalogPublication,
    CATALOG_GENERATION_SCHEMA_VERSION,
};
use crate::crypto::FrkSubkeys;

pub const HEAD_SCHEMA_VERSION: u16 = 1;
pub const MAX_HEAD_SIZE: usize = 4096;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HeadRecord {
    schema_version: u16,
    envelope_version: u16,
    generation: u64,
    catalog_hash: String,
    required_frk_version: u32,
}

struct HeadCatalogOpener<'a> {
    #[cfg(test)]
    observe_decrypt: Option<&'a mut dyn FnMut()>,
    #[cfg(not(test))]
    marker: std::marker::PhantomData<&'a mut ()>,
}

impl<'a> HeadCatalogOpener<'a> {
    fn unobserved() -> Self {
        Self {
            #[cfg(test)]
            observe_decrypt: None,
            #[cfg(not(test))]
            marker: std::marker::PhantomData,
        }
    }

    #[cfg(test)]
    fn observed(observe_decrypt: &'a mut dyn FnMut()) -> Self {
        Self {
            observe_decrypt: Some(observe_decrypt),
        }
    }

    #[cfg(test)]
    fn with_optional_observer(observe_decrypt: Option<&'a mut dyn FnMut()>) -> Self {
        Self { observe_decrypt }
    }

    fn open(
        &mut self,
        keys: &FrkSubkeys,
        core_id: &str,
        encrypted_catalog: &[u8],
    ) -> Result<CatalogGeneration, HeadError> {
        #[cfg(test)]
        if let Some(observer) = self.observe_decrypt.as_mut() {
            observer();
        }
        Ok(decrypt_catalog_generation(
            keys,
            core_id,
            encrypted_catalog,
        )?)
    }
}

impl HeadRecord {
    pub fn new_for_catalog(
        keys: &FrkSubkeys,
        core_id: &str,
        encrypted_catalog: &[u8],
        required_frk_version: u32,
    ) -> Result<Self, HeadError> {
        if required_frk_version == 0 {
            return Err(HeadError::InvalidFormat(
                "required FRK version must be positive",
            ));
        }
        if keys.frk_version() != required_frk_version {
            return Err(HeadError::CatalogMismatch("required FRK version"));
        }
        let mut catalog_opener = HeadCatalogOpener::unobserved();
        let catalog = catalog_opener.open(keys, core_id, encrypted_catalog)?;
        let info = inspect_catalog_generation_envelope(encrypted_catalog)?;
        if catalog.generation() != info.generation() {
            return Err(HeadError::CatalogMismatch("generation"));
        }
        Self::new_from_catalog_parts(
            info.schema_version(),
            info.generation(),
            sha256_digest(encrypted_catalog),
            required_frk_version,
        )
    }

    #[cfg(not(test))]
    pub(crate) fn new_for_publication(
        keys: &FrkSubkeys,
        core_id: &str,
        catalog: &CatalogGeneration,
        publication: &CatalogPublication<'_>,
        required_frk_version: u32,
    ) -> Result<Self, HeadError> {
        Self::new_for_publication_inner(keys, core_id, catalog, publication, required_frk_version)
    }

    #[cfg(test)]
    pub(crate) fn new_for_publication(
        keys: &FrkSubkeys,
        core_id: &str,
        catalog: &CatalogGeneration,
        publication: &CatalogPublication<'_>,
        required_frk_version: u32,
    ) -> Result<Self, HeadError> {
        Self::new_for_publication_inner(
            keys,
            core_id,
            catalog,
            publication,
            required_frk_version,
            HeadCatalogOpener::unobserved(),
            None,
        )
    }

    fn new_for_publication_inner(
        keys: &FrkSubkeys,
        core_id: &str,
        catalog: &CatalogGeneration,
        publication: &CatalogPublication<'_>,
        required_frk_version: u32,
        #[cfg(test)] mut catalog_opener: HeadCatalogOpener<'_>,
        #[cfg(test)] strict_reopen: Option<&[u8]>,
    ) -> Result<Self, HeadError> {
        if required_frk_version == 0 {
            return Err(HeadError::InvalidFormat(
                "required FRK version must be positive",
            ));
        }
        if keys.frk_version() != required_frk_version {
            return Err(HeadError::CatalogMismatch("required FRK version"));
        }
        if core_id.is_empty() || core_id.len() > u32::MAX as usize {
            return Err(HeadError::Catalog(CatalogError::InvalidFormat("core ID")));
        }
        if publication.origin_core_id() != core_id {
            return Err(HeadError::CatalogMismatch("publication core ID"));
        }
        if publication.origin_frk_version() != keys.frk_version() {
            return Err(HeadError::CatalogMismatch("publication FRK version"));
        }
        if !publication.matches_catalog_key_material(keys)? {
            return Err(HeadError::CatalogMismatch("publication FRK material"));
        }
        if !publication.is_source_catalog(catalog) {
            return Err(HeadError::CatalogMismatch("publication catalog"));
        }
        let info = publication.info();
        if info.schema_version() != catalog.schema_version() {
            return Err(HeadError::CatalogMismatch("envelope version"));
        }
        if info.generation() != catalog.generation() {
            return Err(HeadError::CatalogMismatch("generation"));
        }
        if publication.physical_name()
            != format_catalog_generation_physical_name(info.generation(), &publication.digest())
        {
            return Err(HeadError::CatalogMismatch("publication digest"));
        }
        #[cfg(test)]
        if let Some(encrypted_catalog) = strict_reopen {
            let reopened = catalog_opener.open(keys, core_id, encrypted_catalog)?;
            if &reopened != catalog {
                return Err(HeadError::CatalogMismatch("publication catalog"));
            }
        }
        Self::new_from_catalog_parts(
            info.schema_version(),
            info.generation(),
            publication.digest(),
            required_frk_version,
        )
    }

    #[cfg(test)]
    pub(crate) fn new_for_publication_with_observer(
        keys: &FrkSubkeys,
        core_id: &str,
        catalog: &CatalogGeneration,
        publication: &CatalogPublication<'_>,
        required_frk_version: u32,
        observe_decrypt: &mut dyn FnMut(),
    ) -> Result<Self, HeadError> {
        Self::new_for_publication_inner(
            keys,
            core_id,
            catalog,
            publication,
            required_frk_version,
            HeadCatalogOpener::observed(observe_decrypt),
            None,
        )
    }

    #[cfg(test)]
    pub(crate) fn new_for_publication_with_strict_reopen_observer(
        keys: &FrkSubkeys,
        core_id: &str,
        catalog: &CatalogGeneration,
        publication: &CatalogPublication<'_>,
        required_frk_version: u32,
        encrypted_catalog: &[u8],
        observe_decrypt: &mut dyn FnMut(),
    ) -> Result<Self, HeadError> {
        Self::new_for_publication_inner(
            keys,
            core_id,
            catalog,
            publication,
            required_frk_version,
            HeadCatalogOpener::observed(observe_decrypt),
            Some(encrypted_catalog),
        )
    }

    pub fn schema_version(&self) -> u16 {
        self.schema_version
    }

    pub fn envelope_version(&self) -> u16 {
        self.envelope_version
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn catalog_hash(&self) -> &str {
        &self.catalog_hash
    }

    pub fn required_frk_version(&self) -> u32 {
        self.required_frk_version
    }

    pub fn verify_catalog(
        &self,
        keys: &FrkSubkeys,
        core_id: &str,
        encrypted_catalog: &[u8],
    ) -> Result<(), HeadError> {
        self.verify_and_decrypt_catalog(keys, core_id, encrypted_catalog)
            .map(drop)
    }

    #[cfg(not(test))]
    pub(crate) fn verify_and_decrypt_catalog(
        &self,
        keys: &FrkSubkeys,
        core_id: &str,
        encrypted_catalog: &[u8],
    ) -> Result<CatalogGeneration, HeadError> {
        self.verify_and_decrypt_catalog_inner(keys, core_id, encrypted_catalog)
    }

    #[cfg(test)]
    pub(crate) fn verify_and_decrypt_catalog(
        &self,
        keys: &FrkSubkeys,
        core_id: &str,
        encrypted_catalog: &[u8],
    ) -> Result<CatalogGeneration, HeadError> {
        self.verify_and_decrypt_catalog_inner(keys, core_id, encrypted_catalog, None)
    }

    fn verify_and_decrypt_catalog_inner(
        &self,
        keys: &FrkSubkeys,
        core_id: &str,
        encrypted_catalog: &[u8],
        #[cfg(test)] observe_decrypt: Option<&mut dyn FnMut()>,
    ) -> Result<CatalogGeneration, HeadError> {
        self.validate()?;
        if keys.frk_version() != self.required_frk_version {
            return Err(HeadError::CatalogMismatch("required FRK version"));
        }
        #[cfg(test)]
        let mut catalog_opener = HeadCatalogOpener::with_optional_observer(observe_decrypt);
        #[cfg(not(test))]
        let mut catalog_opener = HeadCatalogOpener::unobserved();
        let catalog = catalog_opener.open(keys, core_id, encrypted_catalog)?;
        let info = inspect_catalog_generation_envelope(encrypted_catalog)?;
        if info.schema_version() != self.envelope_version {
            return Err(HeadError::CatalogMismatch("envelope version"));
        }
        if info.generation() != self.generation {
            return Err(HeadError::CatalogMismatch("generation"));
        }
        if catalog.generation() != self.generation {
            return Err(HeadError::CatalogMismatch("decrypted generation"));
        }
        if sha256_hex(encrypted_catalog) != self.catalog_hash {
            return Err(HeadError::CatalogMismatch("hash"));
        }
        Ok(catalog)
    }

    #[cfg(test)]
    fn verify_and_decrypt_catalog_with_observer(
        &self,
        keys: &FrkSubkeys,
        core_id: &str,
        encrypted_catalog: &[u8],
        observe_decrypt: &mut dyn FnMut(),
    ) -> Result<CatalogGeneration, HeadError> {
        self.verify_and_decrypt_catalog_inner(
            keys,
            core_id,
            encrypted_catalog,
            Some(observe_decrypt),
        )
    }

    fn new_from_catalog_parts(
        envelope_version: u16,
        generation: u64,
        catalog_digest: [u8; 32],
        required_frk_version: u32,
    ) -> Result<Self, HeadError> {
        let value = Self {
            schema_version: HEAD_SCHEMA_VERSION,
            envelope_version,
            generation,
            catalog_hash: digest_hex(catalog_digest),
            required_frk_version,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), HeadError> {
        if self.schema_version != HEAD_SCHEMA_VERSION {
            return Err(HeadError::UnsupportedVersion(self.schema_version));
        }
        if self.envelope_version != CATALOG_GENERATION_SCHEMA_VERSION {
            return Err(HeadError::InvalidFormat(
                "authoritative catalog envelope version",
            ));
        }
        if self.generation == 0 {
            return Err(HeadError::InvalidFormat("generation must be positive"));
        }
        if self.required_frk_version == 0 {
            return Err(HeadError::InvalidFormat(
                "required FRK version must be positive",
            ));
        }
        if self.catalog_hash.len() != 64
            || !self
                .catalog_hash
                .as_bytes()
                .iter()
                .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        {
            return Err(HeadError::InvalidFormat(
                "catalog hash must be lowercase SHA-256 hex",
            ));
        }
        Ok(())
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WireHeadRecord {
    schema_version: u16,
    envelope_version: u16,
    generation: u64,
    catalog_hash: String,
    required_frk_version: u32,
}

pub fn encode_head(head: &HeadRecord) -> Result<Vec<u8>, HeadError> {
    head.validate()?;
    let wire = WireHeadRecord {
        schema_version: head.schema_version,
        envelope_version: head.envelope_version,
        generation: head.generation,
        catalog_hash: head.catalog_hash.clone(),
        required_frk_version: head.required_frk_version,
    };
    bounded_json_to_vec(&wire, MAX_HEAD_SIZE).map_err(map_bounded_error)
}

pub fn decode_head(encoded: &[u8]) -> Result<HeadRecord, HeadError> {
    if encoded.len() > MAX_HEAD_SIZE {
        return Err(HeadError::LimitExceeded);
    }
    let wire: WireHeadRecord = serde_json::from_slice(encoded)?;
    let head = HeadRecord {
        schema_version: wire.schema_version,
        envelope_version: wire.envelope_version,
        generation: wire.generation,
        catalog_hash: wire.catalog_hash,
        required_frk_version: wire.required_frk_version,
    };
    head.validate()?;
    if encode_head(&head)? != encoded {
        return Err(HeadError::InvalidFormat("non-canonical HEAD"));
    }
    Ok(head)
}

fn sha256_hex(bytes: &[u8]) -> String {
    digest_hex(sha256_digest(bytes))
}

fn sha256_digest(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}

fn digest_hex(digest: [u8; 32]) -> String {
    let mut output = String::with_capacity(64);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for byte in digest {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn map_bounded_error(error: BoundedJsonError) -> HeadError {
    match error {
        BoundedJsonError::LimitExceeded => HeadError::LimitExceeded,
        BoundedJsonError::Json(error) => HeadError::Json(error),
    }
}

#[derive(Debug, thiserror::Error)]
pub enum HeadError {
    #[error("invalid fs/HEAD: {0}")]
    InvalidFormat(&'static str),
    #[error("unsupported fs/HEAD schema version: {0}")]
    UnsupportedVersion(u16),
    #[error("fs/HEAD limit exceeded")]
    LimitExceeded,
    #[error("fs/HEAD catalog mismatch: {0}")]
    CatalogMismatch(&'static str),
    #[error("invalid fs/HEAD JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("invalid authoritative catalog: {0}")]
    Catalog(#[from] CatalogError),
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;

    use crate::catalog::{
        encrypt_catalog_generation, encrypt_catalog_generation_for_publication, CatalogEntryCommon,
        CatalogGeneration, CatalogGenerationEntry,
    };
    use crate::crypto::{derive_corefs_subkeys, FrkSubkeys, SecretBytes};
    use crate::folders::{FolderOwner, PortableName};
    use crate::id::OpaqueId;
    use crate::policy::AnimaAccess;

    use super::{encode_head, HeadError, HeadRecord};

    fn keys() -> FrkSubkeys {
        derive_corefs_subkeys(&SecretBytes::new(vec![0x22; 32]).unwrap(), 3).unwrap()
    }

    fn catalog(generation: u64) -> CatalogGeneration {
        catalog_named(generation, "Core")
    }

    fn catalog_named(generation: u64, name: &str) -> CatalogGeneration {
        CatalogGeneration::new(
            generation,
            vec![CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                OpaqueId::parse("01J00000000000000000000000").unwrap(),
                None,
                PortableName::parse(name).unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            ))],
        )
        .unwrap()
    }

    #[test]
    fn verified_catalog_open_decrypts_once_and_returns_that_generation() {
        let keys = keys();
        let catalog = catalog(7);
        let encrypted = encrypt_catalog_generation(&keys, "01JCORE", &catalog).unwrap();
        let head = HeadRecord::new_for_catalog(&keys, "01JCORE", &encrypted, 3).unwrap();
        let decrypts = Cell::new(0);
        let mut observe_decrypt = || decrypts.set(decrypts.get() + 1);

        let opened = head
            .verify_and_decrypt_catalog_with_observer(
                &keys,
                "01JCORE",
                &encrypted,
                &mut observe_decrypt,
            )
            .unwrap();

        assert_eq!(decrypts.get(), 1);
        assert_eq!(opened, catalog);
        assert_eq!(opened.generation(), 7);
    }

    #[test]
    fn trusted_publication_head_matches_public_constructor_bytes() {
        let keys = keys();
        let catalog = catalog(7);
        let publication =
            encrypt_catalog_generation_for_publication(&keys, "01JCORE", &catalog).unwrap();

        let public =
            HeadRecord::new_for_catalog(&keys, "01JCORE", publication.encrypted(), 3).unwrap();
        let trusted =
            HeadRecord::new_for_publication(&keys, "01JCORE", &catalog, &publication, 3).unwrap();

        assert_eq!(
            encode_head(&trusted).unwrap(),
            encode_head(&public).unwrap()
        );
    }

    #[test]
    fn trusted_publication_rejects_a_different_core_id() {
        let keys = keys();
        let catalog = catalog(7);
        let publication =
            encrypt_catalog_generation_for_publication(&keys, "01JCOREA", &catalog).unwrap();

        let result = HeadRecord::new_for_publication(&keys, "01JCOREB", &catalog, &publication, 3);

        assert!(matches!(
            result,
            Err(HeadError::CatalogMismatch("publication core ID"))
        ));
    }

    #[test]
    fn trusted_publication_rejects_different_same_version_frk_material() {
        let publication_keys = keys();
        let different_keys =
            derive_corefs_subkeys(&SecretBytes::new(vec![0x33; 32]).unwrap(), 3).unwrap();
        let catalog = catalog(7);
        let publication =
            encrypt_catalog_generation_for_publication(&publication_keys, "01JCORE", &catalog)
                .unwrap();

        let result =
            HeadRecord::new_for_publication(&different_keys, "01JCORE", &catalog, &publication, 3);

        assert!(matches!(
            result,
            Err(HeadError::CatalogMismatch("publication FRK material"))
        ));
    }

    #[test]
    fn trusted_publication_rejects_a_different_same_generation_catalog() {
        let keys = keys();
        let source_catalog = catalog_named(7, "Core");
        let different_catalog = catalog_named(7, "Other");
        let publication =
            encrypt_catalog_generation_for_publication(&keys, "01JCORE", &source_catalog).unwrap();

        let result =
            HeadRecord::new_for_publication(&keys, "01JCORE", &different_catalog, &publication, 3);

        assert!(matches!(
            result,
            Err(HeadError::CatalogMismatch("publication catalog"))
        ));
    }

    #[test]
    fn publication_observer_counts_a_deliberate_strict_reopen() {
        let keys = keys();
        let catalog = catalog(7);
        let publication =
            encrypt_catalog_generation_for_publication(&keys, "01JCORE", &catalog).unwrap();
        let decrypts = Cell::new(0);
        let mut observe_decrypt = || decrypts.set(decrypts.get() + 1);

        HeadRecord::new_for_publication_with_strict_reopen_observer(
            &keys,
            "01JCORE",
            &catalog,
            &publication,
            3,
            publication.encrypted(),
            &mut observe_decrypt,
        )
        .unwrap();

        assert_eq!(decrypts.get(), 1);
    }
}
