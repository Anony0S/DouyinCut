# 本地接口与前端路由

[返回项目规格与文档入口](index.md)。维护规则和迁移来源见索引；原章节编号保留用于追溯。

## 7. 本地接口

所有接口前缀 `/api/v1`，长任务返回任务 ID。只维护当前流程需要的接口。

| 接口 | 当前用途 |
| --- | --- |
| `POST /auth` | 本地会话 Cookie |
| `GET /projects` | page（默认 1）、page_size（默认 20，最大 100）、search、status；返回 items / total / page / page_size / pages |
| `GET /projects/{asset_id}` | 当前视频、资料草稿、同源方案 / 候选 / 产物 / 相关任务及最新分析 |
| `PATCH /projects/{asset_id}` | 保存 product_name、instructions、target_duration_us、duration_tolerance_us（0–30000000，默认15000000） |
| `DELETE /projects/{asset_id}` | 删除项目及本地专属数据；活动任务时拒绝，OSS 临时音频保留 |
| `POST /assets/upload?filename=...` | 认证的原始二进制流，本地复制与排队探测 |
| `GET /assets`、`GET /assets/{id}/media` | 素材查询与原素材范围预览 |
| `POST /assets/{id}/analyze` | product_name、instructions、target_duration_us、duration_tolerance_us、refresh_asr；快照模型配置 |
| `GET /plans`、`PATCH /plans/{id}`、`DELETE /plans/{id}` | 方案查询、保存新版本 / 自动排队预览、删除单个方案及其本地产物 |
| `POST /plans/{id}/approve` | 审核保存版本；当前界面不调用 |
| `POST /plans/{id}/exports` | 只接受 format=mp4，默认 mp4；当前界面不调用 |
| `POST /plans/{id}/jianying-drafts` | body 包含 revision 和可选 cover_id；后台生成该已保存修订的剪映工程草稿。cover_id 必须属于同一视频项目、方案和修订，带封面时创建新的草稿目录并返回任务 |
| `POST /product-platform/products/images` | body 包含 asset_id、platform_product_id；从当前星斗商品详情的 `data.images` 下载允许的 HTTPS 商品图片到该项目封面参考目录，返回本地图片 ID 和接口地址；不持久化带签名的原始图片 URL |
| `GET /assets/{asset_id}/cover-references/{image_id}` | 读取当前项目已下载的商品参考图 |
| `POST /assets/{asset_id}/covers` | body 包含 plan_id、revision、reference_id、prompt；校验当前已保存方案修订后排队 `cover` 任务，使用独立 OpenAI Images 兼容服务生成草稿列表封面 |
| `GET /assets/{asset_id}/covers/{cover_id}/media` | 读取成功封面任务的本地图片结果；封面与方案修订及视频项目绑定 |
| `POST /settings/cloud/short-product-name` | body 包含 product_name；使用已配置的文本模型返回简短商品名称，供封面弹窗写入生成要求 |
| `GET /exports`、`GET /exports/{id}/media` | 产物历史与播放 |
| `GET/POST /live/monitors`、`PATCH/DELETE /live/monitors/{id}` | 抖音监控项列表、添加、修改设置（auto_record / notify_live / notify_offline / notify_split_complete）、移除；活动录制期间拒绝删除 |
| `GET /live/monitors?window_start=…&window_end=…&include_end_recording=…` | 两个 Unix 秒时间戳须成对提供且开始早于结束；不提供范围时使用服务端最近 24 小时。按窗口返回每个直播间所有相交场次，不限 20 场。已结束场次要求 started < window_end 且 ended > window_start；录制中默认要求 started < window_end。include_end_recording=true 时额外包含恰好在 window_end 开始录制的场次；前端今天及历史日期窗口均发送 false，排除恰好次日 00:00 开始的录制。省略范围时采用服务端最近窗口语义。无效或非有限范围返回 422。列表内的场次只返回摘要：id、status、started、ended、end_time_source 与 product_error（该场次存在商品列表获取错误，或商品消息连接状态为 error），不含分段、讲解区间、商品片段等详情；详情按场次标识读取 |
| `GET /live/monitors/{monitor_id}/sessions/{session_id}` | 按稳定场次标识读取完整当前详情（分段、商品事件、讲解区间、商品片段、当前商品、监听状态与目录错误），不受时间窗口限制。成功读取但该直播间下无此场次时返回 200 / null，前端据此确认选择已失效；鉴权错误或存储失败保持非成功响应，不伪装成 null |
| `POST /live/monitors/{monitor_id}/sessions/{session_id}/product-listener/retry` | 当前录制场次在商品列表或商品消息连接错误时请求重试；由监控进程按错误类型刷新商品目录、重连消息或两者同时执行；已有目录且只有消息连接异常时不请求目录，重复请求或无可重试错误返回 409 |
| `DELETE /live/monitors/{monitor_id}/sessions/{session_id}` | 删除已结束场次的监控记录及未导入、未被项目引用的场次目录文件；录制中、导入中、商品片段活动任务或项目引用保护时返回 409；返回删除记录数、清理文件数 / 体积及保留文件数 |
| `POST /live/monitors/{id}/recording` | action=start / stop 手动开始或停止录制；未开播时开始返回 409 |
| `POST /live/segments/{id}/import` | 内部媒体块导入端点；当前界面不调用 |
| `POST /live/segments/{id}/open` | body 的 reveal=true 在资源管理器中选中文件，否则用系统默认播放器打开；仅限直播录制目录内的现有文件 |
| `DELETE /live/segments/{id}` | 内部媒体块清理端点；当前界面不调用，活动或被项目引用时返回 409 |
| `GET /live/product-periods/{id}/split-plan` | 获取单次讲解的来源分段、缺失 / 待封存状态及切分任务状态 |
| `POST /live/product-periods/{id}/product-clips` | body 的 confirmed_missing_segment_ids 确认缺失来源；按该次讲解已封存部分排队，部分缺失先返回确认信息，无新增区间返回 noop，全部来源不可用返回 409 |
| `POST /live/product-clips/{id}/import` | 将待导入商品片段排入 import 任务，成功后创建普通视频项目 |
| `POST /live/product-clips/{id}/open` | body 的 reveal=true 在资源管理器中选中文件，否则用系统默认播放器打开；仅限直播录制目录内的现有文件 |
| `DELETE /live/product-clips/{id}` | 删除商品片段记录；未导入时同时删除录制目录内片段文件，已导入时保留项目文件；生成中 / 导入中返回 409 |
| `GET /jobs`、`GET /jobs/{id}` | 任务状态；列表支持 limit / offset，或配对的 before_created / before_id 创建时间游标（游标不能与非零 offset 混用），按 created DESC、id DESC 排序；任务页请求 21 条，展示 20 条并据额外一条判断后续页 |
| `GET /jobs-summary` | 全部任务的 total、updated_at、active 汇总，避免通过有限任务列表计算活动数 |
| `POST /jobs/{id}/pause`、`resume`、`cancel`、`retry` | 任务控制；普通重试不解除 ASR 未确定标记 |
| `POST /jobs/{id}/acknowledge_asr_uncertain` | 用户核查云端请求记录后，释放当前素材 / 接口 / ASR 模型的未确定标记并重新排队；不删除成功缓存 |
| `GET /jobs/{id}/transcript` | 查看汇总全量 ASR 对话与源时间戳 |
| `GET /jobs/{id}/error-detail` | 完整脱敏错误详情 |
| `GET /events` | fetch SSE，jobs 事件发送任务汇总变化信号；共用认证、退避重连与卸载取消 |
| `GET/PUT /settings/editing-defaults` | 认证读取 / 保存本机默认剪辑要求 instructions（最多4000字符，空白重置为内置要求），不创建分析或调用云端；项目草稿与全局默认要求分别持久化 |
| `GET/PATCH /settings/cloud`、`POST /settings/secrets` | 文本字段 base_url、planner_model、chat_timeout_seconds、封面字段 image_base_url、image_model、image_timeout_seconds 与语音字段 asr_base_url、asr_model、asr_timeout_seconds 分开保存；密钥名 llm_api_key、image_generation_api_key、asr_api_key 分别使用系统凭据存储，配置加载仅返回各自状态及缩略值 |
| `POST /settings/cloud/test-model` | 本地认证后按 body.config 中的 Base URL、planner_model、chat_timeout_seconds 与可选 api_key 测试文本生成；空密钥使用已保存 llm_api_key。只发一次请求，成功与可预期测试失败返回 ok、model、elapsed_ms、message，不保存设置或密钥；非法配置返回 422，未授权返回 401 |
| `POST /settings/cloud/test-oss` | 本地认证后按 body.config 的 oss_endpoint、oss_bucket 与可选 access_key_id / access_key_secret 测试小对象上传；两项空密钥分别使用已保存凭据。返回 ok、elapsed_ms、message，不保存表单或创建任务；总等待不超过 30 秒及进程清理时间，未授权返回 401 |
| `POST /settings/secrets/{name}/reveal` | 显式获取完整密钥，禁止缓存 |
| `GET /product-platform/status` | 只返回星斗本机登录状态、脱敏账号和所选合作范围，不返回令牌 |
| `POST /product-platform/send-code`、`login` | body.mobile；登录另外包含 sms_code，验证码不持久化；发送成功后本服务会话内限制 60 秒再次发送 |
| `POST /product-platform/scope`、`logout` | 选择关联范围 main_ip_id，或清理本机登录；不登出用户其他浏览器 |
| `POST /product-platform/products/search` | body.keyword 为非空商品名称；返回 product（第一条安全字段或 null）与 scope_name；登录失效返回 401 + detail.code=PRODUCT_PLATFORM_AUTH_REQUIRED，缺少合作范围返回 409 |
| `POST /product-platform/products/add-to-showcase` | body.platform_product_id 为非空星斗商品 ID；后端从当前合作范围读取 `ipId` 与 `memberTalentId` 并执行单商品添加；返回 `{added: true}`，登录失效返回 401，缺少范围或达人关联返回 409 |
| `GET/PUT /settings/workspace-layout` | 素材与粗剪页的布局偏好：left_width 为左侧卡片宽度（像素，230–10000）或 null（默认宽度）；随工作目录保存，只保留最新值 |
| `GET /health`（无前缀）、`GET /diagnostics` | 服务存活检查与本机环境诊断 |
| `GET /logs` | 返回 files（非空日志文件：category=runtime / task、name、size、modified，任务日志另含 group、group_type=job / product_clip、job_kind、product_name）与 runtime_available；应用运行日志在前，任务日志按修改时间倒序 |
| `GET /logs/{category}/{name}?after=…` | 读取日志内容。不带 after 时返回末尾最多 512 KB 并从整行开始；带 after（字节偏移）时返回其后的新增内容。返回 text、start、next（下次读取的偏移）、size、truncated、reset（文件短于 after，已改为返回新内容末尾）。只接受两处日志目录内的 `.log` 文件名，运行日志仅限当前与上一份；名称无效返回 422，文件不存在返回 404 |
| `POST /logs/open` | body 的 category 必填、name 可选：提供 name 时在资源管理器中选中该日志文件，否则打开对应日志目录；仅支持 Windows |
| `DELETE /logs` | 清空应用运行日志、删除上一份运行日志及非活动任务的日志文件；返回 runtime_cleared、deleted_files、retained_files、bytes_reclaimed |
| `GET /products`、`GET /candidates`、`GET /assets/{id}/batches` | 辅助查询：商品、候选及素材 ASR 分段，编辑页不依赖 |

导入拒绝不支持后缀、空文件；使用随机存储名与原显示名，路径不接受文件名目录跳转，失败清理副本 / 临时文件。分析前校验商品名称、音轨、文本与语音各自的模型密钥和 OSS 配置。同素材活动分析返回 409。

### 7.1 前端路由与页面状态

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `#/`、未知路径 | 默认入口 | 跳转 `#/live-monitoring`；已知页面直接链接按对应路由打开 |
| `#/projects` | 视频项目 | 分页列表；`page`、`search`、`status` 作为可分享 / 可恢复的查询参数 |
| `#/projects/{asset_id}/edit` | 素材与粗剪 | 只加载当前源视频及其关联方案、产物、候选和任务 |
| `#/live-monitoring` | 直播监控 | 管理抖音直播间，查看检测状态、录制场次、商品讲解区间及商品视频片段 |
| `#/jobs` | 后台任务 | 全局任务状态和控制 |
| `#/appearance` | 外观 | 主题、窗口位置记忆及启动窗口大小 |
| `#/settings` | 云端设置 | 文本模型、语音模型、OSS 存储、直播登录与星斗商品平台独立管理；cloudTab=product-platform 直接定位星斗登录，表单草稿仅在当前页面生命周期内存在 |
| `#/local-settings` | 本地设置 | 工作目录管理和版本更新以列表行排列 |
| `#/logs` | 运行日志 | 查看、搜索、跟随和清理应用运行日志与任务日志 |

进入受保护页面先建立本地会话；路由切换会保存当前商品资料草稿。当前项目存在未保存片段调整时，切换到其他项目或新建视频会被阻止；保存失败时地址和编辑内容保持不变。导航中的异步项目加载使用序号校验，旧请求不能覆盖较新的页面。直接打开不存在的项目保留地址并显示可重试错误，不触发分析或其他付费请求。刷新页面只恢复已保存资料和方案，未保存片段不写入浏览器存储。

入口与导航保护的实现见 [router.ts](../../../desktop/src/router.ts)；工作台保存调整、预览和生成剪映草稿的界面见 [WorkspaceView.vue](../../../desktop/src/views/WorkspaceView.vue)。
