# 项目开发约定

- 以后添加或修改功能，只以当前 SPEC.md 与当前代码的数据格式为准，不兼容应用旧版本。
- 不新增旧接口、旧任务、旧缓存、旧模型配置或旧数据库结构的兼容 / 迁移分支；调整协议时同步前后端及文档。
- 开发阶段允许按用户明确要求清空旧业务数据；不要因此删除用户原始视频、云端连接设置或系统凭据。
- 继续使用 Python / uv、Vue / pnpm。功能与流程变化同步修改 SPEC.md。
- 当前流程：单商品本地视频 → 全量 ASR 对话与源时间戳 → 决策模型返回时间区间 → 裁剪拼接预览 → MP4 / SRT。
- 默认从“视频项目”分页列表进入；一个源视频一个项目，同源多方案归入该项目，素材与粗剪只加载当前项目。商品资料 / 要求草稿与片段方案分别保存。

“不兼容旧版本”指本应用历史版本；当前依赖的 Windows、浏览器 / WebView、模型 API 支持和剪映工程实际兼容性仍按需求验证。

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo using root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.
