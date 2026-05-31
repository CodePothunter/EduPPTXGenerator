from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.materials.asset_ingest_job_store import (
    AssetIngestJobStore,
    default_asset_ingest_job_db_path,
)
from edupptx.materials.ai_image_asset_db import _build_reuse_target_asset, _target_keyword_cache_key
from edupptx.models import ImageResult, PlanningDraft
from edupptx.session import Session


def test_phase2_materials_uses_candidate_image_path_for_reuse_suffix(tmp_path, monkeypatch):
    library_dir = tmp_path / "materials_library"
    library_dir.mkdir()
    source = library_dir / "asset.webp"
    source.write_bytes(b"reused-image")

    agent = PPTXAgent(
        Config(
            library_dir=library_dir,
            output_dir=tmp_path / "output",
        )
    )
    session = Session(tmp_path / "output")
    draft = PlanningDraft.model_validate(
        {
            "meta": {"topic": "lesson", "audience": "grade 2"},
            "pages": [
                {
                    "page_number": 1,
                    "page_type": "content",
                    "title": "Page",
                    "material_needs": {
                        "images": [
                            {
                                "query": "reusable image",
                                "source": "ai_generate",
                                "role": "illustration",
                                "aspect_ratio": "1:1",
                            }
                        ]
                    },
                }
            ],
        }
    )

    def fake_find_reusable_ai_image(**kwargs):
        return {
            "asset": {
                "asset_id": "asset_webp",
                "asset_kind": "page_image",
                "image_path": "ai_images/asset.webp",
            },
            "candidate_image_path": str(source),
            "keyword_score": 0.9,
        }

    monkeypatch.setattr(agent, "_find_reusable_ai_image", fake_find_reusable_ai_image)

    assets = asyncio.run(agent._phase2_materials(draft, session))

    dest = session.dir / "materials" / "page_01_illustration_1.webp"
    assert assets[1].image_paths["illustration_1"] == dest
    assert dest.read_bytes() == b"reused-image"


def test_phase2_materials_collects_reuse_candidates_before_policy_and_generates_unmatched(
    tmp_path,
    monkeypatch,
):
    import edupptx.materials.ai_image_asset_db as db_mod
    import edupptx.materials.image_provider as image_provider

    library_dir = tmp_path / "materials_library"
    library_dir.mkdir()
    source = library_dir / "asset.png"
    source.write_bytes(b"reused-image")
    calls: list[str] = []

    agent = PPTXAgent(
        Config(
            library_dir=library_dir,
            output_dir=tmp_path / "output",
            materials_concurrency=2,
        )
    )
    session = Session(tmp_path / "output")
    draft = PlanningDraft.model_validate(
        {
            "meta": {"topic": "lesson", "audience": "grade 2"},
            "pages": [
                {
                    "page_number": 1,
                    "page_type": "content",
                    "title": "Reuse Page",
                    "material_needs": {
                        "images": [
                            {
                                "query": "reusable image",
                                "source": "ai_generate",
                                "role": "illustration",
                                "aspect_ratio": "1:1",
                            }
                        ]
                    },
                },
                {
                    "page_number": 2,
                    "page_type": "content",
                    "title": "Generate Page",
                    "material_needs": {
                        "images": [
                            {
                                "query": "new generated image",
                                "source": "ai_generate",
                                "role": "illustration",
                                "aspect_ratio": "1:1",
                            }
                        ]
                    },
                },
            ],
        }
    )

    def fake_prewarm(targets, _keyword_client, _cache, **_kwargs):
        calls.append(f"prewarm:{len(targets)}")
        return len(targets)

    def fake_find_reusable_ai_image(**kwargs):
        context = kwargs["debug_context"]
        assert kwargs["collect_candidates_only"] is True
        assert kwargs["keyword_client"] is None
        calls.append(f"collect:{context['page_number']}:{context['slot_key']}")
        return {
            "_reuse_candidate_collection": True,
            "debug_context": context,
            "candidates": [{"asset": {"asset_id": "candidate"}}],
        }

    def fake_finalize(collection, **_kwargs):
        context = collection["debug_context"]
        calls.append(f"finalize:{context['page_number']}:{context['slot_key']}")
        if context["page_number"] != 1:
            return None
        return {
            "asset": {
                "asset_id": "asset_png",
                "asset_kind": "page_image",
                "image_path": "ai_images/asset.png",
            },
            "candidate_image_path": str(source),
            "keyword_score": 0.91,
        }

    async def fake_fetch_images(needs, _config):
        calls.append("fetch:" + ",".join(need.query for need in needs))
        results = []
        for index, need in enumerate(needs):
            generated = tmp_path / f"generated_{index}.png"
            generated.write_bytes(f"generated:{need.query}".encode("utf-8"))
            results.append(ImageResult(url="", source="seedream", local_path=generated))
        return results

    monkeypatch.setattr(db_mod, "_prewarm_reuse_target_keywords", fake_prewarm)
    monkeypatch.setattr(db_mod, "_finalize_reuse_candidate_collection", fake_finalize)
    monkeypatch.setattr(agent, "_find_reusable_ai_image", fake_find_reusable_ai_image)
    monkeypatch.setattr(image_provider, "fetch_images", fake_fetch_images)

    assets = asyncio.run(agent._phase2_materials(draft, session))

    first_finalize = next(index for index, call in enumerate(calls) if call.startswith("finalize:"))
    collect_indices = [index for index, call in enumerate(calls) if call.startswith("collect:")]
    assert calls[0] == "prewarm:2"
    assert max(collect_indices) < first_finalize
    assert any(call == "fetch:new generated image" for call in calls)
    assert assets[1].image_paths["illustration_1"].read_bytes() == b"reused-image"
    assert assets[2].image_paths["illustration_1"].read_bytes() == b"generated:new generated image"


def test_phase2c_asset_library_can_be_disabled(tmp_path, monkeypatch):
    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
            asset_library_ingest_enabled=False,
        )
    )
    session = Session(tmp_path / "output")
    calls: list[str] = []

    monkeypatch.setattr(agent, "_enqueue_asset_library_update_job", lambda _session, _ctx=None: "job_1")
    monkeypatch.setattr(agent, "_launch_asset_library_update_worker", lambda _session: calls.append("background"))

    agent._phase2c_asset_library(session)

    assert calls == []


def test_phase2c_asset_library_background_starts_worker(tmp_path, monkeypatch):
    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
        )
    )
    session = Session(tmp_path / "output")
    calls: list[str] = []

    monkeypatch.setattr(agent, "_enqueue_asset_library_update_job", lambda _session, _ctx=None: "job_1")
    monkeypatch.setattr(agent, "_launch_asset_library_update_worker", lambda _session: calls.append("background"))

    agent._phase2c_asset_library(session)

    assert calls == ["background"]


def test_phase2c_asset_library_defaults_to_background(tmp_path, monkeypatch):
    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
        )
    )
    session = Session(tmp_path / "output")
    calls: list[str] = []

    monkeypatch.setattr(agent, "_enqueue_asset_library_update_job", lambda _session, _ctx=None: "job_1")
    monkeypatch.setattr(agent, "_launch_asset_library_update_worker", lambda _session: calls.append("background"))

    agent._phase2c_asset_library(session)

    assert calls == ["background"]


def test_asset_library_worker_command_only_enables_vlm_review_when_configured(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    class FakeProcess:
        pid = 1234

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr("edupptx.agent.subprocess.Popen", fake_popen)

    default_agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
        )
    )
    default_agent._launch_asset_library_update_worker(Session(tmp_path / "output"))

    review_agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
            asset_library_vlm_review=True,
        )
    )
    review_agent._launch_asset_library_update_worker(Session(tmp_path / "output"))

    assert "--vlm-review" not in commands[0]
    assert "--vlm-review" in commands[1]


def test_phase2c_background_enqueues_sqlite_job_with_in_memory_seed(tmp_path, monkeypatch):
    from PIL import Image

    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library_ppt",
            output_dir=tmp_path / "output",
        )
    )
    session = Session(tmp_path / "output")
    materials_dir = session.dir / "materials"
    image_path = materials_dir / "page_01_illustration_1.png"
    Image.new("RGB", (16, 12), "white").save(image_path)

    query = "eclipse phases diagram"
    caption = "eclipse phase diagram"
    plan = {
        "meta": {"topic": "Light", "grade": "八年级", "subject": "物理"},
        "pages": [
            {
                "page_number": 1,
                "page_type": "content",
                "title": "Eclipse",
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
    session.save_plan(plan)
    target = _build_reuse_target_asset(
        asset_kind="page_image",
        prompt=query,
        prompt_route=None,
        background_route=None,
        theme="Light",
        grade="八年级",
        subject="物理",
        page_title="Eclipse",
        page_type="content",
        role="illustration",
        aspect_ratio="4:3",
        caption=caption,
    )
    context = SimpleNamespace(
        target_keyword_cache={
            _target_keyword_cache_key(target): {
                **target,
                "caption": caption,
                "context_summary": "in-memory match summary",
                "teaching_intent": "in-memory teaching intent",
                "subject": "物理",
                "grade_norm": "八年级",
                "grade_band": "high",
                "general": False,
                "strict_reuse_group": "C05_scene_decor_container",
            }
        },
        query_embedding_cache={},
    )
    launches: list[Path] = []
    monkeypatch.setattr(agent, "_launch_asset_library_update_worker", lambda _session: launches.append(_session.dir))

    agent._phase2c_asset_library(session, reuse_search_context=context)

    assert launches == [session.dir]
    assert not (session.dir / "reuse_query_cache.json").exists()
    store = AssetIngestJobStore(default_asset_ingest_job_db_path(agent.config.library_dir))
    job = store.claim_next(worker_id="test-worker")
    assert job is not None
    asset = job["payload"]["assets"][0]
    assert asset["caption"] == caption
    assert asset["context_summary"] == "in-memory match summary"
    assert asset["strict_reuse_group"] == "C05_scene_decor_container"


def test_reuse_query_cache_only_persists_when_debug_artifacts_enabled(tmp_path):
    context = SimpleNamespace(
        target_keyword_cache={"target:test": {"caption": "debug caption"}},
        query_embedding_cache={},
    )

    default_agent = PPTXAgent(Config(output_dir=tmp_path / "output"))
    default_session = Session(tmp_path / "output")
    default_agent._persist_reuse_query_cache(default_session, context)

    assert not (default_session.dir / "reuse_query_cache.json").exists()

    debug_agent = PPTXAgent(Config(output_dir=tmp_path / "output", debug_artifacts=True))
    debug_session = Session(tmp_path / "output")
    debug_agent._persist_reuse_query_cache(debug_session, context)

    assert (debug_session.dir / "reuse_query_cache.json").exists()
