// SPDX-License-Identifier: Apache-2.0

use serde::{Deserialize, Serialize};
use std::io::{Read, Seek, SeekFrom};

use crate::{FileToolError, OperationControl};

pub const MAX_BACKEND_PATH_BYTES: usize = 32 * 1024;

/// The authority domain selected by the caller.
///
/// Backends are passed explicitly. File operations must never infer this value
/// from a path or URI string.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BackendKind {
    HostFs,
    CoreFs,
}

/// Path and lookup behavior implemented by a backend.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PathSemantics {
    HostNative,
    PortableNfcCaseSensitive,
}

/// Multi-item publication guarantee implemented by a backend.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationAtomicity {
    BestEffort,
    CatalogGeneration,
}

/// Capabilities reported alongside every concrete backend handle.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendCapabilities {
    backend: BackendKind,
    path_semantics: PathSemantics,
    mutation_atomicity: MutationAtomicity,
}

impl BackendCapabilities {
    pub const fn new(
        backend: BackendKind,
        path_semantics: PathSemantics,
        mutation_atomicity: MutationAtomicity,
    ) -> Self {
        Self {
            backend,
            path_semantics,
            mutation_atomicity,
        }
    }

    pub const fn backend(self) -> BackendKind {
        self.backend
    }

    pub const fn path_semantics(self) -> PathSemantics {
        self.path_semantics
    }

    pub const fn mutation_atomicity(self) -> MutationAtomicity {
        self.mutation_atomicity
    }
}

/// A path paired with the authority domain that is allowed to interpret it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BackendPath {
    backend: BackendKind,
    value: String,
}

impl BackendPath {
    pub fn new(backend: BackendKind, value: impl Into<String>) -> Result<Self, FileToolError> {
        let value = value.into();
        if value.len() > MAX_BACKEND_PATH_BYTES {
            return Err(FileToolError::InvalidPath {
                path: "<oversized path>".to_string(),
                reason: format!(
                    "path is {} bytes; maximum is {MAX_BACKEND_PATH_BYTES}",
                    value.len()
                ),
            });
        }
        if value.contains('\0') {
            return Err(FileToolError::InvalidPath {
                path: value,
                reason: "paths must not contain NUL bytes".to_string(),
            });
        }
        Ok(Self { backend, value })
    }

    pub const fn backend(&self) -> BackendKind {
        self.backend
    }

    pub fn as_str(&self) -> &str {
        &self.value
    }
}

pub trait ReadSeek: Read + Seek {}

impl<T: Read + Seek + ?Sized> ReadSeek for T {}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EntryKind {
    Directory,
    File,
    Other,
}

/// Backend-authenticated content classification used by text-only tools.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContentClassification {
    /// The backend has no authoritative declaration; bounded text probing is used.
    Unknown,
    Text,
    Binary,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EntryMetadata {
    pub kind: EntryKind,
    pub is_symlink: bool,
    pub size: u64,
    pub content: ContentClassification,
}

impl EntryMetadata {
    pub const fn file(size: u64) -> Self {
        Self {
            kind: EntryKind::File,
            is_symlink: false,
            size,
            content: ContentClassification::Unknown,
        }
    }

    pub const fn text_file(size: u64) -> Self {
        Self {
            kind: EntryKind::File,
            is_symlink: false,
            size,
            content: ContentClassification::Text,
        }
    }

    pub const fn binary_file(size: u64) -> Self {
        Self {
            kind: EntryKind::File,
            is_symlink: false,
            size,
            content: ContentClassification::Binary,
        }
    }

    pub const fn directory(is_symlink: bool) -> Self {
        Self {
            kind: EntryKind::Directory,
            is_symlink,
            size: 0,
            content: ContentClassification::Unknown,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DirectoryEntry {
    pub path: BackendPath,
    pub metadata: EntryMetadata,
}

impl DirectoryEntry {
    pub const fn new(path: BackendPath, metadata: EntryMetadata) -> Self {
        Self { path, metadata }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct DirectoryListing {
    pub entries: Vec<DirectoryEntry>,
    pub truncated: bool,
}

impl From<Vec<DirectoryEntry>> for DirectoryListing {
    fn from(entries: Vec<DirectoryEntry>) -> Self {
        Self {
            entries,
            truncated: false,
        }
    }
}

/// Backend identity shared by all bounded algorithms.
/// Authorization and path normalization remain backend responsibilities.
pub trait FileBackend: Send + Sync {
    fn capabilities(&self) -> BackendCapabilities;
}

/// Storage primitive required by the bounded read engine.
pub trait ReadBackend: FileBackend {
    fn open_read(&self, path: &str) -> Result<Box<dyn ReadSeek + Send>, FileToolError>;

    /// Opens and positions a reader while preserving the operation's
    /// cancellation and deadline contract. Backends with expensive logical
    /// positioning can override this; native files retain an efficient seek.
    fn open_read_at(
        &self,
        path: &str,
        offset: u64,
        _max_bytes: usize,
        control: &OperationControl,
    ) -> Result<Box<dyn ReadSeek + Send>, FileToolError> {
        control.check()?;
        let mut reader = self.open_read(path)?;
        control.check()?;
        reader
            .seek(SeekFrom::Start(offset))
            .map_err(|error| FileToolError::Backend {
                operation: "seek",
                path: path.to_owned(),
                message: error.to_string(),
            })?;
        control.check()?;
        Ok(reader)
    }
}

/// Storage primitives required by the bounded directory walker.
pub trait WalkBackend: FileBackend {
    fn metadata(&self, path: &str) -> Result<EntryMetadata, FileToolError>;

    fn read_directory(&self, path: &str) -> Result<DirectoryListing, FileToolError>;
}

pub trait SearchableBackend: ReadBackend + WalkBackend {}

impl<T: ReadBackend + WalkBackend> SearchableBackend for T {}
