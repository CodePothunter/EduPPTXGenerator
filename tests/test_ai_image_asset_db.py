import hashlib
import json

from PIL import Image

import edupptx.materials.ai_image_asset_db as image_db
from edupptx.materials.ai_image_asset_db import (
    ASPECT_REUSE_BUCKETS,
    BACKGROUND_COLOR_BIAS_REUSE_WEIGHT,
    BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT,
    DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE,
    DEFAULT_REUSE_CANDIDATE_LIMIT,
    HYBRID_BM25_WEIGHT,
    HYBRID_EMBEDDING_WEIGHT,
    HYBRID_SUBSTRING_WEIGHT,
    MAX_LLM_REVIEW_WORKERS,
    MAX_LLM_REVIEWS_PER_QUERY,
    BACKGROUND_REUSE_GATE_THRESHOLDS,
    PAGE_IMAGE_REUSE_GATE_THRESHOLDS,
    _apply_strict_reuse_group_from_payload,
    _apply_keyword_payload,
    _asset_embedding_text,
    _build_keyword_messages,
    _candidate_unknown_fields_for_reuse,
    _candidate_hybrid_text,
    _reuse_gate_profile,
    _reuse_gate_thresholds_for_target,
    _reuse_debug_asset_payload,
    _reuse_hard_filter_reject_reason,
    _reuse_review_accept_score_threshold,
    _route_match_index_for_target,
    _score_reuse_candidate_details,
    _subject_scope_compatible,
    _subject_scope_decision,
    _target_metadata_unknown_fields,
    _target_unknown_fields_for_reuse,
    _normalize_subject_value,
    _normalize_asset_for_match,
    _save_reusable_png_with_transparent_padding,
    build_ai_image_match_index,
    find_reusable_ai_image_asset,
    infer_grade,
    infer_grade_band,
    materialize_reused_ai_image_asset,
    normalize_aspect_bucket,
    normalize_grade_info,
    read_ai_image_split_match_index,
    write_ai_image_match_index,
    write_ai_image_split_match_indexes,
)


DELETED_FIELDS = {
    "core_keywords",
    "semantic_aliases",
    "constraints",
    "context_summary_keywords",
}


def _asset(asset_id: str, group: str, *, prompt: str = "single apple card") -> dict:
    return {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "image_path": f"ai_images/{asset_id}.png",
        "original_image_path": f"ai_images_original/{asset_id}.png",
        "actual_width": 1200,
        "actual_height": 571,
        "padded_width": 1200,
        "padded_height": 675,
        "aspect_ratio": "1:1",
        "aspect_bucket": "1:1",
        "role": "illustration",
        "padding_capacity": "high",
        "padded_image_path": f"ai_images_padded/{asset_id}.png",
        "content_prompt": prompt,
        "context_summary": "object recognition page",
        "teaching_intent": "recognize the object",
        "strict_reuse_group": group,
        "core_keywords": ["legacy keyword"],
        "semantic_aliases": {"legacy keyword": ["old alias"]},
        "constraints": [{"kind": "object", "value": "legacy", "importance": 2}],
        "context_summary_keywords": ["legacy context keyword"],
    }


def test_reuse_constants_match_phase4_configuration():
    assert DEFAULT_REUSE_CANDIDATE_LIMIT == 8
    assert DEFAULT_HYBRID_RETRIEVAL_POOL_SIZE == 20
    assert MAX_LLM_REVIEWS_PER_QUERY == 5
    assert MAX_LLM_REVIEW_WORKERS == 15
    assert (HYBRID_BM25_WEIGHT, HYBRID_EMBEDDING_WEIGHT, HYBRID_SUBSTRING_WEIGHT) == (0.25, 0.55, 0.20)
    assert BACKGROUND_CONTENT_PROMPT_REUSE_WEIGHT == 0.85
    assert BACKGROUND_COLOR_BIAS_REUSE_WEIGHT == 0.15


def test_aspect_bucket_set_and_nearest_mapping_are_fixed():
    assert ASPECT_REUSE_BUCKETS == ("1:1", "3:4", "4:3", "9:16", "16:9", "other")
    assert normalize_aspect_bucket(width=1920, height=1080) == "16:9"
    assert normalize_aspect_bucket(width=1080, height=1920) == "9:16"
    assert normalize_aspect_bucket(width=1200, height=900) == "4:3"
    assert normalize_aspect_bucket(width=900, height=1200) == "3:4"
    assert normalize_aspect_bucket(width=1000, height=1000) == "1:1"
    assert normalize_aspect_bucket(width=2000, height=1000) == "other"
    assert "9:6" not in ASPECT_REUSE_BUCKETS
    assert "6:9" not in ASPECT_REUSE_BUCKETS


def test_match_index_skips_c00_and_drops_deleted_fields(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    for name in ("skip", "keep", "unknown_default"):
        (image_dir / f"{name}.png").write_bytes(name.encode("ascii"))

    db = {
        "schema_version": 1,
        "assets": [
            _asset("skip", "C00_strict_text_problem_skip"),
            _asset("unknown_default", "not_a_current_material_category", prompt="generic classroom illustration"),
            _asset("keep", "C02_generic_subject_object"),
        ],
    }

    index = build_ai_image_match_index(db, library_root=tmp_path)

    assert [asset["asset_id"] for asset in index["assets"]] == ["keep", "unknown_default"]
    match_asset = next(asset for asset in index["assets"] if asset["asset_id"] == "keep")
    legacy_asset = next(asset for asset in index["assets"] if asset["asset_id"] == "unknown_default")
    assert match_asset["strict_reuse_group"] == "C02_generic_subject_object"
    assert legacy_asset["strict_reuse_group"] == "C03_scene_decor_container"
    assert match_asset["aspect_ratio"] == "1:1"
    assert match_asset["original_image_path"] == "ai_images_original/keep.png"
    assert match_asset["actual_width"] == 1200
    assert match_asset["actual_height"] == 571
    assert match_asset["padded_width"] == 1200
    assert match_asset["padded_height"] == 675
    assert match_asset["teaching_intent"] == "recognize the object"
    for removed_field in ("role", "aspect_bucket", "padding_capacity", "padded_image_path"):
        assert removed_field not in match_asset
    for field in DELETED_FIELDS:
        assert field not in match_asset


def test_keyword_messages_do_not_include_image_role():
    messages = _build_keyword_messages(
        [
            {
                "asset_id": "a1",
                "asset_kind": "page_image",
                "theme": "lesson",
                "content_prompt": "apple illustration",
                "role": "illustration",
                "page_type": "content",
                "aspect_ratio": "1:1",
                "strict_reuse_group": "C02_generic_subject_object",
            }
        ],
        include_match_keywords=False,
    )

    payload = json.loads(messages[-1]["content"].split("\n", 1)[1])
    assert "image_role" not in payload["assets"][0]
    assert "role" not in payload["assets"][0]


def test_embedding_sidecar_reuses_when_meta_matches_embeddable_asset_count(tmp_path, monkeypatch):
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", "local-model")

    import numpy as np

    index_path = tmp_path / image_db.DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = tmp_path / image_db.DEFAULT_EMBEDDING_META_FILENAME
    np.savez_compressed(
        index_path,
        asset_ids=np.asarray(["asset_with_text"], dtype=str),
        vectors=np.asarray([[1.0, 0.0, 0.0]], dtype="float32"),
    )
    meta_path.write_text(
        json.dumps(
            {
                "schema_version": image_db.EMBEDDING_INDEX_SCHEMA_VERSION,
                "model": "local-model",
                "asset_count": 1,
                "background_color_bias_asset_count": 0,
                "vector_dim": 3,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    match_index = {
        "assets": [
            {
                "asset_id": "asset_with_text",
                "asset_kind": "page_image",
                "image_path": "pptx_images/asset_with_text.png",
                "caption": "apple card illustration",
            },
            {
                "asset_id": "asset_without_text",
                "asset_kind": "page_image",
                "image_path": "pptx_images/asset_without_text.png",
            },
        ],
    }

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("embedding sidecar should be reused, not rebuilt")

    monkeypatch.setattr(image_db, "write_ai_image_embedding_index", fail_rebuild)

    report = image_db._ensure_ai_image_embedding_index(match_index, tmp_path)

    assert report["enabled"] is True
    assert report["asset_count"] == 1
    assert report["match_asset_count"] == 2
    assert report["non_embeddable_asset_count"] == 1


def test_embedding_sidecar_reuses_cross_platform_local_model_path(tmp_path, monkeypatch):
    local_model = tmp_path / "models" / "Qwen3-Embedding-0.6B"
    local_model.mkdir(parents=True)
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", str(local_model))

    import numpy as np

    index_path = tmp_path / image_db.DEFAULT_EMBEDDING_INDEX_FILENAME
    meta_path = tmp_path / image_db.DEFAULT_EMBEDDING_META_FILENAME
    np.savez_compressed(
        index_path,
        asset_ids=np.asarray(["asset_with_text"], dtype=str),
        vectors=np.asarray([[1.0, 0.0, 0.0]], dtype="float32"),
    )
    meta_path.write_text(
        json.dumps(
            {
                "schema_version": image_db.EMBEDDING_INDEX_SCHEMA_VERSION,
                "model": "/home/zsq/EduPPTXGenerator/models/Qwen3-Embedding-0.6B",
                "asset_count": 1,
                "background_color_bias_asset_count": 0,
                "vector_dim": 3,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    match_index = {
        "assets": [
            {
                "asset_id": "asset_with_text",
                "asset_kind": "page_image",
                "image_path": "pptx_images/asset_with_text.png",
                "caption": "apple card illustration",
            }
        ],
    }

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("cross-platform local model path should reuse sidecar")

    monkeypatch.setattr(image_db, "write_ai_image_embedding_index", fail_rebuild)

    report = image_db._ensure_ai_image_embedding_index(match_index, tmp_path)

    assert report["enabled"] is True
    assert report["model"] == "Qwen3-Embedding-0.6B"
    assert report["asset_count"] == 1


def test_embedding_build_reuses_unchanged_vectors_and_encodes_only_changed_assets(tmp_path, monkeypatch):
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", "local-model")

    import numpy as np

    keep = {**_asset("keep", "C02_generic_subject_object"), "caption": "single apple card"}
    changed_old = {**_asset("changed", "C02_generic_subject_object"), "caption": "old triangle card"}
    changed_new = {**_asset("changed", "C02_generic_subject_object"), "caption": "new triangle card"}
    added = {**_asset("added", "C02_generic_subject_object"), "caption": "new square card"}

    old_rows = [
        (keep["asset_id"], image_db._asset_embedding_text(keep)),
        (changed_old["asset_id"], image_db._asset_embedding_text(changed_old)),
    ]
    (tmp_path / image_db.DEFAULT_EMBEDDING_META_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": image_db.EMBEDDING_INDEX_SCHEMA_VERSION,
                "model": "local-model",
                "asset_count": 2,
                "background_color_bias_asset_count": 0,
                "vector_dim": 3,
                "assets": [
                    {
                        "asset_id": asset_id,
                        "embedding_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                    }
                    for asset_id, text in old_rows
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    np.savez_compressed(
        tmp_path / image_db.DEFAULT_EMBEDDING_INDEX_FILENAME,
        asset_ids=np.asarray(["keep", "changed"], dtype=str),
        vectors=np.asarray([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype="float32"),
    )

    encoded_texts: list[str] = []

    def fake_encode_embedding_texts(texts, **_kwargs):
        encoded_texts.extend(texts)
        return np.asarray(
            [[0.0, 0.0, float(index + 1)] for index, _text in enumerate(texts)],
            dtype="float32",
        )

    monkeypatch.setattr("edupptx.reuse._embedding._encode_embedding_texts", fake_encode_embedding_texts)

    report = image_db.write_ai_image_embedding_index(
        {"assets": [keep, changed_new, added]},
        tmp_path,
    )

    assert encoded_texts == [
        image_db._asset_embedding_text(changed_new),
        image_db._asset_embedding_text(added),
    ]
    assert report["reused_asset_count"] == 1
    assert report["encoded_asset_count"] == 2
    meta = json.loads((tmp_path / image_db.DEFAULT_EMBEDDING_META_FILENAME).read_text(encoding="utf-8"))
    assert meta["model"] == "local-model"
    assert "model_identity" not in meta
    with np.load(tmp_path / image_db.DEFAULT_EMBEDDING_INDEX_FILENAME) as data:
        assert data["asset_ids"].tolist() == ["keep", "changed", "added"]
        assert data["vectors"].tolist()[0] == [1.0, 0.0, 0.0]


def test_embedding_build_writes_missing_caption_review_and_does_not_embed_query(tmp_path, monkeypatch):
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", "local-model")

    def fail_encode(*_args, **_kwargs):
        raise AssertionError("query must not be embedded when caption is missing")

    monkeypatch.setattr("edupptx.reuse._embedding._encode_embedding_texts", fail_encode)
    asset = {
        "asset_id": "missing_caption",
        "asset_kind": "page_image",
        "image_path": "pptx_images/missing_caption.png",
        "query": "verbose fallback query must only go to review",
        "strict_reuse_group": "C03_scene_decor_container",
        "file_name": "lesson.pptx",
    }

    report = image_db.write_ai_image_embedding_index({"assets": [asset]}, tmp_path)

    assert report["enabled"] is False
    assert report["reason"] == "empty_embedding_text"
    assert report["missing_caption_count"] == 1
    review_path = tmp_path / "ai_image_embedding_missing_caption_review.json"
    assert review_path.exists()
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    assert payload["missing_caption_count"] == 1
    assert payload["assets"][0]["asset_id"] == "missing_caption"
    assert payload["assets"][0]["query"] == "verbose fallback query must only go to review"
    assert any("embedding_missing_caption" in warning for warning in report["warnings"])


def test_embedding_sidecar_with_same_model_name_ignores_stale_path_identity(tmp_path, monkeypatch):
    local_model = tmp_path / "models" / "Qwen3-Embedding-0.6B"
    local_model.mkdir(parents=True)
    (local_model / "config.json").write_text('{"hidden_size": 1024}', encoding="utf-8")
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", str(local_model))

    import numpy as np

    np.savez_compressed(
        tmp_path / image_db.DEFAULT_EMBEDDING_INDEX_FILENAME,
        asset_ids=np.asarray(["asset_with_text"], dtype=str),
        vectors=np.asarray([[1.0, 0.0, 0.0]], dtype="float32"),
    )
    (tmp_path / image_db.DEFAULT_EMBEDDING_META_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": image_db.EMBEDDING_INDEX_SCHEMA_VERSION,
                "model": "/home/zsq/models/Qwen3-Embedding-0.6B",
                "model_identity": {
                    "kind": "local_path",
                    "name": "Qwen3-Embedding-0.6B",
                    "fingerprint": "different-model-content",
                },
                "asset_count": 1,
                "background_color_bias_asset_count": 0,
                "vector_dim": 3,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    match_index = {
        "assets": [
            {
                "asset_id": "asset_with_text",
                "asset_kind": "page_image",
                "image_path": "pptx_images/asset_with_text.png",
                "caption": "apple card illustration",
            }
        ],
    }
    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("same canonical model name should reuse sidecar despite stale path identity")

    monkeypatch.setattr(image_db, "write_ai_image_embedding_index", fail_rebuild)

    report = image_db._ensure_ai_image_embedding_index(match_index, tmp_path)

    assert report["enabled"] is True
    assert report["model"] == "Qwen3-Embedding-0.6B"


def test_transform_policy_uses_aspect_ratio_without_bucket_fields():
    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "4:3",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "content_prompt": "apple object card",
        "context_summary": "object recognition",
    }
    candidate = {
        **target,
        "asset_id": "candidate",
        "image_path": "ai_images/candidate.png",
        "aspect_bucket": "4:3",
    }

    details = _score_reuse_candidate_details(target, candidate)
    policy = details["transform_policy"]

    assert policy["candidate_aspect_ratio"] == "4:3"
    assert policy["target_aspect_ratio"] == "4:3"
    assert "candidate_aspect_bucket" not in policy
    assert "target_aspect_bucket" not in policy


def test_transform_policy_uses_transparent_pad_for_allowed_cross_aspect_pair():
    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "4:3",
        "subject": "\u8bed\u6587",
        "grade_norm": "\u4e94\u5e74\u7ea7",
        "grade_band": "\u9ad8\u5e74\u7ea7",
        "content_prompt": "apple object card",
        "context_summary": "object recognition",
    }
    candidate = {
        **target,
        "asset_id": "candidate",
        "image_path": "ai_images/candidate.png",
        "aspect_ratio": "16:9",
        "actual_width": 160,
        "actual_height": 90,
    }

    details = _score_reuse_candidate_details(target, candidate)
    policy = details["transform_policy"]

    assert policy["decision"] == "accept"
    assert policy["mode"] == "transparent_pad"
    assert policy["candidate_aspect_ratio"] == "16:9"
    assert policy["target_aspect_ratio"] == "4:3"
    assert policy["crop_loss"] > 0


def test_materialize_reuse_writes_target_sized_transparent_pad(tmp_path):
    source = tmp_path / "library.png"
    dest = tmp_path / "session" / "materials" / "hit.png"
    Image.new("RGBA", (160, 90), (220, 20, 10, 255)).save(source)

    materialize_reused_ai_image_asset(
        session_dir=tmp_path / "session",
        session_image_path=dest,
        match={
            "reuse_asset_id": "asset",
            "candidate_image_path": str(source),
            "transform_policy": {
                "decision": "accept",
                "mode": "transparent_pad",
                "candidate_aspect_ratio": "16:9",
                "target_aspect_ratio": "4:3",
                "target_width": 120,
                "target_height": 90,
            },
        },
    )

    with Image.open(dest) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.size == (120, 90)
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((60, 45)) == (220, 20, 10, 255)


def test_split_indexes_collapse_legacy_skip_groups_and_read_back_without_c00(tmp_path):
    match_index = {
        "schema_version": 14,
        "asset_root": str(tmp_path),
        "assets": [
            {"asset_id": "text", "asset_kind": "page_image", "strict_reuse_group": "C00_strict_text_problem_skip"},
            {"asset_id": "subject", "asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object"},
            {"asset_id": "skip", "asset_kind": "page_image", "strict_reuse_group": "C00_strict_text_problem_skip"},
        ],
    }

    split_dir = write_ai_image_split_match_indexes(match_index, tmp_path)
    skip_payload = json.loads((split_dir / "C00_strict_text_problem_skip.json").read_text(encoding="utf-8"))
    merged, source_dir = read_ai_image_split_match_index(tmp_path)

    assert source_dir == split_dir
    assert (split_dir / "C00_strict_text_problem_skip.json").exists()
    assert {asset["asset_id"] for asset in skip_payload["assets"]} == {"text", "skip"}
    assert {asset["strict_reuse_group"] for asset in skip_payload["assets"]} == {"C00_strict_text_problem_skip"}
    assert {asset["asset_id"] for asset in merged["assets"]} == {"subject"}


def test_c00_split_index_accumulates_across_runs(tmp_path):
    """M-4: 逐个 PPTX 入库时 C00.json 必须跨 run 累积，而非被整体覆盖丢失。"""

    def _run(asset_id: str, caption: str) -> None:
        write_ai_image_split_match_indexes(
            {
                "schema_version": 14,
                "asset_root": str(tmp_path),
                "assets": [
                    {
                        "asset_id": asset_id,
                        "asset_kind": "page_image",
                        "strict_reuse_group": "C00_strict_text_problem_skip",
                        "original_image_path": f"skip_images/{asset_id}_original.png",
                        "caption": caption,
                    }
                ],
            },
            tmp_path,
        )

    _run("deck1_problem", "first")
    _run("deck2_problem", "second")
    # 第三次重新入库 deck1（caption 更新）：current run 应覆盖同 id，旧 deck2 保留
    _run("deck1_problem", "first_updated")

    split_dir = tmp_path / image_db.STRICT_REUSE_INDEX_DIRNAME
    payload = json.loads((split_dir / "C00_strict_text_problem_skip.json").read_text(encoding="utf-8"))
    by_id = {a["asset_id"]: a for a in payload["assets"]}
    assert set(by_id) == {"deck1_problem", "deck2_problem"}
    assert by_id["deck1_problem"]["caption"] == "first_updated"  # current run wins
    assert by_id["deck2_problem"]["caption"] == "second"  # earlier run preserved


def test_split_indexes_write_gapless_active_group_names(tmp_path):
    index = {
        "schema_version": 14,
        "asset_root": str(tmp_path),
        "assets": [
            {"asset_id": "subject", "asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object"},
            {"asset_id": "scene", "asset_kind": "page_image", "strict_reuse_group": "C03_scene_decor_container"},
            {
                "asset_id": "invalid_subject",
                "asset_kind": "page_image",
                "strict_reuse_group": "not_a_current_material_category",
            },
            {"asset_id": "invalid_scene", "asset_kind": "page_image", "strict_reuse_group": "not_a_current_material_category"},
        ],
        "skip_reuse_assets": [
            {"asset_id": "skip", "asset_kind": "page_image", "strict_reuse_group": "C00_strict_text_problem_skip"}
        ],
    }

    split_dir = write_ai_image_split_match_indexes(index, tmp_path)

    assert (split_dir / "C00_strict_text_problem_skip.json").exists()
    assert (split_dir / "C02_generic_subject_object.json").exists()
    assert (split_dir / "C03_scene_decor_container.json").exists()
    assert not (split_dir / "not_a_current_material_category.json").exists()
    assert not (split_dir / "not_a_current_material_category.json").exists()

    subject_payload = json.loads((split_dir / "C02_generic_subject_object.json").read_text(encoding="utf-8"))
    assert [asset["asset_id"] for asset in subject_payload["assets"]] == ["subject"]

    scene_payload = json.loads((split_dir / "C03_scene_decor_container.json").read_text(encoding="utf-8"))
    assert [asset["asset_id"] for asset in scene_payload["assets"]] == ["scene", "invalid_subject", "invalid_scene"]


def test_match_index_preserves_secondary_reuse_caption():
    asset = {
        "asset_id": "lm",
        "asset_kind": "page_image",
        "image_path": "x.png",
        "aspect_ratio": "16:9",
        "caption": "西湖晴天湖景",
        "strict_reuse_group": "C01_irreplaceable_entity_event_action",
        "strict_reuse_secondary_group": "C03_scene_decor_container",
        "secondary_reuse_caption": "晴天湖景",
    }
    out = _normalize_asset_for_match(asset)
    assert out["secondary_reuse_caption"] == "晴天湖景"
    assert out["strict_reuse_secondary_group"] == "C03_scene_decor_container"


def test_c01_landmark_dual_writes_denamed_projection_into_c03(tmp_path):
    match_index = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "lm",
                "asset_kind": "page_image",
                "image_path": "x.png",
                "aspect_ratio": "16:9",
                "caption": "西湖晴天湖景",
                "strict_reuse_group": "C01_irreplaceable_entity_event_action",
                "strict_reuse_secondary_group": "C03_scene_decor_container",
                "secondary_reuse_caption": "晴天湖景",
            }
        ],
    }
    write_ai_image_split_match_indexes(match_index, tmp_path)
    split = tmp_path / "strict_reuse_indexes"
    c01 = json.loads((split / "C01_irreplaceable_entity_event_action.json").read_text(encoding="utf-8"))
    c03 = json.loads((split / "C03_scene_decor_container.json").read_text(encoding="utf-8"))
    a01 = next(a for a in c01["assets"] if a["asset_id"] == "lm")
    assert a01["caption"] == "西湖晴天湖景"
    assert "secondary_projection" not in a01
    a03 = next(a for a in c03["assets"] if a["asset_id"] == "lm")
    assert a03["caption"] == "晴天湖景"
    assert a03["strict_reuse_group"] == "C03_scene_decor_container"
    assert a03["secondary_projection"] is True
    assert a03["secondary_projection_of"] == "lm"
    assert "strict_reuse_secondary_group" not in a03


def test_read_split_skips_secondary_projection_keeps_canonical_c01(tmp_path):
    match_index = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "lm",
                "asset_kind": "page_image",
                "image_path": "x.png",
                "aspect_ratio": "16:9",
                "caption": "西湖晴天湖景",
                "strict_reuse_group": "C01_irreplaceable_entity_event_action",
                "strict_reuse_secondary_group": "C03_scene_decor_container",
                "secondary_reuse_caption": "晴天湖景",
            }
        ],
    }
    write_ai_image_split_match_indexes(match_index, tmp_path)
    db, _ = read_ai_image_split_match_index(tmp_path)
    matches = [a for a in db["assets"] if a["asset_id"] == "lm"]
    assert len(matches) == 1
    assert matches[0]["strict_reuse_group"] == "C01_irreplaceable_entity_event_action"
    assert matches[0]["caption"] == "西湖晴天湖景"


def test_write_match_index_retains_c00_split_file_but_excludes_c00_from_matching(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    for name in ("skip", "keep"):
        (image_dir / f"{name}.png").write_bytes(name.encode("ascii"))
    db = {
        "schema_version": 1,
        "assets": [
            _asset("skip", "C00_strict_text_problem_skip", prompt="batch exact text card"),
            _asset("keep", "C02_generic_subject_object", prompt="single apple card"),
        ],
    }

    index, split_dir = write_ai_image_match_index(db, tmp_path, write_embedding_index=False)

    c00_payload = json.loads((split_dir / "C00_strict_text_problem_skip.json").read_text(encoding="utf-8"))
    assert [asset["asset_id"] for asset in c00_payload["assets"]] == ["skip"]
    assert [asset["asset_id"] for asset in index["assets"]] == ["keep"]
    merged, _source_dir = read_ai_image_split_match_index(tmp_path)
    assert {asset["asset_id"] for asset in merged["assets"]} == {"keep"}


def test_write_match_index_persists_general_boolean_to_split_indexes(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    (image_dir / "keep.png").write_bytes(b"keep")
    db = {
        "schema_version": 1,
        "assets": [
            {
                **_asset("keep", "C02_generic_subject_object", prompt="blank speech bubble"),
                "general": True,
            },
        ],
    }

    index, split_dir = write_ai_image_match_index(db, tmp_path, write_embedding_index=False)

    payload = json.loads((split_dir / "C02_generic_subject_object.json").read_text(encoding="utf-8"))
    assert index["assets"][0]["general"] is True
    assert payload["assets"][0]["general"] is True


def test_query_embedding_cache_persists_to_configured_retrieve_dir(tmp_path, monkeypatch):
    import numpy as np

    library_dir = tmp_path / "library"
    image_dir = library_dir / "ai_images"
    image_dir.mkdir(parents=True)
    (image_dir / "candidate.png").write_bytes(b"candidate")
    target = {
        "asset_kind": "page_image",
        "caption": "cartoon apple card",
    }
    asset = {
        **_asset("candidate", "C02_generic_subject_object", prompt="cartoon apple card"),
        "caption": "cartoon apple card",
        "image_path": "ai_images/candidate.png",
    }
    embedding_index = {
        "asset_ids": ["candidate"],
        "vectors": np.asarray([[1.0, 0.0]], dtype="float32"),
    }
    cache_dir = tmp_path / "run" / "03_retrieve"
    encode_calls = []

    def fake_encode(texts, *, model_name=image_db.DEFAULT_EMBEDDING_MODEL, query=False):
        assert query is True
        encode_calls.append(list(texts))
        return np.asarray([[1.0, 0.0] for _text in texts], dtype="float32")

    monkeypatch.setattr("edupptx.reuse._embedding._encode_embedding_texts", fake_encode)

    first = image_db._rank_embedding_candidates(
        target,
        [asset],
        library_root=library_dir,
        embedding_index=embedding_index,
        limit=8,
        query_embedding_cache={},
        query_embedding_cache_dir=cache_dir,
    )

    assert [row["asset"]["asset_id"] for row in first] == ["candidate"]
    assert encode_calls == [["cartoon apple card"]]
    assert (cache_dir / "ai_image_query_embedding_cache.npz").exists()
    assert (cache_dir / "ai_image_query_embedding_cache_meta.json").exists()

    def fail_encode(*_args, **_kwargs):
        raise AssertionError("query embedding should have been loaded from disk")

    monkeypatch.setattr("edupptx.reuse._embedding._encode_embedding_texts", fail_encode)

    second = image_db._rank_embedding_candidates(
        target,
        [asset],
        library_root=library_dir,
        embedding_index=embedding_index,
        limit=8,
        query_embedding_cache={},
        query_embedding_cache_dir=cache_dir,
    )

    assert [row["asset"]["asset_id"] for row in second] == ["candidate"]
    assert second[0]["embedding_score"] == first[0]["embedding_score"]


def test_rank_embedding_candidates_query_failure_records_status(tmp_path, monkeypatch):
    """H-1: query-side encode failure must surface (status_sink), not silently return []."""
    import numpy as np

    image_db._EMBEDDING_QUERY_FAILURE_WARNED = False
    target = {"asset_kind": "page_image", "caption": "cartoon apple card"}
    asset = {
        **_asset("candidate", "C02_generic_subject_object", prompt="cartoon apple card"),
        "caption": "cartoon apple card",
        "image_path": "ai_images/candidate.png",
    }
    embedding_index = {
        "asset_ids": ["candidate"],
        "vectors": np.asarray([[1.0, 0.0]], dtype="float32"),
    }

    def boom_encode(*_args, **_kwargs):
        raise RuntimeError("model not found on this host")

    monkeypatch.setattr("edupptx.reuse._embedding._encode_embedding_texts", boom_encode)

    status: dict = {"enabled": True}
    result = image_db._rank_embedding_candidates(
        target,
        [asset],
        library_root=tmp_path,
        embedding_index=embedding_index,
        limit=8,
        query_embedding_cache={},
        status_sink=status,
    )

    assert result == []
    assert status["query_encode_failed"] is True
    assert status["reason"] == "embedding_query_failed"
    assert "RuntimeError" in status["query_encode_error"]


def test_match_index_preserves_ppt_vlm_llm_comparison_fields():
    db = {
        "schema_version": 10,
        "assets": [
            {
                "asset_id": "ppt_compare",
                "asset_kind": "page_image",
                "image_path": "pptx_images/ppt_compare.png",
                "aspect_ratio": "1:1",
                "query": "plain classroom icon",
                "caption": "LLM caption",
                "vlm_caption": "VLM caption",
                "vlm_general": True,
                "llm_general": False,
                "general": False,
                "context_summary": "VLM context",
                "teaching_intent": "VLM intent",
                "strict_reuse_group": "C02_generic_subject_object",
                "strict_reuse_confidence": 0.91,
                "strict_reuse_reason": "LLM says subject object",
                "visual_reuse_group": "C03_scene_decor_container",
                "visual_reuse_confidence": 0.82,
                "visual_reuse_reason": "VLM says scene",
            }
        ],
    }

    index = build_ai_image_match_index(db)

    asset = index["assets"][0]
    assert asset["vlm_caption"] == "VLM caption"
    assert asset["vlm_general"] is True
    assert asset["llm_general"] is False
    assert asset["general"] is False
    assert asset["visual_reuse_group"] == "C03_scene_decor_container"
    assert asset["strict_reuse_group"] == "C02_generic_subject_object"


def test_split_indexes_write_backgrounds_to_dedicated_json(tmp_path):
    match_index = {
        "schema_version": 14,
        "asset_root": str(tmp_path),
        "assets": [
            {
                "asset_id": "background",
                "asset_kind": "background",
                "strict_reuse_group": "C03_scene_decor_container",
                "image_path": "ai_images/background.png",
                "aspect_ratio": "16:9",
                "normalized_prompt": "light paper texture",
                "context_summary": "quiet classroom background",
            },
            {
                "asset_id": "scene",
                "asset_kind": "page_image",
                "strict_reuse_group": "C03_scene_decor_container",
                "image_path": "ai_images/scene.png",
                "aspect_ratio": "16:9",
                "content_prompt": "classroom activity scene",
                "context_summary": "generic classroom scene",
            },
        ],
    }

    split_dir = write_ai_image_split_match_indexes(match_index, tmp_path)
    background_payload = json.loads((split_dir / "background.json").read_text(encoding="utf-8"))
    general_payload = json.loads((split_dir / "C03_scene_decor_container.json").read_text(encoding="utf-8"))
    merged, source_dir = read_ai_image_split_match_index(tmp_path)

    assert source_dir == split_dir
    assert [asset["asset_id"] for asset in background_payload["assets"]] == ["background"]
    assert all(asset["asset_kind"] == "background" for asset in background_payload["assets"])
    assert {asset["asset_id"] for asset in general_payload["assets"]} == {"scene"}
    assert {asset["asset_id"] for asset in merged["assets"]} == {"background", "scene"}


def test_background_reuse_routes_to_background_split(tmp_path):
    split_dir = write_ai_image_split_match_indexes(
        {
            "schema_version": 14,
            "asset_root": str(tmp_path),
            "assets": [
                {
                    "asset_id": "background",
                    "asset_kind": "background",
                    "strict_reuse_group": "C03_scene_decor_container",
                    "image_path": "ai_images/background.png",
                    "aspect_ratio": "16:9",
                    "normalized_prompt": "light paper texture",
                    "context_summary": "quiet classroom background",
                },
                {
                    "asset_id": "scene",
                    "asset_kind": "page_image",
                    "strict_reuse_group": "C03_scene_decor_container",
                    "image_path": "ai_images/scene.png",
                    "aspect_ratio": "16:9",
                    "content_prompt": "classroom activity scene",
                    "context_summary": "generic classroom scene",
                },
            ],
        },
        tmp_path,
    )
    merged, _source_dir = read_ai_image_split_match_index(tmp_path)

    route = _route_match_index_for_target(
        tmp_path,
        merged,
        split_dir,
        {"asset_kind": "background", "strict_reuse_group": "C03_scene_decor_container"},
    )

    assert route is not None
    routed_index, routed_path, routed_assets, route_group = route
    assert routed_path.name == "background.json"
    assert route_group == "background"
    assert [asset["asset_id"] for asset in routed_assets] == ["background"]
    assert routed_index["strict_reuse_group"] == "background"


def test_current_split_backgrounds_are_read_and_rewritten_to_background_json(tmp_path):
    split_dir = tmp_path / "strict_reuse_indexes"
    split_dir.mkdir()
    (split_dir / "C03_scene_decor_container.json").write_text(
        json.dumps(
            {
                "schema_version": 14,
                "strict_reuse_group": "C03_scene_decor_container",
                "asset_root": str(tmp_path),
                "assets": [
                    {
                        "asset_id": "page_background",
                        "asset_kind": "background",
                        "strict_reuse_group": "C03_scene_decor_container",
                        "image_path": "ai_images/background.png",
                        "normalized_prompt": "light paper texture",
                    },
                    {
                        "asset_id": "scene",
                        "asset_kind": "page_image",
                        "strict_reuse_group": "C03_scene_decor_container",
                        "image_path": "ai_images/scene.png",
                        "content_prompt": "generic classroom scene",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    merged, source_dir = read_ai_image_split_match_index(tmp_path)
    rewritten_dir = write_ai_image_split_match_indexes(merged, tmp_path)
    background_payload = json.loads((rewritten_dir / "background.json").read_text(encoding="utf-8"))
    general_payload = json.loads((rewritten_dir / "C03_scene_decor_container.json").read_text(encoding="utf-8"))

    assert source_dir == split_dir
    assert {asset["asset_id"] for asset in merged["assets"]} == {"page_background", "scene"}
    assert [asset["asset_id"] for asset in background_payload["assets"]] == ["page_background"]
    assert {asset["asset_id"] for asset in general_payload["assets"]} == {"scene"}


def test_review_accept_threshold_is_single_cross_category_value():
    assert _reuse_review_accept_score_threshold({"asset_kind": "page_image", "strict_reuse_group": "C03_scene_decor_container"}) == 0.60
    assert _reuse_review_accept_score_threshold({"asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object"}) == 0.60
    assert _reuse_review_accept_score_threshold({"asset_kind": "page_image", "strict_reuse_group": "C01_irreplaceable_entity_event_action"}) == 0.60
    assert _reuse_review_accept_score_threshold({"asset_kind": "page_image", "strict_reuse_group": "C00_strict_text_problem_skip"}) == 0.60
    assert _reuse_review_accept_score_threshold(
        {"asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object"},
        policy_result={"llm_accept_threshold_override": 0.66},
    ) == 0.66


def test_reuse_gate_profiles_follow_current_four_material_categories():
    assert _reuse_gate_profile({"asset_kind": "page_image", "strict_reuse_group": "C03_scene_decor_container"}) == "loose"
    assert _reuse_gate_profile({"asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object"}) == "medium"
    assert _reuse_gate_profile({"asset_kind": "page_image", "strict_reuse_group": "C01_irreplaceable_entity_event_action"}) == "strict_knowledge"
    assert _reuse_gate_profile({"asset_kind": "page_image", "strict_reuse_group": "C00_strict_text_problem_skip"}) == "medium"
    assert _reuse_gate_profile({"asset_kind": "page_image", "strict_reuse_group": "C00_strict_text_problem_skip"}) == "medium"


def test_reuse_gate_thresholds_are_single_cross_ppt_values():
    loose_target = {"asset_kind": "page_image", "strict_reuse_group": "C03_scene_decor_container"}
    medium_target = {"asset_kind": "page_image", "strict_reuse_group": "C02_generic_subject_object"}
    strict_target = {"asset_kind": "page_image", "strict_reuse_group": "C01_irreplaceable_entity_event_action"}

    assert _reuse_gate_thresholds_for_target(loose_target) == PAGE_IMAGE_REUSE_GATE_THRESHOLDS["loose"]
    assert _reuse_gate_thresholds_for_target(medium_target) == PAGE_IMAGE_REUSE_GATE_THRESHOLDS["medium"]
    assert _reuse_gate_thresholds_for_target(strict_target) == PAGE_IMAGE_REUSE_GATE_THRESHOLDS["strict_knowledge"]
    assert _reuse_gate_thresholds_for_target({"asset_kind": "background"}) == BACKGROUND_REUSE_GATE_THRESHOLDS
    assert PAGE_IMAGE_REUSE_GATE_THRESHOLDS["loose"]["keyword_min"] == 0.0
    assert PAGE_IMAGE_REUSE_GATE_THRESHOLDS["medium"]["keyword_min"] == 0.0
    assert BACKGROUND_REUSE_GATE_THRESHOLDS["keyword_min"] == 0.0


def test_keyword_prompt_requests_llm_subject_and_grade_enums():
    messages = _build_keyword_messages(
        [
            {
                "asset_id": "asset_grade",
                "asset_kind": "page_image",
                "theme": "五年级语文《刷子李》",
                "content_prompt": "刷子李人物插画",
                "subject": "小学语文",
                "subject_hint": "小学语文",
                "grade_hint": "五年级",
                "grade_norm": "其他",
                "grade_band": "其他",
            }
        ]
    )

    system = messages[0]["content"]

    assert "grade_norm" in system
    assert "grade_band" in system
    assert "subject" in system
    assert "语文、数学、物理、其他" in system
    assert "一年级、二年级、三年级、四年级、五年级、六年级、七年级、八年级、九年级、高一、高二、高三、其他" in system
    assert "低年级、高年级、其他" in system
    assert "subject 必须" in system
    assert "grade_norm 必须" in system
    assert "grade_band 必须" in system
    assert "subject_hint" in messages[1]["content"]
    assert "grade_hint" in messages[1]["content"]
    assert "必须只返回严格 JSON" in system
    assert "must be exactly" not in system
    assert "Return strict JSON only" not in system


def test_keyword_prompt_requests_general_boolean_and_rules():
    messages = _build_keyword_messages(
        [
            {
                "asset_id": "asset_general",
                "asset_kind": "page_image",
                "theme": "通用课堂素材",
                "content_prompt": "带装饰的空白对话气泡贴纸",
                "subject": "其他",
                "subject_hint": "其他",
                "grade_hint": "五年级",
                "grade_norm": "其他",
                "grade_band": "其他",
            }
        ]
    )

    system = messages[0]["content"]

    assert "general" in system
    assert "general 必须是布尔值" in system
    # GENERAL_RULE injected: ordered strong-false decision rules present.
    assert "强-false（命中任一即 false）" in system
    assert "判定顺序：先查强-false" in system
    assert "具名或故事身份" in system
    # NOTE: GENERAL_RULE's exact example wording lives in general_rules.py and is
    # covered there; this test only verifies the keyword prompt injects the rule.


def test_subject_normalization_accepts_only_current_chinese_enums():
    assert _normalize_subject_value("语文") == "语文"
    assert _normalize_subject_value("数学") == "数学"
    assert _normalize_subject_value("物理") == "物理"
    assert _normalize_subject_value("小学语文") == "其他"
    assert _normalize_subject_value("math") == "其他"
    assert _normalize_subject_value("") == "其他"


def test_subject_scope_allows_explicit_general_cross_subject():
    assert _subject_scope_compatible(
        {"subject": "语文"},
        {"subject": "数学", "general": True},
    )


def test_subject_scope_rejects_explicit_non_general_cross_subject():
    assert not _subject_scope_compatible(
        {"subject": "语文"},
        {"subject": "数学", "general": False},
    )


def test_subject_scope_allows_legacy_other_missing_general():
    decision = _subject_scope_decision(
        {"subject": "语文"},
        {"subject": "其他"},
    )

    assert decision["compatible"] is True
    assert decision["subject_filter_mode"] == "subject_other_default"
    assert decision["general_defaulted_from_subject_other"] is True


def test_subject_scope_unknown_target_only_allows_general():
    assert _subject_scope_compatible(
        {"subject": "其他"},
        {"subject": "数学", "general": True},
    )
    assert not _subject_scope_compatible(
        {"subject": "其他"},
        {"subject": "数学", "general": False},
    )


def test_reuse_debug_asset_payload_includes_general():
    payload = _reuse_debug_asset_payload(
        {
            "asset_id": "asset_general",
            "asset_kind": "page_image",
            "content_prompt": "带装饰的空白对话气泡贴纸",
            "subject": "其他",
            "general": True,
        }
    )

    assert payload["general"] is True


def test_hard_filter_allows_legacy_other_subject_after_subject_unknown_filter():
    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C03_scene_decor_container",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "aspect_ratio": "1:1",
    }
    candidate = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C03_scene_decor_container",
        "subject": "其他",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "aspect_ratio": "1:1",
    }

    assert _reuse_hard_filter_reject_reason(target, candidate) == ""


def test_hard_filter_treats_other_subject_as_general_even_when_general_false():
    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C03_scene_decor_container",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "aspect_ratio": "1:1",
    }
    candidate = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C03_scene_decor_container",
        "subject": "其他",
        "general": False,
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "aspect_ratio": "1:1",
    }

    assert _subject_scope_decision(target, candidate)["subject_filter_mode"] == "subject_other_default"
    assert _reuse_hard_filter_reject_reason(target, candidate) == ""


def test_hard_filter_still_rejects_known_cross_subject_when_non_general():
    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C03_scene_decor_container",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "aspect_ratio": "1:1",
    }
    candidate = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C03_scene_decor_container",
        "subject": "数学",
        "general": False,
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "aspect_ratio": "1:1",
    }

    assert _reuse_hard_filter_reject_reason(target, candidate) == "subject_mismatch"


def test_reuse_unknown_fields_ignore_grade_metadata_for_now():
    target = {"subject": "语文", "grade_norm": "其他", "grade_band": "其他"}
    candidate = {
        "subject": "语文",
        "grade_norm": "其他",
        "grade_band": "其他",
        "strict_reuse_group": "C02_generic_subject_object",
    }
    decision = _subject_scope_decision(target, candidate)

    assert _target_metadata_unknown_fields(target) == ["grade_norm", "grade_band"]
    assert _target_unknown_fields_for_reuse(target) == []
    assert _candidate_unknown_fields_for_reuse(candidate, decision) == []


def test_apply_keyword_payload_uses_llm_grade_enums():
    asset = {
        "asset_id": "asset_grade",
        "asset_kind": "page_image",
        "image_path": "ai_images/asset_grade.png",
        "aspect_ratio": "1:1",
        "content_prompt": "刷子李人物插画",
        "theme": "人物描写课文插画",
        "subject": "其他",
        "grade_norm": "其他",
        "grade_band": "其他",
    }

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "asset_grade",
            "content_prompt": "刷子李人物插画",
            "context_summary": "课文人物插画",
            "teaching_intent": "帮助学生理解人物形象",
            "subject": "语文",
            "grade_norm": "五年级",
            "grade_band": "高年级",
        },
    )

    assert asset["subject"] == "语文"
    assert asset["grade_norm"] == "五年级"
    assert asset["grade_band"] == "高年级"


def test_apply_keyword_payload_preserves_deck_metadata_for_reuse_targets():
    asset = {
        "asset_id": "target_asset",
        "asset_kind": "page_image",
        "image_path": "",
        "aspect_ratio": "1:1",
        "content_prompt": "刷子李人物插画",
        "theme": "八年级语文课",
        "subject": "语文",
        "grade_norm": "八年级",
        "grade_band": "高年级",
    }

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "target_asset",
            "caption": "人物插画",
            "context_summary": "人物描写课文插画",
            "teaching_intent": "理解人物形象",
            "subject": "数学",
            "grade_norm": "三年级",
            "grade_band": "低年级",
            "general": False,
            "strict_reuse_group": "C02_generic_subject_object",
        },
        include_match_keywords=True,
    )

    assert asset["subject"] == "语文"
    assert asset["grade_norm"] == "八年级"
    assert asset["grade_band"] == "高年级"
    assert asset["strict_reuse_group"] == "C02_generic_subject_object"
    assert asset["caption"] == "人物插画"


def test_apply_keyword_payload_persists_boolean_general():
    asset = {
        "asset_id": "asset_general",
        "asset_kind": "page_image",
        "image_path": "ai_images/asset_general.png",
        "aspect_ratio": "1:1",
        "content_prompt": "带装饰的空白对话气泡贴纸",
        "theme": "通用课堂素材",
        "subject": "其他",
        "grade_norm": "其他",
        "grade_band": "其他",
    }

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "asset_general",
            "content_prompt": "带装饰的空白对话气泡贴纸",
            "context_summary": "空白气泡贴纸用于课堂展示",
            "teaching_intent": "承载可替换文字内容",
            "subject": "其他",
            "grade_norm": "其他",
            "grade_band": "其他",
            "general": True,
        },
    )

    assert asset["general"] is True


def test_apply_keyword_payload_ignores_non_boolean_general():
    asset = {
        "asset_id": "asset_invalid_general",
        "asset_kind": "page_image",
        "image_path": "ai_images/asset_invalid_general.png",
        "aspect_ratio": "1:1",
        "content_prompt": "米字格中的汉字“你”",
        "theme": "识字",
        "subject": "语文",
        "grade_norm": "一年级",
        "grade_band": "低年级",
    }

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "asset_invalid_general",
            "content_prompt": "米字格中的汉字“你”",
            "context_summary": "汉字书写示意图",
            "teaching_intent": "辅助识字书写",
            "subject": "语文",
            "grade_norm": "一年级",
            "grade_band": "低年级",
            "general": "false",
        },
    )

    assert "general" not in asset


def test_apply_keyword_payload_normalizes_invalid_or_missing_subject_to_other():
    asset = {
        "asset_id": "asset_invalid_subject",
        "asset_kind": "page_image",
        "image_path": "ai_images/asset_invalid_subject.png",
        "aspect_ratio": "1:1",
        "content_prompt": "数学函数图像",
        "subject": "数学",
        "grade_norm": "五年级",
        "grade_band": "高年级",
    }

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "asset_invalid_subject",
            "content_prompt": "数学函数图像",
            "context_summary": "函数教学图像",
            "teaching_intent": "辅助理解函数",
            "subject": "数学学科",
            "grade_norm": "五年级",
            "grade_band": "高年级",
        },
    )
    assert asset["subject"] == "其他"

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "asset_invalid_subject",
            "content_prompt": "数学函数图像",
            "context_summary": "函数教学图像",
            "teaching_intent": "辅助理解函数",
            "grade_norm": "五年级",
            "grade_band": "高年级",
        },
    )
    assert asset["subject"] == "其他"


def test_apply_keyword_payload_normalizes_invalid_grade_enums_to_other():
    asset = {
        "asset_id": "asset_invalid_grade",
        "asset_kind": "page_image",
        "image_path": "ai_images/asset_invalid_grade.png",
        "aspect_ratio": "1:1",
        "content_prompt": "泛用教学插画",
        "theme": "未指定年级主题",
    }

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "asset_invalid_grade",
            "content_prompt": "泛用教学插画",
            "context_summary": "通用教学插画",
            "teaching_intent": "辅助课堂讲解",
            "grade_norm": "小学高段",
            "grade_band": "中年级",
        },
    )

    assert asset["grade_norm"] == "其他"
    assert asset["grade_band"] == "其他"


def test_apply_keyword_payload_fills_missing_grade_fields_with_other():
    asset = {
        "asset_id": "asset_missing_grade",
        "asset_kind": "background",
        "image_path": "ai_images/asset_missing_grade.png",
        "aspect_ratio": "16:9",
        "content_prompt": "淡雅课堂背景",
    }

    _apply_keyword_payload(
        asset,
        {
            "asset_id": "asset_missing_grade",
            "normalized_prompt": "色调:柔和; 纹理:纸感; 明度:明亮; 构图:留白",
            "context_summary": "通用课堂背景",
            "teaching_intent": "承托页面内容",
        },
    )

    assert asset["grade_norm"] == "其他"
    assert asset["grade_band"] == "其他"


def test_grade_helpers_only_accept_llm_enums_without_text_mapping():
    assert infer_grade("五年级") == "五年级"
    assert infer_grade("高中二年级") == "其他"
    assert infer_grade_band("高年级") == "高年级"
    assert infer_grade_band("高中") == "其他"
    assert normalize_grade_info("五年级", "高年级") == {
        "grade_norm": "五年级",
        "grade_band": "高年级",
    }
    assert normalize_grade_info("初中二年级", "高中") == {
        "grade_norm": "其他",
        "grade_band": "其他",
    }


def test_target_metadata_unknown_fields_include_subject_and_grade_enums():
    assert _target_metadata_unknown_fields(
        {"subject": "其他", "grade_norm": "五年级", "grade_band": "高年级"}
    ) == ["subject"]
    assert _target_metadata_unknown_fields(
        {"subject": "语文", "grade_norm": "其他", "grade_band": "其他"}
    ) == ["grade_norm", "grade_band"]


class _KeywordClient:
    def __init__(self, payload: dict):
        self.payload = payload

    def chat_json(self, messages=None, *args, **kwargs):
        payload = dict(self.payload)
        if messages:
            data = json.loads(messages[-1]["content"].split("\n", 1)[1])
            payload["asset_id"] = data["assets"][0]["asset_id"]
        return {"assets": [payload]}


def test_target_reuse_preserves_deck_subject_and_writes_debug(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    (image_dir / "candidate.png").write_bytes(b"candidate")
    write_ai_image_split_match_indexes(
        {
            "schema_version": 14,
            "asset_root": str(tmp_path),
            "assets": [
                {
                    "asset_id": "candidate",
                    "asset_kind": "page_image",
                    "image_path": "ai_images/candidate.png",
                    "aspect_ratio": "1:1",
                    "subject": "语文",
                    "grade_norm": "五年级",
                    "grade_band": "高年级",
                    "content_prompt": "刷子李人物插画",
                    "context_summary": "人物描写课文插画",
                    "strict_reuse_group": "C02_generic_subject_object",
                }
            ],
        },
        tmp_path,
    )
    debug_path = tmp_path / "reuse_debug.json"

    match = find_reusable_ai_image_asset(
        library_dir=tmp_path,
        asset_kind="page_image",
        prompt="刷子李人物插画",
        theme="五年级语文《刷子李》",
        grade="五年级",
        subject="语文",
        aspect_ratio="1:1",
        keyword_client=_KeywordClient(
            {
                "asset_id": "target",
                "content_prompt": "刷子李人物插画",
                "context_summary": "人物描写课文插画",
                "teaching_intent": "理解人物形象",
                "subject": "其他",
                "grade_norm": "五年级",
                "grade_band": "高年级",
                "strict_reuse_group": "C02_generic_subject_object",
            }
        ),
        debug_path=debug_path,
    )

    assert match is None
    payload = json.loads(debug_path.read_text(encoding="utf-8"))
    query = payload["queries"][0]
    decision = query["decision"]
    assert decision["reason"] == "retrieval_no_candidate"
    assert "unknown_fields" not in decision
    assert query["candidate_scores"][0]["score_details"]["subject_filter"]["subject_filter_mode"] == "same_subject"


def test_reuse_routing_is_always_split(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    (image_dir / "candidate.png").write_bytes(b"candidate")
    write_ai_image_split_match_indexes(
        {
            "schema_version": 14,
            "asset_root": str(tmp_path),
            "assets": [
                {
                    "asset_id": "candidate",
                    "asset_kind": "page_image",
                    "image_path": "ai_images/candidate.png",
                    "aspect_ratio": "1:1",
                    "subject": "语文",
                    "grade_norm": "五年级",
                    "grade_band": "高年级",
                    "content_prompt": "红色苹果插画",
                    "context_summary": "识字课水果图",
                    "strict_reuse_group": "C02_generic_subject_object",
                }
            ],
        },
        tmp_path,
    )

    collection = find_reusable_ai_image_asset(
        library_dir=tmp_path,
        asset_kind="page_image",
        prompt="红色苹果插画",
        theme="五年级语文识字课",
        grade="五年级",
        subject="语文",
        aspect_ratio="1:1",
        keyword_client=_KeywordClient(
            {
                "asset_id": "target",
                "content_prompt": "红色苹果插画",
                "context_summary": "识字课水果图",
                "teaching_intent": "识别苹果",
                "subject": "语文",
                "grade_norm": "五年级",
                "grade_band": "高年级",
                "strict_reuse_group": "C02_generic_subject_object",
            }
        ),
        _collect_candidates_only=True,
    )

    route = collection["debug_record"]["reuse_group_route"]
    assert route["route_mode"] == "split"
    assert route["routed"] is True


def test_embedding_and_hybrid_text_use_current_fields_only():
    page = {
        "asset_kind": "page_image",
        "caption": "visible apple card",
        "query": "legacy query must not be retrieved",
        "content_prompt": "legacy content prompt must not be retrieved",
        "prompt": "legacy prompt must not be retrieved",
        "context_summary": "object recognition",
        "teaching_intent": "kept but not retrieved",
        "core_keywords": ["deleted"],
        "semantic_aliases": {"deleted": ["old"]},
        "constraints": [{"kind": "object", "value": "deleted", "importance": 2}],
        "context_summary_keywords": ["deleted context"],
    }
    background = {
        "asset_kind": "background",
        "content_prompt": "raw background prompt",
        "normalized_prompt": "light paper texture",
        "context_summary": "low noise background",
        "teaching_intent": "kept but not retrieved",
        "core_keywords": ["deleted"],
        "semantic_aliases": {"deleted": ["old"]},
        "context_summary_keywords": ["deleted context"],
    }

    for text in (_asset_embedding_text(page), _candidate_hybrid_text(page)):
        assert "visible apple card" in text
        assert "legacy query" not in text
        assert "legacy content prompt" not in text
        assert "legacy prompt" not in text
        assert "object recognition" not in text
        assert "kept but not retrieved" not in text
        assert "deleted" not in text

    no_caption_page = {
        "asset_kind": "page_image",
        "query": "legacy query must not be retrieved",
        "content_prompt": "legacy content prompt must not be retrieved",
        "prompt": "legacy prompt must not be retrieved",
    }
    assert _asset_embedding_text(no_caption_page) == ""
    assert _candidate_hybrid_text(no_caption_page) == ""

    for text in (_asset_embedding_text(background), _candidate_hybrid_text(background)):
        assert "light paper texture" in text
        assert "low noise background" not in text
        assert "raw background prompt" not in text
        assert "kept but not retrieved" not in text
        assert "deleted" not in text


def test_transparent_padding_saves_only_png_canvas(tmp_path):
    source = tmp_path / "wide.jpg"
    output = tmp_path / "library.png"
    Image.new("RGB", (200, 100), (255, 0, 0)).save(source)

    _save_reusable_png_with_transparent_padding(source, output, aspect_bucket="16:9")

    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.size == (200, 112)
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((100, 56))[3] == 255


def test_embed_rescue_floor_default_is_0_70():
    import importlib
    import edupptx.materials.ai_image_asset_db as db

    importlib.reload(db)
    assert db.EMBED_RESCUE_FLOOR == 0.70


def test_embed_rescue_floor_respects_env(monkeypatch):
    import importlib

    # EMBED_RESCUE_FLOOR 现定义在 reuse._decide（随裁决层迁出），reload 该模块以重读 env。
    monkeypatch.setenv("EDUPPTX_REUSE_EMBED_RESCUE_FLOOR", "0.60")
    import edupptx.reuse._decide as dec

    importlib.reload(dec)
    assert dec.EMBED_RESCUE_FLOOR == 0.60
    monkeypatch.delenv("EDUPPTX_REUSE_EMBED_RESCUE_FLOOR", raising=False)
    importlib.reload(dec)


def test_embedding_rescue_decision_rescues_high_embedding():
    from edupptx.materials.ai_image_asset_db import _embedding_rescue_decision

    assert _embedding_rescue_decision(embedding_score=0.70, transform_rejected=False) is True
    assert _embedding_rescue_decision(embedding_score=0.69, transform_rejected=False) is False


def test_embedding_rescue_decision_rejects_low_or_transform_or_missing():
    from edupptx.materials.ai_image_asset_db import _embedding_rescue_decision

    assert _embedding_rescue_decision(embedding_score=0.50, transform_rejected=False) is False
    assert _embedding_rescue_decision(embedding_score=0.90, transform_rejected=True) is False
    assert _embedding_rescue_decision(embedding_score=None, transform_rejected=False) is False


def _make_rescue_candidate(asset_id, embedding, keyword):
    return {
        "asset": {
            "asset_id": asset_id,
            "asset_kind": "page_image",
            "aspect_ratio": "16:9",
            "subject": "语文",
            "strict_reuse_group": "C02_generic_subject_object",
            "caption": "小学生学习",
            "query": "小学生坐在书桌前抄写课文",
        },
        "score_details": {
            "keyword_score": keyword,
            "embedding_score": embedding,
            "substring_score": 0.05,
        },
    }


_RESCUE_TARGET = {
    "asset_kind": "page_image",
    "aspect_ratio": "16:9",
    "subject": "语文",
    "strict_reuse_group": "C02_generic_subject_object",
    "caption": "小学生学习",
    "query": "小学生坐在书桌前抄写课文",
}


def test_high_embedding_candidate_is_rescued_to_llm_review():
    from edupptx.materials.ai_image_asset_db import _apply_reuse_policy_to_ranked_candidates

    candidates = [_make_rescue_candidate("kbpptx_hi", embedding=0.70, keyword=0.32)]
    _apply_reuse_policy_to_ranked_candidates(
        _RESCUE_TARGET,
        candidates,
        threshold=0.55,
        embedding_status={},
        df_ratio_lookup={},
        keyword_client=None,
        reuse_session_state=None,
        llm_review_enabled=True,
    )
    assert candidates[0]["reuse_policy"]["llm_review_required"] is True


def test_low_embedding_candidate_is_not_rescued():
    from edupptx.materials.ai_image_asset_db import _apply_reuse_policy_to_ranked_candidates

    candidates = [_make_rescue_candidate("kbpptx_lo", embedding=0.40, keyword=0.10)]
    _apply_reuse_policy_to_ranked_candidates(
        _RESCUE_TARGET,
        candidates,
        threshold=0.55,
        embedding_status={},
        df_ratio_lookup={},
        keyword_client=None,
        reuse_session_state=None,
        llm_review_enabled=True,
    )
    assert candidates[0]["reuse_policy"]["llm_review_required"] is False


def test_consistency_gate_exempts_high_embedding_page_image():
    from edupptx.materials.ai_image_asset_db import _embedding_keyword_gap_reject

    target = {
        "asset_kind": "page_image",
        "subject": "语文",
        "strict_reuse_group": "C02_generic_subject_object",
        "caption": "小学生学习",
        "query": "小学生抄写课文",
    }
    candidate_asset = {
        "asset_id": "kbpptx_x",
        "asset_kind": "page_image",
        "subject": "语文",
        "strict_reuse_group": "C02_generic_subject_object",
        "caption": "物理实验",
        "query": "显微镜观察细胞",
    }
    score_details = {"embedding_score": 0.80, "keyword_score": 0.05}
    assert _embedding_keyword_gap_reject(target, candidate_asset, score_details) is None


def test_consistency_gate_still_rejects_low_embedding_gap():
    from edupptx.materials.ai_image_asset_db import _embedding_keyword_gap_reject

    target = {
        "asset_kind": "page_image",
        "subject": "语文",
        "strict_reuse_group": "C02_generic_subject_object",
        "caption": "小学生学习",
        "query": "小学生抄写课文",
    }
    candidate_asset = {
        "asset_id": "kbpptx_y",
        "asset_kind": "page_image",
        "subject": "语文",
        "strict_reuse_group": "C02_generic_subject_object",
        "caption": "一只猫",
        "query": "一只橘猫睡觉",
    }
    score_details = {"embedding_score": 0.52, "keyword_score": 0.02}
    result = _embedding_keyword_gap_reject(target, candidate_asset, score_details)
    assert result is not None and result.get("decision") == "reject"


def test_reuse_debug_payload_includes_verbose_query():
    from edupptx.materials.ai_image_asset_db import _reuse_debug_asset_payload

    asset = {
        "asset_id": "kbpptx_q",
        "asset_kind": "page_image",
        "caption": "小学生学习",
        "query": "小学生坐在书桌前抄写课文",
        "subject": "语文",
        "aspect_ratio": "16:9",
    }
    payload = _reuse_debug_asset_payload(asset)
    assert payload["query"] == "小学生坐在书桌前抄写课文"
    assert payload["caption"] == "小学生学习"


def test_medium_profile_uses_single_review_accept_threshold():
    from edupptx.materials.ai_image_asset_db import _reuse_review_accept_score_threshold

    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
    }
    candidate = {
        "asset": {
            "asset_id": "kbpptx_m",
            "asset_kind": "page_image",
            "strict_reuse_group": "C02_generic_subject_object",
        }
    }
    assert _reuse_review_accept_score_threshold(target, candidate) == 0.60


def test_review_rules_reference_has_query_and_action_cap():
    from edupptx.materials.ai_image_asset_db import _load_reuse_review_score_rules_reference

    text = _load_reuse_review_score_rules_reference()
    assert "query" in text and "caption" in text
    assert "0.60" in text
    assert "主体-动作-对象" in text
