//! Explicit, user-initiated updates for our Inno Setup distribution.
//! Trust is HTTPS + assets from the fixed GitHub repository, with SHA-256
//! integrity checks. This is not Tauri's signed NSIS/MSI updater protocol.
use reqwest::{blocking::Client, redirect::Policy, StatusCode, Url};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{atomic::{AtomicBool, Ordering}, Mutex},
    time::Duration,
};
use tauri::Manager;

const RELEASE_API: &str = "https://api.github.com/repos/Anony0S/DouyinCut/releases/latest";
const DOWNLOAD_ROOT: &str = "https://github.com/Anony0S/DouyinCut/releases/download/";
const MAX_INSTALLER_SIZE: u64 = 2 * 1024 * 1024 * 1024;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateStatus {
    phase: String,
    current_version: String,
    latest_version: Option<String>,
    notes: String,
    has_update: bool,
    downloaded_bytes: u64,
    total_bytes: u64,
    message: String,
}

impl Default for UpdateStatus {
    fn default() -> Self {
        Self {
            phase: "idle".into(), current_version: env!("DOUYINCUT_VERSION").into(),
            latest_version: None, notes: String::new(), has_update: false,
            downloaded_bytes: 0, total_bytes: 0, message: String::new(),
        }
    }
}

#[derive(Clone)]
struct Candidate {
    url: String,
    checksum_url: String,
    filename: String,
    size: u64,
}

#[derive(Default)]
struct UpdateState {
    status: UpdateStatus,
    candidate: Option<Candidate>,
    verified: Option<(PathBuf, String, u64)>,
}

#[derive(Default)]
pub struct UpdateHost {
    state: Mutex<UpdateState>,
    cancelled: AtomicBool,
}

impl UpdateHost {
    fn change(&self, change: impl FnOnce(&mut UpdateState)) {
        if let Ok(mut state) = self.state.lock() { change(&mut state); }
    }

    fn fail(&self, message: String) {
        self.change(|state| {
            state.status.phase = "error".into();
            state.status.message = message;
        });
    }
}

#[derive(Deserialize)]
struct Release {
    tag_name: String,
    draft: bool,
    prerelease: bool,
    body: Option<String>,
    assets: Vec<Asset>,
}

#[derive(Deserialize)]
struct Asset { name: String, browser_download_url: String, size: u64 }

fn trusted_url(url: &Url) -> bool {
    url.scheme() == "https" && url.port_or_known_default() == Some(443)
        && url.username().is_empty() && url.password().is_none()
        && matches!(url.host_str(), Some("api.github.com" | "github.com" |
            "release-assets.githubusercontent.com" | "objects.githubusercontent.com"))
}

fn redirects() -> Policy {
    Policy::custom(|attempt| {
        if attempt.previous().len() >= 5 || !trusted_url(attempt.url()) {
            attempt.error("更新下载重定向到了不受信任的地址")
        } else { attempt.follow() }
    })
}

fn client(timeout: Duration) -> Result<Client, String> {
    Client::builder()
        .user_agent(concat!("DouyinCut/", env!("DOUYINCUT_VERSION")))
        .connect_timeout(Duration::from_secs(10))
        .timeout(timeout)
        .redirect(redirects())
        .build().map_err(|error| format!("无法建立更新连接：{error}"))
}

fn read_limited(response: reqwest::blocking::Response, max: u64) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    response.take(max + 1).read_to_end(&mut bytes).map_err(|e| format!("读取更新响应失败：{e}"))?;
    if bytes.len() as u64 > max { return Err("更新响应过大".into()); }
    Ok(bytes)
}

fn release_candidate(release: Release, current: &str) -> Result<(UpdateStatus, Option<Candidate>), String> {
    if release.draft || release.prerelease { return Err("更新源未返回正式发行版".into()); }
    let version = release.tag_name.strip_prefix('v').unwrap_or(&release.tag_name);
    let latest = semver::Version::parse(version).map_err(|_| "Release 标签必须为 v加三段式版本号，例如 v0.2.137")?;
    if !latest.pre.is_empty() || !latest.build.is_empty() {
        return Err("正式版标签不能包含预发布或构建后缀".into());
    }
    let current = semver::Version::parse(current).map_err(|_| "当前应用版本无效")?;
    let mut status = UpdateStatus {
        latest_version: Some(version.into()),
        notes: release.body.unwrap_or_default().chars().take(16000).collect(),
        ..UpdateStatus::default()
    };
    if latest <= current {
        status.message = format!("当前版本 {} 已是最新版本", current);
        return Ok((status, None));
    }
    let filename = format!("DouyinCut-{version}-setup.exe");
    let checksum_name = format!("{filename}.sha256");
    let find_asset = |name: &str| -> Result<&Asset, String> {
        let matches: Vec<_> = release.assets.iter().filter(|a| a.name == name).collect();
        if matches.len() != 1 { return Err(format!("该版本缺少唯一的发布附件 {name}，请联系发布者")); }
        let asset = matches[0];
        // Reject foreign repositories, arbitrary executable URLs and query strings.
        let expected = format!("{DOWNLOAD_ROOT}{}/{name}", release.tag_name);
        if asset.browser_download_url != expected { return Err("发布附件地址不属于指定仓库和版本".into()); }
        Ok(asset)
    };
    let installer = find_asset(&filename)?;
    let checksum = find_asset(&checksum_name)?;
    if installer.size == 0 || installer.size > MAX_INSTALLER_SIZE || checksum.size == 0 || checksum.size > 1024 {
        return Err("发布附件大小无效".into());
    }
    status.phase = "available".into();
    status.has_update = true;
    status.total_bytes = installer.size;
    status.message = format!("发现新版本 {version}");
    let candidate = Candidate { url: installer.browser_download_url.clone(),
        checksum_url: checksum.browser_download_url.clone(), filename, size: installer.size };
    Ok((status, Some(candidate)))
}

fn check_release() -> Result<(UpdateStatus, Option<Candidate>), String> {
    let response = client(Duration::from_secs(30))?.get(RELEASE_API)
        .header("Accept", "application/vnd.github+json")
        .send().map_err(|e| format!("无法连接 GitHub，请检查网络后重试：{e}"))?;
    match response.status() {
        StatusCode::NOT_FOUND => return Ok((UpdateStatus { message: "仓库尚未发布正式版本".into(), ..UpdateStatus::default() }, None)),
        StatusCode::FORBIDDEN | StatusCode::TOO_MANY_REQUESTS => return Err("GitHub 暂时限制了请求，请稍后再试".into()),
        status if !status.is_success() => return Err(format!("检查更新失败：GitHub HTTP {status}")),
        _ => {}
    }
    let release: Release = serde_json::from_slice(&read_limited(response, 1024 * 1024)?)
        .map_err(|e| format!("Release 数据无效：{e}"))?;
    release_candidate(release, env!("DOUYINCUT_VERSION"))
}

fn ensure_available(state: &UpdateState) -> Result<(), String> {
    if matches!(state.status.phase.as_str(), "checking" | "downloading" | "installing") {
        Err("已有更新操作正在进行".into())
    } else { Ok(()) }
}

#[tauri::command]
pub fn get_update_status(host: tauri::State<'_, UpdateHost>) -> Result<UpdateStatus, String> {
    Ok(host.state.lock().map_err(|_| "更新状态不可用")?.status.clone())
}

#[tauri::command]
pub async fn check_for_update(app: tauri::AppHandle) -> Result<UpdateStatus, String> {
    let host = app.state::<UpdateHost>();
    {
        let mut state = host.state.lock().map_err(|_| "更新状态不可用")?;
        ensure_available(&state)?;
        state.status.phase = "checking".into();
        state.status.message = "正在检查 GitHub Releases…".into();
    }
    let result = tauri::async_runtime::spawn_blocking(check_release).await.map_err(|e| e.to_string())
        .and_then(|result| result);
    match result {
        Ok((status, candidate)) => {
            host.change(|state| { state.status = status.clone(); state.candidate = candidate; state.verified = None; });
            Ok(status)
        }
        Err(message) => { host.fail(message.clone()); Err(message) }
    }
}

fn parse_checksum(bytes: &[u8], filename: &str) -> Result<String, String> {
    let text = std::str::from_utf8(bytes).map_err(|_| "校验文件不是 UTF-8 文本")?;
    let parts: Vec<_> = text.split_whitespace().collect();
    if parts.len() != 2 || parts[1].trim_start_matches('*') != filename ||
        parts[0].len() != 64 || !parts[0].bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("SHA-256 校验文件格式或文件名不匹配".into());
    }
    Ok(parts[0].to_ascii_lowercase())
}

fn copy_verified(
    source: &mut impl Read, target: &mut impl Write, size: u64, expected: &str,
    cancelled: &AtomicBool, mut progress: impl FnMut(u64),
) -> Result<(), String> {
    let mut hasher = Sha256::new();
    let mut received = 0;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        if cancelled.load(Ordering::SeqCst) { return Err("下载已取消".into()); }
        let count = source.read(&mut buffer).map_err(|e| format!("下载安装包失败：{e}"))?;
        if count == 0 { break; }
        received += count as u64;
        if received > size { return Err("安装包超出声明的大小".into()); }
        hasher.update(&buffer[..count]);
        target.write_all(&buffer[..count]).map_err(|e| format!("保存安装包失败：{e}"))?;
        progress(received);
    }
    if received != size { return Err("安装包不完整，请重新下载".into()); }
    if format!("{:x}", hasher.finalize()) != expected { return Err("安装包 SHA-256 校验失败，请重新下载".into()); }
    if cancelled.load(Ordering::SeqCst) { return Err("下载已取消".into()); }
    Ok(())
}

// Dropping the in-flight HTTP future interrupts a stalled connection as well as
// a flowing transfer. Cancellation never leaves a worker writing into a retry.
async fn cancellable<T>(cancelled: &AtomicBool, operation: impl std::future::Future<Output = Result<T, reqwest::Error>>) -> Result<T, String> {
    tokio::pin!(operation);
    loop {
        if cancelled.load(Ordering::SeqCst) { return Err("下载已取消".into()); }
        tokio::select! {
            result = &mut operation => return result.map_err(|e| format!("更新下载连接失败：{}", e.without_url())),
            _ = tokio::time::sleep(Duration::from_millis(100)) => {}
        }
    }
}

async fn download(host: &UpdateHost, candidate: &Candidate, cache: &Path) -> Result<(), String> {
    let http = reqwest::Client::builder()
        .user_agent(concat!("DouyinCut/", env!("DOUYINCUT_VERSION")))
        .connect_timeout(Duration::from_secs(10))
        .read_timeout(Duration::from_secs(30))
        .timeout(Duration::from_secs(15 * 60)).redirect(redirects())
        .build().map_err(|e| e.to_string())?;
    let mut response = cancellable(&host.cancelled, http.get(&candidate.checksum_url).send()).await?
        .error_for_status().map_err(|e| format!("获取校验文件失败：{}", e.without_url()))?;
    let mut checksum_bytes = Vec::new();
    while let Some(chunk) = cancellable(&host.cancelled, response.chunk()).await? {
        if checksum_bytes.len() + chunk.len() > 1024 { return Err("校验文件过大".into()); }
        checksum_bytes.extend_from_slice(&chunk);
    }
    let checksum = parse_checksum(&checksum_bytes, &candidate.filename)?;
    fs::create_dir_all(cache).map_err(|e| format!("创建更新缓存失败：{e}"))?;
    let partial = cache.join("setup.exe.part");
    let target = cache.join("setup.exe");
    // This directory is exclusively owned by the updater; never touch work data.
    let result = async {
        let mut response = cancellable(&host.cancelled, http.get(&candidate.url).send()).await?
            .error_for_status().map_err(|e| format!("下载安装包失败：{}", e.without_url()))?;
        if response.content_length().is_some_and(|length| length != candidate.size) {
            return Err("安装包长度与 Release 声明不一致".into());
        }
        let mut file = File::create(&partial).map_err(|e| format!("无法写入更新缓存：{e}"))?;
        let mut hasher = Sha256::new();
        let mut received = 0;
        while let Some(chunk) = cancellable(&host.cancelled, response.chunk()).await? {
            received += chunk.len() as u64;
            if received > candidate.size { return Err("安装包超出声明的大小".into()); }
            hasher.update(&chunk);
            file.write_all(&chunk).map_err(|e| format!("保存安装包失败：{e}"))?;
            host.change(|state| state.status.downloaded_bytes = received);
        }
        if received != candidate.size { return Err("安装包不完整，请重新下载".into()); }
        if format!("{:x}", hasher.finalize()) != checksum { return Err("安装包 SHA-256 校验失败，请重新下载".into()); }
        if host.cancelled.load(Ordering::SeqCst) { return Err("下载已取消".into()); }
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        if target.exists() { fs::remove_file(&target).map_err(|e| e.to_string())?; }
        fs::rename(&partial, &target).map_err(|e| e.to_string())?;
        host.change(|state| {
            state.verified = Some((target, checksum, candidate.size));
            state.status.phase = "ready".into();
            state.status.message = "安装包已下载并校验，可退出应用并安装".into();
        });
        Ok(())
    }.await;
    if result.is_err() { let _ = fs::remove_file(&partial); }
    result
}

#[tauri::command]
pub fn download_update(app: tauri::AppHandle) -> Result<(), String> {
    if !cfg!(all(windows, target_arch = "x86_64")) { return Err("在线更新仅支持 Windows x64".into()); }
    let cache = app.path().app_cache_dir().map_err(|e| e.to_string())?.join("updates");
    let host = app.state::<UpdateHost>();
    let candidate = {
        let mut state = host.state.lock().map_err(|_| "更新状态不可用")?;
        ensure_available(&state)?;
        let candidate = state.candidate.clone().ok_or("请先检查更新")?;
        host.cancelled.store(false, Ordering::SeqCst);
        state.verified = None;
        state.status.phase = "downloading".into();
        state.status.downloaded_bytes = 0;
        state.status.message = "正在下载安装包…".into();
        candidate
    };
    tauri::async_runtime::spawn(async move {
        let host = app.state::<UpdateHost>();
        if let Err(message) = download(&host, &candidate, &cache).await { host.fail(message); }
    });
    Ok(())
}

#[tauri::command]
pub fn cancel_update_download(host: tauri::State<'_, UpdateHost>) {
    host.cancelled.store(true, Ordering::SeqCst);
}

#[tauri::command]
pub async fn install_update(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    let url = window.url().map_err(|e| e.to_string())?;
    if cfg!(debug_assertions) || window.label() != "workbench" || url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1") || app.try_state::<crate::session::SessionHost>().is_none() {
        return Err("请在已安装的 Windows 应用中执行安装更新；开发模式只支持检查和下载".into());
    }
    let (path, checksum, size) = {
        let host = app.state::<UpdateHost>();
        let mut state = host.state.lock().map_err(|_| "更新状态不可用")?;
        if state.status.phase != "ready" { return Err("请先下载并校验安装包".into()); }
        let verified = state.verified.clone().ok_or("安装包尚未校验")?;
        state.status.phase = "installing".into();
        verified
    };
    let result = tauri::async_runtime::spawn_blocking({
        let app = app.clone();
        move || -> Result<(), String> {
            // Verify once more before execution, including after a cancelled exit prompt.
            let validation = (|| {
                let mut file = File::open(&path).map_err(|e| format!("无法读取安装包：{e}"))?;
                copy_verified(&mut file, &mut std::io::sink(), size, &checksum, &AtomicBool::new(false), |_| {})
            })();
            if let Err(message) = validation {
                app.state::<UpdateHost>().change(|state| state.verified = None);
                return Err(message);
            }
            let application = std::env::current_exe().map_err(|e| e.to_string())?;
            let install_dir = application.parent().ok_or("无法定位安装目录")?;
            // Launch first: if Windows refuses the installer, keep the runtime usable.
            // Inno Setup waits for this PID before showing the wizard or writing files.
            let mut installer = std::process::Command::new(&path)
                .arg(format!("/UPDATEPID={}", std::process::id()))
                .arg(format!("/DIR={}", install_dir.display()))
                .args(["/SP-", "/NORESTART", "/CLOSEAPPLICATIONS=NO"])
                .spawn().map_err(|e| format!("无法启动安装程序：{e}"))?;
            if let Err(message) = app.state::<crate::session::SessionHost>().shutdown() {
                let _ = installer.kill();
                let _ = installer.wait();
                crate::session::show_error(&format!("已取消安装更新：{message}"));
                app.exit(1);
                return Err(message);
            }
            app.exit(0);
            Ok(())
        }
    }).await.map_err(|e| e.to_string()).and_then(|result| result);
    if let Err(message) = &result {
        app.state::<UpdateHost>().change(|state| {
            // A launch failure is retryable. A changed/missing package must be downloaded again.
            state.status.phase = if state.verified.is_some() { "ready" } else { "error" }.into();
            state.status.message = message.clone();
        });
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn release(version: &str) -> Release {
        let filename = format!("DouyinCut-{version}-setup.exe");
        Release { tag_name: format!("v{version}"), draft: false, prerelease: false, body: Some("更新说明".into()),
            assets: [filename.clone(), format!("{filename}.sha256")].into_iter().map(|name| Asset {
                browser_download_url: format!("{DOWNLOAD_ROOT}v{version}/{name}"), name, size: 12,
            }).collect() }
    }

    #[test]
    fn semantic_versions_and_stable_releases_only() {
        assert!(release_candidate(release("0.2.140"), "0.2.99").unwrap().0.has_update);
        assert!(!release_candidate(release("0.2.99"), "0.2.140").unwrap().0.has_update);
        assert!(!release_candidate(release("0.2.140"), "0.2.140").unwrap().0.has_update);
        for version in ["0.3.0-beta.1", "0.3.0+build", "not-a-version"] {
            assert!(release_candidate(release(version), "0.2.140").is_err());
        }
        let mut preview = release("0.3.0"); preview.prerelease = true;
        assert!(release_candidate(preview, "0.2.140").is_err());
    }

    #[test]
    fn rejects_missing_foreign_duplicate_and_oversized_assets() {
        let mut missing = release("0.3.0"); missing.assets.pop();
        assert!(release_candidate(missing, "0.2.140").is_err());
        let mut foreign = release("0.3.0"); foreign.assets[0].browser_download_url = "https://evil.example/setup.exe".into();
        assert!(release_candidate(foreign, "0.2.140").is_err());
        let mut duplicate = release("0.3.0"); duplicate.assets.push(Asset {
            name: duplicate.assets[0].name.clone(), browser_download_url: duplicate.assets[0].browser_download_url.clone(), size: 12,
        });
        assert!(release_candidate(duplicate, "0.2.140").is_err());
        let mut large = release("0.3.0"); large.assets[0].size = MAX_INSTALLER_SIZE + 1;
        assert!(release_candidate(large, "0.2.140").is_err());
        assert!(!trusted_url(&Url::parse("http://github.com/file").unwrap()));
        assert!(!trusted_url(&Url::parse("https://github.com.evil.example/file").unwrap()));
        assert!(trusted_url(&Url::parse("https://release-assets.githubusercontent.com/file").unwrap()));
    }

    #[test]
    fn checksum_is_bound_to_exact_filename() {
        let hash = "a".repeat(64);
        assert_eq!(parse_checksum(format!("{hash}  app.exe\n").as_bytes(), "app.exe").unwrap(), hash);
        assert!(parse_checksum(format!("{hash}  other.exe").as_bytes(), "app.exe").is_err());
        assert!(parse_checksum(b"bad app.exe", "app.exe").is_err());
    }

    #[test]
    fn validates_stream_length_hash_and_cancellation() {
        let content = b"installer";
        let checksum = format!("{:x}", Sha256::digest(content));
        let run = |bytes: &[u8], size, hash: &str, cancelled| {
            let mut out = Vec::new();
            copy_verified(&mut Cursor::new(bytes), &mut out, size, hash, &AtomicBool::new(cancelled), |_| {})
        };
        assert!(run(content, 9, &checksum, false).is_ok());
        assert!(run(content, 10, &checksum, false).is_err());
        assert!(run(content, 8, &checksum, false).is_err());
        assert!(run(content, 9, &"0".repeat(64), false).is_err());
        assert!(run(content, 9, &checksum, true).is_err());
    }

    #[tokio::test]
    async fn cancels_a_stalled_network_operation() {
        let cancelled = AtomicBool::new(false);
        let operation = std::future::pending::<Result<(), reqwest::Error>>();
        let (result, _) = tokio::join!(
            tokio::time::timeout(Duration::from_secs(1), cancellable(&cancelled, operation)),
            async { tokio::time::sleep(Duration::from_millis(20)).await; cancelled.store(true, Ordering::SeqCst); }
        );
        assert_eq!(result.unwrap().unwrap_err(), "下载已取消");
    }

    #[tokio::test]
    async fn downloads_http_stream_and_never_keeps_a_corrupt_installer() {
        use std::net::TcpListener;
        // The initial URL comes from release_candidate in production. This local
        // fixture exercises real HTTP bodies and filesystem cleanup without GitHub.
        for corrupted in [false, true] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let url = format!("http://{}", listener.local_addr().unwrap());
            let content = b"fixture installer";
            let hash = if corrupted { "0".repeat(64) } else { format!("{:x}", Sha256::digest(content)) };
            let checksum = format!("{hash}  fixture.exe\n");
            let server = std::thread::spawn(move || {
                for body in [checksum.as_bytes(), content] {
                    let (mut stream, _) = listener.accept().unwrap();
                    stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                    let mut request = [0; 4096]; stream.read(&mut request).unwrap();
                    write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).unwrap();
                    stream.write_all(body).unwrap();
                }
            });
            let cache = std::env::temp_dir().join(format!("douyincut-update-test-{}-{}-{corrupted}", std::process::id(),
                std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
            let candidate = Candidate { url: format!("{url}/installer"), checksum_url: format!("{url}/checksum"),
                filename: "fixture.exe".into(), size: content.len() as u64 };
            let host = UpdateHost::default();
            let result = download(&host, &candidate, &cache).await;
            server.join().unwrap();
            assert_eq!(result.is_ok(), !corrupted);
            assert!(!cache.join("setup.exe.part").exists());
            if corrupted {
                assert!(!cache.join("setup.exe").exists());
                assert!(host.state.lock().unwrap().verified.is_none());
            } else {
                assert_eq!(fs::read(cache.join("setup.exe")).unwrap(), content);
                let state = host.state.lock().unwrap();
                assert_eq!(state.status.phase, "ready");
                assert_eq!(state.status.downloaded_bytes, content.len() as u64);
                assert!(state.verified.is_some());
            }
            fs::remove_dir_all(cache).unwrap();
        }
    }

    #[test]
    fn serializes_updates_and_preserves_ready_download_on_busy_request() {
        let mut state = UpdateState::default();
        for phase in ["checking", "downloading", "installing"] {
            state.status.phase = phase.into(); assert!(ensure_available(&state).is_err());
        }
        state.status.phase = "ready".into(); assert!(ensure_available(&state).is_ok());
    }
}
