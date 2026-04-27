"""Image provider protocol and dispatcher."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from loguru import logger

from edupptx.config import Config
from edupptx.models import IMAGE_RATIO_SIZES, ImageNeed, ImageResult


# Common Chinese→English keyword mappings for image search
_ZH_EN_KEYWORDS = {
    "直角三角形": "right triangle", "三角形": "triangle", "几何": "geometry",
    "光合作用": "photosynthesis", "叶绿体": "chloroplast", "植物": "plant",
    "细胞": "cell", "分子": "molecule", "原子": "atom", "化学": "chemistry",
    "物理": "physics", "数学": "math", "地球": "earth", "太阳": "sun",
    "网络": "network", "计算机": "computer", "服务器": "server",
    "建筑": "architecture building", "工人": "worker", "梯子": "ladder",
    "大棚": "greenhouse", "农田": "farmland", "森林": "forest",
    "实验": "experiment", "显微镜": "microscope", "望远镜": "telescope",
    "电路": "circuit", "磁铁": "magnet", "透镜": "lens",
    "历史": "history", "地图": "map", "文化": "culture",
    "教室": "classroom", "学生": "student", "老师": "teacher",
    "示意图": "diagram", "结构": "structure", "流程": "process",
}


def _simplify_query(query: str) -> str:
    """Convert Chinese image query to English keywords for Pixabay/Unsplash."""
    # Extract matching Chinese keywords and translate
    english_parts = []
    for zh, en in _ZH_EN_KEYWORDS.items():
        if zh in query:
            english_parts.append(en)
    if english_parts:
        return " ".join(english_parts[:3])  # max 3 keywords
    # Fallback: keep original
    return query


@runtime_checkable
class ImageProvider(Protocol):
    async def search(self, query: str, count: int = 3) -> list[ImageResult]: ...
    async def generate(self, prompt: str, size: str = "1280x720") -> list[ImageResult]: ...


async def fetch_images(needs: list[ImageNeed], config: Config) -> list[ImageResult | None]:
    """Fetch all needed images in input order.

    Returns one entry per ``ImageNeed`` so multiple images with the same role do not
    overwrite each other.
    """
    from edupptx.materials.pixabay import PixabayProvider
    from edupptx.materials.unsplash import UnsplashProvider
    from edupptx.materials.seedream import SeedreamProvider

    search_providers: list[ImageProvider] = []
    if config.pixabay_api_key:
        search_providers.append(PixabayProvider(config.pixabay_api_key))
    if config.unsplash_access_key:
        search_providers.append(UnsplashProvider(config.unsplash_access_key))

    gen_provider = SeedreamProvider(config) if config.image_api_key else None

    async def _fetch_one(need: ImageNeed) -> ImageResult | None:
        if need.source == "ai_generate":
            if gen_provider is None:
                logger.warning("No AI image provider configured, skipping: {}", need.query)
                return None
            size = IMAGE_RATIO_SIZES.get(need.aspect_ratio, "2848x1600")
            logger.info("Generating image [{}] ratio={} size={}", need.role, need.aspect_ratio, size)
            imgs = await gen_provider.generate(need.query, size=size)
            return imgs[0] if imgs else None

        # search — try original query first, then simplified English keywords
        query = need.query
        for attempt in range(2):
            for provider in search_providers:
                imgs = await provider.search(query)
                if imgs:
                    return imgs[0]
            if attempt == 0:
                # Simplify: extract key nouns, try English keywords
                query = _simplify_query(need.query)
                if query == need.query:
                    break  # no simplification possible
        logger.warning("No search results for: {}", need.query)
        return None

    tasks = [_fetch_one(need) for need in needs]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[ImageResult | None] = []
    for item in fetched:
        if isinstance(item, Exception):
            logger.warning("Image fetch failed: {}", item)
            results.append(None)
            continue
        results.append(item)

    return results
