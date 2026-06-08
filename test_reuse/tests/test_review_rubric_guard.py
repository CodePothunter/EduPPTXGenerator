from edupptx.materials.ai_image_asset_db import (
    REUSE_REVIEW_SCORE_RULES_REFERENCE,
    _load_reuse_review_score_rules_reference,
)


def test_rubric_final_rule_anchors_present():
    text = _load_reuse_review_score_rules_reference()
    assert "AI 图像复用审核评分规则" in text
    assert "核心教学元素" in text
    assert "外围属性" in text
    assert "特定内容类型的适用规则" in text
    assert "满足条件时可给 0.60 以上" in text
    assert "具名人物肖像" in text
    assert "几何体颜色/风格" in text
    assert "动物常识特征" in text
    assert "小蝌蚪静态动作" in text
    assert "折扇常见展示状态" in text
    assert "器材文字标注" in text
    assert "适用规则优先级" in text
    assert "不得再把同一差异写成核心缺失" in text
    assert "不得把外围差异规则用于核心缺失" in text
    assert "以下核心缺失场景不得放行" in text
    assert "场景构图" in text
    assert "动作或动作对象不确定" in text
    assert "装饰组合要求缺失" in text
    assert "额外主物体" in text
    assert "0.60-0.70" in text
    assert "unconfirmable_core" in text
    assert "禁止自造" in text
    assert "core_elements" not in text


def test_rubric_uses_final_rule_wording_without_revision_anchors():
    raw_text = REUSE_REVIEW_SCORE_RULES_REFERENCE.read_text(encoding="utf-8")
    loaded_text = _load_reuse_review_score_rules_reference()
    for text in (raw_text, loaded_text):
        assert "v2.5.3" not in text
        assert "A.3 窄召回补偿" not in text
        assert "A.2 精度护栏" not in text
        assert "A.1 召回" not in text
        assert "白名单例外" not in text
        assert "具名人物肖像补偿" not in text
        assert "几何体颜色/风格补偿" not in text
        assert "安全补偿不得恢复" not in text
        assert "非白名单不得套用召回" not in text
        assert "器材标注例外" not in text
        assert "改进建议" not in text
        assert "补丁" not in text


def test_rubric_scopes_literal_mention_clause_to_peripheral():
    text = _load_reuse_review_score_rules_reference()
    assert "只适用于外围属性" in text


def test_rubric_keeps_positive_boundary_examples_for_safe_exceptions():
    text = _load_reuse_review_score_rules_reference()
    assert "身份正确优先于画风和装饰背景" in text
    assert "不得因画风或装饰背景差异压到 0.60 以下" in text
    assert "辅助线、轮廓线、标注线" in text
    assert "不得因颜色、表现形式或辅助标注差异压到 0.60 以下" in text
    assert "普通水生环境、群体数量和尾部运动" in text
    assert "童话化外观描述" in text
    assert "按常识性外观特征处理" in text
    assert "器材文字标注可由 PPT 文字层补充" in text
    assert "不得仅因缺少文字标注压到 0.60 以下" in text


def test_rubric_keeps_minimal_output_schema():
    text = _load_reuse_review_score_rules_reference()
    for field in ("score", "brief_reason", "evidence", "risk_factors"):
        assert field in text
    assert "core_keywords" not in text
    assert "missing_constraints" not in text
    assert "core_elements" not in text
