use anima_corefs::catalog::{
    catalog_physical_name, decode_catalog, decrypt_catalog, encode_catalog, encrypt_catalog,
    CatalogEntry, CatalogError, CatalogPayload, CATALOG_FORMAT_VERSION, MAX_CATALOG_PLAINTEXT_SIZE,
};
use anima_corefs::crypto::{derive_corefs_subkeys, SecretBytes};
use serde_json::json;

const STABLE_A: &str = "01J00000000000000000000000";
const STABLE_Z: &str = "01J00000000000000000000001";
const STABLE_OTHER: &str = "01J00000000000000000000002";

fn keys(byte: u8) -> anima_corefs::crypto::FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![byte; 32]).unwrap(), 1).unwrap()
}

fn entry(stable_id: &str, record: serde_json::Value) -> CatalogEntry {
    CatalogEntry::new(stable_id, record).unwrap()
}

fn payload() -> CatalogPayload {
    CatalogPayload::new(
        9,
        vec![
            entry(STABLE_Z, json!({"z": 1, "a": {"y": 2, "x": 1}})),
            entry(STABLE_A, json!({"logicalName": "private/diary/entry.md"})),
        ],
    )
    .unwrap()
}

#[test]
fn canonical_encoding_is_independent_of_entry_and_map_insertion_order() {
    let first = payload();
    let second = CatalogPayload::new(
        9,
        vec![
            entry(STABLE_A, json!({"logicalName": "private/diary/entry.md"})),
            entry(STABLE_Z, json!({"a": {"x": 1, "y": 2}, "z": 1})),
        ],
    )
    .unwrap();
    assert_eq!(
        encode_catalog(&first).unwrap(),
        encode_catalog(&second).unwrap()
    );
    assert_eq!(
        decode_catalog(&encode_catalog(&first).unwrap()).unwrap(),
        first
    );
}

#[test]
fn encode_catalog_canonicalizes_unsorted_public_payloads() {
    let unsorted = CatalogPayload {
        schema_version: CATALOG_FORMAT_VERSION,
        generation: 9,
        entries: vec![
            entry(STABLE_Z, json!({"z": 1})),
            entry(STABLE_A, json!({"a": 1})),
        ],
    };
    let sorted = CatalogPayload::new(
        9,
        vec![
            entry(STABLE_A, json!({"a": 1})),
            entry(STABLE_Z, json!({"z": 1})),
        ],
    )
    .unwrap();

    assert_eq!(
        encode_catalog(&unsorted).unwrap(),
        encode_catalog(&sorted).unwrap()
    );
}

#[test]
fn catalog_encryption_roundtrip_and_physical_name_are_private() {
    let payload = payload();
    let encrypted = encrypt_catalog(&keys(0x22), "01JCORE", &payload).unwrap();
    let decoded = decrypt_catalog(&keys(0x22), "01JCORE", &encrypted).unwrap();
    assert_eq!(decoded, payload);
    assert!(!encrypted
        .windows(b"private/diary/entry.md".len())
        .any(|window| window == b"private/diary/entry.md"));
    let name = catalog_physical_name(payload.generation, &encrypted).unwrap();
    assert!(name.ends_with(".acore"));
    assert!(name.contains("00000000000000000009"));
    assert!(!name.contains("diary"));
    assert!(!name.contains("entry"));
    assert!(catalog_physical_name(payload.generation + 1, &encrypted).is_err());
}

#[test]
fn wrong_generation_core_key_and_tampering_fail() {
    let payload = payload();
    let encrypted = encrypt_catalog(&keys(0x22), "01JCORE", &payload).unwrap();
    assert!(decrypt_catalog(&keys(0x33), "01JCORE", &encrypted).is_err());
    assert!(decrypt_catalog(&keys(0x22), "OTHER", &encrypted).is_err());

    let mut generation = encrypted.clone();
    generation[10] ^= 1;
    assert!(decrypt_catalog(&keys(0x22), "01JCORE", &generation).is_err());

    let mut tampered = encrypted;
    let last = tampered.len() - 1;
    tampered[last] ^= 1;
    assert!(decrypt_catalog(&keys(0x22), "01JCORE", &tampered).is_err());

    let encrypted = encrypt_catalog(&keys(0x22), "01JCORE", &payload).unwrap();
    assert!(decrypt_catalog(&keys(0x22), "01JCORE", &encrypted[..encrypted.len() - 1]).is_err());
    let mut trailing = encrypted;
    trailing.push(0);
    assert!(decrypt_catalog(&keys(0x22), "01JCORE", &trailing).is_err());
}

#[test]
fn physical_name_rejects_invalid_catalog_envelope_lengths() {
    let encrypted = encrypt_catalog(&keys(0x22), "01JCORE", &payload()).unwrap();

    assert!(matches!(
        catalog_physical_name(9, &encrypted[..33]),
        Err(CatalogError::InvalidFormat("truncated header"))
    ));

    let mut trailing = encrypted.clone();
    trailing.push(0);
    assert!(matches!(
        catalog_physical_name(9, &trailing),
        Err(CatalogError::InvalidFormat("catalog ciphertext length"))
    ));

    let mut oversized_declaration = encrypted;
    oversized_declaration[30..34]
        .copy_from_slice(&((MAX_CATALOG_PLAINTEXT_SIZE as u32) + 17).to_le_bytes());
    assert!(matches!(
        catalog_physical_name(9, &oversized_declaration),
        Err(CatalogError::LimitExceeded("catalog ciphertext"))
    ));
}

#[test]
fn duplicate_ids_and_versions_are_rejected() {
    assert!(matches!(
        CatalogPayload::new(
            1,
            vec![
                CatalogEntry::new(STABLE_OTHER, json!(1)).unwrap(),
                CatalogEntry::new(STABLE_OTHER, json!(2)).unwrap(),
            ],
        ),
        Err(CatalogError::DuplicateStableId(_))
    ));
    assert!(CatalogPayload::new(0, vec![]).is_err());

    let mut unsupported_payload = serde_json::to_value(payload()).unwrap();
    unsupported_payload["schemaVersion"] = json!(CATALOG_FORMAT_VERSION + 1);
    let unsupported_payload = serde_json::to_vec(&unsupported_payload).unwrap();
    assert!(matches!(
        decode_catalog(&unsupported_payload),
        Err(CatalogError::UnsupportedVersion(2))
    ));

    let mut unsupported_envelope = encrypt_catalog(&keys(0x22), "01JCORE", &payload()).unwrap();
    unsupported_envelope[8..10].copy_from_slice(&(CATALOG_FORMAT_VERSION + 1).to_le_bytes());
    assert!(matches!(
        decrypt_catalog(&keys(0x22), "01JCORE", &unsupported_envelope),
        Err(CatalogError::UnsupportedVersion(2))
    ));
}

#[test]
fn oversized_native_catalog_is_rejected_before_canonical_copy() {
    let huge = "x".repeat(MAX_CATALOG_PLAINTEXT_SIZE + 1);
    let oversized = CatalogPayload::new(1, vec![entry(STABLE_OTHER, json!(huge))]).unwrap();
    assert!(matches!(
        encode_catalog(&oversized),
        Err(CatalogError::LimitExceeded(_))
    ));

    let encrypted = encrypt_catalog(&keys(0x22), "01JCORE", &payload()).unwrap();
    let mut oversized_declaration = encrypted;
    oversized_declaration[30..34]
        .copy_from_slice(&((MAX_CATALOG_PLAINTEXT_SIZE as u32) + 17).to_le_bytes());
    assert!(matches!(
        decrypt_catalog(&keys(0x22), "01JCORE", &oversized_declaration),
        Err(CatalogError::LimitExceeded(_))
    ));
}
