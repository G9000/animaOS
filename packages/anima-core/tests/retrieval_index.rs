use anima_core::retrieval_index::{IndexFamily, RetrievalManifest};
use std::path::PathBuf;

use anima_core::retrieval_index::{
    delete_memory_document, delete_transcript_document, load_manifest, mark_family_dirty,
    search_memory_documents, search_memory_documents_by_vector, search_transcript_documents,
    upsert_memory_document, upsert_transcript_document, MemoryIndexDocument,
    TranscriptIndexDocument,
};

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

fn temp_root(test_name: &str) -> PathBuf {
    let base = std::env::temp_dir()
        .join("anima-core-retrieval-index-tests")
        .join(test_name);
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    base
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
    assert!(search_memory_documents(&root, 7, "coffee", 5).unwrap().is_empty());
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
    assert!(search_transcript_documents(&root, 7, "quantum", 5).unwrap().is_empty());
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
