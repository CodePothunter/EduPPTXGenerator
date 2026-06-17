"""The shared background must reach every slide — including one that falls back
to image embedding when its native conversion throws. Regression for the embed
fallback silently dropping the backdrop (so the failed slide alone looked
different from its peers)."""

import zipfile

import edupptx.output.pptx_assembler as asm
from edupptx.output.pptx_assembler import assemble_pptx


def _png(path):
    from PIL import Image
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path)


def _slide_files(pptx_path):
    with zipfile.ZipFile(pptx_path) as zf:
        return (
            zf.read("ppt/slides/slide1.xml").decode("utf-8"),
            zf.read("ppt/slides/_rels/slide1.xml.rels").decode("utf-8"),
        )


def _write_svg(path):
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
        '<rect x="100" y="100" width="200" height="100" fill="#3366cc"/></svg>',
        encoding="utf-8",
    )


def test_native_fallback_still_layers_shared_background(tmp_path, monkeypatch):
    # Force the slide's native conversion to fail so the embed fallback runs,
    # and stub the PNG render so the test needs no cairosvg. convert_svg_to_slide_shapes
    # is imported inside _assemble_native_shapes, so patch it at its source module.
    def _boom(*_a, **_k):
        raise ValueError("bad slide")

    monkeypatch.setattr("edupptx.output.svg_to_shapes.convert_svg_to_slide_shapes", _boom)
    monkeypatch.setattr(asm, "_svg_to_png", lambda *a, **k: False)

    svg = tmp_path / "slide_01.svg"
    _write_svg(svg)
    bg = tmp_path / "background.png"
    _png(bg)
    out = tmp_path / "out.pptx"

    assemble_pptx([svg], out, embed=False, bg_path=bg)

    slide_xml, rels_xml = _slide_files(out)
    assert 'name="Background"' in slide_xml          # bg picture layered in
    assert 'r:embed="rIdBg1"' in slide_xml           # via its own rel id
    assert 'Id="rIdBg1"' in rels_xml                 # relationship declared
    assert "../media/background.png" in rels_xml     # pointing at the shared bg
    # background must sit BEHIND the embedded slide image
    assert slide_xml.index('name="Background"') < slide_xml.index('name="SVG 1"')


def test_native_happy_path_keeps_background(tmp_path):
    # Guards that refactoring the bg pic into a shared helper did not change the
    # normal (non-fallback) native output.
    svg = tmp_path / "slide_01.svg"
    _write_svg(svg)
    bg = tmp_path / "background.png"
    _png(bg)
    out = tmp_path / "out.pptx"

    assemble_pptx([svg], out, embed=False, bg_path=bg)

    slide_xml, rels_xml = _slide_files(out)
    assert 'name="Background"' in slide_xml
    assert "../media/background.png" in rels_xml
