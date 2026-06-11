"""Dry-run or apply LLM general=true/false classification for material-library assets."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.config import Config
from edupptx.llm_client import create_llm_client
from edupptx.materials.ai_image_asset_db import DEFAULT_KEYWORD_BATCH_SIZE, write_ai_image_match_index
from edupptx.materials.general_rules import build_general_system_prompt, judge_records

STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"
RECLASSIFIABLE_ASSET_KINDS = {"background", "page_image"}
DEFAULT_GENERAL_WORKERS = 15


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


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


def _read_all_split_indexes(library_dir: Path) -> tuple[dict[str, Any], Path] | None:
    split_dir = library_dir / STRICT_REUSE_INDEX_DIRNAME
    if not split_dir.exists():
        return None
    assets_by_id: dict[str, dict[str, Any]] = {}
    first_payload: dict[str, Any] = {}
    warnings: list[str] = []
    for path in sorted(split_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"split index skipped unreadable JSON: {path.name}: {type(exc).__name__}")
            continue
        if not isinstance(payload, dict):
            warnings.append(f"split index skipped non-object JSON: {path.name}")
            continue
        if not first_payload:
            first_payload = payload
        group = _clean_text(payload.get("strict_reuse_group") or path.stem)
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            continue
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue
            asset = deepcopy(raw_asset)
            asset_id = _clean_text(asset.get("asset_id"))
            if not asset_id:
                continue
            asset.setdefault("strict_reuse_group", group)
            if not asset.get("asset_kind"):
                asset["asset_kind"] = "background" if group == "background" else "page_image"
            assets_by_id[asset_id] = asset
    if not assets_by_id:
        return None
    db = {
        "schema_version": int(first_payload.get("schema_version") or 1),
        "built_at": first_payload.get("built_at"),
        "updated_at": datetime.now().isoformat(),
        "asset_root": first_payload.get("asset_root") or str(library_dir),
        "asset_count": len(assets_by_id),
        "assets": list(assets_by_id.values()),
        "warnings": warnings,
        "source_kind": "all_split_indexes",
    }
    return db, split_dir


def _read_input_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = {"assets": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        raise ValueError("input JSON must be an object with assets array or a raw assets array")
    return {
        "schema_version": 1,
        "asset_root": str(path.parent),
        "asset_count": len(payload["assets"]),
        "assets": [deepcopy(item) for item in payload["assets"] if isinstance(item, dict)],
        "warnings": [],
        "source_kind": "input_json",
    }


def _asset_general_query(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("query")) or _clean_text(asset.get("content_prompt"))


def _select_reclassifiable_assets(db: dict[str, Any], allow_ids: set[str] | None) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for asset in db.get("assets", []):
        if not isinstance(asset, dict):
            continue
        if _clean_text(asset.get("asset_kind")) not in RECLASSIFIABLE_ASSET_KINDS:
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        if allow_ids is not None and asset_id not in allow_ids:
            continue
        if not _asset_general_query(asset):
            continue
        selected.append(deepcopy(asset))
    return selected


def _general_input_item(asset: dict[str, Any]) -> dict[str, Any]:
    return {"query": _asset_general_query(asset)}


def _build_general_messages(batch: list[dict[str, Any]]) -> list[dict[str, str]]:
    minimal = [_general_input_item(asset) for asset in batch]
    return [
        {"role": "system", "content": build_general_system_prompt()},
        {
            "role": "user",
            "content": "现在请处理下面的 JSON 数组：\n"
            + json.dumps(minimal, ensure_ascii=False, indent=2),
        },
    ]


def _call_general_llm(client: Any, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for asset in batch:
        record = deepcopy(asset)
        record["query"] = _asset_general_query(asset)
        records.append(record)
    return judge_records(
        records,
        client,
        query_field="query",
        general_field="general",
        batch_size=max(1, len(records)),
    )


def _general_payload_by_asset_id(response: dict[str, Any] | list[Any], warnings: list[str]) -> dict[str, dict[str, Any]]:
    items = response.get("assets") if isinstance(response, dict) else response
    if not isinstance(items, list):
        raise ValueError("general LLM response must contain an assets array")
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            warnings.append("general payload skipped non-object item")
            continue
        asset_id = _clean_text(item.get("asset_id"))
        if not asset_id:
            warnings.append("general payload skipped item without asset_id")
            continue
        if not isinstance(item.get("general"), bool):
            warnings.append(f"general payload for {asset_id} missing boolean general")
            continue
        by_id[asset_id] = {"asset_id": asset_id, "general": item["general"]}
    return by_id


def _apply_general_payload(asset: dict[str, Any], payload: dict[str, Any]) -> None:
    if isinstance(payload.get("general"), bool):
        asset["general"] = payload["general"]


def _classify_assets_with_llm(
    assets: list[dict[str, Any]],
    client: Any,
    *,
    batch_size: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    classified = [deepcopy(asset) for asset in assets if isinstance(asset, dict)]
    warnings: list[str] = []
    batch_size = max(1, int(batch_size or DEFAULT_KEYWORD_BATCH_SIZE))
    workers = max(1, int(workers or DEFAULT_GENERAL_WORKERS))
    batches = [
        (batch_index, start, classified[start : start + batch_size])
        for batch_index, start in enumerate(range(0, len(classified), batch_size))
    ]

    def classify_batch(batch_index: int, batch: list[dict[str, Any]]) -> tuple[int, dict[str, dict[str, Any]], list[str]]:
        batch_warnings: list[str] = []
        try:
            response = _call_general_llm(client, batch)
            by_id = {
                _clean_text(item.get("asset_id")): {
                    "asset_id": _clean_text(item.get("asset_id")),
                    "general": item["general"],
                }
                for item in response
                if _clean_text(item.get("asset_id")) and isinstance(item.get("general"), bool)
            }
        except Exception as exc:
            batch_warnings.append(f"general batch {batch_index + 1} failed: {exc}; retrying singly")
            by_id = {}
            for asset in batch:
                asset_id = _clean_text(asset.get("asset_id"))
                try:
                    single_response = _call_general_llm(client, [asset])
                    for item in single_response:
                        item_id = _clean_text(item.get("asset_id"))
                        if item_id and isinstance(item.get("general"), bool):
                            by_id[item_id] = {"asset_id": item_id, "general": item["general"]}
                except Exception as single_exc:
                    batch_warnings.append(f"general asset {asset_id} failed after single retry: {single_exc}")
        return batch_index, by_id, batch_warnings

    results_by_batch: dict[int, tuple[dict[str, dict[str, Any]], list[str]]] = {}
    if workers == 1 or len(batches) <= 1:
        for batch_index, _start, batch in batches:
            result_index, by_id, batch_warnings = classify_batch(batch_index, batch)
            results_by_batch[result_index] = (by_id, batch_warnings)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(batches))) as executor:
            futures = {
                executor.submit(classify_batch, batch_index, batch): batch_index
                for batch_index, _start, batch in batches
            }
            for future in as_completed(futures):
                result_index, by_id, batch_warnings = future.result()
                results_by_batch[result_index] = (by_id, batch_warnings)

    for batch_index, _start, batch in batches:
        by_id, batch_warnings = results_by_batch.get(batch_index, ({}, []))
        warnings.extend(batch_warnings)
        for asset in batch:
            asset_id = _clean_text(asset.get("asset_id"))
            payload = by_id.get(asset_id)
            if payload is None:
                warnings.append(f"general payload missing for {asset_id}")
                continue
            _apply_general_payload(asset, payload)
    return classified, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", default="materials_library_ppt")
    parser.add_argument("--input-json", default=None)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--keyword-batch-size", type=int, default=DEFAULT_KEYWORD_BATCH_SIZE)
    parser.add_argument("--workers", type=int, default=DEFAULT_GENERAL_WORKERS)
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--asset-ids", nargs="*", default=None)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library_dir = Path(args.library_dir).expanduser().resolve()
    if args.input_json:
        db = _read_input_json(Path(args.input_json).expanduser().resolve())
        split_dir = None
    else:
        split = _read_all_split_indexes(library_dir)
        if split is None:
            raise FileNotFoundError(f"Split indexes not found under: {library_dir}")
        db, split_dir = split
    allow_ids = set(args.asset_ids or ()) or None
    assets = _select_reclassifiable_assets(db, allow_ids)
    if not assets:
        print("No page_image/background assets to classify")
        return 0

    originals_by_id = {_clean_text(asset.get("asset_id")): deepcopy(asset) for asset in assets}
    config = Config.from_env(args.env_file)
    if not config.llm_api_key or not config.llm_model:
        raise RuntimeError("GEN_APIKEY/GEN_MODEL not configured")
    client = create_llm_client(config, web_search=False)
    classified, warnings = _classify_assets_with_llm(
        assets,
        client,
        batch_size=args.keyword_batch_size,
        workers=args.workers,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(args.report_dir) if args.report_dir else REPO_ROOT / "report" / f"general_classify_dryrun_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    diff_rows: list[dict[str, Any]] = []
    changed = 0
    for asset in classified:
        asset_id = _clean_text(asset.get("asset_id"))
        before = originals_by_id.get(asset_id, {})
        before_general = before.get("general") if isinstance(before.get("general"), bool) else None
        after_general = asset.get("general") if isinstance(asset.get("general"), bool) else None
        if before_general != after_general:
            changed += 1
        diff_rows.append(
            {
                "asset_id": asset_id,
                "query": _asset_general_query(before),
                "subject": _clean_text(before.get("subject")),
                "strict_reuse_group": _clean_text(before.get("strict_reuse_group")),
                "before_general": before_general,
                "after_general": after_general,
            }
        )

    (report_dir / "before_assets.json").write_text(
        json.dumps(list(originals_by_id.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "would_be_assets.json").write_text(
        json.dumps(classified, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "diff.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in diff_rows) + "\n",
        encoding="utf-8",
    )

    applied_index_path = None
    if args.apply:
        updated_by_id = {_clean_text(asset.get("asset_id")): asset for asset in classified}
        merged_assets: list[dict[str, Any]] = []
        for asset in db.get("assets", []):
            if not isinstance(asset, dict):
                continue
            merged = deepcopy(asset)
            updated = updated_by_id.get(_clean_text(asset.get("asset_id")))
            if updated is not None:
                _apply_general_payload(merged, updated)
            merged_assets.append(merged)
        updated_db = deepcopy(db)
        updated_db["assets"] = merged_assets
        updated_db["asset_count"] = len(merged_assets)
        existing_warnings = db.get("warnings") if isinstance(db.get("warnings"), list) else []
        updated_db["warnings"] = list(dict.fromkeys([*existing_warnings, *warnings]))
        applied_index, applied_index_path = write_ai_image_match_index(
            updated_db,
            library_dir,
            write_embedding_index=False,
        )
        (report_dir / "applied_index_snapshot.json").write_text(
            json.dumps(applied_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary_lines = [
        f"# LLM general {'apply' if args.apply else 'dry-run'} @ {timestamp}",
        "",
        f"- Library: `{library_dir}`",
        f"- Split dir: `{split_dir}`",
        f"- Model: `{config.llm_model}`",
        f"- Assets tested: {len(classified)}",
        f"- Batch size: {max(1, int(args.keyword_batch_size or DEFAULT_KEYWORD_BATCH_SIZE))}",
        f"- Workers: {max(1, int(args.workers or DEFAULT_GENERAL_WORKERS))}",
        f"- Applied to library: {'yes' if args.apply else 'no'}",
        f"- General changed: {changed}",
        "",
        "## Changed assets",
        "",
        "| asset_id | before | after | query |",
        "| --- | --- | --- | --- |",
    ]
    for row in diff_rows:
        if row["before_general"] != row["after_general"]:
            summary_lines.append(
                f"| `{row['asset_id']}` | {row['before_general']} | {row['after_general']} | {row['query']} |"
            )
    if changed == 0:
        summary_lines.append("| _(none)_ | | | |")
    if warnings:
        summary_lines.extend(["", "## Warnings", ""])
        summary_lines.extend(f"- {warning}" for warning in warnings)
    if applied_index_path is not None:
        summary_lines.extend(["", f"- Updated split indexes: `{applied_index_path}`"])
    (report_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"{'Apply' if args.apply else 'Dry-run'} complete.")
    print(f"  Tested: {len(classified)} assets")
    print(f"  General changed: {changed}")
    print(f"  Report: {report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
