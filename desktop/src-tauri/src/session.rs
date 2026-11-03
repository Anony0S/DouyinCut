use std::{
    io::{BufRead, BufReader, Write},
    path::Path,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Condvar, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use tauri::Manager;

pub struct SessionInfo {
    pub url: String,
    pub token: String,
}

pub struct SessionHost {
    child: Arc<Mutex<Child>>,
    stopping: Arc<AtomicBool>,
    shutdown_result: Arc<(Mutex<Option<Result<(), String>>>, Condvar)>,
    #[cfg(windows)]
    _job: ProcessJob,
}

impl SessionHost {
    pub fn start(
        executable: &Path,
        work: &Path,
        workspace_config: Option<&Path>,
        on_failure: impl Fn(&str) + Send + 'static,
    ) -> Result<(Self, SessionInfo), String> {
        if !executable.is_file() {
            return Err(format!("缺少冻结运行时：{}", executable.display()));
        }
        if !work.is_absolute() || !work.is_dir() {
            return Err(format!(
                "测试工作目录必须是已存在的绝对路径：{}",
                work.display()
            ));
        }
        // Diagnostics stay outside the data root until Python preflight passes.
        let logs = workspace_config
            .and_then(Path::parent)
            .unwrap_or(work)
            .join("logs");
        std::fs::create_dir_all(&logs).map_err(|e| format!("无法准备诊断日志：{e}"))?;
        let log_path = logs.join("desktop-runtime.log");
        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|e| format!("无法写入诊断日志：{e}"))?;
        let mut command = Command::new(executable);
        command
            .arg("--work-dir")
            .arg(work)
            .arg("--host-controlled")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(log));
        if let Some(config) = workspace_config {
            command.arg("--workspace-config").arg(config);
            let application = std::env::current_exe().map_err(|e| e.to_string())?;
            command
                .arg("--application-dir")
                .arg(application.parent().ok_or("无法定位程序目录")?);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW; retain private stdio pipes.
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("无法启动冻结运行时：{e}"))?;
        #[cfg(windows)]
        let job = match ProcessJob::attach(&child) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        child
            .stdin
            .as_mut()
            .ok_or("缺少运行时控制管道")?
            .write_all(b"start\n")
            .map_err(|e| format!("无法授权运行时启动：{e}"))?;
        let stdout = child.stdout.take().ok_or("缺少运行时启动管道")?;
        let host = Self {
            child: Arc::new(Mutex::new(child)),
            stopping: Arc::new(AtomicBool::new(false)),
            shutdown_result: Arc::new((Mutex::new(None), Condvar::new())),
            #[cfg(windows)]
            _job: job,
        };
        let (send, receive) = mpsc::channel();
        let stopping = Arc::clone(&host.stopping);
        let failure_log = log_path.clone();
        thread::spawn(move || {
            let mut ready = false;
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let Ok(message) = serde_json::from_str::<serde_json::Value>(&line) else {
                    continue;
                };
                match message["event"].as_str() {
                    Some("ready") if !ready => {
                        let info = message["url"].as_str().zip(message["token"].as_str()).map(
                            |(url, token)| SessionInfo {
                                url: url.to_owned(),
                                token: token.to_owned(),
                            },
                        );
                        if let Some(info) = info {
                            ready = true;
                            if send.send(Ok(info)).is_err() {
                                break;
                            }
                        }
                    }
                    Some("error") => {
                        let message = message["message"]
                            .as_str()
                            .unwrap_or("运行时报告未知错误")
                            .to_owned();
                        if !ready {
                            let _ = send.send(Err(message));
                        } else if !stopping.load(Ordering::SeqCst) {
                            on_failure(&message);
                        }
                        return;
                    }
                    Some("stopped") => break,
                    _ => {}
                }
            }
            if ready && !stopping.load(Ordering::SeqCst) {
                on_failure(&format!(
                    "桌面运行时已意外退出，请检查诊断日志：{}。",
                    failure_log.display()
                ));
            }
        });
        match receive.recv_timeout(Duration::from_secs(45)) {
            Ok(Ok(info)) => Ok((host, info)),
            Ok(Err(error)) => Err(error),
            Err(_) => Err(format!(
                "桌面会话未能就绪，请检查诊断日志：{}。",
                log_path.display()
            )),
        }
    }

    pub fn shutdown(&self) -> Result<(), String> {
        if self.stopping.swap(true, Ordering::SeqCst) {
            let (result, changed) = &*self.shutdown_result;
            let result = result.lock().map_err(|_| "退出状态已中断")?;
            let result = changed
                .wait_while(result, |result| result.is_none())
                .map_err(|_| "退出状态已中断")?;
            return result
                .clone()
                .unwrap_or_else(|| Err("退出状态已中断".into()));
        }
        let result = if let Ok(mut child) = self.child.lock() {
            if let Some(mut input) = child.stdin.take() {
                let _ = input.write_all(b"shutdown\n");
            }
            let deadline = Instant::now() + Duration::from_secs(70);
            let result = loop {
                if Instant::now() >= deadline {
                    break Err(
                        "桌面运行时未能正常收尾，已终止本地进程。请检查 desktop-runtime.log。"
                            .into(),
                    );
                }
                match child.try_wait() {
                    Ok(Some(status)) => {
                        break if status.success() {
                            Ok(())
                        } else {
                            Err(format!("桌面运行时异常退出：{status}"))
                        }
                    }
                    Err(error) => break Err(format!("无法检查桌面运行时退出状态：{error}")),
                    _ => thread::sleep(Duration::from_millis(50)),
                }
            };
            if result.is_err() {
                let _ = child.kill();
                let _ = child.wait();
            }
            result
        } else {
            Err("桌面运行时控制已中断。".into())
        };
        let (shutdown_result, changed) = &*self.shutdown_result;
        if let Ok(mut shutdown_result) = shutdown_result.lock() {
            *shutdown_result = Some(result.clone());
            changed.notify_all();
        }
        result
    }
}

impl Drop for SessionHost {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}

#[cfg(windows)]
pub(crate) struct ProcessJob(isize);

#[cfg(windows)]
impl ProcessJob {
    pub(crate) fn attach(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::{Foundation::CloseHandle, System::JobObjects::*};
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(format!(
                    "无法创建桌面进程组：{}",
                    std::io::Error::last_os_error()
                ));
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let configured = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of_val(&info) as u32,
            );
            if configured == 0 || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0 {
                let error = std::io::Error::last_os_error();
                CloseHandle(handle);
                return Err(format!("无法管理桌面进程组：{error}"));
            }
            Ok(Self(handle as isize))
        }
    }
}

#[cfg(windows)]
impl Drop for ProcessJob {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
        }
    }
}

pub fn show_error(message: &str) {
    #[cfg(windows)]
    {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
        let message: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "DouyinCut 启动或运行失败"
            .encode_utf16()
            .chain(Some(0))
            .collect();
        unsafe {
            MessageBoxW(
                std::ptr::null_mut(),
                message.as_ptr(),
                title.as_ptr(),
                MB_OK | MB_ICONERROR,
            );
        }
    }
    #[cfg(not(windows))]
    eprintln!("{message}");
}

pub fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(all(windows, not(debug_assertions)))]
    create_tray(app)?;
    let activation_app = app.handle().clone();
    app.state::<crate::instance::CurrentUserInstance>()
        .listen_for_activation(move || {
            let restore_app = activation_app.clone();
            let main_thread_app = activation_app.clone();
            let _ = main_thread_app.run_on_main_thread(move || restore_main_window(&restore_app));
        })
        .map_err(std::io::Error::other)?;
    if cfg!(debug_assertions) {
        tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?.build()?;
        return Ok(());
    }
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let config = if args.is_empty() {
        app.path().app_config_dir()?.join("workspace.json")
    } else {
        // Find --workspace-config argument, ignoring any Tauri-injected arguments
        let workspace_config_pos = args.iter().position(|arg| arg == "--workspace-config");
        if let Some(pos) = workspace_config_pos {
            if pos + 1 < args.len() {
                std::path::PathBuf::from(&args[pos + 1])
            } else {
                return Err(std::io::Error::other(
                    "--workspace-config requires a path argument.",
                )
                .into());
            }
        } else {
            app.path().app_config_dir()?.join("workspace.json")
        }
    };
    if !config.is_absolute() {
        return Err(std::io::Error::other("定位配置必须为绝对路径").into());
    }
    let resource_dir = app.path().resource_dir()?;
    let executable = resource_dir.join("runtime/DouyinCutRuntime.exe");
    if !executable.is_file() {
        return Err(std::io::Error::other(format!(
            "缺少冻结运行时。resource_dir: {}, 期望: {}",
            resource_dir.display(),
            executable.display()
        ))
        .into());
    }
    app.manage(crate::workspace::WorkDirectoryHost::new(executable, config.clone()));
    let mut window_config = app.config().app.windows[0].clone();
    // If workspace.json exists and is valid, skip setup and go straight to main
    let initial_url = if config.is_file() {
        if let Ok(content) = std::fs::read_to_string(&config) {
            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&content) {
                if parsed.get("work_directory").and_then(|v| v.as_str()).is_some() {
                    tauri::WebviewUrl::App("index.html".into())
                } else {
                    tauri::WebviewUrl::App("setup.html".into())
                }
            } else {
                tauri::WebviewUrl::App("setup.html".into())
            }
        } else {
            tauri::WebviewUrl::App("setup.html".into())
        }
    } else {
        tauri::WebviewUrl::App("setup.html".into())
    };
    window_config.url = initial_url;
    tauri::WebviewWindowBuilder::from_config(app, &window_config)?.build()?;
    Ok(())
}

#[cfg(all(windows, not(debug_assertions)))]
static TRAY_CLOSE_NOTICE_SHOWN: AtomicBool = AtomicBool::new(false);

#[cfg(all(windows, not(debug_assertions)))]
pub fn handle_window_event(window: &tauri::Window, event: &tauri::WindowEvent) {
    if !matches!(window.label(), "main" | "workbench") {
        return;
    }
    let tauri::WindowEvent::CloseRequested { api, .. } = event else {
        return;
    };
    api.prevent_close();
    let _ = window.hide();
    if !TRAY_CLOSE_NOTICE_SHOWN.swap(true, Ordering::SeqCst) {
        show_tray_notice();
    }
}

#[cfg(any(not(windows), debug_assertions))]
pub fn handle_window_event(_: &tauri::Window, _: &tauri::WindowEvent) {}

#[cfg(all(windows, not(debug_assertions)))]
fn create_tray(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::{
        menu::{Menu, MenuItem, PredefinedMenuItem},
        tray::TrayIconBuilder,
    };

    let open = MenuItem::with_id(app, "open", "打开 DouyinCut", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let exit = MenuItem::with_id(app, "exit", "退出 DouyinCut", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &separator, &exit])?;
    let icon = tauri::image::Image::from_app_icon_resource(32)?;

    let _tray = TrayIconBuilder::with_id("douyincut-tray")
        .icon(icon)
        .tooltip("DouyinCut 正在后台运行")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => restore_main_window(app),
            "exit" => request_exit(app),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

#[cfg(all(windows, not(debug_assertions)))]
fn request_exit(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("workbench") {
        restore_main_window(app);
        let _ = window.eval("window.dispatchEvent(new Event('douyincut-exit-request'))");
    } else {
        app.exit(0);
    }
}

#[tauri::command]
pub fn complete_exit(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    let url = window.url().map_err(|error| error.to_string())?;
    if window.label() != "workbench"
        || url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port().is_none()
    {
        return Err("退出操作仅允许从本机工作台执行。".into());
    }
    if app.try_state::<SessionHost>().is_none() {
        return Err("当前桌面会话尚未启动。".into());
    }
    thread::spawn(move || {
        let result = app.state::<SessionHost>().shutdown();
        if let Err(message) = result {
            show_error(&message);
            app.exit(1);
        } else {
            app.exit(0);
        }
    });
    Ok(())
}

fn restore_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app
        .get_webview_window("workbench")
        .or_else(|| app.get_webview_window("main"))
    {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

#[cfg(all(windows, not(debug_assertions)))]
fn show_tray_notice() {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, MB_ICONINFORMATION, MB_OK, MB_SETFOREGROUND, MB_TOPMOST,
    };
    let message: Vec<u16> = "关闭窗口只会将 DouyinCut 收起到系统托盘；已启动的后台工作会继续运行。单击或右键托盘图标，在菜单中选择“打开 DouyinCut”或“退出 DouyinCut”。图标也可能位于任务栏的隐藏图标列表中。尚未选择工作数据目录时，后台服务不会启动。"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let title: Vec<u16> = "DouyinCut 正在后台运行"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            message.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST,
        );
    }
}

pub fn open_workbench(
    app: &tauri::AppHandle,
    executable: &Path,
    work: &Path,
    workspace_config: Option<&Path>,
) -> Result<(), String> {
    let handle = app.clone();
    let (host, info) = SessionHost::start(executable, work, workspace_config, move |message| {
        show_error(message);
        handle.exit(1);
    })?;
    let url: tauri::Url = info.url.parse().map_err(|e| format!("无效会话地址：{e}"))?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port().is_none()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || info.token.len() < 32
    {
        return Err("运行时返回了无效的本机会话地址。".into());
    }
    let origin = url.origin().ascii_serialization();
    let script = format!(
        "if (window.location.origin === {}) {{ Object.defineProperty(globalThis, '__DOUYINCUT_SESSION_TOKEN__', {{ value: {}, configurable: true }}); }}",
        serde_json::to_string(&origin).map_err(|e| e.to_string())?, serde_json::to_string(&info.token).map_err(|e| e.to_string())?
    );
    let mut config = app.config().app.windows[0].clone();
    config.url = tauri::WebviewUrl::External(url);
    config.label = "workbench".into();
    tauri::WebviewWindowBuilder::from_config(app, &config)
        .map_err(|e| e.to_string())?
        .initialization_script(script)
        .on_navigation(move |url| url.origin().ascii_serialization() == origin)
        .build()
        .map_err(|e| e.to_string())?;
    app.manage(host);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::SessionHost;
    use std::{
        fs,
        io::{Read, Write},
        net::TcpStream,
        path::Path,
        time::{Duration, SystemTime, UNIX_EPOCH},
    };

    #[test]
    fn frozen_session_reads_authenticated_projects_and_exits_gracefully() {
        let executable = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("runtime/DouyinCutRuntime/DouyinCutRuntime.exe");
        assert!(
            executable.is_file(),
            "Build the frozen runtime before this native integration check"
        );
        let work = std::env::temp_dir().join(format!(
            "DouyinCut native 会话 {}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&work).unwrap();
        let (failure_send, failure_receive) = std::sync::mpsc::channel();
        let result = (|| {
            let (host, info) = SessionHost::start(&executable, &work, None, move |message| {
                let _ = failure_send.send(message.to_owned());
            })
            .expect("Native owner must start the real frozen session");
            let url: tauri::Url = info.url.parse().unwrap();
            let address = format!("127.0.0.1:{}", url.port().unwrap());
            let mut connection = TcpStream::connect(&address).unwrap();
            connection
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            write!(connection, "GET /api/v1/projects HTTP/1.1\r\nHost: {address}\r\nX-Session-Token: {}\r\nConnection: close\r\n\r\n", info.token).unwrap();
            let mut response = String::new();
            connection.read_to_string(&mut response).unwrap();
            assert!(response.starts_with("HTTP/1.1 200"), "{response}");
            assert!(response.contains("\"items\":[]"), "{response}");
            drop(connection);
            host.shutdown()
                .expect("Idle native shutdown must receive a successful runtime exit");
            assert!(
                TcpStream::connect(&address).is_err(),
                "Owned API must no longer accept connections"
            );
            assert!(
                failure_receive.try_recv().is_err(),
                "Graceful shutdown must not report a runtime failure"
            );
        })();
        fs::remove_dir_all(&work).unwrap();
        result
    }
}
