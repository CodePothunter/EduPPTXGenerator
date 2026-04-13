# 布局系统

EduPPTX 使用基于 EMU 坐标的 Schema 驱动布局系统。样式 JSON 中的命名意图（如 `margin: "comfortable"`）被解析为具体 EMU 数值，再由 layout resolver 计算每个形状的精确坐标。

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

常量定义在 `style_schema.py`：

```python
SLIDE_W = 12_192_000    # 960pt
SLIDE_H = 6_858_000     # 540pt
PT      = 12_700        # 1pt 的 EMU 值
```

## 画布分区

```
 ←──────────────── SLIDE_W (960pt) ──────────────────→
 ┌──────────────────────────────────────────────────┐ ↑
 │ ←─ margin_left ─→ ←── content_w ──→             │ │
 │                  ┌──────────────────┐            │ │ margin_top
 │                  │   标题 (60pt)    │ TITLE      │ │
 │                  ├──────────────────┤            │ │
 │                  │  副标题 (35pt)   │            │ │
 │                  ├──────────────────┤            │ │
 │                  │                  │            │ SLIDE_H
 │                  │  卡片区域        │            │ (540pt)
 │                  │  (200pt)         │            │ │
 │                  │                  │            │ │
 │                  ├──────────────────┤            │ │
 │                  │  页脚 (70pt)     │ FOOTER     │ │
 │                  └──────────────────┘            │ │
 └──────────────────────────────────────────────────┘ ↓
```

margin_left 和 content_w 由样式 JSON 的 `layout.margin` 意图控制：

| margin 意图 | margin_left | content_w |
|-------------|-------------|-----------|
| tight | 50pt | 860pt |
| comfortable | 80pt | 800pt |
| spacious | 120pt | 720pt |

关键垂直位置（固定值，在 `style_schema.py` 定义）：

| 区域 | Y 坐标 | 高度 | pt 值 |
|------|--------|------|-------|
| 标题 | 635,000 | 762,000 | 50pt → 60pt |
| 副标题 | 1,397,000 | 444,500 | 110pt → 35pt |
| 卡片起始 | 2,159,000 | 2,540,000 | 170pt → 200pt |
| 页脚 | 5,334,000 | 889,000 | 420pt → 70pt |

## 命名意图系统

样式 JSON 中不写具体数值，而是写**意图名称**。`style_resolver` 将其解析为 EMU：

### margin（边距）

| 意图 | left | top | content_w |
|------|------|-----|-----------|
| tight | 635,000 (50pt) | 508,000 (40pt) | 10,922,000 (860pt) |
| comfortable | 1,016,000 (80pt) | 635,000 (50pt) | 10,160,000 (800pt) |
| spacious | 1,524,000 (120pt) | 762,000 (60pt) | 9,144,000 (720pt) |

### card_spacing（卡片间距）

| 意图 | gap |
|------|-----|
| tight | 152,400 (12pt) |
| normal | 304,800 (24pt) |
| wide | 457,200 (36pt) |

### icon_size（图标大小）

| 意图 | size |
|------|------|
| small | 304,800 (24pt) |
| medium | 457,200 (36pt) |
| large | 609,600 (48pt) |

### content_density（内容密度）

控制卡片内部间距，直接影响 body 文本可用空间：

| 意图 | card_pad | icon_margin | title_h |
|------|----------|-------------|---------|
| compact | 12pt | 6pt | 20pt |
| standard | 18pt | 12pt | 30pt |
| relaxed | 24pt | 18pt | 40pt |

**comfortable + normal + large + standard** 的组合等于默认值，确保视觉基线一致。

## Slide 类型与 Resolver

17 种 slide 类型由 `_SLIDE_RESOLVERS` dict 分派到 resolver 函数：

| 类型 | Resolver | 卡片 | 特殊元素 |
|------|----------|------|---------|
| cover | `_resolve_cover` | 3 | subtitle, formula, 标题居中 |
| content | `_resolve_content` | 2-3 | footer |
| lead_in | `_resolve_content` | 3 | footer |
| definition | `_resolve_content` | 2-3 | footer |
| history | `_resolve_content` | 3 | footer |
| proof | `_resolve_content` | 2-3 | formula, footer |
| example | `_resolve_content` | 1-2 | - |
| exercise | `_resolve_content` | 2-3 | - |
| answer | `_resolve_content` | 2-3 | - |
| summary | `_resolve_content` | 3-4 | footer |
| extension | `_resolve_content` | 2-3 | footer |
| big_quote | `_resolve_big_quote` | 0 | 引用栏装饰, 出处 |
| closing | `_resolve_closing` | 0 | 居中标题+副标题, 圆形装饰 |
| section | `_resolve_section` | 0 | 居中标题, 菱形装饰 |
| full_image | `_resolve_full_image` | 0 | 全宽素材区 |
| image_left | `_resolve_image_left` | 1-2 | 左 50% 素材 + 右卡片 |
| image_right | `_resolve_image_right` | 1-2 | 左卡片 + 右 50% 素材 |

大多数内容类 slide（lead_in, definition, history 等）复用 `_resolve_content`，它根据素材位置（center/left/right/full/none）自动调整卡片区域。

## 卡片列计算

`_resolve_cards()` 是布局核心。给定卡片数 n 和可用区域，等分宽度生成每张卡片：

```
n=3, comfortable margin, normal spacing:

可用宽度 = content_w = 800pt
卡片间距 = card_gap = 24pt
卡片宽度 = (800 - 24 * 2) / 3 ≈ 250pt

卡片 X 坐标：
  Card 0: x = 80pt
  Card 1: x = 80 + 250 + 24 = 354pt
  Card 2: x = 354 + 250 + 24 = 628pt
```

### 卡片内部布局

每张卡片内部从上到下排列，有三种自适应模式：

```
┌────── card_w ──────┐
│ ← card_pad →       │
│  ┌────────────┐    │   Full 模式 (48pt icon)
│  │  图标 48pt │    │   或
│  └────────────┘    │   Compact 模式 (32pt icon)
│  ← icon_margin →   │   或
│  ┌────────────┐    │   Minimal 模式 (无图标)
│  │ 标题 30pt  │    │
│  └────────────┘    │
│  ← icon_margin →   │
│  ┌────────────┐    │
│  │ body 填充  │    │   ← body_h 必须 >= 38pt
│  └────────────┘    │
│ ← card_pad →       │
└────────────────────┘
```

### 自适应模式选择

布局引擎**先预计算** body_h，不够就自动降级。这保证了任何样式组合（包括 relaxed 密度 + large icon）都能正确渲染：

```python
# 预计算 full 模式的 body 空间
_full_overhead = icon_sz + icon_margin + card_title_h + icon_margin
_full_body_h = usable_h - _full_overhead

if usable_h >= 140pt AND _full_body_h >= 38pt:
    → Full 模式（48pt 图标 + 30pt 标题 + body）

elif usable_h >= 80pt AND compact_body_h >= 38pt:
    → Compact 模式（32pt 图标 + 24pt 标题 + body）

else:
    → Minimal 模式（无图标，24pt 标题 + body 最大化）
```

以 relaxed 密度为例（card_pad=24pt, icon_margin=18pt, title_h=40pt）：
- Full 模式 body_h = 200 - 48 - 24 - 48 - 18 - 40 - 18 = 28pt < 38pt → 不够
- Compact 模式 body_h = 200 - 48 - 32 - 8 - 24 - 8 = 80pt >= 38pt → 选择 compact

## ResolvedShape 数据模型

layout resolver 的输出。所有值都是具体的 EMU 坐标和 hex 颜色，无引用：

```python
@dataclass
class ResolvedShape:
    shape_type: str           # textbox, rounded_rect, oval, image, line
    left: int; top: int       # EMU 坐标
    width: int; height: int   # EMU 尺寸
    text: str | None
    font: ResolvedFont | None
    fill_color: str | None    # hex
    corner_radius: int        # OOXML 0-100000
    shadow: ResolvedShadow | None
    alpha_pct: int            # 0-100
    z_order: int              # 层叠顺序
    auto_shrink: bool         # 文字自动缩小
    v_anchor: str             # t/ctr/b 垂直对齐
```

PptxWriter 读取这些字段直接生成形状，不做任何决策。

## Z-Order 层叠

每张 slide 的形状按 z_order 排列：

| 层 | z_order | 内容 |
|----|---------|------|
| 背景 | 0 | 全屏背景图 |
| 蒙版 | 1 | 半透明覆盖层 |
| 面板 | 5 | 内容区域半透明底板 |
| 装饰 | 8-10 | 下划线、分隔线、菱形等 |
| 标题 | 10 | 标题文本 |
| 卡片 | 20+ | 卡片容器 + 图标 + 文字（每张卡片 +5） |
| 素材 | 50 | 插图/图表 |
| 页脚 | 60 | 页脚文本 |

## Validator

渲染前的最后一道防线，检查 resolver 输出的合理性：

- **越界 clamp**: 形状超出画布边界时裁剪并警告
- **最小尺寸**: textbox 宽度不够放 6 个 CJK 字符时警告
- **Card body 高度**: body 区域小于 30pt 时警告（触发自适应降级的信号）
- **重叠检测**: 卡片容器之间不应重叠

validator 只警告不崩溃，保证始终能输出 PPTX。

## 扩展布局

添加新的 slide 类型：

1. 在 `layout_resolver.py` 定义 resolver 函数，接受 `(slide: SlideContent, style: ResolvedStyle)` 参数，返回 `list[ResolvedShape]`
2. 注册到 `_SLIDE_RESOLVERS` dict
3. 在 `prompts/content.py` 的约束表中添加类型
