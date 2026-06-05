from edupptx.materials.ai_image_asset_db import (
    REUSE_REVIEW_SCORE_RULES_REFERENCE,
    _load_reuse_review_score_rules_reference,
)


def test_rubric_v253_narrow_recall_anchors_present():
    text = _load_reuse_review_score_rules_reference()
    assert "v2.5.3" in text
    assert "核心教学元素" in text
    assert "外围属性" in text
    assert "A.3 窄召回补偿" in text
    assert "白名单例外" in text
    assert "具名人物肖像补偿" in text
    assert "几何体颜色/风格补偿" in text
    assert "安全补偿不得恢复 A.1 宽泛召回" in text
    assert "非白名单不得套用召回" in text
    assert "场景构图" in text
    assert "动作或动作对象不确定" in text
    assert "装饰组合要求缺失" in text
    assert "额外主物体" in text
    assert "器材标注例外" in text
    assert "0.60-0.70" in text
    assert "unconfirmable_core" in text
    assert "禁止自造" in text
    assert "core_elements" not in text


def test_rubric_keeps_a2_as_comment_only_for_rollback():
    raw_text = REUSE_REVIEW_SCORE_RULES_REFERENCE.read_text(encoding="utf-8")
    loaded_text = _load_reuse_review_score_rules_reference()
    assert "<!-- A.2 历史版本：已注释，仅用于回滚" in raw_text
    assert "## A.2 精度护栏：A.1 召回只做白名单例外" in raw_text
    assert "A.2 历史版本：已注释" not in loaded_text
    assert "## A.2 精度护栏：A.1 召回只做白名单例外" not in loaded_text


def test_rubric_scopes_literal_mention_clause_to_peripheral():
    text = _load_reuse_review_score_rules_reference()
    assert "只适用于外围属性" in text


def test_rubric_keeps_minimal_output_schema():
    text = _load_reuse_review_score_rules_reference()
    for field in ("score", "brief_reason", "evidence", "risk_factors"):
        assert field in text
    assert "core_keywords" not in text
    assert "missing_constraints" not in text
    assert "core_elements" not in text
