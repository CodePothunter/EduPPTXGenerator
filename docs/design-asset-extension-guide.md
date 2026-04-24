# 新增颜色、图片特征与 SVG 模板指引

本文说明在现有设计系统中新增颜色、图片生成特征、页面 SVG 模板时需要修改的位置。除非新增 `page_type` 或 `layout_hint`，普通新增模板不需要改路由代码。

## 1. 新增颜色方案

主要修改文件：

- `edupptx/design/references/palette-routing.xml`

新增步骤：

1. 在 `<palette_library>` 中新增一个 `<palette id="...">`。
2. 至少补齐这些颜色字段：`primary`、`secondary`、`accent`、`card_bg`、`secondary_bg`、`text`、`heading`。
3. 在 `<palette_routing>` 中新增 `<rule priority="...">`，用 `match_terms` 描述命中关键词，用 `apply_palette` 指向新增 palette id。
4. 如需影响 LLM 生成 SVG 时的颜色倾向，在 `<palette_color_bias>` 中补同 id 的说明。
5. 运行 palette 路由测试，确认关键词能命中新颜色。

示例：

```xml
<palette id="ocean_lab_blue">
  <primary>#2D7DD2</primary>
  <secondary>#97DFFC</secondary>
  <accent>#F6AE2D</accent>
  <card_bg>#FFFFFF</card_bg>
  <secondary_bg>#EAF7FF</secondary_bg>
  <text>#203040</text>
  <heading>#1E5C99</heading>
</palette>

<rule priority="105">
  <match_terms>海洋, 水循环, 科学实验, 海水</match_terms>
  <apply_palette>ocean_lab_blue</apply_palette>
</rule>
```

注意事项：

- `priority` 越高越优先。主题越具体，priority 应越高。
- 不要只加 palette 不加 routing rule，否则它不会自动命中。
- SVG 模板中的硬编码颜色不会自动替换成 palette。生成阶段会把 palette 作为提示传给设计模型，因此模板本身应避免过多不可调整的强风格颜色。

## 2. 新增图片特点

主要修改文件：

- `edupptx/design/references/image-prompt-profiles.json`
- 相关模板目录下的 `metadata.xml`

`image-prompt-profiles.json` 控制图片提示词风格。新增 profile 时，通常加入到 `profiles` 数组：

```json
{
  "id": "lower_grade_ocean_scene",
  "match": {
    "template_families": ["低年级"],
    "keywords_any": ["海洋", "小鱼", "浪花", "水循环"]
  },
  "prompt_terms": [
    "明亮干净的低年级自然场景",
    "主体轮廓清晰",
    "保留适合文字排版的留白"
  ],
  "negative_terms": [
    "不要复杂背景",
    "不要真实恐怖动物"
  ]
}
```

匹配字段说明：

- `template_families`：限定模板家族，如 `低年级`、`高年级`、`复用`。
- `page_types`：限定页面类型，如 `cover`、`content`、`summary`。
- `roles`：限定图片角色，如 `hero`、`illustration`、`icon`、`background`。
- `keywords_any`：命中任意一个关键词即可加分。
- `keywords_all`：所有关键词都出现才匹配，适合更窄的场景。

如果某个模板需要固定图片槽位，在对应 `metadata.xml` 的 `<page>` 内维护 `<image_slots>`：

```xml
<image_slots>
  <slot role="hero" aspect_ratio="16:9" query_from="title" source="generate"/>
</image_slots>
```

注意事项：

- 图片 profile 只影响图片提示词，不会改变页面模板路由。
- `image_range` 写在 `variant_catalog` 中，用于模板变体打分和规划阶段提示。
- 如果模板不需要图片，写 `<image_range min="0" max="0"/>`，避免规划阶段硬塞图片。

## 3. 新增 SVG 模板

主要修改位置：

- `edupptx/design/page_templates/<模板家族>/你的模板.svg`
- `edupptx/design/page_templates/<模板家族>/metadata.xml`
- 可选：`edupptx/design/page_templates/<模板家族>/style_guide.md`

模板家族通常是：

- `低年级`
- `高年级`
- `复用`

新增步骤：

1. 把 SVG 放到对应模板家族目录。
2. 在同目录 `metadata.xml` 的 `<variant_catalog>` 中新增 `<variant>`。
3. `stem` 必须等于 SVG 文件名去掉 `.svg` 后的名称。
4. `page_type` 使用已有页面类型。`comparison` 和 `relation` 不是 `page_type`，应写成 `page_type="content"` 加对应 `layout_hint`。
5. 填写 `image_range`、`card_range`、`subcard_range`、`hit_keywords`、`page_features`、`reference_rule`。
6. 运行路由测试，确认新模板可以命中。

示例：

```xml
<variant stem="content_relation_2" page_type="content">
  <layout_hint>relation</layout_hint>
  <hit_keywords>关系图, 因果关系, 结构关系, 分类整理</hit_keywords>
  <image_range min="0" max="0"/>
  <card_range min="1" max="1"/>
  <subcard_range min="3" max="6"/>
  <page_features>一张外层大卡承载 3 到 6 个关系节点。</page_features>
  <reference_rule>保持图式关系，不要退化成普通项目列表。</reference_rule>
</variant>
```

字段语义：

- `page_type`：页面功能类型，来自 `PageType`，如 `cover`、`toc`、`content`、`exercise`、`summary`。
- `layout_hint`：版式形态，如 `comparison`、`relation`、`hero_with_microcards`、`mixed_grid`。
- `image_range`：该模板建议的图片数量。
- `card_range`：只代表顶层或外层大 card 数量，不包含内部小卡、节点、表格行列。
- `subcard_range`：代表内部子 card、microcard、关系节点、比较项、表格行列等内部结构数量。
- `hit_keywords`：路由加分关键词。它不是唯一命中依据，但会影响同类模板排序。
- `page_features`：给规划阶段看的模板结构说明。
- `reference_rule`：约束规划阶段如何参考模板，避免机械照抄。

路由命中依据：

- 先根据课件主题选择模板家族。
- 页面级别路由时，低年级或高年级主模板与 `复用` 模板同等参与变体打分。
- 变体打分综合 `page_type`、`layout_hint`、`hit_keywords`、`card_range`、`subcard_range`、`image_range` 和专用规则。
- `复用` 目录不靠自身 tags 抢主家族命中，它会在主家族确定后自动合并参与页面级打分。

## 4. 什么时候需要改代码

不需要改代码的情况：

- 只新增颜色 palette，并配置已有 routing 规则字段。
- 只新增图片 profile，并使用已有 match 字段。
- 只新增 SVG 模板，且使用已有 `page_type` 和已有 `layout_hint`。
- 只给已有模板补 `image_range`、`card_range`、`subcard_range`、关键词或说明。

需要改代码的情况：

- 新增 `page_type`：修改 `edupptx/models.py`、规划提示、内容规划器、模板路由测试。
- 新增 `layout_hint`：修改 `edupptx/models.py`、`edupptx/planning/content_planner.py`、规划提示、必要的设计提示和路由测试。
- 新增 metadata 字段并希望参与路由打分：修改 `edupptx/design/template_router.py` 的数据模型、解析逻辑、打分逻辑和测试。
- 新增图片 profile match 字段：修改图片提示词路由代码和测试。

## 5. 回归验证

推荐至少运行：

```powershell
python -m pytest tests/test_template_router.py tests/test_palette_routing.py tests/test_image_prompt_router.py
```

如果本机默认 Python 缺少依赖，应使用项目当前可运行的 Python 环境执行同样的测试。新增模板后，建议再用一个低年级 session 和一个高年级 session 做生成验证，重点看模板命中日志、SVG 预览和 PPTX 转换后的文本位置。
