use std::fs;
use std::time::Duration;

use anima_corefs::benchmark::{
    build_fixture, build_fixture_matrix, percentile_nearest_rank, run_fixture_benchmark,
    BenchmarkRunConfig, CatalogFixtureSpec, FixtureKind, MAX_CATALOG_PLAINTEXT_BYTES,
};

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
    assert_eq!(first[1].kind(), FixtureKind::MaximumLive);
    assert_eq!(first[1].live_count(), 25_000);
    assert_eq!(first[1].tombstone_count(), 2_500);
    assert_eq!(first[1].total_count(), 27_500);
    assert!(first[1].serialized_size() <= MAX_CATALOG_PLAINTEXT_BYTES);
    assert_eq!(first[2].kind(), FixtureKind::SerializedLimit);
    assert!(first[2].live_count() <= 25_000);
    assert_eq!(first[2].serialized_size(), MAX_CATALOG_PLAINTEXT_BYTES);

    let first_fingerprints: Vec<_> = first.iter().map(|value| value.fingerprint()).collect();
    let second_fingerprints: Vec<_> = second.iter().map(|value| value.fingerprint()).collect();
    assert_eq!(first_fingerprints, second_fingerprints);
}

#[test]
fn exact_size_gate_skips_only_an_already_full_maximum_live_fixture() {
    let exact = build_fixture(&CatalogFixtureSpec::new(
        FixtureKind::MaximumLive,
        25_000,
        2_500,
        Some(MAX_CATALOG_PLAINTEXT_BYTES),
    ))
    .unwrap();
    assert_eq!(exact.serialized_size(), MAX_CATALOG_PLAINTEXT_BYTES);
    assert_eq!(build_fixture_matrix().unwrap().len(), 3);

    let oversized = CatalogFixtureSpec::new(
        FixtureKind::MaximumLive,
        25_000,
        2_500,
        Some(MAX_CATALOG_PLAINTEXT_BYTES + 1),
    );
    let error = build_fixture(&oversized).unwrap_err();
    assert!(error.to_string().contains("16 MiB"));
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
            warmup_commits: 0,
            measured_commits: 1,
        },
    )
    .unwrap();
    let value = serde_json::to_value(report).unwrap();

    assert_eq!(value["sampleCount"], 1);
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
fn measured_runner_publishes_real_catalog_and_authoritative_head_generations() {
    let root = test_root("real-publication");
    let _ = fs::remove_dir_all(&root);
    let fixture =
        build_fixture(&CatalogFixtureSpec::new(FixtureKind::TestOnly, 10, 2, None)).unwrap();

    let report = run_fixture_benchmark(
        &root,
        &fixture,
        BenchmarkRunConfig {
            warmup_commits: 1,
            measured_commits: 2,
        },
    )
    .unwrap();

    assert_eq!(report.final_head_generation(), 5);
    assert!(root.join("fs").join("HEAD").is_file());
    let catalog_count = fs::read_dir(root.join("fs").join("catalogs"))
        .unwrap()
        .filter_map(Result::ok)
        .count();
    assert_eq!(catalog_count, 5);
    assert!(report.lock_hold().p50() > Duration::ZERO);

    let _ = fs::remove_dir_all(root);
}
