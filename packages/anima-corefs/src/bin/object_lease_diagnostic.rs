use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use anima_corefs::benchmark::{
    run_object_lease_diagnostic, BenchmarkError, ObjectLeaseDiagnosticConfig,
};
use serde::Serialize;

#[derive(Debug)]
struct Arguments {
    target: PathBuf,
    output: PathBuf,
    object_count: usize,
    warmups: usize,
    samples: usize,
    mutation_matrix: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ErrorDiagnostic<'a> {
    error: &'a str,
    message: String,
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
    let raw_arguments: Vec<OsString> = std::env::args_os().collect();
    let literal_argv = raw_arguments
        .iter()
        .map(|argument| {
            argument
                .to_str()
                .map(str::to_owned)
                .ok_or_else(|| "arguments must be valid Unicode".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()
        .map_err(|message| ("invalidArguments", message))?;
    let arguments = parse_arguments(raw_arguments.into_iter().skip(1))
        .map_err(|message| ("invalidArguments", message))?;
    let mut output = reserve_output(&arguments.output)
        .map_err(|error| ("outputUnavailable", error.to_string()))?;
    let result = run_object_lease_diagnostic(
        &arguments.target,
        ObjectLeaseDiagnosticConfig {
            object_count: arguments.object_count,
            warmups: arguments.warmups,
            samples: arguments.samples,
            mutation_matrix: arguments.mutation_matrix,
        },
    );
    let mut outcome = match result {
        Ok(outcome) => outcome,
        Err(error) => {
            drop(output);
            let _ = fs::remove_file(&arguments.output);
            return Err(match error {
                BenchmarkError::BackendUnavailable(_) => ("backendUnavailable", error.to_string()),
                _ => ("diagnosticFailed", error.to_string()),
            });
        }
    };
    outcome
        .report_mut()
        .bind_cli_invocation(&arguments.output, literal_argv)
        .map_err(|error| ("outputUnavailable", error.to_string()))?;
    serde_json::to_writer_pretty(&mut output, outcome.report())
        .map_err(|error| ("outputUnavailable", error.to_string()))?;
    writeln!(output).map_err(|error| ("outputUnavailable", error.to_string()))?;
    output
        .sync_all()
        .map_err(|error| ("outputUnavailable", error.to_string()))?;
    if !outcome.report().native_acceptance_passed() {
        return Err((
            "nativeAcceptanceFailed",
            "native lease diagnostic did not meet its correctness and lifecycle gates".to_owned(),
        ));
    }
    Ok(())
}

fn reserve_output(path: &PathBuf) -> io::Result<File> {
    OpenOptions::new().write(true).create_new(true).open(path)
}

fn parse_arguments(arguments: impl IntoIterator<Item = OsString>) -> Result<Arguments, String> {
    let mut target = None;
    let mut output = None;
    let mut object_count = None;
    let mut warmups = None;
    let mut samples = None;
    let mut mutation_matrix = false;
    let mut arguments = arguments.into_iter();
    while let Some(argument) = arguments.next() {
        let flag = argument
            .to_str()
            .ok_or_else(|| "arguments must be valid Unicode".to_owned())?;
        match flag {
            "--target" if target.is_none() => {
                target = Some(path_value(&mut arguments, flag)?);
            }
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
            "--mutation-matrix" if !mutation_matrix => mutation_matrix = true,
            "--target" | "--output" | "--objects" | "--warmups" | "--samples"
            | "--mutation-matrix" => return Err(format!("duplicate {flag}")),
            _ => return Err(format!("unknown argument {flag}")),
        }
    }
    Ok(Arguments {
        target: target.ok_or_else(|| "missing --target".to_owned())?,
        output: output.ok_or_else(|| "missing --output".to_owned())?,
        object_count: object_count.ok_or_else(|| "missing --objects".to_owned())?,
        warmups: warmups.ok_or_else(|| "missing --warmups".to_owned())?,
        samples: samples.ok_or_else(|| "missing --samples".to_owned())?,
        mutation_matrix,
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
