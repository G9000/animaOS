use std::env;
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-env-changed=RUSTC");
    let rustc = env::var_os("RUSTC").expect("Cargo must provide RUSTC to the build script");
    let output = Command::new(rustc)
        .arg("--version")
        .output()
        .expect("the build compiler must report its version");
    assert!(
        output.status.success(),
        "the build compiler version command failed"
    );
    let identity = String::from_utf8(output.stdout)
        .expect("the build compiler identity must be UTF-8")
        .trim()
        .to_owned();
    assert!(
        !identity.is_empty(),
        "the build compiler identity must not be empty"
    );
    println!("cargo:rustc-env=ANIMA_CORE_BUILD_RUSTC={identity}");
}
