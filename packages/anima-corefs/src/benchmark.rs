//! Deterministic release benchmark support for full immutable catalog commits.

use std::collections::BTreeMap;
use std::fs;
use std::io::Cursor;
use std::path::Path;
use std::time::{Duration, Instant};

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
        let started = Instant::now();
        let outcome = coordinator.commit(
            &keys,
            &[],
            &[precondition],
            |_, _| Ok(next_catalog),
            |_| Ok(()),
        )?;
        commit_samples.push(started.elapsed());
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
