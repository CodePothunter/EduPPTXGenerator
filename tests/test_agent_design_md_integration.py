"""Tests for Layer 3b: agent.py DESIGN.md write path + load_style dispatch."""

from __future__ import annotations

import json

import pytest

from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.planning import visual_planner
from edupptx.style_schema import StyleSchema, load_style


_VALID_DESIGN_MD = """\
---
schema_version: "1.0"
name: 测试蓝
audience: 高中生
colors:
  primary: "#1E40AF"
  accent: "#F59E0B"
  bg: "#EFF6FF"
  card_fill: "#FFFFFF"
  text: "#1E293B"
  text_secondary: "#475569"
  shadow: "#93C5FD"
  icon: "#2563EB"
typography:
  title:      { fontFamily: "Noto Sans SC", fontSize: 38pt, fontWeight: 700 }
  card-title: { fontFamily: "Noto Sans SC", fontSize: 16pt, fontWeight: 600 }
  body:       { fontFamily: "Noto Sans SC", fontSize: 12pt }
spacing:
  margin: comfortable
  card_gap: normal
---

## Overview
clean
## Colors
ok
## Typography
ok
## Layout
ok
## Elevation
ok
## Shapes
ok
## Components
ok
## Do's and Don'ts
ok
"""


@pytest.fixture
def agent():
    return PPTXAgent(Config(
        llm_api_key="test-key", llm_model="test-model",
        llm_base_url="http://localhost:8080/v1",
    ))


def test_load_style_dispatches_md(tmp_path):
    md_path = tmp_path / "style.md"
    md_path.write_text(_VALID_DESIGN_MD, encoding="utf-8")
    schema = load_style(md_path)
    assert isinstance(schema, StyleSchema)
    assert schema.global_tokens.palette["primary"] == "#1E40AF"
    assert schema.global_tokens.palette["accent"] == "#F59E0B"
    assert schema.meta.name == "测试蓝"


def test_load_style_dispatches_json(tmp_path):
    json_path = tmp_path / "style.json"
    payload = {
        "meta": {"schema_version": "0.1", "name": "test-json", "description": ""},
        "global": {"palette": {"primary": "#111111", "accent": "#222222"}},
    }
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    schema = load_style(json_path)
    assert isinstance(schema, StyleSchema)
    assert schema.meta.name == "test-json"
    assert schema.global_tokens.palette["accent"] == "#222222"


def test_phase1e_design_md_off_by_default(agent, monkeypatch):
    monkeypatch.delenv("EDUPPTX_VISUAL_PLANNER_FORMAT", raising=False)
    # generate_design_md should NOT be called when env is unset.
    called = {"hit": False}

    def boom(*args, **kwargs):
        called["hit"] = True
        return "should-not-be-returned"

    monkeypatch.setattr(visual_planner, "generate_design_md", boom)
    out = agent._phase1e_design_md(draft=None, palette_hint=None, template_label="")
    assert out is None
    assert called["hit"] is False


def test_phase1e_design_md_returns_string_when_enabled(agent, monkeypatch):
    monkeypatch.setenv("EDUPPTX_VISUAL_PLANNER_FORMAT", "design_md")
    monkeypatch.setattr(
        visual_planner,
        "generate_design_md",
        lambda draft, config, palette_hint=None, template_label="": "STUB-DESIGN-MD",
    )
    out = agent._phase1e_design_md(draft=None, palette_hint=None, template_label="blue")
    assert out == "STUB-DESIGN-MD"


def test_phase1e_design_md_swallows_exceptions(agent, monkeypatch):
    monkeypatch.setenv("EDUPPTX_VISUAL_PLANNER_FORMAT", "design_md")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated LLM crash")

    monkeypatch.setattr(visual_planner, "generate_design_md", boom)
    out = agent._phase1e_design_md(draft=None, palette_hint=None, template_label="")
    assert out is None
