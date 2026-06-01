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
    DEFAULT_KEYWORD_BATCH_SIZE,
    DEFAULT_MATCH_INDEX_FILENAME,
    enrich_ai_image_asset_db_keywords,
    extract_topic_refs,
    normalize_grade_info,
    read_ai_image_split_match_index,
    write_ai_image_match_index,
)
from edupptx.materials.strict_reuse_classifier import (
    MATERIAL_CATEGORY_RULES_TEXT,
    normalize_strict_reuse_group,
)

PPT_LIBRARY_SCHEMA_VERSION = 10
EXTRACTOR_VERSION = "ppt_materials_library.v10"
DEFAULT_LIBRARY_DIR = Path("materials_library_ppt")
DEFAULT_IMAGE_DIR = "pptx_images"
DEFAULT_ORIGINAL_IMAGE_DIR = "pptx_images_original"
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

PPT_VLM_SYSTEM_PROMPT = """你是教学课件素材库的图片标注助手。给定一张从 PPT 中提取的图片以及它所在课件、页码和前后页文本，你只生成可重新生成该图的完整 query 与一个视觉分类判断。

输出 JSON 结构：
{
  "query": "可重新生成该图的完整中文 prompt，保留全部可见文字/数值/标注/图形关系",
  "context_summary": "一句 20-40 个汉字的短句，描述画面内容和页面功能",
  "teaching_intent": "该图服务的教学动作或学习目标",
  "general": false,
  "visual_reuse_group": "<C00-C05 material category ID>",
  "visual_reuse_confidence": 0.0,
  "visual_reuse_reason": "一句中文判断依据"
}

通用原则：
1. query 是"若要重新生成这张图，会怎么写生成 prompt"：保留教学主体、教学载体、影响题意的动作或场景，以及可读教学内容（具体汉字、拼音、数字、公式、标签等原子值）和它们之间的数量、顺序、对应、因果、空间或比较关系。这些原子值与关系是分类依据，绝不能省略。

2. query 省略所有用途、课堂活动、页面功能、教学目标、使用方式和来源语境（课程、年级、学科、页码、文件名等）。

3. 当图片仅为通用工具或空白底图时，query 必须显式说明不含具体可读内容，例如"不含具体汉字、拼音或文字"，避免被误用为含字底图。

4. 面对教学模板化组件时，query 写"教学载体 + 具体教学内容"，对多音字、形近字、偏旁部首、词语辨析、算式推导、实验标签等，必须保留具体对象及其对应关系。

5. context_summary 保持短句风格（20-40 汉字），写"画面内容 + 页面功能"，先说主体/动作/关系再说页面功能；不要退化为"页面类型+用于+主题+展示"模板句。

6. teaching_intent 写教学动作或学习目标，不重复 context_summary 也不重复来源信息。

7. visual_reuse_group 是你看图直接给出的素材分类（C00-C05），作为对 query 文本分类的交叉校验；visual_reuse_confidence 为 0-1，visual_reuse_reason 用一句中文说明画面依据。

8. 不要输出 caption、content_prompt、detail_prompt、core_keywords、semantic_aliases、query_aliases 或结构之外的任何字段。
"""


PPT_VLM_SYSTEM_PROMPT += (
    "\n\nFinal override for the current material-library schema:\n"
    "- visual_reuse_group must be one of the 6 material category IDs below (C00-C05).\n"
    "- C00_strict_text_problem_skip means skip reuse and skip library ingestion.\n"
    "- 用 query 作为唯一的图片本体描述字段；不要输出 caption/content_prompt/detail_prompt。\n"
    '- Output "general": false in every JSON object unless the image is clearly reusable across subjects.\n'
    "- general 必须是布尔值 true 或 false；你是严格保守的跨学科通用复用分类器，模糊时输出 false。\n"
    + MATERIAL_CATEGORY_RULES_TEXT
)


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
    env_file: str | Path = ".env",
    vlm_max_side: int = 1280,
    keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
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
        "pptx_count": 0,
        "raw_picture_count": 0,
        "kept_asset_count": 0,
        "updated_asset_count": 0,
        "skipped_count": 0,
        "warnings": [],
        "processed_pptx": [],
        "skipped": [],
    }

    selected_pptx_paths = [Path(item).expanduser().resolve() for item in (pptx_paths or [])]
    total_assets_before = len(assets_by_id)
    ppt_asset_source_by_id: dict[str, dict[str, Any]] = {}
    iter_pptx_paths = selected_pptx_paths if selected_pptx_paths else None
    for pptx_path in _iter_pptx_files(pptx_root, iter_pptx_paths, limit):
        if max_assets and report["kept_asset_count"] >= max_assets:
            break
        if not pptx_path.exists():
            _add_skip(report, str(pptx_path), "pptx_missing")
            continue

        meta = _load_pptx_metadata(kb_db_path, pptx_path, pptx_root)
        markdown_excerpt = _extract_markitdown_excerpt(pptx_path)
        raw_items = _extract_raw_ppt_images(pptx_path)
        wide_repeated = _repeated_wide_hashes(raw_items)
        pptx_summary = {
            "pptx_path": str(pptx_path),
            "raw_picture_count": len(raw_items),
            "kept": 0,
            "skipped": 0,
        }
        report["pptx_count"] += 1
        report["raw_picture_count"] += len(raw_items)

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
                existing_asset = assets_by_id[asset_id]
                existing_asset.update(image_fields)
                existing_asset["image_path"] = image_rel
                existing_asset["original_image_path"] = original_image_rel
                existing_asset["asset_kind"] = _ppt_asset_kind(item)
                if existing_asset["asset_kind"] == "background":
                    existing_asset["asset_category"] = "background"
                    existing_asset["normalized_prompt"] = _clean_text(
                        existing_asset.get("normalized_prompt") or existing_asset.get("content_prompt")
                    )
                for removed_key in ("aspect_bucket", "role", "padding_capacity"):
                    existing_asset.pop(removed_key, None)
                pptx_summary["kept"] += 1
                report["kept_asset_count"] += 1
                continue

            context = _build_context(item, meta, markdown_excerpt)
            annotation: dict[str, Any]
            if use_vlm:
                try:
                    annotation = _annotate_with_vlm(
                        vlm_client=vlm_client,
                        image_path=original_image_abs,
                        item=item,
                        meta=meta,
                        context=context,
                        vlm_max_side=vlm_max_side,
                    )
                except Exception as exc:
                    annotation = _fallback_annotation(item, meta, context, f"vlm_failed:{type(exc).__name__}")
                    report["warnings"].append(f"{_usage_label(item)} VLM failed: {exc}")
            else:
                annotation = _fallback_annotation(item, meta, context, "vlm_skipped")

            annotation = _normalize_annotation(annotation, item, meta, context, image_path=original_image_abs)
            if _is_skip_material_category(annotation.get("visual_reuse_group")):
                _add_skip(report, _usage_label(item), "C00_strict_text_problem_skip")
                pptx_summary["skipped"] += 1
                try:
                    image_abs.unlink(missing_ok=True)
                    original_image_abs.unlink(missing_ok=True)
                except OSError:
                    pass
                continue

            asset = _build_asset_from_annotation(
                asset_id=asset_id,
                image_rel=image_rel,
                original_image_rel=original_image_rel,
                image_fields=image_fields,
                item=item,
                meta=meta,
                context=context,
                annotation=annotation,
            )
            assets_by_id[asset_id] = asset
            pptx_summary["kept"] += 1
            report["kept_asset_count"] += 1

        report["processed_pptx"].append(pptx_summary)
        if write_match_index and flush_each_ppt:
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

    db = _build_library_db_snapshot(
        assets_by_id=assets_by_id,
        library_root=library_root,
        existing_db=existing_db,
        teach_root=teach_root,
        warnings=report["warnings"],
    )
    if use_keyword_enrichment and keyword_client is not None:
        from edupptx.materials.caption_rules import summarize_records

        page_assets = [
            a for a in (db.get("assets") or [])
            if isinstance(a, dict)
            and _clean_text(a.get("asset_kind")) != "background"
            and not _clean_text(a.get("caption"))
        ]
        if page_assets:
            records = [{"query": _clean_text(a.get("query"))} for a in page_assets]
            try:
                summarized = summarize_records(records, keyword_client, query_field="query", caption_field="caption")
                for asset, item in zip(page_assets, summarized):
                    asset["caption"] = _clean_text(item.get("caption")) or _clean_text(asset.get("query"))
            except Exception as exc:
                report["warnings"].append(f"caption_summarize_failed:{type(exc).__name__}: {exc}")
                for asset in page_assets:
                    asset["caption"] = _clean_text(asset.get("query"))
        enrich_ai_image_asset_db_keywords(
            db,
            keyword_client,
            batch_size=keyword_batch_size,
            preserve_existing_context_fields=True,
        )
    elif use_keyword_enrichment:
        report["warnings"].append("keyword_enrichment_skipped: no LLM or VLM client configured")
    _attach_ppt_source_metadata(db, ppt_asset_source_by_id)
    near_duplicate_report = _dedupe_ppt_near_duplicate_assets(db, library_root)
    report["near_duplicate_count"] = near_duplicate_report["removed_count"]
    if near_duplicate_report["groups"]:
        report["near_duplicates"] = near_duplicate_report["groups"]
    mismatch_audit_path = _write_query_visual_mismatch_audit(db, library_root)
    report["query_visual_mismatch_audit_path"] = str(mismatch_audit_path)
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
            _index, index_path = write_ai_image_match_index(
                db,
                library_root,
            )
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
    return normalize_strict_reuse_group(value, default="C06_generic_scene_activity")


def _is_skip_material_category(value: Any) -> bool:
    return _normalize_material_category(value) == "C00_strict_text_problem_skip"


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


QUERY_VISUAL_MISMATCH_AUDIT_FILENAME = "query_visual_group_mismatch.json"


def _write_query_visual_mismatch_audit(db: dict[str, Any], library_root: str | Path) -> Path:
    """Log assets where query classification disagrees with the VLM visual group.

    Classification stays query-based (canonical). This is a passive audit file
    for human review; it never rewrites strict_reuse_group.
    """
    assets = db.get("assets") if isinstance(db.get("assets"), list) else []
    mismatches: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict) or _clean_text(asset.get("asset_kind")) != "page_image":
            continue
        query_group = normalize_strict_reuse_group(asset.get("strict_reuse_group"), default="")
        visual_group = normalize_strict_reuse_group(asset.get("visual_reuse_group"), default="")
        if not query_group or not visual_group or query_group == visual_group:
            continue
        mismatches.append({
            "asset_id": _clean_text(asset.get("asset_id")),
            "image_path": _clean_text(asset.get("image_path")),
            "query": _clean_text(asset.get("query")),
            "caption": _clean_text(asset.get("caption")),
            "strict_reuse_group": query_group,
            "strict_reuse_reason": _clean_text(asset.get("strict_reuse_reason")),
            "visual_reuse_group": visual_group,
            "visual_reuse_confidence": asset.get("visual_reuse_confidence"),
            "visual_reuse_reason": _clean_text(asset.get("visual_reuse_reason")),
        })
    debug_dir = Path(library_root) / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / QUERY_VISUAL_MISMATCH_AUDIT_FILENAME
    payload = {
        "note": "classification is query-based (canonical); these assets disagree with the VLM visual group, review manually",
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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
    prompt = "教学配图"
    detail = prompt
    if context.get("slide_text"):
        detail = f"{prompt}，页面可见文字包含：{_clean_text(context.get('slide_text'))[:80]}"
    return {
        "content_prompt": prompt,
        "detail_prompt": detail,
        "context_summary": _fallback_ppt_context_summary(context),
        "teaching_intent": "辅助课堂讲解和页面视觉说明",
        "general": False,
        "strict_reuse_group": "C06_generic_scene_activity",
        "strict_reuse_confidence": 0.5,
        "strict_reuse_reason": reason,
    }


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
        query = _fallback_annotation(item, meta, context, "missing_query")["content_prompt"]
    context_summary = _clean_text(raw.get("context_summary")) or _fallback_ppt_context_summary(context)
    teaching_intent = _clean_text(raw.get("teaching_intent")) or "辅助课堂教学说明"
    visual_reuse_group = _normalize_material_category(
        raw.get("visual_reuse_group") or raw.get("strict_reuse_group")
    )
    visual_reuse_confidence = max(0.0, min(1.0, _safe_float(raw.get("visual_reuse_confidence"))))
    if visual_reuse_confidence <= 0:
        visual_reuse_confidence = 0.5 if visual_reuse_group == "C05_scene_decor_container" else 0.75
    visual_reuse_reason = _clean_text(raw.get("visual_reuse_reason")) or "PPT VLM visual reuse group"
    general = _optional_bool(raw.get("general"))
    ann = {
        "query": query,
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "visual_reuse_group": visual_reuse_group,
        "visual_reuse_confidence": round(visual_reuse_confidence, 4),
        "visual_reuse_reason": visual_reuse_reason,
    }
    if general is not None:
        ann["general"] = general
    return ann


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
    grade_info = normalize_grade_info(course.get("grade"), course.get("course_path"), course.get("lesson"))
    subject = _clean_text(course.get("subject"))
    page_type = _infer_page_type(item.slide_no, context.get("slide_text"), context.get("slide_title_guess"))
    theme = _theme_from_course(course)
    lesson_ref = course.get("lesson") or Path(meta.get("file_name") or item.pptx_path.name).stem
    unit_ref = _clean_text(course.get("unit"))
    topic_refs = extract_topic_refs(lesson_ref)
    asset_kind = _ppt_asset_kind(item)
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
        "subject": subject,
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
        "unit_ref": unit_ref,
        "topic_refs": topic_refs,
        "query": query,
        "context_summary": annotation["context_summary"],
        "teaching_intent": annotation["teaching_intent"],
        "asset_category": "background" if asset_kind == "background" else "unknown",
        "duplicate_asset_ids": [],
        "visual_reuse_group": annotation.get("visual_reuse_group") or "C05_scene_decor_container",
        "visual_reuse_confidence": annotation.get("visual_reuse_confidence") or 0.5,
        "visual_reuse_reason": annotation.get("visual_reuse_reason") or "PPT VLM visual reuse group",
        "strict_reuse_group": annotation.get("visual_reuse_group") or "C05_scene_decor_container",
        "strict_reuse_confidence": annotation.get("visual_reuse_confidence") or 0.5,
        "strict_reuse_reason": annotation.get("visual_reuse_reason") or "PPT VLM visual reuse group (pre-query-classify)",
        "strict_reuse_signals": ["ppt_vlm_visual_reuse_group"],
    }
    general = _optional_bool(annotation.get("general"))
    if general is not None:
        asset["general"] = general
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
) -> dict[str, Any]:
    assets = sorted(
        [asset for asset in assets_by_id.values() if not _is_skip_material_category(asset.get("strict_reuse_group"))],
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
    )
    _attach_ppt_source_metadata(db, ppt_asset_source_by_id)
    _strip_ppt_internal_asset_fields(db)
    _index, index_path = write_ai_image_match_index(
        db,
        library_root,
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


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


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
    parser.add_argument("--vlm-max-side", type=int, default=1280)
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
        env_file=args.env_file,
        vlm_max_side=args.vlm_max_side,
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
