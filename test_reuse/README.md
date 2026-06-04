# test_reuse

`test_reuse` 是 PPT 生图素材复用的生产等价分阶段评估工具。

核心原则：

- `output/session_*/plan.json` 是测试输入，target 生成和复用逻辑走生产路径。
- `goldset.json` 只用于统计指标，不参与 target 生成、候选召回、策略判断或最终选择。
- VLM 当前不调用；`review --review --allow-llm` 只启用候选 LLM review。
- `prepare` 是唯一会生成 target LLM 字段的阶段，后续阶段复用 `targets.jsonl`，不重复调用 target LLM。

## 分阶段流程

第一步必须真实调用 LLM，生成并固化 target enrichment：

```powershell
python -m test_reuse prepare `
  --plan output\session_20260603_150913\plan.json `
  --goldset test_reuse\fixtures\reuse_caption_goldset_20260603\goldset.json `
  --output-dir report `
  --run-id reuse_eval_manual `
  --allow-llm
```

后续阶段复用同一个 `--run-dir`：

```powershell
python -m test_reuse hard-filter --run-dir report\reuse_eval_manual --library-dir materials_library_ppt
python -m test_reuse retrieve --run-dir report\reuse_eval_manual --library-dir materials_library_ppt
python -m test_reuse review --run-dir report\reuse_eval_manual
python -m test_reuse summarize --run-dir report\reuse_eval_manual
```

需要启用最终候选 LLM review 时，只在 review 阶段添加：

```powershell
python -m test_reuse review `
  --run-dir report\reuse_eval_manual `
  --review `
  --allow-llm
```

## 一次性运行

`run-all` 也需要 `--allow-llm`，因为它内部会先执行生产等价 `prepare`：

```powershell
python -m test_reuse run-all `
  --plan output\session_20260603_150913\plan.json `
  --goldset test_reuse\fixtures\reuse_caption_goldset_20260603\goldset.json `
  --library-dir materials_library_ppt `
  --output-dir report `
  --run-id reuse_eval_manual `
  --allow-llm
```

## 输出文件

每次运行写入 `report/<run_id>/`：

- `manifest.json`
- `plans/`
- `01_prepare/plan_needs.jsonl`
- `01_prepare/targets.jsonl`
- `01_prepare/target_enrichment.jsonl`
- `01_prepare/target_enrichment_summary.json`
- `01_prepare/target_classification_summary.json`
- `01_prepare/target_class_mismatches_review.csv`
- `01_prepare/target_class_c00_cases_review.csv`
- `01_prepare/target_class_mismatch_summary.csv`
- `02_hard_filter/hard_filter_pairs.jsonl`
- `02_hard_filter/hard_filter_summary.json`
- `03_retrieve/candidate_collections.jsonl`
- `03_retrieve/candidate_score_audit.jsonl`
- `03_retrieve/candidate_score_audit.csv`
- `03_retrieve/retrieve_summary.json`
- `04_review/llm_reviews.jsonl`
- `04_review/llm_review_summary.json`
- `04_review/final_matches.jsonl`
- `05_summarize/failure_cases.jsonl`
- `05_summarize/prompt_issue_log.jsonl`
- `05_summarize/metrics.json`
- `05_summarize/report.md`

`test_set.json` 和 `labeled_plans/` 不会在这里生成。
