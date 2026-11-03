#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod instance;
mod session;
mod workspace;

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
        .manage(instance)
        .invoke_handler(tauri::generate_handler![
            workspace::workspace_action,
            workspace::delete_workspace_config,
            workspace::relaunch,
            workspace::open_log_directory,
            workspace::clear_logs,
            workspace::check_for_update,
            session::complete_exit
        ])
        .on_window_event(session::handle_window_event)
        .setup(session::setup)
        .build(context);
    match application {
        Ok(app) => app.run(|handle, event| {
            if let tauri::RunEvent::Exit = event {
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
