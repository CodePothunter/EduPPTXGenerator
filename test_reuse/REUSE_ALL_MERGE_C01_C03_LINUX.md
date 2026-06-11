# Linux 全数据集复用测试：忽略 C01-C03 互分类

本文档说明如何在 Linux 上运行一轮全数据集 `test_reuse` 评估，并在硬过滤阶段不关注 C01/C02/C03 之间的分类差异。

## 目标

这套命令用于回答：

- 当前素材库在全数据集上能命中多少人工标答。
- 如果不把 C01、C02、C03 互相错分当作硬过滤失败，后续召回、阈值筛选和最终复用表现如何。
- C00 是否仍然被正确挡掉。

注意：`--category-routing merge-c01-c03` 只合并 C01/C02/C03 的候选路由和分类硬过滤判断。它不会关闭 C00 过滤，也不会关闭学科、`general`、尺寸比例等其它硬过滤条件。

## 前置条件

在项目根目录运行：

```bash
cd /path/to/EduPPTXGenerator
```

需要准备：

- `output/session_*/plan.json`：全数据集 plan。
- `materials_library_ppt`：当前素材库。
- `test_reuse/fixtures/reuse_caption_goldset_20260603/goldset.json`：当前人工标答。
- `.env`：如果要让 `prepare` 真实调用 LLM，需要有可用配置。
- `uv`：Linux 环境中的 Python 运行入口。

## 一键分阶段运行

`run-all` 当前没有暴露 `--category-routing` 参数，所以这个模式必须分阶段运行。

```bash
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

"$UV" run python -m test_reuse prepare \
  "${PREPARE_ARGS[@]}" \
  --goldset "$GOLD" \
  --output-dir report \
  --run-id "$RUN_ID" \
  --env-file "$ENV_FILE" \
  --allow-llm

"$UV" run python -m test_reuse hard-filter \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB" \
  --category-routing merge-c01-c03

"$UV" run python -m test_reuse retrieve \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB"

"$UV" run python -m test_reuse review \
  --run-dir "$RUN_DIR"

"$UV" run python -m test_reuse summarize \
  --run-dir "$RUN_DIR"

cat "$RUN_DIR/05_summarize/metrics.json"
head -n 80 "$RUN_DIR/05_summarize/report.md"
```

## 每一步在做什么

### 1. 收集全数据集 plan

```bash
mapfile -t PLANS < <(find output -path 'output/session_*/plan.json' | sort)
```

这一步扫描所有 `output/session_*/plan.json`，把它们作为全数据集输入。排序是为了让每次运行的输入顺序稳定，便于对比不同 run 的结果。

如果 `Plan count` 为 `0`，说明 Linux 环境还没有同步或生成 session 输出，后续命令不应继续跑。

### 2. prepare

```bash
"$UV" run python -m test_reuse prepare \
  "${PREPARE_ARGS[@]}" \
  --goldset "$GOLD" \
  --output-dir report \
  --run-id "$RUN_ID" \
  --env-file "$ENV_FILE" \
  --allow-llm
```

`prepare` 做三件事：

- 从每个 `plan.json` 中抽取需要生成或复用的图片 target。
- 对 target 做生产等价的 target enrichment，包括分类、学科、尺寸比例、关键词等。
- 把 `goldset.json` 中的人工标答挂到对应 target 上，用于后续指标统计。

重要约束：`goldset.json` 只用于评估标签，不参与候选召回、硬过滤、排序或最终选择。

主要输出：

- `report/$RUN_ID/01_prepare/plan_needs.jsonl`
- `report/$RUN_ID/01_prepare/targets.jsonl`
- `report/$RUN_ID/01_prepare/target_enrichment_summary.json`
- `report/$RUN_ID/01_prepare/target_classification_summary.json`
- `report/$RUN_ID/01_prepare/target_class_mismatches_review.csv`
- `report/$RUN_ID/01_prepare/target_class_c00_cases_review.csv`

检查命令：

```bash
cat "$RUN_DIR/01_prepare/target_enrichment_summary.json"
cat "$RUN_DIR/01_prepare/target_classification_summary.json"
wc -l "$RUN_DIR/01_prepare/targets.jsonl"
head -n 1 "$RUN_DIR/01_prepare/targets.jsonl"
```

`target_classification_summary.json` 仍然会统计分类准确率。即使本次后续评估不关注 C01/C02/C03 互分类，这个文件仍然有价值，因为它可以单独观察 target 分类器表现。

### 3. hard-filter

```bash
"$UV" run python -m test_reuse hard-filter \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB" \
  --category-routing merge-c01-c03
```

这是本流程的关键步骤。

普通 `baseline` 模式会按 target 的 strict reuse group 路由素材，并把 C01/C02/C03 之间的组别不一致视为分类硬过滤失败。

`merge-c01-c03` 模式会把 C01、C02、C03 合并成同一个可复用候选池：

- target 是 C01/C02/C03 时，可以从 C01/C02/C03 的素材中找候选。
- candidate 是 C01/C02/C03 时，不因为三者互相不一致而失败。
- C00 仍然是不可复用类别，不会被合并进来。
- background 仍按 background 逻辑处理。
- 学科兼容、`general`、尺寸比例等过滤仍然继续生效。

主要输出：

- `report/$RUN_ID/02_hard_filter/hard_filter_pairs.jsonl`
- `report/$RUN_ID/02_hard_filter/hard_filter_summary.json`
- `report/$RUN_ID/02_hard_filter/category_routing_comparison.json`
- `report/$RUN_ID/02_hard_filter/baseline_hard_filter_pairs.jsonl`
- `report/$RUN_ID/02_hard_filter/subject_filter_false_rejections.csv`
- `report/$RUN_ID/02_hard_filter/size_filter_gold_rejection_by_aspect_combo.csv`

检查命令：

```bash
cat "$RUN_DIR/02_hard_filter/hard_filter_summary.json"
cat "$RUN_DIR/02_hard_filter/category_routing_comparison.json"
wc -l "$RUN_DIR/02_hard_filter/hard_filter_pairs.jsonl"
```

重点看：

- `stage.candidate_hit_rate`
- `stage.best_hit_rate`
- `category_filter.candidate_hit_rate`
- `category_routing_comparison.json` 中 baseline 与 merge 模式的差异

如果 merge 模式下命中率明显上升，说明原本有一部分标答被 C01/C02/C03 互分类挡掉。

### 4. retrieve

```bash
"$UV" run python -m test_reuse retrieve \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB"
```

`retrieve` 读取 `hard-filter` 通过的候选集合，对候选做 BM25、embedding、hybrid scoring 和阈值筛选。

它不会重新调用 target 分类 LLM，也不会重新读取 goldset 来影响召回；goldset 仍只用于计算命中率。

主要输出：

- `report/$RUN_ID/03_retrieve/candidate_collections.jsonl`
- `report/$RUN_ID/03_retrieve/candidate_score_audit.jsonl`
- `report/$RUN_ID/03_retrieve/candidate_score_audit.csv`
- `report/$RUN_ID/03_retrieve/retrieve_summary.json`

检查命令：

```bash
cat "$RUN_DIR/03_retrieve/retrieve_summary.json"
wc -l "$RUN_DIR/03_retrieve/candidate_collections.jsonl"
wc -l "$RUN_DIR/03_retrieve/candidate_score_audit.jsonl"
```

如果本地 embedding index 不完整，`retrieve` 可能比较慢，因为它可能需要构建或补齐 embedding。正式评估建议不要禁用 embedding。

只做流程 smoke test 时可以临时禁用：

```bash
EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS=1 "$UV" run python -m test_reuse retrieve \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB"
```

### 5. review

```bash
"$UV" run python -m test_reuse review \
  --run-dir "$RUN_DIR"
```

`review` 做最终复用策略决策。默认不调用 LLM review，只执行最终策略、reuse policy、occupancy 等生产等价检查。

主要输出：

- `report/$RUN_ID/04_review/final_decisions.jsonl`
- `report/$RUN_ID/04_review/reuse_finalize_debug.jsonl`

检查命令：

```bash
wc -l "$RUN_DIR/04_review/final_decisions.jsonl"
head -n 1 "$RUN_DIR/04_review/final_decisions.jsonl"
```

如果需要启用 LLM review，可以改成：

```bash
"$UV" run python -m test_reuse review \
  --run-dir "$RUN_DIR" \
  --env-file "$ENV_FILE" \
  --allow-llm \
  --review
```

### 6. summarize

```bash
"$UV" run python -m test_reuse summarize \
  --run-dir "$RUN_DIR"
```

`summarize` 只读取前面阶段的 artifacts，计算最终指标和失败案例，不会重跑 prepare、hard-filter、retrieve 或 review。

主要输出：

- `report/$RUN_ID/05_summarize/metrics.json`
- `report/$RUN_ID/05_summarize/report.md`
- `report/$RUN_ID/05_summarize/failure_cases.jsonl`
- `report/$RUN_ID/05_summarize/prompt_issue_log.jsonl`

检查命令：

```bash
cat "$RUN_DIR/05_summarize/metrics.json"
head -n 80 "$RUN_DIR/05_summarize/report.md"
wc -l "$RUN_DIR/05_summarize/failure_cases.jsonl"
```

重点看：

- `target_classification`：target 分类器表现，仍按 C00-C03 标答统计。
- `hard_filter`：合并 C01-C03 后的硬过滤表现。
- `threshold`：retrieve 阈值后的候选命中表现。
- `final`：最终复用决策表现。
- `asset_kind_buckets`：`page_image` 与 `background` 拆分指标。

## 已有 prepare 结果时重跑后续阶段

如果已经跑过 `prepare`，只想在同一个 `RUN_DIR` 上重跑“不关注 C01-C03 互分类”的后续阶段：

```bash
set -euo pipefail

UV="${UV:-uv}"
LIB="materials_library_ppt"
RUN_DIR="report/你的run_id"

"$UV" run python -m test_reuse hard-filter \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB" \
  --category-routing merge-c01-c03

"$UV" run python -m test_reuse retrieve \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB"

"$UV" run python -m test_reuse review \
  --run-dir "$RUN_DIR"

"$UV" run python -m test_reuse summarize \
  --run-dir "$RUN_DIR"
```

这种方式会复用 `01_prepare/targets.jsonl`，不会重新做 target enrichment。

## 和 baseline 对比

如果要比较“严格关注 C01-C03 分类”和“不关注 C01-C03 互分类”的差异，可以对同一个 `RUN_DIR` 做两轮 hard-filter。

第一轮 baseline：

```bash
"$UV" run python -m test_reuse hard-filter \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB" \
  --category-routing baseline
```

第二轮 merge：

```bash
"$UV" run python -m test_reuse hard-filter \
  --run-dir "$RUN_DIR" \
  --library-dir "$LIB" \
  --category-routing merge-c01-c03
```

merge 模式会写出：

```bash
cat "$RUN_DIR/02_hard_filter/category_routing_comparison.json"
```

这个文件用于直接观察：

- baseline 有多少候选通过。
- merge-c01-c03 有多少候选通过。
- 分类过滤造成的候选命中损失是否被恢复。

如果要保留两套完整后续结果，建议使用两个不同 `RUN_ID` 分别跑完整流程，否则后跑的 hard-filter/retrieve/review/summarize 会覆盖同一 `RUN_DIR` 下对应阶段产物。

## 常见问题

### 为什么不用 run-all？

`run-all` 当前没有 `--category-routing` 参数，内部调用 `hard-filter` 时会使用默认 `baseline`。所以本模式必须显式分阶段运行。

### 这个模式是不是完全不看分类？

不是。它只是不把 C01/C02/C03 之间的差异作为候选路由和硬过滤失败。

仍然保留：

- C00 跳过逻辑。
- background 与 page_image 的不同处理。
- 学科兼容过滤。
- `general=true` 的通用素材逻辑。
- 尺寸比例过滤。
- 后续 retrieve threshold 与最终 reuse policy。

### target_classification 指标还要看吗？

要看，但它是独立诊断项。

本流程的后续召回指标用于评估“如果 C01/C02/C03 互分类不挡候选，素材库和检索策略能否命中标答”。`target_classification_summary.json` 则用于单独判断分类器本身有没有问题。

### 如何确认这轮确实是 merge-c01-c03？

检查：

```bash
cat "$RUN_DIR/02_hard_filter/hard_filter_summary.json"
cat "$RUN_DIR/02_hard_filter/category_routing_comparison.json"
```

`category_routing_comparison.json` 只有在 `--category-routing merge-c01-c03` 时才会写出。
