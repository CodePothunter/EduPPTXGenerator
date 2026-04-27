# 复用 · 通用结构库

## 适用范围
- 面向所有年级、所有学科。
- 主要提供目录页、章节页、关系页、练习页、总结页和通用内容版式。
- 关键词：中性、稳定、清晰、可复用、跨学科。

## 全局视觉来源
- 配色、背景生成 prompt 与背景色彩偏向统一由 `edupptx/design/references` 控制。
- 本目录只负责通用布局骨架、页型约束和跨学科可复用结构。
- 运行时会被主家族自动合并，不单独承担学科或年级风格判断。

## 通用视觉语言
- 优先保证布局稳定和信息层级清楚，不带过强的年级或学科特征。
- 默认使用中性卡片结构，便于主家族叠加自身风格提示。
- 不机械复制示例卡数和图片区数量，按 variant 的区间理解。

## 装饰语言
- 只允许轻量中性装饰，如圆点、短线、柔和几何角标。
- 装饰优先服务层级和节奏，不带强烈低龄化或高冷编辑感。

## 当前模板文件
- `toc_1.svg`
- `toc_2.svg`
- `section.svg`
- `content_mixed_grid.svg`
- `content_mixed_grid_2.svg`
- `content_bento_2col_equal.svg`
- `content_bento_2col_asymmetric.svg`
- `content_bento_3col.svg`
- `content_cards_top_hero_bottom.svg`
- `content_hero_top_cards_bottom.svg`
- `content_hero_with_microcards_2.svg`
- `content_relation_1.svg`
- `exercise.svg`
- `quiz.svg`
- `summary.svg`
- `closing.svg`
