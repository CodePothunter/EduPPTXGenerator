"""Integration test: run the full agent with mocked LLM, verify output structure."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edupptx.agent import PPTXAgent
from edupptx.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="http://localhost:8080/v1",
        library_dir=tmp_path / "library",
        output_dir=tmp_path / "output",
    )


def _full_plan():
    """A realistic 3-slide plan with material decisions."""
    return {
        "topic": "勾股定理",
        "palette": "emerald",
        "language": "zh",
        "slides": [
            {
                "type": "cover",
                "title": "探索勾股定理",
                "subtitle": "数学之美",
                "cards": [
                    {"icon": "triangle", "title": "定义", "body": "直角三角形三边关系"},
                    {"icon": "calculator", "title": "计算", "body": "a² + b² = c²"},
                    {"icon": "lightbulb", "title": "应用", "body": "测量与工程"},
                ],
                "formula": "a² + b² = c²",
                "footer": None,
                "notes": "今天我们来学习勾股定理",
                "bg_action": {"action": "generate", "style": "diagonal_gradient", "tags": ["math", "geometry"]},
                "content_materials": None,
            },
            {
                "type": "content",
                "title": "定理内容",
                "subtitle": None,
                "cards": [
                    {"icon": "book", "title": "条件", "body": "直角三角形"},
                    {"icon": "check-circle", "title": "结论", "body": "两直角边平方和等于斜边平方"},
                ],
                "formula": None,
                "footer": "勾股定理是几何学的基石",
                "notes": "让我们深入了解定理的内容",
                "bg_action": {"action": "generate", "style": "radial_gradient", "tags": ["math"]},
                "content_materials": None,
            },
            {
                "type": "closing",
                "title": "谢谢",
                "subtitle": "期待下次课",
                "cards": [],
                "formula": None,
                "footer": None,
                "notes": "本节课结束",
                "bg_action": {"action": "generate", "style": "geometric_circles", "tags": ["math"]},
                "content_materials": None,
            },
        ],
    }


@patch("edupptx.agent.LLMClient")
def test_full_agent_run(mock_llm_cls, config):
    """End-to-end: agent creates complete session directory with all artifacts."""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _full_plan()
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(config)
    session_dir = agent.run("勾股定理")

    # Session directory exists with correct structure
    assert session_dir.exists()
    assert (session_dir / "output.pptx").exists()
    assert (session_dir / "plan.json").exists()
    assert (session_dir / "thinking.jsonl").exists()
    assert (session_dir / "materials").is_dir()
    assert (session_dir / "slides").is_dir()

    # Plan JSON is valid and correct
    plan = json.loads((session_dir / "plan.json").read_text())
    assert plan["topic"] == "勾股定理"
    assert plan["palette"] == "emerald"
    assert len(plan["slides"]) == 3

    # Thinking log has structured entries
    lines = (session_dir / "thinking.jsonl").read_text().strip().split("\n")
    assert len(lines) >= 3
    entries = [json.loads(l) for l in lines]
    types = [e["type"] for e in entries]
    assert "planning" in types
    assert "done" in types

    # Per-slide state files exist
    slide_files = sorted((session_dir / "slides").glob("*.json"))
    assert len(slide_files) == 3

    # PPTX is valid (non-empty)
    pptx = session_dir / "output.pptx"
    assert pptx.stat().st_size > 0

    # Library was populated with backgrounds
    assert len(agent.library.list_all()) >= 3


@patch("edupptx.agent.LLMClient")
def test_agent_with_diagram_material(mock_llm_cls, config):
    """Agent handles slides with diagram content materials."""
    plan = _full_plan()
    # Add a diagram to slide 2
    plan["slides"][1]["content_materials"] = [
        {
            "action": "generate_diagram",
            "position": "center",
            "diagram_type": "flowchart",
            "diagram_data": {
                "nodes": [
                    {"id": "1", "label": "假设"},
                    {"id": "2", "label": "推导"},
                    {"id": "3", "label": "结论"},
                ],
                "edges": [
                    {"from": "1", "to": "2"},
                    {"from": "2", "to": "3"},
                ],
                "direction": "TB",
            },
            "tags": ["math", "proof"],
        }
    ]

    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = plan
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(config)
    session_dir = agent.run("勾股定理")

    # Should succeed without error — diagrams are rendered natively by the renderer
    # (no PNG generation needed, diagram_native.py draws pptx shapes from data)
    assert (session_dir / "output.pptx").exists()


@patch("edupptx.agent.LLMClient")
def test_agent_library_persists_across_runs(mock_llm_cls, config):
    """Materials from first run are available in second run's library."""
    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = _full_plan()
    mock_llm_cls.return_value = mock_llm

    # First run
    agent1 = PPTXAgent(config)
    agent1.run("勾股定理")
    first_count = len(agent1.library.list_all())
    assert first_count >= 3

    # Second run — new agent, same library_dir
    agent2 = PPTXAgent(config)
    # Library should have materials from first run
    assert len(agent2.library.list_all()) == first_count
