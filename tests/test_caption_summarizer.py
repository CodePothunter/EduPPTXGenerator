import json

from edupptx.materials import caption_rules
from edupptx.materials.caption_rules import CAPTION_RULE


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.last_system = None

    def chat(self, *, messages, temperature=0.0, max_tokens=2048):
        self.last_system = messages[0]["content"]
        return json.dumps(self._payload, ensure_ascii=False)


def test_summarize_records_writes_caption_field():
    client = _FakeClient([{"caption": "小朋友做游戏"}])
    out = caption_rules.summarize_records(
        [{"query": "7个小朋友做游戏，配对话气泡“请你抬起一条腿”"}],
        client,
        query_field="query",
        caption_field="caption",
        batch_size=50,
    )
    assert out[0]["caption"] == "小朋友做游戏"
    assert out[0]["query"].startswith("7个小朋友")


def test_summarize_query_single_returns_caption():
    client = _FakeClient([{"caption": "小女孩和苹果"}])
    assert caption_rules.summarize_query("扎双丸子头的卡通小女孩和红苹果", client) == "小女孩和苹果"


def test_summarizer_uses_shared_caption_rule():
    client = _FakeClient([{"caption": "x"}])
    caption_rules.summarize_records([{"query": "q"}], client, batch_size=50)
    assert CAPTION_RULE in client.last_system


def test_backfill_field_mapping_uses_content_prompt():
    client = _FakeClient([{"caption": "小女孩和苹果"}])
    out = caption_rules.summarize_records(
        [{"content_prompt": "扎双丸子头的卡通小女孩和红苹果"}],
        client,
        query_field="content_prompt",
        caption_field="caption",
        batch_size=50,
    )
    assert out[0]["caption"] == "小女孩和苹果"
    assert out[0]["content_prompt"].startswith("扎双丸子头")
