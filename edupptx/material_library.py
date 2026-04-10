"""Persistent material library — searchable asset store for backgrounds, diagrams, illustrations."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger

from edupptx.models import MaterialEntry


class MaterialLibrary:
    """Manages a persistent library of visual materials."""

    def __init__(self, library_dir: Path):
        self.dir = Path(library_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        self._entries: list[MaterialEntry] = self._load_index()

    def _load_index(self) -> list[MaterialEntry]:
        if not self.index_path.exists():
            return []
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        return [MaterialEntry.model_validate(e) for e in raw]

    def _save_index(self) -> None:
        data = [e.model_dump() for e in self._entries]
        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def search(
        self,
        tags: list[str],
        type: str | None = None,
        palette: str | None = None,
    ) -> list[MaterialEntry]:
        """Search by tag overlap with optional type/palette filtering."""
        results: list[tuple[int, MaterialEntry]] = []
        for entry in self._entries:
            if type and entry.type != type:
                continue
            tag_score = len(set(tags) & set(entry.tags))
            if tag_score == 0:
                continue
            palette_bonus = 2 if palette and entry.palette == palette else 0
            results.append((tag_score + palette_bonus, entry))
        results.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in results]

    def add(
        self,
        source_path: Path,
        type: str,
        tags: list[str],
        palette: str,
        source: str,
        description: str,
        resolution: tuple[int, int] = (1920, 1080),
    ) -> MaterialEntry:
        """Copy file into library, register in index, return entry."""
        mat_id = f"mat_{len(self._entries):04d}"
        subdir = self.dir / f"{type}s"
        subdir.mkdir(exist_ok=True)
        dest = subdir / f"{mat_id}_{source_path.name}"
        shutil.copy2(source_path, dest)
        entry = MaterialEntry(
            id=mat_id,
            type=type,
            tags=tags,
            palette=palette,
            source=source,
            description=description,
            resolution=resolution,
            path=str(dest.relative_to(self.dir)),
            created_at=datetime.now().isoformat(),
        )
        self._entries.append(entry)
        self._save_index()
        logger.debug("Added material {} to library: {}", mat_id, description)
        return entry

    def get(self, material_id: str) -> MaterialEntry | None:
        return next((e for e in self._entries if e.id == material_id), None)

    def list_all(self, type: str | None = None) -> list[MaterialEntry]:
        if type:
            return [e for e in self._entries if e.type == type]
        return list(self._entries)

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for e in self._entries:
            counts[e.type] = counts.get(e.type, 0) + 1
        return {"total": len(self._entries), "by_type": counts}
