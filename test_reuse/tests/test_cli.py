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


def test_summarize_prints_metrics_block(tmp_path: Path, capsys):
    run_dir = tmp_path / "report" / "run1"
    run_dir.mkdir(parents=True)
    for file_name in (
        "targets.jsonl",
        "hard_filter_pairs.jsonl",
        "scored_candidates.jsonl",
        "threshold_candidates.jsonl",
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
