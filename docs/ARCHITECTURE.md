# EduPPTX 架构与现状（ARCHITECTURE）

> 生成日期：2026-06-15
> 方法：并行深读 11 个子系统的真实源码后综合（非凭文档/记忆）。file:line 引用基于当时 `master`（`14afd9a`）。
> 用途：项目现状快照 + 技术债清单，供 review 与排期。

---

## 目录

1. [总览](#1-总览)
2. [完整管线逐阶段详解](#2-完整管线逐阶段详解)
3. [复用库子系统](#3-复用库子系统项目重心)
4. [子系统成熟度地图](#4-子系统成熟度地图)
5. [技术债与缺口清单](#5-技术债与缺口清单)
6. [整体评价与建议优先级](#6-整体评价与建议优先级)
7. [附录：模块清单 / 关键常量 / 环境开关](#7-附录)

---

## 1. 总览

**定位**：AI Agent 驱动的教育 PPTX 生成器。技术路线 = **LLM 生成全页 SVG → 逐元素转 PowerPoint 原生形状**，产出的 PPTX 打开即可编辑（不是贴图、不需"转换为形状"）。

**规模**：`edupptx/` 约 **29,859 行 Python** / 30 模块。复杂度高度集中——`materials/ai_image_asset_db.py` 单文件 **8,896 行**（占全仓近 30%）。测试 76 文件，**700 passed / 2 skipped**。

**一个关键结构事实**：代码体量与测试投入的 **70–80% 集中在 AI 图片素材复用库（v3）**，而主线 SVG 管线（尤其输出层）相对单薄。这是理解整个项目当前状态的核心。

**最大模块（行数）**：

| 模块 | 行数 | 角色 |
|---|---:|---|
| `materials/ai_image_asset_db.py` | 8896 | 复用库核心（建库/路由/三路召回/RRF/policy/review） |
| `postprocess/svg_validator.py` | 2200 | SVG 确定性自动修复 |
| `output/svg_to_shapes.py` | 1899 | SVG→DrawingML 逐元素转换 |
| `agent.py` | 1666 | 5 阶段管线编排器 |
| `design/template_router.py` | 1569 | 页型/模板路由 |

---

## 2. 完整管线逐阶段详解

实际管线比 CLAUDE.md 架构图更细。`agent.py` 的 `PPTXAgent._run_async` 是一条 async 流水线，**唯一的阶段间数据载体是被反复就地变异的 `PlanningDraft` 对象**（外加 `dict[int, SlideAssets]` 资产表）。三个对外入口共享 phase 实现：`run`（全流程）、`run_from_plan`（从 plan.json resume，对应 `edupptx render`）、`run_images_from_plan`（只跑到图片/入库）。

### Phase 0 — 输入处理
`input/document_parser.py`（PDF 用 pymupdf 纯文本、无 OCR；DOCX/MD/TXT）+ `input/web_researcher.py`（Tavily 联网，best-effort，任何异常吞成空串）→ 填进 `InputContext`。

### Phase 1 — 规划（实际是 1a–1f 六个子阶段）

这是最被低估的部分——**不是单次规划，是多轮 LLM 往返**：

| 子阶段 | 实现 | 做什么 |
|---|---|---|
| 1a 内容规划 | `content_planner.generate_planning_outline` → `refine_planning_draft` | 两段式：第一段纯内容大纲（**禁模板/配色/图片槽**），第二段喂命中模板 brief 后补全素材槽 |
| 1a 模板路由 | `template_router.resolve_style_routing` | `_score_variant_spec` 确定性打分选模板族 + palette |
| 1b 变体分配 | `assign_page_template_variants` | 每页路由到具体 SVG 变体（确定性打分→LLM top-3 兜底→content 返回 None 放弃） |
| 1c 精炼 | `refine_planning_draft`（stage-2） | 带模板参考精炼内容 |
| 1d reveal 展开 | `content_planner.finalize_reveal_pages` | **伪动画核心**：扫 quiz/exercise 页自动复制"答案揭晓页" |
| 1e 视觉规划 | `visual_planner.generate_visual_plan` / `generate_design_md` | VisualPlan JSON（默认）或 DESIGN.md（opt-in） |
| 1f 模板对齐 | `align_draft_to_template` | 裁标题、限 toc、填图片槽 |

**两段式规划的设计哲学**：1a system prompt 明令"不要为模板反推内容结构"（`prompts.py:111`），刻意分离信息架构 vs 视觉细化——对应 CLAUDE.md "策划/设计分离"。

**reveal 伪动画**：深拷贝源题页、只补答案、强制把 `design_notes` 改为"保留原布局只补答案不新增元素"（`content_planner.py:347`），让 Phase 3 生成视觉连续两页；并重排全局页码 + `old_to_new` 修正 `reveal_from_page`。**代价**：揭晓页要拿前一页 SVG 当参考，会把整个 Phase 3 从并行降级为串行（`svg_generator.py:145`）。

**JSON 抗脏**：`_load_json_with_repair` 四级降级（json.loads → 去尾逗号 → strict=False → json_repair），全失败 dump 原文再抛。

**题库绑定（可选，默认关）**：`exercise_plan_binder` 把真实题库题目精确绑进 exercise 页。本会话刚把它从"硬抛异常"改为"逐 ref 降级 + warning"（M-15，commit `ae40c3a`）——幻觉 ref / 无答案 / 缺图都降级为单条 warning 而非崩整轮生成。

### Phase 2 — 背景 / 素材 / 异步入库

- **2 背景**：先试库复用 `background.png`，未命中才 Seedream 文生图（2848×1600）。
  **精巧设计**：背景有两个 prompt 函数——不含配色的 `build_background_content_prompt`（复用检索 key，`agent.py:799`）和含 `background_color_bias` 的 `build_background_prompt`（真实生成）。换配色不影响命中同一张可复用背景。
- **2b 素材**：三段式——(1) `collect_reuse_candidates` 线程池并行三路检索搜集候选；(2) `_run_policy` 第二个线程池（`EDUPPTX_REUSE_POLICY_WORKERS` 默认 5）并行 policy+LLM/VLM review，两段都传 `reuse_session_state=None` 抑制占用竞态；(3) **串行 materialize + `_strict_reuse_occupancy_status` 复检**保 strict-reuse-per-session 不变式。未命中回落 `fetch_images`。
- **2c 异步入库**：`_enqueue_asset_library_update_job`（job_id 含资产 sha256 指纹防撞主键，M-5）写 SQLite 队列 → `subprocess.Popen` 起 detached 子进程 `asset_ingest_worker`，**完全不阻塞主路径**。

- **`--debug` 模式**：不进 `_phase2_materials`，直接给每页造只带 `background_path` 的空 `SlideAssets`（`agent.py:273-279`），下游 Phase 3 用虚线占位框。

### Phase 3 — SVG 生成

并行 LLM 生成 1280×720 Bento Grid 全页 SVG（`svg_generator.generate_slide_svgs`）。

- **system prompt 很大**：design-base.md 23KB + page-types.md 28KB + shared-standards.md + 每页 3000 字模板参考；靠 `max_tokens=16384` / `timeout=300s` 撑。
- **模板是"参考骨架"不是"填空模板"**：prompt 坚持"从中取法但不要复制装饰和文字"（`prompts.py:458/474`），按族差异化装饰规则（低龄 3-8 活泼 / 高龄 1-4 克制 / reusable 中性）。
- **DESIGN.md 真消费（v3.2）**：`design_md.build_phase3_constraints` 抽 typography 硬约束 + Components/Elevation/Shapes/Do's-and-Don'ts 四段 prose（`{colors.xxx}`→hex）注入 system prompt 末段（`prompts.py:257`）。
- **图片/图标占位**：LLM 写 `__IMAGE__` href 占位 + `data-icon`，后处理换真位图与图标。
- **11+ 布局模式**：center_hero / vertical_list / bento_2col(equal·asymmetric) / bento_3col / hero_top_cards_bottom / mixed_grid / relation / comparison / timeline / full_image …

### Phase 4 — 校验 + 审阅（per-slide 并行）

四步把 LLM 的"野生 SVG"驯化成 PPT 安全的中间格式（`agent._phase4_postprocess._process_one`）：

1. **`svg_validator.validate_and_fix`**（2200 行）：约 25 条确定性规则修 viewBox/字体/circle 编号对齐/边界 clamp/文字换行/重叠/图片宽高比与溢出/卡片高度。**修复顺序是有依赖的硬编码序列**（先 `_wrap_long_text` 改 tspan 结构 → 再 `_clamp_boundaries` → 再 `_fix_text_overlaps`/`_fix_text_outside_cards` 读 live DOM 真实底边）。防 XXE（`_SAFE_PARSER` resolve_entities=False/no_network）。
2. **LLM review**（`svg_reviewer.review_and_fix_svg`）：受 `_needs_llm_review` 成本门控（minor warning 白名单全命中则跳过）。**单次 LLM pass**（无渲染、无重试）。占位符丢失检测：LLM 删了 `__IMAGE_*__` 则整体回退 pre-review。
3. **`svg_sanitizer.sanitize_for_ppt`**：去 script/事件/emoji/嵌套 tspan/注释/root 宽高，HTML 实体转数字字符引用。
4. latex/icon/image base64 注入。

**独立旁路 — style_linter**：在 `style_resolver.resolve_style` 末尾对解析后的 DESIGN.md/StyleSchema 跑 WCAG 对比度（正文 4.5:1 / 装饰 3.0:1）+ palette broken-ref。broken-ref 必抛 `StyleValidationError`；contrast 默认 warning（`EDUPPTX_LINT_STRICT=1` 升级）。

### Phase 5 — SVG→DrawingML→PPTX

**1899 行零依赖手写 SVG 子集解释器**（仅 stdlib xml.etree + PIL 读尺寸）：

- 映射：`rect→roundRect/rect`，`circle/ellipse→ellipse`，`line/path/polygon→custGeom`，`text+tspan→txBox`，`image→p:pic blipFill`，`g→坐标上下文递归`，`use→从 defs 内联`。
- 坐标 **1 SVG px = 9525 EMU**（1280×9525 = 12192000 = 16:9 画布宽，1:1 直映射）。
- `path→custGeom` 四级流水线：相对转绝对 → H/V 补全 → S/T 平滑曲线反射 / Q→C 升阶 → **椭圆弧端点参数化转贝塞尔（`_arc_to_cubic` 教科书级正确）**。
- CJK 双字体：每 run 同写 `<a:latin>` + `<a:ea>`，PPT 按字符自动选东亚字体（默认 Noto Sans SC）。
- 文本框宽度：`estimate_text_width` 字符宽度系数 + "容器矩形 + 右侧相邻 text 列边界"启发式收窄（专治 LLM 把多列文字塞单框）。
- native shapes 模式：直接字符串拼 XML 再 ZIP 重打包，python-pptx 只生成带正确 layout/theme 的空壳；单页失败 → SVG+PNG embed 回退。
- embed 模式：`asvg:svgBlip` 扩展实现 PNG 主显 + SVG 矢量双嵌入（Office 2016+ 官方机制）。

---

## 3. 复用库子系统（项目重心）

`ai_image_asset_db.py`（8896 行）+ 一圈支撑模块。分三块。

### 3.1 读路径（检索 / 裁决）

```
find_reusable_ai_image_asset → 硬过滤 eligible_assets
  → 三路并行召回(BM25 + Qwen3-Embedding + substring，各取 pool_limit=max(20, limit))
  → _rank_hybrid_reuse_candidates(RRF K=60 合池)
  → _candidate_policy_score(0.25·kw + 0.55·emb + 0.20·sub) = 唯一裁决分
  → decide_reuse 三档: ≥0.75 且 gap≥0.02 直接复用 / <0.35 拒 / 灰区 gap≤0.02 进 LLM review@0.60
  → R5 near-miss VLM 兜底(需 vlm_client，生产默认走不到)
```

- **核心设计被代码反复强调并落实**：`policy_score` 是唯一裁决分，RRF 只用于合池召回、不参与高低切（`reuse_policy.py:73-75`）。
- **缓存容器** `ReuseSearchContext`（`:223`）：library_cache / route_index_cache / query_embedding_cache + `cache_lock`(RLock)，避免一个 PPT 多 slot 重复读 sidecar / 重编码 target embedding。
- 多库：各库 `_collect_candidates_only` 收候选 → `_global_reuse_candidate_rank` 合池 → `_finalize_reuse_candidate_collection` 统一裁决。
- **H-1**：query 端 embedding 编码失败不静默——进程级 once-guard 大声 warning + 写 `status_sink[query_encode_failed]`，降级 text-only recall 而非伪装 enabled 全拒。
- **本会话清理已坐实**：R2a 删装饰性 RRF 排序（sort key 只剩 policy>keyword>embedding>substring）；R2a-3 缓存复用（`dict()` 浅拷防改共享缓存）；M-14 single-flight（`_load_reuse_library_for_search` build 进 RLock）。

### 3.2 写路径（建库 / 入库）

- **全程原子落盘**（.tmp→os.replace）。
- `write_ai_image_split_match_indexes` 拆 4 个语义组 JSON + background.json；C03 组追加 C01→C03 secondary projection；C00 组写前与磁盘旧文件 union（M-4 跨 run 累积）。
- `write_ai_image_embedding_index`：npz 向量 sidecar，按 batch 编码、每批 checkpoint（fingerprint 校验 + 逐行 prefix 验证）断点续传，复用旧 sidecar 未变向量免重算。
- 异步入库队列 `asset_ingest_job_store`：WAL + `BEGIN IMMEDIATE` 租约**全局串行化**（同一时刻仅一 job，避免 split JSON 互覆）。
- **去重分两套**：入库 merge 仅按 asset_id 精确去重；感知去重（`ppt_dedupe`：sha256 + dHash 汉明距离 + 颜色签名 L1 + 文本 2-gram Jaccard）是 build-time 工具，不在 per-session 路径上（本会话 B1 加了颜色门修 dHash 对纯色块的颜色盲）。

### 3.3 支撑层

| 模块 | 职责 |
|---|---|
| `strict_reuse_classifier.py` | C00-C03 四类教学语义分类（主判据"替换不变性"）+ C01→C03 投影 + split 读写 |
| `reuse_policy.py` | 阈值单一来源（T_DIRECT=0.75/T_REJECT=0.35/T_GAP=0.02）+ decide_reuse 三档 |
| `caption_rules / general_rules / vlm_metadata_rules` | 建库侧与 plan 侧共用规则文本（防 prompt 漂移） |
| `asset_store.py` | sqlite-vec 后端 library.db（schema / thread-local 连接 / migrate / export / doctor） |
| `reuse_observability.py` | 跨会话覆盖缺口 JSONL 日志（ceiling=0.60，只记"库里连中等相关都没有"的真空洞） |
| `vlm_asset_enricher.py` | VLM 入库审查（默认关闭，未充分调试） |

**双后端**：`json`（默认，split JSON + npz）| `sqlite`（opt-in，`EDUPPTX_REUSE_BACKEND=sqlite`，library.db，已在 zlz 真实库 A/B 等价验证）。

---

## 4. 子系统成熟度地图

| 子系统 | 判定 | 依据 |
|---|---|---|
| 管线编排 | 🟢 solid | 错误处理一致、防御性强，可选步骤全 try/except 后继续不 abort |
| Phase 1 规划 | 🟢 solid | JSON 四级降级、DESIGN.md 双路径+兜底永不阻塞、reveal 页码重排完整 |
| Phase 3 SVG 生成 | 🟢 solid | 路由分层（确定性打分+LLM 兜底+放弃）、prompt 工程细致 |
| Phase 4 校验 | 🟢 solid | "被生产数据反复教育过"，每条规则有针对性豁免、防 XXE |
| Phase 5 SVG→PPTX | 🟢 solid | 手写解释器覆盖完整、椭圆弧教科书级、单元素失败只 skip 不炸页 |
| 复用·读路径 | 🟢 solid | 裁决链自洽、每边界有 finish reason、清理带 pin 测试 |
| 复用·写路径 | 🟢 solid | 原子落盘 + checkpoint + 租约串行化，多轮加固 |
| 素材生成 | 🟢 solid | 小而稳，provider 全有容错降级 |
| 地基层 | 🟢 solid | LLM 输出三重归一化、三厂商分支、配置遵循三件套 |
| **复用·支撑层** | 🟡 **rough** | vlm_asset_enricher 未收尾（过期 prompt + 死代码）；asset_store R1 半成品演进中 |
| **测试体系** | 🟡 **rough** | "一条腿粗"——复用库测试扎实，主线 SVG 管线几乎裸奔 |

---

## 5. 技术债与缺口清单

> 全部基于读真代码。优先级 🔴 高 / 🟡 中。

### 🔴 测试覆盖严重失衡
- **SVG→PPTX 输出层零测试**：`svg_to_shapes.py`(1899行核心)、`pptx_assembler.py`、`svg_generator.py`、`svg_reviewer.py`、`document_parser.py`、`web_researcher.py`、`pixabay.py`、`unsplash.py` —— **8 模块在 tests/ 零直接引用**。rect→custGeom / EMU 坐标 / CJK 字体等关键逻辑无回归保护。
- tests/ 约 80% 体量压在复用库；主线靠 `test_agent_*`(8 文件，高度 monkeypatch)间接覆盖，非真实端到端。
- `test_reuse/` 评测依赖真实 `output/session_*/plan.json` + 真实 LLM key，非 CI 可无人值守。

### 🔴 CLAUDE.md Self-Validation 要求未落地
CLAUDE.md 末尾要求把 `tests/visual_qa.py` 集成进主管线"生成后自动校验"。实际：`visual_qa.py` 只有 bbox/重叠检测函数（255 行，未实现 LibreOffice→PNG 闭环），**`agent.py` grep 不到任何 visual_qa 调用**，要求的 `tests/test_visual_qa.py` 不存在。**明确未兑现的需求。**

### 🟡 文档与代码不符
- CLAUDE.md（及历史描述）说 reviewer 是"cairosvg 直渲→审查→修→再生成迭代循环"，但 `svg_reviewer.review_and_fix_svg` 实际是**单次 LLM pass**（无渲染、无重试、无收敛判断）。cairosvg 只在 `pptx_assembler.py:321` 和 `icons.py`，不在 review 回路。

### 🟡 vlm_asset_enricher 未收尾
- `vlm_asset_enricher.py:71-78` prompt 仍写"7 个类别 C00-C06"，但**实际只有 4 类 C00-C03**，框架文字与注入规则自相矛盾，会诱导 VLM 输出非法类别。
- `:81-121` `VLM_REDESCRIBE_SYSTEM_PROMPT` 被连续赋值两次，第一版是死代码。
- 默认关闭、"未充分调试"；本会话修过其 VLM 预算 TOCTOU（M-12）但路径未进回归保护。

### 🟡 Phase 3 模板资产 / 代码契约不匹配
- `bar/line/pie/kpi` 四图表模板**未被消费**（`_CHART_TEMPLATE_MAP` 只有 timeline/relation，`prompts.py:36-39`）；relation 还有个死引用。
- `data`/`case` 页型**永远走无模板分支**（`_PAGE_TYPE_TEMPLATE_STEMS` 没映射，`prompts.py:26`）。
- reuse 族文件名：`summary.svg` 的预览叫 `summery.png`（拼写）。
- references 目录 `image-prompt-profiles.json` / `image-prompt-routing.md` / `notes-guidelines.md` / `planning-image-rules.md` 不被 `prompts.py` 消费。

### 🟡 SVG→PPTX 几何简化
- 坐标变换是"累加 translate + 乘积 scale"，**非完整 2D 仿射矩阵**：`<g>` 上 `rotate`/`matrix()` 被忽略，带旋转的组渲染错位。
- `convert_path` 的 scale 分量被解析却没传进 `path_commands_to_drawingml`，静默丢失（`svg_to_shapes.py:930-933`）。
- `radialGradient` 固定输出居中圆，忽略 cx/cy/fx/fy/r，偏心径向渐变渲染错。
- `build_shadow_xml` 把 dx 钳到非负，dx<0（向左偏移）阴影方向算错。
- `convert_use` 用临时 set/del 原地改 defs 共享元素的 opacity，非线程安全且缺 finally 还原。

### 🟡 其他卫生 / dead config
- `cli.py:599-604` reuse-check 命令中文 help 是**乱码**（GBK/UTF-8 编码事故，单点）。
- 三档检索阈值表（loose/medium/strict，`PAGE_IMAGE_REUSE_GATE_THRESHOLDS`）目前**大量 dead config**——真实裁决只用 policy_score + LLM review（`ai_image_asset_db.py:3456-3458` 注释自承"恢复属行为变更需 goldset 验证"）。
- `style_linter` 只检查 ResolvedStyle 固定 5×2 语义色对，**不检查 SVG 实际渲染出的任意颜色对**——LLM 自选配色对比度无人把关。
- `svg_validator.py:1946` 死函数 `_fix_text_outside_cards_legacy_unused`（函数名自承 unused）。
- `pptx_assembler.py:25-26` `SCALE_X/SCALE_Y` 定义后从未使用（与 `EMU_PER_PX` 重复）。
- 入库队列全局串行化（有意，防互覆）但吞吐上限低；库变大后每 job 全量重写 4 个 split JSON，**写放大明显**。
- `_merge_asset_library_db` 入库期无感知去重：同图换 asset_id 重入会当新图收录，靠离线脚本事后清。
- `config.py` `web_search` 字段不从 env 解析（仅 CLI flag 注入）；`style_schema.py:142` `slide_overrides` 定义但 resolve_style 全程未消费（dead config surface）。

---

## 6. 整体评价与建议优先级

**主线管线成熟可用**：0→5 阶段每环都是被真实 LLM 输出迭代打磨过的生产代码，不是脚手架。错误处理纪律一致（可选步骤全降级不 abort），CJK / 数学公式 / 占位符保护这些真实坑都踩过补过。

**两个结构性问题**：
1. **测试投入与代码重心错配**——80% 测试在复用库，而**项目卖点（SVG→可编辑 PPTX）的输出层零测试**。`svg_to_shapes` 改坏没有自动化能拦。
2. **8896 行的复用核心是维护性炸弹**——单文件扛建库/检索/入库/policy/review 全部职责，架构上早该拆。

**本地可做的优先级**（不碰部署/goldset）：

| 优先级 | 动作 | 价值 |
|---|---|---|
| 1 | 补 SVG→PPTX 输出层回归测试 + 把 `visual_qa.py` 接进 Phase 5（兑现 CLAUDE.md） | 补上项目卖点的质量空洞 |
| 2 | 清理 vlm_asset_enricher 过期 prompt + 死代码、cli.py 乱码 help、死函数 | 低风险卫生 |
| 3 | 拆 `ai_image_asset_db.py`（读/写/支撑分文件） | 大手术，需谨慎、分步 |

**卡在部署/goldset 的（暂搁）**：R2b substring 双重计数、切 sqlite 默认、R3 词法 IDF、R4 Chinese-CLIP、R5 退役 json。第一前置是把 master 部署到 zlz（现跑 v3）+ 建正式 goldset。

---

## 7. 附录

### 7.1 关键常量

| 常量 | 值 | 出处 |
|---|---|---|
| EMU_PER_PX | 9525 | `svg_to_shapes.py`（1 SVG px = 9525 EMU） |
| 画布 | 1280×720（=12192000×6858000 EMU，16:9） | viewBox |
| 三路检索权重 | BM25 0.25 / embedding 0.55 / substring 0.20 | `ai_image_asset_db.py` |
| 复用阈值 | T_DIRECT=0.75 / T_REJECT=0.35 / T_GAP=0.02 / LLM review 0.60 | `reuse_policy.py` |
| RRF K | 60 | `_rank_hybrid_reuse_candidates` |
| LLM | timeout=300s / max_retries=1 / 豆包 thinking:disabled | `llm_client.py` |

### 7.2 关键环境开关（详见 README / .env.example）

| 开关 | 默认 | 作用 |
|---|---|---|
| `EDUPPTX_VISUAL_PLANNER_FORMAT` | json | json 旧路径 / design_md 新 8 段路径 |
| `EDUPPTX_LINT_STRICT` | 0 | 1 时 contrast warning 升级为 error |
| `EDUPPTX_DISABLE_AI_IMAGE_REUSE` | 0 | 1 关闭复用读路径 |
| `EDUPPTX_REUSE_BACKEND` | json | sqlite 切 library.db 后端（需先 `assets migrate`） |
| `EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS` | 0 | 1 仅 BM25+substring |
| `REUSE_LIBRARY_DIRS` | — | 复用检索库目录列表 |
| `EDUPPTX_REUSE_POLICY_WORKERS` | 5 | Phase 2b policy 并行 worker 数 |

### 7.3 CLI 命令面（16 个）

- **主路径**：`gen` / `render`（从 plan.json）/ `plan`
- **复用运维**：`reuse-check` / `asset-ingest` / `embedding-build` / `strict-reuse-classify` / `strict-reuse-export-check` / `vlm-enrich` / `assets migrate|export|doctor`
- **风格**：`styles`（含 convert）

### 7.4 关键设计决策（代码自证）

1. **SVG 作为设计中间格式**：LLM 擅长生成 SVG，但 SVG 嵌 PPTX 不可编辑 → 逐元素转原生形状。
2. **policy_score 单一裁决**：三路召回只产 rank（无权 RRF 入池），policy_score 是唯一驱动 decide_reuse 的分。
3. **读路径不写库（sqlite）**：sqlite 后端纯读；json 后端读时仍可能 lazy 重建 sidecar（已 single-flight，写本原子）。
4. **背景 content/final prompt 分离**：换配色不影响命中可复用背景。
5. **零依赖手写 SVG→DrawingML**：符合"最小依赖/显式优于巧妙"，但 SVG 特性支持范围由 1899 行手工决定。

---

*本文档为现状快照。代码演进后请同步更新，或移入 `docs/_archive/` 标注日期。*
