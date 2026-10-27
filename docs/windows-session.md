# Windows 生产桌面会话与工作目录（工单 01—02）

本页对应 .scratch/windows-packaging/issues/01-production-desktop-session.md 和 02-work-directory-setup.md。当前交付边界是带工作目录选择 / 原位置恢复的桌面测试程序，不是安装包，也不代表全部业务已通过冻结验收。

托盘、窗口生命周期与单实例的实现及人工验收步骤见 [Windows 托盘与单实例](windows-tray.md)。

## 构建

在 Windows x64 构建机的项目根目录运行：

~~~powershell
./scripts/build-windows-session.ps1 -MediaSource 'C:/构建依赖/ffmpeg-8.1-full_build'
~~~

需要 uv、pnpm 10、Node、Rust MSVC 工具链、Visual Studio C++ Build Tools 与 Windows SDK。脚本使用 uv 管理的标准 CPython 3.12.14，按 uv.lock / pnpm-lock.yaml 安装依赖，构建生产前端、PyInstaller 单目录运行时和 Tauri 测试程序。不要用含定制证书路径的宿主 Python 冻结。构建阶段允许下载依赖，失败立即终止；日志位于 .runtime/windows-packaging/logs。

只有 Python 冻结验证时可运行：

~~~powershell
./scripts/build-windows-session.ps1 -RuntimeOnly -MediaSource 'C:/构建依赖/ffmpeg-8.1-full_build'
uv run python scripts/verify-windows-session.py --runtime desktop/src-tauri/runtime/DouyinCutRuntime/DouyinCutRuntime.exe
~~~

运行时资源位于 desktop/src-tauri/runtime/DouyinCutRuntime，其中必须保留同目录的 _internal。验证脚本将整目录复制到带中文、空格的临时路径，移除子进程的开发工具 PATH 和 Python 环境设置，从当前格式隔离项目验证 API、静态前端、认证与三个角色的退出。验证器自身使用构建机 Python，并不表示被验证的冻结程序依赖该 Python。

原生测试程序位于 desktop/src-tauri/target/release/douyincut.exe；构建脚本通过 tauri.session.conf.json 配置将运行时复制到同级 runtime。基础开发配置不依赖这个生成目录，原有 tauri dev 不要求先冻结。直接启动后在初始化页面选择空目录或原位置的当前格式数据。隔离验证可显式指定独立定位配置：

~~~powershell
./desktop/src-tauri/target/release/douyincut.exe --workspace-config 'D:/DouyinCut 测试/用户配置/workspace.json'
~~~

不传参数时，定位配置使用 Tauri 当前用户 app_config_dir 下的 workspace.json，与工作数据独立。取消选择会保留初始化页面，不启动 API、worker 或监控，也不创建默认工作区。不要用当前测试程序运行真实录制或长时间云端任务。

## 工作目录与原位置恢复

- 选择已有空目录后先检查实际绝对路径、可写性及程序目录隔离，再初始化 database、recordings、analysis、previews、exports、logs。程序目录及其子目录不能用作工作目录。Windows 原生选择器保留中文与空格路径。
- 定位记录、工作目录的 .douyincut-workspace.json 和数据库 desktop_workspace 保存同一身份与原始绝对路径；每次启动先核对身份、当前表 / 字段、数据库完整性及各子目录可写性，通过后才允许准备后台服务。
- 原盘缺失、目录 / 数据库损坏或权限不足时停在修复页；不会创建替代目录或空库。恢复原盘符和路径后点击“重试原位置”。
- 重装或定位记录丢失后，可重新选择原位置的当前格式数据；搬迁、改名、改盘符或链接副本会被拒绝。不提供多工作区切换，不迁移旧业务格式，不重写视频或剪映绝对引用。
- 新建目录先暂存完整数据，再按身份绑定的 .douyincut-initializing.json 发布；发布中断可重新选择相同位置继续。遇到未知内容或冲突保留所有文件并报错，绝不合并覆盖。定位配置写入失败后，有效工作数据仍可重新关联。

## 运行边界

- DesktopSession 统一启动 API、任务 worker 和直播监控，在真实回环 API 读到当前格式项目后才报告就绪。可用端口由系统分配；重启轮换随机令牌。
- Python 运行时通过私有标准输入 / 输出管道与原生外壳握手；在原生进程接管完成、收到启动许可前不创建数据库或启动角色。令牌只进入会话内存；不放进 URL、前端构建或浏览器持久化存储。前端读取后删除注入的临时全局属性。
- 生产前端、API、SSE 和媒体使用同一回环来源，继续校验令牌 / HttpOnly 会话 Cookie 和 Origin，并为生产响应设置 CSP。生产配置不接受开发服务器来源。
- 子进程 stdout 诊断转入 stderr；原生外壳将 stderr 追加到定位配置同级 logs/desktop-runtime.log，避免校验失败时提前修改工作数据目录。错误消息显示实际日志路径。不要把私有启动描述符保存为日志。
- 启动超时或关键角色异常退出会清理其余已启动角色；空闲退出等待三个角色结束。原生外壳另使用 Windows Job Object 持有进程树，避免外壳异常消失后留下自有子进程。

工单 05 增加显式退出协调：托盘请求先恢复工作台，处理未保存草稿和活动本地工作确认；确认后关闭新任务入口、持久化暂停队列，并要求运行中媒体任务到检查点后停下。原生窗口显示收尾状态，直到后台角色退出后才关闭会话。运行时边界与媒体检查点有自动化覆盖；托盘弹窗、保存失败和普通桌面状态仍须按 [托盘说明](windows-tray.md) 做原生验收。云端退出中断和再次提交、直播查询阻塞的隔离由工单 06—09 覆盖。

## 2026-10-27 实际验证

- Windows 11 x64 构建机：标准 CPython 3.12.14、PyInstaller 6.22.3 单目录冻结成功。
- 冻结程序复制到中文与空格路径、子进程 PATH 仅保留 Windows 系统路径：项目列表、拒绝未认证 / 外部来源、生产静态前端 / SPA 路由与空闲退出通过。
- 源码生产边界覆盖真实回环 API、Cookie、会话轮换、占用端口、启动超时、资源 / 目录缺失及关键服务异常清理。最终测试数量和原生构建结果见工单完成记录。
- Rust 1.99.1 / MSVC 构建成功；Cargo.lock 固定 405 项依赖。原生检查、release 测试程序和 SessionHost 冻结集成测试通过，集成测试验证真实认证项目 API、空闲正常退出及端口关闭。
- 实际 release WebView 在复制后的中文 / 空格目录、无开发工具 PATH 下显示“Windows 桌面会话验收.mp4”，认证项目请求返回 200；令牌未进入 URL / localStorage / sessionStorage，临时注入属性已删除。测试宿主被终止后，四个自有运行时进程均实际退出。
- 可重复命令：uv run python scripts/verify-windows-webview.py。该检查仅为它启动的隔离进程设置临时回环调试端口，产物默认不开放调试监听。截图与 result.json 保存到 .runtime/windows-packaging/native 中文 测试 <时间戳>。
- 正常关闭验证使用 SessionHost 公开边界：cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml frozen_session_reads_authenticated_projects_and_exits_gracefully。CDP 的 Page.close / window.close 不等同于 Tauri 窗口关闭，未将它们记作原生关闭按钮验收；托盘与关闭按钮的最终语义属于工单 04。
- 后端完整回归 212 项测试、59 个子测试通过；五项新增运行时测试、前端全部十组脚本、Vue 类型检查及生产构建通过。代码规范 / 规格两路审查发现的开发资源依赖、Job 接管竞态与测试失败清理已修复并复核。
- 未生成或验收 .exe 安装器；未执行付费模型、OSS、真实直播和剪映验收。Windows 10 / 11 干净标准账户、离线安装和完整业务由后续工单覆盖。

## 工单 02 当前验证边界（2026-10-27）

- 最终源码回归：后端 222 项测试、61 个子测试通过；运行时文件 15 项（包含日志 / marker 提交前强退恢复）；前端全部 11 组脚本、Vue 类型检查通过，Rust check 与 cargo test --no-run 编译通过。规范 / 规格两路复核均无剩余实质代码问题。
- 源码运行时覆盖目录初始化、身份 / 表字段验证、原路径恢复、定位记录丢失关联、权限 / 写入失败、搬迁拒绝以及中断初始化重试；前端覆盖选择、取消、修复、重试、启动和失败。
- 实际中间构建的 WebView 首次页面与原目录恢复流程通过，截图和 result.json 位于 .runtime/windows-packaging/目录恢复 1793037593906103500/。已查看首次页及修复页截图，长中文路径正常换行。该构建早于最终故障修复。
- 最新完整构建受 uv 沙箱访问限制阻止；申请提权时自动审批服务返回 404，操作未执行。前端生产构建的 esbuild 同样受父目录访问限制；Vue 类型检查和 Rust check 可独立通过。源码通过不能替代最新冻结程序验收。
- Windows 原生目录选择器点击 / 取消和非系统盘实测未完成，Computer Use 本机管道不可用。故工单 02 标为 ready-for-human，尚未完成全部验收。
- 初始化中断后仅接管完整、原路径身份可验证的初始化意图；不完整文件或冲突内容保留并明确报错，不据文件名前缀删除未知文件。
- Git 暂存写入受 .git 只读权限限制，提权也被同一审批服务 404 阻止，因此工单 02 代码尚未提交。仅本票的 23 文件快照与补丁保存在 .runtime/windows-packaging/stage-02/，README / SPEC 已按本票修改行隔离；用户原有未提交改动保持在工作区，Git 暂存区为空。

### 2026-10-27：不调用审批服务继续验证

用户要求暂不使用 codex-auto-review。此后未申请提权，也未更改审批配置。使用现有项目依赖完成新的生产前端、Python 冻结与 Tauri release 构建，无需访问受限 uv 启动器：

~~~powershell
pnpm --dir desktop exec vue-tsc --noEmit
pnpm --dir desktop exec vite build --configLoader runner
./.venv/Scripts/python.exe -m PyInstaller --clean --noconfirm --workpath .runtime/windows-packaging/pyinstaller --distpath desktop/src-tauri/runtime packaging/windows/runtime.spec
~~~

Tauri 构建使用常规 tauri.session.conf.json，并追加 .runtime/windows-packaging/build-existing-dependencies.json，将 beforeBuildCommand 设为 pnpm exec vue-tsc --noEmit && pnpm exec vite build --configLoader runner；Cargo 使用已缓存锁定依赖并设置 CARGO_NET_OFFLINE=true。该覆盖仅改变构建入口，不改变生产资源或安全校验；未重新安装依赖。

- 最新冻结程序通过中文 / 空格路径、移除开发工具 PATH、认证项目列表、三个角色正常退出及数据库缺失时不启动后台的验证。
- 最新 SessionHost 集成测试通过真实冻结 API 与正常退出。生产 release 程序构建成功，运行时主文件与复制资源 SHA-256 一致。
- 最新实际 WebView 复验未通过：默认数据目录报 HRESULT 0x800700AA（资源正在使用中）；改用测试专属 WEBVIEW2_USER_DATA_FOLDER 后宿主不再立即异常退出，但验证器未发现预期工作台，不能记为界面验收通过。验证器保留宿主日志与失败诊断，专属 WebView 数据目录仅用于测试。
- 尝试原生界面工具时，其内部自动审批仍返回同一 404；立即停止该工具，不继续调用审批服务。原生选择器 / 取消和非系统盘仍未验收。
- Git 提交仍受现有 .git 只读权限限制，本轮未再次申请提权或尝试写入。代码、隔离快照及未完成事项均保留；ready-for-human 状态不变。

### 2026-10-27：空白窗口诊断

- 最小隔离复现定位到原生 WebView 创建阶段：setup 已进入，但窗口构建未返回，也未触发任何页面加载。故无法把空白归因于 Vue 页面或数据库校验。
- 对照将窗口创建放到独立线程，结果不变，该实验已撤回；软件渲染对照也不变，未写入生产配置。
- 开启仅本次测试的浏览器日志后，记录到 GPU 子进程连续退出，exit_code=-1073741790，即 Windows STATUS_ACCESS_DENIED（0xC0000022）；随后浏览器以 GPU process isn't usable 结束，并伴随 network service 崩溃。证据：.runtime/windows-packaging/webview-probe-1793072419762771300/browser.log；软件渲染对照：webview-probe-1793072575951144000/browser.log。
- 当前证据指向执行环境的访问限制，但尚未证明普通桌面也会失败，不能宣称具体系统策略或应用代码已确定为根因。未更改 ACL、安全设置或审批配置，未关闭浏览器沙箱；临时应用调试日志已移除，已重建干净 release 程序。
- 普通桌面隔离验证入口：.runtime/windows-packaging/launch-desktop-check.cmd。从资源管理器双击，无需管理员；每次使用新的 manual-validation/run-<随机值> 目录、独立 WebView profile 和 workspace.json。测试窗口会显示专属 work 文件夹位置。先确认出现目录选择页，再只选择该测试 work 文件夹，检查是否能进入空项目列表；不要选真实工作数据。日志保存在同一运行目录。
- 等待上述人工对照结果，再决定继续修应用还是处理执行环境。两种情况均不得把未完成的真实 WebView / 选择器验收标为通过。

### 2026-10-27：普通桌面首次页面已显示

用户从资源管理器启动隔离入口后提供截图，实际 DouyinCut 窗口已完整显示“选择工作数据目录”、说明文字及可点击的“选择目录”按钮，没有出现此前受限执行中的空白页面。普通桌面首屏加载通过；尚未据此验收选择 / 取消或后台工作台启动。截图中另一个 msedgewebview2.exe 诊断窗口对应手工测试入口启用的浏览器日志，已从该入口移除 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS 日志参数；当前已打开实例不会被此文件修改改变，重新启动后生效。保留 CMD 测试启动窗口用于显示隔离 work 路径与收集退出结果；正式程序入口为原生 exe。

用户随后回复“可以正常进入”，确认普通桌面通过原生目录选择进入视频项目列表。已只读核对 run-16379-28331/workspace.json 指向 run-15784-5433/work；定位记录、工作目录 marker、数据库 desktop_workspace 身份和路径一致，六个工作子目录齐全。人工结果保存为 manual-validation/run-16379-28331/manual-acceptance.json。普通桌面的首次页 → 原生选目录 → 工作台链路通过，受限自动执行中的白屏不再作为普通桌面已复现的应用缺陷。取消操作未获独立明确反馈，非系统盘及最新目录失效恢复仍保留验收缺口，不据此将工单全部勾选或标 resolved。
