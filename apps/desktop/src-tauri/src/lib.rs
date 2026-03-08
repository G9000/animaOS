use std::{
    fs::create_dir_all,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::Path,
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, State,
};

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[derive(Default)]
struct ApiProcessState {
    child: Mutex<Option<Child>>,
}

fn api_binary_name() -> &'static str {
    if cfg!(target_os = "windows") {
        "anima-api.exe"
    } else {
        "anima-api"
    }
}

fn ensure_customer_data_layout(data_dir: &Path) -> Result<(), String> {
    let users_dir = data_dir.join("users");

    create_dir_all(data_dir).map_err(|err| format!("failed creating data dir: {err}"))?;
    create_dir_all(&users_dir).map_err(|err| format!("failed creating users dir: {err}"))?;

    Ok(())
}

fn api_healthcheck() -> bool {
    let addr: SocketAddr = "127.0.0.1:3031".parse().expect("valid API socket address");
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(300)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(300)));

    let request = b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }

    response.contains("\"healthy\"")
}

fn wait_for_api_ready(timeout: Duration) -> Result<(), String> {
    let started_at = Instant::now();
    while started_at.elapsed() < timeout {
        if api_healthcheck() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err("timed out waiting for local API health check".to_string())
}

fn start_api_sidecar(app: &tauri::AppHandle) -> Result<Child, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|err| format!("failed resolving resource dir: {err}"))?;
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|err| format!("failed resolving app data dir: {err}"))?;

    let sidecar_path = resource_dir.join("bin").join(api_binary_name());
    let prompts_dir = resource_dir.join("prompts");
    let migrations_dir = resource_dir.join("drizzle");

    if !sidecar_path.exists() {
        return Err(format!(
            "API sidecar binary not found at {}",
            sidecar_path.display()
        ));
    }
    if !prompts_dir.exists() {
        return Err(format!("prompts dir missing at {}", prompts_dir.display()));
    }
    if !migrations_dir.exists() {
        return Err(format!(
            "migrations dir missing at {}",
            migrations_dir.display()
        ));
    }

    ensure_customer_data_layout(&data_dir)?;

    Command::new(&sidecar_path)
        .env("ANIMA_DATA_DIR", &data_dir)
        .env("ANIMA_PROMPTS_DIR", &prompts_dir)
        .env("ANIMA_MIGRATIONS_DIR", &migrations_dir)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| {
            format!(
                "failed starting API sidecar {}: {err}",
                sidecar_path.display()
            )
        })
}

fn stop_api_sidecar(state: &ApiProcessState) {
    if let Ok(mut child_guard) = state.child.lock() {
        if let Some(mut child) = child_guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(ApiProcessState::default())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            if !cfg!(debug_assertions) {
                let mut child = start_api_sidecar(&app.handle())
                    .map_err(|err| -> Box<dyn std::error::Error> { err.into() })?;

                if let Err(err) = wait_for_api_ready(Duration::from_secs(10)) {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(err.into());
                }

                let state: State<ApiProcessState> = app.state();
                let mut guard = state
                    .child
                    .lock()
                    .map_err(|_| "failed to lock API process state".to_string())?;
                *guard = Some(child);
            }

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
        .invoke_handler(tauri::generate_handler![greet])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app, event| {
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            let state: State<ApiProcessState> = app.state();
            stop_api_sidecar(&state);
        }
    });
}
