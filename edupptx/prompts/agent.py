"""Enriched system prompt for the thin-agent content planner."""

from edupptx.prompts.content import SYSTEM_PROMPT as BASE_SYSTEM_PROMPT

DIAGRAM_TYPES_REFERENCE = """
## 可用图表类型

当幻灯片内容适合用图表表达时，在 content_materials 中指定图表生成指令。

| 类型 | 用途 | data 格式 |
|------|------|----------|
| flowchart | 流程/步骤 | {"nodes": [{"id": "1", "label": "步骤1"}], "edges": [{"from": "1", "to": "2"}], "direction": "TB"} |
| timeline | 时间线/历史 | {"events": [{"year": "2020", "label": "事件A", "description": "描述"}]} |
| comparison | 对比/优劣 | {"columns": [{"header": "优点", "items": ["快速", "简单"]}]} |
| hierarchy | 层级/分类 | {"root": {"label": "根", "children": [{"label": "子节点", "children": []}]}} |
| cycle | 循环/流转 | {"steps": [{"label": "步骤1", "description": "描述"}]} |
"""

MATERIAL_INSTRUCTIONS = """
## 素材决策指南

每个 slide 可以包含：
1. **bg_action** — 背景图决策：
   - `{{"action": "generate", "style": "diagonal_gradient|radial_gradient|geometric_circles|geometric_triangles", "tags": ["主题标签"]}}`
   - `{{"action": "reuse", "material_id": "mat_xxxx"}}` — 复用素材库中已有的素材

2. **content_materials** — 内容素材（图表/插图）：
   - 图表生成：`{{"action": "generate_diagram", "position": "center|full|left|right", "diagram_type": "flowchart|timeline|comparison|hierarchy|cycle", "diagram_data": {{...}}, "tags": [...]}}`
   - AI插图生成：`{{"action": "generate_illustration", "position": "center|full|left|right", "illustration_description": "描述", "illustration_style": "flat|realistic|sketch|watercolor", "tags": [...]}}`
   - 复用素材：`{{"action": "reuse", "position": "center", "material_id": "mat_xxxx"}}`

### position 说明
- `"full"`: 素材占满内容区域，替换卡片（此时 cards 应为空）
- `"left"` / `"right"`: 素材占一半，卡片占另一半
- `"center"`: 素材在标题和卡片之间

### 何时使用素材
- **流程/步骤类内容** → flowchart
- **历史/时间线** → timeline
- **对比/优劣** → comparison
- **分类/组织结构** → hierarchy
- **循环过程** → cycle
- **抽象概念需要可视化** → AI 插图
- **背景图** → 每页都需要，优先复用库中已有的

### 素材库当前状态
{library_summary}
"""


def build_agent_system_prompt(library_summary: str) -> str:
    """Build the enriched system prompt with library context."""
    return (
        BASE_SYSTEM_PROMPT
        + "\n\n"
        + MATERIAL_INSTRUCTIONS.format(library_summary=library_summary)
        + "\n\n"
        + DIAGRAM_TYPES_REFERENCE
    )


def build_agent_user_message(topic: str, requirements: str = "") -> str:
    """Build the user message for the enriched planning call."""
    parts = [f"请为以下教学主题设计完整的演示文稿方案：\n\n主题：{topic}"]
    if requirements:
        parts.append(f"\n附加要求：{requirements}")
    parts.append("\n请在每个 slide 中包含 bg_action 和 content_materials 决策。")
    return "\n".join(parts)
