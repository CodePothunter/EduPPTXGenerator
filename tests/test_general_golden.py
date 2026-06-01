import json
import os
from pathlib import Path

import pytest

GOLDEN = json.loads(Path("tests/data/general_golden.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not os.getenv("EDUPPTX_RUN_LLM_GOLDEN"),
    reason="set EDUPPTX_RUN_LLM_GOLDEN=1 to run against real LLM",
)
def test_general_golden_against_real_llm():
    from edupptx.config import Config
    from edupptx.llm_client import create_llm_client
    from edupptx.materials.general_rules import judge_records

    client = create_llm_client(Config.from_env(".env"))
    output = judge_records([{"query": row["query"]} for row in GOLDEN], client, batch_size=12)
    wrong = [
        (row["query"], row["general"], generated["general"])
        for row, generated in zip(GOLDEN, output)
        if generated["general"] != row["general"]
    ]
    assert not wrong, f"general mismatches: {wrong}"


def test_golden_file_is_balanced():
    true_count = sum(1 for row in GOLDEN if row["general"])
    assert true_count >= 4 and (len(GOLDEN) - true_count) >= 4
