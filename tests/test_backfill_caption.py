import json
from pathlib import Path

from scripts.backfill_caption import backfill_caption_in_index


class _FakeClient:
    def chat(self, *, messages, temperature=0.0, max_tokens=2048):
        return json.dumps([{"caption": "girl and apple"}], ensure_ascii=False)


def test_backfill_adds_caption_keeps_label(tmp_path: Path):
    index = {
        "assets": [
            {
                "asset_id": "a1",
                "content_prompt": "cartoon girl with red apple",
                "strict_reuse_group": "C02_generic_subject_object",
            }
        ]
    }
    path = tmp_path / "idx.json"
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    updated = backfill_caption_in_index(path, _FakeClient(), batch_size=50)

    out = json.loads(path.read_text(encoding="utf-8"))["assets"][0]
    assert updated == 1
    assert out["caption"] == "girl and apple"
    assert out["strict_reuse_group"] == "C02_generic_subject_object"


def test_backfill_keeps_existing_caption_by_default(tmp_path: Path):
    index = {
        "assets": [
            {
                "asset_id": "a1",
                "content_prompt": "cartoon girl with red apple",
                "caption": "existing caption",
            }
        ]
    }
    path = tmp_path / "idx.json"
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    updated = backfill_caption_in_index(path, _FakeClient(), batch_size=50)

    out = json.loads(path.read_text(encoding="utf-8"))["assets"][0]
    assert updated == 0
    assert out["caption"] == "existing caption"
