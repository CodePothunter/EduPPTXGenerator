"""OpenAI-compatible LLM client wrapper."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from openai import OpenAI

from edupptx.config import Config


class LLMClient:
    """Thin wrapper around the OpenAI SDK for text generation."""

    def __init__(self, config: Config):
        self._client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url or None,
            timeout=180,
            max_retries=1,
        )
        self._model = config.llm_model
        self._is_doubao = "volces.com" in (config.llm_base_url or "")

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> str:
        kwargs: dict = dict(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Doubao-specific: disable thinking for structured output
        if self._is_doubao:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Strip markdown code fences from LLM response."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """Chat expecting a JSON response. Retries on parse failure."""
        for attempt in range(1 + max_retries):
            raw = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
            text = self._strip_fences(raw)
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                if attempt < max_retries:
                    logger.warning(
                        "JSON parse failed (attempt {}), retrying: {}",
                        attempt + 1, str(e)[:80],
                    )
                    continue
                logger.error("JSON parse failed after {} attempts, raw length: {} chars",
                             attempt + 1, len(raw))
                raise


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
        size: str = "2K",
        n: int = 1,
        watermark: bool = False,
    ) -> list[str]:
        """Generate images. Returns list of URLs or base64 data.

        size: preset ('2K','3K') or exact pixels ('2848x1600').
              Recommended 2K sizes by aspect ratio:
              1:1=2048x2048, 4:3=2304x1728, 3:4=1728x2304,
              16:9=2848x1600, 9:16=1600x2848, 3:2=2496x1664,
              2:3=1664x2496, 21:9=3136x1344
        """
        resp = self._client.images.generate(
            model=self._model,
            prompt=prompt,
            size=size,
            n=n,
            extra_body={
                "watermark": watermark,
                "output_format": "png",
            },
        )
        return [item.url or "" for item in resp.data if item.url]
