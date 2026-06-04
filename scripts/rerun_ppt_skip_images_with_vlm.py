"""Rerun VLM/LLM metadata for selected PPT skip images.

This is a small recovery tool for images already archived under ``skip_image``
or ``skip_images``. It reuses the PPT VLM prompt and strict reuse classifier,
but cannot restore full PPT slide context unless that metadata is already
present in the existing split indexes.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.config import Config
from edupptx.llm_client import create_llm_client, create_vlm_client
from edupptx.materials.ai_image_asset_db import write_ai_image_match_index
from edupptx.materials.reuse_policy import reuse_level_from_material_category
from scripts.build_ppt_materials_library import (
    DEFAULT_ORIGINAL_IMAGE_DIR,
    DEFAULT_SKIP_IMAGE_DIR,
    DEFAULT_IMAGE_DIR,
    DEFAULT_PPT_KEYWORD_BATCH_SIZE,
    RawPptImage,
    _annotate_and_build_ppt_asset,
    _clean_text,
    _enrich_single_ppt_asset_with_llm,
    _ppt_aspect_ratio_name,
    _save_ppt_image_derivatives,
)


DEFAULT_LIBRARY_DIR = Path("materials_library_ppt")
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
DEFAULT_REPORT_FILENAME = "skip_image_vlm_rerun_report.json"
SPLIT_INDEX_FILENAMES = (
    "background.json",
    "C00_strict_text_problem_skip.json",
    "C01_irreplaceable_entity_event_action.json",
    "C02_generic_subject_object.json",
    "C03_scene_decor_container.json",
)


def rerun_ppt_skip_images_with_vlm(
    *,
    library_dir: str | Path = DEFAULT_LIBRARY_DIR,
    skip_dir: str | Path | None = None,
    images: list[str | Path] | None = None,
    report_path: str | Path | None = None,
    apply: bool = False,
    rebuild_embedding: bool = False,
    env_file: str | Path = ".env",
    vlm_max_side: int = 1280,
    keyword_batch_size: int = DEFAULT_PPT_KEYWORD_BATCH_SIZE,
    vlm_client: Any | None = None,
    keyword_client: Any | None = None,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    source_dir = _resolve_skip_dir(library_root, skip_dir)
    selected_images = _select_skip_images(library_root, source_dir, images)
    output_report = (
        Path(report_path).expanduser().resolve()
        if report_path
        else library_root / DEFAULT_REPORT_FILENAME
    )

    config: Config | None = None
    if vlm_client is None:
        config = Config.from_env(env_file)
        if not config.vlm_api_key or not config.vlm_model:
            raise RuntimeError("VLM_APIKEY/VLM_MODEL not configured")
        vlm_client = create_vlm_client(config)
    if keyword_client is None:
        if config is None:
            config = Config.from_env(env_file)
        if not config.llm_api_key or not config.llm_model:
            raise RuntimeError("GEN_APIKEY/GEN_MODEL not configured")
        keyword_client = create_llm_client(config, web_search=False)

    existing_assets = _read_all_split_assets(library_root)
    existing_by_id = {
        _clean_text(asset.get("asset_id")): deepcopy(asset)
        for asset in existing_assets
        if isinstance(asset, dict) and _clean_text(asset.get("asset_id"))
    }

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "applied": bool(apply),
        "library_dir": str(library_root),
        "skip_dir": str(source_dir),
        "image_count": len(selected_images),
        "updated_count": 0,
        "reusable_count": 0,
        "c00_count": 0,
        "background_count": 0,
        "warnings": [],
        "assets": [],
    }

    updated_assets: dict[str, dict[str, Any]] = {}
    for image_path in selected_images:
        asset_id = _asset_id_from_skip_image(image_path)
        existing = existing_by_id.get(asset_id, {})
        try:
            asset, warnings = _rerun_single_image(
                image_path,
                asset_id=asset_id,
                library_root=library_root,
                source_dir=source_dir,
                existing_asset=existing,
                vlm_client=vlm_client,
                keyword_client=keyword_client,
                vlm_max_side=vlm_max_side,
                keyword_batch_size=keyword_batch_size,
            )
            report["warnings"].extend(warnings)
        except Exception as exc:
            report["warnings"].append(f"{asset_id} rerun_failed:{type(exc).__name__}: {exc}")
            continue

        if apply:
            _materialize_asset_image(
                asset,
                image_path=image_path,
                library_root=library_root,
            )
        updated_assets[asset_id] = asset
        report["updated_count"] += 1
        if asset.get("asset_kind") == "background":
            report["background_count"] += 1
        elif reuse_level_from_material_category(asset.get("strict_reuse_group")) == "skip":
            report["c00_count"] += 1
        else:
            report["reusable_count"] += 1
        report["assets"].append(
            {
                "asset_id": asset_id,
                "source_image": str(image_path),
                "strict_reuse_group": asset.get("strict_reuse_group"),
                "asset_kind": asset.get("asset_kind"),
                "query": asset.get("query"),
                "caption": asset.get("caption"),
                "image_path": asset.get("image_path"),
                "original_image_path": asset.get("original_image_path"),
            }
        )

    if apply:
        merged_by_id = dict(existing_by_id)
        merged_by_id.update(updated_assets)
        db = {
            "schema_version": 10,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "output_root": str(library_root),
            "asset_count": len(merged_by_id),
            "assets": list(merged_by_id.values()),
            "warnings": report["warnings"],
            "ppt_extractor": {
                "schema_version": 10,
                "method": "ppt_skip_image_vlm_rerun",
                "source_root": str(source_dir),
                "image_dir": DEFAULT_IMAGE_DIR,
            },
        }
        _index, split_dir = write_ai_image_match_index(
            db,
            library_root,
            write_embedding_index=rebuild_embedding,
        )
        report["updated_split_indexes"] = str(split_dir)
        report["embedding_rebuild"] = bool(rebuild_embedding)

    report["warning_count"] = len(report["warnings"])
    report["report_path"] = str(output_report)
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _rerun_single_image(
    image_path: Path,
    *,
    asset_id: str,
    library_root: Path,
    source_dir: Path,
    existing_asset: dict[str, Any],
    vlm_client: Any,
    keyword_client: Any,
    vlm_max_side: int,
    keyword_batch_size: int,
) -> tuple[dict[str, Any], list[str]]:
    image_fields = _image_fields(image_path)
    image_rel = f"{DEFAULT_IMAGE_DIR}/{asset_id}.png"
    original_rel = f"{DEFAULT_ORIGINAL_IMAGE_DIR}/{asset_id}.png"
    if existing_asset and reuse_level_from_material_category(existing_asset.get("strict_reuse_group")) == "skip":
        image_rel = _library_rel_or_skip_rel(library_root, image_path)
        original_rel = _clean_text(existing_asset.get("original_image_path")) or image_rel

    item = _raw_item_for_skip_image(image_path, asset_id=asset_id, image_fields=image_fields)
    meta = _meta_for_skip_image(image_path, asset_id=asset_id, existing_asset=existing_asset)
    context = _context_for_skip_image(existing_asset)
    asset, vlm_warnings = _annotate_and_build_ppt_asset(
        vlm_client=vlm_client,
        image_path=image_path,
        asset_id=asset_id,
        image_rel=image_rel,
        original_image_rel=original_rel,
        image_fields=image_fields,
        item=item,
        meta=meta,
        context=context,
        vlm_max_side=vlm_max_side,
    )
    asset, llm_warnings = _enrich_single_ppt_asset_with_llm(
        asset,
        keyword_client,
        batch_size=keyword_batch_size,
    )
    if reuse_level_from_material_category(asset.get("strict_reuse_group")) == "skip":
        skip_rel = _library_rel_or_skip_rel(library_root, image_path)
        asset["image_path"] = skip_rel
        asset["original_image_path"] = skip_rel
    else:
        asset["image_path"] = image_rel
        asset["original_image_path"] = original_rel
    return asset, [*vlm_warnings, *llm_warnings]


def _materialize_asset_image(asset: dict[str, Any], *, image_path: Path, library_root: Path) -> None:
    if reuse_level_from_material_category(asset.get("strict_reuse_group")) == "skip":
        return
    image_rel = _clean_text(asset.get("image_path"))
    original_rel = _clean_text(asset.get("original_image_path"))
    if not image_rel or not original_rel:
        return
    image_fields = _save_ppt_image_derivatives(
        image_path.read_bytes(),
        original_path=library_root / original_rel,
        runtime_path=library_root / image_rel,
    )
    asset.update(image_fields)


def _read_all_split_assets(library_root: Path) -> list[dict[str, Any]]:
    split_dir = library_root / STRICT_REUSE_INDEX_DIRNAME
    assets_by_id: dict[str, dict[str, Any]] = {}
    for filename in SPLIT_INDEX_FILENAMES:
        path = split_dir / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        group = _clean_text(payload.get("strict_reuse_group")) or path.stem
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            continue
        for item in raw_assets:
            if not isinstance(item, dict) or item.get("secondary_projection") is True:
                continue
            asset = deepcopy(item)
            asset_id = _clean_text(asset.get("asset_id"))
            if not asset_id:
                continue
            if filename == "background.json":
                asset["asset_kind"] = "background"
            asset.setdefault("strict_reuse_group", group)
            assets_by_id[asset_id] = asset
    return list(assets_by_id.values())


def _resolve_skip_dir(library_root: Path, skip_dir: str | Path | None) -> Path:
    if skip_dir is not None:
        path = Path(skip_dir).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"skip directory not found: {path}")
        return path
    for dirname in ("skip_image", DEFAULT_SKIP_IMAGE_DIR):
        path = library_root / dirname
        if path.exists():
            return path.resolve()
    raise FileNotFoundError(f"no skip_image or {DEFAULT_SKIP_IMAGE_DIR} directory under {library_root}")


def _select_skip_images(library_root: Path, source_dir: Path, images: list[str | Path] | None) -> list[Path]:
    if images:
        selected = []
        for value in images:
            path = Path(value).expanduser()
            if not path.is_absolute():
                candidate = (Path.cwd() / path).resolve()
                if not candidate.exists():
                    candidate = (source_dir / path).resolve()
                path = candidate
            else:
                path = path.resolve()
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"image not found: {path}")
            selected.append(path)
        return sorted(dict.fromkeys(selected))

    files = sorted(path for path in source_dir.iterdir() if path.is_file())
    by_asset: dict[str, Path] = {}
    for path in files:
        asset_id = _asset_id_from_skip_image(path)
        if path.stem.endswith("_original") or asset_id not in by_asset:
            by_asset[asset_id] = path
    return sorted(by_asset.values())


def _asset_id_from_skip_image(path: Path) -> str:
    stem = path.stem
    if stem.endswith("_original"):
        stem = stem[: -len("_original")]
    return stem


def _image_fields(path: Path) -> dict[str, int | str]:
    with Image.open(path) as img:
        rgba = img.convert("RGBA")
        aspect_ratio = _ppt_aspect_ratio_name(rgba.width, rgba.height)
        return {
            "actual_width": rgba.width,
            "actual_height": rgba.height,
            "padded_width": rgba.width,
            "padded_height": rgba.height,
            "aspect_ratio": aspect_ratio,
        }


def _raw_item_for_skip_image(path: Path, *, asset_id: str, image_fields: dict[str, int | str]) -> RawPptImage:
    width = int(image_fields.get("actual_width") or 0)
    height = int(image_fields.get("actual_height") or 0)
    return RawPptImage(
        pptx_path=Path(f"{asset_id}.pptx"),
        slide_no=0,
        shape_idx=0,
        source_media_path=path.name,
        suffix=path.suffix or ".png",
        data=path.read_bytes(),
        sha256=asset_id,
        width=width,
        height=height,
        mode="RGBA",
        bbox={
            "x": 0.0,
            "y": 0.0,
            "width": float(width),
            "height": float(height),
            "unit": "image_pixels",
            "area_ratio": 1.0,
        },
        slide_text="",
        slide_title_guess="",
    )


def _meta_for_skip_image(path: Path, *, asset_id: str, existing_asset: dict[str, Any]) -> dict[str, Any]:
    refs = existing_asset.get("source_pptx_refs") if isinstance(existing_asset, dict) else None
    ref = refs[0] if isinstance(refs, list) and refs and isinstance(refs[0], dict) else {}
    return {
        "id": _clean_text(ref.get("pptx_id")),
        "period_id": _clean_text(ref.get("period_id")),
        "file_name": _clean_text(ref.get("file_name")) or f"{asset_id}.pptx",
        "file_path": _clean_text(ref.get("file_path")) or str(path),
        "description": _clean_text(existing_asset.get("context_summary")),
        "subject": _clean_text(existing_asset.get("subject")),
        "grade": _clean_text(existing_asset.get("grade_norm")),
        "grade_band": _clean_text(existing_asset.get("grade_band")),
    }


def _context_for_skip_image(existing_asset: dict[str, Any]) -> dict[str, Any]:
    text = _clean_text(existing_asset.get("context_summary"))
    return {
        "slide_no": 0,
        "shape_idx": 0,
        "slide_text": text,
        "slide_title_guess": _clean_text(existing_asset.get("caption")) or text[:40],
        "markdown_excerpt": "",
    }


def _library_rel_or_skip_rel(library_root: Path, image_path: Path) -> str:
    try:
        return image_path.resolve().relative_to(library_root).as_posix()
    except ValueError:
        return f"{DEFAULT_SKIP_IMAGE_DIR}/{image_path.name}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR)
    parser.add_argument("--skip-dir", type=Path, default=None)
    parser.add_argument("--image", action="append", default=[], help="Image path or filename; repeatable.")
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--vlm-max-side", type=int, default=1280)
    parser.add_argument("--keyword-batch-size", type=int, default=DEFAULT_PPT_KEYWORD_BATCH_SIZE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rebuild-embedding", action="store_true")
    args = parser.parse_args(argv)

    report = rerun_ppt_skip_images_with_vlm(
        library_dir=args.library_dir,
        skip_dir=args.skip_dir,
        images=args.image or None,
        report_path=args.report_path,
        apply=args.apply,
        rebuild_embedding=args.rebuild_embedding,
        env_file=args.env_file,
        vlm_max_side=args.vlm_max_side,
        keyword_batch_size=args.keyword_batch_size,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
