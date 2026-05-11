import json

from edupptx.materials.ai_image_asset_db import (
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
    prompts = {asset["prompt"] for asset in db["assets"]}
    assert prompts == {"淡雅秋日课堂背景", "深秋北海公园菊花盛放的静谧场景"}
    for asset in db["assets"]:
        assert asset["theme"] == "七年级语文《秋天的怀念》课文教学"
        assert asset["grade"] == "七年级"
        assert asset["subject"] == "语文"
        assert asset["image_path"].startswith("session_20260506_111550/materials/")
        assert asset["source"]["session_id"] == "session_20260506_111550"


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
            assert "史铁生肖像" in messages[1]["content"]
            assert "汉字拼音标注" in messages[1]["content"]
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
    assert db["keyword_builder"]["method"] == "llm_reuse_scope_keyword_extraction"
    assert db["keyword_builder"]["model"] == "fake-keyword-model"
    assert author["reuse_scope"] == "course_specific"
    assert author["context_summary"] == "作者背景页的史铁生肖像素材"
    assert author["specificity_score"] == 5
    assert author["core_keywords"] == ["史铁生", "肖像"]
    assert author["context_keywords"] == ["秋天的怀念", "作者介绍"]
    assert author["style_keywords"] == ["线条简洁"]
    assert author["match_key"] == "史铁生|肖像|秋天的怀念|作者介绍|语文|七年级"

    assert pinyin["reuse_scope"] == "subject_generic"
    assert pinyin["context_summary"] == "生字词正音页的汉字拼音教学示意"
    assert pinyin["context_keywords"] == []
    assert pinyin["match_key"] == "汉字|拼音标注|教学示意|语文"
    assert "秋天的怀念" not in pinyin["match_key"]


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
                            "query": "史铁生肖像插画，编辑感风格，线条简洁",
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


def test_finds_reusable_asset_with_semantic_bm25_score(tmp_path):
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
                "prompt": "史铁生肖像插画，编辑感风格，线条简洁",
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
        prompt="史铁生肖像插画，编辑感风格，线条简洁",
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
    assert match["keyword_score"] >= 0.7

    debug = json.loads((library_dir / "reuse_debug.json").read_text(encoding="utf-8"))
    assert debug["queries"][0]["context"]["page_number"] == 4
    assert debug["queries"][0]["decision"]["reused"] is True
    assert debug["queries"][0]["decision"]["reason"] == "reused_by_semantic_bm25_score"
    assert debug["queries"][0]["ranked_candidates"][0]["score_details"]["core_score"] == 1.0


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
                            "query": "史铁生肖像插画，编辑感风格，线条简洁",
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
        "schema_version": 2,
        "source_asset_count": 1,
        "asset_count": 1,
        "assets": [
            {
                "asset_id": "asset_author",
                "asset_kind": "page_image",
                "image_path": "ai_images/asset_author.png",
                "role": "illustration",
                "aspect_ratio": "1:1",
                "subject": "lang",
                "grade_norm": "7",
                "grade_band": "high",
                "reuse_scope": "course_specific",
                "specificity_score": 5,
                "prompt": "author portrait",
                "normalized_prompt": "author portrait",
                "context_summary": "author profile image",
                "teaching_intent": "author profile image",
                "core_keywords": ["author", "portrait"],
                "context_keywords": ["lesson"],
                "main_entities": ["author"],
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
