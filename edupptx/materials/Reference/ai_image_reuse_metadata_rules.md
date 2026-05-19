# AI 图像复用元数据规则

只返回严格 JSON，顶层必须是 `assets` 数组。每个条目只能包含其 `asset_kind` 允许的字段。

## 页面图元数据

对于 `page_image`，只输出：

```json
{
  "asset_id": "",
  "normalized_prompt": "",
  "context_summary": "",
  "teaching_intent": "",
  "context_summary_keywords": [],
  "asset_category": "unknown",
  "constraints": [],
  "core_keywords": [],
  "semantic_aliases": {}
}
```

`constraints` 和 `core_keywords` 都必须直接基于 `content_prompt` 中的可见内容提取。`normalized_prompt` 只是简洁视觉描述,不能作为约束或关键词的唯一来源。

`constraints` 用于复用安全过滤,`core_keywords` 用于 BM25、embedding 和 substring 召回。

### 非空硬约束

任何 `page_image` 都必须输出至少 **1 条 constraints** 和 **≥3 个 core_keywords**；`asset_category` 不得为 `unknown`（若实在判别不出，选 `concept_scene` 或 `symbolic_material` 兜底）。

封面页（`page_type=cover`）不得因为「主题氛围」「整体装饰」等理由留空 constraints。封面图统一按下面三类拆解：

- **封面主视觉**：抽出画面里可见的主要实体作为 constraint，importance 按下方「entity 三步判别决策」判定。
- **封面艺术字标题**：标题文字本身按 `text.teaching_content imp=2` 抽——封面是少数允许把页面标题当可见 text 的场景；`core_keywords` 至少包含标题词本身 + 主题词。
- **封面装饰元素**：按 `object.decorative imp=0` 处理，但仍需出现在 constraints 列表中。

允许的 `asset_category` 取值：

`learning_behavior`, `generic_tool`, `generic_diagram`, `concept_scene`, `content_specific`, `character_action`, `emotion_scene`, `symbolic_material`, `unknown`。

`asset_category` 取值会直接影响 reuse 复用级别：

- `learning_behavior`、`generic_tool`、`generic_diagram` 这三类被视为通用装饰素材，**强制 loose**，不参与约束过滤，纯走 BM25 + embedding 召回。
- 其它类别（`content_specific`、`character_action`、`concept_scene`、`emotion_scene`、`symbolic_material`、`unknown`）由约束自身决定复用级别。

## 约束结构

每个约束必须使用：

```json
{
  "kind": "entity | object | action | scene | emotion | text | math | physics",
  "subtype": "",
  "value": "",
  "importance": 0,
  "confidence": 0.0,
  "evidence": "",
  "reason": ""
}
```

约束基本规则：

- 只从 `content_prompt` 中提取约束。
- `value` 必须是原子级短名词、物体名、可读文字、公式、物理量或短动作短语。
- 不要使用完整句子，也不要使用包含 `的` 的中文组合短语。
- 不要把风格、画法、图像质量、课堂用途、页面功能或泛化视觉修饰作为约束。

### 多主体并列拆分

当 `content_prompt` 列出 **N≥2 个并列实体**时，必须**逐个**抽为独立 constraint。**禁止**合并成「一群X」「各种X」「若干X」「多种X」「多个X」这类聚合 value——聚合写法会让复用候选无法逐一对应，任何「含有其中一两项」的候选都会被错误高分匹配。

每条独立 constraint 的 importance 按下方「entity 三步判别决策」独立判定。同一组并列实体可能拥有不同 importance：若是本课要让学生识别／认识的对象，每条都升 species_instance imp=2；若只是氛围装饰，每条保持 generic_class imp=0。

### kind 类型说明

- `entity`：具名人物、动物、角色或特定主体身份。
- `object`：关键物体、教学对象、工具、卡片、网格、形状或可见教学载体。
- `action`：可见动作、姿态、互动或行为。
- `scene`：地点、空间关系、场景或故事情境。
- `emotion`：可见情绪状态。
- `text`：可读汉字、词语、拼音、标签、答案或其他可见文字。**只在 `content_prompt` 明确指示画面「显示／标注／写有」该文字时抽**；仅作为「来源／出处／作品名／教学背景／引用」出现的标题或作品名**不抽**——它们是 metadata 而非画面元素。判别问：如果生成模型不画这几个字，画面看起来还合理吗？合理 → 不抽。**例外**：封面页的艺术字标题属于「画面就是这几个字」的场景，按 `text.teaching_content imp=2` 抽（见上方「非空硬约束」）。
- `math`：公式、数量、方程、不等式、计数或几何关系。
- `physics`：物理量、单位、公式、电路标注或实验标注。

### subtype 取值表（决定 importance 上限）

`subtype` 是对**这个 value 这个词**的分类，不是对整张图的分类。一张图通常有多个 constraints，每个独立分类。

| kind | 允许的 subtype | 含义 |
|---|---|---|
| entity | `named_individual` | 具名个体：真实人物、虚构角色名、品牌人物（史铁生、爱因斯坦、孙悟空、Elsa） |
| entity | `species_instance` | 不可类替换的具体物种／生物分类／文化符号实体。涵盖三种情形：（a）文学／教材绑定的特定生物；（b）本课目标是让学生识别／认识／区分的真实物种；（c）不可类替换的文化具象符号（龙、凤凰、麒麟） |
| entity | `role` | 角色、亲缘称谓、职业（妈妈、老师、医生、警察、农民） |
| entity | `generic_class` | 泛类生物或角色（小朋友、男孩、女孩、动物、植物）——具体物种名是否走这里，由「entity 三步判别决策」决定 |
| object | `teaching_carrier` | **硬教学载体**：替换会破坏教学事实，且在所教知识体系里有专用名称（田字格、五线谱、坐标轴、量杯、温度计、数轴、地图、笔顺箭头） |
| object | `layout_container` | **软载体 / 通用排版容器**：承载教学内容但容器本身可替换，替换不改变内容（卡片、表格、边框、纸张、课本、课文、笔、黑板、白板） |
| object | `scene_prop` | 与教学主题关联的物体（讲秋天页面的落叶、讲告别页面的火车） |
| object | `decorative` | 装饰或场景陪体（桌子、椅子、灯、窗、植物盆栽、文具） |
| action | `teaching_fact` | 动作本身即教学事实（笔顺、实验步骤、特定操作流程） |
| action | `generic_motion` | 通用动作（举手、读书、跑步、挥手、捡起） |
| text/math/physics | `teaching_content` | 教学内容（汉字、公式、单位、标签、可读答案） |
| text | `decorative_text` | 装饰性文字、品牌水印、无教学意义文字 |
| scene | `story_scene` | 与故事/教学绑定的具体场景（深夜医院、考场、田径场、教室特写） |
| scene | `generic_ambient` | 通用氛围场景（草地、天空、室内、户外） |
| emotion | `narrative_emotion` | 教学叙事关键情绪（着急、思念、愤怒、悲伤、惊讶） |
| emotion | `generic_ambient` | 通用氛围情绪（开心、温馨、平静） |

### importance 决策表

**默认 importance = 0。** 只有满足下列升级条件之一，才升到 1 或 2。

| subtype | importance |
|---|---|
| entity.named_individual | **2** |
| entity.species_instance | **2** |
| entity.role | 0（默认）或 1（仅当本页核心叙事就是这个角色，例如"母亲深夜送孩子求医"页里的妈妈） |
| entity.generic_class | 0（默认）或 1（仅当本页核心就是这个泛类，例如"动物分类"页里的"动物"）。**例外**：单主体卡通形象图（"X 的卡通形象/头像/特写"），X 即便属 generic_class，importance **不得低于 1**——单主体图换主体等于换图，imp=0 会让 BM25 高分但跨主体的候选直接通过 |
| object.teaching_carrier | **2** |
| object.layout_container | **0**（默认；通用容器一律 imp=0，**绝不升 imp=2**）|
| object.scene_prop | 1（与教学主题强相关）或 0 |
| object.decorative | **0** |
| action.teaching_fact | **2** |
| action.generic_motion | 1（页面动作核心，如"挥手告别"页里的挥手）或 **0**（背景动作） |
| text/math/physics.teaching_content | **2** |
| text.decorative_text | **0** |
| scene.story_scene | 1 |
| scene.generic_ambient | **0** |
| emotion.narrative_emotion | 1 |
| emotion.generic_ambient | **0** |

importance 等级语义：

- `importance=0`：软语义描述，**不参与确定性 filter**，只作为 embedding/BM25 召回信号。
- `importance=1`：有教学方向但可替换；embedding 高直接通过，灰区或低分**交由 LLM review**，永不直接 reject。
- `importance=2`：不可替换的硬事实；embedding 低或缺同 kind 约束**直接 reject**；text/math/physics 仍走 LLM 复核。

### importance 自检：可替换性判别

判断 importance 时用这个判别问题：

> 如果把这个 value 换成同 kind 的同类词，本页教学还**完全讲得通吗**？

- 完全讲得通（例如"小朋友读书"图里把小朋友换成另一个小朋友） → imp=0
- 部分讲不通，需要看上下文（例如亲情主题页里的"妈妈"换成"老师"） → imp=1
- 讲不通（例如"$E=mc^2$"换成另一个公式、"史铁生"换成"鲁迅"） → imp=2

写 `reason` 字段时建议显式包含这个判别结论，例如："角色可替换为任意成年女性，imp=0"。

### entity 三步判别决策

`entity` 子类型（`named_individual` / `species_instance` / `role` / `generic_class`）的选择，**只看"被替换后教学还成立吗"，不看字面词性**。按下面三步：

**第一步：判断"具体身份是否承载教学事实"**

把这个 value 换成同 kind 的另一个具体值（任意同类替换），本页的教学目标还讲得通吗？

- **完全讲不通**（教学事实就是这个具体身份） → 升 imp=2
- **部分讲不通**（替换改变叙事／情感） → imp=1
- **完全讲得通**（具体身份是占位） → imp=0

**第二步：在 imp=2 的情况下，决定走 `named_individual` 还是 `species_instance`**

- value 是**专有名／人名／角色名**（含姓氏+名字，或公认的命名个体：史铁生、爱因斯坦、孙悟空、Elsa） → `named_individual` imp=2
- value 是**具体物种／文化符号／生物分类**，满足下面任一 → `species_instance` imp=2：
  - 真实存在的特定动物／植物／微生物物种名
  - 文学／教材作品里有专属设定的角色化物种或生物
  - 不可类替换的文化具象符号

判别问：**X 的核心识别特征（外形／生态／文化含义）是教学要传授的内容吗？** 是 → species_instance。

**第三步：在 imp<2 的情况下，决定走 `role` 还是 `generic_class`**

- value 是**人物身份／亲缘／职业**（妈妈、医生、学生、邮递员） → `role`
- value 是**泛类指代**（动物、植物、人物、小朋友、男孩、孩子） → `generic_class`

落到下方「角色／亲缘／职业硬性兜底词表」的，强制 imp≤1，不可升 imp=2。

#### 判别推理对照表（按 value 性质 × 场景类型抽象，不绑定具体课文）

| value 性质 | 场景／教学定位 | subtype | importance |
|---|---|---|---|
| 真实物种／植物／微生物名 | 本课目标是识别／认识／区分这个物种 | `species_instance` | 2 |
| 真实物种／植物／微生物名 | 本课部分依赖其特征（季节符号、文化象征、特定生态） | `species_instance` | 1 |
| 真实物种／植物／微生物名 | 本课只是通用场景陪体（动物园、田野、水域等氛围） | `generic_class` | 0 |
| 文学／教材绑定的特定生物 | 故事主线主体 | `species_instance` | 2 |
| 文学／教材绑定的特定生物 | 通用占位／装饰 | `generic_class` | 0 |
| 不可类替换的文化具象符号 | 主体或核心装饰 | `species_instance` | 2 |
| 完整专有名／人名 | 任何场景 | `named_individual` | 2 |
| 亲缘／职业称谓 | 叙事核心角色 | `role` | 1 |
| 亲缘／职业称谓 | 通用陪体／占位 | `role` 或 `generic_class` | 0 |
| 泛类指代（人／动物／植物／儿童） | 本课核心就是这个泛类 | `generic_class` | 1 |
| 泛类指代 | 通用装饰／陪体 | `generic_class` | 0 |
| 单主体卡通形象图（"X 的形象／头像／特写"）的主视觉主体 | 任何场景 | 按以上规则但不低于 1 | ≥1 |

阅读方式：先看 value 是哪类（左列），再看本页对它的教学定位（中列），结果就是 subtype + importance。判别不出场景类型时，按"本页教学是否依赖该 value 的特定身份"回退到上方三步判别。

### object.teaching_carrier vs object.layout_container 判别

`object` kind 下"载体"分两层。判别问题：

> 如果把这个容器**换成同类的另一个容器**（保持承载的内容不变），教学事实还成立吗？

- 不成立 → `teaching_carrier`，imp=2（容器本身就是教学事实的一部分）
- 成立 → `layout_container`，imp=0（容器只是承载形式，可替换）

加分准则：`teaching_carrier` 通常在所教知识体系里有专用名称（汉字书写专用、音乐记谱专用、数学专用、计量专用）。`layout_container` 是通用工具或排版形式，跨学科通用。

对照判别示例：

| value | subtype | imp | 判别推理 |
|---|---|---|---|
| 田字格 | `teaching_carrier` | 2 | 换成普通方格 → 汉字书写规则丢失，教学事实变 |
| 卡片 | `layout_container` | 0 | 换成圆形泡 / 表格行 → 承载的字不变，教学等价 |
| 量杯 | `teaching_carrier` | 2 | 换成普通杯子 → 刻度信息丢失，教学事实变 |
| 杯子 | `layout_container` | 0 | 内容（液体描述）跟杯子形状无关 |
| 笔顺箭头 | `teaching_carrier` | 2 | 笔顺方向就是教学事实，不能换 |
| 笔 | `layout_container` | 0 | 写字过程才是教学，笔本身（铅笔/毛笔/钢笔）可替换 |
| 五线谱 | `teaching_carrier` | 2 | 音符位置依赖五条线，换网格谱就错 |
| 表格 | `layout_container` | 0 | 同样数据放在列表 / 树图也能讲清 |
| 数轴 | `teaching_carrier` | 2 | 数学概念依赖于"有序线性结构"的容器形式 |
| 课文 | `layout_container` | 0 | 教学内容是字词，载体（课文/段落/材料）可替换 |
| 黑板 / 白板 | `layout_container` | 0 | 板面只是书写表面，内容才是教学 |

注意：**`layout_container` 默认且永远 imp=0**。即使该容器在页面中很显眼，也不升 imp=1 或 2。容器的"重要性"用 importance=2 的同图教学内容（teaching_content / teaching_fact / teaching_carrier）来承担，而不是把容器自己抬级。

### 角色/亲缘/职业硬性兜底词表

如果 `value` 命中以下词表，`entity.subtype` 强制为 `role` 或 `generic_class`，`importance` 上限为 1。**不允许**这些词单独升到 imp=2。

```
亲缘称谓：爸爸 妈妈 爹 娘 父亲 母亲 妈 爸 爷爷 奶奶 外公 外婆 姥爷 姥姥
        叔叔 阿姨 伯伯 舅舅 姑姑 姨妈 哥哥 姐姐 弟弟 妹妹
        儿子 女儿 孙子 孙女 外孙 宝宝 宝贝

职业角色：老师 教师 学生 同学 医生 护士 警察 消防员 农民 工人 司机
        厨师 服务员 售货员 运动员 舞蹈家 画家 音乐家 科学家 工程师
        律师 法官 记者 园丁 清洁工 邮递员 教练

泛类指代：男孩 女孩 小朋友 孩子 小孩 男人 女人 人物 人 卡通人物
        动漫人物 动物 植物
```

例外：当 value 是"完整专有名字"（含姓氏+名字，例如"史铁生"、"爱因斯坦"）时，subtype 走 `named_individual`，imp=2，不受词表限制。

**此词表只为已知 LLM 高频翻车的泛类指代词兜底，不预防性扩展**。新出现的具体物种名、新角色名、新职业名都不应加进来——它们由「entity 三步判别决策」自动分流到正确的 subtype 和 importance。如果观察到 LLM 在某个新词上稳定翻车，再考虑加入；不要凭印象预先加。

## 页面图召回关键词

`core_keywords` 必须由 LLM 直接从 `content_prompt` 的可见内容中提取：

- 只包含少量、可见、有区分度、适合召回的原子级关键词。
- 实体、物体、动作、状态尽量拆开写。
- 建议 3-8 个；如果画面只有一个明确主体，可以少于 3 个。
- 不要输出整句式短语。
- 不要输出包含 `的` 的组合短语。
- 不要输出风格、画法、用途、页面功能、课堂属性或质量描述。
- 不要把修饰语和主体合并成硬绑定短语。

`semantic_aliases` 必须在 `core_keywords` 之后生成。每个 key 必须来自 `core_keywords`，value 是等价或近义短语，不得引入新的核心语义。

## 背景图元数据

对于 `background`，只输出：

```json
{
  "asset_id": "",
  "normalized_prompt": "",
  "context_summary": "",
  "teaching_intent": "",
  "core_keywords": [],
  "semantic_aliases": {},
  "context_summary_keywords": []
}
```

背景图的 `core_keywords` 由 LLM 直接从 `content_prompt` 生成，并由代码清洗。它们应描述可复用的背景色彩、情绪、空间、主题和视觉氛围。

## 少样本示例

少样本只为规则文字讲不清的判别灰区锚定边界，不重复规则已明确的情形。下面两条对应整套规则里最容易出错的两个判别面。

### 示例 1：layout_container vs teaching_carrier 的对照

`object` kind 下"载体"分两层，是规则里最容易混淆的判别面。同样的可读汉字放在不同容器里，subtype 和 importance 完全不同。

**A. 普通卡片承载汉字**（容器可类替换 → `layout_container` imp=0）

```json
{
  "asset_category": "content_specific",
  "constraints": [
    {
      "kind": "text",
      "subtype": "teaching_content",
      "value": "枚",
      "importance": 2,
      "confidence": 0.98,
      "evidence": "卡片上的可读汉字",
      "reason": "可读教学文字，imp=2"
    },
    {
      "kind": "text",
      "subtype": "teaching_content",
      "value": "爽",
      "importance": 2,
      "confidence": 0.98,
      "evidence": "卡片上的可读汉字",
      "reason": "可读教学文字，imp=2"
    },
    {
      "kind": "object",
      "subtype": "layout_container",
      "value": "卡片",
      "importance": 0,
      "confidence": 0.9,
      "evidence": "汉字承载形式",
      "reason": "换成圆形泡、表格行也能讲清，容器可替换，imp=0"
    }
  ],
  "core_keywords": ["枚", "爽", "卡片"]
}
```

**B. 田字格承载汉字 + 笔顺**（容器本身即教学事实 → `teaching_carrier` imp=2）

```json
{
  "asset_category": "content_specific",
  "constraints": [
    {
      "kind": "text",
      "subtype": "teaching_content",
      "value": "比",
      "importance": 2,
      "confidence": 0.98,
      "evidence": "教学核心是可读汉字'比'",
      "reason": "可读文字必须高置信匹配，imp=2"
    },
    {
      "kind": "object",
      "subtype": "teaching_carrier",
      "value": "田字格",
      "importance": 2,
      "confidence": 0.94,
      "evidence": "汉字书写的专用教学载体",
      "reason": "田字格不可替换为普通方格，imp=2"
    },
    {
      "kind": "action",
      "subtype": "teaching_fact",
      "value": "笔顺",
      "importance": 2,
      "confidence": 0.9,
      "evidence": "画面呈现笔顺信息",
      "reason": "笔顺是教学事实，不可替换，imp=2"
    }
  ],
  "core_keywords": ["比", "田字格", "笔顺"]
}
```

**判别面**：把容器换成同类的另一个容器，教学事实还成立吗？
- 成立（卡片 → 圆泡 → 表格行，承载的字不变） → `layout_container` imp=0。
- 不成立（田字格 → 普通方格，汉字书写规则丢失） → `teaching_carrier` imp=2。

### 示例 2：同一张图里 imp=2 / imp=1 / imp=0 并存

`importance` 是**对每个 value 独立判定**的，不是对整张图统一打分。下例展示 species_instance 主体（imp=2）+ 通用动作（imp=1）+ 装饰道具（imp=0）并存。这是 LLM 实测最容易把全部约束推到同一档的灰区。

```json
{
  "asset_category": "character_action",
  "constraints": [
    {
      "kind": "entity",
      "subtype": "species_instance",
      "value": "小蝌蚪",
      "importance": 2,
      "confidence": 0.95,
      "evidence": "画面主体是文学绑定的特定生物",
      "reason": "故事绑定物种不可跨物种替换，imp=2"
    },
    {
      "kind": "action",
      "subtype": "generic_motion",
      "value": "举旗子",
      "importance": 1,
      "confidence": 0.86,
      "evidence": "动作是本页主要语义",
      "reason": "页面动作核心，imp=1"
    },
    {
      "kind": "object",
      "subtype": "decorative",
      "value": "旗子",
      "importance": 0,
      "confidence": 0.84,
      "evidence": "旗子是动作的伴随物",
      "reason": "可替换为标语牌、横幅，imp=0"
    }
  ],
  "core_keywords": ["小蝌蚪", "举旗子", "旗子"]
}
```

**判别面**：同一张图里不同 constraint 的 importance 互相独立，每条按"换了讲不讲得通"单独判定，不互相绑定升降。

## 复用级别派生（仅供 LLM 自检）

代码会根据元数据派生 reuse_level：

- `asset_category` ∈ {learning_behavior, generic_tool, generic_diagram} → **loose**（硬置，不参与约束过滤）
- 存在 text/math/physics 且 imp=2 → **strict**
- 存在 entity.named_individual 且 imp=2 → **strict**
- 存在任何 imp=2 约束 → **medium**
- 存在 ≥2 个 imp=1 约束 → **medium**
- 其余 → **loose**

LLM 不需要输出 reuse_level；它会由代码根据 constraints 和 asset_category 派生。
