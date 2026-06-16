"""复用层建库/写索引编排：扫 session 资产建库、入库 merge、拷图、写 split+npz+sqlite 同步。函数体逐字一致。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger as PROGRESS_LOGGER

from edupptx.reuse._util import (
    _as_int,
    _clean_text,
    _dedupe_terms,
    _dict,
    _read_json_if_exists,
)
from edupptx.reuse._constants import (
    DEFAULT_MATCH_INDEX_FILENAME,
    REUSE_MANIFEST_FILENAME,
    SCHEMA_VERSION,
    _BACKGROUND_REUSE_TARGET_METADATA_FIELDS,
    _IMAGE_SUFFIXES,
    _OTHER_GRADE,
    _PAGE_REUSE_TARGET_METADATA_FIELDS,
    _REUSE_TARGET_METADATA_SEEDED_FIELD,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _asset_caption,
    _asset_page_type,
    _clean_prompt_route,
    _is_background_asset,
    _normalize_subject_value,
    _topic_refs_for_asset,
    extract_topic_refs,
)
from edupptx.reuse._normalize import (
    _normalize_grade_band_value,
    _normalize_grade_norm_value,
    grade_band_from_norm,
)
from edupptx.reuse._scoring import (
    _clean_background_route,
)
from edupptx.reuse._backend import (
    _reuse_backend,
)
from edupptx.reuse._embedding import (
    _read_npz_embedding_index,
    write_ai_image_embedding_index,
)
from edupptx.reuse._store import (
    _dedupe_warnings,
    _default_context_summary,
    _default_teaching_intent,
    _match_background_route,
    _match_prompt_route,
    _normalize_rich_asset_fields,
    _strip_background_color_bias_from_prompt,
    build_ai_image_match_index,
    write_ai_image_split_match_indexes,
)


def build_ai_image_asset_db(
    output_root: str | Path,
    *,
    target_keyword_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan rendered sessions and return the generated-image asset database.

    The persisted fields stay focused on reusable image content:
    prompt text, route metadata, normalized prompt, context summary,
    teaching intent, and grade/subject.
    """

    root = Path(output_root).expanduser().resolve()
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []

    single_session_root = (root / "plan.json").exists()
    provided_target_keyword_cache = target_keyword_cache if isinstance(target_keyword_cache, dict) else None

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
        session_target_keyword_cache = (
            provided_target_keyword_cache
            if single_session_root and provided_target_keyword_cache is not None
            else _load_session_reuse_target_keyword_cache(session_dir)
        )

        background_asset = _build_background_asset(
            root=root,
            session_dir=session_dir,
            plan_path=plan_path,
            materials_dir=materials_dir,
            context=context,
            plan=plan,
            reused_image_paths=reused_image_paths,
            target_keyword_cache=session_target_keyword_cache,
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
                target_keyword_cache=session_target_keyword_cache,
            ):
                assets.append(asset)

    assets.sort(
        key=lambda item: (
            _clean_text(item.get("asset_kind")),
            _clean_text(item.get("image_path")),
            _clean_text(item.get("asset_id")),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "output_root": str(root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": warnings,
    }


def write_ai_image_match_index(
    db: dict[str, Any],
    library_dir: str | Path,
    *,
    index_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    write_embedding_index: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Write the split matching indexes used by image reuse matching."""

    root = Path(library_dir).expanduser().resolve()
    index = build_ai_image_match_index(db, library_root=root)
    if write_embedding_index:
        embedding_report = write_ai_image_embedding_index(index, root)
        if embedding_report:
            index["embedding_index"] = embedding_report
            embedding_warnings = _as_string_list(embedding_report.get("warnings"))
            if embedding_warnings:
                index["warnings"] = _dedupe_warnings([*_as_string_list(index.get("warnings")), *embedding_warnings])
    split_dir = write_ai_image_split_match_indexes(index, root)
    legacy_target = root / index_filename
    if legacy_target.exists():
        legacy_target.unlink()
    # B2 stage-3a (bridge): in sqlite mode keep library.db in sync from the freshly
    # written split index + npz so new ingests are reachable via the sqlite read path.
    # Reads the fresh .npz directly (not the branched reader, which would read the db).
    # Direct json-skip write is R5; this guarantees db == json so read-parity holds.
    if _reuse_backend() == "sqlite":
        try:
            from edupptx.materials.asset_store import AssetStore

            AssetStore(root).migrate_from_split_index(embedding_index=_read_npz_embedding_index(root))
        except Exception as exc:
            PROGRESS_LOGGER.warning("sqlite library.db sync after write failed: {}", str(exc)[:160])
    return index, split_dir


def _target_keyword_cache_key(target: dict[str, Any]) -> str:
    payload = {
        "asset_kind": _clean_text(target.get("asset_kind")),
        "caption": _asset_caption(target),
        "normalized_prompt": _clean_text(target.get("normalized_prompt")),
        "theme": _clean_text(target.get("theme")),
        "topic_refs": _topic_refs_for_asset(target),
        "grade_norm": _clean_text(target.get("grade_norm")),
        "grade_band": _clean_text(target.get("grade_band")),
        "subject": _clean_text(target.get("subject")),
        "page_type": _asset_page_type(target),
        "aspect_ratio": _clean_text(target.get("aspect_ratio")),
        "prompt_route": _match_prompt_route(target.get("prompt_route")),
        "background_route": _match_background_route(target.get("background_route")),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "target:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _build_reuse_target_asset(
    *,
    asset_kind: str,
    prompt: str,
    prompt_route: dict[str, Any] | None,
    theme: str,
    grade: str,
    subject: str,
    page_title: str,
    page_type: str,
    role: str,
    aspect_ratio: str,
    background_route: dict[str, Any] | None = None,
    caption: str = "",
    grade_band: str = "",
) -> dict[str, Any]:
    route = _clean_prompt_route(prompt_route)
    bg_route = _clean_background_route(background_route)
    content_prompt = _clean_text(prompt)
    if asset_kind == "background":
        content_prompt = _strip_background_color_bias_from_prompt(
            content_prompt,
            _clean_text(bg_route.get("background_color_bias")),
        )
    asset_key = "|".join([asset_kind, content_prompt, grade, subject, aspect_ratio])
    grade_info = normalize_grade_info(grade, grade_band)
    target = {
        "asset_id": "target_" + hashlib.sha256(asset_key.encode("utf-8")).hexdigest()[:16],
        "asset_kind": asset_kind,
        "image_path": "",
        "aspect_ratio": aspect_ratio,
        "theme": _clean_text(theme),
        "topic_refs": extract_topic_refs(theme),
        "query": content_prompt,
        "caption": _clean_text(caption) or content_prompt,
        "prompt_route": route,
        "background_route": bg_route,
        "normalized_prompt": content_prompt[:80],
        "context_summary": _default_context_summary(
            asset_kind=asset_kind,
            content_prompt=content_prompt,
            theme=theme,
            page_title=page_title,
            page_type=page_type,
        ),
        "teaching_intent": _default_teaching_intent(asset_kind=asset_kind),
        "page_type": _clean_text(page_type),
        "subject_hint": _clean_text(subject),
        "grade_hint": _clean_text(grade),
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
        "subject": _normalize_subject_value(subject),
    }
    _normalize_rich_asset_fields(target, keep_match_keywords=True)
    return target


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


def normalize_grade_info(*texts: Any) -> dict[str, Any]:
    values = list(texts)
    grade_norm = _normalize_grade_norm_value(values[0] if values else "")
    grade_band = _normalize_grade_band_value(values[1] if len(values) > 1 else "")
    if grade_band == _OTHER_GRADE:
        grade_band = grade_band_from_norm(grade_norm)
    return {"grade_norm": grade_norm, "grade_band": grade_band}


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
    grade = _clean_text(meta.get("grade"))
    subject = _clean_text(meta.get("subject"))

    return {
        "theme": topic,
        "grade": grade,
        "subject": subject,
        "audience": audience,
    }


def _load_session_reuse_target_keyword_cache(session_dir: Path) -> dict[str, Any]:
    try:
        from edupptx.materials.reuse_query_cache import load_reuse_query_cache

        keyword_cache, _embedding_cache = load_reuse_query_cache(session_dir)
    except Exception:
        return {}
    return keyword_cache if isinstance(keyword_cache, dict) else {}


def _metadata_seed_from_reuse_target(
    target: dict[str, Any],
    target_keyword_cache: dict[str, Any] | None,
) -> dict[str, Any]:
    if not target_keyword_cache:
        return {}
    cached = target_keyword_cache.get(_target_keyword_cache_key(target))
    return cached if isinstance(cached, dict) else {}


def _apply_reuse_target_metadata_seed(asset: dict[str, Any], seed: dict[str, Any]) -> None:
    if not seed:
        return
    fields = (
        _BACKGROUND_REUSE_TARGET_METADATA_FIELDS
        if _is_background_asset(asset)
        else _PAGE_REUSE_TARGET_METADATA_FIELDS
    )
    copied = False
    for key in fields:
        if key not in seed:
            continue
        value = seed.get(key)
        if isinstance(value, bool):
            asset[key] = value
            copied = True
        elif isinstance(value, (list, dict)):
            if value:
                asset[key] = deepcopy(value)
                copied = True
        elif _clean_text(value):
            asset[key] = value
            copied = True
    if copied:
        asset[_REUSE_TARGET_METADATA_SEEDED_FIELD] = True


def _build_background_asset(
    *,
    root: Path,
    session_dir: Path,
    plan_path: Path,
    materials_dir: Path,
    context: dict[str, str],
    plan: dict[str, Any],
    reused_image_paths: set[str] | None = None,
    target_keyword_cache: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from edupptx.materials.background_generator import build_background_content_prompt

    visual = _dict(plan.get("visual"))
    prompt = _clean_text(build_background_content_prompt(visual))
    image_path = materials_dir / "background.png"
    if not prompt or not image_path.exists():
        return None
    if _is_reused_image_path(image_path, session_dir, reused_image_paths):
        return None

    background_route = _build_background_route(plan)
    target = _build_reuse_target_asset(
        asset_kind="background",
        prompt=prompt,
        prompt_route=None,
        background_route=background_route,
        theme=context.get("theme", ""),
        grade=context.get("grade", ""),
        subject=context.get("subject", ""),
        page_title="",
        page_type="",
        role="background",
        aspect_ratio="16:9",
    )
    return _make_asset(
        root=root,
        session_dir=session_dir,
        image_path=image_path,
        prompt=prompt,
        context=context,
        asset_kind="background",
        page_title="",
        role="background",
        aspect_ratio="16:9",
        background_route=background_route,
        metadata_seed=_metadata_seed_from_reuse_target(target, target_keyword_cache),
    )


def _build_background_route(plan: dict[str, Any]) -> dict[str, Any]:
    visual = _dict(plan.get("visual"))
    routing = _dict(plan.get("style_routing"))
    route = {
        "template_family": routing.get("template_family"),
        "style_name": routing.get("style_name"),
        "palette_id": routing.get("palette_id"),
        "primary_color": visual.get("primary_color"),
        "secondary_color": visual.get("secondary_color"),
        "accent_color": visual.get("accent_color"),
        "card_bg_color": visual.get("card_bg_color"),
        "secondary_bg_color": visual.get("secondary_bg_color"),
        "background_color_bias": visual.get("background_color_bias"),
    }
    color_terms = [
        visual.get("primary_color"),
        visual.get("secondary_color"),
        visual.get("accent_color"),
        visual.get("background_color_bias"),
    ]
    cleaned = _clean_background_route(route)
    terms = _dedupe_terms([_clean_text(item) for item in color_terms if _clean_text(item)])
    if terms:
        cleaned["color_terms"] = terms
    return cleaned


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
    target_keyword_cache: dict[str, Any] | None = None,
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
        prompt_route = _clean_prompt_route(image_need.get("prompt_route"))
        caption = _clean_text(image_need.get("caption"))

        image_path = _find_page_image_path(materials_dir, page_number, role, role_counts[role])
        if image_path is None:
            continue
        if _is_reused_image_path(image_path, session_dir, reused_image_paths):
            continue

        target = _build_reuse_target_asset(
            asset_kind="page_image",
            prompt=prompt,
            prompt_route=prompt_route,
            background_route=None,
            theme=context.get("theme", ""),
            grade=context.get("grade", ""),
            subject=context.get("subject", ""),
            page_title=_clean_text(page.get("title")),
            page_type=_clean_text(page.get("page_type")),
            role=role,
            aspect_ratio=_clean_text(image_need.get("aspect_ratio")),
            caption=caption,
        )

        yield _make_asset(
            root=root,
            session_dir=session_dir,
            image_path=image_path,
            prompt=prompt,
            prompt_route=prompt_route,
            context=context,
            asset_kind="page_image",
            page_title=_clean_text(page.get("title")),
            role=role,
            aspect_ratio=_clean_text(image_need.get("aspect_ratio")),
            page_type=_clean_text(page.get("page_type")),
            caption=caption,
            metadata_seed=_metadata_seed_from_reuse_target(target, target_keyword_cache),
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
    image_path: Path,
    prompt: str,
    context: dict[str, str],
    asset_kind: str,
    page_title: str,
    role: str = "",
    aspect_ratio: str = "",
    page_type: str = "",
    prompt_route: dict[str, Any] | None = None,
    background_route: dict[str, Any] | None = None,
    caption: str = "",
    metadata_seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rel_image_path = _relative_path(image_path, root)
    route = _clean_prompt_route(prompt_route)
    bg_route = _clean_background_route(background_route)
    content_prompt = _clean_text(prompt)
    asset_caption = _clean_text(caption)
    if asset_kind == "background":
        content_prompt = _strip_background_color_bias_from_prompt(
            content_prompt,
            _clean_text(bg_route.get("background_color_bias")),
        )
    asset_key = "|".join(
        [
            session_dir.name,
            asset_kind,
            rel_image_path,
            content_prompt,
            context.get("theme", ""),
            context.get("grade", ""),
            context.get("subject", ""),
        ]
    )
    asset_id = "aiimg_" + hashlib.sha256(asset_key.encode("utf-8")).hexdigest()[:20]
    grade_info = normalize_grade_info(context.get("grade"), "")
    normalized_prompt = content_prompt[:80]
    context_summary = _default_context_summary(
        asset_kind=asset_kind,
        content_prompt=content_prompt,
        theme=context.get("theme", ""),
        page_title=page_title,
        page_type=page_type,
    )
    teaching_intent = _default_teaching_intent(asset_kind=asset_kind, page_type=page_type)
    topic_refs = extract_topic_refs(context.get("theme", ""))
    if asset_kind == "background":
        background_asset = {
            "asset_id": asset_id,
            "asset_kind": "background",
            "image_path": rel_image_path,
            "aspect_ratio": aspect_ratio,
            "theme": context.get("theme", ""),
            "subject_hint": context.get("subject", ""),
            "grade_hint": context.get("grade", ""),
            "subject": _normalize_subject_value(context.get("subject", "")),
            "grade_norm": grade_info["grade_norm"],
            "grade_band": grade_info["grade_band"],
            "topic_refs": topic_refs,
            "content_prompt": content_prompt,
            "background_route": bg_route,
            "normalized_prompt": normalized_prompt,
            "context_summary": context_summary,
            "teaching_intent": teaching_intent,
        }
        _apply_reuse_target_metadata_seed(background_asset, metadata_seed or {})
        return background_asset

    # the field from the VLM step — reuse matching that happens before VLM has
    page_image_asset: dict[str, Any] = {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "image_path": rel_image_path,
        "aspect_ratio": aspect_ratio,
        "page_type": page_type,
        "theme": context.get("theme", ""),
        "subject_hint": context.get("subject", ""),
        "grade_hint": context.get("grade", ""),
        "subject": _normalize_subject_value(context.get("subject", "")),
        "grade_norm": grade_info["grade_norm"],
        "grade_band": grade_info["grade_band"],
        "topic_refs": topic_refs,
        "caption": asset_caption or content_prompt,
        "context_summary": context_summary,
        "teaching_intent": teaching_intent,
        "duplicate_asset_ids": [],
    }
    _apply_reuse_target_metadata_seed(page_image_asset, metadata_seed or {})
    return page_image_asset


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())
