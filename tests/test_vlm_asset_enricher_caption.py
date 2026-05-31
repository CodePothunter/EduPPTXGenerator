from edupptx.materials.caption_rules import CAPTION_RULE
from edupptx.materials.vlm_asset_enricher import (
    VLM_REDESCRIBE_SYSTEM_PROMPT,
    _METADATA_FIELDS,
    _apply_redescription,
)


def test_redescription_writes_caption_not_content_prompt():
    asset = {"asset_id": "a1", "content_prompt": "old detailed prompt"}
    payload = {
        "caption": "girl and apple",
        "strict_reuse_group": "C04_generic_subject_object",
        "strict_reuse_reason": "generic subject: girl and apple",
    }

    _apply_redescription(asset, payload)

    assert asset["caption"] == "girl and apple"
    assert asset["strict_reuse_group"] == "C04_generic_subject_object"
    assert asset["strict_reuse_reason"] == "generic subject: girl and apple"
    assert "content_prompt" not in asset


def test_redescription_prompt_uses_shared_caption_rule():
    assert CAPTION_RULE in VLM_REDESCRIBE_SYSTEM_PROMPT
    assert '"caption"' in VLM_REDESCRIBE_SYSTEM_PROMPT
    assert '"content_prompt"' not in VLM_REDESCRIBE_SYSTEM_PROMPT


def test_vlm_metadata_fields_use_caption():
    assert "caption" in _METADATA_FIELDS
    assert "content_prompt" not in _METADATA_FIELDS
