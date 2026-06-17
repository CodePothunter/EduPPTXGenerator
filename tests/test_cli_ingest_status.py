"""`edupptx assets ingest-status` surfaces background-ingest failures, and must
inspect the SAME queue DB the agent writes to (incl. the env override)."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from edupptx.cli import main
from edupptx.materials.asset_ingest_job_store import (
    AssetIngestJobStore,
    default_asset_ingest_job_db_path,
)


def _asset(asset_id="a1"):
    return {"asset_id": asset_id, "asset_kind": "page_image", "image_path": "m/p.png"}


def _payload(result):
    assert result.exit_code == 0, result.output
    for line in reversed(result.output.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON in output: {result.output!r}")


def _failed_store(db_path, lib, tmp_path):
    store = AssetIngestJobStore(db_path)
    store.enqueue(job_id="j_fail", session_dir=tmp_path / "s", library_dir=lib, assets=[_asset()])
    store.claim_next(worker_id="w", lease_seconds=600)
    store.mark_failed("j_fail", "kaboom")
    return store


def test_ingest_status_no_db_reports_all_clear(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    res = CliRunner().invoke(main, ["assets", "ingest-status", str(lib), "--json"],
                             env={"EDUPPTX_ASSET_INGEST_JOB_DB": ""})
    payload = _payload(res)
    assert payload["ok"] is True
    assert payload["failed"] == 0 and payload["stuck_queued"] == 0
    assert "no ingest queue db yet" in payload["note"]


def test_ingest_status_reports_failed_job(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    db_path = default_asset_ingest_job_db_path(lib)
    _failed_store(db_path, lib, tmp_path)
    res = CliRunner().invoke(main, ["assets", "ingest-status", str(lib), "--json"],
                             env={"EDUPPTX_ASSET_INGEST_JOB_DB": ""})
    payload = _payload(res)
    assert payload["failed"] == 1
    assert payload["failed_jobs"][0]["job_id"] == "j_fail"
    assert payload["db_path"] == str(db_path)


def test_ingest_status_honors_db_override(tmp_path):
    # Regression: the CLI must follow the same db resolution as the agent. With
    # the queue db relocated via env/--db, default resolution sees nothing while
    # the override sees the real failed job — a false all-clear otherwise.
    lib = tmp_path / "lib"
    lib.mkdir()
    override = tmp_path / "elsewhere" / "queue.sqlite3"
    _failed_store(override, lib, tmp_path)
    runner = CliRunner()

    default_res = runner.invoke(main, ["assets", "ingest-status", str(lib), "--json"],
                                env={"EDUPPTX_ASSET_INGEST_JOB_DB": ""})
    assert _payload(default_res)["failed"] == 0  # default path is empty

    env_res = runner.invoke(main, ["assets", "ingest-status", str(lib), "--json"],
                            env={"EDUPPTX_ASSET_INGEST_JOB_DB": str(override)})
    assert _payload(env_res)["failed"] == 1  # env override finds it

    db_res = runner.invoke(main, ["assets", "ingest-status", str(lib), "--db", str(override), "--json"],
                           env={"EDUPPTX_ASSET_INGEST_JOB_DB": ""})
    assert _payload(db_res)["failed"] == 1  # explicit --db finds it
