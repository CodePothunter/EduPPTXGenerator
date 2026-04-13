# 布局系统

EduPPTX 使用基于 EMU 坐标的槽位模板系统来定位 slide 上的所有元素。本文档详细说明坐标系、模板机制和卡片计算逻辑。

## EMU 坐标系

PowerPoint 内部使用 **EMU (English Metric Units)** 作为坐标单位：

```
1 inch  = 914,400 EMU
1 pt    = 12,700 EMU
1 cm    = 360,000 EMU
```

标准 16:9 画布尺寸：

```
宽度: 12,192,000 EMU = 960pt = 33.867cm
高度:  6,858,000 EMU = 540pt = 19.05cm
```

所有代码中的坐标和尺寸都是 EMU 整数值。`layout_engine.py` 顶部定义了常量：

```python
SLIDE_W = 12_192_000    # 960pt
SLIDE_H = 6_858_000     # 540pt
PT      = 12_700        # 1pt 的 EMU 值
```

## 画布分区

```
 ←───────────── SLIDE_W (960pt) ──────────────→
 ┌────────────────────────────────────────────┐ ↑
 │ ←MARGIN_X→ ←── CONTENT_W (800pt) ──→      │ │
 │            ┌──────────────────────┐        │ │
 │            │     标题 (60pt)      │ TITLE  │ MARGIN_Y
 │            ├──────────────────────┤        │ (50pt)
 │            │   副标题 (35pt)      │        │ │
 │            ├──────────────────────┤        │ │
 │            │                      │        │ │
 │            │   卡片区域           │        │ SLIDE_H
 │            │   (200pt 默认高度)   │        │ (540pt)
 │            │                      │        │ │
 │            ├──────────────────────┤        │ │
 │            │   页脚 (70pt)        │ FOOTER │ │
 │            └──────────────────────┘        │ │
 └────────────────────────────────────────────┘ ↓
```

关键垂直位置（从顶部算起）：

| 区域 | Y 坐标 | 高度 | pt 值 |
|------|--------|------|-------|
| 标题 | 635,000 | 762,000 | 50pt → 60pt |
| 副标题 | 1,397,000 | 444,500 | 110pt → 35pt |
| 卡片起始 | 2,159,000 | 2,540,000 | 170pt → 200pt |
| 页脚 | 5,334,000 | 889,000 | 420pt → 70pt |

## 槽位模板

每种 slide type 对应一个**槽位模板函数**，返回 `SlotLayout` 对象。`SlotLayout` 包含所有元素的坐标定义：

```python
@dataclass
class SlotLayout:
    background: SlotPosition     # 全画布 (0,0 → SLIDE_W, SLIDE_H)
    overlay: SlotPosition        # 全画布 (蒙版)
    title: SlotPosition          # 标题文本框
    subtitle: SlotPosition | None
    cards: list[SlotPosition]    # 卡片容器的坐标
    card_icons: list[SlotPosition]   # 图标坐标
    card_titles: list[SlotPosition]  # 卡片标题坐标
    card_bodies: list[SlotPosition]  # 卡片正文坐标
    footer: SlotPosition | None
    formula: SlotPosition | None
```

### 模板列表

| 模板 | 函数 | 默认卡片数 | 特殊元素 |
|------|------|-----------|---------|
| cover | `layout_cover()` | 3 | subtitle, formula |
| lead_in | `layout_lead_in()` | 4 | subtitle, footer |
| definition | `layout_definition()` | 3 | subtitle(定义框), footer |
| content | `layout_content()` | 3 | footer |
| example | `layout_example()` | 2 | 更高的卡片 |
| exercise | `layout_exercise()` | 3 | subtitle, 更高的卡片 |
| summary | `layout_summary()` | 5 | 更矮的卡片 (150pt), footer |
| closing | `layout_closing()` | 0 | 垂直居中的标题+副标题 |

`history`, `proof`, `extension` 复用 `layout_content`；`answer` 复用 `layout_exercise`。

### 模板分派

```python
_LAYOUT_MAP = {
    "cover": layout_cover,
    "lead_in": layout_lead_in,
    "definition": layout_definition,
    "content": layout_content,
    "history": layout_content,      # 复用
    "proof": layout_content,        # 复用
    "example": layout_example,
    "exercise": layout_exercise,
    "answer": layout_exercise,      # 复用
    "summary": layout_summary,
    "extension": layout_content,    # 复用
    "closing": layout_closing,
}

def get_layout(slide_type: str, n_cards: int) -> SlotLayout:
    func = _LAYOUT_MAP.get(slide_type, layout_content)
    if slide_type == "closing":
        return func()         # closing 没有卡片参数
    return func(n_cards)
```

## 卡片列计算

`_make_card_columns(n)` 是布局系统的核心算法。给定卡片数 n，它等分内容区宽度，生成每张卡片及其子元素的坐标：

```
n=3 的计算过程：

可用宽度 = CONTENT_W = 10,160,000 EMU (800pt)
卡片间距 = CARD_GAP = 254,000 EMU (20pt)
卡片宽度 = (CONTENT_W - GAP * (n-1)) / n
         = (10,160,000 - 254,000 * 2) / 3
         = 9,652,000 / 3
         = 3,217,333 EMU (~253pt)

卡片 X 坐标：
  Card 0: x = MARGIN_X = 1,016,000
  Card 1: x = 1,016,000 + 3,217,333 + 254,000 = 4,487,333
  Card 2: x = 4,487,333 + 3,217,333 + 254,000 = 7,958,666
```

### 卡片内部布局

每张卡片内部从上到下排列：

```
┌─────── card_w ───────┐
│ ← CARD_PAD (15pt) →  │ ↑
│  ┌──────────────┐    │ │ CARD_PAD
│  │  图标 (60pt) │    │ │
│  └──────────────┘    │ │
│  ← ICON_MARGIN → ── │ │ 10pt
│  ┌──────────────┐    │ │
│  │ 标题 (30pt)  │    │ │ 卡片
│  └──────────────┘    │ │ 高度
│  ← ICON_MARGIN → ── │ │
│  ┌──────────────┐    │ │
│  │              │    │ │
│  │ 正文 (填充)  │    │ │
│  │              │    │ │
│  └──────────────┘    │ │
│ ← CARD_PAD ───────→  │ ↓ CARD_PAD
└──────────────────────┘
```

图标在卡片内**水平居中**：`icon_x = card_x + (card_w - ICON_SIZE) / 2`

标题和正文有左右内边距 `CARD_PAD`，宽度为 `card_w - 2 * CARD_PAD`。

## 特殊模板

### closing（结束页）

唯一没有卡片的模板。标题和副标题**垂直居中**放置：

```python
title_y  = SLIDE_H // 2 - 762_000   # 中线上方 60pt
subtitle_y = SLIDE_H // 2 + 127_000  # 中线下方 10pt
```

### summary（总结页）

卡片高度缩短到 150pt（默认 200pt），以容纳更多卡片（通常 5 个）。

### definition（定义页）

副标题区域作为**定义框**（40pt 高），卡片区域下移到定义框下方，高度自适应填充到页脚区。

### example（例题页）

卡片从副标题下方开始（比默认高 50pt），高度扩展到页脚区，为例题内容提供更大空间。

## 扩展布局

添加新布局模板只需三步：

1. 在 `layout_engine.py` 中定义新函数：

```python
def layout_timeline(n_cards: int = 4) -> SlotLayout:
    # 定义槽位坐标...
    cards, icons, titles, bodies = _make_card_columns(n_cards)
    return SlotLayout(cards=cards, card_icons=icons, ...)
```

2. 注册到 `_LAYOUT_MAP`：

```python
_LAYOUT_MAP["timeline"] = layout_timeline
```

3. 在 `models.py` 的 `SlideType` 中添加类型：

```python
SlideType = Literal[..., "timeline"]
```

布局系统不关心内容，只负责坐标计算。渲染器会根据 `SlotLayout` 中的坐标放置元素。

## v2 布局系统：命名意图 + ResolvedShape

v2 管线用命名意图（named intents）替代硬编码常量：

### 命名意图预设

| 意图 | tight | comfortable | spacious |
|------|-------|-------------|----------|
| margin_left | 635,000 (50pt) | 1,016,000 (80pt) | 1,524,000 (120pt) |
| margin_top | 508,000 (40pt) | 635,000 (50pt) | 762,000 (60pt) |
| content_w | 10,922,000 (860pt) | 10,160,000 (800pt) | 9,144,000 (720pt) |

| 意图 | tight | normal | wide |
|------|-------|--------|------|
| card_spacing | 152,400 (12pt) | 304,800 (24pt) | 457,200 (36pt) |

| 意图 | small | medium | large |
|------|-------|--------|-------|
| icon_size | 304,800 (24pt) | 457,200 (36pt) | 609,600 (48pt) |

**comfortable + normal + large** 的值等于 v1 管线的硬编码常量，确保视觉一致。

### ResolvedShape 数据模型

v2 管线的中间表示。所有值都是具体的（EMU 坐标、hex 颜色），无引用：

```python
@dataclass
class ResolvedShape:
    shape_type: str       # textbox, rounded_rect, oval, image, line
    left: int; top: int; width: int; height: int  # EMU
    text: str | None
    font: ResolvedFont | None
    fill_color: str | None    # hex
    corner_radius: int        # OOXML 0-100000
    shadow: ResolvedShadow | None
    alpha_pct: int            # 0-100
    z_order: int              # 层叠顺序
    auto_shrink: bool         # normAutofit
    v_anchor: str             # t/ctr/b
```

PPTX writer 读取这些字段直接生成形状，不做任何决策。
