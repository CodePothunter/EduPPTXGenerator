"""SVG to DrawingML Native Shapes Converter.

Converts LLM-generated SVG slides into native PowerPoint DrawingML shapes,
so the PPTX is directly editable without manual "Convert to Shape".

Adapted from ppt-master (MIT license) with additions for:
- <tspan> multi-line text → multi-paragraph DrawingML
- Rounded rect (rx/ry) → roundRect preset geometry
- <use href="#id"> → inline expansion from <defs>
- <feDropShadow> shorthand → outerShdw effect
- CJK-optimized font mapping (Noto Sans SC first)
"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# 1 SVG pixel = 9525 EMU (at 96 DPI)
EMU_PER_PX = 9525

# DrawingML font size: 1/100 of a point.  1px ≈ 0.75pt → 75 hundredths-pt
FONT_PX_TO_HUNDREDTHS_PT = 75

# DrawingML angle unit: 60000ths of a degree
ANGLE_UNIT = 60000

EA_FONTS = {
    "PingFang SC", "Microsoft YaHei", "Microsoft JhengHei",
    "SimSun", "SimHei", "FangSong", "KaiTi",
    "Noto Sans SC", "Noto Sans TC", "Noto Serif SC",
    "Source Han Sans SC", "WenQuanYi Micro Hei",
    "Hiragino Sans", "Hiragino Sans GB",
    "微软雅黑",
}

DASH_PRESETS = {
    "4,4": "dash", "4 4": "dash", "6,3": "dash", "6 3": "dash",
    "2,2": "sysDot", "2 2": "sysDot",
    "8,4": "lgDash", "8 4": "lgDash",
}


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class ConvertContext:
    """Shared state threaded through the conversion pipeline."""
    defs: dict[str, ET.Element] = field(default_factory=dict)
    id_counter: int = 2          # 1 reserved for spTree root
    slide_num: int = 1
    translate_x: float = 0.0
    translate_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    filter_id: str | None = None
    media_files: dict[str, bytes] = field(default_factory=dict)
    rel_entries: list[dict[str, str]] = field(default_factory=list)
    rel_id_counter: int = 2      # rId1 reserved for slideLayout

    def next_id(self) -> int:
        cid = self.id_counter
        self.id_counter += 1
        return cid

    def next_rel_id(self) -> str:
        rid = f"rId{self.rel_id_counter}"
        self.rel_id_counter += 1
        return rid

    def child(self, dx: float = 0, dy: float = 0,
              sx: float = 1.0, sy: float = 1.0,
              filter_id: str | None = None) -> ConvertContext:
        return ConvertContext(
            defs=self.defs,
            id_counter=self.id_counter,
            slide_num=self.slide_num,
            translate_x=self.translate_x + dx,
            translate_y=self.translate_y + dy,
            scale_x=self.scale_x * sx,
            scale_y=self.scale_y * sy,
            filter_id=filter_id or self.filter_id,
            media_files=self.media_files,
            rel_entries=self.rel_entries,
            rel_id_counter=self.rel_id_counter,
        )

    def sync_from_child(self, child_ctx: ConvertContext):
        self.id_counter = child_ctx.id_counter
        self.rel_id_counter = child_ctx.rel_id_counter


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def px_to_emu(px: float) -> int:
    return round(px * EMU_PER_PX)

def _f(val: str | None, default: float = 0.0, font_size: float = 16.0) -> float:
    """Parse SVG numeric value, supporting px and em units."""
    if val is None:
        return default
    val = val.strip()
    try:
        if val.endswith("em"):
            return float(val[:-2]) * font_size
        return float(val.replace("px", "").strip())
    except (ValueError, TypeError):
        return default

def ctx_x(val: float, ctx: ConvertContext) -> float:
    return val * ctx.scale_x + ctx.translate_x

def ctx_y(val: float, ctx: ConvertContext) -> float:
    return val * ctx.scale_y + ctx.translate_y

def ctx_w(val: float, ctx: ConvertContext) -> float:
    return val * ctx.scale_x

def ctx_h(val: float, ctx: ConvertContext) -> float:
    return val * ctx.scale_y


# ---------------------------------------------------------------------------
# Color / style parsing
# ---------------------------------------------------------------------------

def parse_hex_color(color_str: str) -> str | None:
    if not color_str:
        return None
    color_str = color_str.strip()
    if color_str.startswith("#"):
        color_str = color_str[1:]
    if len(color_str) == 3:
        color_str = "".join(c * 2 for c in color_str)
    if len(color_str) == 6 and all(c in "0123456789abcdefABCDEF" for c in color_str):
        return color_str.upper()
    return None


def resolve_url_id(url_str: str) -> str | None:
    if not url_str:
        return None
    m = re.match(r"url\(#([^)]+)\)", url_str.strip())
    return m.group(1) if m else None


def get_effective_filter_id(elem: ET.Element, ctx: ConvertContext) -> str | None:
    filt = elem.get("filter")
    if filt:
        return resolve_url_id(filt)
    return ctx.filter_id


def get_fill_opacity(elem: ET.Element) -> float | None:
    base = 1.0
    op = elem.get("opacity")
    if op:
        try:
            base = float(op)
        except ValueError:
            pass
    fill_op = elem.get("fill-opacity")
    if fill_op:
        try:
            base *= float(fill_op)
        except ValueError:
            pass
    return base if base < 1.0 else None


def get_stroke_opacity(elem: ET.Element) -> float | None:
    base = 1.0
    op = elem.get("opacity")
    if op:
        try:
            base = float(op)
        except ValueError:
            pass
    stroke_op = elem.get("stroke-opacity")
    if stroke_op:
        try:
            base *= float(stroke_op)
        except ValueError:
            pass
    return base if base < 1.0 else None


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def parse_font_family(font_family_str: str) -> dict[str, str]:
    if not font_family_str:
        return {"latin": "Arial", "ea": "Noto Sans SC"}
    fonts = [f.strip().strip("'\"") for f in font_family_str.split(",")]
    latin_font = ea_font = None
    for font in fonts:
        if font in ("system-ui", "-apple-system", "BlinkMacSystemFont",
                     "sans-serif", "serif", "monospace"):
            continue
        if font in EA_FONTS:
            ea_font = ea_font or font
        else:
            latin_font = latin_font or font
    if not latin_font and ea_font:
        latin_font = ea_font
    return {
        "latin": latin_font or "Arial",
        "ea": ea_font or "Noto Sans SC",
    }


def is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x2E80 <= cp <= 0x2EFF or 0x3000 <= cp <= 0x303F or
            0xFF00 <= cp <= 0xFFEF or 0xF900 <= cp <= 0xFAFF or
            0x20000 <= cp <= 0x2A6DF)


def estimate_text_width(text: str, font_size: float, bold: bool = False) -> float:
    width = 0.0
    for ch in text:
        if is_cjk_char(ch):
            width += font_size
        elif ch == " ":
            width += font_size * 0.3
        elif ch in "mMwWOQ":
            width += font_size * 0.75
        elif ch in "iIlj1!|":
            width += font_size * 0.3
        else:
            width += font_size * 0.55
    if bold:
        width *= 1.05
    return width


# ---------------------------------------------------------------------------
# DrawingML XML builders
# ---------------------------------------------------------------------------

def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def build_solid_fill(color: str, opacity: float | None = None) -> str:
    alpha = ""
    if opacity is not None and opacity < 1.0:
        alpha = f'<a:alpha val="{int(opacity * 100000)}"/>'
    return f'<a:solidFill><a:srgbClr val="{color}">{alpha}</a:srgbClr></a:solidFill>'


def build_gradient_fill(grad_elem: ET.Element, opacity: float | None = None) -> str:
    tag = grad_elem.tag.replace(f"{{{SVG_NS}}}", "")
    stops_xml = []
    for child in grad_elem:
        child_tag = child.tag.replace(f"{{{SVG_NS}}}", "")
        if child_tag != "stop":
            continue
        offset_str = child.get("offset", "0").strip().rstrip("%")
        try:
            offset = float(offset_str)
            if offset > 1.0:
                offset = offset / 100.0
        except ValueError:
            offset = 0.0
        pos = int(offset * 100000)

        # Color from style or direct attributes
        color = None
        stop_opacity = 1.0
        style = child.get("style", "")
        for part in style.split(";"):
            part = part.strip()
            if part.startswith("stop-color:"):
                color = parse_hex_color(part.split(":", 1)[1].strip())
            elif part.startswith("stop-opacity:"):
                try:
                    stop_opacity = float(part.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if not color:
            color = parse_hex_color(child.get("stop-color", "#000000"))
        if color is None:
            color = "000000"
        direct_op = child.get("stop-opacity")
        if direct_op is not None:
            try:
                stop_opacity = float(direct_op)
            except ValueError:
                pass

        eff_op = stop_opacity * (opacity if opacity is not None else 1.0)
        alpha_xml = ""
        if eff_op < 1.0:
            alpha_xml = f'<a:alpha val="{int(eff_op * 100000)}"/>'
        stops_xml.append(
            f'<a:gs pos="{pos}"><a:srgbClr val="{color}">{alpha_xml}</a:srgbClr></a:gs>'
        )

    if not stops_xml:
        return ""
    gs_list = "\n".join(stops_xml)

    if tag == "linearGradient":
        def _gc(v: str, d: float = 0.0) -> float:
            v = v.strip()
            if v.endswith("%"):
                return float(v.rstrip("%")) / 100.0
            f = float(v)
            return f / 100.0 if f > 1.0 else f

        x1 = _gc(grad_elem.get("x1", "0"))
        y1 = _gc(grad_elem.get("y1", "0"))
        x2 = _gc(grad_elem.get("x2", "1"))
        y2 = _gc(grad_elem.get("y2", "1"))
        angle_rad = math.atan2(y2 - y1, x2 - x1)
        dml_angle = int(((90 + math.degrees(angle_rad)) % 360) * ANGLE_UNIT)
        return f'<a:gradFill>\n<a:gsLst>{gs_list}</a:gsLst>\n<a:lin ang="{dml_angle}" scaled="1"/>\n</a:gradFill>'

    elif tag == "radialGradient":
        return (f'<a:gradFill>\n<a:gsLst>{gs_list}</a:gsLst>\n'
                '<a:path path="circle"><a:fillToRect l="50000" t="50000" r="50000" b="50000"/></a:path>\n'
                '</a:gradFill>')
    return ""


def build_fill_xml(elem: ET.Element, ctx: ConvertContext,
                   opacity: float | None = None) -> str:
    fill = elem.get("fill")
    if fill is None:
        fill = "#000000"
    if fill == "none":
        return "<a:noFill/>"
    grad_id = resolve_url_id(fill)
    if grad_id and grad_id in ctx.defs:
        return build_gradient_fill(ctx.defs[grad_id], opacity)
    color = parse_hex_color(fill)
    if color:
        return build_solid_fill(color, opacity)
    return "<a:noFill/>"


def build_stroke_xml(elem: ET.Element, opacity: float | None = None) -> str:
    stroke = elem.get("stroke")
    if not stroke or stroke == "none":
        return "<a:ln><a:noFill/></a:ln>"
    color = parse_hex_color(stroke)
    if not color:
        return "<a:ln><a:noFill/></a:ln>"
    width = _f(elem.get("stroke-width"), 1.0)
    width_emu = px_to_emu(width)
    dash_xml = ""
    dasharray = elem.get("stroke-dasharray")
    if dasharray and dasharray != "none":
        preset = DASH_PRESETS.get(dasharray.strip(), "dash")
        dash_xml = f'<a:prstDash val="{preset}"/>'
    cap_map = {"round": "rnd", "square": "sq", "butt": "flat"}
    cap_attr = ""
    linecap = elem.get("stroke-linecap")
    if linecap and linecap in cap_map:
        cap_attr = f' cap="{cap_map[linecap]}"'
    alpha_xml = ""
    if opacity is not None and opacity < 1.0:
        alpha_xml = f'<a:alpha val="{int(opacity * 100000)}"/>'
    return (f'<a:ln w="{width_emu}"{cap_attr}>'
            f'<a:solidFill><a:srgbClr val="{color}">{alpha_xml}</a:srgbClr></a:solidFill>'
            f'{dash_xml}</a:ln>')


def build_shadow_xml(filter_elem: ET.Element) -> str:
    if filter_elem is None:
        return ""
    std_dev = 4.0
    dx = 0.0
    dy = 4.0
    shadow_opacity = 0.3

    for child in filter_elem.iter():
        tag = child.tag.replace(f"{{{SVG_NS}}}", "")
        if tag == "feGaussianBlur":
            std_dev = _f(child.get("stdDeviation"), 4.0)
        elif tag == "feOffset":
            dx = _f(child.get("dx"), 0.0)
            dy = _f(child.get("dy"), 4.0)
        elif tag == "feFlood":
            shadow_opacity = _f(child.get("flood-opacity"), 0.3)
        elif tag == "feDropShadow":
            # Shorthand: <feDropShadow dx="0" dy="4" stdDeviation="8" flood-opacity="0.1"/>
            dx = _f(child.get("dx"), 0.0)
            dy = _f(child.get("dy"), 4.0)
            std_dev = _f(child.get("stdDeviation"), 4.0)
            shadow_opacity = _f(child.get("flood-opacity"), 0.3)

    blur_rad = px_to_emu(std_dev * 2)
    dist = px_to_emu(math.sqrt(dx * dx + dy * dy))
    dir_angle = int(((90 + math.degrees(math.atan2(dy, max(dx, 0.001)))) % 360) * ANGLE_UNIT)
    alpha_val = int(shadow_opacity * 100000)
    return (f'<a:effectLst>'
            f'<a:outerShdw blurRad="{blur_rad}" dist="{dist}" dir="{dir_angle}" algn="tl" rotWithShape="0">'
            f'<a:srgbClr val="000000"><a:alpha val="{alpha_val}"/></a:srgbClr>'
            f'</a:outerShdw></a:effectLst>')


# ---------------------------------------------------------------------------
# SVG Path Parser
# ---------------------------------------------------------------------------

@dataclass
class PathCommand:
    cmd: str
    args: list[float] = field(default_factory=list)


def parse_svg_path(d: str) -> list[PathCommand]:
    if not d:
        return []
    commands: list[PathCommand] = []
    tokens = re.findall(
        r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d
    )
    current_cmd: str | None = None
    current_args: list[float] = []

    arg_counts = {
        "M": 2, "m": 2, "L": 2, "l": 2,
        "H": 1, "h": 1, "V": 1, "v": 1,
        "C": 6, "c": 6, "S": 4, "s": 4,
        "Q": 4, "q": 4, "T": 2, "t": 2,
        "A": 7, "a": 7, "Z": 0, "z": 0,
    }

    def flush():
        nonlocal current_cmd, current_args
        if current_cmd is None:
            return
        n = arg_counts.get(current_cmd, 0)
        if n == 0:
            commands.append(PathCommand(current_cmd, []))
        elif n > 0 and len(current_args) >= n:
            i = 0
            while i + n <= len(current_args):
                commands.append(PathCommand(current_cmd, current_args[i:i + n]))
                if current_cmd == "M":
                    current_cmd = "L"
                elif current_cmd == "m":
                    current_cmd = "l"
                i += n
        current_args = []

    for token in tokens:
        if token in "MmLlHhVvCcSsQqTtAaZz":
            flush()
            current_cmd = token
            current_args = []
        else:
            try:
                current_args.append(float(token))
            except ValueError:
                pass
    flush()
    return commands


def svg_path_to_absolute(commands: list[PathCommand]) -> list[PathCommand]:
    result: list[PathCommand] = []
    cx = cy = sx = sy = 0.0
    for cmd in commands:
        a = cmd.args
        if cmd.cmd == "M":
            cx, cy = a[0], a[1]; sx, sy = cx, cy
            result.append(PathCommand("M", [cx, cy]))
        elif cmd.cmd == "m":
            cx += a[0]; cy += a[1]; sx, sy = cx, cy
            result.append(PathCommand("M", [cx, cy]))
        elif cmd.cmd == "L":
            cx, cy = a[0], a[1]
            result.append(PathCommand("L", [cx, cy]))
        elif cmd.cmd == "l":
            cx += a[0]; cy += a[1]
            result.append(PathCommand("L", [cx, cy]))
        elif cmd.cmd == "H":
            cx = a[0]; result.append(PathCommand("L", [cx, cy]))
        elif cmd.cmd == "h":
            cx += a[0]; result.append(PathCommand("L", [cx, cy]))
        elif cmd.cmd == "V":
            cy = a[0]; result.append(PathCommand("L", [cx, cy]))
        elif cmd.cmd == "v":
            cy += a[0]; result.append(PathCommand("L", [cx, cy]))
        elif cmd.cmd == "C":
            result.append(PathCommand("C", list(a))); cx, cy = a[4], a[5]
        elif cmd.cmd == "c":
            aa = [cx+a[0], cy+a[1], cx+a[2], cy+a[3], cx+a[4], cy+a[5]]
            result.append(PathCommand("C", aa)); cx, cy = aa[4], aa[5]
        elif cmd.cmd == "S":
            result.append(PathCommand("S", list(a))); cx, cy = a[2], a[3]
        elif cmd.cmd == "s":
            aa = [cx+a[0], cy+a[1], cx+a[2], cy+a[3]]
            result.append(PathCommand("S", aa)); cx, cy = aa[2], aa[3]
        elif cmd.cmd == "Q":
            result.append(PathCommand("Q", list(a))); cx, cy = a[2], a[3]
        elif cmd.cmd == "q":
            aa = [cx+a[0], cy+a[1], cx+a[2], cy+a[3]]
            result.append(PathCommand("Q", aa)); cx, cy = aa[2], aa[3]
        elif cmd.cmd == "T":
            result.append(PathCommand("T", list(a))); cx, cy = a[0], a[1]
        elif cmd.cmd == "t":
            aa = [cx+a[0], cy+a[1]]
            result.append(PathCommand("T", aa)); cx, cy = aa[0], aa[1]
        elif cmd.cmd == "A":
            result.append(PathCommand("A", list(a))); cx, cy = a[5], a[6]
        elif cmd.cmd == "a":
            aa = [a[0], a[1], a[2], a[3], a[4], cx+a[5], cy+a[6]]
            result.append(PathCommand("A", aa)); cx, cy = aa[5], aa[6]
        elif cmd.cmd in ("Z", "z"):
            result.append(PathCommand("Z", [])); cx, cy = sx, sy
    return result


def _reflect_cp(cp_x: float, cp_y: float, cx: float, cy: float):
    return 2 * cx - cp_x, 2 * cy - cp_y


def _quad_to_cubic(qx: float, qy: float, p0x: float, p0y: float,
                   p3x: float, p3y: float) -> list[float]:
    return [
        p0x + 2/3 * (qx - p0x), p0y + 2/3 * (qy - p0y),
        p3x + 2/3 * (qx - p3x), p3y + 2/3 * (qy - p3y),
        p3x, p3y,
    ]


def _arc_to_cubic(x1: float, y1: float, rx: float, ry: float,
                  phi: float, large_arc: int, sweep: int,
                  x2: float, y2: float) -> list[PathCommand]:
    if abs(x1 - x2) < 1e-10 and abs(y1 - y2) < 1e-10:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-10 or ry < 1e-10:
        return [PathCommand("L", [x2, y2])]

    phi_rad = math.radians(phi)
    cos_phi = math.cos(phi_rad)
    sin_phi = math.sin(phi_rad)
    dx = (x1 - x2) / 2; dy = (y1 - y2) / 2
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    x1p2, y1p2, rx2, ry2 = x1p*x1p, y1p*y1p, rx*rx, ry*ry
    lam = x1p2/rx2 + y1p2/ry2
    if lam > 1:
        s = math.sqrt(lam); rx *= s; ry *= s; rx2 = rx*rx; ry2 = ry*ry

    num = max(rx2*ry2 - rx2*y1p2 - ry2*x1p2, 0)
    den = rx2*y1p2 + ry2*x1p2
    sq = math.sqrt(num/den) if den > 1e-10 else 0.0
    if large_arc == sweep:
        sq = -sq
    cxp = sq * rx * y1p / ry
    cyp = -sq * ry * x1p / rx
    arc_cx = cos_phi*cxp - sin_phi*cyp + (x1+x2)/2
    arc_cy = sin_phi*cxp + cos_phi*cyp + (y1+y2)/2

    def _ab(ux, uy, vx, vy):
        n = math.sqrt((ux*ux+uy*uy)*(vx*vx+vy*vy))
        if n < 1e-10: return 0
        c = max(-1, min(1, (ux*vx+uy*vy)/n))
        a = math.acos(c)
        return -a if ux*vy - uy*vx < 0 else a

    theta1 = _ab(1, 0, (x1p-cxp)/rx, (y1p-cyp)/ry)
    dtheta = _ab((x1p-cxp)/rx, (y1p-cyp)/ry, (-x1p-cxp)/rx, (-y1p-cyp)/ry)
    if sweep == 0 and dtheta > 0: dtheta -= 2*math.pi
    elif sweep == 1 and dtheta < 0: dtheta += 2*math.pi

    n_segs = max(1, int(math.ceil(abs(dtheta) / (math.pi/2))))
    d_per = dtheta / n_segs
    alpha = 4/3 * math.tan(d_per/4)
    result = []
    for i in range(n_segs):
        t1 = theta1 + i*d_per; t2 = theta1 + (i+1)*d_per
        ct1, st1 = math.cos(t1), math.sin(t1)
        ct2, st2 = math.cos(t2), math.sin(t2)
        def _tp(px, py):
            x = rx*px; y = ry*py
            return cos_phi*x - sin_phi*y + arc_cx, sin_phi*x + cos_phi*y + arc_cy
        cp1 = _tp(ct1 - alpha*st1, st1 + alpha*ct1)
        cp2 = _tp(ct2 + alpha*st2, st2 - alpha*ct2)
        ep = _tp(ct2, st2)
        result.append(PathCommand("C", [cp1[0], cp1[1], cp2[0], cp2[1], ep[0], ep[1]]))
    return result


def normalize_path_commands(commands: list[PathCommand]) -> list[PathCommand]:
    result: list[PathCommand] = []
    cx = cy = lcp_x = lcp_y = 0.0
    last_cmd = ""
    for cmd in commands:
        a = cmd.args
        if cmd.cmd == "M":
            cx, cy = a[0], a[1]; lcp_x, lcp_y = cx, cy; result.append(cmd)
        elif cmd.cmd == "L":
            cx, cy = a[0], a[1]; lcp_x, lcp_y = cx, cy; result.append(cmd)
        elif cmd.cmd == "C":
            lcp_x, lcp_y = a[2], a[3]; cx, cy = a[4], a[5]; result.append(cmd)
        elif cmd.cmd == "S":
            rcp = _reflect_cp(lcp_x, lcp_y, cx, cy) if last_cmd in ("C", "S") else (cx, cy)
            lcp_x, lcp_y = a[0], a[1]
            result.append(PathCommand("C", [rcp[0], rcp[1], a[0], a[1], a[2], a[3]]))
            cx, cy = a[2], a[3]
        elif cmd.cmd == "Q":
            cubic = _quad_to_cubic(a[0], a[1], cx, cy, a[2], a[3])
            lcp_x, lcp_y = a[0], a[1]; result.append(PathCommand("C", cubic))
            cx, cy = a[2], a[3]
        elif cmd.cmd == "T":
            qp = _reflect_cp(lcp_x, lcp_y, cx, cy) if last_cmd in ("Q", "T") else (cx, cy)
            lcp_x, lcp_y = qp[0], qp[1]
            cubic = _quad_to_cubic(qp[0], qp[1], cx, cy, a[0], a[1])
            result.append(PathCommand("C", cubic)); cx, cy = a[0], a[1]
        elif cmd.cmd == "A":
            arcs = _arc_to_cubic(cx, cy, a[0], a[1], a[2], int(a[3]), int(a[4]), a[5], a[6])
            result.extend(arcs); cx, cy = a[5], a[6]; lcp_x, lcp_y = cx, cy
        elif cmd.cmd == "Z":
            result.append(cmd)
        else:
            result.append(cmd)
        last_cmd = cmd.cmd
    return result


def path_commands_to_drawingml(
    commands: list[PathCommand],
    offset_x: float = 0, offset_y: float = 0,
    scale_x: float = 1.0, scale_y: float = 1.0,
) -> tuple[str, float, float, float, float]:
    if not commands:
        return "", 0, 0, 0, 0
    points = []
    for cmd in commands:
        if cmd.cmd in ("M", "L"):
            points.append((cmd.args[0]*scale_x+offset_x, cmd.args[1]*scale_y+offset_y))
        elif cmd.cmd == "C":
            for i in range(0, 6, 2):
                points.append((cmd.args[i]*scale_x+offset_x, cmd.args[i+1]*scale_y+offset_y))
    if not points:
        return "", 0, 0, 0, 0
    min_x = min(p[0] for p in points); min_y = min(p[1] for p in points)
    max_x = max(p[0] for p in points); max_y = max(p[1] for p in points)
    width = max(max_x - min_x, 1); height = max(max_y - min_y, 1)

    parts = []
    for cmd in commands:
        if cmd.cmd == "M":
            xe = px_to_emu(cmd.args[0]*scale_x+offset_x - min_x)
            ye = px_to_emu(cmd.args[1]*scale_y+offset_y - min_y)
            parts.append(f'<a:moveTo><a:pt x="{xe}" y="{ye}"/></a:moveTo>')
        elif cmd.cmd == "L":
            xe = px_to_emu(cmd.args[0]*scale_x+offset_x - min_x)
            ye = px_to_emu(cmd.args[1]*scale_y+offset_y - min_y)
            parts.append(f'<a:lnTo><a:pt x="{xe}" y="{ye}"/></a:lnTo>')
        elif cmd.cmd == "C":
            pts = []
            for i in range(0, 6, 2):
                xe = px_to_emu(cmd.args[i]*scale_x+offset_x - min_x)
                ye = px_to_emu(cmd.args[i+1]*scale_y+offset_y - min_y)
                pts.append(f'<a:pt x="{xe}" y="{ye}"/>')
            parts.append(f'<a:cubicBezTo>{"".join(pts)}</a:cubicBezTo>')
        elif cmd.cmd == "Z":
            parts.append("<a:close/>")
    return "\n".join(parts), min_x, min_y, width, height


# ---------------------------------------------------------------------------
# Shape wrapper
# ---------------------------------------------------------------------------

def _wrap_shape(shape_id: int, name: str, off_x: int, off_y: int,
                ext_cx: int, ext_cy: int,
                geom_xml: str, fill_xml: str, stroke_xml: str,
                effect_xml: str = "", extra_xml: str = "",
                rot: int = 0) -> str:
    rot_attr = f' rot="{rot}"' if rot else ""
    return (f'<p:sp>\n<p:nvSpPr>\n'
            f'<p:cNvPr id="{shape_id}" name="{_xml_escape(name)}"/>\n'
            f'<p:cNvSpPr/><p:nvPr/>\n</p:nvSpPr>\n<p:spPr>\n'
            f'<a:xfrm{rot_attr}><a:off x="{off_x}" y="{off_y}"/>'
            f'<a:ext cx="{ext_cx}" cy="{ext_cy}"/></a:xfrm>\n'
            f'{geom_xml}\n{fill_xml}\n{stroke_xml}\n{effect_xml}\n'
            f'</p:spPr>\n{extra_xml}\n</p:sp>')


# ---------------------------------------------------------------------------
# Element converters
# ---------------------------------------------------------------------------

def convert_rect(elem: ET.Element, ctx: ConvertContext) -> str:
    x = ctx_x(_f(elem.get("x")), ctx)
    y = ctx_y(_f(elem.get("y")), ctx)
    w = ctx_w(_f(elem.get("width")), ctx)
    h = ctx_h(_f(elem.get("height")), ctx)
    # Normalize negative dimensions (SVG bar charts use negative height to grow upward)
    if w < 0:
        x += w
        w = -w
    if h < 0:
        y += h
        h = -h
    if w == 0 or h == 0:
        return ""

    fill_op = get_fill_opacity(elem)
    stroke_op = get_stroke_opacity(elem)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, stroke_op)

    effect = ""
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        effect = build_shadow_xml(ctx.defs[filt_id])

    # Rounded rect support
    rx = _f(elem.get("rx"), 0) * ctx.scale_x
    ry = _f(elem.get("ry"), 0) * ctx.scale_y
    r = max(rx, ry)
    if r > 0 and w > 0 and h > 0:
        # avLst val is in 1/50000ths of the shorter side
        shorter = min(w, h)
        av_val = int(r / shorter * 50000)
        av_val = min(av_val, 50000)
        geom = f'<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val {av_val}"/></a:avLst></a:prstGeom>'
    else:
        geom = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'

    shape_id = ctx.next_id()
    return _wrap_shape(
        shape_id, f"Rect {shape_id}",
        px_to_emu(x), px_to_emu(y), px_to_emu(w), px_to_emu(h),
        geom, fill, stroke, effect,
    )


def convert_circle(elem: ET.Element, ctx: ConvertContext) -> str:
    cx_ = ctx_x(_f(elem.get("cx")), ctx)
    cy_ = ctx_y(_f(elem.get("cy")), ctx)
    r_x = _f(elem.get("r")) * ctx.scale_x
    r_y = _f(elem.get("r")) * ctx.scale_y
    if r_x <= 0 or r_y <= 0:
        return ""
    x, y, w, h = cx_ - r_x, cy_ - r_y, r_x * 2, r_y * 2
    fill_op = get_fill_opacity(elem)
    stroke_op = get_stroke_opacity(elem)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, stroke_op)
    effect = ""
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        effect = build_shadow_xml(ctx.defs[filt_id])
    geom = '<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>'
    shape_id = ctx.next_id()
    return _wrap_shape(
        shape_id, f"Circle {shape_id}",
        px_to_emu(x), px_to_emu(y), px_to_emu(w), px_to_emu(h),
        geom, fill, stroke, effect,
    )


def convert_ellipse(elem: ET.Element, ctx: ConvertContext) -> str:
    cx_ = ctx_x(_f(elem.get("cx")), ctx)
    cy_ = ctx_y(_f(elem.get("cy")), ctx)
    rx = _f(elem.get("rx")) * ctx.scale_x
    ry = _f(elem.get("ry")) * ctx.scale_y
    if rx <= 0 or ry <= 0:
        return ""
    x, y, w, h = cx_ - rx, cy_ - ry, rx * 2, ry * 2
    fill_op = get_fill_opacity(elem)
    stroke_op = get_stroke_opacity(elem)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, stroke_op)
    effect = ""
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        effect = build_shadow_xml(ctx.defs[filt_id])
    geom = '<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>'
    shape_id = ctx.next_id()
    return _wrap_shape(
        shape_id, f"Ellipse {shape_id}",
        px_to_emu(x), px_to_emu(y), px_to_emu(w), px_to_emu(h),
        geom, fill, stroke, effect,
    )


def convert_line(elem: ET.Element, ctx: ConvertContext) -> str:
    x1 = ctx_x(_f(elem.get("x1")), ctx)
    y1 = ctx_y(_f(elem.get("y1")), ctx)
    x2 = ctx_x(_f(elem.get("x2")), ctx)
    y2 = ctx_y(_f(elem.get("y2")), ctx)
    mn_x, mn_y = min(x1, x2), min(y1, y2)
    w = max(abs(x2 - x1), 1); h = max(abs(y2 - y1), 1)
    w_emu, h_emu = px_to_emu(w), px_to_emu(h)
    lx1, ly1 = px_to_emu(x1 - mn_x), px_to_emu(y1 - mn_y)
    lx2, ly2 = px_to_emu(x2 - mn_x), px_to_emu(y2 - mn_y)
    geom = (f'<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
            f'<a:rect l="l" t="t" r="r" b="b"/>'
            f'<a:pathLst><a:path w="{w_emu}" h="{h_emu}">'
            f'<a:moveTo><a:pt x="{lx1}" y="{ly1}"/></a:moveTo>'
            f'<a:lnTo><a:pt x="{lx2}" y="{ly2}"/></a:lnTo>'
            f'</a:path></a:pathLst></a:custGeom>')
    stroke_op = get_stroke_opacity(elem)
    stroke = build_stroke_xml(elem, stroke_op)
    shape_id = ctx.next_id()
    return _wrap_shape(
        shape_id, f"Line {shape_id}",
        px_to_emu(mn_x), px_to_emu(mn_y), w_emu, h_emu,
        geom, "<a:noFill/>", stroke,
    )


def convert_path(elem: ET.Element, ctx: ConvertContext) -> str:
    d = elem.get("d", "")
    if not d:
        return ""
    commands = parse_svg_path(d)
    commands = svg_path_to_absolute(commands)
    commands = normalize_path_commands(commands)

    tx, ty, rot = 0.0, 0.0, 0
    transform = elem.get("transform")
    if transform:
        t_m = re.search(r"translate\(\s*([-\d.]+)[\s,]+([-\d.]+)\s*\)", transform)
        if t_m:
            tx, ty = float(t_m.group(1)), float(t_m.group(2))
        r_m = re.search(r"rotate\(\s*([-\d.]+)", transform)
        if r_m:
            rot = int(float(r_m.group(1)) * ANGLE_UNIT)

    path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
        commands, ctx.translate_x + tx, ctx.translate_y + ty,
        ctx.scale_x, ctx.scale_y,
    )
    if not path_xml:
        return ""
    w_emu, h_emu = px_to_emu(width), px_to_emu(height)
    geom = (f'<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
            f'<a:rect l="l" t="t" r="r" b="b"/>'
            f'<a:pathLst><a:path w="{w_emu}" h="{h_emu}">\n{path_xml}\n'
            f'</a:path></a:pathLst></a:custGeom>')
    fill_op = get_fill_opacity(elem)
    stroke_op = get_stroke_opacity(elem)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, stroke_op)
    effect = ""
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        effect = build_shadow_xml(ctx.defs[filt_id])
    shape_id = ctx.next_id()
    return _wrap_shape(
        shape_id, f"Path {shape_id}",
        px_to_emu(min_x), px_to_emu(min_y), w_emu, h_emu,
        geom, fill, stroke, effect, rot=rot,
    )


def convert_polygon(elem: ET.Element, ctx: ConvertContext) -> str:
    points_str = elem.get("points", "")
    if not points_str:
        return ""
    nums = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", points_str)
    if len(nums) < 4:
        return ""
    points = [(float(nums[i]), float(nums[i+1])) for i in range(0, len(nums)-1, 2)]
    commands = [PathCommand("M", [points[0][0], points[0][1]])]
    for px_, py_ in points[1:]:
        commands.append(PathCommand("L", [px_, py_]))
    commands.append(PathCommand("Z", []))
    path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
        commands, ctx.translate_x, ctx.translate_y, ctx.scale_x, ctx.scale_y,
    )
    if not path_xml:
        return ""
    w_emu, h_emu = px_to_emu(width), px_to_emu(height)
    geom = (f'<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
            f'<a:rect l="l" t="t" r="r" b="b"/>'
            f'<a:pathLst><a:path w="{w_emu}" h="{h_emu}">\n{path_xml}\n'
            f'</a:path></a:pathLst></a:custGeom>')
    fill_op = get_fill_opacity(elem)
    stroke_op = get_stroke_opacity(elem)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, stroke_op)
    shape_id = ctx.next_id()
    return _wrap_shape(
        shape_id, f"Polygon {shape_id}",
        px_to_emu(min_x), px_to_emu(min_y), w_emu, h_emu,
        geom, fill, stroke,
    )


# ---------------------------------------------------------------------------
# Text converter — supports <tspan> multi-line → multi-paragraph
# ---------------------------------------------------------------------------

def convert_text(elem: ET.Element, ctx: ConvertContext) -> str:
    """Convert SVG <text> with optional <tspan> children to DrawingML text shape."""
    # Collect paragraphs: each tspan with dy>0 starts a new line
    paragraphs: list[list[dict]] = []  # list of [{"text", "bold", "size", "color"}]
    base_fs = _f(elem.get("font-size"), 16) * ctx.scale_y
    base_weight = elem.get("font-weight", "400")
    base_color = parse_hex_color(elem.get("fill", "#000000")) or "000000"
    font_family_str = elem.get("font-family", "")
    text_anchor = elem.get("text-anchor", "start")
    opacity = get_fill_opacity(elem)
    fonts = parse_font_family(font_family_str)
    x_base = ctx_x(_f(elem.get("x")), ctx)
    y_base = ctx_y(_f(elem.get("y")), ctx)

    tspans = list(elem.iter(f"{{{SVG_NS}}}tspan"))
    if not tspans:
        # Also check non-namespaced tspans
        tspans = list(elem.iter("tspan"))

    if tspans:
        # Multi-line via tspan
        current_line: list[dict] = []
        total_dy = 0.0
        # base_fs is already scaled; use unscaled for em conversion
        raw_fs = _f(elem.get("font-size"), 16)
        for ts in tspans:
            dy = _f(ts.get("dy"), 0, font_size=raw_fs)
            total_dy += dy
            ts_text = (ts.text or "").strip()
            if not ts_text:
                continue
            ts_weight = ts.get("font-weight", base_weight)
            ts_size = _f(ts.get("font-size"), base_fs / ctx.scale_y) * ctx.scale_y
            ts_color = parse_hex_color(ts.get("fill") or elem.get("fill", "#000000")) or base_color
            run = {
                "text": ts_text,
                "bold": ts_weight in ("bold", "600", "700", "800", "900"),
                "size": ts_size,
                "color": ts_color,
            }
            if dy > 0 and current_line:
                paragraphs.append(current_line)
                current_line = [run]
            else:
                current_line.append(run)
        if current_line:
            paragraphs.append(current_line)
    else:
        # Single-line text
        text = (elem.text or "").strip()
        if not text:
            return ""
        paragraphs.append([{
            "text": text,
            "bold": base_weight in ("bold", "600", "700", "800", "900"),
            "size": base_fs,
            "color": base_color,
        }])

    if not paragraphs:
        return ""

    is_multiline = len(paragraphs) > 1
    is_bold = base_weight in ("bold", "600", "700", "800", "900")

    # Calculate text box dimensions
    max_width = 0.0
    total_height = 0.0
    for para in paragraphs:
        line_text = "".join(r["text"] for r in para)
        line_fs = para[0]["size"] if para else base_fs
        w = estimate_text_width(line_text, line_fs, para[0].get("bold", is_bold) if para else is_bold) * 1.15
        max_width = max(max_width, w)
        total_height += line_fs * 1.5

    padding = base_fs * 0.2
    box_w = max_width + padding * 2
    box_h = total_height + padding * 2

    # For multiline text inside cards, try to use tspan's x to infer a reasonable
    # fixed width so wrapping works correctly in PowerPoint
    if is_multiline and tspans:
        # Use the tspan x attribute if available — it tells us the card's left edge
        ts_x = _f(tspans[0].get("x"), 0) * ctx.scale_x + ctx.translate_x
        # Estimate right edge from slide width (1280) minus some margin
        inferred_width = max(box_w, (1230 - ts_x) * 0.95)
        box_w = min(inferred_width, 1200)  # cap at reasonable max

    # Adjust for text-anchor
    if text_anchor == "middle":
        box_x = x_base - box_w / 2
    elif text_anchor == "end":
        box_x = x_base - box_w
    else:
        box_x = x_base - padding

    # y in SVG is baseline; move up
    box_y = y_base - base_fs * 0.85

    # Alignment
    algn_map = {"start": "l", "middle": "ctr", "end": "r"}
    algn = algn_map.get(text_anchor, "l")

    # Alpha
    alpha_xml = ""
    if opacity is not None and opacity < 1.0:
        alpha_xml = f'<a:alpha val="{int(opacity * 100000)}"/>'

    # Build paragraphs XML with proper line spacing for multiline
    line_spc_xml = ""
    if is_multiline:
        # Use the dy value from tspans as line spacing (in hundredths of pt)
        avg_dy = base_fs * 1.4  # default
        if tspans and len(tspans) > 1:
            dy_val = _f(tspans[1].get("dy"), 0)
            if dy_val > 0:
                avg_dy = dy_val * ctx.scale_y
        spc_pts = int(avg_dy * FONT_PX_TO_HUNDREDTHS_PT)
        line_spc_xml = f'<a:lnSpc><a:spcPts val="{spc_pts}"/></a:lnSpc>'

    paras_xml = []
    for para in paragraphs:
        runs_xml = []
        for run in para:
            sz = round(run["size"] * FONT_PX_TO_HUNDREDTHS_PT)
            b_attr = ' b="1"' if run["bold"] else ""
            runs_xml.append(
                f'<a:r><a:rPr lang="zh-CN" sz="{sz}"{b_attr} dirty="0">'
                f'<a:solidFill><a:srgbClr val="{run["color"]}">{alpha_xml}</a:srgbClr></a:solidFill>'
                f'<a:latin typeface="{_xml_escape(fonts["latin"])}"/>'
                f'<a:ea typeface="{_xml_escape(fonts["ea"])}"/>'
                f'</a:rPr><a:t>{_xml_escape(run["text"])}</a:t></a:r>'
            )
        paras_xml.append(f'<a:p><a:pPr algn="{algn}">{line_spc_xml}</a:pPr>{"".join(runs_xml)}</a:p>')

    # Always wrap="square" — enables word wrap for user editability in PowerPoint
    wrap_mode = "square"

    shape_id = ctx.next_id()
    return (
        f'<p:sp>\n<p:nvSpPr>\n'
        f'<p:cNvPr id="{shape_id}" name="Text {shape_id}"/>\n'
        f'<p:cNvSpPr txBox="1"/><p:nvPr/>\n</p:nvSpPr>\n<p:spPr>\n'
        f'<a:xfrm><a:off x="{px_to_emu(box_x)}" y="{px_to_emu(box_y)}"/>'
        f'<a:ext cx="{px_to_emu(box_w)}" cy="{px_to_emu(box_h)}"/></a:xfrm>\n'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>\n'
        f'<a:noFill/><a:ln><a:noFill/></a:ln>\n</p:spPr>\n'
        f'<p:txBody>\n'
        f'<a:bodyPr wrap="{wrap_mode}" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t" anchorCtr="0"/>\n'
        f'<a:lstStyle/>\n'
        f'{"".join(paras_xml)}\n</p:txBody>\n</p:sp>'
    )


def convert_image(elem: ET.Element, ctx: ConvertContext) -> str:
    href = elem.get("href") or elem.get(f"{{{XLINK_NS}}}href")
    if not href:
        return ""
    x = ctx_x(_f(elem.get("x")), ctx)
    y = ctx_y(_f(elem.get("y")), ctx)
    w = ctx_w(_f(elem.get("width")), ctx)
    h = ctx_h(_f(elem.get("height")), ctx)
    if w <= 0 or h <= 0:
        return ""

    if href.startswith("data:"):
        match = re.match(r"data:image/(\w+);base64,(.+)", href, re.DOTALL)
        if not match:
            return ""
        img_format = match.group(1).lower()
        if img_format == "jpeg":
            img_format = "jpg"
        img_data = base64.b64decode(match.group(2))
    else:
        # External file
        try:
            img_data = Path(href).read_bytes()
            img_format = Path(href).suffix.lstrip(".").lower()
            if img_format == "jpeg":
                img_format = "jpg"
        except Exception:
            return ""

    img_idx = len(ctx.media_files) + 1
    img_filename = f"s{ctx.slide_num}_img{img_idx}.{img_format}"
    ctx.media_files[img_filename] = img_data
    r_id = ctx.next_rel_id()
    ctx.rel_entries.append({
        "id": r_id,
        "type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
        "target": f"../media/{img_filename}",
    })
    shape_id = ctx.next_id()
    return (
        f'<p:pic>\n<p:nvPicPr>\n'
        f'<p:cNvPr id="{shape_id}" name="Image {shape_id}"/>\n'
        f'<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>\n'
        f'<p:nvPr/>\n</p:nvPicPr>\n<p:blipFill>\n'
        f'<a:blip r:embed="{r_id}"/>\n'
        f'<a:stretch><a:fillRect/></a:stretch>\n</p:blipFill>\n<p:spPr>\n'
        f'<a:xfrm><a:off x="{px_to_emu(x)}" y="{px_to_emu(y)}"/>'
        f'<a:ext cx="{px_to_emu(w)}" cy="{px_to_emu(h)}"/></a:xfrm>\n'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>\n</p:spPr>\n</p:pic>'
    )


# ---------------------------------------------------------------------------
# Group and Use
# ---------------------------------------------------------------------------

def parse_transform(transform_str: str) -> tuple[float, float, float, float]:
    if not transform_str:
        return 0.0, 0.0, 1.0, 1.0
    dx = dy = 0.0
    sx = sy = 1.0
    m = re.search(r"translate\(\s*([-\d.]+)[\s,]+([-\d.]+)\s*\)", transform_str)
    if m:
        dx, dy = float(m.group(1)), float(m.group(2))
    m = re.search(r"scale\(\s*([-\d.]+)(?:[\s,]+([-\d.]+))?\s*\)", transform_str)
    if m:
        sx = float(m.group(1))
        sy = float(m.group(2)) if m.group(2) else sx
    return dx, dy, sx, sy


def convert_g(elem: ET.Element, ctx: ConvertContext) -> str:
    transform = elem.get("transform", "")
    dx, dy, sx, sy = parse_transform(transform)
    filter_id = resolve_url_id(elem.get("filter", ""))
    group_fill = elem.get("fill")
    group_opacity = elem.get("opacity")

    child_ctx = ctx.child(dx, dy, sx, sy, filter_id)
    shapes = []
    for child in elem:
        # Propagate group attributes to children
        if group_fill and not child.get("fill"):
            child.set("fill", group_fill)
        if group_opacity and not child.get("opacity"):
            child.set("opacity", group_opacity)
        shape_xml = convert_element(child, child_ctx)
        if shape_xml:
            shapes.append(shape_xml)
    ctx.sync_from_child(child_ctx)
    return "\n".join(shapes)


def convert_use(elem: ET.Element, ctx: ConvertContext) -> str:
    """Resolve <use href="#id"> by inlining the referenced element from defs."""
    href = elem.get("href") or elem.get(f"{{{XLINK_NS}}}href") or ""
    ref_id = href.lstrip("#")
    if not ref_id or ref_id not in ctx.defs:
        return ""

    ref_elem = ctx.defs[ref_id]
    # Apply use element's x/y as additional translate
    use_x = _f(elem.get("x"), 0)
    use_y = _f(elem.get("y"), 0)

    # Inherit attributes from <use> onto the referenced element
    use_opacity = elem.get("opacity")

    child_ctx = ctx.child(dx=use_x, dy=use_y)
    if use_opacity:
        # Temporarily set opacity on ref for conversion
        old_op = ref_elem.get("opacity")
        ref_elem.set("opacity", use_opacity)
        result = convert_element(ref_elem, child_ctx)
        if old_op is not None:
            ref_elem.set("opacity", old_op)
        else:
            if "opacity" in ref_elem.attrib:
                del ref_elem.attrib["opacity"]
    else:
        result = convert_element(ref_elem, child_ctx)

    ctx.sync_from_child(child_ctx)
    return result


# ---------------------------------------------------------------------------
# Main dispatch and entry point
# ---------------------------------------------------------------------------

def collect_defs(root: ET.Element) -> dict[str, ET.Element]:
    defs: dict[str, ET.Element] = {}
    for defs_elem in root.iter(f"{{{SVG_NS}}}defs"):
        for child in defs_elem:
            elem_id = child.get("id")
            if elem_id:
                defs[elem_id] = child
    for defs_elem in root.iter("defs"):
        for child in defs_elem:
            elem_id = child.get("id")
            if elem_id:
                defs[elem_id] = child
    return defs


def convert_element(elem: ET.Element, ctx: ConvertContext) -> str:
    tag = elem.tag.replace(f"{{{SVG_NS}}}", "")
    converters = {
        "rect": convert_rect,
        "circle": convert_circle,
        "ellipse": convert_ellipse,
        "line": convert_line,
        "path": convert_path,
        "polygon": convert_polygon,
        "text": convert_text,
        "image": convert_image,
        "g": convert_g,
        "use": convert_use,
    }
    converter = converters.get(tag)
    if converter:
        try:
            return converter(elem, ctx)
        except Exception as e:
            logger.debug("Failed to convert <{}>: {}", tag, e)
            return ""
    return ""


def convert_svg_to_slide_shapes(
    svg_path: Path,
    slide_num: int = 1,
) -> tuple[str, dict[str, bytes], list[dict[str, str]]]:
    """Convert an SVG file to a complete DrawingML slide XML.

    Returns:
        (slide_xml, media_files, rel_entries)
        - slide_xml: Complete slide XML string
        - media_files: {filename: bytes} for media to write to ppt/media/
        - rel_entries: Relationship entries [{id, type, target}]
    """
    # Pre-clean SVG (unescaped &)
    content = svg_path.read_text(encoding="utf-8")
    content = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", content)

    root = ET.fromstring(content)
    defs = collect_defs(root)
    ctx = ConvertContext(defs=defs, slide_num=slide_num)

    shapes = []
    converted = skipped = 0
    for child in root:
        tag = child.tag.replace(f"{{{SVG_NS}}}", "")
        if tag in ("defs", "title", "desc", "metadata", "style"):
            continue
        result = convert_element(child, ctx)
        if result:
            shapes.append(result)
            converted += 1
        else:
            skipped += 1

    logger.debug("SVG→Shapes: {} converted, {} skipped (slide {})", converted, skipped, slide_num)

    shapes_xml = "\n".join(shapes)
    slide_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"\n'
        '       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"\n'
        '       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">\n'
        '<p:cSld>\n<p:spTree>\n'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>\n'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>\n'
        f'{shapes_xml}\n'
        '</p:spTree>\n</p:cSld>\n'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>\n'
        '</p:sld>'
    )
    return slide_xml, ctx.media_files, ctx.rel_entries
