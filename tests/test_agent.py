import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edupptx.agent import PPTXAgent
from edupptx.config import Config


@pytest.fixture
def agent_config(tmp_path):
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="http://localhost:8080/v1",
        library_dir=tmp_path / "library",
        output_dir=tmp_path / "output",
    )


def _mock_plan():
    return {
        "topic": "测试主题",
        "palette": "emerald",
        "language": "zh",
        "slides": [
            {
                "type": "cover",
                "title": "测试封面",
                "subtitle": "副标题",
                "cards": [
                    {"icon": "book", "title": "卡片1", "body": "内容1"},
                    {"icon": "star", "title": "卡片2", "body": "内容2"},
                    {"icon": "target", "title": "卡片3", "body": "内容3"},
                ],
                "formula": None,
                "footer": None,
                "notes": "测试备注",
                "bg_action": {"action": "generate", "style": "diagonal_gradient", "tags": ["test"]},
                "content_materials": None,
            },
            {
                "type": "closing",
                "title": "结束",
                "subtitle": "感谢",
                "cards": [],
                "formula": None,
                "footer": None,
                "notes": "结束备注",
                "bg_action": {"action": "generate", "style": "radial_gradient", "tags": ["test"]},
                "content_materials": None,
            },
        ],
    }


@patch("edupptx.agent.LLMClient")
def test_agent_creates_session(mock_llm_cls, agent_config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_plan()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("测试主题")

    assert result.exists()
    assert (result / "plan.json").exists()
    assert (result / "thinking.jsonl").exists()
    assert (result / "output.pptx").exists()


@patch("edupptx.agent.LLMClient")
def test_agent_saves_plan(mock_llm_cls, agent_config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_plan()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("测试主题")

    plan = json.loads((result / "plan.json").read_text())
    assert plan["topic"] == "测试主题"
    assert len(plan["slides"]) == 2


@patch("edupptx.agent.LLMClient")
def test_agent_writes_thinking_log(mock_llm_cls, agent_config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_plan()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("测试主题")

    lines = (result / "thinking.jsonl").read_text().strip().split("\n")
    assert len(lines) >= 3  # planning, materials, rendering, done
    types = [json.loads(l)["type"] for l in lines]
    assert "planning" in types
    assert "done" in types


@patch("edupptx.agent.LLMClient")
def test_agent_populates_library(mock_llm_cls, agent_config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_plan()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    agent.run("测试主题")

    assert len(agent.library.list_all()) >= 2  # at least 2 backgrounds


@patch("edupptx.agent.LLMClient")
def test_agent_slide_states(mock_llm_cls, agent_config):
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _mock_plan()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("测试主题")

    slide_files = list((result / "slides").glob("*.json"))
    assert len(slide_files) == 2
