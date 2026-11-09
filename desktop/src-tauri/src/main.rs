#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod instance;
mod session;
mod appearance_preferences;
mod window_preferences;
mod workspace;
mod updates;

fn main() {
    use tauri::Manager;
    let context = tauri::generate_context!();
    let mut identifier = context.config().identifier.clone();
    if cfg!(debug_assertions) {
        identifier.push_str(".dev");
    }
    let instance = match instance::CurrentUserInstance::acquire(&identifier) {
        Ok(instance::InstanceAcquisition::Primary(instance)) => instance,
        Ok(instance::InstanceAcquisition::AlreadyRunning) => return,
        Err(message) => {
            session::show_error(&message);
            return;
        }
    };
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(instance)
        .manage(updates::UpdateHost::default())
        .invoke_handler(tauri::generate_handler![
            workspace::workspace_action,
            workspace::delete_workspace_config,
            workspace::relaunch,
            workspace::open_log_directory,
            workspace::clear_logs,
            updates::check_for_update,
            updates::get_update_status,
            updates::download_update,
            updates::cancel_update_download,
            updates::install_update,
            session::complete_exit,
            appearance_preferences::get_appearance_preference,
            appearance_preferences::set_appearance_preference,
            window_preferences::get_window_preferences,
            window_preferences::set_window_preferences
        ])
        .on_window_event(session::handle_window_event)
        .setup(session::setup)
        .build(context);
    match application {
        Ok(app) => app.run(|handle, event| {
            if let tauri::RunEvent::Exit = event {
                window_preferences::save_active_position(handle);
                if let Some(instance) = handle.try_state::<instance::CurrentUserInstance>() {
                    instance.stop_activation_listener();
                }
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
