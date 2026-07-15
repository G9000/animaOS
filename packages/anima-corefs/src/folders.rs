//! First-class portable folder contracts.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use serde_json::Value;
use unicode_normalization::UnicodeNormalization;

use crate::id::OpaqueId;

pub const MAX_PORTABLE_NAME_BYTES: usize = 255;
pub const MAX_CLIENT_ID_BYTES: usize = 64;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct PortableName(String);

impl PortableName {
    pub fn parse(value: &str) -> Result<Self, FolderError> {
        if value.is_empty() {
            return Err(FolderError::InvalidName("name is empty"));
        }
        if value == "." || value == ".." {
            return Err(FolderError::InvalidName("name is a reserved component"));
        }
        if value.len() > MAX_PORTABLE_NAME_BYTES {
            return Err(FolderError::InvalidName("name exceeds the portable limit"));
        }
        if value.contains(['/', '\\']) {
            return Err(FolderError::InvalidName("name contains a path separator"));
        }
        if value.chars().any(char::is_control) {
            return Err(FolderError::InvalidName(
                "name contains a control character",
            ));
        }
        if !value.nfc().eq(value.chars()) {
            return Err(FolderError::InvalidName("name is not NFC-normalized"));
        }
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FolderOwner {
    User,
    Anima,
    Shared,
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ClientId(String);

impl ClientId {
    pub fn parse(value: &str) -> Result<Self, FolderError> {
        let bytes = value.as_bytes();
        let has_canonical_bounds = matches!(bytes.first(), Some(b'a'..=b'z' | b'0'..=b'9'))
            && matches!(bytes.last(), Some(b'a'..=b'z' | b'0'..=b'9'));
        if bytes.is_empty()
            || bytes.len() > MAX_CLIENT_ID_BYTES
            || !has_canonical_bounds
            || !bytes
                .iter()
                .all(|byte| matches!(byte, b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.'))
        {
            return Err(FolderError::InvalidClientId);
        }
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
#[cfg(test)]
pub(crate) enum RoleAssignmentAuthority {
    User,
    Anima,
    UserForClient(ClientId),
    Client(ClientId),
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct FolderRole(String);

impl FolderRole {
    /// Parses a role already present in an authenticated catalog.
    ///
    /// New assignments use the crate-private authorized parser so callers
    /// cannot mint reserved roles by supplying an authority label.
    pub fn parse_existing(value: &str) -> Result<Self, FolderError> {
        validate_role_syntax(value)?;
        Ok(Self(value.to_owned()))
    }

    #[cfg(test)]
    pub(crate) fn parse_authorized(
        value: &str,
        authority: RoleAssignmentAuthority,
    ) -> Result<Self, FolderError> {
        validate_role_syntax(value)?;
        if let Some(role) = value.strip_prefix("core.") {
            debug_assert!(!role.is_empty());
            if authority != RoleAssignmentAuthority::User {
                return Err(FolderError::RoleAuthority);
            }
        } else if value.starts_with("client:") {
            let mut parts = value.split(':');
            let valid_namespace = parts.next() == Some("client");
            let client_id = parts.next().ok_or(FolderError::InvalidRoleNamespace)?;
            let role = parts.next().ok_or(FolderError::InvalidRoleNamespace)?;
            debug_assert!(valid_namespace && !role.is_empty() && parts.next().is_none());
            let client_id = ClientId::parse(client_id)?;
            if !matches!(
                authority,
                RoleAssignmentAuthority::UserForClient(ref authorized) if authorized == &client_id
            ) {
                return Err(FolderError::RoleAuthority);
            }
        }
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FolderEntry {
    id: OpaqueId,
    parent_id: Option<OpaqueId>,
    name: PortableName,
    owner: FolderOwner,
    role: Option<FolderRole>,
    client_metadata: BTreeMap<String, Value>,
}

impl FolderEntry {
    pub fn new(
        id: OpaqueId,
        parent_id: Option<OpaqueId>,
        name: PortableName,
        owner: FolderOwner,
    ) -> Self {
        Self {
            id,
            parent_id,
            name,
            owner,
            role: None,
            client_metadata: BTreeMap::new(),
        }
    }

    pub fn id(&self) -> &OpaqueId {
        &self.id
    }

    pub fn parent_id(&self) -> Option<&OpaqueId> {
        self.parent_id.as_ref()
    }

    pub fn name(&self) -> &PortableName {
        &self.name
    }

    pub fn owner(&self) -> FolderOwner {
        self.owner
    }

    pub fn role(&self) -> Option<&FolderRole> {
        self.role.as_ref()
    }

    pub fn client_metadata(&self) -> &BTreeMap<String, Value> {
        &self.client_metadata
    }

    #[cfg(test)]
    pub(crate) fn with_role(mut self, role: FolderRole) -> Self {
        self.role = Some(role);
        self
    }

    pub fn with_client_metadata<I, K>(
        mut self,
        writer: &ClientId,
        entries: I,
    ) -> Result<Self, FolderError>
    where
        I: IntoIterator<Item = (K, Value)>,
        K: Into<String>,
    {
        for (key, value) in entries {
            let key = key.into();
            validate_client_metadata_key(&key, writer)?;
            if self.client_metadata.insert(key, value).is_some() {
                return Err(FolderError::DuplicateClientMetadataKey);
            }
        }
        Ok(self)
    }
}

fn validate_role_syntax(value: &str) -> Result<(), FolderError> {
    if let Some(role) = value.strip_prefix("core.") {
        if role.is_empty() || role.contains(':') || role.chars().any(char::is_control) {
            return Err(FolderError::InvalidRoleNamespace);
        }
        return Ok(());
    }
    let Some(rest) = value.strip_prefix("client:") else {
        return Err(FolderError::InvalidRoleNamespace);
    };
    let mut parts = rest.split(':');
    let client_id = parts.next().ok_or(FolderError::InvalidRoleNamespace)?;
    let role = parts.next().ok_or(FolderError::InvalidRoleNamespace)?;
    if role.is_empty() || role.chars().any(char::is_control) || parts.next().is_some() {
        return Err(FolderError::InvalidRoleNamespace);
    }
    ClientId::parse(client_id)?;
    Ok(())
}

fn validate_client_metadata_key(value: &str, writer: &ClientId) -> Result<(), FolderError> {
    let mut parts = value.split(':');
    if parts.next() != Some("client") {
        return Err(FolderError::InvalidClientMetadataNamespace);
    }
    let client_id = parts
        .next()
        .ok_or(FolderError::InvalidClientMetadataNamespace)?;
    let key = parts
        .next()
        .ok_or(FolderError::InvalidClientMetadataNamespace)?;
    if key.is_empty() || key.chars().any(char::is_control) || parts.next().is_some() {
        return Err(FolderError::InvalidClientMetadataNamespace);
    }
    if &ClientId::parse(client_id)? != writer {
        return Err(FolderError::ClientMetadataAuthority);
    }
    Ok(())
}

pub fn validate_folder_tree(
    folders: &[FolderEntry],
    non_folder_ids: &[OpaqueId],
) -> Result<(), FolderError> {
    let root_count = folders
        .iter()
        .filter(|folder| folder.parent_id.is_none())
        .count();
    if root_count != 1 {
        return Err(FolderError::InvalidRootCount(root_count));
    }
    let folder_ids: HashSet<_> = folders.iter().map(|folder| &folder.id).collect();
    let folders_by_id: HashMap<_, _> = folders.iter().map(|folder| (&folder.id, folder)).collect();
    let non_folder_ids: HashSet<_> = non_folder_ids.iter().collect();
    for folder in folders {
        if let Some(parent_id) = &folder.parent_id {
            if parent_id == &folder.id {
                return Err(FolderError::SelfParent(folder.id.as_str().to_owned()));
            }
            if non_folder_ids.contains(parent_id) {
                return Err(FolderError::ParentNotFolder(parent_id.as_str().to_owned()));
            }
            if !folder_ids.contains(parent_id) {
                return Err(FolderError::Orphan(folder.id.as_str().to_owned()));
            }
        }
    }
    let mut complete = HashSet::new();
    for folder in folders {
        let mut current = folder;
        let mut path = Vec::new();
        let mut path_ids = HashSet::new();
        loop {
            if complete.contains(&current.id) {
                break;
            }
            if !path_ids.insert(&current.id) {
                return Err(FolderError::Cycle(current.id.as_str().to_owned()));
            }
            path.push(&current.id);
            let Some(parent_id) = &current.parent_id else {
                break;
            };
            current = folders_by_id
                .get(parent_id)
                .expect("parent existence validated above");
        }
        complete.extend(path);
    }
    let mut sibling_names = BTreeSet::new();
    for folder in folders {
        if !sibling_names.insert((folder.parent_id.as_ref(), &folder.name)) {
            return Err(FolderError::DuplicateSiblingName(
                folder.name.as_str().to_owned(),
            ));
        }
    }
    let mut roles = BTreeSet::new();
    for folder in folders {
        if let Some(role) = &folder.role {
            if !roles.insert(role) {
                return Err(FolderError::DuplicateRole(role.as_str().to_owned()));
            }
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum FolderError {
    #[error("invalid portable folder name: {0}")]
    InvalidName(&'static str),
    #[error("folder tree must contain exactly one root, found {0}")]
    InvalidRootCount(usize),
    #[error("folder has no folder parent: {0}")]
    Orphan(String),
    #[error("folder parent is not a folder: {0}")]
    ParentNotFolder(String),
    #[error("folder cannot parent itself: {0}")]
    SelfParent(String),
    #[error("folder parent cycle contains: {0}")]
    Cycle(String),
    #[error("duplicate sibling folder name: {0}")]
    DuplicateSiblingName(String),
    #[error("duplicate folder role: {0}")]
    DuplicateRole(String),
    #[error("folder role assignment lacks user authority")]
    RoleAuthority,
    #[error("invalid canonical client ID")]
    InvalidClientId,
    #[error("invalid folder role namespace")]
    InvalidRoleNamespace,
    #[error("invalid client metadata namespace")]
    InvalidClientMetadataNamespace,
    #[error("client metadata writer does not match the namespace")]
    ClientMetadataAuthority,
    #[error("duplicate client metadata key")]
    DuplicateClientMetadataKey,
}

#[cfg(test)]
mod tests {
    use crate::id::OpaqueId;

    use super::{
        validate_folder_tree, ClientId, FolderEntry, FolderOwner, FolderRole, PortableName,
        RoleAssignmentAuthority,
    };

    fn folder(id: &str, parent: Option<&str>, name: &str) -> FolderEntry {
        FolderEntry::new(
            OpaqueId::parse(id).unwrap(),
            parent.map(|value| OpaqueId::parse(value).unwrap()),
            PortableName::parse(name).unwrap(),
            FolderOwner::User,
        )
    }

    #[test]
    fn reserved_role_assignment_requires_internal_user_authority() {
        assert!(FolderRole::parse_authorized("core.notes", RoleAssignmentAuthority::User).is_ok());
        assert!(
            FolderRole::parse_authorized("core.notes", RoleAssignmentAuthority::Anima).is_err()
        );

        let photos = ClientId::parse("photo-importer").unwrap();
        let other = ClientId::parse("other-client").unwrap();
        assert!(FolderRole::parse_authorized(
            "client:photo-importer:gallery",
            RoleAssignmentAuthority::UserForClient(photos.clone()),
        )
        .is_ok());
        assert!(FolderRole::parse_authorized(
            "client:photo-importer:gallery",
            RoleAssignmentAuthority::UserForClient(other),
        )
        .is_err());
        assert!(FolderRole::parse_authorized(
            "client:photo-importer:gallery",
            RoleAssignmentAuthority::Client(photos),
        )
        .is_err());
    }

    #[test]
    fn assigned_roles_are_unique_across_the_tree() {
        const ROOT: &str = "01J00000000000000000000000";
        const FIRST: &str = "01J00000000000000000000001";
        const SECOND: &str = "01J00000000000000000000002";
        let notes =
            FolderRole::parse_authorized("core.notes", RoleAssignmentAuthority::User).unwrap();
        assert!(validate_folder_tree(
            &[
                folder(ROOT, None, "Root"),
                folder(FIRST, Some(ROOT), "Notes").with_role(notes.clone()),
                folder(SECOND, Some(ROOT), "Also notes").with_role(notes),
            ],
            &[],
        )
        .is_err());
    }
}
