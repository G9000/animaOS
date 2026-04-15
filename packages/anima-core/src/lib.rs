//! anima-core: High-performance memory infrastructure for animaOS.
//!
//! This crate provides the Rust-native hot path for animaOS's cognitive memory system:
//! - SIMD-accelerated vector distance calculations
//! - HNSW approximate nearest neighbor search
//! - BM25 lexical search with optional Tantivy backend
//! - Hybrid search with Reciprocal Rank Fusion
//! - Adaptive retrieval cutoff strategies for dynamic context sizing
//! - Versioned Memory Cards with cardinality-aware schema
//! - In-memory Knowledge Graph with multi-hop traversal
//! - Portable encrypted `.anima` soul capsule export/import
//! - Structural text chunking with semantic boundary awareness
//! - Rules-based memory extraction (30+ regex patterns)
//! - Rules-based triplet extraction for graph ingestion
//! - Unicode-aware text normalization and PDF spacing cleanup
//! - Temporal date parsing for episodic memory
//! - Decision replay traces for agent debugging

pub mod adaptive;
pub mod capsule;
pub mod cards;
pub mod chunker;
pub mod enrich;
pub mod engine;
pub mod integrity;
pub mod frame;
pub mod graph;
pub mod projection;
pub mod retrieval_index;
pub mod search;
pub mod simd;
#[cfg(feature = "temporal")]
pub mod temporal;
pub mod text;
pub mod triplet;

#[cfg(feature = "hnsw")]
pub mod hnsw;

#[cfg(feature = "lex")]
pub mod lex;

#[cfg(feature = "replay")]
pub mod replay;

pub mod path_engine;

#[cfg(feature = "python")]
mod ffi;

/// Crate-level error type.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("frame error: {0}")]
    Frame(String),

    #[error("card error: {0}")]
    Card(String),

    #[error("graph error: {0}")]
    Graph(String),

    #[error("search error: {0}")]
    Search(String),

    #[error("capsule error: {0}")]
    Capsule(String),

    #[error("serialization error: {0}")]
    Serialization(String),

    #[error("encryption error: {0}")]
    Encryption(String),

    #[error("storage error: {0}")]
    Storage(String),

    #[error("lock conflict: {0}")]
    LockConflict(String),

    #[error("io error: {0}")]
    Io(String),

    #[error("{0}")]
    Other(String),
}

pub type Result<T> = std::result::Result<T, Error>;
