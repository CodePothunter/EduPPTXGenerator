from edupptx.materials.caption_rules import CAPTION_RULE, build_caption_system_prompt


def test_caption_rule_has_core_branches():
    for marker in ["可复用的主体内容", "动作/事件", "场景", "图示", "装饰"]:
        assert marker in CAPTION_RULE


def test_caption_rule_forbids_meta_words():
    assert "不写“图片/插画/这张图”等元词" in CAPTION_RULE


def test_build_caption_system_prompt_embeds_rule():
    prompt = build_caption_system_prompt()
    assert CAPTION_RULE in prompt
    assert "JSON" in prompt
