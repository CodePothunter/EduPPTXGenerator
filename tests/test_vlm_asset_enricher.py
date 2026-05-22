import base64
import json

from edupptx.materials.ai_image_asset_db import build_ai_image_match_index, enrich_ai_image_asset_db_keywords
from edupptx.materials.vlm_asset_enricher import VLM_SYSTEM_PROMPT, enrich_assets_with_vlm
from edupptx.materials.vlm_metadata_rules import infer_padding_capacity_from_image


class FakeVLMClient:
    _model = "seed-mini-vlm"

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.messages = []

    def chat_vlm_json(self, messages, **kwargs):
        self.messages.append(messages)
        if not self.payloads:
            return {}
        return self.payloads.pop(0)


class FakeKeywordClient:
    _model = "fake-keyword-model"

    def __init__(self):
        self.messages = []

    def chat_json(self, messages, **kwargs):
        self.messages.append(messages)
        raw = messages[1]["content"]
        request = json.loads(raw[raw.index("{") :])
        asset_id = request["assets"][0]["asset_id"]
        return {
            "assets": [
                {
                    "asset_id": asset_id,
                    "context_summary": "keyword context should not replace rewritten context",
                    "teaching_intent": "keyword intent should not replace rewritten intent",
                    "asset_category": "content_specific",
                    "constraints": [{"kind": "object", "value": "apple", "importance": 1}],
                    "core_keywords": ["apple", "bubble chart"],
                    "semantic_aliases": {"apple": ["fruit"]},
                }
            ]
        }


def test_padding_capacity_infers_from_image_edges(tmp_path):
    transparent = tmp_path / "transparent.png"
    light = tmp_path / "light.png"
    colored = tmp_path / "colored.png"

    from PIL import Image, ImageDraw

    cutout = Image.new("RGBA", (80, 80), (255, 255, 255, 0))
    ImageDraw.Draw(cutout).ellipse((20, 20, 60, 60), fill=(40, 120, 220, 255))
    cutout.save(transparent)
    Image.new("RGB", (80, 80), (242, 242, 236)).save(light)
    Image.new("RGB", (80, 80), (60, 130, 210)).save(colored)

    assert infer_padding_capacity_from_image(transparent) == "high"
    assert infer_padding_capacity_from_image(light) == "mid"
    assert infer_padding_capacity_from_image(colored) == "low"


def test_enrich_assets_with_vlm_writes_slim_asset_fields_and_sidecar(tmp_path):
    from PIL import Image

    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    image_path = image_dir / "page.png"
    Image.new("RGB", (80, 80), (242, 242, 236)).save(image_path)
    image_bytes = image_path.read_bytes()
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
            {
                "value": "six animals",
                "present": "absent",
                "confidence": 0.95,
                "evidence": "Only four animals are visible.",
            }
        ],
        "missing_from_metadata": [{"kind": "object", "value": "pine tree", "importance_hint": 2}],
        "query_aliases": {"peacock": ["blue peacock"]},
        "match_quality_score": 0.42,
        "needs_regeneration": True,
    }
    client = FakeVLMClient(payload)

    report = enrich_assets_with_vlm(db, client, image_root=tmp_path)

    assert report["processed_count"] == 1
    assert report["skipped_non_page_image_count"] == 1
    assert db["vlm_review"]["model"] == "seed-mini-vlm"
    asset = db["assets"][0]
    assert asset["constraints"] == [{"kind": "entity", "value": "six animals", "importance": 2}]
    assert asset["vlm_match_quality"] == 0.42
    # VLM enrichment no longer touches padding_capacity — that field is set at
    # annotation / registration time from pixel edges, decoupled from VLM.
    assert "padding_capacity" not in asset
    assert "transform_advice" not in asset
    assert "query_aliases" not in asset
    assert "vlm_needs_regeneration" not in asset
    assert "vlm_constraint_visibility" not in asset

    review_index = json.loads((tmp_path / "ai_image_vlm_review.json").read_text(encoding="utf-8"))
    review = review_index["assets"]["page"]
    assert review["vlm_needs_regeneration"] is True
    assert review["manual_review_required"] is True
    assert review["manual_review_reasons"] == ["low_match_quality"]
    assert review["constraint_visibility"][0]["presence"] == "absent"
    assert review["missing_from_metadata"] == [{"kind": "object", "value": "pine tree", "importance_hint": 2}]
    assert "query_aliases" not in review

    messages = client.messages[0]
    content = messages[1]["content"]
    metadata_text = content[0]["text"]
    assert "six animals with a peacock" in metadata_text
    data_url = content[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == image_bytes
    assert "query_aliases" in VLM_SYSTEM_PROMPT
    assert "不要生成" in VLM_SYSTEM_PROMPT
    assert "transform_advice" not in VLM_SYSTEM_PROMPT


def test_enrich_assets_with_vlm_skips_reviewed_sidecar_unless_forced(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"png-data")
    (tmp_path / "ai_image_vlm_review.json").write_text(
        json.dumps({"schema_version": 6, "assets": {"page": {"asset_id": "page"}}}),
        encoding="utf-8",
    )
    db = {
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "peacock",
            }
        ]
    }
    client = FakeVLMClient({"match_quality_score": 1.0})

    report = enrich_assets_with_vlm(db, client, image_root=tmp_path)

    assert report["processed_count"] == 0
    assert report["skipped_reviewed_count"] == 1
    assert client.messages == []

    forced_report = enrich_assets_with_vlm(db, client, image_root=tmp_path, skip_reviewed=False)

    assert forced_report["processed_count"] == 1
    assert len(client.messages) == 1


def test_enrich_assets_with_vlm_adds_manual_review_debug_queue(tmp_path):
    from PIL import Image

    image_path = tmp_path / "tadpole.png"
    Image.new("RGB", (80, 80), (60, 130, 210)).save(image_path)
    db = {
        "schema_version": 1,
        "output_root": str(tmp_path),
        "assets": [
            {
                "asset_id": "tadpole",
                "asset_kind": "page_image",
                "image_path": "tadpole.png",
                "content_prompt": "tadpoles swimming in a pond",
                "constraints": [
                    {"kind": "entity", "value": "tadpole", "importance": 2},
                    {"kind": "scene", "value": "pond", "importance": 1},
                ],
                "core_keywords": ["tadpole", "pond"],
            }
        ],
    }
    payload = {
        "constraint_verification": [
            {
                "kind": "entity",
                "value": "tadpole",
                "present": "uncertain",
                "confidence": 0.58,
                "evidence": "The animal could be misread.",
                "possible_misread_as": ["small fish"],
            },
            {
                "kind": "scene",
                "value": "pond",
                "present": "absent",
                "confidence": 0.92,
                "evidence": "No visible pond background.",
            },
        ],
        "match_quality_score": 0.49,
    }

    report = enrich_assets_with_vlm(db, FakeVLMClient(payload), image_root=tmp_path)

    assert report["processed_count"] == 1
    assert report["manual_review_count"] == 1
    assert report["manual_review_asset_ids"] == ["tadpole"]
    asset = db["assets"][0]
    assert asset["vlm_match_quality"] == 0.49
    # padding_capacity is no longer written by the VLM step.
    assert "padding_capacity" not in asset
    assert "transform_advice" not in asset
    assert "vlm_effective_constraints" not in asset
    assert "query_aliases" not in asset

    review = json.loads((tmp_path / "ai_image_vlm_review.json").read_text(encoding="utf-8"))["assets"]["tadpole"]
    assert review["effective_constraints"][0]["effective_importance"] == 1
    assert review["effective_constraints"][0]["vlm_possible_misread_as"] == ["small fish"]
    assert set(review["manual_review_reasons"]) >= {"low_match_quality"}
    assert set(review["risk_reasons"]) >= {"strong_constraint_uncertain", "possible_visual_misread"}

    debug_path = tmp_path / "debug" / "vlm_review_queue.jsonl"
    debug_lines = debug_path.read_text(encoding="utf-8").splitlines()
    assert len(debug_lines) == 1
    debug_record = json.loads(debug_lines[0])
    assert debug_record["asset_id"] == "tadpole"
    assert debug_record["review_status"] == "pending"
    assert debug_record["possible_misreads"][0]["possible_misread_as"] == ["small fish"]
    assert "query_aliases" not in debug_record


def test_low_quality_asset_is_redescribed_and_keywords_are_rebuilt(tmp_path):
    from PIL import Image

    image_path = tmp_path / "page.png"
    Image.new("RGB", (80, 80), (242, 242, 236)).save(image_path)
    db = {
        "schema_version": 1,
        "output_root": str(tmp_path),
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "single character word bubble chart",
                "context_summary": "old context",
                "teaching_intent": "old intent",
                "context_summary_keywords": ["old context keyword"],
                "asset_category": "character_action",
                "constraints": [{"kind": "entity", "value": "tadpole", "importance": 2}],
                "core_keywords": ["character"],
                "semantic_aliases": {"character": ["single character"]},
            }
        ],
    }
    review_payload = {
        "constraint_verification": [{"value": "single character word bubble chart", "present": "absent"}],
        "match_quality_score": 0.2,
        "needs_regeneration": True,
    }
    redescribe_payload = {
        "content_prompt": "apple object bubble chart",
        "detail_prompt": "An apple is surrounded by fruit objects in bubbles.",
        "context_summary": "apple related objects in bubbles",
        "teaching_intent": "compare fruit related objects",
        "core_keywords": ["ignored"],
    }
    keyword_client = FakeKeywordClient()

    report = enrich_assets_with_vlm(
        db,
        FakeVLMClient(review_payload, redescribe_payload),
        image_root=tmp_path,
        keyword_client=keyword_client,
    )

    assert report["processed_count"] == 1
    assert report["auto_rewrite_count"] == 1
    assert report["manual_review_count"] == 0
    assert report["keyword_rewrite_count"] == 1
    asset = db["assets"][0]
    assert asset["regenerate"] is True
    assert asset["content_prompt"] == "apple object bubble chart"
    assert asset["detail_prompt"] == "An apple is surrounded by fruit objects in bubbles."
    assert asset["context_summary"] == "apple related objects in bubbles"
    assert asset["teaching_intent"] == "compare fruit related objects"
    assert asset["asset_category"] == "content_specific"
    assert [(item["kind"], item["value"], item["importance"]) for item in asset["constraints"]] == [
        ("object", "apple", 1)
    ]
    assert asset["core_keywords"] == ["apple", "bubble chart"]
    assert asset["semantic_aliases"] == {"apple": ["fruit"]}
    assert asset["context_summary_keywords"] == []
    assert "tadpole" not in json.dumps(asset, ensure_ascii=False)
    assert "character" not in asset["core_keywords"]
    assert "query_aliases" not in asset

    review = json.loads((tmp_path / "ai_image_vlm_review.json").read_text(encoding="utf-8"))["assets"]["page"]
    assert review["action"] == "auto_rewrite"
    assert review["manual_review_required"] is False
    assert review["regenerate"] is True
    assert review["rewritten_metadata"]["content_prompt"] == "apple object bubble chart"
    assert not (tmp_path / "debug" / "vlm_review_queue.jsonl").exists()
    assert len(keyword_client.messages) == 1


def test_high_quality_score_overrides_manual_review_triggers(tmp_path):
    from PIL import Image

    image_path = tmp_path / "page.png"
    Image.new("RGB", (80, 80), (242, 242, 236)).save(image_path)
    db = {
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "tadpole",
                "constraints": [{"kind": "entity", "value": "tadpole", "importance": 2}],
            }
        ]
    }
    payload = {
        "constraint_verification": [
            {
                "kind": "entity",
                "value": "tadpole",
                "present": "uncertain",
                "possible_misread_as": ["small fish"],
            }
        ],
        "match_quality_score": 0.75,
        "needs_regeneration": True,
    }

    report = enrich_assets_with_vlm(db, FakeVLMClient(payload), image_root=tmp_path)

    assert report["accepted_count"] == 1
    assert report["manual_review_count"] == 0
    review = json.loads((tmp_path / "ai_image_vlm_review.json").read_text(encoding="utf-8"))["assets"]["page"]
    assert review["manual_review_required"] is False
    assert review["action"] == "accept"
    assert set(review["risk_reasons"]) >= {"needs_regeneration", "possible_visual_misread"}


def test_possible_visual_misread_reviews_only_mid_quality_scores(tmp_path):
    from PIL import Image

    image_path = tmp_path / "page.png"
    Image.new("RGB", (80, 80), (242, 242, 236)).save(image_path)
    db = {
        "assets": [
            {
                "asset_id": "mid",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "duck avatar",
            },
            {
                "asset_id": "accepted",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "word card border",
            },
        ]
    }
    client = FakeVLMClient(
        {
            "constraint_verification": [
                {
                    "value": "duck avatar",
                    "present": "uncertain",
                    "possible_misread_as": ["duck full body"],
                }
            ],
            "match_quality_score": 0.65,
        },
        {
            "constraint_verification": [
                {
                    "value": "word card border",
                    "present": "uncertain",
                    "possible_misread_as": ["blank card border"],
                }
            ],
            "match_quality_score": 0.75,
        },
    )

    report = enrich_assets_with_vlm(db, client, image_root=tmp_path)

    assert report["manual_review_count"] == 1
    assert report["manual_review_asset_ids"] == ["mid"]
    assert report["accepted_count"] == 1

    review_assets = json.loads((tmp_path / "ai_image_vlm_review.json").read_text(encoding="utf-8"))["assets"]
    assert review_assets["mid"]["manual_review_reasons"] == ["possible_visual_misread"]
    assert review_assets["accepted"]["manual_review_required"] is False
    assert review_assets["accepted"]["action"] == "accept"
    assert "possible_visual_misread" in review_assets["accepted"]["risk_reasons"]

    debug_path = tmp_path / "debug" / "vlm_review_queue.jsonl"
    debug_lines = debug_path.read_text(encoding="utf-8").splitlines()
    assert len(debug_lines) == 1
    assert json.loads(debug_lines[0])["asset_id"] == "mid"


def test_vlm_reuse_group_mismatch_high_confidence_auto_corrects(tmp_path):
    from PIL import Image

    image_path = tmp_path / "page.png"
    Image.new("RGB", (80, 80), (242, 242, 236)).save(image_path)
    db = {
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "generic classroom illustration",
                "strict_reuse_group": "general_reuse",
                "strict_reuse_requires_exact_match": True,
            }
        ]
    }
    payload = {
        "constraint_verification": [],
        "match_quality_score": 0.76,
        "visual_reuse_group": "content_reuse",
        "visual_reuse_confidence": 0.86,
        "visual_reuse_reason": "The image contains visible exercise text.",
    }

    report = enrich_assets_with_vlm(db, FakeVLMClient(payload), image_root=tmp_path)

    assert report["accepted_count"] == 1
    assert report["manual_review_count"] == 0
    asset = db["assets"][0]
    assert asset["strict_reuse_group"] == "content_reuse"
    assert asset["strict_reuse_confidence"] == 0.86
    assert "strict_reuse_requires_exact_match" not in asset

    review = json.loads((tmp_path / "ai_image_vlm_review.json").read_text(encoding="utf-8"))["assets"]["page"]
    assert review["strict_reuse_group_mismatch"] is True
    assert review["strict_reuse_auto_corrected"] is True
    assert review["strict_reuse_group_update"] == "content_reuse"
    assert review["manual_review_required"] is False


def test_vlm_reuse_group_mismatch_low_confidence_goes_to_manual_review(tmp_path):
    from PIL import Image

    image_path = tmp_path / "page.png"
    Image.new("RGB", (80, 80), (242, 242, 236)).save(image_path)
    db = {
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "exercise text card",
                "strict_reuse_group": "content_reuse",
            }
        ]
    }
    payload = {
        "constraint_verification": [],
        "match_quality_score": 0.76,
        "visual_reuse_group": "general_reuse",
        "visual_reuse_confidence": 0.7,
        "visual_reuse_reason": "The image may only be a blank card.",
    }

    report = enrich_assets_with_vlm(db, FakeVLMClient(payload), image_root=tmp_path)

    assert report["manual_review_count"] == 1
    assert report["manual_review_asset_ids"] == ["page"]
    assert db["assets"][0]["strict_reuse_group"] == "content_reuse"

    review = json.loads((tmp_path / "ai_image_vlm_review.json").read_text(encoding="utf-8"))["assets"]["page"]
    assert review["manual_review_required"] is True
    assert review["action"] == "manual_review"
    assert review["manual_review_reasons"] == ["strict_reuse_group_mismatch"]

    debug_path = tmp_path / "debug" / "vlm_review_queue.jsonl"
    debug_record = json.loads(debug_path.read_text(encoding="utf-8").splitlines()[0])
    assert debug_record["visual_reuse_group"] == "general_reuse"
    assert debug_record["llm_reuse_group"] == "content_reuse"


def test_keyword_enrichment_preserves_review_fields():
    class KeywordClient:
        _model = "fake-keyword-model"

        def chat_json(self, messages, **kwargs):
            raw = messages[1]["content"]
            request = json.loads(raw[raw.index("{") :])
            asset_id = request["assets"][0]["asset_id"]
            return {
                "assets": [
                    {
                        "asset_id": asset_id,
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
                "padding_capacity": "mid",
                "vlm_match_quality": 0.9,
                "regenerate": True,
            }
        ],
    }

    enrich_ai_image_asset_db_keywords(db, KeywordClient())

    asset = db["assets"][0]
    assert asset["padding_capacity"] == "mid"
    assert "transform_advice" not in asset
    assert asset["vlm_match_quality"] == 0.9
    assert asset["regenerate"] is True


def test_match_index_keeps_only_review_passthrough_fields(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"png-data")
    db = {
        "schema_version": 1,
        "output_root": str(tmp_path),
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "page.png",
                "content_prompt": "blue peacock",
                "asset_category": "content_specific",
                "constraints": [{"kind": "entity", "value": "peacock", "importance": 2}],
                "core_keywords": ["peacock"],
                "semantic_aliases": {},
                "query_aliases": {"peacock": ["blue peacock"]},
                "padding_capacity": "mid",
                "vlm_match_quality": 0.91,
                "regenerate": True,
                "vlm_constraint_visibility": [{"value": "peacock", "presence": "present"}],
                "strict_reuse_group": "content_reuse",
                "strict_reuse_requires_exact_match": True,
            }
        ],
    }

    index = build_ai_image_match_index(db, library_root=tmp_path)

    match_asset = index["assets"][0]
    assert "vlm_constraint_visibility" not in match_asset
    assert "query_aliases" not in match_asset
    assert match_asset["vlm_match_quality"] == 0.91
    assert match_asset["regenerate"] is True
    assert match_asset["padding_capacity"] == "mid"
    assert match_asset["strict_reuse_group"] == "content_reuse"
    assert "strict_reuse_requires_exact_match" not in match_asset
    assert "transform_advice" not in match_asset
