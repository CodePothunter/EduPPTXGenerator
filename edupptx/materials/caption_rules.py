"""Single source of truth for the caption summarization rule + summarizer.

Imported by both the plan side (query -> caption, via the agent reuse flow) and
the library side (vlm_asset_enricher: image -> caption). Keep the rule text here
as the ONLY place it lives so the two sides never drift.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

CAPTION_RULE = """用中文短语或短句，概括图片“可复用的主体内容”，只保留复用时起作用的信息：
- 画面主体（人/物/动植物/角色）必留；
- 核心是动作/事件/关系 → 连动作/事件一起留；
- 整体是场景/环境/容器/版式 → 留场景或容器类型；
- 是某种图示（示意图/图表/流程图/光路图等）→ 留图示类型 + 主题。
删除：颜色、服饰、发型、数量、对话/标注文字、氛围、表情、构图等装饰修饰。
风格词（如“卡通”）仅当是主体身份的一部分（虚拟/拟人形象）时保留。
形式：中文短语或短句，尽量精简；不写“图片/插画/这张图”等元词（图示类型词除外）。

示例（演示规则，不是逐例补丁）：
- 雾的形成和消失的科普示意图，左边低温凝雾、右边升温雾散 → 雾的形成和消失的科普示意图
- 七巧板拼成的正方形、思考的小女孩和拼三角形的提示语 → 小女孩思考怎么把七巧板拼成三角形
- 7个小朋友做游戏，配对话气泡“请你抬起一条腿” → 小朋友做游戏
- 卡通风格的淘气雾孩子形象，白蒙蒙圆滚滚雾团，带调皮笑脸 → 卡通雾孩子形象
- 扎双丸子头的卡通小女孩和红苹果 → 小女孩和苹果
- 雾中的城市，房屋街道行人朦胧，小黑猫若隐若现 → 雾中的城市街景"""


def build_caption_system_prompt() -> str:
    """System prompt for the query->caption batch summarizer."""
    return (
        "你是中文教育课件图片的语义摘要器。\n\n"
        + CAPTION_RULE
        + "\n\n我会给你一个 JSON 数组，每个元素含 query 字段。"
        "只输出 JSON 数组，长度、顺序与输入一致；每个元素保留 query，"
        "并新增或覆盖 caption 字段。不要输出解释、Markdown 或多余文本。"
    )


def _strip_json_fences(text: str) -> str:
    text = str(text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_caption_array(raw: str) -> list[dict[str, Any]]:
    text = _strip_json_fences(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        import json_repair

        payload = json_repair.loads(text)
    if not isinstance(payload, list):
        raise ValueError("caption summarizer response must be a JSON array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"caption response item #{index} is not an object")
        result.append(item)
    return result


def summarize_records(
    records: list[dict[str, Any]],
    client: Any,
    *,
    query_field: str = "query",
    caption_field: str = "caption",
    batch_size: int = 50,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    """Summarize each record's query text into a caption via CAPTION_RULE.

    Returns new records (originals preserved) with ``caption_field`` set.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    items = list(records)
    output: list[dict[str, Any]] = []
    total = len(items)
    for start in range(0, total, batch_size):
        batch = items[start : start + batch_size]
        minimal = [{"query": str(r.get(query_field, "")).strip()} for r in batch]
        messages = [
            {"role": "system", "content": build_caption_system_prompt()},
            {
                "role": "user",
                "content": "现在请处理下面的 JSON 数组：\n"
                + json.dumps(minimal, ensure_ascii=False, indent=2),
            },
        ]
        max_tokens = max(2048, min(12000, 220 * len(batch) + 1600))
        raw = client.chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
        parsed = _parse_caption_array(raw)
        if len(parsed) != len(batch):
            raise ValueError(f"expected {len(batch)} caption items, got {len(parsed)}")
        for original, generated in zip(batch, parsed):
            caption = str(generated.get("caption") or "").strip()
            if not caption:
                raise ValueError("caption summarizer returned an empty caption")
            item = deepcopy(original)
            item[caption_field] = caption
            output.append(item)
        if sleep_seconds > 0 and start + batch_size < total:
            time.sleep(sleep_seconds)
    return output


def summarize_query(query: str, client: Any) -> str:
    """Summarize a single query string into a caption."""
    result = summarize_records([{"query": query}], client, batch_size=1)
    return result[0]["caption"]
