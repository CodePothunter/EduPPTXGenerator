"""SQLite-backed queue for asynchronous AI image asset ingest jobs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_JOB_DB_FILENAME = "asset_ingest_jobs.sqlite3"


def default_asset_ingest_job_db_path(library_dir: str | Path) -> Path:
    """Return the default queue DB path colocated with a reusable library."""

    return Path(library_dir).expanduser().resolve() / DEFAULT_JOB_DB_FILENAME


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads(raw: Any) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(str(raw))
    except Exception:
        return {}


class AssetIngestJobStore:
    """Small SQLite queue used to hand generated assets to background ingest."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000):
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            timeout=max(1.0, self.busy_timeout_ms / 1000.0),
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_ingest_jobs (
                    job_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    session_dir TEXT NOT NULL,
                    library_dir TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    asset_count INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    worker_id TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    lease_until TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_ingest_job_assets (
                    job_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    asset_kind TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    metadata_seed_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'queued',
                    error_message TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (job_id, asset_id),
                    FOREIGN KEY (job_id) REFERENCES asset_ingest_jobs(job_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_ingest_jobs_status_created "
                "ON asset_ingest_jobs(status, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_ingest_jobs_lease "
                "ON asset_ingest_jobs(status, lease_until)"
            )

    def enqueue(
        self,
        *,
        session_dir: str | Path,
        library_dir: str | Path,
        assets: list[dict[str, Any]],
        job_id: str | None = None,
        vlm_review: bool = False,
        debug_artifacts: bool = False,
        max_attempts: int = 3,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        session_path = Path(session_dir).expanduser().resolve()
        library_path = Path(library_dir).expanduser().resolve()
        clean_assets = [dict(asset) for asset in assets if isinstance(asset, dict)]
        job_id = job_id or f"asset_ingest_{uuid.uuid4().hex}"
        created_at = _iso()
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "session_id": session_path.name,
            "session_dir": str(session_path),
            "library_dir": str(library_path),
            "vlm_review": bool(vlm_review),
            "debug_artifacts": bool(debug_artifacts),
            "assets": clean_assets,
        }
        if extra_payload:
            payload.update(extra_payload)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR IGNORE INTO asset_ingest_jobs (
                    job_id, schema_version, session_id, status, session_dir,
                    library_dir, payload_json, asset_count, max_attempts,
                    created_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    SCHEMA_VERSION,
                    session_path.name,
                    str(session_path),
                    str(library_path),
                    _json_dumps(payload),
                    len(clean_assets),
                    int(max_attempts),
                    created_at,
                ),
            )
            for asset in clean_assets:
                asset_id = str(asset.get("asset_id") or "").strip()
                if not asset_id:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO asset_ingest_job_assets (
                        job_id, asset_id, asset_kind, image_path, metadata_seed_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        asset_id,
                        str(asset.get("asset_kind") or "").strip(),
                        str(asset.get("image_path") or "").strip(),
                        _json_dumps(asset),
                    ),
                )
        return job_id

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 600,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        now_dt = (now or _utc_now()).astimezone(timezone.utc)
        now_iso = _iso(now_dt)
        lease_iso = _iso(now_dt + timedelta(seconds=max(1, int(lease_seconds))))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM asset_ingest_jobs
                WHERE attempts < max_attempts
                  AND (
                    status = 'queued'
                    OR (status = 'running' AND (lease_until = '' OR lease_until <= ?))
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM asset_ingest_jobs active
                    WHERE active.status = 'running'
                      AND active.lease_until > ?
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now_iso, now_iso),
            ).fetchone()
            if row is None:
                return None
            started_at = row["started_at"] or now_iso
            attempts = int(row["attempts"] or 0) + 1
            conn.execute(
                """
                UPDATE asset_ingest_jobs
                SET status = 'running',
                    attempts = ?,
                    worker_id = ?,
                    started_at = ?,
                    lease_until = ?,
                    error_message = ''
                WHERE job_id = ?
                """,
                (attempts, worker_id, started_at, lease_iso, row["job_id"]),
            )
            conn.execute(
                "UPDATE asset_ingest_job_assets SET status = 'running', error_message = '' WHERE job_id = ?",
                (row["job_id"],),
            )
            updated = conn.execute(
                "SELECT * FROM asset_ingest_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
            return self._row_to_job(updated)

    def mark_succeeded(
        self,
        job_id: str,
        *,
        summary: dict[str, Any] | None = None,
        prune_payload: bool = True,
    ) -> None:
        payload_json = "{}" if prune_payload else None
        metadata_json = "{}" if prune_payload else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if payload_json is None:
                conn.execute(
                    """
                    UPDATE asset_ingest_jobs
                    SET status = 'succeeded',
                        finished_at = ?,
                        lease_until = '',
                        worker_id = '',
                        error_message = '',
                        summary_json = ?
                    WHERE job_id = ?
                    """,
                    (_iso(), _json_dumps(summary or {}), job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE asset_ingest_jobs
                    SET status = 'succeeded',
                        finished_at = ?,
                        lease_until = '',
                        worker_id = '',
                        error_message = '',
                        payload_json = ?,
                        summary_json = ?
                    WHERE job_id = ?
                    """,
                    (_iso(), payload_json, _json_dumps(summary or {}), job_id),
                )
            if metadata_json is None:
                conn.execute(
                    "UPDATE asset_ingest_job_assets SET status = 'succeeded', error_message = '' WHERE job_id = ?",
                    (job_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE asset_ingest_job_assets
                    SET status = 'succeeded', error_message = '', metadata_seed_json = ?
                    WHERE job_id = ?
                    """,
                    (metadata_json, job_id),
                )

    def mark_failed(self, job_id: str, error: str) -> None:
        message = str(error or "")[:2000]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE asset_ingest_jobs
                SET status = 'failed',
                    finished_at = ?,
                    lease_until = '',
                    worker_id = '',
                    error_message = ?
                WHERE job_id = ?
                """,
                (_iso(), message, job_id),
            )
            conn.execute(
                "UPDATE asset_ingest_job_assets SET status = 'failed', error_message = ? WHERE job_id = ?",
                (message, job_id),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM asset_ingest_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_job_assets(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, asset_id, asset_kind, image_path, status, error_message
                FROM asset_ingest_job_assets
                WHERE job_id = ?
                ORDER BY asset_id
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_job(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = _json_loads(row["payload_json"])
        summary = _json_loads(row["summary_json"])
        job = dict(row)
        job["payload"] = payload if isinstance(payload, dict) else {}
        job["summary"] = summary if isinstance(summary, dict) else {}
        return job
