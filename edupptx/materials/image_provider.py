"""Image provider protocol and dispatcher."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from loguru import logger

from edupptx.config import Config
from edupptx.models import ImageNeed, ImageResult


@runtime_checkable
class ImageProvider(Protocol):
    async def search(self, query: str, count: int = 3) -> list[ImageResult]: ...
    async def generate(self, prompt: str, size: str = "1280x720") -> list[ImageResult]: ...


async def fetch_images(needs: list[ImageNeed], config: Config) -> dict[str, ImageResult]:
    """Fetch all needed images. Returns {role: ImageResult}."""
    from edupptx.materials.pixabay import PixabayProvider
    from edupptx.materials.unsplash import UnsplashProvider
    from edupptx.materials.seedream import SeedreamProvider

    search_providers: list[ImageProvider] = []
    if config.pixabay_api_key:
        search_providers.append(PixabayProvider(config.pixabay_api_key))
    if config.unsplash_access_key:
        search_providers.append(UnsplashProvider(config.unsplash_access_key))

    gen_provider = SeedreamProvider(config) if config.image_api_key else None

    results: dict[str, ImageResult] = {}

    async def _fetch_one(need: ImageNeed) -> tuple[str, ImageResult | None]:
        if need.source == "ai_generate":
            if gen_provider is None:
                logger.warning("No AI image provider configured, skipping: {}", need.query)
                return need.role, None
            imgs = await gen_provider.generate(need.query)
            return need.role, imgs[0] if imgs else None

        # search
        for provider in search_providers:
            imgs = await provider.search(need.query)
            if imgs:
                return need.role, imgs[0]
        logger.warning("No search results for: {}", need.query)
        return need.role, None

    tasks = [_fetch_one(need) for need in needs]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)

    for item in fetched:
        if isinstance(item, Exception):
            logger.warning("Image fetch failed: {}", item)
            continue
        role, result = item
        if result is not None:
            results[role] = result

    return results
