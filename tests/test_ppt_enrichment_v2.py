import json
from pathlib import Path

from edupptx.materials.ai_image_asset_db import _build_keyword_messages
from edupptx.materials.caption_rules import CAPTION_RULE
from scripts.build_ppt_materials_library import (
    PPT_VLM_SYSTEM_PROMPT,
    RawPptImage,
    _enrich_ppt_assets_with_llm,
    _normalize_annotation,
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


def test_normalize_annotation_only_keeps_three_vlm_fields():
    annotation = _normalize_annotation(
        {
            "query": "抱着橡果的卡通松鼠",
            "context_summary": "松鼠插画用于封面",
            "teaching_intent": "导入",
            "vlm_caption": "x",
            "vlm_general": False,
            "visual_reuse_group": "C02_generic_subject_object",
        },
        _raw(),
        {},
        {"slide_text": ""},
    )
    assert set(annotation.keys()) == {"query", "context_summary", "teaching_intent"}
    assert "vlm_general" not in annotation
    assert "general" not in annotation
    assert "visual_reuse_group" not in annotation


class _FakeLLM:
    """Routes by which system prompt is sent (classify / caption / general)."""

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        arr = json.loads(user[user.index("[") :])
        out = []
        for item in arr:
            query = item["query"]
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
