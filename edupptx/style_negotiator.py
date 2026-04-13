"""Style negotiator: natural language style requirements -> StyleSchema patches.

Uses LLM to interpret style instructions like "简约商务风，配色偏冷色调" and
produce a JSON patch that modifies the base StyleSchema.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from edupptx.llm_client import LLMClient
from edupptx.style_schema import StyleSchema, load_style

_STYLE_PROMPT = """你是一位演示文稿视觉设计师。用户用自然语言描述了他们想要的演示风格。
你的任务是将这些风格要求转化为一个 JSON 补丁，用来修改基础样式模板。

## 基础样式模板的结构

```json
{
  "global": {
    "palette": {
      "primary": "#颜色",     // 主色调（标题文字等）
      "accent": "#颜色",      // 强调色（下划线、按钮、卡片标题）
      "accent_light": "#颜色", // 浅强调色（卡片背景、图标底色）
      "bg": "#颜色",          // 整体背景色
      "text": "#颜色",        // 主文字色
      "text_secondary": "#颜色", // 次要文字色
      "card_fill": "#颜色",   // 卡片填充色
      "shadow": "#颜色",      // 阴影色
      "icon": "#颜色"         // 图标色
    },
    "fonts": {
      "heading": {"family": "字体名", "fallback": "备选字体"},
      "body": {"family": "字体名", "fallback": "备选字体"}
    },
    "background": {
      "type": "diagonal_gradient|radial_gradient|geometric_circles|geometric_triangles"
    }
  },
  "semantic": {
    "title_size_pt": 38,      // 标题字号
    "subtitle_size_pt": 20,   // 副标题字号
    "body_size_pt": 12,       // 正文字号
    "card_title_size_pt": 16, // 卡片标题字号
    "card_corner_radius": 8000, // 卡片圆角（0=直角, 50000=半圆）
    "card_shadow": {
      "blur_pt": 30,          // 阴影模糊（0=无阴影, 50=浓阴影）
      "dist_pt": 8,           // 阴影偏移
      "alpha_pct": 14         // 阴影透明度
    }
  },
  "layout": {
    "margin": "comfortable|tight|spacious",      // 边距
    "card_spacing": "normal|tight|wide",         // 卡片间距
    "icon_size": "small|medium|large",           // 图标大小
    "content_density": "compact|standard|relaxed" // 内容密度
  },
  "decorations": {
    "title_underline": true,    // 标题下划线
    "content_panel": true,      // 内容区半透明面板
    "panel_alpha_pct": 35,      // 面板透明度(0-100)
    "footer_separator": true,   // 底部分隔线
    "quote_bar": true,          // 引用页竖条装饰
    "closing_circle": true      // 结束页圆形装饰
  }
}
```

## 你的任务

根据用户的风格描述，输出一个 JSON 补丁。只输出需要**修改**的字段，不需要输出没有变化的字段。

### 风格映射参考

- "简约/极简/clean" → 减少装饰(decorations多设false)，大边距(spacious)，低阴影
- "商务/专业/corporate" → 冷色调(蓝灰系)，紧凑布局(comfortable/tight)，方正圆角
- "活泼/年轻/playful" → 暖色调(橙粉系)，大图标(large)，圆润圆角(card_corner_radius高)
- "学术/正式/academic" → 深色文字，衬线字体，minimal装饰
- "暗色/dark" → 深色背景，浅色文字
- "紧凑" → tight margin + tight spacing + compact density
- "宽松" → spacious margin + wide spacing + relaxed density

### 输出格式

严格 JSON，不要 markdown 代码块。只输出需要修改的字段（深度合并到基础模板）：

```
{
  "global": {"palette": {"accent": "#新颜色"}},
  "layout": {"margin": "tight"},
  "decorations": {"title_underline": false}
}
```

如果用户没有提出任何风格要求（只是内容要求如"适合高中生"），输出空对象：{}
"""


def negotiate_style(
    llm: LLMClient,
    base_schema: StyleSchema,
    requirements: str,
) -> StyleSchema:
    """Use LLM to interpret natural language style requirements and patch the schema.

    Returns a new StyleSchema with the patches applied.
    If no style-related requirements are detected, returns the base schema unchanged.
    """
    if not requirements.strip():
        return base_schema

    messages = [
        {"role": "system", "content": _STYLE_PROMPT},
        {"role": "user", "content": f"用户的风格要求：\n{requirements}"},
    ]

    try:
        patch = llm.chat_json(messages, max_tokens=1024)
    except Exception as e:
        logger.warning("Style negotiation failed: {}, using base schema", e)
        return base_schema

    if not patch or patch == {}:
        logger.info("No style modifications requested")
        return base_schema

    # Deep merge patch into base schema
    base_dict = base_schema.model_dump(by_alias=True)
    _deep_merge(base_dict, patch)

    try:
        patched = StyleSchema.model_validate(base_dict)
        logger.info("Style negotiation: applied {} top-level patches", len(patch))
        return patched
    except Exception as e:
        logger.warning("Patched schema validation failed: {}, using base", e)
        return base_schema


def _deep_merge(base: dict, patch: dict) -> None:
    """Recursively merge patch into base dict (in-place)."""
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
