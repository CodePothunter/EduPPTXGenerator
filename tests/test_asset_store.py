"""R1: AssetStore (sqlite-vec backend) faithfulness — migrate/export/load round-trips."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import edupptx.materials.ai_image_asset_db as db
from edupptx.materials.asset_store import AssetStore


def _diverse_match_index(root: Path) -> dict:
    return {
        "schema_version": 14,
        "asset_root": str(root),
        "assets": [
            {
                "asset_id": "a_c02", "asset_kind": "page_image",
                "strict_reuse_group": "C02_generic_subject_object",
                "image_path": "ai_images/a_c02.png", "caption": "青蛙在荷叶上",
                "subject": "语文", "aspect_ratio": "4:3", "topic_refs": ["小蝌蚪找妈妈"],
                "source_pptx_refs": [{"pptx_id": "p1", "file_name": "lesson.pptx", "slide_no": 3}],
            },
            {
                "asset_id": "a_bg", "asset_kind": "background",
                "strict_reuse_group": "C03_scene_decor_container",
                "image_path": "ai_images/a_bg.png", "normalized_prompt": "水墨山水背景",
                "aspect_ratio": "16:9", "background_route": {"background_color_bias": "蓝绿"},
            },
            {
                "asset_id": "a_c01", "asset_kind": "page_image",
                "strict_reuse_group": "C01_irreplaceable_entity_event_action",
                "image_path": "ai_images/a_c01.png", "caption": "故宫太和殿",
                "subject": "语文", "aspect_ratio": "4:3",
                "strict_reuse_secondary_group": "C03_scene_decor_container",
                "secondary_reuse_query": "宫殿建筑远景", "secondary_reuse_caption": "宫殿建筑远景",
            },
        ],
        "skip_reuse_assets": [
            {
                "asset_id": "a_c00", "asset_kind": "page_image",
                "strict_reuse_group": "C00_strict_text_problem_skip",
                "caption": "乘法竖式", "original_image_path": "skip_images/a_c00_original.png",
            },
        ],
    }


def test_migrate_export_roundtrip_is_faithful(tmp_path):
    """migrate(split) -> export(split) must reproduce the same assembled index + raw files."""
    src = tmp_path / "lib"
    src.mkdir()
    db.write_ai_image_split_match_indexes(_diverse_match_index(src), src)
    index_a, _ = db.read_ai_image_split_match_index(src)

    store = AssetStore(src)
    report = store.migrate_from_split_index(embedding_index=None)
    assert report["asset_rows"] == 5  # C00 + C01 + C02 + C03(projection) + background
    assert report["source_refs"] == 1 and report["topic_refs"] == 1

    out = tmp_path / "exported"
    store.library_root = out
    store.export_to_split_index()
    index_b, _ = db.read_ai_image_split_match_index(out)
    store.close()

    a = json.dumps(index_a["assets"], ensure_ascii=False, sort_keys=True)
    b = json.dumps(index_b["assets"], ensure_ascii=False, sort_keys=True)
    assert a == b  # assembled assets identical (order + content)

    # raw files: C00 archive + C01->C03 projection preserved verbatim
    c03 = json.loads((out / "strict_reuse_indexes" / "C03_scene_decor_container.json").read_text(encoding="utf-8"))
    assert any(asset.get("secondary_projection") for asset in c03["assets"])
    c00 = json.loads((out / "strict_reuse_indexes" / "C00_strict_text_problem_skip.json").read_text(encoding="utf-8"))
    assert [asset["asset_id"] for asset in c00["assets"]] == ["a_c00"]


def test_vector_migrate_load_roundtrip(tmp_path, monkeypatch):
    """npz vectors -> library.db -> load_embedding_index must preserve vectors + color-bias."""
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", "test-model")

    def fake_encode(texts, *, model_name=db.DEFAULT_EMBEDDING_MODEL, query=False):
        out = []
        for t in texts:
            v = np.zeros(8, dtype="float32")
            v[abs(hash(t)) % 8] = 1.0
            out.append(v)
        return np.asarray(out, dtype="float32")

    monkeypatch.setattr(db, "_encode_embedding_texts", fake_encode)

    src = tmp_path / "lib"
    src.mkdir()
    mi = _diverse_match_index(src)
    # drop the skip asset for embedding (C00 not embedded), keep the 3 active
    db.write_ai_image_split_match_indexes(mi, src)
    db.write_ai_image_embedding_index({"assets": mi["assets"]}, src)
    emb_a = db._read_ai_image_embedding_index(src)[0]

    store = AssetStore(src)
    store.migrate_from_split_index(embedding_index=emb_a)
    emb_b = store.load_embedding_index()

    def vec_map(e):
        return {aid: [round(x, 5) for x in e["vectors"][i].tolist()] for i, aid in enumerate(e["asset_ids"])}

    assert vec_map(emb_a) == vec_map(emb_b)
    assert set(emb_a["background_color_bias_asset_ids"]) == set(emb_b["background_color_bias_asset_ids"])
    assert store.doctor()["vec_text_orphans"] == 0
    store.close()


def test_backend_ab_equivalence_find_reusable(tmp_path, monkeypatch):
    """R1 gate: json vs sqlite backend must return identical find_reusable matches."""
    monkeypatch.setenv("EDUPPTX_AI_IMAGE_EMBEDDING_MODEL", "test-model")

    def fake_encode(texts, *, model_name=db.DEFAULT_EMBEDDING_MODEL, query=False):
        out = []
        for t in texts:
            v = np.zeros(16, dtype="float32")
            for ch in str(t):
                v[ord(ch) % 16] += 1.0
            n = np.linalg.norm(v)
            out.append((v / n if n > 0 else v).astype("float32"))
        return np.asarray(out, dtype="float32")

    monkeypatch.setattr(db, "_encode_embedding_texts", fake_encode)

    src = tmp_path / "lib"
    (src / "ai_images").mkdir(parents=True)
    for a in ("a1", "a3"):
        (src / "ai_images" / f"{a}.png").write_bytes(a.encode())
    # both in the default route group (C03) so the un-classified target routes to them
    mi = {
        "schema_version": 14, "asset_root": str(src),
        "assets": [
            {"asset_id": "a1", "asset_kind": "page_image", "strict_reuse_group": "C03_scene_decor_container",
             "image_path": "ai_images/a1.png", "caption": "草原骏马奔腾", "subject": "语文", "aspect_ratio": "4:3", "general": True},
            {"asset_id": "a3", "asset_kind": "page_image", "strict_reuse_group": "C03_scene_decor_container",
             "image_path": "ai_images/a3.png", "caption": "池塘里的小蝌蚪", "subject": "语文", "aspect_ratio": "4:3", "general": True},
        ],
    }
    db.write_ai_image_split_match_indexes(mi, src)
    db.write_ai_image_embedding_index({"assets": mi["assets"]}, src)

    def lookup(prompt):
        return db.find_reusable_ai_image_asset(
            library_dir=(str(src),), asset_kind="page_image", prompt=prompt,
            theme="t", grade="二年级", subject="语文", aspect_ratio="4:3", caption=prompt,
            keyword_client=None, reuse_search_context=db.ReuseSearchContext(), llm_review_enabled=False,
        )

    def desc(m):
        return None if not m else (m["asset"]["asset_id"], round(float(m.get("keyword_score") or 0), 3))

    monkeypatch.setenv("EDUPPTX_REUSE_BACKEND", "json")
    hit_json = desc(lookup("池塘里的小蝌蚪"))     # positive
    miss_json = desc(lookup("完全无关的火箭发射器"))  # negative

    AssetStore(src).migrate_from_split_index(embedding_index=db._read_ai_image_embedding_index(src)[0])

    monkeypatch.setenv("EDUPPTX_REUSE_BACKEND", "sqlite")
    hit_sqlite = desc(lookup("池塘里的小蝌蚪"))
    miss_sqlite = desc(lookup("完全无关的火箭发射器"))

    assert hit_json == hit_sqlite and hit_json is not None and hit_json[0] == "a3"
    assert miss_json == miss_sqlite


def test_library_db_exists_and_default_path(tmp_path):
    from edupptx.materials.asset_store import default_library_db_path, library_db_exists

    src = tmp_path / "lib"
    src.mkdir()
    assert not library_db_exists(src)
    db.write_ai_image_split_match_indexes(_diverse_match_index(src), src)
    AssetStore(src).migrate_from_split_index(embedding_index=None)
    assert library_db_exists(src)
    assert default_library_db_path(src) == src.resolve() / "library.db"
