from __future__ import annotations

import json
from pathlib import Path

from scripts.rebuild_ppt_materials_embeddings import rebuild_ppt_materials_embeddings


def test_rebuilds_embedding_sidecars_from_existing_split_index(tmp_path, monkeypatch):
    def fake_encode_embedding_texts(texts, **_kwargs):
        import numpy as np

        return np.asarray([[float(index + 1), 0.0, 1.0] for index, _text in enumerate(texts)], dtype="float32")

    monkeypatch.setattr(
        "edupptx.reuse._embedding._encode_embedding_texts",
        fake_encode_embedding_texts,
    )
    library = tmp_path / "materials_library_ppt"
    split = library / "strict_reuse_indexes"
    split.mkdir(parents=True)
    (split / "C03_scene_decor_container.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset_id": "asset-a",
                        "asset_kind": "page_image",
                        "image_path": "pptx_images/asset-a.png",
                        "caption": "blue teaching card",
                        "query": "blue teaching card",
                        "context_summary": "legacy context must not get its own vector",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = rebuild_ppt_materials_embeddings(library_dir=library)

    assert report["ok"] is True
    assert (library / "ai_image_embedding_index.npz").exists()
    assert (library / "ai_image_embedding_meta.json").exists()
    meta = json.loads((library / "ai_image_embedding_meta.json").read_text(encoding="utf-8"))
    assert meta["asset_count"] == 1
    assert meta["model"] == "Qwen3-Embedding-0.6B"
    assert "model_identity" not in meta
    assert "context_asset_count" not in meta
    assert "constraint_asset_count" not in meta
    import numpy as np

    with np.load(library / "ai_image_embedding_index.npz") as data:
        assert "asset_ids" in data.files
        assert "vectors" in data.files
        assert "context_asset_ids" not in data.files
        assert "context_vectors" not in data.files
        assert "constraint_asset_ids" not in data.files
        assert "constraint_texts" not in data.files
        assert "constraint_vectors" not in data.files
