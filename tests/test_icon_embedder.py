"""Tests for icon placeholder embedding."""

from edupptx.postprocess.icon_embedder import embed_icon_placeholders


class TestIconEmbedder:
    def test_replaces_data_icon_placeholder(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<use data-icon="book-open" x="100" y="200" width="48" height="48" fill="#2563EB"/>'
            '</svg>'
        )
        result, count = embed_icon_placeholders(svg)
        assert count == 1
        assert 'data-icon' not in result
        assert '<g' in result
        assert 'translate(100' in result

    def test_no_placeholder_returns_unchanged(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<rect x="50" y="50" width="100" height="100"/>'
            '</svg>'
        )
        result, count = embed_icon_placeholders(svg)
        assert count == 0
        assert result == svg

    def test_unknown_icon_uses_fallback(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<use data-icon="nonexistent-icon-xyz" x="50" y="50" width="48" height="48"/>'
            '</svg>'
        )
        result, count = embed_icon_placeholders(svg)
        assert count == 1  # fallback circle icon is used
        assert 'data-icon' not in result

    def test_preserves_other_use_elements(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<defs><linearGradient id="g1"><stop offset="0%" stop-color="#fff"/></linearGradient></defs>'
            '<use href="#g1" x="0" y="0"/>'
            '<use data-icon="star" x="100" y="100" width="24" height="24"/>'
            '</svg>'
        )
        result, count = embed_icon_placeholders(svg)
        assert count == 1  # Only the data-icon use is replaced
