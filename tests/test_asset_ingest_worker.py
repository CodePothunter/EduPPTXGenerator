from pathlib import Path

from edupptx.asset_ingest_worker import _parse_args, main
from edupptx.materials.asset_ingest_job_store import AssetIngestJobStore


def test_asset_ingest_worker_vlm_review_defaults_off():
    args = _parse_args(
        [
            "--session-dir",
            "output/session_test",
            "--library-dir",
            "materials_library",
        ]
    )

    assert args.vlm_review is False


def test_asset_ingest_worker_vlm_review_flag_enables_review():
    args = _parse_args(
        [
            "--session-dir",
            "output/session_test",
            "--library-dir",
            "materials_library",
            "--vlm-review",
        ]
    )

    assert args.vlm_review is True


def test_asset_ingest_worker_accepts_job_db_without_session_dir():
    args = _parse_args(["--job-db", "materials_library_ppt/asset_ingest_jobs.sqlite3"])

    assert args.job_db == Path("materials_library_ppt/asset_ingest_jobs.sqlite3")
    assert args.session_dir is None
    assert args.library_dir is None


def test_asset_ingest_worker_consumes_sqlite_job_and_prunes_payload(tmp_path, monkeypatch):
    job_db = tmp_path / "jobs.sqlite3"
    library_dir = tmp_path / "materials_library_ppt"
    store = AssetIngestJobStore(job_db)
    store.enqueue(
        job_id="job_worker",
        session_dir=tmp_path / "output" / "session_a",
        library_dir=library_dir,
        assets=[
            {
                "asset_id": "aiimg_worker",
                "asset_kind": "page_image",
                "image_path": "materials/page_01_illustration_1.png",
                "caption": "worker caption",
            }
        ],
    )
    calls: list[str] = []

    def fake_ingest(job_payload, **_kwargs):
        calls.append(job_payload["job_id"])
        return {"asset_count": 1, "warnings": []}, library_dir / "strict_reuse_indexes"

    monkeypatch.setattr("edupptx.asset_ingest_worker.ingest_ai_image_asset_job", fake_ingest)
    monkeypatch.setattr("edupptx.asset_ingest_worker._build_keyword_client", lambda _config: (None, "disabled"))
    monkeypatch.setattr("edupptx.asset_ingest_worker._build_vlm_client", lambda _config: (None, "disabled"))

    code = main(["--job-db", str(job_db), "--env-file", str(tmp_path / ".env")])

    assert code == 0
    assert calls == ["job_worker"]
    job = store.get_job("job_worker")
    assert job["status"] == "succeeded"
    assert job["payload"] == {}
    assert job["summary"]["asset_count"] == 1
