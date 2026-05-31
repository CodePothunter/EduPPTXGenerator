"""Guard: precise teaching payload routes to content_points/SVG, not images.

These assertions pin the planning-stage rule so a future prompt edit can't silently
re-introduce "ask the image model to render the text" or "prefer a blank container image".
"""

from edupptx.planning.prompts import build_refinement_planning_system_prompt


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
