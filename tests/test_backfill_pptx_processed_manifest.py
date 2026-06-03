from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PIL import Image

from scripts.backfill_pptx_processed_manifest import backfill_pptx_processed_manifest
from scripts.build_ppt_materials_library import _asset_id_for_sha, _extract_raw_ppt_images


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_minimal_pptx(pptx_path: Path, image_path: Path) -> None:
    slide_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld><p:spTree>
    <p:pic>
      <p:nvPicPr><p:cNvPr id="2" name="Picture 1"/></p:nvPicPr>
      <p:blipFill><a:blip r:embed="rId1"/></p:blipFill>
      <p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="2743200" cy="2057400"/></a:xfrm></p:spPr>
    </p:pic>
  </p:spTree></p:cSld>
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
    pptx_path.parent.mkdir(parents=True, exist_ok=True)
    import zipfile

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
  <p:cSld><p:spTree>
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
  </p:spTree></p:cSld>
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
    pptx_path.parent.mkdir(parents=True, exist_ok=True)
    import zipfile

    with zipfile.ZipFile(pptx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ppt/presentation.xml", presentation_xml)
        zf.writestr("ppt/slides/slide1.xml", slide_xml)
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", rels_xml)
        zf.write(first_image_path, "ppt/media/image1.png")
        zf.write(second_image_path, "ppt/media/image2.png")


def _create_teach_db(db_path: Path, rows: list[tuple[str, str, str]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute("CREATE TABLE hierarchy (id TEXT, parent_id TEXT, name TEXT, subject TEXT)")
        con.execute(
            "CREATE TABLE pptx_files (id TEXT, period_id TEXT, file_path TEXT, file_name TEXT, file_size INTEGER, description TEXT)"
        )
        con.executemany(
            "INSERT INTO hierarchy (id, parent_id, name, subject) VALUES (?, ?, ?, ?)",
            [
                ("grade", "", "Grade 1", ""),
                ("semester", "grade", "Semester 1", ""),
                ("unit", "semester", "Unit 1", ""),
                ("lesson", "unit", "Lesson A", ""),
                ("period", "lesson", "Period 1", "Math"),
            ],
        )
        con.executemany(
            "INSERT INTO pptx_files (id, period_id, file_path, file_name, file_size, description) VALUES (?, ?, ?, ?, ?, ?)",
            [(pptx_id, "period", file_path, file_name, 0, "") for pptx_id, file_path, file_name in rows],
        )
        con.commit()
    finally:
        con.close()


def _candidate_asset_ids(pptx_path: Path) -> list[str]:
    return [_asset_id_for_sha(item.sha256) for item in _extract_raw_ppt_images(pptx_path)]


def test_backfills_manifest_and_writes_uncertain_debug_entries(tmp_path):
    teach_root = tmp_path / "teach-kb"
    pptx_root = teach_root / "data" / "uploads" / "pptx"
    pptx_root.mkdir(parents=True)
    db_path = teach_root / "data" / "db" / "teach_kb.db"

    confirmed_image = tmp_path / "confirmed.png"
    partial_one = tmp_path / "partial-one.png"
    partial_two = tmp_path / "partial-two.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(confirmed_image)
    Image.new("RGB", (400, 300), (20, 80, 180)).save(partial_one)
    Image.new("RGB", (400, 300), (220, 80, 20)).save(partial_two)

    confirmed_pptx = pptx_root / "confirmed.pptx"
    partial_pptx = pptx_root / "partial.pptx"
    _write_minimal_pptx(confirmed_pptx, confirmed_image)
    _write_two_picture_pptx(partial_pptx, partial_one, partial_two)
    _create_teach_db(
        db_path,
        [
            ("ppt-confirmed", "pptx/confirmed.pptx", "confirmed.pptx"),
            ("ppt-partial", "pptx/partial.pptx", "partial.pptx"),
        ],
    )

    confirmed_id = _candidate_asset_ids(confirmed_pptx)[0]
    partial_ids = _candidate_asset_ids(partial_pptx)
    library = tmp_path / "materials_library_ppt"
    _write_json(
        library / "strict_reuse_indexes" / "C03_scene_decor_container.json",
        {
            "assets": [
                {"asset_id": confirmed_id, "image_path": f"pptx_images/{confirmed_id}.png"},
                {"asset_id": partial_ids[0], "image_path": f"pptx_images/{partial_ids[0]}.png"},
            ]
        },
    )

    report = backfill_pptx_processed_manifest(
        teach_kb_root=pptx_root,
        library_dir=library,
    )

    manifest_path = library / "processed_pptx_manifest.backfilled.jsonl"
    debug_path = library / "debug.json"
    manifest_rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
    debug_payload = json.loads(debug_path.read_text(encoding="utf-8"))

    assert report["confirmed_by_asset_hash_count"] == 1
    assert report["partial_asset_hash_match_count"] == 1
    assert manifest_rows[0]["status"] == "confirmed_by_asset_hash"
    assert manifest_rows[0]["pptx_id"] == "ppt-confirmed"
    assert manifest_rows[0]["candidate_asset_count"] == 1
    assert manifest_rows[0]["matched_asset_count"] == 1
    assert manifest_rows[1]["status"] == "partial_asset_hash_match"
    assert manifest_rows[1]["matched_asset_ids"] == [partial_ids[0]]
    assert manifest_rows[1]["missing_asset_ids"] == [partial_ids[1]]
    assert debug_payload["uncertain_count"] == 1
    assert debug_payload["uncertain_pptx"][0]["pptx_id"] == "ppt-partial"
    assert debug_payload["uncertain_pptx"][0]["status"] == "partial_asset_hash_match"


def test_backfill_treats_no_candidate_images_as_confirmed_skip(tmp_path):
    teach_root = tmp_path / "teach-kb"
    pptx_root = teach_root / "data" / "uploads" / "pptx"
    pptx_root.mkdir(parents=True)
    db_path = teach_root / "data" / "db" / "teach_kb.db"

    tiny = tmp_path / "tiny.png"
    Image.new("RGB", (20, 20), (120, 180, 220)).save(tiny)
    pptx_path = pptx_root / "tiny.pptx"
    _write_minimal_pptx(pptx_path, tiny)
    _create_teach_db(db_path, [("ppt-tiny", "pptx/tiny.pptx", "tiny.pptx")])

    library = tmp_path / "materials_library_ppt"
    report = backfill_pptx_processed_manifest(teach_kb_root=pptx_root, library_dir=library)
    manifest_rows = [
        json.loads(line)
        for line in (library / "processed_pptx_manifest.backfilled.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    debug_payload = json.loads((library / "debug.json").read_text(encoding="utf-8"))

    assert report["no_candidate_images_count"] == 1
    assert manifest_rows[0]["status"] == "no_candidate_images"
    assert debug_payload["uncertain_count"] == 0


def test_backfill_confirms_assets_from_c00_index_and_skip_image_files(tmp_path):
    teach_root = tmp_path / "teach-kb"
    pptx_root = teach_root / "data" / "uploads" / "pptx"
    pptx_root.mkdir(parents=True)
    db_path = teach_root / "data" / "db" / "teach_kb.db"

    c00_image = tmp_path / "c00.png"
    skip_file_image = tmp_path / "skip-file.png"
    Image.new("RGB", (400, 300), (120, 180, 220)).save(c00_image)
    Image.new("RGB", (400, 300), (20, 80, 180)).save(skip_file_image)

    c00_pptx = pptx_root / "c00.pptx"
    skip_file_pptx = pptx_root / "skip-file.pptx"
    _write_minimal_pptx(c00_pptx, c00_image)
    _write_minimal_pptx(skip_file_pptx, skip_file_image)
    _create_teach_db(
        db_path,
        [
            ("ppt-c00", "pptx/c00.pptx", "c00.pptx"),
            ("ppt-skip-file", "pptx/skip-file.pptx", "skip-file.pptx"),
        ],
    )

    c00_id = _candidate_asset_ids(c00_pptx)[0]
    skip_file_id = _candidate_asset_ids(skip_file_pptx)[0]
    library = tmp_path / "materials_library_ppt"
    _write_json(
        library / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json",
        {"assets": [{"asset_id": c00_id, "image_path": f"skip_images/{c00_id}.png"}]},
    )
    skip_dir = library / "skip_image"
    skip_dir.mkdir(parents=True)
    Image.new("RGB", (8, 8), (1, 2, 3)).save(skip_dir / f"{skip_file_id}_2.png")

    report = backfill_pptx_processed_manifest(teach_kb_root=pptx_root, library_dir=library)
    manifest_rows = [
        json.loads(line)
        for line in (library / "processed_pptx_manifest.backfilled.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert report["confirmed_by_asset_hash_count"] == 2
    assert {row["pptx_id"]: row["status"] for row in manifest_rows} == {
        "ppt-c00": "confirmed_by_asset_hash",
        "ppt-skip-file": "confirmed_by_asset_hash",
    }
