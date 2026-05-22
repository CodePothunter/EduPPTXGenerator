"""Deduplicate a standalone PPT image materials library.

This is an offline maintenance script for libraries built by
``scripts/build_ppt_materials_library.py``. It is intentionally separate from
the extraction pipeline:

    python scripts/dedupe_ppt_materials_library.py --library-dir materials_library_ppt_test_one

By default it reads ``ai_image_match_index.json`` and only writes a report under
``<library>/debug``. Use ``--apply`` to remove exact image duplicates.
Near-duplicate visual cleanup is report-only unless ``--apply-visual`` is also
passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.materials.ai_image_asset_db import DEFAULT_MATCH_INDEX_FILENAME, write_ai_image_match_index

REPORT_FILENAME = "dedupe_report.json"
DEFAULT_VISUAL_THRESHOLD = 10
DEFAULT_APPLY_VISUAL_THRESHOLD = 6
STRICT_TEACHING_KINDS = {"text", "math", "physics"}
SAFE_VISUAL_CATEGORIES = {
    "character_action",
    "concept_scene",
    "emotion_scene",
    "generic_tool",
    "learning_behavior",
    "symbolic_material",
}


@dataclass
class AssetInfo:
    asset: dict[str, Any]
    asset_id: str
    image_path: str
    resolved_image_path: Path | None
    image_exists: bool
    file_sha256: str = ""
    perceptual_hash: str = ""
    width: int = 0
    height: int = 0
    file_size: int = 0

    @property
    def quality_score(self) -> float:
        return float(self.width * self.height + self.file_size)


def dedupe_library(
    *,
    library_dir: str | Path,
    db_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    report_path: str | Path | None = None,
    apply: bool = False,
    apply_visual: bool = False,
    visual_threshold: int = DEFAULT_VISUAL_THRESHOLD,
    apply_visual_threshold: int = DEFAULT_APPLY_VISUAL_THRESHOLD,
    rebuild_match_index: bool = True,
) -> dict[str, Any]:
    root = Path(library_dir).expanduser().resolve()
    db_path = root / db_filename
    db = _read_db(db_path)
    assets = [asset for asset in db.get("assets", []) if isinstance(asset, dict)]
    infos = [_asset_info(root, asset) for asset in assets]
    infos_by_id = {info.asset_id: info for info in infos if info.asset_id}

    exact_groups = _exact_duplicate_groups(infos)
    visual_pairs = _visual_duplicate_pairs(
        infos,
        visual_threshold=max(0, int(visual_threshold)),
    )
    visual_groups = _connected_visual_groups(visual_pairs, infos_by_id)

    exact_plan = _plan_duplicate_groups(exact_groups, reason="exact_image_duplicate")
    visual_plan = _plan_duplicate_groups(
        [
            group
            for group in visual_groups
            if _visual_group_apply_safe(
                group,
                visual_pairs=visual_pairs,
                apply_visual_threshold=max(0, int(apply_visual_threshold)),
            )
        ],
        reason="visual_near_duplicate",
    )

    applied_exact: list[dict[str, Any]] = []
    applied_visual: list[dict[str, Any]] = []
    if apply:
        applied_exact = _apply_duplicate_plan(db, root, exact_plan)
        if apply_visual:
            applied_visual = _apply_duplicate_plan(db, root, visual_plan)
        db["asset_count"] = len([asset for asset in db.get("assets", []) if isinstance(asset, dict)])
        db["updated_at"] = datetime.now(timezone.utc).isoformat()
        db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
        if rebuild_match_index:
            _index, index_path = write_ai_image_match_index(db, root)
        else:
            index_path = None
    else:
        index_path = None

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "library_dir": str(root),
        "db_path": str(db_path),
        "mode": "apply" if apply else "dry_run",
        "apply_visual": bool(apply and apply_visual),
        "asset_count": len(assets),
        "image_count": sum(1 for info in infos if info.image_exists),
        "missing_images": [
            {"asset_id": info.asset_id, "image_path": info.image_path}
            for info in infos
            if not info.image_exists
        ],
        "exact_duplicate_group_count": len(exact_groups),
        "exact_duplicate_groups": [_group_report(item) for item in exact_plan],
        "visual_candidate_pair_count": len(visual_pairs),
        "visual_candidate_pairs": [_visual_pair_report(pair) for pair in visual_pairs],
        "visual_duplicate_group_count": len(visual_groups),
        "visual_duplicate_groups": [_group_report(item) for item in visual_plan],
        "applied_exact_count": sum(len(item.get("removed", [])) for item in applied_exact),
        "applied_visual_count": sum(len(item.get("removed", [])) for item in applied_visual),
        "applied_exact_groups": applied_exact,
        "applied_visual_groups": applied_visual,
        "match_index_path": str(index_path) if index_path is not None else "",
    }

    target_report = (
        Path(report_path).expanduser().resolve()
        if report_path is not None
        else root / "debug" / REPORT_FILENAME
    )
    target_report.parent.mkdir(parents=True, exist_ok=True)
    target_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(target_report)
    target_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _read_db(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"match index not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"asset DB must be a JSON object: {path}")
    return data


def _asset_info(root: Path, asset: dict[str, Any]) -> AssetInfo:
    asset_id = _clean_text(asset.get("asset_id"))
    image_path = _clean_text(asset.get("image_path"))
    resolved = _resolve_library_path(root, image_path) if image_path else None
    info = AssetInfo(
        asset=asset,
        asset_id=asset_id,
        image_path=image_path,
        resolved_image_path=resolved,
        image_exists=bool(resolved and resolved.exists() and resolved.is_file()),
    )
    if not info.image_exists or resolved is None:
        return info
    try:
        data = resolved.read_bytes()
        info.file_sha256 = hashlib.sha256(data).hexdigest()
        info.file_size = len(data)
        with Image.open(resolved) as img:
            info.width, info.height = img.size
        info.perceptual_hash = _perceptual_hash(resolved)
    except Exception:
        info.image_exists = False
    return info


def _resolve_library_path(root: Path, image_path: str) -> Path:
    path = Path(image_path)
    return path if path.is_absolute() else root / path


def _exact_duplicate_groups(infos: list[AssetInfo]) -> list[list[AssetInfo]]:
    groups: dict[str, list[AssetInfo]] = defaultdict(list)
    for info in infos:
        if info.image_exists and info.file_sha256:
            groups[info.file_sha256].append(info)
    return [items for items in groups.values() if len(items) > 1]


def _visual_duplicate_pairs(
    infos: list[AssetInfo],
    *,
    visual_threshold: int,
) -> list[dict[str, Any]]:
    candidates = [
        info
        for info in infos
        if info.image_exists
        and info.perceptual_hash
        and _clean_text(info.asset.get("asset_kind")) == "page_image"
    ]
    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(candidates):
        for right in candidates[i + 1 :]:
            result = _visual_duplicate_reason(left, right, visual_threshold=visual_threshold)
            if result:
                pairs.append(result)
    return pairs


def _visual_duplicate_reason(
    left: AssetInfo,
    right: AssetInfo,
    *,
    visual_threshold: int,
) -> dict[str, Any] | None:
    if left.file_sha256 and left.file_sha256 == right.file_sha256:
        return None
    left_category = _clean_text(left.asset.get("asset_category"))
    right_category = _clean_text(right.asset.get("asset_category"))
    if left_category != right_category:
        return None
    if left_category not in SAFE_VISUAL_CATEGORIES and not _teaching_signature(left.asset):
        return None

    distance = _hash_hamming_distance(left.perceptual_hash, right.perceptual_hash)
    if distance > visual_threshold:
        return None

    left_signature = _teaching_signature(left.asset)
    right_signature = _teaching_signature(right.asset)
    if left_signature or right_signature:
        if left_signature != right_signature:
            return None
        reason = "same_teaching_signature"
    elif _prompt_similarity(left.asset, right.asset) >= 0.55:
        reason = "high_prompt_similarity"
    elif _constraint_terms_compatible(left.asset, right.asset):
        reason = "compatible_entity_action_terms"
    else:
        return None

    return {
        "left": left.asset_id,
        "right": right.asset_id,
        "distance": distance,
        "reason": reason,
        "left_prompt": _clean_text(left.asset.get("content_prompt")),
        "right_prompt": _clean_text(right.asset.get("content_prompt")),
    }


def _connected_visual_groups(
    pairs: list[dict[str, Any]],
    infos_by_id: dict[str, AssetInfo],
) -> list[list[AssetInfo]]:
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        if parent[item] != item:
            parent[item] = find(parent[item])
        return parent[item]

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for pair in pairs:
        left = _clean_text(pair.get("left"))
        right = _clean_text(pair.get("right"))
        if left and right:
            union(left, right)

    grouped: dict[str, list[AssetInfo]] = defaultdict(list)
    for asset_id in list(parent):
        info = infos_by_id.get(asset_id)
        if info is not None:
            grouped[find(asset_id)].append(info)
    return [items for items in grouped.values() if len(items) > 1]


def _visual_group_apply_safe(
    group: list[AssetInfo],
    *,
    visual_pairs: list[dict[str, Any]],
    apply_visual_threshold: int,
) -> bool:
    ids = {info.asset_id for info in group}
    relevant = [
        pair
        for pair in visual_pairs
        if _clean_text(pair.get("left")) in ids and _clean_text(pair.get("right")) in ids
    ]
    if not relevant:
        return False
    if any(int(pair.get("distance") or 99) > apply_visual_threshold for pair in relevant):
        return False
    reasons = {_clean_text(pair.get("reason")) for pair in relevant}
    return bool(reasons & {"same_teaching_signature", "high_prompt_similarity"})


def _plan_duplicate_groups(groups: list[list[AssetInfo]], *, reason: str) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for group in groups:
        ordered = sorted(
            group,
            key=lambda info: (
                info.quality_score,
                len(info.asset.get("constraints") or []),
                len(info.asset.get("core_keywords") or []),
                len(_clean_text(info.asset.get("content_prompt"))),
                _clean_text(info.asset.get("asset_id")),
            ),
            reverse=True,
        )
        keep = ordered[0]
        remove = ordered[1:]
        planned.append(
            {
                "reason": reason,
                "kept": keep.asset_id,
                "removed": [info.asset_id for info in remove],
                "items": [_asset_report(info) for info in ordered],
            }
        )
    return planned


def _apply_duplicate_plan(
    db: dict[str, Any],
    root: Path,
    plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assets = [asset for asset in db.get("assets", []) if isinstance(asset, dict)]
    assets_by_id = {_clean_text(asset.get("asset_id")): asset for asset in assets}
    removed_ids: set[str] = set()
    applied: list[dict[str, Any]] = []
    for group in plan:
        kept_id = _clean_text(group.get("kept"))
        kept_asset = assets_by_id.get(kept_id)
        if kept_asset is None:
            continue
        removed_for_group: list[str] = []
        deleted_paths: list[str] = []
        for removed_id in group.get("removed", []):
            removed_id = _clean_text(removed_id)
            removed_asset = assets_by_id.get(removed_id)
            if removed_asset is None or removed_id in removed_ids:
                continue
            _merge_duplicate_metadata(kept_asset, removed_asset)
            deleted = _delete_asset_image(root, removed_asset)
            if deleted:
                deleted_paths.append(deleted)
            removed_ids.add(removed_id)
            removed_for_group.append(removed_id)
        if removed_for_group:
            applied.append(
                {
                    "reason": group.get("reason"),
                    "kept": kept_id,
                    "removed": removed_for_group,
                    "deleted_image_paths": deleted_paths,
                }
            )
    if removed_ids:
        db["assets"] = [
            asset
            for asset in assets
            if _clean_text(asset.get("asset_id")) not in removed_ids
        ]
    return applied


def _merge_duplicate_metadata(kept: dict[str, Any], removed: dict[str, Any]) -> None:
    duplicate_ids = [
        *_as_string_list(kept.get("duplicate_asset_ids")),
        _clean_text(removed.get("asset_id")),
        *_as_string_list(removed.get("duplicate_asset_ids")),
    ]
    kept["duplicate_asset_ids"] = sorted(_dedupe(duplicate_ids))
    for key in ("topic_refs", "context_summary_keywords", "core_keywords"):
        values = [*_as_string_list(kept.get(key)), *_as_string_list(removed.get(key))]
        if values:
            kept[key] = _dedupe(values)
    kept["constraints"] = _merge_constraints(kept.get("constraints"), removed.get("constraints"))
    kept["semantic_aliases"] = _merge_semantic_aliases(
        kept.get("semantic_aliases"),
        removed.get("semantic_aliases"),
    )


def _merge_constraints(left: Any, right: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for source in (left, right):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            key = (
                _clean_text(item.get("kind")),
                _clean_text(item.get("subtype")),
                _clean_text(item.get("value")),
                _safe_int(item.get("importance")),
            )
            if not key[2] or key in seen:
                continue
            seen.add(key)
            result.append(dict(item))
    return result


def _merge_semantic_aliases(left: Any, right: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for source in (left, right):
        if not isinstance(source, dict):
            continue
        for raw_key, raw_values in source.items():
            key = _clean_text(raw_key)
            if not key:
                continue
            result[key] = _dedupe([*result.get(key, []), *_as_string_list(raw_values)])
    return result


def _delete_asset_image(root: Path, asset: dict[str, Any]) -> str:
    image_path = _clean_text(asset.get("image_path"))
    if not image_path:
        return ""
    path = _resolve_library_path(root, image_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return ""
    if not path.exists() or not path.is_file():
        return ""
    try:
        path.unlink()
        return str(path)
    except OSError:
        return ""


def _teaching_signature(asset: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    constraints = asset.get("constraints")
    if not isinstance(constraints, list):
        return ()
    terms: list[tuple[str, str]] = []
    for item in constraints:
        if not isinstance(item, dict):
            continue
        kind = _clean_text(item.get("kind"))
        subtype = _clean_text(item.get("subtype"))
        value = _normalized_term(item.get("value"))
        importance = _safe_int(item.get("importance"))
        if not value:
            continue
        if kind in STRICT_TEACHING_KINDS or subtype == "teaching_content" or importance >= 2:
            terms.append((kind, value))
    return tuple(sorted(set(terms)))


def _constraint_terms_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for kind in ("entity", "action", "scene", "object"):
        left_terms = _constraint_terms(left, kind)
        right_terms = _constraint_terms(right, kind)
        if left_terms or right_terms:
            if not (left_terms and right_terms and _any_terms_similar(left_terms, right_terms)):
                return False
    return True


def _constraint_terms(asset: dict[str, Any], kind: str) -> list[str]:
    constraints = asset.get("constraints")
    if not isinstance(constraints, list):
        return []
    terms: list[str] = []
    for item in constraints:
        if not isinstance(item, dict):
            continue
        if _clean_text(item.get("kind")) == kind:
            term = _normalized_term(item.get("value"))
            if term:
                terms.append(term)
    return _dedupe(terms)


def _prompt_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_terms = _char_ngrams(_normalized_term(left.get("content_prompt")), 2)
    right_terms = _char_ngrams(_normalized_term(right.get("content_prompt")), 2)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def _any_terms_similar(left_terms: list[str], right_terms: list[str]) -> bool:
    return any(_terms_similar(left, right) for left in left_terms for right in right_terms)


def _terms_similar(left: str, right: str) -> bool:
    left = _normalized_term(left)
    right = _normalized_term(right)
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) >= 2 and (left in right or right in left):
        return True
    left_grams = _char_ngrams(left, 2)
    right_grams = _char_ngrams(right, 2)
    return bool(left_grams and right_grams and len(left_grams & right_grams) / len(left_grams | right_grams) >= 0.55)


def _perceptual_hash(path: Path) -> str:
    with Image.open(path) as img:
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        gray = background.convert("L").resize((9, 8), Image.LANCZOS)
        pixels = list(gray.getdata())
    bits: list[str] = []
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits.append("1" if pixels[row + x] > pixels[row + x + 1] else "0")
    return f"{int(''.join(bits), 2):016x}"


def _hash_hamming_distance(left_hash: str, right_hash: str) -> int:
    try:
        return bin(int(left_hash, 16) ^ int(right_hash, 16)).count("1")
    except ValueError:
        return 64


def _asset_report(info: AssetInfo) -> dict[str, Any]:
    return {
        "asset_id": info.asset_id,
        "image_path": info.image_path,
        "asset_category": _clean_text(info.asset.get("asset_category")),
        "content_prompt": _clean_text(info.asset.get("content_prompt")),
        "width": info.width,
        "height": info.height,
        "file_size": info.file_size,
        "quality_score": info.quality_score,
        "file_sha256": info.file_sha256[:16],
        "perceptual_hash": info.perceptual_hash,
    }


def _group_report(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "reason": group.get("reason"),
        "kept": group.get("kept"),
        "removed": group.get("removed", []),
        "items": group.get("items", []),
    }


def _visual_pair_report(pair: dict[str, Any]) -> dict[str, Any]:
    return {
        "left": pair.get("left"),
        "right": pair.get("right"),
        "distance": pair.get("distance"),
        "reason": pair.get("reason"),
        "left_prompt": pair.get("left_prompt"),
        "right_prompt": pair.get("right_prompt"),
    }


def _normalized_term(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value)).casefold()


def _char_ngrams(value: str, size: int) -> set[str]:
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(0, len(value) - size + 1)}


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", type=Path, default=Path("materials_library_ppt"))
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Apply exact duplicate cleanup")
    parser.add_argument(
        "--apply-visual",
        action="store_true",
        help="Also apply conservative visual near-duplicate cleanup; requires --apply",
    )
    parser.add_argument("--visual-threshold", type=int, default=DEFAULT_VISUAL_THRESHOLD)
    parser.add_argument("--apply-visual-threshold", type=int, default=DEFAULT_APPLY_VISUAL_THRESHOLD)
    parser.add_argument("--no-match-index", action="store_true", help="Do not rebuild match index after --apply")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    if args.apply_visual and not args.apply:
        raise SystemExit("--apply-visual requires --apply")
    report = dedupe_library(
        library_dir=args.library_dir,
        report_path=args.report_path,
        apply=args.apply,
        apply_visual=args.apply_visual,
        visual_threshold=args.visual_threshold,
        apply_visual_threshold=args.apply_visual_threshold,
        rebuild_match_index=not args.no_match_index,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "mode": report["mode"],
                "library_dir": report["library_dir"],
                "asset_count": report["asset_count"],
                "exact_duplicate_group_count": report["exact_duplicate_group_count"],
                "visual_candidate_pair_count": report["visual_candidate_pair_count"],
                "visual_duplicate_group_count": report["visual_duplicate_group_count"],
                "applied_exact_count": report["applied_exact_count"],
                "applied_visual_count": report["applied_visual_count"],
                "report_path": report["report_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
