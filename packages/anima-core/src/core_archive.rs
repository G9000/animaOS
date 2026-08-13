//! Streaming ANIMA CORE V2 archive container.
//!
//! V1 `anima_capsule` remains import-only compatibility. This format carries
//! complete Core records with a closed payload kind, bounded KDF/header,
//! pre-hashed sources, globally monotonic nonces, authenticated chunk
//! coordinates, and an authenticated complete-inventory footer.

use std::collections::HashSet;
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use argon2::{Algorithm, Argon2, Params, Version};
use hkdf::Hkdf;
use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;
use zeroize::Zeroizing;

pub const MAGIC: &[u8; 8] = b"ANIMACR2";
pub const TRAILER_MAGIC: &[u8; 8] = b"ANIMAEND";
pub const FORMAT_VERSION: u16 = 2;
pub const HEADER_LENGTH: u16 = 105;
pub const CIPHER_ID_AES_256_GCM: u8 = 1;
pub const KDF_ID_ARGON2ID_HKDF_SHA256: u8 = 1;
pub const KDF_PROFILE_ID_V2: u8 = 1;
pub const KDF_TIME_COST: u32 = 4;
pub const KDF_MEMORY_KIB: u32 = 131_072;
pub const KDF_PARALLELISM: u32 = 4;
pub const KDF_SALT_BYTES: usize = 32;
pub const CHUNK_LIMIT_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_STREAMING_WORKING_BYTES: usize = 32 * 1024 * 1024;
pub const MAX_MANIFEST_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_RECORDS: usize = 100_000;
pub const MAX_RECORD_PATH_BYTES: usize = 32 * 1024;

const MANIFEST_NONCE_ORDINAL: u64 = 0;
const AAD_DOMAIN: &[u8] = b"anima-core-archive-chunk-v2:";
const MANIFEST_AAD_DOMAIN: &[u8] = b"anima-core-archive-v2-manifest";
const FOOTER_AAD_DOMAIN: &[u8] = b"anima-core-archive-v2-footer";
const HKDF_INFO: &[u8] = b"anima-core-archive-v2";
const GCM_TAG_BYTES: usize = 16;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum PayloadKind {
    Full = 1,
    Soul = 2,
    Fs = 3,
}

impl PayloadKind {
    fn from_u8(value: u8) -> Result<Self, CoreArchiveError> {
        match value {
            1 => Ok(Self::Full),
            2 => Ok(Self::Soul),
            3 => Ok(Self::Fs),
            _ => Err(CoreArchiveError::InvalidHeader("payload kind")),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum VolumeMode {
    Single = 1,
    Multipart = 2,
}

impl VolumeMode {
    fn from_u8(value: u8) -> Result<Self, CoreArchiveError> {
        match value {
            1 => Ok(Self::Single),
            2 => Ok(Self::Multipart),
            _ => Err(CoreArchiveError::InvalidHeader("volume mode")),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum RecordType {
    Manifest = 1,
    SoulDatabase = 2,
    Catalog = 3,
    Object = 4,
    Keyslots = 5,
    Recovery = 6,
}

impl RecordType {
    fn as_u8(self) -> u8 {
        self as u8
    }
}

#[derive(Debug, thiserror::Error)]
pub enum CoreArchiveError {
    #[error("archive I/O failed: {0}")]
    Io(#[from] io::Error),
    #[error("archive serialization failed: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("archive cryptography failed")]
    Crypto,
    #[error("invalid archive header: {0}")]
    InvalidHeader(&'static str),
    #[error("archive manifest is invalid: {0}")]
    InvalidManifest(&'static str),
    #[error("archive record is invalid: {0}")]
    InvalidRecord(&'static str),
    #[error("archive source changed while it was being streamed")]
    SourceChanged,
    #[error("archive nonce ordinal overflow")]
    NonceOverflow,
    #[error("archive streaming memory bound was exceeded")]
    MemoryBound,
    #[error("archive payload kind does not permit record {0:?}")]
    RecordNotAllowed(RecordType),
}

#[derive(Clone, Debug)]
pub struct ArchiveSource {
    pub record_type: RecordType,
    pub record_path: String,
    pub source_path: PathBuf,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArchiveWriteOptions {
    pub payload_kind: PayloadKind,
    pub capture: ArchiveCapture,
    pub archive_id: Uuid,
    pub volume_set_id: Uuid,
    pub volume_mode: VolumeMode,
    pub declared_volume_count: u32,
    pub volume_ordinal: u32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArchiveCapture {
    pub core_id: Uuid,
    pub owner_id: Uuid,
    pub soul_generation: Option<u64>,
    pub filesystem_generation: Option<u64>,
}

impl ArchiveCapture {
    #[must_use]
    pub fn full(
        core_id: Uuid,
        owner_id: Uuid,
        soul_generation: u64,
        filesystem_generation: u64,
    ) -> Self {
        Self {
            core_id,
            owner_id,
            soul_generation: Some(soul_generation),
            filesystem_generation: Some(filesystem_generation),
        }
    }

    #[must_use]
    pub fn soul(core_id: Uuid, owner_id: Uuid, soul_generation: u64) -> Self {
        Self {
            core_id,
            owner_id,
            soul_generation: Some(soul_generation),
            filesystem_generation: None,
        }
    }

    #[must_use]
    pub fn fs(core_id: Uuid, owner_id: Uuid, filesystem_generation: u64) -> Self {
        Self {
            core_id,
            owner_id,
            soul_generation: None,
            filesystem_generation: Some(filesystem_generation),
        }
    }
}

impl ArchiveWriteOptions {
    #[must_use]
    pub fn single(payload_kind: PayloadKind, capture: ArchiveCapture) -> Self {
        let archive_id = Uuid::new_v4();
        Self {
            payload_kind,
            capture,
            archive_id,
            volume_set_id: archive_id,
            volume_mode: VolumeMode::Single,
            declared_volume_count: 1,
            volume_ordinal: 0,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArchiveSummary {
    pub archive_id: Uuid,
    pub volume_set_id: Uuid,
    pub payload_kind: PayloadKind,
    pub capture: ArchiveCapture,
    pub record_count: usize,
    pub chunk_count: u64,
    pub plaintext_bytes: u64,
    pub max_buffer_bytes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExtractedArchive {
    pub summary: ArchiveSummary,
    pub records: Vec<ExtractedRecord>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExtractedRecord {
    pub record_type: RecordType,
    pub record_path: String,
    pub plaintext_length: u64,
    pub record_hash: [u8; 32],
}

#[derive(Clone, Debug)]
struct FixedHeader {
    kdf_salt: [u8; KDF_SALT_BYTES],
    archive_id: Uuid,
    volume_set_id: Uuid,
    payload_kind: PayloadKind,
    volume_mode: VolumeMode,
    declared_volume_count: u32,
    nonce_prefix: [u8; 4],
}

impl FixedHeader {
    fn new(options: &ArchiveWriteOptions) -> Result<Self, CoreArchiveError> {
        validate_volume_contract(
            options.archive_id,
            options.volume_set_id,
            options.volume_mode,
            options.declared_volume_count,
            options.volume_ordinal,
        )?;
        let mut kdf_salt = [0u8; KDF_SALT_BYTES];
        OsRng.fill_bytes(&mut kdf_salt);
        let mut nonce_prefix = [0u8; 4];
        OsRng.fill_bytes(&mut nonce_prefix);
        Ok(Self {
            kdf_salt,
            archive_id: options.archive_id,
            volume_set_id: options.volume_set_id,
            payload_kind: options.payload_kind,
            volume_mode: options.volume_mode,
            declared_volume_count: options.declared_volume_count,
            nonce_prefix,
        })
    }

    fn encode(&self) -> Vec<u8> {
        let mut encoded = Vec::with_capacity(usize::from(HEADER_LENGTH));
        encoded.extend_from_slice(MAGIC);
        encoded.extend_from_slice(&FORMAT_VERSION.to_be_bytes());
        encoded.extend_from_slice(&HEADER_LENGTH.to_be_bytes());
        encoded.push(CIPHER_ID_AES_256_GCM);
        encoded.push(KDF_ID_ARGON2ID_HKDF_SHA256);
        encoded.push(KDF_PROFILE_ID_V2);
        encoded.extend_from_slice(&KDF_TIME_COST.to_be_bytes());
        encoded.extend_from_slice(&KDF_MEMORY_KIB.to_be_bytes());
        encoded.extend_from_slice(&KDF_PARALLELISM.to_be_bytes());
        encoded.extend_from_slice(&self.kdf_salt);
        encoded.extend_from_slice(self.archive_id.as_bytes());
        encoded.extend_from_slice(self.volume_set_id.as_bytes());
        encoded.push(self.payload_kind as u8);
        encoded.push(self.volume_mode as u8);
        encoded.extend_from_slice(&self.declared_volume_count.to_be_bytes());
        encoded.extend_from_slice(&(CHUNK_LIMIT_BYTES as u32).to_be_bytes());
        encoded.extend_from_slice(&self.nonce_prefix);
        debug_assert_eq!(encoded.len(), usize::from(HEADER_LENGTH));
        encoded
    }

    fn decode<R: Read>(reader: &mut R) -> Result<(Self, Vec<u8>), CoreArchiveError> {
        let mut encoded = vec![0u8; usize::from(HEADER_LENGTH)];
        reader.read_exact(&mut encoded)?;
        if &encoded[..8] != MAGIC {
            return Err(CoreArchiveError::InvalidHeader("magic"));
        }
        if read_u16(&encoded, 8)? != FORMAT_VERSION {
            return Err(CoreArchiveError::InvalidHeader("format version"));
        }
        if read_u16(&encoded, 10)? != HEADER_LENGTH {
            return Err(CoreArchiveError::InvalidHeader("header length"));
        }
        if encoded[12] != CIPHER_ID_AES_256_GCM {
            return Err(CoreArchiveError::InvalidHeader("cipher"));
        }
        if encoded[13] != KDF_ID_ARGON2ID_HKDF_SHA256 {
            return Err(CoreArchiveError::InvalidHeader("KDF"));
        }
        if encoded[14] != KDF_PROFILE_ID_V2 {
            return Err(CoreArchiveError::InvalidHeader("KDF profile"));
        }
        if read_u32(&encoded, 15)? != KDF_TIME_COST
            || read_u32(&encoded, 19)? != KDF_MEMORY_KIB
            || read_u32(&encoded, 23)? != KDF_PARALLELISM
        {
            return Err(CoreArchiveError::InvalidHeader("KDF costs"));
        }
        let kdf_salt = copy_array::<KDF_SALT_BYTES>(&encoded, 27)?;
        let archive_id = Uuid::from_bytes(copy_array::<16>(&encoded, 59)?);
        let volume_set_id = Uuid::from_bytes(copy_array::<16>(&encoded, 75)?);
        let payload_kind = PayloadKind::from_u8(encoded[91])?;
        let volume_mode = VolumeMode::from_u8(encoded[92])?;
        let declared_volume_count = read_u32(&encoded, 93)?;
        if read_u32(&encoded, 97)? != CHUNK_LIMIT_BYTES as u32 {
            return Err(CoreArchiveError::InvalidHeader("chunk limit"));
        }
        let nonce_prefix = copy_array::<4>(&encoded, 101)?;
        validate_volume_contract(
            archive_id,
            volume_set_id,
            volume_mode,
            declared_volume_count,
            if volume_mode == VolumeMode::Single {
                0
            } else {
                1
            },
        )?;
        Ok((
            Self {
                kdf_salt,
                archive_id,
                volume_set_id,
                payload_kind,
                volume_mode,
                declared_volume_count,
                nonce_prefix,
            },
            encoded,
        ))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct ArchiveManifest {
    version: u8,
    archive_id: Uuid,
    volume_set_id: Uuid,
    payload_kind: PayloadKind,
    volume_mode: VolumeMode,
    declared_volume_count: u32,
    volume_ordinal: u32,
    core_id: Uuid,
    owner_id: Uuid,
    soul_generation: Option<u64>,
    filesystem_generation: Option<u64>,
    expected_record_count: u64,
    expected_chunk_count: u64,
    expected_plaintext_bytes: u64,
    header_hash: String,
    records: Vec<RecordDescriptor>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct RecordDescriptor {
    record_type: RecordType,
    record_path: String,
    record_ordinal: u64,
    record_hash: String,
    plaintext_length: u64,
    chunk_count: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct InventoryFooter {
    version: u8,
    archive_id: Uuid,
    volume_set_id: Uuid,
    payload_kind: PayloadKind,
    volume_ordinal: u32,
    record_count: u64,
    chunk_count: u64,
    plaintext_bytes: u64,
    inventory_hash: String,
}

struct PreparedSource {
    descriptor: RecordDescriptor,
    file: File,
}

pub fn write_archive<W: Write>(
    writer: &mut W,
    passphrase: &[u8],
    options: &ArchiveWriteOptions,
    sources: Vec<ArchiveSource>,
) -> Result<ArchiveSummary, CoreArchiveError> {
    if passphrase.is_empty() {
        return Err(CoreArchiveError::InvalidManifest("empty passphrase"));
    }
    if sources.is_empty() || sources.len() > MAX_RECORDS {
        return Err(CoreArchiveError::InvalidManifest("record count"));
    }
    validate_capture_contract(options.payload_kind, &options.capture)?;
    validate_source_set(options.payload_kind, &sources)?;

    let mut prepared = Vec::with_capacity(sources.len());
    let mut total_chunks = 0u64;
    let mut total_plaintext = 0u64;
    for (ordinal, source) in sources.into_iter().enumerate() {
        validate_record_path(&source.record_path)?;
        let mut file = OpenOptions::new().read(true).open(&source.source_path)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(CoreArchiveError::InvalidRecord(
                "source is not a regular file",
            ));
        }
        let plaintext_length = metadata.len();
        let record_hash = hash_reader(&mut file)?;
        file.seek(SeekFrom::Start(0))?;
        let chunk_count = chunk_count(plaintext_length)?;
        total_chunks = total_chunks
            .checked_add(chunk_count)
            .ok_or(CoreArchiveError::InvalidManifest("chunk count overflow"))?;
        total_plaintext = total_plaintext.checked_add(plaintext_length).ok_or(
            CoreArchiveError::InvalidManifest("plaintext length overflow"),
        )?;
        prepared.push(PreparedSource {
            descriptor: RecordDescriptor {
                record_type: source.record_type,
                record_path: source.record_path,
                record_ordinal: u64::try_from(ordinal)
                    .map_err(|_| CoreArchiveError::InvalidManifest("record ordinal"))?,
                record_hash: hex(&record_hash),
                plaintext_length,
                chunk_count,
            },
            file,
        });
    }

    let header = FixedHeader::new(options)?;
    let header_bytes = header.encode();
    let header_hash: [u8; 32] = Sha256::digest(&header_bytes).into();
    let manifest = ArchiveManifest {
        version: 1,
        archive_id: options.archive_id,
        volume_set_id: options.volume_set_id,
        payload_kind: options.payload_kind,
        volume_mode: options.volume_mode,
        declared_volume_count: options.declared_volume_count,
        volume_ordinal: options.volume_ordinal,
        core_id: options.capture.core_id,
        owner_id: options.capture.owner_id,
        soul_generation: options.capture.soul_generation,
        filesystem_generation: options.capture.filesystem_generation,
        expected_record_count: u64::try_from(prepared.len())
            .map_err(|_| CoreArchiveError::InvalidManifest("record count"))?,
        expected_chunk_count: total_chunks,
        expected_plaintext_bytes: total_plaintext,
        header_hash: hex(&header_hash),
        records: prepared
            .iter()
            .map(|source| source.descriptor.clone())
            .collect(),
    };
    let manifest_bytes = serde_json::to_vec(&manifest)?;
    if manifest_bytes.len() > MAX_MANIFEST_BYTES {
        return Err(CoreArchiveError::InvalidManifest("manifest size"));
    }

    let key = Zeroizing::new(derive_archive_key(passphrase, &header.kdf_salt)?);
    let cipher = Aes256Gcm::new_from_slice(key.as_ref()).map_err(|_| CoreArchiveError::Crypto)?;
    writer.write_all(&header_bytes)?;
    let manifest_aad = envelope_aad(MANIFEST_AAD_DOMAIN, &header_hash);
    let encrypted_manifest = encrypt(
        &cipher,
        &header.nonce_prefix,
        MANIFEST_NONCE_ORDINAL,
        &manifest_aad,
        &manifest_bytes,
    )?;
    write_length_prefixed(writer, &encrypted_manifest)?;

    let mut nonce_ordinal = 1u64;
    let mut max_buffer_bytes = manifest_bytes.len() + encrypted_manifest.len();
    let mut second_pass_inventory = Sha256::new();
    for source in &mut prepared {
        let expected_hash = parse_hash(&source.descriptor.record_hash)?;
        inventory_update(&mut second_pass_inventory, &source.descriptor)?;
        let mut streamed_hash = Sha256::new();
        let mut offset = 0u64;
        for chunk_index in 0..source.descriptor.chunk_count {
            let remaining = source.descriptor.plaintext_length - offset;
            let plaintext_length = usize::try_from(remaining.min(CHUNK_LIMIT_BYTES as u64))
                .map_err(|_| CoreArchiveError::InvalidRecord("chunk length"))?;
            let mut plaintext = vec![0u8; plaintext_length];
            source.file.read_exact(&mut plaintext)?;
            streamed_hash.update(&plaintext);
            let ciphertext_length = plaintext_length
                .checked_add(GCM_TAG_BYTES)
                .ok_or(CoreArchiveError::InvalidRecord("ciphertext length"))?;
            let aad = chunk_aad(
                &header_hash,
                &manifest,
                &source.descriptor,
                &expected_hash,
                chunk_index,
                offset,
                plaintext_length,
                ciphertext_length,
            )?;
            let ciphertext = encrypt(
                &cipher,
                &header.nonce_prefix,
                nonce_ordinal,
                &aad,
                &plaintext,
            )?;
            nonce_ordinal = nonce_ordinal
                .checked_add(1)
                .ok_or(CoreArchiveError::NonceOverflow)?;
            max_buffer_bytes = max_buffer_bytes.max(plaintext.len() + ciphertext.len());
            enforce_memory_bound(max_buffer_bytes)?;
            write_length_prefixed(writer, &ciphertext)?;
            offset += u64::try_from(plaintext_length)
                .map_err(|_| CoreArchiveError::InvalidRecord("chunk length"))?;
        }
        let actual_hash: [u8; 32] = streamed_hash.finalize().into();
        if actual_hash != expected_hash || source.file.read(&mut [0u8; 1])? != 0 {
            return Err(CoreArchiveError::SourceChanged);
        }
    }

    let footer = InventoryFooter {
        version: 1,
        archive_id: options.archive_id,
        volume_set_id: options.volume_set_id,
        payload_kind: options.payload_kind,
        volume_ordinal: options.volume_ordinal,
        record_count: u64::try_from(prepared.len())
            .map_err(|_| CoreArchiveError::InvalidManifest("record count"))?,
        chunk_count: total_chunks,
        plaintext_bytes: total_plaintext,
        inventory_hash: hex(&second_pass_inventory.finalize()),
    };
    let footer_bytes = serde_json::to_vec(&footer)?;
    let footer_aad = envelope_aad(FOOTER_AAD_DOMAIN, &header_hash);
    let encrypted_footer = encrypt(
        &cipher,
        &header.nonce_prefix,
        nonce_ordinal,
        &footer_aad,
        &footer_bytes,
    )?;
    max_buffer_bytes = max_buffer_bytes.max(footer_bytes.len() + encrypted_footer.len());
    enforce_memory_bound(max_buffer_bytes)?;
    write_length_prefixed(writer, &encrypted_footer)?;
    writer.write_all(TRAILER_MAGIC)?;
    writer.flush()?;
    Ok(ArchiveSummary {
        archive_id: options.archive_id,
        volume_set_id: options.volume_set_id,
        payload_kind: options.payload_kind,
        capture: options.capture.clone(),
        record_count: prepared.len(),
        chunk_count: total_chunks,
        plaintext_bytes: total_plaintext,
        max_buffer_bytes,
    })
}

pub fn extract_archive<R: Read>(
    reader: &mut R,
    passphrase: &[u8],
    destination: &Path,
) -> Result<ExtractedArchive, CoreArchiveError> {
    if destination.exists() {
        return Err(CoreArchiveError::InvalidRecord(
            "staging destination already exists",
        ));
    }
    let result = extract_archive_into(reader, passphrase, destination);
    if result.is_err() && destination.exists() {
        let _ = std::fs::remove_dir_all(destination);
    }
    result
}

fn extract_archive_into<R: Read>(
    reader: &mut R,
    passphrase: &[u8],
    destination: &Path,
) -> Result<ExtractedArchive, CoreArchiveError> {
    if passphrase.is_empty() {
        return Err(CoreArchiveError::InvalidManifest("empty passphrase"));
    }
    let (header, header_bytes) = FixedHeader::decode(reader)?;
    let header_hash: [u8; 32] = Sha256::digest(&header_bytes).into();
    let key = Zeroizing::new(derive_archive_key(passphrase, &header.kdf_salt)?);
    let cipher = Aes256Gcm::new_from_slice(key.as_ref()).map_err(|_| CoreArchiveError::Crypto)?;
    let encrypted_manifest = read_length_prefixed(reader, MAX_MANIFEST_BYTES + GCM_TAG_BYTES)?;
    let manifest_aad = envelope_aad(MANIFEST_AAD_DOMAIN, &header_hash);
    let manifest_bytes = decrypt(
        &cipher,
        &header.nonce_prefix,
        MANIFEST_NONCE_ORDINAL,
        &manifest_aad,
        &encrypted_manifest,
    )?;
    if manifest_bytes.len() > MAX_MANIFEST_BYTES {
        return Err(CoreArchiveError::InvalidManifest("manifest size"));
    }
    let manifest: ArchiveManifest = serde_json::from_slice(&manifest_bytes)?;
    validate_manifest(&header, &header_hash, &manifest)?;

    std::fs::create_dir_all(destination)?;
    let mut nonce_ordinal = 1u64;
    let mut max_buffer_bytes = manifest_bytes.len() + encrypted_manifest.len();
    let mut inventory = Sha256::new();
    let mut records = Vec::with_capacity(manifest.records.len());
    let mut total_chunks = 0u64;
    let mut total_plaintext = 0u64;
    for descriptor in &manifest.records {
        inventory_update(&mut inventory, descriptor)?;
        let record_hash = parse_hash(&descriptor.record_hash)?;
        let target = safe_destination(destination, &descriptor.record_path)?;
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)?;
        let mut hasher = Sha256::new();
        let mut offset = 0u64;
        for chunk_index in 0..descriptor.chunk_count {
            let remaining = descriptor.plaintext_length - offset;
            let plaintext_length = usize::try_from(remaining.min(CHUNK_LIMIT_BYTES as u64))
                .map_err(|_| CoreArchiveError::InvalidRecord("chunk length"))?;
            let ciphertext_length = plaintext_length
                .checked_add(GCM_TAG_BYTES)
                .ok_or(CoreArchiveError::InvalidRecord("ciphertext length"))?;
            let ciphertext = read_length_prefixed(reader, CHUNK_LIMIT_BYTES + GCM_TAG_BYTES)?;
            if ciphertext.len() != ciphertext_length {
                return Err(CoreArchiveError::InvalidRecord("ciphertext length"));
            }
            let aad = chunk_aad(
                &header_hash,
                &manifest,
                descriptor,
                &record_hash,
                chunk_index,
                offset,
                plaintext_length,
                ciphertext_length,
            )?;
            let plaintext = decrypt(
                &cipher,
                &header.nonce_prefix,
                nonce_ordinal,
                &aad,
                &ciphertext,
            )?;
            nonce_ordinal = nonce_ordinal
                .checked_add(1)
                .ok_or(CoreArchiveError::NonceOverflow)?;
            if plaintext.len() != plaintext_length {
                return Err(CoreArchiveError::InvalidRecord("plaintext length"));
            }
            max_buffer_bytes = max_buffer_bytes.max(plaintext.len() + ciphertext.len());
            enforce_memory_bound(max_buffer_bytes)?;
            output.write_all(&plaintext)?;
            hasher.update(&plaintext);
            offset += u64::try_from(plaintext.len())
                .map_err(|_| CoreArchiveError::InvalidRecord("chunk length"))?;
        }
        if offset != descriptor.plaintext_length
            || <[u8; 32]>::from(hasher.finalize()) != record_hash
        {
            return Err(CoreArchiveError::InvalidRecord("record hash"));
        }
        output.flush()?;
        output.sync_all()?;
        total_chunks = total_chunks
            .checked_add(descriptor.chunk_count)
            .ok_or(CoreArchiveError::InvalidManifest("chunk count overflow"))?;
        total_plaintext = total_plaintext
            .checked_add(descriptor.plaintext_length)
            .ok_or(CoreArchiveError::InvalidManifest(
                "plaintext length overflow",
            ))?;
        records.push(ExtractedRecord {
            record_type: descriptor.record_type,
            record_path: descriptor.record_path.clone(),
            plaintext_length: descriptor.plaintext_length,
            record_hash,
        });
    }

    let encrypted_footer = read_length_prefixed(reader, MAX_MANIFEST_BYTES + GCM_TAG_BYTES)?;
    let footer_aad = envelope_aad(FOOTER_AAD_DOMAIN, &header_hash);
    let footer_bytes = decrypt(
        &cipher,
        &header.nonce_prefix,
        nonce_ordinal,
        &footer_aad,
        &encrypted_footer,
    )?;
    let footer: InventoryFooter = serde_json::from_slice(&footer_bytes)?;
    validate_footer(
        &manifest,
        &footer,
        records.len(),
        total_chunks,
        total_plaintext,
        &inventory.finalize(),
    )?;
    let mut trailer = [0u8; 8];
    reader.read_exact(&mut trailer)?;
    if &trailer != TRAILER_MAGIC {
        return Err(CoreArchiveError::InvalidManifest("trailer"));
    }
    if reader.read(&mut [0u8; 1])? != 0 {
        return Err(CoreArchiveError::InvalidManifest("appended data"));
    }
    max_buffer_bytes = max_buffer_bytes.max(footer_bytes.len() + encrypted_footer.len());
    enforce_memory_bound(max_buffer_bytes)?;
    Ok(ExtractedArchive {
        summary: ArchiveSummary {
            archive_id: manifest.archive_id,
            volume_set_id: manifest.volume_set_id,
            payload_kind: manifest.payload_kind,
            capture: ArchiveCapture {
                core_id: manifest.core_id,
                owner_id: manifest.owner_id,
                soul_generation: manifest.soul_generation,
                filesystem_generation: manifest.filesystem_generation,
            },
            record_count: records.len(),
            chunk_count: total_chunks,
            plaintext_bytes: total_plaintext,
            max_buffer_bytes,
        },
        records,
    })
}

fn validate_volume_contract(
    archive_id: Uuid,
    volume_set_id: Uuid,
    volume_mode: VolumeMode,
    declared_volume_count: u32,
    volume_ordinal: u32,
) -> Result<(), CoreArchiveError> {
    match volume_mode {
        VolumeMode::Single
            if archive_id == volume_set_id && declared_volume_count == 1 && volume_ordinal == 0 =>
        {
            Ok(())
        }
        VolumeMode::Multipart
            if declared_volume_count > 1
                && volume_ordinal > 0
                && volume_ordinal <= declared_volume_count =>
        {
            Ok(())
        }
        _ => Err(CoreArchiveError::InvalidHeader("volume contract")),
    }
}

fn validate_source_set(
    payload_kind: PayloadKind,
    sources: &[ArchiveSource],
) -> Result<(), CoreArchiveError> {
    let descriptors = sources
        .iter()
        .enumerate()
        .map(|(index, source)| RecordDescriptor {
            record_type: source.record_type,
            record_path: source.record_path.clone(),
            record_ordinal: index as u64,
            record_hash: "00".repeat(32),
            plaintext_length: 0,
            chunk_count: 1,
        })
        .collect::<Vec<_>>();
    validate_descriptor_set(payload_kind, &descriptors)
}

fn validate_descriptor_set(
    payload_kind: PayloadKind,
    records: &[RecordDescriptor],
) -> Result<(), CoreArchiveError> {
    if records.is_empty() || records.len() > MAX_RECORDS {
        return Err(CoreArchiveError::InvalidManifest("record count"));
    }
    let mut paths = HashSet::with_capacity(records.len());
    let mut has_manifest = false;
    let mut has_soul = false;
    let mut has_head = false;
    let mut has_catalog = false;
    let mut has_keyslots = false;
    for (expected_ordinal, record) in records.iter().enumerate() {
        if record.record_ordinal != expected_ordinal as u64 {
            return Err(CoreArchiveError::InvalidRecord("record ordinal"));
        }
        validate_record_path(&record.record_path)?;
        validate_record_location(record.record_type, &record.record_path)?;
        if !paths.insert(record.record_path.as_str()) {
            return Err(CoreArchiveError::InvalidRecord("duplicate record path"));
        }
        if !record_allowed(payload_kind, record.record_type) {
            return Err(CoreArchiveError::RecordNotAllowed(record.record_type));
        }
        has_manifest |= record.record_type == RecordType::Manifest;
        has_soul |= record.record_type == RecordType::SoulDatabase;
        has_head |= record.record_type == RecordType::Catalog && record.record_path == "fs/HEAD";
        has_catalog |= record.record_type == RecordType::Catalog
            && record.record_path.starts_with("fs/catalogs/");
        has_keyslots |= record.record_type == RecordType::Keyslots;
        if record.chunk_count != chunk_count(record.plaintext_length)? {
            return Err(CoreArchiveError::InvalidRecord("chunk count"));
        }
        parse_hash(&record.record_hash)?;
    }
    if !has_manifest
        || matches!(payload_kind, PayloadKind::Full | PayloadKind::Soul) && !has_soul
        || matches!(payload_kind, PayloadKind::Full | PayloadKind::Fs)
            && (!has_head || !has_catalog)
        || !has_keyslots
    {
        return Err(CoreArchiveError::InvalidManifest("payload completeness"));
    }
    Ok(())
}

fn validate_record_location(
    record_type: RecordType,
    record_path: &str,
) -> Result<(), CoreArchiveError> {
    let accepted = match record_type {
        RecordType::Manifest => record_path == "manifest.json",
        RecordType::SoulDatabase => record_path == "soul/soul.db",
        RecordType::Catalog => {
            record_path == "fs/HEAD"
                || record_path == "fs/CUTOVER_RECEIPT"
                || record_path == "fs/CUTOVER_COMPLETE"
                || record_path.starts_with("fs/catalogs/") && record_path.ends_with(".acore")
        }
        RecordType::Object => {
            record_path.starts_with("objects/") && record_path.ends_with(".acore")
        }
        RecordType::Keyslots => record_path == "keyslots/root-keyslots.json",
        RecordType::Recovery => record_path.starts_with("recovery/"),
    };
    if accepted {
        Ok(())
    } else {
        Err(CoreArchiveError::InvalidRecord("record location"))
    }
}

fn record_allowed(payload_kind: PayloadKind, record_type: RecordType) -> bool {
    match payload_kind {
        PayloadKind::Full => true,
        PayloadKind::Soul => !matches!(record_type, RecordType::Catalog | RecordType::Object),
        PayloadKind::Fs => record_type != RecordType::SoulDatabase,
    }
}

fn validate_capture_contract(
    payload_kind: PayloadKind,
    capture: &ArchiveCapture,
) -> Result<(), CoreArchiveError> {
    let valid = match payload_kind {
        PayloadKind::Full => {
            capture.soul_generation.is_some() && capture.filesystem_generation.is_some()
        }
        PayloadKind::Soul => {
            capture.soul_generation.is_some() && capture.filesystem_generation.is_none()
        }
        PayloadKind::Fs => {
            capture.soul_generation.is_none() && capture.filesystem_generation.is_some()
        }
    };
    if valid {
        Ok(())
    } else {
        Err(CoreArchiveError::InvalidManifest("capture generations"))
    }
}

fn validate_manifest(
    header: &FixedHeader,
    header_hash: &[u8; 32],
    manifest: &ArchiveManifest,
) -> Result<(), CoreArchiveError> {
    if manifest.version != 1
        || manifest.archive_id != header.archive_id
        || manifest.volume_set_id != header.volume_set_id
        || manifest.payload_kind != header.payload_kind
        || manifest.volume_mode != header.volume_mode
        || manifest.declared_volume_count != header.declared_volume_count
        || manifest.header_hash != hex(header_hash)
    {
        return Err(CoreArchiveError::InvalidManifest("header binding"));
    }
    validate_volume_contract(
        manifest.archive_id,
        manifest.volume_set_id,
        manifest.volume_mode,
        manifest.declared_volume_count,
        manifest.volume_ordinal,
    )?;
    validate_capture_contract(
        manifest.payload_kind,
        &ArchiveCapture {
            core_id: manifest.core_id,
            owner_id: manifest.owner_id,
            soul_generation: manifest.soul_generation,
            filesystem_generation: manifest.filesystem_generation,
        },
    )?;
    validate_descriptor_set(manifest.payload_kind, &manifest.records)?;
    let record_count = u64::try_from(manifest.records.len())
        .map_err(|_| CoreArchiveError::InvalidManifest("record count"))?;
    let (chunk_count, plaintext_bytes) =
        manifest
            .records
            .iter()
            .try_fold((0u64, 0u64), |(chunks, bytes), record| {
                Ok::<_, CoreArchiveError>((
                    chunks
                        .checked_add(record.chunk_count)
                        .ok_or(CoreArchiveError::InvalidManifest("chunk count overflow"))?,
                    bytes.checked_add(record.plaintext_length).ok_or(
                        CoreArchiveError::InvalidManifest("plaintext length overflow"),
                    )?,
                ))
            })?;
    if manifest.expected_record_count != record_count
        || manifest.expected_chunk_count != chunk_count
        || manifest.expected_plaintext_bytes != plaintext_bytes
    {
        return Err(CoreArchiveError::InvalidManifest("expected inventory"));
    }
    Ok(())
}

fn validate_footer(
    manifest: &ArchiveManifest,
    footer: &InventoryFooter,
    record_count: usize,
    chunk_count: u64,
    plaintext_bytes: u64,
    inventory_hash: &[u8],
) -> Result<(), CoreArchiveError> {
    if footer.version != 1
        || footer.archive_id != manifest.archive_id
        || footer.volume_set_id != manifest.volume_set_id
        || footer.payload_kind != manifest.payload_kind
        || footer.volume_ordinal != manifest.volume_ordinal
        || footer.record_count != record_count as u64
        || footer.chunk_count != chunk_count
        || footer.plaintext_bytes != plaintext_bytes
        || footer.inventory_hash != hex(inventory_hash)
    {
        return Err(CoreArchiveError::InvalidManifest("inventory footer"));
    }
    Ok(())
}

fn derive_archive_key(
    passphrase: &[u8],
    salt: &[u8; KDF_SALT_BYTES],
) -> Result<[u8; 32], CoreArchiveError> {
    let params = Params::new(KDF_MEMORY_KIB, KDF_TIME_COST, KDF_PARALLELISM, Some(32))
        .map_err(|_| CoreArchiveError::Crypto)?;
    let argon2 = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);
    let mut argon_output = Zeroizing::new([0u8; 32]);
    argon2
        .hash_password_into(passphrase, salt, argon_output.as_mut())
        .map_err(|_| CoreArchiveError::Crypto)?;
    let hkdf = Hkdf::<Sha256>::new(None, argon_output.as_ref());
    let mut archive_key = [0u8; 32];
    hkdf.expand(HKDF_INFO, &mut archive_key)
        .map_err(|_| CoreArchiveError::Crypto)?;
    Ok(archive_key)
}

fn encrypt(
    cipher: &Aes256Gcm,
    nonce_prefix: &[u8; 4],
    ordinal: u64,
    aad: &[u8],
    plaintext: &[u8],
) -> Result<Vec<u8>, CoreArchiveError> {
    cipher
        .encrypt(
            Nonce::from_slice(&nonce(nonce_prefix, ordinal)),
            Payload {
                msg: plaintext,
                aad,
            },
        )
        .map_err(|_| CoreArchiveError::Crypto)
}

fn decrypt(
    cipher: &Aes256Gcm,
    nonce_prefix: &[u8; 4],
    ordinal: u64,
    aad: &[u8],
    ciphertext: &[u8],
) -> Result<Vec<u8>, CoreArchiveError> {
    cipher
        .decrypt(
            Nonce::from_slice(&nonce(nonce_prefix, ordinal)),
            Payload {
                msg: ciphertext,
                aad,
            },
        )
        .map_err(|_| CoreArchiveError::Crypto)
}

fn nonce(prefix: &[u8; 4], ordinal: u64) -> [u8; 12] {
    let mut nonce = [0u8; 12];
    nonce[..4].copy_from_slice(prefix);
    nonce[4..].copy_from_slice(&ordinal.to_be_bytes());
    nonce
}

#[allow(clippy::too_many_arguments)]
fn chunk_aad(
    header_hash: &[u8; 32],
    manifest: &ArchiveManifest,
    descriptor: &RecordDescriptor,
    record_hash: &[u8; 32],
    chunk_index: u64,
    plaintext_offset: u64,
    plaintext_length: usize,
    ciphertext_length: usize,
) -> Result<Vec<u8>, CoreArchiveError> {
    let mut aad = Vec::with_capacity(256 + descriptor.record_path.len());
    push_field(&mut aad, AAD_DOMAIN)?;
    push_field(&mut aad, header_hash)?;
    push_field(&mut aad, manifest.archive_id.as_bytes())?;
    push_field(&mut aad, manifest.volume_set_id.as_bytes())?;
    push_field(&mut aad, &[manifest.payload_kind as u8])?;
    push_field(&mut aad, &[descriptor.record_type.as_u8()])?;
    push_field(&mut aad, descriptor.record_path.as_bytes())?;
    push_field(&mut aad, &descriptor.record_ordinal.to_be_bytes())?;
    push_field(&mut aad, record_hash)?;
    push_field(&mut aad, &chunk_index.to_be_bytes())?;
    push_field(&mut aad, &descriptor.chunk_count.to_be_bytes())?;
    push_field(&mut aad, &plaintext_offset.to_be_bytes())?;
    push_field(
        &mut aad,
        &u64::try_from(plaintext_length)
            .map_err(|_| CoreArchiveError::InvalidRecord("plaintext length"))?
            .to_be_bytes(),
    )?;
    push_field(
        &mut aad,
        &u64::try_from(ciphertext_length)
            .map_err(|_| CoreArchiveError::InvalidRecord("ciphertext length"))?
            .to_be_bytes(),
    )?;
    let final_flag = u8::from(chunk_index + 1 == descriptor.chunk_count);
    push_field(&mut aad, &[final_flag])?;
    push_field(&mut aad, &manifest.volume_ordinal.to_be_bytes())?;
    Ok(aad)
}

fn envelope_aad(domain: &[u8], header_hash: &[u8; 32]) -> Vec<u8> {
    let mut aad = Vec::with_capacity(domain.len() + header_hash.len() + 8);
    push_field(&mut aad, domain).expect("fixed domain fits u32");
    push_field(&mut aad, header_hash).expect("hash fits u32");
    aad
}

fn inventory_update(
    hasher: &mut Sha256,
    descriptor: &RecordDescriptor,
) -> Result<(), CoreArchiveError> {
    let mut encoded = Vec::with_capacity(128 + descriptor.record_path.len());
    push_field(&mut encoded, &[descriptor.record_type.as_u8()])?;
    push_field(&mut encoded, descriptor.record_path.as_bytes())?;
    push_field(&mut encoded, &descriptor.record_ordinal.to_be_bytes())?;
    push_field(&mut encoded, &parse_hash(&descriptor.record_hash)?)?;
    push_field(&mut encoded, &descriptor.plaintext_length.to_be_bytes())?;
    push_field(&mut encoded, &descriptor.chunk_count.to_be_bytes())?;
    hasher.update(encoded);
    Ok(())
}

fn push_field(output: &mut Vec<u8>, value: &[u8]) -> Result<(), CoreArchiveError> {
    let length = u32::try_from(value.len())
        .map_err(|_| CoreArchiveError::InvalidRecord("AAD field length"))?;
    output.extend_from_slice(&length.to_be_bytes());
    output.extend_from_slice(value);
    Ok(())
}

fn hash_reader(file: &mut File) -> Result<[u8; 32], CoreArchiveError> {
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; CHUNK_LIMIT_BYTES];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hasher.finalize().into())
}

fn write_length_prefixed<W: Write>(writer: &mut W, value: &[u8]) -> Result<(), CoreArchiveError> {
    let length = u64::try_from(value.len())
        .map_err(|_| CoreArchiveError::InvalidRecord("length-prefixed value"))?;
    writer.write_all(&length.to_be_bytes())?;
    writer.write_all(value)?;
    Ok(())
}

fn read_length_prefixed<R: Read>(
    reader: &mut R,
    maximum: usize,
) -> Result<Vec<u8>, CoreArchiveError> {
    let mut length = [0u8; 8];
    reader.read_exact(&mut length)?;
    let length = usize::try_from(u64::from_be_bytes(length))
        .map_err(|_| CoreArchiveError::InvalidRecord("length-prefixed value"))?;
    if length > maximum {
        return Err(CoreArchiveError::InvalidRecord("length-prefixed value"));
    }
    let mut value = vec![0u8; length];
    reader.read_exact(&mut value)?;
    Ok(value)
}

fn validate_record_path(value: &str) -> Result<(), CoreArchiveError> {
    if value.is_empty()
        || value.len() > MAX_RECORD_PATH_BYTES
        || value.contains('\0')
        || value.contains('\\')
        || value.starts_with('/')
        || value.ends_with('/')
        || value.split('/').any(|part| part.is_empty())
    {
        return Err(CoreArchiveError::InvalidRecord("record path"));
    }
    let path = Path::new(value);
    if path.components().any(|component| {
        !matches!(component, Component::Normal(_))
            || matches!(component, Component::Normal(part) if part == "." || part == "..")
    }) {
        return Err(CoreArchiveError::InvalidRecord("record path"));
    }
    Ok(())
}

fn safe_destination(root: &Path, value: &str) -> Result<PathBuf, CoreArchiveError> {
    validate_record_path(value)?;
    Ok(value
        .split('/')
        .fold(root.to_path_buf(), |path, part| path.join(part)))
}

fn chunk_count(length: u64) -> Result<u64, CoreArchiveError> {
    if length == 0 {
        return Ok(1);
    }
    length
        .checked_add(CHUNK_LIMIT_BYTES as u64 - 1)
        .map(|value| value / CHUNK_LIMIT_BYTES as u64)
        .ok_or(CoreArchiveError::InvalidRecord("chunk count"))
}

fn enforce_memory_bound(value: usize) -> Result<(), CoreArchiveError> {
    if value > MAX_STREAMING_WORKING_BYTES {
        return Err(CoreArchiveError::MemoryBound);
    }
    Ok(())
}

fn parse_hash(value: &str) -> Result<[u8; 32], CoreArchiveError> {
    if value.len() != 64 {
        return Err(CoreArchiveError::InvalidRecord("record hash"));
    }
    let mut decoded = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        decoded[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
    }
    Ok(decoded)
}

fn hex_nibble(value: u8) -> Result<u8, CoreArchiveError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(CoreArchiveError::InvalidRecord("record hash")),
    }
}

fn hex(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

fn read_u16(encoded: &[u8], offset: usize) -> Result<u16, CoreArchiveError> {
    Ok(u16::from_be_bytes(copy_array::<2>(encoded, offset)?))
}

fn read_u32(encoded: &[u8], offset: usize) -> Result<u32, CoreArchiveError> {
    Ok(u32::from_be_bytes(copy_array::<4>(encoded, offset)?))
}

fn copy_array<const N: usize>(encoded: &[u8], offset: usize) -> Result<[u8; N], CoreArchiveError> {
    encoded
        .get(offset..offset + N)
        .ok_or(CoreArchiveError::InvalidHeader("truncated"))?
        .try_into()
        .map_err(|_| CoreArchiveError::InvalidHeader("truncated"))
}

#[cfg(test)]
mod tests {
    use super::*;

    const PASSPHRASE: &[u8] = b"correct horse battery staple";

    fn write_file(path: &Path, value: &[u8]) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, value).unwrap();
    }

    fn source(root: &Path, record_type: RecordType, record_path: &str) -> ArchiveSource {
        ArchiveSource {
            record_type,
            record_path: record_path.to_owned(),
            source_path: root.join(record_path),
        }
    }

    fn capture(payload_kind: PayloadKind) -> ArchiveCapture {
        let core_id = Uuid::new_v4();
        let owner_id = Uuid::new_v4();
        match payload_kind {
            PayloadKind::Full => ArchiveCapture::full(core_id, owner_id, 7, 11),
            PayloadKind::Soul => ArchiveCapture::soul(core_id, owner_id, 7),
            PayloadKind::Fs => ArchiveCapture::fs(core_id, owner_id, 11),
        }
    }

    fn single(payload_kind: PayloadKind) -> ArchiveWriteOptions {
        ArchiveWriteOptions::single(payload_kind, capture(payload_kind))
    }

    fn full_fixture() -> (tempfile::TempDir, Vec<ArchiveSource>) {
        let root = tempfile::tempdir().unwrap();
        write_file(&root.path().join("manifest.json"), br#"{"version":1}"#);
        write_file(&root.path().join("soul/soul.db"), b"encrypted soul bytes");
        write_file(&root.path().join("fs/HEAD"), b"authenticated head bytes");
        write_file(
            &root.path().join("fs/catalogs/catalog-1.acore"),
            b"encrypted catalog bytes",
        );
        write_file(
            &root.path().join("objects/object-1.acore"),
            b"encrypted object bytes",
        );
        write_file(
            &root.path().join("keyslots/root-keyslots.json"),
            b"scoped wrapped keyslots",
        );
        let sources = vec![
            source(root.path(), RecordType::Manifest, "manifest.json"),
            source(root.path(), RecordType::SoulDatabase, "soul/soul.db"),
            source(root.path(), RecordType::Catalog, "fs/HEAD"),
            source(
                root.path(),
                RecordType::Catalog,
                "fs/catalogs/catalog-1.acore",
            ),
            source(root.path(), RecordType::Object, "objects/object-1.acore"),
            source(
                root.path(),
                RecordType::Keyslots,
                "keyslots/root-keyslots.json",
            ),
        ];
        (root, sources)
    }

    #[test]
    fn v2_roundtrip_streams_binary_larger_than_legacy_section_limit() {
        let root = tempfile::tempdir().unwrap();
        write_file(&root.path().join("manifest.json"), br#"{"version":1}"#);
        write_file(&root.path().join("soul/soul.db"), b"encrypted soul bytes");
        write_file(&root.path().join("fs/HEAD"), b"authenticated head bytes");
        write_file(
            &root.path().join("fs/catalogs/catalog-1.acore"),
            b"encrypted catalog bytes",
        );
        write_file(
            &root.path().join("keyslots/root-keyslots.json"),
            b"scoped wrapped keyslots",
        );
        let object_path = root.path().join("objects/large-object.acore");
        std::fs::create_dir_all(object_path.parent().unwrap()).unwrap();
        let mut object = File::create(&object_path).unwrap();
        let block = vec![0x5a; 1024 * 1024];
        for _ in 0..17 {
            object.write_all(&block).unwrap();
        }
        object.sync_all().unwrap();
        drop(object);

        let sources = vec![
            source(root.path(), RecordType::Manifest, "manifest.json"),
            source(root.path(), RecordType::SoulDatabase, "soul/soul.db"),
            source(root.path(), RecordType::Catalog, "fs/HEAD"),
            source(
                root.path(),
                RecordType::Catalog,
                "fs/catalogs/catalog-1.acore",
            ),
            source(
                root.path(),
                RecordType::Object,
                "objects/large-object.acore",
            ),
            source(
                root.path(),
                RecordType::Keyslots,
                "keyslots/root-keyslots.json",
            ),
        ];
        let options = single(PayloadKind::Full);
        let mut archive = tempfile::tempfile().unwrap();
        let written = write_archive(&mut archive, PASSPHRASE, &options, sources).unwrap();
        assert_eq!(written.chunk_count, 8);
        assert!(written.plaintext_bytes > 16 * 1024 * 1024);
        assert!(written.max_buffer_bytes <= MAX_STREAMING_WORKING_BYTES);

        archive.seek(SeekFrom::Start(0)).unwrap();
        let destination = tempfile::tempdir().unwrap();
        let staging = destination.path().join("staging");
        let extracted = extract_archive(&mut archive, PASSPHRASE, &staging).unwrap();
        assert_eq!(extracted.summary, written);
        assert!(extracted.summary.max_buffer_bytes <= MAX_STREAMING_WORKING_BYTES);
        assert_eq!(
            std::fs::metadata(staging.join("objects/large-object.acore"))
                .unwrap()
                .len(),
            17 * 1024 * 1024
        );
        assert_eq!(
            std::fs::read(staging.join("soul/soul.db")).unwrap(),
            b"encrypted soul bytes"
        );
    }

    #[test]
    fn header_bounds_fail_before_password_work_and_unknown_fields_fail_closed() {
        let mut invalid = vec![0u8; usize::from(HEADER_LENGTH)];
        invalid[..8].copy_from_slice(MAGIC);
        invalid[8..10].copy_from_slice(&FORMAT_VERSION.to_be_bytes());
        invalid[10..12].copy_from_slice(&HEADER_LENGTH.to_be_bytes());
        invalid[12] = CIPHER_ID_AES_256_GCM;
        invalid[13] = KDF_ID_ARGON2ID_HKDF_SHA256;
        invalid[14] = KDF_PROFILE_ID_V2;
        invalid[15..19].copy_from_slice(&KDF_TIME_COST.to_be_bytes());
        invalid[19..23].copy_from_slice(&(KDF_MEMORY_KIB + 1).to_be_bytes());
        invalid[23..27].copy_from_slice(&KDF_PARALLELISM.to_be_bytes());
        let destination = tempfile::tempdir().unwrap();
        let error = extract_archive(
            &mut io::Cursor::new(invalid),
            PASSPHRASE,
            &destination.path().join("staging"),
        )
        .unwrap_err();
        assert!(matches!(
            error,
            CoreArchiveError::InvalidHeader("KDF costs")
        ));
    }

    #[test]
    fn scoped_payload_allowlists_reject_cross_compartment_records() {
        let (root, full) = full_fixture();
        let soul_error = write_archive(
            &mut io::sink(),
            PASSPHRASE,
            &single(PayloadKind::Soul),
            full.clone(),
        )
        .unwrap_err();
        assert!(matches!(
            soul_error,
            CoreArchiveError::RecordNotAllowed(RecordType::Catalog)
        ));

        let fs_sources = vec![
            source(root.path(), RecordType::Manifest, "manifest.json"),
            source(root.path(), RecordType::SoulDatabase, "soul/soul.db"),
            source(
                root.path(),
                RecordType::Catalog,
                "fs/catalogs/catalog-1.acore",
            ),
        ];
        let fs_error = write_archive(
            &mut io::sink(),
            PASSPHRASE,
            &single(PayloadKind::Fs),
            fs_sources,
        )
        .unwrap_err();
        assert!(matches!(
            fs_error,
            CoreArchiveError::RecordNotAllowed(RecordType::SoulDatabase)
        ));
    }

    #[test]
    fn record_paths_and_payload_completeness_are_closed() {
        let (root, _) = full_fixture();
        for invalid in ["../manifest.json", "/manifest.json", "a//b", "a\\b"] {
            let sources = vec![ArchiveSource {
                record_type: RecordType::Manifest,
                record_path: invalid.to_owned(),
                source_path: root.path().join("manifest.json"),
            }];
            assert!(matches!(
                write_archive(
                    &mut io::sink(),
                    PASSPHRASE,
                    &single(PayloadKind::Soul),
                    sources,
                ),
                Err(CoreArchiveError::InvalidRecord("record path"))
            ));
        }
        let incomplete = vec![source(root.path(), RecordType::Manifest, "manifest.json")];
        assert!(matches!(
            write_archive(
                &mut io::sink(),
                PASSPHRASE,
                &single(PayloadKind::Full),
                incomplete,
            ),
            Err(CoreArchiveError::InvalidManifest("payload completeness"))
        ));
    }

    #[test]
    fn aad_is_length_delimited_and_binds_every_normative_chunk_coordinate() {
        let descriptor = RecordDescriptor {
            record_type: RecordType::Object,
            record_path: "objects/a.acore".to_owned(),
            record_ordinal: 2,
            record_hash: "ab".repeat(32),
            plaintext_length: 9,
            chunk_count: 1,
        };
        let archive_id = Uuid::new_v4();
        let manifest = ArchiveManifest {
            version: 1,
            archive_id,
            volume_set_id: archive_id,
            payload_kind: PayloadKind::Full,
            volume_mode: VolumeMode::Single,
            declared_volume_count: 1,
            volume_ordinal: 0,
            core_id: Uuid::new_v4(),
            owner_id: Uuid::new_v4(),
            soul_generation: Some(7),
            filesystem_generation: Some(11),
            expected_record_count: 1,
            expected_chunk_count: 1,
            expected_plaintext_bytes: 9,
            header_hash: "cd".repeat(32),
            records: vec![descriptor.clone()],
        };
        let header_hash = [0xcd; 32];
        let record_hash = [0xab; 32];
        let baseline = chunk_aad(
            &header_hash,
            &manifest,
            &descriptor,
            &record_hash,
            0,
            0,
            9,
            25,
        )
        .unwrap();
        let mut changed = descriptor.clone();
        changed.record_path = "objects/b.acore".to_owned();
        assert_ne!(
            baseline,
            chunk_aad(&header_hash, &manifest, &changed, &record_hash, 0, 0, 9, 25,).unwrap()
        );
        assert_ne!(
            baseline,
            chunk_aad(
                &header_hash,
                &manifest,
                &descriptor,
                &record_hash,
                0,
                1,
                9,
                25,
            )
            .unwrap()
        );
        assert_ne!(nonce(&[1, 2, 3, 4], 1), nonce(&[1, 2, 3, 4], 2));
    }

    #[test]
    fn tampered_chunk_and_appended_bytes_never_extract_as_complete() {
        let (_root, sources) = full_fixture();
        let options = single(PayloadKind::Full);
        let mut archive = Vec::new();
        write_archive(&mut archive, PASSPHRASE, &options, sources).unwrap();
        let manifest_length_offset = usize::from(HEADER_LENGTH);
        let manifest_length = u64::from_be_bytes(
            archive[manifest_length_offset..manifest_length_offset + 8]
                .try_into()
                .unwrap(),
        ) as usize;
        let first_chunk_length_offset = manifest_length_offset + 8 + manifest_length;
        let first_chunk_start = first_chunk_length_offset + 8;
        archive[first_chunk_start] ^= 0x80;
        let tampered_destination = tempfile::tempdir().unwrap();
        let tampered_staging = tampered_destination.path().join("staging");
        assert!(matches!(
            extract_archive(
                &mut io::Cursor::new(&archive),
                PASSPHRASE,
                &tampered_staging,
            ),
            Err(CoreArchiveError::Crypto)
        ));
        assert!(!tampered_staging.exists());

        let (_root, sources) = full_fixture();
        let mut appended = Vec::new();
        write_archive(&mut appended, PASSPHRASE, &options, sources).unwrap();
        appended.push(0);
        let appended_destination = tempfile::tempdir().unwrap();
        let appended_staging = appended_destination.path().join("staging");
        assert!(matches!(
            extract_archive(
                &mut io::Cursor::new(&appended),
                PASSPHRASE,
                &appended_staging,
            ),
            Err(CoreArchiveError::InvalidManifest("appended data"))
        ));
        assert!(!appended_staging.exists());
    }
}
