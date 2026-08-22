use crate::catalog::{CatalogGeneration, CatalogGenerationEntry};
use crate::crypto::FrkSubkeys;
use crate::logical::CoreFsReadSnapshot;
use crate::rotation::FrkKeyring;
use crate::transaction::{
    CatalogPrecondition, CommitConflict, CommitError, CommitOutcome, CommittedCatalog,
    CoreCommitCoordinator, PreparedObjectRevision, ValidationSnapshot,
};

use super::operations::plan_non_patch;
use super::patch::plan_patch_mutation;
use super::preflight::CatalogIndex;
use super::preparation::{prepare_object, PendingObject};
use super::{
    ContentFormatValidator, ConverterMutationAuthority, ConverterPrincipal, LogicalMutation,
    MutationChange, MutationCommitMode, MutationError, MutationPrincipal, MutationResult,
    MutationStamp,
};

pub struct CoreFsMutationExecutor<'a> {
    coordinator: &'a CoreCommitCoordinator,
    keys: &'a FrkSubkeys,
}

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
        if matches!(operation, LogicalMutation::ActivateAuthority) {
            return Err(MutationError::InvalidLifecycle);
        }
        self.ensure_selected_is_current(selected)?;
        let keyring = FrkKeyring::new([self.keys]).map_err(|_| MutationError::Storage)?;
        let read_snapshot = CoreFsReadSnapshot::open(self.coordinator, selected, &keyring)
            .map_err(|_| MutationError::Storage)?;
        let (mut draft, prepared) = plan_and_prepare(
            self.coordinator,
            self.keys,
            principal,
            selected.catalog(),
            &read_snapshot,
            operation,
            &stamp,
            validator,
        )?;

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
            catalog_hash: next.head().catalog_hash().to_owned(),
            changes: draft.changes,
            atomic: true,
            cutover_committed: false,
            recovery_pending: false,
            invalidation_delivered: false,
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

impl<'a> CoreFsMutationExecutor<'a> {
    pub fn new(coordinator: &'a CoreCommitCoordinator, keys: &'a FrkSubkeys) -> Self {
        Self { coordinator, keys }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn execute(
        &self,
        principal: MutationPrincipal,
        selected_generation: u64,
        selected_catalog_hash: &str,
        mode: MutationCommitMode,
        operation: LogicalMutation,
        stamp: MutationStamp,
        validator: &dyn ContentFormatValidator,
    ) -> Result<MutationResult, MutationError> {
        let principal = match principal {
            MutationPrincipal::User => ConverterPrincipal::User,
            MutationPrincipal::Anima => ConverterPrincipal::Anima,
        };
        match mode {
            MutationCommitMode::FirstMutation { cutover_epoch } => {
                let selected = self
                    .coordinator
                    .load_validation_snapshot(self.keys)
                    .map_err(map_commit_error)?
                    .ok_or(MutationError::Storage)?;
                ensure_selected(
                    selected.head().generation(),
                    selected.head().catalog_hash(),
                    selected_generation,
                    selected_catalog_hash,
                )?;
                let keyring = FrkKeyring::new([self.keys]).map_err(|_| MutationError::Storage)?;
                let read_snapshot = CoreFsReadSnapshot::open(self.coordinator, &selected, &keyring)
                    .map_err(|_| MutationError::Storage)?;
                let (draft, prepared) = plan_and_prepare(
                    self.coordinator,
                    self.keys,
                    principal,
                    selected.catalog(),
                    &read_snapshot,
                    operation,
                    &stamp,
                    validator,
                )?;
                let changes = draft.changes;
                let entries = draft.entries;
                let outcome = self
                    .coordinator
                    .commit_first_mutation(
                        self.keys,
                        cutover_epoch,
                        &prepared,
                        &draft.preconditions,
                        move |_current, generation| CatalogGeneration::new(generation, entries),
                        |_| Err("external invalidation required".to_owned()),
                    )
                    .map_err(map_commit_error)?;
                Ok(committed_result(outcome, changes, true))
            }
            MutationCommitMode::Normal => {
                if matches!(operation, LogicalMutation::ActivateAuthority) {
                    return Err(MutationError::InvalidLifecycle);
                }
                let committed = self
                    .coordinator
                    .load_committed(self.keys)
                    .map_err(map_commit_error)?
                    .ok_or(MutationError::Storage)?;
                ensure_selected(
                    committed.head().generation(),
                    committed.head().catalog_hash(),
                    selected_generation,
                    selected_catalog_hash,
                )?;
                self.execute_committed(principal, &committed, operation, stamp, validator)
            }
        }
    }

    fn execute_committed(
        &self,
        principal: ConverterPrincipal,
        committed: &CommittedCatalog,
        operation: LogicalMutation,
        stamp: MutationStamp,
        validator: &dyn ContentFormatValidator,
    ) -> Result<MutationResult, MutationError> {
        let keyring = FrkKeyring::new([self.keys]).map_err(|_| MutationError::Storage)?;
        let read_snapshot =
            CoreFsReadSnapshot::open_committed(self.coordinator, committed, &keyring)
                .map_err(|_| MutationError::Storage)?;
        let (draft, prepared) = plan_and_prepare(
            self.coordinator,
            self.keys,
            principal,
            committed.catalog(),
            &read_snapshot,
            operation,
            &stamp,
            validator,
        )?;
        let changes = draft.changes;
        let entries = draft.entries;
        let outcome = self
            .coordinator
            .commit(
                self.keys,
                &prepared,
                &draft.preconditions,
                move |_current, generation| CatalogGeneration::new(generation, entries),
                |_| Err("external invalidation required".to_owned()),
            )
            .map_err(map_commit_error)?;
        Ok(committed_result(outcome, changes, false))
    }
}

#[allow(clippy::too_many_arguments)]
fn plan_and_prepare(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    principal: ConverterPrincipal,
    catalog: &CatalogGeneration,
    read_snapshot: &CoreFsReadSnapshot,
    operation: LogicalMutation,
    stamp: &MutationStamp,
    validator: &dyn ContentFormatValidator,
) -> Result<(MutationDraft, Vec<PreparedObjectRevision>), MutationError> {
    let index = CatalogIndex::new(catalog)?;
    let mut draft = MutationDraft {
        entries: catalog.entries().to_vec(),
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
            read_snapshot,
            &mut draft,
            &patch,
            expected_revisions,
            add_formats,
            &trash_folder,
            stamp,
            validator,
        )?,
        operation => plan_non_patch(
            principal,
            &index,
            read_snapshot,
            &mut draft,
            operation,
            stamp,
            validator,
        )?,
    }

    let mut prepared = Vec::with_capacity(draft.pending.len());
    for pending in draft.pending.drain(..) {
        let (revision, entry, change) = prepare_object(coordinator, keys, pending, stamp)?;
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
    Ok((draft, prepared))
}

fn ensure_selected(
    actual_generation: u64,
    actual_catalog_hash: &str,
    selected_generation: u64,
    selected_catalog_hash: &str,
) -> Result<(), MutationError> {
    if actual_generation != selected_generation || actual_catalog_hash != selected_catalog_hash {
        return Err(MutationError::OptimisticConflict);
    }
    Ok(())
}

fn committed_result(
    outcome: CommitOutcome,
    mut changes: Vec<MutationChange>,
    cutover_committed: bool,
) -> MutationResult {
    changes.sort_by(|left, right| left.stable_id.cmp(&right.stable_id));
    MutationResult {
        generation: outcome.generation(),
        catalog_hash: outcome.catalog_hash().to_owned(),
        changes,
        atomic: true,
        cutover_committed,
        recovery_pending: outcome.recovery_pending(),
        invalidation_delivered: outcome.invalidation_delivered(),
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
