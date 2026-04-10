"""OpenAI-compatible LLM client wrapper."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from edupptx.config import Config

log = logging.getLogger(__name__)


class LLMClient:
    """Thin wrapper around the OpenAI SDK for text generation."""

    def __init__(self, config: Config):
        self._client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=180,
            max_retries=1,
        )
        self._model = config.llm_model

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return resp.choices[0].message.content or ""

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Chat expecting a JSON response. Parses and returns dict."""
        raw = self.chat(messages, temperature=temperature)
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)


class ImageClient:
    """Thin wrapper around the OpenAI SDK for image generation."""

    def __init__(self, config: Config):
        self._client = OpenAI(
            api_key=config.image_api_key,
            base_url=config.image_base_url,
            timeout=120,
            max_retries=1,
        )
        self._model = config.image_model

    def generate(
        self,
        prompt: str,
        size: str = "1920x1080",
        n: int = 1,
    ) -> list[str]:
        """Generate images. Returns list of URLs or base64 data."""
        resp = self._client.images.generate(
            model=self._model,
            prompt=prompt,
            size=size,
            n=n,
        )
        return [item.url or "" for item in resp.data if item.url]
