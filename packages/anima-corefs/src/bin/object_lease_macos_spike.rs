#![cfg_attr(not(any(test, target_os = "macos")), allow(dead_code))]

use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use serde::Serialize;

#[derive(Debug)]
struct Arguments {
    argv: Vec<String>,
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
    tracked_tree_clean: bool,
    target_triple: String,
    spike_source: BuildArtifactReport,
    cargo_lock: BuildArtifactReport,
    argv: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BuildArtifactReport {
    sha256: String,
    git_blob: String,
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

impl Default for GenerationState {
    fn default() -> Self {
        Self::Clean
    }
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

fn classify_outside_hard_link(
    admitted: PortableStamp,
    observed: PortableStamp,
) -> Result<bool, &'static str> {
    if admitted.device != observed.device
        || admitted.inode != observed.inode
        || admitted.length != observed.length
        || admitted.mode != observed.mode
    {
        return Err("outside hard link observation changed object identity");
    }
    if admitted.links != 1 {
        return Err("admitted object did not begin with one link");
    }
    if observed.links != 2 {
        return Err("outside hard link observation did not have exactly two links");
    }
    Ok(true)
}

fn combine_primary_cleanup<T>(
    primary: Result<T, String>,
    cleanup: Result<(), String>,
) -> Result<T, String> {
    match (primary, cleanup) {
        (Ok(value), Ok(())) => Ok(value),
        (Ok(_), Err(cleanup)) => Err(cleanup),
        (Err(primary), Ok(())) => Err(primary),
        (Err(primary), Err(cleanup)) => Err(format!("{primary}; cleanup also failed: {cleanup}")),
    }
}

fn build_rustc_identity() -> &'static str {
    env!("ANIMA_CORE_BUILD_RUSTC")
}

fn build_tracked_tree_clean() -> bool {
    env!("ANIMA_CORE_BUILD_TRACKED_TREE_CLEAN") == "true"
}

fn validate_build_tracked_tree(clean: bool) -> Result<(), &'static str> {
    if clean {
        Ok(())
    } else {
        Err("refusing native characterization from a dirty tracked build")
    }
}

fn build_report_from_embedded_provenance(argv: Vec<String>) -> BuildReport {
    BuildReport {
        profile: if cfg!(debug_assertions) {
            "debug"
        } else {
            "release"
        },
        rustc: build_rustc_identity().to_owned(),
        source_commit: env!("ANIMA_CORE_BUILD_SOURCE_COMMIT").to_owned(),
        tracked_tree_clean: build_tracked_tree_clean(),
        target_triple: env!("ANIMA_CORE_BUILD_TARGET").to_owned(),
        spike_source: BuildArtifactReport {
            sha256: env!("ANIMA_CORE_BUILD_SPIKE_SOURCE_SHA256").to_owned(),
            git_blob: env!("ANIMA_CORE_BUILD_SPIKE_SOURCE_BLOB").to_owned(),
        },
        cargo_lock: BuildArtifactReport {
            sha256: env!("ANIMA_CORE_BUILD_CARGO_LOCK_SHA256").to_owned(),
            git_blob: env!("ANIMA_CORE_BUILD_CARGO_LOCK_BLOB").to_owned(),
        },
        argv,
    }
}

fn validate_release_quiescence(
    publications_before_release: usize,
    publications_after_release: usize,
    retained_owner_count: usize,
) -> Result<(), &'static str> {
    if publications_after_release != publications_before_release {
        return Err("callback publication occurred after stream release");
    }
    if retained_owner_count != 2 {
        return Err("FSEvents context retain/release count is unbalanced");
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum OwnedComponent {
    ScratchRoot,
    RenameableAncestor,
    Mount,
    Namespace,
    Fs,
    Catalogs,
    Objects,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum RaceOperation {
    RenameRebindRenameBack,
    DeleteOriginalVnode,
    UnmountRevoke,
}

const OWNED_COMPONENTS: [OwnedComponent; 7] = [
    OwnedComponent::ScratchRoot,
    OwnedComponent::RenameableAncestor,
    OwnedComponent::Mount,
    OwnedComponent::Namespace,
    OwnedComponent::Fs,
    OwnedComponent::Catalogs,
    OwnedComponent::Objects,
];
const RACE_OPERATIONS: [RaceOperation; 3] = [
    RaceOperation::RenameRebindRenameBack,
    RaceOperation::DeleteOriginalVnode,
    RaceOperation::UnmountRevoke,
];

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct RaceCase {
    component: OwnedComponent,
    operation: RaceOperation,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct MountEpoch(u64);

impl MountEpoch {
    fn first_attach() -> Self {
        Self(1)
    }

    fn next_attach(self) -> Self {
        Self(
            self.0
                .checked_add(1)
                .expect("mount epoch cannot overflow in one characterization run"),
        )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct DescriptorEpoch(MountEpoch);

impl DescriptorEpoch {
    fn opened_for(mount: MountEpoch) -> Self {
        Self(mount)
    }

    fn require_current(self, mount: MountEpoch) -> Result<(), &'static str> {
        if self.0 == mount {
            Ok(())
        } else {
            Err("descriptor epoch belongs to a revoked mount")
        }
    }
}

const KQUEUE_NOTE_DELETE: u32 = 0x0000_0001;
const KQUEUE_NOTE_RENAME: u32 = 0x0000_0020;
const KQUEUE_NOTE_REVOKE: u32 = 0x0000_0040;
const KQUEUE_RELEVANT_NOTES: u32 = KQUEUE_NOTE_DELETE | KQUEUE_NOTE_RENAME | KQUEUE_NOTE_REVOKE;
const FENCE_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct KqueueEventEvidence {
    ident: usize,
    component: Option<OwnedComponent>,
    notes: u32,
    has_error: bool,
    has_eof: bool,
}

impl KqueueEventEvidence {
    fn is_terminal(self) -> bool {
        self.has_error || self.has_eof || self.notes & KQUEUE_RELEVANT_NOTES != 0
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct KqueuePoll {
    events: Vec<KqueueEventEvidence>,
}

impl KqueuePoll {
    fn is_terminal(&self) -> bool {
        self.events
            .iter()
            .copied()
            .any(KqueueEventEvidence::is_terminal)
    }

    fn extend(&mut self, mut other: Self) {
        self.events.append(&mut other.events);
    }

    fn proof_for(&self, case: RaceCase) -> Option<KqueueProof> {
        let expected = match case.operation {
            RaceOperation::RenameRebindRenameBack => KQUEUE_NOTE_RENAME,
            RaceOperation::DeleteOriginalVnode => KQUEUE_NOTE_DELETE,
            RaceOperation::UnmountRevoke => KQUEUE_NOTE_REVOKE,
        };
        self.events
            .iter()
            .find(|event| {
                event.component == Some(case.component)
                    && event.notes & expected != 0
                    && !event.has_error
            })
            .map(|event| KqueueProof {
                case,
                ident: event.ident,
                component: case.component,
                notes: event.notes,
            })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct KqueueProof {
    case: RaceCase,
    ident: usize,
    component: OwnedComponent,
    notes: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FenceCheckpoint {
    BeforeInitialPoll,
    AfterInitialPoll,
    AfterFlush,
    AfterWait,
    AfterFinalPoll,
    AfterRevalidation,
    BeforeReturn,
    WhileWaiting,
}

impl FenceCheckpoint {
    const ALL: [Self; 7] = [
        Self::BeforeInitialPoll,
        Self::AfterInitialPoll,
        Self::AfterFlush,
        Self::AfterWait,
        Self::AfterFinalPoll,
        Self::AfterRevalidation,
        Self::BeforeReturn,
    ];
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct CallbackProgress {
    generation: GenerationState,
    maximum_event_id: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FenceWait {
    Progress(CallbackProgress),
    TimedOut,
    Cancelled,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FenceCause {
    Clean,
    Callback,
    Kqueue,
    Cancelled(FenceCheckpoint),
    Timeout,
    Revalidation,
    KqueueFailure,
    FlushFailure,
    WaitFailure,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct FenceOutcome {
    state: GenerationState,
    cause: FenceCause,
    kqueue: KqueuePoll,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LeaseAcquisitionDecision {
    Accept,
    Retry,
    Reject,
}

fn classify_lease_acquisition(attempt: usize, outcome: &FenceOutcome) -> LeaseAcquisitionDecision {
    match (attempt, outcome.state, outcome.cause) {
        (_, GenerationState::Clean, FenceCause::Clean) => LeaseAcquisitionDecision::Accept,
        (0, GenerationState::DirtyAll, FenceCause::Callback) => LeaseAcquisitionDecision::Retry,
        _ => LeaseAcquisitionDecision::Reject,
    }
}

trait FenceDriver {
    fn on_callback_queue(&self) -> bool;
    fn cancelled(&mut self, checkpoint: FenceCheckpoint) -> bool;
    fn poll_kqueue(&mut self) -> Result<KqueuePoll, String>;
    fn flush_target(&mut self) -> Result<u64, String>;
    fn callback_progress(&mut self) -> CallbackProgress;
    fn monotonic_now(&mut self) -> Duration;
    fn wait_for_callback_progress(
        &mut self,
        target: u64,
        maximum_wait: Duration,
    ) -> Result<FenceWait, String>;
    fn revalidate(&mut self) -> Result<(), String>;
    fn publish_unknown(&mut self);
}

fn unknown_fence(
    driver: &mut impl FenceDriver,
    cause: FenceCause,
    kqueue: KqueuePoll,
) -> FenceOutcome {
    driver.publish_unknown();
    FenceOutcome {
        state: GenerationState::Unknown,
        cause,
        kqueue,
    }
}

fn cancellation_outcome(
    driver: &mut impl FenceDriver,
    checkpoint: FenceCheckpoint,
    kqueue: KqueuePoll,
) -> Option<FenceOutcome> {
    driver
        .cancelled(checkpoint)
        .then(|| unknown_fence(driver, FenceCause::Cancelled(checkpoint), kqueue))
}

fn run_fence(driver: &mut impl FenceDriver) -> Result<FenceOutcome, String> {
    if driver.on_callback_queue() {
        driver.publish_unknown();
        return Err("fence invoked from callback queue".to_owned());
    }
    let mut kqueue = KqueuePoll::default();
    if let Some(outcome) =
        cancellation_outcome(driver, FenceCheckpoint::BeforeInitialPoll, kqueue.clone())
    {
        return Ok(outcome);
    }
    match driver.poll_kqueue() {
        Ok(events) => kqueue.extend(events),
        Err(_) => return Ok(unknown_fence(driver, FenceCause::KqueueFailure, kqueue)),
    }
    if kqueue.is_terminal() {
        return Ok(unknown_fence(driver, FenceCause::Kqueue, kqueue));
    }
    if let Some(outcome) =
        cancellation_outcome(driver, FenceCheckpoint::AfterInitialPoll, kqueue.clone())
    {
        return Ok(outcome);
    }

    let target = match driver.flush_target() {
        Ok(target) => target,
        Err(_) => return Ok(unknown_fence(driver, FenceCause::FlushFailure, kqueue)),
    };
    if let Some(outcome) = cancellation_outcome(driver, FenceCheckpoint::AfterFlush, kqueue.clone())
    {
        return Ok(outcome);
    }
    if target != 0 {
        let deadline = driver.monotonic_now().saturating_add(FENCE_TIMEOUT);
        loop {
            let progress = driver.callback_progress();
            if flush_target_acknowledged(target, progress.maximum_event_id) {
                break;
            }
            let now = driver.monotonic_now();
            if now >= deadline {
                return Ok(unknown_fence(driver, FenceCause::Timeout, kqueue));
            }
            match driver.wait_for_callback_progress(target, deadline.saturating_sub(now)) {
                Ok(FenceWait::Progress(progress))
                    if flush_target_acknowledged(target, progress.maximum_event_id) =>
                {
                    break;
                }
                Ok(FenceWait::Progress(_)) => {}
                Ok(FenceWait::TimedOut) => {
                    return Ok(unknown_fence(driver, FenceCause::Timeout, kqueue))
                }
                Ok(FenceWait::Cancelled) => {
                    return Ok(unknown_fence(
                        driver,
                        FenceCause::Cancelled(FenceCheckpoint::WhileWaiting),
                        kqueue,
                    ))
                }
                Err(_) => return Ok(unknown_fence(driver, FenceCause::WaitFailure, kqueue)),
            }
        }
    }
    if let Some(outcome) = cancellation_outcome(driver, FenceCheckpoint::AfterWait, kqueue.clone())
    {
        return Ok(outcome);
    }

    match driver.poll_kqueue() {
        Ok(events) => kqueue.extend(events),
        Err(_) => return Ok(unknown_fence(driver, FenceCause::KqueueFailure, kqueue)),
    }
    if kqueue.is_terminal() {
        return Ok(unknown_fence(driver, FenceCause::Kqueue, kqueue));
    }
    if let Some(outcome) =
        cancellation_outcome(driver, FenceCheckpoint::AfterFinalPoll, kqueue.clone())
    {
        return Ok(outcome);
    }
    if driver.revalidate().is_err() {
        return Ok(unknown_fence(driver, FenceCause::Revalidation, kqueue));
    }
    if let Some(outcome) =
        cancellation_outcome(driver, FenceCheckpoint::AfterRevalidation, kqueue.clone())
    {
        return Ok(outcome);
    }
    let progress = driver.callback_progress();
    if let Some(outcome) =
        cancellation_outcome(driver, FenceCheckpoint::BeforeReturn, kqueue.clone())
    {
        return Ok(outcome);
    }
    Ok(FenceOutcome {
        state: progress.generation,
        cause: if progress.generation == GenerationState::Clean {
            FenceCause::Clean
        } else {
            FenceCause::Callback
        },
        kqueue,
    })
}

trait NativeCallbackPublisher {
    fn publish_native_batch(&self, state: GenerationState, event_ids: &[u64]);
}

fn publish_native_callback_synchronously(
    publisher: &impl NativeCallbackPublisher,
    flags: u32,
    event_ids: &[u64],
) {
    publisher.publish_native_batch(classify_callback_batch(flags, false), event_ids);
}

trait SynchronousTeardownDriver {
    type Output;

    fn on_callback_queue(&self) -> bool;
    fn teardown_complete(&self) -> bool;
    fn teardown_step(&mut self) -> Result<Self::Output, String>;
}

fn run_synchronous_teardown<D: SynchronousTeardownDriver>(
    driver: &mut D,
) -> Result<D::Output, String> {
    if driver.on_callback_queue() {
        return Err("shutdown invoked from callback queue".to_owned());
    }
    let mut first_error = None;
    loop {
        match driver.teardown_step() {
            Ok(output) => {
                if !driver.teardown_complete() {
                    return Err("teardown step returned before resource completion".to_owned());
                }
                return match first_error {
                    Some(error) => Err(error),
                    None => Ok(output),
                };
            }
            Err(error) => {
                if first_error.is_none() {
                    first_error = Some(error);
                }
                if driver.teardown_complete() {
                    return Err(first_error.expect("teardown error was recorded"));
                }
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CleanupOperation {
    NormalDetach,
    ForceDetach,
    RemoveMount,
    RemoveImage,
    RemoveRoot,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct CleanupResidue {
    attached: bool,
    mount_exists: bool,
    image_exists: bool,
    root_exists: bool,
}

impl CleanupResidue {
    fn any(self) -> bool {
        self.attached || self.mount_exists || self.image_exists || self.root_exists
    }
}

trait OwnedCleanupDriver {
    fn attached(&self) -> bool;
    fn detach(&mut self, force: bool) -> Result<(), String>;
    fn remove_mount(&mut self) -> Result<(), String>;
    fn remove_image(&mut self) -> Result<(), String>;
    fn remove_root(&mut self) -> Result<(), String>;
    fn residue(&self) -> CleanupResidue;
}

fn run_owned_cleanup(driver: &mut impl OwnedCleanupDriver) -> Result<(), String> {
    let mut errors = Vec::new();
    if driver.attached() {
        if let Err(error) = driver.detach(false) {
            errors.push(error);
        }
        if driver.attached() {
            if let Err(error) = driver.detach(true) {
                errors.push(error);
            }
        }
    }
    if !driver.attached() {
        if let Err(error) = driver.remove_mount() {
            errors.push(error);
        }
        if let Err(error) = driver.remove_image() {
            errors.push(error);
        }
        if let Err(error) = driver.remove_root() {
            errors.push(error);
        }
    } else {
        errors.push("owned cleanup remains attached; unsafe removals were skipped".to_owned());
    }
    let residue = driver.residue();
    if residue.any() {
        errors.push(format!(
            "cleanup residue: attached={}, mount_exists={}, image_exists={}, root_exists={}",
            residue.attached, residue.mount_exists, residue.image_exists, residue.root_exists
        ));
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

fn required_race_cases() -> Vec<RaceCase> {
    OWNED_COMPONENTS
        .into_iter()
        .flat_map(|component| {
            RACE_OPERATIONS.into_iter().map(move |operation| RaceCase {
                component,
                operation,
            })
        })
        .collect()
}

#[cfg(target_os = "macos")]
fn nested_mount_race_cases() -> Vec<RaceCase> {
    let mut cases = [
        OwnedComponent::ScratchRoot,
        OwnedComponent::RenameableAncestor,
        OwnedComponent::Namespace,
        OwnedComponent::Fs,
        OwnedComponent::Catalogs,
        OwnedComponent::Objects,
    ]
    .into_iter()
    .map(|component| RaceCase {
        component,
        operation: RaceOperation::RenameRebindRenameBack,
    })
    .collect::<Vec<_>>();
    cases.push(RaceCase {
        component: OwnedComponent::Mount,
        operation: RaceOperation::UnmountRevoke,
    });
    cases.push(RaceCase {
        component: OwnedComponent::Objects,
        operation: RaceOperation::DeleteOriginalVnode,
    });
    cases
}

#[derive(Clone, Copy, Debug)]
struct RaceObservation {
    case: RaceCase,
    fresh_generation: bool,
    kqueue_proof: Option<KqueueProof>,
    restored_before_callback: bool,
    delayed_zero_id_callback_terminal: bool,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ArmedRaceStep {
    Arm,
    Scan,
    Mutation,
    TerminalEvidence,
}

trait ArmedRaceDriver {
    fn arm(&mut self) -> Result<(), String>;
    fn scan_with_mutation(&mut self) -> Result<Option<String>, String>;
    fn terminal_evidence(&mut self) -> Result<FenceOutcome, String>;
}

struct ArmedRaceRejection {
    proof: KqueueProof,
    scan_failed: bool,
}

fn run_armed_race_attempt(
    driver: &mut impl ArmedRaceDriver,
    case: RaceCase,
) -> Result<ArmedRaceRejection, String> {
    driver.arm()?;
    let scan_failed = driver.scan_with_mutation()?.is_some();
    let outcome = driver.terminal_evidence()?;
    if outcome.cause != FenceCause::Kqueue {
        return Err(format!(
            "armed race {:?} on {:?} terminated as {:?}, not kqueue evidence",
            case.operation, case.component, outcome.cause
        ));
    }
    let proof = outcome.kqueue.proof_for(case).ok_or_else(|| {
        format!(
            "armed race lacks exact kqueue {:?} proof for {:?}",
            case.operation, case.component
        )
    })?;
    Ok(ArmedRaceRejection { proof, scan_failed })
}

struct RaceEvidence {
    required: std::collections::HashSet<RaceCase>,
    complete: std::collections::HashSet<RaceCase>,
    delayed_callback_complete: std::collections::HashSet<RaceCase>,
}

impl RaceEvidence {
    fn new(required: Vec<RaceCase>) -> Self {
        Self {
            required: required.into_iter().collect(),
            complete: std::collections::HashSet::new(),
            delayed_callback_complete: std::collections::HashSet::new(),
        }
    }

    fn record(&mut self, observation: RaceObservation) {
        let exact_kqueue_proof = observation
            .kqueue_proof
            .is_some_and(|proof| proof.case == observation.case);
        if observation.fresh_generation
            && exact_kqueue_proof
            && observation.restored_before_callback
        {
            self.complete.insert(observation.case);
        }
        if observation.fresh_generation
            && exact_kqueue_proof
            && observation.restored_before_callback
            && observation.delayed_zero_id_callback_terminal
        {
            self.delayed_callback_complete.insert(observation.case);
        }
    }

    fn ordered_boundary_proven(&self) -> bool {
        self.required.is_subset(&self.complete)
    }

    fn zero_id_root_changed_rejected_clean(&self) -> bool {
        self.required.is_subset(&self.delayed_callback_complete)
    }

    fn component_completed(&self, component: OwnedComponent) -> bool {
        self.required
            .iter()
            .filter(|case| case.component == component)
            .all(|case| self.complete.contains(case))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum NativeCall {
    CreateStream,
    Schedule,
    Start,
    Flush,
    Cancel,
    Stop,
    Invalidate,
    Barrier,
    ReleaseStreamAndContext,
    ValidateReleaseQuiescence,
    OwnerDrop,
    QueueDrop,
    KernelQueueDrop,
    DescriptorDrop,
}

trait NativeCallDriver {
    fn invoke(&mut self, call: NativeCall) -> Result<u64, String>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StreamPhase {
    Created,
    Scheduled,
    Started,
    Stopped,
    Invalidated,
    Quiesced,
    Released,
}

struct StreamLifecycle {
    phase: StreamPhase,
}

impl StreamLifecycle {
    fn establish(driver: &mut impl NativeCallDriver) -> Result<Self, String> {
        driver.invoke(NativeCall::CreateStream)?;
        let mut lifecycle = Self {
            phase: StreamPhase::Created,
        };
        if let Err(primary) = driver.invoke(NativeCall::Schedule) {
            let cleanup = lifecycle.teardown(driver);
            return combine_primary_cleanup(Err(primary), cleanup);
        }
        lifecycle.phase = StreamPhase::Scheduled;
        if let Err(primary) = driver.invoke(NativeCall::Start) {
            let cleanup = lifecycle.teardown(driver);
            return combine_primary_cleanup(Err(primary), cleanup);
        }
        lifecycle.phase = StreamPhase::Started;
        Ok(lifecycle)
    }

    #[cfg(test)]
    fn flush(&mut self, driver: &mut impl NativeCallDriver) -> Result<u64, String> {
        if self.phase != StreamPhase::Started {
            return Err("flush requires a started stream".to_owned());
        }
        driver.invoke(NativeCall::Flush)
    }

    fn teardown(&mut self, driver: &mut impl NativeCallDriver) -> Result<(), String> {
        if self.phase == StreamPhase::Released {
            return Ok(());
        }
        let mut errors = Vec::new();
        if self.phase == StreamPhase::Started {
            if let Err(value) = driver.invoke(NativeCall::Cancel) {
                errors.push(value);
            }
            match driver.invoke(NativeCall::Stop) {
                Ok(_) => self.phase = StreamPhase::Stopped,
                Err(value) => {
                    errors.push(value);
                    return Err(errors.join("; "));
                }
            }
        }
        if matches!(self.phase, StreamPhase::Scheduled | StreamPhase::Stopped) {
            match driver.invoke(NativeCall::Invalidate) {
                Ok(_) => self.phase = StreamPhase::Invalidated,
                Err(value) => {
                    errors.push(value);
                    return Err(errors.join("; "));
                }
            }
        }
        if self.phase == StreamPhase::Invalidated {
            match driver.invoke(NativeCall::Barrier) {
                Ok(_) => self.phase = StreamPhase::Quiesced,
                Err(value) => {
                    errors.push(value);
                    return Err(errors.join("; "));
                }
            }
        }
        if matches!(self.phase, StreamPhase::Created | StreamPhase::Quiesced) {
            match driver.invoke(NativeCall::ReleaseStreamAndContext) {
                Ok(_) => self.phase = StreamPhase::Released,
                Err(value) => {
                    errors.push(value);
                    return Err(errors.join("; "));
                }
            }
            if let Err(value) = driver.invoke(NativeCall::ValidateReleaseQuiescence) {
                errors.push(value);
                return Err(errors.join("; "));
            }
        }
        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors.join("; "))
        }
    }
}

#[cfg(test)]
struct InjectedNativeCalls {
    calls: Vec<NativeCall>,
    fail_at: Option<NativeCall>,
}

#[cfg(test)]
impl InjectedNativeCalls {
    fn fail(call: NativeCall) -> Self {
        Self {
            calls: Vec::new(),
            fail_at: Some(call),
        }
    }

    fn success() -> Self {
        Self {
            calls: Vec::new(),
            fail_at: None,
        }
    }
}

#[cfg(test)]
impl NativeCallDriver for InjectedNativeCalls {
    fn invoke(&mut self, call: NativeCall) -> Result<u64, String> {
        self.calls.push(call);
        if self.fail_at == Some(call) {
            Err(format!("injected {call:?} failure"))
        } else if call == NativeCall::Flush {
            Ok(77)
        } else {
            Ok(1)
        }
    }
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

impl CharacterizationReport {
    #[cfg(test)]
    fn contract_example(sampling: SamplingReport) -> Self {
        let restored_path_tested = matches!(&sampling, SamplingReport::RestoredPathRace { .. });
        let argv = match sampling {
            SamplingReport::Performance { warmups, samples } => vec![
                "--objects".to_owned(),
                "2500".to_owned(),
                "--warmups".to_owned(),
                warmups.to_string(),
                "--samples".to_owned(),
                samples.to_string(),
                "--output".to_owned(),
                "/tmp/corefs-object-lease-macos.json".to_owned(),
            ],
            SamplingReport::RestoredPathRace { race_samples } => vec![
                "--objects".to_owned(),
                "2500".to_owned(),
                "--race-samples".to_owned(),
                race_samples.to_string(),
                "--mount-restored-path".to_owned(),
                "--output".to_owned(),
                "/tmp/corefs-object-lease-macos-races.json".to_owned(),
            ],
        };
        Self {
            schema_version: 2,
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
                tracked_tree_clean: true,
                target_triple: "aarch64-apple-darwin".to_owned(),
                spike_source: BuildArtifactReport {
                    sha256: "contract-example-sha256".to_owned(),
                    git_blob: "contract-example-blob".to_owned(),
                },
                cargo_lock: BuildArtifactReport {
                    sha256: "contract-example-sha256".to_owned(),
                    git_blob: "contract-example-blob".to_owned(),
                },
                argv,
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
                tested: restored_path_tested,
                ancestor_above_volume_covered: restored_path_tested,
                zero_id_root_changed_rejected_clean: restored_path_tested,
            },
            outcomes: OutcomeReport {
                ordinary_events_dirty_all: true,
                ambiguous_flags_unknown: true,
                outside_hard_link_rejected: true,
            },
            ordered_boundary_proven: restored_path_tested,
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
    validate_build_tracked_tree(build_tracked_tree_clean())
        .map_err(|message| ("buildProvenanceRejected", message.to_owned()))?;
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
    let arguments = arguments.into_iter().collect::<Vec<_>>();
    let argv = arguments
        .iter()
        .map(|argument| {
            argument
                .to_str()
                .map(str::to_owned)
                .ok_or_else(|| "arguments must be valid Unicode".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
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
        argv,
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
    use std::time::{Instant, SystemTime, UNIX_EPOCH};

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
        descriptor_epoch: DescriptorEpoch,
    }

    impl ObjectWorkspace {
        fn create_under(
            parent: &Path,
            object_count: usize,
            mount_epoch: MountEpoch,
        ) -> NativeResult<Self> {
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
                descriptor_epoch: DescriptorEpoch::opened_for(mount_epoch),
            })
        }

        fn reopen_existing(
            parent: &Path,
            object_count: usize,
            mount_epoch: MountEpoch,
        ) -> NativeResult<Self> {
            let objects_path = parent
                .join("namespace")
                .join("fs")
                .join("catalogs")
                .join("objects");
            let objects = open_directory(&objects_path, false)?;
            let mut records = Vec::with_capacity(object_count);
            for index in 0..object_count {
                let name = CString::new(format!("{index:08}.object"))
                    .map_err(|_| "object name contains NUL".to_owned())?;
                let stamp = admit_opened_linked(objects.raw(), &name)?;
                records.push(ObjectRecord { name, stamp });
            }
            let entry_count = fs::read_dir(&objects_path)
                .map_err(|error| format!("enumerate reopened object namespace: {error}"))?
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| format!("enumerate reopened object entry: {error}"))?
                .len();
            if entry_count != object_count {
                return Err(format!(
                    "reopened object namespace contains {entry_count} entries, expected {object_count}"
                ));
            }
            Ok(Self {
                objects_path,
                objects,
                records,
                descriptor_epoch: DescriptorEpoch::opened_for(mount_epoch),
            })
        }

        fn require_current_mount(&self, mount_epoch: MountEpoch) -> NativeResult<()> {
            self.descriptor_epoch
                .require_current(mount_epoch)
                .map_err(str::to_owned)
        }

        fn safe_open_scan(&self) -> NativeResult<()> {
            for record in &self.records {
                let stamp = admit_opened_linked(self.objects.raw(), &record.name)?;
                validate_object_stamp(record.stamp, stamp)?;
            }
            Ok(())
        }

        fn safe_open_scan_with_mutation(
            &self,
            mut mutation: impl FnMut() -> NativeResult<()>,
        ) -> NativeResult<Option<String>> {
            let mutation_index = self.records.len() / 2;
            let mut scan_failure = None;
            for (index, record) in self.records.iter().enumerate() {
                if index == mutation_index {
                    mutation()?;
                }
                if scan_failure.is_none() {
                    let result = admit_opened_linked(self.objects.raw(), &record.name)
                        .and_then(|stamp| validate_object_stamp(record.stamp, stamp));
                    if let Err(error) = result {
                        scan_failure = Some(error);
                    }
                }
            }
            Ok(scan_failure)
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
            let primary = stat_at(self.objects.raw(), &self.records[0].name).and_then(|observed| {
                classify_outside_hard_link(self.records[0].stamp, observed).map_err(str::to_owned)
            });
            let cleanup = fs::remove_file(&target)
                .map_err(|error| format!("remove outside hard link: {error}"));
            combine_primary_cleanup(primary, cleanup)
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

        fn cleanup(&mut self) -> NativeResult<()> {
            run_owned_cleanup(&mut NativeOwnedCleanup {
                volume: None,
                scratch: Some(self),
            })
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
        component: Option<OwnedComponent>,
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
                component: None,
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
                    component: None,
                });
            }
            assign_owned_components(&mut entries, &watch_path)?;
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

    fn assign_owned_components(
        entries: &mut [DescriptorEntry],
        watch_path: &Path,
    ) -> NativeResult<()> {
        let objects = watch_path;
        let catalogs = objects
            .parent()
            .ok_or_else(|| "objects path has no catalogs parent".to_owned())?;
        let fs_path = catalogs
            .parent()
            .ok_or_else(|| "catalogs path has no fs parent".to_owned())?;
        let namespace = fs_path
            .parent()
            .ok_or_else(|| "fs path has no namespace parent".to_owned())?;
        let namespace_parent = namespace
            .parent()
            .ok_or_else(|| "namespace path has no owned parent".to_owned())?;
        let mount = (namespace_parent.file_name().and_then(|name| name.to_str()) == Some("mount"))
            .then_some(namespace_parent);
        let renameable = mount.and_then(Path::parent);
        let scratch = renameable
            .and_then(Path::parent)
            .unwrap_or(namespace_parent);

        for entry in entries {
            entry.component = if entry.path == objects {
                Some(OwnedComponent::Objects)
            } else if entry.path == catalogs {
                Some(OwnedComponent::Catalogs)
            } else if entry.path == fs_path {
                Some(OwnedComponent::Fs)
            } else if entry.path == namespace {
                Some(OwnedComponent::Namespace)
            } else if mount == Some(entry.path.as_path()) {
                Some(OwnedComponent::Mount)
            } else if renameable == Some(entry.path.as_path()) {
                Some(OwnedComponent::RenameableAncestor)
            } else if entry.path == scratch {
                Some(OwnedComponent::ScratchRoot)
            } else {
                None
            };
        }
        Ok(())
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
        registrations: std::collections::HashMap<usize, OwnedComponent>,
    }

    impl KernelQueue {
        fn register(chain: &DescriptorChain) -> NativeResult<Self> {
            let fd = unsafe { libc::kqueue() };
            if fd < 0 {
                return Err(last_error("kqueue"));
            }
            let fd = OwnedFd(fd);
            if unsafe { libc::fcntl(fd.raw(), libc::F_SETFD, libc::FD_CLOEXEC) } != 0 {
                return Err(last_error("configure kqueue"));
            }
            let mut registrations = std::collections::HashMap::new();
            for entry in &chain.entries {
                if let Some(component) = entry.component {
                    registrations.insert(entry.fd.raw() as usize, component);
                }
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
                registrations,
            })
        }

        fn poll(&self) -> NativeResult<KqueuePoll> {
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
            let mut evidence = Vec::with_capacity(count as usize);
            for event in events.into_iter().take(count as usize) {
                let ident = event.ident;
                let flags = event.flags;
                let mut notes = 0;
                if event.fflags & libc::NOTE_RENAME != 0 {
                    notes |= KQUEUE_NOTE_RENAME;
                }
                if event.fflags & libc::NOTE_DELETE != 0 {
                    notes |= KQUEUE_NOTE_DELETE;
                }
                if event.fflags & libc::NOTE_REVOKE != 0 {
                    notes |= KQUEUE_NOTE_REVOKE;
                }
                evidence.push(KqueueEventEvidence {
                    ident,
                    component: self.registrations.get(&ident).copied(),
                    notes,
                    has_error: flags & libc::EV_ERROR != 0,
                    has_eof: flags & libc::EV_EOF != 0,
                });
            }
            Ok(KqueuePoll { events: evidence })
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

    impl NativeCallbackPublisher for CallbackOwner {
        fn publish_native_batch(&self, state: GenerationState, event_ids: &[u64]) {
            self.publish(state, event_ids);
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
            publish_native_callback_synchronously(owner, combined, ids);
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

    struct MacNativeCalls {
        stream: FSEventStreamRef,
        queue: Option<SerialQueue>,
        callback: Option<Arc<CallbackOwner>>,
        watch_path: PathBuf,
        calls: Mutex<Vec<NativeCall>>,
        release_publications: Option<usize>,
    }

    unsafe impl Send for MacNativeCalls {}

    impl MacNativeCalls {
        fn queue(&self) -> NativeResult<&SerialQueue> {
            self.queue
                .as_ref()
                .ok_or_else(|| "dispatch queue was released".to_owned())
        }

        fn callback(&self) -> NativeResult<&Arc<CallbackOwner>> {
            self.callback
                .as_ref()
                .ok_or_else(|| "callback owner was released".to_owned())
        }

        fn create_stream(&mut self) -> NativeResult<()> {
            let watch_path = CString::new(self.watch_path.as_os_str().as_bytes())
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
                info: Arc::as_ptr(self.callback()?) as *mut c_void,
                retain: Some(context_retain),
                release: Some(context_release),
                copy_description: None,
            };
            let plan = native_stream_plan();
            self.stream = unsafe {
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
            if self.stream.is_null() {
                Err("FSEventStreamCreate returned null".to_owned())
            } else {
                Ok(())
            }
        }

        fn flush_shared(&self) -> NativeResult<u64> {
            self.calls
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .push(NativeCall::Flush);
            Ok(unsafe { FSEventStreamFlushAsync(self.stream) })
        }
    }

    impl NativeCallDriver for MacNativeCalls {
        fn invoke(&mut self, call: NativeCall) -> Result<u64, String> {
            self.calls
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .push(call);
            match call {
                NativeCall::CreateStream => {
                    self.create_stream()?;
                    Ok(1)
                }
                NativeCall::Schedule => {
                    unsafe {
                        FSEventStreamSetDispatchQueue(self.stream, self.queue()?.0);
                    }
                    Ok(1)
                }
                NativeCall::Start => {
                    if unsafe { FSEventStreamStart(self.stream) } == 0 {
                        Err("FSEventStreamStart returned false".to_owned())
                    } else {
                        Ok(1)
                    }
                }
                NativeCall::Flush => Ok(unsafe { FSEventStreamFlushAsync(self.stream) }),
                NativeCall::Cancel => {
                    self.callback()?.cancel();
                    Ok(1)
                }
                NativeCall::Stop => {
                    unsafe {
                        FSEventStreamStop(self.stream);
                    }
                    Ok(1)
                }
                NativeCall::Invalidate => {
                    unsafe {
                        FSEventStreamInvalidate(self.stream);
                    }
                    Ok(1)
                }
                NativeCall::Barrier => {
                    self.queue()?.barrier();
                    Ok(1)
                }
                NativeCall::ReleaseStreamAndContext => {
                    if self.stream.is_null() {
                        return Err("FSEventStream was already released".to_owned());
                    }
                    let callback = self.callback()?.clone();
                    let publications = callback.snapshot().publication_count;
                    unsafe {
                        FSEventStreamRelease(self.stream);
                    }
                    self.stream = ptr::null_mut();
                    callback.released.store(true, Ordering::Release);
                    self.release_publications = Some(publications);
                    Ok(1)
                }
                NativeCall::ValidateReleaseQuiescence => {
                    let publications = self.release_publications.ok_or_else(|| {
                        "release quiescence validation ran before stream release".to_owned()
                    })?;
                    let callback = self.callback()?.clone();
                    std::thread::sleep(CALLBACK_QUIET_PERIOD);
                    validate_release_quiescence(
                        publications,
                        callback.snapshot().publication_count,
                        Arc::strong_count(&callback),
                    )
                    .map(|()| 1)
                    .map_err(str::to_owned)
                }
                NativeCall::OwnerDrop => {
                    self.callback.take();
                    Ok(1)
                }
                NativeCall::QueueDrop => {
                    self.queue.take();
                    Ok(1)
                }
                NativeCall::KernelQueueDrop | NativeCall::DescriptorDrop => Ok(1),
            }
        }
    }

    struct NativeLease {
        lifecycle: Option<StreamLifecycle>,
        native: Option<MacNativeCalls>,
        kqueue: Option<KernelQueue>,
        chain: Option<DescriptorChain>,
        fence_serialization: Mutex<()>,
    }

    unsafe impl Send for NativeLease {}

    struct NativeFenceDriver<'a> {
        lease: &'a NativeLease,
        objects: &'a OwnedFd,
        started: Instant,
    }

    impl FenceDriver for NativeFenceDriver<'_> {
        fn on_callback_queue(&self) -> bool {
            self.lease
                .native()
                .and_then(MacNativeCalls::queue)
                .map(SerialQueue::is_current)
                .unwrap_or(false)
        }

        fn cancelled(&mut self, _checkpoint: FenceCheckpoint) -> bool {
            self.lease
                .callback()
                .map(|callback| callback.cancelled.load(Ordering::Acquire))
                .unwrap_or(true)
        }

        fn poll_kqueue(&mut self) -> NativeResult<KqueuePoll> {
            self.lease.poll_kernel()
        }

        fn flush_target(&mut self) -> NativeResult<u64> {
            let lifecycle = self
                .lease
                .lifecycle
                .as_ref()
                .ok_or_else(|| "stream lifecycle was released".to_owned())?;
            if lifecycle.phase != StreamPhase::Started {
                return Err("flush requires a started stream".to_owned());
            }
            self.lease.native()?.flush_shared()
        }

        fn callback_progress(&mut self) -> CallbackProgress {
            self.lease
                .callback()
                .map(|callback| {
                    let snapshot = callback.snapshot();
                    CallbackProgress {
                        generation: snapshot.generation,
                        maximum_event_id: snapshot.maximum_event_id,
                    }
                })
                .unwrap_or(CallbackProgress {
                    generation: GenerationState::Unknown,
                    maximum_event_id: 0,
                })
        }

        fn monotonic_now(&mut self) -> Duration {
            self.started.elapsed()
        }

        fn wait_for_callback_progress(
            &mut self,
            target: u64,
            maximum_wait: Duration,
        ) -> NativeResult<FenceWait> {
            let callback = self.lease.callback()?;
            let mut snapshot = callback
                .snapshot
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if callback.cancelled.load(Ordering::Acquire) {
                snapshot.generation.publish(GenerationState::Unknown);
                callback.notification.notify_all();
                return Ok(FenceWait::Cancelled);
            }
            if flush_target_acknowledged(target, snapshot.maximum_event_id) {
                return Ok(FenceWait::Progress(CallbackProgress {
                    generation: snapshot.generation,
                    maximum_event_id: snapshot.maximum_event_id,
                }));
            }
            let (snapshot, timeout) = callback
                .notification
                .wait_timeout(snapshot, maximum_wait)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if callback.cancelled.load(Ordering::Acquire) {
                return Ok(FenceWait::Cancelled);
            }
            let progress = CallbackProgress {
                generation: snapshot.generation,
                maximum_event_id: snapshot.maximum_event_id,
            };
            if timeout.timed_out() && !flush_target_acknowledged(target, progress.maximum_event_id)
            {
                Ok(FenceWait::TimedOut)
            } else {
                Ok(FenceWait::Progress(progress))
            }
        }

        fn revalidate(&mut self) -> NativeResult<()> {
            self.lease.revalidate(self.objects)
        }

        fn publish_unknown(&mut self) {
            if let Ok(callback) = self.lease.callback() {
                callback.publish_unknown();
            }
        }
    }

    impl NativeLease {
        fn create(objects: &OwnedFd) -> NativeResult<Self> {
            let chain = DescriptorChain::open_from_pinned(objects)?;
            let kqueue = KernelQueue::register(&chain)?;
            let queue = SerialQueue::create()?;
            let callback = Arc::new(CallbackOwner::new());
            let mut native = MacNativeCalls {
                stream: ptr::null_mut(),
                queue: Some(queue),
                callback: Some(callback),
                watch_path: chain.watch_path.clone(),
                calls: Mutex::new(Vec::new()),
                release_publications: None,
            };
            let lifecycle = StreamLifecycle::establish(&mut native)?;
            let mut lease = Self {
                lifecycle: Some(lifecycle),
                native: Some(native),
                kqueue: Some(kqueue),
                chain: Some(chain),
                fence_serialization: Mutex::new(()),
            };
            if let Err(primary) = lease.revalidate(objects) {
                let cleanup = lease.shutdown().map(|_| ());
                return match combine_primary_cleanup::<()>(Err(primary), cleanup) {
                    Ok(()) => unreachable!("an error plus cleanup cannot become success"),
                    Err(error) => Err(error),
                };
            }
            Ok(lease)
        }

        fn native(&self) -> NativeResult<&MacNativeCalls> {
            self.native
                .as_ref()
                .ok_or_else(|| "native stream ownership was released".to_owned())
        }

        fn native_mut(&mut self) -> NativeResult<&mut MacNativeCalls> {
            self.native
                .as_mut()
                .ok_or_else(|| "native stream ownership was released".to_owned())
        }

        fn callback(&self) -> NativeResult<&Arc<CallbackOwner>> {
            self.native()?.callback()
        }

        fn poll_kernel(&self) -> NativeResult<KqueuePoll> {
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

        fn fence_with_evidence(&self, objects: &OwnedFd) -> NativeResult<FenceOutcome> {
            let _fence = self
                .fence_serialization
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            run_fence(&mut NativeFenceDriver {
                lease: self,
                objects,
                started: Instant::now(),
            })
        }

        fn fence(&self, objects: &OwnedFd) -> NativeResult<GenerationState> {
            Ok(self.fence_with_evidence(objects)?.state)
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
                    self.native()?.stream,
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

        fn shutdown(&mut self) -> NativeResult<CallbackSnapshot> {
            run_synchronous_teardown(self)
        }

        fn shutdown_in_place(&mut self) -> NativeResult<CallbackSnapshot> {
            {
                let lifecycle = self
                    .lifecycle
                    .as_mut()
                    .ok_or_else(|| "stream lifecycle was already released".to_owned())?;
                let native = self
                    .native
                    .as_mut()
                    .ok_or_else(|| "native stream ownership was released".to_owned())?;
                lifecycle.teardown(native)?;
            }
            let snapshot = self.callback()?.snapshot();
            let mut cleanup_errors = Vec::new();
            for call in [NativeCall::OwnerDrop, NativeCall::QueueDrop] {
                if let Err(error) = self
                    .native_mut()
                    .and_then(|native| native.invoke(call).map(|_| ()))
                {
                    cleanup_errors.push(error);
                }
            }
            if let Err(error) = self
                .native_mut()
                .and_then(|native| native.invoke(NativeCall::KernelQueueDrop).map(|_| ()))
            {
                cleanup_errors.push(error);
            }
            self.kqueue.take();
            if let Err(error) = self
                .native_mut()
                .and_then(|native| native.invoke(NativeCall::DescriptorDrop).map(|_| ()))
            {
                cleanup_errors.push(error);
            }
            self.chain.take();
            if cleanup_errors.is_empty() {
                self.lifecycle.take();
                self.native.take();
                Ok(snapshot)
            } else {
                Err(cleanup_errors.join("; "))
            }
        }
    }

    impl SynchronousTeardownDriver for NativeLease {
        type Output = CallbackSnapshot;

        fn on_callback_queue(&self) -> bool {
            self.native()
                .and_then(MacNativeCalls::queue)
                .map(SerialQueue::is_current)
                .unwrap_or(false)
        }

        fn teardown_complete(&self) -> bool {
            self.lifecycle.is_none()
        }

        fn teardown_step(&mut self) -> NativeResult<Self::Output> {
            self.shutdown_in_place()
        }
    }

    impl Drop for NativeLease {
        fn drop(&mut self) {
            if self.lifecycle.is_none() {
                return;
            }
            if SynchronousTeardownDriver::on_callback_queue(self) {
                eprintln!("live NativeLease dropped on its callback queue; aborting");
                std::process::abort();
            }
            if let Err(error) = run_synchronous_teardown(self) {
                eprintln!("NativeLease teardown completed after error: {error}");
            }
            assert!(
                self.lifecycle.is_none(),
                "NativeLease teardown did not complete"
            );
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
                run_performance(arguments.object_count, warmups, samples, &arguments.argv)
            }
            SamplingReport::RestoredPathRace { race_samples } => {
                run_restored_path(arguments.object_count, race_samples, &arguments.argv)
            }
        };
        let report = result.map_err(|message| ("nativeCharacterizationFailed", message))?;
        write_report(&arguments.output, &report).map_err(|message| ("outputUnavailable", message))
    }

    fn run_performance(
        object_count: usize,
        warmups: usize,
        samples: usize,
        argv: &[String],
    ) -> NativeResult<CharacterizationReport> {
        let baseline_descriptors = descriptor_count()?;
        let mut scratch = ScratchRoot::create("lease-performance")?;
        let scratch_path = scratch.path.clone();
        let primary = (|| {
            let outside = scratch.path.join("outside");
            fs::create_dir(&outside)
                .map_err(|error| format!("create outside directory: {error}"))?;
            let workspace = ObjectWorkspace::create_under(
                &scratch.path,
                object_count,
                MountEpoch::first_attach(),
            )?;
            let filesystem = filesystem_report(&workspace.objects_path)?;
            if !filesystem.name.eq_ignore_ascii_case("apfs") {
                return Err(format!(
                    "performance namespace uses {}, expected APFS",
                    filesystem.name
                ));
            }
            let outside_hard_link_rejected =
                workspace.prove_outside_hard_link_rejected(&outside)?;
            if !outside_hard_link_rejected {
                return Err("fresh outside hard link was not rejected".to_owned());
            }

            let mut lease = acquire_clean_lease(&workspace)?;
            let lease_primary = (|| {
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
                Ok((
                    maximum_descriptors,
                    safe_open_samples,
                    lease_samples,
                    callback_panic_contained,
                    ordinary_events_dirty_all,
                    ambiguous_flags_unknown,
                ))
            })();
            let mut snapshot = None;
            let lease_cleanup = lease.shutdown().map(|value| {
                snapshot = Some(value);
            });
            let (
                maximum_descriptors,
                safe_open_samples,
                lease_samples,
                callback_panic_contained,
                ordinary_events_dirty_all,
                ambiguous_flags_unknown,
            ) = combine_primary_cleanup(lease_primary, lease_cleanup)?;
            let snapshot =
                snapshot.ok_or_else(|| "lease teardown did not return a snapshot".to_owned())?;
            drop(workspace);
            let post_teardown_descriptors = descriptor_count()?;

            build_report(
                object_count,
                SamplingReport::Performance { warmups, samples },
                filesystem,
                argv,
                ReportEvidence {
                    safe_open: distribution_from_nanos(&safe_open_samples)
                        .map_err(str::to_owned)?,
                    lease: distribution_from_nanos(&lease_samples).map_err(str::to_owned)?,
                    resources: ResourceReport {
                        maximum_descriptor_delta: maximum_descriptors - baseline_descriptors,
                        post_teardown_descriptor_delta: post_teardown_descriptors
                            - baseline_descriptors,
                        residue_count: 0,
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
                    ordered_boundary_proven: false,
                },
            )
        })();
        let cleanup = scratch.cleanup();
        let mut report = combine_primary_cleanup(primary, cleanup)?;
        report.resources.residue_count = usize::from(scratch_path.exists());
        if report.resources.residue_count != 0 {
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
        report.resources.post_teardown_descriptor_delta =
            post_cleanup_descriptors - baseline_descriptors;
        Ok(report)
    }

    fn acquire_clean_lease(workspace: &ObjectWorkspace) -> NativeResult<NativeLease> {
        for attempt in 0..=1 {
            let mut lease = NativeLease::create(&workspace.objects)?;
            let outcome = (|| {
                let initial_kqueue = lease.poll_kernel()?;
                if initial_kqueue.is_terminal() {
                    return Ok(FenceOutcome {
                        state: GenerationState::Unknown,
                        cause: FenceCause::Kqueue,
                        kqueue: initial_kqueue,
                    });
                }
                lease.revalidate(&workspace.objects)?;
                workspace.safe_open_scan()?;
                lease.fence_with_evidence(&workspace.objects)
            })();

            let outcome = match outcome {
                Ok(outcome) => outcome,
                Err(primary) => {
                    return combine_primary_cleanup(Err(primary), lease.shutdown().map(|_| ()))
                }
            };
            match classify_lease_acquisition(attempt, &outcome) {
                LeaseAcquisitionDecision::Accept => return Ok(lease),
                LeaseAcquisitionDecision::Retry => {
                    lease.shutdown()?;
                }
                LeaseAcquisitionDecision::Reject => {
                    let primary = format!(
                        "lease acquisition attempt {} rejected: state={:?}, cause={:?}, kqueue={:?}",
                        attempt + 1,
                        outcome.state,
                        outcome.cause,
                        outcome.kqueue
                    );
                    return combine_primary_cleanup(Err(primary), lease.shutdown().map(|_| ()));
                }
            }
        }
        unreachable!("the second lease acquisition attempt cannot request another retry")
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
        argv: &[String],
        evidence: ReportEvidence,
    ) -> NativeResult<CharacterizationReport> {
        Ok(CharacterizationReport {
            schema_version: 2,
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
            build: build_report_from_embedded_provenance(argv.to_vec()),
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
        argv: &[String],
    ) -> NativeResult<CharacterizationReport> {
        run_restored_path_characterization(object_count, race_samples, argv)
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
        mount_epoch: MountEpoch,
    }

    struct ApfsCreateFailure {
        primary: String,
        volume: ApfsVolume,
    }

    impl ApfsVolume {
        fn create(owned_root: &Path) -> Result<Self, ApfsCreateFailure> {
            Self::create_at(
                owned_root.join("corefs-lease.sparseimage"),
                owned_root.join("renameable").join("mount"),
                "ANIMA_CORE_LEASE",
            )
        }

        fn create_at(
            image: PathBuf,
            mount: PathBuf,
            volume_name: &str,
        ) -> Result<Self, ApfsCreateFailure> {
            let mut volume = Self {
                image,
                mount,
                attached: false,
                mount_epoch: MountEpoch(0),
            };
            let primary = (|| {
                let image = volume
                    .image
                    .to_str()
                    .ok_or_else(|| "owned APFS image path is not UTF-8".to_owned())?;
                let create = vec![
                    "hdiutil".to_owned(),
                    "create".to_owned(),
                    "-size".to_owned(),
                    "256m".to_owned(),
                    "-fs".to_owned(),
                    "APFS".to_owned(),
                    "-volname".to_owned(),
                    volume_name.to_owned(),
                    "-type".to_owned(),
                    "SPARSE".to_owned(),
                    image.to_owned(),
                ];
                fs::create_dir_all(&volume.mount)
                    .map_err(|error| format!("create APFS mount point: {error}"))?;
                run_command_plan(&create)?;
                volume.attach()
            })();
            if let Err(error) = primary {
                return Err(ApfsCreateFailure {
                    primary: error,
                    volume,
                });
            }
            Ok(volume)
        }

        fn attach(&mut self) -> NativeResult<()> {
            if self.attached {
                return Err("APFS image is already attached".to_owned());
            }
            fs::create_dir_all(&self.mount)
                .map_err(|error| format!("recreate APFS mount point: {error}"))?;
            self.attached = true;
            let result = run_status(
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
            );
            // hdiutil failures are ambiguous: the attached state intentionally remains set so
            // cleanup must prove detachment before removing any owned paths.
            result?;
            self.mount_epoch = if self.mount_epoch.0 == 0 {
                MountEpoch::first_attach()
            } else {
                self.mount_epoch.next_attach()
            };
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
    }

    struct NativeOwnedCleanup<'a> {
        volume: Option<&'a mut ApfsVolume>,
        scratch: Option<&'a mut ScratchRoot>,
    }

    impl OwnedCleanupDriver for NativeOwnedCleanup<'_> {
        fn attached(&self) -> bool {
            self.volume.as_deref().is_some_and(|volume| volume.attached)
        }

        fn detach(&mut self, force: bool) -> NativeResult<()> {
            let volume = self
                .volume
                .as_deref_mut()
                .ok_or_else(|| "cleanup has no APFS volume to detach".to_owned())?;
            if force {
                volume.detach_for_race()
            } else {
                volume.detach()
            }
        }

        fn remove_mount(&mut self) -> NativeResult<()> {
            let Some(volume) = self.volume.as_deref_mut() else {
                return Ok(());
            };
            if volume.mount.exists() {
                fs::remove_dir_all(&volume.mount)
                    .map_err(|error| format!("remove APFS mount point: {error}"))?;
            }
            Ok(())
        }

        fn remove_image(&mut self) -> NativeResult<()> {
            let Some(volume) = self.volume.as_deref_mut() else {
                return Ok(());
            };
            if volume.image.exists() {
                fs::remove_file(&volume.image)
                    .map_err(|error| format!("remove APFS image: {error}"))?;
            }
            Ok(())
        }

        fn remove_root(&mut self) -> NativeResult<()> {
            let Some(scratch) = self.scratch.as_deref_mut() else {
                return Ok(());
            };
            let result = if scratch.path.exists() {
                fs::remove_dir_all(&scratch.path).map_err(|error| {
                    format!("remove scratch root {}: {error}", scratch.path.display())
                })
            } else {
                Ok(())
            };
            if !scratch.path.exists() {
                scratch.active = false;
            }
            result
        }

        fn residue(&self) -> CleanupResidue {
            CleanupResidue {
                attached: self.volume.as_deref().is_some_and(|volume| volume.attached),
                mount_exists: self
                    .volume
                    .as_deref()
                    .is_some_and(|volume| volume.mount.exists()),
                image_exists: self
                    .volume
                    .as_deref()
                    .is_some_and(|volume| volume.image.exists()),
                root_exists: self
                    .scratch
                    .as_deref()
                    .is_some_and(|scratch| scratch.path.exists()),
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

    struct NativeArmedRaceAttempt<'a> {
        active_workspace: &'a ObjectWorkspace,
        stable_workspace: &'a ObjectWorkspace,
        scratch_root: &'a Path,
        target: &'a Path,
        volume: &'a mut ApfsVolume,
        case: RaceCase,
        lease: Option<NativeLease>,
        scan_elapsed_nanos: Option<u128>,
    }

    impl NativeArmedRaceAttempt<'_> {
        fn lease(&self) -> NativeResult<&NativeLease> {
            self.lease
                .as_ref()
                .ok_or_else(|| "armed race lease was not established".to_owned())
        }

        fn into_lease(mut self) -> NativeResult<NativeLease> {
            self.lease
                .take()
                .ok_or_else(|| "armed race lease was not established".to_owned())
        }

        fn scan_elapsed_nanos(&self) -> NativeResult<u128> {
            self.scan_elapsed_nanos
                .ok_or_else(|| "armed race validation scan did not run".to_owned())
        }
    }

    impl ArmedRaceDriver for NativeArmedRaceAttempt<'_> {
        fn arm(&mut self) -> NativeResult<()> {
            self.active_workspace
                .require_current_mount(self.volume.mount_epoch)?;
            let lease = NativeLease::create(&self.active_workspace.objects)?;
            if lease.poll_kernel()?.is_terminal() {
                return Err(format!(
                    "fresh {:?} lease was terminal immediately after start",
                    self.case.component
                ));
            }
            lease.revalidate(&self.active_workspace.objects)?;
            self.lease = Some(lease);
            Ok(())
        }

        fn scan_with_mutation(&mut self) -> NativeResult<Option<String>> {
            let case = self.case;
            let scratch_root = self.scratch_root;
            let target = self.target;
            let stable_workspace = self.stable_workspace;
            let volume = &mut self.volume;
            let started = Instant::now();
            let result =
                self.active_workspace
                    .safe_open_scan_with_mutation(|| match case.operation {
                        RaceOperation::RenameRebindRenameBack => {
                            exercise_rename_delete_rebind_race(
                                scratch_root,
                                target,
                                stable_workspace,
                            )
                        }
                        RaceOperation::UnmountRevoke => volume.detach_for_race(),
                        RaceOperation::DeleteOriginalVnode => {
                            exercise_original_vnode_delete(scratch_root, target)
                        }
                    });
            self.scan_elapsed_nanos = Some(started.elapsed().as_nanos());
            result
        }

        fn terminal_evidence(&mut self) -> NativeResult<FenceOutcome> {
            self.lease()?
                .fence_with_evidence(&self.active_workspace.objects)
        }
    }

    struct RaceScheduleResult {
        evidence: RaceEvidence,
        safe_open_samples: Vec<u128>,
        fence_samples: Vec<u128>,
        maximum_descriptors: i64,
        callback_after_release: bool,
    }

    fn run_owned_component_matrix(
        host_root: &Path,
        object_count: usize,
        sample_count: usize,
        required_cases: &[RaceCase],
        baseline_descriptors: i64,
    ) -> NativeResult<RaceScheduleResult> {
        if sample_count < required_cases.len() {
            return Err(format!(
                "owned-component matrix requires at least {} samples, got {sample_count}",
                required_cases.len()
            ));
        }
        let mut volume = match ApfsVolume::create_at(
            host_root.join("owned-component-matrix.sparseimage"),
            host_root.join("owned-component-matrix-volume"),
            "ANIMA_CORE_LEASE_MATRIX",
        ) {
            Ok(volume) => volume,
            Err(mut failure) => {
                let cleanup = run_owned_cleanup(&mut NativeOwnedCleanup {
                    volume: Some(&mut failure.volume),
                    scratch: None,
                });
                return combine_primary_cleanup(Err(failure.primary), cleanup);
            }
        };
        let primary = (|| {
            let owned_root = volume.mount.clone();
            let logical_scratch = owned_root.join("scratch");
            let logical_mount = logical_scratch.join("renameable").join("mount");
            let mut workspace =
                ObjectWorkspace::create_under(&logical_mount, object_count, volume.mount_epoch)?;
            let rename_cases = required_cases
                .iter()
                .copied()
                .filter(|case| case.operation == RaceOperation::RenameRebindRenameBack)
                .collect::<Vec<_>>();
            let mut schedule = Vec::with_capacity(sample_count);
            for sample in 0..sample_count - required_cases.len() {
                schedule.push(rename_cases[sample % rename_cases.len()]);
            }
            schedule.extend(required_cases.iter().copied());

            let mut evidence = RaceEvidence::new(required_cases.to_vec());
            let mut safe_open_samples = Vec::with_capacity(sample_count);
            let mut fence_samples = Vec::with_capacity(sample_count);
            let mut maximum_descriptors = baseline_descriptors;
            let mut callback_after_release = false;

            for case in schedule {
                workspace.require_current_mount(volume.mount_epoch)?;
                let target = race_component_path(&logical_scratch, &logical_mount, case.component);
                let fence_started = Instant::now();
                let mut attempt = NativeArmedRaceAttempt {
                    active_workspace: &workspace,
                    stable_workspace: &workspace,
                    scratch_root: &owned_root,
                    target: &target,
                    volume: &mut volume,
                    case,
                    lease: None,
                    scan_elapsed_nanos: None,
                };
                let armed_rejection = run_armed_race_attempt(&mut attempt, case);
                if attempt.scan_elapsed_nanos.is_some() {
                    safe_open_samples.push(attempt.scan_elapsed_nanos()?);
                }
                let mut lease = match attempt.into_lease() {
                    Ok(lease) => lease,
                    Err(no_lease) => {
                        return match armed_rejection {
                            Ok(_) => Err(no_lease),
                            Err(primary) => Err(primary),
                        };
                    }
                };
                let race_primary = (|| {
                    let rejection = armed_rejection?;
                    if matches!(
                        case.operation,
                        RaceOperation::UnmountRevoke | RaceOperation::DeleteOriginalVnode
                    ) && !rejection.scan_failed
                    {
                        return Err(format!(
                            "armed {:?} scan did not observe its revoked descriptor",
                            case.operation
                        ));
                    }
                    maximum_descriptors = maximum_descriptors
                        .max(descriptor_count()?)
                        .max(baseline_descriptors + lease.descriptor_count() as i64);

                    if case.operation == RaceOperation::UnmountRevoke {
                        volume.attach()?;
                    }
                    let restored_before_callback = target.exists();
                    if !restored_before_callback {
                        return Err(format!(
                            "owned-component race path was not restored before delayed callback: {}",
                            target.display()
                        ));
                    }

                    let before_callback = lease.callback()?.snapshot();
                    lease.inject_callback(FSEVENT_ROOT_CHANGED, 0)?;
                    let after_callback = lease.callback()?.snapshot();
                    let delayed_zero_id_callback_terminal = after_callback.publication_count
                        > before_callback.publication_count
                        && after_callback.generation == GenerationState::Unknown
                        && after_callback.maximum_event_id == before_callback.maximum_event_id
                        && lease.fence(&workspace.objects)? == GenerationState::Unknown;
                    fence_samples.push(fence_started.elapsed().as_nanos());
                    Ok(RaceObservation {
                        case,
                        fresh_generation: true,
                        kqueue_proof: Some(rejection.proof),
                        restored_before_callback,
                        delayed_zero_id_callback_terminal,
                    })
                })();
                let lease_cleanup = lease.shutdown().map(|snapshot| {
                    callback_after_release |= snapshot.callback_after_release;
                });
                evidence.record(combine_primary_cleanup(race_primary, lease_cleanup)?);

                drop(workspace);
                if case.operation == RaceOperation::DeleteOriginalVnode {
                    if logical_scratch.exists() {
                        fs::remove_dir_all(&logical_scratch).map_err(|error| {
                            format!(
                                "reset owned-component matrix {}: {error}",
                                logical_scratch.display()
                            )
                        })?;
                    }
                    workspace = ObjectWorkspace::create_under(
                        &logical_mount,
                        object_count,
                        volume.mount_epoch,
                    )?;
                } else {
                    workspace = ObjectWorkspace::reopen_existing(
                        &logical_mount,
                        object_count,
                        volume.mount_epoch,
                    )?;
                }
                workspace.require_current_mount(volume.mount_epoch)?;
            }

            Ok(RaceScheduleResult {
                evidence,
                safe_open_samples,
                fence_samples,
                maximum_descriptors,
                callback_after_release,
            })
        })();
        let cleanup = run_owned_cleanup(&mut NativeOwnedCleanup {
            volume: Some(&mut volume),
            scratch: None,
        });
        combine_primary_cleanup(primary, cleanup)
    }

    fn run_restored_path_characterization(
        object_count: usize,
        race_samples: usize,
        argv: &[String],
    ) -> NativeResult<CharacterizationReport> {
        let required_cases = required_race_cases();
        let nested_mount_cases = nested_mount_race_cases();
        let minimum_samples = required_cases.len() + nested_mount_cases.len();
        if race_samples < minimum_samples {
            return Err(format!(
                "restored-path characterization requires at least {} samples, got {race_samples}",
                minimum_samples
            ));
        }
        let baseline_descriptors = descriptor_count()?;
        let mut scratch = ScratchRoot::create("lease-apfs")?;
        let scratch_path = scratch.path.clone();
        let primary = (|| -> NativeResult<CharacterizationReport> {
            let mut volume = match ApfsVolume::create(&scratch.path) {
                Ok(volume) => volume,
                Err(mut failure) => {
                    let cleanup = run_owned_cleanup(&mut NativeOwnedCleanup {
                        volume: Some(&mut failure.volume),
                        scratch: Some(&mut scratch),
                    });
                    return combine_primary_cleanup(Err(failure.primary), cleanup);
                }
            };
            let volume_primary = (|| {
                let mut workspace =
                    ObjectWorkspace::create_under(&volume.mount, object_count, volume.mount_epoch)?;
                let filesystem = filesystem_report(&workspace.objects_path)?;
                if !filesystem.name.eq_ignore_ascii_case("apfs") {
                    return Err(format!(
                        "owned characterization image mounted as {}, expected APFS",
                        filesystem.name
                    ));
                }
                let outside = volume.mount.join("outside");
                fs::create_dir(&outside)
                    .map_err(|error| format!("create APFS outside path: {error}"))?;
                let outside_hard_link_rejected =
                    workspace.prove_outside_hard_link_rejected(&outside)?;
                if !outside_hard_link_rejected {
                    return Err("fresh APFS outside hard link was not rejected".to_owned());
                }

                let schedule = nested_mount_cases.clone();
                let mut nested_mount_evidence = RaceEvidence::new(nested_mount_cases.clone());
                let mut safe_open_samples = Vec::with_capacity(race_samples);
                let mut race_fence_samples = Vec::with_capacity(race_samples);
                let mut maximum_descriptors = baseline_descriptors;
                let mut callback_after_release = false;

                for case in schedule {
                    workspace.require_current_mount(volume.mount_epoch)?;
                    let disposable_root = volume.mount.join("original-vnode-delete-case");
                    let disposable_workspace =
                        if case.operation == RaceOperation::DeleteOriginalVnode {
                            Some(ObjectWorkspace::create_under(
                                &disposable_root,
                                object_count,
                                volume.mount_epoch,
                            )?)
                        } else {
                            None
                        };
                    let active_workspace = disposable_workspace.as_ref().unwrap_or(&workspace);
                    let target = if case.operation == RaceOperation::DeleteOriginalVnode {
                        active_workspace.objects_path.clone()
                    } else {
                        race_component_path(&scratch.path, &volume.mount, case.component)
                    };
                    let fence_started = Instant::now();
                    let mut attempt = NativeArmedRaceAttempt {
                        active_workspace,
                        stable_workspace: &workspace,
                        scratch_root: &scratch.path,
                        target: &target,
                        volume: &mut volume,
                        case,
                        lease: None,
                        scan_elapsed_nanos: None,
                    };
                    let armed_rejection = run_armed_race_attempt(&mut attempt, case);
                    if attempt.scan_elapsed_nanos.is_some() {
                        safe_open_samples.push(attempt.scan_elapsed_nanos()?);
                    }
                    let mut lease = match attempt.into_lease() {
                        Ok(lease) => lease,
                        Err(no_lease) => {
                            return match armed_rejection {
                                Ok(_) => Err(no_lease),
                                Err(primary) => Err(primary),
                            };
                        }
                    };
                    let race_primary = (|| {
                        let rejection = armed_rejection?;
                        if matches!(
                            case.operation,
                            RaceOperation::UnmountRevoke | RaceOperation::DeleteOriginalVnode
                        ) && !rejection.scan_failed
                        {
                            return Err(format!(
                                "armed {:?} scan did not observe its revoked descriptor",
                                case.operation
                            ));
                        }
                        maximum_descriptors = maximum_descriptors
                            .max(descriptor_count()?)
                            .max(baseline_descriptors + lease.descriptor_count() as i64);

                        if case.operation == RaceOperation::UnmountRevoke {
                            volume.attach()?;
                        }
                        let restored_before_callback = target.exists();
                        if !restored_before_callback {
                            return Err(format!(
                                "race path was not rebound before delayed callback: {}",
                                target.display()
                            ));
                        }

                        let before_callback = lease.callback()?.snapshot();
                        lease.inject_callback(FSEVENT_ROOT_CHANGED, 0)?;
                        let after_callback = lease.callback()?.snapshot();
                        let delayed_zero_id_callback_terminal = after_callback.publication_count
                            > before_callback.publication_count
                            && after_callback.generation == GenerationState::Unknown
                            && after_callback.maximum_event_id == before_callback.maximum_event_id
                            && lease.fence(&active_workspace.objects)? == GenerationState::Unknown;
                        race_fence_samples.push(fence_started.elapsed().as_nanos());
                        Ok(RaceObservation {
                            case,
                            fresh_generation: true,
                            kqueue_proof: Some(rejection.proof),
                            restored_before_callback,
                            delayed_zero_id_callback_terminal,
                        })
                    })();
                    let lease_cleanup = lease.shutdown().map(|snapshot| {
                        callback_after_release |= snapshot.callback_after_release;
                    });
                    nested_mount_evidence
                        .record(combine_primary_cleanup(race_primary, lease_cleanup)?);
                    drop(disposable_workspace);
                    if case.operation == RaceOperation::DeleteOriginalVnode {
                        fs::remove_dir_all(&disposable_root).map_err(|error| {
                            format!(
                                "remove disposable original-vnode case {}: {error}",
                                disposable_root.display()
                            )
                        })?;
                        if disposable_root.exists() {
                            return Err(format!(
                                "disposable original-vnode case still exists: {}",
                                disposable_root.display()
                            ));
                        }
                    }
                    if case.operation == RaceOperation::UnmountRevoke {
                        workspace = ObjectWorkspace::reopen_existing(
                            &volume.mount,
                            object_count,
                            volume.mount_epoch,
                        )?;
                        workspace.require_current_mount(volume.mount_epoch)?;
                    }
                }

                let matrix = run_owned_component_matrix(
                    &scratch.path,
                    object_count,
                    race_samples - nested_mount_cases.len(),
                    &required_cases,
                    baseline_descriptors,
                )?;
                safe_open_samples.extend(matrix.safe_open_samples);
                race_fence_samples.extend(matrix.fence_samples);
                maximum_descriptors = maximum_descriptors.max(matrix.maximum_descriptors);
                callback_after_release |= matrix.callback_after_release;

                let ordered_boundary_proven = nested_mount_evidence.ordered_boundary_proven()
                    && matrix.evidence.ordered_boundary_proven();
                let zero_id_root_changed_rejected_clean = nested_mount_evidence
                    .zero_id_root_changed_rejected_clean()
                    && matrix.evidence.zero_id_root_changed_rejected_clean();
                let ancestor_above_volume_covered = nested_mount_evidence
                    .component_completed(OwnedComponent::ScratchRoot)
                    && nested_mount_evidence
                        .component_completed(OwnedComponent::RenameableAncestor);
                if !ordered_boundary_proven {
                    return Err(
                        "fresh-generation kqueue evidence did not cover every required race"
                            .to_owned(),
                    );
                }
                if !zero_id_root_changed_rejected_clean {
                    return Err(
                        "delayed zero-ID root-changed callbacks were not terminal for every race"
                            .to_owned(),
                    );
                }
                if !ancestor_above_volume_covered {
                    return Err(
                        "race evidence did not cover both disposable ancestors above the volume"
                            .to_owned(),
                    );
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

                drop(workspace);
                build_report(
                    object_count,
                    SamplingReport::RestoredPathRace { race_samples },
                    filesystem,
                    argv,
                    ReportEvidence {
                        safe_open: distribution_from_nanos(&safe_open_samples)
                            .map_err(str::to_owned)?,
                        lease: distribution_from_nanos(&race_fence_samples)
                            .map_err(str::to_owned)?,
                        resources: ResourceReport {
                            maximum_descriptor_delta: maximum_descriptors - baseline_descriptors,
                            post_teardown_descriptor_delta: 0,
                            residue_count: 0,
                        },
                        lifecycle: LifecycleReport {
                            creation_passed: true,
                            start_passed: true,
                            callback_panic_contained,
                            teardown_passed: true,
                            callback_after_release,
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
                        ordered_boundary_proven,
                    },
                )
            })();
            let cleanup = run_owned_cleanup(&mut NativeOwnedCleanup {
                volume: Some(&mut volume),
                scratch: Some(&mut scratch),
            });
            combine_primary_cleanup(volume_primary, cleanup)
        })();
        let mut report = primary?;
        report.resources.residue_count = usize::from(scratch_path.exists());
        if report.resources.residue_count != 0 {
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
        report.resources.post_teardown_descriptor_delta =
            post_cleanup_descriptors - baseline_descriptors;
        Ok(report)
    }

    fn race_component_path(
        scratch_root: &Path,
        mount: &Path,
        component: OwnedComponent,
    ) -> PathBuf {
        match component {
            OwnedComponent::ScratchRoot => scratch_root.to_owned(),
            OwnedComponent::RenameableAncestor => scratch_root.join("renameable"),
            OwnedComponent::Mount => mount.to_owned(),
            OwnedComponent::Namespace => mount.join("namespace"),
            OwnedComponent::Fs => mount.join("namespace/fs"),
            OwnedComponent::Catalogs => mount.join("namespace/fs/catalogs"),
            OwnedComponent::Objects => mount.join("namespace/fs/catalogs/objects"),
        }
    }

    fn exercise_rename_delete_rebind_race(
        owned_root: &Path,
        target: &Path,
        workspace: &ObjectWorkspace,
    ) -> NativeResult<()> {
        if !target.starts_with(owned_root) {
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
        if let Err(primary) = result {
            let recovery = {
                let mut errors = Vec::new();
                if target.exists() {
                    if let Err(error) = fs::remove_dir_all(target) {
                        errors.push(format!(
                            "remove rebound path {} during recovery: {error}",
                            target.display()
                        ));
                    }
                }
                if away.exists() && !target.exists() {
                    if let Err(error) = fs::rename(&away, target) {
                        errors.push(format!(
                            "restore {} during recovery: {error}",
                            target.display()
                        ));
                    }
                }
                if !target.exists() || away.exists() {
                    errors.push(format!(
                        "rename race recovery left target_exists={} away_exists={}",
                        target.exists(),
                        away.exists()
                    ));
                }
                if errors.is_empty() {
                    Ok(())
                } else {
                    Err(errors.join("; "))
                }
            };
            return combine_primary_cleanup::<()>(Err(primary), recovery);
        }
        Ok(())
    }

    fn exercise_original_vnode_delete(owned_root: &Path, target: &Path) -> NativeResult<()> {
        if !target.starts_with(owned_root) || target == owned_root {
            return Err(format!(
                "refusing original-vnode deletion outside a child of owned scratch root: {}",
                target.display()
            ));
        }
        let file_name = target
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| "delete target has no UTF-8 file name".to_owned())?;
        let away = target.with_file_name(format!("{file_name}.original"));
        if away.exists() {
            return Err(format!(
                "delete staging path already exists: {}",
                away.display()
            ));
        }
        let original_identity = directory_identity(open_directory(target, true)?.raw())?;
        fs::rename(target, &away)
            .map_err(|error| format!("rename original {} away: {error}", target.display()))?;
        let primary = (|| {
            fs::create_dir_all(target)
                .map_err(|error| format!("create rebound path {}: {error}", target.display()))?;
            let rebound_identity = directory_identity(open_directory(target, true)?.raw())?;
            if rebound_identity == original_identity {
                return Err(format!(
                    "rebound path unexpectedly retained original vnode at {}",
                    target.display()
                ));
            }
            fs::remove_dir_all(&away).map_err(|error| {
                format!("delete original vnode tree {}: {error}", away.display())
            })?;
            if !target.exists() || away.exists() {
                return Err(format!(
                    "original-vnode delete left target_exists={} original_exists={}",
                    target.exists(),
                    away.exists()
                ));
            }
            Ok(())
        })();
        if let Err(primary) = primary {
            let recovery = {
                let mut errors = Vec::new();
                if target.exists() {
                    if let Err(error) = fs::remove_dir_all(target) {
                        errors.push(format!(
                            "remove rebound path {} during recovery: {error}",
                            target.display()
                        ));
                    }
                }
                if away.exists() && !target.exists() {
                    if let Err(error) = fs::rename(&away, target) {
                        errors.push(format!(
                            "restore original path {} during recovery: {error}",
                            target.display()
                        ));
                    }
                }
                if errors.is_empty() {
                    Ok(())
                } else {
                    Err(errors.join("; "))
                }
            };
            return combine_primary_cleanup::<()>(Err(primary), recovery);
        }
        Ok(())
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
            parsed.argv,
            vec![
                "--objects",
                "2500",
                "--warmups",
                "30",
                "--samples",
                "200",
                "--output",
                "/tmp/corefs-object-lease-macos.json",
            ]
        );
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
                "schemaVersion": 2,
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
                    "sourceCommit": "contract-example",
                    "trackedTreeClean": true,
                    "targetTriple": "aarch64-apple-darwin",
                    "spikeSource": {
                        "sha256": "contract-example-sha256",
                        "gitBlob": "contract-example-blob"
                    },
                    "cargoLock": {
                        "sha256": "contract-example-sha256",
                        "gitBlob": "contract-example-blob"
                    },
                    "argv": [
                        "--objects",
                        "2500",
                        "--warmups",
                        "30",
                        "--samples",
                        "200",
                        "--output",
                        "/tmp/corefs-object-lease-macos.json"
                    ]
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
                    "tested": false,
                    "ancestorAboveVolumeCovered": false,
                    "zeroIdRootChangedRejectedClean": false
                },
                "outcomes": {
                    "ordinaryEventsDirtyAll": true,
                    "ambiguousFlagsUnknown": true,
                    "outsideHardLinkRejected": true
                },
                "orderedBoundaryProven": false
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

    #[test]
    fn required_race_matrix_covers_every_operation_for_every_owned_component() {
        let required = required_race_cases();
        let components = [
            OwnedComponent::ScratchRoot,
            OwnedComponent::RenameableAncestor,
            OwnedComponent::Mount,
            OwnedComponent::Namespace,
            OwnedComponent::Fs,
            OwnedComponent::Catalogs,
            OwnedComponent::Objects,
        ];
        let operations = [
            RaceOperation::RenameRebindRenameBack,
            RaceOperation::DeleteOriginalVnode,
            RaceOperation::UnmountRevoke,
        ];

        assert_eq!(required.len(), components.len() * operations.len());
        for component in components {
            for operation in operations {
                assert!(
                    required.contains(&RaceCase {
                        component,
                        operation,
                    }),
                    "missing {operation:?} characterization for {component:?}"
                );
            }
        }
    }

    #[test]
    fn race_evidence_requires_fresh_kqueue_and_delayed_callback_proof_for_every_case() {
        let required = required_race_cases();
        assert!(required
            .iter()
            .any(|case| case.component == OwnedComponent::ScratchRoot));
        assert!(required
            .iter()
            .any(|case| case.component == OwnedComponent::Mount));
        assert!(required
            .iter()
            .any(|case| case.operation == RaceOperation::DeleteOriginalVnode));
        assert!(required
            .iter()
            .any(|case| case.operation == RaceOperation::UnmountRevoke));

        let mut evidence = RaceEvidence::new(required.clone());
        for case in &required {
            evidence.record(RaceObservation {
                case: *case,
                fresh_generation: true,
                kqueue_proof: Some(KqueueProof {
                    case: *case,
                    ident: 1,
                    component: case.component,
                    notes: match case.operation {
                        RaceOperation::RenameRebindRenameBack => KQUEUE_NOTE_RENAME,
                        RaceOperation::DeleteOriginalVnode => KQUEUE_NOTE_DELETE,
                        RaceOperation::UnmountRevoke => KQUEUE_NOTE_REVOKE,
                    },
                }),
                restored_before_callback: true,
                delayed_zero_id_callback_terminal: true,
            });
        }
        assert!(evidence.ordered_boundary_proven());
        assert!(evidence.zero_id_root_changed_rejected_clean());
        assert!(evidence.component_completed(OwnedComponent::ScratchRoot));
        assert!(evidence.component_completed(OwnedComponent::Mount));

        let mut incomplete = RaceEvidence::new(required);
        incomplete.record(RaceObservation {
            case: required_race_cases()[0],
            fresh_generation: false,
            kqueue_proof: None,
            restored_before_callback: true,
            delayed_zero_id_callback_terminal: true,
        });
        assert!(!incomplete.ordered_boundary_proven());
        assert!(!incomplete.zero_id_root_changed_rejected_clean());
    }

    #[test]
    fn native_lifecycle_injection_runs_the_real_partial_state_machine() {
        let mut null_create = InjectedNativeCalls::fail(NativeCall::CreateStream);
        assert!(StreamLifecycle::establish(&mut null_create).is_err());
        assert_eq!(null_create.calls, vec![NativeCall::CreateStream]);

        let mut failed_schedule = InjectedNativeCalls::fail(NativeCall::Schedule);
        assert!(StreamLifecycle::establish(&mut failed_schedule).is_err());
        assert_eq!(
            failed_schedule.calls,
            vec![
                NativeCall::CreateStream,
                NativeCall::Schedule,
                NativeCall::ReleaseStreamAndContext,
                NativeCall::ValidateReleaseQuiescence,
            ]
        );

        let mut failed_start = InjectedNativeCalls::fail(NativeCall::Start);
        assert!(StreamLifecycle::establish(&mut failed_start).is_err());
        assert_eq!(
            failed_start.calls,
            vec![
                NativeCall::CreateStream,
                NativeCall::Schedule,
                NativeCall::Start,
                NativeCall::Invalidate,
                NativeCall::Barrier,
                NativeCall::ReleaseStreamAndContext,
                NativeCall::ValidateReleaseQuiescence,
            ]
        );

        let mut failed_cancel = InjectedNativeCalls::fail(NativeCall::Cancel);
        let mut lifecycle = StreamLifecycle::establish(&mut failed_cancel).unwrap();
        assert!(lifecycle.teardown(&mut failed_cancel).is_err());
        assert_eq!(
            failed_cancel.calls,
            vec![
                NativeCall::CreateStream,
                NativeCall::Schedule,
                NativeCall::Start,
                NativeCall::Cancel,
                NativeCall::Stop,
                NativeCall::Invalidate,
                NativeCall::Barrier,
                NativeCall::ReleaseStreamAndContext,
                NativeCall::ValidateReleaseQuiescence,
            ]
        );

        let mut failed_barrier = InjectedNativeCalls::fail(NativeCall::Barrier);
        let mut lifecycle = StreamLifecycle::establish(&mut failed_barrier).unwrap();
        assert!(lifecycle.teardown(&mut failed_barrier).is_err());
        assert_eq!(failed_barrier.calls.last(), Some(&NativeCall::Barrier));
        assert_eq!(lifecycle.phase, StreamPhase::Invalidated);

        let mut successful = InjectedNativeCalls::success();
        let mut lifecycle = StreamLifecycle::establish(&mut successful).unwrap();
        assert_eq!(lifecycle.flush(&mut successful).unwrap(), 77);
        lifecycle.teardown(&mut successful).unwrap();
        for call in [
            NativeCall::OwnerDrop,
            NativeCall::QueueDrop,
            NativeCall::KernelQueueDrop,
            NativeCall::DescriptorDrop,
        ] {
            successful.invoke(call).unwrap();
        }
        assert_eq!(
            successful.calls,
            vec![
                NativeCall::CreateStream,
                NativeCall::Schedule,
                NativeCall::Start,
                NativeCall::Flush,
                NativeCall::Cancel,
                NativeCall::Stop,
                NativeCall::Invalidate,
                NativeCall::Barrier,
                NativeCall::ReleaseStreamAndContext,
                NativeCall::ValidateReleaseQuiescence,
                NativeCall::OwnerDrop,
                NativeCall::QueueDrop,
                NativeCall::KernelQueueDrop,
                NativeCall::DescriptorDrop,
            ]
        );
    }

    #[test]
    fn release_validation_rejects_late_callbacks_and_unbalanced_context_ownership() {
        assert!(validate_release_quiescence(4, 4, 2).is_ok());
        assert_eq!(
            validate_release_quiescence(4, 5, 2).unwrap_err(),
            "callback publication occurred after stream release"
        );
        assert_eq!(
            validate_release_quiescence(4, 4, 3).unwrap_err(),
            "FSEvents context retain/release count is unbalanced"
        );
    }

    struct ReleaseThenValidationFailure {
        release_count: usize,
        validation_count: usize,
        retained_owner_count: usize,
    }

    impl NativeCallDriver for ReleaseThenValidationFailure {
        fn invoke(&mut self, call: NativeCall) -> Result<u64, String> {
            match call {
                NativeCall::ReleaseStreamAndContext => {
                    self.release_count += 1;
                    self.retained_owner_count = 2;
                    Ok(1)
                }
                NativeCall::ValidateReleaseQuiescence => {
                    self.validation_count += 1;
                    assert_eq!(self.retained_owner_count, 2);
                    Err("injected post-release validation failure".to_owned())
                }
                _ => Err(format!("unexpected native call: {call:?}")),
            }
        }
    }

    #[test]
    fn successful_native_release_is_terminal_when_post_release_validation_fails() {
        let mut calls = ReleaseThenValidationFailure {
            release_count: 0,
            validation_count: 0,
            retained_owner_count: 0,
        };
        let mut lifecycle = StreamLifecycle {
            phase: StreamPhase::Quiesced,
        };

        assert_eq!(
            lifecycle.teardown(&mut calls).unwrap_err(),
            "injected post-release validation failure"
        );
        assert_eq!(lifecycle.phase, StreamPhase::Released);
        assert_eq!(calls.release_count, 1);
        assert_eq!(calls.validation_count, 1);
        assert_eq!(calls.retained_owner_count, 2);

        lifecycle.teardown(&mut calls).unwrap();
        assert_eq!(calls.release_count, 1);
        assert_eq!(calls.validation_count, 1);
        assert_eq!(calls.retained_owner_count, 2);
    }

    #[test]
    fn cleanup_failures_are_combined_with_primary_failures() {
        assert_eq!(
            combine_primary_cleanup::<()>(
                Err("primary failure".to_owned()),
                Err("cleanup failure".to_owned())
            )
            .unwrap_err(),
            "primary failure; cleanup also failed: cleanup failure"
        );
        assert_eq!(
            combine_primary_cleanup(Ok(4), Err("cleanup failure".to_owned())).unwrap_err(),
            "cleanup failure"
        );
    }

    #[test]
    fn hard_link_rejection_requires_an_exact_two_link_same_inode_observation() {
        let original = PortableStamp {
            device: 3,
            inode: 7,
            length: 10,
            mode: PORTABLE_REGULAR_MODE,
            links: 1,
        };
        let observed = PortableStamp {
            links: 2,
            ..original
        };
        assert!(classify_outside_hard_link(original, observed).unwrap());

        let unrelated = PortableStamp {
            inode: 8,
            ..observed
        };
        assert!(classify_outside_hard_link(original, unrelated).is_err());
    }

    #[test]
    fn build_compiler_identity_is_embedded_not_queried_at_runtime() {
        let identity = build_rustc_identity();
        assert_eq!(identity, env!("ANIMA_CORE_BUILD_RUSTC"));
        assert!(identity.starts_with("rustc "));
        assert!(!identity.trim_start_matches("rustc ").is_empty());
        assert!(!identity.contains('\r') && !identity.contains('\n'));
    }

    #[test]
    fn build_provenance_is_embedded_and_dirty_tracked_builds_are_rejected() {
        use sha2::{Digest, Sha256};

        let argv = vec![
            "--objects".to_owned(),
            "2500".to_owned(),
            "--warmups".to_owned(),
            "30".to_owned(),
        ];
        let build = build_report_from_embedded_provenance(argv.clone());
        assert!(!build.source_commit.is_empty());
        assert!(!build.target_triple.is_empty());
        assert_eq!(build.spike_source.sha256.len(), 64);
        assert!(!build.spike_source.git_blob.is_empty());
        assert_eq!(build.cargo_lock.sha256.len(), 64);
        assert!(!build.cargo_lock.git_blob.is_empty());
        assert_eq!(build.argv, argv);
        assert_eq!(
            build.spike_source.sha256,
            hex::encode(Sha256::digest(include_bytes!(
                "object_lease_macos_spike.rs"
            )))
        );
        let cargo_lock = std::fs::read(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../Cargo.lock"),
        )
        .unwrap();
        assert_eq!(
            build.cargo_lock.sha256,
            hex::encode(Sha256::digest(cargo_lock))
        );
        let runtime_git_query = ["rev", "-parse"].concat();
        assert!(!include_str!("object_lease_macos_spike.rs").contains(&runtime_git_query));
        assert!(validate_build_tracked_tree(true).is_ok());
        assert_eq!(
            validate_build_tracked_tree(false).unwrap_err(),
            "refusing native characterization from a dirty tracked build"
        );
    }

    #[test]
    fn build_script_resolves_relative_git_paths_from_the_git_command_directory() {
        let source = include_str!("../../build.rs");
        let git_watch_setup = source
            .split("for git_path in")
            .nth(1)
            .and_then(|source| source.split("let rustc =").next())
            .expect("build script Git watch setup");

        assert!(git_watch_setup.contains("manifest.join(path)"));
        assert!(!git_watch_setup.contains("repository.join(path)"));
    }

    #[test]
    fn macos_kqueue_uses_zero_timeout_instead_of_file_status_flags() {
        let source = include_str!("object_lease_macos_spike.rs");
        let registration = source
            .split("impl KernelQueue {")
            .nth(1)
            .and_then(|source| source.split("fn poll(&self)").next())
            .expect("macOS kqueue registration");
        let polling = source
            .split("fn poll(&self)")
            .nth(1)
            .and_then(|source| source.split("struct CallbackSnapshot").next())
            .expect("macOS kqueue polling");

        assert!(registration.contains("F_SETFD"));
        assert!(!registration.contains("F_SETFL"));
        assert!(!registration.contains("O_NONBLOCK"));
        assert!(polling.contains("tv_sec: 0"));
        assert!(polling.contains("tv_nsec: 0"));
    }

    #[test]
    fn revoked_descriptor_epochs_cannot_be_reused_after_reattach() {
        let first_mount = MountEpoch::first_attach();
        let first_descriptors = DescriptorEpoch::opened_for(first_mount);
        first_descriptors.require_current(first_mount).unwrap();

        let second_mount = first_mount.next_attach();
        assert_eq!(
            first_descriptors.require_current(second_mount).unwrap_err(),
            "descriptor epoch belongs to a revoked mount"
        );
        DescriptorEpoch::opened_for(second_mount)
            .require_current(second_mount)
            .unwrap();
    }

    struct InjectedArmedRace {
        steps: Vec<ArmedRaceStep>,
        scan_failure: Option<String>,
        kqueue: KqueuePoll,
    }

    impl ArmedRaceDriver for InjectedArmedRace {
        fn arm(&mut self) -> Result<(), String> {
            self.steps.push(ArmedRaceStep::Arm);
            Ok(())
        }

        fn scan_with_mutation(&mut self) -> Result<Option<String>, String> {
            self.steps.push(ArmedRaceStep::Scan);
            self.steps.push(ArmedRaceStep::Mutation);
            Ok(self.scan_failure.take())
        }

        fn terminal_evidence(&mut self) -> Result<FenceOutcome, String> {
            self.steps.push(ArmedRaceStep::TerminalEvidence);
            Ok(FenceOutcome {
                state: GenerationState::Unknown,
                cause: FenceCause::Kqueue,
                kqueue: self.kqueue.clone(),
            })
        }
    }

    #[test]
    fn armed_scan_mutations_require_exact_kqueue_rejection_for_rename_and_revoke() {
        for (case, notes, scan_failure) in [
            (
                RaceCase {
                    component: OwnedComponent::Objects,
                    operation: RaceOperation::RenameRebindRenameBack,
                },
                KQUEUE_NOTE_RENAME,
                None,
            ),
            (
                RaceCase {
                    component: OwnedComponent::Mount,
                    operation: RaceOperation::UnmountRevoke,
                },
                KQUEUE_NOTE_REVOKE,
                Some("scan observed revoked descriptor".to_owned()),
            ),
        ] {
            let mut driver = InjectedArmedRace {
                steps: Vec::new(),
                scan_failure,
                kqueue: KqueuePoll {
                    events: vec![KqueueEventEvidence {
                        ident: 17,
                        component: Some(case.component),
                        notes,
                        has_error: false,
                        has_eof: false,
                    }],
                },
            };
            let rejection = run_armed_race_attempt(&mut driver, case).unwrap();
            assert_eq!(
                driver.steps,
                vec![
                    ArmedRaceStep::Arm,
                    ArmedRaceStep::Scan,
                    ArmedRaceStep::Mutation,
                    ArmedRaceStep::TerminalEvidence,
                ]
            );
            assert_eq!(rejection.proof.case, case);
            assert_eq!(
                rejection.scan_failed,
                case.operation == RaceOperation::UnmountRevoke
            );
        }

        let case = RaceCase {
            component: OwnedComponent::Objects,
            operation: RaceOperation::RenameRebindRenameBack,
        };
        let mut missing_evidence = InjectedArmedRace {
            steps: Vec::new(),
            scan_failure: Some("scan failed".to_owned()),
            kqueue: KqueuePoll::default(),
        };
        assert!(run_armed_race_attempt(&mut missing_evidence, case).is_err());
    }

    struct InjectedSynchronousTeardown {
        callback_queue: bool,
        fail_first: bool,
        complete: bool,
        attempts: usize,
    }

    impl SynchronousTeardownDriver for InjectedSynchronousTeardown {
        type Output = ();

        fn on_callback_queue(&self) -> bool {
            self.callback_queue
        }

        fn teardown_complete(&self) -> bool {
            self.complete
        }

        fn teardown_step(&mut self) -> Result<Self::Output, String> {
            self.attempts += 1;
            if self.fail_first && self.attempts == 1 {
                Err("injected retryable teardown failure".to_owned())
            } else {
                self.complete = true;
                Ok(())
            }
        }
    }

    #[test]
    fn teardown_is_synchronous_and_complete_before_error_return() {
        let mut teardown = InjectedSynchronousTeardown {
            callback_queue: false,
            fail_first: true,
            complete: false,
            attempts: 0,
        };
        assert_eq!(
            run_synchronous_teardown(&mut teardown).unwrap_err(),
            "injected retryable teardown failure"
        );
        assert!(teardown.complete);
        assert_eq!(teardown.attempts, 2);

        let source = include_str!("object_lease_macos_spike.rs");
        let detached_spawn = ["thread", "::spawn"].concat();
        let forbidden_transfer = ["transfer", "_teardown"].concat();
        assert!(!source.contains(&detached_spawn));
        assert!(!source.contains(&forbidden_transfer));
    }

    #[test]
    fn kqueue_proof_preserves_ident_component_and_exact_operation_notes() {
        let delete_case = RaceCase {
            component: OwnedComponent::Objects,
            operation: RaceOperation::DeleteOriginalVnode,
        };
        let poll = KqueuePoll {
            events: vec![
                KqueueEventEvidence {
                    ident: 41,
                    component: Some(OwnedComponent::Objects),
                    notes: KQUEUE_NOTE_RENAME,
                    has_error: false,
                    has_eof: false,
                },
                KqueueEventEvidence {
                    ident: 41,
                    component: Some(OwnedComponent::Objects),
                    notes: KQUEUE_NOTE_DELETE,
                    has_error: false,
                    has_eof: false,
                },
            ],
        };
        let proof = poll.proof_for(delete_case).unwrap();
        assert_eq!(proof.ident, 41);
        assert_eq!(proof.component, OwnedComponent::Objects);
        assert_eq!(proof.notes, KQUEUE_NOTE_DELETE);

        let rename_only = KqueuePoll {
            events: vec![KqueueEventEvidence {
                ident: 41,
                component: Some(OwnedComponent::Objects),
                notes: KQUEUE_NOTE_RENAME,
                has_error: false,
                has_eof: false,
            }],
        };
        assert!(rename_only.proof_for(delete_case).is_none());

        let unmount_case = RaceCase {
            component: OwnedComponent::Mount,
            operation: RaceOperation::UnmountRevoke,
        };
        assert!(KqueuePoll {
            events: vec![KqueueEventEvidence {
                ident: 17,
                component: Some(OwnedComponent::Mount),
                notes: KQUEUE_NOTE_RENAME,
                has_error: false,
                has_eof: false,
            }],
        }
        .proof_for(unmount_case)
        .is_none());
        assert!(KqueuePoll {
            events: vec![KqueueEventEvidence {
                ident: 17,
                component: Some(OwnedComponent::Mount),
                notes: KQUEUE_NOTE_REVOKE,
                has_error: false,
                has_eof: false,
            }],
        }
        .proof_for(unmount_case)
        .is_some());
    }

    #[derive(Default)]
    struct ScriptedFenceDriver {
        cancel_at: Option<FenceCheckpoint>,
        checkpoints: Vec<FenceCheckpoint>,
        polls: std::collections::VecDeque<KqueuePoll>,
        flush_target: u64,
        progress: CallbackProgress,
        waits: std::collections::VecDeque<FenceWait>,
        clock: Duration,
        wait_clock_advance: Duration,
        revalidation_failure: bool,
        unknown_publications: usize,
    }

    impl FenceDriver for ScriptedFenceDriver {
        fn on_callback_queue(&self) -> bool {
            false
        }

        fn cancelled(&mut self, checkpoint: FenceCheckpoint) -> bool {
            self.checkpoints.push(checkpoint);
            self.cancel_at == Some(checkpoint)
        }

        fn poll_kqueue(&mut self) -> Result<KqueuePoll, String> {
            Ok(self.polls.pop_front().unwrap_or_default())
        }

        fn flush_target(&mut self) -> Result<u64, String> {
            Ok(self.flush_target)
        }

        fn callback_progress(&mut self) -> CallbackProgress {
            self.progress
        }

        fn monotonic_now(&mut self) -> Duration {
            self.clock
        }

        fn wait_for_callback_progress(
            &mut self,
            _target: u64,
            _maximum_wait: Duration,
        ) -> Result<FenceWait, String> {
            self.clock = self.clock.saturating_add(self.wait_clock_advance);
            let wait = self.waits.pop_front().unwrap_or(FenceWait::TimedOut);
            if let FenceWait::Progress(progress) = wait {
                self.progress = progress;
            }
            Ok(wait)
        }

        fn revalidate(&mut self) -> Result<(), String> {
            if self.revalidation_failure {
                Err("injected revalidation failure".to_owned())
            } else {
                Ok(())
            }
        }

        fn publish_unknown(&mut self) {
            self.unknown_publications += 1;
            self.progress.generation.publish(GenerationState::Unknown);
        }
    }

    fn clean_fence_driver() -> ScriptedFenceDriver {
        ScriptedFenceDriver {
            polls: [KqueuePoll::default(), KqueuePoll::default()].into(),
            ..ScriptedFenceDriver::default()
        }
    }

    #[test]
    fn real_fence_algorithm_covers_every_cancellation_checkpoint_with_zero_target() {
        for checkpoint in FenceCheckpoint::ALL {
            let mut driver = clean_fence_driver();
            driver.cancel_at = Some(checkpoint);
            let outcome = run_fence(&mut driver).unwrap();
            assert_eq!(outcome.state, GenerationState::Unknown);
            assert_eq!(outcome.cause, FenceCause::Cancelled(checkpoint));
            assert_eq!(driver.unknown_publications, 1);
        }
    }

    #[test]
    fn real_fence_algorithm_exposes_timeout_progress_kqueue_and_revalidation_causes() {
        let mut timeout = clean_fence_driver();
        timeout.flush_target = 9;
        timeout.waits.push_back(FenceWait::TimedOut);
        assert_eq!(run_fence(&mut timeout).unwrap().cause, FenceCause::Timeout);

        let mut timeout_clock = clean_fence_driver();
        timeout_clock.flush_target = 9;
        timeout_clock.wait_clock_advance = FENCE_TIMEOUT;
        timeout_clock
            .waits
            .push_back(FenceWait::Progress(CallbackProgress::default()));
        assert_eq!(
            run_fence(&mut timeout_clock).unwrap().cause,
            FenceCause::Timeout
        );

        let mut cancelled_wait = clean_fence_driver();
        cancelled_wait.flush_target = 9;
        cancelled_wait.waits.push_back(FenceWait::Cancelled);
        assert_eq!(
            run_fence(&mut cancelled_wait).unwrap().cause,
            FenceCause::Cancelled(FenceCheckpoint::WhileWaiting)
        );

        let mut progress = clean_fence_driver();
        progress.flush_target = 9;
        progress
            .waits
            .push_back(FenceWait::Progress(CallbackProgress {
                generation: GenerationState::DirtyAll,
                maximum_event_id: 9,
            }));
        let progress_outcome = run_fence(&mut progress).unwrap();
        assert_eq!(progress_outcome.state, GenerationState::DirtyAll);
        assert_eq!(progress_outcome.cause, FenceCause::Callback);

        let mut kqueue = clean_fence_driver();
        kqueue.polls[0] = KqueuePoll {
            events: vec![KqueueEventEvidence {
                ident: 72,
                component: Some(OwnedComponent::Catalogs),
                notes: KQUEUE_NOTE_RENAME,
                has_error: false,
                has_eof: false,
            }],
        };
        let kqueue_outcome = run_fence(&mut kqueue).unwrap();
        assert_eq!(kqueue_outcome.cause, FenceCause::Kqueue);
        assert_eq!(kqueue_outcome.kqueue.events.len(), 1);

        let mut revalidation = clean_fence_driver();
        revalidation.revalidation_failure = true;
        assert_eq!(
            run_fence(&mut revalidation).unwrap().cause,
            FenceCause::Revalidation
        );
    }

    #[test]
    fn lease_acquisition_retries_one_dirty_scan_but_never_uncertainty() {
        let clean = FenceOutcome {
            state: GenerationState::Clean,
            cause: FenceCause::Clean,
            kqueue: KqueuePoll::default(),
        };
        assert_eq!(
            classify_lease_acquisition(0, &clean),
            LeaseAcquisitionDecision::Accept
        );

        let dirty = FenceOutcome {
            state: GenerationState::DirtyAll,
            cause: FenceCause::Callback,
            kqueue: KqueuePoll::default(),
        };
        assert_eq!(
            classify_lease_acquisition(0, &dirty),
            LeaseAcquisitionDecision::Retry
        );
        assert_eq!(
            classify_lease_acquisition(1, &dirty),
            LeaseAcquisitionDecision::Reject
        );

        for cause in [
            FenceCause::Kqueue,
            FenceCause::Timeout,
            FenceCause::Revalidation,
            FenceCause::KqueueFailure,
            FenceCause::FlushFailure,
            FenceCause::WaitFailure,
        ] {
            let uncertain = FenceOutcome {
                state: GenerationState::Unknown,
                cause,
                kqueue: KqueuePoll::default(),
            };
            assert_eq!(
                classify_lease_acquisition(0, &uncertain),
                LeaseAcquisitionDecision::Reject
            );
        }
    }

    #[test]
    fn native_callback_publication_is_synchronous_and_kqueue_proof_is_independent() {
        #[derive(Default)]
        struct RecordingPublisher {
            publications: std::cell::RefCell<Vec<(GenerationState, Vec<u64>)>>,
        }

        impl NativeCallbackPublisher for RecordingPublisher {
            fn publish_native_batch(&self, state: GenerationState, event_ids: &[u64]) {
                self.publications
                    .borrow_mut()
                    .push((state, event_ids.to_vec()));
            }
        }

        let case = RaceCase {
            component: OwnedComponent::Objects,
            operation: RaceOperation::RenameRebindRenameBack,
        };
        let kqueue = KqueuePoll {
            events: vec![KqueueEventEvidence {
                ident: 8,
                component: Some(OwnedComponent::Objects),
                notes: KQUEUE_NOTE_RENAME,
                has_error: false,
                has_eof: false,
            }],
        };
        let proof = kqueue.proof_for(case).unwrap();
        let publisher = RecordingPublisher::default();

        publish_native_callback_synchronously(&publisher, FSEVENT_ROOT_CHANGED, &[0]);

        assert_eq!(
            *publisher.publications.borrow(),
            vec![(GenerationState::Unknown, vec![0])]
        );
        assert_eq!(kqueue.proof_for(case), Some(proof));
        assert!(KqueuePoll::default().proof_for(case).is_none());
    }

    #[test]
    fn callback_queue_shutdown_rejection_preserves_ownership_for_non_callback_retry() {
        let mut teardown = InjectedSynchronousTeardown {
            callback_queue: true,
            fail_first: false,
            complete: false,
            attempts: 0,
        };
        let rejected = run_synchronous_teardown(&mut teardown);
        assert_eq!(
            rejected.unwrap_err(),
            "shutdown invoked from callback queue"
        );
        assert_eq!(teardown.attempts, 0);
        assert!(!teardown.complete);

        teardown.callback_queue = false;
        run_synchronous_teardown(&mut teardown).unwrap();
        assert_eq!(teardown.attempts, 1);
        assert!(teardown.complete);
    }

    #[test]
    fn barrier_failure_retains_stream_and_context_until_retry_quiesces() {
        let mut calls = InjectedNativeCalls::success();
        let mut lifecycle = StreamLifecycle::establish(&mut calls).unwrap();
        calls.fail_at = Some(NativeCall::Barrier);
        assert!(lifecycle.teardown(&mut calls).is_err());
        assert_ne!(lifecycle.phase, StreamPhase::Released);
        assert_eq!(
            calls
                .calls
                .iter()
                .filter(|call| **call == NativeCall::ReleaseStreamAndContext)
                .count(),
            0
        );

        calls.fail_at = None;
        lifecycle.teardown(&mut calls).unwrap();
        assert_eq!(lifecycle.phase, StreamPhase::Released);
        assert_eq!(
            calls
                .calls
                .iter()
                .filter(|call| **call == NativeCall::ReleaseStreamAndContext)
                .count(),
            1
        );
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum InjectedCleanupFailure {
        NormalDetach,
        ForceDetach,
        RemoveMount,
        RemoveImage,
        RemoveRoot,
    }

    struct InjectedOwnedCleanup {
        attached: bool,
        mount_exists: bool,
        image_exists: bool,
        root_exists: bool,
        failures: Vec<InjectedCleanupFailure>,
        calls: Vec<CleanupOperation>,
    }

    impl OwnedCleanupDriver for InjectedOwnedCleanup {
        fn attached(&self) -> bool {
            self.attached
        }

        fn detach(&mut self, force: bool) -> Result<(), String> {
            self.calls.push(if force {
                CleanupOperation::ForceDetach
            } else {
                CleanupOperation::NormalDetach
            });
            if !force
                && self
                    .failures
                    .contains(&InjectedCleanupFailure::NormalDetach)
            {
                return Err("normal detach failed".to_owned());
            }
            if force && self.failures.contains(&InjectedCleanupFailure::ForceDetach) {
                return Err("force detach failed".to_owned());
            }
            self.attached = false;
            Ok(())
        }

        fn remove_mount(&mut self) -> Result<(), String> {
            self.calls.push(CleanupOperation::RemoveMount);
            if self.failures.contains(&InjectedCleanupFailure::RemoveMount) {
                return Err("mount removal failed".to_owned());
            }
            self.mount_exists = false;
            Ok(())
        }

        fn remove_image(&mut self) -> Result<(), String> {
            self.calls.push(CleanupOperation::RemoveImage);
            if self.failures.contains(&InjectedCleanupFailure::RemoveImage) {
                return Err("image removal failed".to_owned());
            }
            self.image_exists = false;
            Ok(())
        }

        fn remove_root(&mut self) -> Result<(), String> {
            self.calls.push(CleanupOperation::RemoveRoot);
            if self.failures.contains(&InjectedCleanupFailure::RemoveRoot) {
                return Err("root removal failed".to_owned());
            }
            self.root_exists = false;
            Ok(())
        }

        fn residue(&self) -> CleanupResidue {
            CleanupResidue {
                attached: self.attached,
                mount_exists: self.mount_exists,
                image_exists: self.image_exists,
                root_exists: self.root_exists,
            }
        }
    }

    #[test]
    fn cleanup_force_detaches_attempts_every_safe_step_and_reports_all_residue() {
        let mut cleanup = InjectedOwnedCleanup {
            attached: true,
            mount_exists: true,
            image_exists: true,
            root_exists: true,
            failures: vec![
                InjectedCleanupFailure::NormalDetach,
                InjectedCleanupFailure::RemoveMount,
                InjectedCleanupFailure::RemoveImage,
                InjectedCleanupFailure::RemoveRoot,
            ],
            calls: Vec::new(),
        };
        let error = run_owned_cleanup(&mut cleanup).unwrap_err();
        assert_eq!(
            cleanup.calls,
            vec![
                CleanupOperation::NormalDetach,
                CleanupOperation::ForceDetach,
                CleanupOperation::RemoveMount,
                CleanupOperation::RemoveImage,
                CleanupOperation::RemoveRoot,
            ]
        );
        for expected in [
            "normal detach failed",
            "mount removal failed",
            "image removal failed",
            "root removal failed",
            "mount_exists=true",
            "image_exists=true",
            "root_exists=true",
        ] {
            assert!(error.contains(expected), "missing {expected}: {error}");
        }
    }

    #[test]
    fn cleanup_retains_attached_volume_and_skips_unsafe_removals_when_force_detach_fails() {
        let mut cleanup = InjectedOwnedCleanup {
            attached: true,
            mount_exists: true,
            image_exists: true,
            root_exists: true,
            failures: vec![
                InjectedCleanupFailure::NormalDetach,
                InjectedCleanupFailure::ForceDetach,
            ],
            calls: Vec::new(),
        };
        let error = run_owned_cleanup(&mut cleanup).unwrap_err();
        assert_eq!(
            cleanup.calls,
            vec![
                CleanupOperation::NormalDetach,
                CleanupOperation::ForceDetach
            ]
        );
        for expected in [
            "normal detach failed",
            "force detach failed",
            "unsafe removals were skipped",
            "attached=true",
            "mount_exists=true",
            "image_exists=true",
            "root_exists=true",
        ] {
            assert!(error.contains(expected), "missing {expected}: {error}");
        }
    }
}
