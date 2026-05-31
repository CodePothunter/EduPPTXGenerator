"""Run the LLM strict_reuse_group classification on existing reusable assets.

Reads materials_library/strict_reuse_indexes/, deep-copies the page_image and
background assets, re-runs a classification-only LLM pass on the copy, and
writes a diff report to report/llm_classify_dryrun_<timestamp>/.

By default this is a dry-run and the on-disk material library is not modified.
Pass --apply to update the split material-library indexes with the reclassified
assets.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.materials.ai_image_asset_db import (
    DEFAULT_KEYWORD_BATCH_SIZE,
    write_ai_image_match_index,
)
from edupptx.materials.strict_reuse_classifier import (
    MATERIAL_CATEGORY_RULES_TEXT,
    normalize_strict_reuse_group,
)


RECLASSIFIABLE_ASSET_KINDS = {"background", "page_image"}
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
LEGACY_BACKGROUND_GROUPS = {"background", "C11_background"}
CLASSIFICATION_UPDATE_FIELDS = frozenset(
    {
        "strict_reuse_group",
        "strict_reuse_confidence",
        "strict_reuse_reason",
    }
)

def _read_reclassify_source(library_dir: Path) -> tuple[dict, Path] | None:
    return _read_all_split_indexes(library_dir)


def _read_all_split_indexes(library_dir: Path) -> tuple[dict, Path] | None:
    split_dir = library_dir / STRICT_REUSE_INDEX_DIRNAME
    if not split_dir.exists():
        return None

    assets_by_id: dict[str, dict] = {}
    warnings: list[str] = []
    first_payload: dict = {}
    json_files: list[tuple[int, float, str, Path]] = []
    for path in split_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"split index skipped unreadable JSON: {path.name}: {type(exc).__name__}")
            continue
        if not isinstance(payload, dict):
            warnings.append(f"split index skipped non-object JSON: {path.name}")
            continue
        group = str(payload.get("strict_reuse_group") or path.stem).strip()
        priority = _split_index_priority(group, path)
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            modified_at = 0.0
        json_files.append((priority, modified_at, path.name, path))

    for _priority, _modified_at, _name, path in sorted(json_files):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"split index skipped unreadable JSON: {path.name}: {type(exc).__name__}")
            continue
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            continue
        if not first_payload:
            first_payload = payload
        group = str(payload.get("strict_reuse_group") or path.stem).strip()
        is_background_group = group in LEGACY_BACKGROUND_GROUPS or path.stem in LEGACY_BACKGROUND_GROUPS
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue
            asset = deepcopy(raw_asset)
            asset_id = str(asset.get("asset_id") or "")
            if not asset_id:
                continue
            if is_background_group:
                asset["asset_kind"] = "background"
            elif not asset.get("asset_kind"):
                asset["asset_kind"] = "page_image"
            asset.setdefault("strict_reuse_group", group)
            assets_by_id[asset_id] = asset

    assets = list(assets_by_id.values())
    if not assets:
        return None

    db = {
        "schema_version": int(first_payload.get("schema_version") or 1),
        "built_at": first_payload.get("built_at"),
        "updated_at": datetime.now().isoformat(),
        "asset_root": first_payload.get("asset_root") or str(library_dir),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": warnings,
        "source_kind": "all_split_indexes",
    }
    return db, split_dir


def _split_index_priority(group: str, path: Path) -> int:
    if group in LEGACY_BACKGROUND_GROUPS or path.stem in LEGACY_BACKGROUND_GROUPS:
        return 2
    normalized = normalize_strict_reuse_group(group, default="")
    if not normalized:
        return 0
    return 2 if normalized.casefold() == group.casefold() else 1


def _select_reclassifiable_assets(db: dict, allow_ids: set[str] | None) -> list[dict]:
    return [
        deepcopy(asset)
        for asset in db.get("assets", [])
        if isinstance(asset, dict)
        and asset.get("asset_kind") in RECLASSIFIABLE_ASSET_KINDS
        and (allow_ids is None or asset.get("asset_id") in allow_ids)
    ]


def _increment_direction_count(counts: dict[str, int], before_group: str | None, after_group: str | None) -> None:
    key = f"{before_group or 'missing'}→{after_group or 'missing'}"
    counts[key] = int(counts.get(key) or 0) + 1


def _format_direction_counts(counts: dict[str, int]) -> str:
    return ", ".join(
        f"{key}: {count}"
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _without_classification_fields(asset: dict) -> dict:
    return {
        key: value
        for key, value in asset.items()
        if key not in CLASSIFICATION_UPDATE_FIELDS
    }


def _classification_input_item(asset: dict) -> dict:
    return {
        "asset_id": asset.get("asset_id"),
        "asset_kind": asset.get("asset_kind"),
        "content_prompt": _clean_text(asset.get("content_prompt")),
    }


def _build_classification_messages(batch: list[dict]) -> list[dict[str, str]]:
    payload = {
        "assets": [_classification_input_item(asset) for asset in batch],
    }
    system = (
        "你正在为素材库重新判断 strict_reuse_group。这是 classification-only 任务，只做分类，不做元数据补全。"
        "必须只返回严格 JSON，顶层对象必须包含 assets 数组。"
        "每个 assets 项只允许包含 asset_id、strict_reuse_group、strict_reuse_confidence、strict_reuse_reason。"
        "不要返回 content_prompt、context_summary、teaching_intent、subject、grade_norm、grade_band、"
        "normalized_prompt 或任何其他非分类字段。"
        "输入只提供 asset_id、asset_kind 和 content_prompt；asset_id 与 asset_kind 只用于回填结果。"
        "strict_reuse_group 分类只能基于 content_prompt 的字面内容。"
        "strict_reuse_group 必须是下方素材类别 ID 之一。"
        "\n\n"
        + MATERIAL_CATEGORY_RULES_TEXT
        + "\nstrict_reuse_confidence 必须是 0 到 1 的数字。"
        "strict_reuse_reason 应简短说明分类原因。"
    )
    user = "请只返回这些素材的 strict_reuse_group 分类结果：\n" + json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _call_classification_llm(client: Any, batch: list[dict]) -> dict | list:
    messages = _build_classification_messages(batch)
    max_tokens = max(1024, min(8192, 350 * len(batch) + 1200))
    chat_json = getattr(client, "chat_json", None)
    if callable(chat_json):
        try:
            response = chat_json(
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
                max_retries=1,
            )
        except TypeError:
            response = chat_json(messages, temperature=0.0, max_tokens=max_tokens)
        if isinstance(response, str):
            return json.loads(_strip_json_fences(response))
        return response

    chat = getattr(client, "chat", None)
    if not callable(chat):
        raise TypeError("classification client must provide chat_json() or chat()")
    raw = chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
    return json.loads(_strip_json_fences(str(raw or "")))


def _classification_payload_by_asset_id(response: dict | list, warnings: list[str]) -> dict[str, dict]:
    if isinstance(response, dict):
        items = response.get("assets")
    else:
        items = response
    if not isinstance(items, list):
        raise ValueError("classification LLM response must contain an assets array")

    by_id: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            warnings.append("classification payload skipped non-object item")
            continue
        asset_id = _clean_text(item.get("asset_id"))
        if not asset_id:
            warnings.append("classification payload skipped item without asset_id")
            continue
        raw_group = _clean_text(item.get("strict_reuse_group"))
        group = normalize_strict_reuse_group(raw_group, default="")
        if not group:
            warnings.append(f"classification payload for {asset_id} missing valid strict_reuse_group")
            continue
        confidence = _optional_float(item.get("strict_reuse_confidence"))
        if confidence is None:
            confidence = 0.8
        confidence = max(0.0, min(1.0, confidence))
        by_id[asset_id] = {
            "asset_id": asset_id,
            "strict_reuse_group": group,
            "strict_reuse_confidence": round(confidence, 4),
            "strict_reuse_reason": _clean_text(item.get("strict_reuse_reason")) or "LLM strict reuse classification",
        }
    return by_id


def _apply_classification_payload(asset: dict, payload: dict) -> None:
    if "strict_reuse_group" in payload:
        asset["strict_reuse_group"] = payload["strict_reuse_group"]
    if "strict_reuse_confidence" in payload:
        asset["strict_reuse_confidence"] = payload["strict_reuse_confidence"]
    if "strict_reuse_reason" in payload:
        asset["strict_reuse_reason"] = payload["strict_reuse_reason"]


def _classify_assets_with_llm(assets: list[dict], client: Any, *, batch_size: int) -> tuple[list[dict], list[str]]:
    classified = [deepcopy(asset) for asset in assets if isinstance(asset, dict)]
    warnings: list[str] = []
    batch_size = max(1, int(batch_size or DEFAULT_KEYWORD_BATCH_SIZE))

    for start in range(0, len(classified), batch_size):
        batch = classified[start : start + batch_size]
        try:
            response = _call_classification_llm(client, batch)
            by_id = _classification_payload_by_asset_id(response, warnings)
        except Exception as exc:
            warnings.append(f"classification batch {start // batch_size + 1} failed: {exc}; retrying singly")
            by_id = {}
            for asset in batch:
                asset_id = _clean_text(asset.get("asset_id"))
                try:
                    single_response = _call_classification_llm(client, [asset])
                    by_id.update(_classification_payload_by_asset_id(single_response, warnings))
                except Exception as single_exc:
                    warnings.append(f"classification asset {asset_id} failed after single retry: {single_exc}")

        for asset in batch:
            asset_id = _clean_text(asset.get("asset_id"))
            payload = by_id.get(asset_id)
            if payload is None:
                warnings.append(f"classification payload missing for {asset_id}")
                continue
            _apply_classification_payload(asset, payload)

    return classified, warnings


def _read_prompt_list_assets(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"prompt list must be a JSON array: {path}")
    assets: list[dict] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        content_prompt = _clean_text(item.get("content_prompt"))
        if not content_prompt:
            continue
        asset = {
            "asset_id": f"prompt_{index:06d}",
            "asset_kind": "page_image",
            "content_prompt": content_prompt,
            "strict_reuse_group": "",
        }
        expected_group = normalize_strict_reuse_group(item.get("expected_strict_reuse_group"), default="")
        if expected_group:
            asset["expected_strict_reuse_group"] = expected_group
        assets.append(asset)
    return assets


def _audit_flags_for_prompt_classification(content_prompt: str, group: str) -> list[str]:
    text = _clean_text(content_prompt)
    normalized = normalize_strict_reuse_group(group, default="")
    flags: list[str] = []

    short_language_markers = ("田字格", "米字格", "汉字", "拼音", "笔顺", "偏旁", "部首")
    irreplaceable_markers = (
        "团聚", "寻找", "告别", "救助", "比赛", "领奖", "争执", "拒绝",
        "偷听", "劝说", "藏进", "藏进身后", "摔东西", "捂着胸口", "强忍",
        "痛苦", "愤怒", "展示尾巴", "参赛",
    )
    generic_subject_markers = ("头像", "单体", "形象", "普通", "道具", "器材", "植物", "动物")
    container_markers = ("边框", "背景", "空白", "卡片", "模板", "占位", "装饰")

    if normalized == "C00_strict_text_problem_skip" and any(marker in text for marker in short_language_markers):
        if not any(marker in text for marker in ("4个", "5个", "6个", "7个", "8个", "9个", "10个", "整段", "课文", "题干")):
            flags.append("c00_possible_short_language_symbol")
    if normalized == "C01_language_glyph_visual" and any(marker in text for marker in ("整段", "课文片段", "题干", "选项", "竖式")):
        flags.append("c01_possible_exact_payload")
    if normalized == "C02_structure_diagram_visual" and any(marker in text for marker in generic_subject_markers):
        flags.append("c02_possible_generic_subject")
    if normalized == "C03_irreplaceable_entity_event_action" and not any(marker in text for marker in irreplaceable_markers):
        if any(marker in text for marker in generic_subject_markers):
            flags.append("c03_possible_generic_subject")
    if normalized == "C04_generic_subject_object" and any(marker in text for marker in irreplaceable_markers):
        flags.append("c04_possible_irreplaceable_action_or_relation")
    if normalized == "C05_scene_decor_container" and any(marker in text for marker in irreplaceable_markers):
        flags.append("c05_possible_irreplaceable_event")
    if normalized == "C05_scene_decor_container" and any(marker in text for marker in generic_subject_markers):
        if not any(marker in text for marker in container_markers):
            flags.append("c05_possible_subject_core")
    return flags


def _write_prompt_list_audit_report(report_dir: Path, assets: list[dict]) -> None:
    counts: dict[str, int] = {}
    items: list[dict] = []
    for asset in assets:
        group = normalize_strict_reuse_group(asset.get("strict_reuse_group"), default="")
        counts[group] = counts.get(group, 0) + 1
        flags = _audit_flags_for_prompt_classification(_clean_text(asset.get("content_prompt")), group)
        expected_group = normalize_strict_reuse_group(asset.get("expected_strict_reuse_group"), default="")
        if expected_group and group != expected_group:
            flags.append("expected_group_mismatch")
        items.append(
            {
                "asset_id": _clean_text(asset.get("asset_id")),
                "content_prompt": _clean_text(asset.get("content_prompt")),
                "strict_reuse_group": group,
                "expected_strict_reuse_group": expected_group,
                "strict_reuse_confidence": _optional_float(asset.get("strict_reuse_confidence")),
                "strict_reuse_reason": _clean_text(asset.get("strict_reuse_reason")),
                "review_flags": flags,
            }
        )

    payload = {
        "mode": "prompt_list_audit",
        "asset_count": len(items),
        "counts": counts,
        "items": items,
    }
    (report_dir / "prompt_list_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_lines = [
        "# Prompt List Classification Audit",
        "",
        f"- Assets tested: {len(items)}",
        f"- Counts: {json.dumps(counts, ensure_ascii=False, sort_keys=True)}",
        "",
        "| asset_id | group | expected | flags | content_prompt |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in items:
        summary_lines.append(
            f"| `{item['asset_id']}` | {item['strict_reuse_group']} | "
            f"{item['expected_strict_reuse_group']} | {', '.join(item['review_flags'])} | "
            f"{item['content_prompt']} |"
        )
    (report_dir / "prompt_list_audit_summary.md").write_text("\n".join(summary_lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", default="materials_library")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--keyword-batch-size",
        type=int,
        default=DEFAULT_KEYWORD_BATCH_SIZE,
        help="Classification batch size. Kept under the old option name for CLI compatibility.",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="Output directory. Defaults to report/llm_classify_dryrun_<timestamp>/.",
    )
    parser.add_argument(
        "--asset-ids",
        nargs="*",
        default=None,
        help="Optional asset_id allow-list; if omitted, all page_image and background assets are tested.",
    )
    parser.add_argument(
        "--prompt-list-json",
        default=None,
        help="Optional JSON list of {content_prompt} items to classify for audit, such as merged_prompt.json.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the reclassified assets back into the material library split indexes.",
    )
    parser.add_argument(
        "--rebuild-embedding",
        action="store_true",
        help="When --apply is used, rebuild ai_image_embedding_index.npz sidecars. Default is to skip.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library_dir = Path(args.library_dir).expanduser().resolve()
    split = _read_reclassify_source(library_dir)
    if split is None:
        raise FileNotFoundError(f"Split indexes not found under: {library_dir}")
    db, split_dir = split

    allow_ids = set(args.asset_ids or ()) or None
    reclassify_assets = _select_reclassifiable_assets(db, allow_ids)
    if not reclassify_assets:
        print(f"No page_image/background assets to test under {split_dir}")
        return 0

    originals_by_id = {asset["asset_id"]: deepcopy(asset) for asset in reclassify_assets}
    original_assets_by_id = {
        asset.get("asset_id"): deepcopy(asset)
        for asset in db.get("assets", [])
        if isinstance(asset, dict) and asset.get("asset_id")
    }

    config = Config.from_env(args.env_file)
    if not config.llm_api_key or not config.llm_model:
        raise RuntimeError("GEN_APIKEY/GEN_MODEL not configured")
    client = create_llm_client(config, web_search=False)

    reclassify_assets, classification_warnings = _classify_assets_with_llm(
        reclassify_assets,
        client,
        batch_size=max(1, args.keyword_batch_size),
    )
    prompt_list_assets: list[dict] = []
    if args.prompt_list_json:
        prompt_list_path = Path(args.prompt_list_json).expanduser().resolve()
        prompt_list_assets = _read_prompt_list_assets(prompt_list_path)
        if prompt_list_assets:
            prompt_list_assets, prompt_list_warnings = _classify_assets_with_llm(
                prompt_list_assets,
                client,
                batch_size=max(1, args.keyword_batch_size),
            )
            classification_warnings.extend(
                f"prompt_list: {warning}" for warning in prompt_list_warnings
            )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(args.report_dir) if args.report_dir else REPO_ROOT / "report" / f"llm_classify_dryrun_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)
    if prompt_list_assets:
        _write_prompt_list_audit_report(report_dir, prompt_list_assets)

    diff_rows: list[dict] = []
    applied_preview_assets: list[dict] = []
    changed = 0
    metadata_changed = 0
    direction_counts: dict[str, int] = {}
    unchanged = 0
    for asset in reclassify_assets:
        asset_id = asset.get("asset_id")
        before = originals_by_id.get(asset_id, {})
        applied_asset = deepcopy(before or asset)
        _apply_classification_payload(applied_asset, asset)
        applied_preview_assets.append(applied_asset)
        before_group = before.get("strict_reuse_group")
        after_group = applied_asset.get("strict_reuse_group")
        asset_metadata_changed = _without_classification_fields(before) != _without_classification_fields(applied_asset)
        row = {
            "asset_id": asset_id,
            "content_prompt": before.get("content_prompt"),
            "asset_category": before.get("asset_category"),
            "before_group": before_group,
            "after_group": after_group,
            "before_reason": before.get("strict_reuse_reason"),
            "after_reason": applied_asset.get("strict_reuse_reason"),
            "before_confidence": before.get("strict_reuse_confidence"),
            "after_confidence": applied_asset.get("strict_reuse_confidence"),
            "metadata_changed": asset_metadata_changed,
        }
        if asset_metadata_changed:
            metadata_changed += 1
        if before_group != after_group:
            changed += 1
            _increment_direction_count(direction_counts, before_group, after_group)
        else:
            unchanged += 1
        diff_rows.append(row)

    diff_path = report_dir / "diff.jsonl"
    diff_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in diff_rows) + "\n",
        encoding="utf-8",
    )

    full_after_path = report_dir / "would_be_assets.json"
    full_after_path.write_text(json.dumps(applied_preview_assets, ensure_ascii=False, indent=2), encoding="utf-8")

    before_path = report_dir / "before_assets.json"
    before_path.write_text(
        json.dumps(
            [original_assets_by_id[asset_id] for asset_id in originals_by_id if asset_id in original_assets_by_id],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (report_dir / "before_index_snapshot.json").write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    applied_index_path = None
    embedding_report = None
    rebuild_embedding = bool(args.rebuild_embedding)
    if args.apply:
        updated_by_id = {asset.get("asset_id"): asset for asset in reclassify_assets if asset.get("asset_id")}
        merged_assets: list[dict] = []
        for asset in db.get("assets", []):
            if not isinstance(asset, dict):
                continue
            asset_id = asset.get("asset_id")
            merged = deepcopy(asset)
            updated = updated_by_id.get(asset_id)
            if updated is not None:
                _apply_classification_payload(merged, updated)
            merged_assets.append(merged)

        updated_db = deepcopy(db)
        updated_db["assets"] = merged_assets
        updated_db["asset_count"] = len(merged_assets)
        updated_db["input_asset_count"] = len(merged_assets)
        existing_warnings = db.get("warnings") if isinstance(db.get("warnings"), list) else []
        updated_db["warnings"] = list(dict.fromkeys([*existing_warnings, *classification_warnings]))
        if allow_ids is not None:
            updated_db["warnings"].append(
                f"partial LLM classify update applied to {len(reclassify_assets)} page_image/background assets"
            )

        applied_index, applied_index_path = write_ai_image_match_index(
            updated_db,
            library_dir,
            write_embedding_index=rebuild_embedding,
        )
        embedding_report = applied_index.get("embedding_index")
        (report_dir / "applied_index_snapshot.json").write_text(
            json.dumps(applied_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary_lines = [
        f"# LLM strict_reuse_group {'apply' if args.apply else 'dry-run'} @ {timestamp}",
        "",
        f"- Library: `{library_dir}`",
        f"- Model: `{config.llm_model}`",
        f"- Assets tested: {len(reclassify_assets)}",
        f"- Batch size: {args.keyword_batch_size}",
        f"- Prompt list assets tested: {len(prompt_list_assets)}",
        f"- Applied to library: {'yes' if args.apply else 'no'}",
        f"- Group changed: {changed}",
        f"- Metadata changed: {metadata_changed}",
        f"- Unchanged: {unchanged}",
    ]
    if direction_counts:
        summary_lines.append(f"- Changed directions: {_format_direction_counts(direction_counts)}")
    if applied_index_path is not None:
        summary_lines.append(f"- Updated split indexes: `{applied_index_path}`")
    if embedding_report:
        summary_lines.append(f"- Embedding rebuild: `{json.dumps(embedding_report, ensure_ascii=False)}`")
    elif args.apply and not rebuild_embedding:
        summary_lines.append("- Embedding rebuild: skipped (default)")
    summary_lines.extend(
        [
            "",
            "## Changed assets",
            "",
            "| asset_id | before | after | content_prompt |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in diff_rows:
        if row["before_group"] != row["after_group"]:
            summary_lines.append(
                f"| `{row['asset_id']}` | {row['before_group']} | {row['after_group']} | {row['content_prompt']} |"
            )
    if changed == 0:
        summary_lines.append("| _(none)_ | | | |")
    summary_lines.append("")
    if classification_warnings:
        summary_lines.append("## Warnings")
        summary_lines.append("")
        for w in classification_warnings:
            summary_lines.append(f"- {w}")
        summary_lines.append("")
    (report_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    if args.apply:
        print(f"Apply complete. Library updated.")
        print(f"  Updated split indexes: {applied_index_path}")
        if not rebuild_embedding:
            print(f"  Embedding rebuild: skipped (default)")
        elif embedding_report:
            print(f"  Embedding rebuild: {embedding_report}")
    else:
        print(f"Dry-run complete. Library untouched.")
    print(f"  Tested: {len(reclassify_assets)} page_image/background assets")
    print(f"  Group changed: {changed}")
    if direction_counts:
        print(f"  Changed directions: {_format_direction_counts(direction_counts)}")
    print(f"  Metadata changed: {metadata_changed}")
    print(f"  Report: {report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
