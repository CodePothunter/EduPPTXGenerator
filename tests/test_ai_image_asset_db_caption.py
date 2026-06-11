from edupptx.materials.ai_image_asset_db import _asset_caption, _build_match_text


def test_caption_preferred_over_legacy_text_fields():
    asset = {
        "caption": "visible apple card",
        "query": "verbose query should not be used",
        "content_prompt": "legacy content prompt should not be used",
        "prompt": "legacy prompt should not be used",
    }
    assert _asset_caption(asset) == "visible apple card"


def test_legacy_text_fields_do_not_fallback_when_no_caption():
    asset = {
        "asset_kind": "page_image",
        "query": "legacy query",
        "content_prompt": "legacy content prompt",
        "prompt": "legacy prompt",
    }
    assert _asset_caption(asset) == ""
    assert _build_match_text(asset) == ""


def test_match_text_uses_caption_for_page_image():
    asset = {
        "asset_kind": "page_image",
        "caption": "children playing",
        "context_summary": "classroom activity page",
    }
    text = _build_match_text(asset)
    assert "children playing" in text
