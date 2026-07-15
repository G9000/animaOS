//! Streaming, authenticated `.acore` object envelopes.

use std::collections::{BTreeMap, HashSet};
use std::io::{self, Cursor, Read, Write};
use std::ops::Range;

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::crypto::{
    BodyFrameAad, CryptoError, MetadataFrameAad, ObjectBaseAad, SecretBytes, NONCE_LENGTH,
};

pub const ENVELOPE_VERSION: u16 = 1;
pub const CIPHER_AES_256_GCM: u8 = 1;
pub const CHUNKING_FIXED_V1: u8 = 1;
pub const METADATA_SCHEMA_VERSION: u16 = 1;
pub const BODY_CHUNK_PLAINTEXT_SIZE: usize = 4 * 1024 * 1024;
pub const MAX_BODY_CHUNKS: usize = 2_048;
pub const MAX_BODY_LENGTH: u64 = BODY_CHUNK_PLAINTEXT_SIZE as u64 * MAX_BODY_CHUNKS as u64;
pub const MAX_METADATA_PLAINTEXT_SIZE: usize = 1024 * 1024;
pub const ENVELOPE_HEADER_SIZE: usize = 32;

const MAGIC: &[u8; 8] = b"ACOREV1\0";
const FRAME_HEADER_SIZE: usize = 32;
const TAG_LENGTH: usize = 16;

#[derive(Debug, thiserror::Error)]
pub enum EnvelopeError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("cryptographic validation failed: {0}")]
    Crypto(#[from] CryptoError),
    #[error("invalid envelope: {0}")]
    InvalidFormat(&'static str),
    #[error("unsupported envelope parameter: {0}")]
    Unsupported(&'static str),
    #[error("envelope limit exceeded: {0}")]
    LimitExceeded(&'static str),
    #[error("invalid metadata JSON: {0}")]
    Json(#[from] serde_json::Error),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EnvelopeMetadata {
    pub schema_version: u16,
    pub kind: String,
    pub object_id: String,
    pub revision: u64,
    pub created_at: String,
    pub updated_at: String,
    pub content_type: String,
    pub metadata: BTreeMap<String, Value>,
    pub body_encoding: String,
    pub body_length: u64,
    pub body_sha256: String,
    pub chunk_plaintext_size: u32,
    pub chunk_count: u32,
}

impl EnvelopeMetadata {
    #[allow(clippy::too_many_arguments)]
    pub fn for_body(
        kind: impl Into<String>,
        object_id: impl Into<String>,
        revision: u64,
        created_at: impl Into<String>,
        updated_at: impl Into<String>,
        content_type: impl Into<String>,
        metadata: BTreeMap<String, Value>,
        body_encoding: impl Into<String>,
        body: &[u8],
    ) -> Result<Self, EnvelopeError> {
        let body_length =
            u64::try_from(body.len()).map_err(|_| EnvelopeError::LimitExceeded("body length"))?;
        let chunk_count = expected_chunk_count(body_length)?;
        let mut value = Self {
            schema_version: METADATA_SCHEMA_VERSION,
            kind: kind.into(),
            object_id: object_id.into(),
            revision,
            created_at: created_at.into(),
            updated_at: updated_at.into(),
            content_type: content_type.into(),
            metadata,
            body_encoding: body_encoding.into(),
            body_length,
            body_sha256: hex_digest(body),
            chunk_plaintext_size: BODY_CHUNK_PLAINTEXT_SIZE as u32,
            chunk_count,
        };
        canonicalize_map(&mut value.metadata);
        value.validate_shape()?;
        Ok(value)
    }

    fn validate_shape(&self) -> Result<(), EnvelopeError> {
        if self.schema_version != METADATA_SCHEMA_VERSION {
            return Err(EnvelopeError::Unsupported("metadata schema version"));
        }
        if self.object_id.is_empty()
            || self.kind.is_empty()
            || self.created_at.is_empty()
            || self.updated_at.is_empty()
            || self.content_type.is_empty()
            || self.body_encoding.is_empty()
            || self.revision == 0
        {
            return Err(EnvelopeError::InvalidFormat("incomplete metadata"));
        }
        if self.body_length > MAX_BODY_LENGTH {
            return Err(EnvelopeError::LimitExceeded("body length"));
        }
        if self.chunk_plaintext_size != BODY_CHUNK_PLAINTEXT_SIZE as u32 {
            return Err(EnvelopeError::Unsupported("chunk plaintext size"));
        }
        if self.chunk_count != expected_chunk_count(self.body_length)? {
            return Err(EnvelopeError::InvalidFormat("metadata chunk count"));
        }
        parse_sha256(&self.body_sha256)?;
        Ok(())
    }

    fn validate_for_aad(&self, aad: &ObjectBaseAad) -> Result<(), EnvelopeError> {
        self.validate_shape()?;
        if self.object_id != aad.object_id()
            || self.revision != aad.revision()
            || self.kind != aad.kind().as_str()
            || aad.envelope_version() != ENVELOPE_VERSION
        {
            return Err(EnvelopeError::InvalidFormat("metadata/AAD mismatch"));
        }
        Ok(())
    }

    fn canonical_bytes(&self) -> Result<Vec<u8>, EnvelopeError> {
        let mut canonical = self.clone();
        canonicalize_map(&mut canonical.metadata);
        canonical.validate_shape()?;
        let bytes = serde_json::to_vec(&canonical)?;
        if bytes.len() > MAX_METADATA_PLAINTEXT_SIZE {
            return Err(EnvelopeError::LimitExceeded("metadata plaintext"));
        }
        Ok(bytes)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EnvelopeRead {
    pub metadata: EnvelopeMetadata,
    pub whole_body_verified: bool,
}

#[derive(Clone, Copy)]
struct Header {
    metadata_ciphertext_length: usize,
    body_length: u64,
    chunk_count: u32,
}

#[derive(Clone, Copy)]
struct FrameHeader {
    nonce: [u8; NONCE_LENGTH],
    index: u32,
    offset: u64,
    plaintext_length: u32,
    final_chunk: bool,
    ciphertext_length: usize,
}

struct MetadataRead {
    header: Header,
    metadata: EnvelopeMetadata,
    frame_hash: [u8; 32],
    cipher: Aes256Gcm,
    nonces: HashSet<[u8; NONCE_LENGTH]>,
}

pub fn write_envelope<W: Write, R: Read>(
    writer: &mut W,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    metadata: &EnvelopeMetadata,
    body: &mut R,
) -> Result<(), EnvelopeError> {
    metadata.validate_for_aad(aad)?;
    let metadata_plaintext = metadata.canonical_bytes()?;
    let metadata_ciphertext_length = metadata_plaintext
        .len()
        .checked_add(TAG_LENGTH)
        .ok_or(EnvelopeError::LimitExceeded("metadata ciphertext"))?;
    let header = Header {
        metadata_ciphertext_length,
        body_length: metadata.body_length,
        chunk_count: metadata.chunk_count,
    };
    write_header(writer, header)?;

    let cipher = cipher(key)?;
    let mut used_nonces = HashSet::new();
    let metadata_nonce = unique_nonce(&mut used_nonces)?;
    let metadata_aad = MetadataFrameAad::new(aad.clone(), CHUNKING_FIXED_V1 as u16)?;
    let metadata_ciphertext = cipher
        .encrypt(
            Nonce::from_slice(&metadata_nonce),
            Payload {
                msg: &metadata_plaintext,
                aad: &metadata_aad.to_bytes(),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    writer.write_all(&metadata_nonce)?;
    writer.write_all(&metadata_ciphertext)?;

    let metadata_frame_hash = metadata_frame_hash(&metadata_nonce, &metadata_ciphertext);
    let mut body_hasher = Sha256::new();
    let mut remaining = metadata.body_length;
    for index in 0..metadata.chunk_count {
        let plaintext_length = remaining.min(BODY_CHUNK_PLAINTEXT_SIZE as u64) as usize;
        let mut plaintext = vec![0_u8; plaintext_length];
        body.read_exact(&mut plaintext)?;
        body_hasher.update(&plaintext);
        let offset = u64::from(index) * BODY_CHUNK_PLAINTEXT_SIZE as u64;
        let final_chunk = index + 1 == metadata.chunk_count;
        let frame_aad = BodyFrameAad::new(
            aad.clone(),
            metadata_frame_hash,
            index,
            metadata.chunk_count,
            offset,
            plaintext_length as u64,
            metadata.body_length,
            final_chunk,
        )?;
        let nonce = unique_nonce(&mut used_nonces)?;
        let ciphertext = cipher
            .encrypt(
                Nonce::from_slice(&nonce),
                Payload {
                    msg: &plaintext,
                    aad: &frame_aad.to_bytes(),
                },
            )
            .map_err(|_| CryptoError::Authentication)?;
        write_frame_header(
            writer,
            FrameHeader {
                nonce,
                index,
                offset,
                plaintext_length: plaintext_length as u32,
                final_chunk,
                ciphertext_length: ciphertext.len(),
            },
        )?;
        writer.write_all(&ciphertext)?;
        remaining -= plaintext_length as u64;
    }
    let mut extra = [0_u8; 1];
    if body.read(&mut extra)? != 0 || remaining != 0 {
        return Err(EnvelopeError::InvalidFormat("body length mismatch"));
    }
    if body_hasher.finalize().as_slice() != parse_sha256(&metadata.body_sha256)? {
        return Err(EnvelopeError::InvalidFormat("body hash mismatch"));
    }
    Ok(())
}

pub fn encode_envelope(
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    metadata: &EnvelopeMetadata,
    body: &[u8],
) -> Result<Vec<u8>, EnvelopeError> {
    let mut output = Vec::new();
    write_envelope(&mut output, key, aad, metadata, &mut Cursor::new(body))?;
    Ok(output)
}

pub fn read_envelope<R: Read, W: Write>(
    reader: &mut R,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    output: &mut W,
) -> Result<EnvelopeRead, EnvelopeError> {
    let mut state = read_metadata(reader, key, aad)?;
    let mut body_hasher = Sha256::new();
    for index in 0..state.header.chunk_count {
        let frame = read_and_validate_frame_header(reader, state.header, index, &mut state.nonces)?;
        let mut ciphertext = vec![0_u8; frame.ciphertext_length];
        reader.read_exact(&mut ciphertext)?;
        let plaintext = decrypt_frame(
            &state.cipher,
            aad,
            state.header,
            state.frame_hash,
            frame,
            &ciphertext,
        )?;
        body_hasher.update(&plaintext);
        output.write_all(&plaintext)?;
    }
    require_eof(reader)?;
    if body_hasher.finalize().as_slice() != parse_sha256(&state.metadata.body_sha256)? {
        return Err(EnvelopeError::InvalidFormat("body hash mismatch"));
    }
    Ok(EnvelopeRead {
        metadata: state.metadata,
        whole_body_verified: true,
    })
}

pub fn decode_envelope(
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    encoded: &[u8],
) -> Result<(EnvelopeRead, Vec<u8>), EnvelopeError> {
    let mut body = Vec::new();
    let result = read_envelope(&mut Cursor::new(encoded), key, aad, &mut body)?;
    Ok((result, body))
}

pub fn read_envelope_range<R: Read, W: Write>(
    reader: &mut R,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    range: Range<u64>,
    output: &mut W,
) -> Result<EnvelopeRead, EnvelopeError> {
    let mut state = read_metadata(reader, key, aad)?;
    if range.start > range.end || range.end > state.header.body_length {
        return Err(EnvelopeError::InvalidFormat("range bounds"));
    }
    let whole_body = range.start == 0 && range.end == state.header.body_length;
    let mut body_hasher = Sha256::new();
    for index in 0..state.header.chunk_count {
        let frame = read_and_validate_frame_header(reader, state.header, index, &mut state.nonces)?;
        let frame_end = frame.offset + u64::from(frame.plaintext_length);
        let intersects = range.start < frame_end && range.end > frame.offset;
        if intersects || whole_body {
            let mut ciphertext = vec![0_u8; frame.ciphertext_length];
            reader.read_exact(&mut ciphertext)?;
            let plaintext = decrypt_frame(
                &state.cipher,
                aad,
                state.header,
                state.frame_hash,
                frame,
                &ciphertext,
            )?;
            if whole_body {
                body_hasher.update(&plaintext);
            }
            if intersects {
                let from = range.start.saturating_sub(frame.offset) as usize;
                let to = (range.end.min(frame_end) - frame.offset) as usize;
                output.write_all(&plaintext[from..to])?;
            }
        } else {
            discard_exact(reader, frame.ciphertext_length)?;
        }
    }
    require_eof(reader)?;
    if whole_body && body_hasher.finalize().as_slice() != parse_sha256(&state.metadata.body_sha256)?
    {
        return Err(EnvelopeError::InvalidFormat("body hash mismatch"));
    }
    Ok(EnvelopeRead {
        metadata: state.metadata,
        whole_body_verified: whole_body,
    })
}

pub fn decode_envelope_range(
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    encoded: &[u8],
    range: Range<u64>,
) -> Result<(EnvelopeRead, Vec<u8>), EnvelopeError> {
    let mut body = Vec::new();
    let result = read_envelope_range(&mut Cursor::new(encoded), key, aad, range, &mut body)?;
    Ok((result, body))
}

fn cipher(key: &SecretBytes) -> Result<Aes256Gcm, EnvelopeError> {
    Aes256Gcm::new_from_slice(key.as_slice())
        .map_err(|_| EnvelopeError::InvalidFormat("invalid object key"))
}

fn read_metadata<R: Read>(
    reader: &mut R,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
) -> Result<MetadataRead, EnvelopeError> {
    let header = read_header(reader)?;
    let mut nonce = [0_u8; NONCE_LENGTH];
    reader.read_exact(&mut nonce)?;
    let mut nonces = HashSet::new();
    nonces.insert(nonce);
    let mut ciphertext = vec![0_u8; header.metadata_ciphertext_length];
    reader.read_exact(&mut ciphertext)?;
    let metadata_hash = metadata_frame_hash(&nonce, &ciphertext);
    let cipher = cipher(key)?;
    let metadata_aad = MetadataFrameAad::new(aad.clone(), CHUNKING_FIXED_V1 as u16)?;
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &ciphertext,
                aad: &metadata_aad.to_bytes(),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    if plaintext.len() > MAX_METADATA_PLAINTEXT_SIZE {
        return Err(EnvelopeError::LimitExceeded("metadata plaintext"));
    }
    let metadata: EnvelopeMetadata = serde_json::from_slice(&plaintext)?;
    metadata.validate_for_aad(aad)?;
    if metadata.body_length != header.body_length
        || metadata.chunk_count != header.chunk_count
        || metadata.chunk_plaintext_size != BODY_CHUNK_PLAINTEXT_SIZE as u32
    {
        return Err(EnvelopeError::InvalidFormat("metadata/header mismatch"));
    }
    if metadata.canonical_bytes()? != plaintext {
        return Err(EnvelopeError::InvalidFormat("non-canonical metadata"));
    }
    Ok(MetadataRead {
        header,
        metadata,
        frame_hash: metadata_hash,
        cipher,
        nonces,
    })
}

fn expected_chunk_count(body_length: u64) -> Result<u32, EnvelopeError> {
    if body_length > MAX_BODY_LENGTH {
        return Err(EnvelopeError::LimitExceeded("body length"));
    }
    if body_length == 0 {
        return Ok(0);
    }
    let count = (body_length - 1) / BODY_CHUNK_PLAINTEXT_SIZE as u64 + 1;
    if count > MAX_BODY_CHUNKS as u64 {
        return Err(EnvelopeError::LimitExceeded("body chunk count"));
    }
    Ok(count as u32)
}

fn write_header<W: Write>(writer: &mut W, header: Header) -> Result<(), EnvelopeError> {
    let mut bytes = [0_u8; ENVELOPE_HEADER_SIZE];
    bytes[..8].copy_from_slice(MAGIC);
    bytes[8..10].copy_from_slice(&ENVELOPE_VERSION.to_le_bytes());
    bytes[10] = CIPHER_AES_256_GCM;
    bytes[11] = CHUNKING_FIXED_V1;
    bytes[12..16].copy_from_slice(&(header.metadata_ciphertext_length as u32).to_le_bytes());
    bytes[16..24].copy_from_slice(&header.body_length.to_le_bytes());
    bytes[24..28].copy_from_slice(&(BODY_CHUNK_PLAINTEXT_SIZE as u32).to_le_bytes());
    bytes[28..32].copy_from_slice(&header.chunk_count.to_le_bytes());
    writer.write_all(&bytes)?;
    Ok(())
}

fn read_header<R: Read>(reader: &mut R) -> Result<Header, EnvelopeError> {
    let mut bytes = [0_u8; ENVELOPE_HEADER_SIZE];
    reader.read_exact(&mut bytes)?;
    if &bytes[..8] != MAGIC {
        return Err(EnvelopeError::InvalidFormat("magic"));
    }
    if u16::from_le_bytes(bytes[8..10].try_into().expect("fixed slice")) != ENVELOPE_VERSION {
        return Err(EnvelopeError::Unsupported("envelope version"));
    }
    if bytes[10] != CIPHER_AES_256_GCM {
        return Err(EnvelopeError::Unsupported("cipher"));
    }
    if bytes[11] != CHUNKING_FIXED_V1 {
        return Err(EnvelopeError::Unsupported("chunking"));
    }
    let metadata_ciphertext_length =
        u32::from_le_bytes(bytes[12..16].try_into().expect("fixed slice")) as usize;
    if !(TAG_LENGTH..=MAX_METADATA_PLAINTEXT_SIZE + TAG_LENGTH)
        .contains(&metadata_ciphertext_length)
    {
        return Err(EnvelopeError::LimitExceeded("metadata ciphertext"));
    }
    let body_length = u64::from_le_bytes(bytes[16..24].try_into().expect("fixed slice"));
    if body_length > MAX_BODY_LENGTH {
        return Err(EnvelopeError::LimitExceeded("body length"));
    }
    let chunk_size = u32::from_le_bytes(bytes[24..28].try_into().expect("fixed slice"));
    if chunk_size != BODY_CHUNK_PLAINTEXT_SIZE as u32 {
        return Err(EnvelopeError::Unsupported("chunk plaintext size"));
    }
    let chunk_count = u32::from_le_bytes(bytes[28..32].try_into().expect("fixed slice"));
    if chunk_count as usize > MAX_BODY_CHUNKS {
        return Err(EnvelopeError::LimitExceeded("body chunk count"));
    }
    if chunk_count != expected_chunk_count(body_length)? {
        return Err(EnvelopeError::InvalidFormat("header chunk count"));
    }
    Ok(Header {
        metadata_ciphertext_length,
        body_length,
        chunk_count,
    })
}

fn write_frame_header<W: Write>(writer: &mut W, frame: FrameHeader) -> Result<(), EnvelopeError> {
    let mut bytes = [0_u8; FRAME_HEADER_SIZE];
    bytes[..12].copy_from_slice(&frame.nonce);
    bytes[12..16].copy_from_slice(&frame.index.to_le_bytes());
    bytes[16..24].copy_from_slice(&frame.offset.to_le_bytes());
    bytes[24..28].copy_from_slice(&frame.plaintext_length.to_le_bytes());
    bytes[28] = u8::from(frame.final_chunk);
    bytes[29..32].copy_from_slice(&(frame.ciphertext_length as u32).to_le_bytes()[..3]);
    writer.write_all(&bytes)?;
    Ok(())
}

fn read_and_validate_frame_header<R: Read>(
    reader: &mut R,
    header: Header,
    expected_index: u32,
    nonces: &mut HashSet<[u8; NONCE_LENGTH]>,
) -> Result<FrameHeader, EnvelopeError> {
    let mut bytes = [0_u8; FRAME_HEADER_SIZE];
    reader.read_exact(&mut bytes)?;
    let nonce: [u8; NONCE_LENGTH] = bytes[..12].try_into().expect("fixed slice");
    if !nonces.insert(nonce) {
        return Err(EnvelopeError::InvalidFormat("repeated frame nonce"));
    }
    let index = u32::from_le_bytes(bytes[12..16].try_into().expect("fixed slice"));
    let offset = u64::from_le_bytes(bytes[16..24].try_into().expect("fixed slice"));
    let plaintext_length = u32::from_le_bytes(bytes[24..28].try_into().expect("fixed slice"));
    if bytes[28] > 1 {
        return Err(EnvelopeError::InvalidFormat("final chunk flag"));
    }
    let final_chunk = bytes[28] == 1;
    let ciphertext_length = u32::from_le_bytes([bytes[29], bytes[30], bytes[31], 0]) as usize;
    let expected_length = (header.body_length
        - u64::from(expected_index) * BODY_CHUNK_PLAINTEXT_SIZE as u64)
        .min(BODY_CHUNK_PLAINTEXT_SIZE as u64) as u32;
    if index != expected_index
        || offset != u64::from(expected_index) * BODY_CHUNK_PLAINTEXT_SIZE as u64
        || plaintext_length != expected_length
        || final_chunk != (expected_index + 1 == header.chunk_count)
        || ciphertext_length != plaintext_length as usize + TAG_LENGTH
    {
        return Err(EnvelopeError::InvalidFormat("body frame declaration"));
    }
    Ok(FrameHeader {
        nonce,
        index,
        offset,
        plaintext_length,
        final_chunk,
        ciphertext_length,
    })
}

fn decrypt_frame(
    cipher: &Aes256Gcm,
    aad: &ObjectBaseAad,
    header: Header,
    metadata_hash: [u8; 32],
    frame: FrameHeader,
    ciphertext: &[u8],
) -> Result<Vec<u8>, EnvelopeError> {
    let frame_aad = BodyFrameAad::new(
        aad.clone(),
        metadata_hash,
        frame.index,
        header.chunk_count,
        frame.offset,
        u64::from(frame.plaintext_length),
        header.body_length,
        frame.final_chunk,
    )?;
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&frame.nonce),
            Payload {
                msg: ciphertext,
                aad: &frame_aad.to_bytes(),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    if plaintext.len() != frame.plaintext_length as usize {
        return Err(EnvelopeError::InvalidFormat("body plaintext length"));
    }
    Ok(plaintext)
}

fn unique_nonce(
    used: &mut HashSet<[u8; NONCE_LENGTH]>,
) -> Result<[u8; NONCE_LENGTH], EnvelopeError> {
    for _ in 0..16 {
        let mut nonce = [0_u8; NONCE_LENGTH];
        getrandom::getrandom(&mut nonce).map_err(|_| CryptoError::Randomness)?;
        if used.insert(nonce) {
            return Ok(nonce);
        }
    }
    Err(CryptoError::Randomness.into())
}

fn metadata_frame_hash(nonce: &[u8; NONCE_LENGTH], ciphertext: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(nonce);
    hasher.update(ciphertext);
    hasher.finalize().into()
}

fn parse_sha256(value: &str) -> Result<[u8; 32], EnvelopeError> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(EnvelopeError::InvalidFormat("body SHA-256"));
    }
    let mut output = [0_u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        let text =
            std::str::from_utf8(chunk).map_err(|_| EnvelopeError::InvalidFormat("body SHA-256"))?;
        output[index] = u8::from_str_radix(text, 16)
            .map_err(|_| EnvelopeError::InvalidFormat("body SHA-256"))?;
    }
    if hex_bytes(&output) != value {
        return Err(EnvelopeError::InvalidFormat("non-canonical body SHA-256"));
    }
    Ok(output)
}

fn hex_digest(value: &[u8]) -> String {
    let digest: [u8; 32] = Sha256::digest(value).into();
    hex_bytes(&digest)
}

fn hex_bytes(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn canonicalize_map(map: &mut BTreeMap<String, Value>) {
    for value in map.values_mut() {
        canonicalize_value(value);
    }
}

fn canonicalize_value(value: &mut Value) {
    match value {
        Value::Array(values) => {
            for value in values {
                canonicalize_value(value);
            }
        }
        Value::Object(map) => {
            let mut entries: Vec<_> = std::mem::take(map).into_iter().collect();
            entries.sort_by(|left, right| left.0.cmp(&right.0));
            for (_, value) in &mut entries {
                canonicalize_value(value);
            }
            map.extend(entries);
        }
        _ => {}
    }
}

fn require_eof<R: Read>(reader: &mut R) -> Result<(), EnvelopeError> {
    let mut byte = [0_u8; 1];
    if reader.read(&mut byte)? != 0 {
        return Err(EnvelopeError::InvalidFormat("trailing bytes"));
    }
    Ok(())
}

fn discard_exact<R: Read>(reader: &mut R, length: usize) -> Result<(), EnvelopeError> {
    let copied = io::copy(&mut reader.take(length as u64), &mut io::sink())?;
    if copied != length as u64 {
        return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "truncated frame").into());
    }
    Ok(())
}
