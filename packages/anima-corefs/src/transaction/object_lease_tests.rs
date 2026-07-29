use std::collections::VecDeque;
use std::fmt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, TryLockError, Weak};
use std::time::Duration;

use crate::catalog::{
    CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, ContentHash, ObjectPhysicalName,
    WrappedObjectDekRecord,
};
use crate::crypto::{
    derive_corefs_subkeys, ObjectKind, SecretBytes, OBJECT_KEY_ENVELOPE_VERSION,
    OBJECT_WRAP_ALGORITHM,
};
use crate::folders::{FolderOwner, PortableName};
use crate::id::OpaqueId;
use crate::policy::AnimaAccess;
use crate::rotation::FrkKeyring;

use super::cache::{
    AuthenticatedCommitSnapshot, CacheLookupKey, CommitCache, PointerSet, ValidatedObjectBinding,
    ValidatedObjectState,
};
#[cfg(windows)]
use super::object_lease::ObjectLeaseDiagnosticObserver;
use super::object_lease::{
    global_lease_budget, DirectoryIdentity, FenceOutcome, LeaseAttemptDecision, LeaseAttemptPolicy,
    LeaseBudget, LeaseBudgetUsage, LeaseMonitorResource, LeaseResourceFactory, LeaseResourcePlan,
    MonitorState, MonitorStateCell, MonotonicClock, ObjectSetFingerprint, ObjectValidationLease,
    OptimizationMiss, ValidationAnchor, MAX_OBJECT_LEASE_ENTRIES, MAX_PROCESS_OBJECT_LEASES,
    MAX_PROCESS_OBJECT_LEASE_ENTRIES, MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES,
};
use super::{CommitError, CoreCommitLock};

const CORE_ID: &str = "object-lease-test-core";
const ROOT_ID: &str = "01J00000000000000000000000";

#[derive(Default)]
struct TestClock {
    now_seconds: AtomicU64,
}

impl TestClock {
    fn advance(&self, seconds: u64) {
        self.now_seconds.fetch_add(seconds, Ordering::SeqCst);
    }
}

impl MonotonicClock for TestClock {
    fn now(&self) -> Duration {
        Duration::from_secs(self.now_seconds.load(Ordering::SeqCst))
    }
}

#[derive(Clone, Debug)]
struct BudgetDropProbe {
    budget: Arc<LeaseBudget>,
    observed: Arc<Mutex<Option<LeaseBudgetUsage>>>,
}

impl BudgetDropProbe {
    fn new(budget: Arc<LeaseBudget>) -> Self {
        Self {
            budget,
            observed: Arc::new(Mutex::new(None)),
        }
    }

    fn record(&self) {
        *self.observed.lock().unwrap() = Some(self.budget.usage());
    }

    fn observed(&self) -> Option<LeaseBudgetUsage> {
        *self.observed.lock().unwrap()
    }
}

#[derive(Clone, Debug)]
pub(super) struct KernelLockDropProbe {
    root: PathBuf,
    drops_after_unlock: Arc<AtomicUsize>,
    drops_while_locked: Arc<AtomicUsize>,
}

impl KernelLockDropProbe {
    pub(super) fn new(root: PathBuf) -> Self {
        Self {
            root,
            drops_after_unlock: Arc::new(AtomicUsize::new(0)),
            drops_while_locked: Arc::new(AtomicUsize::new(0)),
        }
    }

    pub(super) fn record(&self) {
        match CoreCommitLock::acquire(&self.root) {
            Ok(lock) => {
                self.drops_after_unlock.fetch_add(1, Ordering::SeqCst);
                drop(lock);
            }
            Err(CommitError::LockBusy | CommitError::RecordedOwnerAlive { .. }) => {
                self.drops_while_locked.fetch_add(1, Ordering::SeqCst);
            }
            Err(error) => panic!("monitor teardown could not probe CoreCommitLock: {error}"),
        }
    }

    pub(super) fn drops_after_unlock(&self) -> usize {
        self.drops_after_unlock.load(Ordering::SeqCst)
    }

    pub(super) fn drops_while_locked(&self) -> usize {
        self.drops_while_locked.load(Ordering::SeqCst)
    }
}

struct TestMonitorResource {
    drops: Arc<AtomicUsize>,
    cache: Option<Weak<CommitCache>>,
    dropped_outside_cache_lock: Option<Arc<AtomicUsize>>,
    budget_at_drop: Option<BudgetDropProbe>,
    kernel_lock_at_drop: Option<KernelLockDropProbe>,
}

impl fmt::Debug for TestMonitorResource {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.debug_struct("TestMonitorResource").finish()
    }
}

impl Drop for TestMonitorResource {
    fn drop(&mut self) {
        self.drops.fetch_add(1, Ordering::SeqCst);
        if let (Some(cache), Some(probe)) = (
            self.cache.as_ref().and_then(Weak::upgrade),
            &self.dropped_outside_cache_lock,
        ) {
            match cache.inner.try_lock() {
                Ok(_guard) => {
                    probe.fetch_add(1, Ordering::SeqCst);
                }
                Err(TryLockError::Poisoned(_poisoned)) => {
                    probe.fetch_add(1, Ordering::SeqCst);
                }
                Err(TryLockError::WouldBlock) => {}
            }
        }
        if let Some(probe) = &self.budget_at_drop {
            probe.record();
        }
        if let Some(probe) = &self.kernel_lock_at_drop {
            probe.record();
        }
    }
}

impl LeaseMonitorResource for TestMonitorResource {
    fn fence(&self) -> FenceOutcome {
        FenceOutcome::Clean
    }
}

#[derive(Debug)]
pub(super) struct TestFactory {
    platform_supported: bool,
    planned_monitor_resources: AtomicUsize,
    fail_anchor_at: Option<usize>,
    monitor_attempts: AtomicUsize,
    monitor_plan_resources: AtomicUsize,
    anchor_attempts: AtomicUsize,
    anchor_queries: Option<Arc<AtomicUsize>>,
    monitor_drops: Arc<AtomicUsize>,
    cache: Option<Weak<CommitCache>>,
    dropped_outside_cache_lock: Option<Arc<AtomicUsize>>,
    budget_at_monitor_drop: Option<BudgetDropProbe>,
    kernel_lock_at_monitor_drop: Option<KernelLockDropProbe>,
}

impl TestFactory {
    pub(super) fn successful() -> Self {
        Self {
            platform_supported: true,
            planned_monitor_resources: AtomicUsize::new(0),
            fail_anchor_at: None,
            monitor_attempts: AtomicUsize::new(0),
            monitor_plan_resources: AtomicUsize::new(usize::MAX),
            anchor_attempts: AtomicUsize::new(0),
            anchor_queries: None,
            monitor_drops: Arc::new(AtomicUsize::new(0)),
            cache: None,
            dropped_outside_cache_lock: None,
            budget_at_monitor_drop: None,
            kernel_lock_at_monitor_drop: None,
        }
    }

    fn unsupported() -> Self {
        Self {
            platform_supported: false,
            ..Self::successful()
        }
    }

    fn with_monitor_resources(mut self, monitor_resources: usize) -> Self {
        *self.planned_monitor_resources.get_mut() = monitor_resources;
        self
    }

    fn with_anchor_queries(mut self, queries: Arc<AtomicUsize>) -> Self {
        self.anchor_queries = Some(queries);
        self
    }

    pub(super) fn with_kernel_lock_drop_probe(mut self, probe: KernelLockDropProbe) -> Self {
        self.kernel_lock_at_monitor_drop = Some(probe);
        self
    }
}

impl LeaseResourceFactory for TestFactory {
    fn resource_plan(&self) -> LeaseResourcePlan {
        if self.platform_supported {
            LeaseResourcePlan::supported(self.planned_monitor_resources.load(Ordering::SeqCst))
        } else {
            LeaseResourcePlan::unsupported()
        }
    }

    fn create_monitor(
        &self,
        plan: LeaseResourcePlan,
        _state: Arc<MonitorStateCell>,
    ) -> Result<Box<dyn LeaseMonitorResource>, ()> {
        self.monitor_attempts.fetch_add(1, Ordering::SeqCst);
        self.monitor_plan_resources
            .store(plan.monitor_resource_count(), Ordering::SeqCst);
        Ok(Box::new(TestMonitorResource {
            drops: self.monitor_drops.clone(),
            cache: self.cache.clone(),
            dropped_outside_cache_lock: self.dropped_outside_cache_lock.clone(),
            budget_at_drop: self.budget_at_monitor_drop.clone(),
            kernel_lock_at_drop: self.kernel_lock_at_monitor_drop.clone(),
        }))
    }

    fn create_anchor(
        &self,
        index: usize,
        _binding: &ValidatedObjectBinding,
    ) -> Result<ValidationAnchor, ()> {
        self.anchor_attempts.fetch_add(1, Ordering::SeqCst);
        if self.fail_anchor_at == Some(index) {
            Err(())
        } else if let Some(queries) = &self.anchor_queries {
            Ok(ValidationAnchor::test_observed(
                index as u64,
                queries.clone(),
                Arc::new(Mutex::new(VecDeque::new())),
            ))
        } else {
            Ok(ValidationAnchor::test(index as u64))
        }
    }
}

fn binding(index: usize) -> ValidatedObjectBinding {
    let fill = u8::try_from(index % 251 + 1).unwrap();
    ValidatedObjectBinding {
        object_id: OpaqueId::parse(&format!("01J{index:023}")).unwrap(),
        revision: index as u64 + 1,
        object_key_epoch: 1,
        physical_name: ObjectPhysicalName::parse(&format!(
            "object-{}.acore",
            format!("{fill:02x}").repeat(16)
        ))
        .unwrap(),
        content_hash: ContentHash::parse(&format!("{fill:02x}").repeat(32)).unwrap(),
        kind: ObjectKind::Note,
        wrapped_dek: WrappedObjectDekRecord::from_parts(
            1,
            1,
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[fill; 12],
            vec![fill; 48],
        )
        .unwrap(),
        binding_digest: [fill; 32],
    }
}

fn bindings(count: usize) -> Vec<ValidatedObjectBinding> {
    (0..count).map(binding).collect()
}

fn acquire(
    budget: &LeaseBudget,
    count: usize,
    monitor_resources: usize,
    factory: &TestFactory,
) -> Result<Arc<ObjectValidationLease>, OptimizationMiss> {
    factory
        .planned_monitor_resources
        .store(monitor_resources, Ordering::SeqCst);
    ObjectValidationLease::try_acquire_with_budget(budget, bindings(count), factory)
}

fn assert_usage(budget: &LeaseBudget, expected: LeaseBudgetUsage) {
    assert_eq!(budget.usage(), expected);
}

fn fingerprint(fill: u8) -> ObjectSetFingerprint {
    [fill; 32]
}

fn snapshot_with_lease(lease: Arc<ObjectValidationLease>) -> Arc<AuthenticatedCommitSnapshot> {
    let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x71; 32]).unwrap(), 1).unwrap();
    let keyring = FrkKeyring::single(&keys);
    let key = CacheLookupKey::derive(PointerSet::default(), CORE_ID, &keyring, &keys).unwrap();
    let catalog = Arc::new(
        CatalogGeneration::new(
            1,
            vec![CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                OpaqueId::parse(ROOT_ID).unwrap(),
                None,
                PortableName::parse("Core").unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            ))],
        )
        .unwrap(),
    );
    Arc::new(
        AuthenticatedCommitSnapshot::new(
            &key,
            catalog,
            Some(Arc::new(ValidatedObjectState::empty())),
        )
        .with_object_lease(Some(lease)),
    )
}

#[test]
fn lease_state_is_terminal_after_dirty_or_unknown() {
    let budget = LeaseBudget::isolated();
    let dirty = acquire(&budget, 0, 1, &TestFactory::successful()).unwrap();
    assert_eq!(dirty.state(), MonitorState::Clean);
    assert_eq!(dirty.fence(), MonitorState::Clean);
    dirty.publish_dirty();
    dirty.publish_unknown();
    assert_eq!(dirty.state(), MonitorState::DirtyAll);

    let unknown = acquire(&budget, 0, 1, &TestFactory::successful()).unwrap();
    assert_eq!(unknown.state(), MonitorState::Clean);
    unknown.publish_unknown();
    unknown.publish_dirty();
    assert_eq!(unknown.state(), MonitorState::Unknown);
}

#[test]
fn clean_lease_rejects_every_catalog_object_tuple_change() {
    let budget = LeaseBudget::isolated();
    let expected = bindings(1);
    let lease = acquire(&budget, 1, 1, &TestFactory::successful()).unwrap();
    assert!(lease.matches_object_tuple(&expected));

    let mut cases = Vec::new();
    let mut changed = expected[0].clone();
    changed.revision += 1;
    cases.push(changed);
    let mut changed = expected[0].clone();
    changed.object_key_epoch += 1;
    cases.push(changed);
    let mut changed = expected[0].clone();
    changed.physical_name =
        ObjectPhysicalName::parse("object-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.acore").unwrap();
    cases.push(changed);
    let mut changed = expected[0].clone();
    changed.content_hash = ContentHash::parse(&"aa".repeat(32)).unwrap();
    cases.push(changed);
    let mut changed = expected[0].clone();
    changed.kind = ObjectKind::Task;
    cases.push(changed);
    let mut changed = expected[0].clone();
    changed.wrapped_dek = WrappedObjectDekRecord::from_parts(
        2,
        changed.object_key_epoch,
        OBJECT_WRAP_ALGORITHM,
        OBJECT_KEY_ENVELOPE_VERSION,
        &[0xa5; 12],
        vec![0xa5; 48],
    )
    .unwrap();
    cases.push(changed);
    let mut changed = expected[0].clone();
    changed.binding_digest = [0xa6; 32];
    cases.push(changed);

    for changed in cases {
        assert!(!lease.matches_object_tuple(&[changed]));
    }
}

#[test]
fn clean_lease_directory_identity_mismatch_is_terminal_unknown() {
    let budget = LeaseBudget::isolated();
    let expected = bindings(1);
    let lease = acquire(&budget, 1, 1, &TestFactory::successful()).unwrap();

    assert!(lease
        .try_carry_forward(
            &expected,
            DirectoryIdentity {
                device: 1,
                inode: 1,
            },
        )
        .is_none());
    assert_eq!(lease.state(), MonitorState::Unknown);
}

#[test]
fn lease_carry_forward_2500_bindings_keeps_same_arc_and_exact_permits() {
    let budget = LeaseBudget::isolated();
    let expected = bindings(2_500);
    let mut lease = acquire(&budget, 2_500, 3, &TestFactory::successful()).unwrap();

    for _ in 0..4 {
        let next = lease
            .try_carry_forward(&expected, DirectoryIdentity::default())
            .expect("unchanged exact lease must carry forward");
        assert!(Arc::ptr_eq(&lease, &next));
        lease = next;
        assert_eq!(
            budget.usage(),
            LeaseBudgetUsage {
                entries: 2_500,
                leases: 1,
                monitor_resources: 3,
                epoch: 0,
            }
        );
    }
}

#[test]
fn exact_budget_reservation_is_atomic_and_raii_released() {
    let budget = LeaseBudget::isolated();
    let lease = acquire(&budget, 7, 3, &TestFactory::successful()).unwrap();
    assert_usage(
        &budget,
        LeaseBudgetUsage {
            entries: 7,
            leases: 1,
            monitor_resources: 3,
            epoch: 0,
        },
    );

    let denied = budget.try_reserve_exact(
        MAX_PROCESS_OBJECT_LEASE_ENTRIES,
        MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES,
    );
    assert!(denied.is_none());
    assert_eq!(budget.usage().entries, 7);
    assert_eq!(budget.usage().leases, 1);
    assert_eq!(budget.usage().monitor_resources, 3);

    drop(lease);
    let released = budget.usage();
    assert_eq!(released.entries, 0);
    assert_eq!(released.leases, 0);
    assert_eq!(released.monitor_resources, 0);
    assert!(released.epoch > 0);
}

#[test]
fn budget_enforces_4096_entries_four_leases_and_260_monitor_resources() {
    let entry_budget = LeaseBudget::isolated();
    let entries = entry_budget
        .try_reserve_exact(MAX_PROCESS_OBJECT_LEASE_ENTRIES, 0)
        .unwrap();
    assert!(entry_budget.try_reserve_exact(1, 0).is_none());
    drop(entries);

    let lease_budget = LeaseBudget::isolated();
    let leases: Vec<_> = (0..MAX_PROCESS_OBJECT_LEASES)
        .map(|_| lease_budget.try_reserve_exact(0, 0).unwrap())
        .collect();
    assert!(lease_budget.try_reserve_exact(0, 0).is_none());
    drop(leases);

    let monitor_budget = LeaseBudget::isolated();
    let monitors = monitor_budget
        .try_reserve_exact(0, MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES)
        .unwrap();
    assert!(monitor_budget.try_reserve_exact(0, 1).is_none());
    drop(monitors);

    assert!(entry_budget
        .try_reserve_exact(MAX_OBJECT_LEASE_ENTRIES + 1, 0)
        .is_none());
    let _ = global_lease_budget();
}

#[test]
fn entry_and_monitor_permits_are_not_double_counted_when_arc_is_shared() {
    let budget = LeaseBudget::isolated();
    let lease = acquire(&budget, 1, 1, &TestFactory::successful()).unwrap();
    let shared_binding = lease.bindings()[0].clone();
    let shared_lease = lease.clone();
    assert_eq!(budget.usage().entries, 1);
    assert_eq!(budget.usage().leases, 1);
    assert_eq!(budget.usage().monitor_resources, 1);

    drop(shared_lease);
    drop(lease);
    assert_eq!(budget.usage().entries, 1);
    assert_eq!(budget.usage().leases, 0);
    assert_eq!(budget.usage().monitor_resources, 0);

    drop(shared_binding);
    assert_eq!(budget.usage().entries, 0);
}

#[test]
fn budget_denial_retries_only_after_epoch_or_object_set_change() {
    let clock = Arc::new(TestClock::default());
    let mut policy = LeaseAttemptPolicy::new(clock);
    let epoch = 7;

    assert_eq!(
        policy.decision(fingerprint(1), 2_500, epoch),
        LeaseAttemptDecision::Eligible
    );
    policy.record_budget_denial(fingerprint(1), 2_500, epoch);
    assert_eq!(
        policy.decision(fingerprint(1), 2_500, epoch),
        LeaseAttemptDecision::BudgetDeniedSuppressed
    );
    assert_eq!(
        policy.decision(fingerprint(2), 2_500, epoch),
        LeaseAttemptDecision::Eligible
    );
    assert_eq!(
        policy.decision(fingerprint(1), 2_499, epoch),
        LeaseAttemptDecision::Eligible
    );
    assert_eq!(
        policy.decision(fingerprint(1), 2_500, epoch + 1),
        LeaseAttemptDecision::Eligible
    );
}

#[test]
fn generation_only_pointer_change_keeps_same_denial_suppressed() {
    let clock = Arc::new(TestClock::default());
    let mut policy = LeaseAttemptPolicy::new(clock);
    let object_set = fingerprint(9);
    policy.record_budget_denial(object_set, 2_500, 11);

    let generation_before = 40;
    let generation_after = 41;
    assert_ne!(generation_before, generation_after);
    assert_eq!(
        policy.decision(object_set, 2_500, 11),
        LeaseAttemptDecision::BudgetDeniedSuppressed
    );
}

#[test]
fn transient_failure_backoff_runs_from_one_to_sixty_seconds() {
    let clock = Arc::new(TestClock::default());
    let mut policy = LeaseAttemptPolicy::new(clock.clone());
    let expected = [1, 2, 4, 8, 16, 32, 60, 60];

    for delay in expected {
        policy.record_transient_failure();
        assert_eq!(
            policy.decision(fingerprint(3), 1, 0),
            LeaseAttemptDecision::TransientBackoff
        );
        clock.advance(delay - 1);
        assert_eq!(
            policy.decision(fingerprint(3), 1, 0),
            LeaseAttemptDecision::TransientBackoff
        );
        clock.advance(1);
        assert_eq!(
            policy.decision(fingerprint(3), 1, 0),
            LeaseAttemptDecision::Eligible
        );
    }

    policy.record_success();
    policy.record_transient_failure();
    clock.advance(1);
    assert_eq!(
        policy.decision(fingerprint(3), 1, 0),
        LeaseAttemptDecision::Eligible
    );
}

#[test]
fn catalog_counts_4096_and_4097_select_eligible_and_fallback() {
    let clock = Arc::new(TestClock::default());
    let policy = LeaseAttemptPolicy::new(clock);
    assert_eq!(
        policy.decision(fingerprint(4), MAX_OBJECT_LEASE_ENTRIES, 0),
        LeaseAttemptDecision::Eligible
    );
    assert_eq!(
        policy.decision(fingerprint(4), MAX_OBJECT_LEASE_ENTRIES + 1, 0),
        LeaseAttemptDecision::OverCeiling
    );
}

#[test]
fn partial_candidate_failure_releases_every_permit() {
    let budget = Arc::new(LeaseBudget::isolated());
    let budget_at_monitor_drop = BudgetDropProbe::new(budget.clone());
    let factory = TestFactory {
        fail_anchor_at: Some(2),
        budget_at_monitor_drop: Some(budget_at_monitor_drop.clone()),
        ..TestFactory::successful()
    };
    assert_eq!(
        acquire(budget.as_ref(), 5, 3, &factory).unwrap_err(),
        OptimizationMiss::TransientAcquisition
    );
    assert_eq!(factory.anchor_attempts.load(Ordering::SeqCst), 3);
    assert_eq!(factory.monitor_drops.load(Ordering::SeqCst), 1);
    assert_eq!(
        budget_at_monitor_drop.observed(),
        Some(LeaseBudgetUsage {
            entries: 5,
            leases: 1,
            monitor_resources: 3,
            epoch: 0,
        }),
        "the live monitor must be destroyed before any permit becomes available"
    );
    let usage = budget.usage();
    assert_eq!(usage.entries, 0);
    assert_eq!(usage.leases, 0);
    assert_eq!(usage.monitor_resources, 0);
    assert!(usage.epoch > 0);
}

#[test]
fn unsupported_platform_returns_fallback_without_acquiring_resources() {
    let budget = LeaseBudget::isolated();
    let factory = TestFactory::unsupported();

    let result = acquire(&budget, 3, 1, &factory);

    assert!(matches!(result, Err(OptimizationMiss::UnsupportedPlatform)));
    assert_eq!(factory.monitor_attempts.load(Ordering::SeqCst), 0);
    assert_eq!(factory.anchor_attempts.load(Ordering::SeqCst), 0);
    assert_eq!(factory.monitor_drops.load(Ordering::SeqCst), 0);
    assert_eq!(budget.usage(), LeaseBudgetUsage::default());
}

#[test]
fn unsupported_validation_anchor_is_fail_closed() {
    assert_eq!(
        ValidationAnchor::Unsupported.validate(),
        FenceOutcome::Unknown
    );
}

#[test]
fn terminal_state_between_fences_skips_all_remaining_metadata_queries() {
    let budget = LeaseBudget::isolated();
    let queries = Arc::new(AtomicUsize::new(0));
    let factory = TestFactory::successful().with_anchor_queries(queries.clone());
    let lease = acquire(&budget, MAX_OBJECT_LEASE_ENTRIES, 1, &factory).unwrap();

    assert_eq!(
        lease.fence_with_validation_hook_for_test(|| lease.publish_dirty()),
        MonitorState::DirtyAll
    );
    assert_eq!(
        queries.load(Ordering::SeqCst),
        0,
        "terminal monitor state must fall back before any remaining metadata query"
    );
}

#[test]
fn production_acquisition_uses_singleton_budget_and_factory_resource_plan() {
    let budget = global_lease_budget();
    let before = budget.usage();
    assert_eq!(before.entries, 0);
    assert_eq!(before.leases, 0);
    assert_eq!(before.monitor_resources, 0);
    let factory = TestFactory::successful().with_monitor_resources(3);

    let lease = ObjectValidationLease::try_acquire(bindings(2), &factory).unwrap();

    let acquired = budget.usage();
    assert_eq!(acquired.entries, 2);
    assert_eq!(acquired.leases, 1);
    assert_eq!(acquired.monitor_resources, 3);
    assert_eq!(
        factory.monitor_plan_resources.load(Ordering::SeqCst),
        3,
        "monitor construction must receive the immutable preflight plan"
    );
    drop(lease);
    let released = budget.usage();
    assert_eq!(released.entries, 0);
    assert_eq!(released.leases, 0);
    assert_eq!(released.monitor_resources, 0);
    assert!(released.epoch > before.epoch);
}

#[test]
fn completed_lease_destroys_monitor_before_cache_releases_permits() {
    let budget = Arc::new(LeaseBudget::isolated());
    let budget_at_monitor_drop = BudgetDropProbe::new(budget.clone());
    let factory = TestFactory {
        budget_at_monitor_drop: Some(budget_at_monitor_drop.clone()),
        ..TestFactory::successful()
    };
    let lease = acquire(budget.as_ref(), 2, 2, &factory).unwrap();
    let cache = CommitCache::default();
    cache.replace(snapshot_with_lease(lease));

    cache.clear();

    assert_eq!(
        budget_at_monitor_drop.observed(),
        Some(LeaseBudgetUsage {
            entries: 2,
            leases: 1,
            monitor_resources: 2,
            epoch: 0,
        }),
        "cache clear must destroy the completed monitor before releasing any permit"
    );
    let released = budget.usage();
    assert_eq!(released.entries, 0);
    assert_eq!(released.leases, 0);
    assert_eq!(released.monitor_resources, 0);
    assert!(released.epoch > 0);
}

#[test]
fn lease_failure_cache_poison_discards_lease_resources_outside_its_mutex() {
    for action in ["clear", "replace", "poison-recover", "drop-lease-poison"] {
        let cache = Arc::new(CommitCache::default());
        let dropped_outside = Arc::new(AtomicUsize::new(0));
        let factory = TestFactory {
            cache: Some(Arc::downgrade(&cache)),
            dropped_outside_cache_lock: Some(dropped_outside.clone()),
            ..TestFactory::successful()
        };
        let lease = acquire(&LeaseBudget::isolated(), 0, 1, &factory).unwrap();
        cache.replace(snapshot_with_lease(lease));

        match action {
            "clear" => cache.clear(),
            "replace" => {
                let replacement =
                    acquire(&LeaseBudget::isolated(), 0, 0, &TestFactory::successful()).unwrap();
                cache.replace(snapshot_with_lease(replacement));
            }
            "poison-recover" => {
                let poisoned_cache = cache.clone();
                let _ = std::panic::catch_unwind(move || {
                    let _guard = poisoned_cache.inner.lock().unwrap();
                    panic!("poison cache for recovery test");
                });
                assert!(cache.current().is_none());
            }
            "drop-lease-poison" => {
                let poisoned_cache = cache.clone();
                let _ = std::panic::catch_unwind(move || {
                    let _guard = poisoned_cache.inner.lock().unwrap();
                    panic!("poison cache before lease-only drop");
                });
                cache.drop_object_lease();
                assert!(
                    cache.current().is_none(),
                    "lease-only poison recovery retained unauthenticated cache state"
                );
            }
            _ => unreachable!(),
        }

        assert_eq!(
            dropped_outside.load(Ordering::SeqCst),
            1,
            "{action} dropped a lease resource while cache.inner was held"
        );
    }
}

#[cfg(windows)]
mod windows_object_lease_tests {
    use std::fs;
    use std::io::Write;
    use std::os::windows::fs::OpenOptionsExt as _;
    use std::sync::Arc;

    use cap_std::ambient_authority;
    use cap_std::fs::Dir;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
    };

    use super::*;
    use crate::transaction::object_lease::windows::{
        notification_outcome_for_test, probe_name_for_test, BoundarySnapshotForTest,
        ConstructionEventForTest, RetainedValidationAnchor, TestNotification, WindowsLeaseFactory,
        WindowsLeaseTestControl, WorkerFaultForTest,
    };

    fn monitored_lease(
        label: &str,
    ) -> (
        std::path::PathBuf,
        Arc<ObjectValidationLease>,
        Arc<LeaseBudget>,
    ) {
        let (root, lease, budget, _control) = monitored_lease_with_control(label);
        (root, lease, budget)
    }

    fn monitored_lease_with_control(
        label: &str,
    ) -> (
        std::path::PathBuf,
        Arc<ObjectValidationLease>,
        Arc<LeaseBudget>,
        WindowsLeaseTestControl,
    ) {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-windows-object-lease-{label}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let object = binding(0);
        fs::write(root.join(object.physical_name.as_str()), b"ciphertext").unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        let control = WindowsLeaseTestControl::new();
        let factory = WindowsLeaseFactory::new_for_test(dir, control.clone()).unwrap();
        let budget = Arc::new(LeaseBudget::isolated());
        let lease = ObjectValidationLease::try_acquire_with_budget(&budget, vec![object], &factory)
            .unwrap();
        (root, lease, budget, control)
    }

    fn assert_no_probe_residue(root: &std::path::Path) {
        let residue: Vec<_> = fs::read_dir(root)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .filter(|name| name.starts_with("AL") && name.ends_with(".TMP"))
            .collect();
        assert!(residue.is_empty(), "probe residue: {residue:?}");
    }

    #[test]
    fn windows_validation_open_rejects_existing_writer() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-windows-existing-object-writer-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let path = root.join("object.acore");
        fs::write(&path, b"ciphertext").unwrap();
        let writer = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&path)
            .unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();

        let opened = crate::transaction::open_regular_file_in(&dir, path.file_name().unwrap());

        assert!(
            opened.is_err(),
            "validation must not start while a writer can remain open across its fences"
        );
        drop(writer);
        drop(dir);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_retained_validation_anchor_rejects_later_writer() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-windows-retained-anchor-writer-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let path = root.join("object.acore");
        fs::write(&path, b"ciphertext").unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        let file =
            crate::transaction::open_regular_file_in(&dir, path.file_name().unwrap()).unwrap();
        let anchor = RetainedValidationAnchor::new(file).unwrap();

        let writer = fs::OpenOptions::new()
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&path);

        assert!(
            writer.is_err(),
            "a retained validation anchor must exclude writers for its entire lifetime"
        );
        drop(anchor);
        let writer = fs::OpenOptions::new()
            .write(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&path)
            .unwrap();
        drop(writer);
        drop(dir);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_event_flood_is_constant_space_and_terminal() {
        let (root, lease, budget, control) = monitored_lease_with_control("event-flood");
        let mut observed_batches = control.native_batch_count();
        for index in 0..64 {
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(root.join(format!("event-flood-{index:04}")))
                .unwrap();
            file.write_all(b"native traffic").unwrap();
            drop(file);
            assert!(
                control.wait_until_native_batch_count(observed_batches + 1, Duration::from_secs(2)),
                "native monitor did not observe flood event {index}"
            );
            observed_batches = control.native_batch_count();
        }

        let terminal_without_fence = control
            .wait_until_state(MonitorState::DirtyAll, Duration::from_millis(250))
            || control.wait_until_state(MonitorState::Unknown, Duration::from_millis(250));
        let retained_high_water = control.retained_notification_batch_high_water();
        drop(lease);

        assert!(
            terminal_without_fence,
            "native traffic must synchronously publish terminal monitor state"
        );
        assert!(
            retained_high_water <= 1,
            "event publication retained {retained_high_water} batches instead of fixed folded state"
        );
        assert_eq!(control.join_count(), 1);
        assert_eq!(budget.usage().leases, 0);
        assert!(!control.worker_resources_alive());
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_dirty_fence_tracks_probe_boundary_after_same_buffer_mutation() {
        let (root, lease, _budget, control) =
            monitored_lease_with_control("same-buffer-dirty-boundary");
        let before = control.boundary_snapshot().unwrap();
        let probe = "ALBNDRY1.TMP";
        control.queue_probe_name(probe);
        control.inject_dirty_fence_batch(probe);

        assert_eq!(lease.fence(), MonitorState::DirtyAll);
        assert_eq!(lease.state(), MonitorState::DirtyAll);
        let after = control.boundary_snapshot().unwrap();
        assert_eq!(
            after,
            BoundarySnapshotForTest {
                terminal: FenceOutcome::DirtyAll,
                acknowledged_fence_generation: before.acknowledged_fence_generation + 1,
                boundary_progress: before.boundary_progress + 1,
                deferred_outcome: FenceOutcome::DirtyAll,
                active_probe_complete: true,
            },
            "DirtyAll must remain terminal while exact probe records still prove the requested boundary"
        );

        drop(lease);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_same_buffer_semantic_ambiguity_after_probe_is_immediately_unknown() {
        let (root, lease, _budget, control) =
            monitored_lease_with_control("same-buffer-semantic-unknown");
        let probe = "ALSEM001.TMP";
        control.queue_probe_name(probe);
        control.inject_semantic_unknown_fence_batch(probe);

        let outcome = lease.monitor().fence();
        assert!(control.wait_until_after_injected_batch_paused(Duration::from_secs(2)));
        let state_at_return = lease.state();
        let snapshot = control.boundary_snapshot().unwrap();
        control.release_after_injected_batch();

        assert_eq!(outcome, FenceOutcome::Unknown);
        assert_eq!(state_at_return, MonitorState::Unknown);
        assert_eq!(snapshot.terminal, FenceOutcome::Unknown);
        assert_eq!(snapshot.deferred_outcome, FenceOutcome::Clean);
        drop(lease);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_same_buffer_malformed_tail_after_probe_is_immediately_unknown() {
        let (root, lease, _budget, control) =
            monitored_lease_with_control("same-buffer-malformed-unknown");
        let probe = "ALMAL001.TMP";
        control.queue_probe_name(probe);
        control.inject_malformed_tail_fence_batch(probe);

        let outcome = lease.monitor().fence();
        assert!(
            control.wait_until_parser_error_publication_paused(Duration::from_secs(2)),
            "malformed parser publication barrier was not reached"
        );
        let state_at_return = lease.state();
        control.release_parser_error_publication();

        assert_eq!(outcome, FenceOutcome::Unknown);
        assert_eq!(state_at_return, MonitorState::Unknown);
        drop(lease);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_resource_plan_matches_three_live_monitor_resources() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-windows-resource-plan-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let object = binding(0);
        fs::write(root.join(object.physical_name.as_str()), b"ciphertext").unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        let control = WindowsLeaseTestControl::new();
        let factory = WindowsLeaseFactory::new_for_test(dir, control.clone()).unwrap();
        assert_eq!(
            factory.resource_plan().monitor_resource_count(),
            3,
            "production factory must reserve notification, cancellation, and join resources"
        );
        let budget = Arc::new(LeaseBudget::isolated());
        let lease = ObjectValidationLease::try_acquire_with_budget(&budget, vec![object], &factory)
            .unwrap();

        let usage = budget.usage();
        assert_eq!(
            usage.entries, 1,
            "retained object handle is an entry permit"
        );
        assert_eq!(usage.leases, 1);
        assert_eq!(usage.monitor_resources, 3);
        assert_eq!(control.live_monitor_resource_count(), 3);

        drop(lease);
        drop(factory);
        assert_eq!(control.live_monitor_resource_count(), 0);
        assert_eq!(budget.usage().entries, 0);
        assert_eq!(budget.usage().monitor_resources, 0);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_teardown_target_miss_retains_ownership_until_completion() {
        let (root, lease, budget, control) =
            monitored_lease_with_control("delayed-native-completion");
        control.pause_next_native_completion();

        let (done_sender, done_receiver) = std::sync::mpsc::channel();
        let drop_thread = std::thread::spawn(move || {
            drop(lease);
            let _ = done_sender.send(());
        });
        assert!(control.wait_until_cancel_requested(Duration::from_secs(2)));
        assert!(
            control.wait_until_native_completion_paused(Duration::from_secs(2)),
            "worker did not reach the finite native-completion latch"
        );

        std::thread::sleep(Duration::from_millis(2_100));
        assert!(
            done_receiver.try_recv().is_err(),
            "teardown returned before native completion was confirmed"
        );
        assert_eq!(control.teardown_target_miss_count(), 1);
        assert_eq!(control.native_completion_count(), 0);
        assert_eq!(control.join_count(), 0);
        assert!(control.native_buffer_alive());
        assert_eq!(control.live_monitor_resource_count(), 3);
        let live_usage = budget.usage();
        assert_eq!(live_usage.entries, 1);
        assert_eq!(live_usage.leases, 1);
        assert_eq!(live_usage.monitor_resources, 3);

        control.release_native_completion();
        done_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("teardown did not finish after finite completion release");
        drop_thread.join().unwrap();

        assert_eq!(control.native_completion_count(), 1);
        assert_eq!(control.join_count(), 1);
        assert!(!control.native_buffer_alive());
        assert_eq!(control.live_monitor_resource_count(), 0);
        let released_usage = budget.usage();
        assert_eq!(released_usage.entries, 0);
        assert_eq!(released_usage.leases, 0);
        assert_eq!(released_usage.monitor_resources, 0);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_teardown_observation_starts_at_first_cancellation_request() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-windows-teardown-observation-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let object = binding(0);
        fs::write(root.join(object.physical_name.as_str()), b"ciphertext").unwrap();
        let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        let diagnostics = Arc::new(ObjectLeaseDiagnosticObserver::default());
        let factory = WindowsLeaseFactory::new_observed(dir, Arc::clone(&diagnostics)).unwrap();
        let budget = Arc::new(LeaseBudget::isolated());
        let lease = ObjectValidationLease::try_acquire_with_budget(&budget, vec![object], &factory)
            .unwrap();

        lease.begin_release();
        std::thread::sleep(Duration::from_millis(2_100));
        drop(lease);

        let teardown = diagnostics
            .teardown()
            .expect("observed production monitor must record confirmed teardown");
        assert!(
            teardown.elapsed >= Duration::from_millis(2_000),
            "teardown elapsed time omitted the interval after first cancellation: {:?}",
            teardown.elapsed
        );
        assert!(
            !teardown.target_met,
            "targetMet must use the full interval from first cancellation through join"
        );
        assert!(teardown.completion_confirmed);
        assert_eq!(budget.usage().entries, 0);
        assert_eq!(budget.usage().leases, 0);
        assert_eq!(budget.usage().monitor_resources, 0);

        drop(factory);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_object_lease_probe_is_unpredictable_8_3_compatible_ascii() {
        let left = probe_name_for_test().unwrap();
        let right = probe_name_for_test().unwrap();
        assert_ne!(left, right);
        for name in [left, right] {
            let (stem, extension) = name.split_once('.').unwrap();
            assert_eq!(stem.len(), 8);
            assert_eq!(extension, "TMP");
            assert!(name.is_ascii());
            assert!(stem.starts_with("AL"));
            assert!(stem.bytes().all(|byte| byte.is_ascii_alphanumeric()));
        }
    }

    #[test]
    fn windows_object_lease_only_exact_active_probe_lifecycle_is_clean() {
        let probe = "AL123456.TMP";
        assert_eq!(
            notification_outcome_for_test(
                probe,
                &[
                    TestNotification::Added(probe.into()),
                    TestNotification::Removed(probe.into()),
                ],
                true,
            ),
            FenceOutcome::Clean
        );
        assert_eq!(
            notification_outcome_for_test(
                probe,
                &[
                    TestNotification::Added("al123456.tmp".into()),
                    TestNotification::Removed("al123456.tmp".into()),
                ],
                true,
            ),
            FenceOutcome::Unknown,
            "alternate case must not masquerade as exact fence traffic"
        );
        assert_eq!(
            notification_outcome_for_test(
                probe,
                &[
                    TestNotification::Modified(probe.into()),
                    TestNotification::Removed(probe.into()),
                ],
                true,
            ),
            FenceOutcome::Unknown
        );
        assert_eq!(
            notification_outcome_for_test(
                probe,
                &[
                    TestNotification::Added("UNREF.TMP".into()),
                    TestNotification::Removed("UNREF.TMP".into()),
                    TestNotification::Added(probe.into()),
                    TestNotification::Removed(probe.into()),
                ],
                true,
            ),
            FenceOutcome::DirtyAll
        );
    }

    #[test]
    fn windows_object_lease_ambiguity_and_failure_are_unknown() {
        let probe = "AL123456.TMP";
        for notifications in [
            vec![TestNotification::RenamedOld(probe.into())],
            vec![TestNotification::RenamedNew(probe.into())],
            vec![
                TestNotification::RenamedOld("OLD.TMP".into()),
                TestNotification::Added(probe.into()),
                TestNotification::RenamedNew("NEW.TMP".into()),
                TestNotification::Removed(probe.into()),
            ],
        ] {
            assert_eq!(
                notification_outcome_for_test(probe, &notifications, true),
                FenceOutcome::Unknown
            );
        }
        assert_eq!(
            notification_outcome_for_test(
                probe,
                &[
                    TestNotification::Added(probe.into()),
                    TestNotification::Removed(probe.into()),
                ],
                false,
            ),
            FenceOutcome::Unknown,
            "probe cleanup failure must be terminal uncertainty"
        );
    }

    #[test]
    fn windows_object_lease_startup_collision_and_cleanup_failure_are_terminal() {
        for scenario in ["collision", "cleanup-once", "cleanup-persistent"] {
            let root = std::env::temp_dir().join(format!(
                "anima-corefs-windows-object-startup-{scenario}-{}",
                std::process::id()
            ));
            let _ = fs::remove_dir_all(&root);
            fs::create_dir_all(&root).unwrap();
            let object = binding(0);
            fs::write(root.join(object.physical_name.as_str()), b"ciphertext").unwrap();
            let probe = match scenario {
                "collision" => "ALCOLL01.TMP",
                "cleanup-once" => "ALFAIL01.TMP",
                "cleanup-persistent" => "ALFAIL02.TMP",
                _ => unreachable!(),
            };
            if scenario == "collision" {
                fs::write(root.join(probe), b"collision").unwrap();
            }
            let control = WindowsLeaseTestControl::new();
            control.queue_probe_name(probe);
            match scenario {
                "cleanup-once" => control.fail_next_probe_cleanup_once(),
                "cleanup-persistent" => control.fail_probe_cleanup_persistently(),
                _ => {}
            }
            let dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
            let factory = WindowsLeaseFactory::new_for_test(dir, control.clone()).unwrap();

            let acquisition = ObjectValidationLease::try_acquire_with_budget(
                &LeaseBudget::isolated(),
                vec![object],
                &factory,
            );
            assert!(matches!(
                acquisition,
                Err(OptimizationMiss::TransientAcquisition)
            ));
            drop(factory);
            assert_eq!(control.monitor_state(), Some(MonitorState::Unknown));
            assert_eq!(
                control.probe_attempt_count(),
                1,
                "terminal startup ambiguity must never retry with a new probe"
            );

            match scenario {
                "collision" => {
                    assert_eq!(fs::read(root.join(probe)).unwrap(), b"collision");
                    fs::remove_file(root.join(probe)).unwrap();
                }
                "cleanup-once" => assert!(
                    !root.join(probe).exists(),
                    "drop must retry and remove a probe after a real first cleanup failure"
                ),
                "cleanup-persistent" => {
                    assert!(
                        root.join(probe).exists(),
                        "an impossible cleanup must remain visible and fail closed"
                    );
                    control.release_probe_cleanup_blocker();
                    fs::remove_file(root.join(probe)).unwrap();
                }
                _ => unreachable!(),
            }
            assert_no_probe_residue(&root);
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn windows_object_lease_worker_faults_use_real_worker_and_parser_paths() {
        for (label, fault) in [
            ("overflow", WorkerFaultForTest::Overflow),
            ("parse", WorkerFaultForTest::MalformedBatch),
            ("handle-loss", WorkerFaultForTest::LoseMonitorHandle),
        ] {
            let (root, lease, _budget, control) = monitored_lease_with_control(label);
            control.pause_next_read();
            fs::write(root.join("wake-current-read"), b"wake").unwrap();
            assert!(control.wait_until_read_paused(Duration::from_secs(2)));
            control.inject_next_worker_fault(fault);
            control.release_read_pause();
            assert!(control.wait_until_state(MonitorState::Unknown, Duration::from_secs(2)));
            assert_eq!(lease.state(), MonitorState::Unknown, "{label}");
            drop(lease);
            assert_no_probe_residue(&root);
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn lease_failure_windows_monitor_panic_is_immediately_terminal_and_bounded() {
        let (root, lease, budget, control) = monitored_lease_with_control("worker-panic");
        control.pause_next_read();
        fs::write(root.join("wake-current-read"), b"wake").unwrap();
        assert!(control.wait_until_read_paused(Duration::from_secs(2)));
        assert!(
            control.read_pending(),
            "panic injection must run while the native-read handshake is armed"
        );
        control.inject_next_worker_fault(WorkerFaultForTest::Panic);
        control.release_read_pause();

        assert!(
            control.wait_until_state(MonitorState::Unknown, Duration::from_secs(2)),
            "worker panic must publish terminal Unknown before monitor teardown"
        );
        assert!(control.wait_until_read_idle(Duration::from_secs(2)));
        assert_eq!(lease.state(), MonitorState::Unknown);

        let (done_sender, done_receiver) = std::sync::mpsc::channel();
        let drop_thread = std::thread::spawn(move || {
            let started = std::time::Instant::now();
            drop(lease);
            let _ = done_sender.send(started.elapsed());
        });
        let elapsed = done_receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("contained worker panic must permit bounded monitor teardown");
        drop_thread.join().unwrap();
        assert!(elapsed < Duration::from_secs(2));
        assert_eq!(budget.usage().leases, 0);
        assert!(
            !control.worker_resources_alive(),
            "worker and retained native handles must be released after teardown"
        );
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_object_lease_cancel_between_check_and_native_read_cannot_hang() {
        let (root, lease, budget, control) = monitored_lease_with_control("cancel-read-race");
        control.pause_next_read();
        fs::write(root.join("wake-current-read"), b"wake").unwrap();
        assert!(control.wait_until_read_paused(Duration::from_secs(2)));

        let (done_sender, done_receiver) = std::sync::mpsc::channel();
        let drop_thread = std::thread::spawn(move || {
            let started = std::time::Instant::now();
            drop(lease);
            let _ = done_sender.send(started.elapsed());
        });
        assert!(control.wait_until_cancel_requested(Duration::from_secs(2)));
        control.release_read_pause();

        let elapsed = match done_receiver.recv_timeout(Duration::from_secs(2)) {
            Ok(elapsed) => elapsed,
            Err(_) => {
                fs::write(root.join("watchdog-unblock"), b"wake").unwrap();
                let _ = done_receiver.recv_timeout(Duration::from_secs(1));
                drop_thread.join().unwrap();
                panic!("monitor teardown hung after cancellation won the pre-read race");
            }
        };
        drop_thread.join().unwrap();
        assert!(elapsed < Duration::from_secs(2));
        assert_eq!(control.monitor_state(), Some(MonitorState::Unknown));
        assert_eq!(budget.usage().leases, 0);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_object_lease_monitor_is_observably_armed_before_anchor_creation() {
        let (root, lease, _budget, control) = monitored_lease_with_control("construction-order");
        assert_eq!(
            control.construction_events(),
            vec![
                ConstructionEventForTest::MonitorArmed,
                ConstructionEventForTest::AnchorCreated,
            ]
        );
        drop(lease);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_object_lease_real_directory_junction_activity_is_dirty_all() {
        use std::os::windows::fs::MetadataExt;
        use std::process::Command;

        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;

        let (root, lease, _budget) = monitored_lease("junction");
        let target = root.with_extension("junction-target");
        let junction = root.join("junction-link");
        let _ = fs::remove_dir_all(&target);
        fs::create_dir_all(&target).unwrap();
        let output = Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&target)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "creating unprivileged directory junction failed: stdout={} stderr={}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert_ne!(
            fs::symlink_metadata(&junction).unwrap().file_attributes()
                & FILE_ATTRIBUTE_REPARSE_POINT,
            0,
            "test fixture must be a genuine Windows reparse point"
        );
        assert_eq!(lease.fence(), MonitorState::DirtyAll);
        drop(lease);
        fs::remove_dir(&junction).unwrap();
        fs::remove_dir_all(target).unwrap();
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_object_lease_every_non_probe_mutation_is_dirty_all() {
        let probe = "AL123456.TMP";
        for notification in [
            TestNotification::Added("CREATE.ACORE".into()),
            TestNotification::Removed("DELETE.ACORE".into()),
            TestNotification::Modified("TRUNCATE.ACORE".into()),
            TestNotification::Modified("REPARSE.ACORE".into()),
            TestNotification::Added("REPLACE.ACORE".into()),
        ] {
            assert_eq!(
                notification_outcome_for_test(
                    probe,
                    &[
                        notification,
                        TestNotification::Added(probe.into()),
                        TestNotification::Removed(probe.into()),
                    ],
                    true,
                ),
                FenceOutcome::DirtyAll
            );
        }
        assert_eq!(
            notification_outcome_for_test(
                probe,
                &[
                    TestNotification::RenamedOld("OLD.ACORE".into()),
                    TestNotification::RenamedNew("NEW.ACORE".into()),
                    TestNotification::Added(probe.into()),
                    TestNotification::Removed(probe.into()),
                ],
                true,
            ),
            FenceOutcome::DirtyAll
        );
    }

    #[test]
    fn windows_object_lease_arms_before_anchor_scan_and_fences_both_seams() {
        let (root, lease, budget) = monitored_lease("ordered-seams");
        assert_eq!(lease.fence(), MonitorState::Clean);

        fs::write(root.join("before-first-fence"), b"mutation").unwrap();
        assert_eq!(lease.fence(), MonitorState::DirtyAll);
        drop(lease);
        assert_eq!(budget.usage().leases, 0);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();

        let (root, lease, budget) = monitored_lease("between-fences");
        assert_eq!(
            lease.fence_with_validation_hook_for_test(|| {
                fs::write(root.join("between-fences"), b"mutation").unwrap();
            }),
            MonitorState::DirtyAll
        );
        drop(lease);
        assert_eq!(budget.usage().leases, 0);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_object_lease_real_create_delete_rename_and_replace_are_dirty() {
        for mutation in ["create", "delete", "rename", "replace"] {
            let (root, lease, _budget) = monitored_lease(mutation);
            let target = root.join("mutation-target");
            let object = root.join(binding(0).physical_name.as_str());
            match mutation {
                "create" => fs::write(&target, b"payload").unwrap(),
                "delete" => fs::remove_file(&object).unwrap(),
                "rename" => fs::rename(&object, &target).unwrap(),
                "replace" => {
                    fs::rename(&object, &target).unwrap();
                    fs::write(&object, b"replacement").unwrap();
                }
                _ => unreachable!(),
            }
            assert_eq!(lease.fence(), MonitorState::DirtyAll, "{mutation}");
            drop(lease);
            let _ = fs::remove_file(&target);
            assert_no_probe_residue(&root);
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn windows_object_lease_blocks_in_place_truncate_and_stays_clean() {
        let (root, lease, _budget) = monitored_lease("blocked-truncate");
        let object = root.join(binding(0).physical_name.as_str());

        let truncate = fs::write(&object, []);

        assert!(
            truncate.is_err(),
            "the retained anchor must deny an in-place truncate"
        );
        assert_eq!(fs::read(&object).unwrap(), b"ciphertext");
        assert_eq!(lease.fence(), MonitorState::Clean);
        drop(lease);
        fs::write(&object, []).unwrap();
        assert!(fs::read(&object).unwrap().is_empty());
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn windows_object_lease_retained_anchor_rejects_handle_loss_and_outside_hard_link() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-windows-object-anchor-{}",
            std::process::id()
        ));
        let outside = root.with_extension("outside");
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&outside);
        fs::create_dir_all(&root).unwrap();
        fs::create_dir_all(&outside).unwrap();
        let path = root.join("object.acore");
        fs::write(&path, b"ciphertext").unwrap();

        let anchor = RetainedValidationAnchor::new(fs::File::open(&path).unwrap()).unwrap();
        let lost = RetainedValidationAnchor::new(fs::File::open(&path).unwrap()).unwrap();
        assert_eq!(anchor.validate(), FenceOutcome::Clean);
        fs::hard_link(&path, outside.join("outside-link")).unwrap();
        assert_eq!(anchor.validate(), FenceOutcome::Unknown);

        lost.invalidate_for_test();
        assert_eq!(lost.validate(), FenceOutcome::Unknown);

        drop(anchor);
        drop(lost);
        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(outside).unwrap();

        let (root, lease, _budget) = monitored_lease("outside-hard-link");
        let outside = root.with_extension("outside");
        let _ = fs::remove_dir_all(&outside);
        fs::create_dir_all(&outside).unwrap();
        fs::hard_link(
            root.join(binding(0).physical_name.as_str()),
            outside.join("outside-link"),
        )
        .unwrap();
        assert_eq!(
            lease.fence(),
            MonitorState::Unknown,
            "a link created outside the watched directory must be rejected by fresh handle metadata"
        );
        drop(lease);
        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(outside).unwrap();
    }

    #[test]
    fn windows_object_lease_cancellation_is_unknown_and_leaves_zero_residue() {
        let (root, lease, budget) = monitored_lease("cancel");
        let state = lease.state_cell_for_test();
        let started = std::time::Instant::now();
        drop(lease);
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "monitor cancellation exceeded two seconds"
        );
        assert_eq!(state.state(), MonitorState::Unknown);
        assert_eq!(budget.usage().leases, 0);
        assert_no_probe_residue(&root);
        fs::remove_dir_all(root).unwrap();
    }
}
