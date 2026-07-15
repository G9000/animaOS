use std::collections::BTreeMap;
use std::io::Cursor;

use anima_corefs::crypto::{ObjectBaseAad, ObjectKind, SecretBytes};
use anima_corefs::envelope::{
    decode_envelope, decode_envelope_range, encode_envelope, read_envelope, write_envelope,
    EnvelopeError, EnvelopeMetadata, BODY_CHUNK_PLAINTEXT_SIZE, ENVELOPE_HEADER_SIZE,
    MAX_BODY_CHUNKS, MAX_METADATA_PLAINTEXT_SIZE,
};
use serde_json::json;

fn key(byte: u8) -> SecretBytes {
    SecretBytes::new(vec![byte; 32]).unwrap()
}

fn aad(core: &str, object: &str, revision: u64, kind: ObjectKind, epoch: u32) -> ObjectBaseAad {
    ObjectBaseAad::new(core, object, kind, 1, epoch, revision).unwrap()
}

fn metadata(object_id: &str, revision: u64, body: &[u8]) -> EnvelopeMetadata {
    let mut opaque = BTreeMap::new();
    opaque.insert("zeta".to_string(), json!({"b": 2, "a": 1}));
    opaque.insert("alpha".to_string(), json!(true));
    EnvelopeMetadata::for_body(
        "note",
        object_id,
        revision,
        "2026-07-15T00:00:00Z",
        "2026-07-15T00:00:01Z",
        "text/markdown",
        opaque,
        "identity",
        body,
    )
    .unwrap()
}

#[test]
fn empty_single_and_multi_chunk_roundtrip_streaming() {
    let bodies = vec![
        Vec::new(),
        b"hello anima".to_vec(),
        vec![0x5a; BODY_CHUNK_PLAINTEXT_SIZE * 2 + 37],
    ];
    for body in bodies {
        let meta = metadata("01JOBJECT", 7, &body);
        let base = aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3);
        let mut encoded = Vec::new();
        write_envelope(
            &mut encoded,
            &key(0x11),
            &base,
            &meta,
            &mut Cursor::new(&body),
        )
        .unwrap();

        let mut decoded_body = Vec::new();
        let result = read_envelope(
            &mut Cursor::new(&encoded),
            &key(0x11),
            &base,
            &mut decoded_body,
        )
        .unwrap();
        assert_eq!(result.metadata, meta);
        assert_eq!(decoded_body, body);
        assert!(result.whole_body_verified);
    }
}

#[test]
fn encoded_bytes_hide_metadata_body_and_logical_paths() {
    let body = b"known plaintext body";
    let mut meta = metadata("01JOBJECT", 7, body);
    meta.metadata
        .insert("displayName".into(), json!("private/diary/secret-entry.md"));
    let encoded = encode_envelope(
        &key(0x11),
        &aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3),
        &meta,
        body,
    )
    .unwrap();
    assert!(!encoded.windows(body.len()).any(|window| window == body));
    assert!(!encoded
        .windows(b"private/diary/secret-entry.md".len())
        .any(|window| window == b"private/diary/secret-entry.md"));
    assert!(!encoded.windows(10).any(|window| window == b"displayNam"));
}

#[test]
fn wrong_base_aad_dimensions_and_key_fail_authentication() {
    let body = b"bound content";
    let meta = metadata("01JOBJECT", 7, body);
    let good = aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3);
    let encoded = encode_envelope(&key(0x11), &good, &meta, body).unwrap();
    let wrong = [
        aad("OTHER", "01JOBJECT", 7, ObjectKind::Note, 3),
        aad("01JCORE", "OTHER", 7, ObjectKind::Note, 3),
        aad("01JCORE", "01JOBJECT", 8, ObjectKind::Note, 3),
        aad("01JCORE", "01JOBJECT", 7, ObjectKind::Task, 3),
        aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 4),
    ];
    for bad in wrong {
        assert!(decode_envelope(&key(0x11), &bad, &encoded).is_err());
    }
    assert!(decode_envelope(&key(0x22), &good, &encoded).is_err());
}

#[test]
fn tampering_truncation_trailing_reorder_duplicate_and_splice_are_rejected() {
    let body = vec![0x41; BODY_CHUNK_PLAINTEXT_SIZE * 2 + 11];
    let meta = metadata("01JOBJECT", 7, &body);
    let base = aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3);
    let encoded = encode_envelope(&key(0x11), &base, &meta, &body).unwrap();

    for index in [ENVELOPE_HEADER_SIZE + 2, encoded.len() - 1] {
        let mut tampered = encoded.clone();
        tampered[index] ^= 0x80;
        assert!(decode_envelope(&key(0x11), &base, &tampered).is_err());
    }
    assert!(decode_envelope(&key(0x11), &base, &encoded[..encoded.len() - 1]).is_err());
    let mut trailing = encoded.clone();
    trailing.push(0);
    assert!(decode_envelope(&key(0x11), &base, &trailing).is_err());

    let frames = body_frame_ranges(&encoded);
    assert_eq!(frames.len(), 3);
    let mut reordered = encoded[..frames[0].start].to_vec();
    reordered.extend_from_slice(&encoded[frames[1].clone()]);
    reordered.extend_from_slice(&encoded[frames[0].clone()]);
    reordered.extend_from_slice(&encoded[frames[2].clone()]);
    assert!(decode_envelope(&key(0x11), &base, &reordered).is_err());

    let mut duplicated = encoded[..frames[0].start].to_vec();
    duplicated.extend_from_slice(&encoded[frames[0].clone()]);
    duplicated.extend_from_slice(&encoded[frames[0].clone()]);
    duplicated.extend_from_slice(&encoded[frames[2].clone()]);
    assert!(decode_envelope(&key(0x11), &base, &duplicated).is_err());

    let other = encode_envelope(&key(0x11), &base, &meta, &body).unwrap();
    let other_frames = body_frame_ranges(&other);
    let mut spliced = encoded[..frames[0].start].to_vec();
    spliced.extend_from_slice(&encoded[frames[0].clone()]);
    spliced.extend_from_slice(&other[other_frames[1].clone()]);
    spliced.extend_from_slice(&encoded[frames[2].clone()]);
    assert!(decode_envelope(&key(0x11), &base, &spliced).is_err());
}

#[test]
fn altered_frame_offset_final_length_and_hash_are_rejected() {
    let body = vec![0x44; BODY_CHUNK_PLAINTEXT_SIZE + 9];
    let meta = metadata("01JOBJECT", 7, &body);
    let base = aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3);
    let encoded = encode_envelope(&key(0x11), &base, &meta, &body).unwrap();
    let first = body_frame_ranges(&encoded)[0].start;
    for offset in [first + 12, first + 16, first + 28] {
        let mut changed = encoded.clone();
        changed[offset] ^= 1;
        assert!(decode_envelope(&key(0x11), &base, &changed).is_err());
    }

    let mut bad_hash = meta.clone();
    bad_hash.body_sha256 = "00".repeat(32);
    assert!(encode_envelope(&key(0x11), &base, &bad_hash, &body).is_err());
    let mut bad_length = meta;
    bad_length.body_length += 1;
    assert!(encode_envelope(&key(0x11), &base, &bad_length, &body).is_err());
}

#[test]
fn declarations_are_bounded_before_allocation() {
    let body = b"small";
    let meta = metadata("01JOBJECT", 7, body);
    let base = aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3);
    let encoded = encode_envelope(&key(0x11), &base, &meta, body).unwrap();

    let mut too_much_metadata = encoded.clone();
    too_much_metadata[12..16]
        .copy_from_slice(&((MAX_METADATA_PLAINTEXT_SIZE as u32) + 17).to_le_bytes());
    assert!(matches!(
        decode_envelope(&key(0x11), &base, &too_much_metadata),
        Err(EnvelopeError::LimitExceeded(_))
    ));

    let mut too_many_chunks = encoded;
    too_many_chunks[28..32].copy_from_slice(&((MAX_BODY_CHUNKS as u32) + 1).to_le_bytes());
    assert!(matches!(
        decode_envelope(&key(0x11), &base, &too_many_chunks),
        Err(EnvelopeError::LimitExceeded(_))
    ));

    let mut too_large_body = encode_envelope(&key(0x11), &base, &meta, body).unwrap();
    too_large_body[16..24]
        .copy_from_slice(&(anima_corefs::envelope::MAX_BODY_LENGTH + 1).to_le_bytes());
    assert!(matches!(
        decode_envelope(&key(0x11), &base, &too_large_body),
        Err(EnvelopeError::LimitExceeded(_))
    ));
}

#[test]
fn unsupported_wire_parameters_schema_versions_and_repeated_nonces_are_rejected() {
    let body = b"one encrypted chunk";
    let meta = metadata("01JOBJECT", 7, body);
    let base = aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3);
    let encoded = encode_envelope(&key(0x11), &base, &meta, body).unwrap();

    for offset in [8, 10, 11] {
        let mut changed = encoded.clone();
        changed[offset] = changed[offset].wrapping_add(1);
        assert!(matches!(
            decode_envelope(&key(0x11), &base, &changed),
            Err(EnvelopeError::Unsupported(_))
        ));
    }

    let mut unsupported_metadata = meta.clone();
    unsupported_metadata.schema_version += 1;
    assert!(matches!(
        encode_envelope(&key(0x11), &base, &unsupported_metadata, body),
        Err(EnvelopeError::Unsupported(_))
    ));

    let first_frame = body_frame_ranges(&encoded)[0].start;
    let mut repeated_nonce = encoded;
    let metadata_nonce: [u8; 12] = repeated_nonce[ENVELOPE_HEADER_SIZE..ENVELOPE_HEADER_SIZE + 12]
        .try_into()
        .unwrap();
    repeated_nonce[first_frame..first_frame + 12].copy_from_slice(&metadata_nonce);
    assert!(decode_envelope(&key(0x11), &base, &repeated_nonce).is_err());
}

#[test]
fn range_reads_authenticate_intersecting_chunks_and_report_scope() {
    let body: Vec<u8> = (0..BODY_CHUNK_PLAINTEXT_SIZE * 2 + 100)
        .map(|index| (index % 251) as u8)
        .collect();
    let meta = metadata("01JOBJECT", 7, &body);
    let base = aad("01JCORE", "01JOBJECT", 7, ObjectKind::Note, 3);
    let encoded = encode_envelope(&key(0x11), &base, &meta, &body).unwrap();

    let start = BODY_CHUNK_PLAINTEXT_SIZE as u64 - 20;
    let end = BODY_CHUNK_PLAINTEXT_SIZE as u64 + 30;
    let (result, bytes) = decode_envelope_range(&key(0x11), &base, &encoded, start..end).unwrap();
    assert_eq!(bytes, body[start as usize..end as usize]);
    assert!(!result.whole_body_verified);

    let (result, all) =
        decode_envelope_range(&key(0x11), &base, &encoded, 0..body.len() as u64).unwrap();
    assert_eq!(all, body);
    assert!(result.whole_body_verified);

    let frames = body_frame_ranges(&encoded);
    let mut tampered = encoded.clone();
    tampered[frames[1].end - 1] ^= 1;
    assert!(decode_envelope_range(&key(0x11), &base, &tampered, start..end).is_err());
}

fn body_frame_ranges(bytes: &[u8]) -> Vec<std::ops::Range<usize>> {
    let metadata_ciphertext_len = u32::from_le_bytes(bytes[12..16].try_into().unwrap()) as usize;
    let chunk_count = u32::from_le_bytes(bytes[28..32].try_into().unwrap()) as usize;
    let mut cursor = ENVELOPE_HEADER_SIZE + 12 + metadata_ciphertext_len;
    let mut ranges = Vec::new();
    for _ in 0..chunk_count {
        let cipher_len = u32::from_le_bytes([
            bytes[cursor + 29],
            bytes[cursor + 30],
            bytes[cursor + 31],
            0,
        ]) as usize;
        let end = cursor + 32 + cipher_len;
        ranges.push(cursor..end);
        cursor = end;
    }
    ranges
}
