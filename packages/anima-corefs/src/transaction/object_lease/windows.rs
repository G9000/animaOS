use std::fmt;
use std::fs::File;
use std::io;
use std::mem::MaybeUninit;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use cap_std::fs::{Dir, OpenOptions, OpenOptionsExt};
use getrandom::getrandom;
use windows_sys::Win32::Foundation::{
    DuplicateHandle, GetLastError, DUPLICATE_SAME_ACCESS, ERROR_NOT_FOUND, ERROR_OPERATION_ABORTED,
    HANDLE,
};
use windows_sys::Win32::Storage::FileSystem::{
    GetFileInformationByHandle, ReadDirectoryChangesW, BY_HANDLE_FILE_INFORMATION,
    FILE_ACTION_ADDED, FILE_ACTION_MODIFIED, FILE_ACTION_REMOVED, FILE_ACTION_RENAMED_NEW_NAME,
    FILE_ACTION_RENAMED_OLD_NAME, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_BACKUP_SEMANTICS, FILE_LIST_DIRECTORY, FILE_NOTIFY_CHANGE_ATTRIBUTES,
    FILE_NOTIFY_CHANGE_CREATION, FILE_NOTIFY_CHANGE_DIR_NAME, FILE_NOTIFY_CHANGE_FILE_NAME,
    FILE_NOTIFY_CHANGE_LAST_WRITE, FILE_NOTIFY_CHANGE_SECURITY, FILE_NOTIFY_CHANGE_SIZE,
    FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, GetCurrentThread};
use windows_sys::Win32::System::IO::CancelSynchronousIo;

use super::{
    FenceOutcome, LeaseMonitorResource, LeaseResourceFactory, LeaseResourcePlan, MonitorStateCell,
    PlatformValidationAnchor, ValidationAnchor,
};
use crate::transaction::cache::ValidatedObjectBinding;

const MONITOR_RESOURCE_COUNT: usize = 2;
const NOTIFICATION_BUFFER_SIZE: usize = 64 * 1024;
const FENCE_TIMEOUT: Duration = Duration::from_secs(2);
const STARTUP_ATTEMPTS: usize = 8;
const STARTUP_ATTEMPT_TIMEOUT: Duration = Duration::from_millis(250);
const NOTIFY_FILTER: u32 = FILE_NOTIFY_CHANGE_FILE_NAME
    | FILE_NOTIFY_CHANGE_DIR_NAME
    | FILE_NOTIFY_CHANGE_ATTRIBUTES
    | FILE_NOTIFY_CHANGE_SIZE
    | FILE_NOTIFY_CHANGE_LAST_WRITE
    | FILE_NOTIFY_CHANGE_CREATION
    | FILE_NOTIFY_CHANGE_SECURITY;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileIdentity {
    volume_serial_number: u32,
    file_index: u64,
}

pub(in crate::transaction) struct RetainedValidationAnchor {
    file: Mutex<Option<File>>,
    identity: FileIdentity,
}

impl fmt::Debug for RetainedValidationAnchor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RetainedValidationAnchor")
            .field("identity", &self.identity)
            .finish_non_exhaustive()
    }
}

impl RetainedValidationAnchor {
    pub(in crate::transaction) fn new(file: File) -> io::Result<Self> {
        let information = query_file_information(&file)?;
        if !valid_retained_object(&information) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "retained CoreFS object handle is not a unique nonempty regular file",
            ));
        }
        Ok(Self {
            file: Mutex::new(Some(file)),
            identity: file_identity(&information),
        })
    }

    fn validate_fresh(&self) -> FenceOutcome {
        let guard = self
            .file
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let Some(file) = guard.as_ref() else {
            return FenceOutcome::Unknown;
        };
        match query_file_information(file) {
            Ok(information)
                if file_identity(&information) == self.identity
                    && valid_retained_object(&information) =>
            {
                FenceOutcome::Clean
            }
            Ok(_) | Err(_) => FenceOutcome::Unknown,
        }
    }

    #[cfg(test)]
    pub(in crate::transaction) fn invalidate_for_test(&self) {
        let _ = self
            .file
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take();
    }

    #[cfg(test)]
    pub(in crate::transaction) fn validate(&self) -> FenceOutcome {
        self.validate_fresh()
    }
}

impl PlatformValidationAnchor for RetainedValidationAnchor {
    fn validate(&self) -> FenceOutcome {
        self.validate_fresh()
    }
}

fn query_file_information(file: &File) -> io::Result<BY_HANDLE_FILE_INFORMATION> {
    let mut information = MaybeUninit::<BY_HANDLE_FILE_INFORMATION>::zeroed();
    // SAFETY: `file` owns a live Windows file handle and `information` points to a
    // correctly sized writable `BY_HANDLE_FILE_INFORMATION` value for the duration
    // of the call.
    let result = unsafe {
        GetFileInformationByHandle(raw_handle(file.as_raw_handle()), information.as_mut_ptr())
    };
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        // SAFETY: a successful `GetFileInformationByHandle` initialized the full value.
        Ok(unsafe { information.assume_init() })
    }
}

fn file_identity(information: &BY_HANDLE_FILE_INFORMATION) -> FileIdentity {
    FileIdentity {
        volume_serial_number: information.dwVolumeSerialNumber,
        file_index: (u64::from(information.nFileIndexHigh) << 32)
            | u64::from(information.nFileIndexLow),
    }
}

fn valid_retained_object(information: &BY_HANDLE_FILE_INFORMATION) -> bool {
    let attributes = information.dwFileAttributes;
    let length = (u64::from(information.nFileSizeHigh) << 32) | u64::from(information.nFileSizeLow);
    attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) == 0
        && length > 0
        && information.nNumberOfLinks == 1
}

#[derive(Debug)]
pub(in crate::transaction) struct WindowsLeaseFactory {
    objects_dir: Arc<Dir>,
}

impl WindowsLeaseFactory {
    pub(in crate::transaction) fn new(objects_dir: Dir) -> io::Result<Self> {
        Ok(Self {
            objects_dir: Arc::new(objects_dir),
        })
    }
}

impl LeaseResourceFactory for WindowsLeaseFactory {
    fn resource_plan(&self) -> LeaseResourcePlan {
        LeaseResourcePlan::supported(MONITOR_RESOURCE_COUNT)
    }

    fn create_monitor(
        &self,
        plan: LeaseResourcePlan,
        state: Arc<MonitorStateCell>,
    ) -> Result<Box<dyn LeaseMonitorResource>, ()> {
        if plan.monitor_resource_count() != MONITOR_RESOURCE_COUNT {
            return Err(());
        }
        WindowsLeaseMonitor::start(self.objects_dir.clone(), state)
            .map(|monitor| Box::new(monitor) as Box<dyn LeaseMonitorResource>)
            .map_err(|_error| {
                #[cfg(test)]
                eprintln!("Windows object lease monitor acquisition failed: {_error}");
            })
    }

    fn create_anchor(
        &self,
        _index: usize,
        binding: &ValidatedObjectBinding,
    ) -> Result<ValidationAnchor, ()> {
        let file = crate::transaction::open_regular_file_in(
            self.objects_dir.as_ref(),
            binding.physical_name.as_str().as_ref(),
        )
        .map_err(|_| ())?;
        let anchor = RetainedValidationAnchor::new(file).map_err(|_| ())?;
        Ok(ValidationAnchor::Windows(Arc::new(anchor)))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum Notification {
    Added(String),
    Removed(String),
    Modified(String),
    RenamedOld(String),
    RenamedNew(String),
}

#[derive(Debug)]
enum WorkerMessage {
    Armed,
    Notifications(Vec<Notification>),
    Unknown,
    Cancelled,
}

struct WorkerShared {
    objects_dir: Arc<Dir>,
    notification_handle: Arc<File>,
    worker_thread: Mutex<Option<OwnedHandle>>,
    state: Arc<MonitorStateCell>,
    cancelled: AtomicBool,
}

pub(super) struct WindowsLeaseMonitor {
    shared: Arc<WorkerShared>,
    receiver: Mutex<Receiver<WorkerMessage>>,
    fence_lock: Mutex<()>,
    active_probe: Mutex<Option<String>>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl fmt::Debug for WindowsLeaseMonitor {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("WindowsLeaseMonitor")
            .field("state", &self.shared.state.state())
            .finish_non_exhaustive()
    }
}

impl WindowsLeaseMonitor {
    fn start(objects_dir: Arc<Dir>, state: Arc<MonitorStateCell>) -> io::Result<Self> {
        let notification_handle = Arc::new(open_notification_handle(objects_dir.as_ref())?);
        let shared = Arc::new(WorkerShared {
            objects_dir,
            notification_handle,
            worker_thread: Mutex::new(None),
            state,
            cancelled: AtomicBool::new(false),
        });
        let (sender, receiver) = mpsc::channel();
        let worker_shared = shared.clone();
        let worker = thread::Builder::new()
            .name("anima-corefs-object-lease".into())
            .spawn(move || notification_worker(worker_shared, sender))?;
        let monitor = Self {
            shared,
            receiver: Mutex::new(receiver),
            fence_lock: Mutex::new(()),
            active_probe: Mutex::new(None),
            worker: Mutex::new(Some(worker)),
        };

        let armed = monitor
            .receiver
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .recv_timeout(FENCE_TIMEOUT);
        if !matches!(armed, Ok(WorkerMessage::Armed)) {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                "could not prove Windows directory monitor arm boundary",
            ));
        }
        let mut proved_boundary = false;
        for _ in 0..STARTUP_ATTEMPTS {
            if monitor.run_fence(STARTUP_ATTEMPT_TIMEOUT) == FenceOutcome::Clean {
                proved_boundary = true;
                break;
            }
            if monitor.shared.state.state() != super::MonitorState::Clean {
                break;
            }
        }
        if !proved_boundary {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                "could not prove Windows directory monitor arm boundary",
            ));
        }
        Ok(monitor)
    }

    fn run_fence(&self, timeout: Duration) -> FenceOutcome {
        let _fence_guard = self
            .fence_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if self.shared.cancelled.load(Ordering::Acquire) {
            return FenceOutcome::Unknown;
        }
        let probe = match probe_name() {
            Ok(probe) => probe,
            Err(_) => return FenceOutcome::Unknown,
        };
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        let probe_file = match self.shared.objects_dir.open_with(&probe, &options) {
            Ok(file) => file,
            Err(_) => return FenceOutcome::Unknown,
        };
        *self
            .active_probe
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(probe.clone());
        drop(probe_file);
        let cleanup_ok = self.shared.objects_dir.remove_file(&probe).is_ok();
        if !cleanup_ok {
            return FenceOutcome::Unknown;
        }
        self.active_probe
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take();

        let deadline = Instant::now() + timeout;
        let mut notifications = Vec::new();
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return FenceOutcome::Unknown;
            }
            let message = self
                .receiver
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .recv_timeout(remaining);
            match message {
                Ok(WorkerMessage::Notifications(batch)) => {
                    notifications.extend(batch);
                    if !notification_prefix_is_possible(&probe, &notifications) {
                        return FenceOutcome::Unknown;
                    }
                    if probe_lifecycle_complete(&probe, &notifications) {
                        return notification_outcome(&probe, &notifications, true);
                    }
                }
                Ok(WorkerMessage::Armed) => continue,
                Ok(WorkerMessage::Unknown)
                | Ok(WorkerMessage::Cancelled)
                | Err(RecvTimeoutError::Disconnected)
                | Err(RecvTimeoutError::Timeout) => return FenceOutcome::Unknown,
            }
        }
    }
}

impl LeaseMonitorResource for WindowsLeaseMonitor {
    fn fence(&self) -> FenceOutcome {
        self.run_fence(FENCE_TIMEOUT)
    }
}

impl Drop for WindowsLeaseMonitor {
    fn drop(&mut self) {
        self.shared.cancelled.store(true, Ordering::Release);
        self.shared.state.publish(FenceOutcome::Unknown);
        if let Some(probe) = self
            .active_probe
            .get_mut()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take()
        {
            let _ = self.shared.objects_dir.remove_file(probe);
        }
        let worker_thread = self
            .shared
            .worker_thread
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        // SAFETY: the duplicated thread handle remains owned by `shared` through the call.
        // The worker is dedicated to the synchronous directory read being cancelled.
        let cancelled = worker_thread.as_ref().map_or(0, |worker_thread| unsafe {
            CancelSynchronousIo(raw_handle(worker_thread.as_raw_handle()))
        });
        drop(worker_thread);
        if cancelled == 0 {
            // SAFETY: `GetLastError` has no preconditions.
            let error = unsafe { GetLastError() };
            if error != ERROR_NOT_FOUND {
                self.shared.state.publish(FenceOutcome::Unknown);
            }
        }
        if self
            .worker
            .get_mut()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take()
            .is_some_and(|worker| worker.join().is_err())
        {
            self.shared.state.publish(FenceOutcome::Unknown);
        }
    }
}

fn notification_worker(shared: Arc<WorkerShared>, sender: mpsc::Sender<WorkerMessage>) {
    let mut buffer = vec![0_u8; NOTIFICATION_BUFFER_SIZE];
    let worker_thread = match duplicate_current_thread_handle() {
        Ok(worker_thread) => worker_thread,
        Err(_) => {
            shared.state.publish(FenceOutcome::Unknown);
            let _ = sender.send(WorkerMessage::Unknown);
            return;
        }
    };
    *shared
        .worker_thread
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(worker_thread);
    if sender.send(WorkerMessage::Armed).is_err() {
        return;
    }
    while !shared.cancelled.load(Ordering::Acquire) {
        let mut bytes_returned = 0_u32;
        // SAFETY: the independently opened capability-relative directory handle remains live
        // through `shared`; the buffer and byte-count output are valid for the synchronous call.
        let result = unsafe {
            ReadDirectoryChangesW(
                raw_handle(shared.notification_handle.as_raw_handle()),
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                0,
                NOTIFY_FILTER,
                &mut bytes_returned,
                std::ptr::null_mut(),
                None,
            )
        };
        if result == 0 {
            let error = io::Error::last_os_error();
            if shared.cancelled.load(Ordering::Acquire)
                && error.raw_os_error() == Some(ERROR_OPERATION_ABORTED as i32)
            {
                let _ = sender.send(WorkerMessage::Cancelled);
            } else {
                shared.state.publish(FenceOutcome::Unknown);
                let _ = sender.send(WorkerMessage::Unknown);
            }
            return;
        }
        if bytes_returned == 0 {
            shared.state.publish(FenceOutcome::Unknown);
            let _ = sender.send(WorkerMessage::Unknown);
            return;
        }
        let used = usize::try_from(bytes_returned).unwrap_or(usize::MAX);
        let notifications = match parse_notifications(buffer.get(..used).unwrap_or_default()) {
            Ok(notifications) => notifications,
            Err(()) => {
                shared.state.publish(FenceOutcome::Unknown);
                let _ = sender.send(WorkerMessage::Unknown);
                return;
            }
        };
        if sender
            .send(WorkerMessage::Notifications(notifications))
            .is_err()
        {
            return;
        }
    }
    let _ = sender.send(WorkerMessage::Cancelled);
}

fn open_notification_handle(objects_dir: &Dir) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options
        .read(true)
        .access_mode(FILE_LIST_DIRECTORY)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS);
    objects_dir
        .open_with(".", &options)
        .map(cap_std::fs::File::into_std)
}

fn duplicate_current_thread_handle() -> io::Result<OwnedHandle> {
    let mut duplicate = MaybeUninit::<HANDLE>::uninit();
    // SAFETY: the pseudo process/thread handles are valid in this process; the output points
    // to writable storage and requests an owned same-access duplicate in this process.
    let result = unsafe {
        DuplicateHandle(
            GetCurrentProcess(),
            GetCurrentThread(),
            GetCurrentProcess(),
            duplicate.as_mut_ptr(),
            0,
            0,
            DUPLICATE_SAME_ACCESS,
        )
    };
    if result == 0 {
        Err(io::Error::last_os_error())
    } else {
        // SAFETY: successful `DuplicateHandle` initialized a newly owned real thread handle.
        Ok(unsafe { OwnedHandle::from_raw_handle(duplicate.assume_init() as _) })
    }
}

fn parse_notifications(buffer: &[u8]) -> Result<Vec<Notification>, ()> {
    const HEADER_SIZE: usize = 12;
    let mut offset = 0_usize;
    let mut notifications = Vec::new();
    loop {
        let header = buffer.get(offset..offset + HEADER_SIZE).ok_or(())?;
        let next = read_u32(&header[0..4])? as usize;
        let action = read_u32(&header[4..8])?;
        let name_length = read_u32(&header[8..12])? as usize;
        if name_length == 0 || name_length % 2 != 0 {
            return Err(());
        }
        let name_bytes = buffer
            .get(offset + HEADER_SIZE..offset + HEADER_SIZE + name_length)
            .ok_or(())?;
        let mut wide = Vec::with_capacity(name_length / 2);
        for encoded in name_bytes.chunks_exact(2) {
            wide.push(u16::from_le_bytes([encoded[0], encoded[1]]));
        }
        let name = String::from_utf16(&wide).map_err(|_| ())?;
        if name.is_empty() || name.contains(['/', '\\', '\0']) {
            return Err(());
        }
        notifications.push(match action {
            FILE_ACTION_ADDED => Notification::Added(name),
            FILE_ACTION_REMOVED => Notification::Removed(name),
            FILE_ACTION_MODIFIED => Notification::Modified(name),
            FILE_ACTION_RENAMED_OLD_NAME => Notification::RenamedOld(name),
            FILE_ACTION_RENAMED_NEW_NAME => Notification::RenamedNew(name),
            _ => return Err(()),
        });
        if next == 0 {
            if offset + HEADER_SIZE + name_length != buffer.len() {
                return Err(());
            }
            return Ok(notifications);
        }
        if next % 4 != 0 || next < HEADER_SIZE + name_length {
            return Err(());
        }
        offset = offset.checked_add(next).ok_or(())?;
        if offset >= buffer.len() {
            return Err(());
        }
    }
}

fn read_u32(bytes: &[u8]) -> Result<u32, ()> {
    let encoded: [u8; 4] = bytes.try_into().map_err(|_| ())?;
    Ok(u32::from_le_bytes(encoded))
}

fn probe_name() -> io::Result<String> {
    let mut random = [0_u8; 3];
    getrandom(&mut random).map_err(io::Error::other)?;
    Ok(format!(
        "AL{:02X}{:02X}{:02X}.TMP",
        random[0], random[1], random[2]
    ))
}

fn probe_lifecycle_complete(probe: &str, notifications: &[Notification]) -> bool {
    let mut added = false;
    for notification in notifications {
        match notification {
            Notification::Added(name) if name == probe => added = true,
            Notification::Removed(name) if name == probe && added => return true,
            _ => {}
        }
    }
    false
}

fn notification_prefix_is_possible(probe: &str, notifications: &[Notification]) -> bool {
    let mut saw_probe_add = false;
    let mut saw_probe_remove = false;
    let mut pending_rename = false;
    for notification in notifications {
        let (name, action_is_add, action_is_remove, action_is_rename_old, action_is_rename_new) =
            match notification {
                Notification::Added(name) => (name, true, false, false, false),
                Notification::Removed(name) => (name, false, true, false, false),
                Notification::Modified(name) => (name, false, false, false, false),
                Notification::RenamedOld(name) => (name, false, false, true, false),
                Notification::RenamedNew(name) => (name, false, false, false, true),
            };
        if action_is_rename_old {
            if pending_rename {
                return false;
            }
            pending_rename = true;
        } else if action_is_rename_new {
            if !pending_rename {
                return false;
            }
            pending_rename = false;
        } else if pending_rename {
            return false;
        }
        if name == probe {
            if action_is_add && !saw_probe_add && !saw_probe_remove {
                saw_probe_add = true;
            } else if action_is_remove && saw_probe_add && !saw_probe_remove {
                saw_probe_remove = true;
            } else {
                return false;
            }
        } else if name.eq_ignore_ascii_case(probe) {
            return false;
        }
    }
    true
}

fn notification_outcome(
    probe: &str,
    notifications: &[Notification],
    cleanup_ok: bool,
) -> FenceOutcome {
    if !cleanup_ok {
        return FenceOutcome::Unknown;
    }
    let mut outcome = FenceOutcome::Clean;
    let mut saw_probe_add = false;
    let mut saw_probe_remove = false;
    let mut pending_rename = false;
    for notification in notifications {
        let (name, action_is_add, action_is_remove, action_is_rename_old, action_is_rename_new) =
            match notification {
                Notification::Added(name) => (name, true, false, false, false),
                Notification::Removed(name) => (name, false, true, false, false),
                Notification::Modified(name) => (name, false, false, false, false),
                Notification::RenamedOld(name) => (name, false, false, true, false),
                Notification::RenamedNew(name) => (name, false, false, false, true),
            };

        if action_is_rename_old {
            if pending_rename {
                return FenceOutcome::Unknown;
            }
            pending_rename = true;
        } else if action_is_rename_new {
            if !pending_rename {
                return FenceOutcome::Unknown;
            }
            pending_rename = false;
        } else if pending_rename {
            return FenceOutcome::Unknown;
        }

        if name == probe {
            if action_is_add && !saw_probe_add && !saw_probe_remove {
                saw_probe_add = true;
            } else if action_is_remove && saw_probe_add && !saw_probe_remove {
                saw_probe_remove = true;
            } else {
                return FenceOutcome::Unknown;
            }
        } else if name.eq_ignore_ascii_case(probe) {
            return FenceOutcome::Unknown;
        } else {
            outcome = FenceOutcome::DirtyAll;
        }
    }
    if pending_rename {
        return FenceOutcome::Unknown;
    }
    if saw_probe_add && saw_probe_remove {
        outcome
    } else {
        FenceOutcome::Unknown
    }
}

fn raw_handle(handle: std::os::windows::io::RawHandle) -> HANDLE {
    handle as HANDLE
}

#[cfg(test)]
#[derive(Clone, Debug, Eq, PartialEq)]
pub(in crate::transaction) enum TestNotification {
    Added(String),
    Removed(String),
    Modified(String),
    RenamedOld(String),
    RenamedNew(String),
    Overflow,
    ParseError,
    HandleLoss,
    Cancelled,
}

#[cfg(test)]
pub(in crate::transaction) fn notification_outcome_for_test(
    probe: &str,
    notifications: &[TestNotification],
    cleanup_ok: bool,
) -> FenceOutcome {
    let mut native = Vec::with_capacity(notifications.len());
    for notification in notifications {
        native.push(match notification {
            TestNotification::Added(name) => Notification::Added(name.clone()),
            TestNotification::Removed(name) => Notification::Removed(name.clone()),
            TestNotification::Modified(name) => Notification::Modified(name.clone()),
            TestNotification::RenamedOld(name) => Notification::RenamedOld(name.clone()),
            TestNotification::RenamedNew(name) => Notification::RenamedNew(name.clone()),
            TestNotification::Overflow
            | TestNotification::ParseError
            | TestNotification::HandleLoss
            | TestNotification::Cancelled => return FenceOutcome::Unknown,
        });
    }
    notification_outcome(probe, &native, cleanup_ok)
}

#[cfg(test)]
pub(in crate::transaction) fn probe_name_for_test() -> io::Result<String> {
    probe_name()
}
