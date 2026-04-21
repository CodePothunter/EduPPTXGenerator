"""Planning prompt assembly."""

from __future__ import annotations

from pathlib import Path


_REFS_DIR = Path(__file__).resolve().parent.parent / "design" / "references"


def _load_ref(name: str) -> str:
    path = _REFS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_planning_system_prompt() -> str:
    from edupptx.materials.icons import list_icons

    icon_list = ", ".join(list_icons())
    template = _SYSTEM_PROMPT_TEMPLATE.replace("{icon_list}", icon_list)
    notes_requirements = _load_ref("notes-guidelines.md")
    image_rules = _load_ref("planning-image-rules.md")

    parts = [template]
    if notes_requirements:
        parts.append(notes_requirements)
    if image_rules:
        parts.append(image_rules)
    return "\n\n".join(parts)


_SYSTEM_PROMPT_TEMPLATE = """你是一位资深的教育演示文稿策划师，负责根据主题、受众和补充资料，输出结构化的 PPT 规划 JSON。

## 输出目标
- 直接输出一个合法 JSON，不要附加解释文字
- 规划要兼顾教学逻辑、页面节奏、页面类型和素材需求
- 如果给了模板参考，优先遵守其中的 page_type、layout_hint、字数、图片槽位和节奏限制

## 页面类型
- `cover`: 封面页
- `toc`: 目录页
- `section`: 章节过渡页
- `content`: 常规知识讲解页
- `data`: 数据或统计页
- `case`: 案例页
- `closing`: 收束结束页
- `timeline`: 时间线页
- `comparison`: 对比页
- `exercise`: 练习页
- `summary`: 总结归纳页
- `relation`: 关系图页
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
- `mixed_grid`
- `full_image`
- `timeline`
- `comparison`
- `relation`

## material_needs 规则
- `images` 是有序数组，不是集合
- 一块独立图片区，对应一条 `images` 记录
- `aspect_ratio` 只能从以下集合选择：
  `1:1`, `3:4`, `4:3`, `16:9`, `9:16`, `3:2`, `2:3`, `21:9`
- 多图页面中，`images` 顺序默认与版面顺序一致：从左到右、从上到下
- 如果 `design_notes` 或模板参考明确要求多个图片区，`images` 数量必须匹配
- `icons` 只能从以下列表选择：{icon_list}

## 规划约束
- 页数范围通常为 5-25 页
- 必须包含 `cover` 和 `closing`
- `design_notes` 用一句话概括页面设计意图
- `notes` 写教师讲解话术
- `layout_hint` 应根据内容特征变化，避免所有页重复
- `toc` 应保持导航感，目录项要短，不要写成长段解释
- `relation` 页适合概念关系、分类关系、因果链，不要退化成普通列表页
- `timeline` 页中每个 `content_point` 对应一个独立时间节点

## reveal 页面规则
- 当 `quiz` / `exercise` 需要“先出题，后揭晓答案”时，必须规划为 2 张连续页面
- 揭晓页必须与源题页保持相同 `page_type`、`layout_hint`、`title`、`content_points`、`material_needs`
- 揭晓页只补答案层，不新增图片区、不改变原有布局
- 选择题/判断题使用 `reveal_mode="highlight_correct_option"`
- 填空题/简答题使用 `reveal_mode="show_answer"`

## 输出格式
严格输出如下 JSON 结构：

```json
{
  "meta": {
    "topic": "主题",
    "audience": "目标受众",
    "purpose": "教学目的",
    "style_direction": "自然语言风格方向",
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
        "background": "diagonal_gradient",
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


def build_planning_user_prompt(
    topic: str,
    requirements: str = "",
    source_text: str | None = None,
    research_summary: str | None = None,
    template_brief: str = "",
) -> str:
    parts = [f"请为以下主题策划一份教育演示文稿：\n\n**主题：** {topic}"]

    if requirements:
        parts.append(f"\n**附加要求：** {requirements}")

    if source_text:
        truncated = source_text[:8000] + ("..." if len(source_text) > 8000 else "")
        parts.append(f"\n**参考文档内容：**\n{truncated}")

    if research_summary:
        parts.append(f"\n**网络搜索资料：**\n{research_summary}")

    if template_brief:
        parts.append(
            "\n**已选模板参考（规划阶段优先遵守）：**\n"
            "以下内容来自已选模板的 metadata，可用于约束 page_type、layout_hint、标题长度、图片槽位和内容节奏：\n"
            f"```text\n{template_brief}\n```"
        )

    parts.append("\n请输出策划稿 JSON。")
    return "\n".join(parts)
