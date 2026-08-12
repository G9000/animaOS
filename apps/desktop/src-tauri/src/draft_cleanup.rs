use fs4::FileExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    process::Command,
    sync::Mutex,
    time::{Duration, Instant},
};
use unicode_normalization::UnicodeNormalization;

const BUNDLE_ID: &str = "com.leoca.anima";
const PROTOCOL_VERSION: u16 = 1;
const CAPABILITY_TTL: Duration = Duration::from_secs(5);
const LOCK_FILE: &str = "legacy-draft-cleanup-v1.lock";
const EPOCH_FILE: &str = "legacy-draft-cleanup-v1.epoch.json";
const IDENTITY_DOMAIN: &[u8] = b"anima-installed-identity-v1\0";
const HOST_IDENTITY_MESSAGE: &[u8] = b"anima-host-identity-v1\0com.leoca.anima";
const HOST_IDENTITY_PREFIX: &[u8] = b"anima-host-identity-v1:";
const HOST_IDENTITY_MARKER_LEN: usize = HOST_IDENTITY_PREFIX.len() + 64 + 1 + 128;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InstalledIdentity {
    pub bundle_id: String,
    pub installer_family: String,
    pub package_version: String,
    pub canonical_target: String,
    pub native_file_identity: String,
    pub platform_identity: String,
}

impl InstalledIdentity {
    pub fn digest(&self) -> [u8; 32] {
        installed_identity_digest(&[
            &self.bundle_id,
            &self.installer_family,
            &self.package_version,
            &self.canonical_target,
            &self.native_file_identity,
            &self.platform_identity,
        ])
    }
}

pub fn installed_identity_digest(fields: &[&str]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(IDENTITY_DOMAIN);
    for field in fields {
        let normalized: String = field.nfc().collect();
        let bytes = normalized.as_bytes();
        hasher.update(u32::try_from(bytes.len()).unwrap_or(u32::MAX).to_be_bytes());
        hasher.update(bytes);
    }
    hasher.finalize().into()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CleanupEpoch {
    protocol_version: u16,
    installed_identity_digest: String,
    process_start_identity: u64,
    boot_identity: String,
}

#[derive(Clone)]
struct Capability {
    audience: [u8; 32],
    expires_at: Instant,
}

struct EnabledAuthority {
    _lease: File,
    epoch_path: PathBuf,
    identity_digest: [u8; 32],
    boot_identity: String,
    eligible_after_reboot: bool,
    capabilities: HashMap<[u8; 32], Capability>,
    executable_classifications: HashMap<String, bool>,
}

enum AuthorityState {
    Disabled,
    Enabled(EnabledAuthority),
}

pub struct DraftCleanupAuthority {
    state: Mutex<AuthorityState>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IssuedDraftCleanupCapability {
    capability: String,
    expires_in_ms: u16,
}

impl DraftCleanupAuthority {
    pub fn bootstrap() -> Result<Self, String> {
        // This build-time-only branch exists solely in the protected release
        // workflow's unpublished predecessor fixture. It models an older host
        // that neither knows nor honors the V1 launch gate so the real native
        // installers must prove they can stop it during replacement.
        if option_env!("ANIMA_DRAFT_CLEANUP_LEGACY_FIXTURE") == Some("1") {
            // Force the cross-version signed identity into the fixture's final
            // linked image even though its legacy behavior returns before the
            // cleanup-capable bootstrap path.
            std::hint::black_box(option_env!("ANIMA_EMBEDDED_HOST_IDENTITY"));
            std::thread::park_timeout(Duration::from_secs(600));
            return Ok(Self {
                state: Mutex::new(AuthorityState::Disabled),
            });
        }
        if !cleanup_release_intent() {
            return Ok(Self {
                state: Mutex::new(AuthorityState::Disabled),
            });
        }

        verify_own_embedded_host_identity()?;
        let identity = verify_installed_identity()?;
        let identity_digest = identity.digest();
        let boot_identity = boot_identity()?;
        let process_start_identity = inspect_process_start_time(std::process::id())?
            .ok_or_else(|| "current process start identity is unavailable".to_owned())?;
        let state_dir = cleanup_state_dir()?;
        let lease = acquire_lifetime_lease(&state_dir)?;
        let mut executable_classifications = HashMap::new();
        verify_no_other_anima_hosts(&identity, &mut executable_classifications)?;
        let epoch_path = state_dir.join(EPOCH_FILE);
        let prior_epoch = read_epoch(&epoch_path)?;
        let eligible_after_reboot =
            epoch_is_eligible(prior_epoch.as_ref(), identity_digest, &boot_identity);
        if !eligible_after_reboot {
            write_epoch_atomic(
                &epoch_path,
                &CleanupEpoch {
                    protocol_version: PROTOCOL_VERSION,
                    installed_identity_digest: hex::encode(identity_digest),
                    process_start_identity,
                    boot_identity: boot_identity.clone(),
                },
            )?;
        }

        Ok(Self {
            state: Mutex::new(AuthorityState::Enabled(EnabledAuthority {
                _lease: lease,
                epoch_path,
                identity_digest,
                boot_identity,
                eligible_after_reboot,
                capabilities: HashMap::new(),
                executable_classifications,
            })),
        })
    }

    #[cfg(test)]
    fn enabled_for_test(identity_digest: [u8; 32], boot_identity: &str) -> Self {
        let lease = tempfile_file_for_test();
        Self {
            state: Mutex::new(AuthorityState::Enabled(EnabledAuthority {
                _lease: lease,
                epoch_path: PathBuf::new(),
                identity_digest,
                boot_identity: boot_identity.to_owned(),
                eligible_after_reboot: true,
                capabilities: HashMap::new(),
                executable_classifications: HashMap::new(),
            })),
        }
    }

    fn issue_with_verifier<F>(
        &self,
        audience_digest: &str,
        verify: F,
    ) -> Result<IssuedDraftCleanupCapability, String>
    where
        F: FnOnce([u8; 32], &str, &Path, &mut HashMap<String, bool>) -> Result<(), String>,
    {
        let audience = decode_digest(audience_digest)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "cleanup authority is unavailable")?;
        let AuthorityState::Enabled(enabled) = &mut *state else {
            return Err("draft cleanup authority is unavailable".to_owned());
        };
        if !enabled.eligible_after_reboot {
            return Err("draft cleanup epoch has not crossed an OS reboot".to_owned());
        }
        verify(
            enabled.identity_digest,
            &enabled.boot_identity,
            &enabled.epoch_path,
            &mut enabled.executable_classifications,
        )?;
        let mut key = [0_u8; 32];
        getrandom::getrandom(&mut key).map_err(|_| "cannot issue cleanup authority".to_owned())?;
        enabled
            .capabilities
            .retain(|_, value| value.expires_at > Instant::now());
        enabled.capabilities.insert(
            key,
            Capability {
                audience,
                expires_at: Instant::now() + CAPABILITY_TTL,
            },
        );
        Ok(IssuedDraftCleanupCapability {
            capability: hex::encode(key),
            expires_in_ms: CAPABILITY_TTL.as_millis() as u16,
        })
    }

    fn consume_with_verifier<F>(
        &self,
        capability: &str,
        audience_digest: &str,
        verify: F,
    ) -> Result<bool, String>
    where
        F: FnOnce([u8; 32], &str, &Path, &mut HashMap<String, bool>) -> Result<(), String>,
    {
        let capability_key = decode_digest(capability)?;
        let audience = decode_digest(audience_digest)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "cleanup authority is unavailable")?;
        let AuthorityState::Enabled(enabled) = &mut *state else {
            return Err("draft cleanup authority is unavailable".to_owned());
        };
        let issued = enabled
            .capabilities
            .get(&capability_key)
            .cloned()
            .ok_or_else(|| "cleanup capability is absent or already consumed".to_owned())?;
        if issued.expires_at <= Instant::now() || issued.audience != audience {
            enabled.capabilities.remove(&capability_key);
            return Err("cleanup capability is expired or has the wrong audience".to_owned());
        }
        verify(
            enabled.identity_digest,
            &enabled.boot_identity,
            &enabled.epoch_path,
            &mut enabled.executable_classifications,
        )?;
        if issued.expires_at <= Instant::now() {
            enabled.capabilities.remove(&capability_key);
            return Err("cleanup capability expired during native verification".to_owned());
        }
        enabled.capabilities.remove(&capability_key);
        Ok(true)
    }

    pub fn issue(&self, audience_digest: &str) -> Result<IssuedDraftCleanupCapability, String> {
        self.issue_with_verifier(audience_digest, verify_runtime_authority)
    }

    pub fn consume(&self, capability: &str, audience_digest: &str) -> Result<bool, String> {
        self.consume_with_verifier(capability, audience_digest, verify_runtime_authority)
    }

    #[doc(hidden)]
    pub fn verify_packaged_runtime_cycle(&self) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "cleanup authority is unavailable")?;
        let AuthorityState::Enabled(enabled) = &mut *state else {
            return Err("draft cleanup authority is unavailable".to_owned());
        };
        // The first exact verification models issue and discovers any WebView
        // helper image identities created after pre-WebView bootstrap. The
        // five-second capability starts only after issue returns; the second
        // exact verification is therefore the consume-time deadline gate.
        verify_runtime_authority(
            enabled.identity_digest,
            &enabled.boot_identity,
            &enabled.epoch_path,
            &mut enabled.executable_classifications,
        )?;
        let expires_at = Instant::now() + CAPABILITY_TTL;
        verify_runtime_authority(
            enabled.identity_digest,
            &enabled.boot_identity,
            &enabled.epoch_path,
            &mut enabled.executable_classifications,
        )?;
        if Instant::now() >= expires_at {
            return Err("packaged consume-time census exceeded five seconds".to_owned());
        }
        Ok(())
    }
}

#[tauri::command]
pub fn draft_cleanup_issue_v1(
    authority: tauri::State<'_, DraftCleanupAuthority>,
    audience_digest: String,
) -> Result<IssuedDraftCleanupCapability, String> {
    authority.issue(&audience_digest)
}

#[tauri::command]
pub fn draft_cleanup_consume_v1(
    authority: tauri::State<'_, DraftCleanupAuthority>,
    capability: String,
    audience_digest: String,
) -> Result<bool, String> {
    authority.consume(&capability, &audience_digest)
}

fn cleanup_release_intent() -> bool {
    option_env!("ANIMA_DRAFT_CLEANUP_RELEASE") == Some("1")
        && matches!(
            option_env!("ANIMA_INSTALLER_FAMILY"),
            Some("windows" | "macos" | "debian" | "rpm")
        )
}

fn verify_embedded_package_version(runtime_version: &str) -> Result<(), String> {
    let embedded = option_env!("ANIMA_DESKTOP_VERSION_OVERRIDE")
        .ok_or_else(|| "cleanup release has no embedded package version".to_owned())?;
    if runtime_version != embedded {
        return Err("installed package version does not match the running executable".to_owned());
    }
    Ok(())
}

fn decode_digest(value: &str) -> Result<[u8; 32], String> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("cleanup digest must be 64 lowercase hexadecimal characters".to_owned());
    }
    let decoded = hex::decode(value).map_err(|_| "cleanup digest is malformed".to_owned())?;
    decoded
        .try_into()
        .map_err(|_| "cleanup digest has the wrong length".to_owned())
}

fn verify_own_embedded_host_identity() -> Result<(), String> {
    let marker = option_env!("ANIMA_EMBEDDED_HOST_IDENTITY")
        .ok_or_else(|| "cleanup release has no signed host identity".to_owned())?;
    let current_key = option_env!("ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX")
        .ok_or_else(|| "cleanup release has no host-identity public key".to_owned())?;
    if marker.len() != HOST_IDENTITY_MARKER_LEN
        || marker.as_bytes()[HOST_IDENTITY_PREFIX.len()..HOST_IDENTITY_PREFIX.len() + 64]
            != *current_key.as_bytes()
        || !host_identity_marker_is_trusted(marker.as_bytes())
    {
        return Err("cleanup release has an invalid signed host identity".to_owned());
    }
    Ok(())
}

fn host_identity_marker_is_trusted(marker: &[u8]) -> bool {
    let trusted_keys = [
        option_env!("ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX"),
        option_env!("ANIMA_INSTALL_IDENTITY_PREVIOUS_PUBLIC_KEY_HEX"),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>();
    host_identity_marker_is_trusted_by(marker, &trusted_keys)
}

fn host_identity_marker_is_trusted_by(marker: &[u8], trusted_keys: &[&str]) -> bool {
    use ring::signature::{UnparsedPublicKey, ED25519};

    if marker.len() != HOST_IDENTITY_MARKER_LEN || !marker.starts_with(HOST_IDENTITY_PREFIX) {
        return false;
    }
    let key_start = HOST_IDENTITY_PREFIX.len();
    let key_end = key_start + 64;
    if marker.get(key_end) != Some(&b':') {
        return false;
    }
    let Ok(key_hex) = std::str::from_utf8(&marker[key_start..key_end]) else {
        return false;
    };
    if !trusted_keys.contains(&key_hex) {
        return false;
    }
    let Ok(key) = hex::decode(key_hex) else {
        return false;
    };
    let Ok(signature_hex) = std::str::from_utf8(&marker[key_end + 1..]) else {
        return false;
    };
    let Ok(signature) = hex::decode(signature_hex) else {
        return false;
    };
    key.len() == 32
        && signature.len() == 64
        && UnparsedPublicKey::new(&ED25519, key)
            .verify(HOST_IDENTITY_MESSAGE, &signature)
            .is_ok()
}

fn epoch_is_eligible(
    epoch: Option<&CleanupEpoch>,
    installed_identity_digest: [u8; 32],
    current_boot_identity: &str,
) -> bool {
    epoch.is_some_and(|epoch| {
        epoch.protocol_version == PROTOCOL_VERSION
            && epoch.installed_identity_digest == hex::encode(installed_identity_digest)
            && epoch.boot_identity != current_boot_identity
    })
}

fn cleanup_state_dir() -> Result<PathBuf, String> {
    let base = dirs::data_local_dir()
        .ok_or_else(|| "app-local data directory is unavailable".to_owned())?;
    let path = base.join(BUNDLE_ID).join("draft-cleanup");
    if !path.exists() {
        create_owner_only_directory(&path)?;
    }
    protect_directory(&path)?;
    Ok(path)
}

#[cfg(unix)]
fn create_owner_only_directory(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::DirBuilderExt;
    let mut builder = fs::DirBuilder::new();
    builder.recursive(true).mode(0o700);
    builder
        .create(path)
        .map_err(|error| format!("cannot create cleanup state directory: {error}"))
}

#[cfg(windows)]
fn create_owner_only_directory(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path)
        .map_err(|error| format!("cannot create cleanup state directory: {error}"))?;
    protect_directory(path)
}

#[cfg(not(any(unix, windows)))]
fn create_owner_only_directory(_path: &Path) -> Result<(), String> {
    Err("draft cleanup is unsupported on this platform".to_owned())
}

#[cfg(unix)]
fn protect_directory(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect cleanup directory: {error}"))?;
    if !metadata.file_type().is_dir()
        || metadata.file_type().is_symlink()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.permissions().mode() & 0o777 != 0o700
    {
        return Err("cleanup state directory is not an owner-controlled directory".to_owned());
    }
    Ok(())
}

#[cfg(windows)]
fn protect_directory(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect cleanup path: {error}"))?;
    if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
        return Err("cleanup state path is not a regular directory".to_owned());
    }
    set_and_verify_windows_owner_acl(path, true)
}

#[cfg(not(any(unix, windows)))]
fn protect_directory(_path: &Path) -> Result<(), String> {
    Err("draft cleanup is unsupported on this platform".to_owned())
}

fn acquire_lifetime_lease(directory: &Path) -> Result<File, String> {
    let path = directory.join(LOCK_FILE);
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options
        .open(&path)
        .map_err(|error| format!("cannot open cleanup launch gate: {error}"))?;
    protect_regular_file(&path)?;
    file.try_lock_exclusive()
        .map_err(|_| "another ANIMA host owns the cleanup launch gate".to_owned())?;
    Ok(file)
}

#[doc(hidden)]
pub fn acquire_test_launch_gate(directory: &Path) -> Result<File, String> {
    if !directory.exists() {
        create_owner_only_directory(directory)?;
    }
    protect_directory(directory)?;
    acquire_lifetime_lease(directory)
}

#[cfg(unix)]
fn protect_regular_file(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect cleanup file: {error}"))?;
    if !metadata.file_type().is_file()
        || metadata.file_type().is_symlink()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.permissions().mode() & 0o077 != 0
    {
        return Err("cleanup state is not an owner-controlled regular file".to_owned());
    }
    Ok(())
}

#[cfg(windows)]
fn protect_regular_file(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect cleanup file: {error}"))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err("cleanup state path is not a regular file".to_owned());
    }
    set_and_verify_windows_owner_acl(path, false)
}

#[cfg(windows)]
fn set_and_verify_windows_owner_acl(path: &Path, directory: bool) -> Result<(), String> {
    // Construct a fresh protected DACL instead of editing the inherited ACL in
    // place. That removes any pre-existing explicit Everyone/other-user ACEs.
    let script = r#"
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$isDirectory=$env:ANIMA_ACL_KIND -eq 'directory'
if($isDirectory){$acl=New-Object Security.AccessControl.DirectorySecurity}else{$acl=New-Object Security.AccessControl.FileSecurity}
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true,$false)
$inheritance=if($isDirectory){[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}else{[Security.AccessControl.InheritanceFlags]::None}
$rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,[Security.AccessControl.FileSystemRights]::FullControl,$inheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $env:ANIMA_ACL_PATH -AclObject $acl
$check=Get-Acl -LiteralPath $env:ANIMA_ACL_PATH
$owner=(New-Object Security.Principal.NTAccount($check.Owner)).Translate([Security.Principal.SecurityIdentifier]).Value
$rules=@($check.Access)
if($owner -ne $sid.Value -or $rules.Count -ne 1){exit 2}
$actual=$rules[0]
$actualSid=$actual.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
if($actual.IsInherited -or $actual.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $actualSid -ne $sid.Value){exit 3}
if(($actual.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl){exit 4}
"#;
    let status = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .env("ANIMA_ACL_PATH", path)
        .env(
            "ANIMA_ACL_KIND",
            if directory { "directory" } else { "file" },
        )
        .status()
        .map_err(|error| format!("cannot establish owner-only cleanup ACL: {error}"))?;
    if !status.success() {
        return Err(
            "cleanup ACL contains an owner or access rule outside the current user".to_owned(),
        );
    }
    Ok(())
}

#[cfg(not(any(unix, windows)))]
fn protect_regular_file(_path: &Path) -> Result<(), String> {
    Err("draft cleanup is unsupported on this platform".to_owned())
}

fn read_epoch(path: &Path) -> Result<Option<CleanupEpoch>, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("cannot read cleanup epoch: {error}")),
    };
    protect_regular_file(path)?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|_| "cleanup epoch is malformed".to_owned())
}

fn write_epoch_atomic(path: &Path, epoch: &CleanupEpoch) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "cleanup epoch has no parent".to_owned())?;
    let mut nonce = [0_u8; 16];
    getrandom::getrandom(&mut nonce).map_err(|_| "cannot create cleanup epoch nonce".to_owned())?;
    let temp = parent.join(format!(".{EPOCH_FILE}.{}.tmp", hex::encode(nonce)));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| {
        let mut file = options
            .open(&temp)
            .map_err(|error| format!("cannot create cleanup epoch: {error}"))?;
        let body =
            serde_json::to_vec(epoch).map_err(|_| "cannot encode cleanup epoch".to_owned())?;
        file.write_all(&body)
            .map_err(|error| format!("cannot write cleanup epoch: {error}"))?;
        file.sync_all()
            .map_err(|error| format!("cannot sync cleanup epoch: {error}"))?;
        protect_regular_file(&temp)?;
        atomic_replace(&temp, path)?;
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| format!("cannot sync cleanup directory: {error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

#[cfg(unix)]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), String> {
    fs::rename(source, destination)
        .map_err(|error| format!("cannot publish cleanup epoch: {error}"))
}

#[cfg(windows)]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };
    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    if unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    } == 0
    {
        return Err(format!(
            "cannot publish cleanup epoch: {}",
            io::Error::last_os_error()
        ));
    }
    Ok(())
}

#[cfg(not(any(unix, windows)))]
fn atomic_replace(_source: &Path, _destination: &Path) -> Result<(), String> {
    Err("atomic cleanup epoch replacement is unsupported".to_owned())
}

fn verify_runtime_authority(
    expected_digest: [u8; 32],
    expected_boot: &str,
    _epoch: &Path,
    executable_classifications: &mut HashMap<String, bool>,
) -> Result<(), String> {
    let identity = verify_installed_identity()?;
    if identity.digest() != expected_digest {
        return Err("installed ANIMA identity changed".to_owned());
    }
    if boot_identity()? != expected_boot {
        return Err("OS boot identity changed during cleanup".to_owned());
    }
    verify_no_other_anima_hosts(&identity, executable_classifications)
}

fn command_output(program: &str, arguments: &[&str]) -> Result<String, String> {
    let output = Command::new(program)
        .args(arguments)
        .output()
        .map_err(|error| format!("cannot inspect installed ANIMA: {error}"))?;
    if !output.status.success() {
        return Err("installed ANIMA identity verification failed".to_owned());
    }
    String::from_utf8(output.stdout).map_err(|_| "installed ANIMA metadata is not UTF-8".to_owned())
}

fn command_combined(program: &str, arguments: &[&str]) -> Result<String, String> {
    let output = Command::new(program)
        .args(arguments)
        .output()
        .map_err(|error| format!("cannot inspect installed ANIMA: {error}"))?;
    if !output.status.success() {
        return Err("installed ANIMA identity verification failed".to_owned());
    }
    let mut bytes = output.stdout;
    bytes.extend(output.stderr);
    String::from_utf8(bytes).map_err(|_| "installed ANIMA metadata is not UTF-8".to_owned())
}

#[cfg(target_os = "linux")]
fn sha256_file(path: &Path) -> Result<String, String> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect installed target: {error}"))?;
    if !metadata.file_type().is_file()
        || metadata.file_type().is_symlink()
        || metadata.uid() != 0
        || metadata.permissions().mode() & 0o022 != 0
    {
        return Err("installed target is not a protected root-owned regular file".to_owned());
    }
    let file =
        File::open(path).map_err(|error| format!("cannot read installed target: {error}"))?;
    sha256_file_handle(&file)
}

fn sha256_file_handle(file: &File) -> Result<String, String> {
    use std::io::{Read, Seek, SeekFrom};

    let mut reader = file
        .try_clone()
        .map_err(|error| format!("cannot clone executable handle: {error}"))?;
    reader
        .seek(SeekFrom::Start(0))
        .map_err(|error| format!("cannot seek executable handle: {error}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|error| format!("cannot read executable handle: {error}"))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex::encode(hasher.finalize()))
}

#[cfg(target_os = "linux")]
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LinuxInstallManifest {
    schema_version: u16,
    bundle_id: String,
    package_family: String,
    package_name: String,
    package_version: String,
    executable_path: String,
    executable_sha256: String,
    launch_targets: Vec<LinuxLaunchTarget>,
}

#[cfg(target_os = "linux")]
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LinuxLaunchTarget {
    path: String,
    sha256: String,
}

#[cfg(target_os = "linux")]
fn verify_installed_identity() -> Result<InstalledIdentity, String> {
    use ring::signature::{UnparsedPublicKey, ED25519};
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    const MANIFEST_PATH: &str = "/usr/share/anima/install-identity-v1.json";
    const SIGNATURE_PATH: &str = "/usr/share/anima/install-identity-v1.json.sig";
    let public_key_hex = option_env!("ANIMA_INSTALL_IDENTITY_PUBLIC_KEY_HEX")
        .ok_or_else(|| "Linux release identity key is not embedded".to_owned())?;
    let public_key = hex::decode(public_key_hex)
        .map_err(|_| "embedded Linux release identity key is malformed".to_owned())?;
    if public_key.len() != 32 {
        return Err("embedded Linux release identity key has the wrong length".to_owned());
    }
    let manifest_path = Path::new(MANIFEST_PATH);
    let signature_path = Path::new(SIGNATURE_PATH);
    for path in [manifest_path, signature_path] {
        let metadata = fs::symlink_metadata(path)
            .map_err(|error| format!("cannot inspect Linux install identity: {error}"))?;
        if !metadata.file_type().is_file()
            || metadata.file_type().is_symlink()
            || metadata.uid() != 0
            || metadata.permissions().mode() & 0o022 != 0
        {
            return Err(
                "Linux install identity is not a protected root-owned regular file".to_owned(),
            );
        }
    }
    let bytes = fs::read(manifest_path)
        .map_err(|error| format!("cannot read Linux install identity: {error}"))?;
    let signature = fs::read(signature_path)
        .map_err(|error| format!("cannot read Linux install signature: {error}"))?;
    if signature.len() != 64 {
        return Err("Linux install identity signature has the wrong length".to_owned());
    }
    UnparsedPublicKey::new(&ED25519, public_key)
        .verify(&bytes, &signature)
        .map_err(|_| "Linux install identity signature is invalid".to_owned())?;
    let manifest: LinuxInstallManifest = serde_json::from_slice(&bytes)
        .map_err(|_| "Linux install identity manifest is malformed".to_owned())?;
    if manifest.schema_version != 1
        || manifest.bundle_id != BUNDLE_ID
        || !matches!(manifest.package_family.as_str(), "debian" | "rpm")
        || manifest.launch_targets.len() != 1
        || !is_lower_sha256(&manifest.executable_sha256)
    {
        return Err("Linux install identity manifest violates the release contract".to_owned());
    }
    verify_embedded_package_version(&manifest.package_version)?;
    let executable = PathBuf::from(&manifest.executable_path);
    let canonical_executable = executable
        .canonicalize()
        .map_err(|error| format!("cannot resolve installed executable: {error}"))?;
    let current_executable = std::env::current_exe()
        .and_then(|path| path.canonicalize())
        .map_err(|error| format!("cannot resolve current executable: {error}"))?;
    if canonical_executable != executable || current_executable != canonical_executable {
        return Err("running executable is not the canonical package-owned target".to_owned());
    }
    verify_linux_package_owner(manifest_path, &manifest)?;
    verify_linux_package_owner(signature_path, &manifest)?;
    if sha256_file(&executable)? != manifest.executable_sha256 {
        return Err("installed executable does not match its signed identity".to_owned());
    }
    verify_linux_package_owner(&executable, &manifest)?;
    for target in &manifest.launch_targets {
        if !is_lower_sha256(&target.sha256) {
            return Err("Linux launch-target hash is malformed".to_owned());
        }
        let path = PathBuf::from(&target.path);
        if path
            .canonicalize()
            .map_err(|_| "Linux launch target is missing".to_owned())?
            != path
            || sha256_file(&path)? != target.sha256
        {
            return Err("Linux launch target does not match its signed identity".to_owned());
        }
        verify_linux_package_owner(&path, &manifest)?;
        let desktop = fs::read_to_string(&path)
            .map_err(|_| "Linux desktop entry is unreadable".to_owned())?;
        let expected_exec = format!("Exec={}", manifest.executable_path);
        if !desktop.lines().any(|line| line.trim() == expected_exec) {
            return Err(
                "Linux desktop entry does not resolve to the canonical executable".to_owned(),
            );
        }
    }
    let metadata = fs::metadata(&executable)
        .map_err(|error| format!("cannot inspect installed executable: {error}"))?;
    Ok(InstalledIdentity {
        bundle_id: manifest.bundle_id,
        installer_family: manifest.package_family,
        package_version: manifest.package_version,
        canonical_target: manifest.executable_path,
        native_file_identity: format!("{}:{}", metadata.dev(), metadata.ino()),
        platform_identity: format!(
            "{}:{}:{}:{}",
            manifest.package_name,
            sha256_file(manifest_path)?,
            public_key_hex,
            manifest.executable_sha256,
        ),
    })
}

#[cfg(target_os = "linux")]
fn verify_linux_package_owner(path: &Path, manifest: &LinuxInstallManifest) -> Result<(), String> {
    let path = path
        .to_str()
        .ok_or_else(|| "installed Linux path is not UTF-8".to_owned())?;
    let owner_matches = if manifest.package_family == "debian" {
        command_output("dpkg-query", &["-S", path])?
            .lines()
            .filter_map(|line| line.rsplit_once(": ").map(|(owner, _)| owner))
            .any(|owner| owner.split(':').next() == Some(manifest.package_name.as_str()))
    } else {
        command_output("rpm", &["-qf", "--qf", "%{NAME}", path])?.trim() == manifest.package_name
    };
    if !owner_matches {
        return Err("installed Linux file has the wrong package owner".to_owned());
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn is_lower_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(target_os = "macos")]
fn verify_installed_identity() -> Result<InstalledIdentity, String> {
    let current = std::env::current_exe()
        .and_then(|path| path.canonicalize())
        .map_err(|error| format!("cannot resolve current executable: {error}"))?;
    let app = current
        .ancestors()
        .find(|path| path.extension().is_some_and(|extension| extension == "app"))
        .ok_or_else(|| "running executable is not inside an app bundle".to_owned())?;
    if app != Path::new("/Applications/ANIMA.app") {
        return Err("running app is not the canonical replacement-only target".to_owned());
    }
    let receipt = command_output("/usr/sbin/pkgutil", &["--pkg-info", BUNDLE_ID])?;
    let package_version = receipt
        .lines()
        .find_map(|line| line.strip_prefix("version: "))
        .ok_or_else(|| "ANIMA package receipt has no version".to_owned())?;
    verify_embedded_package_version(package_version)?;
    let signing = command_combined(
        "/usr/bin/codesign",
        &["-d", "-r-", "--verbose=4", app.to_str().unwrap_or_default()],
    )?;
    if !signing.contains("Identifier=com.leoca.anima") || !signing.contains("designated =>") {
        return Err("ANIMA app has the wrong code-signing identity".to_owned());
    }
    command_output(
        "/usr/sbin/spctl",
        &[
            "--assess",
            "--type",
            "execute",
            "--verbose=4",
            app.to_str().unwrap_or_default(),
        ],
    )?;
    let matches = fs::read_dir("/Applications")
        .map_err(|error| format!("cannot inspect Applications: {error}"))?
        .filter_map(Result::ok)
        .filter(|entry| entry.file_name() == "ANIMA.app")
        .count();
    if matches != 1 {
        return Err("ANIMA has an ambiguous Applications registration".to_owned());
    }
    let volume = command_output(
        "/usr/sbin/diskutil",
        &["info", app.to_str().unwrap_or_default()],
    )?;
    let volume_uuid = volume
        .lines()
        .find_map(|line| {
            line.split_once("Volume UUID:")
                .map(|(_, value)| value.trim())
        })
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "ANIMA volume UUID is unavailable".to_owned())?;
    let (inode, generation) = mac_file_identity(&current)?;
    let designated = signing
        .lines()
        .find(|line| line.contains("designated =>"))
        .and_then(|line| line.split_once("designated =>").map(|(_, value)| value))
        .unwrap_or_default()
        .trim();
    Ok(InstalledIdentity {
        bundle_id: BUNDLE_ID.to_owned(),
        installer_family: "macos".to_owned(),
        package_version: package_version.to_owned(),
        canonical_target: current.to_string_lossy().into_owned(),
        native_file_identity: format!("{}:{}:{}", volume_uuid, inode, generation),
        platform_identity: format!("{}:{}", BUNDLE_ID, designated),
    })
}

#[cfg(target_os = "macos")]
fn mac_file_identity(path: &Path) -> Result<(u64, u32), String> {
    use std::{ffi::CString, os::unix::ffi::OsStrExt};
    let path = CString::new(path.as_os_str().as_bytes())
        .map_err(|_| "installed executable path contains NUL".to_owned())?;
    let mut stat = unsafe { std::mem::zeroed::<libc::stat>() };
    if unsafe { libc::stat(path.as_ptr(), &mut stat) } != 0 {
        return Err(format!(
            "cannot inspect installed executable vnode: {}",
            io::Error::last_os_error()
        ));
    }
    Ok((stat.st_ino, stat.st_gen))
}

#[cfg(windows)]
fn verify_installed_identity() -> Result<InstalledIdentity, String> {
    let current = std::env::current_exe()
        .and_then(|path| path.canonicalize())
        .map_err(|error| format!("cannot resolve current executable: {error}"))?;
    let script = r#"$r=@(Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | Where-Object {$_.DisplayName -eq 'ANIMA'}); if($r.Count -ne 1){exit 2}; $s=Get-AuthenticodeSignature -FilePath $env:ANIMA_CURRENT_EXE; if($s.Status -ne 'Valid'){exit 3}; $w=New-Object -ComObject WScript.Shell; $roots=@([Environment]::GetFolderPath('CommonStartMenu'),[Environment]::GetFolderPath('StartMenu')) | Where-Object {$_}; $links=@($roots | ForEach-Object {Get-ChildItem -LiteralPath $_ -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue} | Where-Object {$_.BaseName -eq 'ANIMA'} | ForEach-Object {$w.CreateShortcut($_.FullName)}); if($links.Count -ne 1){exit 4}; [pscustomobject]@{Version=$r[0].DisplayVersion;ProductCode=$r[0].PSChildName;CanonicalTarget=$links[0].TargetPath;Signer=$s.SignerCertificate.Thumbprint}|ConvertTo-Json -Compress"#;
    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .env("ANIMA_CURRENT_EXE", &current)
        .output()
        .map_err(|error| format!("cannot inspect MSI identity: {error}"))?;
    if !output.status.success() {
        return Err("MSI registration or Authenticode identity is invalid".to_owned());
    }
    #[derive(Deserialize)]
    #[serde(rename_all = "PascalCase")]
    struct MsiIdentity {
        version: String,
        product_code: String,
        canonical_target: String,
        signer: String,
    }
    let msi: MsiIdentity = serde_json::from_slice(&output.stdout)
        .map_err(|_| "MSI identity response is malformed".to_owned())?;
    verify_embedded_package_version(&msi.version)?;
    let related_products = windows_related_products("f0493d57-7a66-504b-ac04-60f1cb1cfb52")?;
    if related_products.len() != 1 || !related_products[0].eq_ignore_ascii_case(&msi.product_code) {
        return Err("MSI UpgradeCode does not resolve to exactly the running product".to_owned());
    }
    let registered_target = PathBuf::from(&msi.canonical_target)
        .canonicalize()
        .map_err(|_| "MSI registered launch target is missing".to_owned())?;
    if registered_target != current {
        return Err("running executable is not the exact MSI-registered launch target".to_owned());
    }
    let upgrade_code =
        option_env!("ANIMA_WINDOWS_UPGRADE_CODE").unwrap_or("f0493d57-7a66-504b-ac04-60f1cb1cfb52");
    Ok(InstalledIdentity {
        bundle_id: BUNDLE_ID.to_owned(),
        installer_family: "windows".to_owned(),
        package_version: msi.version,
        canonical_target: current.to_string_lossy().into_owned(),
        native_file_identity: windows_file_identity(&current)?,
        platform_identity: format!("{}:{}:{}", upgrade_code, msi.product_code, msi.signer),
    })
}

#[cfg(windows)]
fn windows_related_products(upgrade_code: &str) -> Result<Vec<String>, String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::{
        Foundation::ERROR_NO_MORE_ITEMS,
        System::ApplicationInstallationAndServicing::MsiEnumRelatedProductsW,
    };
    let canonical = canonical_msi_guid(upgrade_code)?;
    let upgrade_code: Vec<u16> = std::ffi::OsStr::new(&canonical)
        .encode_wide()
        .chain(Some(0))
        .collect();
    let mut products = Vec::new();
    for index in 0..=1 {
        let mut product = [0_u16; 39];
        let status = unsafe {
            MsiEnumRelatedProductsW(upgrade_code.as_ptr(), 0, index, product.as_mut_ptr())
        };
        if status == ERROR_NO_MORE_ITEMS {
            break;
        }
        if status != 0 {
            return Err("cannot resolve the MSI UpgradeCode".to_owned());
        }
        let length = product
            .iter()
            .position(|value| *value == 0)
            .ok_or_else(|| "MSI ProductCode is malformed".to_owned())?;
        products.push(
            String::from_utf16(&product[..length])
                .map_err(|_| "MSI ProductCode is malformed".to_owned())?,
        );
    }
    Ok(products)
}

#[doc(hidden)]
pub fn canonical_msi_guid(value: &str) -> Result<String, String> {
    let bare = value
        .strip_prefix('{')
        .and_then(|value| value.strip_suffix('}'))
        .unwrap_or(value);
    let valid = bare.len() == 36
        && bare.bytes().enumerate().all(|(index, byte)| {
            matches!(index, 8 | 13 | 18 | 23) && byte == b'-'
                || !matches!(index, 8 | 13 | 18 | 23) && byte.is_ascii_hexdigit()
        });
    if !valid {
        return Err("MSI GUID is malformed".to_owned());
    }
    Ok(format!("{{{}}}", bare.to_ascii_uppercase()))
}

#[cfg(windows)]
fn windows_file_identity(path: &Path) -> Result<String, String> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_SHARE_DELETE, FILE_SHARE_READ,
        FILE_SHARE_WRITE,
    };
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .open(path)
        .map_err(|error| format!("cannot open installed executable: {error}"))?;
    windows_file_identity_from_file(&file)
}

#[cfg(windows)]
fn windows_file_identity_from_file(file: &File) -> Result<String, String> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };
    let mut info = unsafe { std::mem::zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(file.as_raw_handle() as _, &mut info) } == 0 {
        return Err(format!(
            "cannot inspect installed executable file ID: {}",
            io::Error::last_os_error()
        ));
    }
    Ok(format!(
        "{}:{}",
        info.dwVolumeSerialNumber,
        (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow)
    ))
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn verify_installed_identity() -> Result<InstalledIdentity, String> {
    Err("draft cleanup is unsupported on this platform".to_owned())
}

struct LiveExecutableSnapshot {
    pid: u32,
    start_identity: u64,
    file_identity: String,
    _executable: File,
    #[cfg(target_os = "linux")]
    _pidfd: File,
}

fn verify_no_other_anima_hosts(
    identity: &InstalledIdentity,
    executable_classifications: &mut HashMap<String, bool>,
) -> Result<(), String> {
    let current_pid = std::process::id();
    let mut current_classifications = HashMap::new();
    for (pid, name) in enumerate_native_processes()? {
        if pid == current_pid {
            continue;
        }
        let snapshot = match inspect_live_executable(pid) {
            Ok(Some(snapshot)) => snapshot,
            Ok(None) => continue,
            Err(error) => {
                return Err(format!(
                    "cannot inspect current-user process {name} (PID {pid}): {error}"
                ))
            }
        };
        let is_anima = if let Some(value) =
            cached_process_classification(executable_classifications, &snapshot)
        {
            *value
        } else {
            executable_is_anima_host(&snapshot, identity)?
        };
        retain_process_classification(&mut current_classifications, &snapshot, is_anima);
        let Some(after) = inspect_live_executable(pid)? else {
            return Err("ANIMA process disappeared during census".to_owned());
        };
        if snapshot.start_identity != after.start_identity
            || snapshot.file_identity != after.file_identity
        {
            return Err("ANIMA process identity changed during census".to_owned());
        }
        if !is_anima {
            continue;
        }
        return Err("another ANIMA host process is running".to_owned());
    }
    *executable_classifications = current_classifications;
    Ok(())
}

#[cfg(target_os = "linux")]
fn process_classification_key(snapshot: &LiveExecutableSnapshot) -> String {
    format!(
        "{}:{}:{}",
        snapshot.pid, snapshot.start_identity, snapshot.file_identity
    )
}

#[cfg(target_os = "linux")]
fn cached_process_classification<'a>(
    cache: &'a HashMap<String, bool>,
    snapshot: &LiveExecutableSnapshot,
) -> Option<&'a bool> {
    cache.get(&process_classification_key(snapshot))
}

#[cfg(not(target_os = "linux"))]
fn cached_process_classification<'a>(
    _cache: &'a HashMap<String, bool>,
    _snapshot: &LiveExecutableSnapshot,
) -> Option<&'a bool> {
    None
}

#[cfg(target_os = "linux")]
fn retain_process_classification(
    cache: &mut HashMap<String, bool>,
    snapshot: &LiveExecutableSnapshot,
    value: bool,
) {
    cache.insert(process_classification_key(snapshot), value);
}

#[cfg(not(target_os = "linux"))]
fn retain_process_classification(
    _cache: &mut HashMap<String, bool>,
    _snapshot: &LiveExecutableSnapshot,
    _value: bool,
) {
}

#[cfg(target_os = "linux")]
fn enumerate_native_processes() -> Result<Vec<(u32, String)>, String> {
    let current_uid = unsafe { libc::geteuid() };
    let mut processes = Vec::new();
    for entry in
        fs::read_dir("/proc").map_err(|error| format!("cannot enumerate /proc: {error}"))?
    {
        let entry = entry.map_err(|error| format!("cannot enumerate /proc entry: {error}"))?;
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|value| value.parse::<u32>().ok())
        else {
            continue;
        };
        let status = match fs::read_to_string(entry.path().join("status")) {
            Ok(status) => status,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("cannot identify live PID {pid}: {error}")),
        };
        let effective_uid = status
            .lines()
            .find_map(|line| line.strip_prefix("Uid:"))
            .and_then(|line| line.split_whitespace().nth(1))
            .and_then(|value| value.parse::<u32>().ok())
            .ok_or_else(|| format!("PID {pid} has malformed ownership metadata"))?;
        if effective_uid != current_uid {
            continue;
        }
        let comm = entry.path().join("comm");
        let name = match fs::read_to_string(comm) {
            Ok(name) => name.trim().to_owned(),
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("cannot identify live PID {pid}: {error}")),
        };
        processes.push((pid, name));
    }
    Ok(processes)
}

#[cfg(target_os = "macos")]
fn enumerate_native_processes() -> Result<Vec<(u32, String)>, String> {
    const PROC_ALL_PIDS: u32 = 1;
    let bytes = unsafe { libc::proc_listpids(PROC_ALL_PIDS, 0, std::ptr::null_mut(), 0) };
    if bytes <= 0 {
        return Err("cannot size the macOS process census".to_owned());
    }
    let mut pids = vec![0_i32; bytes as usize / std::mem::size_of::<i32>() + 256];
    let filled = unsafe {
        libc::proc_listpids(
            PROC_ALL_PIDS,
            0,
            pids.as_mut_ptr().cast(),
            i32::try_from(pids.len() * std::mem::size_of::<i32>())
                .map_err(|_| "macOS process census is too large")?,
        )
    };
    if filled < 0 {
        return Err("cannot enumerate macOS processes".to_owned());
    }
    pids.truncate(filled as usize / std::mem::size_of::<i32>());
    let mut processes = Vec::new();
    for pid in pids.into_iter().filter(|pid| *pid > 0) {
        let mut info = unsafe { std::mem::zeroed::<libc::proc_bsdinfo>() };
        let size = std::mem::size_of::<libc::proc_bsdinfo>();
        let written = unsafe {
            libc::proc_pidinfo(
                pid,
                libc::PROC_PIDTBSDINFO,
                0,
                std::ptr::addr_of_mut!(info).cast(),
                size as libc::c_int,
            )
        };
        if written == 0 {
            let result = unsafe { libc::kill(pid, 0) };
            if result != 0 && io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
                continue;
            }
            return Err(format!("cannot inspect process ownership for PID {pid}"));
        }
        if written as usize != size || info.pbi_pid != pid as u32 {
            return Err(format!("PID {pid} returned incomplete ownership metadata"));
        }
        if info.pbi_uid != unsafe { libc::geteuid() } {
            continue;
        }
        let mut name = vec![0_u8; 1024];
        let length = unsafe { libc::proc_name(pid, name.as_mut_ptr().cast(), name.len() as u32) };
        if length <= 0 {
            return Err(format!("cannot identify current-user PID {pid}"));
        }
        name.truncate(length as usize);
        processes.push((pid as u32, String::from_utf8_lossy(&name).into_owned()));
    }
    Ok(processes)
}

#[cfg(windows)]
fn enumerate_native_processes() -> Result<Vec<(u32, String)>, String> {
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        Security::{EqualSid, GetTokenInformation, TokenUser, TOKEN_QUERY, TOKEN_USER},
        System::{
            RemoteDesktop::{WTSEnumerateProcessesW, WTSFreeMemory, WTS_PROCESS_INFOW},
            Threading::{GetCurrentProcess, OpenProcessToken},
        },
    };
    let mut token = std::ptr::null_mut();
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
        return Err("cannot identify the current Windows user".to_owned());
    }
    let result = (|| {
        let mut required = 0_u32;
        unsafe {
            GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut required);
        }
        if required < std::mem::size_of::<TOKEN_USER>() as u32 {
            return Err("current Windows user identity is unavailable".to_owned());
        }
        let mut user = vec![0_u8; required as usize];
        if unsafe {
            GetTokenInformation(
                token,
                TokenUser,
                user.as_mut_ptr().cast(),
                required,
                &mut required,
            )
        } == 0
        {
            return Err("cannot read the current Windows user identity".to_owned());
        }
        let user_sid = unsafe { (*(user.as_ptr().cast::<TOKEN_USER>())).User.Sid };
        let mut entries = std::ptr::null_mut::<WTS_PROCESS_INFOW>();
        let mut count = 0_u32;
        if unsafe { WTSEnumerateProcessesW(std::ptr::null_mut(), 0, 1, &mut entries, &mut count) }
            == 0
        {
            return Err("cannot enumerate current-user Windows processes".to_owned());
        }
        struct WtsMemory(*mut WTS_PROCESS_INFOW);
        impl Drop for WtsMemory {
            fn drop(&mut self) {
                unsafe { WTSFreeMemory(self.0.cast()) };
            }
        }
        let _entries = WtsMemory(entries);
        let mut processes = Vec::new();
        for entry in unsafe { std::slice::from_raw_parts(entries, count as usize) } {
            if entry.pUserSid.is_null() || unsafe { EqualSid(user_sid, entry.pUserSid) } == 0 {
                continue;
            }
            let mut length = 0;
            if !entry.pProcessName.is_null() {
                while unsafe { *entry.pProcessName.add(length) } != 0 {
                    length += 1;
                }
            }
            let name = if length == 0 {
                "<unnamed>".to_owned()
            } else {
                String::from_utf16_lossy(unsafe {
                    std::slice::from_raw_parts(entry.pProcessName, length)
                })
            };
            processes.push((entry.ProcessId, name));
        }
        Ok(processes)
    })();
    unsafe { CloseHandle(token) };
    result
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn enumerate_native_processes() -> Result<Vec<(u32, String)>, String> {
    Err("native process enumeration is unsupported on this platform".to_owned())
}

#[doc(hidden)]
pub fn verify_process_census_for_test() -> Result<(), String> {
    let current = std::env::current_exe().map_err(|error| error.to_string())?;
    let current_hash = hex::encode(Sha256::digest(
        fs::read(&current).map_err(|error| error.to_string())?,
    ));
    verify_no_other_anima_hosts(
        &InstalledIdentity {
            bundle_id: BUNDLE_ID.to_owned(),
            installer_family: "test".to_owned(),
            package_version: "test".to_owned(),
            canonical_target: current.to_string_lossy().into_owned(),
            native_file_identity: "test".to_owned(),
            platform_identity: format!("anima:test:{current_hash}"),
        },
        &mut HashMap::new(),
    )
}

#[cfg(target_os = "linux")]
fn inspect_live_executable(pid: u32) -> Result<Option<LiveExecutableSnapshot>, String> {
    use std::os::{fd::FromRawFd, unix::fs::MetadataExt};

    let pidfd =
        unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::pid_t, 0) as libc::c_int };
    if pidfd < 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(None);
        }
        return Err(format!("cannot open pidfd for PID {pid}: {error}"));
    }
    let pidfd = unsafe { File::from_raw_fd(pidfd) };
    let start_identity = match inspect_process_start_time_with_pidfd(pid, &pidfd)? {
        Some(value) => value,
        None => return Ok(None),
    };
    let proc_executable = PathBuf::from(format!("/proc/{pid}/exe"));
    let executable = match File::open(&proc_executable) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(format!(
                "cannot open live executable for PID {pid}: {error}"
            ))
        }
    };
    let metadata = executable
        .metadata()
        .map_err(|error| format!("cannot inspect live executable for PID {pid}: {error}"))?;
    let path = fs::read_link(&proc_executable)
        .map_err(|error| format!("cannot resolve live executable for PID {pid}: {error}"))?;
    let start_after = inspect_process_start_time_with_pidfd(pid, &pidfd)?
        .ok_or_else(|| format!("PID {pid} disappeared during executable inspection"))?;
    if start_identity != start_after {
        return Err(format!("PID {pid} was reused during executable inspection"));
    }
    Ok(Some(LiveExecutableSnapshot {
        pid,
        start_identity,
        file_identity: format!("{}:{}", metadata.dev(), metadata.ino()),
        _executable: executable,
        _pidfd: pidfd,
    }))
}

#[cfg(target_os = "macos")]
fn inspect_live_executable(pid: u32) -> Result<Option<LiveExecutableSnapshot>, String> {
    use std::os::{fd::AsRawFd, unix::ffi::OsStringExt};

    let start_identity = match inspect_process_start_time(pid)? {
        Some(value) => value,
        None => return Ok(None),
    };
    let mut path = vec![0_u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
    let length = unsafe {
        libc::proc_pidpath(
            pid as libc::c_int,
            path.as_mut_ptr().cast(),
            path.len() as u32,
        )
    };
    if length <= 0 {
        if inspect_process_start_time(pid)?.is_none() {
            return Ok(None);
        }
        return Err(format!("cannot resolve live executable for PID {pid}"));
    }
    path.truncate(length as usize);
    let path = PathBuf::from(std::ffi::OsString::from_vec(path));
    let executable = File::open(&path)
        .map_err(|error| format!("cannot open live executable for PID {pid}: {error}"))?;
    let mut stat = unsafe { std::mem::zeroed::<libc::stat>() };
    if unsafe { libc::fstat(executable.as_raw_fd(), &mut stat) } != 0 {
        return Err(format!(
            "cannot inspect live executable for PID {pid}: {}",
            io::Error::last_os_error()
        ));
    }
    let start_after = inspect_process_start_time(pid)?
        .ok_or_else(|| format!("PID {pid} disappeared during executable inspection"))?;
    if start_identity != start_after {
        return Err(format!("PID {pid} was reused during executable inspection"));
    }
    Ok(Some(LiveExecutableSnapshot {
        pid,
        start_identity,
        file_identity: format!("{}:{}:{}", stat.st_dev, stat.st_ino, stat.st_gen),
        _executable: executable,
    }))
}

#[cfg(windows)]
fn inspect_live_executable(pid: u32) -> Result<Option<LiveExecutableSnapshot>, String> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_sys::Win32::{
        Foundation::{CloseHandle, ERROR_INVALID_PARAMETER},
        Storage::FileSystem::{FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE},
        System::Threading::{
            OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
    };
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(ERROR_INVALID_PARAMETER as i32) {
            return Ok(None);
        }
        return Err(format!("cannot open process PID {pid}: {error}"));
    }
    let result = (|| {
        let start_identity = windows_process_start_time_from_handle(pid, handle)?
            .ok_or_else(|| format!("PID {pid} is not live"))?;
        let mut path = vec![0_u16; 32_768];
        let mut length = path.len() as u32;
        if unsafe { QueryFullProcessImageNameW(handle, 0, path.as_mut_ptr(), &mut length) } == 0 {
            return Err(format!(
                "cannot resolve live executable for PID {pid}: {}",
                io::Error::last_os_error()
            ));
        }
        path.truncate(length as usize);
        let path = PathBuf::from(
            String::from_utf16(&path)
                .map_err(|_| format!("PID {pid} executable path is malformed"))?,
        );
        let executable = OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .open(&path)
            .map_err(|error| format!("cannot open live executable for PID {pid}: {error}"))?;
        let file_identity = windows_file_identity_from_file(&executable)?;
        let start_after = windows_process_start_time_from_handle(pid, handle)?
            .ok_or_else(|| format!("PID {pid} disappeared during executable inspection"))?;
        if start_identity != start_after {
            return Err(format!("PID {pid} was reused during executable inspection"));
        }
        Ok(Some(LiveExecutableSnapshot {
            pid,
            start_identity,
            file_identity,
            _executable: executable,
        }))
    })();
    unsafe { CloseHandle(handle) };
    result
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn inspect_live_executable(_pid: u32) -> Result<Option<LiveExecutableSnapshot>, String> {
    Err("live executable inspection is unsupported on this platform".to_owned())
}

#[cfg(target_os = "linux")]
fn executable_is_anima_host(
    snapshot: &LiveExecutableSnapshot,
    _identity: &InstalledIdentity,
) -> Result<bool, String> {
    file_has_trusted_host_identity(&snapshot._executable)
}

#[cfg(target_os = "linux")]
fn file_has_trusted_host_identity(file: &File) -> Result<bool, String> {
    use std::io::{Read, Seek, SeekFrom};

    let mut reader = file
        .try_clone()
        .map_err(|error| format!("cannot clone executable handle: {error}"))?;
    reader
        .seek(SeekFrom::Start(0))
        .map_err(|error| format!("cannot seek executable handle: {error}"))?;
    let mut buffer = vec![0_u8; 64 * 1024 + HOST_IDENTITY_MARKER_LEN.saturating_sub(1)];
    let mut retained = 0_usize;
    loop {
        let read = reader
            .read(&mut buffer[retained..])
            .map_err(|error| format!("cannot read executable handle: {error}"))?;
        let total = retained + read;
        if buffer[..total]
            .windows(HOST_IDENTITY_MARKER_LEN)
            .any(host_identity_marker_is_trusted)
        {
            return Ok(true);
        }
        if read == 0 {
            return Ok(false);
        }
        retained = HOST_IDENTITY_MARKER_LEN.saturating_sub(1).min(total);
        let start = total - retained;
        buffer.copy_within(start..start + retained, 0);
    }
}

#[cfg(target_os = "macos")]
fn executable_is_anima_host(
    snapshot: &LiveExecutableSnapshot,
    identity: &InstalledIdentity,
) -> Result<bool, String> {
    if identity.installer_family == "test" {
        let expected = identity
            .platform_identity
            .rsplit(':')
            .next()
            .unwrap_or_default();
        return Ok(sha256_file_handle(&snapshot._executable)? == expected);
    }
    use security_framework::os::macos::code_signing::{
        Flags, GuestAttributes, SecCode, SecRequirement,
    };
    use std::str::FromStr;

    let designated = identity
        .platform_identity
        .strip_prefix(&format!("{BUNDLE_ID}:"))
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "macOS designated requirement is unavailable".to_owned())?;
    let requirement = SecRequirement::from_str(designated)
        .map_err(|error| format!("macOS designated requirement is invalid: {error}"))?;
    let mut attributes = GuestAttributes::new();
    attributes.set_pid(snapshot.pid as libc::pid_t);
    let code = SecCode::copy_guest_with_attribues(None, &attributes, Flags::NONE)
        .map_err(|error| format!("cannot bind macOS PID to its loaded signed code: {error}"))?;
    match code.check_validity(Flags::BASIC_VALIDATE_ONLY, &requirement) {
        Ok(()) => Ok(true),
        // errSecCSReqFailed is the only unambiguous "signed code does not
        // satisfy ANIMA's designated requirement" result. Every other
        // Security.framework failure is denied or ambiguous census metadata.
        Err(error) if error.code() == -67050 => Ok(false),
        Err(error) => Err(format!(
            "cannot validate macOS PID {} loaded signed code: {error}",
            snapshot.pid
        )),
    }
}

#[cfg(windows)]
fn executable_is_anima_host(
    snapshot: &LiveExecutableSnapshot,
    identity: &InstalledIdentity,
) -> Result<bool, String> {
    if identity.installer_family == "test" {
        let expected = identity
            .platform_identity
            .rsplit(':')
            .next()
            .unwrap_or_default();
        return Ok(sha256_file_handle(&snapshot._executable)? == expected);
    }
    process_memory_has_trusted_host_identity(snapshot.pid)
}

#[cfg(windows)]
fn process_memory_has_trusted_host_identity(pid: u32) -> Result<bool, String> {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, HMODULE},
        System::{
            Memory::{
                VirtualQueryEx, MEMORY_BASIC_INFORMATION, MEM_COMMIT, PAGE_GUARD, PAGE_NOACCESS,
            },
            ProcessStatus::{K32EnumProcessModules, K32GetModuleInformation, MODULEINFO},
            Threading::{
                OpenProcess, PROCESS_QUERY_INFORMATION, PROCESS_QUERY_LIMITED_INFORMATION,
                PROCESS_VM_READ,
            },
        },
    };

    let handle = unsafe {
        OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
            0,
            pid,
        )
    };
    if handle.is_null() {
        return Err(format!(
            "cannot open PID {pid} loaded image: {}",
            io::Error::last_os_error()
        ));
    }
    let result = (|| {
        let mut module: HMODULE = std::ptr::null_mut();
        let mut required = 0_u32;
        if unsafe {
            K32EnumProcessModules(
                handle,
                &mut module,
                std::mem::size_of::<HMODULE>() as u32,
                &mut required,
            )
        } == 0
            || module.is_null()
            || required < std::mem::size_of::<HMODULE>() as u32
        {
            return Err(format!("cannot bind PID {pid} to its loaded main module"));
        }
        let mut module_info = unsafe { std::mem::zeroed::<MODULEINFO>() };
        if unsafe {
            K32GetModuleInformation(
                handle,
                module,
                &mut module_info,
                std::mem::size_of::<MODULEINFO>() as u32,
            )
        } == 0
        {
            return Err(format!("cannot inspect PID {pid} loaded main module"));
        }
        let module_start = module_info.lpBaseOfDll as usize;
        let module_end = module_start
            .checked_add(module_info.SizeOfImage as usize)
            .ok_or_else(|| format!("PID {pid} loaded image range overflow"))?;
        let mut cursor = module_start;
        while cursor < module_end {
            let mut region = unsafe { std::mem::zeroed::<MEMORY_BASIC_INFORMATION>() };
            if unsafe {
                VirtualQueryEx(
                    handle,
                    cursor as *const _,
                    &mut region,
                    std::mem::size_of::<MEMORY_BASIC_INFORMATION>(),
                )
            } == 0
            {
                return Err(format!("cannot inspect PID {pid} loaded image pages"));
            }
            let region_start = cursor.max(region.BaseAddress as usize);
            let region_end = module_end.min(
                (region.BaseAddress as usize)
                    .checked_add(region.RegionSize)
                    .ok_or_else(|| format!("PID {pid} memory region overflow"))?,
            );
            if region_end <= cursor {
                return Err(format!("PID {pid} returned a non-advancing memory region"));
            }
            if region.State == MEM_COMMIT
                && region.Protect & (PAGE_GUARD | PAGE_NOACCESS) == 0
                && process_region_has_trusted_host_identity(handle, pid, region_start, region_end)?
            {
                return Ok(true);
            }
            cursor = region_end;
        }
        Ok(false)
    })();
    unsafe { CloseHandle(handle) };
    result
}

#[cfg(windows)]
fn process_region_has_trusted_host_identity(
    handle: windows_sys::Win32::Foundation::HANDLE,
    pid: u32,
    start: usize,
    end: usize,
) -> Result<bool, String> {
    use windows_sys::Win32::System::Diagnostics::Debug::ReadProcessMemory;

    let mut cursor = start;
    let mut retained = Vec::<u8>::new();
    while cursor < end {
        let requested = (end - cursor).min(64 * 1024);
        let prefix = retained.len();
        let mut bytes = vec![0_u8; prefix + requested];
        bytes[..prefix].copy_from_slice(&retained);
        let mut read = 0_usize;
        if unsafe {
            ReadProcessMemory(
                handle,
                cursor as *const _,
                bytes[prefix..].as_mut_ptr().cast(),
                requested,
                &mut read,
            )
        } == 0
            || read == 0
        {
            return Err(format!("cannot read PID {pid} loaded image pages"));
        }
        bytes.truncate(prefix + read);
        if bytes
            .windows(HOST_IDENTITY_MARKER_LEN)
            .any(host_identity_marker_is_trusted)
        {
            return Ok(true);
        }
        let keep = HOST_IDENTITY_MARKER_LEN.saturating_sub(1).min(bytes.len());
        retained.clear();
        retained.extend_from_slice(&bytes[bytes.len() - keep..]);
        cursor += read;
    }
    Ok(false)
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn executable_is_anima_host(
    _snapshot: &LiveExecutableSnapshot,
    _identity: &InstalledIdentity,
) -> Result<bool, String> {
    Err("process classification is unsupported on this platform".to_owned())
}

#[cfg(target_os = "linux")]
fn boot_identity() -> Result<String, String> {
    let value = fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map_err(|error| format!("cannot read Linux boot identity: {error}"))?;
    let value = value.trim();
    if value.is_empty() {
        return Err("Linux boot identity is empty".to_owned());
    }
    Ok(value.to_owned())
}

#[cfg(target_os = "macos")]
fn boot_identity() -> Result<String, String> {
    let value = command_output("/usr/sbin/sysctl", &["-n", "kern.bootsessionuuid"])?;
    let value = value.trim();
    if value.is_empty() {
        return Err("macOS boot-session UUID is empty".to_owned());
    }
    Ok(value.to_owned())
}

#[cfg(windows)]
fn boot_identity() -> Result<String, String> {
    use windows_sys::core::GUID;
    #[repr(C)]
    struct SystemBootEnvironmentInformation {
        boot_identifier: GUID,
        firmware_type: u32,
        boot_flags: u64,
    }
    #[link(name = "ntdll")]
    extern "system" {
        fn NtQuerySystemInformation(
            class: u32,
            info: *mut std::ffi::c_void,
            length: u32,
            returned: *mut u32,
        ) -> i32;
    }
    let mut info = unsafe { std::mem::zeroed::<SystemBootEnvironmentInformation>() };
    let status = unsafe {
        NtQuerySystemInformation(
            90,
            (&mut info as *mut SystemBootEnvironmentInformation).cast(),
            std::mem::size_of::<SystemBootEnvironmentInformation>() as u32,
            std::ptr::null_mut(),
        )
    };
    if status < 0 {
        return Err("Windows BootIdentifier is unavailable".to_owned());
    }
    let guid = info.boot_identifier;
    Ok(format!(
        "{:08x}-{:04x}-{:04x}-{}",
        guid.data1,
        guid.data2,
        guid.data3,
        hex::encode(guid.data4)
    ))
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn boot_identity() -> Result<String, String> {
    Err("OS boot identity is unsupported on this platform".to_owned())
}

#[cfg(windows)]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, String> {
    use windows_sys::Win32::Foundation::{
        CloseHandle, ERROR_INVALID_PARAMETER, FILETIME, STILL_ACTIVE,
    };
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(ERROR_INVALID_PARAMETER as i32) {
            return Ok(None);
        }
        return Err(format!("cannot inspect PID {pid}: {error}"));
    }
    let result = windows_process_start_time_from_handle(pid, handle);
    unsafe { CloseHandle(handle) };
    result
}

#[cfg(windows)]
fn windows_process_start_time_from_handle(
    pid: u32,
    handle: windows_sys::Win32::Foundation::HANDLE,
) -> Result<Option<u64>, String> {
    use windows_sys::Win32::{
        Foundation::{FILETIME, STILL_ACTIVE},
        System::Threading::{GetExitCodeProcess, GetProcessTimes},
    };
    let mut exit = 0;
    if unsafe { GetExitCodeProcess(handle, &mut exit) } == 0 {
        return Err(format!("cannot inspect PID {pid} exit state"));
    }
    if exit != STILL_ACTIVE as u32 {
        return Ok(None);
    }
    let mut created = FILETIME {
        dwLowDateTime: 0,
        dwHighDateTime: 0,
    };
    let mut exited = created;
    let mut kernel = created;
    let mut user = created;
    if unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) } == 0 {
        return Err(format!("cannot inspect PID {pid} creation time"));
    }
    Ok(Some(
        (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime),
    ))
}

#[cfg(target_os = "linux")]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, String> {
    use std::os::fd::FromRawFd;
    let pidfd =
        unsafe { libc::syscall(libc::SYS_pidfd_open, pid as libc::pid_t, 0) as libc::c_int };
    if pidfd < 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(None);
        }
        return Err(format!("cannot open pidfd for PID {pid}: {error}"));
    }
    let pidfd = unsafe { File::from_raw_fd(pidfd) };
    inspect_process_start_time_with_pidfd(pid, &pidfd)
}

#[cfg(target_os = "linux")]
fn inspect_process_start_time_with_pidfd(pid: u32, _pidfd: &File) -> Result<Option<u64>, String> {
    let stat = match fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(value) => value,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("cannot inspect PID {pid}: {error}")),
    };
    parse_linux_process_stat(pid, &stat)
}

#[cfg(target_os = "linux")]
fn parse_linux_process_stat(pid: u32, stat: &str) -> Result<Option<u64>, String> {
    let command_end = stat
        .rfind(')')
        .ok_or_else(|| format!("PID {pid} has malformed stat data"))?;
    let mut fields = stat[command_end + 1..].split_whitespace();
    let state = fields
        .next()
        .ok_or_else(|| format!("PID {pid} has truncated stat data"))?;
    if matches!(state, "Z" | "X" | "x") {
        return Ok(None);
    }
    let start = fields
        .nth(18)
        .ok_or_else(|| format!("PID {pid} has truncated stat data"))?;
    start
        .parse()
        .map(Some)
        .map_err(|_| format!("PID {pid} has invalid start identity"))
}

#[cfg(target_os = "macos")]
fn inspect_process_start_time(pid: u32) -> Result<Option<u64>, String> {
    let mut info = unsafe { std::mem::zeroed::<libc::proc_bsdinfo>() };
    let size = std::mem::size_of::<libc::proc_bsdinfo>();
    let written = unsafe {
        libc::proc_pidinfo(
            pid as libc::c_int,
            libc::PROC_PIDTBSDINFO,
            0,
            std::ptr::addr_of_mut!(info).cast(),
            size as libc::c_int,
        )
    };
    if written == 0 {
        let result = unsafe { libc::kill(pid as libc::pid_t, 0) };
        if result != 0 && io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            return Ok(None);
        }
        return Err(format!("cannot inspect PID {pid}"));
    }
    if written as usize != size || info.pbi_pid != pid {
        return Err(format!("PID {pid} returned incomplete identity"));
    }
    info.pbi_start_tvsec
        .checked_mul(1_000_000)
        .and_then(|value| value.checked_add(info.pbi_start_tvusec))
        .map(Some)
        .ok_or_else(|| "process start identity overflow".to_owned())
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
fn inspect_process_start_time(_pid: u32) -> Result<Option<u64>, String> {
    Err("process start identity is unsupported on this platform".to_owned())
}

#[doc(hidden)]
pub fn process_start_identity_for_test(pid: u32) -> Result<Option<u64>, String> {
    inspect_process_start_time(pid)
}

#[doc(hidden)]
pub fn boot_identity_for_test() -> Result<String, String> {
    boot_identity()
}

#[cfg(test)]
fn tempfile_file_for_test() -> File {
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT_FILE: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "anima-draft-cleanup-test-{}-{}",
        std::process::id(),
        NEXT_FILE.fetch_add(1, Ordering::Relaxed),
    ));
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(&path)
        .unwrap();
    let _ = fs::remove_file(path);
    file
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signed_host_identity_marker_is_key_bound_and_tamper_evident() {
        use ring::{
            rand::SystemRandom,
            signature::{Ed25519KeyPair, KeyPair},
        };

        let rng = SystemRandom::new();
        let pkcs8 = Ed25519KeyPair::generate_pkcs8(&rng).unwrap();
        let pair = Ed25519KeyPair::from_pkcs8(pkcs8.as_ref()).unwrap();
        let public_key = hex::encode(pair.public_key().as_ref());
        let signature = hex::encode(pair.sign(HOST_IDENTITY_MESSAGE).as_ref());
        let marker = format!("anima-host-identity-v1:{public_key}:{signature}");
        assert!(host_identity_marker_is_trusted_by(
            marker.as_bytes(),
            &[&public_key]
        ));
        let wrong_key = "00".repeat(32);
        assert!(!host_identity_marker_is_trusted_by(
            marker.as_bytes(),
            &[&wrong_key]
        ));
        let mut tampered = marker.into_bytes();
        *tampered.last_mut().unwrap() = if tampered.last() == Some(&b'0') {
            b'1'
        } else {
            b'0'
        };
        assert!(!host_identity_marker_is_trusted_by(
            &tampered,
            &[&public_key]
        ));
    }

    #[test]
    fn installed_identity_digest_is_domain_separated_length_delimited_and_nfc() {
        let composed = installed_identity_digest(&["com.leoca.anima", "caf\u{e9}"]);
        let decomposed = installed_identity_digest(&["com.leoca.anima", "cafe\u{301}"]);
        assert_eq!(composed, decomposed);
        assert_ne!(
            installed_identity_digest(&["ab", "c"]),
            installed_identity_digest(&["a", "bc"])
        );
    }

    #[test]
    fn msi_guids_are_canonicalized_for_windows_installer_apis() {
        assert_eq!(
            canonical_msi_guid("f0493d57-7a66-504b-ac04-60f1cb1cfb52").unwrap(),
            "{F0493D57-7A66-504B-AC04-60F1CB1CFB52}",
        );
        assert!(canonical_msi_guid("not-a-guid").is_err());
    }

    #[test]
    fn malformed_or_uppercase_digests_fail_closed() {
        assert!(decode_digest(&"a".repeat(63)).is_err());
        assert!(decode_digest(&"A".repeat(64)).is_err());
        assert!(decode_digest(&"g".repeat(64)).is_err());
    }

    #[test]
    fn epoch_requires_same_install_identity_and_a_different_boot() {
        let digest = [9; 32];
        let epoch = CleanupEpoch {
            protocol_version: PROTOCOL_VERSION,
            installed_identity_digest: hex::encode(digest),
            process_start_identity: 100,
            boot_identity: "boot-a".to_owned(),
        };
        assert!(!epoch_is_eligible(None, digest, "boot-b"));
        assert!(!epoch_is_eligible(Some(&epoch), digest, "boot-a"));
        assert!(!epoch_is_eligible(Some(&epoch), [8; 32], "boot-b"));
        assert!(epoch_is_eligible(Some(&epoch), digest, "boot-b"));
    }

    #[test]
    fn expired_capability_is_removed_before_replay() {
        let authority = DraftCleanupAuthority::enabled_for_test([7; 32], "boot-b");
        let audience = "a".repeat(64);
        let issued = authority
            .issue_with_verifier(&audience, |_, _, _, _| Ok(()))
            .unwrap();
        let key = decode_digest(&issued.capability).unwrap();
        let mut state = authority.state.lock().unwrap();
        let AuthorityState::Enabled(enabled) = &mut *state else {
            panic!("test authority disabled")
        };
        enabled.capabilities.get_mut(&key).unwrap().expires_at = Instant::now();
        drop(state);
        assert!(authority
            .consume_with_verifier(&issued.capability, &audience, |_, _, _, _| Ok(()))
            .is_err());
        assert!(authority
            .consume_with_verifier(&issued.capability, &audience, |_, _, _, _| Ok(()))
            .is_err());
    }

    #[test]
    fn capability_expiring_during_verification_is_removed_before_replay() {
        let authority = DraftCleanupAuthority::enabled_for_test([7; 32], "boot-b");
        let audience = "a".repeat(64);
        let issued = authority
            .issue_with_verifier(&audience, |_, _, _, _| Ok(()))
            .unwrap();
        let key = decode_digest(&issued.capability).unwrap();
        let mut state = authority.state.lock().unwrap();
        let AuthorityState::Enabled(enabled) = &mut *state else {
            panic!("test authority disabled")
        };
        enabled.capabilities.get_mut(&key).unwrap().expires_at =
            Instant::now() + Duration::from_millis(1);
        drop(state);
        assert!(authority
            .consume_with_verifier(&issued.capability, &audience, |_, _, _, _| {
                std::thread::sleep(Duration::from_millis(5));
                Ok(())
            })
            .is_err());
        assert!(authority
            .consume_with_verifier(&issued.capability, &audience, |_, _, _, _| Ok(()))
            .is_err());
    }

    #[test]
    fn capability_is_one_shot_and_audience_bound() {
        let authority = DraftCleanupAuthority::enabled_for_test([7; 32], "boot-b");
        let audience = "a".repeat(64);
        let issued = authority
            .issue_with_verifier(&audience, |_, _, _, _| Ok(()))
            .unwrap();
        assert!(authority
            .consume_with_verifier(&issued.capability, &audience, |_, _, _, _| Ok(()))
            .unwrap());
        assert!(authority
            .consume_with_verifier(&issued.capability, &audience, |_, _, _, _| Ok(()))
            .is_err());
    }

    #[test]
    fn wrong_audience_consumes_the_capability() {
        let authority = DraftCleanupAuthority::enabled_for_test([7; 32], "boot-b");
        let issued = authority
            .issue_with_verifier(&"a".repeat(64), |_, _, _, _| Ok(()))
            .unwrap();
        assert!(authority
            .consume_with_verifier(&issued.capability, &"b".repeat(64), |_, _, _, _| Ok(()))
            .is_err());
        assert!(authority
            .consume_with_verifier(&issued.capability, &"a".repeat(64), |_, _, _, _| Ok(()))
            .is_err());
    }

    #[test]
    fn failed_runtime_recheck_does_not_grant_success() {
        let authority = DraftCleanupAuthority::enabled_for_test([7; 32], "boot-b");
        let audience = "a".repeat(64);
        let issued = authority
            .issue_with_verifier(&audience, |_, _, _, _| Ok(()))
            .unwrap();
        assert!(authority
            .consume_with_verifier(&issued.capability, &audience, |_, _, _, _| Err(
                "contender".to_owned()
            ))
            .is_err());
    }

    #[test]
    fn concurrent_double_consume_has_exactly_one_success() {
        use std::sync::{Arc, Barrier};
        let authority = Arc::new(DraftCleanupAuthority::enabled_for_test([7; 32], "boot-b"));
        let audience = "a".repeat(64);
        let issued = authority
            .issue_with_verifier(&audience, |_, _, _, _| Ok(()))
            .unwrap();
        let barrier = Arc::new(Barrier::new(3));
        let handles: Vec<_> = (0..2)
            .map(|_| {
                let authority = Arc::clone(&authority);
                let barrier = Arc::clone(&barrier);
                let capability = issued.capability.clone();
                let audience = audience.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    authority
                        .consume_with_verifier(&capability, &audience, |_, _, _, _| Ok(()))
                        .is_ok()
                })
            })
            .collect();
        barrier.wait();
        let successes: usize = handles
            .into_iter()
            .map(|handle| usize::from(handle.join().unwrap()))
            .sum();
        assert_eq!(successes, 1);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn linux_process_parser_reads_current_precise_start_identity() {
        assert!(
            inspect_process_start_time(std::process::id())
                .unwrap()
                .unwrap()
                > 0
        );
    }
}
