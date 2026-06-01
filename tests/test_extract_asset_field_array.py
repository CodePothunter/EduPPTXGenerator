from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def test_extract_field_values_reads_split_indexes_only_by_default(tmp_path):
    module = _load_module()
    source_dir = tmp_path / "strict_reuse_indexes"
    source_dir.mkdir()
    _write_json(
        source_dir / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "assets": [
                {"asset_id": "a", "content_prompt": " apple card "},
                {"asset_id": "b", "content_prompt": ""},
                {"asset_id": "c", "content_prompt": "line\nbreak prompt"},
            ],
        },
    )
    _write_json(
        source_dir / "general_content_prompt_audit_suspects.json",
        {
            "assets": [
                {"asset_id": "audit", "content_prompt": "duplicate audit prompt"},
            ],
        },
    )

    values = module.extract_field_values(source_dir)

    assert values == ["apple card", "line break prompt"]


def test_main_writes_json_string_array(tmp_path):
    module = _load_module()
    source_dir = tmp_path / "strict_reuse_indexes"
    output_path = tmp_path / "content_prompts.json"
    source_dir.mkdir()
    _write_json(
        source_dir / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "assets": [
                {"asset_id": "a", "content_prompt": "classroom scene"},
            ],
        },
    )

    result = module.main(
        [
            "--source-dir",
            str(source_dir),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == ["classroom scene"]


def test_groups_filter_accepts_c_range_and_prefixes(tmp_path):
    module = _load_module()
    source_dir = tmp_path / "strict_reuse_indexes"
    source_dir.mkdir()
    _write_json(
        source_dir / "C00_strict_text_problem_skip.json",
        {
            "strict_reuse_group": "C00_strict_text_problem_skip",
            "assets": [{"asset_id": "c02", "content_prompt": "diagram"}],
        },
    )
    _write_json(
        source_dir / "C01_irreplaceable_entity_event_action.json",
        {
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "assets": [{"asset_id": "c03", "content_prompt": "story event"}],
        },
    )
    _write_json(
        source_dir / "C02_generic_subject_object.json",
        {
            "strict_reuse_group": "C02_generic_subject_object",
            "assets": [{"asset_id": "c04", "content_prompt": "single object"}],
        },
    )
    _write_json(
        source_dir / "C03_scene_decor_container.json",
        {
            "strict_reuse_group": "C03_scene_decor_container",
            "assets": [{"asset_id": "c05", "content_prompt": "scene"}],
        },
    )

    values = module.extract_field_values(source_dir, groups=["C01-C03"])

    assert values == ["story event", "single object", "scene"]


def test_default_output_path_adds_group_suffix(tmp_path):
    module = _load_module()

    path = module.default_output_path(tmp_path, "content_prompt", groups=["C01-C03"])

    assert path == tmp_path / "content_prompts_C01_C02_C03.json"


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "extract_asset_field_array.py"
    spec = importlib.util.spec_from_file_location("extract_asset_field_array", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
