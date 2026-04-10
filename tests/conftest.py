"""Shared test fixtures."""

import pytest

from edupptx.config import Config
from edupptx.design_system import get_design_tokens
from edupptx.material_library import MaterialLibrary
from edupptx.session import Session


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


@pytest.fixture
def library(tmp_path):
    return MaterialLibrary(tmp_path / "test_library")


@pytest.fixture
def session(tmp_path):
    return Session(tmp_path / "test_output")
