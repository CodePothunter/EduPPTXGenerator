# test_reuse Linux 全量 session 分阶段测评流程

本文档用于在 Linux 环境中一次性评估所有 `output/session_*/plan.json`。目标是运行生产等价的分阶段素材复用评估，并保留每个阶段的可审计产物。

核心原则：

- `prepare --allow-llm` 只运行一次，真实调用 target LLM，生成并固化 target enrichment。
- `hard-filter`、`retrieve`、`review`、`summarize` 都复用同一个 `RUN_DIR`。
- 后续阶段复用 `01_prepare/targets.jsonl`，不重复调用 target LLM。
- 默认流程不调用 VLM。
- `goldset.json` 只用于贴标和计算指标，不参与 target 生成、候选召回、硬过滤、排序、阈值筛选、策略判断或最终选择。
- 如果要测试 `merge-c01-c03`，必须分阶段运行；`run-all` 当前没有 `--category-routing` 参数。

## 1. 环境准备

进入项目根目录：

```bash
cd /path/to/EduPPTXGenerator
```

确认必要路径存在：

```bash
test -d output
test -f test_reuse/fixtures/reuse_caption_goldset_20260603/goldset.json
test -d materials_library_ppt
test -f .env
which uv
uv --version
```

可选：先跑单元测试确认代码状态。

```bash
UV="${UV:-uv}"
mkdir -p .tmp_pytest
"$UV" run pytest test_reuse/tests -q --basetemp .tmp_pytest/test-reuse-all
```

当前代码期望 `test_reuse/tests` 全量通过；具体 passed 数以后续代码为准。

## 2. 设置全量测试变量

```bash
UV="${UV:-uv}"
GOLD="test_reuse/fixtures/reuse_caption_goldset_20260603/goldset.json"
LIB="materials_library_ppt"
ENV_FILE=".env"
RUN_ID="reuse_all_test_$(date +%Y%m%d_%H%M%S)"
RUN_DIR="report/$RUN_ID"
```

收集所有 session plan：

```bash
mapfile -t PLANS < <(find output -path 'output/session_*/plan.json' | sort)

echo "Plan count: ${#PLANS[@]}"
printf '%s\n' "${PLANS[@]}"

if [ "${#PLANS[@]}" -eq 0 ]; then
  echo "No plan.json found under output/session_*/plan.json" >&2
  exit 1
fi
```

构造 `prepare` 参数：

```bash
PREPARE_ARGS=()
for plan in "${PLANS[@]}"; do
  PREPARE_ARGS+=(--plan "$plan")
done
```

## 3. Stage 1: prepare

`prepare` 会读取 plan、生成 target enrichment、应用 goldset 标答，并写入 `01_prepare`。这是全流程中唯一必须真实调用 target LLM 的阶段。

```bash
"$UV" run python -m test_reuse prepare \
  "${PREPARE_ARGS[@]}" \
  --goldset "$GOLD" \
  --output-dir report \
  --run-id "$RUN_ID" \
  --env-file "$ENV_FILE" \
  --allow-llm
```

主要输出：

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

检查命令：

```bash
cat "$RUN_DIR/01_prepare/target_enrichment_summary.json"
cat "$RUN_DIR/01_prepare/target_classification_summary.json"
wc -l "$RUN_DIR/01_prepare/plan_needs.jsonl"
wc -l "$RUN_DIR/01_prepare/targets.jsonl"
head -n 1 "$RUN_DIR/01_prepare/targets.jsonl"
```

如果安装了 `jq`：

```bash
jq '.target_count, .missing_required_field_count' "$RUN_DIR/01_prepare/target_enrichment_summary.json"
jq '.total_targets, .target_class_accuracy, .c00_f1' "$RUN_DIR/01_prepare/target_classification_summary.json"
head -n 1 "$RUN_DIR/01_prepare/targets.jsonl" | jq '.target.strict_reuse_group, .target.match_text'
```

关键检查点：

- `missing_required_field_count` 应为 `0`。
- `targets.jsonl` 行数应等于所有 session 的 AI 图片需求总数。
- `target.strict_reuse_group`、`target.match_text` 应非空。
- `target_classification_summary.json` 用于单独诊断 target 分类准确率。

## 4. Stage 2: hard-filter

`hard-filter` 不调用 LLM，也不调用 VLM。它读取 `01_prepare/targets.jsonl`，按素材库 split index 路由候选，并执行分类、学科/general、尺寸比例等硬过滤。

### 4.1 生产等价 baseline

```bash
"$UV" run python -m test_reuse hard-filter \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB" \
  --category-routing baseline
```

`--category-routing baseline` 是默认值，可省略。

### 4.2 C01-C03 合并候选池实验

如果要测试“C01/C02/C03 互分类不再直接挡候选”的效果，使用：

```bash
"$UV" run python -m test_reuse hard-filter \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB" \
  --category-routing merge-c01-c03
```

说明：

- `merge-c01-c03` 只合并 C01/C02/C03 候选池和分类硬过滤判断。
- C00 仍然不进入可复用候选池。
- 学科/general、尺寸比例、素材类型等其他硬过滤仍然生效。
- merge 模式会额外写出 baseline 对比产物。

主要输出：

- `02_hard_filter/hard_filter_pairs.jsonl`
- `02_hard_filter/hard_filter_summary.json`
- `02_hard_filter/size_filter_gold_rejection_by_aspect_combo.csv`
- `02_hard_filter/subject_filter_false_rejections.csv`
- `02_hard_filter/subject_only_false_rejections.csv`
- `02_hard_filter/baseline_hard_filter_pairs.jsonl`，仅 merge 模式
- `02_hard_filter/category_routing_comparison.json`，仅 merge 模式

检查命令：

```bash
cat "$RUN_DIR/02_hard_filter/hard_filter_summary.json"
test -f "$RUN_DIR/02_hard_filter/hard_filter_pairs.jsonl" && echo "hard_filter_pairs exists"
wc -l "$RUN_DIR/02_hard_filter/hard_filter_pairs.jsonl"

test -f "$RUN_DIR/02_hard_filter/category_routing_comparison.json" \
  && cat "$RUN_DIR/02_hard_filter/category_routing_comparison.json"
```

重点字段：

- `stage.candidate_hit_rate`
- `stage.best_hit_rate`
- `stage.loss_by_reason`
- `filter_ablation.size_only`
- `filter_ablation.subject_only`
- `filter_ablation.category_only`
- `filter_ablation.subject_size`
- `non_c00_target_match_counts`
- `category_filter`、`subject_filter`、`aspect_filter`

`non_c00_target_match_counts` 会按非 C00 target 分类统计：

- `target_count`
- `reusable_need_count`
- `acceptable_gold_pair_count`
- `best_gold_pair_count`
- `candidate_pair_count`
- `hard_pass_pair_count`
- `candidate_hit_need_count/rate`
- `best_hit_need_count/rate`

## 5. Stage 3: retrieve

`retrieve` 复用 `01_prepare/targets.jsonl` 中的 target 字段，不再调用 target LLM。它会对硬过滤后的候选做 BM25、embedding、substring、hybrid 排序和阈值筛选。

```bash
"$UV" run python -m test_reuse retrieve \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB"
```

主要输出：

- `03_retrieve/candidate_collections.jsonl`
- `03_retrieve/candidate_score_audit.jsonl`
- `03_retrieve/candidate_score_audit.csv`
- `03_retrieve/retrieve_summary.json`
- `03_retrieve/threshold_rejected_gold_candidates.csv`
- `03_retrieve/threshold_accepted_non_gold_candidates.csv`

检查命令：

```bash
cat "$RUN_DIR/03_retrieve/retrieve_summary.json"
test -f "$RUN_DIR/03_retrieve/candidate_collections.jsonl" && echo "candidate_collections exists"
test -f "$RUN_DIR/03_retrieve/candidate_score_audit.jsonl" && echo "candidate_score_audit exists"
test -f "$RUN_DIR/03_retrieve/retrieve_summary.json" && echo "retrieve_summary exists"
test -f "$RUN_DIR/03_retrieve/threshold_rejected_gold_candidates.csv" && echo "threshold rejected gold csv exists"
test -f "$RUN_DIR/03_retrieve/threshold_accepted_non_gold_candidates.csv" && echo "threshold accepted non-gold csv exists"
wc -l "$RUN_DIR/03_retrieve/candidate_collections.jsonl"
wc -l "$RUN_DIR/03_retrieve/candidate_score_audit.jsonl"
```

重点字段：

- `candidate_score_audit`：每个 query 的候选分数、rank、policy_input 和 gold 标签。
- `stage`：阈值阶段按 need 统计的 candidate/best hit rate 和 top-k recall。
- `threshold_decision_audit`：完整 gold 口径下的阈值直接拒绝/直接接受审计。
- `size_compatible_gold`：只在硬过滤之后使用的 size-pass gold 对照口径。

`threshold_decision_audit` 用于回答：

- 阈值直接拒绝的候选里是否有 gold：`direct_reject_has_gold`
- 阈值直接拒绝的候选里是否有 best gold：`direct_reject_has_best`
- 阈值直接接受的候选里是否有非 gold：`direct_accept_has_non_gold`
- 对应数量：`rejected_gold_pair_count`、`rejected_best_pair_count`、`accepted_non_gold_pair_count`

两个 CSV 明细表用于人工检查：

- `threshold_rejected_gold_candidates.csv`：阈值没过但其实是 gold 的候选。
- `threshold_accepted_non_gold_candidates.csv`：阈值通过但不是 gold 的候选。

### size-pass gold 对照口径

`size_compatible_gold` 的 gold 集合定义为：

```text
original_gold ∩ hard_filter_pairs 中 size_only_pass=True 的候选集
```

它不会重新计算 size 规则，只读取硬过滤阶段已经产出的 `size_only_pass` 候选集。当前双口径只用于硬过滤之后的阶段：

- `03_retrieve/retrieve_summary.json.size_compatible_gold`
- `05_summarize/metrics.json.retrieval_size_compatible_gold`
- `05_summarize/metrics.json.ranking_size_compatible_gold`
- `05_summarize/metrics.json.final_raw_gold_audit`
- `05_summarize/metrics.json.size_compatible_gold_adjustment`

### embedding smoke 模式

如果本地 embedding index 不完整，正式 retrieve 可能较慢。正式统计准确率时不要禁用 embedding。只验证流程时可以临时关闭：

```bash
EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS=1 "$UV" run python -m test_reuse retrieve \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB"
```

## 6. Stage 4: review

默认 `review` 不调用 LLM，也不调用 VLM，只执行最终复用策略、reuse policy、strict reuse occupancy 等生产等价检查。

```bash
"$UV" run python -m test_reuse review \
  --run-dir "$RUN_DIR"
```

主要输出：

- `04_review/llm_reviews.jsonl`
- `04_review/llm_review_summary.json`
- `04_review/final_matches.jsonl`
- `04_review/reuse_finalize_debug.jsonl`，如果 finalize debug 有写入

检查命令：

```bash
cat "$RUN_DIR/04_review/llm_review_summary.json"
test -f "$RUN_DIR/04_review/final_matches.jsonl" && echo "final_matches exists"
wc -l "$RUN_DIR/04_review/final_matches.jsonl"
head -n 3 "$RUN_DIR/04_review/final_matches.jsonl"
```

重点字段：

- `policy_candidate_count`：进入 review 阶段前的 policy 输入候选数。
- `review_candidate_count`：写入 `llm_reviews.jsonl` 的候选数。
- `llm_review_required_count`：策略标记需要 LLM review 的候选数。
- `llm_review_performed_count` / `reviewed_count`：实际执行 LLM review 的候选数。
- `accepted_count`、`rejected_count`
- `llm_review_required_rate`、`llm_review_performed_rate`
- `llm_accept_correctness_rate`、`llm_false_reject_rate`

默认不加 `--review` 时，通常 `reviewed_count` 为 `0`。如果 strict 资产超过生产复用次数限制，`final_matches.jsonl.failure_stage` 会标记为 `strict_reuse_occupancy`。

### 可选：启用候选 LLM review

只有要测试候选 LLM review 准确率时才运行。该模式会真实调用 LLM，但仍不调用 VLM。

```bash
"$UV" run python -m test_reuse review \
  --run-dir "$RUN_DIR" \
  --review \
  --env-file "$ENV_FILE" \
  --allow-llm

"$UV" run python -m test_reuse summarize \
  --run-dir "$RUN_DIR"
```

## 7. Stage 5: summarize

`summarize` 只读取前面阶段已经写出的 artifacts，不会重跑或重写 prepare、hard-filter、retrieve、review。

```bash
"$UV" run python -m test_reuse summarize \
  --run-dir "$RUN_DIR"
```

主要输出：

- `05_summarize/metrics.json`
- `05_summarize/report.md`
- `05_summarize/failure_cases.jsonl`
- `05_summarize/prompt_issue_log.jsonl`

检查命令：

```bash
cat "$RUN_DIR/05_summarize/metrics.json"
head -n 80 "$RUN_DIR/05_summarize/report.md"
wc -l "$RUN_DIR/05_summarize/failure_cases.jsonl"
```

`metrics.json` 重点字段：

- `target_classification`
- `hard_filter`
- `threshold`
- `retrieval_size_compatible_gold`
- `asset_kind_buckets`
- `llm_review`
- `ranking`
- `ranking_size_compatible_gold`
- `final`
- `final_raw_gold_audit`
- `size_compatible_gold_adjustment`
- `target_count`
- `unlabeled_need_count`

`threshold/ranking/final` 是完整 gold 口径；对应的 `*_size_compatible_gold` 是硬过滤之后 size-pass gold 口径。`hard_filter` 本身仍按完整 gold 统计。

## 8. 最小全量命令：生产等价 baseline

```bash
cd /path/to/EduPPTXGenerator

set -euo pipefail

UV="${UV:-uv}"
GOLD="test_reuse/fixtures/reuse_caption_goldset_20260603/goldset.json"
LIB="materials_library_ppt"
ENV_FILE=".env"
RUN_ID="reuse_all_test_$(date +%Y%m%d_%H%M%S)"
RUN_DIR="report/$RUN_ID"

mapfile -t PLANS < <(find output -path 'output/session_*/plan.json' | sort)
echo "Plan count: ${#PLANS[@]}"
if [ "${#PLANS[@]}" -eq 0 ]; then
  echo "No plan.json found under output/session_*/plan.json" >&2
  exit 1
fi

PREPARE_ARGS=()
for plan in "${PLANS[@]}"; do
  PREPARE_ARGS+=(--plan "$plan")
done

"$UV" run python -m test_reuse prepare "${PREPARE_ARGS[@]}" --goldset "$GOLD" --output-dir report --run-id "$RUN_ID" --env-file "$ENV_FILE" --allow-llm
"$UV" run python -m test_reuse hard-filter --run-dir "$RUN_DIR" --library-dir "$LIB" --category-routing baseline
"$UV" run python -m test_reuse retrieve --run-dir "$RUN_DIR" --library-dir "$LIB"
"$UV" run python -m test_reuse review --run-dir "$RUN_DIR"
"$UV" run python -m test_reuse summarize --run-dir "$RUN_DIR"

cat "$RUN_DIR/05_summarize/metrics.json"
head -n 80 "$RUN_DIR/05_summarize/report.md"
```

## 9. 最小全量命令：merge-c01-c03 实验

```bash
cd /path/to/EduPPTXGenerator

set -euo pipefail

UV="${UV:-uv}"
GOLD="test_reuse/fixtures/reuse_caption_goldset_20260603/goldset.json"
LIB="materials_library_ppt"
ENV_FILE=".env"
RUN_ID="reuse_all_merge_c01_c03_$(date +%Y%m%d_%H%M%S)"
RUN_DIR="report/$RUN_ID"

mapfile -t PLANS < <(find output -path 'output/session_*/plan.json' | sort)
echo "Plan count: ${#PLANS[@]}"
if [ "${#PLANS[@]}" -eq 0 ]; then
  echo "No plan.json found under output/session_*/plan.json" >&2
  exit 1
fi

PREPARE_ARGS=()
for plan in "${PLANS[@]}"; do
  PREPARE_ARGS+=(--plan "$plan")
done

"$UV" run python -m test_reuse prepare "${PREPARE_ARGS[@]}" --goldset "$GOLD" --output-dir report --run-id "$RUN_ID" --env-file "$ENV_FILE" --allow-llm
"$UV" run python -m test_reuse hard-filter --run-dir "$RUN_DIR" --library-dir "$LIB" --category-routing merge-c01-c03
"$UV" run python -m test_reuse retrieve --run-dir "$RUN_DIR" --library-dir "$LIB"
"$UV" run python -m test_reuse review --run-dir "$RUN_DIR"
"$UV" run python -m test_reuse summarize --run-dir "$RUN_DIR"

cat "$RUN_DIR/02_hard_filter/category_routing_comparison.json"
cat "$RUN_DIR/05_summarize/metrics.json"
head -n 80 "$RUN_DIR/05_summarize/report.md"
```

## 10. run-all 快速流程

`run-all` 会顺序执行 prepare、hard-filter、retrieve、review、summarize。它适合 baseline 快速跑完整流程。

限制：

- `run-all` 当前没有 `--category-routing`，因此不能跑 `merge-c01-c03`。
- 因为内部会执行 prepare，所以正式跑时仍需要 `--allow-llm`。

```bash
"$UV" run python -m test_reuse run-all \
  "${PREPARE_ARGS[@]}" \
  --goldset "$GOLD" \
  --library-dir "$LIB" \
  --output-dir report \
  --run-id "$RUN_ID" \
  --env-file "$ENV_FILE" \
  --allow-llm
```

如果要启用候选 LLM review：

```bash
"$UV" run python -m test_reuse run-all \
  "${PREPARE_ARGS[@]}" \
  --goldset "$GOLD" \
  --library-dir "$LIB" \
  --output-dir report \
  --run-id "$RUN_ID" \
  --env-file "$ENV_FILE" \
  --allow-llm \
  --review
```

## 11. 已有 prepare 结果时重跑后续阶段

如果已经跑过 `prepare`，只想复用同一个 `RUN_DIR` 重新跑后续阶段：

```bash
UV="${UV:-uv}"
LIB="materials_library_ppt"
RUN_DIR="report/你的_run_id"

"$UV" run python -m test_reuse hard-filter --run-dir "$RUN_DIR" --library-dir "$LIB" --category-routing baseline
"$UV" run python -m test_reuse retrieve --run-dir "$RUN_DIR" --library-dir "$LIB"
"$UV" run python -m test_reuse review --run-dir "$RUN_DIR"
"$UV" run python -m test_reuse summarize --run-dir "$RUN_DIR"
```

这种方式不会重新做 target enrichment。注意：后跑的阶段会覆盖同一 `RUN_DIR` 下对应阶段产物。

如果要保留 baseline 和 merge 两套完整后续结果，建议用两个不同 `RUN_ID` 各跑完整流程；或者先复制 `RUN_DIR` 再重跑后续阶段。

## 12. 单 session 调试

只排查某一个 session 时使用：

```bash
UV="${UV:-uv}"
PLAN="output/session_20260603_150913/plan.json"
GOLD="test_reuse/fixtures/reuse_caption_goldset_20260603/goldset.json"
LIB="materials_library_ppt"
ENV_FILE=".env"
RUN_ID="reuse_single_debug_$(date +%Y%m%d_%H%M%S)"
RUN_DIR="report/$RUN_ID"

"$UV" run python -m test_reuse prepare --plan "$PLAN" --goldset "$GOLD" --output-dir report --run-id "$RUN_ID" --env-file "$ENV_FILE" --allow-llm
"$UV" run python -m test_reuse hard-filter --run-dir "$RUN_DIR" --library-dir "$LIB"
"$UV" run python -m test_reuse retrieve --run-dir "$RUN_DIR" --library-dir "$LIB"
"$UV" run python -m test_reuse review --run-dir "$RUN_DIR"
"$UV" run python -m test_reuse summarize --run-dir "$RUN_DIR"
```

## 13. 常见问题

### Plan count 是 0

原因：Linux 环境没有 `output/session_*/plan.json`。

处理：

```bash
find output -path 'output/session_*/plan.json' | sort
```

确认 session 输出已经同步到 Linux 机器。

### prepare 报错 requires --allow-llm

原因：`prepare` 需要真实调用 target LLM 生成 target enrichment。

处理：重新运行 prepare，并添加 `--allow-llm`。

### prepare 报错 LLM credentials missing

原因：`.env` 中没有可用的 LLM API key 或模型配置。

处理：

```bash
cat .env
```

确认项目使用的 LLM 配置项已经填写，然后重新运行 `prepare --allow-llm`。

### hard-filter 报错 requires enriched targets

原因：没有先运行生产等价 `prepare --allow-llm`，或者手动修改过 `targets.jsonl`。

处理：重新跑 `prepare --allow-llm`，然后继续后续阶段。

### retrieve 很慢

原因：素材库 embedding index 不完整，正式 retrieve 可能会构建或补齐 embedding。

处理：

- 正式实验：等待 embedding index 构建完成。
- 只验证流程：使用 `EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS=1` smoke 模式。

### review --review 报错 requires --allow-llm

原因：启用候选 LLM review 时必须显式允许 LLM。

处理：

```bash
"$UV" run python -m test_reuse review \
  --run-dir "$RUN_DIR" \
  --review \
  --env-file "$ENV_FILE" \
  --allow-llm
```

### 为什么 threshold/ranking/final 有两套指标

完整 gold 口径用于观察当前系统对人工标答的整体命中情况。

size-pass gold 口径用于回答：“如果 size 过滤被认为正确，只看当前 size 规则允许复用的标答，后续 retrieve、ranking、final 表现如何。”

这套口径不会反向修改 hard-filter，也不会重新计算 size 规则。
