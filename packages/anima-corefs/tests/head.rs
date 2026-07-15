use anima_corefs::catalog::{
    encrypt_catalog, encrypt_catalog_generation, CatalogEntry, CatalogEntryCommon,
    CatalogGeneration, CatalogGenerationEntry, CatalogPayload,
};
use anima_corefs::crypto::{derive_corefs_subkeys, SecretBytes};
use anima_corefs::folders::{FolderOwner, PortableName};
use anima_corefs::head::{decode_head, encode_head, HeadRecord};
use anima_corefs::id::OpaqueId;
use anima_corefs::policy::AnimaAccess;
use serde_json::json;

const ROOT_ID: &str = "01J00000000000000000000000";

fn keys() -> anima_corefs::crypto::FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![0x22; 32]).unwrap(), 3).unwrap()
}

fn encrypted_catalog(generation: u64) -> Vec<u8> {
    let catalog = CatalogGeneration::new(
        generation,
        vec![CatalogGenerationEntry::folder(CatalogEntryCommon::new(
            OpaqueId::parse(ROOT_ID).unwrap(),
            None,
            PortableName::parse("Core").unwrap(),
            FolderOwner::Anima,
            AnimaAccess::Manage,
        ))],
    )
    .unwrap();
    encrypt_catalog_generation(&keys(), "01JCORE", &catalog).unwrap()
}

#[test]
fn head_canonically_roundtrips_and_verifies_exact_catalog_bytes() {
    let catalog = encrypted_catalog(7);
    let head = HeadRecord::new_for_catalog(&keys(), "01JCORE", &catalog, 3).unwrap();
    let encoded = encode_head(&head).unwrap();
    let decoded = decode_head(&encoded).unwrap();

    assert_eq!(decoded, head);
    assert_eq!(decoded.schema_version(), 1);
    assert_eq!(decoded.envelope_version(), 2);
    assert_eq!(decoded.generation(), 7);
    assert_eq!(decoded.required_frk_version(), 3);
    assert_eq!(decoded.catalog_hash().len(), 64);
    decoded
        .verify_catalog(&keys(), "01JCORE", &catalog)
        .unwrap();
}

#[test]
fn head_rejects_wrong_catalog_hash_generation_and_non_v2_catalogs() {
    let catalog = encrypted_catalog(7);
    let head = HeadRecord::new_for_catalog(&keys(), "01JCORE", &catalog, 3).unwrap();
    let mut other = encrypted_catalog(7);
    let last = other.len() - 1;
    other[last] ^= 1;
    assert!(head.verify_catalog(&keys(), "01JCORE", &other).is_err());

    let mismatched = format!(
        "{{\"schemaVersion\":1,\"envelopeVersion\":2,\"generation\":8,\"catalogHash\":\"{}\",\"requiredFrkVersion\":3}}",
        head.catalog_hash()
    );
    let mismatched = decode_head(mismatched.as_bytes()).unwrap();
    assert!(mismatched
        .verify_catalog(&keys(), "01JCORE", &catalog)
        .is_err());

    let legacy = CatalogPayload::new(
        7,
        vec![CatalogEntry::new(ROOT_ID, json!({"legacy": true})).unwrap()],
    )
    .unwrap();
    let legacy = encrypt_catalog(&keys(), "01JCORE", &legacy).unwrap();
    assert!(HeadRecord::new_for_catalog(&keys(), "01JCORE", &legacy, 3).is_err());
}

#[test]
fn head_cannot_bless_an_unauthenticated_v2_shaped_envelope() {
    let mut fake = Vec::new();
    fake.extend_from_slice(b"ACATV2\0\0");
    fake.extend_from_slice(&2_u16.to_le_bytes());
    fake.extend_from_slice(&7_u64.to_le_bytes());
    fake.extend_from_slice(&[0_u8; 12]);
    fake.extend_from_slice(&16_u32.to_le_bytes());
    fake.extend_from_slice(&[0_u8; 16]);

    assert!(HeadRecord::new_for_catalog(&keys(), "01JCORE", &fake, 3).is_err());
}

#[test]
fn head_decode_rejects_zero_versions_unknown_fields_and_noncanonical_bytes() {
    let head = HeadRecord::new_for_catalog(&keys(), "01JCORE", &encrypted_catalog(7), 3).unwrap();
    let encoded = encode_head(&head).unwrap();
    let mut trailing = encoded.clone();
    trailing.push(b' ');
    assert!(decode_head(&trailing).is_err());

    for (field, value) in [
        ("schemaVersion", json!(2)),
        ("envelopeVersion", json!(1)),
        ("generation", json!(0)),
        ("requiredFrkVersion", json!(0)),
        ("catalogHash", json!("AB".repeat(32))),
    ] {
        let mut document: serde_json::Value = serde_json::from_slice(&encoded).unwrap();
        document[field] = value;
        assert!(
            decode_head(&serde_json::to_vec(&document).unwrap()).is_err(),
            "accepted invalid {field}"
        );
    }

    let mut document: serde_json::Value = serde_json::from_slice(&encoded).unwrap();
    document["unknown"] = json!(true);
    assert!(decode_head(&serde_json::to_vec(&document).unwrap()).is_err());
}
