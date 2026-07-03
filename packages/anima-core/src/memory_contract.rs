use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TemporalRecordStatus {
    Active,
    Superseded,
    Retracted,
    Inactive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryEndpointKind {
    User,
    Agent,
    Person,
    Project,
    Organization,
    Concept,
    External,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryClass {
    Identity,
    LifeEvent,
    Relationship,
    ActiveProject,
    Casual,
    Transient,
    EmotionalPattern,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StabilityClass {
    Stable,
    Evolving,
    Temporary,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecayClass {
    Anchored,
    Slow,
    Standard,
    Fast,
    Ephemeral,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RecallScoreBreakdown {
    #[serde(default)]
    pub lexical: f64,
    #[serde(default)]
    pub vector: f64,
    #[serde(default)]
    pub graph: f64,
    #[serde(default)]
    pub temporal: f64,
    #[serde(default)]
    pub profile: f64,
    #[serde(default)]
    pub salience: f64,
    #[serde(default)]
    pub recency: f64,
    #[serde(default)]
    pub importance: f64,
    #[serde(default)]
    pub access: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MemorySalience {
    pub memory_class: MemoryClass,
    pub stability_class: StabilityClass,
    pub decay_class: DecayClass,
    #[serde(default)]
    pub emotional_salience: f64,
    #[serde(default = "default_evidence_strength")]
    pub evidence_strength: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub heat_floor: Option<f64>,
}

fn default_evidence_strength() -> f64 {
    0.8
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TemporalFact {
    pub id: String,
    pub subject: String,
    pub predicate: String,
    pub object: String,
    pub status: TemporalRecordStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_from: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_to: Option<String>,
    #[serde(default)]
    pub evidence_ids: Vec<String>,
    #[serde(default)]
    pub score: RecallScoreBreakdown,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TemporalRelationship {
    pub id: String,
    pub source_id: String,
    pub target_id: String,
    pub relation_type: String,
    pub status: TemporalRecordStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_from: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_to: Option<String>,
    #[serde(default)]
    pub evidence_ids: Vec<String>,
    #[serde(default)]
    pub score: RecallScoreBreakdown,
}
