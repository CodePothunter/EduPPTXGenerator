# AI 图片复用元数据规则

必须只返回严格 JSON，顶层对象必须包含 `assets` 数组。

## 允许输出的字段

`page_image` 只允许输出：

```json
{
  "asset_id": "",
  "caption": "",
  "context_summary": "",
  "teaching_intent": "",
  "subject": "其他",
  "grade_norm": "其他",
  "grade_band": "其他",
  "general": false,
  "strict_reuse_group": "",
  "strict_reuse_secondary_group": "",
  "secondary_reuse_query": "",
  "secondary_reuse_caption": "",
  "strict_reuse_confidence": 0.0,
  "strict_reuse_reason": ""
}
```

`background` 只允许输出：

```json
{
  "asset_id": "",
  "normalized_prompt": "",
  "color_temperature": "",
  "context_summary": "",
  "teaching_intent": "",
  "subject": "其他",
  "grade_norm": "其他",
  "grade_band": "其他",
  "general": false,
  "strict_reuse_group": "",
  "strict_reuse_secondary_group": "",
  "strict_reuse_confidence": 0.0,
  "strict_reuse_reason": ""
}
```

不要输出 `core_keywords`、`semantic_aliases`、`constraints`、`context_summary_keywords`、`asset_category`、`query_aliases` 或其他查询别名字段。

## 学科与年级字段

`subject` 必须只从以下枚举中选择：`语文`、`数学`、`物理`、`其他`。

`grade_norm` 必须只从以下枚举中选择：`一年级`、`二年级`、`三年级`、`四年级`、`五年级`、`六年级`、`七年级`、`八年级`、`九年级`、`高一`、`高二`、`高三`、`其他`。

`grade_band` 必须只从以下枚举中选择：`低年级`、`高年级`、`其他`。

请根据 `theme`、`caption`、`subject_hint`、`grade_hint` 和用户显式线索自行判断并归一这三个字段。即使输入字段已有值，也必须重新输出上述枚举，不要复制非枚举格式。无法判断、不确定或缺少线索时输出 `其他`。不要依赖固定格式；只要内容语义能明确指向某个学科、年级或学段，就给出对应枚举。

## 通用复用字段

`general` 必须是布尔值，表示当前素材本身是否可以跨 `语文`、`数学`、`物理` 通用复用。

判定顺序：先查强排除，命中任一即 `general=false`；都不命中则 `general=true`。不要因“判断模糊”就默认 `false`。

强排除包括：确定可读汉字/词/句/拼音/数值/公式/台词/标签/栏目词；具名人物、地点、物体、作品、典故；故事绑定的角色/事件/动作/强情绪叙事；具体知识结构、流程、原理、变量对比图示；历史文物、考古器物、古人/古装人物、具体历史/民俗/典故场景。显微镜、放大镜、直尺/尺子、硬币/1元硬币、砝码、温度计、烧杯等跨数学、物理、生活场景都可能复用的中性器材或实物，不因学科来源触发 `false`。

语文/历史/文化绑定的整体场景题材输出 `false`：带古装人物活动、具名地点/地标、具体典故/诗文情节、历史民俗语境、强叙事或强情绪氛围的整体场景。

纯装饰底图豁免：无人物、无具名地点、无叙事、无具体知识/故事载荷的整体山水、群山、风景、园林、氛围背景、装饰图案、纹样底图，不触发 `false`；若未命中其他强排除，则 `general=true`。国画、水墨、水彩、青绿、写实笔法本身不是 `false` 触发器。

仅在未命中强排除时，领域中性的离散通用视觉元素输出 `true`：通用卡通角色/人物、单个动植物、日常物件、通用工具、装饰/边框/空白容器/对话气泡、空白脚手架。常见计量、实验、生活实物及其中性测量/盛放场景也输出 `true`，例如尺子、硬币、1元硬币、砝码、温度计、烧杯。离散单主体即便使用国画/水墨风格，也仍可 `true`。

## 检索字段

页面图片检索只使用 `caption`。

背景图检索只使用 `normalized_prompt`。

`context_summary` 和 `teaching_intent` 只作为素材库元数据保留，不作为检索文本。

## 类别路由

`strict_reuse_group` 必须是当前 4 个素材类别主类 ID 之一：`C00_strict_text_problem_skip`、`C01_irreplaceable_entity_event_action`、`C02_generic_subject_object`、`C03_scene_decor_container`。`C00_strict_text_problem_skip` 表示图片需要精确匹配文字、数字、公式、符号、题目、选项或原文段落；这类素材跳过复用，也不写入可复用匹配索引。

`strict_reuse_secondary_group` 只在主类为 `C01_irreplaceable_entity_event_action` 的具名地标图、且周边场景本身也可作氛围复用时输出 `C03_scene_decor_container`。纯肖像、角色、文献、结构图及其它情况省略该字段。

命中上述副标签时，额外写 `secondary_reuse_query` 与 `secondary_reuse_caption`：两者都必须删地标/人物等专名，保留天气、时段、季节、光照、水景/江景/楼阁/远山等可迁移场景属性。canonical `query`/`caption` 仍保留 C01 具名身份。

启用真双写：主类 C01、副类 C03 的具名地标场景图，会以去名通用场景 caption（删地标/人物专名、保留天气/场景等区分属性）投影写入 C03 split；投影条目带 `secondary_projection` 标记，canonical 仍以 C01 为准。

`strict_reuse_group` 分类只能依据 `query` 的字面内容。不要用 `page_type`、`subject`、`grade_norm`、`grade_band`、`image_role` 来判断类别。

## 背景规范化

`normalized_prompt` 是紧凑的视觉特征列表，不是完整自然语言 prompt。尽量使用以下格式：

`色调:X; 纹理:Y; 明度:Z; 构图:W`

只使用客观视觉词。冷色、暖色、中性色写入 `color_temperature`，不要写入 `normalized_prompt`。
