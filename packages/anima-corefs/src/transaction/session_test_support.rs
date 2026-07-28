//! Narrow cross-crate test support for unlock-scoped session teardown.
//!
//! This module is compiled only for the `session-test-seams` feature. It exposes
//! observations and finite latches around the real platform monitor while leaving
//! production construction and teardown semantics unchanged.

#[cfg(windows)]
use std::sync::{Arc, Condvar, Mutex};
#[cfg(windows)]
use std::time::Duration;

#[cfg(windows)]
use super::cache::{AuthenticatedCommitSnapshot, CacheLookupKey, PointerSet, ValidatedObjectState};
use super::object_lease::MonitorState;
#[cfg(windows)]
use super::object_lease::{FenceOutcome, ObjectLeaseCandidate};
use super::object_lease::{LeasePermitBundle, MAX_PROCESS_OBJECT_LEASE_ENTRIES};
use super::CoreCommitCoordinator;
#[cfg(windows)]
use super::{catalog_object_bindings, validate_existing_object_file};
#[cfg(windows)]
use crate::crypto::FrkSubkeys;
#[cfg(windows)]
use crate::rotation::FrkKeyring;

#[cfg(windows)]
use super::object_lease::windows::{WindowsLeaseFactory, WindowsLeaseTestControl};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct SessionLeaseUsage {
    pub entries: usize,
    pub leases: usize,
    pub monitor_resources: usize,
}

pub struct SessionLeaseBudgetReservation {
    _permits: LeasePermitBundle,
}

#[cfg(windows)]
#[derive(Clone, Debug, Default)]
pub struct SessionPublicationPause {
    inner: Arc<(Mutex<SessionPublicationPauseState>, Condvar)>,
}

#[cfg(windows)]
#[derive(Debug, Default)]
struct SessionPublicationPauseState {
    paused: bool,
    released: bool,
}

#[cfg(windows)]
impl SessionPublicationPause {
    pub fn wait_until_paused(&self, timeout: Duration) -> bool {
        let (state, changed) = &*self.inner;
        let state = state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (state, _) = changed
            .wait_timeout_while(state, timeout, |state| !state.paused)
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.paused
    }

    pub fn release(&self) {
        let (state, changed) = &*self.inner;
        let mut state = state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.released = true;
        changed.notify_all();
    }

    fn pause(&self) {
        let (state, changed) = &*self.inner;
        let mut state = state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        state.paused = true;
        changed.notify_all();
        drop(
            changed
                .wait_while(state, |state| !state.released)
                .unwrap_or_else(|poisoned| poisoned.into_inner()),
        );
    }
}

#[cfg(windows)]
#[derive(Clone, Debug)]
pub struct WindowsSessionLeaseControl {
    control: WindowsLeaseTestControl,
    coordinator: Arc<CoreCommitCoordinator>,
}

#[cfg(windows)]
impl WindowsSessionLeaseControl {
    pub fn usage(&self) -> SessionLeaseUsage {
        let usage = self.coordinator.lease_budget().usage();
        SessionLeaseUsage {
            entries: usage.entries,
            leases: usage.leases,
            monitor_resources: usage.monitor_resources,
        }
    }

    pub fn pause_next_read(&self) {
        self.control.pause_next_read();
    }

    pub fn probe_attempt_count(&self) -> usize {
        self.control.probe_attempt_count()
    }

    pub fn wait_until_probe_attempt_count(&self, expected: usize, timeout: Duration) -> bool {
        let deadline = std::time::Instant::now() + timeout;
        while self.probe_attempt_count() < expected {
            if std::time::Instant::now() >= deadline {
                return false;
            }
            std::thread::yield_now();
        }
        true
    }

    pub fn wait_until_read_paused(&self, timeout: Duration) -> bool {
        self.control.wait_until_read_paused(timeout)
    }

    pub fn release_read_pause(&self) {
        self.control.release_read_pause();
    }

    pub fn wait_until_cancel_requested(&self, timeout: Duration) -> bool {
        self.control.wait_until_cancel_requested(timeout)
    }

    pub fn pause_next_native_completion(&self) {
        self.control.pause_next_native_completion();
    }

    pub fn wait_until_native_completion_paused(&self, timeout: Duration) -> bool {
        self.control.wait_until_native_completion_paused(timeout)
    }

    pub fn release_native_completion(&self) {
        self.control.release_native_completion();
    }

    pub fn native_completion_count(&self) -> usize {
        self.control.native_completion_count()
    }

    pub fn teardown_target_miss_count(&self) -> usize {
        self.control.teardown_target_miss_count()
    }

    pub fn join_count(&self) -> usize {
        self.control.join_count()
    }

    pub fn native_buffer_alive(&self) -> bool {
        self.control.native_buffer_alive()
    }

    pub fn live_monitor_resource_count(&self) -> usize {
        self.control.live_monitor_resource_count()
    }

    pub fn monitor_is_unknown(&self) -> bool {
        self.control.monitor_state() == Some(MonitorState::Unknown)
    }

    pub fn internal_locks_available(&self) -> bool {
        self.control.internal_locks_available_for_session_test()
    }
}

#[cfg(windows)]
pub struct WindowsSessionLeaseCandidate {
    candidate: Option<ObjectLeaseCandidate>,
    control: WindowsSessionLeaseControl,
}

#[cfg(windows)]
impl WindowsSessionLeaseCandidate {
    pub fn control(&self) -> &WindowsSessionLeaseControl {
        &self.control
    }
}

#[cfg(windows)]
impl CoreCommitCoordinator {
    pub fn session_test_seed_validation_cache(&self, keys: &FrkSubkeys) -> Result<(), String> {
        let _lease_operation = self
            .admit_lease_publication_operation()
            .map_err(|error| error.to_string())?;
        let selected = self
            .load_validation_snapshot(keys)
            .map_err(|error| error.to_string())?
            .ok_or_else(|| "CoreFS validation snapshot is missing".to_owned())?;
        let pointers = PointerSet {
            head: Some(selected.head().clone()),
            ..PointerSet::default()
        };
        let key = CacheLookupKey::derive(pointers, &self.core_id, &FrkKeyring::single(keys), keys)
            .map_err(|error| error.to_string())?;
        let objects = ValidatedObjectState::from_catalog_bindings(
            catalog_object_bindings(selected.catalog()).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
        self.cache
            .replace(Arc::new(AuthenticatedCommitSnapshot::new(
                &key,
                Arc::new(selected.catalog().clone()),
                Some(Arc::new(objects)),
            )));
        Ok(())
    }

    fn build_windows_session_candidate(
        self: &Arc<Self>,
    ) -> Result<WindowsSessionLeaseCandidate, String> {
        let snapshot = self
            .cache
            .current()
            .ok_or_else(|| "CoreFS session test cache is empty".to_owned())?;
        let mut bindings =
            catalog_object_bindings(snapshot.catalog()).map_err(|error| error.to_string())?;
        bindings.sort_unstable_by(|left, right| left.object_id.cmp(&right.object_id));

        let control = WindowsLeaseTestControl::new();
        let factory = WindowsLeaseFactory::new_for_test(
            self.objects_dir
                .try_clone()
                .map_err(|error| error.to_string())?,
            control.clone(),
        )
        .map_err(|error| error.to_string())?;
        let mut candidate = ObjectLeaseCandidate::try_begin(
            self.lease_budget(),
            bindings.clone(),
            self.object_directory_identity()
                .map_err(|error| error.to_string())?,
            self.lease_publication_generation(),
            &factory,
        )
        .map_err(|error| format!("CoreFS session lease candidate miss: {error:?}"))?;
        for binding in bindings {
            let file = validate_existing_object_file(
                &self.objects_dir,
                &binding.physical_name,
                None,
                #[cfg(test)]
                None,
            )
            .map_err(|error| error.to_string())?;
            candidate
                .add_validated_file(binding, file, &factory)
                .map_err(|error| format!("CoreFS session lease anchor miss: {error:?}"))?;
        }
        if candidate.fence() != FenceOutcome::Clean {
            return Err("CoreFS session lease candidate did not fence cleanly".to_owned());
        }
        Ok(WindowsSessionLeaseCandidate {
            candidate: Some(candidate),
            control: WindowsSessionLeaseControl {
                control,
                coordinator: Arc::clone(self),
            },
        })
    }

    pub fn session_test_install_windows_lease(
        self: &Arc<Self>,
    ) -> Result<WindowsSessionLeaseControl, String> {
        let _lease_operation = self
            .admit_lease_publication_operation()
            .map_err(|error| error.to_string())?;
        let mut prepared = self.build_windows_session_candidate()?;
        let candidate = prepared
            .candidate
            .take()
            .expect("fresh session test candidate is present");
        let lease = candidate
            .finish(FenceOutcome::Clean)
            .map_err(|_| "CoreFS session test candidate could not finish".to_owned())?;
        let current = self
            .cache
            .current()
            .ok_or_else(|| "CoreFS session test cache disappeared".to_owned())?;
        self.cache.replace(Arc::new(
            current.with_session_test_object_lease(Some(lease)),
        ));
        Ok(prepared.control)
    }

    pub fn session_test_prepare_windows_candidate(
        self: &Arc<Self>,
    ) -> Result<WindowsSessionLeaseCandidate, String> {
        self.build_windows_session_candidate()
    }

    pub fn session_test_attempt_candidate_publication(
        &self,
        candidate: &mut WindowsSessionLeaseCandidate,
    ) -> bool {
        self.session_test_attempt_candidate_publication_inner(candidate, None)
    }

    pub fn session_test_attempt_candidate_publication_paused(
        &self,
        candidate: &mut WindowsSessionLeaseCandidate,
        pause: &SessionPublicationPause,
    ) -> bool {
        self.session_test_attempt_candidate_publication_inner(candidate, Some(pause))
    }

    fn session_test_attempt_candidate_publication_inner(
        &self,
        candidate: &mut WindowsSessionLeaseCandidate,
        pause: Option<&SessionPublicationPause>,
    ) -> bool {
        let Ok(_lease_operation) = self.admit_lease_publication_operation() else {
            drop(candidate.candidate.take());
            return false;
        };
        let Some(candidate) = candidate.candidate.take() else {
            return false;
        };
        if !self.lease_publication_is_open()
            || candidate.publication_generation() != self.lease_publication_generation()
        {
            drop(candidate);
            return false;
        }
        let Ok(lease) = candidate.finish(FenceOutcome::Clean) else {
            return false;
        };
        if !self.object_lease_can_publish(&lease) {
            lease.begin_release();
            drop(lease);
            return false;
        }
        if let Some(pause) = pause {
            pause.pause();
        }
        let Some(current) = self.cache.current() else {
            lease.begin_release();
            drop(lease);
            return false;
        };
        self.cache.replace(Arc::new(
            current.with_session_test_object_lease(Some(lease)),
        ));
        true
    }

    pub fn session_test_cache_is_empty(&self) -> bool {
        self.cache.current().is_none()
    }

    pub fn session_test_cache_has_object_lease(&self) -> bool {
        self.cache
            .current()
            .as_ref()
            .is_some_and(|snapshot| snapshot.object_lease.is_some())
    }

    pub fn session_test_fence_cached_lease_is_unknown(&self) -> Option<bool> {
        self.cache
            .current()
            .and_then(|snapshot| snapshot.object_lease.clone())
            .map(|lease| lease.fence() == MonitorState::Unknown)
    }

    pub fn session_test_cache_lock_available(&self) -> bool {
        self.cache.inner.try_lock().is_ok()
    }

    pub fn session_test_budget_lock_available(&self) -> bool {
        self.lease_budget().guard_available_for_test()
    }
}

#[cfg(not(windows))]
impl CoreCommitCoordinator {
    pub fn session_test_cache_is_empty(&self) -> bool {
        self.cache.current().is_none()
    }

    pub fn session_test_cache_has_object_lease(&self) -> bool {
        self.cache
            .current()
            .as_ref()
            .is_some_and(|snapshot| snapshot.object_lease.is_some())
    }

    pub fn session_test_fence_cached_lease_is_unknown(&self) -> Option<bool> {
        self.cache
            .current()
            .and_then(|snapshot| snapshot.object_lease.clone())
            .map(|lease| lease.fence() == MonitorState::Unknown)
    }

    pub fn session_test_cache_lock_available(&self) -> bool {
        self.cache.inner.try_lock().is_ok()
    }

    pub fn session_test_budget_lock_available(&self) -> bool {
        self.lease_budget().guard_available_for_test()
    }
}

impl CoreCommitCoordinator {
    pub fn session_test_new_isolated(
        core_root: impl AsRef<std::path::Path>,
        core_id: impl Into<String>,
    ) -> Result<Self, String> {
        Self::new_with_isolated_lease_budget(core_root, core_id).map_err(|error| error.to_string())
    }

    pub fn session_test_core_root(&self) -> &std::path::Path {
        self.core_root()
    }

    pub fn session_test_try_reserve_budget(
        &self,
        entries: usize,
        monitor_resources: usize,
    ) -> Option<SessionLeaseBudgetReservation> {
        self.lease_budget()
            .try_reserve_exact(entries, monitor_resources)
            .map(|permits| SessionLeaseBudgetReservation { _permits: permits })
    }

    pub fn session_test_reserve_full_entry_budget(&self) -> Option<SessionLeaseBudgetReservation> {
        self.session_test_try_reserve_budget(MAX_PROCESS_OBJECT_LEASE_ENTRIES, 0)
    }
}
