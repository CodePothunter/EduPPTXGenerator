# AI Image Reuse Metadata Rules

还必须输出简化的复用策略字段和 `reuse_risk`。

`reuse_level` 只能是 `loose`、`medium`、`strict`。

`asset_category` 只能是 `learning_behavior`、`generic_tool`、`generic_diagram`、`concept_scene`、`content_specific`、`character_action`、`unknown`。

`core_constraints` 必须是对象数组，每个对象包含 `kind`、`value`、`exact`，可选 `hard`、`aliases`。

`kind` 只能是 `text`、`math`、`physics`、`entity`、`object`、`action`、`relation`、`setting`、`emotion`、`count`。

`generic_support_allowed` 必须是布尔值。

`reuse_risk` 必须包含 `readable_knowledge`、`unique_referent`、`exact_relation`；每项都必须是包含布尔 `required` 和数组 `evidence` 的对象。

这些字段不是关键词标签，而是用来判断相似但不完全相同的图片是否会破坏教学正确性。

## Asset Category

`learning_behavior` 表示通用学习或课堂行为，通常应为 `medium`。

`generic_tool` 表示可复用的视觉辅助、符号工具、标注气泡、图标、抽象支架，通常应为 `medium`。

`generic_diagram` 表示不绑定具体知识对象的通用图示。若图示绑定具体知识点、可读标签、结构关系、步骤、数量或答案，则不要仅按通用图示处理。

`concept_scene` 表示可广泛复用的语义场景。只要相似主体或相似场景通过阈值后仍能服务页面，通常应为 `medium`。

`content_specific` 表示课文或知识点中的特定事件、具体可读内容、固定知识对象、固定顺序、具体对应关系，或关键元素变化会改变含义的图示。

`character_action` 表示人物、角色或形象的身份、动作方向、作用对象、状态、情绪、数量或场景关系不可替换。

## Reuse Level

`loose` 只用于背景、纹理、纯氛围、纯装饰或不承载具体教学语义的素材。

`medium` 用于可泛化复用的素材。素材可以包含具体主体、动作、状态、外观属性或普通场景，但只要这些内容不是教学正确性必需条件，就不要标为 `strict`。

`strict` 只用于复用范围很窄的素材。只有当缺失、替换或弱化主体、动作、对象、数量、情绪、关系、可读内容、知识对象、固定顺序或事件节点会直接造成教学事实错误时，才使用 `strict`。

标为 `strict` 之前，先判断这张图是否仍可安全用于更泛化的同主体需求、普通角色形象需求、普通场景需求或普通插图需求。如果可以安全泛化复用，应使用 `medium`。

普通主体、普通动物、普通人物、普通地点或普通单一动作本身不构成 `strict`。页面需要出现某主体，只说明它应进入 `core_keywords`，不等于 `unique_referent`。

物种默认外观特征、常见生理特征、普通姿态、普通装饰、普通表情、常见动作和常见持物通常不构成 `strict`。只有当当前页面教学目标明确依赖这些属性、姿态、动作、数量、关系或状态本身时，才考虑 `strict`。

不要因为素材来自某篇课文、服务于知识讲解、展示普通概念、普通实体或普通场景，就自动标为 `strict`。

## Reuse Risk

`readable_knowledge` 只在图片承载具体可读、可数或可验证的教学内容时设为 `true`，包括具体文字、拼音、词语、句子、公式、数字、单位、答案、标签、图例、步骤编号、笔顺、笔画、结构关系或知识对应关系。

不要因为图片服务于知识讲解、展示普通概念、普通实体或普通场景，就把 `readable_knowledge` 设为 `true`。

`unique_referent` 只用于具名人物、具名地点、具名作品对象、具体课文情节节点，或同类替换会直接造成事实错误的唯一对象。

不要把普通动物、普通学生、普通老人、普通场景、识字方法、写字行为、观察行为或泛化角色标为 `unique_referent`。

`exact_relation` 只用于必须保留的具体主体-动作-客体、固定顺序、因果、空间关系、事件节点或知识对应关系。

抽象教学方法、通用隐喻、普通属性、常见外观或没有具体对象的泛化组合逻辑不要设为 `exact_relation`。

## Core Constraints

`strict` 素材通常必须有 `core_constraints`。请把必须过滤的内容拆成原子约束：

- `entity` 表示必需的主体、人物、角色或身份。
- `object` 表示必需的可见物体。
- `action` 表示必需的动作或行为。
- `setting` 表示必需地点或场景。
- `emotion` 表示必需情绪。
- `count` 表示必需数量。
- `relation` 表示主体-动作-客体、空间关系、因果、顺序或事件节点关系。

不可替换的约束设置 `hard: true`。

对于 `character_action`，如果动作方向、对象关系或情绪状态不可替换，应优先提取 `relation`、`action`、`object`、`emotion`，并使用泛化关系表达，而不是只列普通人物或普通动物关键词。

所有 `core_constraints` 都会先做精确、包含、`aliases` 匹配；不通过时再做同 `kind` 的 embedding 语义判断。

`text`、`math`、`physics`、`count`、`relation` 即使通过匹配或 embedding，也会进入 LLM 二次复核。

`entity`、`object`、`action`、`setting`、`emotion` 在高置信匹配时可以直接通过，灰区进入 LLM 复核。

视觉语义类约束可提供 `aliases`，用于表达同义或近义的泛化说法；不要把具体测试样例硬编码为规则。

如果无法可靠判断不可替换过滤条件，不要标 `strict`；使用 `medium`，并保持 `core_constraints` 为空。

所有非 `strict` 素材的 `core_constraints` 必须为空数组；不要给 `medium` 或 `loose` 输出约束。

`medium` 和 `loose` 的主体、动作、物体、状态、外观属性只进入 `core_keywords` 或 `semantic_aliases`。如果你认为某个主体、动作、物体、状态、数量或关系必须作为不可替换过滤条件，则不要标 `medium`，应标 `strict`。

普通可见主体、物体、动作、情绪、场景、颜色或氛围，如果不是教学正确性必需内容，只放入 `core_keywords` 或 `semantic_aliases`，不放入 `core_constraints`。

不要把视觉风格、颜色、质量、构图、比例、页面类型或 `prompt_route` 词放入 `core_constraints`。

## Generic Support

只有真正能作为泛化支持图、通用视觉辅助、背景、氛围、装饰、通用学习行为或通用工具的素材，才设置 `generic_support_allowed: true`。

如果素材包含不可替换的课文事实、知识对象、具体关系、固定顺序、数量、可读内容、角色关系或情绪转变，应设置 `generic_support_allowed: false`。

如果素材只是包含普通主体、普通动物、普通人物、普通外观属性、普通动作或普通场景，并且可以安全泛化复用，允许 `generic_support_allowed: true`。

## Fallback

信息不足时，使用 `reuse_level: medium`、`asset_category: unknown`、`core_constraints: []`、`generic_support_allowed: true`，并将 `reuse_risk` 各项 `required` 设为 `false`。
