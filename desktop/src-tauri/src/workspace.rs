use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::Manager;

pub struct WorkDirectoryHost {
    pub executable: PathBuf,
    pub config: PathBuf,
    operation: Mutex<()>,
}

impl WorkDirectoryHost {
    pub fn new(executable: PathBuf, config: PathBuf) -> Self {
        Self { executable, config, operation: Mutex::new(()) }
    }

    pub fn query(&self, action: &str, selected: Option<&Path>) -> Result<serde_json::Value, String> {
        let mut command = Command::new(&self.executable);
        command.args(["--host-controlled", "--workspace-action", action, "--workspace-config"])
            .arg(&self.config).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let application = std::env::current_exe().map_err(|e| e.to_string())?;
        command.arg("--application-dir").arg(application.parent().ok_or("无法定位程序目录")?);
        if let Some(path) = selected { command.arg("--work-dir").arg(path); }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().map_err(|e| format!("无法验证工作目录：{e}"))?;
        #[cfg(windows)]
        let _job = match crate::session::ProcessJob::attach(&child) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        // Python performs no workspace writes until the native Job owns it.
        if let Err(error) = child.stdin.take().ok_or("缺少目录验证控制管道")?
            .write_all(b"start\n")
        {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("无法授权工作目录验证：{error}"));
        }
        // Drain both pipes while the process runs so diagnostics cannot deadlock it.
        let mut out = child.stdout.take().ok_or("缺少目录验证输出管道")?;
        let mut err = child.stderr.take().ok_or("缺少目录验证诊断管道")?;
        let output = thread::spawn(move || { let mut bytes = Vec::new(); out.read_to_end(&mut bytes).map(|_| bytes) });
        let errors = thread::spawn(move || { let mut bytes = Vec::new(); err.read_to_end(&mut bytes).map(|_| bytes) });
        let deadline = Instant::now() + Duration::from_secs(30);
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Ok(status),
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break Err("工作目录验证未能完成，请检查存储设备和权限后重试。".to_owned());
                }
            }
        };
        let stdout = output.join().map_err(|_| "目录验证输出线程失败")?.map_err(|e| e.to_string())?;
        let stderr = errors.join().map_err(|_| "目录验证诊断线程失败")?.map_err(|e| e.to_string())?;
        if !status?.success() {
            return Err(format!("工作目录验证失败：{}", String::from_utf8_lossy(&stderr)));
        }
        serde_json::from_slice(&stdout).map_err(|e| format!("无法读取工作目录验证结果：{e}"))
    }
}

#[tauri::command]
pub async fn workspace_action(window: tauri::WebviewWindow, action: String) -> Result<serde_json::Value, String> {
    let url = window.url().map_err(|e| e.to_string())?;
    let bundled = matches!((url.scheme(), url.host_str()),
        ("tauri", Some("localhost")) | ("http" | "https", Some("tauri.localhost")));
    if window.label() != "main" || !bundled
        || url.path() != "/setup.html"
    {
        return Err("工作目录操作仅允许从原生初始化页面执行。".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let app = window.app_handle();
        let host = app.state::<WorkDirectoryHost>();
        let _operation = host.operation.lock().map_err(|_| "工作目录操作已中断，请重新打开应用。")?;
        if app.try_state::<crate::session::SessionHost>().is_some() {
            return Err("工作台已启动，不提供工作区切换。".into());
        }
        match action.as_str() {
            "inspect" => host.query("inspect", None),
            "select" => {
                let selected = pick_directory()?;
                match selected {
                    Some(path) => host.query("select", Some(&path)),
                    None => {
                        let mut state = host.query("inspect", None)?;
                        state["message"] = "已取消选择。尚未启动后台服务。".into();
                        Ok(state)
                    }
                }
            }
            "open" => {
                let state = host.query("inspect", None)?;
                if state["state"] != "ready" { return Ok(state); }
                let work = state["path"].as_str().ok_or("缺少工作目录")?;
                crate::session::open_workbench(app, &host.executable, Path::new(work), Some(&host.config))?;
                window.destroy().map_err(|e| e.to_string())?;
                Ok(serde_json::json!({"state":"opened","path":work,"message":""}))
            }
            _ => Err("未知工作目录操作。".into()),
        }
    }).await.map_err(|e| e.to_string())?
}

#[cfg(windows)]
fn pick_directory() -> Result<Option<PathBuf>, String> {
    use windows_sys::Win32::{System::Com::*, UI::Shell::*};
    unsafe {
        let initialized = CoInitializeEx(std::ptr::null(), COINIT_APARTMENTTHREADED as u32);
        if initialized < 0 { return Err("无法打开 Windows 目录选择器。".into()); }
        let title: Vec<u16> = "选择空目录或原位置的 DouyinCut 工作数据目录".encode_utf16().chain(Some(0)).collect();
        let info = BROWSEINFOW {
            lpszTitle: title.as_ptr(),
            ulFlags: BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX,
            ..std::mem::zeroed()
        };
        let item = SHBrowseForFolderW(&info);
        let result = if item.is_null() { Ok(None) } else {
            let mut path = vec![0u16; 32768];
            let success = SHGetPathFromIDListEx(item, path.as_mut_ptr(), path.len() as u32, GPFIDL_DEFAULT);
            CoTaskMemFree(item as _);
            if success == 0 { Err("所选位置不是可用的本地文件目录。".into()) } else {
                use std::os::windows::ffi::OsStringExt;
                let length = path.iter().position(|c| *c == 0).unwrap_or(path.len());
                Ok(Some(PathBuf::from(std::ffi::OsString::from_wide(&path[..length]))))
            }
        };
        CoUninitialize();
        result
    }
}

#[cfg(not(windows))]
fn pick_directory() -> Result<Option<PathBuf>, String> {
    Err("目录选择仅支持 Windows。".into())
}
