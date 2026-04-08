//! SIMD-accelerated distance calculations for vector search.
//!
//! Provides optimized L2 (Euclidean) and cosine distance functions using
//! the `wide` crate for portable SIMD across x86_64 (AVX2) and aarch64 (NEON).
//! Falls back to scalar when the `simd` feature is disabled.
//!
//! Adapted from memvid's SIMD kernels, extended with cosine similarity.

#[cfg(feature = "simd")]
use wide::f32x8;

// ── L2 Distance ──────────────────────────────────────────────────────

/// Compute squared L2 distance between two f32 slices using SIMD.
///
/// Uses 8-wide SIMD lanes. Falls back to scalar for remainder elements.
#[cfg(feature = "simd")]
#[must_use]
pub fn l2_distance_squared(a: &[f32], b: &[f32]) -> f32 {
    debug_assert_eq!(a.len(), b.len(), "vectors must have same length");

    let len = a.len();
    let chunks = len / 8;
    let remainder = len % 8;

    let mut sum = f32x8::ZERO;

    for i in 0..chunks {
        let offset = i * 8;
        let a_chunk = f32x8::new([
            a[offset],
            a[offset + 1],
            a[offset + 2],
            a[offset + 3],
            a[offset + 4],
            a[offset + 5],
            a[offset + 6],
            a[offset + 7],
        ]);
        let b_chunk = f32x8::new([
            b[offset],
            b[offset + 1],
            b[offset + 2],
            b[offset + 3],
            b[offset + 4],
            b[offset + 5],
            b[offset + 6],
            b[offset + 7],
        ]);
        let diff = a_chunk - b_chunk;
        sum += diff * diff;
    }

    let sum_array: [f32; 8] = sum.into();
    let mut total: f32 = sum_array.iter().sum();

    let offset = chunks * 8;
    for i in 0..remainder {
        let diff = a[offset + i] - b[offset + i];
        total += diff * diff;
    }

    total
}

/// Compute L2 distance (with sqrt) using SIMD.
#[cfg(feature = "simd")]
#[must_use]
pub fn l2_distance(a: &[f32], b: &[f32]) -> f32 {
    l2_distance_squared(a, b).sqrt()
}

// ── Cosine Similarity ────────────────────────────────────────────────

/// Compute cosine similarity between two f32 slices using SIMD.
///
/// Returns value in [-1.0, 1.0]. Uses 8-wide SIMD for dot product and norms.
#[cfg(feature = "simd")]
#[must_use]
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    debug_assert_eq!(a.len(), b.len(), "vectors must have same length");

    let len = a.len();
    let chunks = len / 8;
    let remainder = len % 8;

    let mut dot = f32x8::ZERO;
    let mut norm_a = f32x8::ZERO;
    let mut norm_b = f32x8::ZERO;

    for i in 0..chunks {
        let offset = i * 8;
        let a_chunk = f32x8::new([
            a[offset],
            a[offset + 1],
            a[offset + 2],
            a[offset + 3],
            a[offset + 4],
            a[offset + 5],
            a[offset + 6],
            a[offset + 7],
        ]);
        let b_chunk = f32x8::new([
            b[offset],
            b[offset + 1],
            b[offset + 2],
            b[offset + 3],
            b[offset + 4],
            b[offset + 5],
            b[offset + 6],
            b[offset + 7],
        ]);
        dot += a_chunk * b_chunk;
        norm_a += a_chunk * a_chunk;
        norm_b += b_chunk * b_chunk;
    }

    let dot_arr: [f32; 8] = dot.into();
    let na_arr: [f32; 8] = norm_a.into();
    let nb_arr: [f32; 8] = norm_b.into();

    let mut dot_total: f32 = dot_arr.iter().sum();
    let mut na_total: f32 = na_arr.iter().sum();
    let mut nb_total: f32 = nb_arr.iter().sum();

    let offset = chunks * 8;
    for i in 0..remainder {
        dot_total += a[offset + i] * b[offset + i];
        na_total += a[offset + i] * a[offset + i];
        nb_total += b[offset + i] * b[offset + i];
    }

    let denom = (na_total * nb_total).sqrt();
    if denom < f32::EPSILON {
        return 0.0;
    }
    dot_total / denom
}

/// Cosine distance = 1 - cosine_similarity. In [0.0, 2.0].
#[cfg(feature = "simd")]
#[must_use]
pub fn cosine_distance(a: &[f32], b: &[f32]) -> f32 {
    1.0 - cosine_similarity(a, b)
}

// ── Scalar Fallbacks ─────────────────────────────────────────────────

#[cfg(not(feature = "simd"))]
#[must_use]
pub fn l2_distance_squared(a: &[f32], b: &[f32]) -> f32 {
    a.iter()
        .zip(b.iter())
        .map(|(x, y)| {
            let diff = x - y;
            diff * diff
        })
        .sum()
}

#[cfg(not(feature = "simd"))]
#[must_use]
pub fn l2_distance(a: &[f32], b: &[f32]) -> f32 {
    l2_distance_squared(a, b).sqrt()
}

#[cfg(not(feature = "simd"))]
#[must_use]
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let na: f32 = a.iter().map(|x| x * x).sum();
    let nb: f32 = b.iter().map(|x| x * x).sum();
    let denom = (na * nb).sqrt();
    if denom < f32::EPSILON {
        return 0.0;
    }
    dot / denom
}

#[cfg(not(feature = "simd"))]
#[must_use]
pub fn cosine_distance(a: &[f32], b: &[f32]) -> f32 {
    1.0 - cosine_similarity(a, b)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_l2_distance_squared_basic() {
        let a = [0.0, 0.0, 0.0];
        let b = [3.0, 4.0, 0.0];
        let dist_sq = l2_distance_squared(&a, &b);
        assert!(
            (dist_sq - 25.0).abs() < 1e-6,
            "expected 25.0, got {dist_sq}"
        );
    }

    #[test]
    fn test_l2_distance_basic() {
        let a = [0.0, 0.0];
        let b = [3.0, 4.0];
        let dist = l2_distance(&a, &b);
        assert!((dist - 5.0).abs() < 1e-6, "expected 5.0, got {dist}");
    }

    #[test]
    fn test_cosine_similarity_identical() {
        let a = [1.0, 2.0, 3.0];
        let sim = cosine_similarity(&a, &a);
        assert!((sim - 1.0).abs() < 1e-6, "expected 1.0, got {sim}");
    }

    #[test]
    fn test_cosine_similarity_orthogonal() {
        let a = [1.0, 0.0];
        let b = [0.0, 1.0];
        let sim = cosine_similarity(&a, &b);
        assert!(sim.abs() < 1e-6, "expected 0.0, got {sim}");
    }

    #[test]
    fn test_cosine_similarity_opposite() {
        let a = [1.0, 0.0];
        let b = [-1.0, 0.0];
        let sim = cosine_similarity(&a, &b);
        assert!((sim - (-1.0)).abs() < 1e-6, "expected -1.0, got {sim}");
    }

    #[test]
    fn test_cosine_distance_range() {
        let a = [1.0, 2.0, 3.0];
        let b = [4.0, 5.0, 6.0];
        let dist = cosine_distance(&a, &b);
        assert!(
            dist >= 0.0 && dist <= 2.0,
            "cosine distance out of range: {dist}"
        );
    }

    #[test]
    fn test_zero_vector_safety() {
        let a = [0.0, 0.0, 0.0];
        let b = [1.0, 2.0, 3.0];
        let sim = cosine_similarity(&a, &b);
        assert_eq!(sim, 0.0, "zero vector should return 0.0 similarity");
    }

    #[test]
    fn test_384_dim_simd_vs_scalar() {
        let a: Vec<f32> = (0..384).map(|i| (i as f32) * 0.01).collect();
        let b: Vec<f32> = (0..384).map(|i| ((i + 1) as f32) * 0.01).collect();

        let l2_result = l2_distance(&a, &b);
        let scalar_l2: f32 = a
            .iter()
            .zip(b.iter())
            .map(|(x, y)| (x - y).powi(2))
            .sum::<f32>()
            .sqrt();
        assert!(
            (l2_result - scalar_l2).abs() < 1e-4,
            "L2: SIMD {l2_result} vs scalar {scalar_l2}"
        );

        let cos_result = cosine_similarity(&a, &b);
        let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
        let na: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
        let nb: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
        let scalar_cos = dot / (na * nb);
        assert!(
            (cos_result - scalar_cos).abs() < 1e-4,
            "Cosine: SIMD {cos_result} vs scalar {scalar_cos}"
        );
    }

    #[test]
    fn test_1536_dim() {
        let a: Vec<f32> = (0..1536).map(|i| ((i % 100) as f32) * 0.001).collect();
        let b: Vec<f32> = (0..1536).map(|i| ((i % 100 + 1) as f32) * 0.001).collect();

        let dist = l2_distance(&a, &b);
        assert!(dist > 0.0, "distance should be positive");

        let sim = cosine_similarity(&a, &b);
        assert!(
            sim > 0.0 && sim <= 1.0,
            "similarity should be in (0, 1]: {sim}"
        );
    }
}
