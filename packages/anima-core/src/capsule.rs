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

use std::collections::HashMap;
use std::io::{self, Cursor, Read, Write};

use serde::{Deserialize, Serialize};

use crate::Result;

/// Magic bytes identifying an `.anima` capsule.
pub const MAGIC: &[u8; 4] = b"ANMA";

/// Current format version.
pub const FORMAT_VERSION: u8 = 1;

/// Section type identifiers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[repr(u8)]
pub enum SectionKind {
    Frames = 0,
    Cards = 1,
    Graph = 2,
    Metadata = 3,
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
    zstd::decode_all(Cursor::new(data)).map_err(|e| crate::Error::Io(e.to_string()))
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
            let mut compressed = compress(data)?;

            // Encrypt if password set
            #[cfg(feature = "encryption")]
            if let Some(ref password) = self.password {
                compressed = crypto::encrypt(&compressed, password)?;
            }

            let hash = checksum(&compressed);
            let size = compressed.len() as u32;

            entries.push(SectionEntry {
                kind,
                offset: current_offset,
                size,
                checksum: hash,
            });

            current_offset += size;
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
        for _ in 0..header.section_count {
            entries.push(SectionEntry::read_from(&mut cursor)?);
        }

        let data_start = cursor.position() as usize;

        Ok(Self {
            header,
            entries,
            data_start,
            raw,
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

        let start = self.data_start + entry.offset as usize;
        let end = start + entry.size as usize;

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

        let mut data = section_data.to_vec();

        // Decrypt if needed
        #[cfg(feature = "encryption")]
        if self.header.is_encrypted() {
            if let Some(ref password) = self.password {
                data = crypto::decrypt(&data, password)?;
            }
        }

        #[cfg(not(feature = "encryption"))]
        if self.header.is_encrypted() {
            return Err(crate::Error::Capsule(
                "capsule is encrypted but encryption feature not enabled".into(),
            ));
        }

        decompress(&data)
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
