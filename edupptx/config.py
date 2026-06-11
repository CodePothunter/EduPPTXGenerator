"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MATERIALS_CONCURRENCY = 4


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

    # Vision-language model for asset verification/enrichment
    vlm_api_key: str = ""
    vlm_model: str = ""
    vlm_base_url: str = ""

    # Image search
    pixabay_api_key: str = ""
    unsplash_access_key: str = ""

    # Web research
    tavily_api_key: str = ""

    # Provider selection
    llm_provider: str = "chat"  # "chat" (Chat Completions) | "responses" (Responses API)
    llm_thinking: str = ""      # Provider-specific thinking mode, e.g. enabled/disabled
    llm_reasoning_effort: str = ""  # Provider-specific reasoning effort, e.g. low/medium/high
    web_search: bool = False    # 联网搜索 (仅 responses provider 有效)
    llm_concurrency: int = 4    # LLM 并行请求数 (SVG 生成 + Review)
    materials_concurrency: int = DEFAULT_MATERIALS_CONCURRENCY

    # Paths
    env_file: Path = field(default_factory=lambda: Path(".env"))
    library_dir: Path = field(default_factory=lambda: Path("./materials_library"))
    reuse_library_dirs: tuple[Path, ...] = field(default_factory=tuple)
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    styles_dir: Path = field(default_factory=lambda: Path(""))

    # AI image reuse read path (set EDUPPTX_DISABLE_AI_IMAGE_REUSE=1 to roll back to no-reuse)
    reuse_enabled: bool = True
    # Asset library background ingest
    asset_library_ingest_enabled: bool = True
    asset_library_vlm_review: bool = False
    asset_ingest_job_db: Path | None = None
    debug_artifacts: bool = False

    # Optional exercise-bank planning policy
    exercise_policy_enabled: bool = False
    exercise_bank_path: Path | None = None
    exercise_db_path: Path | None = None
    exercise_image_root: Path | None = None
    exercise_candidate_limit_per_category: int = 4

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> Config:
        env_file = Path(env_path or ".env")
        load_dotenv(env_file)
        pkg_dir = Path(__file__).parent

        # LLM base URL: try GEN_BASE_URL, then API_BASE_URL, then default Volcengine
        _DEFAULT_LLM_BASE = "https://ark.cn-beijing.volces.com/api/v3"
        llm_base = os.getenv("GEN_BASE_URL", "") or os.getenv("API_BASE_URL", "") or _DEFAULT_LLM_BASE
        # Strip /chat/completions if user included it (OpenAI SDK adds it)
        llm_base = llm_base.rstrip("/")
        if llm_base.endswith("/chat/completions"):
            llm_base = llm_base[:-len("/chat/completions")]

        # Image base URL: independent from LLM
        # Seedream uses Volcengine (ark.cn-beijing.volces.com)
        _DEFAULT_IMAGE_BASE = "https://ark.cn-beijing.volces.com/api/v3"
        image_base = os.getenv("VISION_GEN_BASE_URL", "") or _DEFAULT_IMAGE_BASE
        vlm_base = os.getenv("VLM_BASE_URL", "") or _DEFAULT_IMAGE_BASE
        vlm_base = vlm_base.rstrip("/")
        if vlm_base.endswith("/chat/completions"):
            vlm_base = vlm_base[:-len("/chat/completions")]

        library_dir = Path(os.getenv("LIBRARY_DIR", "./materials_library"))
        reuse_library_dirs = _reuse_library_dirs_from_env(library_dir)

        return cls(
            llm_api_key=os.getenv("GEN_APIKEY", ""),
            llm_model=os.getenv("GEN_MODEL", "").split("#")[0].strip(),
            llm_base_url=llm_base,
            llm_provider=os.getenv("LLM_PROVIDER", "chat"),
            llm_thinking=_normalize_llm_thinking(os.getenv("GEN_THINKING", "")),
            llm_reasoning_effort=os.getenv("GEN_REASONING_EFFORT", "").strip(),
            llm_concurrency=int(os.getenv("LLM_CONCURRENCY", "4")),
            materials_concurrency=int(
                os.getenv("EDUPPTX_MATERIALS_CONCURRENCY")
                or os.getenv("MATERIALS_CONCURRENCY")
                or str(DEFAULT_MATERIALS_CONCURRENCY)
            ),
            image_api_key=os.getenv("VISION_GEN_APIKEY", ""),
            image_model=os.getenv("VISION_GEN_MODEL", "").split("#")[0].strip(),
            image_base_url=image_base,
            vlm_api_key=os.getenv("VLM_APIKEY", ""),
            vlm_model=os.getenv("VLM_MODEL", "").split("#")[0].strip(),
            vlm_base_url=vlm_base,
            pixabay_api_key=os.getenv("PIXABAY_API_KEY", ""),
            unsplash_access_key=os.getenv("UNSPLASH_ACCESS_KEY", ""),
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            env_file=env_file,
            library_dir=library_dir,
            reuse_library_dirs=reuse_library_dirs,
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
            styles_dir=pkg_dir / "design" / "style_templates",
            reuse_enabled=not _env_bool("EDUPPTX_DISABLE_AI_IMAGE_REUSE", False),
            asset_library_vlm_review=_env_bool("EDUPPTX_ASSET_LIBRARY_VLM_REVIEW", False),
            asset_ingest_job_db=(
                Path(os.getenv("EDUPPTX_ASSET_INGEST_JOB_DB", ""))
                if os.getenv("EDUPPTX_ASSET_INGEST_JOB_DB", "").strip()
                else None
            ),
            exercise_policy_enabled=_env_bool("EDUPPTX_EXERCISE_POLICY", False),
            exercise_bank_path=(
                Path(os.getenv("EDUPPTX_EXERCISE_BANK_PATH", ""))
                if os.getenv("EDUPPTX_EXERCISE_BANK_PATH", "").strip()
                else None
            ),
            exercise_db_path=(
                Path(os.getenv("EDUPPTX_EXERCISE_DB_PATH", ""))
                if os.getenv("EDUPPTX_EXERCISE_DB_PATH", "").strip()
                else None
            ),
            exercise_image_root=(
                Path(os.getenv("EDUPPTX_EXERCISE_IMAGE_ROOT", ""))
                if os.getenv("EDUPPTX_EXERCISE_IMAGE_ROOT", "").strip()
                else None
            ),
            exercise_candidate_limit_per_category=int(os.getenv("EDUPPTX_EXERCISE_CANDIDATE_LIMIT", "4")),
        )


def _reuse_library_dirs_from_env(primary_library_dir: Path) -> tuple[Path, ...]:
    raw = os.getenv("REUSE_LIBRARY_DIRS", "").strip()
    if raw:
        paths: list[Path] = []
        for part in raw.split(","):
            for item in part.split(os.pathsep):
                text = item.strip()
                if text:
                    paths.append(Path(text))
        return _dedupe_paths(paths)
    return _dedupe_paths([primary_library_dir, Path("./materials_library_ppt")])


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def _normalize_llm_thinking(value: str) -> str:
    text = value.strip()
    aliases = {
        "disable": "disabled",
        "enable": "enabled",
    }
    return aliases.get(text.lower(), text)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default
