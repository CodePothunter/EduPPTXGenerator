import json
from pathlib import Path

from edupptx.materials.ai_image_asset_db import build_ai_image_match_index
from edupptx.materials.strict_reuse_classifier import (
    C00_STRICT_TEXT_PROBLEM_SKIP,
    C01_LANGUAGE_GLYPH_VISUAL,
    C02_STRUCTURE_DIAGRAM_VISUAL,
    C03_IRREPLACEABLE_ENTITY_EVENT_ACTION,
    C03_SPECIFIC_EVENT_INTERACTION,
    C04_GENERIC_SUBJECT_OBJECT,
    C04_TEACHING_BOUND_ENTITY,
    C05_SCENE_DECOR_CONTAINER,
    C06_GENERIC_SCENE_ACTIVITY,
    CONTENT_REUSE_GROUP,
    GENERAL_REUSE_GROUP,
    MATERIAL_CATEGORIES,
    MATERIAL_CATEGORY_RULES_TEXT,
    classify_asset_strict_reuse,
    classify_strict_reuse_groups,
    classify_strict_reuse_library,
    export_strict_reuse_visual_check,
    normalize_strict_reuse_group,
    should_skip_from_index,
    write_strict_reuse_group_indexes,
)


def test_material_categories_are_gapless_six_class_ids():
    assert MATERIAL_CATEGORIES == (
        C00_STRICT_TEXT_PROBLEM_SKIP,
        C01_LANGUAGE_GLYPH_VISUAL,
        C02_STRUCTURE_DIAGRAM_VISUAL,
        C03_IRREPLACEABLE_ENTITY_EVENT_ACTION,
        C04_GENERIC_SUBJECT_OBJECT,
        C05_SCENE_DECOR_CONTAINER,
    )
    assert GENERAL_REUSE_GROUP == C05_SCENE_DECOR_CONTAINER
    assert CONTENT_REUSE_GROUP == C00_STRICT_TEXT_PROBLEM_SKIP


def test_legacy_category_ids_normalize_to_gapless_ids():
    assert normalize_strict_reuse_group("C03_specific_event_interaction") == C03_IRREPLACEABLE_ENTITY_EVENT_ACTION
    assert normalize_strict_reuse_group(C03_SPECIFIC_EVENT_INTERACTION) == C03_IRREPLACEABLE_ENTITY_EVENT_ACTION
    assert normalize_strict_reuse_group(C04_TEACHING_BOUND_ENTITY) == C03_IRREPLACEABLE_ENTITY_EVENT_ACTION
    assert normalize_strict_reuse_group("c04_teaching_bound_entity") == C03_IRREPLACEABLE_ENTITY_EVENT_ACTION
    assert normalize_strict_reuse_group("C04_single_subject_asset") == C04_GENERIC_SUBJECT_OBJECT
    assert normalize_strict_reuse_group("C05_generic_subject_asset") == C04_GENERIC_SUBJECT_OBJECT
    assert normalize_strict_reuse_group("C05_decor_layout_container") == C05_SCENE_DECOR_CONTAINER
    assert normalize_strict_reuse_group("C06_scene_decor_container") == C05_SCENE_DECOR_CONTAINER
    assert normalize_strict_reuse_group(C06_GENERIC_SCENE_ACTIVITY) == C05_SCENE_DECOR_CONTAINER


def test_material_category_prompt_exposes_only_gapless_active_outputs():
    rules = MATERIAL_CATEGORY_RULES_TEXT

    assert "6 类分类规则（v7" in rules
    assert "新分类只允许输出以下 6 个 ID" in rules
    for group in MATERIAL_CATEGORIES:
        assert group in rules
    assert "C04_teaching_bound_entity" not in rules
    assert "旧 C04" not in rules
    assert "兼容 C04" not in rules
    assert "C04 合并" not in rules
    assert "C05_generic_subject_asset" not in rules
    assert "C06_scene_decor_container" not in rules


def test_previous_v62_prompt_is_preserved_below_active_prompt():
    source = Path("edupptx/materials/strict_reuse_classifier.py").read_text(encoding="utf-8")

    active_index = source.index("MATERIAL_CATEGORY_RULES_TEXT = (")
    old_index = source.index("# OLD MATERIAL_CATEGORY_RULES_TEXT v6.2 before C03 event-boundary refinement.")
    assert active_index < old_index


def test_material_category_prompt_keeps_language_symbol_teaching_in_c01():
    rules = MATERIAL_CATEGORY_RULES_TEXT

    assert "4 个及以上独立教学字词" in rules
    assert "1-3 个明确汉字/词语/拼音" in rules
    assert "汉字、词语、拼音、读音、笔画、笔顺、部首、偏旁、字源、演变、构字、结构" in rules
    assert "只要教学对象仍是语言符号本体，归 C01" in rules
    assert "课文结构、阅读结构、写作结构、内容梳理、思维导图、人物关系图" in rules


def test_material_category_prompt_protects_c00_c01_c02_before_c03():
    rules = MATERIAL_CATEGORY_RULES_TEXT

    assert "按下面顺序判断；命中高优先级类别后不再下探" in rules
    assert "C03 不能抢走文字题图、语言符号、数学/物理/语文结构图" in rules
    assert "4 个及以上独立教学字词" in rules
    assert "1-3 个明确汉字/词语/拼音" in rules
    assert "语言符号本体教学归 C01" in rules
    assert "必要条件：遮住具体文字/数值后，仍能看出它是某类知识结构或原理图" in rules


def test_material_category_prompt_uses_caption_only_reuse_granularity():
    rules = MATERIAL_CATEGORY_RULES_TEXT

    assert "C03_irreplaceable_entity_event_action（不可替代实体/事件/动作类" in rules
    assert "分类依据是 caption 自身表达的复用粒度" in rules
    assert "不可替代语义命题" in rules
    assert "如果需要依赖课文名、theme、teaching_intent 或教学上下文才不可替代，不归 C03" in rules
    assert "C03 不要求主体必须是具名人物、唯一地点、唯一物体或课文专名" in rules
    assert "故事绑定的角色身份、主体关系、叙事动作或情绪状态组合" in rules
    assert "亲属关系、故事角色关系、角色功能关系" in rules
    assert "有意图、对象或结果的动作" in rules
    assert "姿态、道具、环境或氛围共同表达故事状态" in rules
    assert "把主体换成同类型另一个、把动作简化成普通姿态、或移除关系/情绪状态后" in rules
    assert "普通动物群体、普通人物组合、轻量社交动作、普通自然状态" in rules
    assert "比喻性视觉特征" in rules
    assert "整体场景、天气、氛围、远景、背景、页面装饰、空白容器" in rules
    assert "看到某个词就强制归类" in rules
    assert "团聚、寻找、告别" not in rules
    assert "把东西藏进口袋、摔东西拒绝出门" not in rules
    assert "轮椅上背对窗户" not in rules
    assert "雾孩子的不同卡通形象" not in rules
    assert "母亲站在床边温柔劝说男孩" not in rules
    assert "池塘里一群小蝌蚪围着青蛙妈妈游动" not in rules


def test_legacy_reuse_group_field_is_ignored_by_v3_classification():
    legacy_groups = [
        "none",
        "non_none",
        "math_problem",
        "physics_problem",
        "chinese_word_text",
        "chinese_passage_text",
    ]

    for raw_group in legacy_groups:
        result = classify_asset_strict_reuse(
            {
                "asset_id": raw_group,
                "asset_kind": "page_image",
                "reuse_group": raw_group,
                "strict_reuse_requires_exact_match": True,
            }
        )

        assert result["strict_reuse_group"] == C06_GENERIC_SCENE_ACTIVITY
        assert result["strict_reuse_confidence"] == 0.5
        assert result["strict_reuse_review_required"] is True
        assert result["strict_reuse_review_reasons"] == ["missing_upstream_reuse_classification"]
        assert "strict_reuse_requires_exact_match" not in result


def test_missing_upstream_group_defaults_general_and_requires_review():
    result = classify_asset_strict_reuse(
        {
            "asset_id": "plain",
            "asset_kind": "page_image",
            "content_prompt": "generic classroom illustration",
        }
    )

    assert result["strict_reuse_group"] == GENERAL_REUSE_GROUP
    assert result["strict_reuse_confidence"] == 0.5
    assert result["strict_reuse_review_required"] is True
    assert result["strict_reuse_review_reasons"] == ["missing_upstream_reuse_classification"]


def test_legacy_missing_classification_does_not_promote_by_keywords():
    cases = [
        {
            "asset_id": "character_grid",
            "asset_kind": "page_image",
            "content_prompt": "田字格中“争”“频”的笔顺书写示范",
            "constraints": [
                {"kind": "text", "value": "争", "importance": 2},
                {"kind": "text", "value": "频", "importance": 2},
            ],
            "core_keywords": ["田字格", "笔顺", "争", "频"],
        },
        {
            "asset_id": "math_problem",
            "asset_kind": "page_image",
            "content_prompt": "看图列式：脐橙8个，苹果是脐橙5倍，求总个数",
            "constraints": [
                {"kind": "math", "value": "8个", "importance": 2},
                {"kind": "math", "value": "5倍", "importance": 2},
            ],
            "core_keywords": ["看图列式", "脐橙", "苹果", "倍数"],
        },
    ]

    for asset in cases:
        result = classify_asset_strict_reuse(asset, infer_legacy_missing=True)

        assert result["strict_reuse_group"] == GENERAL_REUSE_GROUP
        assert result["strict_reuse_signals"] == ["legacy_default_generic_scene_activity"]


def test_classify_strict_reuse_groups_does_not_migrate_legacy_labels():
    index = {
        "assets": [
            {
                "asset_id": "math",
                "asset_kind": "page_image",
                "reuse_group": "math_problem",
                "strict_reuse_requires_exact_match": True,
            },
            {
                "asset_id": "plain",
                "asset_kind": "page_image",
                "reuse_group": "none",
                "strict_reuse_requires_exact_match": False,
            },
        ]
    }

    report = classify_strict_reuse_groups(index)

    assets = {asset["asset_id"]: asset for asset in index["assets"]}
    assert assets["math"]["strict_reuse_group"] == GENERAL_REUSE_GROUP
    assert assets["plain"]["strict_reuse_group"] == GENERAL_REUSE_GROUP
    assert assets["math"]["reuse_group"] == "math_problem"
    assert assets["plain"]["reuse_group"] == "none"
    assert "strict_reuse_requires_exact_match" not in assets["math"]
    assert "strict_reuse_requires_exact_match" not in assets["plain"]
    assert report["group_counts"][GENERAL_REUSE_GROUP] == 2
    assert report["review_required_count"] == 2
    assert report["review_reason_counts"]["missing_upstream_reuse_classification"] == 2


def test_classification_fields_survive_match_index_rebuild_without_old_field(tmp_path):
    db = {
        "schema_version": 1,
        "output_root": str(tmp_path),
        "assets": [
            {
                "asset_id": "math",
                "asset_kind": "page_image",
                "image_path": "math.png",
                "content_prompt": "division problem card",
                "context_summary": "math division diagram",
                "teaching_intent": "support division understanding",
                "strict_reuse_group": C01_LANGUAGE_GLYPH_VISUAL,
                "strict_reuse_confidence": 0.9,
                "strict_reuse_reason": "classified by LLM",
                "strict_reuse_signals": ["llm_reuse_group"],
                "strict_reuse_vlm_review_required": False,
                "strict_reuse_vlm_review_reasons": [],
            }
        ],
    }

    index = build_ai_image_match_index(db, library_root=tmp_path)

    asset = index["assets"][0]
    assert asset["strict_reuse_group"] == C01_LANGUAGE_GLYPH_VISUAL
    assert asset["strict_reuse_signals"] == ["llm_reuse_group"]
    assert "strict_reuse_requires_exact_match" not in asset
    assert "strict_reuse_vlm_review_required" not in asset
    assert "strict_reuse_vlm_review_reasons" not in asset


def test_classify_strict_reuse_library_writes_queue_and_binary_splits(tmp_path):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    index = {
        "schema_version": 13,
        "asset_root": str(library_dir),
        "assets": [
            {
                "asset_id": "missing",
                "asset_kind": "page_image",
                "image_path": "missing.png",
                "content_prompt": "unclassified asset",
            },
            {
                "asset_id": "legacy_math",
                "asset_kind": "page_image",
                "image_path": "math.png",
                "content_prompt": "math problem",
                "reuse_group": "math_problem",
            },
        ],
    }
    (library_dir / "ai_image_match_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    report, index_path = classify_strict_reuse_library(library_dir, split_dir="strict_splits")

    assert index_path == library_dir / "strict_splits"
    assert not (library_dir / "ai_image_match_index.json").exists()
    # legacy_math is not migrated locally; missing infers to GENERAL_REUSE_GROUP in legacy mode.
    general_split = library_dir / "strict_splits" / f"{GENERAL_REUSE_GROUP}.json"
    assert general_split.exists()
    general_assets = json.loads(general_split.read_text(encoding="utf-8"))["assets"]
    by_id = {a["asset_id"]: a for a in general_assets}
    assert by_id["legacy_math"]["strict_reuse_group"] == GENERAL_REUSE_GROUP
    assert by_id["legacy_math"]["reuse_group"] == "math_problem"
    # "missing" has no upstream label → legacy inference kicks in
    assert by_id["missing"]["strict_reuse_group"] == GENERAL_REUSE_GROUP
    assert report["review_required_count"] == 0
    queue_path = library_dir / "debug" / "strict_reuse_review_queue.jsonl"
    assert queue_path.exists()
    assert queue_path.read_text(encoding="utf-8") == ""
    assert not (library_dir / "strict_splits" / "strict_reuse_split_manifest.json").exists()
    assert not (library_dir / "ai_image_vlm_review.json").exists()


def test_strict_reuse_classifier_writes_background_split_separately(tmp_path):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    index = {
        "schema_version": 14,
        "asset_root": str(library_dir),
        "assets": [
            {
                "asset_id": "background",
                "asset_kind": "background",
                "image_path": "bg.png",
                "strict_reuse_group": GENERAL_REUSE_GROUP,
                "normalized_prompt": "light paper texture",
            },
            {
                "asset_id": "scene",
                "asset_kind": "page_image",
                "image_path": "scene.png",
                "strict_reuse_group": GENERAL_REUSE_GROUP,
                "content_prompt": "generic classroom scene",
            },
        ],
    }

    report = write_strict_reuse_group_indexes(index, library_dir, split_dir="strict_splits")

    background_split = json.loads((library_dir / "strict_splits" / "background.json").read_text(encoding="utf-8"))
    general_split = json.loads((library_dir / "strict_splits" / f"{GENERAL_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    assert report["groups"]["background"]["asset_count"] == 1
    assert [asset["asset_id"] for asset in background_split["assets"]] == ["background"]
    assert {asset["asset_id"] for asset in general_split["assets"]} == {"scene"}


def test_strict_reuse_classifier_migrates_legacy_background_split(tmp_path):
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    split_dir.mkdir(parents=True)
    (split_dir / f"{GENERAL_REUSE_GROUP}.json").write_text(
        json.dumps(
            {
                "schema_version": 14,
                "strict_reuse_group": GENERAL_REUSE_GROUP,
                "asset_root": str(library_dir),
                "assets": [
                    {
                        "asset_id": "legacy_background",
                        "asset_kind": "background",
                        "image_path": "bg.png",
                        "strict_reuse_group": GENERAL_REUSE_GROUP,
                        "normalized_prompt": "light paper texture",
                    },
                    {
                        "asset_id": "scene",
                        "asset_kind": "page_image",
                        "image_path": "scene.png",
                        "strict_reuse_group": GENERAL_REUSE_GROUP,
                        "content_prompt": "generic classroom scene",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report, _index_path = classify_strict_reuse_library(library_dir, write_debug=False)

    background_split = json.loads((split_dir / "background.json").read_text(encoding="utf-8"))
    general_split = json.loads((split_dir / f"{GENERAL_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    assert report["source_kind"] == "split_index"
    assert [asset["asset_id"] for asset in background_split["assets"]] == ["legacy_background"]
    assert {asset["asset_id"] for asset in general_split["assets"]} == {"scene"}


def test_classify_legacy_unclassified_index_defaults_without_keyword_inference(tmp_path):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    index = {
        "schema_version": 13,
        "asset_root": str(library_dir),
        "assets": [
            {
                "asset_id": "math",
                "asset_kind": "page_image",
                "image_path": "math.png",
                "content_prompt": "三位数加法数学题：123+456=579",
                "constraints": [{"kind": "math", "value": "123+456=579", "importance": 2}],
                "core_keywords": ["123+456=579", "数学题"],
            },
            {
                "asset_id": "pinyin",
                "asset_kind": "page_image",
                "image_path": "pinyin.png",
                "content_prompt": "生字拼音词语卡片展示",
                "core_keywords": ["生字", "拼音", "词语"],
            },
            {
                "asset_id": "plain",
                "asset_kind": "page_image",
                "image_path": "plain.png",
                "content_prompt": "学生围坐讨论的普通课堂插图",
                "core_keywords": ["学生", "讨论"],
            },
            {
                "asset_id": "blank",
                "asset_kind": "page_image",
                "image_path": "blank.png",
                "content_prompt": "卡通人物举着空白卡片，不要文字",
                "core_keywords": ["卡通人物", "空白卡片"],
            },
        ],
    }
    (library_dir / "ai_image_match_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    report, _index_path = classify_strict_reuse_library(library_dir, split_dir="strict_splits")

    assert report["classification_source"] == "legacy_unclassified_index_migration"
    assert report["group_counts"][CONTENT_REUSE_GROUP] == 0
    assert report["group_counts"][GENERAL_REUSE_GROUP] == 4
    assert report["review_required_count"] == 0
    content_split = json.loads((library_dir / "strict_splits" / f"{CONTENT_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    general_split = json.loads((library_dir / "strict_splits" / f"{GENERAL_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    assert content_split["assets"] == []
    assert {asset["asset_id"] for asset in general_split["assets"]} == {"math", "pinyin", "plain", "blank"}


def test_legacy_inference_keeps_generic_visual_assets_general():
    cases = [
        {
            "asset_id": "microscope",
            "asset_kind": "page_image",
            "content_prompt": "双目光学显微镜实物",
            "asset_category": "generic_tool",
            "constraints": [{"kind": "object", "value": "双目光学显微镜", "importance": 2}],
            "core_keywords": ["双目显微镜", "光学显微镜", "实物"],
        },
        {
            "asset_id": "triangle_ruler",
            "asset_kind": "page_image",
            "content_prompt": "带刻度的黄色直角三角尺",
            "asset_category": "generic_tool",
            "constraints": [
                {"kind": "object", "value": "直角三角尺", "importance": 2},
                {"kind": "object", "value": "刻度", "importance": 2},
            ],
            "core_keywords": ["直角三角尺", "刻度", "黄色"],
        },
        {
            "asset_id": "pinyin_gesture",
            "asset_kind": "page_image",
            "content_prompt": "双手摆出汉语拼音ɑ的手势",
            "asset_category": "learning_behavior",
            "constraints": [
                {"kind": "text", "value": "ɑ", "importance": 2},
                {"kind": "action", "value": "摆手势", "importance": 2},
            ],
            "core_keywords": ["ɑ", "汉语拼音", "手势", "双手"],
        },
        {
            "asset_id": "tangram",
            "asset_kind": "page_image",
            "content_prompt": "七巧板拼成的小房子造型",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "object", "value": "七巧板", "importance": 2},
                {"kind": "object", "value": "小房子造型", "importance": 2},
            ],
            "core_keywords": ["七巧板", "小房子", "拼图"],
        },
    ]

    for asset in cases:
        result = classify_asset_strict_reuse(asset, infer_legacy_missing=True)

        assert result["strict_reuse_group"] == GENERAL_REUSE_GROUP
        assert result["strict_reuse_signals"] == ["legacy_default_generic_scene_activity"]


def test_legacy_inference_keeps_art_portraits_and_incidental_labels_general():
    cases = [
        {
            "asset_id": "named_portrait_label",
            "asset_kind": "page_image",
            "content_prompt": "贾谊画像，上方有篆书“賈太傅”",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "entity", "value": "贾谊", "importance": 2},
                {"kind": "text", "value": "賈太傅", "importance": 2},
            ],
            "core_keywords": ["贾谊", "画像", "篆书", "賈太傅"],
        },
        {
            "asset_id": "poem_mood_painting",
            "asset_kind": "page_image",
            "content_prompt": "《过故人庄》古诗意境水墨乡村庭院画",
            "asset_category": "concept_scene",
            "constraints": [
                {"kind": "text", "value": "过故人庄", "importance": 2},
                {"kind": "scene", "value": "乡村庭院", "importance": 2},
            ],
            "core_keywords": ["过故人庄", "古诗", "水墨", "乡村庭院"],
        },
        {
            "asset_id": "book_cover_label",
            "asset_kind": "page_image",
            "content_prompt": "封面带有“千家詩”字样的旧诗集",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "text", "value": "千家詩", "importance": 2},
                {"kind": "object", "value": "旧诗集", "importance": 2},
            ],
            "core_keywords": ["千家詩", "旧诗集", "封面"],
        },
    ]

    for asset in cases:
        result = classify_asset_strict_reuse(asset, infer_legacy_missing=True)

        assert result["strict_reuse_group"] == GENERAL_REUSE_GROUP
        assert result["strict_reuse_signals"] == ["legacy_default_generic_scene_activity"]


def test_legacy_inference_does_not_promote_exact_text_math_and_physics_content():
    cases = [
        {
            "asset_id": "relation_diagram",
            "asset_kind": "page_image",
            "content_prompt": "中国少年与少年中国的关系示意图",
            "asset_category": "concept_scene",
            "constraints": [
                {"kind": "entity", "value": "中国少年", "importance": 1},
                {"kind": "object", "value": "关系示意图", "importance": 2},
            ],
            "core_keywords": ["中国少年", "少年中国", "关系示意图"],
        },
        {
            "asset_id": "physics_labels",
            "asset_kind": "page_image",
            "content_prompt": "平面镜MN和物体AB的物理示意图",
            "asset_category": "generic_diagram",
            "constraints": [
                {"kind": "object", "value": "平面镜", "importance": 2},
                {"kind": "text", "value": "MN", "importance": 2},
                {"kind": "text", "value": "AB", "importance": 2},
            ],
            "core_keywords": ["平面镜", "MN", "物体", "AB", "物理示意图"],
        },
        {
            "asset_id": "math_derivation",
            "asset_kind": "page_image",
            "content_prompt": "有理数分数混合运算解法1的分步推导过程",
            "asset_category": "generic_diagram",
            "constraints": [
                {"kind": "math", "value": "有理数分数混合运算", "importance": 2},
                {"kind": "action", "value": "分步推导", "importance": 2},
            ],
            "core_keywords": ["有理数", "分数混合运算", "分步推导"],
        },
        {
            "asset_id": "poem",
            "asset_kind": "page_image",
            "content_prompt": "古诗《早春呈水部张十八员外》及注释",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "text", "value": "早春呈水部张十八员外", "importance": 2},
                {"kind": "text", "value": "注释", "importance": 2},
            ],
            "core_keywords": ["早春呈水部张十八员外", "古诗", "注释"],
        },
        {
            "asset_id": "word_problem",
            "asset_kind": "page_image",
            "content_prompt": "看图列式：脐橙8个，苹果是脐橙5倍，求总个数",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "math", "value": "8个", "importance": 2},
                {"kind": "math", "value": "5倍", "importance": 2},
            ],
            "core_keywords": ["看图列式", "脐橙", "苹果", "倍数"],
        },
        {
            "asset_id": "character_grid",
            "asset_kind": "page_image",
            "content_prompt": "米字格中的红色楷书汉字“你”",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "text", "value": "你", "importance": 2},
                {"kind": "object", "value": "米字格", "importance": 2},
            ],
            "core_keywords": ["你", "米字格", "汉字"],
        },
        {
            "asset_id": "shopping_receipt",
            "asset_kind": "page_image",
            "content_prompt": "生活购物超市购物小票，包含品名、数量、单价、金额栏",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "text", "value": "品名", "importance": 2},
                {"kind": "text", "value": "单价", "importance": 2},
                {"kind": "text", "value": "金额", "importance": 2},
            ],
            "core_keywords": ["购物小票", "品名", "单价", "金额"],
        },
        {
            "asset_id": "numbered_tangram_triangle",
            "asset_kind": "page_image",
            "content_prompt": "两组用七巧板板块拼成的三角形拼图⑥和⑦",
            "asset_category": "content_specific",
            "constraints": [
                {"kind": "object", "value": "七巧板", "importance": 2},
                {"kind": "object", "value": "三角形", "importance": 2},
            ],
            "core_keywords": ["七巧板", "三角形", "拼图"],
        },
        {
            "asset_id": "route_time_diagram",
            "asset_kind": "page_image",
            "content_prompt": "家、健身公园、商场、办公楼的行程路线时间示意图",
            "asset_category": "generic_diagram",
            "constraints": [
                {"kind": "object", "value": "行程路线时间示意图", "importance": 2},
                {"kind": "text", "value": "家", "importance": 2},
            ],
            "core_keywords": ["家", "健身公园", "商场", "办公楼", "行程路线", "时间示意图"],
        },
    ]

    for asset in cases:
        result = classify_asset_strict_reuse(asset, infer_legacy_missing=True)

        assert result["strict_reuse_group"] == GENERAL_REUSE_GROUP
        assert result["strict_reuse_signals"] == ["legacy_default_generic_scene_activity"]
        assert result["strict_reuse_review_required"] is False


def test_legacy_inference_ignores_context_summary_and_bare_count_noise():
    result = classify_asset_strict_reuse(
        {
            "asset_id": "ordinary_count_scene",
            "asset_kind": "page_image",
            "content_prompt": "跑步的小学生插画，共13名学生",
            "detail_prompt": "画面中有长方形桌面和正方形装饰块",
            "context_summary": "辅助课文教学的普通课堂配图",
            "constraints": [
                {"kind": "math", "subtype": "teaching_content", "value": "13", "importance": 2},
                {"kind": "text", "subtype": "teaching_content", "value": "图1", "importance": 2},
            ],
            "core_keywords": ["小学生", "跑步", "13名"],
        },
        infer_legacy_missing=True,
    )

    assert result["strict_reuse_group"] == GENERAL_REUSE_GROUP
    assert result["strict_reuse_signals"] == ["legacy_default_generic_scene_activity"]


def test_classify_prefers_existing_split_indexes_over_legacy_main_index(tmp_path):
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    split_dir.mkdir(parents=True)
    (library_dir / "ai_image_match_index.json").write_text(
        json.dumps(
            {
                "schema_version": 13,
                "assets": [
                    {
                        "asset_id": "stale",
                        "asset_kind": "page_image",
                        "image_path": "stale.png",
                        "content_prompt": "stale unclassified item",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (split_dir / f"{GENERAL_REUSE_GROUP}.json").write_text(
        json.dumps({"schema_version": 13, "strict_reuse_group": GENERAL_REUSE_GROUP, "assets": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (split_dir / f"{CONTENT_REUSE_GROUP}.json").write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": CONTENT_REUSE_GROUP,
                "assets": [
                    {
                        "asset_id": "kept",
                        "asset_kind": "page_image",
                        "image_path": "kept.png",
                        "content_prompt": "已拆分的数学题素材",
                        "strict_reuse_group": CONTENT_REUSE_GROUP,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report, _index_path = classify_strict_reuse_library(library_dir)

    assert report["source_kind"] == "split_index"
    assert report["source_index_path"] == str(split_dir)
    assert report["group_counts"][CONTENT_REUSE_GROUP] == 1
    assert report["group_counts"][GENERAL_REUSE_GROUP] == 0
    assert not (library_dir / "ai_image_match_index.json").exists()


def test_classify_can_force_rebuild_from_main_index_over_existing_split(tmp_path):
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    split_dir.mkdir(parents=True)
    (library_dir / "ai_image_match_index.json").write_text(
        json.dumps(
            {
                "schema_version": 13,
                "assets": [
                    {
                        "asset_id": "pinyin",
                        "asset_kind": "page_image",
                        "image_path": "pinyin.png",
                        "content_prompt": "生字拼音词语卡片展示",
                        "core_keywords": ["生字", "拼音", "词语"],
                    },
                    {
                        "asset_id": "plain",
                        "asset_kind": "page_image",
                        "image_path": "plain.png",
                        "content_prompt": "学生围坐讨论的普通课堂插图",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (split_dir / f"{CONTENT_REUSE_GROUP}.json").write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": CONTENT_REUSE_GROUP,
                "assets": [
                    {
                        "asset_id": "bad_previous_split",
                        "asset_kind": "page_image",
                        "image_path": "bad.png",
                        "content_prompt": "stale bad split",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (split_dir / f"{GENERAL_REUSE_GROUP}.json").write_text(
        json.dumps({"schema_version": 13, "strict_reuse_group": GENERAL_REUSE_GROUP, "assets": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    report, _index_path = classify_strict_reuse_library(
        library_dir,
        prefer_split_index=False,
        write_debug=False,
    )

    assert report["source_kind"] == "legacy_match_index"
    assert report["group_counts"][CONTENT_REUSE_GROUP] == 0
    assert report["group_counts"][GENERAL_REUSE_GROUP] == 2
    content_split = json.loads((split_dir / f"{CONTENT_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    general_split = json.loads((split_dir / f"{GENERAL_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    assert content_split["assets"] == []
    assert {asset["asset_id"] for asset in general_split["assets"]} == {"pinyin", "plain"}


def test_classify_recomputes_split_indexes_written_by_legacy_inference(tmp_path):
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    split_dir.mkdir(parents=True)
    stale_content_asset = {
        "asset_id": "ordinary_count_scene",
        "asset_kind": "page_image",
        "image_path": "count.png",
        "content_prompt": "跑步的小学生插画，共13名学生",
        "constraints": [{"kind": "math", "value": "13", "importance": 2}],
        "strict_reuse_group": CONTENT_REUSE_GROUP,
        "strict_reuse_signals": ["legacy_content_keyword"],
    }
    stale_general_asset = {
        "asset_id": "pinyin",
        "asset_kind": "page_image",
        "image_path": "pinyin.png",
        "content_prompt": "生字拼音词语卡片展示",
        "core_keywords": ["生字", "拼音", "词语"],
        "strict_reuse_group": GENERAL_REUSE_GROUP,
        "strict_reuse_signals": ["legacy_default_generic_scene_activity"],
    }
    (split_dir / f"{CONTENT_REUSE_GROUP}.json").write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": CONTENT_REUSE_GROUP,
                "assets": [stale_content_asset],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (split_dir / f"{GENERAL_REUSE_GROUP}.json").write_text(
        json.dumps(
            {
                "schema_version": 13,
                "strict_reuse_group": GENERAL_REUSE_GROUP,
                "assets": [stale_general_asset],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report, _index_path = classify_strict_reuse_library(library_dir, write_debug=False)

    assert report["source_kind"] == "split_index"
    assert report["group_counts"][CONTENT_REUSE_GROUP] == 0
    assert report["group_counts"][GENERAL_REUSE_GROUP] == 2
    content_split = json.loads((split_dir / f"{CONTENT_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    general_split = json.loads((split_dir / f"{GENERAL_REUSE_GROUP}.json").read_text(encoding="utf-8"))
    assert content_split["assets"] == []
    assert {asset["asset_id"] for asset in general_split["assets"]} == {"ordinary_count_scene", "pinyin"}


def test_classifier_does_not_touch_existing_vlm_review_sidecar(tmp_path):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    index = {
        "schema_version": 13,
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "generic classroom illustration",
                "strict_reuse_group": GENERAL_REUSE_GROUP,
            }
        ],
    }
    existing_sidecar = {
        "schema_version": 7,
        "assets": {
            "page": {
                "asset_id": "page",
                "model": "real-vlm",
                "action": "manual_review",
                "manual_review_required": True,
                "manual_review_reasons": ["low_match_quality"],
                "visual_reuse_group": CONTENT_REUSE_GROUP,
                "strict_reuse_group_mismatch": True,
            }
        },
    }
    (library_dir / "ai_image_match_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    sidecar_path = library_dir / "ai_image_vlm_review.json"
    sidecar_path.write_text(json.dumps(existing_sidecar, ensure_ascii=False), encoding="utf-8")
    before = sidecar_path.read_text(encoding="utf-8")

    classify_strict_reuse_library(library_dir, write_debug=False)

    assert sidecar_path.read_text(encoding="utf-8") == before


def test_export_strict_reuse_visual_check_splits_images_without_touching_library(tmp_path):
    library_dir = tmp_path / "library"
    image_dir = library_dir / "ai_images"
    image_dir.mkdir(parents=True)
    (image_dir / "plain.png").write_bytes(b"plain-image")
    (image_dir / "math.png").write_bytes(b"math-image")
    index = {
        "schema_version": 13,
        "assets": [
            {
                "asset_id": "plain",
                "asset_kind": "page_image",
                "image_path": "ai_images/plain.png",
                "content_prompt": "generic tree illustration",
                "reuse_group": "none",
            },
            {
                "asset_id": "math",
                "asset_kind": "page_image",
                "image_path": "ai_images/math.png",
                "content_prompt": "division problem",
                "reuse_group": "math_problem",
            },
        ],
    }
    index_path = library_dir / "ai_image_match_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    before = index_path.read_text(encoding="utf-8")
    output_root = tmp_path / "visual_check"
    (output_root / "none").mkdir(parents=True)
    (output_root / "non_none").mkdir()

    manifest, output_dir = export_strict_reuse_visual_check(library_dir, output_root)

    assert manifest["group_counts"][CONTENT_REUSE_GROUP] == 0
    assert manifest["group_counts"][GENERAL_REUSE_GROUP] == 2
    assert manifest["missing_image_count"] == 0
    assert len(list((output_dir / GENERAL_REUSE_GROUP).glob("*.png"))) == 2
    assert not (output_dir / "none").exists()
    assert not (output_dir / "non_none").exists()
    assert (output_dir / "manifest.json").exists()
    assert "html_path" not in manifest
    assert not (output_dir / "index.html").exists()
    assert index_path.read_text(encoding="utf-8") == before


# --- should_skip_from_index tests ---


def test_skip_from_index_c00():
    asset = {"asset_kind": "page_image", "strict_reuse_group": "C00_strict_text_problem_skip"}
    assert should_skip_from_index(asset) is True


def test_skip_from_index_low_vlm_quality():
    asset = {"asset_kind": "page_image", "strict_reuse_group": "C04_generic_subject_object",
             "vlm_match_quality": 0.2}
    assert should_skip_from_index(asset) is True


def test_skip_from_index_vlm_quality_above_threshold():
    asset = {"asset_kind": "page_image", "strict_reuse_group": "C04_generic_subject_object",
             "vlm_match_quality": 0.5}
    assert should_skip_from_index(asset) is False


def test_skip_from_index_other_bucket_no_padding():
    asset = {"asset_kind": "page_image", "strict_reuse_group": "C04_generic_subject_object",
             "aspect_ratio": "32:15", "padding_capacity": "none"}
    assert should_skip_from_index(asset) is True


def test_skip_from_index_other_bucket_with_padding():
    asset = {"asset_kind": "page_image", "strict_reuse_group": "C04_generic_subject_object",
             "aspect_ratio": "32:15", "padding_capacity": "mid"}
    assert should_skip_from_index(asset) is False


def test_skip_from_index_normal_asset():
    asset = {"asset_kind": "page_image", "strict_reuse_group": "C04_generic_subject_object",
             "aspect_ratio": "4:3", "vlm_match_quality": 0.8, "padding_capacity": "mid"}
    assert should_skip_from_index(asset) is False
