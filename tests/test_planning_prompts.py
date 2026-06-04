"""Guard: precise teaching payload routes to content_points/SVG, not images.

These assertions pin the planning-stage rule so a future prompt edit can't silently
re-introduce "ask the image model to render the text" or "prefer a blank container image".
"""

from edupptx.planning.prompts import (
    build_outline_planning_system_prompt,
    build_refinement_planning_system_prompt,
)


def test_refinement_prompt_routes_precise_text_to_content_points_not_image():
    prompt = build_refinement_planning_system_prompt()

    # New rule present: precise text / formula / characters / grids -> content_points + SVG;
    # images are only for scenes/illustrations.
    assert "由 SVG 生成文字/线条" in prompt
    assert "只用于场景、人物、动物、物体、情境、装饰等插画" in prompt
    assert "精确教学载荷不得由图片生成" in prompt  # rewritten planning-image-rules.md §5 title

    # Old inducements removed: do NOT tell the image model to list/render precise text,
    # and do NOT prefer blank "container" images to carry text.
    assert "query 必须列出" not in prompt
    assert "可读教学内容必须具体" not in prompt
    assert "无字容器" not in prompt


def test_outline_prompt_leaves_exercise_policy_off_by_default():
    prompt = build_outline_planning_system_prompt()

    assert "A - 复习巩固" not in prompt
    assert "B - 综合运用" not in prompt
    assert "C - 扩展探索" not in prompt


def test_outline_prompt_guides_subject_aware_exercise_distribution_when_enabled():
    prompt = build_outline_planning_system_prompt(
        exercise_policy_enabled=True,
        exercise_candidates_text="- ex_001 | A | 有图片: 否 | 计算分数加法",
    )

    assert "A - 复习巩固" in prompt
    assert "B - 综合运用" in prompt
    assert "C - 扩展探索" in prompt
    assert "exercise_refs" in prompt
    assert "ex_001" in prompt
    assert "数学可适当增加练习数量" in prompt
    assert "物理保持中等题量" in prompt
    assert "语文控制题量" in prompt
    assert "C 类扩展探索默认最多 1 题" in prompt
    assert "如果 PPT 页数较少" in prompt
