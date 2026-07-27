//! Deterministic release benchmark support for full immutable catalog commits.

use std::collections::BTreeMap;
use std::fs;
#[cfg(windows)]
use std::fs::File;
use std::io::Cursor;
#[cfg(windows)]
use std::io::Read;
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::process::Command;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::mem::MaybeUninit;
#[cfg(windows)]
use std::os::windows::ffi::OsStrExt as _;
#[cfg(windows)]
use std::os::windows::io::AsRawHandle as _;
#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{
    GetFileInformationByHandle, GetVolumeInformationW, GetVolumePathNameW,
    BY_HANDLE_FILE_INFORMATION,
};
#[cfg(windows)]
use windows_sys::Win32::System::Threading::{GetCurrentProcess, GetProcessHandleCount};

use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::catalog::CatalogEntryCommon;
use crate::catalog::{
    encode_catalog_generation, CatalogClientMetadata, CatalogCutoverMarker, CatalogError,
    CatalogGeneration, CatalogGenerationEntry, CatalogObject, ContentHash, FolderLifecycle,
    ObjectLifecycle, ObjectPhysicalName, WrappedObjectDekRecord, MAX_CATALOG_PLAINTEXT_SIZE,
};
use crate::crypto::{
    derive_corefs_subkeys, unwrap_filesystem_root_key, wrap_filesystem_root_key, CryptoError,
    FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes, OBJECT_KEY_ENVELOPE_VERSION,
    OBJECT_WRAP_ALGORITHM,
};
use crate::envelope::{
    encode_envelope, BodyEncoding, EnvelopeError, EnvelopeMetadata, ENVELOPE_VERSION,
};
use crate::folders::{ClientId, FolderError, FolderOwner, PortableName};
use crate::id::{OpaqueId, OpaqueIdError};
use crate::policy::AnimaAccess;
use crate::transaction::{
    CatalogPrecondition, CommitConflict, CommitError, CoreCommitCoordinator, PreparedObjectRevision,
};

pub const MAX_CATALOG_PLAINTEXT_BYTES: usize = MAX_CATALOG_PLAINTEXT_SIZE;
pub const REFERENCE_WARMUP_COMMITS: usize = 30;
pub const REFERENCE_MEASURED_COMMITS: usize = 200;

const CORE_ID: &str = "catalog-reference-v1";
const CREDENTIAL: &str = "anima-corefs-catalog-reference-v1";
const CREDENTIAL_AAD: &[u8] = b"anima-corefs:catalog-reference-v1";
const SERIALIZED_SIZE_GENERATION: u64 = 100;
const CUTOVER_EPOCH: u64 = 1;
const FIXTURE_MANIFEST_FINGERPRINT_DOMAIN: &str =
    "anima-corefs-catalog-benchmark-fixture-manifest-v1";
const BENCHMARK_OBJECT_KEY_DOMAIN: &[u8] = b"anima-corefs-catalog-benchmark-object-key-v1\0";
const BENCHMARK_OBJECT_BODY: &[u8] = b"benchmark tombstone\n";
const BENCHMARK_OBJECT_TIMESTAMP: &str = "2026-07-18T00:00:00Z";
const BENCHMARK_OBJECT_MEDIA_TYPE: &str = "text/plain";
const BENCHMARK_OBJECT_REVISION: u64 = 1;
const BENCHMARK_OBJECT_KEY_EPOCH: u32 = 1;
const REFERENCE_GENERATION_WIDTHS: [(usize, u64); 3] = [(1, 9), (2, 99), (3, 100)];

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
    target_serialized_size: Option<usize>,
    padding_by_generation_width: BTreeMap<usize, usize>,
    precalibrated_generation_widths: Vec<usize>,
    serialized_size: usize,
    manifest_fingerprint: String,
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

    pub fn manifest_fingerprint(&self) -> &str {
        &self.manifest_fingerprint
    }

    pub fn lifecycle_counts(&self) -> Result<(usize, usize), BenchmarkError> {
        derive_lifecycle_counts_from_entries(&self.entries)
    }

    pub fn precalibrated_generation_widths(&self) -> &[usize] {
        &self.precalibrated_generation_widths
    }

    pub fn calibrated_serialized_size(&self, generation: u64) -> Option<usize> {
        self.padding_by_generation_width
            .contains_key(&generation.to_string().len())
            .then_some(self.target_serialized_size?)
    }

    fn catalog(
        &self,
        entries: &[CatalogGenerationEntry],
        generation: u64,
        with_cutover_marker: bool,
    ) -> Result<CatalogGeneration, CatalogError> {
        self.catalog_with_size_marker(
            entries,
            generation,
            with_cutover_marker,
            with_cutover_marker,
        )
    }

    fn commit_catalog(
        &self,
        entries: &[CatalogGenerationEntry],
        generation: u64,
    ) -> Result<CatalogGeneration, CatalogError> {
        self.catalog_with_size_marker(entries, generation, false, true)
    }

    fn catalog_with_size_marker(
        &self,
        source_entries: &[CatalogGenerationEntry],
        generation: u64,
        with_cutover_marker: bool,
        _size_includes_cutover_marker: bool,
    ) -> Result<CatalogGeneration, CatalogError> {
        let entries =
            if self.target_serialized_size.is_some() {
                let width = generation.to_string().len();
                let padding = self.padding_by_generation_width.get(&width).ok_or(
                    CatalogError::InvalidFormat("benchmark generation width was not precalibrated"),
                )?;
                entries_with_padding(source_entries, *padding)?
            } else {
                source_entries.to_vec()
            };
        catalog_from_entries(generation, entries, with_cutover_marker)
    }
}

fn catalog_from_entries(
    generation: u64,
    entries: Vec<CatalogGenerationEntry>,
    with_cutover_marker: bool,
) -> Result<CatalogGeneration, CatalogError> {
    let catalog = CatalogGeneration::new(generation, entries)?;
    if with_cutover_marker {
        catalog.with_cutover_marker(CatalogCutoverMarker::new(CUTOVER_EPOCH)?)
    } else {
        Ok(catalog)
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
    #[error("commit precondition error: {0}")]
    CommitConflict(#[from] CommitConflict),
    #[error("crypto error: {0}")]
    Crypto(#[from] CryptoError),
    #[error("object envelope error: {0}")]
    Envelope(#[from] EnvelopeError),
    #[error("folder value error: {0}")]
    Folder(#[from] FolderError),
    #[error("opaque ID error: {0}")]
    OpaqueId(#[from] OpaqueIdError),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("object-validation lease backend unavailable: {0}")]
    BackendUnavailable(&'static str),
    #[error("object-validation lease diagnostic invariant failed: {0}")]
    DiagnosticInvariant(&'static str),
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
    let mut fixtures = vec![medium, maximum_live];
    if needs_serialized_limit_fixture(fixtures[1].serialized_size())? {
        fixtures.push(build_fixture(&CatalogFixtureSpec::new(
            FixtureKind::SerializedLimit,
            25_000,
            0,
            Some(MAX_CATALOG_PLAINTEXT_BYTES),
        ))?);
    }
    Ok(fixtures)
}

pub fn needs_serialized_limit_fixture(maximum_live_size: usize) -> Result<bool, BenchmarkError> {
    if maximum_live_size > MAX_CATALOG_PLAINTEXT_BYTES {
        return Err(BenchmarkError::MaximumLiveSizeGate {
            actual: maximum_live_size,
        });
    }
    Ok(maximum_live_size < MAX_CATALOG_PLAINTEXT_BYTES)
}

pub fn expected_reference_fixture_manifest_fingerprint(
    kind: FixtureKind,
) -> Result<&'static str, BenchmarkError> {
    match kind {
        FixtureKind::Medium => {
            Ok("d1f8817ba635359cc10208d86b79652dc0e2180c2514f1e1d0634a96ebcb40c4")
        }
        FixtureKind::MaximumLive => {
            Ok("1c37d0254fbb9852b5789fa39811f0e1a23a4a3ae440b20c9c478fbf8bf9f7b5")
        }
        FixtureKind::SerializedLimit => {
            Ok("26c1c693e8b564e6a971c0af6b62b9b223612bea8bc7c0fe71388abfb06fbd87")
        }
        FixtureKind::TestOnly => Err(BenchmarkError::InvalidFixture(
            "test-only fixtures have no release fingerprint",
        )),
    }
}

fn fixture_manifest_fingerprint(
    spec: &CatalogFixtureSpec,
    entries: &[CatalogGenerationEntry],
    padding_by_generation_width: &BTreeMap<usize, usize>,
) -> Result<String, BenchmarkError> {
    let target = spec
        .target_serialized_size
        .map_or_else(|| "none".to_owned(), |value| value.to_string());
    let mut hasher = Sha256::new();
    hasher.update(format!(
        "{FIXTURE_MANIFEST_FINGERPRINT_DOMAIN}\0name={}\0live={}\0tombstones={}\0target={target}\0",
        spec.kind.name(),
        spec.live_count,
        spec.tombstone_count
    ));
    hasher.update(b"object-preparation-recipe\0");
    hasher.update(BENCHMARK_OBJECT_KEY_DOMAIN);
    hasher.update(BENCHMARK_OBJECT_BODY);
    hasher.update(BENCHMARK_OBJECT_TIMESTAMP.as_bytes());
    hasher.update(BENCHMARK_OBJECT_MEDIA_TYPE.as_bytes());
    hasher.update(BENCHMARK_OBJECT_REVISION.to_le_bytes());
    hasher.update(BENCHMARK_OBJECT_KEY_EPOCH.to_le_bytes());
    hasher.update(ObjectKind::Note.as_str().as_bytes());
    hasher.update(ENVELOPE_VERSION.to_le_bytes());
    hasher.update(OBJECT_KEY_ENVELOPE_VERSION.to_le_bytes());
    hasher.update(OBJECT_WRAP_ALGORITHM.as_bytes());
    let unpadded = encode_fixture(entries)?;
    hasher.update(b"logical-catalog-generation-100\0");
    hasher.update(
        u64::try_from(unpadded.len())
            .unwrap_or(u64::MAX)
            .to_le_bytes(),
    );
    hasher.update(&unpadded);
    for (width, generation) in REFERENCE_GENERATION_WIDTHS {
        let Some(padding) = padding_by_generation_width.get(&width) else {
            continue;
        };
        let padded_entries = entries_with_padding(entries, *padding)?;
        let catalog = catalog_from_entries(generation, padded_entries, true)?;
        let encoded = encode_catalog_generation(&catalog)?;
        hasher.update(b"calibrated-generation-variant\0");
        hasher.update(u64::try_from(width).unwrap_or(u64::MAX).to_le_bytes());
        hasher.update(generation.to_le_bytes());
        hasher.update(u64::try_from(*padding).unwrap_or(u64::MAX).to_le_bytes());
        hasher.update(
            u64::try_from(encoded.len())
                .unwrap_or(u64::MAX)
                .to_le_bytes(),
        );
        hasher.update(&encoded);
    }
    let digest = hasher.finalize();
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(output, "{byte:02x}");
    }
    Ok(output)
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

    let entries = fixture_entries(
        spec.live_count,
        spec.tombstone_count,
        spec.target_serialized_size.map(|_| ""),
    )?;
    let unpadded = encode_fixture(&entries)?;
    if let Some(target) = spec.target_serialized_size {
        if unpadded.len() > target {
            return Err(BenchmarkError::TargetTooSmall {
                target,
                actual: unpadded.len(),
            });
        }
    }
    let mut fixture = CatalogBenchmarkFixture {
        kind: spec.kind,
        live_count: spec.live_count,
        tombstone_count: spec.tombstone_count,
        entries,
        target_serialized_size: spec.target_serialized_size,
        padding_by_generation_width: BTreeMap::new(),
        precalibrated_generation_widths: Vec::new(),
        serialized_size: 0,
        manifest_fingerprint: String::new(),
    };
    if let Some(target) = spec.target_serialized_size {
        for (width, generation) in REFERENCE_GENERATION_WIDTHS {
            let unpadded = catalog_from_entries(generation, fixture.entries.clone(), true)?;
            let actual = encode_catalog_generation(&unpadded)?.len();
            let padding = target
                .checked_sub(actual)
                .ok_or(BenchmarkError::TargetTooSmall { target, actual })?;
            let calibrated = catalog_from_entries(
                generation,
                entries_with_padding(&fixture.entries, padding)?,
                true,
            )?;
            let calibrated_size = encode_catalog_generation(&calibrated)?.len();
            if calibrated_size != target {
                return Err(BenchmarkError::InvalidFixture(
                    "benchmark serialized-size calibration drifted",
                ));
            }
            fixture.padding_by_generation_width.insert(width, padding);
            fixture.precalibrated_generation_widths.push(width);
        }
    }
    fixture.manifest_fingerprint =
        fixture_manifest_fingerprint(spec, &fixture.entries, &fixture.padding_by_generation_width)?;
    let encoded = encode_catalog_generation(&fixture.catalog(
        &fixture.entries,
        SERIALIZED_SIZE_GENERATION,
        true,
    )?)?;

    fixture.serialized_size = encoded.len();
    let actual_counts = fixture.lifecycle_counts()?;
    if actual_counts != (spec.live_count, spec.tombstone_count) {
        return Err(BenchmarkError::InvalidFixture(
            "fixture lifecycle counts contradict the advertised counts",
        ));
    }
    Ok(fixture)
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
        let stable_id = fixture_id(index)?;
        let common = common(stable_id.clone(), Some(trash_id.clone()), &name)?;
        entries.push(CatalogGenerationEntry::object(
            common,
            placeholder_tombstone_object(
                &stable_id,
                &trash_id,
                1_700_000_000_000_u64 + u64::try_from(offset).unwrap_or_default(),
            )?,
        ));
    }
    Ok(entries)
}

fn placeholder_tombstone_object(
    stable_id: &OpaqueId,
    trash_id: &OpaqueId,
    deleted_at_ms: u64,
) -> Result<CatalogObject, BenchmarkError> {
    let digest = Sha256::digest(stable_id.as_str().as_bytes());
    let physical_name = ObjectPhysicalName::parse(&format!(
        "object-{}.acore",
        hex_sha256(&digest)[..32].to_owned()
    ))?;
    let content_hash = ContentHash::parse(&hex_sha256(BENCHMARK_OBJECT_BODY))?;
    let wrapped_dek = WrappedObjectDekRecord::from_parts(
        1,
        1,
        OBJECT_WRAP_ALGORITHM,
        OBJECT_KEY_ENVELOPE_VERSION,
        &digest[..12],
        vec![digest[12]; 48],
    )?;
    Ok(CatalogObject::new(
        1,
        physical_name,
        content_hash,
        ObjectKind::Note,
        wrapped_dek,
        ObjectLifecycle::tombstone(trash_id.clone(), deleted_at_ms)?,
    )?)
}

pub fn derive_fixture_lifecycle_counts(
    catalog: &CatalogGeneration,
) -> Result<(usize, usize), BenchmarkError> {
    derive_lifecycle_counts_from_entries(catalog.entries())
}

fn derive_lifecycle_counts_from_entries(
    entries: &[CatalogGenerationEntry],
) -> Result<(usize, usize), BenchmarkError> {
    let mut live = 0_usize;
    let mut tombstones = 0_usize;
    for entry in entries {
        match entry {
            CatalogGenerationEntry::Folder(common) => match common.folder_lifecycle() {
                FolderLifecycle::Live => live += 1,
                FolderLifecycle::Trashed(_) => {
                    return Err(BenchmarkError::InvalidFixture(
                        "trashed folder is not a catalog tombstone",
                    ));
                }
            },
            CatalogGenerationEntry::Object(_, object) => match object.lifecycle() {
                ObjectLifecycle::Live => live += 1,
                ObjectLifecycle::Tombstone { .. } => tombstones += 1,
                ObjectLifecycle::Trashed(_) => {
                    return Err(BenchmarkError::InvalidFixture(
                        "trashed object is not a catalog tombstone",
                    ));
                }
            },
        }
    }
    Ok((live, tombstones))
}

fn entries_with_padding(
    source_entries: &[CatalogGenerationEntry],
    padding_size: usize,
) -> Result<Vec<CatalogGenerationEntry>, CatalogError> {
    let mut entries = source_entries.to_vec();
    let writer = ClientId::parse("benchmark.fixture")
        .map_err(|_| CatalogError::InvalidFormat("benchmark client ID"))?;
    let root = match entries.first() {
        Some(CatalogGenerationEntry::Folder(common)) => common.clone(),
        Some(CatalogGenerationEntry::Object(_, _)) | None => {
            return Err(CatalogError::InvalidFormat("benchmark root entry"));
        }
    };
    entries[0] =
        CatalogGenerationEntry::folder(root.with_client_metadata(CatalogClientMetadata::new(
            &writer,
            vec![(
                "client:benchmark.fixture:padding",
                json!("x".repeat(padding_size)),
            )],
        )?));
    Ok(entries)
}

fn prepare_fixture_entries(
    fixture: &CatalogBenchmarkFixture,
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
) -> Result<(Vec<CatalogGenerationEntry>, Vec<PreparedObjectRevision>), BenchmarkError> {
    let mut entries = Vec::with_capacity(fixture.entries.len());
    let mut prepared_revisions = Vec::with_capacity(fixture.tombstone_count);
    for entry in &fixture.entries {
        let CatalogGenerationEntry::Object(common, object) = entry else {
            entries.push(entry.clone());
            continue;
        };
        let ObjectLifecycle::Tombstone {
            trash_folder_id,
            deleted_at_ms,
        } = object.lifecycle()
        else {
            return Err(BenchmarkError::InvalidFixture(
                "benchmark object is not a true tombstone",
            ));
        };
        let stable_id = common.stable_id();
        let mut hasher = Sha256::new();
        hasher.update(BENCHMARK_OBJECT_KEY_DOMAIN);
        hasher.update(stable_id.as_str().as_bytes());
        let object_key = SecretBytes::new(hasher.finalize().to_vec())?;
        let aad = ObjectBaseAad::new(
            CORE_ID,
            stable_id.as_str(),
            ObjectKind::Note,
            ENVELOPE_VERSION,
            BENCHMARK_OBJECT_KEY_EPOCH,
            BENCHMARK_OBJECT_REVISION,
        )?;
        let metadata = EnvelopeMetadata::for_body(
            ObjectKind::Note.as_str(),
            stable_id.as_str(),
            BENCHMARK_OBJECT_REVISION,
            BENCHMARK_OBJECT_TIMESTAMP,
            BENCHMARK_OBJECT_TIMESTAMP,
            BENCHMARK_OBJECT_MEDIA_TYPE,
            BTreeMap::new(),
            BodyEncoding::Utf8,
            BENCHMARK_OBJECT_BODY,
        )?;
        let encoded = encode_envelope(&object_key, &aad, &metadata, BENCHMARK_OBJECT_BODY)?;
        let prepared = coordinator.prepare_object_revision(
            keys,
            &object_key,
            &aad,
            &mut Cursor::new(encoded),
        )?;
        let actual = CatalogObject::new(
            prepared.revision(),
            prepared.physical_name().clone(),
            prepared.content_hash().clone(),
            ObjectKind::Note,
            prepared.wrapped_dek().clone(),
            ObjectLifecycle::tombstone(trash_folder_id.clone(), *deleted_at_ms)?,
        )?;
        entries.push(CatalogGenerationEntry::object(common.clone(), actual));
        prepared_revisions.push(prepared);
    }
    Ok((entries, prepared_revisions))
}

fn verify_prepared_fixture_manifest(
    fixture: &CatalogBenchmarkFixture,
    prepared_entries: &[CatalogGenerationEntry],
    prepared_revisions: &[PreparedObjectRevision],
) -> Result<(), BenchmarkError> {
    if fixture.entries.len() != prepared_entries.len()
        || fixture.tombstone_count != prepared_revisions.len()
    {
        return Err(BenchmarkError::InvalidFixture(
            "prepared fixture does not match manifest cardinality",
        ));
    }
    let expected_content_hash = ContentHash::parse(&hex_sha256(BENCHMARK_OBJECT_BODY))?;
    let mut revisions = prepared_revisions.iter();
    for (manifest_entry, actual_entry) in fixture.entries.iter().zip(prepared_entries) {
        match (manifest_entry, actual_entry) {
            (CatalogGenerationEntry::Folder(expected), CatalogGenerationEntry::Folder(actual))
                if expected == actual => {}
            (
                CatalogGenerationEntry::Object(expected_common, expected_object),
                CatalogGenerationEntry::Object(actual_common, actual_object),
            ) => {
                let Some(prepared) = revisions.next() else {
                    return Err(BenchmarkError::InvalidFixture(
                        "prepared fixture is missing an object revision",
                    ));
                };
                let wrapped = actual_object.wrapped_dek();
                let wrapped_payload = wrapped.to_wrapped_object_dek()?;
                if expected_common != actual_common
                    || expected_object.lifecycle() != actual_object.lifecycle()
                    || actual_object.revision() != BENCHMARK_OBJECT_REVISION
                    || actual_object.kind() != ObjectKind::Note
                    || actual_object.content_hash() != &expected_content_hash
                    || actual_object.physical_name() != prepared.physical_name()
                    || actual_object.content_hash() != prepared.content_hash()
                    || actual_object.wrapped_dek() != prepared.wrapped_dek()
                    || prepared.object_id() != actual_common.stable_id()
                    || prepared.revision() != BENCHMARK_OBJECT_REVISION
                    || prepared.object_key_epoch() != BENCHMARK_OBJECT_KEY_EPOCH
                    || wrapped.frk_version() != 1
                    || wrapped.object_key_epoch() != BENCHMARK_OBJECT_KEY_EPOCH
                    || wrapped_payload.algorithm() != OBJECT_WRAP_ALGORITHM
                    || wrapped_payload.envelope_version() != OBJECT_KEY_ENVELOPE_VERSION
                {
                    return Err(BenchmarkError::InvalidFixture(
                        "prepared object does not project to the fixture manifest",
                    ));
                }
            }
            _ => {
                return Err(BenchmarkError::InvalidFixture(
                    "prepared fixture entry kind or content changed",
                ));
            }
        }
    }
    if revisions.next().is_some() {
        return Err(BenchmarkError::InvalidFixture(
            "prepared fixture has an extra object revision",
        ));
    }
    Ok(())
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
    hex_sha256_digest(digest)
}

fn hex_sha256_digest(digest: [u8; 32]) -> String {
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(output, "{byte:02x}");
    }
    output
}

#[cfg(test)]
mod fingerprint_tests {
    use super::*;

    #[test]
    fn fixture_manifest_fingerprint_changes_when_logical_entry_content_changes() {
        let spec = CatalogFixtureSpec::new(FixtureKind::TestOnly, 3, 0, None);
        let entries = fixture_entries(3, 0, None).unwrap();
        let original = fixture_manifest_fingerprint(&spec, &entries, &BTreeMap::new()).unwrap();
        let mut renamed = entries.clone();
        renamed[2] = CatalogGenerationEntry::folder(
            common(
                fixture_id(2).unwrap(),
                Some(fixture_id(0).unwrap()),
                "renamed",
            )
            .unwrap(),
        );
        let changed = fixture_manifest_fingerprint(&spec, &renamed, &BTreeMap::new()).unwrap();

        assert_ne!(original, changed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measured_interval_wraps_the_complete_public_commit_callback() {
        let mut events = Vec::new();
        let minimum_elapsed = Duration::from_millis(20);

        let (result, elapsed) = measure_public_commit(|| {
            events.push("entry");
            std::thread::sleep(minimum_elapsed);
            events.push("exit");
            "complete"
        });

        assert_eq!(result, "complete");
        assert_eq!(events, ["entry", "exit"]);
        assert!(elapsed >= minimum_elapsed);
    }

    #[cfg(windows)]
    #[test]
    fn diagnostic_clean_status_covers_root_build_inputs_outside_corefs() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-diagnostic-clean-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("packages").join("anima-corefs")).unwrap();
        std::fs::write(root.join("Cargo.toml"), b"[workspace]\n").unwrap();
        std::fs::write(root.join("packages/anima-corefs/marker"), b"corefs").unwrap();

        let git = |arguments: &[&str]| {
            let status = Command::new("git")
                .arg("-C")
                .arg(&root)
                .args(arguments)
                .status()
                .unwrap();
            assert!(status.success(), "git command failed: {arguments:?}");
        };
        git(&["init", "--quiet"]);
        git(&["add", "."]);
        git(&[
            "-c",
            "user.name=ANIMA Test",
            "-c",
            "user.email=anima-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ]);
        assert!(git_worktree_is_clean(&root).unwrap());

        std::fs::write(root.join("Cargo.toml"), b"[workspace]\nresolver = \"2\"\n").unwrap();
        assert!(
            !git_worktree_is_clean(&root).unwrap(),
            "a dirty root Cargo.toml must make the diagnostic source unclean"
        );

        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn diagnostic_file_hash_is_single_sha256() {
        let path = std::env::temp_dir().join(format!(
            "anima-corefs-diagnostic-hash-{}",
            std::process::id()
        ));
        std::fs::write(&path, b"abc").unwrap();

        assert_eq!(
            sha256_file(&path).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );

        std::fs::remove_file(path).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn native_acceptance_rejects_every_provenance_relationship_mismatch() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-diagnostic-provenance-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        let repository_root = root.join("repo");
        let executable = repository_root.join("target/release/object_lease_diagnostic.exe");
        let target = root.join("diagnostic-target");
        let output = root.join("diagnostic.json");
        std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(repository_root.join("Cargo.lock"), b"lock").unwrap();
        std::fs::write(&executable, b"binary").unwrap();
        std::fs::write(&output, b"{}").unwrap();

        let canonical_repository_root = std::fs::canonicalize(&repository_root).unwrap();
        let canonical_executable = std::fs::canonicalize(&executable).unwrap();
        let canonical_target = std::fs::canonicalize(&target).unwrap();
        let canonical_output = std::fs::canonicalize(&output).unwrap();
        let report = ObjectLeaseDiagnosticReport {
            schema_version: 1,
            platform: ObjectLeaseDiagnosticPlatform::Windows,
            hardware: ObjectLeaseDiagnosticHardware {
                architecture: "x86_64",
                logical_processors: 1,
            },
            os: ObjectLeaseDiagnosticOs {
                family: "windows",
                version: "test".to_owned(),
            },
            filesystem: ObjectLeaseDiagnosticFilesystem {
                name: "NTFS".to_owned(),
                target: canonical_target.clone(),
            },
            build: ObjectLeaseDiagnosticBuild {
                source: ObjectLeaseDiagnosticSource {
                    repository_root: canonical_repository_root.clone(),
                    commit: "a".repeat(40),
                    clean: true,
                },
                executable: ObjectLeaseDiagnosticExecutable {
                    canonical_path: canonical_executable,
                    sha256: "b".repeat(64),
                    volume_serial: 1,
                    file_id: 2,
                },
                cargo_lock: ObjectLeaseDiagnosticCargoLock {
                    canonical_path: std::fs::canonicalize(repository_root.join("Cargo.lock"))
                        .unwrap(),
                    working_sha256: "c".repeat(64),
                    committed_sha256: "c".repeat(64),
                    matches_commit: true,
                },
                target: ObjectLeaseDiagnosticFileIdentity {
                    canonical_path: canonical_target.clone(),
                    volume_serial: 1,
                    file_id: 3,
                },
                output: Some(ObjectLeaseDiagnosticFileIdentity {
                    canonical_path: canonical_output,
                    volume_serial: 1,
                    file_id: 4,
                }),
                argv: vec![
                    executable.to_string_lossy().into_owned(),
                    "--target".to_owned(),
                    target.to_string_lossy().into_owned(),
                    "--objects".to_owned(),
                    "2500".to_owned(),
                    "--warmups".to_owned(),
                    "30".to_owned(),
                    "--samples".to_owned(),
                    "200".to_owned(),
                    "--mutation-matrix".to_owned(),
                    "--output".to_owned(),
                    output.to_string_lossy().into_owned(),
                ],
                native_test_contract: ObjectLeaseDiagnosticNativeTestContract {
                    source_commit: "a".repeat(40),
                    required_tests: vec!["native-test"],
                },
                crate_version: "0.1.0",
                debug_assertions: false,
                architecture: "x86_64",
            },
            object_count: 2500,
            warmups: 30,
            samples: 200,
            safe_open: ObjectLeaseDiagnosticPercentiles {
                p50_ms: 1.0,
                p95_ms: 1.0,
                p99_ms: 1.0,
            },
            lease: ObjectLeaseDiagnosticLeaseMetrics {
                p50_ms: 1.0,
                p95_ms: 1.0,
                p99_ms: 1.0,
                safe_open_count: 0,
                metadata_query_count: 2500,
                fence_count: 2,
            },
            resources: ObjectLeaseDiagnosticResources {
                live_entry_permits: 2500,
                live_lease_permits: 1,
                live_monitor_resources: 3,
                post_teardown_entry_permits: 0,
                post_teardown_lease_permits: 0,
                post_teardown_monitor_resources: 0,
                descriptor_delta: 0,
            },
            teardown: ObjectLeaseDiagnosticTeardown {
                target_ms: Some(2000),
                elapsed_ms: Some(1.0),
                completion_confirmed: true,
                target_met: Some(true),
            },
            correctness: ObjectLeaseDiagnosticCorrectness {
                ordered_boundary_proven: true,
                mutation_matrix_passed: true,
                teardown_passed: true,
            },
            residue_count: 0,
        };
        assert!(report.native_acceptance_passed());

        let mut invalid = report.clone();
        invalid.build.source.clean = false;
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report.clone();
        invalid.build.source.commit = "d".repeat(40);
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report.clone();
        invalid.build.executable.sha256 = "not-hex".to_owned();
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report.clone();
        invalid.build.cargo_lock.matches_commit = false;
        assert!(!invalid.native_acceptance_passed());
        let mut line_ending_normalized = report.clone();
        line_ending_normalized.build.cargo_lock.working_sha256 = "d".repeat(64);
        assert!(
            line_ending_normalized.native_acceptance_passed(),
            "git cleanliness accepts line-ending-normalized lockfiles even when raw hashes differ"
        );
        let mut invalid = report.clone();
        invalid.build.cargo_lock.working_sha256 = "not-hex".to_owned();
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report.clone();
        invalid.build.argv[4] = "02500".to_owned();
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report.clone();
        invalid.build.argv.swap(5, 7);
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report.clone();
        invalid.build.argv[2] = root.join("different-target").to_string_lossy().into_owned();
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report.clone();
        invalid.build.argv[11] = root.join("different.json").to_string_lossy().into_owned();
        assert!(!invalid.native_acceptance_passed());
        let mut invalid = report;
        invalid.filesystem.target = root.join("different-target");
        assert!(!invalid.native_acceptance_passed());

        std::fs::remove_dir_all(root).unwrap();
    }
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

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct ByteRange {
    min: usize,
    max: usize,
}

impl ByteRange {
    fn from_samples(samples: &[usize]) -> Option<Self> {
        Some(Self {
            min: *samples.iter().min()?,
            max: *samples.iter().max()?,
        })
    }
}

fn require_measured_catalog_size(
    fixture: &CatalogBenchmarkFixture,
    actual: usize,
) -> Result<(), BenchmarkError> {
    if actual > MAX_CATALOG_PLAINTEXT_BYTES {
        return Err(BenchmarkError::MaximumLiveSizeGate { actual });
    }
    if fixture
        .target_serialized_size
        .is_some_and(|target| target != actual)
    {
        return Err(BenchmarkError::InvalidFixture(
            "production catalog serialization drifted from calibrated size",
        ));
    }
    Ok(())
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
    warmup_serialized_size_bytes: Option<ByteRange>,
    measured_serialized_size_bytes: ByteRange,
    fixture_manifest_sha256: String,
    warmup_commits: usize,
    sample_count: usize,
    final_head_generation: u64,
    final_catalog_count: usize,
    bytes_written: u64,
    total_bytes_written: u64,
    production_serializations_per_commit: u32,
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

fn measure_public_commit<T>(commit: impl FnOnce() -> T) -> (T, Duration) {
    let started = Instant::now();
    let result = commit();
    let elapsed = started.elapsed();
    (result, elapsed)
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
    let root_id = fixture_id(0)?;
    let (entries, prepared_revisions) = prepare_fixture_entries(fixture, &coordinator, &keys)?;
    verify_prepared_fixture_manifest(fixture, &entries, &prepared_revisions)?;
    if derive_lifecycle_counts_from_entries(&entries)?
        != (fixture.live_count, fixture.tombstone_count)
    {
        return Err(BenchmarkError::InvalidFixture(
            "prepared fixture lifecycle counts changed",
        ));
    }

    let validation_catalog = fixture.catalog(&entries, 1, false)?;
    let validation =
        coordinator.initialize_validation_snapshot(&keys, &prepared_revisions, |_| {
            Ok(validation_catalog)
        })?;
    let first_precondition = CatalogPrecondition::folder(validation.catalog(), &root_id)?;
    let first_catalog = fixture.commit_catalog(&entries, 2)?;
    coordinator.commit_first_mutation(
        &keys,
        CUTOVER_EPOCH,
        &[],
        &[first_precondition],
        |_, _| Ok(first_catalog),
        |_| Ok(()),
    )?;

    let mut warmup_sizes = Vec::with_capacity(config.warmup_commits);
    for _ in 0..config.warmup_commits {
        let current = coordinator
            .load_committed(&keys)?
            .ok_or(BenchmarkError::InvalidFixture(
                "initialized benchmark catalog is missing",
            ))?;
        let precondition = CatalogPrecondition::folder(current.catalog(), &root_id)?;
        let generation =
            current
                .head()
                .generation()
                .checked_add(1)
                .ok_or(BenchmarkError::InvalidFixture(
                    "benchmark generation overflow",
                ))?;
        let next_catalog = fixture.commit_catalog(&entries, generation)?;
        let outcome = coordinator.commit(
            &keys,
            &[],
            &[precondition],
            |_, _| Ok(next_catalog),
            |_| Ok(()),
        )?;
        require_measured_catalog_size(fixture, outcome.catalog_plaintext_bytes())?;
        warmup_sizes.push(outcome.catalog_plaintext_bytes());
    }

    let mut commit_samples = Vec::with_capacity(config.measured_commits);
    let mut lock_samples = Vec::with_capacity(config.measured_commits);
    let mut measured_sizes = Vec::with_capacity(config.measured_commits);
    let mut bytes_written = 0_u64;
    let mut total_bytes_written = 0_u64;
    let mut final_head_generation = 0_u64;
    for _ in 0..config.measured_commits {
        let current = coordinator
            .load_committed(&keys)?
            .ok_or(BenchmarkError::InvalidFixture(
                "initialized benchmark catalog is missing",
            ))?;
        let precondition = CatalogPrecondition::folder(current.catalog(), &root_id)?;
        let generation =
            current
                .head()
                .generation()
                .checked_add(1)
                .ok_or(BenchmarkError::InvalidFixture(
                    "benchmark generation overflow",
                ))?;
        let next_catalog = fixture.commit_catalog(&entries, generation)?;
        let (outcome, elapsed) = measure_public_commit(|| {
            coordinator.commit(
                &keys,
                &[],
                &[precondition],
                |_, _| Ok(next_catalog),
                |_| Ok(()),
            )
        });
        let outcome = outcome?;
        commit_samples.push(elapsed);
        require_measured_catalog_size(fixture, outcome.catalog_plaintext_bytes())?;
        lock_samples.push(outcome.lock_hold_duration());
        bytes_written = bytes_written.max(outcome.bytes_written());
        total_bytes_written = total_bytes_written.saturating_add(outcome.bytes_written());
        final_head_generation = outcome.generation();
        measured_sizes.push(outcome.catalog_plaintext_bytes());
    }
    let measured_serialized_size_bytes = ByteRange::from_samples(&measured_sizes).ok_or(
        BenchmarkError::InvalidFixture("measured catalog sizes are missing"),
    )?;
    let final_catalog_count = fs::read_dir(root.join("fs").join("catalogs"))?
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_type()
                .map(|kind| kind.is_file())
                .unwrap_or(false)
        })
        .count();
    if final_catalog_count != usize::try_from(final_head_generation).unwrap_or(usize::MAX) {
        return Err(BenchmarkError::InvalidFixture(
            "final catalog count does not match authoritative HEAD generation",
        ));
    }

    Ok(FixtureBenchmarkReport {
        name: fixture.kind().name(),
        live_count: fixture.live_count(),
        tombstone_count: fixture.tombstone_count(),
        total_count: fixture.total_count(),
        serialized_size_bytes: measured_serialized_size_bytes.max,
        warmup_serialized_size_bytes: ByteRange::from_samples(&warmup_sizes),
        measured_serialized_size_bytes,
        fixture_manifest_sha256: fixture.manifest_fingerprint().to_owned(),
        warmup_commits: config.warmup_commits,
        sample_count: config.measured_commits,
        final_head_generation,
        final_catalog_count,
        bytes_written,
        total_bytes_written,
        production_serializations_per_commit: 1,
        commit_ms: DurationPercentiles::from_samples(&commit_samples),
        lock_hold_ms: DurationPercentiles::from_samples(&lock_samples),
        publication_path: [
            "commit-lock",
            "serialize",
            "encrypt",
            "temporary-file-write",
            "durable-flush",
            "atomic-rename",
            "directory-durability",
            "fs-head-write-flush",
        ],
    })
}

fn duration_ms(value: Duration) -> f64 {
    value.as_secs_f64() * 1_000.0
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ObjectLeaseDiagnosticConfig {
    pub object_count: usize,
    pub warmups: usize,
    pub samples: usize,
    pub mutation_matrix: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ObjectLeaseDiagnosticEvent {
    FenceClean,
    FenceDirtyAll,
    FenceUnknown,
    BetweenFenceMutation,
    MetadataQuery,
}

impl From<crate::transaction::ObjectLeaseDiagnosticBoundaryEvent> for ObjectLeaseDiagnosticEvent {
    fn from(value: crate::transaction::ObjectLeaseDiagnosticBoundaryEvent) -> Self {
        match value {
            crate::transaction::ObjectLeaseDiagnosticBoundaryEvent::FenceClean => Self::FenceClean,
            crate::transaction::ObjectLeaseDiagnosticBoundaryEvent::FenceDirtyAll => {
                Self::FenceDirtyAll
            }
            crate::transaction::ObjectLeaseDiagnosticBoundaryEvent::FenceUnknown => {
                Self::FenceUnknown
            }
            crate::transaction::ObjectLeaseDiagnosticBoundaryEvent::BetweenFenceMutation => {
                Self::BetweenFenceMutation
            }
            crate::transaction::ObjectLeaseDiagnosticBoundaryEvent::MetadataQuery => {
                Self::MetadataQuery
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ObjectLeaseDiagnosticMutationCase {
    BetweenFirstAndFinalFence,
    CreateDelete,
    DeleteRestore,
    RenameRoundTrip,
    InPlaceWriteBlocked,
    ReplaceRestore,
    DirectoryJunction,
    InsideDirectoryHardLink,
    OutsideDirectoryHardLink,
    AmbiguousRenameAway,
    EventFlood,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticPercentiles {
    p50_ms: f64,
    p95_ms: f64,
    p99_ms: f64,
}

impl ObjectLeaseDiagnosticPercentiles {
    #[cfg(windows)]
    fn from_samples(samples: &[Duration]) -> Self {
        Self {
            p50_ms: duration_ms(percentile_nearest_rank(samples, 0.50)),
            p95_ms: duration_ms(percentile_nearest_rank(samples, 0.95)),
            p99_ms: duration_ms(percentile_nearest_rank(samples, 0.99)),
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticLeaseMetrics {
    p50_ms: f64,
    p95_ms: f64,
    p99_ms: f64,
    safe_open_count: usize,
    metadata_query_count: usize,
    fence_count: usize,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ObjectLeaseDiagnosticResourceSnapshot {
    entry_permits: usize,
    lease_permits: usize,
    monitor_resources: usize,
}

impl ObjectLeaseDiagnosticResourceSnapshot {
    pub const fn entry_permits(&self) -> usize {
        self.entry_permits
    }

    pub const fn lease_permits(&self) -> usize {
        self.lease_permits
    }

    pub const fn monitor_resources(&self) -> usize {
        self.monitor_resources
    }
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticResources {
    live_entry_permits: usize,
    live_lease_permits: usize,
    live_monitor_resources: usize,
    post_teardown_entry_permits: usize,
    post_teardown_lease_permits: usize,
    post_teardown_monitor_resources: usize,
    descriptor_delta: i64,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticTeardown {
    target_ms: Option<u64>,
    elapsed_ms: Option<f64>,
    completion_confirmed: bool,
    target_met: Option<bool>,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticCorrectness {
    ordered_boundary_proven: bool,
    mutation_matrix_passed: bool,
    teardown_passed: bool,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
enum ObjectLeaseDiagnosticPlatform {
    #[cfg(windows)]
    Windows,
    #[cfg(target_os = "macos")]
    Macos,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticHardware {
    architecture: &'static str,
    logical_processors: usize,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticOs {
    family: &'static str,
    version: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticFilesystem {
    name: String,
    target: PathBuf,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticSource {
    repository_root: PathBuf,
    commit: String,
    clean: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticExecutable {
    canonical_path: PathBuf,
    sha256: String,
    volume_serial: u64,
    file_id: u64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticCargoLock {
    canonical_path: PathBuf,
    working_sha256: String,
    committed_sha256: String,
    matches_commit: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticFileIdentity {
    canonical_path: PathBuf,
    volume_serial: u64,
    file_id: u64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticNativeTestContract {
    source_commit: String,
    required_tests: Vec<&'static str>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObjectLeaseDiagnosticBuild {
    source: ObjectLeaseDiagnosticSource,
    executable: ObjectLeaseDiagnosticExecutable,
    cargo_lock: ObjectLeaseDiagnosticCargoLock,
    target: ObjectLeaseDiagnosticFileIdentity,
    output: Option<ObjectLeaseDiagnosticFileIdentity>,
    argv: Vec<String>,
    native_test_contract: ObjectLeaseDiagnosticNativeTestContract,
    crate_version: &'static str,
    debug_assertions: bool,
    architecture: &'static str,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ObjectLeaseDiagnosticReport {
    schema_version: u32,
    platform: ObjectLeaseDiagnosticPlatform,
    hardware: ObjectLeaseDiagnosticHardware,
    os: ObjectLeaseDiagnosticOs,
    filesystem: ObjectLeaseDiagnosticFilesystem,
    build: ObjectLeaseDiagnosticBuild,
    object_count: usize,
    warmups: usize,
    samples: usize,
    safe_open: ObjectLeaseDiagnosticPercentiles,
    lease: ObjectLeaseDiagnosticLeaseMetrics,
    resources: ObjectLeaseDiagnosticResources,
    teardown: ObjectLeaseDiagnosticTeardown,
    correctness: ObjectLeaseDiagnosticCorrectness,
    residue_count: usize,
}

impl ObjectLeaseDiagnosticReport {
    pub fn native_acceptance_passed(&self) -> bool {
        self.correctness.ordered_boundary_proven
            && self.correctness.mutation_matrix_passed
            && self.correctness.teardown_passed
            && self.provenance_matches_report()
    }

    fn provenance_matches_report(&self) -> bool {
        let Some(output) = &self.build.output else {
            return false;
        };
        let source = &self.build.source;
        let executable = &self.build.executable;
        let cargo_lock = &self.build.cargo_lock;
        let target = &self.build.target;
        let argv = &self.build.argv;
        source.clean
            && is_hex_with_len(&source.commit, 40)
            && source.commit == self.build.native_test_contract.source_commit
            && is_hex_with_len(&executable.sha256, 64)
            && is_hex_with_len(&cargo_lock.working_sha256, 64)
            && is_hex_with_len(&cargo_lock.committed_sha256, 64)
            && cargo_lock.matches_commit
            && cargo_lock.canonical_path == source.repository_root.join("Cargo.lock")
            && self.filesystem.target == target.canonical_path
            && argv.len() == 12
            && canonical_argv_path_matches(&argv[0], &executable.canonical_path)
            && argv[1] == "--target"
            && canonical_argv_path_matches(&argv[2], &target.canonical_path)
            && argv[3] == "--objects"
            && argv[4] == self.object_count.to_string()
            && argv[5] == "--warmups"
            && argv[6] == self.warmups.to_string()
            && argv[7] == "--samples"
            && argv[8] == self.samples.to_string()
            && argv[9] == "--mutation-matrix"
            && argv[10] == "--output"
            && canonical_argv_path_matches(&argv[11], &output.canonical_path)
    }

    pub fn bind_cli_invocation(
        &mut self,
        output: &Path,
        argv: Vec<String>,
    ) -> Result<(), BenchmarkError> {
        let canonical = fs::canonicalize(output)?;
        #[cfg(windows)]
        {
            let (volume_serial, file_id) = windows_file_identity(&canonical)?;
            self.build.output = Some(ObjectLeaseDiagnosticFileIdentity {
                canonical_path: canonical,
                volume_serial: u64::from(volume_serial),
                file_id,
            });
        }
        #[cfg(not(windows))]
        {
            self.build.output = Some(ObjectLeaseDiagnosticFileIdentity {
                canonical_path: canonical,
                volume_serial: 0,
                file_id: 0,
            });
        }
        self.build.argv = argv;
        Ok(())
    }
}

fn is_hex_with_len(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn canonical_argv_path_matches(argument: &str, expected: &Path) -> bool {
    fs::canonicalize(argument)
        .map(|canonical| canonical == expected)
        .unwrap_or(false)
}

#[derive(Clone, Debug)]
pub struct ObjectLeaseDiagnosticObservations {
    clean_safe_open_count: usize,
    clean_metadata_query_count: usize,
    clean_fence_count: usize,
    dirty_all_safe_open_count: usize,
    unknown_safe_open_count: usize,
    clean_boundary_samples: Vec<Vec<ObjectLeaseDiagnosticEvent>>,
    between_fence_mutation_boundary: Vec<ObjectLeaseDiagnosticEvent>,
    mutation_cases: Vec<ObjectLeaseDiagnosticMutationCase>,
    live_resource_samples: Vec<ObjectLeaseDiagnosticResourceSnapshot>,
    post_teardown_resources: ObjectLeaseDiagnosticResourceSnapshot,
}

impl ObjectLeaseDiagnosticObservations {
    pub const fn clean_safe_open_count(&self) -> usize {
        self.clean_safe_open_count
    }

    pub const fn clean_metadata_query_count(&self) -> usize {
        self.clean_metadata_query_count
    }

    pub const fn clean_fence_count(&self) -> usize {
        self.clean_fence_count
    }

    pub const fn dirty_all_safe_open_count(&self) -> usize {
        self.dirty_all_safe_open_count
    }

    pub const fn unknown_safe_open_count(&self) -> usize {
        self.unknown_safe_open_count
    }

    pub fn clean_boundary_samples(&self) -> &[Vec<ObjectLeaseDiagnosticEvent>] {
        &self.clean_boundary_samples
    }

    pub fn between_fence_mutation_boundary(&self) -> &[ObjectLeaseDiagnosticEvent] {
        &self.between_fence_mutation_boundary
    }

    pub fn mutation_cases(&self) -> &[ObjectLeaseDiagnosticMutationCase] {
        &self.mutation_cases
    }

    pub fn live_resource_samples(&self) -> &[ObjectLeaseDiagnosticResourceSnapshot] {
        &self.live_resource_samples
    }

    pub const fn post_teardown_resources(&self) -> &ObjectLeaseDiagnosticResourceSnapshot {
        &self.post_teardown_resources
    }
}

#[derive(Clone, Debug)]
pub struct ObjectLeaseDiagnosticOutcome {
    report: ObjectLeaseDiagnosticReport,
    observations: ObjectLeaseDiagnosticObservations,
}

impl ObjectLeaseDiagnosticOutcome {
    pub const fn report(&self) -> &ObjectLeaseDiagnosticReport {
        &self.report
    }

    pub const fn observations(&self) -> &ObjectLeaseDiagnosticObservations {
        &self.observations
    }

    pub fn report_mut(&mut self) -> &mut ObjectLeaseDiagnosticReport {
        &mut self.report
    }

    pub fn into_report(self) -> ObjectLeaseDiagnosticReport {
        self.report
    }
}

pub fn run_object_lease_diagnostic(
    target: &Path,
    config: ObjectLeaseDiagnosticConfig,
) -> Result<ObjectLeaseDiagnosticOutcome, BenchmarkError> {
    if config.object_count == 0 || config.object_count > 4_096 {
        return Err(BenchmarkError::DiagnosticInvariant(
            "object count must be between 1 and 4096",
        ));
    }
    if config.samples == 0 {
        return Err(BenchmarkError::DiagnosticInvariant(
            "at least one measured sample is required",
        ));
    }
    #[cfg(target_os = "macos")]
    {
        let _ = (target, config);
        return Err(BenchmarkError::BackendUnavailable(
            "the approved macOS native backend is not enabled in this build",
        ));
    }
    #[cfg(all(not(windows), not(target_os = "macos")))]
    {
        let _ = (target, config);
        return Err(BenchmarkError::BackendUnavailable(
            "this platform has no production object-validation lease backend",
        ));
    }
    #[cfg(windows)]
    run_windows_object_lease_diagnostic(target, config)
}

#[cfg(windows)]
fn run_windows_object_lease_diagnostic(
    target: &Path,
    config: ObjectLeaseDiagnosticConfig,
) -> Result<ObjectLeaseDiagnosticOutcome, BenchmarkError> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::create_dir(target)?;
    let canonical_target = fs::canonicalize(target)?;
    let filesystem_name = windows_filesystem_name(&canonical_target)?;
    if !filesystem_name.eq_ignore_ascii_case("NTFS") {
        return Err(BenchmarkError::BackendUnavailable(
            "the Windows production lease diagnostic requires NTFS",
        ));
    }
    let fixture = build_fixture(&CatalogFixtureSpec::new(
        FixtureKind::TestOnly,
        2,
        config.object_count,
        None,
    ))?;
    let coordinator = CoreCommitCoordinator::new_with_object_lease_diagnostics(target, CORE_ID)?;
    let frk = SecretBytes::new(vec![0x42; 32])?;
    let wrapped = wrap_filesystem_root_key(CREDENTIAL, &frk, CREDENTIAL_AAD)?;
    let unlocked = unwrap_filesystem_root_key(CREDENTIAL, &wrapped, CREDENTIAL_AAD)?;
    let keys = derive_corefs_subkeys(&unlocked, 1)?;
    let root_id = fixture_id(0)?;
    let (entries, prepared_revisions) = prepare_fixture_entries(&fixture, &coordinator, &keys)?;
    verify_prepared_fixture_manifest(&fixture, &entries, &prepared_revisions)?;
    let validation_catalog = fixture.catalog(&entries, 1, false)?;
    let validation =
        coordinator.initialize_validation_snapshot(&keys, &prepared_revisions, |_| {
            Ok(validation_catalog)
        })?;
    let os_version = windows_version();
    let baseline_descriptors = process_descriptor_count()?;
    let first_precondition = CatalogPrecondition::folder(validation.catalog(), &root_id)?;
    let first_catalog = fixture.commit_catalog(&entries, 2)?;
    coordinator.commit_first_mutation(
        &keys,
        CUTOVER_EPOCH,
        &[],
        &[first_precondition],
        |_, _| Ok(first_catalog),
        |_| Ok(()),
    )?;
    require_diagnostic_resources(&coordinator, config.object_count)?;

    for _ in 0..config.warmups {
        diagnostic_commit(&coordinator, &keys, &fixture, &entries, &root_id)?;
    }

    let mut safe_open_samples = Vec::with_capacity(config.samples);
    for _ in 0..config.samples {
        ensure_diagnostic_lease(
            &coordinator,
            &keys,
            &fixture,
            &entries,
            &root_id,
            config.object_count,
        )?;
        if !coordinator.publish_object_lease_unknown_for_diagnostic() {
            return Err(BenchmarkError::DiagnosticInvariant(
                "safe-open sample did not have a production lease",
            ));
        }
        coordinator.reset_object_lease_diagnostic_counters();
        safe_open_samples.push(diagnostic_commit(
            &coordinator,
            &keys,
            &fixture,
            &entries,
            &root_id,
        )?);
        let counters = diagnostic_counters(&coordinator)?;
        if counters.safe_opens != config.object_count {
            return Err(BenchmarkError::DiagnosticInvariant(
                "safe-open sample did not open every unchanged object",
            ));
        }
    }

    ensure_diagnostic_lease(
        &coordinator,
        &keys,
        &fixture,
        &entries,
        &root_id,
        config.object_count,
    )?;
    let mut lease_samples = Vec::with_capacity(config.samples);
    let mut live_resource_samples = Vec::with_capacity(config.samples);
    let mut clean_boundary_samples = Vec::with_capacity(config.samples);
    let mut ordered_boundary_proven = true;
    let mut clean = crate::transaction::ObjectLeaseDiagnosticCounterSnapshot::default();
    for _ in 0..config.samples {
        coordinator.reset_object_lease_diagnostic_counters();
        lease_samples.push(diagnostic_commit(
            &coordinator,
            &keys,
            &fixture,
            &entries,
            &root_id,
        )?);
        clean = diagnostic_counters(&coordinator)?;
        let boundary = diagnostic_boundary_events(&coordinator)?;
        ordered_boundary_proven &= clean.safe_opens == 0
            && clean.metadata_queries == config.object_count
            && clean.fences == 2
            && diagnostic_boundary_is_ordered(
                &boundary,
                config.object_count,
                ObjectLeaseDiagnosticEvent::FenceClean,
            );
        clean_boundary_samples.push(boundary);
        let live = diagnostic_resource_snapshot(&coordinator);
        ordered_boundary_proven &= live
            == (ObjectLeaseDiagnosticResourceSnapshot {
                entry_permits: config.object_count,
                lease_permits: 1,
                monitor_resources: 3,
            });
        live_resource_samples.push(live);
    }

    let objects_path = target.join("objects");
    let dirty_sentinel = objects_path.join("lease-diagnostic-dirty.tmp");
    fs::write(&dirty_sentinel, b"dirty-all")?;
    fs::remove_file(&dirty_sentinel)?;
    coordinator.reset_object_lease_diagnostic_counters();
    diagnostic_commit(&coordinator, &keys, &fixture, &entries, &root_id)?;
    let dirty_all = diagnostic_counters(&coordinator)?;
    if dirty_all.safe_opens != config.object_count {
        return Err(BenchmarkError::DiagnosticInvariant(
            "DirtyAll did not force a full safe-open scan",
        ));
    }

    ensure_diagnostic_lease(
        &coordinator,
        &keys,
        &fixture,
        &entries,
        &root_id,
        config.object_count,
    )?;
    if !coordinator.publish_object_lease_unknown_for_diagnostic() {
        return Err(BenchmarkError::DiagnosticInvariant(
            "Unknown probe did not have a production lease",
        ));
    }
    coordinator.reset_object_lease_diagnostic_counters();
    diagnostic_commit(&coordinator, &keys, &fixture, &entries, &root_id)?;
    let unknown = diagnostic_counters(&coordinator)?;
    if unknown.safe_opens != config.object_count {
        return Err(BenchmarkError::DiagnosticInvariant(
            "Unknown did not force a full safe-open scan",
        ));
    }

    let (between_fence_mutation_boundary, mutation_cases, mutation_matrix_passed) = if config
        .mutation_matrix
    {
        let (between_fence_passed, between_fence_boundary) = run_between_fence_mutation_diagnostic(
            &coordinator,
            &keys,
            &fixture,
            &entries,
            &root_id,
            &objects_path,
            config.object_count,
        )?;
        ordered_boundary_proven &= between_fence_passed
            && diagnostic_mutation_boundary_is_ordered(&between_fence_boundary);
        let mut mutation_cases = run_diagnostic_mutation_matrix(
            &coordinator,
            &keys,
            &fixture,
            &entries,
            &root_id,
            &objects_path,
            prepared_revisions
                .first()
                .ok_or(BenchmarkError::DiagnosticInvariant(
                    "mutation matrix requires at least one prepared object",
                ))?
                .physical_name()
                .as_str(),
            config.object_count,
        )?;
        if between_fence_passed {
            mutation_cases.insert(
                0,
                ObjectLeaseDiagnosticMutationCase::BetweenFirstAndFinalFence,
            );
        }
        let passed = mutation_cases.len() == 11;
        (between_fence_boundary, mutation_cases, passed)
    } else {
        (Vec::new(), Vec::new(), false)
    };

    let live_resources =
        live_resource_samples
            .first()
            .copied()
            .ok_or(BenchmarkError::DiagnosticInvariant(
                "live resource observations are missing",
            ))?;
    let (target_volume_serial, target_file_id) =
        coordinator.object_lease_diagnostic_target_identity()?;
    coordinator.release_object_lease()?;
    let post_teardown_resources = diagnostic_resource_snapshot(&coordinator);
    let teardown = coordinator.object_lease_teardown_observation().ok_or(
        BenchmarkError::DiagnosticInvariant("native teardown observation is missing"),
    )?;
    let descriptor_delta = i64::from(process_descriptor_count()?) - i64::from(baseline_descriptors);
    drop(coordinator);
    let residue_count = diagnostic_residue_count(&objects_path)?;
    let teardown_passed = teardown.completion_confirmed
        && teardown.target_met
        && post_teardown_resources == ObjectLeaseDiagnosticResourceSnapshot::default()
        && descriptor_delta == 0
        && residue_count == 0;

    let lease_percentiles = ObjectLeaseDiagnosticPercentiles::from_samples(&lease_samples);
    let observations = ObjectLeaseDiagnosticObservations {
        clean_safe_open_count: clean.safe_opens,
        clean_metadata_query_count: clean.metadata_queries,
        clean_fence_count: clean.fences,
        dirty_all_safe_open_count: dirty_all.safe_opens,
        unknown_safe_open_count: unknown.safe_opens,
        clean_boundary_samples,
        between_fence_mutation_boundary,
        mutation_cases,
        live_resource_samples,
        post_teardown_resources,
    };
    let report = ObjectLeaseDiagnosticReport {
        schema_version: 1,
        platform: ObjectLeaseDiagnosticPlatform::Windows,
        hardware: ObjectLeaseDiagnosticHardware {
            architecture: std::env::consts::ARCH,
            logical_processors: std::thread::available_parallelism()
                .map(usize::from)
                .unwrap_or(1),
        },
        os: ObjectLeaseDiagnosticOs {
            family: std::env::consts::OS,
            version: os_version,
        },
        filesystem: ObjectLeaseDiagnosticFilesystem {
            name: filesystem_name,
            target: canonical_target.clone(),
        },
        build: diagnostic_build_facts(&canonical_target, target_volume_serial, target_file_id)?,
        object_count: config.object_count,
        warmups: config.warmups,
        samples: config.samples,
        safe_open: ObjectLeaseDiagnosticPercentiles::from_samples(&safe_open_samples),
        lease: ObjectLeaseDiagnosticLeaseMetrics {
            p50_ms: lease_percentiles.p50_ms,
            p95_ms: lease_percentiles.p95_ms,
            p99_ms: lease_percentiles.p99_ms,
            safe_open_count: clean.safe_opens,
            metadata_query_count: clean.metadata_queries,
            fence_count: clean.fences,
        },
        resources: ObjectLeaseDiagnosticResources {
            live_entry_permits: live_resources.entry_permits,
            live_lease_permits: live_resources.lease_permits,
            live_monitor_resources: live_resources.monitor_resources,
            post_teardown_entry_permits: post_teardown_resources.entry_permits,
            post_teardown_lease_permits: post_teardown_resources.lease_permits,
            post_teardown_monitor_resources: post_teardown_resources.monitor_resources,
            descriptor_delta,
        },
        teardown: ObjectLeaseDiagnosticTeardown {
            target_ms: Some(2_000),
            elapsed_ms: Some(duration_ms(teardown.elapsed)),
            completion_confirmed: teardown.completion_confirmed,
            target_met: Some(teardown.target_met),
        },
        correctness: ObjectLeaseDiagnosticCorrectness {
            ordered_boundary_proven,
            mutation_matrix_passed,
            teardown_passed,
        },
        residue_count,
    };
    Ok(ObjectLeaseDiagnosticOutcome {
        report,
        observations,
    })
}

#[cfg(windows)]
fn diagnostic_commit(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    fixture: &CatalogBenchmarkFixture,
    entries: &[CatalogGenerationEntry],
    root_id: &OpaqueId,
) -> Result<Duration, BenchmarkError> {
    let current = coordinator
        .load_committed(keys)?
        .ok_or(BenchmarkError::DiagnosticInvariant(
            "diagnostic catalog is missing",
        ))?;
    let precondition = CatalogPrecondition::folder(current.catalog(), root_id)?;
    let generation =
        current
            .head()
            .generation()
            .checked_add(1)
            .ok_or(BenchmarkError::DiagnosticInvariant(
                "diagnostic generation overflow",
            ))?;
    let next_catalog = fixture.commit_catalog(entries, generation)?;
    let (outcome, elapsed) = measure_public_commit(|| {
        coordinator.commit(
            keys,
            &[],
            &[precondition],
            |_, _| Ok(next_catalog),
            |_| Ok(()),
        )
    });
    outcome?;
    Ok(elapsed)
}

#[cfg(windows)]
fn diagnostic_counters(
    coordinator: &CoreCommitCoordinator,
) -> Result<crate::transaction::ObjectLeaseDiagnosticCounterSnapshot, BenchmarkError> {
    coordinator
        .object_lease_diagnostic_counters()
        .ok_or(BenchmarkError::DiagnosticInvariant(
            "diagnostic counters are not installed",
        ))
}

#[cfg(windows)]
fn diagnostic_boundary_events(
    coordinator: &CoreCommitCoordinator,
) -> Result<Vec<ObjectLeaseDiagnosticEvent>, BenchmarkError> {
    coordinator
        .object_lease_diagnostic_boundary_events()
        .ok_or(BenchmarkError::DiagnosticInvariant(
            "diagnostic boundary observations are not installed",
        ))
        .map(|events| events.into_iter().map(Into::into).collect())
}

#[cfg(windows)]
fn diagnostic_boundary_is_ordered(
    events: &[ObjectLeaseDiagnosticEvent],
    object_count: usize,
    final_fence: ObjectLeaseDiagnosticEvent,
) -> bool {
    events.len() == object_count.saturating_add(2)
        && events.first() == Some(&ObjectLeaseDiagnosticEvent::FenceClean)
        && events.last() == Some(&final_fence)
        && events[1..events.len().saturating_sub(1)]
            .iter()
            .all(|event| *event == ObjectLeaseDiagnosticEvent::MetadataQuery)
}

#[cfg(windows)]
fn diagnostic_mutation_boundary_is_ordered(events: &[ObjectLeaseDiagnosticEvent]) -> bool {
    events.len() == 3
        && events.first() == Some(&ObjectLeaseDiagnosticEvent::FenceClean)
        && events.get(1) == Some(&ObjectLeaseDiagnosticEvent::BetweenFenceMutation)
        && events.last() == Some(&ObjectLeaseDiagnosticEvent::FenceDirtyAll)
}

#[cfg(windows)]
fn diagnostic_resource_snapshot(
    coordinator: &CoreCommitCoordinator,
) -> ObjectLeaseDiagnosticResourceSnapshot {
    let (entry_permits, lease_permits, monitor_resources) =
        coordinator.object_lease_diagnostic_resources();
    ObjectLeaseDiagnosticResourceSnapshot {
        entry_permits,
        lease_permits,
        monitor_resources,
    }
}

#[cfg(windows)]
fn require_diagnostic_resources(
    coordinator: &CoreCommitCoordinator,
    object_count: usize,
) -> Result<(), BenchmarkError> {
    let resources = diagnostic_resource_snapshot(coordinator);
    if resources
        != (ObjectLeaseDiagnosticResourceSnapshot {
            entry_permits: object_count,
            lease_permits: 1,
            monitor_resources: 3,
        })
    {
        return Err(BenchmarkError::DiagnosticInvariant(
            "production lease did not retain its exact resource budget",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn ensure_diagnostic_lease(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    fixture: &CatalogBenchmarkFixture,
    entries: &[CatalogGenerationEntry],
    root_id: &OpaqueId,
    object_count: usize,
) -> Result<(), BenchmarkError> {
    if coordinator.object_lease_diagnostic_resources().1 == 0 {
        diagnostic_commit(coordinator, keys, fixture, entries, root_id)?;
    }
    require_diagnostic_resources(coordinator, object_count)
}

#[cfg(windows)]
#[allow(clippy::too_many_arguments)]
fn run_between_fence_mutation_diagnostic(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    fixture: &CatalogBenchmarkFixture,
    entries: &[CatalogGenerationEntry],
    root_id: &OpaqueId,
    objects_path: &Path,
    object_count: usize,
) -> Result<(bool, Vec<ObjectLeaseDiagnosticEvent>), BenchmarkError> {
    ensure_diagnostic_lease(coordinator, keys, fixture, entries, root_id, object_count)?;
    let sentinel = objects_path.join("lease-diagnostic-between-fences.tmp");
    coordinator.reset_object_lease_diagnostic_counters();
    let mutation_rejected = coordinator.prove_between_fence_mutation_for_diagnostic(&sentinel);
    let boundary = diagnostic_boundary_events(coordinator)?;
    let ordered = diagnostic_mutation_boundary_is_ordered(&boundary);
    coordinator.reset_object_lease_diagnostic_counters();
    diagnostic_commit(coordinator, keys, fixture, entries, root_id)?;
    let recovery = diagnostic_counters(coordinator)?.safe_opens == object_count;
    ensure_diagnostic_lease(coordinator, keys, fixture, entries, root_id, object_count)?;
    Ok((
        mutation_rejected && ordered && recovery && !sentinel.exists(),
        boundary,
    ))
}

#[cfg(windows)]
#[allow(clippy::too_many_arguments)]
fn run_diagnostic_mutation_matrix(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    fixture: &CatalogBenchmarkFixture,
    entries: &[CatalogGenerationEntry],
    root_id: &OpaqueId,
    objects_path: &Path,
    object_physical_name: &str,
    object_count: usize,
) -> Result<Vec<ObjectLeaseDiagnosticMutationCase>, BenchmarkError> {
    let mut passed_cases = Vec::with_capacity(10);
    let first = objects_path.join("lease-diagnostic-mutation-a.tmp");
    let second = objects_path.join("lease-diagnostic-mutation-b.tmp");
    let object = objects_path.join(object_physical_name);
    let original = fs::read(&object)?;
    let prove_full_fallback = || -> Result<bool, BenchmarkError> {
        coordinator.reset_object_lease_diagnostic_counters();
        diagnostic_commit(coordinator, keys, fixture, entries, root_id)?;
        let safe_opens = diagnostic_counters(coordinator)?.safe_opens;
        Ok(safe_opens == object_count)
    };

    fs::write(&first, b"create")?;
    fs::remove_file(&first)?;
    if !prove_full_fallback()? {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::CreateDelete);

    fs::remove_file(&object)?;
    fs::write(&object, &original)?;
    if !prove_full_fallback()? {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::DeleteRestore);

    fs::rename(&object, &first)?;
    fs::rename(&first, &object)?;
    if !prove_full_fallback()? {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::RenameRoundTrip);

    ensure_diagnostic_lease(coordinator, keys, fixture, entries, root_id, object_count)?;
    let in_place_write = fs::write(&object, []);
    let in_place_write_blocked = in_place_write.is_err() && fs::read(&object)? == original;
    if in_place_write.is_ok() {
        fs::write(&object, &original)?;
        let _ = prove_full_fallback()?;
        return Ok(passed_cases);
    }
    coordinator.reset_object_lease_diagnostic_counters();
    diagnostic_commit(coordinator, keys, fixture, entries, root_id)?;
    if !in_place_write_blocked || diagnostic_counters(coordinator)?.safe_opens != 0 {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::InPlaceWriteBlocked);

    fs::rename(&object, &first)?;
    fs::write(&object, &original)?;
    fs::remove_file(&first)?;
    if !prove_full_fallback()? {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::ReplaceRestore);

    let junction_target = objects_path.with_extension("lease-diagnostic-junction-target");
    let junction = objects_path.join("lease-diagnostic-junction");
    let _ = fs::remove_dir_all(&junction_target);
    fs::create_dir(&junction_target)?;
    let junction_result = Command::new("cmd.exe")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(&junction)
        .arg(&junction_target)
        .output()?;
    if !junction_result.status.success() {
        let _ = fs::remove_dir_all(&junction_target);
        return Ok(passed_cases);
    }
    fs::remove_dir(&junction)?;
    fs::remove_dir_all(&junction_target)?;
    if !prove_full_fallback()? {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::DirectoryJunction);

    let inside_link = objects_path.join("lease-diagnostic-inside-hard-link.tmp");
    fs::hard_link(&object, &inside_link)?;
    coordinator.reset_object_lease_diagnostic_counters();
    let inside_hard_link_rejected =
        diagnostic_commit(coordinator, keys, fixture, entries, root_id).is_err();
    fs::remove_file(&inside_link)?;
    if !inside_hard_link_rejected {
        return Ok(passed_cases);
    }
    coordinator.reset_object_lease_diagnostic_counters();
    diagnostic_commit(coordinator, keys, fixture, entries, root_id)?;
    if diagnostic_counters(coordinator)?.safe_opens < object_count {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::InsideDirectoryHardLink);

    let outside = objects_path.with_extension("lease-diagnostic-outside");
    let outside_link = outside.join("outside-link");
    let _ = fs::remove_dir_all(&outside);
    fs::create_dir(&outside)?;
    fs::hard_link(&object, &outside_link)?;
    coordinator.reset_object_lease_diagnostic_counters();
    let hard_link_rejected =
        diagnostic_commit(coordinator, keys, fixture, entries, root_id).is_err();
    fs::remove_file(&outside_link)?;
    fs::remove_dir(&outside)?;
    if !hard_link_rejected {
        return Ok(passed_cases);
    }
    coordinator.reset_object_lease_diagnostic_counters();
    diagnostic_commit(coordinator, keys, fixture, entries, root_id)?;
    let hard_link_recovery_safe_opens = diagnostic_counters(coordinator)?.safe_opens;
    if hard_link_recovery_safe_opens < object_count {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::OutsideDirectoryHardLink);

    let ambiguous = objects_path.with_extension("lease-diagnostic-ambiguous-object");
    let _ = fs::remove_file(&ambiguous);
    fs::rename(&object, &ambiguous)?;
    coordinator.reset_object_lease_diagnostic_counters();
    let ambiguity_rejected =
        diagnostic_commit(coordinator, keys, fixture, entries, root_id).is_err();
    fs::rename(&ambiguous, &object)?;
    if !ambiguity_rejected {
        return Ok(passed_cases);
    }
    coordinator.reset_object_lease_diagnostic_counters();
    diagnostic_commit(coordinator, keys, fixture, entries, root_id)?;
    let ambiguity_recovery_safe_opens = diagnostic_counters(coordinator)?.safe_opens;
    if ambiguity_recovery_safe_opens < object_count {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::AmbiguousRenameAway);

    for index in 0..4_096 {
        let flood = objects_path.join(format!("lease-diagnostic-flood-{index:04}.tmp"));
        fs::write(&flood, b"flood")?;
        fs::remove_file(flood)?;
    }
    if !prove_full_fallback()? {
        return Ok(passed_cases);
    }
    passed_cases.push(ObjectLeaseDiagnosticMutationCase::EventFlood);
    debug_assert!(!first.exists() && !second.exists());
    Ok(passed_cases)
}

#[cfg(windows)]
fn diagnostic_residue_count(objects_path: &Path) -> Result<usize, BenchmarkError> {
    Ok(fs::read_dir(objects_path)?
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_name()
                .to_str()
                .map_or(true, |name| !name.ends_with(".acore"))
        })
        .count())
}

#[cfg(windows)]
fn process_descriptor_count() -> Result<u32, BenchmarkError> {
    let mut count = 0_u32;
    // SAFETY: the current-process pseudo-handle is always valid and `count` is writable.
    let result = unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) };
    if result == 0 {
        Err(std::io::Error::last_os_error().into())
    } else {
        Ok(count)
    }
}

#[cfg(windows)]
fn windows_filesystem_name(target: &Path) -> Result<String, BenchmarkError> {
    let target_wide: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut volume_path = vec![0_u16; 32_768];
    // SAFETY: both strings are NUL-terminated/writable for the documented buffer lengths.
    let path_result = unsafe {
        GetVolumePathNameW(
            target_wide.as_ptr(),
            volume_path.as_mut_ptr(),
            u32::try_from(volume_path.len()).unwrap_or(u32::MAX),
        )
    };
    if path_result == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let mut filesystem_name = vec![0_u16; 256];
    // SAFETY: the volume path is initialized by `GetVolumePathNameW`; unused optional
    // outputs are null and the filesystem-name output is a writable buffer.
    let information_result = unsafe {
        GetVolumeInformationW(
            volume_path.as_ptr(),
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            filesystem_name.as_mut_ptr(),
            u32::try_from(filesystem_name.len()).unwrap_or(u32::MAX),
        )
    };
    if information_result == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let length = filesystem_name
        .iter()
        .position(|value| *value == 0)
        .unwrap_or(filesystem_name.len());
    Ok(String::from_utf16_lossy(&filesystem_name[..length]))
}

#[cfg(windows)]
fn diagnostic_build_facts(
    canonical_target: &Path,
    target_volume_serial: u64,
    target_file_id: u64,
) -> Result<ObjectLeaseDiagnosticBuild, BenchmarkError> {
    let repository_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .ok_or(BenchmarkError::DiagnosticInvariant(
            "could not locate the diagnostic source repository",
        ))?;
    let repository_root = fs::canonicalize(repository_root)?;
    let source_commit = git_output(&repository_root, &["rev-parse", "HEAD"])?;
    let source_clean = git_worktree_is_clean(&repository_root)?;
    let cargo_lock = fs::canonicalize(repository_root.join("Cargo.lock"))?;
    let working_lock_hash = sha256_file(&cargo_lock)?;
    let lock_matches_commit = Command::new("git")
        .arg("-C")
        .arg(&repository_root)
        .args(["diff", "--quiet", "HEAD", "--", "Cargo.lock"])
        .output()?
        .status
        .success();
    let committed_lock = Command::new("git")
        .arg("-C")
        .arg(&repository_root)
        .args(["show", "HEAD:Cargo.lock"])
        .output()?;
    if !committed_lock.status.success() {
        return Err(BenchmarkError::DiagnosticInvariant(
            "could not read committed Cargo.lock",
        ));
    }
    let committed_lock_hash = hex_sha256(&committed_lock.stdout);
    let executable = fs::canonicalize(std::env::current_exe()?)?;
    let executable_hash = sha256_file(&executable)?;
    let (executable_volume_serial, executable_file_id) = windows_file_identity(&executable)?;
    Ok(ObjectLeaseDiagnosticBuild {
        source: ObjectLeaseDiagnosticSource {
            repository_root,
            commit: source_commit.clone(),
            clean: source_clean,
        },
        executable: ObjectLeaseDiagnosticExecutable {
            canonical_path: executable,
            sha256: executable_hash,
            volume_serial: u64::from(executable_volume_serial),
            file_id: executable_file_id,
        },
        cargo_lock: ObjectLeaseDiagnosticCargoLock {
            canonical_path: cargo_lock,
            working_sha256: working_lock_hash,
            committed_sha256: committed_lock_hash,
            matches_commit: lock_matches_commit,
        },
        target: ObjectLeaseDiagnosticFileIdentity {
            canonical_path: canonical_target.to_path_buf(),
            volume_serial: target_volume_serial,
            file_id: target_file_id,
        },
        output: None,
        argv: Vec::new(),
        native_test_contract: ObjectLeaseDiagnosticNativeTestContract {
            source_commit,
            required_tests: vec![
                "windows_event_flood_is_constant_space_and_terminal",
                "windows_resource_plan_matches_three_live_monitor_resources",
                "windows_teardown_target_miss_retains_ownership_until_completion",
                "windows_object_lease_ambiguity_and_failure_are_unknown",
                "windows_object_lease_real_directory_junction_activity_is_dirty_all",
                "windows_object_lease_real_create_delete_rename_and_replace_are_dirty",
                "windows_object_lease_blocks_in_place_truncate_and_stays_clean",
                "windows_validation_open_rejects_existing_writer",
                "windows_retained_validation_anchor_rejects_later_writer",
                "windows_object_lease_retained_anchor_rejects_handle_loss_and_outside_hard_link",
                "cache_hit_rejects_unexpected_hard_link_production_link_count",
            ],
        },
        crate_version: env!("CARGO_PKG_VERSION"),
        debug_assertions: cfg!(debug_assertions),
        architecture: std::env::consts::ARCH,
    })
}

#[cfg(windows)]
fn git_output(repository_root: &Path, arguments: &[&str]) -> Result<String, BenchmarkError> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repository_root)
        .args(arguments)
        .output()?;
    if !output.status.success() {
        return Err(BenchmarkError::DiagnosticInvariant(
            "could not capture diagnostic source provenance",
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

#[cfg(windows)]
fn git_worktree_is_clean(repository_root: &Path) -> Result<bool, BenchmarkError> {
    Ok(git_output(
        repository_root,
        &["status", "--porcelain", "--untracked-files=all"],
    )?
    .is_empty())
}

#[cfg(windows)]
fn sha256_file(path: &Path) -> Result<String, BenchmarkError> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(hex_sha256_digest(hasher.finalize().into()))
}

#[cfg(windows)]
fn windows_file_identity(path: &Path) -> Result<(u32, u64), BenchmarkError> {
    let file = File::open(path)?;
    let mut information = MaybeUninit::<BY_HANDLE_FILE_INFORMATION>::zeroed();
    // SAFETY: `file` owns a live file handle and `information` is a correctly
    // sized writable output for the duration of the call.
    let result =
        unsafe { GetFileInformationByHandle(file.as_raw_handle() as _, information.as_mut_ptr()) };
    if result == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    // SAFETY: successful `GetFileInformationByHandle` initialized the output.
    let information = unsafe { information.assume_init() };
    Ok((
        information.dwVolumeSerialNumber,
        (u64::from(information.nFileIndexHigh) << 32) | u64::from(information.nFileIndexLow),
    ))
}

#[cfg(windows)]
fn windows_version() -> String {
    Command::new("cmd")
        .args(["/D", "/C", "ver"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".to_owned())
}
