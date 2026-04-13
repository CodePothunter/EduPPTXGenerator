"""Seedream AI image generation provider (OpenAI-compatible API)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from loguru import logger
from openai import AsyncOpenAI

from edupptx.config import Config
from edupptx.models import ImageResult


class SeedreamProvider:
    def __init__(self, config: Config):
        self._client = AsyncOpenAI(
            api_key=config.image_api_key,
            base_url=config.image_base_url,
            timeout=120,
            max_retries=1,
        )
        self._model = config.image_model

    async def search(self, query: str, count: int = 3) -> list[ImageResult]:
        return []

    async def generate(self, prompt: str, size: str = "2848x1600") -> list[ImageResult]:
        if not self._model:
            return []

        try:
            resp = await self._client.images.generate(
                model=self._model,
                prompt=prompt,
                size=size,
                n=1,
                extra_body={
                    "watermark": False,
                    "output_format": "png",
                },
            )

            results: list[ImageResult] = []
            for item in resp.data:
                url = item.url or ""
                if not url:
                    continue
                local_path = await _download(url)
                results.append(ImageResult(
                    url=url,
                    source="seedream",
                    local_path=local_path,
                ))
            return results

        except Exception as e:
            logger.warning("Seedream generation failed: {}", e)
            return []


async def _download(url: str) -> Path | None:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(resp.content)
        tmp.close()
        return Path(tmp.name)
    except Exception as e:
        logger.warning("Image download failed: {}", e)
        return None
