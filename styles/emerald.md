---
schema_version: '0.1'
name: emerald
colors:
  primary: '#1F2937'
  accent: '#059669'
  accent_light: '#D1FAE5'
  bg: '#F0FDF4'
  text: '#1F2937'
  text_secondary: '#4B5563'
  card_fill: '#FFFFFF'
  shadow: '#6EE7B7'
  icon: '#059669'
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
  semantic:
    subtitle_size_pt: 20
    footer_size_pt: 13
    formula_size_pt: 18
    card_corner_radius: 8000
    bg_overlay_alpha: 0.55
---

## Overview

翠绿（emerald）是一套面向自然科学、生命科学、生态、健康、成长主题的友好风格。整体调性温润、清新、可亲近：以中性深灰（primary `{colors.primary}`）作为正文颜色——故意不用纯黑，避免课堂语境下的「考试感」——以饱和度恰到好处的翠绿（accent `{colors.accent}`）做强调，让生命力与专注感共存。受众定位为高中生 / 大学生的生物、地理、环境、化学课程，以及科普讲座、社团活动课件。设计目标是让学生在长达 40 分钟的连续观看中，眼睛持续放松，又能在重点处被自然引导。

## Colors

调色板的逻辑是「绿色家族 + 中性骨架」：

- **primary `{colors.primary}` 暖灰深色**——比纯黑温柔，作为正文与标题的默认色，配合任何浅底都不会形成生硬的对比。
- **accent `{colors.accent}` 翡翠绿**——本风格的灵魂色，明度低于常见的「自然绿」，避免泛荧光感；在白底上对比度满足 WCAG AA。
- **accent_light `{colors.accent_light}` 薄荷绿**——浅薄荷调，专门做高亮底色与公式背景，暗示「自然里的留白」。
- **bg `{colors.bg}` 极浅绿白**——SVG 整页底色，营造森林晨雾般的氛围。
- **text `{colors.text}` / text_secondary `{colors.text_secondary}`**——主文 / 次要描述的双层级，灰度差对应字号差。
- **card_fill `{colors.card_fill}` 纯白**——所有 Bento Grid 卡片的统一底色，确保上彩底时白色卡片像「叶面上的水珠」一样轻盈。
- **shadow `{colors.shadow}` 嫩绿**——阴影色取嫩绿而非纯黑，叠加 14% alpha 后呈现「植物投影」的自然柔和感。
- **icon `{colors.icon}`**——与 accent 同色，确保 Lucide 图标作为符号系统时颜色一致，不抢主色风头。

## Typography

字体策略遵循三条硬规则：

1. **正文 ≥ 12pt，卡片标题 ≥ 16pt**——教室投影最远座位（约 8m）的可读下限。低于此阈值会触发 `style_linter` 的 readability 警告。
2. **CJK 全部走 Noto Sans SC**——在生物 / 化学命名（含拉丁学名、化学式、上下标）混排时，Noto Sans SC 的字宽与基线最稳定，西文回退 Arial。
3. **避免使用细体**——生命科学课件常含较小的图注，<300 字重在投影远端会模糊到无法辨认。300 是下限，400/500 更安全。

正文段落保持 1.5 倍行高；卡片内多行说明保持 1.6 倍行高，模拟课本「图注式」阅读节奏。

## Layout

画布固定 1280×720（16:9），所有卡片落在 `x∈[50, 1230], y∈[110, 660]` 区域。`margin: comfortable` 与 `card_gap: normal` 配合自然主题最佳——更紧的 `tight` 会让画面失去呼吸感，违背本风格的氛围；更松的 `spacious` 在生物课件常见的「图 + 标签 + 解释」三段式中会留太多虚空。

适配的 Bento Grid 布局排序（由强到弱）：

1. `vertical_list`——分类、分级、生命周期、食物链等线性结构。
2. `bento_2col`——「现象 + 解释」「结构 + 功能」「实验组 + 对照组」对比卡。
3. `center_hero`——课程封面、章节起点、单一核心概念页。
4. `mixed_grid`——一节课多知识点总览（如「光合作用四要素」）。

慎用 `hero_top`——大块翠绿覆盖会让画面变得卡通；如必须使用，请把 hero 区高度限制在 ≤ 180px。

## Elevation

阴影策略：**柔、薄、绿**。

- **blur ≤ 30pt，dist ≤ 8pt**——任何超过此值的投射阴影都会让卡片显得「重」，与本风格希望的轻盈、自然冲突。
- **alpha 14%**——介于「察觉得到」与「察觉不到」之间，给卡片浮起感而不形成视觉负担。
- **shadow 颜色 = `{colors.shadow}` 嫩绿**——而非黑或灰。这是本风格最重要的细节：植物在阳光下的投影本身就是带绿调的，用嫩绿阴影会让整页色彩家族保持统一。

整页只有「卡片」这一个层级有阴影；标题、装饰条、页脚均不投影。

## Shapes

- **卡片圆角 8px（中等）**——比 4px 更柔和，比 16px 更克制；介于「学术」与「儿童」之间，适合本风格的「亲和但不幼稚」定位。8000 EMU = 0.083 inch ≈ 8px @ 96 DPI。
- **图标尺寸 large（48pt）**——与 16pt card-title 形成约 3:1 的视觉比，图标承担「这张卡讲什么」的快速识别。
- **装饰元素**：标题下划线、内容面板、页脚分隔线、引用条、章节菱形、闭幕圆圈——全部启用，panel_alpha = 35% 让面板既存在感足够，又不抢主体。

## Components

四个常用组件，颜色全部用 token 引用，确保切换调色板时自动生效：

- **card-knowledge**：知识点卡，背景 `{colors.card_fill}`，标题 `{colors.accent}`，正文 `{colors.text_secondary}`，左上图标 `{colors.icon}`，圆角 8px。
- **card-formula**：公式 / 化学方程式卡，背景 `{colors.accent_light}`，公式文本 `{colors.primary}`，formula_size_pt = 18；可加 `{colors.accent}` 边框 1px 作为「实验框」的视觉提示。
- **card-quote**：引用卡（科学家名言、教材摘录、经典定义），左侧 4px `{colors.accent}` 引用条，引文颜色 `{colors.text}`，作者署名 `{colors.text_secondary}`。
- **card-stat**：数据卡（如「光合作用每年固定 X 吨碳」），巨大数字 `{colors.accent}`（≥ 36pt），标签 `{colors.text_secondary}`（≤ 14pt）。

## Do's and Don'ts

✅ 使用 `{colors.accent}` 标记关键术语、生命周期阶段、实验对照标签——一节课同色重复 ≤ 5 次，超过会让重点失去权重。
✅ 配图优先选真实生态摄影、显微图、解剖图——本风格的「自然」气质来自真实图片的纹理与光线，不要 AI 生成的过度饱和插画。
✅ 在引用名人语录时使用 card-quote，让引文成为「本节课的精神锚点」而不是装饰。
❌ 不要把 accent 翠绿用作大块背景——`{colors.accent}` 在大于 40% 面积时会变得 saturated，与本风格的「清新」相反。
❌ 不要混入红色作强调——红绿对在生物色弱学生（约占 5%）眼中是不可分辨的；如需第二强调色，请用 primary 深灰，靠字重 + 字号建立层级。
❌ 不要在同一张卡片内堆叠 ≥ 3 个图标——会让画面像信息图而非教学卡，降低焦点。
