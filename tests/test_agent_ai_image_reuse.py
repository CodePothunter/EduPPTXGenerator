from __future__ import annotations

import asyncio

from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.models import PlanningDraft
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


def test_phase2c_asset_library_can_be_disabled(tmp_path, monkeypatch):
    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
            asset_library_update_mode="off",
        )
    )
    session = Session(tmp_path / "output")
    calls: list[str] = []

    monkeypatch.setattr(agent, "_update_asset_library_inline", lambda _session: calls.append("inline"))
    monkeypatch.setattr(agent, "_launch_asset_library_update_worker", lambda _session: calls.append("background"))

    agent._phase2c_asset_library(session)

    assert calls == []


def test_phase2c_asset_library_background_starts_worker(tmp_path, monkeypatch):
    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
            asset_library_update_mode="background",
        )
    )
    session = Session(tmp_path / "output")
    calls: list[str] = []

    monkeypatch.setattr(agent, "_update_asset_library_inline", lambda _session: calls.append("inline"))
    monkeypatch.setattr(agent, "_launch_asset_library_update_worker", lambda _session: calls.append("background"))

    agent._phase2c_asset_library(session)

    assert calls == ["background"]


def test_phase2c_asset_library_force_inline_overrides_background(tmp_path, monkeypatch):
    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
            asset_library_update_mode="background",
        )
    )
    session = Session(tmp_path / "output")
    calls: list[str] = []

    monkeypatch.setattr(agent, "_update_asset_library_inline", lambda _session: calls.append("inline"))
    monkeypatch.setattr(agent, "_launch_asset_library_update_worker", lambda _session: calls.append("background"))

    agent._phase2c_asset_library(session, force_inline=True)

    assert calls == ["inline"]
