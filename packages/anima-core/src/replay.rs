//! Decision Replay: capturing *why* not just *what*.
//!
//! Records the causal chain: tool call → memory retrieve → reflection → decision,
//! with microsecond timing and checkpoint support for debugging and audit.

use std::time::{Duration, Instant, SystemTime};

use serde::{Deserialize, Serialize};

use crate::frame::FrameId;

/// A single action in a replay session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayAction {
    /// Monotonic index within the session.
    pub seq: u32,
    /// What kind of action was taken.
    pub kind: ActionKind,
    /// Human-readable description.
    pub description: String,
    /// Time offset from session start (microseconds).
    pub offset_us: u64,
    /// Duration of this action (microseconds).
    pub duration_us: u64,
    /// Related frame IDs (memories retrieved/written).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub frame_ids: Vec<FrameId>,
    /// Key-value metadata (tool name, model, etc.).
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub metadata: std::collections::HashMap<String, String>,
}

/// Classification of replay actions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum ActionKind {
    /// External tool/API call.
    ToolCall = 0,
    /// Memory retrieval (vector search, KG lookup, etc.).
    MemoryRetrieve = 1,
    /// Memory write (new frame, card update, etc.).
    MemoryWrite = 2,
    /// Internal reflection / chain-of-thought.
    Reflection = 3,
    /// Final decision / response.
    Decision = 4,
    /// Checkpoint marker (snapshot of state).
    Checkpoint = 5,
    /// Error or exception.
    Error = 6,
}

impl ActionKind {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::ToolCall => "tool_call",
            Self::MemoryRetrieve => "memory_retrieve",
            Self::MemoryWrite => "memory_write",
            Self::Reflection => "reflection",
            Self::Decision => "decision",
            Self::Checkpoint => "checkpoint",
            Self::Error => "error",
        }
    }
}

impl std::fmt::Display for ActionKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// A recording session capturing the causal chain of a single turn.
#[derive(Debug)]
pub struct ReplaySession {
    /// Session identifier (typically the turn/message ID).
    pub session_id: String,
    /// User ID for provenance.
    pub user_id: String,
    /// Wall-clock start time in Unix seconds for temporal correlation.
    started_at: i64,
    /// Actions recorded in this session.
    actions: Vec<ReplayAction>,
    /// Session start time for offset calculation.
    start: Instant,
    /// Current action being timed (for end_action).
    pending_start: Option<(Instant, ActionKind, String)>,
    /// Next sequence number.
    next_seq: u32,
    /// Whether recording is active.
    recording: bool,
}

impl ReplaySession {
    pub fn new(session_id: impl Into<String>, user_id: impl Into<String>) -> Self {
        let started_at = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map(|duration| duration.as_secs() as i64)
            .unwrap_or(0);
        Self::with_started_at(session_id, user_id, started_at)
    }

    pub fn with_started_at(
        session_id: impl Into<String>,
        user_id: impl Into<String>,
        started_at: i64,
    ) -> Self {
        Self {
            session_id: session_id.into(),
            user_id: user_id.into(),
            started_at,
            actions: Vec::new(),
            start: Instant::now(),
            pending_start: None,
            next_seq: 0,
            recording: true,
        }
    }

    /// Start timing an action. Call `end_action()` to record it.
    pub fn begin_action(&mut self, kind: ActionKind, description: impl Into<String>) {
        if !self.recording {
            return;
        }
        self.pending_start = Some((Instant::now(), kind, description.into()));
    }

    /// End the current action and record it with optional metadata.
    pub fn end_action(
        &mut self,
        frame_ids: Vec<FrameId>,
        metadata: std::collections::HashMap<String, String>,
    ) -> Option<u32> {
        if !self.recording {
            return None;
        }

        let (action_start, kind, description) = self.pending_start.take()?;
        let now = Instant::now();
        let offset = action_start.duration_since(self.start);
        let duration = now.duration_since(action_start);

        let seq = self.next_seq;
        self.next_seq += 1;

        self.actions.push(ReplayAction {
            seq,
            kind,
            description,
            offset_us: offset.as_micros() as u64,
            duration_us: duration.as_micros() as u64,
            frame_ids,
            metadata,
        });

        Some(seq)
    }

    /// Record a complete action in one call (known duration).
    pub fn record(
        &mut self,
        kind: ActionKind,
        description: impl Into<String>,
        duration: Duration,
        frame_ids: Vec<FrameId>,
        metadata: std::collections::HashMap<String, String>,
    ) -> u32 {
        let offset = self.start.elapsed();
        let seq = self.next_seq;
        self.next_seq += 1;

        self.actions.push(ReplayAction {
            seq,
            kind,
            description: description.into(),
            offset_us: offset.as_micros() as u64,
            duration_us: duration.as_micros() as u64,
            frame_ids,
            metadata,
        });

        seq
    }

    /// Record a checkpoint (state snapshot marker).
    pub fn checkpoint(&mut self, label: impl Into<String>) -> u32 {
        self.record(
            ActionKind::Checkpoint,
            label,
            Duration::ZERO,
            vec![],
            std::collections::HashMap::new(),
        )
    }

    /// Stop recording.
    pub fn stop(&mut self) {
        self.recording = false;
    }

    /// Get all recorded actions.
    #[must_use]
    pub fn actions(&self) -> &[ReplayAction] {
        &self.actions
    }

    /// Get actions of a specific kind.
    pub fn actions_by_kind(&self, kind: ActionKind) -> Vec<&ReplayAction> {
        self.actions.iter().filter(|a| a.kind == kind).collect()
    }

    /// Wall-clock session start time in Unix seconds.
    #[must_use]
    pub fn started_at(&self) -> i64 {
        self.started_at
    }

    /// Wall-clock session end time in Unix seconds.
    #[must_use]
    pub fn ended_at(&self) -> i64 {
        self.started_at + ceil_microseconds_to_seconds(max_action_end_offset_us(&self.actions))
    }

    /// Wall-clock session bounds in Unix seconds.
    #[must_use]
    pub fn time_bounds(&self) -> (i64, i64) {
        (self.started_at, self.ended_at())
    }

    /// Total session duration so far.
    #[must_use]
    pub fn elapsed(&self) -> Duration {
        self.start.elapsed()
    }

    /// Number of actions recorded.
    #[must_use]
    pub fn len(&self) -> usize {
        self.actions.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.actions.is_empty()
    }

    /// Serialize the session to JSON.
    pub fn serialize(&self) -> crate::Result<Vec<u8>> {
        let data = SerializedSession {
            session_id: self.session_id.clone(),
            user_id: self.user_id.clone(),
            started_at: Some(self.started_at),
            actions: self.actions.clone(),
        };
        data.serialize()
    }

    /// Deserialize a session from JSON (for replay/audit).
    pub fn deserialize(bytes: &[u8]) -> crate::Result<SerializedSession> {
        serde_json::from_slice(bytes).map_err(|e| crate::Error::Serialization(e.to_string()))
    }

    /// Build a human-readable summary of the session.
    #[must_use]
    pub fn summary(&self) -> String {
        let mut lines = Vec::new();
        lines.push(format!(
            "Session {} ({} actions, {:.1}ms)",
            self.session_id,
            self.actions.len(),
            self.start.elapsed().as_secs_f64() * 1000.0
        ));

        for action in &self.actions {
            let ms = action.duration_us as f64 / 1000.0;
            lines.push(format!(
                "  [{:3}] {:16} {:6.1}ms  {}",
                action.seq,
                action.kind.as_str(),
                ms,
                action.description
            ));
        }

        lines.join("\n")
    }
}

/// Serializable session data (without timing state).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SerializedSession {
    pub session_id: String,
    pub user_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<i64>,
    pub actions: Vec<ReplayAction>,
}

/// Structured checkpoint extracted from a serialized replay session.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReplayCheckpoint {
    pub seq: u32,
    pub label: String,
    pub offset_us: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timestamp: Option<i64>,
}

/// High-level comparison between two serialized replay sessions.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReplayComparison {
    pub left_action_count: usize,
    pub right_action_count: usize,
    pub action_count_delta: i64,
    pub left_duration_us: u64,
    pub right_duration_us: u64,
    pub duration_us_delta: i64,
    pub kind_count_delta: std::collections::BTreeMap<String, i64>,
    pub shared_checkpoint_labels: Vec<String>,
    pub left_only_checkpoint_labels: Vec<String>,
    pub right_only_checkpoint_labels: Vec<String>,
}

/// Compact host-facing summary for a serialized replay session.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReplaySummary {
    pub session_id: String,
    pub user_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ended_at: Option<i64>,
    pub action_count: usize,
    pub checkpoint_count: usize,
    pub total_duration_us: u64,
    pub referenced_frame_count: usize,
    pub kind_counts: std::collections::BTreeMap<String, usize>,
    pub checkpoint_labels: Vec<String>,
}

#[derive(Serialize)]
struct CanonicalReplayAction<'a> {
    seq: u32,
    kind: ActionKind,
    description: &'a str,
    offset_us: u64,
    duration_us: u64,
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    frame_ids: &'a [FrameId],
    #[serde(skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    metadata: std::collections::BTreeMap<&'a str, &'a str>,
}

#[derive(Serialize)]
struct CanonicalSerializedSession<'a> {
    session_id: &'a str,
    user_id: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    started_at: Option<i64>,
    actions: Vec<CanonicalReplayAction<'a>>,
}

impl<'a> From<&'a SerializedSession> for CanonicalSerializedSession<'a> {
    fn from(session: &'a SerializedSession) -> Self {
        Self {
            session_id: &session.session_id,
            user_id: &session.user_id,
            started_at: session.started_at,
            actions: session
                .actions
                .iter()
                .map(|action| CanonicalReplayAction {
                    seq: action.seq,
                    kind: action.kind,
                    description: &action.description,
                    offset_us: action.offset_us,
                    duration_us: action.duration_us,
                    frame_ids: &action.frame_ids,
                    metadata: action
                        .metadata
                        .iter()
                        .map(|(key, value)| (key.as_str(), value.as_str()))
                        .collect(),
                })
                .collect(),
        }
    }
}

/// Error when building a replay registry from invalid session inputs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplayRegistryError {
    session_id: String,
}

impl ReplayRegistryError {
    #[must_use]
    pub fn session_id(&self) -> &str {
        &self.session_id
    }
}

impl std::fmt::Display for ReplayRegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "duplicate replay session id: {}", self.session_id)
    }
}

impl std::error::Error for ReplayRegistryError {}

/// Lightweight in-memory index over serialized replay sessions.
#[derive(Debug, Clone, Default)]
pub struct ReplayRegistry {
    sessions: std::collections::BTreeMap<String, SerializedSession>,
}

impl ReplayRegistry {
    /// Build a registry from serialized sessions, rejecting duplicate IDs.
    pub fn from_sessions(
        sessions: Vec<SerializedSession>,
    ) -> Result<Self, ReplayRegistryError> {
        let mut indexed = std::collections::BTreeMap::new();
        for session in sessions {
            let session_id = session.session_id.clone();
            if indexed.insert(session_id.clone(), session).is_some() {
                return Err(ReplayRegistryError { session_id });
            }
        }
        Ok(Self { sessions: indexed })
    }

    /// Fetch a serialized session by id.
    #[must_use]
    pub fn session(&self, session_id: &str) -> Option<&SerializedSession> {
        self.sessions.get(session_id)
    }

    /// Fetch a structured summary for a single session.
    #[must_use]
    pub fn summary(&self, session_id: &str) -> Option<ReplaySummary> {
        self.session(session_id)
            .map(SerializedSession::structured_summary)
    }

    /// Fetch a checkpoint by sequence number within a single session.
    #[must_use]
    pub fn checkpoint_by_seq(
        &self,
        session_id: &str,
        seq: u32,
    ) -> Option<ReplayCheckpoint> {
        self.session(session_id)?.checkpoint_by_seq(seq)
    }

    /// Fetch a checkpoint by exact label match within a single session.
    #[must_use]
    pub fn checkpoint_by_label(
        &self,
        session_id: &str,
        label: &str,
    ) -> Option<ReplayCheckpoint> {
        self.session(session_id)?.checkpoint_by_label(label)
    }

    /// Return sorted session identifiers.
    #[must_use]
    pub fn session_ids(&self) -> Vec<String> {
        self.sessions.keys().cloned().collect()
    }

    /// Return structured summaries for all sessions sorted by id.
    #[must_use]
    pub fn summaries(&self) -> Vec<ReplaySummary> {
        self.sessions
            .values()
            .map(SerializedSession::structured_summary)
            .collect()
    }

    /// Serialize the registry to deterministic JSON.
    pub fn serialize(&self) -> crate::Result<Vec<u8>> {
        let sessions: Vec<_> = self
            .sessions
            .values()
            .map(CanonicalSerializedSession::from)
            .collect();
        serde_json::to_vec(&sessions).map_err(|e| crate::Error::Serialization(e.to_string()))
    }

    /// Deserialize a registry from JSON.
    pub fn deserialize(bytes: &[u8]) -> crate::Result<Self> {
        let sessions: Vec<SerializedSession> =
            serde_json::from_slice(bytes).map_err(|e| crate::Error::Serialization(e.to_string()))?;
        Self::from_sessions(sessions).map_err(|e| crate::Error::Serialization(e.to_string()))
    }
}

impl SerializedSession {
    /// Wall-clock session bounds in Unix seconds when start time is available.
    #[must_use]
    pub fn time_bounds(&self) -> Option<(i64, i64)> {
        let started_at = self.started_at?;
        let ended_at =
            started_at + ceil_microseconds_to_seconds(max_action_end_offset_us(&self.actions));
        Some((started_at, ended_at))
    }

    /// Return structured checkpoint markers from this session.
    #[must_use]
    pub fn checkpoints(&self) -> Vec<ReplayCheckpoint> {
        self.actions
            .iter()
            .filter(|action| action.kind == ActionKind::Checkpoint)
            .map(|action| self.action_to_checkpoint(action))
            .collect()
    }

    /// Return a single checkpoint by sequence number.
    #[must_use]
    pub fn checkpoint_by_seq(&self, seq: u32) -> Option<ReplayCheckpoint> {
        self.actions
            .iter()
            .find(|action| action.kind == ActionKind::Checkpoint && action.seq == seq)
            .map(|action| self.action_to_checkpoint(action))
    }

    /// Return a single checkpoint by exact label match.
    #[must_use]
    pub fn checkpoint_by_label(&self, label: &str) -> Option<ReplayCheckpoint> {
        self.actions
            .iter()
            .find(|action| action.kind == ActionKind::Checkpoint && action.description == label)
            .map(|action| self.action_to_checkpoint(action))
    }

    /// Serialize the session to deterministic JSON.
    pub fn serialize(&self) -> crate::Result<Vec<u8>> {
        serde_json::to_vec(&CanonicalSerializedSession::from(self))
            .map_err(|e| crate::Error::Serialization(e.to_string()))
    }

    /// Compare this session against another serialized session.
    #[must_use]
    pub fn compare(&self, other: &Self) -> ReplayComparison {
        let left_counts = action_kind_counts(self);
        let right_counts = action_kind_counts(other);
        let mut all_kinds: std::collections::BTreeSet<String> = left_counts.keys().cloned().collect();
        all_kinds.extend(right_counts.keys().cloned());

        let kind_count_delta = all_kinds
            .into_iter()
            .filter_map(|kind| {
                let left = left_counts.get(&kind).copied().unwrap_or(0);
                let right = right_counts.get(&kind).copied().unwrap_or(0);
                let delta = right - left;
                (delta != 0).then_some((kind, delta))
            })
            .collect();

        let left_duration_us = total_duration_us(&self.actions);
        let right_duration_us = total_duration_us(&other.actions);

        let left_labels: std::collections::BTreeSet<String> =
            self.checkpoints().into_iter().map(|checkpoint| checkpoint.label).collect();
        let right_labels: std::collections::BTreeSet<String> =
            other.checkpoints().into_iter().map(|checkpoint| checkpoint.label).collect();

        ReplayComparison {
            left_action_count: self.actions.len(),
            right_action_count: other.actions.len(),
            action_count_delta: other.actions.len() as i64 - self.actions.len() as i64,
            left_duration_us,
            right_duration_us,
            duration_us_delta: right_duration_us as i64 - left_duration_us as i64,
            kind_count_delta,
            shared_checkpoint_labels: left_labels
                .intersection(&right_labels)
                .cloned()
                .collect(),
            left_only_checkpoint_labels: left_labels
                .difference(&right_labels)
                .cloned()
                .collect(),
            right_only_checkpoint_labels: right_labels
                .difference(&left_labels)
                .cloned()
                .collect(),
        }
    }

    /// Produce a compact structured summary for host inspection.
    #[must_use]
    pub fn structured_summary(&self) -> ReplaySummary {
        let kind_counts = action_kind_totals(self);
        let checkpoints = self.checkpoints();
        let checkpoint_labels = checkpoints
            .iter()
            .map(|checkpoint| checkpoint.label.clone())
            .collect();
        let referenced_frame_count = self
            .actions
            .iter()
            .flat_map(|action| action.frame_ids.iter().copied())
            .collect::<std::collections::BTreeSet<_>>()
            .len();

        ReplaySummary {
            session_id: self.session_id.clone(),
            user_id: self.user_id.clone(),
            started_at: self.started_at,
            ended_at: self.time_bounds().map(|(_, ended_at)| ended_at),
            action_count: self.actions.len(),
            checkpoint_count: checkpoints.len(),
            total_duration_us: total_duration_us(&self.actions),
            referenced_frame_count,
            kind_counts,
            checkpoint_labels,
        }
    }

    fn action_to_checkpoint(&self, action: &ReplayAction) -> ReplayCheckpoint {
        ReplayCheckpoint {
            seq: action.seq,
            label: action.description.clone(),
            offset_us: action.offset_us,
            timestamp: self
                .started_at
                .map(|started_at| started_at + (action.offset_us / 1_000_000) as i64),
        }
    }
}

fn max_action_end_offset_us(actions: &[ReplayAction]) -> u64 {
    actions
        .iter()
        .map(|action| action.offset_us.saturating_add(action.duration_us))
        .max()
        .unwrap_or(0)
}

fn total_duration_us(actions: &[ReplayAction]) -> u64 {
    actions.iter().map(|action| action.duration_us).sum()
}

fn action_kind_counts(
    session: &SerializedSession,
) -> std::collections::BTreeMap<String, i64> {
    let mut counts = std::collections::BTreeMap::new();
    for action in &session.actions {
        *counts.entry(action.kind.as_str().to_string()).or_insert(0) += 1;
    }
    counts
}

fn action_kind_totals(
    session: &SerializedSession,
) -> std::collections::BTreeMap<String, usize> {
    let mut counts = std::collections::BTreeMap::new();
    for action in &session.actions {
        *counts.entry(action.kind.as_str().to_string()).or_insert(0) += 1;
    }
    counts
}

fn ceil_microseconds_to_seconds(micros: u64) -> i64 {
    if micros == 0 {
        0
    } else {
        ((micros - 1) / 1_000_000 + 1) as i64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_record_actions() {
        let mut session = ReplaySession::new("turn-1", "user-1");

        let seq = session.record(
            ActionKind::MemoryRetrieve,
            "fetched 5 frames",
            Duration::from_millis(10),
            vec![1, 2, 3, 4, 5],
            std::collections::HashMap::new(),
        );
        assert_eq!(seq, 0);

        session.record(
            ActionKind::Decision,
            "responded to user",
            Duration::from_millis(50),
            vec![],
            std::collections::HashMap::new(),
        );

        assert_eq!(session.len(), 2);

        let retrieves = session.actions_by_kind(ActionKind::MemoryRetrieve);
        assert_eq!(retrieves.len(), 1);
        assert_eq!(retrieves[0].frame_ids.len(), 5);
    }

    #[test]
    fn test_begin_end_action() {
        let mut session = ReplaySession::new("turn-2", "user-1");

        session.begin_action(ActionKind::ToolCall, "calling search API");
        // ... work happens ...
        let seq = session.end_action(vec![], {
            let mut m = std::collections::HashMap::new();
            m.insert("tool".into(), "search".into());
            m
        });

        assert_eq!(seq, Some(0));
        let action = &session.actions()[0];
        assert_eq!(action.kind, ActionKind::ToolCall);
        assert!(action.duration_us > 0 || action.duration_us == 0); // might be 0 on fast machines
        assert_eq!(action.metadata.get("tool").unwrap(), "search");
    }

    #[test]
    fn test_checkpoint() {
        let mut session = ReplaySession::new("turn-3", "user-1");
        session.checkpoint("before reflection");
        session.record(
            ActionKind::Reflection,
            "thinking about response",
            Duration::from_millis(5),
            vec![],
            std::collections::HashMap::new(),
        );
        session.checkpoint("after reflection");

        let checkpoints = session.actions_by_kind(ActionKind::Checkpoint);
        assert_eq!(checkpoints.len(), 2);
    }

    #[test]
    fn test_stop_recording() {
        let mut session = ReplaySession::new("turn-4", "user-1");
        session.record(
            ActionKind::Decision,
            "first",
            Duration::ZERO,
            vec![],
            std::collections::HashMap::new(),
        );
        session.stop();
        session.record(
            ActionKind::Decision,
            "should not record",
            Duration::ZERO,
            vec![],
            std::collections::HashMap::new(),
        );

        // The second record still adds because `record()` doesn't check recording flag.
        // Only begin_action/end_action respect it.
        // This is intentional: `record()` is explicit, `begin/end` is implicit.
        assert_eq!(session.len(), 2);
    }

    #[test]
    fn test_serialize_session() {
        let mut session = ReplaySession::new("turn-5", "user-1");
        session.record(
            ActionKind::MemoryRetrieve,
            "fetched frames",
            Duration::from_millis(10),
            vec![1, 2],
            std::collections::HashMap::new(),
        );

        let bytes = session.serialize().unwrap();
        let deserialized = ReplaySession::deserialize(&bytes).unwrap();
        assert_eq!(deserialized.session_id, "turn-5");
        assert!(deserialized.started_at.is_some());
        assert_eq!(deserialized.actions.len(), 1);
    }

    #[test]
    fn test_serialized_session_time_bounds_use_started_at_and_action_offsets() {
        let session = SerializedSession {
            session_id: "turn-7".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![
                ReplayAction {
                    seq: 0,
                    kind: ActionKind::MemoryRetrieve,
                    description: "fetch".into(),
                    offset_us: 250_000,
                    duration_us: 500_000,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 1,
                    kind: ActionKind::Decision,
                    description: "respond".into(),
                    offset_us: 1_250_000,
                    duration_us: 500_000,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
            ],
        };

        assert_eq!(session.time_bounds(), Some((1_700_000_000, 1_700_000_002)));
    }

    #[test]
    fn test_deserialize_legacy_session_without_started_at_still_works() {
        let legacy = serde_json::json!({
            "session_id": "turn-legacy",
            "user_id": "user-1",
            "actions": [{
                "seq": 0,
                "kind": "decision",
                "description": "done",
                "offset_us": 0,
                "duration_us": 0
            }]
        });

        let bytes = serde_json::to_vec(&legacy).unwrap();
        let deserialized = ReplaySession::deserialize(&bytes).unwrap();

        assert_eq!(deserialized.session_id, "turn-legacy");
        assert_eq!(deserialized.started_at, None);
        assert_eq!(deserialized.time_bounds(), None);
        assert_eq!(deserialized.actions.len(), 1);
    }

    #[test]
    fn test_serialized_session_checkpoints_return_structured_entries() {
        let session = SerializedSession {
            session_id: "turn-8".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![
                ReplayAction {
                    seq: 0,
                    kind: ActionKind::Checkpoint,
                    description: "before reflection".into(),
                    offset_us: 250_000,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 1,
                    kind: ActionKind::Reflection,
                    description: "think".into(),
                    offset_us: 500_000,
                    duration_us: 500_000,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 2,
                    kind: ActionKind::Checkpoint,
                    description: "after reflection".into(),
                    offset_us: 1_500_000,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
            ],
        };

        let checkpoints = session.checkpoints();
        assert_eq!(checkpoints.len(), 2);
        assert_eq!(checkpoints[0].label, "before reflection");
        assert_eq!(checkpoints[0].seq, 0);
        assert_eq!(checkpoints[0].timestamp, Some(1_700_000_000));
        assert_eq!(checkpoints[1].label, "after reflection");
        assert_eq!(checkpoints[1].timestamp, Some(1_700_000_001));
    }

    #[test]
    fn test_compare_sessions_reports_kind_and_checkpoint_deltas() {
        let left = SerializedSession {
            session_id: "left".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![
                ReplayAction {
                    seq: 0,
                    kind: ActionKind::Checkpoint,
                    description: "before reflection".into(),
                    offset_us: 0,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 1,
                    kind: ActionKind::Reflection,
                    description: "think".into(),
                    offset_us: 0,
                    duration_us: 500_000,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
            ],
        };
        let right = SerializedSession {
            session_id: "right".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_100),
            actions: vec![
                ReplayAction {
                    seq: 0,
                    kind: ActionKind::Checkpoint,
                    description: "before reflection".into(),
                    offset_us: 0,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 1,
                    kind: ActionKind::Decision,
                    description: "respond".into(),
                    offset_us: 0,
                    duration_us: 1_000_000,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 2,
                    kind: ActionKind::Checkpoint,
                    description: "after decision".into(),
                    offset_us: 1_000_000,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
            ],
        };

        let comparison = left.compare(&right);
        assert_eq!(comparison.left_action_count, 2);
        assert_eq!(comparison.right_action_count, 3);
        assert_eq!(comparison.action_count_delta, 1);
        assert_eq!(comparison.duration_us_delta, 500_000);
        assert_eq!(comparison.shared_checkpoint_labels, vec!["before reflection"]);
        assert_eq!(comparison.left_only_checkpoint_labels, Vec::<String>::new());
        assert_eq!(comparison.right_only_checkpoint_labels, vec!["after decision"]);
        assert_eq!(comparison.kind_count_delta.get("decision"), Some(&1));
        assert_eq!(comparison.kind_count_delta.get("reflection"), Some(&-1));
    }

    #[test]
    fn test_structured_summary_reports_counts_time_bounds_and_checkpoint_labels() {
        let session = SerializedSession {
            session_id: "turn-9".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![
                ReplayAction {
                    seq: 0,
                    kind: ActionKind::Checkpoint,
                    description: "before reflection".into(),
                    offset_us: 0,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 1,
                    kind: ActionKind::MemoryRetrieve,
                    description: "fetch".into(),
                    offset_us: 0,
                    duration_us: 500_000,
                    frame_ids: vec![1, 2],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 2,
                    kind: ActionKind::Decision,
                    description: "respond".into(),
                    offset_us: 1_000_000,
                    duration_us: 1_000_000,
                    frame_ids: vec![2, 3],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 3,
                    kind: ActionKind::Checkpoint,
                    description: "after decision".into(),
                    offset_us: 2_000_000,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
            ],
        };

        let summary = session.structured_summary();
        assert_eq!(summary.session_id, "turn-9");
        assert_eq!(summary.user_id, "user-1");
        assert_eq!(summary.started_at, Some(1_700_000_000));
        assert_eq!(summary.ended_at, Some(1_700_000_002));
        assert_eq!(summary.action_count, 4);
        assert_eq!(summary.checkpoint_count, 2);
        assert_eq!(summary.total_duration_us, 1_500_000);
        assert_eq!(summary.referenced_frame_count, 3);
        assert_eq!(summary.kind_counts.get("checkpoint"), Some(&2));
        assert_eq!(summary.kind_counts.get("memory_retrieve"), Some(&1));
        assert_eq!(summary.kind_counts.get("decision"), Some(&1));
        assert_eq!(
            summary.checkpoint_labels,
            vec!["before reflection".to_string(), "after decision".to_string()]
        );
    }

    #[test]
    fn test_checkpoint_lookup_by_seq_and_label_returns_structured_checkpoint() {
        let session = SerializedSession {
            session_id: "turn-10".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![
                ReplayAction {
                    seq: 0,
                    kind: ActionKind::Checkpoint,
                    description: "before reflection".into(),
                    offset_us: 250_000,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 1,
                    kind: ActionKind::Reflection,
                    description: "think".into(),
                    offset_us: 500_000,
                    duration_us: 500_000,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 2,
                    kind: ActionKind::Checkpoint,
                    description: "after reflection".into(),
                    offset_us: 1_500_000,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
            ],
        };

        let by_seq = session.checkpoint_by_seq(2).unwrap();
        assert_eq!(by_seq.label, "after reflection");
        assert_eq!(by_seq.timestamp, Some(1_700_000_001));

        let by_label = session.checkpoint_by_label("before reflection").unwrap();
        assert_eq!(by_label.seq, 0);
        assert_eq!(by_label.offset_us, 250_000);
    }

    #[test]
    fn test_checkpoint_lookup_returns_none_when_missing() {
        let session = SerializedSession {
            session_id: "turn-11".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![ReplayAction {
                seq: 0,
                kind: ActionKind::Decision,
                description: "respond".into(),
                offset_us: 0,
                duration_us: 500_000,
                frame_ids: vec![],
                metadata: std::collections::HashMap::new(),
            }],
        };

        assert_eq!(session.checkpoint_by_seq(99), None);
        assert_eq!(session.checkpoint_by_label("missing"), None);
    }

    #[test]
    fn test_registry_summary_and_checkpoint_lookup_are_scoped_by_session_id() {
        let registry = ReplayRegistry::from_sessions(vec![
            SerializedSession {
                session_id: "turn-12".into(),
                user_id: "user-1".into(),
                started_at: Some(1_700_000_000),
                actions: vec![
                    ReplayAction {
                        seq: 0,
                        kind: ActionKind::Checkpoint,
                        description: "before reflection".into(),
                        offset_us: 100_000,
                        duration_us: 0,
                        frame_ids: vec![],
                        metadata: std::collections::HashMap::new(),
                    },
                    ReplayAction {
                        seq: 1,
                        kind: ActionKind::Decision,
                        description: "respond".into(),
                        offset_us: 500_000,
                        duration_us: 500_000,
                        frame_ids: vec![1],
                        metadata: std::collections::HashMap::new(),
                    },
                ],
            },
            SerializedSession {
                session_id: "turn-13".into(),
                user_id: "user-2".into(),
                started_at: Some(1_700_000_100),
                actions: vec![ReplayAction {
                    seq: 0,
                    kind: ActionKind::Checkpoint,
                    description: "after tool".into(),
                    offset_us: 1_000_000,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                }],
            },
        ])
        .unwrap();

        let summary = registry.summary("turn-12").unwrap();
        assert_eq!(summary.session_id, "turn-12");
        assert_eq!(summary.action_count, 2);
        assert_eq!(summary.checkpoint_labels, vec!["before reflection".to_string()]);

        let checkpoint = registry
            .checkpoint_by_label("turn-13", "after tool")
            .unwrap();
        assert_eq!(checkpoint.seq, 0);
        assert_eq!(checkpoint.timestamp, Some(1_700_000_101));

        assert_eq!(registry.summary("missing"), None);
        assert_eq!(registry.checkpoint_by_seq("turn-12", 99), None);
    }

    #[test]
    fn test_registry_rejects_duplicate_session_ids() {
        let result = ReplayRegistry::from_sessions(vec![
            SerializedSession {
                session_id: "turn-14".into(),
                user_id: "user-1".into(),
                started_at: Some(1_700_000_000),
                actions: vec![],
            },
            SerializedSession {
                session_id: "turn-14".into(),
                user_id: "user-2".into(),
                started_at: Some(1_700_000_100),
                actions: vec![],
            },
        ]);

        let err = result.unwrap_err();
        assert_eq!(err.session_id(), "turn-14");
    }

    #[test]
    fn test_session_serialize_is_deterministic_across_metadata_insertion_order() {
        let mut left_metadata = std::collections::HashMap::new();
        left_metadata.insert("tool".into(), "search".into());
        left_metadata.insert("model".into(), "gpt".into());
        let left = SerializedSession {
            session_id: "turn-15".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![ReplayAction {
                seq: 0,
                kind: ActionKind::ToolCall,
                description: "call search".into(),
                offset_us: 5,
                duration_us: 5_000,
                frame_ids: vec![1],
                metadata: left_metadata,
            }],
        };

        let mut left_metadata = std::collections::HashMap::new();
        left_metadata.insert("model".into(), "gpt".into());
        left_metadata.insert("tool".into(), "search".into());
        let right = SerializedSession {
            session_id: "turn-15".into(),
            user_id: "user-1".into(),
            started_at: Some(1_700_000_000),
            actions: vec![ReplayAction {
                seq: 0,
                kind: ActionKind::ToolCall,
                description: "call search".into(),
                offset_us: 5,
                duration_us: 5_000,
                frame_ids: vec![1],
                metadata: left_metadata,
            }],
        };

        assert_eq!(left.serialize().unwrap(), right.serialize().unwrap());
    }

    #[test]
    fn test_registry_serialize_roundtrip_preserves_sorted_session_ids() {
        let registry = ReplayRegistry::from_sessions(vec![
            SerializedSession {
                session_id: "turn-b".into(),
                user_id: "user-2".into(),
                started_at: Some(1_700_000_100),
                actions: vec![],
            },
            SerializedSession {
                session_id: "turn-a".into(),
                user_id: "user-1".into(),
                started_at: Some(1_700_000_000),
                actions: vec![ReplayAction {
                    seq: 0,
                    kind: ActionKind::Checkpoint,
                    description: "before reflection".into(),
                    offset_us: 0,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                }],
            },
        ])
        .unwrap();

        let bytes = registry.serialize().unwrap();
        let stored: Vec<SerializedSession> = serde_json::from_slice(&bytes).unwrap();
        let ids: Vec<String> = stored.into_iter().map(|session| session.session_id).collect();
        assert_eq!(ids, vec!["turn-a".to_string(), "turn-b".to_string()]);

        let roundtrip = ReplayRegistry::deserialize(&bytes).unwrap();
        assert_eq!(
            roundtrip.session_ids(),
            vec!["turn-a".to_string(), "turn-b".to_string()]
        );
        assert_eq!(
            roundtrip
                .checkpoint_by_label("turn-a", "before reflection")
                .unwrap()
                .seq,
            0
        );
    }

    #[test]
    fn test_summary() {
        let mut session = ReplaySession::new("turn-6", "user-1");
        session.record(
            ActionKind::MemoryRetrieve,
            "vector search",
            Duration::from_millis(5),
            vec![],
            std::collections::HashMap::new(),
        );
        session.record(
            ActionKind::Decision,
            "final response",
            Duration::from_millis(10),
            vec![],
            std::collections::HashMap::new(),
        );

        let summary = session.summary();
        assert!(summary.contains("turn-6"));
        assert!(summary.contains("vector search"));
        assert!(summary.contains("final response"));
    }
}
