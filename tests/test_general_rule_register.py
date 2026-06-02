from edupptx.materials.general_rules import GENERAL_RULE


def test_general_rule_makes_whole_scenes_false_not_brushwork():
    t = GENERAL_RULE
    assert "通用场景与国画风景" not in t
    assert "即便用国画风格仍可 true" not in t
    assert "整体" in t and "风景" in t
    assert "离散" in t


def test_general_rule_examples_flip_landscape_false_keep_discrete_true():
    t = GENERAL_RULE
    assert "山水" in t
    assert "松鼠" in t or "飞鸟" in t
