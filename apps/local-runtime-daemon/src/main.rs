use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, HeaderValue, Method, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use chrono::{DateTime, Utc};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::{
    env,
    fmt::Display,
    fs::{self, OpenOptions},
    io::{ErrorKind, Write},
    path::{Path as FsPath, PathBuf},
    process::{ExitStatus, Stdio},
    str::FromStr,
    sync::Arc,
    time::Duration,
};
use tokio::{
    process::{Child, Command},
    sync::Mutex,
    time,
};
use tower_http::cors::{AllowOrigin, CorsLayer};
use tracing::{error, info, warn};
use uuid::Uuid;

const DAEMON_API_VERSION: &str = "v1";
const DEFAULT_DAEMON_HOST: &str = "127.0.0.1";
const DEFAULT_DAEMON_PORT: u16 = 3032;
const DEFAULT_RUNTIME_HOST: &str = "127.0.0.1";
const DEFAULT_RUNTIME_PORT: u16 = 3031;
const DEFAULT_HEALTH_POLL_SECONDS: u64 = 5;
const DEFAULT_HEALTH_TIMEOUT_SECONDS: u64 = 2;
const DEFAULT_MAX_RESTART_ATTEMPTS: u32 = 3;
const DEFAULT_BASE_RESTART_SECONDS: u64 = 2;
const DEFAULT_MAX_RESTART_SECONDS: u64 = 20;
const DEFAULT_LOG_ROTATE_BYTES: u64 = 5 * 1024 * 1024;
const DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS: u64 = 10;
const DEFAULT_DAEMON_STATE_FILE: &str = "runtime-daemon.state.json";
const DEFAULT_DAEMON_CONTROL_TOKEN_FILE: &str = "runtime-daemon.control-token";
const DEFAULT_DAEMON_ALLOWED_ORIGINS: &[&str] = &[
    "tauri://localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
];
const DAEMON_CONTROL_TOKEN_HEADER: &str = "x-anima-daemon-token";

#[derive(Clone)]
struct DaemonRuntime {
    state: Arc<DaemonRuntimeState>,
}

#[derive(Clone)]
struct DaemonRuntimeState {
    config: RuntimeConfig,
    inner: Arc<Mutex<RuntimeState>>,
}

struct RuntimeState {
    policy: DaemonPolicy,
    process: Option<RuntimeProcess>,
    status: DaemonState,
    expected_running: bool,
    pid: Option<u32>,
    started_at: Option<DateTime<Utc>>,
    ready_at: Option<DateTime<Utc>>,
    last_error: Option<String>,
    restart_attempts: u32,
    consecutive_health_failures: u32,
    restart_wait_seconds: Option<u64>,
}

enum DaemonCorsOrigins {
    Any,
    List(Vec<HeaderValue>),
    Deny,
}

struct RuntimeProcess {
    child: Child,
    pid: u32,
    started_at: DateTime<Utc>,
}

#[derive(Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum DaemonState {
    Stopped,
    Starting,
    Ready,
    Degraded,
    Locked,
    Stopping,
    Failed,
}

#[derive(Clone)]
struct DaemonPolicy {
    locked: bool,
    lock_on_close: bool,
    lock_on_idle: bool,
    background_enabled: bool,
}

#[derive(Clone)]
struct RuntimeConfig {
    daemon_bind_host: String,
    daemon_bind_port: u16,
    control_token: String,
    runtime_host: String,
    runtime_port: u16,
    runtime_command: String,
    runtime_args: Vec<String>,
    runtime_working_dir: Option<PathBuf>,
    runtime_launch_mode: String,
    runtime_artifact: String,
    runtime_nonce: String,
    data_dir: PathBuf,
    health_poll_interval_seconds: u64,
    health_timeout_seconds: u64,
    max_restart_attempts: u32,
    base_restart_seconds: u64,
    max_restart_seconds: u64,
    runtime_log_rotate_bytes: u64,
    lock_on_close: bool,
    lock_on_idle: bool,
}

impl RuntimeConfig {
    fn from_env() -> Self {
        let daemon_bind_host = env_or("ANIMA_DAEMON_BIND_HOST", DEFAULT_DAEMON_HOST);
        let daemon_bind_port = env_parse("ANIMA_DAEMON_BIND_PORT", DEFAULT_DAEMON_PORT);
        let runtime_host = env_or("ANIMA_DAEMON_RUNTIME_HOST", DEFAULT_RUNTIME_HOST);
        let runtime_port = env_parse("ANIMA_DAEMON_RUNTIME_PORT", DEFAULT_RUNTIME_PORT);
        let runtime_artifact = env_or("ANIMA_DAEMON_RUNTIME_ARTIFACT", "");
        let launch_mode = env_or("ANIMA_DAEMON_RUNTIME_LAUNCH_MODE", "python");
        let runtime_launch_command = env_or("ANIMA_DAEMON_RUNTIME_COMMAND", "");

        let (runtime_command, runtime_args, runtime_launch_mode) = resolve_runtime_launcher(
            &runtime_launch_command,
            &runtime_artifact,
            &runtime_host,
            runtime_port,
            &launch_mode,
        );

        let runtime_working_dir = env_opt("ANIMA_DAEMON_RUNTIME_WORKDIR")
            .map(PathBuf::from)
            .or_else(default_runtime_working_dir);

        let data_dir = env_opt("ANIMA_DAEMON_DATA_DIR")
            .map(PathBuf::from)
            .or_else(default_data_dir)
            .unwrap_or_else(default_fallback_data_dir);
        let control_token_path = data_dir.join(DEFAULT_DAEMON_CONTROL_TOKEN_FILE);
        let control_token = resolve_control_token(&control_token_path);

        Self {
            daemon_bind_host,
            daemon_bind_port,
            control_token,
            runtime_host,
            runtime_port,
            runtime_command,
            runtime_args,
            runtime_working_dir,
            runtime_launch_mode,
            runtime_artifact,
            runtime_nonce: random_nonce(),
            data_dir,
            health_poll_interval_seconds: env_parse(
                "ANIMA_DAEMON_HEALTH_POLL_SECONDS",
                DEFAULT_HEALTH_POLL_SECONDS,
            ),
            health_timeout_seconds: env_parse(
                "ANIMA_DAEMON_HEALTH_TIMEOUT_SECONDS",
                DEFAULT_HEALTH_TIMEOUT_SECONDS,
            ),
            max_restart_attempts: env_parse(
                "ANIMA_DAEMON_MAX_RESTART_ATTEMPTS",
                DEFAULT_MAX_RESTART_ATTEMPTS,
            ),
            base_restart_seconds: env_parse(
                "ANIMA_DAEMON_BASE_RESTART_SECONDS",
                DEFAULT_BASE_RESTART_SECONDS,
            ),
            max_restart_seconds: env_parse(
                "ANIMA_DAEMON_MAX_RESTART_SECONDS",
                DEFAULT_MAX_RESTART_SECONDS,
            ),
            runtime_log_rotate_bytes: env_parse(
                "ANIMA_DAEMON_RUNTIME_LOG_ROTATE_BYTES",
                DEFAULT_LOG_ROTATE_BYTES,
            ),
            lock_on_close: env_bool("ANIMA_DAEMON_LOCK_ON_CLOSE", true),
            lock_on_idle: env_bool("ANIMA_DAEMON_LOCK_ON_IDLE", true),
        }
    }

    fn runtime_health_url(&self) -> String {
        format!("http://{}:{}/health", self.runtime_host, self.runtime_port)
    }

    fn runtime_health_url_with_prefix(&self) -> String {
        format!(
            "http://{}:{}/api/health",
            self.runtime_host, self.runtime_port
        )
    }

    fn runtime_port_file(&self) -> PathBuf {
        self.data_dir.join("runtime.port")
    }

    fn runtime_pid_file(&self) -> PathBuf {
        self.data_dir.join("runtime.pid")
    }

    fn runtime_log_file(&self) -> PathBuf {
        self.data_dir.join("runtime.log")
    }

    fn lock_file(&self) -> PathBuf {
        self.data_dir.join(DEFAULT_DAEMON_STATE_FILE)
    }
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct DaemonRuntimeIdentity {
    command: String,
    args: Vec<String>,
    working_dir: Option<String>,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct DaemonRuntimeStatus {
    pid: Option<u32>,
    port: u16,
    port_file: String,
    pid_file: String,
    log_file: String,
    artifact_path: String,
    launch_mode: String,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct DaemonLockStatus {
    enabled: bool,
    lock_on_close: bool,
    lock_on_idle: bool,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct DaemonRestartPolicy {
    enabled: bool,
    max_attempts: u32,
    attempts: u32,
    next_delay_seconds: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DaemonStatusResponse {
    version: &'static str,
    state: DaemonState,
    runtime_identity: DaemonRuntimeIdentity,
    runtime: DaemonRuntimeStatus,
    lock: DaemonLockStatus,
    restart: DaemonRestartPolicy,
    background_enabled: bool,
    updated_at: String,
    error: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DaemonRuntimeNonceResponse {
    runtime_nonce: String,
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DaemonControlRequest {
    #[serde(default)]
    background_enabled: Option<bool>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DaemonControlResponse {
    success: bool,
    message: String,
    state: DaemonState,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DaemonLogResponse {
    log_file: String,
    requested_lines: usize,
    lines: Vec<String>,
    truncated: bool,
}

#[derive(Serialize)]
struct DaemonHealthResponse {
    version: &'static str,
    status: DaemonState,
    updated_at: String,
}

#[derive(Serialize)]
struct DaemonError {
    category: String,
    message: String,
    detail: Option<String>,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "kebab-case")]
enum DaemonCommand {
    Start,
    Stop,
    Restart,
    Lock,
    Unlock,
    SetBackground,
}

impl RuntimeState {
    fn default_with_background(
        background_enabled: bool,
        lock_on_close: bool,
        lock_on_idle: bool,
    ) -> Self {
        Self {
            policy: DaemonPolicy {
                locked: false,
                lock_on_close,
                lock_on_idle,
                background_enabled,
            },
            process: None,
            status: DaemonState::Stopped,
            expected_running: false,
            pid: None,
            started_at: None,
            ready_at: None,
            last_error: None,
            restart_attempts: 0,
            consecutive_health_failures: 0,
            restart_wait_seconds: None,
        }
    }

    fn apply_lock_state(&mut self) {
        if self.policy.locked && self.expected_running {
            self.status = DaemonState::Locked;
        } else if !self.expected_running {
            self.status = DaemonState::Stopped;
        } else if self.process.is_some() {
            if self.consecutive_health_failures > 0 {
                self.status = DaemonState::Degraded;
            } else {
                self.status = DaemonState::Ready;
            }
        } else {
            self.status = DaemonState::Stopped;
        }
    }

    fn mark_restart_delay(&mut self, config: &RuntimeConfig, reason: &str) -> Option<u64> {
        if !self.expected_running || !self.policy.background_enabled {
            self.status = DaemonState::Stopped;
            self.restart_wait_seconds = None;
            return None;
        }

        if self.restart_attempts >= config.max_restart_attempts {
            self.status = DaemonState::Failed;
            self.restart_wait_seconds = None;
            self.last_error = Some(format!("Restart limit reached: {reason}"));
            return None;
        }

        let shift = self.restart_attempts.min(10);
        let max = u64::from(u32::MAX);
        let multiplier = 1u64.checked_shl(shift).unwrap_or(u64::MAX);
        let next_delay = (config.base_restart_seconds.saturating_mul(multiplier))
            .min(config.max_restart_seconds.max(1));
        self.restart_attempts = self.restart_attempts.saturating_add(1);
        self.restart_wait_seconds = Some(next_delay);
        self.status = DaemonState::Degraded;
        self.last_error = Some(format!("{reason}; restart in {next_delay}s"));
        Some(next_delay.min(max))
    }
}

fn resolve_control_token(path: &FsPath) -> String {
    let explicit = env_opt("ANIMA_DAEMON_CONTROL_TOKEN")
        .and_then(|token| {
            let trimmed = token.trim().to_string();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed)
            }
        });

    if let Some(token) = explicit {
        if let Err(err) = write_state_file(path, &token) {
            warn!(
                "Failed to persist explicit daemon control token {}: {err}",
                path.display()
            );
        }
        return token;
    }

    if let Some(stored) = read_control_token(path) {
        return stored;
    }

    let generated = random_nonce();
    if let Err(err) = write_state_file(path, &generated) {
        warn!(
            "Failed to persist generated daemon control token {}: {err}",
            path.display()
        );
    }
    generated
}

fn read_control_token(path: &FsPath) -> Option<String> {
    let token = fs::read_to_string(path).ok()?;
    let trimmed = token.trim();
    if trimmed.is_empty() {
        None
    } else {
        tighten_private_file_permissions(path, "daemon control token");
        Some(trimmed.to_string())
    }
}

#[cfg(unix)]
fn tighten_private_file_permissions(path: &FsPath, label: &str) {
    use std::os::unix::fs::PermissionsExt;

    let Ok(metadata) = fs::metadata(path) else {
        return;
    };

    let mode = metadata.permissions().mode() & 0o777;
    if mode == 0o600 {
        return;
    }

    let mut permissions = metadata.permissions();
    permissions.set_mode(0o600);
    if let Err(err) = fs::set_permissions(path, permissions) {
        warn!("Failed to tighten permissions for {label} {}: {err}", path.display());
    }
}

#[cfg(not(unix))]
fn tighten_private_file_permissions(_path: &FsPath, _label: &str) {}

fn daemon_allowed_origins() -> DaemonCorsOrigins {
    match env_opt("ANIMA_DAEMON_ALLOWED_ORIGINS") {
        Some(configured) => parse_daemon_allowed_origins(&configured, "ANIMA_DAEMON_ALLOWED_ORIGINS"),
        None => parse_daemon_allowed_origins(
            &DEFAULT_DAEMON_ALLOWED_ORIGINS.join(","),
            "DEFAULT_DAEMON_ALLOWED_ORIGINS",
        ),
    }
}

fn parse_daemon_allowed_origins(configured: &str, source: &str) -> DaemonCorsOrigins {
    let mut parsed = Vec::<HeaderValue>::new();
    for origin in configured.split(',') {
        let origin = origin.trim();
        if origin.is_empty() {
            continue;
        }
        if origin == "*" {
            return DaemonCorsOrigins::Any;
        }
        match HeaderValue::from_str(origin) {
            Ok(value) => parsed.push(value),
            Err(err) => warn!("Ignoring invalid CORS origin '{origin}': {err}"),
        }
    }

    if parsed.is_empty() {
        warn!("No valid daemon CORS origins parsed from {source}; rejecting cross-origin browser access");
        return DaemonCorsOrigins::Deny;
    }

    DaemonCorsOrigins::List(parsed)
}

#[tokio::main]
async fn main() {
    let env_filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| "anima_local_runtime_daemon=info".parse().unwrap());
    tracing_subscriber::fmt().with_env_filter(env_filter).init();

    let config = RuntimeConfig::from_env();
    if let Err(err) = fs::create_dir_all(&config.data_dir) {
        warn!(
            "Cannot create daemon data directory {}: {err}",
            config.data_dir.display()
        );
    }

    if let Some(log_dir) = config.runtime_log_file().parent() {
        let _ = fs::create_dir_all(log_dir);
    }

    let runtime = DaemonRuntime {
        state: Arc::new(DaemonRuntimeState {
            config: config.clone(),
            inner: Arc::new(Mutex::new(RuntimeState::default_with_background(
                env_bool("ANIMA_DAEMON_BACKGROUND_ENABLED", true),
                env_bool("ANIMA_DAEMON_LOCK_ON_CLOSE", true),
                env_bool("ANIMA_DAEMON_LOCK_ON_IDLE", true),
            ))),
        }),
    };

    if let Err(err) = write_state_file(&runtime.state.config.lock_file(), "created") {
        warn!(
            "Failed to write startup state file {}: {err}",
            runtime.state.config.lock_file().display()
        );
    }

    let runtime_status = runtime.clone();
    tokio::spawn(async move {
        let interval = runtime_status
            .state
            .config
            .health_poll_interval_seconds
            .max(1);
        let mut ticker = time::interval(Duration::from_secs(interval));

        loop {
            ticker.tick().await;
            if let Err(err) = tick_poll(&runtime_status).await {
                warn!("Health poll warning: {err}");
            }
        }
    });

    if env_bool("ANIMA_DAEMON_AUTO_START", false) {
        if let Err(err) = start_runtime(&runtime, false).await {
            warn!("Failed to start runtime from auto-start: {err}");
        }
    }

    let cors_layer = CorsLayer::new()
        .allow_methods([Method::GET, Method::POST, Method::OPTIONS])
        .allow_headers(tower_http::cors::Any);
    let cors_layer = match daemon_allowed_origins() {
        DaemonCorsOrigins::Any => cors_layer.allow_origin(tower_http::cors::Any),
        DaemonCorsOrigins::List(allowed_origins) => {
            cors_layer.allow_origin(AllowOrigin::list(allowed_origins))
        }
        DaemonCorsOrigins::Deny => cors_layer,
    };

    let app = Router::new()
        .route("/v1/health", get(health))
        .route("/v1/status", get(status))
        .route("/v1/nonce", get(runtime_nonce))
        .route("/v1/control/{command}", post(control))
        .route("/v1/logs", get(open_logs))
        .with_state(runtime.clone())
        .layer(cors_layer);

    let bind = format!(
        "{}:{}",
        runtime.state.config.daemon_bind_host, runtime.state.config.daemon_bind_port
    );
    info!("Starting local runtime daemon on {bind}");

    let listener = tokio::net::TcpListener::bind(&bind)
        .await
        .unwrap_or_else(|err| panic!("Failed to bind daemon control socket at {bind}: {err}"));

    if let Err(err) = axum::serve(listener, app).await {
        error!("Daemon server error: {err}");
    }
}

async fn health(State(runtime): State<DaemonRuntime>) -> Json<DaemonHealthResponse> {
    let state = runtime.state.inner.lock().await;
    Json(DaemonHealthResponse {
        version: DAEMON_API_VERSION,
        status: state.status.clone(),
        updated_at: Utc::now().to_rfc3339(),
    })
}

async fn status(State(runtime): State<DaemonRuntime>, headers: HeaderMap) -> Response {
    if let Err(response) = authorize_control(&runtime.state.config, &headers) {
        return response.into_response();
    }

    let state = runtime.state.inner.lock().await;
    (
        StatusCode::OK,
        Json(build_status_response(&runtime.state.config, &state)),
    )
        .into_response()
}

#[derive(Deserialize)]
struct OpenLogParams {
    lines: Option<usize>,
}

async fn open_logs(
    State(runtime): State<DaemonRuntime>,
    headers: HeaderMap,
    Query(params): Query<OpenLogParams>,
) -> Response {
    if let Err(response) = authorize_control(&runtime.state.config, &headers) {
        return response.into_response();
    }

    let limit = params.lines.unwrap_or(120).clamp(10, 500);
    let log_path = runtime.state.config.runtime_log_file();

    let raw = match fs::read_to_string(&log_path) {
        Ok(content) => content,
        Err(err) if err.kind() == ErrorKind::NotFound => String::new(),
        Err(err) => {
            let message = format!("Cannot open runtime log {}: {err}", log_path.display());
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({ "error": message })),
            )
                .into_response();
        }
    };

    let mut lines: Vec<String> = raw.lines().map(std::string::ToString::to_string).collect();
    let truncated = lines.len() > limit;
    if lines.len() > limit {
        let start = lines.len() - limit;
        lines = lines[start..].to_vec();
    }

    (
        StatusCode::OK,
        Json(DaemonLogResponse {
            log_file: log_path.to_string_lossy().to_string(),
            requested_lines: limit,
            lines,
            truncated,
        }),
    )
        .into_response()
}

async fn runtime_nonce(State(runtime): State<DaemonRuntime>, headers: HeaderMap) -> Response {
    if let Err(response) = authorize_control(&runtime.state.config, &headers) {
        return response.into_response();
    }

    (
        StatusCode::OK,
        Json(DaemonRuntimeNonceResponse {
            runtime_nonce: runtime.state.config.runtime_nonce.clone(),
        }),
    )
        .into_response()
}

async fn control(
    State(runtime): State<DaemonRuntime>,
    Path(command): Path<DaemonCommand>,
    headers: HeaderMap,
    body: Option<Json<DaemonControlRequest>>,
) -> Response {
    if let Err(response) = authorize_control(&runtime.state.config, &headers) {
        return response.into_response();
    }

    let payload = body.map(|Json(value)| value).unwrap_or_default();
    let result = match command {
        DaemonCommand::Start => start_runtime(&runtime, false).await,
        DaemonCommand::Stop => stop_runtime(&runtime).await,
        DaemonCommand::Restart => match stop_runtime(&runtime).await {
            Ok(message) if message == "Runtime stop already in progress" => {
                Err("Runtime is stopping; wait for shutdown before restarting".to_string())
            }
            Ok(_) => start_runtime(&runtime, false).await,
            Err(message) => Err(message),
        },
        DaemonCommand::Lock => set_lock(&runtime, true).await,
        DaemonCommand::Unlock => set_lock(&runtime, false).await,
        DaemonCommand::SetBackground => match payload.background_enabled {
            Some(background_enabled) => set_background_enabled(&runtime, background_enabled).await,
            None => Err("backgroundEnabled missing".to_string()),
        },
    };

    match result {
        Ok(message) => {
            let state = runtime.state.inner.lock().await;
            (
                StatusCode::OK,
                Json(DaemonControlResponse {
                    success: true,
                    message,
                    state: state.status.clone(),
                }),
            )
                .into_response()
        }
        Err(message) => {
            let state = runtime.state.inner.lock().await;
            (
                StatusCode::CONFLICT,
                Json(DaemonControlResponse {
                    success: false,
                    message,
                    state: state.status.clone(),
                }),
            )
                .into_response()
        }
    }
}

fn authorize_control(
    config: &RuntimeConfig,
    headers: &HeaderMap,
) -> Result<(), (StatusCode, Json<DaemonError>)> {
    let token = config.control_token.trim();
    if token.is_empty() {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(DaemonError {
                category: "auth".to_string(),
                message: "Daemon control token is required".to_string(),
                detail: None,
            }),
        ));
    };

    let provided = headers
        .get(DAEMON_CONTROL_TOKEN_HEADER)
        .and_then(|value| value.to_str().ok())
        .map(str::trim);

    if provided == Some(token) {
        return Ok(());
    }

    Err((
        StatusCode::UNAUTHORIZED,
        Json(DaemonError {
            category: "auth".to_string(),
            message: "Missing or invalid control token".to_string(),
            detail: None,
        }),
    ))
}

async fn start_runtime(runtime: &DaemonRuntime, from_restart: bool) -> Result<String, String> {
    {
        let mut state = runtime.state.inner.lock().await;
        if state.status == DaemonState::Stopping {
            return Err("Runtime is stopping; wait for shutdown before starting".to_string());
        }
        if state.policy.locked {
            return Err("Runtime is locked; unlock before starting".to_string());
        }
        if state.process.is_some() && state.expected_running {
            return Err("Runtime already running".to_string());
        }

        state.expected_running = true;
        if !from_restart {
            state.restart_attempts = 0;
            state.restart_wait_seconds = None;
            state.consecutive_health_failures = 0;
        }
        state.last_error = None;
        state.status = DaemonState::Starting;
    }

    let config = &runtime.state.config;
    let mut command = Command::new(&config.runtime_command);
    command.args(&config.runtime_args);
    command.env("ANIMA_RUNTIME_HOST", &config.runtime_host);
    command.env("ANIMA_RUNTIME_PORT", config.runtime_port.to_string());
    command.env("ANIMA_SIDECAR_NONCE", &config.runtime_nonce);
    command.env("PYTHONUNBUFFERED", "1");
    command.env("ANIMA_DAEMON_CONTROL_TOKEN", &config.control_token);

    if let Some(workdir) = &config.runtime_working_dir {
        command.current_dir(workdir);
    }

    if let Some(dir) = config.runtime_log_file().parent() {
        let _ = fs::create_dir_all(dir);
    }

    rotate_log_if_needed(&config.runtime_log_file(), config.runtime_log_rotate_bytes);

    let log = match OpenOptions::new()
        .create(true)
        .append(true)
        .open(&config.runtime_log_file())
    {
        Ok(log) => log,
        Err(err) => {
            let mut state = runtime.state.inner.lock().await;
            state.status = DaemonState::Failed;
            state.last_error = Some(format!(
                "Cannot open runtime log {}: {err}",
                config.runtime_log_file().display()
            ));
            return Err(format!(
                "Cannot open runtime log {}: {err}",
                config.runtime_log_file().display()
            ));
        }
    };

    let stdout = match log.try_clone() {
        Ok(stdout) => Stdio::from(stdout),
        Err(err) => {
            let mut state = runtime.state.inner.lock().await;
            state.status = DaemonState::Failed;
            state.last_error = Some(format!(
                "Cannot duplicate runtime log handle {}: {err}",
                config.runtime_log_file().display()
            ));
            return Err(format!(
                "Cannot duplicate runtime log handle {}: {err}",
                config.runtime_log_file().display()
            ));
        }
    };
    command.stdout(stdout);
    command.stderr(Stdio::from(log));

    let child = match command.spawn() {
        Ok(child) => child,
        Err(err) => {
            let mut state = runtime.state.inner.lock().await;
            state.status = DaemonState::Failed;
            state.last_error = Some(format!("Failed to start runtime process: {err}"));
            return Err(format!("Failed to start runtime process: {err}"));
        }
    };

    let pid = child.id().unwrap_or_default();
    let now = Utc::now();

    {
        let mut state = runtime.state.inner.lock().await;
        state.pid = if pid == 0 { None } else { Some(pid) };
        state.process = Some(RuntimeProcess {
            child,
            pid,
            started_at: now,
        });
        state.started_at = Some(now);
        state.ready_at = None;
        state.status = if state.policy.locked {
            DaemonState::Locked
        } else {
            DaemonState::Starting
        };
        state.last_error = None;
    }

    if pid != 0 {
        if let Err(err) = fs::write(config.runtime_pid_file(), pid.to_string()) {
            warn!(
                "Failed to write pid file {}: {err}",
                config.runtime_pid_file().display()
            );
        }
    }

    if let Err(err) = fs::write(config.runtime_port_file(), config.runtime_port.to_string()) {
        warn!(
            "Failed to write runtime port file {}: {err}",
            config.runtime_port_file().display()
        );
    }
    if let Err(err) = write_state_file(&config.lock_file(), "started") {
        warn!(
            "Failed to write state file {}: {err}",
            config.lock_file().display()
        );
    }

    info!(
        "Started runtime process pid={} command={} {}",
        pid,
        config.runtime_command,
        config.runtime_args.join(" ")
    );

    Ok("Runtime start requested".to_string())
}

async fn stop_runtime(runtime: &DaemonRuntime) -> Result<String, String> {
    let process = {
        let mut state = runtime.state.inner.lock().await;
        if state.process.is_none() {
            if state.status == DaemonState::Stopping {
                return Ok("Runtime stop already in progress".to_string());
            }
            if state.expected_running {
                state.expected_running = false;
            }
            state.status = if state.policy.locked {
                DaemonState::Locked
            } else {
                DaemonState::Stopped
            };
            state.pid = None;
            state.process = None;
            state.started_at = None;
            state.ready_at = None;
            state.restart_wait_seconds = None;
            state.consecutive_health_failures = 0;
            return Ok("Runtime already stopped".to_string());
        }

        let process = state.process.take();
        state.expected_running = false;
        state.status = DaemonState::Stopping;
        state.pid = None;
        state.started_at = None;
        state.ready_at = None;
        state.last_error = None;
        state.restart_wait_seconds = None;
        state.consecutive_health_failures = 0;
        process
    };

    if let Some(mut process) = process {
        match shutdown_runtime_process(&mut process).await {
            Ok(status) => {
                let code = status.code().unwrap_or_default();
                if let Err(err) = write_state_file(
                    &runtime.state.config.lock_file(),
                    &format!("stopped:{code}"),
                ) {
                    warn!(
                        "Failed to write state file {}: {err}",
                        runtime.state.config.lock_file().display()
                    );
                }
            }
            Err(err) => {
                return Err(format!(
                    "Failed to stop runtime process {}: {err}",
                    process.pid
                ));
            }
        }
    }

    {
        let mut state = runtime.state.inner.lock().await;
        state.status = if state.policy.locked {
            DaemonState::Locked
        } else {
            DaemonState::Stopped
        };
        state.pid = None;
        state.process = None;
    }

    Ok("Runtime stop requested".to_string())
}

async fn set_lock(runtime: &DaemonRuntime, locked: bool) -> Result<String, String> {
    if locked {
        {
            let mut state = runtime.state.inner.lock().await;
            state.policy.locked = true;
        }
        let _ = stop_runtime(runtime).await?;
    } else {
        let mut state = runtime.state.inner.lock().await;
        state.policy.locked = false;
        state.apply_lock_state();
    }

    if let Err(err) = write_state_file(
        &runtime.state.config.lock_file(),
        if locked { "locked" } else { "unlocked" },
    ) {
        warn!(
            "Failed to write state file {}: {err}",
            runtime.state.config.lock_file().display()
        );
    }

    Ok(if locked {
        "Runtime lock enabled".to_string()
    } else {
        "Runtime lock disabled".to_string()
    })
}

async fn set_background_enabled(
    runtime: &DaemonRuntime,
    background_enabled: bool,
) -> Result<String, String> {
    {
        let mut state = runtime.state.inner.lock().await;
        state.policy.background_enabled = background_enabled;
        if !background_enabled {
            state.restart_wait_seconds = None;
            if state.process.is_none() {
                state.expected_running = false;
                state.consecutive_health_failures = 0;
                state.status = DaemonState::Stopped;
            }
        }
    }

    Ok(if background_enabled {
        "Background mode enabled".to_string()
    } else {
        "Background mode disabled".to_string()
    })
}

async fn tick_poll(runtime: &DaemonRuntime) -> Result<(), String> {
    let mut should_restart_in_seconds: Option<u64> = None;
    let mut should_check_health = false;
    let mut process_to_restart: Option<RuntimeProcess> = None;
    let config = runtime.state.config.clone();

    {
        let mut state = runtime.state.inner.lock().await;
        let mut process_missing = state.process.is_none();

        if state.status == DaemonState::Locked && !state.policy.locked {
            state.status = DaemonState::Stopped;
        }

        if let Some(process) = state.process.as_mut() {
            match process.child.try_wait() {
                Ok(Some(exit_status)) => {
                    let code = exit_status.code().unwrap_or_default();
                    state.pid = None;
                    state.process = None;
                    state.ready_at = None;
                    state.started_at = None;
                    state.consecutive_health_failures =
                        state.consecutive_health_failures.saturating_add(1);
                    process_missing = true;
                    state.last_error = Some(format!("Runtime exited with code {code}"));

                    if state.expected_running {
                        should_restart_in_seconds = state
                            .mark_restart_delay(&config, "runtime process exited unexpectedly");
                    } else {
                        state.status = DaemonState::Stopped;
                        state.consecutive_health_failures = 0;
                    }
                }
                Ok(None) => {
                    should_check_health = state.expected_running && state.process.is_some();
                }
                Err(err) => {
                    state.consecutive_health_failures =
                        state.consecutive_health_failures.saturating_add(1);
                    state.last_error = Some(format!("Runtime wait check failed: {err}"));
                    should_check_health = state.expected_running;
                }
            }
        }

        if process_missing
            && state.expected_running
            && state.process.is_none()
            && should_restart_in_seconds.is_none()
        {
            should_restart_in_seconds =
                state.mark_restart_delay(&config, "runtime not running while expected");
        }

        if state.policy.locked {
            if state.process.is_some() {
                state.status = DaemonState::Locked;
            } else if !state.expected_running && state.status != DaemonState::Failed {
                state.status = DaemonState::Stopped;
            }
        } else if state.process.is_some() && state.consecutive_health_failures > 0 {
            state.status = DaemonState::Degraded;
        } else if !state.expected_running && state.status != DaemonState::Stopped {
            if state.status == DaemonState::Failed {
                // keep failed while errors are present
            } else {
                state.status = DaemonState::Stopped;
            }
        }
    }

    if should_check_health {
        match check_runtime_health(&runtime.state.config).await {
            Ok(()) => {
                let mut state = runtime.state.inner.lock().await;
                state.consecutive_health_failures = 0;
                state.restart_attempts = 0;
                state.restart_wait_seconds = None;
                state.last_error = None;
                state.ready_at = Some(Utc::now());
                if state.policy.locked {
                    state.status = DaemonState::Locked;
                } else {
                    state.status = DaemonState::Ready;
                }
            }
            Err(err) => {
                let mut state = runtime.state.inner.lock().await;
                state.consecutive_health_failures =
                    state.consecutive_health_failures.saturating_add(1);
                state.last_error = Some(err.clone());
                state.status = DaemonState::Degraded;
                if state.consecutive_health_failures >= 2 {
                    should_restart_in_seconds = state
                        .mark_restart_delay(&runtime.state.config, "runtime health check failed");
                    if should_restart_in_seconds.is_some() {
                        process_to_restart = state.process.take();
                    }
                    if should_restart_in_seconds.is_some() && state.consecutive_health_failures > 1
                    {
                        state.status = DaemonState::Degraded;
                    }
                }
            }
        }
    }

    if let Some(mut process) = process_to_restart {
        if let Err(err) = process.child.kill().await {
            warn!(
                "Failed to restart unhealthy runtime process {}: {err}",
                process.pid
            );
        }
        if let Err(err) = process.child.wait().await {
            warn!(
                "Failed to wait for killed unhealthy runtime process {}: {err}",
                process.pid
            );
        }
    }

    if let Some(delay) = should_restart_in_seconds {
        if delay > 0 {
            time::sleep(Duration::from_secs(delay)).await;
        }

        {
            let state = runtime.state.inner.lock().await;
            if !state.expected_running || state.process.is_some() || !state.policy.background_enabled
            {
                return Ok(());
            }
        }

        if let Err(err) = start_runtime(runtime, true).await {
            return Err(err);
        }
        {
            let mut state = runtime.state.inner.lock().await;
            state.restart_wait_seconds = None;
        }
    }

    Ok(())
}

async fn shutdown_runtime_process(process: &mut RuntimeProcess) -> Result<ExitStatus, String> {
    if let Err(err) = request_runtime_process_shutdown(process.pid).await {
        warn!(
            "Failed to request graceful shutdown for runtime process {}: {err}",
            process.pid
        );
    }

    let graceful_timeout = Duration::from_secs(DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS);
    match time::timeout(graceful_timeout, process.child.wait()).await {
        Ok(Ok(status)) => return Ok(status),
        Ok(Err(err)) => {
            return Err(format!(
                "Failed waiting for runtime process {} to exit: {err}",
                process.pid
            ));
        }
        Err(_) => {
            warn!(
                "Runtime process {} did not exit after graceful shutdown request; forcing kill",
                process.pid
            );
        }
    }

    process
        .child
        .kill()
        .await
        .map_err(|err| format!("Failed to force-kill runtime process {}: {err}", process.pid))?;

    process
        .child
        .wait()
        .await
        .map_err(|err| format!("Failed waiting for force-killed runtime process {}: {err}", process.pid))
}

async fn request_runtime_process_shutdown(pid: u32) -> Result<(), String> {
    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("taskkill");
        command.args(["/PID", &pid.to_string(), "/T"]);
        command
    };

    #[cfg(not(windows))]
    let mut command = {
        let mut command = Command::new("kill");
        command.arg("-TERM").arg(pid.to_string());
        command
    };

    let status = command
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
        .map_err(|err| format!("Failed to request graceful shutdown for runtime process {pid}: {err}"))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "Graceful shutdown command for runtime process {pid} exited with {status}"
        ))
    }
}

fn build_status_response(config: &RuntimeConfig, state: &RuntimeState) -> DaemonStatusResponse {
    let artifact = if config.runtime_artifact.is_empty() {
        config.runtime_command.clone()
    } else {
        config.runtime_artifact.clone()
    };

    DaemonStatusResponse {
        version: DAEMON_API_VERSION,
        state: state.status.clone(),
        runtime_identity: DaemonRuntimeIdentity {
            command: config.runtime_command.clone(),
            args: config.runtime_args.clone(),
            working_dir: config
                .runtime_working_dir
                .as_deref()
                .map(|path| path.to_string_lossy().to_string()),
        },
        runtime: DaemonRuntimeStatus {
            pid: state.pid,
            port: config.runtime_port,
            port_file: config.runtime_port_file().to_string_lossy().to_string(),
            pid_file: config.runtime_pid_file().to_string_lossy().to_string(),
            log_file: config.runtime_log_file().to_string_lossy().to_string(),
            artifact_path: artifact,
            launch_mode: config.runtime_launch_mode.clone(),
        },
        lock: DaemonLockStatus {
            enabled: state.policy.locked,
            lock_on_close: config.lock_on_close,
            lock_on_idle: config.lock_on_idle,
        },
        restart: DaemonRestartPolicy {
            enabled: state.policy.background_enabled,
            max_attempts: config.max_restart_attempts,
            attempts: state.restart_attempts,
            next_delay_seconds: state.restart_wait_seconds.unwrap_or(0),
        },
        background_enabled: state.policy.background_enabled,
        updated_at: Utc::now().to_rfc3339(),
        error: state.last_error.clone(),
    }
}

async fn check_runtime_health(config: &RuntimeConfig) -> Result<(), String> {
    let client = Client::new();
    let url = config.runtime_health_url();
    let response = client
        .get(url)
        .timeout(Duration::from_secs(config.health_timeout_seconds.max(1)))
        .send()
        .await
        .map_err(|err| format!("Health request failed: {err}"))?;

    let status = response.status();
    if status.is_success() {
        return Ok(());
    }

    if let Ok(body) = response.text().await {
        return Err(format!(
            "Runtime health endpoint responded with HTTP {}: {body}",
            status
        ));
    }

    Err(format!("Runtime health endpoint failed: {}", status))
}

fn write_state_file(path: &FsPath, value: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        let mut file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .mode(0o600)
            .open(path)?;
        file.write_all(value.as_bytes())
    }

    #[cfg(not(unix))]
    {
        fs::write(path, value)
    }
}

fn rotate_log_if_needed(path: &FsPath, max_bytes: u64) {
    let Ok(metadata) = fs::metadata(path) else {
        return;
    };

    if metadata.len() <= max_bytes {
        return;
    }

    let rotated = path.with_extension("log.old");
    if rotated.exists() {
        let _ = fs::remove_file(&rotated);
    }

    if let Err(err) = fs::rename(path, &rotated) {
        warn!("Failed to rotate runtime log {}: {err}", path.display());
    }
}

fn resolve_runtime_launcher(
    runtime_command: &str,
    runtime_artifact: &str,
    runtime_host: &str,
    runtime_port: u16,
    launch_mode: &str,
) -> (String, Vec<String>, String) {
    let mode = launch_mode.trim().to_lowercase();
    if mode == "artifact" {
        let artifact = FsPath::new(runtime_artifact);
        if artifact.exists() {
            return (
                artifact.to_string_lossy().to_string(),
                Vec::new(),
                "artifact".to_string(),
            );
        }

        warn!(
            "ANIMA_DAEMON_RUNTIME_LAUNCH_MODE=artifact but ANIMA_DAEMON_RUNTIME_ARTIFACT is not usable. Falling back."
        );
    }

    if mode == "command" {
        if let Some((command, args)) = parse_command_line(runtime_command) {
            return (command, args, "command".to_string());
        }

        warn!("ANIMA_DAEMON_RUNTIME_LAUNCH_MODE=command but ANIMA_DAEMON_RUNTIME_COMMAND is empty. Falling back.");
    }

    if !runtime_artifact.is_empty() {
        let artifact = FsPath::new(runtime_artifact);
        if artifact.exists() {
            let extension = artifact
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or("");
            if matches!(extension, "py" | "pyw") {
                return (
                    env_or("ANIMA_DAEMON_PYTHON", "python"),
                    vec![
                        artifact.to_string_lossy().to_string(),
                        "--host".to_string(),
                        runtime_host.to_string(),
                        "--port".to_string(),
                        runtime_port.to_string(),
                    ],
                    "python".to_string(),
                );
            }

            return (
                artifact.to_string_lossy().to_string(),
                Vec::new(),
                "artifact".to_string(),
            );
        }
    }

    let command = parse_command_line(runtime_command)
        .map(|(cmd, mut args)| {
            let mut normalized = args;
            if !has_arg_value(&normalized, "--host") {
                normalized.push("--host".to_string());
                normalized.push(runtime_host.to_string());
            }
            if !has_arg_value(&normalized, "--port") {
                normalized.push("--port".to_string());
                normalized.push(runtime_port.to_string());
            }
            (cmd, normalized, "python".to_string())
        })
        .unwrap_or_else(|| {
            (
                "uv".to_string(),
                vec![
                    "run".to_string(),
                    "--project".to_string(),
                    "apps/server".to_string(),
                    "uvicorn".to_string(),
                    "anima_server.main:app".to_string(),
                    "--app-dir".to_string(),
                    "apps/server/src".to_string(),
                    "--host".to_string(),
                    runtime_host.to_string(),
                    "--port".to_string(),
                    runtime_port.to_string(),
                ],
                "python".to_string(),
            )
        });

    if mode == "command" {
        return command;
    }

    if command.0 == "uv" {
        warn!(
            "No explicit runtime launcher configured. Defaulting to 'uv run uvicorn ...' with local repository source."
        );
    }
    command
}

fn parse_command_line(value: &str) -> Option<(String, Vec<String>)> {
    let mut parts = value.split_whitespace();
    let command = parts.next()?;
    let args = parts.map(std::string::ToString::to_string).collect();
    Some((command.to_string(), args))
}

fn has_arg_value(args: &[String], flag: &str) -> bool {
    args.iter().any(|arg| arg == flag)
}

fn random_nonce() -> String {
    Uuid::new_v4().simple().to_string()
}

fn env_or(key: &str, fallback: &str) -> String {
    env::var(key).unwrap_or_else(|_| fallback.to_string())
}

fn env_opt(key: &str) -> Option<String> {
    env::var(key).ok().and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn env_bool(key: &str, fallback: bool) -> bool {
    env::var(key)
        .ok()
        .and_then(|value| parse_bool(&value).ok())
        .unwrap_or(fallback)
}

fn parse_bool(input: &str) -> Result<bool, String> {
    match input.to_lowercase().as_str() {
        "1" | "true" | "on" | "yes" | "y" => Ok(true),
        "0" | "false" | "off" | "no" | "n" => Ok(false),
        other => Err(format!("Cannot parse bool from '{other}'")),
    }
}

fn env_parse<T>(key: &str, fallback: T) -> T
where
    T: FromStr,
    T::Err: Display,
{
    let Some(raw) = env_opt(key) else {
        return fallback;
    };

    raw.parse::<T>().unwrap_or_else(|err| {
        warn!("Invalid value for {key}: {raw} ({err}); using default");
        fallback
    })
}

fn default_runtime_working_dir() -> Option<PathBuf> {
    env_opt("ANIMA_DAEMON_RUNTIME_WORKDIR").map(PathBuf::from)
}

fn default_data_dir() -> Option<PathBuf> {
    dirs::data_dir().map(|dir| dir.join("anima").join("runtime-daemon"))
}

fn default_fallback_data_dir() -> PathBuf {
    PathBuf::from(".").join(".anima").join("runtime-daemon")
}

