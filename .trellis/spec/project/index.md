# DouyinCut 项目规格与文档入口

- 版本：0.2.158
- 日期：2026-11-20（Asia/Shanghai）
- 状态：当前开发与验收基线
- 平台：Windows；Python / uv、FastAPI、Vue 3 / pnpm、Tauri 桌面外壳

本索引及下列主题是当前开发、测试和验收的依据。变更流程、模型协议或产出目标时同步修改对应主题。真实云端能力与模型效果须单独验证，不能以模拟测试宣称通过。本索引顶部的版本是界面、原生程序、安装器和发布校验的统一应用版本来源。

## 阅读与维护

当前功能与协议以主题正文为准，开发与数据边界集中维护在 [AGENTS.md](../../../AGENTS.md)。[规格变更记录](../../../docs/SPEC_CHANGELOG.md) 仅用于追溯历史，不构成当前接口、兼容或数据迁移要求；[长期设计](../../../docs/FUTURE_DESIGN.md) 不构成本版验收要求。验收清单表示需要检查的项目，不表示已全部通过；实际证据与未验证范围分别记录。

| 阅读目的 | 规格主题（保留原章节编号） |
| --- | --- |
| 产品目标与当前流程 | [产品目标与使用前提](product.md)（§1） |
| 页面、视频项目、粗剪与剪映交付 | [页面与视频项目](workspace.md)（§2、§2.1—2.3） |
| 直播录制、讲解轨、商品切分及删除 | [直播监控](live-monitoring.md)（§2.4） |
| 云端、本地、星抖设置及运行日志 | [设置与日志](settings.md)（§2.5） |
| 模块、目录与运行边界 | [技术架构](architecture.md)（§3） |
| ASR、源时间映射、模型决策与剪辑 | [ASR 与全量对话决策](roughcut.md)（§4—5） |
| 开发数据策略、存储与媒体规则 | [数据与媒体](data-media.md)（原开发与数据策略、§6） |
| 本地接口及前端路由 | [接口与路由](api.md)（§7） |
| 任务恢复、认证与诊断 | [任务、认证与诊断](tasks-security.md)（§8） |
| 验收清单、未验证范围与后续 | [验收与待验证范围](acceptance.md)（§9、§11） |
| 源码运行、Windows 桌面、打包与发布 | [运行与发布](runtime-release.md)（§10） |

2026-11-15 按用户要求将原根目录 `SPEC.md` 按主题完整迁入此目录并删除原文件；原章节编号保留用于追溯，链接改指向对应主题。此迁移不改变业务规则或应用版本，旧会话中的 `SPEC.md §N` 可按上表定位。

## 相关文档

| 需要了解或维护的内容 | 文档入口 |
| --- | --- |
| 开发环境、启动、构建与测试入口 | [README.md](../../../README.md) |
| 开发和数据边界、按任务阅读约定 | [AGENTS.md](../../../AGENTS.md) |
| mini-trellis 日常使用、既有文档和历史会话接入 | [项目使用指南](../../../docs/agents/mini-trellis.md)；[通用记忆层说明](../guides/mini-memory.md) |
| 领域术语与决策理由 | [GLOSSARY.md](../../../GLOSSARY.md)、[领域文档约定](../../../docs/agents/domain.md)、[退出中断云端重试 ADR](../../../docs/adr/0001-exit-interrupted-cloud-retry.md) |
| Windows 会话、单实例与托盘生命周期 | [会话说明](../../../docs/windows-session.md)、[托盘说明](../../../docs/windows-tray.md) |
| 随包媒体工具、安装与更新发布 | [媒体工具](../../../docs/windows-media.md)、[安装包](../../../docs/windows-installer.md)、[更新发布](../../../docs/software-updates.md) |
| 本地工单与分诊状态 | [工单约定](../../../docs/agents/issue-tracker.md)、[分诊约定](../../../docs/agents/triage-labels.md)；工单正文在本机 `.scratch/` |
| 版本变化与过去的验证证据 | [规格变更记录](../../../docs/SPEC_CHANGELOG.md)、[开发状态快照（2026-10-20，0.2.49）](../../../docs/IMPLEMENTATION_STATUS.md) |
| 早期长期方案，实施前需重新确认 | [后续架构设计（历史方案留档）](../../../docs/FUTURE_DESIGN.md) |

研究发现和放弃方案按主题写入 [research](../../research/README.md)；会话交接写入 [workspace](../../workspace/index.md)，链接实际修改的主题文件。已有 ADR 或规格中的结论直接维护原文。

## 规格变更记录

原 §12 的全部版本条目保存在 [docs/SPEC_CHANGELOG.md](../../../docs/SPEC_CHANGELOG.md)。当前应用版本以本索引顶部为准；功能变化时同步更新受影响主题和变更记录，纯文档整理不提升应用版本。
