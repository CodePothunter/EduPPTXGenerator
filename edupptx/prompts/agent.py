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
## 素材决策

每个 slide 必须包含 bg_action，可选包含 content_materials。

**bg_action**（必填）: {{"action":"generate","style":"diagonal_gradient","tags":["标签"]}}
style 可选: diagonal_gradient, radial_gradient, geometric_circles, geometric_triangles

**content_materials**（可选，仅在内容确实适合图表时添加，大多数页面不需要）:
- 图表: {{"action":"generate_diagram","position":"center","diagram_type":"flowchart","diagram_data":{{...}},"tags":[]}}
- diagram_type: flowchart / timeline / comparison / hierarchy / cycle

**重要：输出精简**
- 值为 null 的字段直接省略，不要输出
- content_materials 为空时省略该字段，不要输出空数组
- bg_action 的 tags 只写1-2个关键词
- 大多数页面只需要 bg_action，不需要 content_materials

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
    parts.append("\n每个 slide 必须包含 bg_action。仅在内容适合图表展示时才添加 content_materials，省略值为 null 的字段。")
    return "\n".join(parts)
