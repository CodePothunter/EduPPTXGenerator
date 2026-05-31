# -*- coding: utf-8 -*-
"""Add pixel dimensions and optional transparent padding to PPT split indexes.

The script reads ``<library>/strict_reuse_indexes/*.json`` and writes
``actual_width`` / ``actual_height`` onto each asset from the real image file.
With ``--write-padded`` it also preserves the original image, rewrites
``image_path`` to a transparent-padded runtime image, and refreshes the
canonical ``aspect_ratio`` / ``padded_width`` / ``padded_height`` fields.

Typical usage:

    python scripts/update_ppt_actual_dimensions.py --library-dir materials_library_ppt
    python scripts/update_ppt_actual_dimensions.py --library-dir materials_library_ppt --write-padded
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image


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


def update_ppt_actual_dimensions(
    library_dir: str | Path,
    *,
    dry_run: bool = False,
    write_padded: bool = False,
) -> dict[str, Any]:
    library_root = Path(library_dir).expanduser().resolve()
    split_dir = library_root / "strict_reuse_indexes"
    if not split_dir.exists():
        raise FileNotFoundError(f"split index directory not found: {split_dir}")

    report: dict[str, Any] = {
        "library_dir": str(library_root),
        "split_dir": str(split_dir),
        "dry_run": bool(dry_run),
        "write_padded": bool(write_padded),
        "index_count": 0,
        "asset_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "original_created_count": 0,
        "padded_written_count": 0,
        "missing_image_count": 0,
        "failed_image_count": 0,
        "warnings": [],
    }

    for index_path in sorted(split_dir.glob("*.json")):
        payload = _read_json_object(index_path)
        assets = payload.get("assets")
        if not isinstance(assets, list):
            continue

        report["index_count"] += 1
        index_changed = False
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            report["asset_count"] += 1
            image_path_text = _clean_text(asset.get("image_path"))
            image_path = (
                _source_image_path_for_asset(library_root, asset, image_path_text)
                if write_padded
                else _resolve_library_image_path(library_root, image_path_text)
            )
            asset_id = _clean_text(asset.get("asset_id"))
            if image_path is None or not image_path.exists():
                report["missing_image_count"] += 1
                _add_warning(report, asset_id, image_path_text, "missing_image")
                continue

            try:
                if write_padded:
                    update = _build_padded_update(library_root, asset, image_path, image_path_text)
                else:
                    with Image.open(image_path) as image:
                        width = int(image.width)
                        height = int(image.height)
                    update = {
                        "fields": {
                            "actual_width": width,
                            "actual_height": height,
                        },
                        "original_created": False,
                        "padded_written": False,
                        "original_image": None,
                        "runtime_image": None,
                    }
            except Exception as exc:
                report["failed_image_count"] += 1
                _add_warning(report, asset_id, image_path_text, f"image_open_failed:{type(exc).__name__}")
                continue

            fields = update["fields"]
            asset_changed = _asset_fields_changed(asset, fields)
            if update["original_created"]:
                report["original_created_count"] += 1
            if update["padded_written"]:
                report["padded_written_count"] += 1

            if not asset_changed:
                report["unchanged_count"] += 1
                if write_padded and not dry_run:
                    _write_padded_images(update)
                continue

            if not dry_run:
                asset.update(fields)
                if write_padded:
                    _write_padded_images(update)
                index_changed = True
            report["updated_count"] += 1

        if index_changed and not dry_run:
            _write_json_object(index_path, payload)

    report["warning_count"] = len(report["warnings"])
    return report


def _source_image_path_for_asset(library_root: Path, asset: dict[str, Any], image_path_text: str) -> Path | None:
    original_path_text = _clean_text(asset.get("original_image_path"))
    original_path = _resolve_library_image_path(library_root, original_path_text)
    if original_path is not None and original_path.exists():
        return original_path
    return _resolve_library_image_path(library_root, image_path_text)


def _build_padded_update(
    library_root: Path,
    asset: dict[str, Any],
    source_path: Path,
    image_path_text: str,
) -> dict[str, Any]:
    original_rel = _original_image_rel_for_asset(asset, image_path_text)
    runtime_rel = _runtime_image_rel_for_asset(asset, image_path_text)
    original_path = library_root / original_rel
    runtime_path = library_root / runtime_rel

    with Image.open(source_path) as image:
        rgba = image.convert("RGBA")
    aspect_ratio = _ppt_aspect_ratio_name(rgba.width, rgba.height)
    padded = _pad_image_to_ppt_aspect(rgba, aspect_ratio)
    original_created = not original_path.exists()
    padded_written = _runtime_image_needs_write(runtime_path, padded.width, padded.height)
    return {
        "fields": {
            "image_path": runtime_rel,
            "original_image_path": original_rel,
            "actual_width": rgba.width,
            "actual_height": rgba.height,
            "padded_width": padded.width,
            "padded_height": padded.height,
            "aspect_ratio": aspect_ratio,
        },
        "original_created": original_created,
        "padded_written": padded_written,
        "original_image": rgba,
        "runtime_image": padded,
        "original_path": original_path,
        "runtime_path": runtime_path,
    }


def _write_padded_images(update: dict[str, Any]) -> None:
    original_image = update.get("original_image")
    runtime_image = update.get("runtime_image")
    original_path = update.get("original_path")
    runtime_path = update.get("runtime_path")
    if not isinstance(original_image, Image.Image) or not isinstance(runtime_image, Image.Image):
        return
    if not isinstance(original_path, Path) or not isinstance(runtime_path, Path):
        return
    original_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    if update.get("original_created"):
        original_image.save(original_path, format="PNG", optimize=True)
    if update.get("padded_written"):
        runtime_image.save(runtime_path, format="PNG", optimize=True)


def _runtime_image_needs_write(path: Path, width: int, height: int) -> bool:
    if not path.exists():
        return True
    try:
        with Image.open(path) as image:
            return int(image.width) != width or int(image.height) != height or image.mode != "RGBA"
    except Exception:
        return True


def _asset_fields_changed(asset: dict[str, Any], fields: dict[str, Any]) -> bool:
    return any(asset.get(key) != value for key, value in fields.items())


def _original_image_rel_for_asset(asset: dict[str, Any], image_path_text: str) -> str:
    existing = _normalize_relative_image_path(_clean_text(asset.get("original_image_path")))
    if existing:
        return existing
    normalized = _normalize_relative_image_path(image_path_text)
    if normalized.startswith(f"{DEFAULT_IMAGE_DIR}/"):
        tail = normalized[len(DEFAULT_IMAGE_DIR) + 1:]
        if not tail.lower().endswith(".png"):
            tail = Path(tail).with_suffix(".png").as_posix()
        return f"{DEFAULT_ORIGINAL_IMAGE_DIR}/{tail}"
    name = _asset_image_stem(asset, normalized)
    return f"{DEFAULT_ORIGINAL_IMAGE_DIR}/{name}.png"


def _runtime_image_rel_for_asset(asset: dict[str, Any], image_path_text: str) -> str:
    normalized = _normalize_relative_image_path(image_path_text)
    if normalized and not normalized.startswith(f"{DEFAULT_ORIGINAL_IMAGE_DIR}/") and normalized.lower().endswith(".png"):
        return normalized
    name = _asset_image_stem(asset, normalized)
    return f"{DEFAULT_IMAGE_DIR}/{name}.png"


def _asset_image_stem(asset: dict[str, Any], image_path_text: str) -> str:
    asset_id = _clean_text(asset.get("asset_id"))
    stem = Path(image_path_text).stem if image_path_text else ""
    return _safe_filename_stem(asset_id or stem or "asset")


def _safe_filename_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return cleaned or "asset"


def _normalize_relative_image_path(value: str) -> str:
    text = _clean_text(value).replace("\\", "/").lstrip("/")
    if not text or Path(text).is_absolute() or ":" in text.split("/", 1)[0]:
        return ""
    return text


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


def _pad_image_to_ppt_aspect(image: Image.Image, aspect_ratio: str) -> Image.Image:
    if aspect_ratio == "other":
        return image.copy()
    canvas_width, canvas_height = _padded_size_for_ppt_aspect(image.width, image.height, aspect_ratio)
    if canvas_width == image.width and canvas_height == image.height:
        return image.copy()
    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    left = (canvas_width - image.width) // 2
    top = (canvas_height - image.height) // 2
    canvas.paste(image, (left, top), image)
    return canvas


def _resolve_library_image_path(library_root: Path, image_path: str) -> Path | None:
    if not image_path:
        return None
    path = Path(image_path).expanduser()
    if not path.is_absolute():
        path = library_root / path
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(library_root)
    except ValueError:
        return None
    return resolved


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"index JSON must be an object: {path}")
    return data


def _write_json_object(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _add_warning(report: dict[str, Any], asset_id: str, image_path: str, reason: str) -> None:
    report["warnings"].append(
        {
            "asset_id": asset_id,
            "image_path": image_path,
            "reason": reason,
        }
    )


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--library-dir",
        default="materials_library_ppt",
        help="Library root containing strict_reuse_indexes/ and image files.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Inspect images but do not write JSON files.")
    parser.add_argument("--write-padded", action="store_true", help="Preserve originals and rewrite image_path images as transparent-padded derivatives.")
    parser.add_argument("--json", action="store_true", help="Print the final report as JSON.")
    args = parser.parse_args(argv)

    try:
        report = update_ppt_actual_dimensions(args.library_dir, dry_run=args.dry_run, write_padded=args.write_padded)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"library: {report['library_dir']}")
        print(f"indexes: {report['index_count']}")
        print(f"assets:  {report['asset_count']}")
        print(f"updated: {report['updated_count']}")
        print(f"originals_created: {report['original_created_count']}")
        print(f"padded_written:    {report['padded_written_count']}")
        print(f"missing: {report['missing_image_count']}")
        print(f"failed:  {report['failed_image_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
