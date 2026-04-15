//! BM25 lexical search index.
//!
//! When the `lex` feature is enabled, uses Tantivy for full BM25Okapi ranking.
//! Otherwise, provides a simple in-memory token-frequency BM25 implementation
//! (matching animaOS's current bm25_index.py behavior).

use std::collections::HashMap;

use crate::frame::FrameId;
use serde::{Deserialize, Serialize};

/// Result from a lexical search.
#[derive(Debug, Clone)]
pub struct LexSearchResult {
    pub frame_id: FrameId,
    pub score: f32,
}

// ── Simple BM25 (no Tantivy) ────────────────────────────────────────

/// Simple in-memory BM25 index using token frequency.
///
/// Matches animaOS's current `bm25_index.py` behavior:
/// simple whitespace tokenization + lowercase.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SimpleBm25Index {
    /// frame_id → tokenized content
    documents: HashMap<FrameId, Vec<String>>,
    /// token → set of frame_ids containing it
    inverted_index: HashMap<String, Vec<FrameId>>,
    /// Average document length (in tokens).
    avg_dl: f32,
    /// BM25 parameters.
    k1: f32,
    b: f32,
}

impl SimpleBm25Index {
    /// Create a new empty BM25 index with default parameters.
    pub fn new() -> Self {
        Self {
            documents: HashMap::new(),
            inverted_index: HashMap::new(),
            avg_dl: 0.0,
            k1: 1.5,
            b: 0.75,
        }
    }

    /// Tokenize text: lowercase + split on whitespace + alphanumeric filter.
    fn tokenize(text: &str) -> Vec<String> {
        text.to_lowercase()
            .split_whitespace()
            .map(|w| {
                w.chars()
                    .filter(|c| c.is_alphanumeric())
                    .collect::<String>()
            })
            .filter(|w| !w.is_empty())
            .collect()
    }

    /// Add a document to the index.
    pub fn add_document(&mut self, frame_id: FrameId, content: &str) {
        if self.documents.contains_key(&frame_id) {
            self.remove_document(frame_id);
        }

        let tokens = Self::tokenize(content);

        // Update inverted index (once per unique token per document)
        let unique_tokens: std::collections::HashSet<&String> = tokens.iter().collect();
        for token in unique_tokens {
            self.inverted_index
                .entry(token.clone())
                .or_default()
                .push(frame_id);
        }

        self.documents.insert(frame_id, tokens);
        self.recalc_avg_dl();
    }

    /// Remove a document from the index.
    pub fn remove_document(&mut self, frame_id: FrameId) {
        if let Some(tokens) = self.documents.remove(&frame_id) {
            for token in &tokens {
                if let Some(ids) = self.inverted_index.get_mut(token) {
                    ids.retain(|&id| id != frame_id);
                    if ids.is_empty() {
                        self.inverted_index.remove(token);
                    }
                }
            }
            self.recalc_avg_dl();
        }
    }

    fn recalc_avg_dl(&mut self) {
        if self.documents.is_empty() {
            self.avg_dl = 0.0;
        } else {
            let total: usize = self.documents.values().map(|d| d.len()).sum();
            self.avg_dl = total as f32 / self.documents.len() as f32;
        }
    }

    /// Search the index with a text query. Returns results sorted by BM25 score.
    pub fn search(&self, query: &str, k: usize) -> Vec<LexSearchResult> {
        let query_tokens = Self::tokenize(query);
        if query_tokens.is_empty() || self.documents.is_empty() {
            return vec![];
        }

        let n = self.documents.len() as f32;
        let mut scores: HashMap<FrameId, f32> = HashMap::new();

        for token in &query_tokens {
            let df = self
                .inverted_index
                .get(token)
                .map(|ids| ids.len())
                .unwrap_or(0) as f32;

            if df == 0.0 {
                continue;
            }

            // IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            let idf = ((n - df + 0.5) / (df + 0.5) + 1.0).ln();

            // Score each document containing this token
            if let Some(doc_ids) = self.inverted_index.get(token) {
                for &frame_id in doc_ids {
                    if let Some(doc_tokens) = self.documents.get(&frame_id) {
                        let tf = doc_tokens.iter().filter(|t| *t == token).count() as f32;
                        let dl = doc_tokens.len() as f32;

                        // BM25: idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
                        let numerator = tf * (self.k1 + 1.0);
                        let denominator =
                            tf + self.k1 * (1.0 - self.b + self.b * dl / self.avg_dl.max(1.0));

                        *scores.entry(frame_id).or_insert(0.0) += idf * numerator / denominator;
                    }
                }
            }
        }

        let mut results: Vec<LexSearchResult> = scores
            .into_iter()
            .map(|(frame_id, score)| LexSearchResult { frame_id, score })
            .collect();

        results.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        results.truncate(k);
        results
    }

    /// Number of documents in the index.
    #[must_use]
    pub fn len(&self) -> usize {
        self.documents.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.documents.is_empty()
    }

    /// Rebuild the entire index from scratch.
    pub fn rebuild(&mut self, documents: impl IntoIterator<Item = (FrameId, String)>) {
        self.documents.clear();
        self.inverted_index.clear();

        for (frame_id, content) in documents {
            let tokens = Self::tokenize(&content);
            let unique_tokens: std::collections::HashSet<&String> = tokens.iter().collect();
            for token in unique_tokens {
                self.inverted_index
                    .entry(token.clone())
                    .or_default()
                    .push(frame_id);
            }
            self.documents.insert(frame_id, tokens);
        }

        self.recalc_avg_dl();
    }
}

impl Default for SimpleBm25Index {
    fn default() -> Self {
        Self::new()
    }
}

// ── Tantivy-backed BM25 ─────────────────────────────────────────────

#[cfg(feature = "lex")]
pub mod tantivy_index {
    use super::*;
    use tantivy::collector::TopDocs;
    use tantivy::query::QueryParser;
    use tantivy::schema::{Schema, Value, STORED, TEXT};
    use tantivy::{doc, Index, ReloadPolicy};

    /// Full-text search index backed by Tantivy.
    pub struct TantivyLexIndex {
        index: Index,
        schema: Schema,
        content_field: tantivy::schema::Field,
        id_field: tantivy::schema::Field,
        reader: tantivy::IndexReader,
        writer: Option<tantivy::IndexWriter>,
    }

    impl TantivyLexIndex {
        /// Create a new in-memory Tantivy index.
        pub fn new() -> crate::Result<Self> {
            let mut schema_builder = Schema::builder();
            let id_field = schema_builder.add_u64_field("frame_id", STORED);
            let content_field = schema_builder.add_text_field("content", TEXT | STORED);
            let schema = schema_builder.build();

            let index = Index::create_in_ram(schema.clone());

            let reader = index
                .reader_builder()
                .reload_policy(ReloadPolicy::Manual)
                .try_into()
                .map_err(|e| crate::Error::Search(format!("tantivy reader: {e}")))?;

            let writer = index
                .writer(50_000_000) // 50MB heap
                .map_err(|e| crate::Error::Search(format!("tantivy writer: {e}")))?;

            Ok(Self {
                index,
                schema,
                content_field,
                id_field,
                reader,
                writer: Some(writer),
            })
        }

        /// Add a document to the index.
        pub fn add_document(&mut self, frame_id: FrameId, content: &str) -> crate::Result<()> {
            let writer = self
                .writer
                .as_ref()
                .ok_or_else(|| crate::Error::Search("index writer closed".into()))?;

            writer
                .add_document(doc!(
                    self.id_field => frame_id,
                    self.content_field => content,
                ))
                .map_err(|e| crate::Error::Search(format!("add doc: {e}")))?;

            Ok(())
        }

        /// Commit pending writes and reload the reader.
        pub fn commit(&mut self) -> crate::Result<()> {
            if let Some(ref mut writer) = self.writer {
                writer
                    .commit()
                    .map_err(|e| crate::Error::Search(format!("commit: {e}")))?;
            }
            self.reader
                .reload()
                .map_err(|e| crate::Error::Search(format!("reload: {e}")))?;
            Ok(())
        }

        /// Search using BM25 ranking.
        pub fn search(&self, query: &str, k: usize) -> crate::Result<Vec<LexSearchResult>> {
            let searcher = self.reader.searcher();
            let query_parser = QueryParser::for_index(&self.index, vec![self.content_field]);

            let parsed = query_parser
                .parse_query(query)
                .map_err(|e| crate::Error::Search(format!("parse query: {e}")))?;

            let top_docs = searcher
                .search(&parsed, &TopDocs::with_limit(k))
                .map_err(|e| crate::Error::Search(format!("search: {e}")))?;

            let mut results = Vec::with_capacity(top_docs.len());
            for (score, doc_address) in top_docs {
                let doc: tantivy::TantivyDocument = searcher
                    .doc(doc_address)
                    .map_err(|e| crate::Error::Search(format!("retrieve doc: {e}")))?;

                if let Some(id_value) = doc.get_first(self.id_field) {
                    if let Some(id) = id_value.as_u64() {
                        results.push(LexSearchResult {
                            frame_id: id,
                            score,
                        });
                    }
                }
            }

            Ok(results)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_bm25_basic() {
        let mut index = SimpleBm25Index::new();
        index.add_document(1, "the quick brown fox jumps over the lazy dog");
        index.add_document(2, "the quick brown cat sits on the mat");
        index.add_document(3, "a completely unrelated document about rust programming");

        let results = index.search("quick brown", 10);
        assert!(results.len() >= 2);
        // Doc 1 and 2 should both match
        let ids: Vec<u64> = results.iter().map(|r| r.frame_id).collect();
        assert!(ids.contains(&1));
        assert!(ids.contains(&2));
    }

    #[test]
    fn test_simple_bm25_empty() {
        let index = SimpleBm25Index::new();
        let results = index.search("anything", 10);
        assert!(results.is_empty());
    }

    #[test]
    fn test_simple_bm25_no_match() {
        let mut index = SimpleBm25Index::new();
        index.add_document(1, "hello world");
        let results = index.search("xyzzyfoo", 10);
        assert!(results.is_empty());
    }

    #[test]
    fn test_simple_bm25_remove() {
        let mut index = SimpleBm25Index::new();
        index.add_document(1, "hello world");
        index.add_document(2, "hello earth");

        index.remove_document(1);
        let results = index.search("hello", 10);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].frame_id, 2);
    }

    #[test]
    fn test_simple_bm25_rebuild() {
        let mut index = SimpleBm25Index::new();
        index.rebuild(vec![
            (1, "alpha beta gamma".into()),
            (2, "delta epsilon zeta".into()),
            (3, "alpha delta theta".into()),
        ]);

        assert_eq!(index.len(), 3);
        let results = index.search("alpha", 10);
        assert_eq!(results.len(), 2);
    }

    #[test]
    fn test_simple_bm25_replace_existing_document_removes_stale_matches() {
        let mut index = SimpleBm25Index::new();
        index.add_document(1, "alpha beta");
        index.add_document(1, "gamma delta");

        assert!(
            index.search("alpha", 10).is_empty(),
            "replaced document should not match stale tokens"
        );

        let results = index.search("gamma", 10);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].frame_id, 1);
    }

    #[test]
    fn test_bm25_ranking_order() {
        let mut index = SimpleBm25Index::new();
        // Doc 1 has "rust" once in 4 words
        index.add_document(1, "I love rust programming");
        // Doc 2 has "rust" twice in 6 words — higher tf but also longer
        index.add_document(2, "rust is great rust is fast");

        let results = index.search("rust", 10);
        assert_eq!(results.len(), 2);
        // Both docs should be returned and scores should be > 0
        assert!(results[0].score > 0.0, "score[0] = {}", results[0].score);
        assert!(results[1].score > 0.0, "score[1] = {}", results[1].score);
        // First result should have higher or equal score
        assert!(results[0].score >= results[1].score);
    }
}
