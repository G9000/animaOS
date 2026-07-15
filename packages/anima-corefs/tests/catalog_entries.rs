use anima_corefs::catalog::{
    catalog_generation_physical_name, decrypt_catalog_generation, encode_catalog_generation,
    encrypt_catalog_generation, inspect_catalog_generation_envelope,
    validate_catalog_generation_encoding, CatalogClientMetadata, CatalogEntry, CatalogEntryCommon,
    CatalogError, CatalogGeneration, CatalogGenerationEntry, CatalogObject, CatalogPayload,
    ContentHash, ObjectLifecycle, ObjectPhysicalName, TrashMetadata, WrappedObjectDekRecord,
    MAX_CATALOG_DEPTH, MAX_CATALOG_ENTRIES,
};
use anima_corefs::crypto::{
    derive_corefs_subkeys, ObjectKind, SecretBytes, OBJECT_KEY_ENVELOPE_VERSION,
    OBJECT_WRAP_ALGORITHM,
};
use anima_corefs::folders::{ClientId, FolderOwner, PortableName};
use anima_corefs::id::OpaqueId;
use anima_corefs::policy::AnimaAccess;
use serde_json::json;

const ROOT_ID: &str = "01J00000000000000000000000";
const OBJECT_ID: &str = "01J00000000000000000000001";
const TRASH_ID: &str = "01J00000000000000000000002";
const OTHER_ID: &str = "01J00000000000000000000003";

fn common(id: &str, parent_id: Option<&str>, name: &str) -> CatalogEntryCommon {
    CatalogEntryCommon::new(
        OpaqueId::parse(id).unwrap(),
        parent_id.map(|value| OpaqueId::parse(value).unwrap()),
        PortableName::parse(name).unwrap(),
        FolderOwner::User,
        AnimaAccess::Write,
    )
}

fn physical_name() -> ObjectPhysicalName {
    ObjectPhysicalName::parse("object-0123456789abcdef0123456789abcdef.acore").unwrap()
}

fn object() -> CatalogObject {
    CatalogObject::new(
        1,
        physical_name(),
        ContentHash::parse(&"ab".repeat(32)).unwrap(),
        ObjectKind::Note,
        WrappedObjectDekRecord::from_parts(
            1,
            1,
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[7; 12],
            vec![9; 48],
        )
        .unwrap(),
        ObjectLifecycle::Live,
    )
    .unwrap()
}

fn keys(byte: u8) -> anima_corefs::crypto::FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![byte; 32]).unwrap(), 1).unwrap()
}

#[test]
fn typed_empty_folder_roundtrips_as_authoritative_v2_catalog() {
    let entry_common = CatalogEntryCommon::new(
        OpaqueId::parse(ROOT_ID).unwrap(),
        None,
        PortableName::parse("Core").unwrap(),
        FolderOwner::Anima,
        AnimaAccess::Manage,
    );
    let catalog =
        CatalogGeneration::new(7, vec![CatalogGenerationEntry::folder(entry_common)]).unwrap();

    let encrypted = encrypt_catalog_generation(&keys(0x11), "core-a", &catalog).unwrap();

    assert_eq!(
        decrypt_catalog_generation(&keys(0x11), "core-a", &encrypted).unwrap(),
        catalog
    );
    assert_eq!(catalog.schema_version(), 2);
    assert!(catalog.entries()[0].is_folder());
}

#[test]
fn typed_object_entry_roundtrips_with_all_authoritative_state() {
    let client = ClientId::parse("journal.app").unwrap();
    let metadata = CatalogClientMetadata::new(
        &client,
        vec![("client:journal.app:view", json!({"z": 2, "a": 1}))],
    )
    .unwrap();
    let entry_common = CatalogEntryCommon::new(
        OpaqueId::parse(OBJECT_ID).unwrap(),
        Some(OpaqueId::parse(TRASH_ID).unwrap()),
        PortableName::parse("Entry.md").unwrap(),
        FolderOwner::User,
        AnimaAccess::Write,
    )
    .with_client_metadata(metadata);
    let wrapped = WrappedObjectDekRecord::from_parts(
        3,
        2,
        OBJECT_WRAP_ALGORITHM,
        OBJECT_KEY_ENVELOPE_VERSION,
        &[7; 12],
        vec![9; 48],
    )
    .unwrap();
    let lifecycle = ObjectLifecycle::Trashed(
        TrashMetadata::new(
            OpaqueId::parse(TRASH_ID).unwrap(),
            OpaqueId::parse(ROOT_ID).unwrap(),
            PortableName::parse("Entry.md").unwrap(),
            1_700_000_000_000,
        )
        .unwrap(),
    );
    let object = CatalogObject::new(
        4,
        physical_name(),
        ContentHash::parse(&"ab".repeat(32)).unwrap(),
        ObjectKind::Note,
        wrapped,
        lifecycle,
    )
    .unwrap();
    let catalog = CatalogGeneration::new(
        7,
        vec![
            CatalogGenerationEntry::object(entry_common, object),
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(TRASH_ID, Some(ROOT_ID), "Trash")),
        ],
    )
    .unwrap();

    let encrypted = encrypt_catalog_generation(&keys(0x12), "core-a", &catalog).unwrap();
    let decoded = decrypt_catalog_generation(&keys(0x12), "core-a", &encrypted).unwrap();

    assert_eq!(decoded, catalog);
    assert!(decoded.entries()[1].object_payload().is_some());
}

#[test]
fn typed_object_fields_reject_noncanonical_or_zero_values() {
    assert!(ContentHash::parse(&"AB".repeat(32)).is_err());
    assert!(ContentHash::parse("ab").is_err());
    assert!(WrappedObjectDekRecord::from_parts(
        0,
        1,
        OBJECT_WRAP_ALGORITHM,
        OBJECT_KEY_ENVELOPE_VERSION,
        &[7; 12],
        vec![9; 48],
    )
    .is_err());
    assert!(WrappedObjectDekRecord::from_parts(
        1,
        0,
        OBJECT_WRAP_ALGORITHM,
        OBJECT_KEY_ENVELOPE_VERSION,
        &[7; 12],
        vec![9; 48],
    )
    .is_err());
    assert!(WrappedObjectDekRecord::from_parts(
        1,
        1,
        "wrong",
        OBJECT_KEY_ENVELOPE_VERSION,
        &[7; 12],
        vec![9; 48],
    )
    .is_err());
    assert!(CatalogObject::new(
        0,
        physical_name(),
        ContentHash::parse(&"ab".repeat(32)).unwrap(),
        ObjectKind::Note,
        WrappedObjectDekRecord::from_parts(
            1,
            1,
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[7; 12],
            vec![9; 48],
        )
        .unwrap(),
        ObjectLifecycle::Live,
    )
    .is_err());
    assert!(CatalogObject::new(
        1,
        physical_name(),
        ContentHash::parse(&"ab".repeat(32)).unwrap(),
        ObjectKind::Folder,
        WrappedObjectDekRecord::from_parts(
            1,
            1,
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[7; 12],
            vec![9; 48],
        )
        .unwrap(),
        ObjectLifecycle::Live,
    )
    .is_err());
}

#[test]
fn object_physical_names_are_opaque_strict_and_unique_per_catalog() {
    assert!(ObjectPhysicalName::parse("Entry.md").is_err());
    assert!(ObjectPhysicalName::parse("object-ABCDEF0123456789abcdef0123456789.acore").is_err());
    assert!(ObjectPhysicalName::parse("../object-0123456789abcdef0123456789abcdef.acore").is_err());
    assert!(ObjectPhysicalName::parse("object-0123456789abcdef.acore").is_err());

    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::object(common(OBJECT_ID, Some(ROOT_ID), "One.md"), object(),),
            CatalogGenerationEntry::object(common(OTHER_ID, Some(ROOT_ID), "Two.md"), object(),),
        ],
    )
    .is_err());
}

#[test]
fn lifecycle_state_is_unambiguous_and_positive() {
    assert!(TrashMetadata::new(
        OpaqueId::parse(TRASH_ID).unwrap(),
        OpaqueId::parse(ROOT_ID).unwrap(),
        PortableName::parse("Entry.md").unwrap(),
        0,
    )
    .is_err());
    assert!(ObjectLifecycle::tombstone(OpaqueId::parse(TRASH_ID).unwrap(), 0).is_err());
    assert!(CatalogObject::new(
        1,
        physical_name(),
        ContentHash::parse(&"ab".repeat(32)).unwrap(),
        ObjectKind::Note,
        WrappedObjectDekRecord::from_parts(
            1,
            1,
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[7; 12],
            vec![9; 48],
        )
        .unwrap(),
        ObjectLifecycle::Tombstone {
            trash_folder_id: OpaqueId::parse(TRASH_ID).unwrap(),
            deleted_at_ms: 0,
        },
    )
    .is_err());
}

#[test]
fn catalog_rejects_entry_count_and_depth_before_expensive_graph_work() {
    let root = CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core"));
    let oversized = vec![root.clone(); MAX_CATALOG_ENTRIES + 1];
    assert!(matches!(
        CatalogGeneration::new(1, oversized),
        Err(CatalogError::LimitExceeded("catalog entries"))
    ));

    let mut entries = Vec::new();
    let mut parent = None;
    for depth in 0..=MAX_CATALOG_DEPTH + 1 {
        let id = format!("01J00000000000000000000{depth:03}");
        entries.push(CatalogGenerationEntry::folder(common(
            &id,
            parent.as_deref(),
            &format!("d{depth}"),
        )));
        parent = Some(id);
    }
    assert!(matches!(
        CatalogGeneration::new(1, entries),
        Err(CatalogError::LimitExceeded("catalog depth"))
    ));
}

#[test]
fn trash_and_tombstone_references_must_resolve_to_current_folders() {
    let trashed = CatalogObject::new(
        1,
        physical_name(),
        ContentHash::parse(&"ab".repeat(32)).unwrap(),
        ObjectKind::Note,
        WrappedObjectDekRecord::from_parts(
            1,
            1,
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[7; 12],
            vec![9; 48],
        )
        .unwrap(),
        ObjectLifecycle::Trashed(
            TrashMetadata::new(
                OpaqueId::parse(TRASH_ID).unwrap(),
                OpaqueId::parse(ROOT_ID).unwrap(),
                PortableName::parse("Entry.md").unwrap(),
                1,
            )
            .unwrap(),
        ),
    )
    .unwrap();
    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::object(common(OBJECT_ID, Some(ROOT_ID), "Entry.md"), trashed),
        ],
    )
    .is_err());

    let tombstone = CatalogObject::new(
        1,
        physical_name(),
        ContentHash::parse(&"ab".repeat(32)).unwrap(),
        ObjectKind::Note,
        WrappedObjectDekRecord::from_parts(
            1,
            1,
            OBJECT_WRAP_ALGORITHM,
            OBJECT_KEY_ENVELOPE_VERSION,
            &[7; 12],
            vec![9; 48],
        )
        .unwrap(),
        ObjectLifecycle::tombstone(OpaqueId::parse(TRASH_ID).unwrap(), 2).unwrap(),
    )
    .unwrap();
    assert!(
        CatalogGeneration::new(
            1,
            vec![
                CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
                CatalogGenerationEntry::folder(common(TRASH_ID, Some(ROOT_ID), "Trash")),
                CatalogGenerationEntry::object(
                    common(OBJECT_ID, Some(ROOT_ID), "Entry.md"),
                    tombstone,
                ),
            ],
        )
        .is_err()
    );
}

#[test]
fn catalog_wide_graph_role_and_name_invariants_fail_closed() {
    assert!(CatalogGeneration::new(
        0,
        vec![CatalogGenerationEntry::folder(common(
            ROOT_ID, None, "Core",
        ))]
    )
    .is_err());
    assert!(CatalogGeneration::new(
        1,
        vec![CatalogGenerationEntry::object(
            common(OBJECT_ID, None, "Object"),
            object(),
        )],
    )
    .is_err());
    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(ROOT_ID, Some(ROOT_ID), "Duplicate")),
        ],
    )
    .is_err());
    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(OBJECT_ID, Some(OTHER_ID), "Orphan")),
        ],
    )
    .is_err());
    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(OBJECT_ID, Some(OTHER_ID), "A")),
            CatalogGenerationEntry::folder(common(OTHER_ID, Some(OBJECT_ID), "B")),
        ],
    )
    .is_err());
    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::object(common(OBJECT_ID, Some(ROOT_ID), "Object"), object(),),
            CatalogGenerationEntry::folder(common(OTHER_ID, Some(OBJECT_ID), "Child")),
        ],
    )
    .is_err());

    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(OBJECT_ID, Some(ROOT_ID), "Notes")),
            CatalogGenerationEntry::folder(common(OTHER_ID, Some(ROOT_ID), "Notes")),
        ],
    )
    .is_err());
    assert!(CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(OBJECT_ID, Some(ROOT_ID), "Notes")),
            CatalogGenerationEntry::folder(common(OTHER_ID, Some(ROOT_ID), "notes")),
        ],
    )
    .is_ok());
}

#[test]
fn catalog_effective_policy_must_match_inheritance_and_deny_precedence() {
    let catalog = CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(OBJECT_ID, Some(ROOT_ID), "Child")),
            CatalogGenerationEntry::folder(common(OTHER_ID, Some(OBJECT_ID), "Grandchild")),
        ],
    )
    .unwrap();
    let encoded = encode_catalog_generation(&catalog).unwrap();
    let wire: serde_json::Value = serde_json::from_slice(&encoded).unwrap();

    let mut root_inherits = wire.clone();
    root_inherits["entries"][0]["policyOverride"] =
        json!({"owner": null, "animaAccess": "inherit"});
    assert!(
        validate_catalog_generation_encoding(&serde_json::to_vec(&root_inherits).unwrap()).is_err()
    );

    let mut inherited_escalation = wire.clone();
    inherited_escalation["entries"][1]["owner"] = json!("anima");
    inherited_escalation["entries"][1]["animaAccess"] = json!("manage");
    assert!(validate_catalog_generation_encoding(
        &serde_json::to_vec(&inherited_escalation).unwrap()
    )
    .is_err());

    let mut deny_then_allow = wire.clone();
    deny_then_allow["entries"][1]["animaAccess"] = json!("none");
    deny_then_allow["entries"][1]["policyOverride"] =
        json!({"owner": "user", "animaAccess": "deny"});
    deny_then_allow["entries"][2]["animaAccess"] = json!("manage");
    deny_then_allow["entries"][2]["policyOverride"] =
        json!({"owner": null, "animaAccess": "allow:manage"});
    assert!(
        validate_catalog_generation_encoding(&serde_json::to_vec(&deny_then_allow).unwrap())
            .is_err()
    );
}

#[test]
fn roles_are_folder_only_and_cutover_marker_is_catalog_wide() {
    let role_catalog = CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::folder(common(OBJECT_ID, Some(ROOT_ID), "Notes")),
            CatalogGenerationEntry::folder(common(OTHER_ID, Some(ROOT_ID), "Other")),
        ],
    )
    .unwrap();
    let mut duplicate_roles: serde_json::Value =
        serde_json::from_slice(&encode_catalog_generation(&role_catalog).unwrap()).unwrap();
    duplicate_roles["entries"][1]["role"] = json!("core.notes");
    duplicate_roles["entries"][2]["role"] = json!("core.notes");
    assert!(
        validate_catalog_generation_encoding(&serde_json::to_vec(&duplicate_roles).unwrap())
            .is_err()
    );

    let object_catalog = CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
            CatalogGenerationEntry::object(common(OBJECT_ID, Some(ROOT_ID), "Note.md"), object()),
        ],
    )
    .unwrap();
    let mut object_role: serde_json::Value =
        serde_json::from_slice(&encode_catalog_generation(&object_catalog).unwrap()).unwrap();
    object_role["entries"][1]["role"] = json!("core.notes");
    assert!(
        validate_catalog_generation_encoding(&serde_json::to_vec(&object_role).unwrap()).is_err()
    );
}

#[test]
fn v2_decode_rejects_unknown_noncanonical_and_wrong_schema_json() {
    let catalog = CatalogGeneration::new(
        1,
        vec![CatalogGenerationEntry::folder(common(
            ROOT_ID, None, "Core",
        ))],
    )
    .unwrap();
    let encoded = encode_catalog_generation(&catalog).unwrap();
    let mut trailing = encoded.clone();
    trailing.push(b' ');
    assert!(validate_catalog_generation_encoding(&trailing).is_err());

    let mut value: serde_json::Value = serde_json::from_slice(&encoded).unwrap();
    value["unknown"] = json!(true);
    assert!(validate_catalog_generation_encoding(&serde_json::to_vec(&value).unwrap()).is_err());
    value.as_object_mut().unwrap().remove("unknown");
    value["schemaVersion"] = json!(3);
    assert!(validate_catalog_generation_encoding(&serde_json::to_vec(&value).unwrap()).is_err());
}

#[test]
fn client_metadata_is_namespaced_deterministic_and_catalog_bounded() {
    let client = ClientId::parse("journal.app").unwrap();
    assert!(
        CatalogClientMetadata::new(&client, vec![("client:other.app:view", json!(true))],).is_err()
    );
    let first = CatalogClientMetadata::new(
        &client,
        vec![
            ("client:journal.app:z", json!({"z": 2, "a": 1})),
            ("client:journal.app:a", json!(1)),
        ],
    )
    .unwrap();
    let second = CatalogClientMetadata::new(
        &client,
        vec![
            ("client:journal.app:a", json!(1)),
            ("client:journal.app:z", json!({"a": 1, "z": 2})),
        ],
    )
    .unwrap();
    let first = CatalogGeneration::new(
        1,
        vec![CatalogGenerationEntry::folder(
            common(ROOT_ID, None, "Core").with_client_metadata(first),
        )],
    )
    .unwrap();
    let second = CatalogGeneration::new(
        1,
        vec![CatalogGenerationEntry::folder(
            common(ROOT_ID, None, "Core").with_client_metadata(second),
        )],
    )
    .unwrap();
    assert_eq!(
        encode_catalog_generation(&first).unwrap(),
        encode_catalog_generation(&second).unwrap()
    );

    let oversized = CatalogClientMetadata::new(
        &client,
        vec![(
            "client:journal.app:huge",
            json!("x".repeat(anima_corefs::catalog::MAX_CATALOG_PLAINTEXT_SIZE + 1)),
        )],
    )
    .unwrap();
    let oversized = CatalogGeneration::new(
        1,
        vec![CatalogGenerationEntry::folder(
            common(ROOT_ID, None, "Core").with_client_metadata(oversized),
        )],
    )
    .unwrap();
    assert!(encode_catalog_generation(&oversized).is_err());

    let plain = CatalogGeneration::new(
        1,
        vec![CatalogGenerationEntry::folder(common(
            ROOT_ID, None, "Core",
        ))],
    )
    .unwrap();
    let mut mixed: serde_json::Value =
        serde_json::from_slice(&encode_catalog_generation(&plain).unwrap()).unwrap();
    mixed["entries"][0]["clientMetadata"] = json!({
        "client:journal.app:view": true,
        "client:other.app:view": true
    });
    assert!(matches!(
        validate_catalog_generation_encoding(&serde_json::to_vec(&mixed).unwrap()),
        Err(CatalogError::InvalidFormat(
            "mixed client metadata namespaces"
        ))
    ));
}

#[test]
fn authoritative_v2_envelope_roundtrips_and_exposes_only_safe_header_state() {
    let catalog = CatalogGeneration::new(
        9,
        vec![CatalogGenerationEntry::folder(common(
            ROOT_ID, None, "Core",
        ))],
    )
    .unwrap();
    let encrypted = encrypt_catalog_generation(&keys(0x22), "01JCORE", &catalog).unwrap();
    let info = inspect_catalog_generation_envelope(&encrypted).unwrap();

    assert_eq!(info.schema_version(), 2);
    assert_eq!(info.generation(), 9);
    assert_eq!(
        decrypt_catalog_generation(&keys(0x22), "01JCORE", &encrypted).unwrap(),
        catalog
    );
    let name = catalog_generation_physical_name(&encrypted).unwrap();
    assert!(name.starts_with("catalog-00000000000000000009-"));
    assert!(name.ends_with(".acore"));
    assert!(!encrypted
        .windows("Core".len())
        .any(|window| window == b"Core"));
}

#[test]
fn authoritative_v2_envelope_rejects_wrong_context_tamper_and_bad_lengths() {
    let catalog = CatalogGeneration::new(
        9,
        vec![CatalogGenerationEntry::folder(common(
            ROOT_ID, None, "Core",
        ))],
    )
    .unwrap();
    let encrypted = encrypt_catalog_generation(&keys(0x22), "01JCORE", &catalog).unwrap();
    assert!(decrypt_catalog_generation(&keys(0x33), "01JCORE", &encrypted).is_err());
    assert!(decrypt_catalog_generation(&keys(0x22), "OTHER", &encrypted).is_err());

    let mut generation = encrypted.clone();
    generation[10] ^= 1;
    assert!(decrypt_catalog_generation(&keys(0x22), "01JCORE", &generation).is_err());
    let mut ciphertext = encrypted.clone();
    let last = ciphertext.len() - 1;
    ciphertext[last] ^= 1;
    assert!(decrypt_catalog_generation(&keys(0x22), "01JCORE", &ciphertext).is_err());
    assert!(inspect_catalog_generation_envelope(&encrypted[..33]).is_err());
    assert!(inspect_catalog_generation_envelope(&encrypted[..encrypted.len() - 1]).is_err());
    let mut trailing = encrypted.clone();
    trailing.push(0);
    assert!(inspect_catalog_generation_envelope(&trailing).is_err());
}

#[test]
fn legacy_v1_catalog_envelopes_cannot_become_authoritative() {
    let legacy = CatalogPayload::new(
        9,
        vec![CatalogEntry::new(ROOT_ID, json!({"legacy": true})).unwrap()],
    )
    .unwrap();
    let encrypted =
        anima_corefs::catalog::encrypt_catalog(&keys(0x22), "01JCORE", &legacy).unwrap();

    assert!(inspect_catalog_generation_envelope(&encrypted).is_err());
    assert!(decrypt_catalog_generation(&keys(0x22), "01JCORE", &encrypted).is_err());
}
