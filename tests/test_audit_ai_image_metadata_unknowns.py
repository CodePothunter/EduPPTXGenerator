import csv
import json

from scripts.audit_ai_image_metadata_unknowns import audit_library, main


def test_audit_library_reports_assets_with_other_metadata(tmp_path):
    split_dir = tmp_path / "strict_reuse_indexes"
    split_dir.mkdir()
    (split_dir / "C02_generic_subject_object.json").write_text(
        json.dumps(
            {
                "schema_version": 14,
                "assets": [
                    {
                        "asset_id": "ok",
                        "image_path": "ai_images/ok.png",
                        "subject": "语文",
                        "grade_norm": "五年级",
                        "grade_band": "高年级",
                    },
                    {
                        "asset_id": "unknown_subject",
                        "image_path": "ai_images/unknown.png",
                        "subject": "其他",
                        "grade_norm": "五年级",
                        "grade_band": "高年级",
                        "theme": "五年级语文",
                        "content_prompt": "人物插画",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = audit_library(tmp_path)

    assert report["scanned_assets"] == 2
    assert report["unknown_asset_count"] == 1
    assert report["by_field"] == {"subject": 1, "grade_norm": 0, "grade_band": 0}
    assert report["items"][0]["asset_id"] == "unknown_subject"
    assert report["items"][0]["source"].endswith("C02_generic_subject_object.json")


def test_audit_script_writes_json_and_csv_outputs(tmp_path):
    index_path = tmp_path / "ai_image_match_index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 14,
                "assets": [
                    {
                        "asset_id": "missing_grade",
                        "image_path": "ai_images/missing.png",
                        "subject": "数学",
                        "grade_norm": "",
                        "grade_band": "其他",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    json_output = tmp_path / "unknowns.json"
    csv_output = tmp_path / "unknowns.csv"

    exit_code = main(
        [
            str(tmp_path),
            "--include-missing",
            "--json-output",
            str(json_output),
            "--csv-output",
            str(csv_output),
        ]
    )

    assert exit_code == 1
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["unknown_asset_count"] == 1
    rows = list(csv.DictReader(csv_output.open(encoding="utf-8-sig")))
    assert rows[0]["asset_id"] == "missing_grade"
    assert rows[0]["fields"] == "grade_norm;grade_band"
