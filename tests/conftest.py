"""Shared test fixtures."""

import pytest
from pathlib import Path

from edupptx.config import Config
from edupptx.session import Session
from edupptx.style_resolver import resolve_style
from edupptx.style_schema import load_style

STYLES_DIR = Path(__file__).parent.parent / "styles"


@pytest.fixture
def resolved_emerald():
    return resolve_style(load_style(STYLES_DIR / "emerald.json"))


@pytest.fixture
def config():
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="http://localhost:8080/v1",
    )


@pytest.fixture
def session(tmp_path):
    return Session(tmp_path / "test_output")
