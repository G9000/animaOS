use anima_core::retrieval_index::{IndexFamily, RetrievalManifest};

use anima_core::retrieval_index::{
    delete_memory_document, delete_memory_documents_for_user, delete_transcript_document,
    delete_transcript_documents_for_user, load_manifest, mark_family_dirty,
    search_memory_documents, search_memory_documents_by_vector, search_transcript_documents,
    upsert_memory_document, upsert_transcript_document, MemoryIndexDocument,
    TranscriptIndexDocument,
};
use serde_json::{from_str, to_string};
use tempfile::{Builder, TempDir};

#[test]
fn empty_manifest_starts_clean() {
    let manifest = RetrievalManifest::default();

    assert_eq!(manifest.version, 1);
    assert!(!manifest.is_family_dirty(IndexFamily::Memory));
}

#[test]
fn manifest_tracks_dirty_flags_per_family() {
    let mut manifest = RetrievalManifest::default();

    manifest.mark_dirty(IndexFamily::Memory);

    assert!(manifest.is_family_dirty(IndexFamily::Memory));
    assert!(!manifest.is_family_dirty(IndexFamily::Transcript));
}

struct TempRoot(TempDir);

impl std::ops::Deref for TempRoot {
    type Target = std::path::Path;

    fn deref(&self) -> &Self::Target {
        self.0.path()
    }
}

impl AsRef<std::path::Path> for TempRoot {
    fn as_ref(&self) -> &std::path::Path {
        self.0.path()
    }
}

fn temp_root(test_name: &str) -> TempRoot {
    TempRoot(
        Builder::new()
            .prefix(test_name)
            .tempdir_in(std::env::temp_dir())
            .unwrap(),
    )
}

#[test]
fn temp_root_creates_unique_isolated_directories() {
    let first = temp_root("temp_root_creates_unique_isolated_directories");
    let second = temp_root("temp_root_creates_unique_isolated_directories");

    assert_ne!(first.as_ref(), second.as_ref());
}

fn memory_lexical_index_path(root: &std::path::Path) -> std::path::PathBuf {
    root.join("memory").join("lexical_index.json")
}

fn memory_documents_path(root: &std::path::Path) -> std::path::PathBuf {
    root.join("memory").join("documents.json")
}

fn transcript_lexical_index_path(root: &std::path::Path) -> std::path::PathBuf {
    root.join("transcripts").join("lexical_index.json")
}

fn transcript_documents_path(root: &std::path::Path) -> std::path::PathBuf {
    root.join("transcripts").join("documents.json")
}

fn manifest_path(root: &std::path::Path) -> std::path::PathBuf {
    root.join("manifest.json")
}

#[test]
fn memory_index_upsert_and_search_round_trip() {
    let root = temp_root("memory_index_upsert_and_search_round_trip");
    let document = MemoryIndexDocument {
        record_id: 101,
        user_id: 7,
        text: "user likes pour over coffee".into(),
        embedding: None,
        source_type: "memory_item".into(),
        category: "preference".into(),
        importance: 4,
        created_at: 1_710_000_000,
    };

    upsert_memory_document(&root, document).unwrap();
    let hits = search_memory_documents(&root, 7, "coffee", 5).unwrap();

    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].record_id, 101);
    assert_eq!(hits[0].source_type, "memory_item");
}

#[test]
fn memory_documents_do_not_persist_full_plaintext_text() {
    let root = temp_root("memory_documents_do_not_persist_full_plaintext_text");
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 101,
            user_id: 7,
            text: "user likes pour over coffee in the quiet kitchen".into(),
            embedding: None,
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_000,
        },
    )
    .unwrap();

    let raw_documents = std::fs::read_to_string(memory_documents_path(&root)).unwrap();

    assert!(!raw_documents.contains("\"text\""));
    assert!(!raw_documents.contains("user likes pour over coffee in the quiet kitchen"));
    assert!(
        search_memory_documents(&root, 7, "quiet kitchen", 5)
            .unwrap()
            .len()
            == 1
    );
}

#[test]
fn retrieval_atomic_write_replaces_existing_files() {
    let root = temp_root("retrieval_atomic_write_replaces_existing_files");

    mark_family_dirty(&root, IndexFamily::Memory).unwrap();
    mark_family_dirty(&root, IndexFamily::Transcript).unwrap();

    let manifest = load_manifest(&manifest_path(&root)).unwrap();
    assert!(manifest.is_family_dirty(IndexFamily::Memory));
    assert!(manifest.is_family_dirty(IndexFamily::Transcript));
}

#[test]
fn memory_index_delete_removes_document() {
    let root = temp_root("memory_index_delete_removes_document");
    let document = MemoryIndexDocument {
        record_id: 101,
        user_id: 7,
        text: "user likes pour over coffee".into(),
        embedding: None,
        source_type: "memory_item".into(),
        category: "preference".into(),
        importance: 4,
        created_at: 1_710_000_000,
    };

    upsert_memory_document(&root, document).unwrap();
    assert!(delete_memory_document(&root, 7, 101).unwrap());
    assert!(search_memory_documents(&root, 7, "coffee", 5)
        .unwrap()
        .is_empty());
}

#[test]
fn memory_index_vector_search_round_trip() {
    let root = temp_root("memory_index_vector_search_round_trip");
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 101,
            user_id: 7,
            text: "user likes pour over coffee".into(),
            embedding: Some(vec![1.0, 0.0, 0.0]),
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_000,
        },
    )
    .unwrap();
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 102,
            user_id: 7,
            text: "user works as a designer".into(),
            embedding: Some(vec![0.0, 1.0, 0.0]),
            source_type: "memory_item".into(),
            category: "fact".into(),
            importance: 3,
            created_at: 1_710_000_100,
        },
    )
    .unwrap();

    let hits = search_memory_documents_by_vector(&root, 7, &[0.9, 0.1, 0.0], 5).unwrap();

    assert_eq!(hits.len(), 2);
    assert_eq!(hits[0].record_id, 101);
    assert!(hits[0].score > hits[1].score);
}

#[test]
fn memory_index_upsert_preserves_dirty_manifest() {
    let root = temp_root("memory_index_upsert_preserves_dirty_manifest");
    mark_family_dirty(&root, IndexFamily::Memory).unwrap();

    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 101,
            user_id: 7,
            text: "user likes pour over coffee".into(),
            embedding: None,
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_000,
        },
    )
    .unwrap();

    let manifest = load_manifest(&root.join("manifest.json")).unwrap();
    assert!(manifest.is_family_dirty(IndexFamily::Memory));
}

#[test]
fn memory_index_delete_preserves_dirty_manifest() {
    let root = temp_root("memory_index_delete_preserves_dirty_manifest");
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 101,
            user_id: 7,
            text: "user likes pour over coffee".into(),
            embedding: None,
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_000,
        },
    )
    .unwrap();
    mark_family_dirty(&root, IndexFamily::Memory).unwrap();

    assert!(delete_memory_document(&root, 7, 101).unwrap());

    let manifest = load_manifest(&root.join("manifest.json")).unwrap();
    assert!(manifest.is_family_dirty(IndexFamily::Memory));
}

#[test]
fn memory_index_delete_for_user_preserves_other_users_documents() {
    let root = temp_root("memory_index_delete_for_user_preserves_other_users_documents");
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 101,
            user_id: 7,
            text: "user likes pour over coffee".into(),
            embedding: None,
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_000,
        },
    )
    .unwrap();
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 202,
            user_id: 8,
            text: "user likes jasmine tea".into(),
            embedding: None,
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_100,
        },
    )
    .unwrap();

    assert_eq!(delete_memory_documents_for_user(&root, 7).unwrap(), 1);

    let user_seven_hits = search_memory_documents(&root, 7, "coffee", 5).unwrap();
    let user_eight_hits = search_memory_documents(&root, 8, "jasmine", 5).unwrap();
    assert!(user_seven_hits.is_empty());
    assert_eq!(user_eight_hits.len(), 1);
    assert_eq!(user_eight_hits[0].record_id, 202);
}

#[test]
fn memory_search_rebuilds_missing_persisted_lexical_index() {
    let root = temp_root("memory_search_rebuilds_missing_persisted_lexical_index");
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 101,
            user_id: 7,
            text: "user likes pour over coffee".into(),
            embedding: None,
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_000,
        },
    )
    .unwrap();

    let lexical_index_path = memory_lexical_index_path(&root);
    let _ = std::fs::remove_file(&lexical_index_path);

    let hits = search_memory_documents(&root, 7, "coffee", 5).unwrap();

    assert_eq!(hits.len(), 1);
    assert!(lexical_index_path.exists());
}

#[test]
fn memory_search_rebuilds_stale_persisted_lexical_index() {
    let root = temp_root("memory_search_rebuilds_stale_persisted_lexical_index");
    upsert_memory_document(
        &root,
        MemoryIndexDocument {
            record_id: 101,
            user_id: 7,
            text: "user likes pour over coffee".into(),
            embedding: None,
            source_type: "memory_item".into(),
            category: "preference".into(),
            importance: 4,
            created_at: 1_710_000_000,
        },
    )
    .unwrap();

    let documents_path = memory_documents_path(&root);
    let mut documents: Vec<MemoryIndexDocument> =
        from_str(&std::fs::read_to_string(&documents_path).unwrap()).unwrap();
    documents.push(MemoryIndexDocument {
        record_id: 202,
        user_id: 7,
        text: "user likes jasmine tea".into(),
        embedding: None,
        source_type: "memory_item".into(),
        category: "preference".into(),
        importance: 3,
        created_at: 1_710_000_100,
    });
    std::fs::write(
        &documents_path,
        format!("{}\n", to_string(&documents).unwrap()),
    )
    .unwrap();

    let hits = search_memory_documents(&root, 7, "jasmine", 5).unwrap();

    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].record_id, 202);
}

#[test]
fn transcript_index_upsert_and_search_round_trip() {
    let root = temp_root("transcript_index_upsert_and_search_round_trip");
    let document = TranscriptIndexDocument {
        thread_id: 42,
        user_id: 7,
        transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
        summary: "Conversation about quantum physics".into(),
        keywords: vec!["quantum".into(), "physics".into()],
        text: "User asked about quantum physics and assistant explained the basics.".into(),
        date_start: 1_711_621_600,
    };

    upsert_transcript_document(&root, document).unwrap();
    let hits = search_transcript_documents(&root, 7, "quantum physics", 5).unwrap();

    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].thread_id, 42);
    assert_eq!(hits[0].transcript_ref, "2026-03-28_thread-42.jsonl.enc");
}

#[test]
fn transcript_documents_do_not_persist_full_plaintext_text() {
    let root = temp_root("transcript_documents_do_not_persist_full_plaintext_text");
    upsert_transcript_document(
        &root,
        TranscriptIndexDocument {
            thread_id: 42,
            user_id: 7,
            transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
            summary: "Conversation about planning".into(),
            keywords: vec!["planning".into()],
            text: "Assistant explained the private launch checklist in detail.".into(),
            date_start: 1_711_621_600,
        },
    )
    .unwrap();

    let raw_documents = std::fs::read_to_string(transcript_documents_path(&root)).unwrap();

    assert!(!raw_documents.contains("\"text\""));
    assert!(!raw_documents.contains("Assistant explained the private launch checklist in detail."));
    assert!(
        search_transcript_documents(&root, 7, "private launch checklist", 5)
            .unwrap()
            .len()
            == 1
    );
}

#[test]
fn transcript_index_delete_removes_document() {
    let root = temp_root("transcript_index_delete_removes_document");
    let document = TranscriptIndexDocument {
        thread_id: 42,
        user_id: 7,
        transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
        summary: "Conversation about quantum physics".into(),
        keywords: vec!["quantum".into(), "physics".into()],
        text: "User asked about quantum physics and assistant explained the basics.".into(),
        date_start: 1_711_621_600,
    };

    upsert_transcript_document(&root, document).unwrap();
    assert!(delete_transcript_document(&root, 7, 42).unwrap());
    assert!(search_transcript_documents(&root, 7, "quantum", 5)
        .unwrap()
        .is_empty());
}

#[test]
fn transcript_index_upsert_preserves_dirty_manifest() {
    let root = temp_root("transcript_index_upsert_preserves_dirty_manifest");
    mark_family_dirty(&root, IndexFamily::Transcript).unwrap();

    upsert_transcript_document(
        &root,
        TranscriptIndexDocument {
            thread_id: 42,
            user_id: 7,
            transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
            summary: "Conversation about quantum physics".into(),
            keywords: vec!["quantum".into(), "physics".into()],
            text: "User asked about quantum physics and assistant explained the basics.".into(),
            date_start: 1_711_621_600,
        },
    )
    .unwrap();

    let manifest = load_manifest(&root.join("manifest.json")).unwrap();
    assert!(manifest.is_family_dirty(IndexFamily::Transcript));
}

#[test]
fn transcript_index_delete_preserves_dirty_manifest() {
    let root = temp_root("transcript_index_delete_preserves_dirty_manifest");
    upsert_transcript_document(
        &root,
        TranscriptIndexDocument {
            thread_id: 42,
            user_id: 7,
            transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
            summary: "Conversation about quantum physics".into(),
            keywords: vec!["quantum".into(), "physics".into()],
            text: "User asked about quantum physics and assistant explained the basics.".into(),
            date_start: 1_711_621_600,
        },
    )
    .unwrap();
    mark_family_dirty(&root, IndexFamily::Transcript).unwrap();

    assert!(delete_transcript_document(&root, 7, 42).unwrap());

    let manifest = load_manifest(&root.join("manifest.json")).unwrap();
    assert!(manifest.is_family_dirty(IndexFamily::Transcript));
}

#[test]
fn transcript_index_delete_for_user_preserves_other_users_documents() {
    let root = temp_root("transcript_index_delete_for_user_preserves_other_users_documents");
    upsert_transcript_document(
        &root,
        TranscriptIndexDocument {
            thread_id: 42,
            user_id: 7,
            transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
            summary: "Conversation about deadlines".into(),
            keywords: vec!["deadline".into()],
            text: "User asked about deadlines.".into(),
            date_start: 1_711_621_600,
        },
    )
    .unwrap();
    upsert_transcript_document(
        &root,
        TranscriptIndexDocument {
            thread_id: 77,
            user_id: 8,
            transcript_ref: "2026-03-28_thread-77.jsonl.enc".into(),
            summary: "Conversation about bakery orders".into(),
            keywords: vec!["bakery".into()],
            text: "User asked about bakery orders.".into(),
            date_start: 1_711_622_000,
        },
    )
    .unwrap();

    assert_eq!(delete_transcript_documents_for_user(&root, 7).unwrap(), 1);

    let user_seven_hits = search_transcript_documents(&root, 7, "deadline", 5).unwrap();
    let user_eight_hits = search_transcript_documents(&root, 8, "bakery", 5).unwrap();
    assert!(user_seven_hits.is_empty());
    assert_eq!(user_eight_hits.len(), 1);
    assert_eq!(user_eight_hits[0].thread_id, 77);
}

#[test]
fn transcript_search_rebuilds_missing_persisted_lexical_index() {
    let root = temp_root("transcript_search_rebuilds_missing_persisted_lexical_index");
    upsert_transcript_document(
        &root,
        TranscriptIndexDocument {
            thread_id: 42,
            user_id: 7,
            transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
            summary: "Conversation about deadlines".into(),
            keywords: vec!["deadline".into()],
            text: "User asked about deadlines.".into(),
            date_start: 1_711_621_600,
        },
    )
    .unwrap();

    let lexical_index_path = transcript_lexical_index_path(&root);
    let _ = std::fs::remove_file(&lexical_index_path);

    let hits = search_transcript_documents(&root, 7, "deadline", 5).unwrap();

    assert_eq!(hits.len(), 1);
    assert!(lexical_index_path.exists());
}

#[test]
fn transcript_search_rebuilds_stale_persisted_lexical_index() {
    let root = temp_root("transcript_search_rebuilds_stale_persisted_lexical_index");
    upsert_transcript_document(
        &root,
        TranscriptIndexDocument {
            thread_id: 42,
            user_id: 7,
            transcript_ref: "2026-03-28_thread-42.jsonl.enc".into(),
            summary: "Conversation about deadlines".into(),
            keywords: vec!["deadline".into()],
            text: "User asked about deadlines.".into(),
            date_start: 1_711_621_600,
        },
    )
    .unwrap();

    let documents_path = transcript_documents_path(&root);
    let mut documents: Vec<TranscriptIndexDocument> =
        from_str(&std::fs::read_to_string(&documents_path).unwrap()).unwrap();
    documents.push(TranscriptIndexDocument {
        thread_id: 77,
        user_id: 7,
        transcript_ref: "2026-03-28_thread-77.jsonl.enc".into(),
        summary: "Conversation about bakery orders".into(),
        keywords: vec!["bakery".into()],
        text: "User asked about bakery orders.".into(),
        date_start: 1_711_622_000,
    });
    std::fs::write(
        &documents_path,
        format!("{}\n", to_string(&documents).unwrap()),
    )
    .unwrap();

    let hits = search_transcript_documents(&root, 7, "bakery", 5).unwrap();

    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].thread_id, 77);
}
