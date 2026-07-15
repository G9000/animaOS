use anima_corefs::folders::FolderOwner;
use anima_corefs::policy::{
    client_created_descendant_policy, default_policy, resolve_policy, AdminOperation, AnimaAccess,
    ContentOperation, EffectiveFolderPolicy, FolderPolicyTemplate, LocalAnimaAccess,
    LocalFolderPolicy, Operation, PolicyError, ReadOperation, StructuralOperation,
};

#[test]
fn anima_access_is_closed_and_ordered() {
    assert!(AnimaAccess::None < AnimaAccess::Read);
    assert!(AnimaAccess::Read < AnimaAccess::Write);
    assert!(AnimaAccess::Write < AnimaAccess::Manage);
}

#[test]
fn access_levels_authorize_only_their_operation_groups() {
    let read = Operation::Read(ReadOperation::Read);
    let content = [
        ContentOperation::Write,
        ContentOperation::Create,
        ContentOperation::Mkdir,
        ContentOperation::Patch,
    ];
    let structural = [
        StructuralOperation::Rename,
        StructuralOperation::Move,
        StructuralOperation::Trash,
        StructuralOperation::Restore,
    ];

    assert!(!AnimaAccess::None.allows(read));
    assert!(AnimaAccess::Read.allows(read));
    assert!(!AnimaAccess::Read.allows(Operation::Content(content[0])));
    for operation in content {
        assert!(AnimaAccess::Write.allows(Operation::Content(operation)));
        assert!(AnimaAccess::Manage.allows(Operation::Content(operation)));
    }
    for operation in structural {
        assert!(!AnimaAccess::Write.allows(Operation::Structural(operation)));
        assert!(AnimaAccess::Manage.allows(Operation::Structural(operation)));
    }
}

#[test]
fn admin_operations_are_never_implied_by_anima_manage_access() {
    let admin = [
        AdminOperation::Owner,
        AdminOperation::Access,
        AdminOperation::Deny,
        AdminOperation::ReservedRoles,
        AdminOperation::Grants,
        AdminOperation::Purge,
        AdminOperation::KeyRetirement,
    ];

    for operation in admin {
        let operation = Operation::Admin(operation);
        assert!(!AnimaAccess::Manage.allows(operation));
    }
}

#[test]
fn policy_defaults_match_the_portable_folder_contract() {
    let cases = [
        (
            FolderPolicyTemplate::AnimaRoot,
            Some(FolderOwner::Anima),
            LocalAnimaAccess::Allow(AnimaAccess::Manage),
        ),
        (
            FolderPolicyTemplate::UserCreated,
            Some(FolderOwner::User),
            LocalAnimaAccess::Allow(AnimaAccess::Write),
        ),
        (
            FolderPolicyTemplate::Imported,
            Some(FolderOwner::User),
            LocalAnimaAccess::Allow(AnimaAccess::None),
        ),
        (
            FolderPolicyTemplate::ClientDescendant,
            None,
            LocalAnimaAccess::Inherit,
        ),
        (
            FolderPolicyTemplate::Journal,
            Some(FolderOwner::User),
            LocalAnimaAccess::Allow(AnimaAccess::Write),
        ),
        (
            FolderPolicyTemplate::Notes,
            Some(FolderOwner::User),
            LocalAnimaAccess::Allow(AnimaAccess::Write),
        ),
        (
            FolderPolicyTemplate::Conversations,
            Some(FolderOwner::Shared),
            LocalAnimaAccess::Allow(AnimaAccess::Manage),
        ),
        (
            FolderPolicyTemplate::Reflections,
            Some(FolderOwner::Anima),
            LocalAnimaAccess::Allow(AnimaAccess::Manage),
        ),
    ];

    for (template, owner, anima_access) in cases {
        let policy = default_policy(template);
        assert_eq!(policy.owner(), owner);
        assert_eq!(policy.anima_access(), anima_access);
    }
}

#[test]
fn local_allow_inherits_owner_and_overrides_inherited_access() {
    let parent = default_policy(FolderPolicyTemplate::UserCreated);
    let child =
        LocalFolderPolicy::inherit().with_anima_access(LocalAnimaAccess::Allow(AnimaAccess::Read));

    assert_eq!(
        resolve_policy(&[parent, child]).unwrap(),
        EffectiveFolderPolicy::new(FolderOwner::User, AnimaAccess::Read)
    );
}

#[test]
fn explicit_deny_wins_over_descendant_allow() {
    let root = default_policy(FolderPolicyTemplate::UserCreated);
    let denied = LocalFolderPolicy::inherit().with_anima_access(LocalAnimaAccess::Deny);
    let attempted_override = LocalFolderPolicy::inherit()
        .with_anima_access(LocalAnimaAccess::Allow(AnimaAccess::Manage));

    assert_eq!(
        resolve_policy(&[root, denied, attempted_override]).unwrap(),
        EffectiveFolderPolicy::new(FolderOwner::User, AnimaAccess::None)
    );
}

#[test]
fn a_root_policy_must_resolve_owner_and_access() {
    assert_eq!(
        resolve_policy(&[LocalFolderPolicy::inherit()]),
        Err(PolicyError::MissingOwner)
    );
    assert_eq!(
        resolve_policy(&[LocalFolderPolicy::with_owner(FolderOwner::User)]),
        Err(PolicyError::MissingAccess)
    );
}

#[test]
fn client_created_descendants_inherit_and_cannot_choose_policy() {
    let parent = default_policy(FolderPolicyTemplate::Conversations);
    let inherited = client_created_descendant_policy(None).unwrap();

    assert_eq!(inherited.owner(), None);
    assert_eq!(inherited.anima_access(), LocalAnimaAccess::Inherit);
    assert_eq!(
        resolve_policy(&[parent, inherited]).unwrap(),
        EffectiveFolderPolicy::new(FolderOwner::Shared, AnimaAccess::Manage)
    );

    let requested = default_policy(FolderPolicyTemplate::UserCreated);
    assert_eq!(
        client_created_descendant_policy(Some(requested)),
        Err(PolicyError::ClientPolicyChoice)
    );
}
