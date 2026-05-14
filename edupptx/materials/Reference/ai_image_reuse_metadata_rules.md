# AI Image Reuse Metadata Score Rules

本规则用于生成素材复用元数据。LLM 只负责输出稳定、可解释的连续分数和简短证据，不直接输出 `reuse_level`、`asset_category`、`core_constraints`、`generic_support_allowed`、`reuse_risk`，也不直接判断素材是否应被 strict、medium、loose。

代码会根据固定阈值把分数确定性转换为内部字段。这样同一份 LLM 输出可以通过调整阈值重新分类。

## Required Output

每个 asset 必须输出：

- `asset_id`
- `normalized_prompt`
- `context_summary`
- `teaching_intent`
- `core_keywords`
- `semantic_aliases`
- `context_summary_keywords`
- `reuse_scores`

`reuse_scores` 必须包含：

```json
{
  "strict_score": 0.0,
  "loose_score": 0.0,
  "generic_support_score": 0.0,
  "readable_knowledge_score": 0.0,
  "unique_referent_score": 0.0,
  "exact_relation_score": 0.0,
  "category_scores": {
    "learning_behavior": 0.0,
    "generic_tool": 0.0,
    "generic_diagram": 0.0,
    "concept_scene": 0.0,
    "content_specific": 0.0,
    "character_action": 0.0
  },
  "constraint_scores": [
    {
      "kind": "entity",
      "value": "可见主体或约束内容",
      "importance_score": 0.0,
      "exactness_score": 0.0,
      "aliases": [],
      "evidence": []
    }
  ],
  "evidence": [],
  "risk_factors": [],
  "brief_reason": ""
}
```

所有 score 必须是 0 到 1 的小数。不要输出离散结论字段，不要输出 yes/no，不要输出 strict/medium/loose/category 的最终选择。

## Score Meaning

`strict_score` 表示该素材的复用范围是否很窄。只有当缺失、替换或弱化主体、动作、对象、数量、情绪、关系、可读内容、知识对象、固定顺序或事件节点会直接造成教学事实错误时，才给高分。

`loose_score` 表示素材是否主要是背景、纹理、纯氛围、纯装饰或不承载具体教学语义。

`generic_support_score` 表示素材是否能作为泛化支持图、通用视觉辅助、通用学习行为、背景、氛围或通用工具复用。

`readable_knowledge_score` 表示图片是否承载具体可读、可数或可验证的教学内容，包括具体文字、拼音、词语、句子、公式、数字、单位、答案、标签、图例、步骤编号、笔顺、笔画、结构关系或知识对应关系。

`unique_referent_score` 表示图片是否承载具名人物、具名地点、具名作品对象、具体课文情节节点，或同类替换会直接造成事实错误的唯一对象。

`exact_relation_score` 表示图片是否承载必须保留的具体主体-动作-客体、固定顺序、因果、空间关系、事件节点或知识对应关系。

## Category Scores

为每个候选类别分别打分，代码会选择最高且超过阈值的类别：

- `learning_behavior`：通用学习或课堂行为。
- `generic_tool`：可复用的视觉辅助、符号工具、标注气泡、图标、抽象支架。
- `generic_diagram`：不绑定具体知识对象的通用图示。
- `concept_scene`：可广泛复用的语义场景。
- `content_specific`：课文或知识点中的特定事件、具体可读内容、固定知识对象、固定顺序、具体对应关系，或关键元素变化会改变含义的图示。
- `character_action`：人物、角色或形象的身份、动作方向、作用对象、状态、情绪、数量或场景关系不可替换。

## Constraint Scores

`constraint_scores` 用于列出可能成为硬约束的候选元素，但 LLM 不决定它们是否最终成为硬约束。代码会根据 `importance_score` 阈值过滤。

`kind` 只能是：

- `text`
- `math`
- `physics`
- `entity`
- `object`
- `action`
- `relation`
- `setting`
- `emotion`
- `count`

`importance_score` 表示该元素缺失或替换后造成教学错误的风险。

`exactness_score` 表示该元素是否必须精确一致。文字、数学、物理、数量、固定关系、可验证知识对象通常应给更高 exactness 分数；普通视觉主体、普通姿态、普通场景通常较低。

`aliases` 只放同义或近义表达，不放规则，不放负例。

## Calibration

普通主体、普通动物、普通人物、普通地点、普通单一动作、物种默认外观特征、常见生理特征、普通姿态、普通装饰、普通表情、常见动作和常见持物，通常不应给高 `strict_score` 或高 `importance_score`。

如果素材虽然包含具体主体、动作、状态或外观属性，但仍可安全作为普通同主体形象、普通角色形象、普通场景或泛化插图使用，应降低 `strict_score`，提高 `generic_support_score` 或相应通用类别分数。

不要因为素材来自某篇课文、服务于知识讲解、展示普通概念、普通实体或普通场景，就自动给高 `strict_score`。

只有当当前页面教学目标明确依赖某个属性、姿态、动作、数量、关系、可读内容或状态本身时，才提高对应风险分数和约束分数。

信息不足时，保持分数保守：`strict_score`、`readable_knowledge_score`、`unique_referent_score`、`exact_relation_score` 不要给高分；使用 `brief_reason` 说明不确定点。
