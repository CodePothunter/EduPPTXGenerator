import inspect
from pathlib import Path

from edupptx.config import Config
from edupptx.materials import ai_image_asset_db


ROOT = Path(__file__).resolve().parents[1]


def test_known_obsolete_files_are_removed():
    obsolete_paths = (
        ROOT / "edupptx" / "materials" / "backgrounds.py",
        ROOT / "tests" / "test_archive_unindexed_ppt_skip_images.py",
        ROOT / "tests" / "test_reuse_policy_new_gates.py",
    )

    assert [str(path.relative_to(ROOT)) for path in obsolete_paths if path.exists()] == []


def test_reuse_policy_signatures_do_not_keep_dead_constraint_cache_parameter():
    for func in (
        ai_image_asset_db._apply_reuse_policy_to_ranked_candidates,
        ai_image_asset_db._finalize_reuse_candidate_collection,
    ):
        assert "constraint_embedding_cache" not in inspect.signature(func).parameters


def test_duplicate_matching_no_longer_keeps_removed_constraint_hook():
    assert not hasattr(ai_image_asset_db, "_duplicate_identity_constraints_conflict")


def test_config_no_longer_exposes_legacy_background_cache_dir():
    assert "cache_dir" not in Config.__dataclass_fields__


def test_planning_legacy_compat_wrappers_are_removed():
    from edupptx.planning import content_planner, prompts

    assert not hasattr(content_planner, "generate_planning_draft")
    assert not hasattr(prompts, "build_planning_system_prompt")
    assert not hasattr(prompts, "build_planning_user_prompt")


def test_general_rule_has_no_phantom_skip_categories():
    # Only C00 is a skip class; C04/C05/C06 never existed in the 4-class taxonomy.
    from edupptx.materials.general_rules import GENERAL_RULE

    for phantom in ("C04", "C05", "C06"):
        assert phantom not in GENERAL_RULE


def test_vlm_prompts_match_the_four_class_taxonomy():
    from edupptx.materials import vlm_asset_enricher as v

    assert "7 个类别" not in v.VLM_SYSTEM_PROMPT
    assert "C00-C06" not in v.VLM_SYSTEM_PROMPT
    assert "4 个类别" in v.VLM_SYSTEM_PROMPT
    for phantom in ("C04", "C05", "C06"):
        assert phantom not in v.VLM_SYSTEM_PROMPT
    # The dead, immediately-shadowed first VLM_REDESCRIBE assignment is gone —
    # the surviving prompt is the query-based one.
    assert "完整 query" in v.VLM_REDESCRIBE_SYSTEM_PROMPT


def test_chart_template_map_filenames_all_exist():
    from edupptx.design import prompts

    base = Path(prompts.__file__).parent / "chart_templates"
    missing = [
        fn
        for files in prompts._CHART_TEMPLATE_MAP.values()
        for fn in files
        if not (base / fn).exists()
    ]
    assert missing == []


def test_reuse_check_command_help_is_not_mojibake():
    from edupptx.cli import reuse_check

    help_by_name = {p.name: (getattr(p, "help", "") or "") for p in reuse_check.params}
    assert help_by_name["requirements"] == "附加要求"
    assert help_by_name["research"] == "启用联网搜索充实内容"
    assert help_by_name["output"] == "输出目录"
    for text in help_by_name.values():
        for frag in ("闄", "杈", "鍚", "鐩", "鏂"):
            assert frag not in text


def test_reuse_summary_preview_filename_not_misspelled():
    family = ROOT / "edupptx" / "design" / "page_templates" / "复用"
    assert not (family / "summery.png").exists()
    assert (family / "summary.png").exists()
