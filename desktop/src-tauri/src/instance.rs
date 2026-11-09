pub enum InstanceAcquisition {
    Primary(CurrentUserInstance),
    AlreadyRunning,
}

pub struct CurrentUserInstance {
    #[cfg(windows)]
    platform: platform::WindowsInstance,
}

impl CurrentUserInstance {
    pub fn acquire(identifier: &str) -> Result<InstanceAcquisition, String> {
        if identifier.is_empty()
            || !identifier.chars().all(|character| {
                character.is_ascii_alphanumeric() || matches!(character, '.' | '-')
            })
        {
            return Err("无法建立应用单实例：应用标识格式无效。".into());
        }

        #[cfg(windows)]
        {
            let user_sid = platform::current_user_sid()?;
            platform::acquire_for_user_sid(identifier, &user_sid)
        }
        #[cfg(not(windows))]
        {
            let _ = identifier;
            Ok(InstanceAcquisition::Primary(Self {}))
        }
    }

    pub fn listen_for_activation(
        &self,
        on_activation: impl Fn() + Send + 'static,
    ) -> Result<(), String> {
        #[cfg(windows)]
        {
            self.platform.listen(on_activation)
        }
        #[cfg(not(windows))]
        {
            let _ = on_activation;
            Ok(())
        }
    }

    pub fn stop_activation_listener(&self) {
        #[cfg(windows)]
        self.platform.stop_listener();
    }
}

fn object_names(identifier: &str, user_sid: &str) -> (String, String) {
    let identity = format!("DouyinCut-{identifier}-{user_sid}");
    // Local scope isolates desktop sessions; the SID and default DACL isolate Windows accounts.
    (
        format!("Local\\{identity}-SingleInstance"),
        format!("Local\\{identity}-Activate"),
    )
}

#[cfg(windows)]
mod platform {
    use super::{object_names, CurrentUserInstance, InstanceAcquisition};
    use std::{
        mem::size_of,
        ptr,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc, Mutex,
        },
        thread::{self, JoinHandle},
    };
    use windows_sys::Win32::{
        Foundation::{
            CloseHandle, GetLastError, SetLastError, ERROR_ALREADY_EXISTS, HANDLE, WAIT_OBJECT_0,
        },
        Security::{GetTokenInformation, TokenUser, SID, TOKEN_QUERY, TOKEN_USER},
        System::Threading::{
            CreateEventW, CreateMutexW, GetCurrentProcess, OpenProcessToken, SetEvent,
            WaitForSingleObject, INFINITE,
        },
    };

    pub(super) struct WindowsInstance {
        _mutex: OwnedHandle,
        _installer_guard: OwnedHandle,
        activation: OwnedHandle,
        stopping: Arc<AtomicBool>,
        listener: Mutex<Option<JoinHandle<()>>>,
    }

    struct OwnedHandle(isize);

    impl OwnedHandle {
        fn new(handle: HANDLE) -> Self {
            Self(handle as isize)
        }

        fn raw(&self) -> HANDLE {
            self.0 as HANDLE
        }
    }

    impl Drop for OwnedHandle {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.raw());
            }
        }
    }

    pub(super) fn current_user_sid() -> Result<String, String> {
        let mut token = ptr::null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(format!(
                "无法读取当前 Windows 用户身份：{}",
                std::io::Error::last_os_error()
            ));
        }
        let token = OwnedHandle::new(token);
        let mut required = 0;
        unsafe {
            GetTokenInformation(token.raw(), TokenUser, ptr::null_mut(), 0, &mut required);
        }
        if required == 0 {
            return Err(format!(
                "无法读取当前 Windows 用户身份：{}",
                std::io::Error::last_os_error()
            ));
        }

        let word_count = (required as usize).div_ceil(size_of::<usize>());
        let mut buffer = vec![0usize; word_count];
        if unsafe {
            GetTokenInformation(
                token.raw(),
                TokenUser,
                buffer.as_mut_ptr().cast(),
                required,
                &mut required,
            )
        } == 0
        {
            return Err(format!(
                "无法读取当前 Windows 用户身份：{}",
                std::io::Error::last_os_error()
            ));
        }

        let user = unsafe { &*buffer.as_ptr().cast::<TOKEN_USER>() };
        sid_to_string(user.User.Sid)
    }

    fn sid_to_string(raw_sid: windows_sys::Win32::Security::PSID) -> Result<String, String> {
        if raw_sid.is_null() {
            return Err("Windows 未返回当前用户 SID。".into());
        }
        let sid = unsafe { &*raw_sid.cast::<SID>() };
        let authority = sid
            .IdentifierAuthority
            .Value
            .iter()
            .fold(0u64, |value, byte| (value << 8) | u64::from(*byte));
        let mut value = format!("S-{}-{authority}", sid.Revision);
        for index in 0..sid.SubAuthorityCount as usize {
            let offset = std::mem::offset_of!(SID, SubAuthority) + index * size_of::<u32>();
            let sub_authority =
                unsafe { ptr::read_unaligned(raw_sid.cast::<u8>().add(offset).cast::<u32>()) };
            value.push('-');
            value.push_str(&sub_authority.to_string());
        }
        Ok(value)
    }

    pub(super) fn acquire_for_user_sid(
        identifier: &str,
        user_sid: &str,
    ) -> Result<InstanceAcquisition, String> {
        let (mutex_name, event_name) = object_names(identifier, user_sid);
        // Create the signal before the mutex so concurrent startup can wake the owner.
        let event_name = wide(&event_name);
        let event = unsafe { CreateEventW(ptr::null(), 0, 0, event_name.as_ptr()) };
        if event.is_null() {
            return Err(format!(
                "无法建立当前用户的应用唤醒信号：{}",
                std::io::Error::last_os_error()
            ));
        }
        let activation = OwnedHandle::new(event);

        let mutex_name = wide(&mutex_name);
        unsafe { SetLastError(0) };
        let mutex = unsafe { CreateMutexW(ptr::null(), 0, mutex_name.as_ptr()) };
        if mutex.is_null() {
            return Err(format!(
                "无法建立当前用户的应用单实例锁：{}",
                std::io::Error::last_os_error()
            ));
        }
        let mutex = OwnedHandle::new(mutex);

        if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
            if unsafe { SetEvent(activation.raw()) } == 0 {
                return Err(format!(
                    "无法唤回已运行的 DouyinCut：{}",
                    std::io::Error::last_os_error()
                ));
            }
            return Ok(InstanceAcquisition::AlreadyRunning);
        }

        // A fixed session-local marker lets Inno Setup detect the application
        // without reproducing the SID-specific activation protocol.
        let installer_name = wide("Local\\DouyinCut-InstallerGuard");
        let installer_guard = unsafe { CreateMutexW(ptr::null(), 0, installer_name.as_ptr()) };
        if installer_guard.is_null() {
            return Err(format!("无法建立安装保护锁：{}", std::io::Error::last_os_error()));
        }
        Ok(InstanceAcquisition::Primary(CurrentUserInstance {
            platform: WindowsInstance {
                _mutex: mutex,
                _installer_guard: OwnedHandle::new(installer_guard),
                activation,
                stopping: Arc::new(AtomicBool::new(false)),
                listener: Mutex::new(None),
            },
        }))
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(Some(0)).collect()
    }

    impl WindowsInstance {
        pub(super) fn listen(
            &self,
            on_activation: impl Fn() + Send + 'static,
        ) -> Result<(), String> {
            let mut listener = self
                .listener
                .lock()
                .map_err(|_| "应用唤醒监听状态不可用。")?;
            if listener.is_some() {
                return Ok(());
            }

            let activation = self.activation.0;
            let stopping = Arc::clone(&self.stopping);
            let thread = thread::Builder::new()
                .name("douyincut-instance-activation".into())
                .spawn(move || loop {
                    let result = unsafe { WaitForSingleObject(activation as HANDLE, INFINITE) };
                    if result != WAIT_OBJECT_0 || stopping.load(Ordering::SeqCst) {
                        break;
                    }
                    on_activation();
                })
                .map_err(|error| format!("无法监听应用唤醒信号：{error}"))?;
            *listener = Some(thread);
            Ok(())
        }

        pub(super) fn stop_listener(&self) {
            if !self.stopping.swap(true, Ordering::SeqCst) {
                unsafe {
                    SetEvent(self.activation.raw());
                }
            }
            if let Ok(mut listener) = self.listener.lock() {
                if let Some(thread) = listener.take() {
                    let _ = thread.join();
                }
            }
        }
    }

    impl Drop for WindowsInstance {
        fn drop(&mut self) {
            self.stop_listener();
        }
    }

    #[cfg(test)]
    mod tests {
        use super::super::{object_names, CurrentUserInstance, InstanceAcquisition};
        use super::acquire_for_user_sid;
        use std::{
            sync::mpsc,
            time::{Duration, SystemTime, UNIX_EPOCH},
        };

        fn test_identifier() -> String {
            let nonce = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            format!("com.douyincut.test.{}.{}", std::process::id(), nonce)
        }

        #[test]
        fn repeated_launch_wakes_the_same_user_owner_and_keeps_other_sid_separate() {
            let identifier = test_identifier();
            let second_sid = "S-1-5-21-1002";
            let InstanceAcquisition::Primary(first) =
                CurrentUserInstance::acquire(&identifier).unwrap()
            else {
                panic!("the first launch must own the instance")
            };
            let InstanceAcquisition::Primary(second_user) =
                acquire_for_user_sid(&identifier, second_sid).unwrap()
            else {
                panic!("a different user SID must own an independent instance")
            };
            let (send, receive) = mpsc::channel();
            first
                .listen_for_activation(move || send.send(()).unwrap())
                .unwrap();

            assert!(matches!(
                CurrentUserInstance::acquire(&identifier).unwrap(),
                InstanceAcquisition::AlreadyRunning
            ));
            receive
                .recv_timeout(Duration::from_secs(2))
                .expect("the existing same-user owner must receive the activation signal");

            drop(second_user);
            drop(first);
        }

        #[test]
        fn windows_instance_objects_are_scoped_to_application_and_user() {
            let first_user = object_names("com.douyincut.desktop", "S-1-5-21-1001");
            let same_user = object_names("com.douyincut.desktop", "S-1-5-21-1001");
            let second_user = object_names("com.douyincut.desktop", "S-1-5-21-1002");
            let other_application = object_names("com.example.other", "S-1-5-21-1001");

            assert_eq!(first_user, same_user);
            assert_ne!(first_user, second_user);
            assert_ne!(first_user, other_application);
            assert!(first_user.0.starts_with("Local\\"));
            assert!(first_user.1.starts_with("Local\\"));
        }
    }
}
