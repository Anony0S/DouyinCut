fn main() {
    let spec_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../SPEC.md");
    println!("cargo:rerun-if-changed={}", spec_path.display());
    let spec = std::fs::read_to_string(&spec_path).expect("cannot read SPEC.md");
    let version = spec.lines().find_map(|line| line.strip_prefix("- 版本："))
        .expect("SPEC.md is missing its version").trim();
    assert!(version.split('.').count() == 3 && version.split('.').all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit())), "invalid SPEC.md version");
    let mut config: serde_json::Value = std::env::var("TAURI_CONFIG").ok()
        .map(|value| serde_json::from_str(&value).expect("invalid TAURI_CONFIG"))
        .unwrap_or_else(|| serde_json::json!({}));
    if std::env::var("PROFILE").as_deref() == Ok("debug") {
        let bundle = config.as_object_mut().expect("TAURI_CONFIG must be an object")
            .entry("bundle").or_insert_with(|| serde_json::json!({}));
        bundle.as_object_mut().expect("bundle must be an object")
            .insert("icon".into(), serde_json::json!(["icons/icon-dev.ico"]));
    }
    config.as_object_mut().expect("TAURI_CONFIG must be an object").insert("version".into(), version.into());
    let config = config.to_string();
    std::env::set_var("TAURI_CONFIG", &config);
    println!("cargo:rustc-env=TAURI_CONFIG={config}");
    println!("cargo:rustc-env=DOUYINCUT_VERSION={version}");
    tauri_build::build()
}
