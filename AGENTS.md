# 项目开发约定

## 开发与数据边界

- 添加或修改功能前，阅读 [项目规格索引](.trellis/spec/project/index.md) 及相关主题，以当前规格与当前代码的数据格式为准；功能、流程或协议变化同步修改前后端与规格。版本变更记录仅供追溯，长期设计不作为已实现能力。
- 继续使用 Python / uv、Vue / pnpm。
- 不兼容本应用历史版本，不新增旧接口、旧任务、旧缓存、旧模型配置或旧数据库结构的兼容 / 迁移分支。同版本任务恢复、方案修订与成功缓存继续保留；Windows、浏览器 / WebView、模型 API 支持和剪映工程实际兼容性仍按需求验证。
- 清空旧业务数据须有用户明确要求；保留用户原始视频、云端连接设置和系统凭据。
- 一个源视频一个项目，同源多方案归入该项目；素材与粗剪只加载当前项目。商品资料 / 要求草稿与片段方案分别保存。
- 自动测试、真实云端 / 直播效果和外部应用兼容验收分别记录，按证据声明完成范围。
- 功能开发和验证完成后保留未提交的 Git 变更，交由用户手动验证；仅在用户明确要求提交后创建 Git commit。

## 按任务阅读

产品入口见 [产品目标](.trellis/spec/project/product.md)，单商品 ASR 粗剪与剪映草稿交付见 [页面与视频项目](.trellis/spec/project/workspace.md) 和 [ASR 与决策](.trellis/spec/project/roughcut.md)；本地接口和路由见 [接口规格](.trellis/spec/project/api.md)，运行、发布及待验证范围见 [运行与发布](.trellis/spec/project/runtime-release.md) 和 [验收清单](.trellis/spec/project/acceptance.md)。具体流程集中维护在相应主题中。

- 新建或读取工单：按 [本地 Markdown 工单约定](docs/agents/issue-tracker.md)，使用 `.scratch/<feature-slug>/spec.md` 和独立的 `issues/<NN>-<slug>.md`。
- 设置分诊状态：按 [分诊标签约定](docs/agents/triage-labels.md)，使用 `needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。
- 每次正式发布前，根据本次变更选择应用版本号，并同步规格索引和规格变更记录。[语义化版本规则](docs/software-updates.md#语义化版本规则)
- 探索领域或修改术语 / 决策：先读 [领域文档约定](docs/agents/domain.md)、根目录 [GLOSSARY.md](GLOSSARY.md) 及相关 `docs/adr/`；术语保持一致，冲突须明确提出。
- 跨会话继续、接入历史材料或记录确认结论：按 [mini-trellis 项目指南](docs/agents/mini-trellis.md)，从 [项目文档入口](.trellis/spec/project/index.md) 找到并维护原文。

<!-- TRELLIS:START -->
# mini-trellis

This project uses mini-trellis as a memory layer. Working knowledge lives under `.trellis/`:

- `.trellis/spec/` — durable contracts (short markdown; read `index.md` first)
- `.trellis/research/` — topic inbox (`<topic>.md`; `README.md` is not a topic)
- `.trellis/workspace/` — per-developer journals

Record a session with `/mini-trellis:remember` (Claude/OpenCode), `/mini-trellis-remember` (Pi/OMP/Kiro), `$mini-trellis-remember` (Codex/ZCode), or the `mini-trellis-remember` skill (Cursor and DeepSeek Harness). When earlier decisions or relevant project rules are needed, use `mini-trellis mem recall "<topic or path>"`; expand evidence with `mini-trellis mem read "<ref>"`. No search is required for unrelated work. Promote lasting boundaries into spec with `mini-trellis-update-spec`.

Record only confirmed decisions, reusable research findings, or the session/compact handoff. Skip ordinary turns and unconfirmed guesses. New installs write notes without an automatic git commit; use `mini-trellis doctor` to check host wiring and `mini-trellis refresh --dry-run` before refreshing assets.

Keep confirmed rules short and faithful. Unconfirmed scope, exceptions, and implementation choices stay open or are labeled as inference, not added to the contract.

For a confirmed update, follow this short order: read the existing topic; edit that file; read it back and check the new rule, limits, and negations; run `python3 ./.trellis/scripts/add_session.py --title "..." --summary "..." --topic <actual-topic-path>`; read the journal and check its topic link. Journal-only success is not topic success; report partial writes accurately. New notes follow the requested or discussion language; updates preserve the document language.

When recall excerpts already answer the question with source and qualifiers, cite them and stop. Expand only missing context; do not repeat a lookup or fall back to dialogue-only `search` merely to reconfirm sufficient evidence.

ZCode, Kiro and OMP conversation histories are not parsed; use project knowledge or supply Markdown/text exports. For an explicit request to organize selected past sessions or supplied Markdown/text exports into project knowledge, use `mini-trellis-session-insight`; update existing documents and preserve sources.

mini-trellis does not create or drive tasks.

Managed by mini-trellis. Edits outside this block are preserved.
<!-- TRELLIS:END -->
