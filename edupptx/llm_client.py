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
            timeout=300,
            max_retries=1,
        )
        self._model = config.llm_model
        self._is_doubao = "volces.com" in (config.llm_base_url or "")
        self._is_deepseek = "deepseek.com" in (config.llm_base_url or "")
        self._thinking = config.llm_thinking
        self._reasoning_effort = config.llm_reasoning_effort

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
        if self._is_doubao:
            # 豆包: 默认关闭深度思考（结构化输出场景），GEN_THINKING 可显式覆盖
            kwargs["extra_body"] = {"thinking": {"type": self._thinking or "disabled"}}
        elif self._is_deepseek:
            extra_body: dict[str, Any] = {}
            if self._reasoning_effort:
                extra_body["reasoning_effort"] = self._reasoning_effort
            if self._thinking:
                if (
                    self._thinking.strip().lower() == "disabled"
                    and self._reasoning_effort
                ):
                    logger.debug(
                        "Omitting DeepSeek thinking=disabled because reasoning_effort is set"
                    )
                else:
                    extra_body["thinking"] = {"type": self._thinking}
            if extra_body:
                kwargs["extra_body"] = extra_body
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


class DoubaoResponsesClient(LLMClient):
    """使用火山方舟 Responses API (/responses) 的 LLM client。

    相比 Chat Completions API:
    - system message 提升为 instructions 参数
    - 支持内置联网搜索 (web_search)
    - 服务端自动管理上下文 (本项目暂未使用)
    """

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 16384,
    ) -> str:
        instructions, input_items = self._convert_messages(messages)

        kwargs: dict = dict(
            model=self._model,
            input=input_items,
            temperature=temperature,
            max_output_tokens=max_tokens,
            store=False,
        )
        if instructions:
            kwargs["instructions"] = instructions

        # 联网搜索
        if getattr(self, "_web_search", False):
            kwargs["tools"] = [{"type": "web_search", "max_keyword": 3}]

        if self._is_doubao:
            # 豆包: 默认关闭深度思考，GEN_THINKING 可显式覆盖
            kwargs["extra_body"] = {"thinking": {"type": self._thinking or "disabled"}}

        resp = self._client.responses.create(**kwargs)
        return resp.output_text or ""

    @staticmethod
    def _convert_messages(
        messages: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, str]]]:
        """将 Chat API messages 格式转换为 Responses API 格式。

        Returns (instructions, input_items):
          - instructions: 合并的 system prompts
          - input_items: user/assistant messages
        """
        system_parts: list[str] = []
        input_items: list[dict[str, str]] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                input_items.append({"role": msg["role"], "content": msg["content"]})
        instructions = "\n\n".join(system_parts)
        return instructions, input_items


class BatchLLMClient:
    """火山方舟 / OpenAI Batch API 适配，用于离线批量任务。"""

    def __init__(self, config: "Config"):
        self._client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url or None,
            timeout=300,
            max_retries=1,
        )
        self._model = config.llm_model

    def submit_batch(
        self,
        prompts: list[dict[str, str]],
        *,
        system_prompt: str = "",
    ) -> str:
        import tempfile as _tempfile

        requests = []
        for i, prompt in enumerate(prompts):
            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if isinstance(prompt, dict) and "content" in prompt:
                messages.append({"role": "user", "content": prompt["content"]})
            else:
                messages.append({"role": "user", "content": str(prompt)})
            requests.append({
                "custom_id": f"req_{i}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": self._model,
                    "messages": messages,
                    "max_tokens": 4096,
                },
            })

        jsonl_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests)
        with _tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(jsonl_content)
            f.flush()
            input_file = self._client.files.create(file=open(f.name, "rb"), purpose="batch")

        batch = self._client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        return batch.id

    def poll_batch(self, batch_id: str, *, interval: int = 5, timeout: int = 3600) -> list[str]:
        import time as _time

        elapsed = 0
        while elapsed < timeout:
            batch = self._client.batches.retrieve(batch_id)
            if batch.status == "completed":
                break
            if batch.status in ("failed", "cancelled", "expired"):
                raise RuntimeError(f"Batch {batch_id} ended with status: {batch.status}")
            _time.sleep(interval)
            elapsed += interval
        else:
            raise TimeoutError(f"Batch {batch_id} did not complete within {timeout}s")

        content = self._client.files.content(batch.output_file_id)
        results_by_id: dict[str, str] = {}
        for line in content.text.strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            custom_id = entry.get("custom_id", "")
            body = entry.get("response", {}).get("body", {})
            choices = body.get("choices", [])
            text = choices[0]["message"]["content"] if choices else ""
            results_by_id[custom_id] = text

        return [results_by_id.get(f"req_{i}", "") for i in range(len(results_by_id))]


def create_llm_client(config: "Config", web_search: bool | None = None) -> LLMClient:
    """根据配置创建对应的 LLM client。

    Args:
        config: 全局配置
        web_search: 覆盖 config.web_search（None 则跟随 config）
    """
    if config.llm_provider == "responses":
        client = DoubaoResponsesClient(config)
        client._web_search = config.web_search if web_search is None else web_search
        return client
    return LLMClient(config)


class VLMClient:
    """OpenAI-compatible vision-language client for image verification."""

    def __init__(self, config: Config):
        self._client = OpenAI(
            api_key=config.vlm_api_key,
            base_url=config.vlm_base_url or None,
            timeout=120,
            max_retries=1,
        )
        self._model = config.vlm_model
        self._is_doubao = "volces.com" in (config.vlm_base_url or "")

    def chat_vlm(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        kwargs: dict[str, Any] = dict(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if self._is_doubao:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def chat_vlm_json(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """Chat with image-capable messages and parse a JSON object response."""
        for attempt in range(1 + max_retries):
            raw = self.chat_vlm(messages, temperature=temperature, max_tokens=max_tokens)
            text = LLMClient._strip_fences(raw)
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                try:
                    import json_repair

                    payload = json_repair.loads(text)
                except Exception:
                    if attempt < max_retries:
                        logger.warning(
                            "VLM JSON parse failed (attempt {}), retrying: {}",
                            attempt + 1,
                            str(exc)[:80],
                        )
                        continue
                    logger.error(
                        "VLM JSON parse failed after {} attempts, raw length: {} chars",
                        attempt + 1,
                        len(raw),
                    )
                    raise
            if not isinstance(payload, dict):
                raise ValueError("VLM response is not a JSON object")
            return payload
        raise RuntimeError("VLM JSON parsing exhausted retries")


def create_vlm_client(config: "Config") -> VLMClient:
    """Create the configured OpenAI-compatible VLM client."""
    return VLMClient(config)


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
              16:9=2848x1600, 9:16=1600x2848
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
