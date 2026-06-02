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

采用严格保守判断：只有明确可跨学科复用时输出 `true`；如果素材依赖具体学科、固定文字、固定数字、精确图形关系、课文故事、文学或文化身份、科学现象或实验结构，输出 `false`；判断模糊时输出 `false`。

强排除优先于通用白名单。强排除包括汉字书写、田字格、米字格、拼音、课文、古诗、文言文、命名作者或课文故事；固定数字、算式、公式、题干、几何图、坐标图、统计图、测量图；光路、透镜、电路、力学、实验装置、带标注仪器；固定文字、标签、门牌、路牌、具体故事情节、命名角色和传统文化语境。

仅在未命中强排除时，空白卡片、便签、空白对话气泡、空白文本框、边框、相框、装饰图案、教师、学生、教室、校园、普通学习动作、文具、书本、书包、普通动物、植物、简单装饰物可以输出 `true`。

整体山水/风景/园林/场景/氛围图（任何媒介，含国画/水墨/水彩/写实）输出 `false`（偏语文 register）；离散单主体（单个动植物、日常物件、通用工具、卡通角色、装饰/边框/空白容器）即便用国画/水墨风格仍可 `true`。国画/水墨笔法本身不触发 `false`。

## 检索字段

页面图片检索只使用 `caption`。

背景图检索只使用 `normalized_prompt`。

`context_summary` 和 `teaching_intent` 只作为素材库元数据保留，不作为检索文本。

## 类别路由

`strict_reuse_group` 必须是当前 4 个素材类别主类 ID 之一：`C00_strict_text_problem_skip`、`C01_irreplaceable_entity_event_action`、`C02_generic_subject_object`、`C03_scene_decor_container`。`C00_strict_text_problem_skip` 表示图片需要精确匹配文字、数字、公式、符号、题目、选项或原文段落；这类素材跳过复用，也不写入可复用匹配索引。

`strict_reuse_secondary_group` 只在主类为 `C01_irreplaceable_entity_event_action` 的具名地标图、且周边场景本身也可作氛围复用时输出 `C03_scene_decor_container`。纯肖像、角色、文献、结构图及其它情况省略该字段。

启用真双写：主类 C01、副类 C03 的具名地标场景图，会以去名通用场景 caption（删地标/人物专名、保留天气/场景等区分属性）投影写入 C03 split；投影条目带 `secondary_projection` 标记，canonical 仍以 C01 为准。

`strict_reuse_group` 分类只能依据 `query` 的字面内容。不要用 `page_type`、`subject`、`grade_norm`、`grade_band`、`image_role` 来判断类别。

## 背景规范化

`normalized_prompt` 是紧凑的视觉特征列表，不是完整自然语言 prompt。尽量使用以下格式：

`色调:X; 纹理:Y; 明度:Z; 构图:W`

只使用客观视觉词。冷色、暖色、中性色写入 `color_temperature`，不要写入 `normalized_prompt`。
