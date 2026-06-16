# EduPPTX 架构与现状（ARCHITECTURE）

> 更新日期：2026-06-16
> 基准：`master` `e51c498`（含 Phase A 复用层重构 + R2b substring 修复 + R3 真实 IDF）。
> 方法：6 个 reader 并行深读**当前**源码后综合（非凭记忆/旧文档），并逐条核对旧版（基于 `14afd9a`）已过时处。
> 用途：给人看的「项目是怎么跑起来的」全流程说明 + 现状快照 + 技术债清单，供 review 与排期。

---

## 目录

1. [一分钟看懂这个项目](#1-一分钟看懂这个项目)
2. [完整流程：从一句主题到一份可编辑 PPTX（大白话详解）](#2-完整流程从一句主题到一份可编辑-pptx大白话详解)
3. [复用库子系统（项目重心，已重构）](#3-复用库子系统项目重心已重构)
4. [子系统成熟度地图](#4-子系统成熟度地图)
5. [技术债与缺口清单](#5-技术债与缺口清单)
6. [整体评价与建议优先级](#6-整体评价与建议优先级)
7. [附录](#7-附录)

---

## 1. 一分钟看懂这个项目

**它做什么**：你给一句主题（外加可选的要求、参考文档、联网开关），它产出一份**可以直接在 PowerPoint 里编辑**的教育演示文稿。

**核心赌注**：不让 LLM 直接吐 PPTX（那玩意儿 XML 又臭又长，LLM 写不好），而是让 LLM 先画**整页 SVG**（矢量图，布局自由度高、视觉质量好），再用一个手写转换器把 SVG 的每个元素**逐个翻译成 PowerPoint 原生形状**（圆角矩形、文本框、自由曲线）。结果就是：打开 PPT，每张卡片、每段文字都是真形状，点一下就能拖能改，**不是贴一张图、也不用"转换为形状"**。这是整个项目的卖点，也是它和"把 AI 生成的图塞进 PPT"那类工具的根本区别。

**规模（当前真实数字）**：`edupptx/` 约 **31,661 行 Python / 77 个模块**。测试 **699 passed / 1 skipped**（`tests/` 606 + `test_reuse/tests/` 93）。

**一个关键结构事实**：代码体量与测试投入的大头压在 **AI 图片素材复用库（v3）**——让生成过的图片能跨会话复用，省钱省时。它原本是一个 8896 行的单体文件，现已**重构成 `edupptx/reuse/` 子包（19 个模块 + 一个 1126 行的兼容 shim）**。理解这一点是理解整个项目当前状态的关键：主线 SVG 管线成熟稳定但测试单薄，复用库代码量大、迭代频繁、测试扎实。

**最大的几个模块**：

| 模块 | 行数 | 角色 |
|---|---:|---|
| `reuse/_decide.py` | 1635 | 复用裁决编排（policy 阈值 + 多库合池 + review 调度） |
| `postprocess/svg_validator.py` | 2200 | SVG 确定性自动修复（~25 类规则） |
| `output/svg_to_shapes.py` | 1899 | SVG→DrawingML 逐元素转换器 |
| `agent.py` | 1666 | 0→5 阶段管线编排器 |
| `design/template_router.py` | 1569 | 页型/模板路由 |
| `reuse/`（整包） | 9572 | 复用库全部逻辑（19 模块，见 §3） |
| `materials/ai_image_asset_db.py` | 1126 | **纯 re-export shim**（0 自有定义，保旧 import 路径） |

---

## 2. 完整流程：从一句主题到一份可编辑 PPTX（大白话详解）

整条管线在 `agent.py` 的 `PPTXAgent._run_async` 里，是一条 async 流水线。**贯穿始终的数据只有一个对象**：`PlanningDraft`（落盘就是 `plan.json`），它从 Phase 1 开始被各阶段反复就地填充、修改——页面结构、配色、图片需求、最后连每页的 SVG 都挂在它身上；再加一张 `dict[int, SlideAssets]` 资产表记录每页要用的图片。

有三个对外入口共用同一套阶段实现：
- `run`：跑全流程（`edupptx gen`）。
- `run_from_plan`：从已有 `plan.json` 续跑（`edupptx render`）——比如人工改完 plan 再生成。
- `run_images_from_plan`：只跑到图片/入库，不出 PPT。

下面按数据流的顺序，一段段讲清楚每一步「拿到什么、做了什么、为什么这么做、产出什么」。

---

### Phase 0 — 把杂乱输入收成一个 InputContext

`_phase0_input` 干三件事：
1. 有 `--file` 就解析文档：PDF 用 pymupdf 抽纯文字（无 OCR）、docx 用 python-docx 抽段落、md/txt 直接读。
2. 开了 `--research` 且配了 Tavily key，就异步搜一把网络资料（默认 5 条、超时 30s、`include_answer=True`）。**任何异常都吞掉返回空串**——搜不到不让它拖垮生成。
3. 把 topic / requirements / 源文本 / 搜索摘要打包成 `InputContext`。源文本进 prompt 时截到 8000 字符，防止把上下文撑爆。

---

### Phase 1 — 规划：先想清楚讲什么，再想怎么排（这是最被低估的一段）

很多人以为"规划"是一次 LLM 调用，**其实是六个子阶段串起来的多轮往返**，核心设计哲学就一句：**先定信息架构，再定视觉，绝不让模板反推内容**。

| 子阶段 | 方法（agent.py） | 做什么 |
|---|---|---|
| Stage-1 内容大纲 | `content_planner.generate_planning_outline` | LLM#1 只产页面骨架：page_type / 标题 / 副标题 / 内容点 / layout_hint。**明令禁止**引入模板、配色、图片槽、卡片数量 |
| 1a 模板路由 | `_phase1a` → `resolve_style_routing` | 拿大纲文字 + layout_hint 做关键词打分选模板家族；打分为 0 才让 LLM 兜底，再不行 fallback 到默认家族「复用」，并选定 palette |
| 1b 变体匹配 | `_phase1b` → `assign_page_template_variants` | 每页路由到具体 SVG 模板变体（如 `toc_1`） |
| 1c 内容细化 | `_phase1c` → `refine_planning_draft` | LLM#2 拿大纲 + 逐页模板 brief，补全图片需求 / design_notes / 备注；强调模板的卡片数/图片数是「建议区间不是硬指标」 |
| 1d reveal 展开 | `_phase1d` → `finalize_reveal_pages` | **伪动画核心**：扫 quiz/exercise 页自动复制"答案揭晓页" |
| 1e 视觉规划 | `_phase1e` → `generate_visual_plan` / `generate_design_md` | LLM#3 出统一配色 + 背景 prompt + 内容密度；可选产 DESIGN.md |
| 1f 模板对齐 | `_phase1f` → `align_draft_to_template` | 按模板契约裁标题、限 toc 条目、补图片槽 |

**为什么两段式（大纲 / 细化）分开**：Stage-1 的 system prompt 白纸黑字写着"这一阶段不要为模板去反推内容结构"。这是刻意的——先让模型专注"这堂课要讲哪几个点、怎么排信息逻辑"，等模板和配色定了，再回头补"每页放几张卡、要哪些图"。避免一上来就被模板带跑、内容迁就版式。

**reveal 伪动画（自动展开 + 它的代价）**：PPT 做不了真动画，于是用"两页装一帧"骗——题目一页、答案揭晓一页，翻页即"揭晓"。`finalize_reveal_pages` 在内容细化后扫一遍，给 quiz 页配 `highlight_correct_option`、给 exercise 填空配 `show_answer`。揭晓页是**深拷贝源题页**，只补答案内容点，并**强制改写它的 design_notes**为"保留原布局、只在原空位补答案、不新增图片不动元素"，然后重排全局页码、修好 `reveal_from_page` 指向。**代价**：Phase 3 一旦发现有任何 reveal 页，整批 SVG 生成**从并行降级为串行**——因为揭晓页必须拿源页"已经画好的 SVG"当基底才能保版式一致。

**JSON 抗脏**：LLM 吐的 JSON 经常带尾逗号、串内换行、半截括号。`_load_json_with_repair` 四级降级硬扛：标准 `json.loads` → 正则去尾逗号 → `strict=False` 容忍换行 → `json_repair` 暴力修；全挂了才把原文 dump 到 debug 目录再抛。解析后还有 `_normalize_draft_dict` 把非法的 page_type/layout_hint 归一回合法枚举。

**题库绑定（可选，默认关）**：`exercise_plan_binder` 能把真实题库的题目精确绑进 exercise 页。现在它是**逐条降级不硬抛**：某条 ref 查不到、缺答案、缺图片，就只跳过那一条加一条 warning；strict DB 模式匹配失败则整体回落到 AI 生成的题。**不会因为题库问题崩掉整轮生成。**

> 补充：Phase 1 尾段还会跑一步 `_route_ai_image_prompts`，给每个图片需求挂上生成用的 prompt 并归一化宽高比——CLAUDE.md 顶部的架构图没画这一步。

规划全部跑完，`save_plan` 落地 `plan.json` + 一份人读的 `design_spec.md`（开了 design_md 路径还会写 `DESIGN.md`）。`--review` 模式到此停手，等人改完再 `edupptx render`。

---

### Phase 2 / 2b / 2c — 背景、素材、异步入库（复用库在这里登场）

**Phase 2 背景**：先试"从库里复用一张背景"，没命中才花钱调 Seedream 文生图（2848×1600、无水印、png）。
这里有个**精巧设计——背景有两个 prompt**：检索复用时用 `build_background_content_prompt`（**只描述画面、不含配色**），真正生成时才用 `build_background_prompt`（把"配色偏向：冷蓝"之类拼到末尾）。这样换一套配色，照样能命中同一张构图的可复用背景，复用率更高。背景的复用阈值很低（0.38），打分按 prompt 0.85 + 配色 0.15 加权。

**Phase 2b 素材**（`--debug` 模式整段跳过，图片位置用虚线框占位，只看布局省钱）。非 debug 走三段式：
1. **召回（并行）**：每页每个待生成图槽建一个检索目标，先批量预热关键词，再用线程池（默认 3 worker）并行召回候选——**只收集候选、不裁决**。
2. **裁决（并行）**：第二个线程池（默认 5 worker，`EDUPPTX_REUSE_POLICY_WORKERS` 可调）并行跑 policy 打分 + 必要时 LLM review。这一步刻意把"strict 资产每会话占用上限"降级为建议，避免并发抢占打架。
3. **落地（串行）**：顺序遍历，对每个要复用的资产**重新复查占用上限**（超了就降级回生成），通过就 copy 成 `page_NN_slot.ext`；没命中的攒进 pending，最后并行 `fetch_images`（Seedream 生成 或 Pixabay/Unsplash 搜索）。

**检索裁决怎么算（读路径核心）**：`find_reusable_ai_image_asset` 三路并行召回——
- **BM25**：文本词频匹配；
- **embedding**：Qwen3 向量点积（query 向量带磁盘缓存）；
- **substring**：子串命中率。

三路结果用 **RRF（K=60）只做"合池"**——把三条召回并成一个候选集，算出的 RRF 分**只留作审计，不参与高低排序**。**真正决定取谁的是 `policy_score`**：

> **policy_score =（0.25·关键词分 + 0.55·向量分）/ 0.80**

注意这里**没有独立的 substring 项**了（这是 R2b 修复）——substring 已经折进"关键词分"里算过一次，旧版又在外面独立加 0.20·substring，等于把它算了两遍、有效权重反超 BM25。关掉 embedding 时（`EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS=1`）退化为纯关键词分。其中 BM25 在能拿到全库语料统计时用**真实 IDF**（罕见词权重高、常见词权重低，这是 R3 修复），拿不到才回落旧的"伪语料"（N=2、任何匹配词 IDF 恒为 0.182、毫无区分力）。

拿到 policy_score 后，`decide_reuse` 三档切：**最高分 ≥ 0.75 且和第二名差 ≥ 0.02 → 直接复用**；**< 0.35 → 直接拒**；**中间灰区 → 送 LLM review**（阈值 0.60，每个 query 最多看 5 个候选）。总开关 `EDUPPTX_DISABLE_AI_IMAGE_REUSE=1` 时整条读路径跳过，每次都新生成。

**Phase 2c 异步入库**：这次新生成的图要进库，但**绝不阻塞主路径**——`_enqueue_asset_library_update_job` 往一个 SQLite 队列里塞一条 job（job_id 含图片内容的 sha256 指纹，保证同图幂等、改图算新 job），队列用 WAL + `BEGIN IMMEDIATE` 全局串行（同一时刻只跑一个 job，防止并发写互相覆盖 split 文件）；然后 `subprocess.Popen` 起一个 detached 子进程去慢慢入库，主进程立刻往下走。

---

### Phase 3 — 让 LLM 给每一页画整页 SVG

到这步，plan 里有了内容、配色、可能还有 DESIGN.md、以及每页要用的图。Phase 3（`svg_generator.generate_slide_svgs`）的活就是让 LLM 照这些约束，给每页画出一张 **1280×720 的 Bento Grid 全页 SVG**。

- **并行 / 串行**：普通页用线程池并发（worker = min(页数, `llm_concurrency`)，默认 4），谁先回来先处理、最后按页码排序；**只要有一页是 reveal 揭晓页，整批降级串行**（揭晓页要引用源页已画好的 SVG）。每页调用 `max_tokens=16384`、`temperature=0.7`、客户端 `timeout=300s`，内置一次重试，两次都失败就吐一张"生成失败"占位 SVG 兜底，不让整批崩。
- **system prompt 很大且分层拼**：依次拼 `design-base.md`（≈23KB 公共设计规范）+ `shared-standards.md`（≈7KB 技术约束）+ 按内容密度二选一的 `executor-lecture/review.md` + `page-types.md`（≈28KB 页型定义）+ 一段图片边界硬规则 + 由 VisualPlan 生成的配色块 + 风格家族的 style guide + 末段 DESIGN.md 契约。靠 `max_tokens=16384` / `timeout=300s` 撑住。
- **"模板是参考骨架不是填空模板"**：用户 prompt 里会塞当前页型的参考 SVG（每份截到 3000 字），但措辞反复强调——只学它的**布局骨架、卡片关系、图片区位、间距节奏**，**不要复制它的文字和装饰**。命不中模板时明确告诉 LLM"别硬套别的页型，自己按规则设计"。
- **DESIGN.md 怎么被真消费**：`build_phase3_constraints` 抽出 typography 当**硬约束**（每个角色的字体/字号/字重逐条列），再抽 Components / Elevation / Shapes / Do's-and-Don'ts 四段 prose，把里面的 `{colors.xxx}` 解析成 hex，组成"必须遵守的视觉契约"放在 prompt **最末段**强化注意力。
- **图片/图标/公式都是占位符**：图片写 `<image href="__IMAGE_HERO__">` 之类、图标写 `<use data-icon="名">`、公式打 `data-latex`，由后处理统一替换成真位图 / Lucide 图标 / 渲染公式。`--debug` 模式不发占位符，改让 LLM 画虚线框+灰字描述。
- **布局模式共 13 种**：center_hero / vertical_list / bento_2col_equal / bento_2col_asymmetric / bento_3col / hero_top_cards_bottom / cards_top_hero_bottom / hero_with_microcards / mixed_grid / full_image / timeline / comparison / relation。

---

### Phase 4 — 把"野生 SVG"驯成 PPT 安全格式（五步流水）

LLM 吐的 SVG 是"野生的"：文字溢出、坐标越界、混 emoji / CSS 动画 / `<foreignObject>`、用 PPT 不认的字体、甚至 XML 不合法。Phase 4（`_phase4_postprocess`，每页一个线程并行）把它驯化。先存一份 LLM 原文到 `slides_raw/`，再走五步，最后写 `slides/slide_NN.svg`：

1. **确定性修复 `validate_and_fix`**（`svg_validator.py`，2200 行，~25 类规则）：先做字符级预清洗（HTML 实体转数字实体、裸 `&`/`<` 转义，否则 lxml 直接解析失败），再用一个**写死安全配置的解析器**（`resolve_entities=False, no_network=True`，**这就是防 XXE 的关键**——禁实体展开、禁联网取 DTD，挡住恶意 SVG 读本地文件）。然后按**硬依赖顺序**跑规则：必须先 `_wrap_long_text`（长文折成多行）→ 再 `_clamp_boundaries`（坐标夹进安全区 x∈[50,1230]、底边≤660/720）→ 再 `_fix_text_overlaps`（重叠文字往下推）。顺序不能乱：没折行算出的高度是错的，clamp 会按错高度裁；重叠检测又依赖 clamp 后的最终坐标。
2. **LLM 审阅 `review_and_fix_svg`**：受**成本门控**——没 warning 不审、warning 全是"已自动修好"的小问题也不审，只有出现真问题才花一次 LLM 调用。**它是单次 LLM pass，无渲染、无重试**（注意：这与 CLAUDE.md 宣称的"cairosvg 直渲→审查→修→再生成迭代循环"**不符**，见技术债）。**占位符保护**：审前审后比对 `__IMAGE_xxx__` 占位符计数，LLM 要是把图片占位弄丢了，直接丢弃审阅结果、退回审前 SVG。
3. **`sanitize_for_ppt`**：删 `<script>` / `on*` 事件 / emoji / 注释 / root 宽高，拍平嵌套 tspan，圆形序号的 y 吸附到圆心。
4. **三个注入**：`render_latex_formulas`（公式渲成图）→ `embed_icon_placeholders`（图标换成 Lucide SVG）→ `_inject_images`（图片占位换成真图 base64，>800px 缩到 800 宽，无透明走 JPEG、有透明走 PNG）。

**旁路 style_linter**：它**不在** Phase 4 逐页跑，而是挂在 `resolve_style` 末尾，由 Phase 1b 收尾和 render 入口对 DESIGN.md 调用。WCAG 阈值：正文/标题 4.5:1、大字/图标/装饰 3.0:1。palette broken-ref 必抛错；contrast 默认 warning（`EDUPPTX_LINT_STRICT=1` 升级为 error）。

---

### Phase 5 — SVG → DrawingML → PPTX（卖点落地的地方）

这是"打开即可编辑"真正兑现的一步：`output/svg_to_shapes.py` 是一个 **1899 行、零第三方依赖的手写 SVG 子集解释器**（只用 stdlib 的 xml.etree + PIL 读图片尺寸），逐个元素翻译成 PowerPoint 原生形状。

- **元素映射**：`rect`→圆角矩形/矩形（有 rx 算圆角）、`circle/ellipse`→椭圆、`line/path/polygon`→自由几何 custGeom、`text`→文本框、`image`→图片、`g`→递归并把父级的 translate/scale/fill 下传、`use`→从 `<defs>` 内联展开。**单个元素转换出错只 skip 它、不毁整页**。
- **坐标 1 SVG px = 9525 EMU**：PowerPoint 内部单位叫 EMU，96 DPI 下 1 像素正好 9525 EMU，所以画布 1280×720 直接映射到 12192000×6858000 EMU（标准 16:9）。字号另算（1px≈0.75pt，单位 1/100 pt）。
- **最难的是 path→custGeom 的椭圆弧**：PPT 自由几何只认直线和三次贝塞尔，不认 SVG 的二次贝塞尔（Q/T）和椭圆弧（A）。转换器把相对坐标转绝对、H/V 补全、S/T 按反射补控制点、Q 升三次、**A 命令按 SVG 规范做端点参数化、每 90° 切一段用经典 `4/3·tan(Δθ/4)` 公式近似成三次贝塞尔**——教科书级正确。
- **CJK 双字体**：一个文字 run 同时写 `<a:latin>` 和 `<a:ea>`，PPT 按字符自动选东亚字体（默认 Noto Sans SC），中文走 ea、英文走 latin。文本框宽度还有一套启发式（按字符类型估宽 + 找包住文字的卡片矩形 + 探测右侧同行兄弟文本推断列宽）——专治"LLM 把多列文字塞进一个大框"。
- **两种打包模式**（`pptx_assembler.py`）：
  - **native（默认）**：用 python-pptx 只生成一个带正确版式/主题的**空壳**，再把它当 ZIP 解开，把每页 `slideN.xml` 整个换成手写的 DrawingML，写图片、补关系，重新 ZIP。
  - **embed（`--embed`）**：每页放一张图，用微软的 `asvg:svgBlip` 扩展**双嵌入**——主显 cairosvg 渲染的 2560×1440 PNG，扩展里挂原始 SVG，支持的客户端显示矢量、不支持的退回 PNG。
  - **单页 native 转换失败 → 自动把那一页降级成 embed**，其余页仍是原生形状，一页畸形不拖垮整份。

---

## 3. 复用库子系统（项目重心，已重构）

### 3.1 结构：从 8896 行单体到 `edupptx/reuse/` 子包

旧版 `materials/ai_image_asset_db.py` 是一个 8896 行的单体，扛建库/检索/入库/policy/review 全部职责，是公认的"维护性炸弹"。**Phase A 重构已把它绞杀成 19 个按职责切分的模块**（`edupptx/reuse/`，合计 9572 行），原文件退化为 **1126 行、0 自有定义的纯 re-export shim**，只为保持旧 import 路径可用。

| 模块 | 行 | 职责 |
|---|---:|---|
| `_decide.py` | 1635 | policy 阈值决策 + 多库合池 + review 编排（读路径主入口 `find_reusable_ai_image_asset`） |
| `_store.py` | 971 | 复用库存储后端（split JSON / sqlite 读写） |
| `_embedding.py` | 902 | 向量嵌入（编码、npz sidecar、断点续传） |
| `_scoring.py` | 701 | BM25 / embedding / substring / policy 打分 |
| `_keywords.py` | 699 | 关键词路由与富化 |
| `_materialize.py` | 667 | 复用产物落地 |
| `_build.py` | 638 | 建库 / 写索引 |
| `_ingest.py` | 575 | 后台入库队列编排 |
| `_retrieve.py` | 554 | 三路召回 + RRF 合池 |
| `_constants.py` | 444 | 阈值与权重常量**单一来源** |
| `_gates.py` | 308 | 语义/宽高比/重叠门控 |
| `_normalize.py` | 302 | 字段归一化 |
| `_debug.py` | 266 | per-query 调试记录 |
| `_review.py` | 246 | LLM 灰区审阅 |
| `_vlm.py` | 191 | VLM 入库审查（默认关） |
| `_util.py` / `_assets.py` / `_backend.py` / `_context.py` | 77/310/46/33 | 工具 / 资产对象 / 后端分发 / 检索上下文 |

### 3.2 读路径（检索 / 裁决）

```
find_reusable_ai_image_asset → 硬过滤 eligible_assets
  → 三路并行召回(BM25 + Qwen3-Embedding + substring，各取 pool_limit=max(20, limit))
  → _rank_hybrid_reuse_candidates(RRF K=60 仅合池，RRF 分仅作审计)
  → policy_score = (0.25·kw + 0.55·emb)/0.80   ← 唯一裁决分（R2b：substring 已折进 kw，不再独立计）
  → decide_reuse 三档: ≥0.75 且 gap≥0.02 直接复用 / <0.35 拒 / 灰区进 LLM review@0.60
  → R5 near-miss VLM 兜底(需 vlm_client，生产默认走不到)
```

- **policy_score 是唯一裁决分**，RRF 只用于合池召回、不参与高低切（核心设计，代码反复落实）。
- **BM25 真实 IDF（R3）**：有全库语料统计（df/N/avgdl，按 library_root 缓存）时用真 IDF；无则回落旧伪语料（N=2，匹配词 IDF 恒 0.182 无区分力）。
- **缓存容器 `ReuseSearchContext`**：library_cache / route_index_cache / query_embedding_cache（RLock 保护），避免一个 PPT 多图槽重复读 sidecar、重编码 target 向量。
- query 端向量编码失败不静默——大声 warning + 降级 text-only，绝不伪装成"向量可用但全拒"。

### 3.3 写路径（建库 / 入库）

- 全程**原子落盘**（.tmp → os.replace）。
- split 索引拆 4 个语义组 JSON + background.json；C03 组追加 C01→C03 secondary projection；C00 组写前与磁盘旧文件 union（跨 run 累积）。
- 向量 npz sidecar 按 batch 编码、每批 checkpoint，断点续传，未变向量免重算。
- 异步入库队列：WAL + `BEGIN IMMEDIATE` 租约**全局串行化**（同时只一个 job，防 split JSON 互覆）。
- **去重分两套**：入库 merge 只按 asset_id 精确去重；感知去重（sha256 + dHash + 颜色签名 + 文本 2-gram Jaccard）是 build-time 离线工具，不在 per-session 路径上。

### 3.4 双后端

`json`（默认，split JSON + npz）| `sqlite`（opt-in，`EDUPPTX_REUSE_BACKEND=sqlite`，单文件 `library.db` + sqlite-vec，已在 zlz 真实库 A/B 验证两后端等价；需先 `edupptx assets migrate`）。

---

## 4. 子系统成熟度地图

| 子系统 | 判定 | 依据 |
|---|---|---|
| 管线编排 | 🟢 solid | 错误处理一致、防御性强，可选步骤全 try/except 后继续不 abort |
| Phase 1 规划 | 🟢 solid | JSON 四级降级、DESIGN.md 双路径+兜底永不阻塞、reveal 页码重排完整 |
| Phase 3 SVG 生成 | 🟢 solid | 路由分层（确定性打分+LLM 兜底+放弃）、prompt 工程细致 |
| Phase 4 校验 | 🟢 solid | "被生产数据反复教育过"，每条规则有针对性豁免、防 XXE |
| Phase 5 SVG→PPTX | 🟢 solid | 手写解释器覆盖完整、椭圆弧教科书级、单元素失败只 skip 不炸页 |
| 复用·读路径 | 🟢 solid | 裁决链自洽、policy 单一裁决、R2b/R3 修复带 goldset A/B 把关 |
| 复用·写路径 | 🟢 solid | 原子落盘 + checkpoint + 租约串行化，多轮加固 |
| 复用·代码组织 | 🟢 solid（**已重构**） | 8896 行单体 → 19 模块子包，旧"维护性炸弹"已拆 |
| 素材生成 | 🟢 solid | 小而稳，provider 全有容错降级 |
| 地基层 | 🟢 solid | LLM 输出三重归一化、三厂商分支、配置遵循三件套 |
| **复用·支撑层** | 🟡 **rough** | `vlm_asset_enricher` 未收尾（过期 prompt + 死代码） |
| **测试体系** | 🟡 **rough** | "一条腿粗"——复用库测试扎实，主线 SVG 管线几乎裸奔 |

---

## 5. 技术债与缺口清单

> 全部基于读真代码。优先级 🔴 高 / 🟡 中。**已修项见末尾"本轮已清"。**

### 🔴 测试覆盖严重失衡
- **SVG→PPTX 输出层几乎零测试**：`svg_to_shapes.py`(1899 行核心)、`pptx_assembler.py`、`svg_generator.py`、`svg_reviewer.py`、`document_parser.py`、`web_researcher.py`、`pixabay.py`、`unsplash.py` —— 多数在 `tests/` 零直接引用。rect→custGeom / EMU 坐标 / CJK 字体等关键逻辑无回归保护。
- 测试体量大头压在复用库；主线靠 `test_agent_*`（高度 monkeypatch）间接覆盖，非真实端到端。
- `test_reuse/` 评测依赖真实库 + 真实 LLM key，非 CI 可无人值守。

### ℹ️ visual_qa：刻意保持轻量（非债）
`tests/visual_qa.py` 的 `analyze_pptx` 已实现对最终 PPTX 的几何体检（4 类：重叠>10% / 文字溢出 / 越界 / 空旷>70%，severity 分级），并接进 `edupptx gen --qa`（opt-in）。**重型集成（LibreOffice→PNG 像素渲染 + pytest 包装 + 每次生成自动跑）按用户决定刻意不做**（太重：系统依赖 + 需真实 LLM + 拖慢主路径）。原 CLAUDE.md `## Self-Validation` 指令已移除。

### 🟡 文档与代码不符（多处）
- **reviewer 不是迭代循环**：CLAUDE.md 说 reviewer 是"cairosvg 直渲→审查→修→再生成迭代循环"，实际 `review_and_fix_svg` 是**单次 LLM pass**（无渲染、无重试、无收敛判断）。cairosvg 只在 `pptx_assembler.py`/`icons.py`，不在 review 回路。
- **布局模式数量**：CLAUDE.md 顶部写"11 种布局模式"，代码 `LayoutHint` 实为 **13 种**。
- **目录结构图过时**：CLAUDE.md 把复用代码列在 `materials/ai_image_asset_db.py` 名下，未反映 `edupptx/reuse/` 子包；页型参考模板已从 `design/style_templates/` 搬到 `design/page_templates/{复用,低年级,高年级}/`。

### 🟡 vlm_asset_enricher 未收尾
- prompt 仍写"7 个类别 C00-C06"，但实际只有 4 类 C00-C03，框架文字与注入规则自相矛盾，会诱导 VLM 输出非法类别。
- `VLM_REDESCRIBE_SYSTEM_PROMPT` 被连续赋值两次，第一版是死代码。
- 默认关闭、"未充分调试"。

### 🟡 Phase 3 模板资产 / 代码契约不匹配
- `bar/line/pie/kpi` 四图表模板**未被消费**（chart 映射只有 timeline/relation）。
- `data`/`case` 页型**永远走无模板分支**（页型→模板映射没它们）。
- reuse 族 `summary.svg` 的预览叫 `summery.png`（拼写）。

### 🟡 SVG→PPTX 几何简化
- 坐标变换是"累加 translate + 乘积 scale"，**非完整 2D 仿射矩阵**：`<g>` 上的 `rotate`/`matrix()` 被忽略，带旋转的组渲染错位（注：`<path>` 自身 transform 里的 rotate 是有读的）。
- `radialGradient` 固定输出居中圆，忽略 cx/cy/fx/fy/r。
- `build_shadow_xml` 把 dx 钳到非负，dx<0（向左偏移）阴影方向算错。
- `convert_use` 用临时 set/del 原地改 defs 共享元素，非线程安全且缺 finally 还原。
- 路径包围盒只采样锚点/控制点，不解贝塞尔极值，曲线鼓出盒外部分可能被裁。

### 🟡 其他卫生 / dead config
- `cli.py` reuse-check 命令的中文 help 是**乱码**（编码事故，单点）。
- `style_linter` 只检查 ResolvedStyle 固定语义色对，**不检查 SVG 实际渲染出的任意颜色对**——LLM 自选配色对比度无人把关。
- 入库队列全局串行化（有意，防互覆）但吞吐上限低；库变大后每 job 全量重写 split JSON，**写放大明显**。
- 入库期无感知去重：同图换 asset_id 重入会当新图收录，靠离线脚本事后清。
- `config.py` `web_search` 字段不从 env 解析（仅 CLI flag）；`style_schema.py` `slide_overrides` 定义但 resolve_style 全程未消费（dead config surface）。

### ✅ 本轮已清（旧版列为债，现已修）
- ~~8896 行单体"维护性炸弹"~~ → Phase A 重构成 `reuse/` 19 模块子包。
- ~~伪 BM25 反向 IDF 病理~~ → R3 真实语料 IDF（拿不到统计才回落）。
- ~~substring 双重计数（policy 里独立 0.20·sub）~~ → R2b 移除独立项，substring 只在 keyword_score 内算一次。
- ~~三档检索阈值表 dead config（`PAGE_IMAGE_REUSE_GATE_THRESHOLDS`/`GATE_THRESHOLDS`）~~ → Phase B-0 已从全仓删除。
- ~~`convert_path` scale 分量静默丢失~~ → 现 scale_x/scale_y 独立传入并应用。

---

## 6. 整体评价与建议优先级

**主线管线成熟可用**：0→5 阶段每环都是被真实 LLM 输出迭代打磨过的生产代码，不是脚手架。错误处理纪律一致（可选步骤全降级不 abort），CJK / 数学公式 / 占位符保护这些真实坑都踩过补过。

**结构性问题现在只剩一个半**：
1. **测试投入与代码重心错配**——测试大头在复用库，而**项目卖点（SVG→可编辑 PPTX）的输出层几乎零测试**。`svg_to_shapes` 改坏没有自动化能拦。这是当前最该补的。
2. ~~8896 行复用核心是维护性炸弹~~ —— **已由 Phase A 重构解决**（19 模块子包，shim 保兼容）。

**本地可做的优先级**：

| 优先级 | 动作 | 价值 |
|---|---|---|
| 1 | 补 SVG→PPTX 输出层回归测试 + 把 `visual_qa.py` 接进 Phase 5（兑现 CLAUDE.md） | 补上项目卖点的质量空洞 |
| 2 | 同步 CLAUDE.md（11→13 布局、reviewer 非迭代、`reuse/` 子包、模板目录搬迁） | 文档与代码对齐 |
| 3 | 清理 `vlm_asset_enricher` 过期 prompt + 死代码、cli.py 乱码 help | 低风险卫生 |

**复用库下一程（数据受限）**：R4 Chinese-CLIP / M-11 RetrievalUnit 需要"复用密集（含跨页复用、背景复用）的测试 session"来扩 goldset——当前 goldset 23 个正例全是 C02/C03，0 个 C01/background，gate 够不到那些路径。先有数据才能继续推。

---

## 7. 附录

### 7.1 关键常量

| 常量 | 值 | 出处 |
|---|---|---|
| EMU_PER_PX | 9525 | `svg_to_shapes.py`（1 SVG px = 9525 EMU） |
| 画布 | 1280×720（=12192000×6858000 EMU，16:9） | viewBox |
| 三路权重 | BM25 0.25 / embedding 0.55 / substring 0.20 | `reuse/_constants.py` |
| **policy_score** | **(0.25·kw + 0.55·emb)/0.80**（R2b：substring 折进 kw，不独立计） | `reuse/_scoring.py` |
| BM25 IDF | 有全库 df/N/avgdl 用真 IDF，否则回落 N=2 伪语料（R3） | `reuse/_scoring.py` |
| 复用阈值 | T_DIRECT=0.75 / T_REJECT=0.35 / T_GAP=0.02 / LLM review 0.60 | `materials/reuse_policy.py` |
| 背景阈值 | 0.38（prompt 0.85 + color_bias 0.15 加权） | `reuse` |
| RRF K | 60（仅合池，不裁决） | `reuse/_retrieve.py` |
| LLM | timeout=300s / max_retries=1 / 豆包 thinking:disabled | `llm_client.py` |
| SVG 生成 | max_tokens=16384 / temperature=0.7 / 每份模板参考截 3000 字 | `svg_generator.py` / `prompts.py` |

### 7.2 关键环境开关（详见 README / .env.example）

| 开关 | 默认 | 作用 |
|---|---|---|
| `EDUPPTX_VISUAL_PLANNER_FORMAT` | json | json 旧路径 / design_md 新 8 段路径 |
| `EDUPPTX_LINT_STRICT` | 0 | 1 时 contrast warning 升级为 error |
| `EDUPPTX_DISABLE_AI_IMAGE_REUSE` | 0 | 1 关闭复用读路径 |
| `EDUPPTX_REUSE_BACKEND` | json | sqlite 切 library.db 后端（需先 `assets migrate`） |
| `EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS` | 0 | 1 仅 BM25+substring（policy 退化为纯关键词分） |
| `EDUPPTX_REUSE_POLICY_WORKERS` | 5 | Phase 2b policy 并行 worker 数 |
| `REUSE_LIBRARY_DIRS` | — | 复用检索库目录列表 |

### 7.3 CLI 命令面

- **主路径**：`gen` / `render`（从 plan.json）/ `plan`
- **复用运维**：`reuse-check` / `asset-ingest` / `embedding-build` / `strict-reuse-classify` / `strict-reuse-export-check` / `vlm-enrich` / `assets migrate|export|doctor`
- **风格**：`styles`（含 convert）

### 7.4 关键设计决策（代码自证）

1. **SVG 作为设计中间格式**：LLM 擅长生成 SVG，但 SVG 嵌 PPTX 不可编辑 → 逐元素转原生形状。
2. **policy_score 单一裁决**：三路召回只产 rank（无权 RRF 入池），policy_score 是唯一驱动 decide_reuse 的分。
3. **读路径不写库（sqlite）**：sqlite 后端纯读；json 后端读时仍可能 lazy 重建 sidecar（已 single-flight，写本原子）。
4. **背景 content/final prompt 分离**：换配色不影响命中可复用背景。
5. **零依赖手写 SVG→DrawingML**：符合"最小依赖/显式优于巧妙"，但 SVG 特性支持范围由 1899 行手工决定（见 §5 几何简化）。

---

*本文档为现状快照（master `e51c498`）。代码演进后请同步更新，或移入 `docs/_archive/` 标注日期。*
