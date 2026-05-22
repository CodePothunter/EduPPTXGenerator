"""Detached worker for automatic AI image asset-library ingest."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from edupptx.config import Config
from edupptx.materials.ai_image_asset_db import (
    DEFAULT_KEYWORD_BATCH_SIZE,
    update_ai_image_asset_library,
)


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
    parser = argparse.ArgumentParser(description="Ingest one EduPPTX session into the reusable asset library.")
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--library-dir", required=True, type=Path)
    parser.add_argument("--env-file", default=".env", type=Path)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--job-file", type=Path)
    parser.add_argument("--keyword-batch-size", type=int, default=DEFAULT_KEYWORD_BATCH_SIZE)
    parser.add_argument("--vlm-review", dest="vlm_review", action="store_true", default=True)
    parser.add_argument("--no-vlm-review", dest="vlm_review", action="store_false")
    return parser.parse_args(argv)


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
        session_dir=str(args.session_dir),
        library_dir=str(args.library_dir),
        log_path=str(args.log_file) if args.log_file else "",
    )

    try:
        config = Config.from_env(args.env_file)
        keyword_client, keyword_status = _build_keyword_client(config)
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
