use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};

use serde::{Deserialize, Serialize};
use tauri::{
    webview::NewWindowResponse, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

const LOGIN_WINDOW: &str = "douyin-login";
const LOGIN_PAGE: &str = "https://www.douyin.com/";
const LIVE_PRODUCTS_URL: &str = "https://live.douyin.com/live/promotions/page/";
const SCAN_TIMEOUT: Duration = Duration::from_secs(300);
static LOGIN_IN_PROGRESS: AtomicBool = AtomicBool::new(false);

struct LoginGuard;

impl Drop for LoginGuard {
    fn drop(&mut self) {
        LOGIN_IN_PROGRESS.store(false, Ordering::Release);
    }
}

#[derive(Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum LoginOutcome {
    Saved { preview: String },
    Cancelled,
}

#[derive(Deserialize)]
struct SavedSecret {
    saved: bool,
    preview: String,
}

fn official_page(url: &Url) -> bool {
    url.scheme() == "https"
        && url.port().is_none()
        && url
            .host_str()
            .is_some_and(|host| host == "douyin.com" || host.ends_with(".douyin.com"))
}

fn settings_endpoint(label: &str, url: &Url) -> Option<Url> {
    if !matches!(label, "main" | "workbench")
        || url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return None;
    }
    url.join("/api/v1/settings/secrets").ok()
}

fn logged_in_cookie(name: &str, value: &str) -> bool {
    matches!(name, "sessionid" | "sessionid_ss" | "sid_tt") && !value.is_empty()
}

async fn wait_for_login(
    window: &WebviewWindow,
    closed: &AtomicBool,
) -> Result<Option<String>, String> {
    let target = Url::parse(LIVE_PRODUCTS_URL).expect("valid Douyin cookie URL");
    let scan = async {
        loop {
            if closed.load(Ordering::Acquire) {
                return Ok(None);
            }
            let webview = window.clone();
            let url = target.clone();
            // WebView2 cookie retrieval must run off the UI thread.
            let cookies =
                tauri::async_runtime::spawn_blocking(move || webview.cookies_for_url(url)).await;
            if closed.load(Ordering::Acquire) {
                return Ok(None);
            }
            let cookies = cookies
                .ok()
                .and_then(Result::ok)
                .ok_or("无法读取抖音登录状态，请重新扫码。")?;
            if cookies
                .iter()
                .any(|cookie| logged_in_cookie(cookie.name(), cookie.value()))
            {
                let header = cookies
                    .iter()
                    .map(|cookie| cookie.stripped().to_string())
                    .collect::<Vec<_>>()
                    .join("; ");
                return Ok(Some(header));
            }
            tokio::time::sleep(Duration::from_millis(750)).await;
        }
    };
    tokio::time::timeout(SCAN_TIMEOUT, scan)
        .await
        .map_err(|_| "扫码登录超时，请重试。".to_owned())?
}

async fn save_cookie(endpoint: Url, token: &str, cookie: String) -> Result<String, String> {
    let save_error = "已扫码，但保存抖音登录状态失败，请检查本地服务后重试。";
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(8))
        .build()
        .map_err(|_| save_error)?;
    let response = client
        .post(endpoint)
        .header("X-Session-Token", token)
        .json(&serde_json::json!({ "name": "douyin_cookie", "value": cookie }))
        .send()
        .await
        .map_err(|_| save_error)?;
    if !response.status().is_success() {
        return Err(save_error.into());
    }
    let result = response
        .json::<SavedSecret>()
        .await
        .map_err(|_| save_error)?;
    if !result.saved {
        return Err(save_error.into());
    }
    Ok(result.preview)
}

#[tauri::command]
pub async fn scan_douyin_login(
    window: WebviewWindow,
    session_token: String,
) -> Result<LoginOutcome, String> {
    let origin = window.url().map_err(|_| "无法确认工作台地址。")?;
    let endpoint =
        settings_endpoint(window.label(), &origin).ok_or("扫码登录仅允许从本机工作台发起。")?;
    if session_token.is_empty() {
        return Err("本地服务尚未连接，请重试。".into());
    }
    let app = window.app_handle();
    if LOGIN_IN_PROGRESS
        .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
        .is_err()
    {
        if let Some(active) = app.get_webview_window(LOGIN_WINDOW) {
            let _ = active.set_focus();
        }
        return Err("已有抖音登录窗口打开，请在该窗口继续。".into());
    }
    let _guard = LoginGuard;
    let profile = app
        .path()
        .app_local_data_dir()
        .map_err(|_| "无法定位抖音登录窗口的数据目录。")?
        .join("douyin-login-webview");
    let login = WebviewWindowBuilder::new(
        app,
        LOGIN_WINDOW,
        WebviewUrl::External(Url::parse(LOGIN_PAGE).expect("valid Douyin login URL")),
    )
    .title("抖音扫码登录 · DouyinCut")
    .inner_size(1120.0, 760.0)
    .center()
    .data_directory(profile)
    .incognito(true)
    .visible(false)
    .on_navigation(official_page)
    .on_new_window(|_, _| NewWindowResponse::Deny)
    .build()
    .map_err(|_| "无法打开抖音登录窗口，请重试。")?;

    let closed = Arc::new(AtomicBool::new(false));
    let event_closed = Arc::clone(&closed);
    login.on_window_event(move |event| {
        if matches!(
            event,
            tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed
        ) {
            event_closed.store(true, Ordering::Release);
        }
    });

    let result = async {
        login.show().map_err(|_| "无法显示抖音登录窗口，请重试。")?;
        let _ = login.set_focus();
        let Some(cookie) = wait_for_login(&login, &closed).await? else {
            return Ok(LoginOutcome::Cancelled);
        };
        if closed.load(Ordering::Acquire) {
            return Ok(LoginOutcome::Cancelled);
        }
        let preview = save_cookie(endpoint, &session_token, cookie).await?;
        Ok(LoginOutcome::Saved { preview })
    }
    .await;
    let _ = login.destroy();
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn login_webview_allows_only_official_https_navigation() {
        assert!(official_page(&Url::parse(LOGIN_PAGE).unwrap()));
        for address in [
            "http://www.douyin.com/",
            "https://douyin.com.example.com/",
            "http://127.0.0.1:5173/",
        ] {
            assert!(!official_page(&Url::parse(address).unwrap()));
        }
    }

    #[test]
    fn save_destination_is_the_local_workbench_and_only_login_markers_trigger_save() {
        let local = Url::parse("http://127.0.0.1:5173/#/settings").unwrap();
        assert_eq!(
            settings_endpoint("main", &local).unwrap().as_str(),
            "http://127.0.0.1:5173/api/v1/settings/secrets"
        );
        assert!(settings_endpoint(LOGIN_WINDOW, &local).is_none());
        assert!(settings_endpoint("workbench", &Url::parse(LOGIN_PAGE).unwrap()).is_none());
        assert!(logged_in_cookie("sessionid", "fixture"));
        assert!(!logged_in_cookie("sessionid_ss", ""));
        assert!(!logged_in_cookie("ttwid", "fixture"));
    }

    #[tokio::test]
    async fn native_save_uses_the_authenticated_settings_request() {
        use std::{
            io::{BufRead, BufReader, Read, Write},
            net::TcpListener,
            thread,
        };

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut request = BufReader::new(&mut socket);
            let mut line = String::new();
            request.read_line(&mut line).unwrap();
            assert!(line.starts_with("POST /api/v1/settings/secrets "));
            let mut headers = std::collections::HashMap::new();
            loop {
                line.clear();
                assert_ne!(request.read_line(&mut line).unwrap(), 0);
                if line == "\r\n" {
                    break;
                }
                let (name, value) = line.split_once(':').unwrap();
                headers.insert(name.to_ascii_lowercase(), value.trim().to_owned());
            }
            assert_eq!(headers["x-session-token"], "local-session-fixture");
            let mut body = vec![0; headers["content-length"].parse().unwrap()];
            request.read_exact(&mut body).unwrap();
            assert_eq!(
                serde_json::from_slice::<serde_json::Value>(&body).unwrap(),
                serde_json::json!({"name": "douyin_cookie", "value": "sessionid=fixture"})
            );
            let response = r#"{"saved":true,"preview":"sessi*****ture"}"#;
            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{response}", response.len()).unwrap();
        });

        let endpoint = settings_endpoint("workbench", &origin).unwrap();
        let preview = save_cookie(
            endpoint,
            "local-session-fixture",
            "sessionid=fixture".into(),
        )
        .await
        .unwrap();
        server.join().unwrap();
        assert_eq!(preview, "sessi*****ture");
    }
}
