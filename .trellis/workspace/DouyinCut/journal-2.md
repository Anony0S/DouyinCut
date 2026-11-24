# Journal - DouyinCut (Part 2)

> Continuation from `journal-1.md` (archived at ~2000 lines)
> Started: 2026-11-24

---



## Session 79: 粗剪选片的 ASR 句界纠正
<!-- trellis-session: v=2 fp=37c94612b3f5d7bf -->

**Date**: 2026-11-24
**Task**: 粗剪选片的 ASR 句界纠正

### Summary

正式版失败任务中首次选片的第一段使用了 ASR 中不存在的结束时间。文本决策沿用原始转写并反馈具体无效时间戳，句界与时长共用最多两次纠正。已通过粗剪、云端诊断和核心测试。
Topics: [.trellis/spec/project/roughcut.md](../../spec/project/roughcut.md)

### Git Commits

(No commits - planning session)

### Status

[OK] **Completed**


## Session 80: 粗剪模型返回转写句子索引
<!-- trellis-session: v=2 fp=88b2315d1258c578 -->

**Date**: 2026-11-24
**Task**: 粗剪模型返回转写句子索引

### Summary

模型使用本次提交的转写数组索引选择首尾句，后端查表获取实际毫秒边界。索引与时长共用两次纠正机会。已通过粗剪、诊断、缓存和任务恢复测试。
Topics: [.trellis/spec/project/roughcut.md](../../spec/project/roughcut.md)

### Git Commits

(No commits - planning session)

### Status

[OK] **Completed**
