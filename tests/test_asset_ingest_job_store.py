from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from edupptx.materials.asset_ingest_job_store import AssetIngestJobStore


def _asset(asset_id: str = "aiimg_test") -> dict:
    return {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "image_path": "materials/page_01_illustration_1.png",
        "caption": "solar eclipse diagram",
        "context_summary": "science lesson",
        "teaching_intent": "explain eclipse phases",
        "subject": "物理",
        "grade_norm": "八年级",
        "grade_band": "high",
        "general": False,
        "strict_reuse_group": "content_reuse",
        "_reuse_target_metadata_seeded": True,
    }


def test_enqueue_persists_one_job_with_asset_rows(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    store = AssetIngestJobStore(db_path)

    job_id = store.enqueue(
        job_id="job_1",
        session_dir=tmp_path / "output" / "session_a",
        library_dir=tmp_path / "materials_library_ppt",
        assets=[_asset()],
        vlm_review=False,
        debug_artifacts=False,
    )

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["asset_count"] == 1
    assert job["payload"]["assets"][0]["caption"] == "solar eclipse diagram"

    rows = store.list_job_assets(job_id)
    assert rows == [
        {
            "job_id": "job_1",
            "asset_id": "aiimg_test",
            "asset_kind": "page_image",
            "image_path": "materials/page_01_illustration_1.png",
            "status": "queued",
            "error_message": "",
        }
    ]


def test_claim_next_allows_only_one_running_job_per_store(tmp_path: Path):
    store = AssetIngestJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue(
        job_id="job_1",
        session_dir=tmp_path / "output" / "session_a",
        library_dir=tmp_path / "materials_library_ppt",
        assets=[_asset("asset_1")],
    )
    store.enqueue(
        job_id="job_2",
        session_dir=tmp_path / "output" / "session_b",
        library_dir=tmp_path / "materials_library_ppt",
        assets=[_asset("asset_2")],
    )

    first = store.claim_next(worker_id="worker-a", lease_seconds=300)
    second = store.claim_next(worker_id="worker-b", lease_seconds=300)

    assert first is not None
    assert first["job_id"] == "job_1"
    assert second is None
    assert store.get_job("job_2")["status"] == "queued"


def test_claim_next_recovers_expired_running_job(tmp_path: Path):
    now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    store = AssetIngestJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue(
        job_id="job_1",
        session_dir=tmp_path / "output" / "session_a",
        library_dir=tmp_path / "materials_library_ppt",
        assets=[_asset()],
    )
    claimed = store.claim_next(worker_id="worker-a", lease_seconds=1, now=now)
    assert claimed is not None

    recovered = store.claim_next(
        worker_id="worker-b",
        lease_seconds=60,
        now=now + timedelta(seconds=2),
    )

    assert recovered is not None
    assert recovered["job_id"] == "job_1"
    assert recovered["attempts"] == 2


def test_mark_succeeded_prunes_payload_but_keeps_summary(tmp_path: Path):
    store = AssetIngestJobStore(tmp_path / "jobs.sqlite3")
    store.enqueue(
        job_id="job_1",
        session_dir=tmp_path / "output" / "session_a",
        library_dir=tmp_path / "materials_library_ppt",
        assets=[_asset()],
    )
    assert store.claim_next(worker_id="worker-a") is not None

    store.mark_succeeded(
        "job_1",
        summary={"asset_count": 1, "match_index_path": "materials_library_ppt/strict_reuse_indexes"},
    )

    job = store.get_job("job_1")
    assert job["status"] == "succeeded"
    assert job["payload"] == {}
    assert job["summary"]["asset_count"] == 1
    assert store.list_job_assets("job_1")[0]["status"] == "succeeded"
