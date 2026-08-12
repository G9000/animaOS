//! Closed, independently bounded wire records for crash-resumable CoreFS preparation.

#[cfg(test)]
use std::cell::RefCell;
use std::collections::{BTreeMap, HashSet};
use std::ffi::OsStr;
use std::io::{self, Read};
#[cfg(test)]
use std::sync::atomic::{AtomicUsize, Ordering};
#[cfg(test)]
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use cap_std::fs::Dir;
use serde::{de::DeserializeOwned, Deserialize, Serialize, Serializer};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::bounded::{json_to_vec as bounded_json_to_vec, BoundedJsonError};
use crate::catalog::{
    encode_catalog_generation, CatalogGeneration, ContentHash, ObjectPhysicalName,
    WrappedObjectDekRecord, MAX_CATALOG_ENTRIES,
};
use crate::crypto::{
    generate_object_dek, unwrap_object_dek, CryptoError, FrkSubkeys, ObjectBaseAad, ObjectKeyAad,
    ObjectKind, NONCE_LENGTH,
};
use crate::envelope::{
    open_envelope_stream, BodyEncoding, EnvelopeMetadata, BODY_CHUNK_PLAINTEXT_SIZE,
    ENVELOPE_VERSION, MAX_METADATA_PLAINTEXT_SIZE, METADATA_SCHEMA_VERSION,
};
use crate::id::{validate_opaque_id, OpaqueId};
use crate::publication::{
    atomic_publish_in_with_hook, publish_immutable_in_with_hook, PublicationPhase,
};

const PREPARATION_SCHEMA_VERSION: u16 = 1;
const PREPARATION_ENVELOPE_VERSION: u16 = 1;
const PREPARATION_HEAD_FILE: &str = "PREPARATION_HEAD";
const PREPARATIONS_DIRECTORY: &str = "preparations";
const PREPARATION_QUARANTINE_DIRECTORY: &str = "preparation-quarantine";
const SNAPSHOTS_DIRECTORY: &str = "snapshots";
const DESCRIPTORS_DIRECTORY: &str = "descriptors";
const INTENT_DIRECTORY: &str = "intent";
const RECEIPTS_DIRECTORY: &str = "receipts";
const ENVELOPE_MAGIC: &[u8; 8] = b"APREPV1\0";
const ENVELOPE_FIXED_HEADER_SIZE: usize = 43;
const TAG_LENGTH: usize = 16;
const MAX_AAD_CONTEXT_BYTES: usize = 128;
const MAX_SCOPE_BYTES: usize = 64;
const MAX_SEGMENT_REFERENCES: usize = 1024;
const MAX_SEGMENT_ITEMS: usize = 1024;
const MAX_RECONCILIATION_PAGE_ITEMS: u32 = 128;
const MAX_RECONCILIATION_PAGE_BYTES: u32 = 64 * 1024;

#[cfg(test)]
thread_local! {
    static RECONCILIATION_INSTRUMENTATION: RefCell<Option<Arc<PreparationReconciliationTestInstrumentation>>> = const { RefCell::new(None) };
}

#[cfg(test)]
#[derive(Debug, Default)]
pub(super) struct PreparationReconciliationTestInstrumentation {
    descriptor_segment_reads: AtomicUsize,
}

#[cfg(test)]
impl PreparationReconciliationTestInstrumentation {
    pub(super) fn descriptor_segment_reads(&self) -> usize {
        self.descriptor_segment_reads.load(Ordering::SeqCst)
    }
}

#[cfg(test)]
struct ReconciliationInstrumentationGuard(
    Option<Arc<PreparationReconciliationTestInstrumentation>>,
);

#[cfg(test)]
impl ReconciliationInstrumentationGuard {
    fn install(instrumentation: Arc<PreparationReconciliationTestInstrumentation>) -> Self {
        let previous =
            RECONCILIATION_INSTRUMENTATION.with(|active| active.replace(Some(instrumentation)));
        Self(previous)
    }
}

#[cfg(test)]
impl Drop for ReconciliationInstrumentationGuard {
    fn drop(&mut self) {
        let previous = self.0.take();
        RECONCILIATION_INSTRUMENTATION.with(|active| {
            active.replace(previous);
        });
    }
}

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
    #[error("CoreFS preparation coordination failed: {0}")]
    Commit(#[from] super::CommitError),
    #[error("no active preparation exists")]
    Missing,
    #[error("the active preparation pointer is corrupt or bound to another Core")]
    CorruptPointer,
    #[error("the active preparation snapshot is missing")]
    MissingSnapshot,
    #[error("the active preparation snapshot is corrupt")]
    CorruptSnapshot,
    #[error("the active preparation pointer replays a stale snapshot")]
    StaleSnapshotReplay,
    #[error("the active preparation requires FRK {required}, not provided FRK {provided}")]
    WrongFrkVersion { required: u32, provided: u32 },
    #[error("the active preparation conflicts on {0}")]
    ActiveConflict(&'static str),
    #[error("the caller source state is older than the durable preparation")]
    StaleSourceState,
    #[error("the source mutation generation or inventory digest changed")]
    SourceChanged,
    #[error("the exact preparation pointer/snapshot compare-and-swap failed")]
    CasConflict,
    #[error("the exact expected validation head changed")]
    ValidationHeadConflict,
    #[error("the sealed final intent does not reconstruct the intended catalog")]
    FinalIntentMismatch,
    #[error("the deterministic preparation receipt conflicts with durable state")]
    ReceiptConflict,
    #[error("the preparation layout is missing or invalid")]
    InvalidLayout,
    #[error("the preparation references a missing {kind:?} segment {segment_index}")]
    MissingReferencedRecord {
        kind: PreparationReferenceKind,
        segment_index: u32,
    },
    #[error("the preparation references a corrupt {kind:?} segment {segment_index}")]
    CorruptReferencedRecord {
        kind: PreparationReferenceKind,
        segment_index: u32,
    },
    #[error("the preparation already contains a different revision/content identity for {object_id} revision {revision}")]
    LogicalRevisionConflict { object_id: String, revision: u64 },
    #[error("preparation converter validation failed: {0}")]
    Converter(#[from] super::converter::ValidationBatchError),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PreparationBeginRequest {
    pub(super) scope: String,
    pub(super) expected_validation_generation: Option<u64>,
    pub(super) expected_validation_catalog_sha256: Option<String>,
    pub(super) source_owner_id: String,
    pub(super) source_schema_version: u16,
    pub(super) source_mutation_generation: u64,
    pub(super) source_inventory_sha256: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PreparationCas {
    pub(super) pointer_sha256: String,
    pub(super) snapshot_sequence: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PreparationSealRequest {
    pub(super) source_mutation_generation: u64,
    pub(super) source_inventory_sha256: String,
    pub(super) folders: Vec<super::converter::ValidationBatchFolder>,
    pub(super) objects: Vec<PreparationIdentity>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PreparationFinalizeRequest {
    pub(super) preparation_id: String,
    pub(super) expected: PreparationCas,
    pub(super) source_mutation_generation: u64,
    pub(super) source_inventory_sha256: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PrepareObjectRequest {
    pub(super) object_id: String,
    pub(super) revision: u64,
    pub(super) object_key_epoch: u32,
    pub(super) kind: ObjectKind,
    pub(super) parent_id: String,
    pub(super) name: String,
    pub(super) content_type: String,
    pub(super) body_encoding: BodyEncoding,
    pub(super) body_length: u64,
    pub(super) content_sha256: String,
    pub(super) created_at: String,
    pub(super) updated_at: String,
    pub(super) source_character_count: Option<usize>,
    pub(super) references: Vec<String>,
    pub(super) policy: super::converter::ValidationBatchPolicy,
    pub(super) stable_role: Option<String>,
    pub(super) graph_metadata: BTreeMap<String, Value>,
    pub(super) source_fingerprint_sha256: String,
    pub(super) converter_format_version: u16,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum PrepareObjectDisposition {
    Prepared,
    Matched,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PreparedObjectSummary {
    pub(super) object_id: String,
    pub(super) revision: u64,
    pub(super) content_sha256: String,
    pub(super) preparation_ordinal: u64,
    pub(super) ciphertext_bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PrepareObjectOutcome {
    pub(super) status: PreparationStatus,
    pub(super) disposition: PrepareObjectDisposition,
    pub(super) prepared: PreparedObjectSummary,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct PreparationIdentity {
    pub(super) object_id: String,
    pub(super) revision: u64,
    pub(super) content_sha256: String,
    pub(super) preparation_ordinal: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct PreparationPageLimits {
    pub(super) max_items: u32,
    pub(super) max_bytes: u32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PreparationReconciliationRequest {
    pub(super) cursor: Option<PreparationReconciliationCursor>,
    pub(super) limits: PreparationPageLimits,
    pub(super) expected: Vec<PreparationIdentity>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct PreparationReconciliationCursor {
    pub(super) position: u64,
}

#[cfg(test)]
#[derive(Clone, Debug)]
pub(super) struct PreparationTestLimits {
    pub(super) descriptor_segment_items: usize,
    pub(super) max_object_plaintext_bytes: u64,
    pub(super) logical_plaintext_bytes: Option<u64>,
    pub(super) instrumentation: Option<Arc<super::PreparationTestInstrumentation>>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct PreparedObjectMetadata {
    pub(super) object_id: String,
    pub(super) revision: u64,
    pub(super) object_key_epoch: u32,
    #[serde(serialize_with = "serialize_object_kind")]
    pub(super) kind: ObjectKind,
    pub(super) parent_id: String,
    pub(super) name: String,
    pub(super) content_type: String,
    pub(super) body_encoding: BodyEncoding,
    pub(super) body_length: u64,
    pub(super) content_sha256: String,
    pub(super) created_at: String,
    pub(super) updated_at: String,
    pub(super) source_character_count: Option<usize>,
    pub(super) references: Vec<String>,
    #[serde(serialize_with = "serialize_validation_policy")]
    pub(super) policy: super::converter::ValidationBatchPolicy,
    pub(super) stable_role: Option<String>,
    pub(super) graph_metadata: BTreeMap<String, Value>,
    pub(super) source_fingerprint_sha256: String,
    pub(super) converter_format_version: u16,
    pub(super) preparation_ordinal: u64,
    pub(super) ciphertext_bytes: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct PreparationReconciliationPage {
    pub(super) prepared_count: u32,
    pub(super) total_plaintext_bytes: u64,
    pub(super) total_ciphertext_bytes: u64,
    pub(super) descriptor_manifest_root_sha256: String,
    pub(super) descriptor_segment_roots: Vec<String>,
    pub(super) items: Vec<PreparedObjectMetadata>,
    pub(super) missing: Vec<PreparationIdentity>,
    pub(super) conflicting: Vec<PreparationIdentity>,
    pub(super) next_cursor: Option<PreparationReconciliationCursor>,
    pub(super) encoded_bytes: u32,
}

fn serialize_object_kind<S>(value: &ObjectKind, serializer: S) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    serializer.serialize_str(value.as_str())
}

fn serialize_validation_policy<S>(
    value: &super::converter::ValidationBatchPolicy,
    serializer: S,
) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    serializer.serialize_str(policy_name(*value))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum PreparationOpenDisposition {
    Begun,
    Resumed,
    Reconciled,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum PreparationPublicationTarget {
    Object,
    Descriptor,
    Intent,
    Snapshot,
    Head,
    ValidationCatalog,
    ValidationHead,
    Receipt,
    Clear,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum PreparationReferenceKind {
    Descriptor,
    Intent,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct PreparationStatus {
    pub(super) preparation_id: String,
    pub(super) snapshot_sequence: u64,
    pub(super) snapshot_ciphertext_sha256: String,
    pub(super) pointer_sha256: String,
    pub(super) state: PreparationState,
    pub(super) source_schema_version: u16,
    pub(super) source_mutation_generation: u64,
    pub(super) source_inventory_sha256: String,
    pub(super) total_objects: u32,
    pub(super) total_plaintext_bytes: u64,
    pub(super) total_ciphertext_bytes: u64,
    pub(super) descriptor_manifest_root_sha256: String,
    pub(super) next_descriptor_segment: u32,
    pub(super) next_intent_segment: u32,
    pub(super) disposition: PreparationOpenDisposition,
}

pub(super) struct SealedPreparationRecord {
    encoded: Vec<u8>,
    kind: PreparationRecordKind,
    monotonic_number: u64,
}

impl SealedPreparationRecord {
    pub(super) fn as_bytes(&self) -> &[u8] {
        &self.encoded
    }
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

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
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
    pub(super) total_ciphertext_bytes: u64,
    pub(super) manifest_root_sha256: String,
    pub(super) manifest_segments: Vec<PreparationSegmentReference>,
    pub(super) final_intent_root_sha256: Option<String>,
    pub(super) final_intent_segments: Vec<PreparationSegmentReference>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) canonical_intent_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) intended_validation_generation: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) intended_validation_catalog_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) final_intent_entry_count: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) final_intent_folder_count: Option<u32>,
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
    pub(super) parent_id: String,
    pub(super) name: String,
    pub(super) content_type: String,
    pub(super) body_encoding: String,
    pub(super) body_length: u64,
    #[cfg(test)]
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) logical_body_length: Option<u64>,
    pub(super) created_at: String,
    pub(super) updated_at: String,
    pub(super) source_character_count: Option<u64>,
    pub(super) references: Vec<String>,
    pub(super) policy: String,
    pub(super) stable_role: Option<String>,
    pub(super) graph_metadata: BTreeMap<String, Value>,
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

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum FinalCatalogIntentEntry {
    Folder(FinalCatalogFolderIntent),
    Object(Box<FinalCatalogObjectIntent>),
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct FinalCatalogFolderIntent {
    stable_id: String,
    parent_id: Option<String>,
    name: String,
    role: Option<String>,
    policy: String,
    metadata: BTreeMap<String, Value>,
}

#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct FinalCatalogObjectIntent {
    stable_id: String,
    parent_id: String,
    name: String,
    object_kind: String,
    revision: u64,
    object_key_epoch: u32,
    content_sha256: String,
    content_type: String,
    body_encoding: String,
    body_length: u64,
    created_at: String,
    updated_at: String,
    source_character_count: Option<u64>,
    references: Vec<String>,
    policy: String,
    metadata: BTreeMap<String, Value>,
    source_fingerprint_sha256: String,
    converter_format_version: u16,
    preparation_ordinal: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
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

            pub(super) fn seal(
                &self,
                keys: &FrkSubkeys,
            ) -> Result<SealedPreparationRecord, PreparationError> {
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
        validate_optional_hash(self.canonical_intent_sha256.as_deref())?;
        validate_optional_hash(self.intended_validation_catalog_sha256.as_deref())?;
        if usize::try_from(self.total_objects)
            .map_err(|_| PreparationError::LimitExceeded("total objects"))?
            > MAX_CATALOG_ENTRIES
        {
            return Err(PreparationError::LimitExceeded("total objects"));
        }
        if (self.total_objects == 0)
            != (self.total_plaintext_bytes == 0
                && self.total_ciphertext_bytes == 0
                && self.manifest_segments.is_empty())
        {
            return Err(PreparationError::InvalidFormat(
                "snapshot descriptor accounting",
            ));
        }
        validate_segment_references(&self.manifest_segments)?;
        validate_segment_references(&self.final_intent_segments)?;
        let intent_count = self
            .final_intent_segments
            .iter()
            .try_fold(0_u32, |total, reference| {
                total.checked_add(reference.item_count)
            })
            .ok_or(PreparationError::LimitExceeded("final-intent entries"))?;
        let ready_fields = (
            self.final_intent_root_sha256.as_ref(),
            self.canonical_intent_sha256.as_ref(),
            self.intended_validation_generation,
            self.intended_validation_catalog_sha256.as_ref(),
            self.final_intent_entry_count,
            self.final_intent_folder_count,
        );
        match self.state {
            PreparationState::Ready => match ready_fields {
                (Some(_), Some(_), Some(generation), Some(_), Some(entries), Some(folders))
                    if generation > 0
                        && entries == intent_count
                        && folders <= entries
                        && entries.saturating_sub(folders) == self.total_objects => {}
                _ => return Err(PreparationError::InvalidFormat("ready snapshot intent")),
            },
            _ => {
                if self.canonical_intent_sha256.is_some()
                    || self.intended_validation_generation.is_some()
                    || self.intended_validation_catalog_sha256.is_some()
                    || self.final_intent_entry_count.is_some()
                    || self.final_intent_folder_count.is_some()
                {
                    return Err(PreparationError::InvalidFormat("non-ready snapshot intent"));
                }
            }
        }
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
        let mut previous_ordinal = None;
        for descriptor in &self.descriptors {
            descriptor.validate(self.required_frk_version)?;
            if !stable_ids.insert(descriptor.stable_id.as_str()) {
                return Err(PreparationError::InvalidFormat("duplicate stable ID"));
            }
            if !physical_names.insert(descriptor.physical_name.as_str()) {
                return Err(PreparationError::InvalidFormat("duplicate physical name"));
            }
            if previous_ordinal.is_some_and(|previous: u64| {
                previous.checked_add(1) != Some(descriptor.preparation_ordinal)
            }) {
                return Err(PreparationError::InvalidFormat(
                    "descriptor ordinals are not contiguous",
                ));
            }
            previous_ordinal = Some(descriptor.preparation_ordinal);
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
        let mut previous_ordinal = None;
        for entry in &self.entries {
            entry.validate()?;
            if !stable_ids.insert(entry.stable_id.as_str()) {
                return Err(PreparationError::InvalidFormat("duplicate stable ID"));
            }
            if previous_ordinal
                .is_some_and(|previous: u64| previous.checked_add(1) != Some(entry.ordinal))
            {
                return Err(PreparationError::InvalidFormat(
                    "final-intent ordinals are not contiguous",
                ));
            }
            previous_ordinal = Some(entry.ordinal);
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
        let body_encoding = parse_body_encoding(&self.body_encoding)?;
        let policy = parse_policy(&self.policy)?;
        let source_character_count = self
            .source_character_count
            .map(usize::try_from)
            .transpose()
            .map_err(|_| PreparationError::LimitExceeded("source character count"))?;
        super::converter::validate_converter_object_metadata(
            &super::converter::ConverterObjectMetadata {
                object_id: &self.stable_id,
                revision: self.revision,
                object_key_epoch: self.object_key_epoch,
                parent_id: &self.parent_id,
                name: &self.name,
                kind: ObjectKind::parse(&self.kind)?,
                content_type: &self.content_type,
                body_encoding,
                body_length: self.body_length,
                content_sha256: &self.content_sha256,
                created_at: &self.created_at,
                updated_at: &self.updated_at,
                source_character_count,
                references: &self.references,
                policy,
                stable_role: self.stable_role.as_deref(),
                graph_metadata: &self.graph_metadata,
            },
        )?;
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
        let digest: [u8; 32] = Sha256::digest(self.canonical_catalog_entry_json.as_bytes()).into();
        if hex_bytes(&digest) != self.canonical_catalog_entry_sha256 {
            return Err(PreparationError::InvalidFormat(
                "final-intent entry hash mismatch",
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
) -> Result<SealedPreparationRecord, PreparationError> {
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
    Ok(SealedPreparationRecord {
        encoded: envelope,
        kind: T::KIND,
        monotonic_number: record.monotonic_number(),
    })
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
    for (expected_index, reference) in references.iter().enumerate() {
        let expected_index = u32::try_from(expected_index)
            .map_err(|_| PreparationError::LimitExceeded("segment index"))?;
        if reference.segment_index != expected_index {
            return Err(PreparationError::InvalidFormat(
                "segment indexes are not contiguous",
            ));
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
    sealed: &SealedPreparationRecord,
    hook: &mut F,
) -> Result<(), PreparationError>
where
    F: FnMut(PublicationPhase) -> io::Result<()>,
{
    if sealed.kind != PreparationRecordKind::Head {
        return Err(PreparationError::InvalidFormat(
            "PREPARATION_HEAD requires a head envelope",
        ));
    }
    atomic_publish_in_with_hook(
        fs_dir,
        OsStr::new(PREPARATION_HEAD_FILE),
        sealed.as_bytes(),
        hook,
    )?;
    Ok(())
}

pub(super) fn publish_immutable_preparation_record_with_hook<F>(
    record_dir: &Dir,
    sealed: &SealedPreparationRecord,
    hook: &mut F,
) -> Result<String, PreparationError>
where
    F: FnMut(PublicationPhase) -> io::Result<()>,
{
    let suffix = sealed.kind.immutable_suffix()?;
    let digest: [u8; 32] = Sha256::digest(sealed.as_bytes()).into();
    let name = format!(
        "{:020}-{}.{}",
        sealed.monotonic_number,
        hex_bytes(&digest),
        suffix
    );
    publish_immutable_in_with_hook(record_dir, OsStr::new(&name), sealed.as_bytes(), hook)?;
    Ok(name)
}

struct PreparationLayout {
    snapshots: Dir,
    descriptors: Dir,
    intent: Dir,
    receipts: Dir,
}

struct LoadedPreparation {
    head: PreparationHeadRecord,
    snapshot: PreparationSnapshot,
    pointer_sha256: String,
}

impl super::CoreCommitCoordinator {
    pub(super) fn begin_or_resume_preparation(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationBeginRequest,
    ) -> Result<PreparationStatus, PreparationError> {
        self.begin_or_resume_preparation_with_hook(keys, request, &mut |_, _| Ok(()))
    }

    fn begin_or_resume_preparation_with_hook<F>(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationBeginRequest,
        hook: &mut F,
    ) -> Result<PreparationStatus, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        validate_begin_request(request)?;
        let _lease_operation = self.admit_lease_publication_operation()?;
        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let pointer = read_pointer_bytes(&self.fs_dir)?;
        let result = if let Some(pointer) = pointer {
            let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
            validate_resume_identity(&loaded.snapshot, request)?;
            Ok(status_from_loaded(
                &loaded,
                PreparationOpenDisposition::Resumed,
            )?)
        } else {
            self.begin_preparation_locked(&commit_lock, keys, request, hook)
        };
        drop(commit_lock);
        result
    }

    fn begin_preparation_locked<F>(
        &self,
        _commit_lock: &super::CoreCommitLock,
        keys: &FrkSubkeys,
        request: &PreparationBeginRequest,
        hook: &mut F,
    ) -> Result<PreparationStatus, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        let preparations = super::ensure_child_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
        let _quarantine =
            super::ensure_child_directory(&self.fs_dir, PREPARATION_QUARANTINE_DIRECTORY)?;
        let preparation_id =
            OpaqueId::generate().map_err(|_| PreparationError::InvalidFormat("preparation ID"))?;
        let layout = create_preparation_layout(&preparations, preparation_id.as_str())?;
        let now = unix_time_millis()?;
        let snapshot = PreparationSnapshot {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: self.core_id.clone(),
            preparation_id: preparation_id.as_str().to_owned(),
            sequence: 1,
            state: PreparationState::Collecting,
            scope: request.scope.clone(),
            required_frk_version: keys.frk_version(),
            created_at_unix_ms: now,
            updated_at_unix_ms: now,
            expected_validation_generation: request.expected_validation_generation,
            expected_validation_catalog_sha256: request.expected_validation_catalog_sha256.clone(),
            source_owner_id: request.source_owner_id.clone(),
            source_inventory_version: request.source_schema_version,
            source_mutation_generation: request.source_mutation_generation,
            source_inventory_sha256: request.source_inventory_sha256.clone(),
            total_objects: 0,
            total_plaintext_bytes: 0,
            total_ciphertext_bytes: 0,
            manifest_root_sha256: empty_manifest_root(),
            manifest_segments: Vec::new(),
            final_intent_root_sha256: None,
            final_intent_segments: Vec::new(),
            canonical_intent_sha256: None,
            intended_validation_generation: None,
            intended_validation_catalog_sha256: None,
            final_intent_entry_count: None,
            final_intent_folder_count: None,
        };
        let sealed_snapshot = snapshot.seal(keys)?;
        let snapshot_ciphertext_sha256 = sha256_hex(sealed_snapshot.as_bytes());
        publish_immutable_preparation_record_with_hook(
            &layout.snapshots,
            &sealed_snapshot,
            &mut |phase| hook(PreparationPublicationTarget::Snapshot, phase),
        )?;
        let head = PreparationHeadRecord {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: self.core_id.clone(),
            preparation_id: preparation_id.as_str().to_owned(),
            snapshot_sequence: snapshot.sequence,
            snapshot_ciphertext_sha256: snapshot_ciphertext_sha256.clone(),
            envelope_version: PREPARATION_ENVELOPE_VERSION,
            required_frk_version: keys.frk_version(),
        };
        let sealed_head = head.seal(keys)?;
        publish_preparation_head_with_hook(&self.fs_dir, &sealed_head, &mut |phase| {
            hook(PreparationPublicationTarget::Head, phase)
        })?;
        status_from_loaded(
            &LoadedPreparation {
                head,
                snapshot,
                pointer_sha256: sha256_hex(sealed_head.as_bytes()),
            },
            PreparationOpenDisposition::Begun,
        )
    }

    pub(super) fn load_preparation_status(
        &self,
        keys: &FrkSubkeys,
    ) -> Result<PreparationStatus, PreparationError> {
        let _lease_operation = self.admit_lease_publication_operation()?;
        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let pointer = read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::Missing)?;
        let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
        let status = status_from_loaded(&loaded, PreparationOpenDisposition::Resumed);
        drop(commit_lock);
        status
    }

    pub(super) fn reconcile_preparation_source(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PreparationBeginRequest,
    ) -> Result<PreparationStatus, PreparationError> {
        self.reconcile_preparation_source_with_hook(keys, expected, request, &mut |_, _| Ok(()))
    }

    pub(super) fn reconcile_preparation_source_with_hook<F>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PreparationBeginRequest,
        hook: &mut F,
    ) -> Result<PreparationStatus, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        validate_begin_request(request)?;
        validate_hash(&expected.pointer_sha256)?;
        if expected.snapshot_sequence == 0 {
            return Err(PreparationError::InvalidFormat("snapshot sequence"));
        }
        let _lease_operation = self.admit_lease_publication_operation()?;
        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let pointer = read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
        if loaded.pointer_sha256 != expected.pointer_sha256
            || loaded.snapshot.sequence != expected.snapshot_sequence
        {
            return Err(PreparationError::CasConflict);
        }
        validate_reconciliation_identity(&loaded.snapshot, request)?;
        if request.source_mutation_generation < loaded.snapshot.source_mutation_generation {
            return Err(PreparationError::StaleSourceState);
        }
        if request.source_mutation_generation == loaded.snapshot.source_mutation_generation {
            if request.source_inventory_sha256 != loaded.snapshot.source_inventory_sha256 {
                return Err(PreparationError::ActiveConflict("source inventory"));
            }
            return status_from_loaded(&loaded, PreparationOpenDisposition::Resumed);
        }
        if !matches!(
            loaded.snapshot.state,
            PreparationState::Collecting | PreparationState::Ready
        ) {
            return Err(PreparationError::ActiveConflict("preparation state"));
        }
        let preparations = open_required_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
        let _quarantine = open_required_directory(&self.fs_dir, PREPARATION_QUARANTINE_DIRECTORY)?;
        let layout = open_preparation_layout(&preparations, &loaded.snapshot.preparation_id)?;
        let mut next_snapshot = loaded.snapshot.clone();
        next_snapshot.sequence = next_snapshot
            .sequence
            .checked_add(1)
            .ok_or(PreparationError::LimitExceeded("snapshot sequence"))?;
        next_snapshot.updated_at_unix_ms =
            unix_time_millis()?.max(next_snapshot.created_at_unix_ms);
        next_snapshot.source_mutation_generation = request.source_mutation_generation;
        next_snapshot.source_inventory_sha256 = request.source_inventory_sha256.clone();
        next_snapshot.state = PreparationState::Collecting;
        next_snapshot.final_intent_root_sha256 = None;
        next_snapshot.final_intent_segments.clear();
        clear_sealed_intent_metadata(&mut next_snapshot);
        let sealed_snapshot = next_snapshot.seal(keys)?;
        let snapshot_ciphertext_sha256 = sha256_hex(sealed_snapshot.as_bytes());
        publish_immutable_preparation_record_with_hook(
            &layout.snapshots,
            &sealed_snapshot,
            &mut |phase| hook(PreparationPublicationTarget::Snapshot, phase),
        )?;

        let current_pointer =
            read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let (current_head, current_pointer_sha256) =
            authenticate_pointer(&current_pointer, keys, &self.core_id)?;
        if current_pointer_sha256 != expected.pointer_sha256
            || current_head.snapshot_sequence != expected.snapshot_sequence
        {
            return Err(PreparationError::CasConflict);
        }
        let next_head = PreparationHeadRecord {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: self.core_id.clone(),
            preparation_id: loaded.snapshot.preparation_id,
            snapshot_sequence: next_snapshot.sequence,
            snapshot_ciphertext_sha256: snapshot_ciphertext_sha256.clone(),
            envelope_version: PREPARATION_ENVELOPE_VERSION,
            required_frk_version: keys.frk_version(),
        };
        let sealed_head = next_head.seal(keys)?;
        publish_preparation_head_with_hook(&self.fs_dir, &sealed_head, &mut |phase| {
            hook(PreparationPublicationTarget::Head, phase)
        })?;
        let status = status_from_loaded(
            &LoadedPreparation {
                head: next_head,
                snapshot: next_snapshot,
                pointer_sha256: sha256_hex(sealed_head.as_bytes()),
            },
            PreparationOpenDisposition::Reconciled,
        );
        drop(commit_lock);
        status
    }

    pub(super) fn prepare_object<R: Read>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PrepareObjectRequest,
        body: &mut R,
    ) -> Result<PrepareObjectOutcome, PreparationError> {
        self.prepare_object_inner(
            keys,
            expected,
            request,
            body,
            MAX_SEGMENT_ITEMS,
            u64::MAX,
            #[cfg(test)]
            None,
            #[cfg(test)]
            None,
            &mut |_, _| Ok(()),
        )
    }

    #[cfg(test)]
    pub(super) fn prepare_object_with_limits<R: Read>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PrepareObjectRequest,
        body: &mut R,
        limits: PreparationTestLimits,
    ) -> Result<PrepareObjectOutcome, PreparationError> {
        self.prepare_object_inner(
            keys,
            expected,
            request,
            body,
            limits.descriptor_segment_items,
            limits.max_object_plaintext_bytes,
            limits.logical_plaintext_bytes,
            limits.instrumentation.as_deref(),
            &mut |_, _| Ok(()),
        )
    }

    #[cfg(test)]
    pub(super) fn prepare_object_with_hook<R, F>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PrepareObjectRequest,
        body: &mut R,
        hook: &mut F,
    ) -> Result<PrepareObjectOutcome, PreparationError>
    where
        R: Read,
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        self.prepare_object_inner(
            keys,
            expected,
            request,
            body,
            MAX_SEGMENT_ITEMS,
            u64::MAX,
            None,
            None,
            hook,
        )
    }

    fn prepare_object_inner<R, F>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PrepareObjectRequest,
        body: &mut R,
        descriptor_segment_items: usize,
        max_object_plaintext_bytes: u64,
        #[cfg(test)] logical_plaintext_bytes: Option<u64>,
        #[cfg(test)] instrumentation: Option<&super::PreparationTestInstrumentation>,
        hook: &mut F,
    ) -> Result<PrepareObjectOutcome, PreparationError>
    where
        R: Read,
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        validate_prepare_object_request(request, max_object_plaintext_bytes)?;
        validate_preparation_cas(expected)?;
        if descriptor_segment_items == 0 || descriptor_segment_items > MAX_SEGMENT_ITEMS {
            return Err(PreparationError::LimitExceeded("descriptor segment items"));
        }
        let _lease_operation = self.admit_lease_publication_operation()?;

        {
            let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
            self.validate_pinned_layout()?;
            let pointer = read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
            let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
            validate_loaded_cas(&loaded, expected)?;
            if loaded.snapshot.state != PreparationState::Collecting {
                return Err(PreparationError::ActiveConflict("preparation state"));
            }
            let preparations = open_required_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
            let layout = open_preparation_layout(&preparations, &loaded.snapshot.preparation_id)?;
            if let Some(descriptor) = find_descriptor(&layout, &loaded.snapshot, keys, request)? {
                let prepared = prepared_revision_from_descriptor(&descriptor)?;
                self.validate_prepared_revision_file(&prepared)?;
                let outcome = matched_object_outcome(&loaded, request, &descriptor);
                drop(commit_lock);
                return outcome;
            }
            drop(commit_lock);
        }

        #[cfg(test)]
        let _plaintext_body = instrumentation
            .map(|instrumentation| {
                usize::try_from(request.body_length)
                    .map(|bytes| instrumentation.retain_plaintext_body(bytes))
            })
            .transpose()
            .map_err(|_| PreparationError::LimitExceeded("test plaintext body"))?;
        let metadata = envelope_metadata_for_request(request)?;
        let envelope_metadata_sha256 = envelope_metadata_sha256(&metadata)?;
        let object_key = generate_object_dek().map_err(super::CommitError::from)?;
        let aad = ObjectBaseAad::new(
            &self.core_id,
            &request.object_id,
            request.kind,
            ENVELOPE_VERSION,
            request.object_key_epoch,
            request.revision,
        )
        .map_err(super::CommitError::from)?;
        let prepared = self.prepare_plaintext_object_revision_with_hook(
            keys,
            &object_key,
            &aad,
            &metadata,
            body,
            #[cfg(test)]
            instrumentation,
            &mut |phase| hook(PreparationPublicationTarget::Object, phase),
        )?;
        self.validate_prepared_revision_file(&prepared)?;
        let descriptor = descriptor_from_prepared(
            request,
            &prepared,
            &envelope_metadata_sha256,
            0,
            #[cfg(test)]
            logical_plaintext_bytes,
        )?;

        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let pointer = read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
        validate_loaded_cas(&loaded, expected)?;
        if loaded.snapshot.state != PreparationState::Collecting {
            return Err(PreparationError::ActiveConflict("preparation state"));
        }
        let preparations = open_required_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
        let layout = open_preparation_layout(&preparations, &loaded.snapshot.preparation_id)?;
        if let Some(existing) = find_descriptor(&layout, &loaded.snapshot, keys, request)? {
            let outcome = matched_object_outcome(&loaded, request, &existing);
            drop(commit_lock);
            return outcome;
        }

        let next_ordinal = u64::from(loaded.snapshot.total_objects);
        let mut descriptor = descriptor;
        descriptor.preparation_ordinal = next_ordinal;
        let (segment, replace_last) = next_descriptor_segment(
            &layout,
            &loaded.snapshot,
            keys,
            descriptor,
            descriptor_segment_items,
        )?;
        let segment_plaintext_bytes = u32::try_from(segment.encode()?.len())
            .map_err(|_| PreparationError::LimitExceeded("descriptor segment plaintext"))?;
        let sealed_segment = segment.seal(keys)?;
        let segment_ciphertext_sha256 = sha256_hex(sealed_segment.as_bytes());
        publish_immutable_preparation_record_with_hook(
            &layout.descriptors,
            &sealed_segment,
            &mut |phase| hook(PreparationPublicationTarget::Descriptor, phase),
        )?;

        let mut next_snapshot = loaded.snapshot.clone();
        let next_total_objects = next_snapshot
            .total_objects
            .checked_add(1)
            .ok_or(PreparationError::LimitExceeded("total objects"))?;
        if usize::try_from(next_total_objects)
            .map_err(|_| PreparationError::LimitExceeded("total objects"))?
            > MAX_CATALOG_ENTRIES
        {
            return Err(PreparationError::LimitExceeded("total objects"));
        }
        next_snapshot.total_objects = next_total_objects;
        #[cfg(not(test))]
        let accounted_plaintext_bytes = request.body_length;
        #[cfg(test)]
        let accounted_plaintext_bytes = logical_plaintext_bytes.unwrap_or(request.body_length);
        next_snapshot.total_plaintext_bytes = next_snapshot
            .total_plaintext_bytes
            .checked_add(accounted_plaintext_bytes)
            .ok_or(PreparationError::LimitExceeded("total plaintext bytes"))?;
        next_snapshot.total_ciphertext_bytes = next_snapshot
            .total_ciphertext_bytes
            .checked_add(prepared.encoded_size)
            .ok_or(PreparationError::LimitExceeded("total ciphertext bytes"))?;
        let reference = PreparationSegmentReference {
            segment_index: segment.segment_index,
            ciphertext_sha256: segment_ciphertext_sha256,
            item_count: u32::try_from(segment.descriptors.len())
                .map_err(|_| PreparationError::LimitExceeded("descriptor segment items"))?,
            plaintext_bytes: segment_plaintext_bytes,
        };
        if replace_last {
            *next_snapshot
                .manifest_segments
                .last_mut()
                .ok_or(PreparationError::CorruptSnapshot)? = reference;
        } else {
            if next_snapshot.manifest_segments.len() >= MAX_SEGMENT_REFERENCES {
                return Err(PreparationError::LimitExceeded(
                    "descriptor segment references",
                ));
            }
            next_snapshot.manifest_segments.push(reference);
        }
        next_snapshot.manifest_root_sha256 = manifest_root(&next_snapshot.manifest_segments);
        next_snapshot.final_intent_root_sha256 = None;
        next_snapshot.final_intent_segments.clear();
        clear_sealed_intent_metadata(&mut next_snapshot);
        next_snapshot.sequence = next_snapshot
            .sequence
            .checked_add(1)
            .ok_or(PreparationError::LimitExceeded("snapshot sequence"))?;
        next_snapshot.updated_at_unix_ms =
            unix_time_millis()?.max(next_snapshot.created_at_unix_ms);
        let sealed_snapshot = next_snapshot.seal(keys)?;
        let snapshot_ciphertext_sha256 = sha256_hex(sealed_snapshot.as_bytes());
        publish_immutable_preparation_record_with_hook(
            &layout.snapshots,
            &sealed_snapshot,
            &mut |phase| hook(PreparationPublicationTarget::Snapshot, phase),
        )?;

        let current_pointer =
            read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let (current_head, current_pointer_sha256) =
            authenticate_pointer(&current_pointer, keys, &self.core_id)?;
        if current_pointer_sha256 != expected.pointer_sha256
            || current_head.snapshot_sequence != expected.snapshot_sequence
        {
            return Err(PreparationError::CasConflict);
        }
        let next_head = PreparationHeadRecord {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: self.core_id.clone(),
            preparation_id: loaded.snapshot.preparation_id,
            snapshot_sequence: next_snapshot.sequence,
            snapshot_ciphertext_sha256: snapshot_ciphertext_sha256.clone(),
            envelope_version: PREPARATION_ENVELOPE_VERSION,
            required_frk_version: keys.frk_version(),
        };
        let sealed_head = next_head.seal(keys)?;
        publish_preparation_head_with_hook(&self.fs_dir, &sealed_head, &mut |phase| {
            hook(PreparationPublicationTarget::Head, phase)
        })?;
        let status = status_from_loaded(
            &LoadedPreparation {
                head: next_head,
                snapshot: next_snapshot,
                pointer_sha256: sha256_hex(sealed_head.as_bytes()),
            },
            PreparationOpenDisposition::Reconciled,
        )?;
        drop(commit_lock);
        Ok(PrepareObjectOutcome {
            status,
            disposition: PrepareObjectDisposition::Prepared,
            prepared: PreparedObjectSummary {
                object_id: request.object_id.clone(),
                revision: request.revision,
                content_sha256: request.content_sha256.clone(),
                preparation_ordinal: next_ordinal,
                ciphertext_bytes: prepared.encoded_size,
            },
        })
    }

    pub(super) fn stage_final_intent(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        entries: &[FinalIntentEntry],
    ) -> Result<PreparationStatus, PreparationError> {
        self.stage_final_intent_inner(keys, expected, entries, MAX_SEGMENT_ITEMS, &mut |_, _| {
            Ok(())
        })
    }

    #[cfg(test)]
    pub(super) fn stage_final_intent_with_limits(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        entries: &[FinalIntentEntry],
        segment_items: usize,
    ) -> Result<PreparationStatus, PreparationError> {
        self.stage_final_intent_inner(keys, expected, entries, segment_items, &mut |_, _| Ok(()))
    }

    fn stage_final_intent_inner<F>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        entries: &[FinalIntentEntry],
        segment_items: usize,
        hook: &mut F,
    ) -> Result<PreparationStatus, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        validate_preparation_cas(expected)?;
        validate_final_intent_entries(entries, segment_items)?;
        let _lease_operation = self.admit_lease_publication_operation()?;
        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let pointer = read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
        validate_loaded_cas(&loaded, expected)?;
        if loaded.snapshot.state != PreparationState::Collecting {
            return Err(PreparationError::ActiveConflict("preparation state"));
        }
        let preparations = open_required_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
        let layout = open_preparation_layout(&preparations, &loaded.snapshot.preparation_id)?;
        let segments = final_intent_segments(&loaded.snapshot, entries, segment_items)?;
        let mut references = Vec::with_capacity(segments.len());
        for segment in segments {
            let plaintext_bytes = u32::try_from(segment.encode()?.len())
                .map_err(|_| PreparationError::LimitExceeded("final-intent segment"))?;
            let sealed = segment.seal(keys)?;
            let ciphertext_sha256 = sha256_hex(sealed.as_bytes());
            publish_immutable_preparation_record_with_hook(
                &layout.intent,
                &sealed,
                &mut |phase| hook(PreparationPublicationTarget::Intent, phase),
            )?;
            references.push(PreparationSegmentReference {
                segment_index: segment.segment_index,
                ciphertext_sha256,
                item_count: u32::try_from(segment.entries.len())
                    .map_err(|_| PreparationError::LimitExceeded("final-intent segment items"))?,
                plaintext_bytes,
            });
        }

        let mut next_snapshot = loaded.snapshot.clone();
        next_snapshot.final_intent_root_sha256 = Some(manifest_root(&references));
        next_snapshot.final_intent_segments = references;
        next_snapshot.sequence = next_snapshot
            .sequence
            .checked_add(1)
            .ok_or(PreparationError::LimitExceeded("snapshot sequence"))?;
        next_snapshot.updated_at_unix_ms =
            unix_time_millis()?.max(next_snapshot.created_at_unix_ms);
        let sealed_snapshot = next_snapshot.seal(keys)?;
        let snapshot_ciphertext_sha256 = sha256_hex(sealed_snapshot.as_bytes());
        publish_immutable_preparation_record_with_hook(
            &layout.snapshots,
            &sealed_snapshot,
            &mut |phase| hook(PreparationPublicationTarget::Snapshot, phase),
        )?;

        let current_pointer =
            read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let (current_head, current_pointer_sha256) =
            authenticate_pointer(&current_pointer, keys, &self.core_id)?;
        if current_pointer_sha256 != expected.pointer_sha256
            || current_head.snapshot_sequence != expected.snapshot_sequence
        {
            return Err(PreparationError::CasConflict);
        }
        let next_head = PreparationHeadRecord {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: self.core_id.clone(),
            preparation_id: loaded.snapshot.preparation_id,
            snapshot_sequence: next_snapshot.sequence,
            snapshot_ciphertext_sha256: snapshot_ciphertext_sha256.clone(),
            envelope_version: PREPARATION_ENVELOPE_VERSION,
            required_frk_version: keys.frk_version(),
        };
        let sealed_head = next_head.seal(keys)?;
        publish_preparation_head_with_hook(&self.fs_dir, &sealed_head, &mut |phase| {
            hook(PreparationPublicationTarget::Head, phase)
        })?;
        let status = status_from_loaded(
            &LoadedPreparation {
                head: next_head,
                snapshot: next_snapshot,
                pointer_sha256: sha256_hex(sealed_head.as_bytes()),
            },
            PreparationOpenDisposition::Reconciled,
        );
        drop(commit_lock);
        status
    }

    pub(super) fn seal_preparation(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PreparationSealRequest,
    ) -> Result<PreparationStatus, PreparationError> {
        self.seal_preparation_with_hook(keys, expected, request, &mut |_, _| Ok(()))
    }

    fn seal_preparation_with_hook<F>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        request: &PreparationSealRequest,
        hook: &mut F,
    ) -> Result<PreparationStatus, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        validate_preparation_cas(expected)?;
        validate_seal_request(request)?;
        let _lease_operation = self.admit_lease_publication_operation()?;
        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let pointer = read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
        validate_loaded_cas(&loaded, expected)?;
        if loaded.snapshot.state != PreparationState::Collecting {
            return Err(PreparationError::ActiveConflict("preparation state"));
        }
        validate_source_fence(
            &loaded.snapshot,
            request.source_mutation_generation,
            &request.source_inventory_sha256,
        )?;
        let current = self.load_validation_snapshot(keys)?;
        validate_expected_validation_head(&loaded.snapshot, current.as_ref())?;

        let preparations = open_required_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
        let layout = open_preparation_layout(&preparations, &loaded.snapshot.preparation_id)?;
        let descriptors = read_all_descriptors(&layout, &loaded.snapshot, keys)?;
        validate_descriptor_coverage(&descriptors, &request.objects)?;
        validate_descriptor_revision_preconditions(
            current.as_ref().map(|snapshot| snapshot.catalog()),
            &descriptors,
        )?;
        let next_generation = current.as_ref().map_or(Ok(1), |snapshot| {
            snapshot
                .head()
                .generation()
                .checked_add(1)
                .ok_or(PreparationError::LimitExceeded("validation generation"))
        })?;
        let catalog = build_catalog_from_inputs(
            next_generation,
            &request.folders,
            &descriptors,
            &request.objects,
        )?;
        let catalog_sha256 = canonical_catalog_sha256(&catalog)?;
        let entries = final_intent_entries(&request.folders, &descriptors)?;
        let canonical_intent_sha256 = canonical_intent_sha256(&entries);
        let segments = final_intent_segments(&loaded.snapshot, &entries, MAX_SEGMENT_ITEMS)?;
        let mut references = Vec::with_capacity(segments.len());
        for segment in segments {
            let plaintext_bytes = u32::try_from(segment.encode()?.len())
                .map_err(|_| PreparationError::LimitExceeded("final-intent segment"))?;
            let sealed = segment.seal(keys)?;
            let ciphertext_sha256 = sha256_hex(sealed.as_bytes());
            publish_immutable_preparation_record_with_hook(
                &layout.intent,
                &sealed,
                &mut |phase| hook(PreparationPublicationTarget::Intent, phase),
            )?;
            references.push(PreparationSegmentReference {
                segment_index: segment.segment_index,
                ciphertext_sha256,
                item_count: u32::try_from(segment.entries.len())
                    .map_err(|_| PreparationError::LimitExceeded("final-intent segment items"))?,
                plaintext_bytes,
            });
        }

        let mut next_snapshot = loaded.snapshot.clone();
        next_snapshot.state = PreparationState::Ready;
        next_snapshot.final_intent_root_sha256 = Some(manifest_root(&references));
        next_snapshot.final_intent_segments = references;
        next_snapshot.canonical_intent_sha256 = Some(canonical_intent_sha256);
        next_snapshot.intended_validation_generation = Some(next_generation);
        next_snapshot.intended_validation_catalog_sha256 = Some(catalog_sha256);
        next_snapshot.final_intent_entry_count = Some(
            u32::try_from(entries.len())
                .map_err(|_| PreparationError::LimitExceeded("final-intent entries"))?,
        );
        next_snapshot.final_intent_folder_count = Some(
            u32::try_from(request.folders.len())
                .map_err(|_| PreparationError::LimitExceeded("final-intent folders"))?,
        );
        next_snapshot.sequence = next_snapshot
            .sequence
            .checked_add(1)
            .ok_or(PreparationError::LimitExceeded("snapshot sequence"))?;
        next_snapshot.updated_at_unix_ms =
            unix_time_millis()?.max(next_snapshot.created_at_unix_ms);
        let result = self.publish_preparation_snapshot_and_head_locked(
            keys,
            expected,
            loaded,
            &layout,
            next_snapshot,
            hook,
        );
        drop(commit_lock);
        result
    }

    pub(super) fn finalize_preparation(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationFinalizeRequest,
    ) -> Result<PreparationReceipt, PreparationError> {
        self.finalize_preparation_with_hook(keys, request, &mut |_, _| Ok(()))
    }

    #[cfg(test)]
    pub(super) fn finalize_preparation_with_hook<F>(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationFinalizeRequest,
        hook: &mut F,
    ) -> Result<PreparationReceipt, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        self.finalize_preparation_with_hook_inner(keys, request, hook)
    }

    #[cfg(not(test))]
    fn finalize_preparation_with_hook<F>(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationFinalizeRequest,
        hook: &mut F,
    ) -> Result<PreparationReceipt, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        self.finalize_preparation_with_hook_inner(keys, request, hook)
    }

    fn finalize_preparation_with_hook_inner<F>(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationFinalizeRequest,
        hook: &mut F,
    ) -> Result<PreparationReceipt, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        validate_finalize_request(request)?;
        let _lease_operation = self.admit_lease_publication_operation()?;
        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let preparations = open_required_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
        let layout = open_preparation_layout(&preparations, &request.preparation_id)?;
        let Some(pointer) = read_pointer_bytes(&self.fs_dir)? else {
            let receipt = load_completed_receipt(
                self,
                &layout,
                keys,
                &request.preparation_id,
                &request.expected,
            )?;
            drop(commit_lock);
            return Ok(receipt);
        };
        let loaded = self.load_active_preparation_locked(&commit_lock, keys, pointer)?;
        if loaded.snapshot.preparation_id != request.preparation_id {
            return Err(PreparationError::CasConflict);
        }
        validate_loaded_cas(&loaded, &request.expected)?;
        if loaded.snapshot.state != PreparationState::Ready {
            return Err(PreparationError::ActiveConflict("preparation state"));
        }
        validate_source_fence(
            &loaded.snapshot,
            request.source_mutation_generation,
            &request.source_inventory_sha256,
        )?;
        let (catalog, prepared, descriptors) =
            reconstruct_sealed_catalog(&layout, &loaded.snapshot, keys)?;
        let intended_generation = loaded
            .snapshot
            .intended_validation_generation
            .ok_or(PreparationError::FinalIntentMismatch)?;
        if catalog.generation() != intended_generation
            || canonical_catalog_sha256(&catalog)?
                != loaded
                    .snapshot
                    .intended_validation_catalog_sha256
                    .as_deref()
                    .ok_or(PreparationError::FinalIntentMismatch)?
        {
            return Err(PreparationError::FinalIntentMismatch);
        }

        if self.load_pointer_head(super::HEAD_FILE)?.is_some()
            || self
                .load_pointer_head(super::CUTOVER_RECEIPT_FILE)?
                .is_some()
            || self
                .load_pointer_head(super::CUTOVER_COMPLETE_FILE)?
                .is_some()
        {
            return Err(super::CommitError::CoreAlreadyInitialized.into());
        }
        let current = self.load_validation_snapshot(keys)?;
        if current
            .as_ref()
            .is_some_and(|snapshot| snapshot.catalog().cutover_marker().is_some())
        {
            return Err(super::CommitError::InvalidCutoverTransition.into());
        }
        let committed_head = if current.as_ref().is_some_and(|snapshot| {
            snapshot.head().generation() == intended_generation
                && canonical_catalog_sha256(snapshot.catalog()).ok().as_deref()
                    == loaded
                        .snapshot
                        .intended_validation_catalog_sha256
                        .as_deref()
        }) {
            current
                .as_ref()
                .expect("matching validation snapshot exists")
                .head()
                .clone()
        } else {
            validate_expected_validation_head(&loaded.snapshot, current.as_ref())?;
            validate_descriptor_revision_preconditions(
                current.as_ref().map(|snapshot| snapshot.catalog()),
                &descriptors,
            )?;
            for (revision, descriptor) in prepared.iter().zip(&descriptors) {
                self.validate_prepared_revision_file(revision)?;
                validate_prepared_descriptor_envelope(self, keys, descriptor, revision)?;
            }
            let preconditions = match current.as_ref() {
                Some(snapshot) => super::converter::full_graph_preconditions(
                    snapshot.catalog(),
                    catalog.entries(),
                )?,
                None => Vec::new(),
            };
            if let Some(snapshot) = current.as_ref() {
                super::validate_preconditions(Some(snapshot.catalog()), &preconditions)?;
                super::validate_precondition_coverage(
                    snapshot.catalog(),
                    &catalog,
                    &preconditions,
                )?;
            }
            super::validate_prepared_revisions(
                &self.objects_dir,
                keys,
                &self.core_id,
                (
                    current.as_ref().map(|snapshot| snapshot.catalog()),
                    &catalog,
                ),
                &prepared,
                None,
                #[cfg(test)]
                None,
            )?;
            let (head, _, _, _, _) = self.publish_catalog_pointer_with_hook(
                keys,
                &catalog,
                super::VALIDATION_HEAD_FILE,
                false,
                &mut |point| map_validation_publication_hook(point, hook),
            )?;
            let published = self
                .load_validation_snapshot(keys)?
                .ok_or(PreparationError::ValidationHeadConflict)?;
            if published.head() != &head
                || canonical_catalog_sha256(published.catalog())?
                    != loaded
                        .snapshot
                        .intended_validation_catalog_sha256
                        .as_deref()
                        .ok_or(PreparationError::FinalIntentMismatch)?
            {
                return Err(PreparationError::FinalIntentMismatch);
            }
            head
        };

        let receipt = completed_receipt(&loaded, keys, &committed_head)?;
        publish_or_verify_receipt(&layout, keys, &receipt, hook)?;
        clear_preparation_head_exact(&self.fs_dir, &loaded.pointer_sha256, hook)?;
        drop(commit_lock);
        Ok(receipt)
    }

    fn publish_preparation_snapshot_and_head_locked<F>(
        &self,
        keys: &FrkSubkeys,
        expected: &PreparationCas,
        loaded: LoadedPreparation,
        layout: &PreparationLayout,
        next_snapshot: PreparationSnapshot,
        hook: &mut F,
    ) -> Result<PreparationStatus, PreparationError>
    where
        F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
    {
        let sealed_snapshot = next_snapshot.seal(keys)?;
        let snapshot_ciphertext_sha256 = sha256_hex(sealed_snapshot.as_bytes());
        publish_immutable_preparation_record_with_hook(
            &layout.snapshots,
            &sealed_snapshot,
            &mut |phase| hook(PreparationPublicationTarget::Snapshot, phase),
        )?;
        let current_pointer =
            read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::CasConflict)?;
        let (current_head, current_pointer_sha256) =
            authenticate_pointer(&current_pointer, keys, &self.core_id)?;
        if current_pointer_sha256 != expected.pointer_sha256
            || current_head.snapshot_sequence != expected.snapshot_sequence
        {
            return Err(PreparationError::CasConflict);
        }
        let next_head = PreparationHeadRecord {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: self.core_id.clone(),
            preparation_id: loaded.snapshot.preparation_id,
            snapshot_sequence: next_snapshot.sequence,
            snapshot_ciphertext_sha256: snapshot_ciphertext_sha256.clone(),
            envelope_version: PREPARATION_ENVELOPE_VERSION,
            required_frk_version: keys.frk_version(),
        };
        let sealed_head = next_head.seal(keys)?;
        publish_preparation_head_with_hook(&self.fs_dir, &sealed_head, &mut |phase| {
            hook(PreparationPublicationTarget::Head, phase)
        })?;
        status_from_loaded(
            &LoadedPreparation {
                head: next_head,
                snapshot: next_snapshot,
                pointer_sha256: sha256_hex(sealed_head.as_bytes()),
            },
            PreparationOpenDisposition::Reconciled,
        )
    }

    pub(super) fn reconcile_prepared_objects(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationReconciliationRequest,
    ) -> Result<PreparationReconciliationPage, PreparationError> {
        validate_reconciliation_request(request)?;
        let _lease_operation = self.admit_lease_publication_operation()?;
        let commit_lock = super::CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let pointer = read_pointer_bytes(&self.fs_dir)?.ok_or(PreparationError::Missing)?;
        let (loaded, layout) =
            self.load_active_preparation_page_locked(&commit_lock, keys, pointer)?;
        let page = reconciliation_page(self, &layout, &loaded, keys, request)?;
        drop(commit_lock);
        Ok(page)
    }

    #[cfg(test)]
    pub(super) fn reconcile_prepared_objects_with_instrumentation(
        &self,
        keys: &FrkSubkeys,
        request: &PreparationReconciliationRequest,
        instrumentation: Arc<PreparationReconciliationTestInstrumentation>,
    ) -> Result<PreparationReconciliationPage, PreparationError> {
        let _guard = ReconciliationInstrumentationGuard::install(instrumentation);
        self.reconcile_prepared_objects(keys, request)
    }

    fn load_active_preparation_locked(
        &self,
        commit_lock: &super::CoreCommitLock,
        keys: &FrkSubkeys,
        pointer: Vec<u8>,
    ) -> Result<LoadedPreparation, PreparationError> {
        let (loaded, layout) =
            self.load_active_preparation_page_locked(commit_lock, keys, pointer)?;
        validate_referenced_segments(&layout, &loaded.snapshot, keys, &self.core_id)?;
        Ok(loaded)
    }

    fn load_active_preparation_page_locked(
        &self,
        _commit_lock: &super::CoreCommitLock,
        keys: &FrkSubkeys,
        pointer: Vec<u8>,
    ) -> Result<(LoadedPreparation, PreparationLayout), PreparationError> {
        let (head, pointer_sha256) = authenticate_pointer(&pointer, keys, &self.core_id)?;
        let preparations = open_required_directory(&self.fs_dir, PREPARATIONS_DIRECTORY)?;
        let _quarantine = open_required_directory(&self.fs_dir, PREPARATION_QUARANTINE_DIRECTORY)?;
        let layout = open_preparation_layout(&preparations, &head.preparation_id)?;
        let snapshot_name = format!(
            "{:020}-{}.prep.acore",
            head.snapshot_sequence, head.snapshot_ciphertext_sha256
        );
        let encoded_snapshot = match super::read_bounded_in(
            &layout.snapshots,
            OsStr::new(&snapshot_name),
            MAX_PREPARATION_SNAPSHOT_ENVELOPE_SIZE,
        ) {
            Ok(encoded) => encoded,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return Err(PreparationError::MissingSnapshot)
            }
            Err(_) => return Err(PreparationError::CorruptSnapshot),
        };
        if sha256_hex(&encoded_snapshot) != head.snapshot_ciphertext_sha256 {
            return Err(PreparationError::CorruptSnapshot);
        }
        let snapshot =
            PreparationSnapshot::open(&encoded_snapshot, keys, &self.core_id, keys.frk_version())
                .map_err(|_| PreparationError::CorruptSnapshot)?;
        if snapshot.preparation_id != head.preparation_id
            || snapshot.sequence != head.snapshot_sequence
        {
            return Err(PreparationError::StaleSnapshotReplay);
        }
        validate_reconciliation_snapshot_manifest(&snapshot)?;
        Ok((
            LoadedPreparation {
                head,
                snapshot,
                pointer_sha256,
            },
            layout,
        ))
    }
}

fn validate_preparation_cas(expected: &PreparationCas) -> Result<(), PreparationError> {
    validate_hash(&expected.pointer_sha256)?;
    if expected.snapshot_sequence == 0 {
        return Err(PreparationError::InvalidFormat("snapshot sequence"));
    }
    Ok(())
}

fn validate_loaded_cas(
    loaded: &LoadedPreparation,
    expected: &PreparationCas,
) -> Result<(), PreparationError> {
    if loaded.pointer_sha256 != expected.pointer_sha256
        || loaded.snapshot.sequence != expected.snapshot_sequence
    {
        return Err(PreparationError::CasConflict);
    }
    Ok(())
}

fn validate_prepare_object_request(
    request: &PrepareObjectRequest,
    max_object_plaintext_bytes: u64,
) -> Result<(), PreparationError> {
    if request.body_length > max_object_plaintext_bytes {
        return Err(PreparationError::LimitExceeded("object plaintext bytes"));
    }
    if request.converter_format_version == 0 {
        return Err(PreparationError::InvalidFormat("converter format version"));
    }
    validate_hash(&request.source_fingerprint_sha256)?;
    super::converter::validate_converter_object_metadata(
        &super::converter::ConverterObjectMetadata {
            object_id: &request.object_id,
            revision: request.revision,
            object_key_epoch: request.object_key_epoch,
            parent_id: &request.parent_id,
            name: &request.name,
            kind: request.kind,
            content_type: &request.content_type,
            body_encoding: request.body_encoding,
            body_length: request.body_length,
            content_sha256: &request.content_sha256,
            created_at: &request.created_at,
            updated_at: &request.updated_at,
            source_character_count: request.source_character_count,
            references: &request.references,
            policy: request.policy,
            stable_role: request.stable_role.as_deref(),
            graph_metadata: &request.graph_metadata,
        },
    )
    .map_err(PreparationError::from)
}

fn envelope_metadata_for_request(
    request: &PrepareObjectRequest,
) -> Result<EnvelopeMetadata, PreparationError> {
    let chunk_count = if request.body_length == 0 {
        0
    } else {
        let chunks = request
            .body_length
            .checked_sub(1)
            .and_then(|value| value.checked_div(BODY_CHUNK_PLAINTEXT_SIZE as u64))
            .and_then(|value| value.checked_add(1))
            .ok_or(PreparationError::LimitExceeded("object chunk count"))?;
        u32::try_from(chunks).map_err(|_| PreparationError::LimitExceeded("object chunk count"))?
    };
    let mut metadata = request.graph_metadata.clone();
    for value in metadata.values_mut() {
        canonicalize_json(value);
    }
    Ok(EnvelopeMetadata {
        schema_version: METADATA_SCHEMA_VERSION,
        kind: request.kind.as_str().to_owned(),
        object_id: request.object_id.clone(),
        revision: request.revision,
        created_at: request.created_at.clone(),
        updated_at: request.updated_at.clone(),
        content_type: request.content_type.clone(),
        metadata,
        body_encoding: request.body_encoding,
        body_length: request.body_length,
        body_sha256: request.content_sha256.clone(),
        chunk_plaintext_size: BODY_CHUNK_PLAINTEXT_SIZE as u32,
        chunk_count,
    })
}

fn envelope_metadata_sha256(metadata: &EnvelopeMetadata) -> Result<String, PreparationError> {
    let encoded =
        bounded_json_to_vec(metadata, MAX_METADATA_PLAINTEXT_SIZE).map_err(
            |error| match error {
                BoundedJsonError::LimitExceeded => {
                    PreparationError::LimitExceeded("envelope metadata")
                }
                BoundedJsonError::Json(error) => PreparationError::Json(error),
            },
        )?;
    Ok(sha256_hex(&encoded))
}

fn canonicalize_json(value: &mut Value) {
    match value {
        Value::Array(values) => {
            for value in values {
                canonicalize_json(value);
            }
        }
        Value::Object(values) => {
            let mut sorted = serde_json::Map::new();
            let taken = std::mem::take(values);
            let mut entries: Vec<_> = taken.into_iter().collect();
            entries.sort_by(|left, right| left.0.cmp(&right.0));
            for (key, mut value) in entries {
                canonicalize_json(&mut value);
                sorted.insert(key, value);
            }
            *values = sorted;
        }
        _ => {}
    }
}

fn descriptor_from_prepared(
    request: &PrepareObjectRequest,
    prepared: &super::PreparedObjectRevision,
    envelope_metadata_sha256: &str,
    preparation_ordinal: u64,
    #[cfg(test)] logical_body_length: Option<u64>,
) -> Result<PreparedObjectDescriptor, PreparationError> {
    let wrapped = prepared
        .wrapped_dek
        .to_wrapped_object_dek()
        .map_err(super::CommitError::from)?;
    Ok(PreparedObjectDescriptor {
        stable_id: request.object_id.clone(),
        revision: request.revision,
        kind: request.kind.as_str().to_owned(),
        object_key_epoch: request.object_key_epoch,
        physical_name: prepared.physical_name.as_str().to_owned(),
        encoded_size: prepared.encoded_size,
        encrypted_file_sha256: hex_bytes(&prepared.encrypted_hash),
        content_sha256: request.content_sha256.clone(),
        parent_id: request.parent_id.clone(),
        name: request.name.clone(),
        content_type: request.content_type.clone(),
        body_encoding: body_encoding_name(request.body_encoding).to_owned(),
        body_length: request.body_length,
        #[cfg(test)]
        logical_body_length,
        created_at: request.created_at.clone(),
        updated_at: request.updated_at.clone(),
        source_character_count: request
            .source_character_count
            .map(u64::try_from)
            .transpose()
            .map_err(|_| PreparationError::LimitExceeded("source character count"))?,
        references: request.references.clone(),
        policy: policy_name(request.policy).to_owned(),
        stable_role: request.stable_role.clone(),
        graph_metadata: request.graph_metadata.clone(),
        object_key_binding_sha256: hex_bytes(&prepared.object_key_binding),
        wrapped_object_dek: WrappedObjectDekWire {
            frk_version: prepared.wrapped_dek.frk_version(),
            object_key_epoch: prepared.wrapped_dek.object_key_epoch(),
            algorithm: wrapped.algorithm().to_owned(),
            envelope_version: wrapped.envelope_version(),
            nonce_base64: BASE64.encode(wrapped.nonce()),
            ciphertext_base64: BASE64.encode(wrapped.ciphertext()),
        },
        envelope_metadata_sha256: envelope_metadata_sha256.to_owned(),
        source_fingerprint_sha256: request.source_fingerprint_sha256.clone(),
        converter_format_version: request.converter_format_version,
        preparation_ordinal,
    })
}

fn prepared_revision_from_descriptor(
    descriptor: &PreparedObjectDescriptor,
) -> Result<super::PreparedObjectRevision, PreparationError> {
    let nonce = BASE64
        .decode(&descriptor.wrapped_object_dek.nonce_base64)
        .map_err(|_| PreparationError::InvalidFormat("wrapped object DEK nonce"))?;
    let ciphertext = BASE64
        .decode(&descriptor.wrapped_object_dek.ciphertext_base64)
        .map_err(|_| PreparationError::InvalidFormat("wrapped object DEK ciphertext"))?;
    let wrapped_dek = WrappedObjectDekRecord::from_parts(
        descriptor.wrapped_object_dek.frk_version,
        descriptor.wrapped_object_dek.object_key_epoch,
        &descriptor.wrapped_object_dek.algorithm,
        descriptor.wrapped_object_dek.envelope_version,
        &nonce,
        ciphertext,
    )
    .map_err(super::CommitError::from)?;
    Ok(super::PreparedObjectRevision {
        object_id: OpaqueId::parse(&descriptor.stable_id)
            .map_err(|_| PreparationError::InvalidFormat("stable ID"))?,
        revision: descriptor.revision,
        kind: ObjectKind::parse(&descriptor.kind)?,
        object_key_epoch: descriptor.object_key_epoch,
        physical_name: ObjectPhysicalName::parse(&descriptor.physical_name)
            .map_err(super::CommitError::from)?,
        content_hash: ContentHash::parse(&descriptor.content_sha256)
            .map_err(super::CommitError::from)?,
        encoded_size: descriptor.encoded_size,
        encrypted_hash: parse_hash_array(&descriptor.encrypted_file_sha256)?,
        object_key_binding: parse_hash_array(&descriptor.object_key_binding_sha256)?,
        wrapped_dek,
    })
}

fn validate_seal_request(request: &PreparationSealRequest) -> Result<(), PreparationError> {
    if request.source_mutation_generation == 0 {
        return Err(PreparationError::InvalidFormat(
            "source mutation generation",
        ));
    }
    validate_hash(&request.source_inventory_sha256)?;
    if request.folders.len().saturating_add(request.objects.len()) > MAX_CATALOG_ENTRIES {
        return Err(PreparationError::LimitExceeded("final-intent entries"));
    }
    let mut ids = HashSet::new();
    let mut ordinals = HashSet::new();
    for identity in &request.objects {
        validate_opaque(&identity.object_id, "object ID")?;
        validate_hash(&identity.content_sha256)?;
        if identity.revision == 0
            || usize::try_from(identity.preparation_ordinal)
                .map_or(true, |ordinal| ordinal >= MAX_CATALOG_ENTRIES)
            || !ids.insert(identity.object_id.as_str())
            || !ordinals.insert(identity.preparation_ordinal)
        {
            return Err(PreparationError::InvalidFormat("preparation identity"));
        }
    }
    Ok(())
}

fn validate_finalize_request(request: &PreparationFinalizeRequest) -> Result<(), PreparationError> {
    validate_opaque(&request.preparation_id, "preparation ID")?;
    validate_preparation_cas(&request.expected)?;
    if request.source_mutation_generation == 0 {
        return Err(PreparationError::InvalidFormat(
            "source mutation generation",
        ));
    }
    validate_hash(&request.source_inventory_sha256)
}

fn validate_source_fence(
    snapshot: &PreparationSnapshot,
    source_mutation_generation: u64,
    source_inventory_sha256: &str,
) -> Result<(), PreparationError> {
    if snapshot.source_mutation_generation != source_mutation_generation
        || snapshot.source_inventory_sha256 != source_inventory_sha256
    {
        return Err(PreparationError::SourceChanged);
    }
    Ok(())
}

fn validate_expected_validation_head(
    snapshot: &PreparationSnapshot,
    current: Option<&super::ValidationSnapshot>,
) -> Result<(), PreparationError> {
    match (
        snapshot.expected_validation_generation,
        snapshot.expected_validation_catalog_sha256.as_deref(),
        current,
    ) {
        (None, None, None) => Ok(()),
        (Some(generation), Some(hash), Some(current))
            if current.head().generation() == generation
                && current.head().catalog_hash() == hash =>
        {
            Ok(())
        }
        _ => Err(PreparationError::ValidationHeadConflict),
    }
}

fn read_all_descriptors(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
) -> Result<Vec<PreparedObjectDescriptor>, PreparationError> {
    let mut descriptors = Vec::with_capacity(
        usize::try_from(snapshot.total_objects)
            .map_err(|_| PreparationError::LimitExceeded("total objects"))?,
    );
    for reference in &snapshot.manifest_segments {
        descriptors.extend(read_descriptor_segment(layout, snapshot, keys, reference)?.descriptors);
    }
    if descriptors.len()
        != usize::try_from(snapshot.total_objects)
            .map_err(|_| PreparationError::LimitExceeded("total objects"))?
    {
        return Err(PreparationError::CorruptSnapshot);
    }
    Ok(descriptors)
}

fn validate_descriptor_coverage(
    descriptors: &[PreparedObjectDescriptor],
    identities: &[PreparationIdentity],
) -> Result<(), PreparationError> {
    if descriptors.len() != identities.len() {
        return Err(PreparationError::FinalIntentMismatch);
    }
    let by_ordinal: BTreeMap<_, _> = identities
        .iter()
        .map(|identity| (identity.preparation_ordinal, identity))
        .collect();
    for descriptor in descriptors {
        let identity = by_ordinal
            .get(&descriptor.preparation_ordinal)
            .ok_or(PreparationError::FinalIntentMismatch)?;
        if identity.object_id != descriptor.stable_id
            || identity.revision != descriptor.revision
            || identity.content_sha256 != descriptor.content_sha256
            || descriptor.stable_role.is_some()
        {
            return Err(PreparationError::FinalIntentMismatch);
        }
    }
    Ok(())
}

fn validate_descriptor_revision_preconditions(
    current: Option<&CatalogGeneration>,
    descriptors: &[PreparedObjectDescriptor],
) -> Result<(), PreparationError> {
    for descriptor in descriptors {
        let current_entry = current.and_then(|catalog| {
            catalog
                .entries()
                .iter()
                .find(|entry| entry.stable_id().as_str() == descriptor.stable_id)
        });
        match current_entry.and_then(|entry| entry.object_payload()) {
            Some(object)
                if object.revision().checked_add(1) == Some(descriptor.revision)
                    && object.object_key_epoch() == descriptor.object_key_epoch => {}
            None if current_entry.is_none()
                && descriptor.revision == 1
                && descriptor.object_key_epoch == 1 => {}
            _ => {
                return Err(PreparationError::Converter(
                    super::converter::ValidationBatchError::Invalid(
                        "object revision precondition mismatch",
                    ),
                ))
            }
        }
    }
    Ok(())
}

fn build_catalog_from_inputs(
    generation: u64,
    folders: &[super::converter::ValidationBatchFolder],
    descriptors: &[PreparedObjectDescriptor],
    identities: &[PreparationIdentity],
) -> Result<CatalogGeneration, PreparationError> {
    validate_descriptor_coverage(descriptors, identities)?;
    let objects = descriptors
        .iter()
        .map(|descriptor| {
            Ok(super::converter::PreparedValidationCatalogObject {
                prepared: prepared_revision_from_descriptor(descriptor)?,
                parent_id: descriptor.parent_id.clone(),
                name: descriptor.name.clone(),
                policy: parse_policy(&descriptor.policy)?,
                references: descriptor.references.clone(),
                metadata: descriptor.graph_metadata.clone(),
            })
        })
        .collect::<Result<Vec<_>, PreparationError>>()?;
    super::converter::build_prepared_validation_catalog(generation, folders, objects)
        .map_err(PreparationError::from)
}

fn validate_prepared_descriptor_envelope(
    coordinator: &super::CoreCommitCoordinator,
    keys: &FrkSubkeys,
    descriptor: &PreparedObjectDescriptor,
    prepared: &super::PreparedObjectRevision,
) -> Result<(), PreparationError> {
    let base_aad = ObjectBaseAad::new(
        &coordinator.core_id,
        &descriptor.stable_id,
        ObjectKind::parse(&descriptor.kind)?,
        ENVELOPE_VERSION,
        descriptor.object_key_epoch,
        descriptor.revision,
    )
    .map_err(super::CommitError::from)?;
    let object_key_aad =
        ObjectKeyAad::from_base(base_aad.clone(), prepared.wrapped_dek.frk_version())
            .map_err(super::CommitError::from)?;
    let wrapped = prepared
        .wrapped_dek
        .to_wrapped_object_dek()
        .map_err(super::CommitError::from)?;
    let object_key =
        unwrap_object_dek(keys, &wrapped, &object_key_aad).map_err(super::CommitError::from)?;
    let file = super::open_regular_file_in(
        &coordinator.objects_dir,
        OsStr::new(prepared.physical_name.as_str()),
    )
    .map_err(super::CommitError::from)?;
    let authenticated =
        open_envelope_stream(file, &object_key, base_aad).map_err(super::CommitError::from)?;
    let expected = envelope_metadata_from_descriptor(descriptor)?;
    if authenticated.metadata() != &expected
        || envelope_metadata_sha256(authenticated.metadata())?
            != descriptor.envelope_metadata_sha256
    {
        return Err(PreparationError::FinalIntentMismatch);
    }
    Ok(())
}

fn envelope_metadata_from_descriptor(
    descriptor: &PreparedObjectDescriptor,
) -> Result<EnvelopeMetadata, PreparationError> {
    let body_length = descriptor.body_length;
    let chunks = if body_length == 0 {
        0
    } else {
        body_length
            .checked_add(BODY_CHUNK_PLAINTEXT_SIZE as u64 - 1)
            .ok_or(PreparationError::LimitExceeded("object chunk count"))?
            / BODY_CHUNK_PLAINTEXT_SIZE as u64
    };
    let mut metadata = descriptor.graph_metadata.clone();
    for value in metadata.values_mut() {
        canonicalize_json(value);
    }
    Ok(EnvelopeMetadata {
        schema_version: METADATA_SCHEMA_VERSION,
        kind: descriptor.kind.clone(),
        object_id: descriptor.stable_id.clone(),
        revision: descriptor.revision,
        created_at: descriptor.created_at.clone(),
        updated_at: descriptor.updated_at.clone(),
        content_type: descriptor.content_type.clone(),
        metadata,
        body_encoding: parse_body_encoding(&descriptor.body_encoding)?,
        body_length,
        body_sha256: descriptor.content_sha256.clone(),
        chunk_plaintext_size: BODY_CHUNK_PLAINTEXT_SIZE as u32,
        chunk_count: u32::try_from(chunks)
            .map_err(|_| PreparationError::LimitExceeded("object chunk count"))?,
    })
}

fn final_intent_entries(
    folders: &[super::converter::ValidationBatchFolder],
    descriptors: &[PreparedObjectDescriptor],
) -> Result<Vec<FinalIntentEntry>, PreparationError> {
    let mut values = Vec::with_capacity(folders.len() + descriptors.len());
    for folder in folders {
        values.push((
            folder.stable_id.clone(),
            FinalCatalogIntentEntry::Folder(FinalCatalogFolderIntent {
                stable_id: folder.stable_id.clone(),
                parent_id: folder.parent_id.clone(),
                name: folder.name.clone(),
                role: folder.role.clone(),
                policy: policy_name(folder.policy).to_owned(),
                metadata: folder.metadata.clone(),
            }),
        ));
    }
    for descriptor in descriptors {
        values.push((
            descriptor.stable_id.clone(),
            FinalCatalogIntentEntry::Object(Box::new(FinalCatalogObjectIntent {
                stable_id: descriptor.stable_id.clone(),
                parent_id: descriptor.parent_id.clone(),
                name: descriptor.name.clone(),
                object_kind: descriptor.kind.clone(),
                revision: descriptor.revision,
                object_key_epoch: descriptor.object_key_epoch,
                content_sha256: descriptor.content_sha256.clone(),
                content_type: descriptor.content_type.clone(),
                body_encoding: descriptor.body_encoding.clone(),
                body_length: descriptor.body_length,
                created_at: descriptor.created_at.clone(),
                updated_at: descriptor.updated_at.clone(),
                source_character_count: descriptor.source_character_count,
                references: descriptor.references.clone(),
                policy: descriptor.policy.clone(),
                metadata: descriptor.graph_metadata.clone(),
                source_fingerprint_sha256: descriptor.source_fingerprint_sha256.clone(),
                converter_format_version: descriptor.converter_format_version,
                preparation_ordinal: descriptor.preparation_ordinal,
            })),
        ));
    }
    values.sort_by(|left, right| left.0.cmp(&right.0));
    let mut previous = None;
    let mut entries = Vec::with_capacity(values.len());
    for (ordinal, (stable_id, value)) in values.into_iter().enumerate() {
        if previous.as_deref() == Some(stable_id.as_str()) {
            return Err(PreparationError::Converter(
                super::converter::ValidationBatchError::Invalid("duplicate stable ID"),
            ));
        }
        previous = Some(stable_id.clone());
        let canonical_catalog_entry_json = serde_json::to_string(&serde_json::to_value(&value)?)?;
        entries.push(FinalIntentEntry {
            ordinal: u64::try_from(ordinal)
                .map_err(|_| PreparationError::LimitExceeded("final-intent ordinal"))?,
            stable_id,
            canonical_catalog_entry_sha256: sha256_hex(canonical_catalog_entry_json.as_bytes()),
            canonical_catalog_entry_json,
        });
    }
    validate_final_intent_entries(&entries, MAX_SEGMENT_ITEMS)?;
    Ok(entries)
}

fn canonical_intent_sha256(entries: &[FinalIntentEntry]) -> String {
    let mut digest = Sha256::new();
    digest.update(b"anima-corefs-final-intent-v1\0");
    for entry in entries {
        digest.update(entry.ordinal.to_le_bytes());
        digest.update((entry.stable_id.len() as u64).to_le_bytes());
        digest.update(entry.stable_id.as_bytes());
        digest.update(entry.canonical_catalog_entry_sha256.as_bytes());
    }
    hex_bytes(&digest.finalize())
}

fn canonical_catalog_sha256(catalog: &CatalogGeneration) -> Result<String, PreparationError> {
    Ok(sha256_hex(
        &encode_catalog_generation(catalog)
            .map_err(super::converter::ValidationBatchError::from)?,
    ))
}

fn read_all_final_intent_entries(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
) -> Result<Vec<FinalIntentEntry>, PreparationError> {
    let capacity = snapshot
        .final_intent_entry_count
        .and_then(|count| usize::try_from(count).ok())
        .unwrap_or(0);
    let mut entries = Vec::with_capacity(capacity);
    for reference in &snapshot.final_intent_segments {
        let name = format!(
            "{:020}-{}.prep-intent.acore",
            reference.segment_index, reference.ciphertext_sha256
        );
        let encoded = read_referenced_record(
            &layout.intent,
            &name,
            MAX_FINAL_INTENT_SEGMENT_ENVELOPE_SIZE,
            PreparationReferenceKind::Intent,
            reference.segment_index,
        )?;
        if sha256_hex(&encoded) != reference.ciphertext_sha256 {
            return Err(PreparationError::CorruptReferencedRecord {
                kind: PreparationReferenceKind::Intent,
                segment_index: reference.segment_index,
            });
        }
        let segment = FinalIntentSegment::open(
            &encoded,
            keys,
            &snapshot.core_id,
            snapshot.required_frk_version,
        )?;
        entries.extend(segment.entries);
    }
    Ok(entries)
}

fn reconstruct_sealed_catalog(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
) -> Result<
    (
        CatalogGeneration,
        Vec<super::PreparedObjectRevision>,
        Vec<PreparedObjectDescriptor>,
    ),
    PreparationError,
> {
    let entries = read_all_final_intent_entries(layout, snapshot, keys)?;
    if canonical_intent_sha256(&entries)
        != snapshot
            .canonical_intent_sha256
            .as_deref()
            .ok_or(PreparationError::FinalIntentMismatch)?
    {
        return Err(PreparationError::FinalIntentMismatch);
    }
    let descriptors = read_all_descriptors(layout, snapshot, keys)?;
    let mut folders = Vec::new();
    let mut identities = Vec::new();
    for entry in &entries {
        let value: FinalCatalogIntentEntry =
            serde_json::from_str(&entry.canonical_catalog_entry_json)?;
        match value {
            FinalCatalogIntentEntry::Folder(FinalCatalogFolderIntent {
                stable_id,
                parent_id,
                name,
                role,
                policy,
                metadata,
            }) => {
                if stable_id != entry.stable_id {
                    return Err(PreparationError::FinalIntentMismatch);
                }
                folders.push(super::converter::ValidationBatchFolder {
                    stable_id,
                    parent_id,
                    name,
                    role,
                    policy: parse_policy(&policy)?,
                    metadata,
                });
            }
            FinalCatalogIntentEntry::Object(object) => {
                let FinalCatalogObjectIntent {
                    stable_id,
                    parent_id,
                    name,
                    object_kind,
                    revision,
                    object_key_epoch,
                    content_sha256,
                    content_type,
                    body_encoding,
                    body_length,
                    created_at,
                    updated_at,
                    source_character_count,
                    references,
                    policy,
                    metadata,
                    source_fingerprint_sha256,
                    converter_format_version,
                    preparation_ordinal,
                } = *object;
                if stable_id != entry.stable_id {
                    return Err(PreparationError::FinalIntentMismatch);
                }
                let descriptor = usize::try_from(preparation_ordinal)
                    .ok()
                    .and_then(|ordinal| descriptors.get(ordinal))
                    .ok_or(PreparationError::FinalIntentMismatch)?;
                if descriptor.stable_id != stable_id
                    || descriptor.parent_id != parent_id
                    || descriptor.name != name
                    || descriptor.kind != object_kind
                    || descriptor.revision != revision
                    || descriptor.object_key_epoch != object_key_epoch
                    || descriptor.content_sha256 != content_sha256
                    || descriptor.content_type != content_type
                    || descriptor.body_encoding != body_encoding
                    || descriptor.body_length != body_length
                    || descriptor.created_at != created_at
                    || descriptor.updated_at != updated_at
                    || descriptor.source_character_count != source_character_count
                    || descriptor.references != references
                    || descriptor.policy != policy
                    || descriptor.graph_metadata != metadata
                    || descriptor.source_fingerprint_sha256 != source_fingerprint_sha256
                    || descriptor.converter_format_version != converter_format_version
                    || descriptor.preparation_ordinal != preparation_ordinal
                {
                    return Err(PreparationError::FinalIntentMismatch);
                }
                identities.push(PreparationIdentity {
                    object_id: stable_id,
                    revision,
                    content_sha256,
                    preparation_ordinal,
                });
            }
        }
    }
    if Some(
        u32::try_from(entries.len())
            .map_err(|_| PreparationError::LimitExceeded("final-intent entries"))?,
    ) != snapshot.final_intent_entry_count
        || Some(
            u32::try_from(folders.len())
                .map_err(|_| PreparationError::LimitExceeded("final-intent folders"))?,
        ) != snapshot.final_intent_folder_count
    {
        return Err(PreparationError::FinalIntentMismatch);
    }
    let generation = snapshot
        .intended_validation_generation
        .ok_or(PreparationError::FinalIntentMismatch)?;
    let prepared = descriptors
        .iter()
        .map(prepared_revision_from_descriptor)
        .collect::<Result<Vec<_>, _>>()?;
    let catalog = build_catalog_from_inputs(generation, &folders, &descriptors, &identities)?;
    Ok((catalog, prepared, descriptors))
}

fn clear_sealed_intent_metadata(snapshot: &mut PreparationSnapshot) {
    snapshot.canonical_intent_sha256 = None;
    snapshot.intended_validation_generation = None;
    snapshot.intended_validation_catalog_sha256 = None;
    snapshot.final_intent_entry_count = None;
    snapshot.final_intent_folder_count = None;
}

fn next_descriptor_segment(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
    descriptor: PreparedObjectDescriptor,
    descriptor_segment_items: usize,
) -> Result<(PreparedObjectDescriptorSegment, bool), PreparationError> {
    if let Some(reference) = snapshot.manifest_segments.last() {
        let mut last = read_descriptor_segment(layout, snapshot, keys, reference)?;
        if last.descriptors.len() < descriptor_segment_items {
            last.descriptors.push(descriptor.clone());
            match last.encode() {
                Ok(_) => return Ok((last, true)),
                Err(PreparationError::LimitExceeded(_)) => {}
                Err(error) => return Err(error),
            }
        }
    }
    let segment_index = u32::try_from(snapshot.manifest_segments.len())
        .map_err(|_| PreparationError::LimitExceeded("descriptor segment index"))?;
    Ok((
        PreparedObjectDescriptorSegment {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: snapshot.core_id.clone(),
            preparation_id: snapshot.preparation_id.clone(),
            required_frk_version: snapshot.required_frk_version,
            segment_index,
            descriptors: vec![descriptor],
        },
        false,
    ))
}

fn validate_final_intent_entries(
    entries: &[FinalIntentEntry],
    segment_items: usize,
) -> Result<(), PreparationError> {
    if entries.is_empty() {
        return Err(PreparationError::InvalidFormat("final intent is empty"));
    }
    if entries.len() > MAX_CATALOG_ENTRIES {
        return Err(PreparationError::LimitExceeded("final-intent entries"));
    }
    if segment_items == 0 || segment_items > MAX_SEGMENT_ITEMS {
        return Err(PreparationError::LimitExceeded(
            "final-intent segment items",
        ));
    }
    let mut stable_ids = HashSet::with_capacity(entries.len());
    for (index, entry) in entries.iter().enumerate() {
        entry.validate()?;
        if !stable_ids.insert(entry.stable_id.as_str()) {
            return Err(PreparationError::InvalidFormat("duplicate stable ID"));
        }
        if entry.ordinal
            != u64::try_from(index)
                .map_err(|_| PreparationError::LimitExceeded("final-intent ordinal"))?
        {
            return Err(PreparationError::InvalidFormat(
                "final-intent ordinals are not contiguous",
            ));
        }
    }
    Ok(())
}

fn final_intent_segments(
    snapshot: &PreparationSnapshot,
    entries: &[FinalIntentEntry],
    segment_items: usize,
) -> Result<Vec<FinalIntentSegment>, PreparationError> {
    let mut segments = Vec::new();
    let mut current = Vec::new();
    for entry in entries {
        current.push(entry.clone());
        let segment_index = u32::try_from(segments.len())
            .map_err(|_| PreparationError::LimitExceeded("final-intent segment index"))?;
        let candidate = FinalIntentSegment {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: snapshot.core_id.clone(),
            preparation_id: snapshot.preparation_id.clone(),
            required_frk_version: snapshot.required_frk_version,
            segment_index,
            entries: current.clone(),
        };
        let exceeds_items = current.len() > segment_items;
        let exceeds_bytes = matches!(candidate.encode(), Err(PreparationError::LimitExceeded(_)));
        if exceeds_items || exceeds_bytes {
            let last = current
                .pop()
                .ok_or(PreparationError::InvalidFormat("final intent is empty"))?;
            if current.is_empty() {
                return Err(PreparationError::LimitExceeded("final-intent entry"));
            }
            segments.push(FinalIntentSegment {
                entries: std::mem::take(&mut current),
                ..candidate
            });
            current.push(last);
        }
    }
    if !current.is_empty() {
        let segment_index = u32::try_from(segments.len())
            .map_err(|_| PreparationError::LimitExceeded("final-intent segment index"))?;
        let segment = FinalIntentSegment {
            schema_version: PREPARATION_SCHEMA_VERSION,
            core_id: snapshot.core_id.clone(),
            preparation_id: snapshot.preparation_id.clone(),
            required_frk_version: snapshot.required_frk_version,
            segment_index,
            entries: current,
        };
        segment.encode()?;
        segments.push(segment);
    }
    if segments.len() > MAX_SEGMENT_REFERENCES {
        return Err(PreparationError::LimitExceeded(
            "final-intent segment references",
        ));
    }
    Ok(segments)
}

fn read_descriptor_segment(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
    reference: &PreparationSegmentReference,
) -> Result<PreparedObjectDescriptorSegment, PreparationError> {
    let name = format!(
        "{:020}-{}.prep-manifest.acore",
        reference.segment_index, reference.ciphertext_sha256
    );
    let encoded = read_referenced_record(
        &layout.descriptors,
        &name,
        MAX_DESCRIPTOR_SEGMENT_ENVELOPE_SIZE,
        PreparationReferenceKind::Descriptor,
        reference.segment_index,
    )?;
    if sha256_hex(&encoded) != reference.ciphertext_sha256 {
        return Err(PreparationError::CorruptReferencedRecord {
            kind: PreparationReferenceKind::Descriptor,
            segment_index: reference.segment_index,
        });
    }
    let record = PreparedObjectDescriptorSegment::open(
        &encoded,
        keys,
        &snapshot.core_id,
        snapshot.required_frk_version,
    )
    .map_err(|_| PreparationError::CorruptReferencedRecord {
        kind: PreparationReferenceKind::Descriptor,
        segment_index: reference.segment_index,
    })?;
    let plaintext_bytes = u32::try_from(record.encode()?.len()).map_err(|_| {
        PreparationError::CorruptReferencedRecord {
            kind: PreparationReferenceKind::Descriptor,
            segment_index: reference.segment_index,
        }
    })?;
    if record.preparation_id != snapshot.preparation_id
        || record.segment_index != reference.segment_index
        || u32::try_from(record.descriptors.len()).ok() != Some(reference.item_count)
        || plaintext_bytes != reference.plaintext_bytes
    {
        return Err(PreparationError::CorruptReferencedRecord {
            kind: PreparationReferenceKind::Descriptor,
            segment_index: reference.segment_index,
        });
    }
    Ok(record)
}

fn find_descriptor(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
    request: &PrepareObjectRequest,
) -> Result<Option<PreparedObjectDescriptor>, PreparationError> {
    for reference in &snapshot.manifest_segments {
        let segment = read_descriptor_segment(layout, snapshot, keys, reference)?;
        if let Some(descriptor) = segment
            .descriptors
            .into_iter()
            .find(|descriptor| descriptor.stable_id == request.object_id)
        {
            if descriptor.revision != request.revision
                || descriptor.content_sha256 != request.content_sha256
            {
                return Err(PreparationError::LogicalRevisionConflict {
                    object_id: request.object_id.clone(),
                    revision: request.revision,
                });
            }
            if !descriptor_matches_request(&descriptor, request)? {
                return Err(PreparationError::ActiveConflict("object metadata"));
            }
            return Ok(Some(descriptor));
        }
    }
    Ok(None)
}

fn descriptor_matches_request(
    descriptor: &PreparedObjectDescriptor,
    request: &PrepareObjectRequest,
) -> Result<bool, PreparationError> {
    Ok(descriptor.object_key_epoch == request.object_key_epoch
        && descriptor.kind == request.kind.as_str()
        && descriptor.parent_id == request.parent_id
        && descriptor.name == request.name
        && descriptor.content_type == request.content_type
        && parse_body_encoding(&descriptor.body_encoding)? == request.body_encoding
        && descriptor.body_length == request.body_length
        && descriptor.created_at == request.created_at
        && descriptor.updated_at == request.updated_at
        && descriptor.source_character_count
            == request
                .source_character_count
                .map(u64::try_from)
                .transpose()
                .map_err(|_| PreparationError::LimitExceeded("source character count"))?
        && descriptor.references == request.references
        && parse_policy(&descriptor.policy)? == request.policy
        && descriptor.stable_role == request.stable_role
        && descriptor.graph_metadata == request.graph_metadata
        && descriptor.source_fingerprint_sha256 == request.source_fingerprint_sha256
        && descriptor.converter_format_version == request.converter_format_version)
}

fn matched_object_outcome(
    loaded: &LoadedPreparation,
    request: &PrepareObjectRequest,
    descriptor: &PreparedObjectDescriptor,
) -> Result<PrepareObjectOutcome, PreparationError> {
    Ok(PrepareObjectOutcome {
        status: status_from_loaded(loaded, PreparationOpenDisposition::Resumed)?,
        disposition: PrepareObjectDisposition::Matched,
        prepared: PreparedObjectSummary {
            object_id: request.object_id.clone(),
            revision: request.revision,
            content_sha256: request.content_sha256.clone(),
            preparation_ordinal: descriptor.preparation_ordinal,
            ciphertext_bytes: descriptor.encoded_size,
        },
    })
}

fn descriptor_metadata(
    descriptor: &PreparedObjectDescriptor,
) -> Result<PreparedObjectMetadata, PreparationError> {
    Ok(PreparedObjectMetadata {
        object_id: descriptor.stable_id.clone(),
        revision: descriptor.revision,
        object_key_epoch: descriptor.object_key_epoch,
        kind: ObjectKind::parse(&descriptor.kind)?,
        parent_id: descriptor.parent_id.clone(),
        name: descriptor.name.clone(),
        content_type: descriptor.content_type.clone(),
        body_encoding: parse_body_encoding(&descriptor.body_encoding)?,
        body_length: descriptor.body_length,
        content_sha256: descriptor.content_sha256.clone(),
        created_at: descriptor.created_at.clone(),
        updated_at: descriptor.updated_at.clone(),
        source_character_count: descriptor
            .source_character_count
            .map(usize::try_from)
            .transpose()
            .map_err(|_| PreparationError::LimitExceeded("source character count"))?,
        references: descriptor.references.clone(),
        policy: parse_policy(&descriptor.policy)?,
        stable_role: descriptor.stable_role.clone(),
        graph_metadata: descriptor.graph_metadata.clone(),
        source_fingerprint_sha256: descriptor.source_fingerprint_sha256.clone(),
        converter_format_version: descriptor.converter_format_version,
        preparation_ordinal: descriptor.preparation_ordinal,
        ciphertext_bytes: descriptor.encoded_size,
    })
}

fn validate_reconciliation_request(
    request: &PreparationReconciliationRequest,
) -> Result<(), PreparationError> {
    if request.limits.max_items == 0
        || request.limits.max_items > MAX_RECONCILIATION_PAGE_ITEMS
        || request.limits.max_bytes == 0
        || request.limits.max_bytes > MAX_RECONCILIATION_PAGE_BYTES
        || request.expected.len() > MAX_RECONCILIATION_PAGE_ITEMS as usize
    {
        return Err(PreparationError::LimitExceeded("reconciliation page"));
    }
    let mut identities = HashSet::new();
    let mut ordinals = HashSet::new();
    for identity in &request.expected {
        validate_opaque(&identity.object_id, "object ID")?;
        validate_hash(&identity.content_sha256)?;
        if identity.revision == 0
            || usize::try_from(identity.preparation_ordinal)
                .map_or(true, |ordinal| ordinal >= MAX_CATALOG_ENTRIES)
            || !identities.insert(identity.object_id.as_str())
            || !ordinals.insert(identity.preparation_ordinal)
        {
            return Err(PreparationError::InvalidFormat("reconciliation identity"));
        }
    }
    bounded_json_to_vec(&request.expected, MAX_RECONCILIATION_PAGE_BYTES as usize).map_err(
        |error| match error {
            BoundedJsonError::LimitExceeded => {
                PreparationError::LimitExceeded("reconciliation request")
            }
            BoundedJsonError::Json(error) => PreparationError::Json(error),
        },
    )?;
    Ok(())
}

fn reconciliation_page(
    coordinator: &super::CoreCommitCoordinator,
    layout: &PreparationLayout,
    loaded: &LoadedPreparation,
    keys: &FrkSubkeys,
    request: &PreparationReconciliationRequest,
) -> Result<PreparationReconciliationPage, PreparationError> {
    let prepared_count = u64::from(loaded.snapshot.total_objects);
    let expected_count = u64::try_from(request.expected.len())
        .map_err(|_| PreparationError::LimitExceeded("reconciliation request"))?;
    let sequence_length = prepared_count
        .checked_add(expected_count)
        .ok_or(PreparationError::LimitExceeded("reconciliation cursor"))?;
    let mut position = request.cursor.as_ref().map_or(0, |cursor| cursor.position);
    if position > sequence_length {
        return Err(PreparationError::InvalidFormat("reconciliation cursor"));
    }
    let mut page = PreparationReconciliationPage {
        prepared_count: loaded.snapshot.total_objects,
        total_plaintext_bytes: loaded.snapshot.total_plaintext_bytes,
        total_ciphertext_bytes: loaded.snapshot.total_ciphertext_bytes,
        descriptor_manifest_root_sha256: loaded.snapshot.manifest_root_sha256.clone(),
        descriptor_segment_roots: Vec::new(),
        items: Vec::new(),
        missing: Vec::new(),
        conflicting: Vec::new(),
        next_cursor: (position < sequence_length)
            .then_some(PreparationReconciliationCursor { position }),
        encoded_bytes: 0,
    };
    let mut consumed = 0_u32;
    while position < sequence_length && consumed < request.limits.max_items {
        let mut candidate = page.clone();
        let mut prepared_to_authenticate = None;
        if position < prepared_count {
            let (descriptor, segment_root) =
                descriptor_at_ordinal(layout, &loaded.snapshot, keys, position)?;
            candidate.items.push(descriptor_metadata(&descriptor)?);
            if !candidate.descriptor_segment_roots.contains(&segment_root) {
                candidate.descriptor_segment_roots.push(segment_root);
            }
            prepared_to_authenticate = Some(prepared_revision_from_descriptor(&descriptor)?);
        } else {
            let expected_index = usize::try_from(position - prepared_count)
                .map_err(|_| PreparationError::InvalidFormat("reconciliation cursor"))?;
            let identity = request
                .expected
                .get(expected_index)
                .ok_or(PreparationError::InvalidFormat("reconciliation cursor"))?;
            if identity.preparation_ordinal >= prepared_count {
                candidate.missing.push(identity.clone());
            } else {
                let (descriptor, _) = descriptor_at_ordinal(
                    layout,
                    &loaded.snapshot,
                    keys,
                    identity.preparation_ordinal,
                )?;
                if descriptor.stable_id != identity.object_id {
                    candidate.missing.push(identity.clone());
                } else if descriptor.revision == identity.revision
                    && descriptor.content_sha256 == identity.content_sha256
                {
                    prepared_to_authenticate =
                        Some(prepared_revision_from_descriptor(&descriptor)?);
                } else {
                    candidate.conflicting.push(identity.clone());
                }
            }
        }

        let next_position = position
            .checked_add(1)
            .ok_or(PreparationError::LimitExceeded("reconciliation cursor"))?;
        candidate.next_cursor =
            (next_position < sequence_length).then_some(PreparationReconciliationCursor {
                position: next_position,
            });
        candidate = encode_reconciliation_page(candidate)?;
        if candidate.encoded_bytes > request.limits.max_bytes {
            if consumed == 0 {
                return Err(PreparationError::LimitExceeded("reconciliation item"));
            }
            break;
        }
        if let Some(prepared) = prepared_to_authenticate {
            coordinator.validate_prepared_revision_file(&prepared)?;
        }
        page = candidate;
        position = next_position;
        consumed += 1;
    }

    page = encode_reconciliation_page(page)?;
    if page.encoded_bytes > request.limits.max_bytes {
        return Err(PreparationError::LimitExceeded("reconciliation page"));
    }
    Ok(page)
}

fn descriptor_at_ordinal(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
    ordinal: u64,
) -> Result<(PreparedObjectDescriptor, String), PreparationError> {
    let mut first_ordinal = 0_u64;
    for reference in &snapshot.manifest_segments {
        let next_ordinal = first_ordinal
            .checked_add(u64::from(reference.item_count))
            .ok_or(PreparationError::CorruptSnapshot)?;
        if ordinal >= next_ordinal {
            first_ordinal = next_ordinal;
            continue;
        }
        let segment = read_descriptor_segment(layout, snapshot, keys, reference)?;
        if let Some(descriptor) = segment
            .descriptors
            .into_iter()
            .find(|descriptor| descriptor.preparation_ordinal == ordinal)
        {
            return Ok((descriptor, reference.ciphertext_sha256.clone()));
        }
        return Err(PreparationError::CorruptSnapshot);
    }
    Err(PreparationError::CorruptSnapshot)
}

fn encode_reconciliation_page(
    mut page: PreparationReconciliationPage,
) -> Result<PreparationReconciliationPage, PreparationError> {
    for _ in 0..8 {
        let encoded_bytes = u32::try_from(serde_json::to_vec(&page)?.len())
            .map_err(|_| PreparationError::LimitExceeded("reconciliation page"))?;
        if page.encoded_bytes == encoded_bytes {
            return Ok(page);
        }
        page.encoded_bytes = encoded_bytes;
    }
    Err(PreparationError::InvalidFormat(
        "reconciliation page encoding",
    ))
}

pub(super) fn manifest_root(references: &[PreparationSegmentReference]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"anima-corefs-preparation-descriptor-manifest-v1\0");
    for reference in references {
        hasher.update(reference.segment_index.to_le_bytes());
        hasher.update(reference.ciphertext_sha256.as_bytes());
        hasher.update(reference.item_count.to_le_bytes());
        hasher.update(reference.plaintext_bytes.to_le_bytes());
    }
    hex_bytes(&hasher.finalize())
}

fn body_encoding_name(value: BodyEncoding) -> &'static str {
    match value {
        BodyEncoding::Utf8 => "utf-8",
        BodyEncoding::Binary => "binary",
    }
}

fn parse_body_encoding(value: &str) -> Result<BodyEncoding, PreparationError> {
    match value {
        "utf-8" => Ok(BodyEncoding::Utf8),
        "binary" => Ok(BodyEncoding::Binary),
        _ => Err(PreparationError::InvalidFormat("body encoding")),
    }
}

fn policy_name(value: super::converter::ValidationBatchPolicy) -> &'static str {
    match value {
        super::converter::ValidationBatchPolicy::UserWrite => "user_write",
        super::converter::ValidationBatchPolicy::Inherit => "inherit",
        super::converter::ValidationBatchPolicy::Deny => "deny",
    }
}

fn parse_policy(value: &str) -> Result<super::converter::ValidationBatchPolicy, PreparationError> {
    match value {
        "user_write" => Ok(super::converter::ValidationBatchPolicy::UserWrite),
        "inherit" => Ok(super::converter::ValidationBatchPolicy::Inherit),
        "deny" => Ok(super::converter::ValidationBatchPolicy::Deny),
        _ => Err(PreparationError::InvalidFormat("policy")),
    }
}

fn parse_hash_array(value: &str) -> Result<[u8; 32], PreparationError> {
    validate_hash(value)?;
    let mut decoded = [0_u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        decoded[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
    }
    Ok(decoded)
}

fn hex_nibble(value: u8) -> Result<u8, PreparationError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(PreparationError::InvalidFormat("hash")),
    }
}

fn validate_begin_request(request: &PreparationBeginRequest) -> Result<(), PreparationError> {
    if request.scope != "pcf004-writing-v1" || request.scope.len() > MAX_SCOPE_BYTES {
        return Err(PreparationError::InvalidFormat("scope"));
    }
    validate_optional_head(
        request.expected_validation_generation,
        request.expected_validation_catalog_sha256.as_deref(),
    )?;
    validate_opaque(&request.source_owner_id, "source owner ID")?;
    if request.source_schema_version == 0 || request.source_mutation_generation == 0 {
        return Err(PreparationError::InvalidFormat("source state"));
    }
    validate_hash(&request.source_inventory_sha256)
}

fn validate_resume_identity(
    snapshot: &PreparationSnapshot,
    request: &PreparationBeginRequest,
) -> Result<(), PreparationError> {
    validate_reconciliation_identity(snapshot, request)?;
    if request.source_mutation_generation < snapshot.source_mutation_generation {
        return Err(PreparationError::StaleSourceState);
    }
    if request.source_mutation_generation > snapshot.source_mutation_generation {
        return Err(PreparationError::ActiveConflict("source generation"));
    }
    if request.source_inventory_sha256 != snapshot.source_inventory_sha256 {
        return Err(PreparationError::ActiveConflict("source inventory"));
    }
    Ok(())
}

fn validate_reconciliation_identity(
    snapshot: &PreparationSnapshot,
    request: &PreparationBeginRequest,
) -> Result<(), PreparationError> {
    if request.scope != snapshot.scope {
        return Err(PreparationError::ActiveConflict("scope"));
    }
    if request.source_owner_id != snapshot.source_owner_id {
        return Err(PreparationError::ActiveConflict("source owner"));
    }
    if request.source_schema_version != snapshot.source_inventory_version {
        return Err(PreparationError::ActiveConflict("source schema"));
    }
    if request.expected_validation_generation != snapshot.expected_validation_generation
        || request.expected_validation_catalog_sha256 != snapshot.expected_validation_catalog_sha256
    {
        return Err(PreparationError::ActiveConflict("validation head"));
    }
    Ok(())
}

fn status_from_loaded(
    loaded: &LoadedPreparation,
    disposition: PreparationOpenDisposition,
) -> Result<PreparationStatus, PreparationError> {
    let next_descriptor_segment = u32::try_from(loaded.snapshot.manifest_segments.len())
        .map_err(|_| PreparationError::LimitExceeded("descriptor cursor"))?;
    let next_intent_segment = u32::try_from(loaded.snapshot.final_intent_segments.len())
        .map_err(|_| PreparationError::LimitExceeded("intent cursor"))?;
    Ok(PreparationStatus {
        preparation_id: loaded.snapshot.preparation_id.clone(),
        snapshot_sequence: loaded.snapshot.sequence,
        snapshot_ciphertext_sha256: loaded.head.snapshot_ciphertext_sha256.clone(),
        pointer_sha256: loaded.pointer_sha256.clone(),
        state: loaded.snapshot.state,
        source_schema_version: loaded.snapshot.source_inventory_version,
        source_mutation_generation: loaded.snapshot.source_mutation_generation,
        source_inventory_sha256: loaded.snapshot.source_inventory_sha256.clone(),
        total_objects: loaded.snapshot.total_objects,
        total_plaintext_bytes: loaded.snapshot.total_plaintext_bytes,
        total_ciphertext_bytes: loaded.snapshot.total_ciphertext_bytes,
        descriptor_manifest_root_sha256: loaded.snapshot.manifest_root_sha256.clone(),
        next_descriptor_segment,
        next_intent_segment,
        disposition,
    })
}

fn map_validation_publication_hook<F>(
    point: super::CommitFailurePoint,
    hook: &mut F,
) -> io::Result<()>
where
    F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
{
    match point {
        super::CommitFailurePoint::Publication {
            target: super::PublicationTarget::Catalog,
            phase,
        } => hook(PreparationPublicationTarget::ValidationCatalog, phase),
        super::CommitFailurePoint::Publication {
            target: super::PublicationTarget::ValidationHead,
            phase,
        } => hook(PreparationPublicationTarget::ValidationHead, phase),
        _ => Ok(()),
    }
}

fn completed_receipt(
    loaded: &LoadedPreparation,
    keys: &FrkSubkeys,
    validation_head: &crate::head::HeadRecord,
) -> Result<PreparationReceipt, PreparationError> {
    Ok(PreparationReceipt {
        schema_version: PREPARATION_SCHEMA_VERSION,
        core_id: loaded.snapshot.core_id.clone(),
        preparation_id: loaded.snapshot.preparation_id.clone(),
        receipt_id: deterministic_receipt_id(
            keys,
            &loaded.snapshot.preparation_id,
            PreparationReceiptOutcome::Completed,
            &loaded.head.snapshot_ciphertext_sha256,
        )?,
        outcome: PreparationReceiptOutcome::Completed,
        required_frk_version: loaded.snapshot.required_frk_version,
        final_snapshot_sequence: loaded.snapshot.sequence,
        final_snapshot_ciphertext_sha256: loaded.head.snapshot_ciphertext_sha256.clone(),
        pointer_sha256: loaded.pointer_sha256.clone(),
        validation_generation: Some(validation_head.generation()),
        validation_catalog_sha256: Some(validation_head.catalog_hash().to_owned()),
    })
}

fn deterministic_receipt_id(
    keys: &FrkSubkeys,
    preparation_id: &str,
    outcome: PreparationReceiptOutcome,
    final_snapshot_ciphertext_sha256: &str,
) -> Result<String, PreparationError> {
    let mut context = Vec::with_capacity(128);
    context.extend_from_slice(b"anima-corefs-preparation-receipt-id-v1\0");
    context.extend_from_slice(preparation_id.as_bytes());
    context.push(0);
    context.extend_from_slice(outcome.as_str().as_bytes());
    context.push(0);
    context.extend_from_slice(final_snapshot_ciphertext_sha256.as_bytes());
    let hkdf = hkdf::Hkdf::<Sha256>::from_prk(keys.preparation().as_slice())
        .map_err(|_| CryptoError::Derivation)?;
    let mut keyed = [0_u8; 32];
    hkdf.expand(&context, &mut keyed)
        .map_err(|_| CryptoError::Derivation)?;
    Ok(OpaqueId::derive_migration("preparation-receipt-v1", &keyed)
        .map_err(|_| PreparationError::InvalidFormat("receipt ID"))?
        .as_str()
        .to_owned())
}

fn receipt_file_name(receipt_id: &str) -> String {
    format!("{receipt_id}.prep-receipt.acore")
}

fn publish_or_verify_receipt<F>(
    layout: &PreparationLayout,
    keys: &FrkSubkeys,
    receipt: &PreparationReceipt,
    hook: &mut F,
) -> Result<(), PreparationError>
where
    F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
{
    let name = receipt_file_name(&receipt.receipt_id);
    match super::read_bounded_in(
        &layout.receipts,
        OsStr::new(&name),
        MAX_PREPARATION_RECEIPT_ENVELOPE_SIZE,
    ) {
        Ok(encoded) => {
            let durable = PreparationReceipt::open(
                &encoded,
                keys,
                &receipt.core_id,
                receipt.required_frk_version,
            )
            .map_err(|_| PreparationError::ReceiptConflict)?;
            if &durable != receipt {
                return Err(PreparationError::ReceiptConflict);
            }
            Ok(())
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let sealed = receipt.seal(keys)?;
            publish_immutable_in_with_hook(
                &layout.receipts,
                OsStr::new(&name),
                sealed.as_bytes(),
                &mut |phase| hook(PreparationPublicationTarget::Receipt, phase),
            )?;
            Ok(())
        }
        Err(_) => Err(PreparationError::ReceiptConflict),
    }
}

fn load_completed_receipt(
    coordinator: &super::CoreCommitCoordinator,
    layout: &PreparationLayout,
    keys: &FrkSubkeys,
    preparation_id: &str,
    expected: &PreparationCas,
) -> Result<PreparationReceipt, PreparationError> {
    let mut matched = None;
    let mut inspected = 0_usize;
    for entry in layout.receipts.entries()? {
        let entry = entry?;
        let name = entry.file_name();
        if !name.to_string_lossy().ends_with(".prep-receipt.acore") {
            continue;
        }
        inspected = inspected
            .checked_add(1)
            .ok_or(PreparationError::LimitExceeded("preparation receipts"))?;
        if inspected > 8 {
            return Err(PreparationError::ReceiptConflict);
        }
        let encoded = super::read_bounded_in(
            &layout.receipts,
            &name,
            MAX_PREPARATION_RECEIPT_ENVELOPE_SIZE,
        )
        .map_err(|_| PreparationError::ReceiptConflict)?;
        let receipt =
            PreparationReceipt::open(&encoded, keys, &coordinator.core_id, keys.frk_version())
                .map_err(|_| PreparationError::ReceiptConflict)?;
        if name != OsStr::new(&receipt_file_name(&receipt.receipt_id))
            || receipt.receipt_id
                != deterministic_receipt_id(
                    keys,
                    &receipt.preparation_id,
                    receipt.outcome,
                    &receipt.final_snapshot_ciphertext_sha256,
                )?
        {
            return Err(PreparationError::ReceiptConflict);
        }
        if receipt.preparation_id == preparation_id
            && receipt.outcome == PreparationReceiptOutcome::Completed
            && receipt.final_snapshot_sequence == expected.snapshot_sequence
            && receipt.pointer_sha256 == expected.pointer_sha256
            && matched.replace(receipt).is_some()
        {
            return Err(PreparationError::ReceiptConflict);
        }
    }
    let receipt = matched.ok_or(PreparationError::Missing)?;
    let validation = coordinator
        .load_validation_snapshot(keys)?
        .ok_or(PreparationError::ValidationHeadConflict)?;
    if receipt.validation_generation != Some(validation.head().generation())
        || receipt.validation_catalog_sha256.as_deref() != Some(validation.head().catalog_hash())
    {
        return Err(PreparationError::ValidationHeadConflict);
    }
    Ok(receipt)
}

fn clear_preparation_head_exact<F>(
    fs_dir: &Dir,
    expected_pointer_sha256: &str,
    hook: &mut F,
) -> Result<(), PreparationError>
where
    F: FnMut(PreparationPublicationTarget, PublicationPhase) -> io::Result<()>,
{
    let pointer = read_pointer_bytes(fs_dir)?.ok_or(PreparationError::CasConflict)?;
    if sha256_hex(&pointer) != expected_pointer_sha256 {
        return Err(PreparationError::CasConflict);
    }
    hook(
        PreparationPublicationTarget::Clear,
        PublicationPhase::PayloadSynced,
    )?;
    fs_dir.remove_file(PREPARATION_HEAD_FILE)?;
    hook(
        PreparationPublicationTarget::Clear,
        PublicationPhase::DestinationPublished,
    )?;
    #[cfg(not(windows))]
    crate::publication::sync_directory(fs_dir)?;
    hook(
        PreparationPublicationTarget::Clear,
        PublicationPhase::DestinationSynced,
    )?;
    Ok(())
}

fn create_preparation_layout(
    preparations: &Dir,
    preparation_id: &str,
) -> Result<PreparationLayout, PreparationError> {
    validate_opaque(preparation_id, "preparation ID")?;
    let preparation = super::ensure_child_directory(preparations, preparation_id)?;
    Ok(PreparationLayout {
        snapshots: super::ensure_child_directory(&preparation, SNAPSHOTS_DIRECTORY)?,
        descriptors: super::ensure_child_directory(&preparation, DESCRIPTORS_DIRECTORY)?,
        intent: super::ensure_child_directory(&preparation, INTENT_DIRECTORY)?,
        receipts: super::ensure_child_directory(&preparation, RECEIPTS_DIRECTORY)?,
    })
}

fn open_preparation_layout(
    preparations: &Dir,
    preparation_id: &str,
) -> Result<PreparationLayout, PreparationError> {
    validate_opaque(preparation_id, "preparation ID")?;
    let preparation = open_required_directory(preparations, preparation_id)?;
    Ok(PreparationLayout {
        snapshots: open_required_directory(&preparation, SNAPSHOTS_DIRECTORY)?,
        descriptors: open_required_directory(&preparation, DESCRIPTORS_DIRECTORY)?,
        intent: open_required_directory(&preparation, INTENT_DIRECTORY)?,
        receipts: open_required_directory(&preparation, RECEIPTS_DIRECTORY)?,
    })
}

fn open_required_directory(parent: &Dir, name: &str) -> Result<Dir, PreparationError> {
    let directory = parent
        .open_dir(name)
        .map_err(|_| PreparationError::InvalidLayout)?;
    super::validate_linked_directory(parent, name, &directory)
        .map_err(|_| PreparationError::InvalidLayout)?;
    Ok(directory)
}

fn read_pointer_bytes(fs_dir: &Dir) -> Result<Option<Vec<u8>>, PreparationError> {
    match super::read_bounded_in(
        fs_dir,
        OsStr::new(PREPARATION_HEAD_FILE),
        MAX_PREPARATION_HEAD_ENVELOPE_SIZE,
    ) {
        Ok(encoded) => Ok(Some(encoded)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(PreparationError::CorruptPointer),
    }
}

fn authenticate_pointer(
    pointer: &[u8],
    keys: &FrkSubkeys,
    core_id: &str,
) -> Result<(PreparationHeadRecord, String), PreparationError> {
    if pointer.len() >= 17 && pointer.get(..8) == Some(ENVELOPE_MAGIC.as_slice()) {
        let required = u32::from_le_bytes(pointer[13..17].try_into().expect("fixed slice"));
        if required != keys.frk_version() {
            return Err(PreparationError::WrongFrkVersion {
                required,
                provided: keys.frk_version(),
            });
        }
    }
    let head = PreparationHeadRecord::open(pointer, keys, core_id, keys.frk_version())
        .map_err(|_| PreparationError::CorruptPointer)?;
    Ok((head, sha256_hex(pointer)))
}

fn validate_reconciliation_snapshot_manifest(
    snapshot: &PreparationSnapshot,
) -> Result<(), PreparationError> {
    let descriptor_count = snapshot
        .manifest_segments
        .iter()
        .try_fold(0_u64, |total, reference| {
            total.checked_add(u64::from(reference.item_count))
        })
        .ok_or(PreparationError::CorruptSnapshot)?;
    if descriptor_count != u64::from(snapshot.total_objects)
        || manifest_root(&snapshot.manifest_segments) != snapshot.manifest_root_sha256
    {
        return Err(PreparationError::CorruptSnapshot);
    }
    match (
        snapshot.final_intent_root_sha256.as_deref(),
        snapshot.final_intent_segments.is_empty(),
    ) {
        (None, true) => {}
        (Some(root), false) if root == manifest_root(&snapshot.final_intent_segments) => {}
        _ => return Err(PreparationError::CorruptSnapshot),
    }
    Ok(())
}

fn validate_referenced_segments(
    layout: &PreparationLayout,
    snapshot: &PreparationSnapshot,
    keys: &FrkSubkeys,
    core_id: &str,
) -> Result<(), PreparationError> {
    let mut descriptor_count = 0_u64;
    let mut descriptor_plaintext_total = 0_u64;
    let mut descriptor_ciphertext_total = 0_u64;
    for reference in &snapshot.manifest_segments {
        let record = read_descriptor_segment(layout, snapshot, keys, reference)?;
        for descriptor in &record.descriptors {
            if descriptor.preparation_ordinal != descriptor_count {
                return Err(PreparationError::CorruptReferencedRecord {
                    kind: PreparationReferenceKind::Descriptor,
                    segment_index: reference.segment_index,
                });
            }
            descriptor_count = descriptor_count.checked_add(1).ok_or(
                PreparationError::CorruptReferencedRecord {
                    kind: PreparationReferenceKind::Descriptor,
                    segment_index: reference.segment_index,
                },
            )?;
            #[cfg(not(test))]
            let descriptor_body_length = descriptor.body_length;
            #[cfg(test)]
            let descriptor_body_length = descriptor
                .logical_body_length
                .unwrap_or(descriptor.body_length);
            descriptor_plaintext_total = descriptor_plaintext_total
                .checked_add(descriptor_body_length)
                .ok_or(PreparationError::CorruptReferencedRecord {
                    kind: PreparationReferenceKind::Descriptor,
                    segment_index: reference.segment_index,
                })?;
            descriptor_ciphertext_total = descriptor_ciphertext_total
                .checked_add(descriptor.encoded_size)
                .ok_or(PreparationError::CorruptReferencedRecord {
                    kind: PreparationReferenceKind::Descriptor,
                    segment_index: reference.segment_index,
                })?;
        }
    }
    if descriptor_count != u64::from(snapshot.total_objects)
        || descriptor_plaintext_total != snapshot.total_plaintext_bytes
        || descriptor_ciphertext_total != snapshot.total_ciphertext_bytes
        || manifest_root(&snapshot.manifest_segments) != snapshot.manifest_root_sha256
    {
        return Err(PreparationError::CorruptSnapshot);
    }
    let mut intent_count = 0_u64;
    let mut intent_ids = HashSet::new();
    for reference in &snapshot.final_intent_segments {
        let name = format!(
            "{:020}-{}.prep-intent.acore",
            reference.segment_index, reference.ciphertext_sha256
        );
        let encoded = read_referenced_record(
            &layout.intent,
            &name,
            MAX_FINAL_INTENT_SEGMENT_ENVELOPE_SIZE,
            PreparationReferenceKind::Intent,
            reference.segment_index,
        )?;
        if sha256_hex(&encoded) != reference.ciphertext_sha256 {
            return Err(PreparationError::CorruptReferencedRecord {
                kind: PreparationReferenceKind::Intent,
                segment_index: reference.segment_index,
            });
        }
        let record = FinalIntentSegment::open(&encoded, keys, core_id, keys.frk_version())
            .map_err(|_| PreparationError::CorruptReferencedRecord {
                kind: PreparationReferenceKind::Intent,
                segment_index: reference.segment_index,
            })?;
        let plaintext_bytes = u32::try_from(record.encode()?.len()).map_err(|_| {
            PreparationError::CorruptReferencedRecord {
                kind: PreparationReferenceKind::Intent,
                segment_index: reference.segment_index,
            }
        })?;
        if record.preparation_id != snapshot.preparation_id
            || record.segment_index != reference.segment_index
            || u32::try_from(record.entries.len()).ok() != Some(reference.item_count)
            || plaintext_bytes != reference.plaintext_bytes
        {
            return Err(PreparationError::CorruptReferencedRecord {
                kind: PreparationReferenceKind::Intent,
                segment_index: reference.segment_index,
            });
        }
        for entry in &record.entries {
            if entry.ordinal != intent_count || !intent_ids.insert(entry.stable_id.clone()) {
                return Err(PreparationError::CorruptReferencedRecord {
                    kind: PreparationReferenceKind::Intent,
                    segment_index: reference.segment_index,
                });
            }
            intent_count =
                intent_count
                    .checked_add(1)
                    .ok_or(PreparationError::CorruptReferencedRecord {
                        kind: PreparationReferenceKind::Intent,
                        segment_index: reference.segment_index,
                    })?;
        }
    }
    Ok(())
}

fn read_referenced_record(
    directory: &Dir,
    name: &str,
    limit: usize,
    kind: PreparationReferenceKind,
    segment_index: u32,
) -> Result<Vec<u8>, PreparationError> {
    #[cfg(test)]
    if kind == PreparationReferenceKind::Descriptor {
        RECONCILIATION_INSTRUMENTATION.with(|active| {
            if let Some(instrumentation) = active.borrow().as_ref() {
                instrumentation
                    .descriptor_segment_reads
                    .fetch_add(1, Ordering::SeqCst);
            }
        });
    }
    match super::read_bounded_in(directory, OsStr::new(name), limit) {
        Ok(encoded) => Ok(encoded),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            Err(PreparationError::MissingReferencedRecord {
                kind,
                segment_index,
            })
        }
        Err(_) => Err(PreparationError::CorruptReferencedRecord {
            kind,
            segment_index,
        }),
    }
}

fn empty_manifest_root() -> String {
    manifest_root(&[])
}

fn unix_time_millis() -> Result<u64, PreparationError> {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| PreparationError::InvalidFormat("system clock"))?
        .as_millis();
    u64::try_from(millis).map_err(|_| PreparationError::LimitExceeded("system clock"))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest: [u8; 32] = Sha256::digest(bytes).into();
    hex_bytes(&digest)
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
