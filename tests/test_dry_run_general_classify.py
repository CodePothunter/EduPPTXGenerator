import importlib
import json
from pathlib import Path


class _FakeGeneralClient:
    def __init__(self, response):
        # judge_records calls client.chat and expects a JSON array string;
        # asset_id is re-attached from the input records by order.
        self.response = response  # list of {"general": bool}
        self.calls = []

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return json.dumps(self.response, ensure_ascii=False)


class _EchoGeneralClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        user_content = messages[1]["content"]
        items = json.loads(user_content[user_content.index("[") :])
        self.calls.append([item.get("query") for item in items])
        return json.dumps([{"general": True} for _ in items], ensure_ascii=False)


def _asset(asset_id="a1", prompt="带装饰的空白对话气泡贴纸", subject="其他", general=None):
    asset = {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "content_prompt": prompt,
        "context_summary": "课堂展示素材",
        "subject": subject,
        "strict_reuse_group": "C03_scene_decor_container",
    }
    if general is not None:
        asset["general"] = general
    return asset


GOLDEN_CASES_PATH = Path(__file__).parent / "fixtures" / "general_classify_golden_cases.json"


def _load_golden_cases():
    return json.loads(GOLDEN_CASES_PATH.read_text(encoding="utf-8"))


def test_general_golden_fixture_covers_user_boundary_cases():
    cases = _load_golden_cases()

    assert cases == {
        "expected_true": [
            {"prompt": "老花镜", "general": True},
            {"prompt": "照相机镜头特写", "general": True},
            {"prompt": "放大镜放大文字", "general": True},
            {"prompt": "1元硬币特写", "general": True},
            {"prompt": "普通硬币实物", "general": True},
            {"prompt": "砝码盒和单个砝码", "general": True},
            {"prompt": "手握黄绿色直尺", "general": True},
            {"prompt": "玻璃温度计实物", "general": True},
            {"prompt": "透明玻璃烧杯", "general": True},
            {"prompt": "小朋友坐着大声朗读课文", "general": True},
            {"prompt": "小朋友用横线画课文中的句子", "general": True},
            {"prompt": "铅笔给课文段落标序号", "general": True},
            {"prompt": "带圈画图案和铅笔的打开绘本插画", "general": True},
            {"prompt": "扎双丸子头的卡通小女孩和红苹果", "general": True},
            {"prompt": "五个背书包的小学生并肩同行的背影插画", "general": True},
            {"prompt": "中式空白挂轴装饰画", "general": True},
            {"prompt": "卡通熊猫头像", "general": True},
            {"prompt": "普通森林背景插画", "general": True},
        ],
        "expected_false": [
            {"prompt": "男孩在房间摔东西拒绝出门的场景", "general": False},
            {"prompt": "池塘里一群小蝌蚪围着青蛙妈妈游动的卡通场景", "general": False},
            {"prompt": "年轻男子坐在轮椅上，表情痛苦愤怒，身旁有被摔碎的杯子", "general": False},
            {"prompt": "秋日黄昏，母亲和孩子并肩走在铺满落叶的路上a的温暖场景", "general": False},
            {"prompt": "《比尾巴》课文标题插画", "general": False},
            {"prompt": "课文原文段落朗读卡片", "general": False},
            {"prompt": "田字格中的生字和拼音", "general": False},
            {"prompt": "古诗文字卡片", "general": False},
            {"prompt": "带固定课文句子的圈画示意图", "general": False},
            {"prompt": "黑色台式电子计算器实物", "general": False},
            {"prompt": "圆规绘图工具", "general": False},
            {"prompt": "老式台秤（机械杠杆式台秤）", "general": False},
            {"prompt": "双目光学显微镜实物", "general": False},
            {"prompt": "贵州平塘大窝凼的FAST天眼望远镜", "general": False},
            {"prompt": "印有流氓兔图案的长方形布艺枕头", "general": False},
        ],
    }
    prompts = [item["prompt"] for group in cases.values() for item in group]
    assert len(prompts) == len(set(prompts))


def test_general_classify_prompt_uses_minimal_input_and_boolean_schema():
    module = importlib.import_module("scripts.dry_run_general_classify")
    messages = module._build_general_messages([_asset()])

    system = messages[0]["content"]
    user_array = json.loads(messages[1]["content"][messages[1]["content"].index("[") :])
    item = user_array[0]

    # GENERAL_RULE injected via build_general_system_prompt: ordered strong-false
    # decision rules + boolean general schema; input is minimal (query only).
    assert "general" in system
    assert "跨学科通用复用判断器" in system
    assert "强-false（命中任一即 false）" in system
    assert "判定顺序：先查强-false" in system
    assert "general（true 或 false）" in system
    assert "strict_reuse_group" not in system
    assert set(item) == {"query"}


def test_general_payload_parser_accepts_only_boolean_general():
    module = importlib.import_module("scripts.dry_run_general_classify")
    warnings = []
    parsed = module._general_payload_by_asset_id(
        {"assets": [{"asset_id": "a1", "general": True}, {"asset_id": "a2", "general": "true"}]},
        warnings,
    )

    assert parsed == {"a1": {"asset_id": "a1", "general": True}}
    assert warnings == ["general payload for a2 missing boolean general"]


def test_classify_assets_with_llm_updates_only_general():
    module = importlib.import_module("scripts.dry_run_general_classify")
    client = _FakeGeneralClient([{"general": True}])
    before = _asset(general=False)

    classified, warnings = module._classify_assets_with_llm([before], client, batch_size=1, workers=1)

    assert warnings == []
    assert classified[0]["general"] is True
    assert classified[0]["content_prompt"] == before["content_prompt"]
    assert classified[0]["subject"] == before["subject"]


def test_classify_assets_with_llm_batches_eight_items_with_workers():
    module = importlib.import_module("scripts.dry_run_general_classify")
    client = _EchoGeneralClient()
    assets = [_asset(asset_id=f"a{i}", general=False) for i in range(17)]

    classified, warnings = module._classify_assets_with_llm(assets, client, batch_size=8, workers=15)

    assert warnings == []
    assert sorted(len(call) for call in client.calls) == [1, 8, 8]
    assert [asset["asset_id"] for asset in classified] == [f"a{i}" for i in range(17)]
    assert all(asset["general"] is True for asset in classified)


def test_parse_args_defaults_to_fifteen_workers(monkeypatch):
    module = importlib.import_module("scripts.dry_run_general_classify")
    monkeypatch.setattr("sys.argv", ["dry_run_general_classify.py"])

    args = module.parse_args()

    assert args.keyword_batch_size == 8
    assert args.workers == 15


def test_apply_general_payload_updates_only_general():
    module = importlib.import_module("scripts.dry_run_general_classify")
    asset = _asset(general=False)

    module._apply_general_payload(asset, {"asset_id": "a1", "general": True})

    assert asset["general"] is True
    assert asset["content_prompt"] == "带装饰的空白对话气泡贴纸"
    assert asset["strict_reuse_group"] == "C03_scene_decor_container"
