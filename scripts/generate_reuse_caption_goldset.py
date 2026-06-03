"""Generate semantic gold labels for the 2026-06-03 caption reuse fixture.

This script is an offline labeling aid. It intentionally does not apply
production hard filters, category filters, grade filters, subject filters,
aspect-ratio filters, or the ``general`` flag. Candidate discovery is global
over all reusable PPT strict index files and uses only target query text and
stored material description fields.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from test_reuse.goldset_builder import (
    REUSABLE_INDEX_FILES,
    extract_plan_image_needs,
    write_goldset_artifacts,
)


PLAN_ROOT = REPO_ROOT / "output"
INDEX_DIR = REPO_ROOT / "materials_library_ppt" / "strict_reuse_indexes"
OUTPUT_DIR = REPO_ROOT / "test_reuse" / "fixtures" / "reuse_caption_goldset_20260603"

C00 = "C00_strict_text_problem_skip"
C01 = "C01_irreplaceable_entity_event_action"
C02 = "C02_generic_subject_object"
C03 = "C03_scene_decor_container"

TEXT_SKIP_HINTS = (
    "课文原文",
    "课文段落",
    "原文段落",
    "带拼音",
    "拼音",
    "生字卡",
    "字卡",
    "词语卡",
    "词卡",
    "板书文字",
    "板书",
    "标题文字",
    "文字标签",
    "标签纸",
    "练习题",
    "习题",
    "题目",
    "选择题",
    "填空题",
    "阅读题",
    "应用题",
    "算式",
    "方程",
    "公式",
    "表格数据",
    "坐标轴",
    "统计表",
)

SCENE_HINTS = (
    "风光",
    "风景",
    "景色",
    "远景",
    "背景",
    "场景",
    "庭院",
    "教室",
    "校园",
    "房间",
    "操场",
    "公园",
    "草原",
    "山水",
    "夜景",
    "天空",
    "森林",
    "河流",
    "海边",
    "田野",
    "村庄",
    "城市",
    "边框",
    "装饰",
    "空白卡片",
    "卡片背景",
    "容器",
    "展板",
    "黑板背景",
    "时间轴",
    "流程图",
    "示意图背景",
)

IRREPLACEABLE_HINTS = (
    "中国石拱桥",
    "赵州桥",
    "卢沟桥",
    "人民英雄纪念碑",
    "圆明园",
    "故宫",
    "长城",
    "颐和园",
    "藤野先生",
    "鲁迅",
    "老舍",
    "朱自清",
    "叶圣陶",
    "女娲",
    "盘古",
    "嫦娥",
    "夸父",
    "曹冲",
    "司马光",
    "狐狸分奶酪",
    "小蝌蚪找妈妈",
    "大禹治水",
    "草船借箭",
)

STOP_CHARS = set(" \t\r\n，。！？、；：,.!?;:（）()[]【】《》“”\"'`~·+-*/=<>_")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _asset_text(asset: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "caption",
        "query",
        "context_summary",
        "teaching_intent",
        "theme",
        "unit_ref",
    ):
        value = asset.get(key)
        if isinstance(value, list):
            parts.extend(_clean(item) for item in value)
        else:
            parts.append(_clean(value))
    topic_refs = asset.get("topic_refs")
    if isinstance(topic_refs, list):
        parts.extend(_clean(item) for item in topic_refs)
    return " ".join(part for part in parts if part)


def _load_assets() -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file_name in REUSABLE_INDEX_FILES:
        payload = json.loads((INDEX_DIR / file_name).read_text(encoding="utf-8"))
        for raw in payload.get("assets") or []:
            if not isinstance(raw, dict):
                continue
            asset_id = _clean(raw.get("asset_id"))
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            item = dict(raw)
            item["_source_index_file"] = file_name
            item["_semantic_text"] = _asset_text(raw)
            assets.append(item)
    return assets


def _compact_text(text: str) -> str:
    return "".join(ch for ch in text if ch not in STOP_CHARS)


def _char_counter(text: str) -> Counter[str]:
    compact = _compact_text(text)
    return Counter(ch for ch in compact if ch.strip())


def _coverage(query: str, candidate_text: str) -> float:
    q = _char_counter(query)
    if not q:
        return 0.0
    c = _char_counter(candidate_text)
    overlap = sum(min(count, c.get(ch, 0)) for ch, count in q.items())
    return overlap / max(sum(q.values()), 1)


def _longest_common_run(left: str, right: str) -> int:
    a = _compact_text(left)
    b = _compact_text(right)
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for ch in a:
        current = [0]
        for j, other in enumerate(b, start=1):
            value = previous[j - 1] + 1 if ch == other else 0
            current.append(value)
            if value > best:
                best = value
        previous = current
    return best


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _target_group(query: str) -> tuple[str, str]:
    if _contains_any(query, TEXT_SKIP_HINTS):
        return C00, "目标核心是可读文字/题目/拼音/公式，按 C00 跳过复用。"
    if _contains_any(query, IRREPLACEABLE_HINTS) or "《" in query or "》" in query:
        return C01, "目标包含专有实体、作品人物或特定事件，按 C01。"
    if _contains_any(query, SCENE_HINTS):
        return C03, "目标主要是场景、背景、装饰或容器，按 C03。"
    return C02, "目标主要是通用主体、人物、动物或物体，按 C02。"


def _acceptance_floor(top_score: float) -> float:
    if top_score >= 0.35:
        return 0.16
    if top_score >= 0.25:
        return 0.13
    if top_score >= 0.18:
        return 0.10
    if top_score >= 0.12:
        return 0.075
    return math.inf


def _candidate_summary(asset: dict[str, Any], *, score: float, coverage: float, lcs: int) -> dict[str, Any]:
    return {
        "asset_id": asset.get("asset_id"),
        "score": round(float(score), 6),
        "coverage": round(float(coverage), 4),
        "longest_common_run": int(lcs),
        "caption": _clean(asset.get("caption")),
        "query": _clean(asset.get("query")),
        "context_summary": _clean(asset.get("context_summary")),
        "strict_reuse_group": _clean(asset.get("strict_reuse_group")),
        "asset_kind": _clean(asset.get("asset_kind")),
        "source_index_file": _clean(asset.get("_source_index_file")),
    }


def _select_candidates(
    *,
    query: str,
    ranked_assets: list[tuple[dict[str, Any], float]],
) -> tuple[list[str], list[dict[str, Any]], str]:
    if not ranked_assets:
        return [], [], "no_assets"
    top_score = ranked_assets[0][1]
    floor = _acceptance_floor(top_score)
    accepted: list[str] = []
    audit_candidates: list[dict[str, Any]] = []
    for asset, score in ranked_assets[:40]:
        coverage = _coverage(query, asset["_semantic_text"])
        lcs = _longest_common_run(query, asset["_semantic_text"])
        audit_candidates.append(_candidate_summary(asset, score=score, coverage=coverage, lcs=lcs))
        if len(accepted) >= 8:
            continue
        if score < floor:
            continue
        if coverage < 0.18 and lcs < 3:
            continue
        accepted.append(_clean(asset.get("asset_id")))
    if accepted:
        return accepted, audit_candidates, "semantic_text_match"
    return [], audit_candidates, "no_sufficient_semantic_match"


def main() -> int:
    plan_paths = sorted(PLAN_ROOT.glob("session_*/plan.json"))
    needs = extract_plan_image_needs(plan_paths)
    assets = _load_assets()
    asset_ids = [_clean(asset.get("asset_id")) for asset in assets]

    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(2, 4),
        min_df=1,
        max_features=160000,
        norm="l2",
    )
    query_texts = [_clean(row.get("gold_label_text")) or _clean(row.get("query")) for row in needs]
    asset_texts = [asset["_semantic_text"] for asset in assets]
    matrix = vectorizer.fit_transform([*query_texts, *asset_texts])
    similarity = cosine_similarity(matrix[: len(query_texts)], matrix[len(query_texts) :])

    rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for index, source in enumerate(needs):
        query = query_texts[index]
        group, group_reason = _target_group(query)
        ranked_indices = similarity[index].argsort()[::-1][:80]
        ranked_assets = [(assets[int(i)], float(similarity[index, int(i)])) for i in ranked_indices]
        if group == C00:
            acceptable: list[str] = []
            best: list[str] = []
            label_reason = "target_is_c00_skip"
            audit_candidates = [
                _candidate_summary(
                    asset,
                    score=score,
                    coverage=_coverage(query, asset["_semantic_text"]),
                    lcs=_longest_common_run(query, asset["_semantic_text"]),
                )
                for asset, score in ranked_assets[:20]
            ]
        else:
            acceptable, audit_candidates, label_reason = _select_candidates(
                query=query,
                ranked_assets=ranked_assets,
            )
            best = acceptable[:1]

        row = {
            **source,
            "label_status": "labeled",
            "target_strict_reuse_group_gold": group,
            "target_is_c00_skip": group == C00,
            "should_reuse": bool(acceptable),
            "acceptable_asset_ids": acceptable,
            "best_asset_ids": best,
            "label_notes": (
                f"ChatGPT semantic label from target query and material description fields only; "
                f"{group_reason} candidate_decision={label_reason}."
            ),
            "label_method": "chatgpt_semantic_text_only_no_rule_filter_v1",
        }
        rows.append(row)
        audit_rows.append(
            {
                "need_id": source["need_id"],
                "query": query,
                "target_strict_reuse_group_gold": group,
                "target_group_reason": group_reason,
                "decision": label_reason,
                "acceptable_asset_ids": acceptable,
                "best_asset_ids": best,
                "top_candidates": audit_candidates[:20],
            }
        )

    write_goldset_artifacts(
        rows=rows,
        output_dir=OUTPUT_DIR,
        index_dir=INDEX_DIR,
        candidate_audit_rows=audit_rows,
    )
    summary = {
        "need_count": len(rows),
        "asset_count": len(asset_ids),
        "should_reuse_count": sum(1 for row in rows if row["should_reuse"]),
        "c00_skip_count": sum(1 for row in rows if row["target_is_c00_skip"]),
        "empty_candidate_count": sum(1 for row in rows if not row["acceptable_asset_ids"]),
        "group_counts": dict(Counter(row["target_strict_reuse_group_gold"] for row in rows)),
        "method": "chatgpt_semantic_text_only_no_rule_filter_v1",
    }
    (OUTPUT_DIR / "semantic_label_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
