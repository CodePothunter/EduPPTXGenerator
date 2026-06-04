from test_reuse.metrics import (
    asset_kind_bucket_stage_metrics,
    candidate_filter_metrics,
    final_match_metrics,
    gold_sets_from_targets,
    hard_filter_stage_metrics,
    llm_review_stage_metrics,
    ranking_metrics,
    relabel_rows_for_gold_sets,
    size_compatible_gold_sets_from_hard_rows,
    size_compatible_gold_summary,
    target_classification_metrics,
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


def test_size_compatible_gold_sets_intersect_gold_with_size_pass_candidate_set():
    targets = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["ok", "size_bad"],
            "best_asset_ids": ["size_bad"],
        },
        {
            "need_id": "n2",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["missing_from_pairs"],
            "best_asset_ids": ["missing_from_pairs"],
        },
    ]
    hard_rows = [
        {
            "need_id": "n1",
            "asset_id": "ok",
            "label_status": "labeled",
            "is_acceptable": True,
            "is_best": False,
            "size_only_pass": True,
        },
        {
            "need_id": "n1",
            "asset_id": "size_bad",
            "label_status": "labeled",
            "is_acceptable": True,
            "is_best": True,
            "size_only_pass": False,
        },
    ]

    original = gold_sets_from_targets(targets)
    adjusted = size_compatible_gold_sets_from_hard_rows(targets, hard_rows)

    assert adjusted["n1"]["acceptable"] == {"ok"}
    assert adjusted["n1"]["best"] == set()
    assert adjusted["n1"]["should_reuse"] is True
    assert adjusted["n2"]["acceptable"] == set()
    assert size_compatible_gold_summary(original, adjusted) == {
        "removed_acceptable_pair_count": 2,
        "removed_best_pair_count": 2,
        "affected_acceptable_need_count": 2,
        "affected_best_need_count": 2,
        "original_reusable_need_count": 2,
        "adjusted_reusable_need_count": 1,
    }


def test_relabel_rows_for_size_compatible_gold_changes_pair_truth_labels():
    gold_sets = {
        "n1": {
            "acceptable": {"kept"},
            "best": {"kept"},
            "should_reuse": True,
        }
    }
    rows = [
        {"need_id": "n1", "asset_id": "removed", "is_acceptable": True, "is_best": True},
        {"need_id": "n1", "asset_id": "kept", "is_acceptable": False, "is_best": False},
    ]

    relabeled = relabel_rows_for_gold_sets(rows, gold_sets)

    assert relabeled[0]["is_acceptable"] is False
    assert relabeled[0]["is_best"] is False
    assert relabeled[1]["is_acceptable"] is True
    assert relabeled[1]["is_best"] is True


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
        {"llm_review_required": True, "llm_review_performed": True, "decision": "accept", "is_acceptable": True},
        {"llm_review_required": True, "llm_review_performed": True, "decision": "accept", "is_acceptable": False},
        {"llm_review_required": True, "llm_review_performed": True, "decision": "reject", "is_acceptable": True},
        {"llm_review_required": False, "llm_review_performed": True, "decision": "reject", "is_acceptable": False},
    ]

    metrics = llm_review_stage_metrics(rows, policy_candidate_count=8)

    assert metrics["reviewed_count"] == 4
    assert metrics["review_candidate_count"] == 4
    assert metrics["llm_review_required_count"] == 3
    assert metrics["policy_candidate_count"] == 8
    assert metrics["llm_review_required_rate"] == 3 / 8
    assert metrics["llm_accept_correctness_rate"] == 0.5
    assert metrics["llm_false_reject_rate"] == 0.5
    assert metrics["llm_wrong_accept_rate"] == 0.5


def test_asset_kind_bucket_stage_metrics_splits_background_and_page_image_gold():
    targets = [
        {
            "need_id": "n1",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["page_gold"],
            "best_asset_ids": ["page_gold"],
            "acceptable_asset_metadata": [
                {"asset_id": "page_gold", "asset_kind": "page_image"},
            ],
            "best_asset_metadata": [
                {"asset_id": "page_gold", "asset_kind": "page_image"},
            ],
        },
        {
            "need_id": "n2",
            "label_status": "labeled",
            "should_reuse": True,
            "acceptable_asset_ids": ["bg_gold"],
            "best_asset_ids": ["bg_gold"],
            "acceptable_asset_metadata": [
                {"asset_id": "bg_gold", "asset_kind": "background"},
            ],
            "best_asset_metadata": [
                {"asset_id": "bg_gold", "asset_kind": "background"},
            ],
        },
    ]
    rows = [
        {
            "need_id": "n1",
            "asset_id": "page_gold",
            "asset_kind": "page_image",
            "threshold_pass": True,
            "rank_hybrid": 1,
        },
        {
            "need_id": "n2",
            "asset_id": "wrong_page",
            "asset_kind": "page_image",
            "threshold_pass": True,
            "rank_hybrid": 1,
        },
        {
            "need_id": "n1",
            "asset_id": "wrong_bg",
            "asset_kind": "background",
            "threshold_pass": True,
            "rank_hybrid": 1,
        },
        {
            "need_id": "n2",
            "asset_id": "bg_gold",
            "asset_kind": "background",
            "threshold_pass": False,
            "rank_hybrid": 2,
        },
    ]

    metrics = asset_kind_bucket_stage_metrics(
        rows,
        targets=targets,
        pass_field="threshold_pass",
    )

    assert metrics["overall"]["candidate_hit_rate"] == 0.5
    assert metrics["page_image"]["pair_metrics"]["tp"] == 1
    assert metrics["page_image"]["pair_metrics"]["fp"] == 1
    assert metrics["page_image"]["pair_metrics"]["fn"] == 0
    assert metrics["background"]["pair_metrics"]["tp"] == 0
    assert metrics["background"]["pair_metrics"]["fp"] == 1
    assert metrics["background"]["pair_metrics"]["fn"] == 1
