import importlib
import json


def _write_split(path, *, group, assets):
    path.write_text(
        json.dumps(
            {
                "schema_version": 12,
                "strict_reuse_group": group,
                "asset_root": str(path.parent.parent),
                "assets": assets,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_refresh_metadata_reads_all_split_json_files(tmp_path):
    module = importlib.import_module("scripts.refresh_llm_metadata")
    split_dir = tmp_path / "strict_reuse_indexes"
    split_dir.mkdir()
    _write_split(
        split_dir / "C03_scene_decor_container.json",
        group="C03_scene_decor_container",
        assets=[
            {
                "asset_id": "current_page",
                "asset_kind": "page_image",
                "content_prompt": "current prompt",
                "strict_reuse_group": "C03_scene_decor_container",
            }
        ],
    )
    _write_split(
        split_dir / "legacy_custom_group.json",
        group="legacy_custom_group",
        assets=[
            {
                "asset_id": "legacy_page",
                "content_prompt": "legacy prompt",
                "strict_reuse_group": "legacy_custom_group",
            }
        ],
    )
    _write_split(
        split_dir / "background.json",
        group="background",
        assets=[
            {
                "asset_id": "background",
                "asset_kind": "background",
                "content_prompt": "background prompt",
                "strict_reuse_group": "background",
            }
        ],
    )

    db, source_dir = module._read_refresh_source(tmp_path)

    assert source_dir == split_dir
    by_id = {asset["asset_id"]: asset for asset in db["assets"]}
    assert set(by_id) == {"current_page", "legacy_page", "background"}
    assert by_id["legacy_page"]["asset_kind"] == "page_image"
    assert by_id["background"]["asset_kind"] == "background"
    assert db["source_kind"] == "all_split_indexes"


def test_refresh_metadata_apply_updates_all_fields_rebuilds_embedding_and_removes_stale_splits(
    tmp_path,
    monkeypatch,
):
    module = importlib.import_module("scripts.refresh_llm_metadata")
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    report_dir = tmp_path / "report"
    split_dir.mkdir(parents=True)
    stale_path = split_dir / "legacy_custom_group.json"
    _write_split(
        stale_path,
        group="legacy_custom_group",
        assets=[
            {
                "asset_id": "asset",
                "asset_kind": "page_image",
                "content_prompt": "old prompt",
                "context_summary": "old context",
                "teaching_intent": "old intent",
                "subject": "old subject",
                "grade_norm": "old grade",
                "strict_reuse_group": "legacy_custom_group",
            }
        ],
    )
    fake_client = object()
    captured = {}

    class _FakeConfig:
        llm_api_key = "key"
        llm_model = "model"

    def fake_enrich(db, client, *, batch_size, include_match_keywords=False, preserve_existing_context_fields=False):
        assert client is fake_client
        assert batch_size == 1
        assert include_match_keywords is False
        assert preserve_existing_context_fields is False
        asset = db["assets"][0]
        asset.update(
            {
                "content_prompt": "new prompt",
                "context_summary": "new context",
                "teaching_intent": "new intent",
                "subject": "new subject",
                "grade_norm": "new grade",
                "strict_reuse_group": "C00_strict_text_problem_skip",
                "strict_reuse_confidence": 0.94,
                "strict_reuse_reason": "new reason",
            }
        )
        db["keyword_builder"] = {"method": "llm_reuse_metadata_extraction"}
        db["keyword_built_at"] = "2026-05-27T00:00:00+00:00"
        return db

    def fake_write_ai_image_match_index(db, root, *, write_embedding_index):
        captured["db"] = db
        captured["root"] = root
        captured["write_embedding_index"] = write_embedding_index
        canonical_path = split_dir / "C00_strict_text_problem_skip.json"
        _write_split(
            canonical_path,
            group="C00_strict_text_problem_skip",
            assets=db["assets"],
        )
        return {"embedding_index": {"enabled": True, "asset_count": 1}}, split_dir

    monkeypatch.setattr(module.Config, "from_env", staticmethod(lambda _env_file: _FakeConfig()))
    monkeypatch.setattr(module, "create_llm_client", lambda config, web_search=False: fake_client)
    monkeypatch.setattr(module, "enrich_ai_image_asset_db_keywords", fake_enrich)
    monkeypatch.setattr(module, "write_ai_image_match_index", fake_write_ai_image_match_index)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "refresh_llm_metadata.py",
            "--library-dir",
            str(library_dir),
            "--report-dir",
            str(report_dir),
            "--apply",
        ],
    )

    assert module.main() == 0

    asset = captured["db"]["assets"][0]
    assert captured["write_embedding_index"] is True
    assert asset["content_prompt"] == "new prompt"
    assert asset["context_summary"] == "new context"
    assert asset["teaching_intent"] == "new intent"
    assert asset["subject"] == "new subject"
    assert asset["grade_norm"] == "new grade"
    assert asset["strict_reuse_group"] == "C00_strict_text_problem_skip"
    assert not stale_path.exists()
    assert (split_dir / "C00_strict_text_problem_skip.json").exists()
    summary = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "Embedding rebuild" in summary
    assert "content_prompt" in summary


def test_refresh_metadata_parallel_workers_are_capped_and_call_one_asset_per_llm(
    tmp_path,
    monkeypatch,
):
    module = importlib.import_module("scripts.refresh_llm_metadata")
    library_dir = tmp_path / "library"
    split_dir = library_dir / "strict_reuse_indexes"
    report_dir = tmp_path / "report"
    split_dir.mkdir(parents=True)
    _write_split(
        split_dir / "C03_scene_decor_container.json",
        group="C03_scene_decor_container",
        assets=[
            {
                "asset_id": f"asset_{idx}",
                "asset_kind": "page_image",
                "content_prompt": f"old prompt {idx}",
                "strict_reuse_group": "C03_scene_decor_container",
            }
            for idx in range(3)
        ],
    )
    fake_client = object()
    calls = []

    class _FakeConfig:
        llm_api_key = "key"
        llm_model = "model"

    class _FakeExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers
            self.futures = []

        def __enter__(self):
            calls.append({"executor_workers": self.max_workers})
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            future = _FakeFuture(fn(*args, **kwargs))
            self.futures.append(future)
            return future

    class _FakeFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    def fake_as_completed(futures):
        return list(futures)

    def fake_enrich(db, client, *, batch_size, include_match_keywords=False, preserve_existing_context_fields=False):
        assert client is fake_client
        calls.append(
            {
                "asset_count": len(db["assets"]),
                "batch_size": batch_size,
            }
        )
        asset = db["assets"][0]
        asset["content_prompt"] = f"new {asset['asset_id']}"
        asset["strict_reuse_group"] = "C00_strict_text_problem_skip"
        db["keyword_builder"] = {"method": "llm_reuse_metadata_extraction"}
        db["keyword_built_at"] = "2026-05-27T00:00:00+00:00"
        return db

    monkeypatch.setattr(module.Config, "from_env", staticmethod(lambda _env_file: _FakeConfig()))
    monkeypatch.setattr(module, "create_llm_client", lambda config, web_search=False: fake_client)
    monkeypatch.setattr(module, "enrich_ai_image_asset_db_keywords", fake_enrich)
    monkeypatch.setattr(module, "ThreadPoolExecutor", _FakeExecutor)
    monkeypatch.setattr(module, "as_completed", fake_as_completed)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "refresh_llm_metadata.py",
            "--library-dir",
            str(library_dir),
            "--report-dir",
            str(report_dir),
            "--llm-workers",
            "30",
        ],
    )

    assert module.main() == 0

    assert calls[0] == {"executor_workers": 3}
    llm_calls = calls[1:]
    assert len(llm_calls) == 3
    assert all(call == {"asset_count": 1, "batch_size": 1} for call in llm_calls)
    summary = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "- LLM workers: 3" in summary
