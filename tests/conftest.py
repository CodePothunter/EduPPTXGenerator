"""Shared test fixtures."""

import pytest

from edupptx.config import Config
from edupptx.design_system import get_design_tokens


@pytest.fixture
def design_emerald():
    return get_design_tokens("emerald")


@pytest.fixture
def config():
    return Config(
        llm_api_key="test-key",
        llm_model="test-model",
        llm_base_url="http://localhost:8080/v1",
    )
