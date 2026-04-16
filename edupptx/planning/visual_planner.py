"""Phase 1b: LLM-driven visual planning — theme colors, background style."""

from __future__ import annotations

import json
import re

from loguru import logger

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.models import PlanningDraft, VisualPlan

_SYSTEM_PROMPT = """\
你是一位教育演示文稿的视觉设计顾问。根据 PPT 的主题和内容结构，推荐一套统一的视觉方案。

## 输出要求

输出一个 JSON 对象（用 ```json 包裹），包含以下字段：

```json
{
  "primary_color": "#hex — 主色，用于标题栏装饰条、重要元素",
  "secondary_color": "#hex — 辅色，用于次级标题、图标填充",
  "accent_color": "#hex — 强调色，仅用于关键数据（全局≤3处）",
  "background_prompt": "英文，用于 AI 生图的背景描述，抽象纹理/渐变，16:9，淡色调",
  "card_bg_color": "#hex — 卡片背景色",
  "secondary_bg_color": "#hex — 次背景色，用于区域分隔、交替行、引用区块",
  "text_color": "#hex — 正文文字颜色",
  "heading_color": "#hex — 标题文字颜色",
  "content_density": "lecture 或 review"
}
```

## 配色原则

1. **教育场景优先**：颜色清晰易读，整体克制，不使用高刺激配色
2. **主色决定气质**：理科偏蓝绿，文科偏暖色，综合偏灰蓝
3. **文字对比优先**：text_color 与 card_bg_color 的对比度 ≥ 4.5:1，保证正文可读
4. **卡片贴近背景**：card_bg_color 应接近页面背景主色，可比背景略亮或略暗，不再默认纯白
5. **卡片仍需可辨识**：当 card_bg_color 与背景接近时，必须通过轻描边、弱阴影、圆角边界或明度差保持卡片轮廓清晰
6. **背景要淡且稳定**：background_prompt 生成的图应为低对比、淡色、低纹理干扰的抽象背景，不抢内容焦点
7. **次背景色层级**：secondary_bg_color 相对 card_bg_color 再轻微偏移，用于区域分隔、引用区、交替行
8. **强调色慎用**：accent_color 只用于关键数据、标签、重点提示，与主色明显区分
9. **色彩比例**：页面以背景系和卡片系中性色为主，主色和强调色只承担结构与提示，不大面积铺满

卡片背景不必为纯白，应优先选择与页面背景接近的浅色系颜色；但卡片边界必须仍然清晰可见。

## 内容密度判断

根据用户需求和主题特点选择：
- **lecture**（课堂讲授）：大字、宽松留白、适合投影，正文 24px 基准
- **review**（复习归纳）：信息密集、小字紧凑、适合打印/平板，正文 18px 基准
- 如果用户提到"课件""课堂""讲课""教学" → lecture
- 如果用户提到"复习""总结""归纳""打印""知识点" → review
- 默认 → lecture

## background_prompt 示例

- "Subtle abstract geometric pattern, soft blue gradient, minimalist, light background, 16:9 aspect ratio"
- "Elegant soft green watercolor texture, gentle flowing shapes, light and airy, presentation background"
- "Clean minimal tech grid pattern, very light gray and blue, professional, 1920x1080"
"""


def generate_visual_plan(draft: PlanningDraft, config: Config) -> VisualPlan:
    """Call LLM to generate a visual plan based on content planning."""
    client = create_llm_client(config)

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
