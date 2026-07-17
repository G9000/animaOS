use std::collections::BTreeMap;
use std::fs;
use std::io::Cursor;
use std::path::PathBuf;

use anima_corefs::catalog::{
    CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, CatalogObject, ObjectLifecycle,
    TrashMetadata,
};
use anima_corefs::crypto::{
    derive_corefs_subkeys, FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes,
};
use anima_corefs::envelope::{encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION};
use anima_corefs::folders::{FolderOwner, PortableName};
use anima_corefs::id::OpaqueId;
use anima_corefs::logical::{
    CoreFsReadSnapshot, LogicalGrepRequest, LogicalWalkCursor, LogicalWalkOptions,
    RuntimeSearchState, SearchNotReadyReason, SearchReadinessStatus,
};
use anima_corefs::policy::AnimaAccess;
use anima_corefs::rotation::FrkKeyring;
use anima_corefs::transaction::{
    CoreCommitCoordinator, PreparedObjectRevision, ValidationSnapshot,
};
use anima_file_tools::{
    walk_page, BackendKind, BackendPath, FileBackend, FileToolError, GrepMode, MutationAtomicity,
    OperationControl, OperationLimits, PathSemantics, ReadOptions, SkipReason, WalkOptions,
};

const CORE_ID: &str = "logical-core";
const ROOT_ID: &str = "01J00000000000000000000000";
const NOTES_ID: &str = "01J00000000000000000000001";
const TRASH_ID: &str = "01J00000000000000000000002";
const ALPHA_ID: &str = "01J00000000000000000000003";
const BETA_ID: &str = "01J00000000000000000000004";
const BINARY_ID: &str = "01J00000000000000000000005";
const TRASHED_ID: &str = "01J00000000000000000000006";
const TOMBSTONE_ID: &str = "01J00000000000000000000007";

struct Fixture {
    root: PathBuf,
    coordinator: CoreCommitCoordinator,
    keys: FrkSubkeys,
    validation: ValidationSnapshot,
    beta_physical_name: String,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn snapshot_capabilities_and_normal_lookup_only_expose_live_entries() {
    let fixture = fixture("visibility");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();

    let capabilities = snapshot.capabilities();
    assert_eq!(capabilities.backend(), BackendKind::CoreFs);
    assert_eq!(
        capabilities.path_semantics(),
        PathSemantics::PortableNfcCaseSensitive
    );
    assert_eq!(
        capabilities.mutation_atomicity(),
        MutationAtomicity::CatalogGeneration
    );
    assert_eq!(snapshot.generation(), 1);

    let stat = snapshot.stat("Notes/Alpha.md").unwrap();
    assert_eq!(stat.stable_id, ALPHA_ID);
    assert_eq!(stat.revision, Some(1));
    assert_eq!(stat.generation, 1);
    assert!(snapshot.stat("Trash/trashed.md").is_err());
    assert!(snapshot.stat("Trash/tombstone.md").is_err());

    let limits = OperationLimits::default().validate().unwrap();
    let trash = snapshot
        .list("Trash", None, 10, limits, OperationControl::default())
        .unwrap();
    assert!(trash.entries.is_empty());
}

#[test]
fn walk_list_and_glob_are_deterministic_and_generation_bound() {
    let fixture = fixture("traversal");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();
    let limits = OperationLimits::default().validate().unwrap();

    let first = snapshot
        .walk(
            "",
            LogicalWalkOptions {
                page_size: 2,
                cursor: None,
                include_directories: true,
            },
            limits,
            OperationControl::default(),
        )
        .unwrap();
    assert_eq!(
        first
            .entries
            .iter()
            .map(|entry| entry.path.as_str())
            .collect::<Vec<_>>(),
        vec!["Notes", "Notes/Alpha.md"]
    );
    assert_eq!(first.generation, 1);
    assert!(first.next_cursor.is_some());

    let second = snapshot
        .walk(
            "",
            LogicalWalkOptions {
                page_size: 10,
                cursor: first.next_cursor,
                include_directories: true,
            },
            limits,
            OperationControl::default(),
        )
        .unwrap();
    assert_eq!(
        second
            .entries
            .iter()
            .map(|entry| entry.path.as_str())
            .collect::<Vec<_>>(),
        vec!["Notes/apparent.txt", "Notes/beta.md", "Trash"]
    );

    let mismatched = LogicalWalkCursor::new(2, "Notes/Alpha.md");
    assert!(snapshot
        .walk(
            "",
            LogicalWalkOptions {
                page_size: 10,
                cursor: Some(mismatched),
                include_directories: true,
            },
            limits,
            OperationControl::default(),
        )
        .is_err());

    let glob = snapshot
        .glob("", "**/*.md", None, 10, limits, OperationControl::default())
        .unwrap();
    assert_eq!(
        glob.matches
            .iter()
            .map(|entry| entry.path.as_str())
            .collect::<Vec<_>>(),
        vec!["Notes/Alpha.md", "Notes/beta.md"]
    );
    assert_eq!(glob.generation, 1);
}

#[test]
fn shared_operations_reject_hostfs_paths_before_corefs_lookup() {
    let fixture = fixture("cross-backend");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();

    let error = walk_page(
        &snapshot,
        BackendPath::new(BackendKind::HostFs, "Notes").unwrap(),
        WalkOptions {
            page_size: 10,
            cursor: None,
            include_directories: true,
        },
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::BackendMismatch {
            path_backend: BackendKind::HostFs,
            selected_backend: BackendKind::CoreFs,
        }
    ));
}

#[test]
fn reads_are_bounded_and_return_only_logical_identity_metadata() {
    let fixture = fixture("bounded-read");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();
    let limits = OperationLimits {
        read_chunk_bytes: 8,
        response_bytes: 32,
        ..OperationLimits::default()
    }
    .validate()
    .unwrap();

    let chunks = snapshot
        .read(
            "Notes/Alpha.md",
            ReadOptions {
                offset: 0,
                max_bytes: 32,
            },
            limits,
            OperationControl::default(),
        )
        .unwrap()
        .collect::<Result<Vec<_>, _>>()
        .unwrap();

    assert!(chunks.iter().all(|chunk| chunk.bytes.len() <= 8));
    assert!(chunks.iter().all(|chunk| chunk.stable_id == ALPHA_ID));
    assert!(chunks.iter().all(|chunk| chunk.revision == 1));
    assert!(chunks.iter().all(|chunk| chunk.generation == 1));
    assert_eq!(
        chunks
            .iter()
            .flat_map(|chunk| chunk.bytes.iter().copied())
            .collect::<Vec<_>>(),
        b"caf\xc3\xa9\nneedle one\n"
    );
}

#[test]
fn late_object_authentication_failure_returns_no_untrusted_read_chunk() {
    let fixture = fixture("late-auth");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();
    let object_path = fixture
        .coordinator
        .objects_path()
        .join(&fixture.beta_physical_name);
    let mut encoded = fs::read(&object_path).unwrap();
    *encoded.last_mut().unwrap() ^= 1;
    fs::write(object_path, encoded).unwrap();

    let mut read = snapshot
        .read(
            "Notes/beta.md",
            ReadOptions {
                offset: 0,
                max_bytes: 32,
            },
            OperationLimits::default().validate().unwrap(),
            OperationControl::default(),
        )
        .unwrap();
    assert!(read.next().unwrap().is_err());
    assert!(read.next().is_none());
}

#[test]
fn authoritative_grep_skips_declared_binary_and_attaches_stable_metadata() {
    let fixture = fixture("grep");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();
    let page = snapshot
        .grep(
            LogicalGrepRequest {
                root: "".to_string(),
                query: "needle".to_string(),
                mode: GrepMode::Literal,
                cursor: None,
                max_files: 100,
                max_matches: 100,
                max_line_bytes: 1024,
            },
            OperationLimits::default().validate().unwrap(),
            OperationControl::default(),
        )
        .unwrap();

    assert_eq!(page.generation, 1);
    assert_eq!(page.matches.len(), 2);
    assert_eq!(page.matches[0].stable_id, ALPHA_ID);
    assert_eq!(page.matches[0].revision, 1);
    assert_eq!(page.matches[0].path.as_str(), "Notes/Alpha.md");
    assert_eq!(page.matches[0].line_number, 2);
    assert_eq!(page.matches[0].byte_offset, 6);
    assert_eq!(page.matches[1].stable_id, BETA_ID);
    assert_eq!(page.skipped.len(), 1);
    assert_eq!(page.skipped[0].stable_id, BINARY_ID);
    assert_eq!(page.skipped[0].reason, SkipReason::BinaryContent);
}

#[test]
fn logical_grep_response_budget_includes_identity_metadata() {
    let fixture = fixture("grep-response-budget");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();
    let limits = OperationLimits {
        response_bytes: 200,
        ..OperationLimits::default()
    }
    .validate()
    .unwrap();

    let page = snapshot
        .grep(
            LogicalGrepRequest {
                root: "Notes/Alpha.md".to_string(),
                query: "needle".to_string(),
                mode: GrepMode::Literal,
                cursor: None,
                max_files: 1,
                max_matches: 1,
                max_line_bytes: 64,
            },
            limits,
            OperationControl::default(),
        )
        .unwrap();

    assert!(page.matches.is_empty());
    assert!(page.truncated);
}

#[test]
fn search_readiness_never_claims_a_different_or_incomplete_generation() {
    let fixture = fixture("search-ready");
    let keyring = FrkKeyring::new([&fixture.keys]).unwrap();
    let snapshot =
        CoreFsReadSnapshot::open(&fixture.coordinator, &fixture.validation, &keyring).unwrap();

    let missing = snapshot.search_readiness(RuntimeSearchState::Missing);
    assert_eq!(missing.catalog_generation, 1);
    assert_eq!(missing.index_generation, None);
    assert_eq!(
        missing.status,
        SearchReadinessStatus::NotReady(SearchNotReadyReason::Missing)
    );

    let stale = snapshot.search_readiness(RuntimeSearchState::Ready { generation: 2 });
    assert_eq!(stale.catalog_generation, 1);
    assert_eq!(stale.index_generation, Some(2));
    assert_eq!(
        stale.status,
        SearchReadinessStatus::NotReady(SearchNotReadyReason::GenerationMismatch)
    );

    let building = snapshot.search_readiness(RuntimeSearchState::Building { generation: 1 });
    assert_eq!(
        building.status,
        SearchReadinessStatus::NotReady(SearchNotReadyReason::Building)
    );

    let ready = snapshot.search_readiness(RuntimeSearchState::Ready { generation: 1 });
    assert_eq!(ready.status, SearchReadinessStatus::Ready);
}

fn fixture(name: &str) -> Fixture {
    let root = std::env::temp_dir().join(format!(
        "anima-corefs-logical-{}-{name}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&root);
    let coordinator = CoreCommitCoordinator::new(&root, CORE_ID).unwrap();
    let keys = derive_corefs_subkeys(&SecretBytes::new(vec![0x42; 32]).unwrap(), 1).unwrap();

    let alpha = prepare(
        &coordinator,
        &keys,
        ALPHA_ID,
        BodyEncoding::Utf8,
        b"caf\xc3\xa9\nneedle one\n",
        0x31,
    );
    let beta = prepare(
        &coordinator,
        &keys,
        BETA_ID,
        BodyEncoding::Utf8,
        b"needle two\n",
        0x32,
    );
    let binary = prepare(
        &coordinator,
        &keys,
        BINARY_ID,
        BodyEncoding::Binary,
        b"needle but declared binary",
        0x33,
    );
    let trashed = prepare(
        &coordinator,
        &keys,
        TRASHED_ID,
        BodyEncoding::Utf8,
        b"needle in trash",
        0x34,
    );
    let tombstone = prepare(
        &coordinator,
        &keys,
        TOMBSTONE_ID,
        BodyEncoding::Utf8,
        b"needle tombstone",
        0x35,
    );
    let beta_physical_name = beta.physical_name().as_str().to_string();
    let prepared = vec![alpha, beta, binary, trashed, tombstone];

    let validation = coordinator
        .initialize_validation_snapshot(&keys, &prepared, |generation| {
            CatalogGeneration::new(
                generation,
                vec![
                    CatalogGenerationEntry::folder(common(ROOT_ID, None, "Core")),
                    CatalogGenerationEntry::folder(common(NOTES_ID, Some(ROOT_ID), "Notes")),
                    CatalogGenerationEntry::folder(common(TRASH_ID, Some(ROOT_ID), "Trash")),
                    CatalogGenerationEntry::object(
                        common(ALPHA_ID, Some(NOTES_ID), "Alpha.md"),
                        object(&prepared[0], ObjectLifecycle::Live),
                    ),
                    CatalogGenerationEntry::object(
                        common(BETA_ID, Some(NOTES_ID), "beta.md"),
                        object(&prepared[1], ObjectLifecycle::Live),
                    ),
                    CatalogGenerationEntry::object(
                        common(BINARY_ID, Some(NOTES_ID), "apparent.txt"),
                        object(&prepared[2], ObjectLifecycle::Live),
                    ),
                    CatalogGenerationEntry::object(
                        common(TRASHED_ID, Some(TRASH_ID), "trashed.md"),
                        object(
                            &prepared[3],
                            ObjectLifecycle::Trashed(
                                TrashMetadata::new(
                                    opaque(TRASH_ID),
                                    opaque(NOTES_ID),
                                    PortableName::parse("trashed.md").unwrap(),
                                    1,
                                )
                                .unwrap(),
                            ),
                        ),
                    ),
                    CatalogGenerationEntry::object(
                        common(TOMBSTONE_ID, Some(TRASH_ID), "tombstone.md"),
                        object(
                            &prepared[4],
                            ObjectLifecycle::tombstone(opaque(TRASH_ID), 2).unwrap(),
                        ),
                    ),
                ],
            )
        })
        .unwrap();

    Fixture {
        root,
        coordinator,
        keys,
        validation,
        beta_physical_name,
    }
}

fn prepare(
    coordinator: &CoreCommitCoordinator,
    keys: &FrkSubkeys,
    object_id: &str,
    body_encoding: BodyEncoding,
    body: &[u8],
    key_fill: u8,
) -> PreparedObjectRevision {
    let object_key = SecretBytes::new(vec![key_fill; 32]).unwrap();
    let aad =
        ObjectBaseAad::new(CORE_ID, object_id, ObjectKind::Note, ENVELOPE_VERSION, 1, 1).unwrap();
    let metadata = EnvelopeMetadata::for_body(
        ObjectKind::Note.as_str(),
        object_id,
        1,
        "2026-07-17T00:00:00Z",
        "2026-07-17T00:00:00Z",
        if body_encoding == BodyEncoding::Utf8 {
            "text/markdown"
        } else {
            "application/octet-stream"
        },
        BTreeMap::new(),
        body_encoding,
        body,
    )
    .unwrap();
    let encoded = encode_envelope(&object_key, &aad, &metadata, body).unwrap();
    coordinator
        .prepare_object_revision(keys, &object_key, &aad, &mut Cursor::new(encoded))
        .unwrap()
}

fn object(prepared: &PreparedObjectRevision, lifecycle: ObjectLifecycle) -> CatalogObject {
    CatalogObject::new(
        prepared.revision(),
        prepared.physical_name().clone(),
        prepared.content_hash().clone(),
        ObjectKind::Note,
        prepared.wrapped_dek().clone(),
        lifecycle,
    )
    .unwrap()
}

fn common(id: &str, parent_id: Option<&str>, name: &str) -> CatalogEntryCommon {
    CatalogEntryCommon::new(
        opaque(id),
        parent_id.map(opaque),
        PortableName::parse(name).unwrap(),
        FolderOwner::User,
        AnimaAccess::Write,
    )
}

fn opaque(value: &str) -> OpaqueId {
    OpaqueId::parse(value).unwrap()
}
