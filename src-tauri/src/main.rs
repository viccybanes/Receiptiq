#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    error::Error,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

#[cfg(windows)]
use std::os::windows::{io::AsRawHandle, process::CommandExt};
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    },
};

struct Backend {
    child: Child,
    #[cfg(windows)]
    job: HANDLE,
}
// Job handles are owned by this process and accessed only through the mutex or Drop.
#[cfg(windows)]
unsafe impl Send for Backend {}

impl Drop for Backend {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            CloseHandle(self.job);
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn spawn_backend(executable: PathBuf, port: u16, token: &str) -> Result<Backend, Box<dyn Error>> {
    let mut command = Command::new(executable);
    command
        .args(["--port", &port.to_string()])
        .env("RECEIPTIQ_SESSION_TOKEN", token)
        .env("RECEIPTIQ_DESKTOP", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW; no console flashes on user launch.
    let mut child = command.spawn()?;
    #[cfg(windows)]
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            let _ = child.kill();
            return Err(std::io::Error::last_os_error().into());
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        );
        let assigned = AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE);
        if configured == 0 || assigned == 0 {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            let _ = child.kill();
            return Err(error.into());
        }
        Ok(Backend { child, job })
    }
    #[cfg(not(windows))]
    Ok(Backend { child })
}

fn launch_preferences() -> serde_json::Value {
    let Some(local) = std::env::var_os("LOCALAPPDATA") else {
        return serde_json::Value::Null;
    };
    let path = PathBuf::from(local).join("ReceiptIQ-launch.json");
    std::fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or(serde_json::Value::Null)
}

fn selected_workspace() -> PathBuf {
    std::env::var_os("RECEIPTIQ_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            launch_preferences()
                .get("active_workspace")
                .and_then(|value| value.as_str())
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    PathBuf::from(
                        std::env::var_os("LOCALAPPDATA").expect("Windows LocalAppData is required"),
                    )
                    .join("ReceiptIQ")
                })
        })
}

fn healthy(port: u16, token: &str) -> bool {
    let address = format!("127.0.0.1:{port}").parse().unwrap();
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let request = format!("GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.take(65536).read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200")
}

fn license_url_allowed(url: &str) -> bool {
    matches!(url, "https://ai.google.dev/gemma/terms" | "https://aistudio.google.com/apikey" |
        "https://ai.google.dev/gemini-api/terms")
}

fn open_license_in_browser(url: &str) {
    if !license_url_allowed(url) {
        return;
    }
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL};
        let action: Vec<u16> = "open".encode_utf16().chain(Some(0)).collect();
        let address: Vec<u16> = url.encode_utf16().chain(Some(0)).collect();
        ShellExecuteW(
            std::ptr::null_mut(),
            action.as_ptr(),
            address.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            SW_SHOWNORMAL,
        );
    }
}

fn main() {
    let result = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let executable = if cfg!(debug_assertions) {
                std::env::var_os("RECEIPTIQ_BACKEND_PATH").map(PathBuf::from)
                    .unwrap_or(app.path().resource_dir()?.join("backend/receiptiq.exe"))
            } else { app.path().resource_dir()?.join("backend/receiptiq.exe") };
            let listener = TcpListener::bind("127.0.0.1:0")?;
            let port = listener.local_addr()?.port();
            drop(listener);
            let token = format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
            // Inherit only explicit DATA_DIR overrides; Python applies a queued workspace copy before opening Store.
            let moving_workspace = std::env::var_os("RECEIPTIQ_DATA_DIR").is_none() && !launch_preferences()["pending"].is_null();
            let startup_limit = if moving_workspace { 900 } else { 90 };
            let mut backend = spawn_backend(executable, port, &token)?;
            let started = Instant::now();
            while !healthy(port, &token) {
                if backend.child.try_wait()?.is_some() {
                    return Err("ReceiptIQ backend stopped during startup. Close another workspace instance and retry.".into());
                }
                if started.elapsed() > Duration::from_secs(startup_limit) {
                    return Err("ReceiptIQ backend did not start in time. Check storage permissions and free disk, then retry.".into());
                }
                thread::sleep(Duration::from_millis(200));
            }
            let origin = format!("http://127.0.0.1:{port}");
            let allowed_port = port;
            let script = format!("Object.defineProperty(window, '__RECEIPTIQ_TOKEN__', {{value: {}, writable: false}});", serde_json::to_string(&token)?);
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(origin.parse()?))
                .title("ReceiptIQ").inner_size(1360.0, 900.0).min_inner_size(900.0, 640.0)
                .initialization_script(&script)
                .on_navigation(move |url| url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && url.port() == Some(allowed_port))
                .on_new_window(|url, _| {
                    open_license_in_browser(url.as_str());
                    tauri::webview::NewWindowResponse::Deny
                })
                .build()?;
            if let Some(path) = std::env::var_os("RECEIPTIQ_DESKTOP_SMOKE_REPORT") {
                std::fs::write(path, serde_json::to_vec(&serde_json::json!({"desktop_pid": std::process::id(),
                    "backend_pid": backend.child.id(), "port": port, "workspace": selected_workspace()}))?)?;
            }
            app.manage(Mutex::new(Some(backend)));
            Ok(())
        })
        .build(tauri::generate_context!());
    let application = match result {
        Ok(application) => application,
        Err(error) => {
            #[cfg(windows)]
            unsafe {
                use windows_sys::Win32::UI::WindowsAndMessaging::{
                    MessageBoxW, MB_ICONERROR, MB_OK,
                };
                let message: Vec<u16> = format!("ReceiptIQ could not start.\n\n{error}\n\nClose another ReceiptIQ instance, check free disk and workspace permissions, then retry.").encode_utf16().chain(Some(0)).collect();
                let title: Vec<u16> = "ReceiptIQ startup".encode_utf16().chain(Some(0)).collect();
                MessageBoxW(
                    std::ptr::null_mut(),
                    message.as_ptr(),
                    title.as_ptr(),
                    MB_ICONERROR | MB_OK,
                );
            }
            #[cfg(not(windows))]
            eprintln!("ReceiptIQ could not start: {error}");
            return;
        }
    };
    application.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            app.state::<Mutex<Option<Backend>>>().lock().unwrap().take();
        }
    });
}

#[cfg(test)]
mod tests {
    #[test]
    fn license_browser_allowlist_is_exact() {
        assert!(super::license_url_allowed(
            "https://ai.google.dev/gemma/terms"
        ));
        assert!(super::license_url_allowed("https://aistudio.google.com/apikey"));
        assert!(super::license_url_allowed("https://ai.google.dev/gemini-api/terms"));
        for address in [
            "http://ai.google.dev/gemma/terms",
            "https://ai.google.dev.evil/gemma/terms",
            "https://ai.google.dev/gemma/terms?token=private",
            "file:///C:/Windows/System32/cmd.exe",
            "https://ai.google.dev/gemma/terms#unlisted",
            "https://aistudio.google.com/apikey?key=private",
            "https://aistudio.google.com.evil/apikey",
        ] {
            assert!(!super::license_url_allowed(address));
        }
    }
}
