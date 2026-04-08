//! PyO3 bindings exposing anima-core to Python.
//!
//! Provides zero-overhead native imports for animaOS's Python layer.
//! Build with: `maturin develop --features python`

#[cfg(feature = "python")]
mod python {
    use pyo3::prelude::*;
    use std::collections::HashMap;

    use crate::cards::{
        CardStore, Cardinality, MemoryCard, MemoryKind, Polarity, SchemaRegistry, VersionRelation,
    };
    use crate::frame::{Frame, FrameKind, FrameSource, FrameStore};
    use crate::graph::{EntityKind, KnowledgeGraph};
    use crate::search::{HeatMeta, HeatParams};

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
        fn new(kind: &str, content: String, user_id: String) -> Self {
            let fk = FrameKind::from_str(kind);
            Self {
                inner: Frame::new(0, fk, content, user_id, FrameSource::Api),
            }
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
        ) -> u64 {
            let card = MemoryCard {
                id: 0,
                kind: MemoryKind::from_str(kind),
                entity: entity.into(),
                slot: slot.into(),
                value: value.into(),
                polarity: Polarity::Neutral,
                version: VersionRelation::from_str(version),
                confidence,
                frame_id,
                created_at: chrono::Utc::now().timestamp(),
                active: true,
                superseded_by: None,
            };
            self.inner.put(card)
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
                .upsert_node(name, EntityKind::from_str(kind), confidence, frame_id)
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
    fn parse_temporal(input: &str) -> Option<(String, i64, f32)> {
        let now = chrono::Utc::now();
        crate::temporal::parse_temporal(input, now).map(|m| (m.raw, m.timestamp, m.confidence))
    }

    // ── Capsule Bindings ─────────────────────────────────────────────

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
            let kind = crate::cards::MemoryKind::from_str(kind);
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

    #[pymodule]
    #[pyo3(name = "anima_core")]
    pub fn anima_core_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
        // Frame types
        m.add_class::<PyFrame>()?;
        m.add_class::<PyFrameStore>()?;

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

        // Temporal
        m.add_function(wrap_pyfunction!(parse_temporal, m)?)?;

        // Capsule
        m.add_function(wrap_pyfunction!(write_capsule, m)?)?;
        m.add_function(wrap_pyfunction!(read_capsule, m)?)?;

        // Text
        m.add_function(wrap_pyfunction!(normalize_text, m)?)?;
        m.add_function(wrap_pyfunction!(truncate_at_grapheme_boundary, m)?)?;
        m.add_function(wrap_pyfunction!(fix_pdf_spacing, m)?)?;
        m.add_function(wrap_pyfunction!(extract_triplets, m)?)?;

        // Search
        m.add_function(wrap_pyfunction!(rrf_fuse, m)?)?;
        m.add_function(wrap_pyfunction!(compute_heat, m)?)?;

        // Chunker
        m.add_class::<PyChunkOptions>()?;
        m.add_function(wrap_pyfunction!(chunk_text, m)?)?;

        // Enrich
        m.add_class::<PyRulesEngine>()?;

        Ok(())
    }
}
