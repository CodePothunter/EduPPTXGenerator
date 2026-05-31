from __future__ import annotations

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
        "strict_reuse_group": "C05_scene_decor_container",
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
    assert ingested["strict_reuse_group"] == "C05_scene_decor_container"
    assert (library_dir / "ai_images" / "aiimg_seeded_job.png").exists()
