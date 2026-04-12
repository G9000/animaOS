#![cfg_attr(not(test), allow(dead_code))]

use std::ffi::OsStr;
use std::fs;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use fs4::FileExt;
use serde::{Deserialize, Serialize};

use crate::cards::{CardStore, SchemaRegistry};
use crate::engine::AnimaEngine;
use crate::frame::FrameStore;
use crate::graph::KnowledgeGraph;

const METADATA_FILE_NAME: &str = "metadata.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EngineOpenMode {
    ReadOnly,
    ReadWrite,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct CommittedSnapshot {
    pub generation: u64,
    pub frames_file: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cards_file: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub graph_file: Option<String>,
}

pub(crate) fn frames_file_name(generation: u64) -> String {
    format!("generation-{}.frames.json", generation)
}

pub(crate) fn cards_file_name(generation: u64) -> String {
    format!("generation-{}.cards.json", generation)
}

pub(crate) fn graph_file_name(generation: u64) -> String {
    format!("generation-{}.graph.json", generation)
}

fn metadata_path(root: &Path) -> PathBuf {
    root.join(METADATA_FILE_NAME)
}

fn metadata_temp_path(root: &Path) -> PathBuf {
    root.join(format!("{METADATA_FILE_NAME}.tmp"))
}

fn unique_metadata_temp_path(root: &Path, nonce: u128, attempt: u32) -> PathBuf {
    root.join(format!(
        "{METADATA_FILE_NAME}.tmp.{}.{}.{}",
        std::process::id(),
        nonce,
        attempt
    ))
}

fn canonicalize_existing_path(path: &Path, context: &str) -> crate::Result<PathBuf> {
    fs::canonicalize(path).map_err(|error| io_error(context, error))
}

fn canonical_root(root: &Path) -> crate::Result<PathBuf> {
    canonicalize_existing_path(root, "canonicalize engine root")
}

fn validate_engine_root_exists_and_is_dir(root: &Path) -> crate::Result<()> {
    if !root.exists() {
        return Err(storage_error("engine root path is missing"));
    }
    if !root.is_dir() {
        return Err(storage_error("engine root path must be a directory"));
    }

    Ok(())
}

fn resolve_existing_file_within_root(
    root: &Path,
    path: &Path,
    missing_error: impl Into<String>,
    context: &str,
) -> crate::Result<PathBuf> {
    if !path.exists() {
        return Err(storage_error(missing_error));
    }

    let canonical = canonicalize_existing_path(path, context)?;
    if !canonical.starts_with(root) {
        return Err(storage_error(format!(
            "committed path escapes engine root: {}",
            path.display()
        )));
    }
    if !canonical.is_file() {
        return Err(storage_error(format!(
            "committed path is missing or not a file: {}",
            path.display()
        )));
    }

    Ok(canonical)
}

fn storage_error(message: impl Into<String>) -> crate::Error {
    crate::Error::Storage(message.into())
}

fn io_error(context: &str, error: std::io::Error) -> crate::Error {
    crate::Error::Io(format!("{context}: {error}"))
}

fn serialization_error(error: impl std::fmt::Display) -> crate::Error {
    crate::Error::Serialization(error.to_string())
}

fn is_lock_contention_error(error: &std::io::Error) -> bool {
    matches!(error.kind(), std::io::ErrorKind::WouldBlock) || error.raw_os_error() == Some(33)
}

fn validate_lock_path(root: &Path, lock_path: &Path) -> crate::Result<()> {
    if let Ok(metadata) = fs::symlink_metadata(lock_path) {
        if metadata.file_type().is_symlink() {
            return Err(storage_error(format!(
                "lock file must not be a symlink: {}",
                lock_path.display()
            )));
        }

        let canonical_lock = canonicalize_existing_path(lock_path, "canonicalize lock file")?;
        if !canonical_lock.starts_with(root) {
            return Err(storage_error(format!(
                "lock file escapes engine root: {}",
                lock_path.display()
            )));
        }
    }

    Ok(())
}

fn validate_engine_local_filename(name: &str) -> crate::Result<()> {
    let mut components = Path::new(name).components();
    match (components.next(), components.next()) {
        (Some(Component::Normal(component)), None) if component == OsStr::new(name) => Ok(()),
        _ => Err(storage_error(format!(
            "committed filename must be engine-local: {name}"
        ))),
    }
}

fn committed_file_path(root: &Path, name: &str) -> crate::Result<PathBuf> {
    validate_engine_local_filename(name)?;
    Ok(root.join(name))
}

fn validate_committed_generation(snapshot: &CommittedSnapshot) -> crate::Result<()> {
    let expected_frames = frames_file_name(snapshot.generation);
    let frames_name = Path::new(&snapshot.frames_file)
        .file_name()
        .and_then(OsStr::to_str)
        .unwrap_or(&snapshot.frames_file);
    if frames_name != expected_frames {
        return Err(storage_error(format!(
            "frames_file does not match generation {}",
            snapshot.generation
        )));
    }

    if let Some(cards_file) = snapshot.cards_file.as_ref() {
        let expected_cards = cards_file_name(snapshot.generation);
        let cards_name = Path::new(cards_file)
            .file_name()
            .and_then(OsStr::to_str)
            .unwrap_or(cards_file);
        if cards_name != expected_cards {
            return Err(storage_error(format!(
                "cards_file does not match generation {}",
                snapshot.generation
            )));
        }
    }

    if let Some(graph_file) = snapshot.graph_file.as_ref() {
        let expected_graph = graph_file_name(snapshot.generation);
        let graph_name = Path::new(graph_file)
            .file_name()
            .and_then(OsStr::to_str)
            .unwrap_or(graph_file);
        if graph_name != expected_graph {
            return Err(storage_error(format!(
                "graph_file does not match generation {}",
                snapshot.generation
            )));
        }
    }

    Ok(())
}

fn validate_optional_committed_file(
    root: &Path,
    name: Option<&str>,
) -> crate::Result<Option<PathBuf>> {
    match name {
        Some(name) => committed_file_path(root, name).map(Some),
        None => Ok(None),
    }
}

fn replace_metadata_file(source: &Path, destination: &Path) -> crate::Result<()> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;

        unsafe extern "system" {
            fn MoveFileExW(
                lpExistingFileName: *const u16,
                lpNewFileName: *const u16,
                dwFlags: u32,
            ) -> i32;
        }

        const MOVEFILE_REPLACE_EXISTING: u32 = 0x00000001;
        const MOVEFILE_WRITE_THROUGH: u32 = 0x00000008;

        let destination_wide = destination
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<u16>>();
        let source_wide = source
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<u16>>();

        let replaced = unsafe {
            MoveFileExW(
                source_wide.as_ptr(),
                destination_wide.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        };
        if replaced == 0 {
            return Err(io_error(
                "replace metadata.json",
                std::io::Error::last_os_error(),
            ));
        }

        Ok(())
    }

    #[cfg(not(windows))]
    {
        fs::rename(source, destination).map_err(|error| io_error("replace metadata.json", error))
    }
}

fn cleanup_staged_temp_file(path: &Path) {
    let _ = fs::remove_file(path);
}

fn write_file_and_sync(path: &Path, bytes: &[u8]) -> crate::Result<()> {
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(path)
        .map_err(|error| io_error("open committed file", error))?;
    file.write_all(bytes)
        .map_err(|error| io_error("write committed file", error))?;
    file.sync_all()
        .map_err(|error| io_error("sync committed file", error))?;
    Ok(())
}

fn sync_root_directory(root: &Path, context: &str) -> crate::Result<()> {
    #[cfg(unix)]
    {
        let dir = fs::File::open(root).map_err(|error| io_error(context, error))?;
        dir.sync_all().map_err(|error| io_error(context, error))?;
    }

    #[cfg(not(unix))]
    {
        let _ = (root, context);
    }

    Ok(())
}

fn create_unique_metadata_temp_file(root: &Path) -> crate::Result<(fs::File, PathBuf)> {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);

    for attempt in 0..32 {
        let temp_path = unique_metadata_temp_path(root, nonce, attempt);
        match OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
        {
            Ok(file) => return Ok((file, temp_path)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(io_error("open temp metadata file", error)),
        }
    }

    Err(storage_error(
        "could not allocate a unique temp metadata file in engine root",
    ))
}

#[derive(Debug)]
enum MetadataPublishOutcome {
    Durable,
    PublishedWithPostPublishSyncFailure(crate::Error),
}

fn published_post_sync_error(error: crate::Error) -> crate::Error {
    match error {
        crate::Error::Frame(message) => crate::Error::Frame(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Card(message) => crate::Error::Card(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Graph(message) => crate::Error::Graph(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Search(message) => crate::Error::Search(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Capsule(message) => crate::Error::Capsule(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Serialization(message) => crate::Error::Serialization(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Encryption(message) => crate::Error::Encryption(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Storage(message) => crate::Error::Storage(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::LockConflict(message) => crate::Error::LockConflict(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Io(message) => crate::Error::Io(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
        crate::Error::Other(message) => crate::Error::Other(format!(
            "snapshot was published but post-publish durability sync failed: {message}"
        )),
    }
}

fn write_snapshot_metadata_with_replace_and_post_publish<F, P>(
    root: &Path,
    snapshot: &CommittedSnapshot,
    replace: F,
    post_publish: P,
) -> crate::Result<MetadataPublishOutcome>
where
    F: FnOnce(&Path, &Path) -> crate::Result<()>,
    P: FnOnce(&Path) -> crate::Result<()>,
{
    let metadata_bytes = serde_json::to_vec(snapshot).map_err(serialization_error)?;
    let metadata_path = metadata_path(root);
    let (mut temp_file, temp_path) = create_unique_metadata_temp_file(root)?;
    if let Err(error) = temp_file.write_all(&metadata_bytes) {
        drop(temp_file);
        cleanup_staged_temp_file(&temp_path);
        return Err(io_error("write temp metadata file", error));
    }
    if let Err(error) = temp_file.sync_all() {
        drop(temp_file);
        cleanup_staged_temp_file(&temp_path);
        return Err(io_error("sync temp metadata file", error));
    }
    drop(temp_file);

    if let Err(error) = replace(&temp_path, &metadata_path) {
        cleanup_staged_temp_file(&temp_path);
        return Err(error);
    }

    match post_publish(root) {
        Ok(()) => Ok(MetadataPublishOutcome::Durable),
        Err(error) => Ok(MetadataPublishOutcome::PublishedWithPostPublishSyncFailure(
            published_post_sync_error(error),
        )),
    }
}

fn write_snapshot_metadata_with_replace<F>(
    root: &Path,
    snapshot: &CommittedSnapshot,
    replace: F,
) -> crate::Result<MetadataPublishOutcome>
where
    F: FnOnce(&Path, &Path) -> crate::Result<()>,
{
    write_snapshot_metadata_with_replace_and_post_publish(root, snapshot, replace, |root| {
        sync_root_directory(root, "sync engine root after metadata commit")
    })
}

fn write_snapshot_metadata(
    root: &Path,
    snapshot: &CommittedSnapshot,
) -> crate::Result<MetadataPublishOutcome> {
    write_snapshot_metadata_with_replace(root, snapshot, replace_metadata_file)
}

fn initialize_empty_engine_dir_with_metadata_writer<F>(
    root: &Path,
    snapshot: &CommittedSnapshot,
    metadata_writer: F,
) -> crate::Result<()>
where
    F: FnOnce(&Path, &CommittedSnapshot) -> crate::Result<MetadataPublishOutcome>,
{
    let frames_path = committed_file_path(root, &snapshot.frames_file)?;
    let frames_bytes = FrameStore::new().serialize()?;
    write_file_and_sync(&frames_path, &frames_bytes)?;
    sync_root_directory(root, "sync engine root before metadata commit")?;

    match metadata_writer(root, snapshot) {
        Ok(MetadataPublishOutcome::Durable) => Ok(()),
        Ok(MetadataPublishOutcome::PublishedWithPostPublishSyncFailure(error)) => Err(error),
        Err(error) => {
            let _ = fs::remove_file(&frames_path);
            let _ = fs::remove_file(metadata_path(root));
            Err(error)
        }
    }
}

#[allow(dead_code)]
#[derive(Debug)]
pub struct ReadOnlyPathEngineHandle {
    engine: AnimaEngine,
    root: PathBuf,
    generation: u64,
}

#[allow(dead_code)]
#[derive(Debug)]
pub struct ReadWritePathEngineHandle {
    engine: AnimaEngine,
    root: PathBuf,
    generation: u64,
    lock: WriterLockGuard,
}

#[allow(dead_code)]
#[derive(Debug)]
pub enum EnginePathHandle {
    ReadOnly(ReadOnlyPathEngineHandle),
    ReadWrite(ReadWritePathEngineHandle),
}

#[derive(Debug)]
struct WriterLockGuard {
    file: fs::File,
}

impl WriterLockGuard {
    fn acquire(root: &Path) -> crate::Result<Self> {
        let lock_path = root.join(".lock");
        let canonical_root = canonical_root(root)?;
        validate_lock_path(&canonical_root, &lock_path)?;
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&lock_path)
            .map_err(|error| io_error("open lock file", error))?;
        file.try_lock_exclusive().map_err(|error| {
            if is_lock_contention_error(&error) {
                crate::Error::LockConflict(format!(
                    "writer lock is already held for {}",
                    root.display()
                ))
            } else {
                io_error("lock engine root", error)
            }
        })?;
        Ok(Self { file })
    }
}

impl Drop for WriterLockGuard {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

impl ReadOnlyPathEngineHandle {
    pub fn engine(&self) -> &AnimaEngine {
        &self.engine
    }

    pub fn close(self) {}
}

impl ReadWritePathEngineHandle {
    pub fn engine(&self) -> &AnimaEngine {
        &self.engine
    }

    pub fn engine_mut(&mut self) -> &mut AnimaEngine {
        &mut self.engine
    }

    fn flush_with_metadata_writer<F>(&mut self, metadata_writer: F) -> crate::Result<()>
    where
        F: FnOnce(&Path, &CommittedSnapshot) -> crate::Result<MetadataPublishOutcome>,
    {
        let next_generation = self.generation + 1;
        let snapshot = write_engine_generation(&self.root, &self.engine, next_generation)?;
        sync_root_directory(&self.root, "sync engine root before metadata commit")?;
        match metadata_writer(&self.root, &snapshot)? {
            MetadataPublishOutcome::Durable => {
                self.generation = snapshot.generation;
                Ok(())
            }
            MetadataPublishOutcome::PublishedWithPostPublishSyncFailure(error) => {
                self.generation = snapshot.generation;
                Err(error)
            }
        }
    }

    pub fn flush(&mut self) -> crate::Result<()> {
        self.flush_with_metadata_writer(write_snapshot_metadata)
    }

    pub fn close(self) {}
}

impl EnginePathHandle {
    pub fn close(self) {
        match self {
            Self::ReadOnly(handle) => handle.close(),
            Self::ReadWrite(handle) => handle.close(),
        }
    }
}

fn load_committed_snapshot(root: &Path) -> crate::Result<(PathBuf, CommittedSnapshot)> {
    if !root.exists() {
        return Err(storage_error("engine root path is missing"));
    }
    if !root.is_dir() {
        return Err(storage_error("engine root path must be a directory"));
    }
    let canonical_root = canonical_root(root)?;

    let metadata_path = metadata_path(root);
    let metadata_path = resolve_existing_file_within_root(
        &canonical_root,
        &metadata_path,
        "missing metadata.json",
        "canonicalize metadata.json",
    )?;
    let metadata_bytes =
        fs::read(&metadata_path).map_err(|error| io_error("read metadata.json", error))?;
    let snapshot: CommittedSnapshot =
        serde_json::from_slice(&metadata_bytes).map_err(serialization_error)?;
    validate_committed_generation(&snapshot)?;

    let _frames_path = resolve_existing_file_within_root(
        &canonical_root,
        &committed_file_path(root, &snapshot.frames_file)?,
        format!(
            "missing committed frames generation {}",
            snapshot.generation
        ),
        "canonicalize frames file",
    )?;

    if let Some(cards_path) =
        validate_optional_committed_file(root, snapshot.cards_file.as_deref())?
    {
        let _ = resolve_existing_file_within_root(
            &canonical_root,
            &cards_path,
            "committed cards file is missing or not a file",
            "canonicalize cards file",
        )?;
    }
    if let Some(graph_path) =
        validate_optional_committed_file(root, snapshot.graph_file.as_deref())?
    {
        let _ = resolve_existing_file_within_root(
            &canonical_root,
            &graph_path,
            "committed graph file is missing or not a file",
            "canonicalize graph file",
        )?;
    }

    Ok((canonical_root, snapshot))
}

fn load_engine_from_snapshot(
    root: &Path,
    snapshot: &CommittedSnapshot,
) -> crate::Result<AnimaEngine> {
    let frames_bytes = fs::read(committed_file_path(root, &snapshot.frames_file)?)
        .map_err(|error| io_error("read frames file", error))?;
    let frames = FrameStore::deserialize(&frames_bytes)?;

    let cards = match snapshot.cards_file.as_deref() {
        Some(cards_file) => {
            let cards_bytes = fs::read(committed_file_path(root, cards_file)?)
                .map_err(|error| io_error("read cards file", error))?;
            CardStore::deserialize(&cards_bytes, SchemaRegistry::new())?
        }
        None => CardStore::new(SchemaRegistry::new()),
    };

    let graph = match snapshot.graph_file.as_deref() {
        Some(graph_file) => {
            let graph_bytes = fs::read(committed_file_path(root, graph_file)?)
                .map_err(|error| io_error("read graph file", error))?;
            KnowledgeGraph::deserialize(&graph_bytes)?
        }
        None => KnowledgeGraph::new(),
    };

    Ok(AnimaEngine::from_parts(frames, cards, graph))
}

fn write_engine_generation(
    root: &Path,
    engine: &AnimaEngine,
    generation: u64,
) -> crate::Result<CommittedSnapshot> {
    let frames_file = frames_file_name(generation);
    let frames_bytes = engine.frames().serialize()?;
    write_file_and_sync(&committed_file_path(root, &frames_file)?, &frames_bytes)?;

    let cards_file = if engine.cards().is_empty() {
        None
    } else {
        let cards_file = cards_file_name(generation);
        let cards_bytes = engine.cards().serialize()?;
        write_file_and_sync(&committed_file_path(root, &cards_file)?, &cards_bytes)?;
        Some(cards_file)
    };

    let graph_file = if engine.graph().is_empty() {
        None
    } else {
        let graph_file = graph_file_name(generation);
        let graph_bytes = engine.graph().serialize()?;
        write_file_and_sync(&committed_file_path(root, &graph_file)?, &graph_bytes)?;
        Some(graph_file)
    };

    Ok(CommittedSnapshot {
        generation,
        frames_file,
        cards_file,
        graph_file,
    })
}

pub(crate) fn create_path(path: impl AsRef<Path>) -> crate::Result<ReadWritePathEngineHandle> {
    let root = path.as_ref();
    if root.exists() {
        if !root.is_dir() {
            return Err(storage_error("engine root path must be a directory"));
        }
    } else {
        fs::create_dir_all(root).map_err(|error| io_error("create engine root", error))?;
    }

    let canonical_root = canonical_root(root)?;
    let lock = WriterLockGuard::acquire(&canonical_root)?;
    let _snapshot = initialize_empty_engine_dir(&canonical_root)?;
    let (canonical_root, snapshot) = load_committed_snapshot(&canonical_root)?;
    let engine = load_engine_from_snapshot(&canonical_root, &snapshot)?;

    Ok(ReadWritePathEngineHandle {
        engine,
        root: canonical_root,
        generation: snapshot.generation,
        lock,
    })
}

fn first_dir_entry<I>(entries: &mut I) -> crate::Result<Option<fs::DirEntry>>
where
    I: Iterator<Item = std::io::Result<fs::DirEntry>>,
{
    match entries.next() {
        Some(Ok(entry)) => Ok(Some(entry)),
        Some(Err(error)) => Err(io_error("read engine root entry", error)),
        None => Ok(None),
    }
}

fn is_metadata_staging_file_name(name: &OsStr) -> bool {
    let Some(name) = name.to_str() else {
        return false;
    };

    name == format!("{METADATA_FILE_NAME}.tmp")
        || name.starts_with(&format!("{METADATA_FILE_NAME}.tmp."))
}

fn is_exact_empty_bootstrap_frames_file(path: &Path) -> crate::Result<bool> {
    let expected = FrameStore::new().serialize()?;
    let actual = fs::read(path).map_err(|error| io_error("read bootstrap residue", error))?;
    Ok(actual == expected)
}

fn entry_is_recoverable_bootstrap_residue(entry: &fs::DirEntry) -> crate::Result<bool> {
    let file_type = entry
        .file_type()
        .map_err(|error| io_error("read engine root entry type", error))?;
    if !file_type.is_file() {
        return Ok(false);
    }

    let file_name = entry.file_name();
    if is_metadata_staging_file_name(&file_name) {
        return Ok(true);
    }

    if file_name == OsStr::new(&frames_file_name(0)) {
        return is_exact_empty_bootstrap_frames_file(&entry.path());
    }

    Ok(false)
}

fn cleanup_recoverable_bootstrap_residue(paths: &[PathBuf]) -> crate::Result<()> {
    for path in paths {
        fs::remove_file(path).map_err(|error| io_error("remove bootstrap residue", error))?;
    }

    Ok(())
}

fn initialize_empty_engine_dir(root: impl AsRef<Path>) -> crate::Result<CommittedSnapshot> {
    let root = root.as_ref();

    if root.exists() {
        if !root.is_dir() {
            return Err(storage_error("engine root path must be a directory"));
        }

        let mut entries =
            fs::read_dir(root).map_err(|error| io_error("read engine root", error))?;
        let mut recoverable_residue = Vec::new();
        while let Some(entry) = first_dir_entry(&mut entries)? {
            if entry.file_name() == OsStr::new(".lock") {
                continue;
            }
            if metadata_path(root).exists() {
                return Err(storage_error(
                    "path already looks like an engine directory; use open_path",
                ));
            }

            if entry_is_recoverable_bootstrap_residue(&entry)? {
                recoverable_residue.push(entry.path());
                continue;
            }

            return Err(storage_error(
                "engine root must be empty before initialization",
            ));
        }

        if !recoverable_residue.is_empty() {
            cleanup_recoverable_bootstrap_residue(&recoverable_residue)?;
        }
    } else {
        fs::create_dir_all(root).map_err(|error| io_error("create engine root", error))?;
    }

    let snapshot = CommittedSnapshot {
        generation: 0,
        frames_file: frames_file_name(0),
        cards_file: None,
        graph_file: None,
    };

    initialize_empty_engine_dir_with_metadata_writer(root, &snapshot, write_snapshot_metadata)?;

    Ok(snapshot)
}

pub(crate) fn open_path(
    root: impl AsRef<Path>,
    mode: EngineOpenMode,
) -> crate::Result<EnginePathHandle> {
    let root = root.as_ref();

    match mode {
        EngineOpenMode::ReadOnly => {
            let (canonical_root, snapshot) = load_committed_snapshot(root)?;
            let engine = load_engine_from_snapshot(&canonical_root, &snapshot)?;
            Ok(EnginePathHandle::ReadOnly(ReadOnlyPathEngineHandle {
                engine,
                root: canonical_root,
                generation: snapshot.generation,
            }))
        }
        EngineOpenMode::ReadWrite => {
            validate_engine_root_exists_and_is_dir(root)?;
            let canonical_root = canonical_root(root)?;
            let lock = WriterLockGuard::acquire(&canonical_root)?;
            match load_committed_snapshot(&canonical_root) {
                Ok((canonical_root, snapshot)) => {
                    match load_engine_from_snapshot(&canonical_root, &snapshot) {
                        Ok(engine) => Ok(EnginePathHandle::ReadWrite(ReadWritePathEngineHandle {
                            engine,
                            root: canonical_root,
                            generation: snapshot.generation,
                            lock,
                        })),
                        Err(error) => {
                            drop(lock);
                            Err(error)
                        }
                    }
                }
                Err(error) => {
                    drop(lock);
                    Err(error)
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use crate::cards::{
        CardStore, MemoryCard, MemoryKind, Polarity, SchemaRegistry, VersionRelation,
    };
    use crate::frame::{Frame, FrameKind, FrameSource, FrameStore};
    use crate::graph::{EntityKind, KnowledgeGraph};
    use crate::path_engine::{
        cards_file_name, create_path, first_dir_entry, frames_file_name, graph_file_name,
        initialize_empty_engine_dir, initialize_empty_engine_dir_with_metadata_writer,
        metadata_temp_path, open_path, replace_metadata_file, write_file_and_sync,
        write_snapshot_metadata_with_replace,
        write_snapshot_metadata_with_replace_and_post_publish, CommittedSnapshot, EngineOpenMode,
        EnginePathHandle, MetadataPublishOutcome,
    };

    fn make_card(
        entity: &str,
        slot: &str,
        value: &str,
        version: VersionRelation,
        frame_id: u64,
    ) -> MemoryCard {
        MemoryCard {
            id: 0,
            kind: MemoryKind::Fact,
            entity: entity.into(),
            slot: slot.into(),
            value: value.into(),
            polarity: Polarity::Neutral,
            version,
            confidence: 1.0,
            frame_id,
            created_at: frame_id as i64,
            active: true,
            superseded_by: None,
        }
    }

    mod lock {
        use std::fs;
        use std::fs::OpenOptions;

        use fs4::FileExt;
        use tempfile::tempdir;

        use crate::path_engine::{create_path, open_path, EngineOpenMode, EnginePathHandle};

        #[test]
        fn create_path_holds_lock_until_close() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let writer = create_path(&engine_dir).unwrap();
            let err = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap_err();

            assert!(matches!(err, crate::Error::LockConflict(message) if !message.is_empty()));

            writer.close();
        }

        #[test]
        fn create_path_acquires_lock_before_initializing_on_disk_state() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let lock_path = engine_dir.join(".lock");
            let lock_file = OpenOptions::new()
                .create(true)
                .read(true)
                .write(true)
                .open(&lock_path)
                .unwrap();
            lock_file.try_lock_exclusive().unwrap();

            let err = create_path(&engine_dir).unwrap_err();

            assert!(matches!(err, crate::Error::LockConflict(message) if !message.is_empty()));
            assert!(!engine_dir.join("metadata.json").exists());
            assert!(!engine_dir
                .join(crate::path_engine::frames_file_name(0))
                .exists());
        }

        #[test]
        fn second_writable_open_fails_with_lock_conflict() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let writer = create_path(&engine_dir).unwrap();
            let err = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap_err();

            assert!(matches!(err, crate::Error::LockConflict(message) if !message.is_empty()));

            drop(writer);
        }

        #[test]
        fn close_releases_writer_lock_for_next_writer() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let writer = create_path(&engine_dir).unwrap();
            writer.close();

            let reopened = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap();
            assert!(matches!(reopened, EnginePathHandle::ReadWrite(_)));

            reopened.close();
        }

        #[test]
        fn engine_path_handle_close_releases_writer_lock_for_next_writer() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let writer = create_path(&engine_dir).unwrap();
            writer.close();

            let handle = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap();
            handle.close();

            let reopened = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap();
            assert!(matches!(reopened, EnginePathHandle::ReadWrite(_)));

            reopened.close();
        }

        #[test]
        fn read_only_open_succeeds_while_writer_exists() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let writer = create_path(&engine_dir).unwrap();
            let reader = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap();

            assert!(matches!(reader, EnginePathHandle::ReadOnly(_)));

            drop(writer);
        }

        #[test]
        fn dropping_writer_releases_lock_for_next_writer() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let writer = create_path(&engine_dir).unwrap();
            drop(writer);

            let reopened = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap();
            assert!(matches!(reopened, EnginePathHandle::ReadWrite(_)));

            reopened.close();
        }

        #[test]
        fn unusable_lock_path_returns_io_error() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            fs::create_dir(&engine_dir).unwrap();
            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&crate::path_engine::CommittedSnapshot {
                    generation: 0,
                    frames_file: crate::path_engine::frames_file_name(0),
                    cards_file: None,
                    graph_file: None,
                })
                .unwrap(),
            )
            .unwrap();
            fs::write(
                engine_dir.join(crate::path_engine::frames_file_name(0)),
                crate::frame::FrameStore::new().serialize().unwrap(),
            )
            .unwrap();
            fs::create_dir(engine_dir.join(".lock")).unwrap();

            let err = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap_err();

            assert!(matches!(err, crate::Error::Io(message) if message.contains("open lock file")));
        }

        #[cfg(unix)]
        #[test]
        fn create_path_rejects_symlink_lock_path() {
            use std::os::unix::fs::symlink;

            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let outside = tempdir.path().join("outside");
            fs::create_dir(&outside).unwrap();
            let outside_lock = outside.join("lock-file");
            fs::write(&outside_lock, b"lock").unwrap();
            symlink(&outside_lock, engine_dir.join(".lock")).unwrap();

            let err = create_path(&engine_dir).unwrap_err();

            assert!(matches!(err, crate::Error::Storage(message) if message.contains("symlink")));
        }

        #[cfg(unix)]
        #[test]
        fn create_path_canonicalizes_symlinked_engine_root() {
            use std::os::unix::fs::symlink;

            let tempdir = tempdir().unwrap();
            let actual_root = tempdir.path().join("actual-engine");
            fs::create_dir(&actual_root).unwrap();

            let symlink_root = tempdir.path().join("engine");
            symlink(&actual_root, &symlink_root).unwrap();

            let writer = create_path(&symlink_root).unwrap();
            assert_eq!(writer.root, actual_root.canonicalize().unwrap());

            writer.close();

            let reopened = open_path(&actual_root, EngineOpenMode::ReadOnly).unwrap();
            assert!(matches!(reopened, EnginePathHandle::ReadOnly(_)));
        }

        #[test]
        fn readwrite_open_rejects_with_lock_conflict_before_snapshot_validation() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let lock_path = engine_dir.join(".lock");
            let lock_file = OpenOptions::new()
                .create(true)
                .read(true)
                .write(true)
                .open(&lock_path)
                .unwrap();
            lock_file.try_lock_exclusive().unwrap();

            fs::write(engine_dir.join("metadata.json"), b"not-json").unwrap();

            let err = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap_err();

            assert!(matches!(err, crate::Error::LockConflict(message) if !message.is_empty()));
        }

        #[test]
        fn readwrite_open_missing_root_returns_storage_error_without_lock_opening() {
            let tempdir = tempdir().unwrap();
            let missing_root = tempdir.path().join("missing");

            let err = open_path(&missing_root, EngineOpenMode::ReadWrite).unwrap_err();

            assert!(matches!(err, crate::Error::Storage(message) if message.contains("missing")));
        }

        #[test]
        fn readwrite_open_failure_leaves_inert_lock_file_and_allows_retry() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let err = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap_err();

            assert!(matches!(err, crate::Error::Storage(message) if message.contains("metadata")));
            assert!(engine_dir.join(".lock").exists());

            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&crate::path_engine::CommittedSnapshot {
                    generation: 0,
                    frames_file: crate::path_engine::frames_file_name(0),
                    cards_file: None,
                    graph_file: None,
                })
                .unwrap(),
            )
            .unwrap();
            fs::write(
                engine_dir.join(crate::path_engine::frames_file_name(0)),
                crate::frame::FrameStore::new().serialize().unwrap(),
            )
            .unwrap();

            let reopened = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap();
            assert!(matches!(reopened, EnginePathHandle::ReadWrite(_)));

            reopened.close();
        }

        #[test]
        fn permission_denied_is_not_treated_as_lock_contention() {
            let error = std::io::Error::new(std::io::ErrorKind::PermissionDenied, "acl denied");

            assert!(!crate::path_engine::is_lock_contention_error(&error));
        }
    }

    #[test]
    fn initialize_empty_engine_dir_seeds_generation_zero_and_committed_metadata() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");

        let snapshot = initialize_empty_engine_dir(&engine_dir).unwrap();

        assert_eq!(snapshot.generation, 0);
        assert_eq!(snapshot.frames_file, frames_file_name(0));
        assert_eq!(snapshot.cards_file, None);
        assert_eq!(snapshot.graph_file, None);

        let metadata_bytes = fs::read(engine_dir.join("metadata.json")).unwrap();
        let metadata: CommittedSnapshot = serde_json::from_slice(&metadata_bytes).unwrap();
        assert_eq!(metadata, snapshot);

        let frames_bytes = fs::read(engine_dir.join(snapshot.frames_file)).unwrap();
        let store = FrameStore::deserialize(&frames_bytes).unwrap();
        assert!(store.is_empty());
    }

    #[test]
    fn initialize_empty_engine_dir_fails_when_root_exists_as_file() {
        let tempdir = tempdir().unwrap();
        let root = tempdir.path().join("engine");
        fs::write(&root, b"not a dir").unwrap();

        let err = initialize_empty_engine_dir(&root).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("directory")));
    }

    #[test]
    fn initialize_empty_engine_dir_fails_when_root_directory_is_non_empty_invalid() {
        let tempdir = tempdir().unwrap();
        let root = tempdir.path().join("engine");
        fs::create_dir(&root).unwrap();
        fs::write(root.join("notes.txt"), b"payload").unwrap();

        let err = initialize_empty_engine_dir(&root).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("empty")));
    }

    #[test]
    fn initialize_empty_engine_dir_fails_when_directory_already_looks_like_an_engine_dir() {
        let tempdir = tempdir().unwrap();
        let root = tempdir.path().join("engine");
        fs::create_dir(&root).unwrap();
        fs::write(root.join("metadata.json"), b"{}").unwrap();

        let err = initialize_empty_engine_dir(&root).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("use open_path")));
    }

    #[test]
    fn create_path_recovers_generation_zero_bootstrap_residue() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        fs::write(
            engine_dir.join(frames_file_name(0)),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();
        let legacy_temp_path = metadata_temp_path(&engine_dir);
        fs::write(&legacy_temp_path, b"stale-metadata-temp").unwrap();
        let unique_temp_path = engine_dir.join("metadata.json.tmp.123.456.0");
        fs::write(&unique_temp_path, b"stale-unique-temp").unwrap();

        let writer = create_path(&engine_dir).unwrap();

        let metadata_bytes = fs::read(engine_dir.join("metadata.json")).unwrap();
        let metadata: CommittedSnapshot = serde_json::from_slice(&metadata_bytes).unwrap();
        assert_eq!(metadata.generation, 0);
        assert_eq!(metadata.frames_file, frames_file_name(0));
        assert!(!legacy_temp_path.exists());
        assert!(!unique_temp_path.exists());

        let frames_bytes = fs::read(engine_dir.join(frames_file_name(0))).unwrap();
        let store = FrameStore::deserialize(&frames_bytes).unwrap();
        assert!(store.is_empty());

        writer.close();
    }

    #[test]
    fn create_path_rejects_non_bootstrap_generation_zero_frames_residue() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        fs::write(
            engine_dir.join(frames_file_name(0)),
            b"not the bootstrap payload",
        )
        .unwrap();
        fs::write(engine_dir.join("metadata.json.tmp.1"), b"stale-temp").unwrap();

        let err = create_path(&engine_dir).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("empty")));
        assert!(!engine_dir.join("metadata.json").exists());
    }

    #[test]
    fn write_file_and_sync_writes_bytes_to_target_path() {
        let tempdir = tempdir().unwrap();
        let file_path = tempdir.path().join("committed.json");
        fs::write(&file_path, b"old bytes").unwrap();

        write_file_and_sync(&file_path, b"new bytes").unwrap();

        assert_eq!(fs::read(&file_path).unwrap(), b"new bytes");
    }

    #[test]
    fn open_path_fails_when_root_is_missing() {
        let tempdir = tempdir().unwrap();
        let missing_root = tempdir.path().join("missing");

        let err = open_path(&missing_root, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("missing")));
    }

    #[test]
    fn open_path_fails_when_metadata_is_missing() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("metadata")));
    }

    #[test]
    fn open_path_fails_when_committed_frames_generation_is_missing() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 7,
            frames_file: frames_file_name(7),
            cards_file: None,
            graph_file: None,
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("frames")));
    }

    #[test]
    fn open_path_fails_when_committed_frames_filename_does_not_match_generation() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 12,
            frames_file: frames_file_name(13),
            cards_file: None,
            graph_file: None,
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(frames_file_name(13)),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("frames_file")));
    }

    #[test]
    fn open_path_fails_when_metadata_path_is_a_directory() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();
        fs::create_dir(engine_dir.join("metadata.json")).unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("not a file")));
    }

    #[test]
    fn open_path_fails_when_metadata_is_malformed() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 18,
            frames_file: frames_file_name(18),
            cards_file: None,
            graph_file: None,
        };
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();
        fs::write(engine_dir.join("metadata.json"), b"not-json").unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Serialization(message) if !message.is_empty()));
    }

    mod snapshot {
        use super::*;
        use std::path::PathBuf;

        #[test]
        fn metadata_commit_keeps_previous_snapshot_visible_until_replace() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let committed = CommittedSnapshot {
                generation: 1,
                frames_file: frames_file_name(1),
                cards_file: None,
                graph_file: None,
            };
            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&committed).unwrap(),
            )
            .unwrap();

            let next = CommittedSnapshot {
                generation: 2,
                frames_file: frames_file_name(2),
                cards_file: Some(cards_file_name(2)),
                graph_file: Some(graph_file_name(2)),
            };

            write_snapshot_metadata_with_replace(&engine_dir, &next, |temp_path, metadata_path| {
                let visible_before_replace: CommittedSnapshot =
                    serde_json::from_slice(&fs::read(metadata_path).unwrap()).unwrap();
                assert_eq!(visible_before_replace, committed);

                let staged_bytes = fs::read(temp_path).unwrap();
                let staged_snapshot: CommittedSnapshot =
                    serde_json::from_slice(&staged_bytes).unwrap();
                assert_eq!(staged_snapshot, next);
                assert_ne!(temp_path, metadata_temp_path(&engine_dir));
                assert_eq!(temp_path.parent(), Some(engine_dir.as_path()));

                replace_metadata_file(temp_path, metadata_path)
            })
            .unwrap();

            let visible_after_replace: CommittedSnapshot =
                serde_json::from_slice(&fs::read(engine_dir.join("metadata.json")).unwrap())
                    .unwrap();
            assert_eq!(visible_after_replace, next);
            assert!(!metadata_temp_path(&engine_dir).exists());
        }

        #[test]
        fn metadata_commit_cleans_up_staged_temp_file_when_replace_fails() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let committed = CommittedSnapshot {
                generation: 1,
                frames_file: frames_file_name(1),
                cards_file: None,
                graph_file: None,
            };
            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&committed).unwrap(),
            )
            .unwrap();

            let next = CommittedSnapshot {
                generation: 2,
                frames_file: frames_file_name(2),
                cards_file: None,
                graph_file: None,
            };

            let mut staged_path: Option<PathBuf> = None;
            let err = write_snapshot_metadata_with_replace(&engine_dir, &next, |temp_path, _| {
                staged_path = Some(temp_path.to_path_buf());
                Err(crate::Error::Io("replace failed".into()))
            })
            .unwrap_err();

            assert!(matches!(err, crate::Error::Io(message) if message.contains("replace failed")));
            let staged_path = staged_path.expect("replace hook should observe staged path");
            assert!(!staged_path.exists());
            let visible_after_failure: CommittedSnapshot =
                serde_json::from_slice(&fs::read(engine_dir.join("metadata.json")).unwrap())
                    .unwrap();
            assert_eq!(visible_after_failure, committed);
        }

        #[test]
        fn metadata_commit_reports_published_warning_when_post_publish_sync_fails() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let committed = CommittedSnapshot {
                generation: 1,
                frames_file: frames_file_name(1),
                cards_file: None,
                graph_file: None,
            };
            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&committed).unwrap(),
            )
            .unwrap();

            let next = CommittedSnapshot {
                generation: 2,
                frames_file: frames_file_name(2),
                cards_file: None,
                graph_file: None,
            };

            let outcome = write_snapshot_metadata_with_replace_and_post_publish(
                &engine_dir,
                &next,
                |temp_path, metadata_path| replace_metadata_file(temp_path, metadata_path),
                |_root| Err(crate::Error::Io("post-publish sync failed".into())),
            )
            .unwrap();

            assert!(matches!(
                outcome,
                MetadataPublishOutcome::PublishedWithPostPublishSyncFailure(crate::Error::Io(
                    message
                )) if message.contains("snapshot was published")
                    && message.contains("post-publish sync failed")
            ));

            let visible_after_replace: CommittedSnapshot =
                serde_json::from_slice(&fs::read(engine_dir.join("metadata.json")).unwrap())
                    .unwrap();
            assert_eq!(visible_after_replace, next);
        }

        #[test]
        fn metadata_commit_does_not_reuse_preexisting_legacy_temp_path() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let committed = CommittedSnapshot {
                generation: 1,
                frames_file: frames_file_name(1),
                cards_file: None,
                graph_file: None,
            };
            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&committed).unwrap(),
            )
            .unwrap();

            let legacy_temp_path = metadata_temp_path(&engine_dir);
            fs::write(&legacy_temp_path, b"legacy-temp-payload").unwrap();

            let next = CommittedSnapshot {
                generation: 2,
                frames_file: frames_file_name(2),
                cards_file: None,
                graph_file: None,
            };

            write_snapshot_metadata_with_replace(&engine_dir, &next, |temp_path, metadata_path| {
                assert_ne!(temp_path, legacy_temp_path.as_path());
                replace_metadata_file(temp_path, metadata_path)
            })
            .unwrap();

            let visible_after_replace: CommittedSnapshot =
                serde_json::from_slice(&fs::read(engine_dir.join("metadata.json")).unwrap())
                    .unwrap();
            assert_eq!(visible_after_replace, next);
            assert_eq!(fs::read(&legacy_temp_path).unwrap(), b"legacy-temp-payload");
        }

        #[test]
        fn flush_persists_new_generation_and_reload_roundtrips_state() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let mut handle = create_path(&engine_dir).unwrap();
            let frame_id = handle.engine_mut().frames_mut().insert(Frame::new(
                0,
                FrameKind::Fact,
                "user works at OpenAI".into(),
                "user".into(),
                FrameSource::Extraction,
            ));
            handle.engine_mut().cards_mut().put(make_card(
                "user",
                "employer",
                "OpenAI",
                VersionRelation::Sets,
                frame_id,
            ));
            handle
                .engine_mut()
                .graph_mut()
                .upsert_node("user", EntityKind::Person, 1.0, frame_id)
                .unwrap();
            handle
                .engine_mut()
                .graph_mut()
                .upsert_node("OpenAI", EntityKind::Organization, 1.0, frame_id)
                .unwrap();
            let from = handle.engine().graph().get_by_name("user").unwrap().id;
            let to = handle.engine().graph().get_by_name("OpenAI").unwrap().id;
            handle
                .engine_mut()
                .graph_mut()
                .upsert_edge(from, to, "employer", 1.0, frame_id)
                .unwrap();

            handle.flush().unwrap();

            let metadata: CommittedSnapshot =
                serde_json::from_slice(&fs::read(engine_dir.join("metadata.json")).unwrap())
                    .unwrap();
            assert_eq!(metadata.generation, 1);
            assert_eq!(metadata.frames_file, frames_file_name(1));
            assert_eq!(metadata.cards_file, Some(cards_file_name(1)));
            assert_eq!(metadata.graph_file, Some(graph_file_name(1)));
            assert!(engine_dir.join(frames_file_name(0)).exists());
            assert!(engine_dir.join(frames_file_name(1)).exists());
            assert!(engine_dir.join(cards_file_name(1)).exists());
            assert!(engine_dir.join(graph_file_name(1)).exists());

            let reopened = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap();
            match reopened {
                EnginePathHandle::ReadOnly(handle) => {
                    assert_eq!(handle.engine().frames().len(), 1);
                    assert_eq!(handle.engine().cards().len(), 1);
                    assert_eq!(handle.engine().graph().node_count(), 2);
                    assert_eq!(handle.engine().graph().edge_count(), 1);
                    assert_eq!(
                        handle.engine().frames().iter().next().unwrap().content,
                        "user works at OpenAI"
                    );
                    assert_eq!(
                        handle.engine().cards().get_current("user", "employer")[0].value,
                        "OpenAI"
                    );
                }
                _ => panic!("expected read-only handle"),
            }
        }

        #[test]
        fn flush_advances_generation_when_post_publish_sync_fails() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");

            let mut handle = create_path(&engine_dir).unwrap();
            let err = handle
                .flush_with_metadata_writer(|root, snapshot| {
                    write_snapshot_metadata_with_replace_and_post_publish(
                        root,
                        snapshot,
                        replace_metadata_file,
                        |_root| Err(crate::Error::Io("post-publish sync failed".into())),
                    )
                })
                .unwrap_err();

            assert!(matches!(
                err,
                crate::Error::Io(message)
                    if message.contains("snapshot was published")
                        && message.contains("post-publish sync failed")
            ));
            assert_eq!(handle.generation, 1);

            handle.flush().unwrap();

            let metadata: CommittedSnapshot =
                serde_json::from_slice(&fs::read(engine_dir.join("metadata.json")).unwrap())
                    .unwrap();
            assert_eq!(metadata.generation, 2);
            assert!(engine_dir.join(frames_file_name(1)).exists());
            assert!(engine_dir.join(frames_file_name(2)).exists());
        }

        #[test]
        fn missing_derived_files_load_as_empty_state() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let mut frames = FrameStore::new();
            let frame_id = frames.insert(Frame::new(
                0,
                FrameKind::Fact,
                "frame only".into(),
                "user".into(),
                FrameSource::Extraction,
            ));

            let snapshot = CommittedSnapshot {
                generation: 4,
                frames_file: frames_file_name(4),
                cards_file: None,
                graph_file: None,
            };
            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&snapshot).unwrap(),
            )
            .unwrap();
            fs::write(
                engine_dir.join(&snapshot.frames_file),
                frames.serialize().unwrap(),
            )
            .unwrap();

            let opened = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap();
            match opened {
                EnginePathHandle::ReadOnly(handle) => {
                    assert_eq!(handle.engine().frames().len(), 1);
                    assert_eq!(
                        handle.engine().frames().get(frame_id).unwrap().content,
                        "frame only"
                    );
                    assert!(handle.engine().cards().is_empty());
                    assert!(handle.engine().graph().is_empty());
                }
                _ => panic!("expected read-only handle"),
            }
        }

        #[test]
        fn malformed_derived_file_fails_instead_of_loading_as_missing() {
            let tempdir = tempdir().unwrap();
            let engine_dir = tempdir.path().join("engine");
            fs::create_dir(&engine_dir).unwrap();

            let mut frames = FrameStore::new();
            frames.insert(Frame::new(
                0,
                FrameKind::Fact,
                "frame plus malformed cards".into(),
                "user".into(),
                FrameSource::Extraction,
            ));

            let snapshot = CommittedSnapshot {
                generation: 5,
                frames_file: frames_file_name(5),
                cards_file: Some(cards_file_name(5)),
                graph_file: None,
            };
            fs::write(
                engine_dir.join("metadata.json"),
                serde_json::to_vec(&snapshot).unwrap(),
            )
            .unwrap();
            fs::write(
                engine_dir.join(&snapshot.frames_file),
                frames.serialize().unwrap(),
            )
            .unwrap();
            fs::write(
                engine_dir.join(snapshot.cards_file.as_ref().unwrap()),
                b"not valid card store bytes",
            )
            .unwrap();

            let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

            assert!(matches!(err, crate::Error::Serialization(message) if !message.is_empty()));
        }
    }

    #[test]
    fn initialize_empty_engine_dir_reports_io_when_entry_iteration_fails() {
        struct FailingIter;

        impl Iterator for FailingIter {
            type Item = std::io::Result<fs::DirEntry>;

            fn next(&mut self) -> Option<Self::Item> {
                Some(Err(std::io::Error::other("entry read failed")))
            }
        }

        let mut iter = FailingIter;
        let err = first_dir_entry(&mut iter).unwrap_err();

        assert!(matches!(err, crate::Error::Io(message) if message.contains("entry read failed")));
    }

    #[cfg(unix)]
    #[test]
    fn open_path_rejects_metadata_symlink_escape() {
        use std::os::unix::fs::symlink;

        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let outside = tempdir.path().join("outside");
        fs::create_dir(&outside).unwrap();
        let outside_metadata = outside.join("metadata.json");
        fs::write(
            &outside_metadata,
            serde_json::to_vec(&CommittedSnapshot {
                generation: 1,
                frames_file: frames_file_name(1),
                cards_file: None,
                graph_file: None,
            })
            .unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(frames_file_name(1)),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();
        symlink(&outside_metadata, engine_dir.join("metadata.json")).unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(
            matches!(err, crate::Error::Storage(message) if message.contains("escapes engine root"))
        );
    }

    #[test]
    fn open_path_accepts_present_local_committed_derived_filenames() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 8,
            frames_file: frames_file_name(8),
            cards_file: Some("generation-8.cards.json".into()),
            graph_file: Some("generation-8.graph.json".into()),
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(snapshot.cards_file.as_ref().unwrap()),
            CardStore::new(SchemaRegistry::new()).serialize().unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(snapshot.graph_file.as_ref().unwrap()),
            KnowledgeGraph::new().serialize().unwrap(),
        )
        .unwrap();

        let opened = open_path(&engine_dir, EngineOpenMode::ReadWrite).unwrap();

        match opened {
            EnginePathHandle::ReadWrite(handle) => {
                assert_eq!(handle.root, engine_dir.canonicalize().unwrap());
            }
            _ => panic!("expected read-write handle"),
        }
    }

    #[test]
    fn open_path_rejects_non_local_committed_filename_with_parent_directory() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 19,
            frames_file: frames_file_name(19),
            cards_file: Some("../generation-19.cards.json".into()),
            graph_file: None,
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("engine-local")));
    }

    #[test]
    fn open_path_rejects_non_local_committed_filename_with_nested_path() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 20,
            frames_file: frames_file_name(20),
            cards_file: None,
            graph_file: Some("nested/generation-20.graph.json".into()),
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("engine-local")));
    }

    #[test]
    fn open_path_allows_absent_optional_derived_files() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 9,
            frames_file: frames_file_name(9),
            cards_file: None,
            graph_file: None,
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();

        let opened = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap();

        match opened {
            EnginePathHandle::ReadOnly(handle) => {
                assert_eq!(handle.root, engine_dir.canonicalize().unwrap());
            }
            _ => panic!("expected read-only handle"),
        }
    }

    #[test]
    fn open_path_fails_when_frames_path_is_a_directory() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 10,
            frames_file: frames_file_name(10),
            cards_file: None,
            graph_file: None,
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::create_dir(engine_dir.join(&snapshot.frames_file)).unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("frames")));
    }

    #[test]
    fn open_path_fails_when_named_optional_derived_file_is_missing() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 11,
            frames_file: frames_file_name(11),
            cards_file: Some("generation-11.cards.json".into()),
            graph_file: Some("generation-11.graph.json".into()),
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(snapshot.cards_file.as_ref().unwrap()),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("graph")));
    }

    #[test]
    fn open_path_fails_when_optional_cards_filename_does_not_match_generation() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 14,
            frames_file: frames_file_name(14),
            cards_file: Some("generation-15.cards.json".into()),
            graph_file: None,
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(snapshot.cards_file.as_ref().unwrap()),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("cards_file")));
    }

    #[test]
    fn open_path_fails_when_optional_graph_filename_does_not_match_generation() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 16,
            frames_file: frames_file_name(16),
            cards_file: None,
            graph_file: Some("generation-17.graph.json".into()),
        };
        fs::write(
            engine_dir.join("metadata.json"),
            serde_json::to_vec(&snapshot).unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(&snapshot.frames_file),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();
        fs::write(
            engine_dir.join(snapshot.graph_file.as_ref().unwrap()),
            FrameStore::new().serialize().unwrap(),
        )
        .unwrap();

        let err = open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap_err();

        assert!(matches!(err, crate::Error::Storage(message) if message.contains("graph_file")));
    }

    #[test]
    fn initialize_empty_engine_dir_cleans_up_frames_when_metadata_write_fails() {
        let tempdir = tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");
        fs::create_dir(&engine_dir).unwrap();

        let snapshot = CommittedSnapshot {
            generation: 0,
            frames_file: frames_file_name(0),
            cards_file: None,
            graph_file: None,
        };

        let err = initialize_empty_engine_dir_with_metadata_writer(
            &engine_dir,
            &snapshot,
            |_root, _snapshot| Err(crate::Error::Io("metadata write failed".into())),
        )
        .unwrap_err();

        assert!(
            matches!(err, crate::Error::Io(message) if message.contains("metadata write failed"))
        );
        assert!(!engine_dir.join(&snapshot.frames_file).exists());
        assert!(!engine_dir.join("metadata.json").exists());
    }
}
