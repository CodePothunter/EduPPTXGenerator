import json
from pathlib import Path

from scripts.summarize_image_queries_with_doubao import _load_json_array

# NOTE: the LLM batch summarization logic now lives in
# edupptx.materials.caption_rules and is covered by tests/test_caption_summarizer.py.
# This file only covers the script-specific input normalization.


def test_load_json_array_accepts_strings_and_objects(tmp_path: Path):
    data = ["纯字符串查询", {"query": "对象查询"}, {"content_prompt": "兼容字段"}]
    p = tmp_path / "in.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    items = _load_json_array(p)
    assert items[0]["query"] == "纯字符串查询"
    assert items[1]["query"] == "对象查询"
    assert items[2]["query"] == "兼容字段"
