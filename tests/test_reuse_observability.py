"""Tests for the per-session logical-need summary and the coverage gap log.

These tests use a small synthetic ``ai_image_reuse_debug.json`` shaped like
the real one, so they exercise the grouping logic without requiring an
actual generation run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edupptx.materials.reuse_observability import (
    COVERAGE_GAP_EMBEDDING_CEILING,
    DEFAULT_COVERAGE_LOG_FILENAME,
    DEFAULT_SUMMARY_FILENAME,
    aggregate_coverage_log,
    append_coverage_gap_events,
    load_debug_records,
    write_reuse_logical_summary,
)


def _make_debug_payload() -> dict:
    """Synthetic two-library debug file with three logical needs.

    Need 1 (page=2, slot=illustration_1): matched in ai library.
    Need 2 (page=4, slot=illustration_1): rejected in both libraries, but
        had a candidate at embedding 0.72 (high semantic, policy rejected).
    Need 3 (page=9, slot=illustration_2): coverage gap — no library produced
        any candidate with embedding >= 0.6.
    """

    return {
        "schema_version": 1,
        "queries": [
            # Need 1: matched in ai library
            {
                "context": {
                    "page_number": 2,
                    "slot_key": "illustration_1",
                    "reuse_library_dir": "/abs/path/materials_library",
                },
                "asset_root": "/abs/path/materials_library",
                "target": {
                    "content_prompt": "通用学生学习场景",
                    "subject": "语文",
                    "grade_norm": "二年级",
                    "topic_refs": ["雾在哪里"],
                    "core_keywords": ["学生", "学习"],
                    "role": "illustration",
                    "asset_kind": "page_image",
                    "reuse_level": "loose",
                    "aspect_ratio": "16:9",
                },
                "decision": {
                    "reused": True,
                    "asset_id": "aiimg_matched_001",
                    "keyword_score": 0.71,
                    "reason": "reused_by_hybrid_retrieval_score",
                    "llm_reuse_review_performed": False,
                },
                "no_reuse_top_candidates": [],
            },
            # Need 1 in ppt library: not used because ai matched
            {
                "context": {
                    "page_number": 2,
                    "slot_key": "illustration_1",
                    "reuse_library_dir": "/abs/path/materials_library_ppt",
                },
                "asset_root": "/abs/path/materials_library_ppt",
                "target": {
                    "content_prompt": "通用学生学习场景",
                    "subject": "语文",
                    "topic_refs": ["雾在哪里"],
                },
                "decision": {
                    "reused": False,
                    "reason": "no_candidate_above_reuse_threshold",
                },
                "no_reuse_top_candidates": [],
            },
            # Need 2 in ai library: rejected, low signal
            {
                "context": {
                    "page_number": 4,
                    "slot_key": "illustration_1",
                    "reuse_library_dir": "/abs/path/materials_library",
                },
                "asset_root": "/abs/path/materials_library",
                "target": {
                    "content_prompt": "凸透镜光路图",
                    "subject": "物理",
                    "grade_norm": "八年级",
                    "topic_refs": ["凸透镜成像"],
                    "core_keywords": ["凸透镜", "光路图"],
                    "role": "illustration",
                    "asset_kind": "page_image",
                    "reuse_level": "strict",
                    "aspect_ratio": "16:9",
                },
                "decision": {
                    "reused": False,
                    "reason": "no_candidate_after_reuse_policy_or_occupancy",
                },
                "no_reuse_top_candidates": [
                    {
                        "asset_id": "aiimg_weak_001",
                        "keyword_score": 0.30,
                        "embedding_score": 0.50,
                        "reuse_policy": {"decision": "reject", "reason": "below_threshold"},
                        "llm_reuse_review_performed": False,
                    }
                ],
            },
            # Need 2 in ppt library: rejected, high semantic candidate
            {
                "context": {
                    "page_number": 4,
                    "slot_key": "illustration_1",
                    "reuse_library_dir": "/abs/path/materials_library_ppt",
                },
                "asset_root": "/abs/path/materials_library_ppt",
                "target": {
                    "content_prompt": "凸透镜光路图",
                    "subject": "物理",
                    "topic_refs": ["凸透镜成像"],
                },
                "decision": {
                    "reused": False,
                    "reason": "no_candidate_after_reuse_policy_or_occupancy",
                },
                "no_reuse_top_candidates": [
                    {
                        "asset_id": "kbpptx_high_001",
                        "keyword_score": 0.30,
                        "embedding_score": 0.72,
                        "reuse_policy": {"decision": "reject", "reason": "llm_score_review_rejected"},
                        "llm_reuse_review_performed": True,
                    }
                ],
            },
            # Need 3: coverage gap (no candidate above 0.6 embedding)
            {
                "context": {
                    "page_number": 9,
                    "slot_key": "illustration_2",
                    "reuse_library_dir": "/abs/path/materials_library",
                },
                "asset_root": "/abs/path/materials_library",
                "target": {
                    "content_prompt": "u<f时凸透镜成正立放大虚像的光路示意图",
                    "subject": "物理",
                    "grade_norm": "八年级",
                    "topic_refs": ["凸透镜成像"],
                    "core_keywords": ["凸透镜", "u<f", "正立放大虚像"],
                    "role": "illustration",
                    "asset_kind": "page_image",
                    "reuse_level": "strict",
                    "aspect_ratio": "4:3",
                },
                "decision": {
                    "reused": False,
                    "reason": "no_candidate_above_reuse_threshold",
                },
                "no_reuse_top_candidates": [
                    {
                        "asset_id": "aiimg_far_001",
                        "keyword_score": 0.10,
                        "embedding_score": 0.42,
                        "reuse_policy": {"decision": "reject"},
                    }
                ],
            },
            {
                "context": {
                    "page_number": 9,
                    "slot_key": "illustration_2",
                    "reuse_library_dir": "/abs/path/materials_library_ppt",
                },
                "asset_root": "/abs/path/materials_library_ppt",
                "target": {
                    "content_prompt": "u<f时凸透镜成正立放大虚像的光路示意图",
                    "subject": "物理",
                    "topic_refs": ["凸透镜成像"],
                },
                "decision": {
                    "reused": False,
                    "reason": "no_candidate_above_reuse_threshold",
                },
                "no_reuse_top_candidates": [],
            },
        ],
    }


def _write_debug(tmp_path: Path) -> Path:
    debug_path = tmp_path / "session_demo" / "materials" / "ai_image_reuse_debug.json"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(json.dumps(_make_debug_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    return debug_path


# ---------------------------------------------------------------------------
# Logical summary (Q6)
# ---------------------------------------------------------------------------

def test_summary_groups_by_logical_need(tmp_path: Path):
    debug_path = _write_debug(tmp_path)
    summary = write_reuse_logical_summary(debug_path)
    assert summary is not None
    assert summary["logical_check_count"] == 3
    assert summary["matched_count"] == 1
    # Match rate = 1/3
    assert summary["match_rate"] == pytest.approx(0.3333, abs=0.001)
    # Each row should list both libraries searched
    for row in summary["logical_checks"]:
        assert row["searched_libraries"]  # non-empty
        assert "ai library" not in row["target_summary"]  # smoke for excerpt


def test_summary_writes_to_default_path(tmp_path: Path):
    debug_path = _write_debug(tmp_path)
    write_reuse_logical_summary(debug_path)
    summary_path = debug_path.with_name(DEFAULT_SUMMARY_FILENAME)
    assert summary_path.exists()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] >= 1


def test_summary_failure_categories(tmp_path: Path):
    debug_path = _write_debug(tmp_path)
    summary = write_reuse_logical_summary(debug_path)
    categories = {row["slot_key"]: row["failure_category"] for row in summary["logical_checks"]}
    # Need 2: page=4 slot=illustration_1 — had a 0.72 embedding candidate rejected by policy
    assert categories.get("illustration_1") in {"matched", "high_semantic_rejected_by_policy"}
    # Need 3: page=9 slot=illustration_2 — low semantic across both libs (coverage gap)
    assert categories.get("illustration_2") in {"low_semantic_signal", "no_candidate_above_threshold"}


def test_summary_returns_none_when_debug_missing(tmp_path: Path):
    """No debug file should be a no-op, not raise."""

    result = write_reuse_logical_summary(tmp_path / "no_such_debug.json")
    assert result is None


def test_summary_returns_none_for_empty_debug(tmp_path: Path):
    debug_path = tmp_path / "empty.json"
    debug_path.write_text(json.dumps({"queries": []}, ensure_ascii=False), encoding="utf-8")
    result = write_reuse_logical_summary(debug_path)
    assert result is None


# ---------------------------------------------------------------------------
# Coverage gap log (R6)
# ---------------------------------------------------------------------------

def test_coverage_log_appends_only_uncovered_needs(tmp_path: Path):
    debug_path = _write_debug(tmp_path)
    log_path = tmp_path / DEFAULT_COVERAGE_LOG_FILENAME
    appended = append_coverage_gap_events(debug_path, log_path=log_path)

    # Need 1 matched → no event
    # Need 2 had embedding 0.72 across libraries → above ceiling, no event
    # Need 3 had max embedding 0.42 → below ceiling, ONE event
    assert appended == 1
    assert log_path.exists()
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["slot_key"] == "illustration_2"
    assert row["best_embedding"] < COVERAGE_GAP_EMBEDDING_CEILING


def test_coverage_log_aggregation(tmp_path: Path):
    debug_path = _write_debug(tmp_path)
    log_path = tmp_path / DEFAULT_COVERAGE_LOG_FILENAME
    append_coverage_gap_events(debug_path, log_path=log_path)
    # Append a second session worth of the same gap to bump the count
    append_coverage_gap_events(debug_path, log_path=log_path)

    aggregate = aggregate_coverage_log(log_path)
    assert aggregate["events"] == 2
    # The gap is about 物理 / 凸透镜成像
    assert any("物理" in key for key in aggregate["by_subject_topic"])
    assert any("凸透镜成像" in key for key in aggregate["by_subject_topic"])


def test_coverage_log_appends_nothing_when_no_debug(tmp_path: Path):
    log_path = tmp_path / "log.jsonl"
    appended = append_coverage_gap_events(tmp_path / "missing.json", log_path=log_path)
    assert appended == 0
    # No file should be created either
    assert not log_path.exists()


def test_load_debug_records_handles_corrupt_file(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text("definitely not json", encoding="utf-8")
    assert load_debug_records(path) == []
