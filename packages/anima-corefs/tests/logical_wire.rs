use anima_corefs::logical::{
    LogicalGrepMatch, LogicalGrepPage, LogicalPath, LogicalReadChunk, ModelWireV1, MODEL_WIRE_V1,
};

#[test]
fn v1_wire_base64_encodes_binary_chunks_and_has_one_exact_size_contract() {
    let chunk = LogicalReadChunk {
        generation: u64::MAX,
        path: LogicalPath::parse("Notes/\"quoted\".bin").unwrap(),
        stable_id: "01J00000000000000000000003".to_string(),
        revision: u64::MAX,
        content_hash: "ab".repeat(32),
        offset: u64::MAX,
        bytes: vec![0, 0xff, b'"', b'\\', b'\n'],
    };

    let encoded = chunk.to_model_wire_v1().unwrap();
    let decoded: serde_json::Value = serde_json::from_slice(&encoded).unwrap();

    assert_eq!(decoded["version"], MODEL_WIRE_V1);
    assert_eq!(decoded["result"]["bytesBase64"], "AP8iXAo=");
    assert!(decoded["result"].get("bytes").is_none());
    assert_eq!(encoded.len(), chunk.model_wire_v1_size().unwrap());
}

#[test]
fn v1_wire_exactly_accounts_for_escape_heavy_logical_results() {
    let page = LogicalGrepPage {
        generation: 1,
        matches: vec![LogicalGrepMatch {
            path: LogicalPath::parse("Notes/\"quoted\".md").unwrap(),
            stable_id: "01J00000000000000000000003".to_string(),
            revision: 1,
            content_hash: "cd".repeat(32),
            line_number: 1,
            byte_offset: 0,
            excerpt: "\"\\\t\n\r".repeat(32),
        }],
        skipped: Vec::new(),
        next_cursor: None,
        truncated: false,
        limit_reached: false,
    };

    let encoded = page.to_model_wire_v1().unwrap();
    let decoded: serde_json::Value = serde_json::from_slice(&encoded).unwrap();

    assert_eq!(decoded["version"], MODEL_WIRE_V1);
    assert_eq!(
        decoded["result"]["matches"][0]["excerpt"],
        "\"\\\t\n\r".repeat(32)
    );
    assert_eq!(encoded.len(), page.model_wire_v1_size().unwrap());
}
