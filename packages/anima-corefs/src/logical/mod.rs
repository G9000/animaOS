//! Read-only logical CoreFS operations bound to an explicit catalog snapshot.

mod backend;
mod path;
mod service;

pub use backend::{CoreFsReadSnapshot, LogicalError};
pub use path::{LogicalPath, LogicalPathError};
pub use service::{
    ListCursor, LogicalEntry, LogicalGlobCursor, LogicalGlobPage, LogicalGrepCursor,
    LogicalGrepMatch, LogicalGrepPage, LogicalGrepRequest, LogicalGrepSkipped, LogicalListPage,
    LogicalReadChunk, LogicalReadStream, LogicalStat, LogicalWalkCursor, LogicalWalkEntry,
    LogicalWalkOptions, LogicalWalkPage, RuntimeSearchState, SearchNotReadyReason,
    SearchReadinessReport, SearchReadinessStatus,
};
