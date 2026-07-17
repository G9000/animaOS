use std::collections::BTreeMap;
use std::io::Cursor;

use crate::catalog::{CatalogEntryCommon, CatalogGenerationEntry, CatalogObject, ObjectLifecycle};
use crate::crypto::{
    generate_object_dek, FrkSubkeys, ObjectBaseAad, ObjectKind, OBJECT_KEY_ENVELOPE_VERSION,
};
use crate::envelope::{encode_envelope, EnvelopeMetadata, MAX_BODY_LENGTH};
use crate::transaction::{CoreCommitCoordinator, PreparedObjectRevision};

use super::{
    ContentFormatValidator, MutationChange, MutationError, MutationStamp, ValidatedContent,
};

pub(super) struct PendingObject {
    pub common: CatalogEntryCommon,
    pub kind: ObjectKind,
    pub lifecycle: ObjectLifecycle,
    pub revision: u64,
    pub object_key_epoch: u32,
    pub created_at: String,
    pub content: ValidatedContent,
}

pub(super) fn validate_content(
    validator: &dyn ContentFormatValidator,
    kind: ObjectKind,
    content_type: &str,
    bytes: &[u8],
) -> Result<ValidatedContent, MutationError> {
    if bytes.len() as u64 > MAX_BODY_LENGTH {
        return Err(MutationError::SizeLimit);
    }
    let content = validator.validate(kind, content_type, bytes)?;
    if content.bytes.len() as u64 > MAX_BODY_LENGTH {
        return Err(MutationError::SizeLimit);
    }
    Ok(content)
}

pub(super) fn prepare_object(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    pending: PendingObject,
    stamp: &MutationStamp,
) -> Result<
    (
        PreparedObjectRevision,
        CatalogGenerationEntry,
        MutationChange,
    ),
    MutationError,
> {
    let object_id = pending.common.stable_id().clone();
    let object_key = generate_object_dek().map_err(|_| MutationError::PrepareFailed)?;
    let aad = ObjectBaseAad::new(
        coordinator.core_id(),
        object_id.as_str(),
        pending.kind,
        OBJECT_KEY_ENVELOPE_VERSION,
        pending.object_key_epoch,
        pending.revision,
    )
    .map_err(|_| MutationError::PrepareFailed)?;
    let metadata = EnvelopeMetadata::for_body(
        pending.kind.as_str(),
        object_id.as_str(),
        pending.revision,
        pending.created_at,
        stamp.timestamp_text.clone(),
        pending.content.content_type.clone(),
        BTreeMap::new(),
        pending.content.body_encoding,
        &pending.content.bytes,
    )
    .map_err(|_| MutationError::PrepareFailed)?;
    let encoded = encode_envelope(&object_key, &aad, &metadata, &pending.content.bytes)
        .map_err(|_| MutationError::PrepareFailed)?;
    let prepared = coordinator
        .prepare_object_revision(keys, &object_key, &aad, &mut Cursor::new(encoded))
        .map_err(|_| MutationError::PrepareFailed)?;
    let entry = CatalogGenerationEntry::object(
        pending.common,
        CatalogObject::new(
            prepared.revision(),
            prepared.physical_name().clone(),
            prepared.content_hash().clone(),
            pending.kind,
            prepared.wrapped_dek().clone(),
            pending.lifecycle,
        )
        .map_err(|_| MutationError::PrepareFailed)?,
    );
    let result = MutationChange {
        stable_id: object_id.as_str().to_owned(),
        revision: Some(prepared.revision()),
        content_hash: Some(prepared.content_hash().as_str().to_owned()),
    };
    Ok((prepared, entry, result))
}
