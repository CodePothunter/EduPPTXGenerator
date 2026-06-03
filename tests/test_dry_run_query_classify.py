from scripts.dry_run_query_classify import compare_caption_classification


class _FakeClassifier:
    def classify(self, caption: str) -> str:
        return "C02_generic_subject_object" if "girl" in caption else "C03_scene_decor_container"


def test_reports_mismatches():
    assets = [
        {
            "asset_id": "a1",
            "caption": "girl holding an apple",
            "strict_reuse_group": "C02_generic_subject_object",
        },
        {
            "asset_id": "a2",
            "caption": "foggy city street scene",
            "strict_reuse_group": "C01_irreplaceable_entity_event_action",
        },
    ]

    report = compare_caption_classification(assets, _FakeClassifier())

    assert report["total"] == 2
    assert report["mismatch_count"] == 1
    assert report["mismatches"][0]["asset_id"] == "a2"
    assert report["by_expected_group"]["C01_irreplaceable_entity_event_action"]["mismatch_count"] == 1


def test_classifier_can_return_payload_dict_and_uses_legacy_fallback():
    class PayloadClassifier:
        def classify(self, caption: str) -> dict:
            assert caption == "legacy caption"
            return {"strict_reuse_group": "C03_scene_decor_container"}

    report = compare_caption_classification(
        [
            {
                "asset_id": "legacy",
                "content_prompt": "legacy caption",
                "strict_reuse_group": "C03_scene_decor_container",
            }
        ],
        PayloadClassifier(),
    )

    assert report["total"] == 1
    assert report["mismatch_count"] == 0
