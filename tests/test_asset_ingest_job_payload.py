from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from edupptx.materials.ai_image_asset_db import ingest_ai_image_asset_job


def test_ingest_ai_image_asset_job_uses_seeded_metadata_without_llm(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", "1")
    session_dir = tmp_path / "output" / "session_a"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    image_path = materials_dir / "page_01_illustration_1.png"
    Image.new("RGB", (16, 12), "white").save(image_path)
    library_dir = tmp_path / "materials_library_ppt"

    asset = {
        "asset_id": "aiimg_seeded_job",
        "asset_kind": "page_image",
        "image_path": "materials/page_01_illustration_1.png",
        "aspect_ratio": "4:3",
        "caption": "seeded eclipse diagram",
        "context_summary": "seeded context summary",
        "teaching_intent": "seeded teaching intent",
        "subject": "物理",
        "grade_norm": "八年级",
        "grade_band": "high",
        "general": False,
        "strict_reuse_group": "C03_scene_decor_container",
        "_reuse_target_metadata_seeded": True,
    }

    class RaisingClient:
        def chat_json(self, **_kwargs):
            raise AssertionError("keyword LLM should not be called for seeded job assets")

    db, target = ingest_ai_image_asset_job(
        {
            "job_id": "job_seeded",
            "session_dir": str(session_dir),
            "library_dir": str(library_dir),
            "assets": [asset],
        },
        keyword_client=RaisingClient(),
    )

    assert target == library_dir.resolve() / "strict_reuse_indexes"
    assert db["asset_count"] == 1
    ingested = db["assets"][0]
    assert ingested["asset_id"] == "aiimg_seeded_job"
    assert ingested["caption"] == "seeded eclipse diagram"
    assert ingested["context_summary"] == "seeded context summary"
    assert ingested["strict_reuse_group"] == "C03_scene_decor_container"
    assert (library_dir / "ai_images" / "aiimg_seeded_job.png").exists()


def test_ingest_ai_image_asset_job_skips_c00_assets_without_copying(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", "1")
    session_dir = tmp_path / "output" / "session_c00"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    image_path = materials_dir / "page_01_illustration_1.png"
    Image.new("RGB", (16, 12), "white").save(image_path)
    library_dir = tmp_path / "materials_library"

    asset = {
        "asset_id": "aiimg_c00_job",
        "asset_kind": "page_image",
        "image_path": "materials/page_01_illustration_1.png",
        "aspect_ratio": "4:3",
        "caption": "exact text worksheet",
        "context_summary": "exact text page",
        "teaching_intent": "answer a specific text problem",
        "subject": "math",
        "grade_norm": "grade2",
        "grade_band": "lower",
        "general": False,
        "strict_reuse_group": "C00_strict_text_problem_skip",
        "_reuse_target_metadata_seeded": True,
    }

    db, target = ingest_ai_image_asset_job(
        {
            "job_id": "job_c00",
            "session_dir": str(session_dir),
            "library_dir": str(library_dir),
            "assets": [asset],
        },
        keyword_client=None,
    )

    assert target == library_dir.resolve() / "strict_reuse_indexes"
    assert db["asset_count"] == 0
    assert db["assets"] == []
    assert not (library_dir / "ai_images" / "aiimg_c00_job.png").exists()
    assert any("skipped C00 asset" in warning for warning in db.get("warnings", []))


def test_ingest_ai_image_asset_job_updates_embedding_incrementally(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", "local-model")

    import numpy as np
    import edupptx.materials.ai_image_asset_db as image_db

    encoded_texts: list[str] = []

    def fake_encode_embedding_texts(texts, **_kwargs):
        encoded_texts.extend(texts)
        return np.asarray(
            [[float(index + 1), 0.0, 1.0] for index, _text in enumerate(texts)],
            dtype="float32",
        )

    monkeypatch.setattr("edupptx.reuse._embedding._encode_embedding_texts", fake_encode_embedding_texts)

    library_dir = tmp_path / "materials_library"
    first_session = tmp_path / "output" / "session_first"
    first_materials = first_session / "materials"
    first_materials.mkdir(parents=True)
    Image.new("RGB", (16, 12), "white").save(first_materials / "first.png")
    first_asset = {
        "asset_id": "aiimg_first",
        "asset_kind": "page_image",
        "image_path": "materials/first.png",
        "aspect_ratio": "4:3",
        "caption": "first generated image",
        "context_summary": "first context",
        "teaching_intent": "first intent",
        "subject": "math",
        "grade_norm": "grade2",
        "grade_band": "lower",
        "general": True,
        "strict_reuse_group": "C03_scene_decor_container",
        "_reuse_target_metadata_seeded": True,
    }

    ingest_ai_image_asset_job(
        {
            "job_id": "job_first",
            "session_dir": str(first_session),
            "library_dir": str(library_dir),
            "assets": [first_asset],
        },
        keyword_client=None,
    )
    assert encoded_texts == ["first generated image"]

    encoded_texts.clear()
    second_session = tmp_path / "output" / "session_second"
    second_materials = second_session / "materials"
    second_materials.mkdir(parents=True)
    Image.new("RGB", (16, 12), "black").save(second_materials / "second.png")
    second_asset = {
        "asset_id": "aiimg_second",
        "asset_kind": "page_image",
        "image_path": "materials/second.png",
        "aspect_ratio": "4:3",
        "caption": "second generated image",
        "context_summary": "second context",
        "teaching_intent": "second intent",
        "subject": "math",
        "grade_norm": "grade2",
        "grade_band": "lower",
        "general": True,
        "strict_reuse_group": "C03_scene_decor_container",
        "_reuse_target_metadata_seeded": True,
    }

    ingest_ai_image_asset_job(
        {
            "job_id": "job_second",
            "session_dir": str(second_session),
            "library_dir": str(library_dir),
            "assets": [second_asset],
        },
        keyword_client=None,
    )

    assert encoded_texts == ["second generated image"]
    meta = json.loads((library_dir / "ai_image_embedding_meta.json").read_text(encoding="utf-8"))
    assert meta["asset_count"] == 2
    assert meta["reused_asset_count"] == 1
    assert meta["encoded_asset_count"] == 1
