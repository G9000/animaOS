use std::fmt;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use super::cache::ValidatedObjectBinding;

pub(super) const MAX_OBJECT_LEASE_ENTRIES: usize = 4_096;
pub(super) const MAX_PROCESS_OBJECT_LEASE_ENTRIES: usize = 4_096;
pub(super) const MAX_PROCESS_OBJECT_LEASES: usize = 4;
pub(super) const MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES: usize = 260;
pub(super) const MAX_MACOS_MONITORED_ANCESTORS: usize = 64;

pub(super) type ObjectSetFingerprint = [u8; 32];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub(super) enum MonitorState {
    Clean = 0,
    DirtyAll = 1,
    Unknown = 2,
}

impl MonitorState {
    fn from_raw(value: u8) -> Self {
        match value {
            0 => Self::Clean,
            1 => Self::DirtyAll,
            _ => Self::Unknown,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum FenceOutcome {
    Clean,
    DirtyAll,
    Unknown,
}

#[derive(Debug)]
pub(super) struct MonitorStateCell {
    state: AtomicU8,
}

impl Default for MonitorStateCell {
    fn default() -> Self {
        Self {
            state: AtomicU8::new(MonitorState::Clean as u8),
        }
    }
}

impl MonitorStateCell {
    pub(super) fn state(&self) -> MonitorState {
        MonitorState::from_raw(self.state.load(Ordering::Acquire))
    }

    pub(super) fn publish(&self, outcome: FenceOutcome) -> MonitorState {
        let next = match outcome {
            FenceOutcome::Clean => return self.state(),
            FenceOutcome::DirtyAll => MonitorState::DirtyAll,
            FenceOutcome::Unknown => MonitorState::Unknown,
        };
        let _ = self.state.compare_exchange(
            MonitorState::Clean as u8,
            next as u8,
            Ordering::AcqRel,
            Ordering::Acquire,
        );
        self.state()
    }
}

pub(super) trait PlatformValidationAnchor: fmt::Debug + Send + Sync {}

#[derive(Debug)]
pub(super) enum ValidationAnchor {
    #[cfg(windows)]
    Windows(Arc<dyn PlatformValidationAnchor>),
    #[cfg(target_os = "macos")]
    Macos(Arc<dyn PlatformValidationAnchor>),
    #[cfg(test)]
    Test(u64),
}

impl ValidationAnchor {
    #[cfg(test)]
    pub(super) fn test(identity: u64) -> Self {
        Self::Test(identity)
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(super) struct LeaseBudgetUsage {
    pub(super) entries: usize,
    pub(super) leases: usize,
    pub(super) monitor_resources: usize,
    pub(super) epoch: u64,
}

#[derive(Debug, Default)]
struct LeaseBudgetState {
    entries: usize,
    leases: usize,
    monitor_resources: usize,
    epoch: u64,
}

#[derive(Debug, Default)]
pub(super) struct LeaseBudget {
    inner: Arc<Mutex<LeaseBudgetState>>,
}

impl LeaseBudget {
    #[cfg(test)]
    pub(super) fn isolated() -> Self {
        Self::default()
    }

    pub(super) fn usage(&self) -> LeaseBudgetUsage {
        let guard = self
            .inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        LeaseBudgetUsage {
            entries: guard.entries,
            leases: guard.leases,
            monitor_resources: guard.monitor_resources,
            epoch: guard.epoch,
        }
    }

    pub(super) fn epoch(&self) -> u64 {
        self.usage().epoch
    }

    pub(super) fn try_reserve_exact(
        &self,
        entries: usize,
        monitor_resources: usize,
    ) -> Option<LeasePermitBundle> {
        if entries > MAX_OBJECT_LEASE_ENTRIES
            || entries > MAX_PROCESS_OBJECT_LEASE_ENTRIES
            || monitor_resources > MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES
        {
            return None;
        }

        {
            let mut guard = self
                .inner
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if guard.leases == MAX_PROCESS_OBJECT_LEASES
                || guard.entries.saturating_add(entries) > MAX_PROCESS_OBJECT_LEASE_ENTRIES
                || guard.monitor_resources.saturating_add(monitor_resources)
                    > MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES
            {
                return None;
            }
            guard.entries += entries;
            guard.leases += 1;
            guard.monitor_resources += monitor_resources;
        }

        let entry_permits = (0..entries)
            .map(|_| {
                Arc::new(EntryPermit {
                    inner: self.inner.clone(),
                })
            })
            .collect();
        let monitor_resource_permits = (0..monitor_resources)
            .map(|_| MonitorResourcePermit {
                inner: self.inner.clone(),
            })
            .collect();
        Some(LeasePermitBundle {
            slot: LeaseSlotPermit {
                inner: self.inner.clone(),
            },
            entry_permits,
            monitor_resource_permits,
        })
    }
}

static LEASE_BUDGET: OnceLock<LeaseBudget> = OnceLock::new();

pub(super) fn global_lease_budget() -> &'static LeaseBudget {
    LEASE_BUDGET.get_or_init(LeaseBudget::default)
}

#[derive(Debug)]
struct LeaseSlotPermit {
    inner: Arc<Mutex<LeaseBudgetState>>,
}

impl Drop for LeaseSlotPermit {
    fn drop(&mut self) {
        release_budget(&self.inner, PermitKind::Lease);
    }
}

#[derive(Debug)]
struct EntryPermit {
    inner: Arc<Mutex<LeaseBudgetState>>,
}

impl Drop for EntryPermit {
    fn drop(&mut self) {
        release_budget(&self.inner, PermitKind::Entry);
    }
}

#[derive(Debug)]
struct MonitorResourcePermit {
    inner: Arc<Mutex<LeaseBudgetState>>,
}

impl Drop for MonitorResourcePermit {
    fn drop(&mut self) {
        release_budget(&self.inner, PermitKind::MonitorResource);
    }
}

#[derive(Clone, Copy)]
enum PermitKind {
    Entry,
    Lease,
    MonitorResource,
}

fn release_budget(inner: &Arc<Mutex<LeaseBudgetState>>, kind: PermitKind) {
    let mut guard = inner
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    match kind {
        PermitKind::Entry => {
            debug_assert!(guard.entries > 0);
            guard.entries = guard.entries.saturating_sub(1);
        }
        PermitKind::Lease => {
            debug_assert!(guard.leases > 0);
            guard.leases = guard.leases.saturating_sub(1);
        }
        PermitKind::MonitorResource => {
            debug_assert!(guard.monitor_resources > 0);
            guard.monitor_resources = guard.monitor_resources.saturating_sub(1);
        }
    }
    guard.epoch = guard.epoch.wrapping_add(1);
}

#[derive(Debug)]
pub(super) struct LeasePermitBundle {
    slot: LeaseSlotPermit,
    entry_permits: Vec<Arc<EntryPermit>>,
    monitor_resource_permits: Vec<MonitorResourcePermit>,
}

pub(super) trait LeaseMonitorResource: fmt::Debug + Send + Sync {
    fn fence(&self) -> FenceOutcome;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum PlatformLeaseSupport {
    Supported,
    Unsupported,
}

pub(super) trait LeaseResourceFactory {
    fn platform_support(&self) -> PlatformLeaseSupport;

    fn create_monitor(
        &self,
        state: Arc<MonitorStateCell>,
    ) -> Result<Box<dyn LeaseMonitorResource>, ()>;

    fn create_anchor(
        &self,
        index: usize,
        binding: &ValidatedObjectBinding,
    ) -> Result<ValidationAnchor, ()>;
}

#[derive(Debug)]
pub(super) struct LeasedObjectBinding {
    pub(super) binding: ValidatedObjectBinding,
    pub(super) anchor: ValidationAnchor,
    _entry_permit: Arc<EntryPermit>,
}

impl LeasedObjectBinding {
    pub(super) fn object_id(&self) -> &crate::id::OpaqueId {
        &self.binding.object_id
    }
}

// Field order is the failure-path teardown order. The live monitor must be gone before any
// process-budget permit can become available to another candidate.
struct PartialLeaseCandidate {
    monitor: Box<dyn LeaseMonitorResource>,
    bindings: Vec<Arc<LeasedObjectBinding>>,
    slot_permit: LeaseSlotPermit,
    monitor_resource_permits: Vec<MonitorResourcePermit>,
    entry_permits: Vec<Arc<EntryPermit>>,
}

pub(super) struct ObjectValidationLease {
    state: Arc<MonitorStateCell>,
    monitor: Box<dyn LeaseMonitorResource>,
    bindings: Vec<Arc<LeasedObjectBinding>>,
    _slot_permit: LeaseSlotPermit,
    _monitor_resource_permits: Vec<MonitorResourcePermit>,
}

impl fmt::Debug for ObjectValidationLease {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ObjectValidationLease")
            .field("state", &self.state())
            .field("binding_count", &self.bindings.len())
            .finish_non_exhaustive()
    }
}

impl ObjectValidationLease {
    pub(super) fn try_acquire(
        budget: &LeaseBudget,
        mut bindings: Vec<ValidatedObjectBinding>,
        monitor_resource_count: usize,
        factory: &dyn LeaseResourceFactory,
    ) -> Result<Arc<Self>, OptimizationMiss> {
        if (!cfg!(any(windows, target_os = "macos")) && !cfg!(test))
            || factory.platform_support() == PlatformLeaseSupport::Unsupported
        {
            return Err(OptimizationMiss::UnsupportedPlatform);
        }
        if bindings.len() > MAX_OBJECT_LEASE_ENTRIES {
            return Err(OptimizationMiss::OverCeiling);
        }

        bindings.sort_unstable_by(|left, right| left.object_id.cmp(&right.object_id));
        if bindings
            .windows(2)
            .any(|pair| pair[0].object_id == pair[1].object_id)
        {
            return Err(OptimizationMiss::TransientAcquisition);
        }

        let leased_bindings = Vec::with_capacity(bindings.len());
        let permits = budget
            .try_reserve_exact(bindings.len(), monitor_resource_count)
            .ok_or(OptimizationMiss::BudgetDenied)?;
        let state = Arc::new(MonitorStateCell::default());
        let monitor = factory
            .create_monitor(state.clone())
            .map_err(|_| OptimizationMiss::TransientAcquisition)?;

        let LeasePermitBundle {
            slot,
            entry_permits,
            monitor_resource_permits,
        } = permits;
        let mut candidate = PartialLeaseCandidate {
            monitor,
            bindings: leased_bindings,
            slot_permit: slot,
            monitor_resource_permits,
            entry_permits,
        };
        for (index, binding) in bindings.into_iter().enumerate() {
            let anchor = factory
                .create_anchor(index, &binding)
                .map_err(|_| OptimizationMiss::TransientAcquisition)?;
            let entry_permit = candidate
                .entry_permits
                .get(index)
                .expect("exact lease reservation must provide one permit per binding")
                .clone();
            candidate.bindings.push(Arc::new(LeasedObjectBinding {
                binding,
                anchor,
                _entry_permit: entry_permit,
            }));
        }
        candidate.entry_permits.clear();

        Ok(Arc::new(Self {
            state,
            monitor: candidate.monitor,
            bindings: candidate.bindings,
            _slot_permit: candidate.slot_permit,
            _monitor_resource_permits: candidate.monitor_resource_permits,
        }))
    }

    pub(super) fn state(&self) -> MonitorState {
        self.state.state()
    }

    pub(super) fn publish_fence(&self, outcome: FenceOutcome) -> MonitorState {
        self.state.publish(outcome)
    }

    pub(super) fn fence(&self) -> MonitorState {
        self.publish_fence(self.monitor.fence())
    }

    pub(super) fn publish_dirty(&self) {
        self.publish_fence(FenceOutcome::DirtyAll);
    }

    pub(super) fn publish_unknown(&self) {
        self.publish_fence(FenceOutcome::Unknown);
    }

    pub(super) fn bindings(&self) -> &[Arc<LeasedObjectBinding>] {
        &self.bindings
    }

    pub(super) fn monitor(&self) -> &dyn LeaseMonitorResource {
        self.monitor.as_ref()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum OptimizationMiss {
    UnsupportedPlatform,
    OverCeiling,
    BudgetDenied,
    TransientBackoff,
    TransientAcquisition,
}

pub(super) trait MonotonicClock: Send + Sync {
    fn now(&self) -> Duration;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct BudgetDenialKey {
    epoch: u64,
    object_set: ObjectSetFingerprint,
    requested_count: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum LeaseAttemptDecision {
    Eligible,
    OverCeiling,
    BudgetDeniedSuppressed,
    TransientBackoff,
}

pub(super) struct LeaseAttemptPolicy {
    clock: Arc<dyn MonotonicClock>,
    budget_denial: Option<BudgetDenialKey>,
    transient_retry_at: Option<Duration>,
    next_transient_delay: Duration,
}

impl LeaseAttemptPolicy {
    pub(super) fn new(clock: Arc<dyn MonotonicClock>) -> Self {
        Self {
            clock,
            budget_denial: None,
            transient_retry_at: None,
            next_transient_delay: Duration::from_secs(1),
        }
    }

    pub(super) fn decision(
        &self,
        object_set: ObjectSetFingerprint,
        requested_count: usize,
        budget_epoch: u64,
    ) -> LeaseAttemptDecision {
        if requested_count > MAX_OBJECT_LEASE_ENTRIES {
            return LeaseAttemptDecision::OverCeiling;
        }
        if self
            .transient_retry_at
            .is_some_and(|retry_at| self.clock.now() < retry_at)
        {
            return LeaseAttemptDecision::TransientBackoff;
        }
        if self.budget_denial
            == Some(BudgetDenialKey {
                epoch: budget_epoch,
                object_set,
                requested_count,
            })
        {
            return LeaseAttemptDecision::BudgetDeniedSuppressed;
        }
        LeaseAttemptDecision::Eligible
    }

    pub(super) fn record_budget_denial(
        &mut self,
        object_set: ObjectSetFingerprint,
        requested_count: usize,
        budget_epoch: u64,
    ) {
        self.budget_denial = Some(BudgetDenialKey {
            epoch: budget_epoch,
            object_set,
            requested_count,
        });
    }

    pub(super) fn record_transient_failure(&mut self) {
        self.transient_retry_at = Some(self.clock.now() + self.next_transient_delay);
        let doubled = self.next_transient_delay.as_secs().saturating_mul(2);
        self.next_transient_delay = Duration::from_secs(doubled.min(60));
    }

    pub(super) fn record_success(&mut self) {
        self.budget_denial = None;
        self.transient_retry_at = None;
        self.next_transient_delay = Duration::from_secs(1);
    }
}
