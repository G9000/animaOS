use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use serde::Serialize;

#[derive(Debug)]
struct Arguments {
    output: PathBuf,
    object_count: usize,
    warmups: Option<usize>,
    samples: Option<usize>,
    race_samples: Option<usize>,
    mount_restored_path: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorDiagnostic<'a> {
    error: &'a str,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CharacterizationReport {
    schema_version: u32,
    platform: &'static str,
    hardware: HardwareReport,
    os: OsReport,
    filesystem: FilesystemReport,
    build: BuildReport,
    object_count: usize,
    warmups: usize,
    samples: usize,
    safe_open: DistributionReport,
    lease: DistributionReport,
    resources: ResourceReport,
    lifecycle: LifecycleReport,
    restored_path: RestoredPathReport,
    outcomes: OutcomeReport,
    ordered_boundary_proven: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct HardwareReport {
    model: String,
    architecture: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct OsReport {
    version: String,
    build: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct FilesystemReport {
    name: String,
    mount_path: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BuildReport {
    profile: &'static str,
    rustc: String,
    source_commit: String,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DistributionReport {
    p50_ms: f64,
    p95_ms: f64,
    p99_ms: f64,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ResourceReport {
    maximum_descriptor_delta: i64,
    post_teardown_descriptor_delta: i64,
    residue_count: usize,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LifecycleReport {
    creation_passed: bool,
    start_passed: bool,
    callback_panic_contained: bool,
    teardown_passed: bool,
    callback_after_release: bool,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RestoredPathReport {
    tested: bool,
    ancestor_above_volume_covered: bool,
    zero_id_root_changed_rejected_clean: bool,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct OutcomeReport {
    ordinary_events_dirty_all: bool,
    ambiguous_flags_unknown: bool,
    outside_hard_link_rejected: bool,
}

impl CharacterizationReport {
    #[cfg(test)]
    fn contract_example() -> Self {
        Self {
            schema_version: 1,
            platform: "macos",
            hardware: HardwareReport {
                model: "contract-example".to_owned(),
                architecture: "contract-example".to_owned(),
            },
            os: OsReport {
                version: "contract-example".to_owned(),
                build: "contract-example".to_owned(),
            },
            filesystem: FilesystemReport {
                name: "apfs".to_owned(),
                mount_path: "/tmp/contract-example".to_owned(),
            },
            build: BuildReport {
                profile: "release",
                rustc: "1.75.0".to_owned(),
                source_commit: "contract-example".to_owned(),
            },
            object_count: 2_500,
            warmups: 30,
            samples: 200,
            safe_open: DistributionReport {
                p50_ms: 1.0,
                p95_ms: 1.0,
                p99_ms: 1.0,
            },
            lease: DistributionReport {
                p50_ms: 1.0,
                p95_ms: 1.0,
                p99_ms: 1.0,
            },
            resources: ResourceReport {
                maximum_descriptor_delta: 65,
                post_teardown_descriptor_delta: 0,
                residue_count: 0,
            },
            lifecycle: LifecycleReport {
                creation_passed: true,
                start_passed: true,
                callback_panic_contained: true,
                teardown_passed: true,
                callback_after_release: false,
            },
            restored_path: RestoredPathReport {
                tested: true,
                ancestor_above_volume_covered: true,
                zero_id_root_changed_rejected_clean: true,
            },
            outcomes: OutcomeReport {
                ordinary_events_dirty_all: true,
                ambiguous_flags_unknown: true,
                outside_hard_link_rejected: true,
            },
            ordered_boundary_proven: true,
        }
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err((kind, message)) => {
            let diagnostic = ErrorDiagnostic {
                error: kind,
                message,
            };
            let _ = serde_json::to_writer(io::stderr().lock(), &diagnostic);
            let _ = writeln!(io::stderr().lock());
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), (&'static str, String)> {
    let arguments = parse_arguments(std::env::args_os().skip(1))
        .map_err(|message| ("invalidArguments", message))?;
    if arguments.output.exists() {
        return Err((
            "outputUnavailable",
            format!(
                "refusing to replace existing output {}",
                arguments.output.display()
            ),
        ));
    }
    run_native_characterization(&arguments)
}

#[cfg(target_os = "macos")]
fn run_native_characterization(arguments: &Arguments) -> Result<(), (&'static str, String)> {
    let _ = (
        arguments.object_count,
        arguments.warmups,
        arguments.samples,
        arguments.race_samples,
        arguments.mount_restored_path,
    );
    Err((
        "backendUnavailable",
        "the native macOS FSEvents/kqueue characterization backend is not implemented".to_owned(),
    ))
}

#[cfg(not(target_os = "macos"))]
fn run_native_characterization(arguments: &Arguments) -> Result<(), (&'static str, String)> {
    let _ = (
        arguments.object_count,
        arguments.warmups,
        arguments.samples,
        arguments.race_samples,
        arguments.mount_restored_path,
    );
    Err((
        "backendUnavailable",
        "the macOS characterization must run on a native macOS host".to_owned(),
    ))
}

fn parse_arguments(arguments: impl IntoIterator<Item = OsString>) -> Result<Arguments, String> {
    let mut output = None;
    let mut object_count = None;
    let mut warmups = None;
    let mut samples = None;
    let mut race_samples = None;
    let mut mount_restored_path = false;
    let mut arguments = arguments.into_iter();
    while let Some(argument) = arguments.next() {
        let flag = argument
            .to_str()
            .ok_or_else(|| "arguments must be valid Unicode".to_owned())?;
        match flag {
            "--output" if output.is_none() => {
                output = Some(path_value(&mut arguments, flag)?);
            }
            "--objects" if object_count.is_none() => {
                object_count = Some(usize_value(&mut arguments, flag)?);
            }
            "--warmups" if warmups.is_none() => {
                warmups = Some(usize_value(&mut arguments, flag)?);
            }
            "--samples" if samples.is_none() => {
                samples = Some(usize_value(&mut arguments, flag)?);
            }
            "--race-samples" if race_samples.is_none() => {
                race_samples = Some(usize_value(&mut arguments, flag)?);
            }
            "--mount-restored-path" if !mount_restored_path => {
                mount_restored_path = true;
            }
            "--output"
            | "--objects"
            | "--warmups"
            | "--samples"
            | "--race-samples"
            | "--mount-restored-path" => {
                return Err(format!("duplicate {flag}"));
            }
            _ => return Err(format!("unknown argument {flag}")),
        }
    }

    let object_count = object_count.ok_or_else(|| "missing --objects".to_owned())?;
    if !(1..=4_096).contains(&object_count) {
        return Err("--objects must be between 1 and 4096".to_owned());
    }
    let performance_mode = warmups.is_some() && samples.is_some() && race_samples.is_none();
    let race_mode =
        warmups.is_none() && samples.is_none() && race_samples.is_some() && mount_restored_path;
    if !performance_mode && !race_mode {
        return Err(
            "choose either --warmups/--samples or --race-samples/--mount-restored-path".to_owned(),
        );
    }
    if warmups == Some(0) || samples == Some(0) || race_samples == Some(0) {
        return Err("sample counts must be positive".to_owned());
    }

    Ok(Arguments {
        output: output.ok_or_else(|| "missing --output".to_owned())?,
        object_count,
        warmups,
        samples,
        race_samples,
        mount_restored_path,
    })
}

fn path_value(
    arguments: &mut impl Iterator<Item = OsString>,
    flag: &str,
) -> Result<PathBuf, String> {
    arguments
        .next()
        .map(PathBuf::from)
        .ok_or_else(|| format!("missing value for {flag}"))
}

fn usize_value(
    arguments: &mut impl Iterator<Item = OsString>,
    flag: &str,
) -> Result<usize, String> {
    arguments
        .next()
        .ok_or_else(|| format!("missing value for {flag}"))?
        .to_str()
        .ok_or_else(|| format!("{flag} must be valid Unicode"))?
        .parse()
        .map_err(|_| format!("{flag} must be a nonnegative integer"))
}

#[cfg(test)]
mod tests {
    use std::ffi::OsString;

    use serde_json::json;

    use super::*;

    fn arguments(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn performance_mode_requires_the_closed_native_inputs() {
        let parsed = parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--warmups",
            "30",
            "--samples",
            "200",
            "--output",
            "/tmp/corefs-object-lease-macos.json",
        ]))
        .unwrap();

        assert_eq!(parsed.object_count, 2_500);
        assert_eq!(parsed.warmups, Some(30));
        assert_eq!(parsed.samples, Some(200));
        assert_eq!(parsed.race_samples, None);
        assert!(!parsed.mount_restored_path);
    }

    #[test]
    fn restored_path_mode_requires_race_samples() {
        let parsed = parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--race-samples",
            "200",
            "--mount-restored-path",
            "--output",
            "/tmp/corefs-object-lease-macos-races.json",
        ]))
        .unwrap();

        assert_eq!(parsed.race_samples, Some(200));
        assert!(parsed.mount_restored_path);
        assert_eq!(parsed.warmups, None);
        assert_eq!(parsed.samples, None);
    }

    #[test]
    fn mixed_or_duplicate_modes_are_rejected() {
        assert!(parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--warmups",
            "30",
            "--samples",
            "200",
            "--race-samples",
            "200",
            "--output",
            "/tmp/out.json",
        ]))
        .is_err());
        assert!(parse_arguments(arguments(&[
            "--objects",
            "2500",
            "--objects",
            "2500",
            "--warmups",
            "30",
            "--samples",
            "200",
            "--output",
            "/tmp/out.json",
        ]))
        .is_err());
    }

    #[test]
    fn characterization_report_schema_is_closed() {
        let report = CharacterizationReport::contract_example();
        let value = serde_json::to_value(report).unwrap();
        assert_eq!(
            value,
            json!({
                "schemaVersion": 1,
                "platform": "macos",
                "hardware": {
                    "model": "contract-example",
                    "architecture": "contract-example"
                },
                "os": {
                    "version": "contract-example",
                    "build": "contract-example"
                },
                "filesystem": {
                    "name": "apfs",
                    "mountPath": "/tmp/contract-example"
                },
                "build": {
                    "profile": "release",
                    "rustc": "1.75.0",
                    "sourceCommit": "contract-example"
                },
                "objectCount": 2500,
                "warmups": 30,
                "samples": 200,
                "safeOpen": {
                    "p50Ms": 1.0,
                    "p95Ms": 1.0,
                    "p99Ms": 1.0
                },
                "lease": {
                    "p50Ms": 1.0,
                    "p95Ms": 1.0,
                    "p99Ms": 1.0
                },
                "resources": {
                    "maximumDescriptorDelta": 65,
                    "postTeardownDescriptorDelta": 0,
                    "residueCount": 0
                },
                "lifecycle": {
                    "creationPassed": true,
                    "startPassed": true,
                    "callbackPanicContained": true,
                    "teardownPassed": true,
                    "callbackAfterRelease": false
                },
                "restoredPath": {
                    "tested": true,
                    "ancestorAboveVolumeCovered": true,
                    "zeroIdRootChangedRejectedClean": true
                },
                "outcomes": {
                    "ordinaryEventsDirtyAll": true,
                    "ambiguousFlagsUnknown": true,
                    "outsideHardLinkRejected": true
                },
                "orderedBoundaryProven": true
            })
        );
    }
}
