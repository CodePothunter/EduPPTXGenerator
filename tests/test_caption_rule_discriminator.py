import json

from edupptx.materials.caption_rules import CAPTION_RULE


def test_caption_rule_keeps_discriminating_attributes():
    t = CAPTION_RULE
    assert "区分" in t
    assert "天气" in t and ("晴" in t or "阴" in t)
    assert "氛围、表情、构图等装饰修饰" not in t


class _FakeSceneClient:
    def chat(self, messages, temperature=0.0, max_tokens=4096):
        user = messages[-1]["content"]
        arr = json.loads(user[user.index("[") :])
        out = []
        for item in arr:
            q = item["query"]
            cap = q.replace("西湖", "").replace("卢沟桥的", "").strip()
            out.append({"query": q, "secondary_reuse_caption": cap})
        return json.dumps(out, ensure_ascii=False)


def test_secondary_scene_rule_demands_strip_proper_noun():
    from edupptx.materials.caption_rules import SECONDARY_SCENE_CAPTION_RULE

    t = SECONDARY_SCENE_CAPTION_RULE
    assert "专名" in t and ("去名" in t or "删" in t)
    assert "通用场景" in t


def test_summarize_secondary_scene_strips_landmark_keeps_weather():
    from edupptx.materials.caption_rules import summarize_secondary_scene_records

    rows = summarize_secondary_scene_records([{"query": "西湖晴天湖景"}], _FakeSceneClient())
    assert rows[0]["secondary_reuse_caption"] == "晴天湖景"
