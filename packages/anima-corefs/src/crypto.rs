use std::fmt;

use aes_gcm::{
    aead::{Aead, KeyInit},
    Aes256Gcm, Nonce,
};
use argon2::{Algorithm, Argon2, Params, Version};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use hkdf::Hkdf;
use rand::{rngs::OsRng, CryptoRng, RngCore};
use sha2::Sha256;
use zeroize::{Zeroize, ZeroizeOnDrop};

pub const KEY_LENGTH: usize = 32;
pub const NONCE_LENGTH: usize = 12;
pub const OBJECT_KEY_ENVELOPE_VERSION: u16 = 1;
pub const OBJECT_WRAP_ALGORITHM: &str = "aes-256-gcm";
pub const OBJECT_WRAP_LABEL: &[u8] = b"anima-corefs-object-wrap-v1";
pub const CATALOG_LABEL: &[u8] = b"anima-corefs-catalog-v1";
pub const SEARCH_LABEL: &[u8] = b"anima-corefs-search-v1";
pub const CREDENTIAL_SALT_LENGTH: usize = 16;
pub const CREDENTIAL_TIME_COST: u32 = 3;
pub const CREDENTIAL_MEMORY_COST_KIB: u32 = 64 * 1024;
pub const CREDENTIAL_PARALLELISM: u32 = 4;

#[allow(clippy::too_many_arguments)]
pub fn manifest_keyslot_aad(
    core_id: &str,
    owner_id: &str,
    purpose: &str,
    key_version: u32,
    credential_generation: u32,
    scope: &str,
    frk_version: Option<u32>,
    object_key_epoch: Option<u32>,
    wrapping_path: &str,
) -> Result<Vec<u8>, CryptoError> {
    if core_id.is_empty()
        || owner_id.is_empty()
        || core_id.contains(':')
        || owner_id.contains(':')
        || key_version == 0
        || credential_generation == 0
        || !matches!(scope, "full" | "soul" | "fs")
        || !matches!(wrapping_path, "password" | "recovery")
    {
        return Err(CryptoError::InvalidAad("invalid manifest keyslot metadata"));
    }
    match purpose {
        "filesystem-root"
            if frk_version == Some(key_version)
                && object_key_epoch.is_some_and(|epoch| epoch > 0) => {}
        "soul" if frk_version.is_none() && object_key_epoch.is_none() => {}
        "filesystem-root" => {
            return Err(CryptoError::InvalidAad(
                "FRK keyslot metadata is incomplete",
            ));
        }
        "soul" => {
            return Err(CryptoError::InvalidAad(
                "Soul keyslot declares filesystem metadata",
            ));
        }
        _ => return Err(CryptoError::InvalidAad("invalid manifest keyslot purpose")),
    }
    let frk_version = frk_version
        .map(|version| version.to_string())
        .unwrap_or_else(|| "none".to_string());
    let object_key_epoch = object_key_epoch
        .map(|epoch| epoch.to_string())
        .unwrap_or_else(|| "none".to_string());
    Ok(format!(
        "anima-keyslot-v1:core={core_id}:owner={owner_id}:purpose={purpose}:\
         version={key_version}:generation={credential_generation}:scope={scope}:\
         frk-version={frk_version}:object-key-epoch={object_key_epoch}:path={wrapping_path}"
    )
    .into_bytes())
}

pub fn soul_keyslot_aad(
    core_id: &str,
    owner_id: &str,
    domain: &str,
    key_version: u32,
    credential_generation: u32,
    wrapping_path: &str,
) -> Result<Vec<u8>, CryptoError> {
    if core_id.is_empty()
        || owner_id.is_empty()
        || domain.is_empty()
        || core_id.contains(':')
        || owner_id.contains(':')
        || domain.contains(':')
        || key_version == 0
        || credential_generation == 0
        || !matches!(wrapping_path, "password" | "recovery")
    {
        return Err(CryptoError::InvalidAad("invalid Soul keyslot metadata"));
    }
    Ok(format!(
        "anima-soul-keyslot-v1:core={core_id}:owner={owner_id}:domain={domain}:\
         version={key_version}:generation={credential_generation}:path={wrapping_path}"
    )
    .into_bytes())
}

#[derive(Debug, thiserror::Error)]
pub enum CryptoError {
    #[error("secret must be exactly 32 bytes")]
    InvalidSecretLength,
    #[error("unsupported object kind: {0}")]
    UnsupportedObjectKind(String),
    #[error("unsupported object-key envelope version: {0}")]
    UnsupportedEnvelopeVersion(u16),
    #[error("unsupported object-key wrapping algorithm: {0}")]
    UnsupportedAlgorithm(String),
    #[error("invalid object-key AAD: {0}")]
    InvalidAad(&'static str),
    #[error("FRK version must be positive")]
    InvalidFrkVersion,
    #[error("invalid wrapped object key")]
    InvalidWrappedKey,
    #[error("operating-system random number generator failed")]
    Randomness,
    #[error("key derivation failed")]
    Derivation,
    #[error("invalid credential-wrapped Filesystem Root Key")]
    InvalidCredentialWrappedRoot,
    #[error("object key authentication failed")]
    Authentication,
}

#[derive(Zeroize, ZeroizeOnDrop)]
pub struct SecretBytes(Vec<u8>);

impl SecretBytes {
    pub fn new(bytes: Vec<u8>) -> Result<Self, CryptoError> {
        if bytes.len() != KEY_LENGTH {
            return Err(CryptoError::InvalidSecretLength);
        }
        Ok(Self(bytes))
    }

    pub fn as_slice(&self) -> &[u8] {
        &self.0
    }

    pub fn len(&self) -> usize {
        self.0.len()
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub fn zeroize_now(&mut self) {
        self.0.zeroize();
    }
}

impl fmt::Debug for SecretBytes {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretBytes([REDACTED])")
    }
}

pub struct FrkSubkeys {
    frk_version: u32,
    object_wrap: SecretBytes,
    catalog: SecretBytes,
    search: SecretBytes,
}

impl FrkSubkeys {
    pub fn frk_version(&self) -> u32 {
        self.frk_version
    }

    pub fn object_wrap(&self) -> &SecretBytes {
        &self.object_wrap
    }

    pub fn catalog(&self) -> &SecretBytes {
        &self.catalog
    }

    pub fn search(&self) -> &SecretBytes {
        &self.search
    }
}

fn hkdf_subkey(frk: &SecretBytes, label: &[u8]) -> Result<SecretBytes, CryptoError> {
    let hkdf = Hkdf::<Sha256>::new(None, frk.as_slice());
    let mut output = vec![0_u8; KEY_LENGTH];
    hkdf.expand(label, &mut output)
        .map_err(|_| CryptoError::Derivation)?;
    SecretBytes::new(output)
}

pub fn derive_corefs_subkeys(frk: &SecretBytes, version: u32) -> Result<FrkSubkeys, CryptoError> {
    if version == 0 {
        return Err(CryptoError::InvalidFrkVersion);
    }
    Ok(FrkSubkeys {
        frk_version: version,
        object_wrap: hkdf_subkey(frk, OBJECT_WRAP_LABEL)?,
        catalog: hkdf_subkey(frk, CATALOG_LABEL)?,
        search: hkdf_subkey(frk, SEARCH_LABEL)?,
    })
}

pub fn create_object_dek(rng: &mut (impl RngCore + CryptoRng)) -> Result<SecretBytes, CryptoError> {
    let mut output = vec![0_u8; KEY_LENGTH];
    rng.try_fill_bytes(&mut output)
        .map_err(|_| CryptoError::Randomness)?;
    SecretBytes::new(output)
}

pub fn generate_object_dek() -> Result<SecretBytes, CryptoError> {
    create_object_dek(&mut OsRng)
}

pub fn generate_filesystem_root_key() -> Result<SecretBytes, CryptoError> {
    create_object_dek(&mut OsRng)
}

pub struct WrappedFilesystemRootKey {
    salt: [u8; CREDENTIAL_SALT_LENGTH],
    nonce: [u8; NONCE_LENGTH],
    ciphertext: Vec<u8>,
}

impl WrappedFilesystemRootKey {
    pub fn from_base64_parts(
        salt: &str,
        nonce: &str,
        tag: &str,
        ciphertext: &str,
    ) -> Result<Self, CryptoError> {
        let salt: [u8; CREDENTIAL_SALT_LENGTH] = BASE64
            .decode(salt)
            .map_err(|_| CryptoError::InvalidCredentialWrappedRoot)?
            .try_into()
            .map_err(|_| CryptoError::InvalidCredentialWrappedRoot)?;
        let nonce: [u8; NONCE_LENGTH] = BASE64
            .decode(nonce)
            .map_err(|_| CryptoError::InvalidCredentialWrappedRoot)?
            .try_into()
            .map_err(|_| CryptoError::InvalidCredentialWrappedRoot)?;
        let tag = BASE64
            .decode(tag)
            .map_err(|_| CryptoError::InvalidCredentialWrappedRoot)?;
        let mut ciphertext = BASE64
            .decode(ciphertext)
            .map_err(|_| CryptoError::InvalidCredentialWrappedRoot)?;
        if ciphertext.len() != KEY_LENGTH || tag.len() != 16 {
            return Err(CryptoError::InvalidCredentialWrappedRoot);
        }
        ciphertext.extend_from_slice(&tag);
        Ok(Self {
            salt,
            nonce,
            ciphertext,
        })
    }

    pub fn salt_base64(&self) -> String {
        BASE64.encode(self.salt)
    }

    pub fn nonce_base64(&self) -> String {
        BASE64.encode(self.nonce)
    }

    pub fn tag_base64(&self) -> String {
        BASE64.encode(&self.ciphertext[KEY_LENGTH..])
    }

    pub fn ciphertext_base64(&self) -> String {
        BASE64.encode(&self.ciphertext[..KEY_LENGTH])
    }
}

fn credential_kek(credential: &str, salt: &[u8]) -> Result<[u8; KEY_LENGTH], CryptoError> {
    let params = Params::new(
        CREDENTIAL_MEMORY_COST_KIB,
        CREDENTIAL_TIME_COST,
        CREDENTIAL_PARALLELISM,
        Some(KEY_LENGTH),
    )
    .map_err(|_| CryptoError::Derivation)?;
    let argon2 = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);
    let mut key = [0_u8; KEY_LENGTH];
    argon2
        .hash_password_into(credential.as_bytes(), salt, &mut key)
        .map_err(|_| CryptoError::Derivation)?;
    Ok(key)
}

pub fn wrap_filesystem_root_key(
    credential: &str,
    root: &SecretBytes,
    aad: &[u8],
) -> Result<WrappedFilesystemRootKey, CryptoError> {
    let mut salt = [0_u8; CREDENTIAL_SALT_LENGTH];
    let mut nonce = [0_u8; NONCE_LENGTH];
    getrandom::getrandom(&mut salt).map_err(|_| CryptoError::Randomness)?;
    getrandom::getrandom(&mut nonce).map_err(|_| CryptoError::Randomness)?;
    let mut kek = credential_kek(credential, &salt)?;
    let cipher = Aes256Gcm::new_from_slice(&kek).map_err(|_| CryptoError::Derivation)?;
    kek.zeroize();
    let ciphertext = cipher
        .encrypt(
            Nonce::from_slice(&nonce),
            aes_gcm::aead::Payload {
                msg: root.as_slice(),
                aad,
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    Ok(WrappedFilesystemRootKey {
        salt,
        nonce,
        ciphertext,
    })
}

pub fn unwrap_filesystem_root_key(
    credential: &str,
    wrapped: &WrappedFilesystemRootKey,
    aad: &[u8],
) -> Result<SecretBytes, CryptoError> {
    let mut kek = credential_kek(credential, &wrapped.salt)?;
    let cipher = Aes256Gcm::new_from_slice(&kek).map_err(|_| CryptoError::Derivation)?;
    kek.zeroize();
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&wrapped.nonce),
            aes_gcm::aead::Payload {
                msg: &wrapped.ciphertext,
                aad,
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    SecretBytes::new(plaintext)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ObjectKind {
    AccountProfile,
    Attachment,
    Diary,
    Draft,
    Folder,
    GalleryAsset,
    KnowledgeSource,
    MessageSegment,
    Note,
    Preferences,
    Task,
    Thread,
}

impl ObjectKind {
    pub fn parse(value: &str) -> Result<Self, CryptoError> {
        match value {
            "account-profile" => Ok(Self::AccountProfile),
            "attachment" => Ok(Self::Attachment),
            "diary" => Ok(Self::Diary),
            "draft" => Ok(Self::Draft),
            "folder" => Ok(Self::Folder),
            "gallery-asset" => Ok(Self::GalleryAsset),
            "knowledge-source" => Ok(Self::KnowledgeSource),
            "message-segment" => Ok(Self::MessageSegment),
            "note" => Ok(Self::Note),
            "preferences" => Ok(Self::Preferences),
            "task" => Ok(Self::Task),
            "thread" => Ok(Self::Thread),
            other => Err(CryptoError::UnsupportedObjectKind(other.to_owned())),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::AccountProfile => "account-profile",
            Self::Attachment => "attachment",
            Self::Diary => "diary",
            Self::Draft => "draft",
            Self::Folder => "folder",
            Self::GalleryAsset => "gallery-asset",
            Self::KnowledgeSource => "knowledge-source",
            Self::MessageSegment => "message-segment",
            Self::Note => "note",
            Self::Preferences => "preferences",
            Self::Task => "task",
            Self::Thread => "thread",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObjectBaseAad {
    core_id: String,
    object_id: String,
    kind: ObjectKind,
    envelope_version: u16,
    object_key_epoch: u32,
    revision: u64,
}

impl ObjectBaseAad {
    pub fn new(
        core_id: impl Into<String>,
        object_id: impl Into<String>,
        kind: ObjectKind,
        envelope_version: u16,
        object_key_epoch: u32,
        revision: u64,
    ) -> Result<Self, CryptoError> {
        let aad = Self {
            core_id: core_id.into(),
            object_id: object_id.into(),
            kind,
            envelope_version,
            object_key_epoch,
            revision,
        };
        aad.validate()?;
        Ok(aad)
    }

    fn validate(&self) -> Result<(), CryptoError> {
        if self.core_id.is_empty() {
            return Err(CryptoError::InvalidAad("core ID is empty"));
        }
        if self.object_id.is_empty() {
            return Err(CryptoError::InvalidAad("object ID is empty"));
        }
        if self.revision == 0 {
            return Err(CryptoError::InvalidAad("revision must be positive"));
        }
        if self.envelope_version != OBJECT_KEY_ENVELOPE_VERSION {
            return Err(CryptoError::UnsupportedEnvelopeVersion(
                self.envelope_version,
            ));
        }
        if self.object_key_epoch == 0 {
            return Err(CryptoError::InvalidAad("object-key epoch must be positive"));
        }
        Ok(())
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut encoded = Vec::new();
        encoded.extend_from_slice(b"anima-corefs-object-base-v1\0");
        push_len_value(&mut encoded, b"core-id", &self.core_id);
        push_len_value(&mut encoded, b"object-id", &self.object_id);
        push_value(&mut encoded, b"kind", self.kind.as_str());
        push_value(
            &mut encoded,
            b"envelope-version",
            &self.envelope_version.to_string(),
        );
        push_value(
            &mut encoded,
            b"object-key-epoch",
            &self.object_key_epoch.to_string(),
        );
        push_value(&mut encoded, b"revision", &self.revision.to_string());
        encoded.pop();
        encoded
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObjectKeyAad {
    base: ObjectBaseAad,
    frk_version: u32,
}

impl ObjectKeyAad {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        core_id: impl Into<String>,
        object_id: impl Into<String>,
        revision: u64,
        kind: ObjectKind,
        envelope_version: u16,
        object_key_epoch: u32,
        frk_version: u32,
    ) -> Result<Self, CryptoError> {
        Self::from_base(
            ObjectBaseAad::new(
                core_id,
                object_id,
                kind,
                envelope_version,
                object_key_epoch,
                revision,
            )?,
            frk_version,
        )
    }

    pub fn from_base(base: ObjectBaseAad, frk_version: u32) -> Result<Self, CryptoError> {
        if frk_version == 0 {
            return Err(CryptoError::InvalidAad("FRK version must be positive"));
        }
        Ok(Self { base, frk_version })
    }

    fn validate(&self) -> Result<(), CryptoError> {
        self.base.validate()?;
        if self.frk_version == 0 {
            return Err(CryptoError::InvalidAad("FRK version must be positive"));
        }
        Ok(())
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut encoded = Vec::new();
        encoded.extend_from_slice(b"anima-corefs-object-key-wrap-v1\0");
        push_bytes(&mut encoded, b"base", &self.base.to_bytes());
        push_value(&mut encoded, b"frk-version", &self.frk_version.to_string());
        encoded.pop();
        encoded
    }

    pub fn with_core_id(&self, value: impl Into<String>) -> Self {
        Self {
            base: ObjectBaseAad {
                core_id: value.into(),
                ..self.base.clone()
            },
            ..self.clone()
        }
    }
    pub fn with_object_id(&self, value: impl Into<String>) -> Self {
        Self {
            base: ObjectBaseAad {
                object_id: value.into(),
                ..self.base.clone()
            },
            ..self.clone()
        }
    }
    pub fn with_revision(&self, value: u64) -> Self {
        Self {
            base: ObjectBaseAad {
                revision: value,
                ..self.base.clone()
            },
            ..self.clone()
        }
    }
    pub fn with_kind(&self, value: ObjectKind) -> Self {
        Self {
            base: ObjectBaseAad {
                kind: value,
                ..self.base.clone()
            },
            ..self.clone()
        }
    }
    pub fn with_envelope_version(&self, value: u16) -> Self {
        Self {
            base: ObjectBaseAad {
                envelope_version: value,
                ..self.base.clone()
            },
            ..self.clone()
        }
    }
    pub fn with_object_key_epoch(&self, value: u32) -> Self {
        Self {
            base: ObjectBaseAad {
                object_key_epoch: value,
                ..self.base.clone()
            },
            ..self.clone()
        }
    }
    pub fn with_frk_version(&self, value: u32) -> Self {
        Self {
            frk_version: value,
            ..self.clone()
        }
    }
}

fn push_len_value(encoded: &mut Vec<u8>, name: &[u8], value: &str) {
    encoded.extend_from_slice(name);
    encoded.push(b'=');
    encoded.extend_from_slice(value.len().to_string().as_bytes());
    encoded.push(b':');
    encoded.extend_from_slice(value.as_bytes());
    encoded.push(0);
}

fn push_value(encoded: &mut Vec<u8>, name: &[u8], value: &str) {
    encoded.extend_from_slice(name);
    encoded.push(b'=');
    encoded.extend_from_slice(value.as_bytes());
    encoded.push(0);
}

fn push_bytes(encoded: &mut Vec<u8>, name: &[u8], value: &[u8]) {
    encoded.extend_from_slice(name);
    encoded.push(b'=');
    encoded.extend_from_slice(value.len().to_string().as_bytes());
    encoded.push(b':');
    encoded.extend_from_slice(value);
    encoded.push(0);
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MetadataFrameAad {
    base: ObjectBaseAad,
    chunking_version: u16,
}

impl MetadataFrameAad {
    pub fn new(base: ObjectBaseAad, chunking_version: u16) -> Result<Self, CryptoError> {
        base.validate()?;
        if chunking_version != 1 {
            return Err(CryptoError::InvalidAad("unsupported chunking version"));
        }
        Ok(Self {
            base,
            chunking_version,
        })
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut encoded = Vec::new();
        encoded.extend_from_slice(b"anima-corefs-metadata-frame-v1\0");
        push_bytes(&mut encoded, b"base", &self.base.to_bytes());
        push_value(&mut encoded, b"frame", "metadata");
        push_value(
            &mut encoded,
            b"chunking-version",
            &self.chunking_version.to_string(),
        );
        encoded.pop();
        encoded
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BodyFrameAad {
    base: ObjectBaseAad,
    metadata_frame_sha256: [u8; 32],
    chunk_index: u32,
    chunk_count: u32,
    plaintext_offset: u64,
    plaintext_length: u64,
    total_body_length: u64,
    final_chunk: bool,
}

impl BodyFrameAad {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        base: ObjectBaseAad,
        metadata_frame_sha256: [u8; 32],
        chunk_index: u32,
        chunk_count: u32,
        plaintext_offset: u64,
        plaintext_length: u64,
        total_body_length: u64,
        final_chunk: bool,
    ) -> Result<Self, CryptoError> {
        base.validate()?;
        if chunk_count == 0 || chunk_index >= chunk_count {
            return Err(CryptoError::InvalidAad("invalid body chunk index/count"));
        }
        if plaintext_offset
            .checked_add(plaintext_length)
            .map_or(true, |end| end > total_body_length)
        {
            return Err(CryptoError::InvalidAad("invalid body chunk bounds"));
        }
        if final_chunk != (chunk_index + 1 == chunk_count) {
            return Err(CryptoError::InvalidAad("invalid final-chunk flag"));
        }
        Ok(Self {
            base,
            metadata_frame_sha256,
            chunk_index,
            chunk_count,
            plaintext_offset,
            plaintext_length,
            total_body_length,
            final_chunk,
        })
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut encoded = Vec::new();
        encoded.extend_from_slice(b"anima-corefs-body-frame-v1\0");
        push_bytes(&mut encoded, b"base", &self.base.to_bytes());
        push_value(&mut encoded, b"frame", "body");
        push_bytes(
            &mut encoded,
            b"metadata-frame-sha256",
            &self.metadata_frame_sha256,
        );
        push_value(&mut encoded, b"chunk-index", &self.chunk_index.to_string());
        push_value(&mut encoded, b"chunk-count", &self.chunk_count.to_string());
        push_value(
            &mut encoded,
            b"plaintext-offset",
            &self.plaintext_offset.to_string(),
        );
        push_value(
            &mut encoded,
            b"plaintext-length",
            &self.plaintext_length.to_string(),
        );
        push_value(
            &mut encoded,
            b"total-body-length",
            &self.total_body_length.to_string(),
        );
        push_value(
            &mut encoded,
            b"final-chunk",
            if self.final_chunk { "true" } else { "false" },
        );
        encoded.pop();
        encoded
    }
}

#[derive(Clone)]
pub struct WrappedObjectDek {
    nonce: [u8; NONCE_LENGTH],
    ciphertext: Vec<u8>,
}

impl WrappedObjectDek {
    pub fn from_parts(
        algorithm: &str,
        envelope_version: u16,
        nonce: &[u8],
        ciphertext: Vec<u8>,
    ) -> Result<Self, CryptoError> {
        if algorithm != OBJECT_WRAP_ALGORITHM {
            return Err(CryptoError::UnsupportedAlgorithm(algorithm.to_owned()));
        }
        if envelope_version != OBJECT_KEY_ENVELOPE_VERSION {
            return Err(CryptoError::UnsupportedEnvelopeVersion(envelope_version));
        }
        let nonce = nonce
            .try_into()
            .map_err(|_| CryptoError::InvalidWrappedKey)?;
        if ciphertext.len() != KEY_LENGTH + 16 {
            return Err(CryptoError::InvalidWrappedKey);
        }
        Ok(Self { nonce, ciphertext })
    }

    pub fn algorithm(&self) -> &'static str {
        OBJECT_WRAP_ALGORITHM
    }
    pub fn envelope_version(&self) -> u16 {
        OBJECT_KEY_ENVELOPE_VERSION
    }
    pub fn nonce(&self) -> &[u8] {
        &self.nonce
    }
    pub fn ciphertext(&self) -> &[u8] {
        &self.ciphertext
    }
}

pub fn wrap_object_dek(
    object_dek: &SecretBytes,
    keys: &FrkSubkeys,
    aad: &ObjectKeyAad,
) -> Result<WrappedObjectDek, CryptoError> {
    aad.validate()?;
    if aad.frk_version != keys.frk_version {
        return Err(CryptoError::InvalidAad(
            "FRK version does not match key bundle",
        ));
    }
    let cipher = Aes256Gcm::new_from_slice(keys.object_wrap.as_slice())
        .map_err(|_| CryptoError::InvalidSecretLength)?;
    let mut nonce = [0_u8; NONCE_LENGTH];
    getrandom::getrandom(&mut nonce).map_err(|_| CryptoError::Randomness)?;
    let ciphertext = cipher
        .encrypt(
            Nonce::from_slice(&nonce),
            aes_gcm::aead::Payload {
                msg: object_dek.as_slice(),
                aad: &aad.to_bytes(),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    Ok(WrappedObjectDek { nonce, ciphertext })
}

pub fn unwrap_object_dek(
    keys: &FrkSubkeys,
    wrapped: &WrappedObjectDek,
    aad: &ObjectKeyAad,
) -> Result<SecretBytes, CryptoError> {
    aad.validate()?;
    if aad.frk_version != keys.frk_version {
        return Err(CryptoError::InvalidAad(
            "FRK version does not match key bundle",
        ));
    }
    let cipher = Aes256Gcm::new_from_slice(keys.object_wrap.as_slice())
        .map_err(|_| CryptoError::InvalidSecretLength)?;
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&wrapped.nonce),
            aes_gcm::aead::Payload {
                msg: &wrapped.ciphertext,
                aad: &aad.to_bytes(),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    SecretBytes::new(plaintext)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn aad(kind: ObjectKind) -> ObjectKeyAad {
        ObjectKeyAad::new("019f-core", "019f-object", 7, kind, 1, 2, 3).unwrap()
    }

    #[test]
    fn keyslot_aad_vectors_bind_immutable_metadata() {
        assert_eq!(
            manifest_keyslot_aad(
                "019f-core",
                "019f-owner",
                "filesystem-root",
                1,
                2,
                "full",
                Some(1),
                Some(3),
                "password",
            )
            .unwrap(),
            b"anima-keyslot-v1:core=019f-core:owner=019f-owner:purpose=filesystem-root:\
              version=1:generation=2:scope=full:frk-version=1:object-key-epoch=3:path=password"
                .to_vec(),
        );
        assert_eq!(
            soul_keyslot_aad("019f-core", "019f-owner", "memories", 1, 2, "password",).unwrap(),
            b"anima-soul-keyslot-v1:core=019f-core:owner=019f-owner:domain=memories:\
              version=1:generation=2:path=password"
                .to_vec(),
        );
    }

    #[test]
    fn frk_subkeys_are_deterministic_and_domain_separated() {
        let frk = SecretBytes::new(vec![0x42; 32]).unwrap();
        let first = derive_corefs_subkeys(&frk, 3).unwrap();
        let second = derive_corefs_subkeys(&frk, 3).unwrap();

        assert_eq!(first.frk_version(), 3);
        assert_eq!(
            first.object_wrap().as_slice(),
            second.object_wrap().as_slice()
        );
        assert_ne!(first.object_wrap().as_slice(), first.catalog().as_slice());
        assert_ne!(first.object_wrap().as_slice(), first.search().as_slice());
        assert_ne!(first.catalog().as_slice(), first.search().as_slice());
    }

    #[test]
    fn object_deks_are_random_32_byte_secrets() {
        let first = generate_object_dek().unwrap();
        let second = generate_object_dek().unwrap();

        assert_eq!(first.len(), 32);
        assert_eq!(second.len(), 32);
        assert_ne!(first.as_slice(), second.as_slice());
    }

    #[test]
    fn filesystem_root_credential_wrapper_roundtrips_and_binds_aad() {
        let root = generate_filesystem_root_key().unwrap();
        let wrapped = wrap_filesystem_root_key("credential", &root, b"root-aad").unwrap();
        let reopened = unwrap_filesystem_root_key("credential", &wrapped, b"root-aad").unwrap();
        assert_eq!(reopened.as_slice(), root.as_slice());
        assert!(unwrap_filesystem_root_key("credential", &wrapped, b"wrong-aad").is_err());
    }

    #[test]
    fn wrapped_object_dek_roundtrips_and_binds_all_aad_fields() {
        let frk = SecretBytes::new(vec![0x42; 32]).unwrap();
        let subkeys = derive_corefs_subkeys(&frk, 3).unwrap();
        let dek = SecretBytes::new(vec![0x24; 32]).unwrap();
        let base = aad(ObjectKind::KnowledgeSource);
        let wrapped = wrap_object_dek(&dek, &subkeys, &base).unwrap();

        assert_eq!(
            unwrap_object_dek(&subkeys, &wrapped, &base)
                .unwrap()
                .as_slice(),
            dek.as_slice(),
        );

        let mutations = [
            base.with_core_id("different-core"),
            base.with_object_id("different-object"),
            base.with_revision(8),
            base.with_kind(ObjectKind::GalleryAsset),
            base.with_envelope_version(2),
            base.with_object_key_epoch(3),
            base.with_frk_version(4),
        ];
        for changed in mutations {
            assert!(unwrap_object_dek(&subkeys, &wrapped, &changed).is_err());
        }
    }

    #[test]
    fn approved_v1_object_kinds_are_closed_and_parseable() {
        for kind in [
            "account-profile",
            "folder",
            "diary",
            "draft",
            "note",
            "thread",
            "message-segment",
            "gallery-asset",
            "attachment",
            "knowledge-source",
            "task",
            "preferences",
        ] {
            assert_eq!(ObjectKind::parse(kind).unwrap().as_str(), kind);
        }
        assert!(ObjectKind::parse("source").is_err());
        assert!(ObjectKind::parse("future-unregistered-kind").is_err());
    }

    #[test]
    fn typed_aad_has_a_stable_unambiguous_encoding() {
        assert_eq!(
            hex::encode(aad(ObjectKind::KnowledgeSource).to_bytes()),
            "616e696d612d636f726566732d6f626a6563742d6b65792d777261702d763100626173653d3134333a616e696d612d636f726566732d6f626a6563742d626173652d763100636f72652d69643d393a303139662d636f7265006f626a6563742d69643d31313a303139662d6f626a656374006b696e643d6b6e6f776c656467652d736f7572636500656e76656c6f70652d76657273696f6e3d31006f626a6563742d6b65792d65706f63683d32007265766973696f6e3d370066726b2d76657273696f6e3d33"
        );
    }

    #[test]
    fn metadata_and_body_aad_extend_the_same_base_without_frk_version() {
        let base = ObjectBaseAad::new(
            "019f-core",
            "019f-object",
            ObjectKind::KnowledgeSource,
            1,
            2,
            7,
        )
        .unwrap();
        let metadata = MetadataFrameAad::new(base.clone(), 1).unwrap();
        let body = BodyFrameAad::new(base.clone(), [0xAB; 32], 0, 1, 0, 12, 12, true).unwrap();
        assert!(metadata
            .to_bytes()
            .starts_with(b"anima-corefs-metadata-frame-v1\0"));
        assert!(body.to_bytes().starts_with(b"anima-corefs-body-frame-v1\0"));
        assert!(!metadata
            .to_bytes()
            .windows(11)
            .any(|window| window == b"frk-version"));
        assert!(!body
            .to_bytes()
            .windows(11)
            .any(|window| window == b"frk-version"));
        assert!(ObjectKeyAad::from_base(base, 3)
            .unwrap()
            .to_bytes()
            .windows(11)
            .any(|window| window == b"frk-version"));
    }

    #[test]
    fn secret_bytes_can_be_zeroized_before_drop() {
        let mut secret = SecretBytes::new(vec![0x55; 32]).unwrap();
        secret.zeroize_now();
        assert!(secret.as_slice().iter().all(|byte| *byte == 0));
    }
}
