#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod session;
mod workspace;

fn main() {
    use tauri::Manager;
    let application = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![workspace::workspace_action])
        .setup(session::setup)
        .build(tauri::generate_context!());
    match application {
        Ok(app) => app.run(|handle, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(runtime) = handle.try_state::<session::SessionHost>() {
                    if let Err(message) = runtime.shutdown() {
                        session::show_error(&message);
                    }
                }
            }
        }),
        Err(error) => session::show_error(&error.to_string()),
    }
}
