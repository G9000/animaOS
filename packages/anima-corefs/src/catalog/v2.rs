//! Strict authoritative V2 catalog generations.

use std::collections::{BTreeMap, HashMap, HashSet};

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use hkdf::Hkdf;
use serde::{ser::SerializeSeq, Deserialize, Serialize, Serializer};
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::{CatalogError, MAX_CATALOG_PLAINTEXT_SIZE};
use crate::bounded::{
    bounded_json_preflight, json_to_vec as bounded_json_to_vec, BoundedJsonError,
};
use crate::crypto::{
    CryptoError, FrkSubkeys, ObjectKind, SecretBytes, WrappedObjectDek, KEY_LENGTH, NONCE_LENGTH,
};
use crate::folders::{ClientId, FolderOwner, FolderRole, PortableName};
use crate::id::OpaqueId;
use crate::policy::{AnimaAccess, LocalAnimaAccess, LocalFolderPolicy};

pub const CATALOG_GENERATION_SCHEMA_VERSION: u16 = 2;
pub const MAX_CATALOG_ENTRIES: usize = 50_000;
pub const MAX_CATALOG_DEPTH: usize = 64;

const V2_MAGIC: &[u8; 8] = b"ACATV2\0\0";
const V2_HEADER_SIZE: usize = 34;
const V2_TAG_LENGTH: usize = 16;
const V2_GENERATION_LABEL_PREFIX: &str = "anima-catalog-generation-v2:";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CatalogClientMetadata {
    values: BTreeMap<String, Value>,
}

impl CatalogClientMetadata {
    pub fn empty() -> Self {
        Self {
            values: BTreeMap::new(),
        }
    }

    pub fn new<I, K>(writer: &ClientId, entries: I) -> Result<Self, CatalogError>
    where
        I: IntoIterator<Item = (K, Value)>,
        K: Into<String>,
    {
        let mut values = BTreeMap::new();
        for (key, mut value) in entries {
            let key = key.into();
            validate_client_metadata_key(&key, Some(writer))?;
            canonicalize_value(&mut value);
            if values.insert(key, value).is_some() {
                return Err(CatalogError::InvalidFormat("duplicate client metadata key"));
            }
        }
        Ok(Self { values })
    }

    pub fn values(&self) -> &BTreeMap<String, Value> {
        &self.values
    }

    fn from_wire(mut values: BTreeMap<String, Value>) -> Result<Self, CatalogError> {
        let mut writer = None;
        for (key, value) in &mut values {
            let key_writer = client_metadata_writer(key)?;
            if writer
                .as_ref()
                .is_some_and(|existing: &ClientId| existing != &key_writer)
            {
                return Err(CatalogError::InvalidFormat(
                    "mixed client metadata namespaces",
                ));
            }
            writer = Some(key_writer);
            canonicalize_value(value);
        }
        Ok(Self { values })
    }
}

impl Default for CatalogClientMetadata {
    fn default() -> Self {
        Self::empty()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContentHash(String);

impl ContentHash {
    pub fn parse(value: &str) -> Result<Self, CatalogError> {
        if value.len() != 64
            || !value
                .as_bytes()
                .iter()
                .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
        {
            return Err(CatalogError::InvalidFormat(
                "content hash must be lowercase SHA-256 hex",
            ));
        }
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WrappedObjectDekRecord {
    frk_version: u32,
    object_key_epoch: u32,
    algorithm: String,
    envelope_version: u16,
    nonce: Vec<u8>,
    ciphertext: Vec<u8>,
}

impl WrappedObjectDekRecord {
    #[allow(clippy::too_many_arguments)]
    pub fn from_parts(
        frk_version: u32,
        object_key_epoch: u32,
        algorithm: &str,
        envelope_version: u16,
        nonce: &[u8],
        ciphertext: Vec<u8>,
    ) -> Result<Self, CatalogError> {
        if frk_version == 0 {
            return Err(CatalogError::InvalidFormat("FRK version must be positive"));
        }
        if object_key_epoch == 0 {
            return Err(CatalogError::InvalidFormat(
                "object key epoch must be positive",
            ));
        }
        WrappedObjectDek::from_parts(algorithm, envelope_version, nonce, ciphertext.clone())?;
        Ok(Self {
            frk_version,
            object_key_epoch,
            algorithm: algorithm.to_owned(),
            envelope_version,
            nonce: nonce.to_vec(),
            ciphertext,
        })
    }

    pub fn frk_version(&self) -> u32 {
        self.frk_version
    }

    pub fn object_key_epoch(&self) -> u32 {
        self.object_key_epoch
    }

    pub fn to_wrapped_object_dek(&self) -> Result<WrappedObjectDek, CatalogError> {
        Ok(WrappedObjectDek::from_parts(
            &self.algorithm,
            self.envelope_version,
            &self.nonce,
            self.ciphertext.clone(),
        )?)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TrashMetadata {
    trash_folder_id: OpaqueId,
    original_parent_id: OpaqueId,
    original_name: PortableName,
    trashed_at_ms: u64,
}

impl TrashMetadata {
    pub fn new(
        trash_folder_id: OpaqueId,
        original_parent_id: OpaqueId,
        original_name: PortableName,
        trashed_at_ms: u64,
    ) -> Result<Self, CatalogError> {
        if trashed_at_ms == 0 {
            return Err(CatalogError::InvalidFormat(
                "trash timestamp must be positive",
            ));
        }
        Ok(Self {
            trash_folder_id,
            original_parent_id,
            original_name,
            trashed_at_ms,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ObjectLifecycle {
    Live,
    Trashed(TrashMetadata),
    Tombstone {
        trash_folder_id: OpaqueId,
        deleted_at_ms: u64,
    },
}

impl ObjectLifecycle {
    pub fn tombstone(trash_folder_id: OpaqueId, deleted_at_ms: u64) -> Result<Self, CatalogError> {
        if deleted_at_ms == 0 {
            return Err(CatalogError::InvalidFormat(
                "deletion timestamp must be positive",
            ));
        }
        Ok(Self::Tombstone {
            trash_folder_id,
            deleted_at_ms,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CatalogCutoverMarker {
    epoch: u64,
}

impl CatalogCutoverMarker {
    pub(crate) fn new(epoch: u64) -> Result<Self, CatalogError> {
        if epoch == 0 {
            return Err(CatalogError::InvalidFormat(
                "cutover epoch must be positive",
            ));
        }
        Ok(Self { epoch })
    }

    pub fn epoch(&self) -> u64 {
        self.epoch
    }

    pub fn legacy_rollback_disabled(&self) -> bool {
        true
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CatalogObject {
    revision: u64,
    content_hash: ContentHash,
    kind: ObjectKind,
    wrapped_dek: WrappedObjectDekRecord,
    lifecycle: ObjectLifecycle,
}

impl CatalogObject {
    pub fn new(
        revision: u64,
        content_hash: ContentHash,
        kind: ObjectKind,
        wrapped_dek: WrappedObjectDekRecord,
        lifecycle: ObjectLifecycle,
    ) -> Result<Self, CatalogError> {
        if revision == 0 {
            return Err(CatalogError::InvalidFormat(
                "object revision must be positive",
            ));
        }
        if kind == ObjectKind::Folder {
            return Err(CatalogError::InvalidFormat(
                "folder kind cannot have object payload",
            ));
        }
        match &lifecycle {
            ObjectLifecycle::Trashed(metadata) if metadata.trashed_at_ms == 0 => {
                return Err(CatalogError::InvalidFormat(
                    "trash timestamp must be positive",
                ));
            }
            ObjectLifecycle::Tombstone { deleted_at_ms, .. } if *deleted_at_ms == 0 => {
                return Err(CatalogError::InvalidFormat(
                    "deletion timestamp must be positive",
                ));
            }
            ObjectLifecycle::Live
            | ObjectLifecycle::Trashed(_)
            | ObjectLifecycle::Tombstone { .. } => {}
        }
        Ok(Self {
            revision,
            content_hash,
            kind,
            wrapped_dek,
            lifecycle,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CatalogEntryCommon {
    stable_id: OpaqueId,
    parent_id: Option<OpaqueId>,
    name: PortableName,
    role: Option<FolderRole>,
    owner: FolderOwner,
    anima_access: AnimaAccess,
    policy_override: LocalFolderPolicy,
    client_metadata: CatalogClientMetadata,
}

impl CatalogEntryCommon {
    pub fn new(
        stable_id: OpaqueId,
        parent_id: Option<OpaqueId>,
        name: PortableName,
        owner: FolderOwner,
        anima_access: AnimaAccess,
    ) -> Self {
        let policy_override = if parent_id.is_none() {
            LocalFolderPolicy::new(Some(owner), LocalAnimaAccess::Allow(anima_access))
        } else {
            LocalFolderPolicy::inherit()
        };
        Self {
            stable_id,
            parent_id,
            name,
            role: None,
            owner,
            anima_access,
            policy_override,
            client_metadata: CatalogClientMetadata::empty(),
        }
    }

    pub fn with_client_metadata(mut self, client_metadata: CatalogClientMetadata) -> Self {
        self.client_metadata = client_metadata;
        self
    }

    pub fn stable_id(&self) -> &OpaqueId {
        &self.stable_id
    }

    pub fn parent_id(&self) -> Option<&OpaqueId> {
        self.parent_id.as_ref()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CatalogGenerationEntry {
    Folder(CatalogEntryCommon),
    Object(CatalogEntryCommon, Box<CatalogObject>),
}

impl CatalogGenerationEntry {
    pub fn folder(common: CatalogEntryCommon) -> Self {
        Self::Folder(common)
    }

    pub fn object(common: CatalogEntryCommon, object: CatalogObject) -> Self {
        Self::Object(common, Box::new(object))
    }

    pub fn is_folder(&self) -> bool {
        matches!(self, Self::Folder(_))
    }

    pub fn object_payload(&self) -> Option<&CatalogObject> {
        match self {
            Self::Folder(_) => None,
            Self::Object(_, object) => Some(object.as_ref()),
        }
    }

    fn common(&self) -> &CatalogEntryCommon {
        match self {
            Self::Folder(common) | Self::Object(common, _) => common,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CatalogGeneration {
    generation: u64,
    entries: Vec<CatalogGenerationEntry>,
    cutover_marker: Option<CatalogCutoverMarker>,
}

impl CatalogGeneration {
    pub fn new(
        generation: u64,
        mut entries: Vec<CatalogGenerationEntry>,
    ) -> Result<Self, CatalogError> {
        if entries.len() > MAX_CATALOG_ENTRIES {
            return Err(CatalogError::LimitExceeded("catalog entries"));
        }
        entries.sort_by(|left, right| left.common().stable_id.cmp(&right.common().stable_id));
        let value = Self {
            generation,
            entries,
            cutover_marker: None,
        };
        value.validate()?;
        Ok(value)
    }

    pub const fn schema_version(&self) -> u16 {
        CATALOG_GENERATION_SCHEMA_VERSION
    }

    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub fn entries(&self) -> &[CatalogGenerationEntry] {
        &self.entries
    }

    pub(crate) fn with_cutover_marker(
        mut self,
        cutover_marker: CatalogCutoverMarker,
    ) -> Result<Self, CatalogError> {
        self.cutover_marker = Some(cutover_marker);
        self.validate()?;
        Ok(self)
    }

    pub fn cutover_marker(&self) -> Option<&CatalogCutoverMarker> {
        self.cutover_marker.as_ref()
    }

    fn validate(&self) -> Result<(), CatalogError> {
        if self.generation == 0 {
            return Err(CatalogError::InvalidFormat("generation must be positive"));
        }
        let mut ids = HashSet::new();
        let mut by_id = HashMap::new();
        let mut roles = HashSet::new();
        let mut siblings = HashSet::new();
        for entry in &self.entries {
            let common = entry.common();
            if !ids.insert(common.stable_id.as_str()) {
                return Err(CatalogError::DuplicateStableId(
                    common.stable_id.as_str().to_owned(),
                ));
            }
            by_id.insert(common.stable_id.as_str(), entry);
            if !entry.is_folder() && common.role.is_some() {
                return Err(CatalogError::InvalidFormat("catalog roles are folder-only"));
            }
            if let Some(role) = &common.role {
                if !roles.insert(role.as_str()) {
                    return Err(CatalogError::InvalidFormat("duplicate catalog role"));
                }
            }
            let parent = common.parent_id.as_ref().map(OpaqueId::as_str);
            if !siblings.insert((parent, common.name.as_str())) {
                return Err(CatalogError::InvalidFormat(
                    "duplicate sibling catalog name",
                ));
            }
        }

        let roots: Vec<_> = self
            .entries
            .iter()
            .filter(|entry| entry.common().parent_id.is_none())
            .collect();
        if roots.len() != 1 || !roots[0].is_folder() {
            return Err(CatalogError::InvalidFormat(
                "catalog must contain exactly one folder root",
            ));
        }

        for entry in &self.entries {
            let common = entry.common();
            if let Some(parent_id) = &common.parent_id {
                if parent_id == &common.stable_id {
                    return Err(CatalogError::InvalidFormat("catalog entry self-parent"));
                }
                let parent = by_id
                    .get(parent_id.as_str())
                    .ok_or(CatalogError::InvalidFormat("catalog entry orphan"))?;
                if !parent.is_folder() {
                    return Err(CatalogError::InvalidFormat(
                        "catalog entry parent is not a folder",
                    ));
                }
            }
        }

        validate_catalog_graph(&self.entries, &by_id)?;
        validate_lifecycle_references(&self.entries, &by_id)?;
        validate_effective_policies(&self.entries, &by_id)?;
        Ok(())
    }
}

fn validate_catalog_graph<'a>(
    entries: &'a [CatalogGenerationEntry],
    by_id: &HashMap<&'a str, &'a CatalogGenerationEntry>,
) -> Result<(), CatalogError> {
    let mut depths = HashMap::<&str, usize>::new();
    let mut visiting = HashSet::<&str>::new();
    for start in entries {
        if depths.contains_key(start.common().stable_id.as_str()) {
            continue;
        }
        let mut chain = Vec::new();
        let mut current = start;
        while !depths.contains_key(current.common().stable_id.as_str()) {
            let id = current.common().stable_id.as_str();
            if !visiting.insert(id) {
                return Err(CatalogError::InvalidFormat("catalog parent cycle"));
            }
            chain.push(current);
            let Some(parent_id) = current.common().parent_id.as_ref() else {
                break;
            };
            current = by_id
                .get(parent_id.as_str())
                .expect("catalog parent existence validated");
        }
        for entry in chain.into_iter().rev() {
            let common = entry.common();
            let depth = match common.parent_id.as_ref() {
                Some(parent_id) => depths
                    .get(parent_id.as_str())
                    .copied()
                    .expect("parent depth resolved before child")
                    .checked_add(1)
                    .ok_or(CatalogError::LimitExceeded("catalog depth"))?,
                None => 0,
            };
            if depth > MAX_CATALOG_DEPTH {
                return Err(CatalogError::LimitExceeded("catalog depth"));
            }
            depths.insert(common.stable_id.as_str(), depth);
            visiting.remove(common.stable_id.as_str());
        }
    }
    Ok(())
}

fn validate_lifecycle_references<'a>(
    entries: &'a [CatalogGenerationEntry],
    by_id: &HashMap<&'a str, &'a CatalogGenerationEntry>,
) -> Result<(), CatalogError> {
    let require_folder = |id: &OpaqueId| -> Result<&CatalogGenerationEntry, CatalogError> {
        let entry = by_id
            .get(id.as_str())
            .copied()
            .ok_or(CatalogError::InvalidFormat("lifecycle folder is missing"))?;
        if !entry.is_folder() {
            return Err(CatalogError::InvalidFormat(
                "lifecycle reference is not a folder",
            ));
        }
        Ok(entry)
    };

    for entry in entries {
        let CatalogGenerationEntry::Object(common, object) = entry else {
            continue;
        };
        match &object.lifecycle {
            ObjectLifecycle::Live => {}
            ObjectLifecycle::Trashed(metadata) => {
                require_folder(&metadata.trash_folder_id)?;
                require_folder(&metadata.original_parent_id)?;
                if metadata.trash_folder_id == metadata.original_parent_id {
                    return Err(CatalogError::InvalidFormat(
                        "trash and original folders must differ",
                    ));
                }
                if common.parent_id.as_ref() != Some(&metadata.trash_folder_id) {
                    return Err(CatalogError::InvalidFormat(
                        "trashed object is outside its trash folder",
                    ));
                }
            }
            ObjectLifecycle::Tombstone {
                trash_folder_id, ..
            } => {
                require_folder(trash_folder_id)?;
                if common.parent_id.as_ref() != Some(trash_folder_id) {
                    return Err(CatalogError::InvalidFormat(
                        "tombstone is outside its trash folder",
                    ));
                }
            }
        }
    }
    Ok(())
}

#[derive(Clone, Copy)]
struct ResolvedCatalogPolicy {
    owner: FolderOwner,
    access: AnimaAccess,
    denied: bool,
}

fn validate_effective_policies<'a>(
    entries: &'a [CatalogGenerationEntry],
    by_id: &HashMap<&'a str, &'a CatalogGenerationEntry>,
) -> Result<(), CatalogError> {
    let mut resolved = HashMap::<&str, ResolvedCatalogPolicy>::new();
    for start in entries {
        if resolved.contains_key(start.common().stable_id.as_str()) {
            continue;
        }
        let mut chain = Vec::new();
        let mut current = start;
        while !resolved.contains_key(current.common().stable_id.as_str()) {
            chain.push(current);
            let Some(parent_id) = current.common().parent_id.as_ref() else {
                break;
            };
            current = by_id
                .get(parent_id.as_str())
                .expect("catalog parent existence validated");
        }
        let mut parent_policy = resolved.get(current.common().stable_id.as_str()).copied();
        for entry in chain.into_iter().rev() {
            let common = entry.common();
            let policy = match parent_policy {
                Some(parent) => resolve_child_policy(parent, common)?,
                None => resolve_root_policy(common)?,
            };
            resolved.insert(common.stable_id.as_str(), policy);
            parent_policy = Some(policy);
        }
    }
    Ok(())
}

fn resolve_root_policy(common: &CatalogEntryCommon) -> Result<ResolvedCatalogPolicy, CatalogError> {
    let local = common.policy_override;
    let owner = local
        .owner()
        .ok_or(CatalogError::InvalidFormat("root policy owner"))?;
    let (access, denied) = match local.anima_access() {
        LocalAnimaAccess::Allow(access) => (access, false),
        LocalAnimaAccess::Deny => (AnimaAccess::None, true),
        LocalAnimaAccess::Inherit => {
            return Err(CatalogError::InvalidFormat("root policy access"));
        }
    };
    let resolved = ResolvedCatalogPolicy {
        owner,
        access,
        denied,
    };
    ensure_effective_policy_matches(common, resolved)?;
    Ok(resolved)
}

fn resolve_child_policy(
    parent: ResolvedCatalogPolicy,
    common: &CatalogEntryCommon,
) -> Result<ResolvedCatalogPolicy, CatalogError> {
    let local = common.policy_override;
    let owner = local.owner().unwrap_or(parent.owner);
    let denied = parent.denied || local.anima_access() == LocalAnimaAccess::Deny;
    let access = if denied {
        AnimaAccess::None
    } else {
        match local.anima_access() {
            LocalAnimaAccess::Inherit => parent.access,
            LocalAnimaAccess::Allow(access) => access,
            LocalAnimaAccess::Deny => AnimaAccess::None,
        }
    };
    let resolved = ResolvedCatalogPolicy {
        owner,
        access,
        denied,
    };
    ensure_effective_policy_matches(common, resolved)?;
    Ok(resolved)
}

fn ensure_effective_policy_matches(
    common: &CatalogEntryCommon,
    resolved: ResolvedCatalogPolicy,
) -> Result<(), CatalogError> {
    if common.owner != resolved.owner || common.anima_access != resolved.access {
        return Err(CatalogError::InvalidFormat(
            "effective policy does not match inheritance",
        ));
    }
    Ok(())
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WireCatalogGeneration {
    schema_version: u16,
    generation: u64,
    cutover_marker: Option<WireCutoverMarker>,
    entries: Vec<WireEntry>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WireEntry {
    stable_id: String,
    parent_id: Option<String>,
    name: String,
    role: Option<String>,
    owner: String,
    anima_access: String,
    policy_override: WirePolicyOverride,
    client_metadata: BTreeMap<String, Value>,
    payload: WirePayload,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WirePolicyOverride {
    owner: Option<String>,
    anima_access: String,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
enum WirePayload {
    Folder,
    Object {
        revision: u64,
        content_hash: String,
        object_kind: String,
        wrapped_dek: Box<WireWrappedDek>,
        lifecycle: WireLifecycle,
    },
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WireWrappedDek {
    frk_version: u32,
    object_key_epoch: u32,
    algorithm: String,
    envelope_version: u16,
    nonce: String,
    ciphertext: String,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "state", rename_all = "kebab-case", deny_unknown_fields)]
enum WireLifecycle {
    Live,
    Trashed {
        trash_folder_id: String,
        original_parent_id: String,
        original_name: String,
        trashed_at_ms: u64,
    },
    Tombstone {
        trash_folder_id: String,
        deleted_at_ms: u64,
    },
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct WireCutoverMarker {
    legacy_rollback_disabled: bool,
    epoch: u64,
}

pub fn encode_catalog_generation(payload: &CatalogGeneration) -> Result<Vec<u8>, CatalogError> {
    payload.validate()?;
    let wire = WireCatalogGenerationRef(payload);
    bounded_json_preflight(&wire, MAX_CATALOG_PLAINTEXT_SIZE).map_err(map_bounded_error)?;
    bounded_json_to_vec(&wire, MAX_CATALOG_PLAINTEXT_SIZE).map_err(map_bounded_error)
}

fn decode_catalog_generation(encoded: &[u8]) -> Result<CatalogGeneration, CatalogError> {
    if encoded.len() > MAX_CATALOG_PLAINTEXT_SIZE {
        return Err(CatalogError::LimitExceeded("catalog plaintext"));
    }
    let wire: WireCatalogGeneration = serde_json::from_slice(encoded)?;
    if wire.schema_version != CATALOG_GENERATION_SCHEMA_VERSION {
        return Err(CatalogError::UnsupportedVersion(wire.schema_version));
    }
    let payload = from_wire(wire)?;
    if encode_catalog_generation(&payload)? != encoded {
        return Err(CatalogError::InvalidFormat("non-canonical catalog"));
    }
    Ok(payload)
}

/// Validates untrusted plaintext without promoting it into a catalog value.
///
/// Only authenticated decryption returns `CatalogGeneration`; this prevents
/// callers from minting reserved roles, policy overrides, or cutover state by
/// submitting otherwise canonical JSON.
pub fn validate_catalog_generation_encoding(encoded: &[u8]) -> Result<(), CatalogError> {
    decode_catalog_generation(encoded).map(drop)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CatalogGenerationEnvelopeInfo {
    schema_version: u16,
    generation: u64,
}

impl CatalogGenerationEnvelopeInfo {
    pub fn schema_version(&self) -> u16 {
        self.schema_version
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }
}

pub fn encrypt_catalog_generation(
    keys: &FrkSubkeys,
    core_id: &str,
    payload: &CatalogGeneration,
) -> Result<Vec<u8>, CatalogError> {
    validate_v2_core_id(core_id)?;
    let plaintext = encode_catalog_generation(payload)?;
    let generation_key = v2_generation_key(keys.catalog(), payload.generation)?;
    let cipher = Aes256Gcm::new_from_slice(generation_key.as_slice())
        .map_err(|_| CryptoError::Derivation)?;
    let mut nonce = [0_u8; NONCE_LENGTH];
    getrandom::getrandom(&mut nonce).map_err(|_| CryptoError::Randomness)?;
    let ciphertext = cipher
        .encrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &plaintext,
                aad: &v2_catalog_aad(core_id, payload.generation),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    let ciphertext_length = u32::try_from(ciphertext.len())
        .map_err(|_| CatalogError::LimitExceeded("catalog ciphertext"))?;
    let mut output = Vec::with_capacity(V2_HEADER_SIZE + ciphertext.len());
    output.extend_from_slice(V2_MAGIC);
    output.extend_from_slice(&CATALOG_GENERATION_SCHEMA_VERSION.to_le_bytes());
    output.extend_from_slice(&payload.generation.to_le_bytes());
    output.extend_from_slice(&nonce);
    output.extend_from_slice(&ciphertext_length.to_le_bytes());
    output.extend_from_slice(&ciphertext);
    Ok(output)
}

pub fn decrypt_catalog_generation(
    keys: &FrkSubkeys,
    core_id: &str,
    encoded: &[u8],
) -> Result<CatalogGeneration, CatalogError> {
    validate_v2_core_id(core_id)?;
    let header = parse_v2_header(encoded)?;
    let generation_key = v2_generation_key(keys.catalog(), header.info.generation)?;
    let cipher = Aes256Gcm::new_from_slice(generation_key.as_slice())
        .map_err(|_| CryptoError::Derivation)?;
    let plaintext = cipher
        .decrypt(
            Nonce::from_slice(&header.nonce),
            Payload {
                msg: &encoded[V2_HEADER_SIZE..],
                aad: &v2_catalog_aad(core_id, header.info.generation),
            },
        )
        .map_err(|_| CryptoError::Authentication)?;
    let payload = decode_catalog_generation(&plaintext)?;
    if payload.generation != header.info.generation {
        return Err(CatalogError::InvalidFormat("catalog generation mismatch"));
    }
    Ok(payload)
}

pub fn inspect_catalog_generation_envelope(
    encoded: &[u8],
) -> Result<CatalogGenerationEnvelopeInfo, CatalogError> {
    Ok(parse_v2_header(encoded)?.info)
}

pub fn catalog_generation_physical_name(encoded: &[u8]) -> Result<String, CatalogError> {
    let info = inspect_catalog_generation_envelope(encoded)?;
    let digest: [u8; 32] = Sha256::digest(encoded).into();
    Ok(format!(
        "catalog-{:020}-{}.acore",
        info.generation,
        super::hex_bytes(&digest)
    ))
}

#[derive(Clone, Copy)]
struct ParsedV2Header {
    info: CatalogGenerationEnvelopeInfo,
    nonce: [u8; NONCE_LENGTH],
}

fn parse_v2_header(encoded: &[u8]) -> Result<ParsedV2Header, CatalogError> {
    if encoded.len() < V2_HEADER_SIZE {
        return Err(CatalogError::InvalidFormat("truncated V2 catalog header"));
    }
    if &encoded[..8] != V2_MAGIC {
        return Err(CatalogError::InvalidFormat("V2 catalog magic"));
    }
    let schema_version = u16::from_le_bytes(encoded[8..10].try_into().expect("fixed slice"));
    if schema_version != CATALOG_GENERATION_SCHEMA_VERSION {
        return Err(CatalogError::UnsupportedVersion(schema_version));
    }
    let generation = u64::from_le_bytes(encoded[10..18].try_into().expect("fixed slice"));
    if generation == 0 {
        return Err(CatalogError::InvalidFormat("generation must be positive"));
    }
    let nonce = encoded[18..30].try_into().expect("fixed slice");
    let ciphertext_length =
        u32::from_le_bytes(encoded[30..34].try_into().expect("fixed slice")) as usize;
    if !(V2_TAG_LENGTH..=MAX_CATALOG_PLAINTEXT_SIZE + V2_TAG_LENGTH).contains(&ciphertext_length) {
        return Err(CatalogError::LimitExceeded("catalog ciphertext"));
    }
    let total_length = V2_HEADER_SIZE
        .checked_add(ciphertext_length)
        .ok_or(CatalogError::LimitExceeded("catalog envelope"))?;
    if encoded.len() != total_length {
        return Err(CatalogError::InvalidFormat("catalog ciphertext length"));
    }
    Ok(ParsedV2Header {
        info: CatalogGenerationEnvelopeInfo {
            schema_version,
            generation,
        },
        nonce,
    })
}

fn validate_v2_core_id(core_id: &str) -> Result<(), CatalogError> {
    if core_id.is_empty() || core_id.len() > u32::MAX as usize {
        return Err(CatalogError::InvalidFormat("core ID"));
    }
    Ok(())
}

fn v2_catalog_aad(core_id: &str, generation: u64) -> Vec<u8> {
    let mut aad = b"anima-corefs-authoritative-catalog-v2\0".to_vec();
    aad.extend_from_slice(&(core_id.len() as u32).to_le_bytes());
    aad.extend_from_slice(core_id.as_bytes());
    aad.extend_from_slice(&generation.to_le_bytes());
    aad.extend_from_slice(&CATALOG_GENERATION_SCHEMA_VERSION.to_le_bytes());
    aad
}

fn v2_generation_key(
    catalog_key: &SecretBytes,
    generation: u64,
) -> Result<SecretBytes, CatalogError> {
    let label = format!("{V2_GENERATION_LABEL_PREFIX}{generation}");
    let hkdf = Hkdf::<Sha256>::new(None, catalog_key.as_slice());
    let mut output = vec![0_u8; KEY_LENGTH];
    hkdf.expand(label.as_bytes(), &mut output)
        .map_err(|_| CryptoError::Derivation)?;
    Ok(SecretBytes::new(output)?)
}

struct WireCatalogGenerationRef<'a>(&'a CatalogGeneration);

impl Serialize for WireCatalogGenerationRef<'_> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        use serde::ser::SerializeStruct;

        let catalog = self.0;
        let mut state = serializer.serialize_struct("WireCatalogGeneration", 4)?;
        state.serialize_field("schemaVersion", &CATALOG_GENERATION_SCHEMA_VERSION)?;
        state.serialize_field("generation", &catalog.generation)?;
        state.serialize_field(
            "cutoverMarker",
            &catalog
                .cutover_marker
                .as_ref()
                .map(|marker| WireCutoverMarkerRef {
                    legacy_rollback_disabled: true,
                    epoch: marker.epoch,
                }),
        )?;
        state.serialize_field("entries", &WireEntriesRef(&catalog.entries))?;
        state.end()
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WireCutoverMarkerRef {
    legacy_rollback_disabled: bool,
    epoch: u64,
}

struct WireEntriesRef<'a>(&'a [CatalogGenerationEntry]);

impl Serialize for WireEntriesRef<'_> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let mut sequence = serializer.serialize_seq(Some(self.0.len()))?;
        for entry in self.0 {
            sequence.serialize_element(&WireEntryRef(entry))?;
        }
        sequence.end()
    }
}

struct WireEntryRef<'a>(&'a CatalogGenerationEntry);

impl Serialize for WireEntryRef<'_> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        use serde::ser::SerializeStruct;

        let entry = self.0;
        let common = entry.common();
        let mut state = serializer.serialize_struct("WireEntry", 9)?;
        state.serialize_field("stableId", common.stable_id.as_str())?;
        state.serialize_field("parentId", &common.parent_id.as_ref().map(OpaqueId::as_str))?;
        state.serialize_field("name", common.name.as_str())?;
        state.serialize_field("role", &common.role.as_ref().map(FolderRole::as_str))?;
        state.serialize_field("owner", owner_name(common.owner))?;
        state.serialize_field("animaAccess", access_name(common.anima_access))?;
        state.serialize_field(
            "policyOverride",
            &WirePolicyOverrideRef {
                owner: common.policy_override.owner().map(owner_name),
                anima_access: local_access_name(common.policy_override.anima_access()),
            },
        )?;
        state.serialize_field("clientMetadata", &common.client_metadata.values)?;
        state.serialize_field("payload", &WirePayloadRef(entry))?;
        state.end()
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WirePolicyOverrideRef {
    owner: Option<&'static str>,
    anima_access: &'static str,
}

struct WirePayloadRef<'a>(&'a CatalogGenerationEntry);

impl Serialize for WirePayloadRef<'_> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        use serde::ser::SerializeStruct;

        match self.0 {
            CatalogGenerationEntry::Folder(_) => {
                let mut state = serializer.serialize_struct("WirePayload", 1)?;
                state.serialize_field("kind", "folder")?;
                state.end()
            }
            CatalogGenerationEntry::Object(_, object) => {
                let mut state = serializer.serialize_struct("WirePayload", 6)?;
                state.serialize_field("kind", "object")?;
                state.serialize_field("revision", &object.revision)?;
                state.serialize_field("content_hash", object.content_hash.as_str())?;
                state.serialize_field("object_kind", object.kind.as_str())?;
                state.serialize_field(
                    "wrapped_dek",
                    &WireWrappedDekRef {
                        frk_version: object.wrapped_dek.frk_version,
                        object_key_epoch: object.wrapped_dek.object_key_epoch,
                        algorithm: &object.wrapped_dek.algorithm,
                        envelope_version: object.wrapped_dek.envelope_version,
                        nonce: Base64BytesRef(&object.wrapped_dek.nonce),
                        ciphertext: Base64BytesRef(&object.wrapped_dek.ciphertext),
                    },
                )?;
                state.serialize_field("lifecycle", &WireLifecycleRef(&object.lifecycle))?;
                state.end()
            }
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WireWrappedDekRef<'a> {
    frk_version: u32,
    object_key_epoch: u32,
    algorithm: &'a str,
    envelope_version: u16,
    nonce: Base64BytesRef<'a>,
    ciphertext: Base64BytesRef<'a>,
}

struct Base64BytesRef<'a>(&'a [u8]);

impl Serialize for Base64BytesRef<'_> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let mut encoded = [0_u8; 64];
        let length = BASE64
            .encode_slice(self.0, &mut encoded)
            .map_err(serde::ser::Error::custom)?;
        let encoded = std::str::from_utf8(&encoded[..length]).expect("base64 is ASCII");
        serializer.serialize_str(encoded)
    }
}

struct WireLifecycleRef<'a>(&'a ObjectLifecycle);

impl Serialize for WireLifecycleRef<'_> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        use serde::ser::SerializeStruct;

        match self.0 {
            ObjectLifecycle::Live => {
                let mut state = serializer.serialize_struct("WireLifecycle", 1)?;
                state.serialize_field("state", "live")?;
                state.end()
            }
            ObjectLifecycle::Trashed(metadata) => {
                let mut state = serializer.serialize_struct("WireLifecycle", 6)?;
                state.serialize_field("state", "trashed")?;
                state.serialize_field("trash_folder_id", metadata.trash_folder_id.as_str())?;
                state
                    .serialize_field("original_parent_id", metadata.original_parent_id.as_str())?;
                state.serialize_field("original_name", metadata.original_name.as_str())?;
                state.serialize_field("trashed_at_ms", &metadata.trashed_at_ms)?;
                state.end()
            }
            ObjectLifecycle::Tombstone {
                trash_folder_id,
                deleted_at_ms,
            } => {
                let mut state = serializer.serialize_struct("WireLifecycle", 3)?;
                state.serialize_field("state", "tombstone")?;
                state.serialize_field("trash_folder_id", trash_folder_id.as_str())?;
                state.serialize_field("deleted_at_ms", deleted_at_ms)?;
                state.end()
            }
        }
    }
}

fn from_wire(wire: WireCatalogGeneration) -> Result<CatalogGeneration, CatalogError> {
    if wire.entries.len() > MAX_CATALOG_ENTRIES {
        return Err(CatalogError::LimitExceeded("catalog entries"));
    }
    let generation = wire.generation;
    let cutover_marker = wire
        .cutover_marker
        .map(|marker| {
            if !marker.legacy_rollback_disabled {
                return Err(CatalogError::InvalidFormat(
                    "cutover marker must disable legacy rollback",
                ));
            }
            CatalogCutoverMarker::new(marker.epoch)
        })
        .transpose()?;
    let mut entries = Vec::with_capacity(wire.entries.len());
    for entry in wire.entries {
        let role = entry.role.map(|value| parse_role(&value)).transpose()?;
        let policy_override = LocalFolderPolicy::new(
            entry
                .policy_override
                .owner
                .map(|value| parse_owner(&value))
                .transpose()?,
            parse_local_access(&entry.policy_override.anima_access)?,
        );
        let common = CatalogEntryCommon {
            stable_id: OpaqueId::parse(&entry.stable_id)
                .map_err(|_| CatalogError::InvalidFormat("stable ID"))?,
            parent_id: entry
                .parent_id
                .map(|value| {
                    OpaqueId::parse(&value)
                        .map_err(|_| CatalogError::InvalidFormat("parent stable ID"))
                })
                .transpose()?,
            name: PortableName::parse(&entry.name)
                .map_err(|_| CatalogError::InvalidFormat("portable name"))?,
            role,
            owner: parse_owner(&entry.owner)?,
            anima_access: parse_access(&entry.anima_access)?,
            policy_override,
            client_metadata: CatalogClientMetadata::from_wire(entry.client_metadata)?,
        };
        let decoded = match entry.payload {
            WirePayload::Folder => CatalogGenerationEntry::folder(common),
            WirePayload::Object {
                revision,
                content_hash,
                object_kind,
                wrapped_dek,
                lifecycle,
            } => {
                let wrapped_dek = WrappedObjectDekRecord::from_parts(
                    wrapped_dek.frk_version,
                    wrapped_dek.object_key_epoch,
                    &wrapped_dek.algorithm,
                    wrapped_dek.envelope_version,
                    &BASE64
                        .decode(wrapped_dek.nonce)
                        .map_err(|_| CatalogError::InvalidFormat("wrapped DEK nonce"))?,
                    BASE64
                        .decode(wrapped_dek.ciphertext)
                        .map_err(|_| CatalogError::InvalidFormat("wrapped DEK ciphertext"))?,
                )?;
                CatalogGenerationEntry::object(
                    common,
                    CatalogObject::new(
                        revision,
                        ContentHash::parse(&content_hash)?,
                        ObjectKind::parse(&object_kind)?,
                        wrapped_dek,
                        lifecycle_from_wire(lifecycle)?,
                    )?,
                )
            }
        };
        entries.push(decoded);
    }
    let catalog = CatalogGeneration::new(generation, entries)?;
    match cutover_marker {
        Some(marker) => catalog.with_cutover_marker(marker),
        None => Ok(catalog),
    }
}

fn lifecycle_from_wire(value: WireLifecycle) -> Result<ObjectLifecycle, CatalogError> {
    match value {
        WireLifecycle::Live => Ok(ObjectLifecycle::Live),
        WireLifecycle::Trashed {
            trash_folder_id,
            original_parent_id,
            original_name,
            trashed_at_ms,
        } => Ok(ObjectLifecycle::Trashed(TrashMetadata::new(
            OpaqueId::parse(&trash_folder_id)
                .map_err(|_| CatalogError::InvalidFormat("trash folder ID"))?,
            OpaqueId::parse(&original_parent_id)
                .map_err(|_| CatalogError::InvalidFormat("original parent ID"))?,
            PortableName::parse(&original_name)
                .map_err(|_| CatalogError::InvalidFormat("original portable name"))?,
            trashed_at_ms,
        )?)),
        WireLifecycle::Tombstone {
            trash_folder_id,
            deleted_at_ms,
        } => ObjectLifecycle::tombstone(
            OpaqueId::parse(&trash_folder_id)
                .map_err(|_| CatalogError::InvalidFormat("trash folder ID"))?,
            deleted_at_ms,
        ),
    }
}

fn parse_role(value: &str) -> Result<FolderRole, CatalogError> {
    FolderRole::parse_existing(value).map_err(|_| CatalogError::InvalidFormat("folder role"))
}

fn validate_client_metadata_key(
    value: &str,
    expected_writer: Option<&ClientId>,
) -> Result<(), CatalogError> {
    let client = client_metadata_writer(value)?;
    if expected_writer.is_some_and(|writer| writer != &client) {
        return Err(CatalogError::InvalidFormat("client metadata authority"));
    }
    Ok(())
}

fn client_metadata_writer(value: &str) -> Result<ClientId, CatalogError> {
    let mut parts = value.split(':');
    if parts.next() != Some("client") {
        return Err(CatalogError::InvalidFormat("client metadata namespace"));
    }
    let client = parts
        .next()
        .ok_or(CatalogError::InvalidFormat("client metadata namespace"))?;
    let key = parts
        .next()
        .ok_or(CatalogError::InvalidFormat("client metadata namespace"))?;
    if key.is_empty() || key.chars().any(char::is_control) || parts.next().is_some() {
        return Err(CatalogError::InvalidFormat("client metadata namespace"));
    }
    ClientId::parse(client).map_err(|_| CatalogError::InvalidFormat("client metadata client ID"))
}

fn owner_name(value: FolderOwner) -> &'static str {
    match value {
        FolderOwner::User => "user",
        FolderOwner::Anima => "anima",
        FolderOwner::Shared => "shared",
    }
}

fn parse_owner(value: &str) -> Result<FolderOwner, CatalogError> {
    match value {
        "user" => Ok(FolderOwner::User),
        "anima" => Ok(FolderOwner::Anima),
        "shared" => Ok(FolderOwner::Shared),
        _ => Err(CatalogError::InvalidFormat("folder owner")),
    }
}

fn access_name(value: AnimaAccess) -> &'static str {
    match value {
        AnimaAccess::None => "none",
        AnimaAccess::Read => "read",
        AnimaAccess::Write => "write",
        AnimaAccess::Manage => "manage",
    }
}

fn parse_access(value: &str) -> Result<AnimaAccess, CatalogError> {
    match value {
        "none" => Ok(AnimaAccess::None),
        "read" => Ok(AnimaAccess::Read),
        "write" => Ok(AnimaAccess::Write),
        "manage" => Ok(AnimaAccess::Manage),
        _ => Err(CatalogError::InvalidFormat("ANIMA access")),
    }
}

fn local_access_name(value: LocalAnimaAccess) -> &'static str {
    match value {
        LocalAnimaAccess::Inherit => "inherit",
        LocalAnimaAccess::Deny => "deny",
        LocalAnimaAccess::Allow(AnimaAccess::None) => "allow:none",
        LocalAnimaAccess::Allow(AnimaAccess::Read) => "allow:read",
        LocalAnimaAccess::Allow(AnimaAccess::Write) => "allow:write",
        LocalAnimaAccess::Allow(AnimaAccess::Manage) => "allow:manage",
    }
}

fn parse_local_access(value: &str) -> Result<LocalAnimaAccess, CatalogError> {
    match value {
        "inherit" => Ok(LocalAnimaAccess::Inherit),
        "deny" => Ok(LocalAnimaAccess::Deny),
        value if value.starts_with("allow:") => {
            Ok(LocalAnimaAccess::Allow(parse_access(&value[6..])?))
        }
        _ => Err(CatalogError::InvalidFormat("local ANIMA access")),
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

fn map_bounded_error(error: BoundedJsonError) -> CatalogError {
    match error {
        BoundedJsonError::LimitExceeded => CatalogError::LimitExceeded("catalog plaintext"),
        BoundedJsonError::Json(error) => CatalogError::Json(error),
    }
}

#[cfg(test)]
mod tests {
    use crate::crypto::{derive_corefs_subkeys, SecretBytes};
    use crate::folders::{FolderOwner, PortableName};
    use crate::id::OpaqueId;
    use crate::policy::{AnimaAccess, LocalAnimaAccess, LocalFolderPolicy};

    use super::{
        decode_catalog_generation, encode_catalog_generation, v2_generation_key,
        CatalogCutoverMarker, CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry,
    };

    #[test]
    fn v2_generation_keys_are_domain_separated_from_v1() {
        let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), 1).unwrap();

        let v1 = crate::catalog::generation_key(keys.catalog(), 7).unwrap();
        let v2 = v2_generation_key(keys.catalog(), 7).unwrap();

        assert_ne!(v1.as_slice(), v2.as_slice());
    }

    #[test]
    fn authenticated_existing_policy_overrides_roundtrip() {
        let root_id = OpaqueId::parse("01J00000000000000000000000").unwrap();
        let mut child = CatalogEntryCommon::new(
            OpaqueId::parse("01J00000000000000000000001").unwrap(),
            Some(root_id.clone()),
            PortableName::parse("Shared").unwrap(),
            FolderOwner::Anima,
            AnimaAccess::Manage,
        );
        child.policy_override = LocalFolderPolicy::new(
            Some(FolderOwner::Anima),
            LocalAnimaAccess::Allow(AnimaAccess::Manage),
        );
        let catalog = CatalogGeneration::new(
            1,
            vec![
                CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                    root_id,
                    None,
                    PortableName::parse("Core").unwrap(),
                    FolderOwner::User,
                    AnimaAccess::Write,
                )),
                CatalogGenerationEntry::folder(child),
            ],
        )
        .unwrap();

        let encoded = encode_catalog_generation(&catalog).unwrap();
        assert_eq!(decode_catalog_generation(&encoded).unwrap(), catalog);
    }

    #[test]
    fn coordinator_only_cutover_marker_roundtrips_and_is_strict() {
        assert!(CatalogCutoverMarker::new(0).is_err());
        let catalog = CatalogGeneration::new(
            1,
            vec![CatalogGenerationEntry::folder(CatalogEntryCommon::new(
                OpaqueId::parse("01J00000000000000000000000").unwrap(),
                None,
                PortableName::parse("Core").unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            ))],
        )
        .unwrap()
        .with_cutover_marker(CatalogCutoverMarker::new(9).unwrap())
        .unwrap();
        let encoded = encode_catalog_generation(&catalog).unwrap();
        let decoded = decode_catalog_generation(&encoded).unwrap();
        assert_eq!(decoded.cutover_marker().unwrap().epoch(), 9);

        let mut wire: serde_json::Value = serde_json::from_slice(&encoded).unwrap();
        wire["cutoverMarker"]["legacyRollbackDisabled"] = serde_json::json!(false);
        assert!(decode_catalog_generation(&serde_json::to_vec(&wire).unwrap()).is_err());
    }
}
