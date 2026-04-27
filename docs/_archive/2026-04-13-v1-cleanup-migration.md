# v1 代码清理迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove v1 pipeline code (design_system.py, layout_engine.py) and migrate all consumers to v2 ResolvedStyle.

**Architecture:** All color/font/spacing lookups converge on `styles/*.json` → `StyleSchema` → `ResolvedStyle`. No more parallel `DesignTokens` path.

**Tech Stack:** Python 3.10+, python-pptx, Pydantic, Pillow, pytest

---

## File Structure

| Action | File | Responsibility after change |
|--------|------|-----------------------------|
| Delete | `edupptx/design_system.py` | — |
| Delete | `edupptx/layout_engine.py` | — |
| Delete | `tests/test_layout_engine.py` | — (v2 tests in `test_layout_engine.py` already exist, named same) |
| Modify | `edupptx/backgrounds.py:16,94-99,110-119,224-235` | Use `ResolvedStyle` instead of `DesignTokens` |
| Modify | `edupptx/diagram_native.py:10-11,103-353` | Local `SlotPosition`, use `ResolvedStyle` |
| Modify | `edupptx/agent.py:17,107,125,277-328` | Drop `get_design_tokens`, use `resolved_style` |
| Modify | `edupptx/cli.py:110-115` | Scan `styles/` dir instead of `PALETTES` dict |
| Modify | `tests/conftest.py:6,11-13` | Fixture returns `ResolvedStyle` |
| Modify | `tests/test_renderer.py:11,56-57,62-63,91-92,96-98` | Use resolved style for background generation |

---

### Task 1: Migrate `backgrounds.py` from DesignTokens to ResolvedStyle

**Files:**
- Modify: `edupptx/backgrounds.py:16,94-99,110-119,224-235`
- Test: `tests/test_renderer.py` (existing tests cover background generation)

- [ ] **Step 1: Update import and `generate_background` signature**

Replace the import and function signature in `edupptx/backgrounds.py`:

```python
# OLD (line 16):
from edupptx.design_system import DesignTokens

# NEW:
from edupptx.style_schema import ResolvedStyle
```

Change `generate_background` (line 94-99):

```python
# OLD:
def generate_background(
    design: DesignTokens,
    style: str = "diagonal_gradient",
    output_dir: Path | None = None,
    seed_extra: str = "",
) -> Path:

# NEW:
def generate_background(
    resolved: ResolvedStyle,
    style: str = "diagonal_gradient",
    output_dir: Path | None = None,
    seed_extra: str = "",
) -> Path:
```

- [ ] **Step 2: Update field references in `generate_background`**

Change lines 110-119:

```python
# OLD:
seed = hashlib.md5(f"{style}-{design.accent}-{seed_extra}".encode()).hexdigest()[:8]
...
base = _hex_to_rgb(design.bg_overlay)
accent = _hex_to_rgb(design.accent_light)
highlight = _hex_to_rgb(design.accent)

# NEW:
seed = hashlib.md5(f"{style}-{resolved.accent_color}-{seed_extra}".encode()).hexdigest()[:8]
...
base = _hex_to_rgb(resolved.bg_overlay_color)
accent = _hex_to_rgb(resolved.palette.get("accent_light", "#E0E0E0"))
highlight = _hex_to_rgb(resolved.accent_color)
```

- [ ] **Step 3: Update `generate_ai_background` signature and body**

Change line 224-235:

```python
# OLD:
def generate_ai_background(topic: str, design: DesignTokens, config: Config) -> Path | None:
    ...
    f"Soft {design.accent} tones, clean, professional, suitable as a "

# NEW:
def generate_ai_background(topic: str, resolved: ResolvedStyle, config: Config) -> Path | None:
    ...
    f"Soft {resolved.accent_color} tones, clean, professional, suitable as a "
```

- [ ] **Step 4: Run existing background tests to verify**

Run: `uv run pytest tests/test_renderer.py -v -x`

Expected: Tests will FAIL because `test_renderer.py` still passes `DesignTokens`. That's expected — we fix callers in later tasks.

- [ ] **Step 5: Commit**

```bash
git add edupptx/backgrounds.py
git commit -m "♻️【重构】：backgrounds.py 从 DesignTokens 迁移到 ResolvedStyle"
```

---

### Task 2: Migrate `diagram_native.py` — local SlotPosition + ResolvedStyle

**Files:**
- Modify: `edupptx/diagram_native.py:10-11,103-353`

- [ ] **Step 1: Replace imports with local SlotPosition and ResolvedStyle**

```python
# OLD (lines 10-11):
from edupptx.design_system import DesignTokens
from edupptx.layout_engine import SlotPosition

# NEW:
from dataclasses import dataclass
from edupptx.style_schema import ResolvedStyle


@dataclass
class SlotPosition:
    """A rectangular region on the slide (EMU coordinates)."""
    x: int
    y: int
    width: int
    height: int
```

- [ ] **Step 2: Update all renderer function signatures**

Change `DesignTokens` → `ResolvedStyle` in all 5 draw functions + dispatcher:

```python
# _draw_flowchart (line 103):
def _draw_flowchart(slide, data: dict, slot: SlotPosition, style: ResolvedStyle):

# _draw_timeline (line 166):
def _draw_timeline(slide, data: dict, slot: SlotPosition, style: ResolvedStyle):

# _draw_comparison (line 205):
def _draw_comparison(slide, data: dict, slot: SlotPosition, style: ResolvedStyle):

# _draw_hierarchy (line 235):
def _draw_hierarchy(slide, data: dict, slot: SlotPosition, style: ResolvedStyle):

# _draw_cycle (line 285):
def _draw_cycle(slide, data: dict, slot: SlotPosition, style: ResolvedStyle):

# draw_diagram_on_slide (line 345-352):
def draw_diagram_on_slide(
    slide, diagram_type: str, data: dict,
    slot: SlotPosition, style: ResolvedStyle,
) -> None:
    renderer = _RENDERERS.get(diagram_type)
    if renderer:
        renderer(slide, data, slot, style)
```

- [ ] **Step 3: Update field references in `_draw_flowchart`**

```python
# OLD (lines 127-130):
_add_rounded_box(slide, x, y, box_w, box_h,
                 design.accent_light, design.accent)
_add_text_shape(slide, node["label"],
                x, y, box_w, box_h,
                font_size=12, color=design.text_primary, bold=True)
# ... and line 139:
_add_line(slide, ..., design.accent, width_pt=2)

# NEW:
_add_rounded_box(slide, x, y, box_w, box_h,
                 style.palette.get("accent_light", "#E0E0E0"), style.accent_color)
_add_text_shape(slide, node["label"],
                x, y, box_w, box_h,
                font_size=12, color=style.heading_color, bold=True)
# ... and arrows:
_add_line(slide, ..., style.accent_color, width_pt=2)
```

Apply the same pattern to the LR branch (lines 150-163).

- [ ] **Step 4: Update field references in `_draw_timeline`**

```python
# Lines 180, 188, 193, 202 — replace:
# design.accent → style.accent_color
# design.text_secondary → style.body_color
```

Full replacements in `_draw_timeline`:
- `design.accent` → `style.accent_color` (lines 180, 188, 193)
- `design.text_secondary` → `style.body_color` (line 202)

- [ ] **Step 5: Update field references in `_draw_comparison`**

```python
# Lines 220-221, 223, 231 — replace:
# design.accent → style.accent_color
# design.text_primary → style.heading_color
```

Full replacements in `_draw_comparison`:
- `design.accent` (lines 220, 221) → `style.accent_color`
- `design.text_primary` (line 231) → `style.heading_color`

- [ ] **Step 6: Update field references in `_draw_hierarchy`**

```python
# Lines 253-258, 277 — replace:
# design.accent_light → style.palette.get("accent_light", "#E0E0E0")
# design.accent → style.accent_color
# design.text_primary → style.heading_color
```

- [ ] **Step 7: Update field references in `_draw_cycle`**

```python
# Lines 309-311, 313, 331 — replace:
# design.accent_light → style.palette.get("accent_light", "#E0E0E0")
# design.accent → style.accent_color
# design.text_primary → style.heading_color
```

- [ ] **Step 8: Verify module imports cleanly**

Run: `uv run python -c "from edupptx.diagram_native import draw_diagram_on_slide; print('OK')"`

Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add edupptx/diagram_native.py
git commit -m "♻️【重构】：diagram_native.py 迁移到 ResolvedStyle + 本地 SlotPosition"
```

---

### Task 3: Migrate `agent.py` — drop DesignTokens, use resolved_style

**Files:**
- Modify: `edupptx/agent.py:17,107,125,277-328`

- [ ] **Step 1: Update imports**

```python
# OLD (line 17):
from edupptx.design_system import DesignTokens, get_design_tokens

# NEW (delete the line entirely — no replacement import needed, ResolvedStyle is already
# imported via resolve_style in Phase 5; we'll move it to top-level):
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import ResolvedStyle
```

Also add `from edupptx.style_resolver import resolve_style` at the top level (line ~29) and remove the late import at line 151.

- [ ] **Step 2: Restructure Phase 2 — compute resolved_style early**

Replace lines 106-117:

```python
# OLD:
        # Phase 2: Style negotiation — LLM interprets NL style requirements
        design = get_design_tokens(plan.palette)
        style_path = Path(__file__).parent.parent / "styles" / f"{plan.palette}.json"
        if not style_path.exists():
            style_path = Path(__file__).parent.parent / "styles" / "emerald.json"
        base_schema = load_style(style_path)

        if requirements.strip():
            session.log_step("style", f"Negotiating style from: {requirements[:80]}")
            negotiated_schema = negotiate_style(self.llm, base_schema, requirements)
        else:
            negotiated_schema = base_schema

# NEW:
        # Phase 2: Style negotiation — LLM interprets NL style requirements
        style_path = Path(__file__).parent.parent / "styles" / f"{plan.palette}.json"
        if not style_path.exists():
            style_path = Path(__file__).parent.parent / "styles" / "emerald.json"
        base_schema = load_style(style_path)

        if requirements.strip():
            session.log_step("style", f"Negotiating style from: {requirements[:80]}")
            negotiated_schema = negotiate_style(self.llm, base_schema, requirements)
        else:
            negotiated_schema = base_schema

        resolved_style = resolve_style(negotiated_schema)
```

- [ ] **Step 3: Update Phase 4 call — pass resolved_style**

Change line 125:

```python
# OLD:
        slide_assets = self._execute_materials(plan, design, session)

# NEW:
        slide_assets = self._execute_materials(plan, resolved_style, session)
```

- [ ] **Step 4: Simplify Phase 5 — reuse resolved_style**

Replace lines 148-154:

```python
# OLD:
        from edupptx.layout_resolver import resolve_layout
        from edupptx.pptx_writer import PptxWriter
        from edupptx.style_resolver import resolve_style
        from edupptx.validator import validate_slides

        resolved_style = resolve_style(negotiated_schema)

# NEW:
        from edupptx.layout_resolver import resolve_layout
        from edupptx.pptx_writer import PptxWriter
        from edupptx.validator import validate_slides
```

(resolved_style is already computed in Phase 2)

- [ ] **Step 5: Update `_execute_materials` signature**

Change line 277-278:

```python
# OLD:
    def _execute_materials(
        self, plan: PresentationPlan, design: DesignTokens, session: Session,

# NEW:
    def _execute_materials(
        self, plan: PresentationPlan, style: ResolvedStyle, session: Session,
```

- [ ] **Step 6: Update `_gen_bg` to pass style**

Change line 301:

```python
# OLD:
            bg_path = generate_background(design, style, seed_extra=f"slide{i}")

# NEW:
            bg_path = generate_background(style, bg_style, seed_extra=f"slide{i}")
```

Note: the local variable `style` (background style string) shadows the outer `style` (ResolvedStyle). Rename the background style string to `bg_style` in `_gen_bg`:

```python
        def _gen_bg(i: int, slide: SlideContent) -> tuple[tuple[str, int], Path]:
            bg_style = "diagonal_gradient"
            if slide.bg_action and slide.bg_action.style:
                bg_style = slide.bg_action.style
            tags = slide.bg_action.tags if slide.bg_action else []

            cached = self.library.search(
                tags + [bg_style], type="background", palette=plan.palette,
            )
            if cached:
                lib_path = self.library.dir / cached[0].path
                if lib_path.exists():
                    dest = session.dir / "materials" / lib_path.name
                    shutil.copy2(lib_path, dest)
                    logger.debug("Reused cached background for slide {}: {}", i, cached[0].id)
                    return ("bg", i), lib_path

            bg_path = generate_background(style, bg_style, seed_extra=f"slide{i}")
            self.library.add(
                bg_path, "background", tags + [bg_style], plan.palette, "programmatic",
                f"Slide {i}: {slide.title}",
            )
            dest = session.dir / "materials" / bg_path.name
            shutil.copy2(bg_path, dest)
            return ("bg", i), bg_path
```

- [ ] **Step 7: Update `_make_placeholder` to use ResolvedStyle**

Change lines 310-328:

```python
# OLD:
        def _make_placeholder(desc: str, design: DesignTokens) -> Path:
            ...
            img = Image.new("RGB", (1024, 768), tuple(int(design.accent_light.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)))
            ...
            text_color = tuple(int(design.text_secondary.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))

# NEW:
        def _make_placeholder(desc: str, rs: ResolvedStyle) -> Path:
            from PIL import Image, ImageDraw, ImageFont
            accent_light = rs.palette.get("accent_light", "#E0E0E0")
            img = Image.new("RGB", (1024, 768), tuple(int(accent_light.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)))
            draw = ImageDraw.Draw(img)
            text = desc[:80] + ("..." if len(desc) > 80 else "")
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            except (OSError, IOError):
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (1024 - tw) // 2
            y = (768 - th) // 2
            text_color = tuple(int(rs.body_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
            draw.text((x, y), text, fill=text_color, font=font)
            path = Path(tempfile.mktemp(suffix=".png"))
            img.save(path, "PNG")
            return path
```

Note: `_make_placeholder` is defined but never actually called in the current code (the illustration path either succeeds or returns `None`). We still migrate it for completeness.

- [ ] **Step 8: Verify module imports cleanly**

Run: `uv run python -c "from edupptx.agent import PPTXAgent; print('OK')"`

Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add edupptx/agent.py
git commit -m "♻️【重构】：agent.py 从 DesignTokens 迁移到 ResolvedStyle"
```

---

### Task 4: Migrate CLI `palettes` command

**Files:**
- Modify: `edupptx/cli.py:110-115`

- [ ] **Step 1: Rewrite `palettes` command to scan styles/ directory**

Replace lines 110-115:

```python
# OLD:
@main.command()
def palettes():
    """List available color palettes."""
    from edupptx.design_system import PALETTES
    for name, tokens in PALETTES.items():
        click.echo(f"  {name:10s}  accent={tokens.accent}  overlay={tokens.bg_overlay}")

# NEW:
@main.command()
def palettes():
    """List available style themes."""
    from edupptx.style_schema import load_style
    styles_dir = Path(__file__).parent.parent / "styles"
    if not styles_dir.exists():
        click.echo("No styles directory found.")
        return
    for f in sorted(styles_dir.glob("*.json")):
        schema = load_style(f)
        accent = schema.global_tokens.palette.get("accent", "?")
        bg = schema.global_tokens.palette.get("bg", "?")
        click.echo(f"  {f.stem:10s}  accent={accent}  bg={bg}  — {schema.meta.description}")
```

- [ ] **Step 2: Verify CLI works**

Run: `uv run edupptx palettes`

Expected: lists emerald and blue with accent colors and descriptions.

- [ ] **Step 3: Commit**

```bash
git add edupptx/cli.py
git commit -m "♻️【重构】：CLI palettes 命令改为扫描 styles/ 目录"
```

---

### Task 5: Migrate test fixtures and test_renderer.py

**Files:**
- Modify: `tests/conftest.py:6,11-13`
- Modify: `tests/test_renderer.py:11,56-57,62-63,91-92,96-98`

- [ ] **Step 1: Update `conftest.py` fixture**

```python
# OLD (lines 6, 11-13):
from edupptx.design_system import get_design_tokens

@pytest.fixture
def design_emerald():
    return get_design_tokens("emerald")

# NEW:
from pathlib import Path
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import load_style

STYLES_DIR = Path(__file__).parent.parent / "styles"

@pytest.fixture
def resolved_emerald():
    return resolve_style(load_style(STYLES_DIR / "emerald.json"))
```

- [ ] **Step 2: Update `test_renderer.py` imports and usages**

Replace line 11:

```python
# OLD:
from edupptx.design_system import get_design_tokens

# NEW:
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import load_style
```

Replace `test_renderer_creates_valid_pptx` (lines 53-74):

```python
def test_renderer_creates_valid_pptx():
    """Render a simple plan and verify the output is a valid PPTX."""
    plan = _make_simple_plan()
    resolved = resolve_style(load_style(STYLES_DIR / "emerald.json"))

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir) / "cache"
        styles = ["diagonal_gradient", "radial_gradient", "geometric_circles"]
        backgrounds = [
            generate_background(resolved, styles[i % len(styles)], cache_dir)
            for i in range(len(plan.slides))
        ]

        out_path = Path(tmpdir) / "test.pptx"
        render_with_schema(plan, STYLES_DIR / "emerald.json",
                           bg_paths=backgrounds, output_path=out_path)

        assert out_path.exists()
        assert out_path.stat().st_size > 10000

        prs = Presentation(str(out_path))
        assert len(prs.slides) == 3
```

Replace `test_renderer_speaker_notes` (lines 88-109):

```python
def test_renderer_speaker_notes():
    """Verify speaker notes are embedded."""
    plan = _make_simple_plan()
    resolved = resolve_style(load_style(STYLES_DIR / "emerald.json"))

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir) / "cache"
        styles = ["diagonal_gradient", "radial_gradient", "geometric_circles"]
        backgrounds = [
            generate_background(resolved, styles[i % len(styles)], cache_dir)
            for i in range(len(plan.slides))
        ]

        out_path = Path(tmpdir) / "test.pptx"
        render_with_schema(plan, STYLES_DIR / "emerald.json",
                           bg_paths=backgrounds, output_path=out_path)

        prs = Presentation(str(out_path))
        slide = prs.slides[0]
        assert slide.has_notes_slide
        notes_text = slide.notes_slide.notes_text_frame.text
        assert "Test speaker notes" in notes_text
```

- [ ] **Step 3: Run all renderer tests**

Run: `uv run pytest tests/test_renderer.py -v`

Expected: All 5 tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_renderer.py
git commit -m "♻️【重构】：测试 fixture 从 DesignTokens 迁移到 ResolvedStyle"
```

---

### Task 6: Delete v1 files and their tests

**Files:**
- Delete: `edupptx/design_system.py`
- Delete: `edupptx/layout_engine.py`
- Delete: `tests/test_layout_engine.py` — **Wait.** This file tests v2 layout_resolver, NOT v1 layout_engine. Its name is misleading but its content imports from `edupptx.layout_resolver`. Do NOT delete it.

- [ ] **Step 1: Verify no remaining imports of v1 modules**

Run: `uv run python -c "
import ast, pathlib
for p in pathlib.Path('edupptx').rglob('*.py'):
    tree = ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ('edupptx.design_system', 'edupptx.layout_engine'):
            print(f'{p}:{node.lineno}: {node.module}')
for p in pathlib.Path('tests').rglob('*.py'):
    tree = ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ('edupptx.design_system', 'edupptx.layout_engine'):
            print(f'{p}:{node.lineno}: {node.module}')
print('DONE')
"`

Expected: Only `DONE` — no remaining imports.

If any imports remain, fix them before proceeding.

- [ ] **Step 2: Delete v1 files**

```bash
rm edupptx/design_system.py edupptx/layout_engine.py
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`

Expected: All tests pass. Test count should be ~91 (112 minus the 21 v1 layout_engine tests that were implicitly deleted... wait — actually `test_layout_engine.py` tests the v2 resolver, so it should still pass).

Actually, re-read: `test_layout_engine.py` imports from `edupptx.layout_resolver`, NOT from `edupptx.layout_engine`. The file stays. All 112 tests should still pass minus 0 deleted = 112 tests.

Wait — the old v1 tests. Let me re-check. The earlier test run showed `test_layout_engine.py` has 15 tests and `test_renderer.py` has 6 tests. But `test_layout_engine.py` imports from `edupptx.layout_resolver`, so those 15 tests are v2. The 112 total should remain 112.

Run: `uv run pytest tests/ -v`

Expected: 112 tests pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "🗑️【清理】：删除 v1 遗留文件 design_system.py + layout_engine.py"
```

---

### Task 7: Update documentation references

**Files:**
- Modify: `CLAUDE.md` (update directory structure, remove v1 references)
- Modify: `README.md` (update code examples if they reference v1)

- [ ] **Step 1: Update CLAUDE.md directory structure**

In the directory structure section, remove `design_system.py` and `layout_engine.py` entries. Update their descriptions:

```
# Remove these two lines:
  design_system.py        # 6 套配色方案 (v1)
  layout_engine.py        # 10 种槽位模板 → EMU 坐标 (v1)
```

Also remove `renderer.py` if still listed (it was already deleted).

- [ ] **Step 2: Update CLAUDE.md test count if needed**

Update the test count line to match current count (verify after running tests).

- [ ] **Step 3: Update README.md code examples**

Find and replace any `from edupptx.design_system import ...` or `from edupptx.renderer import ...` examples with v2 equivalents:

```python
# OLD:
from edupptx.design_system import get_design_tokens
from edupptx.renderer import PresentationRenderer

# NEW:
from edupptx.style_schema import load_style
from edupptx.style_resolver import resolve_style
from edupptx.pipeline_v2 import render_with_schema
```

- [ ] **Step 4: Run full test suite one more time**

Run: `uv run pytest tests/ -v`

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "📝【文档】：更新文档删除 v1 引用 + 更新目录结构"
```
