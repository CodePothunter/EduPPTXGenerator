import json

from edupptx.materials.ai_image_asset_db import (
    _route_match_text,
    _score_reuse_candidate_details,
    build_ai_image_asset_db,
    enrich_ai_image_asset_db_keywords,
    find_reusable_ai_image_asset,
    ingest_ai_image_asset_library_from_output,
    infer_grade,
    infer_grade_band,
    infer_subject,
    record_reused_ai_image_asset,
    update_ai_image_asset_library,
    write_ai_image_asset_db,
)


def test_infers_grade_and_subject_from_plan_context():
    assert infer_grade("七年级语文《秋天的怀念》课文教学", "七年级学生、初中语文教师") == "七年级"
    assert infer_subject("七年级语文《秋天的怀念》课文教学", "七年级学生、初中语文教师") == "语文"
    assert infer_grade_band("三年级") == "低年级"
    assert infer_grade_band("四年级") == "高年级"
    assert infer_grade_band("七年级") == "高年级"


def test_route_match_text_excludes_generation_only_prompt_terms():
    text = _route_match_text(
        {
            "style_prompt": "profile style role only page only wide layout quality only no text",
            "prompt_route": {
                "template_family": "lower",
                "profile_ids": ["profile_a"],
                "profile_prompt_terms": ["profile style"],
                "role_prompt_terms": ["role only"],
                "page_type_prompt_terms": ["page only"],
                "aspect_ratio_prompt_terms": ["wide layout"],
                "quality_terms": ["quality only"],
                "negative_terms": ["no text"],
            },
        }
    )

    assert "lower" in text
    assert "profile_a" in text
    assert "profile style" not in text
    assert "wide layout" not in text
    assert "role only" not in text
    assert "page only" not in text
    assert "quality only" not in text
    assert "no text" not in text


def test_reuse_score_keyword_signals_are_limited_to_core_keywords():
    target = {
        "asset_kind": "page_image",
        "reuse_scope": "visual_generic",
        "content_prompt": "",
        "normalized_prompt": "",
        "core_keywords": [],
        "main_entities": ["same entity"],
        "visual_actions": ["same action"],
        "scene_elements": ["same scene"],
        "context_keywords": ["same context"],
    }
    candidate = {
        "asset_kind": "page_image",
        "reuse_scope": "visual_generic",
        "content_prompt": "",
        "normalized_prompt": "",
        "core_keywords": [],
        "main_entities": ["same entity"],
        "visual_actions": ["same action"],
        "scene_elements": ["same scene"],
        "context_keywords": ["same context"],
    }

    details = _score_reuse_candidate_details(target, candidate)

    assert details["score"] == 0.0
    assert details["reject_reason"] == "no_content_match"

    target["core_keywords"] = ["author portrait"]
    candidate["content_prompt"] = "author portrait"

    details = _score_reuse_candidate_details(target, candidate)

    assert details["score"] > 0
    assert details["core_score"] == 1.0
    assert "main_entity_score" not in details


def test_semantic_aliases_score_by_concept_group():
    target = {
        "asset_kind": "page_image",
        "content_prompt": "",
        "normalized_prompt": "",
        "core_keywords": ["author portrait"],
        "semantic_aliases": {
            "author portrait": ["person portrait", "writer headshot", "author image"],
        },
    }
    candidate = {
        "asset_kind": "page_image",
        "content_prompt": "person portrait",
        "normalized_prompt": "",
    }

    details = _score_reuse_candidate_details(target, candidate)

    assert details["score"] > 0
    assert details["core_score"] == 1.0
    assert details["core_hits"][0]["concept"] == "author portrait"
    assert details["core_hits"][0]["matched_term"] == "person portrait"


def test_semantic_aliases_average_across_core_concepts():
    target = {
        "asset_kind": "page_image",
        "content_prompt": "",
        "normalized_prompt": "",
        "core_keywords": ["author portrait", "autumn chrysanthemum"],
        "semantic_aliases": {
            "author portrait": ["person portrait"],
            "autumn chrysanthemum": ["flower"],
        },
    }
    candidate = {
        "asset_kind": "page_image",
        "content_prompt": "person portrait",
        "normalized_prompt": "",
    }

    details = _score_reuse_candidate_details(target, candidate)

    assert 0.49 <= details["core_score"] <= 0.51
    assert details["missing_core_groups"] == ["autumn chrysanthemum"]


def test_content_bm25_uses_target_keywords_not_target_prompt_sentence():
    target = {
        "asset_kind": "page_image",
        "content_prompt": "cartoon tadpole holding a flag",
        "normalized_prompt": "cartoon tadpole holding a flag",
        "core_keywords": ["author portrait"],
        "semantic_aliases": {"author portrait": ["writer headshot"]},
    }
    candidate = {
        "asset_kind": "page_image",
        "content_prompt": "cartoon tadpole holding a flag",
        "normalized_prompt": "cartoon tadpole holding a flag",
    }

    details = _score_reuse_candidate_details(target, candidate)

    assert details["score"] == 0.0
    assert details["reject_reason"] == "no_content_match"
    assert details["content_score"] == 0.0
    assert details["missing_core_groups"] == ["author portrait"]


def test_context_score_uses_target_keywords_against_candidate_summary_sentence():
    target = {
        "asset_kind": "page_image",
        "content_prompt": "",
        "normalized_prompt": "",
        "core_keywords": ["author portrait"],
        "context_summary": "guide page illustration",
        "context_summary_keywords": ["profile"],
    }
    candidate = {
        "asset_kind": "page_image",
        "content_prompt": "author portrait",
        "normalized_prompt": "",
        "context_summary": "author profile slide support image",
        "context_summary_keywords": ["unrelated keyword"],
    }

    details = _score_reuse_candidate_details(target, candidate)

    assert details["score"] > 0
    assert details["target_context_summary_keywords"] == ["profile"]
    assert details["context_score"] > 0
    assert any(hit["target"] == "profile" for hit in details["context_hits"])


def test_context_score_does_not_use_target_summary_or_candidate_context_keywords():
    target = {
        "asset_kind": "page_image",
        "content_prompt": "",
        "normalized_prompt": "",
        "core_keywords": ["author portrait"],
        "context_summary": "guide page illustration",
        "context_summary_keywords": [],
    }
    candidate = {
        "asset_kind": "page_image",
        "content_prompt": "author portrait",
        "normalized_prompt": "",
        "context_summary": "plain support image",
        "context_summary_keywords": ["guide"],
    }

    details = _score_reuse_candidate_details(target, candidate)

    assert details["score"] > 0
    assert details["target_context_summary_keywords"] == []
    assert details["context_score"] == 0.0
    assert details["context_hits"] == []


def test_builds_ai_image_asset_db_from_output_sessions(tmp_path):
    output_root = tmp_path / "output"
    session_dir = output_root / "session_20260506_111550"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    (materials_dir / "background.png").write_bytes(b"bg")
    (materials_dir / "page_01_hero_1.png").write_bytes(b"hero")
    (materials_dir / "page_02_illustration_1.png").write_bytes(b"search")

    plan = {
        "meta": {
            "topic": "七年级语文《秋天的怀念》课文教学",
            "audience": "七年级学生、初中语文教师",
        },
        "visual": {
            "background_prompt": "淡雅秋日课堂背景",
        },
        "style_routing": {
            "template_family": "高年级",
        },
        "pages": [
            {
                "page_number": 1,
                "title": "秋天的怀念",
                "material_needs": {
                    "images": [
                        {
                            "query": "深秋北海公园菊花盛放的静谧场景",
                            "generation_prompt": "深秋北海公园菊花盛放的静谧场景，高年级编辑感风格",
                            "prompt_route": {
                                "template_family": "高年级",
                                "profile_ids": ["upper_grade_base"],
                                "profile_prompt_terms": ["高年级编辑感风格"],
                            },
                            "source": "ai_generate",
                            "role": "hero",
                            "aspect_ratio": "16:9",
                        }
                    ]
                },
            },
            {
                "page_number": 2,
                "title": "搜索图不会入库",
                "material_needs": {
                    "images": [
                        {
                            "query": "web search image",
                            "source": "search",
                            "role": "illustration",
                            "aspect_ratio": "4:3",
                        }
                    ]
                },
            },
        ],
    }
    (session_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    db = build_ai_image_asset_db(output_root)

    assert db["asset_count"] == 2
    prompts = {asset["content_prompt"] for asset in db["assets"]}
    assert prompts == {"淡雅秋日课堂背景", "深秋北海公园菊花盛放的静谧场景"}
    page_asset = next(asset for asset in db["assets"] if asset["asset_kind"] == "page_image")
    assert page_asset["content_prompt"] == "深秋北海公园菊花盛放的静谧场景"
    assert page_asset["generation_prompt"] == "深秋北海公园菊花盛放的静谧场景，高年级编辑感风格"
    assert page_asset["prompt_route"]["profile_ids"] == ["upper_grade_base"]
    for asset in db["assets"]:
        assert "prompt" not in asset
        assert asset["theme"] == "七年级语文《秋天的怀念》课文教学"
        assert asset["grade"] == "七年级"
        assert asset["subject"] == "语文"
        assert asset["image_path"].startswith("session_20260506_111550/materials/")
        assert asset["source"]["session_id"] == "session_20260506_111550"


def test_default_context_summary_describes_slide_function_not_visible_query(tmp_path):
    output_root = tmp_path / "output"
    session_dir = output_root / "session_20260506_111550"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    (materials_dir / "page_02_illustration_1.png").write_bytes(b"image")

    plan = {
        "meta": {"topic": "lesson", "audience": "grade"},
        "pages": [
            {
                "page_number": 2,
                "page_type": "toc",
                "title": "Learning Path",
                "material_needs": {
                    "images": [
                        {
                            "query": "cartoon tadpole holding a flag",
                            "source": "ai_generate",
                            "role": "illustration",
                            "aspect_ratio": "3:4",
                        }
                    ]
                },
            }
        ],
    }
    (session_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    db = build_ai_image_asset_db(output_root)

    asset = db["assets"][0]
    assert asset["context_summary"].startswith("Learning Path")
    assert "学习路径" in asset["context_summary"]
    assert "cartoon tadpole holding a flag" not in asset["context_summary"]


def test_write_ai_image_asset_db_defaults_to_output_root(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()

    db, target = write_ai_image_asset_db(output_root)

    assert target == output_root / "ai_image_asset_db.json"
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["asset_count"] == db["asset_count"]


def test_enriches_asset_db_keywords_with_llm_payload():
    class FakeKeywordClient:
        _model = "fake-keyword-model"

        def chat_json(self, messages, **kwargs):
            assert any(term in messages[1]["content"] for term in ("史铁生肖像", "汉字拼音标注"))
            return {
                "assets": [
                    {
                        "asset_id": "aiimg_author",
                        "normalized_prompt": "史铁生肖像",
                        "context_summary": "作者背景页的史铁生肖像素材",
                        "reuse_scope": "course_specific",
                        "specificity_score": 5,
                        "core_keywords": ["史铁生", "肖像", "插画"],
                        "context_keywords": ["七年级语文", "秋天的怀念", "作者介绍"],
                        "style_keywords": ["线条简洁", "插画"],
                    },
                    {
                        "asset_id": "aiimg_pinyin",
                        "normalized_prompt": "汉字拼音标注教学示意",
                        "context_summary": "生字词正音页的汉字拼音教学示意",
                        "reuse_scope": "subject_generic",
                        "specificity_score": 2,
                        "core_keywords": ["汉字", "拼音标注", "教学示意"],
                        "context_keywords": ["七年级语文", "秋天的怀念", "生字词正音"],
                        "style_keywords": ["简洁清晰"],
                    }
                ]
            }

    db = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "aiimg_author",
                "asset_kind": "page_image",
                "image_path": "session/materials/page_04_illustration_1.png",
                "prompt": "史铁生肖像插画，编辑感风格，线条简洁",
                "theme": "七年级语文《秋天的怀念》课文教学",
                "grade": "七年级",
                "subject": "语文",
                "source": {"page_title": "作者与背景介绍"},
            },
            {
                "asset_id": "aiimg_pinyin",
                "asset_kind": "page_image",
                "image_path": "session/materials/page_07_illustration_1.png",
                "prompt": "汉字拼音标注教学示意图，编辑感风格，简洁清晰",
                "theme": "七年级语文《秋天的怀念》课文教学",
                "grade": "七年级",
                "subject": "语文",
                "source": {"page_title": "生字词正音"},
            },
        ],
        "warnings": [],
    }

    enrich_ai_image_asset_db_keywords(db, FakeKeywordClient(), batch_size=1)

    author = db["assets"][0]
    pinyin = db["assets"][1]
    assert db["schema_version"] == 5
    assert db["keyword_builder"]["method"] == "llm_reuse_metadata_extraction"
    assert db["keyword_builder"]["model"] == "fake-keyword-model"
    assert "reuse_scope" not in author
    assert author["context_summary"] == "作者背景页的史铁生肖像素材"
    assert "specificity_score" not in author
    assert author["core_keywords"] == ["史铁生", "肖像"]
    assert author["semantic_aliases"] == {}
    assert "page_title" not in author.get("source", {})
    assert "context_keywords" not in author
    assert "style_keywords" not in author
    assert "match_key" not in author

    assert "reuse_scope" not in pinyin
    assert pinyin["context_summary"] == "生字词正音页的汉字拼音教学示意"
    assert pinyin["core_keywords"] == ["汉字", "拼音标注", "教学示意"]
    assert "context_keywords" not in pinyin
    assert "match_key" not in pinyin


def test_keyword_enrichment_uses_same_schema_and_unions_terms():
    class FakeKeywordClient:
        _model = "fake-keyword-model"

        def chat_json(self, messages, **kwargs):
            assert "context_summary_keywords" in messages[0]["content"]
            assert "generation_prompt" not in messages[1]["content"]
            return {
                "assets": [
                    {
                        "asset_id": "asset_author",
                        "normalized_prompt": "author portrait",
                        "context_summary": "author profile image",
                        "teaching_intent": "show the author visually",
                        "core_keywords": ["portrait"],
                        "semantic_aliases": {"portrait": ["headshot"]},
                        "context_summary_keywords": ["profile"],
                    }
                ]
            }

    db = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "asset_author",
                "asset_kind": "page_image",
                "image_path": "session/materials/page_01_illustration_1.png",
                "content_prompt": "author portrait",
                "core_keywords": ["author"],
                "semantic_aliases": {"author": ["writer"]},
                "context_summary_keywords": ["lesson"],
            }
        ],
        "warnings": [],
    }

    enrich_ai_image_asset_db_keywords(db, FakeKeywordClient(), batch_size=1)

    asset = db["assets"][0]
    assert asset["core_keywords"] == ["author", "portrait"]
    assert asset["semantic_aliases"] == {"author": ["writer"], "portrait": ["headshot"]}
    assert asset["context_summary_keywords"] == ["lesson", "profile"]


def test_updates_reusable_asset_library_by_copying_images_and_merging_db(tmp_path):
    class FakeKeywordClient:
        _model = "fake-keyword-model"

        def chat_json(self, messages, **kwargs):
            raw = messages[1]["content"]
            request = json.loads(raw[raw.index("{"):])
            assets = []
            for item in request["assets"]:
                if item["asset_kind"] == "background":
                    assets.append(
                        {
                            "asset_id": item["asset_id"],
                            "normalized_prompt": "淡雅秋日课堂背景",
                            "context_summary": "整套课件使用的低干扰秋日背景",
                            "reuse_scope": "visual_generic",
                            "specificity_score": 1,
                            "core_keywords": ["秋日", "背景"],
                            "context_keywords": [],
                            "style_keywords": ["淡雅"],
                        }
                    )
                else:
                    assets.append(
                        {
                            "asset_id": item["asset_id"],
                            "normalized_prompt": "史铁生肖像",
                            "context_summary": "作者介绍页使用的史铁生肖像素材",
                            "reuse_scope": "course_specific",
                            "specificity_score": 5,
                            "core_keywords": ["史铁生", "肖像"],
                            "context_keywords": ["秋天的怀念"],
                            "style_keywords": ["线条简洁"],
                        }
                    )
            return {"assets": assets}

    output_root = tmp_path / "output"
    session_dir = output_root / "session_20260506_111550"
    materials_dir = session_dir / "materials"
    library_dir = tmp_path / "materials_library"
    materials_dir.mkdir(parents=True)
    (materials_dir / "background.png").write_bytes(b"bg")
    (materials_dir / "page_04_illustration_1.png").write_bytes(b"author")

    plan = {
        "meta": {
            "topic": "七年级语文《秋天的怀念》课文教学",
            "audience": "七年级学生、初中语文教师",
        },
        "visual": {"background_prompt": "淡雅秋日课堂背景"},
        "pages": [
            {
                "page_number": 4,
                "title": "作者与背景介绍",
                "material_needs": {
                    "images": [
                        {
                            "query": "史铁生肖像",
                            "generation_prompt": "史铁生肖像插画，编辑感风格，线条简洁",
                            "source": "ai_generate",
                            "role": "illustration",
                            "aspect_ratio": "1:1",
                        }
                    ]
                },
            }
        ],
    }
    (session_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    db, db_path = update_ai_image_asset_library(
        session_dir,
        library_dir,
        keyword_client=FakeKeywordClient(),
        keyword_batch_size=4,
    )

    assert db_path == library_dir / "ai_image_asset_db.json"
    assert db_path.exists()
    assert db["output_root"] == str(library_dir.resolve())
    assert db["asset_count"] == 2
    assert db["schema_version"] == 5
    for asset in db["assets"]:
        copied_path = library_dir / asset["image_path"]
        assert copied_path.exists()
        assert asset["image_path"].startswith("ai_images/")
        assert asset["source"]["source_output_root"] == str(session_dir.resolve())
        assert asset["library"]["source_output_root"] == str(session_dir.resolve())

    persisted = json.loads(db_path.read_text(encoding="utf-8"))
    assert persisted["asset_count"] == 2
    assert {asset["asset_id"] for asset in persisted["assets"]} == {asset["asset_id"] for asset in db["assets"]}


def test_ingests_output_sessions_into_reusable_asset_library(tmp_path):
    output_root = tmp_path / "output"
    library_dir = tmp_path / "materials_library"

    for index in (1, 2):
        session_dir = output_root / f"session_20260507_14000{index}"
        materials_dir = session_dir / "materials"
        materials_dir.mkdir(parents=True)
        (materials_dir / "page_01_illustration_1.png").write_bytes(f"image-{index}".encode("ascii"))
        plan = {
            "meta": {"topic": "topic", "audience": "grade"},
            "pages": [
                {
                    "page_number": 1,
                    "title": f"Page {index}",
                    "material_needs": {
                        "images": [
                            {
                                "query": f"image prompt {index}",
                                "source": "ai_generate",
                                "role": "illustration",
                                "aspect_ratio": "1:1",
                            }
                        ]
                    },
                }
            ],
        }
        (session_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    db, db_path, report = ingest_ai_image_asset_library_from_output(output_root, library_dir)

    assert db_path == library_dir.resolve() / "ai_image_asset_db.json"
    assert report["session_count"] == 2
    assert len(report["processed_sessions"]) == 2
    assert report["failed_sessions"] == []
    assert db["asset_count"] == 2
    index_path = library_dir / "ai_image_match_index.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["source_asset_count"] == 2
    assert index["asset_count"] == 2
    for asset in db["assets"]:
        copied_path = library_dir / asset["image_path"]
        assert copied_path.exists()
        assert asset["image_path"].startswith("ai_images/")

    db_again, _db_path, report_again = ingest_ai_image_asset_library_from_output(output_root, library_dir)
    assert report_again["session_count"] == 2
    assert db_again["asset_count"] == 2


def test_finds_reusable_asset_with_content_bm25_score(tmp_path):
    class FakeReuseClient:
        _model = "fake-reuse-model"

        def chat_json(self, messages, **kwargs):
            raw = messages[1]["content"]
            request = json.loads(raw[raw.index("{"):])
            asset_id = request["assets"][0]["asset_id"]
            return {
                "assets": [
                    {
                        "asset_id": asset_id,
                        "normalized_prompt": "史铁生肖像",
                        "context_summary": "作者介绍页使用的史铁生肖像素材",
                        "reuse_scope": "course_specific",
                        "specificity_score": 5,
                        "core_keywords": ["史铁生", "肖像"],
                        "context_keywords": ["秋天的怀念"],
                        "style_keywords": ["线条简洁"],
                    }
                ]
            }

    library_dir = tmp_path / "materials_library"
    image_dir = library_dir / "ai_images"
    image_dir.mkdir(parents=True)
    (image_dir / "asset_author.png").write_bytes(b"author")
    db = {
        "schema_version": 4,
        "output_root": str(library_dir),
        "asset_count": 1,
        "assets": [
            {
                "asset_id": "asset_author",
                "asset_kind": "page_image",
                "image_path": "ai_images/asset_author.png",
                "role": "illustration",
                "aspect_ratio": "1:1",
                "prompt": "史铁生肖像",
                "generation_prompt": "史铁生肖像插画，编辑感风格，线条简洁",
                "context_summary": "作者介绍页使用的史铁生肖像素材",
                "theme": "七年级语文《秋天的怀念》课文教学",
                "grade": "七年级",
                "subject": "语文",
                "reuse_scope": "course_specific",
                "specificity_score": 5,
                "core_keywords": ["史铁生", "肖像"],
                "context_keywords": ["秋天的怀念"],
                "style_keywords": ["线条简洁"],
                "match_key": "史铁生|肖像|秋天的怀念|语文|七年级",
                "source": {},
            }
        ],
        "warnings": [],
    }
    (library_dir / "ai_image_asset_db.json").write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")

    match = find_reusable_ai_image_asset(
        library_dir=library_dir,
        asset_kind="page_image",
        prompt="史铁生肖像",
        theme="七年级语文《秋天的怀念》课文教学",
        grade="七年级",
        subject="语文",
        page_title="作者与背景介绍",
        role="illustration",
        aspect_ratio="1:1",
        keyword_client=FakeReuseClient(),
        debug_path=library_dir / "reuse_debug.json",
        debug_context={"page_number": 4, "slot_key": "illustration_1"},
    )

    assert match is not None
    assert match["asset"]["asset_id"] == "asset_author"
    assert match["keyword_score"] >= 0.6

    debug = json.loads((library_dir / "reuse_debug.json").read_text(encoding="utf-8"))
    assert debug["queries"][0]["context"]["page_number"] == 4
    assert debug["queries"][0]["decision"]["reused"] is True
    assert debug["queries"][0]["decision"]["reason"] == "reused_by_content_bm25_score"
    assert debug["queries"][0]["ranked_candidates"][0]["score_details"]["content_score"] >= 0.6


def test_reuse_manifest_causes_library_ingest_to_skip_reused_images(tmp_path):
    session_dir = tmp_path / "output" / "session_20260506_111550"
    materials_dir = session_dir / "materials"
    library_dir = tmp_path / "materials_library"
    materials_dir.mkdir(parents=True)
    (materials_dir / "page_04_illustration_1.png").write_bytes(b"reused")
    plan = {
        "meta": {"topic": "七年级语文《秋天的怀念》课文教学", "audience": "七年级学生"},
        "pages": [
            {
                "page_number": 4,
                "title": "作者与背景介绍",
                "material_needs": {
                    "images": [
                        {
                            "query": "史铁生肖像",
                            "generation_prompt": "史铁生肖像插画，编辑感风格，线条简洁",
                            "source": "ai_generate",
                            "role": "illustration",
                            "aspect_ratio": "1:1",
                        }
                    ]
                },
            }
        ],
    }
    (session_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    record_reused_ai_image_asset(
        session_dir=session_dir,
        session_image_path=materials_dir / "page_04_illustration_1.png",
        match={
            "asset": {"asset_id": "asset_author", "image_path": "ai_images/asset_author.png"},
            "keyword_score": 1.0,
        },
    )

    db, _target = update_ai_image_asset_library(session_dir, library_dir)

    assert db["asset_count"] == 0


def test_match_index_omits_deprecated_constraints_and_normalizes_grade(tmp_path):
    from edupptx.materials.ai_image_asset_db import build_ai_image_match_index

    db = {
        "schema_version": 4,
        "assets": [
            {
                "asset_id": "asset_author",
                "asset_kind": "page_image",
                "image_path": "ai_images/asset_author.png",
                "role": "illustration",
                "aspect_ratio": "4:3",
                "prompt": "author portrait",
                "grade": "7年级",
                "subject": "语文",
                "reuse_scope": "course_specific",
                "specificity_score": 5,
                "core_keywords": [
                    "史铁生",
                    "肖像",
                    "高年级编辑感",
                    "教学插画",
                    "无文字水印",
                ],
                "style_keywords": [],
                "must_match": [],
                "must_not_conflict": [
                    "无多余文字",
                    "场景为秋日",
                    "其他作家肖像",
                    "非秋日场景",
                    "写实风格",
                ],
            }
        ],
    }

    index = build_ai_image_match_index(db, library_root=tmp_path)

    asset = index["assets"][0]
    assert asset["grade_norm"] == "七年级"
    assert asset["grade_number"] == 7
    assert asset["grade_band"] == "高年级"
    assert asset["core_keywords"] == ["史铁生", "肖像"]
    assert "reuse_scope" not in asset
    assert "specificity_score" not in asset
    assert "role" not in asset
    assert "prompt" not in asset
    assert "must_match" not in asset
    assert "must_not_conflict" not in asset
    assert "avoid_keywords" not in asset


def test_update_library_writes_slim_match_index(tmp_path):
    library_dir = tmp_path / "materials_library"
    session_dir = tmp_path / "output" / "session_20260507_150001"
    materials_dir = session_dir / "materials"
    materials_dir.mkdir(parents=True)
    (materials_dir / "page_01_illustration_1.png").write_bytes(b"image")
    plan = {
        "meta": {"topic": "topic", "audience": "grade"},
        "pages": [
            {
                "page_number": 1,
                "title": "Page",
                "material_needs": {
                    "images": [
                        {
                            "query": "simple image prompt",
                            "generation_prompt": "simple image prompt, profile style, no text",
                            "prompt_route": {
                                "template_family": "lower",
                                "profiles": [
                                    {
                                        "id": "lower_base",
                                        "priority": 10,
                                        "prompt_terms": ["profile style"],
                                        "negative_terms": ["no text"],
                                    }
                                ],
                                "profile_ids": ["lower_base"],
                                "profile_prompt_terms": ["profile style"],
                                "role_prompt_terms": ["role only"],
                                "page_type_prompt_terms": ["page only"],
                                "aspect_ratio_prompt_terms": ["square layout"],
                                "quality_terms": ["quality only"],
                                "negative_terms": ["no text"],
                                "style_prompt": "profile style, role only, no text",
                            },
                            "source": "ai_generate",
                            "role": "illustration",
                            "aspect_ratio": "1:1",
                        }
                    ]
                },
            }
        ],
    }
    (session_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    db, _db_path = update_ai_image_asset_library(session_dir, library_dir)

    index_path = library_dir / "ai_image_match_index.json"
    assert db["asset_count"] == 1
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["source_asset_count"] == 1
    assert index["asset_count"] == 1
    assert "source" not in index["assets"][0]
    assert "library" not in index["assets"][0]
    asset = index["assets"][0]
    assert "style_prompt" not in asset
    assert asset["prompt_route"] == {"template_family": "lower", "profile_ids": ["lower_base"]}


def test_reuse_reads_slim_match_index_instead_of_rich_db(tmp_path):
    class FakeReuseClient:
        _model = "fake-reuse-model"

        def chat_json(self, messages, **kwargs):
            raw = messages[1]["content"]
            request = json.loads(raw[raw.index("{"):])
            asset_id = request["assets"][0]["asset_id"]
            return {
                "assets": [
                    {
                        "asset_id": asset_id,
                        "normalized_prompt": "author portrait",
                        "context_summary": "author profile image",
                        "reuse_scope": "course_specific",
                        "specificity_score": 5,
                        "core_keywords": ["author", "portrait"],
                        "context_keywords": ["lesson"],
                        "style_keywords": [],
                        "main_entities": ["author"],
                        "visual_actions": [],
                        "scene_elements": [],
                        "emotion_tone": [],
                        "teaching_intent": "author profile image",
                    }
                ]
            }

    library_dir = tmp_path / "materials_library"
    image_dir = library_dir / "ai_images"
    image_dir.mkdir(parents=True)
    (image_dir / "asset_author.png").write_bytes(b"author")
    rich_db = {
        "schema_version": 4,
        "output_root": str(library_dir),
        "asset_count": 1,
        "assets": [
            {
                "asset_id": "asset_author",
                "asset_kind": "page_image",
                "image_path": "ai_images/asset_author.png",
                "role": "illustration",
                "aspect_ratio": "1:1",
                "prompt": "unrelated",
                "subject": "lang",
                "grade": "7",
                "reuse_scope": "course_specific",
                "core_keywords": ["unrelated"],
                "source": {},
            }
        ],
    }
    match_index = {
        "schema_version": 5,
        "source_asset_count": 1,
        "asset_count": 1,
        "assets": [
            {
                "asset_id": "asset_author",
                "asset_kind": "page_image",
                "image_path": "ai_images/asset_author.png",
                "aspect_ratio": "1:1",
                "subject": "lang",
                "grade_norm": "7",
                "grade_band": "high",
                "prompt": "author portrait",
                "content_prompt": "author portrait",
                "normalized_prompt": "author portrait",
                "context_summary": "author profile image",
                "teaching_intent": "author profile image",
                "duplicate_asset_ids": [],
            }
        ],
    }
    (library_dir / "ai_image_asset_db.json").write_text(json.dumps(rich_db, ensure_ascii=False), encoding="utf-8")
    (library_dir / "ai_image_match_index.json").write_text(json.dumps(match_index, ensure_ascii=False), encoding="utf-8")

    match = find_reusable_ai_image_asset(
        library_dir=library_dir,
        asset_kind="page_image",
        prompt="author portrait",
        theme="lesson",
        grade="7",
        subject="lang",
        page_title="Author",
        role="illustration",
        aspect_ratio="1:1",
        keyword_client=FakeReuseClient(),
        debug_path=library_dir / "reuse_debug.json",
    )

    assert match is not None
    assert match["asset"]["asset_id"] == "asset_author"
    debug = json.loads((library_dir / "reuse_debug.json").read_text(encoding="utf-8"))
    assert debug["queries"][0]["match_index_path"].endswith("ai_image_match_index.json")
