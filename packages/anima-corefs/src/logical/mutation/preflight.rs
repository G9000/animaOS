use std::collections::HashMap;

use crate::catalog::{CatalogGeneration, CatalogGenerationEntry, FolderLifecycle, ObjectLifecycle};
use crate::folders::PortableName;
use crate::id::OpaqueId;
use crate::logical::LogicalPath;
use crate::policy::AnimaAccess;
use crate::transaction::CatalogPrecondition;

use super::{ConverterPrincipal, MutationError, MutationTarget};

pub(super) struct CatalogIndex<'a> {
    catalog: &'a CatalogGeneration,
    by_id: HashMap<&'a str, &'a CatalogGenerationEntry>,
    by_path: HashMap<String, &'a CatalogGenerationEntry>,
}

pub(super) struct Destination {
    pub parent_id: OpaqueId,
    pub name: PortableName,
}

impl<'a> CatalogIndex<'a> {
    pub fn new(catalog: &'a CatalogGeneration) -> Result<Self, MutationError> {
        let by_id: HashMap<_, _> = catalog
            .entries()
            .iter()
            .map(|entry| (entry.stable_id().as_str(), entry))
            .collect();
        let mut paths = HashMap::new();
        for entry in catalog.entries() {
            resolve_path(entry, &by_id, &mut paths)?;
        }
        let by_path = catalog
            .entries()
            .iter()
            .map(|entry| {
                (
                    paths
                        .get(entry.stable_id().as_str())
                        .expect("all catalog paths resolved")
                        .clone(),
                    entry,
                )
            })
            .collect();
        Ok(Self {
            catalog,
            by_id,
            by_path,
        })
    }

    pub fn resolve(
        &self,
        target: &MutationTarget,
        include_hidden: bool,
    ) -> Result<&'a CatalogGenerationEntry, MutationError> {
        let entry = match target {
            MutationTarget::StableId(value) => {
                let id = OpaqueId::parse(value).map_err(|_| MutationError::NotFound)?;
                self.by_id
                    .get(id.as_str())
                    .copied()
                    .ok_or(MutationError::NotFound)?
            }
            MutationTarget::Path(value) => {
                let path = LogicalPath::parse(value).map_err(|_| MutationError::InvalidPath)?;
                self.by_path
                    .get(path.as_str())
                    .copied()
                    .ok_or(MutationError::NotFound)?
            }
        };
        if !include_hidden && self.hidden(entry) {
            return Err(MutationError::NotFound);
        }
        Ok(entry)
    }

    pub fn resolve_live_path(
        &self,
        path: &str,
    ) -> Result<&'a CatalogGenerationEntry, MutationError> {
        self.resolve(&MutationTarget::Path(path.to_owned()), false)
    }

    pub fn destination(&self, path: &str) -> Result<Destination, MutationError> {
        let path = LogicalPath::parse(path).map_err(|_| MutationError::InvalidPath)?;
        if path.as_str().is_empty() {
            return Err(MutationError::InvalidPath);
        }
        if self.by_path.contains_key(path.as_str()) {
            return Err(MutationError::Collision);
        }
        let (parent_path, name) = path
            .as_str()
            .rsplit_once('/')
            .map_or(("", path.as_str()), |(parent, name)| (parent, name));
        let parent = self.resolve_live_path(parent_path)?;
        if !parent.is_folder() {
            return Err(MutationError::WrongEntryKind);
        }
        Ok(Destination {
            parent_id: parent.stable_id().clone(),
            name: PortableName::parse(name).map_err(|_| MutationError::InvalidPath)?,
        })
    }

    pub fn destination_allowing_virtual(
        &self,
        path: &str,
        occupied_by_source: bool,
    ) -> Result<Destination, MutationError> {
        let parsed = LogicalPath::parse(path).map_err(|_| MutationError::InvalidPath)?;
        if parsed.as_str().is_empty() {
            return Err(MutationError::InvalidPath);
        }
        if !occupied_by_source && self.by_path.contains_key(parsed.as_str()) {
            return Err(MutationError::Collision);
        }
        let (parent_path, name) = parsed
            .as_str()
            .rsplit_once('/')
            .map_or(("", parsed.as_str()), |(parent, name)| (parent, name));
        let parent = self.resolve_live_path(parent_path)?;
        if !parent.is_folder() {
            return Err(MutationError::WrongEntryKind);
        }
        Ok(Destination {
            parent_id: parent.stable_id().clone(),
            name: PortableName::parse(name).map_err(|_| MutationError::InvalidPath)?,
        })
    }

    pub fn destination_under(
        &self,
        parent_id: &OpaqueId,
        name: PortableName,
    ) -> Result<Destination, MutationError> {
        let parent = self.entry(parent_id).ok_or(MutationError::NotFound)?;
        if !parent.is_folder() || self.hidden(parent) {
            return Err(MutationError::WrongEntryKind);
        }
        if self
            .catalog
            .entries()
            .iter()
            .any(|entry| entry.parent_id() == Some(parent_id) && entry.name() == &name)
        {
            return Err(MutationError::Collision);
        }
        Ok(Destination {
            parent_id: parent_id.clone(),
            name,
        })
    }

    pub fn path_for(&self, entry: &CatalogGenerationEntry) -> &str {
        self.by_path
            .iter()
            .find_map(|(path, candidate)| {
                (candidate.stable_id() == entry.stable_id()).then_some(path.as_str())
            })
            .expect("indexed entry has path")
    }

    pub fn entry(&self, id: &OpaqueId) -> Option<&'a CatalogGenerationEntry> {
        self.by_id.get(id.as_str()).copied()
    }

    pub fn catalog_entries(&self) -> &'a [CatalogGenerationEntry] {
        self.catalog.entries()
    }

    pub fn hidden(&self, entry: &CatalogGenerationEntry) -> bool {
        let mut current = entry;
        loop {
            if current
                .object_payload()
                .is_some_and(|object| object.lifecycle() != &ObjectLifecycle::Live)
                || (current.is_folder()
                    && matches!(
                        current.common_folder_lifecycle(),
                        FolderLifecycle::Trashed(_)
                    ))
            {
                return true;
            }
            let Some(parent) = current.parent_id() else {
                return false;
            };
            current = self
                .by_id
                .get(parent.as_str())
                .expect("validated catalog parent exists");
        }
    }

    pub fn ensure_not_descendant(
        &self,
        source: &OpaqueId,
        destination_parent: &OpaqueId,
    ) -> Result<(), MutationError> {
        let mut current = Some(destination_parent);
        while let Some(id) = current {
            if id == source {
                return Err(MutationError::SourceDescendant);
            }
            current = self.entry(id).and_then(CatalogGenerationEntry::parent_id);
        }
        Ok(())
    }

    pub fn has_role(&self, role: &str) -> bool {
        self.catalog.entries().iter().any(|entry| {
            entry
                .common_for_internal_mutation()
                .role_for_internal_mutation()
                .is_some_and(|existing| existing.as_str() == role)
        })
    }

    pub fn authorize(
        &self,
        principal: ConverterPrincipal,
        entry: &CatalogGenerationEntry,
        required: AnimaAccess,
    ) -> Result<(), MutationError> {
        if principal == ConverterPrincipal::User
            || entry
                .common_for_internal_mutation()
                .anima_access_for_internal_mutation()
                >= required
        {
            Ok(())
        } else {
            Err(MutationError::PolicyDenied)
        }
    }

    pub fn ensure_destination_preserves_policy(
        &self,
        entry: &CatalogGenerationEntry,
        destination_parent: &OpaqueId,
    ) -> Result<(), MutationError> {
        let parent = self
            .entry(destination_parent)
            .ok_or(MutationError::NotFound)?;
        let entry_policy = entry.common_for_internal_mutation();
        let parent_policy = parent.common_for_internal_mutation();
        if entry_policy.owner_for_internal_mutation() != parent_policy.owner_for_internal_mutation()
            || entry_policy.anima_access_for_internal_mutation()
                != parent_policy.anima_access_for_internal_mutation()
        {
            return Err(MutationError::PolicyBoundaryMismatch);
        }
        Ok(())
    }

    pub fn source_precondition(
        &self,
        entry: &CatalogGenerationEntry,
        expected_revision: Option<u64>,
    ) -> Result<CatalogPrecondition, MutationError> {
        match entry.object_payload() {
            Some(object) => {
                if expected_revision != Some(object.revision()) {
                    return Err(MutationError::RevisionConflict);
                }
                CatalogPrecondition::object(self.catalog, entry.stable_id(), object.revision())
                    .map_err(|_| MutationError::RevisionConflict)
            }
            None => {
                if expected_revision.is_some() {
                    return Err(MutationError::RevisionConflict);
                }
                CatalogPrecondition::folder(self.catalog, entry.stable_id())
                    .map_err(|_| MutationError::OptimisticConflict)
            }
        }
    }

    pub fn vacancy_precondition(
        &self,
        destination: &Destination,
    ) -> Result<CatalogPrecondition, MutationError> {
        CatalogPrecondition::vacant(
            self.catalog,
            &destination.parent_id,
            destination.name.clone(),
        )
        .map_err(|_| MutationError::Collision)
    }
}

fn resolve_path<'a>(
    entry: &'a CatalogGenerationEntry,
    by_id: &HashMap<&str, &'a CatalogGenerationEntry>,
    paths: &mut HashMap<String, String>,
) -> Result<String, MutationError> {
    if let Some(path) = paths.get(entry.stable_id().as_str()) {
        return Ok(path.clone());
    }
    let path = match entry.parent_id() {
        None => String::new(),
        Some(parent_id) => {
            let parent = by_id
                .get(parent_id.as_str())
                .copied()
                .ok_or(MutationError::Storage)?;
            let parent_path = resolve_path(parent, by_id, paths)?;
            if parent_path.is_empty() {
                entry.name().as_str().to_owned()
            } else {
                format!("{}/{}", parent_path, entry.name().as_str())
            }
        }
    };
    LogicalPath::parse(&path).map_err(|_| MutationError::InvalidPath)?;
    paths.insert(entry.stable_id().as_str().to_owned(), path.clone());
    Ok(path)
}
