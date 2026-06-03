from edupptx.materials.strict_reuse_classifier import (
    MATERIAL_CATEGORY_RULES_TEXT,
    _build_classify_prompt,
)


def test_rules_text_says_judge_by_query():
    assert "只根据 query" in MATERIAL_CATEGORY_RULES_TEXT
    assert "只根据 content_prompt" not in MATERIAL_CATEGORY_RULES_TEXT


def test_classify_prompt_reads_query_field():
    prompt = _build_classify_prompt({"query": "kids playing games", "asset_id": "a1"})
    assert "kids playing games" in prompt
    assert "content_prompt" not in prompt


def test_classify_prompt_falls_back_to_legacy_content_prompt():
    prompt = _build_classify_prompt({"content_prompt": "legacy detailed prompt", "asset_id": "a1"})
    assert "legacy detailed prompt" in prompt
