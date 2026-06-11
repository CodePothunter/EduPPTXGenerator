import json

from edupptx.materials.general_rules import (
    GENERAL_RULE,
    build_general_system_prompt,
    judge_query,
    judge_records,
)


class _FakeClient:
    """Echo each query; general=false if it carries a strong-false token."""

    STRONG_FALSE = ("精读", "请你抬起", "朱自清", "李白", "纪昌", "青铜", "古人", "竖式", "光路")

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        user = messages[-1]["content"]
        arr = json.loads(user[user.index("[") :])
        out = [
            {"query": item["query"], "general": not any(token in item["query"] for token in self.STRONG_FALSE)}
            for item in arr
        ]
        return json.dumps(out, ensure_ascii=False)


def test_general_rule_is_exported():
    assert "general" in GENERAL_RULE


def test_system_prompt_has_invariant_and_scoping():
    prompt = build_general_system_prompt()
    assert "强-false" in prompt and "题材" in prompt and "风格" in prompt
    assert "theme" in prompt and "盲视" in prompt


def test_judge_records_marks_general_bools():
    records = [
        {"query": "抱着橡果的卡通松鼠"},
        {"query": "戴圆框眼镜的朱自清肖像"},
        {"query": "青铜鼎"},
        {"query": "顶部花边的空白装饰文本框"},
    ]
    output = judge_records(records, _FakeClient(), batch_size=10)
    by_query = {record["query"]: record["general"] for record in output}
    assert by_query["抱着橡果的卡通松鼠"] is True
    assert by_query["戴圆框眼镜的朱自清肖像"] is False
    assert by_query["青铜鼎"] is False
    assert by_query["顶部花边的空白装饰文本框"] is True


def test_judge_query_single():
    assert judge_query("手握黄绿色直尺", _FakeClient()) is True
    assert judge_query("绿色对话气泡内“精读”文字", _FakeClient()) is False
