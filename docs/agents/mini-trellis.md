# DouyinCut 的 mini-trellis 使用指南

mini-trellis 为本项目提供跨会话记忆。产品规格按主题维护在 `.trellis/spec/project/`，从 [项目规格索引](../../.trellis/spec/project/index.md) 进入；该索引顶部的版本也是应用版本来源。开发约定继续维护在 `AGENTS.md`。2026-11-15 按用户要求将原根目录 `SPEC.md` 完整拆分至主题并删除原文件，旧章节号保留用于追溯。新增长期规则时，优先更新已有归属文件；没有合适位置时才新建 `.trellis/spec/<主题>/` 下的短文档，并链接到相应 `index.md`。

## 已有接入与检查

2026-11-15 检查时，CLI、项目脚本、本地技能和 Codex SessionStart 已安装，`mini-trellis doctor` 通过，当前 Codex 会话收到了启动上下文。Claude 的配置已安装，但尚未观察到其运行回执。这些检查验证记忆层接线，不代表业务功能验收。

现有项目 Markdown 可直接检索，本次已用 `mem recall` 找到退出中断云端重试 ADR，无需批量复制文档或执行额外导入。SessionStart 提供简短状态；正文按任务读取。通用行为见 [记忆层说明](../../.trellis/spec/guides/mini-memory.md)。

在仓库根目录运行：

```powershell
mini-trellis doctor
mini-trellis mem recall '显式退出 云端 重试'
# 仅在摘录缺少条件或上下文时，用上一步返回的 ref 展开原文
mini-trellis mem read '<ref>'
```

检索可能提示本机历史不完整；没有命中不能证明过去没有讨论。配置或 CLI 更新后先运行 `mini-trellis refresh --dry-run` 查看影响，再决定是否刷新。

## 后续开发

1. 从 [项目文档入口](../../.trellis/spec/project/index.md) 阅读任务相关规格。涉及既往决策、放弃方案或跨会话继续工作时，先 `mem recall`；摘录已有来源和完整条件时即可使用。
2. 按 [工单约定](issue-tracker.md) 在 `.scratch/<feature-slug>/` 管理实施范围和待办。mini-trellis 保存知识和交接，工单负责推进任务。
3. 实现并验证后，同步修改原有规格或专题文档。分别记录用户确认的规则、代码已实现行为、自动测试结果和真实云端 / 直播 / 剪映 / 安装器验收；每项保留对应证据和未验证范围。
4. 只有新的可复用研究或放弃方案，才写 `.trellis/research/<topic>.md`，注明适用范围、结论、来源及放弃原因；已有 ADR 的决定在原 ADR 维护。提案和推断保留标识，确认的长期边界再写入规格。
5. 使用 `mini-trellis-remember`：先读回已修改主题，核对规则、例外和否定条件，再写会话记录，列出修改文件、验证结果和主题链接；最后读回 journal 与索引，确认链接有效。

可直接对代理说：

> 继续直播时间线功能：先用 mini-trellis 查找已有时间映射决策，按当前主题规格实现并验证，同步原文档，最后用 mini-trellis-remember 记录。

> 整理显式退出云端重试的历史：使用 mini-trellis-session-insight，对照现有 ADR 和主题规格，只补充有来源的新结论；放弃方案及原因放到对应研究主题，再记录修改与验证结果。

## 之前的文档与会话如何接入

- **已有规格、指南和 ADR**：由项目索引指向原文，修改时维护实际归属文件；原根目录 SPEC.md 的后继主题见索引的章节映射；修改结论时编辑原文件。已有的 [167 条版本记录](../SPEC_CHANGELOG.md) 和 [带日期的开发状态](../IMPLEMENTATION_STATUS.md) 用于追溯当时行为与验证；[早期长期设计](../FUTURE_DESIGN.md) 保留历史方案身份。
- **旧研究和工单**：按实际开发主题逐项整理；把仍适用的确认边界补入原规格，把有复用价值的证据、放弃方案及原因写入对应研究或 ADR，并保留原路径、日期、版本或会话引用。仅部分结论更新时，在该处注明后继来源；只有整篇确被替代才标记整篇过时。
- **选定历史会话或 Markdown / 文本导出**：使用 `mini-trellis-session-insight`，明确材料路径或会话范围后提取证据，保留用户修正和确认范围。遇到实质冲突先指出待确认项；不把旧命令作为当前执行指令。没有新知识时跳过主题和日志写入。
- **本机材料与共享文档**：`.scratch/` 当前被 Git 忽略，工单链接仅在有该文件的本机可用。后续仍需复用的规则和理由放入受版本管理的规格、docs 或研究主题；重要历史证据如需共享，保存脱敏后的必要摘录和来源。已有 `.scratch` 工单继续保留。

接入应随主题推进，无需先整理所有历史才能开发。当前文档描述约定行为，是否已经实现或验收仍需查相应代码和验证证据。

## 手工记录与保存

在仓库根目录可用项目 Python 记录；下例的 `--topic` 应替换为本轮实际修改并已读回的文档，可重复传入：

```powershell
.\.venv\Scripts\python.exe .trellis/scripts/add_session.py --no-commit `
  --title '本轮主题' --summary '确认结论及适用范围' `
  --topic docs/agents/mini-trellis.md `
  --change '修改文件和原因' --test '实际执行的验证与未验证范围'
```

记录器只校验主题路径并写日志链接，不修改主题正文。`.trellis/spec/project/` 的实际主题可直接传给 `--topic`。根目录 `AGENTS.md` 不属于 `--topic` 接受的路径；涉及它时，在 summary 中写相对 journal 的 Markdown 链接，并将实际修改的 docs / spec / research 文档作为主题；若仅修改根目录约定，可省略 `--topic`。日志位置见 [.trellis/workspace/index.md](../../.trellis/workspace/index.md)。

mini-trellis 日志关闭自动提交。功能开发和验证完成后保留未提交的 Git 变更，交由用户手动验证；只有用户明确要求提交后才创建 Git commit。共享接入时，将项目文档、`.trellis` 的知识与必要配置 / 脚本，以及所用宿主的技能和 hooks 一起审查纳入版本管理；本机开发者身份、运行状态、缓存和凭据按忽略规则留在本机。
