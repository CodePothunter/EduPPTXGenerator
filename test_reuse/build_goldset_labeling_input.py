"""从 prepare+retrieve 的 run_dir 抽出 sonnet 评标输入（每 need：target 文本 + 候选池）。

用法：python -m test_reuse.build_goldset_labeling_input <run_dir> <out.json>
输出 JSON 数组，每条 = {need_id, target:{...}, candidates:[{asset_id,caption,strict_reuse_group}]}。
候选取自 03_retrieve/candidate_collections.jsonl 的 collection.candidates[*].asset（top-K 召回池）。
needs 无候选 → candidates=[]（评标时 should_reuse=False）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _clean(v) -> str:
    return str(v or "").strip()


def main() -> int:
    run_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    targets = {}
    for line in (run_dir / "01_prepare" / "targets.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        targets[row["need_id"]] = row

    cands_by_need: dict[str, list[dict]] = {}
    coll_path = run_dir / "03_retrieve" / "candidate_collections.jsonl"
    if coll_path.exists():
        for line in coll_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            need_id = row.get("need_id")
            collection = row.get("collection") or {}
            out = []
            for cand in collection.get("candidates") or []:
                asset = cand.get("asset") or {}
                aid = _clean(asset.get("asset_id"))
                if not aid:
                    continue
                out.append({
                    "asset_id": aid,
                    "caption": _clean(asset.get("caption")),
                    "strict_reuse_group": _clean(asset.get("strict_reuse_group")),
                    "subject": _clean(asset.get("subject")),
                    "policy_score": round(float(cand.get("policy_score") or 0.0), 4),
                })
            cands_by_need[need_id] = out

    items = []
    for need_id, t in targets.items():
        items.append({
            "need_id": need_id,
            "target": {
                "raw_query": _clean(t.get("raw_query")),
                "caption": _clean(t.get("caption")),
                "content_prompt": _clean(t.get("content_prompt")),
                "subject": _clean(t.get("subject")),
                "grade_norm": _clean(t.get("grade_norm")),
                "strict_reuse_group": _clean(t.get("strict_reuse_group")),
                "asset_kind": _clean(t.get("asset_kind")),
                "aspect_ratio": _clean(t.get("aspect_ratio")),
                "target_is_c00_skip": bool(t.get("target_is_c00_skip")),
            },
            "candidates": cands_by_need.get(need_id, []),
        })

    out_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    with_cands = sum(1 for it in items if it["candidates"])
    print(f"needs={len(items)} with_candidates={with_cands} → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
