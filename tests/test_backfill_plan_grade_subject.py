# -*- coding: utf-8 -*-
import json
from pathlib import Path
from scripts.backfill_plan_grade_subject import backfill_plan_file, iter_plan_files


def _write_plan(path: Path, meta: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"meta": meta, "pages": []}, ensure_ascii=False), encoding="utf-8")


def test_backfill_dry_run_does_not_write(tmp_path):
    plan = tmp_path / "session_x" / "plan.json"
    _write_plan(plan, {"topic": "八年级物理《质量》", "audience": "初中八年级学生"})
    result = backfill_plan_file(plan, apply=False)
    assert result["after"] == {"subject": "物理", "grade": "八年级", "grade_band": "高年级"}
    assert result["changed"] is True
    # dry-run 不写盘
    on_disk = json.loads(plan.read_text(encoding="utf-8"))
    assert "subject" not in on_disk["meta"]


def test_backfill_apply_writes_and_is_idempotent(tmp_path):
    plan = tmp_path / "session_y" / "plan.json"
    _write_plan(plan, {"topic": "三年级语文《荷花》", "audience": ""})
    backfill_plan_file(plan, apply=True)
    on_disk = json.loads(plan.read_text(encoding="utf-8"))
    assert on_disk["meta"]["subject"] == "语文"
    assert on_disk["meta"]["grade"] == "三年级"
    assert on_disk["meta"]["grade_band"] == "低年级"
    # 再跑一次：幂等，无变化
    result2 = backfill_plan_file(plan, apply=True)
    assert result2["changed"] is False


def test_iter_plan_files_finds_sessions(tmp_path):
    _write_plan(tmp_path / "session_a" / "plan.json", {"topic": "x"})
    _write_plan(tmp_path / "session_b" / "plan.json", {"topic": "y"})
    found = sorted(p.parent.name for p in iter_plan_files(tmp_path))
    assert found == ["session_a", "session_b"]
