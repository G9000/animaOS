use crate::catalog::{
    CatalogEntryCommon, CatalogGenerationEntry, CatalogObject, FolderLifecycle,
    FolderTrashMetadata, ObjectLifecycle, TrashMetadata,
};
use crate::crypto::ObjectKind;
use crate::folders::FolderRole;
use crate::id::OpaqueId;
use crate::logical::CoreFsReadSnapshot;
use crate::policy::AnimaAccess;

use super::executor::MutationDraft;
use super::preflight::{CatalogIndex, Destination};
use super::preparation::{validate_content, PendingObject};
use super::{
    ContentFormatValidator, ConverterPrincipal, LogicalMutation, MutationChange, MutationError,
    MutationStamp,
};

pub(super) fn plan_non_patch(
    principal: ConverterPrincipal,
    index: &CatalogIndex<'_>,
    read_snapshot: &CoreFsReadSnapshot,
    draft: &mut MutationDraft,
    operation: LogicalMutation,
    stamp: &MutationStamp,
    validator: &dyn ContentFormatValidator,
) -> Result<(), MutationError> {
    match operation {
        LogicalMutation::Mkdir {
            path,
            reserved_role,
        } => {
            let destination = index.destination(&path)?;
            let parent = index
                .entry(&destination.parent_id)
                .ok_or(MutationError::NotFound)?;
            index.authorize(principal, parent, AnimaAccess::Write)?;
            let id = OpaqueId::generate().map_err(|_| MutationError::Storage)?;
            let parent_common = parent.common_for_internal_mutation();
            let mut common = CatalogEntryCommon::new(
                id.clone(),
                Some(destination.parent_id.clone()),
                destination.name.clone(),
                parent_common.owner_for_internal_mutation(),
                parent_common.anima_access_for_internal_mutation(),
            );
            if let Some(role) = reserved_role {
                if principal != ConverterPrincipal::User {
                    return Err(MutationError::ReservedRoleRequiresUser);
                }
                if !role.starts_with("core.") {
                    return Err(MutationError::ReservedRoleRequiresUser);
                }
                if index.has_role(&role) {
                    return Err(MutationError::RoleCollision);
                }
                let role = FolderRole::parse_existing(&role)
                    .map_err(|_| MutationError::ReservedRoleRequiresUser)?;
                common = common.with_role_for_internal_mutation(role);
            }
            draft.entries.push(CatalogGenerationEntry::folder(common));
            draft
                .preconditions
                .push(index.vacancy_precondition(&destination)?);
            draft.changes.push(change(&id, None, None));
        }
        LogicalMutation::Create {
            path,
            stable_id,
            kind,
            content_type,
            bytes,
        } => {
            if kind == ObjectKind::Folder {
                return Err(MutationError::WrongEntryKind);
            }
            let destination = index.destination(&path)?;
            let parent = index
                .entry(&destination.parent_id)
                .ok_or(MutationError::NotFound)?;
            index.authorize(principal, parent, AnimaAccess::Write)?;
            let content = validate_content(validator, kind, &content_type, &bytes)?;
            let id = match stable_id {
                Some(value) => OpaqueId::parse(&value).map_err(|_| MutationError::InvalidPath)?,
                None => OpaqueId::generate().map_err(|_| MutationError::Storage)?,
            };
            if index.entry(&id).is_some() {
                return Err(MutationError::Collision);
            }
            let parent_common = parent.common_for_internal_mutation();
            draft.pending.push(PendingObject {
                common: CatalogEntryCommon::new(
                    id.clone(),
                    Some(destination.parent_id.clone()),
                    destination.name.clone(),
                    parent_common.owner_for_internal_mutation(),
                    parent_common.anima_access_for_internal_mutation(),
                ),
                kind,
                lifecycle: ObjectLifecycle::Live,
                revision: 1,
                object_key_epoch: 1,
                created_at: stamp.timestamp_text.clone(),
                content,
            });
            draft
                .preconditions
                .push(index.vacancy_precondition(&destination)?);
            draft.changes.push(change(&id, Some(1), None));
        }
        LogicalMutation::Write {
            target,
            expected_revision,
            content_type,
            bytes,
        } => {
            let entry = index.resolve(&target, false)?;
            let object = entry
                .object_payload()
                .ok_or(MutationError::WrongEntryKind)?;
            index.authorize(principal, entry, AnimaAccess::Write)?;
            draft
                .preconditions
                .push(index.source_precondition(entry, Some(expected_revision))?);
            let content = validate_content(validator, object.kind(), &content_type, &bytes)?;
            let path = index.path_for(entry);
            let node = read_snapshot
                .parse_node(path)
                .map_err(|_| MutationError::Storage)?;
            let metadata = read_snapshot
                .authenticated_metadata(node)
                .map_err(|_| MutationError::Storage)?;
            let revision = object
                .revision()
                .checked_add(1)
                .ok_or(MutationError::RevisionConflict)?;
            let epoch = object
                .object_key_epoch()
                .checked_add(1)
                .ok_or(MutationError::RevisionConflict)?;
            draft.pending.push(PendingObject {
                common: entry.common_for_internal_mutation().clone(),
                kind: object.kind(),
                lifecycle: ObjectLifecycle::Live,
                revision,
                object_key_epoch: epoch,
                created_at: metadata.created_at,
                content,
            });
            draft
                .changes
                .push(change(entry.stable_id(), Some(revision), None));
        }
        LogicalMutation::Move {
            source,
            destination,
            expected_revision,
        } => {
            let entry = index.resolve(&source, false)?;
            if entry.parent_id().is_none() {
                return Err(MutationError::SourceDescendant);
            }
            let destination = index.destination(&destination)?;
            let parent = index
                .entry(&destination.parent_id)
                .ok_or(MutationError::NotFound)?;
            index.authorize(principal, entry, AnimaAccess::Manage)?;
            index.authorize(principal, parent, AnimaAccess::Manage)?;
            index.ensure_destination_preserves_policy(entry, &destination.parent_id)?;
            index.ensure_not_descendant(entry.stable_id(), &destination.parent_id)?;
            draft
                .preconditions
                .push(index.source_precondition(entry, expected_revision)?);
            draft
                .preconditions
                .push(index.vacancy_precondition(&destination)?);
            let moved = entry
                .common_for_internal_mutation()
                .clone()
                .moved_for_internal_mutation(destination.parent_id, destination.name);
            let replacement = match entry.object_payload() {
                Some(object) => CatalogGenerationEntry::object(moved, object.clone()),
                None => CatalogGenerationEntry::folder(moved),
            };
            upsert(&mut draft.entries, replacement);
            draft.changes.push(existing_change(entry));
        }
        LogicalMutation::Trash {
            target,
            trash_folder,
            expected_revision,
        } => {
            let entry = index.resolve(&target, false)?;
            let trash = index.resolve(&trash_folder, false)?;
            if entry.parent_id().is_none()
                || !trash.is_folder()
                || entry.stable_id() == trash.stable_id()
            {
                return Err(MutationError::InvalidLifecycle);
            }
            index.authorize(principal, entry, AnimaAccess::Manage)?;
            index.authorize(principal, trash, AnimaAccess::Manage)?;
            index.ensure_not_descendant(entry.stable_id(), trash.stable_id())?;
            let destination = index.destination_under(trash.stable_id(), entry.name().clone())?;
            draft
                .preconditions
                .push(index.source_precondition(entry, expected_revision)?);
            draft
                .preconditions
                .push(index.vacancy_precondition(&destination)?);
            let original_parent = entry
                .parent_id()
                .cloned()
                .ok_or(MutationError::InvalidLifecycle)?;
            let moved = entry
                .common_for_internal_mutation()
                .clone()
                .moved_for_internal_mutation(trash.stable_id().clone(), entry.name().clone());
            let replacement = trash_entry(
                entry.clone(),
                moved,
                trash.stable_id(),
                original_parent,
                stamp.timestamp_ms,
            )?;
            upsert(&mut draft.entries, replacement);
            draft.changes.push(existing_change(entry));
        }
        LogicalMutation::Restore {
            target,
            destination,
            expected_revision,
        } => {
            let entry = index.resolve(&target, true)?;
            let destination = match entry.object_payload() {
                Some(object) => {
                    let ObjectLifecycle::Trashed(metadata) = object.lifecycle() else {
                        return Err(MutationError::InvalidLifecycle);
                    };
                    restore_destination(
                        index,
                        destination,
                        metadata.original_parent_id(),
                        metadata.original_name(),
                    )?
                }
                None => {
                    let FolderLifecycle::Trashed(metadata) = entry.common_folder_lifecycle() else {
                        return Err(MutationError::InvalidLifecycle);
                    };
                    restore_destination(
                        index,
                        destination,
                        metadata.original_parent_id(),
                        metadata.original_name(),
                    )?
                }
            };
            let parent = index
                .entry(&destination.parent_id)
                .ok_or(MutationError::NotFound)?;
            index.authorize(principal, entry, AnimaAccess::Manage)?;
            index.authorize(principal, parent, AnimaAccess::Manage)?;
            index.ensure_destination_preserves_policy(entry, &destination.parent_id)?;
            index.ensure_not_descendant(entry.stable_id(), &destination.parent_id)?;
            draft
                .preconditions
                .push(index.source_precondition(entry, expected_revision)?);
            draft
                .preconditions
                .push(index.vacancy_precondition(&destination)?);
            let moved = entry
                .common_for_internal_mutation()
                .clone()
                .moved_for_internal_mutation(destination.parent_id, destination.name);
            let replacement = match entry.object_payload() {
                Some(object) => CatalogGenerationEntry::object(
                    moved,
                    CatalogObject::new(
                        object.revision(),
                        object.physical_name().clone(),
                        object.content_hash().clone(),
                        object.kind(),
                        object.wrapped_dek().clone(),
                        ObjectLifecycle::Live,
                    )
                    .map_err(|_| MutationError::InvalidLifecycle)?,
                ),
                None => CatalogGenerationEntry::folder(
                    moved.with_folder_lifecycle(FolderLifecycle::Live),
                ),
            };
            upsert(&mut draft.entries, replacement);
            draft.changes.push(existing_change(entry));
        }
        LogicalMutation::ApplyPatch { .. } => {
            return Err(MutationError::Patch("invalid_operation_routing"));
        }
    }
    Ok(())
}

fn restore_destination(
    index: &CatalogIndex<'_>,
    explicit: Option<String>,
    original_parent: &OpaqueId,
    original_name: &crate::folders::PortableName,
) -> Result<Destination, MutationError> {
    match explicit {
        Some(path) => index.destination(&path),
        None => index.destination_under(original_parent, original_name.clone()),
    }
}

fn trash_entry(
    entry: CatalogGenerationEntry,
    moved: CatalogEntryCommon,
    trash_id: &OpaqueId,
    original_parent: OpaqueId,
    timestamp_ms: u64,
) -> Result<CatalogGenerationEntry, MutationError> {
    Ok(match entry.object_payload() {
        Some(object) => CatalogGenerationEntry::object(
            moved,
            CatalogObject::new(
                object.revision(),
                object.physical_name().clone(),
                object.content_hash().clone(),
                object.kind(),
                object.wrapped_dek().clone(),
                ObjectLifecycle::Trashed(
                    TrashMetadata::new(
                        trash_id.clone(),
                        original_parent,
                        entry.name().clone(),
                        timestamp_ms,
                    )
                    .map_err(|_| MutationError::InvalidLifecycle)?,
                ),
            )
            .map_err(|_| MutationError::InvalidLifecycle)?,
        ),
        None => CatalogGenerationEntry::folder(
            moved.with_folder_lifecycle(FolderLifecycle::Trashed(
                FolderTrashMetadata::new(
                    trash_id.clone(),
                    original_parent,
                    entry.name().clone(),
                    timestamp_ms,
                )
                .map_err(|_| MutationError::InvalidLifecycle)?,
            )),
        ),
    })
}

fn existing_change(entry: &CatalogGenerationEntry) -> MutationChange {
    change(
        entry.stable_id(),
        entry.object_payload().map(CatalogObject::revision),
        entry
            .object_payload()
            .map(|object| object.content_hash().as_str().to_owned()),
    )
}

fn change(id: &OpaqueId, revision: Option<u64>, content_hash: Option<String>) -> MutationChange {
    MutationChange {
        stable_id: id.as_str().to_owned(),
        revision,
        content_hash,
    }
}

fn upsert(entries: &mut Vec<CatalogGenerationEntry>, replacement: CatalogGenerationEntry) {
    if let Some(existing) = entries
        .iter_mut()
        .find(|entry| entry.stable_id() == replacement.stable_id())
    {
        *existing = replacement;
    } else {
        entries.push(replacement);
    }
}
