# Windows 托盘与单实例

本页对应 [工单 04：托盘后台运行与当前用户单实例](../.scratch/windows-packaging/issues/04-tray-single-instance.md)。普通桌面原生验证完成前，工单保持 `ready-for-human`。

## 行为

- 关闭 `main` 初始化窗口或 `workbench` 工作台窗口会拦截原生关闭并隐藏窗口。WebView、页面草稿、SSE 和 `SessionHost` 保持运行；同一程序进程首次关闭时显示托盘说明。
- 托盘菜单中的“打开 DouyinCut”恢复已有工作台或初始化窗口，不重建窗口或服务；“退出 DouyinCut”调用统一退出入口，复用当前空闲会话的正常收尾。活动本地工作确认和暂停语义由工单 05 处理。
- 启动时读取当前 Windows token 的 SID，并创建包含应用标识、SID 和 `Local` 交互会话范围的互斥体 / auto-reset 唤醒事件。Windows 默认对象 ACL 限制同一账户；重复启动只唤醒已运行实例，不创建第二个 Tauri App 或后台服务。
- 唤醒监听在线程中等待，事件到达后仅在 Tauri 主线程恢复窗口。统一退出时停止并等待该监听线程结束。

## 代码级测试

在 Windows 构建环境运行：

~~~powershell
cargo test --offline --manifest-path desktop/src-tauri/Cargo.toml instance::platform::tests -- --nocapture
~~~

测试覆盖当前账户 SID 的单实例重复启动与唤醒、不同 SID 对象隔离及应用标识隔离。它不代替另一个真实 Windows 账户或原生任务栏的人工验证。

## 普通桌面冒烟

先构建包含 runtime 的 release 程序，再运行：

~~~powershell
uv run python scripts/prepare-windows-tray-smoke.py
~~~

脚本在 `.runtime/windows-packaging/tray-smoke/<时间戳>/` 下生成新的测试工作目录、release 程序副本、独立 WebView2 profile 和 `launch-tray-check.cmd`。先通过托盘退出其他 DouyinCut 实例，再双击生成的启动器。验证数据只存在于该目录，不要使用真实工作目录。

打开第二个 PowerShell 窗口，在每个检查点执行以下命令，对比程序与运行时的 PID / 数量：

~~~powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('douyincut.exe', 'DouyinCutRuntime.exe') } |
  Sort-Object ProcessId |
  Select-Object Name, ProcessId, ParentProcessId |
  Format-Table -AutoSize
~~~

1. 等空项目工作台打开后记录 `douyincut.exe` 和 `DouyinCutRuntime.exe` 进程。
2. 关闭窗口并确认首次说明出现；关闭说明后窗口消失，启动器与运行时进程仍存在。
3. 从托盘菜单打开应用，确认恢复同一空项目工作台。再次关闭窗口并启动第二个启动器，确认原窗口恢复且 `DouyinCutRuntime.exe` 进程数量不变。
4. 从托盘菜单显式退出，确认启动器与该应用拥有的运行时进程结束。

记录 Windows 版本、各步骤结果和进程数量，再将工单状态从 `ready-for-human` 更新为实际验收结果。
