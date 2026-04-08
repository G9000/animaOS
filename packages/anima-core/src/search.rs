//! Hybrid search combining vector (semantic) and lexical (BM25) results
//! using Reciprocal Rank Fusion (RRF).
//!
//! Matches animaOS's existing hybrid search formula:
//!   RRF(v, b) = 1/(k + rank_v) + 1/(k + rank_b)  where k=60
//!
//! Integrates heat scoring for final ranking:
//!   H = (α·access + β·depth + δ·importance) · recency + γ·recency
//!   where recency = e^(-t/τ), τ=24h

use std::collections::HashMap;

use crate::frame::FrameId;

/// Combined search result with provenance from both search legs.
#[derive(Debug, Clone)]
pub struct SearchResult {
    pub frame_id: FrameId,
    /// RRF-fused score (higher = better).
    pub rrf_score: f32,
    /// Final score after heat adjustment.
    pub final_score: f32,
    /// Rank from vector search leg (None if not found).
    pub vec_rank: Option<usize>,
    /// Rank from lexical search leg (None if not found).
    pub lex_rank: Option<usize>,
}

/// Heat scoring parameters matching animaOS's heat_scoring.py.
#[derive(Debug, Clone)]
pub struct HeatParams {
    /// Weight for access count. Default: 1.0
    pub alpha: f64,
    /// Weight for interaction depth. Default: 1.0
    pub beta: f64,
    /// Weight for recency. Default: 1.0
    pub gamma: f64,
    /// Weight for importance. Default: 0.5
    pub delta: f64,
    /// Time constant in seconds. Default: 86400 (24h)
    pub tau_seconds: f64,
}

impl Default for HeatParams {
    fn default() -> Self {
        Self {
            alpha: 1.0,
            beta: 1.0,
            gamma: 1.0,
            delta: 0.5,
            tau_seconds: 86400.0, // 24 hours
        }
    }
}

/// Metadata needed to compute heat score for a frame.
#[derive(Debug, Clone, Default)]
pub struct HeatMeta {
    pub access_count: u32,
    pub interaction_depth: f64,
    pub importance: u8,
    /// Unix timestamp of last access.
    pub last_accessed_at: Option<i64>,
    /// Whether this frame is superseded (decays 3x faster).
    pub is_superseded: bool,
}

/// Compute heat score for a single frame.
///
/// Formula matches animaOS: H = (α·access + β·depth + δ·importance) · recency + γ·recency
/// where recency = e^(-t/τ)
pub fn compute_heat(meta: &HeatMeta, params: &HeatParams, now: i64) -> f64 {
    let age_seconds = meta
        .last_accessed_at
        .map(|t| (now - t).max(0) as f64)
        .unwrap_or(params.tau_seconds * 10.0); // Very old if never accessed

    let tau = if meta.is_superseded {
        params.tau_seconds / 3.0
    } else {
        params.tau_seconds
    };

    let recency = (-age_seconds / tau).exp();
    let signal = params.alpha * meta.access_count as f64
        + params.beta * meta.interaction_depth
        + params.delta * meta.importance as f64;

    signal * recency + params.gamma * recency
}

/// Fuse vector and lexical search results using Reciprocal Rank Fusion.
///
/// RRF constant k=60 (standard value, matches animaOS's embeddings.py).
pub fn rrf_fuse(
    vec_results: &[(FrameId, f32)],
    lex_results: &[(FrameId, f32)],
    k: f32,
) -> Vec<(FrameId, f32, Option<usize>, Option<usize>)> {
    let mut scores: HashMap<FrameId, (f32, Option<usize>, Option<usize>)> = HashMap::new();

    for (rank, (frame_id, _distance)) in vec_results.iter().enumerate() {
        let rrf = 1.0 / (k + (rank + 1) as f32);
        let entry = scores.entry(*frame_id).or_insert((0.0, None, None));
        entry.0 += rrf;
        entry.1 = Some(rank + 1);
    }

    for (rank, (frame_id, _score)) in lex_results.iter().enumerate() {
        let rrf = 1.0 / (k + (rank + 1) as f32);
        let entry = scores.entry(*frame_id).or_insert((0.0, None, None));
        entry.0 += rrf;
        entry.2 = Some(rank + 1);
    }

    let mut fused: Vec<(FrameId, f32, Option<usize>, Option<usize>)> = scores
        .into_iter()
        .map(|(id, (score, vr, lr))| (id, score, vr, lr))
        .collect();

    fused.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    fused
}

/// Perform hybrid search: combine vector + lexical results with RRF and heat scoring.
pub fn hybrid_search(
    vec_results: &[(FrameId, f32)],
    lex_results: &[(FrameId, f32)],
    heat_metas: &HashMap<FrameId, HeatMeta>,
    heat_params: &HeatParams,
    now: i64,
    k_rrf: f32,
    limit: usize,
) -> Vec<SearchResult> {
    let fused = rrf_fuse(vec_results, lex_results, k_rrf);

    let mut results: Vec<SearchResult> = fused
        .into_iter()
        .map(|(frame_id, rrf_score, vec_rank, lex_rank)| {
            let heat = heat_metas
                .get(&frame_id)
                .map(|m| compute_heat(m, heat_params, now))
                .unwrap_or(1.0);

            SearchResult {
                frame_id,
                rrf_score,
                final_score: rrf_score * heat as f32,
                vec_rank,
                lex_rank,
            }
        })
        .collect();

    results.sort_by(|a, b| {
        b.final_score
            .partial_cmp(&a.final_score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    results.truncate(limit);
    results
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rrf_fuse_basic() {
        let vec_results = vec![(1, 0.1f32), (2, 0.2), (3, 0.3)];
        let lex_results = vec![(2, 5.0f32), (3, 3.0), (4, 1.0)];

        let fused = rrf_fuse(&vec_results, &lex_results, 60.0);

        // Frame 2 and 3 should have highest scores (appear in both legs)
        let ids: Vec<FrameId> = fused.iter().map(|r| r.0).collect();
        assert!(ids.contains(&2));
        assert!(ids.contains(&3));

        // Frame 2 should be first (rank 2 in vec + rank 1 in lex)
        let frame2_score = fused.iter().find(|r| r.0 == 2).unwrap().1;
        let frame1_score = fused.iter().find(|r| r.0 == 1).unwrap().1;
        assert!(frame2_score > frame1_score);
    }

    #[test]
    fn test_heat_computation() {
        let params = HeatParams::default();
        let now = 1000000i64;

        // Recent, important memory
        let hot = HeatMeta {
            access_count: 10,
            interaction_depth: 2.0,
            importance: 5,
            last_accessed_at: Some(now - 3600), // 1 hour ago
            is_superseded: false,
        };

        // Old, unimportant memory
        let cold = HeatMeta {
            access_count: 1,
            interaction_depth: 0.0,
            importance: 1,
            last_accessed_at: Some(now - 864000), // 10 days ago
            is_superseded: false,
        };

        let hot_heat = compute_heat(&hot, &params, now);
        let cold_heat = compute_heat(&cold, &params, now);
        assert!(
            hot_heat > cold_heat,
            "hot={hot_heat} should > cold={cold_heat}"
        );
    }

    #[test]
    fn test_superseded_decays_faster() {
        let params = HeatParams::default();
        let now = 1000000i64;

        let base = HeatMeta {
            access_count: 5,
            interaction_depth: 1.0,
            importance: 3,
            last_accessed_at: Some(now - 86400), // 1 day ago
            is_superseded: false,
        };

        let superseded = HeatMeta {
            is_superseded: true,
            ..base.clone()
        };

        let base_heat = compute_heat(&base, &params, now);
        let superseded_heat = compute_heat(&superseded, &params, now);
        assert!(
            base_heat > superseded_heat,
            "superseded should decay faster: base={base_heat} > superseded={superseded_heat}"
        );
    }

    #[test]
    fn test_hybrid_search_integration() {
        let vec_results = vec![(1, 0.05f32), (2, 0.15), (3, 0.25)];
        let lex_results = vec![(2, 10.0f32), (4, 5.0)];

        let now = 1000000i64;
        let mut metas = HashMap::new();
        metas.insert(
            1,
            HeatMeta {
                access_count: 1,
                importance: 3,
                last_accessed_at: Some(now - 3600),
                ..Default::default()
            },
        );
        metas.insert(
            2,
            HeatMeta {
                access_count: 10,
                importance: 5,
                last_accessed_at: Some(now - 600),
                ..Default::default()
            },
        );

        let results = hybrid_search(
            &vec_results,
            &lex_results,
            &metas,
            &HeatParams::default(),
            now,
            60.0,
            10,
        );

        assert!(!results.is_empty());
        // Frame 2 should be top (in both legs + high heat)
        assert_eq!(results[0].frame_id, 2);
    }
}
