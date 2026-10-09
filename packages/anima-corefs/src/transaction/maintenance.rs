//! Authenticated, conservative CoreFS retention and maintenance.
mod archive;
use super::*;
use crate::catalog::{catalog_generation_physical_name, decrypt_catalog_generation};
use crate::publication::atomic_publish_in;
use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

const REGISTRY: &str = "RETENTION_V1";
const MAX_CATALOG_FILES: usize = 100_000;
// Aggregate authenticated work bounds; only one catalog AST is held at a time.
const MAX_CATALOG_AGGREGATE_BYTES: u64 = 128 * 1024 * 1024 * 1024;
const MAX_CATALOG_AGGREGATE_ENTRIES: u64 = 500_000_000;
const MAX_OBJECT_FILES: usize = 200_000;
const MAX_JOURNAL_BYTES: usize = 128 * 1024 * 1024;
const DAY_MS: u64 = 86_400_000;

#[derive(Clone, Debug, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MaintenancePin {
    pub generation: u64,
    pub catalog_hash: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaintenanceRequest {
    #[serde(default = "thirty")]
    pub retention_days: u32,
    pub now_ms: u64,
    #[serde(default)]
    pub pins: Vec<MaintenancePin>,
}
fn thirty() -> u32 {
    30
}
impl Default for MaintenanceRequest {
    fn default() -> Self {
        Self {
            retention_days: 30,
            now_ms: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis() as u64,
            pins: Vec::new(),
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum MaintenanceError {
    #[error("CoreFS maintenance request is invalid")]
    Invalid,
    #[error("CoreFS maintenance evidence is incomplete or corrupt")]
    Evidence,
    #[error("CoreFS maintenance inventory changed")]
    Conflict,
    #[error("CoreFS maintenance is blocked")]
    Blocked,
    #[error("CoreFS maintenance transaction failed: {0}")]
    Commit(#[from] CommitError),
    #[error("CoreFS maintenance I/O failed: {0}")]
    Io(#[from] io::Error),
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaintenanceInventory {
    pub version: u16,
    pub head_generation: Option<u64>,
    pub head_catalog_hash: Option<String>,
    pub inventory_digest: String,
    pub retained_catalog_count: usize,
    pub retained_unknown_age_count: usize,
    pub catalog_count: usize,
    pub object_count: usize,
    pub candidate_catalog_count: usize,
    pub candidate_object_count: usize,
    pub candidate_bytes: u64,
    pub candidate_preparation_count: usize,
    pub required_frk_versions: Vec<u32>,
    pub blockers: Vec<String>,
    pub warnings: Vec<String>,
    pub operation: Option<MaintenanceProgress>,
    pub resume_authorized: bool,
    pub cancel_authorized: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MaintenanceProgress {
    pub id: String,
    pub state: String,
    pub quarantined_count: usize,
    pub swept_count: usize,
    pub total_count: usize,
    pub source_head_generation: u64,
    pub source_head_catalog_hash: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub expected_inventory_digest: String,
}

#[derive(Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Registry {
    observed: BTreeMap<String, u64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Candidate {
    catalog: bool,
    name: String,
    hash: String,
    bytes: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    preparation_id: Option<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    archive_files: BTreeMap<String, archive::ArchiveFile>,
}
struct Inventory {
    report: MaintenanceInventory,
    candidates: Vec<Candidate>,
    catalogs: BTreeMap<String, (u32, CatalogGeneration)>,
    catalog_headers: BTreeMap<String, (u32, u64)>,
    registry: Registry,
    active_version: Option<u32>,
    records: BTreeMap<String, Candidate>,
    preparation_archives: Vec<preparation::MaintenancePreparationArchive>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PruneRequest {
    #[serde(flatten)]
    pub policy: MaintenanceRequest,
    pub expected_inventory_digest: String,
    pub action: String,
    pub max_items: usize,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MaintenanceLockRequest {
    #[serde(flatten)]
    pub policy: MaintenanceRequest,
    pub expected_inventory_digest: String,
}

pub struct MaintenanceLock {
    _lock: CoreCommitLock,
    core_root_digest: String,
    request: MaintenanceRequest,
    expected_digest: String,
    pub inventory: MaintenanceInventory,
    runtime: Option<(PathBuf, Dir)>,
    runtime_journal_sha256: Option<String>,
}

impl CoreCommitCoordinator {
    fn maintenance_read_catalog(
        &self,
        keyring: &FrkKeyring<'_>,
        name: &str,
        version: u32,
        generation: u64,
    ) -> Result<CatalogGeneration, MaintenanceError> {
        let encoded = read_bounded_in(
            &self.catalogs_dir,
            OsStr::new(name),
            MAX_CATALOG_ENVELOPE_SIZE,
        )?;
        if catalog_generation_physical_name(&encoded).ok().as_deref() != Some(name) {
            return Err(MaintenanceError::Evidence);
        }
        let catalog = decrypt_catalog_generation(
            keyring
                .require(version)
                .map_err(|_| MaintenanceError::Evidence)?,
            &self.core_id,
            &encoded,
        )
        .map_err(|_| MaintenanceError::Evidence)?;
        if catalog.generation() != generation {
            return Err(MaintenanceError::Evidence);
        }
        Ok(catalog)
    }

    fn ensure_active_journal_locked(
        &self,
        keyring: &FrkKeyring<'_>,
        journal: &Journal,
    ) -> Result<(), MaintenanceError> {
        match read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32) {
            Ok(encoded) => {
                if open::<String>(keyring, &self.core_id, "active", &encoded)?.1
                    != journal.progress.id
                {
                    return Err(MaintenanceError::Evidence);
                }
                return Ok(());
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => {}
            Err(_) => return Err(MaintenanceError::Evidence),
        }
        // Recover the journal-published / marker-not-yet-published crash seam.
        // An authenticated native plan plus its untouched source checkpoint is required.
        if journal.progress.quarantined_count != 0
            || journal.progress.swept_count != 0
            || !matches!(
                journal.progress.state.as_str(),
                "quarantining" | "publishing" | "reanchoring"
            )
        {
            return Err(MaintenanceError::Evidence);
        }
        self.ensure_no_maintenance_quarantine_locked()?;
        let head = self
            .load_pointer_head(HEAD_FILE)?
            .ok_or(MaintenanceError::Evidence)?;
        if head.generation() != journal.progress.source_head_generation
            || head.catalog_hash() != journal.progress.source_head_catalog_hash
        {
            return Err(MaintenanceError::Conflict);
        }
        let inventory = self.maintenance_inventory_locked(keyring, &journal.request)?;
        if !inventory.report.blockers.is_empty() {
            return Err(MaintenanceError::Blocked);
        }
        if journal.purge.is_none() && inventory.report.inventory_digest != journal.expected_digest {
            return Err(MaintenanceError::Conflict);
        }
        for record in &journal.candidates {
            let source = archive::source(self, record)?;
            if !same_record(&archive::record(&source, record, false)?, record) {
                return Err(MaintenanceError::Evidence);
            }
        }
        if let Some(checkpoint) = &journal.purge {
            self.purge_head_record(keyring, &checkpoint.published, checkpoint.active_version)?;
            self.purge_head_record(keyring, &checkpoint.predecessor, checkpoint.active_version)?;
        }
        let keys = keyring
            .require(head.required_frk_version())
            .map_err(|_| MaintenanceError::Evidence)?;
        atomic_publish_in(
            &self.fs_dir,
            OsStr::new(ACTIVE),
            &seal(keys, &self.core_id, "active", &journal.progress.id)?,
        )?;
        Ok(())
    }

    fn maintenance_resume_authority_locked(
        &self,
        keyring: &FrkKeyring<'_>,
        journal: &Journal,
        report: &MaintenanceInventory,
    ) -> Result<(bool, bool), MaintenanceError> {
        if matches!(journal.progress.state.as_str(), "complete" | "cancelled") {
            return Ok((false, false));
        }
        if journal.core_root_digest != hash(self.core_root.to_string_lossy().as_bytes()) {
            return Ok((false, false));
        }
        let marker = match read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32)
        {
            Ok(bytes) => open::<String>(keyring, &self.core_id, "active", &bytes)?,
            Err(_) => return Ok((false, false)),
        };
        if marker.1 != journal.progress.id {
            return Ok((false, false));
        }
        if report.blockers.iter().any(|blocker| {
            blocker != "maintenance_operation_pending"
                && !(blocker == "head_lineage_corrupt"
                    && (journal.reanchor.is_some()
                        || journal
                            .purge
                            .as_ref()
                            .is_some_and(|_| journal.progress.state == "publishing")))
        }) {
            return Ok((false, false));
        }
        let head = self
            .load_pointer_head(HEAD_FILE)?
            .ok_or(MaintenanceError::Evidence)?;
        let is_source = head.generation() == journal.progress.source_head_generation
            && head.catalog_hash() == journal.progress.source_head_catalog_hash;
        let is_expected = head.generation() == journal.head.generation
            && head.catalog_hash() == journal.head.catalog_hash;
        if !is_expected && !(journal.progress.state == "publishing" && is_source) {
            return Ok((false, false));
        }
        let inventory = self.maintenance_inventory_locked(keyring, &journal.request)?;
        let planned: BTreeSet<_> = journal
            .candidates
            .iter()
            .map(|record| record.name.clone())
            .collect();
        if journal.progress.state != "publishing" {
            let retained: BTreeMap<_, _> = inventory
                .records
                .into_iter()
                .filter(|(name, _)| !planned.contains(name))
                .collect();
            if retained.len() != journal.retained.len()
                || retained.iter().any(|(name, record)| {
                    !journal
                        .retained
                        .get(name)
                        .is_some_and(|expected| same_record(record, expected))
                })
            {
                return Ok((false, false));
            }
        }
        let quarantine = match self.fs_dir.open_dir(QUARANTINE) {
            Ok(dir) => Some(dir),
            Err(e) if e.kind() == io::ErrorKind::NotFound => None,
            Err(_) => return Ok((false, false)),
        };
        let mut qnames = BTreeSet::new();
        if let Some(q) = &quarantine {
            validate_linked_directory(&self.fs_dir, QUARANTINE, q)?;
            for entry in q.entries()? {
                let name = entry?
                    .file_name()
                    .into_string()
                    .map_err(|_| MaintenanceError::Evidence)?;
                if !planned.contains(&name) {
                    return Ok((false, false));
                }
                qnames.insert(name);
            }
        }
        for record in &journal.candidates {
            let source = archive::source(self, record)?;
            let actual = if qnames.contains(&record.name) {
                if source.symlink_metadata(&record.name).is_ok() {
                    return Ok((false, false));
                }
                archive::record(
                    quarantine.as_ref().ok_or(MaintenanceError::Evidence)?,
                    record,
                    journal.progress.state == "sweeping",
                )
            } else {
                archive::record(&source, record, false)
            };
            match actual {
                Ok(actual) if same_record(&actual, record) => {}
                Err(MaintenanceError::Io(e))
                    if e.kind() == io::ErrorKind::NotFound
                        && journal.progress.state == "sweeping"
                        && journal.progress.quarantined_count == journal.candidates.len() => {}
                _ => return Ok((false, false)),
            }
        }
        if let Some(checkpoint) = &journal.reanchor {
            for pointer in [
                HEAD_FILE,
                CUTOVER_RECEIPT_FILE,
                CUTOVER_COMPLETE_FILE,
                VALIDATION_HEAD_FILE,
            ] {
                if let Some(pointer_head) = self.load_pointer_head(pointer)? {
                    let generation = pointer_head.generation();
                    let (_, catalog) = self.load_pointer_for_head(keyring, pointer_head)?;
                    let validation = pointer == VALIDATION_HEAD_FILE
                        && catalog.cutover_marker().is_none()
                        && generation < head.generation();
                    if !validation
                        && catalog.cutover_marker().map(CatalogCutoverMarker::epoch)
                            != Some(checkpoint.cutover_epoch)
                    {
                        return Ok((false, false));
                    }
                }
            }
        }
        if let Some(checkpoint) = &journal.purge {
            if journal.progress.state != "publishing" {
                if inventory.preparation_archives.iter().any(|archive| {
                    !planned.contains(&archive.id)
                        && archive
                            .object_ids
                            .iter()
                            .any(|id| checkpoint.target_ids.contains(id))
                }) {
                    return Ok((false, false));
                }
                for (name, (version, generation)) in &inventory.catalog_headers {
                    let catalog =
                        self.maintenance_read_catalog(keyring, name, *version, *generation)?;
                    if !planned.contains(name)
                        && catalog
                            .entries()
                            .iter()
                            .any(|entry| checkpoint.target_ids.contains(entry.stable_id().as_str()))
                    {
                        return Ok((false, false));
                    }
                }
            }
        }
        let cancel = journal.purge.is_none()
            && journal.reanchor.is_none()
            && journal.progress.swept_count == 0
            && matches!(
                journal.progress.state.as_str(),
                "quarantining" | "quarantined" | "cancelling"
            );
        Ok((true, cancel))
    }

    pub fn maintenance_lock_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        request: &MaintenanceLockRequest,
    ) -> Result<MaintenanceLock, MaintenanceError> {
        self.maintenance_lock_with_runtime_v1(keyring, request, None)
    }

    pub fn maintenance_lock_with_runtime_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        request: &MaintenanceLockRequest,
        runtime_journal_directory: Option<&Path>,
    ) -> Result<MaintenanceLock, MaintenanceError> {
        let lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.ensure_maintenance_write_allowed_locked()?;
        self.ensure_no_maintenance_quarantine_locked()?;
        let runtime = runtime_journal_directory
            .map(|path| runtime_directory(&self.core_root, path))
            .transpose()?;
        let runtime_journal_sha256 = if let Some((path, dir)) = &runtime {
            self.verify_terminal_runtime_journal_locked(keyring, path, dir)?
        } else {
            None
        };
        let inventory = self.maintenance_inventory_locked(keyring, &request.policy)?;
        if !inventory.report.blockers.is_empty() {
            return Err(MaintenanceError::Blocked);
        }
        if inventory.report.inventory_digest != request.expected_inventory_digest {
            return Err(MaintenanceError::Conflict);
        }
        Ok(MaintenanceLock {
            _lock: lock,
            core_root_digest: hash(self.core_root.to_string_lossy().as_bytes()),
            request: request.policy.clone(),
            expected_digest: request.expected_inventory_digest.clone(),
            inventory: inventory.report,
            runtime,
            runtime_journal_sha256,
        })
    }

    pub fn maintenance_verify_locked_v1(
        &self,
        guard: &MaintenanceLock,
        keyring: &FrkKeyring<'_>,
    ) -> Result<MaintenanceInventory, MaintenanceError> {
        if guard.core_root_digest != hash(self.core_root.to_string_lossy().as_bytes()) {
            return Err(MaintenanceError::Invalid);
        }
        self.ensure_maintenance_write_allowed_locked()?;
        self.ensure_no_maintenance_quarantine_locked()?;
        if let Some((path, dir)) = &guard.runtime {
            if self.verify_terminal_runtime_journal_locked(keyring, path, dir)?
                != guard.runtime_journal_sha256
            {
                return Err(MaintenanceError::Evidence);
            }
        }
        let inventory = self.maintenance_inventory_locked(keyring, &guard.request)?;
        if !inventory.report.blockers.is_empty() {
            return Err(MaintenanceError::Blocked);
        }
        if inventory.report.inventory_digest != guard.expected_digest {
            return Err(MaintenanceError::Conflict);
        }
        Ok(inventory.report)
    }

    fn verify_terminal_runtime_journal_locked(
        &self,
        keyring: &FrkKeyring<'_>,
        path: &Path,
        dir: &Dir,
    ) -> Result<Option<String>, MaintenanceError> {
        validate_ambient_linked_directory(path, dir)?;
        let name = format!(
            "corefs-maintenance-{}.journal",
            hash(self.core_root.to_string_lossy().as_bytes())
        );
        let digest = match read_bounded_in(dir, OsStr::new(&name), MAX_JOURNAL_BYTES + 32) {
            Ok(encoded) => {
                let (_, journal) = open::<Journal>(keyring, &self.core_id, "journal", &encoded)?;
                if journal.core_root_digest != hash(self.core_root.to_string_lossy().as_bytes())
                    || !matches!(journal.progress.state.as_str(), "complete" | "cancelled")
                {
                    return Err(MaintenanceError::Blocked);
                }
                Some(hash(&encoded))
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => None,
            Err(_) => return Err(MaintenanceError::Evidence),
        };
        validate_ambient_linked_directory(path, dir)?;
        Ok(digest)
    }

    fn ensure_no_maintenance_quarantine_locked(&self) -> Result<(), MaintenanceError> {
        match self.fs_dir.open_dir(QUARANTINE) {
            Ok(dir) => {
                validate_linked_directory(&self.fs_dir, QUARANTINE, &dir)?;
                if dir.entries()?.next().is_some() {
                    return Err(MaintenanceError::Blocked);
                }
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => {}
            Err(_) => return Err(MaintenanceError::Evidence),
        }
        Ok(())
    }

    pub fn maintenance_inventory_with_runtime_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        request: &MaintenanceRequest,
        runtime_journal_directory: Option<&Path>,
    ) -> Result<MaintenanceInventory, MaintenanceError> {
        let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        let inventory = self.maintenance_inventory_locked(keyring, request)?;
        let mut report = inventory.report;
        if report.blockers.is_empty() {
            if let Some(version) = inventory.active_version {
                atomic_publish_in(
                    &self.fs_dir,
                    OsStr::new(REGISTRY),
                    &seal(
                        keyring
                            .require(version)
                            .map_err(|_| MaintenanceError::Evidence)?,
                        &self.core_id,
                        "registry",
                        &inventory.registry,
                    )?,
                )?;
            }
        }
        let mut marker =
            match read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32) {
                Ok(bytes) => Some(open::<String>(keyring, &self.core_id, "active", &bytes)?),
                Err(e) if e.kind() == io::ErrorKind::NotFound => None,
                Err(_) => return Err(MaintenanceError::Evidence),
            };
        if let Some((version, _)) = &marker {
            report.required_frk_versions.push(*version);
            report
                .blockers
                .push("maintenance_operation_pending".to_owned());
        }
        if let Some(path) = runtime_journal_directory {
            let (runtime_path, runtime) = runtime_directory(&self.core_root, path)?;
            let name = format!(
                "corefs-maintenance-{}.journal",
                hash(self.core_root.to_string_lossy().as_bytes())
            );
            match read_bounded_in(&runtime, OsStr::new(&name), MAX_JOURNAL_BYTES + 32) {
                Ok(bytes) => {
                    let (version, journal) =
                        open::<Journal>(keyring, &self.core_id, "journal", &bytes)?;
                    if marker
                        .as_ref()
                        .is_some_and(|(_, id)| *id != journal.progress.id)
                    {
                        return Err(MaintenanceError::Evidence);
                    }
                    if !matches!(journal.progress.state.as_str(), "complete" | "cancelled") {
                        if journal
                            .referenced_frk_versions
                            .iter()
                            .any(|version| !keyring.contains(*version))
                        {
                            return Err(MaintenanceError::Evidence);
                        }
                        report.required_frk_versions.push(version);
                        report
                            .required_frk_versions
                            .extend(journal.referenced_frk_versions.iter().copied());
                        if journal
                            .referenced_frk_versions
                            .iter()
                            .any(|version| !keyring.contains(*version))
                        {
                            report
                                .blockers
                                .push("maintenance_retained_frk_missing".to_owned());
                        }
                        if marker.is_none() {
                            if self.ensure_active_journal_locked(keyring, &journal).is_ok() {
                                let active_version = self
                                    .load_pointer_head(HEAD_FILE)?
                                    .ok_or(MaintenanceError::Evidence)?
                                    .required_frk_version();
                                marker = Some((active_version, journal.progress.id.clone()));
                                report
                                    .blockers
                                    .push("maintenance_operation_pending".to_owned());
                                report.required_frk_versions.push(active_version);
                            } else {
                                report
                                    .blockers
                                    .push("maintenance_journal_without_marker".to_owned());
                            }
                        }
                    } else if let Some(active_version) = inventory.active_version {
                        if active_version != version {
                            self.save_maintenance_journal(
                                &runtime_path,
                                &runtime,
                                &name,
                                keyring
                                    .require(active_version)
                                    .map_err(|_| MaintenanceError::Evidence)?,
                                &journal,
                            )?;
                        }
                    }
                    if marker.is_some()
                        && matches!(journal.progress.state.as_str(), "complete" | "cancelled")
                        && report
                            .blockers
                            .iter()
                            .all(|b| b == "maintenance_operation_pending")
                    {
                        self.ensure_no_maintenance_quarantine_locked()?;
                        if journal.reanchor.is_some() {
                            match read_bounded_in(
                                &self.fs_dir,
                                OsStr::new(REANCHOR_RECOVERY),
                                64 * 1024,
                            ) {
                                Ok(bytes) => {
                                    if open::<CoreReanchorRecovery>(
                                        keyring,
                                        &self.core_id,
                                        "reanchor-recovery",
                                        &bytes,
                                    )?
                                    .1
                                    .operation_id
                                        != journal.progress.id
                                    {
                                        return Err(MaintenanceError::Evidence);
                                    }
                                    durable_remove(&self.fs_dir, REANCHOR_RECOVERY)?;
                                }
                                Err(e) if e.kind() == io::ErrorKind::NotFound => {}
                                Err(e) => return Err(e.into()),
                            }
                        }
                        durable_remove(&self.fs_dir, ACTIVE)?;
                        marker = None;
                        report
                            .blockers
                            .retain(|b| b != "maintenance_operation_pending");
                    }
                    let (resume, cancel) =
                        self.maintenance_resume_authority_locked(keyring, &journal, &report)?;
                    report.resume_authorized = resume;
                    report.cancel_authorized = cancel;
                    if resume {
                        report.inventory_digest = journal.expected_digest.clone();
                    }
                    let mut progress = journal.progress.clone();
                    progress.kind = if journal.purge.is_some() {
                        "purge"
                    } else if journal.reanchor.is_some() {
                        "reanchor"
                    } else {
                        "prune"
                    }
                    .to_owned();
                    progress.expected_inventory_digest = journal.expected_digest.clone();
                    report.operation = Some(progress);
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => {
                    if marker.is_some() {
                        report
                            .blockers
                            .push("maintenance_journal_missing".to_owned());
                    }
                }
                Err(_) => return Err(MaintenanceError::Evidence),
            }
        }
        if let Ok(quarantine) = self.fs_dir.open_dir(QUARANTINE) {
            if quarantine.entries()?.next().is_some() && marker.is_none() {
                report
                    .blockers
                    .push("maintenance_quarantine_without_journal".to_owned());
            }
        }
        report.required_frk_versions.sort_unstable();
        report.required_frk_versions.dedup();
        if !report.blockers.is_empty() {
            report.candidate_catalog_count = 0;
            report.candidate_object_count = 0;
            report.candidate_bytes = 0;
        }
        Ok(report)
    }
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Journal {
    core_root_digest: String,
    request: MaintenanceRequest,
    expected_digest: String,
    head: MaintenancePin,
    candidates: Vec<Candidate>,
    retained: BTreeMap<String, Candidate>,
    progress: MaintenanceProgress,
    #[serde(default)]
    purge: Option<PurgeCheckpoint>,
    #[serde(default)]
    reanchor: Option<ReanchorCheckpoint>,
    #[serde(default)]
    referenced_frk_versions: Vec<u32>,
}
const QUARANTINE: &str = "maintenance-quarantine";
const ACTIVE: &str = "MAINTENANCE_ACTIVE_V1";
const REANCHOR_RECOVERY: &str = "REANCHOR_RECOVERY_V1";

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CoreReanchorRecovery {
    operation_id: String,
    target_head: Vec<u8>,
    // Purge preserves a newer HEAD while anchoring recovery to its clean predecessor.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pointer_head: Option<Vec<u8>>,
    cutover_epoch: u64,
    source_pointers: BTreeMap<String, Option<Vec<u8>>>,
}

impl CoreCommitCoordinator {
    /// Only repairs previously authorized pointer publication. Never selects/deletes data.
    pub(super) fn recover_reanchor_pointer_lineage_locked(
        &self,
        keyring: &FrkKeyring<'_>,
    ) -> Result<bool, CommitError> {
        let result = (|| -> Result<bool, MaintenanceError> {
            let encoded =
                match read_bounded_in(&self.fs_dir, OsStr::new(REANCHOR_RECOVERY), 64 * 1024) {
                    Ok(bytes) => bytes,
                    Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(false),
                    Err(e) => return Err(e.into()),
                };
            let (version, recovery) = open::<CoreReanchorRecovery>(
                keyring,
                &self.core_id,
                "reanchor-recovery",
                &encoded,
            )?;
            let target = decode_head(&recovery.target_head).map_err(CommitError::from)?;
            if target.required_frk_version() != version
                || self.load_pointer_head(HEAD_FILE)?.as_ref() != Some(&target)
            {
                return Err(MaintenanceError::Evidence);
            }
            let (_, catalog) = self.load_pointer_for_head(keyring, target.clone())?;
            if catalog.cutover_marker().map(CatalogCutoverMarker::epoch)
                != Some(recovery.cutover_epoch)
            {
                return Err(MaintenanceError::Evidence);
            }
            let pointer_bytes = recovery
                .pointer_head
                .as_ref()
                .unwrap_or(&recovery.target_head);
            let pointer_target = decode_head(pointer_bytes).map_err(CommitError::from)?;
            let (_, predecessor) = self.load_pointer_for_head(keyring, pointer_target.clone())?;
            if pointer_target.required_frk_version() != version
                || pointer_target.generation() > target.generation()
                || predecessor
                    .cutover_marker()
                    .map(CatalogCutoverMarker::epoch)
                    != Some(recovery.cutover_epoch)
            {
                return Err(MaintenanceError::Evidence);
            }
            let active = read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32)?;
            if open::<String>(keyring, &self.core_id, "active", &active)?.1 != recovery.operation_id
            {
                return Err(MaintenanceError::Evidence);
            }
            if recovery.source_pointers.len() != 4 {
                return Err(MaintenanceError::Evidence);
            }
            for pointer in [
                HEAD_FILE,
                CUTOVER_RECEIPT_FILE,
                CUTOVER_COMPLETE_FILE,
                VALIDATION_HEAD_FILE,
            ] {
                let expected = recovery
                    .source_pointers
                    .get(pointer)
                    .ok_or(MaintenanceError::Evidence)?;
                let current = self.load_pointer_head(pointer)?;
                let original = expected
                    .as_ref()
                    .map(|bytes| decode_head(bytes).map_err(CommitError::from))
                    .transpose()?;
                let intended = if pointer == HEAD_FILE {
                    &target
                } else {
                    &pointer_target
                };
                if current != original && current.as_ref() != Some(intended) {
                    return Err(MaintenanceError::Evidence);
                }
                if let Some(head) = current {
                    let name = format!(
                        "catalog-{:020}-{}.acore",
                        head.generation(),
                        head.catalog_hash()
                    );
                    let bytes = read_bounded_in(
                        &self.catalogs_dir,
                        OsStr::new(&name),
                        MAX_CATALOG_ENVELOPE_SIZE,
                    )?;
                    if hash(&bytes) != head.catalog_hash()
                        || catalog_generation_physical_name(&bytes).ok().as_deref()
                            != Some(name.as_str())
                    {
                        return Err(MaintenanceError::Evidence);
                    }
                    // The Core-authenticated checkpoint attests to original old-key catalog auth.
                    // Available keys are nevertheless rechecked; an old key need not be unlocked for pure forward recovery.
                    if keyring.contains(head.required_frk_version()) {
                        let (_, old) = self.load_pointer_for_head(keyring, head)?;
                        let validation = pointer == VALIDATION_HEAD_FILE
                            && old.cutover_marker().is_none()
                            && old.generation() < target.generation();
                        if !validation
                            && old.cutover_marker().map(CatalogCutoverMarker::epoch)
                                != Some(recovery.cutover_epoch)
                        {
                            return Err(MaintenanceError::Evidence);
                        }
                    }
                }
            }
            for pointer in [
                CUTOVER_RECEIPT_FILE,
                CUTOVER_COMPLETE_FILE,
                VALIDATION_HEAD_FILE,
            ] {
                self.validate_pinned_layout()?;
                atomic_publish_in(&self.fs_dir, OsStr::new(pointer), pointer_bytes)?;
            }
            Ok(true)
        })();
        result.map_err(|_| CommitError::AuthoritativeHeadViolatesCutoverReceipt)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReanchorCheckpoint {
    active_version: u32,
    cutover_epoch: u64,
}

impl CoreCommitCoordinator {
    pub fn maintenance_reanchor_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        runtime_journal_directory: &Path,
        request: &PruneRequest,
    ) -> Result<MaintenanceInventory, MaintenanceError> {
        self.maintenance_reanchor_with_hook(
            keyring,
            runtime_journal_directory,
            request,
            &mut |_| Ok(()),
        )
    }
    fn maintenance_reanchor_with_hook<F>(
        &self,
        keyring: &FrkKeyring<'_>,
        runtime_journal_directory: &Path,
        request: &PruneRequest,
        hook: &mut F,
    ) -> Result<MaintenanceInventory, MaintenanceError>
    where
        F: FnMut(&str) -> io::Result<()>,
    {
        if !matches!(request.action.as_str(), "begin" | "resume") {
            return Err(MaintenanceError::Invalid);
        }
        self.release_object_lease()?;
        self.clear_cached_state();
        let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let (runtime_path, runtime) =
            runtime_directory(&self.core_root, runtime_journal_directory)?;
        let root_digest = hash(self.core_root.to_string_lossy().as_bytes());
        let name = format!("corefs-maintenance-{root_digest}.journal");
        let existing = match read_bounded_in(&runtime, OsStr::new(&name), MAX_JOURNAL_BYTES + 32) {
            Ok(encoded) => Some(open::<Journal>(keyring, &self.core_id, "journal", &encoded)?.1),
            Err(e) if e.kind() == io::ErrorKind::NotFound => None,
            Err(e) => return Err(e.into()),
        };
        let mut journal = if request.action == "begin" {
            self.ensure_maintenance_write_allowed_locked()?;
            if existing
                .as_ref()
                .is_some_and(|j| !matches!(j.progress.state.as_str(), "complete" | "cancelled"))
            {
                return Err(MaintenanceError::Blocked);
            }
            let inventory = self.maintenance_inventory_locked(keyring, &request.policy)?;
            if !inventory.report.blockers.is_empty() {
                return Err(MaintenanceError::Blocked);
            }
            if inventory.report.inventory_digest != request.expected_inventory_digest {
                return Err(MaintenanceError::Conflict);
            }
            let head = self
                .load_pointer_head(HEAD_FILE)?
                .ok_or(MaintenanceError::Evidence)?;
            let (_, catalog) = self.load_pointer_for_head(keyring, head.clone())?;
            let keys = keyring
                .require(head.required_frk_version())
                .map_err(|_| MaintenanceError::Evidence)?;
            let journal = Journal {
                core_root_digest: root_digest.clone(),
                request: request.policy.clone(),
                expected_digest: request.expected_inventory_digest.clone(),
                head: MaintenancePin {
                    generation: head.generation(),
                    catalog_hash: head.catalog_hash().to_owned(),
                },
                candidates: Vec::new(),
                retained: inventory.records,
                progress: MaintenanceProgress {
                    id: hex(&rand::random::<[u8; 16]>()),
                    state: "reanchoring".to_owned(),
                    quarantined_count: 0,
                    swept_count: 0,
                    total_count: 3,
                    source_head_generation: head.generation(),
                    source_head_catalog_hash: head.catalog_hash().to_owned(),
                    kind: "reanchor".to_owned(),
                    expected_inventory_digest: request.expected_inventory_digest.clone(),
                },
                purge: None,
                reanchor: Some(ReanchorCheckpoint {
                    active_version: head.required_frk_version(),
                    cutover_epoch: catalog
                        .cutover_marker()
                        .ok_or(MaintenanceError::Evidence)?
                        .epoch(),
                }),
                referenced_frk_versions: inventory.report.required_frk_versions.clone(),
            };
            atomic_publish_in(
                &self.fs_dir,
                OsStr::new(REGISTRY),
                &seal(keys, &self.core_id, "registry", &inventory.registry)?,
            )?;
            self.save_maintenance_journal(&runtime_path, &runtime, &name, keys, &journal)?;
            atomic_publish_in(
                &self.fs_dir,
                OsStr::new(ACTIVE),
                &seal(keys, &self.core_id, "active", &journal.progress.id)?,
            )?;
            let mut source_pointers = BTreeMap::new();
            for pointer in [
                HEAD_FILE,
                CUTOVER_RECEIPT_FILE,
                CUTOVER_COMPLETE_FILE,
                VALIDATION_HEAD_FILE,
            ] {
                source_pointers.insert(
                    pointer.to_owned(),
                    self.load_pointer_head(pointer)?
                        .as_ref()
                        .map(encode_head)
                        .transpose()
                        .map_err(CommitError::from)?,
                );
            }
            let recovery = CoreReanchorRecovery {
                operation_id: journal.progress.id.clone(),
                target_head: encode_head(&head).map_err(CommitError::from)?,
                pointer_head: None,
                cutover_epoch: catalog
                    .cutover_marker()
                    .ok_or(MaintenanceError::Evidence)?
                    .epoch(),
                source_pointers,
            };
            atomic_publish_in(
                &self.fs_dir,
                OsStr::new(REANCHOR_RECOVERY),
                &seal(keys, &self.core_id, "reanchor-recovery", &recovery)?,
            )?;
            journal
        } else {
            existing.ok_or(MaintenanceError::Evidence)?
        };
        let checkpoint = journal.reanchor.as_ref().ok_or(MaintenanceError::Invalid)?;
        if journal.core_root_digest != root_digest
            || journal.expected_digest != request.expected_inventory_digest
        {
            return Err(MaintenanceError::Conflict);
        }
        let keys = keyring
            .require(checkpoint.active_version)
            .map_err(|_| MaintenanceError::Evidence)?;
        let head = self
            .load_pointer_head(HEAD_FILE)?
            .ok_or(MaintenanceError::Evidence)?;
        if head.generation() != journal.head.generation
            || head.catalog_hash() != journal.head.catalog_hash
        {
            return Err(MaintenanceError::Conflict);
        }
        if journal.progress.state != "complete" {
            self.ensure_active_journal_locked(keyring, &journal)?;
            let encoded =
                read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32)?;
            if open::<String>(keyring, &self.core_id, "active", &encoded)?.1 != journal.progress.id
            {
                return Err(MaintenanceError::Evidence);
            }
            let mut lineage_catalogs = Vec::new();
            for pointer in [
                HEAD_FILE,
                CUTOVER_RECEIPT_FILE,
                CUTOVER_COMPLETE_FILE,
                VALIDATION_HEAD_FILE,
            ] {
                if let Some(pointer_head) = self.load_pointer_head(pointer)? {
                    let generation = pointer_head.generation();
                    let (_, catalog) = self.load_pointer_for_head(keyring, pointer_head)?;
                    let is_validation_before_cutover = pointer == VALIDATION_HEAD_FILE
                        && catalog.cutover_marker().is_none()
                        && generation < head.generation();
                    if !is_validation_before_cutover
                        && catalog.cutover_marker().map(CatalogCutoverMarker::epoch)
                            != Some(checkpoint.cutover_epoch)
                    {
                        return Err(MaintenanceError::Evidence);
                    }
                    lineage_catalogs.push(catalog);
                }
            }
            if self.fs_dir.symlink_metadata(REANCHOR_RECOVERY).is_err() {
                let mut source_pointers = BTreeMap::new();
                for pointer in [
                    HEAD_FILE,
                    CUTOVER_RECEIPT_FILE,
                    CUTOVER_COMPLETE_FILE,
                    VALIDATION_HEAD_FILE,
                ] {
                    source_pointers.insert(
                        pointer.to_owned(),
                        self.load_pointer_head(pointer)?
                            .as_ref()
                            .map(encode_head)
                            .transpose()
                            .map_err(CommitError::from)?,
                    );
                }
                let recovery = CoreReanchorRecovery {
                    operation_id: journal.progress.id.clone(),
                    target_head: encode_head(&head).map_err(CommitError::from)?,
                    pointer_head: None,
                    cutover_epoch: checkpoint.cutover_epoch,
                    source_pointers,
                };
                atomic_publish_in(
                    &self.fs_dir,
                    OsStr::new(REANCHOR_RECOVERY),
                    &seal(keys, &self.core_id, "reanchor-recovery", &recovery)?,
                )?;
            }
            // Reauthenticate the complete inventory despite a temporarily mismatched receipt pair.
            let inventory = self.maintenance_inventory_locked(keyring, &journal.request)?;
            if inventory
                .report
                .blockers
                .iter()
                .any(|b| b != "head_lineage_corrupt")
            {
                return Err(MaintenanceError::Blocked);
            }
            if inventory.records.len() != journal.retained.len()
                || inventory.records.iter().any(|(name, r)| {
                    !journal
                        .retained
                        .get(name)
                        .is_some_and(|expected| same_record(r, expected))
                })
            {
                return Err(MaintenanceError::Conflict);
            }
            let encoded = encode_head(&head).map_err(CommitError::from)?;
            for pointer in [
                CUTOVER_RECEIPT_FILE,
                CUTOVER_COMPLETE_FILE,
                VALIDATION_HEAD_FILE,
            ] {
                self.validate_pinned_layout()?;
                atomic_publish_in(&self.fs_dir, OsStr::new(pointer), &encoded)?;
                hook(pointer)?;
            }
            self.clear_cached_state();
            journal.progress.state = "complete".to_owned();
            self.save_maintenance_journal(&runtime_path, &runtime, &name, keys, &journal)?;
            if self.fs_dir.symlink_metadata(REANCHOR_RECOVERY).is_ok() {
                durable_remove(&self.fs_dir, REANCHOR_RECOVERY)?;
            }
            self.fs_dir.remove_file(ACTIVE)?;
            sync_directory(&self.fs_dir)?;
        }
        let mut report = self
            .maintenance_inventory_locked(keyring, &journal.request)?
            .report;
        if !matches!(journal.progress.state.as_str(), "complete" | "cancelled") {
            report
                .required_frk_versions
                .extend(journal.referenced_frk_versions.iter().copied());
            report.required_frk_versions.sort_unstable();
            report.required_frk_versions.dedup();
        }
        if journal.progress.state == "complete" {
            match read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32) {
                Ok(bytes) => {
                    if open::<String>(keyring, &self.core_id, "active", &bytes)?.1
                        != journal.progress.id
                    {
                        return Err(MaintenanceError::Evidence);
                    }
                    durable_remove(&self.fs_dir, ACTIVE)?;
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.into()),
            }
        }
        report.operation = Some(journal.progress);
        Ok(report)
    }
}

fn same_record(left: &Candidate, right: &Candidate) -> bool {
    left.name == right.name
        && left.catalog == right.catalog
        && left.hash == right.hash
        && left.bytes == right.bytes
        && left.preparation_id == right.preparation_id
        && left.archive_files == right.archive_files
}

#[cfg(not(windows))]
fn sync_directory(dir: &Dir) -> io::Result<()> {
    crate::publication::sync_directory(dir)
}
#[cfg(windows)]
fn sync_directory(_dir: &Dir) -> io::Result<()> {
    Ok(())
}

#[cfg(windows)]
fn mutation_handle(dir: &Dir, name: &str) -> Result<cap_std::fs::File, MaintenanceError> {
    use cap_std::fs::OpenOptionsExt;
    use windows_sys::Win32::Storage::FileSystem::{
        DELETE, FILE_FLAG_WRITE_THROUGH, FILE_SHARE_READ,
    };
    let mut options = cap_std::fs::OpenOptions::new();
    options
        .read(true)
        .access_mode(windows_sys::Win32::Foundation::GENERIC_READ | DELETE)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_WRITE_THROUGH);
    let file = dir.open_with(name, &options)?;
    validate_opened_regular_file(dir, OsStr::new(name), &file.try_clone()?.into_std())?;
    if link_count(&file.metadata()?) != Some(1) {
        return Err(MaintenanceError::Evidence);
    }
    Ok(file)
}
fn durable_move(source: &Dir, name: &str, target: &Dir) -> Result<(), MaintenanceError> {
    #[cfg(windows)]
    {
        let file = mutation_handle(source, name)?;
        crate::publication::rename_file_by_handle(&file, target, OsStr::new(name), false)?;
    }
    #[cfg(not(windows))]
    {
        source.rename(name, target, name)?;
    }
    sync_directory(source)?;
    sync_directory(target)?;
    Ok(())
}
fn durable_remove(dir: &Dir, name: &str) -> Result<(), MaintenanceError> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            FileDispositionInfo, SetFileInformationByHandle, FILE_DISPOSITION_INFO,
        };
        let file = mutation_handle(dir, name)?;
        let disposition = FILE_DISPOSITION_INFO { DeleteFile: 1 };
        let result = unsafe {
            SetFileInformationByHandle(
                file.as_raw_handle(),
                FileDispositionInfo,
                (&disposition as *const FILE_DISPOSITION_INFO).cast(),
                std::mem::size_of::<FILE_DISPOSITION_INFO>() as u32,
            )
        };
        if result == 0 {
            return Err(io::Error::last_os_error().into());
        }
        drop(file);
    }
    #[cfg(not(windows))]
    {
        dir.remove_file(name)?;
    }
    sync_directory(dir)?;
    Ok(())
}

fn runtime_directory(core_root: &Path, path: &Path) -> Result<(PathBuf, Dir), MaintenanceError> {
    ensure_ambient_directory(path)?;
    let path = fs::canonicalize(path)?;
    if path.starts_with(core_root) || core_root.starts_with(&path) {
        return Err(MaintenanceError::Invalid);
    }
    let dir = Dir::open_ambient_dir(&path, ambient_authority())?;
    validate_ambient_linked_directory(&path, &dir)?;
    Ok((path, dir))
}

impl CoreCommitCoordinator {
    pub(super) fn ensure_maintenance_write_allowed_locked(&self) -> Result<(), CommitError> {
        match self.fs_dir.symlink_metadata(ACTIVE) {
            Ok(_) => Err(CommitError::MaintenanceActive),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err(CommitError::MaintenanceActive),
        }
    }
    pub fn maintenance_prune_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        runtime_journal_directory: &Path,
        request: &PruneRequest,
    ) -> Result<MaintenanceInventory, MaintenanceError> {
        if request.max_items == 0
            || request.max_items > 128
            || !matches!(request.action.as_str(), "begin" | "resume" | "cancel")
        {
            return Err(MaintenanceError::Invalid);
        }
        // Drain validation handles before taking the shared commit lock. Commit admission remains coordinated.
        self.release_object_lease()?;
        self.clear_cached_state();
        let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let (runtime_path, runtime) =
            runtime_directory(&self.core_root, runtime_journal_directory)?;
        let root_digest = hash(self.core_root.to_string_lossy().as_bytes());
        let journal_name = format!("corefs-maintenance-{root_digest}.journal");
        let quarantine = ensure_child_directory(&self.fs_dir, QUARANTINE)?;
        validate_linked_directory(&self.fs_dir, QUARANTINE, &quarantine)?;
        let existing =
            match read_bounded_in(&runtime, OsStr::new(&journal_name), MAX_JOURNAL_BYTES + 32) {
                Ok(encoded) => {
                    Some(open::<Journal>(keyring, &self.core_id, "journal", &encoded)?.1)
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => None,
                Err(e) => return Err(e.into()),
            };
        let mut journal = if request.action == "begin" {
            self.ensure_maintenance_write_allowed_locked()?;
            if existing
                .as_ref()
                .is_some_and(|j| !matches!(j.progress.state.as_str(), "complete" | "cancelled"))
            {
                return Err(MaintenanceError::Blocked);
            }
            if quarantine.entries()?.next().is_some() {
                return Err(MaintenanceError::Evidence);
            }
            let inventory = self.maintenance_inventory_locked(keyring, &request.policy)?;
            if !inventory.report.blockers.is_empty() {
                return Err(MaintenanceError::Blocked);
            }
            if inventory.report.inventory_digest != request.expected_inventory_digest {
                return Err(MaintenanceError::Conflict);
            }
            let names: BTreeSet<_> = inventory
                .candidates
                .iter()
                .map(|r| r.name.clone())
                .collect();
            let total = inventory.candidates.len();
            let journal = Journal {
                core_root_digest: root_digest.clone(),
                request: request.policy.clone(),
                expected_digest: request.expected_inventory_digest.clone(),
                head: MaintenancePin {
                    generation: inventory
                        .report
                        .head_generation
                        .ok_or(MaintenanceError::Evidence)?,
                    catalog_hash: inventory
                        .report
                        .head_catalog_hash
                        .clone()
                        .ok_or(MaintenanceError::Evidence)?,
                },
                candidates: inventory.candidates,
                retained: inventory
                    .records
                    .into_iter()
                    .filter(|(name, _)| !names.contains(name))
                    .collect(),
                progress: MaintenanceProgress {
                    id: hex(&rand::random::<[u8; 16]>()),
                    state: "quarantining".to_owned(),
                    quarantined_count: 0,
                    swept_count: 0,
                    total_count: total,
                    source_head_generation: inventory
                        .report
                        .head_generation
                        .ok_or(MaintenanceError::Evidence)?,
                    source_head_catalog_hash: inventory
                        .report
                        .head_catalog_hash
                        .clone()
                        .ok_or(MaintenanceError::Evidence)?,
                    kind: "prune".to_owned(),
                    expected_inventory_digest: request.expected_inventory_digest.clone(),
                },
                purge: None,
                reanchor: None,
                referenced_frk_versions: inventory.report.required_frk_versions.clone(),
            };
            let version = inventory.active_version.ok_or(MaintenanceError::Evidence)?;
            let keys = keyring
                .require(version)
                .map_err(|_| MaintenanceError::Evidence)?;
            atomic_publish_in(
                &self.fs_dir,
                OsStr::new(REGISTRY),
                &seal(keys, &self.core_id, "registry", &inventory.registry)?,
            )?;
            self.save_maintenance_journal(&runtime_path, &runtime, &journal_name, keys, &journal)?;
            // The Core marker blocks unrelated doctor/prune when the operational journal is lost.
            atomic_publish_in(
                &self.fs_dir,
                OsStr::new(ACTIVE),
                &seal(keys, &self.core_id, "active", &journal.progress.id)?,
            )?;
            journal
        } else {
            existing.ok_or(MaintenanceError::Evidence)?
        };
        if journal.core_root_digest != root_digest
            || journal.expected_digest != request.expected_inventory_digest
        {
            return Err(MaintenanceError::Conflict);
        }
        if journal.reanchor.is_some() {
            return Err(MaintenanceError::Invalid);
        }
        if journal.purge.is_some() && request.action == "cancel" {
            return Err(MaintenanceError::Blocked);
        }
        if !matches!(journal.progress.state.as_str(), "complete" | "cancelled") {
            if journal
                .referenced_frk_versions
                .iter()
                .any(|version| !keyring.contains(*version))
            {
                return Err(MaintenanceError::Evidence);
            }
            self.ensure_active_journal_locked(keyring, &journal)?;
            let encoded =
                read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32)?;
            let (_, id) = open::<String>(keyring, &self.core_id, "active", &encoded)?;
            if id != journal.progress.id {
                return Err(MaintenanceError::Evidence);
            }
        }
        let keys = keyring
            .require(
                self.load_pointer_head(HEAD_FILE)?
                    .ok_or(MaintenanceError::Evidence)?
                    .required_frk_version(),
            )
            .map_err(|_| MaintenanceError::Evidence)?;
        if request.action == "cancel" || journal.progress.state == "cancelling" {
            if journal.progress.swept_count != 0
                || journal.progress.state == "sweeping"
                || journal.progress.state == "complete"
            {
                return Err(MaintenanceError::Blocked);
            }
            journal.progress.state = "cancelling".to_owned();
            self.save_maintenance_journal(&runtime_path, &runtime, &journal_name, keys, &journal)?;
            let mut restored = 0;
            for record in journal.candidates.iter().rev() {
                let source = archive::source(self, record)?;
                match archive::record(&quarantine, record, journal.progress.state == "sweeping") {
                    Ok(actual) if same_record(&actual, record) => {
                        if restored >= request.max_items {
                            break;
                        }
                        if source.symlink_metadata(&record.name).is_ok() {
                            return Err(MaintenanceError::Evidence);
                        }
                        archive::move_candidate(&quarantine, &source, record)?;
                        restored += 1;
                    }
                    Err(MaintenanceError::Io(e)) if e.kind() == io::ErrorKind::NotFound => {
                        if !same_record(&archive::record(&source, record, false)?, record) {
                            return Err(MaintenanceError::Evidence);
                        }
                    }
                    _ => return Err(MaintenanceError::Evidence),
                }
            }
            let remaining = quarantine.entries()?.collect::<Result<Vec<_>, _>>()?.len();
            journal.progress.quarantined_count = remaining;
            if remaining == 0 {
                journal.progress.state = "cancelled".to_owned();
            }
            self.save_maintenance_journal(&runtime_path, &runtime, &journal_name, keys, &journal)?;
            if journal.progress.state == "cancelled" {
                durable_remove(&self.fs_dir, ACTIVE)?;
            }
        } else if !matches!(journal.progress.state.as_str(), "complete" | "cancelled") {
            if journal
                .referenced_frk_versions
                .iter()
                .any(|version| !keyring.contains(*version))
            {
                return Err(MaintenanceError::Evidence);
            }
            let inventory = self.maintenance_inventory_locked(keyring, &journal.request)?;
            if !inventory.report.blockers.is_empty() {
                return Err(MaintenanceError::Blocked);
            }
            if inventory.report.head_generation != Some(journal.head.generation)
                || inventory.report.head_catalog_hash.as_deref()
                    != Some(journal.head.catalog_hash.as_str())
            {
                return Err(MaintenanceError::Conflict);
            }
            let names: BTreeSet<_> = journal.candidates.iter().map(|r| r.name.clone()).collect();
            let retained: BTreeMap<_, _> = inventory
                .records
                .into_iter()
                .filter(|(name, _)| !names.contains(name))
                .collect();
            if retained.len() != journal.retained.len()
                || retained.iter().any(|(name, r)| {
                    !journal
                        .retained
                        .get(name)
                        .is_some_and(|expected| same_record(r, expected))
                })
            {
                return Err(MaintenanceError::Conflict);
            }
            if let Some(purge) = &journal.purge {
                if inventory.preparation_archives.iter().any(|archive| {
                    !names.contains(&archive.id)
                        && archive
                            .object_ids
                            .iter()
                            .any(|id| purge.target_ids.contains(id))
                }) {
                    return Err(MaintenanceError::Evidence);
                }
                for (name, (version, generation)) in &inventory.catalog_headers {
                    let catalog =
                        self.maintenance_read_catalog(keyring, name, *version, *generation)?;
                    if !names.contains(name)
                        && catalog
                            .entries()
                            .iter()
                            .any(|entry| purge.target_ids.contains(entry.stable_id().as_str()))
                    {
                        return Err(MaintenanceError::Evidence);
                    }
                }
            }
            let mut qnames = BTreeSet::new();
            for entry in quarantine.entries()? {
                let entry = entry?;
                let name = entry
                    .file_name()
                    .into_string()
                    .map_err(|_| MaintenanceError::Evidence)?;
                if !names.contains(&name) {
                    return Err(MaintenanceError::Evidence);
                }
                qnames.insert(name);
            }
            // Reconcile all physical states first; no mutation happens on partial evidence.
            for record in &journal.candidates {
                let source = archive::source(self, record)?;
                let actual = if qnames.contains(&record.name) {
                    if source.symlink_metadata(&record.name).is_ok() {
                        return Err(MaintenanceError::Evidence);
                    }
                    archive::record(&quarantine, record, journal.progress.state == "sweeping")
                } else {
                    archive::record(&source, record, false)
                };
                match actual {
                    Ok(actual) if same_record(&actual, record) => {}
                    Err(MaintenanceError::Io(e))
                        if e.kind() == io::ErrorKind::NotFound
                            && journal.progress.state == "sweeping"
                            && journal.progress.quarantined_count == journal.candidates.len() => {}
                    _ => return Err(MaintenanceError::Evidence),
                }
            }
            if journal.progress.state == "quarantined" {
                journal.progress.state = "sweeping".to_owned();
                self.save_maintenance_journal(
                    &runtime_path,
                    &runtime,
                    &journal_name,
                    keys,
                    &journal,
                )?;
            }
            let mut work = 0;
            if journal.progress.state == "quarantining" {
                for record in &journal.candidates {
                    if qnames.contains(&record.name) {
                        continue;
                    }
                    if work >= request.max_items {
                        break;
                    }
                    self.validate_pinned_layout()?;
                    validate_linked_directory(&self.fs_dir, QUARANTINE, &quarantine)?;
                    let source = archive::source(self, record)?;
                    archive::move_candidate(&source, &quarantine, record)?;
                    qnames.insert(record.name.clone());
                    work += 1;
                    journal.progress.quarantined_count = qnames.len();
                    self.save_maintenance_journal(
                        &runtime_path,
                        &runtime,
                        &journal_name,
                        keys,
                        &journal,
                    )?;
                }
                if qnames.len() == journal.candidates.len() {
                    journal.progress.quarantined_count = journal.candidates.len();
                    journal.progress.state = "quarantined".to_owned();
                }
            } else if journal.progress.state == "sweeping" {
                for record in &journal.candidates {
                    if !qnames.contains(&record.name) {
                        continue;
                    }
                    if work >= request.max_items {
                        break;
                    }
                    self.validate_pinned_layout()?;
                    validate_linked_directory(&self.fs_dir, QUARANTINE, &quarantine)?;
                    if !same_record(
                        &archive::record(
                            &quarantine,
                            record,
                            journal.progress.state == "sweeping",
                        )?,
                        record,
                    ) {
                        return Err(MaintenanceError::Evidence);
                    }
                    let mut budget = request.max_items - work;
                    let complete = archive::sweep(&quarantine, record, &mut budget)?;
                    work = request.max_items - budget;
                    if !complete {
                        break;
                    }
                    qnames.remove(&record.name);
                    journal.progress.swept_count = journal.candidates.len() - qnames.len();
                    self.save_maintenance_journal(
                        &runtime_path,
                        &runtime,
                        &journal_name,
                        keys,
                        &journal,
                    )?;
                }
                if qnames.is_empty() {
                    journal.progress.state = "complete".to_owned();
                    journal.progress.swept_count = journal.candidates.len();
                }
            }
            self.save_maintenance_journal(&runtime_path, &runtime, &journal_name, keys, &journal)?;
            if journal.progress.state == "complete" {
                self.fs_dir.remove_file(ACTIVE)?;
                sync_directory(&self.fs_dir)?;
            }
        }
        let mut report = self
            .maintenance_inventory_locked(keyring, &journal.request)?
            .report;
        if !matches!(journal.progress.state.as_str(), "complete" | "cancelled") {
            report
                .required_frk_versions
                .extend(journal.referenced_frk_versions.iter().copied());
            report.required_frk_versions.sort_unstable();
            report.required_frk_versions.dedup();
        }
        if matches!(journal.progress.state.as_str(), "complete" | "cancelled") {
            match read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32) {
                Ok(bytes) => {
                    if open::<String>(keyring, &self.core_id, "active", &bytes)?.1
                        != journal.progress.id
                    {
                        return Err(MaintenanceError::Evidence);
                    }
                    durable_remove(&self.fs_dir, ACTIVE)?;
                }
                Err(e) if e.kind() == io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.into()),
            }
        }
        let (resume, cancel) =
            self.maintenance_resume_authority_locked(keyring, &journal, &report)?;
        report.resume_authorized = resume;
        report.cancel_authorized = cancel;
        if resume {
            report.inventory_digest = journal.expected_digest.clone();
        }
        report.operation = Some(journal.progress);
        Ok(report)
    }

    fn save_maintenance_journal(
        &self,
        path: &Path,
        dir: &Dir,
        name: &str,
        keys: &FrkSubkeys,
        journal: &Journal,
    ) -> Result<(), MaintenanceError> {
        validate_ambient_linked_directory(path, dir)?;
        atomic_publish_in(
            dir,
            OsStr::new(name),
            &seal(keys, &self.core_id, "journal", journal)?,
        )?;
        validate_ambient_linked_directory(path, dir)?;
        Ok(())
    }
}

fn hex(value: &[u8]) -> String {
    value.iter().map(|b| format!("{b:02x}")).collect()
}
fn hash(value: &[u8]) -> String {
    hex(&Sha256::digest(value))
}
fn seal<T: Serialize>(
    keys: &FrkSubkeys,
    core_id: &str,
    purpose: &str,
    value: &T,
) -> Result<Vec<u8>, MaintenanceError> {
    let plain = serde_json::to_vec(value).map_err(|_| MaintenanceError::Evidence)?;
    if plain.len() > MAX_JOURNAL_BYTES {
        return Err(MaintenanceError::Evidence);
    }
    let cipher = Aes256Gcm::new_from_slice(keys.preparation().as_slice())
        .map_err(|_| MaintenanceError::Evidence)?;
    let mut nonce = [0; 12];
    getrandom::getrandom(&mut nonce).map_err(|_| MaintenanceError::Evidence)?;
    let aad = format!(
        "anima-maintenance-v1:{core_id}:{purpose}:{}",
        keys.frk_version()
    );
    let encrypted = cipher
        .encrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &plain,
                aad: aad.as_bytes(),
            },
        )
        .map_err(|_| MaintenanceError::Evidence)?;
    let mut out = keys.frk_version().to_be_bytes().to_vec();
    out.extend(nonce);
    out.extend(encrypted);
    Ok(out)
}
fn open<T: for<'a> Deserialize<'a>>(
    keyring: &FrkKeyring<'_>,
    core_id: &str,
    purpose: &str,
    encoded: &[u8],
) -> Result<(u32, T), MaintenanceError> {
    if encoded.len() < 32 || encoded.len() > MAX_JOURNAL_BYTES + 32 {
        return Err(MaintenanceError::Evidence);
    }
    let version = u32::from_be_bytes(
        encoded[..4]
            .try_into()
            .map_err(|_| MaintenanceError::Evidence)?,
    );
    let keys = keyring
        .require(version)
        .map_err(|_| MaintenanceError::Evidence)?;
    let cipher = Aes256Gcm::new_from_slice(keys.preparation().as_slice())
        .map_err(|_| MaintenanceError::Evidence)?;
    let aad = format!("anima-maintenance-v1:{core_id}:{purpose}:{version}");
    let plain = cipher
        .decrypt(
            Nonce::from_slice(&encoded[4..16]),
            Payload {
                msg: &encoded[16..],
                aad: aad.as_bytes(),
            },
        )
        .map_err(|_| MaintenanceError::Evidence)?;
    Ok((
        version,
        serde_json::from_slice(&plain).map_err(|_| MaintenanceError::Evidence)?,
    ))
}

fn file_record(dir: &Dir, name: &str, catalog: bool) -> Result<Candidate, MaintenanceError> {
    let file = open_regular_file_in(dir, OsStr::new(name))?;
    let metadata = Metadata::from_file(&file)?;
    // Unlike immutable-publication recovery, maintenance never accepts an alias.
    if link_count(&metadata) != Some(1) {
        return Err(MaintenanceError::Evidence);
    }
    let bytes = metadata.len();
    if bytes > MAX_ENVELOPE_SIZE {
        return Err(MaintenanceError::Evidence);
    }
    let mut sha = Sha256::new();
    let mut reader = file.take(MAX_ENVELOPE_SIZE + 1);
    let mut buffer = [0; 64 * 1024];
    loop {
        let n = reader.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        sha.update(&buffer[..n]);
    }
    Ok(Candidate {
        catalog,
        name: name.to_owned(),
        hash: hex(&sha.finalize()),
        bytes,
        preparation_id: None,
        archive_files: BTreeMap::new(),
    })
}

impl CoreCommitCoordinator {
    pub fn maintenance_inventory_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        request: &MaintenanceRequest,
    ) -> Result<MaintenanceInventory, MaintenanceError> {
        let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        self.validate_pinned_layout()?;
        let inventory = self.maintenance_inventory_locked(keyring, request)?;
        // Canonical age evidence is durable and authenticated; no filesystem mtime is trusted.
        if inventory.report.blockers.is_empty() {
            if let Some(version) = inventory.active_version {
                let encoded = seal(
                    keyring
                        .require(version)
                        .map_err(|_| MaintenanceError::Evidence)?,
                    &self.core_id,
                    "registry",
                    &inventory.registry,
                )?;
                atomic_publish_in(&self.fs_dir, OsStr::new(REGISTRY), &encoded)?;
            }
        }
        let mut report = inventory.report;
        if self.fs_dir.symlink_metadata(ACTIVE).is_ok() {
            report
                .blockers
                .push("maintenance_operation_pending".to_owned());
            report.candidate_catalog_count = 0;
            report.candidate_object_count = 0;
            report.candidate_bytes = 0;
        }
        if let Ok(quarantine) = self.fs_dir.open_dir(QUARANTINE) {
            if quarantine.entries()?.next().is_some()
                && !report
                    .blockers
                    .iter()
                    .any(|b| b == "maintenance_operation_pending")
            {
                report
                    .blockers
                    .push("maintenance_quarantine_without_journal".to_owned());
                report.candidate_catalog_count = 0;
                report.candidate_object_count = 0;
                report.candidate_bytes = 0;
            }
        }
        Ok(report)
    }

    fn maintenance_inventory_locked(
        &self,
        keyring: &FrkKeyring<'_>,
        request: &MaintenanceRequest,
    ) -> Result<Inventory, MaintenanceError> {
        self.maintenance_inventory_locked_with_budget(
            keyring,
            request,
            MAX_CATALOG_AGGREGATE_BYTES,
            MAX_CATALOG_AGGREGATE_ENTRIES,
        )
    }

    fn maintenance_inventory_locked_with_budget(
        &self,
        keyring: &FrkKeyring<'_>,
        request: &MaintenanceRequest,
        max_bytes: u64,
        max_entries: u64,
    ) -> Result<Inventory, MaintenanceError> {
        let wall_now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| MaintenanceError::Invalid)?
            .as_millis() as u64;
        if request.retention_days != 30
            || request.now_ms == 0
            || request.now_ms > wall_now.saturating_add(60_000)
            || request.pins.len() > 128
        {
            return Err(MaintenanceError::Invalid);
        }
        self.validate_pinned_layout()?;
        let mut blockers = BTreeSet::new();
        let mut required = BTreeSet::new();
        let mut registry =
            match read_bounded_in(&self.fs_dir, OsStr::new(REGISTRY), 64 * 1024 * 1024 + 32) {
                Ok(encoded) => match open::<Registry>(keyring, &self.core_id, "registry", &encoded)
                {
                    Ok((version, registry)) => {
                        required.insert(version);
                        registry
                    }
                    Err(_) => {
                        blockers.insert("retention_registry_corrupt".to_owned());
                        Registry::default()
                    }
                },
                Err(e) if e.kind() == io::ErrorKind::NotFound => Registry::default(),
                Err(_) => {
                    blockers.insert("retention_registry_unsafe".to_owned());
                    Registry::default()
                }
            };
        let mut protected = BTreeSet::new();
        match read_bounded_in(&self.fs_dir, OsStr::new(REANCHOR_RECOVERY), 64 * 1024) {
            Ok(encoded) => match open::<CoreReanchorRecovery>(
                keyring,
                &self.core_id,
                "reanchor-recovery",
                &encoded,
            ) {
                Ok((version, recovery)) => {
                    required.insert(version);
                    let refs = std::iter::once(Some(recovery.target_head))
                        .chain(std::iter::once(recovery.pointer_head))
                        .chain(recovery.source_pointers.into_values());
                    for bytes in refs.flatten() {
                        match decode_head(&bytes) {
                            Ok(head) => {
                                required.insert(head.required_frk_version());
                                protected.insert(format!(
                                    "catalog-{:020}-{}.acore",
                                    head.generation(),
                                    head.catalog_hash()
                                ));
                            }
                            Err(_) => {
                                blockers.insert("reanchor_checkpoint_corrupt".to_owned());
                            }
                        }
                    }
                }
                Err(_) => {
                    blockers.insert("reanchor_checkpoint_corrupt".to_owned());
                }
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound => {}
            Err(_) => {
                blockers.insert("reanchor_checkpoint_corrupt".to_owned());
            }
        }
        let mut head = None;
        for pointer in [
            HEAD_FILE,
            VALIDATION_HEAD_FILE,
            CUTOVER_RECEIPT_FILE,
            CUTOVER_COMPLETE_FILE,
        ] {
            match self.load_pointer_head(pointer) {
                Ok(Some(value)) => {
                    required.insert(value.required_frk_version());
                    protected.insert(format!(
                        "catalog-{:020}-{}.acore",
                        value.generation(),
                        value.catalog_hash()
                    ));
                    if pointer == HEAD_FILE {
                        head = Some(value);
                    }
                }
                Ok(None) => {}
                Err(_) => {
                    blockers.insert("special_pointer_corrupt".to_owned());
                }
            }
        }
        if head.is_none() {
            blockers.insert("authenticated_head_required".to_owned());
        }
        let pointers = self.load_pointer_set(
            #[cfg(test)]
            None,
        );
        if let Ok(pointers) = pointers {
            if self
                .load_committed_once_with_keyring_heads_inner(
                    keyring,
                    pointers.head,
                    pointers.receipt,
                    pointers.complete,
                    #[cfg(test)]
                    None,
                )
                .is_err()
            {
                blockers.insert("head_lineage_corrupt".to_owned());
            }
        }
        // Active or quarantined preparation never becomes evidence of orphanhood.
        if self
            .ensure_preparation_rotation_allowed_locked(keyring)
            .is_err()
        {
            blockers.insert("preparation_active_or_corrupt".to_owned());
        }
        let preparation_versions = match self.quarantined_preparation_frk_versions_locked(keyring) {
            Ok(versions) => versions,
            Err(_) => {
                blockers.insert("preparation_quarantine_unsafe".to_owned());
                Vec::new()
            }
        };
        required.extend(preparation_versions.iter().copied());
        let mut catalogs = BTreeMap::new();
        let mut catalog_headers = BTreeMap::new();
        let mut aggregate_bytes = 0u64;
        let mut aggregate_entries = 0u64;
        let mut records = BTreeMap::new();
        let mut generations = BTreeSet::new();
        let mut physical_catalog_count = 0;
        for entry in self.catalogs_dir.entries()? {
            physical_catalog_count += 1;
            if physical_catalog_count > MAX_CATALOG_FILES {
                blockers.insert("inventory_limit".to_owned());
                break;
            }
            let entry = entry?;
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                blockers.insert("catalog_name_invalid".to_owned());
                continue;
            };
            let record = match file_record(&self.catalogs_dir, &name, true) {
                Ok(r) => r,
                Err(_) => {
                    blockers.insert("catalog_file_unsafe".to_owned());
                    continue;
                }
            };
            let encoded = match read_bounded_in(
                &self.catalogs_dir,
                OsStr::new(&name),
                MAX_CATALOG_ENVELOPE_SIZE,
            ) {
                Ok(e) => e,
                Err(_) => {
                    blockers.insert("catalog_corrupt".to_owned());
                    continue;
                }
            };
            if catalog_generation_physical_name(&encoded).ok().as_deref() != Some(name.as_str()) {
                blockers.insert("catalog_binding_corrupt".to_owned());
                continue;
            }
            let opened = keyring.versions().into_iter().find_map(|version| {
                decrypt_catalog_generation(keyring.require(version).ok()?, &self.core_id, &encoded)
                    .ok()
                    .map(|catalog| (version, catalog))
            });
            let Some((version, catalog)) = opened else {
                blockers.insert("catalog_authentication_failed".to_owned());
                continue;
            };
            aggregate_bytes = aggregate_bytes.saturating_add(encoded.len() as u64);
            aggregate_entries = aggregate_entries.saturating_add(catalog.entries().len() as u64);
            if aggregate_bytes > max_bytes || aggregate_entries > max_entries {
                blockers.insert("catalog_aggregate_limit".to_owned());
                break;
            }
            if !generations.insert(catalog.generation()) {
                blockers.insert("catalog_generation_ambiguous".to_owned());
            }
            if head
                .as_ref()
                .is_some_and(|head| catalog.generation() > head.generation())
            {
                protected.insert(name.clone());
            }
            records.insert(name.clone(), record);
            catalog_headers.insert(name.clone(), (version, catalog.generation()));
            if head.as_ref().is_some_and(|head| {
                head.generation() == catalog.generation()
                    && name
                        == format!(
                            "catalog-{:020}-{}.acore",
                            head.generation(),
                            head.catalog_hash()
                        )
            }) {
                catalogs.insert(name, (version, catalog));
            }
        }
        for name in &protected {
            if !catalog_headers.contains_key(name) {
                blockers.insert("special_pointer_catalog_missing".to_owned());
            }
        }
        for pin in &request.pins {
            let name = format!("catalog-{:020}-{}.acore", pin.generation, pin.catalog_hash);
            if !catalog_headers.contains_key(&name) {
                blockers.insert("pin_catalog_missing_or_invalid".to_owned());
            }
            protected.insert(name);
        }
        if let Some(head) = &head {
            if let Some((name, _)) = catalog_headers
                .iter()
                .filter(|(_, (_, generation))| *generation < head.generation())
                .max_by_key(|(_, (_, generation))| *generation)
            {
                protected.insert(name.clone());
            }
        }
        let cutoff = request
            .now_ms
            .saturating_sub(u64::from(request.retention_days) * DAY_MS);
        let prep_inventory = match self.maintenance_preparation_pins_locked(keyring) {
            Ok(value) => value,
            Err(_) => {
                blockers.insert("preparation_archive_incomplete_or_corrupt".to_owned());
                preparation::MaintenancePreparationPins::default()
            }
        };
        let mut prep_records = BTreeMap::new();
        let mut eligible_preparations = BTreeSet::new();
        let mut prep_reachable = BTreeSet::new();
        if !prep_inventory.archives.is_empty() {
            let parent = self.fs_dir.open_dir("preparations")?;
            validate_linked_directory(&self.fs_dir, "preparations", &parent)?;
            for archived in &prep_inventory.archives {
                required.extend(archived.frk_versions.iter().copied());
                let record = archive::read(&parent, &archived.id, false)?;
                let age_key = format!("preparation-{}-{}", archived.id, archived.terminal_digest);
                let first = *registry.observed.entry(age_key).or_insert(request.now_ms);
                if first == 0 || first > request.now_ms {
                    blockers.insert("preparation_timestamp_ambiguous".to_owned());
                }
                let covered = archived.published.as_ref().map_or(true, |(generation, _)| {
                    head.as_ref()
                        .is_some_and(|head| head.generation() >= *generation)
                });
                let eligible =
                    first > 0 && first < cutoff && covered && preparation_versions.is_empty();
                if eligible {
                    eligible_preparations.insert(archived.id.clone());
                } else {
                    required.extend(archived.frk_versions.iter().copied());
                    prep_reachable.extend(archived.physical_names.iter().cloned());
                    for (generation, hash) in &archived.catalog_pins {
                        let name = format!("catalog-{generation:020}-{hash}.acore");
                        if !catalog_headers.contains_key(&name) {
                            blockers.insert("preparation_catalog_pin_missing".to_owned());
                        }
                        protected.insert(name);
                    }
                }
                prep_records.insert(archived.id.clone(), record);
            }
        }
        if !preparation_versions.is_empty() {
            protected.extend(catalog_headers.keys().cloned());
        }
        let mut unknown = 0;
        for name in catalog_headers.keys() {
            match registry.observed.get(name) {
                Some(first) if *first > request.now_ms || *first == 0 => {
                    blockers.insert("retention_timestamp_ambiguous".to_owned());
                    protected.insert(name.clone());
                }
                Some(first) if *first < cutoff => {}
                Some(_) => {
                    protected.insert(name.clone());
                }
                None => {
                    unknown += 1;
                    protected.insert(name.clone());
                    registry.observed.insert(name.clone(), request.now_ms);
                }
            }
        }
        let mut reachable = BTreeSet::new();
        reachable.extend(prep_reachable);
        let mut verified_bindings = BTreeSet::new();
        for (name, (version, generation)) in &catalog_headers {
            let catalog = self.maintenance_read_catalog(keyring, name, *version, *generation)?;
            required.insert(*version);
            if protected.contains(name) {
                required.insert(*version);
            }
            for entry in catalog.entries() {
                if let Some(object) = entry.object_payload() {
                    let version = object.wrapped_dek().frk_version();
                    required.insert(version);
                    if !keyring.contains(version) {
                        blockers.insert("object_wrapping_key_missing".to_owned());
                    }
                    let key_binding = keyring
                        .require(version)
                        .map_err(CommitError::from)
                        .and_then(|keys| {
                            catalog_object_key_binding(
                                keys,
                                &self.core_id,
                                entry.stable_id(),
                                object,
                                #[cfg(test)]
                                None,
                            )
                        });
                    let Ok(key_binding) = key_binding else {
                        blockers.insert("retained_object_authentication_failed".to_owned());
                        continue;
                    };
                    let binding = (
                        object.physical_name().as_str().to_owned(),
                        entry.stable_id().as_str().to_owned(),
                        object.revision(),
                        version,
                        key_binding,
                    );
                    if verified_bindings.insert(binding) {
                        if verified_bindings.len() > MAX_OBJECT_FILES {
                            blockers.insert("inventory_limit".to_owned());
                        } else {
                            let authentication = (|| -> Result<(), CommitError> {
                                let keys = keyring.require(version)?;
                                let key_aad = ObjectKeyAad::new(
                                    &self.core_id,
                                    entry.stable_id().as_str(),
                                    object.revision(),
                                    object.kind(),
                                    OBJECT_KEY_ENVELOPE_VERSION,
                                    object.object_key_epoch(),
                                    version,
                                )?;
                                let dek = unwrap_object_dek(
                                    keys,
                                    &object.wrapped_dek().to_wrapped_object_dek()?,
                                    &key_aad,
                                )?;
                                let aad = ObjectBaseAad::new(
                                    &self.core_id,
                                    entry.stable_id().as_str(),
                                    object.kind(),
                                    crate::envelope::ENVELOPE_VERSION,
                                    object.object_key_epoch(),
                                    object.revision(),
                                )?;
                                let mut file = open_regular_file_in(
                                    &self.objects_dir,
                                    OsStr::new(object.physical_name().as_str()),
                                )?;
                                if file.metadata()?.len() > MAX_ENVELOPE_SIZE {
                                    return Err(CommitError::ObjectEnvelopeTooLarge);
                                }
                                let read = read_envelope(&mut file, &dek, &aad, &mut io::sink())?;
                                if read.metadata.body_sha256 != object.content_hash().as_str() {
                                    return Err(CommitError::InvalidObjectRevision);
                                }
                                Ok(())
                            })();
                            if authentication.is_err() {
                                blockers.insert("retained_object_authentication_failed".to_owned());
                            }
                        }
                    }
                    if protected.contains(name) {
                        reachable.insert(object.physical_name().as_str().to_owned());
                        required.insert(version);
                    }
                }
            }
        }
        let mut objects = BTreeMap::new();
        let mut physical_object_count = 0;
        for entry in self.objects_dir.entries()? {
            physical_object_count += 1;
            if physical_object_count > MAX_OBJECT_FILES {
                blockers.insert("inventory_limit".to_owned());
                break;
            }
            let entry = entry?;
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                blockers.insert("object_name_invalid".to_owned());
                continue;
            };
            if ObjectPhysicalName::parse(&name).is_err() {
                blockers.insert("object_inventory_ambiguous".to_owned());
                continue;
            }
            match file_record(&self.objects_dir, &name, false) {
                Ok(record) => {
                    objects.insert(name, record);
                }
                Err(_) => {
                    blockers.insert("object_file_unsafe".to_owned());
                }
            }
        }
        for name in &reachable {
            if !objects.contains_key(name) {
                blockers.insert("retained_object_missing".to_owned());
            }
        }
        if !preparation_versions.is_empty() {
            reachable.extend(objects.keys().cloned());
        }
        let mut sha = Sha256::new();
        sha.update(self.core_id.as_bytes());
        sha.update(serde_json::to_vec(&request.pins).map_err(|_| MaintenanceError::Invalid)?);
        for record in records
            .values()
            .chain(objects.values())
            .chain(prep_records.values())
        {
            sha.update(record.name.as_bytes());
            sha.update(record.hash.as_bytes());
            sha.update(record.bytes.to_be_bytes());
        }
        for name in &protected {
            sha.update(name.as_bytes());
        }
        if let Some(head) = &head {
            sha.update(head.catalog_hash().as_bytes());
        }
        let candidates: Vec<_> = if blockers.is_empty() {
            prep_records
                .iter()
                .filter(|(name, _)| eligible_preparations.contains(*name))
                .map(|(_, record)| record.clone())
                .chain(
                    records
                        .iter()
                        .filter(|(name, _)| !protected.contains(*name))
                        .map(|(_, record)| record.clone())
                        .chain(
                            objects
                                .iter()
                                .filter(|(name, _)| !reachable.contains(*name))
                                .map(|(_, record)| record.clone()),
                        ),
                )
                .collect()
        } else {
            Vec::new()
        };
        let report = MaintenanceInventory {
            version: 1,
            head_generation: head.as_ref().map(HeadRecord::generation),
            head_catalog_hash: head.as_ref().map(|h| h.catalog_hash().to_owned()),
            inventory_digest: hex(&sha.finalize()),
            retained_catalog_count: protected.len(),
            retained_unknown_age_count: unknown,
            catalog_count: catalog_headers.len(),
            object_count: objects.len(),
            candidate_catalog_count: candidates.iter().filter(|r| r.catalog).count(),
            candidate_object_count: candidates
                .iter()
                .filter(|r| !r.catalog && r.preparation_id.is_none())
                .count(),
            candidate_preparation_count: candidates
                .iter()
                .filter(|r| r.preparation_id.is_some())
                .count(),
            candidate_bytes: candidates.iter().map(|r| r.bytes).sum(),
            required_frk_versions: required.into_iter().collect(),
            blockers: blockers.into_iter().collect(),
            warnings: if unknown > 0 {
                vec!["older_catalogs_retained_from_first_authenticated_observation".to_owned()]
            } else {
                Vec::new()
            },
            operation: None,
            resume_authorized: false,
            cancel_authorized: false,
        };
        records.extend(objects);
        records.extend(prep_records);
        Ok(Inventory {
            active_version: head.map(|h| h.required_frk_version()),
            report,
            candidates,
            catalogs,
            catalog_headers,
            registry,
            records,
            preparation_archives: prep_inventory.archives,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PurgeInventoryRequest {
    #[serde(flatten)]
    pub policy: MaintenanceRequest,
    #[serde(default)]
    pub target_stable_id: String,
    #[serde(default)]
    pub expected_revision: u64,
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PurgeInventory {
    pub inventory: MaintenanceInventory,
    pub target_stable_id: String,
    pub expected_revision: u64,
    pub recursive_inventory_digest: String,
    pub required_waived_generations: Vec<u64>,
    pub object_count: usize,
    pub blockers: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PurgeRequest {
    #[serde(flatten)]
    pub target: PurgeInventoryRequest,
    pub expected_inventory_digest: String,
    #[serde(default)]
    pub recursive_inventory_digest: String,
    #[serde(default)]
    pub waived_generations: Vec<u64>,
    pub action: String,
    pub max_items: usize,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PurgeCheckpoint {
    target_stable_id: String,
    expected_revision: u64,
    target_ids: BTreeSet<String>,
    recursive_digest: String,
    waived_generations: Vec<u64>,
    predecessor: MaintenancePin,
    published: MaintenancePin,
    active_version: u32,
    cutover_epoch: u64,
}

impl CoreCommitCoordinator {
    pub fn maintenance_purge_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        runtime_journal_directory: &Path,
        request: &PurgeRequest,
    ) -> Result<MaintenanceInventory, MaintenanceError> {
        self.maintenance_purge_with_hook(keyring, runtime_journal_directory, request, &mut |_| {
            Ok(())
        })
    }

    fn maintenance_purge_with_hook<F>(
        &self,
        keyring: &FrkKeyring<'_>,
        runtime_journal_directory: &Path,
        request: &PurgeRequest,
        hook: &mut F,
    ) -> Result<MaintenanceInventory, MaintenanceError>
    where
        F: FnMut(&str) -> io::Result<()>,
    {
        if request.max_items == 0
            || request.max_items > 128
            || !matches!(request.action.as_str(), "begin" | "resume" | "cancel")
        {
            return Err(MaintenanceError::Invalid);
        }
        if request.action == "cancel" {
            return Err(MaintenanceError::Blocked);
        }
        self.release_object_lease()?;
        self.clear_cached_state();
        {
            let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
            self.validate_pinned_layout()?;
            let (runtime_path, runtime) =
                runtime_directory(&self.core_root, runtime_journal_directory)?;
            let root_digest = hash(self.core_root.to_string_lossy().as_bytes());
            let journal_name = format!("corefs-maintenance-{root_digest}.journal");
            let existing = match read_bounded_in(
                &runtime,
                OsStr::new(&journal_name),
                MAX_JOURNAL_BYTES + 32,
            ) {
                Ok(bytes) => Some(open::<Journal>(keyring, &self.core_id, "journal", &bytes)?.1),
                Err(e) if e.kind() == io::ErrorKind::NotFound => None,
                Err(e) => return Err(e.into()),
            };
            let mut journal = if request.action == "begin" {
                if existing
                    .as_ref()
                    .is_some_and(|j| !matches!(j.progress.state.as_str(), "complete" | "cancelled"))
                    || self.fs_dir.symlink_metadata(ACTIVE).is_ok()
                {
                    return Err(MaintenanceError::Blocked);
                }
                if let Ok(q) = self.fs_dir.open_dir(QUARANTINE) {
                    if q.entries()?.next().is_some() {
                        return Err(MaintenanceError::Evidence);
                    }
                }
                let inventory =
                    self.maintenance_inventory_locked(keyring, &request.target.policy)?;
                let (review, ids) = purge_inventory(self, keyring, &inventory, &request.target)?;
                if !review.blockers.is_empty() {
                    return Err(MaintenanceError::Blocked);
                }
                if review.inventory.inventory_digest != request.expected_inventory_digest
                    || review.recursive_inventory_digest != request.recursive_inventory_digest
                    || review.required_waived_generations != request.waived_generations
                {
                    return Err(MaintenanceError::Conflict);
                }
                let source_generation = review
                    .inventory
                    .head_generation
                    .ok_or(MaintenanceError::Evidence)?;
                let source_hash = review
                    .inventory
                    .head_catalog_hash
                    .ok_or(MaintenanceError::Evidence)?;
                let source_name = format!("catalog-{source_generation:020}-{source_hash}.acore");
                let (active_version, current) = inventory
                    .catalogs
                    .get(&source_name)
                    .ok_or(MaintenanceError::Evidence)?;
                let keys = keyring
                    .require(*active_version)
                    .map_err(|_| MaintenanceError::Evidence)?;
                let epoch = current
                    .cutover_marker()
                    .ok_or(MaintenanceError::Evidence)?
                    .epoch();
                let mut object_names = BTreeSet::new();
                let mut candidate_names = BTreeSet::new();
                for (name, (version, generation)) in &inventory.catalog_headers {
                    let catalog =
                        self.maintenance_read_catalog(keyring, name, *version, *generation)?;
                    if request.waived_generations.contains(&catalog.generation()) {
                        if !catalog
                            .entries()
                            .iter()
                            .any(|entry| ids.contains(entry.stable_id().as_str()))
                        {
                            return Err(MaintenanceError::Evidence);
                        }
                        candidate_names.insert(name.clone());
                        for entry in catalog
                            .entries()
                            .iter()
                            .filter(|entry| ids.contains(entry.stable_id().as_str()))
                        {
                            if let Some(object) = entry.object_payload() {
                                object_names.insert(object.physical_name().as_str().to_owned());
                            }
                        }
                    }
                }
                for (name, (version, generation)) in &inventory.catalog_headers {
                    let catalog =
                        self.maintenance_read_catalog(keyring, name, *version, *generation)?;
                    if !candidate_names.contains(name)
                        && catalog
                            .entries()
                            .iter()
                            .filter_map(|entry| entry.object_payload())
                            .any(|object| object_names.contains(object.physical_name().as_str()))
                    {
                        return Err(MaintenanceError::Evidence);
                    }
                }
                candidate_names.extend(object_names);
                for archived in &inventory.preparation_archives {
                    if archived.object_ids.iter().any(|id| ids.contains(id)) {
                        let (generation, hash) = archived
                            .published
                            .as_ref()
                            .ok_or(MaintenanceError::Blocked)?;
                        let catalog_name = format!("catalog-{generation:020}-{hash}.acore");
                        if !request.waived_generations.contains(generation)
                            || !candidate_names.contains(&catalog_name)
                        {
                            return Err(MaintenanceError::Blocked);
                        }
                        candidate_names.insert(archived.id.clone());
                        for (id, names) in &archived.objects {
                            if ids.contains(id) {
                                candidate_names.extend(names.iter().cloned());
                            }
                        }
                    }
                }
                let candidates: Vec<_> = candidate_names
                    .iter()
                    .map(|name| {
                        inventory
                            .records
                            .get(name)
                            .cloned()
                            .ok_or(MaintenanceError::Evidence)
                    })
                    .collect::<Result<_, _>>()?;
                let clean_entries: Vec<_> = current
                    .entries()
                    .iter()
                    .filter(|entry| !ids.contains(entry.stable_id().as_str()))
                    .cloned()
                    .collect();
                let clean_generation = source_generation
                    .checked_add(1)
                    .ok_or(MaintenanceError::Evidence)?;
                let published_generation = source_generation
                    .checked_add(2)
                    .ok_or(MaintenanceError::Evidence)?;
                let predecessor = self.stage_purge_catalog(
                    keys,
                    CatalogGeneration::new(clean_generation, clean_entries.clone())
                        .map_err(CommitError::from)?
                        .with_cutover_marker(
                            CatalogCutoverMarker::new(epoch).map_err(CommitError::from)?,
                        )
                        .map_err(CommitError::from)?,
                )?;
                let published = self.stage_purge_catalog(
                    keys,
                    CatalogGeneration::new(published_generation, clean_entries)
                        .map_err(CommitError::from)?
                        .with_cutover_marker(
                            CatalogCutoverMarker::new(epoch).map_err(CommitError::from)?,
                        )
                        .map_err(CommitError::from)?,
                )?;
                let journal = Journal {
                    core_root_digest: root_digest.clone(),
                    request: request.target.policy.clone(),
                    expected_digest: request.expected_inventory_digest.clone(),
                    head: published.clone(),
                    candidates,
                    retained: BTreeMap::new(),
                    progress: MaintenanceProgress {
                        id: hex(&rand::random::<[u8; 16]>()),
                        state: "publishing".to_owned(),
                        quarantined_count: 0,
                        swept_count: 0,
                        total_count: candidate_names.len(),
                        source_head_generation: source_generation,
                        source_head_catalog_hash: source_hash,
                        kind: "purge".to_owned(),
                        expected_inventory_digest: request.expected_inventory_digest.clone(),
                    },
                    purge: Some(PurgeCheckpoint {
                        target_stable_id: request.target.target_stable_id.clone(),
                        expected_revision: request.target.expected_revision,
                        target_ids: ids,
                        recursive_digest: request.recursive_inventory_digest.clone(),
                        waived_generations: request.waived_generations.clone(),
                        predecessor,
                        published,
                        active_version: *active_version,
                        cutover_epoch: epoch,
                    }),
                    reanchor: None,
                    referenced_frk_versions: inventory.report.required_frk_versions.clone(),
                };
                atomic_publish_in(
                    &self.fs_dir,
                    OsStr::new(REGISTRY),
                    &seal(keys, &self.core_id, "registry", &inventory.registry)?,
                )?;
                self.save_maintenance_journal(
                    &runtime_path,
                    &runtime,
                    &journal_name,
                    keys,
                    &journal,
                )?;
                atomic_publish_in(
                    &self.fs_dir,
                    OsStr::new(ACTIVE),
                    &seal(keys, &self.core_id, "active", &journal.progress.id)?,
                )?;
                journal
            } else {
                existing.ok_or(MaintenanceError::Evidence)?
            };
            let purge = journal.purge.as_ref().ok_or(MaintenanceError::Invalid)?;
            if journal.core_root_digest != root_digest
                || journal.expected_digest != request.expected_inventory_digest
                || (!request.recursive_inventory_digest.is_empty()
                    && purge.recursive_digest != request.recursive_inventory_digest)
                || (!request.waived_generations.is_empty()
                    && purge.waived_generations != request.waived_generations)
                || (!request.target.target_stable_id.is_empty()
                    && purge.target_stable_id != request.target.target_stable_id)
                || (request.target.expected_revision > 0
                    && purge.expected_revision != request.target.expected_revision)
            {
                return Err(MaintenanceError::Conflict);
            }
            let keys = keyring
                .require(purge.active_version)
                .map_err(|_| MaintenanceError::Evidence)?;
            if journal.progress.state == "publishing" {
                self.ensure_active_journal_locked(keyring, &journal)?;
                let bytes =
                    read_bounded_in(&self.fs_dir, OsStr::new(ACTIVE), MAX_JOURNAL_BYTES + 32)?;
                if open::<String>(keyring, &self.core_id, "active", &bytes)?.1
                    != journal.progress.id
                {
                    return Err(MaintenanceError::Evidence);
                }
                let head = self
                    .load_pointer_head(HEAD_FILE)?
                    .ok_or(MaintenanceError::Evidence)?;
                let is_source = head.generation() == journal.progress.source_head_generation
                    && head.catalog_hash() == journal.progress.source_head_catalog_hash;
                let is_published = head.generation() == purge.published.generation
                    && head.catalog_hash() == purge.published.catalog_hash;
                if !is_source && !is_published {
                    return Err(MaintenanceError::Conflict);
                }
                // Every old/new pointer must authenticate the same irreversible marker.
                for pointer in [
                    HEAD_FILE,
                    VALIDATION_HEAD_FILE,
                    CUTOVER_RECEIPT_FILE,
                    CUTOVER_COMPLETE_FILE,
                ] {
                    if let Some(head) = self.load_pointer_head(pointer)? {
                        let generation = head.generation();
                        let (_, catalog) = self.load_pointer_for_head(keyring, head)?;
                        let is_validation_before_cutover = pointer == VALIDATION_HEAD_FILE
                            && catalog.cutover_marker().is_none()
                            && generation < journal.progress.source_head_generation;
                        if !is_validation_before_cutover
                            && catalog.cutover_marker().map(CatalogCutoverMarker::epoch)
                                != Some(purge.cutover_epoch)
                        {
                            return Err(MaintenanceError::Evidence);
                        }
                    }
                }
                let predecessor =
                    self.purge_head_record(keyring, &purge.predecessor, purge.active_version)?;
                let published =
                    self.purge_head_record(keyring, &purge.published, purge.active_version)?;
                atomic_publish_in(
                    &self.fs_dir,
                    OsStr::new(HEAD_FILE),
                    &encode_head(&published).map_err(CommitError::from)?,
                )?;
                hook(HEAD_FILE)?;
                // Durable Core evidence permits ordinary startup to repair a partial pointer pair.
                // HEAD is already published; recovery cannot publish content or authorize deletion.
                let mut source_pointers = BTreeMap::new();
                for pointer in [
                    HEAD_FILE,
                    CUTOVER_RECEIPT_FILE,
                    CUTOVER_COMPLETE_FILE,
                    VALIDATION_HEAD_FILE,
                ] {
                    source_pointers.insert(
                        pointer.to_owned(),
                        self.load_pointer_head(pointer)?
                            .as_ref()
                            .map(encode_head)
                            .transpose()
                            .map_err(CommitError::from)?,
                    );
                }
                let recovery = CoreReanchorRecovery {
                    operation_id: journal.progress.id.clone(),
                    target_head: encode_head(&published).map_err(CommitError::from)?,
                    pointer_head: Some(encode_head(&predecessor).map_err(CommitError::from)?),
                    cutover_epoch: purge.cutover_epoch,
                    source_pointers,
                };
                atomic_publish_in(
                    &self.fs_dir,
                    OsStr::new(REANCHOR_RECOVERY),
                    &seal(keys, &self.core_id, "reanchor-recovery", &recovery)?,
                )?;
                // Reanchoring preserves the irreversible marker and authenticated lineage.
                for pointer in [
                    CUTOVER_RECEIPT_FILE,
                    CUTOVER_COMPLETE_FILE,
                    VALIDATION_HEAD_FILE,
                ] {
                    atomic_publish_in(
                        &self.fs_dir,
                        OsStr::new(pointer),
                        &encode_head(&predecessor).map_err(CommitError::from)?,
                    )?;
                    hook(pointer)?;
                }
                // All recovery pointers are now durable. Remove their source pins before quarantine.
                durable_remove(&self.fs_dir, REANCHOR_RECOVERY)?;
                self.clear_cached_state();
                let inventory = self.maintenance_inventory_locked(keyring, &journal.request)?;
                if !inventory.report.blockers.is_empty() {
                    return Err(MaintenanceError::Blocked);
                }
                let names: BTreeSet<_> =
                    journal.candidates.iter().map(|r| r.name.clone()).collect();
                journal.retained = inventory
                    .records
                    .into_iter()
                    .filter(|(name, _)| !names.contains(name))
                    .collect();
                journal.progress.state = "quarantining".to_owned();
                atomic_publish_in(
                    &self.fs_dir,
                    OsStr::new(REGISTRY),
                    &seal(keys, &self.core_id, "registry", &inventory.registry)?,
                )?;
                self.save_maintenance_journal(
                    &runtime_path,
                    &runtime,
                    &journal_name,
                    keys,
                    &journal,
                )?;
            }
        }
        self.maintenance_prune_v1(
            keyring,
            runtime_journal_directory,
            &PruneRequest {
                policy: request.target.policy.clone(),
                expected_inventory_digest: request.expected_inventory_digest.clone(),
                action: "resume".to_owned(),
                max_items: request.max_items,
            },
        )
    }

    fn stage_purge_catalog(
        &self,
        keys: &FrkSubkeys,
        catalog: CatalogGeneration,
    ) -> Result<MaintenancePin, MaintenanceError> {
        let encoded = crate::catalog::encrypt_catalog_generation(keys, &self.core_id, &catalog)
            .map_err(CommitError::from)?;
        let head = HeadRecord::new_for_catalog(keys, &self.core_id, &encoded, keys.frk_version())
            .map_err(CommitError::from)?;
        let name = catalog_generation_physical_name(&encoded).map_err(CommitError::from)?;
        publish_immutable_in_with_hook(
            &self.catalogs_dir,
            OsStr::new(&name),
            &encoded,
            &mut |_| Ok(()),
        )?;
        Ok(MaintenancePin {
            generation: head.generation(),
            catalog_hash: head.catalog_hash().to_owned(),
        })
    }
    fn purge_head_record(
        &self,
        keyring: &FrkKeyring<'_>,
        pin: &MaintenancePin,
        version: u32,
    ) -> Result<HeadRecord, MaintenanceError> {
        let name = format!("catalog-{:020}-{}.acore", pin.generation, pin.catalog_hash);
        let encoded = read_bounded_in(
            &self.catalogs_dir,
            OsStr::new(&name),
            MAX_CATALOG_ENVELOPE_SIZE,
        )?;
        let head = HeadRecord::new_for_catalog(
            keyring
                .require(version)
                .map_err(|_| MaintenanceError::Evidence)?,
            &self.core_id,
            &encoded,
            version,
        )
        .map_err(CommitError::from)?;
        if head.generation() != pin.generation || head.catalog_hash() != pin.catalog_hash {
            return Err(MaintenanceError::Evidence);
        }
        Ok(head)
    }
}

impl CoreCommitCoordinator {
    pub fn maintenance_purge_inventory_v1(
        &self,
        keyring: &FrkKeyring<'_>,
        request: &PurgeInventoryRequest,
    ) -> Result<PurgeInventory, MaintenanceError> {
        let _lock = CoreCommitLock::acquire_in(&self.root_dir, &self.fs_dir)?;
        let inventory = self.maintenance_inventory_locked(keyring, &request.policy)?;
        let (result, _) = purge_inventory(self, keyring, &inventory, request)?;
        Ok(result)
    }
}

fn purge_inventory(
    coordinator: &CoreCommitCoordinator,
    keyring: &FrkKeyring<'_>,
    inventory: &Inventory,
    request: &PurgeInventoryRequest,
) -> Result<(PurgeInventory, BTreeSet<String>), MaintenanceError> {
    OpaqueId::parse(&request.target_stable_id).map_err(|_| MaintenanceError::Invalid)?;
    if request.expected_revision == 0 {
        return Err(MaintenanceError::Invalid);
    }
    let mut blockers = inventory.report.blockers.clone();
    let head_name = inventory
        .report
        .head_generation
        .zip(inventory.report.head_catalog_hash.as_ref())
        .map(|(generation, hash)| format!("catalog-{generation:020}-{hash}.acore"));
    let current = head_name
        .as_ref()
        .and_then(|name| inventory.catalogs.get(name))
        .map(|(_, catalog)| catalog);
    let mut ids = BTreeSet::new();
    if let Some(current) = current {
        if let Some(target) = current
            .entries()
            .iter()
            .find(|entry| entry.stable_id().as_str() == request.target_stable_id)
        {
            let trashed = target.object_payload().map_or_else(
                || {
                    matches!(
                        target.common_folder_lifecycle(),
                        crate::catalog::FolderLifecycle::Trashed(_)
                    )
                },
                |object| matches!(object.lifecycle(), ObjectLifecycle::Trashed(_)),
            );
            if !trashed {
                blockers.push("purge_target_not_trashed".to_owned());
            }
            let revision = target
                .object_payload()
                .map_or(current.generation(), CatalogObject::revision);
            if revision != request.expected_revision {
                blockers.push("purge_target_revision_stale".to_owned());
            }
            ids.insert(request.target_stable_id.clone());
            loop {
                let previous = ids.len();
                for entry in current.entries() {
                    if entry.parent_id().is_some_and(|p| ids.contains(p.as_str())) {
                        ids.insert(entry.stable_id().as_str().to_owned());
                    }
                }
                if previous == ids.len() {
                    break;
                }
            }
        } else {
            blockers.push("purge_target_missing".to_owned());
        }
    }
    let mut waived = BTreeSet::new();
    let mut sha = Sha256::new();
    sha.update(request.target_stable_id.as_bytes());
    sha.update(request.expected_revision.to_be_bytes());
    for id in &ids {
        sha.update(id.as_bytes());
    }
    let mut objects = BTreeSet::new();
    for (name, (version, generation)) in &inventory.catalog_headers {
        let catalog = coordinator.maintenance_read_catalog(keyring, name, *version, *generation)?;
        let targets: Vec<_> = catalog
            .entries()
            .iter()
            .filter(|entry| ids.contains(entry.stable_id().as_str()))
            .collect();
        if !targets.is_empty() {
            waived.insert(catalog.generation());
        }
        for entry in targets {
            if let Some(object) = entry.object_payload() {
                sha.update(entry.stable_id().as_str().as_bytes());
                sha.update(object.revision().to_be_bytes());
                sha.update(object.physical_name().as_str().as_bytes());
                sha.update(object.content_hash().as_str().as_bytes());
                objects.insert(object.physical_name().as_str().to_owned());
            }
        }
    }
    for archive in &inventory.preparation_archives {
        if archive.object_ids.iter().any(|id| ids.contains(id)) {
            sha.update(archive.terminal_digest.as_bytes());
            if let Some(record) = inventory.records.get(&archive.id) {
                sha.update(record.hash.as_bytes());
            }
            for (id, names) in &archive.objects {
                if ids.contains(id) {
                    objects.extend(names.iter().cloned());
                }
            }
            match &archive.published {
                Some((generation, hash)) => {
                    let name = format!("catalog-{generation:020}-{hash}.acore");
                    if !inventory
                        .catalog_headers
                        .get(&name)
                        .map(|(version, generation)| {
                            coordinator.maintenance_read_catalog(
                                keyring,
                                &name,
                                *version,
                                *generation,
                            )
                        })
                        .transpose()?
                        .is_some_and(|catalog| {
                            catalog
                                .entries()
                                .iter()
                                .any(|entry| ids.contains(entry.stable_id().as_str()))
                        })
                    {
                        blockers
                            .push("purge_preparation_not_covered_by_confirmed_history".to_owned());
                    }
                    waived.insert(*generation);
                }
                None => {
                    blockers.push("purge_preparation_not_covered_by_confirmed_history".to_owned())
                }
            }
        }
    }
    for pin in &request.policy.pins {
        if waived.contains(&pin.generation) {
            blockers.push("purge_target_pinned".to_owned());
        }
    }
    Ok((
        PurgeInventory {
            inventory: inventory.report.clone(),
            target_stable_id: request.target_stable_id.clone(),
            expected_revision: request.expected_revision,
            recursive_inventory_digest: hex(&sha.finalize()),
            required_waived_generations: waived.into_iter().collect(),
            object_count: objects.len(),
            blockers,
        },
        ids,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crypto::{derive_corefs_subkeys, SecretBytes};

    fn fixture(label: &str) -> (std::path::PathBuf, CoreCommitCoordinator, FrkSubkeys) {
        let root = std::env::temp_dir().join(format!(
            "anima-maintenance-{label}-{}",
            rand::random::<u64>()
        ));
        let coordinator = CoreCommitCoordinator::new(&root, "maintenance-core").unwrap();
        let keys = derive_corefs_subkeys(&SecretBytes::new(vec![17; 32]).unwrap(), 1).unwrap();
        (root, coordinator, keys)
    }

    #[test]
    fn inventory_refuses_corrupt_catalog_as_orphan_evidence() {
        let (root, coordinator, keys) = fixture("corrupt");
        std::fs::write(
            coordinator
                .catalogs_path()
                .join(format!("catalog-{:020}-{}.acore", 1, "a".repeat(64))),
            b"corrupt",
        )
        .unwrap();
        let report = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        assert!(!report.blockers.is_empty());
        assert_eq!(report.candidate_catalog_count, 0);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn inventory_does_not_expose_physical_candidates_or_logical_names() {
        let (root, coordinator, keys) = fixture("opaque");
        let name = format!("object-{}.acore", "a".repeat(32));
        std::fs::write(
            coordinator.objects_path().join(&name),
            b"opaque unused object",
        )
        .unwrap();
        let report = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        let wire = serde_json::to_string(&report).unwrap();
        assert!(!wire.contains(&name));
        assert!(
            !report.blockers.is_empty(),
            "absence of authenticated HEAD must block deletion"
        );
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    fn initialized(label: &str) -> (std::path::PathBuf, CoreCommitCoordinator, FrkSubkeys) {
        let (root, coordinator, keys) = fixture(label);
        let catalog = CatalogGeneration::new(
            1,
            vec![CatalogGenerationEntry::folder(
                crate::catalog::CatalogEntryCommon::new(
                    OpaqueId::parse("01J00000000000000000000000").unwrap(),
                    None,
                    PortableName::parse("Core").unwrap(),
                    crate::folders::FolderOwner::User,
                    crate::policy::AnimaAccess::Write,
                ),
            )],
        )
        .unwrap()
        .with_cutover_marker(CatalogCutoverMarker::new(1).unwrap())
        .unwrap();
        coordinator
            .publish_catalog_pointer_with_hook(&keys, &catalog, HEAD_FILE, true, &mut |_| Ok(()))
            .unwrap();
        (root, coordinator, keys)
    }

    #[test]
    fn inventory_streams_more_than_1024_catalogs_and_refuses_aggregate_budget() {
        let (root, coordinator, keys) = initialized("catalog-scale");
        let ring = FrkKeyring::new([&keys]).unwrap();
        let entries = coordinator
            .load_committed(&keys)
            .unwrap()
            .unwrap()
            .catalog()
            .entries()
            .to_vec();
        for generation in 2..=1025 {
            let catalog = CatalogGeneration::new(generation, entries.clone())
                .unwrap()
                .with_cutover_marker(CatalogCutoverMarker::new(1).unwrap())
                .unwrap();
            coordinator
                .publish_catalog_pointer_with_hook(&keys, &catalog, HEAD_FILE, false, &mut |_| {
                    Ok(())
                })
                .unwrap();
        }
        let request = MaintenanceRequest::default();
        let inventory = coordinator
            .maintenance_inventory_locked(&ring, &request)
            .unwrap();
        assert!(
            inventory.report.blockers.is_empty(),
            "{:?}",
            inventory.report.blockers
        );
        assert_eq!(inventory.report.catalog_count, 1025);
        assert_eq!(
            inventory.catalogs.len(),
            1,
            "only current HEAD retains decoded entries"
        );
        let before = coordinator.catalogs_dir.entries().unwrap().count();
        let bounded = coordinator
            .maintenance_inventory_locked_with_budget(&ring, &request, 1, 1)
            .unwrap();
        assert!(bounded
            .report
            .blockers
            .iter()
            .any(|code| code == "catalog_aggregate_limit"));
        assert!(bounded.candidates.is_empty());
        assert_eq!(coordinator.catalogs_dir.entries().unwrap().count(), before);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn quarantine_can_cancel_before_sweep_and_resume_is_idempotent() {
        let (root, coordinator, keys) = initialized("prune");
        let runtime =
            std::env::temp_dir().join(format!("anima-maint-runtime-{}", rand::random::<u64>()));
        std::fs::create_dir(&runtime).unwrap();
        let name = format!("object-{}.acore", "b".repeat(32));
        std::fs::write(
            coordinator.objects_path().join(&name),
            b"unreferenced encrypted material",
        )
        .unwrap();
        let ring = FrkKeyring::new([&keys]).unwrap();
        let inventory = coordinator
            .maintenance_inventory_v1(&ring, &MaintenanceRequest::default())
            .unwrap();
        assert!(inventory.blockers.is_empty(), "{:?}", inventory.blockers);
        assert_eq!(inventory.candidate_object_count, 1);
        let mut request = PruneRequest {
            policy: MaintenanceRequest::default(),
            expected_inventory_digest: inventory.inventory_digest.clone(),
            action: "begin".to_owned(),
            max_items: 1,
        };
        let result = coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        assert_eq!(result.operation.unwrap().state, "quarantined");
        let recovered = coordinator
            .maintenance_inventory_with_runtime_v1(&ring, &request.policy, Some(&runtime))
            .unwrap();
        assert!(recovered.resume_authorized && recovered.cancel_authorized);
        assert_eq!(
            recovered.inventory_digest,
            request.expected_inventory_digest
        );
        assert_eq!(recovered.operation.unwrap().kind, "prune");
        assert!(!coordinator.objects_path().join(&name).exists());
        assert!(
            coordinator
                .commit(
                    &keys,
                    &[],
                    &[],
                    |current, next| CatalogGeneration::new(
                        next,
                        current.unwrap().entries().to_vec()
                    ),
                    |_| Ok(())
                )
                .is_err(),
            "catalog commits must be excluded while maintenance is resumable"
        );
        request.action = "cancel".to_owned();
        assert_eq!(
            coordinator
                .maintenance_prune_v1(&ring, &runtime, &request)
                .unwrap()
                .operation
                .unwrap()
                .state,
            "cancelled"
        );
        assert!(coordinator.objects_path().join(&name).exists());
        request.action = "begin".to_owned();
        coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        request.action = "resume".to_owned();
        assert_eq!(
            coordinator
                .maintenance_prune_v1(&ring, &runtime, &request)
                .unwrap()
                .operation
                .unwrap()
                .state,
            "complete"
        );
        assert_eq!(
            coordinator
                .maintenance_prune_v1(&ring, &runtime, &request)
                .unwrap()
                .operation
                .unwrap()
                .swept_count,
            1
        );
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }

    #[test]
    fn supported_inventory_is_not_limited_to_4096_physical_objects() {
        let (root, coordinator, keys) = initialized("large-inventory");
        for i in 0..4097 {
            std::fs::write(
                coordinator
                    .objects_path()
                    .join(format!("object-{i:032x}.acore")),
                b"orphan ciphertext",
            )
            .unwrap();
        }
        let inventory = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        assert!(inventory.blockers.is_empty(), "{:?}", inventory.blockers);
        assert_eq!(inventory.candidate_object_count, 4097);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn interrupted_checkpoint_refresh_resumes_same_irreversible_lineage() {
        let (root, coordinator, keys) = initialized("reanchor");
        coordinator
            .commit(
                &keys,
                &[],
                &[],
                |current, next| CatalogGeneration::new(next, current.unwrap().entries().to_vec()),
                |_| Ok(()),
            )
            .unwrap();
        let runtime =
            std::env::temp_dir().join(format!("anima-reanchor-runtime-{}", rand::random::<u64>()));
        std::fs::create_dir(&runtime).unwrap();
        let new_keys =
            derive_corefs_subkeys(&SecretBytes::new(vec![0x62; 32]).unwrap(), 2).unwrap();
        coordinator
            .rotate_frk(&FrkKeyring::new([&keys]).unwrap(), &new_keys, 2, |_| Ok(()))
            .unwrap();
        let ring = FrkKeyring::new([&keys, &new_keys]).unwrap();
        let inventory = coordinator
            .maintenance_inventory_v1(&ring, &MaintenanceRequest::default())
            .unwrap();
        let mut request = PruneRequest {
            policy: MaintenanceRequest::default(),
            expected_inventory_digest: inventory.inventory_digest,
            action: "begin".to_owned(),
            max_items: 128,
        };
        let result =
            coordinator.maintenance_reanchor_with_hook(&ring, &runtime, &request, &mut |pointer| {
                if pointer == CUTOVER_RECEIPT_FILE {
                    Err(io::Error::other("injected checkpoint crash"))
                } else {
                    Ok(())
                }
            });
        assert!(result.is_err());
        let journal_path = std::fs::read_dir(&runtime)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let parked = runtime.join("parked-runtime-journal");
        std::fs::rename(&journal_path, &parked).unwrap();
        let recovered_content = coordinator.load_committed(&new_keys).unwrap().unwrap();
        assert_eq!(recovered_content.head().generation(), 3);
        drop(recovered_content);
        std::fs::rename(parked, journal_path).unwrap();
        let recovered = coordinator
            .maintenance_inventory_with_runtime_v1(&ring, &request.policy, Some(&runtime))
            .unwrap();
        assert!(recovered.resume_authorized);
        assert!(!recovered.cancel_authorized);
        assert_eq!(recovered.operation.unwrap().kind, "reanchor");
        request.action = "resume".to_owned();
        let resumed = coordinator
            .maintenance_reanchor_v1(&ring, &runtime, &request)
            .unwrap();
        assert_eq!(resumed.head_generation, Some(3));
        assert_eq!(resumed.operation.unwrap().state, "complete");
        let current = coordinator.load_committed(&new_keys).unwrap().unwrap();
        assert_eq!(current.catalog().cutover_marker().unwrap().epoch(), 1);
        assert_eq!(
            coordinator.load_pointer_head(CUTOVER_RECEIPT_FILE).unwrap(),
            coordinator
                .load_pointer_head(CUTOVER_COMPLETE_FILE)
                .unwrap()
        );
        drop(current);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }

    #[test]
    fn hardlinked_candidate_never_authorizes_deletion() {
        let (root, coordinator, keys) = initialized("hardlink");
        let outside =
            std::env::temp_dir().join(format!("anima-maintenance-link-{}", rand::random::<u64>()));
        std::fs::write(&outside, b"external encrypted file").unwrap();
        let candidate = coordinator
            .objects_path()
            .join(format!("object-{}.acore", "c".repeat(32)));
        std::fs::hard_link(&outside, &candidate).unwrap();
        let report = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        assert!(report.blockers.iter().any(|b| b == "object_file_unsafe"));
        assert_eq!(report.candidate_object_count, 0);
        assert_eq!(std::fs::read(&outside).unwrap(), b"external encrypted file");
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_file(outside).unwrap();
    }

    #[test]
    fn tampered_journal_never_authorizes_resume_or_sweep() {
        let (root, coordinator, keys) = initialized("journal-tamper");
        let runtime =
            std::env::temp_dir().join(format!("anima-tamper-runtime-{}", rand::random::<u64>()));
        std::fs::create_dir(&runtime).unwrap();
        let name = format!("object-{}.acore", "d".repeat(32));
        std::fs::write(
            coordinator.objects_path().join(&name),
            b"unused encrypted material",
        )
        .unwrap();
        let ring = FrkKeyring::new([&keys]).unwrap();
        let policy = MaintenanceRequest::default();
        let report = coordinator
            .maintenance_inventory_v1(&ring, &policy)
            .unwrap();
        let mut request = PruneRequest {
            policy,
            expected_inventory_digest: report.inventory_digest,
            action: "begin".to_owned(),
            max_items: 128,
        };
        coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        let journal = std::fs::read_dir(&runtime)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let mut bytes = std::fs::read(&journal).unwrap();
        let last = bytes.len() - 1;
        bytes[last] ^= 1;
        std::fs::write(journal, bytes).unwrap();
        request.action = "resume".to_owned();
        assert!(matches!(
            coordinator.maintenance_prune_v1(&ring, &runtime, &request),
            Err(MaintenanceError::Evidence)
        ));
        assert!(coordinator
            .maintenance_inventory_with_runtime_v1(&ring, &request.policy, Some(&runtime))
            .is_err());
        assert!(coordinator
            .fs_dir
            .open_dir(QUARANTINE)
            .unwrap()
            .symlink_metadata(&name)
            .is_ok());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }

    #[test]
    fn future_retention_clock_cannot_expire_restore_history() {
        let (root, coordinator, keys) = initialized("future-clock");
        let mut request = MaintenanceRequest::default();
        request.now_ms += 31 * DAY_MS;
        assert!(matches!(
            coordinator.maintenance_inventory_v1(&FrkKeyring::new([&keys]).unwrap(), &request),
            Err(MaintenanceError::Invalid)
        ));
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn held_retirement_lock_reauthenticates_runtime_journal_on_postcheck() {
        let (root, coordinator, keys) = initialized("guard-journal");
        let runtime =
            std::env::temp_dir().join(format!("anima-guard-runtime-{}", rand::random::<u64>()));
        std::fs::create_dir(&runtime).unwrap();
        let ring = FrkKeyring::new([&keys]).unwrap();
        let policy = MaintenanceRequest::default();
        let report = coordinator
            .maintenance_inventory_v1(&ring, &policy)
            .unwrap();
        let mut request = PruneRequest {
            policy: policy.clone(),
            expected_inventory_digest: report.inventory_digest,
            action: "begin".to_owned(),
            max_items: 128,
        };
        coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        request.action = "resume".to_owned();
        coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        let report = coordinator
            .maintenance_inventory_with_runtime_v1(&ring, &policy, Some(&runtime))
            .unwrap();
        let guard = coordinator
            .maintenance_lock_with_runtime_v1(
                &ring,
                &MaintenanceLockRequest {
                    policy,
                    expected_inventory_digest: report.inventory_digest,
                },
                Some(&runtime),
            )
            .unwrap();
        let journal = std::fs::read_dir(&runtime)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let mut bytes = std::fs::read(&journal).unwrap();
        let last = bytes.len() - 1;
        bytes[last] ^= 1;
        std::fs::write(journal, bytes).unwrap();
        assert!(matches!(
            coordinator.maintenance_verify_locked_v1(&guard, &ring),
            Err(MaintenanceError::Evidence)
        ));
        drop(guard);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }

    #[test]
    fn bounded_cancellation_resumes_restoration_without_sweeping() {
        let (root, coordinator, keys) = initialized("cancel-batch");
        let runtime =
            std::env::temp_dir().join(format!("anima-cancel-runtime-{}", rand::random::<u64>()));
        std::fs::create_dir(&runtime).unwrap();
        let first = format!("object-{}.acore", "e".repeat(32));
        let second = format!("object-{}.acore", "f".repeat(32));
        for name in [&first, &second] {
            std::fs::write(
                coordinator.objects_path().join(name),
                b"unused encrypted object",
            )
            .unwrap();
        }
        let ring = FrkKeyring::new([&keys]).unwrap();
        let policy = MaintenanceRequest::default();
        let report = coordinator
            .maintenance_inventory_v1(&ring, &policy)
            .unwrap();
        let mut request = PruneRequest {
            policy,
            expected_inventory_digest: report.inventory_digest,
            action: "begin".to_owned(),
            max_items: 128,
        };
        coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        request.action = "cancel".to_owned();
        request.max_items = 1;
        let cancelled = coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        assert_eq!(cancelled.operation.unwrap().state, "cancelling");
        assert!(cancelled.resume_authorized && cancelled.cancel_authorized);
        request.action = "resume".to_owned();
        let resumed = coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        assert_eq!(resumed.operation.unwrap().state, "cancelled");
        assert!(coordinator.objects_path().join(first).exists());
        assert!(coordinator.objects_path().join(second).exists());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }

    #[test]
    fn authenticated_empty_bootstrap_quarantine_does_not_block_maintenance() {
        let (root, coordinator, keys) = initialized("empty-preparation-quarantine");
        coordinator
            .fs_dir
            .create_dir("preparation-quarantine")
            .unwrap();
        let report = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        assert!(report.blockers.is_empty(), "{:?}", report.blockers);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn expired_terminal_preparation_and_history_release_old_frk_references() {
        let (root, coordinator, keys) = fixture("expired-preparation");
        let source_hash = "a".repeat(64);
        let collecting = coordinator
            .preparation_begin_or_resume_v1(
                &keys,
                &PreparationBeginV1 {
                    scope: "pcf004-writing-v1".to_owned(),
                    expected_validation_generation: None,
                    expected_validation_catalog_sha256: None,
                    source_owner_id: "01J00000000000000000000003".to_owned(),
                    source_schema_version: 1,
                    source_mutation_generation: 1,
                    source_inventory_sha256: source_hash.clone(),
                },
            )
            .unwrap();
        let active = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        assert!(active
            .blockers
            .iter()
            .any(|b| b == "preparation_active_or_corrupt"));
        assert_eq!(active.candidate_preparation_count, 0);
        let ready = coordinator
            .preparation_seal_v1(
                &keys,
                &PreparationCasV1 {
                    pointer_sha256: collecting.pointer_sha256,
                    snapshot_sequence: collecting.snapshot_sequence,
                },
                &PreparationSealV1 {
                    source_mutation_generation: 1,
                    source_inventory_sha256: source_hash.clone(),
                    folders: vec![
                        ValidationBatchFolder {
                            stable_id: "01J00000000000000000000000".to_owned(),
                            parent_id: None,
                            name: "Core".to_owned(),
                            role: None,
                            policy: ValidationBatchPolicy::UserWrite,
                            metadata: BTreeMap::new(),
                        },
                        ValidationBatchFolder {
                            stable_id: "01J00000000000000000000004".to_owned(),
                            parent_id: Some("01J00000000000000000000000".to_owned()),
                            name: "Journal".to_owned(),
                            role: Some("core.journal".to_owned()),
                            policy: ValidationBatchPolicy::UserWrite,
                            metadata: BTreeMap::new(),
                        },
                        ValidationBatchFolder {
                            stable_id: "01J00000000000000000000005".to_owned(),
                            parent_id: Some("01J00000000000000000000000".to_owned()),
                            name: "Notes".to_owned(),
                            role: Some("core.notes".to_owned()),
                            policy: ValidationBatchPolicy::UserWrite,
                            metadata: BTreeMap::new(),
                        },
                    ],
                    objects: Vec::new(),
                },
            )
            .unwrap();
        coordinator
            .preparation_finalize_v1(
                &keys,
                &PreparationFinalizeV1 {
                    preparation_id: ready.preparation_id,
                    expected: PreparationCasV1 {
                        pointer_sha256: ready.pointer_sha256,
                        snapshot_sequence: ready.snapshot_sequence,
                    },
                    source_mutation_generation: 1,
                    source_inventory_sha256: source_hash,
                },
            )
            .unwrap();
        let preparation_root = std::fs::read_dir(root.join(FS_DIRECTORY).join("preparations"))
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let receipt = std::fs::read_dir(preparation_root.join("receipts"))
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let original = std::fs::read(&receipt).unwrap();
        std::fs::write(&receipt, b"tampered terminal receipt").unwrap();
        let corrupt = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        assert!(corrupt
            .blockers
            .iter()
            .any(|b| b == "preparation_archive_incomplete_or_corrupt"));
        assert_eq!(corrupt.candidate_preparation_count, 0);
        std::fs::write(receipt, original).unwrap();
        coordinator
            .commit_first_mutation(
                &keys,
                1,
                &[],
                &[],
                |current, n| CatalogGeneration::new(n, current.unwrap().entries().to_vec()),
                |_| Ok(()),
            )
            .unwrap();
        let new_keys =
            derive_corefs_subkeys(&SecretBytes::new(vec![0x79; 32]).unwrap(), 2).unwrap();
        coordinator
            .rotate_frk(&FrkKeyring::new([&keys]).unwrap(), &new_keys, 2, |_| Ok(()))
            .unwrap();
        let ring = FrkKeyring::new([&keys, &new_keys]).unwrap();
        let runtime =
            std::env::temp_dir().join(format!("anima-archive-runtime-{}", rand::random::<u64>()));
        std::fs::create_dir(&runtime).unwrap();
        let policy = MaintenanceRequest::default();
        let report = coordinator
            .maintenance_inventory_v1(&ring, &policy)
            .unwrap();
        assert!(report.blockers.is_empty(), "{:?}", report.blockers);
        assert!(report.required_frk_versions.contains(&1));
        coordinator
            .maintenance_reanchor_v1(
                &ring,
                &runtime,
                &PruneRequest {
                    policy: policy.clone(),
                    expected_inventory_digest: report.inventory_digest,
                    action: "begin".to_owned(),
                    max_items: 128,
                },
            )
            .unwrap();
        coordinator
            .commit_with_keyring(
                &ring,
                &new_keys,
                &[],
                &[],
                |current, n| CatalogGeneration::new(n, current.unwrap().entries().to_vec()),
                |_| Ok(()),
            )
            .unwrap();
        coordinator
            .maintenance_inventory_v1(&ring, &policy)
            .unwrap();
        let encoded = read_bounded_in(
            &coordinator.fs_dir,
            OsStr::new(REGISTRY),
            4 * 1024 * 1024 + 32,
        )
        .unwrap();
        let (_, mut registry) =
            open::<Registry>(&ring, "maintenance-core", "registry", &encoded).unwrap();
        for first in registry.observed.values_mut() {
            *first = policy.now_ms - 31 * DAY_MS;
        }
        atomic_publish_in(
            &coordinator.fs_dir,
            OsStr::new(REGISTRY),
            &seal(&new_keys, "maintenance-core", "registry", &registry).unwrap(),
        )
        .unwrap();
        let report = coordinator
            .maintenance_inventory_v1(&ring, &policy)
            .unwrap();
        assert!(report.blockers.is_empty(), "{:?}", report.blockers);
        assert_eq!(report.candidate_preparation_count, 1);
        assert!(report.required_frk_versions.contains(&1));
        let mut request = PruneRequest {
            policy: policy.clone(),
            expected_inventory_digest: report.inventory_digest,
            action: "begin".to_owned(),
            max_items: 1,
        };
        let begun = coordinator
            .maintenance_prune_v1(&ring, &runtime, &request)
            .unwrap();
        assert_eq!(begun.operation.unwrap().quarantined_count, 1);
        request.action = "resume".to_owned();
        let mut complete = false;
        for _ in 0..32 {
            let result = coordinator
                .maintenance_prune_v1(&ring, &runtime, &request)
                .unwrap();
            if result.operation.unwrap().state == "complete" {
                complete = true;
                break;
            }
        }
        assert!(complete, "bounded archive sweep did not converge");
        let report = coordinator
            .maintenance_inventory_with_runtime_v1(
                &FrkKeyring::new([&new_keys]).unwrap(),
                &policy,
                Some(&runtime),
            )
            .unwrap();
        assert!(report.blockers.is_empty(), "{:?}", report.blockers);
        assert_eq!(report.required_frk_versions, vec![2]);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }

    #[test]
    fn confirmed_target_history_waiver_retires_completed_preparation_wrappers() {
        let (root, coordinator, keys) = fixture("purge-prepared-attachment");
        let source_hash = "b".repeat(64);
        let body = b"retained attachment";
        let collecting = coordinator
            .preparation_begin_or_resume_v1(
                &keys,
                &PreparationBeginV1 {
                    scope: "pcf004-writing-v1".to_owned(),
                    expected_validation_generation: None,
                    expected_validation_catalog_sha256: None,
                    source_owner_id: "01J00000000000000000000003".to_owned(),
                    source_schema_version: 1,
                    source_mutation_generation: 1,
                    source_inventory_sha256: source_hash.clone(),
                },
            )
            .unwrap();
        let prepared = coordinator
            .preparation_prepare_object_v1(
                &keys,
                &PreparationCasV1 {
                    pointer_sha256: collecting.pointer_sha256,
                    snapshot_sequence: collecting.snapshot_sequence,
                },
                &PreparationObjectV1 {
                    object_id: "01J00000000000000000000006".to_owned(),
                    revision: 1,
                    object_key_epoch: 1,
                    kind: ObjectKind::Attachment,
                    parent_id: "01J00000000000000000000005".to_owned(),
                    name: "retained.bin".to_owned(),
                    content_type: "application/octet-stream".to_owned(),
                    body_encoding: crate::envelope::BodyEncoding::Binary,
                    body_length: body.len() as u64,
                    content_sha256: hash(body),
                    created_at: "2026-10-09T00:00:00Z".to_owned(),
                    updated_at: "2026-10-09T00:00:00Z".to_owned(),
                    source_character_count: None,
                    references: Vec::new(),
                    policy: ValidationBatchPolicy::Inherit,
                    stable_role: None,
                    graph_metadata: BTreeMap::new(),
                    source_fingerprint_sha256: hash(body),
                    converter_format_version: 1,
                },
                &mut std::io::Cursor::new(body),
            )
            .unwrap();
        let folders = vec![
            ValidationBatchFolder {
                stable_id: "01J00000000000000000000000".to_owned(),
                parent_id: None,
                name: "Core".to_owned(),
                role: None,
                policy: ValidationBatchPolicy::UserWrite,
                metadata: BTreeMap::new(),
            },
            ValidationBatchFolder {
                stable_id: "01J00000000000000000000004".to_owned(),
                parent_id: Some("01J00000000000000000000000".to_owned()),
                name: "Journal".to_owned(),
                role: Some("core.journal".to_owned()),
                policy: ValidationBatchPolicy::UserWrite,
                metadata: BTreeMap::new(),
            },
            ValidationBatchFolder {
                stable_id: "01J00000000000000000000005".to_owned(),
                parent_id: Some("01J00000000000000000000000".to_owned()),
                name: "Notes".to_owned(),
                role: Some("core.notes".to_owned()),
                policy: ValidationBatchPolicy::UserWrite,
                metadata: BTreeMap::new(),
            },
        ];
        let ready = coordinator
            .preparation_seal_v1(
                &keys,
                &PreparationCasV1 {
                    pointer_sha256: prepared.status.pointer_sha256,
                    snapshot_sequence: prepared.status.snapshot_sequence,
                },
                &PreparationSealV1 {
                    source_mutation_generation: 1,
                    source_inventory_sha256: source_hash.clone(),
                    folders,
                    objects: vec![PreparationIdentityV1 {
                        object_id: prepared.prepared.object_id,
                        revision: prepared.prepared.revision,
                        content_sha256: prepared.prepared.content_sha256,
                        preparation_ordinal: prepared.prepared.preparation_ordinal,
                    }],
                },
            )
            .unwrap();
        coordinator
            .preparation_finalize_v1(
                &keys,
                &PreparationFinalizeV1 {
                    preparation_id: ready.preparation_id,
                    expected: PreparationCasV1 {
                        pointer_sha256: ready.pointer_sha256,
                        snapshot_sequence: ready.snapshot_sequence,
                    },
                    source_mutation_generation: 1,
                    source_inventory_sha256: source_hash,
                },
            )
            .unwrap();
        coordinator
            .commit_first_mutation(
                &keys,
                1,
                &[],
                &[],
                |current, n| CatalogGeneration::new(n, current.unwrap().entries().to_vec()),
                |_| Ok(()),
            )
            .unwrap();
        let target_id = "01J00000000000000000000006";
        let trash_id = OpaqueId::parse("01J00000000000000000000007").unwrap();
        let current = coordinator.load_committed(&keys).unwrap().unwrap();
        let source =
            CatalogPrecondition::object(current.catalog(), &OpaqueId::parse(target_id).unwrap(), 1)
                .unwrap();
        let vacant = CatalogPrecondition::vacant(
            current.catalog(),
            &OpaqueId::parse("01J00000000000000000000000").unwrap(),
            PortableName::parse("Trash").unwrap(),
        )
        .unwrap();
        coordinator
            .commit(
                &keys,
                &[],
                &[source, vacant],
                |current, n| {
                    let current = current.unwrap();
                    let mut entries = Vec::new();
                    for entry in current.entries() {
                        if entry.stable_id().as_str() == target_id {
                            let old = entry.object_payload().unwrap();
                            entries.push(CatalogGenerationEntry::object(
                                entry
                                    .common_for_internal_mutation()
                                    .clone()
                                    .moved_for_internal_mutation(
                                        trash_id.clone(),
                                        PortableName::parse("retained.bin").unwrap(),
                                    ),
                                CatalogObject::new(
                                    old.revision(),
                                    old.physical_name().clone(),
                                    old.content_hash().clone(),
                                    old.kind(),
                                    old.wrapped_dek().clone(),
                                    ObjectLifecycle::Trashed(
                                        crate::catalog::TrashMetadata::new(
                                            trash_id.clone(),
                                            OpaqueId::parse("01J00000000000000000000005").unwrap(),
                                            PortableName::parse("retained.bin").unwrap(),
                                            1,
                                        )
                                        .unwrap(),
                                    ),
                                )
                                .unwrap(),
                            ));
                        } else {
                            entries.push(entry.clone());
                        }
                    }
                    entries.push(CatalogGenerationEntry::folder(
                        crate::catalog::CatalogEntryCommon::new(
                            trash_id.clone(),
                            Some(OpaqueId::parse("01J00000000000000000000000").unwrap()),
                            PortableName::parse("Trash").unwrap(),
                            crate::folders::FolderOwner::User,
                            crate::policy::AnimaAccess::Write,
                        ),
                    ));
                    CatalogGeneration::new(n, entries)
                },
                |_| Ok(()),
            )
            .unwrap();
        drop(current);
        let ring = FrkKeyring::new([&keys]).unwrap();
        let policy = MaintenanceRequest::default();
        let review_request = PurgeInventoryRequest {
            policy: policy.clone(),
            target_stable_id: target_id.to_owned(),
            expected_revision: 1,
        };
        let review = coordinator
            .maintenance_purge_inventory_v1(&ring, &review_request)
            .unwrap();
        assert!(review.blockers.is_empty(), "{:?}", review.blockers);
        assert_eq!(review.required_waived_generations, vec![1, 2, 3]);
        let runtime = std::env::temp_dir().join(format!(
            "anima-prepared-purge-runtime-{}",
            rand::random::<u64>()
        ));
        std::fs::create_dir(&runtime).unwrap();
        let mut request = PurgeRequest {
            target: review_request,
            expected_inventory_digest: review.inventory.inventory_digest,
            recursive_inventory_digest: review.recursive_inventory_digest,
            waived_generations: review.required_waived_generations,
            action: "begin".to_owned(),
            max_items: 128,
        };
        coordinator
            .maintenance_purge_v1(&ring, &runtime, &request)
            .unwrap();
        request.action = "resume".to_owned();
        request.max_items = 1;
        let mut done = false;
        for _ in 0..64 {
            let result = coordinator
                .maintenance_purge_v1(&ring, &runtime, &request)
                .unwrap();
            if result.operation.unwrap().state == "complete" {
                done = true;
                break;
            }
        }
        assert!(done);
        assert!(coordinator
            .fs_dir
            .open_dir("preparations")
            .unwrap()
            .entries()
            .unwrap()
            .next()
            .is_none());
        assert!(coordinator.objects_dir.entries().unwrap().next().is_none());
        let current = coordinator.load_committed(&keys).unwrap().unwrap();
        assert_eq!(current.head().generation(), 5);
        assert!(!current
            .catalog()
            .entries()
            .iter()
            .any(|entry| entry.stable_id().as_str() == target_id));
        drop(current);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }

    #[test]
    fn purge_rejects_live_target_and_stale_revision() {
        let (root, coordinator, keys) = initialized("purge-live");
        let request = PurgeInventoryRequest {
            policy: MaintenanceRequest::default(),
            target_stable_id: "01J00000000000000000000000".to_owned(),
            expected_revision: 1,
        };
        let result = coordinator
            .maintenance_purge_inventory_v1(&FrkKeyring::new([&keys]).unwrap(), &request)
            .unwrap();
        assert!(!result.blockers.is_empty());
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn inventory_authenticates_retained_object_bodies() {
        let (root, coordinator, keys) = initialized("body-corrupt");
        let object_id = "01J00000000000000000000001";
        let dek = SecretBytes::new(vec![44; 32]).unwrap();
        let aad = ObjectBaseAad::new(
            "maintenance-core",
            object_id,
            ObjectKind::Note,
            crate::envelope::ENVELOPE_VERSION,
            1,
            1,
        )
        .unwrap();
        let metadata = EnvelopeMetadata::for_body(
            "note",
            object_id,
            1,
            "2026-10-09",
            "2026-10-09",
            "text/plain",
            BTreeMap::new(),
            crate::envelope::BodyEncoding::Utf8,
            b"private note",
        )
        .unwrap();
        let encoded =
            crate::envelope::encode_envelope(&dek, &aad, &metadata, b"private note").unwrap();
        let prepared = coordinator
            .prepare_object_revision(&keys, &dek, &aad, &mut std::io::Cursor::new(encoded))
            .unwrap();
        let current = coordinator.load_committed(&keys).unwrap().unwrap();
        let mut entries = current.catalog().entries().to_vec();
        entries.push(CatalogGenerationEntry::object(
            crate::catalog::CatalogEntryCommon::new(
                OpaqueId::parse(object_id).unwrap(),
                Some(OpaqueId::parse("01J00000000000000000000000").unwrap()),
                PortableName::parse("Private note").unwrap(),
                crate::folders::FolderOwner::User,
                crate::policy::AnimaAccess::Write,
            ),
            CatalogObject::new(
                1,
                prepared.physical_name().clone(),
                prepared.content_hash().clone(),
                ObjectKind::Note,
                prepared.wrapped_dek().clone(),
                ObjectLifecycle::Live,
            )
            .unwrap(),
        ));
        let next = CatalogGeneration::new(2, entries)
            .unwrap()
            .with_cutover_marker(CatalogCutoverMarker::new(1).unwrap())
            .unwrap();
        coordinator
            .publish_catalog_pointer_with_hook(&keys, &next, HEAD_FILE, false, &mut |_| Ok(()))
            .unwrap();
        std::fs::write(
            coordinator
                .objects_path()
                .join(prepared.physical_name().as_str()),
            b"damaged",
        )
        .unwrap();
        let result = coordinator
            .maintenance_inventory_v1(
                &FrkKeyring::new([&keys]).unwrap(),
                &MaintenanceRequest::default(),
            )
            .unwrap();
        assert!(
            result
                .blockers
                .iter()
                .any(|b| b == "retained_object_authentication_failed"),
            "{:?}",
            result.blockers
        );
        drop(current);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn purge_removes_target_wrappers_and_keeps_clean_predecessor() {
        purge_crash_recovery_case(HEAD_FILE);
    }

    #[test]
    fn purge_receipt_crash_cold_reopens_and_resumes_without_runtime_journal() {
        for pointer in [
            CUTOVER_RECEIPT_FILE,
            CUTOVER_COMPLETE_FILE,
            VALIDATION_HEAD_FILE,
        ] {
            purge_crash_recovery_case(pointer);
        }
    }

    fn purge_crash_recovery_case(crash_pointer: &str) {
        let (root, coordinator, keys) = initialized("purge-trash");
        let object_id = "01J00000000000000000000001";
        let trash_id = "01J00000000000000000000002";
        let root_id = OpaqueId::parse("01J00000000000000000000000").unwrap();
        let dek = SecretBytes::new(vec![45; 32]).unwrap();
        let aad = ObjectBaseAad::new(
            "maintenance-core",
            object_id,
            ObjectKind::Note,
            crate::envelope::ENVELOPE_VERSION,
            1,
            1,
        )
        .unwrap();
        let metadata = EnvelopeMetadata::for_body(
            "note",
            object_id,
            1,
            "2026-10-09",
            "2026-10-09",
            "text/plain",
            BTreeMap::new(),
            crate::envelope::BodyEncoding::Utf8,
            b"private trashed note",
        )
        .unwrap();
        let encoded =
            crate::envelope::encode_envelope(&dek, &aad, &metadata, b"private trashed note")
                .unwrap();
        let prepared = coordinator
            .prepare_object_revision(&keys, &dek, &aad, &mut std::io::Cursor::new(encoded))
            .unwrap();
        let current = coordinator.load_committed(&keys).unwrap().unwrap();
        let mut entries = current.catalog().entries().to_vec();
        entries.push(CatalogGenerationEntry::folder(
            crate::catalog::CatalogEntryCommon::new(
                OpaqueId::parse(trash_id).unwrap(),
                Some(root_id.clone()),
                PortableName::parse("Trash").unwrap(),
                crate::folders::FolderOwner::User,
                crate::policy::AnimaAccess::Write,
            ),
        ));
        entries.push(CatalogGenerationEntry::object(
            crate::catalog::CatalogEntryCommon::new(
                OpaqueId::parse(object_id).unwrap(),
                Some(OpaqueId::parse(trash_id).unwrap()),
                PortableName::parse("private-trash-name").unwrap(),
                crate::folders::FolderOwner::User,
                crate::policy::AnimaAccess::Write,
            ),
            CatalogObject::new(
                1,
                prepared.physical_name().clone(),
                prepared.content_hash().clone(),
                ObjectKind::Note,
                prepared.wrapped_dek().clone(),
                ObjectLifecycle::Trashed(
                    crate::catalog::TrashMetadata::new(
                        OpaqueId::parse(trash_id).unwrap(),
                        root_id,
                        PortableName::parse("private-original-name").unwrap(),
                        1,
                    )
                    .unwrap(),
                ),
            )
            .unwrap(),
        ));
        let next = CatalogGeneration::new(2, entries)
            .unwrap()
            .with_cutover_marker(CatalogCutoverMarker::new(1).unwrap())
            .unwrap();
        coordinator
            .publish_catalog_pointer_with_hook(&keys, &next, HEAD_FILE, false, &mut |_| Ok(()))
            .unwrap();
        let runtime =
            std::env::temp_dir().join(format!("anima-purge-runtime-{}", rand::random::<u64>()));
        std::fs::create_dir(&runtime).unwrap();
        let ring = FrkKeyring::new([&keys]).unwrap();
        let review_request = PurgeInventoryRequest {
            policy: MaintenanceRequest::default(),
            target_stable_id: object_id.to_owned(),
            expected_revision: 1,
        };
        let review = coordinator
            .maintenance_purge_inventory_v1(&ring, &review_request)
            .unwrap();
        assert!(review.blockers.is_empty(), "{:?}", review.blockers);
        let mut request = PurgeRequest {
            target: review_request,
            expected_inventory_digest: review.inventory.inventory_digest,
            recursive_inventory_digest: review.recursive_inventory_digest,
            waived_generations: review.required_waived_generations,
            action: "begin".to_owned(),
            max_items: 128,
        };
        assert!(coordinator
            .maintenance_purge_with_hook(&ring, &runtime, &request, &mut |pointer| if pointer
                == crash_pointer
            {
                Err(io::Error::other("injected purge publication crash"))
            } else {
                Ok(())
            })
            .is_err());
        let journal_path = std::fs::read_dir(&runtime)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        let parked = runtime.join("parked-runtime-journal");
        std::fs::rename(&journal_path, &parked).unwrap();
        let cold = CoreCommitCoordinator::new(&root, "maintenance-core").unwrap();
        if crash_pointer == CUTOVER_RECEIPT_FILE {
            let recovery_path = root.join(FS_DIRECTORY).join(REANCHOR_RECOVERY);
            let original = std::fs::read(&recovery_path).unwrap();
            let mut tampered = original.clone();
            *tampered.last_mut().unwrap() ^= 1;
            std::fs::write(&recovery_path, tampered).unwrap();
            assert!(
                cold.load_committed(&keys).is_err(),
                "corrupt recovery evidence must not repair pointers"
            );
            std::fs::write(recovery_path, original).unwrap();
        }
        let content = cold
            .load_committed(&keys)
            .expect("ordinary cold read must recover signed purge pointer publication")
            .unwrap();
        assert_eq!(content.head().generation(), 4);
        assert!(!content
            .catalog()
            .entries()
            .iter()
            .any(|entry| entry.stable_id().as_str() == object_id));
        drop(content);
        drop(cold);
        std::fs::rename(parked, journal_path).unwrap();
        let recovered = coordinator
            .maintenance_inventory_with_runtime_v1(&ring, &request.target.policy, Some(&runtime))
            .unwrap();
        assert!(recovered.resume_authorized);
        assert_eq!(recovered.operation.unwrap().kind, "purge");
        request.action = "resume".to_owned();
        request.target.target_stable_id.clear();
        request.target.expected_revision = 0;
        request.recursive_inventory_digest.clear();
        request.waived_generations.clear();
        let result = coordinator
            .maintenance_purge_v1(&ring, &runtime, &request)
            .unwrap();
        assert_eq!(result.operation.as_ref().unwrap().source_head_generation, 2);
        assert_eq!(result.operation.unwrap().state, "quarantined");
        request.action = "resume".to_owned();
        assert_eq!(
            coordinator
                .maintenance_purge_v1(&ring, &runtime, &request)
                .unwrap()
                .operation
                .unwrap()
                .state,
            "complete"
        );
        let result = coordinator.load_committed(&keys).unwrap().unwrap();
        assert_eq!(result.head().generation(), 4);
        assert!(!result
            .catalog()
            .entries()
            .iter()
            .any(|entry| entry.stable_id().as_str() == object_id));
        assert!(!coordinator
            .objects_path()
            .join(prepared.physical_name().as_str())
            .exists());
        let inventory = coordinator
            .maintenance_inventory_v1(&ring, &MaintenanceRequest::default())
            .unwrap();
        assert!(inventory.blockers.is_empty(), "{:?}", inventory.blockers);
        assert!(inventory.retained_catalog_count >= 2);
        drop(current);
        drop(result);
        drop(coordinator);
        std::fs::remove_dir_all(root).unwrap();
        std::fs::remove_dir_all(runtime).unwrap();
    }
}
