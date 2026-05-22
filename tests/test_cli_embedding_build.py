import json

from click.testing import CliRunner

from edupptx.cli import main
from edupptx.materials.ai_image_asset_db import write_ai_image_split_match_indexes


def test_embedding_build_command_writes_sidecars_and_updates_match_index(tmp_path, monkeypatch):
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)

    def fake_encode_embedding_texts(texts, **_kwargs):
        import numpy as np

        return np.asarray(
            [[float(index + 1), 0.0, 1.0] for index, _text in enumerate(texts)],
            dtype="float32",
        )

    monkeypatch.setattr(
        "edupptx.materials.ai_image_asset_db._encode_embedding_texts",
        fake_encode_embedding_texts,
    )

    library_dir = tmp_path / "materials_library_ppt"
    library_dir.mkdir()
    match_index = {
        "schema_version": 13,
        "asset_count": 1,
        "assets": [
            {
                "asset_id": "kbpptx_1",
                "asset_kind": "page_image",
                "image_path": "pptx_images/page.png",
                "content_prompt": "线段图展示倍数关系",
                "context_summary": "线段图呈现数量倍数关系",
                "teaching_intent": "辅助理解倍数应用题",
                "core_keywords": ["线段图", "倍数关系"],
            }
        ],
    }
    write_ai_image_split_match_indexes(match_index, library_dir)

    result = CliRunner().invoke(main, ["embedding-build", str(library_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["asset_count"] == 1
    assert (library_dir / "ai_image_embedding_index.npz").exists()
    assert (library_dir / "ai_image_embedding_meta.json").exists()
    assert payload["split_index_dir"].endswith("strict_reuse_indexes")


def test_asset_ingest_vlm_review_defaults_on_and_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("VLM_APIKEY", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)
    monkeypatch.delenv("GEN_APIKEY", raising=False)
    monkeypatch.delenv("GEN_MODEL", raising=False)
    output_root = tmp_path / "output"
    output_root.mkdir()
    library_dir = tmp_path / "materials_library"
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")

    default_result = CliRunner().invoke(
        main,
        [
            "asset-ingest",
            "--output-root",
            str(output_root),
            "--library-dir",
            str(library_dir),
            "--env-file",
            str(env_file),
            "--json",
        ],
    )

    assert default_result.exit_code == 1
    default_payload = json.loads(default_result.output)
    assert default_payload["kind"] == "MissingVlmConfig"

    disabled_result = CliRunner().invoke(
        main,
        [
            "asset-ingest",
            "--output-root",
            str(output_root),
            "--library-dir",
            str(library_dir),
            "--env-file",
            str(env_file),
            "--VLM_review=False",
            "--json",
        ],
    )

    assert disabled_result.exit_code == 0, disabled_result.output
    disabled_payload = json.loads(disabled_result.output)
    assert disabled_payload["ok"] is True
    assert disabled_payload["VLM_review"] is False


def test_strict_reuse_classify_command_tags_library(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    library_dir.mkdir()
    index_path = library_dir / "ai_image_match_index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 13,
                "asset_count": 1,
                "assets": [
                    {
                        "asset_id": "math",
                        "asset_kind": "page_image",
                        "image_path": "ai_images/math.png",
                        "subject": "数学",
                        "asset_category": "content_specific",
                        "content_prompt": "36除以2的笔算除法竖式分步演示",
                        "constraints": [{"kind": "math", "value": "36÷2", "importance": 2}],
                        "strict_reuse_group": "math_problem",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        [
            "strict-reuse-classify",
            str(library_dir),
            "--split-dir",
            "strict_splits",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["group_counts"]["content_reuse"] == 1
    assert not index_path.exists()
    content_split = json.loads((library_dir / "strict_splits" / "content_reuse.json").read_text(encoding="utf-8"))
    assert content_split["assets"][0]["strict_reuse_group"] == "content_reuse"
    assert not (library_dir / "strict_splits" / "strict_reuse_split_manifest.json").exists()
    assert not (library_dir / "ai_image_vlm_review.json").exists()
    assert "vlm_review_sidecar" not in payload


def test_strict_reuse_export_check_command_copies_general_and_content_reuse(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    image_dir.mkdir(parents=True)
    (image_dir / "none.png").write_bytes(b"none-image")
    (image_dir / "strict.png").write_bytes(b"strict-image")
    index_path = library_dir / "ai_image_match_index.json"
    index_payload = {
        "schema_version": 13,
        "asset_count": 2,
        "assets": [
            {
                "asset_id": "none_asset",
                "asset_kind": "page_image",
                "image_path": "pptx_images/none.png",
                "strict_reuse_group": "none",
                "content_prompt": "普通插画",
            },
            {
                "asset_id": "strict_asset",
                "asset_kind": "page_image",
                "image_path": "pptx_images/strict.png",
                "strict_reuse_group": "math_problem",
                "content_prompt": "36除以2的笔算除法竖式分步演示",
            },
        ],
    }
    index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_dir = tmp_path / "visual_check"

    result = CliRunner().invoke(
        main,
        [
            "strict-reuse-export-check",
            str(library_dir),
            "--output-dir",
            str(output_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["asset_library_unchanged"] is True
    assert payload["group_counts"] == {"content_reuse": 1, "general_reuse": 1}
    assert len(list((output_dir / "general_reuse").glob("*.png"))) == 1
    assert len(list((output_dir / "content_reuse").glob("*.png"))) == 1
    assert json.loads(index_path.read_text(encoding="utf-8")) == index_payload
