use crate::catalog::{CatalogGeneration, CatalogGenerationEntry};
use crate::crypto::FrkSubkeys;
use crate::logical::CoreFsReadSnapshot;
use crate::rotation::FrkKeyring;
use crate::transaction::{
    CatalogPrecondition, CommitConflict, CommitError, CoreCommitCoordinator, ValidationSnapshot,
};

use super::operations::plan_non_patch;
use super::patch::plan_patch_mutation;
use super::preflight::CatalogIndex;
use super::preparation::{prepare_object, PendingObject};
use super::{
    ContentFormatValidator, ConverterMutationAuthority, ConverterPrincipal, LogicalMutation,
    MutationChange, MutationError, MutationResult, MutationStamp,
};

pub(crate) struct CoreFsShadowMutator<'a> {
    _authority: &'a ConverterMutationAuthority,
    coordinator: &'a CoreCommitCoordinator,
    keys: &'a FrkSubkeys,
}

pub(super) struct MutationDraft {
    pub entries: Vec<CatalogGenerationEntry>,
    pub pending: Vec<PendingObject>,
    pub preconditions: Vec<CatalogPrecondition>,
    pub changes: Vec<MutationChange>,
}

impl<'a> CoreFsShadowMutator<'a> {
    pub(crate) fn new(
        authority: &'a ConverterMutationAuthority,
        coordinator: &'a CoreCommitCoordinator,
        keys: &'a FrkSubkeys,
    ) -> Self {
        Self {
            _authority: authority,
            coordinator,
            keys,
        }
    }

    pub(crate) fn execute(
        &self,
        principal: ConverterPrincipal,
        selected: &ValidationSnapshot,
        operation: LogicalMutation,
        stamp: MutationStamp,
        validator: &dyn ContentFormatValidator,
    ) -> Result<MutationResult, MutationError> {
        self.ensure_selected_is_current(selected)?;
        let keyring = FrkKeyring::new([self.keys]).map_err(|_| MutationError::Storage)?;
        let read_snapshot = CoreFsReadSnapshot::open(self.coordinator, selected, &keyring)
            .map_err(|_| MutationError::Storage)?;
        let index = CatalogIndex::new(selected.catalog())?;
        let mut draft = MutationDraft {
            entries: selected.catalog().entries().to_vec(),
            pending: Vec::new(),
            preconditions: Vec::new(),
            changes: Vec::new(),
        };

        match operation {
            LogicalMutation::ApplyPatch {
                patch,
                expected_revisions,
                add_formats,
                trash_folder,
            } => plan_patch_mutation(
                principal,
                &index,
                &read_snapshot,
                &mut draft,
                &patch,
                expected_revisions,
                add_formats,
                &trash_folder,
                &stamp,
                validator,
            )?,
            operation => plan_non_patch(
                principal,
                &index,
                &read_snapshot,
                &mut draft,
                operation,
                &stamp,
                validator,
            )?,
        }

        let mut prepared = Vec::with_capacity(draft.pending.len());
        for pending in draft.pending.drain(..) {
            let (revision, entry, change) =
                prepare_object(self.coordinator, self.keys, pending, &stamp)?;
            upsert(&mut draft.entries, entry);
            if let Some(result) = draft
                .changes
                .iter_mut()
                .find(|result| result.stable_id == change.stable_id)
            {
                result.revision = change.revision;
                result.content_hash = change.content_hash;
            }
            prepared.push(revision);
        }

        let entries = draft.entries;
        let next = self
            .coordinator
            .advance_validation_snapshot(
                self.keys,
                selected,
                &prepared,
                &draft.preconditions,
                move |_current, generation| CatalogGeneration::new(generation, entries),
            )
            .map_err(map_commit_error)?;
        draft
            .changes
            .sort_by(|left, right| left.stable_id.cmp(&right.stable_id));
        Ok(MutationResult {
            generation: next.head().generation(),
            changes: draft.changes,
            atomic: true,
        })
    }

    fn ensure_selected_is_current(
        &self,
        selected: &ValidationSnapshot,
    ) -> Result<(), MutationError> {
        let current = self
            .coordinator
            .load_validation_snapshot(self.keys)
            .map_err(|_| MutationError::Storage)?
            .ok_or(MutationError::Storage)?;
        if current.head() != selected.head() || current.catalog() != selected.catalog() {
            return Err(MutationError::OptimisticConflict);
        }
        Ok(())
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

fn map_commit_error(error: CommitError) -> MutationError {
    match error {
        CommitError::Conflict(CommitConflict::SelectedValidationChanged)
        | CommitError::Conflict(CommitConflict::PathOrRevision { .. })
        | CommitError::Conflict(CommitConflict::DestinationOccupied { .. })
        | CommitError::Conflict(CommitConflict::InvalidDestinationParent { .. })
        | CommitError::Conflict(CommitConflict::MissingSourcePrecondition { .. })
        | CommitError::Conflict(CommitConflict::MissingDestinationPrecondition { .. }) => {
            MutationError::OptimisticConflict
        }
        _ => MutationError::Storage,
    }
}
