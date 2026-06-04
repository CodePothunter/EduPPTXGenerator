import sys
import subprocess
from pathlib import Path


def test_cli_help_lists_stage_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "test_reuse", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "prepare" in result.stdout
    assert "hard-filter" in result.stdout
    assert "retrieve" in result.stdout
    assert "review" in result.stdout
    assert "summarize" in result.stdout


def test_prepare_help_lists_allow_llm_flag():
    result = subprocess.run(
        [sys.executable, "-m", "test_reuse", "prepare", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--allow-llm" in result.stdout


def test_hard_filter_help_lists_category_routing_flag():
    result = subprocess.run(
        [sys.executable, "-m", "test_reuse", "hard-filter", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--category-routing" in result.stdout
    assert "merge-c01-c03" in result.stdout


def test_summarize_prints_metrics_block(tmp_path: Path, capsys):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    for file_name in (
        "targets.jsonl",
        "hard_filter_pairs.jsonl",
        "candidate_score_audit.jsonl",
        "final_matches.jsonl",
    ):
        (run_dir / file_name).write_text("", encoding="utf-8")

    from test_reuse.cli import main

    old_argv = sys.argv
    try:
        sys.argv = ["test_reuse", "summarize", "--run-dir", str(run_dir)]
        assert main() == 0
    finally:
        sys.argv = old_argv

    captured = capsys.readouterr()
    assert "Summary complete:" in captured.out
    assert "Final precision:" in captured.out


def test_hard_filter_summary_prints_ablation_metrics(tmp_path: Path, capsys):
    run_dir = tmp_path / "report" / "run1"
    summary_dir = run_dir / "02_hard_filter"
    summary_dir.mkdir(parents=True)
    (summary_dir / "hard_filter_summary.json").write_text(
        """
{
  "stage": {
    "candidate_hit_rate": 0.5,
    "best_hit_rate": 0.25,
    "pair_metrics": {"precision": 0.125}
  },
  "filter_ablation": {
    "size_only": {"candidate_hit_rate": 0.1, "best_hit_rate": 0.2, "pair_metrics": {"precision": 0.3}},
    "subject_only": {"candidate_hit_rate": 0.4, "best_hit_rate": 0.5, "pair_metrics": {"precision": 0.6}},
    "category_only": {"candidate_hit_rate": 0.7, "best_hit_rate": 0.8, "pair_metrics": {"precision": 0.9}},
    "subject_size": {"candidate_hit_rate": 0.11, "best_hit_rate": 0.22, "pair_metrics": {"precision": 0.33}}
  },
  "size_compatible_gold": {
    "stage": {
      "candidate_hit_rate": 0.95,
      "best_hit_rate": 0.85,
      "pair_metrics": {"precision": 0.75}
    },
    "filter_ablation": {
      "size_only": {"candidate_hit_rate": 1.0, "best_hit_rate": 1.0, "pair_metrics": {"precision": 0.7}},
      "subject_only": {"candidate_hit_rate": 0.8, "best_hit_rate": 0.7, "pair_metrics": {"precision": 0.6}},
      "category_only": {"candidate_hit_rate": 0.9, "best_hit_rate": 0.8, "pair_metrics": {"precision": 0.5}},
      "subject_size": {"candidate_hit_rate": 0.77, "best_hit_rate": 0.66, "pair_metrics": {"precision": 0.55}}
    },
    "gold_adjustment": {"removed_acceptable_pair_count": 3}
  }
}
""",
        encoding="utf-8",
    )

    from test_reuse.cli import _print_stage_summary

    _print_stage_summary(run_dir, stage="hard-filter")

    captured = capsys.readouterr()
    assert "raw_gold.stage.candidate_hit_rate: 0.5000" in captured.out
    assert "raw_gold.filter_ablation.subject_size.candidate_hit_rate: 0.1100" in captured.out
    assert "size_gold.stage.candidate_hit_rate: 0.9500" in captured.out
    assert "size_gold.filter_ablation.size_only.candidate_hit_rate: 1.0000" in captured.out
    assert "size_gold.filter_ablation.subject_size.candidate_hit_rate: 0.7700" in captured.out
    assert "size_gold.gold_adjustment.removed_acceptable_pair_count: 3" in captured.out


def test_retrieve_summary_prints_raw_and_size_gold_metrics(tmp_path: Path, capsys):
    run_dir = tmp_path / "report" / "run1"
    summary_dir = run_dir / "03_retrieve"
    summary_dir.mkdir(parents=True)
    (summary_dir / "retrieve_summary.json").write_text(
        """
{
  "ranking": {"candidate_hit_rate": 0.4, "top_8_recall": 0.5},
  "candidate_score_audit": {"candidate_pair_count": 12, "policy_input_pair_count": 8},
  "size_compatible_gold": {"ranking": {"candidate_hit_rate": 0.9, "top_8_recall": 0.8}}
}
""",
        encoding="utf-8",
    )

    from test_reuse.cli import _print_stage_summary

    _print_stage_summary(run_dir, stage="retrieve")

    captured = capsys.readouterr()
    assert "raw_gold.ranking.candidate_hit_rate: 0.4000" in captured.out
    assert "size_gold.ranking.candidate_hit_rate: 0.9000" in captured.out
    assert "candidate_score_audit.candidate_pair_count: 12" in captured.out
    assert "candidate_score_audit.policy_input_pair_count: 8" in captured.out


def test_review_summary_prints_llm_review_counts(tmp_path: Path, capsys):
    run_dir = tmp_path / "report" / "run1"
    summary_dir = run_dir / "04_review"
    summary_dir.mkdir(parents=True)
    (summary_dir / "llm_review_summary.json").write_text(
        """
{
  "policy_candidate_count": 10,
  "review_candidate_count": 4,
  "llm_review_required_count": 3,
  "llm_review_performed_count": 2,
  "reviewed_count": 2,
  "llm_review_required_rate": 0.3,
  "llm_review_performed_rate": 0.2,
  "llm_accept_correctness_rate": 0.5,
  "llm_false_reject_rate": 0.25
}
""",
        encoding="utf-8",
    )

    from test_reuse.cli import _print_stage_summary

    _print_stage_summary(run_dir, stage="review")

    captured = capsys.readouterr()
    assert "policy_candidate_count: 10" in captured.out
    assert "llm_review_required_count: 3" in captured.out
    assert "llm_review_performed_count: 2" in captured.out
    assert "llm_review_performed_rate: 0.2000" in captured.out
