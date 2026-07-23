use std::fmt;
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
use super::object_lease::{
    global_lease_budget, FenceOutcome, LeaseAttemptDecision, LeaseAttemptPolicy, LeaseBudget,
    LeaseBudgetUsage, LeaseMonitorResource, LeaseResourceFactory, MonitorState, MonitorStateCell,
    MonotonicClock, ObjectSetFingerprint, ObjectValidationLease, OptimizationMiss,
    PlatformLeaseSupport, ValidationAnchor, MAX_OBJECT_LEASE_ENTRIES, MAX_PROCESS_OBJECT_LEASES,
    MAX_PROCESS_OBJECT_LEASE_ENTRIES, MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES,
};

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

struct TestMonitorResource {
    drops: Arc<AtomicUsize>,
    cache: Option<Weak<CommitCache>>,
    dropped_outside_cache_lock: Option<Arc<AtomicUsize>>,
    budget_at_drop: Option<(Arc<LeaseBudget>, Arc<Mutex<Option<LeaseBudgetUsage>>>)>,
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
        if let Some((budget, observed)) = &self.budget_at_drop {
            *observed.lock().unwrap() = Some(budget.usage());
        }
    }
}

impl LeaseMonitorResource for TestMonitorResource {
    fn fence(&self) -> FenceOutcome {
        FenceOutcome::Clean
    }
}

#[derive(Debug)]
struct TestFactory {
    platform_supported: bool,
    fail_anchor_at: Option<usize>,
    monitor_attempts: AtomicUsize,
    anchor_attempts: AtomicUsize,
    monitor_drops: Arc<AtomicUsize>,
    cache: Option<Weak<CommitCache>>,
    dropped_outside_cache_lock: Option<Arc<AtomicUsize>>,
    budget_at_monitor_drop: Option<(Arc<LeaseBudget>, Arc<Mutex<Option<LeaseBudgetUsage>>>)>,
}

impl TestFactory {
    fn successful() -> Self {
        Self {
            platform_supported: true,
            fail_anchor_at: None,
            monitor_attempts: AtomicUsize::new(0),
            anchor_attempts: AtomicUsize::new(0),
            monitor_drops: Arc::new(AtomicUsize::new(0)),
            cache: None,
            dropped_outside_cache_lock: None,
            budget_at_monitor_drop: None,
        }
    }

    fn unsupported() -> Self {
        Self {
            platform_supported: false,
            ..Self::successful()
        }
    }
}

impl LeaseResourceFactory for TestFactory {
    fn platform_support(&self) -> PlatformLeaseSupport {
        if self.platform_supported {
            PlatformLeaseSupport::Supported
        } else {
            PlatformLeaseSupport::Unsupported
        }
    }

    fn create_monitor(
        &self,
        _state: Arc<MonitorStateCell>,
    ) -> Result<Box<dyn LeaseMonitorResource>, ()> {
        self.monitor_attempts.fetch_add(1, Ordering::SeqCst);
        Ok(Box::new(TestMonitorResource {
            drops: self.monitor_drops.clone(),
            cache: self.cache.clone(),
            dropped_outside_cache_lock: self.dropped_outside_cache_lock.clone(),
            budget_at_drop: self.budget_at_monitor_drop.clone(),
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
    ObjectValidationLease::try_acquire(budget, bindings(count), monitor_resources, factory)
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
    let budget_at_monitor_drop = Arc::new(Mutex::new(None));
    let factory = TestFactory {
        fail_anchor_at: Some(2),
        budget_at_monitor_drop: Some((budget.clone(), budget_at_monitor_drop.clone())),
        ..TestFactory::successful()
    };
    assert_eq!(
        acquire(budget.as_ref(), 5, 3, &factory).unwrap_err(),
        OptimizationMiss::TransientAcquisition
    );
    assert_eq!(factory.anchor_attempts.load(Ordering::SeqCst), 3);
    assert_eq!(factory.monitor_drops.load(Ordering::SeqCst), 1);
    assert_eq!(
        *budget_at_monitor_drop.lock().unwrap(),
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
fn cache_discards_lease_resources_outside_its_mutex() {
    for action in ["clear", "replace", "poison-recover"] {
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
            _ => unreachable!(),
        }

        assert_eq!(
            dropped_outside.load(Ordering::SeqCst),
            1,
            "{action} dropped a lease resource while cache.inner was held"
        );
    }
}
