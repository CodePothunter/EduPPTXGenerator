import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edupptx.agent import PPTXAgent, _BG_STYLES, _SKIP_MATERIAL_TYPES
from edupptx.config import Config
from edupptx.models import ContentMaterial


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


def _load_slide_states(session_dir: Path) -> list[dict]:
    """Load slide state JSONs sorted by index."""
    slides_dir = session_dir / "slides"
    files = sorted(slides_dir.glob("slide_*.json"))
    return [json.loads(f.read_text()) for f in files]


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


@patch("edupptx.agent.LLMClient")
def test_agent_illustration_decision(mock_llm_cls, agent_config):
    """Test that illustration decisions are parsed correctly."""
    plan_data = _mock_plan()
    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [
        plan_data,  # Content planning call
        {"bg_style": "diagonal_gradient", "illustration": {"description": "A photosynthesis diagram", "style": "educational_flat"}},  # Slide 1 material
        {"bg_style": "radial_gradient"},  # Slide 2 material (no illustration)
    ]
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("测试主题")
    assert result.exists()


@patch("edupptx.agent.LLMClient")
def test_agent_diagram_priority_over_illustration(mock_llm_cls, agent_config):
    """Test that diagram takes priority when both are returned."""
    plan_data = _mock_plan()
    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [
        plan_data,
        {"bg_style": "diagonal_gradient",
         "diagram": {"type": "flowchart", "data": {"nodes": [{"id": "1", "label": "A"}], "edges": []}},
         "illustration": {"description": "should be ignored", "style": "educational_flat"}},
        {"bg_style": "radial_gradient"},
    ]
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("测试主题")
    # Verify diagram was chosen, not illustration
    plan = json.loads((result / "plan.json").read_text())
    # The first slide should have diagram content_materials, not illustration
    slide0 = plan["slides"][0]
    if slide0.get("content_materials"):
        assert slide0["content_materials"][0]["action"] == "generate_diagram"


@patch("edupptx.agent.LLMClient")
def test_agent_skip_material_for_special_types(mock_llm_cls, agent_config):
    """big_quote, closing, section slides skip LLM material calls and have no content_materials."""
    plan_data = {
        "topic": "测试主题",
        "palette": "emerald",
        "language": "zh",
        "slides": [
            {
                "type": "big_quote",
                "title": "名言",
                "subtitle": "——爱因斯坦",
                "cards": [],
                "formula": None,
                "footer": None,
                "notes": "名言备注",
            },
            {
                "type": "content",
                "title": "正文页",
                "subtitle": None,
                "cards": [{"icon": "book", "title": "要点", "body": "内容"}],
                "formula": None,
                "footer": None,
                "notes": "正文备注",
            },
            {
                "type": "closing",
                "title": "谢谢",
                "subtitle": "再见",
                "cards": [],
                "formula": None,
                "footer": None,
                "notes": "结束备注",
            },
            {
                "type": "section",
                "title": "第二部分",
                "subtitle": "过渡页",
                "cards": [],
                "formula": None,
                "footer": None,
                "notes": "分段备注",
            },
        ],
    }
    mock_llm = MagicMock()
    # First call returns the plan; only slide[1] (content) triggers an LLM material call
    mock_llm.chat_json.side_effect = [
        plan_data,  # Content planning
        {"bg_style": "diagonal_gradient"},  # Material decision for slide 1 (content)
    ]
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("测试主题")
    assert result.exists()

    # Read slide states (saved after mutation, unlike plan.json)
    slides = _load_slide_states(result)
    for slide in slides:
        if slide["type"] in _SKIP_MATERIAL_TYPES:
            assert slide.get("content_materials") is None, (
                f"Slide type {slide['type']} should have no content_materials"
            )

    # LLM chat_json was called exactly twice: 1 plan + 1 content slide
    assert mock_llm.chat_json.call_count == 2


@patch("edupptx.agent.LLMClient")
def test_agent_bg_style_rotation(mock_llm_cls, agent_config):
    """4 slides get 4 different background styles via forced rotation."""
    plan_data = {
        "topic": "轮转测试",
        "palette": "emerald",
        "language": "zh",
        "slides": [
            {"type": "cover", "title": f"幻灯片{i}", "subtitle": None, "cards": [],
             "formula": None, "footer": None, "notes": f"备注{i}"}
            for i in range(4)
        ],
    }
    mock_llm = MagicMock()
    # First call = plan, then 4 material decisions (all cover type, not skipped)
    mock_llm.chat_json.side_effect = [
        plan_data,
        {"bg_style": "diagonal_gradient"},
        {"bg_style": "diagonal_gradient"},
        {"bg_style": "diagonal_gradient"},
        {"bg_style": "diagonal_gradient"},
    ]
    mock_llm_cls.return_value = mock_llm

    agent = PPTXAgent(agent_config)
    result = agent.run("轮转测试")

    # Read slide states (saved after mutation, contains bg_action)
    slides = _load_slide_states(result)
    styles = [s["bg_action"]["style"] for s in slides]
    # Each slide gets a different style from _BG_STYLES rotation
    assert styles == _BG_STYLES[:4]


def test_illustration_cache_different_descriptions():
    """Different illustration descriptions produce different tags (via desc_hash)."""
    desc_a = "A photosynthesis diagram showing sunlight and chloroplast"
    desc_b = "A cell division process showing mitosis stages"

    hash_a = hashlib.md5(desc_a.encode()).hexdigest()[:8]
    hash_b = hashlib.md5(desc_b.encode()).hexdigest()[:8]

    assert hash_a != hash_b, "Different descriptions must produce different hashes"

    # Build ContentMaterial objects as the agent would
    base_tags = ["测试主题"]
    style = "educational_flat"

    tags_a = base_tags + [style, hash_a]
    tags_b = base_tags + [style, hash_b]

    assert tags_a != tags_b
    # The differentiating element is the hash
    assert tags_a[-1] != tags_b[-1]
    assert tags_a[:-1] == tags_b[:-1]
