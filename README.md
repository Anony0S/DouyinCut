# DouyinCut

DouyinCut 是一款面向 Windows 的商品视频剪辑工具。它根据视频中的口播内容和用户填写的剪辑要求，辅助选择片段、生成粗剪预览，并导出成片和字幕。项目也提供抖音直播监控、本地录制与商品讲解片段管理。

> 项目仍在开发中。当前规格和验收边界以 [SPEC.md](SPEC.md) 为准；真实云端服务、直播平台和剪映版本的效果需要在实际环境中验证。

## 功能

- **视频项目**：按源视频管理素材、商品资料、剪辑要求和多个粗剪方案。
- **语音粗剪**：使用云端语音识别获取带时间戳的口播，再由文本模型根据要求选择源视频区间。
- **预览与导出**：查看入选理由和源片段，调整切点、顺序后预览，导出 MP4 和 SRT；也可生成剪映草稿。
- **直播监控与录制**：监控抖音直播间，在本机录制并记录商品讲解区间，手动切分后可导入视频项目。
- **讲解轨道预览**：悬停查看已封存录制的画面和时间，右键打开所在录制文件，并按场次时间显示刻度。
- **任务管理**：查看导入、分析、预览及导出任务的进度，并按任务状态进行暂停、恢复或重试。

粗剪以口播文本为依据，不识别画面内容。建议使用主要讲解同一款商品的视频，并在导出前人工检查画面、切点和字幕。

## 工作流程

1. 在「视频项目」中新建项目并选择本地视频，或从直播录制中导入商品片段。
2. 填写商品名称、剪辑要求、目标时长和允许偏差。
3. 启动云端分析，等待语音识别、文本选片和本地预览完成。
4. 对照原视频检查片段，按需调整并保存。
5. 确认预览后导出 MP4 和 SRT，或生成剪映草稿。

视频导入时会复制到本地工作目录，原文件不会被修改或作为视频上传；语音识别所需音频会上传至配置的 OSS，再由云端识别服务读取。云端调用可能产生费用。

## 环境要求

源码开发使用 Windows，需安装：

- Python 3.12 和 [uv](https://docs.astral.sh/uv/)。
- Node.js 20.19+ 或 22.12+，以及 pnpm 10（项目声明使用 `pnpm@10.15.1`）。
- FFmpeg 和 ffprobe，并确保开发环境可以找到它们。
- 使用 Tauri 桌面外壳时，需要 Rust、Windows MSVC 构建工具和 WebView2。

`scripts/setup.ps1` 会在检测到 Go 时尝试构建可选的商品事件桥；该模块声明 Go 1.26 和 `go1.26.8` 工具链。当前直播商品连接使用 Python，缺少 Go 不影响安装 Python 和前端依赖。

## 本地开发

在仓库根目录执行：

```powershell
# 安装 Python 和前端依赖
.\scripts\setup.ps1

# 启动本地 API、后台 worker 和前端
uv run python scripts/dev.py
```

浏览器打开 `http://127.0.0.1:5173`，使用 `Ctrl+C` 停止服务。开发会话使用本地认证，请通过上述统一入口启动，而不是只启动 Vite。

运行 Tauri 开发外壳：

```powershell
cd desktop
pnpm run tauri dev
```

Tauri 会自动启动开发服务，请勿同时运行上面的统一启动器，以免端口冲突。

## 云端配置与数据

在应用的「云端设置」中分别配置文本剪辑模型、语音识别模型和 OSS 存储。语音服务需支持当前 Qwen 音频协议及句级时间戳；直播商品功能可按需配置抖音登录信息，星斗商品平台需要单独登录和授权。

- 模型服务和 OSS 配置由用户提供，调用前请确认账号权限和费用。
- 音频对象的保留时间由 OSS Bucket 生命周期管理；建议为 `douyincut/audio/` 设置过期规则。
- 模型与 OSS 密钥、抖音 Cookie 保存在本机应用配置中；星斗登录令牌使用 Windows 凭据管理器。请保护工作目录与系统账户，避免将配置、日志或凭据上传到公开仓库。
- 当前开发版本不提供历史应用数据格式的兼容或迁移。涉及数据清理时应先备份，且不会因为升级自动授权删除用户原始视频。

具体协议、任务恢复和数据策略见 [SPEC.md](SPEC.md)。

## 构建 Windows 安装包

在满足 Rust、MSVC、uv、Node.js、pnpm 和 Inno Setup 要求，并准备好项目锁定的媒体工具后，可在仓库根目录运行：

```powershell
.\scripts\build-windows-installer.ps1
```

安装器输出位于 `desktop/src-tauri/target/release/installer/`，版本号从 [SPEC.md](SPEC.md) 读取。构建脚本默认跳过验证测试；如需执行其内置检查，使用 `-SkipTests:$false`。准备媒体工具及安装器维护步骤分别见 [Windows 随包媒体工具](docs/windows-media.md) 和 [Windows 安装包指南](docs/windows-installer.md)。

目前安装、升级和卸载的完整生命周期尚未完成验收，应用内检查更新也尚未接入在线服务。随包 FFmpeg 构建的完整对应源码及再分发材料尚未齐备，公开分发安装包前须完成依赖许可与发布验收。

## 测试

```powershell
# 后端测试
uv run python -m unittest discover -s backend/tests -v

# 前端类型检查与生产构建
pnpm --dir desktop run build

# 示例：直播轨道与商品平台前端检查
pnpm --dir desktop run test:live-product-rail
pnpm --dir desktop run test:timeline-thumbnail-scheduling
pnpm --dir desktop run test:product-platform
```

更多前端检查命令列在 [desktop/package.json](desktop/package.json)。模拟测试不代表真实云端接口、付费调用或安装包验收已通过。

## 项目结构

```text
backend/             Python API、任务、媒体处理与业务模块
desktop/             Vue 前端、Tauri 桌面外壳和前端检查脚本
packaging/windows/   Windows 安装与媒体资源配置
scripts/             开发启动、构建和验证脚本
docs/                架构、Windows 构建与验收文档
SPEC.md              当前产品和技术规格
```

开发约定见 [AGENTS.md](AGENTS.md)，术语见 [GLOSSARY.md](GLOSSARY.md)。

## 第三方组件

项目使用多种第三方依赖，包括随源码保留许可文件的 [pyJianYingDraft](backend/app/_vendor/pyJianYingDraft/LICENSE)。抖音商品事件相关说明见 [第三方声明](backend/app/douyin_product_events/THIRD_PARTY_NOTICES.md)；媒体资源来源和再分发状态见 [Windows 随包媒体工具](docs/windows-media.md)。
