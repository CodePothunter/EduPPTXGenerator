"""Material category classification utilities for AI image material libraries."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from edupptx.materials.ai_image_asset_db import (
    BACKGROUND_REUSE_INDEX_FILENAME,
    BACKGROUND_REUSE_INDEX_GROUP,
    DEFAULT_MATCH_INDEX_FILENAME,
)

STRICT_REUSE_CLASSIFIER_VERSION = 7
STRICT_REUSE_REVIEW_QUEUE_FILENAME = "strict_reuse_review_queue.jsonl"
STRICT_REUSE_REPORT_FILENAME = "strict_reuse_classification_report.json"
STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME = "manifest.json"
STRICT_REUSE_VISUAL_CHECK_HTML_FILENAME = "index.html"
STRICT_REUSE_VISUAL_CHECK_MODE = "strict-reuse-export-check"
STRICT_REUSE_INDEX_DIRNAME = "strict_reuse_indexes"

# --- 6 active material categories (v7; gapless C00-C05) ---
C00_STRICT_TEXT_PROBLEM_SKIP = "C00_strict_text_problem_skip"
C01_LANGUAGE_GLYPH_VISUAL = "C01_language_glyph_visual"
C02_STRUCTURE_DIAGRAM_VISUAL = "C02_structure_diagram_visual"
C03_IRREPLACEABLE_ENTITY_EVENT_ACTION = "C03_irreplaceable_entity_event_action"
C04_GENERIC_SUBJECT_OBJECT = "C04_generic_subject_object"
C05_SCENE_DECOR_CONTAINER = "C05_scene_decor_container"

# Backward-compatible constants. These names remain importable, but their
# values normalize to the current active category set.
C03_SPECIFIC_EVENT_INTERACTION = C03_IRREPLACEABLE_ENTITY_EVENT_ACTION
C04_TEACHING_BOUND_ENTITY = "C04_teaching_bound_entity"
C04_SINGLE_SUBJECT_ASSET = C04_GENERIC_SUBJECT_OBJECT
C05_GENERIC_SUBJECT_ASSET = C04_GENERIC_SUBJECT_OBJECT
C05_DECOR_LAYOUT_CONTAINER = C05_SCENE_DECOR_CONTAINER
C06_SCENE_DECOR_CONTAINER = C05_SCENE_DECOR_CONTAINER
C06_GENERIC_SCENE_ACTIVITY = C05_SCENE_DECOR_CONTAINER

MATERIAL_CATEGORIES = (
    C00_STRICT_TEXT_PROBLEM_SKIP,
    C01_LANGUAGE_GLYPH_VISUAL,
    C02_STRUCTURE_DIAGRAM_VISUAL,
    C03_IRREPLACEABLE_ENTITY_EVENT_ACTION,
    C04_GENERIC_SUBJECT_OBJECT,
    C05_SCENE_DECOR_CONTAINER,
)
_MATERIAL_CATEGORY_SET = frozenset(MATERIAL_CATEGORIES)

_LEGACY_CATEGORY_MIGRATION = {
    "c03_specific_event_interaction": C03_IRREPLACEABLE_ENTITY_EVENT_ACTION,
    "c04_teaching_bound_entity": C03_IRREPLACEABLE_ENTITY_EVENT_ACTION,
    "c04_single_subject_asset": C04_GENERIC_SUBJECT_OBJECT,
    "c05_generic_subject_asset": C04_GENERIC_SUBJECT_OBJECT,
    "c05_decor_layout_container": C05_SCENE_DECOR_CONTAINER,
    "c06_scene_decor_container": C05_SCENE_DECOR_CONTAINER,
    "c06_generic_scene_activity": C05_SCENE_DECOR_CONTAINER,
}

# Legacy v6 prompt before the 2026-05-28 boundary refinement.
# Kept for audit only; not used at runtime.
# OLD MATERIAL_CATEGORY_RULES_TEXT:
# ## strict_reuse_group 7 类分类规则（v6）
# 全局原则：只根据 content_prompt 字面描述判断。严禁参考 theme、subject、grade_norm 等元数据推断分类。按优先级从高到低检查，命中即停。分类依据是复用时哪些信息不可替换，而不是单个关键词是否出现。
#
# 复用宽松度谱系（严→松）：C00 不复用 → C01 符号匹配 → C04 身份匹配 → C03 事件匹配 → C02 结构匹配 → C05 类型匹配 → C06 语境匹配
#
# 0. C00_strict_text_problem_skip（精确教学载荷类 — 跳过复用）：
# 画面核心复用价值依赖不可替换的教学载荷，必须逐字、逐数值、逐符号精确一致。
# 对语言符号内容，3 个及以下明确语言符号本体教学优先归 C01；附带教学辅助标注（如宽窄比例提示、笔顺标注）不改变此判断。4 个或以上独立教学字词、整段课文、未展开外部指代、标题艺术字或主题文字，只要需要整体精确复现，归本类。
# 关键词触发条件：content_prompt 出现「课文」「段落」「片段」「原文」「全诗」「歌词」「童谣」等字样时，仅当文字/数据内容本身是画面核心复用价值时才归 C00。如果描述的是涉及文字的动作/活动/工具使用（如标记、圈画、标注序号），画面核心是动作而非文字内容，不归 C00，继续按 C05/C06 判断。
# 含具体数值且换数值就不能用的数学题/物理题归本类。无具体数值的几何/物理示意图不归本类，继续按 C02 判断。可替换短标签、栏目文字、装饰文字不因可读而归本类。
#
# 1. C01_language_glyph_visual（语言符号形式教学类 — 精确符号匹配）：
# 画面核心是少量（3 个及以下）明确语言符号本身，或其形、音、义、书写、部件、偏旁、构字、字源、演变、组词、搭配。即使画面使用拆分、箭头、对应、演变等图示方式，只要知识对象仍是语言符号本体，就不归 C02。4 个或以上字词或整段语言载荷需要整体复现时，优先按 C00 处理。语言学习中的流程、层级、方法等知识结构图示不归本类，归 C02。本类仅限语言符号（汉字、拼音、笔画等），数学/物理的图示归 C02。
#
# 2. C02_structure_diagram_visual（跨学科知识结构图示类 — 结构+主题匹配）：
# 画面核心是以图形方式呈现知识关系，知识对象不是语言符号本体，匹配关键在于结构/流程/关系走向 + 所属知识主题。
# 遮住测试：把图中具体文字/数值/名称全部遮住，只看布局/节点/连线/流程走向，还能判断这是什么类型的教学图吗？能→C02。不能→按内容判断其他类。
# 适用：思维导图、流程图、原理图（光路/几何概念/成像规律）、实验装置示意图（结构关系图，非实拍照片）、对比框架图、带位值/结构关系的教具模板（如数位表）、分类/关系图。
# 排除：含具体不可替换数值的图→C00；实验实拍场景（核心是可辨识器材/对象在演示现象→C05；大场景/环境→C06）；抽象艺术/装饰插画→C06；通用插画恰巧有组合布局→C05/C06；空白田字格/米字格/方格纸等纯承载容器→C06。
#
# 3. C03_specific_event_interaction（不可替换事件命题类 — 事件匹配）：
# 画面表达不可替换的事件命题。事件三要素必须同时满足：①有可辨识的主体（谁，可以是具名或泛化角色）；②有明确的行为/事件（做了什么/发生了什么）；③该行为具有叙事/情感意义，替换动作会改变画面的故事含义。
# 叙事意义测试：把动作替换成另一个日常动作，画面传达的故事/情感是否根本改变？是→C03；动作只是例行活动→C06。
# 排除：静态环境描写不因出自课文而归 C03→C06；单一主体+姿态不构成事件→C05；例行学习/生活活动（朗读、写字、举手、观察）→C06；具名角色共处但无明确行为→C04。
#
# 4. C04_teaching_bound_entity（教学指定实体类 — 严格身份匹配）：
# 画面核心是世界上唯一的/不可替换的特定实体。判断依据只看 content_prompt，严禁参考 theme/subject。
# 替换测试/唯一性测试：世界上只有这一个/这一位→C04；世界上有很多个同类型→C05。
# 适用：具名人物肖像、课文具名角色（content_prompt 中出现角色名）、具名/唯一地标建筑、具名文学作品实物、多个具名角色共处但无明确行为。
# 排除：通用器材类型（换一台同型号不影响教学→C05）；「X的卡通头像」等通用视觉格式（content_prompt 无具名角色→C05）；通用表情/状态插画（非具名人物→C05）。
#
# 5. C05_generic_subject_asset（通用主体/道具类 — 类型级匹配）：
# 画面核心是可辨识的主体或道具，换成同类型的另一个即可。
# 适用：通用卡通角色、动物头像/单体照片、通用花卉/植物/果实、通用文具/日用品/器材道具、通用主体+简单姿态、实验实拍中核心是可辨识器材/对象在演示现象。
# 与 C04 区分：世界上有很多同类型→C05；唯一不可替换→C04。与 C06 区分：画面有可辨识的独立主体对象→C05；整体是场景/氛围/容器/活动→C06。
#
# 6. C06_scene_decor_container（泛化场景/装饰/容器类 — 语境级匹配）：
# 画面表达可泛化的环境状态、例行活动、页面装饰或内容容器；复用只需语境/氛围相近，不要求主体身份、姿态动作逐项一致。
# 适用：风景/场景/氛围图、例行学习/生活活动场景、涉及文字的动作/活动（标记、圈画等动作是核心而非文字内容）、静态环境描写、空白卡片/边框/装饰框/承载容器、空白田字格/米字格/方格纸等纯承载容器、页面背景图、装饰图案/光斑/印章、大场景实验室/教室全景。
# 若容器内有不可替换文字，按文字内容判断（通常归 C00）。
# 氛围修饰测试：content_prompt 包含氛围词且定义画面整体感受→C06；去掉氛围词后仍是独立可辨识对象→C05。

# OLD MATERIAL_CATEGORY_RULES_TEXT v6.1 before C03/C04 merge.
# Kept for audit only; not used at runtime.
_OLD_MATERIAL_CATEGORY_RULES_TEXT_V61 = (
    "## strict_reuse_group 7 类分类规则（v6.1）\n"
    "全局原则：只根据 content_prompt 字面描述判断。"
    "严禁参考 theme、subject、grade_norm 等元数据推断分类。"
    "按优先级从高到低检查，命中即停；"
    "但每类必须通过“复用时不可替换信息”测试，不得只因关键词命中分类。"
    "分类依据是复用时哪些信息不可替换，而不是单个关键词是否出现。\n"
    "先判断 content_prompt 是否给出明确语言载荷："
    "具体汉字、词语、拼音、读音、笔顺、部首、偏旁、笔画等。"
    "给出明确语言载荷时，优先按 C01/C00 的数量和文本精确性判断；"
    "未给出具体语言载荷、只描述关系模板时，按 C02 判断。\n"
    "\n"
    "复用宽松度谱系（严→松）：C00 不复用 → C01 符号匹配 → C04 身份匹配 → "
    "C03 事件匹配 → C02 结构匹配 → C05 类型匹配 → C06 语境匹配\n"
    "\n"
    "0. C00_strict_text_problem_skip（精确教学载荷类 — 跳过复用）：\n"
    "画面核心复用价值依赖不可替换的教学载荷，必须逐字、逐数值、逐符号精确一致。\n"
    "语言符号内容中，1-3 个明确汉字/词语/拼音用于书写、结构、读音、组词、搭配、辨析教学时，"
    "不归 C00，优先归 C01；宽窄比例提示、笔顺标注、拼音、部首等辅助标注不改变此判断。"
    "4 个及以上独立教学字词、生字表、整段课文、诗文、题干、选项、解题步骤、完整任务说明，"
    "只要复用必须整体精确复现，归 C00。\n"
    "content_prompt 出现“课文”“段落”“片段”“原文”“全诗”“歌词”“童谣”等字样时，"
    "仅当文字/数据内容本身是画面核心复用价值时才归 C00。"
    "如果描述的是涉及文字的动作/活动/工具使用（如标记、圈画、标注序号），"
    "画面核心是动作而非文字内容，不归 C00，继续按 C05/C06 判断。\n"
    "含具体不可替换数值、题号、选项或作答要求的数学/物理题归 C00；"
    "无具体不可替换数值的几何、物理原理或装置关系图继续按 C02 判断。"
    "可替换短标签、栏目文字、装饰文字不因可读而归本类。\n"
    "\n"
    "1. C01_language_glyph_visual（语言符号形式教学类 — 精确符号匹配）：\n"
    "画面核心是少量（1-3 个）明确语言符号本身，或这些明确语言符号的形、音、义、书写、笔顺、部首、偏旁、字源、演变、组词、搭配、"
    "形近字/同音字/多音字辨析。"
    "即使画面使用拆分、箭头、对应、颜色标注、演变等图示方式，"
    "只要 prompt 明确给出具体汉字/词语/拼音/读音对象，且教学对象仍是这些语言符号本体，归 C01。"
    "组词/搭配若给出明确词语、汉字、读音或具体搭配对象，归 C01；"
    "若只写“组词搭配素材/模板/示意图”而没有具体词语内容，按语言关系图示归 C02。"
    "没有具体语言载荷的“左右结构拆分示意图”“部件相加拼新字示意图”“组词搭配卡通素材”等关系模板，"
    "不归 C01，归 C02。"
    "4 个或以上字词或整段语言载荷需要整体复现时，优先按 C00 处理。"
    "本类仅限语言符号；数学/物理的图示归 C02。\n"
    "\n"
    "2. C02_structure_diagram_visual（跨学科知识结构图示类 — 结构+主题匹配）：\n"
    "画面核心是以图形方式呈现知识关系、结构、流程、对应、组合、因果或分类；"
    "匹配关键在于关系走向 + 知识主题，而不是单个主体外观。\n"
    "遮住测试：把图中具体文字/数值/名称全部遮住，只看布局/节点/连线/流程走向，"
    "还能判断这是什么类型的教学图吗？能→C02。不能→按内容判断其他类。\n"
    "适用：思维导图、流程图、原理图（光路/几何概念/成像规律）、"
    "实验装置示意图、对比框架图、带位值/结构关系的教具模板、分类/关系图。"
    "语言学习中，若 prompt 未给出具体汉字/词语内容，而是描述结构拆分、部件组合、组词搭配、构字关系、阅读/写作结构模板，归 C02。"
    "若图中明确给出 1-3 个具体汉字/词语/拼音/读音，并围绕这些语言符号做书写、读音、组词、搭配或辨析教学，"
    "归 C01，而非 C02。"
    "实验实拍/场景若核心只是器材、道具或单体对象，归 C05；"
    "若核心是明确物理原理、光路、成像、会聚/发散或实验现象因果，归 C02。"
    "含具体不可替换数值、题号、选项或作答要求的图归 C00。"
    "抽象艺术/装饰插画→C06；通用插画恰巧有组合布局→C05/C06；"
    "空白田字格/米字格/方格纸等纯承载容器→C06。\n"
    "\n"
    "3. C03_specific_event_interaction（不可替换事件命题类 — 事件匹配）：\n"
    "画面表达不可替换的事件命题。事件三要素必须同时满足：有可辨识主体；有明确行为/事件/状态变化；"
    "该行为或状态具有叙事/情感意义，替换后会改变故事含义。"
    "叙事意义测试：把动作、姿态、表情、道具结果或环境氛围替换掉，画面传达的故事/情感是否根本改变？"
    "是→C03。"
    "人物处于低落、痛苦、愤怒、担忧、强忍等明确情绪状态，且姿态/道具/环境改变会改变课文或故事含义时，归 C03。"
    "单一主体+普通姿态不构成事件，归 C05；"
    "但姿态、表情、道具结果或环境氛围共同表达明确叙事情绪命题时，归 C03。"
    "普通站立、蹲坐、举手、看向某物等无冲突、无情绪转折、无事件结果的主体姿态，不归 C03，按 C05/C06 判断。"
    "例行学习/生活活动（朗读、写字、举手、观察）→C06；"
    "具名角色共处但无明确行为→C04。\n"
    "\n"
    "4. C04_teaching_bound_entity（教学指定实体类 — 严格身份匹配）：\n"
    "画面核心是世界上唯一的/不可替换的特定实体。"
    "判断依据只看 content_prompt，严禁参考 theme/subject。\n"
    "替换测试/唯一性测试：世界上只有这一个/这一位→C04；世界上有很多个同类型→C05。\n"
    "适用：具名人物肖像、content_prompt 明确出现的课文具名角色、具名/唯一地标建筑、具名文学作品实物、"
    "多个具名角色共处但无明确事件行为。"
    "具名角色若正在执行有叙事意义的事件，优先按 C03；无事件、只展示角色身份或形象时归 C04。"
    "若 X 只是通用类别或视觉主题（如孔雀头像、卡通小昆虫、普通教师），不是具名人物/具名角色，归 C05。"
    "通用表情/状态插画（非具名人物）不归 C04，按 C05/C03 判断。\n"
    "\n"
    "5. C05_generic_subject_asset（通用主体/道具类 — 类型级匹配）：\n"
    "适用：可辨识的通用主体或道具，换成同类型另一个仍可复用，且画面不依赖特定事件、强情绪命题、知识结构关系或内容容器。"
    "适用：通用卡通角色、动物头像/单体照片、通用花卉/植物/果实、通用文具/日用品/器材道具、通用主体+普通姿态。"
    "通用器材/道具单体或器材外观展示归 C05；"
    "若器材在表达明确物理原理、光路、成像、会聚/发散或实验现象因果，归 C02。"
    "有明确叙事情绪或事件结果时不归 C05，归 C03；"
    "主体举着空白卡片、文本框、展示板等承载容器时不归 C05，归 C06。"
    "与 C04 区分：世界上有很多同类型→C05；唯一不可替换→C04。"
    "与 C06 区分：画面有可辨识的独立主体对象→C05；整体是场景/氛围/容器/活动→C06。\n"
    "\n"
    "6. C06_scene_decor_container（泛化场景/装饰/容器类 — 语境级匹配）：\n"
    "画面表达可泛化的环境状态、例行活动、页面装饰或内容容器；"
    "复用只需语境/氛围相近，不要求主体身份、姿态动作逐项一致。\n"
    "适用：风景/场景/氛围图、例行学习/生活活动场景、"
    "涉及文字的动作/活动（标记、圈画等动作是核心而非文字内容）、"
    "静态环境描写、页面背景图、装饰图案/光斑/印章、大场景实验室/教室全景。"
    "空白卡片、文本框、便签、黑板、展示板、边框、模板、占位区域优先归 C06，即使旁边有通用角色或动物。"
    "空白田字格/米字格/方格纸等纯承载容器归 C06。"
    "若卡片/框内已有明确不可替换教学文字、汉字、词语、拼音或题目，按文字内容归 C01 或 C00，而不是 C06。"
    "自然物体若处在天气、季节、光线、氛围中并定义整体场景感受，归 C06；"
    "若是白底/孤立展示的单个植物、果实、器物或角色，归 C05。\n"
    "氛围修饰测试：content_prompt 包含氛围词且定义画面整体感受→C06；"
    "去掉氛围词后仍是独立可辨识对象→C05。\n"
)

MATERIAL_CATEGORY_RULES_TEXT = (
    "## strict_reuse_group 6 类分类规则（v7，无缺号，C03 动作边界收紧）\n"
    "\n"
    "全局原则：只根据 content_prompt 字面描述判断。"
    "严禁参考 theme、subject、grade_norm、文件名、原始 strict_reuse_group 或其他元数据推断分类。"
    "分类依据是 content_prompt 自身表达的复用粒度，而不是单个关键词是否出现。"
    "不得看到某个词就强制归类；必须判断画面核心复用价值属于不可替代命题、主体对象还是场景语境。"
    "按下面顺序判断；命中高优先级类别后不再下探。"
    "C03 不能抢走文字题图、语言符号、数学/物理/语文结构图；"
    "C04 不能抢走不可替代动作；C05 不能抢走可辨识主体或事件。\n"
    "\n"
    "新分类只允许输出以下 6 个 ID：\n"
    "C00_strict_text_problem_skip\n"
    "C01_language_glyph_visual\n"
    "C02_structure_diagram_visual\n"
    "C03_irreplaceable_entity_event_action\n"
    "C04_generic_subject_object\n"
    "C05_scene_decor_container\n"
    "\n"
    "复用宽松度谱系（严→松）："
    "C00 不复用 → C01 符号精确匹配 → C03 实体/事件/动作严格匹配 → "
    "C02 结构主题匹配 → C04 类型匹配 → C05 语境匹配\n"
    "\n"
    "0. C00_strict_text_problem_skip（精确教学载荷类 — 跳过复用）：\n"
    "画面核心复用价值依赖不可替换的教学载荷，必须逐字、逐数值、逐符号一致时，归 C00。\n"
    "适用：4 个及以上独立教学字词、生字表、整段课文/诗文/儿歌/选段、完整题干、选项、"
    "解题步骤、完整任务说明、不可替换数值题图。\n"
    "排除：1-3 个明确汉字/词语/拼音用于书写、结构、读音、组词、搭配、辨析教学时，"
    "不归 C00，归 C01。短栏目标签、装饰文字、角色旁的小牌子，不因可读而归 C00，"
    "应按画面主体判断。\n"
    "\n"
    "1. C01_language_glyph_visual（语言符号形式教学类 — 精确符号匹配）：\n"
    "画面核心是语言符号本体或语言符号形式教学时，归 C01。\n"
    "适用：汉字、词语、拼音、读音、笔画、笔顺、部首、偏旁、字源、演变、构字、结构、"
    "形近字/同音字/多音字辨析、少量生字教学卡片。\n"
    "即使画面使用拆分、箭头、对比、演变、图标辅助，只要教学对象仍是语言符号本体，归 C01。\n"
    "排除：课文结构、阅读结构、写作结构、内容梳理、思维导图、人物关系图等，"
    "核心是信息组织而不是字形/字音/字义本体时，归 C02。"
    "4 个及以上独立教学字词或整段语言载荷需整体精确复现时，归 C00。"
    "语言符号本体教学归 C01。\n"
    "\n"
    "2. C02_structure_diagram_visual（知识结构图示类 — 结构+主题匹配）：\n"
    "画面核心是知识关系、流程、结构、对应、分类、因果、规律、图表或原理说明，"
    "匹配关键在于结构走向和知识主题时，归 C02。\n"
    "适用：数学几何/统计/数轴/路线/应用题关系图，物理光路、成像规律、实验装置结构、"
    "变量对比，语文阅读/写作/课文结构思维导图，跨学科流程图、关系图、表格。\n"
    "必要条件：遮住具体文字/数值后，仍能看出它是某类知识结构或原理图。\n"
    "排除：单个工具、道具、器材、普通实验现象或实践场景，"
    "若没有明确结构关系/变量关系/光路/规律对比，归 C04。"
    "语言符号本体教学归 C01。不可替换题干/数值/选项归 C00。\n"
    "\n"
    "3. C03_irreplaceable_entity_event_action（不可替代实体/事件/动作类 — 严格匹配）：\n"
    "只有 content_prompt 自身已经表达不可替代语义命题时才归 C03。"
    "C03 不要求主体必须是具名人物、唯一地点、唯一物体或课文专名；匿名主体也可以归 C03。"
    "判断时必须忽略课文名、theme、subject、grade_norm、teaching_intent、context_summary 和旧分类。"
    "如果需要依赖课文名、theme、teaching_intent 或教学上下文才不可替代，不归 C03。"
    "不得看到某个词就强制归类；必须看完整 content_prompt 是否形成不可替代命题。\n"
    "C03 的核心判断：把画面简化为同类型通用主体/对象、普通姿态或普通动作后，"
    "是否会丢失 content_prompt 自身表达的核心复用意义。"
    "如果会丢失，且丢失的是故事绑定的角色身份、主体关系、叙事动作或情绪状态组合，归 C03。\n"
    "可归 C03 的泛化类型包括：具名或故事绑定的角色身份；"
    "亲属关系、故事角色关系、角色功能关系等不可互换主体关系；"
    "有意图、对象或结果的动作；冲突、抗拒、破坏后果、状态转折、关系张力或故事结果；"
    "姿态、道具、环境或氛围共同表达故事状态的强情绪画面。"
    "这些类型必须由 content_prompt 自身表达，不得从课文主题或旧 metadata 推断。\n"
    "不归 C03 的情况：普通动物群体、普通人物组合、轻量社交动作、普通自然状态、"
    "通用主体的简单姿态、普通表情、外观特征、比喻性视觉特征，"
    "不构成不可替代语义命题时，应下探到 C04 或 C05。\n"
    "\n"
    "4. C04_generic_subject_object（通用主体/道具类 — 类型级匹配）：\n"
    "画面核心复用价值是可辨识的主体或对象时归 C04。"
    "主体或对象可以是人物、角色、动物、植物、自然物、工具、器材、道具、小规模主体组合或普通状态。"
    "简单姿态、普通动作、普通表情、外观特征、比喻性视觉特征、轻量社交动作可以保留在 C04，"
    "前提是它们没有组成不可替代语义命题。"
    "如果把主体换成同类型另一个、把动作简化成普通姿态、或移除关系/情绪状态后，"
    "仍保留主要复用价值，归 C04。"
    "如果这种简化会破坏 content_prompt 自身表达的故事绑定身份、关系、动作结果或情绪状态组合，归 C03。"
    "如果画面主要靠整体环境、天气、远景、氛围或页面承载功能复用，归 C05。"
    "明确知识结构、光路、变量关系或规律图示归 C02。"
    "空白卡片、文本框、边框、模板、占位区域主导画面归 C05。\n"
    "\n"
    "5. C05_scene_decor_container（场景装饰容器类 — 语境级匹配）：\n"
    "画面核心复用价值是整体场景、天气、氛围、远景、背景、页面装饰、空白容器或内容占位模板时归 C05。"
    "复用只需语境、氛围或版式功能相近，不要求独立主体对象精确一致。"
    "视觉具体、季节明确或气氛明显，不会自动提升到 C03。"
    "如果画面裁出某个主体后仍主要作为该主体对象复用，归 C04。"
    "如果 content_prompt 自身表达主体绑定的不可替代强叙事命题，归 C03。"
    "容器内已有不可替换教学文字、题目、段落时，按 C00/C01 判断。\n"
)

MATERIAL_CATEGORY_RULES_TEXT = MATERIAL_CATEGORY_RULES_TEXT.replace("content_prompt", "query")


def _asset_caption(asset: dict[str, Any]) -> str:
    return _clean_text(asset.get("caption")) or _clean_text(asset.get("content_prompt"))


def _asset_query(asset: dict[str, Any]) -> str:
    return (
        _clean_text(asset.get("query"))
        or _clean_text(asset.get("detail_prompt"))
        or _clean_text(asset.get("content_prompt"))
    )


def _build_classify_prompt(payload: dict[str, Any]) -> str:
    query = _asset_query(payload)
    request = {
        "asset_id": _clean_text(payload.get("asset_id")),
        "query": query,
    }
    return (
        "Classify this material into exactly one strict_reuse_group using only the query field.\n\n"
        + MATERIAL_CATEGORY_RULES_TEXT
        + "\n\nInput JSON:\n"
        + json.dumps(request, ensure_ascii=False, indent=2)
    )


# OLD MATERIAL_CATEGORY_RULES_TEXT v6.2 before C03 event-boundary refinement.
# Kept below the active prompt for audit only; commented out and not used at runtime.
#     "## strict_reuse_group 6 类分类规则（v6.2，C03/C04 合并）\n"
#     "\n"
#     "全局原则：只根据 content_prompt 字面描述判断。"
#     "严禁参考 theme、subject、grade_norm 等元数据推断分类。"
#     "分类依据是复用时哪些信息不可替换，而不是单个关键词是否出现。"
#     "按下面顺序判断；命中高优先级类别后不再下探。\n"
#     "\n"
#     "新分类只允许输出以下 6 个 ID：\n"
#     "C00_strict_text_problem_skip\n"
#     "C01_language_glyph_visual\n"
#     "C02_structure_diagram_visual\n"
#     "C03_specific_event_interaction\n"
#     "C05_generic_subject_asset\n"
#     "C06_scene_decor_container\n"
#     "\n"
#     "注意：C04_teaching_bound_entity 是旧版兼容 ID，不允许新分类输出；"
#     "旧数据中的 C04 会由代码归一到 C03。\n"
#     "\n"
#     "复用宽松度谱系（严→松）："
#     "C00 不复用 → C01 符号精确匹配 → C03 实体/事件严格匹配 → "
#     "C02 结构主题匹配 → C05 类型匹配 → C06 语境匹配\n"
#     "\n"
#     "0. C00_strict_text_problem_skip（精确教学载荷类 — 跳过复用）：\n"
#     "画面核心复用价值依赖不可替换的教学载荷，必须逐字、逐数值、逐符号一致时，归 C00。\n"
#     "适用：4 个及以上独立教学字词、生字表、整段课文/诗文/儿歌/选段、完整题干、选项、"
#     "解题步骤、完整任务说明、不可替换数值题图。\n"
#     "排除：1-3 个明确汉字/词语/拼音用于书写、结构、读音、组词、搭配、辨析教学时，"
#     "不归 C00，归 C01。短栏目标签、装饰文字、角色旁的小牌子，不因可读而归 C00，"
#     "应按画面主体判断。\n"
#     "\n"
#     "1. C01_language_glyph_visual（语言符号形式教学类 — 精确符号匹配）：\n"
#     "画面核心是语言符号本体或语言符号形式教学时，归 C01。\n"
#     "适用：汉字、词语、拼音、读音、笔画、笔顺、部首、偏旁、字源、演变、构字、结构、"
#     "形近字/同音字/多音字辨析、少量生字教学卡片。\n"
#     "即使画面使用拆分、箭头、对比、演变、图标辅助，只要教学对象仍是语言符号本体，归 C01。\n"
#     "排除：课文结构、阅读结构、写作结构、内容梳理、思维导图、人物关系图等，"
#     "核心是信息组织而不是字形/字音/字义本体时，归 C02。"
#     "4 个及以上独立教学字词或整段语言载荷需整体精确复现时，归 C00。\n"
#     "\n"
#     "2. C02_structure_diagram_visual（知识结构图示类 — 结构+主题匹配）：\n"
#     "画面核心是知识关系、流程、结构、对应、分类、因果、规律、图表或原理说明，"
#     "匹配关键在于结构走向和知识主题时，归 C02。\n"
#     "适用：数学几何/统计/数轴/路线/应用题关系图，物理光路、成像规律、实验装置结构、"
#     "变量对比，语文阅读/写作/课文结构思维导图，跨学科流程图、关系图、表格。\n"
#     "必要条件：遮住具体文字/数值后，仍能看出它是某类知识结构或原理图。\n"
#     "排除：单个工具、道具、器材、普通实验现象或实践场景，"
#     "若没有明确结构关系/变量关系/光路/规律对比，归 C05。"
#     "语言符号本体教学归 C01。不可替换题干/数值/选项归 C00。\n"
#     "\n"
#     "3. C03_specific_event_interaction（严格实体/事件类 — 身份或事件匹配）：\n"
#     "画面核心包含不可替换的具名实体、唯一实体、课文/文学具名角色，或不可替换的故事事件时，归 C03。\n"
#     "适用：具体人名人物、作者肖像、历史人物、具名文学角色、唯一地标/文物/书籍封面、"
#     "具名人物共处、具名人物正在做普通展示动作、课文故事关键事件、人物关系事件、"
#     "冲突/救助/送别/对话等叙事场面。\n"
#     "合并规则：旧 C04 的“教学指定实体”全部并入 C03。"
#     "只要 content_prompt 明确给出具体人名、具名角色名、唯一实体名，且不是 C00/C01/C02，就归 C03。\n"
#     "事件判断：即使没有具体人名，只要画面表达不可替换的叙事情节，"
#     "替换主体关系、动作结果或事件会改变故事含义，也归 C03。\n"
#     "排除：通用人物、通用动物、通用表情、单体面容、普通姿态、"
#     "普通举旗/拿书/讲解/观察等不绑定特定身份或故事事件的主体，归 C05。"
#     "普通课堂/生活活动场景、背景氛围、空白容器主导画面时，归 C06。\n"
#     "\n"
#     "4. C05_generic_subject_asset（通用主体/道具类 — 类型级匹配）：\n"
#     "画面核心是可辨识的通用主体、角色、动物、植物、工具、器材、道具或普通状态，"
#     "换成同类型另一个仍可复用时，归 C05。\n"
#     "适用：通用卡通人物/动物/植物/器物，工具道具，放大镜/老花镜/显微镜实物，"
#     "普通人物表情或面容，通用主体+普通姿态，举旗、拿书、讲解、观察、带路等非故事关键动作。\n"
#     "排除：具名人物/具名角色/唯一实体归 C03。"
#     "明确知识结构、光路、变量关系或规律图示归 C02。"
#     "空白卡片、文本框、边框、模板、占位区域主导画面归 C06。\n"
#     "\n"
#     "5. C06_scene_decor_container（场景装饰容器类 — 语境级匹配）：\n"
#     "画面核心是泛化场景、氛围、背景、页面装饰、空白容器或内容占位模板，"
#     "复用只需语境/风格相近时，归 C06。\n"
#     "适用：风景/天气/自然氛围、课堂/校园/生活泛场景、页面背景、装饰图案、边框、"
#     "空白卡片、空白文本框、便签、黑板、展示板、模板、PPT 占位区域。\n"
#     "容器判断：只有容器/占位功能是画面主功能时才归 C06。"
#     "若短栏目标签、小牌子或卡片只是角色附属道具，应按主视觉主体判断。\n"
#     "排除：容器内已有不可替换教学文字、题目、段落时，按 C00/C01 判断。"
#     "具名主体或具名事件归 C03。可辨识通用主体/道具是画面核心时，归 C05。\n"

# Canonical convenience constants (downstream code still imports these names).
GENERAL_REUSE_GROUP = C05_SCENE_DECOR_CONTAINER
CONTENT_REUSE_GROUP = C00_STRICT_TEXT_PROBLEM_SKIP
STRICT_REUSE_GROUPS = MATERIAL_CATEGORIES
STRICT_REUSE_SPLIT_GROUPS = MATERIAL_CATEGORIES

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def normalize_strict_reuse_group(value: Any, *, default: str = C05_SCENE_DECOR_CONTAINER) -> str:
    """Normalize canonical material category labels."""

    text = _clean_text(value).casefold()
    if not text:
        return default
    if text in _MATERIAL_CATEGORY_SET:
        return text
    # Case-insensitive match for current IDs
    for cat in MATERIAL_CATEGORIES:
        if text == cat.casefold():
            return cat
    # Legacy persisted IDs map to the current active category set.
    migrated = _LEGACY_CATEGORY_MIGRATION.get(text)
    if migrated is not None:
        return migrated
    return default


SKIP_FROM_INDEX_VLM_QUALITY_THRESHOLD = 0.3


def should_skip_from_index(asset: dict[str, Any]) -> bool:
    group = normalize_strict_reuse_group(asset.get("strict_reuse_group"))
    if group == C00_STRICT_TEXT_PROBLEM_SKIP:
        return True

    vlm_quality = asset.get("vlm_match_quality")
    if vlm_quality is not None:
        try:
            if float(vlm_quality) < SKIP_FROM_INDEX_VLM_QUALITY_THRESHOLD:
                return True
        except (TypeError, ValueError):
            pass

    from edupptx.materials.ai_image_asset_db import normalize_aspect_bucket
    bucket = normalize_aspect_bucket(asset.get("aspect_ratio"))
    padding = _clean_text(asset.get("padding_capacity")).casefold()
    if bucket == "other" and padding == "none":
        return True

    return False


def classify_asset_strict_reuse(
    asset: dict[str, Any],
    *,
    infer_legacy_missing: bool = False,
) -> dict[str, Any]:
    """Normalize one asset's upstream reuse-group decision.

    New assets are classified by the LLM/VLM stages and arrive with
    ``strict_reuse_group`` already set by an LLM/VLM stage. This pass trusts
    explicit upstream labels and only normalizes their format. Missing labels
    are never inferred from local keywords.
    """

    asset_kind = _clean_text(asset.get("asset_kind"))
    if asset_kind == "background":
        return _classification(
            C06_SCENE_DECOR_CONTAINER,
            1.0,
            ["background_asset_kind"],
            [],
            reason="background assets routed by asset_kind, not classified",
        )
    if asset_kind and asset_kind != "page_image":
        return _classification(
            GENERAL_REUSE_GROUP,
            1.0,
            ["non_page_image"],
            [],
            reason=f"non-page images use {GENERAL_REUSE_GROUP} routing",
        )

    raw_group = _clean_text(asset.get("strict_reuse_group"))
    if (
        infer_legacy_missing
        and raw_group
        and normalize_strict_reuse_group(raw_group) == GENERAL_REUSE_GROUP
        and _is_missing_upstream_default(asset)
    ):
        raw_group = ""
    if infer_legacy_missing and raw_group and _is_legacy_unclassified_inference(asset):
        raw_group = ""

    if raw_group:
        group = normalize_strict_reuse_group(raw_group, default="")
        if not group:
            return _classification(
                GENERAL_REUSE_GROUP,
                0.5,
                ["invalid_upstream_reuse_group"],
                ["invalid_upstream_reuse_group"],
                reason=f"invalid upstream reuse group {raw_group}; expected LLM/VLM material category",
            )
        signals = ["upstream_reuse_group"]
        confidence = _to_score(asset.get("strict_reuse_confidence"))
        if confidence is None:
            confidence = 0.86 if group == CONTENT_REUSE_GROUP else 0.9
        reason = f"kept upstream reuse group {group}"
        return _classification(group, confidence, signals, [], reason=reason)

    if infer_legacy_missing:
        return _classification(
            GENERAL_REUSE_GROUP,
            0.78,
            ["legacy_default_generic_scene_activity"],
            [],
            reason=f"legacy unclassified asset defaulted to {GENERAL_REUSE_GROUP}",
        )

    return _classification(
        GENERAL_REUSE_GROUP,
        0.5,
        ["missing_upstream_reuse_classification"],
        ["missing_upstream_reuse_classification"],
        reason=f"no LLM/VLM reuse classification; defaulted to {GENERAL_REUSE_GROUP}",
    )


def classify_strict_reuse_groups(
    index: dict[str, Any],
    *,
    infer_legacy_missing: bool = False,
) -> dict[str, Any]:
    """Mutate an index/database in-place with normalized binary reuse fields."""

    assets = index.get("assets")
    asset_list = assets if isinstance(assets, list) else []
    group_counts: Counter[str] = Counter()
    review_reason_counts: Counter[str] = Counter()
    review_items: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    for asset in asset_list:
        if not isinstance(asset, dict):
            continue
        result = classify_asset_strict_reuse(asset, infer_legacy_missing=infer_legacy_missing)
        _apply_classification(asset, result)
        group_counts[result["strict_reuse_group"]] += 1
        review_reasons = result["strict_reuse_review_reasons"]
        if result["strict_reuse_review_required"]:
            for reason in review_reasons:
                review_reason_counts[reason] += 1
            review_items.append(_review_queue_item(asset, result))

    for group in STRICT_REUSE_GROUPS:
        group_counts.setdefault(group, 0)

    classification_source = (
        "legacy_unclassified_index_migration" if infer_legacy_missing else "reuse_group_format_migration"
    )
    metadata = {
        "classifier_version": STRICT_REUSE_CLASSIFIER_VERSION,
        "updated_at": now,
        "group_counts": dict(group_counts),
        "review_required_count": len(review_items),
        "classification_source": classification_source,
        "legacy_inference_enabled": bool(infer_legacy_missing),
    }
    index["strict_reuse_classification"] = metadata
    index["updated_at"] = now

    return {
        "classifier_version": STRICT_REUSE_CLASSIFIER_VERSION,
        "updated_at": now,
        "asset_count": len(asset_list),
        "group_counts": dict(group_counts),
        "review_required_count": len(review_items),
        "review_reason_counts": dict(review_reason_counts),
        "review_asset_ids": [item["asset_id"] for item in review_items],
        "review_items": review_items,
        "classification_source": classification_source,
        "legacy_inference_enabled": bool(infer_legacy_missing),
    }


def classify_strict_reuse_library(
    library_dir: str | Path,
    *,
    index_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    dry_run: bool = False,
    write_debug: bool = True,
    split_dir: str | Path | None = STRICT_REUSE_INDEX_DIRNAME,
    prefer_split_index: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Normalize one material library index on disk."""

    root = Path(library_dir).expanduser().resolve()
    index_path = root / index_filename
    index, source_path, source_kind = _read_classification_source(
        root,
        index_path,
        split_dir,
        prefer_split_index=prefer_split_index,
    )

    infer_legacy_missing = (
        source_kind == "legacy_match_index"
        or _has_missing_upstream_defaults(index)
        or _has_legacy_unclassified_inferences(index)
    )
    report = classify_strict_reuse_groups(index, infer_legacy_missing=infer_legacy_missing)
    report["library_dir"] = str(root)
    report["source_index_path"] = str(source_path)
    report["source_kind"] = source_kind

    if split_dir is not None:
        split_report = write_strict_reuse_group_indexes(index, root, split_dir=split_dir, dry_run=dry_run)
        report["split_indexes"] = split_report

    if not dry_run:
        if index_path.exists():
            index_path.unlink()
        if write_debug:
            debug_dir = root / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            report_path = debug_dir / STRICT_REUSE_REPORT_FILENAME
            queue_path = debug_dir / STRICT_REUSE_REVIEW_QUEUE_FILENAME
            report_path.write_text(
                json.dumps(_report_without_review_items(report), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _write_review_queue(queue_path, report["review_items"])
            report["debug_report_path"] = str(report_path)
            report["review_queue_path"] = str(queue_path)

    return report, Path(report.get("split_indexes", {}).get("split_dir") or source_path)


def write_strict_reuse_group_indexes(
    index: dict[str, Any],
    library_dir: str | Path,
    *,
    split_dir: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write binary reuse-group indexes that reference the same image paths."""

    root = Path(library_dir).expanduser().resolve()
    target_dir = Path(split_dir)
    if not target_dir.is_absolute():
        target_dir = root / target_dir

    assets = [asset for asset in index.get("assets", []) if isinstance(asset, dict)]
    written: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    for group in STRICT_REUSE_SPLIT_GROUPS:
        group_assets = [
            deepcopy(asset)
            for asset in assets
            if normalize_strict_reuse_group(asset.get("strict_reuse_group")) == group
            and (group == C00_STRICT_TEXT_PROBLEM_SKIP or not should_skip_from_index(asset))
            and _clean_text(asset.get("asset_kind")) != "background"
        ]
        payload = {
            "schema_version": index.get("schema_version"),
            "strict_reuse_group": group,
            "built_at": now,
            "updated_at": now,
            "asset_root": index.get("asset_root") or str(root),
            "asset_count": len(group_assets),
            "assets": group_assets,
        }
        output_path = target_dir / f"{group}.json"
        written[group] = {"path": str(output_path), "asset_count": len(group_assets)}
        if not dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    background_assets = [
        deepcopy(asset)
        for asset in assets
        if _clean_text(asset.get("asset_kind")) == "background"
        and not should_skip_from_index(asset)
    ]
    for asset in background_assets:
        asset["asset_kind"] = "background"
        asset["strict_reuse_group"] = normalize_strict_reuse_group(asset.get("strict_reuse_group"))
    background_payload = {
        "schema_version": index.get("schema_version"),
        "strict_reuse_group": BACKGROUND_REUSE_INDEX_GROUP,
        "built_at": now,
        "updated_at": now,
        "asset_root": index.get("asset_root") or str(root),
        "asset_count": len(background_assets),
        "assets": background_assets,
    }
    background_path = target_dir / BACKGROUND_REUSE_INDEX_FILENAME
    written[BACKGROUND_REUSE_INDEX_GROUP] = {
        "path": str(background_path),
        "asset_count": len(background_assets),
    }
    if not dry_run:
        background_path.parent.mkdir(parents=True, exist_ok=True)
        background_path.write_text(json.dumps(background_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not dry_run:
        legacy_manifest = target_dir / "strict_reuse_split_manifest.json"
        if legacy_manifest.exists():
            legacy_manifest.unlink()
    return {"split_dir": str(target_dir), "groups": written}


def _read_classification_source(
    root: Path,
    index_path: Path,
    split_dir: str | Path | None,
    *,
    prefer_split_index: bool = True,
) -> tuple[dict[str, Any], Path, str]:
    target_dir = Path(split_dir) if split_dir is not None else Path(STRICT_REUSE_INDEX_DIRNAME)
    if not target_dir.is_absolute():
        target_dir = root / target_dir
    split_source = _read_split_classification_source(root, target_dir)
    if prefer_split_index and split_source is not None:
        index, source_path = split_source
        if index.get("assets") or not index_path.exists():
            return index, source_path, "split_index"

    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            raise ValueError(f"AI image match index is not a JSON object: {index_path}")
        return index, index_path, "legacy_match_index"

    if split_source is not None:
        index, source_path = split_source
        return index, source_path, "split_index"

    raise FileNotFoundError(
        f"AI image match index not found: {index_path}; split indexes not found under: {target_dir}"
    )


def _read_split_classification_source(root: Path, target_dir: Path) -> tuple[dict[str, Any], Path] | None:
    assets: list[dict[str, Any]] = []
    found = False
    first_payload: dict[str, Any] = {}
    background_path = target_dir / BACKGROUND_REUSE_INDEX_FILENAME
    has_background_split = background_path.exists()
    for group in STRICT_REUSE_GROUPS:
        path = target_dir / f"{group}.json"
        if not path.exists():
            continue
        found = True
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Strict reuse index is not a JSON object: {path}")
        if not first_payload:
            first_payload = payload
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            continue
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue
            asset = deepcopy(raw_asset)
            asset["strict_reuse_group"] = normalize_strict_reuse_group(
                asset.get("strict_reuse_group") or payload.get("strict_reuse_group") or group,
            )
            if has_background_split and _clean_text(asset.get("asset_kind")) == "background":
                continue
            assets.append(asset)
    if has_background_split:
        found = True
        payload = json.loads(background_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Strict reuse index is not a JSON object: {background_path}")
        if not first_payload:
            first_payload = payload
        raw_assets = payload.get("assets")
        if isinstance(raw_assets, list):
            for raw_asset in raw_assets:
                if not isinstance(raw_asset, dict):
                    continue
                asset = deepcopy(raw_asset)
                if _clean_text(asset.get("asset_kind")) != "background":
                    continue
                asset["asset_kind"] = "background"
                asset["strict_reuse_group"] = normalize_strict_reuse_group(
                    asset.get("strict_reuse_group"),
                )
                assets.append(asset)
    if not found:
        return None

    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": first_payload.get("schema_version"),
        "built_at": first_payload.get("built_at") or now,
        "updated_at": now,
        "asset_root": first_payload.get("asset_root") or str(root),
        "asset_count": len(assets),
        "assets": assets,
        "warnings": first_payload.get("warnings", []),
    }, target_dir


def export_strict_reuse_visual_check(
    library_dir: str | Path,
    output_dir: str | Path,
    *,
    index_filename: str = DEFAULT_MATCH_INDEX_FILENAME,
    clean: bool = True,
    force: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Copy assets into material category folders for inspection."""

    root = Path(library_dir).expanduser().resolve()
    index_path = root / index_filename
    index, source_path, _source_kind = _read_classification_source(root, index_path, None)

    target_dir = Path(output_dir).expanduser()
    if not target_dir.is_absolute():
        target_dir = Path.cwd() / target_dir
    target_dir = target_dir.resolve()
    _ensure_visual_check_target_is_separate(root, target_dir)
    _prepare_visual_check_dir(target_dir, clean=clean, force=force)

    assets = [asset for asset in index.get("assets", []) if isinstance(asset, dict)]
    entries: list[dict[str, Any]] = []
    group_counts: Counter[str] = Counter()
    missing_items: list[dict[str, Any]] = []

    for ordinal, asset in enumerate(assets, 1):
        group = normalize_strict_reuse_group(asset.get("strict_reuse_group"))
        source_path = _resolve_asset_image_path(asset, root, index)
        target_path = _visual_check_target_path(target_dir, group, asset, source_path, ordinal)
        copied = False
        if source_path.exists() and source_path.is_file():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            copied = True
        else:
            missing_items.append(
                {
                    "asset_id": _clean_text(asset.get("asset_id")),
                    "image_path": _clean_text(asset.get("image_path")),
                    "resolved_source_path": str(source_path),
                    "strict_reuse_group": group,
                }
            )

        entries.append(
            {
                "asset_id": _clean_text(asset.get("asset_id")),
                "strict_reuse_group": group,
                "source_image_path": str(source_path),
                "output_image_path": _relative_posix(target_path, target_dir) if copied else "",
                "copied": copied,
                "subject": _clean_text(asset.get("subject")),
                "caption": _asset_caption(asset),
                "vlm_match_quality": asset.get("vlm_match_quality"),
            }
        )
        group_counts[group] += 1

    for group in STRICT_REUSE_GROUPS:
        (target_dir / group).mkdir(parents=True, exist_ok=True)
        group_counts.setdefault(group, 0)

    manifest_path = target_dir / STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "mode": STRICT_REUSE_VISUAL_CHECK_MODE,
        "built_at": now,
        "library_dir": str(root),
        "source_index_path": str(source_path),
        "output_dir": str(target_dir),
        "asset_count": len(assets),
        "copied_count": sum(1 for entry in entries if entry["copied"]),
        "missing_image_count": len(missing_items),
        "group_counts": dict(group_counts),
        "manifest_path": str(manifest_path),
        "missing_items": missing_items,
        "assets": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest, target_dir


def _is_missing_upstream_default(asset: dict[str, Any]) -> bool:
    signals = {_clean_text(item) for item in _as_string_list(asset.get("strict_reuse_signals"))}
    if "missing_upstream_reuse_classification" in signals:
        return True
    reason = _clean_text(asset.get("strict_reuse_reason")).casefold()
    return "missing_upstream_reuse_classification" in reason or f"defaulted to {GENERAL_REUSE_GROUP}".casefold() in reason


def _is_legacy_unclassified_inference(asset: dict[str, Any]) -> bool:
    signals = {_clean_text(item) for item in _as_string_list(asset.get("strict_reuse_signals"))}
    if any(
        signal == "legacy_default_generic_scene_activity"
        or signal == "legacy_content_context"
        or signal == "legacy_content_keyword"
        or signal == "legacy_math_expression"
        or signal.startswith("legacy_exact_constraint:")
        for signal in signals
    ):
        return True
    reason = _clean_text(asset.get("strict_reuse_reason")).casefold()
    return "legacy unclassified asset" in reason


def _has_missing_upstream_defaults(index: dict[str, Any]) -> bool:
    assets = index.get("assets")
    if not isinstance(assets, list):
        return False
    return any(isinstance(asset, dict) and _is_missing_upstream_default(asset) for asset in assets)


def _has_legacy_unclassified_inferences(index: dict[str, Any]) -> bool:
    assets = index.get("assets")
    if not isinstance(assets, list):
        return False
    return any(isinstance(asset, dict) and _is_legacy_unclassified_inference(asset) for asset in assets)


def _apply_classification(asset: dict[str, Any], result: dict[str, Any]) -> None:
    asset["strict_reuse_group"] = result["strict_reuse_group"]
    asset["strict_reuse_confidence"] = result["strict_reuse_confidence"]
    asset["strict_reuse_reason"] = result["strict_reuse_reason"]
    asset.pop("strict_reuse_vlm_review_required", None)
    asset.pop("strict_reuse_vlm_review_reasons", None)
    asset.pop("strict_reuse_review_required", None)
    asset.pop("strict_reuse_review_reasons", None)
    asset.pop("strict_reuse_requires_exact_match", None)
    signals = result["strict_reuse_signals"]
    if signals:
        asset["strict_reuse_signals"] = signals
    else:
        asset.pop("strict_reuse_signals", None)


def _classification(
    group: str,
    confidence: float,
    signals: list[str],
    review_reasons: list[str],
    *,
    reason: str = "",
) -> dict[str, Any]:
    group = normalize_strict_reuse_group(group)
    review_reasons = _dedupe(review_reasons)
    return {
        "strict_reuse_group": group,
        "strict_reuse_confidence": round(max(0.0, min(1.0, confidence)), 4),
        "strict_reuse_reason": reason or _reason_for_group(group, signals, review_reasons),
        "strict_reuse_signals": _dedupe(signals),
        "strict_reuse_review_required": bool(review_reasons),
        "strict_reuse_review_reasons": review_reasons,
    }


def _review_queue_item(asset: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": _clean_text(asset.get("asset_id")),
        "image_path": _clean_text(asset.get("image_path")),
        "subject": _clean_text(asset.get("subject")),
        "caption": _asset_caption(asset),
        "strict_reuse_group": result["strict_reuse_group"],
        "strict_reuse_confidence": result["strict_reuse_confidence"],
        "review_reasons": result["strict_reuse_review_reasons"],
        "signals": result["strict_reuse_signals"],
        "vlm_match_quality": asset.get("vlm_match_quality"),
        "constraints": asset.get("constraints") or [],
        "review_status": "pending",
    }


def _write_review_queue(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not items:
        path.write_text("", encoding="utf-8")
        return
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items) + "\n",
        encoding="utf-8",
    )


def _report_without_review_items(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "review_items"}


def _reason_for_group(group: str, signals: list[str], review_reasons: list[str]) -> str:
    if review_reasons:
        return f"{group} assigned with review flags"
    if signals:
        return f"{group} assigned from {signals[0]}"
    return f"{group} assigned"


def _ensure_visual_check_target_is_separate(library_root: Path, target_dir: Path) -> None:
    if target_dir == library_root or _is_relative_to(target_dir, library_root):
        raise ValueError(
            "Visual check output directory must be outside the material library "
            f"to keep the library unchanged: {target_dir}"
        )


def _prepare_visual_check_dir(target_dir: Path, *, clean: bool, force: bool) -> None:
    manifest_path = target_dir / STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME
    if manifest_path.exists() and not force:
        existing = _load_json_object_or_none(manifest_path)
        if existing is None or existing.get("mode") != STRICT_REUSE_VISUAL_CHECK_MODE:
            raise ValueError(
                f"Refusing to overwrite an unrelated manifest: {manifest_path}. "
                "Use --force or choose a dedicated output directory."
            )

    target_dir.mkdir(parents=True, exist_ok=True)
    if not clean:
        return
    for name in (*STRICT_REUSE_GROUPS, "none", "non_none"):
        path = target_dir / name
        if path.exists():
            shutil.rmtree(path)
    for filename in (STRICT_REUSE_VISUAL_CHECK_MANIFEST_FILENAME, STRICT_REUSE_VISUAL_CHECK_HTML_FILENAME):
        path = target_dir / filename
        if path.exists():
            path.unlink()


def _resolve_asset_image_path(asset: dict[str, Any], library_root: Path, index: dict[str, Any]) -> Path:
    image_path = _clean_text(asset.get("image_path"))
    if not image_path:
        return library_root / "__missing_image_path__"
    raw_path = Path(image_path)
    if raw_path.is_absolute():
        return raw_path

    candidates = [library_root / raw_path]
    asset_root = _clean_text(index.get("asset_root"))
    if asset_root:
        candidates.append(Path(asset_root).expanduser() / raw_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _visual_check_target_path(
    target_dir: Path,
    group: str,
    asset: dict[str, Any],
    source_path: Path,
    ordinal: int,
) -> Path:
    asset_id = _safe_filename(_clean_text(asset.get("asset_id")) or f"asset_{ordinal:06d}")[:96]
    suffix = source_path.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        suffix = ".png"
    return target_dir / group / f"{ordinal:06d}_{group}_{asset_id}{suffix}"


def _relative_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "asset"


def _load_json_object_or_none(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _to_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    text = _clean_text(value)
    return [text] if text else []


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
