use std::{
    io::{BufRead, BufReader, Write},
    path::Path,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
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
    #[cfg(windows)]
    _job: ProcessJob,
}

impl SessionHost {
    pub fn start(
        executable: &Path,
        work: &Path,
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
        let logs = work.join("logs");
        std::fs::create_dir_all(&logs).map_err(|e| format!("无法准备诊断日志：{e}"))?;
        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(logs.join("desktop-runtime.log"))
            .map_err(|e| format!("无法写入诊断日志：{e}"))?;
        let mut command = Command::new(executable);
        command
            .arg("--work-dir")
            .arg(work)
            .arg("--host-controlled")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(log));
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
            #[cfg(windows)]
            _job: job,
        };
        let (send, receive) = mpsc::channel();
        let stopping = Arc::clone(&host.stopping);
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
                on_failure("桌面运行时已意外退出，请检查工作目录中的 logs/desktop-runtime.log。");
            }
        });
        match receive.recv_timeout(Duration::from_secs(45)) {
            Ok(Ok(info)) => Ok((host, info)),
            Ok(Err(error)) => Err(error),
            Err(_) => Err("桌面会话未能就绪，请检查工作目录中的 logs/desktop-runtime.log。".into()),
        }
    }

    pub fn shutdown(&self) -> Result<(), String> {
        if self.stopping.swap(true, Ordering::SeqCst) {
            return Ok(());
        }
        if let Ok(mut child) = self.child.lock() {
            if let Some(mut input) = child.stdin.take() {
                let _ = input.write_all(b"shutdown\n");
            }
            let deadline = Instant::now() + Duration::from_secs(70);
            while Instant::now() < deadline {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        return if status.success() {
                            Ok(())
                        } else {
                            Err(format!("桌面运行时异常退出：{status}"))
                        }
                    }
                    Err(_) => break,
                    _ => thread::sleep(Duration::from_millis(50)),
                }
            }
            let _ = child.kill();
            let _ = child.wait();
        }
        // Closing the Windows job when this owner drops reaps its process tree.
        Err("桌面运行时未能正常收尾，已终止本地进程。请检查 desktop-runtime.log。".into())
    }
}

impl Drop for SessionHost {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}

#[cfg(windows)]
struct ProcessJob(isize);

#[cfg(windows)]
impl ProcessJob {
    fn attach(child: &Child) -> Result<Self, String> {
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
    if cfg!(debug_assertions) {
        tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?.build()?;
        return Ok(());
    }
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 || args[0] != "--work-dir" {
        return Err(std::io::Error::other(
            "最小桌面测试程序需要 --work-dir <已存在的隔离工作目录>。首次目录选择由后续工单提供。",
        )
        .into());
    }
    let executable = app
        .path()
        .resource_dir()?
        .join("runtime/DouyinCutRuntime.exe");
    let handle = app.handle().clone();
    let (host, info) = SessionHost::start(&executable, Path::new(&args[1]), move |message| {
        show_error(message);
        handle.exit(1);
    })
    .map_err(std::io::Error::other)?;
    let url: tauri::Url = info.url.parse()?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port().is_none()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || info.token.len() < 32
    {
        return Err(std::io::Error::other("运行时返回了无效的本机会话地址。").into());
    }
    let origin = url.origin().ascii_serialization();
    let script = format!(
        "if (window.location.origin === {}) {{ Object.defineProperty(globalThis, '__DOUYINCUT_SESSION_TOKEN__', {{ value: {}, configurable: true }}); }}",
        serde_json::to_string(&origin)?, serde_json::to_string(&info.token)?
    );
    let mut config = app.config().app.windows[0].clone();
    config.url = tauri::WebviewUrl::External(url);
    app.manage(host);
    tauri::WebviewWindowBuilder::from_config(app, &config)?
        .initialization_script(script)
        .on_navigation(move |url| url.origin().ascii_serialization() == origin)
        .build()?;
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
            let (host, info) = SessionHost::start(&executable, &work, move |message| {
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
