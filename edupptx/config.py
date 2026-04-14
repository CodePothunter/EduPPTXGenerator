"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    # LLM (OpenAI-compatible)
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""  # e.g. https://api.openai.com/v1

    # Image generation (AI)
    image_api_key: str = ""
    image_model: str = ""
    image_base_url: str = ""

    # Image search
    pixabay_api_key: str = ""
    unsplash_access_key: str = ""

    # Web research
    tavily_api_key: str = ""

    # Paths
    cache_dir: Path = field(default_factory=lambda: Path("./backgrounds_cache"))
    library_dir: Path = field(default_factory=lambda: Path("./materials_library"))
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    styles_dir: Path = field(default_factory=lambda: Path(""))

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> Config:
        load_dotenv(env_path or ".env")
        pkg_dir = Path(__file__).parent

        # LLM base URL: try GEN_BASE_URL first, then API_BASE_URL
        llm_base = os.getenv("GEN_BASE_URL", "") or os.getenv("API_BASE_URL", "")
        # Strip /chat/completions if user included it (OpenAI SDK adds it)
        llm_base = llm_base.rstrip("/")
        if llm_base.endswith("/chat/completions"):
            llm_base = llm_base[:-len("/chat/completions")]

        # Image base URL: independent from LLM
        # Seedream uses Volcengine (ark.cn-beijing.volces.com)
        _DEFAULT_IMAGE_BASE = "https://ark.cn-beijing.volces.com/api/v3"
        image_base = os.getenv("VISION_GEN_BASE_URL", "") or _DEFAULT_IMAGE_BASE

        return cls(
            llm_api_key=os.getenv("GEN_APIKEY", ""),
            llm_model=os.getenv("GEN_MODEL", "").split("#")[0].strip(),
            llm_base_url=llm_base,
            image_api_key=os.getenv("VISION_GEN_APIKEY", ""),
            image_model=os.getenv("VISION_GEN_MODEL", "").split("#")[0].strip(),
            image_base_url=image_base,
            pixabay_api_key=os.getenv("PIXABAY_API_KEY", ""),
            unsplash_access_key=os.getenv("UNSPLASH_ACCESS_KEY", ""),
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            cache_dir=Path(os.getenv("CACHE_DIR", "./backgrounds_cache")),
            library_dir=Path(os.getenv("LIBRARY_DIR", "./materials_library")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
            styles_dir=pkg_dir / "design" / "style_templates",
        )
