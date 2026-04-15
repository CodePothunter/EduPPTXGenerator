"""Pixabay free image search provider."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from loguru import logger

from edupptx.models import ImageResult

_ENDPOINT = "https://pixabay.com/api/"


class PixabayProvider:
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(self, query: str, count: int = 3) -> list[ImageResult]:
        if not self._api_key:
            return []

        params = {
            "key": self._api_key,
            "q": query,
            "image_type": "photo",
            "per_page": count,
            "lang": "zh",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(_ENDPOINT, params=params)
                resp.raise_for_status()
                data = resp.json()

            results: list[ImageResult] = []
            for hit in data.get("hits", []):
                url = hit.get("webformatURL", "")
                if not url:
                    continue

                local_path = await _download(url)
                results.append(ImageResult(
                    url=url,
                    width=hit.get("imageWidth", 0),
                    height=hit.get("imageHeight", 0),
                    source="pixabay",
                    local_path=local_path,
                ))
            return results

        except Exception as e:
            logger.warning("Pixabay search failed: {}", e)
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
