use anima_core::memory_contract::{
    DecayClass, EvidenceStrength, MemoryClass, MemoryEndpointKind, MemorySalience,
    RecallScoreBreakdown, StabilityClass, TemporalFact, TemporalRecordStatus, TemporalRelationship,
};

#[test]
fn memory_contract_serializes_snake_case_status_and_endpoint_values() {
    assert_eq!(
        serde_json::to_value(TemporalRecordStatus::Superseded).unwrap(),
        "superseded"
    );
    assert_eq!(
        serde_json::to_value(MemoryEndpointKind::External).unwrap(),
        "external"
    );
}

#[test]
fn memory_contract_default_score_breakdown_and_salience_shape() {
    let breakdown = RecallScoreBreakdown {
        lexical: 0.25,
        vector: 0.5,
        graph: 0.25,
        ..Default::default()
    };
    let value = serde_json::to_value(&breakdown).unwrap();

    assert_eq!(value["lexical"], 0.25);
    assert_eq!(value["vector"], 0.5);
    assert_eq!(value["graph"], 0.25);
    assert_eq!(value["temporal"], 0.0);

    let salience = MemorySalience {
        memory_class: MemoryClass::ActiveProject,
        stability_class: StabilityClass::Evolving,
        decay_class: DecayClass::Slow,
        emotional_salience: 0.55,
        evidence_strength: EvidenceStrength::Observed,
        heat_floor: Some(0.4),
    };
    let salience_value = serde_json::to_value(&salience).unwrap();

    assert_eq!(salience_value["memory_class"], "active_project");
    assert_eq!(salience_value["stability_class"], "evolving");
    assert_eq!(salience_value["decay_class"], "slow");
    assert_eq!(salience_value["emotional_salience"], 0.55);
    assert_eq!(salience_value["evidence_strength"], "observed");
    assert_eq!(salience_value["heat_floor"], 0.4);
}

#[test]
fn memory_contract_serializes_salience_enum_values_as_snake_case() {
    assert_eq!(
        serde_json::to_value(MemoryClass::EmotionalPattern).unwrap(),
        "emotional_pattern"
    );
    assert_eq!(
        serde_json::to_value(StabilityClass::Temporary).unwrap(),
        "temporary"
    );
    assert_eq!(
        serde_json::to_value(DecayClass::Ephemeral).unwrap(),
        "ephemeral"
    );
    assert_eq!(
        serde_json::to_value(EvidenceStrength::Inferred).unwrap(),
        "inferred"
    );
}

#[test]
fn memory_contract_temporal_records_keep_status_and_provenance_fields() {
    let fact = TemporalFact {
        id: "fact:1".to_string(),
        subject: "user".to_string(),
        predicate: "works_at".to_string(),
        object: "Anima".to_string(),
        status: TemporalRecordStatus::Active,
        valid_from: Some("2026-07-03T08:00:00Z".to_string()),
        valid_to: None,
        evidence_ids: vec!["memory:7".to_string()],
        score: RecallScoreBreakdown {
            profile: 0.8,
            ..Default::default()
        },
    };
    let relationship = TemporalRelationship {
        id: "relation:1".to_string(),
        source_id: "person:leo".to_string(),
        target_id: "project:anima".to_string(),
        relation_type: "builds".to_string(),
        status: TemporalRecordStatus::Active,
        valid_from: None,
        valid_to: None,
        evidence_ids: vec!["relation-evidence:3".to_string()],
        score: RecallScoreBreakdown::default(),
    };

    let fact_value = serde_json::to_value(&fact).unwrap();
    let relationship_value = serde_json::to_value(&relationship).unwrap();

    assert_eq!(fact_value["status"], "active");
    assert_eq!(fact_value["evidence_ids"][0], "memory:7");
    assert_eq!(fact_value["score"]["profile"], 0.8);
    assert_eq!(relationship_value["relation_type"], "builds");
    assert_eq!(relationship_value["evidence_ids"][0], "relation-evidence:3");
}
