//! Read-only logical CoreFS operations bound to an explicit catalog snapshot.

mod backend;
mod path;
mod service;
mod wire;

pub use backend::{CoreFsReadSnapshot, LogicalError};
pub use path::{LogicalPath, LogicalPathError};
pub use service::{
    ListCursor, LogicalEntry, LogicalGlobCursor, LogicalGlobPage, LogicalGrepCursor,
    LogicalGrepMatch, LogicalGrepPage, LogicalGrepRequest, LogicalGrepSkipped, LogicalListPage,
    LogicalReadChunk, LogicalReadStream, LogicalStat, LogicalWalkCursor, LogicalWalkEntry,
    LogicalWalkOptions, LogicalWalkPage, RuntimeSearchState, SearchNotReadyReason,
    SearchReadinessReport, SearchReadinessStatus,
};
pub use wire::{
    model_wire_v1_max_read_payload, model_wire_v1_read_chunk_size, ModelWireV1, MODEL_WIRE_V1,
};
