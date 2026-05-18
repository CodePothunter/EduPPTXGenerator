# AI Image Reuse Metadata Score Rules

本规则用于生成素材复用元数据。LLM 负责输出稳定、可解释的连续分数、结构化视觉字段，以及少量必须参与复用过滤的 `core_constraints`。LLM 不直接输出 `reuse_level`、`asset_category`、`generic_support_allowed`、`reuse_risk`，也不直接判断素材是否应被 strict、medium、loose。

代码会根据固定阈值把分数和 `core_constraints` 确定性转换为内部字段。这样同一份 LLM 输出可以通过调整阈值重新分类，同时保留 LLM 对“哪些元素必须被 filter”的语义判断。

page_image 的内部复用等级由 `core_constraints` 和风险分数决定：
- 没有 `core_constraints` 且没有知识/关系/唯一指代强风险时，归为 `loose`，但仍沿用原 medium 阈值，不使用更低阈值。
- 有主体、动作、对象、情绪、地点等视觉语义 `core_constraints`，但不是文字、数量、公式、固定关系等高风险约束时，归为 `medium`，并进入 deterministic filter。
- 有可读文字、汉字/拼音、数量、公式、物理量、固定关系、知识点或强唯一指代风险时，归为 `strict`。

不要因为素材来自某篇课文、用于 content 页、写了“课文情节”“主旨”“母爱”“人物形象”等教学用途，就自动提高到 strict。普通文学辅助插图通常是 medium：它可以保护主体、动作或情绪极性，但不要求完全同一张情节图。

普通角色词不是唯一指代：`母亲`、`孩子`、`男孩`、`青年`、`人`、`学生`、`小朋友`通常不是 named entity。它们可以作为 medium 的 `core_constraints`，但不要把 `unique_referent_score` 或 `exact_relation_score` 打到 required，除非画面确实包含名人、专名地点、明确数量、可读文字或固定知识关系。

纯情绪表情示意图通常是 loose：例如“人喜出望外的表情插画”应把“喜出望外”放入 `primary_emotions`，不要放入 `core_constraints`；它可复用于同极性、近似情绪的词语理解场景。只有当情绪与具体主体、动作、情节强绑定时，才用 medium 的 emotion constraint。

## Required Output

每个 asset 必须输出：

- `asset_id`
- `normalized_prompt`
- `context_summary`
- `teaching_intent`
- `core_keywords`
- `semantic_aliases`
- `context_summary_keywords`
- `primary_subjects`
- `primary_actions`
- `primary_emotions`
- `teaching_objects`
- `soft_modifiers`
- `core_constraints`
- `reuse_scores`

如果 `asset_kind` 是 `background`，只输出：
- `asset_id`
- `normalized_prompt`
- `context_summary`
- `teaching_intent`
- `core_keywords`
- `semantic_aliases`
- `context_summary_keywords`

背景素材不参与 page_image 的 `loose / medium / strict` 复用等级判断，也不需要输出 `primary_subjects`、`primary_actions`、`primary_emotions`、`teaching_objects`、`soft_modifiers`、`core_constraints`、`reuse_scores`、`reuse_level`、`asset_category`、`generic_support_allowed` 或 `reuse_risk`。背景只用于 BM25、embedding、substring 与颜色倾向匹配。

结构化字段示例：

```json
{
  "primary_subjects": [
    {"name": "母亲", "strictness": "hard", "aliases": ["妈妈", "母亲角色"]}
  ],
  "primary_actions": [
    {"name": "摔东西", "strictness": "hard", "aliases": ["砸东西", "摔砸物品"]}
  ],
  "primary_emotions": [
    {"name": "暴怒", "strictness": "medium", "polarity": "negative", "aliases": ["愤怒", "情绪失控", "生气"]}
  ],
  "teaching_objects": [
    {"name": "田字格中的“比”字", "strictness": "hard", "aliases": ["比字田字格", "比字笔顺"]}
  ],
  "soft_modifiers": ["暖光", "彩色", "虚线"],
  "core_constraints": [
    {"kind": "text", "value": "“比”字", "exact": true, "hard": true, "aliases": ["比字"]}
  ]
}
```

`reuse_scores` 当前必须使用 `dimension_scores`，不要输出 `strict_score` 或 `loose_score`。代码会根据 `dimension_scores` 聚合出 `factual_risk_score`、`visual_guard_score` 和 `reuse_specificity_score`，再按阈值范围确定 loose / medium / strict。

```json
{
  "dimension_scores": {
    "subject_importance_score": 0.0,
    "action_importance_score": 0.0,
    "emotion_importance_score": 0.0,
    "teaching_object_importance_score": 0.0,
    "setting_importance_score": 0.0,
    "readable_text_score": 0.0,
    "count_integrity_score": 0.0,
    "exact_relation_score": 0.0,
    "unique_referent_score": 0.0,
    "generic_support_score": 0.0
  },
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

维度分数含义：

- `subject_importance_score`：主体被替换是否会影响语义；普通母亲/孩子/男孩通常只影响 medium，不应推成 strict。
- `action_importance_score`：动作缺失或替换是否影响语义。
- `emotion_importance_score`：情绪极性或近似情绪是否重要；纯表情示意图通常 loose。
- `teaching_object_importance_score`：教学对象是否需要保护。
- `setting_importance_score`：地点/场景是否是核心语义。
- `readable_text_score`：汉字、拼音、公式、标签、可读文本等可验证内容；高分会推 strict。
- `count_integrity_score`：明确数量、多主体完整性；高分会推 strict。
- `exact_relation_score`：固定知识关系、顺序、因果或必须精确的关系；高分会推 strict。
- `unique_referent_score`：名人、专名地点、专名作品对象等真正唯一指代；普通角色词不要给高分。
- `generic_support_score`：能否作为通用支持图复用。

旧版字段如下仅用于兼容已有素材库，不是新的生成要求：

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

`dimension_scores` 是唯一的新评分入口。不要给 loose、medium、strict 分别打可能性分；只判断各语义维度的重要性，代码会加权聚合并按阈值分档。

`subject_importance_score`、`action_importance_score`、`emotion_importance_score`、`teaching_object_importance_score`、`setting_importance_score` 会加权形成 `visual_guard_score`。这些维度高，通常只说明素材需要进入 medium filter，保护主体/动作/情绪/对象/地点，不应单独推成 strict。

`readable_text_score`、`count_integrity_score`、`exact_relation_score`、`unique_referent_score` 会形成 `factual_risk_score`。只有可读文字、明确数量、固定知识关系、专名/唯一指代等会造成事实错误的维度高，才允许进入 strict。

`reuse_specificity_score` 由 `factual_risk_score` 和 `visual_guard_score` 聚合得到。低于 medium 阈值且没有 core constraints 时为 loose；达到 medium 阈值或存在 visual core constraints 时为 medium；达到 strict 阈值且存在高风险约束时才为 strict。

`generic_support_score` 表示素材是否能作为泛化支持图、通用视觉辅助、通用学习行为、背景、氛围或通用工具复用。

## Category Scores

为每个候选类别分别打分，代码会选择最高且超过阈值的类别：

- `learning_behavior`：通用学习或课堂行为。
- `generic_tool`：可复用的视觉辅助、符号工具、标注气泡、图标、抽象支架。
- `generic_diagram`：不绑定具体知识对象的通用图示。
- `concept_scene`：可广泛复用的语义场景。
- `content_specific`：课文或知识点中的特定事件、具体可读内容、固定知识对象、固定顺序、具体对应关系，或关键元素变化会改变含义的图示。
- `character_action`：人物、角色或形象的身份、动作方向、作用对象、状态、情绪、数量或场景关系不可替换。

## Constraint Scores

`constraint_scores` 用于列出可能成为硬约束的候选元素，并提供连续分数供代码分类。

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

## Core Constraints

`core_constraints` 是真正进入 deterministic filter 的约束。不要把 `primary_subjects`、`primary_actions`、`teaching_objects`、`primary_emotions` 全量照搬进去；只有当替换或缺失会造成教学语义错误、课文角色错误、知识点错误、数量/关系错误时才放入。

`core_constraints` item 只能包含：

```json
{"kind": "entity", "value": "小蝌蚪", "exact": false, "hard": true, "aliases": ["蝌蚪幼体"]}
```

`kind` 必须来自 `Constraint Scores` 的 kind 集合。`hard=true` 表示候选不覆盖该元素时不能自动复用，应 reject 或进入 LLM review。`exact=true` 只用于文字、汉字、拼音、数字、公式、固定数量、固定关系等必须完全一致的对象；普通视觉主体一般用 `exact=false`。

应放入 `core_constraints` 的情况：

- 具体课文角色或动物不能替换：小蝌蚪不能换成松鼠；猴子/兔子/松鼠/公鸡/鸭子/孔雀在对应槽位不能互换。
- 多主体集合有明确成员或数量：六种动物合影、两个人比高矮、三类动物对比。
- 教学对象可验证：汉字、拼音、笔顺、田字格字形、发音部位、数量、公式、方向箭头。
- 核心动作承载教学目标：比高矮、朗读、圈画、摔东西、看窗外、看菊花。
- 情绪是页面核心语义时，用 `kind=emotion`，通常 `exact=false`，只要求极性或相近情绪。

不应放入 `core_constraints` 的情况：

- 纯装饰或软修饰：暖光、黄裙、彩色、虚线、木门材质、特写、装饰花纹。
- 难以视觉稳定表达的微动作：点头、恳求、期待、担忧；这些可放入 structured fields 或由 LLM review 判断。
- 可泛化主体：普通小朋友/学生/儿童在通用学习行为图中通常不需要 hard，除非页面明确要求某个具体人物或性别/年龄。

## Medium Visual Examples

以下素材应保持 medium 或 loose，不要因为来自课文内容页而自动 strict：

- `秋日黄昏，母亲和孩子并肩走在铺满落叶的路上的温暖场景`：母亲、孩子、温暖氛围可作为 medium 约束，但这不是唯一人物或固定情节，不应 required unique/exact relation。
- `男孩在房间摔东西拒绝出门的场景`：摔东西、拒绝出门、负面情绪可进入 medium 约束；除非画面包含可读课文原句或固定可验证知识，否则不应 strict。
- `母亲站在床边温柔劝说男孩的场景`：母亲/男孩/劝说/温柔是可保护的视觉语义，但普通床边交流可合理复用，不应因为“课文对应情节”直接 strict。
- `人喜出望外的表情插画`：纯情绪词表情示意应为 loose，不要把情绪词放进 hard `core_constraints`。

只有以下情况才从 medium 升到 strict：可读文字/汉字/拼音/公式/数量必须准确；多主体成员或数量必须完整；关系是可验证知识关系或 exact relation；地点是专名且缺失会造成事实错误。

## Structured Fields

`primary_subjects` 只保留 `name`、`strictness`、`aliases`。具体人物、具体动物、明确性别角色、承担核心语义的年龄阶段通常是 `hard`；青年、男孩、男人这类非核心年龄/性别差异可以通过 `aliases` 兼容为 `medium`。

`primary_actions` 至少保留 `name`、`strictness`、`aliases`。可见核心动作如摔东西、读书、写字、举旗、比高矮、看菊花通常是 `hard` 或 `medium`；点头、恳求、期待、担忧这类可由关系或氛围推断的动作通常是 `soft` 或 `medium`。

`primary_emotions` 保留 `name`、`strictness`、`polarity`、`aliases`。`polarity` 只能是 `positive`、`negative`、`neutral` 或 `mixed`。情绪要求 polarity 一致，但具体情绪名不要求完全一致，例如痛苦绝望可以和落寞、悲伤、低沉近似。

`teaching_objects` 至少保留 `name`、`strictness`、`aliases`。汉字、拼音、田字格、发音部位、笔顺、数量关系等教学对象通常是 `hard`。

`soft_modifiers` 放非核心修饰，例如暖光、彩色、虚线、特写、黄裙、装饰、像伞参照物、门材质等。不要把 soft modifier 抽成硬主体或硬对象。

## Calibration

普通主体、普通动物、普通人物、普通地点、普通单一动作、物种默认外观特征、常见生理特征、普通姿态、普通装饰、普通表情、常见动作和常见持物，通常不应给高事实风险维度分数，也不应给过高 `importance_score`。

如果素材虽然包含具体主体、动作、状态或外观属性，但仍可安全作为普通同主体形象、普通角色形象、普通场景或泛化插图使用，应降低 `readable_text_score`、`count_integrity_score`、`exact_relation_score`、`unique_referent_score`，提高 `generic_support_score` 或相应通用类别分数。

不要因为素材来自某篇课文、服务于知识讲解、展示普通概念、普通实体或普通场景，就自动给高事实风险维度分数。

只有当当前页面教学目标明确依赖某个属性、姿态、动作、数量、关系、可读内容或状态本身时，才提高对应风险分数和约束分数。

信息不足时，保持分数保守：`readable_text_score`、`count_integrity_score`、`exact_relation_score`、`unique_referent_score` 不要给高分；使用 `brief_reason` 说明不确定点。

## Few-Shot Calibration

### 课文角色主体必须一致

目标素材：

```json
{
  "content_prompt": "卡通小蝌蚪举着小旗子带路的插图",
  "primary_subjects": [{"name": "小蝌蚪", "strictness": "hard", "aliases": ["蝌蚪幼体"]}],
  "primary_actions": [{"name": "举旗带路", "strictness": "medium", "aliases": ["持旗引路"]}],
  "soft_modifiers": ["卡通风格", "小旗子"],
  "core_constraints": [
    {"kind": "entity", "value": "小蝌蚪", "exact": false, "hard": true, "aliases": ["蝌蚪幼体"]}
  ]
}
```

理由：小蝌蚪是课文角色，不能被松鼠、猴子等主体替换；举旗带路是目录引导动作，可进入 structured fields，但不必强制 exact。

### 通用学习行为只约束核心动作

```json
{
  "content_prompt": "小朋友坐在书桌前读语文书",
  "primary_subjects": [{"name": "小朋友", "strictness": "medium", "aliases": ["学生", "儿童"]}],
  "primary_actions": [{"name": "读书", "strictness": "hard", "aliases": ["阅读", "朗读"]}],
  "teaching_objects": [{"name": "语文书", "strictness": "medium", "aliases": ["语文课本", "课本"]}],
  "core_constraints": [
    {"kind": "action", "value": "读书", "exact": false, "hard": true, "aliases": ["阅读", "朗读"]}
  ]
}
```

理由：学生/儿童可泛化，书桌可缺失；页面目标是阅读行为，因此动作需要进入 filter。

### 多主体数量与成员必须保护

```json
{
  "content_prompt": "猴子、兔子、松鼠、公鸡、鸭子、孔雀六种动物站在森林比赛场的大合影",
  "primary_subjects": [
    {"name": "猴子", "strictness": "hard", "aliases": ["小猴子"]},
    {"name": "兔子", "strictness": "hard", "aliases": ["小兔子"]},
    {"name": "松鼠", "strictness": "hard", "aliases": ["小松鼠"]},
    {"name": "公鸡", "strictness": "hard", "aliases": ["大公鸡"]},
    {"name": "鸭子", "strictness": "hard", "aliases": ["小鸭子"]},
    {"name": "孔雀", "strictness": "hard", "aliases": ["孔雀"] }
  ],
  "core_constraints": [
    {"kind": "entity", "value": "猴子", "exact": false, "hard": true, "aliases": ["小猴子"]},
    {"kind": "entity", "value": "兔子", "exact": false, "hard": true, "aliases": ["小兔子"]},
    {"kind": "entity", "value": "松鼠", "exact": false, "hard": true, "aliases": ["小松鼠"]},
    {"kind": "entity", "value": "公鸡", "exact": false, "hard": true, "aliases": ["大公鸡"]},
    {"kind": "entity", "value": "鸭子", "exact": false, "hard": true, "aliases": ["小鸭子"]},
    {"kind": "entity", "value": "孔雀", "exact": false, "hard": true, "aliases": ["孔雀"]},
    {"kind": "count", "value": "六种动物", "exact": true, "hard": true, "aliases": ["6种动物"]}
  ]
}
```

理由：六动物合影不是泛化动物图，三动物组图不能自动替代。

### 教学文字与笔顺必须精确

```json
{
  "content_prompt": "田字格中的“比”字，笔顺用不同颜色标注，左窄右宽结构用虚线标出",
  "teaching_objects": [{"name": "“比”字笔顺", "strictness": "hard", "aliases": ["比字笔顺"]}],
  "soft_modifiers": ["不同颜色", "虚线", "左窄右宽"],
  "core_constraints": [
    {"kind": "text", "value": "“比”字", "exact": true, "hard": true, "aliases": ["比字"]},
    {"kind": "object", "value": "笔顺", "exact": false, "hard": true, "aliases": ["笔画顺序"]}
  ]
}
```

理由：汉字本身不能错；彩色、虚线、结构提示是修饰或辅助标注，不能因为缺少这些修饰直接 hard reject。
