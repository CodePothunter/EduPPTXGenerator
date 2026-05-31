from test_reuse.metrics import candidate_filter_metrics, final_match_metrics, ranking_metrics


def test_candidate_filter_metrics_reports_precision_recall_and_rates():
    rows = [
        {"all_hard_pass": True, "is_acceptable": True},
        {"all_hard_pass": True, "is_acceptable": False},
        {"all_hard_pass": False, "is_acceptable": True},
        {"all_hard_pass": False, "is_acceptable": False},
    ]

    metrics = candidate_filter_metrics(rows, pass_field="all_hard_pass")

    assert metrics["total_pairs"] == 4
    assert metrics["passed_pairs"] == 2
    assert metrics["acceptable_pairs"] == 2
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == 0.5
    assert metrics["pass_rate"] == 0.5
    assert metrics["wrong_pass_rate"] == 0.25
    assert metrics["hit_in_passed_rate"] == 0.5
    assert metrics["acceptable_kept_rate"] == 0.5


def test_ranking_metrics_reports_candidate_hit_and_topk_recall():
    rows = [
        {"need_id": "n1", "asset_id": "a1", "rank_hybrid": 2, "is_acceptable": True},
        {"need_id": "n1", "asset_id": "a2", "rank_hybrid": 1, "is_acceptable": False},
        {"need_id": "n2", "asset_id": "b1", "rank_hybrid": 9, "is_acceptable": True},
    ]

    metrics = ranking_metrics(rows, reusable_need_ids={"n1", "n2", "n3"}, rank_field="rank_hybrid")

    assert metrics["reusable_need_count"] == 3
    assert metrics["candidate_hit_need_count"] == 2
    assert metrics["candidate_hit_rate"] == 2 / 3
    assert metrics["top_1_recall"] == 0.0
    assert metrics["top_3_recall"] == 1 / 3
    assert metrics["top_8_recall"] == 1 / 3


def test_final_match_metrics_separates_correct_wrong_and_missed_matches():
    rows = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "selected_asset_id": "a1",
            "selected_is_acceptable": True,
        },
        {
            "need_id": "n2",
            "label_status": "labeled",
            "should_reuse": True,
            "selected_asset_id": "b1",
            "selected_is_acceptable": False,
        },
        {
            "need_id": "n3",
            "label_status": "labeled",
            "should_reuse": True,
            "selected_asset_id": "",
            "selected_is_acceptable": False,
        },
        {
            "need_id": "n4",
            "label_status": "labeled",
            "should_reuse": False,
            "selected_asset_id": "",
            "selected_is_acceptable": False,
        },
    ]

    metrics = final_match_metrics(rows)

    assert metrics["labeled_needs"] == 4
    assert metrics["selected_count"] == 2
    assert metrics["correct_selected_count"] == 1
    assert metrics["wrong_selected_count"] == 1
    assert metrics["missed_reusable_count"] == 1
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 1 / 3
    assert round(metrics["f1"], 4) == 0.4
    assert metrics["correct_match_rate"] == 0.25
    assert metrics["wrong_match_rate"] == 0.25
    assert metrics["no_match_rate"] == 0.5
