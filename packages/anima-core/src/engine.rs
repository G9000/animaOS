use serde::{Deserialize, Serialize};

use crate::capsule::{
    section_manifest, CapsuleReader, CapsuleWriter, SectionKind, SectionManifestEntry,
};
use crate::cards::{CardStore, MemoryCard, SchemaRegistry};
#[cfg(feature = "temporal")]
use crate::frame::Frame;
use crate::frame::FrameStore;
use crate::graph::KnowledgeGraph;
use crate::integrity::{
    core_stats, scan_card_store, scan_frame_store, CoreStats, IntegrityIssue, IntegrityIssueKind,
    IntegrityReport, IntegritySeverity,
};
use crate::projection::{entity_state_from_cards_and_graph, slot_history, EntityState};
#[cfg(feature = "replay")]
use crate::replay::{ReplayCheckpoint, ReplayRegistry, ReplaySummary};
#[cfg(feature = "temporal")]
use crate::temporal::TemporalIndex;
use crate::path_engine::{EngineOpenMode, EnginePathHandle, ReadWritePathEngineHandle};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct EngineCapsuleMetadata {
    manifest: Vec<SectionManifestEntry>,
}

#[derive(Debug)]
pub struct AnimaEngine {
    frames: FrameStore,
    cards: CardStore,
    graph: KnowledgeGraph,
    #[cfg(feature = "replay")]
    replay_registry: ReplayRegistry,
}

impl AnimaEngine {
    pub fn new() -> Self {
        Self {
            frames: FrameStore::new(),
            cards: CardStore::new(SchemaRegistry::new()),
            graph: KnowledgeGraph::new(),
            #[cfg(feature = "replay")]
            replay_registry: ReplayRegistry::default(),
        }
    }

    pub fn from_parts(frames: FrameStore, cards: CardStore, graph: KnowledgeGraph) -> Self {
        Self {
            frames,
            cards,
            graph,
            #[cfg(feature = "replay")]
            replay_registry: ReplayRegistry::default(),
        }
    }

    #[cfg(feature = "replay")]
    pub fn from_parts_with_replay(
        frames: FrameStore,
        cards: CardStore,
        graph: KnowledgeGraph,
        replay_registry: ReplayRegistry,
    ) -> Self {
        Self {
            frames,
            cards,
            graph,
            replay_registry,
        }
    }

    pub fn frames(&self) -> &FrameStore {
        &self.frames
    }

    pub(crate) fn frames_mut(&mut self) -> &mut FrameStore {
        &mut self.frames
    }

    pub fn cards(&self) -> &CardStore {
        &self.cards
    }

    pub(crate) fn cards_mut(&mut self) -> &mut CardStore {
        &mut self.cards
    }

    pub fn graph(&self) -> &KnowledgeGraph {
        &self.graph
    }

    pub(crate) fn graph_mut(&mut self) -> &mut KnowledgeGraph {
        &mut self.graph
    }

    pub fn entity_state(&self, entity: &str) -> EntityState {
        entity_state_from_cards_and_graph(&self.cards, &self.graph, entity)
    }

    pub fn slot_history(&self, entity: &str, slot: &str) -> Vec<MemoryCard> {
        slot_history(&self.cards, entity, slot)
    }

    pub fn verify(&self) -> IntegrityReport {
        let frame_report = scan_frame_store(&self.frames);
        let card_report = scan_card_store(&self.cards);
        let mut issues = frame_report.issues;
        issues.extend(card_report.issues);
        if !self.graph.is_empty() {
            issues.push(IntegrityIssue {
                kind: IntegrityIssueKind::OrphanedGraphEdge,
                severity: IntegritySeverity::Warning,
                message: "graph integrity not validated: no graph integrity helper exists yet"
                    .into(),
                record_ids: Vec::new(),
                repair_hint: Some(
                    "treat graph stats as inventory only until a dedicated graph integrity scan exists"
                        .into(),
                ),
            });
        }

        let mut stats = core_stats(&self.frames, &self.cards);
        let graph_stats = self.graph.stats();
        stats.graph_node_count = graph_stats.node_count;
        stats.graph_edge_count = graph_stats.edge_count;

        IntegrityReport {
            ok: issues.is_empty(),
            issues,
            stats,
        }
    }

    pub fn stats(&self) -> CoreStats {
        let mut stats = core_stats(&self.frames, &self.cards);
        let graph_stats = self.graph.stats();
        stats.graph_node_count = graph_stats.node_count;
        stats.graph_edge_count = graph_stats.edge_count;
        stats
    }

    pub fn create_path(
        path: impl AsRef<std::path::Path>,
    ) -> crate::Result<ReadWritePathEngineHandle> {
        crate::path_engine::create_path(path)
    }

    pub fn open_path(
        path: impl AsRef<std::path::Path>,
        mode: EngineOpenMode,
    ) -> crate::Result<EnginePathHandle> {
        crate::path_engine::open_path(path, mode)
    }

    pub fn write_capsule(&self, password: Option<&[u8]>) -> crate::Result<Vec<u8>> {
        #[cfg(feature = "encryption")]
        let mut writer = if let Some(password) = password {
            CapsuleWriter::new().with_password(password)
        } else {
            CapsuleWriter::new()
        };

        #[cfg(not(feature = "encryption"))]
        let mut writer = {
            if password.is_some() {
                return Err(crate::Error::Encryption(
                    "anima-core was built without capsule encryption support".into(),
                ));
            }
            CapsuleWriter::new()
        };

        writer.add_section(SectionKind::Frames, self.frames.serialize()?);
        if !self.cards.is_empty() {
            writer.add_section(SectionKind::Cards, self.cards.serialize()?);
        }
        if !self.graph.is_empty() {
            writer.add_section(SectionKind::Graph, self.graph.serialize()?);
        }
        writer.add_section(
            SectionKind::Metadata,
            serde_json::to_vec(&EngineCapsuleMetadata {
                manifest: self.capsule_manifest(),
            })
            .map_err(|e| crate::Error::Serialization(e.to_string()))?,
        );

        writer.write()
    }

    pub fn read_capsule(raw: Vec<u8>, password: Option<&[u8]>) -> crate::Result<Self> {
        let reader = CapsuleReader::open(raw, password)?;
        let sections = reader.sections();
        if !sections.contains(&SectionKind::Frames) {
            return Err(crate::Error::Capsule(
                "capsule missing Frames section".into(),
            ));
        }

        let frames = FrameStore::deserialize(&reader.read_section(SectionKind::Frames)?)?;
        let cards = if sections.contains(&SectionKind::Cards) {
            CardStore::deserialize(&reader.read_section(SectionKind::Cards)?, SchemaRegistry::new())?
        } else {
            CardStore::new(SchemaRegistry::new())
        };
        let graph = if sections.contains(&SectionKind::Graph) {
            KnowledgeGraph::deserialize(&reader.read_section(SectionKind::Graph)?)?
        } else {
            KnowledgeGraph::new()
        };

        if sections.contains(&SectionKind::Metadata) {
            let _metadata: EngineCapsuleMetadata =
                serde_json::from_slice(&reader.read_section(SectionKind::Metadata)?)
                    .map_err(|e| crate::Error::Serialization(e.to_string()))?;
        }

        Ok(Self {
            frames,
            cards,
            graph,
            #[cfg(feature = "replay")]
            replay_registry: ReplayRegistry::default(),
        })
    }

    pub fn capsule_manifest(&self) -> Vec<SectionManifestEntry> {
        let mut sections = vec![SectionKind::Frames];
        if !self.cards.is_empty() {
            sections.push(SectionKind::Cards);
        }
        if !self.graph.is_empty() {
            sections.push(SectionKind::Graph);
        }
        sections.push(SectionKind::Metadata);
        section_manifest(&sections)
    }

    #[cfg(feature = "temporal")]
    pub fn temporal_index(&self) -> TemporalIndex {
        TemporalIndex::from_store(&self.frames)
    }

    #[cfg(feature = "temporal")]
    pub fn temporal_range(
        &self,
        start: Option<i64>,
        end: Option<i64>,
        limit: Option<usize>,
    ) -> Vec<&Frame> {
        self.temporal_index().range(&self.frames, start, end, limit)
    }

    #[cfg(feature = "temporal")]
    pub fn temporal_as_of(&self, timestamp: i64, limit: Option<usize>) -> Vec<&Frame> {
        self.temporal_index().as_of(&self.frames, timestamp, limit)
    }

    #[cfg(feature = "replay")]
    pub fn replay_session_ids(&self) -> Vec<String> {
        self.replay_registry.session_ids()
    }

    #[cfg(feature = "replay")]
    pub fn replay_session_summary(&self, session_id: &str) -> Option<ReplaySummary> {
        self.replay_registry.summary(session_id)
    }

    #[cfg(feature = "replay")]
    pub fn replay_checkpoint_by_label(
        &self,
        session_id: &str,
        label: &str,
    ) -> Option<ReplayCheckpoint> {
        self.replay_registry.checkpoint_by_label(session_id, label)
    }
}

#[cfg(test)]
mod tests {
    use super::AnimaEngine;
    use crate::capsule::{
        CapsuleReader, SectionKind, SectionManifestEntry, SectionStorageClass,
    };
    use crate::cards::{CardStore, MemoryCard, MemoryKind, Polarity, SchemaRegistry, VersionRelation};
    use crate::frame::{Frame, FrameKind, FrameSource, FrameStore};
    use crate::graph::{EntityKind, KnowledgeGraph};
    use crate::path_engine::{EngineOpenMode, EnginePathHandle};
    #[cfg(feature = "replay")]
    use crate::replay::{ActionKind, ReplayAction, ReplayRegistry, SerializedSession};

    fn make_card(
        entity: &str,
        slot: &str,
        value: &str,
        version: VersionRelation,
        frame_id: u64,
    ) -> MemoryCard {
        MemoryCard {
            id: 0,
            kind: MemoryKind::Fact,
            entity: entity.into(),
            slot: slot.into(),
            value: value.into(),
            polarity: Polarity::Neutral,
            version,
            confidence: 1.0,
            frame_id,
            created_at: frame_id as i64,
            active: true,
            superseded_by: None,
        }
    }

    #[test]
    fn engine_new_starts_empty_and_reports_zero_stats() {
        let engine = AnimaEngine::new();

        let stats = engine.stats();
        assert_eq!(stats.frame_count, 0);
        assert_eq!(stats.card_count, 0);
        assert_eq!(stats.graph_node_count, 0);
        assert_eq!(stats.graph_edge_count, 0);
        assert!(engine.entity_state("missing").slots.is_empty());

        let report = engine.verify();
        assert!(report.ok);
        assert!(report.issues.is_empty());
        assert_eq!(report.stats, stats);
    }

    #[test]
    fn engine_from_parts_exposes_projection_and_history_queries() {
        let mut frames = FrameStore::new();
        let mut cards = CardStore::new(SchemaRegistry::new());
        let mut graph = KnowledgeGraph::new();

        let frame_id = frames.insert(Frame::new(
            0,
            FrameKind::Fact,
            "user works at OpenAI".into(),
            "user".into(),
            FrameSource::Extraction,
        ));

        cards.put(make_card(
            "user",
            "employer",
            "OpenAI",
            VersionRelation::Sets,
            frame_id,
        ));
        cards.put(make_card(
            "user",
            "likes",
            "coffee",
            VersionRelation::Sets,
            frame_id,
        ));

        graph.upsert_node("user", EntityKind::Person, 1.0, frame_id).unwrap();
        graph.upsert_node("OpenAI", EntityKind::Organization, 1.0, frame_id).unwrap();
        let from = graph.get_by_name("user").unwrap().id;
        let to = graph.get_by_name("OpenAI").unwrap().id;
        graph
            .upsert_edge(from, to, "employer", 1.0, frame_id)
            .unwrap();

        let engine = AnimaEngine::from_parts(frames, cards, graph);

        let state = engine.entity_state("user");
        assert_eq!(state.entity, "user");
        assert_eq!(state.slots.len(), 2);
        assert_eq!(state.connected_entities.len(), 1);
        assert_eq!(engine.slot_history("user", "employer").len(), 1);

        let stats = engine.stats();
        assert_eq!(stats.frame_count, 1);
        assert_eq!(stats.card_count, 2);
        assert_eq!(stats.graph_node_count, 2);
        assert_eq!(stats.graph_edge_count, 1);

        let report = engine.verify();
        assert!(!report.ok);
        assert_eq!(report.stats, stats);
        assert_eq!(report.issues.len(), 1);
        assert!(report.issues[0].message.contains("graph"));
        assert!(report.issues[0].message.contains("not validated"));
    }

    #[test]
    fn writable_handle_can_get_mutable_engine_access() {
        let mut engine = AnimaEngine::new();

        let frame_id = engine.frames_mut().insert(Frame::new(
            0,
            FrameKind::Fact,
            "user works at OpenAI".into(),
            "user".into(),
            FrameSource::Extraction,
        ));

        engine.cards_mut().put(make_card(
            "user",
            "employer",
            "OpenAI",
            VersionRelation::Sets,
            frame_id,
        ));

        engine
            .graph_mut()
            .upsert_node("user", EntityKind::Person, 1.0, frame_id)
            .unwrap();
        engine
            .graph_mut()
            .upsert_node("OpenAI", EntityKind::Organization, 1.0, frame_id)
            .unwrap();
        let from = engine.graph().get_by_name("user").unwrap().id;
        let to = engine.graph().get_by_name("OpenAI").unwrap().id;
        engine
            .graph_mut()
            .upsert_edge(from, to, "employer", 1.0, frame_id)
            .unwrap();

        assert_eq!(engine.frames().len(), 1);
        assert_eq!(engine.cards().len(), 1);
        assert_eq!(engine.graph().stats().node_count, 2);
        assert_eq!(engine.graph().stats().edge_count, 1);
    }

    #[test]
    fn engine_create_path_returns_writable_handle_with_empty_engine() {
        let tempdir = tempfile::tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");

        let handle = AnimaEngine::create_path(&engine_dir).unwrap();

        assert!(handle.engine().frames().is_empty());
        assert!(handle.engine().cards().is_empty());
        assert!(handle.engine().graph().is_empty());
    }

    #[test]
    fn engine_open_path_read_only_loads_committed_snapshot() {
        let tempdir = tempfile::tempdir().unwrap();
        let engine_dir = tempdir.path().join("engine");

        let mut writer = AnimaEngine::create_path(&engine_dir).unwrap();
        let frame_id = writer.engine_mut().frames_mut().insert(Frame::new(
            0,
            FrameKind::Fact,
            "user works at OpenAI".into(),
            "user".into(),
            FrameSource::Extraction,
        ));
        writer.engine_mut().cards_mut().put(make_card(
            "user",
            "employer",
            "OpenAI",
            VersionRelation::Sets,
            frame_id,
        ));
        writer
            .engine_mut()
            .graph_mut()
            .upsert_node("user", EntityKind::Person, 1.0, frame_id)
            .unwrap();
        writer
            .engine_mut()
            .graph_mut()
            .upsert_node("OpenAI", EntityKind::Organization, 1.0, frame_id)
            .unwrap();
        let from = writer.engine().graph().get_by_name("user").unwrap().id;
        let to = writer.engine().graph().get_by_name("OpenAI").unwrap().id;
        writer
            .engine_mut()
            .graph_mut()
            .upsert_edge(from, to, "employer", 1.0, frame_id)
            .unwrap();
        writer.flush().unwrap();
        drop(writer);

        let opened = AnimaEngine::open_path(&engine_dir, EngineOpenMode::ReadOnly).unwrap();

        match opened {
            EnginePathHandle::ReadOnly(handle) => {
                assert_eq!(handle.engine().frames().len(), 1);
                assert_eq!(handle.engine().cards().len(), 1);
                assert_eq!(handle.engine().graph().stats().node_count, 2);
                assert_eq!(handle.engine().graph().stats().edge_count, 1);
                assert_eq!(
                    handle.engine().frames().iter().next().unwrap().content,
                    "user works at OpenAI"
                );
                assert_eq!(
                    handle.engine().cards().get_current("user", "employer")[0].value,
                    "OpenAI"
                );
            }
            _ => panic!("expected read-only handle"),
        }
    }

    #[cfg(feature = "temporal")]
    #[test]
    fn engine_temporal_queries_use_indexed_frame_ordering() {
        let mut frames = FrameStore::new();
        let cards = CardStore::new(SchemaRegistry::new());
        let graph = KnowledgeGraph::new();

        let early = frames.insert(
            Frame::new(
                0,
                FrameKind::Fact,
                "early".into(),
                "user".into(),
                FrameSource::Extraction,
            )
            .with_timestamp(1_000),
        );
        let middle = frames.insert(
            Frame::new(
                0,
                FrameKind::Fact,
                "middle".into(),
                "user".into(),
                FrameSource::Extraction,
            )
            .with_timestamp(2_000),
        );
        let late = frames.insert(
            Frame::new(
                0,
                FrameKind::Fact,
                "late".into(),
                "user".into(),
                FrameSource::Extraction,
            )
            .with_timestamp(3_000),
        );

        let engine = AnimaEngine::from_parts(frames, cards, graph);

        let index = engine.temporal_index();
        assert_eq!(index.len(), 3);

        let range = engine.temporal_range(None, None, None);
        assert_eq!(range.iter().map(|frame| frame.id).collect::<Vec<_>>(), vec![late, middle, early]);

        let bounded = engine.temporal_range(Some(1_500), Some(3_000), Some(2));
        assert_eq!(bounded.iter().map(|frame| frame.id).collect::<Vec<_>>(), vec![late, middle]);

        let as_of = engine.temporal_as_of(2_000, Some(2));
        assert_eq!(as_of.iter().map(|frame| frame.id).collect::<Vec<_>>(), vec![middle, early]);
    }

    #[cfg(feature = "replay")]
    #[test]
    fn engine_replay_queries_expose_session_summary_and_checkpoint_lookup() {
        let frames = FrameStore::new();
        let cards = CardStore::new(SchemaRegistry::new());
        let graph = KnowledgeGraph::new();
        let replay_registry = ReplayRegistry::from_sessions(vec![SerializedSession {
            session_id: "turn-42".into(),
            user_id: "user-9".into(),
            started_at: Some(1_700_000_000),
            actions: vec![
                ReplayAction {
                    seq: 0,
                    kind: ActionKind::ToolCall,
                    description: "search memory".into(),
                    offset_us: 10,
                    duration_us: 20,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
                ReplayAction {
                    seq: 1,
                    kind: ActionKind::Checkpoint,
                    description: "before response".into(),
                    offset_us: 30,
                    duration_us: 0,
                    frame_ids: vec![],
                    metadata: std::collections::HashMap::new(),
                },
            ],
        }])
        .unwrap();
        let engine = AnimaEngine::from_parts_with_replay(frames, cards, graph, replay_registry);

        assert_eq!(engine.replay_session_ids(), vec!["turn-42".to_string()]);

        let summary = engine.replay_session_summary("turn-42").unwrap();
        assert_eq!(summary.session_id, "turn-42");
        assert_eq!(summary.user_id, "user-9");
        assert_eq!(summary.action_count, 2);
        assert_eq!(summary.checkpoint_count, 1);
        assert_eq!(summary.checkpoint_labels, vec!["before response".to_string()]);

        let checkpoint = engine
            .replay_checkpoint_by_label("turn-42", "before response")
            .unwrap();
        assert_eq!(checkpoint.seq, 1);
        assert_eq!(checkpoint.label, "before response");
        assert_eq!(checkpoint.timestamp, Some(1_700_000_000));
        assert_eq!(engine.replay_session_summary("missing"), None);
        assert_eq!(engine.replay_checkpoint_by_label("turn-42", "missing"), None);
    }

    #[test]
    fn engine_capsule_roundtrip_preserves_frames_and_derived_manifest() {
        let mut frames = FrameStore::new();
        let mut cards = CardStore::new(SchemaRegistry::new());
        let mut graph = KnowledgeGraph::new();

        let frame_id = frames.insert(
            Frame::new(
                0,
                FrameKind::Fact,
                "user works at OpenAI".into(),
                "user".into(),
                FrameSource::Extraction,
            )
            .with_timestamp(1_700_000_000),
        );

        cards.put(make_card(
            "user",
            "employer",
            "OpenAI",
            VersionRelation::Sets,
            frame_id,
        ));

        graph.upsert_node("user", EntityKind::Person, 1.0, frame_id).unwrap();
        graph.upsert_node("OpenAI", EntityKind::Organization, 1.0, frame_id).unwrap();
        let from = graph.get_by_name("user").unwrap().id;
        let to = graph.get_by_name("OpenAI").unwrap().id;
        graph
            .upsert_edge(from, to, "employer", 1.0, frame_id)
            .unwrap();

        let engine = AnimaEngine::from_parts(frames, cards, graph);

        assert_eq!(
            engine.capsule_manifest(),
            vec![
                SectionManifestEntry {
                    kind: SectionKind::Frames,
                    storage_class: SectionStorageClass::Canonical,
                },
                SectionManifestEntry {
                    kind: SectionKind::Cards,
                    storage_class: SectionStorageClass::Derived,
                },
                SectionManifestEntry {
                    kind: SectionKind::Graph,
                    storage_class: SectionStorageClass::Derived,
                },
                SectionManifestEntry {
                    kind: SectionKind::Metadata,
                    storage_class: SectionStorageClass::Derived,
                },
            ]
        );

        let capsule = engine.write_capsule(None).unwrap();
        let reader = CapsuleReader::open(capsule.clone(), None).unwrap();
        assert_eq!(
            reader.sections(),
            vec![
                SectionKind::Frames,
                SectionKind::Cards,
                SectionKind::Graph,
                SectionKind::Metadata,
            ]
        );

        let restored = AnimaEngine::read_capsule(capsule, None).unwrap();
        assert_eq!(restored.frames().len(), 1);
        assert_eq!(restored.frames().iter().next().unwrap().content, "user works at OpenAI");
        assert_eq!(restored.cards().len(), 1);
        assert_eq!(restored.graph().stats().edge_count, 1);
        assert_eq!(restored.capsule_manifest(), engine.capsule_manifest());
    }

    #[test]
    fn engine_read_capsule_allows_missing_derived_sections() {
        let mut frames = FrameStore::new();
        let frame_id = frames.insert(
            Frame::new(
                0,
                FrameKind::Fact,
                "frame only".into(),
                "user".into(),
                FrameSource::Extraction,
            )
            .with_timestamp(1_700_000_000),
        );

        let mut writer = crate::capsule::CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, frames.serialize().unwrap());
        writer.add_section(
            SectionKind::Metadata,
            serde_json::to_vec(&super::EngineCapsuleMetadata {
                manifest: vec![
                    SectionManifestEntry {
                        kind: SectionKind::Frames,
                        storage_class: SectionStorageClass::Canonical,
                    },
                    SectionManifestEntry {
                        kind: SectionKind::Metadata,
                        storage_class: SectionStorageClass::Derived,
                    },
                ],
            })
            .unwrap(),
        );

        let restored = AnimaEngine::read_capsule(writer.write().unwrap(), None).unwrap();
        assert_eq!(restored.frames().len(), 1);
        assert_eq!(restored.frames().get(frame_id).unwrap().content, "frame only");
        assert!(restored.cards().is_empty());
        assert!(restored.graph().is_empty());
    }

    #[test]
    fn engine_read_capsule_does_not_treat_broken_derived_sections_as_missing() {
        let mut frames = FrameStore::new();
        frames.insert(
            Frame::new(
                0,
                FrameKind::Fact,
                "frame plus broken cards".into(),
                "user".into(),
                FrameSource::Extraction,
            )
            .with_timestamp(1_700_000_000),
        );

        let mut writer = crate::capsule::CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, frames.serialize().unwrap());
        writer.add_section(SectionKind::Cards, b"not valid card store bytes".to_vec());
        writer.add_section(
            SectionKind::Metadata,
            serde_json::to_vec(&super::EngineCapsuleMetadata {
                manifest: vec![
                    SectionManifestEntry {
                        kind: SectionKind::Frames,
                        storage_class: SectionStorageClass::Canonical,
                    },
                    SectionManifestEntry {
                        kind: SectionKind::Cards,
                        storage_class: SectionStorageClass::Derived,
                    },
                    SectionManifestEntry {
                        kind: SectionKind::Metadata,
                        storage_class: SectionStorageClass::Derived,
                    },
                ],
            })
            .unwrap(),
        );

        let err = AnimaEngine::read_capsule(writer.write().unwrap(), None).unwrap_err();
        assert!(
            err.to_string().contains("serialization error")
                || err.to_string().contains("invalid")
                || err.to_string().contains("EOF")
        );
    }
}
