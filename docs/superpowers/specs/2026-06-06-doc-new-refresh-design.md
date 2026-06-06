# doc_new 全量刷新设计

日期：2026-06-06

## 目标

对 `doc_new/` 全部 13 个文件做一次代码现状对齐。更新时以当前代码、测试和当前素材库数据为准，同时参考 `6_5report/`、`docs_未整理版本/`、`reviewA_rubric_v25/` 和现有 `docs/`。目标不是重写成一篇总报告，而是让每个 `doc_new` 文件单独阅读时都不会传达过期或错误状态。

本次新增一个明确要求：分析 `materials_library_ppt` 数据库现状，包括 split index 数量、字段缺失、图片缺失、embedding sidecar 状态、teach-kb 覆盖缺口，以及后续需要更新或重建的项目。

## 当前核对结论

当前代码事实：

- active 素材分类仍是 C00-C03 四类，不是旧 13 类、C00-C05 或 C00-C11/C99 草案。
- active review prompt 是 `AI 图像复用审核评分规则(v2.5.3:A.3 窄召回补偿)`。
- review 输出 schema 仍是 `score / brief_reason / evidence / risk_factors`，Stage B `core_elements` 未启用。
- policy 阈值仍是 `T_DIRECT=0.75 / T_REJECT=0.35 / T_GAP=0.02`。
- LLM review 单阈值是 0.60，每 query 预算是 5，review 排序 embedding-first。
- 当前 `scripts/` 只保留 6 个脚本：`build_ppt_materials_library.py`、`dedupe_ppt_materials_library.py`、`dry_run_general_classify.py`、`dry_run_llm_classify.py`、`rebuild_ppt_materials_embeddings.py`、`report_missing_pptx_materials.py`。
- 多个历史维护脚本已删除，不能继续出现在命令速查中作为可执行入口。

`materials_library_ppt` 当前数据事实：

- 没有顶层 `ai_image_asset_db.json` 和 `ai_image_match_index.json`；运行入口是 `strict_reuse_indexes/`。
- split index 当前数量：`background=572`、`C00=1`、`C01=690`、`C02=3592`、`C03=859`，合计 5714 条索引记录。
- page_image 主桶字段基本齐全；C02 有 1 个资产 `kbpptx_26fa1b14fc58c8cdd6e6` 缺 caption，且图片路径和 original 路径均缺失。
- background 桶 572 条都没有 caption，但背景检索使用 `normalized_prompt`；其中 568 条有 `normalized_prompt`，4 条缺失。
- 旧字段 `content_prompt`、`detail_prompt`、`core_keywords`、`constraints`、`semantic_aliases`、`visual_reuse_group`、`vlm_caption`、`vlm_general` 在 split index 中未发现残留。
- 有 2 个重复 asset_id，但它们是 C01 canonical 与 C03 去名 projection，不应按普通重复处理。
- `ai_image_embedding_meta.json` 记录 `asset_count=5706`，而当前可嵌入 split 记录约 5708；meta built_at 早于部分 split index 更新时间。
- 存在 `ai_image_embedding_index.checkpoint.npz` 和 `ai_image_embedding_meta.checkpoint.json`，checkpoint 显示 asset 已编码 1200/5706，说明有未完成 embedding 构建残留。
- `missing_pptx_report.json` 显示 teach-kb DB 中 1515 个 PPTX，当前按 theme 匹配仍有 524 个未覆盖。

## 文档分层

`doc_new/README.md` 要改为 13 个文件完整索引，并区分以下层级：

- 核心交接：`current-status.md`、`material-reuse-pipeline.md`、`reuse-evaluation-and-tuning.md`、`source-document-map.md`、`planned-and-not-implemented.md`、`svg-pipeline.md`。
- 操作手册：`各种命令.md`。
- 数据库和审计：`material_library_ppt构建测试存在问题分析.md`、`test_reuse 评测集与最新测试结果分析报告.md`。
- 历史/辅助说明但需勘误：`素材图片复用流程最新版本.md`、`素材复用分类标准.md`、`素材复用管线交接报告（现状总结）.md`。

## 文件级更新设计

`README.md`

- 补齐全部 13 个文件。
- 写清楚哪些是当前事实，哪些是历史说明已被更新。
- 增加 `materials_library_ppt` 数据库分析已纳入本轮检查。

`current-status.md`

- 补充 embedding sidecar 增量复用与 checkpoint/resume 机制。
- 补充当前 `materials_library_ppt` 数据状态摘要。
- 补充已删除历史脚本不能作为当前能力。

`material-reuse-pipeline.md`

- 更新索引结构说明：当前 PPT 库主要靠 split index，顶层主 DB/match index 可不存在。
- 增加 embedding sidecar 新逻辑：按 asset_id + embedding_text_hash 复用旧向量，只编码新增或变化文本；缺 caption 会写 review sidecar。
- 明确背景图使用 `normalized_prompt` 做检索/embedding，page_image 使用 `caption`。
- 明确 C00 split 可作为归档/检查存在，但不会作为可复用路由。

`素材复用管线交接报告（现状总结）.md`

- 与 `material-reuse-pipeline.md` 保持同一事实口径。
- 加入更适合交接的阶段说明：建库、split index、embedding、检索、review、VLM near-miss、维护风险。

`reuse-evaluation-and-tuning.md`

- 保留 Phase 1、Phase 4、Review A/A.2/A.3/Stage B 结论。
- 强化归因：Phase 4 policy 阈值保留，precision 下滑主要来自 LLM accept 端，不是 direct policy 误纳。
- 标明 `reviewA_rubric_v25` 与 A.3 的差异，避免把 Stage A v2.5 当成当前最优结果。

`test_reuse 评测集与最新测试结果分析报告.md`

- 对齐当前 `test_reuse/cli.py` 的 prepare、hard-filter、retrieve、review、summarize、analyze、run-all。
- 删除不存在或过期的阶段入口。
- 补充 size-compatible gold 与 raw gold 的区别。

`planned-and-not-implemented.md`

- 保留 Stage B、C02 拆分、T_REJECT=0.38、Stage D tie-break 等未启用项。
- 新增“已删除维护脚本和旧命令不是当前入口”。
- 新增 `materials_library_ppt` 待维护项：embedding clean rebuild、缺 caption/图片资产处理、4 个 background normalized_prompt 回填、teach-kb missing PPTX 覆盖。

`source-document-map.md`

- 补充本次核对的 HEAD 更新：embedding incremental build、脚本清理、`materials_library_ppt` 数据形态。
- 更新冲突处理规则，明确库数据和代码冲突时以代码可读逻辑和当前文件存在性为准。

`各种命令.md`

- 按当前 `edupptx/cli.py`、`test_reuse/cli.py`、`scripts/` 重写。
- 删除已不存在脚本：`update_ppt_actual_dimensions.py`、`write_rerun_from_pptx_debug.py`、`backfill_caption.py`、`backfill_plan_grade_subject.py`、`dry_run_query_classify.py` 等。
- 增加 `dry_run_llm_classify.py --apply --skip-embedding-update` 的当前行为说明。
- 增加 `rebuild_ppt_materials_embeddings.py` 与 `edupptx embedding-build` 的使用边界。

`素材复用分类标准.md`

- 将旧 C00-C11/C99 草案改为“历史草案，当前已废弃”。
- 主体内容改为当前 active C00-C03 分类标准。
- 明确如果后续重新拆 C02，必须改 classifier、split index、target enrichment、hard-filter、goldset 和 policy 回归。

`素材图片复用流程最新版本.md`

- 保留流程短稿风格，但更新为当前真实流程。
- 修正“召回数量应该小于 8”这类不精确描述为：默认最终候选 8，混合召回池 20。
- 补充 caption/normalized_prompt 字段使用差异。

`material_library_ppt构建测试存在问题分析.md`

- 保留人工目检问题，但新增当前数据库体检摘要。
- 区分已由代码/测试修正的字段问题、仍需人工复核的问题、以及当前库数据需要重建/回填的问题。
- 明确缺失项：C02 的 1 个缺 caption/缺图片资产、background 4 个缺 normalized_prompt、embedding checkpoint 残留、teach-kb 524 个 missing PPTX。

`svg-pipeline.md`

- 只做必要对齐：素材复用入口、练习题绑定和 DESIGN.md 仍按当前代码事实写。
- 不把素材库数据库细节重复展开到此文件。

## 验证设计

文档更新后做以下验证：

- `rg` 检查 `doc_new` 是否仍把 C00-C11/C99、13 类、C00-C05 写成当前实现。
- `rg` 检查 `doc_new/各种命令.md` 是否仍引用已删除脚本。
- `rg` 检查是否误写 Stage B `core_elements` 为已启用。
- `rg` 检查是否误写 `T_REJECT=0.38` 为当前代码值。
- 用 PowerShell 重新统计 `materials_library_ppt/strict_reuse_indexes/*.json` 的数量、缺字段和图片缺失，确保文档数字和当前库一致。
- 如时间允许，运行轻量测试：`uv run pytest tests/test_ai_image_asset_db.py::test_embedding_build_reuses_unchanged_vectors_and_encodes_only_changed_assets tests/test_asset_ingest_job_payload.py::test_ingest_ai_image_asset_job_updates_embedding_incrementally -q`。

## 非目标

- 不修改 `materials_library_ppt` 数据库文件本身。
- 不重跑 LLM/VLM。
- 不清理 checkpoint、备份或缺失图片；只在文档中记录维护建议。
- 不把 Stage B、C02 拆分、T_REJECT=0.38 或旧分类体系写成当前实现。
- 不恢复已删除脚本。
