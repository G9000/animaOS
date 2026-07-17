use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;

use anima_file_tools::{
    parse_patch, plan_patch, MutationAtomicity, OperationControl, PatchError, PatchSnapshot,
    PlannedMutation, ReadBackend, MAX_PATCH_BYTES,
};

use crate::catalog::{
    CatalogEntryCommon, CatalogGenerationEntry, CatalogObject, FolderLifecycle,
    FolderTrashMetadata, ObjectLifecycle, TrashMetadata,
};
use crate::crypto::ObjectKind;
use crate::id::OpaqueId;
use crate::logical::{CoreFsReadSnapshot, LogicalPath};
use crate::policy::AnimaAccess;

use super::executor::MutationDraft;
use super::preflight::{CatalogIndex, Destination};
use super::preparation::{validate_content, PendingObject};
use super::{
    ContentFormatValidator, ConverterPrincipal, MutationChange, MutationError, MutationStamp,
    MutationTarget, PatchAddFormat,
};

#[allow(clippy::too_many_arguments)]
pub(super) fn plan_patch_mutation(
    principal: ConverterPrincipal,
    index: &CatalogIndex<'_>,
    read_snapshot: &CoreFsReadSnapshot,
    draft: &mut MutationDraft,
    patch_text: &str,
    expected_revisions: BTreeMap<String, u64>,
    add_formats: BTreeMap<String, PatchAddFormat>,
    trash_target: &MutationTarget,
    stamp: &MutationStamp,
    validator: &dyn ContentFormatValidator,
) -> Result<(), MutationError> {
    let adapter = PatchSnapshotAdapter {
        snapshot: read_snapshot,
        index,
    };
    let patch = parse_patch(patch_text).map_err(map_patch_error)?;
    let plan = plan_patch(&adapter, &patch, MutationAtomicity::CatalogGeneration)
        .map_err(map_patch_error)?;
    if plan.atomicity != MutationAtomicity::CatalogGeneration {
        return Err(MutationError::Patch("non_atomic_plan"));
    }

    let trash = index.resolve(trash_target, false)?;
    if !trash.is_folder() {
        return Err(MutationError::WrongEntryKind);
    }
    index.authorize(principal, trash, AnimaAccess::Manage)?;

    let mut virtual_files = BTreeMap::<String, VirtualFile>::new();
    for entry in index.catalog_entries() {
        if index.hidden(entry) {
            continue;
        }
        virtual_files.insert(
            index.path_for(entry).to_owned(),
            VirtualFile {
                entry: entry.clone(),
                pending_index: None,
                selected: true,
            },
        );
    }
    let mut consumed_expected = BTreeSet::new();
    let mut consumed_formats = BTreeSet::new();
    let mut conditioned_sources = BTreeSet::new();
    let mut trash_names: BTreeSet<String> = index
        .catalog_entries()
        .iter()
        .filter(|entry| entry.parent_id() == Some(trash.stable_id()))
        .map(|entry| entry.name().as_str().to_owned())
        .collect();

    for mutation in plan.mutations {
        match mutation {
            PlannedMutation::Write {
                path,
                content,
                remove_source,
            } => {
                let target_path = path.as_str().to_owned();
                let destination = index.destination_allowing_virtual(
                    &target_path,
                    virtual_files.contains_key(&target_path),
                )?;
                let source_path = remove_source
                    .as_ref()
                    .map(|path| path.as_str())
                    .unwrap_or(path.as_str())
                    .to_owned();
                let existing = virtual_files.remove(&source_path);
                match existing {
                    Some(mut file) => {
                        let original_entry = file.entry.clone();
                        let (kind, selected_revision, selected_epoch) =
                            match file.entry.object_payload() {
                                Some(object) => (
                                    object.kind(),
                                    Some(object.revision()),
                                    Some(object.object_key_epoch()),
                                ),
                                None => {
                                    let pending = file
                                        .pending_index
                                        .and_then(|pending| draft.pending.get(pending))
                                        .ok_or(MutationError::WrongEntryKind)?;
                                    (pending.kind, None, None)
                                }
                            };
                        if file.selected
                            && conditioned_sources
                                .insert(file.entry.stable_id().as_str().to_owned())
                        {
                            let expected = expected_revisions
                                .get(&source_path)
                                .copied()
                                .ok_or(MutationError::MissingExpectedRevision)?;
                            consumed_expected.insert(source_path.clone());
                            draft
                                .preconditions
                                .push(index.source_precondition(&file.entry, Some(expected))?);
                        }
                        let moved = remove_source.is_some();
                        if moved {
                            let parent = index
                                .entry(&destination.parent_id)
                                .ok_or(MutationError::NotFound)?;
                            index.authorize(principal, &file.entry, AnimaAccess::Manage)?;
                            index.authorize(principal, parent, AnimaAccess::Manage)?;
                            index.ensure_destination_preserves_policy(
                                &file.entry,
                                &destination.parent_id,
                            )?;
                            draft
                                .preconditions
                                .push(index.vacancy_precondition(&destination)?);
                            file.entry = move_entry(file.entry, &destination);
                        } else {
                            index.authorize(principal, &file.entry, AnimaAccess::Write)?;
                        }
                        let current = match file.pending_index {
                            Some(pending_index) => Some(
                                String::from_utf8(
                                    draft
                                        .pending
                                        .get(pending_index)
                                        .ok_or(MutationError::Storage)?
                                        .content
                                        .bytes
                                        .clone(),
                                )
                                .map_err(|_| MutationError::Patch("pending_content_not_utf8"))?,
                            ),
                            None => adapter.read_text(&source_path).map_err(map_patch_error)?,
                        };
                        let unchanged_move = moved && current.as_deref() == Some(content.as_str());
                        if unchanged_move && file.pending_index.is_none() {
                            upsert(&mut draft.entries, file.entry.clone());
                            draft.changes.push(existing_change(&original_entry));
                        } else {
                            let metadata_path = if file.selected {
                                index.path_for(
                                    index
                                        .entry(file.entry.stable_id())
                                        .ok_or(MutationError::NotFound)?,
                                )
                            } else {
                                source_path.as_str()
                            };
                            let (created_at, content_type) = if file.selected {
                                adapter.metadata(metadata_path)?
                            } else {
                                let pending = file
                                    .pending_index
                                    .and_then(|pending| draft.pending.get(pending))
                                    .ok_or(MutationError::Storage)?;
                                (
                                    pending.created_at.clone(),
                                    pending.content.content_type.clone(),
                                )
                            };
                            let validated = validate_content(
                                validator,
                                kind,
                                &content_type,
                                content.as_bytes(),
                            )?;
                            let pending_index = match file.pending_index {
                                Some(pending_index) => {
                                    let pending = draft
                                        .pending
                                        .get_mut(pending_index)
                                        .ok_or(MutationError::Storage)?;
                                    pending.common =
                                        file.entry.common_for_internal_mutation().clone();
                                    pending.content = validated;
                                    pending_index
                                }
                                None => {
                                    let revision = selected_revision
                                        .ok_or(MutationError::Storage)?
                                        .checked_add(1)
                                        .ok_or(MutationError::RevisionConflict)?;
                                    let epoch = selected_epoch
                                        .ok_or(MutationError::Storage)?
                                        .checked_add(1)
                                        .ok_or(MutationError::RevisionConflict)?;
                                    draft.pending.push(PendingObject {
                                        common: file.entry.common_for_internal_mutation().clone(),
                                        kind,
                                        lifecycle: ObjectLifecycle::Live,
                                        revision,
                                        object_key_epoch: epoch,
                                        created_at,
                                        content: validated,
                                    });
                                    draft.changes.push(change(
                                        file.entry.stable_id(),
                                        Some(revision),
                                        None,
                                    ));
                                    draft.pending.len() - 1
                                }
                            };
                            file.pending_index = Some(pending_index);
                        }
                        virtual_files.insert(target_path, file);
                    }
                    None => {
                        let format = add_formats
                            .get(&target_path)
                            .ok_or(MutationError::Patch("missing_add_format"))?;
                        consumed_formats.insert(target_path.clone());
                        if format.kind == ObjectKind::Folder {
                            return Err(MutationError::WrongEntryKind);
                        }
                        let parent = index
                            .entry(&destination.parent_id)
                            .ok_or(MutationError::NotFound)?;
                        index.authorize(principal, parent, AnimaAccess::Write)?;
                        draft
                            .preconditions
                            .push(index.vacancy_precondition(&destination)?);
                        let id = OpaqueId::generate().map_err(|_| MutationError::Storage)?;
                        let parent_common = parent.common_for_internal_mutation();
                        let validated = validate_content(
                            validator,
                            format.kind,
                            &format.content_type,
                            content.as_bytes(),
                        )?;
                        let common = CatalogEntryCommon::new(
                            id.clone(),
                            Some(destination.parent_id),
                            destination.name,
                            parent_common.owner_for_internal_mutation(),
                            parent_common.anima_access_for_internal_mutation(),
                        );
                        draft.pending.push(PendingObject {
                            common: common.clone(),
                            kind: format.kind,
                            lifecycle: ObjectLifecycle::Live,
                            revision: 1,
                            object_key_epoch: 1,
                            created_at: stamp.timestamp_text.clone(),
                            content: validated,
                        });
                        let pending_index = draft.pending.len() - 1;
                        virtual_files.insert(
                            target_path,
                            VirtualFile {
                                entry: CatalogGenerationEntry::folder(common),
                                pending_index: Some(pending_index),
                                selected: false,
                            },
                        );
                        draft.changes.push(change(&id, Some(1), None));
                    }
                }
            }
            PlannedMutation::Delete { path } => {
                let path = path.as_str().to_owned();
                let file = virtual_files.remove(&path).ok_or(MutationError::NotFound)?;
                if file.selected
                    && conditioned_sources.insert(file.entry.stable_id().as_str().to_owned())
                {
                    let expected = file
                        .entry
                        .object_payload()
                        .map(|_| {
                            expected_revisions
                                .get(&path)
                                .copied()
                                .ok_or(MutationError::MissingExpectedRevision)
                        })
                        .transpose()?;
                    if expected.is_some() {
                        consumed_expected.insert(path.clone());
                    }
                    draft
                        .preconditions
                        .push(index.source_precondition(&file.entry, expected)?);
                }
                index.authorize(principal, &file.entry, AnimaAccess::Manage)?;
                if !trash_names.insert(file.entry.name().as_str().to_owned()) {
                    return Err(MutationError::Collision);
                }
                let destination =
                    index.destination_under(trash.stable_id(), file.entry.name().clone())?;
                draft
                    .preconditions
                    .push(index.vacancy_precondition(&destination)?);
                let original_parent = file
                    .entry
                    .parent_id()
                    .cloned()
                    .ok_or(MutationError::InvalidLifecycle)?;
                let moved = file
                    .entry
                    .common_for_internal_mutation()
                    .clone()
                    .moved_for_internal_mutation(
                        trash.stable_id().clone(),
                        file.entry.name().clone(),
                    );
                if let Some(pending_index) = file.pending_index {
                    let pending = draft
                        .pending
                        .get_mut(pending_index)
                        .ok_or(MutationError::Storage)?;
                    pending.common = moved;
                    pending.lifecycle = ObjectLifecycle::Trashed(
                        TrashMetadata::new(
                            trash.stable_id().clone(),
                            original_parent,
                            file.entry.name().clone(),
                            stamp.timestamp_ms,
                        )
                        .map_err(|_| MutationError::InvalidLifecycle)?,
                    );
                } else {
                    let replacement = trash_entry(
                        file.entry.clone(),
                        moved,
                        trash.stable_id(),
                        original_parent,
                        stamp.timestamp_ms,
                    )?;
                    upsert(&mut draft.entries, replacement);
                    draft.changes.push(existing_change(&file.entry));
                }
            }
        }
    }

    if consumed_expected.len() != expected_revisions.len() {
        return Err(MutationError::Patch("unexpected_expected_revision"));
    }
    if consumed_formats.len() != add_formats.len() {
        return Err(MutationError::Patch("unexpected_add_format"));
    }
    Ok(())
}

struct VirtualFile {
    entry: CatalogGenerationEntry,
    pending_index: Option<usize>,
    selected: bool,
}

struct PatchSnapshotAdapter<'a, 'b> {
    snapshot: &'a CoreFsReadSnapshot,
    index: &'b CatalogIndex<'b>,
}

impl PatchSnapshotAdapter<'_, '_> {
    fn metadata(&self, path: &str) -> Result<(String, String), MutationError> {
        let node = self
            .snapshot
            .parse_node(path)
            .map_err(|_| MutationError::NotFound)?;
        let metadata = self
            .snapshot
            .authenticated_metadata(node)
            .map_err(|_| MutationError::Storage)?;
        Ok((metadata.created_at, metadata.content_type))
    }
}

impl PatchSnapshot for PatchSnapshotAdapter<'_, '_> {
    fn read_text(&self, path: &str) -> Result<Option<String>, PatchError> {
        let entry = match self.index.resolve_live_path(path) {
            Ok(entry) => entry,
            Err(MutationError::NotFound) => return Ok(None),
            Err(_) => {
                return Err(snapshot_error(path, "invalid logical path"));
            }
        };
        if entry.object_payload().is_none() {
            return Ok(None);
        }
        let node = self
            .snapshot
            .parse_node(path)
            .map_err(|_| snapshot_error(path, "object unavailable"))?;
        let metadata = self
            .snapshot
            .authenticated_metadata(node)
            .map_err(|_| snapshot_error(path, "object metadata unavailable"))?;
        if metadata.body_encoding != crate::envelope::BodyEncoding::Utf8 {
            return Err(snapshot_error(path, "object is not declared UTF-8 text"));
        }
        let body_length = usize::try_from(metadata.body_length)
            .map_err(|_| snapshot_error(path, "object exceeds patch limits"))?;
        if body_length > MAX_PATCH_BYTES {
            return Err(snapshot_error(path, "object exceeds patch limits"));
        }
        let control = OperationControl::default();
        let mut reader = self
            .snapshot
            .open_read_at(path, 0, body_length, &control)
            .map_err(|_| snapshot_error(path, "object read failed"))?;
        let mut bytes = Vec::with_capacity(body_length);
        reader
            .read_to_end(&mut bytes)
            .map_err(|_| snapshot_error(path, "object read failed"))?;
        if bytes.len() != body_length {
            return Err(snapshot_error(path, "object read was incomplete"));
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(|_| snapshot_error(path, "object is not valid UTF-8"))
    }

    fn file_entry_exists(&self, path: &str) -> Result<bool, PatchError> {
        match self.index.resolve_live_path(path) {
            Ok(_) => Ok(true),
            Err(MutationError::NotFound) => Ok(false),
            Err(_) => Err(snapshot_error(path, "invalid logical path")),
        }
    }

    fn canonical_key(&self, path: &str) -> Result<String, PatchError> {
        LogicalPath::parse(path)
            .map(|path| path.as_str().to_owned())
            .map_err(|_| snapshot_error(path, "invalid logical path"))
    }
}

fn snapshot_error(path: &str, message: &str) -> PatchError {
    PatchError::Snapshot {
        path: path.to_owned(),
        message: message.to_owned(),
    }
}

fn move_entry(entry: CatalogGenerationEntry, destination: &Destination) -> CatalogGenerationEntry {
    let common = entry
        .common_for_internal_mutation()
        .clone()
        .moved_for_internal_mutation(destination.parent_id.clone(), destination.name.clone());
    match entry.object_payload() {
        Some(object) => CatalogGenerationEntry::object(common, object.clone()),
        None => CatalogGenerationEntry::folder(common),
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

fn map_patch_error(error: PatchError) -> MutationError {
    match error {
        PatchError::InvalidPath { .. } => MutationError::InvalidPath,
        PatchError::MissingPath { .. } => MutationError::NotFound,
        PatchError::PathAlreadyExists { .. } => MutationError::Collision,
        PatchError::Parse { .. } => MutationError::Patch("parse"),
        PatchError::ContextNotFound { .. }
        | PatchError::HunkNotFound { .. }
        | PatchError::EndOfFileMismatch { .. } => MutationError::Patch("hunk"),
        PatchError::Snapshot { .. } => MutationError::Patch("snapshot"),
    }
}
