"""Standalone XML patch utilities for python-pptx shapes.

Pure functions that apply OOXML modifications (shadows, alpha, corners,
font typefaces, autofit) to shape elements. No style lookups — all values
are passed in as concrete parameters.
"""

from __future__ import annotations

from lxml import etree
from pptx.dml.color import RGBColor
from pptx.util import Pt

from edupptx.models import ResolvedShadow

# OOXML namespaces
NSMAP = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
}

_NS_A = NSMAP["a"]


def hex_to_rgb(hex_color: str) -> RGBColor:
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def patch_shadow(shape, shadow: ResolvedShadow) -> None:
    """Add an outer shadow to a shape via XML."""
    sp_pr = shape._element.find(f".//{{{_NS_A}}}spPr")
    if sp_pr is None:
        return

    color_hex = shadow.color.lstrip("#")
    effect_lst = etree.SubElement(sp_pr, f"{{{_NS_A}}}effectLst")
    outer_shdw = etree.SubElement(effect_lst, f"{{{_NS_A}}}outerShdw")
    outer_shdw.set("blurRad", str(shadow.blur_emu))
    outer_shdw.set("dist", str(shadow.dist_emu))
    outer_shdw.set("dir", "5400000")  # straight down
    outer_shdw.set("algn", "t")
    outer_shdw.set("rotWithShape", "0")

    srgb = etree.SubElement(outer_shdw, f"{{{_NS_A}}}srgbClr")
    srgb.set("val", color_hex)
    alpha = etree.SubElement(srgb, f"{{{_NS_A}}}alpha")
    alpha.set("val", str(shadow.alpha_pct * 1000))  # OOXML: 14% = 14000


def patch_corner_radius(shape, adj_val: int = 5000) -> None:
    """Set corner radius on a rounded rectangle (OOXML adjustable coord 0-100000)."""
    prst_geom = shape._element.find(f".//{{{_NS_A}}}prstGeom")
    if prst_geom is None:
        return
    av_lst = prst_geom.find(f"{{{_NS_A}}}avLst")
    if av_lst is None:
        av_lst = etree.SubElement(prst_geom, f"{{{_NS_A}}}avLst")
    for child in list(av_lst):
        av_lst.remove(child)
    gd = etree.SubElement(av_lst, f"{{{_NS_A}}}gd")
    gd.set("name", "adj")
    gd.set("fmla", f"val {adj_val}")


def patch_alpha(shape, alpha_pct: int) -> None:
    """Set fill transparency on a shape (0=transparent, 100=opaque)."""
    solid_fill = shape._element.find(f".//{{{_NS_A}}}solidFill")
    if solid_fill is not None and len(solid_fill):
        alpha_el = etree.SubElement(solid_fill[0], f"{{{_NS_A}}}alpha")
        alpha_el.set("val", str(alpha_pct * 1000))


def set_font_cjk(run, family: str, fallback: str, size_pt: int,
                 bold: bool = False, color: str = "#000000") -> None:
    """Configure font with East Asian typeface support."""
    font = run.font
    font.size = Pt(size_pt)
    font.bold = bold
    font.color.rgb = hex_to_rgb(color)
    font.name = fallback  # Latin typeface

    rPr = run._r.get_or_add_rPr()
    for tag in ["ea", "cs", "sym"]:
        el = rPr.find(f"{{{_NS_A}}}{tag}")
        if el is None:
            el = etree.SubElement(rPr, f"{{{_NS_A}}}{tag}")
        el.set("typeface", family)


def patch_autofit(textbox) -> None:
    """Enable normAutofit on a text box (auto-shrink text to fit)."""
    body_pr = textbox._element.find(f".//{{{_NS_A}}}bodyPr")
    if body_pr is None:
        return
    for tag in ["noAutofit", "spAutoFit", "normAutofit"]:
        for el in body_pr.findall(f"{{{_NS_A}}}{tag}"):
            body_pr.remove(el)
    etree.SubElement(body_pr, f"{{{_NS_A}}}normAutofit")


def patch_v_anchor(textbox, anchor: str = "t") -> None:
    """Set vertical text anchor: 't' (top), 'ctr' (center), 'b' (bottom)."""
    body_pr = textbox._element.find(f".//{{{_NS_A}}}bodyPr")
    if body_pr is not None:
        body_pr.set("anchor", anchor)
