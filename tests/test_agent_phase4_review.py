"""P1-b: Phase 4 re-validates each SVG after the LLM review pass.

review_and_fix_svg is a single LLM pass whose only structural guard is a
placeholder count — it can hand back off-canvas / unsafe content that Step 1's
deterministic validator had already fixed. _phase4_postprocess must re-run
validate_and_fix on the reviewed SVG before sanitizing.
"""

from pathlib import Path

from lxml import etree

import edupptx.agent as agent_mod
import edupptx.postprocess.svg_reviewer as reviewer_mod
from edupptx.agent import PPTXAgent
from edupptx.config import Config
from edupptx.models import (
    GeneratedSlide,
    PagePlan,
    PlanningDraft,
    PlanningMeta,
    VisualPlan,
)
from edupptx.session import Session

SVG_NS = "http://www.w3.org/2000/svg"


def test_phase4_revalidates_after_review(tmp_path, monkeypatch):
    # Force the cost-gated review to fire, and make the reviewer return an SVG
    # whose text sits far below the 720 canvas — the exact regression class the
    # reviewer's placeholder-only guard does not catch.
    monkeypatch.setattr(agent_mod, "_needs_llm_review", lambda page, warnings: True)
    dirty = (
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
        '<text x="100" y="900" font-size="20">越界的正文文字</text></svg>'
    )
    monkeypatch.setattr(reviewer_mod, "review_and_fix_svg", lambda *a, **k: dirty)

    agent = PPTXAgent(
        Config(llm_api_key="k", llm_model="m", llm_base_url="http://localhost/v1")
    )
    session = Session(tmp_path / "out")
    draft = PlanningDraft(
        meta=PlanningMeta(topic="测试"),
        visual=VisualPlan(),
        pages=[PagePlan(page_number=1, page_type="content", title="标题")],
    )
    slide = GeneratedSlide(
        page_number=1,
        svg_content=(
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
            '<text x="100" y="200">原始</text></svg>'
        ),
    )

    paths = agent._phase4_postprocess([slide], session, draft=draft, do_review=True)

    saved = Path(paths[0]).read_text(encoding="utf-8")
    root = etree.fromstring(saved.encode())
    ys = [float(t.get("y")) for t in root.iter(f"{{{SVG_NS}}}text") if t.get("y")]
    assert ys, "no <text> survived postprocess"
    assert max(ys) <= 720, f"post-review off-canvas text not re-clamped: {ys}"
