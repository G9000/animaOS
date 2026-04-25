//! PyO3 bindings exposing anima-core to Python.
//!
//! Provides zero-overhead native imports for animaOS's Python layer.
//! Build with: `maturin develop --features python`

#[cfg(feature = "python")]
mod python {
    use pyo3::prelude::*;
    use pyo3::types::{PyDict, PyList};
    use pyo3::IntoPy;
    use serde_json::{json, Value};
    use std::collections::HashMap;
    use std::path::Path;

    use crate::cards::{
        CardStore, Cardinality, MemoryCard, MemoryKind, Polarity, SchemaRegistry, VersionRelation,
    };
    use crate::frame::{Frame, FrameKind, FrameSource, FrameStore};
    use crate::graph::{EntityKind, KnowledgeGraph};
    use crate::integrity::{
        scan_frame_store, verify_capsule_integrity, CapsuleIntegrityReport, CoreStats,
        IntegrityReport,
    };
    use crate::search::{HeatMeta, HeatParams};
    use crate::temporal::TemporalIndex;

    fn json_value_to_py(py: Python<'_>, value: Value) -> PyResult<PyObject> {
        match value {
            Value::Null => Ok(py.None()),
            Value::Bool(value) => Ok(value.into_py(py)),
            Value::Number(value) => {
                if let Some(value) = value.as_i64() {
                    Ok(value.into_py(py))
                } else if let Some(value) = value.as_u64() {
                    Ok(value.into_py(py))
                } else if let Some(value) = value.as_f64() {
                    Ok(value.into_py(py))
                } else {
                    Err(pyo3::exceptions::PyValueError::new_err(
                        "unsupported numeric value",
                    ))
                }
            }
            Value::String(value) => Ok(value.into_py(py)),
            Value::Array(values) => {
                let list = PyList::empty_bound(py);
                for value in values {
                    list.append(json_value_to_py(py, value)?)?;
                }
                Ok(list.into_py(py))
            }
            Value::Object(values) => {
                let dict = PyDict::new_bound(py);
                for (key, value) in values {
                    dict.set_item(key, json_value_to_py(py, value)?)?;
                }
                Ok(dict.into_py(py))
            }
        }
    }

    fn integrity_report_to_py_dict(py: Python<'_>, report: &IntegrityReport) -> PyResult<PyObject> {
        let value = serde_json::to_value(report)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    fn core_stats_to_py_dict(py: Python<'_>, stats: &CoreStats) -> PyResult<PyObject> {
        let value = serde_json::to_value(stats)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    fn capsule_report_to_py_dict(
        py: Python<'_>,
        report: &CapsuleIntegrityReport,
    ) -> PyResult<PyObject> {
        let value = serde_json::to_value(report)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    fn parse_frame_kind(kind: &str) -> PyResult<FrameKind> {
        match kind {
            "fact" => Ok(FrameKind::Fact),
            "preference" => Ok(FrameKind::Preference),
            "goal" => Ok(FrameKind::Goal),
            "relationship" => Ok(FrameKind::Relationship),
            "episode" => Ok(FrameKind::Episode),
            "claim" => Ok(FrameKind::Claim),
            "emotional_signal" => Ok(FrameKind::EmotionalSignal),
            "self_model" => Ok(FrameKind::SelfModel),
            "kg_node" => Ok(FrameKind::KgNode),
            "kg_edge" => Ok(FrameKind::KgEdge),
            "focus" => Ok(FrameKind::Focus),
            "daily_log" => Ok(FrameKind::DailyLog),
            "growth_log" => Ok(FrameKind::GrowthLog),
            "identity" => Ok(FrameKind::Identity),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown frame kind: {other}"
            ))),
        }
    }

    fn parse_memory_kind(kind: &str) -> PyResult<MemoryKind> {
        match kind.to_lowercase().as_str() {
            "fact" => Ok(MemoryKind::Fact),
            "preference" => Ok(MemoryKind::Preference),
            "event" => Ok(MemoryKind::Event),
            "profile" => Ok(MemoryKind::Profile),
            "relationship" => Ok(MemoryKind::Relationship),
            "goal" => Ok(MemoryKind::Goal),
            "other" => Ok(MemoryKind::Other),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown memory kind: {other}"
            ))),
        }
    }

    fn parse_version_relation(version: &str) -> PyResult<VersionRelation> {
        match version.to_lowercase().as_str() {
            "sets" => Ok(VersionRelation::Sets),
            "updates" => Ok(VersionRelation::Updates),
            "extends" => Ok(VersionRelation::Extends),
            "retracts" => Ok(VersionRelation::Retracts),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown version relation: {other}"
            ))),
        }
    }

    fn parse_entity_kind(kind: &str) -> PyResult<EntityKind> {
        match kind.to_lowercase().as_str() {
            "person" => Ok(EntityKind::Person),
            "organization" => Ok(EntityKind::Organization),
            "project" => Ok(EntityKind::Project),
            "location" => Ok(EntityKind::Location),
            "event" => Ok(EntityKind::Event),
            "product" => Ok(EntityKind::Product),
            "email" => Ok(EntityKind::Email),
            "date" => Ok(EntityKind::Date),
            "url" => Ok(EntityKind::Url),
            "other" => Ok(EntityKind::Other),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown entity kind: {other}"
            ))),
        }
    }

    // ── Frame Bindings ───────────────────────────────────────────────

    #[pyclass(name = "Frame")]
    #[derive(Clone)]
    struct PyFrame {
        inner: Frame,
    }

    #[pymethods]
    impl PyFrame {
        #[new]
        #[pyo3(signature = (kind, content, user_id))]
        fn new(kind: &str, content: String, user_id: String) -> PyResult<Self> {
            let fk = parse_frame_kind(kind)?;
            Ok(Self {
                inner: Frame::new(0, fk, content, user_id, FrameSource::Api),
            })
        }

        #[getter]
        fn id(&self) -> u64 {
            self.inner.id
        }

        #[getter]
        fn kind(&self) -> String {
            self.inner.kind.as_str().to_string()
        }

        #[getter]
        fn content(&self) -> &str {
            &self.inner.content
        }

        #[getter]
        fn timestamp(&self) -> i64 {
            self.inner.timestamp
        }

        #[getter]
        fn user_id(&self) -> &str {
            &self.inner.user_id
        }

        #[getter]
        fn checksum(&self) -> String {
            hex::encode(self.inner.checksum)
        }

        fn verify_checksum(&self) -> bool {
            self.inner.verify_checksum()
        }

        fn to_json(&self) -> PyResult<String> {
            serde_json::to_string(&self.inner)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
        }
    }

    // ── FrameStore Bindings ──────────────────────────────────────────

    #[pyclass(name = "FrameStore")]
    struct PyFrameStore {
        inner: FrameStore,
    }

    #[pyclass(name = "TemporalIndex")]
    struct PyTemporalIndex {
        inner: TemporalIndex,
        store: FrameStore,
    }

    #[pyclass(name = "Engine")]
    struct PyAnimaEngine {
        inner: crate::engine::AnimaEngine,
    }

    #[pymethods]
    impl PyFrameStore {
        #[new]
        fn new() -> Self {
            Self {
                inner: FrameStore::new(),
            }
        }

        fn insert(&mut self, frame: &PyFrame) -> u64 {
            self.inner.insert(frame.inner.clone())
        }

        fn get(&self, id: u64) -> Option<PyFrame> {
            self.inner.get(id).map(|f| PyFrame { inner: f.clone() })
        }

        fn len(&self) -> usize {
            self.inner.len()
        }

        fn temporal_index(&self) -> PyTemporalIndex {
            PyTemporalIndex {
                inner: TemporalIndex::from_store(&self.inner),
                store: self.inner.clone(),
            }
        }

        #[pyo3(signature = (start = None, end = None, limit = None))]
        fn temporal_range(
            &self,
            start: Option<i64>,
            end: Option<i64>,
            limit: Option<usize>,
        ) -> Vec<PyFrame> {
            let index = TemporalIndex::from_store(&self.inner);
            index
                .range(&self.inner, start, end, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (timestamp, limit = None))]
        fn temporal_as_of(&self, timestamp: i64, limit: Option<usize>) -> Vec<PyFrame> {
            let index = TemporalIndex::from_store(&self.inner);
            index
                .as_of(&self.inner, timestamp, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (session_data, padding_before_secs = 0, padding_after_secs = 0, limit = None))]
        fn temporal_session_window(
            &self,
            session_data: Vec<u8>,
            padding_before_secs: i64,
            padding_after_secs: i64,
            limit: Option<usize>,
        ) -> PyResult<Vec<PyFrame>> {
            let session = crate::replay::ReplaySession::deserialize(&session_data)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            let index = TemporalIndex::from_store(&self.inner);
            Ok(index
                .session_window(
                    &self.inner,
                    &session,
                    padding_before_secs,
                    padding_after_secs,
                    limit,
                )
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect())
        }
    }

    #[pymethods]
    impl PyTemporalIndex {
        fn len(&self) -> usize {
            self.inner.len()
        }

        fn is_empty(&self) -> bool {
            self.inner.is_empty()
        }

        #[pyo3(signature = (start = None, end = None, limit = None))]
        fn range(
            &self,
            start: Option<i64>,
            end: Option<i64>,
            limit: Option<usize>,
        ) -> Vec<PyFrame> {
            self.inner
                .range(&self.store, start, end, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (timestamp, limit = None))]
        fn as_of(&self, timestamp: i64, limit: Option<usize>) -> Vec<PyFrame> {
            self.inner
                .as_of(&self.store, timestamp, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (session_data, padding_before_secs = 0, padding_after_secs = 0, limit = None))]
        fn session_window(
            &self,
            session_data: Vec<u8>,
            padding_before_secs: i64,
            padding_after_secs: i64,
            limit: Option<usize>,
        ) -> PyResult<Vec<PyFrame>> {
            let session = crate::replay::ReplaySession::deserialize(&session_data)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            Ok(self
                .inner
                .session_window(
                    &self.store,
                    &session,
                    padding_before_secs,
                    padding_after_secs,
                    limit,
                )
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect())
        }
    }

    #[pymethods]
    impl PyAnimaEngine {
        #[new]
        fn new() -> Self {
            Self {
                inner: crate::engine::AnimaEngine::new(),
            }
        }

        #[staticmethod]
        #[pyo3(signature = (data, password = None))]
        fn from_capsule_bytes(data: Vec<u8>, password: Option<Vec<u8>>) -> PyResult<Self> {
            let inner = crate::engine::AnimaEngine::read_capsule(data, password.as_deref())
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            Ok(Self { inner })
        }

        #[pyo3(signature = (password = None))]
        fn to_capsule_bytes(&self, password: Option<Vec<u8>>) -> PyResult<Vec<u8>> {
            self.inner
                .write_capsule(password.as_deref())
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
        }

        fn verify(&self, py: Python<'_>) -> PyResult<PyObject> {
            integrity_report_to_py_dict(py, &self.inner.verify())
        }

        fn stats(&self, py: Python<'_>) -> PyResult<PyObject> {
            core_stats_to_py_dict(py, &self.inner.stats())
        }

        fn project_entity_state(&self, py: Python<'_>, entity: &str) -> PyResult<PyObject> {
            let value = serde_json::to_value(self.inner.entity_state(entity))
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            json_value_to_py(py, value)
        }

        fn project_slot_history(
            &self,
            py: Python<'_>,
            entity: &str,
            slot: &str,
        ) -> PyResult<PyObject> {
            let value = serde_json::to_value(self.inner.slot_history(entity, slot))
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            json_value_to_py(py, value)
        }

        #[pyo3(signature = (start = None, end = None, limit = None))]
        fn temporal_range(
            &self,
            py: Python<'_>,
            start: Option<i64>,
            end: Option<i64>,
            limit: Option<usize>,
        ) -> PyResult<PyObject> {
            let value = serde_json::to_value(self.inner.temporal_range(start, end, limit))
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            json_value_to_py(py, value)
        }
    }

    // ── SIMD distance functions ──────────────────────────────────────

    #[pyfunction]
    fn l2_distance(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
        if a.len() != b.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "vectors must have same length",
            ));
        }
        Ok(crate::simd::l2_distance(&a, &b))
    }

    #[pyfunction]
    fn cosine_similarity(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
        if a.len() != b.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "vectors must have same length",
            ));
        }
        Ok(crate::simd::cosine_similarity(&a, &b))
    }

    #[pyfunction]
    fn cosine_distance(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
        if a.len() != b.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "vectors must have same length",
            ));
        }
        Ok(crate::simd::cosine_distance(&a, &b))
    }

    #[pyfunction]
    fn normalize_scores(scores: Vec<f32>) -> Vec<f32> {
        crate::adaptive::normalize_scores(&scores)
    }

    #[pyfunction]
    #[pyo3(signature = (scores, strategy = "combined", min_results = 1, max_results = 100, normalize = true, absolute_min = 0.3, relative_threshold = 0.5, max_drop_ratio = 0.4, sensitivity = 1.0))]
    fn find_adaptive_cutoff(
        scores: Vec<f32>,
        strategy: &str,
        min_results: usize,
        max_results: usize,
        normalize: bool,
        absolute_min: f32,
        relative_threshold: f32,
        max_drop_ratio: f32,
        sensitivity: f32,
    ) -> PyResult<(usize, String, Vec<f32>)> {
        let mut config = crate::adaptive::AdaptiveConfig {
            max_results,
            min_results,
            normalize_scores: normalize,
            ..Default::default()
        };

        config.enabled = strategy != "disabled";
        config.strategy = match strategy {
            "absolute_threshold" => crate::adaptive::CutoffStrategy::AbsoluteThreshold {
                min_score: absolute_min,
            },
            "relative_threshold" => crate::adaptive::CutoffStrategy::RelativeThreshold {
                min_ratio: relative_threshold,
            },
            "score_cliff" => crate::adaptive::CutoffStrategy::ScoreCliff { max_drop_ratio },
            "elbow" => crate::adaptive::CutoffStrategy::Elbow { sensitivity },
            "combined" | "disabled" => crate::adaptive::CutoffStrategy::Combined {
                relative_threshold,
                max_drop_ratio,
                absolute_min,
            },
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown adaptive strategy: {other}"
                )))
            }
        };

        Ok(crate::adaptive::find_adaptive_cutoff(&scores, &config))
    }

    // ── HNSW Bindings ────────────────────────────────────────────────

    #[cfg(feature = "hnsw")]
    mod hnsw_bindings {
        use super::*;
        use crate::hnsw::HnswIndex;

        #[pyclass(name = "HnswIndex")]
        pub struct PyHnswIndex {
            inner: HnswIndex,
        }

        #[pymethods]
        impl PyHnswIndex {
            #[new]
            fn new(dimensions: usize) -> Self {
                Self {
                    inner: HnswIndex::new(dimensions),
                }
            }

            fn insert(&mut self, id: u64, embedding: Vec<f32>) -> PyResult<()> {
                self.inner
                    .insert(id, embedding)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
            }

            fn search(&mut self, query: Vec<f32>, k: usize) -> PyResult<Vec<(u64, f32)>> {
                self.inner
                    .search(&query, k)
                    .map(|results| {
                        results
                            .into_iter()
                            .map(|result| (result.frame_id, result.distance))
                            .collect()
                    })
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
            }

            fn remove(&mut self, id: u64) {
                self.inner.remove(id);
            }

            fn len(&self) -> usize {
                self.inner.len()
            }
        }
    }

    // ── CardStore Bindings ───────────────────────────────────────────

    #[pyclass(name = "CardStore")]
    struct PyCardStore {
        inner: CardStore,
    }

    #[pymethods]
    impl PyCardStore {
        #[new]
        fn new() -> Self {
            Self {
                inner: CardStore::new(SchemaRegistry::new()),
            }
        }

        #[pyo3(signature = (entity, slot, value, kind = "fact", version = "sets", confidence = 1.0, frame_id = 0))]
        fn put(
            &mut self,
            entity: &str,
            slot: &str,
            value: &str,
            kind: &str,
            version: &str,
            confidence: f32,
            frame_id: u64,
        ) -> PyResult<u64> {
            let card = MemoryCard {
                id: 0,
                kind: parse_memory_kind(kind)?,
                entity: entity.into(),
                slot: slot.into(),
                value: value.into(),
                polarity: Polarity::Neutral,
                version: parse_version_relation(version)?,
                confidence,
                frame_id,
                created_at: chrono::Utc::now().timestamp(),
                active: true,
                superseded_by: None,
            };
            Ok(self.inner.put(card))
        }

        fn get_current(&self, entity: &str, slot: &str) -> Vec<String> {
            self.inner
                .get_current(entity, slot)
                .into_iter()
                .map(|c| c.value.clone())
                .collect()
        }

        fn get_history(&self, entity: &str, slot: &str) -> Vec<String> {
            self.inner
                .get_history(entity, slot)
                .into_iter()
                .map(|c| c.value.clone())
                .collect()
        }

        fn len(&self) -> usize {
            self.inner.len()
        }

        fn active_count(&self) -> usize {
            self.inner.active_count()
        }

        fn set_cardinality(&mut self, entity_pattern: &str, slot: &str, multiple: bool) {
            let c = if multiple {
                Cardinality::Multiple
            } else {
                Cardinality::Single
            };
            self.inner.schema.set(entity_pattern, slot, c);
        }
    }

    // ── KnowledgeGraph Bindings ──────────────────────────────────────

    #[pyclass(name = "KnowledgeGraph")]
    struct PyKnowledgeGraph {
        inner: KnowledgeGraph,
    }

    #[pymethods]
    impl PyKnowledgeGraph {
        #[new]
        fn new() -> Self {
            Self {
                inner: KnowledgeGraph::new(),
            }
        }

        fn upsert_node(
            &mut self,
            name: &str,
            kind: &str,
            confidence: f32,
            frame_id: u64,
        ) -> PyResult<u64> {
            self.inner
                .upsert_node(name, parse_entity_kind(kind)?, confidence, frame_id)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        }

        fn upsert_edge(
            &mut self,
            from_node: u64,
            to_node: u64,
            relation_type: &str,
            confidence: f32,
            frame_id: u64,
        ) -> PyResult<()> {
            self.inner
                .upsert_edge(from_node, to_node, relation_type, confidence, frame_id)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        }

        #[pyo3(signature = (start_name, relation_filter = None, max_hops = 2))]
        fn follow(
            &self,
            start_name: &str,
            relation_filter: Option<&str>,
            max_hops: usize,
        ) -> Vec<(String, String, f32, usize)> {
            self.inner
                .follow(start_name, relation_filter, max_hops)
                .into_iter()
                .map(|r| {
                    (
                        r.node_name,
                        r.node_kind.as_str().to_string(),
                        r.confidence,
                        r.path_length,
                    )
                })
                .collect()
        }

        fn node_count(&self) -> usize {
            self.inner.node_count()
        }

        fn edge_count(&self) -> usize {
            self.inner.edge_count()
        }
    }

    // ── Temporal Bindings ────────────────────────────────────────────

    #[pyfunction]
    fn project_entity_state(
        py: Python<'_>,
        cards: &PyCardStore,
        graph: &PyKnowledgeGraph,
        entity: &str,
    ) -> PyResult<PyObject> {
        let state = crate::projection::entity_state_from_cards_and_graph(
            &cards.inner,
            &graph.inner,
            entity,
        );
        let value = serde_json::to_value(&state)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn project_slot_history(
        py: Python<'_>,
        cards: &PyCardStore,
        entity: &str,
        slot: &str,
    ) -> PyResult<PyObject> {
        let history = crate::projection::slot_history(&cards.inner, entity, slot);
        let value = serde_json::to_value(&history)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn parse_temporal(input: &str) -> Option<(String, i64, f32)> {
        let now = chrono::Utc::now();
        crate::temporal::parse_temporal(input, now).map(|m| (m.raw, m.timestamp, m.confidence))
    }

    #[pyfunction]
    fn replay_session_time_bounds(data: Vec<u8>) -> PyResult<Option<(i64, i64)>> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        Ok(session.time_bounds())
    }

    #[pyfunction]
    fn replay_session_checkpoints(py: Python<'_>, data: Vec<u8>) -> PyResult<PyObject> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let checkpoints = session.checkpoints();
        let value = serde_json::to_value(checkpoints)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn replay_session_checkpoint_by_seq(
        py: Python<'_>,
        data: Vec<u8>,
        seq: u32,
    ) -> PyResult<Option<PyObject>> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        session
            .checkpoint_by_seq(seq)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn replay_session_checkpoint_by_label(
        py: Python<'_>,
        data: Vec<u8>,
        label: &str,
    ) -> PyResult<Option<PyObject>> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        session
            .checkpoint_by_label(label)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn compare_replay_sessions(
        py: Python<'_>,
        left: Vec<u8>,
        right: Vec<u8>,
    ) -> PyResult<PyObject> {
        let left_session = crate::replay::ReplaySession::deserialize(&left)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let right_session = crate::replay::ReplaySession::deserialize(&right)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let comparison = left_session.compare(&right_session);
        let value = serde_json::to_value(comparison)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn replay_session_summary(py: Python<'_>, data: Vec<u8>) -> PyResult<PyObject> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let summary = session.structured_summary();
        let value = serde_json::to_value(summary)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    // ── Capsule Bindings ─────────────────────────────────────────────

    fn replay_registry_from_bytes(
        sessions: Vec<Vec<u8>>,
    ) -> PyResult<crate::replay::ReplayRegistry> {
        let sessions = sessions
            .into_iter()
            .map(|data| {
                crate::replay::ReplaySession::deserialize(&data)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
            })
            .collect::<PyResult<Vec<_>>>()?;
        crate::replay::ReplayRegistry::from_sessions(sessions)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn replay_registry_session_summary(
        py: Python<'_>,
        sessions: Vec<Vec<u8>>,
        session_id: &str,
    ) -> PyResult<Option<PyObject>> {
        let registry = replay_registry_from_bytes(sessions)?;
        registry
            .summary(session_id)
            .map(|summary| {
                let value = serde_json::to_value(summary)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn replay_registry_session_ids(sessions: Vec<Vec<u8>>) -> PyResult<Vec<String>> {
        let registry = replay_registry_from_bytes(sessions)?;
        Ok(registry.session_ids())
    }

    #[pyfunction]
    fn replay_registry_checkpoint_by_seq(
        py: Python<'_>,
        sessions: Vec<Vec<u8>>,
        session_id: &str,
        seq: u32,
    ) -> PyResult<Option<PyObject>> {
        let registry = replay_registry_from_bytes(sessions)?;
        registry
            .checkpoint_by_seq(session_id, seq)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn replay_registry_checkpoint_by_label(
        py: Python<'_>,
        sessions: Vec<Vec<u8>>,
        session_id: &str,
        label: &str,
    ) -> PyResult<Option<PyObject>> {
        let registry = replay_registry_from_bytes(sessions)?;
        registry
            .checkpoint_by_label(session_id, label)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    #[pyo3(signature = (sections, password = None))]
    fn write_capsule(
        sections: HashMap<String, Vec<u8>>,
        password: Option<Vec<u8>>,
    ) -> PyResult<Vec<u8>> {
        use crate::capsule::{CapsuleWriter, SectionKind};

        #[cfg(feature = "encryption")]
        let mut writer = if let Some(password) = password {
            CapsuleWriter::new().with_password(password)
        } else {
            CapsuleWriter::new()
        };

        #[cfg(not(feature = "encryption"))]
        let mut writer = {
            if password.is_some() {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "anima_core was built without capsule encryption support",
                ));
            }
            CapsuleWriter::new()
        };

        for (key, data) in sections {
            let kind = match key.as_str() {
                "frames" => SectionKind::Frames,
                "cards" => SectionKind::Cards,
                "graph" => SectionKind::Graph,
                "metadata" => SectionKind::Metadata,
                _ => {
                    return Err(pyo3::exceptions::PyValueError::new_err(format!(
                        "unknown section: {key}"
                    )));
                }
            };
            writer.add_section(kind, data);
        }

        writer
            .write()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    #[pyo3(signature = (data, password = None))]
    fn read_capsule(
        data: Vec<u8>,
        password: Option<Vec<u8>>,
    ) -> PyResult<HashMap<String, Vec<u8>>> {
        use crate::capsule::{CapsuleReader, SectionKind};

        let reader = CapsuleReader::open(data, password.as_deref())
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let mut result = HashMap::new();
        for kind in reader.sections() {
            let key = match kind {
                SectionKind::Frames => "frames",
                SectionKind::Cards => "cards",
                SectionKind::Graph => "graph",
                SectionKind::Metadata => "metadata",
            };
            match reader.read_section(kind) {
                Ok(data) => {
                    result.insert(key.to_string(), data);
                }
                Err(e) => {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string()));
                }
            }
        }

        Ok(result)
    }

    #[pyfunction]
    fn verify_frame_store(py: Python<'_>, store: &PyFrameStore) -> PyResult<PyObject> {
        integrity_report_to_py_dict(py, &scan_frame_store(&store.inner))
    }

    #[pyfunction]
    fn frame_store_stats(py: Python<'_>, store: &PyFrameStore) -> PyResult<PyObject> {
        let report = scan_frame_store(&store.inner);
        core_stats_to_py_dict(py, &report.stats)
    }

    #[pyfunction]
    #[pyo3(signature = (data, password = None))]
    fn verify_capsule_bytes(
        py: Python<'_>,
        data: Vec<u8>,
        password: Option<Vec<u8>>,
    ) -> PyResult<PyObject> {
        let report = verify_capsule_integrity(&data, password.as_deref());
        capsule_report_to_py_dict(py, &report)
    }

    // ── Text Bindings ────────────────────────────────────────────────

    #[pyfunction]
    #[pyo3(signature = (text, limit = 4096))]
    fn normalize_text(text: &str, limit: usize) -> Option<(String, bool)> {
        crate::text::normalize_text(text, limit)
            .map(|normalized| (normalized.text, normalized.truncated))
    }

    #[pyfunction]
    fn truncate_at_grapheme_boundary(text: &str, limit: usize) -> usize {
        crate::text::truncate_at_grapheme_boundary(text, limit)
    }

    #[pyfunction]
    fn fix_pdf_spacing(text: &str) -> String {
        crate::text::fix_pdf_spacing(text)
    }

    #[pyfunction]
    fn extract_triplets(
        text: &str,
    ) -> Vec<(String, String, String, String, String, f32, usize, usize)> {
        crate::triplet::extract_triplets(text)
            .into_iter()
            .map(|triplet| {
                (
                    triplet.subject,
                    triplet.subject_type,
                    triplet.predicate,
                    triplet.object,
                    triplet.object_type,
                    triplet.confidence,
                    triplet.char_start,
                    triplet.char_end,
                )
            })
            .collect()
    }

    // ── Chunker Bindings ─────────────────────────────────────────────

    #[pyclass(name = "ChunkOptions")]
    #[derive(Clone)]
    struct PyChunkOptions {
        inner: crate::chunker::ChunkOptions,
    }

    #[pymethods]
    impl PyChunkOptions {
        #[new]
        #[pyo3(signature = (max_chars = 1200, overlap_chars = 0, preserve_code_blocks = true, preserve_tables = true, include_section_headers = true, preserve_lists = true))]
        fn new(
            max_chars: usize,
            overlap_chars: usize,
            preserve_code_blocks: bool,
            preserve_tables: bool,
            include_section_headers: bool,
            preserve_lists: bool,
        ) -> Self {
            Self {
                inner: crate::chunker::ChunkOptions {
                    max_chars,
                    overlap_chars,
                    preserve_code_blocks,
                    preserve_tables,
                    include_section_headers,
                    preserve_lists,
                },
            }
        }
    }

    #[pyfunction]
    #[pyo3(signature = (text, max_chars = 1200, overlap_chars = 0))]
    fn chunk_text(
        text: &str,
        max_chars: usize,
        overlap_chars: usize,
    ) -> Vec<(String, String, usize, usize, usize)> {
        let opts = crate::chunker::ChunkOptions {
            max_chars,
            overlap_chars,
            ..Default::default()
        };
        crate::chunker::chunk_text(text, &opts)
            .into_iter()
            .map(|c| {
                let type_str = format!("{:?}", c.chunk_type).to_lowercase();
                (c.text, type_str, c.index, c.char_start, c.char_end)
            })
            .collect()
    }

    // ── Enrich Bindings ──────────────────────────────────────────────

    #[pyclass(name = "RulesEngine")]
    struct PyRulesEngine {
        inner: crate::enrich::RulesEngine,
    }

    #[pymethods]
    impl PyRulesEngine {
        #[new]
        #[pyo3(signature = (use_defaults = true))]
        fn new(use_defaults: bool) -> Self {
            Self {
                inner: if use_defaults {
                    crate::enrich::RulesEngine::new()
                } else {
                    crate::enrich::RulesEngine::empty()
                },
            }
        }

        fn add_rule(
            &mut self,
            name: &str,
            pattern: &str,
            kind: &str,
            entity_template: &str,
            slot_template: &str,
            value_template: &str,
        ) -> PyResult<()> {
            let kind = parse_memory_kind(kind)?;
            let rule = crate::enrich::ExtractionRule::new(
                name,
                pattern,
                kind,
                entity_template,
                slot_template,
                value_template,
            )
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("invalid regex pattern"))?;
            self.inner.add_rule(rule);
            Ok(())
        }

        fn rule_count(&self) -> usize {
            self.inner.rule_count()
        }

        /// Returns list of (rule_name, entity, slot, value, kind, confidence, char_start, char_end)
        fn extract(
            &self,
            text: &str,
        ) -> Vec<(String, String, String, String, String, f32, usize, usize)> {
            self.inner
                .extract(text)
                .into_iter()
                .map(|e| {
                    (
                        e.rule_name,
                        e.entity,
                        e.slot,
                        e.value,
                        e.kind.as_str().to_string(),
                        e.confidence,
                        e.char_start,
                        e.char_end,
                    )
                })
                .collect()
        }

        fn extract_above(
            &self,
            text: &str,
            min_confidence: f32,
        ) -> Vec<(String, String, String, String, String, f32, usize, usize)> {
            self.inner
                .extract_above(text, min_confidence)
                .into_iter()
                .map(|e| {
                    (
                        e.rule_name,
                        e.entity,
                        e.slot,
                        e.value,
                        e.kind.as_str().to_string(),
                        e.confidence,
                        e.char_start,
                        e.char_end,
                    )
                })
                .collect()
        }
    }

    // ── Search Bindings ──────────────────────────────────────────────

    #[pyfunction]
    fn rrf_fuse(ranked_lists: Vec<Vec<(u64, f32)>>, k: u32) -> Vec<(u64, f32)> {
        let vec_results = ranked_lists.first().map(Vec::as_slice).unwrap_or(&[]);
        let lex_results = ranked_lists.get(1).map(Vec::as_slice).unwrap_or(&[]);
        crate::search::rrf_fuse(vec_results, lex_results, k as f32)
            .into_iter()
            .map(|(frame_id, score, _vec_rank, _lex_rank)| (frame_id, score))
            .collect()
    }

    #[pyfunction]
    #[pyo3(signature = (access_count, depth, importance, seconds_since_access, superseded = false))]
    fn compute_heat(
        access_count: u32,
        depth: u32,
        importance: f32,
        seconds_since_access: f64,
        superseded: bool,
    ) -> f64 {
        let now = chrono::Utc::now().timestamp();
        let age_seconds = seconds_since_access.max(0.0).round() as i64;
        let meta = HeatMeta {
            access_count,
            interaction_depth: depth as f64,
            importance: importance.round().clamp(0.0, 5.0) as u8,
            last_accessed_at: Some(now - age_seconds),
            is_superseded: superseded,
        };
        crate::search::compute_heat(
            &meta,
            &HeatParams::default(),
            now,
        )
    }

    // ── Module Registration ──────────────────────────────────────────

    #[pyfunction]
    fn retrieval_manifest_status(py: Python<'_>, root: &str) -> PyResult<PyObject> {
        let root = Path::new(root);
        let (exists, corrupt, manifest) = crate::retrieval_index::manifest_status(root)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        json_value_to_py(
            py,
            json!({
                "exists": exists,
                "corrupt": corrupt,
                "version": manifest.version,
                "families": manifest.families,
            }),
        )
    }

    #[pyfunction]
    fn mark_retrieval_index_dirty(root: &str, family: &str) -> PyResult<()> {
        let family = match family {
            "memory" => crate::retrieval_index::IndexFamily::Memory,
            "transcript" => crate::retrieval_index::IndexFamily::Transcript,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown retrieval index family: {other}"
                )))
            }
        };
        crate::retrieval_index::mark_family_dirty(Path::new(root), family)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn clear_retrieval_index_dirty(root: &str, family: &str) -> PyResult<()> {
        let family = match family {
            "memory" => crate::retrieval_index::IndexFamily::Memory,
            "transcript" => crate::retrieval_index::IndexFamily::Transcript,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown retrieval index family: {other}"
                )))
            }
        };
        crate::retrieval_index::clear_family_dirty(Path::new(root), family)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn memory_index_upsert(
        root: &str,
        record_id: u64,
        user_id: u64,
        text: &str,
        source_type: &str,
        category: &str,
        importance: u8,
        created_at: i64,
        embedding: Option<Vec<f32>>,
    ) -> PyResult<()> {
        crate::retrieval_index::upsert_memory_document(
            Path::new(root),
            crate::retrieval_index::MemoryIndexDocument {
                record_id,
                user_id,
                text: text.to_owned(),
                embedding,
                source_type: source_type.to_owned(),
                category: category.to_owned(),
                importance,
                created_at,
            },
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn memory_index_delete(root: &str, record_id: u64, user_id: u64) -> PyResult<bool> {
        crate::retrieval_index::delete_memory_document(Path::new(root), user_id, record_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn memory_index_delete_user_documents(root: &str, user_id: u64) -> PyResult<u64> {
        crate::retrieval_index::delete_memory_documents_for_user(Path::new(root), user_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn reset_memory_index(root: &str) -> PyResult<()> {
        crate::retrieval_index::reset_memory_documents(Path::new(root))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn memory_index_search(
        py: Python<'_>,
        root: &str,
        user_id: u64,
        query: &str,
        limit: usize,
    ) -> PyResult<PyObject> {
        let hits = crate::retrieval_index::search_memory_documents(
            Path::new(root),
            user_id,
            query,
            limit,
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let value = serde_json::to_value(hits)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn memory_index_vector_search(
        py: Python<'_>,
        root: &str,
        user_id: u64,
        query_embedding: Vec<f32>,
        limit: usize,
    ) -> PyResult<PyObject> {
        let hits = crate::retrieval_index::search_memory_documents_by_vector(
            Path::new(root),
            user_id,
            &query_embedding,
            limit,
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let value = serde_json::to_value(hits)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn transcript_index_upsert(
        root: &str,
        thread_id: u64,
        user_id: u64,
        transcript_ref: &str,
        summary: &str,
        keywords: Vec<String>,
        text: &str,
        date_start: i64,
    ) -> PyResult<()> {
        crate::retrieval_index::upsert_transcript_document(
            Path::new(root),
            crate::retrieval_index::TranscriptIndexDocument {
                thread_id,
                user_id,
                transcript_ref: transcript_ref.to_owned(),
                summary: summary.to_owned(),
                keywords,
                text: text.to_owned(),
                date_start,
            },
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn transcript_index_delete(root: &str, thread_id: u64, user_id: u64) -> PyResult<bool> {
        crate::retrieval_index::delete_transcript_document(Path::new(root), user_id, thread_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn transcript_index_delete_user_documents(root: &str, user_id: u64) -> PyResult<u64> {
        crate::retrieval_index::delete_transcript_documents_for_user(Path::new(root), user_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn reset_transcript_index(root: &str) -> PyResult<()> {
        crate::retrieval_index::reset_transcript_documents(Path::new(root))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn transcript_index_search(
        py: Python<'_>,
        root: &str,
        user_id: u64,
        query: &str,
        limit: usize,
    ) -> PyResult<PyObject> {
        let hits = crate::retrieval_index::search_transcript_documents(
            Path::new(root),
            user_id,
            query,
            limit,
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let value = serde_json::to_value(hits)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pymodule]
    #[pyo3(name = "anima_core")]
    pub fn anima_core_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
        // Frame types
        m.add_class::<PyFrame>()?;
        m.add_class::<PyFrameStore>()?;
        m.add_class::<PyTemporalIndex>()?;
        m.add_class::<PyAnimaEngine>()?;

        // SIMD functions
        m.add_function(wrap_pyfunction!(l2_distance, m)?)?;
        m.add_function(wrap_pyfunction!(cosine_similarity, m)?)?;
        m.add_function(wrap_pyfunction!(cosine_distance, m)?)?;
        m.add_function(wrap_pyfunction!(normalize_scores, m)?)?;
        m.add_function(wrap_pyfunction!(find_adaptive_cutoff, m)?)?;

        // HNSW
        #[cfg(feature = "hnsw")]
        m.add_class::<hnsw_bindings::PyHnswIndex>()?;

        // Cards
        m.add_class::<PyCardStore>()?;

        // Knowledge Graph
        m.add_class::<PyKnowledgeGraph>()?;
        m.add_function(wrap_pyfunction!(project_entity_state, m)?)?;
        m.add_function(wrap_pyfunction!(project_slot_history, m)?)?;

        // Temporal
        m.add_function(wrap_pyfunction!(parse_temporal, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_time_bounds, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_checkpoints, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_checkpoint_by_seq, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_checkpoint_by_label, m)?)?;
        m.add_function(wrap_pyfunction!(compare_replay_sessions, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_summary, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_session_summary, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_session_ids, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_checkpoint_by_seq, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_checkpoint_by_label, m)?)?;

        // Capsule
        m.add_function(wrap_pyfunction!(write_capsule, m)?)?;
        m.add_function(wrap_pyfunction!(read_capsule, m)?)?;
        m.add_function(wrap_pyfunction!(verify_frame_store, m)?)?;
        m.add_function(wrap_pyfunction!(frame_store_stats, m)?)?;
        m.add_function(wrap_pyfunction!(verify_capsule_bytes, m)?)?;

        // Text
        m.add_function(wrap_pyfunction!(normalize_text, m)?)?;
        m.add_function(wrap_pyfunction!(truncate_at_grapheme_boundary, m)?)?;
        m.add_function(wrap_pyfunction!(fix_pdf_spacing, m)?)?;
        m.add_function(wrap_pyfunction!(extract_triplets, m)?)?;

        // Search
        m.add_function(wrap_pyfunction!(rrf_fuse, m)?)?;
        m.add_function(wrap_pyfunction!(compute_heat, m)?)?;
        m.add_function(wrap_pyfunction!(retrieval_manifest_status, m)?)?;
        m.add_function(wrap_pyfunction!(mark_retrieval_index_dirty, m)?)?;
        m.add_function(wrap_pyfunction!(clear_retrieval_index_dirty, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_upsert, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_delete, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_delete_user_documents, m)?)?;
        m.add_function(wrap_pyfunction!(reset_memory_index, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_search, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_vector_search, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_upsert, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_delete, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_delete_user_documents, m)?)?;
        m.add_function(wrap_pyfunction!(reset_transcript_index, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_search, m)?)?;

        // Chunker
        m.add_class::<PyChunkOptions>()?;
        m.add_function(wrap_pyfunction!(chunk_text, m)?)?;

        // Enrich
        m.add_class::<PyRulesEngine>()?;

        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::sync::Once;

        use crate::capsule::{CapsuleWriter, SectionKind};
        use crate::frame::{Frame, FrameKind, FrameSource};
        use crate::integrity::{scan_frame_store, IntegrityIssueKind};

        fn with_python<T>(f: impl FnOnce(Python<'_>) -> T) -> T {
            static INIT: Once = Once::new();
            INIT.call_once(|| pyo3::prepare_freethreaded_python());
            Python::with_gil(f)
        }

        #[test]
        fn integrity_report_conversion_exposes_python_friendly_shape() {
            with_python(|py| {
                let mut store = FrameStore::new();
                let id = store.insert(Frame::new(
                    0,
                    FrameKind::Fact,
                    "alpha".into(),
                    "user-1".into(),
                    FrameSource::Api,
                ));
                store.get_mut(id).unwrap().checksum = [9; 32];

                let report = scan_frame_store(&store);
                let obj = integrity_report_to_py_dict(py, &report).unwrap();
                let dict = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(dict.get_item("ok").unwrap().unwrap().extract::<bool>().unwrap(), false);
                assert!(dict.get_item("issues").unwrap().is_some());
                assert!(dict.get_item("stats").unwrap().is_some());

                let issues_obj = dict
                    .get_item("issues")
                    .unwrap()
                    .unwrap();
                let issues = issues_obj.downcast::<PyList>().unwrap();
                let first_issue_obj = issues.get_item(0).unwrap();
                let first_issue = first_issue_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    first_issue
                        .get_item("kind")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "frame_checksum_mismatch"
                );
            });
        }

        #[test]
        fn capsule_report_conversion_exposes_sections_and_issues() {
            with_python(|py| {
                let mut writer = CapsuleWriter::new();
                writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
                let mut capsule = writer.write().unwrap();
                let footer_index = capsule.len() - 1;
                capsule[footer_index] ^= 0xFF;

                let report = verify_capsule_integrity(&capsule, None);
                let obj = capsule_report_to_py_dict(py, &report).unwrap();
                let dict = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(dict.get_item("ok").unwrap().unwrap().extract::<bool>().unwrap(), false);
                assert!(dict.get_item("stats").unwrap().is_some());
                assert!(dict.get_item("capsule").unwrap().is_some());

                let issues_obj = dict
                    .get_item("issues")
                    .unwrap()
                    .unwrap();
                let issues = issues_obj.downcast::<PyList>().unwrap();
                let first_issue_obj = issues.get_item(0).unwrap();
                let first_issue = first_issue_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    first_issue
                        .get_item("kind")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "capsule_footer_checksum_mismatch"
                );
            });
        }

        #[test]
        fn exported_verify_and_stats_functions_accept_py_frame_store() {
            with_python(|py| {
                let mut store = PyFrameStore::new();
                let frame = PyFrame {
                    inner: Frame::new(
                        0,
                        FrameKind::Fact,
                        "alpha".into(),
                        "user-1".into(),
                        FrameSource::Api,
                    ),
                };
                store.insert(&frame);

                let verify_obj = verify_frame_store(py, &store).unwrap();
                let verify_dict = verify_obj.bind(py).downcast::<PyDict>().unwrap();
                assert!(verify_dict.get_item("issues").unwrap().is_some());
                assert!(verify_dict.get_item("stats").unwrap().is_some());

                let stats_obj = frame_store_stats(py, &store).unwrap();
                let stats_dict = stats_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    stats_dict
                        .get_item("frame_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    1
                );

                let mut writer = CapsuleWriter::new();
                writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
                let capsule = writer.write().unwrap();

                let capsule_obj = verify_capsule_bytes(py, capsule, None).unwrap();
                let capsule_dict = capsule_obj.bind(py).downcast::<PyDict>().unwrap();
                let capsule_meta_obj = capsule_dict
                    .get_item("capsule")
                    .unwrap()
                    .unwrap();
                let capsule_meta = capsule_meta_obj.downcast::<PyDict>().unwrap();
                assert!(capsule_meta.get_item("sections").unwrap().is_some());
            });
        }

        #[test]
        fn exported_temporal_range_returns_newest_first_within_bounds() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1000_i64, 1100, 1200, 1300, 1400].into_iter().enumerate() {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let results = store.temporal_range(Some(1100), Some(1300), None);
                let timestamps: Vec<i64> = results.into_iter().map(|frame| frame.timestamp()).collect();
                assert_eq!(timestamps, vec![1300, 1200, 1100]);
            });
        }

        #[test]
        fn exported_temporal_as_of_applies_limit() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1000_i64, 1100, 1200, 1300, 1400].into_iter().enumerate() {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let results = store.temporal_as_of(1250, Some(2));
                let timestamps: Vec<i64> = results.into_iter().map(|frame| frame.timestamp()).collect();
                assert_eq!(timestamps, vec![1200, 1100]);
            });
        }

        #[test]
        fn exported_replay_time_bounds_reports_serialized_session_bounds() {
            with_python(|_py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-1".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![crate::replay::ReplayAction {
                        seq: 0,
                        kind: crate::replay::ActionKind::Decision,
                        description: "respond".into(),
                        offset_us: 1_250_000,
                        duration_us: 500_000,
                        frame_ids: vec![],
                        metadata: std::collections::HashMap::new(),
                    }],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                assert_eq!(replay_session_time_bounds(bytes).unwrap(), Some((1_700_000_000, 1_700_000_002)));
            });
        }

        #[test]
        fn exported_temporal_session_window_queries_frames_around_serialized_session() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1098_i64, 1099, 1100, 1101, 1102, 1103, 1104].into_iter().enumerate() {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let session = crate::replay::SerializedSession {
                    session_id: "turn-1".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1100),
                    actions: vec![crate::replay::ReplayAction {
                        seq: 0,
                        kind: crate::replay::ActionKind::Decision,
                        description: "respond".into(),
                        offset_us: 1_250_000,
                        duration_us: 500_000,
                        frame_ids: vec![],
                        metadata: std::collections::HashMap::new(),
                    }],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let results = store.temporal_session_window(bytes, 1, 1, None).unwrap();
                let timestamps: Vec<i64> = results.into_iter().map(|frame| frame.timestamp()).collect();
                assert_eq!(timestamps, vec![1103, 1102, 1101, 1100, 1099]);
            });
        }

        #[test]
        fn exported_temporal_index_snapshots_store_for_reused_queries() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1000_i64, 1100, 1200].into_iter().enumerate() {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let index = store.temporal_index();
                assert_eq!(index.len(), 3);

                let fresh = PyFrame {
                    inner: Frame::new(
                        99,
                        FrameKind::Fact,
                        "fresh".into(),
                        "user-1".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1300),
                };
                store.insert(&fresh);

                let cached_range = index.range(Some(1000), Some(1300), None);
                let cached_timestamps: Vec<i64> =
                    cached_range.into_iter().map(|frame| frame.timestamp()).collect();
                assert_eq!(cached_timestamps, vec![1200, 1100, 1000]);

                let rebuilt_timestamps: Vec<i64> = store
                    .temporal_range(Some(1000), Some(1300), None)
                    .into_iter()
                    .map(|frame| frame.timestamp())
                    .collect();
                assert_eq!(rebuilt_timestamps, vec![1300, 1200, 1100, 1000]);
            });
        }

        #[test]
        fn exported_replay_session_checkpoints_returns_structured_entries() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-1".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after reflection".into(),
                            offset_us: 1_500_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj = replay_session_checkpoints(py, bytes).unwrap();
                let checkpoints = obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(checkpoints.len(), 2);
                let first_obj = checkpoints.get_item(0).unwrap();
                let first = first_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    first.get_item("label").unwrap().unwrap().extract::<String>().unwrap(),
                    "before reflection"
                );
                assert_eq!(
                    first.get_item("timestamp").unwrap().unwrap().extract::<i64>().unwrap(),
                    1_700_000_000
                );
            });
        }

        #[test]
        fn exported_compare_replay_sessions_returns_checkpoint_and_kind_deltas() {
            with_python(|py| {
                let left = crate::replay::SerializedSession {
                    session_id: "left".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 0,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Reflection,
                            description: "think".into(),
                            offset_us: 0,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };
                let right = crate::replay::SerializedSession {
                    session_id: "right".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_100),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 0,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 0,
                            duration_us: 1_000_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 2,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after decision".into(),
                            offset_us: 1_000_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let left_bytes = serde_json::to_vec(&left).unwrap();
                let right_bytes = serde_json::to_vec(&right).unwrap();
                let obj = compare_replay_sessions(py, left_bytes, right_bytes).unwrap();
                let comparison = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(
                    comparison.get_item("action_count_delta").unwrap().unwrap().extract::<i64>().unwrap(),
                    1
                );
                let shared_obj = comparison
                    .get_item("shared_checkpoint_labels")
                    .unwrap()
                    .unwrap();
                let shared = shared_obj.downcast::<PyList>().unwrap();
                assert_eq!(shared.get_item(0).unwrap().extract::<String>().unwrap(), "before reflection");

                let kind_deltas_obj = comparison
                    .get_item("kind_count_delta")
                    .unwrap()
                    .unwrap();
                let kind_deltas = kind_deltas_obj.downcast::<PyDict>().unwrap();
                assert_eq!(kind_deltas.get_item("decision").unwrap().unwrap().extract::<i64>().unwrap(), 1);
                assert_eq!(kind_deltas.get_item("reflection").unwrap().unwrap().extract::<i64>().unwrap(), -1);
            });
        }

        #[test]
        fn exported_replay_session_summary_returns_structured_totals() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-9".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 0,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::MemoryRetrieve,
                            description: "fetch".into(),
                            offset_us: 0,
                            duration_us: 500_000,
                            frame_ids: vec![1, 2],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 2,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 1_000_000,
                            duration_us: 1_000_000,
                            frame_ids: vec![2, 3],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 3,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after decision".into(),
                            offset_us: 2_000_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj = replay_session_summary(py, bytes).unwrap();
                let summary = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(summary.get_item("action_count").unwrap().unwrap().extract::<usize>().unwrap(), 4);
                assert_eq!(summary.get_item("checkpoint_count").unwrap().unwrap().extract::<usize>().unwrap(), 2);
                assert_eq!(summary.get_item("referenced_frame_count").unwrap().unwrap().extract::<usize>().unwrap(), 3);
                assert_eq!(summary.get_item("ended_at").unwrap().unwrap().extract::<i64>().unwrap(), 1_700_000_002);

                let labels_obj = summary.get_item("checkpoint_labels").unwrap().unwrap();
                let labels = labels_obj.downcast::<PyList>().unwrap();
                assert_eq!(labels.get_item(0).unwrap().extract::<String>().unwrap(), "before reflection");
                assert_eq!(labels.get_item(1).unwrap().extract::<String>().unwrap(), "after decision");

                let kind_counts_obj = summary.get_item("kind_counts").unwrap().unwrap();
                let kind_counts = kind_counts_obj.downcast::<PyDict>().unwrap();
                assert_eq!(kind_counts.get_item("checkpoint").unwrap().unwrap().extract::<usize>().unwrap(), 2);
                assert_eq!(kind_counts.get_item("memory_retrieve").unwrap().unwrap().extract::<usize>().unwrap(), 1);
                assert_eq!(kind_counts.get_item("decision").unwrap().unwrap().extract::<usize>().unwrap(), 1);
            });
        }

        #[test]
        fn exported_replay_session_checkpoint_by_seq_returns_structured_checkpoint() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-10".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 500_000,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 2,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after reflection".into(),
                            offset_us: 1_500_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj = replay_session_checkpoint_by_seq(py, bytes.clone(), 2)
                    .unwrap()
                    .unwrap();
                let checkpoint = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(checkpoint.get_item("seq").unwrap().unwrap().extract::<u32>().unwrap(), 2);
                assert_eq!(
                    checkpoint.get_item("label").unwrap().unwrap().extract::<String>().unwrap(),
                    "after reflection"
                );
                assert_eq!(
                    checkpoint.get_item("timestamp").unwrap().unwrap().extract::<i64>().unwrap(),
                    1_700_000_001
                );

                assert!(replay_session_checkpoint_by_seq(py, bytes, 99).unwrap().is_none());
            });
        }

        #[test]
        fn exported_replay_session_checkpoint_by_label_returns_structured_checkpoint() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-11".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Reflection,
                            description: "think".into(),
                            offset_us: 500_000,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj = replay_session_checkpoint_by_label(py, bytes.clone(), "before reflection")
                    .unwrap()
                    .unwrap();
                let checkpoint = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(checkpoint.get_item("seq").unwrap().unwrap().extract::<u32>().unwrap(), 0);
                assert_eq!(checkpoint.get_item("offset_us").unwrap().unwrap().extract::<u64>().unwrap(), 250_000);

                assert!(replay_session_checkpoint_by_label(py, bytes, "missing")
                    .unwrap()
                    .is_none());
            });
        }

        #[test]
        fn exported_replay_registry_summary_and_checkpoint_lookup_are_scoped_by_session_id() {
            with_python(|py| {
                let sessions: Vec<Vec<u8>> = vec![
                    crate::replay::SerializedSession {
                        session_id: "turn-12".into(),
                        user_id: "user-1".into(),
                        started_at: Some(1_700_000_000),
                        actions: vec![
                            crate::replay::ReplayAction {
                                seq: 0,
                                kind: crate::replay::ActionKind::Checkpoint,
                                description: "before reflection".into(),
                                offset_us: 100_000,
                                duration_us: 0,
                                frame_ids: vec![],
                                metadata: std::collections::HashMap::new(),
                            },
                            crate::replay::ReplayAction {
                                seq: 1,
                                kind: crate::replay::ActionKind::Decision,
                                description: "respond".into(),
                                offset_us: 500_000,
                                duration_us: 500_000,
                                frame_ids: vec![1],
                                metadata: std::collections::HashMap::new(),
                            },
                        ],
                    },
                    crate::replay::SerializedSession {
                        session_id: "turn-13".into(),
                        user_id: "user-2".into(),
                        started_at: Some(1_700_000_100),
                        actions: vec![crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after tool".into(),
                            offset_us: 1_000_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        }],
                    },
                ]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let summary_obj =
                    replay_registry_session_summary(py, sessions.clone(), "turn-12")
                        .unwrap()
                        .unwrap();
                let summary = summary_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    summary
                        .get_item("action_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    2
                );

                let checkpoint_obj = replay_registry_checkpoint_by_label(
                    py,
                    sessions.clone(),
                    "turn-13",
                    "after tool",
                )
                .unwrap()
                .unwrap();
                let checkpoint = checkpoint_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    checkpoint.get_item("seq").unwrap().unwrap().extract::<u32>().unwrap(),
                    0
                );
                assert_eq!(
                    checkpoint
                        .get_item("timestamp")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    1_700_000_101
                );

                assert!(replay_registry_session_summary(py, sessions.clone(), "missing")
                    .unwrap()
                    .is_none());
                assert!(replay_registry_checkpoint_by_label(py, sessions, "turn-12", "missing")
                    .unwrap()
                    .is_none());
            });
        }

        #[test]
        fn exported_replay_registry_rejects_duplicate_session_ids() {
            with_python(|py| {
                let duplicate_sessions: Vec<Vec<u8>> = vec![
                    crate::replay::SerializedSession {
                        session_id: "turn-14".into(),
                        user_id: "user-1".into(),
                        started_at: Some(1_700_000_000),
                        actions: vec![],
                    },
                    crate::replay::SerializedSession {
                        session_id: "turn-14".into(),
                        user_id: "user-2".into(),
                        started_at: Some(1_700_000_100),
                        actions: vec![],
                    },
                ]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let err = replay_registry_session_summary(py, duplicate_sessions, "turn-14")
                    .unwrap_err();
                assert!(err.is_instance_of::<pyo3::exceptions::PyValueError>(py));
                assert!(err.to_string().contains("duplicate replay session id: turn-14"));
            });
        }

        #[test]
        fn exported_replay_registry_session_ids_are_sorted() {
            with_python(|_py| {
                let sessions: Vec<Vec<u8>> = vec![
                    crate::replay::SerializedSession {
                        session_id: "turn-b".into(),
                        user_id: "user-2".into(),
                        started_at: Some(1_700_000_100),
                        actions: vec![],
                    },
                    crate::replay::SerializedSession {
                        session_id: "turn-a".into(),
                        user_id: "user-1".into(),
                        started_at: Some(1_700_000_000),
                        actions: vec![],
                    },
                ]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let ids = replay_registry_session_ids(sessions).unwrap();
                assert_eq!(ids, vec!["turn-a".to_string(), "turn-b".to_string()]);
            });
        }

        #[test]
        fn exported_replay_registry_checkpoint_by_seq_returns_structured_checkpoint() {
            with_python(|py| {
                let sessions: Vec<Vec<u8>> = vec![crate::replay::SerializedSession {
                    session_id: "turn-16".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 500_000,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                }]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let checkpoint = replay_registry_checkpoint_by_seq(py, sessions.clone(), "turn-16", 0)
                    .unwrap()
                    .unwrap();
                let checkpoint = checkpoint.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(checkpoint.get_item("label").unwrap().unwrap().extract::<String>().unwrap(), "before reflection");

                assert!(replay_registry_checkpoint_by_seq(py, sessions, "turn-16", 99)
                    .unwrap()
                    .is_none());
            });
        }

        #[test]
        fn capsule_verify_binding_uses_integrity_issue_kinds() {
            with_python(|py| {
                let mut writer = CapsuleWriter::new();
                writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
                let mut capsule = writer.write().unwrap();
                let footer_index = capsule.len() - 1;
                capsule[footer_index] ^= 0xFF;

                let capsule_obj = verify_capsule_bytes(py, capsule, None).unwrap();
                let capsule_dict = capsule_obj.bind(py).downcast::<PyDict>().unwrap();
                let issues_obj = capsule_dict
                    .get_item("issues")
                    .unwrap()
                    .unwrap();
                let issues = issues_obj.downcast::<PyList>().unwrap();
                let first_issue_obj = issues.get_item(0).unwrap();
                let first_issue = first_issue_obj.downcast::<PyDict>().unwrap();
                let kind = first_issue
                    .get_item("kind")
                    .unwrap()
                    .unwrap()
                    .extract::<String>()
                    .unwrap();

                assert_eq!(
                    kind,
                    serde_json::to_value(IntegrityIssueKind::CapsuleFooterChecksumMismatch)
                        .unwrap()
                        .as_str()
                        .unwrap()
                );
            });
        }

        fn make_memory_card(
            entity: &str,
            slot: &str,
            value: &str,
            version: VersionRelation,
            frame_id: u64,
            created_at: i64,
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
                created_at,
                active: true,
                superseded_by: None,
            }
        }

        #[test]
        fn exported_entity_state_returns_python_friendly_shape() {
            with_python(|py| {
                let mut cards = PyCardStore::new();
                cards.inner.put(make_memory_card(
                    "user",
                    "likes",
                    "coffee",
                    VersionRelation::Sets,
                    10,
                    300,
                ));
                cards.inner.put(make_memory_card(
                    "user",
                    "likes",
                    "alpha",
                    VersionRelation::Extends,
                    20,
                    100,
                ));

                let mut graph = PyKnowledgeGraph::new();
                let user = graph
                    .inner
                    .upsert_node("user", EntityKind::Person, 0.9, 1)
                    .unwrap();
                let openai = graph
                    .inner
                    .upsert_node("OpenAI", EntityKind::Organization, 0.9, 2)
                    .unwrap();
                graph
                    .inner
                    .upsert_edge(user, openai, "employer", 0.9, 99)
                    .unwrap();

                let obj = project_entity_state(py, &cards, &graph, "user").unwrap();
                let dict = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(
                    dict.get_item("entity").unwrap().unwrap().extract::<String>().unwrap(),
                    "user"
                );
                assert!(dict.get_item("slots").unwrap().is_some());
                assert!(dict.get_item("connected_entities").unwrap().is_some());
                assert!(dict.get_item("supporting_frame_ids").unwrap().is_some());

                let slots_value = dict.get_item("slots").unwrap().unwrap();
                let slots = slots_value.downcast::<PyList>().unwrap();
                assert_eq!(slots.len(), 1);
                let slot_value = slots.get_item(0).unwrap();
                let slot = slot_value.downcast::<PyDict>().unwrap();
                assert_eq!(slot.get_item("slot").unwrap().unwrap().extract::<String>().unwrap(), "likes");
                let values_binding = slot.get_item("values").unwrap().unwrap();
                let values = values_binding.downcast::<PyList>().unwrap();
                assert_eq!(
                    values
                        .iter()
                        .map(|item| item.extract::<String>().unwrap())
                        .collect::<Vec<_>>(),
                    vec!["alpha".to_string(), "coffee".to_string()]
                );
                let slot_supporting_binding = slot
                    .get_item("supporting_frame_ids")
                    .unwrap()
                    .unwrap();
                let slot_supporting = slot_supporting_binding.downcast::<PyList>().unwrap();
                assert_eq!(
                    slot_supporting
                        .iter()
                        .map(|item| item.extract::<u64>().unwrap())
                        .collect::<Vec<_>>(),
                    vec![10, 20]
                );

                let connected_value = dict.get_item("connected_entities").unwrap().unwrap();
                let connected = connected_value.downcast::<PyList>().unwrap();
                assert_eq!(connected.len(), 1);
                let neighbor_value = connected.get_item(0).unwrap();
                let neighbor = neighbor_value.downcast::<PyDict>().unwrap();
                assert_eq!(
                    neighbor
                        .get_item("relation_type")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "employer"
                );
                assert_eq!(
                    neighbor
                        .get_item("entity_name")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "OpenAI"
                );
                assert_eq!(
                    neighbor
                        .get_item("entity_kind")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "organization"
                );
                assert_eq!(
                    neighbor
                        .get_item("supporting_frame_ids")
                        .unwrap()
                        .unwrap()
                        .downcast::<PyList>()
                        .unwrap()
                        .iter()
                        .map(|item| item.extract::<u64>().unwrap())
                        .collect::<Vec<_>>(),
                    vec![99]
                );

                let supporting_binding = dict
                    .get_item("supporting_frame_ids")
                    .unwrap()
                    .unwrap();
                let supporting = supporting_binding.downcast::<PyList>().unwrap();
                assert_eq!(
                    supporting
                        .iter()
                        .map(|item| item.extract::<u64>().unwrap())
                        .collect::<Vec<_>>(),
                    vec![10, 20, 99]
                );
            });
        }

        #[test]
        fn exported_slot_history_returns_ordered_versions() {
            with_python(|py| {
                let mut cards = PyCardStore::new();
                cards.inner.put(make_memory_card(
                    "user",
                    "employer",
                    "Google",
                    VersionRelation::Sets,
                    1,
                    300,
                ));
                cards.inner.put(make_memory_card(
                    "user",
                    "employer",
                    "Meta",
                    VersionRelation::Updates,
                    2,
                    100,
                ));
                cards.inner.put(make_memory_card(
                    "user",
                    "employer",
                    "Meta",
                    VersionRelation::Retracts,
                    3,
                    200,
                ));

                let obj = project_slot_history(py, &cards, "user", "employer").unwrap();
                let history = obj.bind(py).downcast::<PyList>().unwrap();

                assert_eq!(history.len(), 3);
                let first_value = history.get_item(0).unwrap();
                let second_value = history.get_item(1).unwrap();
                let third_value = history.get_item(2).unwrap();
                let first = first_value.downcast::<PyDict>().unwrap();
                let second = second_value.downcast::<PyDict>().unwrap();
                let third = third_value.downcast::<PyDict>().unwrap();

                assert_eq!(first.get_item("value").unwrap().unwrap().extract::<String>().unwrap(), "Google");
                assert_eq!(
                    first.get_item("version").unwrap().unwrap().extract::<String>().unwrap(),
                    "sets"
                );
                assert_eq!(second.get_item("value").unwrap().unwrap().extract::<String>().unwrap(), "Meta");
                assert_eq!(
                    second.get_item("version").unwrap().unwrap().extract::<String>().unwrap(),
                    "updates"
                );
                assert_eq!(third.get_item("value").unwrap().unwrap().extract::<String>().unwrap(), "Meta");
                assert_eq!(
                    third.get_item("version").unwrap().unwrap().extract::<String>().unwrap(),
                    "retracts"
                );
            });
        }

        #[test]
        fn exported_engine_class_supports_verify_project_and_temporal_queries() {
            with_python(|py| {
                let mut frames = FrameStore::new();
                let mut cards = CardStore::new(SchemaRegistry::new());
                let mut graph = KnowledgeGraph::new();

                let older_frame_id = frames.insert(
                    Frame::new(
                        0,
                        FrameKind::Fact,
                        "user worked at Google".into(),
                        "user".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1_700_000_000),
                );
                let newer_frame_id = frames.insert(
                    Frame::new(
                        0,
                        FrameKind::Fact,
                        "user works at OpenAI".into(),
                        "user".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1_700_000_100),
                );

                cards.put(make_memory_card(
                    "user",
                    "employer",
                    "Google",
                    VersionRelation::Sets,
                    older_frame_id,
                    100,
                ));
                cards.put(make_memory_card(
                    "user",
                    "employer",
                    "OpenAI",
                    VersionRelation::Updates,
                    newer_frame_id,
                    200,
                ));

                graph.upsert_node("user", EntityKind::Person, 1.0, newer_frame_id).unwrap();
                graph
                    .upsert_node("OpenAI", EntityKind::Organization, 1.0, newer_frame_id)
                    .unwrap();
                let from = graph.get_by_name("user").unwrap().id;
                let to = graph.get_by_name("OpenAI").unwrap().id;
                graph.upsert_edge(from, to, "employer", 1.0, newer_frame_id).unwrap();

                let engine = PyAnimaEngine {
                    inner: crate::engine::AnimaEngine::from_parts(frames, cards, graph),
                };

                let verify_obj = engine.verify(py).unwrap();
                let verify = verify_obj.bind(py).downcast::<PyDict>().unwrap();
                assert!(!verify.get_item("ok").unwrap().unwrap().extract::<bool>().unwrap());

                let stats_obj = engine.stats(py).unwrap();
                let stats = stats_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    stats.get_item("frame_count").unwrap().unwrap().extract::<usize>().unwrap(),
                    2
                );
                assert_eq!(
                    stats.get_item("graph_edge_count").unwrap().unwrap().extract::<usize>().unwrap(),
                    1
                );

                let state_obj = engine.project_entity_state(py, "user").unwrap();
                let state = state_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    state.get_item("entity").unwrap().unwrap().extract::<String>().unwrap(),
                    "user"
                );
                let slots_value = state
                    .get_item("slots")
                    .unwrap()
                    .unwrap();
                let slots = slots_value.downcast::<PyList>().unwrap();
                assert_eq!(slots.len(), 1);
                let slot_value = slots.get_item(0).unwrap();
                let slot = slot_value.downcast::<PyDict>().unwrap();
                assert_eq!(
                    slot.get_item("slot").unwrap().unwrap().extract::<String>().unwrap(),
                    "employer"
                );

                let history_obj = engine.project_slot_history(py, "user", "employer").unwrap();
                let history = history_obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(history.len(), 2);
                assert_eq!(
                    history
                        .get_item(0)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("value")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "Google"
                );
                assert_eq!(
                    history
                        .get_item(1)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("value")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "OpenAI"
                );

                let temporal_obj = engine
                    .temporal_range(py, Some(1_700_000_000), Some(1_700_000_100), Some(2))
                    .unwrap();
                let temporal = temporal_obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(temporal.len(), 2);
                assert_eq!(
                    temporal
                        .get_item(0)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("content")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user works at OpenAI"
                );
                assert_eq!(
                    temporal
                        .get_item(1)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("content")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user worked at Google"
                );
            });
        }

        #[test]
        fn exported_engine_capsule_roundtrip_restores_state() {
            with_python(|py| {
                let mut frames = FrameStore::new();
                let mut cards = CardStore::new(SchemaRegistry::new());
                let mut graph = KnowledgeGraph::new();

                let frame_id = frames.insert(
                    Frame::new(
                        0,
                        FrameKind::Fact,
                        "user works at OpenAI".into(),
                        "user".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1_700_000_200),
                );

                cards.put(make_memory_card(
                    "user",
                    "employer",
                    "OpenAI",
                    VersionRelation::Sets,
                    frame_id,
                    200,
                ));

                graph.upsert_node("user", EntityKind::Person, 1.0, frame_id).unwrap();
                graph
                    .upsert_node("OpenAI", EntityKind::Organization, 1.0, frame_id)
                    .unwrap();
                let from = graph.get_by_name("user").unwrap().id;
                let to = graph.get_by_name("OpenAI").unwrap().id;
                graph.upsert_edge(from, to, "employer", 1.0, frame_id).unwrap();

                let engine = PyAnimaEngine {
                    inner: crate::engine::AnimaEngine::from_parts(frames, cards, graph),
                };
                let capsule = engine.to_capsule_bytes(None).unwrap();
                let restored = PyAnimaEngine::from_capsule_bytes(capsule, None).unwrap();

                let stats_obj = restored.stats(py).unwrap();
                let stats = stats_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    stats.get_item("frame_count").unwrap().unwrap().extract::<usize>().unwrap(),
                    1
                );
                assert_eq!(
                    stats.get_item("card_count").unwrap().unwrap().extract::<usize>().unwrap(),
                    1
                );

                let state_obj = restored.project_entity_state(py, "user").unwrap();
                let state = state_obj.bind(py).downcast::<PyDict>().unwrap();
                let slots_value = state
                    .get_item("slots")
                    .unwrap()
                    .unwrap();
                let slots = slots_value.downcast::<PyList>().unwrap();
                assert_eq!(slots.len(), 1);
                let slot_item = slots.get_item(0).unwrap();
                let slot_value = slot_item.downcast::<PyDict>().unwrap();
                let values_value = slot_value
                    .get_item("values")
                    .unwrap()
                    .unwrap();
                let values = values_value.downcast::<PyList>().unwrap();
                assert_eq!(
                    values
                        .iter()
                        .map(|item: pyo3::Bound<'_, pyo3::PyAny>| item.extract::<String>().unwrap())
                        .collect::<Vec<_>>(),
                    vec!["OpenAI".to_string()]
                );

                let temporal_obj = restored.temporal_range(py, None, None, Some(1)).unwrap();
                let temporal = temporal_obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(temporal.len(), 1);
                assert_eq!(
                    temporal
                        .get_item(0)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("content")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user works at OpenAI"
                );
            });
        }

        #[test]
        fn python_bindings_reject_invalid_enum_strings() {
            with_python(|_py| {
                assert!(PyFrame::new("not-a-kind", "alpha".into(), "user-1".into()).is_err());

                let mut cards = PyCardStore::new();
                assert!(cards
                    .put("user", "likes", "coffee", "bogus", "sets", 1.0, 0)
                    .is_err());
                assert!(cards
                    .put("user", "likes", "coffee", "fact", "bogus", 1.0, 0)
                    .is_err());

                let mut graph = PyKnowledgeGraph::new();
                assert!(graph.upsert_node("alice", "bogus", 1.0, 0).is_err());

                let mut rules = PyRulesEngine::new(false);
                assert!(rules
                    .add_rule("r1", ".*", "bogus", "user", "slot", "value")
                    .is_err());
            });
        }
    }
}
