//! Frame: the unifying storage primitive for all typed memories.
//!
//! Every memory category in animaOS (facts, preferences, goals, relationships,
//! episodes, claims, emotional signals, self-model blocks, knowledge graph
//! entities/edges) is represented as a Frame at the storage layer.
//!
//! Typed memory categories are *views* over frames, not separate storage paths.

use serde::{Deserialize, Serialize};

/// Unique frame identifier (dense, monotonically increasing).
pub type FrameId = u64;

/// What kind of memory this frame represents.
///
/// Maps 1:1 to animaOS's existing memory categories so Python can construct
/// frames from SQLAlchemy models without loss.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum FrameKind {
    /// Biographical information: "Works as engineer"
    Fact = 0,
    /// Likes, dislikes, patterns: "Prefers dark mode"
    Preference = 1,
    /// Aspirations, objectives: "Learn Rust"
    Goal = 2,
    /// People and connections: "Sister is Alice"
    Relationship = 3,
    /// Summarized conversation sessions
    Episode = 4,
    /// Structured slot-based claims with evidence
    Claim = 5,
    /// Detected emotional states
    EmotionalSignal = 6,
    /// Agent self-understanding blocks
    SelfModel = 7,
    /// Knowledge graph entity
    KgNode = 8,
    /// Knowledge graph relationship
    KgEdge = 9,
    /// Current primary focus
    Focus = 10,
    /// Raw conversation records
    DailyLog = 11,
    /// Growth log entry (append-only reflection history)
    GrowthLog = 12,
    /// Identity block (soul-tier self-narrative)
    Identity = 13,
}

impl FrameKind {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Fact => "fact",
            Self::Preference => "preference",
            Self::Goal => "goal",
            Self::Relationship => "relationship",
            Self::Episode => "episode",
            Self::Claim => "claim",
            Self::EmotionalSignal => "emotional_signal",
            Self::SelfModel => "self_model",
            Self::KgNode => "kg_node",
            Self::KgEdge => "kg_edge",
            Self::Focus => "focus",
            Self::DailyLog => "daily_log",
            Self::GrowthLog => "growth_log",
            Self::Identity => "identity",
        }
    }

    #[must_use]
    pub fn from_str(s: &str) -> Self {
        match s {
            "fact" => Self::Fact,
            "preference" => Self::Preference,
            "goal" => Self::Goal,
            "relationship" => Self::Relationship,
            "episode" => Self::Episode,
            "claim" => Self::Claim,
            "emotional_signal" => Self::EmotionalSignal,
            "self_model" => Self::SelfModel,
            "kg_node" => Self::KgNode,
            "kg_edge" => Self::KgEdge,
            "focus" => Self::Focus,
            "daily_log" => Self::DailyLog,
            "growth_log" => Self::GrowthLog,
            "identity" => Self::Identity,
            _ => Self::Fact,
        }
    }
}

impl std::fmt::Display for FrameKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// Lifecycle status of a frame.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum FrameStatus {
    /// Currently valid and active.
    Active = 0,
    /// Replaced by a newer frame (full audit trail preserved).
    Superseded = 1,
    /// Soft-deleted (retained for audit, excluded from queries).
    Deleted = 2,
}

impl Default for FrameStatus {
    fn default() -> Self {
        Self::Active
    }
}

/// How this frame was created.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum FrameSource {
    /// Directly from user message content.
    UserMessage = 0,
    /// Extracted by consolidation/LLM pipeline.
    Extraction = 1,
    /// Generated during agent reflection.
    Reflection = 2,
    /// Created by background sleep tasks.
    SleepTask = 3,
    /// Imported from capsule or external source.
    Import = 4,
    /// Created via API call.
    Api = 5,
    /// System-generated (migrations, schema updates).
    System = 6,
}

impl Default for FrameSource {
    fn default() -> Self {
        Self::Extraction
    }
}

/// Kind-specific metadata stored alongside the frame content.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FrameMetadata {
    /// Importance score (1-5, matching animaOS's scale).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub importance: Option<u8>,

    /// Category string for legacy compatibility (e.g., "fact", "preference").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub category: Option<String>,

    /// Tags for filtering and grouping.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,

    /// Self-model section (for SelfModel/Identity kinds).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub section: Option<String>,

    /// Emotional signal fields.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub emotion: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f32>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trajectory: Option<String>,

    /// Episode fields.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub topics: Option<Vec<String>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub emotional_arc: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub significance: Option<f32>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub turn_count: Option<u32>,

    /// Heat score (computed from access frequency, recency, importance).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub heat: Option<f64>,

    /// Reference count (number of times retrieved).
    #[serde(default)]
    pub reference_count: u32,

    /// Last time this frame was referenced (Unix seconds).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_referenced_at: Option<i64>,

    /// Freeform JSON for extensibility.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub extra: Option<serde_json::Value>,
}

/// The fundamental storage atom for all animaOS memories.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Frame {
    /// Unique identifier (dense, monotonically increasing within a store).
    pub id: FrameId,

    /// What kind of memory this represents.
    pub kind: FrameKind,

    /// The textual content of the memory.
    pub content: String,

    /// When this frame was created (Unix seconds).
    pub timestamp: i64,

    /// Lifecycle status.
    #[serde(default)]
    pub status: FrameStatus,

    /// BLAKE3 hash of content bytes for integrity verification.
    pub checksum: [u8; 32],

    /// Kind-specific metadata.
    #[serde(default)]
    pub metadata: FrameMetadata,

    /// If superseded, the ID of the frame that replaced this one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub superseded_by: Option<FrameId>,

    /// How this frame was created.
    #[serde(default)]
    pub source: FrameSource,

    /// User this frame belongs to.
    pub user_id: String,
}

impl Frame {
    /// Create a new active frame with computed checksum.
    pub fn new(
        id: FrameId,
        kind: FrameKind,
        content: String,
        user_id: String,
        source: FrameSource,
    ) -> Self {
        let checksum = blake3::hash(content.as_bytes()).into();
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);

        Self {
            id,
            kind,
            content,
            timestamp,
            status: FrameStatus::Active,
            checksum,
            metadata: FrameMetadata::default(),
            superseded_by: None,
            source,
            user_id,
        }
    }

    /// Create a frame with a specific timestamp (for imports/tests).
    pub fn with_timestamp(mut self, timestamp: i64) -> Self {
        self.timestamp = timestamp;
        self
    }

    /// Attach metadata to this frame.
    pub fn with_metadata(mut self, metadata: FrameMetadata) -> Self {
        self.metadata = metadata;
        self
    }

    /// Mark this frame as superseded by another.
    pub fn supersede(&mut self, new_frame_id: FrameId) {
        self.status = FrameStatus::Superseded;
        self.superseded_by = Some(new_frame_id);
    }

    /// Soft-delete this frame.
    pub fn delete(&mut self) {
        self.status = FrameStatus::Deleted;
    }

    /// Check if this frame is active.
    #[must_use]
    pub fn is_active(&self) -> bool {
        self.status == FrameStatus::Active
    }

    /// Verify content integrity against stored checksum.
    #[must_use]
    pub fn verify_checksum(&self) -> bool {
        let computed: [u8; 32] = blake3::hash(self.content.as_bytes()).into();
        computed == self.checksum
    }

    /// Record an access (bump reference count and timestamp).
    pub fn touch(&mut self) {
        self.metadata.reference_count += 1;
        self.metadata.last_referenced_at = Some(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0),
        );
    }
}

/// An in-memory frame store with fast lookups by ID and kind.
#[derive(Debug, Clone, Default)]
pub struct FrameStore {
    frames: Vec<Frame>,
    next_id: FrameId,
    /// Index: kind → frame indices in `frames` vec.
    kind_index: std::collections::HashMap<FrameKind, Vec<usize>>,
    /// Index: user_id → frame indices.
    user_index: std::collections::HashMap<String, Vec<usize>>,
    id_index: std::collections::HashMap<FrameId, usize>,
}

#[derive(Serialize)]
struct ActiveFrameIdentity<'a> {
    kind: FrameKind,
    content: &'a str,
    checksum: [u8; 32],
    metadata: &'a FrameMetadata,
    source: FrameSource,
    user_id: &'a str,
}

impl FrameStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Insert a frame, assigning the next available ID.
    pub fn insert(&mut self, mut frame: Frame) -> FrameId {
        if frame.is_active() {
            if let Some(existing_id) = self.find_active_duplicate(&frame) {
                return existing_id;
            }
        }

        frame.id = self.next_id;
        self.next_id += 1;

        let idx = self.frames.len();
        self.kind_index.entry(frame.kind).or_default().push(idx);
        self.user_index
            .entry(frame.user_id.clone())
            .or_default()
            .push(idx);
        self.id_index.insert(frame.id, idx);

        let id = frame.id;
        self.frames.push(frame);
        id
    }

    fn find_active_duplicate(&self, frame: &Frame) -> Option<FrameId> {
        self.user_index.get(&frame.user_id).and_then(|indices| {
            let identity = frame_identity_key(frame);
            indices.iter().find_map(|&idx| {
                let existing = &self.frames[idx];
                if existing.is_active() && frame_identity_key(existing) == identity {
                    Some(existing.id)
                } else {
                    None
                }
            })
        })
    }

    /// Get a frame by ID.
    #[must_use]
    pub fn get(&self, id: FrameId) -> Option<&Frame> {
        self.id_index.get(&id).map(|&idx| &self.frames[idx])
    }

    /// Get a mutable frame by ID.
    pub fn get_mut(&mut self, id: FrameId) -> Option<&mut Frame> {
        self.id_index
            .get(&id)
            .copied()
            .map(|idx| &mut self.frames[idx])
    }

    /// Get all active frames of a given kind.
    pub fn by_kind(&self, kind: FrameKind) -> Vec<&Frame> {
        self.kind_index
            .get(&kind)
            .map(|indices| {
                indices
                    .iter()
                    .filter_map(|&idx| {
                        let f = &self.frames[idx];
                        if f.is_active() {
                            Some(f)
                        } else {
                            None
                        }
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Get all active frames for a user.
    pub fn by_user(&self, user_id: &str) -> Vec<&Frame> {
        self.user_index
            .get(user_id)
            .map(|indices| {
                indices
                    .iter()
                    .filter_map(|&idx| {
                        let f = &self.frames[idx];
                        if f.is_active() {
                            Some(f)
                        } else {
                            None
                        }
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Total number of frames (including superseded/deleted).
    #[must_use]
    pub fn len(&self) -> usize {
        self.frames.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.frames.is_empty()
    }

    /// Number of active frames.
    #[must_use]
    pub fn active_count(&self) -> usize {
        self.frames.iter().filter(|f| f.is_active()).count()
    }

    /// Iterate all frames.
    pub fn iter(&self) -> impl Iterator<Item = &Frame> {
        self.frames.iter()
    }

    /// Serialize the entire store to JSON bytes.
    pub fn serialize(&self) -> crate::Result<Vec<u8>> {
        serde_json::to_vec(&self.frames).map_err(|e| crate::Error::Serialization(e.to_string()))
    }

    /// Deserialize frames from JSON bytes and rebuild indices.
    pub fn deserialize(bytes: &[u8]) -> crate::Result<Self> {
        let frames: Vec<Frame> = serde_json::from_slice(bytes)
            .map_err(|e| crate::Error::Serialization(e.to_string()))?;

        let mut store = Self {
            next_id: frames
                .iter()
                .map(|frame| frame.id)
                .max()
                .map_or(0, |id| id + 1),
            ..Default::default()
        };

        for frame in frames {
            let idx = store.frames.len();
            store.kind_index.entry(frame.kind).or_default().push(idx);
            store
                .user_index
                .entry(frame.user_id.clone())
                .or_default()
                .push(idx);
            store.id_index.insert(frame.id, idx);
            store.frames.push(frame);
        }

        Ok(store)
    }
}

fn frame_identity_key(frame: &Frame) -> String {
    serde_json::to_string(&ActiveFrameIdentity {
        kind: frame.kind,
        content: &frame.content,
        checksum: frame.checksum,
        metadata: &frame.metadata,
        source: frame.source,
        user_id: &frame.user_id,
    })
    .expect("frame identity must serialize")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frame_creation_and_checksum() {
        let frame = Frame::new(
            0,
            FrameKind::Fact,
            "Works as engineer".into(),
            "user1".into(),
            FrameSource::Extraction,
        );
        assert!(frame.verify_checksum());
        assert!(frame.is_active());
        assert_eq!(frame.kind, FrameKind::Fact);
    }

    #[test]
    fn test_frame_supersede() {
        let mut old = Frame::new(
            0,
            FrameKind::Fact,
            "Works at Google".into(),
            "user1".into(),
            FrameSource::Extraction,
        );
        old.supersede(1);
        assert_eq!(old.status, FrameStatus::Superseded);
        assert_eq!(old.superseded_by, Some(1));
        assert!(!old.is_active());
    }

    #[test]
    fn test_frame_touch() {
        let mut frame = Frame::new(
            0,
            FrameKind::Fact,
            "test".into(),
            "user1".into(),
            FrameSource::Extraction,
        );
        assert_eq!(frame.metadata.reference_count, 0);
        frame.touch();
        assert_eq!(frame.metadata.reference_count, 1);
        assert!(frame.metadata.last_referenced_at.is_some());
    }

    #[test]
    fn test_frame_store_basic() {
        let mut store = FrameStore::new();
        let f1 = Frame::new(
            0,
            FrameKind::Fact,
            "fact1".into(),
            "user1".into(),
            FrameSource::Extraction,
        );
        let f2 = Frame::new(
            0,
            FrameKind::Preference,
            "pref1".into(),
            "user1".into(),
            FrameSource::Extraction,
        );
        let f3 = Frame::new(
            0,
            FrameKind::Fact,
            "fact2".into(),
            "user2".into(),
            FrameSource::Extraction,
        );

        let id1 = store.insert(f1);
        let id2 = store.insert(f2);
        let id3 = store.insert(f3);

        assert_eq!(id1, 0);
        assert_eq!(id2, 1);
        assert_eq!(id3, 2);

        assert_eq!(store.len(), 3);
        assert_eq!(store.by_kind(FrameKind::Fact).len(), 2);
        assert_eq!(store.by_kind(FrameKind::Preference).len(), 1);
        assert_eq!(store.by_user("user1").len(), 2);
        assert_eq!(store.by_user("user2").len(), 1);
    }

    #[test]
    fn test_frame_store_serialize_roundtrip() {
        let mut store = FrameStore::new();
        store.insert(Frame::new(
            0,
            FrameKind::Fact,
            "hello".into(),
            "u1".into(),
            FrameSource::Extraction,
        ));
        store.insert(Frame::new(
            0,
            FrameKind::Goal,
            "learn rust".into(),
            "u1".into(),
            FrameSource::UserMessage,
        ));

        let bytes = store.serialize().unwrap();
        let restored = FrameStore::deserialize(&bytes).unwrap();

        assert_eq!(restored.len(), 2);
        assert_eq!(restored.get(0).unwrap().content, "hello");
        assert_eq!(restored.get(1).unwrap().content, "learn rust");
    }

    #[test]
    fn test_frame_store_dedups_repeated_active_insert() {
        let mut store = FrameStore::new();
        let frame = Frame::new(
            0,
            FrameKind::Fact,
            "Works remotely".into(),
            "user1".into(),
            FrameSource::Extraction,
        )
        .with_metadata(FrameMetadata {
            importance: Some(4),
            category: Some("fact".into()),
            ..FrameMetadata::default()
        })
        .with_timestamp(123);

        let first_id = store.insert(frame.clone());
        let second_id = store.insert(frame);

        assert_eq!(first_id, second_id);
        assert_eq!(store.len(), 1);
        assert_eq!(store.active_count(), 1);
    }

    #[test]
    fn test_frame_store_allows_insert_after_superseded_or_deleted() {
        let mut store = FrameStore::new();
        let frame = Frame::new(
            0,
            FrameKind::Fact,
            "Lives in KL".into(),
            "user1".into(),
            FrameSource::Extraction,
        );

        let first_id = store.insert(frame.clone());
        store.get_mut(first_id).unwrap().supersede(99);
        let second_id = store.insert(frame.clone());
        store.get_mut(second_id).unwrap().delete();
        let third_id = store.insert(frame);

        assert_eq!(first_id, 0);
        assert_eq!(second_id, 1);
        assert_eq!(third_id, 2);
        assert_eq!(store.len(), 3);
        assert_eq!(store.active_count(), 1);
    }

    #[test]
    fn test_frame_store_deserialize_handles_sparse_and_reordered_ids() {
        let frames = vec![
            Frame::new(
                7,
                FrameKind::Fact,
                "later".into(),
                "user1".into(),
                FrameSource::Extraction,
            ),
            Frame::new(
                3,
                FrameKind::Preference,
                "earlier".into(),
                "user2".into(),
                FrameSource::UserMessage,
            ),
        ];

        let bytes = serde_json::to_vec(&frames).unwrap();
        let mut store = FrameStore::deserialize(&bytes).unwrap();

        assert_eq!(store.get(7).unwrap().content, "later");
        assert_eq!(store.get(3).unwrap().content, "earlier");

        store.get_mut(3).unwrap().delete();
        assert_eq!(store.get(3).unwrap().status, FrameStatus::Deleted);

        let inserted_id = store.insert(Frame::new(
            0,
            FrameKind::Goal,
            "new".into(),
            "user3".into(),
            FrameSource::Extraction,
        ));
        assert_eq!(inserted_id, 8);
        assert_eq!(store.get(8).unwrap().content, "new");
    }

    #[test]
    fn test_frame_kind_roundtrip() {
        for kind in [
            FrameKind::Fact,
            FrameKind::Preference,
            FrameKind::Goal,
            FrameKind::Relationship,
            FrameKind::Episode,
            FrameKind::Claim,
            FrameKind::EmotionalSignal,
            FrameKind::SelfModel,
            FrameKind::KgNode,
            FrameKind::KgEdge,
            FrameKind::Focus,
            FrameKind::DailyLog,
            FrameKind::GrowthLog,
            FrameKind::Identity,
        ] {
            assert_eq!(FrameKind::from_str(kind.as_str()), kind);
        }
    }
}
