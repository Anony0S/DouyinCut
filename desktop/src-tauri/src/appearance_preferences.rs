use serde_json::{json, Value};
use std::{fs, io, path::PathBuf, sync::Mutex};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum Appearance {
    #[default]
    System,
    Light,
    Dark,
}

impl Appearance {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "system" => Some(Self::System),
            "light" => Some(Self::Light),
            "dark" => Some(Self::Dark),
            _ => None,
        }
    }

    fn key(self) -> &'static str {
        match self {
            Self::System => "system",
            Self::Light => "light",
            Self::Dark => "dark",
        }
    }
}

pub struct AppearanceHost {
    path: PathBuf,
    appearance: Mutex<Appearance>,
}

impl AppearanceHost {
    pub fn load(path: PathBuf) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let saved = fs::read(&path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .unwrap_or(Value::Null);
        let appearance = saved
            .get("appearance")
            .and_then(Value::as_str)
            .and_then(Appearance::parse)
            .unwrap_or_default();
        Ok(Self {
            path,
            appearance: Mutex::new(appearance),
        })
    }

    pub fn get(&self) -> Result<String, String> {
        self.appearance
            .lock()
            .map(|appearance| appearance.key().to_owned())
            .map_err(|_| "外观设置不可用，请重启应用。".to_owned())
    }

    pub fn set(&self, value: &str) -> Result<String, String> {
        let appearance =
            Appearance::parse(value).ok_or_else(|| "请选择有效的外观主题。".to_owned())?;
        let mut current = self
            .appearance
            .lock()
            .map_err(|_| "外观设置不可用，请重启应用。".to_owned())?;
        let contents = serde_json::to_vec_pretty(&json!({ "appearance": appearance.key() }))
            .map_err(|error| error.to_string())?;
        fs::write(&self.path, contents).map_err(|error| format!("无法保存外观设置：{error}"))?;
        *current = appearance;
        Ok(appearance.key().to_owned())
    }
}

#[tauri::command]
pub fn get_appearance_preference(
    window: tauri::WebviewWindow,
    preferences: tauri::State<'_, AppearanceHost>,
) -> Result<String, String> {
    crate::window_preferences::ensure_settings_window(&window)?;
    preferences.get()
}

#[tauri::command]
pub fn set_appearance_preference(
    window: tauri::WebviewWindow,
    preferences: tauri::State<'_, AppearanceHost>,
    appearance: String,
) -> Result<String, String> {
    crate::window_preferences::ensure_settings_window(&window)?;
    preferences.set(&appearance)
}

#[cfg(test)]
mod tests {
    use super::AppearanceHost;
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    #[test]
    fn appearance_changes_survive_reload() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("douyincut-appearance-{suffix}.json"));
        let preferences = AppearanceHost::load(path.clone()).unwrap();
        assert_eq!(preferences.get().unwrap(), "system");
        assert_eq!(preferences.set("dark").unwrap(), "dark");
        drop(preferences);
        let reloaded = AppearanceHost::load(path.clone()).unwrap();
        assert_eq!(reloaded.get().unwrap(), "dark");
        assert!(reloaded.set("invalid").is_err());
        fs::remove_file(path).unwrap();
    }
}
