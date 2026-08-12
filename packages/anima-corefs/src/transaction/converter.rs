//! Sealed, validation-only graph converter used before authoritative cutover.

use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsStr;
use std::io::Cursor;

use serde_json::Value;
use sha2::{Digest, Sha256};

use super::{
    CatalogPrecondition, CommitConflict, CommitError, CoreCommitCoordinator,
    PreparedObjectRevision, ValidationSnapshot,
};
use crate::catalog::{
    CatalogClientMetadata, CatalogEntryCommon, CatalogError, CatalogGeneration,
    CatalogGenerationEntry, CatalogObject, ContentHash, ObjectLifecycle, MAX_CATALOG_ENTRIES,
};
use crate::crypto::{
    generate_object_dek, unwrap_object_dek, FrkSubkeys, ObjectBaseAad, ObjectKeyAad, ObjectKind,
};
use crate::envelope::{
    read_envelope, write_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION,
    MAX_BODY_LENGTH,
};
use crate::folders::{ClientId, FolderOwner, FolderRole, PortableName};
use crate::id::OpaqueId;
use crate::policy::{AnimaAccess, LocalAnimaAccess, LocalFolderPolicy};

const DIARY_CONTENT_TYPE: &str = "application/vnd.anima.diary+json;version=1";
const DRAFT_CONTENT_TYPE: &str = "application/vnd.anima.draft+json;version=1";
const NOTE_CONTENT_TYPE: &str = "application/vnd.anima.note+json;version=1";
const ALLOWED_ROLES: [&str; 2] = ["core.journal", "core.notes"];
pub const MAX_WRITING_BODY_CHARS: usize = 20_000_000;
// Canonical HTML and JSON can expand one public source scalar to six ASCII
// bytes (for example an apostrophe becomes `&#x27;` or a control becomes a
// JSON `\u0000` escape). The fixed allowance covers the bounded format fields.
pub const MAX_WRITING_CANONICAL_EXPANSION: usize = 6;
pub const MAX_WRITING_DOCUMENT_BYTES: usize =
    MAX_WRITING_BODY_CHARS * MAX_WRITING_CANONICAL_EXPANSION + 1024 * 1024;
pub const MAX_WRITING_ATTACHMENT_BYTES: usize = 100 * 1024 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ValidationBatchMode {
    Initialize,
    Expect {
        generation: u64,
        catalog_hash: String,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ValidationBatchPolicy {
    UserWrite,
    Inherit,
    Deny,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationBatchFolder {
    pub stable_id: String,
    pub parent_id: Option<String>,
    pub name: String,
    pub role: Option<String>,
    pub policy: ValidationBatchPolicy,
    pub metadata: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationBatchObject {
    pub stable_id: String,
    pub parent_id: String,
    pub name: String,
    pub kind: ObjectKind,
    pub content_type: String,
    pub body_encoding: BodyEncoding,
    pub content: Vec<u8>,
    pub created_at: String,
    pub updated_at: String,
    pub source_character_count: Option<usize>,
    pub expected_revision: Option<u64>,
    pub references: Vec<String>,
    pub policy: ValidationBatchPolicy,
    pub metadata: BTreeMap<String, Value>,
}

pub(super) struct ConverterObjectMetadata<'a> {
    pub(super) object_id: &'a str,
    pub(super) revision: u64,
    pub(super) object_key_epoch: u32,
    pub(super) parent_id: &'a str,
    pub(super) name: &'a str,
    pub(super) kind: ObjectKind,
    pub(super) content_type: &'a str,
    pub(super) body_encoding: BodyEncoding,
    pub(super) body_length: u64,
    pub(super) content_sha256: &'a str,
    pub(super) created_at: &'a str,
    pub(super) updated_at: &'a str,
    pub(super) source_character_count: Option<usize>,
    pub(super) references: &'a [String],
    pub(super) policy: ValidationBatchPolicy,
    pub(super) stable_role: Option<&'a str>,
    pub(super) graph_metadata: &'a BTreeMap<String, Value>,
}

pub(super) struct ConverterGraphObject<'a> {
    pub(super) object_id: &'a str,
    pub(super) parent_id: &'a str,
    pub(super) references: &'a [String],
}

pub(super) struct PreparedValidationCatalogObject {
    pub(super) prepared: PreparedObjectRevision,
    pub(super) parent_id: String,
    pub(super) name: String,
    pub(super) policy: ValidationBatchPolicy,
    pub(super) references: Vec<String>,
    pub(super) metadata: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationBatch {
    pub mode: ValidationBatchMode,
    pub folders: Vec<ValidationBatchFolder>,
    pub objects: Vec<ValidationBatchObject>,
}

#[derive(Debug)]
pub struct ValidationBatchOutcome {
    snapshot: ValidationSnapshot,
    published: bool,
}

impl ValidationBatchOutcome {
    pub fn snapshot(&self) -> &ValidationSnapshot {
        &self.snapshot
    }

    pub const fn published(&self) -> bool {
        self.published
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResolvedValidationRole {
    pub generation: u64,
    pub catalog_hash: String,
    pub stable_id: String,
}

#[derive(Debug, thiserror::Error)]
pub enum ValidationBatchError {
    #[error("invalid validation batch: {0}")]
    Invalid(&'static str),
    #[error("expected validation head does not match the current head")]
    HeadMismatch,
    #[error("validation batch commit failed: {0}")]
    Commit(#[from] CommitError),
    #[error("validation batch catalog failed: {0}")]
    Catalog(#[from] CatalogError),
}

impl From<CommitConflict> for ValidationBatchError {
    fn from(value: CommitConflict) -> Self {
        Self::Commit(value.into())
    }
}

#[derive(Clone)]
struct ValidatedFolder {
    id: OpaqueId,
    parent_id: Option<OpaqueId>,
    name: PortableName,
    role: Option<FolderRole>,
    policy: ValidationBatchPolicy,
    owner: FolderOwner,
    access: AnimaAccess,
    metadata: CatalogClientMetadata,
}

#[derive(Clone)]
struct ValidatedObject {
    id: OpaqueId,
    parent_id: OpaqueId,
    name: PortableName,
    source: ValidationBatchObject,
    owner: FolderOwner,
    access: AnimaAccess,
    metadata: CatalogClientMetadata,
}

impl CoreCommitCoordinator {
    /// Converts one complete writing graph into at most one validation generation.
    ///
    /// This is deliberately separate from the public logical mutation facade,
    /// which remains frozen until the global PCF-008 authority cutover.
    pub fn apply_validation_batch(
        &self,
        keys: &FrkSubkeys,
        batch: ValidationBatch,
    ) -> Result<ValidationBatchOutcome, ValidationBatchError> {
        self.apply_validation_batch_inner(keys, batch, || Ok(()))
    }

    fn apply_validation_batch_inner<F>(
        &self,
        keys: &FrkSubkeys,
        batch: ValidationBatch,
        preparation_barrier: F,
    ) -> Result<ValidationBatchOutcome, ValidationBatchError>
    where
        F: FnOnce() -> Result<(), ValidationBatchError>,
    {
        let current = self.load_validation_snapshot(keys)?;
        match (&batch.mode, current.as_ref()) {
            (ValidationBatchMode::Initialize, None) => {}
            (ValidationBatchMode::Initialize, Some(_)) => {
                return Err(ValidationBatchError::HeadMismatch)
            }
            (
                ValidationBatchMode::Expect {
                    generation,
                    catalog_hash,
                },
                Some(snapshot),
            ) if snapshot.head().generation() == *generation
                && snapshot.head().catalog_hash() == catalog_hash => {}
            (ValidationBatchMode::Expect { .. }, _) => {
                return Err(ValidationBatchError::HeadMismatch)
            }
        }

        // Complete structural/content validation occurs before any immutable
        // encrypted revision is prepared.
        let (folders, objects) = validate_batch(&batch)?;
        if let Some(snapshot) = current.as_ref() {
            if self.graph_is_identical(keys, snapshot.catalog(), &folders, &objects)? {
                return Ok(ValidationBatchOutcome {
                    snapshot: self
                        .load_validation_snapshot(keys)?
                        .ok_or(ValidationBatchError::HeadMismatch)?,
                    published: false,
                });
            }
        }

        let (entries, prepared) = self.prepare_validation_graph(
            keys,
            current.as_ref().map(ValidationSnapshot::catalog),
            folders,
            objects,
        )?;
        // Publication is intentionally separated from preparation. A failed
        // preparation phase may leave unreachable immutable revisions, but it
        // must never make them authoritative through VALIDATION_HEAD.
        preparation_barrier()?;
        let snapshot = match current.as_ref() {
            None => self.initialize_validation_snapshot(keys, &prepared, move |generation| {
                CatalogGeneration::new(generation, entries)
            })?,
            Some(selected) => {
                let preconditions = full_graph_preconditions(selected.catalog(), &entries)?;
                self.advance_validation_snapshot(
                    keys,
                    selected,
                    &prepared,
                    &preconditions,
                    move |_current, generation| CatalogGeneration::new(generation, entries),
                )?
            }
        };
        Ok(ValidationBatchOutcome {
            snapshot,
            published: true,
        })
    }

    pub fn resolve_validation_role(
        &self,
        keys: &FrkSubkeys,
        role: &str,
    ) -> Result<Option<ResolvedValidationRole>, ValidationBatchError> {
        if !ALLOWED_ROLES.contains(&role) {
            return Err(ValidationBatchError::Invalid("unsupported stable role"));
        }
        let Some(snapshot) = self.load_validation_snapshot(keys)? else {
            return Ok(None);
        };
        let mut matches = snapshot.catalog().entries().iter().filter(|entry| {
            entry
                .common_for_internal_mutation()
                .role_for_internal_mutation()
                .is_some_and(|value| value.as_str() == role)
        });
        let Some(entry) = matches.next() else {
            return Ok(None);
        };
        if matches.next().is_some() {
            return Err(ValidationBatchError::Invalid("duplicate stable role"));
        }
        Ok(Some(ResolvedValidationRole {
            generation: snapshot.head().generation(),
            catalog_hash: snapshot.head().catalog_hash().to_owned(),
            stable_id: entry.stable_id().as_str().to_owned(),
        }))
    }

    fn prepare_validation_graph(
        &self,
        keys: &FrkSubkeys,
        current: Option<&CatalogGeneration>,
        folders: Vec<ValidatedFolder>,
        objects: Vec<ValidatedObject>,
    ) -> Result<(Vec<CatalogGenerationEntry>, Vec<PreparedObjectRevision>), ValidationBatchError>
    {
        let current_by_id: BTreeMap<_, _> = current
            .into_iter()
            .flat_map(CatalogGeneration::entries)
            .map(|entry| (entry.stable_id().as_str(), entry))
            .collect();
        let mut entries = Vec::with_capacity(folders.len() + objects.len());
        let mut prepared = Vec::new();
        for folder in folders {
            let mut common = CatalogEntryCommon::new(
                folder.id,
                folder.parent_id,
                folder.name,
                folder.owner,
                folder.access,
            )
            .with_policy_override_for_internal_mutation(local_policy(folder.policy))
            .with_client_metadata(folder.metadata);
            if let Some(role) = folder.role {
                common = common.with_role_for_internal_mutation(role);
            }
            entries.push(CatalogGenerationEntry::folder(common));
        }
        for object in objects {
            let current_entry = current_by_id.get(object.id.as_str()).copied();
            let current_object = current_entry.and_then(CatalogGenerationEntry::object_payload);
            let digest = hex_digest(&object.source.content);
            let authenticated_metadata = current_object
                .map(|value| self.authenticated_object_metadata(keys, &object.id, value))
                .transpose()?;
            let unchanged = current_entry.is_some_and(|entry| {
                entry.parent_id() == Some(&object.parent_id)
                    && entry.name() == &object.name
                    && current_object.is_some_and(|value| {
                        value.kind() == object.source.kind
                            && value.content_hash().as_str() == digest
                            && object.source.expected_revision == Some(value.revision())
                            && authenticated_metadata.as_ref().is_some_and(|metadata| {
                                envelope_identity_matches_source(
                                    metadata,
                                    &object.id,
                                    value.revision(),
                                    &object.source,
                                )
                            })
                    })
            });
            let catalog_object = if unchanged {
                current_object
                    .expect("unchanged objects have a payload")
                    .clone()
            } else {
                let revision = match current_object {
                    Some(value) => {
                        if object.source.expected_revision != Some(value.revision()) {
                            return Err(ValidationBatchError::Invalid(
                                "object revision precondition mismatch",
                            ));
                        }
                        value
                            .revision()
                            .checked_add(1)
                            .ok_or(ValidationBatchError::Invalid("object revision exhausted"))?
                    }
                    None => {
                        if object.source.expected_revision.is_some() {
                            return Err(ValidationBatchError::Invalid(
                                "new object cannot have an expected revision",
                            ));
                        }
                        1
                    }
                };
                let key_epoch = current_object.map_or(1, |value| value.object_key_epoch());
                let object_key = generate_object_dek().map_err(CommitError::from)?;
                let aad = ObjectBaseAad::new(
                    self.core_id.as_str(),
                    object.id.as_str(),
                    object.source.kind,
                    ENVELOPE_VERSION,
                    key_epoch,
                    revision,
                )
                .map_err(CommitError::from)?;
                let metadata = EnvelopeMetadata::for_body(
                    object.source.kind.as_str(),
                    object.id.as_str(),
                    revision,
                    &object.source.created_at,
                    &object.source.updated_at,
                    &object.source.content_type,
                    object.source.metadata.clone(),
                    object.source.body_encoding,
                    &object.source.content,
                )
                .map_err(CommitError::from)?;
                let mut encrypted = Vec::new();
                write_envelope(
                    &mut encrypted,
                    &object_key,
                    &aad,
                    &metadata,
                    &mut Cursor::new(&object.source.content),
                )
                .map_err(CommitError::from)?;
                let token = self.prepare_object_revision(
                    keys,
                    &object_key,
                    &aad,
                    &mut Cursor::new(encrypted),
                )?;
                let value = CatalogObject::new(
                    revision,
                    token.physical_name().clone(),
                    token.content_hash().clone(),
                    object.source.kind,
                    token.wrapped_dek().clone(),
                    ObjectLifecycle::Live,
                )?;
                prepared.push(token);
                value
            };
            let common = CatalogEntryCommon::new(
                object.id,
                Some(object.parent_id),
                object.name,
                object.owner,
                object.access,
            )
            .with_policy_override_for_internal_mutation(local_policy(object.source.policy))
            .with_client_metadata(object.metadata);
            entries.push(CatalogGenerationEntry::object(common, catalog_object));
        }
        Ok((entries, prepared))
    }

    fn authenticated_object_metadata(
        &self,
        keys: &FrkSubkeys,
        object_id: &OpaqueId,
        object: &CatalogObject,
    ) -> Result<EnvelopeMetadata, ValidationBatchError> {
        let base_aad = ObjectBaseAad::new(
            self.core_id.as_str(),
            object_id.as_str(),
            object.kind(),
            ENVELOPE_VERSION,
            object.object_key_epoch(),
            object.revision(),
        )
        .map_err(CommitError::from)?;
        let wrapped = object.wrapped_dek();
        let object_key_aad = ObjectKeyAad::from_base(base_aad.clone(), wrapped.frk_version())
            .map_err(CommitError::from)?;
        let object_key =
            unwrap_object_dek(keys, &wrapped.to_wrapped_object_dek()?, &object_key_aad)
                .map_err(CommitError::from)?;
        let mut file = super::open_regular_file_in(
            &self.objects_dir,
            OsStr::new(object.physical_name().as_str()),
        )
        .map_err(CommitError::from)?;
        let authenticated = read_envelope(&mut file, &object_key, &base_aad, &mut std::io::sink())
            .map_err(CommitError::from)?;
        if authenticated.metadata.body_sha256 != object.content_hash().as_str() {
            return Err(CommitError::InvalidObjectRevision.into());
        }
        Ok(authenticated.metadata)
    }

    fn graph_is_identical(
        &self,
        keys: &FrkSubkeys,
        current: &CatalogGeneration,
        folders: &[ValidatedFolder],
        objects: &[ValidatedObject],
    ) -> Result<bool, ValidationBatchError> {
        if current.entries().len() != folders.len() + objects.len() {
            return Ok(false);
        }
        let folder_matches = folders.iter().all(|folder| {
            current.entries().iter().any(|entry| {
                entry.is_folder()
                    && entry.stable_id() == &folder.id
                    && entry.parent_id() == folder.parent_id.as_ref()
                    && entry.name() == &folder.name
                    && entry
                        .common_for_internal_mutation()
                        .role_for_internal_mutation()
                        == folder.role.as_ref()
                    && entry
                        .common_for_internal_mutation()
                        .policy_override_for_internal_mutation()
                        == local_policy(folder.policy)
                    && entry
                        .common_for_internal_mutation()
                        .client_metadata_for_internal_mutation()
                        == &folder.metadata
            })
        });
        if !folder_matches {
            return Ok(false);
        }
        for object in objects {
            let Some(entry) = current
                .entries()
                .iter()
                .find(|entry| entry.stable_id() == &object.id)
            else {
                return Ok(false);
            };
            let Some(payload) = entry.object_payload() else {
                return Ok(false);
            };
            if entry.parent_id() != Some(&object.parent_id)
                || entry.name() != &object.name
                || payload.kind() != object.source.kind
                || payload.content_hash().as_str() != hex_digest(&object.source.content)
                || object.source.expected_revision != Some(payload.revision())
                || entry
                    .common_for_internal_mutation()
                    .policy_override_for_internal_mutation()
                    != local_policy(object.source.policy)
                || entry
                    .common_for_internal_mutation()
                    .client_metadata_for_internal_mutation()
                    != &object.metadata
            {
                return Ok(false);
            }
            let metadata = self.authenticated_object_metadata(keys, &object.id, payload)?;
            if !envelope_identity_matches_source(
                &metadata,
                &object.id,
                payload.revision(),
                &object.source,
            ) {
                return Ok(false);
            }
        }
        Ok(true)
    }
}

fn validate_batch(
    batch: &ValidationBatch,
) -> Result<(Vec<ValidatedFolder>, Vec<ValidatedObject>), ValidationBatchError> {
    if batch.folders.is_empty() {
        return Err(ValidationBatchError::Invalid("folder graph is empty"));
    }
    if batch.folders.len().saturating_add(batch.objects.len()) > MAX_CATALOG_ENTRIES {
        return Err(ValidationBatchError::Invalid(
            "batch exceeds the catalog entry limit",
        ));
    }
    let mut ids = BTreeSet::new();
    let mut folder_inputs = BTreeMap::new();
    let mut role_counts = BTreeMap::<&str, usize>::new();
    for folder in &batch.folders {
        let id = OpaqueId::parse(&folder.stable_id)
            .map_err(|_| ValidationBatchError::Invalid("invalid folder ID"))?;
        if !ids.insert(id.as_str().to_owned()) {
            return Err(ValidationBatchError::Invalid("duplicate stable ID"));
        }
        if let Some(role) = folder.role.as_deref() {
            if !ALLOWED_ROLES.contains(&role) {
                return Err(ValidationBatchError::Invalid("unsupported stable role"));
            }
            *role_counts.entry(role).or_default() += 1;
        }
        folder_inputs.insert(id.as_str().to_owned(), folder);
    }
    if ALLOWED_ROLES
        .iter()
        .any(|role| role_counts.get(role).copied() != Some(1))
    {
        return Err(ValidationBatchError::Invalid(
            "core.journal and core.notes must each be bound exactly once",
        ));
    }
    for object in &batch.objects {
        let id = OpaqueId::parse(&object.stable_id)
            .map_err(|_| ValidationBatchError::Invalid("invalid object ID"))?;
        if !ids.insert(id.as_str().to_owned()) {
            return Err(ValidationBatchError::Invalid("duplicate stable ID"));
        }
        validate_kind_and_content(object)?;
    }

    validate_converter_graph_relationships(
        folder_inputs.keys().map(String::as_str),
        batch.objects.iter().map(|object| ConverterGraphObject {
            object_id: &object.stable_id,
            parent_id: &object.parent_id,
            references: &object.references,
        }),
    )?;

    let mut effective = BTreeMap::new();
    let mut visiting = BTreeSet::new();
    for id in folder_inputs.keys() {
        resolve_folder_policy(id, &folder_inputs, &mut effective, &mut visiting)?;
    }
    let mut folders = Vec::with_capacity(batch.folders.len());
    for folder in &batch.folders {
        let (owner, access) = effective[folder.stable_id.as_str()];
        if folder.role.is_some() && folder.policy != ValidationBatchPolicy::UserWrite {
            return Err(ValidationBatchError::Invalid(
                "stable writing roots require explicit user/write policy",
            ));
        }
        folders.push(ValidatedFolder {
            id: OpaqueId::parse(&folder.stable_id).expect("validated ID"),
            parent_id: folder
                .parent_id
                .as_deref()
                .map(OpaqueId::parse)
                .transpose()
                .map_err(|_| ValidationBatchError::Invalid("invalid parent ID"))?,
            name: PortableName::parse(&folder.name)
                .map_err(|_| ValidationBatchError::Invalid("invalid folder name"))?,
            role: folder
                .role
                .as_deref()
                .map(FolderRole::parse_existing)
                .transpose()
                .map_err(|_| ValidationBatchError::Invalid("invalid stable role"))?,
            policy: folder.policy,
            owner,
            access,
            metadata: validation_metadata(&folder.metadata)?,
        });
    }
    let mut objects = Vec::with_capacity(batch.objects.len());
    for object in &batch.objects {
        if object.policy == ValidationBatchPolicy::UserWrite {
            return Err(ValidationBatchError::Invalid(
                "descendant objects must inherit or deny policy",
            ));
        }
        let (owner, parent_access) = effective[object.parent_id.as_str()];
        let access = if object.policy == ValidationBatchPolicy::Deny {
            AnimaAccess::None
        } else {
            parent_access
        };
        objects.push(ValidatedObject {
            id: OpaqueId::parse(&object.stable_id).expect("validated ID"),
            parent_id: OpaqueId::parse(&object.parent_id)
                .map_err(|_| ValidationBatchError::Invalid("invalid parent ID"))?,
            name: PortableName::parse(&object.name)
                .map_err(|_| ValidationBatchError::Invalid("invalid object name"))?,
            source: object.clone(),
            owner,
            access,
            metadata: validation_metadata(&object.metadata)?,
        });
    }
    Ok((folders, objects))
}

pub(super) fn build_prepared_validation_catalog(
    generation: u64,
    folder_inputs: &[ValidationBatchFolder],
    object_inputs: Vec<PreparedValidationCatalogObject>,
) -> Result<CatalogGeneration, ValidationBatchError> {
    if folder_inputs.is_empty() {
        return Err(ValidationBatchError::Invalid("folder graph is empty"));
    }
    if folder_inputs.len().saturating_add(object_inputs.len()) > MAX_CATALOG_ENTRIES {
        return Err(ValidationBatchError::Invalid(
            "batch exceeds the catalog entry limit",
        ));
    }

    let mut ids = BTreeSet::new();
    let mut folders_by_id = BTreeMap::new();
    let mut role_counts = BTreeMap::<&str, usize>::new();
    for folder in folder_inputs {
        let id = OpaqueId::parse(&folder.stable_id)
            .map_err(|_| ValidationBatchError::Invalid("invalid folder ID"))?;
        if !ids.insert(id.as_str().to_owned()) {
            return Err(ValidationBatchError::Invalid("duplicate stable ID"));
        }
        if let Some(role) = folder.role.as_deref() {
            if !ALLOWED_ROLES.contains(&role) {
                return Err(ValidationBatchError::Invalid("unsupported stable role"));
            }
            *role_counts.entry(role).or_default() += 1;
        }
        folders_by_id.insert(id.as_str().to_owned(), folder);
    }
    if ALLOWED_ROLES
        .iter()
        .any(|role| role_counts.get(role).copied() != Some(1))
    {
        return Err(ValidationBatchError::Invalid(
            "core.journal and core.notes must each be bound exactly once",
        ));
    }
    for object in &object_inputs {
        if !ids.insert(object.prepared.object_id.as_str().to_owned()) {
            return Err(ValidationBatchError::Invalid("duplicate stable ID"));
        }
    }

    validate_converter_graph_relationships(
        folders_by_id.keys().map(String::as_str),
        object_inputs.iter().map(|object| ConverterGraphObject {
            object_id: object.prepared.object_id.as_str(),
            parent_id: &object.parent_id,
            references: &object.references,
        }),
    )?;

    let mut effective = BTreeMap::new();
    let mut visiting = BTreeSet::new();
    for id in folders_by_id.keys() {
        resolve_folder_policy(id, &folders_by_id, &mut effective, &mut visiting)?;
    }

    let mut entries = Vec::with_capacity(folder_inputs.len() + object_inputs.len());
    for folder in folder_inputs {
        if folder.role.is_some() && folder.policy != ValidationBatchPolicy::UserWrite {
            return Err(ValidationBatchError::Invalid(
                "stable writing roots require explicit user/write policy",
            ));
        }
        let (owner, access) = effective[folder.stable_id.as_str()];
        let mut common = CatalogEntryCommon::new(
            OpaqueId::parse(&folder.stable_id).expect("validated folder ID"),
            folder
                .parent_id
                .as_deref()
                .map(OpaqueId::parse)
                .transpose()
                .map_err(|_| ValidationBatchError::Invalid("invalid parent ID"))?,
            PortableName::parse(&folder.name)
                .map_err(|_| ValidationBatchError::Invalid("invalid folder name"))?,
            owner,
            access,
        )
        .with_policy_override_for_internal_mutation(local_policy(folder.policy))
        .with_client_metadata(validation_metadata(&folder.metadata)?);
        if let Some(role) = folder.role.as_deref() {
            common = common.with_role_for_internal_mutation(
                FolderRole::parse_existing(role)
                    .map_err(|_| ValidationBatchError::Invalid("invalid stable role"))?,
            );
        }
        entries.push(CatalogGenerationEntry::folder(common));
    }

    for object in object_inputs {
        if object.policy == ValidationBatchPolicy::UserWrite {
            return Err(ValidationBatchError::Invalid(
                "descendant objects must inherit or deny policy",
            ));
        }
        let (owner, parent_access) = effective[object.parent_id.as_str()];
        let access = if object.policy == ValidationBatchPolicy::Deny {
            AnimaAccess::None
        } else {
            parent_access
        };
        let common = CatalogEntryCommon::new(
            object.prepared.object_id.clone(),
            Some(
                OpaqueId::parse(&object.parent_id)
                    .map_err(|_| ValidationBatchError::Invalid("invalid parent ID"))?,
            ),
            PortableName::parse(&object.name)
                .map_err(|_| ValidationBatchError::Invalid("invalid object name"))?,
            owner,
            access,
        )
        .with_policy_override_for_internal_mutation(local_policy(object.policy))
        .with_client_metadata(validation_metadata(&object.metadata)?);
        let catalog_object = CatalogObject::new(
            object.prepared.revision,
            object.prepared.physical_name.clone(),
            object.prepared.content_hash.clone(),
            object.prepared.kind,
            object.prepared.wrapped_dek.clone(),
            ObjectLifecycle::Live,
        )?;
        entries.push(CatalogGenerationEntry::object(common, catalog_object));
    }

    CatalogGeneration::new(generation, entries).map_err(ValidationBatchError::from)
}

fn resolve_folder_policy<'a>(
    id: &'a str,
    folders: &BTreeMap<String, &'a ValidationBatchFolder>,
    effective: &mut BTreeMap<&'a str, (FolderOwner, AnimaAccess)>,
    visiting: &mut BTreeSet<&'a str>,
) -> Result<(FolderOwner, AnimaAccess), ValidationBatchError> {
    if let Some(value) = effective.get(id) {
        return Ok(*value);
    }
    if !visiting.insert(id) {
        return Err(ValidationBatchError::Invalid(
            "folder graph contains a cycle",
        ));
    }
    let folder = folders
        .get(id)
        .ok_or(ValidationBatchError::Invalid("folder parent is missing"))?;
    let value = match (folder.parent_id.as_deref(), folder.policy) {
        (None, ValidationBatchPolicy::UserWrite) => (FolderOwner::User, AnimaAccess::Write),
        (None, _) => {
            return Err(ValidationBatchError::Invalid(
                "catalog root requires explicit user/write policy",
            ))
        }
        (Some(parent), ValidationBatchPolicy::UserWrite) => {
            if folder.role.is_none() {
                return Err(ValidationBatchError::Invalid(
                    "only stable writing roots may override user/write",
                ));
            }
            if !folders.contains_key(parent) {
                return Err(ValidationBatchError::Invalid("folder parent is missing"));
            }
            (FolderOwner::User, AnimaAccess::Write)
        }
        (Some(parent), policy) => {
            let (owner, access) = resolve_folder_policy(parent, folders, effective, visiting)?;
            (
                owner,
                if policy == ValidationBatchPolicy::Deny {
                    AnimaAccess::None
                } else {
                    access
                },
            )
        }
    };
    visiting.remove(id);
    effective.insert(id, value);
    Ok(value)
}

fn validate_kind_and_content(object: &ValidationBatchObject) -> Result<(), ValidationBatchError> {
    validate_converter_object_metadata(&ConverterObjectMetadata {
        object_id: &object.stable_id,
        revision: object.expected_revision.unwrap_or(1),
        object_key_epoch: 1,
        parent_id: &object.parent_id,
        name: &object.name,
        kind: object.kind,
        content_type: &object.content_type,
        body_encoding: object.body_encoding,
        body_length: u64::try_from(object.content.len())
            .map_err(|_| ValidationBatchError::Invalid("object body length overflow"))?,
        content_sha256: &hex_digest(&object.content),
        created_at: &object.created_at,
        updated_at: &object.updated_at,
        source_character_count: object.source_character_count,
        references: &object.references,
        policy: object.policy,
        stable_role: None,
        graph_metadata: &object.metadata,
    })?;
    if matches!(
        object.kind,
        ObjectKind::Diary | ObjectKind::Draft | ObjectKind::Note
    ) {
        let source_character_count =
            object
                .source_character_count
                .ok_or(ValidationBatchError::Invalid(
                    "writing source character count is required",
                ))?;
        let document: Value = serde_json::from_slice(&object.content)
            .map_err(|_| ValidationBatchError::Invalid("writing document is not valid JSON"))?;
        let body_field = if object.kind == ObjectKind::Diary {
            "html"
        } else {
            "body"
        };
        let canonical_count = document
            .get(body_field)
            .and_then(Value::as_str)
            .ok_or(ValidationBatchError::Invalid(
                "writing body field is missing",
            ))?
            .chars()
            .count();
        validate_writing_character_counts(
            source_character_count,
            canonical_count,
            MAX_WRITING_BODY_CHARS,
            MAX_WRITING_CANONICAL_EXPANSION,
        )?;
    } else if object.source_character_count.is_some() {
        return Err(ValidationBatchError::Invalid(
            "binary attachment cannot declare a source character count",
        ));
    }
    Ok(())
}

pub(super) fn validate_converter_object_metadata(
    object: &ConverterObjectMetadata<'_>,
) -> Result<(), ValidationBatchError> {
    OpaqueId::parse(object.object_id)
        .map_err(|_| ValidationBatchError::Invalid("invalid object ID"))?;
    OpaqueId::parse(object.parent_id)
        .map_err(|_| ValidationBatchError::Invalid("invalid parent ID"))?;
    PortableName::parse(object.name)
        .map_err(|_| ValidationBatchError::Invalid("invalid object name"))?;
    if object.revision == 0 || object.object_key_epoch == 0 {
        return Err(ValidationBatchError::Invalid(
            "object revision and key epoch must be positive",
        ));
    }
    let kind_limit = u64::try_from(match object.kind {
        ObjectKind::Diary | ObjectKind::Draft | ObjectKind::Note => MAX_WRITING_DOCUMENT_BYTES,
        ObjectKind::Attachment => MAX_WRITING_ATTACHMENT_BYTES,
        _ => 0,
    })
    .map_err(|_| ValidationBatchError::Invalid("object body limit overflow"))?;
    if object.body_length > kind_limit
        || object.body_length > MAX_BODY_LENGTH
        || object.content_type.len() > 255
        || object.created_at.is_empty()
        || object.created_at.len() > 128
        || object.updated_at.is_empty()
        || object.updated_at.len() > 128
        || object.references.len() > MAX_CATALOG_ENTRIES
    {
        return Err(ValidationBatchError::Invalid(
            "object metadata or body exceeds kind-specific converter limits",
        ));
    }
    let valid_format = match object.kind {
        ObjectKind::Diary => {
            object.content_type == DIARY_CONTENT_TYPE && object.body_encoding == BodyEncoding::Utf8
        }
        ObjectKind::Draft => {
            object.content_type == DRAFT_CONTENT_TYPE && object.body_encoding == BodyEncoding::Utf8
        }
        ObjectKind::Note => {
            object.content_type == NOTE_CONTENT_TYPE && object.body_encoding == BodyEncoding::Utf8
        }
        ObjectKind::Attachment => {
            !object.content_type.is_empty() && object.body_encoding == BodyEncoding::Binary
        }
        _ => false,
    };
    if !valid_format {
        return Err(ValidationBatchError::Invalid(
            "unsupported kind/content type/encoding",
        ));
    }
    if matches!(
        object.kind,
        ObjectKind::Diary | ObjectKind::Draft | ObjectKind::Note
    ) != object.source_character_count.is_some()
    {
        return Err(ValidationBatchError::Invalid(
            "writing source character count presence is invalid",
        ));
    }
    if object
        .source_character_count
        .is_some_and(|count| count > MAX_WRITING_BODY_CHARS)
    {
        return Err(ValidationBatchError::Invalid(
            "writing source character count is invalid",
        ));
    }
    ContentHash::parse(object.content_sha256)
        .map_err(|_| ValidationBatchError::Invalid("invalid content hash"))?;
    for reference in object.references {
        OpaqueId::parse(reference)
            .map_err(|_| ValidationBatchError::Invalid("invalid object reference ID"))?;
    }
    if object.policy == ValidationBatchPolicy::UserWrite {
        return Err(ValidationBatchError::Invalid(
            "descendant objects must inherit or deny policy",
        ));
    }
    if let Some(role) = object.stable_role {
        if !ALLOWED_ROLES.contains(&role) {
            return Err(ValidationBatchError::Invalid("unsupported stable role"));
        }
        FolderRole::parse_existing(role)
            .map_err(|_| ValidationBatchError::Invalid("invalid stable role"))?;
    }
    validation_metadata(object.graph_metadata)?;
    Ok(())
}

pub(super) fn validate_converter_graph_relationships<'a, I>(
    folder_ids: impl IntoIterator<Item = &'a str>,
    objects: I,
) -> Result<(), ValidationBatchError>
where
    I: Clone + IntoIterator<Item = ConverterGraphObject<'a>>,
{
    let folder_ids: BTreeSet<_> = folder_ids.into_iter().collect();
    let object_ids: BTreeSet<_> = objects
        .clone()
        .into_iter()
        .map(|object| object.object_id)
        .collect();
    if object_ids.len() != objects.clone().into_iter().count() {
        return Err(ValidationBatchError::Invalid("duplicate stable ID"));
    }
    for object in objects.into_iter() {
        if !folder_ids.contains(object.parent_id) {
            return Err(ValidationBatchError::Invalid(
                "object parent is not a folder",
            ));
        }
        if object
            .references
            .iter()
            .any(|reference| !object_ids.contains(reference.as_str()))
        {
            return Err(ValidationBatchError::Invalid("object reference is missing"));
        }
    }
    Ok(())
}

fn validate_writing_character_counts(
    source_count: usize,
    canonical_count: usize,
    public_limit: usize,
    expansion_limit: usize,
) -> Result<(), ValidationBatchError> {
    if source_count > public_limit {
        return Err(ValidationBatchError::Invalid(
            "writing source character count is invalid",
        ));
    }
    let canonical_limit =
        source_count
            .checked_mul(expansion_limit)
            .ok_or(ValidationBatchError::Invalid(
                "writing character count overflow",
            ))?;
    if canonical_count > canonical_limit {
        return Err(ValidationBatchError::Invalid(
            "canonical writing body exceeds bounded source expansion",
        ));
    }
    Ok(())
}

fn envelope_identity_matches_source(
    authenticated: &EnvelopeMetadata,
    object_id: &OpaqueId,
    revision: u64,
    source: &ValidationBatchObject,
) -> bool {
    EnvelopeMetadata::for_body(
        source.kind.as_str(),
        object_id.as_str(),
        revision,
        &source.created_at,
        &source.updated_at,
        &source.content_type,
        source.metadata.clone(),
        source.body_encoding,
        &source.content,
    )
    .is_ok_and(|expected| expected == *authenticated)
}

fn validation_metadata(
    metadata: &BTreeMap<String, Value>,
) -> Result<CatalogClientMetadata, ValidationBatchError> {
    let writer = ClientId::parse("pcf004")
        .map_err(|_| ValidationBatchError::Invalid("invalid migration metadata writer"))?;
    CatalogClientMetadata::new(
        &writer,
        metadata
            .iter()
            .map(|(key, value)| (format!("client:pcf004:{key}"), value.clone())),
    )
    .map_err(ValidationBatchError::from)
}

pub(super) fn full_graph_preconditions(
    current: &CatalogGeneration,
    next: &[CatalogGenerationEntry],
) -> Result<Vec<CatalogPrecondition>, ValidationBatchError> {
    let mut values = Vec::new();
    for entry in current.entries() {
        values.push(match entry.object_payload() {
            Some(object) => {
                CatalogPrecondition::object(current, entry.stable_id(), object.revision())?
            }
            None => CatalogPrecondition::folder(current, entry.stable_id())?,
        });
    }
    for entry in next {
        let current_entry = current
            .entries()
            .iter()
            .find(|existing| existing.stable_id() == entry.stable_id());
        if current_entry.is_some_and(|existing| {
            existing.parent_id() == entry.parent_id() && existing.name() == entry.name()
        }) {
            continue;
        }
        let Some(parent) = entry.parent_id() else {
            continue;
        };
        let parent_is_current = current
            .entries()
            .iter()
            .any(|existing| existing.stable_id() == parent && existing.is_folder());
        let destination_occupied = current.entries().iter().any(|existing| {
            existing.parent_id() == Some(parent) && existing.name() == entry.name()
        });
        if parent_is_current && !destination_occupied {
            values.push(CatalogPrecondition::vacant(
                current,
                parent,
                entry.name().clone(),
            )?);
        }
    }
    Ok(values)
}

fn local_policy(value: ValidationBatchPolicy) -> LocalFolderPolicy {
    match value {
        ValidationBatchPolicy::UserWrite => LocalFolderPolicy::new(
            Some(FolderOwner::User),
            LocalAnimaAccess::Allow(AnimaAccess::Write),
        ),
        ValidationBatchPolicy::Inherit => LocalFolderPolicy::inherit(),
        ValidationBatchPolicy::Deny => LocalFolderPolicy::new(None, LocalAnimaAccess::Deny),
    }
}

fn hex_digest(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        write!(&mut output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

#[cfg(test)]
mod tests {
    use std::fs;

    use crate::crypto::{derive_corefs_subkeys, SecretBytes};

    use super::*;

    fn native_id(domain: &str, value: &str) -> String {
        OpaqueId::derive_migration(domain, value.as_bytes())
            .unwrap()
            .as_str()
            .to_owned()
    }

    #[test]
    fn injected_character_limit_accepts_worst_html_escaping_and_rejects_one_more() {
        assert!(validate_writing_character_counts(7, 42, 7, 6).is_ok());
        assert!(validate_writing_character_counts(8, 8, 7, 6).is_err());
        assert!(validate_writing_character_counts(7, 43, 7, 6).is_err());
    }

    fn batch(mode: ValidationBatchMode, content: &[u8], revision: Option<u64>) -> ValidationBatch {
        let root = native_id("folder", "root");
        let journal = native_id("folder", "journal");
        ValidationBatch {
            mode,
            folders: vec![
                ValidationBatchFolder {
                    stable_id: root.clone(),
                    parent_id: None,
                    name: "Core".into(),
                    role: None,
                    policy: ValidationBatchPolicy::UserWrite,
                    metadata: BTreeMap::new(),
                },
                ValidationBatchFolder {
                    stable_id: journal.clone(),
                    parent_id: Some(root.clone()),
                    name: "Journal".into(),
                    role: Some("core.journal".into()),
                    policy: ValidationBatchPolicy::UserWrite,
                    metadata: BTreeMap::from([("order".into(), Value::from(0))]),
                },
                ValidationBatchFolder {
                    stable_id: native_id("folder", "notes"),
                    parent_id: Some(root),
                    name: "Notes".into(),
                    role: Some("core.notes".into()),
                    policy: ValidationBatchPolicy::UserWrite,
                    metadata: BTreeMap::new(),
                },
            ],
            objects: vec![ValidationBatchObject {
                stable_id: native_id("diary", "1"),
                parent_id: journal,
                name: "entry.diary.json".into(),
                kind: ObjectKind::Diary,
                content_type: DIARY_CONTENT_TYPE.into(),
                body_encoding: BodyEncoding::Utf8,
                content: content.to_vec(),
                created_at: "2026-08-02T00:00:00Z".into(),
                updated_at: "2026-08-02T00:00:00Z".into(),
                source_character_count: Some(6),
                expected_revision: revision,
                references: vec![],
                policy: ValidationBatchPolicy::Inherit,
                metadata: BTreeMap::from([("sourceCharacterCount".into(), Value::from(6))]),
            }],
        }
    }

    #[test]
    fn envelope_identity_covers_encoding_lifecycle_mime_and_metadata() {
        let object_id = OpaqueId::parse(&native_id("attachment", "identity")).unwrap();
        let source = ValidationBatchObject {
            stable_id: object_id.as_str().to_owned(),
            parent_id: native_id("folder", "journal"),
            name: "identity.bin".into(),
            kind: ObjectKind::Attachment,
            content_type: "application/octet-stream".into(),
            body_encoding: BodyEncoding::Binary,
            content: b"same body".to_vec(),
            created_at: "2026-08-02T00:00:00Z".into(),
            updated_at: "2026-08-02T00:00:00Z".into(),
            source_character_count: None,
            expected_revision: Some(1),
            references: vec![],
            policy: ValidationBatchPolicy::Inherit,
            metadata: BTreeMap::new(),
        };
        let authenticated = EnvelopeMetadata::for_body(
            source.kind.as_str(),
            object_id.as_str(),
            1,
            &source.created_at,
            &source.updated_at,
            &source.content_type,
            source.metadata.clone(),
            source.body_encoding,
            &source.content,
        )
        .unwrap();
        assert!(envelope_identity_matches_source(
            &authenticated,
            &object_id,
            1,
            &source
        ));

        let mut changed = source.clone();
        changed.body_encoding = BodyEncoding::Utf8;
        assert!(!envelope_identity_matches_source(
            &authenticated,
            &object_id,
            1,
            &changed
        ));
        changed = source.clone();
        changed.content_type = "application/vnd.changed".into();
        assert!(!envelope_identity_matches_source(
            &authenticated,
            &object_id,
            1,
            &changed
        ));
        changed = source.clone();
        changed.created_at = "2026-08-01T23:59:59Z".into();
        assert!(!envelope_identity_matches_source(
            &authenticated,
            &object_id,
            1,
            &changed
        ));
        changed = source.clone();
        changed.updated_at = "2026-08-02T00:00:01Z".into();
        assert!(!envelope_identity_matches_source(
            &authenticated,
            &object_id,
            1,
            &changed
        ));
        changed = source;
        changed
            .metadata
            .insert("source".into(), Value::from("legacy"));
        assert!(!envelope_identity_matches_source(
            &authenticated,
            &object_id,
            1,
            &changed
        ));
    }

    #[test]
    fn preparation_failure_preserves_validation_head_bytes() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-preparation-failure-{}-{}",
            std::process::id(),
            native_id("fixture", "preparation-failure")
        ));
        let _ = fs::remove_dir_all(&root);
        let coordinator = CoreCommitCoordinator::new(&root, "core-preparation-failure").unwrap();
        let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x5c; 32]).unwrap(), 1).unwrap();
        let first_content = br#"{"html":"first"}"#;
        let first = coordinator
            .apply_validation_batch(
                &keys,
                batch(ValidationBatchMode::Initialize, first_content, None),
            )
            .unwrap();
        let journal = first
            .snapshot()
            .catalog()
            .entries()
            .iter()
            .find(|entry| {
                entry
                    .common_for_internal_mutation()
                    .role_for_internal_mutation()
                    .is_some_and(|role| role.as_str() == "core.journal")
            })
            .unwrap();
        assert_eq!(
            journal
                .common_for_internal_mutation()
                .client_metadata_for_internal_mutation()
                .values()
                .get("client:pcf004:order"),
            Some(&Value::from(0))
        );
        let head_path = root.join("fs").join("VALIDATION_HEAD");
        let before = fs::read(&head_path).unwrap();
        let mode = ValidationBatchMode::Expect {
            generation: first.snapshot().head().generation(),
            catalog_hash: first.snapshot().head().catalog_hash().to_owned(),
        };

        let second_content = br#"{"html":"second"}"#;
        let error = coordinator
            .apply_validation_batch_inner(&keys, batch(mode, second_content, Some(1)), || {
                Err(ValidationBatchError::Invalid(
                    "injected preparation failure",
                ))
            })
            .unwrap_err();
        assert!(matches!(error, ValidationBatchError::Invalid(_)));
        assert_eq!(fs::read(&head_path).unwrap(), before);
        assert_eq!(
            coordinator
                .load_validation_snapshot(&keys)
                .unwrap()
                .unwrap()
                .head(),
            first.snapshot().head()
        );
        let _ = fs::remove_dir_all(root);
    }
}
