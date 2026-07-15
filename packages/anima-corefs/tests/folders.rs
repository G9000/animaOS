use anima_corefs::folders::{
    validate_folder_tree, ClientId, FolderEntry, FolderError, FolderOwner, FolderRole, PortableName,
};
use anima_corefs::id::OpaqueId;
use serde_json::json;

const ROOT_ID: &str = "01J00000000000000000000000";
const CHILD_ID: &str = "01J00000000000000000000001";
const OTHER_ID: &str = "01J00000000000000000000002";

fn folder(id: &str, parent_id: Option<&str>, name: &str) -> FolderEntry {
    FolderEntry::new(
        OpaqueId::parse(id).unwrap(),
        parent_id.map(|value| OpaqueId::parse(value).unwrap()),
        PortableName::parse(name).unwrap(),
        FolderOwner::User,
    )
}

#[test]
fn portable_names_are_nfc_and_reject_unsafe_components() {
    assert_eq!(
        PortableName::parse("Caf\u{e9}").unwrap().as_str(),
        "Caf\u{e9}"
    );

    for invalid in [
        "",
        ".",
        "..",
        "folder/name",
        "folder\\name",
        "nul\0name",
        "line\nfeed",
        "Cafe\u{301}",
    ] {
        assert!(
            PortableName::parse(invalid).is_err(),
            "accepted unsafe portable name {invalid:?}"
        );
    }
}

#[test]
fn portable_names_have_a_bounded_encoded_length() {
    assert!(PortableName::parse(&"a".repeat(255)).is_ok());
    assert!(PortableName::parse(&"a".repeat(256)).is_err());
}

#[test]
fn an_empty_folder_is_a_first_class_entry() {
    let folder = FolderEntry::new(
        OpaqueId::parse(ROOT_ID).unwrap(),
        None,
        PortableName::parse("Empty").unwrap(),
        FolderOwner::User,
    );

    assert_eq!(folder.id().as_str(), ROOT_ID);
    assert_eq!(folder.parent_id(), None);
    assert_eq!(folder.name().as_str(), "Empty");
    assert_eq!(folder.owner(), FolderOwner::User);
    assert_eq!(folder.role(), None);
    assert!(folder.client_metadata().is_empty());
}

#[test]
fn folder_owner_is_a_closed_user_anima_shared_contract() {
    assert_ne!(FolderOwner::User, FolderOwner::Anima);
    assert_ne!(FolderOwner::Anima, FolderOwner::Shared);
    assert_ne!(FolderOwner::Shared, FolderOwner::User);
}

#[test]
fn a_folder_tree_has_exactly_one_root() {
    assert!(validate_folder_tree(&[folder(ROOT_ID, None, "Root")], &[]).is_ok());
    assert!(validate_folder_tree(&[], &[]).is_err());
    assert!(validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, None, "Another root"),
        ],
        &[],
    )
    .is_err());
}

#[test]
fn a_non_root_folder_parent_must_exist() {
    assert!(validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, Some(ROOT_ID), "Child"),
        ],
        &[],
    )
    .is_ok());
    assert!(validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, Some(OTHER_ID), "Orphan"),
        ],
        &[],
    )
    .is_err());
}

#[test]
fn a_non_root_parent_must_be_a_folder() {
    let error = validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, Some(OTHER_ID), "Child"),
        ],
        &[OpaqueId::parse(OTHER_ID).unwrap()],
    )
    .unwrap_err();

    assert_eq!(error, FolderError::ParentNotFolder(OTHER_ID.to_owned()));
}

#[test]
fn a_folder_cannot_parent_itself() {
    assert!(validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, Some(CHILD_ID), "Self"),
        ],
        &[],
    )
    .is_err());
}

#[test]
fn folder_parent_cycles_are_rejected() {
    assert!(validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, Some(OTHER_ID), "First"),
            folder(OTHER_ID, Some(CHILD_ID), "Second"),
        ],
        &[],
    )
    .is_err());
}

#[test]
fn sibling_folder_names_are_case_sensitive_but_exactly_unique() {
    assert!(validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, Some(ROOT_ID), "Notes"),
            folder(OTHER_ID, Some(ROOT_ID), "notes"),
        ],
        &[],
    )
    .is_ok());
    assert!(validate_folder_tree(
        &[
            folder(ROOT_ID, None, "Root"),
            folder(CHILD_ID, Some(ROOT_ID), "Notes"),
            folder(OTHER_ID, Some(ROOT_ID), "Notes"),
        ],
        &[],
    )
    .is_err());
}

#[test]
fn client_ids_have_one_canonical_spelling() {
    assert_eq!(
        ClientId::parse("photo-importer").unwrap().as_str(),
        "photo-importer"
    );
    for invalid in ["", "Photo-Importer", "-client", "client-", "a:b"] {
        assert!(ClientId::parse(invalid).is_err(), "accepted {invalid:?}");
    }
}

#[test]
fn malformed_role_namespaces_are_rejected() {
    for invalid in ["", "notes", "core.", "core:notes", "core.notes:extra"] {
        assert!(
            FolderRole::parse_existing(invalid).is_err(),
            "accepted {invalid:?}"
        );
    }

    for invalid in [
        "client:photo-importer:",
        "client::gallery",
        "client:Photo-Importer:gallery",
        "client:photo-importer:gallery:extra",
        "client:photo-importer:line\nbreak",
    ] {
        assert!(
            FolderRole::parse_existing(invalid).is_err(),
            "accepted {invalid:?}"
        );
    }

    assert!(FolderRole::parse_existing("core.notes").is_ok());
    assert!(FolderRole::parse_existing("client:photo-importer:gallery").is_ok());
}

#[test]
fn client_metadata_has_deterministic_key_order() {
    let client = ClientId::parse("photo-importer").unwrap();
    let folder = folder(ROOT_ID, None, "Root")
        .with_client_metadata(
            &client,
            [
                ("client:photo-importer:z-last", json!(2)),
                ("client:photo-importer:a-first", json!({"value": 1})),
            ],
        )
        .unwrap();

    assert_eq!(
        folder
            .client_metadata()
            .keys()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        vec![
            "client:photo-importer:a-first",
            "client:photo-importer:z-last"
        ]
    );
}

#[test]
fn a_client_cannot_write_another_clients_metadata_namespace() {
    let writer = ClientId::parse("photo-importer").unwrap();
    assert!(folder(ROOT_ID, None, "Root")
        .with_client_metadata(&writer, [("client:other-client:key", json!("not allowed"))],)
        .is_err());
}

#[test]
fn malformed_client_metadata_namespaces_are_rejected() {
    let writer = ClientId::parse("photo-importer").unwrap();
    for invalid in [
        "core:photo-importer:key",
        "client::key",
        "client:photo-importer:",
        "client:photo-importer:key:extra",
        "client:Photo-Importer:key",
    ] {
        assert!(
            folder(ROOT_ID, None, "Root")
                .with_client_metadata(&writer, [(invalid, json!(1))])
                .is_err(),
            "accepted {invalid:?}"
        );
    }
}
