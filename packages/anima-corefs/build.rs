use std::env;
use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use sha2::{Digest, Sha256};

fn main() {
    println!("cargo:rerun-if-env-changed=RUSTC");
    println!("cargo:rerun-if-env-changed=TARGET");

    let manifest = PathBuf::from(
        env::var_os("CARGO_MANIFEST_DIR").expect("Cargo must provide CARGO_MANIFEST_DIR"),
    );
    let repository = git_text(&manifest, &["rev-parse", "--show-toplevel"])
        .map(PathBuf::from)
        .unwrap_or_else(|| manifest.join("../.."));
    let spike_source = manifest.join("src/bin/object_lease_macos_spike.rs");
    let cargo_lock = repository.join("Cargo.lock");

    println!("cargo:rerun-if-changed={}", spike_source.display());
    println!("cargo:rerun-if-changed={}", cargo_lock.display());
    for git_path in ["HEAD", "index"] {
        if let Some(path) = git_text(&manifest, &["rev-parse", "--git-path", git_path]) {
            let path = PathBuf::from(path);
            let path = if path.is_absolute() {
                path
            } else {
                repository.join(path)
            };
            println!("cargo:rerun-if-changed={}", path.display());
        }
    }

    let rustc = env::var_os("RUSTC").expect("Cargo must provide RUSTC to the build script");
    let rustc_identity = command_text(Command::new(rustc).arg("--version"))
        .expect("the build compiler must report its version");
    let target = env::var("TARGET").expect("Cargo must provide TARGET to the build script");
    let source_commit =
        git_text(&manifest, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unavailable".to_owned());
    let tracked_tree_clean = git_output(
        &manifest,
        &["status", "--porcelain=v1", "--untracked-files=no"],
    )
    .is_some_and(|output| output.is_empty());

    emit_env("ANIMA_CORE_BUILD_RUSTC", &rustc_identity);
    emit_env("ANIMA_CORE_BUILD_TARGET", &target);
    emit_env("ANIMA_CORE_BUILD_SOURCE_COMMIT", &source_commit);
    emit_env(
        "ANIMA_CORE_BUILD_TRACKED_TREE_CLEAN",
        if tracked_tree_clean { "true" } else { "false" },
    );
    emit_artifact("SPIKE_SOURCE", &repository, &spike_source);
    emit_artifact("CARGO_LOCK", &repository, &cargo_lock);
}

fn emit_artifact(label: &str, repository: &Path, path: &Path) {
    let bytes = fs::read(path).unwrap_or_else(|error| {
        panic!("read build provenance artifact {}: {error}", path.display())
    });
    let digest = Sha256::digest(bytes);
    let mut sha256 = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut sha256, "{byte:02x}").expect("write SHA-256 hex");
    }
    let git_blob = git_text(
        repository,
        &[
            "hash-object",
            "--no-filters",
            path.to_str()
                .expect("build provenance artifact path must be UTF-8"),
        ],
    )
    .unwrap_or_else(|| "unavailable".to_owned());
    emit_env(&format!("ANIMA_CORE_BUILD_{label}_SHA256"), &sha256);
    emit_env(&format!("ANIMA_CORE_BUILD_{label}_BLOB"), &git_blob);
}

fn emit_env(name: &str, value: &str) {
    assert!(
        !value.is_empty() && !value.contains(['\r', '\n', '\0']),
        "{name} must be a non-empty single-line value"
    );
    println!("cargo:rustc-env={name}={value}");
}

fn git_text(directory: &Path, arguments: &[&str]) -> Option<String> {
    let output = git_output(directory, arguments)?;
    let text = String::from_utf8(output).ok()?.trim().to_owned();
    (!text.is_empty()).then_some(text)
}

fn git_output(directory: &Path, arguments: &[&str]) -> Option<Vec<u8>> {
    let output = Command::new("git")
        .arg("-C")
        .arg(directory)
        .args(arguments)
        .output()
        .ok()?;
    output.status.success().then_some(output.stdout)
}

fn command_text(command: &mut Command) -> Option<String> {
    let output = command.output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8(output.stdout).ok()?.trim().to_owned();
    (!text.is_empty()).then_some(text)
}
