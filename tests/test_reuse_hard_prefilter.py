"""硬过滤前置化：静态判据 / eligible 剪枝 / 缓存 / 集成 wiring。"""

import json

import edupptx.materials.ai_image_asset_db as db
from edupptx.materials.ai_image_asset_db import _reuse_static_filter_reject_reason


def _target():
    return {
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "16:9",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
    }


def _candidate(**overrides):
    base = {
        "asset_id": "cand",
        "asset_kind": "page_image",
        "strict_reuse_group": "C02_generic_subject_object",
        "aspect_ratio": "16:9",
        "subject": "语文",
        "grade_norm": "五年级",
        "grade_band": "高年级",
    }
    base.update(overrides)
    return base


def test_static_filter_passes_compatible_candidate():
    assert _reuse_static_filter_reject_reason(_target(), _candidate()) == ""


def test_static_filter_ignores_aspect():
    # aspect 差得很远，但静态判据绝不能因 aspect 拒绝（aspect 是每图层）
    assert _reuse_static_filter_reject_reason(_target(), _candidate(aspect_ratio="9:16")) == ""


def test_static_filter_rejects_subject_mismatch():
    assert _reuse_static_filter_reject_reason(_target(), _candidate(subject="数学")) == "subject_mismatch"


def test_static_filter_keeps_general_cross_subject():
    # general=True 对任何学科都兼容，必须保留
    assert _reuse_static_filter_reject_reason(_target(), _candidate(subject="数学", general=True)) == ""


def test_static_filter_rejects_group_mismatch():
    assert _reuse_static_filter_reject_reason(
        _target(), _candidate(strict_reuse_group="C01_irreplaceable_entity_event_action")
    ) == "strict_reuse_group_mismatch"


def test_static_filter_rejects_asset_kind_mismatch():
    assert _reuse_static_filter_reject_reason(_target(), _candidate(asset_kind="background")) == "asset_kind_mismatch"


def test_static_filter_allows_other_subject_as_generic():
    assert _reuse_static_filter_reject_reason(_target(), _candidate(subject="其他")) == ""


def test_eligible_excludes_subject_mismatch_and_keeps_general():
    target = _target()  # 语文, 16:9
    compatible = _candidate(asset_id="ok")
    wrong = _candidate(asset_id="wrong_subject", subject="数学")
    general = _candidate(asset_id="general_any", subject="数学", general=True)
    eligible, summary = db._eligible_reuse_assets(
        target, [compatible, wrong, general], None, "lib", "C02_generic_subject_object"
    )
    assert {c["asset_id"] for c in eligible} == {"ok", "general_any"}
    assert summary["routed_count"] == 3
    assert summary["static_subset_count"] == 2


def test_eligible_excludes_aspect_too_far():
    target = _target()  # 16:9
    far = _candidate(asset_id="far", aspect_ratio="9:16")
    near = _candidate(asset_id="near", aspect_ratio="16:9")
    eligible, summary = db._eligible_reuse_assets(
        target, [far, near], None, "lib", "C02_generic_subject_object"
    )
    assert {c["asset_id"] for c in eligible} == {"near"}
    assert summary["aspect_filtered_count"] == 1
    assert summary["static_subset_count"] == 2  # 两者都过静态层，只 aspect 不同


def test_eligible_keeps_enumerated_cross_aspect_pair():
    target = _target()  # 16:9
    allowed = _candidate(asset_id="allowed", aspect_ratio="4:3")
    eligible, summary = db._eligible_reuse_assets(
        target, [allowed], None, "lib", "C02_generic_subject_object"
    )
    assert {c["asset_id"] for c in eligible} == {"allowed"}
    assert summary["aspect_filtered_count"] == 0


def test_eligible_static_subset_cached_per_group_subject(monkeypatch):
    target_16_9 = _target()
    target_4_3 = {**_target(), "aspect_ratio": "4:3"}
    assets = [_candidate(asset_id="a"), _candidate(asset_id="b", subject="数学")]

    calls = {"n": 0}
    orig = db._reuse_static_filter_reject_reason

    def counting(t, c):
        calls["n"] += 1
        return orig(t, c)

    monkeypatch.setattr(db, "_reuse_static_filter_reject_reason", counting)

    ctx = db.ReuseSearchContext()
    db._eligible_reuse_assets(target_16_9, assets, ctx, "lib", "C02_generic_subject_object")
    first = calls["n"]
    assert first == 2  # 缓存未命中：每个候选评一次
    db._eligible_reuse_assets(target_4_3, assets, ctx, "lib", "C02_generic_subject_object")
    assert calls["n"] == first  # 缓存命中：静态判据不再重跑（仅重做 aspect）
    assert len(ctx.eligible_static_cache) == 1


def test_eligible_empty_when_all_filtered():
    target = _target()
    only_wrong = _candidate(asset_id="x", subject="数学")
    eligible, summary = db._eligible_reuse_assets(
        target, [only_wrong], None, "lib", "C02_generic_subject_object"
    )
    assert eligible == []
    assert summary["eligible_count"] == 0


class _KeywordClient:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, messages=None, *args, **kwargs):
        payload = dict(self.payload)
        if messages:
            data = json.loads(messages[-1]["content"].split("\n", 1)[1])
            payload["asset_id"] = data["assets"][0]["asset_id"]
        return {"assets": [payload]}


def _two_candidate_library(tmp_path):
    image_dir = tmp_path / "ai_images"
    image_dir.mkdir()
    (image_dir / "ok.png").write_bytes(b"ok")
    (image_dir / "wrong.png").write_bytes(b"wrong")
    db.write_ai_image_split_match_indexes(
        {
            "schema_version": 14,
            "asset_root": str(tmp_path),
            "assets": [
                {
                    "asset_id": "ok", "asset_kind": "page_image",
                    "image_path": "ai_images/ok.png", "aspect_ratio": "1:1",
                    "subject": "语文", "grade_norm": "五年级", "grade_band": "高年级",
                    "content_prompt": "红色苹果插画", "context_summary": "识字课水果图",
                    "strict_reuse_group": "C02_generic_subject_object",
                },
                {
                    "asset_id": "wrong_subject", "asset_kind": "page_image",
                    "image_path": "ai_images/wrong.png", "aspect_ratio": "1:1",
                    "subject": "数学", "grade_norm": "五年级", "grade_band": "高年级",
                    "content_prompt": "红色苹果插画", "context_summary": "识字课水果图",
                    "strict_reuse_group": "C02_generic_subject_object",
                },
            ],
        },
        tmp_path,
    )


def _target_keyword_client():
    return _KeywordClient({
        "asset_id": "target", "content_prompt": "红色苹果插画",
        "context_summary": "识字课水果图", "teaching_intent": "识别苹果",
        "subject": "语文", "grade_norm": "五年级", "grade_band": "高年级",
        "strict_reuse_group": "C02_generic_subject_object",
    })


def _run_search(tmp_path, debug_path):
    return db.find_reusable_ai_image_asset(
        library_dir=tmp_path, asset_kind="page_image", prompt="红色苹果插画",
        theme="五年级语文识字课", grade="五年级", subject="语文", aspect_ratio="1:1",
        keyword_client=_target_keyword_client(), debug_path=debug_path,
    )


def _patch_rankers_empty(monkeypatch, seen=None):
    def capture(name):
        def fake(target, assets, *args, **kwargs):
            if seen is not None:
                seen[name] = [c.get("asset_id") for c in assets if isinstance(c, dict)]
            return []
        return fake

    monkeypatch.setattr(db, "_rank_reuse_candidates", capture("bm25"))
    monkeypatch.setattr(db, "_rank_embedding_candidates", capture("embedding"))
    monkeypatch.setattr(db, "_rank_substring_candidates", capture("substring"))


def test_rankers_only_see_eligible_assets(tmp_path, monkeypatch):
    # 修复缺陷 A：subject 不兼容的候选不得进入任何召回器（含 embedding）。
    monkeypatch.setenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", "1")
    _two_candidate_library(tmp_path)
    seen: dict[str, list] = {}
    _patch_rankers_empty(monkeypatch, seen)

    _run_search(tmp_path, debug_path=None)

    for name in ("bm25", "embedding", "substring"):
        assert seen[name] == ["ok"], f"{name} 召回器看到了不合规候选: {seen.get(name)}"


def test_debug_scan_skipped_when_no_debug_path(tmp_path, monkeypatch):
    # 修复缺陷 C：生产路径（debug_path=None）不得跑被丢弃的全量 BM25 调试扫描。
    monkeypatch.setenv("EDUPPTX_DISABLE_AI_IMAGE_EMBEDDINGS", "1")
    _two_candidate_library(tmp_path)
    _patch_rankers_empty(monkeypatch)  # 避免候选进入 policy/LLM-review

    calls = {"n": 0}
    orig = db._collect_reuse_candidate_debug

    def counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(db, "_collect_reuse_candidate_debug", counting)

    _run_search(tmp_path, debug_path=None)
    assert calls["n"] == 0  # 生产路径跳过

    _run_search(tmp_path, debug_path=tmp_path / "reuse_debug.json")
    assert calls["n"] == 1  # 调试路径运行（保留"为何被过滤"可见性）
