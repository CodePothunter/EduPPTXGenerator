from edupptx.materials.ai_image_asset_db import _asset_generation_prompt, _build_reuse_target_asset


def test_dedup_key_not_collapsed_by_coarse_caption():
    a = _build_reuse_target_asset(
        asset_kind="page_image",
        prompt="one red apple",
        prompt_route=None,
        theme="t",
        grade="g",
        subject="s",
        page_title="p",
        page_type="content",
        role="illustration",
        aspect_ratio="4:3",
        caption="apple",
    )
    b = _build_reuse_target_asset(
        asset_kind="page_image",
        prompt="three green apples",
        prompt_route=None,
        theme="t",
        grade="g",
        subject="s",
        page_title="p",
        page_type="content",
        role="illustration",
        aspect_ratio="4:3",
        caption="apple",
    )

    assert a["asset_id"] != b["asset_id"]
    assert a["caption"] == b["caption"] == "apple"
    assert "content_prompt" not in a
    assert "content_prompt" not in b


def test_generation_prompt_does_not_fall_back_to_caption():
    assert _asset_generation_prompt({"caption": "apple"}) == ""
    assert _asset_generation_prompt({"caption": "apple", "normalized_prompt": "detailed apple prompt"}) == (
        "detailed apple prompt"
    )
