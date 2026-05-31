from edupptx.materials.ai_image_asset_db import _asset_caption, _build_match_text


def test_caption_preferred_over_content_prompt():
    asset = {"caption": "小女孩和苹果", "content_prompt": "扎双丸子头的卡通小女孩和红苹果"}
    assert _asset_caption(asset) == "小女孩和苹果"


def test_content_prompt_fallback_when_no_caption():
    asset = {"content_prompt": "雾中的城市街景"}
    assert _asset_caption(asset) == "雾中的城市街景"


def test_match_text_uses_caption_for_page_image():
    asset = {"asset_kind": "page_image", "caption": "小朋友做游戏", "context_summary": "课堂活动页"}
    text = _build_match_text(asset)
    assert "小朋友做游戏" in text
