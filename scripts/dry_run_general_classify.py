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
        if not _clean_text(asset.get("content_prompt")):
            continue
        selected.append(deepcopy(asset))
    return selected


def _general_input_item(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": _clean_text(asset.get("asset_id")),
        "content_prompt": _clean_text(asset.get("content_prompt")),
    }


def _build_general_messages(batch: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload = {"assets": [_general_input_item(asset) for asset in batch]}
    system = (
        "你正在为素材库判断 general 字段。这是 classification-only 任务，只判断通用与否，不做元数据补全。"
        "必须只返回严格 JSON，顶层对象必须包含 assets 数组。"
        "每个 assets 项只允许包含 asset_id、general。general 必须是布尔值 true 或 false。"
        "不要返回输入文本或任何其他字段。"
        "你是严格保守的 PPT 素材跨学科通用复用分类器。general=true 表示当前图片本体可直接作为语文、数学1-8年级、八年级物理 PPT 的通用表达素材，"
        "不是判断同类型图片是否可替换。"
        "只能根据 content_prompt 判断；content_prompt 没写出的信息一律视为未知，不得补充来源、用途或课文背景。"
        "如果判断模糊，输出 false。"
        "general 决策顺序必须固定：先判断强 general=false，再判断直接 general=true，最后处理边界场景；强 false 命中时不能被通用白名单覆盖。"
        "强 general=false 包括：具体课文名、古诗名、作品名、作者名、人物名、具名人物、具名地点、具名建筑、具名工程、IP、品牌、赛事；"
        "固定文字、标签、门牌、路牌、可读段落、课文原文、固定课文句子、题干、选项、固定数字、算式、公式、答案、几何图、坐标图、统计图、测量图；"
        "学科专用工具或器材，如计算器、圆规、台秤、显微镜、望远镜、天平、实验装置、带标注仪器结构；"
        "科学或物理概念图，如光路图、成像原理图、透镜、电路、力学、天文、纳米、蒸发现象、实验结构；"
        "具体故事情节、冲突、拒绝、后果、事故、损坏、救助、疾病、痛苦、愤怒、恐惧、孤独等强情绪或故事状态。"
        "直接 general=true 包括：空白容器、空白卡片、便签、空白对话气泡、空白文本框、边框、相框、占位区、空白挂轴；"
        "通用装饰背景、普通花草树木、普通动物头像、通用图标或头像、表情 emoji、箭头、提示图标、简单装饰物；"
        "通用教学或学习动作，如泛指课文、句子、段落、绘本、书本、资料、页面的朗读、圈画、划线、标序号等普通学习动作，以及做笔记、阅读、讨论、举手；"
        "这些学习动作在没有具体可读课程文字、标题、生字拼音或固定语文知识点时可以输出 true；如果出现公式、题干或固定学科知识点则输出 false；"
        "普通校园或课堂场景、学生同行、教师讲课、课堂互动可以输出 true，自然可见的人数不是题设。"
        "普通观察、记录、放大、聚焦用途的工具或视觉隐喻，在不展示物理光路、成像原理、公式标签或具体可读文字时可以输出 true；"
        "具体可读文字、公式、光路图、成像原理图、带标注仪器结构仍输出 false。"
        "普通生活事件、公共场景、具体工具、具体自然现象，不能只因为普通就输出 true；"
        "如果主要服务单一学科，或只能强行编题、强行当背景、明显改图后才能跨学科复用，输出 false。"
        "few-shot：带装饰的空白对话气泡贴纸=>true；绿色对话气泡框内的“精读”文字=>false；"
        "中式空白挂轴装饰画=>true；卡通熊猫头像=>true；普通森林背景插画=>true；"
        "讲台上的女教师和三名举手的小学生=>true；五个背书包的小学生并肩同行的背影插画=>true；"
        "足球管理员和学生对话场景，标注一共有36个足球=>false；右手握铅笔的正确执笔姿势示意图=>true；"
        "小朋友坐着大声朗读课文=>true；小朋友用横线画课文中的句子=>true；铅笔给课文段落标序号=>true；带圈画图案和铅笔的打开绘本插画=>true；"
        "老花镜=>true；照相机镜头特写=>true；放大镜放大文字=>true；扎双丸子头的卡通小女孩和红苹果=>true；"
        "黑色台式电子计算器实物=>false；圆规绘图工具=>false；老式台秤（机械杠杆式台秤）=>false；双目光学显微镜实物=>false；"
        "贵州平塘大窝凼的FAST天眼望远镜=>false；印有流氓兔图案的长方形布艺枕头=>false；"
        "《比尾巴》课文标题插画=>false；课文原文段落朗读卡片=>false；田字格中的生字和拼音=>false；古诗文字卡片=>false；带固定课文句子的圈画示意图=>false；"
        "男孩在房间摔东西拒绝出门的场景=>false；池塘里一群小蝌蚪围着青蛙妈妈游动的卡通场景=>false；"
        "年轻男子坐在轮椅上，表情痛苦愤怒，身旁有被摔碎的杯子=>false；秋日黄昏，母亲和孩子并肩走在铺满落叶的路上a的温暖场景=>false。"
    )
    user = "请只返回这些素材的 general 判断结果：\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _call_general_llm(client: Any, batch: list[dict[str, Any]]) -> dict[str, Any] | list[Any]:
    messages = _build_general_messages(batch)
    max_tokens = max(512, min(4096, 120 * len(batch) + 800))
    chat_json = getattr(client, "chat_json", None)
    if callable(chat_json):
        response = chat_json(messages=messages, temperature=0.0, max_tokens=max_tokens, max_retries=1)
        if isinstance(response, str):
            return json.loads(_strip_json_fences(response))
        return response
    chat = getattr(client, "chat", None)
    if not callable(chat):
        raise TypeError("general classification client must provide chat_json() or chat()")
    raw = chat(messages=messages, temperature=0.0, max_tokens=max_tokens)
    return json.loads(_strip_json_fences(str(raw or "")))


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
            by_id = _general_payload_by_asset_id(response, batch_warnings)
        except Exception as exc:
            batch_warnings.append(f"general batch {batch_index + 1} failed: {exc}; retrying singly")
            by_id = {}
            for asset in batch:
                asset_id = _clean_text(asset.get("asset_id"))
                try:
                    single_response = _call_general_llm(client, [asset])
                    by_id.update(_general_payload_by_asset_id(single_response, batch_warnings))
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
                "content_prompt": _clean_text(before.get("content_prompt")),
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
        "| asset_id | before | after | content_prompt |",
        "| --- | --- | --- | --- |",
    ]
    for row in diff_rows:
        if row["before_general"] != row["after_general"]:
            summary_lines.append(
                f"| `{row['asset_id']}` | {row['before_general']} | {row['after_general']} | {row['content_prompt']} |"
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
