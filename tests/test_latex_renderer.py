"""Tests for LaTeX formula rendering."""

from edupptx.postprocess.latex_renderer import render_latex_formulas


class TestLatexRenderer:
    def test_renders_math_formula(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<text x="100" y="200" font-size="24" data-latex="x^2 + 1" fill="#1E293B">x²+1</text>'
            "</svg>"
        )
        result, count = render_latex_formulas(svg)
        assert count == 1
        assert "data-latex" not in result
        assert "<image" in result
        assert "data:image/png;base64," in result

    def test_renders_fraction(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            r'<text x="100" y="200" font-size="24" data-latex="\frac{a}{b}" fill="#1E293B">a/b</text>'
            "</svg>"
        )
        result, count = render_latex_formulas(svg)
        assert count == 1
        assert "<image" in result

    def test_no_latex_unchanged(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<text x="100" y="200" font-size="24">plain text</text>'
            "</svg>"
        )
        result, count = render_latex_formulas(svg)
        assert count == 0
        assert result == svg

    def test_invalid_latex_preserves_text(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            '<text x="100" y="200" font-size="24" data-latex="\\invalid{command}{}{}" fill="#1E293B">fallback</text>'
            "</svg>"
        )
        result, count = render_latex_formulas(svg)
        # Should keep original text when rendering fails
        assert count == 0
        assert "fallback" in result

    def test_chemistry_formula(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            r'<text x="100" y="200" font-size="24" data-latex="\mathrm{H_2O}" fill="#1E293B">H₂O</text>'
            "</svg>"
        )
        result, count = render_latex_formulas(svg)
        assert count == 1
        assert "<image" in result
