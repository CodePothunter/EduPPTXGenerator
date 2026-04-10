"""Programmatic diagram generators using Pillow."""
from __future__ import annotations

import math
from PIL import Image, ImageDraw, ImageFont

from edupptx.design_system import DesignTokens


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _get_font(size: int = 16) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ["NotoSansSC-Regular.otf", "NotoSansSC-Regular.ttf", "Arial.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _placeholder(size: tuple[int, int], text: str = "No data") -> Image.Image:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _get_font(24)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size[0] - tw) // 2, (size[1] - th) // 2), text, fill=(150, 150, 150, 200), font=font)
    return img


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    bbox: tuple[int, int, int, int],
    radius: int,
    fill: tuple,
    outline: tuple,
    outline_width: int = 2,
) -> None:
    x0, y0, x1, y1 = bbox
    radius = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=outline_width)


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple,
    width: int = 3,
    arrow_size: int = 12,
) -> None:
    draw.line([start, end], fill=color, width=width)
    # Arrowhead
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return
    ux, uy = dx / length, dy / length
    # Two wing points
    wx, wy = -uy * arrow_size * 0.4, ux * arrow_size * 0.4
    p1 = (end[0] - ux * arrow_size + wx, end[1] - uy * arrow_size + wy)
    p2 = (end[0] - ux * arrow_size - wx, end[1] - uy * arrow_size - wy)
    draw.polygon([end, p1, p2], fill=color)


def generate_flowchart(
    data: dict,
    tokens: DesignTokens,
    size: tuple[int, int] = (1200, 800),
) -> Image.Image:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    direction = data.get("direction", "TB")

    if not nodes:
        return _placeholder(size)

    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent = _hex_to_rgb(tokens.accent) + (255,)
    accent_light = _hex_to_rgb(tokens.accent_light) + (255,)
    text_color = _hex_to_rgb(tokens.text_primary) + (255,)

    padding = 60
    node_w = min(160, (w - padding * 2) // max(len(nodes), 1) - 20)
    node_h = 50

    n = len(nodes)
    positions: dict[str, tuple[int, int]] = {}

    if direction == "LR":
        # Left-to-right: nodes spread horizontally
        step_x = (w - padding * 2) // max(n, 1)
        cy = h // 2
        for i, node in enumerate(nodes):
            cx = padding + step_x * i + step_x // 2
            positions[node["id"]] = (cx, cy)
    else:
        # Top-to-bottom: nodes spread vertically
        step_y = (h - padding * 2) // max(n, 1)
        cx = w // 2
        for i, node in enumerate(nodes):
            cy = padding + step_y * i + step_y // 2
            positions[node["id"]] = (cx, cy)

    # Draw edges first (behind nodes)
    for edge in edges:
        src_id = edge.get("from", "")
        dst_id = edge.get("to", "")
        if src_id not in positions or dst_id not in positions:
            continue
        sx, sy = positions[src_id]
        ex, ey = positions[dst_id]
        # Offset start/end to node borders
        if direction == "LR":
            start = (sx + node_w // 2, sy)
            end = (ex - node_w // 2, ey)
        else:
            start = (sx, sy + node_h // 2)
            end = (ex, ey - node_h // 2)
        _draw_arrow(draw, start, end, accent, width=3)

        label = edge.get("label", "")
        if label:
            mid_x = (start[0] + end[0]) // 2
            mid_y = (start[1] + end[1]) // 2
            font = _get_font(12)
            draw.text((mid_x + 4, mid_y - 14), label, fill=text_color, font=font)

    # Draw nodes
    font = _get_font(14)
    for node in nodes:
        nid = node["id"]
        label = node.get("label", nid)
        if nid not in positions:
            continue
        cx, cy = positions[nid]
        x0 = cx - node_w // 2
        y0 = cy - node_h // 2
        x1 = cx + node_w // 2
        y1 = cy + node_h // 2
        _draw_rounded_rect(draw, (x0, y0, x1, y1), radius=10, fill=accent_light, outline=accent, outline_width=2)
        tbbox = draw.textbbox((0, 0), label, font=font)
        tw = tbbox[2] - tbbox[0]
        th = tbbox[3] - tbbox[1]
        draw.text((cx - tw // 2, cy - th // 2), label, fill=text_color, font=font)

    return img


def generate_timeline(
    data: dict,
    tokens: DesignTokens,
    size: tuple[int, int] = (1400, 400),
) -> Image.Image:
    events = data.get("events", [])

    if not events:
        return _placeholder(size)

    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent = _hex_to_rgb(tokens.accent) + (255,)
    accent_light = _hex_to_rgb(tokens.accent_light) + (255,)
    text_color = _hex_to_rgb(tokens.text_primary) + (255,)
    text_secondary = _hex_to_rgb(tokens.text_secondary) + (255,)

    padding = 80
    line_y = h // 2
    n = len(events)
    step = (w - padding * 2) // max(n - 1, 1) if n > 1 else 0

    # Draw horizontal line
    draw.line([(padding, line_y), (w - padding, line_y)], fill=accent, width=3)

    font_year = _get_font(16)
    font_label = _get_font(14)
    font_desc = _get_font(11)
    marker_r = 10

    for i, event in enumerate(events):
        x = padding + step * i if n > 1 else w // 2
        year = str(event.get("year", ""))
        label = event.get("label", "")
        description = event.get("description", "")

        # Marker circle
        draw.ellipse(
            (x - marker_r, line_y - marker_r, x + marker_r, line_y + marker_r),
            fill=accent,
            outline=accent_light,
            width=3,
        )

        # Year above line
        if year:
            tbbox = draw.textbbox((0, 0), year, font=font_year)
            tw = tbbox[2] - tbbox[0]
            draw.text((x - tw // 2, line_y - marker_r - 30), year, fill=accent, font=font_year)

        # Label below line
        if label:
            tbbox = draw.textbbox((0, 0), label, font=font_label)
            tw = tbbox[2] - tbbox[0]
            draw.text((x - tw // 2, line_y + marker_r + 10), label, fill=text_color, font=font_label)

        # Description further below
        if description:
            tbbox = draw.textbbox((0, 0), description, font=font_desc)
            tw = tbbox[2] - tbbox[0]
            draw.text((x - tw // 2, line_y + marker_r + 34), description, fill=text_secondary, font=font_desc)

    return img


def generate_comparison(
    data: dict,
    tokens: DesignTokens,
    size: tuple[int, int] = (1200, 800),
) -> Image.Image:
    columns = data.get("columns", [])

    if not columns:
        return _placeholder(size)

    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent = _hex_to_rgb(tokens.accent) + (255,)
    accent_light = _hex_to_rgb(tokens.accent_light) + (255,)
    text_color = _hex_to_rgb(tokens.text_primary) + (255,)
    text_secondary = _hex_to_rgb(tokens.text_secondary) + (255,)

    padding = 40
    gap = 20
    n = len(columns)
    col_w = (w - padding * 2 - gap * (n - 1)) // n

    font_header = _get_font(18)
    font_item = _get_font(14)

    for i, col in enumerate(columns):
        x0 = padding + i * (col_w + gap)
        x1 = x0 + col_w
        header = col.get("header", "")
        items = col.get("items", [])

        # Header box
        header_h = 50
        _draw_rounded_rect(draw, (x0, padding, x1, padding + header_h), radius=8, fill=accent, outline=accent, outline_width=0)
        if header:
            tbbox = draw.textbbox((0, 0), header, font=font_header)
            tw = tbbox[2] - tbbox[0]
            th = tbbox[3] - tbbox[1]
            hcx = x0 + col_w // 2
            hcy = padding + header_h // 2
            draw.text((hcx - tw // 2, hcy - th // 2), header, fill=(255, 255, 255, 255), font=font_header)

        # Items box
        items_y0 = padding + header_h + 8
        items_y1 = h - padding
        _draw_rounded_rect(draw, (x0, items_y0, x1, items_y1), radius=8, fill=accent_light, outline=accent, outline_width=2)

        item_x = x0 + 16
        item_y = items_y0 + 16
        line_h = 28
        for item in items:
            if item_y + line_h > items_y1 - 8:
                break
            # Bullet dot
            draw.ellipse((item_x, item_y + 5, item_x + 7, item_y + 12), fill=accent)
            draw.text((item_x + 14, item_y), item, fill=text_color, font=font_item)
            item_y += line_h

    return img


def _layout_tree(
    node: dict,
    depth: int,
    x_counter: list[int],
    level_height: int,
    node_w: int,
    node_h: int,
    padding: int,
) -> dict:
    """Recursively assign (cx, cy) to each node and return enriched tree."""
    children = node.get("children", [])
    enriched_children = [
        _layout_tree(child, depth + 1, x_counter, level_height, node_w, node_h, padding)
        for child in children
    ]

    if not enriched_children:
        # Leaf node: take next x slot
        cx = padding + x_counter[0] * (node_w + 20) + node_w // 2
        x_counter[0] += 1
    else:
        # Internal node: center over children
        min_cx = enriched_children[0]["cx"]
        max_cx = enriched_children[-1]["cx"]
        cx = (min_cx + max_cx) // 2

    cy = padding + depth * level_height + node_h // 2
    return {**node, "cx": cx, "cy": cy, "children": enriched_children}


def _draw_tree(
    draw: ImageDraw.ImageDraw,
    node: dict,
    node_w: int,
    node_h: int,
    accent: tuple,
    accent_light: tuple,
    text_color: tuple,
    font: ImageFont.ImageFont,
) -> None:
    cx, cy = node["cx"], node["cy"]
    x0 = cx - node_w // 2
    y0 = cy - node_h // 2
    x1 = cx + node_w // 2
    y1 = cy + node_h // 2
    _draw_rounded_rect(draw, (x0, y0, x1, y1), radius=8, fill=accent_light, outline=accent, outline_width=2)
    label = node.get("label", "")
    tbbox = draw.textbbox((0, 0), label, font=font)
    tw = tbbox[2] - tbbox[0]
    th = tbbox[3] - tbbox[1]
    draw.text((cx - tw // 2, cy - th // 2), label, fill=text_color, font=font)

    for child in node.get("children", []):
        # Line from bottom of parent to top of child
        start = (cx, cy + node_h // 2)
        end = (child["cx"], child["cy"] - node_h // 2)
        draw.line([start, end], fill=accent, width=2)
        _draw_tree(draw, child, node_w, node_h, accent, accent_light, text_color, font)


def generate_hierarchy(
    data: dict,
    tokens: DesignTokens,
    size: tuple[int, int] = (1200, 800),
) -> Image.Image:
    root = data.get("root")

    if not root:
        return _placeholder(size)

    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent = _hex_to_rgb(tokens.accent) + (255,)
    accent_light = _hex_to_rgb(tokens.accent_light) + (255,)
    text_color = _hex_to_rgb(tokens.text_primary) + (255,)

    # Count total leaf nodes to size the canvas usage
    def count_leaves(n: dict) -> int:
        children = n.get("children", [])
        if not children:
            return 1
        return sum(count_leaves(c) for c in children)

    def tree_depth(n: dict) -> int:
        children = n.get("children", [])
        if not children:
            return 1
        return 1 + max(tree_depth(c) for c in children)

    leaves = count_leaves(root)
    depth = tree_depth(root)

    node_w = min(160, (w - 80) // max(leaves, 1) - 20)
    node_h = 44
    level_height = (h - 80) // max(depth, 1)

    padding = 40
    x_counter = [0]
    enriched = _layout_tree(root, 0, x_counter, level_height, node_w, node_h, padding)

    # Center the whole tree horizontally
    total_width = x_counter[0] * (node_w + 20) - 20
    offset_x = (w - total_width) // 2 - padding

    def shift_x(node: dict, dx: int) -> dict:
        children = [shift_x(c, dx) for c in node.get("children", [])]
        return {**node, "cx": node["cx"] + dx, "children": children}

    enriched = shift_x(enriched, offset_x)

    font = _get_font(14)
    _draw_tree(draw, enriched, node_w, node_h, accent, accent_light, text_color, font)

    return img


def generate_cycle(
    data: dict,
    tokens: DesignTokens,
    size: tuple[int, int] = (800, 800),
) -> Image.Image:
    steps = data.get("steps", [])

    if not steps:
        return _placeholder(size)

    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent = _hex_to_rgb(tokens.accent) + (255,)
    accent_light = _hex_to_rgb(tokens.accent_light) + (255,)
    text_color = _hex_to_rgb(tokens.text_primary) + (255,)
    text_secondary = _hex_to_rgb(tokens.text_secondary) + (255,)

    n = len(steps)
    cx, cy = w // 2, h // 2
    # Radius of the circle along which nodes are placed
    node_w, node_h = 140, 54
    margin = max(node_w, node_h) // 2 + 20
    radius = min(cx, cy) - margin

    font_label = _get_font(14)
    font_desc = _get_font(11)

    # Calculate center position for each step
    step_positions: list[tuple[int, int]] = []
    for i in range(n):
        angle = 2 * math.pi * i / n - math.pi / 2  # start from top
        sx = int(cx + radius * math.cos(angle))
        sy = int(cy + radius * math.sin(angle))
        step_positions.append((sx, sy))

    # Draw arrows between steps (before boxes so boxes sit on top)
    for i in range(n):
        sx, sy = step_positions[i]
        ex, ey = step_positions[(i + 1) % n]

        # Find direction vector
        dx = ex - sx
        dy = ey - sy
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        ux, uy = dx / length, dy / length

        # Start from edge of source box, end at edge of target box
        half_w = node_w // 2
        half_h = node_h // 2
        # Approximate box intersection: use ellipse approximation
        t_src = math.hypot(half_w * uy, half_h * ux) / max(abs(ux * half_h) + abs(uy * half_w), 1)
        start_x = int(sx + ux * half_w)
        start_y = int(sy + uy * half_h)
        end_x = int(ex - ux * half_w)
        end_y = int(ey - uy * half_h)

        _draw_arrow(draw, (start_x, start_y), (end_x, end_y), accent, width=3)

    # Draw step boxes
    for i, (sx, sy) in enumerate(step_positions):
        label = steps[i].get("label", "")
        description = steps[i].get("description", "")

        x0 = sx - node_w // 2
        y0 = sy - node_h // 2
        x1 = sx + node_w // 2
        y1 = sy + node_h // 2
        _draw_rounded_rect(draw, (x0, y0, x1, y1), radius=10, fill=accent_light, outline=accent, outline_width=2)

        if label:
            tbbox = draw.textbbox((0, 0), label, font=font_label)
            tw = tbbox[2] - tbbox[0]
            th = tbbox[3] - tbbox[1]
            if description:
                draw.text((sx - tw // 2, sy - node_h // 4 - th // 2), label, fill=text_color, font=font_label)
                tbbox2 = draw.textbbox((0, 0), description, font=font_desc)
                tw2 = tbbox2[2] - tbbox2[0]
                draw.text((sx - tw2 // 2, sy + node_h // 4 - (tbbox2[3] - tbbox2[1]) // 2), description, fill=text_secondary, font=font_desc)
            else:
                draw.text((sx - tw // 2, sy - th // 2), label, fill=text_color, font=font_label)

    return img


def generate_diagram(
    diagram_type: str,
    data: dict,
    tokens: DesignTokens,
    size: tuple[int, int] | None = None,
) -> Image.Image:
    """Dispatch to the appropriate diagram generator."""
    generators = {
        "flowchart": (generate_flowchart, (1200, 800)),
        "timeline": (generate_timeline, (1400, 400)),
        "comparison": (generate_comparison, (1200, 800)),
        "hierarchy": (generate_hierarchy, (1200, 800)),
        "cycle": (generate_cycle, (800, 800)),
    }
    if diagram_type not in generators:
        return _placeholder(size or (1200, 800), f"Unknown type: {diagram_type}")
    gen_func, default_size = generators[diagram_type]
    return gen_func(data, tokens, size=size or default_size)
