# 教育页面类型定义 (Page Types Reference)

本文件定义 5 种教育场景专用页面类型，每种类型包含布局说明、精确坐标和完整 SVG 示例片段。
LLM 生成对应类型幻灯片时，应参照此文件的坐标和结构。

画布规格：`viewBox="0 0 1280 720"`，固定 16:9。

---

## 1. quiz — 练习检测页

**适用场景**：选择题、判断题、填空题等课堂练习与知识检测。

### 布局说明

- 题目大卡片居于上方，醒目突出
- 4 个选项卡片 2×2 网格排列在下方
- 题号用主题色圆圈标注，选项字母用次主题色圆圈标注

### 坐标规格

| 区域     | x   | y   | w    | h   |
|--------|-----|-----|------|-----|
| 题目卡   | 50  | 100 | 1180 | 220 |
| 选项 A  | 50  | 340 | 570  | 150 |
| 选项 B  | 640 | 340 | 590  | 150 |
| 选项 C  | 50  | 510 | 570  | 150 |
| 选项 D  | 640 | 510 | 590  | 150 |

### SVG 示例

```xml
<!-- quiz: 练习检测页 -->
<rect x="50" y="100" width="1180" height="220" rx="16"
      fill="{card_bg_color}" stroke="{primary_color}" stroke-width="2"/>
<!-- 题号圆圈 -->
<circle cx="110" cy="210" r="30" fill="{primary_color}"/>
<text x="110" y="217" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="22" font-weight="bold" fill="white">1</text>
<!-- 题干文字 -->
<text x="160" y="195"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="24" font-weight="600" fill="{heading_color}">以下关于光合作用的描述，正确的是？</text>
<text x="160" y="235"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" fill="{text_color}">光合作用发生在叶绿体中，利用光能将 CO₂ 和 H₂O 合成有机物并释放 O₂</text>

<!-- 选项 A -->
<rect x="50" y="340" width="570" height="150" rx="12" fill="{card_bg_color}"/>
<circle cx="110" cy="415" r="26" fill="{secondary_color}"/>
<text x="110" y="422" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="20" font-weight="bold" fill="white">A</text>
<text x="155" y="420"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="19" fill="{text_color}">光合作用只在白天进行</text>

<!-- 选项 B -->
<rect x="640" y="340" width="590" height="150" rx="12" fill="{card_bg_color}"/>
<circle cx="702" cy="415" r="26" fill="{secondary_color}"/>
<text x="702" y="422" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="20" font-weight="bold" fill="white">B</text>
<text x="747" y="420"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="19" fill="{text_color}">产物是有机物和氧气</text>

<!-- 选项 C -->
<rect x="50" y="510" width="570" height="150" rx="12" fill="{card_bg_color}"/>
<circle cx="110" cy="585" r="26" fill="{secondary_color}"/>
<text x="110" y="592" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="20" font-weight="bold" fill="white">C</text>
<text x="155" y="590"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="19" fill="{text_color}">线粒体是光合作用的场所</text>

<!-- 选项 D -->
<rect x="640" y="510" width="590" height="150" rx="12" fill="{card_bg_color}"/>
<circle cx="702" cy="585" r="26" fill="{secondary_color}"/>
<text x="702" y="592" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="20" font-weight="bold" fill="white">D</text>
<text x="747" y="590"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="19" fill="{text_color}">暗反应不需要光能</text>
```

### 伪动画答案揭晓规则

适用于同一道题拆成“题目页 + 答案揭晓页”的 `quiz` / `exercise` 页面：

- 第二页必须完整复用第一页的题目卡、选项卡、留白区、题号圆、选项字母圆和所有文字块的位置与尺寸
- 不允许重新估算题目卡或选项卡坐标；如果系统提供上一页 SVG，必须直接沿用其中已有元素的 `x`、`y`、`width`、`height`、`font-size`、换行与对齐方式
- 选择题 / 判断题：只允许新增正确选项的高亮描边、答案角标、勾选标记或浅色遮罩，不允许移动 A/B/C/D 卡片，也不允许重排选项文字
- 填空题 / 简答题：保留原题干、空格、下划线、答题区位置，只在原空位附近补答案，或在不遮挡原内容的位置新增“答案”小标注
- 答案揭晓页新增的是“叠加层”，不是“重做一页”；原有布局元素必须保持不动，避免前后页切换时错位

---

## 2. formula — 公式推导页

**适用场景**：数学/物理公式推导、化学方程式配平、逻辑推理步骤展示。

### 布局说明

- 步骤卡片纵向堆叠排列
- 每步之间用向下箭头连接
- 最后一步（结论）用强调色背景高亮显示
- 步骤编号用主题色圆圈标注，公式使用等宽字体

### 坐标规格

| 区域       | x   | y   | w    | h   |
|----------|-----|-----|------|-----|
| Step 1   | 50  | 100 | 1180 | 120 |
| 箭头 1→2  | 620 | 228 | 40   | 30  |
| Step 2   | 50  | 266 | 1180 | 120 |
| 箭头 2→3  | 620 | 394 | 40   | 30  |
| Step 3   | 50  | 432 | 1180 | 120 |
| 箭头 3→结  | 620 | 560 | 40   | 30  |
| 结论卡    | 50  | 598 | 1180 | 90  |

### SVG 示例

```xml
<!-- formula: 公式推导页 -->
<!-- Step 1 -->
<rect x="50" y="100" width="1180" height="120" rx="12" fill="{card_bg_color}"/>
<circle cx="105" cy="160" r="24" fill="{primary_color}"/>
<text x="105" y="167" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">1</text>
<text x="148" y="148"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">已知匀加速直线运动，初速度 v₀，加速度 a，时间 t</text>
<text x="148" y="178"
      font-family="Courier New, Consolas, monospace"
      font-size="20" font-weight="bold" fill="{heading_color}">v = v₀ + at</text>

<!-- 箭头 1→2 -->
<polygon points="640,228 655,228 648,258" fill="{primary_color}"/>

<!-- Step 2 -->
<rect x="50" y="266" width="1180" height="120" rx="12" fill="{card_bg_color}"/>
<circle cx="105" cy="326" r="24" fill="{primary_color}"/>
<text x="105" y="333" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">2</text>
<text x="148" y="314"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">位移等于速度对时间的积分，代入匀加速公式</text>
<text x="148" y="344"
      font-family="Courier New, Consolas, monospace"
      font-size="20" font-weight="bold" fill="{heading_color}">s = v₀t + ½at²</text>

<!-- 箭头 2→3 -->
<polygon points="640,394 655,394 648,424" fill="{primary_color}"/>

<!-- Step 3 -->
<rect x="50" y="432" width="1180" height="120" rx="12" fill="{card_bg_color}"/>
<circle cx="105" cy="492" r="24" fill="{primary_color}"/>
<text x="105" y="499" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">3</text>
<text x="148" y="480"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">消去 t，得到速度与位移的关系式</text>
<text x="148" y="510"
      font-family="Courier New, Consolas, monospace"
      font-size="20" font-weight="bold" fill="{heading_color}">v² = v₀² + 2as</text>

<!-- 箭头 3→结 -->
<polygon points="640,560 655,560 648,590" fill="{accent_color}"/>

<!-- 结论卡（accent 高亮） -->
<rect x="50" y="598" width="1180" height="90" rx="12" fill="{accent_color}" opacity="0.15"/>
<rect x="50" y="598" width="6" height="90" rx="3" fill="{accent_color}"/>
<text x="80" y="638"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">三个运动学公式相互联系，选用时根据已知量判断</text>
<text x="80" y="668"
      font-family="Courier New, Consolas, monospace"
      font-size="18" font-weight="bold" fill="{accent_color}">v=v₀+at  |  s=v₀t+½at²  |  v²=v₀²+2as</text>
```

---

## 3. experiment — 实验步骤页

**适用场景**：化学/物理/生物实验操作流程、器材介绍与步骤记录。

### 布局说明

- 左侧窄栏（3/10 宽）：列出实验器材，图标+名称
- 右侧宽栏（7/10 宽）：编号步骤卡片
- 右下角小卡片：实验结论，用强调色边框高亮

### 坐标规格

| 区域          | x   | y   | w   | h   |
|-------------|-----|-----|-----|-----|
| 左侧器材面板   | 50  | 100 | 340 | 560 |
| 右侧步骤面板   | 410 | 100 | 820 | 400 |
| 右下结论卡    | 410 | 520 | 820 | 140 |

### SVG 示例

```xml
<!-- experiment: 实验步骤页 -->
<!-- 左侧器材面板 -->
<rect x="50" y="100" width="340" height="560" rx="14" fill="{card_bg_color}"/>
<rect x="50" y="100" width="340" height="50" rx="14" fill="{secondary_color}"/>
<rect x="50" y="136" width="340" height="14" fill="{secondary_color}"/>
<text x="220" y="132" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">实验器材</text>
<!-- 器材条目（图标占位 + 名称） -->
<rect x="76" y="174" width="32" height="32" rx="6" fill="{secondary_bg_color}"/>
<text x="100" y="196" text-anchor="middle" font-size="16" fill="{secondary_color}">⚗</text>
<text x="126" y="196"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">锥形瓶 × 2</text>

<rect x="76" y="224" width="32" height="32" rx="6" fill="{secondary_bg_color}"/>
<text x="100" y="246" text-anchor="middle" font-size="16" fill="{secondary_color}">🧪</text>
<text x="126" y="246"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">导管</text>

<rect x="76" y="274" width="32" height="32" rx="6" fill="{secondary_bg_color}"/>
<text x="100" y="296" text-anchor="middle" font-size="16" fill="{secondary_color}">🔥</text>
<text x="126" y="296"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">酒精灯</text>

<rect x="76" y="324" width="32" height="32" rx="6" fill="{secondary_bg_color}"/>
<text x="100" y="346" text-anchor="middle" font-size="16" fill="{secondary_color}">📏</text>
<text x="126" y="346"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">量筒 100mL</text>

<!-- 右侧步骤面板 -->
<rect x="410" y="100" width="820" height="400" rx="14" fill="{card_bg_color}"/>
<rect x="410" y="100" width="820" height="50" rx="14" fill="{primary_color}"/>
<rect x="410" y="136" width="820" height="14" fill="{primary_color}"/>
<text x="820" y="132" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">实验步骤</text>
<!-- 步骤条目 -->
<circle cx="454" cy="190" r="18" fill="{primary_color}"/>
<text x="454" y="196" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="14" font-weight="bold" fill="white">1</text>
<text x="484" y="194"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">取适量石灰石放入锥形瓶，连接好导管装置</text>

<circle cx="454" cy="260" r="18" fill="{primary_color}"/>
<text x="454" y="266" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="14" font-weight="bold" fill="white">2</text>
<text x="484" y="264"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">缓慢滴入稀盐酸，观察气泡产生情况</text>

<circle cx="454" cy="330" r="18" fill="{primary_color}"/>
<text x="454" y="336" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="14" font-weight="bold" fill="white">3</text>
<text x="484" y="334"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">将导出气体通入澄清石灰水，记录现象</text>

<circle cx="454" cy="400" r="18" fill="{primary_color}"/>
<text x="454" y="406" text-anchor="middle" dominant-baseline="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="14" font-weight="bold" fill="white">4</text>
<text x="484" y="404"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" fill="{text_color}">用排水法收集气体，验证 CO₂ 性质</text>

<!-- 右下结论卡 -->
<rect x="410" y="520" width="820" height="140" rx="14" fill="{card_bg_color}"
      stroke="{accent_color}" stroke-width="2"/>
<rect x="410" y="520" width="6" height="140" rx="3" fill="{accent_color}"/>
<text x="438" y="552"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" font-weight="bold" fill="{accent_color}">实验结论</text>
<text x="438" y="580"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">石灰石与盐酸反应生成 CO₂：CaCO₃ + 2HCl → CaCl₂ + H₂O + CO₂↑</text>
<text x="438" y="610"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="{text_color}">CO₂ 使澄清石灰水变浑浊，验证产物正确</text>
```

---

## 4. comparison — 对比表格页

**适用场景**：多概念/多方案横向对比、性质参数列表、知识点异同分析。

### 布局说明

- 表头行使用主题色背景+白色文字
- 数据行交替使用两种背景色（card_bg_color / secondary_bg_color）
- 行间用细分隔线区分，列间可选分隔线
- 首列可用加粗字体作为行标签

### 坐标规格

| 区域       | x   | y   | w    | 说明          |
|----------|-----|-----|------|-------------|
| 表格区域   | 50  | 100 | 1180 | 整体容器      |
| 表头行    | 50  | 100 | 1180 | h=60，主题色背景 |
| 数据行 1  | 50  | 160 | 1180 | h=80，card_bg |
| 数据行 2  | 50  | 240 | 1180 | h=80，secondary_bg |
| 数据行 3  | 50  | 320 | 1180 | h=80，card_bg |
| 数据行 4  | 50  | 400 | 1180 | h=80，secondary_bg |
| 数据行 5  | 50  | 480 | 1180 | h=80，card_bg |
| 数据行 6  | 50  | 560 | 1180 | h=80，secondary_bg |

### SVG 示例

```xml
<!-- comparison: 对比表格页 -->
<!-- 表格外框 -->
<rect x="50" y="100" width="1180" height="560" rx="14" fill="{card_bg_color}"/>

<!-- 表头行 -->
<rect x="50" y="100" width="1180" height="60" rx="14" fill="{primary_color}"/>
<rect x="50" y="136" width="1180" height="24" fill="{primary_color}"/>
<text x="230" y="137" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">对比项目</text>
<text x="590" y="137" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">有丝分裂</text>
<text x="960" y="137" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="18" font-weight="bold" fill="white">减数分裂</text>

<!-- 列分隔线 -->
<line x1="410" y1="100" x2="410" y2="660" stroke="{secondary_bg_color}" stroke-width="1.5"/>
<line x1="780" y1="100" x2="780" y2="660" stroke="{secondary_bg_color}" stroke-width="1.5"/>

<!-- 数据行 1（card_bg） -->
<rect x="50" y="160" width="1180" height="80" fill="{card_bg_color}"/>
<text x="230" y="207" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" font-weight="600" fill="{heading_color}">发生场所</text>
<text x="590" y="207" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">全身所有细胞</text>
<text x="960" y="207" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">性腺（精巢/卵巢）</text>

<!-- 数据行 2（secondary_bg） -->
<rect x="50" y="240" width="1180" height="80" fill="{secondary_bg_color}"/>
<text x="230" y="287" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" font-weight="600" fill="{heading_color}">分裂次数</text>
<text x="590" y="287" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">1 次</text>
<text x="960" y="287" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">2 次（减数第一次 + 第二次）</text>

<!-- 数据行 3（card_bg） -->
<rect x="50" y="320" width="1180" height="80" fill="{card_bg_color}"/>
<text x="230" y="367" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" font-weight="600" fill="{heading_color}">子细胞数目</text>
<text x="590" y="367" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">2 个子细胞</text>
<text x="960" y="367" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">4 个子细胞（精子）/ 1+3（卵细胞）</text>

<!-- 数据行 4（secondary_bg） -->
<rect x="50" y="400" width="1180" height="80" fill="{secondary_bg_color}"/>
<text x="230" y="447" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" font-weight="600" fill="{heading_color}">染色体数目</text>
<text x="590" y="447" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">与亲代相同（2n）</text>
<text x="960" y="447" text-anchor="middle"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" fill="{text_color}">减半（n），为亲代的一半</text>

<!-- 行分隔线 -->
<line x1="50" y1="240" x2="1230" y2="240" stroke="{secondary_bg_color}" stroke-width="1"/>
<line x1="50" y1="320" x2="1230" y2="320" stroke="{secondary_bg_color}" stroke-width="1"/>
<line x1="50" y1="400" x2="1230" y2="400" stroke="{secondary_bg_color}" stroke-width="1"/>
<line x1="50" y1="480" x2="1230" y2="480" stroke="{secondary_bg_color}" stroke-width="1"/>
```

---

## 5. summary — 知识归纳页

**适用场景**：章节总结、知识点整理、考点归纳、易错点提示。

### 布局说明

- 分类卡片纵向堆叠排列（每张卡片含标题条 + 内容区域）
- 标题条使用次主题色背景+白色文字
- 底部设警示卡（易错点），使用浅红/琥珀色背景突出显示
- 内容区使用圆点列表（· 或 ●）

### 坐标规格

| 区域          | x   | y   | w    | h   |
|-------------|-----|-----|------|-----|
| 分类卡 1     | 50  | 100 | 1180 | 140 |
| 分类卡 2     | 50  | 260 | 1180 | 140 |
| 分类卡 3     | 50  | 420 | 1180 | 140 |
| 警示卡（易错）| 50  | 580 | 1180 | 100 |

### SVG 示例

```xml
<!-- summary: 知识归纳页 -->
<!-- 分类卡 1 -->
<rect x="50" y="100" width="1180" height="140" rx="12" fill="{card_bg_color}"/>
<rect x="50" y="100" width="1180" height="44" rx="12" fill="{secondary_color}"/>
<rect x="50" y="130" width="1180" height="14" fill="{secondary_color}"/>
<text x="90" y="128"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" font-weight="bold" fill="white">一、光合作用的场所与色素</text>
<!-- 内容列表 -->
<circle cx="82" cy="170" r="4" fill="{secondary_color}"/>
<text x="96" y="175"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="{text_color}">场所：叶绿体（类囊体薄膜进行光反应，基质进行暗反应）</text>
<circle cx="82" cy="200" r="4" fill="{secondary_color}"/>
<text x="96" y="205"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="{text_color}">色素：叶绿素 a/b（吸收红/蓝紫光）、类胡萝卜素（吸收蓝紫光）</text>

<!-- 分类卡 2 -->
<rect x="50" y="260" width="1180" height="140" rx="12" fill="{card_bg_color}"/>
<rect x="50" y="260" width="1180" height="44" rx="12" fill="{secondary_color}"/>
<rect x="50" y="290" width="1180" height="14" fill="{secondary_color}"/>
<text x="90" y="288"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" font-weight="bold" fill="white">二、光合作用的两个阶段</text>
<circle cx="82" cy="330" r="4" fill="{secondary_color}"/>
<text x="96" y="335"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="{text_color}">光反应：水的光解（产生 O₂ + [H]）、ATP 合成，依赖光照</text>
<circle cx="82" cy="360" r="4" fill="{secondary_color}"/>
<text x="96" y="365"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="{text_color}">暗反应：CO₂ 固定与 C3 还原（消耗 ATP 和 [H]），不直接需光</text>

<!-- 分类卡 3 -->
<rect x="50" y="420" width="1180" height="140" rx="12" fill="{card_bg_color}"/>
<rect x="50" y="420" width="1180" height="44" rx="12" fill="{secondary_color}"/>
<rect x="50" y="450" width="1180" height="14" fill="{secondary_color}"/>
<text x="90" y="448"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="17" font-weight="bold" fill="white">三、影响光合速率的因素</text>
<circle cx="82" cy="490" r="4" fill="{secondary_color}"/>
<text x="96" y="495"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="{text_color}">光照强度：低于光饱和点时，增强光照可提高速率</text>
<circle cx="82" cy="520" r="4" fill="{secondary_color}"/>
<text x="96" y="525"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="{text_color}">CO₂ 浓度：适当增加 CO₂ 可提高暗反应速率；温度影响酶活性</text>

<!-- 底部警示卡（易错点）—— 浅红背景 -->
<rect x="50" y="580" width="1180" height="100" rx="12" fill="#FFF0F0"
      stroke="#E57373" stroke-width="2"/>
<rect x="50" y="580" width="6" height="100" rx="3" fill="#E57373"/>
<text x="80" y="612"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="16" font-weight="bold" fill="#C62828">⚠ 易错点提示</text>
<text x="80" y="640"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="#B71C1C">暗反应≠不需要光！暗反应需要光反应产生的 ATP 和 [H]，无光条件下暗反应也会停止</text>
<text x="80" y="664"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      font-size="15" fill="#B71C1C">光合速率 ≠ 净光合速率，真正光合速率 = 净光合速率 + 呼吸速率</text>
```

---

## 颜色占位符说明

| 占位符                  | 含义                          |
|----------------------|------------------------------|
| `{primary_color}`    | 主题主色（标题、编号圆圈、表头背景）   |
| `{secondary_color}`  | 次主题色（选项圆圈、归纳标题条、图标色）|
| `{accent_color}`     | 强调色（结论高亮、警示边框）         |
| `{card_bg_color}`    | 卡片背景色（浅色或深色主题卡片底色）  |
| `{secondary_bg_color}`| 次背景色（表格交替行、图标底色）    |
| `{text_color}`       | 正文字色                        |
| `{heading_color}`    | 标题字色（比正文更深/更突出）       |
