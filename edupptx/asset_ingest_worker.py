"""Detached worker for automatic AI image asset-library ingest."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from edupptx.config import Config
from edupptx.materials.ai_image_asset_db import (
    DEFAULT_KEYWORD_BATCH_SIZE,
    ingest_ai_image_asset_job,
    update_ai_image_asset_library,
)
from edupptx.materials.asset_ingest_job_store import AssetIngestJobStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_job_status(job_file: Path | None, **updates: Any) -> None:
    if job_file is None:
        return
    payload: dict[str, Any] = {}
    if job_file.exists():
        try:
            raw = json.loads(job_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
        except Exception:
            payload = {}
    payload.update(updates)
    job_file.parent.mkdir(parents=True, exist_ok=True)
    job_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_keyword_client(config: Config):
    if not config.llm_api_key or not config.llm_model:
        return None, "missing_config"
    try:
        from edupptx.llm_client import create_llm_client

        return create_llm_client(config, web_search=False), "enabled"
    except Exception as exc:
        logger.warning("Keyword client unavailable: {}", str(exc)[:160])
        return None, "unavailable"


def _build_vlm_client(config: Config):
    if not config.vlm_api_key or not config.vlm_model:
        return None, "missing_config"
    try:
        from edupptx.llm_client import create_vlm_client

        return create_vlm_client(config), "enabled"
    except Exception as exc:
        logger.warning("VLM client unavailable: {}", str(exc)[:160])
        return None, "unavailable"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest EduPPTX generated images into the reusable asset library.")
    parser.add_argument("--job-db", type=Path, help="SQLite asset ingest job queue to consume")
    parser.add_argument("--session-dir", type=Path)
    parser.add_argument("--library-dir", type=Path)
    parser.add_argument("--env-file", default=".env", type=Path)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--job-file", type=Path)
    parser.add_argument("--keyword-batch-size", type=int, default=DEFAULT_KEYWORD_BATCH_SIZE)
    parser.add_argument("--worker-id", default="", help="Worker id recorded in the SQLite lease")
    parser.add_argument("--lease-seconds", type=int, default=600)
    parser.add_argument("--once", action="store_true", help="Consume at most one SQLite job")
    parser.add_argument("--vlm-review", dest="vlm_review", action="store_true", default=False)
    parser.add_argument("--no-vlm-review", dest="vlm_review", action="store_false")
    args = parser.parse_args(argv)
    if args.job_db is None and (args.session_dir is None or args.library_dir is None):
        parser.error("either --job-db or both --session-dir and --library-dir are required")
    return args


def _run_sqlite_job_queue(
    args: argparse.Namespace,
    config: Config,
    *,
    keyword_client: Any,
    keyword_status: str,
) -> tuple[int, dict[str, Any]]:
    store = AssetIngestJobStore(args.job_db)
    worker_id = args.worker_id or f"asset-ingest-{os.getpid()}"
    processed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    vlm_client = None
    vlm_status = "disabled"

    while True:
        job = store.claim_next(
            worker_id=worker_id,
            lease_seconds=max(1, int(args.lease_seconds or 600)),
        )
        if job is None:
            break
        payload = job.get("payload") or {}
        job_id = str(job.get("job_id") or payload.get("job_id") or "")
        job_vlm_review = bool(args.vlm_review or payload.get("vlm_review"))
        if job_vlm_review and vlm_client is None:
            vlm_client, vlm_status = _build_vlm_client(config)
        try:
            db, target = ingest_ai_image_asset_job(
                payload,
                keyword_client=keyword_client,
                keyword_batch_size=args.keyword_batch_size,
                vlm_client=vlm_client,
                vlm_review=job_vlm_review,
            )
            summary = {
                "match_index_path": str(target),
                "asset_count": db.get("asset_count", 0),
                "warning_count": len(db.get("warnings") or []),
                "keyword_status": keyword_status,
                "vlm_status": vlm_status if job_vlm_review else "disabled",
            }
            store.mark_succeeded(job_id, summary=summary)
            processed.append({"job_id": job_id, **summary})
            logger.info(
                "AI image asset-library ingest job completed: job_id={}, target={}, assets={}",
                job_id,
                target,
                summary["asset_count"],
            )
        except Exception as exc:
            store.mark_failed(job_id, str(exc))
            failed.append({"job_id": job_id, "error": str(exc), "error_type": type(exc).__name__})
            logger.exception("AI image asset-library ingest job failed: {}", job_id)
        if args.once:
            break

    result = {
        "ok": not failed,
        "status": "completed" if not failed else "failed",
        "finished_at": _utc_now(),
        "mode": "sqlite_queue",
        "job_db": str(args.job_db),
        "processed_job_count": len(processed),
        "failed_job_count": len(failed),
        "processed_jobs": processed,
        "failed_jobs": failed,
        "keyword_status": keyword_status,
    }
    return (0 if not failed else 1), result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logger.remove()
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(args.log_file, level="INFO", encoding="utf-8")
    else:
        logger.add(sys.stderr, level="INFO")

    _write_job_status(
        args.job_file,
        status="running",
        started_at=_utc_now(),
        session_dir=str(args.session_dir) if args.session_dir else "",
        library_dir=str(args.library_dir) if args.library_dir else "",
        job_db=str(args.job_db) if args.job_db else "",
        log_path=str(args.log_file) if args.log_file else "",
    )

    try:
        config = Config.from_env(args.env_file)
        keyword_client, keyword_status = _build_keyword_client(config)
        if args.job_db is not None:
            code, result = _run_sqlite_job_queue(
                args,
                config,
                keyword_client=keyword_client,
                keyword_status=keyword_status,
            )
            _write_job_status(args.job_file, **result)
            output = sys.stdout if code == 0 else sys.stderr
            print(json.dumps(result, ensure_ascii=False), file=output)
            return code

        vlm_client = None
        vlm_status = "disabled"
        if args.vlm_review:
            vlm_client, vlm_status = _build_vlm_client(config)

        logger.info(
            "Starting AI image asset-library ingest: session={}, library={}, keywords={}, vlm={}",
            args.session_dir,
            args.library_dir,
            keyword_status,
            vlm_status,
        )
        db, target = update_ai_image_asset_library(
            args.session_dir,
            args.library_dir,
            keyword_client=keyword_client,
            keyword_batch_size=args.keyword_batch_size,
            vlm_client=vlm_client,
            vlm_review=args.vlm_review,
        )
        result = {
            "ok": True,
            "status": "completed",
            "finished_at": _utc_now(),
            "match_index_path": str(target),
            "asset_count": db.get("asset_count", 0),
            "warning_count": len(db.get("warnings") or []),
            "keyword_status": keyword_status,
            "vlm_status": vlm_status,
        }
        _write_job_status(args.job_file, **result)
        logger.info(
            "AI image asset-library ingest completed: target={}, assets={}, warnings={}",
            target,
            result["asset_count"],
            result["warning_count"],
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        result = {
            "ok": False,
            "status": "failed",
            "finished_at": _utc_now(),
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        _write_job_status(args.job_file, **result)
        logger.exception("AI image asset-library ingest failed")
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
