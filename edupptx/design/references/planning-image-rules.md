# 图片策划硬规则

`material_needs.images` 是数组，不是字典，也不是去重集合。允许出现多条 `role: "illustration"`。


## 0. source field

- `material_needs.images[].source` must be exactly `ai_generate` or `search`.
- Use `ai_generate` for generated teaching illustrations, scenes, diagrams, and reusable visual assets.
- Use `search` only when a real public web image is explicitly needed.
- Do not output aliases such as `public_domain`, `web`, `stock`, `seedream`, or `generated`.


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

## 5. 可读教学内容必须具体

- 当页面涉及可读教学内容时，包括具体汉字、拼音、注音、笔画、笔顺、词语、句子、公式、数字、单位、日期、标签、选项或答案，必须在 `content_points`、`design_notes` 或 `material_needs.images[].query` 中显式写出具体内容。
- `material_needs.images[].query` 不允许只写“生字卡”“拼音卡”“田字格示例”“笔顺图”“公式图”等泛化描述。
- 如果图片只是通用工具或底图，query 必须明确说明不包含具体可读内容，例如“空白田字格底图，不含具体汉字、拼音或笔顺文字”。
- 如果图片需要承载具体可读内容，query 必须列出必须出现的具体文字、符号、数字、顺序或标签。
- 对多音字、形近字、偏旁部首、词语辨析、句子赏析、算式推导、实验标签等内容，query 或同页 `content_points` 必须保留具体对象及其对应关系，不能只保留类别名。

## 6. timeline注意事项
- 对于 `timeline` 页面，如果每个时间线节点上方/旁边都有独立配图，则每个节点都视为一个独立图片区。
- `timeline` 中 `content_points` 有 N 条，且设计为节点配图时，`material_needs.images` 必须正好输出 N 条。
- 不允许只给前几个节点配图、后几个节点留空。
- `images` 数组顺序默认对应时间线从左到右的节点顺序。

## 7. 正反例

错误示例：

```json
{
  "design_notes": "顶部放问题标题，中间左右分栏分别放小蝌蚪和青蛙的图加对应特点文字，特点文字用彩色标注关键词",
  "material_needs": {
    "images": [
      {
        "query": "小蝌蚪和大青蛙左右对比场景",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "16:9"
      }
    ]
  }
}
```

正确示例：

```json
{
  "design_notes": "顶部放问题标题，中间左右分栏分别放小蝌蚪和青蛙的图加对应特点文字，特点文字用彩色标注关键词",
  "material_needs": {
    "images": [
      {
        "query": "小蝌蚪",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "4:3"
      },
      {
        "query": "青蛙",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "4:3"
      }
    ]
  }
}
```
