import json
from pathlib import Path

from edupptx.materials.ai_image_asset_db import _build_keyword_messages
from edupptx.materials.caption_rules import CAPTION_RULE
from scripts.build_ppt_materials_library import (
    PPT_VLM_SYSTEM_PROMPT,
    RawPptImage,
    _build_asset_from_annotation,
    _enrich_ppt_assets_with_llm,
    _normalize_annotation,
    _resolve_ppt_asset_kind,
)


def test_vlm_prompt_drops_classification_and_general_and_caption():
    prompt = PPT_VLM_SYSTEM_PROMPT
    assert "visual_reuse_group" not in prompt
    assert "vlm_general" not in prompt
    assert "vlm_caption" not in prompt


def test_vlm_prompt_keeps_query_context_intent():
    prompt = PPT_VLM_SYSTEM_PROMPT
    assert "query" in prompt and "context_summary" in prompt and "teaching_intent" in prompt


def test_vlm_prompt_requires_named_entity_and_drops_decoration():
    prompt = PPT_VLM_SYSTEM_PROMPT
    assert "专名" in prompt
    assert "年级" in prompt and "学科" in prompt


def _raw():
    return RawPptImage(
        pptx_path=Path("x.pptx"),
        slide_no=1,
        shape_idx=0,
        source_media_path="m",
        suffix=".png",
        data=b"",
        sha256="s",
        width=10,
        height=10,
        mode="RGB",
        bbox={},
        slide_text="",
        slide_title_guess="",
    )


def test_normalize_annotation_keeps_four_vlm_fields_with_is_backdrop():
    annotation = _normalize_annotation(
        {
            "query": "三段大文本框加卡通人物的内容页",
            "context_summary": "内容页",
            "teaching_intent": "讲解",
            "is_backdrop": False,
            "vlm_caption": "x",
            "visual_reuse_group": "C02_generic_subject_object",
        },
        _raw(),
        {},
        {"slide_text": ""},
    )
    assert set(annotation.keys()) == {"query", "context_summary", "teaching_intent", "is_backdrop"}
    assert annotation["is_backdrop"] is False
    assert "visual_reuse_group" not in annotation


def test_normalize_annotation_defaults_is_backdrop_false_when_absent():
    annotation = _normalize_annotation(
        {"query": "渐变纹理底图", "context_summary": "", "teaching_intent": ""},
        _raw(),
        {},
        {"slide_text": ""},
    )
    assert annotation["is_backdrop"] is False


def _fullbleed_raw():
    raw = _raw()
    raw.bbox = {"x": 0.0, "y": 0.0, "width": 1280.0, "height": 720.0, "area_ratio": 1.0}
    return raw


def test_resolve_asset_kind_gates_background_on_is_backdrop():
    full = _fullbleed_raw()
    assert _resolve_ppt_asset_kind(full, True) == "background"
    assert _resolve_ppt_asset_kind(full, False) == "page_image"
    small = _raw()
    assert _resolve_ppt_asset_kind(small, True) == "page_image"


def test_build_asset_from_annotation_persists_is_backdrop_and_kind():
    asset = _build_asset_from_annotation(
        asset_id="a",
        image_rel="pptx_images/a.png",
        original_image_rel="pptx_images_original/a.png",
        image_fields={
            "actual_width": 1280,
            "actual_height": 720,
            "padded_width": 1280,
            "padded_height": 720,
            "aspect_ratio": "16:9",
        },
        item=_fullbleed_raw(),
        meta={"file_name": "lesson.pptx"},
        context={"slide_text": "", "slide_title_guess": ""},
        annotation={
            "query": "三段大文本框加卡通人物的内容页",
            "context_summary": "内容页",
            "teaching_intent": "讲解",
            "is_backdrop": False,
        },
    )
    assert asset["asset_kind"] == "page_image"
    assert asset["is_backdrop"] is False


def test_vlm_prompt_declares_is_backdrop_field():
    assert "is_backdrop" in PPT_VLM_SYSTEM_PROMPT


def test_vlm_query_contract_has_four_invariants():
    p = PPT_VLM_SYSTEM_PROMPT
    assert "图像本身" in p and "匿名" in p
    assert "自然类别" in p and "一种器物" in p
    assert "空白脚手架" in p and "刻度" in p
    assert "天气" in p and "区分" in p


class _FakeLLM:
    """Routes by which system prompt is sent (classify / caption / general)."""

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        arr = json.loads(user[user.index("[") :])
        out = []
        for item in arr:
            query = item["query"]
            if "通用场景摘要器" in system or "secondary_reuse_caption" in system:
                out.append({"query": query, "secondary_reuse_caption": query.replace("西湖", "").strip() or "通用场景"})
                continue
            if "dual landmark" in query and "strict_reuse_group" in system:
                out.append(
                    {
                        "query": query,
                        "strict_reuse_group": "C01_irreplaceable_entity_event_action",
                        "strict_reuse_secondary_group": "C03_scene_decor_container",
                    }
                )
                continue
            if "strict_reuse_group" in system or "分类器" in system:
                group = "C00_strict_text_problem_skip" if "课文" in query else "C02_generic_subject_object"
                out.append({"query": query, "strict_reuse_group": group})
            elif "general" in system:
                out.append({"query": query, "general": "青铜" not in query})
            else:
                out.append({"query": query, "caption": query[:6]})
        return json.dumps(out, ensure_ascii=False)

    def chat_json(self, *args, **kwargs):
        raise NotImplementedError


def test_enrich_skip_shortcircuits_caption_and_general():
    db = {
        "assets": [
            {"asset_id": "a1", "asset_kind": "page_image", "query": "带拼音的课文段落", "subject": "语文"},
            {"asset_id": "a2", "asset_kind": "page_image", "query": "抱着橡果的卡通松鼠", "subject": "数学"},
        ]
    }
    warnings = []
    _enrich_ppt_assets_with_llm(db, _FakeLLM(), batch_size=10, warnings=warnings)
    skip_asset, reusable_asset = db["assets"]
    assert skip_asset["strict_reuse_group"] == "C00_strict_text_problem_skip"
    assert "caption" not in skip_asset and "general" not in skip_asset
    assert reusable_asset["strict_reuse_group"] == "C02_generic_subject_object"
    assert reusable_asset["caption"] and isinstance(reusable_asset["general"], bool)
    assert reusable_asset["subject"] == "数学"


def test_enrich_ppt_assets_preserves_valid_c01_c03_secondary_group():
    db = {
        "assets": [
            {
                "asset_id": "landmark",
                "asset_kind": "page_image",
                "query": "dual landmark river scene",
                "subject": "语文",
            }
        ]
    }
    warnings = []

    _enrich_ppt_assets_with_llm(db, _FakeLLM(), batch_size=10, warnings=warnings)

    asset = db["assets"][0]
    assert asset["strict_reuse_group"] == "C01_irreplaceable_entity_event_action"
    assert asset["strict_reuse_secondary_group"] == "C03_scene_decor_container"


def test_enrich_single_sets_secondary_caption_for_c01_dual_landmark():
    from scripts.build_ppt_materials_library import _enrich_single_ppt_asset_with_llm

    asset = {
        "asset_id": "lm",
        "asset_kind": "page_image",
        "query": "西湖 dual landmark river scene",
        "subject": "语文",
    }
    out, warnings = _enrich_single_ppt_asset_with_llm(asset, _FakeLLM(), batch_size=10)
    assert warnings == []
    assert out["strict_reuse_group"] == "C01_irreplaceable_entity_event_action"
    assert out["strict_reuse_secondary_group"] == "C03_scene_decor_container"
    assert out.get("secondary_reuse_caption")
    assert "西湖" not in out["secondary_reuse_caption"]


def test_keyword_prompt_embeds_caption_rule_and_secondary_and_four_classes():
    messages = _build_keyword_messages(
        [{"asset_id": "a", "asset_kind": "page_image", "query": "卢沟桥的水墨江景"}]
    )
    system = messages[0]["content"]
    # caption 字段遵循 CAPTION_RULE。
    assert CAPTION_RULE in system
    assert "可复用的主体内容" in system
    assert "形态噪声词" in system
    # 输出 C03 副标签字段 + dual 指引。
    assert "strict_reuse_secondary_group" in system
    # 修正陈旧的“6 个素材类别”。
    assert "6 个素材类别" not in system
    assert "4 个素材类别" in system
