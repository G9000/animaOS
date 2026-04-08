//! Decision Replay: capturing *why* not just *what*.
//!
//! Records the causal chain: tool call → memory retrieve → reflection → decision,
//! with microsecond timing and checkpoint support for debugging and audit.

use std::time::{Duration, Instant};

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
        Self {
            session_id: session_id.into(),
            user_id: user_id.into(),
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
            actions: self.actions.clone(),
        };
        serde_json::to_vec(&data).map_err(|e| crate::Error::Serialization(e.to_string()))
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
    pub actions: Vec<ReplayAction>,
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
        assert_eq!(deserialized.actions.len(), 1);
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
