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


class _FakeCaptionClient:
    def chat(self, messages, temperature=0.0, max_tokens=0):
        user = messages[-1]["content"]
        arr = json.loads(user[user.index("[") :])
        return json.dumps(
            [{"query": it["query"], "caption": "粗caption"} for it in arr],
            ensure_ascii=False,
        )


def test_backfill_from_query_overwrites_existing_caption(tmp_path: Path):
    index_path = tmp_path / "C01.json"
    index_path.write_text(
        json.dumps(
            {
                "assets": [
                    {"asset_id": "a", "query": "含寒山寺、远山、古塔的水墨山水画", "caption": "细caption"}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    updated = backfill_caption_in_index(
        index_path,
        _FakeCaptionClient(),
        source_field="query",
        only_missing=False,
    )
    assert updated == 1
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["assets"][0]["caption"] == "粗caption"
