use std::collections::BTreeMap;

use crate::crypto::ObjectKind;
use crate::envelope::{BodyEncoding, MAX_BODY_LENGTH};

#[allow(dead_code)]
mod executor;
#[allow(dead_code)]
mod operations;
#[allow(dead_code)]
mod patch;
#[allow(dead_code)]
mod preflight;
#[allow(dead_code)]
mod preparation;

pub const CORE_FS_MIGRATION_WRITE_FROZEN: &str = "corefs_migration_write_frozen";

pub use executor::CoreFsMutationExecutor;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MutationTarget {
    Path(String),
    StableId(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PatchAddFormat {
    pub stable_id: Option<String>,
    pub kind: ObjectKind,
    pub content_type: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LogicalMutation {
    ActivateAuthority,
    Mkdir {
        path: String,
        reserved_role: Option<String>,
    },
    Create {
        path: String,
        stable_id: Option<String>,
        kind: ObjectKind,
        content_type: String,
        bytes: Vec<u8>,
    },
    Write {
        target: MutationTarget,
        expected_revision: u64,
        content_type: String,
        bytes: Vec<u8>,
    },
    ApplyPatch {
        patch: String,
        expected_revisions: BTreeMap<String, u64>,
        add_formats: BTreeMap<String, PatchAddFormat>,
        trash_folder: MutationTarget,
    },
    Move {
        source: MutationTarget,
        destination: String,
        expected_revision: Option<u64>,
    },
    Trash {
        target: MutationTarget,
        trash_folder: MutationTarget,
        expected_revision: Option<u64>,
    },
    Restore {
        target: MutationTarget,
        destination: Option<String>,
        expected_revision: Option<u64>,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MutationPrincipal {
    User,
    Anima,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MutationCommitMode {
    FirstMutation { cutover_epoch: u64 },
    Normal,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct CoreFsMutationFacade;

impl CoreFsMutationFacade {
    pub fn execute(
        &self,
        _operation: LogicalMutation,
    ) -> Result<MutationResult, PublicMutationError> {
        Err(PublicMutationError::MigrationWriteFrozen)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum PublicMutationError {
    #[error("CoreFS writes remain frozen until migration cutover")]
    MigrationWriteFrozen,
}

impl PublicMutationError {
    pub const fn code(self) -> &'static str {
        match self {
            Self::MigrationWriteFrozen => CORE_FS_MIGRATION_WRITE_FROZEN,
        }
    }
}

pub trait ContentFormatValidator {
    fn validate(
        &self,
        kind: ObjectKind,
        content_type: &str,
        bytes: &[u8],
    ) -> Result<ValidatedContent, ContentValidationError>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatedContent {
    bytes: Vec<u8>,
    content_type: String,
    body_encoding: BodyEncoding,
}

impl ValidatedContent {
    pub fn new(
        bytes: Vec<u8>,
        content_type: impl Into<String>,
        body_encoding: BodyEncoding,
    ) -> Result<Self, ContentValidationError> {
        if bytes.len() as u64 > MAX_BODY_LENGTH {
            return Err(ContentValidationError::SizeLimit);
        }
        let content_type = content_type.into();
        if content_type.is_empty()
            || content_type.len() > 255
            || content_type.chars().any(char::is_control)
        {
            return Err(ContentValidationError::InvalidContentType);
        }
        Ok(Self {
            bytes,
            content_type,
            body_encoding,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ContentValidationError {
    #[error("content validator rejected the input: {0}")]
    Rejected(&'static str),
    #[error("validated content exceeds the CoreFS object limit")]
    SizeLimit,
    #[error("validated content type is invalid")]
    InvalidContentType,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MutationChange {
    pub stable_id: String,
    pub revision: Option<u64>,
    pub content_hash: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MutationResult {
    pub generation: u64,
    pub catalog_hash: String,
    pub changes: Vec<MutationChange>,
    pub atomic: bool,
    pub cutover_committed: bool,
    pub recovery_pending: bool,
    pub invalidation_delivered: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum MutationError {
    #[error("invalid logical mutation path")]
    InvalidPath,
    #[error("logical mutation target was not found")]
    NotFound,
    #[error("logical mutation target is not the required entry kind")]
    WrongEntryKind,
    #[error("logical mutation destination is occupied")]
    Collision,
    #[error("logical mutation expected revision is stale or missing")]
    RevisionConflict,
    #[error("logical mutation is denied by folder policy")]
    PolicyDenied,
    #[error("logical mutation cannot cross folder policy boundaries before policy migration")]
    PolicyBoundaryMismatch,
    #[error("reserved folder roles require the user principal")]
    ReservedRoleRequiresUser,
    #[error("reserved folder role is already bound")]
    RoleCollision,
    #[error("logical mutation lifecycle transition is invalid")]
    InvalidLifecycle,
    #[error("a folder cannot move into itself or its descendant")]
    SourceDescendant,
    #[error(transparent)]
    Format(#[from] ContentValidationError),
    #[error("logical mutation exceeds the CoreFS content limit")]
    SizeLimit,
    #[error("logical patch is invalid: {0}")]
    Patch(&'static str),
    #[error("logical patch is missing an expected object revision")]
    MissingExpectedRevision,
    #[error("selected validation snapshot changed before commit")]
    OptimisticConflict,
    #[error("immutable object preparation failed")]
    PrepareFailed,
    #[error("CoreFS logical mutation storage is unavailable")]
    Storage,
}

impl MutationError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::InvalidPath => "corefs_mutation_invalid_path",
            Self::NotFound => "corefs_mutation_not_found",
            Self::WrongEntryKind => "corefs_mutation_wrong_entry_kind",
            Self::Collision => "corefs_mutation_collision",
            Self::RevisionConflict => "corefs_mutation_revision_conflict",
            Self::PolicyDenied => "corefs_mutation_policy_denied",
            Self::PolicyBoundaryMismatch => "corefs_mutation_policy_boundary_mismatch",
            Self::ReservedRoleRequiresUser => "corefs_mutation_reserved_role_requires_user",
            Self::RoleCollision => "corefs_mutation_role_collision",
            Self::InvalidLifecycle => "corefs_mutation_invalid_lifecycle",
            Self::SourceDescendant => "corefs_mutation_source_descendant",
            Self::Format(_) => "corefs_mutation_invalid_content",
            Self::SizeLimit => "corefs_mutation_size_limit",
            Self::Patch(_) => "corefs_mutation_invalid_patch",
            Self::MissingExpectedRevision => "corefs_mutation_missing_expected_revision",
            Self::OptimisticConflict => "corefs_mutation_optimistic_conflict",
            Self::PrepareFailed => "corefs_mutation_prepare_failed",
            Self::Storage => "corefs_mutation_storage_unavailable",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MutationStamp {
    timestamp_ms: u64,
    timestamp_text: String,
}

impl MutationStamp {
    pub fn new(
        timestamp_ms: u64,
        timestamp_text: impl Into<String>,
    ) -> Result<Self, MutationError> {
        let timestamp_text = timestamp_text.into();
        if timestamp_ms == 0
            || timestamp_text.is_empty()
            || timestamp_text.chars().any(char::is_control)
        {
            return Err(MutationError::InvalidLifecycle);
        }
        Ok(Self {
            timestamp_ms,
            timestamp_text,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[allow(dead_code)]
pub(crate) enum ConverterPrincipal {
    User,
    Anima,
}

#[derive(Debug)]
#[allow(dead_code)]
pub(crate) struct ConverterMutationAuthority {
    _sealed: (),
}

impl ConverterMutationAuthority {
    #[allow(dead_code)]
    pub(crate) const fn new() -> Self {
        Self { _sealed: () }
    }
}

#[cfg(test)]
#[path = "mutation/tests.rs"]
mod tests;
