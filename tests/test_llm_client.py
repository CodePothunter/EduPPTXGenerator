"""Tests for LLM client — DoubaoResponsesClient and create_llm_client factory."""

from unittest.mock import MagicMock, patch

import pytest

from edupptx.config import Config
from edupptx.llm_client import (
    DoubaoResponsesClient,
    LLMClient,
    create_llm_client,
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
def responses_config():
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="https://ark.cn-beijing.volces.com/api/v3",
        llm_provider="responses",
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
    def test_doubao_disables_thinking(self, mock_openai_cls, responses_config):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "ok"
        mock_client.responses.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        client = DoubaoResponsesClient(responses_config)
        client.chat(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_client.responses.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

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


class TestConfigProvider:
    def test_default_provider_is_chat(self):
        config = Config()
        assert config.llm_provider == "chat"

    def test_default_web_search_is_false(self):
        config = Config()
        assert config.web_search is False
