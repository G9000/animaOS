#![cfg_attr(not(any(test, target_os = "macos")), allow(dead_code))]

use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use serde::Serialize;

#[derive(Debug)]
struct Arguments {
    output: PathBuf,
    object_count: usize,
    warmups: Option<usize>,
    samples: Option<usize>,
    race_samples: Option<usize>,
    mount_restored_path: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorDiagnostic<'a> {
    error: &'a str,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CharacterizationReport {
    schema_version: u32,
    platform: &'static str,
    hardware: HardwareReport,
    os: OsReport,
    filesystem: FilesystemReport,
    build: BuildReport,
    object_count: usize,
    sampling: SamplingReport,
    safe_open: DistributionReport,
    lease: DistributionReport,
    resources: ResourceReport,
    lifecycle: LifecycleReport,
    restored_path: RestoredPathReport,
    outcomes: OutcomeReport,
    ordered_boundary_proven: bool,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(tag = "mode")]
enum SamplingReport {
    #[serde(rename = "performance")]
    Performance { warmups: usize, samples: usize },
    #[serde(rename = "restoredPathRace")]
    RestoredPathRace {
        #[serde(rename = "raceSamples")]
        race_samples: usize,
    },
}

impl Arguments {
    fn sampling_report(&self) -> SamplingReport {
        match (
            self.warmups,
            self.samples,
            self.race_samples,
            self.mount_restored_path,
        ) {
            (Some(warmups), Some(samples), None, false) => {
                SamplingReport::Performance { warmups, samples }
            }
            (None, None, Some(race_samples), true) => {
                SamplingReport::RestoredPathRace { race_samples }
            }
            _ => unreachable!("arguments are validated before report construction"),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct HardwareReport {
    model: String,
    architecture: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct OsReport {
    version: String,
    build: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct FilesystemReport {
    name: String,
    mount_path: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BuildReport {
    profile: &'static str,
    rustc: String,
    source_commit: String,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DistributionReport {
    p50_ms: f64,
    p95_ms: f64,
    p99_ms: f64,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ResourceReport {
    maximum_descriptor_delta: i64,
    post_teardown_descriptor_delta: i64,
    residue_count: usize,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LifecycleReport {
    creation_passed: bool,
    start_passed: bool,
    callback_panic_contained: bool,
    teardown_passed: bool,
    callback_after_release: bool,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RestoredPathReport {
    tested: bool,
    ancestor_above_volume_covered: bool,
    zero_id_root_changed_rejected_clean: bool,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct OutcomeReport {
    ordinary_events_dirty_all: bool,
    ambiguous_flags_unknown: bool,
    outside_hard_link_rejected: bool,
}

const FSEVENT_MUST_SCAN_SUBDIRS: u32 = 0x0000_0001;
const FSEVENT_USER_DROPPED: u32 = 0x0000_0002;
const FSEVENT_KERNEL_DROPPED: u32 = 0x0000_0004;
const FSEVENT_IDS_WRAPPED: u32 = 0x0000_0008;
const FSEVENT_ROOT_CHANGED: u32 = 0x0000_0020;
const FSEVENT_MOUNT: u32 = 0x0000_0040;
const FSEVENT_UNMOUNT: u32 = 0x0000_0080;
const STREAM_FLAG_NO_DEFER: u32 = 0x0000_0002;
const STREAM_FLAG_WATCH_ROOT: u32 = 0x0000_0004;
const STREAM_FLAG_FILE_EVENTS: u32 = 0x0000_0010;
const STREAM_FLAGS: u32 = STREAM_FLAG_WATCH_ROOT | STREAM_FLAG_FILE_EVENTS | STREAM_FLAG_NO_DEFER;
const FSEVENT_AMBIGUOUS_MASK: u32 = FSEVENT_MUST_SCAN_SUBDIRS
    | FSEVENT_USER_DROPPED
    | FSEVENT_KERNEL_DROPPED
    | FSEVENT_IDS_WRAPPED
    | FSEVENT_ROOT_CHANGED
    | FSEVENT_MOUNT
    | FSEVENT_UNMOUNT;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum GenerationState {
    Clean,
    DirtyAll,
    Unknown,
}

impl GenerationState {
    fn publish(&mut self, incoming: Self) {
        *self = match (*self, incoming) {
            (Self::Unknown, _) | (_, Self::Unknown) => Self::Unknown,
            (Self::DirtyAll, _) | (_, Self::DirtyAll) => Self::DirtyAll,
            (Self::Clean, Self::Clean) => Self::Clean,
        };
    }
}

fn classify_callback_batch(flags: u32, callback_panicked: bool) -> GenerationState {
    if callback_panicked || flags & FSEVENT_AMBIGUOUS_MASK != 0 {
        GenerationState::Unknown
    } else {
        GenerationState::DirtyAll
    }
}

fn flush_target_acknowledged(target: u64, published: u64) -> bool {
    target != 0 && published != 0 && published >= target
}

fn publish_event_id(maximum: &mut u64, event_id: u64) {
    if event_id != 0 {
        *maximum = (*maximum).max(event_id);
    }
}

fn descriptor_chain_plan(components: &[&str]) -> Result<Vec<String>, &'static str> {
    if components.len() + 1 > 64 {
        return Err("descriptor chain exceeds 64 entries");
    }
    let mut chain = Vec::with_capacity(components.len() + 1);
    chain.push("/".to_owned());
    let mut current = String::new();
    for component in components {
        if component.is_empty()
            || *component == "."
            || *component == ".."
            || component.contains('/')
        {
            return Err("descriptor chain component is not canonical");
        }
        current.push('/');
        current.push_str(component);
        chain.push(current.clone());
    }
    Ok(chain)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StreamOperation {
    SetSerialQueue,
    Start,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct NativeStreamPlan {
    since_when: u64,
    latency_millis: u64,
    flags: u32,
    operations: [StreamOperation; 2],
}

fn native_stream_plan() -> NativeStreamPlan {
    NativeStreamPlan {
        since_when: u64::MAX,
        latency_millis: 50,
        flags: STREAM_FLAGS,
        operations: [StreamOperation::SetSerialQueue, StreamOperation::Start],
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum KqueueClassification {
    Quiet,
    Unknown,
}

fn classify_kqueue_event(has_error: bool, has_eof: bool, vnode_notes: u32) -> KqueueClassification {
    if has_error || has_eof || vnode_notes != 0 {
        KqueueClassification::Unknown
    } else {
        KqueueClassification::Quiet
    }
}

const PORTABLE_FILE_TYPE_MASK: u32 = 0o170000;
const PORTABLE_REGULAR_MODE: u32 = 0o100000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct PortableStamp {
    device: u64,
    inode: u64,
    length: u64,
    mode: u32,
    links: u64,
}

fn validate_opened_linked_stamp(
    opened: PortableStamp,
    linked: PortableStamp,
) -> Result<PortableStamp, &'static str> {
    if opened.mode & PORTABLE_FILE_TYPE_MASK != PORTABLE_REGULAR_MODE
        || linked.mode & PORTABLE_FILE_TYPE_MASK != PORTABLE_REGULAR_MODE
    {
        return Err("object is not a regular file");
    }
    if opened.length == 0 || linked.length == 0 {
        return Err("object is empty");
    }
    if opened.links != 1 || linked.links != 1 {
        return Err("object does not have exactly one link");
    }
    if opened != linked {
        return Err("opened object and linked object identity differ");
    }
    Ok(opened)
}

fn distribution_from_nanos(samples: &[u128]) -> Result<DistributionReport, &'static str> {
    if samples.is_empty() {
        return Err("distribution requires at least one sample");
    }
    let mut ordered = samples.to_vec();
    ordered.sort_unstable();
    let percentile = |numerator: usize| {
        let rank = (ordered.len() * numerator + 99) / 100;
        ordered[rank.saturating_sub(1).min(ordered.len() - 1)] as f64 / 1_000_000.0
    };
    Ok(DistributionReport {
        p50_ms: percentile(50),
        p95_ms: percentile(95),
        p99_ms: percentile(99),
    })
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum CleanupStep {
    Detach(String),
    Remove(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ApfsDriverPlan {
    image: String,
    mount: String,
    create: Vec<String>,
    attach: Vec<String>,
    cleanup: Vec<CleanupStep>,
    race_paths: Vec<String>,
}

fn apfs_driver_plan(owned_root: &str) -> ApfsDriverPlan {
    let root = owned_root.trim_end_matches('/');
    let image = format!("{root}/corefs-lease.sparseimage");
    let renameable = format!("{root}/renameable");
    let mount = format!("{renameable}/mount");
    ApfsDriverPlan {
        create: vec![
            "hdiutil".to_owned(),
            "create".to_owned(),
            "-size".to_owned(),
            "256m".to_owned(),
            "-fs".to_owned(),
            "APFS".to_owned(),
            "-volname".to_owned(),
            "ANIMA_CORE_LEASE".to_owned(),
            "-type".to_owned(),
            "SPARSE".to_owned(),
            image.clone(),
        ],
        attach: vec![
            "hdiutil".to_owned(),
            "attach".to_owned(),
            "-nobrowse".to_owned(),
            "-mountpoint".to_owned(),
            mount.clone(),
            image.clone(),
        ],
        cleanup: vec![
            CleanupStep::Detach(mount.clone()),
            CleanupStep::Remove(image.clone()),
            CleanupStep::Remove(root.to_owned()),
        ],
        race_paths: vec![
            renameable,
            format!("{mount}/namespace"),
            format!("{mount}/namespace/fs"),
            format!("{mount}/namespace/fs/catalogs"),
            format!("{mount}/namespace/fs/catalogs/objects"),
        ],
        image,
        mount,
    }
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CleanupPhase {
    BeforeStreamCreation,
    CreatedNotScheduled,
    ScheduledStartFailed,
    Started,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CleanupAction {
    Cancel,
    StopStream,
    InvalidateStream,
    BarrierQueue,
    ReleaseStream,
    DropOwner,
    ReleaseQueue,
    CloseKernelQueue,
    CloseDescriptors,
}

#[cfg(test)]
const CLEANUP_BEFORE_CREATE: &[CleanupAction] = &[
    CleanupAction::ReleaseQueue,
    CleanupAction::CloseKernelQueue,
    CleanupAction::CloseDescriptors,
];
#[cfg(test)]
const CLEANUP_CREATED: &[CleanupAction] = &[
    CleanupAction::ReleaseStream,
    CleanupAction::ReleaseQueue,
    CleanupAction::CloseKernelQueue,
    CleanupAction::CloseDescriptors,
];
#[cfg(test)]
const CLEANUP_START_FAILED: &[CleanupAction] = &[
    CleanupAction::InvalidateStream,
    CleanupAction::BarrierQueue,
    CleanupAction::ReleaseStream,
    CleanupAction::ReleaseQueue,
    CleanupAction::CloseKernelQueue,
    CleanupAction::CloseDescriptors,
];
#[cfg(test)]
const CLEANUP_STARTED: &[CleanupAction] = &[
    CleanupAction::Cancel,
    CleanupAction::StopStream,
    CleanupAction::InvalidateStream,
    CleanupAction::BarrierQueue,
    CleanupAction::ReleaseStream,
    CleanupAction::DropOwner,
    CleanupAction::ReleaseQueue,
    CleanupAction::CloseKernelQueue,
    CleanupAction::CloseDescriptors,
];

#[cfg(test)]
fn cleanup_actions(phase: CleanupPhase) -> &'static [CleanupAction] {
    match phase {
        CleanupPhase::BeforeStreamCreation => CLEANUP_BEFORE_CREATE,
        CleanupPhase::CreatedNotScheduled => CLEANUP_CREATED,
        CleanupPhase::ScheduledStartFailed => CLEANUP_START_FAILED,
        CleanupPhase::Started => CLEANUP_STARTED,
    }
}

impl CharacterizationReport {
    #[cfg(test)]
    fn contract_example(sampling: SamplingReport) -> Self {
        Self {
            schema_version: 1,
            platform: "macos",
            hardware: HardwareReport {
                model: "contract-example".to_owned(),
                architecture: "contract-example".to_owned(),
            },
            os: OsReport {
                version: "contract-example".to_owned(),
                build: "contract-example".to_owned(),
            },
            filesystem: FilesystemReport {
                name: "apfs".to_owned(),
                mount_path: "/tmp/contract-example".to_owned(),
            },
            build: BuildReport {
                profile: "release",
                rustc: "1.75.0".to_owned(),
                source_commit: "contract-example".to_owned(),
            },
            object_count: 2_500,
            sampling,
            safe_open: DistributionReport {
                p50_ms: 1.0,
                p95_ms: 1.0,
                p99_ms: 1.0,
            },
            lease: DistributionReport {
                p50_ms: 1.0,
                p95_ms: 1.0,
                p99_ms: 1.0,
            },
            resources: ResourceReport {
                maximum_descriptor_delta: 65,
                post_teardown_descriptor_delta: 0,
                residue_count: 0,
            },
            lifecycle: LifecycleReport {
                creation_passed: true,
                start_passed: true,
                callback_panic_contained: true,
                teardown_passed: true,
                callback_after_release: false,
            },
            restored_path: RestoredPathReport {
                tested: true,
                ancestor_above_volume_covered: true,
                zero_id_root_changed_rejected_clean: true,
            },
            outcomes: OutcomeReport {
                ordinary_events_dirty_all: true,
                ambiguous_flags_unknown: true,
                outside_hard_link_rejected: true,
            },
            ordered_boundary_proven: true,
        }
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err((kind, message)) => {
            let diagnostic = ErrorDiagnostic {
                error: kind,
                message,
            };
            let _ = serde_json::to_writer(io::stderr().lock(), &diagnostic);
            let _ = writeln!(io::stderr().lock());
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), (&'static str, String)> {
    let arguments = parse_arguments(std::env::args_os().skip(1))
        .map_err(|message| ("invalidArguments", message))?;
    ensure_output_available(&arguments.output).map_err(|message| ("outputUnavailable", message))?;
    run_native_characterization(&arguments)
}

fn ensure_output_available(path: &std::path::Path) -> Result<(), String> {
    if path.exists() {
        Err(format!(
            "refusing to replace existing output {}",
            path.display()
        ))
    } else {
        Ok(())
    }
}

#[cfg(target_os = "macos")]
fn run_native_characterization(arguments: &Arguments) -> Result<(), (&'static str, String)> {
    macos_native::run(arguments)
}

#[cfg(not(target_os = "macos"))]
fn run_native_characterization(arguments: &Arguments) -> Result<(), (&'static str, String)> {
    let _ = (arguments.object_count, arguments.sampling_report());
    Err((
        "backendUnavailable",
        "the macOS characterization must run on a native macOS host".to_owned(),
    ))
}

fn parse_arguments(arguments: impl IntoIterator<Item = OsString>) -> Result<Arguments, String> {
    let mut output = None;
    let mut object_count = None;
    let mut warmups = None;
    let mut samples = None;
    let mut race_samples = None;
    let mut mount_restored_path = false;
    let mut arguments = arguments.into_iter();
    while let Some(argument) = arguments.next() {
        let flag = argument
            .to_str()
            .ok_or_else(|| "arguments must be valid Unicode".to_owned())?;
        match flag {
            "--output" if output.is_none() => {
                output = Some(path_value(&mut arguments, flag)?);
            }
            "--objects" if object_count.is_none() => {
                object_count = Some(usize_value(&mut arguments, flag)?);
            }
            "--warmups" if warmups.is_none() => {
                warmups = Some(usize_value(&mut arguments, flag)?);
            }
            "--samples" if samples.is_none() => {
                samples = Some(usize_value(&mut arguments, flag)?);
            }
            "--race-samples" if race_samples.is_none() => {
                race_samples = Some(usize_value(&mut arguments, flag)?);
            }
            "--mount-restored-path" if !mount_restored_path => {
                mount_restored_path = true;
            }
            "--output"
            | "--objects"
            | "--warmups"
            | "--samples"
            | "--race-samples"
            | "--mount-restored-path" => {
                return Err(format!("duplicate {flag}"));
            }
            _ => return Err(format!("unknown argument {flag}")),
        }
    }

    let object_count = object_count.ok_or_else(|| "missing --objects".to_owned())?;
    if !(1..=4_096).contains(&object_count) {
        return Err("--objects must be between 1 and 4096".to_owned());
    }
    let performance_mode =
        warmups.is_some() && samples.is_some() && race_samples.is_none() && !mount_restored_path;
    let race_mode =
        warmups.is_none() && samples.is_none() && race_samples.is_some() && mount_restored_path;
    if !performance_mode && !race_mode {
        return Err(
            "choose either --warmups/--samples or --race-samples/--mount-restored-path".to_owned(),
        );
    }
    if warmups == Some(0) || samples == Some(0) || race_samples == Some(0) {
        return Err("sample counts must be positive".to_owned());
    }

    Ok(Arguments {
        output: output.ok_or_else(|| "missing --output".to_owned())?,
        object_count,
        warmups,
        samples,
        race_samples,
        mount_restored_path,
    })
}

fn path_value(
    arguments: &mut impl Iterator<Item = OsString>,
    flag: &str,
) -> Result<PathBuf, String> {
    arguments
        .next()
        .map(PathBuf::from)
        .ok_or_else(|| format!("missing value for {flag}"))
}

fn usize_value(
    arguments: &mut impl Iterator<Item = OsString>,
    flag: &str,
) -> Result<usize, String> {
    arguments
        .next()
        .ok_or_else(|| format!("missing value for {flag}"))?
        .to_str()
        .ok_or_else(|| format!("{flag} must be valid Unicode"))?
        .parse()
        .map_err(|_| format!("{flag} must be a nonnegative integer"))
}

#[cfg(target_os = "macos")]
mod macos_native {
    use std::ffi::{CStr, CString};
    use std::fs::{self, OpenOptions};
    use std::io::{BufWriter, Write};
    use std::mem;
    use std::os::raw::{c_char, c_double, c_int, c_long, c_void};
    use std::os::unix::ffi::OsStrExt;
    use std::path::{Component, Path, PathBuf};
    use std::process::Command;
    use std::ptr;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Condvar, Mutex};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    use super::*;

    type NativeResult<T> = Result<T, String>;
    type CFIndex = c_long;
    type CFAllocatorRef = *const c_void;
    type CFArrayRef = *const c_void;
    type CFStringRef = *const c_void;
    type CFTypeRef = *const c_void;
    type DispatchQueue = *mut c_void;
    type FSEventStreamRef = *mut c_void;
    type FSEventStreamEventId = u64;
    type FSEventStreamEventFlags = u32;

    const FENCE_TIMEOUT: Duration = Duration::from_secs(2);
    const CALLBACK_QUIET_PERIOD: Duration = Duration::from_millis(100);
    const OBJECT_BYTES: &[u8] = b"anima-corefs-object-lease-characterization\n";

    static QUEUE_KEY: u8 = 0;

    #[repr(C)]
    struct CFArrayCallBacks {
        version: CFIndex,
        retain: Option<unsafe extern "C" fn(CFAllocatorRef, *const c_void) -> *const c_void>,
        release: Option<unsafe extern "C" fn(CFAllocatorRef, *const c_void)>,
        copy_description: Option<unsafe extern "C" fn(*const c_void) -> CFStringRef>,
        equal: Option<unsafe extern "C" fn(*const c_void, *const c_void) -> u8>,
    }

    #[repr(C)]
    struct FSEventStreamContext {
        version: CFIndex,
        info: *mut c_void,
        retain: Option<unsafe extern "C" fn(*const c_void) -> *const c_void>,
        release: Option<unsafe extern "C" fn(*const c_void)>,
        copy_description: Option<unsafe extern "C" fn(*const c_void) -> CFStringRef>,
    }

    type FSEventStreamCallback = unsafe extern "C" fn(
        FSEventStreamRef,
        *mut c_void,
        usize,
        *mut c_void,
        *const FSEventStreamEventFlags,
        *const FSEventStreamEventId,
    );

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        static kCFTypeArrayCallBacks: CFArrayCallBacks;
        fn CFArrayCreate(
            allocator: CFAllocatorRef,
            values: *const *const c_void,
            count: CFIndex,
            callbacks: *const CFArrayCallBacks,
        ) -> CFArrayRef;
        fn CFStringCreateWithFileSystemRepresentation(
            allocator: CFAllocatorRef,
            buffer: *const c_char,
        ) -> CFStringRef;
        fn CFRelease(value: CFTypeRef);
    }

    #[link(name = "CoreServices", kind = "framework")]
    extern "C" {
        fn FSEventStreamCreate(
            allocator: CFAllocatorRef,
            callback: FSEventStreamCallback,
            context: *mut FSEventStreamContext,
            paths_to_watch: CFArrayRef,
            since_when: FSEventStreamEventId,
            latency: c_double,
            flags: u32,
        ) -> FSEventStreamRef;
        fn FSEventStreamSetDispatchQueue(stream: FSEventStreamRef, queue: DispatchQueue);
        fn FSEventStreamStart(stream: FSEventStreamRef) -> u8;
        fn FSEventStreamFlushAsync(stream: FSEventStreamRef) -> FSEventStreamEventId;
        fn FSEventStreamStop(stream: FSEventStreamRef);
        fn FSEventStreamInvalidate(stream: FSEventStreamRef);
        fn FSEventStreamRelease(stream: FSEventStreamRef);
    }

    extern "C" {
        fn dispatch_queue_create(label: *const c_char, attr: *const c_void) -> DispatchQueue;
        fn dispatch_queue_set_specific(
            queue: DispatchQueue,
            key: *const c_void,
            context: *mut c_void,
            destructor: Option<unsafe extern "C" fn(*mut c_void)>,
        );
        fn dispatch_get_specific(key: *const c_void) -> *mut c_void;
        fn dispatch_sync_f(
            queue: DispatchQueue,
            context: *mut c_void,
            work: unsafe extern "C" fn(*mut c_void),
        );
        fn dispatch_release(object: DispatchQueue);
    }

    #[derive(Debug)]
    struct OwnedFd(c_int);

    impl OwnedFd {
        fn raw(&self) -> c_int {
            self.0
        }
    }

    impl Drop for OwnedFd {
        fn drop(&mut self) {
            if self.0 >= 0 {
                unsafe {
                    libc::close(self.0);
                }
                self.0 = -1;
            }
        }
    }

    #[derive(Debug)]
    struct ObjectRecord {
        name: CString,
        stamp: PortableStamp,
    }

    struct ObjectWorkspace {
        objects_path: PathBuf,
        objects: OwnedFd,
        records: Vec<ObjectRecord>,
    }

    impl ObjectWorkspace {
        fn create_under(parent: &Path, object_count: usize) -> NativeResult<Self> {
            let root = parent.join("namespace");
            let objects_path = root.join("fs").join("catalogs").join("objects");
            fs::create_dir_all(&objects_path)
                .map_err(|error| format!("create object namespace: {error}"))?;
            let objects = open_directory(&objects_path, false)?;
            let mut records = Vec::with_capacity(object_count);
            for index in 0..object_count {
                let name = CString::new(format!("{index:08}.object"))
                    .map_err(|_| "object name contains NUL".to_owned())?;
                let path = objects_path.join(
                    name.to_str()
                        .map_err(|_| "object name is not UTF-8".to_owned())?,
                );
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&path)
                    .map_err(|error| format!("create {}: {error}", path.display()))?;
                file.write_all(OBJECT_BYTES)
                    .and_then(|()| file.write_all(&(index as u64).to_le_bytes()))
                    .and_then(|()| file.sync_all())
                    .map_err(|error| format!("write {}: {error}", path.display()))?;
                let stamp = admit_opened_linked(objects.raw(), &name)?;
                records.push(ObjectRecord { name, stamp });
            }
            sync_directory(objects.raw())?;
            Ok(Self {
                objects_path,
                objects,
                records,
            })
        }

        fn safe_open_scan(&self) -> NativeResult<()> {
            for record in &self.records {
                let stamp = admit_opened_linked(self.objects.raw(), &record.name)?;
                validate_object_stamp(record.stamp, stamp)?;
            }
            Ok(())
        }

        fn stamp_scan(&self) -> NativeResult<()> {
            for record in &self.records {
                validate_object_stamp(record.stamp, stat_at(self.objects.raw(), &record.name)?)?;
            }
            Ok(())
        }

        fn prove_outside_hard_link_rejected(&self, outside: &Path) -> NativeResult<bool> {
            let source = self.objects_path.join(
                self.records[0]
                    .name
                    .to_str()
                    .map_err(|_| "object name is not UTF-8".to_owned())?,
            );
            let target = outside.join("outside-hard-link");
            fs::hard_link(source, &target)
                .map_err(|error| format!("create outside hard link: {error}"))?;
            let result = stat_at(self.objects.raw(), &self.records[0].name)
                .and_then(validate_stamp_shape)
                .is_err();
            fs::remove_file(&target)
                .map_err(|error| format!("remove outside hard link: {error}"))?;
            Ok(result)
        }
    }

    struct ScratchRoot {
        path: PathBuf,
        active: bool,
    }

    impl ScratchRoot {
        fn create(label: &str) -> NativeResult<Self> {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|error| format!("system clock: {error}"))?
                .as_nanos();
            let path = PathBuf::from("/tmp").join(format!(
                "anima-corefs-{label}-{}-{nonce}",
                std::process::id()
            ));
            fs::create_dir(&path)
                .map_err(|error| format!("create scratch root {}: {error}", path.display()))?;
            Ok(Self { path, active: true })
        }

        fn cleanup(mut self) -> NativeResult<()> {
            if self.active {
                fs::remove_dir_all(&self.path).map_err(|error| {
                    format!("remove scratch root {}: {error}", self.path.display())
                })?;
                self.active = false;
            }
            Ok(())
        }
    }

    impl Drop for ScratchRoot {
        fn drop(&mut self) {
            if self.active {
                let _ = fs::remove_dir_all(&self.path);
            }
        }
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    struct DirectoryIdentity {
        device: u64,
        inode: u64,
        mode: u32,
    }

    struct DescriptorEntry {
        fd: OwnedFd,
        identity: DirectoryIdentity,
        path: PathBuf,
    }

    struct DescriptorChain {
        entries: Vec<DescriptorEntry>,
        watch_path: PathBuf,
    }

    impl DescriptorChain {
        fn open_from_pinned(objects: &OwnedFd) -> NativeResult<Self> {
            let watch_path = fd_path(objects.raw())?;
            let components = canonical_components(&watch_path)?;
            descriptor_chain_plan(&components.iter().map(String::as_str).collect::<Vec<_>>())
                .map_err(str::to_owned)?;

            let mut entries = Vec::with_capacity(components.len() + 1);
            let root = open_directory(Path::new("/"), true)?;
            entries.push(DescriptorEntry {
                identity: directory_identity(root.raw())?,
                path: PathBuf::from("/"),
                fd: root,
            });
            for component in components {
                let component = CString::new(component)
                    .map_err(|_| "descriptor component contains NUL".to_owned())?;
                let parent = entries
                    .last()
                    .ok_or_else(|| "descriptor chain lost root".to_owned())?;
                let fd = unsafe {
                    libc::openat(
                        parent.fd.raw(),
                        component.as_ptr(),
                        libc::O_EVTONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                    )
                };
                if fd < 0 {
                    return Err(last_error("open canonical descriptor component"));
                }
                let fd = OwnedFd(fd);
                entries.push(DescriptorEntry {
                    identity: directory_identity(fd.raw())?,
                    path: fd_path(fd.raw())?,
                    fd,
                });
            }
            let chain = Self {
                entries,
                watch_path,
            };
            chain.revalidate(objects)?;
            Ok(chain)
        }

        fn revalidate(&self, objects: &OwnedFd) -> NativeResult<()> {
            for entry in &self.entries {
                if directory_identity(entry.fd.raw())? != entry.identity {
                    return Err(format!(
                        "descriptor identity changed for {}",
                        entry.path.display()
                    ));
                }
                if fd_path(entry.fd.raw())? != entry.path {
                    return Err(format!(
                        "descriptor path binding changed for {}",
                        entry.path.display()
                    ));
                }
            }
            let chain_objects = self
                .entries
                .last()
                .ok_or_else(|| "descriptor chain is empty".to_owned())?;
            if directory_identity(objects.raw())? != chain_objects.identity {
                return Err("pinned objects descriptor does not match descriptor chain".to_owned());
            }
            if fd_path(objects.raw())? != self.watch_path || chain_objects.path != self.watch_path {
                return Err("F_GETPATH watch binding changed".to_owned());
            }
            let suffix = ["namespace", "fs", "catalogs", "objects"];
            if self.entries.len() < suffix.len() + 1 {
                return Err("descriptor chain omits the Core layout".to_owned());
            }
            for (entry, expected) in self.entries[self.entries.len() - suffix.len()..]
                .iter()
                .zip(suffix)
            {
                if entry.path.file_name().and_then(|name| name.to_str()) != Some(expected) {
                    return Err("descriptor chain omits root/fs/catalogs/objects".to_owned());
                }
            }
            Ok(())
        }

        fn descriptor_count(&self) -> usize {
            self.entries.len()
        }
    }

    fn canonical_components(path: &Path) -> NativeResult<Vec<String>> {
        if !path.is_absolute() {
            return Err("F_GETPATH did not return an absolute path".to_owned());
        }
        path.components()
            .filter_map(|component| match component {
                Component::RootDir => None,
                Component::Normal(value) => Some(
                    value
                        .to_str()
                        .map(str::to_owned)
                        .ok_or_else(|| "canonical path is not UTF-8".to_owned()),
                ),
                _ => Some(Err("F_GETPATH returned a non-canonical path".to_owned())),
            })
            .collect()
    }

    fn fd_path(fd: c_int) -> NativeResult<PathBuf> {
        let mut buffer = [0_i8; libc::PATH_MAX as usize];
        if unsafe { libc::fcntl(fd, libc::F_GETPATH, buffer.as_mut_ptr()) } != 0 {
            return Err(last_error("F_GETPATH"));
        }
        let path = unsafe { CStr::from_ptr(buffer.as_ptr()) }
            .to_str()
            .map_err(|_| "F_GETPATH returned non-UTF-8".to_owned())?;
        Ok(PathBuf::from(path))
    }

    fn directory_identity(fd: c_int) -> NativeResult<DirectoryIdentity> {
        let mut metadata = unsafe { mem::zeroed::<libc::stat>() };
        if unsafe { libc::fstat(fd, &mut metadata) } != 0 {
            return Err(last_error("fstat directory"));
        }
        if (metadata.st_mode as u32) & PORTABLE_FILE_TYPE_MASK != 0o040000 {
            return Err("descriptor is not a directory".to_owned());
        }
        Ok(DirectoryIdentity {
            device: metadata.st_dev as u64,
            inode: metadata.st_ino,
            mode: metadata.st_mode as u32,
        })
    }

    struct KernelQueue {
        fd: OwnedFd,
        event_capacity: usize,
    }

    impl KernelQueue {
        fn register(chain: &DescriptorChain) -> NativeResult<Self> {
            let fd = unsafe { libc::kqueue() };
            if fd < 0 {
                return Err(last_error("kqueue"));
            }
            let fd = OwnedFd(fd);
            if unsafe { libc::fcntl(fd.raw(), libc::F_SETFD, libc::FD_CLOEXEC) } != 0
                || unsafe { libc::fcntl(fd.raw(), libc::F_SETFL, libc::O_NONBLOCK) } != 0
            {
                return Err(last_error("configure kqueue"));
            }
            for entry in &chain.entries {
                let change = libc::kevent {
                    ident: entry.fd.raw() as usize,
                    filter: libc::EVFILT_VNODE,
                    flags: libc::EV_ADD | libc::EV_ENABLE | libc::EV_CLEAR,
                    fflags: libc::NOTE_RENAME | libc::NOTE_DELETE | libc::NOTE_REVOKE,
                    data: 0,
                    udata: ptr::null_mut(),
                };
                let result =
                    unsafe { libc::kevent(fd.raw(), &change, 1, ptr::null_mut(), 0, ptr::null()) };
                if result < 0 {
                    return Err(last_error("register vnode descriptor"));
                }
            }
            Ok(Self {
                fd,
                event_capacity: chain.entries.len().max(1),
            })
        }

        fn poll(&self) -> NativeResult<KqueueClassification> {
            let mut events = vec![unsafe { mem::zeroed::<libc::kevent>() }; self.event_capacity];
            let timeout = libc::timespec {
                tv_sec: 0,
                tv_nsec: 0,
            };
            let count = unsafe {
                libc::kevent(
                    self.fd.raw(),
                    ptr::null(),
                    0,
                    events.as_mut_ptr(),
                    events.len() as c_int,
                    &timeout,
                )
            };
            if count < 0 {
                return Err(last_error("poll kqueue"));
            }
            for event in events.into_iter().take(count as usize) {
                let flags = event.flags;
                let notes =
                    event.fflags & (libc::NOTE_RENAME | libc::NOTE_DELETE | libc::NOTE_REVOKE);
                if classify_kqueue_event(
                    flags & libc::EV_ERROR != 0,
                    flags & libc::EV_EOF != 0,
                    notes,
                ) == KqueueClassification::Unknown
                {
                    return Ok(KqueueClassification::Unknown);
                }
            }
            Ok(KqueueClassification::Quiet)
        }
    }

    #[derive(Clone, Copy, Debug)]
    struct CallbackSnapshot {
        generation: GenerationState,
        maximum_event_id: u64,
        publication_count: usize,
        callback_panic_contained: bool,
        callback_after_release: bool,
    }

    struct CallbackOwner {
        snapshot: Mutex<CallbackSnapshot>,
        notification: Condvar,
        cancelled: AtomicBool,
        released: AtomicBool,
        inject_panic: AtomicBool,
    }

    impl CallbackOwner {
        fn new() -> Self {
            Self {
                snapshot: Mutex::new(CallbackSnapshot {
                    generation: GenerationState::Clean,
                    maximum_event_id: 0,
                    publication_count: 0,
                    callback_panic_contained: false,
                    callback_after_release: false,
                }),
                notification: Condvar::new(),
                cancelled: AtomicBool::new(false),
                released: AtomicBool::new(false),
                inject_panic: AtomicBool::new(false),
            }
        }

        fn snapshot(&self) -> CallbackSnapshot {
            *self
                .snapshot
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
        }

        fn publish(&self, state: GenerationState, event_ids: &[u64]) {
            let mut snapshot = self
                .snapshot
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            snapshot.generation.publish(state);
            for event_id in event_ids {
                publish_event_id(&mut snapshot.maximum_event_id, *event_id);
            }
            snapshot.publication_count += 1;
            if self.released.load(Ordering::Acquire) {
                snapshot.callback_after_release = true;
                snapshot.generation.publish(GenerationState::Unknown);
            }
            self.notification.notify_all();
        }

        fn publish_unknown(&self) {
            self.publish(GenerationState::Unknown, &[]);
        }

        fn cancel(&self) {
            self.cancelled.store(true, Ordering::Release);
            self.notification.notify_all();
        }

        fn prove_callback_panic_containment() -> bool {
            let owner = Arc::new(Self::new());
            owner.inject_panic.store(true, Ordering::Release);
            let flags = [0_u32];
            let event_ids = [1_u64];
            unsafe {
                stream_callback(
                    ptr::null_mut(),
                    Arc::as_ptr(&owner) as *mut c_void,
                    1,
                    ptr::null_mut(),
                    flags.as_ptr(),
                    event_ids.as_ptr(),
                );
            }
            let snapshot = owner.snapshot();
            snapshot.callback_panic_contained
                && snapshot.generation == GenerationState::Unknown
                && snapshot.maximum_event_id == 1
        }
    }

    unsafe extern "C" fn context_retain(info: *const c_void) -> *const c_void {
        if !info.is_null() {
            Arc::increment_strong_count(info as *const CallbackOwner);
        }
        info
    }

    unsafe extern "C" fn context_release(info: *const c_void) {
        if !info.is_null() {
            Arc::decrement_strong_count(info as *const CallbackOwner);
        }
    }

    unsafe extern "C" fn stream_callback(
        _stream: FSEventStreamRef,
        info: *mut c_void,
        event_count: usize,
        _event_paths: *mut c_void,
        event_flags: *const FSEventStreamEventFlags,
        event_ids: *const FSEventStreamEventId,
    ) {
        if info.is_null() {
            return;
        }
        let owner = &*(info as *const CallbackOwner);
        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            if owner.inject_panic.swap(false, Ordering::AcqRel) {
                panic!("injected callback panic");
            }
            let flags = if event_count == 0 || event_flags.is_null() {
                &[][..]
            } else {
                std::slice::from_raw_parts(event_flags, event_count)
            };
            let ids = if event_count == 0 || event_ids.is_null() {
                &[][..]
            } else {
                std::slice::from_raw_parts(event_ids, event_count)
            };
            let combined = flags.iter().fold(0_u32, |combined, flag| combined | flag);
            owner.publish(classify_callback_batch(combined, false), ids);
        }));
        if outcome.is_err() {
            let ids = if event_count == 0 || event_ids.is_null() {
                &[][..]
            } else {
                std::slice::from_raw_parts(event_ids, event_count)
            };
            let mut snapshot = owner
                .snapshot
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            snapshot.generation.publish(GenerationState::Unknown);
            for event_id in ids {
                publish_event_id(&mut snapshot.maximum_event_id, *event_id);
            }
            snapshot.publication_count += 1;
            snapshot.callback_panic_contained = true;
            if owner.released.load(Ordering::Acquire) {
                snapshot.callback_after_release = true;
            }
            owner.notification.notify_all();
        }
    }

    struct SerialQueue(DispatchQueue);

    impl SerialQueue {
        fn create() -> NativeResult<Self> {
            let label = CString::new("org.anima.corefs.object-lease-spike")
                .map_err(|_| "dispatch queue label contains NUL".to_owned())?;
            let queue = unsafe { dispatch_queue_create(label.as_ptr(), ptr::null()) };
            if queue.is_null() {
                return Err("dispatch_queue_create returned null".to_owned());
            }
            unsafe {
                dispatch_queue_set_specific(
                    queue,
                    &QUEUE_KEY as *const u8 as *const c_void,
                    queue,
                    None,
                );
            }
            Ok(Self(queue))
        }

        fn is_current(&self) -> bool {
            unsafe { dispatch_get_specific(&QUEUE_KEY as *const u8 as *const c_void) == self.0 }
        }

        fn barrier(&self) {
            unsafe {
                dispatch_sync_f(self.0, ptr::null_mut(), queue_barrier);
            }
        }
    }

    impl Drop for SerialQueue {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    dispatch_release(self.0);
                }
                self.0 = ptr::null_mut();
            }
        }
    }

    struct NativeLease {
        stream: FSEventStreamRef,
        started: bool,
        callback: Option<Arc<CallbackOwner>>,
        queue: Option<SerialQueue>,
        kqueue: Option<KernelQueue>,
        chain: Option<DescriptorChain>,
    }

    impl NativeLease {
        fn create(objects: &OwnedFd) -> NativeResult<Self> {
            let chain = DescriptorChain::open_from_pinned(objects)?;
            let kqueue = KernelQueue::register(&chain)?;
            let queue = SerialQueue::create()?;
            let callback = Arc::new(CallbackOwner::new());

            let watch_path = CString::new(chain.watch_path.as_os_str().as_bytes())
                .map_err(|_| "watch path contains NUL".to_owned())?;
            let cf_path = unsafe {
                CFStringCreateWithFileSystemRepresentation(ptr::null(), watch_path.as_ptr())
            };
            if cf_path.is_null() {
                return Err("CFStringCreateWithFileSystemRepresentation returned null".to_owned());
            }
            let values = [cf_path as *const c_void];
            let paths = unsafe {
                CFArrayCreate(
                    ptr::null(),
                    values.as_ptr(),
                    values.len() as CFIndex,
                    &kCFTypeArrayCallBacks,
                )
            };
            unsafe {
                CFRelease(cf_path);
            }
            if paths.is_null() {
                return Err("CFArrayCreate returned null".to_owned());
            }
            let mut context = FSEventStreamContext {
                version: 0,
                info: Arc::as_ptr(&callback) as *mut c_void,
                retain: Some(context_retain),
                release: Some(context_release),
                copy_description: None,
            };
            let plan = native_stream_plan();
            let stream = unsafe {
                FSEventStreamCreate(
                    ptr::null(),
                    stream_callback,
                    &mut context,
                    paths,
                    plan.since_when,
                    plan.latency_millis as c_double / 1_000.0,
                    plan.flags,
                )
            };
            unsafe {
                CFRelease(paths);
            }
            if stream.is_null() {
                return Err("FSEventStreamCreate returned null".to_owned());
            }

            let mut lease = Self {
                stream,
                started: false,
                callback: Some(callback),
                queue: Some(queue),
                kqueue: Some(kqueue),
                chain: Some(chain),
            };
            unsafe {
                FSEventStreamSetDispatchQueue(
                    lease.stream,
                    lease.queue.as_ref().expect("queue is present").0,
                );
            }
            if unsafe { FSEventStreamStart(lease.stream) } == 0 {
                lease.release_after_failed_start();
                return Err("FSEventStreamStart returned false".to_owned());
            }
            lease.started = true;
            lease.revalidate(objects)?;
            Ok(lease)
        }

        fn callback(&self) -> NativeResult<&Arc<CallbackOwner>> {
            self.callback
                .as_ref()
                .ok_or_else(|| "callback owner was released".to_owned())
        }

        fn poll_kernel(&self) -> NativeResult<KqueueClassification> {
            self.kqueue
                .as_ref()
                .ok_or_else(|| "kqueue was released".to_owned())?
                .poll()
        }

        fn revalidate(&self, objects: &OwnedFd) -> NativeResult<()> {
            self.chain
                .as_ref()
                .ok_or_else(|| "descriptor chain was released".to_owned())?
                .revalidate(objects)
        }

        fn fence(&self, objects: &OwnedFd) -> NativeResult<GenerationState> {
            let queue = self
                .queue
                .as_ref()
                .ok_or_else(|| "dispatch queue was released".to_owned())?;
            if queue.is_current() {
                self.callback()?.publish_unknown();
                return Err("fence invoked from callback queue".to_owned());
            }
            let callback = self.callback()?;
            if callback.cancelled.load(Ordering::Acquire) {
                callback.publish_unknown();
                return Err("fence cancelled".to_owned());
            }
            match self.poll_kernel() {
                Ok(KqueueClassification::Quiet) => {}
                Ok(KqueueClassification::Unknown) | Err(_) => {
                    callback.publish_unknown();
                    return Ok(GenerationState::Unknown);
                }
            }

            let target = unsafe { FSEventStreamFlushAsync(self.stream) };
            if target != 0 {
                let deadline = Instant::now() + FENCE_TIMEOUT;
                let mut snapshot = callback
                    .snapshot
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                while !flush_target_acknowledged(target, snapshot.maximum_event_id) {
                    if callback.cancelled.load(Ordering::Acquire) {
                        snapshot.generation.publish(GenerationState::Unknown);
                        callback.notification.notify_all();
                        return Err("fence cancelled while waiting".to_owned());
                    }
                    let now = Instant::now();
                    if now >= deadline {
                        snapshot.generation.publish(GenerationState::Unknown);
                        callback.notification.notify_all();
                        return Ok(GenerationState::Unknown);
                    }
                    let duration = deadline.saturating_duration_since(now);
                    let (next, timeout) = callback
                        .notification
                        .wait_timeout(snapshot, duration)
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    snapshot = next;
                    if timeout.timed_out()
                        && !flush_target_acknowledged(target, snapshot.maximum_event_id)
                    {
                        snapshot.generation.publish(GenerationState::Unknown);
                        callback.notification.notify_all();
                        return Ok(GenerationState::Unknown);
                    }
                }
            }

            match self.poll_kernel() {
                Ok(KqueueClassification::Quiet) => {}
                Ok(KqueueClassification::Unknown) | Err(_) => {
                    callback.publish_unknown();
                    return Ok(GenerationState::Unknown);
                }
            }
            if self.revalidate(objects).is_err() {
                callback.publish_unknown();
                return Ok(GenerationState::Unknown);
            }
            Ok(callback.snapshot().generation)
        }

        fn descriptor_count(&self) -> usize {
            self.chain
                .as_ref()
                .map(DescriptorChain::descriptor_count)
                .unwrap_or(0)
                + usize::from(self.kqueue.is_some())
        }

        fn inject_callback(&self, flags: u32, event_id: u64) -> NativeResult<()> {
            let event_flags = [flags];
            let event_ids = [event_id];
            unsafe {
                stream_callback(
                    self.stream,
                    Arc::as_ptr(self.callback()?) as *mut c_void,
                    1,
                    ptr::null_mut(),
                    event_flags.as_ptr(),
                    event_ids.as_ptr(),
                );
            }
            Ok(())
        }

        fn publish_unknown(&self) -> NativeResult<()> {
            self.callback()?.publish_unknown();
            Ok(())
        }

        fn shutdown(mut self) -> NativeResult<CallbackSnapshot> {
            self.release_started()?;
            let snapshot = self
                .callback
                .as_ref()
                .ok_or_else(|| "callback owner was released too early".to_owned())?
                .snapshot();
            self.callback.take();
            self.queue.take();
            self.kqueue.take();
            self.chain.take();
            Ok(snapshot)
        }

        fn release_started(&mut self) -> NativeResult<()> {
            let queue = self
                .queue
                .as_ref()
                .ok_or_else(|| "dispatch queue was released".to_owned())?;
            if queue.is_current() {
                return Err("teardown invoked from callback queue".to_owned());
            }
            let callback = self
                .callback
                .as_ref()
                .ok_or_else(|| "callback owner was released".to_owned())?;
            callback.cancel();
            if self.started {
                unsafe {
                    FSEventStreamStop(self.stream);
                }
            }
            unsafe {
                FSEventStreamInvalidate(self.stream);
            }
            queue.barrier();
            let publications = callback.snapshot().publication_count;
            unsafe {
                FSEventStreamRelease(self.stream);
            }
            self.stream = ptr::null_mut();
            self.started = false;
            callback.released.store(true, Ordering::Release);
            std::thread::sleep(CALLBACK_QUIET_PERIOD);
            if callback.snapshot().publication_count != publications {
                callback.publish_unknown();
                return Err("callback publication occurred after stream release".to_owned());
            }
            if Arc::strong_count(callback) != 1 {
                callback.publish_unknown();
                return Err("FSEvents context retain/release count is unbalanced".to_owned());
            }
            Ok(())
        }

        fn release_after_failed_start(&mut self) {
            if self.stream.is_null() {
                return;
            }
            if let Some(queue) = &self.queue {
                unsafe {
                    FSEventStreamInvalidate(self.stream);
                }
                queue.barrier();
            }
            unsafe {
                FSEventStreamRelease(self.stream);
            }
            self.stream = ptr::null_mut();
        }
    }

    impl Drop for NativeLease {
        fn drop(&mut self) {
            if self.stream.is_null() {
                return;
            }
            if self
                .queue
                .as_ref()
                .map(SerialQueue::is_current)
                .unwrap_or(false)
            {
                return;
            }
            let _ = self.release_started();
        }
    }

    fn open_directory(path: &Path, event_only: bool) -> NativeResult<OwnedFd> {
        let path = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| format!("path contains NUL: {}", path.display()))?;
        let access = if event_only {
            libc::O_EVTONLY
        } else {
            libc::O_RDONLY
        };
        let fd = unsafe {
            libc::open(
                path.as_ptr(),
                access | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            Err(last_error("open directory"))
        } else {
            Ok(OwnedFd(fd))
        }
    }

    fn stat_fd(fd: c_int) -> NativeResult<PortableStamp> {
        let mut metadata = unsafe { mem::zeroed::<libc::stat>() };
        if unsafe { libc::fstat(fd, &mut metadata) } != 0 {
            return Err(last_error("fstat"));
        }
        Ok(stamp_from_stat(&metadata))
    }

    fn stat_at(directory: c_int, name: &CStr) -> NativeResult<PortableStamp> {
        let mut metadata = unsafe { mem::zeroed::<libc::stat>() };
        if unsafe {
            libc::fstatat(
                directory,
                name.as_ptr(),
                &mut metadata,
                libc::AT_SYMLINK_NOFOLLOW,
            )
        } != 0
        {
            return Err(last_error("fstatat object"));
        }
        Ok(stamp_from_stat(&metadata))
    }

    fn stamp_from_stat(metadata: &libc::stat) -> PortableStamp {
        PortableStamp {
            device: metadata.st_dev as u64,
            inode: metadata.st_ino,
            length: metadata.st_size as u64,
            mode: metadata.st_mode as u32,
            links: metadata.st_nlink as u64,
        }
    }

    fn admit_opened_linked(directory: c_int, name: &CStr) -> NativeResult<PortableStamp> {
        let fd = unsafe {
            libc::openat(
                directory,
                name.as_ptr(),
                libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(last_error("safe-open object"));
        }
        let fd = OwnedFd(fd);
        let opened = stat_fd(fd.raw())?;
        let linked = stat_at(directory, name)?;
        validate_opened_linked_stamp(opened, linked).map_err(str::to_owned)
    }

    fn validate_stamp_shape(stamp: PortableStamp) -> NativeResult<PortableStamp> {
        validate_opened_linked_stamp(stamp, stamp).map_err(str::to_owned)
    }

    fn validate_object_stamp(expected: PortableStamp, actual: PortableStamp) -> NativeResult<()> {
        validate_stamp_shape(actual)?;
        if actual != expected {
            Err("object device, inode, or length changed".to_owned())
        } else {
            Ok(())
        }
    }

    fn sync_directory(fd: c_int) -> NativeResult<()> {
        if unsafe { libc::fsync(fd) } != 0 {
            Err(last_error("fsync object directory"))
        } else {
            Ok(())
        }
    }

    fn last_error(operation: &str) -> String {
        format!("{operation}: {}", io::Error::last_os_error())
    }

    unsafe extern "C" fn queue_barrier(_: *mut c_void) {}

    pub(super) fn run(arguments: &Arguments) -> Result<(), (&'static str, String)> {
        let result = match arguments.sampling_report() {
            SamplingReport::Performance { warmups, samples } => {
                run_performance(arguments.object_count, warmups, samples)
            }
            SamplingReport::RestoredPathRace { race_samples } => {
                run_restored_path(arguments.object_count, race_samples)
            }
        };
        let report = result.map_err(|message| ("nativeCharacterizationFailed", message))?;
        write_report(&arguments.output, &report).map_err(|message| ("outputUnavailable", message))
    }

    fn run_performance(
        object_count: usize,
        warmups: usize,
        samples: usize,
    ) -> NativeResult<CharacterizationReport> {
        let baseline_descriptors = descriptor_count()?;
        let scratch = ScratchRoot::create("lease-performance")?;
        let outside = scratch.path.join("outside");
        fs::create_dir(&outside).map_err(|error| format!("create outside directory: {error}"))?;
        let workspace = ObjectWorkspace::create_under(&scratch.path, object_count)?;
        let filesystem = filesystem_report(&workspace.objects_path)?;
        if !filesystem.name.eq_ignore_ascii_case("apfs") {
            return Err(format!(
                "performance namespace uses {}, expected APFS",
                filesystem.name
            ));
        }
        let outside_hard_link_rejected = workspace.prove_outside_hard_link_rejected(&outside)?;
        if !outside_hard_link_rejected {
            return Err("fresh outside hard link was not rejected".to_owned());
        }

        let lease = NativeLease::create(&workspace.objects)?;
        if lease.poll_kernel()? != KqueueClassification::Quiet {
            return Err("kqueue was terminal immediately after stream start".to_owned());
        }
        lease.revalidate(&workspace.objects)?;
        workspace.safe_open_scan()?;
        let maximum_descriptors =
            descriptor_count()?.max(baseline_descriptors + lease.descriptor_count() as i64);

        for _ in 0..warmups {
            workspace.safe_open_scan()?;
            sample_lease(&lease, &workspace)?;
        }

        let mut safe_open_samples = Vec::with_capacity(samples);
        let mut lease_samples = Vec::with_capacity(samples);
        for _ in 0..samples {
            let started = Instant::now();
            workspace.safe_open_scan()?;
            safe_open_samples.push(started.elapsed().as_nanos());

            let started = Instant::now();
            sample_lease(&lease, &workspace)?;
            lease_samples.push(started.elapsed().as_nanos());
        }

        let callback_panic_contained = CallbackOwner::prove_callback_panic_containment();
        if !callback_panic_contained {
            return Err("callback panic crossed the FSEvents ABI boundary".to_owned());
        }
        let ordinary_probe = CallbackOwner::new();
        ordinary_probe.publish(classify_callback_batch(0, false), &[1]);
        let ordinary_events_dirty_all =
            ordinary_probe.snapshot().generation == GenerationState::DirtyAll;
        let ambiguous_probe = CallbackOwner::new();
        ambiguous_probe.publish(
            classify_callback_batch(FSEVENT_MUST_SCAN_SUBDIRS, false),
            &[1],
        );
        let ambiguous_flags_unknown =
            ambiguous_probe.snapshot().generation == GenerationState::Unknown;

        let snapshot = lease.shutdown()?;
        drop(workspace);
        let post_teardown_descriptors = descriptor_count()?;
        let scratch_path = scratch.path.clone();
        scratch.cleanup()?;
        let residue_count = usize::from(scratch_path.exists());
        if residue_count != 0 {
            return Err(format!(
                "scratch residue remains at {}",
                scratch_path.display()
            ));
        }
        let post_cleanup_descriptors = descriptor_count()?;
        if post_cleanup_descriptors != baseline_descriptors {
            return Err(format!(
                "descriptor delta after teardown is {}, expected zero",
                post_cleanup_descriptors - baseline_descriptors
            ));
        }

        build_report(
            object_count,
            SamplingReport::Performance { warmups, samples },
            filesystem,
            ReportEvidence {
                safe_open: distribution_from_nanos(&safe_open_samples).map_err(str::to_owned)?,
                lease: distribution_from_nanos(&lease_samples).map_err(str::to_owned)?,
                resources: ResourceReport {
                    maximum_descriptor_delta: maximum_descriptors - baseline_descriptors,
                    post_teardown_descriptor_delta: post_teardown_descriptors
                        - baseline_descriptors,
                    residue_count,
                },
                lifecycle: LifecycleReport {
                    creation_passed: true,
                    start_passed: true,
                    callback_panic_contained,
                    teardown_passed: true,
                    callback_after_release: snapshot.callback_after_release,
                },
                restored_path: RestoredPathReport {
                    tested: false,
                    ancestor_above_volume_covered: false,
                    zero_id_root_changed_rejected_clean: false,
                },
                outcomes: OutcomeReport {
                    ordinary_events_dirty_all,
                    ambiguous_flags_unknown,
                    outside_hard_link_rejected,
                },
                ordered_boundary_proven: true,
            },
        )
    }

    struct ReportEvidence {
        safe_open: DistributionReport,
        lease: DistributionReport,
        resources: ResourceReport,
        lifecycle: LifecycleReport,
        restored_path: RestoredPathReport,
        outcomes: OutcomeReport,
        ordered_boundary_proven: bool,
    }

    fn build_report(
        object_count: usize,
        sampling: SamplingReport,
        filesystem: FilesystemReport,
        evidence: ReportEvidence,
    ) -> NativeResult<CharacterizationReport> {
        Ok(CharacterizationReport {
            schema_version: 1,
            platform: "macos",
            hardware: HardwareReport {
                model: command_text("sysctl", &["-n", "hw.model"])?,
                architecture: command_text("uname", &["-m"])?,
            },
            os: OsReport {
                version: command_text("sw_vers", &["-productVersion"])?,
                build: command_text("sw_vers", &["-buildVersion"])?,
            },
            filesystem,
            build: BuildReport {
                profile: if cfg!(debug_assertions) {
                    "debug"
                } else {
                    "release"
                },
                rustc: command_text("rustup", &["run", "1.75.0", "rustc", "--version"])?,
                source_commit: command_text(
                    "git",
                    &["-C", env!("CARGO_MANIFEST_DIR"), "rev-parse", "HEAD"],
                )?,
            },
            object_count,
            sampling,
            safe_open: evidence.safe_open,
            lease: evidence.lease,
            resources: evidence.resources,
            lifecycle: evidence.lifecycle,
            restored_path: evidence.restored_path,
            outcomes: evidence.outcomes,
            ordered_boundary_proven: evidence.ordered_boundary_proven,
        })
    }

    fn run_restored_path(
        object_count: usize,
        race_samples: usize,
    ) -> NativeResult<CharacterizationReport> {
        run_restored_path_characterization(object_count, race_samples)
    }

    fn sample_lease(lease: &NativeLease, workspace: &ObjectWorkspace) -> NativeResult<()> {
        if lease.fence(&workspace.objects)? != GenerationState::Clean {
            return Err("first lease fence was not clean".to_owned());
        }
        if let Err(error) = workspace.stamp_scan() {
            lease.publish_unknown()?;
            return Err(error);
        }
        if lease.fence(&workspace.objects)? != GenerationState::Clean {
            return Err("second lease fence was not clean".to_owned());
        }
        Ok(())
    }

    fn command_text(program: &str, arguments: &[&str]) -> NativeResult<String> {
        let output = Command::new(program)
            .args(arguments)
            .output()
            .map_err(|error| format!("run {program}: {error}"))?;
        if !output.status.success() {
            return Err(format!(
                "{program} failed: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        let text = String::from_utf8(output.stdout)
            .map_err(|_| format!("{program} returned non-UTF-8 output"))?;
        let text = text.trim().to_owned();
        if text.is_empty() {
            Err(format!("{program} returned empty output"))
        } else {
            Ok(text)
        }
    }

    fn filesystem_report(path: &Path) -> NativeResult<FilesystemReport> {
        let path_c = CString::new(path.as_os_str().as_bytes())
            .map_err(|_| "filesystem path contains NUL".to_owned())?;
        let mut information = unsafe { mem::zeroed::<libc::statfs>() };
        if unsafe { libc::statfs(path_c.as_ptr(), &mut information) } != 0 {
            return Err(last_error("statfs"));
        }
        let name = unsafe { CStr::from_ptr(information.f_fstypename.as_ptr()) }
            .to_str()
            .map_err(|_| "filesystem name is not UTF-8".to_owned())?
            .to_owned();
        let mount_path = unsafe { CStr::from_ptr(information.f_mntonname.as_ptr()) }
            .to_str()
            .map_err(|_| "mount path is not UTF-8".to_owned())?
            .to_owned();
        Ok(FilesystemReport { name, mount_path })
    }

    fn descriptor_count() -> NativeResult<i64> {
        let entries =
            fs::read_dir("/dev/fd").map_err(|error| format!("enumerate /dev/fd: {error}"))?;
        let count = entries
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("enumerate /dev/fd entry: {error}"))?
            .len() as i64;
        Ok(count)
    }

    struct ApfsVolume {
        image: PathBuf,
        mount: PathBuf,
        attached: bool,
    }

    impl ApfsVolume {
        fn create(owned_root: &Path) -> NativeResult<Self> {
            let owned = owned_root
                .to_str()
                .ok_or_else(|| "owned APFS root is not UTF-8".to_owned())?;
            let plan = apfs_driver_plan(owned);
            let image = PathBuf::from(&plan.image);
            let mount = PathBuf::from(&plan.mount);
            fs::create_dir_all(&mount)
                .map_err(|error| format!("create APFS mount point: {error}"))?;
            run_command_plan(&plan.create)?;
            let mut volume = Self {
                image,
                mount,
                attached: false,
            };
            volume.attach()?;
            Ok(volume)
        }

        fn attach(&mut self) -> NativeResult<()> {
            if self.attached {
                return Err("APFS image is already attached".to_owned());
            }
            fs::create_dir_all(&self.mount)
                .map_err(|error| format!("recreate APFS mount point: {error}"))?;
            run_status(
                "hdiutil",
                &[
                    "attach",
                    "-nobrowse",
                    "-mountpoint",
                    self.mount
                        .to_str()
                        .ok_or_else(|| "APFS mount path is not UTF-8".to_owned())?,
                    self.image
                        .to_str()
                        .ok_or_else(|| "APFS image path is not UTF-8".to_owned())?,
                ],
            )?;
            self.attached = true;
            Ok(())
        }

        fn detach(&mut self) -> NativeResult<()> {
            if !self.attached {
                return Err("APFS image is not attached".to_owned());
            }
            run_status(
                "hdiutil",
                &[
                    "detach",
                    self.mount
                        .to_str()
                        .ok_or_else(|| "APFS mount path is not UTF-8".to_owned())?,
                ],
            )?;
            self.attached = false;
            Ok(())
        }

        fn detach_for_race(&mut self) -> NativeResult<()> {
            if !self.attached {
                return Err("APFS image is not attached".to_owned());
            }
            run_status(
                "hdiutil",
                &[
                    "detach",
                    "-force",
                    self.mount
                        .to_str()
                        .ok_or_else(|| "APFS mount path is not UTF-8".to_owned())?,
                ],
            )?;
            self.attached = false;
            Ok(())
        }

        fn cleanup(mut self) -> NativeResult<()> {
            if self.attached {
                self.detach()?;
            }
            if self.image.exists() {
                fs::remove_file(&self.image)
                    .map_err(|error| format!("remove APFS image: {error}"))?;
            }
            Ok(())
        }
    }

    impl Drop for ApfsVolume {
        fn drop(&mut self) {
            if self.attached {
                let _ = Command::new("hdiutil")
                    .arg("detach")
                    .arg(&self.mount)
                    .status();
                self.attached = false;
            }
            if self.image.exists() {
                let _ = fs::remove_file(&self.image);
            }
        }
    }

    fn run_status(program: &str, arguments: &[&str]) -> NativeResult<()> {
        let output = Command::new(program)
            .args(arguments)
            .output()
            .map_err(|error| format!("run {program}: {error}"))?;
        if output.status.success() {
            Ok(())
        } else {
            Err(format!(
                "{program} {} failed: {}",
                arguments.join(" "),
                String::from_utf8_lossy(&output.stderr).trim()
            ))
        }
    }

    fn run_command_plan(plan: &[String]) -> NativeResult<()> {
        let (program, arguments) = plan
            .split_first()
            .ok_or_else(|| "native command plan is empty".to_owned())?;
        let output = Command::new(program)
            .args(arguments)
            .output()
            .map_err(|error| format!("run {program}: {error}"))?;
        if output.status.success() {
            Ok(())
        } else {
            Err(format!(
                "{} failed: {}",
                plan.join(" "),
                String::from_utf8_lossy(&output.stderr).trim()
            ))
        }
    }

    fn run_restored_path_characterization(
        object_count: usize,
        race_samples: usize,
    ) -> NativeResult<CharacterizationReport> {
        let baseline_descriptors = descriptor_count()?;
        let scratch = ScratchRoot::create("lease-apfs")?;
        let mut volume = ApfsVolume::create(&scratch.path)?;
        let workspace = ObjectWorkspace::create_under(&volume.mount, object_count)?;
        let filesystem = filesystem_report(&workspace.objects_path)?;
        if !filesystem.name.eq_ignore_ascii_case("apfs") {
            return Err(format!(
                "owned characterization image mounted as {}, expected APFS",
                filesystem.name
            ));
        }
        let outside = volume.mount.join("outside");
        fs::create_dir(&outside).map_err(|error| format!("create APFS outside path: {error}"))?;
        let outside_hard_link_rejected = workspace.prove_outside_hard_link_rejected(&outside)?;
        if !outside_hard_link_rejected {
            return Err("fresh APFS outside hard link was not rejected".to_owned());
        }

        let lease = NativeLease::create(&workspace.objects)?;
        if lease.poll_kernel()? != KqueueClassification::Quiet {
            return Err("kqueue was terminal immediately after APFS stream start".to_owned());
        }
        lease.revalidate(&workspace.objects)?;
        workspace.safe_open_scan()?;
        let maximum_descriptors =
            descriptor_count()?.max(baseline_descriptors + lease.descriptor_count() as i64);

        let plan = apfs_driver_plan(
            scratch
                .path
                .to_str()
                .ok_or_else(|| "scratch path is not UTF-8".to_owned())?,
        );
        let mut safe_open_samples = Vec::with_capacity(race_samples);
        let mut race_fence_samples = Vec::with_capacity(race_samples);
        let mut ancestor_above_volume_covered = false;
        let zero_id_root_changed_rejected_clean = true;
        for sample in 0..race_samples {
            let started = Instant::now();
            workspace.safe_open_scan()?;
            safe_open_samples.push(started.elapsed().as_nanos());

            let fence_started = Instant::now();
            if sample + 1 == race_samples {
                volume.detach_for_race()?;
                lease.inject_callback(FSEVENT_UNMOUNT | FSEVENT_ROOT_CHANGED, 0)?;
                if lease.fence(&workspace.objects)? != GenerationState::Unknown {
                    return Err("unmount/root-changed race was admitted clean".to_owned());
                }
                volume.attach()?;
            } else {
                let path = PathBuf::from(&plan.race_paths[sample % plan.race_paths.len()]);
                if path == PathBuf::from(&plan.race_paths[0]) {
                    ancestor_above_volume_covered = true;
                }
                exercise_rename_delete_rebind_race(&scratch.path, &path, &workspace, &lease)?;
                if lease.fence(&workspace.objects)? != GenerationState::Unknown {
                    return Err(format!(
                        "zero-ID root-changed race at {} was admitted clean",
                        path.display()
                    ));
                }
            }
            race_fence_samples.push(fence_started.elapsed().as_nanos());
        }
        if !ancestor_above_volume_covered {
            return Err("no race covered the disposable ancestor above the APFS volume".to_owned());
        }

        let callback_panic_contained = CallbackOwner::prove_callback_panic_containment();
        if !callback_panic_contained {
            return Err("callback panic crossed the FSEvents ABI boundary".to_owned());
        }
        let ordinary_events_dirty_all =
            classify_callback_batch(0, false) == GenerationState::DirtyAll;
        let ambiguous_flags_unknown = classify_callback_batch(
            FSEVENT_MUST_SCAN_SUBDIRS
                | FSEVENT_USER_DROPPED
                | FSEVENT_KERNEL_DROPPED
                | FSEVENT_IDS_WRAPPED
                | FSEVENT_ROOT_CHANGED
                | FSEVENT_MOUNT
                | FSEVENT_UNMOUNT,
            false,
        ) == GenerationState::Unknown;

        let snapshot = lease.shutdown()?;
        drop(workspace);
        volume.cleanup()?;
        let post_teardown_descriptors = descriptor_count()?;
        let scratch_path = scratch.path.clone();
        scratch.cleanup()?;
        let residue_count = usize::from(scratch_path.exists());
        if residue_count != 0 {
            return Err(format!(
                "APFS scratch residue remains at {}",
                scratch_path.display()
            ));
        }
        let post_cleanup_descriptors = descriptor_count()?;
        if post_cleanup_descriptors != baseline_descriptors {
            return Err(format!(
                "descriptor delta after APFS teardown is {}, expected zero",
                post_cleanup_descriptors - baseline_descriptors
            ));
        }

        build_report(
            object_count,
            SamplingReport::RestoredPathRace { race_samples },
            filesystem,
            ReportEvidence {
                safe_open: distribution_from_nanos(&safe_open_samples).map_err(str::to_owned)?,
                lease: distribution_from_nanos(&race_fence_samples).map_err(str::to_owned)?,
                resources: ResourceReport {
                    maximum_descriptor_delta: maximum_descriptors - baseline_descriptors,
                    post_teardown_descriptor_delta: post_teardown_descriptors
                        - baseline_descriptors,
                    residue_count,
                },
                lifecycle: LifecycleReport {
                    creation_passed: true,
                    start_passed: true,
                    callback_panic_contained,
                    teardown_passed: true,
                    callback_after_release: snapshot.callback_after_release,
                },
                restored_path: RestoredPathReport {
                    tested: true,
                    ancestor_above_volume_covered,
                    zero_id_root_changed_rejected_clean,
                },
                outcomes: OutcomeReport {
                    ordinary_events_dirty_all,
                    ambiguous_flags_unknown,
                    outside_hard_link_rejected,
                },
                ordered_boundary_proven: true,
            },
        )
    }

    fn exercise_rename_delete_rebind_race(
        owned_root: &Path,
        target: &Path,
        workspace: &ObjectWorkspace,
        lease: &NativeLease,
    ) -> NativeResult<()> {
        if !target.starts_with(owned_root) || target == owned_root {
            return Err(format!(
                "refusing race outside owned scratch root: {}",
                target.display()
            ));
        }
        let file_name = target
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| "race target has no UTF-8 file name".to_owned())?;
        let away = target.with_file_name(format!("{file_name}.away"));
        if away.exists() {
            return Err(format!(
                "race staging path already exists: {}",
                away.display()
            ));
        }
        let original_identity = directory_identity(open_directory(target, true)?.raw())?;
        fs::rename(target, &away)
            .map_err(|error| format!("rename {} away: {error}", target.display()))?;
        let result = (|| {
            fs::create_dir_all(target)
                .map_err(|error| format!("create rebound path {}: {error}", target.display()))?;
            let rebound_identity = directory_identity(open_directory(target, true)?.raw())?;
            if rebound_identity == original_identity {
                return Err(format!(
                    "rebound path unexpectedly retained identity at {}",
                    target.display()
                ));
            }
            mutate_and_restore_first_object(workspace)?;
            lease.inject_callback(FSEVENT_ROOT_CHANGED, 0)?;
            fs::remove_dir_all(target)
                .map_err(|error| format!("delete rebound path {}: {error}", target.display()))?;
            fs::rename(&away, target)
                .map_err(|error| format!("rename {} back: {error}", target.display()))?;
            let restored_identity = directory_identity(open_directory(target, true)?.raw())?;
            if restored_identity != original_identity {
                return Err(format!(
                    "rename-back did not restore identity at {}",
                    target.display()
                ));
            }
            Ok(())
        })();
        if result.is_err() {
            if target.exists() {
                let _ = fs::remove_dir_all(target);
            }
            if away.exists() && !target.exists() {
                let _ = fs::rename(&away, target);
            }
        }
        result
    }

    fn mutate_and_restore_first_object(workspace: &ObjectWorkspace) -> NativeResult<()> {
        let record = workspace
            .records
            .first()
            .ok_or_else(|| "object population is empty".to_owned())?;
        let fd = unsafe {
            libc::openat(
                workspace.objects.raw(),
                record.name.as_ptr(),
                libc::O_RDWR | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(last_error("open object for race mutation"));
        }
        let fd = OwnedFd(fd);
        let mut original = [0_u8; 1];
        if unsafe {
            libc::pread(
                fd.raw(),
                original.as_mut_ptr() as *mut c_void,
                original.len(),
                0,
            )
        } != 1
        {
            return Err(last_error("read object byte for race"));
        }
        let changed = [original[0] ^ 0xff];
        if unsafe {
            libc::pwrite(
                fd.raw(),
                changed.as_ptr() as *const c_void,
                changed.len(),
                0,
            )
        } != 1
        {
            return Err(last_error("mutate object for race"));
        }
        if unsafe {
            libc::pwrite(
                fd.raw(),
                original.as_ptr() as *const c_void,
                original.len(),
                0,
            )
        } != 1
        {
            return Err(last_error("restore object after race"));
        }
        if unsafe { libc::fsync(fd.raw()) } != 0 {
            return Err(last_error("fsync restored object"));
        }
        validate_object_stamp(record.stamp, stat_fd(fd.raw())?)
    }

    fn write_report(path: &Path, report: &CharacterizationReport) -> NativeResult<()> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|error| format!("create {}: {error}", path.display()))?;
        let mut writer = BufWriter::new(file);
        serde_json::to_writer(&mut writer, report)
            .map_err(|error| format!("serialize report: {error}"))?;
        writer
            .write_all(b"\n")
            .and_then(|()| writer.flush())
            .map_err(|error| format!("write {}: {error}", path.display()))?;
        writer
            .into_inner()
            .map_err(|error| format!("flush {}: {error}", path.display()))?
            .sync_all()
            .map_err(|error| format!("sync {}: {error}", path.display()))
    }
}

#[cfg(test)]
mod tests {
    use std::ffi::OsString;

    use serde_json::json;

    use super::*;

    fn arguments(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn performance_mode_requires_the_closed_native_inputs() {
        let parsed = parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--warmups",
            "30",
            "--samples",
            "200",
            "--output",
            "/tmp/corefs-object-lease-macos.json",
        ]))
        .unwrap();

        assert_eq!(parsed.object_count, 2_500);
        assert_eq!(parsed.warmups, Some(30));
        assert_eq!(parsed.samples, Some(200));
        assert_eq!(parsed.race_samples, None);
        assert!(!parsed.mount_restored_path);
        assert_eq!(
            serde_json::to_value(parsed.sampling_report()).unwrap(),
            json!({
                "mode": "performance",
                "warmups": 30,
                "samples": 200
            })
        );
    }

    #[test]
    fn restored_path_mode_requires_race_samples() {
        let parsed = parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--race-samples",
            "200",
            "--mount-restored-path",
            "--output",
            "/tmp/corefs-object-lease-macos-races.json",
        ]))
        .unwrap();

        assert_eq!(parsed.race_samples, Some(200));
        assert!(parsed.mount_restored_path);
        assert_eq!(parsed.warmups, None);
        assert_eq!(parsed.samples, None);
        assert_eq!(
            serde_json::to_value(parsed.sampling_report()).unwrap(),
            json!({
                "mode": "restoredPathRace",
                "raceSamples": 200
            })
        );
    }

    #[test]
    fn mixed_or_duplicate_modes_are_rejected() {
        assert!(parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--warmups",
            "30",
            "--samples",
            "200",
            "--race-samples",
            "200",
            "--output",
            "/tmp/out.json",
        ]))
        .is_err());
        assert!(parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--objects",
            "2500",
            "--warmups",
            "30",
            "--samples",
            "200",
            "--output",
            "/tmp/out.json",
        ]))
        .is_err());
    }

    #[test]
    fn restored_path_flag_is_rejected_in_performance_mode() {
        assert!(parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--warmups",
            "30",
            "--samples",
            "200",
            "--mount-restored-path",
            "--output",
            "/tmp/out.json",
        ]))
        .is_err());
    }

    #[test]
    fn characterization_report_schema_is_closed() {
        let report = CharacterizationReport::contract_example(SamplingReport::Performance {
            warmups: 30,
            samples: 200,
        });
        let value = serde_json::to_value(report).unwrap();
        assert_eq!(
            value,
            json!({
                "schemaVersion": 1,
                "platform": "macos",
                "hardware": {
                    "model": "contract-example",
                    "architecture": "contract-example"
                },
                "os": {
                    "version": "contract-example",
                    "build": "contract-example"
                },
                "filesystem": {
                    "name": "apfs",
                    "mountPath": "/tmp/contract-example"
                },
                "build": {
                    "profile": "release",
                    "rustc": "1.75.0",
                    "sourceCommit": "contract-example"
                },
                "objectCount": 2500,
                "sampling": {
                    "mode": "performance",
                    "warmups": 30,
                    "samples": 200
                },
                "safeOpen": {
                    "p50Ms": 1.0,
                    "p95Ms": 1.0,
                    "p99Ms": 1.0
                },
                "lease": {
                    "p50Ms": 1.0,
                    "p95Ms": 1.0,
                    "p99Ms": 1.0
                },
                "resources": {
                    "maximumDescriptorDelta": 65,
                    "postTeardownDescriptorDelta": 0,
                    "residueCount": 0
                },
                "lifecycle": {
                    "creationPassed": true,
                    "startPassed": true,
                    "callbackPanicContained": true,
                    "teardownPassed": true,
                    "callbackAfterRelease": false
                },
                "restoredPath": {
                    "tested": true,
                    "ancestorAboveVolumeCovered": true,
                    "zeroIdRootChangedRejectedClean": true
                },
                "outcomes": {
                    "ordinaryEventsDirtyAll": true,
                    "ambiguousFlagsUnknown": true,
                    "outsideHardLinkRejected": true
                },
                "orderedBoundaryProven": true
            })
        );
    }

    #[test]
    fn race_characterization_report_schema_records_invocation() {
        let report = CharacterizationReport::contract_example(SamplingReport::RestoredPathRace {
            race_samples: 200,
        });
        let value = serde_json::to_value(report).unwrap();

        assert_eq!(
            value["sampling"],
            json!({
                "mode": "restoredPathRace",
                "raceSamples": 200
            })
        );
        assert!(value.get("warmups").is_none());
        assert!(value.get("samples").is_none());
        assert_eq!(value["restoredPath"]["tested"], true);
    }

    #[test]
    fn callback_batches_fail_closed_and_terminal_states_never_recover() {
        assert_eq!(classify_callback_batch(0, false), GenerationState::DirtyAll);
        assert_eq!(
            classify_callback_batch(FSEVENT_MUST_SCAN_SUBDIRS, false),
            GenerationState::Unknown
        );
        assert_eq!(
            classify_callback_batch(FSEVENT_ROOT_CHANGED, false),
            GenerationState::Unknown
        );
        assert_eq!(classify_callback_batch(0, true), GenerationState::Unknown);

        let mut dirty = GenerationState::Clean;
        dirty.publish(GenerationState::DirtyAll);
        dirty.publish(GenerationState::Clean);
        assert_eq!(dirty, GenerationState::DirtyAll);

        let mut unknown = GenerationState::Clean;
        unknown.publish(GenerationState::Unknown);
        unknown.publish(GenerationState::DirtyAll);
        assert_eq!(unknown, GenerationState::Unknown);
    }

    #[test]
    fn flush_fence_requires_a_nonzero_published_id_at_or_after_target() {
        assert!(!flush_target_acknowledged(0, 0));
        assert!(!flush_target_acknowledged(0, 99));
        assert!(!flush_target_acknowledged(42, 0));
        assert!(!flush_target_acknowledged(42, 41));
        assert!(flush_target_acknowledged(42, 42));
        assert!(flush_target_acknowledged(42, 43));
    }

    #[test]
    fn callback_publication_ignores_zero_ids_and_keeps_the_maximum() {
        let mut maximum = 0;
        publish_event_id(&mut maximum, 0);
        assert_eq!(maximum, 0);
        publish_event_id(&mut maximum, 40);
        publish_event_id(&mut maximum, 12);
        publish_event_id(&mut maximum, 41);
        assert_eq!(maximum, 41);
    }

    #[test]
    fn cleanup_sequences_match_each_partial_construction_state() {
        assert_eq!(
            cleanup_actions(CleanupPhase::BeforeStreamCreation),
            &[
                CleanupAction::ReleaseQueue,
                CleanupAction::CloseKernelQueue,
                CleanupAction::CloseDescriptors,
            ]
        );
        assert_eq!(
            cleanup_actions(CleanupPhase::CreatedNotScheduled),
            &[
                CleanupAction::ReleaseStream,
                CleanupAction::ReleaseQueue,
                CleanupAction::CloseKernelQueue,
                CleanupAction::CloseDescriptors,
            ]
        );
        assert_eq!(
            cleanup_actions(CleanupPhase::ScheduledStartFailed),
            &[
                CleanupAction::InvalidateStream,
                CleanupAction::BarrierQueue,
                CleanupAction::ReleaseStream,
                CleanupAction::ReleaseQueue,
                CleanupAction::CloseKernelQueue,
                CleanupAction::CloseDescriptors,
            ]
        );
        assert_eq!(
            cleanup_actions(CleanupPhase::Started),
            &[
                CleanupAction::Cancel,
                CleanupAction::StopStream,
                CleanupAction::InvalidateStream,
                CleanupAction::BarrierQueue,
                CleanupAction::ReleaseStream,
                CleanupAction::DropOwner,
                CleanupAction::ReleaseQueue,
                CleanupAction::CloseKernelQueue,
                CleanupAction::CloseDescriptors,
            ]
        );
    }

    #[test]
    fn descriptor_chain_includes_root_and_every_component_and_caps_at_64() {
        let components = descriptor_chain_plan(&[
            "private",
            "tmp",
            "owned-parent",
            "volume",
            "namespace",
            "fs",
            "catalogs",
            "objects",
        ])
        .unwrap();
        assert_eq!(components.first().map(String::as_str), Some("/"));
        assert_eq!(
            components.last().map(String::as_str),
            Some("/private/tmp/owned-parent/volume/namespace/fs/catalogs/objects")
        );
        assert_eq!(components.len(), 9);

        let sixty_three = vec!["component"; 63];
        assert_eq!(descriptor_chain_plan(&sixty_three).unwrap().len(), 64);
        let sixty_four = vec!["component"; 64];
        assert_eq!(
            descriptor_chain_plan(&sixty_four).unwrap_err(),
            "descriptor chain exceeds 64 entries"
        );
    }

    #[test]
    fn stream_plan_is_exact_and_queue_precedes_start() {
        let plan = native_stream_plan();
        assert_eq!(plan.since_when, u64::MAX);
        assert_eq!(plan.latency_millis, 50);
        assert_eq!(plan.flags, 0x0000_0016);
        assert_eq!(
            plan.operations,
            [StreamOperation::SetSerialQueue, StreamOperation::Start]
        );
    }

    #[test]
    fn kqueue_events_fail_closed() {
        assert_eq!(
            classify_kqueue_event(false, false, 0),
            KqueueClassification::Quiet
        );
        assert_eq!(
            classify_kqueue_event(true, false, 0),
            KqueueClassification::Unknown
        );
        assert_eq!(
            classify_kqueue_event(false, true, 0),
            KqueueClassification::Unknown
        );
        assert_eq!(
            classify_kqueue_event(false, false, 1),
            KqueueClassification::Unknown
        );
    }

    #[test]
    fn opened_and_linked_stamps_must_match_and_be_single_link_regular_files() {
        let admitted = PortableStamp {
            device: 4,
            inode: 9,
            length: 32,
            mode: PORTABLE_REGULAR_MODE,
            links: 1,
        };
        assert_eq!(
            validate_opened_linked_stamp(admitted, admitted).unwrap(),
            admitted
        );

        let mut rebound = admitted;
        rebound.inode += 1;
        assert!(validate_opened_linked_stamp(admitted, rebound).is_err());
        let mut linked_outside = admitted;
        linked_outside.links = 2;
        assert!(validate_opened_linked_stamp(linked_outside, linked_outside).is_err());
    }

    #[test]
    fn distributions_use_nearest_rank_percentiles() {
        let report =
            distribution_from_nanos(&[1_000_000, 2_000_000, 3_000_000, 4_000_000]).unwrap();
        assert_eq!(report.p50_ms, 2.0);
        assert_eq!(report.p95_ms, 4.0);
        assert_eq!(report.p99_ms, 4.0);
        assert!(distribution_from_nanos(&[]).is_err());
    }

    #[test]
    fn apfs_driver_plan_owns_every_mutated_path_and_cleans_up_in_reverse() {
        let plan = apfs_driver_plan("/tmp/anima-owned");
        assert_eq!(plan.image, "/tmp/anima-owned/corefs-lease.sparseimage");
        assert_eq!(plan.mount, "/tmp/anima-owned/renameable/mount");
        assert_eq!(
            plan.create,
            vec![
                "hdiutil",
                "create",
                "-size",
                "256m",
                "-fs",
                "APFS",
                "-volname",
                "ANIMA_CORE_LEASE",
                "-type",
                "SPARSE",
                "/tmp/anima-owned/corefs-lease.sparseimage",
            ]
        );
        assert_eq!(
            plan.attach,
            vec![
                "hdiutil",
                "attach",
                "-nobrowse",
                "-mountpoint",
                "/tmp/anima-owned/renameable/mount",
                "/tmp/anima-owned/corefs-lease.sparseimage",
            ]
        );
        assert_eq!(
            plan.cleanup,
            vec![
                CleanupStep::Detach("/tmp/anima-owned/renameable/mount".to_owned()),
                CleanupStep::Remove("/tmp/anima-owned/corefs-lease.sparseimage".to_owned()),
                CleanupStep::Remove("/tmp/anima-owned".to_owned()),
            ]
        );
        assert!(plan
            .race_paths
            .iter()
            .all(|path| path.starts_with("/tmp/anima-owned/")));
    }

    #[test]
    fn existing_output_is_rejected_before_native_characterization() {
        let path = std::env::temp_dir().join(format!(
            "corefs-macos-spike-existing-output-{}.json",
            std::process::id()
        ));
        std::fs::write(&path, b"sentinel").unwrap();
        assert!(ensure_output_available(&path).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"sentinel");
        std::fs::remove_file(path).unwrap();
    }
}
