use anima_file_tools::{
    BackendCapabilities, BackendKind, BackendPath, LimitError, MutationAtomicity, OperationLimits,
    PathSemantics, MAX_BACKEND_PATH_BYTES,
};

#[test]
fn production_defaults_match_the_portable_core_contract() {
    let limits = OperationLimits::default().validate().unwrap();

    assert_eq!(limits.read_chunk_bytes(), 1024 * 1024);
    assert_eq!(limits.walk_depth(), 64);
    assert_eq!(limits.walk_directories(), 10_000);
    assert_eq!(limits.walk_entries(), 50_000);
    assert_eq!(limits.response_bytes(), 4 * 1024 * 1024);
}

#[test]
fn backend_paths_reject_values_above_the_cross_backend_safety_ceiling() {
    let oversized = "x".repeat(MAX_BACKEND_PATH_BYTES + 1);

    assert!(BackendPath::new(BackendKind::HostFs, oversized).is_err());
}

#[test]
fn callers_cannot_raise_any_production_ceiling() {
    let limits = OperationLimits {
        response_bytes: 4 * 1024 * 1024 + 1,
        ..OperationLimits::default()
    };

    assert_eq!(
        limits.validate(),
        Err(LimitError::ExceedsMaximum {
            field: "response_bytes",
            requested: 4 * 1024 * 1024 + 1,
            maximum: 4 * 1024 * 1024,
        })
    );
}

#[test]
fn zero_limits_are_rejected_instead_of_silently_disabling_bounds() {
    let limits = OperationLimits {
        walk_entries: 0,
        ..OperationLimits::default()
    };

    assert_eq!(
        limits.validate(),
        Err(LimitError::MustBePositive {
            field: "walk_entries",
        })
    );
}

#[test]
fn capabilities_keep_backend_authority_and_atomicity_explicit() {
    let host = BackendCapabilities::new(
        BackendKind::HostFs,
        PathSemantics::HostNative,
        MutationAtomicity::BestEffort,
    );
    let core = BackendCapabilities::new(
        BackendKind::CoreFs,
        PathSemantics::PortableNfcCaseSensitive,
        MutationAtomicity::CatalogGeneration,
    );

    assert_eq!(host.backend(), BackendKind::HostFs);
    assert_eq!(host.mutation_atomicity(), MutationAtomicity::BestEffort);
    assert_eq!(core.backend(), BackendKind::CoreFs);
    assert_eq!(
        core.path_semantics(),
        PathSemantics::PortableNfcCaseSensitive
    );
    assert_ne!(host.backend(), core.backend());
}
