#[cfg(test)]
use std::collections::VecDeque;
use std::fmt;
use std::fs::File;
use std::io;
use std::mem::MaybeUninit;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::panic::{self, AssertUnwindSafe};
#[cfg(test)]
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
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

const MONITOR_RESOURCE_COUNT: usize = 3;
const NOTIFICATION_BUFFER_SIZE: usize = 64 * 1024;
const FENCE_TIMEOUT: Duration = Duration::from_secs(2);
const CANCELLATION_RETRY_INTERVAL: Duration = Duration::from_millis(1);
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
    #[cfg(test)]
    control: Option<WindowsLeaseTestControl>,
}

impl WindowsLeaseFactory {
    pub(in crate::transaction) fn new(objects_dir: Dir) -> io::Result<Self> {
        Ok(Self {
            objects_dir: Arc::new(objects_dir),
            #[cfg(test)]
            control: None,
        })
    }

    #[cfg(test)]
    pub(in crate::transaction) fn new_for_test(
        objects_dir: Dir,
        control: WindowsLeaseTestControl,
    ) -> io::Result<Self> {
        Ok(Self {
            objects_dir: Arc::new(objects_dir),
            control: Some(control),
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
        #[cfg(test)]
        if let Some(control) = &self.control {
            control.record_state(state.clone());
        }
        WindowsLeaseMonitor::start(
            self.objects_dir.clone(),
            state,
            #[cfg(test)]
            self.control.clone(),
        )
        .map(|monitor| Box::new(monitor) as Box<dyn LeaseMonitorResource>)
        .map_err(|_| ())
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
        #[cfg(test)]
        if let Some(control) = &self.control {
            control.record_construction_event(ConstructionEventForTest::AnchorCreated);
        }
        Ok(ValidationAnchor::Windows(Arc::new(anchor)))
    }

    fn create_anchor_from_validated_file(
        &self,
        _index: usize,
        _binding: &ValidatedObjectBinding,
        file: File,
    ) -> Result<ValidationAnchor, ()> {
        let anchor = RetainedValidationAnchor::new(file).map_err(|_| ())?;
        #[cfg(test)]
        if let Some(control) = &self.control {
            control.record_construction_event(ConstructionEventForTest::AnchorCreated);
        }
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ProbePhase {
    AwaitCreate,
    AwaitDelete,
    Complete,
}

#[derive(Debug)]
struct ActiveProbe {
    generation: u64,
    name: String,
    phase: ProbePhase,
    outcome: FenceOutcome,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum WorkerState {
    Starting,
    Armed,
    Cancelling,
    NativeComplete,
    Joined,
}

#[derive(Debug)]
struct WindowsMonitorState {
    terminal: FenceOutcome,
    active_probe: Option<ActiveProbe>,
    next_fence_generation: u64,
    acknowledged_fence_generation: u64,
    boundary_progress: u64,
    deferred_outcome: FenceOutcome,
    pending_rename: bool,
    worker_state: WorkerState,
    cancellation_requested: bool,
    native_read_pending: bool,
    teardown_started: bool,
    publication_open: bool,
    teardown_target_missed: bool,
}

impl Default for WindowsMonitorState {
    fn default() -> Self {
        Self {
            terminal: FenceOutcome::Clean,
            active_probe: None,
            next_fence_generation: 0,
            acknowledged_fence_generation: 0,
            boundary_progress: 0,
            deferred_outcome: FenceOutcome::Clean,
            pending_rename: false,
            worker_state: WorkerState::Starting,
            cancellation_requested: false,
            native_read_pending: false,
            teardown_started: false,
            publication_open: true,
            teardown_target_missed: false,
        }
    }
}

struct PendingRead<'a> {
    shared: &'a WorkerShared,
}

impl Drop for PendingRead<'_> {
    fn drop(&mut self) {
        complete_pending_read(self.shared);
    }
}

struct WorkerTerminalGuard {
    shared: Arc<WorkerShared>,
}

impl Drop for WorkerTerminalGuard {
    fn drop(&mut self) {
        complete_pending_read(self.shared.as_ref());
        let mut monitor = self
            .shared
            .monitor
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        publish_terminal(self.shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
        monitor.worker_state = WorkerState::NativeComplete;
        self.shared.changed.notify_all();
    }
}

struct WorkerShared {
    objects_dir: Arc<Dir>,
    notification_handle: Mutex<Option<File>>,
    worker_thread: Mutex<Option<OwnedHandle>>,
    monitor: Mutex<WindowsMonitorState>,
    changed: Condvar,
    state: Arc<MonitorStateCell>,
    #[cfg(test)]
    control: Option<WindowsLeaseTestControl>,
    #[cfg(test)]
    _notification_resource_liveness: Arc<()>,
    #[cfg(test)]
    cancellation_resource_liveness: Mutex<Option<Arc<()>>>,
}

impl WorkerShared {
    fn next_probe_name(&self) -> io::Result<String> {
        #[cfg(test)]
        if let Some(control) = &self.control {
            return control.next_probe_name();
        }
        random_probe_name()
    }

    fn remove_probe(&self, probe: &str) -> io::Result<()> {
        #[cfg(test)]
        {
            if let Some(control) = &self.control {
                control.before_probe_cleanup(self.objects_dir.as_ref(), probe)?;
            }
            let result = self.objects_dir.remove_file(probe);
            if let Some(control) = &self.control {
                control.after_probe_cleanup(result.is_ok());
            }
            result
        }
        #[cfg(not(test))]
        self.objects_dir.remove_file(probe)
    }

    fn cancel_requested(&self) -> bool {
        self.monitor
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .cancellation_requested
    }
}

pub(super) struct WindowsLeaseMonitor {
    shared: Arc<WorkerShared>,
    fence_lock: Mutex<()>,
    worker: Mutex<Option<JoinHandle<()>>>,
    #[cfg(test)]
    _join_resource_liveness: Arc<()>,
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
    fn start(
        objects_dir: Arc<Dir>,
        state: Arc<MonitorStateCell>,
        #[cfg(test)] control: Option<WindowsLeaseTestControl>,
    ) -> io::Result<Self> {
        let notification_handle = open_notification_handle(objects_dir.as_ref())?;
        #[cfg(test)]
        let notification_resource_liveness = Arc::new(());
        #[cfg(test)]
        if let Some(control) = &control {
            control.record_resource_liveness(Arc::downgrade(&notification_resource_liveness));
        }
        let shared = Arc::new(WorkerShared {
            objects_dir,
            notification_handle: Mutex::new(Some(notification_handle)),
            worker_thread: Mutex::new(None),
            monitor: Mutex::new(WindowsMonitorState::default()),
            changed: Condvar::new(),
            state,
            #[cfg(test)]
            control,
            #[cfg(test)]
            _notification_resource_liveness: notification_resource_liveness,
            #[cfg(test)]
            cancellation_resource_liveness: Mutex::new(None),
        });
        let worker_shared = shared.clone();
        let worker = thread::Builder::new()
            .name("anima-corefs-object-lease".into())
            .spawn(move || notification_worker_entry(worker_shared))?;
        #[cfg(test)]
        let join_resource_liveness = {
            let liveness = Arc::new(());
            if let Some(control) = &shared.control {
                control.record_resource_liveness(Arc::downgrade(&liveness));
            }
            liveness
        };
        let monitor = Self {
            shared,
            fence_lock: Mutex::new(()),
            worker: Mutex::new(Some(worker)),
            #[cfg(test)]
            _join_resource_liveness: join_resource_liveness,
        };

        let worker_state = monitor.wait_for_worker_arm(FENCE_TIMEOUT);
        if worker_state != WorkerState::Armed {
            monitor.shared.state.publish(FenceOutcome::Unknown);
            return Err(io::Error::new(
                io::ErrorKind::Other,
                "could not prove Windows directory monitor arm boundary",
            ));
        }
        if monitor.run_fence(FENCE_TIMEOUT) != FenceOutcome::Clean {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                "could not prove Windows directory monitor arm boundary",
            ));
        }
        #[cfg(test)]
        if let Some(control) = &monitor.shared.control {
            control.record_construction_event(ConstructionEventForTest::MonitorArmed);
        }
        Ok(monitor)
    }

    fn wait_for_worker_arm(&self, timeout: Duration) -> WorkerState {
        let monitor = self
            .shared
            .monitor
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (monitor, _) = self
            .shared
            .changed
            .wait_timeout_while(monitor, timeout, |monitor| {
                monitor.worker_state == WorkerState::Starting
            })
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        monitor.worker_state
    }

    fn run_fence(&self, timeout: Duration) -> FenceOutcome {
        let outcome = self.run_fence_unpublished(timeout);
        match self.shared.state.publish(outcome) {
            super::MonitorState::Clean => FenceOutcome::Clean,
            super::MonitorState::DirtyAll => FenceOutcome::DirtyAll,
            super::MonitorState::Unknown => FenceOutcome::Unknown,
        }
    }

    fn run_fence_unpublished(&self, timeout: Duration) -> FenceOutcome {
        let _fence_guard = self
            .fence_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let probe = match self.shared.next_probe_name() {
            Ok(probe) => probe,
            Err(_) => return FenceOutcome::Unknown,
        };
        let generation = {
            let mut monitor = self
                .shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if monitor.cancellation_requested
                || !monitor.publication_open
                || monitor.worker_state != WorkerState::Armed
            {
                return FenceOutcome::Unknown;
            }
            if monitor.terminal != FenceOutcome::Clean {
                return monitor.terminal;
            }
            if monitor.deferred_outcome != FenceOutcome::Clean {
                let outcome = monitor.deferred_outcome;
                monitor.deferred_outcome = FenceOutcome::Clean;
                publish_terminal(self.shared.as_ref(), &mut monitor, outcome);
                return outcome;
            }
            if monitor.active_probe.is_some() {
                publish_terminal(self.shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
                return FenceOutcome::Unknown;
            }
            monitor.next_fence_generation = monitor.next_fence_generation.wrapping_add(1);
            let generation = monitor.next_fence_generation;
            monitor.active_probe = Some(ActiveProbe {
                generation,
                name: probe.clone(),
                phase: ProbePhase::AwaitCreate,
                outcome: FenceOutcome::Clean,
            });
            generation
        };

        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        let probe_file = match self.shared.objects_dir.open_with(&probe, &options) {
            Ok(file) => file,
            Err(_) => {
                let mut monitor = self
                    .shared
                    .monitor
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                monitor.active_probe = None;
                publish_terminal(self.shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
                return FenceOutcome::Unknown;
            }
        };
        drop(probe_file);
        let cleanup_ok = self.shared.remove_probe(&probe).is_ok();
        if !cleanup_ok {
            let mut monitor = self
                .shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            publish_terminal(self.shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
            return FenceOutcome::Unknown;
        }

        let deadline = Instant::now() + timeout;
        let mut monitor = self
            .shared
            .monitor
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        loop {
            if monitor.cancellation_requested || !monitor.publication_open {
                return FenceOutcome::Unknown;
            }
            if monitor.terminal == FenceOutcome::Unknown {
                if monitor
                    .active_probe
                    .as_ref()
                    .is_some_and(|active| active.generation == generation)
                {
                    monitor.active_probe = None;
                }
                return monitor.terminal;
            }
            let completed = monitor.active_probe.as_ref().and_then(|active| {
                (active.generation == generation && active.phase == ProbePhase::Complete)
                    .then_some(active.outcome)
            });
            if let Some(outcome) = completed {
                monitor.acknowledged_fence_generation = generation;
                monitor.active_probe = None;
                return merge_outcome(outcome, monitor.terminal);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                monitor.active_probe = None;
                publish_terminal(self.shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
                return FenceOutcome::Unknown;
            }
            let (next, wait) = self
                .shared
                .changed
                .wait_timeout(monitor, remaining)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            monitor = next;
            if wait.timed_out() {
                monitor.active_probe = None;
                publish_terminal(self.shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
                return FenceOutcome::Unknown;
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
        self.shared.state.publish(FenceOutcome::Unknown);
        {
            let mut monitor = self
                .shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            monitor.terminal = merge_outcome(monitor.terminal, FenceOutcome::Unknown);
            monitor.cancellation_requested = true;
            monitor.teardown_started = true;
            monitor.publication_open = false;
            if matches!(
                monitor.worker_state,
                WorkerState::Starting | WorkerState::Armed
            ) {
                monitor.worker_state = WorkerState::Cancelling;
            }
            self.shared.changed.notify_all();
        }
        #[cfg(test)]
        if let Some(control) = &self.shared.control {
            control.record_cancel_requested();
        }

        let teardown_started = Instant::now();
        let mut target_miss_recorded = false;
        let mut monitor = self
            .shared
            .monitor
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        while monitor.worker_state != WorkerState::NativeComplete
            && monitor.worker_state != WorkerState::Joined
        {
            drop(monitor);
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
            monitor = self
                .shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if !target_miss_recorded && teardown_started.elapsed() >= FENCE_TIMEOUT {
                target_miss_recorded = true;
                monitor.teardown_target_missed = true;
                #[cfg(test)]
                if let Some(control) = &self.shared.control {
                    control.record_teardown_target_miss();
                }
            }
            if monitor.worker_state != WorkerState::NativeComplete
                && monitor.worker_state != WorkerState::Joined
            {
                let until_target = FENCE_TIMEOUT.saturating_sub(teardown_started.elapsed());
                let wait = if until_target.is_zero() {
                    CANCELLATION_RETRY_INTERVAL
                } else {
                    CANCELLATION_RETRY_INTERVAL.min(until_target)
                };
                let (next, _) = self
                    .shared
                    .changed
                    .wait_timeout(monitor, wait)
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                monitor = next;
            }
        }
        drop(monitor);

        let join_failed = self
            .worker
            .get_mut()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take()
            .is_some_and(|worker| worker.join().is_err());
        {
            let mut monitor = self
                .shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            monitor.worker_state = WorkerState::Joined;
            self.shared.changed.notify_all();
        }
        #[cfg(test)]
        if let Some(control) = &self.shared.control {
            control.record_join();
        }
        if join_failed {
            self.shared.state.publish(FenceOutcome::Unknown);
        }

        let active_probe = self
            .shared
            .monitor
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .active_probe
            .take()
            .map(|probe| probe.name);
        if let Some(probe) = active_probe {
            let _ = self.shared.remove_probe(&probe);
        }

        self.shared
            .notification_handle
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take();
        self.shared
            .worker_thread
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take();
        #[cfg(test)]
        self.shared
            .cancellation_resource_liveness
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take();
    }
}

fn notification_worker_entry(shared: Arc<WorkerShared>) {
    let _terminal = WorkerTerminalGuard {
        shared: shared.clone(),
    };
    let _ = panic::catch_unwind(AssertUnwindSafe(|| notification_worker(shared)));
}

fn notification_worker(shared: Arc<WorkerShared>) {
    let mut buffer = Box::new([0_u8; NOTIFICATION_BUFFER_SIZE]);
    #[cfg(test)]
    let _buffer_liveness = {
        let liveness = Arc::new(());
        if let Some(control) = &shared.control {
            control.record_buffer_liveness(Arc::downgrade(&liveness));
        }
        liveness
    };
    let worker_thread = match duplicate_current_thread_handle() {
        Ok(worker_thread) => worker_thread,
        Err(_) => {
            let mut monitor = shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            publish_terminal(shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
            return;
        }
    };
    #[cfg(test)]
    let cancellation_resource_liveness = {
        let liveness = Arc::new(());
        if let Some(control) = &shared.control {
            control.record_resource_liveness(Arc::downgrade(&liveness));
        }
        liveness
    };
    *shared
        .worker_thread
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(worker_thread);
    #[cfg(test)]
    {
        *shared
            .cancellation_resource_liveness
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) =
            Some(cancellation_resource_liveness);
    }

    {
        let mut monitor = shared
            .monitor
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if monitor.cancellation_requested {
            return;
        }
        monitor.worker_state = WorkerState::Armed;
        shared.changed.notify_all();
    }

    loop {
        {
            let mut monitor = shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if monitor.cancellation_requested {
                return;
            }
            monitor.native_read_pending = true;
            #[cfg(test)]
            if let Some(control) = &shared.control {
                control.record_read_pending(true);
            }
        }
        let pending_read = PendingRead {
            shared: shared.as_ref(),
        };
        #[cfg(test)]
        if let Some(control) = &shared.control {
            control.before_native_read();
        }
        if shared.cancel_requested() {
            return;
        }

        let read_result = read_directory_changes(shared.as_ref(), buffer.as_mut_slice());
        drop(pending_read);

        let bytes_returned = match read_result {
            Ok(bytes_returned) => bytes_returned,
            Err(error)
                if shared.cancel_requested()
                    && error.raw_os_error() == Some(ERROR_OPERATION_ABORTED as i32) =>
            {
                return;
            }
            Err(_) => {
                let mut monitor = shared
                    .monitor
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                publish_terminal(shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
                return;
            }
        };
        if shared.cancel_requested() {
            return;
        }
        if bytes_returned == 0 {
            let mut monitor = shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            publish_terminal(shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
            return;
        }
        let used = usize::try_from(bytes_returned).unwrap_or(usize::MAX);
        #[cfg(test)]
        if let Some(control) = &shared.control {
            control.record_notification_batch_enqueued();
        }
        let fold_result =
            fold_notification_buffer(shared.as_ref(), buffer.get(..used).unwrap_or_default());
        #[cfg(test)]
        if let Some(control) = &shared.control {
            control.record_notification_batch_dequeued();
            if fold_result.is_ok() {
                control.after_successful_injected_batch();
            } else {
                control.before_parser_error_publication();
            }
        }
        if fold_result.is_err() {
            let mut monitor = shared
                .monitor
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            publish_terminal(shared.as_ref(), &mut monitor, FenceOutcome::Unknown);
            return;
        }
    }
}

fn complete_pending_read(shared: &WorkerShared) {
    let read_was_pending = shared
        .monitor
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .native_read_pending;
    if !read_was_pending {
        return;
    }
    #[cfg(test)]
    let teardown_completion = shared
        .monitor
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .teardown_started;
    #[cfg(test)]
    if teardown_completion {
        if let Some(control) = &shared.control {
            control.before_native_completion();
        }
    }
    let mut monitor = shared
        .monitor
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    monitor.native_read_pending = false;
    #[cfg(test)]
    if let Some(control) = &shared.control {
        control.record_read_pending(false);
        if teardown_completion {
            control.record_native_completion();
        }
    }
    shared.changed.notify_all();
}

fn read_directory_changes(shared: &WorkerShared, buffer: &mut [u8]) -> io::Result<u32> {
    #[cfg(test)]
    if let Some(control) = &shared.control {
        if let Some(fault) = control.take_next_worker_fault() {
            return match fault {
                WorkerFaultForTest::Overflow => Ok(0),
                WorkerFaultForTest::MalformedBatch => {
                    buffer[0] = 0xff;
                    Ok(1)
                }
                WorkerFaultForTest::Panic => {
                    panic!("injected Windows object lease worker panic");
                }
                WorkerFaultForTest::LoseMonitorHandle => {
                    let file = shared
                        .notification_handle
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner())
                        .take()
                        .ok_or_else(|| {
                            io::Error::new(
                                io::ErrorKind::BrokenPipe,
                                "Windows object lease notification handle is unavailable",
                            )
                        })?;
                    let stale_handle = file.as_raw_handle();
                    drop(file);
                    read_directory_changes_from_handle(stale_handle, buffer)
                }
            };
        }
    }

    let notification_handle = shared
        .notification_handle
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let file = notification_handle.as_ref().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::BrokenPipe,
            "Windows object lease notification handle is unavailable",
        )
    })?;
    #[cfg(test)]
    {
        let result = read_directory_changes_from_handle(file.as_raw_handle(), buffer);
        if result.is_ok() {
            if let Some(batch) = shared
                .control
                .as_ref()
                .and_then(WindowsLeaseTestControl::take_injected_notification_batch)
            {
                return match batch {
                    InjectedNotificationBatchForTest::Semantic(notifications) => {
                        encode_notifications_for_test(buffer, &notifications)
                    }
                    InjectedNotificationBatchForTest::MalformedTail(notifications) => {
                        encode_malformed_tail_for_test(buffer, &notifications)
                    }
                };
            }
        }
        result
    }
    #[cfg(not(test))]
    read_directory_changes_from_handle(file.as_raw_handle(), buffer)
}

fn read_directory_changes_from_handle(
    handle: std::os::windows::io::RawHandle,
    buffer: &mut [u8],
) -> io::Result<u32> {
    let mut bytes_returned = 0_u32;
    // SAFETY: production callers hold an owned capability-relative directory handle for the
    // synchronous call. The handle-loss regression deliberately supplies a just-closed handle
    // and verifies that the native API fails; the writable buffer and byte-count remain valid.
    let result = unsafe {
        ReadDirectoryChangesW(
            raw_handle(handle),
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
        Err(io::Error::last_os_error())
    } else {
        Ok(bytes_returned)
    }
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

fn merge_outcome(current: FenceOutcome, next: FenceOutcome) -> FenceOutcome {
    match (current, next) {
        (FenceOutcome::Unknown, _) | (_, FenceOutcome::Unknown) => FenceOutcome::Unknown,
        (FenceOutcome::DirtyAll, _) | (_, FenceOutcome::DirtyAll) => FenceOutcome::DirtyAll,
        (FenceOutcome::Clean, FenceOutcome::Clean) => FenceOutcome::Clean,
    }
}

fn publish_terminal(
    shared: &WorkerShared,
    monitor: &mut WindowsMonitorState,
    outcome: FenceOutcome,
) {
    monitor.terminal = merge_outcome(monitor.terminal, outcome);
    shared.state.publish(monitor.terminal);
    shared.changed.notify_all();
}

#[cfg(test)]
fn encode_notifications_for_test(
    buffer: &mut [u8],
    notifications: &[Notification],
) -> io::Result<u32> {
    let mut offset = 0_usize;
    for (index, notification) in notifications.iter().enumerate() {
        let (action, name) = match notification {
            Notification::Added(name) => (FILE_ACTION_ADDED, name),
            Notification::Removed(name) => (FILE_ACTION_REMOVED, name),
            Notification::Modified(name) => (FILE_ACTION_MODIFIED, name),
            Notification::RenamedOld(name) => (FILE_ACTION_RENAMED_OLD_NAME, name),
            Notification::RenamedNew(name) => (FILE_ACTION_RENAMED_NEW_NAME, name),
        };
        let encoded_name: Vec<u16> = name.encode_utf16().collect();
        let name_length = encoded_name.len().checked_mul(2).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "notification name is too large",
            )
        })?;
        let raw_length = 12_usize.checked_add(name_length).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "notification record is too large",
            )
        })?;
        let is_last = index + 1 == notifications.len();
        let record_length = if is_last {
            raw_length
        } else {
            raw_length
                .checked_add(3)
                .map(|length| length & !3)
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "notification record is too large",
                    )
                })?
        };
        let record = buffer
            .get_mut(offset..offset + record_length)
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "notification batch exceeds fixed buffer",
                )
            })?;
        record.fill(0);
        let next = if is_last {
            0
        } else {
            u32::try_from(record_length).map_err(|_| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "notification record is too large",
                )
            })?
        };
        record[0..4].copy_from_slice(&next.to_le_bytes());
        record[4..8].copy_from_slice(&action.to_le_bytes());
        record[8..12].copy_from_slice(
            &u32::try_from(name_length)
                .map_err(|_| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "notification name is too large",
                    )
                })?
                .to_le_bytes(),
        );
        for (encoded, destination) in encoded_name
            .iter()
            .zip(record[12..12 + name_length].chunks_exact_mut(2))
        {
            destination.copy_from_slice(&encoded.to_le_bytes());
        }
        offset += record_length;
    }
    u32::try_from(offset).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "notification batch is too large",
        )
    })
}

#[cfg(test)]
fn encode_malformed_tail_for_test(
    buffer: &mut [u8],
    notifications: &[Notification],
) -> io::Result<u32> {
    let used = encode_notifications_for_test(buffer, notifications)?;
    let first_next = usize::try_from(
        read_u32(buffer.get(0..4).ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "missing first notification")
        })?)
        .map_err(|()| io::Error::new(io::ErrorKind::InvalidInput, "invalid first header"))?,
    )
    .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid first notification"))?;
    let second_next = usize::try_from(
        read_u32(buffer.get(first_next..first_next + 4).ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidInput, "missing second notification")
        })?)
        .map_err(|()| io::Error::new(io::ErrorKind::InvalidInput, "invalid second header"))?,
    )
    .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid second notification"))?;
    let malformed_offset = first_next.checked_add(second_next).ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "notification offset overflow")
    })?;
    buffer
        .get_mut(malformed_offset + 8..malformed_offset + 12)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing malformed tail"))?
        .copy_from_slice(&1_u32.to_le_bytes());
    Ok(used)
}

fn apply_observed_outcome(
    shared: &WorkerShared,
    monitor: &mut WindowsMonitorState,
    outcome: FenceOutcome,
) {
    if outcome == FenceOutcome::Clean {
        return;
    }
    if outcome == FenceOutcome::Unknown {
        publish_terminal(shared, monitor, FenceOutcome::Unknown);
        return;
    }
    match monitor.active_probe.as_mut() {
        Some(active) if active.phase == ProbePhase::Complete => {
            monitor.deferred_outcome = merge_outcome(monitor.deferred_outcome, outcome);
        }
        Some(active) => {
            active.outcome = merge_outcome(active.outcome, outcome);
            publish_terminal(shared, monitor, outcome);
        }
        None => publish_terminal(shared, monitor, outcome),
    }
}

fn fold_notification_buffer(shared: &WorkerShared, buffer: &[u8]) -> Result<(), ()> {
    const HEADER_SIZE: usize = 12;
    let mut offset = 0_usize;
    let mut monitor = shared
        .monitor
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let result = (|| loop {
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
        let notification = match action {
            FILE_ACTION_ADDED => Notification::Added(name),
            FILE_ACTION_REMOVED => Notification::Removed(name),
            FILE_ACTION_MODIFIED => Notification::Modified(name),
            FILE_ACTION_RENAMED_OLD_NAME => Notification::RenamedOld(name),
            FILE_ACTION_RENAMED_NEW_NAME => Notification::RenamedNew(name),
            _ => return Err(()),
        };
        #[cfg(test)]
        let is_read_pause_wake = shared
            .control
            .as_ref()
            .is_some_and(|control| control.is_read_pause_wake_notification(&notification));
        #[cfg(not(test))]
        let is_read_pause_wake = false;
        if !is_read_pause_wake {
            fold_notification(shared, &mut monitor, notification);
        }
        if next == 0 {
            if offset + HEADER_SIZE + name_length != buffer.len() {
                return Err(());
            }
            if monitor.pending_rename {
                monitor.pending_rename = false;
                apply_observed_outcome(shared, &mut monitor, FenceOutcome::Unknown);
            }
            return Ok(());
        }
        if next % 4 != 0 || next < HEADER_SIZE + name_length {
            return Err(());
        }
        offset = offset.checked_add(next).ok_or(())?;
        if offset >= buffer.len() {
            return Err(());
        }
    })();
    if result.is_err() {
        monitor.pending_rename = false;
        apply_observed_outcome(shared, &mut monitor, FenceOutcome::Unknown);
    }
    #[cfg(test)]
    if let Some(control) = &shared.control {
        control.record_boundary_snapshot(&monitor);
    }
    shared.changed.notify_all();
    result
}

fn fold_notification(
    shared: &WorkerShared,
    monitor: &mut WindowsMonitorState,
    notification: Notification,
) {
    if !monitor.publication_open || monitor.terminal == FenceOutcome::Unknown {
        return;
    }

    let (name, action) = match notification {
        Notification::Added(name) => (name, FILE_ACTION_ADDED),
        Notification::Removed(name) => (name, FILE_ACTION_REMOVED),
        Notification::Modified(name) => (name, FILE_ACTION_MODIFIED),
        Notification::RenamedOld(name) => (name, FILE_ACTION_RENAMED_OLD_NAME),
        Notification::RenamedNew(name) => (name, FILE_ACTION_RENAMED_NEW_NAME),
    };

    let rename_outcome = if action == FILE_ACTION_RENAMED_OLD_NAME {
        if monitor.pending_rename {
            Some(FenceOutcome::Unknown)
        } else {
            monitor.pending_rename = true;
            None
        }
    } else if action == FILE_ACTION_RENAMED_NEW_NAME {
        if monitor.pending_rename {
            monitor.pending_rename = false;
            None
        } else {
            Some(FenceOutcome::Unknown)
        }
    } else if monitor.pending_rename {
        monitor.pending_rename = false;
        Some(FenceOutcome::Unknown)
    } else {
        None
    };
    if let Some(outcome) = rename_outcome {
        apply_observed_outcome(shared, monitor, outcome);
        return;
    }

    let Some(active) = monitor.active_probe.as_mut() else {
        apply_observed_outcome(shared, monitor, FenceOutcome::DirtyAll);
        return;
    };
    if name != active.name {
        let outcome = if name.eq_ignore_ascii_case(&active.name) {
            FenceOutcome::Unknown
        } else {
            FenceOutcome::DirtyAll
        };
        apply_observed_outcome(shared, monitor, outcome);
        return;
    }

    match (active.phase, action) {
        (ProbePhase::AwaitCreate, FILE_ACTION_ADDED) => {
            active.phase = ProbePhase::AwaitDelete;
        }
        (ProbePhase::AwaitDelete, FILE_ACTION_REMOVED) => {
            active.phase = ProbePhase::Complete;
            monitor.acknowledged_fence_generation = active.generation;
            monitor.boundary_progress = monitor.boundary_progress.wrapping_add(1);
        }
        _ => apply_observed_outcome(shared, monitor, FenceOutcome::Unknown),
    }
}

fn read_u32(bytes: &[u8]) -> Result<u32, ()> {
    let encoded: [u8; 4] = bytes.try_into().map_err(|_| ())?;
    Ok(u32::from_le_bytes(encoded))
}

fn random_probe_name() -> io::Result<String> {
    let mut random = [0_u8; 3];
    getrandom(&mut random).map_err(io::Error::other)?;
    Ok(format!(
        "AL{:02X}{:02X}{:02X}.TMP",
        random[0], random[1], random[2]
    ))
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
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(in crate::transaction) enum ConstructionEventForTest {
    MonitorArmed,
    AnchorCreated,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(in crate::transaction) enum WorkerFaultForTest {
    Overflow,
    MalformedBatch,
    Panic,
    LoseMonitorHandle,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(in crate::transaction) struct BoundarySnapshotForTest {
    pub(in crate::transaction) terminal: FenceOutcome,
    pub(in crate::transaction) acknowledged_fence_generation: u64,
    pub(in crate::transaction) boundary_progress: u64,
    pub(in crate::transaction) deferred_outcome: FenceOutcome,
    pub(in crate::transaction) active_probe_complete: bool,
}

#[cfg(test)]
#[derive(Debug)]
enum InjectedNotificationBatchForTest {
    Semantic(Vec<Notification>),
    MalformedTail(Vec<Notification>),
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
enum CleanupFailureMode {
    #[default]
    None,
    Once,
    Persistent,
}

#[cfg(test)]
#[derive(Debug, Default)]
struct ReadPauseState {
    requested: bool,
    paused: bool,
    released: bool,
}

#[cfg(test)]
#[derive(Debug, Default)]
struct CompletionPauseState {
    requested: bool,
    paused: bool,
    released: bool,
}

#[cfg(test)]
#[derive(Debug, Default)]
struct WindowsLeaseTestControlInner {
    probe_names: Mutex<VecDeque<String>>,
    probe_attempts: AtomicUsize,
    cleanup_failure: Mutex<CleanupFailureMode>,
    cleanup_blocker: Mutex<Option<File>>,
    worker_faults: Mutex<VecDeque<WorkerFaultForTest>>,
    injected_notification_batches: Mutex<VecDeque<InjectedNotificationBatchForTest>>,
    injected_batch_in_flight: AtomicBool,
    pause_after_injected_batch: Mutex<CompletionPauseState>,
    pause_after_injected_batch_changed: Condvar,
    pause_parser_error_publication: Mutex<CompletionPauseState>,
    pause_parser_error_publication_changed: Condvar,
    read_pause: Mutex<ReadPauseState>,
    read_pause_changed: Condvar,
    read_pause_wake_active: AtomicBool,
    completion_pause: Mutex<CompletionPauseState>,
    completion_pause_changed: Condvar,
    read_pending: AtomicBool,
    cancel_requested: AtomicBool,
    native_batch_count: AtomicUsize,
    retained_notification_batches: AtomicUsize,
    retained_notification_batch_high_water: AtomicUsize,
    native_completion_count: AtomicUsize,
    teardown_target_miss_count: AtomicUsize,
    join_count: AtomicUsize,
    state: Mutex<Option<Arc<MonitorStateCell>>>,
    construction_events: Mutex<Vec<ConstructionEventForTest>>,
    resource_liveness: Mutex<Vec<std::sync::Weak<()>>>,
    buffer_liveness: Mutex<Option<std::sync::Weak<()>>>,
    boundary_snapshot: Mutex<Option<BoundarySnapshotForTest>>,
    capture_next_boundary_snapshot: AtomicBool,
    freeze_boundary_snapshot: AtomicBool,
}

#[cfg(test)]
#[derive(Clone, Debug, Default)]
pub(in crate::transaction) struct WindowsLeaseTestControl {
    inner: Arc<WindowsLeaseTestControlInner>,
}

#[cfg(test)]
impl WindowsLeaseTestControl {
    pub(in crate::transaction) fn new() -> Self {
        Self::default()
    }

    pub(in crate::transaction) fn queue_probe_name(&self, name: &str) {
        self.inner
            .probe_names
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push_back(name.to_owned());
    }

    fn next_probe_name(&self) -> io::Result<String> {
        self.inner.probe_attempts.fetch_add(1, Ordering::SeqCst);
        self.inner
            .probe_names
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .pop_front()
            .map_or_else(random_probe_name, Ok)
    }

    pub(in crate::transaction) fn probe_attempt_count(&self) -> usize {
        self.inner.probe_attempts.load(Ordering::SeqCst)
    }

    pub(in crate::transaction) fn fail_next_probe_cleanup_once(&self) {
        *self
            .inner
            .cleanup_failure
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = CleanupFailureMode::Once;
    }

    pub(in crate::transaction) fn fail_probe_cleanup_persistently(&self) {
        *self
            .inner
            .cleanup_failure
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = CleanupFailureMode::Persistent;
    }

    fn before_probe_cleanup(&self, objects_dir: &Dir, probe: &str) -> io::Result<()> {
        let mode = *self
            .inner
            .cleanup_failure
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if mode == CleanupFailureMode::None {
            return Ok(());
        }
        let mut blocker = self
            .inner
            .cleanup_blocker
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if blocker.is_none() {
            let mut options = OpenOptions::new();
            options
                .read(true)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE);
            *blocker = Some(objects_dir.open_with(probe, &options)?.into_std());
        }
        Ok(())
    }

    fn after_probe_cleanup(&self, cleanup_succeeded: bool) {
        let mut mode = self
            .inner
            .cleanup_failure
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if *mode == CleanupFailureMode::Once {
            debug_assert!(!cleanup_succeeded);
            *mode = CleanupFailureMode::None;
            self.inner
                .cleanup_blocker
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .take();
        }
    }

    pub(in crate::transaction) fn release_probe_cleanup_blocker(&self) {
        *self
            .inner
            .cleanup_failure
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = CleanupFailureMode::None;
        self.inner
            .cleanup_blocker
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take();
    }

    pub(in crate::transaction) fn inject_next_worker_fault(&self, fault: WorkerFaultForTest) {
        self.inner
            .worker_faults
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push_back(fault);
    }

    fn take_next_worker_fault(&self) -> Option<WorkerFaultForTest> {
        self.inner
            .worker_faults
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .pop_front()
    }

    pub(in crate::transaction) fn inject_dirty_fence_batch(&self, probe: &str) {
        self.inner
            .capture_next_boundary_snapshot
            .store(true, Ordering::SeqCst);
        self.inner
            .freeze_boundary_snapshot
            .store(false, Ordering::SeqCst);
        self.inner
            .injected_notification_batches
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push_back(InjectedNotificationBatchForTest::Semantic(vec![
                Notification::Added("ORDINARY.ACORE".into()),
                Notification::Added(probe.into()),
                Notification::Removed(probe.into()),
                Notification::Added("AFTER.ACORE".into()),
            ]));
    }

    pub(in crate::transaction) fn inject_semantic_unknown_fence_batch(&self, probe: &str) {
        self.prepare_boundary_snapshot_capture();
        self.inner
            .pause_after_injected_batch
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .requested = true;
        self.inner
            .injected_notification_batches
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push_back(InjectedNotificationBatchForTest::Semantic(vec![
                Notification::Added(probe.into()),
                Notification::Removed(probe.into()),
                Notification::Added(probe.to_ascii_lowercase()),
            ]));
    }

    pub(in crate::transaction) fn inject_malformed_tail_fence_batch(&self, probe: &str) {
        self.prepare_boundary_snapshot_capture();
        self.inner
            .pause_parser_error_publication
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .requested = true;
        self.inner
            .injected_notification_batches
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push_back(InjectedNotificationBatchForTest::MalformedTail(vec![
                Notification::Added(probe.into()),
                Notification::Removed(probe.into()),
                Notification::Added("MALFORMED.ACORE".into()),
            ]));
    }

    fn prepare_boundary_snapshot_capture(&self) {
        self.inner
            .capture_next_boundary_snapshot
            .store(true, Ordering::SeqCst);
        self.inner
            .freeze_boundary_snapshot
            .store(false, Ordering::SeqCst);
    }

    fn take_injected_notification_batch(&self) -> Option<InjectedNotificationBatchForTest> {
        let batch = self
            .inner
            .injected_notification_batches
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .pop_front();
        if batch.is_some() {
            self.inner
                .injected_batch_in_flight
                .store(true, Ordering::SeqCst);
        }
        batch
    }

    fn after_successful_injected_batch(&self) {
        if !self
            .inner
            .injected_batch_in_flight
            .swap(false, Ordering::SeqCst)
        {
            return;
        }
        let mut pause = self
            .inner
            .pause_after_injected_batch
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if !pause.requested {
            return;
        }
        pause.requested = false;
        pause.paused = true;
        self.inner.pause_after_injected_batch_changed.notify_all();
        while !pause.released {
            pause = self
                .inner
                .pause_after_injected_batch_changed
                .wait(pause)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
        pause.paused = false;
        pause.released = false;
    }

    pub(in crate::transaction) fn wait_until_after_injected_batch_paused(
        &self,
        timeout: Duration,
    ) -> bool {
        let pause = self
            .inner
            .pause_after_injected_batch
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (pause, _) = self
            .inner
            .pause_after_injected_batch_changed
            .wait_timeout_while(pause, timeout, |pause| !pause.paused)
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.paused
    }

    pub(in crate::transaction) fn release_after_injected_batch(&self) {
        let mut pause = self
            .inner
            .pause_after_injected_batch
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.released = true;
        self.inner.pause_after_injected_batch_changed.notify_all();
    }

    fn before_parser_error_publication(&self) {
        if !self
            .inner
            .injected_batch_in_flight
            .swap(false, Ordering::SeqCst)
        {
            return;
        }
        let mut pause = self
            .inner
            .pause_parser_error_publication
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if !pause.requested {
            return;
        }
        pause.requested = false;
        pause.paused = true;
        self.inner
            .pause_parser_error_publication_changed
            .notify_all();
        while !pause.released {
            pause = self
                .inner
                .pause_parser_error_publication_changed
                .wait(pause)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
        pause.paused = false;
        pause.released = false;
    }

    pub(in crate::transaction) fn wait_until_parser_error_publication_paused(
        &self,
        timeout: Duration,
    ) -> bool {
        let pause = self
            .inner
            .pause_parser_error_publication
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (pause, _) = self
            .inner
            .pause_parser_error_publication_changed
            .wait_timeout_while(pause, timeout, |pause| !pause.paused)
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.paused
    }

    pub(in crate::transaction) fn release_parser_error_publication(&self) {
        let mut pause = self
            .inner
            .pause_parser_error_publication
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.released = true;
        self.inner
            .pause_parser_error_publication_changed
            .notify_all();
    }

    fn record_boundary_snapshot(&self, monitor: &WindowsMonitorState) {
        let mut snapshot = self
            .inner
            .boundary_snapshot
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let captured = self
            .inner
            .capture_next_boundary_snapshot
            .swap(false, Ordering::SeqCst);
        if captured {
            self.inner
                .freeze_boundary_snapshot
                .store(true, Ordering::SeqCst);
        }
        if captured || !self.inner.freeze_boundary_snapshot.load(Ordering::SeqCst) {
            *snapshot = Some(BoundarySnapshotForTest {
                terminal: monitor.terminal,
                acknowledged_fence_generation: monitor.acknowledged_fence_generation,
                boundary_progress: monitor.boundary_progress,
                deferred_outcome: monitor.deferred_outcome,
                active_probe_complete: monitor
                    .active_probe
                    .as_ref()
                    .is_some_and(|probe| probe.phase == ProbePhase::Complete),
            });
        }
    }

    pub(in crate::transaction) fn boundary_snapshot(&self) -> Option<BoundarySnapshotForTest> {
        *self
            .inner
            .boundary_snapshot
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub(in crate::transaction) fn pause_next_read(&self) {
        let mut pause = self
            .inner
            .read_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.requested = true;
        pause.released = false;
        self.inner
            .read_pause_wake_active
            .store(true, Ordering::SeqCst);
    }

    fn is_read_pause_wake_notification(&self, notification: &Notification) -> bool {
        if !self.inner.read_pause_wake_active.load(Ordering::SeqCst) {
            return false;
        }
        // The existing fault/cancellation regressions use this one test-only file solely
        // to release the current blocking native read before pausing the next one. Excluding
        // that fixture event keeps those tests focused on the injected worker terminal path;
        // production notification folding never filters by name.
        let name = match notification {
            Notification::Added(name)
            | Notification::Removed(name)
            | Notification::Modified(name)
            | Notification::RenamedOld(name)
            | Notification::RenamedNew(name) => name,
        };
        name == "wake-current-read"
    }

    fn before_native_read(&self) {
        let mut pause = self
            .inner
            .read_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if !pause.requested {
            return;
        }
        pause.requested = false;
        pause.paused = true;
        self.inner.read_pause_changed.notify_all();
        while !pause.released {
            pause = self
                .inner
                .read_pause_changed
                .wait(pause)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
        pause.paused = false;
        pause.released = false;
        self.inner.read_pause_changed.notify_all();
    }

    pub(in crate::transaction) fn wait_until_read_paused(&self, timeout: Duration) -> bool {
        let pause = self
            .inner
            .read_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if pause.paused {
            return true;
        }
        let (pause, _) = self
            .inner
            .read_pause_changed
            .wait_timeout_while(pause, timeout, |pause| !pause.paused)
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.paused
    }

    pub(in crate::transaction) fn release_read_pause(&self) {
        let mut pause = self
            .inner
            .read_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.released = true;
        self.inner
            .read_pause_wake_active
            .store(false, Ordering::SeqCst);
        self.inner.read_pause_changed.notify_all();
    }

    fn record_read_pending(&self, read_pending: bool) {
        self.inner
            .read_pending
            .store(read_pending, Ordering::SeqCst);
    }

    pub(in crate::transaction) fn read_pending(&self) -> bool {
        self.inner.read_pending.load(Ordering::SeqCst)
    }

    pub(in crate::transaction) fn wait_until_read_idle(&self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        while self.read_pending() {
            if Instant::now() >= deadline {
                return false;
            }
            thread::yield_now();
        }
        true
    }

    fn record_resource_liveness(&self, liveness: std::sync::Weak<()>) {
        self.inner
            .resource_liveness
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(liveness);
    }

    pub(in crate::transaction) fn worker_resources_alive(&self) -> bool {
        self.live_monitor_resource_count() != 0
    }

    pub(in crate::transaction) fn live_monitor_resource_count(&self) -> usize {
        self.inner
            .resource_liveness
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .iter()
            .filter(|liveness| liveness.strong_count() != 0)
            .count()
    }

    fn record_buffer_liveness(&self, liveness: std::sync::Weak<()>) {
        *self
            .inner
            .buffer_liveness
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(liveness);
    }

    pub(in crate::transaction) fn native_buffer_alive(&self) -> bool {
        self.inner
            .buffer_liveness
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_ref()
            .is_some_and(|liveness| liveness.strong_count() != 0)
    }

    fn record_cancel_requested(&self) {
        self.inner.cancel_requested.store(true, Ordering::SeqCst);
        self.inner.read_pause_changed.notify_all();
    }

    pub(in crate::transaction) fn wait_until_cancel_requested(&self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        while !self.inner.cancel_requested.load(Ordering::SeqCst) {
            if Instant::now() >= deadline {
                return false;
            }
            thread::yield_now();
        }
        true
    }

    fn record_notification_batch_enqueued(&self) {
        self.inner.native_batch_count.fetch_add(1, Ordering::SeqCst);
        let retained = self
            .inner
            .retained_notification_batches
            .fetch_add(1, Ordering::SeqCst)
            + 1;
        self.inner
            .retained_notification_batch_high_water
            .fetch_max(retained, Ordering::SeqCst);
    }

    fn record_notification_batch_dequeued(&self) {
        let _ = self.inner.retained_notification_batches.fetch_update(
            Ordering::SeqCst,
            Ordering::SeqCst,
            |retained| retained.checked_sub(1),
        );
    }

    pub(in crate::transaction) fn native_batch_count(&self) -> usize {
        self.inner.native_batch_count.load(Ordering::SeqCst)
    }

    pub(in crate::transaction) fn wait_until_native_batch_count(
        &self,
        expected: usize,
        timeout: Duration,
    ) -> bool {
        let deadline = Instant::now() + timeout;
        while self.native_batch_count() < expected {
            if Instant::now() >= deadline {
                return false;
            }
            thread::yield_now();
        }
        true
    }

    pub(in crate::transaction) fn retained_notification_batch_high_water(&self) -> usize {
        self.inner
            .retained_notification_batch_high_water
            .load(Ordering::SeqCst)
    }

    pub(in crate::transaction) fn pause_next_native_completion(&self) {
        let mut pause = self
            .inner
            .completion_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.requested = true;
        pause.released = false;
    }

    fn before_native_completion(&self) {
        let mut pause = self
            .inner
            .completion_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if !pause.requested {
            return;
        }
        pause.requested = false;
        pause.paused = true;
        self.inner.completion_pause_changed.notify_all();
        while !pause.released {
            pause = self
                .inner
                .completion_pause_changed
                .wait(pause)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
        pause.paused = false;
        pause.released = false;
        self.inner.completion_pause_changed.notify_all();
    }

    pub(in crate::transaction) fn wait_until_native_completion_paused(
        &self,
        timeout: Duration,
    ) -> bool {
        let pause = self
            .inner
            .completion_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if pause.paused {
            return true;
        }
        let (pause, _) = self
            .inner
            .completion_pause_changed
            .wait_timeout_while(pause, timeout, |pause| !pause.paused)
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.paused
    }

    pub(in crate::transaction) fn release_native_completion(&self) {
        let mut pause = self
            .inner
            .completion_pause
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        pause.released = true;
        self.inner.completion_pause_changed.notify_all();
    }

    fn record_native_completion(&self) {
        self.inner
            .native_completion_count
            .fetch_add(1, Ordering::SeqCst);
    }

    pub(in crate::transaction) fn native_completion_count(&self) -> usize {
        self.inner.native_completion_count.load(Ordering::SeqCst)
    }

    pub(in crate::transaction) fn teardown_target_miss_count(&self) -> usize {
        self.inner.teardown_target_miss_count.load(Ordering::SeqCst)
    }

    fn record_teardown_target_miss(&self) {
        self.inner
            .teardown_target_miss_count
            .fetch_add(1, Ordering::SeqCst);
    }

    fn record_join(&self) {
        self.inner.join_count.fetch_add(1, Ordering::SeqCst);
    }

    pub(in crate::transaction) fn join_count(&self) -> usize {
        self.inner.join_count.load(Ordering::SeqCst)
    }

    fn record_state(&self, state: Arc<MonitorStateCell>) {
        *self
            .inner
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(state);
    }

    pub(in crate::transaction) fn monitor_state(&self) -> Option<super::MonitorState> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_ref()
            .map(|state| state.state())
    }

    pub(in crate::transaction) fn wait_until_state(
        &self,
        expected: super::MonitorState,
        timeout: Duration,
    ) -> bool {
        let deadline = Instant::now() + timeout;
        loop {
            if self.monitor_state() == Some(expected) {
                return true;
            }
            if Instant::now() >= deadline {
                return false;
            }
            thread::yield_now();
        }
    }

    fn record_construction_event(&self, event: ConstructionEventForTest) {
        self.inner
            .construction_events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .push(event);
    }

    pub(in crate::transaction) fn construction_events(&self) -> Vec<ConstructionEventForTest> {
        self.inner
            .construction_events
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
    }
}

#[cfg(test)]
#[derive(Clone, Debug, Eq, PartialEq)]
pub(in crate::transaction) enum TestNotification {
    Added(String),
    Removed(String),
    Modified(String),
    RenamedOld(String),
    RenamedNew(String),
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
        });
    }
    notification_outcome(probe, &native, cleanup_ok)
}

#[cfg(test)]
pub(in crate::transaction) fn probe_name_for_test() -> io::Result<String> {
    random_probe_name()
}
