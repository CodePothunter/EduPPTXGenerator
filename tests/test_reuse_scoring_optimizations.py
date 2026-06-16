"""Regressions for two behaviour-neutral reuse cleanups.

* R2a-3 — the hybrid ranker reuses the first-pass ``score_details_cache``
  instead of recomputing ``_score_reuse_candidate_details`` for every fused
  candidate (deterministic scoring, so the value is identical).
* M-14  — ``_load_reuse_library_for_search`` is single-flight: concurrent
  first-readers of one library build the shared on-disk index once, not N
  times (the previous double-checked form built outside the lock).
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import edupptx.materials.ai_image_asset_db as ai_db
import edupptx.reuse._scoring as scoring_mod
from edupptx.materials.ai_image_asset_db import (
    ReuseSearchContext,
    _load_reuse_library_for_search,
    _rank_hybrid_reuse_candidates,
)


def _passing_target_and_asset():
    target = {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "16:9",
        "subject": "语文",
        "caption": "single apple subject",
        "content_prompt": "single apple subject",
        "context_summary": "object recognition",
        "grade_norm": "五年级",
        "grade_band": "高年级",
    }
    asset = {**target, "asset_id": "cand-1", "image_path": "ai_images/cand-1.png"}
    return target, asset


def test_rank_hybrid_reuses_cached_score_details(monkeypatch, tmp_path):
    target, asset = _passing_target_and_asset()
    bm25_ranked = [{"asset": asset, "candidate_image_path": "x.png", "keyword_score": 0.5}]

    # Seed the cache under the asset's identity — exactly what the first-pass
    # _rank_reuse_candidates would have stored.
    real_details = ai_db._score_reuse_candidate_details(target, asset)

    calls = {"n": 0}
    # Patch where the function is defined (reuse._scoring): _cached_base_reuse_score_details
    # calls it via its own module global, so patching the ai_image_asset_db re-export
    # would not intercept the internal call after the A5 scoring extraction.
    orig = scoring_mod._score_reuse_candidate_details

    def counting(t, c):
        calls["n"] += 1
        return orig(t, c)

    monkeypatch.setattr(scoring_mod, "_score_reuse_candidate_details", counting)

    cache = {id(asset): real_details}
    results = _rank_hybrid_reuse_candidates(
        target,
        [asset],
        library_root=tmp_path,
        bm25_ranked=bm25_ranked,
        embedding_ranked=[],
        substring_ranked=[],
        threshold=0.0,
        limit=8,
        score_details_cache=cache,
    )
    assert results  # candidate survived ranking
    assert calls["n"] == 0  # cache hit -> no redundant second-pass rescore

    # With no cache the same path recomputes, proving the wiring is what saves it.
    calls["n"] = 0
    _rank_hybrid_reuse_candidates(
        target,
        [asset],
        library_root=tmp_path,
        bm25_ranked=bm25_ranked,
        embedding_ranked=[],
        substring_ranked=[],
        threshold=0.0,
        limit=8,
        score_details_cache=None,
    )
    assert calls["n"] >= 1


def test_load_reuse_library_is_single_flight(monkeypatch, tmp_path):
    ctx = ReuseSearchContext()
    build_calls = {"n": 0}
    count_lock = threading.Lock()
    workers = 8
    start = threading.Barrier(workers)

    def fake_build(root):
        with count_lock:
            build_calls["n"] += 1
        time.sleep(0.05)  # widen the window so a build-outside-lock would overlap
        return {
            "library_root": root,
            "db_path": root,
            "db": {},
            "index": {},
            "match_index_path": root,
            "embedding_index": {},
            "embedding_status": {},
        }

    monkeypatch.setattr(ai_db, "_build_reuse_library_payload", fake_build)

    def load(_):
        start.wait()  # release all readers simultaneously
        return _load_reuse_library_for_search(tmp_path, ctx)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(load, range(workers)))

    assert build_calls["n"] == 1  # built once despite N concurrent first-readers
    assert all(r is results[0] for r in results)  # everyone shares one payload
