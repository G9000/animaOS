use std::{
    collections::BTreeMap,
    ffi::OsStr,
    io::{self, Cursor},
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
};

use cap_std::{ambient_authority, fs::Dir};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::crypto::{derive_corefs_subkeys, ObjectKind, SecretBytes};
use crate::envelope::BodyEncoding;
use crate::publication::PublicationPhase;

use super::converter::ValidationBatchPolicy;
use super::preparation::{
    manifest_root, publish_immutable_preparation_record_with_hook,
    publish_preparation_head_with_hook, FinalIntentEntry, FinalIntentSegment,
    PreparationBeginRequest, PreparationCas, PreparationError, PreparationHeadRecord,
    PreparationIdentity, PreparationOpenDisposition, PreparationPageLimits,
    PreparationPublicationTarget, PreparationReceipt, PreparationReceiptOutcome,
    PreparationReconciliationCursor, PreparationReconciliationRequest,
    PreparationReconciliationTestInstrumentation, PreparationReferenceKind,
    PreparationSegmentReference, PreparationSnapshot, PreparationState, PreparationTestLimits,
    PrepareObjectDisposition, PrepareObjectRequest, PreparedObjectDescriptor,
    PreparedObjectDescriptorSegment, WrappedObjectDekWire, MAX_FINAL_INTENT_ENTRY_BYTES,
};
use super::{CoreCommitCoordinator, PreparationTestInstrumentation};

const CORE_ID: &str = "01J00000000000000000000001";
const OTHER_CORE_ID: &str = "01J00000000000000000000002";
const PREPARATION_ID: &str = "01J00000000000000000000003";
const RECEIPT_ID: &str = "01J00000000000000000000004";
const OWNER_ID: &str = "01J00000000000000000000005";
const STABLE_ID: &str = "01J00000000000000000000006";
const STABLE_ID_TWO: &str = "01J00000000000000000000007";
const HASH_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const HASH_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const HASH_C: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

static TEST_DIRECTORY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TestCore {
    root: PathBuf,
}

impl TestCore {
    fn new(label: &str) -> Self {
        let sequence = TEST_DIRECTORY_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!(
            "anima-corefs-preparation-{label}-{}-{sequence}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        Self { root }
    }

    fn coordinator(&self, core_id: &str) -> CoreCommitCoordinator {
        CoreCommitCoordinator::new(&self.root, core_id).unwrap()
    }

    fn fs_path(&self) -> PathBuf {
        self.root.join("fs")
    }
}

impl Drop for TestCore {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

fn keys(version: u32) -> crate::crypto::FrkSubkeys {
    derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), version).unwrap()
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest: [u8; 32] = Sha256::digest(bytes).into();
    hex::encode(digest)
}

fn begin_request() -> PreparationBeginRequest {
    PreparationBeginRequest {
        scope: "pcf004-writing-v1".to_owned(),
        expected_validation_generation: Some(4),
        expected_validation_catalog_sha256: Some(HASH_A.to_owned()),
        source_owner_id: OWNER_ID.to_owned(),
        source_schema_version: 1,
        source_mutation_generation: 42,
        source_inventory_sha256: HASH_B.to_owned(),
    }
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
        total_ciphertext_bytes: 1024,
        manifest_root_sha256: manifest_root(&[segment_reference(0)]),
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
            parent_id: STABLE_ID_TWO.to_owned(),
            name: "entry.diary.json".to_owned(),
            content_type: "application/vnd.anima.diary+json;version=1".to_owned(),
            body_encoding: "utf-8".to_owned(),
            body_length: 1024,
            logical_body_length: None,
            created_at: "2026-08-02T00:00:00Z".to_owned(),
            updated_at: "2026-08-02T00:00:00Z".to_owned(),
            source_character_count: Some(5),
            references: Vec::new(),
            policy: "inherit".to_owned(),
            stable_role: None,
            graph_metadata: BTreeMap::new(),
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
    let canonical_catalog_entry_json = "{\"kind\":\"diary\"}".to_owned();
    FinalIntentSegment {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: PREPARATION_ID.to_owned(),
        required_frk_version: 3,
        segment_index: 0,
        entries: vec![FinalIntentEntry {
            ordinal: 0,
            stable_id: STABLE_ID.to_owned(),
            canonical_catalog_entry_sha256: sha256_hex(canonical_catalog_entry_json.as_bytes()),
            canonical_catalog_entry_json,
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
            PreparationSnapshot::open(sealed.as_bytes(), &current_keys, CORE_ID, 3)
                .unwrap()
                .sequence,
            7
        );
        assert!(
            PreparationSnapshot::open(sealed.as_bytes(), &current_keys, OTHER_CORE_ID, 3).is_err()
        );
        assert!(PreparationSnapshot::open(sealed.as_bytes(), &current_keys, CORE_ID, 4).is_err());
        assert!(PreparationSnapshot::open(sealed.as_bytes(), &keys(4), CORE_ID, 4).is_err());

        let mut trailing = sealed.as_bytes().to_vec();
        trailing.push(0);
        assert!(PreparationSnapshot::open(&trailing, &current_keys, CORE_ID, 3).is_err());

        let mut tampered = sealed.as_bytes().to_vec();
        *tampered.last_mut().unwrap() ^= 0x01;
        assert!(PreparationSnapshot::open(&tampered, &current_keys, CORE_ID, 3).is_err());
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
    fn final_intent_rejects_unbound_or_altered_canonical_json() {
        let mut wrong_digest = intent_segment();
        wrong_digest.entries[0].canonical_catalog_entry_sha256 = HASH_C.to_owned();
        assert!(wrong_digest.encode().is_err());

        let mut altered_json = intent_segment();
        altered_json.entries[0].canonical_catalog_entry_json = "{\"kind\":\"note\"}".to_owned();
        assert!(altered_json.encode().is_err());
    }

    #[test]
    fn snapshot_segment_indexes_must_be_ordered_and_contiguous() {
        let mut reordered = snapshot();
        reordered.manifest_segments = vec![segment_reference(1), segment_reference(0)];
        assert!(reordered.encode().is_err());

        let mut non_contiguous = snapshot();
        non_contiguous.manifest_segments = vec![segment_reference(0), segment_reference(2)];
        assert!(non_contiguous.encode().is_err());
    }

    #[test]
    fn descriptor_ordinals_must_be_ordered_and_contiguous_within_the_segment() {
        let mut reordered = descriptor_segment();
        let mut second = reordered.descriptors[0].clone();
        second.stable_id = STABLE_ID_TWO.to_owned();
        second.physical_name = "object-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.acore".to_owned();
        second.preparation_ordinal = 1;
        reordered.descriptors.insert(0, second.clone());
        assert!(reordered.encode().is_err());

        let mut non_contiguous = descriptor_segment();
        second.preparation_ordinal = 2;
        non_contiguous.descriptors.push(second);
        assert!(non_contiguous.encode().is_err());
    }

    #[test]
    fn final_intent_ordinals_must_be_ordered_and_contiguous_within_the_segment() {
        let mut reordered = intent_segment();
        let canonical_json = "{\"kind\":\"note\"}".to_owned();
        let second = FinalIntentEntry {
            ordinal: 1,
            stable_id: STABLE_ID_TWO.to_owned(),
            canonical_catalog_entry_sha256: sha256_hex(canonical_json.as_bytes()),
            canonical_catalog_entry_json: canonical_json,
        };
        reordered.entries.insert(0, second.clone());
        assert!(reordered.encode().is_err());

        let mut non_contiguous = intent_segment();
        let mut second = second;
        second.ordinal = 2;
        non_contiguous.entries.push(second);
        assert!(non_contiguous.encode().is_err());
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
        let collision = publish_immutable_preparation_record_with_hook(
            &snapshots_dir,
            &sealed_snapshot,
            &mut |_| Ok(()),
        )
        .unwrap_err();
        assert!(matches!(
            collision,
            PreparationError::Io(error) if error.kind() == io::ErrorKind::AlreadyExists
        ));

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
            sealed_head_two.as_bytes()
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

fn install_snapshot_with_references(
    test_core: &TestCore,
    status: &super::preparation::PreparationStatus,
) -> (PathBuf, PathBuf) {
    let preparation_path = test_core
        .fs_path()
        .join("preparations")
        .join(&status.preparation_id);
    let preparation_dir = Dir::open_ambient_dir(&preparation_path, ambient_authority()).unwrap();
    let descriptors_dir = preparation_dir.open_dir("descriptors").unwrap();
    let intent_dir = preparation_dir.open_dir("intent").unwrap();
    let snapshots_dir = preparation_dir.open_dir("snapshots").unwrap();

    let mut descriptor = descriptor_segment();
    descriptor.preparation_id = status.preparation_id.clone();
    let descriptor_plaintext_bytes = u32::try_from(descriptor.encode().unwrap().len()).unwrap();
    let sealed_descriptor = descriptor.seal(&keys(3)).unwrap();
    let descriptor_hash = sha256_hex(sealed_descriptor.as_bytes());
    let descriptor_name = publish_immutable_preparation_record_with_hook(
        &descriptors_dir,
        &sealed_descriptor,
        &mut |_| Ok(()),
    )
    .unwrap();

    let mut intent = intent_segment();
    intent.preparation_id = status.preparation_id.clone();
    let intent_plaintext_bytes = u32::try_from(intent.encode().unwrap().len()).unwrap();
    let sealed_intent = intent.seal(&keys(3)).unwrap();
    let intent_hash = sha256_hex(sealed_intent.as_bytes());
    let intent_name = publish_immutable_preparation_record_with_hook(
        &intent_dir,
        &sealed_intent,
        &mut |_| Ok(()),
    )
    .unwrap();

    let descriptor_reference = PreparationSegmentReference {
        segment_index: 0,
        ciphertext_sha256: descriptor_hash,
        item_count: 1,
        plaintext_bytes: descriptor_plaintext_bytes,
    };
    let next_snapshot = PreparationSnapshot {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: status.preparation_id.clone(),
        sequence: status.snapshot_sequence + 1,
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
        total_ciphertext_bytes: 1024,
        manifest_root_sha256: manifest_root(std::slice::from_ref(&descriptor_reference)),
        manifest_segments: vec![descriptor_reference],
        final_intent_root_sha256: Some(HASH_A.to_owned()),
        final_intent_segments: vec![PreparationSegmentReference {
            segment_index: 0,
            ciphertext_sha256: intent_hash,
            item_count: 1,
            plaintext_bytes: intent_plaintext_bytes,
        }],
    };
    let sealed_snapshot = next_snapshot.seal(&keys(3)).unwrap();
    let snapshot_hash = sha256_hex(sealed_snapshot.as_bytes());
    publish_immutable_preparation_record_with_hook(&snapshots_dir, &sealed_snapshot, &mut |_| {
        Ok(())
    })
    .unwrap();
    let next_head = PreparationHeadRecord {
        schema_version: 1,
        core_id: CORE_ID.to_owned(),
        preparation_id: status.preparation_id.clone(),
        snapshot_sequence: next_snapshot.sequence,
        snapshot_ciphertext_sha256: snapshot_hash,
        envelope_version: 1,
        required_frk_version: 3,
    };
    publish_preparation_head_with_hook(
        &Dir::open_ambient_dir(test_core.fs_path(), ambient_authority()).unwrap(),
        &next_head.seal(&keys(3)).unwrap(),
        &mut |_| Ok(()),
    )
    .unwrap();

    (
        preparation_path.join("descriptors").join(descriptor_name),
        preparation_path.join("intent").join(intent_name),
    )
}

mod begin_resume {
    use super::*;

    #[test]
    fn no_pointer_begins_and_same_source_identity_resumes_deterministically() {
        let test_core = TestCore::new("begin-resume");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();

        assert_eq!(begun.disposition, PreparationOpenDisposition::Begun);
        assert_eq!(begun.snapshot_sequence, 1);
        assert_eq!(begun.state, PreparationState::Collecting);
        assert_eq!(begun.source_schema_version, 1);
        assert_eq!(begun.source_mutation_generation, 42);
        assert_eq!(begun.source_inventory_sha256, HASH_B);
        assert_eq!(begun.next_descriptor_segment, 0);
        assert_eq!(begun.next_intent_segment, 0);
        assert!(!test_core.fs_path().join("VALIDATION_HEAD").exists());
        for relative in ["snapshots", "descriptors", "intent", "receipts"] {
            assert!(test_core
                .fs_path()
                .join("preparations")
                .join(&begun.preparation_id)
                .join(relative)
                .is_dir());
        }
        assert!(test_core.fs_path().join("preparation-quarantine").is_dir());

        let restarted = test_core.coordinator(CORE_ID);
        let resumed = restarted
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        assert_eq!(resumed.disposition, PreparationOpenDisposition::Resumed);
        assert_eq!(resumed.preparation_id, begun.preparation_id);
        assert_eq!(resumed.snapshot_sequence, begun.snapshot_sequence);
        assert_eq!(
            resumed.snapshot_ciphertext_sha256,
            begun.snapshot_ciphertext_sha256
        );
        assert_eq!(resumed.pointer_sha256, begun.pointer_sha256);
    }

    #[test]
    fn competing_or_stale_callers_fail_without_replacing_the_active_preparation() {
        let test_core = TestCore::new("conflicts");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();

        let mut competing = begin_request();
        competing.source_owner_id = STABLE_ID.to_owned();
        assert!(matches!(
            coordinator.begin_or_resume_preparation(&keys(3), &competing),
            Err(PreparationError::ActiveConflict("source owner"))
        ));

        let mut stale = begin_request();
        stale.source_mutation_generation -= 1;
        assert!(matches!(
            coordinator.begin_or_resume_preparation(&keys(3), &stale),
            Err(PreparationError::StaleSourceState)
        ));

        let current = coordinator.load_preparation_status(&keys(3)).unwrap();
        assert_eq!(current.preparation_id, begun.preparation_id);
        assert_eq!(current.pointer_sha256, begun.pointer_sha256);
        assert_eq!(current.snapshot_sequence, 1);
    }

    #[test]
    fn source_reconciliation_requires_exact_pointer_and_snapshot_cas() {
        let test_core = TestCore::new("cas");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let mut changed = begin_request();
        changed.source_mutation_generation += 1;
        changed.source_inventory_sha256 = HASH_C.to_owned();

        let stale_hash = PreparationCas {
            pointer_sha256: HASH_A.to_owned(),
            snapshot_sequence: begun.snapshot_sequence,
        };
        assert!(matches!(
            coordinator.reconcile_preparation_source(&keys(3), &stale_hash, &changed),
            Err(PreparationError::CasConflict)
        ));
        let stale_sequence = PreparationCas {
            pointer_sha256: begun.pointer_sha256.clone(),
            snapshot_sequence: begun.snapshot_sequence + 1,
        };
        assert!(matches!(
            coordinator.reconcile_preparation_source(&keys(3), &stale_sequence, &changed),
            Err(PreparationError::CasConflict)
        ));

        let reconciled = coordinator
            .reconcile_preparation_source(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: begun.pointer_sha256.clone(),
                    snapshot_sequence: begun.snapshot_sequence,
                },
                &changed,
            )
            .unwrap();
        assert_eq!(
            reconciled.disposition,
            PreparationOpenDisposition::Reconciled
        );
        assert_eq!(reconciled.snapshot_sequence, 2);
        assert_ne!(reconciled.pointer_sha256, begun.pointer_sha256);
    }

    #[test]
    fn missing_snapshot_corrupt_pointer_wrong_core_and_wrong_frk_fail_closed() {
        let missing_core = TestCore::new("missing-snapshot");
        let coordinator = missing_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        std::fs::remove_file(
            missing_core
                .fs_path()
                .join("preparations")
                .join(&begun.preparation_id)
                .join("snapshots")
                .join(format!(
                    "{:020}-{}.prep.acore",
                    begun.snapshot_sequence, begun.snapshot_ciphertext_sha256
                )),
        )
        .unwrap();
        assert!(matches!(
            coordinator.begin_or_resume_preparation(&keys(3), &begin_request()),
            Err(PreparationError::MissingSnapshot)
        ));

        let corrupt_core = TestCore::new("corrupt-pointer");
        let coordinator = corrupt_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        std::fs::write(corrupt_core.fs_path().join("PREPARATION_HEAD"), b"torn").unwrap();
        assert!(matches!(
            coordinator.begin_or_resume_preparation(&keys(3), &begin_request()),
            Err(PreparationError::CorruptPointer)
        ));
        assert!(corrupt_core
            .fs_path()
            .join("preparations")
            .join(begun.preparation_id)
            .exists());

        let wrong_binding_core = TestCore::new("wrong-bindings");
        let coordinator = wrong_binding_core.coordinator(CORE_ID);
        coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        assert!(matches!(
            wrong_binding_core
                .coordinator(OTHER_CORE_ID)
                .load_preparation_status(&keys(3)),
            Err(PreparationError::CorruptPointer)
        ));
        assert!(matches!(
            coordinator.load_preparation_status(&keys(4)),
            Err(PreparationError::WrongFrkVersion {
                required: 3,
                provided: 4
            })
        ));
    }

    #[test]
    fn stale_snapshot_replay_and_missing_referenced_segments_fail_closed() {
        let replay_core = TestCore::new("snapshot-replay");
        let coordinator = replay_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let snapshot_directory = replay_core
            .fs_path()
            .join("preparations")
            .join(&begun.preparation_id)
            .join("snapshots");
        let old_name = format!(
            "{:020}-{}.prep.acore",
            begun.snapshot_sequence, begun.snapshot_ciphertext_sha256
        );
        let replay_name = format!(
            "{:020}-{}.prep.acore",
            begun.snapshot_sequence + 1,
            begun.snapshot_ciphertext_sha256
        );
        std::fs::copy(
            snapshot_directory.join(old_name),
            snapshot_directory.join(replay_name),
        )
        .unwrap();
        let replayed_head = PreparationHeadRecord {
            schema_version: 1,
            core_id: CORE_ID.to_owned(),
            preparation_id: begun.preparation_id.clone(),
            snapshot_sequence: begun.snapshot_sequence + 1,
            snapshot_ciphertext_sha256: begun.snapshot_ciphertext_sha256.clone(),
            envelope_version: 1,
            required_frk_version: 3,
        };
        publish_preparation_head_with_hook(
            &Dir::open_ambient_dir(replay_core.fs_path(), ambient_authority()).unwrap(),
            &replayed_head.seal(&keys(3)).unwrap(),
            &mut |_| Ok(()),
        )
        .unwrap();
        assert!(matches!(
            coordinator.load_preparation_status(&keys(3)),
            Err(PreparationError::StaleSnapshotReplay)
        ));

        for (label, missing_kind) in [
            ("missing-descriptor", PreparationReferenceKind::Descriptor),
            ("missing-intent", PreparationReferenceKind::Intent),
        ] {
            let test_core = TestCore::new(label);
            let coordinator = test_core.coordinator(CORE_ID);
            let begun = coordinator
                .begin_or_resume_preparation(&keys(3), &begin_request())
                .unwrap();
            let (descriptor_path, intent_path) =
                install_snapshot_with_references(&test_core, &begun);
            std::fs::remove_file(match missing_kind {
                PreparationReferenceKind::Descriptor => descriptor_path,
                PreparationReferenceKind::Intent => intent_path,
            })
            .unwrap();
            assert!(matches!(
                coordinator.load_preparation_status(&keys(3)),
                Err(PreparationError::MissingReferencedRecord {
                    kind,
                    segment_index: 0
                }) if kind == missing_kind
            ));
        }
    }
}

mod crash_boundaries {
    use super::*;

    fn publication_phases(target: PreparationPublicationTarget) -> Vec<PublicationPhase> {
        let phases = vec![
            PublicationPhase::TemporaryCreated,
            PublicationPhase::PayloadWritten,
            PublicationPhase::PayloadSynced,
            PublicationPhase::DestinationPublished,
            PublicationPhase::DestinationSynced,
        ];
        #[cfg(not(windows))]
        let phases = {
            let mut phases = phases;
            if target == PreparationPublicationTarget::Snapshot {
                phases.extend([
                    PublicationPhase::StagingRemoved,
                    PublicationPhase::CleanupSynced,
                ]);
            }
            phases
        };
        #[cfg(windows)]
        let _ = target;
        phases
    }

    #[test]
    fn restart_after_each_immutable_and_pointer_boundary_observes_only_a_complete_snapshot() {
        for target in [
            PreparationPublicationTarget::Snapshot,
            PreparationPublicationTarget::Head,
        ] {
            for phase in publication_phases(target) {
                let test_core = TestCore::new(&format!("crash-{target:?}-{phase:?}"));
                let coordinator = test_core.coordinator(CORE_ID);
                let begun = coordinator
                    .begin_or_resume_preparation(&keys(3), &begin_request())
                    .unwrap();
                let mut changed = begin_request();
                changed.source_mutation_generation += 1;
                changed.source_inventory_sha256 = HASH_C.to_owned();
                let cas = PreparationCas {
                    pointer_sha256: begun.pointer_sha256.clone(),
                    snapshot_sequence: begun.snapshot_sequence,
                };
                let mut injected = false;
                let result = coordinator.reconcile_preparation_source_with_hook(
                    &keys(3),
                    &cas,
                    &changed,
                    &mut |observed_target, observed_phase| {
                        if observed_target == target && observed_phase == phase {
                            injected = true;
                            return Err(io::Error::new(io::ErrorKind::Interrupted, "crash"));
                        }
                        Ok(())
                    },
                );
                assert!(injected, "missing hook for {target:?} {phase:?}");
                assert!(result.is_err());

                let restarted = test_core.coordinator(CORE_ID);
                let authoritative = restarted.load_preparation_status(&keys(3)).unwrap();
                assert_eq!(authoritative.preparation_id, begun.preparation_id);
                assert_eq!(authoritative.state, PreparationState::Collecting);
                assert_eq!(authoritative.total_objects, 0);
                assert_eq!(authoritative.total_plaintext_bytes, 0);
                assert_eq!(authoritative.source_schema_version, 1);
                assert_eq!(authoritative.next_descriptor_segment, 0);
                assert_eq!(authoritative.next_intent_segment, 0);

                let matching_request = match authoritative.snapshot_sequence {
                    1 => {
                        assert_eq!(authoritative.pointer_sha256, begun.pointer_sha256);
                        assert_eq!(
                            authoritative.snapshot_ciphertext_sha256,
                            begun.snapshot_ciphertext_sha256
                        );
                        assert_eq!(authoritative.source_mutation_generation, 42);
                        assert_eq!(authoritative.source_inventory_sha256, HASH_B);
                        begin_request()
                    }
                    2 => {
                        assert_ne!(authoritative.pointer_sha256, begun.pointer_sha256);
                        assert_ne!(
                            authoritative.snapshot_ciphertext_sha256,
                            begun.snapshot_ciphertext_sha256
                        );
                        assert_eq!(authoritative.source_mutation_generation, 43);
                        assert_eq!(authoritative.source_inventory_sha256, HASH_C);
                        changed.clone()
                    }
                    sequence => panic!("unexpected authoritative snapshot sequence {sequence}"),
                };
                let resumed = restarted
                    .begin_or_resume_preparation(&keys(3), &matching_request)
                    .unwrap();
                assert_eq!(resumed, authoritative);
                let mut wrong_validation_hash = matching_request.clone();
                wrong_validation_hash.expected_validation_catalog_sha256 = Some(HASH_B.to_owned());
                assert!(matches!(
                    restarted.begin_or_resume_preparation(&keys(3), &wrong_validation_hash),
                    Err(PreparationError::ActiveConflict("validation head"))
                ));
                let mut wrong_validation_generation = matching_request;
                wrong_validation_generation.expected_validation_generation = Some(5);
                assert!(matches!(
                    restarted.begin_or_resume_preparation(&keys(3), &wrong_validation_generation),
                    Err(PreparationError::ActiveConflict("validation head"))
                ));
                assert!(!test_core.fs_path().join("VALIDATION_HEAD").exists());
            }
        }
    }
}

mod prepare_object {
    use super::*;

    pub(super) fn object_request(body: &[u8]) -> PrepareObjectRequest {
        PrepareObjectRequest {
            object_id: STABLE_ID.to_owned(),
            revision: 1,
            object_key_epoch: 1,
            kind: ObjectKind::Diary,
            parent_id: STABLE_ID_TWO.to_owned(),
            name: "entry.diary.json".to_owned(),
            content_type: "application/vnd.anima.diary+json;version=1".to_owned(),
            body_encoding: BodyEncoding::Utf8,
            body_length: u64::try_from(body.len()).unwrap(),
            content_sha256: sha256_hex(body),
            created_at: "2026-08-02T00:00:00Z".to_owned(),
            updated_at: "2026-08-02T00:00:00Z".to_owned(),
            source_character_count: Some(5),
            references: Vec::new(),
            policy: ValidationBatchPolicy::Inherit,
            stable_role: None,
            graph_metadata: BTreeMap::from([("order".to_owned(), Value::from(1))]),
            source_fingerprint_sha256: HASH_C.to_owned(),
            converter_format_version: 1,
        }
    }

    fn segmented_corpus(
        label: &str,
    ) -> (TestCore, CoreCommitCoordinator, Vec<PreparationIdentity>) {
        let test_core = TestCore::new(label);
        let coordinator = test_core.coordinator(CORE_ID);
        let mut status = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let limits = PreparationTestLimits {
            descriptor_segment_items: 2,
            max_object_plaintext_bytes: 1024,
            logical_plaintext_bytes: None,
            instrumentation: None,
        };
        let mut identities = Vec::new();
        for index in 0..8_u8 {
            let body = format!(r#"{{"html":"segment-{index}"}}"#).into_bytes();
            let mut request = object_request(&body);
            request.object_id =
                crate::id::OpaqueId::derive_migration("reconciliation-segments", &[index])
                    .unwrap()
                    .as_str()
                    .to_owned();
            request.name = format!("segment-{index}.diary.json");
            status = coordinator
                .prepare_object_with_limits(
                    &keys(3),
                    &PreparationCas {
                        pointer_sha256: status.pointer_sha256,
                        snapshot_sequence: status.snapshot_sequence,
                    },
                    &request,
                    &mut Cursor::new(&body),
                    limits.clone(),
                )
                .unwrap()
                .status;
            identities.push(PreparationIdentity {
                object_id: request.object_id,
                revision: request.revision,
                content_sha256: request.content_sha256,
                preparation_ordinal: u64::from(index),
            });
        }
        (test_core, coordinator, identities)
    }

    fn active_snapshot(test_core: &TestCore) -> PreparationSnapshot {
        let pointer = std::fs::read(test_core.fs_path().join("PREPARATION_HEAD")).unwrap();
        let head = PreparationHeadRecord::open(&pointer, &keys(3), CORE_ID, 3).unwrap();
        let snapshot_name = format!(
            "{:020}-{}.prep.acore",
            head.snapshot_sequence, head.snapshot_ciphertext_sha256
        );
        let encoded = std::fs::read(
            test_core
                .fs_path()
                .join("preparations")
                .join(&head.preparation_id)
                .join("snapshots")
                .join(snapshot_name),
        )
        .unwrap();
        PreparationSnapshot::open(&encoded, &keys(3), CORE_ID, 3).unwrap()
    }

    #[test]
    fn reconciliation_reads_only_the_segment_for_each_processed_cursor_position() {
        let (_test_core, coordinator, identities) = segmented_corpus("prepare-page-segment-reads");
        let prepared_reads = Arc::new(PreparationReconciliationTestInstrumentation::default());
        let page = coordinator
            .reconcile_prepared_objects_with_instrumentation(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected: Vec::new(),
                },
                Arc::clone(&prepared_reads),
            )
            .unwrap();
        assert_eq!(page.items.len(), 1);

        let expected_reads = Arc::new(PreparationReconciliationTestInstrumentation::default());
        let expected = identities.last().unwrap().clone();
        let page = coordinator
            .reconcile_prepared_objects_with_instrumentation(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: Some(PreparationReconciliationCursor { position: 8 }),
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected: vec![expected],
                },
                Arc::clone(&expected_reads),
            )
            .unwrap();
        assert!(page.items.is_empty());
        assert!(page.missing.is_empty());
        assert!(page.conflicting.is_empty());
        assert_eq!(
            (
                prepared_reads.descriptor_segment_reads(),
                expected_reads.descriptor_segment_reads(),
            ),
            (1, 1),
            "reconciliation authenticated unrelated segments for cursor-start and expected-identity pages"
        );
    }

    #[test]
    fn reconciliation_defers_later_segment_corruption_until_that_cursor_position() {
        let (test_core, coordinator, _identities) =
            segmented_corpus("prepare-page-late-corruption");
        let snapshot = active_snapshot(&test_core);
        let reference = snapshot.manifest_segments.last().unwrap();
        assert_eq!(reference.segment_index, 3);
        let descriptor_path = test_core
            .fs_path()
            .join("preparations")
            .join(&snapshot.preparation_id)
            .join("descriptors")
            .join(format!(
                "{:020}-{}.prep-manifest.acore",
                reference.segment_index, reference.ciphertext_sha256
            ));
        let mut encoded = std::fs::read(&descriptor_path).unwrap();
        *encoded.last_mut().unwrap() ^= 0x01;
        std::fs::write(&descriptor_path, encoded).unwrap();

        let first_reads = Arc::new(PreparationReconciliationTestInstrumentation::default());
        let first = coordinator
            .reconcile_prepared_objects_with_instrumentation(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected: Vec::new(),
                },
                Arc::clone(&first_reads),
            )
            .unwrap();
        assert_eq!(first.items[0].preparation_ordinal, 0);
        assert_eq!(first_reads.descriptor_segment_reads(), 1);

        let later_reads = Arc::new(PreparationReconciliationTestInstrumentation::default());
        assert!(matches!(
            coordinator.reconcile_prepared_objects_with_instrumentation(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: Some(PreparationReconciliationCursor { position: 6 }),
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected: Vec::new(),
                },
                Arc::clone(&later_reads),
            ),
            Err(PreparationError::CorruptReferencedRecord {
                kind: PreparationReferenceKind::Descriptor,
                segment_index: 3,
            })
        ));
        assert_eq!(later_reads.descriptor_segment_reads(), 1);
    }

    #[test]
    fn prepares_first_object_and_accounts_exact_bytes_without_publishing_validation_head() {
        let test_core = TestCore::new("prepare-first-object");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let body = br#"{"html":"hello"}"#;

        let outcome = coordinator
            .prepare_object(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: begun.pointer_sha256,
                    snapshot_sequence: begun.snapshot_sequence,
                },
                &object_request(body),
                &mut Cursor::new(body),
            )
            .unwrap();

        assert_eq!(outcome.disposition, PrepareObjectDisposition::Prepared);
        assert_eq!(outcome.status.total_objects, 1);
        assert_eq!(outcome.status.total_plaintext_bytes, body.len() as u64);
        assert_eq!(
            outcome.status.total_ciphertext_bytes,
            outcome.prepared.ciphertext_bytes
        );
        assert!(outcome.prepared.ciphertext_bytes > body.len() as u64);
        assert!(!test_core.fs_path().join("VALIDATION_HEAD").exists());
    }

    #[test]
    fn declared_content_hash_and_length_are_verified_before_pointer_progress() {
        let test_core = TestCore::new("prepare-declared-body");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let body = br#"{"html":"hello"}"#;
        for request in [
            {
                let mut request = object_request(body);
                request.content_sha256 = HASH_A.to_owned();
                request
            },
            {
                let mut request = object_request(body);
                request.body_length += 1;
                request
            },
        ] {
            assert!(coordinator
                .prepare_object(
                    &keys(3),
                    &PreparationCas {
                        pointer_sha256: begun.pointer_sha256.clone(),
                        snapshot_sequence: begun.snapshot_sequence,
                    },
                    &request,
                    &mut Cursor::new(body),
                )
                .is_err());
            let unchanged = coordinator.load_preparation_status(&keys(3)).unwrap();
            assert_eq!(unchanged.pointer_sha256, begun.pointer_sha256);
            assert_eq!(unchanged.snapshot_sequence, begun.snapshot_sequence);
            assert_eq!(unchanged.total_objects, 0);
        }
    }

    struct PanicRead;

    impl io::Read for PanicRead {
        fn read(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
            panic!("idempotent preparation must authenticate durable state without reading a body")
        }
    }

    #[test]
    fn same_logical_revision_and_hash_resumes_without_rereading_but_changed_content_conflicts() {
        let test_core = TestCore::new("prepare-idempotent");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let body = br#"{"html":"hello"}"#;
        let request = object_request(body);
        let first = coordinator
            .prepare_object(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: begun.pointer_sha256,
                    snapshot_sequence: begun.snapshot_sequence,
                },
                &request,
                &mut Cursor::new(body),
            )
            .unwrap();

        let matched = coordinator
            .prepare_object(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: first.status.pointer_sha256.clone(),
                    snapshot_sequence: first.status.snapshot_sequence,
                },
                &request,
                &mut PanicRead,
            )
            .unwrap();
        assert_eq!(matched.disposition, PrepareObjectDisposition::Matched);
        assert_eq!(matched.prepared, first.prepared);
        assert_eq!(matched.status.pointer_sha256, first.status.pointer_sha256);
        assert_eq!(
            matched.status.snapshot_sequence,
            first.status.snapshot_sequence
        );
        assert_eq!(matched.status.total_objects, first.status.total_objects);
        assert_eq!(
            matched.status.total_plaintext_bytes,
            first.status.total_plaintext_bytes
        );
        assert_eq!(
            matched.status.total_ciphertext_bytes,
            first.status.total_ciphertext_bytes
        );

        let different = br#"{"html":"other"}"#;
        let conflict = coordinator
            .prepare_object(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: first.status.pointer_sha256.clone(),
                    snapshot_sequence: first.status.snapshot_sequence,
                },
                &object_request(different),
                &mut Cursor::new(different),
            )
            .unwrap_err();
        assert!(matches!(
            conflict,
            PreparationError::LogicalRevisionConflict { ref object_id, revision }
                if object_id == STABLE_ID && revision == 1
        ));
        let unchanged = coordinator.load_preparation_status(&keys(3)).unwrap();
        assert_eq!(unchanged.pointer_sha256, first.status.pointer_sha256);
        assert_eq!(unchanged.snapshot_sequence, first.status.snapshot_sequence);
        assert_eq!(unchanged.total_objects, first.status.total_objects);

        let object_path = std::fs::read_dir(test_core.root.join("objects"))
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .find(|path| path.extension().is_some_and(|value| value == "acore"))
            .unwrap();
        let mut encoded = std::fs::read(&object_path).unwrap();
        let last = encoded.last_mut().unwrap();
        *last ^= 0x01;
        std::fs::write(object_path, encoded).unwrap();
        assert!(matches!(
            coordinator.prepare_object(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: unchanged.pointer_sha256,
                    snapshot_sequence: unchanged.snapshot_sequence,
                },
                &request,
                &mut PanicRead,
            ),
            Err(PreparationError::Commit(_))
        ));
    }

    #[test]
    fn descriptor_segments_roll_over_and_reconciliation_retains_complete_graph_metadata() {
        let test_core = TestCore::new("prepare-rollover");
        let coordinator = test_core.coordinator(CORE_ID);
        let mut status = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let limits = PreparationTestLimits {
            descriptor_segment_items: 2,
            max_object_plaintext_bytes: 1024,
            logical_plaintext_bytes: None,
            instrumentation: None,
        };
        let mut ciphertext_total = 0_u64;
        for index in 0..3_u8 {
            let body = format!(r#"{{"html":"entry-{index}"}}"#).into_bytes();
            let mut request = object_request(&body);
            request.object_id = crate::id::OpaqueId::derive_migration("prepare-rollover", &[index])
                .unwrap()
                .as_str()
                .to_owned();
            request.name = format!("entry-{index}.diary.json");
            request.references = if index == 0 {
                Vec::new()
            } else {
                vec![
                    crate::id::OpaqueId::derive_migration("prepare-rollover", &[index - 1])
                        .unwrap()
                        .as_str()
                        .to_owned(),
                ]
            };
            request.stable_role = (index == 0).then(|| "core.journal".to_owned());
            request
                .graph_metadata
                .insert("index".to_owned(), Value::from(index));
            request.source_character_count = Some(format!("entry-{index}").chars().count());
            let outcome = coordinator
                .prepare_object_with_limits(
                    &keys(3),
                    &PreparationCas {
                        pointer_sha256: status.pointer_sha256,
                        snapshot_sequence: status.snapshot_sequence,
                    },
                    &request,
                    &mut Cursor::new(&body),
                    limits.clone(),
                )
                .unwrap();
            ciphertext_total = ciphertext_total
                .checked_add(outcome.prepared.ciphertext_bytes)
                .unwrap();
            status = outcome.status;
        }
        assert_eq!(status.total_objects, 3);
        assert_eq!(status.next_descriptor_segment, 2);
        assert_eq!(status.total_ciphertext_bytes, ciphertext_total);

        let first_page = coordinator
            .reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 16 * 1024,
                    },
                    expected: Vec::new(),
                },
            )
            .unwrap();
        assert_eq!(first_page.items.len(), 1);
        assert_eq!(first_page.descriptor_segment_roots.len(), 1);
        assert_eq!(
            first_page.next_cursor,
            Some(PreparationReconciliationCursor { position: 1 })
        );
        let first = &first_page.items[0];
        assert_eq!(first.kind, ObjectKind::Diary);
        assert_eq!(
            first.content_type,
            "application/vnd.anima.diary+json;version=1"
        );
        assert_eq!(first.name, "entry-0.diary.json");
        assert_eq!(first.parent_id, STABLE_ID_TWO);
        assert!(first.references.is_empty());
        assert_eq!(first.policy, ValidationBatchPolicy::Inherit);
        assert_eq!(first.stable_role.as_deref(), Some("core.journal"));
        assert_eq!(first.graph_metadata.get("index"), Some(&Value::from(0)));

        let remaining = coordinator
            .reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: first_page.next_cursor,
                    limits: PreparationPageLimits {
                        max_items: 2,
                        max_bytes: 16 * 1024,
                    },
                    expected: Vec::new(),
                },
            )
            .unwrap();
        assert_eq!(remaining.items.len(), 2);
        assert_eq!(remaining.descriptor_segment_roots.len(), 2);
        assert!(remaining.next_cursor.is_none());
    }

    fn publication_phases() -> Vec<PublicationPhase> {
        vec![
            PublicationPhase::TemporaryCreated,
            PublicationPhase::PayloadWritten,
            PublicationPhase::PayloadSynced,
            PublicationPhase::DestinationPublished,
            PublicationPhase::DestinationSynced,
        ]
    }

    #[test]
    fn object_and_descriptor_are_durable_before_snapshot_pointer_and_restart_is_old_or_new_complete(
    ) {
        let order_core = TestCore::new("prepare-order");
        let coordinator = order_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let body = br#"{"html":"hello"}"#;
        let mut observed = Vec::new();
        coordinator
            .prepare_object_with_hook(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: begun.pointer_sha256,
                    snapshot_sequence: begun.snapshot_sequence,
                },
                &object_request(body),
                &mut Cursor::new(body),
                &mut |target, phase| {
                    observed.push((target, phase));
                    Ok(())
                },
            )
            .unwrap();
        let position = |target, phase| {
            observed
                .iter()
                .position(|value| *value == (target, phase))
                .unwrap()
        };
        assert!(
            position(
                PreparationPublicationTarget::Object,
                PublicationPhase::DestinationSynced
            ) < position(
                PreparationPublicationTarget::Descriptor,
                PublicationPhase::DestinationSynced
            )
        );
        assert!(
            position(
                PreparationPublicationTarget::Descriptor,
                PublicationPhase::DestinationSynced
            ) < position(
                PreparationPublicationTarget::Snapshot,
                PublicationPhase::TemporaryCreated
            )
        );

        for target in [
            PreparationPublicationTarget::Object,
            PreparationPublicationTarget::Descriptor,
            PreparationPublicationTarget::Snapshot,
            PreparationPublicationTarget::Head,
        ] {
            for phase in publication_phases() {
                let test_core = TestCore::new(&format!("prepare-crash-{target:?}-{phase:?}"));
                let coordinator = test_core.coordinator(CORE_ID);
                let begun = coordinator
                    .begin_or_resume_preparation(&keys(3), &begin_request())
                    .unwrap();
                let mut injected = false;
                let result = coordinator.prepare_object_with_hook(
                    &keys(3),
                    &PreparationCas {
                        pointer_sha256: begun.pointer_sha256.clone(),
                        snapshot_sequence: begun.snapshot_sequence,
                    },
                    &object_request(body),
                    &mut Cursor::new(body),
                    &mut |seen_target, seen_phase| {
                        if seen_target == target && seen_phase == phase {
                            injected = true;
                            return Err(io::Error::new(io::ErrorKind::Interrupted, "crash"));
                        }
                        Ok(())
                    },
                );
                assert!(injected, "missing hook for {target:?} {phase:?}");
                assert!(result.is_err());

                let restarted = test_core.coordinator(CORE_ID);
                let status = restarted.load_preparation_status(&keys(3)).unwrap();
                assert!(
                    (status.snapshot_sequence == begun.snapshot_sequence
                        && status.pointer_sha256 == begun.pointer_sha256
                        && status.total_objects == 0)
                        || (status.snapshot_sequence == begun.snapshot_sequence + 1
                            && status.pointer_sha256 != begun.pointer_sha256
                            && status.total_objects == 1),
                    "restart exposed neither old-complete nor new-complete: {status:?}"
                );
                if status.total_objects == 1 {
                    let page = restarted
                        .reconcile_prepared_objects(
                            &keys(3),
                            &PreparationReconciliationRequest {
                                cursor: None,
                                limits: PreparationPageLimits {
                                    max_items: 1,
                                    max_bytes: 4096,
                                },
                                expected: Vec::new(),
                            },
                        )
                        .unwrap();
                    assert_eq!(page.items.len(), 1);
                }
                assert!(!test_core.fs_path().join("VALIDATION_HEAD").exists());
            }
        }
    }

    #[test]
    fn reconciliation_pages_are_byte_and_count_bounded_and_report_missing_and_conflicting_ids() {
        let test_core = TestCore::new("prepare-pages");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let body = br#"{"html":"hello"}"#;
        let prepared = coordinator
            .prepare_object(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: begun.pointer_sha256,
                    snapshot_sequence: begun.snapshot_sequence,
                },
                &object_request(body),
                &mut Cursor::new(body),
            )
            .unwrap();
        let missing = PreparationIdentity {
            object_id: STABLE_ID_TWO.to_owned(),
            revision: 1,
            content_sha256: HASH_A.to_owned(),
            preparation_ordinal: 1,
        };
        let conflicting = PreparationIdentity {
            object_id: STABLE_ID.to_owned(),
            revision: 1,
            content_sha256: HASH_A.to_owned(),
            preparation_ordinal: 0,
        };
        let page = coordinator
            .reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: 3,
                        max_bytes: 4096,
                    },
                    expected: vec![missing.clone(), conflicting.clone()],
                },
            )
            .unwrap();
        assert_eq!(page.prepared_count, 1);
        assert_eq!(page.total_plaintext_bytes, body.len() as u64);
        assert_eq!(
            page.total_ciphertext_bytes,
            prepared.prepared.ciphertext_bytes
        );
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.missing, vec![missing]);
        assert_eq!(page.conflicting, vec![conflicting]);
        assert!(page.encoded_bytes <= 4096);

        assert!(matches!(
            coordinator.reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: u32::MAX,
                        max_bytes: u32::MAX,
                    },
                    expected: Vec::new(),
                }
            ),
            Err(PreparationError::LimitExceeded("reconciliation page"))
        ));
    }

    #[test]
    fn reconciliation_count_limit_applies_across_items_missing_and_conflicting_identities() {
        let test_core = TestCore::new("prepare-page-count");
        let coordinator = test_core.coordinator(CORE_ID);
        let begun = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let body = br#"{"html":"hello"}"#;
        coordinator
            .prepare_object(
                &keys(3),
                &PreparationCas {
                    pointer_sha256: begun.pointer_sha256,
                    snapshot_sequence: begun.snapshot_sequence,
                },
                &object_request(body),
                &mut Cursor::new(body),
            )
            .unwrap();
        let conflicting = PreparationIdentity {
            object_id: STABLE_ID.to_owned(),
            revision: 1,
            content_sha256: HASH_A.to_owned(),
            preparation_ordinal: 0,
        };
        let missing = (0..3_u8)
            .map(|index| PreparationIdentity {
                object_id: crate::id::OpaqueId::derive_migration(
                    "prepare-page-count-missing",
                    &[index],
                )
                .unwrap()
                .as_str()
                .to_owned(),
                revision: 1,
                content_sha256: HASH_A.to_owned(),
                preparation_ordinal: u64::from(index) + 1,
            })
            .collect::<Vec<_>>();

        let expected = std::iter::once(conflicting.clone())
            .chain(missing.clone())
            .collect::<Vec<_>>();
        let mut cursor = None;
        let mut cursor_positions = std::collections::HashSet::new();
        let mut item_ids = Vec::new();
        let mut missing_ids = Vec::new();
        let mut conflicting_ids = Vec::new();
        loop {
            let page = coordinator
                .reconcile_prepared_objects(
                    &keys(3),
                    &PreparationReconciliationRequest {
                        cursor,
                        limits: PreparationPageLimits {
                            max_items: 1,
                            max_bytes: 4096,
                        },
                        expected: expected.clone(),
                    },
                )
                .unwrap();
            assert!(
                page.items.len() + page.missing.len() + page.conflicting.len() <= 1,
                "one page returned more classifications than its unified item budget: {page:?}"
            );
            let encoded = serde_json::to_vec(&page).unwrap();
            assert_eq!(encoded.len(), page.encoded_bytes as usize);
            assert!(encoded.len() <= 4096);
            item_ids.extend(page.items.into_iter().map(|item| item.object_id));
            missing_ids.extend(page.missing.into_iter().map(|identity| identity.object_id));
            conflicting_ids.extend(
                page.conflicting
                    .into_iter()
                    .map(|identity| identity.object_id),
            );
            let Some(next) = page.next_cursor else {
                break;
            };
            assert!(
                cursor_positions.insert(next.position),
                "reconciliation cursor failed to progress"
            );
            cursor = Some(next);
        }
        assert_eq!(item_ids, vec![STABLE_ID.to_owned()]);
        assert_eq!(conflicting_ids, vec![conflicting.object_id]);
        assert_eq!(
            missing_ids,
            missing
                .into_iter()
                .map(|identity| identity.object_id)
                .collect::<Vec<_>>()
        );

        let spacious = coordinator
            .reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected: expected.clone(),
                },
            )
            .unwrap();
        assert!(matches!(
            coordinator.reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: spacious.encoded_bytes - 1,
                    },
                    expected,
                },
            ),
            Err(PreparationError::LimitExceeded("reconciliation item"))
        ));
    }

    #[test]
    fn reconciliation_authenticates_matching_identity_only_when_its_page_is_processed() {
        let test_core = TestCore::new("prepare-page-auth");
        let coordinator = test_core.coordinator(CORE_ID);
        let mut status = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let mut second_identity = None;
        let mut second_path = None;
        for index in 0..2_u8 {
            let before = std::fs::read_dir(test_core.root.join("objects"))
                .unwrap()
                .map(|entry| entry.unwrap().path())
                .collect::<std::collections::HashSet<_>>();
            let body = format!(r#"{{"html":"entry-{index}"}}"#).into_bytes();
            let mut request = object_request(&body);
            request.object_id =
                crate::id::OpaqueId::derive_migration("prepare-page-auth", &[index])
                    .unwrap()
                    .as_str()
                    .to_owned();
            request.name = format!("entry-{index}.diary.json");
            status = coordinator
                .prepare_object(
                    &keys(3),
                    &PreparationCas {
                        pointer_sha256: status.pointer_sha256,
                        snapshot_sequence: status.snapshot_sequence,
                    },
                    &request,
                    &mut Cursor::new(&body),
                )
                .unwrap()
                .status;
            if index == 1 {
                second_identity = Some(PreparationIdentity {
                    object_id: request.object_id,
                    revision: request.revision,
                    content_sha256: request.content_sha256,
                    preparation_ordinal: 1,
                });
                second_path = std::fs::read_dir(test_core.root.join("objects"))
                    .unwrap()
                    .map(|entry| entry.unwrap().path())
                    .find(|path| !before.contains(path));
            }
        }
        let second_path = second_path.expect("second object publication");
        let mut encoded = std::fs::read(&second_path).unwrap();
        *encoded.last_mut().unwrap() ^= 0x01;
        std::fs::write(&second_path, encoded).unwrap();

        let expected = vec![second_identity.unwrap()];
        let first = coordinator
            .reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: None,
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected: expected.clone(),
                },
            )
            .unwrap();
        assert_eq!(first.items.len(), 1);
        assert!(first.missing.is_empty());
        assert!(first.conflicting.is_empty());
        assert!(first.next_cursor.is_some());
        assert!(matches!(
            coordinator.reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: first.next_cursor,
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected: expected.clone(),
                },
            ),
            Err(PreparationError::Commit(_))
        ));
        assert!(matches!(
            coordinator.reconcile_prepared_objects(
                &keys(3),
                &PreparationReconciliationRequest {
                    cursor: Some(PreparationReconciliationCursor { position: 2 }),
                    limits: PreparationPageLimits {
                        max_items: 1,
                        max_bytes: 4096,
                    },
                    expected,
                },
            ),
            Err(PreparationError::Commit(_))
        ));
    }
}

mod bounded_large_corpus {
    use super::*;

    #[test]
    fn bounded_large_corpus_owns_one_body_and_fixed_buffers_instead_of_a_corpus_vector() {
        const MODELLED_ATTACHMENT_BYTES: u64 = 100 * 1024 * 1024;
        const TEST_ATTACHMENT_BYTES: usize = 32;
        const OBJECTS: u8 = 11;
        assert!(MODELLED_ATTACHMENT_BYTES * u64::from(OBJECTS) > 1024 * 1024 * 1024);

        let test_core = TestCore::new("bounded-large-corpus");
        let coordinator = test_core.coordinator(CORE_ID);
        let mut status = coordinator
            .begin_or_resume_preparation(&keys(3), &begin_request())
            .unwrap();
        let instrumentation = Arc::new(PreparationTestInstrumentation::default());
        let limits = PreparationTestLimits {
            descriptor_segment_items: 2,
            max_object_plaintext_bytes: TEST_ATTACHMENT_BYTES as u64,
            logical_plaintext_bytes: Some(MODELLED_ATTACHMENT_BYTES),
            instrumentation: Some(Arc::clone(&instrumentation)),
        };

        for index in 0..OBJECTS {
            let bytes = vec![index; TEST_ATTACHMENT_BYTES];
            let mut request = prepare_object::object_request(&bytes);
            request.object_id =
                crate::id::OpaqueId::derive_migration("bounded-large-corpus", &[index])
                    .unwrap()
                    .as_str()
                    .to_owned();
            request.name = format!("attachment-{index}.bin");
            request.kind = ObjectKind::Attachment;
            request.content_type = "application/octet-stream".to_owned();
            request.body_encoding = BodyEncoding::Binary;
            request.source_character_count = None;
            let mut body = Cursor::new(bytes);
            status = coordinator
                .prepare_object_with_limits(
                    &keys(3),
                    &PreparationCas {
                        pointer_sha256: status.pointer_sha256,
                        snapshot_sequence: status.snapshot_sequence,
                    },
                    &request,
                    &mut body,
                    limits.clone(),
                )
                .unwrap()
                .status;
        }

        assert_eq!(status.total_objects, u32::from(OBJECTS));
        assert_eq!(
            status.total_plaintext_bytes,
            MODELLED_ATTACHMENT_BYTES * u64::from(OBJECTS)
        );
        assert!(status.total_plaintext_bytes > 1024 * 1024 * 1024);
        assert_eq!(instrumentation.peak_live_objects(), 1);
        assert_eq!(
            instrumentation.peak_plaintext_body_bytes(),
            TEST_ATTACHMENT_BYTES
        );
        assert_eq!(
            instrumentation.peak_encryption_chunk_bytes(),
            TEST_ATTACHMENT_BYTES
        );
        assert_eq!(
            instrumentation.peak_copy_buffer_bytes(),
            super::super::COPY_BUFFER_SIZE
        );
        assert!(
            instrumentation.peak_live_bytes()
                <= TEST_ATTACHMENT_BYTES + super::super::COPY_BUFFER_SIZE,
            "preparation retained more than one body plus its fixed buffer: {} bytes",
            instrumentation.peak_live_bytes()
        );
        assert_eq!(instrumentation.live_bytes(), 0);
        assert!(!test_core.fs_path().join("VALIDATION_HEAD").exists());
    }
}
