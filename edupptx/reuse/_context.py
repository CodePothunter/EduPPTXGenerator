"""单次生成的复用检索缓存容器。

零对复用层其余模块的依赖，被 retrieve/decide（及 agent Phase 2）共用——一个 PPT
会对同一素材库查询数十次，保留此对象避免重复读 JSON/NPZ sidecar 与重复编码 target
向量。类定义与原 ai_image_asset_db.py 中逐字一致。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReuseSearchContext:
    """Per-generation cache for repeated AI image reuse lookups.

    A PPT can query the same material libraries dozens of times. Keeping this
    object for one generation avoids rereading JSON/NPZ sidecars and
    re-encoding identical target embedding texts for each image slot.
    """

    library_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    route_index_cache: dict[tuple[str, str], tuple[dict[str, Any], Path, list[Any], str] | None] = field(
        default_factory=dict
    )
    target_keyword_cache: dict[str, Any] = field(default_factory=dict)
    query_embedding_cache: dict[str, Any] = field(default_factory=dict)
    query_embedding_cache_dir: Path | None = None
    eligible_static_cache: dict[tuple[str, str, str, str], list[dict[str, Any]]] = field(default_factory=dict)
    cache_lock: Any = field(default_factory=threading.RLock, repr=False)
