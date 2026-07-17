//! Deterministic release benchmark support for full immutable catalog commits.

use std::fs;
use std::path::Path;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::catalog::CatalogEntryCommon;
use crate::catalog::{
    encode_catalog_generation, CatalogClientMetadata, CatalogCutoverMarker, CatalogError,
    CatalogGeneration, CatalogGenerationEntry, FolderLifecycle, FolderTrashMetadata,
    MAX_CATALOG_PLAINTEXT_SIZE,
};
use crate::crypto::{
    derive_corefs_subkeys, unwrap_filesystem_root_key, wrap_filesystem_root_key, CryptoError,
    SecretBytes,
};
use crate::folders::{ClientId, FolderError, FolderOwner, PortableName};
use crate::id::{OpaqueId, OpaqueIdError};
use crate::policy::AnimaAccess;
use crate::transaction::{CommitError, CoreCommitCoordinator};

pub const MAX_CATALOG_PLAINTEXT_BYTES: usize = MAX_CATALOG_PLAINTEXT_SIZE;
pub const REFERENCE_WARMUP_COMMITS: usize = 30;
pub const REFERENCE_MEASURED_COMMITS: usize = 200;

const CORE_ID: &str = "catalog-reference-v1";
const CREDENTIAL: &str = "anima-corefs-catalog-reference-v1";
const CREDENTIAL_AAD: &[u8] = b"anima-corefs:catalog-reference-v1";
const SERIALIZED_SIZE_GENERATION: u64 = 100;
const CUTOVER_EPOCH: u64 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum FixtureKind {
    Medium,
    MaximumLive,
    SerializedLimit,
    TestOnly,
}

impl FixtureKind {
    pub const fn name(self) -> &'static str {
        match self {
            Self::Medium => "medium",
            Self::MaximumLive => "maximum-live",
            Self::SerializedLimit => "serialized-limit",
            Self::TestOnly => "test-only",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CatalogFixtureSpec {
    kind: FixtureKind,
    live_count: usize,
    tombstone_count: usize,
    target_serialized_size: Option<usize>,
}

impl CatalogFixtureSpec {
    pub const fn new(
        kind: FixtureKind,
        live_count: usize,
        tombstone_count: usize,
        target_serialized_size: Option<usize>,
    ) -> Self {
        Self {
            kind,
            live_count,
            tombstone_count,
            target_serialized_size,
        }
    }
}

#[derive(Clone, Debug)]
pub struct CatalogBenchmarkFixture {
    kind: FixtureKind,
    live_count: usize,
    tombstone_count: usize,
    entries: Vec<CatalogGenerationEntry>,
    serialized_size: usize,
    fingerprint: String,
}

impl CatalogBenchmarkFixture {
    pub const fn kind(&self) -> FixtureKind {
        self.kind
    }

    pub const fn live_count(&self) -> usize {
        self.live_count
    }

    pub const fn tombstone_count(&self) -> usize {
        self.tombstone_count
    }

    pub const fn total_count(&self) -> usize {
        self.live_count + self.tombstone_count
    }

    pub const fn serialized_size(&self) -> usize {
        self.serialized_size
    }

    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }

    fn catalog(
        &self,
        generation: u64,
        with_cutover_marker: bool,
    ) -> Result<CatalogGeneration, CatalogError> {
        let catalog = CatalogGeneration::new(generation, self.entries.clone())?;
        if with_cutover_marker {
            catalog.with_cutover_marker(CatalogCutoverMarker::new(CUTOVER_EPOCH)?)
        } else {
            Ok(catalog)
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum BenchmarkError {
    #[error("invalid benchmark fixture: {0}")]
    InvalidFixture(&'static str),
    #[error("maximum-live serialized catalog exceeds 16 MiB ({actual} bytes)")]
    MaximumLiveSizeGate { actual: usize },
    #[error("requested serialized catalog size exceeds 16 MiB ({requested} bytes)")]
    RequestedSizeGate { requested: usize },
    #[error("catalog benchmark target size {target} is smaller than the deterministic fixture ({actual} bytes)")]
    TargetTooSmall { target: usize, actual: usize },
    #[error("catalog error: {0}")]
    Catalog(#[from] CatalogError),
    #[error("commit error: {0}")]
    Commit(#[from] CommitError),
    #[error("crypto error: {0}")]
    Crypto(#[from] CryptoError),
    #[error("folder value error: {0}")]
    Folder(#[from] FolderError),
    #[error("opaque ID error: {0}")]
    OpaqueId(#[from] OpaqueIdError),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

pub fn build_fixture_matrix() -> Result<Vec<CatalogBenchmarkFixture>, BenchmarkError> {
    let medium = build_fixture(&CatalogFixtureSpec::new(
        FixtureKind::Medium,
        5_000,
        500,
        None,
    ))?;
    let maximum_live = build_fixture(&CatalogFixtureSpec::new(
        FixtureKind::MaximumLive,
        25_000,
        2_500,
        None,
    ))?;
    if maximum_live.serialized_size() > MAX_CATALOG_PLAINTEXT_BYTES {
        return Err(BenchmarkError::MaximumLiveSizeGate {
            actual: maximum_live.serialized_size(),
        });
    }

    let mut fixtures = vec![medium, maximum_live];
    if fixtures[1].serialized_size() < MAX_CATALOG_PLAINTEXT_BYTES {
        fixtures.push(build_fixture(&CatalogFixtureSpec::new(
            FixtureKind::SerializedLimit,
            25_000,
            0,
            Some(MAX_CATALOG_PLAINTEXT_BYTES),
        ))?);
    }
    Ok(fixtures)
}

pub fn build_fixture(spec: &CatalogFixtureSpec) -> Result<CatalogBenchmarkFixture, BenchmarkError> {
    if spec.live_count < 2 {
        return Err(BenchmarkError::InvalidFixture(
            "fixtures require root and trash live folders",
        ));
    }
    if spec.live_count + spec.tombstone_count > crate::catalog::MAX_CATALOG_ENTRIES {
        return Err(BenchmarkError::InvalidFixture(
            "catalog entry count exceeds V1 bound",
        ));
    }
    if spec
        .target_serialized_size
        .is_some_and(|target| target > MAX_CATALOG_PLAINTEXT_BYTES)
    {
        return Err(BenchmarkError::RequestedSizeGate {
            requested: spec.target_serialized_size.unwrap_or_default(),
        });
    }

    let (entries, encoded) = match spec.target_serialized_size {
        Some(target) => {
            let entries = fixture_entries(spec.live_count, spec.tombstone_count, Some(""))?;
            let encoded = encode_fixture(&entries)?;
            if encoded.len() > target {
                return Err(BenchmarkError::TargetTooSmall {
                    target,
                    actual: encoded.len(),
                });
            }
            let padding = "x".repeat(target - encoded.len());
            let entries = fixture_entries(spec.live_count, spec.tombstone_count, Some(&padding))?;
            let encoded = encode_fixture(&entries)?;
            if encoded.len() != target {
                return Err(BenchmarkError::InvalidFixture(
                    "serialized-size padding did not produce the exact target",
                ));
            }
            (entries, encoded)
        }
        None => {
            let entries = fixture_entries(spec.live_count, spec.tombstone_count, None)?;
            let encoded = encode_fixture(&entries)?;
            (entries, encoded)
        }
    };

    let fingerprint = hex_sha256(&encoded);
    Ok(CatalogBenchmarkFixture {
        kind: spec.kind,
        live_count: spec.live_count,
        tombstone_count: spec.tombstone_count,
        entries,
        serialized_size: encoded.len(),
        fingerprint,
    })
}

fn fixture_entries(
    live_count: usize,
    tombstone_count: usize,
    padding: Option<&str>,
) -> Result<Vec<CatalogGenerationEntry>, BenchmarkError> {
    let root_id = fixture_id(0)?;
    let trash_id = fixture_id(1)?;
    let mut root = common(root_id.clone(), None, "Core")?;
    if let Some(padding) = padding {
        let writer = ClientId::parse("benchmark.fixture")?;
        root = root.with_client_metadata(CatalogClientMetadata::new(
            &writer,
            vec![("client:benchmark.fixture:padding", json!(padding))],
        )?);
    }
    let mut entries = Vec::with_capacity(live_count + tombstone_count);
    entries.push(CatalogGenerationEntry::folder(root));
    entries.push(CatalogGenerationEntry::folder(common(
        trash_id.clone(),
        Some(root_id.clone()),
        "Trash",
    )?));

    for index in 2..live_count {
        entries.push(CatalogGenerationEntry::folder(common(
            fixture_id(index)?,
            Some(root_id.clone()),
            &format!("live-{index:05}"),
        )?));
    }
    for offset in 0..tombstone_count {
        let index = live_count + offset;
        let name = format!("deleted-{offset:05}");
        let common = common(fixture_id(index)?, Some(trash_id.clone()), &name)?
            .with_folder_lifecycle(FolderLifecycle::Trashed(FolderTrashMetadata::new(
                trash_id.clone(),
                root_id.clone(),
                PortableName::parse(&name)?,
                1_700_000_000_000_u64 + u64::try_from(offset).unwrap_or_default(),
            )?));
        entries.push(CatalogGenerationEntry::folder(common));
    }
    Ok(entries)
}

fn common(
    stable_id: OpaqueId,
    parent_id: Option<OpaqueId>,
    name: &str,
) -> Result<CatalogEntryCommon, BenchmarkError> {
    Ok(CatalogEntryCommon::new(
        stable_id,
        parent_id,
        PortableName::parse(name)?,
        FolderOwner::User,
        AnimaAccess::Write,
    ))
}

fn fixture_id(index: usize) -> Result<OpaqueId, BenchmarkError> {
    let encoded = format!("{index:026}");
    Ok(OpaqueId::parse(&encoded)?)
}

fn encode_fixture(entries: &[CatalogGenerationEntry]) -> Result<Vec<u8>, BenchmarkError> {
    let catalog = CatalogGeneration::new(SERIALIZED_SIZE_GENERATION, entries.to_vec())?
        .with_cutover_marker(CatalogCutoverMarker::new(CUTOVER_EPOCH)?)?;
    encode_catalog_generation(&catalog).map_err(Into::into)
}

fn hex_sha256(value: &[u8]) -> String {
    let digest: [u8; 32] = Sha256::digest(value).into();
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(output, "{byte:02x}");
    }
    output
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BenchmarkRunConfig {
    pub warmup_commits: usize,
    pub measured_commits: usize,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DurationPercentiles {
    p50: f64,
    p95: f64,
    p99: f64,
}

impl DurationPercentiles {
    fn from_samples(samples: &[Duration]) -> Self {
        Self {
            p50: duration_ms(percentile_nearest_rank(samples, 0.50)),
            p95: duration_ms(percentile_nearest_rank(samples, 0.95)),
            p99: duration_ms(percentile_nearest_rank(samples, 0.99)),
        }
    }

    pub fn p50(&self) -> Duration {
        Duration::from_secs_f64(self.p50 / 1_000.0)
    }

    pub fn p95(&self) -> Duration {
        Duration::from_secs_f64(self.p95 / 1_000.0)
    }

    pub fn p99(&self) -> Duration {
        Duration::from_secs_f64(self.p99 / 1_000.0)
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FixtureBenchmarkReport {
    name: &'static str,
    live_count: usize,
    tombstone_count: usize,
    total_count: usize,
    serialized_size_bytes: usize,
    fixture_sha256: String,
    warmup_commits: usize,
    sample_count: usize,
    final_head_generation: u64,
    bytes_written: u64,
    total_bytes_written: u64,
    commit_ms: DurationPercentiles,
    lock_hold_ms: DurationPercentiles,
    publication_path: [&'static str; 8],
}

impl FixtureBenchmarkReport {
    pub const fn final_head_generation(&self) -> u64 {
        self.final_head_generation
    }

    pub const fn lock_hold(&self) -> &DurationPercentiles {
        &self.lock_hold_ms
    }
}

pub fn percentile_nearest_rank(samples: &[Duration], percentile: f64) -> Duration {
    assert!(
        !samples.is_empty(),
        "percentile requires at least one sample"
    );
    assert!((0.0..=1.0).contains(&percentile));
    let mut sorted = samples.to_vec();
    sorted.sort_unstable();
    let rank = (percentile * sorted.len() as f64).ceil() as usize;
    sorted[rank.saturating_sub(1).min(sorted.len() - 1)]
}

pub fn run_fixture_benchmark(
    root: &Path,
    fixture: &CatalogBenchmarkFixture,
    config: BenchmarkRunConfig,
) -> Result<FixtureBenchmarkReport, BenchmarkError> {
    if config.measured_commits == 0 {
        return Err(BenchmarkError::InvalidFixture(
            "at least one measured commit is required",
        ));
    }
    let final_generation = 2_usize
        .checked_add(config.warmup_commits)
        .and_then(|value| value.checked_add(config.measured_commits))
        .ok_or(BenchmarkError::InvalidFixture(
            "benchmark generation overflow",
        ))?;
    if final_generation >= 1_000 {
        return Err(BenchmarkError::InvalidFixture(
            "benchmark fixture size is calibrated for three-digit generations",
        ));
    }

    fs::create_dir_all(root)?;
    let coordinator = CoreCommitCoordinator::new(root, CORE_ID)?;
    let frk = SecretBytes::new(vec![0x42; 32])?;
    let wrapped = wrap_filesystem_root_key(CREDENTIAL, &frk, CREDENTIAL_AAD)?;
    let unlocked = unwrap_filesystem_root_key(CREDENTIAL, &wrapped, CREDENTIAL_AAD)?;
    let keys = derive_corefs_subkeys(&unlocked, 1)?;

    coordinator.initialize_validation_snapshot(&keys, &[], |generation| {
        fixture.catalog(generation, false)
    })?;
    coordinator.commit_first_mutation(
        &keys,
        CUTOVER_EPOCH,
        &[],
        &[],
        |_, generation| fixture.catalog(generation, false),
        |_| Ok(()),
    )?;

    for _ in 0..config.warmup_commits {
        coordinator.commit(
            &keys,
            &[],
            &[],
            |current, generation| {
                CatalogGeneration::new(
                    generation,
                    current
                        .expect("initialized benchmark catalog")
                        .entries()
                        .to_vec(),
                )
            },
            |_| Ok(()),
        )?;
    }

    let mut commit_samples = Vec::with_capacity(config.measured_commits);
    let mut lock_samples = Vec::with_capacity(config.measured_commits);
    let mut bytes_written = 0_u64;
    let mut total_bytes_written = 0_u64;
    let mut final_head_generation = 0_u64;
    for _ in 0..config.measured_commits {
        let started = Instant::now();
        let outcome = coordinator.commit(
            &keys,
            &[],
            &[],
            |current, generation| {
                CatalogGeneration::new(
                    generation,
                    current
                        .expect("initialized benchmark catalog")
                        .entries()
                        .to_vec(),
                )
            },
            |_| Ok(()),
        )?;
        commit_samples.push(started.elapsed());
        lock_samples.push(outcome.lock_hold_duration());
        bytes_written = bytes_written.max(outcome.bytes_written());
        total_bytes_written = total_bytes_written.saturating_add(outcome.bytes_written());
        final_head_generation = outcome.generation();
    }

    Ok(FixtureBenchmarkReport {
        name: fixture.kind().name(),
        live_count: fixture.live_count(),
        tombstone_count: fixture.tombstone_count(),
        total_count: fixture.total_count(),
        serialized_size_bytes: fixture.serialized_size(),
        fixture_sha256: fixture.fingerprint().to_owned(),
        warmup_commits: config.warmup_commits,
        sample_count: config.measured_commits,
        final_head_generation,
        bytes_written,
        total_bytes_written,
        commit_ms: DurationPercentiles::from_samples(&commit_samples),
        lock_hold_ms: DurationPercentiles::from_samples(&lock_samples),
        publication_path: [
            "serialize",
            "encrypt",
            "temporary-file-write",
            "durable-flush",
            "atomic-rename",
            "directory-durability",
            "fs-head-write-flush",
            "commit-lock",
        ],
    })
}

fn duration_ms(value: Duration) -> f64 {
    value.as_secs_f64() * 1_000.0
}
