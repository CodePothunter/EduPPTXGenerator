# 图片策划硬规则

`material_needs.images` 是数组，不是字典，也不是去重集合。允许出现多条 `role: "illustration"`。


## 0. source 字段

- `material_needs.images[].source` 必须严格为 `ai_generate` 或 `search`。
- 对于生成的教学插图、场景、图表和可复用视觉素材，使用 `ai_generate`。
- 仅当明确需要真实的公开网页图片时，才使用 `search`。
- 不要输出 `public_domain`、`web`、`stock`、`seedream` 或 `generated` 等别名。


## 1. 按图片区数量列图

- 如果版面中有 2 个独立图片区，就必须输出 2 条 `images`。
- 如果版面中有 3 个独立图片区，就必须输出 3 条 `images`。
- 每一条 `images` 只服务一个图片区、一个主主体，不要一条记录覆盖多个图片区。

## 2. 禁止把多图需求压成一张合成图

- 不要用一条 query 同时描述多个应分开展示的主体。
- 不要写“左边 A 右边 B”“A 和 B 对比插画”“三种事物同框”来代替多张图片。
- 只有当页面设计明确需要一张主视觉、封面 hero 图、整页横幅图或单张信息图时，才允许一条图片需求承载多个概念。

## 3. 同一个 role 可以重复

- 多张内容配图时，连续使用 `role: "illustration"` 即可。
- `images` 数组顺序默认对应版面顺序：先左后右、先上后下。

## 4. query 的写法

- 每条 query 只负责“画什么”：聚焦单一主体、必要场景、可见动作或教学内容。
- 不要在 query 中写年级画风、编辑感、绘本感、统一风格、高清、无文字、无水印、构图约束等生成风格词；这些由图片 prompt 路由在生成阶段补全。
- 如果多张图需要风格统一，也不要把“风格统一 / 同色调 / 同一插画风格”写进 query；系统会按模板家族、role、page_type 和 aspect_ratio 统一补充。
- 左右分栏、双图对比、三图并列，优先使用 `4:3`、`3:4` 或 `1:1`，不要默认都写成 `16:9`。

## 5. 精确教学载荷不得由图片生成

- 大段文字、公式、算式、具体汉字（生字）、词语、拼音、句子、题干、选项、答案，以及田字格、米字格、拼音四线格、表格、线格、坐标网格等，必须写入 `content_points`，由 SVG 生成文字/线条，不得规划为图片。
- 不要用空白田字格图、生字卡占位图、公式卡占位图等变相承载这些文字——田字格、表格、线格等结构由 SVG 线条绘制，文字由 SVG 文本层写入。
- `material_needs.images` 只用于场景、人物、动物、物体、情境、装饰等插画。
- query 要写清画面对象、场景和空间/动作关系，不要用“本课、这些、若干、多个、重点”等泛指词代替具体画面对象。
- 宽松装饰框、大留白卡片、背景纹理等不要求精密对齐的装饰图，仍可作为图片，供 SVG 文本层叠加文字。

## 6. 页面内容、图片和讲稿一致性

- query 不要引入当前页 `title`、`content_points` 或教学目标中没有支撑的新知识、新对象、新标签或新文本。
- `design_notes` 必须说明图片如何服务页面内容；精确文字、公式、汉字、词语等由 SVG 文本层承载，不放进图片。
- `notes` 可以包含导入、提问、讲解、分析、拓展和课堂互动，不要求只复述图片内容。
- 但当 `notes` 明确引用图片时，必须和 `material_needs.images[].query`、`design_notes` 保持一致。凡是“看图中、图上、这张图显示、图片里有、从图中可以看到”等视觉指认，只能描述 query 和 design_notes 已规划的可见内容。
- 不要把教师补充讲解说成图片已经呈现的内容。

## 7. timeline注意事项

- 对于 `timeline` 页面，如果每个时间线节点上方/旁边都有独立配图，则每个节点都视为一个独立图片区。
- `timeline` 中 `content_points` 有 N 条，且设计为节点配图时，`material_needs.images` 必须正好输出 N 条。
- 不允许只给前几个节点配图、后几个节点留空。
- `images` 数组顺序默认对应时间线从左到右的节点顺序。

## 8. 通用正反例

### 多个独立观察对象不要压成一张图

错误：

```json
{
  "design_notes": "页面用左右两栏分别观察对象A和对象B，并在各自下方写特点。",
  "material_needs": {
    "images": [
      {
        "query": "对象A和对象B左右对比场景",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "16:9"
      }
    ]
  }
}
```

正确：

```json
{
  "design_notes": "页面用左右两栏分别观察对象A和对象B，并在各自下方写特点。",
  "material_needs": {
    "images": [
      {
        "query": "对象A的单独观察图",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "4:3"
      },
      {
        "query": "对象B的单独观察图",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "4:3"
      }
    ]
  }
}
```

### 可读内容写进 content_points，不要塞进图片

错误：

```json
{
  "material_needs": {
    "images": [
      {
        "query": "本页重点内容卡片，标注正确文字",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "4:3"
      }
    ]
  }
}
```

正确：

```json
{
  "content_points": ["把本页要呈现的具体文字、词语、公式逐条写在这里"],
  "design_notes": "具体文字由 SVG 文本层和卡片（rect）承载，本页不为承载文字而生成图片。",
  "material_needs": { "images": [] }
}
```
