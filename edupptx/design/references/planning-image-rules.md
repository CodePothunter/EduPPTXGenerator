# 图片策划硬规则

`material_needs.images` 是数组，不是字典，也不是去重集合。允许出现多条 `role: "illustration"`。


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

- 每条 query 聚焦单一主体，再补充风格、场景、视角或用途。
- 如果需要风格统一，应把“风格统一 / 同色调 / 同一插画风格”分别写进每条 query，而不是把多个主体塞进一条 query。
- 左右分栏、双图对比、三图并列，优先使用 `4:3`、`3:4` 或 `1:1`，不要默认都写成 `16:9`。

## 5. timeline注意事项
- 对于 `timeline` 页面，如果每个时间线节点上方/旁边都有独立配图，则每个节点都视为一个独立图片区。
- `timeline` 中 `content_points` 有 N 条，且设计为节点配图时，`material_needs.images` 必须正好输出 N 条。
- 不允许只给前几个节点配图、后几个节点留空。
- `images` 数组顺序默认对应时间线从左到右的节点顺序。

## 6. 正反例

错误示例：

```json
{
  "design_notes": "顶部放问题标题，中间左右分栏分别放小蝌蚪和青蛙的图加对应特点文字，特点文字用彩色标注关键词",
  "material_needs": {
    "images": [
      {
        "query": "卡通小蝌蚪和大青蛙对比插画 左边小蝌蚪右边青蛙 风格统一",
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
        "query": "卡通小蝌蚪 科普插画 风格统一",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "4:3"
      },
      {
        "query": "卡通青蛙 科普插画 风格统一",
        "source": "ai_generate",
        "role": "illustration",
        "aspect_ratio": "4:3"
      }
    ]
  }
}
```
