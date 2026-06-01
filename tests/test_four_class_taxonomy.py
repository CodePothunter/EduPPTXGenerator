from edupptx.materials.reuse_policy import reuse_level_from_material_category
from edupptx.materials.strict_reuse_classifier import (
    MATERIAL_CATEGORIES,
    MATERIAL_CATEGORY_RULES_TEXT,
    normalize_strict_reuse_group,
)


def test_active_set_is_contiguous_four_classes():
    assert MATERIAL_CATEGORIES == (
        "C00_strict_text_problem_skip",
        "C01_irreplaceable_entity_event_action",
        "C02_generic_subject_object",
        "C03_scene_decor_container",
    )


def test_old_category_ids_are_not_compatible_aliases():
    for old_id in (
        "C01_language_glyph_visual",
        "C02_structure_diagram_visual",
        "C03_irreplaceable_entity_event_action",
        "C04_generic_subject_object",
        "C05_scene_decor_container",
        "C06_scene_decor_container",
        "C06_generic_scene_activity",
    ):
        assert normalize_strict_reuse_group(old_id, default="INVALID") == "INVALID"


def test_rules_text_lists_only_contiguous_ids():
    text = MATERIAL_CATEGORY_RULES_TEXT
    for category_id in (
        "C00_strict_text_problem_skip",
        "C01_irreplaceable_entity_event_action",
        "C02_generic_subject_object",
        "C03_scene_decor_container",
    ):
        assert category_id in text
    for old_id in (
        "C01_language_glyph_visual",
        "C02_structure_diagram_visual",
        "C03_irreplaceable_entity_event_action",
        "C04_generic_subject_object",
        "C05_scene_decor_container",
    ):
        assert old_id not in text
    assert "语言符号" in text and "知识结构" in text


def test_contiguous_reuse_levels():
    assert reuse_level_from_material_category("C00_strict_text_problem_skip") == "skip"
    assert reuse_level_from_material_category("C01_irreplaceable_entity_event_action") == "strict"
    assert reuse_level_from_material_category("C02_generic_subject_object") == "medium"
    assert reuse_level_from_material_category("C03_scene_decor_container") == "loose"
