from pathlib import Path

from PIL import Image

from edupptx.materials.ai_image_asset_db import (
    _build_match_text,
    _build_background_route,
    _build_reuse_target_asset,
    _target_keyword_cache_key,
    build_ai_image_asset_db,
    _iter_page_image_assets,
    update_ai_image_asset_library,
)
from edupptx.materials.background_generator import build_background_content_prompt
from edupptx.materials.reuse_query_cache import save_reuse_query_cache


def test_target_asset_carries_caption_when_provided():
    target = _build_reuse_target_asset(
        asset_kind="page_image",
        prompt="foggy city with houses streets pedestrians and a hidden black cat",
        prompt_route=None,
        theme="Where is the fog",
        grade="grade 2",
        subject="language",
        page_title="Fog",
        page_type="content",
        role="illustration",
        aspect_ratio="4:3",
        caption="foggy city street scene",
    )

    assert target["caption"] == "foggy city street scene"
    assert "foggy city street scene" in _build_match_text(target)
    assert "content_prompt" not in target


def test_generated_asset_carries_caption_from_plan_need(tmp_path: Path):
    session_dir = tmp_path / "session"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    image_path = materials_dir / "page_01_illustration_1.png"
    image_path.write_bytes(b"fake image")

    page = {
        "page_number": 1,
        "page_type": "content",
        "title": "Fog",
        "material_needs": {
            "images": [
                {
                    "source": "ai_generate",
                    "role": "illustration",
                    "query": "foggy city with houses streets pedestrians and a hidden black cat",
                    "caption": "foggy city street scene",
                    "aspect_ratio": "4:3",
                }
            ]
        },
    }

    assets = list(
        _iter_page_image_assets(
            root=tmp_path,
            session_dir=session_dir,
            plan_path=session_dir / "plan.json",
            materials_dir=materials_dir,
            context={"theme": "Where is the fog", "grade": "grade 2", "subject": "language"},
            page=page,
            page_index=0,
        )
    )

    assert assets[0]["caption"] == "foggy city street scene"
    assert "content_prompt" not in assets[0]


def test_generated_asset_reuses_match_stage_metadata_from_query_cache(tmp_path: Path):
    session_dir = tmp_path / "session"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    (session_dir / "slides").mkdir()
    (session_dir / "slides_raw").mkdir()
    image_path = materials_dir / "page_01_illustration_1.png"
    image_path.write_bytes(b"fake image")
    query = "foggy city with houses streets pedestrians and a hidden black cat"
    caption = "foggy city street scene"
    plan = {
        "meta": {"topic": "Where is the fog", "grade": "五年级", "subject": "语文"},
        "pages": [
            {
                "page_number": 1,
                "page_type": "content",
                "title": "Fog",
                "material_needs": {
                    "images": [
                        {
                            "source": "ai_generate",
                            "role": "illustration",
                            "query": query,
                            "caption": caption,
                            "aspect_ratio": "4:3",
                        }
                    ]
                },
            }
        ],
    }
    (session_dir / "plan.json").write_text(__import__("json").dumps(plan, ensure_ascii=False), encoding="utf-8")
    target = _build_reuse_target_asset(
        asset_kind="page_image",
        prompt=query,
        prompt_route=None,
        background_route=None,
        theme="Where is the fog",
        grade="五年级",
        subject="语文",
        page_title="Fog",
        page_type="content",
        role="illustration",
        aspect_ratio="4:3",
        caption=caption,
    )
    enriched = {
        **target,
        "context_summary": "cached context summary",
        "teaching_intent": "cached teaching intent",
        "general": True,
        "strict_reuse_group": "C05_scene_decor_container",
        "strict_reuse_confidence": 0.91,
        "strict_reuse_reason": "cached reuse group",
    }
    save_reuse_query_cache(
        session_dir,
        target_keyword_cache={_target_keyword_cache_key(target): enriched},
        query_embedding_cache={},
    )

    db = build_ai_image_asset_db(session_dir)

    asset = db["assets"][0]
    assert asset["caption"] == caption
    assert asset["context_summary"] == "cached context summary"
    assert asset["teaching_intent"] == "cached teaching intent"
    assert asset["general"] is True
    assert asset["strict_reuse_group"] == "C05_scene_decor_container"
    assert asset["strict_reuse_reason"] == "cached reuse group"


def test_asset_library_update_skips_llm_when_match_metadata_seeded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", "1")
    session_dir = tmp_path / "session"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    (session_dir / "slides").mkdir()
    (session_dir / "slides_raw").mkdir()
    image_path = materials_dir / "page_01_illustration_1.png"
    Image.new("RGB", (16, 12), "white").save(image_path)
    query = "foggy city with houses streets pedestrians and a hidden black cat"
    caption = "foggy city street scene"
    plan = {
        "meta": {"topic": "Where is the fog", "grade": "五年级", "subject": "语文"},
        "pages": [
            {
                "page_number": 1,
                "page_type": "content",
                "title": "Fog",
                "material_needs": {
                    "images": [
                        {
                            "source": "ai_generate",
                            "role": "illustration",
                            "query": query,
                            "caption": caption,
                            "aspect_ratio": "4:3",
                        }
                    ]
                },
            }
        ],
    }
    (session_dir / "plan.json").write_text(__import__("json").dumps(plan, ensure_ascii=False), encoding="utf-8")
    target = _build_reuse_target_asset(
        asset_kind="page_image",
        prompt=query,
        prompt_route=None,
        background_route=None,
        theme="Where is the fog",
        grade="五年级",
        subject="语文",
        page_title="Fog",
        page_type="content",
        role="illustration",
        aspect_ratio="4:3",
        caption=caption,
    )
    enriched = {
        **target,
        "context_summary": "cached context summary",
        "teaching_intent": "cached teaching intent",
        "general": True,
        "strict_reuse_group": "C05_scene_decor_container",
        "strict_reuse_confidence": 0.91,
        "strict_reuse_reason": "cached reuse group",
    }
    save_reuse_query_cache(
        session_dir,
        target_keyword_cache={_target_keyword_cache_key(target): enriched},
        query_embedding_cache={},
    )

    class RaisingClient:
        def chat_json(self, **_kwargs):
            raise AssertionError("keyword LLM should not be called for seeded metadata")

    db, _target = update_ai_image_asset_library(
        session_dir,
        tmp_path / "library",
        keyword_client=RaisingClient(),
    )

    assert db["assets"][0]["context_summary"] == "cached context summary"
    assert db["assets"][0]["strict_reuse_group"] == "C05_scene_decor_container"


def test_background_asset_reuses_match_stage_metadata_from_query_cache(tmp_path: Path):
    session_dir = tmp_path / "session"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    Image.new("RGB", (16, 9), "white").save(materials_dir / "background.png")
    plan = {
        "meta": {"topic": "Where is the fog", "grade": "五年级", "subject": "语文"},
        "visual": {
            "primary_color": "#335577",
            "secondary_color": "#ddeeff",
            "accent_color": "#ffcc66",
            "background_color_bias": "cool blue",
        },
        "style_routing": {"template_family": "edu", "style_name": "calm"},
        "pages": [],
    }
    (session_dir / "plan.json").write_text(__import__("json").dumps(plan, ensure_ascii=False), encoding="utf-8")
    target = _build_reuse_target_asset(
        asset_kind="background",
        prompt=build_background_content_prompt(plan["visual"]),
        prompt_route=None,
        background_route=_build_background_route(plan),
        theme="Where is the fog",
        grade="五年级",
        subject="语文",
        page_title="",
        page_type="",
        role="background",
        aspect_ratio="16:9",
    )
    enriched = {
        **target,
        "normalized_prompt": "cached normalized background",
        "color_temperature": "cool",
        "context_summary": "cached background context",
        "teaching_intent": "cached background intent",
        "general": True,
        "strict_reuse_group": "C05_scene_decor_container",
        "strict_reuse_confidence": 0.92,
        "strict_reuse_reason": "cached background group",
    }
    save_reuse_query_cache(
        session_dir,
        target_keyword_cache={_target_keyword_cache_key(target): enriched},
        query_embedding_cache={},
    )

    db = build_ai_image_asset_db(session_dir)

    asset = db["assets"][0]
    assert asset["asset_kind"] == "background"
    assert asset["normalized_prompt"] == "cached normalized background"
    assert asset["color_temperature"] == "cool"
    assert asset["context_summary"] == "cached background context"
    assert asset["strict_reuse_group"] == "C05_scene_decor_container"
