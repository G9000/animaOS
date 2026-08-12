fn main() {
    for name in [
        "ANIMA_DESKTOP_VERSION_OVERRIDE",
        "ANIMA_DRAFT_CLEANUP_LEGACY_FIXTURE",
        "ANIMA_DRAFT_CLEANUP_RELEASE",
        "ANIMA_INSTALLER_FAMILY",
        "ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX",
        "ANIMA_INSTALL_IDENTITY_PREVIOUS_PUBLIC_KEY_HEX",
        "ANIMA_HOST_IDENTITY_SIGNATURE_HEX",
    ] {
        println!("cargo:rerun-if-env-changed={name}");
    }
    if let (Ok(public_key), Ok(signature)) = (
        std::env::var("ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX"),
        std::env::var("ANIMA_HOST_IDENTITY_SIGNATURE_HEX"),
    ) {
        println!(
            "cargo:rustc-env=ANIMA_EMBEDDED_HOST_IDENTITY=anima-host-identity-v1:{public_key}:{signature}"
        );
    }
    tauri_build::build()
}
