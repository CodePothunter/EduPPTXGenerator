"""Phase 1b: LLM-driven visual planning — theme colors, background style."""

from __future__ import annotations

import json
import re

from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import LLMClient
from edupptx.models import PlanningDraft, VisualPlan

_SYSTEM_PROMPT = """\
你是一位教育演示文稿的视觉设计顾问。根据 PPT 的主题和内容结构，推荐一套统一的视觉方案。

## 输出要求

输出一个 JSON 对象（用 ```json 包裹），包含以下字段：

```json
{
  "primary_color": "#hex",
  "secondary_color": "#hex",
  "accent_color": "#hex",
  "background_prompt": "用于 AI 生图的背景描述（英文），抽象纹理/渐变，16:9，淡色调，适合做 PPT 底图",
  "card_bg_color": "#hex",
  "text_color": "#hex",
  "heading_color": "#hex"
}
```

## 配色原则

1. **教育场景优先**：颜色清晰易读，不要花哨
2. **主色决定气质**：理科偏蓝绿，文科偏暖色，综合偏灰蓝
3. **对比度充足**：text_color 和 card_bg_color 的对比度 ≥ 4.5:1
4. **背景要淡**：background_prompt 生成的图应是淡色抽象纹理，不抢内容焦点
5. **强调色慎用**：accent_color 只用于关键数据/按钮，与主色有明显区分

## background_prompt 示例

- "Subtle abstract geometric pattern, soft blue gradient, minimalist, light background, 16:9 aspect ratio"
- "Elegant soft green watercolor texture, gentle flowing shapes, light and airy, presentation background"
- "Clean minimal tech grid pattern, very light gray and blue, professional, 1920x1080"
"""


def generate_visual_plan(draft: PlanningDraft, config: Config) -> VisualPlan:
    """Call LLM to generate a visual plan based on content planning."""
    client = LLMClient(config)

    page_types = [f"{p.page_number}. {p.page_type}: {p.title}" for p in draft.pages]
    user_prompt = (
        f"## PPT 信息\n"
        f"- 主题：{draft.meta.topic}\n"
        f"- 受众：{draft.meta.audience or '通用'}\n"
        f"- 目的：{draft.meta.purpose or '教学演示'}\n"
        f"- 风格方向：{draft.meta.style_direction or '专业教育'}\n"
        f"- 页数：{len(draft.pages)}\n\n"
        f"## 页面结构\n" + "\n".join(page_types) + "\n\n"
        f"请根据以上信息，推荐一套视觉方案。"
    )

    try:
        response = client.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=1024,
        )
        return _parse_visual_plan(response)
    except Exception as e:
        logger.warning("Visual planning failed, using defaults: {}", e)
        return VisualPlan()


def _parse_visual_plan(response: str) -> VisualPlan:
    """Extract VisualPlan JSON from LLM response."""
    # Try ```json fence
    m = re.search(r"```json\s*\n(.*?)```", response, re.DOTALL)
    if m:
        text = m.group(1).strip()
    else:
        # Try bare JSON object
        m = re.search(r"\{[^{}]*\}", response, re.DOTALL)
        text = m.group(0) if m else "{}"

    try:
        data = json.loads(text)
        return VisualPlan.model_validate(data)
    except Exception as e:
        logger.warning("Failed to parse visual plan JSON: {}", e)
        return VisualPlan()
