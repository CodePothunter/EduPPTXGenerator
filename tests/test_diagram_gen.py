import pytest
from PIL import Image
from edupptx.design_system import get_design_tokens
from edupptx.diagram_gen import (
    generate_comparison, generate_cycle, generate_flowchart,
    generate_hierarchy, generate_timeline,
)


@pytest.fixture
def tokens():
    return get_design_tokens("emerald")


class TestFlowchart:
    def test_basic(self, tokens):
        data = {
            "nodes": [{"id": "1", "label": "开始"}, {"id": "2", "label": "结束"}],
            "edges": [{"from": "1", "to": "2"}],
            "direction": "TB",
        }
        img = generate_flowchart(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (1200, 800)

    def test_empty_nodes_returns_placeholder(self, tokens):
        data = {"nodes": [], "edges": [], "direction": "TB"}
        img = generate_flowchart(data, tokens)
        assert isinstance(img, Image.Image)

    def test_custom_size(self, tokens):
        data = {"nodes": [{"id": "1", "label": "A"}], "edges": [], "direction": "LR"}
        img = generate_flowchart(data, tokens, size=(800, 600))
        assert img.size == (800, 600)


class TestTimeline:
    def test_basic(self, tokens):
        data = {"events": [
            {"year": "2020", "label": "Event A"},
            {"year": "2021", "label": "Event B"},
            {"year": "2022", "label": "Event C"},
        ]}
        img = generate_timeline(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (1400, 400)

    def test_empty_events(self, tokens):
        data = {"events": []}
        img = generate_timeline(data, tokens)
        assert isinstance(img, Image.Image)


class TestComparison:
    def test_basic(self, tokens):
        data = {"columns": [
            {"header": "优点", "items": ["快速", "简单"]},
            {"header": "缺点", "items": ["成本高"]},
        ]}
        img = generate_comparison(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (1200, 800)


class TestHierarchy:
    def test_basic(self, tokens):
        data = {"root": {
            "label": "Root",
            "children": [
                {"label": "Child A", "children": []},
                {"label": "Child B", "children": [
                    {"label": "Grandchild", "children": []}
                ]},
            ],
        }}
        img = generate_hierarchy(data, tokens)
        assert isinstance(img, Image.Image)

    def test_single_node(self, tokens):
        data = {"root": {"label": "Alone", "children": []}}
        img = generate_hierarchy(data, tokens)
        assert isinstance(img, Image.Image)


class TestCycle:
    def test_basic(self, tokens):
        data = {"steps": [
            {"label": "Step 1"}, {"label": "Step 2"},
            {"label": "Step 3"}, {"label": "Step 4"},
        ]}
        img = generate_cycle(data, tokens)
        assert isinstance(img, Image.Image)
        assert img.size == (800, 800)

    def test_empty_steps(self, tokens):
        data = {"steps": []}
        img = generate_cycle(data, tokens)
        assert isinstance(img, Image.Image)
