# DESIGN.md → Phase 3 真消费计划（v3.2）

- **日期**：2026-04-30
- **作者**：CodePothunter（与 Claude 共同起草）
- **状态**：v1.0，全稿一次性实施
- **关联**：`docs/_archive/2026-04-30-design-md-integration.md`（v3.1，DESIGN.md 落盘）
- **范围**：让 Phase 3 SVG 生成真正读取并贯彻 DESIGN.md 中的视觉契约

---

## 1. Context

v3.1（已合并 PR #2）让 Phase 1b 写出 DESIGN.md 视觉系统快照，但 Phase 3 SVG 生成仍由 `VisualPlan` JSON 驱动，DESIGN.md 中更细的 components / Do's & Don'ts / typography 约束不参与 SVG 生成。结果：

1. DESIGN.md 像"美丽的便签"——LLM 写得很好，但下游不读
2. 用户 `--review` 后编辑 DESIGN.md 不生效（已在 v3.1 标注为路线图项）
3. style_linter 只检查 `ResolvedStyle`，DESIGN.md 中的 broken-ref 漏网

实测一次 v3.1 跑光合作用，DESIGN.md 输出包含具体约束如"≤3 核心知识点/页"、`{colors.primary}10` token 引用、card-knowledge/formula/quote/stat 四组件定义——这些**全部没进 Phase 3 prompt**。

## 2. Goals & Non-goals

### Goals
1. Phase 3 SVG generator 在 system prompt 中包含 DESIGN.md 中可执行的视觉约束（components、do's&don'ts、typography、elevation、shapes）
2. `{colors.xxx}` token 引用在注入 prompt 前解析为实际 hex（LLM 不必学 token 语法）
3. `EDUPPTX_VISUAL_PLANNER_FORMAT=design_md` 端到端连通——不仅写文件，还驱动 SVG
4. DESIGN.md 加载后立即过 `resolve_style + lint`，broken-ref 等错误 fail-fast 阻止 Phase 3
5. 关闭 env 时完全不影响旧路径（135 测试 + 实跑无回归）

### Non-goals
- **不**重构整个 svg_generator.py 抽象——保持 `build_svg_system_prompt` 签名向后兼容
- **不**修改 DESIGN.md 自身格式（Layer 3a 的 schema 已稳定）
- **不**改 Phase 2 背景生成路径（仍由 `VisualPlan.background_prompt` 驱动）
- **不**实现 `run_from_plan` 中"用户编辑 DESIGN.md → 重跑 Phase 3"的全功能闭环——仍是 follow-up；本期只保证 `gen` 路径打通

## 3. Architecture

### 3.1 数据流变化

```
[Phase 1b]
  ├─ generate_visual_plan → draft.visual (legacy, 不变)
  └─ generate_design_md → DESIGN.md str  ← v3.1
        │
        ▼
  session.dir / DESIGN.md (写入)
        │
        ▼
[Phase 1b 末尾，新增]
  parse_design_md(DESIGN.md str) → StyleSchema
  resolve_style(schema) → ResolvedStyle  ← v3.2 lint 在此触发
        │
        ▼
[Phase 3, 新增 design_md_str / resolved_style 入参]
  build_svg_system_prompt(
      style_guide,
      visual_plan,
      content_density,
      design_md_str=...,    ← 新
      resolved_style=...,   ← 新
  )
        │
        ├─ 现有：design-base / shared-standards / page-types / image-boundary
        ├─ 现有：visual_plan 配色块（兼容）
        ├─ 现有：风格指南
        └─ 新增："## DESIGN.md 视觉契约"块：
              ├── Typography 硬约束（字号下限 + 字体）
              ├── Components 定义（card-knowledge 等，token 已解析为 hex）
              ├── Elevation 阴影策略
              ├── Shapes 圆角策略
              └── Do's & Don'ts（3 条守门规则）
```

### 3.2 文件落点

```
edupptx/
├── design/
│   ├── prompts.py             # build_svg_system_prompt 加 design_md 参数 (~30 行)
│   └── svg_generator.py       # generate_slide_svgs 接收并传递 (~10 行)
├── style/
│   └── design_md.py           # 新增 build_phase3_constraints(text, palette) -> str (~80 行)
├── agent.py                   # Phase 1b 末尾 lint，Phase 3 入口传 design_md (~20 行)
└── ...

tests/
├── test_phase3_design_md_injection.py  # 新增 (~150 行)
└── test_design_md.py                   # 扩展 build_phase3_constraints 单元测试
```

## 4. 实现细节

### 4.1 新模块函数：`build_phase3_constraints`

`edupptx/style/design_md.py` 加：

```python
def build_phase3_constraints(text: str, *, resolved_palette: dict[str, str] | None = None) -> str:
    """从 DESIGN.md 提取 Phase 3 SVG 生成需要的可执行约束块。

    返回 Markdown 字符串，将被注入 SVG system prompt。包含：
    - Typography 硬约束（字号下限 + 字体）
    - Components 定义（{colors.xxx} 已解析为 hex）
    - Elevation 阴影策略
    - Shapes 圆角策略
    - Do's & Don'ts

    跳过 Overview / Colors / Layout（信息冗余或已在其他 prompt 块）。
    """
    post = frontmatter.loads(text)
    yaml_data = post.metadata or {}
    body = post.content or ""
    sections = _parse_h2_sections(body)
    palette = dict(resolved_palette or yaml_data.get("colors", {}) or {})

    parts: list[str] = ["## DESIGN.md 视觉契约（必须遵守）", ""]

    typo = yaml_data.get("typography", {}) or {}
    if typo:
        parts.append("### 字体与字号（硬约束）")
        for role, cfg in typo.items():
            font = (cfg or {}).get("fontFamily", "Noto Sans SC")
            size = (cfg or {}).get("fontSize", "")
            weight = (cfg or {}).get("fontWeight", "")
            line = f"- **{role}**: {font}"
            if size:
                line += f", {size}"
            if weight:
                line += f", weight {weight}"
            parts.append(line)
        parts.append("")

    for header in ("Components", "Elevation", "Shapes", "Do's and Don'ts"):
        body_text = sections.get(header, "").strip()
        if not body_text:
            continue
        resolved = _resolve_color_tokens(body_text, palette)
        parts.append(f"### {header}")
        parts.append(resolved)
        parts.append("")

    return "\n".join(parts).strip()


def _resolve_color_tokens(text: str, palette: dict[str, str]) -> str:
    """Replace {colors.xxx} token refs with concrete hex values."""
    import re
    pattern = re.compile(r"\{colors\.([a-zA-Z_][a-zA-Z0-9_]*)\}")
    def _sub(m):
        key = m.group(1)
        return palette.get(key, m.group(0))  # leave unresolved if missing
    return pattern.sub(_sub, text)
```

### 4.2 `build_svg_system_prompt` 扩展

`edupptx/design/prompts.py`：

```python
def build_svg_system_prompt(
    style_guide: str,
    visual_plan: VisualPlan | None = None,
    content_density: Literal["lecture", "review"] = "lecture",
    design_md: str | None = None,        # v3.2 新增
) -> str:
    parts: list[str] = []
    parts.append(_load_ref("design-base.md"))
    parts.append(_load_ref("shared-standards.md"))
    if content_density == "review":
        parts.append(_load_ref("executor-review.md"))
    else:
        parts.append(_load_ref("executor-lecture.md"))
    parts.append(_load_ref("page-types.md"))
    parts.append(_IMAGE_BOUNDARY_RULES)

    if visual_plan:
        parts.append(_build_color_spec(visual_plan))

    if style_guide:
        parts.append(f"\n## 风格指南\n\n{style_guide}")

    # v3.2: DESIGN.md 视觉契约——放在最后强化 LLM 注意力
    if design_md:
        from edupptx.style.design_md import build_phase3_constraints
        constraints = build_phase3_constraints(design_md)
        if constraints:
            parts.append(constraints)

    return "\n\n".join(p for p in parts if p.strip())
```

### 4.3 `generate_slide_svgs` 入参

`edupptx/design/svg_generator.py`：

```python
async def generate_slide_svgs(
    draft: PlanningDraft,
    all_assets: dict[int, SlideAssets],
    style_name: str,
    config: Config,
    debug: bool = False,
    on_slide: Callable[[GeneratedSlide], None] | None = None,
    design_md: str | None = None,    # v3.2 新增
) -> list[GeneratedSlide]:
    ...
    system_prompt = build_svg_system_prompt(
        style_guide,
        visual_plan=draft.visual,
        content_density=draft.visual.content_density,
        design_md=design_md,         # v3.2
    )
    ...
```

### 4.4 Agent 集成 + lint fail-fast

`edupptx/agent.py`：

1. Phase 1e 后，如果 `design_md_str` 非空：
   ```python
   if design_md_str:
       (session.dir / "DESIGN.md").write_text(design_md_str, encoding="utf-8")
       # v3.2: 立即过 lint，broken-ref 直接抛
       try:
           from edupptx.style.design_md import parse_design_md
           from edupptx.style_resolver import resolve_style
           schema = parse_design_md(design_md_str)
           resolve_style(schema)  # lint hook will raise on error findings
           logger.info("DESIGN.md lint passed")
       except Exception as exc:
           logger.warning("DESIGN.md lint failed, falling back to legacy path: {}", str(exc)[:120])
           design_md_str = None  # 不阻塞，但 Phase 3 不用
   ```

2. Phase 3 调用：
   ```python
   slides = await self._phase3_design(
       draft, all_assets,
       draft.style_routing.style_name or style,
       session,
       debug=debug,
       design_md=design_md_str,   # v3.2
   )
   ```

3. `_phase3_design` 接收并透传到 `generate_slide_svgs`。

4. `run_from_plan`：从 `session_dir/DESIGN.md` 读，如存在则同样传给 Phase 3，**消费** v3.1 中标注为"informational only"的产物。把之前 v3.1 加的 logger.warning 改回 info（或直接删除——edits-and-render 现在真生效）。

### 4.5 测试覆盖

`tests/test_design_md.py` 扩展：
- `test_build_phase3_constraints_full_md` — 完整 fixture（光合作用 DESIGN.md），验证返回包含 Typography / Components / Do's & Don'ts 段且 `{colors.primary}` 已被替换为实际 hex
- `test_build_phase3_constraints_empty_palette` — palette 为空时 token 保留原样（不崩溃）
- `test_build_phase3_constraints_unresolved_token` — `{colors.nonexistent}` 保留 raw token（不崩溃）
- `test_build_phase3_constraints_missing_sections` — 缺 Components 段时不抛错，跳过该段
- `test_resolve_color_tokens_multiple_refs` — 一句话内多个 token 全部替换

新文件 `tests/test_phase3_design_md_injection.py`：
- `test_build_svg_system_prompt_with_design_md` — 显式传 design_md 字符串，验证返回的 system prompt 包含 "DESIGN.md 视觉契约" 段
- `test_build_svg_system_prompt_without_design_md` — 不传 design_md 时回到旧行为，输出与 v3.1 完全一致（diff 检查）
- `test_phase3_lint_fail_falls_back` — agent 中构造 broken-ref DESIGN.md，验证 design_md 被设为 None，Phase 3 走旧路径不阻塞

### 4.6 文档更新

- **CLAUDE.md**：DESIGN.md 视觉系统章节末尾加"v3.2: Phase 3 已真消费 components / do's-don'ts / typography 约束"
- **docs/svg-pipeline.md**：删除"DESIGN.md 当前为只读产物"说明，替换为"DESIGN.md 中的 Components / Do's / Typography 段会注入 Phase 3 system prompt"
- **README.md**：DESIGN.md 章节"实验性"标签移除，改为"启用后驱动 Phase 3 输出"

## 5. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Prompt 膨胀使 LLM 复述而非贯彻约束 | 中 | 中 | 只注入 5 段（typography/components/elevation/shapes/dos）；Overview/Colors/Layout 跳过 |
| `{colors.xxx}` 解析正则误伤合法文本 | 低 | 低 | 严格匹配 `\{colors\.<identifier>\}`，未匹配保留原样；测试覆盖 |
| DESIGN.md lint 失败阻塞用户 | 低 | 高 | lint 抛错时 design_md 设为 None，Phase 3 走旧路径不中断 |
| 测试中带 LLM 调用导致 flaky | n/a | n/a | 全部测试 monkeypatch LLM client；不发真请求 |
| 中文 prose 中含 `{` 字符触发误解析 | 低 | 低 | 正则要求紧跟 `colors.`，不会误伤孤立 `{` |

## 6. 验收标准

- [ ] `tests/test_design_md.py` 加 5 个 build_phase3_constraints 测试，全过
- [ ] `tests/test_phase3_design_md_injection.py` 3 个测试，全过
- [ ] 现有 135 测试 0 回归
- [ ] `EDUPPTX_VISUAL_PLANNER_FORMAT=design_md uv run edupptx gen "光合作用" --debug` 跑通，slide_01.svg 中应能看到 DESIGN.md token 引用产生的实际颜色（如 `#69B578`）出现在 card-knowledge 上
- [ ] env 不设时 `uv run edupptx gen "..." --debug` 输出与 v3.1 一致（手动 diff system prompt 字节级一致）
- [ ] 文档 3 处更新

## 7. 不做的事（明确边界）

- 不做 `--style xxx.md` 直接选 DESIGN.md 文件作为输入（用户编辑 DESIGN.md → 重跑 render 是 follow-up）
- 不做 Components 段的 SVG 模板自动生成（LLM 仍自己作图，DESIGN.md 是约束不是模板）
- 不做 prompt 长度自动裁剪（DESIGN.md 控制在 ~5KB，Phase 3 system prompt 仍在 token 限制内）
- 不做 Components 段 schema 严格化（继续允许 prose 自由表述，LLM 自己理解）
