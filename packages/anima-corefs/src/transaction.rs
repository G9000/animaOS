//! Core-wide atomic catalog commit coordination.

#[cfg_attr(not(test), allow(dead_code))]
mod cache;
// The lease contract is integrated incrementally by the following implementation tasks.
#[allow(dead_code)]
mod object_lease;

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::ffi::OsStr;
use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom, Write};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt as _;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, ThreadId};
use std::time::{Duration, Instant};

use cap_fs_ext::MetadataExt as _;
use cap_std::ambient_authority;
#[cfg(unix)]
use cap_std::fs::OpenOptionsExt as _;
#[cfg(windows)]
use cap_std::fs::OpenOptionsExt as _;
use cap_std::fs::{Dir, File as CapFile, Metadata, OpenOptions};
use fs4::FileExt;
use getrandom::getrandom;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{FILE_SHARE_DELETE, FILE_SHARE_READ};

use self::cache::{
    AuthenticatedCommitSnapshot, CacheLookupKey, CommitCache, PointerSet, ValidatedObjectBinding,
    ValidatedObjectState,
};
use self::object_lease::global_lease_budget;
pub(crate) use self::object_lease::ObjectLeaseDiagnosticBoundaryEvent;
use self::object_lease::{
    object_set_fingerprint, DirectoryIdentity, FenceOutcome, LeaseAttemptDecision,
    LeaseAttemptPolicy, LeaseBudget, LeaseResourceFactory, MonotonicClock, ObjectLeaseCandidate,
    ObjectLeaseDiagnosticObserver, ObjectValidationLease, OptimizationMiss,
};
#[cfg(windows)]
pub(crate) use self::object_lease::{
    ObjectLeaseDiagnosticCounterSnapshot, ObjectLeaseTeardownObservation,
};

#[cfg(test)]
use crate::catalog::encrypt_catalog_generation_for_publication_with_observer;
use crate::catalog::{
    encrypt_catalog_generation_for_publication, CatalogCutoverMarker, CatalogError,
    CatalogGeneration, CatalogGenerationEntry, CatalogObject, ContentHash, ObjectLifecycle,
    ObjectPhysicalName, WrappedObjectDekRecord, MAX_CATALOG_ENVELOPE_SIZE,
};
use crate::crypto::{
    generate_object_dek, unwrap_object_dek, wrap_object_dek, CryptoError, ObjectKeyAad,
    OBJECT_KEY_ENVELOPE_VERSION,
};
use crate::crypto::{FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes};
use crate::envelope::{
    read_envelope, rotate_object_key_envelope, EnvelopeError, MAX_ENVELOPE_SIZE,
};
use crate::folders::PortableName;
use crate::head::{decode_head, encode_head, HeadError, HeadRecord, MAX_HEAD_SIZE};
use crate::id::OpaqueId;
#[cfg(any(unix, test))]
use crate::publication::is_temporary_name_for_target;
use crate::publication::{
    atomic_publish_in_with_hook, create_temporary_in, durable_create_directory_in,
    publish_immutable_in_with_hook, publish_staged_immutable_in_with_hook, PublicationPhase,
};
use crate::rotation::{FrkKeyring, RotationError};

const LOCK_SCHEMA_VERSION: u16 = 1;
const MAX_LOCK_METADATA_SIZE: usize = 4096;
const COPY_BUFFER_SIZE: usize = 1024 * 1024;
const FS_DIRECTORY: &str = "fs";
const CATALOGS_DIRECTORY: &str = "catalogs";
const OBJECTS_DIRECTORY: &str = "objects";
const HEAD_FILE: &str = "HEAD";
const VALIDATION_HEAD_FILE: &str = "VALIDATION_HEAD";
const CUTOVER_RECEIPT_FILE: &str = "CUTOVER_RECEIPT";
const CUTOVER_COMPLETE_FILE: &str = "CUTOVER_COMPLETE";
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
    acquired_at: Instant,
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
        Self::acquire_in_with_post_kernel_lock_hook(root_dir, fs_dir, || {})
    }

    fn acquire_in_with_post_kernel_lock_hook<F>(
        root_dir: &Dir,
        fs_dir: &Dir,
        post_kernel_lock: F,
    ) -> Result<Self, CommitError>
    where
        F: FnOnce(),
    {
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
        // cap-std uses O_PATH for Dir on Linux, which cannot be locked.
        let anchor = root_dir.open(".")?.into_std();
        #[cfg(windows)]
        let anchor = file.try_clone()?;
        anchor.try_lock_exclusive().map_err(|error| {
            if is_lock_contention_error(&error) {
                CommitError::LockBusy
            } else {
                CommitError::Io(error)
            }
        })?;
        let acquired_at = Instant::now();
        post_kernel_lock();

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
            acquired_at,
            identity,
            _fs_dir: fs_dir,
        })
    }

    fn acquired_at(&self) -> Instant {
        self.acquired_at
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

fn measure_lock_hold_through_release(lock_started: Instant, release: impl FnOnce()) -> Duration {
    release();
    lock_started.elapsed()
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
        if catalog
            .entries()
            .iter()
            .any(|entry| entry.parent_id() == Some(parent_id) && entry.name() == &name)
        {
            return Err(CommitConflict::DestinationOccupied {
                parent_id: parent_id.as_str().to_owned(),
                name: name.as_str().to_owned(),
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

    pub const fn object_key_epoch(&self) -> u32 {
        self.object_key_epoch
    }
}

#[derive(Debug)]
pub struct CommittedCatalog {
    head: HeadRecord,
    catalog: Arc<CatalogGeneration>,
}

impl CommittedCatalog {
    pub fn head(&self) -> &HeadRecord {
        &self.head
    }

    pub fn catalog(&self) -> &CatalogGeneration {
        self.catalog.as_ref()
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
    recovery_pending: bool,
    lock_hold_duration: Duration,
    bytes_written: u64,
    catalog_plaintext_bytes: usize,
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

    pub const fn recovery_pending(&self) -> bool {
        self.recovery_pending
    }

    /// Time spent inside the kernel-backed Core-wide commit lock.
    pub const fn lock_hold_duration(&self) -> Duration {
        self.lock_hold_duration
    }

    /// Catalog, pointer, and cutover-marker payload bytes durably written by this commit.
    pub const fn bytes_written(&self) -> u64 {
        self.bytes_written
    }

    /// Plaintext bytes emitted by the production catalog serialization used by this commit.
    pub const fn catalog_plaintext_bytes(&self) -> usize {
        self.catalog_plaintext_bytes
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PublicationTarget {
    Object,
    Catalog,
    CutoverReceipt,
    CutoverComplete,
    AuthoritativeHead,
    ValidationHead,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CommitFailurePoint {
    Publication {
        target: PublicationTarget,
        phase: PublicationPhase,
    },
    BeforeInvalidation,
    AfterInvalidation,
}

struct CommitCallbacks<'a, I, H> {
    invalidate: I,
    hook: &'a mut H,
}

#[derive(Default)]
struct DeferredLeaseTeardown {
    cache_snapshots: Vec<Arc<AuthenticatedCommitSnapshot>>,
    candidates: Vec<ObjectLeaseCandidate>,
    leases: Vec<Arc<ObjectValidationLease>>,
}

impl DeferredLeaseTeardown {
    fn cache_snapshots(&mut self) -> &mut Vec<Arc<AuthenticatedCommitSnapshot>> {
        &mut self.cache_snapshots
    }

    fn retain_snapshot_lease_owner(&mut self, snapshot: Option<&Arc<AuthenticatedCommitSnapshot>>) {
        let Some(snapshot) = snapshot.filter(|snapshot| snapshot.object_lease.is_some()) else {
            return;
        };
        if self
            .cache_snapshots
            .iter()
            .any(|retained| Arc::ptr_eq(retained, snapshot))
        {
            return;
        }
        self.cache_snapshots.push(Arc::clone(snapshot));
    }

    fn retain_current_cache_lease_owner(&mut self, cache: &CommitCache) {
        let snapshot = cache.current_deferred(&mut self.cache_snapshots);
        self.retain_snapshot_lease_owner(snapshot.as_ref());
    }

    fn retain_candidate(&mut self, candidate: ObjectLeaseCandidate) {
        self.candidates.push(candidate);
    }

    fn retain_lease(&mut self, lease: &Arc<ObjectValidationLease>) {
        self.leases.push(Arc::clone(lease));
    }
}

struct RotationCacheKeyMaterial<'a, 'keys> {
    keyring: &'a FrkKeyring<'keys>,
    pending: &'a FrkSubkeys,
}

struct DeferredCandidateGuard<'a> {
    candidate: Option<ObjectLeaseCandidate>,
    deferred_teardown: &'a mut DeferredLeaseTeardown,
}

impl<'a> DeferredCandidateGuard<'a> {
    fn new(
        candidate: ObjectLeaseCandidate,
        deferred_teardown: &'a mut DeferredLeaseTeardown,
    ) -> Self {
        Self {
            candidate: Some(candidate),
            deferred_teardown,
        }
    }

    fn candidate(&self) -> Option<&ObjectLeaseCandidate> {
        self.candidate.as_ref()
    }

    fn candidate_mut(&mut self) -> Option<&mut ObjectLeaseCandidate> {
        self.candidate.as_mut()
    }

    fn take(&mut self) -> Option<ObjectLeaseCandidate> {
        self.candidate.take()
    }

    fn defer_now(&mut self) {
        if let Some(candidate) = self.candidate.take() {
            self.deferred_teardown.retain_candidate(candidate);
        }
    }
}

impl Drop for DeferredCandidateGuard<'_> {
    fn drop(&mut self) {
        self.defer_now();
    }
}

struct LeaseFailureGuard<'a> {
    cache: &'a CommitCache,
    armed: bool,
}

impl<'a> LeaseFailureGuard<'a> {
    fn new(cache: &'a CommitCache) -> Self {
        Self { cache, armed: true }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for LeaseFailureGuard<'_> {
    fn drop(&mut self) {
        if self.armed {
            self.cache.drop_object_lease();
        }
    }
}

#[cfg(test)]
#[derive(Default)]
struct CoordinatorPublicationProbe {
    ciphertext_hashes: usize,
    catalog_decrypts: usize,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CatalogLoadStage {
    KernelLock,
    PointerIo,
    KeyDerivation,
    CacheAccess,
    CatalogFileIo,
    CatalogCrypto,
    SecondHeadRead,
}

#[cfg(test)]
#[derive(Default)]
struct CatalogLoadProbe<'a> {
    pointer_reads: usize,
    key_derivations: usize,
    catalog_file_reads: usize,
    catalog_decrypts: usize,
    catalog_encodes: usize,
    observe_stage: Option<&'a mut dyn FnMut(CatalogLoadStage)>,
}

#[cfg(test)]
impl<'a> CatalogLoadProbe<'a> {
    fn observed(observe_stage: &'a mut dyn FnMut(CatalogLoadStage)) -> Self {
        Self {
            observe_stage: Some(observe_stage),
            ..Self::default()
        }
    }

    fn stage(&mut self, stage: CatalogLoadStage) {
        if let Some(observer) = self.observe_stage.as_mut() {
            observer(stage);
        }
    }

    fn pointer_read(&mut self) {
        self.pointer_reads += 1;
        self.stage(CatalogLoadStage::PointerIo);
    }

    fn catalog_file_read(&mut self) {
        self.catalog_file_reads += 1;
        self.stage(CatalogLoadStage::CatalogFileIo);
    }

    fn key_derivation(&mut self) {
        self.key_derivations += 1;
        self.stage(CatalogLoadStage::KeyDerivation);
    }

    fn catalog_crypto_started(&mut self) {
        self.stage(CatalogLoadStage::CatalogCrypto);
    }

    fn catalog_crypto_completed(&mut self) {
        self.catalog_decrypts += 1;
        self.catalog_encodes += 1;
    }
}

#[cfg(test)]
fn observe_catalog_key_derivation<T>(value: T, probe: Option<&mut CatalogLoadProbe<'_>>) -> T {
    if let Some(probe) = probe {
        probe.key_derivation();
    }
    value
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CommitStage {
    KernelLock,
    PointerIo,
    KeyDerivation,
    CatalogIoCrypto,
    PreconditionAndBuild,
    LeaseMonitorArmed,
    LeaseLayoutRevalidated,
    LeaseObjectValidated,
    LeaseFence,
    EncryptionAndPublication,
    FailureHook,
    InvalidationCallback,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RotationStage {
    KernelLock,
    PointerIo,
    KeyDerivation,
    CatalogIoCrypto,
    ObjectRewrap,
    EncryptionAndPublication,
    FailureHook,
    InvalidationCallback,
}

#[cfg(test)]
#[derive(Default)]
struct RotationProbe<'a> {
    observe_stage: Option<&'a mut dyn FnMut(RotationStage)>,
}

#[cfg(test)]
impl<'a> RotationProbe<'a> {
    fn observed(observe_stage: &'a mut dyn FnMut(RotationStage)) -> Self {
        Self {
            observe_stage: Some(observe_stage),
        }
    }

    fn stage(&mut self, stage: RotationStage) {
        if let Some(observer) = self.observe_stage.as_mut() {
            observer(stage);
        }
    }
}

#[cfg(test)]
#[derive(Default)]
struct CommitProbe<'a> {
    pointer_reads: usize,
    catalog_file_reads: usize,
    catalog_decrypts: usize,
    catalog_encodes: usize,
    object_dek_unwraps: usize,
    object_safe_opens: usize,
    observe_stage: Option<&'a mut dyn FnMut(CommitStage)>,
}

#[cfg(test)]
impl<'a> CommitProbe<'a> {
    fn observed(observe_stage: &'a mut dyn FnMut(CommitStage)) -> Self {
        Self {
            observe_stage: Some(observe_stage),
            ..Self::default()
        }
    }

    fn stage(&mut self, stage: CommitStage) {
        if let Some(observer) = self.observe_stage.as_mut() {
            observer(stage);
        }
    }

    fn absorb_catalog_load_counts(
        &mut self,
        pointer_reads: usize,
        catalog_file_reads: usize,
        catalog_decrypts: usize,
        catalog_encodes: usize,
    ) {
        self.pointer_reads += pointer_reads;
        self.catalog_file_reads += catalog_file_reads;
        self.catalog_decrypts += catalog_decrypts;
        self.catalog_encodes += catalog_encodes;
    }

    fn object_dek_unwrap(&mut self) {
        self.object_dek_unwraps += 1;
    }

    fn object_safe_open(&mut self) {
        self.object_safe_opens += 1;
    }
}

#[cfg(test)]
fn observe_commit_key_derivation<T>(value: T, probe: Option<&mut CommitProbe<'_>>) -> T {
    if let Some(probe) = probe {
        probe.stage(CommitStage::KeyDerivation);
    }
    value
}

#[cfg(test)]
fn observe_commit_failure_hook<T>(value: T, probe: Option<&mut CommitProbe<'_>>) -> T {
    if let Some(probe) = probe {
        probe.stage(CommitStage::FailureHook);
    }
    value
}

#[derive(Debug)]
struct CoordinatorMonotonicClock {
    started: Instant,
}

impl CoordinatorMonotonicClock {
    fn new() -> Self {
        Self {
            started: Instant::now(),
        }
    }
}

impl MonotonicClock for CoordinatorMonotonicClock {
    fn now(&self) -> Duration {
        self.started.elapsed()
    }
}

#[derive(Debug)]
struct UnsupportedLeaseFactory;

impl LeaseResourceFactory for UnsupportedLeaseFactory {
    fn resource_plan(&self) -> object_lease::LeaseResourcePlan {
        object_lease::LeaseResourcePlan::unsupported()
    }

    fn create_monitor(
        &self,
        _plan: object_lease::LeaseResourcePlan,
        _state: Arc<object_lease::MonitorStateCell>,
    ) -> Result<Box<dyn object_lease::LeaseMonitorResource>, ()> {
        Err(())
    }

    fn create_anchor(
        &self,
        _index: usize,
        _binding: &ValidatedObjectBinding,
    ) -> Result<object_lease::ValidationAnchor, ()> {
        Ok(object_lease::ValidationAnchor::Unsupported)
    }
}

pub struct CoreCommitCoordinator {
    core_id: String,
    // Task 4 integrates lookup/replacement into the existing load paths.
    #[allow(dead_code)]
    cache: CommitCache,
    core_root: PathBuf,
    catalogs_path: PathBuf,
    objects_path: PathBuf,
    head_path: PathBuf,
    validation_head_path: PathBuf,
    cutover_receipt_path: PathBuf,
    cutover_complete_path: PathBuf,
    lock_path: PathBuf,
    root_dir: Dir,
    fs_dir: Dir,
    catalogs_dir: Dir,
    objects_dir: Dir,
    lease_publication: LeasePublicationAuthority,
    lease_attempt_policy: Mutex<LeaseAttemptPolicy>,
    #[cfg(test)]
    lease_factory_override: Mutex<Option<Arc<dyn object_lease::LeaseResourceFactory>>>,
    lease_budget_override: Option<LeaseBudget>,
    object_lease_diagnostics: Option<Arc<ObjectLeaseDiagnosticObserver>>,
}

#[derive(Debug)]
struct LeasePublicationAuthority {
    state: Mutex<LeasePublicationState>,
    changed: Condvar,
}

#[derive(Debug)]
struct LeasePublicationState {
    open: bool,
    generation: u64,
    active_operations: usize,
    active_operations_by_thread: HashMap<ThreadId, usize>,
    release_depth: usize,
}

impl Default for LeasePublicationAuthority {
    fn default() -> Self {
        Self {
            state: Mutex::new(LeasePublicationState {
                open: true,
                generation: 0,
                active_operations: 0,
                active_operations_by_thread: HashMap::new(),
                release_depth: 0,
            }),
            changed: Condvar::new(),
        }
    }
}

impl LeasePublicationAuthority {
    fn admit(&self) -> Result<LeasePublicationOperation<'_>, CommitError> {
        let owner_thread = thread::current().id();
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if !state.open {
            return Err(CommitError::ObjectLeaseReleaseInProgress);
        }
        let active_operations = state
            .active_operations
            .checked_add(1)
            .ok_or(CommitError::ObjectLeaseOperationOverflow)?;
        let active_on_thread = state
            .active_operations_by_thread
            .get(&owner_thread)
            .copied()
            .unwrap_or(0)
            .checked_add(1)
            .ok_or(CommitError::ObjectLeaseOperationOverflow)?;
        state.active_operations = active_operations;
        state
            .active_operations_by_thread
            .insert(owner_thread, active_on_thread);
        Ok(LeasePublicationOperation {
            authority: self,
            owner_thread,
        })
    }

    fn ensure_release_not_reentrant(&self) -> Result<(), CommitError> {
        let owner_thread = thread::current().id();
        let state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if state
            .active_operations_by_thread
            .get(&owner_thread)
            .copied()
            .unwrap_or(0)
            != 0
        {
            return Err(CommitError::ObjectLeaseReleaseReentrant);
        }
        Ok(())
    }

    fn begin_release(&self) -> Result<(), CommitError> {
        let owner_thread = thread::current().id();
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if state
            .active_operations_by_thread
            .get(&owner_thread)
            .copied()
            .unwrap_or(0)
            != 0
        {
            return Err(CommitError::ObjectLeaseReleaseReentrant);
        }
        state.release_depth = state.release_depth.saturating_add(1);
        if state.open {
            state.open = false;
            state.generation = state.generation.wrapping_add(1);
        }
        Ok(())
    }

    fn wait_for_drain(&self) -> Result<(), CommitError> {
        let owner_thread = thread::current().id();
        let state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if state
            .active_operations_by_thread
            .get(&owner_thread)
            .copied()
            .unwrap_or(0)
            != 0
        {
            return Err(CommitError::ObjectLeaseReleaseReentrant);
        }
        drop(
            self.changed
                .wait_while(state, |state| state.active_operations != 0)
                .unwrap_or_else(|poisoned| poisoned.into_inner()),
        );
        Ok(())
    }

    fn resume(&self) {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.release_depth = state.release_depth.saturating_sub(1);
        if state.release_depth == 0 {
            state.open = true;
            self.changed.notify_all();
        }
    }

    fn is_open(&self) -> bool {
        self.state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .open
    }

    fn generation(&self) -> u64 {
        self.state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .generation
    }

    fn can_publish(&self, generation: u64) -> bool {
        let state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.open && state.generation == generation
    }
}

struct LeasePublicationOperation<'a> {
    authority: &'a LeasePublicationAuthority,
    owner_thread: ThreadId,
}

impl Drop for LeasePublicationOperation<'_> {
    fn drop(&mut self) {
        let mut state = self
            .authority
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        debug_assert!(state.active_operations > 0);
        state.active_operations = state.active_operations.saturating_sub(1);
        let active_on_thread = state
            .active_operations_by_thread
            .get_mut(&self.owner_thread)
            .expect("admitted lease operation retains its thread owner");
        debug_assert!(*active_on_thread > 0);
        *active_on_thread = active_on_thread.saturating_sub(1);
        if *active_on_thread == 0 {
            state.active_operations_by_thread.remove(&self.owner_thread);
        }
        self.authority.changed.notify_all();
    }
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
        Self::new_with_lease_budget(core_root, core_id, None, None)
    }

    #[cfg(any(test, feature = "session-test-seams"))]
    fn new_with_isolated_lease_budget(
        core_root: impl AsRef<Path>,
        core_id: impl Into<String>,
    ) -> Result<Self, CommitError> {
        Self::new_with_lease_budget(core_root, core_id, Some(LeaseBudget::isolated()), None)
    }

    #[cfg(windows)]
    pub(crate) fn new_with_object_lease_diagnostics(
        core_root: impl AsRef<Path>,
        core_id: impl Into<String>,
    ) -> Result<Self, CommitError> {
        let diagnostics = Arc::new(ObjectLeaseDiagnosticObserver::default());
        Self::new_with_lease_budget(
            core_root,
            core_id,
            Some(LeaseBudget::isolated()),
            Some(diagnostics),
        )
    }

    fn new_with_lease_budget(
        core_root: impl AsRef<Path>,
        core_id: impl Into<String>,
        lease_budget_override: Option<LeaseBudget>,
        object_lease_diagnostics: Option<Arc<ObjectLeaseDiagnosticObserver>>,
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
            cache: CommitCache::default(),
            head_path: fs_path.join(HEAD_FILE),
            validation_head_path: fs_path.join(VALIDATION_HEAD_FILE),
            cutover_receipt_path: fs_path.join(CUTOVER_RECEIPT_FILE),
            cutover_complete_path: fs_path.join(CUTOVER_COMPLETE_FILE),
            lock_path: fs_path.join(COMMIT_LOCK_FILE),
            core_root,
            catalogs_path,
            objects_path,
            root_dir,
            fs_dir,
            catalogs_dir,
            objects_dir,
            lease_publication: LeasePublicationAuthority::default(),
            lease_attempt_policy: Mutex::new(LeaseAttemptPolicy::new(Arc::new(
                CoordinatorMonotonicClock::new(),
            ))),
            #[cfg(test)]
            lease_factory_override: Mutex::new(None),
            lease_budget_override,
            object_lease_diagnostics,
        })
    }

    #[cfg(test)]
    fn set_lease_factory_for_test(&self, factory: Arc<dyn object_lease::LeaseResourceFactory>) {
        *self
            .lease_factory_override
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(factory);
    }

    fn lease_budget(&self) -> &LeaseBudget {
        self.lease_budget_override
            .as_ref()
            .unwrap_or_else(|| global_lease_budget())
    }

    fn lease_factory(&self) -> Arc<dyn LeaseResourceFactory> {
        #[cfg(test)]
        if let Some(factory) = self
            .lease_factory_override
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
        {
            return factory;
        }

        #[cfg(all(windows, not(test)))]
        {
            let objects_dir = match self.objects_dir.try_clone() {
                Ok(objects_dir) => objects_dir,
                Err(_) => return Arc::new(UnsupportedLeaseFactory),
            };
            let factory = if let Some(diagnostics) = &self.object_lease_diagnostics {
                object_lease::windows::WindowsLeaseFactory::new_observed(
                    objects_dir,
                    Arc::clone(diagnostics),
                )
            } else {
                object_lease::windows::WindowsLeaseFactory::new(objects_dir)
            };
            factory.map_or_else(
                |_| Arc::new(UnsupportedLeaseFactory) as Arc<dyn LeaseResourceFactory>,
                |factory| Arc::new(factory) as Arc<dyn LeaseResourceFactory>,
            )
        }

        #[cfg(any(test, not(windows)))]
        Arc::new(UnsupportedLeaseFactory)
    }

    #[cfg(windows)]
    pub(crate) fn object_lease_diagnostic_counters(
        &self,
    ) -> Option<ObjectLeaseDiagnosticCounterSnapshot> {
        self.object_lease_diagnostics
            .as_ref()
            .map(|diagnostics| diagnostics.counters())
    }

    #[cfg(windows)]
    pub(crate) fn object_lease_diagnostic_boundary_events(
        &self,
    ) -> Option<Vec<ObjectLeaseDiagnosticBoundaryEvent>> {
        self.object_lease_diagnostics
            .as_ref()
            .map(|diagnostics| diagnostics.boundary_events())
    }

    #[cfg(windows)]
    pub(crate) fn reset_object_lease_diagnostic_counters(&self) {
        if let Some(diagnostics) = &self.object_lease_diagnostics {
            diagnostics.reset_counters();
        }
    }

    #[cfg(windows)]
    pub(crate) fn object_lease_diagnostic_resources(&self) -> (usize, usize, usize) {
        let usage = self.lease_budget().usage();
        (usage.entries, usage.leases, usage.monitor_resources)
    }

    #[cfg(windows)]
    pub(crate) fn object_lease_diagnostic_target_identity(
        &self,
    ) -> Result<(u64, u64), CommitError> {
        let metadata = Metadata::from_file(&self.root_dir.try_clone()?.into_std_file())?;
        Ok((metadata.dev(), metadata.ino()))
    }

    #[cfg(windows)]
    pub(crate) fn object_lease_teardown_observation(
        &self,
    ) -> Option<ObjectLeaseTeardownObservation> {
        self.object_lease_diagnostics
            .as_ref()
            .and_then(|diagnostics| diagnostics.teardown())
    }

    #[cfg(windows)]
    pub(crate) fn publish_object_lease_unknown_for_diagnostic(&self) -> bool {
        self.cache
            .current()
            .and_then(|snapshot| snapshot.object_lease.clone())
            .is_some_and(|lease| {
                lease.publish_unknown();
                true
            })
    }

    #[cfg(windows)]
    pub(crate) fn prove_between_fence_mutation_for_diagnostic(&self, path: &Path) -> bool {
        let Some(diagnostics) = &self.object_lease_diagnostics else {
            return false;
        };
        let Some(lease) = self
            .cache
            .current()
            .and_then(|snapshot| snapshot.object_lease.clone())
        else {
            return false;
        };
        diagnostics.arm_between_fence_mutation(path.to_path_buf());
        let outcome = lease.fence();
        diagnostics.between_fence_mutation_succeeded()
            && outcome == object_lease::MonitorState::DirtyAll
    }

    fn object_directory_identity(&self) -> Result<DirectoryIdentity, CommitError> {
        let metadata = Metadata::from_file(&self.objects_dir.try_clone()?.into_std_file())?;
        Ok(DirectoryIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
        })
    }

    fn record_lease_optimization_miss(
        &self,
        miss: OptimizationMiss,
        fingerprint: object_lease::ObjectSetFingerprint,
        requested_count: usize,
        budget_epoch: u64,
    ) {
        let mut policy = self
            .lease_attempt_policy
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        match miss {
            OptimizationMiss::BudgetDenied => {
                policy.record_budget_denial(fingerprint, requested_count, budget_epoch);
            }
            OptimizationMiss::TransientAcquisition => policy.record_transient_failure(),
            OptimizationMiss::UnsupportedPlatform
            | OptimizationMiss::OverCeiling
            | OptimizationMiss::TransientBackoff => {}
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn validate_prepared_revisions_with_candidate(
        &self,
        keys: &FrkSubkeys,
        current: &CatalogGeneration,
        next: &CatalogGeneration,
        prepared: &[PreparedObjectRevision],
        cached: Option<&ValidatedObjectState>,
        expected_pointers: &PointerSet,
        deferred_teardown: &mut DeferredLeaseTeardown,
        #[cfg(test)] mut probe: Option<&mut CommitProbe<'_>>,
    ) -> Result<(ValidatedObjectState, Option<Arc<ObjectValidationLease>>), CommitError> {
        let expected_bindings = catalog_object_bindings(next)?;
        let candidate_publication_generation = self.lease_publication_generation();
        if !self.lease_publication_is_open() {
            let validated = validate_prepared_revisions_observed(
                &self.objects_dir,
                keys,
                &self.core_id,
                (Some(current), next),
                prepared,
                cached,
                self.object_lease_diagnostics.as_deref(),
                |_, _| {},
                #[cfg(test)]
                probe,
            )?;
            return Ok((validated, None));
        }
        let fingerprint = object_set_fingerprint(&expected_bindings);
        let requested_count = expected_bindings.len();
        let budget = self.lease_budget();
        let decision_budget_epoch = budget.epoch();
        let decision = self
            .lease_attempt_policy
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .decision(fingerprint, requested_count, decision_budget_epoch);
        if decision != LeaseAttemptDecision::Eligible {
            let validated = validate_prepared_revisions_observed(
                &self.objects_dir,
                keys,
                &self.core_id,
                (Some(current), next),
                prepared,
                cached,
                self.object_lease_diagnostics.as_deref(),
                |_, _| {},
                #[cfg(test)]
                probe,
            )?;
            return Ok((validated, None));
        }

        let factory = self.lease_factory();
        for attempt in 0..2 {
            #[cfg(test)]
            let observed_pointers = self.load_pointer_set(None)?;
            #[cfg(not(test))]
            let observed_pointers = self.load_pointer_set()?;
            if &observed_pointers != expected_pointers {
                let validated = validate_prepared_revisions_observed(
                    &self.objects_dir,
                    keys,
                    &self.core_id,
                    (Some(current), next),
                    prepared,
                    cached,
                    self.object_lease_diagnostics.as_deref(),
                    |_, _| {},
                    #[cfg(test)]
                    probe,
                )?;
                return Ok((validated, None));
            }

            let directory_identity = self.object_directory_identity()?;
            let acquisition_budget_epoch = budget.epoch();
            let candidate = match ObjectLeaseCandidate::try_begin(
                budget,
                expected_bindings.clone(),
                directory_identity,
                candidate_publication_generation,
                factory.as_ref(),
            ) {
                Ok(candidate) => candidate,
                Err(miss) => {
                    self.record_lease_optimization_miss(
                        miss,
                        fingerprint,
                        requested_count,
                        acquisition_budget_epoch,
                    );
                    let validated = validate_prepared_revisions_observed(
                        &self.objects_dir,
                        keys,
                        &self.core_id,
                        (Some(current), next),
                        prepared,
                        cached,
                        self.object_lease_diagnostics.as_deref(),
                        |_, _| {},
                        #[cfg(test)]
                        probe,
                    )?;
                    return Ok((validated, None));
                }
            };
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CommitStage::LeaseMonitorArmed);
            }

            let mut candidate = DeferredCandidateGuard::new(candidate, deferred_teardown);
            self.validate_pinned_layout()?;
            let current_directory_identity = self.object_directory_identity()?;
            if current_directory_identity != directory_identity {
                return Err(CommitError::InvalidCoreLayout);
            }
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CommitStage::LeaseLayoutRevalidated);
            }

            let validated = validate_prepared_revisions_observed(
                &self.objects_dir,
                keys,
                &self.core_id,
                (Some(current), next),
                prepared,
                cached,
                self.object_lease_diagnostics.as_deref(),
                |binding, file| {
                    let acquisition_failed = candidate.candidate_mut().is_some_and(|active| {
                        active
                            .add_validated_file(binding, file, factory.as_ref())
                            .is_err()
                    });
                    if acquisition_failed {
                        candidate.defer_now();
                    }
                },
                #[cfg(test)]
                probe.as_deref_mut(),
            )?;
            let Some(active) = candidate.candidate() else {
                self.record_lease_optimization_miss(
                    OptimizationMiss::TransientAcquisition,
                    fingerprint,
                    requested_count,
                    acquisition_budget_epoch,
                );
                return Ok((validated, None));
            };
            let fence = active.fence();
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CommitStage::LeaseFence);
            }
            if fence == FenceOutcome::Clean {
                if !self.lease_publication_is_open()
                    || self.lease_publication_generation() != candidate_publication_generation
                {
                    candidate.defer_now();
                    return Ok((validated, None));
                }
                let active_candidate = candidate
                    .take()
                    .expect("clean fence retains the active lease candidate");
                drop(candidate);
                return match active_candidate.finish(fence) {
                    Ok(lease) => {
                        self.lease_attempt_policy
                            .lock()
                            .unwrap_or_else(|poisoned| poisoned.into_inner())
                            .record_success();
                        Ok((validated, Some(lease)))
                    }
                    Err(candidate) => {
                        deferred_teardown.retain_candidate(*candidate);
                        self.record_lease_optimization_miss(
                            OptimizationMiss::TransientAcquisition,
                            fingerprint,
                            requested_count,
                            acquisition_budget_epoch,
                        );
                        Ok((validated, None))
                    }
                };
            }
            candidate.defer_now();
            drop(candidate);
            if fence != FenceOutcome::DirtyAll || attempt == 1 {
                self.record_lease_optimization_miss(
                    OptimizationMiss::TransientAcquisition,
                    fingerprint,
                    requested_count,
                    acquisition_budget_epoch,
                );
                return Ok((validated, None));
            }
        }
        unreachable!("candidate scan retries at most once")
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

    pub fn cutover_complete_path(&self) -> &Path {
        &self.cutover_complete_path
    }

    pub fn catalogs_path(&self) -> &Path {
        &self.catalogs_path
    }

    pub fn objects_path(&self) -> &Path {
        &self.objects_path
    }

    pub(crate) fn core_id(&self) -> &str {
        &self.core_id
    }

    pub(crate) fn clone_objects_dir(&self) -> io::Result<Dir> {
        self.objects_dir.try_clone()
    }

    pub fn lock_path(&self) -> &Path {
        &self.lock_path
    }

    pub fn core_root(&self) -> &Path {
        &self.core_root
    }

    fn admit_lease_publication_operation(
        &self,
    ) -> Result<LeasePublicationOperation<'_>, CommitError> {
        self.lease_publication.admit()
    }

    /// Marks the current object-validation lease terminally unknown and wakes any
    /// cancellation-aware monitor fence without waiting for backend destruction.
    pub fn ensure_object_lease_release_not_reentrant(&self) -> Result<(), CommitError> {
        self.lease_publication.ensure_release_not_reentrant()
    }

    pub fn begin_object_lease_release(&self) -> Result<(), CommitError> {
        self.lease_publication.begin_release()?;
        self.cache.begin_object_lease_release();
        Ok(())
    }

    /// Clears and completion-drains the current object-validation lease.
    ///
    /// The cache mutex is released before monitor destruction, native completion,
    /// worker join, anchor destruction, or process-budget permit return.
    pub fn finish_object_lease_release(&self) -> Result<(), CommitError> {
        self.lease_publication.wait_for_drain()?;
        self.cache.drop_object_lease();
        Ok(())
    }

    /// Idempotently cancels, drains, and clears the current object-validation lease.
    pub fn release_object_lease(&self) -> Result<(), CommitError> {
        self.begin_object_lease_release()?;
        let result = self.finish_object_lease_release();
        self.resume_object_lease_publication();
        result
    }

    /// Reopens lease construction after a non-terminal, completion-confirmed release.
    pub fn resume_object_lease_publication(&self) {
        self.lease_publication.resume();
    }

    /// Clears all process-local authenticated cache state after lease cancellation.
    ///
    /// Disk pointers and authenticated catalog bytes remain authoritative; a later
    /// operation on an open coordinator rebuilds the cache from disk.
    pub fn clear_cached_state(&self) {
        self.cache.clear();
    }

    fn lease_publication_is_open(&self) -> bool {
        self.lease_publication.is_open()
    }

    fn lease_publication_generation(&self) -> u64 {
        self.lease_publication.generation()
    }

    fn object_lease_can_publish(&self, lease: &ObjectValidationLease) -> bool {
        self.lease_publication
            .can_publish(lease.publication_generation())
    }

    pub fn prepare_object_revision<R: Read>(
        &self,
        keys: &FrkSubkeys,
        object_key: &SecretBytes,
        aad: &ObjectBaseAad,
        encrypted_object: &mut R,
    ) -> Result<PreparedObjectRevision, CommitError> {
        self.prepare_object_revision_with_hook(keys, object_key, aad, encrypted_object, &mut |_| {
            Ok(())
        })
    }

    fn prepare_object_revision_with_hook<R, H>(
        &self,
        keys: &FrkSubkeys,
        object_key: &SecretBytes,
        aad: &ObjectBaseAad,
        encrypted_object: &mut R,
        hook: &mut H,
    ) -> Result<PreparedObjectRevision, CommitError>
    where
        R: Read,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        self.validate_pinned_layout()?;
        if aad.core_id() != self.core_id {
            return Err(CommitError::InvalidObjectRevision);
        }
        let (mut staged, staged_name) =
            create_temporary_in(&self.objects_dir, OsStr::new("object"))?;
        hook(CommitFailurePoint::Publication {
            target: PublicationTarget::Object,
            phase: PublicationPhase::TemporaryCreated,
        })?;
        let result = (|| {
            let (encoded_size, encrypted_hash) =
                copy_bounded(encrypted_object, &mut staged, MAX_ENVELOPE_SIZE)?;
            hook(CommitFailurePoint::Publication {
                target: PublicationTarget::Object,
                phase: PublicationPhase::PayloadWritten,
            })?;
            staged.sync_all()?;
            hook(CommitFailurePoint::Publication {
                target: PublicationTarget::Object,
                phase: PublicationPhase::PayloadSynced,
            })?;
            self.finalize_staged_object_revision(
                keys,
                object_key,
                aad,
                &mut staged,
                &staged_name,
                encoded_size,
                encrypted_hash,
                hook,
            )
        })();
        drop(staged);
        if result.is_err() {
            let _ = self.objects_dir.remove_file(&staged_name);
        }
        result
    }

    /// Prepares a targeted object-key replacement outside the commit lock.
    ///
    /// The new Object DEK is generated internally. The returned immutable
    /// revision remains unreferenced until a normal preconditioned catalog
    /// commit publishes it.
    pub fn prepare_object_key_rotation(
        &self,
        active_keys: &FrkSubkeys,
        current: &CatalogGeneration,
        object_id: &OpaqueId,
        old_object_key: &SecretBytes,
        updated_at: &str,
    ) -> Result<PreparedObjectRevision, CommitError> {
        self.prepare_object_key_rotation_with_hook(
            active_keys,
            current,
            object_id,
            old_object_key,
            updated_at,
            &mut |_| Ok(()),
        )
    }

    fn prepare_object_key_rotation_with_hook<H>(
        &self,
        active_keys: &FrkSubkeys,
        current: &CatalogGeneration,
        object_id: &OpaqueId,
        old_object_key: &SecretBytes,
        updated_at: &str,
        hook: &mut H,
    ) -> Result<PreparedObjectRevision, CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        self.validate_pinned_layout()?;
        let source = current
            .entries()
            .iter()
            .find(|entry| entry.stable_id() == object_id)
            .and_then(CatalogGenerationEntry::object_payload)
            .ok_or_else(|| CommitError::ObjectKeyRotationSourceMissing {
                stable_id: object_id.as_str().to_owned(),
            })?;
        if matches!(source.lifecycle(), ObjectLifecycle::Tombstone { .. }) {
            return Err(CommitError::ObjectKeyRotationTombstone {
                stable_id: object_id.as_str().to_owned(),
            });
        }
        if catalog_object_key_binding(
            active_keys,
            &self.core_id,
            object_id,
            source,
            #[cfg(test)]
            None,
        )? != object_key_binding(old_object_key)
        {
            return Err(CommitError::ReferencedObjectKeyMismatch {
                stable_id: object_id.as_str().to_owned(),
            });
        }
        let next_revision = source
            .revision()
            .checked_add(1)
            .ok_or(CommitError::ObjectRevisionExhausted)?;
        let next_epoch = source
            .object_key_epoch()
            .checked_add(1)
            .ok_or(CommitError::ObjectKeyEpochExhausted)?;
        let old_aad = ObjectBaseAad::new(
            &self.core_id,
            object_id.as_str(),
            source.kind(),
            OBJECT_KEY_ENVELOPE_VERSION,
            source.object_key_epoch(),
            source.revision(),
        )?;
        let new_aad = ObjectBaseAad::new(
            &self.core_id,
            object_id.as_str(),
            source.kind(),
            OBJECT_KEY_ENVELOPE_VERSION,
            next_epoch,
            next_revision,
        )?;
        let new_object_key = generate_object_dek()?;
        let mut source_file = open_regular_file_in(
            &self.objects_dir,
            OsStr::new(source.physical_name().as_str()),
        )?;
        let (mut staged, staged_name) =
            create_temporary_in(&self.objects_dir, OsStr::new("object"))?;
        hook(CommitFailurePoint::Publication {
            target: PublicationTarget::Object,
            phase: PublicationPhase::TemporaryCreated,
        })?;
        let result = (|| {
            let rotated_metadata = rotate_object_key_envelope(
                &mut source_file,
                &mut staged,
                old_object_key,
                &old_aad,
                &new_object_key,
                &new_aad,
                updated_at,
            )?;
            if rotated_metadata.body_sha256 != source.content_hash().as_str() {
                return Err(CommitError::ObjectKeyRotationSourceMismatch {
                    stable_id: object_id.as_str().to_owned(),
                });
            }
            hook(CommitFailurePoint::Publication {
                target: PublicationTarget::Object,
                phase: PublicationPhase::PayloadWritten,
            })?;
            staged.sync_all()?;
            hook(CommitFailurePoint::Publication {
                target: PublicationTarget::Object,
                phase: PublicationPhase::PayloadSynced,
            })?;
            staged.seek(SeekFrom::Start(0))?;
            let (encoded_size, encrypted_hash) =
                copy_bounded(&mut staged, &mut io::sink(), MAX_ENVELOPE_SIZE)?;
            self.finalize_staged_object_revision(
                active_keys,
                &new_object_key,
                &new_aad,
                &mut staged,
                &staged_name,
                encoded_size,
                encrypted_hash,
                hook,
            )
        })();
        drop(staged);
        if result.is_err() {
            let _ = self.objects_dir.remove_file(&staged_name);
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    fn finalize_staged_object_revision<H>(
        &self,
        keys: &FrkSubkeys,
        object_key: &SecretBytes,
        aad: &ObjectBaseAad,
        staged: &mut CapFile,
        staged_name: &OsStr,
        encoded_size: u64,
        encrypted_hash: [u8; 32],
        hook: &mut H,
    ) -> Result<PreparedObjectRevision, CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        staged.seek(SeekFrom::Start(0))?;
        let envelope = read_envelope(staged, object_key, aad, &mut io::sink())?;
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
            let mut publication_hook = |phase| {
                hook(CommitFailurePoint::Publication {
                    target: PublicationTarget::Object,
                    phase,
                })
            };
            match publish_staged_immutable_in_with_hook(
                &self.objects_dir,
                staged,
                staged_name,
                OsStr::new(physical_name.as_str()),
                &mut publication_hook,
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
    }

    pub fn load_committed(
        &self,
        keys: &FrkSubkeys,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        self.load_committed_with_keyring(&FrkKeyring::single(keys))
    }

    #[cfg(test)]
    fn load_committed_with_probe(
        &self,
        keys: &FrkSubkeys,
        probe: &mut CatalogLoadProbe<'_>,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        self.load_committed_with_keyring_observation_hook_inner(
            &FrkKeyring::single(keys),
            || {},
            Some(probe),
        )
    }

    #[cfg(test)]
    fn load_committed_locked_with_probe(
        &self,
        keyring: &FrkKeyring<'_>,
        probe: &mut CatalogLoadProbe<'_>,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        let mut deferred_teardown = DeferredLeaseTeardown::default();
        let commit_lock = CoreCommitLock::acquire_in_with_post_kernel_lock_hook(
            &self.root_dir,
            &self.fs_dir,
            || probe.stage(CatalogLoadStage::KernelLock),
        )?;
        let result = self.load_committed_recovering_with_keyring_and_hook_inner(
            &commit_lock,
            keyring,
            &mut |_| Ok(()),
            &mut deferred_teardown,
            Some(probe),
        );
        drop(commit_lock);
        drop(deferred_teardown);
        result
    }

    /// Reads the version declared by the current HEAD so the session can select
    /// matching pending or active key material before authenticating the catalog.
    /// The returned value is an untrusted selection hint until a load verifies
    /// the referenced encrypted catalog.
    pub fn required_frk_version(&self) -> Result<Option<u32>, CommitError> {
        self.validate_pinned_layout()?;
        Ok(self
            .load_pointer_head(HEAD_FILE)?
            .map(|head| head.required_frk_version()))
    }

    /// Loads the committed catalog across an FRK activation boundary.
    ///
    /// The current HEAD is always authenticated with its declared key. Retained
    /// cutover markers use their own older version while it remains available.
    pub fn load_committed_with_keyring(
        &self,
        keyring: &FrkKeyring<'_>,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        self.load_committed_with_keyring_observation_hook(keyring, || {})
    }

    fn load_committed_with_keyring_observation_hook<R>(
        &self,
        keyring: &FrkKeyring<'_>,
        after_candidate_selection: R,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        R: FnOnce(),
    {
        self.load_committed_with_keyring_observation_hook_inner(
            keyring,
            after_candidate_selection,
            #[cfg(test)]
            None,
        )
    }

    fn load_committed_with_keyring_observation_hook_inner<R>(
        &self,
        keyring: &FrkKeyring<'_>,
        after_candidate_selection: R,
        #[cfg(test)] mut probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        R: FnOnce(),
    {
        let _lease_operation = self.admit_lease_publication_operation()?;
        self.validate_pinned_layout()?;
        let pointers = self.load_pointer_set(
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;
        let lookup_key = self.cache_lookup_key(
            &pointers,
            keyring,
            #[cfg(test)]
            probe.as_deref_mut(),
        );
        let cached = lookup_key.as_ref().and_then(|key| self.cache.get(key));
        #[cfg(test)]
        if lookup_key.is_some() {
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CatalogLoadStage::CacheAccess);
            }
        }
        after_candidate_selection();
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.stage(CatalogLoadStage::SecondHeadRead);
        }
        if self.load_pointer_head_inner(
            HEAD_FILE,
            #[cfg(test)]
            probe.as_deref_mut(),
        )? != pointers.head
        {
            let mut deferred_teardown = DeferredLeaseTeardown::default();
            let commit_lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
            self.validate_pinned_layout()?;
            let result = self.load_committed_recovering_with_keyring(
                &commit_lock,
                keyring,
                &mut deferred_teardown,
            );
            drop(commit_lock);
            drop(deferred_teardown);
            return result;
        }

        if let (Some(head), Some(snapshot)) = (pointers.head.clone(), cached) {
            if let Err(error) = self.reauthenticate_cached_catalog_bytes(
                &pointers,
                #[cfg(test)]
                probe.as_deref_mut(),
            ) {
                self.cache.clear();
                return Err(error);
            }
            return Ok(Some(CommittedCatalog {
                head,
                catalog: Arc::clone(snapshot.catalog()),
            }));
        }

        let committed = self.load_committed_once_with_keyring_heads_inner(
            keyring,
            pointers.head.clone(),
            pointers.receipt.clone(),
            pointers.complete.clone(),
            #[cfg(test)]
            probe,
        );
        if let (Some(key), Ok(Some(committed))) = (lookup_key.as_ref(), committed.as_ref()) {
            self.cache
                .replace(Arc::new(AuthenticatedCommitSnapshot::new(
                    key,
                    Arc::clone(&committed.catalog),
                    None,
                )));
        }
        if !matches!(
            &committed,
            Err(CommitError::CutoverRecoveryRequired
                | CommitError::AuthoritativeHeadMissingAfterCutover
                | CommitError::AuthoritativeHeadViolatesCutoverReceipt)
        ) {
            if !matches!(&committed, Ok(Some(_))) {
                self.cache.clear();
            }
            return committed;
        }
        let mut deferred_teardown = DeferredLeaseTeardown::default();
        let commit_lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let result = self.load_committed_recovering_with_keyring(
            &commit_lock,
            keyring,
            &mut deferred_teardown,
        );
        drop(commit_lock);
        drop(deferred_teardown);
        result
    }

    fn cache_lookup_key(
        &self,
        pointers: &PointerSet,
        keyring: &FrkKeyring<'_>,
        #[cfg(test)] probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Option<CacheLookupKey> {
        if !pointers.is_complete_non_recovery_shape() {
            return None;
        }
        let head = pointers.head.as_ref()?;
        let active_keys = keyring.require(head.required_frk_version()).ok()?;
        #[cfg(test)]
        {
            observe_catalog_key_derivation(
                CacheLookupKey::derive(pointers.clone(), &self.core_id, keyring, active_keys).ok(),
                probe,
            )
        }
        #[cfg(not(test))]
        {
            CacheLookupKey::derive(pointers.clone(), &self.core_id, keyring, active_keys).ok()
        }
    }

    fn load_committed_once_with_keyring_heads_inner(
        &self,
        keyring: &FrkKeyring<'_>,
        committed_head: Option<HeadRecord>,
        receipt_head: Option<HeadRecord>,
        complete_head: Option<HeadRecord>,
        #[cfg(test)] mut probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        if committed_head.is_none() && receipt_head.is_none() && complete_head.is_none() {
            return Ok(None);
        }
        let Some(committed_head) = committed_head else {
            return if receipt_head.is_some() && complete_head.is_some() {
                Err(CommitError::AuthoritativeHeadMissingAfterCutover)
            } else {
                Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt)
            };
        };
        let committed = self.load_pointer_for_head_inner(
            keyring,
            committed_head,
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;
        let (Some(receipt_head), Some(complete_head)) = (receipt_head, complete_head) else {
            return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt);
        };
        if receipt_head != complete_head {
            return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt);
        }

        if keyring.contains(receipt_head.required_frk_version()) {
            let receipt = self.load_pointer_for_head_inner(
                keyring,
                receipt_head.clone(),
                #[cfg(test)]
                probe.as_deref_mut(),
            )?;
            let complete = self.load_pointer_for_head_inner(
                keyring,
                complete_head,
                #[cfg(test)]
                probe,
            )?;
            if complete.0 != receipt.0
                || complete.1.cutover_marker() != receipt.1.cutover_marker()
                || !cutover_lineage_is_valid(&committed.0, &committed.1, &receipt.0, &receipt.1)
            {
                return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt);
            }
            return Ok(Some(CommittedCatalog {
                head: committed.0,
                catalog: Arc::new(committed.1),
            }));
        }

        if committed.1.cutover_marker().is_none()
            || committed.0.generation() <= receipt_head.generation()
        {
            return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt);
        }
        Ok(Some(CommittedCatalog {
            head: committed.0,
            catalog: Arc::new(committed.1),
        }))
    }

    #[cfg(test)]
    fn load_committed_with_hook<H>(
        &self,
        keys: &FrkSubkeys,
        hook: &mut H,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        self.load_committed_with_observation_hook(keys, hook, || {})
    }

    #[cfg(test)]
    fn load_committed_with_observation_hook<H, R>(
        &self,
        keys: &FrkSubkeys,
        hook: &mut H,
        after_head_read: R,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
        R: FnOnce(),
    {
        self.validate_pinned_layout()?;
        let committed = self.load_committed_once_with_hook(keys, after_head_read);
        if !matches!(
            &committed,
            Err(CommitError::CutoverRecoveryRequired
                | CommitError::AuthoritativeHeadMissingAfterCutover
                | CommitError::AuthoritativeHeadViolatesCutoverReceipt)
        ) {
            return committed;
        }

        let mut deferred_teardown = DeferredLeaseTeardown::default();
        let commit_lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let result = self.load_committed_recovering_with_hook(
            &commit_lock,
            keys,
            hook,
            &mut deferred_teardown,
        );
        drop(commit_lock);
        drop(deferred_teardown);
        result
    }

    #[cfg(test)]
    fn load_committed_once_with_hook<R>(
        &self,
        keys: &FrkSubkeys,
        after_head_read: R,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        R: FnOnce(),
    {
        let committed = self.load_pointer(keys, HEAD_FILE)?;
        after_head_read();
        let receipt = self.load_pointer(keys, CUTOVER_RECEIPT_FILE)?;
        let complete = self.load_pointer(keys, CUTOVER_COMPLETE_FILE)?;
        match (committed, receipt, complete) {
            (None, None, None) => Ok(None),
            (None, Some(_), None) => Err(CommitError::CutoverRecoveryRequired),
            (Some((_head, catalog)), None, None) => {
                if catalog.cutover_marker().is_some() {
                    Err(CommitError::CutoverRecoveryRequired)
                } else {
                    Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt)
                }
            }
            (Some((head, catalog)), Some((receipt_head, receipt_catalog)), None) => {
                if cutover_lineage_is_valid(&head, &catalog, &receipt_head, &receipt_catalog) {
                    Err(CommitError::CutoverRecoveryRequired)
                } else {
                    Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt)
                }
            }
            (None, Some(_), Some(_)) => Err(CommitError::AuthoritativeHeadMissingAfterCutover),
            (
                Some((head, catalog)),
                Some((receipt_head, receipt_catalog)),
                Some((complete_head, complete_catalog)),
            ) => {
                let receipt_marker = receipt_catalog.cutover_marker();
                let committed_marker = catalog.cutover_marker();
                if receipt_marker.is_none()
                    || complete_catalog.cutover_marker() != receipt_marker
                    || complete_head != receipt_head
                    || committed_marker != receipt_marker
                    || head.generation() < receipt_head.generation()
                    || (head.generation() == receipt_head.generation() && head != receipt_head)
                {
                    return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt);
                }
                Ok(Some(CommittedCatalog {
                    head,
                    catalog: Arc::new(catalog),
                }))
            }
            _ => Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt),
        }
    }

    fn load_committed_recovering(
        &self,
        commit_lock: &CoreCommitLock,
        keys: &FrkSubkeys,
        deferred_teardown: &mut DeferredLeaseTeardown,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        self.load_committed_recovering_with_keyring(
            commit_lock,
            &FrkKeyring::single(keys),
            deferred_teardown,
        )
    }

    fn load_committed_recovering_with_keyring(
        &self,
        commit_lock: &CoreCommitLock,
        keyring: &FrkKeyring<'_>,
        deferred_teardown: &mut DeferredLeaseTeardown,
    ) -> Result<Option<CommittedCatalog>, CommitError> {
        self.load_committed_recovering_with_keyring_and_hook_inner(
            commit_lock,
            keyring,
            &mut |_| Ok(()),
            deferred_teardown,
            #[cfg(test)]
            None,
        )
    }

    fn load_committed_recovering_with_keyring_and_hook_inner<H>(
        &self,
        commit_lock: &CoreCommitLock,
        keyring: &FrkKeyring<'_>,
        hook: &mut H,
        deferred_teardown: &mut DeferredLeaseTeardown,
        #[cfg(test)] mut probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        deferred_teardown.retain_current_cache_lease_owner(&self.cache);
        self.validate_pinned_layout()?;
        let pointers = self.load_pointer_set(
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;
        let lookup_key = self.cache_lookup_key(
            &pointers,
            keyring,
            #[cfg(test)]
            probe.as_deref_mut(),
        );
        let cached = lookup_key.as_ref().and_then(|key| {
            self.cache
                .get_deferred(key, deferred_teardown.cache_snapshots())
        });
        #[cfg(test)]
        if lookup_key.is_some() {
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CatalogLoadStage::CacheAccess);
            }
        }
        if let (Some(head), Some(snapshot)) = (pointers.head.clone(), cached) {
            if let Err(error) = self.reauthenticate_cached_catalog_bytes(
                &pointers,
                #[cfg(test)]
                probe.as_deref_mut(),
            ) {
                if let Some(lease) = snapshot.object_lease.as_ref() {
                    lease.publish_unknown();
                }
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return Err(error);
            }
            return Ok(Some(CommittedCatalog {
                head,
                catalog: Arc::clone(snapshot.catalog()),
            }));
        }

        let full_load = self.load_committed_once_with_keyring_heads_inner(
            keyring,
            pointers.head.clone(),
            pointers.receipt.clone(),
            pointers.complete.clone(),
            #[cfg(test)]
            probe.as_deref_mut(),
        );
        let recovery_required = matches!(
            &full_load,
            Err(CommitError::CutoverRecoveryRequired
                | CommitError::AuthoritativeHeadMissingAfterCutover
                | CommitError::AuthoritativeHeadViolatesCutoverReceipt)
        );
        let committed = if recovery_required {
            let recovery = self.recover_cutover_with_keyring_and_hook_inner(
                commit_lock,
                keyring,
                hook,
                #[cfg(test)]
                probe.as_deref_mut(),
            );
            match recovery {
                Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt) => full_load,
                other => other,
            }
        } else {
            full_load
        };

        if recovery_required && matches!(&committed, Ok(Some(_))) {
            let final_pointers = match self.load_pointer_set(
                #[cfg(test)]
                probe.as_deref_mut(),
            ) {
                Ok(pointers) => pointers,
                Err(error) => {
                    self.cache
                        .clear_deferred(deferred_teardown.cache_snapshots());
                    return Err(error);
                }
            };
            let verified = self.load_committed_once_with_keyring_heads_inner(
                keyring,
                final_pointers.head.clone(),
                final_pointers.receipt.clone(),
                final_pointers.complete.clone(),
                #[cfg(test)]
                probe.as_deref_mut(),
            );
            let Ok(Some(verified_catalog)) = verified.as_ref() else {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return verified;
            };
            let replacement_key = self.cache_lookup_key(
                &final_pointers,
                keyring,
                #[cfg(test)]
                probe,
            );
            if let Some(key) = replacement_key.as_ref() {
                self.cache.replace_deferred(
                    Arc::new(AuthenticatedCommitSnapshot::new(
                        key,
                        Arc::clone(&verified_catalog.catalog),
                        None,
                    )),
                    deferred_teardown.cache_snapshots(),
                );
            } else {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
            }
            return verified;
        }
        if let (Some(key), Ok(Some(committed_catalog))) = (lookup_key.as_ref(), committed.as_ref())
        {
            self.cache.replace_deferred(
                Arc::new(AuthenticatedCommitSnapshot::new(
                    key,
                    Arc::clone(&committed_catalog.catalog),
                    None,
                )),
                deferred_teardown.cache_snapshots(),
            );
        } else if recovery_required || matches!(&committed, Ok(None)) {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
        }
        committed
    }

    #[cfg(test)]
    fn load_committed_recovering_with_hook<H>(
        &self,
        commit_lock: &CoreCommitLock,
        keys: &FrkSubkeys,
        hook: &mut H,
        deferred_teardown: &mut DeferredLeaseTeardown,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        self.load_committed_recovering_with_keyring_and_hook_inner(
            commit_lock,
            &FrkKeyring::single(keys),
            hook,
            deferred_teardown,
            #[cfg(test)]
            None,
        )
    }

    fn recover_cutover_with_keyring_and_hook_inner<H>(
        &self,
        _commit_lock: &CoreCommitLock,
        keyring: &FrkKeyring<'_>,
        hook: &mut H,
        #[cfg(test)] mut probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<Option<CommittedCatalog>, CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let pointers = self.load_pointer_set(
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;
        let committed = match pointers.head {
            Some(head) => Some(self.load_pointer_for_head_inner(
                keyring,
                head,
                #[cfg(test)]
                probe.as_deref_mut(),
            )?),
            None => None,
        };
        let receipt = match pointers.receipt {
            Some(head) => Some(self.load_pointer_for_head_inner(
                keyring,
                head,
                #[cfg(test)]
                probe.as_deref_mut(),
            )?),
            None => None,
        };
        let receipt_head = match (committed, receipt, pointers.complete) {
            (None, Some((receipt_head, receipt_catalog)), None)
                if receipt_catalog.cutover_marker().is_some() =>
            {
                let encoded_head = encode_head(&receipt_head)?;
                self.publish_pointer_and_revalidate(
                    HEAD_FILE,
                    &encoded_head,
                    |dir, target, payload| {
                        let mut pointer_hook = |phase| {
                            hook(CommitFailurePoint::Publication {
                                target: PublicationTarget::AuthoritativeHead,
                                phase,
                            })
                        };
                        atomic_publish_in_with_hook(dir, target, payload, &mut pointer_hook)?;
                        Ok(())
                    },
                    Self::validate_pinned_layout,
                )?;
                receipt_head
            }
            (Some((head, catalog)), Some((receipt_head, receipt_catalog)), None)
                if cutover_lineage_is_valid(&head, &catalog, &receipt_head, &receipt_catalog) =>
            {
                receipt_head
            }
            (Some((head, catalog)), None, None) if catalog.cutover_marker().is_some() => {
                let encoded_head = encode_head(&head)?;
                self.validate_pinned_layout()?;
                let mut receipt_hook = |phase| {
                    hook(CommitFailurePoint::Publication {
                        target: PublicationTarget::CutoverReceipt,
                        phase,
                    })
                };
                publish_immutable_in_with_hook(
                    &self.fs_dir,
                    OsStr::new(CUTOVER_RECEIPT_FILE),
                    &encoded_head,
                    &mut receipt_hook,
                )?;
                self.validate_pinned_layout()?;
                head
            }
            _ => return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt),
        };

        let encoded_head = encode_head(&receipt_head)?;
        self.validate_pinned_layout()?;
        let mut complete_hook = |phase| {
            hook(CommitFailurePoint::Publication {
                target: PublicationTarget::CutoverComplete,
                phase,
            })
        };
        publish_immutable_in_with_hook(
            &self.fs_dir,
            OsStr::new(CUTOVER_COMPLETE_FILE),
            &encoded_head,
            &mut complete_hook,
        )?;
        self.validate_pinned_layout()?;
        let final_pointers = self.load_pointer_set(
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;
        self.load_committed_once_with_keyring_heads_inner(
            keyring,
            final_pointers.head,
            final_pointers.receipt,
            final_pointers.complete,
            #[cfg(test)]
            probe,
        )
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
        let Some(head) = self.load_pointer_head(pointer_name)? else {
            return Ok(None);
        };
        self.load_pointer_for_head(&FrkKeyring::single(keys), head)
            .map(Some)
    }

    fn load_pointer_set(
        &self,
        #[cfg(test)] mut probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<PointerSet, CommitError> {
        Ok(PointerSet {
            head: self.load_pointer_head_inner(
                HEAD_FILE,
                #[cfg(test)]
                probe.as_deref_mut(),
            )?,
            receipt: self.load_pointer_head_inner(
                CUTOVER_RECEIPT_FILE,
                #[cfg(test)]
                probe.as_deref_mut(),
            )?,
            complete: self.load_pointer_head_inner(
                CUTOVER_COMPLETE_FILE,
                #[cfg(test)]
                probe,
            )?,
        })
    }

    fn load_pointer_head(&self, pointer_name: &str) -> Result<Option<HeadRecord>, CommitError> {
        self.load_pointer_head_inner(
            pointer_name,
            #[cfg(test)]
            None,
        )
    }

    fn load_pointer_head_inner(
        &self,
        pointer_name: &str,
        #[cfg(test)] probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<Option<HeadRecord>, CommitError> {
        #[cfg(test)]
        if let Some(probe) = probe {
            probe.pointer_read();
        }
        let encoded_head =
            match read_bounded_in(&self.fs_dir, OsStr::new(pointer_name), MAX_HEAD_SIZE) {
                Ok(encoded) => encoded,
                Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
                Err(error) => return Err(error.into()),
            };
        Ok(Some(decode_head(&encoded_head)?))
    }

    fn load_pointer_for_head(
        &self,
        keyring: &FrkKeyring<'_>,
        head: HeadRecord,
    ) -> Result<(HeadRecord, CatalogGeneration), CommitError> {
        self.load_pointer_for_head_inner(
            keyring,
            head,
            #[cfg(test)]
            None,
        )
    }

    fn load_pointer_for_head_inner(
        &self,
        keyring: &FrkKeyring<'_>,
        head: HeadRecord,
        #[cfg(test)] mut probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<(HeadRecord, CatalogGeneration), CommitError> {
        let keys = keyring.require(head.required_frk_version())?;
        let catalog_name = format!(
            "catalog-{:020}-{}.acore",
            head.generation(),
            head.catalog_hash()
        );
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.catalog_file_read();
        }
        let encrypted_catalog = read_bounded_in(
            &self.catalogs_dir,
            OsStr::new(&catalog_name),
            MAX_CATALOG_ENVELOPE_SIZE,
        )?;
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.catalog_crypto_started();
        }
        let catalog = head.verify_and_decrypt_catalog(keys, &self.core_id, &encrypted_catalog)?;
        #[cfg(test)]
        if let Some(probe) = probe {
            probe.catalog_crypto_completed();
        }
        Ok((head, catalog))
    }

    fn reauthenticate_cached_catalog_bytes(
        &self,
        pointers: &PointerSet,
        #[cfg(test)] mut probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<(), CommitError> {
        let head = pointers
            .head
            .as_ref()
            .ok_or(CommitError::AuthoritativeHeadMissingAfterCutover)?;
        self.reauthenticate_cached_catalog_record(
            head,
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;

        let receipt = pointers
            .receipt
            .as_ref()
            .ok_or(CommitError::AuthoritativeHeadViolatesCutoverReceipt)?;
        if receipt != head {
            self.reauthenticate_cached_catalog_record(
                receipt,
                #[cfg(test)]
                probe.as_deref_mut(),
            )?;
        }

        let complete = pointers
            .complete
            .as_ref()
            .ok_or(CommitError::AuthoritativeHeadViolatesCutoverReceipt)?;
        if complete != head && complete != receipt {
            self.reauthenticate_cached_catalog_record(
                complete,
                #[cfg(test)]
                probe,
            )?;
        }
        Ok(())
    }

    fn reauthenticate_cached_catalog_record(
        &self,
        head: &HeadRecord,
        #[cfg(test)] probe: Option<&mut CatalogLoadProbe<'_>>,
    ) -> Result<(), CommitError> {
        let catalog_name = format!(
            "catalog-{:020}-{}.acore",
            head.generation(),
            head.catalog_hash()
        );
        #[cfg(test)]
        if let Some(probe) = probe {
            probe.catalog_file_read();
        }
        let encrypted_catalog = read_bounded_in(
            &self.catalogs_dir,
            OsStr::new(&catalog_name),
            MAX_CATALOG_ENVELOPE_SIZE,
        )?;
        head.verify_catalog_hash(&encrypted_catalog)?;
        Ok(())
    }

    /// Rewraps recoverable Object DEKs into a complete next catalog generation
    /// and atomically activates the pending FRK version in HEAD.
    pub fn rotate_frk<I>(
        &self,
        keyring: &FrkKeyring<'_>,
        pending_keys: &FrkSubkeys,
        expected_generation: u64,
        invalidate: I,
    ) -> Result<CommitOutcome, CommitError>
    where
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
    {
        self.rotate_frk_with_hook(
            keyring,
            pending_keys,
            expected_generation,
            invalidate,
            &mut |_| Ok(()),
            #[cfg(test)]
            None,
        )
    }

    fn rotate_frk_with_hook<I, H>(
        &self,
        keyring: &FrkKeyring<'_>,
        pending_keys: &FrkSubkeys,
        expected_generation: u64,
        invalidate: I,
        hook: &mut H,
        #[cfg(test)] probe: Option<&mut RotationProbe<'_>>,
    ) -> Result<CommitOutcome, CommitError>
    where
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let _lease_operation = self.admit_lease_publication_operation()?;
        let mut lease_failure = LeaseFailureGuard::new(&self.cache);
        let result = self.rotate_frk_with_hook_inner(
            keyring,
            pending_keys,
            expected_generation,
            invalidate,
            hook,
            #[cfg(test)]
            probe,
        );
        if result.is_ok() {
            lease_failure.disarm();
        }
        result
    }

    fn rotate_frk_with_hook_inner<I, H>(
        &self,
        keyring: &FrkKeyring<'_>,
        pending_keys: &FrkSubkeys,
        expected_generation: u64,
        invalidate: I,
        hook: &mut H,
        #[cfg(test)] mut probe: Option<&mut RotationProbe<'_>>,
    ) -> Result<CommitOutcome, CommitError>
    where
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let mut deferred_teardown = DeferredLeaseTeardown::default();
        let (event, lock_hold_duration, bytes_written, catalog_plaintext_bytes) = {
            #[cfg(test)]
            let commit_lock = CoreCommitLock::acquire_in_with_post_kernel_lock_hook(
                &self.root_dir,
                &self.fs_dir,
                || {
                    if let Some(probe) = probe.as_deref_mut() {
                        probe.stage(RotationStage::KernelLock);
                    }
                },
            )?;
            #[cfg(not(test))]
            let commit_lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
            let lock_started = commit_lock.acquired_at();
            self.validate_pinned_layout()?;
            #[cfg(test)]
            let committed = {
                let mut observe_load_stage = |stage| {
                    let rotation_stage = match stage {
                        CatalogLoadStage::PointerIo => Some(RotationStage::PointerIo),
                        CatalogLoadStage::KeyDerivation => Some(RotationStage::KeyDerivation),
                        CatalogLoadStage::CatalogFileIo | CatalogLoadStage::CatalogCrypto => {
                            Some(RotationStage::CatalogIoCrypto)
                        }
                        CatalogLoadStage::KernelLock
                        | CatalogLoadStage::CacheAccess
                        | CatalogLoadStage::SecondHeadRead => None,
                    };
                    if let (Some(probe), Some(stage)) = (probe.as_deref_mut(), rotation_stage) {
                        probe.stage(stage);
                    }
                };
                let mut load_probe = CatalogLoadProbe::observed(&mut observe_load_stage);
                self.load_committed_recovering_with_keyring_and_hook_inner(
                    &commit_lock,
                    keyring,
                    &mut |_| Ok(()),
                    &mut deferred_teardown,
                    Some(&mut load_probe),
                )?
            };
            #[cfg(not(test))]
            let committed = self
                .load_committed_recovering_with_keyring(
                    &commit_lock,
                    keyring,
                    &mut deferred_teardown,
                )?
                .ok_or(CommitError::CoreNotInitialized)?;
            #[cfg(test)]
            let committed = committed.ok_or(CommitError::CoreNotInitialized)?;
            let initial_pointers = self.load_rotation_pointer_set(
                #[cfg(test)]
                probe.as_deref_mut(),
            )?;
            if initial_pointers.head.as_ref() != Some(committed.head()) {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return Err(CommitError::AuthoritativeHeadViolatesCutoverReceipt);
            }
            #[cfg(test)]
            let reauthenticated = {
                let mut observe_load_stage = |stage| {
                    let rotation_stage = match stage {
                        CatalogLoadStage::PointerIo => Some(RotationStage::PointerIo),
                        CatalogLoadStage::KeyDerivation => Some(RotationStage::KeyDerivation),
                        CatalogLoadStage::CatalogFileIo | CatalogLoadStage::CatalogCrypto => {
                            Some(RotationStage::CatalogIoCrypto)
                        }
                        CatalogLoadStage::KernelLock
                        | CatalogLoadStage::CacheAccess
                        | CatalogLoadStage::SecondHeadRead => None,
                    };
                    if let (Some(probe), Some(stage)) = (probe.as_deref_mut(), rotation_stage) {
                        probe.stage(stage);
                    }
                };
                let mut load_probe = CatalogLoadProbe::observed(&mut observe_load_stage);
                self.load_committed_once_with_keyring_heads_inner(
                    keyring,
                    initial_pointers.head.clone(),
                    initial_pointers.receipt.clone(),
                    initial_pointers.complete.clone(),
                    Some(&mut load_probe),
                )
            };
            #[cfg(not(test))]
            let reauthenticated = self.load_committed_once_with_keyring_heads_inner(
                keyring,
                initial_pointers.head.clone(),
                initial_pointers.receipt.clone(),
                initial_pointers.complete.clone(),
            );
            let committed = match reauthenticated {
                Ok(Some(committed)) => committed,
                Ok(None) => {
                    self.cache
                        .clear_deferred(deferred_teardown.cache_snapshots());
                    return Err(CommitError::CoreNotInitialized);
                }
                Err(error) => {
                    self.cache
                        .clear_deferred(deferred_teardown.cache_snapshots());
                    return Err(error);
                }
            };
            let prior_snapshot = self
                .cache
                .current_deferred(deferred_teardown.cache_snapshots())
                .filter(|snapshot| {
                    snapshot.pointers == initial_pointers
                        && snapshot.catalog().as_ref() == committed.catalog.as_ref()
                });
            deferred_teardown.retain_snapshot_lease_owner(prior_snapshot.as_ref());
            let actual_generation = committed.head.generation();
            if actual_generation != expected_generation {
                return Err(RotationError::GenerationMismatch {
                    expected: expected_generation,
                    actual: actual_generation,
                }
                .into());
            }
            let active_version = committed.head.required_frk_version();
            keyring.require(active_version)?;
            if keyring.contains(pending_keys.frk_version()) {
                keyring.require_matching(pending_keys)?;
            }
            if pending_keys.frk_version() <= active_version {
                return Err(RotationError::PendingVersionNotNewer {
                    active: active_version,
                    pending: pending_keys.frk_version(),
                }
                .into());
            }
            let expected_pending_version = active_version
                .checked_add(1)
                .ok_or(RotationError::VersionExhausted)?;
            if pending_keys.frk_version() != expected_pending_version {
                return Err(RotationError::PendingVersionNotSuccessor {
                    active: active_version,
                    pending: pending_keys.frk_version(),
                }
                .into());
            }
            if keyring.reuses_material_from_other_version(pending_keys) {
                return Err(RotationError::PendingKeyMaterialReused.into());
            }
            let next_generation = actual_generation
                .checked_add(1)
                .ok_or(RotationError::GenerationExhausted)?;
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(RotationStage::ObjectRewrap);
            }
            let next_catalog = Arc::new(committed.catalog.rewrap_for_frk_rotation(
                &self.core_id,
                keyring,
                pending_keys,
                next_generation,
            )?);
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(RotationStage::CatalogIoCrypto);
                probe.stage(RotationStage::EncryptionAndPublication);
            }
            let (head, _, recovery_pending, bytes_written, catalog_plaintext_bytes) = {
                let mut observed_hook = |point| {
                    #[cfg(test)]
                    if let Some(probe) = probe.as_deref_mut() {
                        probe.stage(RotationStage::FailureHook);
                    }
                    hook(point)
                };
                self.publish_catalog_pointer_with_hook(
                    pending_keys,
                    &next_catalog,
                    HEAD_FILE,
                    false,
                    &mut observed_hook,
                )?
            };
            debug_assert!(!recovery_pending);
            let expected_final_pointers = PointerSet {
                head: Some(head.clone()),
                receipt: initial_pointers.receipt,
                complete: initial_pointers.complete,
            };
            self.publish_rotation_cache_authority(
                RotationCacheKeyMaterial {
                    keyring,
                    pending: pending_keys,
                },
                &expected_final_pointers,
                Arc::clone(&next_catalog),
                prior_snapshot,
                &mut deferred_teardown,
                #[cfg(test)]
                probe.as_deref_mut(),
            );
            let event = InvalidationEvent {
                generation: head.generation(),
                catalog_hash: head.catalog_hash().to_owned(),
                required_frk_version: head.required_frk_version(),
            };
            let lock_hold_duration =
                measure_lock_hold_through_release(lock_started, || drop(commit_lock));
            (
                event,
                lock_hold_duration,
                bytes_written,
                catalog_plaintext_bytes,
            )
        };
        drop(deferred_teardown);

        let before_invalidation = hook(CommitFailurePoint::BeforeInvalidation);
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.stage(RotationStage::FailureHook);
        }
        if let Err(error) = before_invalidation {
            self.cache.drop_object_lease();
            return Err(error.into());
        }
        let invalidation_delivered = invalidate(event.clone()).is_ok();
        if !invalidation_delivered {
            self.cache.drop_object_lease();
        }
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.stage(RotationStage::InvalidationCallback);
        }
        let after_invalidation = hook(CommitFailurePoint::AfterInvalidation);
        #[cfg(test)]
        if let Some(probe) = probe {
            probe.stage(RotationStage::FailureHook);
        }
        if let Err(error) = after_invalidation {
            self.cache.drop_object_lease();
            return Err(error.into());
        }
        Ok(CommitOutcome {
            event,
            invalidation_delivered,
            recovery_pending: false,
            lock_hold_duration,
            bytes_written,
            catalog_plaintext_bytes,
        })
    }

    fn load_rotation_pointer_set(
        &self,
        #[cfg(test)] mut probe: Option<&mut RotationProbe<'_>>,
    ) -> Result<PointerSet, CommitError> {
        #[cfg(test)]
        {
            let mut observe_load_stage = |stage| {
                if stage == CatalogLoadStage::PointerIo {
                    if let Some(probe) = probe.as_deref_mut() {
                        probe.stage(RotationStage::PointerIo);
                    }
                }
            };
            let mut load_probe = CatalogLoadProbe::observed(&mut observe_load_stage);
            self.load_pointer_set(Some(&mut load_probe))
        }
        #[cfg(not(test))]
        {
            self.load_pointer_set()
        }
    }

    fn publish_rotation_cache_authority(
        &self,
        key_material: RotationCacheKeyMaterial<'_, '_>,
        expected_pointers: &PointerSet,
        catalog: Arc<CatalogGeneration>,
        prior_snapshot: Option<Arc<AuthenticatedCommitSnapshot>>,
        deferred_teardown: &mut DeferredLeaseTeardown,
        #[cfg(test)] mut probe: Option<&mut RotationProbe<'_>>,
    ) {
        let final_pointers = match self.load_rotation_pointer_set(
            #[cfg(test)]
            probe.as_deref_mut(),
        ) {
            Ok(pointers) => pointers,
            Err(_) => {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return;
            }
        };
        if &final_pointers != expected_pointers {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        }
        if self
            .reauthenticate_cached_catalog_bytes(
                &final_pointers,
                #[cfg(test)]
                None,
            )
            .is_err()
        {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        }
        #[cfg(test)]
        if let Some(probe) = probe {
            probe.stage(RotationStage::KeyDerivation);
        }
        let Some(key) = self.rotation_cache_lookup_key(
            &final_pointers,
            key_material.keyring,
            key_material.pending,
        ) else {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        };
        let next_bindings = match catalog_object_bindings(&catalog) {
            Ok(bindings) => bindings,
            Err(_) => {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return;
            }
        };
        let objects = match ValidatedObjectState::from_catalog_bindings(next_bindings.clone()) {
            Ok(objects) => Arc::new(objects),
            Err(_) => {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return;
            }
        };
        let mut object_lease = prior_snapshot.and_then(|snapshot| {
            let lease = snapshot.object_lease.as_ref()?;
            let old_bindings = catalog_object_bindings(snapshot.catalog()).ok()?;
            let directory_identity = match self.object_directory_identity() {
                Ok(identity) => identity,
                Err(_) => {
                    lease.publish_unknown();
                    return None;
                }
            };
            let carried =
                lease.try_rebind_after_rotation(&old_bindings, next_bindings, directory_identity);
            if carried.is_none() {
                lease.publish_unknown();
            }
            carried
        });
        if object_lease
            .as_ref()
            .is_some_and(|lease| !self.object_lease_can_publish(lease))
        {
            if let Some(lease) = object_lease.take() {
                lease.begin_release();
                deferred_teardown.retain_lease(&lease);
            }
        }
        self.cache.replace_deferred(
            Arc::new(
                AuthenticatedCommitSnapshot::new(&key, catalog, Some(objects))
                    .with_object_lease(object_lease),
            ),
            deferred_teardown.cache_snapshots(),
        );
    }

    fn rotation_cache_lookup_key(
        &self,
        pointers: &PointerSet,
        keyring: &FrkKeyring<'_>,
        pending_keys: &FrkSubkeys,
    ) -> Option<CacheLookupKey> {
        if !pointers.is_complete_non_recovery_shape() {
            return None;
        }
        let mut versions = pointers.required_frk_versions();
        versions.sort_unstable();
        versions.dedup();
        let selected = versions
            .into_iter()
            .map(|version| {
                if version == pending_keys.frk_version() {
                    Some(pending_keys)
                } else {
                    keyring.require(version).ok()
                }
            })
            .collect::<Option<Vec<_>>>()?;
        let complete_keyring = FrkKeyring::new(selected).ok()?;
        CacheLookupKey::derive(
            pointers.clone(),
            &self.core_id,
            &complete_keyring,
            pending_keys,
        )
        .ok()
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
        self.initialize_validation_snapshot_with_hook(
            keys,
            prepared_revisions,
            build_next,
            &mut |_| Ok(()),
        )
    }

    fn initialize_validation_snapshot_with_hook<B, H>(
        &self,
        keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        build_next: B,
        hook: &mut H,
    ) -> Result<ValidationSnapshot, CommitError>
    where
        B: FnOnce(u64) -> Result<CatalogGeneration, CatalogError>,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let _lease_operation = self.admit_lease_publication_operation()?;
        let mut deferred_teardown = DeferredLeaseTeardown::default();
        let commit_lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        if self
            .load_committed_recovering(&commit_lock, keys, &mut deferred_teardown)?
            .is_some()
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
            (None, &catalog),
            prepared_revisions,
            None,
            #[cfg(test)]
            None,
        )?;
        let (head, _, _, _, _) = self.publish_catalog_pointer_with_hook(
            keys,
            &catalog,
            VALIDATION_HEAD_FILE,
            false,
            hook,
        )?;
        Ok(ValidationSnapshot { head, catalog })
    }

    pub(crate) fn advance_validation_snapshot<B>(
        &self,
        keys: &FrkSubkeys,
        selected: &ValidationSnapshot,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        build_next: B,
    ) -> Result<ValidationSnapshot, CommitError>
    where
        B: FnOnce(&CatalogGeneration, u64) -> Result<CatalogGeneration, CatalogError>,
    {
        self.advance_validation_snapshot_with_hook(
            keys,
            selected,
            prepared_revisions,
            preconditions,
            build_next,
            &mut |_| Ok(()),
        )
    }

    fn advance_validation_snapshot_with_hook<B, H>(
        &self,
        keys: &FrkSubkeys,
        selected: &ValidationSnapshot,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        build_next: B,
        hook: &mut H,
    ) -> Result<ValidationSnapshot, CommitError>
    where
        B: FnOnce(&CatalogGeneration, u64) -> Result<CatalogGeneration, CatalogError>,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let _lease_operation = self.admit_lease_publication_operation()?;
        let mut deferred_teardown = DeferredLeaseTeardown::default();
        let commit_lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        if self
            .load_committed_recovering(&commit_lock, keys, &mut deferred_teardown)?
            .is_some()
        {
            return Err(CommitError::CutoverAlreadyCommitted);
        }
        let current = self
            .load_validation_snapshot(keys)?
            .ok_or(CommitError::CoreNotInitialized)?;
        if current.head != selected.head || current.catalog != selected.catalog {
            return Err(CommitConflict::SelectedValidationChanged.into());
        }
        if current.catalog.cutover_marker().is_some() {
            return Err(CommitError::InvalidCutoverTransition);
        }
        validate_preconditions(Some(&current.catalog), preconditions)?;
        let next_generation = current
            .head
            .generation()
            .checked_add(1)
            .ok_or(CommitError::GenerationExhausted)?;
        let next_catalog = build_next(&current.catalog, next_generation)?;
        if next_catalog.generation() != next_generation {
            return Err(CommitError::WrongNextGeneration {
                expected: next_generation,
                actual: next_catalog.generation(),
            });
        }
        if next_catalog.cutover_marker().is_some() {
            return Err(CommitError::InvalidCutoverTransition);
        }
        validate_precondition_coverage(&current.catalog, &next_catalog, preconditions)?;
        validate_prepared_revisions(
            &self.objects_dir,
            keys,
            &self.core_id,
            (Some(&current.catalog), &next_catalog),
            prepared_revisions,
            None,
            #[cfg(test)]
            None,
        )?;
        let (head, _, _, _, _) = self.publish_catalog_pointer_with_hook(
            keys,
            &next_catalog,
            VALIDATION_HEAD_FILE,
            false,
            hook,
        )?;
        Ok(ValidationSnapshot {
            head,
            catalog: next_catalog,
        })
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

    #[cfg(test)]
    fn commit_with_probe<B, I>(
        &self,
        keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        build_next: B,
        invalidate: I,
        probe: &mut CommitProbe<'_>,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
    {
        let keyring = FrkKeyring::single(keys);
        let mut hook = |_| Ok(());
        self.commit_internal_with_keyring_and_hook(
            &keyring,
            keys,
            prepared_revisions,
            preconditions,
            CommitMode::Normal,
            build_next,
            CommitCallbacks {
                invalidate,
                hook: &mut hook,
            },
            Some(probe),
        )
    }

    /// Commits a normal catalog generation while retained cutover pointers may
    /// still require older FRK versions. `active_keys` encrypts the new catalog.
    pub fn commit_with_keyring<B, I>(
        &self,
        keyring: &FrkKeyring<'_>,
        active_keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        build_next: B,
        invalidate: I,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
    {
        keyring.require_matching(active_keys)?;
        let mut hook = |_| Ok(());
        self.commit_internal_with_keyring_and_hook(
            keyring,
            active_keys,
            prepared_revisions,
            preconditions,
            CommitMode::Normal,
            build_next,
            CommitCallbacks {
                invalidate,
                hook: &mut hook,
            },
            #[cfg(test)]
            None,
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
        let mut hook = |_| Ok(());
        self.commit_internal_with_hook(
            keys,
            prepared_revisions,
            preconditions,
            mode,
            build_next,
            CommitCallbacks {
                invalidate,
                hook: &mut hook,
            },
        )
    }

    fn commit_internal_with_hook<B, I, H>(
        &self,
        keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        mode: CommitMode,
        build_next: B,
        callbacks: CommitCallbacks<'_, I, H>,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let keyring = FrkKeyring::single(keys);
        self.commit_internal_with_keyring_and_hook(
            &keyring,
            keys,
            prepared_revisions,
            preconditions,
            mode,
            build_next,
            callbacks,
            #[cfg(test)]
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn commit_internal_with_keyring_and_hook<B, I, H>(
        &self,
        keyring: &FrkKeyring<'_>,
        active_keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        mode: CommitMode,
        build_next: B,
        callbacks: CommitCallbacks<'_, I, H>,
        #[cfg(test)] probe: Option<&mut CommitProbe<'_>>,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let _lease_operation = self.admit_lease_publication_operation()?;
        let mut lease_failure = LeaseFailureGuard::new(&self.cache);
        let result = self.commit_internal_with_keyring_and_hook_inner(
            keyring,
            active_keys,
            prepared_revisions,
            preconditions,
            mode,
            build_next,
            callbacks,
            #[cfg(test)]
            probe,
        );
        if result.is_ok() {
            lease_failure.disarm();
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    fn commit_internal_with_keyring_and_hook_inner<B, I, H>(
        &self,
        keyring: &FrkKeyring<'_>,
        active_keys: &FrkSubkeys,
        prepared_revisions: &[PreparedObjectRevision],
        preconditions: &[CatalogPrecondition],
        mode: CommitMode,
        build_next: B,
        callbacks: CommitCallbacks<'_, I, H>,
        #[cfg(test)] mut probe: Option<&mut CommitProbe<'_>>,
    ) -> Result<CommitOutcome, CommitError>
    where
        B: FnOnce(Option<&CatalogGeneration>, u64) -> Result<CatalogGeneration, CatalogError>,
        I: FnOnce(InvalidationEvent) -> Result<(), String>,
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        let mut deferred_teardown = DeferredLeaseTeardown::default();
        let (event, recovery_pending, lock_hold_duration, bytes_written, catalog_plaintext_bytes) = {
            #[cfg(test)]
            let commit_lock = CoreCommitLock::acquire_in_with_post_kernel_lock_hook(
                &self.root_dir,
                &self.fs_dir,
                || {
                    if let Some(probe) = probe.as_deref_mut() {
                        probe.stage(CommitStage::KernelLock);
                    }
                },
            )?;
            #[cfg(not(test))]
            let commit_lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
            let lock_started = commit_lock.acquired_at();
            self.validate_pinned_layout()?;
            #[cfg(test)]
            let authoritative = {
                let (result, counts) = {
                    let mut observe_load_stage = |stage| {
                        let commit_stage = match stage {
                            CatalogLoadStage::PointerIo => Some(CommitStage::PointerIo),
                            CatalogLoadStage::KeyDerivation => Some(CommitStage::KeyDerivation),
                            CatalogLoadStage::CatalogFileIo | CatalogLoadStage::CatalogCrypto => {
                                Some(CommitStage::CatalogIoCrypto)
                            }
                            CatalogLoadStage::KernelLock
                            | CatalogLoadStage::CacheAccess
                            | CatalogLoadStage::SecondHeadRead => None,
                        };
                        if let (Some(probe), Some(stage)) = (probe.as_deref_mut(), commit_stage) {
                            probe.stage(stage);
                        }
                    };
                    let mut load_probe = CatalogLoadProbe::observed(&mut observe_load_stage);
                    let result = self.load_committed_recovering_with_keyring_and_hook_inner(
                        &commit_lock,
                        keyring,
                        &mut |_| Ok(()),
                        &mut deferred_teardown,
                        Some(&mut load_probe),
                    );
                    let counts = (
                        load_probe.pointer_reads,
                        load_probe.catalog_file_reads,
                        load_probe.catalog_decrypts,
                        load_probe.catalog_encodes,
                    );
                    (result, counts)
                };
                if let Some(probe) = probe.as_deref_mut() {
                    probe.absorb_catalog_load_counts(counts.0, counts.1, counts.2, counts.3);
                }
                result?
            };
            #[cfg(not(test))]
            let authoritative = self.load_committed_recovering_with_keyring(
                &commit_lock,
                keyring,
                &mut deferred_teardown,
            )?;
            let initial_snapshot = self
                .cache
                .current_deferred(deferred_teardown.cache_snapshots());
            let initial_pointers = initial_snapshot
                .as_ref()
                .map_or_else(PointerSet::default, |snapshot| snapshot.pointers.clone());
            let matched_snapshot = match (mode, authoritative.as_ref(), initial_snapshot.as_ref()) {
                (CommitMode::Normal, Some(authoritative), Some(snapshot))
                    if snapshot.pointers.head.as_ref() == Some(&authoritative.head)
                        && Arc::ptr_eq(snapshot.catalog(), &authoritative.catalog) =>
                {
                    CacheLookupKey::derive(
                        initial_pointers.clone(),
                        &self.core_id,
                        keyring,
                        active_keys,
                    )
                    .ok()
                    .and_then(|key| {
                        self.cache
                            .get_deferred(&key, deferred_teardown.cache_snapshots())
                    })
                    .filter(|matched| Arc::ptr_eq(matched, snapshot))
                }
                _ => None,
            };
            deferred_teardown.retain_snapshot_lease_owner(initial_snapshot.as_ref());
            deferred_teardown.retain_snapshot_lease_owner(matched_snapshot.as_ref());
            let cached_objects = matched_snapshot
                .as_ref()
                .and_then(|snapshot| snapshot.objects.clone());
            let current = match mode {
                CommitMode::FirstMutation { .. } => {
                    if authoritative.is_some() {
                        return Err(CommitError::CutoverAlreadyCommitted);
                    }
                    let validation = self
                        .load_validation_snapshot(active_keys)?
                        .ok_or(CommitError::CoreNotInitialized)?;
                    CommittedCatalog {
                        head: validation.head,
                        catalog: Arc::new(validation.catalog),
                    }
                }
                CommitMode::Normal => {
                    if let Some(authoritative) = authoritative {
                        authoritative
                    } else if self.load_validation_snapshot(active_keys)?.is_some() {
                        return Err(CommitError::CutoverAuthorizationRequired);
                    } else {
                        return Err(CommitError::CoreNotInitialized);
                    }
                }
            };
            if active_keys.frk_version() != current.head.required_frk_version() {
                return Err(RotationError::ActiveVersionMismatch {
                    expected: current.head.required_frk_version(),
                    actual: active_keys.frk_version(),
                }
                .into());
            }
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
            let expected_bindings = catalog_object_bindings(&next_catalog)?;
            let had_cached_lease = matched_snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.object_lease.is_some());
            let carried = if prepared_revisions.is_empty() {
                matched_snapshot.as_ref().and_then(|snapshot| {
                    let lease = snapshot.object_lease.as_ref()?;
                    let Some(objects) = snapshot.objects.as_ref() else {
                        lease.publish_unknown();
                        return None;
                    };
                    if !lease.matches_object_tuple(&expected_bindings) {
                        lease.publish_dirty();
                        return None;
                    }
                    let directory_identity = match self.object_directory_identity() {
                        Ok(identity) => identity,
                        Err(_) => {
                            lease.publish_unknown();
                            return None;
                        }
                    };
                    #[cfg(test)]
                    if let Some(probe) = probe.as_deref_mut() {
                        probe.stage(CommitStage::LeaseFence);
                    }
                    lease
                        .try_carry_forward(&expected_bindings, directory_identity)
                        .map(|lease| (Arc::clone(objects), lease))
                })
            } else {
                if let Some(lease) = matched_snapshot
                    .as_ref()
                    .and_then(|snapshot| snapshot.object_lease.as_ref())
                {
                    lease.publish_dirty();
                }
                None
            };
            let (validated_objects, candidate_lease) = if let Some((objects, lease)) = carried {
                (objects, Some(lease))
            } else {
                if had_cached_lease {
                    self.cache
                        .clear_deferred(deferred_teardown.cache_snapshots());
                }
                drop(matched_snapshot);
                drop(initial_snapshot);
                let (objects, candidate) = self.validate_prepared_revisions_with_candidate(
                    active_keys,
                    current.catalog(),
                    &next_catalog,
                    prepared_revisions,
                    cached_objects.as_deref(),
                    &initial_pointers,
                    &mut deferred_teardown,
                    #[cfg(test)]
                    probe.as_deref_mut(),
                )?;
                (Arc::new(objects), candidate)
            };
            if let Some(lease) = candidate_lease.as_ref() {
                deferred_teardown.retain_lease(lease);
            }
            let next_catalog = Arc::new(next_catalog);
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CommitStage::PreconditionAndBuild);
            }
            let mut authoritative_head_synced = false;
            let publication = {
                let mut observed_hook = |point| {
                    if point
                        == (CommitFailurePoint::Publication {
                            target: PublicationTarget::AuthoritativeHead,
                            phase: PublicationPhase::DestinationSynced,
                        })
                    {
                        authoritative_head_synced = true;
                    }
                    #[cfg(test)]
                    {
                        observe_commit_failure_hook((callbacks.hook)(point), probe.as_deref_mut())
                    }
                    #[cfg(not(test))]
                    {
                        (callbacks.hook)(point)
                    }
                };
                self.publish_catalog_pointer_with_hook(
                    active_keys,
                    &next_catalog,
                    HEAD_FILE,
                    matches!(mode, CommitMode::FirstMutation { .. }),
                    &mut observed_hook,
                )
            };
            let (head, _, recovery_pending, bytes_written, catalog_plaintext_bytes) =
                match publication {
                    Ok(publication) => publication,
                    Err(error) => {
                        if authoritative_head_synced {
                            self.reconcile_cache_after_commit_publication_error(
                                &initial_pointers,
                                keyring,
                                mode,
                                &mut deferred_teardown,
                                #[cfg(test)]
                                probe.as_deref_mut(),
                            );
                        }
                        return Err(error);
                    }
                };
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CommitStage::EncryptionAndPublication);
            }
            let expected_final_pointers = PointerSet {
                head: Some(head.clone()),
                receipt: match mode {
                    CommitMode::Normal => initial_pointers.receipt.clone(),
                    CommitMode::FirstMutation { .. } => Some(head.clone()),
                },
                complete: match mode {
                    CommitMode::Normal => initial_pointers.complete.clone(),
                    CommitMode::FirstMutation { .. } => Some(head.clone()),
                },
            };
            self.publish_commit_cache_authority(
                keyring,
                &expected_final_pointers,
                Arc::clone(&next_catalog),
                validated_objects,
                candidate_lease,
                recovery_pending,
                &mut deferred_teardown,
                #[cfg(test)]
                probe.as_deref_mut(),
            );
            let event = InvalidationEvent {
                generation: head.generation(),
                catalog_hash: head.catalog_hash().to_owned(),
                required_frk_version: head.required_frk_version(),
            };
            let lock_hold_duration =
                measure_lock_hold_through_release(lock_started, || drop(commit_lock));
            (
                event,
                recovery_pending,
                lock_hold_duration,
                bytes_written,
                catalog_plaintext_bytes,
            )
        };
        drop(deferred_teardown);

        let before_invalidation = (callbacks.hook)(CommitFailurePoint::BeforeInvalidation);
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.stage(CommitStage::FailureHook);
        }
        if let Err(error) = before_invalidation {
            self.cache.drop_object_lease();
            return Err(error.into());
        }
        let invalidation_result = (callbacks.invalidate)(event.clone());
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.stage(CommitStage::InvalidationCallback);
        }
        let invalidation_delivered = invalidation_result.is_ok();
        if !invalidation_delivered {
            self.cache.drop_object_lease();
        }
        let after_invalidation = (callbacks.hook)(CommitFailurePoint::AfterInvalidation);
        #[cfg(test)]
        if let Some(probe) = probe {
            probe.stage(CommitStage::FailureHook);
        }
        if let Err(error) = after_invalidation {
            self.cache.drop_object_lease();
            return Err(error.into());
        }
        Ok(CommitOutcome {
            event,
            invalidation_delivered,
            recovery_pending,
            lock_hold_duration,
            bytes_written,
            catalog_plaintext_bytes,
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn publish_commit_cache_authority(
        &self,
        keyring: &FrkKeyring<'_>,
        expected_pointers: &PointerSet,
        catalog: Arc<CatalogGeneration>,
        objects: Arc<ValidatedObjectState>,
        object_lease: Option<Arc<ObjectValidationLease>>,
        recovery_pending: bool,
        deferred_teardown: &mut DeferredLeaseTeardown,
        #[cfg(test)] mut probe: Option<&mut CommitProbe<'_>>,
    ) {
        if recovery_pending {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        }
        let final_pointers = match self.load_commit_pointer_set(
            #[cfg(test)]
            probe.as_deref_mut(),
        ) {
            Ok(pointers) => pointers,
            Err(_) => {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return;
            }
        };
        if &final_pointers != expected_pointers {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        }
        let Some(key) = self.commit_cache_lookup_key(
            &final_pointers,
            keyring,
            #[cfg(test)]
            probe,
        ) else {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        };
        if self
            .reauthenticate_cached_catalog_bytes(
                &final_pointers,
                #[cfg(test)]
                None,
            )
            .is_err()
        {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        }
        let mut object_lease = object_lease;
        if object_lease
            .as_ref()
            .is_some_and(|lease| !self.object_lease_can_publish(lease))
        {
            if let Some(lease) = object_lease.take() {
                lease.begin_release();
                deferred_teardown.retain_lease(&lease);
            }
        }
        self.cache.replace_deferred(
            Arc::new(
                AuthenticatedCommitSnapshot::new(&key, catalog, Some(objects))
                    .with_object_lease(object_lease),
            ),
            deferred_teardown.cache_snapshots(),
        );
    }

    fn reconcile_cache_after_commit_publication_error(
        &self,
        initial_pointers: &PointerSet,
        keyring: &FrkKeyring<'_>,
        mode: CommitMode,
        deferred_teardown: &mut DeferredLeaseTeardown,
        #[cfg(test)] mut probe: Option<&mut CommitProbe<'_>>,
    ) {
        let final_pointers = match self.load_commit_pointer_set(
            #[cfg(test)]
            probe.as_deref_mut(),
        ) {
            Ok(pointers) => pointers,
            Err(_) => {
                self.cache
                    .clear_deferred(deferred_teardown.cache_snapshots());
                return;
            }
        };
        if &final_pointers == initial_pointers {
            return;
        }
        if matches!(mode, CommitMode::FirstMutation { .. })
            || self.validate_pinned_layout().is_err()
        {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        }
        let authenticated = self.load_committed_once_with_keyring_heads_inner(
            keyring,
            final_pointers.head.clone(),
            final_pointers.receipt.clone(),
            final_pointers.complete.clone(),
            #[cfg(test)]
            None,
        );
        let Ok(Some(committed)) = authenticated else {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        };
        let Some(key) = self.commit_cache_lookup_key(
            &final_pointers,
            keyring,
            #[cfg(test)]
            probe,
        ) else {
            self.cache
                .clear_deferred(deferred_teardown.cache_snapshots());
            return;
        };
        self.cache.replace_deferred(
            Arc::new(AuthenticatedCommitSnapshot::new(
                &key,
                Arc::clone(&committed.catalog),
                None,
            )),
            deferred_teardown.cache_snapshots(),
        );
    }

    fn commit_cache_lookup_key(
        &self,
        pointers: &PointerSet,
        keyring: &FrkKeyring<'_>,
        #[cfg(test)] probe: Option<&mut CommitProbe<'_>>,
    ) -> Option<CacheLookupKey> {
        let head = pointers.head.as_ref()?;
        let active_keys = keyring.require(head.required_frk_version()).ok()?;
        #[cfg(test)]
        {
            observe_commit_key_derivation(
                CacheLookupKey::derive(pointers.clone(), &self.core_id, keyring, active_keys).ok(),
                probe,
            )
        }
        #[cfg(not(test))]
        {
            CacheLookupKey::derive(pointers.clone(), &self.core_id, keyring, active_keys).ok()
        }
    }

    fn load_commit_pointer_set(
        &self,
        #[cfg(test)] mut probe: Option<&mut CommitProbe<'_>>,
    ) -> Result<PointerSet, CommitError> {
        #[cfg(test)]
        {
            let (result, pointer_reads) = {
                let mut observe_load_stage = |stage| {
                    if stage == CatalogLoadStage::PointerIo {
                        if let Some(probe) = probe.as_deref_mut() {
                            probe.stage(CommitStage::PointerIo);
                        }
                    }
                };
                let mut load_probe = CatalogLoadProbe::observed(&mut observe_load_stage);
                let result = self.load_pointer_set(Some(&mut load_probe));
                (result, load_probe.pointer_reads)
            };
            if let Some(probe) = probe {
                probe.pointer_reads += pointer_reads;
            }
            result
        }
        #[cfg(not(test))]
        {
            self.load_pointer_set()
        }
    }

    #[cfg(not(test))]
    fn publish_catalog_pointer_with_hook<H>(
        &self,
        keys: &FrkSubkeys,
        catalog: &CatalogGeneration,
        pointer_name: &str,
        publish_cutover_receipt: bool,
        hook: &mut H,
    ) -> Result<(HeadRecord, String, bool, u64, usize), CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        self.publish_catalog_pointer_with_hook_inner(
            keys,
            catalog,
            pointer_name,
            publish_cutover_receipt,
            hook,
        )
    }

    #[cfg(test)]
    fn publish_catalog_pointer_with_hook<H>(
        &self,
        keys: &FrkSubkeys,
        catalog: &CatalogGeneration,
        pointer_name: &str,
        publish_cutover_receipt: bool,
        hook: &mut H,
    ) -> Result<(HeadRecord, String, bool, u64, usize), CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        self.publish_catalog_pointer_with_hook_inner(
            keys,
            catalog,
            pointer_name,
            publish_cutover_receipt,
            hook,
            None,
        )
    }

    #[cfg(test)]
    fn publish_catalog_pointer_with_hook_observed<H>(
        &self,
        keys: &FrkSubkeys,
        catalog: &CatalogGeneration,
        pointer_name: &str,
        publish_cutover_receipt: bool,
        hook: &mut H,
        probe: &mut CoordinatorPublicationProbe,
    ) -> Result<(HeadRecord, String, bool, u64, usize), CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        self.publish_catalog_pointer_with_hook_inner(
            keys,
            catalog,
            pointer_name,
            publish_cutover_receipt,
            hook,
            Some(probe),
        )
    }

    fn publish_catalog_pointer_with_hook_inner<H>(
        &self,
        keys: &FrkSubkeys,
        catalog: &CatalogGeneration,
        pointer_name: &str,
        publish_cutover_receipt: bool,
        hook: &mut H,
        #[cfg(test)] mut probe: Option<&mut CoordinatorPublicationProbe>,
    ) -> Result<(HeadRecord, String, bool, u64, usize), CommitError>
    where
        H: FnMut(CommitFailurePoint) -> io::Result<()>,
    {
        #[cfg(test)]
        let publication = match probe.as_deref_mut() {
            Some(probe) => {
                let mut observe_hash = || probe.ciphertext_hashes += 1;
                encrypt_catalog_generation_for_publication_with_observer(
                    keys,
                    &self.core_id,
                    catalog,
                    &mut observe_hash,
                )?
            }
            None => encrypt_catalog_generation_for_publication(keys, &self.core_id, catalog)?,
        };
        #[cfg(not(test))]
        let publication = encrypt_catalog_generation_for_publication(keys, &self.core_id, catalog)?;
        let catalog_name = publication.physical_name().to_owned();
        self.validate_pinned_layout()?;
        let mut catalog_hook = |phase| {
            hook(CommitFailurePoint::Publication {
                target: PublicationTarget::Catalog,
                phase,
            })
        };
        publish_immutable_in_with_hook(
            &self.catalogs_dir,
            OsStr::new(&catalog_name),
            publication.encrypted(),
            &mut catalog_hook,
        )?;
        #[cfg(test)]
        let head = match probe {
            Some(probe) => {
                let mut observe_decrypt = || probe.catalog_decrypts += 1;
                HeadRecord::new_for_publication_with_observer(
                    keys,
                    &self.core_id,
                    catalog,
                    &publication,
                    keys.frk_version(),
                    &mut observe_decrypt,
                )?
            }
            None => HeadRecord::new_for_publication(
                keys,
                &self.core_id,
                catalog,
                &publication,
                keys.frk_version(),
            )?,
        };
        #[cfg(not(test))]
        let head = HeadRecord::new_for_publication(
            keys,
            &self.core_id,
            catalog,
            &publication,
            keys.frk_version(),
        )?;
        let encoded_head = encode_head(&head)?;
        let head_bytes =
            u64::try_from(encoded_head.len()).map_err(|_| CommitError::GenerationExhausted)?;
        let mut bytes_written = u64::try_from(publication.encrypted().len())
            .map_err(|_| CommitError::GenerationExhausted)?
            .checked_add(head_bytes)
            .ok_or(CommitError::GenerationExhausted)?;
        let pointer_target = if pointer_name == HEAD_FILE {
            PublicationTarget::AuthoritativeHead
        } else {
            PublicationTarget::ValidationHead
        };
        self.publish_pointer_and_revalidate(
            pointer_name,
            &encoded_head,
            |dir, target, payload| {
                let mut pointer_hook = |phase| {
                    hook(CommitFailurePoint::Publication {
                        target: pointer_target,
                        phase,
                    })
                };
                atomic_publish_in_with_hook(dir, target, payload, &mut pointer_hook)?;
                Ok(())
            },
            Self::validate_pinned_layout,
        )?;
        let recovery_pending = publish_cutover_receipt
            && (|| -> Result<(), CommitError> {
                self.validate_pinned_layout()?;
                let mut receipt_hook = |phase| {
                    hook(CommitFailurePoint::Publication {
                        target: PublicationTarget::CutoverReceipt,
                        phase,
                    })
                };
                publish_immutable_in_with_hook(
                    &self.fs_dir,
                    OsStr::new(CUTOVER_RECEIPT_FILE),
                    &encoded_head,
                    &mut receipt_hook,
                )?;
                bytes_written = bytes_written
                    .checked_add(head_bytes)
                    .ok_or(CommitError::GenerationExhausted)?;
                self.validate_pinned_layout()?;
                let mut complete_hook = |phase| {
                    hook(CommitFailurePoint::Publication {
                        target: PublicationTarget::CutoverComplete,
                        phase,
                    })
                };
                publish_immutable_in_with_hook(
                    &self.fs_dir,
                    OsStr::new(CUTOVER_COMPLETE_FILE),
                    &encoded_head,
                    &mut complete_hook,
                )?;
                bytes_written = bytes_written
                    .checked_add(head_bytes)
                    .ok_or(CommitError::GenerationExhausted)?;
                self.validate_pinned_layout()?;
                Ok(())
            })()
            .is_err();
        Ok((
            head,
            catalog_name,
            recovery_pending,
            bytes_written,
            publication.plaintext_size(),
        ))
    }

    fn publish_pointer_and_revalidate<P, V>(
        &self,
        pointer_name: &str,
        payload: &[u8],
        publish_pointer: P,
        validate_layout: V,
    ) -> Result<(), CommitError>
    where
        P: FnOnce(&Dir, &OsStr, &[u8]) -> Result<(), CommitError>,
        V: Fn(&Self) -> Result<(), CommitError>,
    {
        validate_layout(self)?;
        publish_pointer(&self.fs_dir, OsStr::new(pointer_name), payload)?;
        validate_layout(self)
    }

    fn validate_pinned_layout(&self) -> Result<(), CommitError> {
        validate_ambient_linked_directory(&self.core_root, &self.root_dir)?;
        validate_linked_directory(&self.root_dir, FS_DIRECTORY, &self.fs_dir)?;
        validate_linked_directory(&self.fs_dir, CATALOGS_DIRECTORY, &self.catalogs_dir)?;
        validate_linked_directory(&self.root_dir, OBJECTS_DIRECTORY, &self.objects_dir)
    }
}

fn cutover_lineage_is_valid(
    head: &HeadRecord,
    catalog: &CatalogGeneration,
    receipt_head: &HeadRecord,
    receipt_catalog: &CatalogGeneration,
) -> bool {
    let receipt_marker = receipt_catalog.cutover_marker();
    receipt_marker.is_some()
        && catalog.cutover_marker() == receipt_marker
        && head.generation() >= receipt_head.generation()
        && (head.generation() != receipt_head.generation() || head == receipt_head)
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
    validate_precondition_coverage_ordered(current, next, preconditions)
}

fn validate_precondition_coverage_ordered(
    current: &CatalogGeneration,
    next: &CatalogGeneration,
    preconditions: &[CatalogPrecondition],
) -> Result<(), CommitError> {
    let current_entries = current.entries();
    let next_entries = next.entries();
    let vacant_destinations: HashSet<(&str, &str)> = preconditions
        .iter()
        .filter_map(|precondition| match precondition {
            CatalogPrecondition::Vacant(expected)
                if catalog_contains_stable_id(
                    current_entries,
                    expected.parent_path.stable_id(),
                ) =>
            {
                Some((
                    expected.parent_path.stable_id().as_str(),
                    expected.name.as_str(),
                ))
            }
            CatalogPrecondition::Object(_)
            | CatalogPrecondition::Folder(_)
            | CatalogPrecondition::Vacant(_) => None,
        })
        .collect();

    let mut current_index = 0;
    let mut next_index = 0;
    let mut missing_destination = None;

    while current_index < current_entries.len() || next_index < next_entries.len() {
        match (
            current_entries.get(current_index),
            next_entries.get(next_index),
        ) {
            (Some(current_entry), Some(next_entry)) => {
                match current_entry
                    .stable_id()
                    .as_str()
                    .cmp(next_entry.stable_id().as_str())
                {
                    Ordering::Less => {
                        require_source_precondition(current_entry, preconditions)?;
                        current_index += 1;
                    }
                    Ordering::Equal => {
                        if current_entry != next_entry {
                            require_source_precondition(current_entry, preconditions)?;
                            if current_entry.parent_id() != next_entry.parent_id()
                                || current_entry.name() != next_entry.name()
                            {
                                missing_destination = missing_destination.or_else(|| {
                                    uncovered_existing_destination(
                                        current_entries,
                                        next_entry,
                                        &vacant_destinations,
                                    )
                                });
                            }
                        }
                        current_index += 1;
                        next_index += 1;
                    }
                    Ordering::Greater => {
                        missing_destination = missing_destination.or_else(|| {
                            uncovered_existing_destination(
                                current_entries,
                                next_entry,
                                &vacant_destinations,
                            )
                        });
                        next_index += 1;
                    }
                }
            }
            (Some(current_entry), None) => {
                require_source_precondition(current_entry, preconditions)?;
                current_index += 1;
            }
            (None, Some(next_entry)) => {
                missing_destination = missing_destination.or_else(|| {
                    uncovered_existing_destination(
                        current_entries,
                        next_entry,
                        &vacant_destinations,
                    )
                });
                next_index += 1;
            }
            (None, None) => break,
        }
    }

    if let Some((parent_id, name)) = missing_destination {
        return Err(CommitConflict::MissingDestinationPrecondition {
            parent_id: parent_id.to_owned(),
            name: name.to_owned(),
        }
        .into());
    }
    Ok(())
}

fn require_source_precondition(
    entry: &CatalogGenerationEntry,
    preconditions: &[CatalogPrecondition],
) -> Result<(), CommitError> {
    if preconditions
        .iter()
        .any(|precondition| precondition_covers_source(precondition, entry))
    {
        return Ok(());
    }
    Err(CommitConflict::MissingSourcePrecondition {
        stable_id: entry.stable_id().as_str().to_owned(),
    }
    .into())
}

fn uncovered_existing_destination<'a>(
    current_entries: &[CatalogGenerationEntry],
    entry: &'a CatalogGenerationEntry,
    vacant_destinations: &HashSet<(&str, &str)>,
) -> Option<(&'a str, &'a str)> {
    let parent_id = entry.parent_id()?;
    if !catalog_contains_stable_id(current_entries, parent_id)
        || vacant_destinations.contains(&(parent_id.as_str(), entry.name().as_str()))
    {
        return None;
    }
    Some((parent_id.as_str(), entry.name().as_str()))
}

fn catalog_contains_stable_id(entries: &[CatalogGenerationEntry], stable_id: &OpaqueId) -> bool {
    entries
        .binary_search_by(|entry| entry.stable_id().as_str().cmp(stable_id.as_str()))
        .is_ok()
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
    catalogs: (Option<&CatalogGeneration>, &CatalogGeneration),
    prepared: &[PreparedObjectRevision],
    cached: Option<&ValidatedObjectState>,
    #[cfg(test)] probe: Option<&mut CommitProbe<'_>>,
) -> Result<ValidatedObjectState, CommitError> {
    validate_prepared_revisions_observed(
        objects_dir,
        keys,
        core_id,
        catalogs,
        prepared,
        cached,
        None,
        |_, _| {},
        #[cfg(test)]
        probe,
    )
}

fn catalog_object_bindings(
    catalog: &CatalogGeneration,
) -> Result<Vec<ValidatedObjectBinding>, CommitError> {
    catalog
        .entries()
        .iter()
        .filter_map(|entry| {
            entry.object_payload().map(|object| {
                ValidatedObjectBinding::from_catalog_object(entry.stable_id(), object)
                    .map_err(CommitError::from)
            })
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn validate_prepared_revisions_observed<F>(
    objects_dir: &Dir,
    keys: &FrkSubkeys,
    core_id: &str,
    catalogs: (Option<&CatalogGeneration>, &CatalogGeneration),
    prepared: &[PreparedObjectRevision],
    cached: Option<&ValidatedObjectState>,
    diagnostics: Option<&ObjectLeaseDiagnosticObserver>,
    mut observe_validated: F,
    #[cfg(test)] mut probe: Option<&mut CommitProbe<'_>>,
) -> Result<ValidatedObjectState, CommitError>
where
    F: FnMut(ValidatedObjectBinding, File),
{
    let (current, next) = catalogs;
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
    let mut validated = Vec::with_capacity(current_objects.len().max(prepared.len()));

    for entry in next.entries() {
        let Some(object) = entry.object_payload() else {
            continue;
        };
        let candidate = ValidatedObjectBinding::from_catalog_object(entry.stable_id(), object)?;
        let unchanged = current_objects
            .get(entry.stable_id().as_str())
            .is_some_and(|current| same_object_body(current, object));
        if unchanged {
            let file = validate_existing_object_file(
                objects_dir,
                object.physical_name(),
                diagnostics,
                #[cfg(test)]
                probe.as_deref_mut(),
            )?;
            if cached
                .and_then(|state| state.get(entry.stable_id()))
                .is_some_and(|binding| binding == &candidate)
            {
                #[cfg(test)]
                if let Some(probe) = probe.as_deref_mut() {
                    probe.stage(CommitStage::LeaseObjectValidated);
                }
                observe_validated(candidate.clone(), file);
                validated.push(candidate);
                continue;
            }
            catalog_object_key_binding(
                keys,
                core_id,
                entry.stable_id(),
                object,
                #[cfg(test)]
                probe.as_deref_mut(),
            )?;
            #[cfg(test)]
            if let Some(probe) = probe.as_deref_mut() {
                probe.stage(CommitStage::LeaseObjectValidated);
            }
            observe_validated(candidate.clone(), file);
            validated.push(candidate);
            continue;
        }

        let next_key_binding = catalog_object_key_binding(
            keys,
            core_id,
            entry.stable_id(),
            object,
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;

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
        let file = validate_prepared_file(
            objects_dir,
            token,
            diagnostics,
            #[cfg(test)]
            probe.as_deref_mut(),
        )?;
        consumed.insert(token.physical_name.as_str());
        #[cfg(test)]
        if let Some(probe) = probe.as_deref_mut() {
            probe.stage(CommitStage::LeaseObjectValidated);
        }
        observe_validated(candidate.clone(), file);
        validated.push(candidate);
    }

    if let Some(unused) = prepared
        .iter()
        .find(|value| !consumed.contains(value.physical_name.as_str()))
    {
        return Err(CommitError::UnusedPreparedRevision {
            physical_name: unused.physical_name.as_str().to_owned(),
        });
    }
    Ok(ValidatedObjectState::from_catalog_bindings(validated)?)
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
    #[cfg(test)] probe: Option<&mut CommitProbe<'_>>,
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
    #[cfg(test)]
    if let Some(probe) = probe {
        probe.object_dek_unwrap();
    }
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
    diagnostics: Option<&ObjectLeaseDiagnosticObserver>,
    #[cfg(test)] probe: Option<&mut CommitProbe<'_>>,
) -> Result<File, CommitError> {
    if let Some(diagnostics) = diagnostics {
        diagnostics.record_safe_open();
    }
    #[cfg(test)]
    if let Some(probe) = probe {
        probe.object_safe_open();
    }
    let file = open_regular_file_in(objects_dir, OsStr::new(physical_name.as_str()))?;
    if file.metadata()?.len() == 0 {
        return Err(CommitError::ReferencedObjectMissing {
            physical_name: physical_name.as_str().to_owned(),
        });
    }
    Ok(file)
}

fn validate_prepared_file(
    objects_dir: &Dir,
    prepared: &PreparedObjectRevision,
    diagnostics: Option<&ObjectLeaseDiagnosticObserver>,
    #[cfg(test)] probe: Option<&mut CommitProbe<'_>>,
) -> Result<File, CommitError> {
    if let Some(diagnostics) = diagnostics {
        diagnostics.record_safe_open();
    }
    #[cfg(test)]
    if let Some(probe) = probe {
        probe.object_safe_open();
    }
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
    Ok(file)
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

pub(crate) fn open_regular_file_in(dir: &Dir, name: &OsStr) -> io::Result<File> {
    #[cfg(windows)]
    let file = {
        let mut options = OpenOptions::new();
        options
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_DELETE);
        dir.open_with(name, &options)?.into_std()
    };
    #[cfg(not(windows))]
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
    if target_text == CUTOVER_RECEIPT_FILE
        || target_text == CUTOVER_COMPLETE_FILE
        || is_catalog_physical_name(target_text)
    {
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
    #[error("selected validation snapshot changed before commit")]
    SelectedValidationChanged,
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
    #[error("CoreFS object-lease release is in progress")]
    ObjectLeaseReleaseInProgress,
    #[error(
        "CoreFS object-lease release cannot wait on an operation active on the current thread"
    )]
    ObjectLeaseReleaseReentrant,
    #[error("CoreFS object-lease operation count overflow")]
    ObjectLeaseOperationOverflow,
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
    #[error("object revision is exhausted during targeted key rotation")]
    ObjectRevisionExhausted,
    #[error("object-key epoch is exhausted during targeted key rotation")]
    ObjectKeyEpochExhausted,
    #[error("targeted key-rotation source object is missing: {stable_id}")]
    ObjectKeyRotationSourceMissing { stable_id: String },
    #[error("targeted key rotation cannot rewrite tombstoned content: {stable_id}")]
    ObjectKeyRotationTombstone { stable_id: String },
    #[error("targeted key-rotation source content does not match the catalog: {stable_id}")]
    ObjectKeyRotationSourceMismatch { stable_id: String },
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
    #[error("an interrupted first CoreFS cutover requires recovery under the commit lock")]
    CutoverRecoveryRequired,
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
    #[error("CoreFS key rotation failed: {0}")]
    Rotation(#[from] RotationError),
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::io::{self, Cursor};
    use std::thread;
    use std::time::{Duration, Instant};

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
    fn lock_hold_measurement_includes_release_cleanup() {
        let release_delay = Duration::from_millis(25);
        let started = Instant::now();

        let measured = super::measure_lock_hold_through_release(started, || {
            thread::sleep(release_delay);
        });

        assert!(measured >= release_delay);
    }

    #[test]
    fn lock_hold_measurement_starts_when_the_kernel_lock_is_acquired() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-lock-acquisition-timing-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let coordinator = CoreCommitCoordinator::new(&root, "core-a").unwrap();
        let bookkeeping_delay = Duration::from_millis(25);

        let commit_lock = super::CoreCommitLock::acquire_in_with_post_kernel_lock_hook(
            &coordinator.root_dir,
            &coordinator.fs_dir,
            || thread::sleep(bookkeeping_delay),
        )
        .unwrap();
        let lock_hold_duration =
            super::measure_lock_hold_through_release(commit_lock.acquired_at(), || {
                drop(commit_lock)
            });

        assert!(lock_hold_duration >= bookkeeping_delay);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn trusted_publication_path_hashes_once_and_decrypts_zero_times() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-trusted-publication-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let coordinator = CoreCommitCoordinator::new(&root, "01JCORE").unwrap();
        let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), 3).unwrap();
        let catalog = CatalogGeneration::new(
            7,
            vec![CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                OpaqueId::parse("01J00000000000000000000000").unwrap(),
                None,
                PortableName::parse("Core").unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            ))],
        )
        .unwrap();
        let mut probe = super::CoordinatorPublicationProbe::default();

        let (head, catalog_name, _, _, _) = coordinator
            .publish_catalog_pointer_with_hook_observed(
                &keys,
                &catalog,
                "VALIDATION_HEAD",
                false,
                &mut |_| Ok(()),
                &mut probe,
            )
            .unwrap();

        assert_eq!(probe.ciphertext_hashes, 1);
        assert_eq!(probe.catalog_decrypts, 0);
        assert_eq!(head.generation(), catalog.generation());
        assert!(coordinator.catalogs_path().join(catalog_name).is_file());
        assert!(coordinator.validation_head_path().is_file());

        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
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
        std::fs::hard_link(root.join(target), root.join(stale_stage)).unwrap();
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
            .publish_catalog_pointer_with_hook(
                &keys,
                &catalog,
                "VALIDATION_HEAD",
                false,
                &mut |_| Ok(()),
            )
            .unwrap_err();

        assert!(matches!(error, CommitError::InvalidCoreLayout));
        assert!(!pinned_root.join("fs").join("VALIDATION_HEAD").exists());
        drop(coordinator);
        std::fs::remove_dir_all(pinned_root).unwrap();
        std::fs::remove_dir_all(replacement_root).unwrap();
    }

    #[test]
    fn catalog_publication_revalidates_after_pointer_publish() {
        let pinned_root = std::env::temp_dir().join(format!(
            "anima-corefs-post-publish-pinned-root-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&pinned_root);
        let coordinator = CoreCommitCoordinator::new(&pinned_root, "core-a").unwrap();
        let pointer_published = Cell::new(false);

        let error = coordinator
            .publish_pointer_and_revalidate(
                "VALIDATION_HEAD",
                b"head",
                |dir, target, payload| {
                    crate::publication::atomic_publish_in(dir, target, payload)?;
                    pointer_published.set(true);
                    Ok(())
                },
                |coordinator| {
                    if pointer_published.get() {
                        Err(CommitError::InvalidCoreLayout)
                    } else {
                        coordinator.validate_pinned_layout()
                    }
                },
            )
            .unwrap_err();

        assert!(pointer_published.get());
        assert!(matches!(error, CommitError::InvalidCoreLayout));
        assert!(pinned_root.join("fs").join("VALIDATION_HEAD").is_file());
        drop(coordinator);
        std::fs::remove_dir_all(pinned_root).unwrap();
    }
}

#[cfg(test)]
mod cache_tests;
#[cfg(test)]
mod failure_tests;
#[cfg(test)]
mod object_lease_tests;
#[cfg(feature = "session-test-seams")]
pub mod session_test_support;
