# DESIGN.md 集成计划（v2 SVG Pipeline 风格层升级）

- **日期**：2026-04-30
- **作者**：CodePothunter（与 Claude 共同起草）
- **状态**：v2.1，已纳入 eng-review P0 修订
- **关联**：`docs/superpowers/specs/2026-04-13-v2-svg-pipeline-design.md`，`edupptx/style_schema.py`，`edupptx/style_resolver.py`，`edupptx/postprocess/svg_validator.py`
- **参考**：`google-labs-code/design.md`（Apache 2.0，alpha 规范）

> **v2.1 修订摘要**（2026-04-30 eng-review 后）：4 个 P0 issue + 8 项加固已合入。
> 主要变化：A1 引入 `pptx-extensions:` YAML 命名空间承载 PPT 特有字段；
> A2 引入 `palette_hint` 双路径整合；A4 改用 `mistune` 解析；新增 LLM 全失败兜底；
> 新增 Layer 4 像素级 regression 测试；DRY 化 lint pair 枚举。
> 工作量从 8–10 天调整为 **11–13 天**。

---
## 0. 行动指南

1. 新建一个branch feature/visual-improvement-DESIGN-dot-MD
2. 增加一个APACHE 2.0 的LICENSE

## 1. Context

EduPPTX 当前 V2 SVG Pipeline 的 Phase 1b VisualPlanner 输出风格信息为内部 `VisualPlan` dataclass，问题：

1. **风格不可读**：用户无法看到每次会话生成的视觉决策，问题难以诊断
2. **风格不可改**：要调整风格只能改代码或重跑 LLM
3. **风格不可沉淀**：好的会话风格留不下来，无法跨会话复用
4. **缺少视觉自检**：现有 `tests/visual_qa.py` 只覆盖几何（重叠/溢出/空白），无对比度等视觉规则
5. **`palette.xxx` 引用语法**与业界 W3C Design Tokens / Google DESIGN.md 不一致，未来生态对接困难

Google Labs 在 2026 年开源的 `DESIGN.md` 规范（前身为 Stitch 内部约定）解决了"为 LLM agent 描述设计系统"的问题，提供：

- YAML front matter 机器可读 token + Markdown 8 段 prose 解释 why
- `{path.to.token}` 引用语法（W3C DTCG 兼容）
- 7 条 lint 规则（contrast-ratio、broken-ref、orphaned-tokens 等）
- 完整的 8 段结构（Overview / Colors / Typography / Layout / Elevation / Shapes / Components / Do's & Don'ts）

但 Stitch 范例**全是 app UI**（天气 / 宠物 / 音乐节），与教育 PPT 场景错配，因此**仅借鉴格式协议，不复用范例内容**。"分析真实优质教育 PPT 建范例库"作为长期目标占位（层面 5），本期不做。

## 1.5 现有系统对接面（v2.1 review 后补充）

**eng-review 发现计划没承认的现有功能**，本期必须正确整合或显式排除：

| 现有功能 | 位置 | 本期处理 |
|---|---|---|
| `palette_hint` 覆盖机制（template palette 强制覆盖 LLM 颜色输出） | `visual_planner.py:42-54` | **必须整合**（见 §4.2 修订） |
| `VisualPlan.background_color_bias` | `models.py:197` | 进 `pptx-extensions:` YAML |
| `VisualPlan.content_density: lecture\|review` | `models.py:201` | 进 `pptx-extensions:` YAML |
| `StyleRouting.style_name` 路由 | `models.py:209-223` | 不动 |
| 5 套 SVG 设计模板 `edu_academic/emerald/minimal/tech/warm.svg` | `edupptx/design/style_templates/` | **明确 NOT in scope**（独立系统） |
| 现有 `_apply_palette_hint` + `_parse_visual_plan` JSON fallback | `visual_planner.py:42, 114-129` | 复用 fallback 模式作为 LLM 全失败兜底 |

## 2. Goals & Non-goals

### Goals
1. 让 VisualPlanner 输出可读、可改、可 diff 的 `DESIGN.md` 产物
2. 现有 `styles/blue.json` / `emerald.json` 平滑迁移到 `.md` 格式
3. 引入对比度 / 引用完整性自检，提升 SVG 输出质量
4. 保留 PPT 特化的 EMU / preset / `prstGeom` 内部 IR 不变
5. 与 W3C Design Tokens 生态对接（`export --format dtcg`）的可能性留好接口

### Non-goals
- **不**采集真实 PPT 反推教育版范例库（层面 5，暂缓）
- **不**直接复用 Google Stitch 的任意 example 内容（领域错配）
- **不**实现 Tailwind / DTCG 双向 export（按需后置）
- **不**改动 SVG→DrawingML 转换逻辑（独立模块，与本计划无重叠）
- **不**重写整个 `style_schema.py`（保留为内部 IR，仅做 parser/serializer 桥接）
- **不**改造 `edupptx/design/style_templates/edu_*.svg` 5 套 SVG 设计模板（独立系统，未来另计划）
- **不**重构 `StyleRouting`（保留现有路由）

## 3. Architecture

### 3.1 数据流

```
[user input + topic]
       │
       ▼
┌────────────────────────────────────┐
│  Phase 1b: VisualPlanner (LLM)     │
│  prompt: 强制 8 段 markdown 输出   │ ← 层面 2
│  output: DESIGN.md 字符串          │ ← 层面 3
└────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────┐
│  edupptx/style/design_md.py        │ ← 层面 3 新模块
│  parse_design_md(str) → StyleSchema│
│  serialize_style(schema) → str     │
└────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────┐
│  edupptx/style_resolver.py (现有)  │
│  resolve_style() + lint hook       │ ← 层面 1 接入点
└────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────┐
│  edupptx/postprocess/style_linter  │ ← 层面 1 新模块
│  - contrast-ratio                  │
│  - broken-ref                      │
│  - (P2) orphaned-tokens            │
└────────────────────────────────────┘
       │
       ▼
[Phase 3 SVG generation 不变]
```

### 3.2 文件落点

```
output/session_xxx/
├── plan.json              # 内容架构（不变）
├── DESIGN.md              # ← 新增，视觉系统快照
├── materials/
└── slides/

styles/
├── blue.json              # 旧格式，loader 兼容兜底（一段时间后删除）
├── blue.md                # ← 新增，DESIGN.md 格式
├── emerald.json
└── emerald.md             # ← 新增

edupptx/
├── style/                 # ← 新建子包
│   └── design_md.py       # parser + serializer
├── postprocess/
│   └── style_linter.py    # ← 新增
└── ...（其余不变）
```

## 4. 五个层面

### 层面 1（P0）：自检规则移植

#### 范围
移植 Google `design.md` 的两条核心 lint 规则到 Python：
- `contrast-ratio`：WCAG AA 4.5:1
- `broken-ref`：palette / token 引用完整性

#### 实现

**新文件 `edupptx/postprocess/style_linter.py`**：

```python
from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning", "info"]

@dataclass
class Finding:
    severity: Severity
    rule: str
    path: str
    message: str

def wcag_relative_luminance(hex_color: str) -> float:
    rgb = [int(hex_color.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4 for c in rgb]
    return 0.2126*rgb[0] + 0.7152*rgb[1] + 0.0722*rgb[2]

def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = wcag_relative_luminance(fg_hex)
    l2 = wcag_relative_luminance(bg_hex)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

def _is_valid_hex(value: str | None) -> bool:
    """v2.1: 健壮性检查，None / 非 hex / rgba 都跳过不抛错"""
    if not value or not isinstance(value, str):
        return False
    if not value.startswith("#"):
        return False
    if len(value) not in (4, 7):  # #fff or #ffffff
        return False
    try:
        int(value.lstrip("#"), 16)
        return True
    except ValueError:
        return False


# v2.1 修订（Q1）：用 dataclasses introspection 自动生成 pair 矩阵，
# 替代硬编码 3 对 —— 新加 ResolvedStyle 颜色字段时自动纳入
def _build_contrast_pairs(style: ResolvedStyle) -> list[tuple[str, str, str]]:
    """枚举 (fg_name, fg_value, bg_value) 三元组。"""
    bg_candidates = {
        "card_fill": style.card_fill_color,
        "bg_overlay": style.bg_overlay_color,
    }
    fg_fields = {
        "heading":    style.heading_color,
        "body":       style.body_color,
        "card_title": style.card_title_color,
        "accent":     style.accent_color,
        "icon":       style.icon_color,
    }
    # 默认配对：每个 fg 对每个 bg
    return [(f"{fg_name}_on_{bg_name}", fg_val, bg_val)
            for fg_name, fg_val in fg_fields.items()
            for bg_name, bg_val in bg_candidates.items()]


def lint_resolved_style(style: ResolvedStyle) -> list[Finding]:
    findings = []
    for name, fg, bg in _build_contrast_pairs(style):
        if not (_is_valid_hex(fg) and _is_valid_hex(bg)):
            continue  # 非 hex 跳过（rgba/gradient/None）
        ratio = contrast_ratio(fg, bg)
        if ratio < 4.5:
            findings.append(Finding(
                severity="warning",
                rule="contrast-ratio",
                path=f"contrast.{name}",
                message=f"{fg} on {bg} = {ratio:.2f}:1 (< WCAG AA 4.5:1)",
            ))
    return findings

def lint_style_schema(schema: StyleSchema) -> list[Finding]:
    findings = []
    palette = schema.global_tokens.palette
    referenced = set()
    for field_name, value in [
        ("semantic.heading_color",  schema.semantic.heading_color),
        ("semantic.body_color",     schema.semantic.body_color),
        ("semantic.accent_color",   schema.semantic.accent_color),
        # ... 全部 palette.xxx 字段
    ]:
        if value.startswith("palette."):
            key = value[len("palette."):]
            referenced.add(key)
            if key not in palette:
                findings.append(Finding(
                    severity="error",
                    rule="broken-ref",
                    path=field_name,
                    message=f"reference '{value}' not in palette: {sorted(palette.keys())}",
                ))
    return findings
```

**接入点**：
- `edupptx/style_resolver.py::resolve_style()` 末尾调用 `lint_resolved_style` + `lint_style_schema`
- error 级 finding 抛 `StyleValidationError`
- warning 写 `loguru` warning，可通过 `EDUPPTX_LINT_STRICT=1` 升级为 error

**测试 `tests/test_style_linter.py`**（v2.1 critical 加固）：
- 现有 `emerald.json` 应 0 finding
- 现有 `blue.json` 应 0 finding
- 构造 `palette.text="#888888"` on `palette.card_fill="#FFFFFF"` 触发 contrast warning（实际 3.94:1）
- 构造 `body_color="palette.nonexistent"` 触发 broken-ref error
- **[critical]** 故意构造 `card_fill_color=None` / `card_fill_color="rgba(255,0,0,0.5)"` —— `lint_resolved_style` **必须不抛错，跳过该 pair**
- **[critical]** 短 hex `#fff` 必须正确处理（`_is_valid_hex` 接受 4 字符）
- 跑 `tests/visual_qa.py` 现存输出，统计违例基线

#### 工作量
1.5–2 天。核心算法 ~80 行（含 introspection + 健壮性），接入 + 测试 ~80 行。

#### 风险
低。新增模块，纯函数，无副作用。

---

### 层面 2（P0）：8 段结构改造 VisualPlanner prompt

#### 范围
重写 `edupptx/planning/prompts.py` 中 VisualPlanner 的 prompt，强制输出 8 段 Markdown + YAML front matter。**不引入外部范例，纯靠结构约束**。

#### v2.1 修订（A2）：`palette_hint` 双路径整合

现有 `_apply_palette_hint` 在 LLM 输出后强制覆盖颜色。新流程要保持这个语义但避免"prose 解释已被覆盖的旧色"问题：

```python
# edupptx/planning/visual_planner.py 改造后

def generate_design_md(draft, config, palette_hint=None) -> str:
    """返回 DESIGN.md 字符串。"""
    has_hint = palette_hint is not None

    if has_hint:
        # 路径 B：palette 已确定，LLM 只生成 prose 8 段
        prompt = build_prose_only_prompt(draft, palette_hint)
        prose_md = call_llm_with_retry(prompt, max_retries=1)
        if prose_md is None:
            return _fallback_design_md(palette_hint, draft)  # v2.1 兜底
        return _compose_design_md(palette_hint, prose_md)
    else:
        # 路径 A：LLM 自由生成完整 DESIGN.md
        prompt = build_full_prompt(draft)
        full_md = call_llm_with_retry(prompt, max_retries=1)
        if full_md is None or not _validate_8_sections(full_md):
            return _fallback_design_md(_default_palette(), draft)  # v2.1 兜底
        return full_md


def _fallback_design_md(palette, draft) -> str:
    """v2.1 critical：LLM 全失败时的兜底，不阻塞用户。
    用 palette（来自 palette_hint 或默认 emerald）生成最小可用 DESIGN.md。"""
    from edupptx.style.design_md import serialize_style
    schema = _palette_to_schema(palette)
    prose = _default_prose_for_topic(draft.meta.topic)  # 模板化 prose
    return serialize_style(schema, prose_sections=prose)
```

**两个 prompt 模板**：
- `build_full_prompt`：完整 8 段 + YAML 全集（无 hint 时）
- `build_prose_only_prompt`：只要 8 段 prose，YAML 用 `{palette_hint_block}` 占位

**fallback 路径**：当 LLM 调用全失败或 `_validate_8_sections` 不通过且重试也失败，调用 `_fallback_design_md` 返回最小可用 DESIGN.md，**不阻塞 Phase 3**。

#### Prompt 结构

```
你是教育演示文稿视觉设计师。请为主题「{topic}」（受众：{audience}）输出
DESIGN.md 草案，严格遵循以下格式：

---
name: <风格名，2-4 字中文>
audience: {audience}
domain: <学科领域>
colors:
  primary:        # hex
  accent:         # hex
  bg:             # hex（页面底色）
  card_fill:      # hex
  text:           # hex（深色，正文）
  text_secondary: # hex
  shadow:         # hex（不能是纯黑）
  icon:           # hex
typography:
  title:         { fontFamily: Noto Sans SC, fontSize: 38pt, fontWeight: 700 }
  card-title:    { fontFamily: Noto Sans SC, fontSize: 16pt, fontWeight: 600 }
  body:          { fontFamily: Noto Sans SC, fontSize: 12pt }
spacing:
  margin: comfortable | tight | spacious
  card_gap: tight | normal | wide
rounded:
  sm: 4px
  md: 8px
  lg: 16px
---

## Overview
2–3 句中文，描述情绪基调与受众契合。

## Colors
对每个颜色用一句话解释"为什么是这个色"，强调教育场景约束。

## Typography
说明字体策略。硬约束：
- body 不低于 12pt（投影后排可读）
- card-title 不低于 16pt
- 中文优先 Noto Sans SC

## Layout
1280×720 viewBox，Bento Grid。说明本主题适合哪几种布局。

## Elevation
深度通过什么表达：阴影 / 边框 / 底色对比。
教育 PPT 不建议大阴影（投影会脏）。

## Shapes
圆角策略 + 选择理由。

## Components
至少定义：card-knowledge / card-formula / card-quote / card-stat。
每个给 backgroundColor / textColor / rounded（用 token 引用如 {colors.primary}）。

## Do's and Don'ts
本风格的 3 条守门规则。

只输出 markdown 内容，不要任何 ``` 包裹，不要解释。
```

#### 校验
`visual_planner.py` 加：
- 段落数 = 8 检查（必须包含 8 个 `## ` 段头）
- YAML front matter 可解析（中文 fixture 必测）
- palette 至少有 `primary / accent / bg / card_fill / text` 5 个键
- 不通过则重试 1 次（max_retries=1，与现有 LLM client 一致）
- **v2.1**：重试也失败 → 走 `_fallback_design_md` 而非阻塞

#### 工作量
2 天。改 prompt + 双路径 + fallback + 测试。

#### 风险
中。
- LLM 对中文 + Markdown 混合输出的稳定性需要 1–2 轮微调（**保留旧 JSON prompt 路径作 v0.5.x 灰度回退开关**：`EDUPPTX_VISUAL_PLANNER_FORMAT=json|design_md`）

---

### 层面 3（P1）：DESIGN.md 解析 + 落盘产物

#### 范围
- 新模块 `edupptx/style/design_md.py`：`parse_design_md` 双向解析
- 修改 `agent.py` Phase 1b 落盘 + Phase 3 入口读取
- 用户编辑 DESIGN.md 后可重跑 Phase 3，不必改代码

#### v2.1 修订（A1 + A4 + Q3）：YAML schema 扩展

DESIGN.md YAML 增加 **`pptx-extensions:` 命名空间** 承载 PPT 特有字段（DESIGN.md 规范允许 unknown sections preserve）：

```yaml
---
schema_version: "1.0"      # v2.1 (Q3) forward compat
name: 科技蓝
audience: 中学生
domain: 信息技术
colors:
  primary: "#1E293B"
  # ... Google DESIGN.md 标准字段
typography:
  # ... 标准
spacing:
  margin: comfortable      # named intent
  card_gap: normal
rounded:
  sm: 4px
  md: 8px
  lg: 16px

# v2.1 (A1) PPT 特有字段，DESIGN.md 规范 unknown sections preserve 行为
pptx-extensions:
  decorations:
    title_underline: true
    content_panel: true
    panel_alpha_pct: 35
    footer_separator: true
    quote_bar: true
    section_diamond: true
    closing_circle: true
  card_shadow:
    blur_pt: 30
    dist_pt: 8
    color: "palette.shadow"   # 仍支持 ref
    alpha_pct: 14
  background:
    type: diagonal_gradient
    seed_extra: ""
  visual_plan:
    background_color_bias: ""
    content_density: lecture
---
```

**markdown 解析改用 `mistune` 而非 regex**（A4 修订）：避免 fenced code block 内的 `## ` 被误切。

#### API 设计

```python
# edupptx/style/design_md.py

import frontmatter  # python-frontmatter
import yaml

def parse_design_md(text: str) -> StyleSchema:
    """DESIGN.md 字符串 → StyleSchema（内部 IR 不变）"""
    post = frontmatter.loads(text)
    yaml_data = post.metadata
    body = post.content

    palette = dict(yaml_data.get("colors", {}))

    # 8 段 prose 拼回 description（保留语境给后续 Phase）
    sections = _parse_h2_sections(body)
    description = "\n\n".join(f"## {h}\n{c}" for h, c in sections.items())

    # spacing 别名 → named intent
    spacing = yaml_data.get("spacing", {})
    margin = spacing.get("margin", "comfortable")
    if margin not in ("comfortable", "tight", "spacious"):
        margin = "comfortable"  # 兜底

    return StyleSchema(
        meta=SchemaMeta(
            name=yaml_data.get("name", "unnamed"),
            description=description,
        ),
        global_tokens=GlobalTokens(
            palette=palette,
            fonts=_parse_fonts(yaml_data.get("typography", {})),
            background=yaml_data.get("background", {"type": "diagonal_gradient"}),
        ),
        semantic=_build_semantic(yaml_data, palette),
        layout=LayoutTokens(
            margin=margin,
            card_spacing=spacing.get("card_gap", "normal"),
            icon_size="large",
            content_density="standard",
        ),
        decorations=DecorationTokens(),  # 默认值
    )


def serialize_style(schema: StyleSchema, prose_sections: dict[str, str] | None = None) -> str:
    """StyleSchema → DESIGN.md 字符串（用于反向导出现有 .json 或保存最终态）"""
    yaml_data = {
        "name": schema.meta.name,
        "colors": dict(schema.global_tokens.palette),
        "typography": _serialize_typography(schema.semantic),
        "spacing": {"margin": schema.layout.margin, "card_gap": schema.layout.card_spacing},
        "rounded": {"sm": "4px", "md": "8px", "lg": "16px"},
    }
    fm = yaml.safe_dump(yaml_data, allow_unicode=True, sort_keys=False)
    body = prose_sections or _placeholder_prose()  # TODO 占位
    return f"---\n{fm}---\n\n" + "\n\n".join(f"## {h}\n{c}" for h, c in body.items())


def _parse_h2_sections(body: str) -> dict[str, str]:
    """v2.1 (A4): 用 mistune AST 切段，避免 regex 误切 fenced code block."""
    import mistune
    md = mistune.create_markdown(renderer=None)  # AST 模式
    tokens = md(body)
    sections: dict[str, str] = {}
    current_heading: str | None = None
    buffer: list[str] = []
    for tok in tokens:
        if tok["type"] == "heading" and tok["attrs"]["level"] == 2:
            if current_heading is not None:
                sections[current_heading] = "\n".join(buffer).strip()
            current_heading = _extract_text(tok)
            buffer = []
        elif current_heading is not None:
            buffer.append(_render_token(tok))
    if current_heading is not None:
        sections[current_heading] = "\n".join(buffer).strip()
    return sections
```

> **新增 dep**：`mistune>=3.0` —— 与 `python-frontmatter` 一起加进 `pyproject.toml`。
> 两者都是稳定常用库（mistune 4k+ stars），属 Layer 1。

#### 修改点
- `edupptx/agent.py` Phase 1b：
  - VisualPlanner 输出 `design_md_str: str`
  - 写入 `session_dir / "DESIGN.md"`
  - `parse_design_md(design_md_str)` → `StyleSchema`
  - `resolve_style(schema)` → `ResolvedStyle`（含层面 1 lint）
- `edupptx/agent.py` Phase 3：
  - 优先从 `session_dir / "DESIGN.md"` 读取（支持用户手改后重跑）
  - 读不到则走 Phase 1b 重新生成

#### 测试
```python
def test_idempotent_roundtrip():
    """parse → serialize → parse 应保持等价"""
    text1 = read_test_fixture("blue.md")
    schema1 = parse_design_md(text1)
    text2 = serialize_style(schema1, prose_sections=...)
    schema2 = parse_design_md(text2)
    assert schema1.global_tokens.palette == schema2.global_tokens.palette
    assert schema1.layout.margin == schema2.layout.margin

def test_partial_design_md_with_defaults():
    """用户简写也能解析"""
    text = "---\nname: minimal\ncolors:\n  primary: '#000'\n  bg: '#fff'\n  card_fill: '#fff'\n  text: '#000'\n  text_secondary: '#666'\n---\n\n## Overview\n..."
    schema = parse_design_md(text)
    assert schema.layout.margin == "comfortable"  # 默认值
```

#### 工作量
3–5 天。parser 100 行 + serializer 80 行 + 接入 + 测试 ~150 行。

#### 风险
中。
- YAML 嵌套引号 + 中文字符串可能踩坑（依赖 `python-frontmatter` 而不是手写）
- 新增 dependency `python-frontmatter`（轻量，~200 行）需在 `pyproject.toml` 加入

---

### 层面 4（P1）：styles 迁移到 .md

#### 范围
- 手工把 `styles/blue.json` / `emerald.json` 翻译为 `blue.md` / `emerald.md`
- prose 部分手写教育场景说明（含字号下限、阴影色策略、对比度考量等）
- CLI 加 `uv run edupptx styles convert <name>` 自动框架（YAML 自动 + prose 占位 TODO）
- loader 同时支持 `.md` 和 `.json`（先 `.md`，找不到再 `.json`）

#### 实现
- `edupptx/cli.py` 增加 `styles convert` 子命令
- `edupptx/style_schema.py::load_style(path)` 改为：
  ```python
  def load_style(path: Path) -> StyleSchema:
      if path.suffix == ".md":
          return parse_design_md(path.read_text(encoding="utf-8"))
      with open(path, "r", encoding="utf-8") as f:
          return StyleSchema.model_validate(json.load(f))
  ```
- agent.py 的 `--style blue` 解析时优先尝试 `styles/blue.md`，回退 `styles/blue.json`

#### v2.1 critical：baseline regression 测试

**新建 `tests/test_style_migration_regression.py`**：

```python
def test_blue_json_md_equivalent():
    """blue.json 加载得到的 ResolvedStyle 必须与 blue.md 像素级等价."""
    schema_json = load_style(Path("styles/blue.json"))
    schema_md   = load_style(Path("styles/blue.md"))
    rs_json = resolve_style(schema_json)
    rs_md   = resolve_style(schema_md)
    # 所有 EMU 字段、所有 hex 字段必须严格等
    for f in fields(ResolvedStyle):
        if f.name == "decorations":
            continue  # dataclass 比较走子字段
        assert getattr(rs_json, f.name) == getattr(rs_md, f.name), \
            f"Field {f.name} diverged: json={getattr(rs_json, f.name)} md={getattr(rs_md, f.name)}"
    assert rs_json.decorations == rs_md.decorations
```

**emerald 同样测**。任何不等价都阻断 Layer 4 上线。

#### 工作量
2.5–3 天（其中 prose 撰写 1.5 天 + regression 测试 + CLI 子命令 0.5 天）。

#### 风险
低。
- 主要是手工翻译质量，没有架构风险
- 旧 .json 保留 6 个月作为兜底，避免破坏现有用户
- regression 测试是迁移正确性的 hard gate

---

### 层面 5（暂缓）：教育版真实范例库

#### 启动条件（明确的 kill switch）
当且仅当满足**全部**以下：
- ✅ 层面 1–4 全部上线 ≥ 4 周
- ✅ 跑过 ≥ 20 个真实 session 并做过满意度评分
- ✅ 满意度 < 7/10 且**主要问题指向"风格不够多样 / 有审美短板"**
- ✅ 团队愿意投入 1–2 周做样本工程

否则不启动。

#### 未来设计（占位）
- 反推来源：TED-Ed / 3Blue1Brown / 人教版课件 / 优质学术 PPT
- 工具链：截图 → GPT-4V/Claude vision → 反推 8 段 DESIGN.md → 人工审核入库
- 落点：`styles/library/{category}/{name}.md`
- 接入：VisualPlanner few-shot 检索（embedding 或主题路由）

## 5. 依赖关系

```
层面 1 ─────────┐
              │ (独立)
层面 2 ─────────┤
              │
              ▼
            层面 3 ─────► 层面 4 ─────► 层面 5（暂缓）
```

层面 1 和 2 完全独立，可并行。层面 3 是结构枢纽，层面 4 / 5 都依赖它。

## 6. 时间线（v2.1 调整）

```
Week 1
├── Day 1–2  层面 1: contrast-ratio + broken-ref lint
│             含 None / 短 hex / rgba 健壮性测试
└── Day 3–4  层面 2: VisualPlanner 8 段 prompt + palette_hint 双路径 + fallback
              ↓ 跑 3 主题对比实验，验证质量提升

Week 2
├── Day 5–8  层面 3: DESIGN.md 解析（mistune+pptx-extensions）+ 落盘
│             含 idempotent roundtrip + 中文 fixture + decorations 测试
└── Day 9–11 层面 4: blue.md + emerald.md + regression 测试

Week 3+
└── 收集 ≥ 20 真实 session 满意度数据，再决定是否启动层面 5
```

**总工作量**：v2.0 是 8–10 天，v2.1 修订后 **11–13 天**（+3 天用于补 P0 issue）。

## 7. 验收标准

### 层面 1
- [ ] `tests/test_style_linter.py` 全过
- [ ] 现有 `emerald.json` / `blue.json` 0 finding
- [ ] 构造的 broken-ref / 低对比度 fixture 能正确触发
- [ ] 跑现有 3 主题对比 baseline，统计抓出多少历史 contrast bug

### 层面 2
- [ ] LLM 输出能稳定通过 8 段校验（连续 5 次 ≥ 4 次成功，重试 1 次后 100% 成功）
- [ ] 3 主题对比（光合作用 / 中国近代史 / 计算机网络）人工评估，至少 2 主题视觉质量 ≥ baseline

### 层面 3
- [ ] roundtrip idempotent 测试通过
- [ ] 手工编辑 DESIGN.md 后重跑能反映改动
- [ ] session 目录正确产出 DESIGN.md

### 层面 4
- [ ] `styles/blue.md` / `emerald.md` 可被 loader 正确加载
- [ ] `--style blue` 行为与之前 `blue.json` 完全等价（输出 SVG 像素级一致或差异 ≤ 5%）

## 8. 风险与缓解（v2.1 加强）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LLM 8 段输出不稳定 | 中 | 中 | 重试 1 次 + **`_fallback_design_md` 兜底（v2.1）** + `EDUPPTX_VISUAL_PLANNER_FORMAT=json` 灰度回退 |
| `python-frontmatter` 中文 YAML 踩坑 | 低 | 中 | 中文 fixture 必测；`yaml.safe_dump(allow_unicode=True)` |
| roundtrip 不 idempotent | 中 | 低 | **pptx-extensions 命名空间承载所有 PPT 字段（v2.1 A1）**；测试 fixture 覆盖 decorations |
| 用户手改 DESIGN.md 写错语法 | 中 | 低 | parser 抛错信息友好（指出 line + 期待 schema） |
| 层面 1 误报阻塞生成 | 低 | 中 | warning 不阻塞，仅 error 阻塞；提供 `EDUPPTX_LINT_STRICT` 开关 |
| 现有 .json 用户被破坏 | 低 | 高 | loader 双格式兼容 ≥ 6 个月 + **像素级 regression 测试（v2.1）**作 hard gate |
| `palette_hint` 整合错误导致颜色 / prose 不一致 | 中 | 中 | **双路径 prompt（v2.1 A2）**，hint 路径只生成 prose |
| LLM 全失败导致 Phase 3 阻塞 | 低 | 高 | **`_fallback_design_md` 兜底（v2.1）**用默认 palette + 模板 prose |
| `mistune` 不可用时降级 | 极低 | 低 | 退回 regex 切段（带警告） |
| `ResolvedStyle` 加新颜色字段后 lint 漏检 | 中 | 低 | **introspection 自动配对（v2.1 Q1）**，新字段自动纳入 |

## 9. Open Questions

1. `DESIGN.md` 中 prose 是用中文还是英文？倾向**中文**，与现有 LLM 输出一致，但 W3C 互操作时可能要双语
2. `spacing.margin` 用 named intent (`comfortable`) 还是真实 px (`24px`)？倾向**保留 named intent**，简化 LLM 输出，仅 `style_resolver.py` 做 EMU 转换
3. 是否在 layer 1 顺手实现 `orphaned-tokens`？倾向 **P2 推迟**，先验证 contrast/broken-ref 价值
4. `styles/blue.json` 何时正式废弃？建议 v0.5.0 标记 deprecated，v0.6.0 移除
5. 层面 5 的"教育满意度评分"由谁/怎么打？需要先设计评分量表

## 10. 不做的事（明确边界）

- 不做 Tailwind / DTCG export（按需后置）
- 不做 DESIGN.md diff CLI（用 `git diff` 即可）
- 不做 components 段的完整组件级 token 生成（教育 PPT 没有 button-hover 这种交互态）
- 不做 SVG 中每个 text 元素的"实际渲染色 vs 父背景"contrast 检查（成本高，先做 ResolvedStyle 级别）

---

**下一步**：v2.1 修订已完成，可启动**层面 1（contrast-ratio lint）**实现。

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | not run |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | skipped (auto mode) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | **CLEAR (issues addressed)** | 4 P0 + 3 P1 issues, all merged into v2.1 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | n/a (backend) |
| DX Review | `/plan-devex-review` | DX gaps | 0 | — | n/a |

**KEY FINDINGS（v2.1 已修复）**：
- A1 roundtrip 不 idempotent → 引入 `pptx-extensions:` YAML 命名空间
- A2 `palette_hint` 整合缺失 → 双路径 prompt（free / hint-only-prose）
- A4 regex 切段不稳健 → 改用 `mistune` AST 解析
- LLM 全失败无 fallback → `_fallback_design_md` 兜底
- Layer 1 None color crash → `_is_valid_hex` 健壮性 + critical test
- Layer 4 缺 baseline regression → `test_blue_json_md_equivalent` 像素级等价
- Q1 lint pair 硬编码 → `_build_contrast_pairs` introspection
- Q3 缺 schema_version → YAML 加 `schema_version: "1.0"`

**UNRESOLVED**: 0 (所有 P0/P1 已合入 v2.1)
**VERDICT**: **ENG CLEARED — ready to implement Layer 1**
