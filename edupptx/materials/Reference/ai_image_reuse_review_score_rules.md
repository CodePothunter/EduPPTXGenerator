# AI 图像复用审核评分规则

你正在审核一个可复用 AI 图像候选素材，是否可以安全替代教育 PPT 中的目标图像需求。

只返回严格 JSON。审核阶段只输出连续分数和证据，不输出 accept/reject/uncertain 决策；代码会在评分后应用阈值。

## 必须输出

```json
{
  "score": 0.0,
  "evidence": [],
  "risk_factors": [],
  "brief_reason": "",
  "matched_constraints": [],
  "mismatched_constraints": [],
  "missing_constraints": []
}
```

`matched_constraints`、`mismatched_constraints` 和 `missing_constraints` 使用：

```json
{
  "kind": "entity | object | action | scene | emotion | text | math | physics",
  "value": "",
  "importance": 0,
  "confidence": 0.0,
  "evidence": "",
  "reason": ""
}
```

## 评分区间

`score` 是 0.00 到 1.00 的数字：

- 0.90-1.00：目标和候选在教学目的上几乎等价。
- 0.75-0.89：核心主体、动作、对象、知识内容或关系匹配，仅存在轻微背景、风格、颜色或构图差异。
- 0.60-0.74：候选有明确相似性，但存在需要代码阈值处理的语义风险。
- 0.30-0.59：部分相似，但主体、动作、对象、状态、关系或教学目的存在明显缺口。
- 0.00-0.29：不适合替代；复用会造成教学错误或语义错配。

## 审核标准

检查可见语义和教学语义：

- 主体、对象、动作、场景和情绪。
- 可读文字、拼音、标签、答案、数学公式、数量、单位和物理量。
- 主体-动作-对象绑定关系，以及正确主体是否对正确对象执行正确动作。
- 当计数、顺序、因果、空间关系或故事关系会改变教学事实时，必须检查这些内容。
- 具名人物、故事节点、知识对象、公式、单位和标签。

如果信息不足，降低分数，并在 `risk_factors` 中说明不确定性。

当核心语义匹配时，风格、渲染方式、颜色、轻微背景差异和小幅姿态差异仍可获得高分。主体、动作、对象、可读文字、公式、物理量、单位或教学事实不匹配时，必须给低分。

## 教学语境（teaching_intent / context_summary）软参考

`target` 和 `candidate` 都会带 `teaching_intent`、`context_summary`、`context_summary_keywords`、`topic_refs`。这些字段描述的是图像在某一页里"为什么用、怎么用"，**不是画面事实**。VLM 已显式拒绝把它们当作画面 constraint。

使用规则：

- **仅作为软参考**：用来判断"画面看着对上了，但教学情境是否合拍"。例如目标 `teaching_intent="作为识字课开篇情境导入"`，候选 `teaching_intent="作为单元复习总结"`，画面再相似也应该把 `score` 下调 0.05–0.10，并在 `risk_factors` 写明 `teaching_intent_mismatch`。
- **不作为硬约束**：teaching_intent / context_summary 文本不一致，**不构成** `mismatched_constraints`，也不单独触发低分封顶。它们只调节最终分，不替代画面证据。
- **不替代缺失证据**：当画面证据不足时，不要因为 teaching_intent 文字写得像就上调分数；画面看不到的东西，文字写得再像也不算证据。
- **识别模板化兜底文本**：当 `teaching_intent` 是 `_default_teaching_intent` 生成的通用兜底短语（典型如"插图支持本页教学内容"、"作为教学辅助"），视为信息缺失，不参与软调节。

允许的最大软调节幅度：±0.10。超出该幅度的判分必须由画面证据（constraints / visible semantics）单独支撑。

## 候选额外教学内容

`mismatched_constraints` 中如果包含 `kind` 为 `text` / `math` / `physics` 且 `subtype` 为 `teaching_content` 的项（即候选含有目标未要求的额外汉字、公式、物理量），必须给低分（≤ 0.55）。

理由：教学内容是精确匹配语义，候选不能额外携带教学事实。

例如：

- 目标”田字格中’比’字”，候选”田字格中’枚’和’比’字 + 笔顺”：候选包含额外汉字”枚”，应给低分（≤ 0.55）。
- 目标”勾股定理 $a^2+b^2=c^2$”，候选同时展示”$a^2+b^2=c^2$ 与 $S=\\pi r^2$”：候选包含额外公式，应给低分。
- 目标”F=ma”，候选展示”F=ma 与 W=mg”：候选包含额外公式，应给低分。

不适用本规则的情形：

- 额外内容并非教学事实，例如风格、装饰、背景元素：按常规标准评分。
- 额外内容是同一教学事实的等价表达（如简体/繁体、不同写法的同一字），按常规匹配判断。

## 主体替换的教学语义不可替换性

**评分前先回答这个判别问题**：如果把候选画面里的主视觉主体，替换成目标 `content_prompt` 要求的那个主体，本页的教学目标还成立吗？

封顶分级**完全由目标侧主视觉主体的 `subtype` 和 `importance` 决定**，不看具体 value 是什么字面词：

| 目标主体 subtype + importance | 主体身份跨值替换时的 `score` 封顶 |
|---|---|
| `entity.named_individual` imp=2 | **0.50**（具名个体不可替换） |
| `entity.species_instance` imp=2 | **0.50**（物种／文化符号身份就是教学事实） |
| `text/math/physics.teaching_content` imp=2 | 走「候选额外教学内容」段；本节不重复封顶 |
| `entity.species_instance` imp=1 | **0.70**（部分依赖该物种特征，跨值有偏差） |
| `entity.role` imp=1 | **0.70**（角色是叙事主体，跨称谓偏移叙事重心） |
| `entity.generic_class` imp=1 — 跨大类替换（动物↔植物↔人） | **0.50** |
| `entity.generic_class` imp=1 — 同大类跨子项（不同物种之间） | **0.50**，除非 `target_policy.asset_category ∈ {learning_behavior, generic_tool, generic_diagram}`（这三类强制 loose 装饰素材不封顶） |
| 任何 imp=0 的主体 | **不因主体差异封顶**（主体是占位，按其他维度正常评分） |

**判别细节**：

- “主体身份跨值”指候选画面的主视觉主体与目标 `content_prompt` 要求的主体在身份层面不一致；同身份不同风格／姿态／场景不属于跨值，不封顶。
- “主视觉主体”指 constraints 列表里 importance 最高且 kind=entity 的条目；如有多条并列同 importance，按多主体覆盖率综合判断（参考 reuse_policy 的 subject_coverage_undercoverage）。
- 本节封顶只针对 `entity` kind 的主体替换。`object` / `action` / `scene` 等其他 kind 的替换按常规评分维度处理。
- 封顶意为”`score` 不得高于此值”；封顶之上是否进一步扣分，仍由其他维度（动作／场景／情绪／教学内容匹配度）决定。

注意：本规则只封顶**主体身份**的替换。候选与目标的主体身份一致（同一具名个体／同一物种／同一角色），不适用本节封顶。

## 校准示例

跨学科分布，避免单一学科补丁化：

- 目标“显微镜下的植物细胞”，候选“显微镜下的动物细胞”：低分，受保护具象符号（细胞类型）替换。（生物，species_instance 替换）
- 目标“田字格中的‘比’字”，候选“比字笔顺图”：如果汉字和笔顺匹配，可以高分。候选“长字笔顺图”：低分。（语文，teaching_content+teaching_carrier 高内聚）
- 目标“地图上标注长江”，候选“地图同时标注长江和黄河”：低分，候选含额外教学标注。（地理，候选额外教学内容）
- 目标“学生读书”，候选“孩子学习 / 做作业”：对于宽松的学习行为插图，可以高分。（learning_behavior 通用等价）
- 目标“挥手告别”，候选“挥别 / 摆手告别”：可以高分。候选只有“站立微笑”：分数应降低。（generic_motion 等价 vs 缺失）
- 目标包含 `a^2+b^2=c^2`：不同公式必须给低分。（数学，teaching_content exact）
- 目标包含 `F=ma` 或特定单位标签：物理量、单位或标签变化时必须给低分。（物理，teaching_content + 单位）
- 如果输入 `transform_policy.decision=reject`，由于宽高比转换不安全，应给低分。
