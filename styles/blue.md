---
schema_version: '0.1'
name: blue
colors:
  primary: '#1E293B'
  accent: '#2563EB'
  accent_light: '#DBEAFE'
  bg: '#EFF6FF'
  text: '#1E293B'
  text_secondary: '#475569'
  card_fill: '#FFFFFF'
  shadow: '#93C5FD'
  icon: '#3B82F6'
typography:
  title:
    fontFamily: Noto Sans SC
    fontSize: 38pt
    fontWeight: 700
  card-title:
    fontFamily: Noto Sans SC
    fontSize: 16pt
    fontWeight: 600
  body:
    fontFamily: Noto Sans SC
    fontSize: 12pt
spacing:
  margin: comfortable
  card_gap: normal
rounded:
  sm: 4px
  md: 8px
  lg: 16px
pptx-extensions:
  decorations:
    title_underline: true
    content_panel: true
    panel_alpha_pct: 35
    footer_separator: true
    quote_bar: true
    section_diamond: true
    closing_circle: true
  card_shadow:
    blur_pt: 30
    dist_pt: 8
    color: palette.shadow
    alpha_pct: 14
  background:
    type: diagonal_gradient
    seed_extra: ''
---

## Overview

科技蓝（blue）是一套面向科技、信息技术、物理与数学等理工科课堂的专业风格。整体调性克制、专注、可信赖：以暗石板蓝（primary `{colors.primary}`）作为正文与标题主色，以纯净的蓝色（accent `{colors.accent}`）做强调，避免任何让人分心的高饱和或暖色干扰。受众定位为中学生、大学生 STEM 课程，以及科技公司的内部分享。设计目标是让人在投影仪、教室白屏、夜晚直播课等多种放映环境下，眼睛都不容易疲劳，重点也始终一眼就能找到。

## Colors

调色板的逻辑是「一冷一深一空」：

- **primary `{colors.primary}` 暗石板蓝**——介于深蓝与中性灰之间，是阅读疲劳最低的深色，作为正文与标题文字的默认色比纯黑（`#000`）柔和、比纯灰更有方向感。
- **accent `{colors.accent}` 经典蓝**——WCAG AA 级别可在白色背景上承担正文链接、卡片标题、关键公式色。明度足够高，但不刺眼。
- **accent_light `{colors.accent_light}`**——浅蓝，用作高亮底色与公式背景，确保白色卡片上叠加色块时仍然轻盈。
- **bg `{colors.bg}`**——近白冷蓝，是 SVG 整页底色与卡片之间形成的微弱冷调对比。
- **text `{colors.text}` / text_secondary `{colors.text_secondary}`**——主文 / 次要描述的双层级，灰度差大致对应字号差，建立纵向阅读节奏。
- **card_fill `{colors.card_fill}` 纯白**——所有 Bento Grid 卡片的统一底色，在彩色背景上保证 4.5:1 以上对比度。
- **shadow `{colors.shadow}`**——阴影色取浅蓝而非纯黑，叠加 14% alpha 后产生「悬浮」而非「压扁」的观感。
- **icon `{colors.icon}`**——介于 accent 与 accent_light 之间的中度蓝，专门给 Lucide 图标使用，保证图标在卡片角落不会过分抢戏。

## Typography

字体策略遵循三条硬规则：

1. **正文 ≥ 12pt，卡片标题 ≥ 16pt**——这是教室投影最远座位（约 8m）仍可读的下限。低于此阈值会触发 `style_linter` 的 readability 警告。
2. **CJK 全部走 Noto Sans SC**——Noto Sans SC 在中英混排、数字字符宽度上比 Microsoft YaHei 更稳定，西文回退 Arial。
3. **不使用粗细超过 700 的字重**——投影仪因暗角损失会让 800/900 字重糊成一团，700 是清晰度与权重感的平衡点。

正文段落建议保持 1.5 倍行高，避免 1.0/1.2 让密度过高。Card-title 与正文的比值 16:12 ≈ 1.33，对应 Bento Grid 中卡片内信息层级的视觉跳跃量。

## Layout

画布固定 1280×720（16:9），所有卡片落在 `x∈[50, 1230], y∈[110, 660]` 区域，上方留 110px 给章节标题，下方留 60px 给页脚。`margin: comfortable` 与 `card_gap: normal` 在此风格中是黄金搭配——更紧的 `tight` 会让科技感变得拥挤、廉价；更松的 `spacious` 会浪费投影空间。

适配的 Bento Grid 布局排序（由强到弱）：

1. `center_hero`——单一概念课的封面 / 章节起始页。
2. `vertical_list`——定理/公理/定义的纵向并列。
3. `bento_2col`——「概念 + 例题」「公式 + 推导」二元对照。
4. `mixed_grid`——一节课多个知识点的总览。

不推荐 `hero_top` 与 `bento_3col` 在本风格上使用 ——前者会让暗色 primary 占据视觉重心；后者会把每张卡压到不到 360px 宽，正文 12pt 在投影上会变模糊。

## Elevation

阴影策略：**轻、低、冷**。

- **blur ≤ 30pt，dist ≤ 8pt**——任何超过此值的投射阴影在投影仪上都会糊成一片灰雾。
- **alpha 14%**——这是「能感觉到、但说不上来」的临界值，正是科技风需要的克制。
- **shadow 颜色 = `{colors.shadow}` 浅蓝**——而非黑色。教室投影本身已经压暗，再叠纯黑阴影会让卡片像被吸进背景里。

整页只有「卡片」这一个层级有阴影；标题、装饰条、页脚均不投影。

## Shapes

- **卡片圆角 8px（中等）**——足够柔化「冷感」但不至于像消费类产品那么俏皮。8000 EMU = 0.083 inch ≈ 8px @ 96 DPI。
- **图标尺寸 large（48pt）**——保持与 16pt card-title 大致 3:1 的视觉比，让图标在「左上角图标 + 标题」的组合里担任锚点。
- **装饰元素**：标题下划线、内容面板、页脚分隔线、引用条、章节菱形、闭幕圆圈——全部启用，但 panel_alpha 仅 35%，确保这些装饰是「画上去的」而不是「叠上去的」。

## Components

四个常用组件，颜色全部用 token 引用，确保跟随调色板自动生效：

- **card-knowledge**：知识点卡，背景 `{colors.card_fill}`，标题 `{colors.accent}`，正文 `{colors.text_secondary}`，左上图标 `{colors.icon}`，圆角 8px。
- **card-formula**：公式卡，背景 `{colors.accent_light}`，公式文本 `{colors.primary}`，formula_size_pt = 18，可叠 `{colors.accent}` 边框 1px。
- **card-quote**：引用卡，左侧 4px `{colors.accent}` 引用条，引文颜色 `{colors.text}`，作者署名 `{colors.text_secondary}`。
- **card-stat**：数据卡，巨大数字 `{colors.accent}`（≥ 36pt），标签 `{colors.text_secondary}`（≤ 14pt），用于体现规模、趋势、KPI。

## Do's and Don'ts

✅ 使用 `{colors.accent}` 标记重点公式、定义关键词、章节序号——一节课同色重复 ≤ 5 次，超过会失去强调意义。
✅ 正文段落保持 1.5 倍行高，段间距 ≥ 8pt，让远处的学生也能看清断点。
✅ 配图优先选用真实科学图、坐标图、实验照——AI 抽象渲染图与本风格的「严谨可信」气质相冲突。
❌ 不要在投影中堆叠超过 2 层阴影——shadow + glow + outer-stroke 会在弱光教室糊成一团。
❌ 不要把 accent 蓝用作背景大色块——`{colors.accent}` 在 50% 以上面积下会让人产生屏幕过亮的视觉疲劳。
❌ 不要混入暖色（橙/红/黄）作为「强调中的强调」——本风格的科技感来自冷色统一，破坏冷色调一致性比丢失视觉层级损失更大。
