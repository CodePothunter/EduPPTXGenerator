"""Offline builder for the generated AI image asset database."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
KEYWORD_SCHEMA_VERSION = 3
DEFAULT_DB_FILENAME = "ai_image_asset_db.json"
DEFAULT_KEYWORD_BATCH_SIZE = 12
DEFAULT_LIBRARY_IMAGE_DIR = "ai_images"
REUSE_MANIFEST_FILENAME = "ai_image_reuse_manifest.json"
REUSE_DEBUG_FILENAME = "ai_image_reuse_debug.json"
DEFAULT_REUSE_CANDIDATE_LIMIT = 5
DEFAULT_MIN_REUSE_KEYWORD_SCORE = 0.55

KEYWORD_REUSE_WEIGHT = 0.80
PROMPT_REUSE_WEIGHT = 0.12
CONTEXT_REUSE_WEIGHT = 0.08
CORE_KEYWORD_WEIGHT = 0.80
SCOPE_KEYWORD_WEIGHT = 0.10
ROLE_ASPECT_KEYWORD_WEIGHT = 0.05
STYLE_KEYWORD_WEIGHT = 0.05

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_REUSE_SCOPES = {"course_specific", "subject_generic", "visual_generic"}
_GENERIC_CORE_NOISE = {
    "插画",
    "教学插画",
    "编辑感",
    "风格",
    "简洁",
    "清晰",
    "简洁清晰",
    "高清",
    "背景",
    "场景",
    "示意图",
    "图片",
}
_GENERIC_STYLE_NOISE = {
    "插画",
    "教学插画",
    "编辑感",
    "风格",
    "高清",
    "背景",
    "图片",
}
_GRADE_RE = re.compile(
    r"(小学[一二三四五六0-9]+年级|初中[一二三0-9]+年级|高中[一二三0-9]+年级|"
    r"[一二三四五六七八九十0-9]+年级|初[一二三123]|高[一二三123]|大[一二三四1234])"
)

_SUBJECT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("语文", ("语文", "课文", "作文", "阅读", "古诗", "文言文", "文学", "拼音", "汉字")),
    ("数学", ("数学", "代数", "几何", "函数", "方程", "勾股", "概率", "统计")),
    ("英语", ("英语", "英文", "english", "grammar", "vocabulary")),
    ("物理", ("物理", "力学", "电磁", "光学", "热学", "运动", "电路")),
    ("化学", ("化学", "元素", "分子", "原子", "化合", "实验")),
    ("生物", ("生物", "细胞", "生态", "光合作用", "遗传", "生命科学")),
    ("历史", ("历史", "朝代", "战争", "革命", "文明史")),
    ("地理", ("地理", "地图", "气候", "地形", "经纬", "区域")),
    ("道德与法治", ("道德与法治", "政治", "法治", "思想品德")),
    ("科学", ("科学", "自然科学", "科学课")),
    ("信息技术", ("信息技术", "编程", "计算机", "人工智能", "算法")),
    ("美术", ("美术", "绘画", "色彩", "艺术")),
    ("音乐", ("音乐", "乐理", "节奏", "旋律")),
    ("体育", ("体育", "运动", "体能")),
)


def build_ai_image_asset_db(output_root: str | Path) -> dict[str, Any]:
    """Scan rendered sessions and return the generated-image asset database.

    The semantic fields intentionally stay minimal for the first reuse pass:
    prompt, theme, grade, and subject. Source fields are retained only so the
    asset can be found and traced back to its plan slot.
    """

    root = Path(output_root).expanduser().resolve()
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []

    for session_dir in _iter_session_dirs(root):
        plan_path = session_dir / "plan.json"
        try:
            plan = _read_json(plan_path)
        except Exception as exc:
            warnings.append(f"skip {plan_path}: {exc}")
            continue

        context = _extract_context(plan)
        materials_dir = session_dir / "materials"
        reused_image_paths = _load_reused_image_paths(session_dir)

        background_asset = _build_background_asset(
            root=root,
            session_dir=session_dir,
            plan_path=plan_path,
            materials_dir=materials_dir,
            context=context,
            plan=plan,
            reused_image_paths=reused_image_paths,
        )
        if background_asset is not None:
            assets.append(background_asset)

        pages = plan.get("pages") if isinstance(plan, dict) else None
        if not isinstance(pages, list):
            warnings.append(f"skip image slots in {plan_path}: pages is not a list")
            continue

        for page_index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            for asset in _iter_page_image_assets(
                root=root,
                session_dir=session_dir,
                plan_path=plan_path,
                materials_dir=materials_dir,
                context=context,
                page=page,
                page_index=page_index,
                reused_image_paths=reused_image_paths,
            ):
                assets.append(asset)

    assets.sort(key=lambda item: (item["source"]["session_id"], item["source"].get("page_number") or 0, item["asset_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "output_root": str(root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": warnings,
    }


def write_ai_image_asset_db(
    output_root: str | Path,
    db_path: str | Path | None = None,
    *,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> tuple[dict[str, Any], Path]:
    """Build and write the database JSON file."""

    root = Path(output_root).expanduser().resolve()
    target = Path(db_path).expanduser().resolve() if db_path else root / DEFAULT_DB_FILENAME
    db = build_ai_image_asset_db(root)
    if keyword_client is not None:
        enrich_ai_image_asset_db_keywords(
            db,
            keyword_client,
            batch_size=keyword_batch_size,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    return db, target


def update_ai_image_asset_library(
    session_dir: str | Path,
    library_dir: str | Path,
    *,
    db_filename: str = DEFAULT_DB_FILENAME,
    keyword_client: Any | None = None,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> tuple[dict[str, Any], Path]:
    """Copy a session's AI-generated images into the reusable library and merge metadata.

    The library is append/update only: assets are keyed by asset_id and the
    central JSON stays at ``library_dir/db_filename``. This does not perform
    reuse matching; it only ingests newly generated materials for future runs.
    """

    session_root = Path(session_dir).expanduser().resolve()
    library_root = Path(library_dir).expanduser().resolve()
    db_path = library_root / db_filename
    library_root.mkdir(parents=True, exist_ok=True)

    session_db = build_ai_image_asset_db(session_root)
    if keyword_client is not None:
        enrich_ai_image_asset_db_keywords(
            session_db,
            keyword_client,
            batch_size=keyword_batch_size,
        )

    ingested_db = _copy_db_assets_to_library(
        session_db,
        source_root=session_root,
        library_root=library_root,
    )
    existing_db = _read_existing_db(db_path)
    merged_db = _merge_asset_library_db(
        existing_db,
        ingested_db,
        library_root=library_root,
    )
    db_path.write_text(json.dumps(merged_db, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged_db, db_path


def find_reusable_ai_image_asset(
    *,
    library_dir: str | Path,
    asset_kind: str,
    prompt: str,
    theme: str = "",
    grade: str = "",
    subject: str = "",
    page_title: str = "",
    role: str = "",
    aspect_ratio: str = "",
    keyword_client: Any | None = None,
    candidate_limit: int = DEFAULT_REUSE_CANDIDATE_LIMIT,
    min_keyword_score: float = DEFAULT_MIN_REUSE_KEYWORD_SCORE,
    debug_path: str | Path | None = None,
    debug_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Find a reusable AI image asset from the central library.

    The final reuse decision is deterministic: BM25 keyword, prompt, and
    context scores are weighted, then the best candidate above threshold wins.
    The LLM is used only to build target keywords/context when a client exists.
    """

    library_root = Path(library_dir).expanduser().resolve()
    db_path = library_root / DEFAULT_DB_FILENAME
    db = _read_existing_db(db_path)
    assets = db.get("assets")

    target = _build_reuse_target_asset(
        asset_kind=asset_kind,
        prompt=prompt,
        theme=theme,
        grade=grade,
        subject=subject,
        page_title=page_title,
        role=role,
        aspect_ratio=aspect_ratio,
    )

    debug_record = _new_reuse_debug_record(
        library_root=library_root,
        db_path=db_path,
        asset_count=len(assets) if isinstance(assets, list) else 0,
        candidate_limit=candidate_limit,
        min_keyword_score=min_keyword_score,
        context=debug_context,
    )

    def finish(reason: str, match: dict[str, Any] | None = None) -> dict[str, Any] | None:
        debug_record["decision"] = {
            "reused": match is not None,
            "reason": reason,
            "asset_id": _dict(match.get("asset")).get("asset_id") if match else "",
            "keyword_score": match.get("keyword_score") if match else None,
        }
        _append_reuse_debug_record(debug_path, debug_record)
        return match

    if not isinstance(assets, list) or not assets:
        debug_record["target"] = _reuse_debug_asset_payload(target)
        return finish("empty_library")

    if keyword_client is not None:
        target_db = {"schema_version": SCHEMA_VERSION, "assets": [target], "warnings": []}
        enrich_ai_image_asset_db_keywords(target_db, keyword_client, batch_size=1)
        target = target_db["assets"][0]
    debug_record["target"] = _reuse_debug_asset_payload(target)
    debug_record["candidate_scores"] = _collect_reuse_candidate_debug(target, assets, library_root)

    ranked_candidates = _rank_reuse_candidates(
        target,
        assets,
        library_root=library_root,
        limit=candidate_limit,
    )
    debug_record["ranked_candidates"] = [
        _reuse_debug_candidate_payload(candidate) for candidate in ranked_candidates
    ]
    candidates = [candidate for candidate in ranked_candidates if candidate["keyword_score"] >= min_keyword_score]
    debug_record["thresholded_candidates"] = [
        _reuse_debug_candidate_payload(candidate) for candidate in candidates
    ]
    if not candidates:
        return finish("no_candidate_above_keyword_threshold")

    return finish("reused_by_weighted_bm25_score", candidates[0])


def record_reused_ai_image_asset(
    *,
    session_dir: str | Path,
    session_image_path: str | Path,
    match: dict[str, Any],
) -> None:
    """Record that a session image came from the reusable asset library."""

    session_root = Path(session_dir).expanduser().resolve()
    image_path = Path(session_image_path).expanduser().resolve()
    try:
        rel_image_path = image_path.relative_to(session_root).as_posix()
    except ValueError:
        rel_image_path = str(image_path)

    asset = _dict(match.get("asset"))
    entry = {
        "image_path": rel_image_path,
        "reuse_asset_id": asset.get("asset_id"),
        "library_image_path": asset.get("image_path"),
        "keyword_score": match.get("keyword_score"),
        "score_details": match.get("score_details", {}),
        "reused_at": datetime.now(timezone.utc).isoformat(),
    }

    manifest_path = session_root / "materials" / REUSE_MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _read_json_if_exists(manifest_path)
    entries = manifest.get("reused_assets") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        entries = []
    entries = [item for item in entries if _dict(item).get("image_path") != rel_image_path]
    entries.append(entry)
    manifest = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reused_assets": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_reuse_debug_record(
    *,
    library_root: Path,
    db_path: Path,
    asset_count: int,
    candidate_limit: int,
    min_keyword_score: float,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "context": context or {},
        "library_dir": str(library_root),
        "db_path": str(db_path),
        "asset_count": asset_count,
        "candidate_limit": candidate_limit,
        "min_keyword_score": min_keyword_score,
        "target": {},
        "candidate_scores": [],
        "ranked_candidates": [],
        "thresholded_candidates": [],
        "decision": {},
    }


def _append_reuse_debug_record(path: str | Path | None, record: dict[str, Any]) -> None:
    if path is None:
        return
    debug_path = Path(path).expanduser()
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_json_if_exists(debug_path)
    queries = existing.get("queries") if isinstance(existing, dict) else None
    if not isinstance(queries, list):
        queries = []
    queries.append(record)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
    }
    debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _reuse_debug_asset_payload(asset: dict[str, Any]) -> dict[str, Any]:
    source = _dict(asset.get("source"))
    grade = _clean_text(asset.get("grade"))
    return {
        "asset_id": asset.get("asset_id"),
        "asset_kind": asset.get("asset_kind"),
        "image_path": asset.get("image_path"),
        "prompt": asset.get("prompt"),
        "core_keywords": _keyword_list(asset.get("core_keywords"), max_items=16),
        "style_keywords": _keyword_list(asset.get("style_keywords"), max_items=12),
        "page_title": source.get("page_title"),
        "subject": asset.get("subject"),
        "grade": grade,
        "grade_band": infer_grade_band(grade),
        "role": _asset_role(asset),
        "aspect_ratio": asset.get("aspect_ratio"),
        "reuse_scope": asset.get("reuse_scope"),
        "context_summary": asset.get("context_summary"),
    }


def _reuse_debug_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = _reuse_debug_asset_payload(_dict(candidate.get("asset")))
    payload["keyword_score"] = candidate.get("keyword_score")
    payload["library_image_path"] = str(candidate.get("library_image_path") or "")
    payload["score_details"] = candidate.get("score_details") or {}
    return payload


def _collect_reuse_candidate_debug(
    target: dict[str, Any],
    assets: list[Any],
    library_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        payload = _reuse_debug_asset_payload(item)
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            payload["keyword_score"] = 0.0
            payload["library_image_path"] = str(image_path or "")
            payload["score_details"] = {
                "score": 0.0,
                "reject_reason": "missing_library_image",
            }
            rows.append(payload)
            continue

        details = _score_reuse_candidate_details(target, item)
        score = float(details.get("score") or 0.0)
        payload["keyword_score"] = round(score, 4)
        payload["library_image_path"] = str(image_path)
        payload["score_details"] = _debug_score_details(details)
        rows.append(payload)

    rows.sort(key=lambda item: float(item.get("keyword_score") or 0.0), reverse=True)
    return rows


def enrich_ai_image_asset_db_keywords(
    db: dict[str, Any],
    client: Any,
    *,
    batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> dict[str, Any]:
    """Add LLM-built keyword fields to an already scanned asset DB.

    This is intentionally an offline enrichment step. It does not participate
    in PPT generation unless a caller later chooses to consume the generated
    fields.
    """

    assets = db.get("assets")
    if not isinstance(assets, list) or not assets:
        return db

    batch_size = max(1, int(batch_size or DEFAULT_KEYWORD_BATCH_SIZE))
    warnings = db.setdefault("warnings", [])
    db["schema_version"] = max(int(db.get("schema_version") or 0), KEYWORD_SCHEMA_VERSION)
    db["keyword_built_at"] = datetime.now(timezone.utc).isoformat()
    db["keyword_builder"] = {
        "method": "llm_reuse_scope_keyword_extraction",
        "batch_size": batch_size,
        "model": _client_model_name(client),
    }

    for start in range(0, len(assets), batch_size):
        batch = [asset for asset in assets[start:start + batch_size] if isinstance(asset, dict)]
        if not batch:
            continue
        try:
            response = _call_keyword_llm(client, batch)
            by_id = _keyword_payload_by_asset_id(response)
        except Exception as exc:
            warnings.append(f"keyword batch {start // batch_size + 1} failed: {exc}")
            continue

        for asset in batch:
            asset_id = _clean_text(asset.get("asset_id"))
            payload = by_id.get(asset_id)
            if payload is None:
                warnings.append(f"keyword payload missing for {asset_id}")
                continue
            _apply_keyword_payload(asset, payload)

    return db


def _call_keyword_llm(client: Any, batch: list[dict[str, Any]]) -> dict[str, Any] | list[Any]:
    messages = _build_keyword_messages(batch)
    max_tokens = max(2048, min(16384, 900 * len(batch) + 1200))
    chat_json = getattr(client, "chat_json", None)
    if callable(chat_json):
        try:
            return chat_json(
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
                max_retries=1,
            )
        except TypeError:
            return chat_json(messages, temperature=0.0, max_tokens=max_tokens)

    chat = getattr(client, "chat", None)
    if not callable(chat):
        raise TypeError("keyword client must provide chat_json() or chat()")
    raw = chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
    return _load_json_response(raw)


def _build_keyword_messages(batch: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, Any]] = []
    for asset in batch:
        source = _dict(asset.get("source"))
        items.append(
            {
                "asset_id": asset.get("asset_id"),
                "asset_kind": asset.get("asset_kind"),
                "prompt": asset.get("prompt"),
                "theme": asset.get("theme"),
                "grade": asset.get("grade"),
                "subject": asset.get("subject"),
                "page_title": source.get("page_title"),
                "page_type": source.get("page_type"),
                "layout_hint": source.get("layout_hint"),
                "content_points": source.get("content_points"),
            }
        )

    system = (
        "你是教育 PPT 的 AI 图片素材复用关键词构建器。"
        "你的任务是根据图片生成 prompt 和 plan 中的课程上下文判断素材复用范围，"
        "抽取可用于素材复用匹配的关键词，并生成上下文总结。"
        "只输出 JSON，不要输出 Markdown 或解释。\n\n"
        "要求：\n"
        "1. reuse_scope 必须是 course_specific、subject_generic、visual_generic 三者之一。\n"
        "2. course_specific：素材依赖具体作品、作者、人物关系、课文情节或课程专属概念，"
        "例如史铁生肖像、三次看花、母亲病床前与儿子对话。\n"
        "3. subject_generic：素材是学科内通用教学图，不依赖具体课文，"
        "例如汉字拼音标注、词语释义教学示意、易错读音标注。\n"
        "4. visual_generic：素材主要是通用视觉主体或氛围，可跨学科复用，"
        "例如秋日菊花特写、银杏叶纹理背景。\n"
        "5. specificity_score 是 1-5 的整数，越高越专属；course_specific 通常为 4-5，"
        "subject_generic 通常为 2-3，visual_generic 通常为 1-2。\n"
        "6. core_keywords 只放图像主体、人物、地点、物体、动作、教学概念等高区分度词。"
        "不要放年级、学科、年级+学科组合词。\n"
        "7. context_keywords 只在素材确实依赖课程时放课题、作品名、作者、章节线索；"
        "通用教学图和通用视觉图必须尽量为空。不要把“七年级”“语文”“七年级语文”放入 context_keywords。\n"
        "8. style_keywords 只放低权重的画风、色调、构图、氛围词。"
        "不要输出“插画、编辑感、风格、简洁、清晰、高清、背景”等没有区分度的单独词。\n"
        "9. normalized_prompt 是去掉通用风格噪声后的短语化描述，最多 80 个中文字符。\n"
        "10. context_summary 必须根据 theme、page_title、page_type、content_points 和 prompt 总结图片在课程中的用途，"
        "最多 80 个中文字符；通用素材也要说明其通用教学用途。\n"
        "11. 每个关键词应是短词或短语，优先中文，保留专名；数组内按匹配重要性从高到低排序。\n\n"
        "输出格式严格为：\n"
        "{\"assets\":[{\"asset_id\":\"...\",\"normalized_prompt\":\"...\","
        "\"context_summary\":\"...\","
        "\"reuse_scope\":\"subject_generic\",\"specificity_score\":2,"
        "\"core_keywords\":[\"...\"],\"context_keywords\":[\"...\"],"
        "\"style_keywords\":[\"...\"]}]}"
    )
    user = (
        "请为以下素材构建复用匹配关键词：\n"
        + json.dumps({"assets": items}, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _load_json_response(raw: Any) -> dict[str, Any] | list[Any]:
    text = _strip_fences(str(raw or ""))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import json_repair

            repaired = json_repair.loads(text)
        except Exception:
            raise
        if not isinstance(repaired, (dict, list)):
            raise ValueError("keyword LLM response is not a JSON object or array")
        return repaired


def _strip_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _keyword_payload_by_asset_id(response: dict[str, Any] | list[Any]) -> dict[str, dict[str, Any]]:
    if isinstance(response, dict):
        items = response.get("assets")
        if items is None:
            items = response.get("keywords")
    else:
        items = response
    if not isinstance(items, list):
        raise ValueError("keyword LLM response must contain an assets array")

    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = _clean_text(item.get("asset_id"))
        if asset_id:
            by_id[asset_id] = item
    return by_id


def _apply_keyword_payload(asset: dict[str, Any], payload: dict[str, Any]) -> None:
    normalized_prompt = _clean_text(payload.get("normalized_prompt")) or _clean_text(asset.get("prompt"))
    context_summary = _clean_text(payload.get("context_summary")) or _fallback_context_summary(asset)
    reuse_scope = _clean_reuse_scope(payload.get("reuse_scope"))
    specificity_score = _specificity_score(payload.get("specificity_score"))
    context_exclusions = _context_exclusions(asset)
    core_keywords = _keyword_list(
        payload.get("core_keywords", payload.get("prompt_keywords", payload.get("content_keywords"))),
        max_items=12,
        exclude=context_exclusions | _GENERIC_CORE_NOISE,
    )
    context_keywords = _keyword_list(
        payload.get("context_keywords", payload.get("theme_keywords")),
        max_items=8,
        exclude=context_exclusions,
    )
    if reuse_scope != "course_specific":
        context_keywords = []
    style_keywords = _keyword_list(
        payload.get("style_keywords"),
        max_items=10,
        exclude=context_exclusions | _GENERIC_STYLE_NOISE,
    )

    asset["normalized_prompt"] = normalized_prompt
    asset["context_summary"] = context_summary
    asset["reuse_scope"] = reuse_scope
    asset["specificity_score"] = specificity_score
    asset["core_keywords"] = core_keywords
    asset["context_keywords"] = context_keywords
    asset["style_keywords"] = style_keywords
    asset["match_text"] = _build_match_text(asset)
    asset["match_key"] = _build_match_key(asset)


def _fallback_context_summary(asset: dict[str, Any]) -> str:
    source = _dict(asset.get("source"))
    summary = _join_texts(
        asset.get("theme"),
        source.get("page_title"),
        source.get("page_type"),
        asset.get("prompt"),
    )
    return summary[:120]


def _clean_reuse_scope(value: Any) -> str:
    scope = _clean_text(value)
    return scope if scope in _REUSE_SCOPES else "visual_generic"


def _specificity_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, score))


def _context_exclusions(asset: dict[str, Any]) -> set[str]:
    grade = _clean_text(asset.get("grade"))
    subject = _clean_text(asset.get("subject"))
    exclusions = {grade, subject}
    if grade and subject:
        exclusions.add(f"{grade}{subject}")
        exclusions.add(f"{grade} {subject}")
    return {item for item in exclusions if item}


def _keyword_list(value: Any, *, max_items: int, exclude: set[str] | None = None) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = re.split(r"[,，、;；\n]+", value)
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = []

    terms: list[str] = []
    seen: set[str] = set()
    excluded = exclude or set()
    for item in raw_items:
        term = _clean_keyword(item)
        if not term or term in seen or _is_excluded_keyword(term, excluded):
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_items:
            break
    return terms


def _is_excluded_keyword(term: str, excluded: set[str]) -> bool:
    normalized = term.replace(" ", "")
    for value in excluded:
        blocked = value.replace(" ", "")
        if normalized == blocked:
            return True
        if len(blocked) >= 4 and blocked in normalized:
            return True
    return False


def _clean_keyword(value: Any) -> str:
    text = _clean_text(value)
    text = re.sub(r"^[,，、;；:：。.!！?？\"'“”‘’\s]+", "", text)
    text = re.sub(r"[,，、;；:：。.!！?？\"'“”‘’\s]+$", "", text)
    return text[:40]


def _build_match_text(asset: dict[str, Any]) -> str:
    terms = _dedupe_terms(
        [
            *_keyword_list(asset.get("core_keywords"), max_items=16),
            *_keyword_list(asset.get("context_keywords"), max_items=12),
            *_keyword_list(asset.get("style_keywords"), max_items=12),
            _clean_text(asset.get("subject")),
            _clean_text(asset.get("grade")),
        ]
    )
    return " ".join(terms)


def _build_match_key(asset: dict[str, Any]) -> str:
    reuse_scope = _clean_reuse_scope(asset.get("reuse_scope"))
    core_keywords = _keyword_list(asset.get("core_keywords"), max_items=10)
    context_keywords = _keyword_list(asset.get("context_keywords"), max_items=6)
    subject = _clean_text(asset.get("subject"))
    grade = _clean_text(asset.get("grade"))

    values: list[str]
    if reuse_scope == "course_specific":
        values = [*core_keywords, *context_keywords, subject, grade]
    elif reuse_scope == "subject_generic":
        values = [*core_keywords, subject]
    else:
        values = core_keywords

    terms = _dedupe_terms(values)
    return "|".join(terms[:12])


def _dedupe_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        term = _clean_keyword(value)
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _client_model_name(client: Any) -> str:
    return _clean_text(getattr(client, "_model", "")) or _clean_text(getattr(client, "model", ""))


def _build_reuse_target_asset(
    *,
    asset_kind: str,
    prompt: str,
    theme: str,
    grade: str,
    subject: str,
    page_title: str,
    role: str,
    aspect_ratio: str,
) -> dict[str, Any]:
    asset_key = "|".join([asset_kind, prompt, theme, grade, subject, page_title, role, aspect_ratio])
    return {
        "asset_id": "target_" + hashlib.sha256(asset_key.encode("utf-8")).hexdigest()[:16],
        "asset_kind": asset_kind,
        "image_path": "",
        "role": role,
        "aspect_ratio": aspect_ratio,
        "prompt": _clean_text(prompt),
        "theme": _clean_text(theme),
        "grade": _clean_text(grade),
        "subject": _clean_text(subject),
        "source": {
            "session_id": "",
            "plan_path": "",
            "prompt_path": "",
            "page_number": None,
            "page_title": _clean_text(page_title),
            "image_index": None,
        },
    }


def _rank_reuse_candidates(
    target: dict[str, Any],
    assets: list[Any],
    *,
    library_root: Path,
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        image_path = _resolve_asset_image_path(library_root, item.get("image_path"))
        if image_path is None or not image_path.exists():
            continue
        score_details = _score_reuse_candidate_details(target, item)
        score = float(score_details.get("score") or 0.0)
        if score <= 0:
            continue
        scored.append(
            {
                "asset": item,
                "library_image_path": image_path,
                "keyword_score": round(score, 4),
                "score_details": _debug_score_details(score_details),
            }
        )
    scored.sort(key=lambda item: item["keyword_score"], reverse=True)
    return scored[: max(1, int(limit or DEFAULT_REUSE_CANDIDATE_LIMIT))]


def _score_reuse_candidate(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    return float(_score_reuse_candidate_details(target, candidate).get("score", 0.0))


def _score_reuse_candidate_details(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if _clean_text(target.get("asset_kind")) != _clean_text(candidate.get("asset_kind")):
        return {"score": 0.0, "reject_reason": "asset_kind_mismatch"}

    target_scope = _clean_reuse_scope(target.get("reuse_scope"))
    candidate_scope = _clean_reuse_scope(candidate.get("reuse_scope"))
    target_subject = _clean_text(target.get("subject"))
    candidate_subject = _clean_text(candidate.get("subject"))

    if candidate_scope == "subject_generic" and target_subject and candidate_subject and target_subject != candidate_subject:
        return {"score": 0.0, "reject_reason": "subject_generic_subject_mismatch"}

    target_core = _keyword_list(target.get("core_keywords"), max_items=16)
    candidate_core = _keyword_list(candidate.get("core_keywords"), max_items=16)
    target_style = _keyword_list(target.get("style_keywords"), max_items=12)
    candidate_style = _keyword_list(candidate.get("style_keywords"), max_items=12)

    core_score, core_hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values(target_core),
        _bm25_tokens_from_values(candidate_core),
    )
    if core_score <= 0:
        return {
            "score": 0.0,
            "reject_reason": "no_core_keyword_bm25_match",
            "target_core_keywords": target_core,
            "candidate_core_keywords": candidate_core,
        }

    scope_score = _reuse_scope_score(target_scope, candidate_scope)
    role_aspect_score = (_role_score(target, candidate) + _aspect_ratio_score(target, candidate)) / 2
    style_score, style_hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values(target_style),
        _bm25_tokens_from_values(candidate_style),
    )
    prompt_score, prompt_hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values([target.get("prompt"), target.get("normalized_prompt")]),
        _bm25_tokens_from_values([candidate.get("prompt"), candidate.get("normalized_prompt")]),
    )
    context_score, context_hits = _bm25_similarity_with_hits(
        _bm25_tokens_from_values([_asset_context_text(target)]),
        _bm25_tokens_from_values([_asset_context_text(candidate)]),
    )

    keyword_score = (
        CORE_KEYWORD_WEIGHT * core_score
        + SCOPE_KEYWORD_WEIGHT * scope_score
        + ROLE_ASPECT_KEYWORD_WEIGHT * role_aspect_score
        + STYLE_KEYWORD_WEIGHT * style_score
    )
    score = (
        KEYWORD_REUSE_WEIGHT * keyword_score
        + PROMPT_REUSE_WEIGHT * prompt_score
        + CONTEXT_REUSE_WEIGHT * context_score
    )
    return {
        "score": max(0.0, min(1.0, score)),
        "reject_reason": "",
        "keyword_score": max(0.0, min(1.0, keyword_score)),
        "core_score": core_score,
        "core_hits": core_hits,
        "scope_score": scope_score,
        "role_aspect_score": role_aspect_score,
        "style_score": style_score,
        "style_hits": style_hits,
        "prompt_score": prompt_score,
        "prompt_hits": prompt_hits,
        "context_score": context_score,
        "context_hits": context_hits,
        "target_core_keywords": target_core,
        "candidate_core_keywords": candidate_core,
        "target_style_keywords": target_style,
        "candidate_style_keywords": candidate_style,
    }


def _debug_score_details(details: dict[str, Any]) -> dict[str, Any]:
    score = float(details.get("score") or 0.0)
    return {
        "score": round(score, 4),
        "reject_reason": _clean_text(details.get("reject_reason")),
        "keyword_score": round(float(details.get("keyword_score") or 0.0), 4),
        "core_score": round(float(details.get("core_score") or 0.0), 4),
        "core_hits": details.get("core_hits") or [],
        "scope_score": round(float(details.get("scope_score") or 0.0), 4),
        "role_aspect_score": round(float(details.get("role_aspect_score") or 0.0), 4),
        "style_score": round(float(details.get("style_score") or 0.0), 4),
        "style_hits": details.get("style_hits") or [],
        "prompt_score": round(float(details.get("prompt_score") or 0.0), 4),
        "prompt_hits": details.get("prompt_hits") or [],
        "context_score": round(float(details.get("context_score") or 0.0), 4),
        "context_hits": details.get("context_hits") or [],
        "target_core_keywords": details.get("target_core_keywords") or [],
        "candidate_core_keywords": details.get("candidate_core_keywords") or [],
        "target_style_keywords": details.get("target_style_keywords") or [],
        "candidate_style_keywords": details.get("candidate_style_keywords") or [],
    }


def _asset_context_text(asset: dict[str, Any]) -> str:
    source = _dict(asset.get("source"))
    return _join_texts(
        asset.get("context_summary"),
        asset.get("context_keywords"),
        source.get("page_title"),
        source.get("page_type"),
        source.get("content_points"),
        asset.get("theme"),
    )


def _bm25_tokens_from_values(values: list[Any]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            tokens.extend(_bm25_tokens_from_values(list(value)))
            continue
        text = _clean_text(value)
        if not text:
            continue
        lowered = text.casefold()
        tokens.append(lowered)
        for part in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", lowered):
            tokens.append(part)
            if re.fullmatch(r"[\u4e00-\u9fff]+", part):
                max_n = min(4, len(part))
                for n in range(2, max_n + 1):
                    for idx in range(0, len(part) - n + 1):
                        tokens.append(part[idx:idx + n])
            elif len(part) > 3:
                for sub in re.split(r"[_\-\s]+", part):
                    if sub:
                        tokens.append(sub)
    return _dedupe_terms(tokens)


def _bm25_similarity_with_hits(query_tokens: list[str], doc_tokens: list[str]) -> tuple[float, list[dict[str, str]]]:
    query = [token for token in query_tokens if token]
    doc = [token for token in doc_tokens if token]
    if not query or not doc:
        return 0.0, []

    score = _bm25_score(query, doc, [doc, query])
    self_score = _bm25_score(query, query, [doc, query])
    normalized = 0.0 if self_score <= 0 else min(1.0, score / self_score)
    doc_terms = set(doc)
    hits = [{"target": token, "candidate": token} for token in _dedupe_terms(query) if token in doc_terms]
    return normalized, hits[:24]


def _bm25_score(query_tokens: list[str], doc_tokens: list[str], corpus_docs: list[list[str]]) -> float:
    if not query_tokens or not doc_tokens or not corpus_docs:
        return 0.0
    k1 = 1.5
    b = 0.75
    doc_len = len(doc_tokens)
    avgdl = sum(len(doc) for doc in corpus_docs) / max(1, len(corpus_docs))
    frequencies: dict[str, int] = {}
    for token in doc_tokens:
        frequencies[token] = frequencies.get(token, 0) + 1
    score = 0.0
    for token in _dedupe_terms(query_tokens):
        freq = frequencies.get(token, 0)
        if freq <= 0:
            continue
        containing_docs = sum(1 for doc in corpus_docs if token in set(doc))
        idf = math.log(1 + (len(corpus_docs) - containing_docs + 0.5) / (containing_docs + 0.5))
        denom = freq + k1 * (1 - b + b * doc_len / max(avgdl, 1e-9))
        score += idf * (freq * (k1 + 1)) / max(denom, 1e-9)
    return score


def _overlap_score(target_terms: list[str], candidate_terms: list[str]) -> float:
    score, _hits = _overlap_score_with_hits(target_terms, candidate_terms)
    return score


def _overlap_score_with_hits(
    target_terms: list[str],
    candidate_terms: list[str],
) -> tuple[float, list[dict[str, str]]]:
    if not target_terms or not candidate_terms:
        return 0.0, []
    hits: list[dict[str, str]] = []
    for target in target_terms:
        matched = next((candidate for candidate in candidate_terms if _terms_match(target, candidate)), "")
        if matched:
            hits.append({"target": target, "candidate": matched})
    return len(hits) / len(target_terms), hits


def _terms_match(left: str, right: str) -> bool:
    left = _clean_keyword(left)
    right = _clean_keyword(right)
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 2 and (left in right or right in left)


def _reuse_scope_score(target_scope: str, candidate_scope: str) -> float:
    if target_scope == candidate_scope:
        return 1.0
    if target_scope == "course_specific" and candidate_scope == "visual_generic":
        return 0.65
    if target_scope == "course_specific" and candidate_scope == "subject_generic":
        return 0.45
    if target_scope == "subject_generic" and candidate_scope == "visual_generic":
        return 0.25
    if target_scope == "visual_generic" and candidate_scope == "subject_generic":
        return 0.25
    return 0.0


def _role_score(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    target_role = _asset_role(target)
    candidate_role = _asset_role(candidate)
    if not target_role or not candidate_role:
        return 0.5
    if target_role == candidate_role:
        return 1.0
    if {target_role, candidate_role} <= {"hero", "illustration"}:
        return 0.6
    return 0.0


def _aspect_ratio_score(target: dict[str, Any], candidate: dict[str, Any]) -> float:
    target_ratio = _clean_text(target.get("aspect_ratio"))
    candidate_ratio = _clean_text(candidate.get("aspect_ratio"))
    if not target_ratio or not candidate_ratio:
        return 0.5
    if target_ratio == candidate_ratio:
        return 1.0
    target_orientation = _ratio_orientation(target_ratio)
    candidate_orientation = _ratio_orientation(candidate_ratio)
    return 0.6 if target_orientation and target_orientation == candidate_orientation else 0.2


def _ratio_orientation(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 2:
        return ""
    try:
        width = float(parts[0])
        height = float(parts[1])
    except ValueError:
        return ""
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def _asset_role(asset: dict[str, Any]) -> str:
    role = _clean_text(asset.get("role"))
    if role:
        return role
    image_path = _clean_text(asset.get("image_path"))
    if "background" in image_path:
        return "background"
    if "_hero_" in image_path:
        return "hero"
    if "_illustration_" in image_path:
        return "illustration"
    return ""


def _copy_db_assets_to_library(
    db: dict[str, Any],
    *,
    source_root: Path,
    library_root: Path,
) -> dict[str, Any]:
    copied = deepcopy(db)
    image_dir = library_root / DEFAULT_LIBRARY_IMAGE_DIR
    image_dir.mkdir(parents=True, exist_ok=True)

    copied_assets: list[dict[str, Any]] = []
    warnings = copied.setdefault("warnings", [])
    ingested_at = datetime.now(timezone.utc).isoformat()

    for asset in copied.get("assets", []):
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        source_image_path = _resolve_asset_image_path(source_root, asset.get("image_path"))
        if not asset_id or source_image_path is None or not source_image_path.exists():
            warnings.append(f"library ingest skipped missing image for {asset_id or '<missing asset_id>'}")
            continue

        suffix = source_image_path.suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            suffix = source_image_path.suffix or ".img"
        dest_rel = f"{DEFAULT_LIBRARY_IMAGE_DIR}/{asset_id}{suffix}"
        dest_path = library_root / dest_rel
        shutil.copy2(source_image_path, dest_path)

        original_rel = _relative_path(source_image_path, source_root)
        asset["image_path"] = dest_rel
        source = dict(_dict(asset.get("source")))
        source.setdefault("source_output_root", str(source_root))
        source.setdefault("source_image_path", original_rel)
        asset["source"] = source
        asset["library"] = {
            "ingested_at": ingested_at,
            "source_output_root": str(source_root),
            "source_image_path": original_rel,
        }
        copied_assets.append(asset)

    copied["output_root"] = str(library_root)
    copied["assets"] = copied_assets
    copied["asset_count"] = len(copied_assets)
    return copied


def _read_existing_db(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "warnings": [f"existing library DB could not be read: {path}"],
        }
    return data if isinstance(data, dict) else {"warnings": [f"existing library DB is not an object: {path}"]}


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_reused_image_paths(session_dir: Path) -> set[str]:
    manifest = _read_json_if_exists(session_dir / "materials" / REUSE_MANIFEST_FILENAME)
    entries = manifest.get("reused_assets")
    if not isinstance(entries, list):
        return set()
    paths: set[str] = set()
    for item in entries:
        image_path = _clean_text(_dict(item).get("image_path"))
        if image_path:
            paths.add(image_path.replace("\\", "/"))
    return paths


def _is_reused_image_path(image_path: Path, session_dir: Path, reused_image_paths: set[str] | None) -> bool:
    if not reused_image_paths:
        return False
    try:
        rel_path = image_path.resolve().relative_to(session_dir.resolve()).as_posix()
    except ValueError:
        rel_path = image_path.as_posix()
    return rel_path in reused_image_paths


def _merge_asset_library_db(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    library_root: Path,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    by_id: dict[str, dict[str, Any]] = {}

    for asset in existing.get("assets", []):
        if isinstance(asset, dict):
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id:
                by_id[asset_id] = asset

    for asset in incoming.get("assets", []):
        if isinstance(asset, dict):
            asset_id = _clean_text(asset.get("asset_id"))
            if asset_id:
                by_id[asset_id] = asset

    assets = sorted(
        by_id.values(),
        key=lambda item: (
            str(_dict(item.get("source")).get("session_id", "")),
            _dict(item.get("source")).get("page_number") or 0,
            str(item.get("asset_id", "")),
        ),
    )
    schema_version = max(
        int(existing.get("schema_version") or 0),
        int(incoming.get("schema_version") or 0),
        SCHEMA_VERSION,
    )
    merged: dict[str, Any] = {
        "schema_version": schema_version,
        "built_at": existing.get("built_at") or incoming.get("built_at") or now,
        "updated_at": now,
        "output_root": str(library_root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": _dedupe_warnings(
            [
                *(_as_string_list(existing.get("warnings"))),
                *(_as_string_list(incoming.get("warnings"))),
            ]
        ),
    }
    keyword_built_at = incoming.get("keyword_built_at") or existing.get("keyword_built_at")
    keyword_builder = incoming.get("keyword_builder") or existing.get("keyword_builder")
    if keyword_built_at:
        merged["keyword_built_at"] = keyword_built_at
    if keyword_builder:
        merged["keyword_builder"] = keyword_builder
    return merged


def _resolve_asset_image_path(root: Path, image_path: Any) -> Path | None:
    text = _clean_text(image_path)
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else root / path


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _dedupe_warnings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    warnings: list[str] = []
    for value in values:
        warning = _clean_text(value)
        if not warning or warning in seen:
            continue
        seen.add(warning)
        warnings.append(warning)
    return warnings


def infer_grade(*texts: Any) -> str:
    """Infer grade from topic/audience/template text, returning an empty string if unknown."""

    combined = _join_texts(*texts)
    match = _GRADE_RE.search(combined)
    if match:
        return _normalize_grade(match.group(1))
    if "低年级" in combined:
        return "低年级"
    if "高年级" in combined:
        return "高年级"
    if "小学" in combined:
        return "小学"
    if "初中" in combined:
        return "初中"
    if "高中" in combined:
        return "高中"
    return ""


def infer_grade_band(*texts: Any) -> str:
    """Map concrete grades to the reuse bands used by image matching."""

    combined = _normalize_grade(_join_texts(*texts))
    if not combined:
        return ""
    if "低年级" in combined:
        return "低年级"
    if any(term in combined for term in ("高年级", "初中", "高中", "初一", "初二", "初三", "高一", "高二", "高三")):
        return "高年级"

    match = re.search(r"([一二三四五六七八九十0-9]+)年级", combined)
    if not match:
        return ""
    grade_number = _grade_number(match.group(1))
    if grade_number is None:
        return ""
    return "低年级" if grade_number <= 3 else "高年级"


def infer_subject(*texts: Any) -> str:
    """Infer subject from topic/audience/page text, returning an empty string if unknown."""

    combined = _join_texts(*texts).casefold()
    for subject, keywords in _SUBJECT_KEYWORDS:
        if any(keyword.casefold() in combined for keyword in keywords):
            return subject
    return ""


def _iter_session_dirs(root: Path):
    if (root / "plan.json").exists():
        yield root
        return
    if not root.exists():
        return
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_dir() and (child / "plan.json").exists():
            yield child


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_context(plan: dict[str, Any]) -> dict[str, str]:
    meta = _dict(plan.get("meta"))
    routing = _dict(plan.get("style_routing"))
    topic = _clean_text(meta.get("topic"))
    audience = _clean_text(meta.get("audience"))
    purpose = _clean_text(meta.get("purpose"))
    style_direction = _clean_text(meta.get("style_direction"))
    template_family = _clean_text(routing.get("template_family"))
    style_name = _clean_text(routing.get("style_name"))

    grade = _clean_text(meta.get("grade")) or infer_grade(
        topic,
        audience,
        template_family,
        style_name,
    )
    subject = _clean_text(meta.get("subject")) or infer_subject(
        topic,
        audience,
        purpose,
        style_direction,
    )

    return {
        "theme": topic,
        "grade": grade,
        "subject": subject,
    }


def _build_background_asset(
    *,
    root: Path,
    session_dir: Path,
    plan_path: Path,
    materials_dir: Path,
    context: dict[str, str],
    plan: dict[str, Any],
    reused_image_paths: set[str] | None = None,
) -> dict[str, Any] | None:
    visual = _dict(plan.get("visual"))
    prompt = _clean_text(visual.get("background_prompt"))
    image_path = materials_dir / "background.png"
    if not prompt or not image_path.exists():
        return None
    if _is_reused_image_path(image_path, session_dir, reused_image_paths):
        return None

    return _make_asset(
        root=root,
        session_dir=session_dir,
        plan_path=plan_path,
        image_path=image_path,
        prompt=prompt,
        context=context,
        asset_kind="background",
        prompt_path="visual.background_prompt",
        page_number=None,
        page_title="",
        image_index=None,
        role="background",
        aspect_ratio="16:9",
    )


def _iter_page_image_assets(
    *,
    root: Path,
    session_dir: Path,
    plan_path: Path,
    materials_dir: Path,
    context: dict[str, str],
    page: dict[str, Any],
    page_index: int,
    reused_image_paths: set[str] | None = None,
):
    needs = _dict(page.get("material_needs"))
    images = needs.get("images")
    if not isinstance(images, list):
        return

    page_number = _as_int(page.get("page_number"))
    if page_number is None:
        return

    role_counts: dict[str, int] = {}
    for image_index, image_need in enumerate(images):
        if not isinstance(image_need, dict):
            continue
        if image_need.get("source") != "ai_generate":
            continue

        role = _clean_text(image_need.get("role")) or "illustration"
        role_counts[role] = role_counts.get(role, 0) + 1
        prompt = _clean_text(image_need.get("query"))
        if not prompt:
            continue

        image_path = _find_page_image_path(materials_dir, page_number, role, role_counts[role])
        if image_path is None:
            continue
        if _is_reused_image_path(image_path, session_dir, reused_image_paths):
            continue

        yield _make_asset(
            root=root,
            session_dir=session_dir,
            plan_path=plan_path,
            image_path=image_path,
            prompt=prompt,
            context=context,
            asset_kind="page_image",
            prompt_path=f"pages[{page_index}].material_needs.images[{image_index}].query",
            page_number=page_number,
            page_title=_clean_text(page.get("title")),
            image_index=image_index + 1,
            role=role,
            aspect_ratio=_clean_text(image_need.get("aspect_ratio")),
            page_type=_clean_text(page.get("page_type")),
            layout_hint=_clean_text(page.get("layout_hint")),
            content_points=page.get("content_points"),
        )


def _find_page_image_path(materials_dir: Path, page_number: int, role: str, occurrence: int) -> Path | None:
    stem = f"page_{page_number:02d}_{role}_{occurrence}"
    for suffix in _IMAGE_SUFFIXES:
        path = materials_dir / f"{stem}{suffix}"
        if path.exists():
            return path
    matches = sorted(materials_dir.glob(f"{stem}.*"))
    return matches[0] if matches else None


def _make_asset(
    *,
    root: Path,
    session_dir: Path,
    plan_path: Path,
    image_path: Path,
    prompt: str,
    context: dict[str, str],
    asset_kind: str,
    prompt_path: str,
    page_number: int | None,
    page_title: str,
    image_index: int | None,
    role: str = "",
    aspect_ratio: str = "",
    page_type: str = "",
    layout_hint: str = "",
    content_points: Any = None,
) -> dict[str, Any]:
    rel_image_path = _relative_path(image_path, root)
    rel_plan_path = _relative_path(plan_path, root)
    source = {
        "session_id": session_dir.name,
        "plan_path": rel_plan_path,
        "prompt_path": prompt_path,
        "page_number": page_number,
        "page_title": page_title,
        "image_index": image_index,
    }
    if page_type:
        source["page_type"] = page_type
    if layout_hint:
        source["layout_hint"] = layout_hint
    if isinstance(content_points, list):
        source["content_points"] = [_clean_text(item) for item in content_points if _clean_text(item)]
    asset_key = "|".join(
        [
            session_dir.name,
            asset_kind,
            rel_image_path,
            prompt,
            context.get("theme", ""),
            context.get("grade", ""),
            context.get("subject", ""),
        ]
    )
    return {
        "asset_id": "aiimg_" + hashlib.sha256(asset_key.encode("utf-8")).hexdigest()[:20],
        "asset_kind": asset_kind,
        "image_path": rel_image_path,
        "role": role,
        "aspect_ratio": aspect_ratio,
        "prompt": prompt,
        "theme": context.get("theme", ""),
        "grade": context.get("grade", ""),
        "subject": context.get("subject", ""),
        "source": source,
    }


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _join_texts(*texts: Any) -> str:
    return "\n".join(_clean_text(text) for text in texts if _clean_text(text))


def _normalize_grade(value: str) -> str:
    return value.replace("初1", "初一").replace("初2", "初二").replace("初3", "初三").replace(
        "高1", "高一"
    ).replace("高2", "高二").replace("高3", "高三")


def _grade_number(value: str) -> int | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)
    mapping = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if cleaned in mapping:
        return mapping[cleaned]
    if "十" not in cleaned:
        return None
    left, right = cleaned.split("十", 1)
    tens = mapping.get(left, 1) if left else 1
    ones = mapping.get(right, 0) if right else 0
    return tens * 10 + ones
