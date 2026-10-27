# Windows 随包媒体工具（工单 03）

## 构建输入

锁定 8.1-full_build-www.gyan.dev Windows x64 静态构建。当前输入来自构建机已安装的 Gyan.FFmpeg WinGet 包；本地上游 README 标注 GPL v3 与 FFmpeg 源码提交 9047fa1b08。packaging/windows/media-manifest.json 记录来源、四个文件的 SHA-256 和需要的编码器，不根据 PATH 自动选择别的版本。

先取得该版本的完整解压目录，使用显式路径准备工具：

~~~powershell
./.venv/Scripts/python.exe scripts/prepare-windows-media.py --source 'C:/构建依赖/ffmpeg-8.1-full_build'
./scripts/build-windows-session.ps1 -MediaSource 'C:/构建依赖/ffmpeg-8.1-full_build'
~~~

准备阶段验证 bin/ffmpeg.exe、bin/ffprobe.exe、LICENSE、README.txt 的哈希，并实际枚举 libx264、aac、pcm_s16le、mjpeg 编码器。验证成功后只复制这四个文件与清单到 .runtime/windows-packaging/media/。PyInstaller 使用显式资源目录，构建前再次核对清单。二进制不提交到 Git。

冻结输出 _internal/media/ 包含四个文件与 manifest.json。生产运行时先验证全部文件哈希，再以绝对路径传给三个后台角色。冻结程序忽略开发环境中的 DOUYINCUT_FFMPEG / DOUYINCUT_FFPROBE，资源缺失或损坏直接报错，不回退全局工具。源码开发继续允许 Settings / 环境变量指定工具。

## 许可证与对外发布边界

本次保留上游 GPL v3 LICENSE、包含构建配置及来源的 README.txt，并随冻结资源分发。它们证明本地输入的许可证声明，不能代替完整的再分发准备。

当前网络受限，尚未在线核对下载档案、源代码归档、全部静态链接外部库的对应版本 / 许可证与完整 Corresponding Source。清单只记录实测文件哈希，不声称是上游发布的档案校验值；不猜测下载 URL，也不把 FFmpeg 源码链接当作完整源代码交付。

对外发布安装包前必须在工单 11—12 整理可访问的对应源代码、构建材料及相关许可证，按实际分发方式核对 GPL 义务，再把媒体依赖记录为可再分发。当前冻结包只用于本机开发验收，不能据此标记许可证交付已完成。

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
