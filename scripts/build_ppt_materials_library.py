# -*- coding: utf-8 -*-
"""Build a standalone reusable image library from teach-kb PPTX files.

This is intentionally kept outside ``edupptx/materials/ai_image_asset_db.py``.
It writes a separate library directory, defaulting to ``materials_library_ppt``,
and persists only the split reuse metadata files.

Typical usage:

    python scripts/build_ppt_materials_library.py ^
      --teach-kb-root "D:\\朱司琪\\实习\\teach-kb\\data\\uploads\\pptx" ^
      --library-dir materials_library_ppt ^
      --limit 10

Use ``--skip-vlm`` for a dry structural extraction without Doubao VLM metadata.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import mimetypes
import posixpath
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.config import Config
from edupptx.llm_client import create_llm_client, create_vlm_client
from edupptx.materials.ai_image_asset_db import (
    DEFAULT_EMBEDDING_INDEX_FILENAME,
    DEFAULT_EMBEDDING_META_FILENAME,
    DEFAULT_KEYWORD_BATCH_SIZE,
    DEFAULT_MATCH_INDEX_FILENAME,
    extract_topic_refs,
    normalize_grade_info,
    read_ai_image_split_match_index,
    resolve_meta_grade_subject,
    write_ai_image_match_index,
)
from edupptx.materials.ppt_dedupe import dedupe_ppt_db_assets
from edupptx.materials.strict_reuse_classifier import (
    SECONDARY_REUSE_GROUP_FIELD,
    normalize_secondary_reuse_group,
    normalize_strict_reuse_group,
)

PPT_LIBRARY_SCHEMA_VERSION = 10
EXTRACTOR_VERSION = "ppt_materials_library.v10"
DEFAULT_LIBRARY_DIR = Path("materials_library_ppt")
DEFAULT_IMAGE_DIR = "pptx_images"
DEFAULT_ORIGINAL_IMAGE_DIR = "pptx_images_original"
DEFAULT_SKIP_IMAGE_DIR = "skip_images"
DEFAULT_PPT_KEYWORD_BATCH_SIZE = 1
DEFAULT_PPT_VLM_WORKERS = 15
DEFAULT_PPT_LLM_WORKERS = 15
PPT_ASPECT_MAX_LOSS = 0.50
PPT_ASPECT_RATIO_PAIRS = {
    "1:1": (1, 1),
    "3:4": (3, 4),
    "4:3": (4, 3),
    "16:9": (16, 9),
    "9:16": (9, 16),
}
DEFAULT_MIN_SOURCE_WIDTH = 160
DEFAULT_MIN_SOURCE_HEIGHT = 120
DEFAULT_MIN_DISPLAY_WIDTH = 120.0
DEFAULT_MIN_DISPLAY_HEIGHT = 96.0
FULL_BACKGROUND_AREA_RATIO = 0.55
FULL_BACKGROUND_MIN_WIDTH = 1120.0
FULL_BACKGROUND_MIN_HEIGHT = 430.0
REPEATED_WIDE_WIDTH_RATIO = 0.78
REPEATED_WIDE_MIN_HEIGHT = 72.0
REPEATED_WIDE_MIN_USES = 2
DECORATIVE_SMALL_AREA_RATIO = 0.03
DECORATIVE_WIDE_RATIO = 2.35
DECORATIVE_WIDE_MAX_HEIGHT = 190.0
CANVAS_W = 1280.0
CANVAS_H = 720.0
CANVAS_AREA = CANVAS_W * CANVAS_H
PPT_NEAR_DUPLICATE_CATEGORIES = {"character_action", "symbolic_material"}
PPT_NEAR_DUPLICATE_PHASH_MAX_DISTANCE = 18
PPT_INTERNAL_ASSET_KEYS = (
    "_pptx_path",
    "_ppt_usage",
    "_ppt_source_media_path",
    "_ppt_source_width",
    "_ppt_source_height",
    "_ppt_source_pixels",
    "_ppt_display_width",
    "_ppt_display_height",
    "_ppt_display_pixels",
    "_ppt_display_area_ratio",
    "_ppt_perceptual_hash",
)

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

PPT_VLM_SYSTEM_PROMPT = """你是教学课件素材库的图片标注助手。给定一张从 PPT 中提取的图片以及它所在课件、页码和前后页文本，你只输出四个字段。

输出 JSON 结构：
{
  "query": "重新生成该图的中文 prompt（见下方契约）",
  "context_summary": "一句 20-40 个汉字的短句，描述画面内容和页面功能",
  "teaching_intent": "该图服务的教学动作或学习目标",
  "is_backdrop": true
}

is_backdrop 判定（与画幅是否满铺无关，只看画面内容）：
- true：画面是可在其上叠加文字/卡片的版式/底图/氛围背景——含命名地标/主题场景作氛围底图。
- false：画面本身就是要传达的内容——含大段正文/题干/题目载荷、人物动作叙事、或作主标识的可读标题。

query 契约——写“画面是什么”，不写“用在哪”：
1. 上下文锚定身份才安专名（B1）：当页面/课件上下文明确指向具名人物、地标、作品或实物，
   且图像形态支持该类身份（肖像/人物照片/可辨地标场景/带可读题名标签的实物）时，
   即使 VLM 无法从像素独立识别具体身份，也不要把 canonical query 改成匿名类别；
   C01 canonical query 保留专有身份，C03 projection 再通过 secondary_reuse_query/secondary_reuse_caption 去名泛化。
   当图像是泛化匿名呈现（背影、泛人群、泛山泛水、无个体特征的群体），且上下文也无法锚定到明确肖像/地标/作品时，
   只写图像可证的通用内容（如“科考队徒步背影”）。
2. 真实自然粒度，不阉割也不臆造（B2）：如实写主体的自然类别名（青铜簋就写“青铜簋”、瀑布就写“瀑布”），
   既不许泛化成“一种器物”丢信息，也不许把类型升格成唯一专名（除非图/图内文字确证）。
3. 保留教学原子值：画面里可读的汉字/拼音/数字/公式/标签及其数量、顺序、对应、因果、空间、比较关系，
   绝不省略（这些是后续分类依据）。
4. 删除法医式装饰：服饰、发型、无关小物件、构图等与身份/教学无关的细节不写；收紧到“自然生成意图”的简洁程度。
5. 保留区分性属性（B4）：删某属性前先问“删掉它本图会不会和同主体的另一张图无法区分？会就保留”。
   场景/风景的天气（晴/阴/雨/雪）、时段（晨昏夜）、季节、光照氛围是主体的区分性属性，不是无关装饰，必须保留。
6. 空白脚手架显式标注（B3）：空白脚手架（空坐标系/空网格/空数位表/空田字格/空烧杯线稿）query 必须显式写“空白/不含具体题目文字”，
   且不得把脚手架自带的轴刻度/网格标号当题目数值逐一列出（“x 轴 -4 到 7”是刻度，不是要复现的题目载荷）。
7. 仍禁用途语境：年级、学科、课时、页面功能、课程名、文件名等“用在哪”的来源语境一律不写。

context_summary 保持短句（20-40 汉字），写“画面内容 + 页面功能”，先说主体/动作/关系再说页面功能。
teaching_intent 写教学动作或学习目标，不重复 context_summary，也不重复来源信息。

不要输出上述四个字段之外的任何字段；尤其不要输出 caption、分类、通用复用判断、
关键词、语义别名、查询别名或其他结构外字段。
"""


class _KeywordClientFromVLM:
    """Text-only adapter so PPT extraction can reuse metadata enrichment with a VLM-only config."""

    def __init__(self, vlm_client: Any):
        self._vlm_client = vlm_client
        self._model = _clean_text(getattr(vlm_client, "_model", ""))

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        return self._vlm_client.chat_vlm_json(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_retries: int = 1,
    ) -> str:
        import json as _json

        result = self._vlm_client.chat_vlm_json(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )
        return _json.dumps(result, ensure_ascii=False)


@dataclass
class RawPptImage:
    pptx_path: Path
    slide_no: int
    shape_idx: int
    source_media_path: str
    suffix: str
    data: bytes
    sha256: str
    width: int
    height: int
    mode: str
    bbox: dict[str, Any]
    slide_text: str
    slide_title_guess: str
    is_corrupt: bool = False
    error: str = ""


def _infer_teach_kb_root_from_pptx_dir(pptx_root: Path) -> Path:
    if (
        pptx_root.name == "pptx"
        and pptx_root.parent.name == "uploads"
        and pptx_root.parent.parent.name == "data"
    ):
        return pptx_root.parent.parent.parent
    return pptx_root


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value if value is not None else default))
    except (TypeError, ValueError):
        return max(1, int(default))


def _should_flush(
    *,
    pptx_count: int,
    flush_every_n_ppt: int,
    flush_each_ppt: bool,
    write_match_index: bool,
) -> bool:
    """Whether to rewrite the incremental index after this PPT.

    Gated by the master switches (write_match_index / flush_each_ppt), then
    only fires every ``flush_every_n_ppt`` processed PPTs. The end-of-run write
    always persists the full index, so a larger interval only widens the
    crash-recovery checkpoint, it never loses the tail on normal completion.
    """
    if not (write_match_index and flush_each_ppt):
        return False
    interval = flush_every_n_ppt if flush_every_n_ppt > 0 else 1
    return pptx_count % interval == 0


def build_ppt_image_materials_library(
    *,
    teach_kb_root: str | Path,
    output_library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    db_path: str | Path | None = None,
    pptx_paths: Iterable[str | Path] | None = None,
    limit: int = 0,
    max_assets: int = 0,
    vlm_client: Any | None = None,
    keyword_client: Any | None = None,
    use_vlm: bool = True,
    use_keyword_enrichment: bool = True,
    keep_rejected: bool = False,
    write_match_index: bool = True,
    flush_each_ppt: bool = True,
    flush_every_n_ppt: int = 1,
    env_file: str | Path = ".env",
    vlm_max_side: int = 1280,
    keyword_batch_size: int = DEFAULT_PPT_KEYWORD_BATCH_SIZE,
    vlm_workers: int = DEFAULT_PPT_VLM_WORKERS,
    llm_workers: int = DEFAULT_PPT_LLM_WORKERS,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Extract PPTX images into an isolated library compatible with reuse DB fields.

    This function does not modify the existing AI image asset DB module or the
    default ``materials_library`` directory.
    """

    pptx_root = Path(teach_kb_root).expanduser().resolve()
    teach_root = _infer_teach_kb_root_from_pptx_dir(pptx_root)
    library_root = Path(output_library_dir).expanduser().resolve()
    image_root = library_root / DEFAULT_IMAGE_DIR
    kb_db_path = Path(db_path).expanduser().resolve() if db_path else teach_root / "data" / "db" / "teach_kb.db"
    index_target = library_root / "strict_reuse_indexes"

    library_root.mkdir(parents=True, exist_ok=True)
    image_root.mkdir(parents=True, exist_ok=True)
    keyword_batch_size = _positive_int(keyword_batch_size, DEFAULT_PPT_KEYWORD_BATCH_SIZE)
    vlm_workers = _positive_int(vlm_workers, DEFAULT_PPT_VLM_WORKERS)
    llm_workers = _positive_int(llm_workers, DEFAULT_PPT_LLM_WORKERS)
    flush_every_n_ppt = _positive_int(flush_every_n_ppt, 1)

    config: Config | None = None
    if use_vlm and vlm_client is None:
        config = Config.from_env(env_file)
        if not config.vlm_api_key or not config.vlm_model:
            raise RuntimeError("VLM_APIKEY/VLM_MODEL not configured; pass --skip-vlm for structural extraction")
        vlm_client = create_vlm_client(config)
    if use_keyword_enrichment and keyword_client is None:
        if config is None:
            config = Config.from_env(env_file)
        if config.llm_api_key and config.llm_model:
            keyword_client = create_llm_client(config, web_search=False)
        elif vlm_client is not None:
            keyword_client = _KeywordClientFromVLM(vlm_client)

    split_existing = read_ai_image_split_match_index(library_root)
    existing_db = split_existing[0] if split_existing is not None else _read_json_if_exists(library_root / DEFAULT_MATCH_INDEX_FILENAME)
    assets_by_id = {
        _clean_text(asset.get("asset_id")): dict(asset)
        for asset in existing_db.get("assets", [])
        if isinstance(asset, dict)
        and _clean_text(asset.get("asset_id"))
        and not _is_skip_material_category(asset.get("strict_reuse_group"))
    }

    report: dict[str, Any] = {
        "schema_version": EXTRACTOR_VERSION,
        "teach_kb_root": str(teach_root),
        "pptx_root": str(pptx_root),
        "library_dir": str(library_root),
        "match_index_path": str(index_target),
        "use_vlm": bool(use_vlm),
        "use_keyword_enrichment": bool(use_keyword_enrichment and keyword_client is not None),
        "vlm_workers": vlm_workers if use_vlm else 0,
        "llm_workers": llm_workers if use_keyword_enrichment and keyword_client is not None else 0,
        "keyword_batch_size": keyword_batch_size,
        "pptx_count": 0,
        "raw_picture_count": 0,
        "kept_asset_count": 0,
        "updated_asset_count": 0,
        "skipped_count": 0,
        "warnings": [],
        "processed_pptx": [],
        "failed_pptx": [],
        "failed_pptx_count": 0,
        "skipped": [],
    }

    selected_pptx_paths = [Path(item).expanduser().resolve() for item in (pptx_paths or [])]
    total_assets_before = len(assets_by_id)
    ppt_asset_source_by_id: dict[str, dict[str, Any]] = {}
    iter_pptx_paths = selected_pptx_paths if selected_pptx_paths else None
    if use_keyword_enrichment and keyword_client is None:
        report["warnings"].append("keyword_enrichment_skipped: no LLM or VLM client configured")

    vlm_executor = ThreadPoolExecutor(max_workers=vlm_workers) if use_vlm else None
    llm_executor = (
        ThreadPoolExecutor(max_workers=llm_workers)
        if use_keyword_enrichment and keyword_client is not None
        else None
    )

    def submit_llm_or_store(asset: dict[str, Any], futures: dict[Any, str]) -> None:
        asset_id = _clean_text(asset.get("asset_id"))
        if llm_executor is None:
            if asset_id:
                assets_by_id[asset_id] = asset
            return
        future = llm_executor.submit(
            _enrich_single_ppt_asset_with_llm,
            dict(asset),
            keyword_client,
            batch_size=keyword_batch_size,
        )
        futures[future] = asset_id

    try:
        for pptx_path in _iter_pptx_files(pptx_root, iter_pptx_paths, limit):
            if max_assets and report["kept_asset_count"] >= max_assets:
                break
            if not pptx_path.exists():
                _add_failed_pptx(report, pptx_path, "pptx_missing")
                continue

            try:
                meta = _load_pptx_metadata(kb_db_path, pptx_path, pptx_root)
                markdown_excerpt = _extract_markitdown_excerpt(pptx_path)
                deck_metadata = _resolve_ppt_deck_metadata(
                    meta,
                    pptx_path,
                    markdown_excerpt,
                    keyword_client if use_keyword_enrichment else None,
                )
                meta = {**meta, "deck_metadata": deck_metadata}
                raw_items = _extract_raw_ppt_images(pptx_path)
                wide_repeated = _repeated_wide_hashes(raw_items)
            except Exception as exc:
                _add_failed_pptx(report, pptx_path, f"pptx_process_failed:{type(exc).__name__}: {exc}")
                continue
            pptx_summary = {
                "pptx_path": str(pptx_path),
                "raw_picture_count": len(raw_items),
                "kept": 0,
                "skipped": 0,
            }
            report["pptx_count"] += 1
            report["raw_picture_count"] += len(raw_items)
            vlm_futures: dict[Any, str] = {}
            llm_futures: dict[Any, str] = {}

            for item in raw_items:
                if max_assets and report["kept_asset_count"] >= max_assets:
                    break
                reason = _classify_exclusion(item, wide_repeated)
                if reason:
                    _add_skip(report, _usage_label(item), reason)
                    pptx_summary["skipped"] += 1
                    continue

                asset_id = _asset_id_for_sha(item.sha256)
                image_rel = f"{DEFAULT_IMAGE_DIR}/{asset_id}.png"
                original_image_rel = f"{DEFAULT_ORIGINAL_IMAGE_DIR}/{asset_id}.png"
                image_abs = library_root / image_rel
                original_image_abs = library_root / original_image_rel
                try:
                    image_fields = _save_ppt_image_derivatives(
                        item.data,
                        original_path=original_image_abs,
                        runtime_path=image_abs,
                    )
                except Exception as exc:
                    _add_skip(report, _usage_label(item), f"image_save_failed:{type(exc).__name__}")
                    pptx_summary["skipped"] += 1
                    continue

                ppt_asset_source_by_id[asset_id] = _ppt_asset_source_meta(item)
                if asset_id in assets_by_id:
                    existing_asset = dict(assets_by_id[asset_id])
                    _append_source_pptx_ref(existing_asset, _ppt_source_ref(item, meta))
                    deck_metadata = _ppt_deck_metadata_from_meta(meta)
                    existing_asset["subject"] = deck_metadata["subject"]
                    existing_asset["grade_norm"] = deck_metadata["grade_norm"]
                    existing_asset["grade_band"] = deck_metadata["grade_band"]
                    existing_asset.update(image_fields)
                    existing_asset["image_path"] = image_rel
                    existing_asset["original_image_path"] = original_image_rel
                    stored_backdrop = existing_asset.get("is_backdrop")
                    existing_asset["asset_kind"] = (
                        _resolve_ppt_asset_kind(item, _as_bool(stored_backdrop, default=False))
                        if stored_backdrop is not None
                        else _ppt_asset_kind(item)
                    )
                    if existing_asset["asset_kind"] == "background":
                        existing_asset["asset_category"] = "background"
                        existing_asset["normalized_prompt"] = _clean_text(
                            existing_asset.get("normalized_prompt") or existing_asset.get("content_prompt")
                        )
                    for removed_key in ("aspect_bucket", "role", "padding_capacity"):
                        existing_asset.pop(removed_key, None)
                    submit_llm_or_store(existing_asset, llm_futures)
                    pptx_summary["kept"] += 1
                    report["kept_asset_count"] += 1
                    continue

                context = _build_context(item, meta, markdown_excerpt)
                if vlm_executor is not None:
                    vlm_futures[vlm_executor.submit(
                        _annotate_and_build_ppt_asset,
                        vlm_client=vlm_client,
                        image_path=original_image_abs,
                        asset_id=asset_id,
                        image_rel=image_rel,
                        original_image_rel=original_image_rel,
                        image_fields=image_fields,
                        item=item,
                        meta=meta,
                        context=context,
                        vlm_max_side=vlm_max_side,
                    )] = _usage_label(item)
                else:
                    asset = _build_fallback_ppt_asset(
                        asset_id=asset_id,
                        image_rel=image_rel,
                        original_image_rel=original_image_rel,
                        image_fields=image_fields,
                        item=item,
                        meta=meta,
                        context=context,
                    )
                    submit_llm_or_store(asset, llm_futures)
                pptx_summary["kept"] += 1
                report["kept_asset_count"] += 1

            for future in as_completed(vlm_futures):
                usage = vlm_futures[future]
                try:
                    asset, task_warnings = future.result()
                    report["warnings"].extend(task_warnings)
                    submit_llm_or_store(asset, llm_futures)
                except Exception as exc:
                    report["warnings"].append(f"{usage} VLM pipeline failed: {type(exc).__name__}: {exc}")

            for future in as_completed(llm_futures):
                asset_id = llm_futures[future]
                try:
                    asset, task_warnings = future.result()
                    report["warnings"].extend(task_warnings)
                    if asset_id:
                        assets_by_id[asset_id] = asset
                except Exception as exc:
                    report["warnings"].append(f"{asset_id or 'unknown_asset'} LLM pipeline failed: {type(exc).__name__}: {exc}")

            report["processed_pptx"].append(pptx_summary)
            if _should_flush(
                pptx_count=report["pptx_count"],
                flush_every_n_ppt=flush_every_n_ppt,
                flush_each_ppt=flush_each_ppt,
                write_match_index=write_match_index,
            ):
                try:
                    _write_incremental_match_index(
                        assets_by_id=assets_by_id,
                        library_root=library_root,
                        existing_db=existing_db,
                        teach_root=teach_root,
                        report=report,
                        ppt_asset_source_by_id=ppt_asset_source_by_id,
                    )
                except Exception as exc:
                    report["warnings"].append(f"incremental_match_index_failed:{type(exc).__name__}: {exc}")
    finally:
        if vlm_executor is not None:
            vlm_executor.shutdown(wait=True)
        if llm_executor is not None:
            llm_executor.shutdown(wait=True)

    db = _build_library_db_snapshot(
        assets_by_id=assets_by_id,
        library_root=library_root,
        existing_db=existing_db,
        teach_root=teach_root,
        warnings=report["warnings"],
        include_skip=True,
    )
    if use_keyword_enrichment and keyword_client is not None:
        db["keyword_built_at"] = datetime.now(timezone.utc).isoformat()
        db["keyword_builder"] = {
            "method": PPT_LLM_ENRICHMENT_METHOD,
            "batch_size": keyword_batch_size,
            "workers": llm_workers,
            "model": _clean_text(getattr(keyword_client, "_model", "")),
        }
    skip_archive_report = _archive_ppt_skip_images(
        db,
        library_root=library_root,
        warnings=report["warnings"],
    )
    report["skip_image_archive_count"] = skip_archive_report["archived_count"]
    report["skip_image_archive_missing_count"] = skip_archive_report["missing_count"]
    _attach_ppt_source_metadata(db, ppt_asset_source_by_id)
    dedupe_report = dedupe_ppt_db_assets(
        db,
        library_root=library_root,
        apply=True,
        mode="build_apply",
    )
    report["dedupe_removed_count"] = int(dedupe_report.get("applied_removed_count") or 0)
    report["dedupe_bucket_counts"] = dedupe_report.get("buckets", {})
    report["dedupe_report_path"] = _clean_text(dedupe_report.get("report_path"))
    if dedupe_report.get("groups"):
        report["dedupe_groups"] = dedupe_report["groups"]
    for item in dedupe_report.get("missing_images", []):
        if isinstance(item, dict):
            report["warnings"].append(
                "ppt_dedupe_missing_image:"
                f"{item.get('bucket')}:{item.get('asset_id')}:{item.get('image_path')}"
            )
    _strip_ppt_internal_asset_fields(db)
    db["asset_count"] = len(db.get("assets", []) if isinstance(db.get("assets"), list) else [])
    db["warnings"] = _dedupe(
        [
            *(db.get("warnings", []) if isinstance(db.get("warnings"), list) else []),
            *report["warnings"],
        ]
    )
    report["warnings"] = _dedupe(
        [
            *report["warnings"],
            *db["warnings"],
        ]
    )
    index_path = index_target
    if write_match_index:
        try:
            match_index, index_path = write_ai_image_match_index(
                db,
                library_root,
            )
            embedding_report = match_index.get("embedding_index") if isinstance(match_index, dict) else None
            if isinstance(embedding_report, dict):
                report["warnings"].extend(
                    warning
                    for warning in embedding_report.get("warnings", [])
                    if isinstance(warning, str) and warning
                )
            if not (match_index.get("assets") if isinstance(match_index.get("assets"), list) else []):
                _remove_ppt_embedding_sidecars(library_root, warnings=report["warnings"])
            report["match_index_path"] = str(index_path)
        except Exception as exc:
            report["warnings"].append(f"match_index_failed:{type(exc).__name__}: {exc}")

    final_asset_count = int(db.get("asset_count") or 0)
    report["asset_count"] = final_asset_count
    report["new_asset_count"] = max(0, final_asset_count - total_assets_before)
    report["updated_asset_count"] = final_asset_count - total_assets_before
    report["warning_count"] = len(report["warnings"])
    return db, index_path, report


def _extract_raw_ppt_images(pptx_path: Path) -> list[RawPptImage]:
    with zipfile.ZipFile(str(pptx_path)) as zf:
        names = set(zf.namelist())
        slide_cx, slide_cy = _presentation_size(zf, names)
        slide_paths = sorted(
            [name for name in names if re.search(r"ppt/slides/slide\d+\.xml$", name)],
            key=_slide_no_from_path,
        )
        slide_texts: dict[int, tuple[str, str]] = {}
        raw_items: list[RawPptImage] = []
        for slide_path in slide_paths:
            slide_no = _slide_no_from_path(slide_path)
            root = ET.fromstring(zf.read(slide_path))
            slide_text = _collect_text(root)
            slide_texts[slide_no] = (_guess_slide_title(slide_text), slide_text)
            rels = _read_slide_relationships(zf, slide_path)
            shape_idx = 0
            for pic in root.findall(".//p:pic", NS):
                shape_idx += 1
                media_path = _picture_media_path(pic, rels)
                if not media_path or media_path not in names:
                    continue
                data = zf.read(media_path)
                suffix = Path(media_path).suffix.lower() or ".png"
                info = _inspect_image(data)
                bbox = _parse_bbox(pic, slide_cx, slide_cy)
                title_guess, text = slide_texts.get(slide_no, ("", ""))
                raw_items.append(
                    RawPptImage(
                        pptx_path=pptx_path,
                        slide_no=slide_no,
                        shape_idx=shape_idx,
                        source_media_path=media_path,
                        suffix=suffix,
                        data=data,
                        sha256=hashlib.sha256(data).hexdigest(),
                        width=int(info.get("width") or 0),
                        height=int(info.get("height") or 0),
                        mode=_clean_text(info.get("mode")),
                        bbox=bbox,
                        slide_text=text,
                        slide_title_guess=title_guess,
                        is_corrupt=bool(info.get("is_corrupt")),
                        error=_clean_text(info.get("error")),
                    )
                )
    return raw_items


def _presentation_size(zf: zipfile.ZipFile, names: set[str]) -> tuple[int, int]:
    if "ppt/presentation.xml" not in names:
        return 12192000, 6858000
    root = ET.fromstring(zf.read("ppt/presentation.xml"))
    sld_sz = root.find("p:sldSz", NS)
    if sld_sz is None:
        return 12192000, 6858000
    try:
        return int(sld_sz.attrib.get("cx") or 12192000), int(sld_sz.attrib.get("cy") or 6858000)
    except ValueError:
        return 12192000, 6858000


def _read_slide_relationships(zf: zipfile.ZipFile, slide_path: str) -> dict[str, str]:
    rel_path = slide_path.replace("ppt/slides/", "ppt/slides/_rels/") + ".rels"
    if rel_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rel_path))
    rels: dict[str, str] = {}
    for rel in root:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rid and target:
            rels[rid] = _normalize_rel_target(target)
    return rels


def _picture_media_path(pic: ET.Element, rels: dict[str, str]) -> str:
    blip = pic.find(".//a:blip", NS)
    if blip is None:
        return ""
    rid = blip.attrib.get(_qname("r:embed")) or blip.attrib.get(_qname("r:link")) or ""
    return rels.get(rid, "")


def _normalize_rel_target(target: str) -> str:
    target = target.strip()
    if not target:
        return ""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("ppt/slides", target))


def _parse_bbox(node: ET.Element, slide_cx: int, slide_cy: int) -> dict[str, Any]:
    xfrm = node.find(".//a:xfrm", NS)
    if xfrm is None:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0, "area_ratio": 0.0}
    off = xfrm.find("a:off", NS)
    ext = xfrm.find("a:ext", NS)
    try:
        x = int(off.attrib.get("x") or 0) if off is not None else 0
        y = int(off.attrib.get("y") or 0) if off is not None else 0
        cx = int(ext.attrib.get("cx") or 0) if ext is not None else 0
        cy = int(ext.attrib.get("cy") or 0) if ext is not None else 0
    except ValueError:
        x = y = cx = cy = 0
    scale_x = CANVAS_W / float(slide_cx or 1)
    scale_y = CANVAS_H / float(slide_cy or 1)
    width = cx * scale_x
    height = cy * scale_y
    return {
        "x": round(x * scale_x, 2),
        "y": round(y * scale_y, 2),
        "width": round(width, 2),
        "height": round(height, 2),
        "unit": "canvas_1280x720",
        "area_ratio": round((width * height) / CANVAS_AREA, 4) if CANVAS_AREA else 0.0,
    }


def _collect_text(node: ET.Element) -> str:
    parts: list[str] = []
    for text_node in node.findall(".//a:t", NS):
        text = _clean_text(text_node.text)
        if text:
            parts.append(text)
    return _clean_text(" ".join(parts))


def _guess_slide_title(slide_text: str) -> str:
    if not slide_text:
        return ""
    for sep in ("。", "？", "！", "\n"):
        if sep in slide_text:
            slide_text = slide_text.split(sep, 1)[0]
            break
    return _clean_text(slide_text)[:40]


def _inspect_image(data: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return {
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
                "is_corrupt": False,
            }
    except Exception as exc:
        return {"width": 0, "height": 0, "mode": "", "is_corrupt": True, "error": str(exc)}


def _save_ppt_image_derivatives(
    data: bytes,
    *,
    original_path: Path,
    runtime_path: Path,
) -> dict[str, int | str]:
    original_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data)) as img:
        rgba = img.convert("RGBA")
        aspect_ratio = _ppt_aspect_ratio_name(rgba.width, rgba.height)
        padded = _pad_image_to_ppt_aspect(rgba, aspect_ratio)
        if not original_path.exists():
            rgba.save(original_path, format="PNG", optimize=True)
        if not runtime_path.exists():
            padded.save(runtime_path, format="PNG", optimize=True)
        return {
            "actual_width": rgba.width,
            "actual_height": rgba.height,
            "padded_width": padded.width,
            "padded_height": padded.height,
            "aspect_ratio": aspect_ratio,
        }


def _ppt_aspect_ratio_name(width: int, height: int) -> str:
    bucket, loss = _nearest_ppt_aspect_ratio(width, height)
    return bucket if loss < PPT_ASPECT_MAX_LOSS else "other"


def _nearest_ppt_aspect_ratio(width: int, height: int) -> tuple[str, float]:
    if width <= 0 or height <= 0:
        return "other", float("inf")
    ratio = float(width) / float(height)
    best_bucket = "other"
    best_loss = float("inf")
    for bucket, (target_w, target_h) in PPT_ASPECT_RATIO_PAIRS.items():
        target_ratio = float(target_w) / float(target_h)
        loss = 1.0 - min(ratio, target_ratio) / max(ratio, target_ratio)
        if loss < best_loss:
            best_bucket = bucket
            best_loss = loss
    return best_bucket, best_loss


def _padded_size_for_ppt_aspect(width: int, height: int, aspect_ratio: str) -> tuple[int, int]:
    pair = PPT_ASPECT_RATIO_PAIRS.get(aspect_ratio)
    if not pair:
        return width, height
    target_w, target_h = pair
    k = max(math.ceil(width / target_w), math.ceil(height / target_h))
    return target_w * k, target_h * k


def _pad_image_to_ppt_aspect(img: Image.Image, aspect_ratio: str) -> Image.Image:
    if aspect_ratio == "other":
        return img.copy()
    canvas_width, canvas_height = _padded_size_for_ppt_aspect(img.width, img.height, aspect_ratio)
    if canvas_width == img.width and canvas_height == img.height:
        return img.copy()
    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    left = (canvas_width - img.width) // 2
    top = (canvas_height - img.height) // 2
    canvas.paste(img, (left, top), img)
    return canvas


def _repeated_wide_hashes(items: list[RawPptImage]) -> set[str]:
    counts: dict[str, int] = {}
    for item in items:
        bbox = item.bbox
        if bbox["width"] >= CANVAS_W * REPEATED_WIDE_WIDTH_RATIO and bbox["height"] >= REPEATED_WIDE_MIN_HEIGHT:
            counts[item.sha256] = counts.get(item.sha256, 0) + 1
    return {sha for sha, count in counts.items() if count >= REPEATED_WIDE_MIN_USES}


def _classify_exclusion(item: RawPptImage, wide_repeated: set[str]) -> str:
    bbox = item.bbox
    if item.is_corrupt:
        return "corrupt_image"
    if item.width < DEFAULT_MIN_SOURCE_WIDTH or item.height < DEFAULT_MIN_SOURCE_HEIGHT:
        return "small_source_image"
    if bbox["width"] < DEFAULT_MIN_DISPLAY_WIDTH or bbox["height"] < DEFAULT_MIN_DISPLAY_HEIGHT:
        return "small_display_image"
    if bbox["area_ratio"] < DECORATIVE_SMALL_AREA_RATIO:
        return "small_decoration"
    if item.sha256 in wide_repeated:
        return "repeated_wide_decoration"
    ratio = bbox["width"] / bbox["height"] if bbox["height"] else 0.0
    if ratio >= DECORATIVE_WIDE_RATIO and bbox["height"] <= DECORATIVE_WIDE_MAX_HEIGHT:
        return "wide_banner_decoration"
    return ""


def _is_full_slide_background(item: RawPptImage) -> bool:
    bbox = item.bbox if isinstance(item.bbox, dict) else {}
    return (
        float(bbox.get("area_ratio") or 0.0) >= FULL_BACKGROUND_AREA_RATIO
        and float(bbox.get("width") or 0.0) >= FULL_BACKGROUND_MIN_WIDTH
        and float(bbox.get("height") or 0.0) >= FULL_BACKGROUND_MIN_HEIGHT
    )


def _ppt_asset_kind(item: RawPptImage) -> str:
    return "background" if _is_full_slide_background(item) else "page_image"


def _resolve_ppt_asset_kind(item: RawPptImage, is_backdrop: bool) -> str:
    if not _is_full_slide_background(item):
        return "page_image"
    return "background" if is_backdrop else "page_image"


def _ppt_asset_source_meta(item: RawPptImage) -> dict[str, Any]:
    bbox = item.bbox if isinstance(item.bbox, dict) else {}
    display_width = float(bbox.get("width") or 0.0)
    display_height = float(bbox.get("height") or 0.0)
    return {
        "_pptx_path": str(item.pptx_path.resolve()),
        "_ppt_usage": _usage_label(item),
        "_ppt_source_media_path": item.source_media_path,
        "_ppt_source_width": int(item.width or 0),
        "_ppt_source_height": int(item.height or 0),
        "_ppt_source_pixels": int(item.width or 0) * int(item.height or 0),
        "_ppt_display_width": display_width,
        "_ppt_display_height": display_height,
        "_ppt_display_pixels": display_width * display_height,
        "_ppt_display_area_ratio": float(bbox.get("area_ratio") or 0.0),
    }


def _ppt_source_ref(item: RawPptImage, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "pptx_id": _clean_text(meta.get("id")),
        "period_id": _clean_text(meta.get("period_id")),
        "file_path": _clean_text(meta.get("file_path")) or str(item.pptx_path.resolve()),
        "file_name": _clean_text(meta.get("file_name")) or item.pptx_path.name,
        "absolute_path": str(item.pptx_path.resolve()),
        "slide_no": int(item.slide_no or 0),
        "shape_idx": int(item.shape_idx or 0),
        "source_media_path": _clean_text(item.source_media_path),
        "source": "builder",
    }


def _append_source_pptx_ref(asset: dict[str, Any], ref: dict[str, Any]) -> None:
    refs = asset.get("source_pptx_refs")
    if not isinstance(refs, list):
        refs = []
    key = (
        _clean_text(ref.get("pptx_id")),
        _clean_text(ref.get("file_path")),
        _clean_text(ref.get("absolute_path")),
        _clean_text(ref.get("slide_no")),
        _clean_text(ref.get("shape_idx")),
        _clean_text(ref.get("source_media_path")),
        _clean_text(ref.get("source")),
    )
    for existing in refs:
        if not isinstance(existing, dict):
            continue
        existing_key = (
            _clean_text(existing.get("pptx_id")),
            _clean_text(existing.get("file_path")),
            _clean_text(existing.get("absolute_path")),
            _clean_text(existing.get("slide_no")),
            _clean_text(existing.get("shape_idx")),
            _clean_text(existing.get("source_media_path")),
            _clean_text(existing.get("source")),
        )
        if existing_key == key:
            asset["source_pptx_refs"] = refs
            return
    refs.append(ref)
    asset["source_pptx_refs"] = refs


def _attach_ppt_source_metadata(db: dict[str, Any], source_by_id: dict[str, dict[str, Any]]) -> None:
    assets = db.get("assets")
    if not isinstance(assets, list):
        return
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        source = source_by_id.get(_clean_text(asset.get("asset_id")))
        if source:
            asset.update(source)


def _dedupe_ppt_near_duplicate_assets(db: dict[str, Any], library_root: Path) -> dict[str, Any]:
    assets = [asset for asset in db.get("assets", []) if isinstance(asset, dict)]
    if len(assets) < 2:
        return {"removed_count": 0, "groups": []}

    groups_by_ppt: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        if not _is_ppt_near_duplicate_eligible(asset):
            continue
        pptx_path = _clean_text(asset.get("_pptx_path"))
        if not pptx_path:
            continue
        groups_by_ppt.setdefault(pptx_path, []).append(asset)

    duplicate_ids: set[str] = set()
    duplicate_groups: list[dict[str, Any]] = []
    for pptx_path, group in groups_by_ppt.items():
        representatives: list[dict[str, Any]] = []
        ordered = sorted(
            group,
            key=lambda asset: (
                -_ppt_near_duplicate_quality_score(asset),
                _clean_text(asset.get("asset_id")),
            ),
        )
        for asset in ordered:
            duplicate_of = next(
                (
                    representative
                    for representative in representatives
                    if _are_ppt_near_duplicates(asset, representative, library_root)
                ),
                None,
            )
            if duplicate_of is None:
                representatives.append(asset)
                continue

            asset_id = _clean_text(asset.get("asset_id"))
            duplicate_ids.add(asset_id)
            _record_duplicate_asset_id(duplicate_of, asset_id)
            for nested_id in _as_string_list(asset.get("duplicate_asset_ids")):
                _record_duplicate_asset_id(duplicate_of, nested_id)
            deleted_image_path = _delete_ppt_duplicate_image(asset, library_root)
            duplicate_groups.append(
                {
                    "pptx_path": pptx_path,
                    "kept": _clean_text(duplicate_of.get("asset_id")),
                    "removed": asset_id,
                    "deleted_image_path": deleted_image_path,
                    "kept_usage": _clean_text(duplicate_of.get("_ppt_usage")),
                    "removed_usage": _clean_text(asset.get("_ppt_usage")),
                }
            )

    if duplicate_ids:
        db["assets"] = [asset for asset in assets if _clean_text(asset.get("asset_id")) not in duplicate_ids]
    db["asset_count"] = len(db.get("assets", []))
    return {"removed_count": len(duplicate_ids), "groups": duplicate_groups}


def _is_ppt_near_duplicate_eligible(asset: dict[str, Any]) -> bool:
    if _clean_text(asset.get("asset_kind")) != "page_image":
        return False
    return _clean_text(asset.get("asset_category")) in PPT_NEAR_DUPLICATE_CATEGORIES


def _are_ppt_near_duplicates(left: dict[str, Any], right: dict[str, Any], library_root: Path) -> bool:
    if _clean_text(left.get("asset_category")) != _clean_text(right.get("asset_category")):
        return False
    if not _ppt_subject_terms_match(left, right):
        return False
    if not _ppt_action_terms_match(left, right):
        return False
    left_hash = _ppt_asset_perceptual_hash(left, library_root)
    right_hash = _ppt_asset_perceptual_hash(right, library_root)
    if not left_hash or not right_hash:
        return False
    return _hash_hamming_distance(left_hash, right_hash) <= PPT_NEAR_DUPLICATE_PHASH_MAX_DISTANCE


def _ppt_subject_terms_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_terms = _ppt_content_terms(left)
    right_terms = _ppt_content_terms(right)
    return bool(left_terms and right_terms and _any_terms_similar(left_terms, right_terms))


def _ppt_action_terms_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_terms = _ppt_content_terms(left)
    right_terms = _ppt_content_terms(right)
    if not left_terms and not right_terms:
        return True
    return bool(left_terms and right_terms and _any_terms_similar(left_terms, right_terms))


def _ppt_content_terms(asset: dict[str, Any]) -> list[str]:
    text = _clean_text(asset.get("content_prompt"))
    if not text:
        return []
    terms: list[str] = []
    terms.append(_normalized_near_duplicate_term(text))
    terms.extend(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text.casefold()))
    cleaned: list[str] = []
    for term in terms:
        term = _normalized_near_duplicate_term(term)
        if term:
            cleaned.append(term)
    return _dedupe(cleaned)[:8]


def _any_terms_similar(left_terms: list[str], right_terms: list[str]) -> bool:
    return any(_near_duplicate_terms_similar(left, right) for left in left_terms for right in right_terms)


def _near_duplicate_terms_similar(left: str, right: str) -> bool:
    left = _normalized_near_duplicate_term(left)
    right = _normalized_near_duplicate_term(right)
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) >= 2 and (left in right or right in left):
        return True
    left_grams = _char_ngrams(left, 2)
    right_grams = _char_ngrams(right, 2)
    if not left_grams or not right_grams:
        return False
    return len(left_grams & right_grams) / len(left_grams | right_grams) >= 0.5


def _normalized_near_duplicate_term(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value)).casefold()


def _char_ngrams(value: str, size: int) -> set[str]:
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(0, len(value) - size + 1)}


def _ppt_asset_perceptual_hash(asset: dict[str, Any], library_root: Path) -> str:
    cached = _clean_text(asset.get("_ppt_perceptual_hash"))
    if cached:
        return cached
    image_path = _clean_text(asset.get("image_path"))
    if not image_path:
        return ""
    path = (library_root / image_path).resolve()
    if not path.exists():
        return ""
    try:
        with Image.open(path) as img:
            rgba = img.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            background.alpha_composite(rgba)
            gray = background.convert("L").resize((9, 8), Image.LANCZOS)
            pixels = list(gray.getdata())
    except Exception:
        return ""

    bits: list[str] = []
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits.append("1" if pixels[row + x] > pixels[row + x + 1] else "0")
    digest = f"{int(''.join(bits), 2):016x}"
    asset["_ppt_perceptual_hash"] = digest
    return digest


def _hash_hamming_distance(left_hash: str, right_hash: str) -> int:
    try:
        return bin(int(left_hash, 16) ^ int(right_hash, 16)).count("1")
    except ValueError:
        return 64


def _ppt_near_duplicate_quality_score(asset: dict[str, Any]) -> float:
    source_pixels = _safe_float(asset.get("_ppt_source_pixels"))
    display_pixels = _safe_float(asset.get("_ppt_display_pixels"))
    return source_pixels + display_pixels


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_material_category(value: Any) -> str:
    return normalize_strict_reuse_group(value, default="C03_scene_decor_container")


def _is_skip_material_category(value: Any) -> bool:
    return _normalize_material_category(value) == "C00_strict_text_problem_skip"


def _archive_ppt_skip_images(
    db: dict[str, Any],
    *,
    library_root: Path,
    warnings: list[Any],
) -> dict[str, int]:
    assets = db.get("assets")
    if not isinstance(assets, list):
        return {"archived_count": 0, "missing_count": 0}

    archived_count = 0
    missing_count = 0
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if not _is_skip_material_category(asset.get("strict_reuse_group")):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        if not asset_id:
            missing_count += 1
            warnings.append("ppt_skip_image_archive_missing_asset_id")
            continue

        runtime_rel = _clean_text(asset.get("image_path"))
        original_rel = _clean_text(asset.get("original_image_path"))
        runtime_target_rel = f"{DEFAULT_SKIP_IMAGE_DIR}/{asset_id}.png"
        original_target_rel = f"{DEFAULT_SKIP_IMAGE_DIR}/{asset_id}_original.png"

        runtime_moved = _move_library_file(library_root, runtime_rel, runtime_target_rel)
        if not runtime_moved:
            missing_count += 1
            warnings.append(f"ppt_skip_image_archive_missing:{asset_id}:{runtime_rel}")
            continue
        asset["image_path"] = runtime_target_rel

        if original_rel and original_rel != runtime_rel:
            original_moved = _move_library_file(library_root, original_rel, original_target_rel)
            if original_moved:
                asset["original_image_path"] = original_target_rel
            else:
                missing_count += 1
                warnings.append(f"ppt_skip_image_archive_missing_original:{asset_id}:{original_rel}")
                asset["original_image_path"] = runtime_target_rel
        else:
            asset["original_image_path"] = runtime_target_rel

        archived_count += 1

    return {"archived_count": archived_count, "missing_count": missing_count}


def _move_library_file(library_root: Path, source_rel: str, dest_rel: str) -> bool:
    source = _library_file_path(library_root, source_rel)
    if source is None or not source.exists():
        return False
    dest = library_root / dest_rel
    root = library_root.resolve()
    try:
        source_resolved = source.resolve()
        dest_resolved = dest.resolve()
        source_resolved.relative_to(root)
        dest_resolved.parent.relative_to(root)
    except ValueError:
        return False
    if source_resolved == dest_resolved:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    source.replace(dest)
    return True


def _library_file_path(library_root: Path, value: Any) -> Path | None:
    text = _clean_text(value)
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return library_root / path


def _remove_ppt_embedding_sidecars(library_root: Path, *, warnings: list[Any]) -> None:
    targets = [
        library_root / DEFAULT_EMBEDDING_INDEX_FILENAME,
        library_root / DEFAULT_EMBEDDING_META_FILENAME,
    ]
    targets.extend(
        [
            path.with_name(f"{path.stem}.checkpoint{path.suffix}")
            if path.suffix != ".json"
            else path.with_name(f"{path.stem}.checkpoint.json")
            for path in targets
        ]
    )
    for path in targets:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"ppt_embedding_sidecar_cleanup_failed:{path.name}:{type(exc).__name__}")


def _record_duplicate_asset_id(target: dict[str, Any], duplicate_id: str) -> None:
    duplicate_id = _clean_text(duplicate_id)
    if not duplicate_id:
        return
    if duplicate_id == _clean_text(target.get("asset_id")):
        return
    existing = _as_string_list(target.get("duplicate_asset_ids"))
    if duplicate_id in existing:
        return
    existing.append(duplicate_id)
    target["duplicate_asset_ids"] = existing


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text)
    return result


def _delete_ppt_duplicate_image(asset: dict[str, Any], library_root: Path) -> str:
    image_path = _clean_text(asset.get("image_path"))
    if not image_path:
        return ""
    root = library_root.resolve()
    path = (root / image_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return ""
    if path.exists() and path.is_file():
        try:
            path.unlink()
        except OSError:
            return ""
    return str(path)


PPT_LLM_ENRICHMENT_METHOD = "ppt_three_independent_query_judgments"

PPT_ALLOWED_SUBJECTS = {"语文", "数学", "物理", "其他", "other"}
PPT_ALLOWED_GRADE_NORMS = {
    "一年级",
    "二年级",
    "三年级",
    "四年级",
    "五年级",
    "六年级",
    "七年级",
    "八年级",
    "九年级",
    "高一",
    "高二",
    "高三",
    "其他",
    "other",
}
PPT_ALLOWED_GRADE_BANDS = {"低年级", "高年级", "其他", "other", "upper", "lower"}


def _normalize_ppt_enum(value: Any, allowed: set[str], default: str = "其他") -> str:
    text = _clean_text(value)
    return text if text in allowed else default


def _apply_ppt_classification_result(asset: dict[str, Any], result: dict[str, Any]) -> None:
    group = normalize_strict_reuse_group(result.get("strict_reuse_group"))
    asset["strict_reuse_group"] = group
    secondary = normalize_secondary_reuse_group(
        result.get(SECONDARY_REUSE_GROUP_FIELD),
        primary=group,
    )
    if secondary:
        asset[SECONDARY_REUSE_GROUP_FIELD] = secondary
    else:
        asset.pop(SECONDARY_REUSE_GROUP_FIELD, None)


def _apply_secondary_scene_caption(item: dict[str, Any], client: Any, *, batch_size: int) -> list[str]:
    from edupptx.materials.caption_rules import summarize_secondary_scene_records
    from edupptx.materials.strict_reuse_classifier import (
        C01_IRREPLACEABLE_ENTITY_EVENT_ACTION,
        C03_SCENE_DECOR_CONTAINER,
        normalize_secondary_reuse_group,
        normalize_strict_reuse_group,
    )

    primary = normalize_strict_reuse_group(item.get("strict_reuse_group"), default="")
    secondary = normalize_secondary_reuse_group(item.get(SECONDARY_REUSE_GROUP_FIELD), primary=primary)
    if not (primary == C01_IRREPLACEABLE_ENTITY_EVENT_ACTION and secondary == C03_SCENE_DECOR_CONTAINER):
        item.pop("secondary_reuse_query", None)
        item.pop("secondary_reuse_caption", None)
        return []
    if not _clean_text(item.get("query")):
        return []
    try:
        rows = summarize_secondary_scene_records(
            [item],
            client,
            query_field="query",
            batch_size=max(1, int(batch_size or 1)),
        )
        query_text = _clean_text(rows[0].get("secondary_reuse_query")) if rows else ""
        caption = _clean_text(rows[0].get("secondary_reuse_caption")) if rows else ""
        if query_text:
            item["secondary_reuse_query"] = query_text
        if caption:
            item["secondary_reuse_caption"] = caption
        return []
    except Exception as exc:
        return [f"ppt_secondary_caption_failed:{type(exc).__name__}: {exc}"]


def _enrich_single_ppt_asset_with_llm(
    asset: dict[str, Any],
    client: Any,
    *,
    batch_size: int,
) -> tuple[dict[str, Any], list[str]]:
    from edupptx.materials.caption_rules import summarize_records
    from edupptx.materials.general_rules import judge_records
    from edupptx.materials.reuse_policy import reuse_level_from_material_category
    from edupptx.materials.strict_reuse_classifier import classify_records

    item = dict(asset)
    warnings: list[str] = []
    if _clean_text(item.get("asset_kind")) != "page_image" or not _clean_text(item.get("query")):
        return item, warnings

    size = max(1, int(batch_size or DEFAULT_PPT_KEYWORD_BATCH_SIZE))
    asset_id = _clean_text(item.get("asset_id")) or "unknown_asset"
    try:
        classified = classify_records([item], client, batch_size=size)
        result = classified[0] if classified else {}
        _apply_ppt_classification_result(item, result)
        item["strict_reuse_signals"] = _dedupe(
            [
                *[str(entry) for entry in (item.get("strict_reuse_signals") or []) if str(entry).strip()],
                "ppt_independent_llm_classify",
            ]
        )
    except Exception as exc:
        warnings.append(f"{asset_id} ppt_classify_failed:{type(exc).__name__}: {exc}")
        return item, warnings

    if reuse_level_from_material_category(item.get("strict_reuse_group")) == "skip":
        return item, warnings

    try:
        summarized = summarize_records(
            [item],
            client,
            query_field="query",
            caption_field="caption",
            batch_size=size,
        )
        result = summarized[0] if summarized else {}
        item["caption"] = _clean_text(result.get("caption")) or _clean_text(item.get("query"))
    except Exception as exc:
        warnings.append(f"{asset_id} ppt_caption_failed:{type(exc).__name__}: {exc}")

    try:
        judged = judge_records(
            [item],
            client,
            query_field="query",
            general_field="general",
            batch_size=size,
        )
        result = judged[0] if judged else {}
        item["general"] = bool(result.get("general"))
    except Exception as exc:
        warnings.append(f"{asset_id} ppt_general_failed:{type(exc).__name__}: {exc}")
    warnings.extend(_apply_secondary_scene_caption(item, client, batch_size=size))
    return item, warnings


def _enrich_ppt_assets_with_llm(
    db: dict[str, Any],
    client: Any,
    *,
    batch_size: int,
    warnings: list[Any],
) -> None:
    from edupptx.materials.caption_rules import summarize_records
    from edupptx.materials.general_rules import judge_records
    from edupptx.materials.reuse_policy import reuse_level_from_material_category
    from edupptx.materials.strict_reuse_classifier import (
        classify_records,
        normalize_strict_reuse_group,
    )

    page_assets = [
        asset
        for asset in (db.get("assets") or [])
        if isinstance(asset, dict)
        and _clean_text(asset.get("asset_kind")) == "page_image"
        and _clean_text(asset.get("query"))
    ]
    if not page_assets:
        return
    size = max(1, int(batch_size or DEFAULT_KEYWORD_BATCH_SIZE))
    db["keyword_built_at"] = datetime.now(timezone.utc).isoformat()
    db["keyword_builder"] = {
        "method": PPT_LLM_ENRICHMENT_METHOD,
        "batch_size": size,
        "model": _clean_text(getattr(client, "_model", "")),
    }

    try:
        classified = classify_records(page_assets, client, batch_size=size)
        for asset, result in zip(page_assets, classified):
            _apply_ppt_classification_result(asset, result)
            asset["strict_reuse_signals"] = _dedupe(
                [
                    *[str(item) for item in (asset.get("strict_reuse_signals") or []) if str(item).strip()],
                    "ppt_independent_llm_classify",
                ]
            )
    except Exception as exc:
        warnings.append(f"ppt_classify_failed:{type(exc).__name__}: {exc}")
        return

    reusable = [
        asset
        for asset in page_assets
        if reuse_level_from_material_category(asset.get("strict_reuse_group")) != "skip"
    ]
    if not reusable:
        return
    try:
        summarized = summarize_records(
            reusable,
            client,
            query_field="query",
            caption_field="caption",
            batch_size=size,
        )
        for asset, result in zip(reusable, summarized):
            asset["caption"] = _clean_text(result.get("caption")) or _clean_text(asset.get("query"))
    except Exception as exc:
        warnings.append(f"ppt_caption_failed:{type(exc).__name__}: {exc}")
    try:
        judged = judge_records(
            reusable,
            client,
            query_field="query",
            general_field="general",
            batch_size=size,
        )
        for asset, result in zip(reusable, judged):
            asset["general"] = bool(result.get("general"))
    except Exception as exc:
        warnings.append(f"ppt_general_failed:{type(exc).__name__}: {exc}")
    for asset in reusable:
        warnings.extend(_apply_secondary_scene_caption(asset, client, batch_size=size))


def _strip_ppt_internal_asset_fields(db: dict[str, Any]) -> None:
    assets = db.get("assets")
    if not isinstance(assets, list):
        return
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        for key in PPT_INTERNAL_ASSET_KEYS:
            asset.pop(key, None)


def _annotate_with_vlm(
    *,
    vlm_client: Any,
    image_path: Path,
    item: RawPptImage,
    meta: dict[str, Any],
    context: dict[str, Any],
    vlm_max_side: int,
) -> dict[str, Any]:
    payload = {
        "course": _course_block(meta),
        "pptx": {
            "file_name": meta.get("file_name") or item.pptx_path.name,
            "description": meta.get("description") or "",
            "slide_no": item.slide_no,
            "shape_idx": item.shape_idx,
        },
        "image": {
            "width": item.width,
            "height": item.height,
            "aspect_ratio": _ppt_aspect_ratio_name(item.width, item.height),
            "display_bbox": item.bbox,
            "source_media_path": item.source_media_path,
        },
        "context": context,
    }
    messages = [
        {"role": "system", "content": PPT_VLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请标注这张 PPT 教学图片。上下文 JSON:\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2),
                },
                {"type": "image_url", "image_url": {"url": _image_data_url(image_path, vlm_max_side)}},
            ],
        },
    ]
    return vlm_client.chat_vlm_json(messages=messages, temperature=0.1, max_tokens=4096)


def _annotate_and_build_ppt_asset(
    *,
    vlm_client: Any,
    image_path: Path,
    asset_id: str,
    image_rel: str,
    original_image_rel: str,
    image_fields: dict[str, int | str],
    item: RawPptImage,
    meta: dict[str, Any],
    context: dict[str, Any],
    vlm_max_side: int,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        annotation = _annotate_with_vlm(
            vlm_client=vlm_client,
            image_path=image_path,
            item=item,
            meta=meta,
            context=context,
            vlm_max_side=vlm_max_side,
        )
    except Exception as exc:
        annotation = _fallback_annotation(item, meta, context, f"vlm_failed:{type(exc).__name__}")
        warnings.append(f"{_usage_label(item)} VLM failed: {exc}")
    normalized = _normalize_annotation(annotation, item, meta, context, image_path=image_path)
    asset = _build_asset_from_annotation(
        asset_id=asset_id,
        image_rel=image_rel,
        original_image_rel=original_image_rel,
        image_fields=image_fields,
        item=item,
        meta=meta,
        context=context,
        annotation=normalized,
    )
    return asset, warnings


def _build_fallback_ppt_asset(
    *,
    asset_id: str,
    image_rel: str,
    original_image_rel: str,
    image_fields: dict[str, int | str],
    item: RawPptImage,
    meta: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    annotation = _fallback_annotation(item, meta, context, "vlm_skipped")
    normalized = _normalize_annotation(annotation, item, meta, context)
    return _build_asset_from_annotation(
        asset_id=asset_id,
        image_rel=image_rel,
        original_image_rel=original_image_rel,
        image_fields=image_fields,
        item=item,
        meta=meta,
        context=context,
        annotation=normalized,
    )


def _image_data_url(image_path: Path, max_side: int) -> str:
    with Image.open(image_path) as img:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        if img.mode in {"RGBA", "LA"}:
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        else:
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=86, optimize=True)
    encoded = base64.b64encode(out.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _fallback_annotation(item: RawPptImage, meta: dict[str, Any], context: dict[str, Any], reason: str) -> dict[str, Any]:
    query = "教学配图"
    if context.get("slide_text"):
        query = f"教学配图，页面可见文字包含：{_clean_text(context.get('slide_text'))[:60]}"
    return {
        "query": query,
        "context_summary": _fallback_ppt_context_summary(context),
        "teaching_intent": "辅助课堂讲解和页面视觉说明",
        "fallback_reason": reason,
    }


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = _clean_text(value).casefold()
    if text in {"true", "1", "yes", "是"}:
        return True
    if text in {"false", "0", "no", "否"}:
        return False
    return default


def _normalize_annotation(
    annotation: dict[str, Any],
    item: RawPptImage,
    meta: dict[str, Any],
    context: dict[str, Any],
    *,
    image_path: Path | None = None,
) -> dict[str, Any]:
    raw = annotation if isinstance(annotation, dict) else {}
    query = _clean_text(raw.get("query")) or _clean_text(raw.get("content_prompt"))
    if not query:
        query = _fallback_annotation(item, meta, context, "missing_query")["query"]
    context_summary = _clean_text(raw.get("context_summary")) or _fallback_ppt_context_summary(context)
    teaching_intent = _clean_text(raw.get("teaching_intent")) or "辅助课堂教学说明"
    return {
        "query": query,
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "is_backdrop": _as_bool(raw.get("is_backdrop"), default=False),
    }


def _build_asset_from_annotation(
    *,
    asset_id: str,
    image_rel: str,
    original_image_rel: str,
    image_fields: dict[str, int | str],
    item: RawPptImage,
    meta: dict[str, Any],
    context: dict[str, Any],
    annotation: dict[str, Any],
) -> dict[str, Any]:
    course = _course_block(meta)
    deck_metadata = _ppt_deck_metadata_from_meta(meta)
    page_type = _infer_page_type(item.slide_no, context.get("slide_text"), context.get("slide_title_guess"))
    theme = _theme_from_course(course)
    lesson_ref = course.get("lesson") or Path(meta.get("file_name") or item.pptx_path.name).stem
    unit_ref = _clean_text(course.get("unit"))
    topic_refs = extract_topic_refs(lesson_ref)
    is_backdrop = bool(annotation.get("is_backdrop"))
    asset_kind = _resolve_ppt_asset_kind(item, is_backdrop)
    query = annotation["query"]
    asset = {
        "asset_id": asset_id,
        "asset_kind": asset_kind,
        "image_path": image_rel,
        "original_image_path": original_image_rel,
        "actual_width": image_fields["actual_width"],
        "actual_height": image_fields["actual_height"],
        "padded_width": image_fields["padded_width"],
        "padded_height": image_fields["padded_height"],
        "aspect_ratio": image_fields["aspect_ratio"],
        "page_type": page_type,
        "theme": theme,
        "subject": deck_metadata["subject"],
        "grade_norm": deck_metadata["grade_norm"],
        "grade_band": deck_metadata["grade_band"],
        "unit_ref": unit_ref,
        "topic_refs": topic_refs,
        "query": query,
        "is_backdrop": is_backdrop,
        "context_summary": annotation["context_summary"],
        "teaching_intent": annotation["teaching_intent"],
        "asset_category": "background" if asset_kind == "background" else "unknown",
        "duplicate_asset_ids": [],
        "source_pptx_refs": [_ppt_source_ref(item, meta)],
    }
    if asset_kind == "background":
        asset["normalized_prompt"] = query
    return asset


def _load_pptx_metadata(db_path: Path, pptx_path: Path, pptx_root: Path) -> dict[str, Any]:
    fallback = {
        "id": "",
        "period_id": "",
        "file_name": pptx_path.name,
        "file_path": str(pptx_path),
        "description": "",
        "subject": "",
        "grade": "",
        "semester": "",
        "unit": "",
        "lesson": "",
        "period": "",
    }
    if not db_path.exists():
        return fallback
    rel_candidates = []
    try:
        rel = pptx_path.resolve().relative_to(pptx_root.resolve()).as_posix()
        rel_candidates.append(f"pptx/{rel}")
    except Exception:
        pass
    rel_candidates.extend([f"pptx/{pptx_path.name}", pptx_path.name, str(pptx_path)])

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = None
        for rel_path in rel_candidates:
            row = con.execute(
                """
                WITH p AS (SELECT * FROM pptx_files WHERE file_path=? OR file_name=? LIMIT 1)
                SELECT p.id, p.period_id, p.file_path, p.file_name, p.file_size, p.description,
                       h.subject, h.name AS period,
                       l.name AS lesson, u.name AS unit, s.name AS semester, g.name AS grade
                FROM p
                LEFT JOIN hierarchy h ON h.id = p.period_id
                LEFT JOIN hierarchy l ON l.id = h.parent_id
                LEFT JOIN hierarchy u ON u.id = l.parent_id
                LEFT JOIN hierarchy s ON s.id = u.parent_id
                LEFT JOIN hierarchy g ON g.id = s.parent_id
                """,
                (rel_path, pptx_path.name),
            ).fetchone()
            if row is not None:
                break
        if row is None:
            return fallback
        meta = dict(fallback)
        for key in row.keys():
            meta[key] = row[key]
        return meta
    except Exception:
        return fallback
    finally:
        con.close()


def _course_block(meta: dict[str, Any]) -> dict[str, str]:
    values = {
        "subject": _clean_text(meta.get("subject")),
        "grade": _clean_text(meta.get("grade")),
        "semester": _clean_text(meta.get("semester")),
        "unit": _clean_text(meta.get("unit")),
        "lesson": _clean_text(meta.get("lesson")),
        "period": _clean_text(meta.get("period")),
    }
    values["course_path"] = "/".join([value for value in values.values() if value])
    return values


def _resolve_ppt_deck_metadata(
    meta: dict[str, Any],
    pptx_path: Path,
    markdown_excerpt: str = "",
    normalizer_client: Any | None = None,
) -> dict[str, str]:
    course = _course_block(meta)
    context_text = " ".join(
        text
        for text in (
            _clean_text(meta.get("description")),
            _clean_text(course.get("course_path")),
            _clean_text(markdown_excerpt)[:1600],
        )
        if text
    )
    resolved = resolve_meta_grade_subject(
        llm_subject=course.get("subject"),
        llm_grade=course.get("grade"),
        llm_grade_band=meta.get("grade_band", ""),
        topic=course.get("lesson") or Path(meta.get("file_name") or pptx_path.name).stem,
        audience=course.get("period"),
        requirements=context_text,
        normalizer_client=normalizer_client,
    )
    return {
        "subject": resolved["subject"],
        "grade_norm": resolved["grade"],
        "grade_band": resolved["grade_band"],
    }


def _ppt_deck_metadata_from_meta(meta: dict[str, Any]) -> dict[str, str]:
    seeded = meta.get("deck_metadata")
    if isinstance(seeded, dict):
        grade_info = normalize_grade_info(seeded.get("grade_norm") or seeded.get("grade"), seeded.get("grade_band"))
        return {
            "subject": _clean_text(seeded.get("subject")) or "其他",
            "grade_norm": grade_info["grade_norm"],
            "grade_band": grade_info["grade_band"],
        }
    course = _course_block(meta)
    grade_info = normalize_grade_info(course.get("grade"), meta.get("grade_band"))
    return {
        "subject": _clean_text(course.get("subject")) or "其他",
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
    }


def _build_context(item: RawPptImage, meta: dict[str, Any], markdown_excerpt: str) -> dict[str, Any]:
    return {
        "slide_no": item.slide_no,
        "slide_title_guess": item.slide_title_guess,
        "slide_text": item.slide_text,
        "deck_markdown_excerpt": markdown_excerpt[:1600],
        "course": _course_block(meta),
    }


def _fallback_ppt_context_summary(context: dict[str, Any]) -> str:
    text = _clean_text(f"{context.get('slide_title_guess', '')} {context.get('slide_text', '')}")
    if any(term in text for term in ("字词", "生字", "拼音", "笔顺", "田字格", "部首")):
        return "字词学习页面，用于生字教学演示"
    if any(term in text for term in ("练习", "作业", "检测", "巩固")):
        return "练习巩固页面，用于课堂互动和学习检测"
    if any(term in text for term in ("导入", "初读", "默读", "课文")):
        return "课文学习页面，用于情境导入和内容理解"
    if any(term in text for term in ("小结", "总结", "回顾")):
        return "课堂总结页面，用于知识回顾和要点梳理"
    return "教学内容页面，用于课堂讲解辅助"


def _compact_theme_text(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value).replace("/", ""))


def _extract_markitdown_excerpt(pptx_path: Path) -> str:
    try:
        from markitdown import MarkItDown  # type: ignore
    except Exception:
        return ""
    try:
        result = MarkItDown().convert(str(pptx_path))
        return _clean_text(getattr(result, "text_content", "") or getattr(result, "markdown", ""))
    except Exception:
        return ""


def _build_library_db_snapshot(
    *,
    assets_by_id: dict[str, dict[str, Any]],
    library_root: Path,
    existing_db: dict[str, Any],
    teach_root: Path,
    warnings: Iterable[Any],
    include_skip: bool = False,
) -> dict[str, Any]:
    snapshot_assets = list(assets_by_id.values())
    if not include_skip:
        snapshot_assets = [
            asset for asset in snapshot_assets
            if not _is_skip_material_category(asset.get("strict_reuse_group"))
        ]
    assets = sorted(
        snapshot_assets,
        key=lambda asset: (
            _clean_text(asset.get("asset_kind")),
            _clean_text(asset.get("image_path")),
            _clean_text(asset.get("asset_id")),
        ),
    )
    for asset in assets:
        if not asset.get("topic_refs"):
            asset["topic_refs"] = extract_topic_refs(asset.get("theme"))
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": PPT_LIBRARY_SCHEMA_VERSION,
        "built_at": existing_db.get("built_at") or now,
        "updated_at": now,
        "output_root": str(library_root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": _dedupe(
            [
                *existing_db.get("warnings", []),
                *warnings,
            ]
        ),
        "ppt_extractor": {
            "schema_version": PPT_LIBRARY_SCHEMA_VERSION,
            "method": EXTRACTOR_VERSION,
            "source_root": str(teach_root),
            "image_dir": DEFAULT_IMAGE_DIR,
        },
    }


def _write_incremental_match_index(
    *,
    assets_by_id: dict[str, dict[str, Any]],
    library_root: Path,
    existing_db: dict[str, Any],
    teach_root: Path,
    report: dict[str, Any],
    ppt_asset_source_by_id: dict[str, dict[str, Any]],
) -> Path:
    db = _build_library_db_snapshot(
        assets_by_id=assets_by_id,
        library_root=library_root,
        existing_db=existing_db,
        teach_root=teach_root,
        warnings=report.get("warnings", []),
        include_skip=True,
    )
    _attach_ppt_source_metadata(db, ppt_asset_source_by_id)
    _strip_ppt_internal_asset_fields(db)
    _index, index_path = write_ai_image_match_index(
        db,
        library_root,
        write_embedding_index=False,
    )
    report["match_index_path"] = str(index_path)
    report["incremental_match_index_written"] = True
    return index_path


def _iter_pptx_files(
    pptx_root: Path,
    pptx_paths: Iterable[str | Path] | None,
    limit: int,
) -> Iterable[Path]:
    if pptx_paths:
        count = 0
        for item in pptx_paths:
            if limit and count >= limit:
                break
            count += 1
            yield Path(item).expanduser().resolve()
        return
    files = sorted(pptx_root.glob("*.pptx"))
    if limit:
        files = files[:limit]
    yield from files


def _infer_page_type(slide_no: int, slide_text: Any, slide_title: Any) -> str:
    text = f"{_clean_text(slide_title)} {_clean_text(slide_text)}"
    if slide_no == 1:
        return "cover"
    if any(token in text for token in ("练习", "作业", "检测", "巩固")):
        return "exercise"
    if any(token in text for token in ("小结", "总结", "回顾")):
        return "summary"
    return "content"


def _theme_from_course(course: dict[str, str]) -> str:
    grade = course.get("grade", "")
    subject = course.get("subject", "")
    lesson = course.get("lesson", "")
    period = course.get("period", "")
    parts = [grade, subject, lesson, period]
    return _compact_theme_text("".join(part for part in parts if part)) or _compact_theme_text(course.get("course_path", ""))


def _asset_id_for_sha(sha256: str) -> str:
    return f"kbpptx_{sha256[:20]}"


def _usage_label(item: RawPptImage) -> str:
    return f"{item.pptx_path.name}:slide{item.slide_no}:shape{item.shape_idx}"


def _add_skip(report: dict[str, Any], item: str, reason: str) -> None:
    report["skipped_count"] += 1
    report["skipped"].append({"item": item, "reason": reason})


def _add_failed_pptx(report: dict[str, Any], pptx_path: Path, reason: str) -> None:
    report["failed_pptx_count"] = int(report.get("failed_pptx_count") or 0) + 1
    report.setdefault("failed_pptx", []).append({"pptx_path": str(pptx_path), "reason": reason})
    report.setdefault("warnings", []).append(f"pptx_failed:{pptx_path}:{reason}")


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _qname(name: str) -> str:
    prefix, local = name.split(":", 1)
    return f"{{{NS[prefix]}}}{local}"


def _slide_no_from_path(path: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", path)
    return int(match.group(1)) if match else 0


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teach-kb-root", type=Path, default=Path("data/uploads/pptx"), help="Directory containing source PPTX files, e.g. /srv/teach-kb/data/uploads/pptx")
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--db", type=Path, default=None, help="teach-kb SQLite DB path")
    parser.add_argument("--pptx", action="append", default=[], help="Specific PPTX path; can be repeated")
    parser.add_argument("--limit", type=int, default=0, help="Limit PPTX files when scanning directory")
    parser.add_argument("--max-assets", type=int, default=0, help="Stop after this many kept image usages")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--skip-vlm", action="store_true", help="Do not call VLM; write fallback metadata")
    parser.add_argument("--keep-rejected", action="store_true", help="Deprecated compatibility flag; VLM no longer emits rejection fields")
    parser.add_argument("--no-match-index", action="store_true", help="Do not write split reuse indexes")
    parser.add_argument("--no-incremental-index", action="store_true", help="Only write split reuse indexes after all PPTX files finish")
    parser.add_argument("--flush-every", type=int, default=20, help="Rewrite the index every N processed PPTX (default 20). Smaller = safer on crash but more disk I/O; ignored when --no-incremental-index is set. The final full write always happens at the end.")
    parser.add_argument("--vlm-max-side", type=int, default=1280)
    parser.add_argument("--keyword-batch-size", type=int, default=DEFAULT_PPT_KEYWORD_BATCH_SIZE)
    parser.add_argument("--vlm-workers", type=int, default=DEFAULT_PPT_VLM_WORKERS)
    parser.add_argument("--llm-workers", type=int, default=DEFAULT_PPT_LLM_WORKERS)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    db, index_path, report = build_ppt_image_materials_library(
        teach_kb_root=args.teach_kb_root,
        output_library_dir=args.library_dir,
        db_path=args.db,
        pptx_paths=args.pptx or None,
        limit=args.limit,
        max_assets=args.max_assets,
        use_vlm=not args.skip_vlm,
        keep_rejected=args.keep_rejected,
        write_match_index=not args.no_match_index,
        flush_each_ppt=not args.no_incremental_index,
        flush_every_n_ppt=args.flush_every,
        env_file=args.env_file,
        vlm_max_side=args.vlm_max_side,
        keyword_batch_size=args.keyword_batch_size,
        vlm_workers=args.vlm_workers,
        llm_workers=args.llm_workers,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "match_index_path": str(index_path),
                "asset_count": db.get("asset_count", 0),
                "pptx_count": report.get("pptx_count", 0),
                "skipped_count": report.get("skipped_count", 0),
                "warning_count": report.get("warning_count", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
