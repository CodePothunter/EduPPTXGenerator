import importlib
import json
from pathlib import Path


class _FakeGeneralClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat_json(self, messages, temperature=0.0, max_tokens=4096, max_retries=1):
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "max_retries": max_retries,
            }
        )
        return self.response


class _EchoGeneralClient:
    def __init__(self):
        self.calls = []

    def chat_json(self, messages, temperature=0.0, max_tokens=4096, max_retries=1):
        user_content = messages[1]["content"]
        payload = json.loads(user_content[user_content.index("{") :])
        items = payload["assets"]
        self.calls.append([item["asset_id"] for item in items])
        return {
            "assets": [
                {"asset_id": item["asset_id"], "general": True}
                for item in items
            ]
        }


def _asset(asset_id="a1", prompt="带装饰的空白对话气泡贴纸", subject="其他", general=None):
    asset = {
        "asset_id": asset_id,
        "asset_kind": "page_image",
        "content_prompt": prompt,
        "context_summary": "课堂展示素材",
        "subject": subject,
        "strict_reuse_group": "C05_scene_decor_container",
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
    user_payload = json.loads(messages[1]["content"][messages[1]["content"].index("{") :])
    item = user_payload["assets"][0]

    assert "general" in system
    assert "asset_id、general" in system
    assert "严格保守" in system
    assert "只能根据 content_prompt 判断" in system
    assert "content_prompt 没写出的信息一律视为未知" in system
    assert "不是判断同类型图片是否可替换" in system
    assert "general 决策顺序必须固定：先判断强 general=false，再判断直接 general=true" in system
    assert "强 false 命中时不能被通用白名单覆盖" in system
    assert "具体故事情节、冲突、拒绝、后果、事故、损坏、救助、疾病、痛苦、愤怒、恐惧、孤独等强情绪或故事状态" in system
    assert "具名人物、具名地点、具名建筑、具名工程、IP、品牌、赛事" in system
    assert "学科专用工具或器材" in system
    assert "计算器、圆规、台秤、显微镜、望远镜、天平、实验装置" in system
    assert "通用装饰背景" in system
    assert "通用图标或头像" in system
    assert "普通动物头像" in system
    assert "通用教学或学习动作" in system
    assert "普通校园或课堂场景" in system
    assert "自然可见的人数不是题设" in system
    assert "泛指课文、句子、段落、绘本、书本、资料、页面的朗读、圈画、划线、标序号等普通学习动作" in system
    assert "没有具体可读课程文字、标题、生字拼音或固定语文知识点时可以输出 true" in system
    assert "普通观察、记录、放大、聚焦用途的工具或视觉隐喻" in system
    assert "不展示物理光路、成像原理、公式标签或具体可读文字时可以输出 true" in system
    assert "具体可读文字、公式、光路图、成像原理图、带标注仪器结构" in system
    assert "小朋友坐着大声朗读课文=>true" in system
    assert "小朋友用横线画课文中的句子=>true" in system
    assert "铅笔给课文段落标序号=>true" in system
    assert "带圈画图案和铅笔的打开绘本插画=>true" in system
    assert "五个背书包的小学生并肩同行的背影插画=>true" in system
    assert "中式空白挂轴装饰画=>true" in system
    assert "卡通熊猫头像=>true" in system
    assert "普通森林背景插画=>true" in system
    assert "老花镜=>true" in system
    assert "照相机镜头特写=>true" in system
    assert "放大镜放大文字=>true" in system
    assert "黑色台式电子计算器实物=>false" in system
    assert "圆规绘图工具=>false" in system
    assert "老式台秤（机械杠杆式台秤）=>false" in system
    assert "双目光学显微镜实物=>false" in system
    assert "贵州平塘大窝凼的FAST天眼望远镜=>false" in system
    assert "印有流氓兔图案的长方形布艺枕头=>false" in system
    assert "《比尾巴》课文标题插画=>false" in system
    assert "课文原文段落朗读卡片=>false" in system
    assert "男孩在房间摔东西拒绝出门的场景=>false" in system
    assert "池塘里一群小蝌蚪围着青蛙妈妈游动的卡通场景=>false" in system
    assert "年轻男子坐在轮椅上，表情痛苦愤怒，身旁有被摔碎的杯子=>false" in system
    assert "秋日黄昏，母亲和孩子并肩走在铺满落叶的路上a的温暖场景=>false" in system
    assert "context_summary" not in system
    assert "subject" not in system
    assert "strict_reuse_group" not in system
    assert set(item) == {"asset_id", "content_prompt"}


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
    client = _FakeGeneralClient({"assets": [{"asset_id": "a1", "general": True}]})
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
    assert asset["strict_reuse_group"] == "C05_scene_decor_container"
