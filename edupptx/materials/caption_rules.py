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
删除标准用“区分性”判据：删某属性前先问“删掉它本图会不会和同主体的另一张图无法区分？会就保留”。
保留区分性属性——场景/风景的天气（晴/阴/雨/雪）、时段（晨昏夜）、季节、光照氛围；主体的关键状态/动作。
删除与选材无关的纯装饰：服饰、发型、数量、对话/标注文字、表情、构图；纯形式/形态噪声词（插画、手绘、绘有、扁平、…形象）；手势的方向与持物细节；并列陪衬元素。
保留：画种/媒介词（水墨、国画、山水画——文化形态身份，利召回）；风格词（如“卡通”）仅当是主体身份的一部分（虚拟/拟人形象）时保留。
具名/历史/文化身份（具体人物、历史人物、名作、文物、地标）保留其确定呈现形态（肖像/照片/塑像/邮票等）与名字——这是它能复用到哪里的关键，不当作元词或颜色删除，也不要把具名个体泛化成通用场景或通用职业。
摆拍/肖像/静态主体没有动作：只有 query 明确表达动作/事件时才写动作，不得把背景陈设或环境（书架、窗、远山）升格为主体谓语（如“站在…前”“坐在…旁”）。
锐化规则：P1 删形式噪声、留画种与具名身份；P2 多元素并列时只留具名地标或最显著 1–2 特征，删陪衬，避免稀释焦点；P3 手势/方向细节抽象成它表示的动作（手指向一侧/前方 → 讲解）；P4 删次要道具、整体精简成“主体(+动作/场景类型)”。
主干名词归约：优先输出可复用的自然主干名词，而不是完整视觉描写。
C02 通用主体/物体：颜色、材质、局部结构、背景、远景陪衬默认删除；只有会改变复用目标或教学语义才保留。
通用工具/物体：输出自然类别名，如“计算器”“直角三角尺”“无指针钟表”；匿名人物输出身份主干，如“民国男性”“卡通女教师”“小学生”。
保留限定：会改变用途、教学对象或检索目标的属性，如“直角”“无指针”“空白”“肖像”“卡通”（作为角色身份时）。
整体场景：输出场景类型 + 必要区分属性；天气、时段、季节、光照、水景/江景/山景等会影响选材的场景属性保留，远处小船、古塔、山林等陪衬默认删除。
具名身份投影：C01 canonical caption 对上下文明确具名身份且图像形态支持的具名人物/地标/作品/实物保留专名；不得因 VLM 不能从像素独立识别就把 canonical 改成匿名类别。C03 projection 使用 secondary_reuse_query/secondary_reuse_caption 去名泛化。
形式：中文短语或短句，尽量精简；不写“图片/插画/这张图”等元词（图示类型词除外）。

示例（演示规则，不是逐例补丁）：
- 含寒山寺、乘船文人、水面、远山、古塔的水墨山水画 → 寒山寺江景水墨画
- 绘有满月、苍松、溪流瀑布、远山旷野的中式山水画 → 月夜溪瀑山水画
- 水墨国画，荷塘竹林，浣女在木船中，渔夫驾船 → 荷塘竹林浣女与渔夫的国画
- 手持书本、手指向一侧的卡通女教师形象 → 卡通女教师讲解
- 卡通男性学生形象手持文件夹做讲解手势 → 卡通男学生讲解
- 带花瓣的花枝手绘插画 → 花枝与飘落花瓣
- 扁平手绘郁金香花束 → 郁金香花束
- 山峦插画 → 山峦
- 山水插画 → 山水景色
- 银灰配色带深蓝色按键的台式计算器 → 计算器
- 木质直角三角尺，带有深色握持部件 → 直角三角尺
- 无指针的圆形钟表盘面，带 1 到 12 数字刻度 → 无指针钟表
- 身着黑色长衫、头戴圆顶瓜皮帽的民国男性，腰间挂着油漆刷，背景是中式古街商铺 → 民国男性
- 阴沉天色下的江面，远处有覆林小山与古塔，江面上有小船 → 阴天江景
- 朱自清黑白肖像 → 朱自清肖像
- 卡通风格的淘气雾孩子形象，白蒙蒙圆滚滚雾团，带调皮笑脸 → 卡通雾孩子形象
- 西湖晴天湖景 → 西湖晴天湖景（天气是区分属性，保留）
- 西湖阴天湖景 → 西湖阴天湖景"""


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


SECONDARY_SCENE_CAPTION_RULE = """把一张具名地标场景图投影成可迁移的【通用场景】，用于 C03 语境级复用：对 query 产出 secondary_reuse_query，对主 caption 产出 secondary_reuse_caption。
边界：C01 canonical 保留具名身份；C03 projection 才去名泛化，不能反向要求 canonical 去名。
去名：删掉地标/人物等专名（西湖、卢沟桥、寒山寺、望湖楼…），不要保留任何具体地点/人物名。
secondary_reuse_query：对【query】去名，保留可重新生成/审查该通用场景所需的啰嗦视觉内容。
secondary_reuse_caption：对【主 caption】去名，它就是“主 caption 减去专名”，保持主 caption 的精简度与粒度，不是对 query 的二次概括。
精简不变量：主 caption 已删掉的并列陪衬（远处小船、古塔、山林、拱桥、垂柳等）不得在副 caption 重新出现；多元素并列只留最显著 1–2 特征，副 caption 不得比主 caption 更啰嗦。
保留区分属性：天气（晴/阴/雨/雪）、时段（晨昏夜）、季节、光照、水景/江景/山景等会影响选材的场景类型属性。
形式：中文短语，尽量精简；不写“图片/这张图”等元词。

示例（演示规则，不是逐例补丁；箭头左为主 caption，右为 secondary_reuse_caption）：
- 西湖晴天湖景 → 晴天湖景
- 寒山寺江景水墨画 → 江景水墨画
- 暴雨中的望湖楼湖景 → 雨中楼阁湖景"""


def build_secondary_scene_system_prompt() -> str:
    return (
        "你是中文教育课件图片的通用场景摘要器。\n\n"
        + SECONDARY_SCENE_CAPTION_RULE
        + "\n\n我会给你一个 JSON 数组，每个元素含 query（啰嗦原始描述）与 caption（已精简的主 caption）字段。"
        "secondary_reuse_query 对 query 去名，secondary_reuse_caption 对 caption 去名（不要从 query 重新概括 caption）。"
        "只输出 JSON 数组，长度、顺序与输入一致；每个元素保留 query，"
        "并新增 secondary_reuse_query 与 secondary_reuse_caption 字段。不要输出解释、Markdown 或多余文本。"
    )


def summarize_secondary_scene_records(
    records: list[dict[str, Any]],
    client: Any,
    *,
    query_field: str = "query",
    caption_input_field: str = "caption",
    caption_field: str = "secondary_reuse_caption",
    batch_size: int = 50,
    sleep_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    """Produce a de-named generic-scene caption for C03 dual-write.

    secondary_reuse_query is de-named from the verbose ``query``; the terse
    secondary_reuse_caption is de-named from the already-cleaned primary
    ``caption`` so it inherits the foil-dropping the caption pass already did,
    instead of re-summarizing the noisy query.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    items = list(records)
    output: list[dict[str, Any]] = []
    total = len(items)
    for start in range(0, total, batch_size):
        batch = items[start : start + batch_size]
        minimal = [
            {
                "query": str(r.get(query_field, "")).strip(),
                "caption": str(r.get(caption_input_field, "")).strip(),
            }
            for r in batch
        ]
        messages = [
            {"role": "system", "content": build_secondary_scene_system_prompt()},
            {
                "role": "user",
                "content": "现在请处理下面的 JSON 数组：\n"
                + json.dumps(minimal, ensure_ascii=False, indent=2),
            },
        ]
        max_tokens = max(2048, min(12000, 120 * len(batch) + 1600))
        raw = client.chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
        parsed = _parse_caption_array(raw)
        if len(parsed) != len(batch):
            raise ValueError(f"expected {len(batch)} secondary captions, got {len(parsed)}")
        for original, generated in zip(batch, parsed):
            item = deepcopy(original)
            query_text = str(generated.get("secondary_reuse_query") or generated.get(caption_field) or "").strip()
            caption = str(generated.get(caption_field) or query_text).strip()
            item["secondary_reuse_query"] = query_text
            item[caption_field] = caption
            output.append(item)
        if sleep_seconds > 0 and start + batch_size < total:
            time.sleep(sleep_seconds)
    return output
