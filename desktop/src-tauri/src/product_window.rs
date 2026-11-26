use tauri::{
    webview::NewWindowResponse, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

pub const LABEL: &str = "xingdongli-products";
const PATH: &str = "/xingdongli-products.html";

fn loopback(url: &Url) -> bool {
    url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port().is_some()
        && url.username().is_empty()
        && url.password().is_none()
}

pub fn is_product_window(window: &WebviewWindow) -> bool {
    window.label() == LABEL && window.url().is_ok_and(|url| loopback(&url) && url.path() == PATH)
}

#[tauri::command]
pub async fn xingdongli_products_always_on_top(
    window: WebviewWindow,
    pinned: Option<bool>,
) -> Result<bool, String> {
    if !is_product_window(&window) {
        return Err("置顶操作仅适用于星动力商品列表窗口。".into());
    }
    if let Some(pinned) = pinned {
        window.set_always_on_top(pinned).map_err(|error| error.to_string())?;
    }
    window.is_always_on_top().map_err(|error| error.to_string())
}

fn product_url(label: &str, source: &Url, monitor_id: &str) -> Result<Url, String> {
    if !(label == "workbench" || (cfg!(debug_assertions) && label == "main")) || !loopback(source) {
        return Err("商品列表仅允许从本机工作台打开。".into());
    }
    if !monitor_id.strip_prefix("live_").is_some_and(|id| {
        id.len() == 32 && id.bytes().all(|byte| byte.is_ascii_hexdigit())
    }) {
        return Err("直播间标识无效，请重新选择。".into());
    }
    let mut url = source.join(PATH).map_err(|error| error.to_string())?;
    url.query_pairs_mut().append_pair("monitor_id", monitor_id);
    Ok(url)
}

#[tauri::command]
pub async fn open_xingdongli_products(window: WebviewWindow, monitor_id: String) -> Result<(), String> {
    let source = window.url().map_err(|error| error.to_string())?;
    let url = product_url(window.label(), &source, &monitor_id)?;
    let app = window.app_handle();
    if let Some(existing) = app.get_webview_window(LABEL) {
        if existing.url().map_err(|error| error.to_string())? != url {
            existing.navigate(url).map_err(|error| error.to_string())?;
        }
        existing.unminimize().map_err(|error| error.to_string())?;
        return existing.set_focus().map_err(|error| error.to_string());
    }

    // The workbench and the catalog share the local origin and WebView2 profile.
    // The authenticated session cookie is already available to the catalog API.
    let origin = url.origin();
    WebviewWindowBuilder::new(app, LABEL, WebviewUrl::External(url))
        .title("星动力商品列表 · DouyinCut")
        .inner_size(820.0, 650.0)
        .min_inner_size(440.0, 420.0)
        .center()
        .on_navigation(move |target| target.origin() == origin && target.path() == PATH)
        .on_new_window(|_, _| NewWindowResponse::Deny)
        .build()
        .map_err(|error| format!("无法打开星动力商品列表：{error}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_uses_a_fixed_same_origin_page() {
        let source = Url::parse("http://127.0.0.1:8765/#/live-monitoring").unwrap();
        let id = "live_00000000000000000000000000000001";
        let target = product_url("workbench", &source, id).unwrap();
        assert_eq!(target.origin(), source.origin());
        assert_eq!(target.path(), PATH);
        assert_eq!(target.query(), Some(format!("monitor_id={id}").as_str()));
        assert_eq!(target.fragment(), None);
        assert!(product_url(LABEL, &source, id).is_err());
        assert!(product_url("workbench", &source, "../settings").is_err());
        assert!(product_url("workbench", &Url::parse("https://example.com").unwrap(), id).is_err());
    }
}
