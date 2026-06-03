"""Dry-run query-based strict_reuse_group classification for material assets.

Audit classification (``_build_caption_classification_messages``) mirrors the
production classifier and decides on the verbose ``query`` field;
``compare_caption_classification`` compares a caption-driven classifier's output
against the stored ``strict_reuse_group`` for offline evaluation.
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
from edupptx.materials.ai_image_asset_db import DEFAULT_KEYWORD_BATCH_SIZE
from edupptx.materials.strict_reuse_classifier import (
    MATERIAL_CATEGORY_RULES_TEXT,
    normalize_strict_reuse_group,
)

STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _asset_caption(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("caption")) or _clean_text(asset.get("content_prompt"))


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


def _result_group(result: Any) -> str:
    if isinstance(result, dict):
        result = result.get("strict_reuse_group") or result.get("group")
    return normalize_strict_reuse_group(result, default="")


def compare_caption_classification(
    assets: list[dict[str, Any]],
    classifier: Any,
    *,
    predicted_groups_by_asset_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compare caption-only classifier results with existing strict_reuse_group."""

    rows: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    by_expected_group: dict[str, dict[str, Any]] = {}

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        caption = _asset_caption(asset)
        expected = normalize_strict_reuse_group(asset.get("strict_reuse_group"), default="")
        if not expected:
            continue

        if predicted_groups_by_asset_id is not None and asset_id in predicted_groups_by_asset_id:
            predicted = normalize_strict_reuse_group(predicted_groups_by_asset_id.get(asset_id), default="")
        else:
            predicted = _result_group(classifier.classify(caption))

        matched = bool(predicted and predicted == expected)
        row = {
            "asset_id": asset_id,
            "caption": caption,
            "expected_group": expected,
            "predicted_group": predicted,
            "matched": matched,
        }
        rows.append(row)
        if not matched:
            mismatches.append(row)

        group_stats = by_expected_group.setdefault(
            expected,
            {"total": 0, "mismatch_count": 0, "mismatch_rate": 0.0},
        )
        group_stats["total"] += 1
        if not matched:
            group_stats["mismatch_count"] += 1

    for group_stats in by_expected_group.values():
        total = int(group_stats["total"])
        group_stats["mismatch_rate"] = round(group_stats["mismatch_count"] / total, 4) if total else 0.0

    total = len(rows)
    mismatch_count = len(mismatches)
    return {
        "total": total,
        "mismatch_count": mismatch_count,
        "mismatch_rate": round(mismatch_count / total, 4) if total else 0.0,
        "by_expected_group": dict(sorted(by_expected_group.items())),
        "mismatches": mismatches,
        "rows": rows,
    }


def _iter_index_paths(library_dir: Path) -> list[Path]:
    paths: list[Path] = []
    split_dir = library_dir / STRICT_REUSE_INDEX_DIRNAME
    if split_dir.exists():
        paths.extend(
            path
            for path in sorted(split_dir.glob("*.json"))
            if not path.stem.startswith("content_prompts")
            and path.stem not in {"strict_reuse_split_manifest", "general_audit_suspects"}
        )
    merged_index = library_dir / "ai_image_match_index.json"
    if merged_index.exists():
        paths.append(merged_index)
    return paths


def load_caption_assets(library_dir: str | Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    root = Path(library_dir)
    assets_by_id: dict[str, dict[str, Any]] = {}
    for path in _iter_index_paths(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        group_hint = normalize_strict_reuse_group(payload.get("strict_reuse_group") or path.stem, default="")
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            continue
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            asset = deepcopy(item)
            asset_id = _clean_text(asset.get("asset_id"))
            if not asset_id or asset_id in assets_by_id:
                continue
            expected = normalize_strict_reuse_group(asset.get("strict_reuse_group") or group_hint, default="")
            caption = _asset_caption(asset)
            if not expected or not caption:
                continue
            asset["asset_id"] = asset_id
            asset["caption"] = caption
            asset["strict_reuse_group"] = expected
            assets_by_id[asset_id] = asset
            if limit and len(assets_by_id) >= limit:
                return list(assets_by_id.values())
    return list(assets_by_id.values())


def _asset_query(asset: dict[str, Any]) -> str:
    return (
        _clean_text(asset.get("query"))
        or _clean_text(asset.get("detail_prompt"))
        or _clean_text(asset.get("content_prompt"))
    )


def _caption_input_item(asset: dict[str, Any]) -> dict[str, str]:
    # Audit classification mirrors production: classify on the verbose query field.
    return {
        "asset_id": _clean_text(asset.get("asset_id")),
        "query": _asset_query(asset),
    }


def _build_caption_classification_messages(batch: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload = {"assets": [_caption_input_item(asset) for asset in batch]}
    system = (
        "Classify material-library assets into strict_reuse_group. "
        "This is classification-only: do not rewrite metadata. "
        "Use only the query field to decide. "
        "Return strict JSON with an assets array; each item must contain "
        "asset_id and strict_reuse_group only.\n\n"
        + MATERIAL_CATEGORY_RULES_TEXT
    )
    user = "Classify these assets by query only:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _call_caption_classifier_llm(client: Any, batch: list[dict[str, Any]]) -> dict[str, str]:
    messages = _build_caption_classification_messages(batch)
    max_tokens = max(1024, min(8192, 250 * len(batch) + 1200))
    chat_json = getattr(client, "chat_json", None)
    if callable(chat_json):
        try:
            response = chat_json(messages=messages, temperature=0.0, max_tokens=max_tokens, max_retries=1)
        except TypeError:
            response = chat_json(messages, temperature=0.0, max_tokens=max_tokens)
    else:
        chat = getattr(client, "chat", None)
        if not callable(chat):
            raise TypeError("caption classifier client must provide chat_json() or chat()")
        response = chat(messages=messages, temperature=0.0, max_tokens=max_tokens)

    if isinstance(response, str):
        response = json.loads(_strip_json_fences(response))
    items = response.get("assets") if isinstance(response, dict) else response
    if not isinstance(items, list):
        raise ValueError("caption classifier response must contain an assets array")

    predictions: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = _clean_text(item.get("asset_id"))
        group = normalize_strict_reuse_group(item.get("strict_reuse_group"), default="")
        if asset_id and group:
            predictions[asset_id] = group
    return predictions


def classify_caption_assets_with_llm(
    assets: list[dict[str, Any]],
    client: Any,
    *,
    batch_size: int = DEFAULT_KEYWORD_BATCH_SIZE,
) -> tuple[dict[str, str], list[str]]:
    predictions: dict[str, str] = {}
    warnings: list[str] = []
    batch_size = max(1, int(batch_size or DEFAULT_KEYWORD_BATCH_SIZE))
    for start in range(0, len(assets), batch_size):
        batch = assets[start : start + batch_size]
        try:
            predictions.update(_call_caption_classifier_llm(client, batch))
        except Exception as exc:
            warnings.append(f"caption classify batch {start // batch_size + 1} failed: {exc}; retrying singly")
            for asset in batch:
                asset_id = _clean_text(asset.get("asset_id"))
                try:
                    predictions.update(_call_caption_classifier_llm(client, [asset]))
                except Exception as single_exc:
                    warnings.append(f"caption classify asset {asset_id} failed: {single_exc}")
    return predictions, warnings


class _UnusedClassifier:
    def classify(self, caption: str) -> str:
        return ""


def _write_report(report: dict[str, Any], report_dir: Path, *, library_dir: Path, model: str, warnings: list[str]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "library_dir": str(library_dir),
        "model": model,
        "warnings": warnings,
        **report,
    }
    (report_dir / "caption_classify_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "mismatches.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in report["mismatches"]) + "\n",
        encoding="utf-8",
    )
    summary_lines = [
        "# Caption Classification Dry Run",
        "",
        f"- Library: `{library_dir}`",
        f"- Model: `{model}`",
        f"- Assets tested: {report['total']}",
        f"- Mismatches: {report['mismatch_count']}",
        f"- Mismatch rate: {report['mismatch_rate']}",
        "",
        "## By Expected Group",
        "",
        "| group | total | mismatches | rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for group, stats in report["by_expected_group"].items():
        summary_lines.append(
            f"| {group} | {stats['total']} | {stats['mismatch_count']} | {stats['mismatch_rate']} |"
        )
    if warnings:
        summary_lines.extend(["", "## Warnings", ""])
        summary_lines.extend(f"- {warning}" for warning in warnings)
    (report_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", required=True, help="Material library directory to inspect.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max asset count.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_KEYWORD_BATCH_SIZE)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--report-dir", default="")
    args = parser.parse_args(argv)

    library_dir = Path(args.library_dir)
    assets = load_caption_assets(library_dir, limit=args.limit or None)
    if not assets:
        print(f"No caption-bearing classified assets found in {library_dir}")
        return 1

    config = Config.from_env(args.env_file)
    client = create_llm_client(config)
    predictions, warnings = classify_caption_assets_with_llm(assets, client, batch_size=args.batch_size)
    if not predictions:
        warnings.append("caption classifier produced no usable predictions")
    report = compare_caption_classification(
        assets,
        _UnusedClassifier(),
        predicted_groups_by_asset_id=predictions,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(args.report_dir) if args.report_dir else REPO_ROOT / "report" / f"caption_classify_dryrun_{timestamp}"
    _write_report(report, report_dir, library_dir=library_dir, model=config.llm_model, warnings=warnings)

    print(f"Tested: {report['total']} assets")
    print(f"Mismatches: {report['mismatch_count']} ({report['mismatch_rate']})")
    print(f"Report: {report_dir}")
    if warnings:
        print(f"Warnings: {len(warnings)}")
    return 0 if predictions else 1


if __name__ == "__main__":
    raise SystemExit(main())
