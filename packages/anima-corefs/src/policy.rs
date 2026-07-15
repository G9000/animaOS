//! Principal-aware folder policy and inheritance contracts.

use crate::folders::{ClientId, FolderOwner};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum AnimaAccess {
    None,
    Read,
    Write,
    Manage,
}

impl AnimaAccess {
    pub fn allows(self, operation: Operation) -> bool {
        match operation {
            Operation::Read(_) => self >= Self::Read,
            Operation::Content(_) => self >= Self::Write,
            Operation::Structural(_) => self >= Self::Manage,
            Operation::Admin(_) => false,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReadOperation {
    Read,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ContentOperation {
    Write,
    Create,
    Mkdir,
    Patch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StructuralOperation {
    Rename,
    Move,
    Trash,
    Restore,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AdminOperation {
    Owner,
    Access,
    Deny,
    ReservedRoles,
    Grants,
    Purge,
    KeyRetirement,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Operation {
    Read(ReadOperation),
    Content(ContentOperation),
    Structural(StructuralOperation),
    Admin(AdminOperation),
}

/// A principal established by the CoreFS session/capability broker.
///
/// The variants and constructors are deliberately crate-private: accepting a
/// caller-created `User` or `Client` value would turn identity labels into
/// authorization tokens. Public issuance belongs to the authenticated broker
/// introduced with the logical CoreFS API.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Principal {
    kind: PrincipalKind,
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum PrincipalKind {
    User,
    Anima,
    #[allow(dead_code)]
    Client(ClientId),
}

#[cfg(test)]
impl Principal {
    pub(crate) const fn user() -> Self {
        Self {
            kind: PrincipalKind::User,
        }
    }

    pub(crate) const fn anima() -> Self {
        Self {
            kind: PrincipalKind::Anima,
        }
    }

    pub(crate) fn client(client_id: ClientId) -> Self {
        Self {
            kind: PrincipalKind::Client(client_id),
        }
    }
}

pub fn authorize(principal: &Principal, access: AnimaAccess, operation: Operation) -> bool {
    match &principal.kind {
        PrincipalKind::User => true,
        PrincipalKind::Anima => access.allows(operation),
        // Client access additionally requires a device-local, folder-scoped
        // grant. Until the grant broker lands, client authorization is closed.
        PrincipalKind::Client(_) => false,
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LocalAnimaAccess {
    Inherit,
    Allow(AnimaAccess),
    Deny,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LocalFolderPolicy {
    owner: Option<FolderOwner>,
    anima_access: LocalAnimaAccess,
}

impl LocalFolderPolicy {
    pub const fn new(owner: Option<FolderOwner>, anima_access: LocalAnimaAccess) -> Self {
        Self {
            owner,
            anima_access,
        }
    }

    pub const fn inherit() -> Self {
        Self::new(None, LocalAnimaAccess::Inherit)
    }

    pub const fn with_owner(owner: FolderOwner) -> Self {
        Self::new(Some(owner), LocalAnimaAccess::Inherit)
    }

    pub const fn owner(&self) -> Option<FolderOwner> {
        self.owner
    }

    pub const fn anima_access(&self) -> LocalAnimaAccess {
        self.anima_access
    }

    pub fn with_anima_access(mut self, anima_access: LocalAnimaAccess) -> Self {
        self.anima_access = anima_access;
        self
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EffectiveFolderPolicy {
    owner: FolderOwner,
    anima_access: AnimaAccess,
}

impl EffectiveFolderPolicy {
    pub const fn new(owner: FolderOwner, anima_access: AnimaAccess) -> Self {
        Self {
            owner,
            anima_access,
        }
    }

    pub const fn owner(&self) -> FolderOwner {
        self.owner
    }

    pub const fn anima_access(&self) -> AnimaAccess {
        self.anima_access
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FolderPolicyTemplate {
    AnimaRoot,
    UserCreated,
    Imported,
    ClientDescendant,
    Journal,
    Notes,
    Conversations,
    Reflections,
}

pub const fn default_policy(template: FolderPolicyTemplate) -> LocalFolderPolicy {
    match template {
        FolderPolicyTemplate::AnimaRoot => LocalFolderPolicy::new(
            Some(FolderOwner::Anima),
            LocalAnimaAccess::Allow(AnimaAccess::Manage),
        ),
        FolderPolicyTemplate::UserCreated
        | FolderPolicyTemplate::Journal
        | FolderPolicyTemplate::Notes => LocalFolderPolicy::new(
            Some(FolderOwner::User),
            LocalAnimaAccess::Allow(AnimaAccess::Write),
        ),
        FolderPolicyTemplate::Imported => LocalFolderPolicy::new(
            Some(FolderOwner::User),
            LocalAnimaAccess::Allow(AnimaAccess::None),
        ),
        FolderPolicyTemplate::ClientDescendant => LocalFolderPolicy::inherit(),
        FolderPolicyTemplate::Conversations => LocalFolderPolicy::new(
            Some(FolderOwner::Shared),
            LocalAnimaAccess::Allow(AnimaAccess::Manage),
        ),
        FolderPolicyTemplate::Reflections => LocalFolderPolicy::new(
            Some(FolderOwner::Anima),
            LocalAnimaAccess::Allow(AnimaAccess::Manage),
        ),
    }
}

pub fn resolve_policy(
    policies: &[LocalFolderPolicy],
) -> Result<EffectiveFolderPolicy, PolicyError> {
    let mut owner = None;
    let mut access = None;
    let mut denied = false;

    for policy in policies {
        if let Some(local_owner) = policy.owner {
            owner = Some(local_owner);
        }
        match policy.anima_access {
            LocalAnimaAccess::Inherit => {}
            LocalAnimaAccess::Allow(local_access) if !denied => access = Some(local_access),
            LocalAnimaAccess::Allow(_) => {}
            LocalAnimaAccess::Deny => denied = true,
        }
    }

    let owner = owner.ok_or(PolicyError::MissingOwner)?;
    if denied {
        return Ok(EffectiveFolderPolicy::new(owner, AnimaAccess::None));
    }
    let access = access.ok_or(PolicyError::MissingAccess)?;
    Ok(EffectiveFolderPolicy::new(owner, access))
}

pub fn validate_policy_change(
    principal: &Principal,
    ancestors: &[EffectiveFolderPolicy],
    proposed: &LocalFolderPolicy,
) -> Result<(), PolicyError> {
    if principal.kind == PrincipalKind::User {
        return Ok(());
    }

    if principal.kind == PrincipalKind::Anima
        && ancestors
            .iter()
            .any(|policy| policy.owner == FolderOwner::User)
    {
        let inherited_access = ancestors
            .last()
            .map_or(AnimaAccess::None, EffectiveFolderPolicy::anima_access);
        if matches!(
            proposed.anima_access,
            LocalAnimaAccess::Allow(access) if access > inherited_access
        ) {
            return Err(PolicyError::AnimaSelfElevation);
        }
    }

    Err(PolicyError::PolicyAdministrationRequiresUser)
}

pub fn client_created_descendant_policy(
    requested: Option<LocalFolderPolicy>,
) -> Result<LocalFolderPolicy, PolicyError> {
    if requested.is_some() {
        return Err(PolicyError::ClientPolicyChoice);
    }
    Ok(default_policy(FolderPolicyTemplate::ClientDescendant))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum PolicyError {
    #[error("folder policy ancestry does not resolve an owner")]
    MissingOwner,
    #[error("folder policy ancestry does not resolve ANIMA access")]
    MissingAccess,
    #[error("policy administration requires the user principal")]
    PolicyAdministrationRequiresUser,
    #[error("ANIMA cannot elevate itself within a user-owned ancestor")]
    AnimaSelfElevation,
    #[error("client-created descendants cannot choose owner or access policy")]
    ClientPolicyChoice,
}

#[cfg(test)]
mod tests {
    use crate::folders::{ClientId, FolderOwner};

    use super::{
        authorize, validate_policy_change, AdminOperation, AnimaAccess, ContentOperation,
        EffectiveFolderPolicy, LocalAnimaAccess, LocalFolderPolicy, Operation, PolicyError,
        Principal,
    };

    #[test]
    fn clients_fail_closed_until_the_device_local_grant_broker_exists() {
        let client = Principal::client(ClientId::parse("photo-importer").unwrap());

        assert!(!authorize(
            &client,
            AnimaAccess::Manage,
            Operation::Content(ContentOperation::Write),
        ));
    }

    #[test]
    fn only_broker_established_user_principals_can_administer_policy() {
        let operation = Operation::Admin(AdminOperation::Access);
        assert!(authorize(&Principal::user(), AnimaAccess::None, operation));
        assert!(!authorize(
            &Principal::anima(),
            AnimaAccess::Manage,
            operation
        ));

        let parent = EffectiveFolderPolicy::new(FolderOwner::Anima, AnimaAccess::Manage);
        let unchanged = LocalFolderPolicy::inherit()
            .with_anima_access(LocalAnimaAccess::Allow(AnimaAccess::Manage));
        assert!(validate_policy_change(&Principal::user(), &[parent], &unchanged).is_ok());
        assert_eq!(
            validate_policy_change(&Principal::anima(), &[parent], &unchanged),
            Err(PolicyError::PolicyAdministrationRequiresUser)
        );
    }

    #[test]
    fn anima_cannot_self_elevate_inside_user_owned_ancestry() {
        let parent = EffectiveFolderPolicy::new(FolderOwner::User, AnimaAccess::Read);
        let elevated = LocalFolderPolicy::inherit()
            .with_anima_access(LocalAnimaAccess::Allow(AnimaAccess::Manage));
        assert_eq!(
            validate_policy_change(&Principal::anima(), &[parent], &elevated),
            Err(PolicyError::AnimaSelfElevation)
        );
    }
}
