from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

REPORT_FILENAME = "ppt_dedupe_report.json"
SPLIT_INDEX_DIRNAME = "strict_reuse_indexes"

BUCKET_INDEX_FILES = {
    "background": "background.json",
    "C01": "C01_irreplaceable_entity_event_action.json",
    "C02": "C02_generic_subject_object.json",
    "C03": "C03_scene_decor_container.json",
}

BUCKET_RULES = {
    "C01": {"visual_threshold": 3, "text_threshold": 0.82, "require_asset_kind": True},
    "C02": {"visual_threshold": 6, "text_threshold": 0.70, "require_asset_kind": True},
    "C03": {"visual_threshold": 8, "text_threshold": 0.58, "require_asset_kind": False},
    "background": {"visual_threshold": 8, "text_threshold": 0.52, "require_asset_kind": False},
}


@dataclass
class PptDedupeInfo:
    bucket: str
    source_index: str
    asset: dict[str, Any]
    asset_id: str
    image_path: str
    original_image_path: str
    resolved_image_path: Path | None
    resolved_original_image_path: Path | None
    image_exists: bool
    file_sha256: str = ""
    perceptual_hash: str = ""
    width: int = 0
    height: int = 0
    file_size: int = 0

    @property
    def quality_score(self) -> float:
        source_pixels = _safe_float(self.asset.get("_ppt_source_pixels"))
        display_pixels = _safe_float(self.asset.get("_ppt_display_pixels"))
        intrinsic_pixels = float(self.width * self.height)
        return max(source_pixels, intrinsic_pixels) + display_pixels + float(self.file_size)


def dedupe_ppt_split_index_library(
    library_dir: str | Path,
    *,
    apply: bool = False,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(library_dir).expanduser().resolve()
    payloads = _read_split_payloads(root)
    buckets = {
        bucket: [asset for asset in payload.get("assets", []) if isinstance(asset, dict)]
        for bucket, payload in payloads.items()
    }
    result = dedupe_ppt_asset_buckets(
        buckets,
        library_root=root,
        apply=apply,
        mode="apply" if apply else "dry_run",
    )
    if apply:
        _write_split_payloads(root, payloads, result["assets_by_bucket"])
    target_report = _report_path(root, report_path)
    target_report.parent.mkdir(parents=True, exist_ok=True)
    result["report_path"] = str(target_report)
    _write_report(target_report, result)
    return result


def dedupe_ppt_asset_buckets(
    buckets: dict[str, list[dict[str, Any]]],
    *,
    library_root: str | Path,
    apply: bool,
    mode: str,
) -> dict[str, Any]:
    root = Path(library_root).expanduser().resolve()
    assets_by_bucket = {
        bucket: [asset for asset in buckets.get(bucket, []) if isinstance(asset, dict)]
        for bucket in BUCKET_INDEX_FILES
    }
    dedupe_assets_by_bucket = {
        bucket: [asset for asset in assets if not _is_secondary_projection(asset)]
        for bucket, assets in assets_by_bucket.items()
    }
    infos_by_bucket = {
        bucket: [_asset_info(root, bucket, BUCKET_INDEX_FILES[bucket], asset) for asset in assets]
        for bucket, assets in dedupe_assets_by_bucket.items()
    }

    groups: list[dict[str, Any]] = []
    missing_images: list[dict[str, str]] = []
    visual_candidate_pair_count = 0
    exact_duplicate_group_count = 0
    applied_removed_count = 0

    for bucket, infos in infos_by_bucket.items():
        missing_images.extend(
            {"bucket": bucket, "asset_id": info.asset_id, "image_path": info.image_path}
            for info in infos
            if not info.image_exists
        )
        exact_groups = _exact_duplicate_groups(bucket, infos)
        exact_duplicate_group_count += len(exact_groups)
        visual_pairs = _visual_pairs(bucket, infos)
        visual_candidate_pair_count += len(visual_pairs)
        merge_groups = _groups_from_exact_and_visual(bucket, exact_groups, visual_pairs, infos)
        for group in merge_groups:
            if apply:
                removed = _apply_group(root, assets_by_bucket[bucket], group)
                group["status"] = "applied"
                group["removed"] = removed
                applied_removed_count += len(removed)
            groups.append(group)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "library_dir": str(root),
        "asset_count": sum(len(items) for items in infos_by_bucket.values()),
        "buckets": {bucket: len(items) for bucket, items in infos_by_bucket.items()},
        "exact_duplicate_group_count": exact_duplicate_group_count,
        "visual_candidate_pair_count": visual_candidate_pair_count,
        "mergeable_group_count": len(groups),
        "applied_removed_count": applied_removed_count,
        "missing_images": missing_images,
        "groups": groups,
        "assets_by_bucket": assets_by_bucket,
    }


def dedupe_ppt_db_assets(
    db: dict[str, Any],
    *,
    library_root: str | Path,
    apply: bool = True,
    mode: str = "build_apply",
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(library_root).expanduser().resolve()
    assets = [asset for asset in db.get("assets", []) if isinstance(asset, dict)]
    buckets = _buckets_from_db_assets(assets)
    report = dedupe_ppt_asset_buckets(buckets, library_root=root, apply=apply, mode=mode)
    if apply:
        kept_bucket_ids = {
            _clean_text(asset.get("asset_id"))
            for bucket_assets in report["assets_by_bucket"].values()
            for asset in bucket_assets
        }
        passthrough = [
            asset
            for asset in assets
            if _bucket_for_db_asset(asset) == ""
            and _clean_text(asset.get("asset_id")) not in kept_bucket_ids
        ]
        db["assets"] = sorted(
            [
                *passthrough,
                *[
                    asset
                    for bucket_assets in report["assets_by_bucket"].values()
                    for asset in bucket_assets
                ],
            ],
            key=lambda asset: (
                _clean_text(asset.get("asset_kind")),
                _clean_text(asset.get("image_path")),
                _clean_text(asset.get("asset_id")),
            ),
        )
        db["asset_count"] = len(db["assets"])

    target_report = _report_path(root, report_path)
    target_report.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(target_report)
    _write_report(target_report, report)
    return _public_report(report)


def _read_split_payloads(root: Path) -> dict[str, dict[str, Any]]:
    index_dir = root / SPLIT_INDEX_DIRNAME
    if not index_dir.exists():
        raise FileNotFoundError(f"split index directory not found: {index_dir}")
    payloads: dict[str, dict[str, Any]] = {}
    for bucket, filename in BUCKET_INDEX_FILES.items():
        path = index_dir / filename
        if not path.exists():
            payloads[bucket] = {"strict_reuse_group": bucket, "asset_count": 0, "assets": []}
            continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"expected JSON object: {path}")
        assets = payload.get("assets")
        if assets is None:
            payload["assets"] = []
        elif not isinstance(assets, list):
            raise ValueError(f"expected assets list: {path}")
        payloads[bucket] = payload
    return payloads


def _write_split_payloads(
    root: Path,
    payloads: dict[str, dict[str, Any]],
    assets_by_bucket: dict[str, list[dict[str, Any]]],
) -> None:
    index_dir = root / SPLIT_INDEX_DIRNAME
    index_dir.mkdir(parents=True, exist_ok=True)
    for bucket, assets in assets_by_bucket.items():
        payload = payloads[bucket]
        payload["assets"] = assets
        payload["asset_count"] = len(assets)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        (index_dir / BUCKET_INDEX_FILES[bucket]).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _asset_info(root: Path, bucket: str, source_index: str, asset: dict[str, Any]) -> PptDedupeInfo:
    image_path = _clean_text(asset.get("image_path"))
    original_image_path = _clean_text(asset.get("original_image_path"))
    resolved = _resolve_library_path(root, image_path) if image_path else None
    original = _resolve_library_path(root, original_image_path) if original_image_path else None
    info = PptDedupeInfo(
        bucket=bucket,
        source_index=source_index,
        asset=asset,
        asset_id=_clean_text(asset.get("asset_id")),
        image_path=image_path,
        original_image_path=original_image_path,
        resolved_image_path=resolved,
        resolved_original_image_path=original,
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


def _exact_duplicate_groups(bucket: str, infos: list[PptDedupeInfo]) -> list[list[PptDedupeInfo]]:
    grouped: dict[str, list[PptDedupeInfo]] = defaultdict(list)
    for info in infos:
        if info.image_exists and info.file_sha256:
            grouped[info.file_sha256].append(info)
    groups = [items for items in grouped.values() if len(items) > 1]
    if bucket != "C01":
        return groups
    return [
        group
        for group in groups
        if _all_text_similar(group, float(BUCKET_RULES["C01"]["text_threshold"]))
    ]


def _all_text_similar(infos: list[PptDedupeInfo], threshold: float) -> bool:
    for index, left in enumerate(infos):
        for right in infos[index + 1 :]:
            if _text_similarity(left.asset, right.asset) < threshold:
                return False
    return True


def _visual_pairs(bucket: str, infos: list[PptDedupeInfo]) -> list[dict[str, Any]]:
    rule = BUCKET_RULES[bucket]
    candidates = [info for info in infos if info.image_exists and info.perceptual_hash]
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            if left.file_sha256 and left.file_sha256 == right.file_sha256:
                continue
            reason = _visual_pair_reason(bucket, left, right, rule)
            if reason:
                pairs.append(reason)
    return pairs


def _visual_pair_reason(
    bucket: str,
    left: PptDedupeInfo,
    right: PptDedupeInfo,
    rule: dict[str, Any],
) -> dict[str, Any] | None:
    if rule["require_asset_kind"] and _clean_text(left.asset.get("asset_kind")) != _clean_text(right.asset.get("asset_kind")):
        return None
    if not _aspect_compatible(left.asset.get("aspect_ratio"), right.asset.get("aspect_ratio")):
        return None
    distance = _hash_hamming_distance(left.perceptual_hash, right.perceptual_hash)
    if distance > int(rule["visual_threshold"]):
        return None
    text_score = _text_similarity(left.asset, right.asset)
    if text_score < float(rule["text_threshold"]):
        return None
    return {
        "bucket": bucket,
        "left": left.asset_id,
        "right": right.asset_id,
        "distance": distance,
        "text_similarity": round(text_score, 4),
        "reason": "visual_and_text_near_duplicate",
    }


def _groups_from_exact_and_visual(
    bucket: str,
    exact_groups: list[list[PptDedupeInfo]],
    visual_pairs: list[dict[str, Any]],
    infos: list[PptDedupeInfo],
) -> list[dict[str, Any]]:
    by_id = {info.asset_id: info for info in infos if info.asset_id}
    planned = [_plan_group(bucket, group, "exact_image_duplicate") for group in exact_groups]
    seen = {asset_id for group in planned for asset_id in [group["kept"], *group["removed"]]}
    for component in _connected_components(visual_pairs, by_id):
        ids = {info.asset_id for info in component}
        if ids & seen:
            continue
        planned.append(_plan_group(bucket, component, "visual_and_text_near_duplicate", visual_pairs=visual_pairs))
    return planned


def _plan_group(
    bucket: str,
    infos: list[PptDedupeInfo],
    reason: str,
    visual_pairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ordered = sorted(
        infos,
        key=lambda info: (
            info.quality_score,
            bool(_clean_text(info.asset.get("caption"))),
            len(_main_text(info.asset)),
            len(_as_string_list(info.asset.get("topic_refs"))),
            info.asset_id,
        ),
        reverse=True,
    )
    kept = ordered[0]
    removed = ordered[1:]
    ids = {info.asset_id for info in ordered}
    relevant_pairs = [
        pair
        for pair in (visual_pairs or [])
        if _clean_text(pair.get("left")) in ids and _clean_text(pair.get("right")) in ids
    ]
    return {
        "bucket": bucket,
        "status": "mergeable",
        "reason": reason,
        "kept": kept.asset_id,
        "removed": [info.asset_id for info in removed],
        "visual_distances": [pair["distance"] for pair in relevant_pairs],
        "text_similarity": [pair["text_similarity"] for pair in relevant_pairs],
        "items": [_item_summary(info) for info in ordered],
    }


def _apply_group(root: Path, assets: list[dict[str, Any]], group: dict[str, Any]) -> list[str]:
    assets_by_id = {_clean_text(asset.get("asset_id")): asset for asset in assets}
    kept = assets_by_id.get(_clean_text(group.get("kept")))
    if kept is None:
        return []
    removed_ids: list[str] = []
    for removed_id in [_clean_text(item) for item in group.get("removed", [])]:
        removed = assets_by_id.get(removed_id)
        if removed is None:
            continue
        _merge_duplicate_metadata(kept, removed)
        _delete_asset_files(root, removed)
        removed_ids.append(removed_id)
    if removed_ids:
        removed_set = set(removed_ids)
        assets[:] = [asset for asset in assets if _clean_text(asset.get("asset_id")) not in removed_set]
    return removed_ids


def _buckets_from_db_assets(assets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in BUCKET_INDEX_FILES}
    for asset in assets:
        bucket = _bucket_for_db_asset(asset)
        if bucket:
            buckets[bucket].append(asset)
    return buckets


def _bucket_for_db_asset(asset: dict[str, Any]) -> str:
    if _clean_text(asset.get("asset_kind")) == "background":
        return "background"
    group = _clean_text(asset.get("strict_reuse_group"))
    if group == "C01_irreplaceable_entity_event_action":
        return "C01"
    if group == "C02_generic_subject_object":
        return "C02"
    if group == "C03_scene_decor_container":
        return "C03"
    return ""


def _is_secondary_projection(asset: dict[str, Any]) -> bool:
    return asset.get("secondary_projection") is True


def _text_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_text = _main_text(left)
    right_text = _main_text(right)
    if not left_text or not right_text:
        return 0.0
    left_grams = _char_ngrams(left_text, 2)
    right_grams = _char_ngrams(right_text, 2)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _main_text(asset: dict[str, Any]) -> str:
    parts = [
        _clean_text(asset.get("caption")),
        _clean_text(asset.get("query")),
        _clean_text(asset.get("normalized_prompt")),
    ]
    return _normalize_text(" ".join(part for part in parts if part))


def _aspect_compatible(left: Any, right: Any) -> bool:
    left_text = _clean_text(left)
    right_text = _clean_text(right)
    if not left_text or not right_text:
        return True
    if left_text == "other" or right_text == "other":
        return left_text == right_text
    return left_text == right_text


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


def _delete_asset_files(root: Path, asset: dict[str, Any]) -> list[str]:
    deleted: list[str] = []
    for rel in _dedupe([asset.get("image_path"), asset.get("original_image_path")]):
        path = _resolve_library_path(root, _clean_text(rel)).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if path.exists() and path.is_file():
            path.unlink()
            deleted.append(str(path))
    return deleted


def _connected_components(
    pairs: list[dict[str, Any]],
    by_id: dict[str, PptDedupeInfo],
) -> list[list[PptDedupeInfo]]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

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
    grouped: dict[str, list[PptDedupeInfo]] = defaultdict(list)
    for asset_id in list(parent):
        info = by_id.get(asset_id)
        if info is not None:
            grouped[find(asset_id)].append(info)
    return [items for items in grouped.values() if len(items) > 1]


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
    if isinstance(kept.get("semantic_aliases"), dict) or isinstance(removed.get("semantic_aliases"), dict):
        kept["semantic_aliases"] = _merge_aliases(kept.get("semantic_aliases"), removed.get("semantic_aliases"))


def _item_summary(info: PptDedupeInfo) -> dict[str, Any]:
    return {
        "asset_id": info.asset_id,
        "image_path": info.image_path,
        "caption": _clean_text(info.asset.get("caption")),
        "query": _clean_text(info.asset.get("query")),
        "normalized_prompt": _clean_text(info.asset.get("normalized_prompt")),
        "aspect_ratio": _clean_text(info.asset.get("aspect_ratio")),
        "subject": _clean_text(info.asset.get("subject")),
        "topic_refs": _as_string_list(info.asset.get("topic_refs")),
        "quality_score": info.quality_score,
    }


def _report_path(root: Path, report_path: str | Path | None) -> Path:
    return Path(report_path).expanduser().resolve() if report_path else root / "debug" / REPORT_FILENAME


def _resolve_library_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _hash_hamming_distance(left_hash: str, right_hash: str) -> int:
    try:
        return bin(int(left_hash, 16) ^ int(right_hash, 16)).count("1")
    except ValueError:
        return 64


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。、“”‘’：:；;,.!?！？（）()\[\]【】<>《》/\\|_-]+", "", value.casefold())


def _char_ngrams(value: str, size: int) -> set[str]:
    if not value:
        return set()
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(0, len(value) - size + 1)}


def _merge_aliases(left: Any, right: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for source in (left, right):
        if not isinstance(source, dict):
            continue
        for raw_key, raw_values in source.items():
            key = _clean_text(raw_key)
            if key:
                result[key] = _dedupe([*result.get(key, []), *_as_string_list(raw_values)])
    return result


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(_public_report(report), ensure_ascii=False, indent=2), encoding="utf-8")


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "assets_by_bucket"}


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
