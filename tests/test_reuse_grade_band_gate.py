# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from edupptx.materials import ai_image_asset_db as db

_LIB = Path(__file__).resolve().parents[1] / "materials_library_ppt"


def test_normalize_grade_info_derives_band_from_norm():
    # 第二参数缺失/非法 band 时，从 grade_norm 派生
    info = db.normalize_grade_info("八年级", "")
    assert info == {"grade_norm": "八年级", "grade_band": "高年级"}
    info2 = db.normalize_grade_info("三年级", "某课程路径/三年级/上册")
    assert info2 == {"grade_norm": "三年级", "grade_band": "低年级"}
    # 第二参数是合法 band 时尊重它
    info3 = db.normalize_grade_info("八年级", "低年级")
    assert info3 == {"grade_norm": "八年级", "grade_band": "低年级"}


def test_effective_grade_band_for_stored_other():
    # 存量资产 band=其他，但 grade_norm 已知 → 派生出有效 band
    asset = {"grade_norm": "三年级", "grade_band": "其他"}
    assert db._effective_grade_band(asset) == "低年级"


def test_target_metadata_not_unknown_when_grade_known():
    # 模拟 target：subject 已知 + grade_norm 已知 + band=其他(存量)
    target = {"subject": "语文", "grade_norm": "三年级", "grade_band": "其他"}
    assert "grade_band" not in db._target_metadata_unknown_fields(target)
    # grade_norm 未知时仍判 unknown
    t2 = {"subject": "语文", "grade_norm": "其他", "grade_band": "其他"}
    assert "grade_norm" in db._target_metadata_unknown_fields(t2)


def test_build_target_uses_explicit_grade_band():
    # 显式传 grade_band 时，target band 取它（LLM 权威），而非仅从 grade 派生
    t = db._build_reuse_target_asset(
        asset_kind="page_image", prompt="青铜簋", prompt_route=None,
        theme="八年级物理《质量》", grade="八年级", subject="物理",
        page_title="", page_type="content", role="illustration",
        aspect_ratio="4:3", grade_band="低年级",
    )
    assert t["grade_band"] == "低年级"
    # 不传 grade_band 时，从 grade 派生
    t2 = db._build_reuse_target_asset(
        asset_kind="page_image", prompt="青铜簋", prompt_route=None,
        theme="八年级物理《质量》", grade="八年级", subject="物理",
        page_title="", page_type="content", role="illustration",
        aspect_ratio="4:3",
    )
    assert t2["grade_band"] == "高年级"


def test_agent_reuse_context_includes_grade_band():
    from edupptx.agent import PPTXAgent
    from edupptx.models import PlanningDraft, PlanningMeta, PagePlan
    draft = PlanningDraft(
        meta=PlanningMeta(topic="八年级物理", subject="物理", grade="八年级", grade_band="高年级"),
        pages=[PagePlan(page_number=1, page_type="cover", title="x")],
    )
    ctx = PPTXAgent._ai_image_reuse_context(draft)
    assert ctx["grade"] == "八年级"
    assert ctx["subject"] == "物理"
    assert ctx["grade_band"] == "高年级"


@pytest.mark.skipif(
    not (_LIB / "strict_reuse_indexes" / "C01_irreplaceable_entity_event_action.json").exists(),
    reason="materials_library_ppt 不存在，跳过 e2e 召回测试",
)
def test_e2e_reuse_no_longer_short_circuits_on_grade_band():
    from edupptx.materials.ai_image_asset_db import (
        ReuseSearchContext,
        _build_reuse_target_asset,
        _eligible_reuse_assets,
        _load_reuse_library_for_search,
        _normalize_asset_for_match,
    )

    # 公开 API：target 不再因 grade_band 短路（修复前恒为 target_metadata_unknown）
    res = db.find_reusable_ai_image_asset(
        library_dir=str(_LIB), asset_kind="page_image",
        prompt="小学生在教室里朗读课文", prompt_route=None,
        theme="三年级语文《荷花》课文教学",
        grade="三年级", subject="语文", grade_band="低年级",
        page_title="荷花", page_type="content",
        role="illustration", aspect_ratio="4:3",
        keyword_client=None, llm_review_enabled=False,
        _collect_candidates_only=True,
    )
    assert isinstance(res, dict)
    assert res.get("empty_reason") != "target_metadata_unknown"

    # 闸门层：subject/grade_band/aspect 硬过滤后仍放行大量同学科候选。
    # 修复前 grade_band='其他' 会把候选全判 candidate_metadata_unknown（eligible=0）。
    rc = ReuseSearchContext()
    loaded = _load_reuse_library_for_search(_LIB, rc)
    assets = loaded["index"].get("assets")
    target = _build_reuse_target_asset(
        asset_kind="page_image", prompt="小学生在教室里朗读课文", prompt_route=None,
        theme="三年级语文《荷花》课文教学", grade="三年级", subject="语文",
        page_title="荷花", page_type="content", role="illustration",
        aspect_ratio="4:3", grade_band="低年级",
    )
    target = _normalize_asset_for_match(target, for_target=True) or target
    assert db._target_unknown_fields_for_reuse(target) == []
    _eligible, summary = _eligible_reuse_assets(target, assets, rc, _LIB, "")
    assert summary["eligible_count"] > 0
