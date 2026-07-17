//! Streaming, authenticated `.acore` object envelopes.

use std::collections::{BTreeMap, HashSet};
use std::io::{self, Cursor, Read, Seek, SeekFrom, Write};
use std::ops::Range;

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use zeroize::Zeroize;

use crate::bounded::{
    clone_after_bounded_json_preflight, json_to_vec as bounded_json_to_vec, BoundedJsonError,
};
use crate::crypto::{
    BodyFrameAad, CryptoError, MetadataFrameAad, ObjectBaseAad, SecretBytes, NONCE_LENGTH,
};
use crate::id::validate_opaque_id;

pub const ENVELOPE_VERSION: u16 = 1;
pub const CIPHER_AES_256_GCM: u8 = 1;
pub const CHUNKING_FIXED_V1: u8 = 1;
pub const METADATA_SCHEMA_VERSION: u16 = 1;
pub const BODY_CHUNK_PLAINTEXT_SIZE: usize = 4 * 1024 * 1024;
pub const MAX_BODY_CHUNKS: usize = 2_048;
pub const MAX_BODY_LENGTH: u64 = BODY_CHUNK_PLAINTEXT_SIZE as u64 * MAX_BODY_CHUNKS as u64;
pub const MAX_METADATA_PLAINTEXT_SIZE: usize = 1024 * 1024;
pub const MAX_OBJECT_ID_LENGTH: usize = 1024;
pub const ENVELOPE_HEADER_SIZE: usize = 39;
pub const MAX_ENVELOPE_SIZE: u64 = ENVELOPE_HEADER_SIZE as u64
    + MAX_OBJECT_ID_LENGTH as u64
    + NONCE_LENGTH as u64
    + MAX_METADATA_PLAINTEXT_SIZE as u64
    + 16
    + MAX_BODY_LENGTH
    + MAX_BODY_CHUNKS as u64 * (32 + 16);
pub const MAX_AUTHENTICATED_RANGE_BYTES: usize = 4 * 1024 * 1024;

const MAGIC: &[u8; 8] = b"ACOREV1\0";
const KEY_DOMAIN_OBJECT_DEK: u8 = 1;
const FRAME_HEADER_SIZE: usize = 32;
const TAG_LENGTH: usize = 16;

const RESERVED_METADATA_AUTHORITY_KEYS: &[&str] = &[
    "path",
    "logicalPath",
    "logical_path",
    "parentPath",
    "parent_path",
    "parentId",
    "parent_id",
    "folderPath",
    "folder_path",
    "folderId",
    "folder_id",
];

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

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum BodyEncoding {
    #[serde(rename = "utf-8")]
    Utf8,
    #[serde(rename = "binary")]
    Binary,
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
    pub body_encoding: BodyEncoding,
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
        body_encoding: BodyEncoding,
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
            body_encoding,
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
            || self.revision == 0
        {
            return Err(EnvelopeError::InvalidFormat("incomplete metadata"));
        }
        if validate_opaque_id(&self.object_id).is_err() {
            return Err(EnvelopeError::InvalidFormat("object ID"));
        }
        validate_metadata_authority(&self.metadata)?;
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
        let mut canonical = clone_after_bounded_json_preflight(self, MAX_METADATA_PLAINTEXT_SIZE)
            .map_err(|error| match error {
            BoundedJsonError::LimitExceeded => EnvelopeError::LimitExceeded("metadata plaintext"),
            BoundedJsonError::Json(error) => EnvelopeError::Json(error),
        })?;
        canonicalize_map(&mut canonical.metadata);
        canonical.validate_shape()?;
        bounded_json_to_vec(&canonical, MAX_METADATA_PLAINTEXT_SIZE).map_err(|error| match error {
            BoundedJsonError::LimitExceeded => EnvelopeError::LimitExceeded("metadata plaintext"),
            BoundedJsonError::Json(error) => EnvelopeError::Json(error),
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EnvelopeRead {
    pub metadata: EnvelopeMetadata,
    pub whole_body_verified: bool,
}

/// Sequential reader that authenticates each immutable body chunk before it is
/// returned. The final chunk is withheld until EOF and the whole-body hash has
/// also been verified.
pub struct AuthenticatedEnvelopeStream<R> {
    reader: R,
    aad: ObjectBaseAad,
    state: MetadataRead,
    next_index: u32,
    body_hasher: Sha256,
    plaintext: Cursor<Vec<u8>>,
    finished: bool,
    failed: bool,
}

impl<R: Read> AuthenticatedEnvelopeStream<R> {
    pub fn metadata(&self) -> &EnvelopeMetadata {
        &self.state.metadata
    }

    fn load_next_chunk(&mut self) -> Result<bool, EnvelopeError> {
        if self.next_index == self.state.header.chunk_count {
            if !self.finished {
                require_eof(&mut self.reader)?;
                if std::mem::take(&mut self.body_hasher).finalize().as_slice()
                    != parse_sha256(&self.state.metadata.body_sha256)?
                {
                    return Err(EnvelopeError::InvalidFormat("body hash mismatch"));
                }
                self.finished = true;
            }
            return Ok(false);
        }

        let frame = read_and_validate_frame_header(
            &mut self.reader,
            &self.state.header,
            self.next_index,
            &mut self.state.nonces,
        )?;
        let mut ciphertext = vec![0_u8; frame.ciphertext_length];
        self.reader.read_exact(&mut ciphertext)?;
        let plaintext = decrypt_frame(
            &self.state.cipher,
            &self.aad,
            &self.state.header,
            self.state.frame_hash,
            frame,
            &ciphertext,
        )?;
        self.body_hasher.update(&plaintext);
        self.next_index += 1;

        if self.next_index == self.state.header.chunk_count {
            require_eof(&mut self.reader)?;
            if std::mem::take(&mut self.body_hasher).finalize().as_slice()
                != parse_sha256(&self.state.metadata.body_sha256)?
            {
                return Err(EnvelopeError::InvalidFormat("body hash mismatch"));
            }
            self.finished = true;
        }
        self.plaintext = Cursor::new(plaintext);
        Ok(true)
    }
}

impl<R: Read> Read for AuthenticatedEnvelopeStream<R> {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        if output.is_empty() {
            return Ok(0);
        }
        if self.failed {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "authenticated envelope stream is invalid",
            ));
        }

        let position = self.plaintext.position() as usize;
        if position == self.plaintext.get_ref().len() {
            self.plaintext = Cursor::new(Vec::new());
            match self.load_next_chunk() {
                Ok(true) => {}
                Ok(false) => return Ok(0),
                Err(error) => {
                    self.failed = true;
                    return Err(io::Error::new(io::ErrorKind::InvalidData, error));
                }
            }
        }
        self.plaintext.read(output)
    }
}

pub fn open_envelope_stream<R: Read>(
    mut reader: R,
    key: &SecretBytes,
    aad: ObjectBaseAad,
) -> Result<AuthenticatedEnvelopeStream<R>, EnvelopeError> {
    let state = read_metadata(&mut reader, key, &aad)?;
    Ok(AuthenticatedEnvelopeStream {
        reader,
        aad,
        state,
        next_index: 0,
        body_hasher: Sha256::new(),
        plaintext: Cursor::new(Vec::new()),
        finished: false,
        failed: false,
    })
}

#[derive(Clone)]
struct Header {
    object_key_epoch: u32,
    object_id: String,
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

/// Streams an authenticated envelope into `writer`.
///
/// The writer may contain a partial envelope when this function returns `Err`.
/// Callers must discard or roll back that sink. Canonical CoreFS publication must
/// write to a staged sink and atomically publish it only after `Ok(())`.
pub fn write_envelope<W: Write, R: Read>(
    writer: &mut W,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    metadata: &EnvelopeMetadata,
    body: &mut R,
) -> Result<(), EnvelopeError> {
    write_envelope_with_nonce_source(writer, key, aad, metadata, body, &mut SystemNonceSource)
}

/// Re-encrypts one authenticated object into the next revision and key epoch.
///
/// Plaintext is held only for the currently authenticated body chunk. The output
/// may be partial on error and must be discarded rather than published.
#[allow(clippy::too_many_arguments)]
pub fn rotate_object_key_envelope<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
    old_key: &SecretBytes,
    old_aad: &ObjectBaseAad,
    new_key: &SecretBytes,
    new_aad: &ObjectBaseAad,
    updated_at: &str,
) -> Result<EnvelopeMetadata, EnvelopeError> {
    if old_key.as_slice() == new_key.as_slice() {
        return Err(EnvelopeError::InvalidFormat(
            "object-key rotation reused DEK",
        ));
    }
    if old_aad.core_id() != new_aad.core_id()
        || old_aad.object_id() != new_aad.object_id()
        || old_aad.kind() != new_aad.kind()
        || old_aad.envelope_version() != new_aad.envelope_version()
        || old_aad.revision().checked_add(1) != Some(new_aad.revision())
        || old_aad.object_key_epoch().checked_add(1) != Some(new_aad.object_key_epoch())
    {
        return Err(EnvelopeError::InvalidFormat("object-key rotation lineage"));
    }
    if updated_at.is_empty() {
        return Err(EnvelopeError::InvalidFormat("rotation timestamp"));
    }

    let mut old_state = read_metadata(reader, old_key, old_aad)?;
    let mut new_metadata = old_state.metadata.clone();
    new_metadata.revision = new_aad.revision();
    new_metadata.updated_at = updated_at.to_owned();
    new_metadata.validate_for_aad(new_aad)?;
    let metadata_plaintext = new_metadata.canonical_bytes()?;
    let metadata_ciphertext_length = metadata_plaintext
        .len()
        .checked_add(TAG_LENGTH)
        .ok_or(EnvelopeError::LimitExceeded("metadata ciphertext"))?;
    let new_header = Header {
        object_key_epoch: new_aad.object_key_epoch(),
        object_id: new_aad.object_id().to_owned(),
        metadata_ciphertext_length,
        body_length: new_metadata.body_length,
        chunk_count: new_metadata.chunk_count,
    };
    write_header(writer, &new_header)?;

    let new_cipher = cipher(new_key)?;
    let mut new_nonces = HashSet::new();
    let mut nonce_source = SystemNonceSource;
    let metadata_nonce = unique_nonce(&mut new_nonces, &mut nonce_source)?;
    let metadata_aad = MetadataFrameAad::new(new_aad.clone(), CHUNKING_FIXED_V1 as u16)?;
    let metadata_ciphertext = new_cipher
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
    let new_metadata_hash = metadata_frame_hash(&metadata_nonce, &metadata_ciphertext);

    let mut body_hasher = Sha256::new();
    for index in 0..old_state.header.chunk_count {
        let old_frame = read_and_validate_frame_header(
            reader,
            &old_state.header,
            index,
            &mut old_state.nonces,
        )?;
        let mut old_ciphertext = vec![0_u8; old_frame.ciphertext_length];
        reader.read_exact(&mut old_ciphertext)?;
        let plaintext = decrypt_frame(
            &old_state.cipher,
            old_aad,
            &old_state.header,
            old_state.frame_hash,
            old_frame,
            &old_ciphertext,
        )?;
        body_hasher.update(&plaintext);

        let new_frame_aad = BodyFrameAad::new(
            new_aad.clone(),
            new_metadata_hash,
            old_frame.index,
            new_metadata.chunk_count,
            old_frame.offset,
            u64::from(old_frame.plaintext_length),
            new_metadata.body_length,
            old_frame.final_chunk,
        )?;
        let nonce = unique_nonce(&mut new_nonces, &mut nonce_source)?;
        let new_ciphertext = new_cipher
            .encrypt(
                Nonce::from_slice(&nonce),
                Payload {
                    msg: &plaintext,
                    aad: &new_frame_aad.to_bytes(),
                },
            )
            .map_err(|_| CryptoError::Authentication)?;
        write_frame_header(
            writer,
            FrameHeader {
                nonce,
                index: old_frame.index,
                offset: old_frame.offset,
                plaintext_length: old_frame.plaintext_length,
                final_chunk: old_frame.final_chunk,
                ciphertext_length: new_ciphertext.len(),
            },
        )?;
        writer.write_all(&new_ciphertext)?;
    }
    require_eof(reader)?;
    if body_hasher.finalize().as_slice() != parse_sha256(&old_state.metadata.body_sha256)? {
        return Err(EnvelopeError::InvalidFormat("body hash mismatch"));
    }
    Ok(new_metadata)
}

fn write_envelope_with_nonce_source<W: Write, R: Read, N: NonceSource>(
    writer: &mut W,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    metadata: &EnvelopeMetadata,
    body: &mut R,
    nonce_source: &mut N,
) -> Result<(), EnvelopeError> {
    metadata.validate_for_aad(aad)?;
    let metadata_plaintext = metadata.canonical_bytes()?;
    let metadata_ciphertext_length = metadata_plaintext
        .len()
        .checked_add(TAG_LENGTH)
        .ok_or(EnvelopeError::LimitExceeded("metadata ciphertext"))?;
    validate_object_id(aad.object_id())?;
    let header = Header {
        object_key_epoch: aad.object_key_epoch(),
        object_id: aad.object_id().to_owned(),
        metadata_ciphertext_length,
        body_length: metadata.body_length,
        chunk_count: metadata.chunk_count,
    };
    write_header(writer, &header)?;

    let cipher = cipher(key)?;
    let mut used_nonces = HashSet::new();
    let metadata_nonce = unique_nonce(&mut used_nonces, nonce_source)?;
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
        let nonce = unique_nonce(&mut used_nonces, nonce_source)?;
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

/// Authenticates and streams the complete body into `output`.
///
/// Each chunk is authenticated before release, but a later frame, EOF, or whole-body
/// hash failure can occur after earlier plaintext was written. Callers must discard or
/// roll back `output` on `Err`.
pub fn read_envelope<R: Read, W: Write>(
    reader: &mut R,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    output: &mut W,
) -> Result<EnvelopeRead, EnvelopeError> {
    let mut state = read_metadata(reader, key, aad)?;
    let mut body_hasher = Sha256::new();
    for index in 0..state.header.chunk_count {
        let frame =
            read_and_validate_frame_header(reader, &state.header, index, &mut state.nonces)?;
        let mut ciphertext = vec![0_u8; frame.ciphertext_length];
        reader.read_exact(&mut ciphertext)?;
        let plaintext = decrypt_frame(
            &state.cipher,
            aad,
            &state.header,
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

/// Authenticates intersecting chunks and streams the requested range into `output`.
///
/// A terminal envelope validation failure can occur after range bytes were written.
/// Callers must discard or roll back `output` on `Err`.
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
        let frame =
            read_and_validate_frame_header(reader, &state.header, index, &mut state.nonces)?;
        let frame_end = frame.offset + u64::from(frame.plaintext_length);
        let intersects = range.start < frame_end && range.end > frame.offset;
        if intersects || whole_body {
            let mut ciphertext = vec![0_u8; frame.ciphertext_length];
            reader.read_exact(&mut ciphertext)?;
            let plaintext = decrypt_frame(
                &state.cipher,
                aad,
                &state.header,
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

/// Authenticates and returns only the fixed-v1 frames intersecting a bounded
/// plaintext range. Nonintersecting ciphertext is addressed by seek, never
/// scanned or decrypted. The callback is checked at every bounded I/O and
/// authentication boundary.
pub fn read_envelope_seekable_range<R, W, F>(
    reader: &mut R,
    key: &SecretBytes,
    aad: &ObjectBaseAad,
    offset: u64,
    max_bytes: usize,
    output: &mut W,
    mut check: F,
) -> Result<EnvelopeRead, EnvelopeError>
where
    R: Read + Seek,
    W: Write,
    F: FnMut() -> io::Result<()>,
{
    if max_bytes > MAX_AUTHENTICATED_RANGE_BYTES {
        return Err(EnvelopeError::LimitExceeded("authenticated range"));
    }
    check()?;
    let mut state = read_metadata(reader, key, aad)?;
    check()?;
    if max_bytes == 0 || offset >= state.header.body_length {
        return Ok(EnvelopeRead {
            metadata: state.metadata,
            whole_body_verified: false,
        });
    }

    let body_start = reader.stream_position()?;
    let frame_overhead = (FRAME_HEADER_SIZE + TAG_LENGTH) as u64;
    let frame_bytes = u64::from(state.header.chunk_count)
        .checked_mul(frame_overhead)
        .and_then(|value| value.checked_add(state.header.body_length))
        .ok_or(EnvelopeError::LimitExceeded("envelope length"))?;
    let expected_end = body_start
        .checked_add(frame_bytes)
        .ok_or(EnvelopeError::LimitExceeded("envelope length"))?;
    check()?;
    if reader.seek(SeekFrom::End(0))? != expected_end {
        return Err(EnvelopeError::InvalidFormat("envelope file length"));
    }

    let requested = u64::try_from(max_bytes)
        .map_err(|_| EnvelopeError::LimitExceeded("authenticated range"))?;
    let end = offset
        .saturating_add(requested)
        .min(state.header.body_length);
    let chunk_size = BODY_CHUNK_PLAINTEXT_SIZE as u64;
    let first_index = offset / chunk_size;
    let last_index = (end - 1) / chunk_size;
    let mut staged = Vec::with_capacity((end - offset) as usize);

    for index in first_index..=last_index {
        check()?;
        let prior_frame_bytes = index
            .checked_mul(chunk_size + frame_overhead)
            .ok_or(EnvelopeError::LimitExceeded("frame offset"))?;
        let frame_position = body_start
            .checked_add(prior_frame_bytes)
            .ok_or(EnvelopeError::LimitExceeded("frame offset"))?;
        reader.seek(SeekFrom::Start(frame_position))?;
        check()?;
        let frame =
            read_and_validate_frame_header(reader, &state.header, index as u32, &mut state.nonces)?;
        let mut ciphertext = vec![0_u8; frame.ciphertext_length];
        check()?;
        reader.read_exact(&mut ciphertext)?;
        check()?;
        let mut plaintext = decrypt_frame(
            &state.cipher,
            aad,
            &state.header,
            state.frame_hash,
            frame,
            &ciphertext,
        )?;
        ciphertext.zeroize();
        let frame_end = frame.offset + u64::from(frame.plaintext_length);
        let from = offset.saturating_sub(frame.offset) as usize;
        let to = (end.min(frame_end) - frame.offset) as usize;
        staged.extend_from_slice(&plaintext[from..to]);
        plaintext.zeroize();
    }

    check()?;
    let whole_body = offset == 0 && end == state.header.body_length;
    if whole_body
        && Sha256::digest(&staged).as_slice() != parse_sha256(&state.metadata.body_sha256)?
    {
        staged.zeroize();
        return Err(EnvelopeError::InvalidFormat("body hash mismatch"));
    }
    output.write_all(&staged)?;
    staged.zeroize();
    Ok(EnvelopeRead {
        metadata: state.metadata,
        whole_body_verified: whole_body,
    })
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
    let header = read_header(reader, aad)?;
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
        || metadata.object_id != header.object_id
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

fn validate_object_id(object_id: &str) -> Result<(), EnvelopeError> {
    if validate_opaque_id(object_id).is_err() {
        return Err(EnvelopeError::InvalidFormat("object ID"));
    }
    if object_id.len() > MAX_OBJECT_ID_LENGTH {
        return Err(EnvelopeError::LimitExceeded("object ID"));
    }
    Ok(())
}

fn write_header<W: Write>(writer: &mut W, header: &Header) -> Result<(), EnvelopeError> {
    let object_id = header.object_id.as_bytes();
    validate_object_id(&header.object_id)?;
    if header.object_key_epoch == 0 {
        return Err(EnvelopeError::InvalidFormat("object-key epoch"));
    }
    let mut bytes = [0_u8; ENVELOPE_HEADER_SIZE];
    bytes[..8].copy_from_slice(MAGIC);
    bytes[8..10].copy_from_slice(&ENVELOPE_VERSION.to_le_bytes());
    bytes[10] = CIPHER_AES_256_GCM;
    bytes[11] = CHUNKING_FIXED_V1;
    bytes[12] = KEY_DOMAIN_OBJECT_DEK;
    bytes[13..17].copy_from_slice(&header.object_key_epoch.to_le_bytes());
    bytes[17..19].copy_from_slice(&(object_id.len() as u16).to_le_bytes());
    bytes[19..23].copy_from_slice(&(header.metadata_ciphertext_length as u32).to_le_bytes());
    bytes[23..31].copy_from_slice(&header.body_length.to_le_bytes());
    bytes[31..35].copy_from_slice(&(BODY_CHUNK_PLAINTEXT_SIZE as u32).to_le_bytes());
    bytes[35..39].copy_from_slice(&header.chunk_count.to_le_bytes());
    writer.write_all(&bytes)?;
    writer.write_all(object_id)?;
    Ok(())
}

fn read_header<R: Read>(reader: &mut R, aad: &ObjectBaseAad) -> Result<Header, EnvelopeError> {
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
    if bytes[12] != KEY_DOMAIN_OBJECT_DEK {
        return Err(EnvelopeError::Unsupported("key domain"));
    }
    let object_key_epoch = u32::from_le_bytes(bytes[13..17].try_into().expect("fixed slice"));
    if object_key_epoch == 0 {
        return Err(EnvelopeError::InvalidFormat("object-key epoch"));
    }
    let object_id_length =
        u16::from_le_bytes(bytes[17..19].try_into().expect("fixed slice")) as usize;
    if object_id_length == 0 {
        return Err(EnvelopeError::InvalidFormat("object ID"));
    }
    if object_id_length > MAX_OBJECT_ID_LENGTH {
        return Err(EnvelopeError::LimitExceeded("object ID"));
    }
    let metadata_ciphertext_length =
        u32::from_le_bytes(bytes[19..23].try_into().expect("fixed slice")) as usize;
    if !(TAG_LENGTH..=MAX_METADATA_PLAINTEXT_SIZE + TAG_LENGTH)
        .contains(&metadata_ciphertext_length)
    {
        return Err(EnvelopeError::LimitExceeded("metadata ciphertext"));
    }
    let body_length = u64::from_le_bytes(bytes[23..31].try_into().expect("fixed slice"));
    if body_length > MAX_BODY_LENGTH {
        return Err(EnvelopeError::LimitExceeded("body length"));
    }
    let chunk_size = u32::from_le_bytes(bytes[31..35].try_into().expect("fixed slice"));
    if chunk_size != BODY_CHUNK_PLAINTEXT_SIZE as u32 {
        return Err(EnvelopeError::Unsupported("chunk plaintext size"));
    }
    let chunk_count = u32::from_le_bytes(bytes[35..39].try_into().expect("fixed slice"));
    if chunk_count as usize > MAX_BODY_CHUNKS {
        return Err(EnvelopeError::LimitExceeded("body chunk count"));
    }
    if chunk_count != expected_chunk_count(body_length)? {
        return Err(EnvelopeError::InvalidFormat("header chunk count"));
    }
    let mut object_id = vec![0_u8; object_id_length];
    reader.read_exact(&mut object_id)?;
    let object_id = String::from_utf8(object_id)
        .map_err(|_| EnvelopeError::InvalidFormat("object ID encoding"))?;
    validate_object_id(&object_id)?;
    if object_key_epoch != aad.object_key_epoch() || object_id != aad.object_id() {
        return Err(EnvelopeError::InvalidFormat("header/AAD mismatch"));
    }
    Ok(Header {
        object_key_epoch,
        object_id,
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
    header: &Header,
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
    header: &Header,
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

trait NonceSource {
    fn next_nonce(&mut self) -> Result<[u8; NONCE_LENGTH], CryptoError>;
}

struct SystemNonceSource;

impl NonceSource for SystemNonceSource {
    fn next_nonce(&mut self) -> Result<[u8; NONCE_LENGTH], CryptoError> {
        let mut nonce = [0_u8; NONCE_LENGTH];
        getrandom::getrandom(&mut nonce).map_err(|_| CryptoError::Randomness)?;
        Ok(nonce)
    }
}

fn unique_nonce<N: NonceSource>(
    used: &mut HashSet<[u8; NONCE_LENGTH]>,
    source: &mut N,
) -> Result<[u8; NONCE_LENGTH], EnvelopeError> {
    for _ in 0..16 {
        let nonce = source.next_nonce()?;
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

fn validate_metadata_authority(metadata: &BTreeMap<String, Value>) -> Result<(), EnvelopeError> {
    for (key, value) in metadata {
        if RESERVED_METADATA_AUTHORITY_KEYS.contains(&key.as_str()) {
            return Err(EnvelopeError::InvalidFormat(
                "reserved metadata authority key",
            ));
        }
        validate_metadata_authority_value(value)?;
    }
    Ok(())
}

fn validate_metadata_authority_value(value: &Value) -> Result<(), EnvelopeError> {
    match value {
        Value::Array(values) => {
            for value in values {
                validate_metadata_authority_value(value)?;
            }
        }
        Value::Object(values) => {
            for (key, value) in values {
                if RESERVED_METADATA_AUTHORITY_KEYS.contains(&key.as_str()) {
                    return Err(EnvelopeError::InvalidFormat(
                        "reserved metadata authority key",
                    ));
                }
                validate_metadata_authority_value(value)?;
            }
        }
        _ => {}
    }
    Ok(())
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

#[cfg(test)]
mod tests {
    use std::collections::{BTreeMap, VecDeque};
    use std::io::Cursor;

    use super::*;
    use crate::crypto::{ObjectKind, SecretBytes};

    const OBJECT_ID: &str = "01J00000000000000000000000";

    struct SequenceNonceSource {
        values: VecDeque<[u8; NONCE_LENGTH]>,
        calls: usize,
    }

    impl SequenceNonceSource {
        fn new(values: impl IntoIterator<Item = [u8; NONCE_LENGTH]>) -> Self {
            Self {
                values: values.into_iter().collect(),
                calls: 0,
            }
        }
    }

    impl NonceSource for SequenceNonceSource {
        fn next_nonce(&mut self) -> Result<[u8; NONCE_LENGTH], CryptoError> {
            self.calls += 1;
            self.values.pop_front().ok_or(CryptoError::Randomness)
        }
    }

    fn fixture(body: &[u8]) -> (SecretBytes, ObjectBaseAad, EnvelopeMetadata) {
        let key = SecretBytes::new(vec![0x41; 32]).unwrap();
        let aad = ObjectBaseAad::new("01JCORE", OBJECT_ID, ObjectKind::Note, 1, 1, 1).unwrap();
        let metadata = EnvelopeMetadata::for_body(
            "note",
            OBJECT_ID,
            1,
            "2026-07-15T00:00:00Z",
            "2026-07-15T00:00:01Z",
            "application/octet-stream",
            BTreeMap::new(),
            BodyEncoding::Binary,
            body,
        )
        .unwrap();
        (key, aad, metadata)
    }

    #[test]
    fn duplicate_generated_nonce_is_retried_before_frame_encryption() {
        let body = b"one body frame";
        let (key, aad, metadata) = fixture(body);
        let nonce_a = [0x11; NONCE_LENGTH];
        let nonce_b = [0x22; NONCE_LENGTH];
        let mut source = SequenceNonceSource::new([nonce_a, nonce_a, nonce_b]);
        let mut encoded = Vec::new();

        write_envelope_with_nonce_source(
            &mut encoded,
            &key,
            &aad,
            &metadata,
            &mut Cursor::new(body),
            &mut source,
        )
        .unwrap();

        assert_eq!(source.calls, 3);
        assert_eq!(decode_envelope(&key, &aad, &encoded).unwrap().1, body);
    }

    #[test]
    fn persistent_generated_nonce_collision_fails_the_envelope_write() {
        let body = b"one body frame";
        let (key, aad, metadata) = fixture(body);
        let nonce = [0x11; NONCE_LENGTH];
        let mut source = SequenceNonceSource::new(std::iter::repeat(nonce).take(17));
        let mut encoded = Vec::new();

        assert!(matches!(
            write_envelope_with_nonce_source(
                &mut encoded,
                &key,
                &aad,
                &metadata,
                &mut Cursor::new(body),
                &mut source,
            ),
            Err(EnvelopeError::Crypto(CryptoError::Randomness))
        ));
        assert_eq!(source.calls, 17);
        assert!(decode_envelope(&key, &aad, &encoded).is_err());
    }
}
