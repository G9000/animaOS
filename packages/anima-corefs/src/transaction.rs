//! Core-wide atomic catalog commit coordination.

use std::collections::{HashMap, HashSet};
use std::ffi::OsStr;
use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom, Write};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt as _;
use std::path::{Path, PathBuf};

use cap_fs_ext::MetadataExt as _;
use cap_std::ambient_authority;
#[cfg(unix)]
use cap_std::fs::OpenOptionsExt as _;
#[cfg(windows)]
use cap_std::fs::OpenOptionsExt as _;
use cap_std::fs::{Dir, Metadata, OpenOptions};
use fs4::FileExt;
use getrandom::getrandom;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::catalog::{
    catalog_generation_physical_name, decrypt_catalog_generation, encrypt_catalog_generation,
    CatalogCutoverMarker, CatalogError, CatalogGeneration, CatalogGenerationEntry, CatalogObject,
    ContentHash, ObjectPhysicalName, WrappedObjectDekRecord, MAX_CATALOG_ENVELOPE_SIZE,
};
use crate::crypto::{
    unwrap_object_dek, wrap_object_dek, CryptoError, ObjectKeyAad, OBJECT_KEY_ENVELOPE_VERSION,
};
use crate::crypto::{FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes};
use crate::envelope::{read_envelope, EnvelopeError, MAX_ENVELOPE_SIZE};
use crate::folders::PortableName;
use crate::head::{decode_head, encode_head, HeadError, HeadRecord, MAX_HEAD_SIZE};
use crate::id::OpaqueId;
#[cfg(any(unix, test))]
use crate::publication::is_temporary_name_for_target;
use crate::publication::{
    atomic_publish_in, create_temporary_in, durable_create_directory_in, publish_immutable_in,
    publish_staged_immutable_in,
};

const LOCK_SCHEMA_VERSION: u16 = 1;
const MAX_LOCK_METADATA_SIZE: usize = 4096;
const COPY_BUFFER_SIZE: usize = 1024 * 1024;
const FS_DIRECTORY: &str = "fs";
const CATALOGS_DIRECTORY: &str = "catalogs";
const OBJECTS_DIRECTORY: &str = "objects";
const HEAD_FILE: &str = "HEAD";
const VALIDATION_HEAD_FILE: &str = "VALIDATION_HEAD";
const CUTOVER_RECEIPT_FILE: &str = "CUTOVER_RECEIPT";
const COMMIT_LOCK_FILE: &str = "commit.lock";
#[cfg(any(unix, test))]
const COMMIT_LOCK_FILE_MODE: u32 = 0o600;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcessIdentity {
    pid: u32,
    process_start_time: u64,
}

impl ProcessIdentity {
    pub const fn pid(&self) -> u32 {
        self.pid
    }

    pub const fn process_start_time(&self) -> u64 {
        self.process_start_time
    }

    fn current() -> Result<Self, CommitError> {
        let pid = std::process::id();
        let process_start_time = inspect_process_start_time(pid)?.ok_or_else(|| {
            CommitError::ProcessInspection("current process is not inspectable".to_owned())
        })?;
        if process_start_time == 0 {
            return Err(CommitError::ProcessInspection(
                "current process start time is unavailable".to_owned(),
            ));
        }
        Ok(Self {
            pid,
            process_start_time,
        })
    }

    fn is_still_alive(&self) -> Result<bool, CommitError> {
        Ok(inspect_process_start_time(self.pid)?
            .is_some_and(|start_time| start_time == self.process_start_time))
    }
}

#[cfg(windows)]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, CommitError> {
    use windows_sys::Win32::Foundation::{
        CloseHandle, ERROR_INVALID_PARAMETER, FILETIME, STILL_ACTIVE,
    };
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(ERROR_INVALID_PARAMETER as i32) {
            return Ok(None);
        }
        return Err(CommitError::ProcessInspection(format!(
            "cannot inspect PID {pid}: {error}"
        )));
    }

    let result = (|| {
        let mut exit_code = 0_u32;
        if unsafe { GetExitCodeProcess(handle, &mut exit_code) } == 0 {
            return Err(CommitError::ProcessInspection(format!(
                "cannot inspect PID {pid} exit state: {}",
                io::Error::last_os_error()
            )));
        }
        if exit_code != STILL_ACTIVE as u32 {
            return Ok(None);
        }

        let mut created = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        let mut exited = created;
        let mut kernel = created;
        let mut user = created;
        if unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) }
            == 0
        {
            return Err(CommitError::ProcessInspection(format!(
                "cannot inspect PID {pid} creation time: {}",
                io::Error::last_os_error()
            )));
        }
        Ok(Some(
            (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime),
        ))
    })();
    unsafe { CloseHandle(handle) };
    result
}

#[cfg(any(target_os = "linux", target_os = "android"))]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, CommitError> {
    let stat_path = PathBuf::from(format!("/proc/{pid}/stat"));
    let stat = match fs::read_to_string(&stat_path) {
        Ok(stat) => stat,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            prove_unix_process_absent(pid)?;
            return Ok(None);
        }
        Err(error) => {
            return Err(CommitError::ProcessInspection(format!(
                "cannot inspect PID {pid}: {error}"
            )))
        }
    };
    parse_linux_process_stat(pid, &stat)
}

#[cfg(any(target_os = "linux", target_os = "android", test))]
fn parse_linux_process_stat(pid: u32, stat: &str) -> Result<Option<u64>, CommitError> {
    let command_end = stat.rfind(')').ok_or_else(|| {
        CommitError::ProcessInspection(format!("PID {pid} has malformed /proc stat data"))
    })?;
    let mut fields = stat[command_end + 1..].split_whitespace();
    let state = fields.next().ok_or_else(|| {
        CommitError::ProcessInspection(format!("PID {pid} has truncated /proc stat data"))
    })?;
    if matches!(state, "Z" | "X" | "x") {
        return Ok(None);
    }
    let start_time = fields
        .nth(18)
        .ok_or_else(|| {
            CommitError::ProcessInspection(format!("PID {pid} has truncated /proc stat data"))
        })?
        .parse::<u64>()
        .map_err(|_| {
            CommitError::ProcessInspection(format!("PID {pid} has invalid /proc start time"))
        })?;
    Ok(Some(start_time))
}

#[cfg(any(target_os = "macos", target_os = "ios"))]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, CommitError> {
    let mut info = unsafe { std::mem::zeroed::<libc::proc_bsdinfo>() };
    let size = std::mem::size_of::<libc::proc_bsdinfo>();
    let written = unsafe {
        libc::proc_pidinfo(
            pid as libc::c_int,
            libc::PROC_PIDTBSDINFO,
            0,
            std::ptr::addr_of_mut!(info).cast::<libc::c_void>(),
            size as libc::c_int,
        )
    };
    if written == 0 {
        prove_unix_process_absent(pid)?;
        return Ok(None);
    }
    if written as usize != size || info.pbi_pid != pid {
        return Err(CommitError::ProcessInspection(format!(
            "PID {pid} returned incomplete native process identity"
        )));
    }
    let micros = info
        .pbi_start_tvsec
        .checked_mul(1_000_000)
        .and_then(|value| value.checked_add(info.pbi_start_tvusec))
        .ok_or_else(|| {
            CommitError::ProcessInspection(format!("PID {pid} start identity overflow"))
        })?;
    Ok(Some(micros))
}

#[cfg(all(
    unix,
    not(any(
        target_os = "linux",
        target_os = "android",
        target_os = "macos",
        target_os = "ios"
    ))
))]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, CommitError> {
    Err(CommitError::ProcessInspection(format!(
        "precise process start identity is unsupported for PID {pid} on this Unix platform"
    )))
}

#[cfg(unix)]
fn prove_unix_process_absent(pid: u32) -> Result<(), CommitError> {
    let result = unsafe { libc::kill(pid as libc::pid_t, 0) };
    if result == 0 {
        return Err(CommitError::ProcessInspection(format!(
            "PID {pid} exists but its start identity is unavailable"
        )));
    }
    let error = io::Error::last_os_error();
    match error.raw_os_error() {
        Some(libc::ESRCH) => Ok(()),
        _ => Err(CommitError::ProcessInspection(format!(
            "cannot prove PID {pid} is gone: {error}"
        ))),
    }
}

#[cfg(not(any(windows, unix)))]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, CommitError> {
    Err(CommitError::ProcessInspection(format!(
        "process start identity is unsupported for PID {pid} on this platform"
    )))
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LockOwnerMetadata {
    schema_version: u16,
    pid: u32,
    process_start_time: u64,
}

impl From<&ProcessIdentity> for LockOwnerMetadata {
    fn from(value: &ProcessIdentity) -> Self {
        Self {
            schema_version: LOCK_SCHEMA_VERSION,
            pid: value.pid,
            process_start_time: value.process_start_time,
        }
    }
}

impl LockOwnerMetadata {
    fn identity(&self) -> ProcessIdentity {
        ProcessIdentity {
            pid: self.pid,
            process_start_time: self.process_start_time,
        }
    }
}

pub struct CoreCommitLock {
    file: File,
    anchor: File,
    identity: ProcessIdentity,
    _fs_dir: Dir,
}

impl fmt::Debug for CoreCommitLock {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CoreCommitLock")
            .field("identity", &self.identity)
            .finish_non_exhaustive()
    }
}

impl CoreCommitLock {
    pub fn acquire(core_root: &Path) -> Result<Self, CommitError> {
        ensure_ambient_directory(core_root)?;
        let root_dir = Dir::open_ambient_dir(core_root, ambient_authority())?;
        let fs_dir = ensure_child_directory(&root_dir, FS_DIRECTORY)?;
        Self::acquire_in(&root_dir, &fs_dir)
    }

    fn acquire_in(root_dir: &Dir, fs_dir: &Dir) -> Result<Self, CommitError> {
        #[cfg(windows)]
        let _ = root_dir;
        let fs_dir = fs_dir.try_clone()?;
        reject_symlink_in(&fs_dir, OsStr::new(COMMIT_LOCK_FILE))?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        options.mode(COMMIT_LOCK_FILE_MODE);
        #[cfg(windows)]
        options.share_mode(
            windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ
                | windows_sys::Win32::Storage::FileSystem::FILE_SHARE_WRITE,
        );
        let mut file = fs_dir.open_with(COMMIT_LOCK_FILE, &options)?.into_std();
        validate_opened_regular_file(&fs_dir, OsStr::new(COMMIT_LOCK_FILE), &file)?;
        #[cfg(unix)]
        secure_commit_lock_permissions(&file)?;
        #[cfg(not(windows))]
        let anchor = root_dir.try_clone()?.into_std_file();
        #[cfg(windows)]
        let anchor = file.try_clone()?;
        anchor.try_lock_exclusive().map_err(|error| {
            if is_lock_contention_error(&error) {
                CommitError::LockBusy
            } else {
                CommitError::Io(error)
            }
        })?;

        let identity = ProcessIdentity::current()?;
        let recorded = match read_lock_metadata(&mut file) {
            Ok(recorded) => recorded,
            // The authoritative kernel lock is already held, so malformed
            // bytes can only be crash residue or inactive tampering. Replace
            // them below while preserving live-owner checks for valid records.
            Err(CommitError::InvalidLockMetadata) => None,
            Err(error) => return Err(error),
        };
        if let Some(recorded) = recorded {
            let recorded_identity = recorded.identity();
            if recorded_identity.is_still_alive()? {
                return Err(CommitError::RecordedOwnerAlive {
                    pid: recorded_identity.pid,
                    process_start_time: recorded_identity.process_start_time,
                });
            }
        }
        write_lock_metadata(&mut file, &identity)?;
        Ok(Self {
            file,
            anchor,
            identity,
            _fs_dir: fs_dir,
        })
    }

    pub fn owner_identity(&self) -> &ProcessIdentity {
        &self.identity
    }

    pub fn encoded_owner_metadata(&self) -> Vec<u8> {
        serde_json::to_vec(&LockOwnerMetadata::from(&self.identity))
            .expect("lock owner metadata is serializable")
    }
}

impl Drop for CoreCommitLock {
    fn drop(&mut self) {
        let _ = self.file.set_len(0);
        let _ = self.file.seek(SeekFrom::Start(0));
        let _ = self.file.sync_all();
        let _ = FileExt::unlock(&self.anchor);
    }
}

fn read_lock_metadata(file: &mut File) -> Result<Option<LockOwnerMetadata>, CommitError> {
    let length = file.metadata()?.len();
    if length == 0 {
        return Ok(None);
    }
    if length > MAX_LOCK_METADATA_SIZE as u64 {
        return Err(CommitError::InvalidLockMetadata);
    }
    file.seek(SeekFrom::Start(0))?;
    let mut encoded = Vec::with_capacity(length as usize);
    file.take(MAX_LOCK_METADATA_SIZE as u64 + 1)
        .read_to_end(&mut encoded)?;
    if encoded.len() > MAX_LOCK_METADATA_SIZE {
        return Err(CommitError::InvalidLockMetadata);
    }
    let metadata: LockOwnerMetadata =
        serde_json::from_slice(&encoded).map_err(|_| CommitError::InvalidLockMetadata)?;
    if metadata.schema_version != LOCK_SCHEMA_VERSION
        || metadata.pid == 0
        || metadata.process_start_time == 0
        || serde_json::to_vec(&metadata).map_err(|_| CommitError::InvalidLockMetadata)? != encoded
    {
        return Err(CommitError::InvalidLockMetadata);
    }
    Ok(Some(metadata))
}

fn write_lock_metadata(file: &mut File, identity: &ProcessIdentity) -> Result<(), CommitError> {
    let encoded = serde_json::to_vec(&LockOwnerMetadata::from(identity))
        .map_err(|_| CommitError::InvalidLockMetadata)?;
    file.set_len(0)?;
    file.seek(SeekFrom::Start(0))?;
    file.write_all(&encoded)?;
    file.sync_all()?;
    Ok(())
}

fn is_lock_contention_error(error: &io::Error) -> bool {
    matches!(error.kind(), io::ErrorKind::WouldBlock) || error.raw_os_error() == Some(33)
}

#[cfg(unix)]
fn secure_commit_lock_permissions(file: &File) -> Result<(), CommitError> {
    let mut permissions = file.metadata()?.permissions();
    if permissions.mode() & 0o777 != COMMIT_LOCK_FILE_MODE {
        permissions.set_mode(COMMIT_LOCK_FILE_MODE);
        file.set_permissions(permissions)?;
    }
    if file.metadata()?.permissions().mode() & 0o777 != COMMIT_LOCK_FILE_MODE {
        return Err(CommitError::InvalidCoreLayout);
    }
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct CatalogPathSnapshot {
    // Clone the complete catalog entries, not only display path fields. This
    // makes owner/access/policy or metadata drift anywhere in the ancestor
    // chain invalidate an authorization decision made before the lock.
    components: Vec<CatalogGenerationEntry>,
}

impl CatalogPathSnapshot {
    fn capture(catalog: &CatalogGeneration, stable_id: &OpaqueId) -> Option<Self> {
        let mut components = Vec::new();
        let mut next = stable_id;
        loop {
            let entry = catalog
                .entries()
                .iter()
                .find(|entry| entry.stable_id() == next)?;
            components.push(entry.clone());
            let Some(parent_id) = entry.parent_id() else {
                break;
            };
            next = parent_id;
        }
        components.reverse();
        Some(Self { components })
    }

    fn stable_id(&self) -> &OpaqueId {
        self.components
            .last()
            .expect("catalog path snapshots are non-empty")
            .stable_id()
    }

    fn leaf_is_folder(&self) -> bool {
        self.components
            .last()
            .expect("catalog path snapshots are non-empty")
            .is_folder()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObjectPrecondition {
    path: CatalogPathSnapshot,
    revision: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FolderPrecondition {
    path: CatalogPathSnapshot,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VacantPrecondition {
    parent_path: CatalogPathSnapshot,
    name: PortableName,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CatalogPrecondition {
    Object(ObjectPrecondition),
    Folder(FolderPrecondition),
    Vacant(VacantPrecondition),
}

impl CatalogPrecondition {
    pub fn object(
        catalog: &CatalogGeneration,
        stable_id: &OpaqueId,
        revision: u64,
    ) -> Result<Self, CommitConflict> {
        let path = CatalogPathSnapshot::capture(catalog, stable_id).ok_or_else(|| {
            CommitConflict::PathOrRevision {
                stable_id: stable_id.as_str().to_owned(),
            }
        })?;
        let entry = find_entry(Some(catalog), stable_id).expect("captured entry exists");
        if revision == 0
            || entry
                .object_payload()
                .map_or(true, |object| object.revision() != revision)
        {
            return Err(CommitConflict::PathOrRevision {
                stable_id: stable_id.as_str().to_owned(),
            });
        }
        Ok(Self::Object(ObjectPrecondition { path, revision }))
    }

    pub fn folder(
        catalog: &CatalogGeneration,
        stable_id: &OpaqueId,
    ) -> Result<Self, CommitConflict> {
        let path = CatalogPathSnapshot::capture(catalog, stable_id).ok_or_else(|| {
            CommitConflict::PathOrRevision {
                stable_id: stable_id.as_str().to_owned(),
            }
        })?;
        if !path.leaf_is_folder() {
            return Err(CommitConflict::PathOrRevision {
                stable_id: stable_id.as_str().to_owned(),
            });
        }
        Ok(Self::Folder(FolderPrecondition { path }))
    }

    pub fn vacant(
        catalog: &CatalogGeneration,
        parent_id: &OpaqueId,
        name: PortableName,
    ) -> Result<Self, CommitConflict> {
        let parent_path = CatalogPathSnapshot::capture(catalog, parent_id).ok_or_else(|| {
            CommitConflict::InvalidDestinationParent {
                parent_id: parent_id.as_str().to_owned(),
            }
        })?;
        if !parent_path.leaf_is_folder() {
            return Err(CommitConflict::InvalidDestinationParent {
                parent_id: parent_id.as_str().to_owned(),
            });
        }
        Ok(Self::Vacant(VacantPrecondition { parent_path, name }))
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct PreparedObjectRevision {
    object_id: OpaqueId,
    revision: u64,
    kind: ObjectKind,
    object_key_epoch: u32,
    physical_name: ObjectPhysicalName,
    content_hash: ContentHash,
    encoded_size: u64,
    encrypted_hash: [u8; 32],
    object_key_binding: [u8; 32],
    wrapped_dek: WrappedObjectDekRecord,
}

impl fmt::Debug for PreparedObjectRevision {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PreparedObjectRevision")
            .field("object_id", &self.object_id)
            .field("revision", &self.revision)
            .field("kind", &self.kind)
            .field("object_key_epoch", &self.object_key_epoch)
            .field("physical_name", &self.physical_name)
            .field("content_hash", &self.content_hash)
            .field("encoded_size", &self.encoded_size)
            .field("encrypted_hash", &self.encrypted_hash)
            .field("object_key_binding", &"<redacted>")
            .field("wrapped_dek", &"<redacted>")
            .finish()
    }
}

impl PreparedObjectRevision {
    pub fn object_id(&self) -> &OpaqueId {
        &self.object_id
    }

    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub fn physical_name(&self) -> &ObjectPhysicalName {
        &self.physical_name
    }

    pub fn content_hash(&self) -> &ContentHash {
        &self.content_hash
    }

    pub fn wrapped_dek(&self) -> &WrappedObjectDekRecord {
        &self.wrapped_dek
    }
}

#[derive(Debug)]
pub struct CommittedCatalog {
    head: HeadRecord,
    catalog: CatalogGeneration,
}

impl CommittedCatalog {
    pub fn head(&self) -> &HeadRecord {
        &self.head
    }

    pub fn catalog(&self) -> &CatalogGeneration {
        &self.catalog
    }
}

#[derive(Debug)]
pub struct ValidationSnapshot {
    head: HeadRecord,
    catalog: CatalogGeneration,
}

impl ValidationSnapshot {
    pub fn head(&self) -> &HeadRecord {
        &self.head
    }

    pub fn catalog(&self) -> &CatalogGeneration {
        &self.catalog
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InvalidationEvent {
    generation: u64,
    catalog_hash: String,
    required_frk_version: u32,
}

impl InvalidationEvent {
    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub fn catalog_hash(&self) -> &str {
        &self.catalog_hash
    }

    pub const fn required_frk_version(&self) -> u32 {
        self.required_frk_version
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommitOutcome {
    event: InvalidationEvent,
    invalidation_delivered: bool,
}

impl CommitOutcome {
    pub const fn generation(&self) -> u64 {
        self.event.generation
    }

    pub fn catalog_hash(&self) -> &str {
        &self.event.catalog_hash
    }

    pub const fn invalidation_delivered(&self) -> bool {
        self.invalidation_delivered
    }
}

pub struct CoreCommitCoordinator {
    core_id: String,
    core_root: PathBuf,
    catalogs_path: PathBuf,
    objects_path: PathBuf,
    head_path: PathBuf,
    validation_head_path: PathBuf,
    cutover_receipt_path: PathBuf,
    lock_path: PathBuf,
    root_dir: Dir,
    fs_dir: Dir,
    catalogs_dir: Dir,
    objects_dir: Dir,
}

impl fmt::Debug for CoreCommitCoordinator {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CoreCommitCoordinator")
            .field("core_id", &self.core_id)
            .field("core_root", &self.core_root)
            .finish_non_exhaustive()
    }
}

impl CoreCommitCoordinator {
    pub fn new(
        core_root: impl AsRef<Path>,
        core_id: impl Into<String>,
    ) -> Result<Self, CommitError> {
        let core_root = core_root.as_ref();
        ensure_ambient_directory(core_root)?;
        let core_root = fs::canonicalize(core_root)?;
        let root_dir = Dir::open_ambient_dir(&core_root, ambient_authority())?;
        let fs_dir = ensure_child_directory(&root_dir, FS_DIRECTORY)?;
        let catalogs_dir = ensure_child_directory(&fs_dir, CATALOGS_DIRECTORY)?;
        let objects_dir = ensure_child_directory(&root_dir, OBJECTS_DIRECTORY)?;

        let core_id = core_id.into();
        if core_id.is_empty() || core_id.contains(':') {
            return Err(CommitError::InvalidCoreId);
        }
        let fs_path = core_root.join(FS_DIRECTORY);
        let catalogs_path = fs_path.join(CATALOGS_DIRECTORY);
        let objects_path = core_root.join(OBJECTS_DIRECTORY);
        Ok(Self {
            core_id,
            head_path: fs_path.join(HEAD_FILE),
            validation_head_path: fs_path.join(VALIDATION_HEAD_FILE),
            cutover_receipt_path: fs_path.join(CUTOVER_RECEIPT_FILE),
            lock_path: fs_path.join(COMMIT_LOCK_FILE),
            core_root,
            catalogs_path,
            objects_path,
            root_dir,
            fs_dir,
            catalogs_dir,
            objects_dir,
        })
    }

    pub fn head_path(&self) -> &Path {
        &self.head_path
    }

    pub fn validation_head_path(&self) -> &Path {
        &self.validation_head_path
    }

    pub fn cutover_receipt_path(&self) -> &Path {
        &self.cutover_receipt_path
    }

    pub fn catalogs_path(&self) -> &Path {
        &self.catalogs_path
    }

    pub fn objects_path(&self) -> &Path {
        &self.objects_path
    }

    pub fn lock_path(&self) -> &Path {
        &self.lock_path
    }

    pub fn prepare_object_revision<R: Read>(
        &self,
        keys: &FrkSubkeys,
        object_key: &SecretBytes,
        aad: &ObjectBaseAad,
        encrypted_object: &mut R,
    ) -> Result<PreparedObjectRevision, CommitError> {
        self.validate_pinned_layout()?;
        if aad.core_id() != self.core_id {
            return Err(CommitError::InvalidObjectRevision);
        }
        let (mut staged, staged_name) =
            create_temporary_in(&self.objects_dir, OsStr::new("object"))?;
        let result = (|| {
            let (encoded_size, encrypted_hash) =
                copy_bounded(encrypted_object, &mut staged, MAX_ENVELOPE_SIZE)?;
            staged.sync_all()?;
            staged.seek(SeekFrom::Start(0))?;
            let envelope = read_envelope(&mut staged, object_key, aad, &mut io::sink())?;
            let object_id = OpaqueId::parse(&envelope.metadata.object_id)
                .map_err(|_| CommitError::InvalidObjectRevision)?;
            let content_hash = ContentHash::parse(&envelope.metadata.body_sha256)?;
            let key_aad = ObjectKeyAad::from_base(aad.clone(), keys.frk_version())?;
            let wrapped_dek = wrap_object_dek(object_key, keys, &key_aad)?;
            let wrapped_dek = WrappedObjectDekRecord::from_parts(
                keys.frk_version(),
                aad.object_key_epoch(),
                wrapped_dek.algorithm(),
                wrapped_dek.envelope_version(),
                wrapped_dek.nonce(),
                wrapped_dek.ciphertext().to_vec(),
            )?;

            for _ in 0..16 {
                let physical_name = random_object_physical_name()?;
                match publish_staged_immutable_in(
                    &self.objects_dir,
                    &staged,
                    &staged_name,
                    OsStr::new(physical_name.as_str()),
                ) {
                    Ok(()) => {
                        return Ok(PreparedObjectRevision {
                            object_id,
                            revision: envelope.metadata.revision,
                            kind: aad.kind(),
                            object_key_epoch: aad.object_key_epoch(),
                            physical_name,
                            content_hash,
                            encoded_size,
                            encrypted_hash,
                            object_key_binding: object_key_binding(object_key),
                            wrapped_dek,
                        })
                    }
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                    Err(error) => return Err(error.into()),
                }
            }
            Err(CommitError::ObjectNameExhausted)
        })();
        drop(staged);
        if result.is_err() {
            let _ = self.objects_dir.remove_file(&staged_name);
        }
        result
    }

    pub fn load_committed(
        &self,
        keys: &FrkSubkeys,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        self.validate_pinned_layout()?;
        let committed = self.load_committed_once(keys);
        if !matches!(
            &committed,
            Err(CommitError::AuthoritativeHeadMissingAfterCutover)
        ) {
            return committed;
        }

        let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        self.load_committed_once(keys)
    }

    fn load_committed_once(
        &self,
        keys: &FrkSubkeys,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        let committed = self.load_pointer(keys, HEAD_FILE)?;
        let receipt = self.load_pointer(keys, CUTOVER_RECEIPT_FILE)?;
        match (committed, receipt) {
            (None, None) => Ok(None),
            (None, Some(_)) => Err(CommitError::AuthoritativeHeadMissingAfterCutover),
            (Some(_), None) => Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt),
            (Some((head, catalog)), Some((receipt_head, receipt_catalog))) => {
                let receipt_marker = receipt_catalog.cutover_marker();
                let committed_marker = catalog.cutover_marker();
                if receipt_marker.is_none()
                    || committed_marker != receipt_marker
                    || head.generation() < receipt_head.generation()
                    || (head.generation() == receipt_head.generation() && head != receipt_head)
                {
                    return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt);
                }
                Ok(Some(CommittedCatalog { head, catalog }))
            }
        }
    }

    pub fn load_validation_snapshot(
        &self,
        keys: &FrkSubkeys,
    ) -> Result<Option<ValidationSnapshot>, CommitError> {
        self.validate_pinned_layout()?;
        let Some((head, catalog)) = self.load_pointer(keys, VALIDATION_HEAD_FILE)? else {
            return Ok(None);
        };
        Ok(Some(ValidationSnapshot { head, catalog }))
    }

    fn load_pointer(
        &self,
        keys: &FrkSubkeys,
        pointer_name: &str,
    ) -> Result<Option<(HeadRecord, CatalogGeneration)>, CommitError> {
        let encoded_head =
            match read_bounded_in(&self.fs_dir, OsStr::new(pointer_name), MAX_HEAD_SIZE) {
                Ok(encoded) => encoded,
                Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
                Err(error) => return Err(error.into()),
            };
        let head = decode_head(&encoded_head)?;
        let catalog_name = format!(
            "catalog-{:020}-{}.acore",
            head.generation(),
            head.catalog_hash()
        );
        let encrypted_catalog = read_bounded_in(
            &self.catalogs_dir,
            OsStr::new(&catalog_name),
            MAX_CATALOG_ENVELOPE_SIZE,
        )?;
        head.verify_catalog(keys, &self.core_id, &encrypted_catalog)?;
        let catalog = decrypt_catalog_generation(keys, &self.core_id, &encrypted_catalog)?;
        Ok(Some((head, catalog)))
    }

    pub fn initialize_validation_snapshot<B>(
        &self,
        keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        build_next: B,
    ) -> Result<ValidationSnapshot, CommitError>
    where
        B: FnOnce(u64) -> Result<CatalogGeneration, CatalogError>,
    {
        let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        if self.load_committed_once(keys)?.is_some()
            || self.load_validation_snapshot(keys)?.is_some()
        {
            return Err(CommitError::CoreAlreadyInitialized);
        }
        let catalog = build_next(1)?;
        if catalog.generation() != 1 {
            return Err(CommitError::WrongNextGeneration {
                expected: 1,
                actual: catalog.generation(),
            });
        }
        if catalog.cutover_marker().is_some() {
            return Err(CommitError::InvalidCutoverTransition);
        }
        validate_prepared_revisions(
            &self.objects_dir,
            keys,
            &self.core_id,
            None,
            &catalog,
            prepared_revisions,
        )?;
        let (head, _) =
            self.publish_catalog_pointer(keys, &catalog, VALIDATION_HEAD_FILE, false)?;
        Ok(ValidationSnapshot { head, catalog })
    }

    pub fn commit_first_mutation<B, I>(
        &self,
        keys: &FrkSubkeys,
        cutover_epoch: u64,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        build_next: B,
        invalidate: I,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
    {
        self.commit_internal(
            keys,
            prepared_revisions,
            preconditions,
            CommitMode::FirstMutation { cutover_epoch },
            build_next,
            invalidate,
        )
    }

    pub fn commit<B, I>(
        &self,
        keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        build_next: B,
        invalidate: I,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
    {
        self.commit_internal(
            keys,
            prepared_revisions,
            preconditions,
            CommitMode::Normal,
            build_next,
            invalidate,
        )
    }

    fn commit_internal<B, I>(
        &self,
        keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        mode: CommitMode,
        build_next: B,
        invalidate: I,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
    {
        let event = {
            let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
            self.validate_pinned_layout()?;
            let authoritative = self.load_committed_once(keys)?;
            let current = match mode {
                CommitMode::FirstMutation { .. } => {
                    if authoritative.is_some() {
                        return Err(CommitError::CutoverAlreadyCommitted);
                    }
                    let validation = self
                        .load_validation_snapshot(keys)?
                        .ok_or(CommitError::CoreNotInitialized)?;
                    CommittedCatalog {
                        head: validation.head,
                        catalog: validation.catalog,
                    }
                }
                CommitMode::Normal => {
                    if let Some(authoritative) = authoritative {
                        authoritative
                    } else if self.load_validation_snapshot(keys)?.is_some() {
                        return Err(CommitError::CutoverAuthorizationRequired);
                    } else {
                        return Err(CommitError::CoreNotInitialized);
                    }
                }
            };
            mode.validate_current(&current)?;
            validate_preconditions(Some(current.catalog()), preconditions)?;
            let next_generation = current
                .head
                .generation()
                .checked_add(1)
                .ok_or(CommitError::GenerationExhausted)?;
            let next_catalog = build_next(Some(current.catalog()), next_generation)?;
            if next_catalog.generation() != next_generation {
                return Err(CommitError::WrongNextGeneration {
                    expected: next_generation,
                    actual: next_catalog.generation(),
                });
            }
            let next_catalog = mode.apply_cutover_marker(&current, next_catalog)?;
            validate_precondition_coverage(current.catalog(), &next_catalog, preconditions)?;
            validate_prepared_revisions(
                &self.objects_dir,
                keys,
                &self.core_id,
                Some(current.catalog()),
                &next_catalog,
                prepared_revisions,
            )?;
            let (head, _) = self.publish_catalog_pointer(
                keys,
                &next_catalog,
                HEAD_FILE,
                matches!(mode, CommitMode::FirstMutation { .. }),
            )?;
            InvalidationEvent {
                generation: head.generation(),
                catalog_hash: head.catalog_hash().to_owned(),
                required_frk_version: head.required_frk_version(),
            }
        };

        let invalidation_delivered = invalidate(event.clone()).is_ok();
        Ok(CommitOutcome {
            event,
            invalidation_delivered,
        })
    }

    fn publish_catalog_pointer(
        &self,
        keys: &FrkSubkeys,
        catalog: &CatalogGeneration,
        pointer_name: &str,
        publish_cutover_receipt: bool,
    ) -> Result<(HeadRecord, String), CommitError> {
        let encrypted_catalog = encrypt_catalog_generation(keys, &self.core_id, catalog)?;
        let catalog_name = catalog_generation_physical_name(&encrypted_catalog)?;
        self.validate_pinned_layout()?;
        publish_immutable_in(
            &self.catalogs_dir,
            OsStr::new(&catalog_name),
            &encrypted_catalog,
        )?;
        let head = HeadRecord::new_for_catalog(
            keys,
            &self.core_id,
            &encrypted_catalog,
            keys.frk_version(),
        )?;
        let encoded_head = encode_head(&head)?;
        if publish_cutover_receipt {
            self.validate_pinned_layout()?;
            publish_immutable_in(
                &self.fs_dir,
                OsStr::new(CUTOVER_RECEIPT_FILE),
                &encoded_head,
            )?;
        }
        self.validate_pinned_layout()?;
        atomic_publish_in(&self.fs_dir, OsStr::new(pointer_name), &encoded_head)?;
        Ok((head, catalog_name))
    }

    fn validate_pinned_layout(&self) -> Result<(), CommitError> {
        validate_ambient_linked_directory(&self.core_root, &self.root_dir)?;
        validate_linked_directory(&self.root_dir, FS_DIRECTORY, &self.fs_dir)?;
        validate_linked_directory(&self.fs_dir, CATALOGS_DIRECTORY, &self.catalogs_dir)?;
        validate_linked_directory(&self.root_dir, OBJECTS_DIRECTORY, &self.objects_dir)
    }
}

#[derive(Clone, Copy, Debug)]
enum CommitMode {
    FirstMutation { cutover_epoch: u64 },
    Normal,
}

impl CommitMode {
    fn validate_current(&self, current: &CommittedCatalog) -> Result<(), CommitError> {
        match self {
            Self::FirstMutation { .. } if current.catalog.cutover_marker().is_some() => {
                Err(CommitError::CutoverAlreadyCommitted)
            }
            Self::FirstMutation { .. } => Ok(()),
            Self::Normal if current.catalog.cutover_marker().is_none() => {
                Err(CommitError::CutoverAuthorizationRequired)
            }
            Self::Normal => Ok(()),
        }
    }

    fn apply_cutover_marker(
        &self,
        current: &CommittedCatalog,
        next: CatalogGeneration,
    ) -> Result<CatalogGeneration, CommitError> {
        match self {
            Self::FirstMutation { cutover_epoch } => {
                if next.cutover_marker().is_some() {
                    return Err(CommitError::InvalidCutoverTransition);
                }
                Ok(next.with_cutover_marker(CatalogCutoverMarker::new(*cutover_epoch)?)?)
            }
            Self::Normal => {
                if next.cutover_marker().is_some() {
                    return Err(CommitError::InvalidCutoverTransition);
                }
                let marker = current
                    .catalog
                    .cutover_marker()
                    .cloned()
                    .ok_or(CommitError::CutoverAuthorizationRequired)?;
                Ok(next.with_cutover_marker(marker)?)
            }
        }
    }
}

fn validate_preconditions(
    current: Option<&CatalogGeneration>,
    preconditions: &[CatalogPrecondition],
) -> Result<(), CommitError> {
    for precondition in preconditions {
        match precondition {
            CatalogPrecondition::Object(expected) => {
                let stable_id = expected.path.stable_id();
                let actual_path =
                    current.and_then(|catalog| CatalogPathSnapshot::capture(catalog, stable_id));
                let actual_revision = find_entry(current, stable_id)
                    .and_then(CatalogGenerationEntry::object_payload)
                    .map(CatalogObject::revision);
                if actual_path.as_ref() != Some(&expected.path)
                    || actual_revision != Some(expected.revision)
                {
                    return Err(CommitConflict::PathOrRevision {
                        stable_id: stable_id.as_str().to_owned(),
                    }
                    .into());
                }
            }
            CatalogPrecondition::Folder(expected) => {
                let stable_id = expected.path.stable_id();
                let actual_path =
                    current.and_then(|catalog| CatalogPathSnapshot::capture(catalog, stable_id));
                if actual_path.as_ref() != Some(&expected.path)
                    || actual_path
                        .as_ref()
                        .map_or(true, |path| !path.leaf_is_folder())
                {
                    return Err(CommitConflict::PathOrRevision {
                        stable_id: stable_id.as_str().to_owned(),
                    }
                    .into());
                }
            }
            CatalogPrecondition::Vacant(expected) => {
                let parent_id = expected.parent_path.stable_id();
                let actual_parent =
                    current.and_then(|catalog| CatalogPathSnapshot::capture(catalog, parent_id));
                if actual_parent.as_ref() != Some(&expected.parent_path)
                    || actual_parent
                        .as_ref()
                        .map_or(true, |path| !path.leaf_is_folder())
                {
                    return Err(CommitConflict::InvalidDestinationParent {
                        parent_id: parent_id.as_str().to_owned(),
                    }
                    .into());
                }
                if current.is_some_and(|catalog| {
                    catalog.entries().iter().any(|entry| {
                        entry.parent_id() == Some(parent_id) && entry.name() == &expected.name
                    })
                }) {
                    return Err(CommitConflict::DestinationOccupied {
                        parent_id: parent_id.as_str().to_owned(),
                        name: expected.name.as_str().to_owned(),
                    }
                    .into());
                }
            }
        }
    }
    Ok(())
}

fn validate_precondition_coverage(
    current: &CatalogGeneration,
    next: &CatalogGeneration,
    preconditions: &[CatalogPrecondition],
) -> Result<(), CommitError> {
    let current_by_id: HashMap<_, _> = current
        .entries()
        .iter()
        .map(|entry| (entry.stable_id().as_str(), entry))
        .collect();
    let next_by_id: HashMap<_, _> = next
        .entries()
        .iter()
        .map(|entry| (entry.stable_id().as_str(), entry))
        .collect();

    for entry in current.entries() {
        if next_by_id
            .get(entry.stable_id().as_str())
            .is_some_and(|next_entry| *next_entry == entry)
        {
            continue;
        }
        if !preconditions
            .iter()
            .any(|precondition| precondition_covers_source(precondition, entry))
        {
            return Err(CommitConflict::MissingSourcePrecondition {
                stable_id: entry.stable_id().as_str().to_owned(),
            }
            .into());
        }
    }

    for entry in next.entries() {
        let moved_or_created =
            current_by_id
                .get(entry.stable_id().as_str())
                .map_or(true, |current_entry| {
                    current_entry.parent_id() != entry.parent_id()
                        || current_entry.name() != entry.name()
                });
        if !moved_or_created {
            continue;
        }
        let Some(parent_id) = entry.parent_id() else {
            continue;
        };
        if !current_by_id.contains_key(parent_id.as_str()) {
            continue;
        }
        if !preconditions.iter().any(|precondition| {
            matches!(precondition,
                CatalogPrecondition::Vacant(expected)
                    if expected.parent_path.stable_id() == parent_id
                        && expected.name == *entry.name()
            )
        }) {
            return Err(CommitConflict::MissingDestinationPrecondition {
                parent_id: parent_id.as_str().to_owned(),
                name: entry.name().as_str().to_owned(),
            }
            .into());
        }
    }
    Ok(())
}

fn precondition_covers_source(
    precondition: &CatalogPrecondition,
    entry: &CatalogGenerationEntry,
) -> bool {
    match (precondition, entry.object_payload().is_some()) {
        (CatalogPrecondition::Object(expected), true) => {
            expected.path.stable_id() == entry.stable_id()
        }
        (CatalogPrecondition::Folder(expected), false) => {
            expected.path.stable_id() == entry.stable_id()
        }
        (CatalogPrecondition::Object(_), false)
        | (CatalogPrecondition::Folder(_), true)
        | (CatalogPrecondition::Vacant(_), _) => false,
    }
}

fn validate_prepared_revisions(
    objects_dir: &Dir,
    keys: &FrkSubkeys,
    core_id: &str,
    current: Option<&CatalogGeneration>,
    next: &CatalogGeneration,
    prepared: &[PreparedObjectRevision],
) -> Result<(), CommitError> {
    let mut by_physical_name = HashMap::new();
    for value in prepared {
        if by_physical_name
            .insert(value.physical_name.as_str(), value)
            .is_some()
        {
            return Err(CommitError::DuplicatePreparedRevision {
                physical_name: value.physical_name.as_str().to_owned(),
            });
        }
    }

    let current_objects: HashMap<_, _> = current
        .into_iter()
        .flat_map(CatalogGeneration::entries)
        .filter_map(|entry| {
            entry
                .object_payload()
                .map(|object| (entry.stable_id().as_str(), object))
        })
        .collect();
    let mut consumed = HashSet::new();

    for entry in next.entries() {
        let Some(object) = entry.object_payload() else {
            continue;
        };
        let next_key_binding =
            catalog_object_key_binding(keys, core_id, entry.stable_id(), object)?;
        let unchanged = current_objects
            .get(entry.stable_id().as_str())
            .is_some_and(|current| same_object_body(current, object));
        if unchanged {
            let current_object = current_objects[entry.stable_id().as_str()];
            let current_key_binding =
                catalog_object_key_binding(keys, core_id, entry.stable_id(), current_object)?;
            if current_key_binding != next_key_binding {
                return Err(CommitError::ReferencedObjectKeyMismatch {
                    stable_id: entry.stable_id().as_str().to_owned(),
                });
            }
            validate_existing_object_file(objects_dir, object.physical_name())?;
            continue;
        }

        let Some(token) = by_physical_name.get(object.physical_name().as_str()) else {
            return Err(CommitError::MissingPreparedRevision {
                stable_id: entry.stable_id().as_str().to_owned(),
                revision: object.revision(),
            });
        };
        if token.object_id != *entry.stable_id()
            || token.revision != object.revision()
            || token.kind != object.kind()
            || token.object_key_epoch != object.object_key_epoch()
            || token.content_hash != *object.content_hash()
            || token.object_key_binding != next_key_binding
            || token.wrapped_dek != *object.wrapped_dek()
        {
            return Err(CommitError::PreparedRevisionMismatch {
                stable_id: entry.stable_id().as_str().to_owned(),
                physical_name: object.physical_name().as_str().to_owned(),
            });
        }
        validate_prepared_file(objects_dir, token)?;
        consumed.insert(token.physical_name.as_str());
    }

    if let Some(unused) = prepared
        .iter()
        .find(|value| !consumed.contains(value.physical_name.as_str()))
    {
        return Err(CommitError::UnusedPreparedRevision {
            physical_name: unused.physical_name.as_str().to_owned(),
        });
    }
    Ok(())
}

fn same_object_body(current: &CatalogObject, next: &CatalogObject) -> bool {
    current.revision() == next.revision()
        && current.physical_name() == next.physical_name()
        && current.content_hash() == next.content_hash()
        && current.kind() == next.kind()
        && current.object_key_epoch() == next.object_key_epoch()
        && current.wrapped_dek() == next.wrapped_dek()
}

fn catalog_object_key_binding(
    keys: &FrkSubkeys,
    core_id: &str,
    stable_id: &OpaqueId,
    object: &CatalogObject,
) -> Result<[u8; 32], CommitError> {
    let record = object.wrapped_dek();
    let aad = ObjectKeyAad::new(
        core_id,
        stable_id.as_str(),
        object.revision(),
        object.kind(),
        OBJECT_KEY_ENVELOPE_VERSION,
        object.object_key_epoch(),
        record.frk_version(),
    )?;
    let wrapped = record.to_wrapped_object_dek()?;
    let object_key = unwrap_object_dek(keys, &wrapped, &aad)?;
    Ok(object_key_binding(&object_key))
}

fn object_key_binding(object_key: &SecretBytes) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(b"anima-corefs-prepared-object-key-binding-v1\0");
    hasher.update(object_key.as_slice());
    hasher.finalize().into()
}

fn validate_existing_object_file(
    objects_dir: &Dir,
    physical_name: &ObjectPhysicalName,
) -> Result<(), CommitError> {
    let file = open_regular_file_in(objects_dir, OsStr::new(physical_name.as_str()))?;
    if file.metadata()?.len() == 0 {
        return Err(CommitError::ReferencedObjectMissing {
            physical_name: physical_name.as_str().to_owned(),
        });
    }
    Ok(())
}

fn validate_prepared_file(
    objects_dir: &Dir,
    prepared: &PreparedObjectRevision,
) -> Result<(), CommitError> {
    let mut file = open_regular_file_in(objects_dir, OsStr::new(prepared.physical_name.as_str()))
        .map_err(|_| CommitError::PreparedRevisionCorrupt {
        physical_name: prepared.physical_name.as_str().to_owned(),
    })?;
    if file.metadata()?.len() != prepared.encoded_size {
        return Err(CommitError::PreparedRevisionCorrupt {
            physical_name: prepared.physical_name.as_str().to_owned(),
        });
    }
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; COPY_BUFFER_SIZE];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    let actual: [u8; 32] = hasher.finalize().into();
    if actual != prepared.encrypted_hash {
        return Err(CommitError::PreparedRevisionCorrupt {
            physical_name: prepared.physical_name.as_str().to_owned(),
        });
    }
    Ok(())
}

fn find_entry<'a>(
    current: Option<&'a CatalogGeneration>,
    stable_id: &OpaqueId,
) -> Option<&'a CatalogGenerationEntry> {
    current?
        .entries()
        .iter()
        .find(|entry| entry.stable_id() == stable_id)
}

fn ensure_ambient_directory(path: &Path) -> Result<(), CommitError> {
    let path = if path.as_os_str().is_empty() {
        Path::new(".")
    } else {
        path
    };
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
                return Err(CommitError::InvalidCoreLayout);
            }
            return Ok(());
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }

    let parent = path.parent().ok_or(CommitError::InvalidCoreLayout)?;
    let parent = if parent.as_os_str().is_empty() {
        Path::new(".")
    } else {
        parent
    };
    ensure_ambient_directory(parent)?;
    let parent_dir = Dir::open_ambient_dir(parent, ambient_authority())?;
    let name = path.file_name().ok_or(CommitError::InvalidCoreLayout)?;
    match durable_create_directory_in(&parent_dir, name) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    let metadata = parent_dir.symlink_metadata(name)?;
    if !metadata.is_dir() || metadata.is_symlink() {
        return Err(CommitError::InvalidCoreLayout);
    }
    Ok(())
}

fn ensure_child_directory(parent: &Dir, name: &str) -> Result<Dir, CommitError> {
    ensure_child_directory_with(parent, name, durable_create_directory_in)
}

fn ensure_child_directory_with<F>(
    parent: &Dir,
    name: &str,
    create_directory: F,
) -> Result<Dir, CommitError>
where
    F: FnOnce(&Dir, &OsStr) -> io::Result<()>,
{
    match parent.symlink_metadata(name) {
        Ok(metadata) => {
            if !metadata.is_dir() || metadata.is_symlink() {
                return Err(CommitError::InvalidCoreLayout);
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            match create_directory(parent, OsStr::new(name)) {
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                Err(error) => return Err(error.into()),
            }
        }
        Err(error) => return Err(error.into()),
    }
    let dir = parent.open_dir(name)?;
    let opened = Metadata::from_file(&dir.try_clone()?.into_std_file())?;
    let linked = parent.symlink_metadata(name)?;
    if !linked.is_dir() || linked.is_symlink() || !same_file_identity(&opened, &linked) {
        return Err(CommitError::InvalidCoreLayout);
    }
    Ok(dir)
}

fn validate_linked_directory(
    parent: &Dir,
    name: &str,
    opened_dir: &Dir,
) -> Result<(), CommitError> {
    let opened = Metadata::from_file(&opened_dir.try_clone()?.into_std_file())?;
    let linked = parent.symlink_metadata(name)?;
    if !opened.is_dir()
        || !linked.is_dir()
        || linked.is_symlink()
        || !same_file_identity(&opened, &linked)
    {
        return Err(CommitError::InvalidCoreLayout);
    }
    Ok(())
}

fn validate_ambient_linked_directory(path: &Path, opened_dir: &Dir) -> Result<(), CommitError> {
    let opened = Metadata::from_file(&opened_dir.try_clone()?.into_std_file())?;
    let linked = match (path.parent(), path.file_name()) {
        (Some(parent), Some(name)) => {
            let parent = Dir::open_ambient_dir(parent, ambient_authority())?;
            parent.symlink_metadata(Path::new(name))?
        }
        _ => {
            let linked = Dir::open_ambient_dir(path, ambient_authority())?;
            Metadata::from_file(&linked.into_std_file())?
        }
    };
    if !opened.is_dir()
        || !linked.is_dir()
        || linked.is_symlink()
        || !same_file_identity(&opened, &linked)
    {
        return Err(CommitError::InvalidCoreLayout);
    }
    Ok(())
}

fn reject_symlink_in(dir: &Dir, name: &OsStr) -> Result<(), CommitError> {
    match dir.symlink_metadata(name) {
        Ok(metadata) if metadata.is_symlink() => Err(CommitError::InvalidCoreLayout),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn open_regular_file_in(dir: &Dir, name: &OsStr) -> io::Result<File> {
    let file = dir.open(name)?.into_std();
    validate_opened_regular_file(dir, name, &file).map_err(|error| match error {
        CommitError::Io(error) => error,
        other => io::Error::new(io::ErrorKind::InvalidData, other.to_string()),
    })?;
    Ok(file)
}

fn validate_opened_regular_file(dir: &Dir, name: &OsStr, file: &File) -> Result<(), CommitError> {
    let opened = Metadata::from_file(file)?;
    let linked = dir.symlink_metadata(name)?;
    if !opened.is_file()
        || !linked.is_file()
        || linked.is_symlink()
        || !same_file_identity(&opened, &linked)
    {
        return Err(CommitError::InvalidCoreLayout);
    }
    match link_count(&opened) {
        Some(1) => {}
        #[cfg(any(unix, test))]
        Some(2) if has_known_crash_stale_immutable_stage(dir, name, &opened)? => {}
        _ => return Err(CommitError::InvalidCoreLayout),
    }
    Ok(())
}

#[cfg(any(unix, test))]
fn has_known_crash_stale_immutable_stage(
    dir: &Dir,
    target: &OsStr,
    target_metadata: &Metadata,
) -> Result<bool, CommitError> {
    let Some(staging_target) = immutable_staging_target(target) else {
        return Ok(false);
    };
    let mut matching_aliases = 0_u8;
    for entry in dir.entries()? {
        let candidate = entry?.file_name();
        if !is_temporary_name_for_target(&candidate, staging_target) {
            continue;
        }
        let candidate_metadata = dir.symlink_metadata(&candidate)?;
        if candidate_metadata.is_file()
            && !candidate_metadata.is_symlink()
            && same_file_identity(target_metadata, &candidate_metadata)
        {
            matching_aliases = matching_aliases.saturating_add(1);
        }
    }
    Ok(matching_aliases == 1)
}

#[cfg(any(unix, test))]
fn immutable_staging_target(target: &OsStr) -> Option<&OsStr> {
    let target_text = target.to_str()?;
    if target_text == CUTOVER_RECEIPT_FILE || is_catalog_physical_name(target_text) {
        return Some(target);
    }
    ObjectPhysicalName::parse(target_text)
        .ok()
        .map(|_| OsStr::new("object"))
}

#[cfg(any(unix, test))]
fn is_catalog_physical_name(value: &str) -> bool {
    let Some((generation, hash)) = value
        .strip_prefix("catalog-")
        .and_then(|value| value.strip_suffix(".acore"))
        .and_then(|value| value.split_once('-'))
    else {
        return false;
    };
    generation.len() == 20
        && generation.bytes().all(|byte| byte.is_ascii_digit())
        && generation.parse::<u64>().is_ok_and(|value| value > 0)
        && hash.len() == 64
        && hash
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn same_file_identity(left: &Metadata, right: &Metadata) -> bool {
    left.dev() == right.dev() && left.ino() == right.ino()
}

fn link_count(metadata: &Metadata) -> Option<u64> {
    Some(metadata.nlink())
}

fn read_bounded_in(dir: &Dir, name: &OsStr, limit: usize) -> io::Result<Vec<u8>> {
    let file = open_regular_file_in(dir, name)?;
    let length = file.metadata()?.len();
    if length > limit as u64 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "CoreFS file exceeds its encoded size limit",
        ));
    }
    let mut encoded = Vec::with_capacity(length as usize);
    file.take(limit as u64 + 1).read_to_end(&mut encoded)?;
    if encoded.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "CoreFS file exceeds its encoded size limit",
        ));
    }
    Ok(encoded)
}

fn copy_bounded<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
    limit: u64,
) -> Result<(u64, [u8; 32]), CommitError> {
    let mut buffer = vec![0_u8; COPY_BUFFER_SIZE];
    let mut total = 0_u64;
    let mut hasher = Sha256::new();
    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        total = total
            .checked_add(count as u64)
            .ok_or(CommitError::InvalidObjectRevision)?;
        if total > limit {
            return Err(CommitError::ObjectEnvelopeTooLarge);
        }
        writer.write_all(&buffer[..count])?;
        hasher.update(&buffer[..count]);
    }
    if total == 0 {
        return Err(CommitError::InvalidObjectRevision);
    }
    Ok((total, hasher.finalize().into()))
}

fn random_object_physical_name() -> Result<ObjectPhysicalName, CommitError> {
    let mut random = [0_u8; 16];
    getrandom(&mut random).map_err(io::Error::other)?;
    Ok(ObjectPhysicalName::parse(&format!(
        "object-{}.acore",
        hex_bytes(&random)
    ))?)
}

fn hex_bytes(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum CommitConflict {
    #[error("catalog path or revision changed for {stable_id}")]
    PathOrRevision { stable_id: String },
    #[error("catalog destination is occupied: parent={parent_id}, name={name}")]
    DestinationOccupied { parent_id: String, name: String },
    #[error("catalog destination parent is missing or not a folder: {parent_id}")]
    InvalidDestinationParent { parent_id: String },
    #[error("changed catalog source is missing a precondition: {stable_id}")]
    MissingSourcePrecondition { stable_id: String },
    #[error("new catalog destination is missing a vacancy precondition: parent={parent_id}, name={name}")]
    MissingDestinationPrecondition { parent_id: String, name: String },
}

#[derive(Debug, thiserror::Error)]
pub enum CommitError {
    #[error("CoreFS commit lock is already held")]
    LockBusy,
    #[error("recorded CoreFS lock owner is still alive: pid={pid}, start={process_start_time}")]
    RecordedOwnerAlive { pid: u32, process_start_time: u64 },
    #[error("invalid CoreFS lock metadata")]
    InvalidLockMetadata,
    #[error("process inspection failed: {0}")]
    ProcessInspection(String),
    #[error("invalid CoreFS layout")]
    InvalidCoreLayout,
    #[error("invalid Core ID")]
    InvalidCoreId,
    #[error("prepared object revision is invalid")]
    InvalidObjectRevision,
    #[error("prepared object envelope exceeds the CoreFS maximum")]
    ObjectEnvelopeTooLarge,
    #[error("could not allocate an opaque object revision name")]
    ObjectNameExhausted,
    #[error("CoreFS generation is exhausted")]
    GenerationExhausted,
    #[error("CoreFS validation snapshot is already initialized")]
    CoreAlreadyInitialized,
    #[error("CoreFS validation snapshot is not initialized")]
    CoreNotInitialized,
    #[error("authoritative CoreFS HEAD is missing after irreversible cutover")]
    AuthoritativeHeadMissingAfterCutover,
    #[error("authoritative CoreFS HEAD violates the irreversible cutover receipt")]
    AuthoritativeHeadViolatesCutoverReceipt,
    #[error("the first CoreFS mutation requires explicit cutover authorization")]
    CutoverAuthorizationRequired,
    #[error("the irreversible CoreFS cutover has already committed")]
    CutoverAlreadyCommitted,
    #[error("invalid CoreFS cutover marker transition")]
    InvalidCutoverTransition,
    #[error("next catalog generation mismatch: expected {expected}, got {actual}")]
    WrongNextGeneration { expected: u64, actual: u64 },
    #[error("missing prepared object revision for {stable_id} revision {revision}")]
    MissingPreparedRevision { stable_id: String, revision: u64 },
    #[error("prepared object revision does not match catalog object {stable_id}: {physical_name}")]
    PreparedRevisionMismatch {
        stable_id: String,
        physical_name: String,
    },
    #[error("prepared object revision changed after validation: {physical_name}")]
    PreparedRevisionCorrupt { physical_name: String },
    #[error("prepared object revision was supplied more than once: {physical_name}")]
    DuplicatePreparedRevision { physical_name: String },
    #[error("prepared object revision is not referenced by the next catalog: {physical_name}")]
    UnusedPreparedRevision { physical_name: String },
    #[error("catalog references a missing object revision: {physical_name}")]
    ReferencedObjectMissing { physical_name: String },
    #[error("catalog wrapper no longer resolves to the referenced object key: {stable_id}")]
    ReferencedObjectKeyMismatch { stable_id: String },
    #[error("CoreFS commit conflict: {0}")]
    Conflict(#[from] CommitConflict),
    #[error("CoreFS I/O failed: {0}")]
    Io(#[from] io::Error),
    #[error("CoreFS object envelope failed: {0}")]
    Envelope(#[from] EnvelopeError),
    #[error("CoreFS catalog failed: {0}")]
    Catalog(#[from] CatalogError),
    #[error("CoreFS HEAD failed: {0}")]
    Head(#[from] HeadError),
    #[error("CoreFS cryptography failed: {0}")]
    Crypto(#[from] CryptoError),
}

#[cfg(test)]
mod tests {
    use std::io::{self, Cursor};

    use cap_std::{ambient_authority, fs::Dir};

    use crate::catalog::{CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry};
    use crate::crypto::{derive_corefs_subkeys, SecretBytes};
    use crate::folders::{FolderOwner, PortableName};
    use crate::id::OpaqueId;
    use crate::policy::AnimaAccess;
    use crate::publication::durable_create_directory_in;

    use super::{copy_bounded, ensure_child_directory_with, CommitError, CoreCommitCoordinator};

    #[test]
    fn streaming_preparation_stops_before_writing_past_its_bound() {
        let mut output = Vec::new();
        let error = copy_bounded(&mut Cursor::new(b"four"), &mut output, 3).unwrap_err();
        assert!(matches!(error, CommitError::ObjectEnvelopeTooLarge));
        assert!(output.is_empty());
    }

    #[test]
    fn commit_lock_files_use_owner_only_unix_permissions() {
        assert_eq!(super::COMMIT_LOCK_FILE_MODE, 0o600);
    }

    #[test]
    fn linux_process_stat_parser_reads_a_live_process_start_identity() {
        let mut fields = vec!["0"; 20];
        fields[0] = "S";
        fields[19] = "4242";
        let stat = format!("123 (worker with ) parenthesis) {}", fields.join(" "));

        assert_eq!(
            super::parse_linux_process_stat(123, &stat).unwrap(),
            Some(4242)
        );
    }

    #[test]
    fn linux_process_stat_parser_treats_a_zombie_as_absent() {
        let mut fields = vec!["0"; 20];
        fields[0] = "Z";
        fields[19] = "4242";
        let stat = format!("123 (worker) {}", fields.join(" "));

        assert_eq!(super::parse_linux_process_stat(123, &stat).unwrap(), None);
    }

    #[test]
    fn linux_process_stat_parser_treats_dead_states_as_absent() {
        for state in ["X", "x"] {
            let mut fields = vec!["0"; 20];
            fields[0] = state;
            fields[19] = "4242";
            let stat = format!("123 (worker) {}", fields.join(" "));

            assert_eq!(super::parse_linux_process_stat(123, &stat).unwrap(), None);
        }
    }

    #[test]
    fn child_directory_creation_tolerates_a_concurrent_winner() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-child-directory-race-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let parent = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();

        let child = ensure_child_directory_with(&parent, "fs", |parent, name| {
            durable_create_directory_in(parent, name)?;
            Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "simulated concurrent directory winner",
            ))
        })
        .unwrap();

        assert!(child.metadata(".").unwrap().is_dir());
        drop(child);
        drop(parent);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn immutable_file_validation_tolerates_a_known_crash_stale_stage_alias() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-crash-stale-stage-alias-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let target = concat!(
            "catalog-00000000000000000001-",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.acore"
        );
        let stale_stage = format!(".{target}.17.tmp");
        std::fs::write(root.join(target), b"catalog").unwrap();
        std::fs::hard_link(root.join(target), root.join(&stale_stage)).unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        let file = dir.open(target).unwrap().into_std();

        super::validate_opened_regular_file(&dir, target.as_ref(), &file).unwrap();

        drop(file);
        drop(dir);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn immutable_file_validation_rejects_an_unrecognized_extra_hard_link() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-unrecognized-extra-link-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let target = concat!(
            "catalog-00000000000000000001-",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.acore"
        );
        std::fs::write(root.join(target), b"catalog").unwrap();
        std::fs::hard_link(root.join(target), root.join("unexpected-link")).unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        let file = dir.open(target).unwrap().into_std();

        let error = super::validate_opened_regular_file(&dir, target.as_ref(), &file).unwrap_err();
        assert!(matches!(error, CommitError::InvalidCoreLayout));

        drop(file);
        drop(dir);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn mutable_pointer_validation_rejects_a_stage_shaped_extra_hard_link() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-mutable-stage-shaped-link-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("HEAD"), b"head").unwrap();
        std::fs::hard_link(root.join("HEAD"), root.join(".HEAD.17.tmp")).unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        let file = dir.open("HEAD").unwrap().into_std();

        let error = super::validate_opened_regular_file(&dir, "HEAD".as_ref(), &file).unwrap_err();
        assert!(matches!(error, CommitError::InvalidCoreLayout));

        drop(file);
        drop(dir);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn pinned_root_must_still_match_the_core_root_path() {
        let pinned_root =
            std::env::temp_dir().join(format!("anima-corefs-pinned-root-{}", std::process::id()));
        let replacement_root = std::env::temp_dir().join(format!(
            "anima-corefs-replacement-root-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&pinned_root);
        let _ = std::fs::remove_dir_all(&replacement_root);
        std::fs::create_dir_all(&replacement_root).unwrap();
        let mut coordinator = CoreCommitCoordinator::new(&pinned_root, "core-a").unwrap();
        coordinator.core_root = std::fs::canonicalize(&replacement_root).unwrap();

        let error = coordinator.validate_pinned_layout().unwrap_err();

        assert!(matches!(error, CommitError::InvalidCoreLayout));
        drop(coordinator);
        std::fs::remove_dir_all(pinned_root).unwrap();
        std::fs::remove_dir_all(replacement_root).unwrap();
    }

    #[test]
    fn catalog_publication_revalidates_the_pinned_root() {
        let pinned_root = std::env::temp_dir().join(format!(
            "anima-corefs-publish-pinned-root-{}",
            std::process::id()
        ));
        let replacement_root = std::env::temp_dir().join(format!(
            "anima-corefs-publish-replacement-root-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&pinned_root);
        let _ = std::fs::remove_dir_all(&replacement_root);
        std::fs::create_dir_all(&replacement_root).unwrap();
        let mut coordinator = CoreCommitCoordinator::new(&pinned_root, "core-a").unwrap();
        coordinator.core_root = std::fs::canonicalize(&replacement_root).unwrap();
        let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), 1).unwrap();
        let catalog = CatalogGeneration::new(
            1,
            vec![CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                OpaqueId::parse("01J00000000000000000000000").unwrap(),
                None,
                PortableName::parse("Core").unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            ))],
        )
        .unwrap();

        let error = coordinator
            .publish_catalog_pointer(&keys, &catalog, "VALIDATION_HEAD", false)
            .unwrap_err();

        assert!(matches!(error, CommitError::InvalidCoreLayout));
        assert!(!pinned_root.join("fs").join("VALIDATION_HEAD").exists());
        drop(coordinator);
        std::fs::remove_dir_all(pinned_root).unwrap();
        std::fs::remove_dir_all(replacement_root).unwrap();
    }
}
