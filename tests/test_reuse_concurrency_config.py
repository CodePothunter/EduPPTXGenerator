"""复用并发/批量相关配置开关。"""

import edupptx.materials.ai_image_asset_db as db


def test_target_keyword_batch_size_default_is_one(monkeypatch):
    monkeypatch.delenv("EDUPPTX_REUSE_TARGET_KEYWORD_BATCH_SIZE", raising=False)
    assert db._reuse_target_keyword_batch_size() == 1


def test_target_keyword_workers_default_is_fifteen(monkeypatch):
    monkeypatch.delenv("EDUPPTX_REUSE_TARGET_KEYWORD_WORKERS", raising=False)
    assert db._reuse_target_keyword_workers() == 15


def test_target_keyword_config_env_override(monkeypatch):
    monkeypatch.setenv("EDUPPTX_REUSE_TARGET_KEYWORD_BATCH_SIZE", "4")
    monkeypatch.setenv("EDUPPTX_REUSE_TARGET_KEYWORD_WORKERS", "8")
    assert db._reuse_target_keyword_batch_size() == 4
    assert db._reuse_target_keyword_workers() == 8


def test_target_keyword_config_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("EDUPPTX_REUSE_TARGET_KEYWORD_BATCH_SIZE", "oops")
    assert db._reuse_target_keyword_batch_size() == 1


from edupptx.materials.ai_image_asset_db import _review_worker_count, MAX_LLM_REVIEWS_PER_QUERY


def test_review_worker_count_capped_at_budget():
    assert MAX_LLM_REVIEWS_PER_QUERY == 3
    assert _review_worker_count(10) == 3
    assert _review_worker_count(3) == 3
    assert _review_worker_count(2) == 2
    assert _review_worker_count(1) == 1
    assert _review_worker_count(0) == 1


def test_default_reuse_policy_workers_is_five():
    from edupptx.agent import DEFAULT_REUSE_POLICY_WORKERS
    assert DEFAULT_REUSE_POLICY_WORKERS == 5
