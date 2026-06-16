"""把 sonnet workflow 评标结果合进 targets.jsonl，输出 goldset.json（schema_version=1, items[]）。

用法：python -m test_reuse.assemble_goldset <run_dir> <labels.json> <out_goldset.json>
labels.json = workflow 产出的数组，每条 {need_id, should_reuse, acceptable_asset_ids, best_asset_ids, label_notes}。
target 元数据原样取自 01_prepare/targets.jsonl，仅填标签字段。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    run_dir = Path(sys.argv[1])
    labels_path = Path(sys.argv[2])
    out_path = Path(sys.argv[3])

    targets = []
    for line in (run_dir / "01_prepare" / "targets.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            targets.append(json.loads(line))

    labels = {row["need_id"]: row for row in json.loads(labels_path.read_text(encoding="utf-8")) if row}

    items = []
    labeled = 0
    positives = 0
    for t in targets:
        nid = t["need_id"]
        lbl = labels.get(nid)
        item = dict(t)
        if lbl is not None:
            acc = [a for a in (lbl.get("acceptable_asset_ids") or []) if isinstance(a, str) and a]
            best = [a for a in (lbl.get("best_asset_ids") or []) if isinstance(a, str) and a]
            should = bool(lbl.get("should_reuse")) and bool(acc)
            item["label_status"] = "labeled"
            item["should_reuse"] = should
            item["acceptable_asset_ids"] = acc
            item["best_asset_ids"] = best or (acc[:1] if acc else [])
            item["label_notes"] = str(lbl.get("label_notes") or "")
            item["label_method"] = "sonnet_workflow_judge"
            labeled += 1
            positives += 1 if should else 0
        else:
            item["label_status"] = "unlabeled"
        items.append(item)

    out_path.write_text(json.dumps({"schema_version": 1, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"items={len(items)} labeled={labeled} positives={positives} → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
