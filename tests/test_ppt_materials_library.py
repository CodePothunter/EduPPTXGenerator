from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import sqlite3
import sys
import threading
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


def _write_two_picture_pptx(pptx_path: Path, first_image_path: Path, second_image_path: Path) -> None:
    slide_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody><a:p><a:r><a:t>Pipeline lesson image page</a:t></a:r></a:p></p:txBody>
      </p:sp>
      <p:pic>
        <p:nvPicPr><p:cNvPr id="2" name="Picture 1"/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="rId1"/></p:blipFill>
        <p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="1828800" cy="1371600"/></a:xfrm></p:spPr>
      </p:pic>
      <p:pic>
        <p:nvPicPr><p:cNvPr id="3" name="Picture 2"/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="rId2"/></p:blipFill>
        <p:spPr><a:xfrm><a:off x="3657600" y="914400"/><a:ext cx="1828800" cy="1371600"/></a:xfrm></p:spPr>
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
  <Relationship Id="rId2"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="../media/image2.png"/>
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
        zf.write(first_image_path, "ppt/media/image1.png")
        zf.write(second_image_path, "ppt/media/image2.png")


def test_build_ppt_arg_parser_defaults_to_pipeline_workers_and_single_item_batches():
    args = MODULE.build_arg_parser().parse_args([])

    assert args.keyword_batch_size == 1
    assert args.vlm_workers == 15
    assert args.llm_workers == 15


def test_build_ppt_materials_library_pipelines_llm_after_each_vlm_completion(tmp_path):
    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(first_image)
    Image.new("RGB", (400, 300), (20, 80, 180)).save(second_image)
    _write_two_picture_pptx(pptx_dir / "lesson.pptx", first_image, second_image)

    lock = threading.Lock()
    events: list[str] = []
    first_llm_started = threading.Event()

    def record(event: str) -> None:
        with lock:
            events.append(event)

    class PipelineVlmClient:
        _model = "fake-vlm"

        def chat_vlm_json(self, *, messages, temperature=0.1, max_tokens=4096):
            user_content = messages[-1]["content"]
            payload_text = user_content[0]["text"]
            payload = json.loads(payload_text[payload_text.index("{") :])
            source_media_path = payload["image"]["source_media_path"]
            is_first = source_media_path.endswith("image1.png")
            label = "first" if is_first else "second"
            record(f"vlm_start_{label}")
            if not is_first:
                if not first_llm_started.wait(timeout=0.5):
                    record("second_vlm_finished_before_first_llm")
            record(f"vlm_done_{label}")
            return {
                "query": f"{label} teaching object",
                "context_summary": f"{label} context",
                "teaching_intent": f"{label} intent",
            }

    class PipelineKeywordClient:
        _model = "fake-llm"

        def chat(self, messages, temperature=0.0, max_tokens=4096):
            system = messages[0]["content"]
            payload = json.loads(messages[-1]["content"][messages[-1]["content"].index("[") :])
            query = payload[0]["query"]
            label = "first" if query.startswith("first") else "second"
            record(f"llm_start_{label}")
            if label == "first":
                first_llm_started.set()
            if "strict_reuse_group" in system or "分类器" in system:
                return json.dumps(
                    [{"query": query, "strict_reuse_group": "C02_generic_subject_object"}],
                    ensure_ascii=False,
                )
            if "general" in system:
                return json.dumps([{"query": query, "general": True}], ensure_ascii=False)
            return json.dumps([{"query": query, "caption": query}], ensure_ascii=False)

    db, _index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=tmp_path / "materials_library_ppt",
        vlm_client=PipelineVlmClient(),
        keyword_client=PipelineKeywordClient(),
        use_vlm=True,
        use_keyword_enrichment=True,
        write_match_index=False,
        vlm_workers=2,
        llm_workers=1,
        keyword_batch_size=1,
    )

    assert report["kept_asset_count"] == 2
    assert db["asset_count"] == 2
    assert "second_vlm_finished_before_first_llm" not in events
    assert events.index("llm_start_first") < events.index("vlm_done_second")


def test_build_ppt_image_materials_library_reports_missing_caption_without_embedding_query_fallback(tmp_path, monkeypatch):
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
    assert (library_dir / "strict_reuse_indexes" / "C03_scene_decor_container.json").exists()
    assert (library_dir / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json").exists()
    assert not (library_dir / "strict_reuse_indexes" / "strict_reuse_split_manifest.json").exists()
    assert not (library_dir / "ai_image_asset_db.json").exists()
    assert not (library_dir / "ppt_extraction_report.json").exists()
    assert not (library_dir / "ai_image_embedding_index.npz").exists()
    assert not (library_dir / "ai_image_embedding_meta.json").exists()
    assert (library_dir / "ai_image_embedding_missing_caption_review.json").exists()
    assert db["asset_count"] == 1
    assert report["raw_picture_count"] == 1
    index = json.loads((library_dir / "strict_reuse_indexes" / "C03_scene_decor_container.json").read_text(encoding="utf-8"))
    assert index["asset_count"] == 1
    missing_caption_review = json.loads(
        (library_dir / "ai_image_embedding_missing_caption_review.json").read_text(encoding="utf-8")
    )
    assert missing_caption_review["missing_caption_count"] == 1
    assert missing_caption_review["assets"][0]["asset_id"] == index["assets"][0]["asset_id"]
    assert any("embedding_missing_caption" in warning for warning in report["warnings"])
    assert index["ppt_extractor"]["schema_version"] == MODULE.PPT_LIBRARY_SCHEMA_VERSION
    assert index["assets"][0]["query"].startswith("教学配图")
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
    assert asset["query"].startswith("教学配图")
    assert "detail_prompt" not in asset
    assert "content_prompt" not in asset
    assert "prompt_route" not in asset
    assert "normalized_prompt" not in asset
    assert "source_type" not in asset
    assert "source" not in asset
    assert "ppt_context" not in asset
    assert "vlm_visual_style" not in asset
    assert not asset["context_summary"].startswith(("来自", "图片来自", "该图来自"))


def test_incremental_match_index_flush_does_not_write_embedding_sidecar(tmp_path, monkeypatch):
    library_dir = tmp_path / "materials_library_ppt"
    captured = {}

    def fake_write_ai_image_match_index(db, root, *, write_embedding_index):
        captured["db"] = db
        captured["root"] = root
        captured["write_embedding_index"] = write_embedding_index
        return {"assets": db["assets"]}, root / "strict_reuse_indexes"

    monkeypatch.setattr(MODULE, "write_ai_image_match_index", fake_write_ai_image_match_index)

    report = {"warnings": []}
    index_path = MODULE._write_incremental_match_index(
        assets_by_id={
            "page": {
                "asset_id": "page",
                "asset_kind": "page_image",
                "image_path": "pptx_images/page.png",
                "original_image_path": "pptx_images_original/page.png",
                "query": "generic classroom illustration",
                "strict_reuse_group": "C02_generic_subject_object",
            }
        },
        library_root=library_dir,
        existing_db={"warnings": []},
        teach_root=tmp_path / "teach-kb",
        report=report,
        ppt_asset_source_by_id={},
    )

    assert index_path == library_dir / "strict_reuse_indexes"
    assert captured["write_embedding_index"] is False
    assert report["incremental_match_index_written"] is True


def test_build_ppt_materials_library_writes_source_pptx_refs_to_split_index(tmp_path, monkeypatch):
    _patch_embedding_encoder(monkeypatch)
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
        con.execute(
            "INSERT INTO pptx_files (id, period_id, file_path, file_name, file_size, description) VALUES (?, ?, ?, ?, ?, ?)",
            ("pptx-id", "period-id", "pptx/lesson.pptx", "lesson.pptx", pptx_path.stat().st_size, ""),
        )
        con.commit()
    finally:
        con.close()

    library_dir = tmp_path / "materials_library_ppt"
    build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        use_vlm=False,
        use_keyword_enrichment=False,
        write_match_index=True,
    )

    index = json.loads((library_dir / "strict_reuse_indexes" / "C03_scene_decor_container.json").read_text(encoding="utf-8"))
    refs = index["assets"][0]["source_pptx_refs"]
    assert refs == [
        {
            "pptx_id": "pptx-id",
            "period_id": "period-id",
            "file_path": "pptx/lesson.pptx",
            "file_name": "lesson.pptx",
            "absolute_path": str(pptx_path.resolve()),
            "slide_no": 1,
            "shape_idx": 1,
            "source_media_path": "ppt/media/image1.png",
            "source": "builder",
        }
    ]


def test_build_ppt_materials_library_skips_failed_pptx_and_continues(tmp_path):
    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(image_path)
    _write_minimal_pptx(pptx_dir / "good.pptx", image_path)
    (pptx_dir / "bad.pptx").write_text("not a zip package", encoding="utf-8")

    db, _index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=tmp_path / "materials_library_ppt",
        use_vlm=False,
        use_keyword_enrichment=False,
        write_match_index=False,
    )

    assert db["asset_count"] == 1
    assert report["pptx_count"] == 1
    assert report["failed_pptx_count"] == 1
    assert report["failed_pptx"][0]["pptx_path"].endswith("bad.pptx")
    assert report["failed_pptx"][0]["reason"].startswith("pptx_process_failed:")


def test_build_ppt_materials_library_runs_bucketed_dedupe_before_writing_indexes(tmp_path, monkeypatch):
    _patch_embedding_encoder(monkeypatch)

    class SameC03KeywordClient:
        _model = "fake-c03-classifier"

        def chat(self, messages, temperature=0.0, max_tokens=4096):
            system = messages[0]["content"]
            payload = json.loads(messages[-1]["content"][messages[-1]["content"].index("[") :])
            if "strict_reuse_group" in system:
                return json.dumps(
                    [
                        {
                            "query": item["query"],
                            "strict_reuse_group": "C03_scene_decor_container",
                        }
                        for item in payload
                    ],
                    ensure_ascii=False,
                )
            if "general" in system:
                return json.dumps(
                    [{"query": item["query"], "general": True} for item in payload],
                    ensure_ascii=False,
                )
            return json.dumps(
                [{"query": item["query"], "caption": "blue blank classroom frame"} for item in payload],
                ensure_ascii=False,
            )

    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(first_image)
    Image.new("RGB", (400, 300), (121, 181, 221)).save(second_image)
    _write_two_picture_pptx(pptx_dir / "lesson.pptx", first_image, second_image)

    library_dir = tmp_path / "materials_library_ppt"
    db, _index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        use_vlm=False,
        keyword_client=SameC03KeywordClient(),
        use_keyword_enrichment=True,
        write_match_index=True,
    )

    assert report["kept_asset_count"] == 2
    assert report["dedupe_removed_count"] == 1
    assert report["dedupe_bucket_counts"]["C03"] == 2
    assert Path(report["dedupe_report_path"]).exists()
    assert db["asset_count"] == 1
    assert len(db["assets"][0]["duplicate_asset_ids"]) == 1
    c03_payload = json.loads(
        (library_dir / "strict_reuse_indexes" / "C03_scene_decor_container.json").read_text(encoding="utf-8")
    )
    assert c03_payload["asset_count"] == 1
    assert len(c03_payload["assets"][0]["duplicate_asset_ids"]) == 1


def test_build_ppt_materials_library_archives_c00_images_to_skip_images(tmp_path, monkeypatch):
    _patch_embedding_encoder(monkeypatch)

    class C00KeywordClient:
        _model = "fake-c00-classifier"

        def __init__(self):
            self.calls = []

        def chat(self, messages, temperature=0.0, max_tokens=4096):
            self.calls.append(messages)
            payload = json.loads(messages[-1]["content"][messages[-1]["content"].index("[") :])
            return json.dumps(
                [
                    {
                        "query": item["query"],
                        "strict_reuse_group": "C00_strict_text_problem_skip",
                    }
                    for item in payload
                ],
                ensure_ascii=False,
            )

    teach_kb_root = tmp_path / "teach-kb"
    pptx_dir = teach_kb_root / "data" / "uploads" / "pptx"
    pptx_dir.mkdir(parents=True)
    image_path = tmp_path / "source.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(image_path)
    pptx_path = pptx_dir / "lesson.pptx"
    _write_minimal_pptx(pptx_path, image_path)

    library_dir = tmp_path / "materials_library_ppt"
    db, _index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        use_vlm=False,
        keyword_client=C00KeywordClient(),
        use_keyword_enrichment=True,
        write_match_index=True,
    )

    assert report["skip_image_archive_count"] == 1
    assert report["skip_image_archive_missing_count"] == 0
    assert db["asset_count"] == 1
    asset = db["assets"][0]
    assert asset["strict_reuse_group"] == "C00_strict_text_problem_skip"
    assert asset["image_path"].startswith("skip_images/")
    assert asset["original_image_path"].startswith("skip_images/")
    assert (library_dir / asset["image_path"]).exists()
    assert (library_dir / asset["original_image_path"]).exists()
    assert not (library_dir / "pptx_images" / f"{asset['asset_id']}.png").exists()
    assert not (library_dir / "pptx_images_original" / f"{asset['asset_id']}.png").exists()

    c00_payload = json.loads(
        (library_dir / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json").read_text(encoding="utf-8")
    )
    assert [item["asset_id"] for item in c00_payload["assets"]] == [asset["asset_id"]]
    assert c00_payload["assets"][0]["image_path"].startswith("skip_images/")
    assert c00_payload["assets"][0]["original_image_path"].startswith("skip_images/")

    merged, _split_dir = MODULE.read_ai_image_split_match_index(library_dir)
    assert asset["asset_id"] not in {item["asset_id"] for item in merged["assets"]}
    assert not (library_dir / "ai_image_embedding_meta.json").exists()


def test_ppt_incremental_match_index_writes_c00_before_final_archive(tmp_path):
    library_dir = tmp_path / "materials_library_ppt"
    image_dir = library_dir / "pptx_images"
    image_dir.mkdir(parents=True)
    Image.new("RGB", (400, 300), (120, 180, 220)).save(image_dir / "c00.png")
    asset = {
        "asset_id": "c00",
        "asset_kind": "page_image",
        "image_path": "pptx_images/c00.png",
        "query": "精确文字题目卡片",
        "caption": "精确文字题目卡片",
        "strict_reuse_group": "C00_strict_text_problem_skip",
    }
    report = {"warnings": []}

    MODULE._write_incremental_match_index(
        assets_by_id={"c00": asset},
        library_root=library_dir,
        existing_db={},
        teach_root=tmp_path,
        report=report,
        ppt_asset_source_by_id={},
    )

    c00_payload = json.loads(
        (library_dir / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json").read_text(encoding="utf-8")
    )
    assert c00_payload["asset_count"] == 1
    assert c00_payload["assets"][0]["asset_id"] == "c00"
    assert c00_payload["assets"][0]["image_path"] == "pptx_images/c00.png"
    assert (library_dir / "pptx_images" / "c00.png").exists()
    assert not (library_dir / "skip_images" / "c00.png").exists()


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

    class _BackdropVLM:
        _model = "fake-vlm"

        def chat_vlm_json(self, **kwargs):
            # Background classification now requires the VLM to flag is_backdrop.
            return {
                "query": "浅蓝色渐变全幅背景底图",
                "context_summary": "用作版式底图的浅蓝背景",
                "teaching_intent": "承载叠加文字内容",
                "is_backdrop": True,
            }

    db, index_path, report = build_ppt_image_materials_library(
        teach_kb_root=pptx_dir,
        output_library_dir=library_dir,
        vlm_client=_BackdropVLM(),
        use_vlm=True,
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
    assert asset["normalized_prompt"] == asset["query"]
    background_index = json.loads((library_dir / "strict_reuse_indexes" / "background.json").read_text(encoding="utf-8"))
    general_index = json.loads(
        (library_dir / "strict_reuse_indexes" / "C03_scene_decor_container.json").read_text(encoding="utf-8")
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
                "query": "blue rectangle teaching illustration",
                "context_summary": "visual support image",
                "teaching_intent": "support explanation",
                # Stray old-schema fields must be ignored by the normalizer.
                "vlm_caption": "blue rectangle",
                "vlm_general": True,
                "visual_reuse_group": "C03_scene_decor_container",
                "query_aliases": {"蓝色矩形": [{"alias": "蓝色方块", "confidence": 0.9}]},
                "padding_capacity": "high",
                "vlm_visual_style": {"palette": "blue"},
                "core_keywords": ["should not be used from vlm"],
            }

    class FakeKeywordClient:
        messages = None
        call_count = 0

        def chat(self, messages, temperature=0.0, max_tokens=4096):
            if self.messages is None:
                self.messages = []
            self.messages.append(messages)
            self.call_count += 1
            system = messages[0]["content"]
            if "PPT/deck 级学科与年级字段归一化" in system:
                return json.dumps({"subject": "其他", "grade": "其他", "grade_band": "其他"}, ensure_ascii=False)
            payload = json.loads(messages[-1]["content"][messages[-1]["content"].index("[") :])
            query = payload[0]["query"]
            if "strict_reuse_group" in system or "分类器" in system:
                return json.dumps([{"query": query, "strict_reuse_group": "C02_generic_subject_object"}], ensure_ascii=False)
            if "general" in system:
                return json.dumps([{"query": query, "general": False}], ensure_ascii=False)
            return json.dumps([{"query": query, "caption": "LLM caption"}], ensure_ascii=False)

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
    assert keyword_client.call_count == 4
    assert asset["query"] == "blue rectangle teaching illustration"
    assert "content_prompt" not in asset
    assert "detail_prompt" not in asset
    assert asset["caption"] == "LLM caption"
    assert asset["context_summary"] == "visual support image"
    assert asset["teaching_intent"] == "support explanation"
    assert asset["general"] is False
    assert "query_aliases" not in asset
    assert "padding_capacity" not in asset
    assert asset["strict_reuse_group"] == "C02_generic_subject_object"
    assert asset["strict_reuse_signals"] == ["ppt_independent_llm_classify"]
    assert "transform_advice" not in asset
    for deleted_field in (
        "context_summary_keywords",
        "constraints",
        "core_keywords",
        "semantic_aliases",
        "vlm_caption",
        "vlm_general",
        "llm_general",
        "visual_reuse_group",
    ):
        assert deleted_field not in asset
    assert asset["topic_refs"] == ["lesson"]
    assert asset["duplicate_asset_ids"] == []
    assert report["use_keyword_enrichment"] is True
    assert "prompt_route" not in asset
    assert "normalized_prompt" not in asset
    assert "vlm_visual_style" not in asset
    assert "source_type" not in asset
    system_prompt = vlm_client.messages[0]["content"]
    assert '"query"' in system_prompt
    assert '"context_summary"' in system_prompt
    assert '"teaching_intent"' in system_prompt
    assert '"visual_reuse_group"' not in system_prompt
    assert '"vlm_caption"' not in system_prompt
    assert '"vlm_general"' not in system_prompt
    assert "专名" in system_prompt
    assert "20-40 个汉字" in system_prompt
    assert "不含具体题目文字" in system_prompt
    assert '"content_prompt"' not in system_prompt
    assert '"detail_prompt"' not in system_prompt
    assert "transform_advice" not in system_prompt
    assert "padding_capacity" not in system_prompt
    for disallowed_field in (
        "normalized_prompt",
        "prompt_route",
        "vlm_visual_style",
        "asset_category",
    ):
        assert disallowed_field not in system_prompt
    for messages in keyword_client.messages[1:]:
        user_payload = json.loads(messages[-1]["content"][messages[-1]["content"].index("[") :])
        assert user_payload == [{"query": "blue rectangle teaching illustration"}]


def test_ppt_llm_enrichment_sends_query_only_to_independent_judges():
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def chat(self, messages, temperature=0.0, max_tokens=4096):
            self.calls.append(messages)
            system = messages[0]["content"]
            payload = json.loads(messages[-1]["content"][messages[-1]["content"].index("[") :])
            query = payload[0]["query"]
            if "strict_reuse_group" in system or "分类器" in system:
                return json.dumps([{"query": query, "strict_reuse_group": "C02_generic_subject_object"}], ensure_ascii=False)
            if "general" in system:
                return json.dumps([{"query": query, "general": True}], ensure_ascii=False)
            return json.dumps([{"query": query, "caption": "blue rectangle"}], ensure_ascii=False)

    db = {
        "assets": [
            {
                "asset_id": "ppt_asset",
                "asset_kind": "page_image",
                "query": "blue rectangle teaching illustration",
                "context_summary": "visual support image",
                "teaching_intent": "support explanation",
                "theme": "demo lesson",
                "subject": "language",
                "grade_norm": "grade5",
            }
        ]
    }
    client = RecordingClient()

    MODULE._enrich_ppt_assets_with_llm(db, client, batch_size=10, warnings=[])

    assert len(client.calls) == 3
    for messages in client.calls:
        payload = json.loads(messages[-1]["content"][messages[-1]["content"].index("[") :])
        assert payload == [{"query": "blue rectangle teaching illustration"}]
        assert "theme" not in messages[-1]["content"]
        assert "subject" not in messages[-1]["content"]
        assert "context_summary" not in messages[-1]["content"]
        assert "teaching_intent" not in messages[-1]["content"]


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
        "query": "汉字“傻”生字教学卡，含拼音、田字格、笔画与部首，标注音量图标与连续/分步按钮",
        "context_summary": "汉字“傻”的生字卡，承担课堂上的读音与书写讲解",
        "teaching_intent": "辅助学生识记生字",
        "visual_reuse_group": "C00_strict_text_problem_skip",
        "visual_reuse_confidence": 0.91,
        "visual_reuse_reason": "画面含有具体汉字和拼音内容",
        "query_aliases": {"汉字生字卡": [{"alias": "生字卡", "confidence": 0.9}, {"alias": "character card", "confidence": 0.9}]},
        "transform_advice": {"padding_capacity": "LOW"},
        "visible_text": ["傻"],
        "vlm_visual_style": {"palette": "warm"},
    }

    normalized = MODULE._normalize_annotation(annotation, object(), {}, {})

    assert normalized == {
        "query": "汉字“傻”生字教学卡，含拼音、田字格、笔画与部首，标注音量图标与连续/分步按钮",
        "context_summary": "汉字“傻”的生字卡，承担课堂上的读音与书写讲解",
        "teaching_intent": "辅助学生识记生字",
        "is_backdrop": False,
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


def test_ppt_vlm_prompt_does_not_request_caption_or_general_comparison_fields():
    assert '"vlm_caption"' not in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert '"vlm_general"' not in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert '"visual_reuse_group"' not in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert "关键词" in MODULE.PPT_VLM_SYSTEM_PROMPT


def test_ppt_vlm_prompt_requests_only_query_context_intent():
    assert '"query"' in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert '"context_summary"' in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert '"teaching_intent"' in MODULE.PPT_VLM_SYSTEM_PROMPT
    assert '"general"' not in MODULE.PPT_VLM_SYSTEM_PROMPT


def test_ppt_annotation_drops_boolean_general():
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
            "strict_reuse_group": "C03_scene_decor_container",
            "strict_reuse_confidence": 0.9,
            "strict_reuse_reason": "属于场景装饰容器：空白气泡",
        },
        item,
        meta,
        context,
    )

    assert "general" not in annotation


def test_ppt_annotation_drops_legacy_general_and_visual_group():
    item = _raw_ppt_image_for_general_test()

    annotation = MODULE._normalize_annotation(
        {
            "query": "decorated blank speech bubble sticker",
            "context_summary": "VLM context",
            "teaching_intent": "VLM intent",
            "general": True,
            "visual_reuse_group": "C03_scene_decor_container",
            "visual_reuse_confidence": 0.9,
            "visual_reuse_reason": "blank reusable container",
        },
        item,
        {"file_name": "demo.pptx", "course": {"subject": "language"}},
        {"slide_text": "classroom display", "slide_title_guess": "intro"},
    )

    assert annotation == {
        "query": "decorated blank speech bubble sticker",
        "context_summary": "VLM context",
        "teaching_intent": "VLM intent",
        "is_backdrop": False,
    }


def test_ppt_annotation_drops_vlm_caption():
    item = _raw_ppt_image_for_general_test()

    annotation = MODULE._normalize_annotation(
        {
            "query": "decorated blank speech bubble sticker",
            "vlm_caption": "blank speech bubble",
            "context_summary": "VLM context",
            "teaching_intent": "VLM intent",
            "vlm_general": False,
            "visual_reuse_group": "C03_scene_decor_container",
            "visual_reuse_confidence": 0.9,
            "visual_reuse_reason": "blank reusable container",
        },
        item,
        {},
        {},
    )

    assert annotation == {
        "query": "decorated blank speech bubble sticker",
        "context_summary": "VLM context",
        "teaching_intent": "VLM intent",
        "is_backdrop": False,
    }


def test_ppt_asset_does_not_persist_general_from_annotation():
    item = _raw_ppt_image_for_general_test()
    meta = {"file_name": "demo.pptx", "course": {"subject": "其他", "grade": "五年级"}}
    context = {"slide_text": "课堂展示", "slide_title_guess": "导入"}
    annotation = {
        "query": "带装饰的空白对话气泡贴纸，不含具体文字",
        "context_summary": "空白气泡贴纸用于课堂展示",
        "teaching_intent": "承载可替换文字内容",
        "general": True,
        "visual_reuse_group": "C03_scene_decor_container",
        "visual_reuse_confidence": 0.9,
        "visual_reuse_reason": "属于场景装饰容器：空白气泡",
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

    assert "general" not in asset


class _DeckMetaClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def chat_json(self, *args, **kwargs):
        self.calls += 1
        return self.payload


def test_ppt_deck_metadata_is_normalized_once_and_copied_to_assets():
    item = _raw_ppt_image_for_general_test()
    meta = {
        "file_name": "刷子李.pptx",
        "description": "初二语文课件",
        "subject": "小学语文",
        "grade": "初二",
        "lesson": "《刷子李》",
    }
    client = _DeckMetaClient({"subject": "语文", "grade": "八年级", "grade_band": "高年级"})

    deck_metadata = MODULE._resolve_ppt_deck_metadata(
        meta,
        item.pptx_path,
        "课件内容围绕刷子李人物描写",
        client,
    )
    meta = {**meta, "deck_metadata": deck_metadata}

    first = MODULE._build_asset_from_annotation(
        asset_id="ppt_asset_one",
        image_rel="pptx_images/ppt_asset_one.png",
        original_image_rel="pptx_images_original/ppt_asset_one.png",
        image_fields={
            "actual_width": 400,
            "actual_height": 300,
            "padded_width": 400,
            "padded_height": 300,
            "aspect_ratio": "4:3",
        },
        item=item,
        meta=meta,
        context={"slide_text": "课堂展示", "slide_title_guess": "导入"},
        annotation={
            "query": "人物插画",
            "context_summary": "人物描写插图",
            "teaching_intent": "理解人物形象",
        },
    )
    second = MODULE._build_asset_from_annotation(
        asset_id="ppt_asset_two",
        image_rel="pptx_images/ppt_asset_two.png",
        original_image_rel="pptx_images_original/ppt_asset_two.png",
        image_fields={
            "actual_width": 400,
            "actual_height": 300,
            "padded_width": 400,
            "padded_height": 300,
            "aspect_ratio": "4:3",
        },
        item=item,
        meta=meta,
        context={"slide_text": "课堂展示", "slide_title_guess": "讲解"},
        annotation={
            "query": "课堂配图",
            "context_summary": "课堂讲解配图",
            "teaching_intent": "辅助讲解",
        },
    )

    assert client.calls == 1
    assert deck_metadata == {"subject": "语文", "grade_norm": "八年级", "grade_band": "高年级"}
    for asset in (first, second):
        assert asset["subject"] == "语文"
        assert asset["grade_norm"] == "八年级"
        assert asset["grade_band"] == "高年级"


def test_ppt_asset_does_not_persist_vlm_comparison_fields_from_annotation():
    item = _raw_ppt_image_for_general_test()
    annotation = {
        "query": "decorated blank speech bubble sticker without readable text",
        "vlm_caption": "blank speech bubble",
        "context_summary": "VLM context",
        "teaching_intent": "VLM intent",
        "vlm_general": True,
        "general": True,
        "visual_reuse_group": "C03_scene_decor_container",
        "visual_reuse_confidence": 0.9,
        "visual_reuse_reason": "blank reusable container",
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
        meta={"file_name": "demo.pptx", "course": {"subject": "other", "grade": "grade5"}},
        context={"slide_text": "classroom display", "slide_title_guess": "intro"},
        annotation=annotation,
    )

    assert "vlm_caption" not in asset
    assert "vlm_general" not in asset
    assert "general" not in asset
    assert "visual_reuse_group" not in asset
    assert asset["context_summary"] == "VLM context"
    assert asset["teaching_intent"] == "VLM intent"


def test_ppt_annotation_normalization_maps_legacy_content_prompt_to_query():
    # 旧 annotation 只有 content_prompt（无 query）时，归一化把它映射成 query，且不再产 content_prompt/detail_prompt
    annotation = {
        "content_prompt": "线段图：钢笔5支，铅笔是钢笔的3倍，求铅笔几支",
        "context_summary": "线段图配题，承担二年级倍数应用题的练习展示",
        "teaching_intent": "练习根据线段图列式解倍数问题",
    }

    normalized = MODULE._normalize_annotation(annotation, object(), {}, {})

    assert normalized["query"] == "线段图：钢笔5支，铅笔是钢笔的3倍，求铅笔几支"
    assert "content_prompt" not in normalized
    assert "detail_prompt" not in normalized
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
        "strict_reuse_group": "C02_generic_subject_object",
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
