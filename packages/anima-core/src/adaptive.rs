//! Adaptive retrieval cutoff strategies.
//!
//! Rather than returning a fixed number of results for every query, adaptive
//! retrieval cuts ranked results where the score distribution indicates a
//! natural boundary. This reduces context noise while keeping dense result
//! sets intact for broad queries.

use serde::{Deserialize, Serialize};

/// Strategy for determining where to cut off ranked results.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum CutoffStrategy {
    /// Stop when the normalized score drops below a fixed value.
    AbsoluteThreshold { min_score: f32 },
    /// Stop when the score drops below a ratio of the top score.
    RelativeThreshold { min_ratio: f32 },
    /// Stop when the score drops sharply from the previous result.
    ScoreCliff { max_drop_ratio: f32 },
    /// Detect the elbow point in the score curve.
    Elbow { sensitivity: f32 },
    /// Apply absolute, relative, and cliff checks together.
    Combined {
        relative_threshold: f32,
        max_drop_ratio: f32,
        absolute_min: f32,
    },
}

impl Default for CutoffStrategy {
    fn default() -> Self {
        Self::Combined {
            relative_threshold: 0.5,
            max_drop_ratio: 0.4,
            absolute_min: 0.3,
        }
    }
}

/// Configuration for adaptive retrieval.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AdaptiveConfig {
    pub enabled: bool,
    pub max_results: usize,
    pub min_results: usize,
    pub strategy: CutoffStrategy,
    pub normalize_scores: bool,
}

impl Default for AdaptiveConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            max_results: 100,
            min_results: 1,
            strategy: CutoffStrategy::default(),
            normalize_scores: true,
        }
    }
}

/// Normalize scores into the `[0, 1]` range using min-max normalization.
#[must_use]
pub fn normalize_scores(scores: &[f32]) -> Vec<f32> {
    if scores.is_empty() {
        return Vec::new();
    }

    let max_score = scores.iter().copied().fold(f32::NEG_INFINITY, f32::max);
    let min_score = scores.iter().copied().fold(f32::INFINITY, f32::min);
    let range = max_score - min_score;

    if range.abs() < f32::EPSILON {
        return vec![1.0; scores.len()];
    }

    scores
        .iter()
        .map(|score| (score - min_score) / range)
        .collect()
}

/// Find the adaptive cutoff for a ranked score list.
///
/// Returns `(cutoff_index, trigger, normalized_scores)` where results in
/// `0..cutoff_index` should be kept.
#[must_use]
pub fn find_adaptive_cutoff(scores: &[f32], config: &AdaptiveConfig) -> (usize, String, Vec<f32>) {
    if scores.is_empty() {
        return (0, "no_results".to_string(), Vec::new());
    }

    let effective_max = config.max_results.max(config.min_results).max(1);
    let capped = &scores[..scores.len().min(effective_max)];
    let normalized = if config.normalize_scores {
        normalize_scores(capped)
    } else {
        capped.to_vec()
    };

    if !config.enabled {
        return (capped.len(), "disabled".to_string(), normalized);
    }

    if capped.len() <= config.min_results {
        return (capped.len(), "min_results".to_string(), normalized);
    }

    let (cutoff, trigger) = match &config.strategy {
        CutoffStrategy::AbsoluteThreshold { min_score } => {
            find_absolute_cutoff(&normalized, *min_score, config.min_results)
        }
        CutoffStrategy::RelativeThreshold { min_ratio } => {
            let threshold = normalized[0] * min_ratio;
            find_absolute_cutoff(&normalized, threshold, config.min_results)
        }
        CutoffStrategy::ScoreCliff { max_drop_ratio } => {
            find_cliff_cutoff(&normalized, *max_drop_ratio, config.min_results)
        }
        CutoffStrategy::Elbow { sensitivity } => {
            find_elbow_cutoff(&normalized, *sensitivity, config.min_results)
        }
        CutoffStrategy::Combined {
            relative_threshold,
            max_drop_ratio,
            absolute_min,
        } => find_combined_cutoff(
            &normalized,
            normalized[0],
            *relative_threshold,
            *max_drop_ratio,
            *absolute_min,
            config.min_results,
        ),
    };

    (cutoff.min(capped.len()), trigger, normalized)
}

fn find_absolute_cutoff(scores: &[f32], min_score: f32, min_results: usize) -> (usize, String) {
    for (index, score) in scores.iter().enumerate() {
        if index >= min_results && *score < min_score {
            return (index, "absolute_threshold".to_string());
        }
    }
    (scores.len(), "no_cutoff".to_string())
}

fn find_cliff_cutoff(scores: &[f32], max_drop_ratio: f32, min_results: usize) -> (usize, String) {
    for index in 1..scores.len() {
        if index < min_results {
            continue;
        }

        let prev = scores[index - 1];
        let curr = scores[index];
        if prev > f32::EPSILON {
            let drop_ratio = (prev - curr) / prev;
            if drop_ratio > max_drop_ratio {
                return (index, format!("score_cliff({:.1}%)", drop_ratio * 100.0));
            }
        }
    }

    (scores.len(), "no_cutoff".to_string())
}

fn find_elbow_cutoff(scores: &[f32], sensitivity: f32, min_results: usize) -> (usize, String) {
    if scores.len() < 3 {
        return (scores.len(), "too_few_points".to_string());
    }

    let len = scores.len();
    let x_norm: Vec<f32> = (0..len)
        .map(|index| index as f32 / (len.saturating_sub(1)) as f32)
        .collect();
    let x1 = x_norm[0];
    let y1 = scores[0];
    let x2 = x_norm[len - 1];
    let y2 = scores[len - 1];
    let line_len = ((x2 - x1).powi(2) + (y2 - y1).powi(2)).sqrt();
    if line_len < f32::EPSILON {
        return (scores.len(), "flat_curve".to_string());
    }

    let mut max_distance = 0.0f32;
    let mut elbow_index = min_results;

    for index in min_results..len.saturating_sub(1) {
        let x0 = x_norm[index];
        let y0 = scores[index];
        let distance = ((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1).abs() / line_len;
        let adjusted_distance = distance * (1.0 + sensitivity * (1.0 - x_norm[index]));
        if adjusted_distance > max_distance {
            max_distance = adjusted_distance;
            elbow_index = index;
        }
    }

    if max_distance > 0.05 * sensitivity {
        (elbow_index + 1, "elbow_detection".to_string())
    } else {
        (scores.len(), "no_significant_elbow".to_string())
    }
}

fn find_combined_cutoff(
    scores: &[f32],
    top_score: f32,
    relative_threshold: f32,
    max_drop_ratio: f32,
    absolute_min: f32,
    min_results: usize,
) -> (usize, String) {
    let relative_min = top_score * relative_threshold;

    for index in 0..scores.len() {
        if index < min_results {
            continue;
        }

        let score = scores[index];
        if score < absolute_min {
            return (index, "absolute_min".to_string());
        }

        if score < relative_min {
            return (index, "relative_threshold".to_string());
        }

        if index > 0 {
            let prev = scores[index - 1];
            if prev > f32::EPSILON {
                let drop_ratio = (prev - score) / prev;
                if drop_ratio > max_drop_ratio {
                    return (index, format!("score_cliff({:.1}%)", drop_ratio * 100.0));
                }
            }
        }
    }

    (scores.len(), "no_cutoff".to_string())
}

#[cfg(test)]
mod tests {
    use super::{find_adaptive_cutoff, normalize_scores, AdaptiveConfig, CutoffStrategy};

    #[test]
    fn normalizes_scores_into_zero_to_one_range() {
        let scores = normalize_scores(&[0.2, 0.5, 0.8]);
        assert!((scores[0] - 0.0).abs() < 0.001);
        assert!((scores[2] - 1.0).abs() < 0.001);
    }

    #[test]
    fn combined_cutoff_stops_on_relative_threshold() {
        let config = AdaptiveConfig {
            min_results: 2,
            max_results: 10,
            strategy: CutoffStrategy::Combined {
                relative_threshold: 0.5,
                max_drop_ratio: 0.9,
                absolute_min: 0.1,
            },
            ..AdaptiveConfig::default()
        };

        let (cutoff, trigger, _normalized) =
            find_adaptive_cutoff(&[0.92, 0.9, 0.86, 0.47, 0.04], &config);

        assert_eq!(cutoff, 3);
        assert_eq!(trigger, "relative_threshold");
    }

    #[test]
    fn score_cliff_detects_large_drop() {
        let config = AdaptiveConfig {
            min_results: 2,
            strategy: CutoffStrategy::ScoreCliff {
                max_drop_ratio: 0.4,
            },
            ..AdaptiveConfig::default()
        };

        let (cutoff, trigger, _normalized) =
            find_adaptive_cutoff(&[1.0, 0.96, 0.9, 0.42, 0.4], &config);

        assert_eq!(cutoff, 3);
        assert!(trigger.starts_with("score_cliff"));
    }

    #[test]
    fn disabled_config_returns_all_capped_results() {
        let config = AdaptiveConfig {
            enabled: false,
            max_results: 3,
            ..AdaptiveConfig::default()
        };

        let (cutoff, trigger, normalized) =
            find_adaptive_cutoff(&[0.9, 0.7, 0.5, 0.3, 0.1], &config);

        assert_eq!(cutoff, 3);
        assert_eq!(trigger, "disabled");
        assert_eq!(normalized.len(), 3);
    }
}
