"""A3.3: 库缺失/损坏降级 + 总开关关闭时不触发复用 LLM 调用。

这些路径在 v3 原始实现里零测试覆盖（审查报告 tests-docs 盲区）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import edupptx.materials.image_provider as image_provider
from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.materials.ai_image_asset_db import find_reusable_ai_image_asset
from edupptx.models import ImageResult, PlanningDraft


def test_reuse_missing_library_returns_none_without_raising(tmp_path):
    result = find_reusable_ai_image_asset(
        library_dir=(str(tmp_path / "does_not_exist"),),
        asset_kind="page_image",
        prompt="一只青蛙在荷叶上",
        theme="t",
        grade="二年级",
        subject="语文",
        aspect_ratio="4:3",
        keyword_client=None,
    )
    assert result is None


def test_reuse_corrupt_split_index_returns_none_without_raising(tmp_path):
    split = tmp_path / "strict_reuse_indexes"
    split.mkdir()
    (split / "C02_generic_subject_object.json").write_text("{not valid json", encoding="utf-8")
    result = find_reusable_ai_image_asset(
        library_dir=(str(tmp_path),),
        asset_kind="page_image",
        prompt="一只青蛙",
        theme="t",
        grade="二年级",
        subject="语文",
        aspect_ratio="4:3",
        keyword_client=None,
    )
    assert result is None


def _single_ai_generate_draft() -> PlanningDraft:
    return PlanningDraft.model_validate(
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


def test_reuse_disabled_skips_reuse_and_generates(tmp_path, monkeypatch):
    """总开关关闭：不调用任何复用查找，ai_generate 需求直接走生成。"""
    from edupptx.session import Session

    agent = PPTXAgent(
        Config(
            library_dir=tmp_path / "materials_library",
            output_dir=tmp_path / "output",
            reuse_enabled=False,
        )
    )
    session = Session(tmp_path / "output")

    reuse_calls: list = []
    monkeypatch.setattr(
        agent,
        "_find_reusable_ai_image",
        lambda **kw: reuse_calls.append(kw) or None,
    )

    generated = tmp_path / "gen.png"
    generated.write_bytes(b"generated")

    async def fake_fetch_images(needs, _config):
        return [ImageResult(url="x", source="seedream", local_path=generated) for _ in needs]

    monkeypatch.setattr(image_provider, "fetch_images", fake_fetch_images)

    assets = asyncio.run(agent._phase2_materials(_single_ai_generate_draft(), session))

    # ai_generate 需求经生成路径落地
    dest = session.dir / "materials" / "page_01_illustration_1.png"
    assert assets[1].image_paths["illustration_1"] == dest
    assert dest.read_bytes() == b"generated"
    # 复用查找一次都没被调用
    assert reuse_calls == []
