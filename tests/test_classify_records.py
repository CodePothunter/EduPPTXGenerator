import json

from edupptx.materials.strict_reuse_classifier import classify_records


class _FakeClient:
    def chat(self, messages, temperature=0.0, max_tokens=4096):
        user = messages[-1]["content"]
        arr = json.loads(user[user.index("[") :])
        out = []
        for item in arr:
            query = item["query"]
            if "课文" in query or "竖式" in query or "光路" in query or "拼音" in query:
                group = "C00_strict_text_problem_skip"
            elif "朱自清" in query or "纪昌" in query:
                group = "C01_irreplaceable_entity_event_action"
            elif "风景" in query or "边框" in query or "空白" in query:
                group = "C03_scene_decor_container"
            else:
                group = "C02_generic_subject_object"
            out.append({"query": query, "strict_reuse_group": group})
        return json.dumps(out, ensure_ascii=False)


def test_classify_records_returns_four_class_ids():
    records = [
        {"query": "带拼音的课文段落"},
        {"query": "抱着橡果的卡通松鼠"},
        {"query": "水墨山水风景插画"},
        {"query": "戴圆框眼镜的朱自清肖像"},
    ]
    output = classify_records(records, _FakeClient(), batch_size=10)
    by_query = {record["query"]: record["strict_reuse_group"] for record in output}
    assert by_query["带拼音的课文段落"] == "C00_strict_text_problem_skip"
    assert by_query["抱着橡果的卡通松鼠"] == "C02_generic_subject_object"
    assert by_query["水墨山水风景插画"] == "C03_scene_decor_container"
    assert by_query["戴圆框眼镜的朱自清肖像"] == "C01_irreplaceable_entity_event_action"


def test_classify_records_normalizes_unknown_to_default():
    class _Bad:
        def chat(self, messages, temperature=0.0, max_tokens=4096):
            user = messages[-1]["content"]
            arr = json.loads(user[user.index("[") :])
            return json.dumps(
                [{"query": item["query"], "strict_reuse_group": "garbage"} for item in arr],
                ensure_ascii=False,
            )

    output = classify_records([{"query": "x"}], _Bad(), batch_size=1)
    assert output[0]["strict_reuse_group"] == "C03_scene_decor_container"
