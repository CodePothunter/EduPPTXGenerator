import importlib
import json

import pytest


class _FakeClassificationClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat_json(self, *, messages, temperature, max_tokens, max_retries=1):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "max_retries": max_retries,
            }
        )
        return self.response


def test_llm_reclassify_selects_page_images_and_backgrounds_only():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    db = {
        "assets": [
            {"asset_id": "background", "asset_kind": "background"},
            {"asset_id": "page", "asset_kind": "page_image"},
            {"asset_id": "icon", "asset_kind": "icon"},
        ]
    }

    selected = module._select_reclassifiable_assets(db, allow_ids=None)

    assert [asset["asset_id"] for asset in selected] == ["background", "page"]


def test_llm_reclassify_asset_id_allowlist_applies_to_backgrounds():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    db = {
        "assets": [
            {"asset_id": "background", "asset_kind": "background"},
            {"asset_id": "page", "asset_kind": "page_image"},
        ]
    }

    selected = module._select_reclassifiable_assets(db, allow_ids={"background"})

    assert [asset["asset_id"] for asset in selected] == ["background"]


def test_llm_reclassify_loads_query_list_assets_with_optional_expected_groups(tmp_path):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    path = tmp_path / "boundary_cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "query": "frog reunion scene",
                    "expected_strict_reuse_group": "C01_irreplaceable_entity_event_action",
                },
                {
                    "query": "autumn flowers",
                    "expected_strict_reuse_group": "C02_generic_subject_object",
                },
                {"query": "letter grid"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assets = module._read_prompt_list_assets(path)

    assert assets == [
        {
            "asset_id": "prompt_000001",
            "asset_kind": "page_image",
            "query": "frog reunion scene",
            "strict_reuse_group": "",
            "expected_strict_reuse_group": "C01_irreplaceable_entity_event_action",
        },
        {
            "asset_id": "prompt_000002",
            "asset_kind": "page_image",
            "query": "autumn flowers",
            "strict_reuse_group": "",
            "expected_strict_reuse_group": "C02_generic_subject_object",
        },
        {
            "asset_id": "prompt_000003",
            "asset_kind": "page_image",
            "query": "letter grid",
            "strict_reuse_group": "",
        },
    ]


def test_llm_reclassify_writes_query_audit_report(tmp_path):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    assets = [
        {
            "asset_id": "prompt_000001",
            "query": "autumn flowers",
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "expected_strict_reuse_group": "C02_generic_subject_object",
            "strict_reuse_confidence": 0.91,
            "strict_reuse_reason": "incorrectly promoted via lesson context",
        }
    ]

    module._write_prompt_list_audit_report(tmp_path, assets)

    payload = json.loads((tmp_path / "prompt_list_audit.json").read_text(encoding="utf-8"))
    assert payload["asset_count"] == 1
    assert payload["counts"] == {"C01_irreplaceable_entity_event_action": 1}
    assert payload["items"][0]["query"] == "autumn flowers"
    assert payload["items"][0]["review_flags"] == ["expected_group_mismatch"]
    summary = (tmp_path / "prompt_list_audit_summary.md").read_text(encoding="utf-8")
    assert "query" in summary
    assert "expected_group_mismatch" in summary


def test_llm_reclassify_boundary_fixture_loads_expected_groups():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    path = module.REPO_ROOT / "tests" / "fixtures" / "content_prompt_only_boundary_cases.json"

    assets = module._read_prompt_list_assets(path)

    assert len(assets) == 16
    assert all(asset.get("query") for asset in assets)
    assert all("content_prompt" not in asset for asset in assets)


def test_llm_reclassify_classification_prompt_uses_query_only_payload():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    client = _FakeClassificationClient(
        {
            "assets": [
                {
                    "asset_id": "page",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "strict_reuse_confidence": 0.93,
                    "strict_reuse_reason": "query itself is a generic object",
                }
            ]
        }
    )
    asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "theme": "must not be sent",
        "query": "autumn flowers",
        "subject": "must not be sent",
        "grade_norm": "must not be sent",
        "grade_band": "must not be sent",
        "page_type": "content",
        "role": "illustration",
        "aspect_ratio": "16:9",
        "context_summary": "must not be sent",
        "teaching_intent": "must not be sent",
        "strict_reuse_group": "C01_irreplaceable_entity_event_action",
        "content_prompt": "legacy field must not be sent",
    }

    classified, warnings = module._classify_assets_with_llm([asset], client, batch_size=1)

    assert warnings == []
    assert classified[0]["strict_reuse_group"] == "C02_generic_subject_object"
    call = client.calls[0]
    system_prompt = call["messages"][0]["content"]
    user_message = call["messages"][1]["content"]
    user_payload = json.loads(user_message[user_message.index("{") :])
    item = user_payload["assets"][0]
    assert "query" in system_prompt
    for forbidden_key in (
        "theme",
        "subject",
        "grade_norm",
        "grade_band",
        "page_type",
        "image_role",
        "aspect_ratio",
        "context_summary",
        "teaching_intent",
        "strict_reuse_group",
        "content_prompt",
    ):
        assert forbidden_key not in item
    assert item == {
        "asset_id": "page",
        "asset_kind": "page_image",
        "query": "autumn flowers",
    }


def test_llm_reclassify_valid_response_updates_only_classification_fields():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    client = _FakeClassificationClient(
        {
            "assets": [
                {
                    "asset_id": "page",
                    "strict_reuse_group": "C01_irreplaceable_entity_event_action",
                    "strict_reuse_confidence": 0.88,
                    "strict_reuse_reason": "classification only",
                }
            ]
        }
    )
    asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "query": "animals waving goodbye",
        "context_summary": "original context",
        "teaching_intent": "original intent",
        "subject": "language",
        "grade_norm": "grade 1",
        "strict_reuse_group": "C03_scene_decor_container",
        "strict_reuse_confidence": 0.6,
        "strict_reuse_reason": "old classification",
    }

    classified, warnings = module._classify_assets_with_llm([asset], client, batch_size=1)

    assert warnings == []
    assert classified == [
        {
            **asset,
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "strict_reuse_confidence": 0.88,
            "strict_reuse_reason": "classification only",
        }
    ]


def test_llm_reclassify_ignores_non_classification_response_fields():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    client = _FakeClassificationClient(
        {
            "assets": [
                {
                    "asset_id": "page",
                    "query": "LLM returned this extra field",
                    "context_summary": "LLM returned another extra field",
                    "strict_reuse_group": "C01_irreplaceable_entity_event_action",
                    "strict_reuse_confidence": 0.88,
                    "strict_reuse_reason": "classification only",
                }
            ]
        }
    )
    asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "query": "animals waving goodbye",
        "strict_reuse_group": "C03_scene_decor_container",
        "strict_reuse_confidence": 0.6,
        "strict_reuse_reason": "old classification",
    }

    classified, warnings = module._classify_assets_with_llm([asset], client, batch_size=1)

    assert warnings == []
    assert classified == [
        {
            **asset,
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "strict_reuse_confidence": 0.88,
            "strict_reuse_reason": "classification only",
        }
    ]
    assert classified[0]["query"] == asset["query"]
    assert "context_summary" not in classified[0]


def test_llm_reclassify_apply_merges_only_classification_fields(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    library_dir = tmp_path / "library"
    report_dir = tmp_path / "report"
    source_dir = library_dir / "strict_reuse_indexes"
    original_asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "query": "original query",
        "context_summary": "original context",
        "teaching_intent": "original intent",
        "strict_reuse_group": "C03_scene_decor_container",
        "strict_reuse_confidence": 0.6,
        "strict_reuse_reason": "old classification",
    }
    db = {
        "schema_version": 1,
        "assets": [original_asset.copy()],
        "warnings": [],
    }
    fake_client = object()
    captured = {}

    class _FakeConfig:
        llm_api_key = "key"
        llm_model = "model"

    def fake_classify(assets, client, *, batch_size):
        assert client is fake_client
        assert batch_size == module.DEFAULT_KEYWORD_BATCH_SIZE
        return [
            {
                **assets[0],
                "query": "rewritten query must not be applied",
                "context_summary": "rewritten context must not be applied",
                "teaching_intent": "rewritten intent must not be applied",
                "strict_reuse_group": "C00_strict_text_problem_skip",
                "strict_reuse_confidence": 0.91,
                "strict_reuse_reason": "classification only",
            }
        ], []

    def fake_write_ai_image_match_index(updated_db, root, *, write_embedding_index):
        captured["db"] = updated_db
        captured["root"] = root
        captured["write_embedding_index"] = write_embedding_index
        return updated_db, source_dir

    monkeypatch.setattr(module.Config, "from_env", staticmethod(lambda _env_file: _FakeConfig()))
    monkeypatch.setattr(module, "create_llm_client", lambda config, web_search=False: fake_client)
    monkeypatch.setattr(module, "_read_reclassify_source", lambda _library_dir: (db, source_dir))
    monkeypatch.setattr(module, "_classify_assets_with_llm", fake_classify)
    monkeypatch.setattr(module, "write_ai_image_match_index", fake_write_ai_image_match_index)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "dry_run_llm_classify.py",
            "--library-dir",
            str(library_dir),
            "--report-dir",
            str(report_dir),
            "--apply",
        ],
    )

    assert module.main() == 0

    updated_asset = captured["db"]["assets"][0]
    assert updated_asset["query"] == "original query"
    assert updated_asset["context_summary"] == "original context"
    assert updated_asset["teaching_intent"] == "original intent"
    assert updated_asset["strict_reuse_group"] == "C00_strict_text_problem_skip"
    assert updated_asset["strict_reuse_confidence"] == 0.91
    assert updated_asset["strict_reuse_reason"] == "classification only"
    assert "keyword_builder" not in captured["db"]
    assert "keyword_built_at" not in captured["db"]
    assert captured["write_embedding_index"] is False
    summary = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "- Group changed: 1" in summary
    assert "C03_scene_decor_container" in summary
    assert "C00_strict_text_problem_skip" in summary
    diff_rows = [
        json.loads(line)
        for line in (report_dir / "diff.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert diff_rows[0]["metadata_changed"] is False
    assert diff_rows[0]["query"] == "original query"


def test_llm_reclassify_apply_can_explicitly_rebuild_embedding(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    library_dir = tmp_path / "library"
    report_dir = tmp_path / "report"
    source_dir = library_dir / "strict_reuse_indexes"
    db = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "page",
                "asset_kind": "page_image",
                "query": "original query",
                "strict_reuse_group": "C03_scene_decor_container",
            }
        ],
        "warnings": [],
    }
    fake_client = object()
    captured = {}

    class _FakeConfig:
        llm_api_key = "key"
        llm_model = "model"

    def fake_classify(assets, client, *, batch_size):
        return [
            {
                **assets[0],
                "strict_reuse_group": "C00_strict_text_problem_skip",
                "strict_reuse_confidence": 0.91,
                "strict_reuse_reason": "classification only",
            }
        ], []

    def fake_write_ai_image_match_index(updated_db, root, *, write_embedding_index):
        captured["write_embedding_index"] = write_embedding_index
        if write_embedding_index:
            updated_db["embedding_index"] = {"enabled": True, "asset_count": 1}
        return updated_db, source_dir

    monkeypatch.setattr(module.Config, "from_env", staticmethod(lambda _env_file: _FakeConfig()))
    monkeypatch.setattr(module, "create_llm_client", lambda config, web_search=False: fake_client)
    monkeypatch.setattr(module, "_read_reclassify_source", lambda _library_dir: (db, source_dir))
    monkeypatch.setattr(module, "_classify_assets_with_llm", fake_classify)
    monkeypatch.setattr(module, "write_ai_image_match_index", fake_write_ai_image_match_index)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "dry_run_llm_classify.py",
            "--library-dir",
            str(library_dir),
            "--report-dir",
            str(report_dir),
            "--apply",
            "--rebuild-embedding",
        ],
    )

    assert module.main() == 0

    assert captured["write_embedding_index"] is True
    summary = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert '"enabled": true' in summary


def test_llm_reclassify_skip_embedding_rebuild_option_is_removed(monkeypatch):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "dry_run_llm_classify.py",
            "--skip-embedding-rebuild",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        module.parse_args()

    assert excinfo.value.code == 2


def test_llm_reclassify_reads_legacy_split_indexes(tmp_path):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    split_dir = tmp_path / "strict_reuse_indexes"
    split_dir.mkdir()
    (split_dir / "C11_background.json").write_text(
        json.dumps(
            {
                "schema_version": 12,
                "strict_reuse_group": "C11_background",
                "asset_root": str(tmp_path),
                "assets": [
                    {
                        "asset_id": "legacy_background",
                        "asset_kind": "background",
                        "image_path": "ai_images/bg.png",
                        "strict_reuse_group": "C11_background",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (split_dir / "C04_info_diagram.json").write_text(
        json.dumps(
            {
                "schema_version": 12,
                "strict_reuse_group": "C04_info_diagram",
                "asset_root": str(tmp_path),
                "assets": [
                    {
                        "asset_id": "legacy_page",
                        "image_path": "ai_images/page.png",
                        "strict_reuse_group": "C04_info_diagram",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    db, source_dir = module._read_reclassify_source(tmp_path)

    assert source_dir == split_dir
    by_id = {asset["asset_id"]: asset for asset in db["assets"]}
    assert set(by_id) == {"legacy_background", "legacy_page"}
    assert by_id["legacy_background"]["asset_kind"] == "background"
    assert by_id["legacy_page"]["asset_kind"] == "page_image"
    assert db["source_kind"] == "all_split_indexes"


def test_llm_reclassify_merges_all_split_json_when_current_files_exist(tmp_path):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    split_dir = tmp_path / "strict_reuse_indexes"
    split_dir.mkdir()
    (split_dir / "C03_scene_decor_container.json").write_text(
        json.dumps(
            {
                "schema_version": 12,
                "strict_reuse_group": "C03_scene_decor_container",
                "asset_root": str(tmp_path),
                "assets": [
                    {
                        "asset_id": "current_page",
                        "asset_kind": "page_image",
                        "image_path": "ai_images/current.png",
                        "strict_reuse_group": "C03_scene_decor_container",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (split_dir / "C04_info_diagram.json").write_text(
        json.dumps(
            {
                "schema_version": 12,
                "strict_reuse_group": "C04_info_diagram",
                "asset_root": str(tmp_path),
                "assets": [
                    {
                        "asset_id": "legacy_page",
                        "image_path": "ai_images/legacy.png",
                        "strict_reuse_group": "C04_info_diagram",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    db, source_dir = module._read_reclassify_source(tmp_path)

    assert source_dir == split_dir
    by_id = {asset["asset_id"]: asset for asset in db["assets"]}
    assert set(by_id) == {"current_page", "legacy_page"}
    assert by_id["current_page"]["asset_kind"] == "page_image"
    assert by_id["legacy_page"]["asset_kind"] == "page_image"
    assert by_id["legacy_page"]["strict_reuse_group"] == "C04_info_diagram"
    assert db["source_kind"] == "all_split_indexes"


def test_llm_reclassify_direction_counts_use_actual_group_ids():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    counts = {}

    module._increment_direction_count(counts, "C11_background", "C03_scene_decor_container")

    assert list(counts.values()) == [1]
    key = next(iter(counts))
    assert "C11_background" in key
    assert "C03_scene_decor_container" in key
