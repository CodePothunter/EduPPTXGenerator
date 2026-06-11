# 素材复用库交接文档（handover）

实习生（zsq）开发 AI 图片素材复用库系统后离职，留下约 13 份现状/设计文档，但**未推送到 git**——它们只在她的机器上。本目录用于归档这些交接文档，避免现状知识只存在于聊天记录。

## 待归档文档（来源：实习生本机 `doc_new/` 等）

核心交接：
- `current-status.md` — 功能总览与当前数据摘要
- `material-reuse-pipeline.md` — 复用管线（建库→split index→embedding→检索→review）
- `reuse-evaluation-and-tuning.md` — 评测与调参
- `素材复用分类标准.md` — C00-C03 四类边界判定
- `素材复用管线交接报告（现状总结）.md`
- `test_reuse 评测集与最新测试结果分析报告.md`
- `material_library_ppt构建测试存在问题分析.md`
- `planned-and-not-implemented.md`、`各种命令.md` 等

> 待用户把上述原始文档放入本目录。仓库内已有的相关 spec：
> `docs/superpowers/specs/2026-06-06-doc-new-refresh-design.md`（描述这 13 份文档"应写什么"）。

## 现状与改进结论（已审查并验证）

仓库根目录 **`v3-review-report-20260610.md`** 是对 v3 分支的独立深度审查报告，逐条核验了交接文档中的声明（架构/阈值/口径基本属实），并确认了 25 条缺陷与生产级改进路线（Phase A 地基修复 → B 数据层 pHash+sqlite-vec → C Chinese-CLIP 多模态 → D 评测重建）。

## Phase A 地基修复（本分支 `fix/v3-phase-a-hardening`）

已落地的安全修复（详见审查报告第七节与 git log）：
- 复用读路径总开关 `EDUPPTX_DISABLE_AI_IMAGE_REUSE`（一键回滚）
- embedding 查询编码失败不再静默吞掉（H-1）
- 显式关闭 embedding 时 policy_score 按可用权重归一化（M-1）
- 恢复豆包 volces.com 默认 `thinking:disabled`（M-6）
- C00 索引跨 run 增量合并（M-4，修"C00 只剩 1 条"）
- 入库 job_id 加内容指纹防撞车（M-5）
- 删除三处死代码（return-True 阈值桩 / pre-LLM floor / metadata-unknown 死分支）
