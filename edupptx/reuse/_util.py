"""复用层叶子工具：纯函数 + 轻量文件读取，零对复用层其余模块的依赖。

这是绞杀重构的地基层——后续抽出的 store/retrieve/decide 等子模块都依赖这些
helper，把它们集中在无回向依赖的模块里，避免子模块与 ai_image_asset_db 间的
循环 import。所有函数行为与原 ai_image_asset_db.py 中的定义逐字一致。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _join_texts(*texts: Any) -> str:
    return "\n".join(_clean_text(text) for text in texts if _clean_text(text))


def _clean_keyword(value: Any) -> str:
    text = _clean_text(value)
    text = text.strip(" \t\r\n,;:.!?\"'[](){}<>")
    text = text.strip("、，；：。！？“”‘’【】（）")
    return text[:40]


def _dedupe_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        term = _clean_keyword(value)
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _client_model_name(client: Any) -> str:
    return _clean_text(getattr(client, "_model", "")) or _clean_text(getattr(client, "model", ""))


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_existing_db(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "warnings": [f"existing library DB could not be read: {path}"],
        }
    return data if isinstance(data, dict) else {"warnings": [f"existing library DB is not an object: {path}"]}
