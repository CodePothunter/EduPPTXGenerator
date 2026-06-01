"""Single source of truth for the general (cross-subject reuse) judgment.

Imported by the PPT library build (and later the plan / AI-image library) so
the general definition never drifts. The judgment is based only on ``query``:
it is blind to theme, subject, grade, and source course metadata.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

GENERAL_RULE = """判断该素材是否可以在语文、数学、物理等不同学科课件中跨学科通用复用（general）。
只看 query 描述的可见画面内容，对 theme、subject、grade、来源课程一律盲视。
general 不是判断素材是否能脱离原 PPT 页面，也不是判断同类型图片是否可替换。
只有明确跨学科中性的素材才输出 true；模糊项默认 false。

判定顺序固定：
1. 先查强制 false，命中任一即 general=false。
2. 再查正向准入，只有明确命中跨学科中性白名单才 general=true。
3. 其余边界、模糊、需要强行解释用途的素材都 general=false。

强制 false（命中任一即 false）：
1. 烤进画面的确定可读内容：query 写出确定的汉字、词句、拼音、数值、公式、台词、标签、题干、选项、答案、课文原文。
2. 具名或故事身份：具名人物、地点、作品、典故、IP、品牌、具体课文故事角色，或故事绑定的事件、动作、强情绪叙事。
3. 具体知识结构：数学/物理/科学关系图、实验原理、变量对比、图形关系、测量图、统计图、流程机制图。
4. 单一知识点工具或操作：直角三角尺、钟表盘面、圆规、计算器、量角器、台秤、显微镜、望远镜、折扇讲角、手折纸认识角、实验器材操作。
5. 文学、季节或课文意境装饰：春花、樱花、垂柳、燕子、春日山水、古诗意境、国风卷轴山水、特定课文氛围装饰。
6. 历史文化专属题材：历史文物、考古器物、古人、古装人物、具体历史/民俗/典故场景。

正向准入（未命中强制 false 且命中以下任一类才 true）：
1. 通用课堂人物或角色：普通学生、教师、举手、指向、拿书、课堂互动等弱教学姿态，不含具体题目、课文文字或强叙事情节。
2. 通用日常物件：钥匙、铅笔、普通书本、空白卡片、普通图标、简单文具，不承担单一学科知识点。
3. 通用容器与版式装饰：空白底图、边框、对话气泡、空白文本框、占位框、空白挂轴、便签。
4. 通用弱动作：握笔、发言、指向、阅读、讨论、普通记录等不绑定具体学科知识的动作。

示例（演示规则，不是逐例补丁）：
- 黄色钥匙插画 / 卡通小女孩 / 卡通女教师手持书本指向一侧 / 手握铅笔的手的简笔画 / 空白装饰底图 / 对话气泡装饰框 → true
- 手折纸认识角 / 带奔马水墨画的折扇 / 直角三角尺 / 园艺剪刀讲生活中的角 / 钟表盘面 → false
- 春花花枝 / 樱花花枝 / 飞鸟燕子 / 垂柳枝条 / 春日山水 / 国风卷轴山水 → false
- 朱自清肖像 / 青铜鼎 / 绿色对话气泡内“精读”文字 / 凸透镜光路图 / 看图列式题图 → false"""


def build_general_system_prompt() -> str:
    return (
        "你是中文教育课件图片的跨学科通用复用判断器。\n\n"
        + GENERAL_RULE
        + "\n\n我会给你一个 JSON 数组，每个元素含 query 字段。"
        "只输出 JSON 数组，长度、顺序与输入一致；每个元素保留 query，"
        "并新增或覆盖布尔字段 general（true 或 false）。"
        "不要输出解释、Markdown 或多余文本。"
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


def _parse_general_array(raw: str) -> list[dict[str, Any]]:
    text = _strip_json_fences(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        import json_repair

        payload = json_repair.loads(text)
    if not isinstance(payload, list):
        raise ValueError("general judge response must be a JSON array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"general response item #{index} is not an object")
        result.append(item)
    return result


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    return text in {"true", "1", "yes", "是"}


def judge_records(
    records: list[dict[str, Any]],
    client: Any,
    *,
    query_field: str = "query",
    general_field: str = "general",
    batch_size: int = 50,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    """Judge each record's general bool from its query via GENERAL_RULE."""

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    items = list(records)
    output: list[dict[str, Any]] = []
    total = len(items)
    for start in range(0, total, batch_size):
        batch = items[start : start + batch_size]
        minimal = [{"query": str(record.get(query_field, "")).strip()} for record in batch]
        messages = [
            {"role": "system", "content": build_general_system_prompt()},
            {
                "role": "user",
                "content": "现在请处理下面的 JSON 数组：\n"
                + json.dumps(minimal, ensure_ascii=False, indent=2),
            },
        ]
        max_tokens = max(2048, min(12000, 60 * len(batch) + 1600))
        raw = client.chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
        parsed = _parse_general_array(raw)
        if len(parsed) != len(batch):
            raise ValueError(f"expected {len(batch)} general items, got {len(parsed)}")
        for original, generated in zip(batch, parsed):
            item = deepcopy(original)
            item[general_field] = _coerce_bool(generated.get("general"))
            output.append(item)
        if sleep_seconds > 0 and start + batch_size < total:
            time.sleep(sleep_seconds)
    return output


def judge_query(query: str, client: Any) -> bool:
    """Judge a single query's general bool."""

    result = judge_records([{"query": query}], client, batch_size=1)
    return bool(result[0]["general"])
