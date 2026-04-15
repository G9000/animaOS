use std::fs;
use std::io::Write;
use std::path::Path;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::lex::SimpleBm25Index;

const RETRIEVAL_MANIFEST_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IndexFamily {
    Memory,
    Transcript,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RetrievalFamilyState {
    pub generation: u64,
    pub dirty: bool,
}

impl Default for RetrievalFamilyState {
    fn default() -> Self {
        Self {
            generation: 0,
            dirty: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RetrievalFamilies {
    pub memory: RetrievalFamilyState,
    pub transcript: RetrievalFamilyState,
}

impl Default for RetrievalFamilies {
    fn default() -> Self {
        Self {
            memory: RetrievalFamilyState::default(),
            transcript: RetrievalFamilyState::default(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RetrievalManifest {
    pub version: u32,
    pub families: RetrievalFamilies,
}

impl Default for RetrievalManifest {
    fn default() -> Self {
        Self {
            version: RETRIEVAL_MANIFEST_VERSION,
            families: RetrievalFamilies::default(),
        }
    }
}

impl RetrievalManifest {
    fn family(&self, family: IndexFamily) -> &RetrievalFamilyState {
        match family {
            IndexFamily::Memory => &self.families.memory,
            IndexFamily::Transcript => &self.families.transcript,
        }
    }

    fn family_mut(&mut self, family: IndexFamily) -> &mut RetrievalFamilyState {
        match family {
            IndexFamily::Memory => &mut self.families.memory,
            IndexFamily::Transcript => &mut self.families.transcript,
        }
    }

    #[must_use]
    pub fn is_family_dirty(&self, family: IndexFamily) -> bool {
        self.family(family).dirty
    }

    pub fn mark_dirty(&mut self, family: IndexFamily) {
        self.family_mut(family).dirty = true;
    }

    pub fn clear_dirty(&mut self, family: IndexFamily) {
        self.family_mut(family).dirty = false;
    }

    pub fn bump_generation(&mut self, family: IndexFamily) -> u64 {
        let state = self.family_mut(family);
        state.generation += 1;
        state.generation
    }
}

pub fn load_manifest(path: &Path) -> crate::Result<RetrievalManifest> {
    let raw = fs::read_to_string(path)
        .map_err(|e| crate::Error::Io(format!("read retrieval manifest {}: {e}", path.display())))?;
    serde_json::from_str(&raw).map_err(|e| {
        crate::Error::Serialization(format!(
            "deserialize retrieval manifest {}: {e}",
            path.display()
        ))
    })
}

pub fn save_manifest(path: &Path, manifest: &RetrievalManifest) -> crate::Result<()> {
    let serialized = serde_json::to_string_pretty(manifest).map_err(|e| {
        crate::Error::Serialization(format!(
            "serialize retrieval manifest {}: {e}",
            path.display()
        ))
    })?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            crate::Error::Io(format!(
                "create retrieval manifest directory {}: {e}",
                parent.display()
            ))
        })?;
    }
    atomic_write(path, format!("{serialized}\n").as_bytes(), "retrieval manifest")
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MemoryIndexDocument {
    pub record_id: u64,
    pub user_id: u64,
    pub text: String,
    pub embedding: Option<Vec<f32>>,
    pub source_type: String,
    pub category: String,
    pub importance: u8,
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MemorySearchHit {
    pub record_id: u64,
    pub score: f32,
    pub source_type: String,
    pub category: String,
    pub importance: u8,
    pub created_at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TranscriptIndexDocument {
    pub thread_id: u64,
    pub user_id: u64,
    pub transcript_ref: String,
    pub summary: String,
    pub keywords: Vec<String>,
    pub text: String,
    pub date_start: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TranscriptSearchHit {
    pub thread_id: u64,
    pub transcript_ref: String,
    pub score: f32,
    pub date_start: i64,
}

fn manifest_path(root: &Path) -> std::path::PathBuf {
    root.join("manifest.json")
}

fn memory_documents_path(root: &Path) -> std::path::PathBuf {
    root.join("memory").join("documents.json")
}

fn transcript_documents_path(root: &Path) -> std::path::PathBuf {
    root.join("transcripts").join("documents.json")
}

fn load_or_default_manifest(root: &Path) -> crate::Result<RetrievalManifest> {
    let path = manifest_path(root);
    if !path.exists() {
        return Ok(RetrievalManifest::default());
    }
    load_manifest(&path)
}

fn save_root_manifest(root: &Path, manifest: &RetrievalManifest) -> crate::Result<()> {
    save_manifest(&manifest_path(root), manifest)
}

pub fn mark_family_dirty(root: &Path, family: IndexFamily) -> crate::Result<()> {
    let mut manifest = load_or_default_manifest(root)?;
    manifest.mark_dirty(family);
    save_root_manifest(root, &manifest)
}

pub fn clear_family_dirty(root: &Path, family: IndexFamily) -> crate::Result<()> {
    let mut manifest = load_or_default_manifest(root)?;
    manifest.clear_dirty(family);
    manifest.bump_generation(family);
    save_root_manifest(root, &manifest)
}

fn load_memory_documents(root: &Path) -> crate::Result<Vec<MemoryIndexDocument>> {
    let path = memory_documents_path(root);
    if !path.exists() {
        return Ok(Vec::new());
    }

    let raw = fs::read_to_string(&path).map_err(|e| {
        crate::Error::Io(format!("read memory index documents {}: {e}", path.display()))
    })?;
    serde_json::from_str(&raw).map_err(|e| {
        crate::Error::Serialization(format!(
            "deserialize memory index documents {}: {e}",
            path.display()
        ))
    })
}

fn save_memory_documents(root: &Path, documents: &[MemoryIndexDocument]) -> crate::Result<()> {
    let path = memory_documents_path(root);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            crate::Error::Io(format!(
                "create memory index directory {}: {e}",
                parent.display()
            ))
        })?;
    }

    let serialized = serde_json::to_string_pretty(documents).map_err(|e| {
        crate::Error::Serialization(format!(
            "serialize memory index documents {}: {e}",
            path.display()
        ))
    })?;
    atomic_write(&path, format!("{serialized}\n").as_bytes(), "memory index documents")
}

fn load_transcript_documents(root: &Path) -> crate::Result<Vec<TranscriptIndexDocument>> {
    let path = transcript_documents_path(root);
    if !path.exists() {
        return Ok(Vec::new());
    }

    let raw = fs::read_to_string(&path).map_err(|e| {
        crate::Error::Io(format!(
            "read transcript index documents {}: {e}",
            path.display()
        ))
    })?;
    serde_json::from_str(&raw).map_err(|e| {
        crate::Error::Serialization(format!(
            "deserialize transcript index documents {}: {e}",
            path.display()
        ))
    })
}

fn save_transcript_documents(root: &Path, documents: &[TranscriptIndexDocument]) -> crate::Result<()> {
    let path = transcript_documents_path(root);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            crate::Error::Io(format!(
                "create transcript index directory {}: {e}",
                parent.display()
            ))
        })?;
    }

    let serialized = serde_json::to_string_pretty(documents).map_err(|e| {
        crate::Error::Serialization(format!(
            "serialize transcript index documents {}: {e}",
            path.display()
        ))
    })?;
    atomic_write(
        &path,
        format!("{serialized}\n").as_bytes(),
        "transcript index documents",
    )
}

fn atomic_write(path: &Path, contents: &[u8], label: &str) -> crate::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            crate::Error::Io(format!("create {label} directory {}: {e}", parent.display()))
        })?;
    }

    let temp_path = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut temp_file = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temp_path)
        .map_err(|e| {
            crate::Error::Io(format!("create temporary {label} {}: {e}", temp_path.display()))
        })?;

    if let Err(err) = temp_file.write_all(contents) {
        let _ = fs::remove_file(&temp_path);
        return Err(crate::Error::Io(format!(
            "write temporary {label} {}: {err}",
            temp_path.display()
        )));
    }

    if let Err(err) = temp_file.sync_all() {
        let _ = fs::remove_file(&temp_path);
        return Err(crate::Error::Io(format!(
            "sync temporary {label} {}: {err}",
            temp_path.display()
        )));
    }

    drop(temp_file);

    fs::rename(&temp_path, path).map_err(|e| {
        let _ = fs::remove_file(&temp_path);
        crate::Error::Io(format!("replace {label} {}: {e}", path.display()))
    })
}

pub fn upsert_memory_document(root: &Path, document: MemoryIndexDocument) -> crate::Result<()> {
    let manifest = load_or_default_manifest(root)?;
    let mut documents = load_memory_documents(root)?;

    if let Some(existing) = documents
        .iter_mut()
        .find(|existing| existing.user_id == document.user_id && existing.record_id == document.record_id)
    {
        *existing = document;
    } else {
        documents.push(document);
    }

    save_memory_documents(root, &documents)?;
    save_root_manifest(root, &manifest)
}

pub fn delete_memory_document(root: &Path, user_id: u64, record_id: u64) -> crate::Result<bool> {
    let manifest = load_or_default_manifest(root)?;
    let mut documents = load_memory_documents(root)?;
    let before = documents.len();
    documents.retain(|document| !(document.user_id == user_id && document.record_id == record_id));
    let deleted = documents.len() != before;

    if deleted {
        save_memory_documents(root, &documents)?;
    }

    save_root_manifest(root, &manifest)?;
    Ok(deleted)
}

pub fn delete_memory_documents_for_user(root: &Path, user_id: u64) -> crate::Result<u64> {
    let manifest = load_or_default_manifest(root)?;
    let mut documents = load_memory_documents(root)?;
    let before = documents.len();
    documents.retain(|document| document.user_id != user_id);
    let deleted = (before - documents.len()) as u64;

    if deleted > 0 {
        save_memory_documents(root, &documents)?;
    }

    save_root_manifest(root, &manifest)?;
    Ok(deleted)
}

pub fn reset_memory_documents(root: &Path) -> crate::Result<()> {
    let mut manifest = load_or_default_manifest(root)?;
    save_memory_documents(root, &[])?;
    manifest.mark_dirty(IndexFamily::Memory);
    save_root_manifest(root, &manifest)
}

pub fn search_memory_documents(
    root: &Path,
    user_id: u64,
    query: &str,
    limit: usize,
) -> crate::Result<Vec<MemorySearchHit>> {
    let documents = load_memory_documents(root)?;
    let filtered: Vec<MemoryIndexDocument> = documents
        .into_iter()
        .filter(|document| document.user_id == user_id)
        .collect();

    if filtered.is_empty() || limit == 0 {
        return Ok(Vec::new());
    }

    if query.trim().is_empty() {
        let mut docs = filtered;
        docs.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        docs.truncate(limit);
        return Ok(docs
            .into_iter()
            .map(|document| MemorySearchHit {
                record_id: document.record_id,
                score: 1.0,
                source_type: document.source_type,
                category: document.category,
                importance: document.importance,
                created_at: document.created_at,
            })
            .collect());
    }

    let mut index = SimpleBm25Index::new();
    let mut by_id = std::collections::HashMap::new();
    for document in filtered {
        index.add_document(document.record_id, &document.text);
        by_id.insert(document.record_id, document);
    }

    Ok(index
        .search(query, limit)
        .into_iter()
        .filter_map(|result| {
            by_id.remove(&result.frame_id).map(|document| MemorySearchHit {
                record_id: document.record_id,
                score: result.score,
                source_type: document.source_type,
                category: document.category,
                importance: document.importance,
                created_at: document.created_at,
            })
        })
        .collect())
}

pub fn search_memory_documents_by_vector(
    root: &Path,
    user_id: u64,
    query_embedding: &[f32],
    limit: usize,
) -> crate::Result<Vec<MemorySearchHit>> {
    if query_embedding.is_empty() || limit == 0 {
        return Ok(Vec::new());
    }

    let documents = load_memory_documents(root)?;
    let mut scored: Vec<(f32, MemoryIndexDocument)> = documents
        .into_iter()
        .filter(|document| document.user_id == user_id)
        .filter_map(|document| {
            let embedding = document.embedding.as_ref()?;
            if embedding.len() != query_embedding.len() {
                return None;
            }
            let score = crate::simd::cosine_similarity(query_embedding, embedding);
            Some((score, document))
        })
        .collect();

    scored.sort_by(|a, b| b.0.total_cmp(&a.0));
    scored.truncate(limit);

    Ok(scored
        .into_iter()
        .map(|(score, document)| MemorySearchHit {
            record_id: document.record_id,
            score,
            source_type: document.source_type,
            category: document.category,
            importance: document.importance,
            created_at: document.created_at,
        })
        .collect())
}

pub fn upsert_transcript_document(
    root: &Path,
    document: TranscriptIndexDocument,
) -> crate::Result<()> {
    let manifest = load_or_default_manifest(root)?;
    let mut documents = load_transcript_documents(root)?;

    if let Some(existing) = documents.iter_mut().find(|existing| {
        existing.user_id == document.user_id && existing.thread_id == document.thread_id
    }) {
        *existing = document;
    } else {
        documents.push(document);
    }

    save_transcript_documents(root, &documents)?;
    save_root_manifest(root, &manifest)
}

pub fn delete_transcript_document(root: &Path, user_id: u64, thread_id: u64) -> crate::Result<bool> {
    let manifest = load_or_default_manifest(root)?;
    let mut documents = load_transcript_documents(root)?;
    let before = documents.len();
    documents.retain(|document| !(document.user_id == user_id && document.thread_id == thread_id));
    let deleted = documents.len() != before;

    if deleted {
        save_transcript_documents(root, &documents)?;
    }

    save_root_manifest(root, &manifest)?;
    Ok(deleted)
}

pub fn delete_transcript_documents_for_user(root: &Path, user_id: u64) -> crate::Result<u64> {
    let manifest = load_or_default_manifest(root)?;
    let mut documents = load_transcript_documents(root)?;
    let before = documents.len();
    documents.retain(|document| document.user_id != user_id);
    let deleted = (before - documents.len()) as u64;

    if deleted > 0 {
        save_transcript_documents(root, &documents)?;
    }

    save_root_manifest(root, &manifest)?;
    Ok(deleted)
}

pub fn reset_transcript_documents(root: &Path) -> crate::Result<()> {
    let mut manifest = load_or_default_manifest(root)?;
    save_transcript_documents(root, &[])?;
    manifest.mark_dirty(IndexFamily::Transcript);
    save_root_manifest(root, &manifest)
}

pub fn search_transcript_documents(
    root: &Path,
    user_id: u64,
    query: &str,
    limit: usize,
) -> crate::Result<Vec<TranscriptSearchHit>> {
    let documents = load_transcript_documents(root)?;
    let filtered: Vec<TranscriptIndexDocument> = documents
        .into_iter()
        .filter(|document| document.user_id == user_id)
        .collect();

    if filtered.is_empty() || limit == 0 {
        return Ok(Vec::new());
    }

    if query.trim().is_empty() {
        let mut docs = filtered;
        docs.sort_by(|a, b| b.date_start.cmp(&a.date_start));
        docs.truncate(limit);
        return Ok(docs
            .into_iter()
            .map(|document| TranscriptSearchHit {
                thread_id: document.thread_id,
                transcript_ref: document.transcript_ref,
                score: 1.0,
                date_start: document.date_start,
            })
            .collect());
    }

    let mut index = SimpleBm25Index::new();
    let mut by_id = std::collections::HashMap::new();
    for document in filtered {
        let search_text = format!(
            "{}\n{}\n{}",
            document.summary,
            document.keywords.join(" "),
            document.text
        );
        index.add_document(document.thread_id, &search_text);
        by_id.insert(document.thread_id, document);
    }

    Ok(index
        .search(query, limit)
        .into_iter()
        .filter_map(|result| {
            by_id.remove(&result.frame_id).map(|document| TranscriptSearchHit {
                thread_id: document.thread_id,
                transcript_ref: document.transcript_ref,
                score: result.score,
                date_start: document.date_start,
            })
        })
        .collect())
}
