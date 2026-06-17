"""`edupptx render` (run_from_plan) must not destroy the user's hand-edited
plan.json. The model roundtrip drops unknown fields and save_plan overwrites
the same plan.json the user just edited, so run_from_plan first mirrors the
verbatim input to plan.input.json for recovery.
"""

import asyncio
import json

from edupptx.agent import PPTXAgent
from edupptx.config import Config


def test_run_from_plan_preserves_user_input(tmp_path, monkeypatch):
    session_dir = tmp_path / "session_x"
    session_dir.mkdir()
    plan_path = session_dir / "plan.json"
    original = {
        "meta": {"topic": "测试主题"},
        "pages": [{"page_number": 1, "page_type": "content", "title": "标题"}],
        # an unknown field a user might add by hand — dropped by the model
        # roundtrip, so it must survive in the backup.
        "user_custom_note": "请勿丢失这个字段",
    }
    plan_path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
    original_bytes = plan_path.read_bytes()

    agent = PPTXAgent(
        Config(llm_api_key="k", llm_model="m", llm_base_url="http://localhost/v1")
    )

    # Stub the heavy phases; exercise only the load -> preserve -> normalize path.
    monkeypatch.setattr(agent, "_ensure_template_state", lambda d: d)
    monkeypatch.setattr(agent, "_route_ai_image_prompts", lambda d: d)
    monkeypatch.setattr(agent, "_new_ai_image_reuse_search_context", lambda: None)
    monkeypatch.setattr(agent, "_persist_reuse_query_cache", lambda *a, **k: None)

    async def _no_bg(*a, **k):
        return None

    async def _no_slides(*a, **k):
        return []

    monkeypatch.setattr(agent, "_phase2_background", _no_bg)
    monkeypatch.setattr(agent, "_phase3_design", _no_slides)
    monkeypatch.setattr(agent, "_phase4_postprocess", lambda *a, **k: [])
    monkeypatch.setattr(agent, "_phase5_output", lambda *a, **k: None)

    asyncio.run(agent.run_from_plan(plan_path, debug=True))

    backup = session_dir / "plan.input.json"
    assert backup.exists(), "plan.input.json backup was not created"
    assert backup.read_bytes() == original_bytes, "backup is not the verbatim input"
    # the user-added field is recoverable from the backup even though the
    # normalized plan.json silently drops it
    assert json.loads(backup.read_text(encoding="utf-8"))["user_custom_note"] == "请勿丢失这个字段"
    assert "user_custom_note" not in json.loads(plan_path.read_text(encoding="utf-8"))
