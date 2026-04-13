# v1 代码清理：全量迁移到 v2 Schema 管线

**Date:** 2026-04-13
**Status:** Approved

## 背景

v2 三层 Schema 管线已完全替代 v1 渲染管线。CLI 和 agent 都已使用 v2，但 v1 的 `design_system.py` 和 `layout_engine.py` 仍被多处引用。本次清理将消除双轨颜色定义，让 `styles/*.json` 成为唯一视觉配置源。

## 删除文件

| 文件 | 行数 | 原因 |
|------|------|------|
| `edupptx/layout_engine.py` | 476 | v2 `layout_resolver.py` 已完全替代 |
| `edupptx/design_system.py` | 116 | v2 `style_schema.py` + `style_resolver.py` 已替代 |
| `tests/test_layout_engine.py` | ~90 | v2 layout 测试已覆盖等价场景 |

## 依赖迁移

### 1. `backgrounds.py` — DesignTokens → ResolvedStyle

函数签名从 `generate_background(design: DesignTokens, ...)` 改为 `generate_background(style: ResolvedStyle, ...)`。

字段映射：
- `design.bg_overlay` → `style.bg_overlay_color`
- `design.accent_light` → `style.palette["accent_light"]`
- `design.accent` → `style.accent_color`

同样，`generate_ai_background(topic, design, config)` → `generate_ai_background(topic, style, config)`。

### 2. `agent.py` — 删除 DesignTokens，提前 resolve style

当前流程：
```
Phase 2: design = get_design_tokens(palette)  # v1
         negotiated_schema = negotiate_style(...)
Phase 4: _execute_materials(plan, design, ...)  # 用 v1 DesignTokens
Phase 5: resolved_style = resolve_style(negotiated_schema)  # v2
```

改为：
```
Phase 2: negotiated_schema = negotiate_style(...)
         resolved_style = resolve_style(negotiated_schema)  # 提前到这里
Phase 4: _execute_materials(plan, resolved_style, ...)  # 用 v2 ResolvedStyle
Phase 5: # resolved_style 已有，直接用
```

`_execute_materials()` 和 `_make_placeholder()` 参数类型改为 `ResolvedStyle`。

placeholder 字段映射：
- `design.accent_light` → `style.palette["accent_light"]`
- `design.text_secondary` → `style.body_color`

### 3. `diagram_native.py` — SlotPosition 本地化 + DesignTokens → ResolvedStyle

`SlotPosition` 是一个简单的 `(x, y, width, height)` dataclass。在 `diagram_native.py` 内部重新定义：

```python
@dataclass
class SlotPosition:
    x: int
    y: int
    width: int
    height: int
```

DesignTokens 的字段使用映射：
- `design.accent` → `style.accent_color`
- `design.text_primary` → `style.heading_color`
- `design.text_secondary` → `style.body_color`
- `design.accent_light` → `style.palette["accent_light"]`
- `design.card_bg` → `style.card_fill_color`
- `design.font_primary` → `style.heading_font.family`
- `design.font_fallback` → `style.heading_font.fallback`

### 4. CLI `palettes` 命令 → 扫描 styles/ 目录

从：
```python
from edupptx.design_system import PALETTES
for name, tokens in PALETTES.items():
    click.echo(f"  {name}  accent={tokens.accent}")
```

改为：
```python
from edupptx.style_schema import load_style
styles_dir = Path(__file__).parent.parent / "styles"
for f in sorted(styles_dir.glob("*.json")):
    schema = load_style(f)
    accent = schema.global_tokens.palette.get("accent", "?")
    click.echo(f"  {f.stem}  accent={accent}  — {schema.meta.description}")
```

### 5. 测试 fixture — conftest.py

`design_emerald()` fixture 改为返回 `ResolvedStyle`：

```python
from edupptx.style_schema import load_style
from edupptx.style_resolver import resolve_style

@pytest.fixture
def resolved_emerald():
    schema = load_style(Path(__file__).parent.parent / "styles" / "emerald.json")
    return resolve_style(schema)
```

所有引用 `design_emerald` 的测试改为用 `resolved_emerald`。

### 6. `test_renderer.py` — 更新 import

删除 `from edupptx.design_system import get_design_tokens`，改用 resolved style。

## 不变的部分

- `style_schema.py`, `style_resolver.py`, `layout_resolver.py`, `validator.py`, `pptx_writer.py` — 不需要修改
- `pipeline_v2.py` — 不需要修改
- `styles/*.json` — 不需要修改
- `content_planner.py` — 不引用 v1 代码
- v2 测试文件 — 不需要修改

## 测试策略

- 修改后运行 `uv run pytest tests/ -v`，确保所有测试通过
- 被删除的 21 个 v1 测试不需要替代（v2 已有 49+ 个等价测试）
- 重点验证 `test_renderer.py` 的背景生成测试仍然正常

## 风险

- **低风险**: 所有映射都是直接的字段重命名，无逻辑变更
- `diagram_native.py` 当前没有被 agent 执行路径调用（图表生成未接入），所以改它不影响运行时行为
