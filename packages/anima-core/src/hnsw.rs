//! HNSW (Hierarchical Navigable Small World) vector index.
//!
//! Thin wrapper over the `instant-distance` crate providing:
//! - Insert/search/remove for high-dimensional embeddings
//! - Custom SIMD-accelerated distance function
//! - Support for 384-dim (nomic-embed) and 1536-dim (OpenAI) vectors

use std::collections::HashMap;

use crate::simd;

/// A point in the HNSW index storing an embedding vector.
#[derive(Clone, Debug)]
struct EmbeddingPoint {
    embedding: Vec<f32>,
}

impl instant_distance::Point for EmbeddingPoint {
    fn distance(&self, other: &Self) -> f32 {
        simd::cosine_distance(&self.embedding, &other.embedding)
    }
}

/// Result from a vector search.
#[derive(Debug, Clone)]
pub struct VectorSearchResult {
    /// Frame ID of the matched vector.
    pub frame_id: u64,
    /// Distance from query (lower = more similar for cosine distance).
    pub distance: f32,
}

/// HNSW approximate nearest neighbor index.
///
/// Wraps `instant-distance` with frame ID mapping and SIMD distance.
pub struct HnswIndex {
    /// Expected dimensionality of vectors.
    dimensions: usize,
    /// Frame ID to embedding mapping (source of truth).
    embeddings: HashMap<u64, Vec<f32>>,
    /// The built HNSW graph (rebuilt after mutations).
    hnsw: Option<instant_distance::HnswMap<EmbeddingPoint, u64>>,
    /// Whether the index needs rebuilding.
    dirty: bool,
}

impl HnswIndex {
    /// Create a new empty index for vectors of the given dimensionality.
    pub fn new(dimensions: usize) -> Self {
        Self {
            dimensions,
            embeddings: HashMap::new(),
            hnsw: None,
            dirty: false,
        }
    }

    /// Number of vectors in the index.
    #[must_use]
    pub fn len(&self) -> usize {
        self.embeddings.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.embeddings.is_empty()
    }

    /// Expected dimensionality.
    #[must_use]
    pub fn dimensions(&self) -> usize {
        self.dimensions
    }

    /// Insert a vector associated with a frame ID.
    ///
    /// If a vector already exists for this frame ID, it is replaced.
    /// The index is marked dirty and must be rebuilt before searching.
    pub fn insert(&mut self, frame_id: u64, embedding: Vec<f32>) -> crate::Result<()> {
        if embedding.len() != self.dimensions {
            return Err(crate::Error::Search(format!(
                "expected {}-dim vector, got {}-dim",
                self.dimensions,
                embedding.len()
            )));
        }
        self.embeddings.insert(frame_id, embedding);
        self.dirty = true;
        Ok(())
    }

    /// Remove a vector by frame ID.
    pub fn remove(&mut self, frame_id: u64) -> bool {
        let removed = self.embeddings.remove(&frame_id).is_some();
        if removed {
            self.dirty = true;
        }
        removed
    }

    /// Rebuild the HNSW graph from current embeddings.
    ///
    /// Must be called after inserts/removes and before searching.
    pub fn rebuild(&mut self) {
        if self.embeddings.is_empty() {
            self.hnsw = None;
            self.dirty = false;
            return;
        }

        let mut points = Vec::with_capacity(self.embeddings.len());
        let mut values = Vec::with_capacity(self.embeddings.len());

        for (&frame_id, embedding) in &self.embeddings {
            points.push(EmbeddingPoint {
                embedding: embedding.clone(),
            });
            values.push(frame_id);
        }

        let seed = 42u64;
        let map = instant_distance::Builder::default()
            .seed(seed)
            .build(points, values);

        self.hnsw = Some(map);
        self.dirty = false;
    }

    /// Search for the k nearest neighbors to the query vector.
    ///
    /// Automatically rebuilds the index if dirty.
    pub fn search(&mut self, query: &[f32], k: usize) -> crate::Result<Vec<VectorSearchResult>> {
        if query.len() != self.dimensions {
            return Err(crate::Error::Search(format!(
                "query is {}-dim, index expects {}-dim",
                query.len(),
                self.dimensions
            )));
        }

        if self.dirty {
            self.rebuild();
        }

        let hnsw = match &self.hnsw {
            Some(h) => h,
            None => return Ok(vec![]),
        };

        let query_point = EmbeddingPoint {
            embedding: query.to_vec(),
        };

        let mut search = instant_distance::Search::default();
        let results: Vec<VectorSearchResult> = hnsw
            .search(&query_point, &mut search)
            .take(k)
            .map(|item| VectorSearchResult {
                frame_id: *item.value,
                distance: item.distance,
            })
            .collect();

        Ok(results)
    }

    /// Batch insert multiple vectors and rebuild once.
    pub fn insert_batch(
        &mut self,
        items: impl IntoIterator<Item = (u64, Vec<f32>)>,
    ) -> crate::Result<usize> {
        let mut count = 0;
        for (frame_id, embedding) in items {
            if embedding.len() != self.dimensions {
                return Err(crate::Error::Search(format!(
                    "expected {}-dim vector, got {}-dim for frame {}",
                    self.dimensions,
                    embedding.len(),
                    frame_id
                )));
            }
            self.embeddings.insert(frame_id, embedding);
            count += 1;
        }
        if count > 0 {
            self.dirty = true;
            self.rebuild();
        }
        Ok(count)
    }

    /// Get the raw embedding for a frame ID.
    #[must_use]
    pub fn get_embedding(&self, frame_id: u64) -> Option<&Vec<f32>> {
        self.embeddings.get(&frame_id)
    }

    /// Check if the index contains a vector for this frame ID.
    #[must_use]
    pub fn contains(&self, frame_id: u64) -> bool {
        self.embeddings.contains_key(&frame_id)
    }
}

impl std::fmt::Debug for HnswIndex {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HnswIndex")
            .field("dimensions", &self.dimensions)
            .field("size", &self.embeddings.len())
            .field("dirty", &self.dirty)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn random_vector(dim: usize, seed: u64) -> Vec<f32> {
        // Simple deterministic pseudo-random
        let mut v = Vec::with_capacity(dim);
        let mut state = seed;
        for _ in 0..dim {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            v.push(((state >> 33) as f32) / (u32::MAX as f32) - 0.5);
        }
        v
    }

    #[test]
    fn test_empty_index() {
        let mut index = HnswIndex::new(384);
        assert!(index.is_empty());
        let results = index.search(&random_vector(384, 1), 10).unwrap();
        assert!(results.is_empty());
    }

    #[test]
    fn test_insert_and_search() {
        let mut index = HnswIndex::new(8);
        let v1 = vec![1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        let v2 = vec![0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        let v3 = vec![0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0];

        index.insert(1, v1.clone()).unwrap();
        index.insert(2, v2).unwrap();
        index.insert(3, v3).unwrap();

        let results = index.search(&v1, 2).unwrap();
        assert_eq!(results.len(), 2);
        // Closest should be frame 1 (identical) or frame 2 (very similar)
        assert!(results[0].frame_id == 1 || results[0].frame_id == 2);
    }

    #[test]
    fn test_dimension_mismatch() {
        let mut index = HnswIndex::new(8);
        let wrong = vec![1.0, 2.0, 3.0]; // 3-dim, expected 8
        assert!(index.insert(1, wrong).is_err());
    }

    #[test]
    fn test_remove() {
        let mut index = HnswIndex::new(4);
        index.insert(1, vec![1.0, 0.0, 0.0, 0.0]).unwrap();
        index.insert(2, vec![0.0, 1.0, 0.0, 0.0]).unwrap();
        assert_eq!(index.len(), 2);

        assert!(index.remove(1));
        assert_eq!(index.len(), 1);
        assert!(!index.contains(1));
        assert!(index.contains(2));
    }

    #[test]
    fn test_batch_insert() {
        let mut index = HnswIndex::new(4);
        let items: Vec<(u64, Vec<f32>)> = (0..100).map(|i| (i, random_vector(4, i))).collect();

        let count = index.insert_batch(items).unwrap();
        assert_eq!(count, 100);
        assert_eq!(index.len(), 100);

        // Search should work immediately (batch rebuilds)
        let results = index.search(&random_vector(4, 0), 5).unwrap();
        assert_eq!(results.len(), 5);
    }

    #[test]
    fn test_384_dim_recall() {
        let mut index = HnswIndex::new(384);

        // Insert 100 vectors
        for i in 0..100u64 {
            index.insert(i, random_vector(384, i)).unwrap();
        }

        // Query with vector 0 — should find itself as nearest
        let query = random_vector(384, 0);
        let results = index.search(&query, 1).unwrap();
        assert!(!results.is_empty());
        assert_eq!(results[0].frame_id, 0, "should find the identical vector");
        assert!(results[0].distance < 0.01, "distance to self should be ~0");
    }
}
