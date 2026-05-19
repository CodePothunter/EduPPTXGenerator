import base64
import json

from edupptx.materials.ai_image_asset_db import enrich_ai_image_asset_db_keywords
from edupptx.materials.vlm_asset_enricher import enrich_assets_with_vlm


class FakeVLMClient:
    _model = "seed-mini-vlm"

    def __init__(self, payload):
        self.payload = payload
        self.messages = []

    def chat_vlm_json(self, messages, **kwargs):
        self.messages.append(messages)
        return self.payload


def test_enrich_assets_with_vlm_appends_fields_and_sends_image_data_url(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    image_path = image_dir / "page.png"
    image_path.write_bytes(b"png-data")
    db = {
        "schema_version": 1,
        "output_root": str(tmp_path),
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "ai_images/page.png",
                "content_prompt": "six animals with a peacock",
                "constraints": [{"kind": "entity", "value": "six animals", "importance": 2}],
                "core_keywords": ["peacock"],
                "semantic_aliases": {},
            },
            {
                "asset_id": "background",
                "asset_kind": "background",
                "image_path": "ai_images/page.png",
            },
        ],
    }
    payload = {
        "constraint_verification": [
            {"value": "six animals", "present": False, "evidence": "Only four animals are visible."}
        ],
        "missing_from_metadata": [
            {"kind": "object", "value": "pine tree", "importance_hint": 2}
        ],
        "visual_aliases": {"peacock": ["blue peacock", "fan tail"]},
        "visual_style": {
            "dominant_colors": ["#abcdef", "#123456"],
            "composition": "centered",
            "background_type": "flat",
        },
        "match_quality_score": 0.42,
        "needs_regeneration": True,
    }
    client = FakeVLMClient(payload)

    report = enrich_assets_with_vlm(db, client, image_root=tmp_path)

    assert report["processed_count"] == 1
    assert report["skipped_non_page_image_count"] == 1
    assert db["vlm_builder"]["model"] == "seed-mini-vlm"
    asset = db["assets"][0]
    assert asset["constraints"] == [{"kind": "entity", "value": "six animals", "importance": 2}]
    assert asset["vlm_verified"] is True
    assert asset["vlm_verified_constraints"] == [
        {"value": "six animals", "present": False, "evidence": "Only four animals are visible."}
    ]
    assert asset["vlm_missing_from_prompt"] == [
        {"kind": "object", "value": "pine tree", "importance_hint": 2}
    ]
    assert asset["vlm_visual_aliases"] == {"peacock": ["blue peacock", "fan tail"]}
    assert asset["vlm_visual_style"] == {
        "dominant_colors": ["#ABCDEF", "#123456"],
        "composition": "centered",
        "background_type": "flat",
    }
    assert asset["vlm_match_quality"] == 0.42
    assert asset["vlm_needs_regeneration"] is True

    messages = client.messages[0]
    content = messages[1]["content"]
    metadata_text = content[0]["text"]
    assert "six animals with a peacock" in metadata_text
    data_url = content[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == b"png-data"


def test_enrich_assets_with_vlm_skips_verified_unless_forced(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"png-data")
    db = {
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "peacock",
                "vlm_verified": True,
            }
        ]
    }
    client = FakeVLMClient({"match_quality_score": 1.0})

    report = enrich_assets_with_vlm(db, client, image_root=tmp_path)

    assert report["processed_count"] == 0
    assert report["skipped_verified_count"] == 1
    assert client.messages == []

    forced_report = enrich_assets_with_vlm(db, client, image_root=tmp_path, skip_verified=False)

    assert forced_report["processed_count"] == 1
    assert len(client.messages) == 1


def test_enrich_assets_with_vlm_reports_missing_images_and_asset_ids(tmp_path):
    db = {
        "assets": [
            {
                "asset_id": "target",
                "asset_kind": "page_image",
                "image_path": "missing.png",
                "content_prompt": "peacock",
            }
        ]
    }
    client = FakeVLMClient({"match_quality_score": 1.0})

    report = enrich_assets_with_vlm(
        db,
        client,
        image_root=tmp_path,
        asset_ids=["target", "absent"],
    )

    assert report["processed_count"] == 0
    assert report["missing_image_count"] == 1
    assert report["missing_asset_ids"] == ["absent"]
    assert client.messages == []


def test_keyword_enrichment_preserves_existing_vlm_fields():
    class FakeKeywordClient:
        _model = "fake-keyword-model"

        def chat_json(self, messages, **kwargs):
            raw = messages[1]["content"]
            request = json.loads(raw[raw.index("{"):])
            asset_id = request["assets"][0]["asset_id"]
            return {
                "assets": [
                    {
                        "asset_id": asset_id,
                        "normalized_prompt": "blue peacock",
                        "context_summary": "teaching illustration",
                        "teaching_intent": "show the peacock",
                        "asset_category": "content_specific",
                        "constraints": [],
                        "core_keywords": ["peacock"],
                        "semantic_aliases": {"peacock": ["bird"]},
                    }
                ]
            }

    db = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "blue peacock",
                "vlm_verified": True,
                "vlm_visual_aliases": {"peacock": ["blue peacock"]},
                "vlm_match_quality": 0.9,
            }
        ],
    }

    enrich_ai_image_asset_db_keywords(db, FakeKeywordClient())

    asset = db["assets"][0]
    assert asset["vlm_verified"] is True
    assert asset["vlm_visual_aliases"] == {"peacock": ["blue peacock"]}
    assert asset["vlm_match_quality"] == 0.9
