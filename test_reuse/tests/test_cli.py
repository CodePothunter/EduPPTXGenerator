import subprocess
import sys


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
