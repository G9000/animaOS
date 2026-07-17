use anima_file_tools::{
    glob, grep, read_stream, walk_page, BackendKind, BackendPath, EntryKind, GlobCursor,
    GlobRequest, GrepCursor, GrepMode, GrepRequest, OperationControl, ReadOptions, ReadStream,
    SkipReason, ValidatedLimits, WalkBackend, WalkCursor, WalkOptions,
};

use crate::crypto::ObjectKind;
use crate::envelope::BodyEncoding;

use super::backend::{CoreFsReadSnapshot, LogicalError, Node};
use super::LogicalPath;

const RESPONSE_ENTRY_OVERHEAD: usize = 192;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalEntry {
    pub path: LogicalPath,
    pub stable_id: String,
    pub revision: Option<u64>,
    pub content_hash: Option<String>,
    pub kind: EntryKind,
    pub object_kind: Option<ObjectKind>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalStat {
    pub path: LogicalPath,
    pub stable_id: String,
    pub revision: Option<u64>,
    pub content_hash: Option<String>,
    pub kind: EntryKind,
    pub object_kind: Option<ObjectKind>,
    pub body_encoding: Option<BodyEncoding>,
    pub content_type: Option<String>,
    pub size: u64,
    pub generation: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ListCursor {
    generation: u64,
    after: String,
}

impl ListCursor {
    pub fn new(generation: u64, after: impl Into<String>) -> Self {
        Self {
            generation,
            after: after.into(),
        }
    }

    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub fn after(&self) -> &str {
        &self.after
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalListPage {
    pub generation: u64,
    pub entries: Vec<LogicalEntry>,
    pub next_cursor: Option<ListCursor>,
    pub truncated: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalWalkCursor {
    generation: u64,
    inner: WalkCursor,
}

impl LogicalWalkCursor {
    pub fn new(generation: u64, after: impl Into<String>) -> Self {
        Self {
            generation,
            inner: WalkCursor::after(after),
        }
    }

    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub fn after(&self) -> &str {
        self.inner.as_str()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalWalkOptions {
    pub page_size: usize,
    pub cursor: Option<LogicalWalkCursor>,
    pub include_directories: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalWalkEntry {
    pub path: LogicalPath,
    pub stable_id: String,
    pub revision: Option<u64>,
    pub content_hash: Option<String>,
    pub kind: EntryKind,
    pub object_kind: Option<ObjectKind>,
    pub depth: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalWalkPage {
    pub generation: u64,
    pub entries: Vec<LogicalWalkEntry>,
    pub errors: Vec<(LogicalPath, String)>,
    pub next_cursor: Option<LogicalWalkCursor>,
    pub truncated: bool,
    pub limit_reached: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalGlobCursor {
    generation: u64,
    inner: GlobCursor,
}

impl LogicalGlobCursor {
    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub fn after(&self) -> &str {
        self.inner.as_str()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalGlobPage {
    pub generation: u64,
    pub matches: Vec<LogicalEntry>,
    pub next_cursor: Option<LogicalGlobCursor>,
    pub truncated: bool,
    pub limit_reached: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalGrepCursor {
    generation: u64,
    inner: GrepCursor,
}

impl LogicalGrepCursor {
    pub const fn generation(&self) -> u64 {
        self.generation
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalGrepRequest {
    pub root: String,
    pub query: String,
    pub mode: GrepMode,
    pub cursor: Option<LogicalGrepCursor>,
    pub max_files: usize,
    pub max_matches: usize,
    pub max_line_bytes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalGrepMatch {
    pub path: LogicalPath,
    pub stable_id: String,
    pub revision: u64,
    pub content_hash: String,
    pub line_number: usize,
    pub byte_offset: u64,
    pub excerpt: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalGrepSkipped {
    pub path: LogicalPath,
    pub stable_id: String,
    pub revision: u64,
    pub content_hash: String,
    pub reason: SkipReason,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalGrepPage {
    pub generation: u64,
    pub matches: Vec<LogicalGrepMatch>,
    pub skipped: Vec<LogicalGrepSkipped>,
    pub next_cursor: Option<LogicalGrepCursor>,
    pub truncated: bool,
    pub limit_reached: bool,
}

#[derive(Debug)]
pub struct LogicalReadStream {
    generation: u64,
    path: LogicalPath,
    stable_id: String,
    revision: u64,
    content_hash: String,
    inner: ReadStream,
    failed: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalReadChunk {
    pub generation: u64,
    pub path: LogicalPath,
    pub stable_id: String,
    pub revision: u64,
    pub content_hash: String,
    pub offset: u64,
    pub bytes: Vec<u8>,
}

impl Iterator for LogicalReadStream {
    type Item = Result<LogicalReadChunk, LogicalError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.failed {
            return None;
        }
        match self.inner.next()? {
            Ok(chunk) => Some(Ok(LogicalReadChunk {
                generation: self.generation,
                path: self.path.clone(),
                stable_id: self.stable_id.clone(),
                revision: self.revision,
                content_hash: self.content_hash.clone(),
                offset: chunk.offset,
                bytes: chunk.bytes,
            })),
            Err(error) => {
                self.failed = true;
                Some(Err(LogicalError::FileTool(error)))
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeSearchState {
    Missing,
    Building { generation: u64 },
    Ready { generation: u64 },
    Degraded { generation: u64 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SearchNotReadyReason {
    Missing,
    Building,
    Degraded,
    GenerationMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SearchReadinessStatus {
    Ready,
    NotReady(SearchNotReadyReason),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SearchReadinessReport {
    pub catalog_generation: u64,
    pub index_generation: Option<u64>,
    pub status: SearchReadinessStatus,
}

impl CoreFsReadSnapshot {
    pub fn stat(&self, path: &str) -> Result<LogicalStat, LogicalError> {
        let node = self.parse_node(path)?;
        let (body_encoding, content_type, size) = match node.object.as_ref() {
            None => (None, None, 0),
            Some(_) => {
                let metadata = self.authenticated_metadata(node)?;
                (
                    Some(metadata.body_encoding),
                    Some(metadata.content_type),
                    metadata.body_length,
                )
            }
        };
        Ok(LogicalStat {
            path: node.path.clone(),
            stable_id: node.stable_id.clone(),
            revision: node.revision(),
            content_hash: node.content_hash().map(str::to_owned),
            kind: node.entry_kind(),
            object_kind: node.object_kind(),
            body_encoding,
            content_type,
            size,
            generation: self.generation(),
        })
    }

    pub fn list(
        &self,
        path: &str,
        cursor: Option<ListCursor>,
        limit: usize,
        limits: ValidatedLimits,
        control: OperationControl,
    ) -> Result<LogicalListPage, LogicalError> {
        control.check()?;
        ensure_cursor_generation(
            self.generation(),
            cursor.as_ref().map(|value| value.generation),
        )?;
        if limit == 0 || limit > limits.walk_entries() {
            return Err(LogicalError::InvalidLimit(format!(
                "list limit must be between 1 and {}",
                limits.walk_entries()
            )));
        }
        let directory = self.parse_node(path)?;
        if directory.object.is_some() {
            return Err(LogicalError::NotDirectory {
                path: directory.path.as_str().to_owned(),
            });
        }
        let listing = WalkBackend::read_directory(self, directory.path.as_str())?;
        let mut entries = Vec::new();
        let mut response_bytes = 0usize;
        let mut after_cursor = cursor.is_none();
        let mut truncated = false;
        for child in listing.entries {
            control.check()?;
            if !after_cursor {
                if cursor
                    .as_ref()
                    .is_some_and(|value| value.after == child.path.as_str())
                {
                    after_cursor = true;
                }
                continue;
            }
            let node = self
                .nodes
                .get(child.path.as_str())
                .expect("backend paths come from the selected catalog");
            let item_bytes = node
                .path
                .as_str()
                .len()
                .saturating_add(node.stable_id.len())
                .saturating_add(RESPONSE_ENTRY_OVERHEAD);
            if entries.len() == limit
                || response_bytes.saturating_add(item_bytes) > limits.response_bytes()
            {
                truncated = true;
                break;
            }
            response_bytes += item_bytes;
            entries.push(logical_entry(node));
        }
        let next_cursor = truncated
            .then(|| entries.last())
            .flatten()
            .map(|entry| ListCursor::new(self.generation(), entry.path.as_str()));
        Ok(LogicalListPage {
            generation: self.generation(),
            entries,
            next_cursor,
            truncated,
        })
    }

    pub fn walk(
        &self,
        root: &str,
        options: LogicalWalkOptions,
        limits: ValidatedLimits,
        control: OperationControl,
    ) -> Result<LogicalWalkPage, LogicalError> {
        let root = backend_path(root)?;
        ensure_cursor_generation(
            self.generation(),
            options.cursor.as_ref().map(|value| value.generation),
        )?;
        let page = walk_page(
            self,
            root,
            WalkOptions {
                page_size: options.page_size,
                cursor: options.cursor.map(|value| value.inner),
                include_directories: options.include_directories,
            },
            limits,
            control,
        )?;
        let entries = page
            .entries
            .into_iter()
            .map(|entry| {
                let node = &self.nodes[entry.path.as_str()];
                LogicalWalkEntry {
                    path: node.path.clone(),
                    stable_id: node.stable_id.clone(),
                    revision: node.revision(),
                    content_hash: node.content_hash().map(str::to_owned),
                    kind: entry.kind,
                    object_kind: node.object_kind(),
                    depth: entry.depth,
                }
            })
            .collect();
        let errors = page
            .errors
            .into_iter()
            .map(|error| {
                (
                    LogicalPath::parse(error.path.as_str())
                        .expect("backend returns canonical paths"),
                    error.message,
                )
            })
            .collect();
        Ok(LogicalWalkPage {
            generation: self.generation(),
            entries,
            errors,
            next_cursor: page.next_cursor.map(|inner| LogicalWalkCursor {
                generation: self.generation(),
                inner,
            }),
            truncated: page.truncated,
            limit_reached: page.limit_reached,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn glob(
        &self,
        root: &str,
        pattern: &str,
        cursor: Option<LogicalGlobCursor>,
        max_results: usize,
        limits: ValidatedLimits,
        control: OperationControl,
    ) -> Result<LogicalGlobPage, LogicalError> {
        ensure_cursor_generation(
            self.generation(),
            cursor.as_ref().map(|value| value.generation),
        )?;
        let page = glob(
            self,
            GlobRequest {
                root: backend_path(root)?,
                pattern: pattern.to_owned(),
                cursor: cursor.map(|value| value.inner),
                max_results,
            },
            limits,
            control,
        )?;
        Ok(LogicalGlobPage {
            generation: self.generation(),
            matches: page
                .matches
                .into_iter()
                .map(|path| logical_entry(&self.nodes[path.as_str()]))
                .collect(),
            next_cursor: page.next_cursor.map(|inner| LogicalGlobCursor {
                generation: self.generation(),
                inner,
            }),
            truncated: page.truncated,
            limit_reached: page.limit_reached,
        })
    }

    pub fn grep(
        &self,
        request: LogicalGrepRequest,
        limits: ValidatedLimits,
        control: OperationControl,
    ) -> Result<LogicalGrepPage, LogicalError> {
        ensure_cursor_generation(
            self.generation(),
            request.cursor.as_ref().map(|value| value.generation),
        )?;
        let page = grep(
            self,
            GrepRequest {
                root: backend_path(&request.root)?,
                query: request.query,
                mode: request.mode,
                cursor: request.cursor.map(|value| value.inner),
                max_files: request.max_files,
                max_matches: request.max_matches,
                max_line_bytes: request.max_line_bytes,
            },
            limits,
            control,
        )?;
        let matches = page
            .matches
            .into_iter()
            .map(|found| {
                let node = &self.nodes[found.path.as_str()];
                let object = node.object.as_ref().expect("grep returns files only");
                LogicalGrepMatch {
                    path: node.path.clone(),
                    stable_id: node.stable_id.clone(),
                    revision: object.revision,
                    content_hash: object.content_hash.clone(),
                    line_number: found.line_number,
                    byte_offset: found.byte_offset,
                    excerpt: found.excerpt,
                }
            })
            .collect();
        let skipped = page
            .skipped
            .into_iter()
            .map(|skipped| {
                let node = &self.nodes[skipped.path.as_str()];
                let object = node.object.as_ref().expect("grep skips files only");
                LogicalGrepSkipped {
                    path: node.path.clone(),
                    stable_id: node.stable_id.clone(),
                    revision: object.revision,
                    content_hash: object.content_hash.clone(),
                    reason: skipped.reason,
                }
            })
            .collect();
        Ok(LogicalGrepPage {
            generation: self.generation(),
            matches,
            skipped,
            next_cursor: page.next_cursor.map(|inner| LogicalGrepCursor {
                generation: self.generation(),
                inner,
            }),
            truncated: page.truncated,
            limit_reached: page.limit_reached,
        })
    }

    pub fn read(
        &self,
        path: &str,
        options: ReadOptions,
        limits: ValidatedLimits,
        control: OperationControl,
    ) -> Result<LogicalReadStream, LogicalError> {
        let node = self.parse_node(path)?;
        let object = node.object.as_ref().ok_or_else(|| LogicalError::NotFile {
            path: node.path.as_str().to_owned(),
        })?;
        let inner = read_stream(
            self,
            backend_path(node.path.as_str())?,
            options,
            limits,
            control,
        )?;
        Ok(LogicalReadStream {
            generation: self.generation(),
            path: node.path.clone(),
            stable_id: node.stable_id.clone(),
            revision: object.revision,
            content_hash: object.content_hash.clone(),
            inner,
            failed: false,
        })
    }

    pub fn search_readiness(&self, state: RuntimeSearchState) -> SearchReadinessReport {
        let (index_generation, status) = match state {
            RuntimeSearchState::Missing => (
                None,
                SearchReadinessStatus::NotReady(SearchNotReadyReason::Missing),
            ),
            RuntimeSearchState::Building { generation } => (
                Some(generation),
                SearchReadinessStatus::NotReady(if generation == self.generation() {
                    SearchNotReadyReason::Building
                } else {
                    SearchNotReadyReason::GenerationMismatch
                }),
            ),
            RuntimeSearchState::Degraded { generation } => (
                Some(generation),
                SearchReadinessStatus::NotReady(if generation == self.generation() {
                    SearchNotReadyReason::Degraded
                } else {
                    SearchNotReadyReason::GenerationMismatch
                }),
            ),
            RuntimeSearchState::Ready { generation } if generation == self.generation() => {
                (Some(generation), SearchReadinessStatus::Ready)
            }
            RuntimeSearchState::Ready { generation } => (
                Some(generation),
                SearchReadinessStatus::NotReady(SearchNotReadyReason::GenerationMismatch),
            ),
        };
        SearchReadinessReport {
            catalog_generation: self.generation(),
            index_generation,
            status,
        }
    }
}

fn logical_entry(node: &Node) -> LogicalEntry {
    LogicalEntry {
        path: node.path.clone(),
        stable_id: node.stable_id.clone(),
        revision: node.revision(),
        content_hash: node.content_hash().map(str::to_owned),
        kind: node.entry_kind(),
        object_kind: node.object_kind(),
    }
}

fn backend_path(path: &str) -> Result<BackendPath, LogicalError> {
    let path = LogicalPath::parse(path)?;
    Ok(BackendPath::new(
        BackendKind::CoreFs,
        path.as_str().to_owned(),
    )?)
}

fn ensure_cursor_generation(selected: u64, cursor: Option<u64>) -> Result<(), LogicalError> {
    if let Some(cursor) = cursor {
        if cursor != selected {
            return Err(LogicalError::CursorGeneration { cursor, selected });
        }
    }
    Ok(())
}
