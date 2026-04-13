"""Unsplash free image search provider."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from loguru import logger

from edupptx.models import ImageResult

_ENDPOINT = "https://api.unsplash.com/search/photos"


class UnsplashProvider:
    def __init__(self, access_key: str):
        self._access_key = access_key

    async def search(self, query: str, count: int = 3) -> list[ImageResult]:
        if not self._access_key:
            return []

        headers = {"Authorization": f"Client-ID {self._access_key}"}
        params = {"query": query, "per_page": count}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(_ENDPOINT, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()

            results: list[ImageResult] = []
            for photo in data.get("results", []):
                url = photo.get("urls", {}).get("regular", "")
                if not url:
                    continue

                local_path = await _download(url)
                results.append(ImageResult(
                    url=url,
                    width=photo.get("width", 0),
                    height=photo.get("height", 0),
                    source="unsplash",
                    local_path=local_path,
                ))
            return results

        except Exception as e:
            logger.warning("Unsplash search failed: {}", e)
            return []

    async def generate(self, prompt: str, size: str = "1280x720") -> list[ImageResult]:
        return []


async def _download(url: str) -> Path | None:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        suffix = ".jpg"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(resp.content)
        tmp.close()
        return Path(tmp.name)
    except Exception as e:
        logger.warning("Image download failed: {}", e)
        return None
