"""复用层物化：命中后把复用图按 transform_policy 落地到 session（裁剪/padding/blur/微拉伸）、写复用清单、从 plan 评估批量复用匹配。函数体逐字一致。"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

from loguru import logger as PROGRESS_LOGGER

from edupptx.reuse._util import (
    _clean_text,
    _dict,
)
from edupptx.reuse._constants import (
    DEFAULT_KEYWORD_BATCH_SIZE,
    DEFAULT_REUSE_MAX_WORKERS,
    STRICT_REUSE_MAX_PER_SESSION,
)
from edupptx.reuse._scoring import (
    _optional_int,
    _ratio_value,
)
from edupptx.reuse._embedding import (
    _relative_output_path,
)
from edupptx.reuse._review import (
    _log_snippet,
)
from edupptx.reuse._build import (
    _build_background_route,
    _build_reuse_target_asset,
)
from edupptx.reuse._decide import (
    _finalize_reuse_candidate_collection,
    _is_strict_reuse_limited_asset,
    _load_reuse_library_for_search,
    _match_llm_reuse_review_performed,
    _match_transform_policy,
    _normalize_reuse_debug_mode,
    _normalize_reuse_library_dirs,
    _strict_reuse_occupancy_ids,
    find_reusable_ai_image_asset,
    record_reused_ai_image_asset,
)
from edupptx.reuse._keywords import (
    _prewarm_reuse_target_keywords,
)
from edupptx.reuse._decide import (
    _finalize_reuse_candidate_collection,
    _is_strict_reuse_limited_asset,
    _load_reuse_library_for_search,
    _match_llm_reuse_review_performed,
    _match_transform_policy,
    _normalize_reuse_debug_mode,
    _normalize_reuse_library_dirs,
    _strict_reuse_occupancy_ids,
    find_reusable_ai_image_asset,
    record_reused_ai_image_asset,
)


def mark_reused_ai_image_asset_in_session(
    match: dict[str, Any],
    reuse_session_state: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an accepted match in the current in-memory reuse session state."""

    if reuse_session_state is None:
        return {}
    asset = _dict(match.get("asset"))
    if not _is_strict_reuse_limited_asset(asset):
        return {
            "enabled": True,
            "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
            "limited": False,
            "decision": "not_limited",
        }

    counts = reuse_session_state.setdefault("strict_asset_use_counts", {})
    used_by = reuse_session_state.setdefault("strict_asset_used_by", {})
    ids = _strict_reuse_occupancy_ids(asset)
    used_count_before = max([int(_dict(counts).get(asset_id) or 0) for asset_id in ids] or [0])
    context_payload = context or {}
    for asset_id in ids:
        counts[asset_id] = int(counts.get(asset_id) or 0) + 1
        used_by.setdefault(asset_id, []).append(context_payload)
    used_count_after = max([int(_dict(counts).get(asset_id) or 0) for asset_id in ids] or [0])
    occupancy = {
        "enabled": True,
        "max_per_session": STRICT_REUSE_MAX_PER_SESSION,
        "limited": True,
        "asset_ids": ids,
        "used_count_before": used_count_before,
        "used_count_after": used_count_after,
        "decision": "accepted_within_limit",
    }
    match["strict_reuse_occupancy"] = occupancy
    return occupancy


def materialize_reused_ai_image_asset(
    *,
    session_dir: str | Path,
    session_image_path: str | Path,
    match: dict[str, Any],
) -> None:
    """Copy or derive a reusable image according to its aspect transform policy."""

    dest = Path(session_image_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    reuse_image_path = Path(_clean_text(match.get("candidate_image_path"))).expanduser()
    transform_policy = _match_transform_policy(match)
    if _clean_text(transform_policy.get("decision")) == "reject":
        reason = _clean_text(transform_policy.get("reason")) or "aspect_transform_rejected"
        raise ValueError(f"refusing to materialize rejected AI image reuse match: {reason}")
    mode = _clean_text(transform_policy.get("mode")) or "copy"

    try:
        if mode == "copy":
            shutil.copy2(reuse_image_path, dest)
        else:
            _write_transformed_reuse_image(reuse_image_path, dest, transform_policy)
    except Exception:
        if mode == "transparent_pad":
            raise
        shutil.copy2(reuse_image_path, dest)

    record_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=dest,
        match=match,
    )


def evaluate_ai_image_reuse_matches_from_plan(
    *,
    plan_path: str | Path,
    library_dir: str | Path | list[str | Path] | tuple[str | Path, ...],
    keyword_client: Any | None = None,
    debug_path: str | Path | None = None,
    include_background: bool = True,
    materialize_matches: bool = False,
    llm_review_enabled: bool = True,
    reuse_debug_mode: str = "full",
    reuse_search_concurrency: int = DEFAULT_REUSE_MAX_WORKERS,
    target_keyword_batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> dict[str, Any]:
    """Evaluate reuse matches from a plan without generating or ingesting assets.

    When ``materialize_matches`` is true, accepted reusable-library matches are
    copied into the plan session's ``materials/`` directory. This still does not
    generate new images or update the central asset library.
    """

    from edupptx.materials.background_generator import build_background_content_prompt
    from edupptx.materials.image_prompt_router import build_routed_image_needs
    from edupptx.models import PlanningDraft, iter_image_slot_keys

    plan_file = Path(plan_path).expanduser().resolve()
    library_roots = _normalize_reuse_library_dirs(library_dir)
    data = json.loads(plan_file.read_text(encoding="utf-8"))
    draft = PlanningDraft.model_validate(data)
    plan_data = draft.model_dump()
    context = {
        "theme": _clean_text(draft.meta.topic),
        "grade": _clean_text(getattr(draft.meta, "grade", "")),
        "subject": _clean_text(getattr(draft.meta, "subject", "")),
        "grade_band": _clean_text(getattr(draft.meta, "grade_band", "")),
    }
    reuse_session_state: dict[str, Any] = {
        "strict_asset_use_counts": {},
        "strict_asset_used_by": {},
    }
    reuse_search_context = ReuseSearchContext()
    reuse_debug_mode = _normalize_reuse_debug_mode(reuse_debug_mode)
    checks: list[dict[str, Any]] = []
    materialized_count = 0
    specs: list[dict[str, Any]] = []
    if include_background:
        background_prompt = build_background_content_prompt(draft.visual)
        specs.append(
            {
                "asset_kind": "background",
                "page_number": None,
                "slot_key": "background",
                "need": None,
                "prompt": background_prompt,
                "prompt_route": None,
                "background_route": _build_background_route(plan_data),
                "page_title": "",
                "page_type": "",
                "role": "",
                "aspect_ratio": "16:9",
                "debug_context": {"check_type": "plan_reuse_match", "asset_kind": "background"},
            }
        )
    for page in draft.pages:
        routed_needs = build_routed_image_needs(draft, page)
        for slot_key, need in iter_image_slot_keys(routed_needs):
            if need.source == "ai_generate":
                specs.append(
                    {
                        "asset_kind": "page_image",
                        "page_number": page.page_number,
                        "slot_key": slot_key,
                        "need": need,
                        "prompt": need.query,
                        "prompt_route": need.prompt_route,
                        "background_route": None,
                        "page_title": page.title,
                        "page_type": page.page_type,
                        "role": need.role,
                        "aspect_ratio": need.aspect_ratio,
                        "debug_context": {
                            "check_type": "plan_reuse_match",
                            "asset_kind": "page_image",
                            "page_number": page.page_number,
                            "slot_key": slot_key,
                            "aspect_ratio": need.aspect_ratio,
                        },
                    }
                )

    total_checks = len(specs)
    page_image_count = sum(1 for spec in specs if spec["asset_kind"] == "page_image")
    reuse_search_concurrency = max(1, int(reuse_search_concurrency or 1))
    PROGRESS_LOGGER.info(
        "AI image reuse plan check start: plan={}, checks={}, background={}, page_images={}, libraries={}, "
        "keywords={}, materialize={}, search_concurrency={}",
        plan_file,
        total_checks,
        bool(include_background),
        page_image_count,
        [str(root) for root in library_roots],
        bool(keyword_client),
        bool(materialize_matches),
        reuse_search_concurrency,
    )

    for root in library_roots:
        _load_reuse_library_for_search(root, reuse_search_context)

    targets = [
        _build_reuse_target_asset(
            asset_kind=spec["asset_kind"],
            prompt=spec["prompt"],
            prompt_route=spec["prompt_route"],
            background_route=spec["background_route"],
            theme=context["theme"],
            grade=context["grade"],
            subject=context["subject"],
            grade_band=context["grade_band"],
            page_title=spec["page_title"],
            page_type=spec["page_type"],
            role=spec["role"],
            aspect_ratio=spec["aspect_ratio"],
        )
        for spec in specs
    ]
    _prewarm_reuse_target_keywords(
        targets,
        keyword_client,
        reuse_search_context.target_keyword_cache,
        batch_size=target_keyword_batch_size,
    )

    def collect_candidates(spec: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} candidate search start: background prompt={}",
                ordinal,
                total_checks,
                _log_snippet(spec["prompt"], 96),
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} candidate search start: page={}, slot={}, role={}, aspect={}, query={}",
                ordinal,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
                _clean_text(spec["role"]) or "unknown",
                _clean_text(spec["aspect_ratio"]) or "unknown",
                _log_snippet(spec["prompt"], 96),
            )
        collection = find_reusable_ai_image_asset(
            library_dir=library_dir,
            asset_kind=spec["asset_kind"],
            prompt=spec["prompt"],
            prompt_route=spec["prompt_route"],
            background_route=spec["background_route"],
            theme=context["theme"],
            grade=context["grade"],
            subject=context["subject"],
            grade_band=context["grade_band"],
            page_title=spec["page_title"],
            page_type=spec["page_type"],
            role=spec["role"],
            aspect_ratio=spec["aspect_ratio"],
            keyword_client=None,
            debug_path=None,
            debug_context=spec["debug_context"],
            reuse_session_state=None,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
            reuse_search_context=reuse_search_context,
            _collect_candidates_only=True,
        )
        candidate_count = (
            len(collection.get("candidates") or [])
            if isinstance(collection, dict)
            else 0
        )
        PROGRESS_LOGGER.info(
            "AI image reuse check {}/{} candidate search done: asset_kind={}, candidates={}",
            ordinal,
            total_checks,
            spec["asset_kind"],
            candidate_count,
        )
        return collection

    collected: list[dict[str, Any] | None] = [None] * len(specs)
    if specs and reuse_search_concurrency > 1:
        max_workers = min(reuse_search_concurrency, len(specs))
        PROGRESS_LOGGER.info(
            "AI image reuse candidate searches parallel start: checks={}, workers={}",
            len(specs),
            max_workers,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(collect_candidates, spec, index + 1): index
                for index, spec in enumerate(specs)
            }
            for future in as_completed(futures):
                index = futures[future]
                collected[index] = future.result()
        PROGRESS_LOGGER.info("AI image reuse candidate searches parallel done: checks={}", len(specs))
    else:
        for index, spec in enumerate(specs):
            collected[index] = collect_candidates(spec, index + 1)

    for index, spec in enumerate(specs):
        current_check = index + 1
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} policy start: background",
                current_check,
                total_checks,
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} policy start: page={}, slot={}",
                current_check,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
            )
        match = _finalize_reuse_candidate_collection(
            collected[index],
            debug_path=debug_path,
            keyword_client=keyword_client,
            reuse_session_state=reuse_session_state,
            llm_review_enabled=llm_review_enabled,
            reuse_debug_mode=reuse_debug_mode,
        )
        session_image_path: Path | None = None
        if match:
            if materialize_matches:
                session_image_path = _materialize_plan_reuse_match(
                    session_dir=plan_file.parent,
                    asset_kind=spec["asset_kind"],
                    page_number=spec["page_number"],
                    slot_key=spec["slot_key"],
                    match=match,
                )
                materialized_count += 1
            mark_context = dict(spec["debug_context"])
            mark_context["session_image_path"] = str(session_image_path or "")
            mark_reused_ai_image_asset_in_session(match, reuse_session_state, mark_context)
        checks.append(
            _plan_reuse_check_record(
                spec["asset_kind"],
                spec["page_number"],
                spec["slot_key"],
                spec["need"].model_dump() if spec["need"] is not None else None,
                match,
                session_image_path=session_image_path,
            )
        )
        if spec["asset_kind"] == "background":
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} done: background matched={}, asset_id={}, reason={}, materialized={}",
                current_check,
                total_checks,
                bool(match),
                _match_asset_id(match),
                _match_decision_reason(match),
                bool(session_image_path),
            )
        else:
            PROGRESS_LOGGER.info(
                "AI image reuse check {}/{} done: page={}, slot={}, matched={}, asset_id={}, score={}, reason={}, "
                "materialized={}",
                current_check,
                total_checks,
                spec["page_number"],
                spec["slot_key"],
                bool(match),
                _match_asset_id(match),
                _match_score(match),
                _match_decision_reason(match),
                bool(session_image_path),
            )

    matched = [item for item in checks if item["matched"]]
    PROGRESS_LOGGER.info(
        "AI image reuse plan check complete: matched={}/{}, materialized={}, debug_path={}",
        len(matched),
        len(checks),
        materialized_count,
        debug_path or "",
    )
    return {
        "schema_version": 1,
        "asset_root": _relative_output_path(library_roots[0]),
        "asset_roots": [_relative_output_path(root) for root in library_roots],
        "generated_images": False,
        "updated_asset_store": False,
        "materialize_matches": materialize_matches,
        "materialized_count": materialized_count,
        "reuse_search_concurrency": reuse_search_concurrency,
        "target_keyword_batch_size": target_keyword_batch_size,
        "materials_dir": _relative_output_path(plan_file.parent / "materials") if materialize_matches else "",
        "check_count": len(checks),
        "matched_count": len(matched),
        "unmatched_count": len(checks) - len(matched),
        "strict_asset_use_counts": reuse_session_state["strict_asset_use_counts"],
        "checks": checks,
    }


def _materialize_plan_reuse_match(
    *,
    session_dir: Path,
    asset_kind: str,
    page_number: int | None,
    slot_key: str,
    match: dict[str, Any],
) -> Path:
    materials_dir = session_dir / "materials"
    if asset_kind == "background":
        dest = materials_dir / "background.png"
    else:
        suffix = Path(_clean_text(match.get("candidate_image_path"))).suffix.lower() or ".img"
        dest = materials_dir / f"page_{int(page_number or 0):02d}_{slot_key}{suffix}"
    materialize_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=dest,
        match=match,
    )
    return dest


def _plan_reuse_check_record(
    asset_kind: str,
    page_number: int | None,
    slot_key: str,
    need: dict[str, Any] | None,
    match: dict[str, Any] | None,
    *,
    session_image_path: str | Path | None = None,
) -> dict[str, Any]:
    asset = _dict(match.get("asset")) if match else {}
    return {
        "asset_kind": asset_kind,
        "page_number": page_number,
        "slot_key": slot_key,
        "need": _plan_need_debug_payload(need),
        "matched": match is not None,
        "asset_id": asset.get("asset_id", ""),
        "candidate_image_path": _relative_output_path(match.get("candidate_image_path")) if match else "",
        "reuse_library_dir": _relative_output_path(match.get("library_dir") or match.get("asset_root")) if match else "",
        "session_image_path": _relative_output_path(session_image_path) if session_image_path else "",
        "keyword_score": match.get("keyword_score") if match else None,
        "policy_score": match.get("policy_score") if match else None,
        "reuse_policy": match.get("reuse_policy") if match else {},
        "reuse_audit": match.get("reuse_audit") if match else {},
        "llm_reuse_review_performed": _match_llm_reuse_review_performed(match) if match else False,
        "transform_policy": _match_transform_policy(match) if match else {},
        "strict_reuse_occupancy": match.get("strict_reuse_occupancy") if match else {},
    }


def _plan_need_debug_payload(need: dict[str, Any] | None) -> dict[str, Any]:
    data = _dict(need)
    return {
        key: data.get(key)
        for key in ("query", "role", "aspect_ratio", "prompt_route")
        if key in data
    }


def _match_asset_id(match: dict[str, Any] | None) -> str:
    if not match:
        return ""
    return _clean_text(_dict(match.get("asset")).get("asset_id"))


def _match_score(match: dict[str, Any] | None) -> float | str:
    if not match:
        return ""
    score = match.get("policy_score")
    if score is None:
        score = _dict(match.get("score_details")).get("policy_score")
    if score is None:
        score = match.get("keyword_score")
    if score is None:
        score = _dict(match.get("score_details")).get("score")
    try:
        return round(float(score), 4)
    except (TypeError, ValueError):
        return ""


def _match_decision_reason(match: dict[str, Any] | None) -> str:
    if not match:
        return "no_match"
    policy = _dict(match.get("reuse_policy"))
    return (
        _clean_text(policy.get("reason"))
        or _clean_text(match.get("multi_library_reuse_reason"))
        or "matched"
    )


def _write_transformed_reuse_image(input_path: Path, dest: Path, transform_policy: dict[str, Any]) -> None:
    from PIL import Image

    mode = _clean_text(transform_policy.get("mode")) or "copy"
    target_ratio = _ratio_value(_clean_text(transform_policy.get("target_aspect_ratio")))
    with Image.open(input_path) as img:
        image = img.convert("RGBA") if img.mode not in {"RGB", "RGBA"} else img.copy()
        if target_ratio <= 0:
            image.save(dest)
            return

        if mode == "cover_crop":
            result = _cover_crop_image(image, target_ratio)
        elif mode == "transparent_pad":
            result = _transparent_pad_image(image, target_ratio, _target_size_from_transform_policy(transform_policy))
        elif mode == "contain_pad":
            result = _contain_pad_image(image, target_ratio)
        elif mode == "blur_pad":
            result = _blur_pad_image(image, target_ratio)
        elif mode == "micro_stretch":
            result = _micro_stretch_image(image, target_ratio)
        else:
            result = image

        if dest.suffix.lower() in {".jpg", ".jpeg"} and result.mode == "RGBA":
            background = Image.new("RGB", result.size, _average_rgb(result))
            background.paste(result, mask=result.getchannel("A"))
            result = background
        result.save(dest)


def _target_size_from_transform_policy(transform_policy: dict[str, Any]) -> tuple[int, int] | None:
    width = _optional_int(transform_policy.get("target_width"))
    height = _optional_int(transform_policy.get("target_height"))
    if width and height and width > 0 and height > 0:
        return width, height
    return None


def _cover_crop_image(image: Any, target_ratio: float) -> Any:
    width, height = image.size
    image_ratio = width / max(1, height)
    if image_ratio > target_ratio:
        crop_width = max(1, int(round(height * target_ratio)))
        left = max(0, (width - crop_width) // 2)
        return image.crop((left, 0, left + crop_width, height))
    crop_height = max(1, int(round(width / target_ratio)))
    top = max(0, (height - crop_height) // 2)
    return image.crop((0, top, width, top + crop_height))


def _transparent_pad_image(image: Any, target_ratio: float, target_size: tuple[int, int] | None = None) -> Any:
    from PIL import Image

    source = image.convert("RGBA")
    width, height = source.size
    if target_size is None:
        canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    else:
        canvas_width, canvas_height = target_size

    scale = min(canvas_width / max(1, width), canvas_height / max(1, height))
    scaled_width = max(1, int(round(width * scale)))
    scaled_height = max(1, int(round(height * scale)))
    if (scaled_width, scaled_height) != source.size:
        source = source.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    left = (canvas_width - scaled_width) // 2
    top = (canvas_height - scaled_height) // 2
    canvas.paste(source, (left, top), source)
    return canvas


def _contain_pad_image(image: Any, target_ratio: float) -> Any:
    from PIL import Image

    width, height = image.size
    canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    canvas = Image.new(image.mode, (canvas_width, canvas_height), _average_rgba(image))
    left = (canvas_width - width) // 2
    top = (canvas_height - height) // 2
    canvas.paste(image, (left, top), image if image.mode == "RGBA" else None)
    return canvas


def _blur_pad_image(image: Any, target_ratio: float) -> Any:
    from PIL import ImageFilter

    width, height = image.size
    canvas_width, canvas_height = _contain_canvas_size(width, height, target_ratio)
    background = image.convert("RGB").resize((canvas_width, canvas_height))
    background = background.filter(ImageFilter.GaussianBlur(radius=max(8, min(canvas_width, canvas_height) // 24)))
    foreground = image.convert("RGBA")
    background = background.convert("RGBA")
    left = (canvas_width - width) // 2
    top = (canvas_height - height) // 2
    background.paste(foreground, (left, top), foreground)
    return background


def _micro_stretch_image(image: Any, target_ratio: float) -> Any:
    width, height = image.size
    area = max(1, width * height)
    target_width = max(1, int(round(math.sqrt(area * target_ratio))))
    target_height = max(1, int(round(target_width / target_ratio)))
    return image.resize((target_width, target_height))


def _contain_canvas_size(width: int, height: int, target_ratio: float) -> tuple[int, int]:
    image_ratio = width / max(1, height)
    if image_ratio > target_ratio:
        return width, max(height, int(round(width / target_ratio)))
    return max(width, int(round(height * target_ratio))), height


def _average_rgba(image: Any) -> tuple[int, int, int, int]:
    rgb = _average_rgb(image)
    return rgb[0], rgb[1], rgb[2], 255


def _average_rgb(image: Any) -> tuple[int, int, int]:
    from PIL import ImageStat

    stat = ImageStat.Stat(image.convert("RGB").resize((1, 1)))
    return tuple(int(value) for value in stat.mean[:3])
