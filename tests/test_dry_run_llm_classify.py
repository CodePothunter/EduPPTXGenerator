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


def test_llm_reclassify_loads_prompt_list_assets_with_optional_expected_groups(tmp_path):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    path = tmp_path / "boundary_cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "content_prompt": "小蝌蚪和青蛙妈妈在荷叶边团聚的温馨场景",
                    "expected_strict_reuse_group": "C01_irreplaceable_entity_event_action",
                },
                {
                    "content_prompt": "北海公园秋天盛开的菊花",
                    "expected_strict_reuse_group": "C02_generic_subject_object",
                },
                {
                    "content_prompt": "真实的海岸照片，海浪拍打着岸边的石头",
                    "expected_strict_reuse_group": "C03_scene_decor_container",
                },
                {"content_prompt": "米字格中的汉字“雨”"},
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
            "content_prompt": "小蝌蚪和青蛙妈妈在荷叶边团聚的温馨场景",
            "strict_reuse_group": "",
            "expected_strict_reuse_group": "C01_irreplaceable_entity_event_action",
        },
        {
            "asset_id": "prompt_000002",
            "asset_kind": "page_image",
            "content_prompt": "北海公园秋天盛开的菊花",
            "strict_reuse_group": "",
            "expected_strict_reuse_group": "C02_generic_subject_object",
        },
        {
            "asset_id": "prompt_000003",
            "asset_kind": "page_image",
            "content_prompt": "真实的海岸照片，海浪拍打着岸边的石头",
            "strict_reuse_group": "",
            "expected_strict_reuse_group": "C03_scene_decor_container",
        },
        {
            "asset_id": "prompt_000004",
            "asset_kind": "page_image",
            "content_prompt": "米字格中的汉字“雨”",
            "strict_reuse_group": "",
        },
    ]


def test_llm_reclassify_prompt_audit_flags_suspicious_boundaries():
    module = importlib.import_module("scripts.dry_run_llm_classify")

    assert module._audit_flags_for_prompt_classification(
        "田字格中的汉字“雨”",
        "C00_strict_text_problem_skip",
    ) == ["c00_possible_short_language_symbol"]
    assert module._audit_flags_for_prompt_classification(
        "小蝌蚪和青蛙妈妈在荷叶边团聚的温馨场景",
        "C02_generic_subject_object",
    ) == ["c04_possible_irreplaceable_action_or_relation"]
    assert module._audit_flags_for_prompt_classification(
        "卡通兔子头像",
        "C01_irreplaceable_entity_event_action",
    ) == ["c03_possible_generic_subject"]
    assert module._audit_flags_for_prompt_classification(
        "词语卡片装饰边框，无文字",
        "C03_scene_decor_container",
    ) == []


def test_llm_reclassify_writes_prompt_audit_report(tmp_path):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    assets = [
        {
            "asset_id": "prompt_000001",
            "content_prompt": "小蝌蚪和青蛙妈妈在荷叶边团聚的温馨场景",
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "strict_reuse_confidence": 0.91,
            "strict_reuse_reason": "不可替代关系事件",
        }
    ]

    module._write_prompt_list_audit_report(tmp_path, assets)

    payload = json.loads((tmp_path / "prompt_list_audit.json").read_text(encoding="utf-8"))
    assert payload["asset_count"] == 1
    assert payload["counts"] == {"C01_irreplaceable_entity_event_action": 1}
    assert payload["items"][0]["review_flags"] == []
    summary = (tmp_path / "prompt_list_audit_summary.md").read_text(encoding="utf-8")
    assert "prompt_000001" in summary
    assert "C01_irreplaceable_entity_event_action" in summary


def test_llm_reclassify_prompt_audit_flags_expected_group_mismatch(tmp_path):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    assets = [
        {
            "asset_id": "prompt_000001",
            "content_prompt": "北海公园秋天盛开的菊花",
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "expected_strict_reuse_group": "C02_generic_subject_object",
            "strict_reuse_confidence": 0.91,
            "strict_reuse_reason": "incorrectly promoted via lesson context",
        },
        {
            "asset_id": "prompt_000002",
            "content_prompt": "小蝌蚪和青蛙妈妈在荷叶边团聚的温馨场景",
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "expected_strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "strict_reuse_confidence": 0.91,
            "strict_reuse_reason": "self-contained relationship event",
        },
    ]

    module._write_prompt_list_audit_report(tmp_path, assets)

    payload = json.loads((tmp_path / "prompt_list_audit.json").read_text(encoding="utf-8"))
    by_id = {item["asset_id"]: item for item in payload["items"]}
    assert by_id["prompt_000001"]["expected_strict_reuse_group"] == "C02_generic_subject_object"
    assert by_id["prompt_000001"]["review_flags"] == ["expected_group_mismatch"]
    assert by_id["prompt_000002"]["expected_strict_reuse_group"] == "C01_irreplaceable_entity_event_action"
    assert by_id["prompt_000002"]["review_flags"] == []
    summary = (tmp_path / "prompt_list_audit_summary.md").read_text(encoding="utf-8")
    assert "expected_group_mismatch" in summary


def test_llm_reclassify_boundary_fixture_loads_expected_groups():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    path = module.REPO_ROOT / "tests" / "fixtures" / "content_prompt_only_boundary_cases.json"

    assets = module._read_prompt_list_assets(path)

    assert len(assets) == 16
    by_prompt = {asset["content_prompt"]: asset for asset in assets}
    expected = {
        "小蝌蚪和青蛙妈妈在荷叶边团聚的温馨场景": "C01_irreplaceable_entity_event_action",
        "卡通雾孩子把大海、太阳、城市一个个藏进身后的口袋里的插画": "C01_irreplaceable_entity_event_action",
        "雾孩子的不同卡通形象，分别是捣蛋鬼、魔术师、小画家的样子": "C01_irreplaceable_entity_event_action",
        "母亲拿着外套，笑着邀请轮椅上的儿子出门": "C01_irreplaceable_entity_event_action",
        "母亲站在门外，贴着墙悄悄听房间里的动静，神情担忧": "C01_irreplaceable_entity_event_action",
        "母亲站在床边温柔劝说男孩的场景": "C01_irreplaceable_entity_event_action",
        "男孩在房间摔东西拒绝出门的场景": "C01_irreplaceable_entity_event_action",
        "年轻男子坐在轮椅上，表情痛苦愤怒，身旁有被摔碎的杯子": "C01_irreplaceable_entity_event_action",
        "年轻人坐在轮椅上背对窗户，姿态低落的插画，氛围压抑": "C01_irreplaceable_entity_event_action",
        "池塘里一群小蝌蚪围着青蛙妈妈游动的卡通场景": "C01_irreplaceable_entity_event_action",
        "史铁生肖像": "C01_irreplaceable_entity_event_action",
        "北海公园秋天盛开的菊花": "C02_generic_subject_object",
        "秋天的雨中飘落的金黄银杏叶": "C02_generic_subject_object",
        "松鼠尾巴撑开像降落伞的卡通插图": "C02_generic_subject_object",
        "小动物们挥手告别的卡通场景": "C02_generic_subject_object",
        "真实的海岸照片，海浪拍打着岸边的石头": "C03_scene_decor_container",
    }
    assert set(by_prompt) == set(expected)
    for prompt, expected_group in expected.items():
        assert by_prompt[prompt]["expected_strict_reuse_group"] == expected_group


def test_llm_reclassify_classification_prompt_uses_content_prompt_only_payload():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    client = _FakeClassificationClient(
        {
            "assets": [
                {
                    "asset_id": "page",
                    "strict_reuse_group": "C02_generic_subject_object",
                    "strict_reuse_confidence": 0.93,
                    "strict_reuse_reason": "content_prompt 自身是可辨识主体对象",
                }
            ]
        }
    )
    asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "theme": "七年级语文《秋天的怀念》课文教学",
        "content_prompt": "北海公园秋天盛开的菊花",
        "subject": "语文",
        "grade_norm": "七年级",
        "grade_band": "初中",
        "page_type": "content",
        "role": "illustration",
        "aspect_ratio": "16:9",
        "context_summary": "must not be sent",
        "teaching_intent": "must not be sent",
        "strict_reuse_group": "C01_irreplaceable_entity_event_action",
    }

    classified, warnings = module._classify_assets_with_llm([asset], client, batch_size=1)

    assert warnings == []
    assert classified[0]["strict_reuse_group"] == "C02_generic_subject_object"
    call = client.calls[0]
    system_prompt = call["messages"][0]["content"]
    user_message = call["messages"][1]["content"]
    user_payload = json.loads(user_message[user_message.index("{") :])
    item = user_payload["assets"][0]
    assert "只能基于 content_prompt" in system_prompt
    assert "只用于定位资产和阅读上下文" not in system_prompt
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
    ):
        assert forbidden_key not in item
    assert item == {
        "asset_id": "page",
        "asset_kind": "page_image",
        "content_prompt": "北海公园秋天盛开的菊花",
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
                    "strict_reuse_reason": "属于角色物件画面：小动物告别场景",
                }
            ]
        }
    )
    asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "content_prompt": "小动物们挥手告别的卡通场景",
        "context_summary": "原上下文",
        "teaching_intent": "原教学用途",
        "subject": "语文",
        "grade_norm": "一年级",
        "strict_reuse_group": "C03_scene_decor_container",
        "strict_reuse_confidence": 0.6,
        "strict_reuse_reason": "旧分类",
    }

    classified, warnings = module._classify_assets_with_llm([asset], client, batch_size=1)

    assert warnings == []
    assert classified == [
        {
            **asset,
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "strict_reuse_confidence": 0.88,
            "strict_reuse_reason": "属于角色物件画面：小动物告别场景",
        }
    ]


def test_llm_reclassify_ignores_non_classification_response_fields():
    module = importlib.import_module("scripts.dry_run_llm_classify")
    client = _FakeClassificationClient(
        {
            "assets": [
                {
                    "asset_id": "page",
                    "content_prompt": "LLM returned this extra field",
                    "context_summary": "LLM returned another extra field",
                    "strict_reuse_group": "C01_irreplaceable_entity_event_action",
                    "strict_reuse_confidence": 0.88,
                    "strict_reuse_reason": "属于角色物件画面：小动物告别场景",
                }
            ]
        }
    )
    asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "content_prompt": "小动物们挥手告别的卡通场景",
        "strict_reuse_group": "C03_scene_decor_container",
        "strict_reuse_confidence": 0.6,
        "strict_reuse_reason": "旧分类",
    }

    classified, warnings = module._classify_assets_with_llm([asset], client, batch_size=1)

    assert warnings == []
    assert classified == [
        {
            **asset,
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
            "strict_reuse_confidence": 0.88,
            "strict_reuse_reason": "属于角色物件画面：小动物告别场景",
        }
    ]
    assert classified[0]["content_prompt"] == asset["content_prompt"]
    assert "context_summary" not in classified[0]


def test_llm_reclassify_apply_merges_only_classification_fields(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.dry_run_llm_classify")
    library_dir = tmp_path / "library"
    report_dir = tmp_path / "report"
    source_dir = library_dir / "strict_reuse_indexes"
    original_asset = {
        "asset_id": "page",
        "asset_kind": "page_image",
        "content_prompt": "original prompt",
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
                "content_prompt": "rewritten prompt must not be applied",
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
    assert updated_asset["content_prompt"] == "original prompt"
    assert updated_asset["context_summary"] == "original context"
    assert updated_asset["teaching_intent"] == "original intent"
    assert updated_asset["strict_reuse_group"] == "C00_strict_text_problem_skip"
    assert updated_asset["strict_reuse_confidence"] == 0.91
    assert updated_asset["strict_reuse_reason"] == "classification only"
    assert "keyword_builder" not in captured["db"]
    assert "keyword_built_at" not in captured["db"]
    assert captured["write_embedding_index"] is False
    summary = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "general→content" not in summary
    assert "content→general" not in summary
    assert "- Group changed: 1" in summary
    assert "- Changed directions: C03_scene_decor_container→C00_strict_text_problem_skip: 1" in summary
    diff_rows = [
        json.loads(line)
        for line in (report_dir / "diff.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert diff_rows[0]["metadata_changed"] is False


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
                "content_prompt": "original prompt",
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

    assert counts["C11_background→C03_scene_decor_container"] == 1
