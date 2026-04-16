import json
from pathlib import Path

from edupptx.session import Session


def test_session_creates_directory_structure(tmp_path):
    session = Session(tmp_path)
    assert session.dir.exists()
    assert (session.dir / "materials").is_dir()
    assert (session.dir / "slides").is_dir()
    assert (session.dir / "slides_raw").is_dir()
    assert session.thinking_file.name == "thinking.jsonl"
    assert session.output_path.name == "output.pptx"


def test_session_creates_slides_raw_dir(tmp_path):
    session = Session(tmp_path)
    assert (session.dir / "slides_raw").is_dir()


def test_session_dir_name_has_timestamp(tmp_path):
    session = Session(tmp_path)
    assert session.dir.name.startswith("session_")
    # Format: session_YYYYMMDD_HHMMSS
    assert len(session.dir.name) == len("session_20260410_143022")


def test_log_step(tmp_path):
    session = Session(tmp_path)
    session.log_step("planning", "Planning 12 slides about 勾股定理")

    lines = session.thinking_file.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["type"] == "planning"
    assert "勾股定理" in entry["content"]
    assert "ts" in entry


def test_log_step_appends(tmp_path):
    session = Session(tmp_path)
    session.log_step("planning", "Step 1")
    session.log_step("material", "Generating background")

    lines = session.thinking_file.read_text().strip().split("\n")
    assert len(lines) == 2


def test_save_plan(tmp_path):
    session = Session(tmp_path)
    plan = {"topic": "勾股定理", "slides": []}
    session.save_plan(plan)

    saved = json.loads((session.dir / "plan.json").read_text())
    assert saved["topic"] == "勾股定理"


def test_save_slide_state(tmp_path):
    session = Session(tmp_path)
    session.save_slide_state(0, "cover", {"title": "封面", "cards": []})
    session.save_slide_state(1, "content", {"title": "内容", "cards": []})

    files = list((session.dir / "slides").glob("*.json"))
    assert len(files) == 2
    assert (session.dir / "slides" / "slide_00_cover.json").exists()
    assert (session.dir / "slides" / "slide_01_content.json").exists()
