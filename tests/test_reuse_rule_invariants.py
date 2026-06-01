"""Guard generalized reuse-rule invariants in shared prompt constants.

These deterministic substring tests do not call an LLM. Behavioral correctness
is verified by the plan's production-function checks.
"""

from edupptx.materials.caption_rules import CAPTION_RULE
from edupptx.materials.strict_reuse_classifier import MATERIAL_CATEGORY_RULES_TEXT


def test_c00_uses_substitution_invariance_not_digit_presence():
    # C00 should key on whether text/numbers are required teaching payload,
    # not whether any digits happen to be visible on an otherwise reusable tool.
    assert "替换不变性" in MATERIAL_CATEGORY_RULES_TEXT
    assert "刻度" in MATERIAL_CATEGORY_RULES_TEXT
    assert "content_prompt" not in MATERIAL_CATEGORY_RULES_TEXT
    assert "caption" not in MATERIAL_CATEGORY_RULES_TEXT


def test_c02_c03_split_landscape_by_discrete_vs_scenery():
    # Landscape wording should not flip category by itself: classify by
    # reusable discrete natural subject versus whole scenery/decor function.
    assert "离散主体" in MATERIAL_CATEGORY_RULES_TEXT
    assert "整体景观" in MATERIAL_CATEGORY_RULES_TEXT


def test_caption_preserves_named_identity_form_and_forbids_fabricated_action():
    # Named/historical/cultural identities keep form words such as portrait or
    # photo; static subjects must not invent action from background details.
    assert "确定呈现形态" in CAPTION_RULE
    assert "升格为主体谓语" in CAPTION_RULE
    assert "不写“图片/插画/这张图”等元词" in CAPTION_RULE
