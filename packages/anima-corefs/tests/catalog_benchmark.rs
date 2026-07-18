use std::fs;
use std::time::Duration;

use anima_corefs::benchmark::{
    build_fixture, build_fixture_matrix, derive_fixture_lifecycle_counts,
    expected_reference_fixture_manifest_fingerprint, needs_serialized_limit_fixture,
    percentile_nearest_rank, run_fixture_benchmark, BenchmarkRunConfig, CatalogFixtureSpec,
    FixtureKind, MAX_CATALOG_PLAINTEXT_BYTES,
};
use anima_corefs::catalog::{
    CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, FolderLifecycle,
    FolderTrashMetadata,
};
use anima_corefs::folders::{FolderOwner, PortableName};
use anima_corefs::id::OpaqueId;
use anima_corefs::policy::AnimaAccess;

fn test_root(name: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!(
        "anima-corefs-catalog-benchmark-{}-{name}",
        std::process::id()
    ))
}

#[test]
fn fixture_matrix_is_deterministic_and_preserves_advertised_counts() {
    let first = build_fixture_matrix().unwrap();
    let second = build_fixture_matrix().unwrap();

    assert_eq!(first.len(), 3);
    assert_eq!(first[0].kind(), FixtureKind::Medium);
    assert_eq!(first[0].live_count(), 5_000);
    assert_eq!(first[0].tombstone_count(), 500);
    assert_eq!(first[0].total_count(), 5_500);
    assert_eq!(first[0].lifecycle_counts().unwrap(), (5_000, 500));
    assert_eq!(first[1].kind(), FixtureKind::MaximumLive);
    assert_eq!(first[1].live_count(), 25_000);
    assert_eq!(first[1].tombstone_count(), 2_500);
    assert_eq!(first[1].total_count(), 27_500);
    assert_eq!(first[1].lifecycle_counts().unwrap(), (25_000, 2_500));
    assert!(first[1].serialized_size() <= MAX_CATALOG_PLAINTEXT_BYTES);
    assert_eq!(first[2].kind(), FixtureKind::SerializedLimit);
    assert!(first[2].live_count() <= 25_000);
    assert_eq!(first[2].serialized_size(), MAX_CATALOG_PLAINTEXT_BYTES);

    let first_fingerprints: Vec<_> = first
        .iter()
        .map(|value| value.manifest_fingerprint())
        .collect();
    let second_fingerprints: Vec<_> = second
        .iter()
        .map(|value| value.manifest_fingerprint())
        .collect();
    assert_eq!(first_fingerprints, second_fingerprints);
    let expected_fingerprints: Vec<_> = first
        .iter()
        .map(|fixture| expected_reference_fixture_manifest_fingerprint(fixture.kind()).unwrap())
        .collect();
    assert_eq!(first_fingerprints, expected_fingerprints);
}

#[test]
fn lifecycle_counting_rejects_trashed_folders_as_tombstones() {
    fn common(id: usize, parent: Option<usize>, name: &str) -> CatalogEntryCommon {
        CatalogEntryCommon::new(
            OpaqueId::parse(&format!("{id:026}")).unwrap(),
            parent.map(|value| OpaqueId::parse(&format!("{value:026}")).unwrap()),
            PortableName::parse(name).unwrap(),
            FolderOwner::User,
            AnimaAccess::Write,
        )
    }

    let trashed = common(2, Some(1), "deleted").with_folder_lifecycle(FolderLifecycle::Trashed(
        FolderTrashMetadata::new(
            OpaqueId::parse(&format!("{:026}", 1)).unwrap(),
            OpaqueId::parse(&format!("{:026}", 0)).unwrap(),
            PortableName::parse("deleted").unwrap(),
            1,
        )
        .unwrap(),
    ));
    let catalog = CatalogGeneration::new(
        1,
        vec![
            CatalogGenerationEntry::folder(common(0, None, "Core")),
            CatalogGenerationEntry::folder(common(1, Some(0), "Trash")),
            CatalogGenerationEntry::folder(trashed),
        ],
    )
    .unwrap();

    let error = derive_fixture_lifecycle_counts(&catalog).unwrap_err();
    assert!(error.to_string().contains("trashed folder"));
}

#[test]
fn conditional_exact_size_fixture_exercises_include_skip_and_oversize_branches() {
    assert!(needs_serialized_limit_fixture(MAX_CATALOG_PLAINTEXT_BYTES - 1).unwrap());
    assert!(!needs_serialized_limit_fixture(MAX_CATALOG_PLAINTEXT_BYTES).unwrap());
    let error = needs_serialized_limit_fixture(MAX_CATALOG_PLAINTEXT_BYTES + 1).unwrap_err();
    assert!(error.to_string().contains("16 MiB"));
}

#[test]
fn exact_size_padding_tracks_generation_digit_widths() {
    let fixture = build_fixture(&CatalogFixtureSpec::new(
        FixtureKind::TestOnly,
        10,
        0,
        Some(4_096),
    ))
    .unwrap();

    assert_eq!(fixture.precalibrated_generation_widths(), &[1, 2, 3]);
    for generation in [3, 9, 10, 99, 100, 232] {
        assert_eq!(fixture.calibrated_serialized_size(generation), Some(4_096));
    }
}

#[test]
fn percentile_uses_deterministic_nearest_rank_and_report_schema_has_required_metrics() {
    let samples = [
        Duration::from_millis(40),
        Duration::from_millis(10),
        Duration::from_millis(30),
        Duration::from_millis(20),
    ];
    assert_eq!(
        percentile_nearest_rank(&samples, 0.50),
        Duration::from_millis(20)
    );
    assert_eq!(
        percentile_nearest_rank(&samples, 0.95),
        Duration::from_millis(40)
    );
    assert_eq!(
        percentile_nearest_rank(&samples, 0.99),
        Duration::from_millis(40)
    );

    let root = test_root("report-schema");
    let _ = fs::remove_dir_all(&root);
    let fixture =
        build_fixture(&CatalogFixtureSpec::new(FixtureKind::TestOnly, 10, 2, None)).unwrap();
    let report = run_fixture_benchmark(
        &root,
        &fixture,
        BenchmarkRunConfig {
            warmup_commits: 1,
            measured_commits: 1,
        },
    )
    .unwrap();
    let value = serde_json::to_value(report).unwrap();

    assert_eq!(value["warmupCommits"], 1);
    assert_eq!(value["sampleCount"], 1);
    assert_eq!(value["finalCatalogCount"], 4);
    assert_eq!(value["productionSerializationsPerCommit"], 1);
    assert_eq!(
        fs::read_dir(root.join("objects"))
            .unwrap()
            .filter_map(Result::ok)
            .count(),
        2,
        "true tombstones must retain immutable object revisions"
    );
    assert!(value["warmupSerializedSizeBytes"]["min"].is_number());
    assert!(value["warmupSerializedSizeBytes"]["max"].is_number());
    assert!(value["measuredSerializedSizeBytes"]["min"].is_number());
    assert!(value["measuredSerializedSizeBytes"]["max"].is_number());
    assert!(value["commitMs"]["p50"].is_number());
    assert!(value["commitMs"]["p95"].is_number());
    assert!(value["commitMs"]["p99"].is_number());
    assert!(value["lockHoldMs"]["p95"].is_number());
    assert!(value["bytesWritten"].as_u64().unwrap() > 0);
    assert_eq!(
        value["publicationPath"],
        serde_json::json!([
            "serialize",
            "encrypt",
            "temporary-file-write",
            "durable-flush",
            "atomic-rename",
            "directory-durability",
            "fs-head-write-flush",
            "commit-lock"
        ])
    );

    let _ = fs::remove_dir_all(root);
}

#[test]
fn measured_runner_publishes_real_exact_catalogs_for_all_reference_generations() {
    let root = test_root("real-publication");
    let _ = fs::remove_dir_all(&root);
    let fixture = build_fixture(&CatalogFixtureSpec::new(
        FixtureKind::TestOnly,
        10,
        0,
        Some(4_096),
    ))
    .unwrap();

    let report = run_fixture_benchmark(
        &root,
        &fixture,
        BenchmarkRunConfig {
            warmup_commits: 30,
            measured_commits: 200,
        },
    )
    .unwrap();

    assert_eq!(report.final_head_generation(), 232);
    assert!(root.join("fs").join("HEAD").is_file());
    let catalog_count = fs::read_dir(root.join("fs").join("catalogs"))
        .unwrap()
        .filter_map(Result::ok)
        .count();
    assert_eq!(catalog_count, 232);
    let value = serde_json::to_value(&report).unwrap();
    assert_eq!(value["serializedSizeBytes"], 4_096);
    assert_eq!(value["warmupSerializedSizeBytes"]["min"], 4_096);
    assert_eq!(value["warmupSerializedSizeBytes"]["max"], 4_096);
    assert_eq!(value["measuredSerializedSizeBytes"]["min"], 4_096);
    assert_eq!(value["measuredSerializedSizeBytes"]["max"], 4_096);
    assert_eq!(value["finalCatalogCount"], 232);
    assert_eq!(value["productionSerializationsPerCommit"], 1);
    assert!(report.lock_hold().p50() > Duration::ZERO);

    let _ = fs::remove_dir_all(root);
}
