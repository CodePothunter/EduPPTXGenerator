"""复用层后端选择器：EDUPPTX_REUSE_BACKEND=json|sqlite 的判定 + AssetStore
进程级缓存（library.db sqlite-vec 后端）。

是 store/embedding 等读写层的共同底层（读路径据此分支 json/sqlite）。
_ASSET_STORE_CACHE/_LOCK 随其唯一消费者 _get_asset_store 一并归入本模块。
函数体与原 ai_image_asset_db.py 逐字一致。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from edupptx.reuse._util import _clean_text


def _reuse_backend() -> str:
    return _clean_text(os.environ.get("EDUPPTX_REUSE_BACKEND")).lower() or "json"


_ASSET_STORE_CACHE: dict[str, Any] = {}
_ASSET_STORE_LOCK = threading.Lock()


def _use_sqlite_backend(library_root: Path) -> bool:
    """True when EDUPPTX_REUSE_BACKEND=sqlite and a library.db exists for this root."""
    if _reuse_backend() != "sqlite":
        return False
    from edupptx.materials.asset_store import library_db_exists

    return library_db_exists(library_root)


def _get_asset_store(library_root: Path):
    from edupptx.materials.asset_store import AssetStore

    key = str(Path(library_root).expanduser().resolve())
    with _ASSET_STORE_LOCK:
        store = _ASSET_STORE_CACHE.get(key)
        if store is None:
            store = AssetStore(library_root)
            store.connect()
            _ASSET_STORE_CACHE[key] = store
        return store
