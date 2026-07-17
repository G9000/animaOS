use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use serde_json::{json, Value};

use anima_file_tools::{EntryKind, SkipReason};

use crate::crypto::ObjectKind;
use crate::envelope::BodyEncoding;

use super::service::{
    ListCursor, LogicalEntry, LogicalGlobPage, LogicalGrepMatch, LogicalGrepPage,
    LogicalGrepSkipped, LogicalListPage, LogicalReadChunk, LogicalStat, LogicalWalkEntry,
    LogicalWalkPage, SearchNotReadyReason, SearchReadinessReport, SearchReadinessStatus,
};
use super::{LogicalError, LogicalPath};

pub const MODEL_WIRE_V1: &str = "corefs-logical-v1";

/// The single model-visible encoding contract for logical CoreFS results.
/// Layer 3 adapters must reuse this trait instead of inventing another shape.
pub trait ModelWireV1 {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError>;

    fn model_wire_v1_size(&self) -> Result<usize, LogicalError> {
        self.to_model_wire_v1().map(|encoded| encoded.len())
    }
}

fn encode(result: Value) -> Result<Vec<u8>, LogicalError> {
    serde_json::to_vec(&json!({
        "version": MODEL_WIRE_V1,
        "result": result,
    }))
    .map_err(|_| LogicalError::ModelEncoding)
}

fn path_value(path: &LogicalPath) -> Value {
    Value::String(path.as_str().to_owned())
}

fn entry_kind(kind: EntryKind) -> &'static str {
    match kind {
        EntryKind::Directory => "directory",
        EntryKind::File => "file",
        EntryKind::Other => "other",
    }
}

fn object_kind(kind: Option<ObjectKind>) -> Value {
    kind.map(|value| Value::String(value.as_str().to_owned()))
        .unwrap_or(Value::Null)
}

fn body_encoding(encoding: Option<BodyEncoding>) -> Value {
    encoding
        .map(|value| {
            Value::String(
                match value {
                    BodyEncoding::Utf8 => "utf-8",
                    BodyEncoding::Binary => "binary",
                }
                .to_owned(),
            )
        })
        .unwrap_or(Value::Null)
}

fn skip_reason(reason: SkipReason) -> &'static str {
    match reason {
        SkipReason::BinaryContent => "binary_content",
        SkipReason::InvalidUtf8 => "invalid_utf8",
        SkipReason::LineTooLong => "line_too_long",
    }
}

fn entry_value(entry: &LogicalEntry) -> Value {
    json!({
        "path": path_value(&entry.path),
        "stableId": entry.stable_id,
        "revision": entry.revision,
        "contentHash": entry.content_hash,
        "kind": entry_kind(entry.kind),
        "objectKind": object_kind(entry.object_kind),
    })
}

fn walk_entry_value(entry: &LogicalWalkEntry) -> Value {
    json!({
        "path": path_value(&entry.path),
        "stableId": entry.stable_id,
        "revision": entry.revision,
        "contentHash": entry.content_hash,
        "kind": entry_kind(entry.kind),
        "objectKind": object_kind(entry.object_kind),
        "depth": entry.depth,
    })
}

fn grep_match_value(found: &LogicalGrepMatch) -> Value {
    json!({
        "path": path_value(&found.path),
        "stableId": found.stable_id,
        "revision": found.revision,
        "contentHash": found.content_hash,
        "lineNumber": found.line_number,
        "byteOffset": found.byte_offset,
        "excerpt": found.excerpt,
    })
}

fn grep_skip_value(skipped: &LogicalGrepSkipped) -> Value {
    json!({
        "path": path_value(&skipped.path),
        "stableId": skipped.stable_id,
        "revision": skipped.revision,
        "contentHash": skipped.content_hash,
        "reason": skip_reason(skipped.reason),
    })
}

fn read_chunk_value(chunk: &LogicalReadChunk, bytes_base64: String) -> Value {
    json!({
        "generation": chunk.generation,
        "path": path_value(&chunk.path),
        "stableId": chunk.stable_id,
        "revision": chunk.revision,
        "contentHash": chunk.content_hash,
        "offset": chunk.offset,
        "bytesBase64": bytes_base64,
    })
}

impl ModelWireV1 for LogicalEntry {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(entry_value(self))
    }
}

impl ModelWireV1 for LogicalStat {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(json!({
            "path": path_value(&self.path),
            "stableId": self.stable_id,
            "revision": self.revision,
            "contentHash": self.content_hash,
            "kind": entry_kind(self.kind),
            "objectKind": object_kind(self.object_kind),
            "bodyEncoding": body_encoding(self.body_encoding),
            "contentType": self.content_type,
            "size": self.size,
            "generation": self.generation,
        }))
    }
}

impl ModelWireV1 for LogicalListPage {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(list_page_value(
            self.generation,
            &self.entries,
            self.next_cursor.as_ref(),
            self.truncated,
        ))
    }
}

fn list_page_value(
    generation: u64,
    entries: &[LogicalEntry],
    next_cursor: Option<&ListCursor>,
    truncated: bool,
) -> Value {
    json!({
        "generation": generation,
        "entries": entries.iter().map(entry_value).collect::<Vec<_>>(),
        "nextCursor": next_cursor.map(|cursor| json!({
            "generation": cursor.generation(),
            "after": cursor.after(),
        })),
        "truncated": truncated,
    })
}

pub(super) fn logical_list_page_size(
    generation: u64,
    entries: &[LogicalEntry],
    next_cursor: Option<&ListCursor>,
    truncated: bool,
) -> Result<usize, LogicalError> {
    encode(list_page_value(generation, entries, next_cursor, truncated))
        .map(|encoded| encoded.len())
}

impl ModelWireV1 for LogicalWalkEntry {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(walk_entry_value(self))
    }
}

impl ModelWireV1 for LogicalWalkPage {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(json!({
            "generation": self.generation,
            "entries": self.entries.iter().map(walk_entry_value).collect::<Vec<_>>(),
            "errors": self.errors.iter().map(|(path, message)| json!({
                "path": path_value(path),
                "message": message,
            })).collect::<Vec<_>>(),
            "nextCursor": self.next_cursor.as_ref().map(|cursor| json!({
                "generation": cursor.generation(),
                "after": cursor.after(),
            })),
            "truncated": self.truncated,
            "limitReached": self.limit_reached,
        }))
    }
}

impl ModelWireV1 for LogicalGlobPage {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(json!({
            "generation": self.generation,
            "matches": self.matches.iter().map(entry_value).collect::<Vec<_>>(),
            "nextCursor": self.next_cursor.as_ref().map(|cursor| json!({
                "generation": cursor.generation(),
                "after": cursor.after(),
            })),
            "truncated": self.truncated,
            "limitReached": self.limit_reached,
        }))
    }
}

impl ModelWireV1 for LogicalGrepMatch {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(grep_match_value(self))
    }
}

impl ModelWireV1 for LogicalGrepSkipped {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(grep_skip_value(self))
    }
}

impl ModelWireV1 for LogicalGrepPage {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(json!({
            "generation": self.generation,
            "matches": self.matches.iter().map(grep_match_value).collect::<Vec<_>>(),
            "skipped": self.skipped.iter().map(grep_skip_value).collect::<Vec<_>>(),
            "nextCursor": self.next_cursor.as_ref().map(|cursor| json!({
                "generation": cursor.generation(),
                "path": cursor.path(),
                "byteOffset": cursor.byte_offset(),
                "walkAfter": cursor.walk_after(),
            })),
            "truncated": self.truncated,
            "limitReached": self.limit_reached,
        }))
    }
}

impl ModelWireV1 for LogicalReadChunk {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        encode(read_chunk_value(self, BASE64.encode(&self.bytes)))
    }
}

impl ModelWireV1 for SearchReadinessReport {
    fn to_model_wire_v1(&self) -> Result<Vec<u8>, LogicalError> {
        let status = match self.status {
            SearchReadinessStatus::Ready => json!({ "state": "ready" }),
            SearchReadinessStatus::NotReady(reason) => json!({
                "state": "not_ready",
                "reason": match reason {
                    SearchNotReadyReason::Missing => "missing",
                    SearchNotReadyReason::Building => "building",
                    SearchNotReadyReason::Degraded => "degraded",
                    SearchNotReadyReason::GenerationMismatch => "generation_mismatch",
                },
            }),
        };
        encode(json!({
            "catalogGeneration": self.catalog_generation,
            "indexGeneration": self.index_generation,
            "status": status,
        }))
    }
}

pub fn model_wire_v1_read_chunk_size(
    generation: u64,
    path: &LogicalPath,
    stable_id: &str,
    revision: u64,
    content_hash: &str,
    offset: u64,
    payload_bytes: usize,
) -> Result<usize, LogicalError> {
    let chunk = LogicalReadChunk {
        generation,
        path: path.clone(),
        stable_id: stable_id.to_owned(),
        revision,
        content_hash: content_hash.to_owned(),
        offset,
        bytes: Vec::new(),
    };
    let empty_size = encode(read_chunk_value(&chunk, String::new()))?.len();
    let groups = payload_bytes
        .checked_add(2)
        .ok_or(LogicalError::ModelEncoding)?
        / 3;
    empty_size
        .checked_add(groups.checked_mul(4).ok_or(LogicalError::ModelEncoding)?)
        .ok_or(LogicalError::ModelEncoding)
}

pub fn model_wire_v1_max_read_payload(
    generation: u64,
    path: &LogicalPath,
    stable_id: &str,
    revision: u64,
    content_hash: &str,
    offset: u64,
    response_bytes: usize,
) -> Result<usize, LogicalError> {
    let empty_size = model_wire_v1_read_chunk_size(
        generation,
        path,
        stable_id,
        revision,
        content_hash,
        offset,
        0,
    )?;
    Ok(response_bytes.saturating_sub(empty_size) / 4 * 3)
}
