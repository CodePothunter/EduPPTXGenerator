from edupptx.materials.strict_reuse_classifier import (
    MATERIAL_CATEGORY_RULES_TEXT,
    _asset_query,
    _build_classify_prompt,
)


def test_asset_query_fallback_order():
    assert _asset_query({"query": "Q", "detail_prompt": "D", "content_prompt": "C"}) == "Q"
    assert _asset_query({"detail_prompt": "D", "content_prompt": "C"}) == "D"
    assert _asset_query({"content_prompt": "C"}) == "C"
    assert _asset_query({}) == ""


def test_rules_text_keys_on_query_not_caption_or_content_prompt():
    assert "query" in MATERIAL_CATEGORY_RULES_TEXT
    assert "content_prompt" not in MATERIAL_CATEGORY_RULES_TEXT
    # caption 不再作为分类字段 token 出现在规则正文里
    assert "caption" not in MATERIAL_CATEGORY_RULES_TEXT


def test_build_classify_prompt_uses_query_field():
    prompt = _build_classify_prompt({"asset_id": "a1", "query": "12个竹笋分到3个盘子里，每个盘子4个的示意图"})
    assert "using only the query field" in prompt
    assert "12个竹笋分到3个盘子里" in prompt
    assert '"query"' in prompt


from edupptx.materials.ai_image_asset_db import (
    _asset_query as _db_asset_query,
    _apply_keyword_payload,
    _build_reuse_target_asset,
    _normalize_rich_asset_fields,
)


def test_db_asset_query_fallback():
    assert _db_asset_query({"query": "Q"}) == "Q"
    assert _db_asset_query({"detail_prompt": "D"}) == "D"
    assert _db_asset_query({"content_prompt": "C"}) == "C"


def test_normalize_preserves_query_for_page_image():
    asset = {
        "asset_id": "a1",
        "asset_kind": "page_image",
        "image_path": "x.png",
        "aspect_ratio": "1:1",
        "query": "带田字格的“点”字，四点底用红色标注",
        "caption": "点字田字格标注图",
    }
    _normalize_rich_asset_fields(asset)
    assert asset["query"] == "带田字格的“点”字，四点底用红色标注"
    assert asset["caption"] == "点字田字格标注图"


def test_keyword_payload_preserves_query():
    # 第二处 asset.clear() 重建点：富化后 query 不能被丢弃
    asset = {
        "asset_id": "a1",
        "asset_kind": "page_image",
        "image_path": "x.png",
        "aspect_ratio": "1:1",
        "query": "12个竹笋分到3个盘子里，每个盘子4个的示意图",
    }
    _apply_keyword_payload(asset, {
        "caption": "竹笋分盘的分法示意图",
        "strict_reuse_group": "C00_strict_text_problem_skip",
        "strict_reuse_confidence": 0.9,
    })
    assert asset["query"] == "12个竹笋分到3个盘子里，每个盘子4个的示意图"
    # payload 组优先：query 分类结果生效
    assert asset["strict_reuse_group"] == "C00_strict_text_problem_skip"


def test_reuse_target_carries_verbose_query_and_caption():
    target = _build_reuse_target_asset(
        asset_kind="page_image",
        prompt="12个竹笋分到3个盘子里，每个盘子4个的卡通分法示意图",
        prompt_route=None,
        theme="除法",
        grade="二年级",
        subject="数学",
        page_title="平均分",
        page_type="content",
        role="hero",
        aspect_ratio="4:3",
        caption="竹笋分盘的分法示意图",
    )
    assert target["query"] == "12个竹笋分到3个盘子里，每个盘子4个的卡通分法示意图"
    assert target["caption"] == "竹笋分盘的分法示意图"


from edupptx.materials.ai_image_asset_db import _build_keyword_messages


def test_keyword_messages_feed_query_and_classify_on_query():
    msgs = _build_keyword_messages([
        {
            "asset_id": "a1",
            "asset_kind": "page_image",
            "query": "习题几何图：AB平行于CD，AB=AC，标注∠ABC=68°",
            "caption": "平行线与等腰三角形组合的几何图",
        }
    ])
    system = msgs[0]["content"]
    user = msgs[1]["content"]
    assert "AB平行于CD" in user  # 完整 query 进入待分类输入
    assert "strict_reuse_group 分类只能基于 query" in system
    assert "strict_reuse_group 分类只能基于 caption" not in system


import importlib


def test_dry_run_audit_classifies_on_query():
    mod = importlib.import_module("scripts.dry_run_query_classify")
    item = mod._caption_input_item({"asset_id": "a1", "query": "Q-VERBOSE", "caption": "c-lossy"})
    assert item["query"] == "Q-VERBOSE"
    assert "caption" not in item
    msgs = mod._build_caption_classification_messages([{"asset_id": "a1", "query": "Q-VERBOSE"}])
    assert "Q-VERBOSE" in msgs[1]["content"]
    assert "query" in msgs[0]["content"]


from edupptx.materials.vlm_asset_enricher import (
    VLM_REDESCRIBE_SYSTEM_PROMPT,
    _apply_redescription,
)


def test_redescribe_prompt_asks_for_query_not_caption():
    assert '"query"' in VLM_REDESCRIBE_SYSTEM_PROMPT
    assert '"caption"' not in VLM_REDESCRIBE_SYSTEM_PROMPT
    assert '"strict_reuse_group"' not in VLM_REDESCRIBE_SYSTEM_PROMPT


def test_apply_redescription_sets_query_and_drops_legacy_text():
    asset = {"caption": "old", "content_prompt": "oldcp", "detail_prompt": "olddp"}
    _apply_redescription(asset, {
        "query": "习题几何图：AB平行于CD，AB=AC，标注∠ABC=68°",
        "context_summary": "几何习题配图",
        "teaching_intent": "讲解等腰三角形",
        "general": False,
    })
    assert asset["query"] == "习题几何图：AB平行于CD，AB=AC，标注∠ABC=68°"
    assert "content_prompt" not in asset
    assert "detail_prompt" not in asset


from edupptx.materials.vlm_asset_enricher import _summarize_captions_for_assets


class _FakeChatClient:
    def chat(self, *, messages, temperature=0.0, max_tokens=2048, **kwargs):
        import json as _json

        user = messages[-1]["content"]
        data = _json.loads(user.split("：\n", 1)[1])
        return _json.dumps([{"query": r["query"], "caption": r["query"][:6]} for r in data])


def test_summarize_captions_fills_caption_from_query():
    assets = [{"asset_id": "a1", "query": "习题几何图：AB平行于CD，AB=AC"}]
    _summarize_captions_for_assets(assets, _FakeChatClient())
    assert assets[0]["caption"] == "习题几何图：AB平行于CD，AB=AC"[:6]


def test_ppt_prompt_and_annotation_use_query():
    bp = importlib.import_module("scripts.build_ppt_materials_library")
    assert '"query"' in bp.PPT_VLM_SYSTEM_PROMPT
    assert '"visual_reuse_group"' not in bp.PPT_VLM_SYSTEM_PROMPT
    assert '"vlm_general"' not in bp.PPT_VLM_SYSTEM_PROMPT
    assert '"vlm_caption"' not in bp.PPT_VLM_SYSTEM_PROMPT
    assert '"content_prompt"' not in bp.PPT_VLM_SYSTEM_PROMPT
    assert '"detail_prompt"' not in bp.PPT_VLM_SYSTEM_PROMPT
    ann = bp._normalize_annotation(
        {"query": "Q-PPT", "visual_reuse_group": "C00_strict_text_problem_skip",
         "visual_reuse_confidence": 0.9, "visual_reuse_reason": "含课文",
         "context_summary": "课文片段配图，用于初读", "teaching_intent": "借拼音朗读"},
        item=None, meta={}, context={},
    )
    assert set(ann) == {"query", "context_summary", "teaching_intent", "is_backdrop"}
    assert ann["query"] == "Q-PPT"
    assert "visual_reuse_group" not in ann
    assert "content_prompt" not in ann
    assert "detail_prompt" not in ann


def test_keyword_client_from_vlm_has_chat():
    bp = importlib.import_module("scripts.build_ppt_materials_library")
    assert hasattr(bp._KeywordClientFromVLM, "chat")


def test_general_mismatch_audit_api_was_removed_with_vlm_general(tmp_path):
    bp = importlib.import_module("scripts.build_ppt_materials_library")
    assert not hasattr(bp, "_write_general_mismatch_audit")
    return
    db = {
        "assets": [
            {
                "asset_id": "a1",
                "asset_kind": "page_image",
                "image_path": "p/a1.png",
                "query": "decorated blank speech bubble sticker",
                "vlm_caption": "blank speech bubble",
                "caption": "speech bubble sticker",
                "vlm_general": True,
                "llm_general": False,
                "general": False,
                "strict_reuse_group": "C02_generic_subject_object",
                "strict_reuse_reason": "LLM says bound object",
                "visual_reuse_group": "C03_scene_decor_container",
                "visual_reuse_reason": "VLM says reusable container",
            },
            {
                "asset_id": "a2",
                "asset_kind": "page_image",
                "image_path": "p/a2.png",
                "query": "plain pencil icon",
                "vlm_caption": "pencil icon",
                "caption": "pencil icon",
                "vlm_general": True,
                "llm_general": True,
                "general": True,
                "strict_reuse_group": "C02_generic_subject_object",
                "visual_reuse_group": "C02_generic_subject_object",
            },
            {
                "asset_id": "a3",
                "asset_kind": "page_image",
                "image_path": "p/a3.png",
                "query": "missing LLM decision",
                "vlm_general": False,
                "general": False,
            },
        ],
    }

    path = bp._write_general_mismatch_audit(db, tmp_path)

    import json as _json

    data = _json.loads(path.read_text(encoding="utf-8"))
    assert data["mismatch_count"] == 1
    rec = data["mismatches"][0]
    assert rec["asset_id"] == "a1"
    assert rec["vlm_caption"] == "blank speech bubble"
    assert rec["caption"] == "speech bubble sticker"
    assert rec["vlm_general"] is True
    assert rec["llm_general"] is False
    assert rec["general"] is False
    assert rec["visual_reuse_group"] == "C03_scene_decor_container"
    assert rec["strict_reuse_group"] == "C02_generic_subject_object"


def test_query_visual_mismatch_audit_api_was_removed_with_vlm_classification(tmp_path):
    bp = importlib.import_module("scripts.build_ppt_materials_library")
    assert not hasattr(bp, "_write_query_visual_mismatch_audit")
    return
    db = {"assets": [
        {"asset_id": "a1", "asset_kind": "page_image", "image_path": "p/a1.png",
         "query": "习题几何图：AB平行于CD，AB=AC，∠ABC=68°", "caption": "几何图",
         "strict_reuse_group": "C00_strict_text_problem_skip",
         "visual_reuse_group": "C00_strict_text_problem_skip", "visual_reuse_confidence": 0.7,
         "visual_reuse_reason": "看着像结构图"},
        {"asset_id": "a2", "asset_kind": "page_image", "image_path": "p/a2.png",
         "query": "卡通熊猫", "caption": "卡通熊猫",
         "strict_reuse_group": "C02_generic_subject_object",
         "visual_reuse_group": "C02_generic_subject_object", "visual_reuse_confidence": 0.9,
         "visual_reuse_reason": "通用主体"},
    ]}
    path = bp._write_query_visual_mismatch_audit(db, tmp_path)
    import json as _json
    data = _json.loads(path.read_text(encoding="utf-8"))
    ids = [r["asset_id"] for r in data["mismatches"]]
    assert ids == ["a1"]  # 仅记录不一致项
    rec = data["mismatches"][0]
    assert rec["strict_reuse_group"] == "C00_strict_text_problem_skip"
    assert rec["visual_reuse_group"] == "C00_strict_text_problem_skip"
    assert rec["query"].startswith("习题几何图")
