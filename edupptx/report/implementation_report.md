# 素材复用逻辑改进实现报告

## 1. 任务背景

本次任务围绕 AI 图片素材复用库的构建与匹配逻辑进行收敛。核心目标是让素材复用只依赖图片内容语义，而不是完整生成 prompt 中的年级风格、质量词、负向词等生成专用信息。

## 2. 需求概述

已实现的需求包括：

1. 新写入的素材库和 match index 删除 `prompt` 字段，统一使用 `content_prompt` 表示不带风格词的内容描述。旧库如果只有 `prompt`，读取时仍作为兼容 fallback，规范化后会移除。
2. PPT 生成阶段查询素材库时不再传入 `generation_prompt`，只使用原始无风格内容 query。
3. 离线库侧和在线目标侧 LLM 提取字段保持一致：`normalized_prompt`、`context_summary`、`teaching_intent`、`core_keywords`、`semantic_aliases`、`context_summary_keywords`。
4. LLM 重复提取结果与已有字段取并集：`core_keywords` 去重合并，`context_summary_keywords` 去重合并，`semantic_aliases` 同 key 合并 alias 列表。
5. `semantic_aliases` 改为按核心概念组计算：一个 core keyword 和它的 aliases 是同一概念组，候选命中组内任意表达就算该概念命中。
6. 不再保存或使用 `role`、`page_title`、`reuse_scope`、`specificity_score`、`context_keywords`、`style_keywords`、`main_entities`、`visual_actions`、`scene_elements`、`emotion_tone` 等旧字段。

## 3. 修改文件清单

1. `edupptx/materials/ai_image_asset_db.py`
   - 素材库字段规范化、图片入库拷贝、match index、在线目标提取、候选打分和 debug payload。
2. `edupptx/agent.py`
   - 复用查询不再传 `generation_prompt`、`theme`、`page_title`、`role`。
3. `edupptx/models.py`
   - `ImageNeed` 保留 `generation_prompt` 和 `prompt_route`，用于生成链路；复用查询不使用 `generation_prompt`。
4. `edupptx/materials/image_prompt_router.py`
   - 负责把 plan 的原始 query 路由成生成用 `generation_prompt`。
5. `edupptx/materials/image_provider.py`
   - 生成图片时使用 `generation_prompt or query`。
6. `tests/test_ai_image_asset_db.py`
   - 覆盖字段清理、图片复制入库、match index、同义词概念组打分等。
7. `tests/test_image_prompt_router.py`、`tests/test_models.py`
   - 覆盖 prompt 拆分和 source 归一化。

## 4. 素材库构建与存入字段

素材库由 `update_ai_image_asset_library(session.dir, config.library_dir)` 更新。流程是：

1. `build_ai_image_asset_db()` 扫描当前 session 的 `plan.json` 和 `materials/`。
2. 背景图读取 `visual.background_prompt` 和 `materials/background.png`。
3. 页面图只读取 `material_needs.images[].source == "ai_generate"` 的图片。
4. 复用来的图片会通过 `ai_image_reuse_manifest.json` 跳过，避免重复入库。
5. `_copy_db_assets_to_library()` 将图片复制到中心库 `ai_images/{asset_id}.{suffix}`。
6. 数据库中的 `image_path` 改为中心库内路径。
7. `_merge_asset_library_db()` 规范化字段并写回 `ai_image_asset_db.json`。
8. `write_ai_image_match_index()` 写 `ai_image_match_index.json`。

当前新素材持久化字段主要包括：

- `asset_id`
- `asset_kind`
- `image_path`
- `aspect_ratio`
- `content_prompt`
- `generation_prompt`
- `style_prompt`
- `prompt_route`
- `background_route`
- `normalized_prompt`
- `context_summary`
- `teaching_intent`
- `core_keywords`
- `semantic_aliases`
- `context_summary_keywords`
- `theme`
- `grade`
- `subject`
- `grade_norm`
- `grade_number`
- `grade_band`
- `source`
- `library`

`generation_prompt` 只用于追溯当时实际生成图片的完整 prompt，不进入复用查询 target，也不进入 match index 主匹配字段。

## 5. Match Index 字段

`ai_image_match_index.json` 的 schema version 是 5。单条 match asset 保留：

- `asset_id`
- `asset_kind`
- `image_path`
- `aspect_ratio`
- `subject`
- `grade`
- `grade_norm`
- `grade_number`
- `grade_band`
- `content_prompt`
- `style_prompt`
- `prompt_route`
- `background_route`
- `normalized_prompt`
- `context_summary`
- `teaching_intent`
- `core_keywords`
- `semantic_aliases`
- `context_summary_keywords`
- `duplicate_asset_ids`

不再写入 `prompt`、`generation_prompt`、`role`、`page_title`、`reuse_scope`、`specificity_score` 等旧字段。

## 6. 在线查询字段

PPT 生成阶段：

- 页面图传入：`asset_kind`、`prompt=need.query`、`prompt_route`、`grade`、`subject`、`aspect_ratio`、`keyword_client`。
- 背景图传入：`asset_kind=background`、背景内容 prompt、`background_route`、`grade`、`subject`、`aspect_ratio=16:9`、`keyword_client`。
- 不传 `generation_prompt`。
- 不传 `theme`、`page_title`、`role`。

如果 `keyword_client` 可用，目标侧会临时提取：

- `normalized_prompt`
- `context_summary`
- `teaching_intent`
- `core_keywords`
- `semantic_aliases`
- `context_summary_keywords`

这些目标侧字段只用于当前查询，不写入素材库。

## 7. 匹配与分数

硬过滤：

1. `asset_kind` 不一致，直接拒绝。
2. 候选图片文件不存在，跳过。
3. `content_match_score <= 0`，直接拒绝。
4. 最终分数低于阈值，不复用。

总分公式：

```text
score =
  0.75 * content_match_score
+ 0.10 * route_score
+ 0.10 * aspect_ratio_score
+ 0.05 * context_score
```

其中：

```text
content_match_score = max(
  content_score,
  core_score * 0.9,
  semantic_score * 0.75
)
```

当前 `semantic_score` 固定为 0，因为旧结构化语义字段已关闭。

### content_score

`content_score` 是目标侧内容文本对候选侧内容文本的 BM25：

- 目标侧：`content_prompt`、`normalized_prompt`、`core_keywords`、`semantic_aliases` 展开词。
- 候选侧：`content_prompt`、`normalized_prompt`。

### core_score

`core_score` 当前是概念组打分，不再是简单 token 展开。

每个核心概念组由一个 `core_keyword` 和它的 aliases 构成，例如：

```json
{
  "core_keywords": ["作者画像"],
  "semantic_aliases": {
    "作者画像": ["人物肖像", "作者肖像"]
  }
}
```

会形成一个概念组：

```text
作者画像 = ["作者画像", "人物肖像", "作者肖像"]
```

候选 `content_prompt` / `normalized_prompt` 命中组内任意一个表达，就算这个核心概念命中。实现上是对组内每个表达分别做 BM25，取最高分作为该概念组分数，然后对所有核心概念组求平均。

因此：

- 只有一个核心概念组，命中任意 alias，`core_score` 可达到 1.0。
- 有两个核心概念组，只命中一个，`core_score` 约为 0.5。
- debug 中会记录 `core_hits`、`missing_core_groups`、`target_semantic_alias_groups`。

### route_score

`route_score` 使用低权重路由文本 BM25：

- `template_family`
- `profile_ids`
- `profile_prompt_terms`
- `aspect_ratio_prompt_terms`
- `background_route` 中的颜色/风格路由字段

不使用 `role_prompt_terms`、`page_type_prompt_terms`、`quality_terms`、`negative_terms`。

### aspect_ratio_score

`aspect_ratio_score` 只比较尺寸比例：

- 完全相同：1.0
- 一方缺失：0.5
- 方向相同：0.6
- 方向不同：0.2

不再使用 `role`。

### context_score

`context_score` 使用：

- 目标侧：`context_summary_keywords`，没有则回退 `context_summary`。
- 候选侧：`context_summary`。

## 8. 阈值

默认阈值：

- 页面图：0.28
- 背景图：0.25

如果调用方显式传入 `min_keyword_score`，则使用显式阈值，并限制在 0 到 1。

默认候选数：

- `DEFAULT_REUSE_CANDIDATE_LIMIT = 5`

流程是先打分排序，取前 5，再按阈值过滤，最后复用最高分候选。

## 9. 测试结果

已执行：

```powershell
python -m py_compile edupptx\materials\ai_image_asset_db.py
```

结果：通过。

```powershell
python -m pytest tests\test_ai_image_asset_db.py -q
```

结果：16 passed。

```powershell
python -m pytest tests\test_image_prompt_router.py tests\test_ai_image_asset_db.py tests\test_models.py -q
```

结果：30 passed。

全量测试此前仍受本机 Cairo 原生库缺失影响，`tests/test_icon_embedder.py` 收集阶段会失败；这与素材复用逻辑无关。

## 10. 结论

当前实现已从“同义词展开为普通 token”改为“核心概念组等价匹配”。这能避免 `作者画像 / 人物肖像 / 作者肖像` 这类同义表达被当成多个独立关键词稀释分数。素材复用仍保持 CPU 友好的 BM25 方案，没有引入 embedding。
