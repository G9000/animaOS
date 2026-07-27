use std::fs;
use std::time::Duration;

use anima_corefs::benchmark::{
    build_fixture, build_fixture_matrix, derive_fixture_lifecycle_counts,
    expected_reference_fixture_manifest_fingerprint, needs_serialized_limit_fixture,
    percentile_nearest_rank, run_fixture_benchmark, run_object_lease_diagnostic,
    BenchmarkRunConfig, CatalogFixtureSpec, FixtureKind, ObjectLeaseDiagnosticConfig,
    ObjectLeaseDiagnosticEvent, ObjectLeaseDiagnosticMutationCase, MAX_CATALOG_PLAINTEXT_BYTES,
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

fn read_child_json(
    path: &std::path::Path,
    completed: &std::process::Output,
    context: &str,
) -> serde_json::Value {
    let bytes = fs::read(path).unwrap_or_else(|error| {
        panic!(
            "{context} did not publish {} after child status {}: {error}; stdout={}; stderr={}",
            path.display(),
            completed.status,
            String::from_utf8_lossy(&completed.stdout),
            String::from_utf8_lossy(&completed.stderr),
        )
    });
    serde_json::from_slice(&bytes).unwrap_or_else(|error| {
        panic!(
            "{context} published invalid JSON after child status {}: {error}; stdout={}; stderr={}",
            completed.status,
            String::from_utf8_lossy(&completed.stdout),
            String::from_utf8_lossy(&completed.stderr),
        )
    })
}

fn temporary_residue(root: &std::path::Path) -> Vec<std::path::PathBuf> {
    fn visit(root: &std::path::Path, residue: &mut Vec<std::path::PathBuf>) {
        let Ok(entries) = fs::read_dir(root) else {
            return;
        };
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            if path.is_dir() {
                visit(&path, residue);
            } else if entry
                .file_name()
                .to_string_lossy()
                .to_ascii_lowercase()
                .ends_with(".tmp")
            {
                residue.push(path);
            }
        }
    }

    let mut residue = Vec::new();
    visit(root, &mut residue);
    residue
}

fn assert_closed_object_lease_diagnostic_schema(value: &serde_json::Value) {
    fn object_keys(value: &serde_json::Value) -> std::collections::BTreeSet<&str> {
        value
            .as_object()
            .expect("diagnostic schema member must be an object")
            .keys()
            .map(String::as_str)
            .collect()
    }

    assert_eq!(
        object_keys(value),
        [
            "build",
            "correctness",
            "filesystem",
            "hardware",
            "lease",
            "objectCount",
            "os",
            "platform",
            "residueCount",
            "resources",
            "safeOpen",
            "samples",
            "schemaVersion",
            "teardown",
            "warmups",
        ]
        .into_iter()
        .collect()
    );
    assert!(
        matches!(value["platform"].as_str(), Some("windows" | "macos")),
        "platform must be a closed native-platform enum"
    );
    assert!(value["schemaVersion"].is_u64());
    assert!(value["objectCount"].is_u64());
    assert!(value["warmups"].is_u64());
    assert!(value["samples"].is_u64());
    assert!(value["residueCount"].is_u64());
    assert_eq!(
        object_keys(&value["hardware"]),
        ["architecture", "logicalProcessors"].into_iter().collect()
    );
    assert!(value["hardware"]["architecture"].is_string());
    assert!(value["hardware"]["logicalProcessors"].is_u64());
    assert_eq!(
        object_keys(&value["os"]),
        ["family", "version"].into_iter().collect()
    );
    assert!(value["os"]["family"].is_string());
    assert!(value["os"]["version"].is_string());
    assert_eq!(
        object_keys(&value["filesystem"]),
        ["name", "target"].into_iter().collect()
    );
    assert!(value["filesystem"]["name"].is_string());
    assert!(value["filesystem"]["target"].is_string());
    assert_eq!(
        object_keys(&value["safeOpen"]),
        ["p50Ms", "p95Ms", "p99Ms"].into_iter().collect()
    );
    assert_eq!(
        object_keys(&value["lease"]),
        [
            "fenceCount",
            "metadataQueryCount",
            "p50Ms",
            "p95Ms",
            "p99Ms",
            "safeOpenCount",
        ]
        .into_iter()
        .collect()
    );
    assert_eq!(
        object_keys(&value["resources"]),
        [
            "descriptorDelta",
            "liveEntryPermits",
            "liveLeasePermits",
            "liveMonitorResources",
            "postTeardownEntryPermits",
            "postTeardownLeasePermits",
            "postTeardownMonitorResources",
        ]
        .into_iter()
        .collect()
    );
    assert_eq!(
        object_keys(&value["teardown"]),
        ["completionConfirmed", "elapsedMs", "targetMet", "targetMs"]
            .into_iter()
            .collect()
    );
    assert_eq!(
        object_keys(&value["correctness"]),
        [
            "mutationMatrixPassed",
            "orderedBoundaryProven",
            "teardownPassed",
        ]
        .into_iter()
        .collect()
    );
    assert_eq!(
        object_keys(&value["build"]),
        [
            "architecture",
            "argv",
            "cargoLock",
            "crateVersion",
            "debugAssertions",
            "executable",
            "nativeTestContract",
            "output",
            "source",
            "target",
        ]
        .into_iter()
        .collect()
    );
    assert_eq!(
        object_keys(&value["build"]["source"]),
        ["clean", "commit", "repositoryRoot"].into_iter().collect()
    );
    assert_eq!(
        object_keys(&value["build"]["executable"]),
        ["canonicalPath", "fileId", "sha256", "volumeSerial"]
            .into_iter()
            .collect()
    );
    assert_eq!(
        object_keys(&value["build"]["cargoLock"]),
        [
            "canonicalPath",
            "committedSha256",
            "matchesCommit",
            "workingSha256",
        ]
        .into_iter()
        .collect()
    );
    assert_eq!(
        object_keys(&value["build"]["target"]),
        ["canonicalPath", "fileId", "volumeSerial"]
            .into_iter()
            .collect()
    );
    assert_eq!(
        object_keys(&value["build"]["output"]),
        ["canonicalPath", "fileId", "volumeSerial"]
            .into_iter()
            .collect()
    );
    assert_eq!(
        object_keys(&value["build"]["nativeTestContract"]),
        ["requiredTests", "sourceCommit"].into_iter().collect()
    );
    assert!(value["build"]["architecture"].is_string());
    assert!(value["build"]["crateVersion"].is_string());
    assert!(value["build"]["debugAssertions"].is_boolean());
    assert!(value["build"]["source"]["repositoryRoot"].is_string());
    assert!(value["build"]["source"]["commit"].is_string());
    assert!(value["build"]["source"]["clean"].is_boolean());
    assert!(value["build"]["executable"]["canonicalPath"].is_string());
    assert!(value["build"]["executable"]["sha256"].is_string());
    assert!(value["build"]["executable"]["volumeSerial"].is_u64());
    assert!(value["build"]["executable"]["fileId"].is_u64());
    assert!(value["build"]["cargoLock"]["canonicalPath"].is_string());
    assert!(value["build"]["cargoLock"]["workingSha256"].is_string());
    assert!(value["build"]["cargoLock"]["committedSha256"].is_string());
    assert!(value["build"]["cargoLock"]["matchesCommit"].is_boolean());
    assert!(value["build"]["target"]["canonicalPath"].is_string());
    assert!(value["build"]["target"]["volumeSerial"].is_u64());
    assert!(value["build"]["target"]["fileId"].is_u64());
    assert!(value["build"]["output"]["canonicalPath"].is_string());
    assert!(value["build"]["output"]["volumeSerial"].is_u64());
    assert!(value["build"]["output"]["fileId"].is_u64());
    assert!(value["build"]["argv"]
        .as_array()
        .is_some_and(|arguments| arguments.iter().all(serde_json::Value::is_string)));
    assert!(value["build"]["nativeTestContract"]["sourceCommit"].is_string());
    assert!(value["build"]["nativeTestContract"]["requiredTests"]
        .as_array()
        .is_some_and(|tests| tests.iter().all(serde_json::Value::is_string)));
}

#[cfg(windows)]
#[test]
fn production_lease_diagnostic_validates_2500_descriptors_in_an_isolated_child() {
    let root = test_root("production-lease-isolated-child");
    let target = root.join("target");
    let output = root.join("object-lease.json");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    let completed = std::process::Command::new(env!("CARGO_BIN_EXE_object_lease_diagnostic"))
        .args([
            "--target",
            target.to_str().unwrap(),
            "--objects",
            "2500",
            "--warmups",
            "1",
            "--samples",
            "2",
            "--mutation-matrix",
            "--output",
            output.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    let value = read_child_json(&output, &completed, "production lease diagnostic");
    assert_closed_object_lease_diagnostic_schema(&value);
    let strict_provenance_matches = value["build"]["source"]["clean"] == true
        && value["build"]["source"]["commit"]
            == value["build"]["nativeTestContract"]["sourceCommit"]
        && value["build"]["cargoLock"]["matchesCommit"] == true;
    assert_eq!(
        completed.status.success(),
        strict_provenance_matches,
        "{}",
        String::from_utf8_lossy(&completed.stderr)
    );
    if !strict_provenance_matches {
        let diagnostic: serde_json::Value = serde_json::from_slice(&completed.stderr).unwrap();
        assert_eq!(diagnostic["error"], "nativeAcceptanceFailed");
    }
    assert_eq!(value["lease"]["safeOpenCount"], 0);
    assert_eq!(
        value["lease"]["metadataQueryCount"], 2_500,
        "report counters are per sample; samples already carries the repetition count"
    );
    assert_eq!(
        value["lease"]["fenceCount"], 2,
        "report counters are per sample; samples already carries the repetition count"
    );
    assert_eq!(value["resources"]["liveEntryPermits"], 2_500);
    assert_eq!(value["resources"]["liveLeasePermits"], 1);
    assert_eq!(value["resources"]["liveMonitorResources"], 3);
    assert_eq!(value["platform"], "windows");
    assert_eq!(value["objectCount"], 2_500);
    assert_eq!(value["warmups"], 1);
    assert_eq!(value["samples"], 2);
    assert_eq!(value["teardown"]["targetMs"], 2_000);
    assert_eq!(value["teardown"]["completionConfirmed"], true);
    assert_eq!(value["teardown"]["targetMet"], true);
    assert_eq!(value["resources"]["postTeardownEntryPermits"], 0);
    assert_eq!(value["resources"]["postTeardownLeasePermits"], 0);
    assert_eq!(value["resources"]["postTeardownMonitorResources"], 0);
    assert_eq!(value["resources"]["descriptorDelta"], 0);
    assert_eq!(value["residueCount"], 0);
    assert_eq!(value["correctness"]["orderedBoundaryProven"], true);
    assert_eq!(value["correctness"]["mutationMatrixPassed"], true);
    assert_eq!(
        value["correctness"]["teardownPassed"].as_bool().unwrap(),
        value["teardown"]["completionConfirmed"].as_bool().unwrap()
            && value["teardown"]["targetMet"].as_bool().unwrap()
            && value["resources"]["postTeardownEntryPermits"] == 0
            && value["resources"]["postTeardownLeasePermits"] == 0
            && value["resources"]["postTeardownMonitorResources"] == 0
            && value["resources"]["descriptorDelta"] == 0
            && value["residueCount"] == 0
    );

    let _ = fs::remove_dir_all(root);
}

#[cfg(windows)]
#[test]
fn object_lease_diagnostic_requires_the_mutation_matrix_for_native_acceptance() {
    let root = test_root("production-lease-matrix-required");
    let _ = fs::remove_dir_all(&root);
    let outcome = run_object_lease_diagnostic(
        &root,
        ObjectLeaseDiagnosticConfig {
            object_count: 8,
            warmups: 0,
            samples: 1,
            mutation_matrix: false,
        },
    )
    .unwrap();
    let value = serde_json::to_value(outcome.report()).unwrap();

    assert_eq!(value["correctness"]["mutationMatrixPassed"], false);
    assert!(
        !outcome.report().native_acceptance_passed(),
        "skipping the mandatory mutation matrix must fail native acceptance"
    );

    let _ = fs::remove_dir_all(root);
}

#[cfg(windows)]
#[test]
fn object_lease_diagnostic_records_ordered_boundaries_and_required_mutations() {
    let root = test_root("production-lease-ordered-boundary");
    let _ = fs::remove_dir_all(&root);
    let outcome = run_object_lease_diagnostic(
        &root,
        ObjectLeaseDiagnosticConfig {
            object_count: 8,
            warmups: 0,
            samples: 2,
            mutation_matrix: true,
        },
    )
    .unwrap();
    let observations = outcome.observations();
    let mut clean_sequence = vec![ObjectLeaseDiagnosticEvent::FenceClean];
    clean_sequence.extend(std::iter::repeat(ObjectLeaseDiagnosticEvent::MetadataQuery).take(8));
    clean_sequence.push(ObjectLeaseDiagnosticEvent::FenceClean);
    let between_fence_sequence = vec![
        ObjectLeaseDiagnosticEvent::FenceClean,
        ObjectLeaseDiagnosticEvent::BetweenFenceMutation,
        ObjectLeaseDiagnosticEvent::FenceDirtyAll,
    ];

    assert_eq!(observations.clean_boundary_samples().len(), 2);
    for sample in observations.clean_boundary_samples() {
        assert_eq!(sample, &clean_sequence);
    }
    assert_eq!(
        observations.between_fence_mutation_boundary(),
        between_fence_sequence
    );
    assert!(observations
        .mutation_cases()
        .contains(&ObjectLeaseDiagnosticMutationCase::BetweenFirstAndFinalFence));
    assert!(observations
        .mutation_cases()
        .contains(&ObjectLeaseDiagnosticMutationCase::InsideDirectoryHardLink));
    assert!(observations
        .mutation_cases()
        .contains(&ObjectLeaseDiagnosticMutationCase::InPlaceWriteBlocked));
    assert_eq!(observations.clean_safe_open_count(), 0);
    assert_eq!(observations.clean_metadata_query_count(), 8);
    assert_eq!(observations.clean_fence_count(), 2);

    let value = serde_json::to_value(outcome.report()).unwrap();
    assert_eq!(value["lease"]["metadataQueryCount"], 8);
    assert_eq!(value["lease"]["fenceCount"], 2);
    assert_eq!(value["correctness"]["orderedBoundaryProven"], true);
    assert_eq!(value["correctness"]["mutationMatrixPassed"], true);

    let _ = fs::remove_dir_all(root);
}

#[test]
fn object_lease_diagnostic_cli_has_a_closed_create_only_contract() {
    let root = test_root("lease-cli");
    let target = root.join("target");
    let output = root.join("object-lease.json");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    let completed = std::process::Command::new(env!("CARGO_BIN_EXE_object_lease_diagnostic"))
        .args([
            "--target",
            target.to_str().unwrap(),
            "--objects",
            "8",
            "--warmups",
            "1",
            "--samples",
            "1",
            "--mutation-matrix",
            "--output",
            output.to_str().unwrap(),
        ])
        .output()
        .unwrap();

    if cfg!(windows) {
        let value = read_child_json(&output, &completed, "lease diagnostic CLI");
        assert_closed_object_lease_diagnostic_schema(&value);
        assert_eq!(value["platform"], "windows");
        assert_eq!(value["objectCount"], 8);
        assert_eq!(value["warmups"], 1);
        assert_eq!(value["samples"], 1);
        assert_eq!(value["teardown"]["targetMs"], 2_000);
        assert_eq!(value["teardown"]["completionConfirmed"], true);
        assert_eq!(value["teardown"]["targetMet"], true);
        assert_eq!(value["correctness"]["orderedBoundaryProven"], true);
        assert_eq!(value["correctness"]["mutationMatrixPassed"], true);
        assert_eq!(value["correctness"]["teardownPassed"], true);
        assert_eq!(value["resources"]["postTeardownEntryPermits"], 0);
        assert_eq!(value["resources"]["postTeardownLeasePermits"], 0);
        assert_eq!(value["resources"]["postTeardownMonitorResources"], 0);
        assert_eq!(value["resources"]["descriptorDelta"], 0);
        assert_eq!(value["residueCount"], 0);
        let expected_argv = vec![
            env!("CARGO_BIN_EXE_object_lease_diagnostic"),
            "--target",
            target.to_str().unwrap(),
            "--objects",
            "8",
            "--warmups",
            "1",
            "--samples",
            "1",
            "--mutation-matrix",
            "--output",
            output.to_str().unwrap(),
        ];
        assert_eq!(value["build"]["argv"], serde_json::json!(expected_argv));
        let reported_argv = value["build"]["argv"].as_array().unwrap();
        assert_eq!(
            fs::canonicalize(reported_argv[0].as_str().unwrap()).unwrap(),
            std::path::PathBuf::from(
                value["build"]["executable"]["canonicalPath"]
                    .as_str()
                    .unwrap()
            )
        );
        assert_eq!(
            fs::canonicalize(reported_argv[2].as_str().unwrap()).unwrap(),
            std::path::PathBuf::from(value["build"]["target"]["canonicalPath"].as_str().unwrap()),
            "normal CLI paths and extended canonical Windows paths must bind by identity"
        );
        assert_eq!(
            fs::canonicalize(reported_argv[11].as_str().unwrap()).unwrap(),
            std::path::PathBuf::from(value["build"]["output"]["canonicalPath"].as_str().unwrap())
        );
        assert_eq!(
            value["build"]["target"]["canonicalPath"],
            fs::canonicalize(&target).unwrap().to_str().unwrap()
        );
        assert_eq!(
            value["build"]["output"]["canonicalPath"],
            fs::canonicalize(&output).unwrap().to_str().unwrap()
        );
        assert_eq!(
            value["filesystem"]["target"],
            value["build"]["target"]["canonicalPath"]
        );
        assert_eq!(
            value["build"]["source"]["commit"].as_str().unwrap().len(),
            40
        );
        assert_eq!(
            value["build"]["executable"]["sha256"]
                .as_str()
                .unwrap()
                .len(),
            64
        );
        assert_eq!(
            value["build"]["cargoLock"]["workingSha256"]
                .as_str()
                .unwrap()
                .len(),
            64
        );
        assert_eq!(
            value["build"]["cargoLock"]["committedSha256"]
                .as_str()
                .unwrap()
                .len(),
            64
        );
        let lock_matches_commit = std::process::Command::new("git")
            .arg("-C")
            .arg(value["build"]["source"]["repositoryRoot"].as_str().unwrap())
            .args(["diff", "--quiet", "HEAD", "--", "Cargo.lock"])
            .output()
            .unwrap()
            .status
            .success();
        assert_eq!(
            value["build"]["cargoLock"]["matchesCommit"],
            lock_matches_commit
        );
        let strict_provenance_matches = value["build"]["source"]["clean"] == true
            && value["build"]["source"]["commit"]
                == value["build"]["nativeTestContract"]["sourceCommit"]
            && value["build"]["cargoLock"]["matchesCommit"] == true;
        assert_eq!(
            completed.status.success(),
            strict_provenance_matches,
            "{}",
            String::from_utf8_lossy(&completed.stderr)
        );
        if !strict_provenance_matches {
            let diagnostic: serde_json::Value = serde_json::from_slice(&completed.stderr).unwrap();
            assert_eq!(diagnostic["error"], "nativeAcceptanceFailed");
        }

        let repeated = std::process::Command::new(env!("CARGO_BIN_EXE_object_lease_diagnostic"))
            .args([
                "--target",
                target.to_str().unwrap(),
                "--objects",
                "8",
                "--warmups",
                "1",
                "--samples",
                "1",
                "--mutation-matrix",
                "--output",
                output.to_str().unwrap(),
            ])
            .output()
            .unwrap();
        assert!(!repeated.status.success(), "paths must be create-only");
    } else {
        assert!(!completed.status.success());
        let diagnostic: serde_json::Value =
            serde_json::from_slice(&completed.stderr).expect("typed backendUnavailable JSON");
        assert_eq!(diagnostic["error"], "backendUnavailable");
        if cfg!(target_os = "macos") {
            assert!(diagnostic["message"]
                .as_str()
                .unwrap()
                .contains("macOS native backend is not enabled"));
        } else {
            assert!(diagnostic["message"]
                .as_str()
                .unwrap()
                .contains("no production object-validation lease backend"));
        }
        assert!(!output.exists());
    }

    let _ = fs::remove_dir_all(root);
}

#[test]
fn object_lease_diagnostic_create_only_paths_preserve_existing_contents() {
    let root = test_root("lease-cli-create-only");
    let existing_target = root.join("existing-target");
    let fresh_output = root.join("fresh-output.json");
    let fresh_target = root.join("fresh-target");
    let existing_output = root.join("existing-output.json");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&existing_target).unwrap();
    fs::write(existing_target.join("marker"), b"target-marker").unwrap();
    fs::write(&existing_output, b"output-marker").unwrap();

    let run = |target: &std::path::Path, output: &std::path::Path| {
        std::process::Command::new(env!("CARGO_BIN_EXE_object_lease_diagnostic"))
            .args([
                "--target",
                target.to_str().unwrap(),
                "--objects",
                "8",
                "--warmups",
                "1",
                "--samples",
                "1",
                "--mutation-matrix",
                "--output",
                output.to_str().unwrap(),
            ])
            .output()
            .unwrap()
    };

    let existing_target_result = run(&existing_target, &fresh_output);
    assert!(!existing_target_result.status.success());
    assert_eq!(
        fs::read(existing_target.join("marker")).unwrap(),
        b"target-marker"
    );
    assert!(!fresh_output.exists());

    let existing_output_result = run(&fresh_target, &existing_output);
    assert!(!existing_output_result.status.success());
    assert!(!fresh_target.exists());
    assert_eq!(fs::read(&existing_output).unwrap(), b"output-marker");

    let _ = fs::remove_dir_all(root);
}

#[test]
fn object_lease_diagnostic_cli_rejects_every_duplicate_value_flag() {
    let root = test_root("lease-cli-duplicate-flags");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();

    for flag in [
        "--target",
        "--output",
        "--objects",
        "--warmups",
        "--samples",
    ] {
        let target = root.join(format!("target-{}", &flag[2..]));
        let output = root.join(format!("output-{}.json", &flag[2..]));
        let duplicate_path = root.join(format!("duplicate-{}", &flag[2..]));
        let duplicate_value = match flag {
            "--target" | "--output" => duplicate_path.to_str().unwrap(),
            _ => "0",
        };
        let completed = std::process::Command::new(env!("CARGO_BIN_EXE_object_lease_diagnostic"))
            .args([
                "--target",
                target.to_str().unwrap(),
                "--output",
                output.to_str().unwrap(),
                "--objects",
                "0",
                "--warmups",
                "0",
                "--samples",
                "1",
                flag,
                duplicate_value,
            ])
            .output()
            .unwrap();

        assert!(!completed.status.success());
        let diagnostic: serde_json::Value =
            serde_json::from_slice(&completed.stderr).expect("typed invalidArguments JSON");
        assert_eq!(diagnostic["error"], "invalidArguments");
        assert!(
            diagnostic["message"]
                .as_str()
                .is_some_and(|message| message.contains(&format!("duplicate {flag}"))),
            "{flag} duplicate was not rejected: {}",
            String::from_utf8_lossy(&completed.stderr)
        );
        assert!(!target.exists());
        assert!(!output.exists());
        assert!(!duplicate_path.exists());
    }

    let _ = fs::remove_dir_all(root);
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
    assert_eq!(first[2].tombstone_count(), 0);
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
            "commit-lock",
            "serialize",
            "encrypt",
            "temporary-file-write",
            "durable-flush",
            "atomic-rename",
            "directory-durability",
            "fs-head-write-flush"
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
    assert!(
        temporary_residue(&root).is_empty(),
        "benchmark left temporary-file residue: {:?}",
        temporary_residue(&root)
    );

    let _ = fs::remove_dir_all(root);
}
