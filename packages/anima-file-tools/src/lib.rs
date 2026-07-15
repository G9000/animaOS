// SPDX-License-Identifier: Apache-2.0

//! Bounded file-operation contracts shared by explicit ANIMA storage backends.

mod backend;
mod control;
mod error;
mod glob;
mod limits;
mod patch;
mod read;
mod search;
mod text;
mod walk;

pub use backend::{
    BackendCapabilities, BackendKind, BackendPath, DirectoryEntry, DirectoryListing, EntryKind,
    EntryMetadata, FileBackend, MutationAtomicity, PathSemantics, ReadBackend, ReadSeek,
    SearchableBackend, WalkBackend, MAX_BACKEND_PATH_BYTES,
};
pub use control::{CancellationToken, OperationControl};
pub use error::{FileToolError, TextReadIssue};
pub use glob::{glob, GlobCursor, GlobPage, GlobRequest};
pub use limits::{
    LimitError, OperationLimits, ValidatedLimits, MAX_PATTERN_BYTES, MAX_READ_CHUNK_BYTES,
    MAX_RESPONSE_BYTES, MAX_WALK_DEPTH, MAX_WALK_DIRECTORIES, MAX_WALK_ENTRIES, MAX_WALK_ERRORS,
    MAX_WALK_ERROR_MESSAGE_BYTES,
};
pub use patch::{
    parse_patch, plan_patch, Patch, PatchChunk, PatchError, PatchOperation, PatchPath, PatchPlan,
    PatchSnapshot, PlannedMutation, MAX_PATCH_BYTES, MAX_PATCH_OPERATIONS,
};
pub use read::{read_stream, ReadChunk, ReadOptions, ReadStream};
pub use search::{
    grep, GrepCursor, GrepMatch, GrepMode, GrepPage, GrepRequest, GrepSkipped, SkipReason,
};
pub use text::{read_text_lines, TextLine, TextReadPage, TextReadRequest};
pub use walk::{walk_page, WalkCursor, WalkEntry, WalkError, WalkOptions, WalkPage};
