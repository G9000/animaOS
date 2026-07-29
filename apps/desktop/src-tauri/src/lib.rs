use serde::Deserialize;
use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Stdio,
    sync::Mutex,
};
use std::sync::Arc;
use sysinfo::{get_current_pid, Components, Networks, ProcessesToUpdate, System};
use tauri::{
    menu::{Menu, MenuItem},
    path::BaseDirectory,
    tray::TrayIconBuilder,
    Manager, RunEvent, WindowEvent,
};

// ── System monitor ────────────────────────────────────────────────────────────

#[derive(serde::Serialize, Clone, Default)]
struct GpuInfo {
    name: Option<String>,
    usage: Option<f32>,
    temp_c: Option<f32>,
    vram_used_mb: Option<u64>,
    vram_total_mb: Option<u64>,
}

struct AppMonitor {
    system: Mutex<System>,
    gpu_cache: Arc<Mutex<GpuInfo>>,
    networks: Mutex<Networks>,
    net_prev: Mutex<Option<(u64, u64, std::time::Instant)>>,
}

#[derive(serde::Serialize)]
struct SystemStats {
    cpu_usage: f32,
    cpu_temp_c: Option<f32>,
    ram_used_mb: u64,
    ram_total_mb: u64,
    app_ram_mb: u64,
    gpu: GpuInfo,
}

#[derive(serde::Serialize)]
struct NetworkStats {
    download_kbps: f64,
    upload_kbps: f64,
}

#[tauri::command]
fn get_system_stats(monitor: tauri::State<AppMonitor>) -> SystemStats {
    let mut sys = monitor.system.lock().unwrap();
    sys.refresh_cpu_all();
    sys.refresh_memory();

    let cpu_usage = sys.global_cpu_usage();
    let ram_total_mb = sys.total_memory() / 1_048_576;
    let ram_used_mb = sys.used_memory() / 1_048_576;

    let app_ram_mb = get_current_pid()
        .ok()
        .and_then(|pid| {
            sys.refresh_processes(ProcessesToUpdate::Some(&[pid]), false);
            sys.process(pid).map(|p| p.memory() / 1_048_576)
        })
        .unwrap_or(0);

    let cpu_temp_c = {
        let components = Components::new_with_refreshed_list();
        let found = components.iter()
            .find(|c| {
                let l = c.label().to_lowercase();
                l.contains("package") || l.contains("tdie") || l.contains("tccd")
                    || (l.contains("cpu") && !l.contains("core"))
            })
            .or_else(|| components.iter().find(|c| c.label().to_lowercase().contains("core")));
        found.and_then(|c| c.temperature()).filter(|&t| t.is_finite() && t > 0.0)
    };

    let gpu = monitor.gpu_cache.lock().unwrap().clone();

    SystemStats { cpu_usage, cpu_temp_c, ram_used_mb, ram_total_mb, app_ram_mb, gpu }
}

#[tauri::command]
fn get_network_stats(monitor: tauri::State<AppMonitor>) -> NetworkStats {
    let mut nets = monitor.networks.lock().unwrap();
    nets.refresh(false);

    let (total_rx, total_tx) = nets.iter().fold((0u64, 0u64), |(ar, at), (_, d)| {
        (ar + d.total_received(), at + d.total_transmitted())
    });

    let now = std::time::Instant::now();
    let mut prev = monitor.net_prev.lock().unwrap();

    let (download_kbps, upload_kbps) = match *prev {
        Some((prev_rx, prev_tx, prev_time)) => {
            let secs = now.duration_since(prev_time).as_secs_f64();
            if secs > 0.1 {
                let dl = total_rx.saturating_sub(prev_rx) as f64 / secs / 1024.0;
                let ul = total_tx.saturating_sub(prev_tx) as f64 / secs / 1024.0;
                (dl.max(0.0), ul.max(0.0))
            } else {
                (0.0, 0.0)
            }
        }
        None => (0.0, 0.0),
    };

    *prev = Some((total_rx, total_tx, now));

    NetworkStats { download_kbps, upload_kbps }
}

// GPU queried in a background thread — never blocks the main stats call.
fn query_gpu_info() -> GpuInfo {
    if let Some(info) = try_nvidia() {
        return info;
    }
    #[cfg(target_os = "linux")]
    if let Some(info) = try_amd_linux().or_else(try_intel_linux) {
        return info;
    }
    #[cfg(target_os = "windows")]
    if let Some(info) = try_windows_gpu() {
        return info;
    }
    GpuInfo::default()
}

// NVIDIA — query used + free so total = used+free, matching Task Manager's
// "Dedicated GPU memory" which excludes driver-reserved VRAM.
fn try_nvidia() -> Option<GpuInfo> {
    let out = std::process::Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,utilization.gpu,memory.used,memory.free,temperature.gpu",
            "--format=csv,noheader,nounits",
        ])
        .output()
        .ok()
        .filter(|o| o.status.success())?;
    let s = String::from_utf8(out.stdout).ok()?;
    let parts: Vec<&str> = s.trim().lines().next()?.split(',').map(str::trim).collect();
    if parts.len() < 4 { return None; }
    let vram_used: Option<u64> = parts[2].parse().ok();
    let vram_free: Option<u64> = parts[3].parse().ok();
    let vram_total = vram_used.zip(vram_free).map(|(u, f)| u + f);
    let temp_c: Option<f32> = parts.get(4).and_then(|s| s.parse().ok()).filter(|&t: &f32| t > 0.0);
    Some(GpuInfo {
        name:          Some(parts[0].to_string()),
        usage:         parts[1].parse().ok(),
        temp_c,
        vram_used_mb:  vram_used,
        vram_total_mb: vram_total,
    })
}

// AMD on Linux — sysfs busy percent + VRAM + name via lspci.
#[cfg(target_os = "linux")]
fn try_amd_linux() -> Option<GpuInfo> {
    for i in 0..4u8 {
        let busy = format!("/sys/class/drm/card{}/device/gpu_busy_percent", i);
        let vram_used = format!("/sys/class/drm/card{}/device/mem_info_vram_used", i);
        let vram_total = format!("/sys/class/drm/card{}/device/mem_info_vram_total", i);
        if let Ok(usage_s) = std::fs::read_to_string(&busy) {
            let temp_c = std::fs::read_to_string(format!("/sys/class/drm/card{}/device/hwmon/hwmon0/temp1_input", i))
                .ok().and_then(|s| s.trim().parse::<f32>().ok()).map(|m| m / 1000.0);
            return Some(GpuInfo {
                name:         lspci_gpu_name(),
                usage:        usage_s.trim().parse().ok(),
                temp_c,
                vram_used_mb: std::fs::read_to_string(&vram_used).ok()
                    .and_then(|s| s.trim().parse::<u64>().ok())
                    .map(|b| b / 1_048_576),
                vram_total_mb: std::fs::read_to_string(&vram_total).ok()
                    .and_then(|s| s.trim().parse::<u64>().ok())
                    .map(|b| b / 1_048_576),
            });
        }
    }
    None
}

// Intel on Linux — gt_busy_percent sysfs + name via lspci.
#[cfg(target_os = "linux")]
fn try_intel_linux() -> Option<GpuInfo> {
    for i in 0..4u8 {
        let busy = format!("/sys/class/drm/card{}/gt_busy_percent", i);
        if let Ok(s) = std::fs::read_to_string(&busy) {
            return Some(GpuInfo {
                name:          lspci_gpu_name(),
                usage:         s.trim().parse().ok(),
                temp_c:        None,
                vram_used_mb:  None,
                vram_total_mb: None,
            });
        }
    }
    None
}

#[cfg(target_os = "linux")]
fn lspci_gpu_name() -> Option<String> {
    let out = std::process::Command::new("lspci").output().ok()?;
    String::from_utf8(out.stdout).ok()?.lines()
        .find(|l| l.contains("VGA") || l.contains("Display") || l.contains("3D"))
        .map(|l| l.splitn(2, ':').nth(1).unwrap_or(l).trim().to_string())
}

// Windows — WMI for name + total VRAM, PDH for utilization + used VRAM.
// PDH Get-Counter takes ~1s per call; fine here since we're in the BG thread.
#[cfg(target_os = "windows")]
fn try_windows_gpu() -> Option<GpuInfo> {
    let ps = r#"
$g = Get-WmiObject Win32_VideoController | Select-Object -First 1
$name = $g.Name
$totalMB = [math]::Round($g.AdapterRAM / 1MB)
try {
    $util = ((Get-Counter '\GPU Engine(*engtype_3D)\Utilization Percentage' -ErrorAction Stop).CounterSamples | Measure-Object -Property CookedValue -Sum).Sum
} catch { $util = -1 }
try {
    $usedMB = [math]::Round(((Get-Counter '\GPU Local Adapter Memory(*)\Local Usage' -ErrorAction Stop).CounterSamples | Measure-Object -Property CookedValue -Sum).Sum / 1MB)
} catch { $usedMB = -1 }
"$name|$totalMB|$util|$usedMB"
"#;
    let out = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", ps])
        .output()
        .ok()
        .filter(|o| o.status.success())?;
    let s = String::from_utf8(out.stdout).ok()?;
    let parts: Vec<&str> = s.trim().split('|').collect();
    if parts.len() < 4 { return None; }
    let name = (!parts[0].is_empty()).then(|| parts[0].to_string());
    let vram_total_mb: Option<u64> = parts[1].parse().ok().filter(|&v| v > 0);
    let usage: Option<f32> = parts[2].parse().ok().filter(|&v: &f32| v >= 0.0).map(|v| v.min(100.0));
    let vram_used_mb: Option<u64> = parts[3].parse().ok().filter(|&v| v > 0);
    if name.is_none() && vram_total_mb.is_none() { return None; }
    Some(GpuInfo { name, usage, temp_c: None, vram_used_mb, vram_total_mb })
}

// ─────────────────────────────────────────────────────────────────────────────

const DEFAULT_DAEMON_CONTROL_TOKEN_FILE: &str = "runtime-daemon.control-token";
const DEFAULT_DAEMON_RELEASE_MANIFEST: &str = "runtime/runtime-daemon-release.json";

fn default_daemon_data_dir() -> Option<PathBuf> {
    dirs::data_dir().map(|dir| dir.join("anima").join("runtime-daemon"))
}

fn daemon_data_dir() -> PathBuf {
    env::var("ANIMA_DAEMON_DATA_DIR")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(default_daemon_data_dir)
        .or_else(|| {
            dirs::home_dir().map(|dir| {
                dir.join(".local")
                    .join("share")
                    .join("anima")
                    .join("runtime-daemon")
            })
        })
        .unwrap_or_else(|| env::temp_dir().join("anima").join("runtime-daemon"))
}

fn daemon_control_token_path() -> PathBuf {
    daemon_data_dir().join(DEFAULT_DAEMON_CONTROL_TOKEN_FILE)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DaemonReleaseManifest {
    daemon: DaemonReleaseManifestDaemon,
    runtime: DaemonReleaseManifestRuntime,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DaemonReleaseManifestDaemon {
    artifact_candidates: Vec<String>,
    config_default: DaemonReleaseManifestConfigDefault,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DaemonReleaseManifestConfigDefault {
    daemon_bind_host: String,
    daemon_bind_port: u16,
    runtime_host: String,
    runtime_port: u16,
    runtime_launch_mode: String,
    runtime_artifact: Option<String>,
    python_entry: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DaemonReleaseManifestRuntime {
    source_root: String,
    runtime_entrypoint: String,
    python_launcher_hint: String,
}

struct ResolvedManifestDaemonLaunch {
    executable: PathBuf,
    current_dir: Option<PathBuf>,
    env: Vec<(String, String)>,
}

#[tauri::command]
fn read_daemon_control_token() -> Option<String> {
    let token = fs::read_to_string(daemon_control_token_path()).ok()?;
    let trimmed = token.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

#[tauri::command]
fn start_local_runtime_daemon(app: tauri::AppHandle) -> Result<(), String> {
    let workspace_root = find_workspace_root();

    if let Some(executable) = resolve_configured_daemon_executable() {
        spawn_daemon_executable(&executable, workspace_root.as_deref(), &[])?;
        return Ok(());
    }

    if let Some(launch) = resolve_manifest_daemon_launch(&app) {
        spawn_daemon_executable(
            &launch.executable,
            launch.current_dir.as_deref(),
            &launch.env,
        )?;
        return Ok(());
    }

    if let Some(executable) = resolve_workspace_daemon_executable(workspace_root.as_deref()) {
        spawn_daemon_executable(&executable, workspace_root.as_deref(), &[])?;
        return Ok(());
    }

    if let Some(root) = workspace_root.as_deref() {
        let manifest_path = root.join("Cargo.toml");
        let mut command = std::process::Command::new("cargo");
        command
            .args([
                "run",
                "--manifest-path",
                manifest_path.to_string_lossy().as_ref(),
                "-p",
                "anima-local-runtime-daemon",
            ])
            .current_dir(root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }

        command.spawn().map_err(|err| {
            format!(
                "Failed to launch daemon with cargo from {}: {err}",
                root.display()
            )
        })?;
        return Ok(());
    }

    Err(
        "Unable to locate the local runtime daemon binary or workspace root. Set ANIMA_DAEMON_EXECUTABLE to the daemon binary path."
            .to_string(),
    )
}

fn spawn_daemon_executable(
    executable: &Path,
    current_dir: Option<&Path>,
    env_pairs: &[(String, String)],
) -> Result<(), String> {
    let mut command = std::process::Command::new(executable);
    if let Some(root) = current_dir {
        command.current_dir(root);
    }
    for (key, value) in env_pairs {
        command.env(key, value);
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    command.spawn().map_err(|err| {
        format!(
            "Failed to launch daemon executable {}: {err}",
            executable.display()
        )
    })?;

    Ok(())
}

fn resolve_configured_daemon_executable() -> Option<PathBuf> {
    env::var("ANIMA_DAEMON_EXECUTABLE")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .filter(|path| path.is_file())
}

fn load_release_manifest(app: &tauri::AppHandle) -> Option<(PathBuf, DaemonReleaseManifest)> {
    let manifest_path = env::var("ANIMA_DAEMON_RELEASE_MANIFEST")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .or_else(|| {
            app.path()
                .resolve(DEFAULT_DAEMON_RELEASE_MANIFEST, BaseDirectory::Resource)
                .ok()
                .filter(|path| path.is_file())
        })
        .or_else(find_release_manifest)?;

    let raw = fs::read_to_string(&manifest_path).ok()?;
    let manifest = serde_json::from_str::<DaemonReleaseManifest>(&raw).ok()?;

    Some((manifest_path, manifest))
}

fn resolve_manifest_daemon_launch(app: &tauri::AppHandle) -> Option<ResolvedManifestDaemonLaunch> {
    let (manifest_path, manifest) = load_release_manifest(app)?;
    let runtime_working_dir =
        resolve_manifest_runtime_working_dir(&manifest_path, &manifest.runtime);
    let runtime_artifact = manifest
        .daemon
        .config_default
        .runtime_artifact
        .as_deref()
        .and_then(|candidate| resolve_manifest_artifact_path(&manifest_path, candidate))
        .filter(|path| path.is_file());

    let mut env = vec![
        (
            "ANIMA_DAEMON_BIND_HOST".to_string(),
            manifest.daemon.config_default.daemon_bind_host.clone(),
        ),
        (
            "ANIMA_DAEMON_BIND_PORT".to_string(),
            manifest.daemon.config_default.daemon_bind_port.to_string(),
        ),
        (
            "ANIMA_DAEMON_RUNTIME_HOST".to_string(),
            manifest.daemon.config_default.runtime_host.clone(),
        ),
        (
            "ANIMA_DAEMON_RUNTIME_PORT".to_string(),
            manifest.daemon.config_default.runtime_port.to_string(),
        ),
        (
            "ANIMA_DAEMON_RUNTIME_LAUNCH_MODE".to_string(),
            manifest.daemon.config_default.runtime_launch_mode.clone(),
        ),
    ];

    if let Some(runtime_artifact) = runtime_artifact.as_ref() {
        env.push((
            "ANIMA_DAEMON_RUNTIME_ARTIFACT".to_string(),
            runtime_artifact.to_string_lossy().to_string(),
        ));
    }

    let runtime_command = manifest.daemon.config_default.python_entry.trim();
    if !runtime_command.is_empty() {
        env.push((
            "ANIMA_DAEMON_RUNTIME_COMMAND".to_string(),
            runtime_command.to_string(),
        ));
    }

    if let Some(runtime_tools_dir) = manifest_path
        .parent()
        .map(|parent| parent.join("runtime-tools"))
        .filter(|path| path.is_dir())
    {
        env.push(("PATH".to_string(), prepend_path_env(&runtime_tools_dir)));
    }

    let python_launcher_hint = manifest.runtime.python_launcher_hint.trim();
    if !python_launcher_hint.is_empty() {
        env.push((
            "ANIMA_DAEMON_PYTHON".to_string(),
            python_launcher_hint.to_string(),
        ));
    }

    if let Some(runtime_working_dir) = runtime_working_dir.as_ref() {
        env.push((
            "ANIMA_DAEMON_RUNTIME_WORKDIR".to_string(),
            runtime_working_dir.to_string_lossy().to_string(),
        ));
    }

    env.extend(resolve_runtime_data_env_overrides(
        runtime_working_dir.as_deref(),
    ));

    let executable = manifest
        .daemon
        .artifact_candidates
        .into_iter()
        .filter_map(|candidate| resolve_manifest_artifact_path(&manifest_path, &candidate))
        .find(|path| path.is_file())?;

    Some(ResolvedManifestDaemonLaunch {
        executable,
        current_dir: runtime_working_dir.or_else(|| manifest_path.parent().map(Path::to_path_buf)),
        env,
    })
}

fn prepend_path_env(path: &Path) -> String {
    let mut paths = vec![path.to_path_buf()];
    if let Some(existing) = env::var_os("PATH") {
        paths.extend(env::split_paths(&existing));
    }

    env::join_paths(paths)
        .map(|joined| joined.to_string_lossy().to_string())
        .unwrap_or_else(|_| path.to_string_lossy().to_string())
}

fn resolve_manifest_artifact_path(
    manifest_path: &Path,
    artifact_candidate: &str,
) -> Option<PathBuf> {
    let artifact_path = PathBuf::from(artifact_candidate);
    if artifact_path.is_absolute() {
        return Some(artifact_path);
    }

    manifest_path
        .parent()
        .map(|parent| parent.join(artifact_path))
}

fn resolve_manifest_runtime_working_dir(
    manifest_path: &Path,
    runtime: &DaemonReleaseManifestRuntime,
) -> Option<PathBuf> {
    let source_root = runtime.source_root.trim();
    if !source_root.is_empty() {
        if let Some(path) = resolve_manifest_artifact_path(manifest_path, source_root) {
            if path.is_dir() {
                return Some(path);
            }
        }
    }

    let runtime_entrypoint = runtime.runtime_entrypoint.trim();
    if !runtime_entrypoint.is_empty() {
        if let Some(path) = resolve_manifest_artifact_path(manifest_path, runtime_entrypoint) {
            if path.is_file() {
                return path.parent().map(Path::to_path_buf);
            }
        }
    }

    None
}

fn resolve_runtime_data_env_overrides(runtime_working_dir: Option<&Path>) -> Vec<(String, String)> {
    if runtime_working_dir
        .and_then(find_workspace_root_from)
        .is_some()
    {
        return Vec::new();
    }

    let explicit_data_dir = env_var_nonempty("ANIMA_DATA_DIR").map(PathBuf::from);
    let runtime_data_dir = explicit_data_dir
        .clone()
        .unwrap_or_else(|| daemon_data_dir().join("runtime"));
    let _ = fs::create_dir_all(&runtime_data_dir);
    let uv_project_environment = runtime_data_dir.join(".venv");

    let mut env = Vec::new();
    if explicit_data_dir.is_none() {
        env.push((
            "ANIMA_DATA_DIR".to_string(),
            runtime_data_dir.to_string_lossy().to_string(),
        ));
    }

    if env_var_nonempty("ANIMA_DATABASE_URL").is_none() {
        env.push((
            "ANIMA_DATABASE_URL".to_string(),
            sqlite_database_url(&runtime_data_dir.join("anima.db")),
        ));
    }

    env.push((
        "UV_PROJECT_ENVIRONMENT".to_string(),
        uv_project_environment.to_string_lossy().to_string(),
    ));
    env.push((
        "VIRTUAL_ENV".to_string(),
        uv_project_environment.to_string_lossy().to_string(),
    ));

    env
}

fn env_var_nonempty(key: &str) -> Option<String> {
    env::var(key)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn sqlite_database_url(path: &Path) -> String {
    let normalized = path.to_string_lossy().replace('\\', "/");
    format!("sqlite:///{normalized}")
}

fn resolve_workspace_daemon_executable(workspace_root: Option<&Path>) -> Option<PathBuf> {
    let root = workspace_root?;
    [
        root.join("target")
            .join("release")
            .join(daemon_binary_name()),
        root.join("target").join("debug").join(daemon_binary_name()),
        root.join(daemon_binary_name()),
    ]
    .into_iter()
    .find(|path| path.is_file())
}

fn daemon_binary_name() -> &'static str {
    #[cfg(windows)]
    {
        "anima-local-runtime-daemon.exe"
    }

    #[cfg(not(windows))]
    {
        "anima-local-runtime-daemon"
    }
}

fn find_release_manifest() -> Option<PathBuf> {
    let relative = Path::new(DEFAULT_DAEMON_RELEASE_MANIFEST);

    env::current_dir()
        .ok()
        .and_then(|dir| find_in_ancestors(&dir, relative))
        .or_else(|| {
            env::current_exe()
                .ok()
                .and_then(|path| path.parent().map(Path::to_path_buf))
                .and_then(|dir| find_in_ancestors(&dir, relative))
        })
}

fn find_workspace_root() -> Option<PathBuf> {
    env::current_dir()
        .ok()
        .and_then(|dir| find_workspace_root_from(&dir))
        .or_else(|| {
            env::current_exe()
                .ok()
                .and_then(|path| path.parent().map(Path::to_path_buf))
                .and_then(|dir| find_workspace_root_from(&dir))
        })
}

fn find_workspace_root_from(start: &Path) -> Option<PathBuf> {
    let mut cursor = Some(start);

    while let Some(path) = cursor {
        if path
            .join("apps")
            .join("local-runtime-daemon")
            .join("Cargo.toml")
            .is_file()
            && path
                .join("apps")
                .join("server")
                .join("src")
                .join("anima_server")
                .join("main.py")
                .is_file()
        {
            return Some(path.to_path_buf());
        }

        cursor = path.parent();
    }

    None
}

fn find_in_ancestors(start: &Path, relative: &Path) -> Option<PathBuf> {
    let mut cursor = Some(start);

    while let Some(path) = cursor {
        let candidate = path.join(relative);
        if candidate.is_file() {
            return Some(candidate);
        }

        cursor = path.parent();
    }

    None
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            // System tray
            let show = MenuItem::with_id(app, "show", "Open ANIMA", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("ANIMA")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let tauri::tray::TrayIconEvent::Click { .. } = event {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .manage({
            let gpu_cache = Arc::new(Mutex::new(GpuInfo::default()));
            let gpu_cache_bg = Arc::clone(&gpu_cache);
            std::thread::spawn(move || loop {
                let info = query_gpu_info();
                *gpu_cache_bg.lock().unwrap() = info;
                std::thread::sleep(std::time::Duration::from_secs(2));
            });
            AppMonitor {
                system: Mutex::new(System::new()),
                gpu_cache,
                networks: Mutex::new(Networks::new_with_refreshed_list()),
                net_prev: Mutex::new(None),
            }
        })
        .invoke_handler(tauri::generate_handler![
            read_daemon_control_token,
            start_local_runtime_daemon,
            get_system_stats,
            get_network_stats,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app, event| {
        if let RunEvent::WindowEvent {
            event: WindowEvent::CloseRequested { api, .. },
            ..
        } = event
        {
            api.prevent_close();
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.hide();
            }
        }
    });
}
