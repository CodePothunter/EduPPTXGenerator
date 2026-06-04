"""Tests for LLM client — DoubaoResponsesClient and create_llm_client factory."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edupptx.config import Config
from edupptx.llm_client import (
    DoubaoResponsesClient,
    LLMClient,
    VLMClient,
    create_llm_client,
    create_vlm_client,
)


@pytest.fixture
def chat_config():
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="https://ark.cn-beijing.volces.com/api/v3",
        llm_provider="chat",
    )


@pytest.fixture
def deepseek_config():
    return Config(
        llm_api_key="test-key",
        llm_model="deepseek-v4-pro",
        llm_base_url="https://api.deepseek.com",
        llm_provider="chat",
    )


@pytest.fixture
def responses_config():
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="https://ark.cn-beijing.volces.com/api/v3",
        llm_provider="responses",
    )


@pytest.fixture
def vlm_config():
    return Config(
        vlm_api_key="test-vlm-key",
        vlm_model="test-vlm-model",
        vlm_base_url="https://ark.cn-beijing.volces.com/api/v3",
    )


class TestCreateLlmClient:
    def test_chat_provider_returns_base_client(self, chat_config):
        client = create_llm_client(chat_config)
        assert type(client) is LLMClient

    def test_responses_provider_returns_doubao_client(self, responses_config):
        client = create_llm_client(responses_config)
        assert isinstance(client, DoubaoResponsesClient)

    def test_responses_inherits_from_llm_client(self, responses_config):
        client = create_llm_client(responses_config)
        assert isinstance(client, LLMClient)

    def test_web_search_from_config(self, responses_config):
        responses_config.web_search = True
        client = create_llm_client(responses_config)
        assert client._web_search is True

    def test_web_search_override(self, responses_config):
        responses_config.web_search = True
        client = create_llm_client(responses_config, web_search=False)
        assert client._web_search is False

    def test_web_search_default_false(self, responses_config):
        client = create_llm_client(responses_config)
        assert client._web_search is False


class TestLLMClientChat:
    @patch("edupptx.llm_client.OpenAI")
    def test_doubao_chat_omits_thinking_by_default(self, mock_openai_cls, chat_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = LLMClient(chat_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "extra_body" not in call_kwargs

    @patch("edupptx.llm_client.OpenAI")
    def test_doubao_chat_uses_configured_thinking(self, mock_openai_cls, chat_config):
        chat_config.llm_thinking = "enabled"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = LLMClient(chat_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}

    @patch("edupptx.llm_client.OpenAI")
    def test_deepseek_chat_omits_reasoning_by_default(self, mock_openai_cls, deepseek_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = LLMClient(deepseek_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "extra_body" not in call_kwargs

    @patch("edupptx.llm_client.OpenAI")
    def test_deepseek_chat_uses_configured_reasoning(self, mock_openai_cls, deepseek_config):
        deepseek_config.llm_thinking = "enabled"
        deepseek_config.llm_reasoning_effort = "high"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = LLMClient(deepseek_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        }

    @patch("edupptx.llm_client.OpenAI")
    def test_deepseek_chat_omits_disabled_thinking_when_reasoning_is_set(
        self,
        mock_openai_cls,
        deepseek_config,
    ):
        deepseek_config.llm_thinking = "disabled"
        deepseek_config.llm_reasoning_effort = "high"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = LLMClient(deepseek_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"reasoning_effort": "high"}


class TestConvertMessages:
    def test_system_to_instructions(self):
        messages = [
            {"role": "system", "content": "你是教育助手"},
            {"role": "user", "content": "讲解勾股定理"},
        ]
        instructions, input_items = DoubaoResponsesClient._convert_messages(messages)
        assert instructions == "你是教育助手"
        assert len(input_items) == 1
        assert input_items[0]["role"] == "user"
        assert input_items[0]["content"] == "讲解勾股定理"

    def test_multiple_system_messages_merged(self):
        messages = [
            {"role": "system", "content": "规则一"},
            {"role": "system", "content": "规则二"},
            {"role": "user", "content": "问题"},
        ]
        instructions, input_items = DoubaoResponsesClient._convert_messages(messages)
        assert "规则一" in instructions
        assert "规则二" in instructions
        assert len(input_items) == 1

    def test_no_system_message(self):
        messages = [
            {"role": "user", "content": "你好"},
        ]
        instructions, input_items = DoubaoResponsesClient._convert_messages(messages)
        assert instructions == ""
        assert len(input_items) == 1

    def test_multi_turn_preserved(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        instructions, input_items = DoubaoResponsesClient._convert_messages(messages)
        assert instructions == "sys"
        assert len(input_items) == 3
        assert input_items[0]["role"] == "user"
        assert input_items[1]["role"] == "assistant"
        assert input_items[2]["role"] == "user"


class TestDoubaoResponsesClientChat:
    @patch("edupptx.llm_client.OpenAI")
    def test_calls_responses_create(self, mock_openai_cls, responses_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "回答内容"
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        result = client.chat(
            messages=[
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "问题"},
            ],
            temperature=0.5,
            max_tokens=1024,
        )

        assert result == "回答内容"
        call_kwargs = mock_client.responses.create.call_args
        assert call_kwargs.kwargs["model"] == "test-model"
        assert call_kwargs.kwargs["instructions"] == "系统提示"
        assert call_kwargs.kwargs["temperature"] == 0.5
        assert call_kwargs.kwargs["max_output_tokens"] == 1024
        assert call_kwargs.kwargs["store"] is False

    @patch("edupptx.llm_client.OpenAI")
    def test_doubao_omits_thinking_by_default(self, mock_openai_cls, responses_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "ok"
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.responses.create.call_args.kwargs
        assert "extra_body" not in call_kwargs

    @patch("edupptx.llm_client.OpenAI")
    def test_doubao_uses_configured_thinking(self, mock_openai_cls, responses_config):
        responses_config.llm_thinking = "enabled"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "ok"
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.responses.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}

    @patch("edupptx.llm_client.OpenAI")
    def test_web_search_tool_injected(self, mock_openai_cls, responses_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "搜索结果"
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        client._web_search = True
        client.chat(messages=[{"role": "user", "content": "最新新闻"}])

        call_kwargs = mock_client.responses.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["type"] == "web_search"

    @patch("edupptx.llm_client.OpenAI")
    def test_no_web_search_by_default(self, mock_openai_cls, responses_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "ok"
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.responses.create.call_args.kwargs
        assert "tools" not in call_kwargs

    @patch("edupptx.llm_client.OpenAI")
    def test_empty_output_returns_empty_string(self, mock_openai_cls, responses_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = None
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        result = client.chat(messages=[{"role": "user", "content": "test"}])
        assert result == ""

    @patch("edupptx.llm_client.OpenAI")
    def test_chat_json_works_via_responses(self, mock_openai_cls, responses_config):
        """chat_json() inherits from LLMClient and calls self.chat() which is overridden."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = '{"key": "value"}'
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        result = client.chat_json(messages=[{"role": "user", "content": "json please"}])
        assert result == {"key": "value"}


class TestVLMClient:
    def test_create_vlm_client_returns_vlm_client(self, vlm_config):
        client = create_vlm_client(vlm_config)
        assert isinstance(client, VLMClient)

    @patch("edupptx.llm_client.OpenAI")
    def test_chat_vlm_json_calls_chat_completions_with_image_messages(self, mock_openai_cls, vlm_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        messages = [
            {"role": "system", "content": "return json"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "metadata"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            },
        ]

        client = VLMClient(vlm_config)
        result = client.chat_vlm_json(messages=messages, temperature=0.2, max_tokens=123)

        assert result == {"ok": True}
        init_kwargs = mock_openai_cls.call_args.kwargs
        assert init_kwargs["api_key"] == "test-vlm-key"
        assert init_kwargs["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
        assert init_kwargs["timeout"] == 120
        assert init_kwargs["max_retries"] == 1

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "test-vlm-model"
        assert call_kwargs["messages"] == messages
        assert call_kwargs["temperature"] == 0.2
        assert call_kwargs["max_tokens"] == 123
        assert call_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


class TestConfigProvider:
    def test_default_provider_is_chat(self):
        config = Config()
        assert config.llm_provider == "chat"

    def test_default_web_search_is_false(self):
        config = Config()
        assert config.web_search is False

    def test_from_env_loads_vlm_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VLM_MODEL", raising=False)
        monkeypatch.delenv("VLM_APIKEY", raising=False)
        monkeypatch.delenv("VLM_BASE_URL", raising=False)
        monkeypatch.delenv("LIBRARY_DIR", raising=False)
        monkeypatch.delenv("REUSE_LIBRARY_DIRS", raising=False)
        env_path = tmp_path / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "VLM_MODEL=seed-mini # comment",
                    "VLM_APIKEY=vlm-key",
                    "VLM_BASE_URL=https://example.test/api/v3/chat/completions",
                ]
            ),
            encoding="utf-8",
        )

        config = Config.from_env(env_path)

        assert config.vlm_model == "seed-mini"
        assert config.vlm_api_key == "vlm-key"
        assert config.vlm_base_url == "https://example.test/api/v3"
        assert config.reuse_library_dirs == (config.library_dir, Path("./materials_library_ppt"))

    def test_from_env_has_no_asset_library_update_mode(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("", encoding="utf-8")

        config = Config.from_env(env_path)

        assert config.env_file == env_path
        assert config.asset_library_ingest_enabled is True
        assert not hasattr(config, "asset_library_update_mode")

    def test_from_env_defaults_asset_library_ingest_enabled(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text("", encoding="utf-8")

        config = Config.from_env(env_path)

        assert config.asset_library_ingest_enabled is True
        assert config.asset_library_vlm_review is False
        assert config.debug_artifacts is False

    def test_from_env_normalizes_llm_thinking_disable_alias(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GEN_THINKING", raising=False)
        env_path = tmp_path / ".env"
        env_path.write_text("GEN_THINKING=disable\n", encoding="utf-8")

        config = Config.from_env(env_path)

        assert config.llm_thinking == "disabled"

    def test_from_env_loads_exercise_policy_switch(self, tmp_path, monkeypatch):
        monkeypatch.delenv("EDUPPTX_EXERCISE_POLICY", raising=False)
        monkeypatch.delenv("EDUPPTX_EXERCISE_BANK_PATH", raising=False)
        env_path = tmp_path / ".env"
        bank_path = tmp_path / "exercise_bank.json"
        env_path.write_text(
            f"EDUPPTX_EXERCISE_POLICY=1\nEDUPPTX_EXERCISE_BANK_PATH={bank_path}\n",
            encoding="utf-8",
        )

        config = Config.from_env(env_path)

        assert config.exercise_policy_enabled is True
        assert config.exercise_bank_path == bank_path
