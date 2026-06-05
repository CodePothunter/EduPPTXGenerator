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
