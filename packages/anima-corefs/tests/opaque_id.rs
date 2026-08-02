use std::collections::BTreeMap;

use anima_corefs::catalog::{decode_catalog, CatalogEntry, CatalogError, CatalogPayload};
use anima_corefs::crypto::{ObjectBaseAad, ObjectKind};
use anima_corefs::envelope::{BodyEncoding, EnvelopeMetadata};
use anima_corefs::id::OpaqueId;
use serde_json::json;

const VALID_ID: &str = "01J00000000000000000000000";

#[test]
fn canonical_ulid_opaque_ids_roundtrip_and_reject_display_or_path_values() {
    assert_eq!(OpaqueId::parse(VALID_ID).unwrap().as_str(), VALID_ID);

    for invalid in [
        "01j00000000000000000000000",
        "81J00000000000000000000000",
        "01J0000000000000000000000I",
        "01J0000000000000000000000L",
        "01J0000000000000000000000O",
        "01J0000000000000000000000U",
        "../private/diary.md",
        "alice@example.com",
        "My private note title",
        "01J0000000000000000000000\0",
        "01J000000000000000000000",
        "01J000000000000000000000000",
        "日记000000000000000000000000",
    ] {
        assert!(OpaqueId::parse(invalid).is_err(), "accepted {invalid:?}");
        assert!(ObjectBaseAad::new("01JCORE", invalid, ObjectKind::Note, 1, 1, 1).is_err());
        assert!(CatalogEntry::new(invalid, json!({})).is_err());
    }
}

#[test]
fn metadata_and_catalog_decode_reject_noncanonical_stable_ids() {
    assert!(EnvelopeMetadata::for_body(
        "note",
        "private/diary.md",
        1,
        "2026-07-15T00:00:00Z",
        "2026-07-15T00:00:01Z",
        "text/plain",
        BTreeMap::new(),
        BodyEncoding::Utf8,
        b"body",
    )
    .is_err());

    let payload = CatalogPayload::new(
        1,
        vec![CatalogEntry::new(VALID_ID, json!({"displayName": "private/diary.md"})).unwrap()],
    )
    .unwrap();
    assert_eq!(payload.entries[0].stable_id, VALID_ID);

    let invalid = br#"{"schemaVersion":1,"generation":1,"entries":[{"stableId":"private/diary.md","record":{}}]}"#;
    assert!(matches!(
        decode_catalog(invalid),
        Err(CatalogError::InvalidFormat("stable ID"))
    ));
}

#[test]
fn migration_ids_are_deterministic_native_and_domain_separated() {
    let first = OpaqueId::derive_migration("diary-entry", b"42").unwrap();
    let repeated = OpaqueId::derive_migration("diary-entry", b"42").unwrap();
    let attachment = OpaqueId::derive_migration("diary-attachment", b"42").unwrap();

    assert_eq!(first, repeated);
    assert_ne!(first, attachment);
    assert_eq!(first.as_str().len(), 26);
    assert_eq!(OpaqueId::parse(first.as_str()).unwrap(), first);
    assert!(OpaqueId::derive_migration("", b"42").is_err());
    assert!(OpaqueId::derive_migration("diary-entry", b"").is_err());
}
