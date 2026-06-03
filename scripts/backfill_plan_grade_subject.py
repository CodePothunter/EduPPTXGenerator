# -*- coding: utf-8 -*-
"""回填存量 plan.json 的 deck 级 grade/subject/grade_band。

用法：
    python scripts/backfill_plan_grade_subject.py --output-dir output            # dry-run
    python scripts/backfill_plan_grade_subject.py --output-dir output --apply    # 写回
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edupptx.materials.ai_image_asset_db import resolve_meta_grade_subject

_FIELDS = ("subject", "grade", "grade_band")


def iter_plan_files(output_dir: str | Path) -> Iterator[Path]:
    root = Path(output_dir)
    if (root / "plan.json").exists():
        yield root / "plan.json"
        return
    if not root.exists():
        return
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        plan = child / "plan.json"
        if child.is_dir() and plan.exists():
            yield plan


def backfill_plan_file(plan_path: str | Path, *, apply: bool = False) -> dict[str, Any]:
    path = Path(plan_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    resolved = resolve_meta_grade_subject(
        llm_subject=meta.get("subject", ""),
        llm_grade=meta.get("grade", ""),
        llm_grade_band=meta.get("grade_band", ""),
        topic=meta.get("topic", ""),
        audience=meta.get("audience", ""),
    )
    before = {key: meta.get(key) for key in _FIELDS}
    changed = any(before.get(key) != resolved[key] for key in _FIELDS)
    if apply and changed:
        meta.update(resolved)
        data["meta"] = meta
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"plan": str(path), "before": before, "after": resolved, "changed": changed}


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 plan.json 的 grade/subject/grade_band")
    parser.add_argument("--output-dir", default="output", help="输出根目录或单个 session 目录")
    parser.add_argument("--apply", action="store_true", help="写回；缺省为 dry-run")
    args = parser.parse_args()

    changed_count = 0
    total = 0
    for plan in iter_plan_files(args.output_dir):
        total += 1
        result = backfill_plan_file(plan, apply=args.apply)
        flag = "CHANGED" if result["changed"] else "ok"
        print(f"[{flag}] {result['plan']}: {result['before']} -> {result['after']}")
        if result["changed"]:
            changed_count += 1
    mode = "applied" if args.apply else "dry-run"
    print(f"\n{mode}: {changed_count}/{total} plan(s) need backfill")


if __name__ == "__main__":
    main()
