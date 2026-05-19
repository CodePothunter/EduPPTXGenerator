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

`constraints` 和 `core_keywords` 都必须直接基于 `content_prompt` 中的可见内容提取。`normalized_prompt` 只是简洁视觉描述，不能作为约束或关键词的唯一来源。

`constraints` 用于复用安全过滤，`core_keywords` 用于 BM25、embedding 和 substring 召回。

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

### kind 类型说明

- `entity`：具名人物、动物、角色或特定主体身份。
- `object`：关键物体、教学对象、工具、卡片、网格、形状或可见教学载体。
- `action`：可见动作、姿态、互动或行为。
- `scene`：地点、空间关系、场景或故事情境。
- `emotion`：可见情绪状态。
- `text`：可读汉字、词语、拼音、标签、答案或其他可见文字。
- `math`：公式、数量、方程、不等式、计数或几何关系。
- `physics`：物理量、单位、公式、电路标注或实验标注。

### subtype 取值表（决定 importance 上限）

`subtype` 是对**这个 value 这个词**的分类，不是对整张图的分类。一张图通常有多个 constraints，每个独立分类。

| kind | 允许的 subtype | 含义 |
|---|---|---|
| entity | `named_individual` | 具名个体：真实人物、虚构角色名、品牌人物（史铁生、爱因斯坦、孙悟空、Elsa） |
| entity | `species_instance` | 与特定故事/教材绑定的物种实例（"小蝌蚪找妈妈"里的小蝌蚪、"龟兔赛跑"里的乌龟） |
| entity | `role` | 角色、亲缘称谓、职业（妈妈、老师、医生、警察、农民） |
| entity | `generic_class` | 泛类生物或角色（小朋友、男孩、女孩、小猴子、动物） |
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
| entity.generic_class | 0（默认）或 1（仅当本页核心就是这个泛类，例如"动物分类"页里的"动物"） |
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

### 示例 1：卡通小朋友捧课本朗读（learning_behavior，全部 imp=0）

```json
{
  "asset_category": "learning_behavior",
  "constraints": [
    {
      "kind": "entity",
      "subtype": "generic_class",
      "value": "小朋友",
      "importance": 0,
      "confidence": 0.92,
      "evidence": "通用学习行为插画的主体",
      "reason": "可替换为任意儿童形象，imp=0"
    },
    {
      "kind": "object",
      "subtype": "decorative",
      "value": "课本",
      "importance": 0,
      "confidence": 0.86,
      "evidence": "学习行为的常规陪体",
      "reason": "可替换为书本、绘本，imp=0"
    },
    {
      "kind": "action",
      "subtype": "generic_motion",
      "value": "朗读",
      "importance": 0,
      "confidence": 0.85,
      "evidence": "通用学习动作",
      "reason": "可替换为读书、看书，imp=0"
    }
  ],
  "core_keywords": ["小朋友", "课本", "朗读"],
  "semantic_aliases": {
    "小朋友": ["儿童", "学生"],
    "朗读": ["读书", "诵读"]
  }
}
```

### 示例 2：妈妈和孩子在公园（concept_scene，亲情主题，imp=1 为主）

```json
{
  "asset_category": "concept_scene",
  "constraints": [
    {
      "kind": "entity",
      "subtype": "role",
      "value": "妈妈",
      "importance": 1,
      "confidence": 0.94,
      "evidence": "亲情主题页的核心人物",
      "reason": "本页讲亲子情感，妈妈是叙事主体；同类替换需 LLM 看图判断，imp=1"
    },
    {
      "kind": "entity",
      "subtype": "generic_class",
      "value": "孩子",
      "importance": 1,
      "confidence": 0.92,
      "evidence": "亲情主题页的核心配体",
      "reason": "本页讲亲子情感，孩子是必要参与方，imp=1"
    },
    {
      "kind": "scene",
      "subtype": "generic_ambient",
      "value": "公园",
      "importance": 0,
      "confidence": 0.86,
      "evidence": "通用户外氛围",
      "reason": "可替换为草地、户外，imp=0"
    }
  ],
  "core_keywords": ["妈妈", "孩子", "公园"],
  "semantic_aliases": {
    "妈妈": ["母亲", "母子"],
    "孩子": ["儿童", "小孩"]
  }
}
```

### 示例 3：深夜母亲背孩子奔向医院（character_action，故事场景）

```json
{
  "asset_category": "character_action",
  "constraints": [
    {
      "kind": "entity",
      "subtype": "role",
      "value": "母亲",
      "importance": 1,
      "confidence": 0.96,
      "evidence": "故事场景的叙事主角",
      "reason": "本页讲母爱叙事，母亲是必要主体，imp=1"
    },
    {
      "kind": "scene",
      "subtype": "story_scene",
      "value": "深夜医院门口",
      "importance": 1,
      "confidence": 0.92,
      "evidence": "故事关键场景",
      "reason": "场景指向具体叙事，跨场景复用需 LLM 看图，imp=1"
    },
    {
      "kind": "action",
      "subtype": "generic_motion",
      "value": "背孩子",
      "importance": 1,
      "confidence": 0.9,
      "evidence": "本页核心动作",
      "reason": "动作是叙事载体，imp=1"
    },
    {
      "kind": "emotion",
      "subtype": "narrative_emotion",
      "value": "焦急",
      "importance": 1,
      "confidence": 0.88,
      "evidence": "教学叙事关键情绪",
      "reason": "情绪指向具体叙事，imp=1"
    }
  ],
  "core_keywords": ["母亲", "孩子", "医院", "深夜", "背"],
  "semantic_aliases": {
    "母亲": ["妈妈"],
    "医院": ["急诊", "病房"]
  }
}
```

### 示例 4：田字格中的"比"字（content_specific，imp=2 教学事实）

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
      "evidence": "汉字书写的特定教学载体",
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
  "core_keywords": ["比", "田字格", "笔顺"],
  "semantic_aliases": {
    "比": ["比字"],
    "笔顺": ["书写顺序"]
  }
}
```

### 示例 5：史铁生肖像（content_specific，命名个体）

```json
{
  "asset_category": "content_specific",
  "constraints": [
    {
      "kind": "entity",
      "subtype": "named_individual",
      "value": "史铁生",
      "importance": 2,
      "confidence": 0.96,
      "evidence": "画面要求特定作者肖像",
      "reason": "命名个体不可替换，imp=2"
    }
  ],
  "core_keywords": ["史铁生", "肖像"],
  "semantic_aliases": {
    "史铁生": ["史铁生作者"]
  }
}
```

### 示例 6：小蝌蚪举旗子（character_action，故事绑定物种）

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
      "evidence": "画面主体是'小蝌蚪找妈妈'故事里的小蝌蚪",
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
  "core_keywords": ["小蝌蚪", "举旗子", "旗子"],
  "semantic_aliases": {
    "小蝌蚪": ["蝌蚪幼体"],
    "举旗子": ["挥旗"]
  }
}
```

### 示例 7：生字卡片图（layout_container 对比 teaching_carrier）

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
    },
    {
      "kind": "object",
      "subtype": "layout_container",
      "value": "边框",
      "importance": 0,
      "confidence": 0.85,
      "evidence": "卡片外框",
      "reason": "纯排版元素，可替换，imp=0"
    }
  ],
  "core_keywords": ["枚", "爽", "卡片"],
  "semantic_aliases": {
    "卡片": ["字卡"]
  }
}
```

**对照**：如果同一张图把"卡片"换成"田字格"（即"田字格里写着'枚'和'爽'"），则"田字格"应标 `teaching_carrier` imp=2，因为田字格不可替换。这就是 layout_container 与 teaching_carrier 的判别面。

## 复用级别派生（仅供 LLM 自检）

代码会根据元数据派生 reuse_level：

- `asset_category` ∈ {learning_behavior, generic_tool, generic_diagram} → **loose**（硬置，不参与约束过滤）
- 存在 text/math/physics 且 imp=2 → **strict**
- 存在 entity.named_individual 且 imp=2 → **strict**
- 存在任何 imp=2 约束 → **medium**
- 存在 ≥2 个 imp=1 约束 → **medium**
- 其余 → **loose**

LLM 不需要输出 reuse_level；它会由代码根据 constraints 和 asset_category 派生。
