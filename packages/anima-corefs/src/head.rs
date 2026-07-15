//! Canonical `fs/HEAD` pointer records for authoritative catalog generations.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::bounded::{json_to_vec as bounded_json_to_vec, BoundedJsonError};
use crate::catalog::{
    decrypt_catalog_generation, inspect_catalog_generation_envelope, CatalogError,
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
        let catalog = decrypt_catalog_generation(keys, core_id, encrypted_catalog)?;
        let info = inspect_catalog_generation_envelope(encrypted_catalog)?;
        if catalog.generation() != info.generation() {
            return Err(HeadError::CatalogMismatch("generation"));
        }
        let value = Self {
            schema_version: HEAD_SCHEMA_VERSION,
            envelope_version: info.schema_version(),
            generation: info.generation(),
            catalog_hash: sha256_hex(encrypted_catalog),
            required_frk_version,
        };
        value.validate()?;
        Ok(value)
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
        self.validate()?;
        if keys.frk_version() != self.required_frk_version {
            return Err(HeadError::CatalogMismatch("required FRK version"));
        }
        let catalog = decrypt_catalog_generation(keys, core_id, encrypted_catalog)?;
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
        Ok(())
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
    let digest: [u8; 32] = Sha256::digest(bytes).into();
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
