"""Optional prompt fragment for exercise-bank aware PPT planning."""

from __future__ import annotations

from collections.abc import Iterable


def build_exercise_policy_prompt(exercise_candidates_text: str = "") -> str:
    """Return the optional A/B/C exercise policy prompt fragment."""

    parts = [
        "## 习题节奏与题量（可选题库功能）",
        "- 如果题库提供 A/B/C 三类习题：A - 复习巩固，B - 综合运用，C - 扩展探索，应按教学节奏选择使用，不要把三类题全部机械堆进 PPT",
        "- A 类适合课前回顾、知识点后短练；B 类适合课堂综合练习或核心任务；C 类适合结尾拓展、课后挑战或选做探究",
        "- 根据学科、课时长度、PPT 总页数和教学节奏自主决定 A/B/C 题量；题目数量服务教学目标，练习页占比不能过高",
        "- 数学可适当增加练习数量，适合保留更多 A 类巩固题和 B 类综合题",
        "- 物理保持中等题量，优先结合图示、实验、现象解释和必要计算，不要堆过多纯计算题",
        "- 语文控制题量，优先选择阅读、表达、赏析和讨论价值高的问题，做到少而精",
        "- C 类扩展探索默认最多 1 题，通常放在结尾或作为课后挑战；页数较少或教学主线紧凑时可以省略",
        "- 如果 PPT 页数较少，优先保留 B 类综合运用，其次保留少量 A 类复习巩固，C 类可以省略或改为课后思考",
        "",
        "### 题库题目引用规则",
        "- 如果使用下方候选题，只能在页面对象中输出 `exercise_refs`: [\"题目ID\"]，不要自行改写题干、答案、解析或题目图片",
        "- 题干、选项、答案和题目自带图片会在规划后由代码按 `exercise_refs` 确定性绑定",
        "- 不要把题干、选项、答案、公式、具体数值或题目图片写成 `material_needs.images[].query`",
        "- 可以根据候选题的分类、知识点、题型和是否有图片，决定它适合放在课前回顾、知识点后短练、综合运用或课后拓展",
    ]
    if exercise_candidates_text.strip():
        parts.extend([
            "",
            "### 可选题库候选",
            "只能引用以下候选题 ID；如果没有合适题目，可以不引用。",
            exercise_candidates_text.strip(),
        ])
    return "\n".join(parts)


def build_exercise_refinement_prompt() -> str:
    """Return the stage-2 preservation rules for bound exercise payloads."""

    return "\n".join([
        "## 题库习题保持规则（可选题库功能）",
        "- 如果阶段 1 页面中存在 `exercise_refs` 或 `exercise_payloads`，必须原样保留题目 ID，不要替换为自造题目",
        "- 题干、选项、答案、解析和题目自带图片属于精确教学载荷，不能改写、重算或改成图片检索 query",
        "- 模板细化只能调整页面设计备注、讲解话术和布局表达，不得改变数据库题目的答案与配图绑定关系",
    ])


def format_exercise_candidates_for_prompt(candidates: Iterable[object]) -> str:
    """Format normalized exercise records for the stage-1 planning prompt."""

    lines: list[str] = []
    for item in candidates:
        exercise_id = str(getattr(item, "exercise_id", "") or "").strip()
        if not exercise_id:
            continue
        category = str(getattr(item, "category", "") or "").strip() or "其他"
        subject = str(getattr(item, "subject", "") or "").strip() or "其他"
        grade = str(getattr(item, "grade", "") or "").strip() or "其他"
        stem = _compact(str(getattr(item, "stem", "") or ""), 80)
        points = getattr(item, "knowledge_points", ()) or ()
        point_text = "、".join(str(point) for point in points if str(point).strip())
        has_image = "是" if getattr(item, "image_assets", ()) else "否"
        detail = f"- {exercise_id} | {category} | {subject}/{grade} | 有图片: {has_image} | {stem}"
        if point_text:
            detail += f" | 知识点: {_compact(point_text, 40)}"
        lines.append(detail)
    return "\n".join(lines)


def _compact(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"
