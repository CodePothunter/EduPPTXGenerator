from __future__ import annotations

from types import SimpleNamespace

from edupptx.agent import PPTXAgent
from edupptx.config import Config


class _CapturingStore:
    """Captures enqueue(job_id=...) calls so we can assert collision behaviour."""

    seen: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def enqueue(self, *, job_id, **_kwargs):
        _CapturingStore.seen.append(job_id)


def _enqueue_with_assets(agent, session, assets, monkeypatch):
    monkeypatch.setattr(
        "edupptx.materials.ai_image_asset_db.build_ai_image_asset_db",
        lambda *_a, **_k: {"assets": assets, "warnings": []},
    )
    monkeypatch.setattr(
        "edupptx.materials.asset_ingest_job_store.AssetIngestJobStore",
        _CapturingStore,
    )
    return agent._enqueue_asset_library_update_job(session)


def test_ingest_job_id_varies_with_asset_content(tmp_path, monkeypatch):
    """M-5: 同一 session 重渲染产出不同资产集合时，job_id 必须不同，避免 INSERT OR IGNORE 静默丢弃。"""
    _CapturingStore.seen = []
    agent = PPTXAgent(Config(library_dir=tmp_path / "lib", output_dir=tmp_path / "output"))
    session = SimpleNamespace(dir=tmp_path / "output" / "session_20260611")

    a1 = [{"asset_id": "img_a"}, {"asset_id": "img_b"}]
    a2 = [{"asset_id": "img_a"}, {"asset_id": "img_c"}]  # 重渲染：一张图变了

    job1 = _enqueue_with_assets(agent, session, a1, monkeypatch)
    job2 = _enqueue_with_assets(agent, session, a2, monkeypatch)
    job1_again = _enqueue_with_assets(agent, session, list(reversed(a1)), monkeypatch)

    assert job1 and job2
    assert job1 != job2  # 内容不同 → 不同 job
    assert job1 == job1_again  # 同内容（顺序无关）→ 幂等
    assert all(j.startswith("asset_ingest_session_20260611_") for j in (job1, job2))
    assert _CapturingStore.seen == [job1, job2, job1_again]
