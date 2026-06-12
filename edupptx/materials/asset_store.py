"""SQLite (sqlite-vec) backend for the AI image reuse library — Phase B2 / R1.

R1 goal: behavior-neutral storage substrate. One ``library.db`` per library_dir
replaces the ``strict_reuse_indexes/*.json`` split files + ``*.npz`` embedding
sidecar. This module is the ONLY code that touches ``library.db``.

R1 design choice (de-risk): each split-file entry is stored verbatim
(``payload_json``) alongside queryable columns + the vector. ``load_*`` returns
dicts isomorphic to the JSON/npz readers, so the retrieval layer is unchanged
and ``export(migrate(json))`` round-trips. Full normalization / FTS5 / KNN
push-down / the M-11 projection-vector fix are deliberately LATER phases
(R2-R4): each changes behavior and must pass its own goldset gate.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

DEFAULT_DB_FILENAME = "library.db"
SCHEMA_VERSION = 1

# Split-file names mirror the on-disk layout this replaces.
_GROUP_FILES = (
    "C00_strict_text_problem_skip",
    "C01_irreplaceable_entity_event_action",
    "C02_generic_subject_object",
    "C03_scene_decor_container",
    "background",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per (split-file, asset). Mirrors strict_reuse_indexes/<group>.json 1:1.
CREATE TABLE IF NOT EXISTS split_assets (
    group_file             TEXT NOT NULL,
    ordinal                INTEGER NOT NULL,
    asset_id               TEXT NOT NULL,
    asset_kind             TEXT,
    route_group            TEXT,
    subject                TEXT,
    grade_band             TEXT,
    general                INTEGER,        -- 1 / 0 / NULL (tri-state)
    aspect_bucket          TEXT,
    image_sha256           TEXT,
    is_background          INTEGER NOT NULL DEFAULT 0,
    is_skip                INTEGER NOT NULL DEFAULT 0,
    is_secondary_projection INTEGER NOT NULL DEFAULT 0,
    payload_json           TEXT NOT NULL,  -- authoritative verbatim asset dict
    PRIMARY KEY (group_file, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_split_filter
    ON split_assets(group_file, asset_kind, route_group, subject, general, aspect_bucket);

-- source_pptx_refs flattened (missing-report join target). Deduped across split files.
CREATE TABLE IF NOT EXISTS asset_source_refs (
    asset_id       TEXT NOT NULL,
    pptx_id        TEXT DEFAULT '',
    period_id      TEXT DEFAULT '',
    file_path      TEXT DEFAULT '',
    file_name      TEXT DEFAULT '',
    absolute_path  TEXT DEFAULT '',
    slide_no       INTEGER,
    shape_idx      INTEGER,
    source_media_path TEXT DEFAULT '',
    source         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_srefs_pptx ON asset_source_refs(pptx_id);
CREATE INDEX IF NOT EXISTS idx_srefs_file ON asset_source_refs(file_name);

CREATE TABLE IF NOT EXISTS asset_topic_refs (
    asset_id  TEXT NOT NULL,
    topic_ref TEXT NOT NULL,
    PRIMARY KEY (asset_id, topic_ref)
);
CREATE INDEX IF NOT EXISTS idx_trefs_topic ON asset_topic_refs(topic_ref);
"""


def default_library_db_path(library_dir: str | Path) -> Path:
    return Path(library_dir).expanduser().resolve() / DEFAULT_DB_FILENAME


def library_db_exists(library_dir: str | Path) -> bool:
    return default_library_db_path(library_dir).exists()


class AssetStoreError(RuntimeError):
    pass


class AssetStore:
    """Sole owner of a library.db. Open per library_root; cache the instance."""

    def __init__(self, library_dir: str | Path):
        self.library_root = Path(library_dir).expanduser().resolve()
        self.db_path = self.library_root / DEFAULT_DB_FILENAME
        self._conn: sqlite3.Connection | None = None
        self._vec_loaded = False

    # ---- connection / extension --------------------------------------------
    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.library_root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        self._conn = conn
        return conn

    def _load_vec(self, conn: sqlite3.Connection) -> bool:
        """Load sqlite-vec. On failure, log loudly (H-1 spirit) and degrade."""
        if self._vec_loaded:
            return True
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            self._vec_loaded = True
            return True
        except Exception as exc:
            logger.warning(
                "sqlite-vec extension load FAILED ({}: {}); vector tables unavailable, "
                "reuse degrades to text-only on this library.",
                type(exc).__name__,
                str(exc)[:200],
            )
            return False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._vec_loaded = False

    def __enter__(self) -> "AssetStore":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- schema -------------------------------------------------------------
    def initialize_schema(self, *, vector_dim: int) -> None:
        conn = self.connect()
        conn.executescript(_SCHEMA)
        if self._load_vec(conn) and vector_dim > 0:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_text USING vec0("
                f"asset_id TEXT PRIMARY KEY, embedding FLOAT[{int(vector_dim)}] distance_metric=cosine)"
            )
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_color_bias USING vec0("
                f"asset_id TEXT PRIMARY KEY, embedding FLOAT[{int(vector_dim)}] distance_metric=cosine)"
            )
        self._set_meta(conn, "schema_version", str(SCHEMA_VERSION))
        conn.commit()

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str = "") -> str:
        conn = self.connect()
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ---- migration: JSON split index + npz -> library.db --------------------
    def migrate_from_split_index(self, *, embedding_index: dict[str, Any] | None) -> dict[str, Any]:
        """Populate library.db from the on-disk split index + (optional) npz.

        ``embedding_index`` is the dict returned by ``_read_ai_image_embedding_index``
        (asset_ids/vectors/background_color_bias_*/meta) or None when absent.
        """
        from edupptx.materials.ai_image_asset_db import (
            STRICT_REUSE_INDEX_DIRNAME,
            _is_background_asset,
            _is_skip_reuse_group,
            _normalize_binary_reuse_group,
            _read_json_if_exists,
        )

        split_dir = self.library_root / STRICT_REUSE_INDEX_DIRNAME
        if not split_dir.exists():
            raise AssetStoreError(f"no split index to migrate at {split_dir}")

        meta = (embedding_index or {}).get("meta") if embedding_index else {}
        vector_dim = int((meta or {}).get("vector_dim") or _infer_vector_dim(embedding_index) or 0)
        self.initialize_schema(vector_dim=vector_dim)
        conn = self.connect()

        report = {"groups": {}, "asset_rows": 0, "source_refs": 0, "topic_refs": 0}
        conn.execute("DELETE FROM split_assets")
        conn.execute("DELETE FROM asset_source_refs")
        conn.execute("DELETE FROM asset_topic_refs")
        # Full replace: clear vec tables too (re-migrate must not collide on PK; vec0
        # has no INSERT OR REPLACE). Tables may not exist yet on a vectorless first run.
        for vec_table in ("vec_text", "vec_color_bias"):
            try:
                conn.execute(f"DELETE FROM {vec_table}")
            except sqlite3.OperationalError:
                pass

        seen_source_assets: set[str] = set()
        passthrough: dict[str, Any] = {}
        for group in _GROUP_FILES:
            fname = "background.json" if group == "background" else f"{group}.json"
            payload = _read_json_if_exists(split_dir / fname)
            if not payload:
                continue
            raw = payload.get("assets")
            if not isinstance(raw, list):
                continue
            for key in ("ppt_extractor", "keyword_builder", "keyword_built_at", "asset_root", "built_at"):
                if key in payload and key not in passthrough:
                    passthrough[key] = payload[key]
            for ordinal, asset in enumerate(raw):
                if not isinstance(asset, dict):
                    continue
                asset_id = str(asset.get("asset_id") or "").strip()
                if not asset_id:
                    continue
                group_norm = _normalize_binary_reuse_group(
                    asset.get("strict_reuse_group") or payload.get("strict_reuse_group") or group,
                    default="C03_scene_decor_container",
                )
                conn.execute(
                    "INSERT OR REPLACE INTO split_assets(group_file, ordinal, asset_id, asset_kind, "
                    "route_group, subject, grade_band, general, aspect_bucket, image_sha256, "
                    "is_background, is_skip, is_secondary_projection, payload_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        group,
                        ordinal,
                        asset_id,
                        _clean(asset.get("asset_kind")),
                        group_norm,
                        _clean(asset.get("subject")),
                        _clean(asset.get("grade_band")),
                        _tri_bool(asset.get("general")),
                        _clean(asset.get("aspect_bucket")) or _clean(asset.get("aspect_ratio")),
                        _clean(asset.get("image_sha256")) or _clean(asset.get("_image_sha256")),
                        1 if _is_background_asset(asset) else 0,
                        1 if _is_skip_reuse_group(asset.get("strict_reuse_group")) else 0,
                        1 if asset.get("secondary_projection") is True else 0,
                        json.dumps(asset, ensure_ascii=False, sort_keys=False),
                    ),
                )
                report["asset_rows"] += 1
                # child tables: dedup by asset_id across split files
                if asset_id not in seen_source_assets:
                    seen_source_assets.add(asset_id)
                    report["source_refs"] += self._insert_source_refs(conn, asset_id, asset.get("source_pptx_refs"))
                    report["topic_refs"] += self._insert_topic_refs(conn, asset_id, asset.get("topic_refs"))
            report["groups"][group] = len(raw)

        # vectors
        if embedding_index and vector_dim > 0 and self._vec_loaded:
            report["vec_text"] = self._migrate_vectors(
                conn, "vec_text",
                embedding_index.get("asset_ids"), embedding_index.get("vectors"),
            )
            report["vec_color_bias"] = self._migrate_vectors(
                conn, "vec_color_bias",
                embedding_index.get("background_color_bias_asset_ids"),
                embedding_index.get("background_color_bias_vectors"),
            )
            if meta:
                self._set_meta(conn, "embedding_meta_json", json.dumps(meta, ensure_ascii=False))
                self._set_meta(conn, "embedding_model", str(meta.get("model") or ""))
                self._set_meta(conn, "vector_dim", str(vector_dim))

        for key, value in passthrough.items():
            self._set_meta(conn, f"passthrough_{key}", json.dumps(value, ensure_ascii=False))
        self._set_meta(conn, "asset_root", str(self.library_root))
        conn.commit()
        return report

    def _insert_source_refs(self, conn: sqlite3.Connection, asset_id: str, refs: Any) -> int:
        if not isinstance(refs, list):
            return 0
        n = 0
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            conn.execute(
                "INSERT INTO asset_source_refs(asset_id, pptx_id, period_id, file_path, file_name, "
                "absolute_path, slide_no, shape_idx, source_media_path, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    asset_id, _clean(ref.get("pptx_id")), _clean(ref.get("period_id")),
                    _clean(ref.get("file_path")), _clean(ref.get("file_name")), _clean(ref.get("absolute_path")),
                    _int_or_none(ref.get("slide_no")), _int_or_none(ref.get("shape_idx")),
                    _clean(ref.get("source_media_path")), _clean(ref.get("source")),
                ),
            )
            n += 1
        return n

    def _insert_topic_refs(self, conn: sqlite3.Connection, asset_id: str, refs: Any) -> int:
        if not isinstance(refs, list):
            return 0
        n = 0
        for topic in refs:
            t = _clean(topic)
            if t:
                conn.execute(
                    "INSERT OR IGNORE INTO asset_topic_refs(asset_id, topic_ref) VALUES (?,?)",
                    (asset_id, t),
                )
                n += 1
        return n

    def _migrate_vectors(self, conn: sqlite3.Connection, table: str, asset_ids: Any, vectors: Any) -> int:
        import sqlite_vec

        if not asset_ids or vectors is None:
            return 0
        rows = vectors.tolist() if hasattr(vectors, "tolist") else list(vectors)
        n = 0
        for asset_id, vec in zip(asset_ids, rows):
            aid = str(asset_id).strip()
            if not aid:
                continue
            conn.execute(
                f"INSERT INTO {table}(asset_id, embedding) VALUES (?, ?)",
                (aid, sqlite_vec.serialize_float32([float(x) for x in vec])),
            )
            n += 1
        return n

    # ---- read: group payloads (verbatim) -----------------------------------
    def iter_group_payloads(self) -> Iterable[tuple[str, dict[str, Any]]]:
        """Yield (group_file, payload) reproducing the on-disk split JSON files."""
        conn = self.connect()
        passthrough = self._passthrough()
        for group in _GROUP_FILES:
            rows = conn.execute(
                "SELECT payload_json FROM split_assets WHERE group_file=? ORDER BY ordinal",
                (group,),
            ).fetchall()
            if not rows:
                continue
            assets = [json.loads(r["payload_json"]) for r in rows]
            group_name = "background" if group == "background" else group
            payload = {
                "schema_version": 14,
                "strict_reuse_group": group_name,
                "asset_count": len(assets),
                "assets": assets,
                "warnings": [],
            }
            payload.update(passthrough)
            yield group, payload

    def _passthrough(self) -> dict[str, Any]:
        conn = self.connect()
        out: dict[str, Any] = {}
        for row in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'passthrough_%'").fetchall():
            try:
                out[row["key"][len("passthrough_"):]] = json.loads(row["value"])
            except Exception:
                continue
        return out

    def load_group_payload(self, group: str) -> dict[str, Any] | None:
        for g, payload in self.iter_group_payloads():
            if g == group:
                return payload
        return None

    # ---- read: embedding index (npz-isomorphic) ----------------------------
    def load_embedding_index(self) -> dict[str, Any] | None:
        import numpy as np

        conn = self.connect()
        if not self._has_vec_table(conn, "vec_text"):
            return None
        asset_ids, vectors = self._dump_vectors(conn, "vec_text")
        bg_ids, bg_vectors = self._dump_vectors(conn, "vec_color_bias")
        meta = {}
        raw_meta = self.get_meta("embedding_meta_json")
        if raw_meta:
            try:
                meta = json.loads(raw_meta)
            except Exception:
                meta = {}
        if not asset_ids:
            return None
        return {
            "asset_ids": asset_ids,
            "vectors": np.asarray(vectors, dtype="float32"),
            "background_color_bias_asset_ids": bg_ids,
            "background_color_bias_vectors": (np.asarray(bg_vectors, dtype="float32") if bg_ids else None),
            "meta": meta,
        }

    def _has_vec_table(self, conn: sqlite3.Connection, name: str) -> bool:
        if not self._load_vec(conn):
            return False
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _dump_vectors(self, conn: sqlite3.Connection, table: str) -> tuple[list[str], list[list[float]]]:
        import sqlite_vec  # noqa: F401

        if not self._has_vec_table(conn, table):
            return [], []
        ids: list[str] = []
        vecs: list[list[float]] = []
        for row in conn.execute(f"SELECT asset_id, embedding FROM {table} ORDER BY asset_id").fetchall():
            ids.append(str(row["asset_id"]))
            vecs.append(list(_deserialize_float32(row["embedding"])))
        return ids, vecs

    # ---- export: library.db -> JSON split index + npz ----------------------
    def export_to_split_index(self, *, split_dirname: str = "strict_reuse_indexes") -> Path:
        split_dir = self.library_root / split_dirname
        split_dir.mkdir(parents=True, exist_ok=True)
        for group, payload in self.iter_group_payloads():
            fname = "background.json" if group == "background" else f"{group}.json"
            path = split_dir / fname
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        return split_dir

    # ---- doctor -------------------------------------------------------------
    def doctor(self) -> dict[str, Any]:
        conn = self.connect()
        report: dict[str, Any] = {}
        report["split_assets"] = conn.execute("SELECT COUNT(*) c FROM split_assets").fetchone()["c"]
        report["distinct_assets"] = conn.execute(
            "SELECT COUNT(DISTINCT asset_id) c FROM split_assets"
        ).fetchone()["c"]
        if self._has_vec_table(conn, "vec_text"):
            report["vec_text"] = conn.execute("SELECT COUNT(*) c FROM vec_text").fetchone()["c"]
            # orphan vectors: vec rows whose asset_id is no longer in split_assets
            orphans = conn.execute(
                "SELECT COUNT(*) c FROM vec_text WHERE asset_id NOT IN "
                "(SELECT asset_id FROM split_assets)"
            ).fetchone()["c"]
            report["vec_text_orphans"] = orphans
        return report


# ---- small helpers ----------------------------------------------------------
def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _tri_bool(value: Any) -> int | None:
    if value is True:
        return 1
    if value is False:
        return 0
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _deserialize_float32(blob: Any) -> list[float]:
    import struct

    data = bytes(blob)
    return list(struct.unpack(f"{len(data)//4}f", data))


def _infer_vector_dim(embedding_index: dict[str, Any] | None) -> int:
    if not embedding_index:
        return 0
    vectors = embedding_index.get("vectors")
    if vectors is not None and hasattr(vectors, "shape") and len(vectors.shape) == 2:
        return int(vectors.shape[1])
    return 0
