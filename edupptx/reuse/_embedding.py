"""复用层 embedding 向量 sidecar：Qwen3-Embedding 模型加载/编码、npz 读写、checkpoint 断点续传、缺 caption 审查。读路径据 _backend 分支 json npz / sqlite。函数体与原 ai_image_asset_db.py 逐字一致。"""

from __future__ import annotations

import re
import json
import os
import hashlib
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger as PROGRESS_LOGGER

from edupptx.reuse._util import (
    _clean_text,
    _read_json_if_exists,
)
from edupptx.reuse._constants import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_INDEX_FILENAME,
    DEFAULT_EMBEDDING_META_FILENAME,
    DEFAULT_EMBEDDING_MISSING_CAPTION_REVIEW_FILENAME,
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_INDEX_SCHEMA_VERSION,
    _OUTPUT_PATH_MARKERS,
    _PROJECT_ROOT,
)
from edupptx.reuse._assets import (
    _asset_caption,
    _asset_embedding_text,
    _is_background_asset,
)
from edupptx.reuse._scoring import (
    _background_color_bias,
    _embedding_disabled,
)
from edupptx.reuse._backend import (
    _get_asset_store,
    _use_sqlite_backend,
)

# 嵌入模型进程级懒加载缓存（重）。锁守护懒加载，随唯一消费者 _load_embedding_model 归此。
_EMBEDDING_MODEL_CACHE: dict[str, Any] = {}
_EMBEDDING_MODEL_LOCK = threading.RLock()


def _relative_output_path(value: Any) -> str:
    """Return repo-relative paths for JSON reports/debug payloads."""

    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if "://" in raw:
        return raw

    normalized = raw.replace("\\", "/")
    path = Path(raw).expanduser()
    native_abs = path.is_absolute()
    windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", raw))
    posix_abs = normalized.startswith("/")
    if not (native_abs or windows_abs or posix_abs):
        return normalized

    if native_abs:
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        roots = (_PROJECT_ROOT, Path.cwd().resolve())
        for root in roots:
            try:
                return resolved.relative_to(root).as_posix()
            except ValueError:
                continue

    parts = [part for part in normalized.split("/") if part]
    if _PROJECT_ROOT.name in parts:
        start = parts.index(_PROJECT_ROOT.name) + 1
        if start < len(parts):
            return "/".join(parts[start:])
    for marker in _OUTPUT_PATH_MARKERS:
        if marker in parts:
            return "/".join(parts[parts.index(marker) :])
    return normalized


def _embedding_missing_caption_review_item(asset: dict[str, Any]) -> dict[str, Any]:
    item = {
        "asset_id": _clean_text(asset.get("asset_id")),
        "asset_kind": _clean_text(asset.get("asset_kind")),
        "strict_reuse_group": _clean_text(asset.get("strict_reuse_group")),
        "image_path": _clean_text(asset.get("image_path")),
        "query": _clean_text(asset.get("query")),
        "file_name": _clean_text(asset.get("file_name")),
        "theme": _clean_text(asset.get("theme")),
    }
    source_refs = asset.get("source_pptx_refs")
    if isinstance(source_refs, list):
        item["source_pptx_refs"] = deepcopy(source_refs)
    return {key: value for key, value in item.items() if value not in ("", [], None)}


def _write_embedding_missing_caption_review(
    path: Path,
    *,
    model_name: str,
    assets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not assets:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    payload = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "missing_caption_count": len(assets),
        "assets": [_embedding_missing_caption_review_item(asset) for asset in assets],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, path)
    return payload


def write_ai_image_embedding_index(
    match_index: dict[str, Any],
    library_dir: str | Path,
    *,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    index_filename: str = DEFAULT_EMBEDDING_INDEX_FILENAME,
    meta_filename: str = DEFAULT_EMBEDDING_META_FILENAME,
) -> dict[str, Any]:
    """Write the vector sidecar index used by hybrid image reuse retrieval.

    The build is checkpointed after each encode batch. If the process is
    interrupted, rerunning the command resumes from the checkpoint and only
    atomically replaces the final sidecar after every category is complete.
    """

    root = Path(library_dir).expanduser().resolve()
    model_name = _embedding_model_name(model_name)
    sidecar_model_name = _embedding_sidecar_model_name(model_name)
    if _embedding_disabled():
        return {
            "enabled": False,
            "reason": "disabled_by_environment",
            "model": sidecar_model_name,
        }

    assets = match_index.get("assets")
    if not isinstance(assets, list) or not assets:
        return {
            "enabled": False,
            "reason": "empty_match_index",
            "model": sidecar_model_name,
        }

    rows: list[tuple[str, str]] = []
    background_color_bias_rows: list[tuple[str, str]] = []
    missing_caption_assets: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = _clean_text(asset.get("asset_id"))
        text = _asset_embedding_text(asset)
        if asset_id and text:
            rows.append((asset_id, text))
        elif asset_id and not _is_background_asset(asset) and not _asset_caption(asset):
            missing_caption_assets.append(asset)
        color_bias = _background_color_bias(asset) if _is_background_asset(asset) else ""
        if asset_id and color_bias:
            background_color_bias_rows.append((asset_id, color_bias))

    missing_caption_review_path = root / DEFAULT_EMBEDDING_MISSING_CAPTION_REVIEW_FILENAME
    missing_caption_review = _write_embedding_missing_caption_review(
        missing_caption_review_path,
        model_name=sidecar_model_name,
        assets=missing_caption_assets,
    )
    missing_caption_warnings = []
    if missing_caption_review is not None:
        missing_caption_warnings.append(
            f"embedding_missing_caption:{len(missing_caption_assets)}:{_relative_output_path(missing_caption_review_path)}"
        )
    if not rows:
        return {
            "enabled": False,
            "reason": "empty_embedding_text",
            "model": sidecar_model_name,
            "missing_caption_count": len(missing_caption_assets),
            "missing_caption_review_path": _relative_output_path(missing_caption_review_path)
            if missing_caption_review is not None
            else "",
            "warnings": missing_caption_warnings,
        }

    import numpy as np

    index_path = root / index_filename
    meta_path = root / meta_filename
    index_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_index_path = index_path.with_name(f"{index_path.stem}.checkpoint{index_path.suffix}")
    checkpoint_meta_path = meta_path.with_name(f"{meta_path.stem}.checkpoint.json")
    try:
        build_batch_size = int(
            os.environ.get("EDUPPTX_AI_IMAGE_EMBEDDING_BUILD_BATCH_SIZE")
            or DEFAULT_EMBEDDING_BATCH_SIZE
        )
    except ValueError:
        build_batch_size = DEFAULT_EMBEDDING_BATCH_SIZE
    build_batch_size = max(1, build_batch_size)

    def rows_digest(items: list[tuple[str, str]]) -> str:
        digest = hashlib.sha256()
        for asset_id, text in items:
            digest.update(asset_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(text.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    category_specs = [
        {
            "name": "asset",
            "rows": rows,
            "ids_key": "asset_ids",
            "texts_key": None,
            "vectors_key": "vectors",
            "meta_assets_key": "assets",
        },
        {
            "name": "background_color_bias",
            "rows": background_color_bias_rows,
            "ids_key": "background_color_bias_asset_ids",
            "texts_key": None,
            "vectors_key": "background_color_bias_vectors",
            "meta_assets_key": "background_color_bias_assets",
        },
    ]
    total_counts = {spec["name"]: len(spec["rows"]) for spec in category_specs}
    total_texts = sum(total_counts.values())
    fingerprint_payload = {
        "schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
        "model": sidecar_model_name,
        "index_filename": index_filename,
        "meta_filename": meta_filename,
        "row_hashes": {
            spec["name"]: rows_digest(spec["rows"])
            for spec in category_specs
        },
    }
    build_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    encoded: dict[str, dict[str, Any]] = {
        spec["name"]: {"ids": [], "texts": [], "vectors": None}
        for spec in category_specs
    }
    reused_counts = {spec["name"]: 0 for spec in category_specs}
    encoded_counts = {spec["name"]: 0 for spec in category_specs}

    def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, path)

    def write_npz_atomic(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_name(f"{path.name}.tmp")
        with temp_path.open("wb") as handle:
            np.savez_compressed(handle, **payload)
        os.replace(temp_path, path)

    def checkpoint_payload() -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for spec in category_specs:
            state = encoded[spec["name"]]
            vectors = state.get("vectors")
            if vectors is None:
                continue
            ids = list(state.get("ids") or [])
            if not ids:
                continue
            payload[spec["ids_key"]] = np.asarray(ids, dtype=str)
            texts_key = spec.get("texts_key")
            if texts_key:
                payload[texts_key] = np.asarray(list(state.get("texts") or []), dtype=str)
            payload[spec["vectors_key"]] = np.asarray(vectors, dtype="float32")
        return payload

    def checkpoint_meta() -> dict[str, Any]:
        encoded_counts = {
            spec["name"]: len(encoded[spec["name"]].get("ids") or [])
            for spec in category_specs
        }
        return {
            "checkpoint_schema_version": 1,
            "schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
            "build_fingerprint": build_fingerprint,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model": sidecar_model_name,
            "index_filename": index_filename,
            "meta_filename": meta_filename,
            "batch_size": build_batch_size,
            "total_counts": total_counts,
            "encoded_counts": encoded_counts,
        }

    def save_checkpoint(reason: str) -> None:
        payload = checkpoint_payload()
        if not payload:
            return
        write_npz_atomic(checkpoint_index_path, payload)
        write_json_atomic(checkpoint_meta_path, checkpoint_meta())
        encoded_total = sum(len(encoded[spec["name"]].get("ids") or []) for spec in category_specs)
        PROGRESS_LOGGER.info(
            "AI image embedding checkpoint saved: library={}, encoded={}/{}, reason={}, checkpoint={}",
            root,
            encoded_total,
            total_texts,
            reason,
            checkpoint_index_path,
        )

    def validate_loaded_prefix(spec: dict[str, Any], ids: list[str], texts: list[str]) -> bool:
        rows_for_spec = spec["rows"]
        count = len(ids)
        if count > len(rows_for_spec):
            return False
        if ids != [asset_id for asset_id, _text in rows_for_spec[:count]]:
            return False
        texts_key = spec.get("texts_key")
        if texts_key and texts != [text for _asset_id, text in rows_for_spec[:count]]:
            return False
        return True

    def load_checkpoint() -> None:
        if not checkpoint_index_path.exists() or not checkpoint_meta_path.exists():
            return
        meta = _read_json_if_exists(checkpoint_meta_path)
        if meta.get("build_fingerprint") != build_fingerprint:
            PROGRESS_LOGGER.info(
                "AI image embedding checkpoint ignored: library={}, reason=fingerprint_changed",
                root,
            )
            return
        try:
            data = np.load(checkpoint_index_path, allow_pickle=False)
            try:
                for spec in category_specs:
                    vectors_key = spec["vectors_key"]
                    ids_key = spec["ids_key"]
                    if vectors_key not in data.files or ids_key not in data.files:
                        continue
                    vectors = np.asarray(data[vectors_key], dtype="float32")
                    if len(vectors.shape) == 1:
                        vectors = vectors.reshape(1, -1)
                    ids = [str(item) for item in data[ids_key].tolist()]
                    count = min(len(ids), int(vectors.shape[0]))
                    ids = ids[:count]
                    vectors = vectors[:count]
                    texts: list[str] = []
                    texts_key = spec.get("texts_key")
                    if texts_key:
                        if texts_key not in data.files:
                            raise ValueError(f"checkpoint missing {texts_key}")
                        texts = [str(item) for item in data[texts_key].tolist()][:count]
                    if not validate_loaded_prefix(spec, ids, texts):
                        raise ValueError(f"checkpoint prefix mismatch: {spec['name']}")
                    encoded[spec["name"]] = {
                        "ids": ids,
                        "texts": texts,
                        "vectors": vectors,
                    }
            finally:
                data.close()
        except Exception as exc:
            PROGRESS_LOGGER.warning(
                "AI image embedding checkpoint ignored: library={}, reason={}",
                root,
                str(exc)[:180],
            )
            return

        encoded_total = sum(len(encoded[spec["name"]].get("ids") or []) for spec in category_specs)
        if encoded_total:
            PROGRESS_LOGGER.info(
                "AI image embedding checkpoint loaded: library={}, encoded={}/{}",
                root,
                encoded_total,
                total_texts,
            )

    def append_vectors(existing: Any, new_vectors: Any) -> Any:
        new_vectors = np.asarray(new_vectors, dtype="float32")
        if len(new_vectors.shape) == 1:
            new_vectors = new_vectors.reshape(1, -1)
        if existing is None:
            return new_vectors
        return np.vstack([np.asarray(existing, dtype="float32"), new_vectors])

    def load_reusable_sidecar() -> dict[str, dict[tuple[str, str], Any]]:
        reusable: dict[str, dict[tuple[str, str], Any]] = {
            spec["name"]: {}
            for spec in category_specs
        }
        if not index_path.exists() or not meta_path.exists():
            return reusable
        meta = _read_json_if_exists(meta_path)
        if (
            int(meta.get("schema_version") or 0) != EMBEDDING_INDEX_SCHEMA_VERSION
            or not _embedding_model_sidecar_matches(meta.get("model"), model_name, meta.get("model_identity"))
        ):
            return reusable
        try:
            data = np.load(index_path, allow_pickle=False)
            try:
                for spec in category_specs:
                    ids_key = spec["ids_key"]
                    vectors_key = spec["vectors_key"]
                    meta_assets_key = spec["meta_assets_key"]
                    refs = meta.get(meta_assets_key)
                    if (
                        ids_key not in data.files
                        or vectors_key not in data.files
                        or not isinstance(refs, list)
                    ):
                        continue
                    ids = [str(item) for item in data[ids_key].tolist()]
                    vectors = np.asarray(data[vectors_key], dtype="float32")
                    if len(vectors.shape) == 1:
                        vectors = vectors.reshape(1, -1)
                    count = min(len(ids), int(vectors.shape[0]), len(refs))
                    for index in range(count):
                        ref = refs[index]
                        if not isinstance(ref, dict):
                            continue
                        asset_id = _clean_text(ref.get("asset_id"))
                        text_hash = _clean_text(ref.get("embedding_text_hash"))
                        if not asset_id or not text_hash or asset_id != ids[index]:
                            continue
                        reusable[spec["name"]][(asset_id, text_hash)] = vectors[index:index + 1]
            finally:
                data.close()
        except Exception as exc:
            PROGRESS_LOGGER.warning(
                "AI image embedding sidecar reuse ignored: library={}, reason={}",
                root,
                str(exc)[:180],
            )
        return reusable

    reusable_vectors = load_reusable_sidecar()

    def append_rows(
        spec: dict[str, Any],
        batch_rows: list[tuple[str, str]],
        vectors: Any,
        *,
        reused: bool,
    ) -> None:
        name = spec["name"]
        state = encoded[name]
        vectors = np.asarray(vectors, dtype="float32")
        if len(vectors.shape) == 1:
            vectors = vectors.reshape(1, -1)
        if int(vectors.shape[0]) != len(batch_rows):
            raise ValueError(f"embedding vector count mismatch: {name}")
        state["ids"].extend(asset_id for asset_id, _text in batch_rows)
        if spec.get("texts_key"):
            state["texts"].extend(text for _asset_id, text in batch_rows)
        state["vectors"] = append_vectors(state.get("vectors"), vectors)
        if reused:
            reused_counts[name] += len(batch_rows)
        else:
            encoded_counts[name] += len(batch_rows)

    def reusable_vector_for(spec: dict[str, Any], row: tuple[str, str]) -> Any:
        asset_id, text = row
        key = (asset_id, _embedding_text_hash(text))
        return reusable_vectors.get(spec["name"], {}).get(key)

    def encode_missing(spec: dict[str, Any]) -> None:
        name = spec["name"]
        spec_rows = spec["rows"]
        state = encoded[name]
        done = len(state.get("ids") or [])
        while done < len(spec_rows):
            row = spec_rows[done]
            reusable = reusable_vector_for(spec, row)
            if reusable is not None:
                append_rows(spec, [row], reusable, reused=True)
                done = len(state.get("ids") or [])
                continue

            batch_rows: list[tuple[str, str]] = []
            cursor = done
            while cursor < len(spec_rows) and len(batch_rows) < build_batch_size:
                candidate = spec_rows[cursor]
                if reusable_vector_for(spec, candidate) is not None:
                    break
                batch_rows.append(candidate)
                cursor += 1
            batch_texts = [text for _asset_id, text in batch_rows]
            vectors = _encode_embedding_texts(batch_texts, model_name=model_name, query=False)
            append_rows(spec, batch_rows, vectors, reused=False)
            done = len(state.get("ids") or [])
            save_checkpoint(f"{name}:{done}/{len(spec_rows)}")

    try:
        load_checkpoint()
        for spec in category_specs:
            if spec["rows"]:
                encode_missing(spec)
    except Exception as exc:
        return {
            "enabled": False,
            "reason": "embedding_build_failed",
            "model": sidecar_model_name,
            "warnings": [f"AI image embedding index skipped: {str(exc)[:180]}"],
        }

    final_payload = checkpoint_payload()
    vectors = encoded["asset"].get("vectors")
    if vectors is None:
        return {
            "enabled": False,
            "reason": "empty_embedding_vectors",
            "model": sidecar_model_name,
        }

    meta = {
        "schema_version": EMBEDDING_INDEX_SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "model": sidecar_model_name,
        "index_filename": index_filename,
        "match_asset_count": len(assets),
        "asset_count": len(rows),
        "non_embeddable_asset_count": max(0, len(assets) - len(rows)),
        "background_color_bias_asset_count": len(background_color_bias_rows),
        "missing_caption_count": len(missing_caption_assets),
        "missing_caption_review_path": _relative_output_path(missing_caption_review_path)
        if missing_caption_review is not None
        else "",
        "vector_dim": int(vectors.shape[1]) if len(vectors.shape) == 2 else 0,
        "assets": [
            {"asset_id": asset_id, "embedding_text_hash": _embedding_text_hash(text)}
            for asset_id, text in rows
        ],
        "background_color_bias_assets": [
            {"asset_id": asset_id, "embedding_text_hash": _embedding_text_hash(text)}
            for asset_id, text in background_color_bias_rows
        ],
        "reused_asset_count": reused_counts["asset"],
        "encoded_asset_count": encoded_counts["asset"],
        "reused_background_color_bias_asset_count": reused_counts["background_color_bias"],
        "encoded_background_color_bias_asset_count": encoded_counts["background_color_bias"],
        "warnings": missing_caption_warnings,
    }
    write_npz_atomic(index_path, final_payload)
    write_json_atomic(meta_path, meta)
    try:
        checkpoint_index_path.unlink(missing_ok=True)
        checkpoint_meta_path.unlink(missing_ok=True)
    except Exception:
        pass
    return {
        "enabled": True,
        "model": sidecar_model_name,
        "index_path": _relative_output_path(index_path),
        "meta_path": _relative_output_path(meta_path),
        "match_asset_count": len(assets),
        "asset_count": len(rows),
        "non_embeddable_asset_count": max(0, len(assets) - len(rows)),
        "background_color_bias_asset_count": len(background_color_bias_rows),
        "missing_caption_count": len(missing_caption_assets),
        "missing_caption_review_path": _relative_output_path(missing_caption_review_path)
        if missing_caption_review is not None
        else "",
        "vector_dim": meta["vector_dim"],
        "reused_asset_count": reused_counts["asset"],
        "encoded_asset_count": encoded_counts["asset"],
        "reused_background_color_bias_asset_count": reused_counts["background_color_bias"],
        "encoded_background_color_bias_asset_count": encoded_counts["background_color_bias"],
        "warnings": missing_caption_warnings,
    }


def _embedding_query_text(text: str) -> str:
    text = _clean_text(text)
    if not text:
        return ""
    return f"Instruct: 根据图片需求检索可复用的教学图片素材\nQuery: {text}"


def _embedding_model_name(model_name: str | None = None) -> str:
    configured = _clean_text(os.environ.get("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL"))
    return configured or _clean_text(model_name) or DEFAULT_EMBEDDING_MODEL


def _embedding_sidecar_model_name(model_name: str | None = None) -> str:
    if model_name is None:
        model = _embedding_model_name()
    else:
        model = _clean_text(model_name)
        if not model:
            return ""
    for part in Path(model.replace("\\", "/")).parts:
        if part.startswith("models--"):
            pieces = [piece for piece in part.split("--") if piece]
            if pieces:
                return pieces[-1]
    name = Path(model.replace("\\", "/")).name
    return name or model


def _embedding_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _embedding_model_sidecar_matches(
    stored_model: Any,
    current_model: str,
    stored_identity: Any = None,
) -> bool:
    del stored_identity
    return _embedding_sidecar_model_name(_clean_text(stored_model)) == _embedding_sidecar_model_name(
        current_model
    )


def _load_embedding_model(model_name: str = DEFAULT_EMBEDDING_MODEL) -> Any:
    model_name = _embedding_model_name(model_name)
    with _EMBEDDING_MODEL_LOCK:
        cached = _EMBEDDING_MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached
        from sentence_transformers import SentenceTransformer

        PROGRESS_LOGGER.info("AI image embedding model load start: model={}", model_name)
        model = SentenceTransformer(model_name)
        _EMBEDDING_MODEL_CACHE[model_name] = model
        PROGRESS_LOGGER.info("AI image embedding model load done: model={}", model_name)
        return model


def _encode_embedding_texts(
    texts: list[str],
    *,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    query: bool = False,
) -> Any:
    model_name = _embedding_model_name(model_name)
    cleaned = [_clean_text(text) for text in texts if _clean_text(text)]
    if not cleaned:
        raise ValueError("empty embedding texts")
    if query:
        cleaned = [_embedding_query_text(text) for text in cleaned]
    model = _load_embedding_model(model_name)
    log_encode = len(cleaned) > 1 or not query
    if log_encode:
        PROGRESS_LOGGER.info(
            "AI image embedding encode start: texts={}, query={}, model={}",
            len(cleaned),
            bool(query),
            model_name,
        )
    with _EMBEDDING_MODEL_LOCK:
        vectors = model.encode(
            cleaned,
            batch_size=DEFAULT_EMBEDDING_BATCH_SIZE,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    if log_encode:
        PROGRESS_LOGGER.info(
            "AI image embedding encode done: texts={}, query={}, model={}",
            len(cleaned),
            bool(query),
            model_name,
        )
    import numpy as np

    if len(vectors.shape) == 1:
        vectors = vectors.reshape(1, -1)
    return np.asarray(vectors, dtype="float32")


def _embedding_refs_match(stored_refs: Any, expected_refs: list[dict[str, str]]) -> bool | None:
    if not isinstance(stored_refs, list):
        return None
    stored_pairs: list[tuple[str, str]] = []
    for item in stored_refs:
        if not isinstance(item, dict):
            return False
        asset_id = _clean_text(item.get("asset_id"))
        text_hash = _clean_text(item.get("embedding_text_hash"))
        if not asset_id or not text_hash:
            return False
        stored_pairs.append((asset_id, text_hash))
    expected_pairs = [
        (_clean_text(item.get("asset_id")), _clean_text(item.get("embedding_text_hash")))
        for item in expected_refs
    ]
    return sorted(stored_pairs) == sorted(expected_pairs)


def _ensure_ai_image_embedding_index(match_index: dict[str, Any], library_root: Path) -> dict[str, Any]:
    model_name = _embedding_model_name()
    sidecar_model_name = _embedding_sidecar_model_name(model_name)
    if _embedding_disabled():
        return {"enabled": False, "reason": "disabled_by_environment", "model": sidecar_model_name}
    index_path = library_root / DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = library_root / DEFAULT_EMBEDDING_META_FILENAME
    meta = _read_json_if_exists(meta_path)
    assets = match_index.get("assets")
    match_asset_count = len(assets) if isinstance(assets, list) else 0
    expected_count = 0
    expected_background_color_bias_count = 0
    expected_asset_refs: list[dict[str, str]] = []
    expected_background_color_bias_refs: list[dict[str, str]] = []
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_id = _clean_text(asset.get("asset_id"))
            text = _asset_embedding_text(asset)
            if asset_id and text:
                expected_count += 1
                expected_asset_refs.append(
                    {
                        "asset_id": asset_id,
                        "embedding_text_hash": _embedding_text_hash(text),
                    }
                )
            color_bias = _background_color_bias(asset) if _is_background_asset(asset) else ""
            if asset_id and color_bias:
                expected_background_color_bias_count += 1
                expected_background_color_bias_refs.append(
                    {
                        "asset_id": asset_id,
                        "embedding_text_hash": _embedding_text_hash(color_bias),
                    }
                )
    non_embeddable_asset_count = max(0, match_asset_count - expected_count)
    asset_refs_match = _embedding_refs_match(meta.get("assets"), expected_asset_refs)
    background_color_bias_refs_match = _embedding_refs_match(
        meta.get("background_color_bias_assets"),
        expected_background_color_bias_refs,
    )
    if (
        index_path.exists()
        and meta_path.exists()
        and int(meta.get("schema_version") or 0) == EMBEDDING_INDEX_SCHEMA_VERSION
        and _embedding_model_sidecar_matches(meta.get("model"), model_name, meta.get("model_identity"))
        and int(meta.get("asset_count") or -1) == expected_count
        and int(meta.get("background_color_bias_asset_count") or 0) == expected_background_color_bias_count
        and (asset_refs_match is not False)
        and (background_color_bias_refs_match is not False)
    ):
        return {
            "enabled": True,
            "model": sidecar_model_name,
            "index_path": _relative_output_path(index_path),
            "meta_path": _relative_output_path(meta_path),
            "asset_count": expected_count,
            "match_asset_count": match_asset_count,
            "non_embeddable_asset_count": non_embeddable_asset_count,
            "background_color_bias_asset_count": int(meta.get("background_color_bias_asset_count") or 0),
            "vector_dim": int(meta.get("vector_dim") or 0),
        }
    PROGRESS_LOGGER.info(
        "AI image embedding index build start: library={}, assets={}, embeddable_assets={}, model={}, reason=missing_or_stale_sidecar",
        library_root,
        match_asset_count,
        expected_count,
        model_name,
    )
    report = write_ai_image_embedding_index(match_index, library_root)
    if report.get("enabled"):
        PROGRESS_LOGGER.info(
            "AI image embedding index build done: library={}, assets={}, model={}, vector_dim={}",
            library_root,
            report.get("asset_count", 0),
            report.get("model", model_name),
            report.get("vector_dim", 0),
        )
    else:
        PROGRESS_LOGGER.warning(
            "AI image embedding index build skipped: library={}, reason={}, model={}",
            library_root,
            report.get("reason") or "unknown",
            report.get("model", model_name),
        )
    return report


def _read_npz_embedding_index(library_root: Path) -> dict[str, Any] | None:
    """Direct .npz embedding read with NO backend branch — for write-time db sync."""
    index_path = library_root / DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = library_root / DEFAULT_EMBEDDING_META_FILENAME
    if not index_path.exists() or not meta_path.exists():
        return None
    try:
        import numpy as np

        data = np.load(index_path, allow_pickle=False)
        asset_ids = [str(item) for item in data["asset_ids"].tolist()]
        vectors = np.asarray(data["vectors"], dtype="float32")
        bg_ids: list[str] = []
        bg_vectors = None
        if "background_color_bias_asset_ids" in data.files and "background_color_bias_vectors" in data.files:
            bg_ids = [str(item) for item in data["background_color_bias_asset_ids"].tolist()]
            bg_vectors = np.asarray(data["background_color_bias_vectors"], dtype="float32")
        meta = _read_json_if_exists(meta_path)
    except Exception:
        return None
    return {
        "asset_ids": asset_ids,
        "vectors": vectors,
        "background_color_bias_asset_ids": bg_ids,
        "background_color_bias_vectors": bg_vectors,
        "meta": meta,
    }


def _read_ai_image_embedding_index(library_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    model_name = _embedding_model_name()
    sidecar_model_name = _embedding_sidecar_model_name(model_name)
    if _embedding_disabled():
        return {}, {"enabled": False, "reason": "disabled_by_environment", "model": sidecar_model_name}
    if _use_sqlite_backend(library_root):
        try:
            index = _get_asset_store(library_root).load_embedding_index()
        except Exception as exc:
            return {}, {
                "enabled": False,
                "reason": "embedding_index_read_failed",
                "model": sidecar_model_name,
                "warnings": [f"sqlite embedding index could not be read: {str(exc)[:180]}"],
            }
        if not index or index.get("vectors") is None or not (index.get("asset_ids") or []):
            return {}, {"enabled": False, "reason": "missing_embedding_index", "model": sidecar_model_name}
        meta = index.get("meta") or {}
        vectors = index.get("vectors")
        return index, {
            "enabled": True,
            "model": _embedding_sidecar_model_name(meta.get("model")) or sidecar_model_name,
            "backend": "sqlite",
            "asset_count": len(index.get("asset_ids") or []),
            "background_color_bias_asset_count": len(index.get("background_color_bias_asset_ids") or []),
            "vector_dim": int(vectors.shape[1]) if hasattr(vectors, "shape") and len(vectors.shape) == 2 else 0,
        }
    index_path = library_root / DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = library_root / DEFAULT_EMBEDDING_META_FILENAME
    if not index_path.exists() or not meta_path.exists():
        return {}, {"enabled": False, "reason": "missing_embedding_index", "model": sidecar_model_name}
    try:
        import numpy as np

        data = np.load(index_path, allow_pickle=False)
        asset_ids = [str(item) for item in data["asset_ids"].tolist()]
        vectors = np.asarray(data["vectors"], dtype="float32")
        background_color_bias_asset_ids: list[str] = []
        background_color_bias_vectors = None
        if "background_color_bias_asset_ids" in data.files and "background_color_bias_vectors" in data.files:
            background_color_bias_asset_ids = [
                str(item) for item in data["background_color_bias_asset_ids"].tolist()
            ]
            background_color_bias_vectors = np.asarray(data["background_color_bias_vectors"], dtype="float32")
        meta = _read_json_if_exists(meta_path)
    except Exception as exc:
        return {}, {
            "enabled": False,
            "reason": "embedding_index_read_failed",
            "model": sidecar_model_name,
            "warnings": [f"AI image embedding index could not be read: {str(exc)[:180]}"],
        }
    return {
        "asset_ids": asset_ids,
        "vectors": vectors,
        "background_color_bias_asset_ids": background_color_bias_asset_ids,
        "background_color_bias_vectors": background_color_bias_vectors,
        "meta": meta,
    }, {
        "enabled": True,
        "model": _embedding_sidecar_model_name(meta.get("model")) or sidecar_model_name,
        "index_path": _relative_output_path(index_path),
        "meta_path": _relative_output_path(meta_path),
        "asset_count": len(asset_ids),
        "background_color_bias_asset_count": len(background_color_bias_asset_ids),
        "vector_dim": int(vectors.shape[1]) if len(vectors.shape) == 2 else 0,
    }
