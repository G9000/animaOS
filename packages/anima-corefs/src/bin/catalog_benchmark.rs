use std::env;
use std::fs;
use std::path::PathBuf;

use anima_corefs::benchmark::{
    build_fixture_matrix, run_fixture_benchmark, BenchmarkRunConfig, FixtureBenchmarkReport,
    REFERENCE_MEASURED_COMMITS, REFERENCE_WARMUP_COMMITS,
};
use serde::Serialize;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RunnerReport {
    schema_version: u16,
    warmup_commits: usize,
    measured_commits: usize,
    fixtures: Vec<FixtureBenchmarkReport>,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("catalog benchmark failed: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut target = None;
    let mut warmup_commits = REFERENCE_WARMUP_COMMITS;
    let mut measured_commits = REFERENCE_MEASURED_COMMITS;
    let mut args = env::args().skip(1);
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--target" => target = args.next().map(PathBuf::from),
            "--warmups" => warmup_commits = parse_count(args.next(), "--warmups")?,
            "--samples" => measured_commits = parse_count(args.next(), "--samples")?,
            _ => return Err(format!("unknown argument: {argument}").into()),
        }
    }
    let target = target.ok_or("--target is required")?;
    fs::create_dir_all(&target)?;

    let config = BenchmarkRunConfig {
        warmup_commits,
        measured_commits,
    };
    let mut reports = Vec::new();
    for fixture in build_fixture_matrix()? {
        let fixture_root = target.join(fixture.kind().name());
        if fixture_root.exists() {
            return Err(format!(
                "fixture target already exists; refuse to mix benchmark runs: {}",
                fixture_root.display()
            )
            .into());
        }
        reports.push(run_fixture_benchmark(&fixture_root, &fixture, config)?);
    }

    let report = RunnerReport {
        schema_version: 1,
        warmup_commits,
        measured_commits,
        fixtures: reports,
    };
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}

fn parse_count(value: Option<String>, argument: &str) -> Result<usize, Box<dyn std::error::Error>> {
    let value = value.ok_or_else(|| format!("{argument} requires a value"))?;
    Ok(value.parse()?)
}
