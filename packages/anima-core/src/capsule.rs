//! Portable `.anima` capsule format for soul export/import.
//!
//! Binary format with:
//! - Magic header + version
//! - Section directory (frames, cards, graph, metadata)
//! - Zstd-compressed sections
//! - Optional AES-256-GCM encryption (Argon2id KDF: time=4, mem=128MiB)
//! - BLAKE3 checksums per section + footer
//!
//! This replaces PG dump / SQLCipher file copy with a clean, portable format.

use std::collections::{HashMap, HashSet};
use std::io::{self, Cursor, Read, Write};

use serde::{Deserialize, Serialize};

use crate::integrity::IntegritySeverity;
use crate::Result;

/// Magic bytes identifying an `.anima` capsule.
pub const MAGIC: &[u8; 4] = b"ANMA";

/// Current format version.
pub const FORMAT_VERSION: u8 = 1;
const MAX_DECOMPRESSED_SECTION_SIZE: usize = 16 * 1024 * 1024;

/// Section type identifiers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[repr(u8)]
pub enum SectionKind {
    Frames = 0,
    Cards = 1,
    Graph = 2,
    Metadata = 3,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SectionStorageClass {
    Canonical,
    Derived,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SectionManifestEntry {
    pub kind: SectionKind,
    pub storage_class: SectionStorageClass,
}

#[must_use]
pub fn section_storage_class(kind: SectionKind) -> SectionStorageClass {
    match kind {
        SectionKind::Frames => SectionStorageClass::Canonical,
        SectionKind::Cards | SectionKind::Graph | SectionKind::Metadata => {
            SectionStorageClass::Derived
        }
    }
}

#[must_use]
pub fn section_manifest(sections: &[SectionKind]) -> Vec<SectionManifestEntry> {
    sections
        .iter()
        .copied()
        .map(|kind| SectionManifestEntry {
            kind,
            storage_class: section_storage_class(kind),
        })
        .collect()
}

/// Public section metadata exposed by capsule verification.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapsuleSectionInfo {
    pub kind: SectionKind,
    pub offset: u32,
    pub size: u32,
    pub encrypted: bool,
}

/// Explicit capsule verification issue kinds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapsuleVerificationIssueKind {
    CapsuleTooSmall,
    SectionTooLarge,
    SectionOffsetOverflow,
    DuplicateSectionKind,
    InvalidMagic,
    HeaderReadFailed,
    UnsupportedVersion,
    MissingPassword,
    DirectoryReadFailed,
    FooterChecksumMismatch,
    SectionChecksumMismatch,
    SectionOutOfBounds,
    SectionDecryptionFailed,
    SectionDecompressionFailed,
}

/// Explicit verification issue surfaced to hosts.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapsuleVerificationIssue {
    pub kind: CapsuleVerificationIssueKind,
    pub severity: IntegritySeverity,
    pub message: String,
    pub section: Option<SectionKind>,
}

/// Structured report for capsule verification.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapsuleVerificationReport {
    pub ok: bool,
    pub version: u8,
    pub encrypted: bool,
    pub sections: Vec<CapsuleSectionInfo>,
    pub issues: Vec<CapsuleVerificationIssue>,
}

impl SectionKind {
    fn from_u8(v: u8) -> Option<Self> {
        match v {
            0 => Some(Self::Frames),
            1 => Some(Self::Cards),
            2 => Some(Self::Graph),
            3 => Some(Self::Metadata),
            _ => None,
        }
    }
}

/// Header of an `.anima` capsule (16 bytes fixed).
#[derive(Debug, Clone)]
pub struct CapsuleHeader {
    /// Magic bytes: b"ANMA"
    pub magic: [u8; 4],
    /// Format version
    pub version: u8,
    /// Number of sections
    pub section_count: u8,
    /// Flags: bit 0 = encrypted
    pub flags: u16,
    /// Reserved for future use
    pub reserved: [u8; 8],
}

impl CapsuleHeader {
    fn new(section_count: u8, encrypted: bool) -> Self {
        Self {
            magic: *MAGIC,
            version: FORMAT_VERSION,
            section_count,
            flags: if encrypted { 1 } else { 0 },
            reserved: [0u8; 8],
        }
    }

    fn is_encrypted(&self) -> bool {
        self.flags & 1 != 0
    }

    fn write_to(&self, w: &mut impl Write) -> io::Result<()> {
        w.write_all(&self.magic)?;
        w.write_all(&[self.version])?;
        w.write_all(&[self.section_count])?;
        w.write_all(&self.flags.to_le_bytes())?;
        w.write_all(&self.reserved)?;
        Ok(())
    }

    fn read_from(r: &mut impl Read) -> crate::Result<Self> {
        let mut magic = [0u8; 4];
        r.read_exact(&mut magic)
            .map_err(|e| crate::Error::Io(e.to_string()))?;
        if &magic != MAGIC {
            return Err(crate::Error::Capsule("invalid magic bytes".into()));
        }

        let mut buf1 = [0u8; 1];
        r.read_exact(&mut buf1)
            .map_err(|e| crate::Error::Io(e.to_string()))?;
        let version = buf1[0];

        r.read_exact(&mut buf1)
            .map_err(|e| crate::Error::Io(e.to_string()))?;
        let section_count = buf1[0];

        let mut buf2 = [0u8; 2];
        r.read_exact(&mut buf2)
            .map_err(|e| crate::Error::Io(e.to_string()))?;
        let flags = u16::from_le_bytes(buf2);

        let mut reserved = [0u8; 8];
        r.read_exact(&mut reserved)
            .map_err(|e| crate::Error::Io(e.to_string()))?;

        Ok(Self {
            magic,
            version,
            section_count,
            flags,
            reserved,
        })
    }
}

/// Directory entry for a section (12 bytes).
#[derive(Debug, Clone)]
pub struct SectionEntry {
    pub kind: SectionKind,
    /// Offset from start of data area.
    pub offset: u32,
    /// Compressed size.
    pub size: u32,
    /// BLAKE3 checksum of compressed data.
    pub checksum: [u8; 32],
}

impl SectionEntry {
    fn write_to(&self, w: &mut impl Write) -> io::Result<()> {
        w.write_all(&[self.kind as u8])?;
        w.write_all(&[0u8; 3])?; // padding
        w.write_all(&self.offset.to_le_bytes())?;
        w.write_all(&self.size.to_le_bytes())?;
        w.write_all(&self.checksum)?;
        Ok(())
    }

    fn read_from(r: &mut impl Read) -> crate::Result<Self> {
        let mut kind_buf = [0u8; 1];
        r.read_exact(&mut kind_buf)
            .map_err(|e| crate::Error::Io(e.to_string()))?;
        let kind = SectionKind::from_u8(kind_buf[0]).ok_or_else(|| {
            crate::Error::Capsule(format!("unknown section kind: {}", kind_buf[0]))
        })?;

        let mut pad = [0u8; 3];
        r.read_exact(&mut pad)
            .map_err(|e| crate::Error::Io(e.to_string()))?;

        let mut buf4 = [0u8; 4];
        r.read_exact(&mut buf4)
            .map_err(|e| crate::Error::Io(e.to_string()))?;
        let offset = u32::from_le_bytes(buf4);

        r.read_exact(&mut buf4)
            .map_err(|e| crate::Error::Io(e.to_string()))?;
        let size = u32::from_le_bytes(buf4);

        let mut checksum = [0u8; 32];
        r.read_exact(&mut checksum)
            .map_err(|e| crate::Error::Io(e.to_string()))?;

        Ok(Self {
            kind,
            offset,
            size,
            checksum,
        })
    }
}

/// Compress data with Zstd (level 3 — good balance).
fn compress(data: &[u8]) -> crate::Result<Vec<u8>> {
    zstd::encode_all(Cursor::new(data), 3).map_err(|e| crate::Error::Io(e.to_string()))
}

/// Decompress Zstd data.
fn decompress(data: &[u8]) -> crate::Result<Vec<u8>> {
    let decoder =
        zstd::stream::read::Decoder::new(Cursor::new(data)).map_err(|e| crate::Error::Io(e.to_string()))?;
    let mut limited = decoder.take((MAX_DECOMPRESSED_SECTION_SIZE + 1) as u64);
    let mut output = Vec::new();
    limited
        .read_to_end(&mut output)
        .map_err(|e| crate::Error::Io(e.to_string()))?;
    if output.len() > MAX_DECOMPRESSED_SECTION_SIZE {
        return Err(crate::Error::Capsule(format!(
            "decompressed section exceeds {} bytes",
            MAX_DECOMPRESSED_SECTION_SIZE
        )));
    }
    Ok(output)
}

/// BLAKE3 hash of data.
fn checksum(data: &[u8]) -> [u8; 32] {
    blake3::hash(data).into()
}

/// Encryption support (AES-256-GCM with Argon2id KDF).
#[cfg(feature = "encryption")]
mod crypto {
    use aes_gcm::aead::AeadCore;
    use aes_gcm::{
        aead::{Aead, OsRng},
        Aes256Gcm, KeyInit, Nonce,
    };
    use argon2::Argon2;

    /// Derive a 256-bit key from password using Argon2id.
    /// Params match animaOS: time=4, mem=128MiB, parallelism=2.
    pub fn derive_key(password: &[u8], salt: &[u8; 16]) -> crate::Result<[u8; 32]> {
        let params = argon2::Params::new(128 * 1024, 4, 2, Some(32))
            .map_err(|e| crate::Error::Encryption(e.to_string()))?;
        let argon2 = Argon2::new(argon2::Algorithm::Argon2id, argon2::Version::V0x13, params);

        let mut key = [0u8; 32];
        argon2
            .hash_password_into(password, salt, &mut key)
            .map_err(|e| crate::Error::Encryption(e.to_string()))?;
        Ok(key)
    }

    /// Encrypt data with AES-256-GCM. Returns: salt (16) + nonce (12) + ciphertext.
    pub fn encrypt(data: &[u8], password: &[u8]) -> crate::Result<Vec<u8>> {
        use aes_gcm::aead::rand_core::RngCore;

        let mut salt = [0u8; 16];
        OsRng.fill_bytes(&mut salt);

        let key = derive_key(password, &salt)?;
        let cipher =
            Aes256Gcm::new_from_slice(&key).map_err(|e| crate::Error::Encryption(e.to_string()))?;

        let nonce = Aes256Gcm::generate_nonce(&mut OsRng);
        let ciphertext = cipher
            .encrypt(&nonce, data)
            .map_err(|e| crate::Error::Encryption(e.to_string()))?;

        let mut output = Vec::with_capacity(16 + 12 + ciphertext.len());
        output.extend_from_slice(&salt);
        output.extend_from_slice(nonce.as_slice());
        output.extend_from_slice(&ciphertext);
        Ok(output)
    }

    /// Decrypt data. Input format: salt (16) + nonce (12) + ciphertext.
    pub fn decrypt(data: &[u8], password: &[u8]) -> crate::Result<Vec<u8>> {
        if data.len() < 28 {
            return Err(crate::Error::Encryption("data too short".into()));
        }

        let salt: [u8; 16] = data[..16]
            .try_into()
            .map_err(|_| crate::Error::Encryption("invalid salt".into()))?;
        let nonce_bytes: [u8; 12] = data[16..28]
            .try_into()
            .map_err(|_| crate::Error::Encryption("invalid nonce".into()))?;
        let ciphertext = &data[28..];

        let key = derive_key(password, &salt)?;
        let cipher =
            Aes256Gcm::new_from_slice(&key).map_err(|e| crate::Error::Encryption(e.to_string()))?;
        let nonce = Nonce::from_slice(&nonce_bytes);

        cipher
            .decrypt(nonce, ciphertext)
            .map_err(|e| crate::Error::Encryption(e.to_string()))
    }
}

/// Builder for writing `.anima` capsules.
pub struct CapsuleWriter {
    sections: HashMap<SectionKind, Vec<u8>>,
    password: Option<Vec<u8>>,
}

impl CapsuleWriter {
    pub fn new() -> Self {
        Self {
            sections: HashMap::new(),
            password: None,
        }
    }

    /// Enable encryption with a password.
    #[cfg(feature = "encryption")]
    pub fn with_password(mut self, password: impl AsRef<[u8]>) -> Self {
        self.password = Some(password.as_ref().to_vec());
        self
    }

    /// Add a section of raw (uncompressed) data.
    pub fn add_section(&mut self, kind: SectionKind, data: Vec<u8>) {
        self.sections.insert(kind, data);
    }

    /// Write the capsule to a byte vector.
    pub fn write(self) -> Result<Vec<u8>> {
        let encrypted = self.password.is_some();
        let section_count = self.sections.len() as u8;

        let header = CapsuleHeader::new(section_count, encrypted);

        // Compress each section and build directory entries
        let mut compressed_sections = Vec::new();
        let mut entries = Vec::new();
        let mut current_offset: u32 = 0;

        // Sort by SectionKind for deterministic output
        let mut section_keys: Vec<SectionKind> = self.sections.keys().copied().collect();
        section_keys.sort_by_key(|k| *k as u8);

        for kind in section_keys {
            let data = &self.sections[&kind];
            let compressed = compress(data)?;

            // Encrypt if password set
            #[cfg(feature = "encryption")]
            let compressed = if let Some(ref password) = self.password {
                crypto::encrypt(&compressed, password)?
            } else {
                compressed
            };

            let hash = checksum(&compressed);
            let size = u32::try_from(compressed.len())
                .map_err(|_| crate::Error::Capsule(format!("section {:?} exceeds 4 GiB", kind)))?;

            entries.push(SectionEntry {
                kind,
                offset: current_offset,
                size,
                checksum: hash,
            });

            current_offset = current_offset.checked_add(size).ok_or_else(|| {
                crate::Error::Capsule("capsule section offsets exceed 4 GiB".into())
            })?;
            compressed_sections.push(compressed);
        }

        // Write everything
        let mut output = Vec::new();
        header
            .write_to(&mut output)
            .map_err(|e| crate::Error::Io(e.to_string()))?;

        for entry in &entries {
            entry
                .write_to(&mut output)
                .map_err(|e| crate::Error::Io(e.to_string()))?;
        }

        for section in compressed_sections {
            output.extend_from_slice(&section);
        }

        // Footer: BLAKE3 of entire file so far
        let footer_hash = checksum(&output);
        output.extend_from_slice(&footer_hash);

        Ok(output)
    }
}

impl Default for CapsuleWriter {
    fn default() -> Self {
        Self::new()
    }
}

/// Reader for `.anima` capsules.
pub struct CapsuleReader {
    header: CapsuleHeader,
    entries: Vec<SectionEntry>,
    data_start: usize,
    raw: Vec<u8>,
    #[cfg(feature = "encryption")]
    password: Option<Vec<u8>>,
}

impl CapsuleReader {
    /// Open a capsule from raw bytes.
    pub fn open(raw: Vec<u8>, password: Option<&[u8]>) -> Result<Self> {
        if raw.len() < 16 + 32 {
            return Err(crate::Error::Capsule("capsule too small".into()));
        }

        // Verify footer checksum
        let footer_start = raw.len() - 32;
        let expected_hash: [u8; 32] = raw[footer_start..]
            .try_into()
            .map_err(|_| crate::Error::Capsule("invalid footer checksum".into()))?;
        let actual_hash = checksum(&raw[..footer_start]);
        if expected_hash != actual_hash {
            return Err(crate::Error::Capsule("footer checksum mismatch".into()));
        }

        let mut cursor = Cursor::new(&raw[..]);
        let header = CapsuleHeader::read_from(&mut cursor)?;

        if header.version > FORMAT_VERSION {
            return Err(crate::Error::Capsule(format!(
                "unsupported version: {} (max: {})",
                header.version, FORMAT_VERSION
            )));
        }

        if header.is_encrypted() && password.is_none() {
            return Err(crate::Error::Capsule(
                "capsule is encrypted but no password provided".into(),
            ));
        }

        let mut entries = Vec::new();
        let mut seen_kinds = HashSet::new();
        for _ in 0..header.section_count {
            let entry = SectionEntry::read_from(&mut cursor)?;
            if !seen_kinds.insert(entry.kind) {
                return Err(crate::Error::Capsule(format!(
                    "duplicate section kind: {:?}",
                    entry.kind
                )));
            }
            entries.push(entry);
        }

        let data_start = cursor.position() as usize;

        Ok(Self {
            header,
            entries,
            data_start,
            raw,
            #[cfg(feature = "encryption")]
            password: password.map(|p| p.to_vec()),
        })
    }

    /// Read and decompress a section by kind.
    pub fn read_section(&self, kind: SectionKind) -> Result<Vec<u8>> {
        let entry = self
            .entries
            .iter()
            .find(|e| e.kind == kind)
            .ok_or_else(|| crate::Error::Capsule(format!("section {:?} not found", kind)))?;

        let start = self
            .data_start
            .checked_add(entry.offset as usize)
            .ok_or_else(|| crate::Error::Capsule("section offset overflow".into()))?;
        let end = start
            .checked_add(entry.size as usize)
            .ok_or_else(|| crate::Error::Capsule("section size overflow".into()))?;

        if end > self.raw.len() - 32 {
            return Err(crate::Error::Capsule(
                "section extends past data area".into(),
            ));
        }

        let section_data = &self.raw[start..end];

        // Verify section checksum
        let actual_hash = checksum(section_data);
        if actual_hash != entry.checksum {
            return Err(crate::Error::Capsule(format!(
                "section {:?} checksum mismatch",
                kind
            )));
        }

        let data = section_data.to_vec();

        // Decrypt if needed
        #[cfg(feature = "encryption")]
        let data = if self.header.is_encrypted() {
            if let Some(ref password) = self.password {
                crypto::decrypt(&data, password)
                    .map_err(|e| crate::Error::Capsule(format!("section {:?} decrypt failed: {e}", kind)))?
            } else {
                data
            }
        } else {
            data
        };

        #[cfg(not(feature = "encryption"))]
        if self.header.is_encrypted() {
            return Err(crate::Error::Capsule(
                "capsule is encrypted but encryption feature not enabled".into(),
            ));
        }

        decompress(&data)
            .map_err(|e| crate::Error::Capsule(format!("section {:?} decompress failed: {e}", kind)))
    }

    /// List available section kinds.
    pub fn sections(&self) -> Vec<SectionKind> {
        self.entries.iter().map(|e| e.kind).collect()
    }

    /// Check if the capsule is encrypted.
    pub fn is_encrypted(&self) -> bool {
        self.header.is_encrypted()
    }

    /// Format version.
    pub fn version(&self) -> u8 {
        self.header.version
    }
}

#[must_use]
pub fn verify_capsule(raw: &[u8], password: Option<&[u8]>) -> CapsuleVerificationReport {
    let mut report = CapsuleVerificationReport::default();

    if raw.len() < 16 + 32 {
        report.issues.push(CapsuleVerificationIssue {
            kind: CapsuleVerificationIssueKind::CapsuleTooSmall,
            severity: IntegritySeverity::Error,
            message: "capsule too small".into(),
            section: None,
        });
        report.ok = false;
        return report;
    }

    let footer_start = raw.len() - 32;
    let expected_hash: [u8; 32] = match raw[footer_start..].try_into() {
        Ok(hash) => hash,
        Err(_) => {
            report.issues.push(CapsuleVerificationIssue {
                kind: CapsuleVerificationIssueKind::FooterChecksumMismatch,
                severity: IntegritySeverity::Error,
                message: "invalid footer checksum".into(),
                section: None,
            });
            report.ok = false;
            return report;
        }
    };

    let actual_hash = checksum(&raw[..footer_start]);
    if expected_hash != actual_hash {
        report.issues.push(CapsuleVerificationIssue {
            kind: CapsuleVerificationIssueKind::FooterChecksumMismatch,
            severity: IntegritySeverity::Error,
            message: "footer checksum mismatch".into(),
            section: None,
        });
    }

    let mut cursor = Cursor::new(raw);
    let header = match CapsuleHeader::read_from(&mut cursor) {
        Ok(header) => header,
        Err(crate::Error::Capsule(message)) if message == "invalid magic bytes" => {
            report.issues.push(CapsuleVerificationIssue {
                kind: CapsuleVerificationIssueKind::InvalidMagic,
                severity: IntegritySeverity::Error,
                message,
                section: None,
            });
            report.ok = false;
            return report;
        }
        Err(err) => {
            report.issues.push(CapsuleVerificationIssue {
                kind: CapsuleVerificationIssueKind::HeaderReadFailed,
                severity: IntegritySeverity::Error,
                message: err.to_string(),
                section: None,
            });
            report.ok = false;
            return report;
        }
    };

    report.version = header.version;
    report.encrypted = header.is_encrypted();

    if header.version > FORMAT_VERSION {
        report.issues.push(CapsuleVerificationIssue {
            kind: CapsuleVerificationIssueKind::UnsupportedVersion,
            severity: IntegritySeverity::Error,
            message: format!(
                "unsupported version: {} (max: {})",
                header.version, FORMAT_VERSION
            ),
            section: None,
        });
    }

    if header.is_encrypted() && password.is_none() {
        report.issues.push(CapsuleVerificationIssue {
            kind: CapsuleVerificationIssueKind::MissingPassword,
            severity: IntegritySeverity::Error,
            message: "capsule is encrypted but no password provided".into(),
            section: None,
        });
    }

    let mut entries = Vec::new();
    let mut seen_kinds = HashSet::new();
    for _ in 0..header.section_count {
        match SectionEntry::read_from(&mut cursor) {
            Ok(entry) => {
                if !seen_kinds.insert(entry.kind) {
                    report.issues.push(CapsuleVerificationIssue {
                        kind: CapsuleVerificationIssueKind::DuplicateSectionKind,
                        severity: IntegritySeverity::Error,
                        message: format!("duplicate section kind: {:?}", entry.kind),
                        section: Some(entry.kind),
                    });
                    continue;
                }
                report.sections.push(CapsuleSectionInfo {
                    kind: entry.kind,
                    offset: entry.offset,
                    size: entry.size,
                    encrypted: header.is_encrypted(),
                });
                entries.push(entry);
            }
            Err(err) => {
                report.issues.push(CapsuleVerificationIssue {
                    kind: CapsuleVerificationIssueKind::DirectoryReadFailed,
                    severity: IntegritySeverity::Error,
                    message: err.to_string(),
                    section: None,
                });
                report.ok = false;
                return report;
            }
        }
    }

    let data_start = cursor.position() as usize;
    for entry in entries {
        let start = match data_start.checked_add(entry.offset as usize) {
            Some(start) => start,
            None => {
                report.issues.push(CapsuleVerificationIssue {
                    kind: CapsuleVerificationIssueKind::SectionOffsetOverflow,
                    severity: IntegritySeverity::Error,
                    message: format!("section {:?} offset overflow", entry.kind),
                    section: Some(entry.kind),
                });
                continue;
            }
        };
        let end = match start.checked_add(entry.size as usize) {
            Some(end) => end,
            None => {
                report.issues.push(CapsuleVerificationIssue {
                    kind: CapsuleVerificationIssueKind::SectionTooLarge,
                    severity: IntegritySeverity::Error,
                    message: format!("section {:?} size overflow", entry.kind),
                    section: Some(entry.kind),
                });
                continue;
            }
        };

        if end > footer_start {
            report.issues.push(CapsuleVerificationIssue {
                kind: CapsuleVerificationIssueKind::SectionOutOfBounds,
                severity: IntegritySeverity::Error,
                message: format!("section {:?} extends past data area", entry.kind),
                section: Some(entry.kind),
            });
            continue;
        }

        let section_data = &raw[start..end];
        if checksum(section_data) != entry.checksum {
            report.issues.push(CapsuleVerificationIssue {
                kind: CapsuleVerificationIssueKind::SectionChecksumMismatch,
                severity: IntegritySeverity::Error,
                message: format!("section {:?} checksum mismatch", entry.kind),
                section: Some(entry.kind),
            });
            continue;
        }

        let data = section_data.to_vec();

        #[cfg(feature = "encryption")]
        let data = if header.is_encrypted() {
            if let Some(password) = password {
                match crypto::decrypt(&data, password) {
                    Ok(data) => data,
                    Err(err) => {
                        report.issues.push(CapsuleVerificationIssue {
                            kind: CapsuleVerificationIssueKind::SectionDecryptionFailed,
                            severity: IntegritySeverity::Error,
                            message: format!("section {:?} decrypt failed: {err}", entry.kind),
                            section: Some(entry.kind),
                        });
                        continue;
                    }
                }
            } else {
                data
            }
        } else {
            data
        };

        #[cfg(not(feature = "encryption"))]
        if header.is_encrypted() {
            report.issues.push(CapsuleVerificationIssue {
                kind: CapsuleVerificationIssueKind::MissingPassword,
                severity: IntegritySeverity::Error,
                message: "capsule is encrypted but encryption feature not enabled".into(),
                section: Some(entry.kind),
            });
            continue;
        }

        if let Err(err) = decompress(&data) {
            report.issues.push(CapsuleVerificationIssue {
                kind: CapsuleVerificationIssueKind::SectionDecompressionFailed,
                severity: IntegritySeverity::Error,
                message: format!("section {:?} decompress failed: {err}", entry.kind),
                section: Some(entry.kind),
            });
        }
    }

    report.ok = report.issues.is_empty();
    report
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_roundtrip_unencrypted() {
        let frames = b"test frames data: hello world with some content";
        let cards = b"test cards data: some memory cards here";

        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, frames.to_vec());
        writer.add_section(SectionKind::Cards, cards.to_vec());

        let capsule = writer.write().unwrap();

        let reader = CapsuleReader::open(capsule, None).unwrap();
        assert!(!reader.is_encrypted());
        assert_eq!(reader.version(), FORMAT_VERSION);
        assert_eq!(reader.sections().len(), 2);

        let restored_frames = reader.read_section(SectionKind::Frames).unwrap();
        let restored_cards = reader.read_section(SectionKind::Cards).unwrap();

        assert_eq!(restored_frames, frames);
        assert_eq!(restored_cards, cards);
    }

    #[test]
    fn test_missing_section() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"data".to_vec());
        let capsule = writer.write().unwrap();

        let reader = CapsuleReader::open(capsule, None).unwrap();
        assert!(reader.read_section(SectionKind::Cards).is_err());
    }

    #[test]
    fn test_tampered_footer() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"data".to_vec());
        let mut capsule = writer.write().unwrap();

        // Tamper with the last byte
        let len = capsule.len();
        capsule[len - 1] ^= 0xFF;

        assert!(CapsuleReader::open(capsule, None).is_err());
    }

    #[test]
    fn test_tampered_section() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"real data here".to_vec());
        let mut capsule = writer.write().unwrap();

        // Tamper with data area (after header + directory, before footer)
        // Header = 16 bytes, 1 entry = 44 bytes, data starts at 60
        // Tamper a byte in the data area
        let data_start = 16 + 44; // header + 1 section entry
        if capsule.len() > data_start + 5 {
            capsule[data_start + 2] ^= 0xFF;

            // Recompute footer so we get past that check
            let footer_start = capsule.len() - 32;
            let new_hash = checksum(&capsule[..footer_start]);
            capsule[footer_start..].copy_from_slice(&new_hash);

            let reader = CapsuleReader::open(capsule, None).unwrap();
            assert!(reader.read_section(SectionKind::Frames).is_err());
        }
    }

    #[test]
    fn verification_report_lists_available_sections() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
        writer.add_section(SectionKind::Metadata, b"meta-data".to_vec());

        let capsule = writer.write().unwrap();
        let report = verify_capsule(&capsule, None);

        assert!(report.ok);
        assert_eq!(report.version, FORMAT_VERSION);
        assert!(!report.encrypted);
        assert_eq!(report.sections.len(), 2);
        assert_eq!(report.sections[0].kind, SectionKind::Frames);
        assert_eq!(report.sections[1].kind, SectionKind::Metadata);
        assert!(report.sections.iter().all(|section| section.size > 0));
        assert!(report.issues.is_empty());
    }

    #[test]
    fn verification_report_surfaces_footer_mismatch_issue() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
        let mut capsule = writer.write().unwrap();

        let footer_index = capsule.len() - 1;
        capsule[footer_index] ^= 0xFF;

        let report = verify_capsule(&capsule, None);

        assert!(!report.ok);
        assert!(report.issues.iter().any(|issue| {
            issue.kind == CapsuleVerificationIssueKind::FooterChecksumMismatch
        }));
    }

    #[test]
    fn verification_report_flags_unreadable_compressed_section() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
        let mut capsule = writer.write().unwrap();

        let data_start = 16 + 44;
        capsule[data_start] ^= 0xFF;
        let footer_start = capsule.len() - 32;
        let section_checksum = checksum(&capsule[data_start..footer_start]);
        let checksum_start = 16 + 12;
        capsule[checksum_start..checksum_start + 32].copy_from_slice(&section_checksum);

        let new_hash = checksum(&capsule[..footer_start]);
        capsule[footer_start..].copy_from_slice(&new_hash);

        let report = verify_capsule(&capsule, None);

        assert!(!report.ok);
        assert!(report.issues.iter().any(|issue| {
            issue.kind == CapsuleVerificationIssueKind::SectionDecompressionFailed
        }));
    }

    #[test]
    fn verification_report_flags_oversized_decompressed_section() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(
            SectionKind::Frames,
            vec![42u8; MAX_DECOMPRESSED_SECTION_SIZE + 1],
        );
        let capsule = writer.write().unwrap();

        let report = verify_capsule(&capsule, None);

        assert!(!report.ok);
        assert!(report.issues.iter().any(|issue| {
            issue.kind == CapsuleVerificationIssueKind::SectionDecompressionFailed
        }));
    }

    #[test]
    fn duplicate_section_kinds_are_rejected() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
        writer.add_section(SectionKind::Metadata, b"meta-data".to_vec());
        let mut capsule = writer.write().unwrap();

        let second_entry_kind_offset = 16 + 44;
        capsule[second_entry_kind_offset] = SectionKind::Frames as u8;

        let footer_start = capsule.len() - 32;
        let new_hash = checksum(&capsule[..footer_start]);
        capsule[footer_start..].copy_from_slice(&new_hash);

        let report = verify_capsule(&capsule, None);
        assert!(!report.ok);
        assert!(report.issues.iter().any(|issue| {
            issue.kind == CapsuleVerificationIssueKind::DuplicateSectionKind
        }));

        assert!(CapsuleReader::open(capsule, None).is_err());
    }

    #[test]
    fn section_manifest_marks_frames_canonical_and_cards_graph_derived() {
        let manifest = section_manifest(&[
            SectionKind::Frames,
            SectionKind::Cards,
            SectionKind::Graph,
            SectionKind::Metadata,
        ]);

        assert_eq!(
            manifest,
            vec![
                SectionManifestEntry {
                    kind: SectionKind::Frames,
                    storage_class: SectionStorageClass::Canonical,
                },
                SectionManifestEntry {
                    kind: SectionKind::Cards,
                    storage_class: SectionStorageClass::Derived,
                },
                SectionManifestEntry {
                    kind: SectionKind::Graph,
                    storage_class: SectionStorageClass::Derived,
                },
                SectionManifestEntry {
                    kind: SectionKind::Metadata,
                    storage_class: SectionStorageClass::Derived,
                },
            ]
        );
    }

    #[test]
    fn test_compressed_size() {
        // Repetitive data should compress well
        let repetitive = vec![42u8; 10_000];
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, repetitive.clone());
        let capsule = writer.write().unwrap();

        // Capsule should be much smaller than raw data
        assert!(capsule.len() < repetitive.len());

        let reader = CapsuleReader::open(capsule, None).unwrap();
        let restored = reader.read_section(SectionKind::Frames).unwrap();
        assert_eq!(restored, repetitive);
    }

    #[cfg(feature = "encryption")]
    #[test]
    fn test_roundtrip_encrypted() {
        let data = b"secret soul data";
        let password = b"strong-password-123";

        let mut writer = CapsuleWriter::new().with_password(password);
        writer.add_section(SectionKind::Frames, data.to_vec());
        let capsule = writer.write().unwrap();

        // Should fail without password
        assert!(CapsuleReader::open(capsule.clone(), None).is_err());

        // Should fail with wrong password
        let reader = CapsuleReader::open(capsule.clone(), Some(b"wrong")).unwrap();
        assert!(reader.read_section(SectionKind::Frames).is_err());

        // Should succeed with correct password
        let reader = CapsuleReader::open(capsule, Some(password)).unwrap();
        assert!(reader.is_encrypted());
        let restored = reader.read_section(SectionKind::Frames).unwrap();
        assert_eq!(restored, data);
    }
}
