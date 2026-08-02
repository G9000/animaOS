//! Closed, independently bounded wire records for crash-resumable CoreFS preparation.

use std::collections::HashSet;
use std::ffi::OsStr;
use std::io;

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use cap_std::fs::Dir;
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::bounded::{json_to_vec as bounded_json_to_vec, BoundedJsonError};
use crate::catalog::{ObjectPhysicalName, MAX_CATALOG_ENTRIES};
use crate::crypto::{CryptoError, FrkSubkeys, ObjectKind, NONCE_LENGTH};
use crate::id::validate_opaque_id;
use crate::publication::{
    atomic_publish_in_with_hook, publish_immutable_in_with_hook, PublicationPhase,
};

const PREPARATION_SCHEMA_VERSION: u16 = 1;
const PREPARATION_ENVELOPE_VERSION: u16 = 1;
const PREPARATION_HEAD_FILE: &str = "PREPARATION_HEAD";
const ENVELOPE_MAGIC: &[u8; 8] = b"APREPV1\0";
const ENVELOPE_FIXED_HEADER_SIZE: usize = 43;
const TAG_LENGTH: usize = 16;
const MAX_AAD_CONTEXT_BYTES: usize = 128;
const MAX_SCOPE_BYTES: usize = 64;
const MAX_SEGMENT_REFERENCES: usize = 1024;
const MAX_SEGMENT_ITEMS: usize = 1024;

pub(super) const MAX_PREPARATION_HEAD_PLAINTEXT_SIZE: usize = 4 * 1024;
pub(super) const MAX_PREPARATION_SNAPSHOT_PLAINTEXT_SIZE: usize = 64 * 1024;
pub(super) const MAX_DESCRIPTOR_SEGMENT_PLAINTEXT_SIZE: usize = 1024 * 1024;
pub(super) const MAX_FINAL_INTENT_SEGMENT_PLAINTEXT_SIZE: usize = 1024 * 1024;
pub(super) const MAX_PREPARATION_RECEIPT_PLAINTEXT_SIZE: usize = 16 * 1024;
pub(super) const MAX_FINAL_INTENT_ENTRY_BYTES: usize = 64 * 1024;

pub(super) const MAX_PREPARATION_HEAD_ENVELOPE_SIZE: usize = ENVELOPE_FIXED_HEADER_SIZE
    + MAX_AAD_CONTEXT_BYTES
    + MAX_PREPARATION_HEAD_PLAINTEXT_SIZE
    + TAG_LENGTH;
pub(super) const MAX_PREPARATION_SNAPSHOT_ENVELOPE_SIZE: usize = ENVELOPE_FIXED_HEADER_SIZE
    + MAX_AAD_CONTEXT_BYTES
    + MAX_PREPARATION_SNAPSHOT_PLAINTEXT_SIZE
    + TAG_LENGTH;
pub(super) const MAX_DESCRIPTOR_SEGMENT_ENVELOPE_SIZE: usize = ENVELOPE_FIXED_HEADER_SIZE
    + MAX_AAD_CONTEXT_BYTES
    + MAX_DESCRIPTOR_SEGMENT_PLAINTEXT_SIZE
    + TAG_LENGTH;
pub(super) const MAX_FINAL_INTENT_SEGMENT_ENVELOPE_SIZE: usize = ENVELOPE_FIXED_HEADER_SIZE
    + MAX_AAD_CONTEXT_BYTES
    + MAX_FINAL_INTENT_SEGMENT_PLAINTEXT_SIZE
    + TAG_LENGTH;
pub(super) const MAX_PREPARATION_RECEIPT_ENVELOPE_SIZE: usize = ENVELOPE_FIXED_HEADER_SIZE
    + MAX_AAD_CONTEXT_BYTES
    + MAX_PREPARATION_RECEIPT_PLAINTEXT_SIZE
    + TAG_LENGTH;

#[derive(Debug, thiserror::Error)]
pub(super) enum PreparationError {
    #[error("invalid preparation record: {0}")]
    InvalidFormat(&'static str),
    #[error("unsupported preparation schema version: {0}")]
    UnsupportedSchemaVersion(u16),
    #[error("unsupported preparation envelope version: {0}")]
    UnsupportedEnvelopeVersion(u16),
    #[error("preparation record exceeds its independent bound: {0}")]
    LimitExceeded(&'static str),
    #[error("invalid preparation JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("preparation cryptography failed: {0}")]
    Crypto(#[from] CryptoError),
    #[error("preparation publication failed: {0}")]
    Io(#[from] io::Error),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
enum PreparationRecordKind {
    Head = 1,
    Snapshot = 2,
    DescriptorSegment = 3,
    FinalIntentSegment = 4,
    Receipt = 5,
}

impl PreparationRecordKind {
    fn parse(value: u8) -> Result<Self, PreparationError> {
        match value {
            1 => Ok(Self::Head),
            2 => Ok(Self::Snapshot),
            3 => Ok(Self::DescriptorSegment),
            4 => Ok(Self::FinalIntentSegment),
            5 => Ok(Self::Receipt),
            _ => Err(PreparationError::InvalidFormat("record kind")),
        }
    }

    fn immutable_suffix(self) -> Result<&'static str, PreparationError> {
        match self {
            Self::Head => Err(PreparationError::InvalidFormat(
                "preparation head is not immutable",
            )),
            Self::Snapshot => Ok("prep.acore"),
            Self::DescriptorSegment => Ok("prep-manifest.acore"),
            Self::FinalIntentSegment => Ok("prep-intent.acore"),
            Self::Receipt => Ok("prep-receipt.acore"),
        }
    }

    fn maximum_envelope_size(self) -> usize {
        match self {
            Self::Head => MAX_PREPARATION_HEAD_ENVELOPE_SIZE,
            Self::Snapshot => MAX_PREPARATION_SNAPSHOT_ENVELOPE_SIZE,
            Self::DescriptorSegment => MAX_DESCRIPTOR_SEGMENT_ENVELOPE_SIZE,
            Self::FinalIntentSegment => MAX_FINAL_INTENT_SEGMENT_ENVELOPE_SIZE,
            Self::Receipt => MAX_PREPARATION_RECEIPT_ENVELOPE_SIZE,
        }
    }
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PreparationHeadRecord {
    pub(super) schema_version: u16,
    pub(super) core_id: String,
    pub(super) preparation_id: String,
    pub(super) snapshot_sequence: u64,
    pub(super) snapshot_ciphertext_sha256: String,
    pub(super) envelope_version: u16,
    pub(super) required_frk_version: u32,
}

#[derive(Clone, Copy, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum PreparationState {
    Collecting,
    Ready,
    Completed,
    Abandoned,
}

impl PreparationState {
    fn as_str(self) -> &'static str {
        match self {
            Self::Collecting => "collecting",
            Self::Ready => "ready",
            Self::Completed => "completed",
            Self::Abandoned => "abandoned",
        }
    }
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PreparationSegmentReference {
    pub(super) segment_index: u32,
    pub(super) ciphertext_sha256: String,
    pub(super) item_count: u32,
    pub(super) plaintext_bytes: u32,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PreparationSnapshot {
    pub(super) schema_version: u16,
    pub(super) core_id: String,
    pub(super) preparation_id: String,
    pub(super) sequence: u64,
    pub(super) state: PreparationState,
    pub(super) scope: String,
    pub(super) required_frk_version: u32,
    pub(super) created_at_unix_ms: u64,
    pub(super) updated_at_unix_ms: u64,
    pub(super) expected_validation_generation: Option<u64>,
    pub(super) expected_validation_catalog_sha256: Option<String>,
    pub(super) source_owner_id: String,
    pub(super) source_inventory_version: u16,
    pub(super) source_mutation_generation: u64,
    pub(super) source_inventory_sha256: String,
    pub(super) total_objects: u32,
    pub(super) total_plaintext_bytes: u64,
    pub(super) manifest_root_sha256: String,
    pub(super) manifest_segments: Vec<PreparationSegmentReference>,
    pub(super) final_intent_root_sha256: Option<String>,
    pub(super) final_intent_segments: Vec<PreparationSegmentReference>,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct WrappedObjectDekWire {
    pub(super) frk_version: u32,
    pub(super) object_key_epoch: u32,
    pub(super) algorithm: String,
    pub(super) envelope_version: u16,
    pub(super) nonce_base64: String,
    pub(super) ciphertext_base64: String,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PreparedObjectDescriptor {
    pub(super) stable_id: String,
    pub(super) revision: u64,
    pub(super) kind: String,
    pub(super) object_key_epoch: u32,
    pub(super) physical_name: String,
    pub(super) encoded_size: u64,
    pub(super) encrypted_file_sha256: String,
    pub(super) content_sha256: String,
    pub(super) object_key_binding_sha256: String,
    pub(super) wrapped_object_dek: WrappedObjectDekWire,
    pub(super) envelope_metadata_sha256: String,
    pub(super) source_fingerprint_sha256: String,
    pub(super) converter_format_version: u16,
    pub(super) preparation_ordinal: u64,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PreparedObjectDescriptorSegment {
    pub(super) schema_version: u16,
    pub(super) core_id: String,
    pub(super) preparation_id: String,
    pub(super) required_frk_version: u32,
    pub(super) segment_index: u32,
    pub(super) descriptors: Vec<PreparedObjectDescriptor>,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalIntentEntry {
    pub(super) ordinal: u64,
    pub(super) stable_id: String,
    pub(super) canonical_catalog_entry_sha256: String,
    pub(super) canonical_catalog_entry_json: String,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FinalIntentSegment {
    pub(super) schema_version: u16,
    pub(super) core_id: String,
    pub(super) preparation_id: String,
    pub(super) required_frk_version: u32,
    pub(super) segment_index: u32,
    pub(super) entries: Vec<FinalIntentEntry>,
}

#[derive(Clone, Copy, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum PreparationReceiptOutcome {
    Completed,
    Abandoned,
    Quarantined,
}

impl PreparationReceiptOutcome {
    fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Abandoned => "abandoned",
            Self::Quarantined => "quarantined",
        }
    }
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct PreparationReceipt {
    pub(super) schema_version: u16,
    pub(super) core_id: String,
    pub(super) preparation_id: String,
    pub(super) receipt_id: String,
    pub(super) outcome: PreparationReceiptOutcome,
    pub(super) required_frk_version: u32,
    pub(super) final_snapshot_sequence: u64,
    pub(super) final_snapshot_ciphertext_sha256: String,
    pub(super) pointer_sha256: String,
    pub(super) validation_generation: Option<u64>,
    pub(super) validation_catalog_sha256: Option<String>,
}

trait PreparationRecord: Clone + DeserializeOwned + Serialize {
    const KIND: PreparationRecordKind;
    const MAX_PLAINTEXT_SIZE: usize;
    const MAX_ENVELOPE_SIZE: usize;

    fn schema_version(&self) -> u16;
    fn core_id(&self) -> &str;
    fn required_frk_version(&self) -> u32;
    fn monotonic_number(&self) -> u64;
    fn aad_context(&self) -> String;
    fn validate(&self) -> Result<(), PreparationError>;
}

macro_rules! impl_record_codecs {
    ($type:ty) => {
        impl $type {
            pub(super) fn encode(&self) -> Result<Vec<u8>, PreparationError> {
                encode_record(self)
            }

            pub(super) fn decode(
                encoded: &[u8],
                expected_core_id: &str,
                expected_frk_version: u32,
            ) -> Result<Self, PreparationError> {
                decode_record(encoded, expected_core_id, expected_frk_version)
            }

            pub(super) fn seal(&self, keys: &FrkSubkeys) -> Result<Vec<u8>, PreparationError> {
                seal_record(self, keys)
            }

            pub(super) fn open(
                encoded: &[u8],
                keys: &FrkSubkeys,
                expected_core_id: &str,
                expected_frk_version: u32,
            ) -> Result<Self, PreparationError> {
                open_record(encoded, keys, expected_core_id, expected_frk_version)
            }
        }
    };
}

impl_record_codecs!(PreparationHeadRecord);
impl_record_codecs!(PreparationSnapshot);
impl_record_codecs!(PreparedObjectDescriptorSegment);
impl_record_codecs!(FinalIntentSegment);
impl_record_codecs!(PreparationReceipt);

impl PreparationRecord for PreparationHeadRecord {
    const KIND: PreparationRecordKind = PreparationRecordKind::Head;
    const MAX_PLAINTEXT_SIZE: usize = MAX_PREPARATION_HEAD_PLAINTEXT_SIZE;
    const MAX_ENVELOPE_SIZE: usize = MAX_PREPARATION_HEAD_ENVELOPE_SIZE;

    fn schema_version(&self) -> u16 {
        self.schema_version
    }
    fn core_id(&self) -> &str {
        &self.core_id
    }
    fn required_frk_version(&self) -> u32 {
        self.required_frk_version
    }
    fn monotonic_number(&self) -> u64 {
        self.snapshot_sequence
    }
    fn aad_context(&self) -> String {
        self.preparation_id.clone()
    }
    fn validate(&self) -> Result<(), PreparationError> {
        validate_common(self, &self.preparation_id)?;
        validate_hash(&self.snapshot_ciphertext_sha256)?;
        if self.snapshot_sequence == 0 {
            return Err(PreparationError::InvalidFormat("snapshot sequence"));
        }
        if self.envelope_version != PREPARATION_ENVELOPE_VERSION {
            return Err(PreparationError::UnsupportedEnvelopeVersion(
                self.envelope_version,
            ));
        }
        Ok(())
    }
}

impl PreparationRecord for PreparationSnapshot {
    const KIND: PreparationRecordKind = PreparationRecordKind::Snapshot;
    const MAX_PLAINTEXT_SIZE: usize = MAX_PREPARATION_SNAPSHOT_PLAINTEXT_SIZE;
    const MAX_ENVELOPE_SIZE: usize = MAX_PREPARATION_SNAPSHOT_ENVELOPE_SIZE;

    fn schema_version(&self) -> u16 {
        self.schema_version
    }
    fn core_id(&self) -> &str {
        &self.core_id
    }
    fn required_frk_version(&self) -> u32 {
        self.required_frk_version
    }
    fn monotonic_number(&self) -> u64 {
        self.sequence
    }
    fn aad_context(&self) -> String {
        format!("{}:{}", self.preparation_id, self.state.as_str())
    }
    fn validate(&self) -> Result<(), PreparationError> {
        validate_common(self, &self.preparation_id)?;
        if self.sequence == 0 {
            return Err(PreparationError::InvalidFormat("snapshot sequence"));
        }
        if self.scope.is_empty() || self.scope.len() > MAX_SCOPE_BYTES {
            return Err(PreparationError::LimitExceeded("scope"));
        }
        if self.created_at_unix_ms == 0
            || self.updated_at_unix_ms < self.created_at_unix_ms
            || self.source_inventory_version == 0
            || self.source_mutation_generation == 0
        {
            return Err(PreparationError::InvalidFormat("snapshot counters"));
        }
        validate_opaque(&self.source_owner_id, "source owner ID")?;
        validate_optional_head(
            self.expected_validation_generation,
            self.expected_validation_catalog_sha256.as_deref(),
        )?;
        validate_hash(&self.source_inventory_sha256)?;
        validate_hash(&self.manifest_root_sha256)?;
        validate_optional_hash(self.final_intent_root_sha256.as_deref())?;
        if usize::try_from(self.total_objects)
            .map_err(|_| PreparationError::LimitExceeded("total objects"))?
            > MAX_CATALOG_ENTRIES
        {
            return Err(PreparationError::LimitExceeded("total objects"));
        }
        validate_segment_references(&self.manifest_segments)?;
        validate_segment_references(&self.final_intent_segments)?;
        Ok(())
    }
}

impl PreparationRecord for PreparedObjectDescriptorSegment {
    const KIND: PreparationRecordKind = PreparationRecordKind::DescriptorSegment;
    const MAX_PLAINTEXT_SIZE: usize = MAX_DESCRIPTOR_SEGMENT_PLAINTEXT_SIZE;
    const MAX_ENVELOPE_SIZE: usize = MAX_DESCRIPTOR_SEGMENT_ENVELOPE_SIZE;

    fn schema_version(&self) -> u16 {
        self.schema_version
    }
    fn core_id(&self) -> &str {
        &self.core_id
    }
    fn required_frk_version(&self) -> u32 {
        self.required_frk_version
    }
    fn monotonic_number(&self) -> u64 {
        u64::from(self.segment_index)
    }
    fn aad_context(&self) -> String {
        self.preparation_id.clone()
    }
    fn validate(&self) -> Result<(), PreparationError> {
        validate_common(self, &self.preparation_id)?;
        if self.descriptors.is_empty() || self.descriptors.len() > MAX_SEGMENT_ITEMS {
            return Err(PreparationError::LimitExceeded("descriptor segment items"));
        }
        let mut stable_ids = HashSet::new();
        let mut physical_names = HashSet::new();
        let mut ordinals = HashSet::new();
        for descriptor in &self.descriptors {
            descriptor.validate(self.required_frk_version)?;
            if !stable_ids.insert(descriptor.stable_id.as_str()) {
                return Err(PreparationError::InvalidFormat("duplicate stable ID"));
            }
            if !physical_names.insert(descriptor.physical_name.as_str()) {
                return Err(PreparationError::InvalidFormat("duplicate physical name"));
            }
            if !ordinals.insert(descriptor.preparation_ordinal) {
                return Err(PreparationError::InvalidFormat(
                    "duplicate preparation ordinal",
                ));
            }
        }
        Ok(())
    }
}

impl PreparationRecord for FinalIntentSegment {
    const KIND: PreparationRecordKind = PreparationRecordKind::FinalIntentSegment;
    const MAX_PLAINTEXT_SIZE: usize = MAX_FINAL_INTENT_SEGMENT_PLAINTEXT_SIZE;
    const MAX_ENVELOPE_SIZE: usize = MAX_FINAL_INTENT_SEGMENT_ENVELOPE_SIZE;

    fn schema_version(&self) -> u16 {
        self.schema_version
    }
    fn core_id(&self) -> &str {
        &self.core_id
    }
    fn required_frk_version(&self) -> u32 {
        self.required_frk_version
    }
    fn monotonic_number(&self) -> u64 {
        u64::from(self.segment_index)
    }
    fn aad_context(&self) -> String {
        self.preparation_id.clone()
    }
    fn validate(&self) -> Result<(), PreparationError> {
        validate_common(self, &self.preparation_id)?;
        if self.entries.is_empty() || self.entries.len() > MAX_SEGMENT_ITEMS {
            return Err(PreparationError::LimitExceeded(
                "final-intent segment items",
            ));
        }
        let mut stable_ids = HashSet::new();
        let mut ordinals = HashSet::new();
        for entry in &self.entries {
            entry.validate()?;
            if !stable_ids.insert(entry.stable_id.as_str()) {
                return Err(PreparationError::InvalidFormat("duplicate stable ID"));
            }
            if !ordinals.insert(entry.ordinal) {
                return Err(PreparationError::InvalidFormat(
                    "duplicate final-intent ordinal",
                ));
            }
        }
        Ok(())
    }
}

impl PreparationRecord for PreparationReceipt {
    const KIND: PreparationRecordKind = PreparationRecordKind::Receipt;
    const MAX_PLAINTEXT_SIZE: usize = MAX_PREPARATION_RECEIPT_PLAINTEXT_SIZE;
    const MAX_ENVELOPE_SIZE: usize = MAX_PREPARATION_RECEIPT_ENVELOPE_SIZE;

    fn schema_version(&self) -> u16 {
        self.schema_version
    }
    fn core_id(&self) -> &str {
        &self.core_id
    }
    fn required_frk_version(&self) -> u32 {
        self.required_frk_version
    }
    fn monotonic_number(&self) -> u64 {
        self.final_snapshot_sequence
    }
    fn aad_context(&self) -> String {
        format!("{}:{}", self.pointer_sha256, self.outcome.as_str())
    }
    fn validate(&self) -> Result<(), PreparationError> {
        validate_common(self, &self.preparation_id)?;
        if self.final_snapshot_sequence == 0 {
            return Err(PreparationError::InvalidFormat("snapshot sequence"));
        }
        validate_opaque(&self.receipt_id, "receipt ID")?;
        validate_hash(&self.final_snapshot_ciphertext_sha256)?;
        validate_hash(&self.pointer_sha256)?;
        validate_optional_head(
            self.validation_generation,
            self.validation_catalog_sha256.as_deref(),
        )?;
        if self.outcome == PreparationReceiptOutcome::Completed
            && self.validation_generation.is_none()
        {
            return Err(PreparationError::InvalidFormat(
                "completed receipt lacks validation generation",
            ));
        }
        Ok(())
    }
}

impl PreparedObjectDescriptor {
    fn validate(&self, required_frk_version: u32) -> Result<(), PreparationError> {
        validate_opaque(&self.stable_id, "stable ID")?;
        if self.revision == 0
            || self.object_key_epoch == 0
            || self.encoded_size == 0
            || self.converter_format_version == 0
        {
            return Err(PreparationError::InvalidFormat("descriptor counters"));
        }
        ObjectKind::parse(&self.kind)?;
        ObjectPhysicalName::parse(&self.physical_name)
            .map_err(|_| PreparationError::InvalidFormat("physical name"))?;
        validate_hash(&self.encrypted_file_sha256)?;
        validate_hash(&self.content_sha256)?;
        validate_hash(&self.object_key_binding_sha256)?;
        validate_hash(&self.envelope_metadata_sha256)?;
        validate_hash(&self.source_fingerprint_sha256)?;
        self.wrapped_object_dek
            .validate(required_frk_version, self.object_key_epoch)
    }
}

impl WrappedObjectDekWire {
    fn validate(
        &self,
        required_frk_version: u32,
        object_key_epoch: u32,
    ) -> Result<(), PreparationError> {
        if self.frk_version != required_frk_version
            || self.object_key_epoch != object_key_epoch
            || self.algorithm != "aes-256-gcm"
            || self.envelope_version != 1
        {
            return Err(PreparationError::InvalidFormat("wrapped object DEK"));
        }
        let nonce = BASE64
            .decode(&self.nonce_base64)
            .map_err(|_| PreparationError::InvalidFormat("wrapped object DEK nonce"))?;
        let ciphertext = BASE64
            .decode(&self.ciphertext_base64)
            .map_err(|_| PreparationError::InvalidFormat("wrapped object DEK ciphertext"))?;
        if nonce.len() != NONCE_LENGTH || ciphertext.len() != 32 + TAG_LENGTH {
            return Err(PreparationError::InvalidFormat(
                "wrapped object DEK lengths",
            ));
        }
        Ok(())
    }
}

impl FinalIntentEntry {
    fn validate(&self) -> Result<(), PreparationError> {
        validate_opaque(&self.stable_id, "stable ID")?;
        validate_hash(&self.canonical_catalog_entry_sha256)?;
        if self.canonical_catalog_entry_json.is_empty()
            || self.canonical_catalog_entry_json.len() > MAX_FINAL_INTENT_ENTRY_BYTES
        {
            return Err(PreparationError::LimitExceeded("final-intent entry"));
        }
        let value: serde_json::Value = serde_json::from_str(&self.canonical_catalog_entry_json)?;
        if !value.is_object() || serde_json::to_string(&value)? != self.canonical_catalog_entry_json
        {
            return Err(PreparationError::InvalidFormat(
                "non-canonical final-intent entry",
            ));
        }
        Ok(())
    }
}

fn encode_record<T: PreparationRecord>(record: &T) -> Result<Vec<u8>, PreparationError> {
    record.validate()?;
    bounded_json_to_vec(record, T::MAX_PLAINTEXT_SIZE).map_err(|error| match error {
        BoundedJsonError::LimitExceeded => PreparationError::LimitExceeded("plaintext"),
        BoundedJsonError::Json(error) => PreparationError::Json(error),
    })
}

fn decode_record<T: PreparationRecord>(
    encoded: &[u8],
    expected_core_id: &str,
    expected_frk_version: u32,
) -> Result<T, PreparationError> {
    if encoded.len() > T::MAX_PLAINTEXT_SIZE {
        return Err(PreparationError::LimitExceeded("plaintext"));
    }
    let record: T = serde_json::from_slice(encoded)?;
    record.validate()?;
    validate_expected_binding(&record, expected_core_id, expected_frk_version)?;
    if encode_record(&record)? != encoded {
        return Err(PreparationError::InvalidFormat("non-canonical record"));
    }
    Ok(record)
}

fn seal_record<T: PreparationRecord>(
    record: &T,
    keys: &FrkSubkeys,
) -> Result<Vec<u8>, PreparationError> {
    if keys.frk_version() != record.required_frk_version() {
        return Err(PreparationError::InvalidFormat("FRK version mismatch"));
    }
    let plaintext = encode_record(record)?;
    let context = record.aad_context();
    if context.is_empty() || context.len() > MAX_AAD_CONTEXT_BYTES {
        return Err(PreparationError::LimitExceeded("AAD context"));
    }
    let context_length =
        u16::try_from(context.len()).map_err(|_| PreparationError::LimitExceeded("AAD context"))?;
    let mut nonce = [0_u8; NONCE_LENGTH];
    getrandom::getrandom(&mut nonce).map_err(|_| CryptoError::Randomness)?;
    let aad = record_aad(
        T::KIND,
        record.schema_version(),
        record.core_id(),
        record.required_frk_version(),
        record.monotonic_number(),
        context.as_bytes(),
    )?;
    let cipher = Aes256Gcm::new_from_slice(keys.preparation().as_slice())
        .map_err(|_| CryptoError::Derivation)?;
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
        .map_err(|_| PreparationError::LimitExceeded("ciphertext"))?;
    let mut envelope = Vec::with_capacity(
        ENVELOPE_FIXED_HEADER_SIZE
            .checked_add(context.len())
            .and_then(|size| size.checked_add(ciphertext.len()))
            .ok_or(PreparationError::LimitExceeded("envelope"))?,
    );
    envelope.extend_from_slice(ENVELOPE_MAGIC);
    envelope.extend_from_slice(&PREPARATION_ENVELOPE_VERSION.to_le_bytes());
    envelope.push(T::KIND as u8);
    envelope.extend_from_slice(&record.schema_version().to_le_bytes());
    envelope.extend_from_slice(&record.required_frk_version().to_le_bytes());
    envelope.extend_from_slice(&record.monotonic_number().to_le_bytes());
    envelope.extend_from_slice(&context_length.to_le_bytes());
    envelope.extend_from_slice(&nonce);
    envelope.extend_from_slice(&ciphertext_length.to_le_bytes());
    envelope.extend_from_slice(context.as_bytes());
    envelope.extend_from_slice(&ciphertext);
    if envelope.len() > T::MAX_ENVELOPE_SIZE {
        return Err(PreparationError::LimitExceeded("envelope"));
    }
    Ok(envelope)
}

fn open_record<T: PreparationRecord>(
    encoded: &[u8],
    keys: &FrkSubkeys,
    expected_core_id: &str,
    expected_frk_version: u32,
) -> Result<T, PreparationError> {
    validate_opaque(expected_core_id, "Core ID")?;
    if encoded.len() > T::MAX_ENVELOPE_SIZE
        || encoded.len() < ENVELOPE_FIXED_HEADER_SIZE + TAG_LENGTH
    {
        return Err(PreparationError::LimitExceeded("envelope"));
    }
    if &encoded[..8] != ENVELOPE_MAGIC {
        return Err(PreparationError::InvalidFormat("envelope magic"));
    }
    let envelope_version = u16::from_le_bytes(encoded[8..10].try_into().expect("fixed slice"));
    if envelope_version != PREPARATION_ENVELOPE_VERSION {
        return Err(PreparationError::UnsupportedEnvelopeVersion(
            envelope_version,
        ));
    }
    let kind = PreparationRecordKind::parse(encoded[10])?;
    if kind != T::KIND {
        return Err(PreparationError::InvalidFormat("record kind mismatch"));
    }
    let schema_version = u16::from_le_bytes(encoded[11..13].try_into().expect("fixed slice"));
    let frk_version = u32::from_le_bytes(encoded[13..17].try_into().expect("fixed slice"));
    let monotonic_number = u64::from_le_bytes(encoded[17..25].try_into().expect("fixed slice"));
    let context_length = usize::from(u16::from_le_bytes(
        encoded[25..27].try_into().expect("fixed slice"),
    ));
    if context_length == 0 || context_length > MAX_AAD_CONTEXT_BYTES {
        return Err(PreparationError::LimitExceeded("AAD context"));
    }
    let nonce: [u8; NONCE_LENGTH] = encoded[27..39].try_into().expect("fixed slice");
    let ciphertext_length = usize::try_from(u32::from_le_bytes(
        encoded[39..43].try_into().expect("fixed slice"),
    ))
    .map_err(|_| PreparationError::LimitExceeded("ciphertext"))?;
    if !(TAG_LENGTH..=T::MAX_PLAINTEXT_SIZE + TAG_LENGTH).contains(&ciphertext_length) {
        return Err(PreparationError::LimitExceeded("ciphertext"));
    }
    let context_end = ENVELOPE_FIXED_HEADER_SIZE
        .checked_add(context_length)
        .ok_or(PreparationError::LimitExceeded("envelope"))?;
    let envelope_end = context_end
        .checked_add(ciphertext_length)
        .ok_or(PreparationError::LimitExceeded("envelope"))?;
    if encoded.len() != envelope_end {
        return Err(PreparationError::InvalidFormat("envelope length"));
    }
    if schema_version != PREPARATION_SCHEMA_VERSION {
        return Err(PreparationError::UnsupportedSchemaVersion(schema_version));
    }
    if frk_version != expected_frk_version || keys.frk_version() != expected_frk_version {
        return Err(PreparationError::InvalidFormat("FRK version mismatch"));
    }
    let context = &encoded[ENVELOPE_FIXED_HEADER_SIZE..context_end];
    let aad = record_aad(
        T::KIND,
        schema_version,
        expected_core_id,
        frk_version,
        monotonic_number,
        context,
    )?;
    let cipher = Aes256Gcm::new_from_slice(keys.preparation().as_slice())
        .map_err(|_| CryptoError::Derivation)?;
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &encoded[context_end..],
                aad: &aad,
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    let record = decode_record::<T>(&plaintext, expected_core_id, expected_frk_version)?;
    if record.schema_version() != schema_version
        || record.monotonic_number() != monotonic_number
        || record.aad_context().as_bytes() != context
    {
        return Err(PreparationError::InvalidFormat("envelope binding mismatch"));
    }
    Ok(record)
}

fn record_aad(
    kind: PreparationRecordKind,
    schema_version: u16,
    core_id: &str,
    frk_version: u32,
    monotonic_number: u64,
    context: &[u8],
) -> Result<Vec<u8>, PreparationError> {
    validate_opaque(core_id, "Core ID")?;
    let core_length =
        u32::try_from(core_id.len()).map_err(|_| PreparationError::LimitExceeded("Core ID"))?;
    let context_length =
        u16::try_from(context.len()).map_err(|_| PreparationError::LimitExceeded("AAD context"))?;
    let mut aad = b"anima-corefs-preparation-envelope-v1\0".to_vec();
    aad.push(kind as u8);
    aad.extend_from_slice(&PREPARATION_ENVELOPE_VERSION.to_le_bytes());
    aad.extend_from_slice(&schema_version.to_le_bytes());
    aad.extend_from_slice(&core_length.to_le_bytes());
    aad.extend_from_slice(core_id.as_bytes());
    aad.extend_from_slice(&frk_version.to_le_bytes());
    aad.extend_from_slice(&monotonic_number.to_le_bytes());
    aad.extend_from_slice(&context_length.to_le_bytes());
    aad.extend_from_slice(context);
    Ok(aad)
}

fn validate_common<T: PreparationRecord>(
    record: &T,
    preparation_id: &str,
) -> Result<(), PreparationError> {
    if record.schema_version() != PREPARATION_SCHEMA_VERSION {
        return Err(PreparationError::UnsupportedSchemaVersion(
            record.schema_version(),
        ));
    }
    validate_opaque(record.core_id(), "Core ID")?;
    validate_opaque(preparation_id, "preparation ID")?;
    if record.required_frk_version() == 0 {
        return Err(PreparationError::InvalidFormat("FRK version"));
    }
    Ok(())
}

fn validate_expected_binding<T: PreparationRecord>(
    record: &T,
    expected_core_id: &str,
    expected_frk_version: u32,
) -> Result<(), PreparationError> {
    if record.core_id() != expected_core_id || record.required_frk_version() != expected_frk_version
    {
        return Err(PreparationError::InvalidFormat("record binding mismatch"));
    }
    Ok(())
}

fn validate_segment_references(
    references: &[PreparationSegmentReference],
) -> Result<(), PreparationError> {
    if references.len() > MAX_SEGMENT_REFERENCES {
        return Err(PreparationError::LimitExceeded("segment references"));
    }
    let mut indexes = HashSet::new();
    for reference in references {
        if !indexes.insert(reference.segment_index) {
            return Err(PreparationError::InvalidFormat("duplicate segment index"));
        }
        validate_hash(&reference.ciphertext_sha256)?;
        let item_count = usize::try_from(reference.item_count)
            .map_err(|_| PreparationError::LimitExceeded("segment item count"))?;
        let plaintext_bytes = usize::try_from(reference.plaintext_bytes)
            .map_err(|_| PreparationError::LimitExceeded("segment plaintext bytes"))?;
        if item_count == 0
            || item_count > MAX_SEGMENT_ITEMS
            || plaintext_bytes == 0
            || plaintext_bytes > MAX_DESCRIPTOR_SEGMENT_PLAINTEXT_SIZE
        {
            return Err(PreparationError::LimitExceeded("segment reference"));
        }
    }
    Ok(())
}

fn validate_optional_head(
    generation: Option<u64>,
    catalog_hash: Option<&str>,
) -> Result<(), PreparationError> {
    match (generation, catalog_hash) {
        (None, None) => Ok(()),
        (Some(generation), Some(hash)) if generation > 0 => validate_hash(hash),
        _ => Err(PreparationError::InvalidFormat("incomplete head tuple")),
    }
}

fn validate_optional_hash(value: Option<&str>) -> Result<(), PreparationError> {
    if let Some(value) = value {
        validate_hash(value)?;
    }
    Ok(())
}

fn validate_hash(value: &str) -> Result<(), PreparationError> {
    if value.len() != 64
        || !value
            .as_bytes()
            .iter()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    {
        return Err(PreparationError::InvalidFormat("SHA-256 hash"));
    }
    Ok(())
}

fn validate_opaque(value: &str, field: &'static str) -> Result<(), PreparationError> {
    validate_opaque_id(value).map_err(|_| PreparationError::InvalidFormat(field))
}

pub(super) fn publish_preparation_head_with_hook<F>(
    fs_dir: &Dir,
    encoded: &[u8],
    hook: &mut F,
) -> Result<(), PreparationError>
where
    F: FnMut(PublicationPhase) -> io::Result<()>,
{
    let (_, kind) = publication_identity(encoded)?;
    if kind != PreparationRecordKind::Head {
        return Err(PreparationError::InvalidFormat(
            "PREPARATION_HEAD requires a head envelope",
        ));
    }
    atomic_publish_in_with_hook(fs_dir, OsStr::new(PREPARATION_HEAD_FILE), encoded, hook)?;
    Ok(())
}

pub(super) fn publish_immutable_preparation_record_with_hook<F>(
    record_dir: &Dir,
    encoded: &[u8],
    hook: &mut F,
) -> Result<String, PreparationError>
where
    F: FnMut(PublicationPhase) -> io::Result<()>,
{
    let (monotonic_number, kind) = publication_identity(encoded)?;
    kind.immutable_suffix()?;
    let digest: [u8; 32] = Sha256::digest(encoded).into();
    let name = format!(
        "{monotonic_number:020}-{}.{}",
        hex_bytes(&digest),
        kind.immutable_suffix()?
    );
    publish_immutable_in_with_hook(record_dir, OsStr::new(&name), encoded, hook)?;
    Ok(name)
}

fn publication_identity(encoded: &[u8]) -> Result<(u64, PreparationRecordKind), PreparationError> {
    if encoded.len() < ENVELOPE_FIXED_HEADER_SIZE + TAG_LENGTH {
        return Err(PreparationError::InvalidFormat("truncated envelope"));
    }
    if &encoded[..8] != ENVELOPE_MAGIC {
        return Err(PreparationError::InvalidFormat("envelope magic"));
    }
    let envelope_version = u16::from_le_bytes(encoded[8..10].try_into().expect("fixed slice"));
    if envelope_version != PREPARATION_ENVELOPE_VERSION {
        return Err(PreparationError::UnsupportedEnvelopeVersion(
            envelope_version,
        ));
    }
    let kind = PreparationRecordKind::parse(encoded[10])?;
    if encoded.len() > kind.maximum_envelope_size() {
        return Err(PreparationError::LimitExceeded("preparation envelope"));
    }
    let schema_version = u16::from_le_bytes(encoded[11..13].try_into().expect("fixed slice"));
    if schema_version != PREPARATION_SCHEMA_VERSION {
        return Err(PreparationError::UnsupportedSchemaVersion(schema_version));
    }
    let monotonic_number = u64::from_le_bytes(encoded[17..25].try_into().expect("fixed slice"));
    let context_length = usize::from(u16::from_le_bytes(
        encoded[25..27].try_into().expect("fixed slice"),
    ));
    let ciphertext_length = usize::try_from(u32::from_le_bytes(
        encoded[39..43].try_into().expect("fixed slice"),
    ))
    .map_err(|_| PreparationError::LimitExceeded("ciphertext"))?;
    if context_length == 0
        || context_length > MAX_AAD_CONTEXT_BYTES
        || ciphertext_length < TAG_LENGTH
    {
        return Err(PreparationError::InvalidFormat("envelope lengths"));
    }
    let expected_length = ENVELOPE_FIXED_HEADER_SIZE
        .checked_add(context_length)
        .and_then(|length| length.checked_add(ciphertext_length))
        .ok_or(PreparationError::LimitExceeded("preparation envelope"))?;
    if encoded.len() != expected_length {
        return Err(PreparationError::InvalidFormat("envelope length"));
    }
    Ok((monotonic_number, kind))
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
