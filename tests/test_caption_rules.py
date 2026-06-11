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


def test_caption_rule_drops_form_noise_keeps_painting_medium():
    assert "形态噪声词" in CAPTION_RULE  # 删纯形式词
    assert "画种" in CAPTION_RULE  # 保留画种/媒介词


def test_caption_rule_has_enumeration_and_gesture_sharpening():
    assert "陪衬" in CAPTION_RULE  # P2 枚举留焦点删陪衬
    assert "讲解" in CAPTION_RULE  # P3 手势→动作（few-shot）


def test_caption_rule_few_shot_landmark_scene():
    assert "寒山寺江景水墨画" in CAPTION_RULE
