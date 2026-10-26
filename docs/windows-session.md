# Windows 生产桌面会话（工单 01）

本页对应 .scratch/windows-packaging/issues/01-production-desktop-session.md。当前交付边界是隔离工作目录下的最小桌面测试程序，不是安装包，也不代表全部业务已通过冻结验收。

## 构建

在 Windows x64 构建机的项目根目录运行：

~~~powershell
./scripts/build-windows-session.ps1
~~~

需要 uv、pnpm 10、Node、Rust MSVC 工具链、Visual Studio C++ Build Tools 与 Windows SDK。脚本使用 uv 管理的标准 CPython 3.12.14，按 uv.lock / pnpm-lock.yaml 安装依赖，构建生产前端、PyInstaller 单目录运行时和 Tauri 测试程序。不要用含定制证书路径的宿主 Python 冻结。构建阶段允许下载依赖，失败立即终止；日志位于 .runtime/windows-packaging/logs。

只有 Python 冻结验证时可运行：

~~~powershell
./scripts/build-windows-session.ps1 -RuntimeOnly
uv run python scripts/verify-windows-session.py --runtime desktop/src-tauri/runtime/DouyinCutRuntime/DouyinCutRuntime.exe
~~~

运行时资源位于 desktop/src-tauri/runtime/DouyinCutRuntime，其中必须保留同目录的 _internal。验证脚本将整目录复制到带中文、空格的临时路径，移除子进程的开发工具 PATH 和 Python 环境设置，从当前格式隔离项目验证 API、静态前端、认证与三个角色的退出。验证器自身使用构建机 Python，并不表示被验证的冻结程序依赖该 Python。

原生测试程序位于 desktop/src-tauri/target/release/douyincut.exe；构建脚本通过 tauri.session.conf.json 配置将运行时复制到同级 runtime。基础开发配置不依赖这个生成目录，原有 tauri dev 不要求先冻结。使用已存在的隔离工作目录：

~~~powershell
./desktop/src-tauri/target/release/douyincut.exe --work-dir 'D:/DouyinCut 测试/隔离目录'
~~~

首版测试入口不会替用户选择工作目录。缺少目录或前端资源时拒绝启动，不在另一个位置创建替代库。首次选择、目录身份验证及失效恢复属于工单 02。不要用本票测试程序运行真实录制或长时间云端任务。

## 运行边界

- DesktopSession 统一启动 API、任务 worker 和直播监控，在真实回环 API 读到当前格式项目后才报告就绪。可用端口由系统分配；重启轮换随机令牌。
- Python 运行时通过私有标准输入 / 输出管道与原生外壳握手；在原生进程接管完成、收到启动许可前不创建数据库或启动角色。令牌只进入会话内存；不放进 URL、前端构建或浏览器持久化存储。前端读取后删除注入的临时全局属性。
- 生产前端、API、SSE 和媒体使用同一回环来源，继续校验令牌 / HttpOnly 会话 Cookie 和 Origin，并为生产响应设置 CSP。生产配置不接受开发服务器来源。
- 子进程 stdout 诊断转入 stderr；原生外壳将 stderr 追加到隔离工作目录的 logs/desktop-runtime.log。不要把私有启动描述符保存为日志。
- 启动超时或关键角色异常退出会清理其余已启动角色；空闲退出等待三个角色结束。原生外壳另使用 Windows Job Object 持有进程树，避免外壳异常消失后留下自有子进程。

本票的空闲退出不替代工单 05—09 的活动任务协议：托盘、关闭窗口继续后台、显式退出确认、云端不等待与中断后直接重发、本地任务安全暂停及录制封存尚须独立实现和验收。

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
