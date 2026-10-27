# Windows 随包媒体工具（工单 03）

## 构建输入

锁定 8.1-full_build-www.gyan.dev Windows x64 静态构建。当前输入来自构建机已安装的 Gyan.FFmpeg WinGet 包；本地上游 README 标注 GPL v3 与 FFmpeg 源码提交 `9047fa1b084f76b1b4d065af2d743df1b40dfb56`。packaging/windows/media-manifest.json 记录来源、四个文件的 SHA-256 和需要的编码器，不根据 PATH 自动选择别的版本。

先取得该版本的完整解压目录，使用显式路径准备工具：

~~~powershell
./.venv/Scripts/python.exe scripts/prepare-windows-media.py --source 'C:/构建依赖/ffmpeg-8.1-full_build'
./scripts/build-windows-session.ps1 -MediaSource 'C:/构建依赖/ffmpeg-8.1-full_build'
~~~

媒体准备步骤另外要求版本匹配的 `packaging/windows/media-sources.json`。其中发布资产摘要与 FFmpeg 完整提交用于来源追踪，观测到的构建开关和依赖版本用于复核；当前文件明确记录 `redistribution_ready: false`，它不是完整 SBOM、对应源码归档或许可证包。

准备阶段验证 bin/ffmpeg.exe、bin/ffprobe.exe、LICENSE、README.txt 的哈希，并实际枚举 libx264、aac、pcm_s16le、mjpeg 编码器。验证成功后复制这四个文件、来源清单和资源 manifest 到 `.runtime/windows-packaging/media/`。冻结运行时再核验来源清单哈希。PyInstaller 使用显式资源目录，构建前再次核对清单。二进制不提交到 Git。

冻结输出 _internal/media/ 包含四个文件与 manifest.json。生产运行时先验证全部文件哈希，再以绝对路径传给三个后台角色。冻结程序忽略开发环境中的 DOUYINCUT_FFMPEG / DOUYINCUT_FFPROBE，资源缺失或损坏直接报错，不回退全局工具。源码开发继续允许 Settings / 环境变量指定工具。

## 许可证与对外发布边界

本次保留上游 GPL v3 LICENSE、包含构建配置及来源的 README.txt，并随冻结资源分发。它们证明本地输入的许可证声明，不能代替完整的再分发准备。

来源研究记录在 `.scratch/windows-packaging/research-media-sources.md`。Gyan 8.1 release API 提供了完整构建 ZIP 的上游 SHA-256；它属于整个归档，不等于当前 exe 的摘要，本机未下载该归档进行逐项比对。FFmpeg 主源码固定到上述完整提交；对应源码压缩包和固定许可证 URL 可由上游 API / 仓库定位。本轮下载尝试遇到 TLS 握手超时，没有取得可验证的本地源码归档。

本地 README 列出 70 个外部库版本，FFmpeg 实测 build configuration 记录 102 个开关；依赖表仍含未固定的 `openal-soft latest`。逐项的源码、确切许可证原文、构建脚本、补丁和工具链尚未收齐，也未验证其与当前二进制构建完全对应。机器可读的 `media-sources.json` 将这些内容与待补资料区分，并保持 `redistribution_ready: false`。不得把来源记录、主 FFmpeg 源码链接或该布尔值以外的局部材料说成完整对应源码或许可审查结论。

对外发布安装包前必须取得并核验该固定构建的完整可访问对应源码、构建材料及相关许可证，按实际分发方式核对义务，再由明确的发布验收把媒体依赖记录为可再分发。上游材料缺失时，需要先选择并重新固定可追溯构建，再完整回归；不可静默替换依赖。当前冻结包只用于本机开发验收，不能据此标记许可证交付已完成。

## 无付费媒体验收

~~~powershell
./.venv/Scripts/python.exe scripts/verify-windows-media.py --runtime desktop/src-tauri/runtime/DouyinCutRuntime/DouyinCutRuntime.exe --keep-output .runtime/m03
~~~

--keep-output 必须是不存在的隔离目录；不指向真实工作数据。省略时自动使用临时目录。验证器复制冻结程序，在中文 / 空格工作路径运行，PATH 只保留 Windows 系统目录，同时将媒体环境变量设为不存在的程序，证明生产包使用随包工具。

验收使用 6 秒红 / 蓝画面及 440 / 880 Hz 音轨，真实上传、探测、任务执行、认证 Cookie、SSE、视频 Range、方案调整 / 审核、预览与最终 MP4 / SRT 均走冻结生产 API。通过解码画面颜色、音频过零次数、音视频时长及字幕顺序核对内容；比较哈希确认原视频和历史成片保持不变。缺少工具时验证启动失败且没有后台角色。

云端仅在运行时停止时写入当前格式的已成功 ASR / 决策缓存，模型域名使用 .invalid，进程注入假凭据，实际付费请求为零。没有新增生产 mock 模式或绕过认证。此项不覆盖真实 OSS 上传、模型传输、计费及退出重发。

源码测试在公开导出 / worker 边界注入文件系统写入拒绝，验证原视频和已发布成片不被清理。任务错误明确显示输出目录及权限、空间、占用排查提示，临时目录按当前机制清理。

自动验收报告 acceptance.json 区分“生产前端静态资源已服务”与“原生界面已点击”。前端导入、方案保存、审核和导出语义由现有 Vue 行为脚本回归；受限环境中的 WebView 自动交互仍有缺口。普通桌面用户确认单独记录到 manual-acceptance.json，不能把行为脚本记作实际原生界面验收。

## 2026-10-27 验证结果

最终报告为 .runtime/m03-final/acceptance.json，包含正常退出和缺工具拒绝检查；仅在全部检查通过后发布 passed。后端完整 225 项测试、前端全部 11 组脚本、Vue 类型检查、生产构建、冻结基础会话、Rust SessionHost 集成测试通过。规范 / 规格复审发现的上传错误反馈和报告时机问题已修复并复核。

首次较长路径验收第二次预览发生过 PermissionError；原路径再次导出和后续更长目录完整冻结复验成功，当前无法稳定复现。没有证据确定路径长度或占用为根因，未加入盲目重试；现有日志可记录后续媒体文件系统异常。

普通桌面入口为项目根目录 launch-media-check.cmd，使用 .runtime/m03-final/desktop-check/ 独立程序副本、独立 WebView profile 和合成工作区。验收步骤为打开“用户 原视频.mp4”项目，核对源视频、已有预览 / 成片可播放、字幕和路径可见，再执行一次当前已审核方案导出；可另导入 .runtime/m03-final/用户 原视频.mp4 验证文件选择。不要在测试配置启动云端分析，也不要选真实素材。

2026-10-27 用户反馈“我已经进行测试了，功能正常”，记录所要求的普通桌面播放 / 导出流程人工验收通过，证据见 .runtime/m03-final/manual-acceptance.json。再次导入属于此前可选步骤，用户没有逐项说明，故不单独宣称该步骤或全部支持格式已经人工实测。自动报告保留其原有 not_automated 标记；完整对应源码及外部许可证仍未完成。

### 2026-10-27：来源清单与隔离导入验收准备

- 新增 `.scratch/windows-packaging/research-media-sources.md` 与 `packaging/windows/media-sources.json`，固定完整 FFmpeg 提交、Gyan 发布 ZIP 的上游摘要、实测构建配置和 README 中的 70 个外部库版本；源码包未能经本轮 TLS 下载验证，所有未收齐资料明示为缺口，并将 `redistribution_ready` 保持为 false。
- 构建准备 CLI 现在要求版本及 README 摘要匹配的来源清单，并将其纳入冻结运行时哈希校验；缺失、错版或篡改时拒绝构建 / 启动。新增公开 CLI 验证 6 项通过。已用固定媒体工具重备资源，并重建冻结运行时；完整媒体验收再次通过，真实付费请求数为 0。
- 原生文件选择器导入仍未由用户验收。运行 `uv run python scripts/prepare-windows-media-import-check.py` 会生成新空工作区、测试版及 `launch-import-check.cmd`；测试输入是合成 6 秒 160×120 视频，云端设置指向 `.invalid`。请在退出所有其他 DouyinCut 窗口后启动该 launcher，在“新建视频项目”选择 fixture 输出中 `synthetic_video` 指向的文件，等项目加载并确认显示 6 秒及 160×120，然后通过托盘菜单退出；不要点击云端分析。成功后原文件 SHA-256 不变、导入副本与项目素材由隔离工作区接收。此项仍需真实 Windows 文件选择器桌面操作，自动 API 上传不替代它。
- GPL 对应源码、外部库精确许可证及完整构建材料仍未齐备，工单 03 继续保持 `ready-for-human`；工单 05 因 03 未完成，暂不能开始。
