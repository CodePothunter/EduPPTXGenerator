"""OOXML renderer — python-pptx + XML patches for SVG icons, shadows, alpha."""

from __future__ import annotations

import io
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import BinaryIO

from loguru import logger
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Emu, Pt

from edupptx.design_system import DesignTokens
from edupptx.icons import get_icon_png, get_icon_svg
from edupptx.layout_engine import (
    SLIDE_H,
    SLIDE_W,
    SlotLayout,
    SlotPosition,
    get_layout,
)
from edupptx.models import PresentationPlan, SlideCard, SlideContent

# XML namespaces
_NSMAP = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
}


def _hex_to_rgb(hex_color: str) -> RGBColor:
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _set_font(run, design: DesignTokens, size_pt: int, bold: bool = False, color: str | None = None):
    """Configure font properties on a text run."""
    font = run.font
    font.size = Pt(size_pt)
    font.bold = bold
    font.color.rgb = _hex_to_rgb(color or design.text_primary)
    font.name = design.font_fallback  # Latin typeface

    # Set East Asian / Complex Script / Symbol typefaces via XML
    rPr = run._r.get_or_add_rPr()
    for tag, attr in [("a:ea", design.font_primary), ("a:cs", design.font_primary), ("a:sym", design.font_primary)]:
        ns = _NSMAP["a"]
        el = rPr.find(f"{{{ns}}}{tag.split(':')[1]}")
        if el is None:
            el = etree.SubElement(rPr, f"{{{ns}}}{tag.split(':')[1]}")
        el.set("typeface", attr)


class PresentationRenderer:
    """Renders a PresentationPlan into a .pptx file."""

    def __init__(self, design: DesignTokens):
        self.prs = Presentation()
        self.prs.slide_width = Emu(SLIDE_W)
        self.prs.slide_height = Emu(SLIDE_H)
        self.design = design
        self._temp_dir = tempfile.mkdtemp(prefix="edupptx_")

    def render(self, plan: PresentationPlan, backgrounds: list[Path]) -> Path:
        """Render all slides and save to a .pptx file."""
        for i, slide_content in enumerate(plan.slides):
            bg = backgrounds[i % len(backgrounds)]
            logger.info("Rendering slide %d/%d: %s", i + 1, len(plan.slides), slide_content.type)
            self._render_slide(slide_content, bg)

        output = Path(f"{plan.topic}.pptx")
        self.prs.save(str(output))
        logger.info("Saved presentation: %s", output)
        return output

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        self.prs.save(str(out))
        return out

    def render_slide(
        self, content: SlideContent,
        bg_path: Path | None = None,
        material_path: Path | None = None,
    ) -> None:
        """Render a single slide into the presentation."""
        if bg_path is None:
            from PIL import Image
            import tempfile
            img = Image.new("RGB", (1920, 1080), tuple(int(self.design.bg_overlay.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)))
            bg_path = Path(tempfile.mktemp(suffix=".jpg"))
            img.save(bg_path, "JPEG")
        self._render_slide(content, bg_path, material_path)

    def _render_slide(self, content: SlideContent, bg_path: Path, material_path: Path | None = None):
        """Render a single slide with all its components."""
        layout_idx = min(6, len(self.prs.slide_layouts) - 1)
        layout = self.prs.slide_layouts[layout_idx]
        slide = self.prs.slides.add_slide(layout)

        # Determine material position from content_materials
        mat_position = None
        if material_path and material_path.exists() and content.content_materials:
            mat_position = content.content_materials[0].position

        slots = get_layout(content.type, len(content.cards), material_position=mat_position)

        # 1. Background image (full canvas)
        self._add_background(slide, bg_path, slots.background)

        # 2. Title bar background (semi-transparent strip behind title area only)
        title_bar_h = slots.title.height
        if content.subtitle and slots.subtitle:
            title_bar_h = (slots.subtitle.y + slots.subtitle.height) - slots.title.y
        title_bar = SlotPosition(
            x=0, y=0,
            width=SLIDE_W,
            height=slots.title.y + title_bar_h + 254000,  # +20pt padding
        )
        self._add_overlay(slide, title_bar)

        # 3. Title
        self._add_textbox(
            slide, content.title, slots.title,
            size_pt=self.design.size_title, bold=True,
        )

        # 4. Subtitle
        if content.subtitle and slots.subtitle:
            self._add_textbox(
                slide, content.subtitle, slots.subtitle,
                size_pt=self.design.size_subtitle,
                color=self.design.text_secondary,
            )

        # 5. Content material (diagram/illustration) at material_slot
        if material_path and material_path.exists() and slots.material_slot:
            ms = slots.material_slot
            # Fit image into slot while preserving aspect ratio
            from PIL import Image as PILImage
            with PILImage.open(material_path) as img:
                img_w, img_h = img.size
            aspect = img_w / img_h
            slot_aspect = ms.width / ms.height
            if aspect > slot_aspect:
                # Image wider than slot — fit by width
                fit_w = ms.width
                fit_h = int(ms.width / aspect)
            else:
                # Image taller than slot — fit by height
                fit_h = ms.height
                fit_w = int(ms.height * aspect)
            # Center in slot
            x = ms.x + (ms.width - fit_w) // 2
            y = ms.y + (ms.height - fit_h) // 2
            slide.shapes.add_picture(
                str(material_path),
                Emu(x), Emu(y), Emu(fit_w), Emu(fit_h),
            )

        # 6. Cards with icons
        for j, card in enumerate(content.cards):
            if j >= len(slots.cards):
                break
            self._add_card(
                slide, card,
                slots.cards[j],
                slots.card_icons[j] if j < len(slots.card_icons) else None,
                slots.card_titles[j] if j < len(slots.card_titles) else None,
                slots.card_bodies[j] if j < len(slots.card_bodies) else None,
            )

        # 7. Formula
        if content.formula and slots.formula:
            self._add_formula_bar(slide, content.formula, slots.formula)

        # 8. Footer
        if content.footer and slots.footer:
            self._add_footer(slide, content.footer, slots.footer)

        # 9. Speaker notes
        if content.notes:
            notes_slide = slide.notes_slide
            tf = notes_slide.notes_text_frame
            tf.text = content.notes

    def _add_background(self, slide, bg_path: Path, slot: SlotPosition):
        """Add a full-canvas background image."""
        slide.shapes.add_picture(
            str(bg_path),
            Emu(slot.x), Emu(slot.y), Emu(slot.width), Emu(slot.height),
        )

    def _add_overlay(self, slide, slot: SlotPosition):
        """Add a semi-transparent color overlay."""
        shape = slide.shapes.add_shape(
            1,  # MSO_SHAPE.RECTANGLE
            Emu(slot.x), Emu(slot.y), Emu(slot.width), Emu(slot.height),
        )
        shape.line.fill.background()  # No border

        # Set fill with alpha via XML
        fill = shape.fill
        fill.solid()
        fill.fore_color.rgb = _hex_to_rgb(self.design.bg_overlay)

        # Patch alpha
        sp_xml = shape._element
        ns_a = _NSMAP["a"]
        solid_fill = sp_xml.find(f".//{{{ns_a}}}solidFill")
        if solid_fill is not None:
            color_el = solid_fill[0] if len(solid_fill) else None
            if color_el is not None:
                alpha_val = int(self.design.bg_overlay_alpha * 100000)
                alpha_el = etree.SubElement(color_el, f"{{{ns_a}}}alpha")
                alpha_el.set("val", str(alpha_val))

    def _add_textbox(
        self, slide, text: str, slot: SlotPosition,
        size_pt: int = 16, bold: bool = False, color: str | None = None,
        align: PP_ALIGN = PP_ALIGN.LEFT,
    ):
        """Add a text box at the given slot position."""
        txbox = slide.shapes.add_textbox(
            Emu(slot.x), Emu(slot.y), Emu(slot.width), Emu(slot.height),
        )
        tf = txbox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        _set_font(run, self.design, size_pt, bold=bold, color=color)

    def _add_card(
        self, slide, card: SlideCard,
        card_slot: SlotPosition,
        icon_slot: SlotPosition | None,
        title_slot: SlotPosition | None,
        body_slot: SlotPosition | None,
    ):
        """Add a card component: container + icon + title + body."""
        # Card container (rounded rectangle with shadow)
        shape = slide.shapes.add_shape(
            5,  # MSO_SHAPE.ROUNDED_RECTANGLE
            Emu(card_slot.x), Emu(card_slot.y),
            Emu(card_slot.width), Emu(card_slot.height),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = _hex_to_rgb(self.design.card_bg)
        shape.line.fill.background()

        # Patch: add shadow
        self._patch_card_shadow(shape)

        # Patch: adjust corner radius
        self._patch_corner_radius(shape, 5000)  # ~5% roundness

        # Icon (as PNG image — SVG embedding is complex, PNG is reliable)
        if icon_slot:
            self._add_icon(slide, card.icon, icon_slot)

        # Card title
        if title_slot:
            self._add_textbox(
                slide, card.title, title_slot,
                size_pt=self.design.size_card_title, bold=True,
                align=PP_ALIGN.CENTER,
            )

        # Card body
        if body_slot:
            self._add_textbox(
                slide, card.body, body_slot,
                size_pt=self.design.size_card_body,
                color=self.design.text_secondary,
            )

    def _add_icon(self, slide, icon_name: str, slot: SlotPosition):
        """Add an icon as a PNG image (with SVG extension if possible)."""
        # Generate PNG
        png_bytes = get_icon_png(icon_name, self.design.icon_color, size=48)
        png_stream = io.BytesIO(png_bytes)

        pic = slide.shapes.add_picture(
            png_stream,
            Emu(slot.x), Emu(slot.y), Emu(slot.width), Emu(slot.height),
        )

        # Try to add SVG extension for modern PowerPoint
        try:
            self._patch_svg_blip(pic, icon_name)
        except Exception:
            pass  # PNG fallback is fine

    def _add_formula_bar(self, slide, formula: str, slot: SlotPosition):
        """Add a formula highlight bar."""
        # Background shape
        shape = slide.shapes.add_shape(
            5,  # ROUNDED_RECTANGLE
            Emu(slot.x), Emu(slot.y), Emu(slot.width), Emu(slot.height),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = _hex_to_rgb(self.design.accent_light)
        shape.line.fill.background()
        self._patch_corner_radius(shape, 3000)

        # Formula text
        self._add_textbox(
            slide, formula, slot,
            size_pt=self.design.size_formula, bold=True,
            color=self.design.accent,
            align=PP_ALIGN.CENTER,
        )

    def _add_footer(self, slide, text: str, slot: SlotPosition):
        """Add a footer text line."""
        self._add_textbox(
            slide, text, slot,
            size_pt=self.design.size_footer,
            color=self.design.text_secondary,
            align=PP_ALIGN.CENTER,
        )

    # ── XML Patches ──────────────────────────────────────────────

    def _patch_card_shadow(self, shape):
        """Add an outer shadow to a card shape via XML."""
        ns_a = _NSMAP["a"]
        sp_pr = shape._element.find(f".//{{{ns_a}}}spPr")
        if sp_pr is None:
            return

        shadow_color = self.design.shadow_color.lstrip("#")

        effect_lst = etree.SubElement(sp_pr, f"{{{ns_a}}}effectLst")
        outer_shdw = etree.SubElement(effect_lst, f"{{{ns_a}}}outerShdw")
        outer_shdw.set("blurRad", "190500")   # ~15pt blur
        outer_shdw.set("dist", "101600")       # ~8pt distance
        outer_shdw.set("dir", "2700000")       # Bottom-right
        outer_shdw.set("algn", "tl")
        outer_shdw.set("rotWithShape", "0")

        srgb_clr = etree.SubElement(outer_shdw, f"{{{ns_a}}}srgbClr")
        srgb_clr.set("val", shadow_color)
        alpha = etree.SubElement(srgb_clr, f"{{{ns_a}}}alpha")
        alpha.set("val", "25000")  # 25% opacity

    def _patch_corner_radius(self, shape, adj_val: int = 5000):
        """Set the corner radius of a rounded rectangle."""
        ns_a = _NSMAP["a"]
        prst_geom = shape._element.find(f".//{{{ns_a}}}prstGeom")
        if prst_geom is not None:
            av_lst = prst_geom.find(f"{{{ns_a}}}avLst")
            if av_lst is None:
                av_lst = etree.SubElement(prst_geom, f"{{{ns_a}}}avLst")
            # Clear existing
            for child in list(av_lst):
                av_lst.remove(child)
            gd = etree.SubElement(av_lst, f"{{{ns_a}}}gd")
            gd.set("name", "adj")
            gd.set("fmla", f"val {adj_val}")

    def _patch_svg_blip(self, pic_shape, icon_name: str):
        """Add SVG extension to a picture's blip (for modern PowerPoint)."""
        svg_str = get_icon_svg(icon_name, self.design.icon_color)
        svg_bytes = svg_str.encode("utf-8")

        # Save SVG to temp file and add as relationship
        svg_path = os.path.join(self._temp_dir, f"{icon_name}.svg")
        with open(svg_path, "wb") as f:
            f.write(svg_bytes)

        # Add SVG as a relationship on the slide part
        slide_part = pic_shape.part
        svg_rel = slide_part.relate_to_file(
            svg_path,
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
        )

        # Patch the blip XML to include SVG extension
        ns_a = _NSMAP["a"]
        blip = pic_shape._element.find(f".//{{{ns_a}}}blip")
        if blip is None:
            return

        ext_lst = blip.find(f"{{{ns_a}}}extLst")
        if ext_lst is None:
            ext_lst = etree.SubElement(blip, f"{{{ns_a}}}extLst")

        # Add SVG extension
        ext = etree.SubElement(ext_lst, f"{{{ns_a}}}ext")
        ext.set("uri", "{96DAC541-7B7A-43D3-8B79-37D633B846F1}")

        ns_asvg = _NSMAP["asvg"]
        svg_blip = etree.SubElement(ext, f"{{{ns_asvg}}}svgBlip")
        svg_blip.set(f"{{{_NSMAP['r']}}}embed", svg_rel)
