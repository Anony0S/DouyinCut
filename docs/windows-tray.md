# Windows 托盘与单实例

本页对应 [工单 04：托盘后台运行与当前用户单实例](../.scratch/windows-packaging/issues/04-tray-single-instance.md)。2026-10-27 结合代码验证及用户人工验收反馈，工单已标记为 `resolved`。

## 行为

- 关闭 `main` 初始化窗口或 `workbench` 工作台窗口会拦截原生关闭并隐藏窗口。WebView、页面草稿、SSE 和 `SessionHost` 保持运行；同一程序进程首次关闭时显示托盘说明。
- 托盘菜单中的“打开 DouyinCut”恢复已有工作台或初始化窗口，不重建窗口或服务；“退出 DouyinCut”先恢复工作台并发起退出请求。未保存修改时默认提供“返回保存”，也可明确放弃后继续；活动本地任务需要二次确认。确认退出后显示收尾状态，暂停本地队列、等待媒体检查点和应用自有进程结束后退出。重启后暂停任务不自动运行，可在后台任务页手动恢复。云端中断请求的行为由工单 06–08 处理。
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

脚本在 `.runtime/windows-packaging/tray-smoke/<时间戳>/` 下生成新的测试工作目录、release 程序副本、独立 WebView2 profile 和 `launch-tray-check.cmd`。启动器使用 ASCII 子目录名，启动失败后会保留命令窗口并显示退出码及运行时日志位置。先通过托盘退出其他 DouyinCut 实例，再双击生成的启动器。验证数据只存在于该目录，不要使用真实工作目录。

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

工单 05 的退出交互使用隔离桌面程序验证。构建好 Windows release 程序及随包媒体工具后运行：

~~~powershell
uv run python scripts/prepare-windows-exit-check.py
~~~

脚本会在 `.runtime/windows-packaging/exit-check/<时间戳>/` 生成启动器、操作说明和独立测试工作区。它使用随包 FFmpeg 生成合成视频，并预置一个项目、一个已审核方案和两个合法本地导出任务；不访问真实工作目录或云端。双击生成目录中的 `launch-exit-check.cmd`，按同目录 `README.txt` 依次验证草稿返回 / 放弃、活动任务退出取消与继续、确认退出收尾及子进程清理、重启后保持暂停和手动恢复。若原生退出命令失败，确认框应重新出现，提示应包含可读错误详情，可再次尝试；相同错误再次发生也应再次显示。若提示仍为空或第二次点击无反应，记录提示文字并检查对应隔离工作区的 `config/logs/desktop-runtime.log` 与任务状态。测试时如任务已完成，按说明恢复第二项后重测检查点。保存失败路径不由预置夹具稳定触发，仍需在隔离项目中另行验证，确保保存失败时退出提示不会被绕过。不要选择真实数据或填写云端凭据。

记录 Windows 版本、各步骤结果和进程数量，再将工单状态从 `ready-for-human` 更新为实际验收结果。

## 人工验收记录

2026-10-27，用户报告隔离程序启动时有 1 个 `douyincut.exe` 和 4 个 `DouyinCutRuntime.exe`。点击关闭、托盘恢复和首次关闭提示均正常；从托盘退出后，进程查询没有返回相关进程。后续复测确认先隐藏原窗口再双击同一启动器，原窗口恢复且运行时进程数不变。早先一次双击未观察到可见变化，后续复测成功；两次之间未修改运行时。隐藏期间活动状态和其他用户 / 会话隔离仍待验证，工单 04 保持 `ready-for-human`。

同日，在收到隐藏期间本地导出 / 状态保持及另一 Windows 用户隔离的补充验证步骤后，用户回复“验证完成，功能正常”。结合此前结果，按用户报告补录剩余验收，工单 04 标记为 `resolved`。本次没有新增代理自动桌面操作证据；Windows 具体版本、第二账户信息及逐项截图 / 进程快照未提供。早先一次未观察到唤回的记录继续保留，不宣称已定位其根因。安装器、活动任务显式退出与云端中断恢复不在本票完成范围内。
