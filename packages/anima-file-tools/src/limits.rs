// SPDX-License-Identifier: Apache-2.0
// Bounded-operation defaults adapted from OpenAI Codex codex-rs/file-system/src/lib.rs at
// 9e552e9d15ba52bed7077d5357f3e18e330f8f38; limits and API rewritten for ANIMA backends.

use thiserror::Error;

pub const MAX_READ_CHUNK_BYTES: usize = 1024 * 1024;
pub const MAX_PATTERN_BYTES: usize = 64 * 1024;
pub const MAX_WALK_DEPTH: usize = 64;
pub const MAX_WALK_DIRECTORIES: usize = 10_000;
pub const MAX_WALK_ENTRIES: usize = 50_000;
pub const MAX_WALK_ERRORS: usize = 64;
pub const MAX_WALK_ERROR_MESSAGE_BYTES: usize = 512;
pub const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

/// Requested per-operation limits. Validation converts these into a type that
/// bounded engines accept, so callers cannot silently bypass production caps.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct OperationLimits {
    pub read_chunk_bytes: usize,
    pub walk_depth: usize,
    pub walk_directories: usize,
    pub walk_entries: usize,
    pub response_bytes: usize,
}

impl Default for OperationLimits {
    fn default() -> Self {
        Self {
            read_chunk_bytes: MAX_READ_CHUNK_BYTES,
            walk_depth: MAX_WALK_DEPTH,
            walk_directories: MAX_WALK_DIRECTORIES,
            walk_entries: MAX_WALK_ENTRIES,
            response_bytes: MAX_RESPONSE_BYTES,
        }
    }
}

impl OperationLimits {
    pub fn validate(self) -> Result<ValidatedLimits, LimitError> {
        validate(
            "read_chunk_bytes",
            self.read_chunk_bytes,
            MAX_READ_CHUNK_BYTES,
        )?;
        validate("walk_depth", self.walk_depth, MAX_WALK_DEPTH)?;
        validate(
            "walk_directories",
            self.walk_directories,
            MAX_WALK_DIRECTORIES,
        )?;
        validate("walk_entries", self.walk_entries, MAX_WALK_ENTRIES)?;
        validate("response_bytes", self.response_bytes, MAX_RESPONSE_BYTES)?;
        Ok(ValidatedLimits(self))
    }
}

fn validate(field: &'static str, requested: usize, maximum: usize) -> Result<(), LimitError> {
    if requested == 0 {
        return Err(LimitError::MustBePositive { field });
    }
    if requested > maximum {
        return Err(LimitError::ExceedsMaximum {
            field,
            requested,
            maximum,
        });
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ValidatedLimits(OperationLimits);

impl ValidatedLimits {
    pub const fn read_chunk_bytes(self) -> usize {
        self.0.read_chunk_bytes
    }

    pub const fn walk_depth(self) -> usize {
        self.0.walk_depth
    }

    pub const fn walk_directories(self) -> usize {
        self.0.walk_directories
    }

    pub const fn walk_entries(self) -> usize {
        self.0.walk_entries
    }

    pub const fn response_bytes(self) -> usize {
        self.0.response_bytes
    }
}

#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum LimitError {
    #[error("{field} must be greater than zero")]
    MustBePositive { field: &'static str },
    #[error("{field}={requested} exceeds the production maximum {maximum}")]
    ExceedsMaximum {
        field: &'static str,
        requested: usize,
        maximum: usize,
    },
}
