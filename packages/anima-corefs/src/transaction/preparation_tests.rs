use std::ffi::OsStr;

use cap_std::{ambient_authority, fs::Dir};
use serde_json::Value;

use crate::crypto::{derive_corefs_subkeys, SecretBytes};
use crate::publication::PublicationPhase;

use super::preparation::{
    publish_immutable_preparation_record_with_hook, publish_preparation_head_with_hook,
    FinalIntentEntry, FinalIntentSegment, PreparationHeadRecord, PreparationReceipt,
    PreparationReceiptOutcome, PreparationSegmentReference, PreparationSnapshot, PreparationState,
    PreparedObjectDescriptor, PreparedObjectDescriptorSegment, WrappedObjectDekWire,
    MAX_FINAL_INTENT_ENTRY_BYTES,
};

const CORE_ID: &str = "01J00000000000000000000001";
const OTHER_CORE_ID: &str = "01J00000000000000000000002";
const PREPARATION_ID: &str = "01J00000000000000000000003";
const RECEIPT_ID: &str = "01J00000000000000000000004";
const OWNER_ID: &str = "01J00000000000000000000005";
const STABLE_ID: &str = "01J00000000000000000000006";
const HASH_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const HASH_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const HASH_C: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

fn keys(version: u32) -> crate::crypto::FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), version).unwrap()
}

fn head() -> PreparationHeadRecord {
    PreparationHeadRecord {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: PREPARATION_ID.to_owned(),
        snapshot_sequence: 7,
        snapshot_ciphertext_sha256: HASH_A.to_owned(),
        envelope_version: 1,
        required_frk_version: 3,
    }
}

fn segment_reference(index: u32) -> PreparationSegmentReference {
    PreparationSegmentReference {
        segment_index: index,
        ciphertext_sha256: HASH_B.to_owned(),
        item_count: 1,
        plaintext_bytes: 128,
    }
}

fn snapshot() -> PreparationSnapshot {
    PreparationSnapshot {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: PREPARATION_ID.to_owned(),
        sequence: 7,
        state: PreparationState::Collecting,
        scope: "pcf004-writing-v1".to_owned(),
        required_frk_version: 3,
        created_at_unix_ms: 1_700_000_000_000,
        updated_at_unix_ms: 1_700_000_000_001,
        expected_validation_generation: Some(4),
        expected_validation_catalog_sha256: Some(HASH_A.to_owned()),
        source_owner_id: OWNER_ID.to_owned(),
        source_inventory_version: 1,
        source_mutation_generation: 42,
        source_inventory_sha256: HASH_B.to_owned(),
        total_objects: 1,
        total_plaintext_bytes: 1024,
        manifest_root_sha256: HASH_C.to_owned(),
        manifest_segments: vec![segment_reference(0)],
        final_intent_root_sha256: None,
        final_intent_segments: Vec::new(),
    }
}

fn descriptor_segment() -> PreparedObjectDescriptorSegment {
    PreparedObjectDescriptorSegment {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: PREPARATION_ID.to_owned(),
        required_frk_version: 3,
        segment_index: 0,
        descriptors: vec![PreparedObjectDescriptor {
            stable_id: STABLE_ID.to_owned(),
            revision: 1,
            kind: "diary".to_owned(),
            object_key_epoch: 1,
            physical_name: "object-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.acore".to_owned(),
            encoded_size: 1024,
            encrypted_file_sha256: HASH_A.to_owned(),
            content_sha256: HASH_B.to_owned(),
            object_key_binding_sha256: HASH_C.to_owned(),
            wrapped_object_dek: WrappedObjectDekWire {
                frk_version: 3,
                object_key_epoch: 1,
                algorithm: "aes-256-gcm".to_owned(),
                envelope_version: 1,
                nonce_base64: "AAAAAAAAAAAAAAAA".to_owned(),
                ciphertext_base64:
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA".to_owned(),
            },
            envelope_metadata_sha256: HASH_A.to_owned(),
            source_fingerprint_sha256: HASH_B.to_owned(),
            converter_format_version: 1,
            preparation_ordinal: 0,
        }],
    }
}

fn intent_segment() -> FinalIntentSegment {
    FinalIntentSegment {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: PREPARATION_ID.to_owned(),
        required_frk_version: 3,
        segment_index: 0,
        entries: vec![FinalIntentEntry {
            ordinal: 0,
            stable_id: STABLE_ID.to_owned(),
            canonical_catalog_entry_sha256: HASH_C.to_owned(),
            canonical_catalog_entry_json: "{\"kind\":\"diary\"}".to_owned(),
        }],
    }
}

fn receipt() -> PreparationReceipt {
    PreparationReceipt {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: PREPARATION_ID.to_owned(),
        receipt_id: RECEIPT_ID.to_owned(),
        outcome: PreparationReceiptOutcome::Completed,
        required_frk_version: 3,
        final_snapshot_sequence: 7,
        final_snapshot_ciphertext_sha256: HASH_A.to_owned(),
        pointer_sha256: HASH_B.to_owned(),
        validation_generation: Some(5),
        validation_catalog_sha256: Some(HASH_C.to_owned()),
    }
}

fn with_json_mutation(
    encoded: Vec<u8>,
    mutate: impl FnOnce(&mut serde_json::Map<String, Value>),
) -> Vec<u8> {
    let mut value: Value = serde_json::from_slice(&encoded).unwrap();
    mutate(value.as_object_mut().unwrap());
    serde_json::to_vec(&value).unwrap()
}

fn assert_all_decoders_reject(mutate: impl Copy + Fn(&mut serde_json::Map<String, Value>)) {
    let encoded = with_json_mutation(head().encode().unwrap(), mutate);
    assert!(PreparationHeadRecord::decode(&encoded, CORE_ID, 3).is_err());

    let encoded = with_json_mutation(snapshot().encode().unwrap(), mutate);
    assert!(PreparationSnapshot::decode(&encoded, CORE_ID, 3).is_err());

    let encoded = with_json_mutation(descriptor_segment().encode().unwrap(), mutate);
    assert!(PreparedObjectDescriptorSegment::decode(&encoded, CORE_ID, 3).is_err());

    let encoded = with_json_mutation(intent_segment().encode().unwrap(), mutate);
    assert!(FinalIntentSegment::decode(&encoded, CORE_ID, 3).is_err());

    let encoded = with_json_mutation(receipt().encode().unwrap(), mutate);
    assert!(PreparationReceipt::decode(&encoded, CORE_ID, 3).is_err());
}

mod formats {
    use super::*;

    #[test]
    fn records_roundtrip_and_encrypted_envelopes_bind_all_aad_dimensions() {
        let current_keys = keys(3);

        let encoded = head().encode().unwrap();
        assert_eq!(
            PreparationHeadRecord::decode(&encoded, CORE_ID, 3)
                .unwrap()
                .snapshot_sequence,
            7
        );

        let sealed = snapshot().seal(&current_keys).unwrap();
        assert_eq!(
            PreparationSnapshot::open(&sealed, &current_keys, CORE_ID, 3)
                .unwrap()
                .sequence,
            7
        );
        assert!(PreparationSnapshot::open(&sealed, &current_keys, OTHER_CORE_ID, 3).is_err());
        assert!(PreparationSnapshot::open(&sealed, &current_keys, CORE_ID, 4).is_err());
        assert!(PreparationSnapshot::open(&sealed, &keys(4), CORE_ID, 4).is_err());

        let mut trailing = sealed;
        trailing.push(0);
        assert!(PreparationSnapshot::open(&trailing, &current_keys, CORE_ID, 3).is_err());
    }

    #[test]
    fn every_record_rejects_unknown_fields() {
        assert_all_decoders_reject(|object| {
            object.insert("futureField".to_owned(), Value::Bool(true));
        });
    }

    #[test]
    fn every_record_rejects_wrong_schema_core_and_frk_versions() {
        assert_all_decoders_reject(|object| {
            object.insert("schemaVersion".to_owned(), Value::from(2));
        });
        assert_all_decoders_reject(|object| {
            object.insert("coreId".to_owned(), Value::from(OTHER_CORE_ID));
        });
        assert_all_decoders_reject(|object| {
            object.insert("requiredFrkVersion".to_owned(), Value::from(4));
        });
    }

    #[test]
    fn every_record_rejects_trailing_bytes() {
        let mut encoded = head().encode().unwrap();
        encoded.push(b' ');
        assert!(PreparationHeadRecord::decode(&encoded, CORE_ID, 3).is_err());

        let mut encoded = snapshot().encode().unwrap();
        encoded.push(b' ');
        assert!(PreparationSnapshot::decode(&encoded, CORE_ID, 3).is_err());

        let mut encoded = descriptor_segment().encode().unwrap();
        encoded.push(b' ');
        assert!(PreparedObjectDescriptorSegment::decode(&encoded, CORE_ID, 3).is_err());

        let mut encoded = intent_segment().encode().unwrap();
        encoded.push(b' ');
        assert!(FinalIntentSegment::decode(&encoded, CORE_ID, 3).is_err());

        let mut encoded = receipt().encode().unwrap();
        encoded.push(b' ');
        assert!(PreparationReceipt::decode(&encoded, CORE_ID, 3).is_err());
    }

    #[test]
    fn snapshot_rejects_duplicate_segment_indexes() {
        let encoded = with_json_mutation(snapshot().encode().unwrap(), |object| {
            let segments = object
                .get_mut("manifestSegments")
                .unwrap()
                .as_array_mut()
                .unwrap();
            segments.push(segments[0].clone());
        });

        assert!(PreparationSnapshot::decode(&encoded, CORE_ID, 3).is_err());
    }

    #[test]
    fn formats_reject_oversized_fields_and_independent_envelopes() {
        let encoded = with_json_mutation(head().encode().unwrap(), |object| {
            object.insert(
                "snapshotCiphertextSha256".to_owned(),
                Value::from("a".repeat(65)),
            );
        });
        assert!(PreparationHeadRecord::decode(&encoded, CORE_ID, 3).is_err());

        let encoded = with_json_mutation(snapshot().encode().unwrap(), |object| {
            object.insert("scope".to_owned(), Value::from("x".repeat(65)));
        });
        assert!(PreparationSnapshot::decode(&encoded, CORE_ID, 3).is_err());

        let encoded = with_json_mutation(descriptor_segment().encode().unwrap(), |object| {
            object
                .get_mut("descriptors")
                .unwrap()
                .as_array_mut()
                .unwrap()[0]
                .as_object_mut()
                .unwrap()
                .insert(
                    "sourceFingerprintSha256".to_owned(),
                    Value::from("b".repeat(65)),
                );
        });
        assert!(PreparedObjectDescriptorSegment::decode(&encoded, CORE_ID, 3).is_err());

        let encoded = with_json_mutation(intent_segment().encode().unwrap(), |object| {
            object.get_mut("entries").unwrap().as_array_mut().unwrap()[0]
                .as_object_mut()
                .unwrap()
                .insert(
                    "canonicalCatalogEntryJson".to_owned(),
                    Value::from("x".repeat(MAX_FINAL_INTENT_ENTRY_BYTES + 1)),
                );
        });
        assert!(FinalIntentSegment::decode(&encoded, CORE_ID, 3).is_err());

        let encoded = with_json_mutation(receipt().encode().unwrap(), |object| {
            object.insert("pointerSha256".to_owned(), Value::from("c".repeat(65)));
        });
        assert!(PreparationReceipt::decode(&encoded, CORE_ID, 3).is_err());
    }

    #[test]
    fn publication_uses_fixed_or_content_addressed_relative_names_and_durable_hooks() {
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-preparation-publication-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let fs_dir = Dir::open_ambient_dir(&root, ambient_authority()).unwrap();
        fs_dir.create_dir("snapshots").unwrap();
        let snapshots_dir = fs_dir.open_dir("snapshots").unwrap();
        let mut immutable_phases = Vec::new();

        let sealed_snapshot = snapshot().seal(&keys(3)).unwrap();
        let name = publish_immutable_preparation_record_with_hook(
            &snapshots_dir,
            &sealed_snapshot,
            &mut |phase| {
                immutable_phases.push(phase);
                Ok(())
            },
        )
        .unwrap();
        assert!(name.starts_with("00000000000000000007-"));
        assert!(name.ends_with(".prep.acore"));
        assert!(snapshots_dir.open(OsStr::new(&name)).is_ok());
        assert!(immutable_phases.contains(&PublicationPhase::PayloadSynced));
        assert!(immutable_phases.contains(&PublicationPhase::DestinationSynced));

        let mut pointer_phases = Vec::new();
        let sealed_head_one = head().seal(&keys(3)).unwrap();
        let mut next_head = head();
        next_head.snapshot_sequence = 8;
        next_head.snapshot_ciphertext_sha256 = HASH_B.to_owned();
        let sealed_head_two = next_head.seal(&keys(3)).unwrap();
        publish_preparation_head_with_hook(&fs_dir, &sealed_head_one, &mut |phase| {
            pointer_phases.push(phase);
            Ok(())
        })
        .unwrap();
        publish_preparation_head_with_hook(&fs_dir, &sealed_head_two, &mut |_| Ok(())).unwrap();
        assert_eq!(
            std::fs::read(root.join("PREPARATION_HEAD")).unwrap(),
            sealed_head_two
        );
        assert!(
            publish_preparation_head_with_hook(&fs_dir, &sealed_snapshot, &mut |_| Ok(())).is_err()
        );
        assert!(pointer_phases.contains(&PublicationPhase::PayloadSynced));
        assert!(pointer_phases.contains(&PublicationPhase::DestinationSynced));

        drop(snapshots_dir);
        drop(fs_dir);
        std::fs::remove_dir_all(root).unwrap();
    }
}
