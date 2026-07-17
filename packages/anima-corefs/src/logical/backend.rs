use std::collections::{BTreeMap, HashMap};
use std::ffi::OsStr;
use std::fs::File;
use std::io::{self, Cursor, Read, Seek, SeekFrom};
use std::sync::Arc;

use anima_file_tools::{
    BackendCapabilities, BackendKind, BackendPath, DirectoryEntry, DirectoryListing, EntryKind,
    EntryMetadata, FileBackend, FileToolError, MutationAtomicity, OperationControl, PathSemantics,
    ReadBackend, ReadSeek, WalkBackend,
};
use cap_std::fs::Dir;

use crate::catalog::{CatalogGeneration, CatalogGenerationEntry, ObjectLifecycle};
use crate::crypto::{
    unwrap_object_dek, ObjectBaseAad, ObjectKeyAad, ObjectKind, SecretBytes,
    OBJECT_KEY_ENVELOPE_VERSION,
};
use crate::envelope::{
    open_envelope_stream, read_envelope_seekable_range, AuthenticatedEnvelopeStream, BodyEncoding,
    EnvelopeMetadata, ENVELOPE_VERSION,
};
use crate::rotation::FrkKeyring;
use crate::transaction::{open_regular_file_in, CoreCommitCoordinator, ValidationSnapshot};

use super::{LogicalPath, LogicalPathError};

#[derive(Debug, thiserror::Error)]
pub enum LogicalError {
    #[error(transparent)]
    InvalidPath(#[from] LogicalPathError),
    #[error(transparent)]
    FileTool(#[from] FileToolError),
    #[error("logical path was not found: {path}")]
    NotFound { path: String },
    #[error("logical path is not a file: {path}")]
    NotFile { path: String },
    #[error("logical path is not a directory: {path}")]
    NotDirectory { path: String },
    #[error("cursor generation {cursor} does not match selected catalog generation {selected}")]
    CursorGeneration { cursor: u64, selected: u64 },
    #[error("selected catalog cannot authorize logical object {stable_id}")]
    ObjectAuthorization { stable_id: String },
    #[error("selected catalog contains an invalid logical path for {stable_id}")]
    InvalidCatalogPath { stable_id: String },
    #[error("CoreFS object storage is unavailable")]
    StorageUnavailable,
    #[error("invalid operation limit: {0}")]
    InvalidLimit(String),
    #[error("logical model wire encoding failed")]
    ModelEncoding,
}

#[derive(Clone)]
pub(super) struct ObjectNode {
    pub revision: u64,
    pub kind: ObjectKind,
    pub physical_name: String,
    pub content_hash: String,
    pub object_key_epoch: u32,
    pub key: Arc<SecretBytes>,
}

pub(super) struct Node {
    pub path: LogicalPath,
    pub stable_id: String,
    pub object: Option<ObjectNode>,
}

impl Node {
    pub fn entry_kind(&self) -> EntryKind {
        if self.object.is_some() {
            EntryKind::File
        } else {
            EntryKind::Directory
        }
    }

    pub fn revision(&self) -> Option<u64> {
        self.object.as_ref().map(|object| object.revision)
    }

    pub fn object_kind(&self) -> Option<ObjectKind> {
        self.object.as_ref().map(|object| object.kind)
    }

    pub fn content_hash(&self) -> Option<&str> {
        self.object
            .as_ref()
            .map(|object| object.content_hash.as_str())
    }
}

/// Read-only CoreFS backend pinned to one already-authenticated validation
/// catalog generation. Construction unwraps only that catalog's live object
/// wrappers and never consults `fs/HEAD` afterward.
pub struct CoreFsReadSnapshot {
    core_id: String,
    generation: u64,
    objects_dir: Dir,
    pub(super) nodes: BTreeMap<String, Node>,
    children: BTreeMap<String, Vec<String>>,
}

impl std::fmt::Debug for CoreFsReadSnapshot {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CoreFsReadSnapshot")
            .field("generation", &self.generation)
            .field("entries", &self.nodes.len())
            .finish_non_exhaustive()
    }
}

impl CoreFsReadSnapshot {
    pub fn open(
        coordinator: &CoreCommitCoordinator,
        selected: &ValidationSnapshot,
        keyring: &FrkKeyring<'_>,
    ) -> Result<Self, LogicalError> {
        let catalog = selected.catalog();
        if selected.head().generation() != catalog.generation() {
            return Err(LogicalError::StorageUnavailable);
        }
        let objects_dir = coordinator
            .clone_objects_dir()
            .map_err(|_| LogicalError::StorageUnavailable)?;
        let paths = catalog_paths(catalog)?;
        let mut nodes = BTreeMap::new();

        for entry in catalog.entries() {
            let path = paths
                .get(entry.stable_id().as_str())
                .expect("every validated catalog entry has a path")
                .clone();
            let object = match entry.object_payload() {
                None => None,
                Some(object) if object.lifecycle() != &ObjectLifecycle::Live => continue,
                Some(object) => {
                    let record = object.wrapped_dek();
                    let keys = keyring.require(record.frk_version()).map_err(|_| {
                        LogicalError::ObjectAuthorization {
                            stable_id: entry.stable_id().as_str().to_owned(),
                        }
                    })?;
                    let base_aad = ObjectBaseAad::new(
                        coordinator.core_id(),
                        entry.stable_id().as_str(),
                        object.kind(),
                        OBJECT_KEY_ENVELOPE_VERSION,
                        object.object_key_epoch(),
                        object.revision(),
                    )
                    .map_err(|_| LogicalError::ObjectAuthorization {
                        stable_id: entry.stable_id().as_str().to_owned(),
                    })?;
                    let key_aad =
                        ObjectKeyAad::from_base(base_aad, record.frk_version()).map_err(|_| {
                            LogicalError::ObjectAuthorization {
                                stable_id: entry.stable_id().as_str().to_owned(),
                            }
                        })?;
                    let wrapped = record.to_wrapped_object_dek().map_err(|_| {
                        LogicalError::ObjectAuthorization {
                            stable_id: entry.stable_id().as_str().to_owned(),
                        }
                    })?;
                    let key = unwrap_object_dek(keys, &wrapped, &key_aad).map_err(|_| {
                        LogicalError::ObjectAuthorization {
                            stable_id: entry.stable_id().as_str().to_owned(),
                        }
                    })?;
                    Some(ObjectNode {
                        revision: object.revision(),
                        kind: object.kind(),
                        physical_name: object.physical_name().as_str().to_owned(),
                        content_hash: object.content_hash().as_str().to_owned(),
                        object_key_epoch: object.object_key_epoch(),
                        key: Arc::new(key),
                    })
                }
            };
            nodes.insert(
                path.as_str().to_owned(),
                Node {
                    path,
                    stable_id: entry.stable_id().as_str().to_owned(),
                    object,
                },
            );
        }

        let mut children = BTreeMap::<String, Vec<String>>::new();
        for node in nodes.values() {
            if node.path.as_str().is_empty() {
                continue;
            }
            let parent = node
                .path
                .as_str()
                .rsplit_once('/')
                .map_or("", |(parent, _)| parent);
            if nodes
                .get(parent)
                .is_some_and(|entry| entry.object.is_none())
            {
                children
                    .entry(parent.to_owned())
                    .or_default()
                    .push(node.path.as_str().to_owned());
            }
        }
        for values in children.values_mut() {
            values.sort();
        }

        Ok(Self {
            core_id: coordinator.core_id().to_owned(),
            generation: catalog.generation(),
            objects_dir,
            nodes,
            children,
        })
    }

    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub(super) fn parse_node(&self, path: &str) -> Result<&Node, LogicalError> {
        let path = LogicalPath::parse(path)?;
        self.nodes
            .get(path.as_str())
            .ok_or_else(|| LogicalError::NotFound {
                path: path.as_str().to_owned(),
            })
    }

    pub(super) fn authenticated_metadata(
        &self,
        node: &Node,
    ) -> Result<EnvelopeMetadata, LogicalError> {
        let object = node.object.as_ref().ok_or_else(|| LogicalError::NotFile {
            path: node.path.as_str().to_owned(),
        })?;
        open_object_stream(&self.objects_dir, &self.core_id, node, object)
            .map(|stream| stream.metadata().clone())
            .map_err(LogicalError::FileTool)
    }

    fn backend_node(&self, path: &str) -> Result<&Node, FileToolError> {
        let canonical = LogicalPath::parse(path).map_err(|error| invalid_path(path, error))?;
        self.nodes
            .get(canonical.as_str())
            .ok_or_else(|| backend_error("lookup", canonical.as_str(), "logical path not found"))
    }
}

impl FileBackend for CoreFsReadSnapshot {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::CoreFs,
            PathSemantics::PortableNfcCaseSensitive,
            MutationAtomicity::CatalogGeneration,
        )
    }
}

impl ReadBackend for CoreFsReadSnapshot {
    fn open_read(&self, path: &str) -> Result<Box<dyn ReadSeek + Send>, FileToolError> {
        self.open_object_reader(path)
            .map(|reader| Box::new(reader) as Box<dyn ReadSeek + Send>)
    }

    fn open_read_at(
        &self,
        path: &str,
        offset: u64,
        max_bytes: usize,
        control: &OperationControl,
    ) -> Result<Box<dyn ReadSeek + Send>, FileToolError> {
        control.check()?;
        let node = self.backend_node(path)?;
        let object = node
            .object
            .as_ref()
            .ok_or_else(|| backend_error("open_read", path, "logical path is not a file"))?;
        let metadata = open_object_stream(&self.objects_dir, &self.core_id, node, object)?
            .metadata()
            .clone();
        if offset >= metadata.body_length || max_bytes == 0 {
            return Ok(Box::new(Cursor::new(Vec::new())));
        }

        let mut file = open_regular_file_in(&self.objects_dir, OsStr::new(&object.physical_name))
            .map_err(|_| {
            backend_error(
                "open_read",
                node.path.as_str(),
                "authorized object revision is unavailable",
            )
        })?;
        let aad = ObjectBaseAad::new(
            &self.core_id,
            &node.stable_id,
            object.kind,
            ENVELOPE_VERSION,
            object.object_key_epoch,
            object.revision,
        )
        .map_err(|_| backend_error("open_read", node.path.as_str(), "invalid object authority"))?;
        let mut bytes = Vec::new();
        let result = read_envelope_seekable_range(
            &mut file,
            object.key.as_ref(),
            &aad,
            offset,
            max_bytes,
            &mut bytes,
            || {
                control
                    .check()
                    .map_err(|error| io::Error::new(io::ErrorKind::Interrupted, error.to_string()))
            },
        );
        if let Err(error) = result {
            control.check()?;
            return Err(backend_error(
                "open_read",
                node.path.as_str(),
                &format!("object range authentication failed: {error}"),
            ));
        }
        Ok(Box::new(Cursor::new(bytes)))
    }
}

impl CoreFsReadSnapshot {
    fn open_object_reader(&self, path: &str) -> Result<CoreFsObjectReader, FileToolError> {
        let node = self.backend_node(path)?;
        let object = node
            .object
            .as_ref()
            .ok_or_else(|| backend_error("open_read", path, "logical path is not a file"))?;
        let objects_dir = self
            .objects_dir
            .try_clone()
            .map_err(|_| backend_error("open_read", path, "object storage is unavailable"))?;
        CoreFsObjectReader::open(objects_dir, self.core_id.clone(), node, object)
    }
}

impl WalkBackend for CoreFsReadSnapshot {
    fn metadata(&self, path: &str) -> Result<EntryMetadata, FileToolError> {
        let node = self.backend_node(path)?;
        let Some(object) = node.object.as_ref() else {
            return Ok(EntryMetadata::directory(false));
        };
        let stream = open_object_stream(&self.objects_dir, &self.core_id, node, object)?;
        let metadata = stream.metadata();
        Ok(match metadata.body_encoding {
            BodyEncoding::Utf8 => EntryMetadata::text_file(metadata.body_length),
            BodyEncoding::Binary => EntryMetadata::binary_file(metadata.body_length),
        })
    }

    fn read_directory(&self, path: &str) -> Result<DirectoryListing, FileToolError> {
        let node = self.backend_node(path)?;
        if node.object.is_some() {
            return Err(backend_error(
                "read_directory",
                path,
                "logical path is not a directory",
            ));
        }
        let entries = self
            .children
            .get(node.path.as_str())
            .into_iter()
            .flatten()
            .map(|child| {
                let node = &self.nodes[child];
                let metadata = if node.object.is_some() {
                    EntryMetadata::file(0)
                } else {
                    EntryMetadata::directory(false)
                };
                BackendPath::new(BackendKind::CoreFs, child.clone())
                    .map(|path| DirectoryEntry::new(path, metadata))
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(entries.into())
    }
}

fn catalog_paths(
    catalog: &CatalogGeneration,
) -> Result<HashMap<String, LogicalPath>, LogicalError> {
    let by_id: HashMap<_, _> = catalog
        .entries()
        .iter()
        .map(|entry| (entry.stable_id().as_str(), entry))
        .collect();
    let mut paths = HashMap::new();
    for entry in catalog.entries() {
        resolve_catalog_path(entry, &by_id, &mut paths)?;
    }
    Ok(paths)
}

fn resolve_catalog_path(
    entry: &CatalogGenerationEntry,
    by_id: &HashMap<&str, &CatalogGenerationEntry>,
    paths: &mut HashMap<String, LogicalPath>,
) -> Result<LogicalPath, LogicalError> {
    if let Some(path) = paths.get(entry.stable_id().as_str()) {
        return Ok(path.clone());
    }
    let path = match entry.parent_id() {
        None => LogicalPath::parse("").expect("empty logical root is valid"),
        Some(parent_id) => {
            let parent = by_id
                .get(parent_id.as_str())
                .expect("validated catalog parent exists");
            resolve_catalog_path(parent, by_id, paths)?
                .join_component(entry.name().as_str())
                .map_err(|_| LogicalError::InvalidCatalogPath {
                    stable_id: entry.stable_id().as_str().to_owned(),
                })?
        }
    };
    paths.insert(entry.stable_id().as_str().to_owned(), path.clone());
    Ok(path)
}

fn open_object_stream(
    objects_dir: &Dir,
    core_id: &str,
    node: &Node,
    object: &ObjectNode,
) -> Result<AuthenticatedEnvelopeStream<File>, FileToolError> {
    let file =
        open_regular_file_in(objects_dir, OsStr::new(&object.physical_name)).map_err(|_| {
            backend_error(
                "open_read",
                node.path.as_str(),
                "authorized object revision is unavailable",
            )
        })?;
    let aad = ObjectBaseAad::new(
        core_id,
        &node.stable_id,
        object.kind,
        ENVELOPE_VERSION,
        object.object_key_epoch,
        object.revision,
    )
    .map_err(|_| backend_error("open_read", node.path.as_str(), "invalid object authority"))?;
    let stream = open_envelope_stream(file, object.key.as_ref(), aad).map_err(|error| {
        backend_error(
            "open_read",
            node.path.as_str(),
            &format!("object authentication failed: {error}"),
        )
    })?;
    if stream.metadata().body_sha256 != object.content_hash {
        return Err(backend_error(
            "open_read",
            node.path.as_str(),
            "catalog content hash does not match authenticated object metadata",
        ));
    }
    Ok(stream)
}

struct CoreFsObjectReader {
    objects_dir: Dir,
    core_id: String,
    node_path: LogicalPath,
    stable_id: String,
    object: ObjectNode,
    stream: AuthenticatedEnvelopeStream<File>,
    body_length: u64,
    position: u64,
}

impl CoreFsObjectReader {
    fn open(
        objects_dir: Dir,
        core_id: String,
        node: &Node,
        object: &ObjectNode,
    ) -> Result<Self, FileToolError> {
        let stream = open_object_stream(&objects_dir, &core_id, node, object)?;
        let body_length = stream.metadata().body_length;
        Ok(Self {
            objects_dir,
            core_id,
            node_path: node.path.clone(),
            stable_id: node.stable_id.clone(),
            object: object.clone(),
            stream,
            body_length,
            position: 0,
        })
    }

    fn reset(&mut self) -> io::Result<()> {
        let node = Node {
            path: self.node_path.clone(),
            stable_id: self.stable_id.clone(),
            object: Some(self.object.clone()),
        };
        self.stream = open_object_stream(&self.objects_dir, &self.core_id, &node, &self.object)
            .map_err(file_tool_to_io)?;
        self.position = 0;
        Ok(())
    }

    fn seek_to(&mut self, target: u64) -> io::Result<u64> {
        self.seek_to_controlled(target, &OperationControl::default())
            .map_err(file_tool_to_io)
    }

    fn seek_to_controlled(
        &mut self,
        target: u64,
        control: &OperationControl,
    ) -> Result<u64, FileToolError> {
        control.check()?;
        let readable_target = target.min(self.body_length);
        if readable_target < self.position {
            self.reset().map_err(|error| {
                backend_error("seek", self.node_path.as_str(), &error.to_string())
            })?;
        }
        let remaining = readable_target.saturating_sub(self.position);
        let discarded = discard_authenticated_with_control(
            &mut self.stream,
            remaining,
            control,
            self.node_path.as_str(),
        )?;
        self.position += discarded;
        self.position = target;
        Ok(target)
    }
}

fn discard_authenticated_with_control<R: Read>(
    reader: &mut R,
    mut remaining: u64,
    control: &OperationControl,
    logical_path: &str,
) -> Result<u64, FileToolError> {
    let mut discarded = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    while remaining > 0 {
        control.check()?;
        let requested = remaining.min(buffer.len() as u64) as usize;
        let read = reader.read(&mut buffer[..requested]).map_err(|error| {
            backend_error(
                "seek",
                logical_path,
                &format!("authenticated read failed: {error}"),
            )
        })?;
        if read == 0 {
            return Err(backend_error(
                "seek",
                logical_path,
                "authenticated body ended before its declared length",
            ));
        }
        discarded += read as u64;
        remaining -= read as u64;
    }
    control.check()?;
    Ok(discarded)
}

impl Read for CoreFsObjectReader {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        if self.position >= self.body_length {
            return Ok(0);
        }
        let requested = (self.body_length - self.position).min(output.len() as u64) as usize;
        let read = self.stream.read(&mut output[..requested])?;
        self.position += read as u64;
        Ok(read)
    }
}

impl Seek for CoreFsObjectReader {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        let target = match position {
            SeekFrom::Start(target) => i128::from(target),
            SeekFrom::End(delta) => i128::from(self.body_length) + i128::from(delta),
            SeekFrom::Current(delta) => i128::from(self.position) + i128::from(delta),
        };
        if !(0..=i128::from(u64::MAX)).contains(&target) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "logical seek is outside the supported range",
            ));
        }
        self.seek_to(target as u64)
    }
}

fn invalid_path(path: &str, error: LogicalPathError) -> FileToolError {
    FileToolError::InvalidPath {
        path: path.to_owned(),
        reason: error.to_string(),
    }
}

fn backend_error(operation: &'static str, path: &str, message: &str) -> FileToolError {
    FileToolError::Backend {
        operation,
        path: path.to_owned(),
        message: message.to_owned(),
    }
}

fn file_tool_to_io(error: FileToolError) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error)
}

#[cfg(test)]
mod tests {
    use std::io::{self, Read};

    use anima_file_tools::{CancellationToken, FileToolError, OperationControl};

    use super::discard_authenticated_with_control;

    struct CancellingReader {
        cancellation: CancellationToken,
        reads: usize,
    }

    impl Read for CancellingReader {
        fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
            self.reads += 1;
            output.fill(0);
            self.cancellation.cancel();
            Ok(output.len())
        }
    }

    #[test]
    fn corefs_positioning_checks_control_between_authenticated_discards() {
        let cancellation = CancellationToken::new();
        let control = OperationControl::new(cancellation.clone(), None);
        let mut reader = CancellingReader {
            cancellation,
            reads: 0,
        };

        let error = discard_authenticated_with_control(
            &mut reader,
            128 * 1024,
            &control,
            "Notes/large.bin",
        )
        .unwrap_err();

        assert_eq!(error, FileToolError::Cancelled);
        assert_eq!(reader.reads, 1);
    }
}
