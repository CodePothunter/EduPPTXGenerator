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
            generic = q.replace("西湖", "").replace("卢沟桥的", "").strip()
            out.append(
                {
                    "query": q,
                    "secondary_reuse_query": generic,
                    "secondary_reuse_caption": generic,
                }
            )
        return json.dumps(out, ensure_ascii=False)


def test_secondary_scene_rule_demands_strip_proper_noun():
    from edupptx.materials.caption_rules import SECONDARY_SCENE_CAPTION_RULE

    t = SECONDARY_SCENE_CAPTION_RULE
    assert "专名" in t and ("去名" in t or "删" in t)
    assert "通用场景" in t


def test_summarize_secondary_scene_strips_landmark_keeps_weather():
    from edupptx.materials.caption_rules import summarize_secondary_scene_records

    rows = summarize_secondary_scene_records([{"query": "西湖晴天湖景"}], _FakeSceneClient())
    assert rows[0]["secondary_reuse_query"] == "晴天湖景"
    assert rows[0]["secondary_reuse_caption"] == "晴天湖景"


def test_caption_rule_keeps_c01_named_identity_but_denames_c03_projection():
    from edupptx.materials.caption_rules import CAPTION_RULE, SECONDARY_SCENE_CAPTION_RULE

    assert "C01 canonical" in CAPTION_RULE
    assert "上下文明确具名身份" in CAPTION_RULE
    assert "图像形态支持" in CAPTION_RULE
    assert "不得因 VLM 不能从像素独立识别" in CAPTION_RULE
    assert "C03 projection" in SECONDARY_SCENE_CAPTION_RULE
    assert "C01 canonical" in SECONDARY_SCENE_CAPTION_RULE


class _CaptionAwareSceneClient:
    """De-names the primary *caption* for secondary_reuse_caption and the
    verbose *query* for secondary_reuse_query. If the production payload omits
    the primary caption, ``cap`` stays empty and the caption cannot be produced
    from it — exactly the bug we are fixing."""

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        user = messages[-1]["content"]
        arr = json.loads(user[user.index("[") :])
        out = []
        for item in arr:
            q = item.get("query", "")
            cap = item.get("caption", "")
            out.append(
                {
                    "query": q,
                    "secondary_reuse_query": q.replace("西湖", "").strip(),
                    "secondary_reuse_caption": cap.replace("西湖", "").strip(),
                }
            )
        return json.dumps(out, ensure_ascii=False)


def test_secondary_caption_denames_primary_caption_not_verbose_query():
    from edupptx.materials.caption_rules import summarize_secondary_scene_records

    rows = summarize_secondary_scene_records(
        [
            {
                "query": "西湖水景插画，有拱桥、湖面小舟、中式古建、垂柳与远山",
                "caption": "西湖水景",
            }
        ],
        _CaptionAwareSceneClient(),
    )
    # caption is the de-named *primary caption* (terse, foils already dropped),
    # NOT a re-summary of the verbose query.
    assert rows[0]["secondary_reuse_caption"] == "水景"
    # query stays the de-named verbose query (regen/review payload).
    assert rows[0]["secondary_reuse_query"] == "水景插画，有拱桥、湖面小舟、中式古建、垂柳与远山"


def test_secondary_scene_rule_anchors_caption_on_primary_caption_and_drops_foils():
    from edupptx.materials.caption_rules import SECONDARY_SCENE_CAPTION_RULE

    t = SECONDARY_SCENE_CAPTION_RULE
    # caption = de-named primary caption (same terseness), not re-summary of query
    assert "主 caption" in t
    # juxtaposed foils dropped, mirroring the main CAPTION_RULE discipline
    assert "陪衬" in t
