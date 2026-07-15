//! Deterministic catalog payloads and authenticated catalog envelopes.

use std::collections::HashSet;

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use hkdf::Hkdf;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::bounded::{
    clone_after_bounded_json_preflight, json_to_vec as bounded_json_to_vec, BoundedJsonError,
};
use crate::crypto::{CryptoError, FrkSubkeys, SecretBytes, KEY_LENGTH, NONCE_LENGTH};
use crate::id::validate_opaque_id;

pub const CATALOG_FORMAT_VERSION: u16 = 1;
pub const MAX_CATALOG_PLAINTEXT_SIZE: usize = 16 * 1024 * 1024;

const MAGIC: &[u8; 8] = b"ACATV1\0\0";
const HEADER_SIZE: usize = 34;
const TAG_LENGTH: usize = 16;
const GENERATION_LABEL_PREFIX: &str = "anima-catalog-generation-v1:";

pub const MAX_CATALOG_ENVELOPE_SIZE: usize = HEADER_SIZE + MAX_CATALOG_PLAINTEXT_SIZE + TAG_LENGTH;

#[derive(Debug, thiserror::Error)]
pub enum CatalogError {
    #[error("invalid catalog: {0}")]
    InvalidFormat(&'static str),
    #[error("unsupported catalog format version: {0}")]
    UnsupportedVersion(u16),
    #[error("catalog limit exceeded: {0}")]
    LimitExceeded(&'static str),
    #[error("duplicate catalog stable ID: {0}")]
    DuplicateStableId(String),
    #[error("invalid catalog JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("cryptographic validation failed: {0}")]
    Crypto(#[from] CryptoError),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CatalogEntry {
    pub stable_id: String,
    pub record: Value,
}

impl CatalogEntry {
    pub fn new(stable_id: impl Into<String>, mut record: Value) -> Result<Self, CatalogError> {
        let stable_id = stable_id.into();
        validate_stable_id(&stable_id)?;
        canonicalize_value(&mut record);
        Ok(Self { stable_id, record })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CatalogPayload {
    pub schema_version: u16,
    pub generation: u64,
    pub entries: Vec<CatalogEntry>,
}

impl CatalogPayload {
    pub fn new(generation: u64, mut entries: Vec<CatalogEntry>) -> Result<Self, CatalogError> {
        for entry in &mut entries {
            canonicalize_value(&mut entry.record);
        }
        entries.sort_by(|left, right| left.stable_id.cmp(&right.stable_id));
        let value = Self {
            schema_version: CATALOG_FORMAT_VERSION,
            generation,
            entries,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), CatalogError> {
        if self.schema_version != CATALOG_FORMAT_VERSION {
            return Err(CatalogError::UnsupportedVersion(self.schema_version));
        }
        if self.generation == 0 {
            return Err(CatalogError::InvalidFormat("generation must be positive"));
        }
        let mut previous: Option<&str> = None;
        let mut ids = HashSet::new();
        for entry in &self.entries {
            validate_stable_id(&entry.stable_id)?;
            if !ids.insert(entry.stable_id.as_str()) {
                return Err(CatalogError::DuplicateStableId(entry.stable_id.clone()));
            }
            if previous.is_some_and(|value| value > entry.stable_id.as_str()) {
                return Err(CatalogError::InvalidFormat("entries are not ordered"));
            }
            previous = Some(&entry.stable_id);
        }
        Ok(())
    }
}

pub fn encode_catalog(payload: &CatalogPayload) -> Result<Vec<u8>, CatalogError> {
    let mut canonical = clone_after_bounded_json_preflight(payload, MAX_CATALOG_PLAINTEXT_SIZE)
        .map_err(|error| match error {
            BoundedJsonError::LimitExceeded => CatalogError::LimitExceeded("catalog plaintext"),
            BoundedJsonError::Json(error) => CatalogError::Json(error),
        })?;
    canonical.validate()?;
    for entry in &mut canonical.entries {
        canonicalize_value(&mut entry.record);
    }
    canonical
        .entries
        .sort_by(|left, right| left.stable_id.cmp(&right.stable_id));
    bounded_json_to_vec(&canonical, MAX_CATALOG_PLAINTEXT_SIZE).map_err(|error| match error {
        BoundedJsonError::LimitExceeded => CatalogError::LimitExceeded("catalog plaintext"),
        BoundedJsonError::Json(error) => CatalogError::Json(error),
    })
}

pub fn decode_catalog(encoded: &[u8]) -> Result<CatalogPayload, CatalogError> {
    if encoded.len() > MAX_CATALOG_PLAINTEXT_SIZE {
        return Err(CatalogError::LimitExceeded("catalog plaintext"));
    }
    let payload: CatalogPayload = serde_json::from_slice(encoded)?;
    payload.validate()?;
    if encode_catalog(&payload)? != encoded {
        return Err(CatalogError::InvalidFormat("non-canonical catalog"));
    }
    Ok(payload)
}

pub fn encrypt_catalog(
    keys: &FrkSubkeys,
    core_id: &str,
    payload: &CatalogPayload,
) -> Result<Vec<u8>, CatalogError> {
    validate_core_id(core_id)?;
    let plaintext = encode_catalog(payload)?;
    let generation_key = generation_key(keys.catalog(), payload.generation)?;
    let cipher = Aes256Gcm::new_from_slice(generation_key.as_slice())
        .map_err(|_| CryptoError::Derivation)?;
    let mut nonce = [0_u8; NONCE_LENGTH];
    getrandom::getrandom(&mut nonce).map_err(|_| CryptoError::Randomness)?;
    let aad = catalog_aad(core_id, payload.generation);
    let ciphertext = cipher
        .encrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &plaintext,
                aad: &aad,
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    let ciphertext_length = u32::try_from(ciphertext.len())
        .map_err(|_| CatalogError::LimitExceeded("catalog ciphertext"))?;
    let mut output = Vec::with_capacity(HEADER_SIZE + ciphertext.len());
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&CATALOG_FORMAT_VERSION.to_le_bytes());
    output.extend_from_slice(&payload.generation.to_le_bytes());
    output.extend_from_slice(&nonce);
    output.extend_from_slice(&ciphertext_length.to_le_bytes());
    output.extend_from_slice(&ciphertext);
    Ok(output)
}

pub fn decrypt_catalog(
    keys: &FrkSubkeys,
    core_id: &str,
    encoded: &[u8],
) -> Result<CatalogPayload, CatalogError> {
    validate_core_id(core_id)?;
    let header = parse_catalog_header(encoded)?;
    let generation_key = generation_key(keys.catalog(), header.generation)?;
    let cipher = Aes256Gcm::new_from_slice(generation_key.as_slice())
        .map_err(|_| CryptoError::Derivation)?;
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&header.nonce),
            Payload {
                msg: &encoded[HEADER_SIZE..],
                aad: &catalog_aad(core_id, header.generation),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    let payload = decode_catalog(&plaintext)?;
    if payload.generation != header.generation {
        return Err(CatalogError::InvalidFormat("catalog generation mismatch"));
    }
    Ok(payload)
}

pub fn catalog_physical_name(
    generation: u64,
    encrypted_catalog: &[u8],
) -> Result<String, CatalogError> {
    if generation == 0 {
        return Err(CatalogError::InvalidFormat("generation must be positive"));
    }
    let header = parse_catalog_header(encrypted_catalog)?;
    if generation != header.generation {
        return Err(CatalogError::InvalidFormat(
            "physical-name generation mismatch",
        ));
    }
    let digest: [u8; 32] = Sha256::digest(encrypted_catalog).into();
    Ok(format!(
        "catalog-{generation:020}-{}.acore",
        hex_bytes(&digest)
    ))
}

#[derive(Clone, Copy)]
struct CatalogHeader {
    generation: u64,
    nonce: [u8; NONCE_LENGTH],
}

fn parse_catalog_header(encoded: &[u8]) -> Result<CatalogHeader, CatalogError> {
    if encoded.len() < HEADER_SIZE {
        return Err(CatalogError::InvalidFormat("truncated header"));
    }
    if &encoded[..8] != MAGIC {
        return Err(CatalogError::InvalidFormat("magic"));
    }
    let version = u16::from_le_bytes(encoded[8..10].try_into().expect("fixed slice"));
    if version != CATALOG_FORMAT_VERSION {
        return Err(CatalogError::UnsupportedVersion(version));
    }
    let generation = u64::from_le_bytes(encoded[10..18].try_into().expect("fixed slice"));
    if generation == 0 {
        return Err(CatalogError::InvalidFormat("generation must be positive"));
    }
    let nonce = encoded[18..30].try_into().expect("fixed slice");
    let ciphertext_length =
        u32::from_le_bytes(encoded[30..34].try_into().expect("fixed slice")) as usize;
    if !(TAG_LENGTH..=MAX_CATALOG_PLAINTEXT_SIZE + TAG_LENGTH).contains(&ciphertext_length) {
        return Err(CatalogError::LimitExceeded("catalog ciphertext"));
    }
    let total_length = HEADER_SIZE
        .checked_add(ciphertext_length)
        .ok_or(CatalogError::LimitExceeded("catalog envelope"))?;
    if encoded.len() != total_length {
        return Err(CatalogError::InvalidFormat("catalog ciphertext length"));
    }
    Ok(CatalogHeader { generation, nonce })
}

fn generation_key(catalog_key: &SecretBytes, generation: u64) -> Result<SecretBytes, CatalogError> {
    let label = format!("{GENERATION_LABEL_PREFIX}{generation}");
    let hkdf = Hkdf::<Sha256>::new(None, catalog_key.as_slice());
    let mut output = vec![0_u8; KEY_LENGTH];
    hkdf.expand(label.as_bytes(), &mut output)
        .map_err(|_| CryptoError::Derivation)?;
    Ok(SecretBytes::new(output)?)
}

fn catalog_aad(core_id: &str, generation: u64) -> Vec<u8> {
    let mut aad = b"anima-corefs-catalog-envelope-v1\0".to_vec();
    aad.extend_from_slice(&(core_id.len() as u32).to_le_bytes());
    aad.extend_from_slice(core_id.as_bytes());
    aad.extend_from_slice(&generation.to_le_bytes());
    aad.extend_from_slice(&CATALOG_FORMAT_VERSION.to_le_bytes());
    aad
}

fn validate_core_id(core_id: &str) -> Result<(), CatalogError> {
    if core_id.is_empty() || core_id.len() > u32::MAX as usize {
        return Err(CatalogError::InvalidFormat("core ID"));
    }
    Ok(())
}

fn validate_stable_id(stable_id: &str) -> Result<(), CatalogError> {
    validate_opaque_id(stable_id).map_err(|_| CatalogError::InvalidFormat("stable ID"))
}

fn canonicalize_value(value: &mut Value) {
    match value {
        Value::Array(values) => {
            for value in values {
                canonicalize_value(value);
            }
        }
        Value::Object(map) => {
            let mut entries: Vec<_> = std::mem::take(map).into_iter().collect();
            entries.sort_by(|left, right| left.0.cmp(&right.0));
            for (_, value) in &mut entries {
                canonicalize_value(value);
            }
            map.extend(entries);
        }
        _ => {}
    }
}

fn hex_bytes(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}
