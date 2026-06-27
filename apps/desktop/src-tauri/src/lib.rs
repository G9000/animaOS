use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Stdio,
};
use serde::Deserialize;
use tauri::{
    path::BaseDirectory,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
    RunEvent,
    WindowEvent,
};

const DEFAULT_DAEMON_CONTROL_TOKEN_FILE: &str = "runtime-daemon.control-token";
const DEFAULT_DAEMON_RELEASE_MANIFEST: &str = ".anima/runtime-daemon-release.json";

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
        .unwrap_or_else(|| PathBuf::from(".").join(".anima").join("runtime-daemon"))
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

        command
            .spawn()
            .map_err(|err| format!("Failed to launch daemon with cargo from {}: {err}", root.display()))?;
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

    command
        .spawn()
        .map_err(|err| format!("Failed to launch daemon executable {}: {err}", executable.display()))?;

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
    let runtime_working_dir = resolve_manifest_runtime_working_dir(&manifest_path, &manifest.runtime);
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

    env.extend(resolve_runtime_data_env_overrides(runtime_working_dir.as_deref()));

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

fn resolve_manifest_artifact_path(manifest_path: &Path, artifact_candidate: &str) -> Option<PathBuf> {
    let artifact_path = PathBuf::from(artifact_candidate);
    if artifact_path.is_absolute() {
        return Some(artifact_path);
    }

    manifest_path.parent().map(|parent| parent.join(artifact_path))
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
    if runtime_working_dir.and_then(find_workspace_root_from).is_some() {
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
        root.join("target").join("release").join(daemon_binary_name()),
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
        if path.join("apps").join("local-runtime-daemon").join("Cargo.toml").is_file()
            && path.join("apps").join("server").join("src").join("anima_server").join("main.py").is_file()
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
        .invoke_handler(tauri::generate_handler![
            read_daemon_control_token,
            start_local_runtime_daemon
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
