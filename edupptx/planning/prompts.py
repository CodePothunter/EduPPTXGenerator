"""Planning prompt assembly."""

from __future__ import annotations

from pathlib import Path

from edupptx.planning.exercise_policy_prompt import (
    build_exercise_policy_prompt,
    build_exercise_refinement_prompt,
)


_REFS_DIR = Path(__file__).resolve().parent.parent / "design" / "references"


def _load_ref(name: str) -> str:
    path = _REFS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_outline_planning_system_prompt(
    *,
    exercise_policy_enabled: bool = False,
    exercise_candidates_text: str = "",
) -> str:
    parts = [_OUTLINE_SYSTEM_PROMPT]
    if exercise_policy_enabled:
        parts.append(build_exercise_policy_prompt(exercise_candidates_text))
    return "\n\n".join(parts)


def build_outline_planning_user_prompt(
    topic: str,
    requirements: str = "",
    source_text: str | None = None,
    research_summary: str | None = None,
) -> str:
    parts = [f"请先完成第 1 阶段内容策划。\n\n**主题：** {topic}"]

    if requirements:
        parts.append(f"\n**附加要求：** {requirements}")

    if source_text:
        truncated = source_text[:8000] + ("..." if len(source_text) > 8000 else "")
        parts.append(f"\n**参考文档内容：**\n{truncated}")

    if research_summary:
        parts.append(f"\n**网络搜索资料：**\n{research_summary}")

    parts.append("\n请输出第 1 阶段策划 JSON。")
    return "\n".join(parts)


def build_refinement_planning_system_prompt(
    *,
    exercise_policy_enabled: bool = False,
) -> str:
    from edupptx.materials.icons import list_icons

    icon_list = ", ".join(list_icons())
    template = _REFINEMENT_SYSTEM_PROMPT.replace("{icon_list}", icon_list)
    notes_requirements = _load_ref("notes-guidelines.md")
    image_rules = _load_ref("planning-image-rules.md")
    image_prompt_routing = _load_ref("image-prompt-routing.md")

    parts = [template]
    if notes_requirements:
        parts.append(notes_requirements)
    if image_rules:
        parts.append(image_rules)
    if image_prompt_routing:
        parts.append(image_prompt_routing)
    if exercise_policy_enabled:
        parts.append(build_exercise_refinement_prompt())
    return "\n\n".join(parts)


def build_refinement_planning_user_prompt(
    outline_json: str,
    template_refinement_brief: str,
    template_family: str,
) -> str:
    parts = [
        "请完成第 2 阶段模板细化规划。",
        "",
        "**阶段 1 初稿：**",
        f"```json\n{outline_json}\n```",
    ]

    if template_refinement_brief.strip():
        parts.extend([
            "",
            f"**已命中的模板家族：** {template_family}",
            "",
            "**逐页模板细化参考：**",
            f"```text\n{template_refinement_brief}\n```",
        ])

    parts.append("\n请输出第 2 阶段完整规划 JSON。")
    return "\n".join(parts)


_OUTLINE_SYSTEM_PROMPT = """你是一位资深的教育演示文稿策划师，当前只负责第 1 阶段：内容初稿规划。

## 阶段目标
- 先确定整套 PPT 的教学结构、页数和节奏
- 先决定每页的 `page_type`、`title`、`subtitle`、`content_points`、`layout_hint`
- 这一阶段不要引入任何模板、SVG、metadata、配色、图片槽位、卡片数量等参考
- 这一阶段不要为模板去反推内容结构

## 页面类型
- `cover`: 封面页
- `toc`: 目录页
- `section`: 章节过渡页
- `content`: 常规知识讲解页
- `data`: 数据或统计页
- `case`: 案例页
- `closing`: 收束结束页
- `timeline`: 时间线页
- `exercise`: 练习页
- `summary`: 总结归纳页
- `quiz`: 测验页
- `formula`: 公式推导页
- `experiment`: 实验步骤页

## layout_hint 选择范围
- `center_hero`
- `vertical_list`
- `bento_2col_equal`
- `bento_2col_asymmetric`
- `bento_3col`
- `hero_top_cards_bottom`
- `cards_top_hero_bottom`
- `hero_with_microcards`
- `mixed_grid`
- `full_image`
- `timeline`
- `comparison`
- `relation`

## 阶段 1 约束
- 页数范围通常为 5-25 页
- 必须包含 `cover` 和 `closing`
- `layout_hint` 应根据内容特征变化，避免所有页重复
- `comparison` 和 `relation` 只能作为 `layout_hint` 使用；对比页/关系图页的 `page_type` 使用 `content`
- `toc` 只写短目录，不要写成长段解释
- `timeline` 页的 `content_points` 应天然适合时间节点
- `layout_hint=relation` 页的 `content_points` 应天然适合关系图
- `hero_with_microcards` 适合“单一主卡统领卡内分区”的页面，可用于并列短信息模块、连续文本展示或讲解总结结构；如果选择它，应让 `design_notes` 明确体现属于哪一类
- 不要在这一阶段生成 reveal 页，也不要为了伪动画提前复制题目页
- `material_needs`、`design_notes`、`notes` 在这一阶段可以留空或省略

## 学科与年级字段（deck 级，整套只判定一次）
- `subject` 必须只从以下枚举中选择：语文、数学、物理、其他
- `grade` 必须只从以下枚举中选择：一年级、二年级、三年级、四年级、五年级、六年级、七年级、八年级、九年级、高一、高二、高三、其他
- `grade_band` 必须只从以下枚举中选择：低年级、高年级、其他
- 依据 topic、audience、用户要求自行判断并归一；不确定或无法判断时一律输出"其他"

## 输出格式
直接输出合法 JSON，不要附加解释文字。结构如下：

```json
{
  "meta": {
    "topic": "主题",
    "audience": "目标受众",
    "purpose": "教学目的",
    "style_direction": "自然语言风格方向",
    "subject": "语文",
    "grade": "三年级",
    "grade_band": "低年级",
    "total_pages": 10
  },
  "research_context": "搜索资料摘要",
  "pages": [
    {
      "page_number": 1,
      "page_type": "cover",
      "title": "页面标题",
      "subtitle": "副标题",
      "content_points": [],
      "layout_hint": "center_hero"
    }
  ]
}
```"""


_REFINEMENT_SYSTEM_PROMPT = """你是一位资深的教育演示文稿策划师，当前负责第 2 阶段：模板细化规划。

## 阶段目标
- 输入是第 1 阶段内容初稿，输出是完整规划 JSON
- 保留阶段 1 的页数、顺序和主线节奏
- 允许对单页 `content_points`、`layout_hint` 做小幅优化，但不要推翻整套结构
- 本阶段补全：`material_needs`、`design_notes`、`notes`

## 模板使用规则
- 只参考当前页命中的单个模板变体，不要同时融合多个模板
- 模板只是页面特征参考，不是刚性蓝图
- 不要机械照抄模板示例中的 card 数量、图片数量、位置或尺寸
- 如果模板参考写的是 `card_range` / `subcard_range` / `image_range`，把它理解成建议区间，而不是硬性数量
- `card_range` 只指顶层/外层大 card 数量；内部 microcard/subcard、关系图节点、表格行列都不计入
- `subcard_range` 指大 card 内部的 microcard/subcard、关系图节点、比较项、表格行列等内部结构数量
- 即使参考了模板，最终页面仍然应服从当前页的教学内容，而不是反过来让内容硬塞进模板
- 如果某页没有命中模板，就直接按通用教学逻辑完成该页规划

## 图片与图标规则
- `images` 是有序数组，不是集合
- 一块独立图片区，对应一条 `images` 记录
- `material_needs.images[].role` 只能填写：`hero`、`illustration`、`icon`、`background`
- `aspect_ratio` 只能从以下集合选择：
  `1:1`, `3:4`, `4:3`, `16:9`, `9:16`
- 多图页面中，`images` 顺序默认与版面顺序一致：从左到右、从上到下
- `icons` 只能从以下列表选择：{icon_list}

## 页面一致性自检
- 每页先确定真实教学目标，再同步生成 `content_points`、`material_needs.images`、`design_notes` 和 `notes`
- 大段文字、公式、算式、具体汉字（生字）、词语、拼音、句子、题干、选项、答案，以及田字格、米字格、拼音四线格、表格、线格、坐标网格等，必须写入 `content_points`，由 SVG 生成文字/线条，不得规划为 `material_needs.images`
- `material_needs.images` 只用于场景、人物、动物、物体、情境、装饰等插画
- `material_needs.images[].query` 必须服务当前页内容，不能引入当前页没有支撑的新知识、新对象、新标签或新文本
- query 中不要使用“本课、这些、若干、多个、重点、正确、对应、提示”等泛指词代替具体画面对象；应写清具体的画面对象、场景和关系
- `notes` 可以包含导入、提问、讲解、分析、拓展和课堂互动；但凡是“看图中、图上、图片里有、从图中可以看到”等视觉指认，必须只描述 query 和 `design_notes` 已规划的可见内容

## 页面规划要求
- `design_notes` 用一句话概括页面设计意图，说明页面如何承载内容
- `notes` 写教师讲解话术，必须贴合本页真实内容
- `material_needs.images` 的数量应服务于教学表达，不要为了凑模板示例而强行增减
- `cover` / `closing` / `section` 等节奏页应保持简洁，不要过度堆料

## reveal / 伪动画规则
- 不要手动创建 reveal 页，不要复制题目页
- 如果某个 `quiz` / `exercise` 页面需要“先出题，后揭晓答案”的伪动画效果，请在源题页上显式设置 `reveal_mode`
- 选择题 / 判断题使用 `reveal_mode="highlight_correct_option"`
- 填空题 / 简答题使用 `reveal_mode="show_answer"`
- `reveal_from_page` 在源题页保持为 null，系统会在规划后自动复制出揭晓页
- 题目页的 `design_notes` 和 `notes` 应体现“先作答、稍后揭晓”的课堂节奏

## 学科与年级字段（deck 级，整套只判定一次）
- `subject` 必须只从以下枚举中选择：语文、数学、物理、其他
- `grade` 必须只从以下枚举中选择：一年级、二年级、三年级、四年级、五年级、六年级、七年级、八年级、九年级、高一、高二、高三、其他
- `grade_band` 必须只从以下枚举中选择：低年级、高年级、其他
- 依据 topic、audience、用户要求自行判断并归一；不确定或无法判断时一律输出"其他"

## 输出格式
直接输出合法 JSON，不要附加解释文字。输出完整结构：

```json
{
  "meta": {
    "topic": "主题",
    "audience": "目标受众",
    "purpose": "教学目的",
    "style_direction": "自然语言风格方向",
    "subject": "语文",
    "grade": "三年级",
    "grade_band": "低年级",
    "total_pages": 10
  },
  "research_context": "搜索资料摘要",
  "pages": [
    {
      "page_number": 1,
      "page_type": "cover",
      "title": "页面标题",
      "subtitle": "副标题",
      "content_points": [],
      "layout_hint": "center_hero",
      "material_needs": {
        "background": null,
        "images": [],
        "icons": [],
        "chart": null
      },
      "design_notes": "一句话说明页面设计意图",
      "reveal_from_page": null,
      "reveal_mode": null,
      "notes": "讲解备注"
    }
  ]
}
```"""
