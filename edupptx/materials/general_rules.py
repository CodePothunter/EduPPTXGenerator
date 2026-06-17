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

GENERAL_RULE = """判断该素材本身是否可在语文、数学、物理之间跨学科通用复用（general）。
只看 query 描述的画面内容，对 theme、subject、grade、来源课程一律盲视。
本判断只在非 skip（C00 精确文字/题目页）素材上评估——skip 类已前置短路，不进入此判断。
判定顺序：先查强-false，命中任一即 general=false；都不命中则 general=true。不要因“判断模糊”就默认 false。

强-false（命中任一即 false）：
1. 烤进画面的确定可读内容：query 写出确定的可读汉字/词/句/拼音/数值/公式/台词/标签/栏目词
   （如“精读”、游戏指令“请你抬起一条腿”、题面）。复用会连文字一起带走 → 锁定语境。
   只看是否有确定可读内容，不因泛指“有文字/有标注”而触发。
2. 具名或故事身份：具名人物/地点/物体/作品/典故（朱自清、李白、孙悟空、西游记、纪昌学射、女娲补天…），
   或故事绑定的角色/事件/动作/强情绪叙事。
3. 具体知识结构：特定知识关系/流程/原理/变量对比的结构图示（多数已是 skip，此处兜底）。
4. 历史/文化题材身份：画面主体是历史文物/考古器物（青铜器、瓷器、编钟、出土器物）、
   古人/古装人物、具体历史/民俗/典故场景——即主体本身是历史文化专属题材。
   · 通用器物中性：显微镜、放大镜、直尺/尺子、硬币/1元硬币、砝码、温度计、烧杯等
     跨数学、物理、生活场景都可能复用的中性器材或实物，不触发 false。
5. 语文/历史/文化绑定的整体场景题材：
   带古装人物活动、具名地点/地标、具体典故/诗文情节、历史民俗语境、
   强叙事或强情绪氛围的整体场景 → general=false。
   纯装饰底图豁免：无人物、无具名地点、无叙事、无具体知识/故事载荷的整体山水、群山、
   风景、园林、氛围背景、装饰图案、纹样底图 → 不触发 false；
   若未命中其他强-false，则 general=true。
   任何媒介都一样：国画、水墨、水彩、青绿、写实笔法本身不是 false 触发器。
   离散单主体（单个花/鸟/竹/石/动物/工具/物件/卡通角色/装饰边框/空白容器）即便用国画/水墨风格，仍 general=true。

general=true（仅在以上都不命中时）：领域中性的离散通用视觉元素，放在任意一科都不违和。
例如通用卡通角色/人物、单个动植物、日常物件、通用工具、装饰/边框/空白容器/对话气泡；
常见计量、实验、生活实物及其中性测量/盛放场景也可 true，例如尺子、硬币、1元硬币、砝码、温度计、烧杯；
无人物、无具名地点、无叙事、无知识/故事载荷的纯装饰底图也可 true——即便用国画/水墨风格。

示例（演示规则，不是逐例补丁）：
- 抱着橡果的卡通松鼠 / 双目光学显微镜实物 / 国画风格单只飞鸟 / 手握黄绿色直尺 / 1元硬币特写 / 砝码、温度计和烧杯实物 / 空白装饰文本框 → true
- 水墨山水风景插画 / 水彩山峦 / 青绿色水彩风格群山插画 / 青绿色渐变国风山水装饰图案 → true（纯装饰底图豁免）
- 樱花花枝与飘落花瓣的清新插画背景 / 水墨白玉兰花枝插画 → true（离散装饰或花枝主体）
- 西湖晴天湖景 / 寒山寺江景水墨画 / 古诗背景青绿山水卷轴插画 → false（具名地点或诗文语境）
- 青铜鼎 → false（历史文物题材）
- 竹林亭边古人饮茶国画 → false（古装人物题材）
- 朱自清肖像 / 李白工笔肖像 / 纪昌学射箭 → false（具名或故事身份）
- 秋日黄昏母亲孩子并肩温暖场景 → false（强情绪叙事场景）
- 绿色对话气泡内“精读”文字 / 小朋友做游戏配气泡“请你抬起一条腿” → false（烤进可读文字锁语境）"""


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
