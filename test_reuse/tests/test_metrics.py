from test_reuse.metrics import (
    candidate_filter_metrics,
    final_match_metrics,
    gold_sets_from_targets,
    hard_filter_stage_metrics,
    llm_review_stage_metrics,
    ranking_metrics,
    target_classification_metrics,
    threshold_stage_metrics,
)


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


def test_target_classification_metrics_reports_confusion_and_c00_rates():
    rows = [
        {
            "target_strict_reuse_group_gold": "C00_strict_text_problem_skip",
            "strict_reuse_group": "C00_strict_text_problem_skip",
        },
        {
            "target_strict_reuse_group_gold": "C00_strict_text_problem_skip",
            "strict_reuse_group": "C02_generic_subject_object",
        },
        {
            "target_strict_reuse_group_gold": "C02_generic_subject_object",
            "strict_reuse_group": "C00_strict_text_problem_skip",
        },
        {
            "target_strict_reuse_group_gold": "C02_generic_subject_object",
            "strict_reuse_group": "C02_generic_subject_object",
        },
    ]

    metrics = target_classification_metrics(rows)

    assert metrics["total_targets"] == 4
    assert metrics["target_class_accuracy"] == 0.5
    assert metrics["c00_precision"] == 0.5
    assert metrics["c00_recall"] == 0.5
    assert metrics["c00_f1"] == 0.5
    assert metrics["confusion_matrix"]["C00_strict_text_problem_skip"]["C02_generic_subject_object"] == 1


def test_hard_filter_stage_metrics_reports_best_and_loss_reason():
    rows = [
        {
            "need_id": "n1",
            "asset_id": "a1",
            "all_hard_pass": True,
            "is_acceptable": True,
            "is_best": True,
            "reject_reasons": [],
        },
        {
            "need_id": "n1",
            "asset_id": "a2",
            "all_hard_pass": True,
            "is_acceptable": False,
            "is_best": False,
            "reject_reasons": [],
        },
        {
            "need_id": "n2",
            "asset_id": "b1",
            "all_hard_pass": False,
            "is_acceptable": True,
            "is_best": True,
            "reject_reasons": ["subject_mismatch"],
        },
    ]

    metrics = hard_filter_stage_metrics(rows)

    assert metrics["candidate_hit_rate"] == 0.5
    assert metrics["best_hit_rate"] == 0.5
    assert metrics["loss_by_reason"]["subject_mismatch"] == 1
    assert metrics["pair_metrics"]["precision"] == 0.5


def test_threshold_stage_metrics_reports_topk_best_recall():
    rows = [
        {
            "need_id": "n1",
            "asset_id": "a1",
            "threshold_pass": True,
            "rank_hybrid": 1,
            "is_acceptable": False,
            "is_best": False,
        },
        {
            "need_id": "n1",
            "asset_id": "a2",
            "threshold_pass": True,
            "rank_hybrid": 2,
            "is_acceptable": True,
            "is_best": True,
        },
        {
            "need_id": "n2",
            "asset_id": "b1",
            "threshold_pass": False,
            "rank_hybrid": 3,
            "is_acceptable": True,
            "is_best": True,
        },
    ]

    metrics = threshold_stage_metrics(rows)

    assert metrics["candidate_hit_rate"] == 0.5
    assert metrics["best_hit_rate"] == 0.5
    assert metrics["top_1_acceptable_recall"] == 0.0
    assert metrics["top_3_best_recall"] == 1.0


def test_threshold_metrics_counts_missing_gold_candidate_as_false_negative():
    targets = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["gold_a"],
            "best_asset_ids": ["gold_a"],
        }
    ]
    rows = []

    metrics = threshold_stage_metrics(rows, gold_sets=gold_sets_from_targets(targets))

    assert metrics["reusable_need_count"] == 1
    assert metrics["candidate_hit_rate"] == 0.0
    assert metrics["pair_metrics"]["acceptable_pairs"] == 1
    assert metrics["pair_metrics"]["fn"] == 1


def test_final_metrics_reports_correct_none_and_selected_best():
    rows = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "selected_asset_id": "a1",
            "selected_is_acceptable": True,
            "selected_is_best": True,
        },
        {
            "need_id": "n2",
            "label_status": "labeled",
            "should_reuse": False,
            "selected_asset_id": "",
            "selected_is_acceptable": False,
            "selected_is_best": False,
        },
    ]

    metrics = final_match_metrics(rows)

    assert metrics["selected_best_count"] == 1
    assert metrics["selected_best_rate"] == 1.0
    assert metrics["correct_none_count"] == 1
    assert metrics["correct_none_rate"] == 1.0


def test_llm_review_stage_metrics_reports_false_reject_and_wrong_accept():
    rows = [
        {"llm_review_performed": True, "decision": "accept", "is_acceptable": True},
        {"llm_review_performed": True, "decision": "accept", "is_acceptable": False},
        {"llm_review_performed": True, "decision": "reject", "is_acceptable": True},
        {"llm_review_performed": True, "decision": "reject", "is_acceptable": False},
    ]

    metrics = llm_review_stage_metrics(rows)

    assert metrics["reviewed_count"] == 4
    assert metrics["llm_accept_correctness_rate"] == 0.5
    assert metrics["llm_false_reject_rate"] == 0.5
    assert metrics["llm_wrong_accept_rate"] == 0.5
