"""An empty work-list must be a clean no-op, not a ValueError.

Both Phase 3 SVG generation and Phase 4 postprocess size their thread pool as
``min(len(items), concurrency)`` — which is 0 for an empty deck, and
``ThreadPoolExecutor(max_workers=0)`` raises ``ValueError``. The max(1, ...)
guard turns the degenerate case into an empty result.
"""

import asyncio

from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.design.svg_generator import generate_slide_svgs
from edupptx.models import PlanningDraft, PlanningMeta
from edupptx.session import Session


def _config():
    return Config(llm_api_key="k", llm_model="m", llm_base_url="http://localhost/v1")


def test_phase4_postprocess_empty_slides_is_noop(tmp_path):
    agent = PPTXAgent(_config())
    session = Session(tmp_path / "out")
    assert agent._phase4_postprocess([], session) == []


def test_generate_slide_svgs_empty_pages_is_noop():
    draft = PlanningDraft(meta=PlanningMeta(topic="t"), pages=[])
    result = asyncio.run(generate_slide_svgs(draft, {}, "edu_emerald", _config()))
    assert result == []
