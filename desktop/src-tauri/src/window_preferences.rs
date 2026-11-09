use serde_json::{json, Value};
use std::{fs, io, path::PathBuf, sync::Mutex};
use tauri::{Manager, PhysicalPosition, PhysicalSize};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum StartupSize {
    #[default]
    Standard,
    FullHd,
    Qhd,
}

impl StartupSize {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "standard" => Some(Self::Standard),
            "full-hd" => Some(Self::FullHd),
            "qhd" => Some(Self::Qhd),
            _ => None,
        }
    }

    pub fn dimensions(self) -> (u32, u32) {
        match self {
            Self::Standard => (1440, 810),
            Self::FullHd => (1920, 1080),
            Self::Qhd => (2560, 1440),
        }
    }

    fn key(self) -> &'static str {
        match self {
            Self::Standard => "standard",
            Self::FullHd => "full-hd",
            Self::Qhd => "qhd",
        }
    }
}

#[derive(Clone, Debug)]
struct Preferences {
    remember_position: bool,
    startup_size: StartupSize,
    position: Option<(i32, i32)>,
}

impl Default for Preferences {
    fn default() -> Self {
        Self {
            remember_position: true,
            startup_size: StartupSize::default(),
            position: None,
        }
    }
}

pub struct WindowPreferencesHost {
    path: PathBuf,
    preferences: Mutex<Preferences>,
}

impl WindowPreferencesHost {
    pub fn load(path: PathBuf) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let saved = fs::read(&path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .unwrap_or(Value::Null);
        let position = saved
            .get("position")
            .and_then(Value::as_object)
            .and_then(|position| Some((position.get("x")?.as_i64()?, position.get("y")?.as_i64()?)))
            .and_then(|(x, y)| Some((i32::try_from(x).ok()?, i32::try_from(y).ok()?)));
        let preferences = Preferences {
            remember_position: saved
                .get("remember_position")
                .and_then(Value::as_bool)
                .unwrap_or(true),
            startup_size: saved
                .get("startup_size")
                .and_then(Value::as_str)
                .and_then(StartupSize::parse)
                .unwrap_or_default(),
            position,
        };
        Ok(Self {
            path,
            preferences: Mutex::new(preferences),
        })
    }

    pub fn startup_size(&self) -> StartupSize {
        self.preferences
            .lock()
            .map(|preferences| preferences.startup_size)
            .unwrap_or_default()
    }

    pub fn public_value(&self) -> Result<Value, String> {
        let preferences = self
            .preferences
            .lock()
            .map_err(|_| "窗口设置不可用，请重启应用。".to_owned())?;
        Ok(json!({
            "rememberPosition": preferences.remember_position,
            "startupSize": preferences.startup_size.key(),
        }))
    }

    pub fn update(&self, remember_position: bool, startup_size: &str) -> Result<Value, String> {
        let startup_size = StartupSize::parse(startup_size)
            .ok_or_else(|| "请选择有效的启动窗口大小。".to_owned())?;
        let mut preferences = self
            .preferences
            .lock()
            .map_err(|_| "窗口设置不可用，请重启应用。".to_owned())?;
        preferences.remember_position = remember_position;
        preferences.startup_size = startup_size;
        if !remember_position {
            preferences.position = None;
        }
        self.persist(&preferences)?;
        Ok(json!({
            "rememberPosition": preferences.remember_position,
            "startupSize": preferences.startup_size.key(),
        }))
    }

    pub fn save_position(&self, position: PhysicalPosition<i32>) {
        let Ok(mut preferences) = self.preferences.lock() else {
            return;
        };
        if !preferences.remember_position {
            return;
        }
        preferences.position = Some((position.x, position.y));
        let _ = self.persist(&preferences);
    }

    pub fn restore_position(&self, window: &tauri::WebviewWindow) {
        let position = self.preferences.lock().ok().and_then(|preferences| {
            preferences
                .remember_position
                .then_some(preferences.position)
                .flatten()
        });
        let visible = position.filter(|(x, y)| {
            let (Ok(size), Ok(monitors)) = (window.outer_size(), window.available_monitors())
            else {
                return false;
            };
            monitors.iter().any(|monitor| {
                position_has_visible_area(*x, *y, size, *monitor.position(), *monitor.size())
            })
        });
        if let Some((x, y)) = visible {
            if window.set_position(PhysicalPosition::new(x, y)).is_ok() {
                return;
            }
        }
        let _ = window.center();
    }

    fn persist(&self, preferences: &Preferences) -> Result<(), String> {
        let value = json!({
            "remember_position": preferences.remember_position,
            "startup_size": preferences.startup_size.key(),
            "position": preferences.position.map(|(x, y)| json!({"x": x, "y": y})),
        });
        let contents = serde_json::to_vec_pretty(&value).map_err(|error| error.to_string())?;
        fs::write(&self.path, contents).map_err(|error| format!("无法保存窗口设置：{error}"))
    }
}

fn position_has_visible_area(
    x: i32,
    y: i32,
    window: PhysicalSize<u32>,
    monitor_position: PhysicalPosition<i32>,
    monitor_size: PhysicalSize<u32>,
) -> bool {
    let left = i64::from(x).max(i64::from(monitor_position.x));
    let top = i64::from(y).max(i64::from(monitor_position.y));
    let right = (i64::from(x) + i64::from(window.width))
        .min(i64::from(monitor_position.x) + i64::from(monitor_size.width));
    let bottom = (i64::from(y) + i64::from(window.height))
        .min(i64::from(monitor_position.y) + i64::from(monitor_size.height));
    right - left >= 120 && bottom - top >= 80
}

pub(crate) fn ensure_settings_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    let url = window.url().map_err(|error| error.to_string())?;
    let local = (url.scheme() == "http"
        && matches!(url.host_str(), Some("127.0.0.1") | Some("localhost")))
        || (url.scheme() == "tauri" && url.host_str() == Some("localhost"));
    let expected_window =
        window.label() == "workbench" || (cfg!(debug_assertions) && window.label() == "main");
    if local && expected_window {
        Ok(())
    } else {
        Err("窗口设置仅允许从本机工作台修改。".into())
    }
}

#[tauri::command]
pub fn get_window_preferences(
    window: tauri::WebviewWindow,
    preferences: tauri::State<'_, WindowPreferencesHost>,
) -> Result<Value, String> {
    ensure_settings_window(&window)?;
    preferences.public_value()
}

#[tauri::command]
pub fn set_window_preferences(
    window: tauri::WebviewWindow,
    preferences: tauri::State<'_, WindowPreferencesHost>,
    remember_position: bool,
    startup_size: String,
) -> Result<Value, String> {
    ensure_settings_window(&window)?;
    let result = preferences.update(remember_position, &startup_size)?;
    let (width, height) = preferences.startup_size().dimensions();
    window
        .set_size(tauri::LogicalSize::new(f64::from(width), f64::from(height)))
        .map_err(|error| format!("窗口大小调整失败：{error}"))?;
    Ok(result)
}

pub fn save_active_position(app: &tauri::AppHandle) {
    let Some(window) = app
        .get_webview_window("workbench")
        .or_else(|| app.get_webview_window("main"))
    else {
        return;
    };
    let Some(preferences) = app.try_state::<WindowPreferencesHost>() else {
        return;
    };
    if let Ok(position) = window.outer_position() {
        preferences.save_position(position);
    }
}

#[cfg(test)]
mod tests {
    use super::{position_has_visible_area, StartupSize, WindowPreferencesHost};
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };
    use tauri::{PhysicalPosition, PhysicalSize};

    #[test]
    fn startup_sizes_are_all_sixteen_by_nine() {
        for size in [StartupSize::Standard, StartupSize::FullHd, StartupSize::Qhd] {
            let (width, height) = size.dimensions();
            assert_eq!(width * 9, height * 16);
        }
        assert_eq!(StartupSize::Standard.dimensions(), (1440, 810));
        assert_eq!(StartupSize::FullHd.dimensions(), (1920, 1080));
        assert_eq!(StartupSize::Qhd.dimensions(), (2560, 1440));
    }

    #[test]
    fn saved_position_must_leave_a_useful_area_on_a_connected_monitor() {
        let monitor_position = PhysicalPosition::new(0, 0);
        let monitor_size = PhysicalSize::new(1920, 1080);
        let window_size = PhysicalSize::new(1440, 810);
        assert!(position_has_visible_area(
            100,
            100,
            window_size,
            monitor_position,
            monitor_size
        ));
        assert!(!position_has_visible_area(
            3000,
            2000,
            window_size,
            monitor_position,
            monitor_size
        ));
        assert!(!position_has_visible_area(
            1880,
            100,
            window_size,
            monitor_position,
            monitor_size
        ));
    }

    #[test]
    fn preference_changes_survive_reload_and_turning_position_off_clears_it() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("douyincut-window-preferences-{suffix}.json"));
        let preferences = WindowPreferencesHost::load(path.clone()).unwrap();
        assert_eq!(preferences.startup_size(), StartupSize::Standard);
        preferences.save_position(PhysicalPosition::new(40, 60));
        preferences.update(false, "qhd").unwrap();
        drop(preferences);

        let reloaded = WindowPreferencesHost::load(path.clone()).unwrap();
        assert_eq!(reloaded.startup_size(), StartupSize::Qhd);
        let state = reloaded.preferences.lock().unwrap();
        assert!(!state.remember_position);
        assert_eq!(state.position, None);
        drop(state);
        fs::remove_file(path).unwrap();
    }
}
