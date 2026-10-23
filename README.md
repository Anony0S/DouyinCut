# DouyinCut

Windows 服装视频语音粗剪工具。**输入视频主要讲同一款商品**。当前基线见 [SPEC.md](SPEC.md)，验证记录见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)。

主流程为选择视频 → 填写商品资料与剪辑要求 → ASR → 文本模型选取时间区间 → 拼接预览 → 调整并导出 MP4 / SRT。应用也支持剪映草稿生成，以及抖音直播监控和本机分段录制；当前粗剪依据口播文本，不做画面识别。

前端使用 Vue Router 4 + Pinia 3：`#/projects` 是分页项目列表，`#/projects/{asset_id}/edit` 是当前项目编辑页，另有 `#/jobs` 和 `#/settings`。项目列表的页码、搜索和状态筛选写入 hash 查询参数，因此刷新或复制地址可恢复列表位置；编辑页切换项目前会保存资料，未保存片段调整会阻止切换。

## Windows 开发启动

需要 uv、pnpm 10、Node.js 20.19+ 或 22.12+、FFmpeg / ffprobe，并将它们放入 PATH。Python 环境由 uv 管理，默认使用 Python 3.12；Rust / MSVC 仅在运行 Tauri 外壳时需要。

项目根目录是 uv workspace，`backend` 为成员包。`uv sync` 在根目录创建或同步 `.venv`，可编辑安装后端，使用根目录的 `uv.lock` 锁文件管理依赖；锁文件应提交版本控制。

前端在 `desktop/package.json` 固定使用 `pnpm@10.15.1`，包存储目录为 `.runtime/pnpm-store`。已有 `desktop/pnpm-lock.yaml`，应提交版本控制；使用 `pnpm install --frozen-lockfile` 进行可复现安装。

```powershell
# 在项目根目录安装 Python 与前端依赖
.\scripts\setup.ps1
# 可选：指定已有 Python 解释器，避免下载 Python
# .\scripts\setup.ps1 -Python 'C:\路径\python.exe'

# 单独同步 Python 依赖
uv sync --group dev

# 单独安装前端依赖
pnpm --dir desktop install

# 启动 API、独立 worker 和 Vue；会话密钥不写入文件
uv run python scripts/dev.py
```

打开 `http://127.0.0.1:5173`。Ctrl+C 停止开发服务。单独启动 Vite 不会获得开发会话令牌，应使用统一启动器。

普通 API 和任务实时事件共用本地会话令牌请求头。若提示“本地会话已失效”，刷新页面；若刚重启服务，停止原开发启动器，再通过 `uv run python scripts/dev.py` 统一启动并重新打开页面。实时连接不会因认证失效持续重连报 401，网络中断则自动退避重连。

## 当前操作流程

1. 默认进入“视频项目”，点击“新建视频项目”选择本地视频，探测完成后进入该视频的“素材与粗剪”；已有项目点击“查看 / 继续编辑”。列表每页 20 条，支持名称 / 商品搜索、状态筛选，按最近活动排序。
2. 填写商品名称，可补充面料、颜色、版型等；剪辑要求默认突出上身效果和面料、去掉价格，可自行修改。
3. 选择约 60 / 90 秒，点击“使用云端 AI 分析并粗剪”。文本模型始终参与，空白要求使用默认模板。
4. ASR 与选句完成后自动生成拼接预览；查看每段原话与入选理由，可播放源片段、删除、重排、微调切点。
5. 保存调整后等待新版本预览，再确认导出 MP4；当前编辑页下方“导出结果”展示 MP4 / SRT 位置、输出目录和可播放的历史成片。同目录包含内部时间线和清单。旧方案保留，可修改要求重新粗剪，不再提供独立“预览与交付”页。

每个源视频一个项目，编辑页只展示当前视频。同源多个方案保留在该项目内。商品资料与要求可点击“保存资料与要求”，离开编辑页或提交分析前也会保存；返回列表保留筛选与页码，重新打开恢复保存内容与最新方案。片段调整需点击“保存调整”；未保存片段时不能打开其他项目或新建视频。

文件选择会通过本机接口复制到数据目录，保留原文件，会额外占一份本地视频空间；原视频不上传到云端。上传云端的是识别所需的音频。导入使用当前文件选择与本地上传接口。

“上身效果 / 面料”按口播判断，本版不核查对应画面。多款混讲视频需用户先选成主要讲同款的素材。最终效果需要人工预览审核。

## 百炼与 OSS 设置

“云端设置”分为“模型连接”和“OSS”独立卡片，各自保存。

- 模型连接填写 Base URL、模型 API Key、文本剪辑决策模型与 ASR 模型。保存后，新分析和失败分析的重试使用最新设置；进行中 / 暂停恢复的任务保留提交快照。默认语音 API ID 为 `qwen-audio-3.1-asr-flash`。默认地址 `https://dashscope.aliyuncs.com/compatible-mode/v1`，不要追加 `/chat/completions`。
- 用户选择的默认决策模型为 `qwen3.8-flash`，ASR 为 `qwen-audio-3.1-asr-flash`，均可手动配置；真实账号权限与效果待验证。
- 文本请求使用 `<Base URL>/chat/completions`，音频使用同一主机的 `/api/v1/services/aigc/multimodal-generation/generation`，共用模型密钥。例如自有百炼主机配聊天兼容地址后会自动推导同主机原生音频接口。
- OSS 填 HTTPS endpoint、Bucket、RAM AccessKey ID / Secret，私有 Bucket 使用临时签名 URL。请配置 `douyincut/audio/` 生命周期；应用尚不自动删除远端音频。

本地 FFmpeg 提取最多 **120 秒**单声道 16kHz PCM WAV，尾段正常处理；小于 1 秒的探测尾差跳过，空 WAV 不会上传。ASR 请求 `input_audio.data`、`format: "wav"`、`sample_rate: "16000"`，禁用 SSE，单次等待 300 秒。30 分钟最多 15 次语音请求，串行更新进度。

响应支持顶层 / output 的 sentence / sentences，并避免重复解析镜像。长段带 words 时用真实词级时间与标点 / 停顿拆成短句，保留完整返回用于缓存重解析。句级毫秒转换为源微秒并加段偏移，**只有整段文本、无有效句级时间戳时明确报错，不伪造精确切点**。120 秒真实输入上限、多句返回完整性和语音边界精度仍待真实账号验证。

模型在整个视频转写完成后统一决策，返回源视频绝对 start_ms / end_ms 区间与理由；程序校验有效短句边界、重叠、重复、越界和总时长，再逐段裁剪拼接。不足一分钟时保留较短的完整内容并提示。截断句子的人工调整不会导出整句字幕，需要自行核查。

可勾选“重新识别语音”后再分析；此操作再次调用 ASR，成功结果仍会复用，不绕过不确定提交保护。界面提供全量 ASR 对话查看，上方显示原素材 / 源片段，下方显示拼接成片，方便对照。

成功 ASR 按源摘要、区间、模型与接口缓存，改变剪辑要求或文本选片重试可复用。401 / 403 / 429 等明确拒绝允许修正后重试；超时 / 408 / 5xx 保留不确定标记，停止自动重提，需核查云端请求记录。核查确认未产生云端结果后，在任务页或素材页点击“已核查云端，重试”，才会释放对应标记并重新排队。任务使用当前商品资料和模型连接配置。

文本决策等待默认 300 秒，可配置 30–900 秒，使用非流式、有界输出。新任务使用新设置，已有任务保留快照。自定义主机必须同时支持聊天文本及原生音频协议；密钥发送到填写的 HTTPS 主机，不转发到重定向地址。

## 密钥显示与错误详情

密钥以普通文本框平铺展示，保存后显示首尾与固定五个星号，如 `sk-exa*****1234`。点击或聚焦不清空；小眼睛查看完整值，旁边按钮复制完整已保存值或当前替换草稿。查看 / 复制不保存；需要替换时全选输入新值，留空保留旧密钥。模型与 OSS 凭据保存在本机应用 SQLite 配置中，列表接口仅提供缩略值，显式查看接口要求本地认证并禁止缓存；抖音 Cookie 使用同一设置页配置。

失败任务旁错误图标可悬停 / 聚焦查看完整详情，点击固定，支持滚动、选中复制和 Escape 关闭。区分客户端连接异常、云端 HTTP 响应和应用解析错误，保存脱敏的完整响应、实际请求头和请求体；为检查 `NO_VALID_AUDIO_ERROR`，`input_audio.data` 的完整临时签名 URL 会保留在本地错误详情中，Authorization 和其他密钥仍隐藏。RemoteDisconnected 表示未收到 HTTP 响应，不能据此推断云端拒绝原因；未收到正文时明确标注。HTML / 脚本按纯文本显示，任务列表与 SSE 不重复发送长正文。

后端更新后在原启动终端 Ctrl+C，再运行 `uv run python scripts/dev.py` 并刷新；本机设置保存在应用数据中。

## 无前端的核心命令

在根目录使用 uv 启动 CLI，无需激活虚拟环境或手动设置 `PYTHONPATH`。媒体链本身只依赖 Pydantic 和 FFmpeg；workspace 默认仍同步全部后端依赖。

```powershell
$env:DOUYINCUT_DATA_DIR = (Join-Path (Get-Location) 'data')
uv run python -m app.cli doctor
uv run python -m app.cli import 'C:\素材\直播.mp4'
# 复制并修改 scripts/recipe.example.json 的路径与真实时间区间
uv run python -m app.cli render scripts/recipe.example.json
```

`start_us` / `end_us` 为源素材整数微秒，区间左闭右开。配方声明的是人工确认的单款素材，CLI 不会验证真实画面是不是同款。输出保存到 `data/exports/`，包含成片、独立片段、字幕、时间线和清单。

## 检查与当前验证

```powershell
uv run python -m unittest discover -s backend/tests -v
Push-Location desktop
pnpm run test:roughcut
pnpm run test:secret-input
pnpm run test:events
pnpm run test:job-error
pnpm run test:routing
pnpm run build
Pop-Location
```

本轮后端回归测试通过（具体数量见开发状态），包含 API、缓存恢复与真实 FFmpeg 自动预览 / 字幕检查。前端类型检查、粗剪交互、密钥保存 / 显示 / 复制、事件认证、错误浮层和生产构建通过。云端为模拟响应，未发起真实付费请求，也未进行实际服装素材效果验收。

后端代码按职责分包：`backend/app/core` 放配置、领域类型、SQLite 和密钥处理；`ai` 放云端模型调用与剪辑决策；`media` 放本地媒体处理、时间线编辑和导出；`projects`、`live_monitoring` 分别承载项目与直播录制业务；`integrations` 放剪映等外部应用集成；`douyin_product_events` 封装抖音消息协议。`api.py`、`worker.py`、`cli.py` 保持为应用入口和编排层。

前端代码位于 `desktop/src`，按 `views`、`components`、`composables`、`stores`、`services` 分层；各类前端验证脚本集中在 `desktop/scripts`。视频播放、文件选择器和设置表单草稿保留在页面组件内，密钥明文不写入 Pinia 或浏览器存储。

本次直接使用项目 `.venv` 和现有前端依赖，未下载依赖；执行环境 PATH 未找到 uv，尚未验证本机 `uv sync`。Vite 使用 `pnpm exec node node_modules/vite/bin/vite.js build --configLoader runner` 验证构建。依赖锁文件已存在。

合成离线媒体演示可运行 `uv run python scripts/offline_demo.py`，产物位置记录在 `.runtime/offline-demo/result.json`；该配方演示不代表云端识别效果。

Tauri 当前仅为开发外壳：先启动统一启动器，再在 desktop 执行 `pnpm run tauri dev`。Python sidecar 与 Windows 生产安装包尚未完成。

## 开发约定与本次数据清理

以后添加或修改功能只维护当前 SPEC 和代码协议，不兼容应用旧版本。规则见 [AGENTS.md](AGENTS.md)；前后端与文档同步更新，不新增旧字段、旧任务 / 缓存或数据库迁移分支。同版本任务恢复、方案修订和防重复计费继续保留。

2026-10-14 已按要求清空本地旧视频业务记录、分析音频、预览、导出和任务缓存，保留云端设置、系统密钥和用户原始视频。OSS 远端对象未删除。以后不会因功能更新自动删除新素材。
