from pathlib import Path
import importlib.util
import json
import sqlite3
import sys
import zipfile

from PIL import Image, ImageDraw

from edupptx.materials.ai_image_asset_db import build_ai_image_match_index

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_ppt_materials_library.py"
SPEC = importlib.util.spec_from_file_location("build_ppt_materials_library", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
build_ppt_image_materials_library = MODULE.build_ppt_image_materials_library


def test_ppt_aspect_ratio_uses_fifty_percent_loss_threshold():
    assert MODULE._ppt_aspect_ratio_name(1200, 571) == "16:9"
    assert MODULE._ppt_aspect_ratio_name(200, 1600) == "other"
    assert MODULE._ppt_aspect_ratio_name(400, 600) == "3:4"


def test_ppt_transparent_padding_uses_exact_integer_target_ratio():
    img = Image.new("RGBA", (1200, 571), (255, 0, 0, 255))

    padded = MODULE._pad_image_to_ppt_aspect(img, "16:9")

    assert padded.size == (1200, 675)
    assert padded.getpixel((0, 0))[3] == 0
    assert padded.getpixel((600, 337))[3] == 255
    assert padded.width * 9 == padded.height * 16


def _patch_embedding_encoder(monkeypatch) -> None:
    monkeypatch.delenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", raising=False)

    def fake_encode_embedding_texts(texts, **_kwargs):
        import numpy as np

        return np.asarray(
            [[float(index + 1), 0.0, 1.0] for index, _text in enumerate(texts)],
            dtype="float32",
        )

    monkeypatch.setattr(
        "edupptx.materials.ai_image_asset_db._encode_embedding_texts",
        fake_encode_embedding_texts,
    )


def _write_minimal_pptx(
    pptx_path: Path,
    image_path: Path,
    *,
    slide_text: str = "Lesson image page",
    off: tuple[int, int] = (914400, 914400),
    ext: tuple[int, int] = (2743200, 2057400),
) -> None:
    off_x, off_y = off
    ext_cx, ext_cy = ext
    slide_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody><a:p><a:r><a:t>{slide_text}</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <p:pic>
        <p:nvPicPr><p:cNvPr id="2" name="Picture 1"/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="rId1"/></p:blipFill>
        <p:spPr>
          <a:xfrm>
            <a:off x="{off_x}" y="{off_y}"/>
            <a:ext cx="{ext_cx}" cy="{ext_cy}"/>
          </a:xfrm>
        </p:spPr>
      </p:pic>
    </p:spTree>
  </p:cSld>
</p:sld>
"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="../media/image1.png"/>
</Relationships>
"""
    presentation_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>
"""
    with zipfile.ZipFile(pptx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ppt/presentation.xml", presentation_xml)
        zf.writestr("ppt/slides/slide1.xml", slide_xml)
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", rels_xml)
        zf.write(image_path, "ppt/media/image1.png")


def test_build_ppt_image_materials_library_writes_match_index_and_embedding_sidecars(tmp_path, monkeypatch):
    _patch_embedding_encoder(monkeypatch)
    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(image_path)

    pptx_path = pptx_dir / "lesson.pptx"
    _write_minimal_pptx(pptx_path, image_path)

    library_dir = tmp_path / "materials_library_ppt"
    db, index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        use_vlm=False,
        use_keyword_enrichment=False,
    )

    assert index_path == library_dir / "strict_reuse_indexes"
    assert not (library_dir / "ai_image_match_index.json").exists()
    assert (library_dir / "strict_reuse_indexes" / "C05_scene_decor_container.json").exists()
    assert (library_dir / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json").exists()
    assert not (library_dir / "strict_reuse_indexes" / "strict_reuse_split_manifest.json").exists()
    assert not (library_dir / "ai_image_asset_db.json").exists()
    assert not (library_dir / "ppt_extraction_report.json").exists()
    assert (library_dir / "ai_image_embedding_index.npz").exists()
    assert (library_dir / "ai_image_embedding_meta.json").exists()
    assert db["asset_count"] == 1
    assert report["raw_picture_count"] == 1
    index = json.loads((library_dir / "strict_reuse_indexes" / "C05_scene_decor_container.json").read_text(encoding="utf-8"))
    assert index["asset_count"] == 1
    embedding_meta = json.loads((library_dir / "ai_image_embedding_meta.json").read_text(encoding="utf-8"))
    assert embedding_meta["asset_count"] == 1
    assert index["ppt_extractor"]["schema_version"] == MODULE.PPT_LIBRARY_SCHEMA_VERSION
    assert index["assets"][0]["detail_prompt"].startswith("教学配图")
    asset = db["assets"][0]
    assert asset["asset_id"].startswith("kbpptx_")
    assert asset["asset_kind"] == "page_image"
    assert asset["image_path"].startswith("pptx_images/")
    assert (library_dir / asset["image_path"]).exists()
    assert asset["topic_refs"] == ["lesson"]
    assert asset["asset_category"] == "unknown"
    for deleted_field in ("context_summary_keywords", "constraints", "core_keywords", "semantic_aliases"):
        assert deleted_field not in asset
    assert asset["duplicate_asset_ids"] == []
    assert asset["detail_prompt"].startswith("教学配图")
    assert "prompt_route" not in asset
    assert "normalized_prompt" not in asset
    assert "source_type" not in asset
    assert "source" not in asset
    assert "ppt_context" not in asset
    assert "vlm_visual_style" not in asset
    assert not asset["context_summary"].startswith(("来自", "图片来自", "该图来自"))


def test_build_ppt_materials_library_preserves_original_and_writes_padded_runtime_image(tmp_path, monkeypatch):
    _patch_embedding_encoder(monkeypatch)
    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (1200, 571), (60, 130, 210)).save(image_path)

    pptx_path = pptx_dir / "lesson.pptx"
    _write_minimal_pptx(pptx_path, image_path)

    library_dir = tmp_path / "materials_library_ppt"
    db, _index_path, _report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        use_vlm=False,
        use_keyword_enrichment=False,
        write_match_index=True,
    )

    asset = db["assets"][0]
    assert asset["original_image_path"].startswith("pptx_images_original/")
    assert asset["image_path"].startswith("pptx_images/")
    assert asset["actual_width"] == 1200
    assert asset["actual_height"] == 571
    assert asset["padded_width"] == 1200
    assert asset["padded_height"] == 675
    assert asset["aspect_ratio"] == "16:9"
    assert "aspect_bucket" not in asset
    assert "role" not in asset
    assert "padding_capacity" not in asset

    with Image.open(library_dir / asset["original_image_path"]) as original:
        assert original.size == (1200, 571)
    with Image.open(library_dir / asset["image_path"]) as padded:
        assert padded.mode == "RGBA"
        assert padded.size == (1200, 675)
        assert padded.width * 9 == padded.height * 16
        assert padded.getpixel((0, 0))[3] == 0


def test_build_ppt_materials_library_keeps_other_images_unpadded(tmp_path):
    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (200, 1600), (20, 80, 120)).save(image_path)

    pptx_path = pptx_dir / "lesson.pptx"
    _write_minimal_pptx(pptx_path, image_path)

    library_dir = tmp_path / "materials_library_ppt"
    db, _index_path, _report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        use_vlm=False,
        use_keyword_enrichment=False,
        write_match_index=False,
    )

    asset = db["assets"][0]
    assert asset["aspect_ratio"] == "other"
    assert asset["actual_width"] == 200
    assert asset["actual_height"] == 1600
    assert asset["padded_width"] == 200
    assert asset["padded_height"] == 1600
    with Image.open(library_dir / asset["image_path"]) as runtime:
        assert runtime.size == (200, 1600)


def test_build_ppt_image_materials_library_keeps_full_slide_backgrounds(tmp_path, monkeypatch):
    _patch_embedding_encoder(monkeypatch)
    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    image_path = tmp_path / "background.png"
    Image.new("RGB", (1280, 720), (190, 215, 240)).save(image_path)

    pptx_path = pptx_dir / "background.pptx"
    _write_minimal_pptx(
        pptx_path,
        image_path,
        off=(0, 0),
        ext=(12192000, 6858000),
    )

    library_dir = tmp_path / "materials_library_ppt"
    db, index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        use_vlm=False,
        use_keyword_enrichment=False,
    )

    assert index_path == library_dir / "strict_reuse_indexes"
    assert db["asset_count"] == 1
    assert report["skipped_count"] == 0
    asset = db["assets"][0]
    assert asset["asset_kind"] == "background"
    assert "role" not in asset
    assert "aspect_bucket" not in asset
    assert "padding_capacity" not in asset
    assert asset["asset_category"] == "background"
    assert asset["normalized_prompt"] == asset["content_prompt"]
    background_index = json.loads((library_dir / "strict_reuse_indexes" / "background.json").read_text(encoding="utf-8"))
    general_index = json.loads(
        (library_dir / "strict_reuse_indexes" / "C05_scene_decor_container.json").read_text(encoding="utf-8")
    )
    assert [item["asset_id"] for item in background_index["assets"]] == [asset["asset_id"]]
    assert general_index["assets"] == []


def test_build_ppt_image_materials_library_saves_unit_separately(tmp_path):
    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    db_dir = teach_kb_root / "data" / "db"
    pptx_dir.mkdir(parents=True)
    db_dir.mkdir(parents=True)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(image_path)

    pptx_path = pptx_dir / "lesson.pptx"
    _write_minimal_pptx(pptx_path, image_path)

    con = sqlite3.connect(db_dir / "teach_kb.db")
    try:
        con.execute("CREATE TABLE hierarchy (id TEXT, parent_id TEXT, name TEXT, subject TEXT)")
        con.execute(
            "CREATE TABLE pptx_files (id TEXT, period_id TEXT, file_path TEXT, file_name TEXT, file_size INTEGER, description TEXT)"
        )
        con.executemany(
            "INSERT INTO hierarchy (id, parent_id, name, subject) VALUES (?, ?, ?, ?)",
            [
                ("grade", "", "Grade 2", ""),
                ("semester", "grade", "Spring", ""),
                ("unit", "semester", "Unit 2", ""),
                ("lesson", "unit", "Comparing Numbers", ""),
                ("period", "lesson", "Lesson 1", "Math"),
            ],
        )
        con.execute(
            "INSERT INTO pptx_files (id, period_id, file_path, file_name, file_size, description) VALUES (?, ?, ?, ?, ?, ?)",
            ("pptx", "period", "pptx/lesson.pptx", "lesson.pptx", pptx_path.stat().st_size, ""),
        )
        con.commit()
    finally:
        con.close()

    db, _index_path, _report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=tmp_path / "materials_library_ppt",
        use_vlm=False,
        use_keyword_enrichment=False,
        write_match_index=False,
    )

    asset = db["assets"][0]
    assert asset["unit_ref"] == "Unit 2"
    assert asset["topic_refs"] == ["Comparing Numbers"]
    assert asset["theme"] == "Grade2MathComparingNumbersLesson1"
    index = build_ai_image_match_index(db, library_root=tmp_path / "materials_library_ppt")
    assert index["assets"][0]["unit_ref"] == "Unit 2"
    assert index["assets"][0]["topic_refs"] == ["Comparing Numbers"]


def test_ppt_near_duplicate_character_assets_keep_best_display_image(tmp_path):
    image_dir = tmp_path / "pptx_images"
    image_dir.mkdir()

    def draw_girl(path: Path, size) -> None:
        img = Image.new("RGBA", size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        w, h = size
        draw.ellipse((w * 0.28, h * 0.08, w * 0.72, h * 0.38), fill=(250, 210, 190, 255))
        draw.rectangle((w * 0.4, h * 0.38, w * 0.62, h * 0.7), fill=(240, 120, 160, 255))
        draw.line((w * 0.62, h * 0.45, w * 0.86, h * 0.25), fill=(90, 70, 60, 255), width=max(2, w // 35))
        draw.line((w * 0.44, h * 0.7, w * 0.35, h * 0.92), fill=(90, 70, 60, 255), width=max(2, w // 35))
        draw.line((w * 0.58, h * 0.7, w * 0.7, h * 0.92), fill=(90, 70, 60, 255), width=max(2, w // 35))
        img.save(path)

    draw_girl(image_dir / "small.png", (180, 260))
    draw_girl(image_dir / "large.png", (220, 320))

    base = {
        "asset_kind": "page_image",
        "aspect_ratio": "9:16",
        "role": "illustration",
        "page_type": "content",
        "theme": "lesson",
        "subject": "math",
        "grade_norm": "grade 2",
        "grade_band": "lower",
        "unit_ref": "unit",
        "topic_refs": ["lesson"],
        "context_summary": "class interaction",
        "teaching_intent": "invite students to speak",
        "asset_category": "character_action",
        "_pptx_path": "same.pptx",
    }
    db = {
        "assets": [
            {
                **base,
                "asset_id": "small",
                "image_path": "pptx_images/small.png",
                "content_prompt": "cartoon girl speaking",
                "_ppt_source_pixels": 180 * 260,
                "_ppt_display_pixels": 10_000,
            },
            {
                **base,
                "asset_id": "large",
                "image_path": "pptx_images/large.png",
                "content_prompt": "cartoon girl raising hand and speaking",
                "_ppt_source_pixels": 220 * 320,
                "_ppt_display_pixels": 20_000,
            },
        ],
    }

    report = MODULE._dedupe_ppt_near_duplicate_assets(db, tmp_path)

    assert report["removed_count"] == 1
    assert [asset["asset_id"] for asset in db["assets"]] == ["large"]
    assert db["assets"][0]["duplicate_asset_ids"] == ["small"]
    assert not (image_dir / "small.png").exists()
    assert (image_dir / "large.png").exists()


def test_build_ppt_image_materials_library_uses_vlm_metadata(tmp_path):
    class FakeVLMClient:
        _model = "doubao-vlm-test"
        messages = None

        def chat_vlm_json(self, **kwargs):
            self.messages = kwargs["messages"]
            return {
                "content_prompt": "blue rectangle teaching illustration",
                "detail_prompt": "blue rectangle teaching illustration with a plain blue fill",
                "context_summary": "visual support image",
                "teaching_intent": "support explanation",
                "strict_reuse_group": "C05_scene_decor_container",
                "strict_reuse_confidence": 0.82,
                "strict_reuse_reason": "plain visual support image",
                "query_aliases": {"蓝色矩形": [{"alias": "蓝色方块", "confidence": 0.9}]},
                # padding_capacity is now derived from pixel-edge analysis at
                # annotation time; VLM-side hints (if any) are discarded by the
                # normalizer in favor of the pixel result.
                "padding_capacity": "high",
                "vlm_visual_style": {"palette": "blue"},
                "core_keywords": ["should not be used from vlm"],
            }

    class FakeKeywordClient:
        messages = None

        def chat_json(self, messages, **kwargs):
            self.messages = messages
            payload = json.loads(messages[-1]["content"].split("\n", 1)[1])
            asset_id = payload["assets"][0]["asset_id"]
            return {
                "assets": [
                    {
                        "asset_id": asset_id,
                        "context_summary": "keyword context should not replace VLM context",
                        "teaching_intent": "keyword intent should not replace VLM intent",
                        "context_summary_keywords": ["visual support"],
                        "core_keywords": ["blue rectangle", "teaching illustration", "visual support"],
                        "semantic_aliases": {"blue rectangle": ["blue block"]},
                    }
                ],
            }

    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (400, 300), (60, 130, 210)).save(image_path)

    pptx_path = pptx_dir / "lesson.pptx"
    _write_minimal_pptx(pptx_path, image_path)

    vlm_client = FakeVLMClient()
    keyword_client = FakeKeywordClient()
    db, _index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=tmp_path / "materials_library_ppt",
        vlm_client=vlm_client,
        keyword_client=keyword_client,
        use_vlm=True,
        write_match_index=False,
    )

    asset = db["assets"][0]
    assert asset["content_prompt"] == "blue rectangle teaching illustration"
    assert asset["detail_prompt"] == "blue rectangle teaching illustration with a plain blue fill"
    assert asset["context_summary"] == "visual support image"
    assert asset["teaching_intent"] == "support explanation"
    assert "query_aliases" not in asset
    # padding_capacity is computed at annotation time from pixel edges; the
    # synthesized blue rectangle test image has fully colored borders → low.
    assert "padding_capacity" not in asset
    assert asset["strict_reuse_group"] == "C05_scene_decor_container"
    assert asset["strict_reuse_confidence"] == 0.82
    assert asset["strict_reuse_reason"] == "plain visual support image"
    assert "transform_advice" not in asset
    for deleted_field in ("context_summary_keywords", "constraints", "core_keywords", "semantic_aliases"):
        assert deleted_field not in asset
    assert asset["topic_refs"] == ["lesson"]
    assert asset["duplicate_asset_ids"] == []
    assert report["use_keyword_enrichment"] is True
    assert "prompt_route" not in asset
    assert "normalized_prompt" not in asset
    assert "vlm_visual_style" not in asset
    assert "source_type" not in asset
    assert all(not key.startswith("vlm_") for key in asset)
    system_prompt = vlm_client.messages[0]["content"]
    assert "material_needs.images[].query" not in system_prompt
    assert "只描述图片本体" in system_prompt
    assert "detail_prompt" in system_prompt
    assert "≤ 30 个汉字" in system_prompt
    assert "20-40 个汉字" in system_prompt
    assert "教学载体 + 具体教学内容" in system_prompt
    assert "承载层" in system_prompt
    assert "只回答\"这张图是什么\"" in system_prompt
    assert "禁止出现\"用于\"" in system_prompt
    assert "这些内容不得进入 content_prompt" in system_prompt
    assert "core_keywords" in system_prompt
    assert "semantic_aliases" in system_prompt
    assert "query_aliases" in system_prompt
    # padding_capacity is now derived from pixel-edge analysis, not VLM output
    assert "transform_advice" not in system_prompt
    assert "padding_capacity" not in system_prompt
    assert "safe_crop" not in system_prompt
    assert "max_safe_crop_pct" not in system_prompt
    assert "card_background_fill_safe" not in system_prompt
    assert "max_safe_padding_pct" not in system_prompt
    assert "不含具体汉字、拼音或文字" in system_prompt
    assert "{page_type}+用于+{topic}+展示/学习" in system_prompt
    assert "不要写教学目标、解题过程、学习效果" in system_prompt
    for disallowed_field in (
        "normalized_prompt",
        "prompt_route",
        "vlm_visual_style",
        "asset_category",
    ):
        assert disallowed_field not in system_prompt
    assert '"content_prompt": "blue rectangle teaching illustration"' in keyword_client.messages[-1]["content"]


def test_ppt_theme_is_compact_without_inserted_spaces():
    assert (
        MODULE._theme_from_course(
            {
                "grade": "五年级",
                "subject": "语文",
                "lesson": "《刷子李》",
                "period": "第1课时",
            }
        )
        == "五年级语文《刷子李》第1课时"
    )


def test_ppt_annotation_normalization_keeps_only_vlm_semantics():
    annotation = {
        "content_prompt": "汉字“傻”生字教学卡，含拼音、田字格、笔画与部首",
        "detail_prompt": "生字教学卡片，展示汉字“傻”的拼音和田字格写法，左下角标注笔画、部首，右上角有音量播放图标，下方有连续、分步按钮",
        "context_summary": "汉字“傻”的生字卡，承担课堂上的读音与书写讲解",
        "teaching_intent": "辅助学生识记生字",
        "strict_reuse_group": "C01_language_glyph_visual",
        "strict_reuse_confidence": 0.91,
        "strict_reuse_reason": "画面含有具体汉字和拼音内容",
        "query_aliases": {"汉字生字卡": [{"alias": "生字卡", "confidence": 0.9}, {"alias": "character card", "confidence": 0.9}]},
        "transform_advice": {"padding_capacity": "LOW"},
        "visible_text": ["傻"],
        "vlm_visual_style": {"palette": "warm"},
    }

    normalized = MODULE._normalize_annotation(annotation, object(), {}, {})

    assert normalized == {
        "content_prompt": "汉字“傻”生字教学卡，含拼音、田字格、笔画与部首",
        "detail_prompt": "生字教学卡片，展示汉字“傻”的拼音和田字格写法，左下角标注笔画、部首，右上角有音量播放图标，下方有连续、分步按钮",
        "context_summary": "汉字“傻”的生字卡，承担课堂上的读音与书写讲解",
        "teaching_intent": "辅助学生识记生字",
        "strict_reuse_group": "C01_language_glyph_visual",
        "strict_reuse_confidence": 0.91,
        "strict_reuse_reason": "画面含有具体汉字和拼音内容",
    }


def _raw_ppt_image_for_general_test():
    return MODULE.RawPptImage(
        pptx_path=Path("demo.pptx"),
        slide_no=1,
        shape_idx=1,
        source_media_path="ppt/media/image1.png",
        suffix=".png",
        data=b"fake-image-bytes",
        sha256="0" * 64,
        width=400,
        height=300,
        mode="RGB",
        bbox={"x": 0, "y": 0, "w": 400, "h": 300},
        slide_text="课堂展示",
        slide_title_guess="导入",
    )


def test_ppt_vlm_prompt_requests_general_boolean():
    assert '"general": false' in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert "general 必须是布尔值" in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert "严格保守" in MODULE.PPT_VLM_SYSTEM_PROMPT


def test_ppt_annotation_normalizes_boolean_general():
    item = _raw_ppt_image_for_general_test()
    meta = {"file_name": "demo.pptx", "course": {"subject": "语文"}}
    context = {"slide_text": "课堂展示", "slide_title_guess": "导入"}

    annotation = MODULE._normalize_annotation(
        {
            "content_prompt": "带装饰的空白对话气泡贴纸",
            "detail_prompt": "带装饰的空白对话气泡贴纸",
            "context_summary": "空白气泡贴纸用于课堂展示",
            "teaching_intent": "承载可替换文字内容",
            "general": True,
            "strict_reuse_group": "C05_scene_decor_container",
            "strict_reuse_confidence": 0.9,
            "strict_reuse_reason": "属于场景装饰容器：空白气泡",
        },
        item,
        meta,
        context,
    )

    assert annotation["general"] is True


def test_ppt_asset_persists_general_from_annotation():
    item = _raw_ppt_image_for_general_test()
    meta = {"file_name": "demo.pptx", "course": {"subject": "其他", "grade": "五年级"}}
    context = {"slide_text": "课堂展示", "slide_title_guess": "导入"}
    annotation = {
        "content_prompt": "带装饰的空白对话气泡贴纸",
        "detail_prompt": "带装饰的空白对话气泡贴纸",
        "context_summary": "空白气泡贴纸用于课堂展示",
        "teaching_intent": "承载可替换文字内容",
        "general": True,
        "strict_reuse_group": "C05_scene_decor_container",
        "strict_reuse_confidence": 0.9,
        "strict_reuse_reason": "属于场景装饰容器：空白气泡",
    }

    asset = MODULE._build_asset_from_annotation(
        asset_id="ppt_asset",
        image_rel="pptx_images/ppt_asset.png",
        original_image_rel="pptx_images_original/ppt_asset.png",
        image_fields={
            "actual_width": 400,
            "actual_height": 300,
            "padded_width": 400,
            "padded_height": 300,
            "aspect_ratio": "4:3",
        },
        item=item,
        meta=meta,
        context=context,
        annotation=annotation,
    )

    assert asset["general"] is True


def test_ppt_annotation_normalization_falls_back_when_detail_prompt_missing():
    annotation = {
        "content_prompt": "线段图：钢笔5支，铅笔是钢笔的3倍，求铅笔几支",
        "context_summary": "线段图配题，承担二年级倍数应用题的练习展示",
        "teaching_intent": "练习根据线段图列式解倍数问题",
    }

    normalized = MODULE._normalize_annotation(annotation, object(), {}, {})

    assert normalized["detail_prompt"] == normalized["content_prompt"]
    assert "query_aliases" not in normalized
    # No image path supplied → no pixel-derived capacity available.
    assert "padding_capacity" not in normalized
    assert "transform_advice" not in normalized


def test_match_index_keeps_distinct_text_teaching_cards(tmp_path):
    image_dir = tmp_path / "pptx_images"
    image_dir.mkdir()
    Image.new("RGB", (400, 300), (120, 180, 220)).save(image_dir / "sha.png")
    Image.new("RGB", (400, 300), (80, 120, 180)).save(image_dir / "kan.png")

    base = {
        "asset_kind": "page_image",
        "aspect_ratio": "4:3",
        "role": "illustration",
        "page_type": "content",
        "theme": "五年级语文《刷子李》第1课时",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
        "topic_refs": ["刷子李"],
        "context_summary": "字词学习页面，用于生字教学演示",
        "teaching_intent": "辅助学生识记生字的读音和写法",
        "strict_reuse_group": "C04_generic_subject_object",
        "duplicate_asset_ids": [],
    }
    db = {
        "schema_version": 2,
        "assets": [
            {
                **base,
                "asset_id": "kbpptx_sha",
                "image_path": "pptx_images/sha.png",
                    "content_prompt": "apple object card with a red apple on a white background",
            },
            {
                **base,
                "asset_id": "kbpptx_kan",
                "image_path": "pptx_images/kan.png",
                    "content_prompt": "city skyline illustration with blue buildings at night",
            },
        ],
    }

    index = build_ai_image_match_index(db, library_root=tmp_path)

    assert index["asset_count"] == 2
    assert {asset["asset_id"] for asset in index["assets"]} == {"kbpptx_sha", "kbpptx_kan"}
