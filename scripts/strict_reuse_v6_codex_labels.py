"""Build Codex-authored strict reuse v7 review labels.

This module intentionally does not classify text. It only prepares unbiased
content_prompt queues, validates manual labels, and writes grouped audit JSON.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CATEGORY_ORDER = [
    "C00_strict_text_problem_skip",
    "C01_irreplaceable_entity_event_action",
    "C02_generic_subject_object",
    "C03_scene_decor_container",
]
CATEGORY_SET = set(CATEGORY_ORDER)
MISSING_PROMPT_FLAG = "missing_or_insufficient_content_prompt"
AUDIT_FIELDS = [
    "asset_kind",
    "image_path",
    "theme",
    "subject",
    "grade_norm",
]


@dataclass
class AggregateResult:
    assets: list[dict[str, Any]]
    warnings: list[str]


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def aggregate_split_assets(split_dir: str | Path) -> AggregateResult:
    root = Path(split_dir)
    assets_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    warnings: list[str] = []

    for path in sorted(root.glob("*.json"), key=lambda p: p.name):
        try:
            payload = read_json(path)
        except Exception as exc:
            warnings.append(f"skipped unreadable JSON {path.name}: {type(exc).__name__}: {exc}")
            continue
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            warnings.append(f"skipped JSON without assets array: {path.name}")
            continue
        fallback_group = clean_text(payload.get("strict_reuse_group") or path.stem)
        for index, raw_asset in enumerate(raw_assets, 1):
            if not isinstance(raw_asset, dict):
                warnings.append(f"skipped non-object asset in {path.name} at index {index}")
                continue
            asset_id = clean_text(raw_asset.get("asset_id"))
            if not asset_id:
                warnings.append(f"skipped asset without asset_id in {path.name} at index {index}")
                continue
            record = dict(raw_asset)
            record["asset_id"] = asset_id
            record["content_prompt"] = clean_text(raw_asset.get("content_prompt"))
            record["source_file"] = path.name
            record["original_strict_reuse_group"] = clean_text(
                raw_asset.get("strict_reuse_group") or fallback_group
            )
            if asset_id in assets_by_id:
                warnings.append(f"duplicate asset_id kept from later file: {asset_id}")
            else:
                order.append(asset_id)
            assets_by_id[asset_id] = record

    return AggregateResult(assets=[assets_by_id[asset_id] for asset_id in order], warnings=warnings)


def write_items_for_codex(assets: list[dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = [
        {
            "ordinal": ordinal,
            "asset_id": clean_text(asset.get("asset_id")),
            "content_prompt": clean_text(asset.get("content_prompt")),
        }
        for ordinal, asset in enumerate(assets, 1)
    ]
    payload = {
        "decision_basis": "content_prompt_only",
        "asset_count": len(items),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_label_jsonl(path: str | Path) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    label_path = Path(path)
    for lineno, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"label line {lineno} is not an object")
        asset_id = clean_text(item.get("asset_id"))
        if not asset_id:
            raise ValueError(f"label line {lineno} missing asset_id")
        category = clean_text(item.get("assigned_category"))
        if category not in CATEGORY_SET:
            raise ValueError(f"invalid category for {asset_id}: {category}")
        reason = clean_text(item.get("decision_reason"))
        if not reason:
            raise ValueError(f"label line {lineno} missing decision_reason")
        flags = item.get("review_flags", [])
        if flags is None:
            flags = []
        if not isinstance(flags, list):
            raise ValueError(f"review_flags for {asset_id} must be a list")
        if asset_id in labels:
            raise ValueError(f"duplicate manual label for {asset_id}")
        labels[asset_id] = {
            "assigned_category": category,
            "decision_reason": reason,
            "review_flags": [clean_text(flag) for flag in flags if clean_text(flag)],
        }
    return labels


def build_review_payload(
    assets: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    *,
    source_dir: str,
    warnings: list[str],
) -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORY_ORDER}
    missing_labels = [
        clean_text(asset.get("asset_id"))
        for asset in assets
        if clean_text(asset.get("content_prompt")) and clean_text(asset.get("asset_id")) not in labels
    ]
    if missing_labels:
        preview = ", ".join(missing_labels[:10])
        raise ValueError(f"missing manual labels for {len(missing_labels)} assets: {preview}")

    for asset in assets:
        asset_id = clean_text(asset.get("asset_id"))
        content_prompt = clean_text(asset.get("content_prompt"))
        if not content_prompt:
            category = "C00_strict_text_problem_skip"
            reason = "content_prompt缺失，按最保守跳过复用处理"
            review_flags = [MISSING_PROMPT_FLAG]
        else:
            label = labels[asset_id]
            category = label["assigned_category"]
            reason = label["decision_reason"]
            review_flags = list(label.get("review_flags") or [])

        entry = {
            "asset_id": asset_id,
            "content_prompt": content_prompt,
            "assigned_category": category,
            "decision_reason": reason,
            "review_flags": review_flags,
            "source_file": clean_text(asset.get("source_file")),
            "original_strict_reuse_group": clean_text(asset.get("original_strict_reuse_group")),
        }
        for field in AUDIT_FIELDS:
            entry[field] = clean_text(asset.get(field))
        categories[category].append(entry)

    counts = {category: len(categories[category]) for category in CATEGORY_ORDER}
    return {
        "rule_version": "v7",
        "source_dir": source_dir,
        "decision_basis": "content_prompt_only",
        "original_classification_policy": "ignored_for_decision_retained_for_audit",
        "category_order": CATEGORY_ORDER,
        "counts": counts,
        "asset_count": sum(counts.values()),
        "warnings": warnings,
        "categories": categories,
    }


def validate_review_payload(payload: dict[str, Any], *, expected_asset_count: int) -> None:
    if payload.get("category_order") != CATEGORY_ORDER:
        raise ValueError("category_order mismatch")
    categories = payload.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("categories must be an object")
    seen: set[str] = set()
    total = 0
    for category in CATEGORY_ORDER:
        entries = categories.get(category)
        if not isinstance(entries, list):
            raise ValueError(f"category missing or not list: {category}")
        total += len(entries)
        for entry in entries:
            asset_id = clean_text(entry.get("asset_id"))
            if not asset_id:
                raise ValueError(f"entry missing asset_id in {category}")
            if asset_id in seen:
                raise ValueError(f"duplicate asset in categories: {asset_id}")
            seen.add(asset_id)
            if entry.get("assigned_category") != category:
                raise ValueError(f"assigned_category mismatch for {asset_id}")
            if not clean_text(entry.get("decision_reason")):
                raise ValueError(f"entry missing decision_reason: {asset_id}")
    if total != expected_asset_count:
        raise ValueError(f"asset count mismatch: expected {expected_asset_count}, got {total}")
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise ValueError("counts must be an object")
    for category in CATEGORY_ORDER:
        if int(counts.get(category, -1)) != len(categories[category]):
            raise ValueError(f"count mismatch for {category}")


def write_review_payload(payload: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def command_prepare(args: argparse.Namespace) -> int:
    result = aggregate_split_assets(Path(args.library_dir) / "strict_reuse_indexes")
    write_items_for_codex(result.assets, args.output)
    if result.warnings:
        for warning in result.warnings:
            print(f"warning: {warning}")
    print(f"wrote {len(result.assets)} items to {args.output}")
    return 0


def command_build(args: argparse.Namespace) -> int:
    split_dir = Path(args.library_dir) / "strict_reuse_indexes"
    result = aggregate_split_assets(split_dir)
    labels = read_label_jsonl(args.labels_file)
    payload = build_review_payload(
        result.assets,
        labels,
        source_dir=str(split_dir),
        warnings=result.warnings,
    )
    validate_review_payload(payload, expected_asset_count=len(result.assets))
    write_review_payload(payload, args.output)
    print(f"wrote {payload['asset_count']} labeled assets to {args.output}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="write unbiased content_prompt queue")
    prepare.add_argument("--library-dir", required=True)
    prepare.add_argument("--output", required=True)
    prepare.set_defaults(func=command_prepare)

    build = subparsers.add_parser("build", help="build grouped review JSON from manual labels")
    build.add_argument("--library-dir", required=True)
    build.add_argument("--labels-file", required=True)
    build.add_argument("--output", required=True)
    build.set_defaults(func=command_build)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
