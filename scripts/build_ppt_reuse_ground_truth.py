"""Build the hand-labeled PPT image reuse gold set for replay evaluation.

The labels in this file were assigned from the saved plan image needs and the
fields in ``materials_library_ppt/strict_reuse_indexes`` only. Existing replay
reports, debug records, and query caches are intentionally not read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "tests" / "fixtures" / "ppt_reuse_ground_truth.json"
LIBRARY_INDEX_ROOT = REPO_ROOT / "materials_library_ppt" / "strict_reuse_indexes"

SESSIONS = [
    "output/session_20260522_211932",
    "output/session_20260522_212237",
    "output/session_20260522_212250",
    "output/session_20260522_212304",
    "output/session_20260523_010029",
    "output/session_20260523_012722",
]

CHRYSANTHEMUM = [
    "kbpptx_895af9e748a80cf26bfa",
    "kbpptx_e5013bc188147563aca3",
    "kbpptx_25f9b5d9a8e11c3270df",
    "kbpptx_30a80ae15ee15d6eb3ea",
    "kbpptx_6db29b6d7923a9fdfaa5",
]
BLANK_CARD = [
    "kbpptx_e35802cf95818b43a646",
    "kbpptx_17665f490f818adca5d1",
    "kbpptx_a85ea884cf1dfefdbd0f",
    "kbpptx_a0e0f507758a07b04671",
    "kbpptx_fea9461a51d4e7ccf30a",
    "kbpptx_5a30f66cd08f2761addc",
    "kbpptx_a76487ae426ea9a284d4",
]
READING_CHILD = [
    "kbpptx_102d16cf898718f57907",
    "kbpptx_4c6bdbee3f540b527010",
    "kbpptx_1945be8ece8a044fd1d5",
]
THINKING_CHILD = [
    "kbpptx_7272685d9b237de64ecf",
    "kbpptx_6c283b5318980f6f89f9",
    "kbpptx_794ff9e1e0a2a3785d5b",
    "kbpptx_be0352cedb7f36b3d869",
    "kbpptx_923d61bb1d9cbcf8cc5f",
]
WRITING_CHILD = [
    "kbpptx_a9561434c200a48289b5",
    "kbpptx_60c135619c73b5c83960",
    "kbpptx_67d761c8bd25056d6eb5",
    "kbpptx_ca45c456ef2cffb5e94b",
    "kbpptx_4e11948812b7e73e3cd5",
]
AUTUMN_LEAVES = [
    "kbpptx_3186e53672dcccc17c4a",
    "kbpptx_e449f63ab22560e5da73",
    "kbpptx_61dc389bf2ea3e02a009",
    "kbpptx_2e9d6022458811b73fbf",
]
TEXTBOOK_PENCIL = [
    "kbpptx_e4c5fa875579e05d9eab",
    "kbpptx_5589b0e8bab8e127846e",
]
RAIN_DROPLET = [
    "kbpptx_63f62475c67d680b3cba",
    "kbpptx_1be9af69c4f0704899d2",
    "kbpptx_aaf639eb344b6fe80c40",
    "kbpptx_c27bedecf95433e16dc4",
]
FROG = [
    "kbpptx_34756847d9db23945136",
    "kbpptx_de8103b22645e843434b",
]
RABBIT = [
    "kbpptx_8a1e887e4080fbfd0d53",
    "kbpptx_c6c5949a3b619582c724",
    "kbpptx_d94a128e4f39518e27dd",
    "kbpptx_ed997f9a4b50ba3a4b24",
    "kbpptx_3862d7da3ad480cb0b4f",
    "kbpptx_372c53cd6511bf5cab85",
]
SQUIRREL = [
    "kbpptx_02620466249ee5d6ef58",
    "kbpptx_78a2297f57ce9d704033",
]
ROOSTER = [
    "kbpptx_9c61301d3cc5dfbf7095",
    "kbpptx_d3e175664f3acb371d6a",
]
DUCK = [
    "kbpptx_1d7e4e4968bf99b96268",
    "kbpptx_b636da5f4a2c3f384b71",
]
PARACHUTE = [
    "kbpptx_9de31fd7f0a278dc4481",
    "kbpptx_47bd9aaef3851d685320",
    "kbpptx_d910d2d6ca2c256a2b6a",
]
FOG_COAST = [
    "kbpptx_24a2469294fb61195ac3",
    "kbpptx_1265ebb2f3ccb342d5da",
    "kbpptx_f71fd2b5a529cc9eb8b7",
    "kbpptx_4ea97e9b9bd7e9f8d106",
]
FOG_FOREST = [
    "kbpptx_8b2ee5de48e121d115b5",
    "kbpptx_4ea97e9b9bd7e9f8d106",
    "kbpptx_29fe0989960f47c176be",
    "kbpptx_f71fd2b5a529cc9eb8b7",
]
LENS_GENERAL = [
    "kbpptx_8dc0022ce119b9cf7ca9",
    "kbpptx_dc20ebf6e689c71b68d2",
    "kbpptx_d40f9a68eb43e949d339",
    "kbpptx_46a682b9cefb74a9981a",
    "kbpptx_556bfc4903ceee3b6900",
    "kbpptx_3617a2df027e1ee95b3d",
    "kbpptx_0d95c0b68820bb27c7a4",
    "kbpptx_33b9136b4011f2741498",
    "kbpptx_7a2b3476f418f568d5ea",
]
OPTICAL_BENCH = [
    "kbpptx_2acba77b02a4d69a8873",
    "kbpptx_10c1b8a8fe1fd864d457",
]
MAGNIFIER = [
    "kbpptx_e453a775e9a1c615b1bc",
    "kbpptx_aacff0f169e7f3952d85",
]


LABELS: dict[str, dict[str, Any]] = {
    "session_20260522_211932:p02:illustration_1": {
        "acceptable": CHRYSANTHEMUM,
        "best": ["kbpptx_895af9e748a80cf26bfa"],
        "notes": "菊花盛放是核心语义；库中没有北海公园场景，大片菊花花丛最贴近。",
    },
    "session_20260522_211932:p07:illustration_1": {
        "acceptable": BLANK_CARD,
        "best": ["kbpptx_e35802cf95818b43a646"],
        "notes": "空白拼音卡底图可由空白记事/便签/文本框类素材承载。",
    },
    "session_20260522_211932:p07:illustration_2": {
        "acceptable": BLANK_CARD,
        "best": ["kbpptx_e35802cf95818b43a646"],
        "notes": "同页重复空白拼音卡底图需求。",
    },
    "session_20260522_211932:p08:illustration_1": {
        "acceptable": BLANK_CARD,
        "best": ["kbpptx_e35802cf95818b43a646"],
        "notes": "空白词语解释卡可复用空白卡片/文本框素材。",
    },
    "session_20260522_211932:p08:illustration_2": {
        "acceptable": BLANK_CARD,
        "best": ["kbpptx_e35802cf95818b43a646"],
        "notes": "同页重复空白词语解释卡需求。",
    },
    "session_20260522_211932:p08:illustration_3": {
        "acceptable": BLANK_CARD,
        "best": ["kbpptx_e35802cf95818b43a646"],
        "notes": "同页重复空白词语解释卡需求。",
    },
    "session_20260522_212237:p01:illustration_1": {
        "acceptable": FROG,
        "best": ["kbpptx_34756847d9db23945136"],
        "notes": "青蛙与荷叶是核心对象；三只青蛙荷叶图最接近大青蛙卡通插画。",
    },
    "session_20260522_212250:p01:illustration_1": {
        "acceptable": AUTUMN_LEAVES,
        "best": ["kbpptx_3186e53672dcccc17c4a"],
        "notes": "库中没有梧桐雨景，秋日黄叶飘落场景可接受。",
    },
    "session_20260522_212250:p01:illustration_2": {
        "acceptable": ["kbpptx_2983b849df61b5658a27"],
        "best": ["kbpptx_2983b849df61b5658a27"],
        "notes": "雨中撑伞学生场景与踩水洼小朋友同属雨天学生场景。",
    },
    "session_20260522_212250:p02:illustration_1": {
        "acceptable": TEXTBOOK_PENCIL,
        "best": ["kbpptx_e4c5fa875579e05d9eab"],
        "notes": "语文课本和铅笔匹配，缺少枫叶但教学物件角色一致。",
    },
    "session_20260522_212250:p06:illustration_1": {
        "acceptable": READING_CHILD,
        "best": ["kbpptx_102d16cf898718f57907"],
        "notes": "儿童共读/读书场景可承担读课文插图。",
    },
    "session_20260522_212250:p07:illustration_2": {
        "acceptable": RAIN_DROPLET,
        "best": ["kbpptx_63f62475c67d680b3cba"],
        "notes": "卡通雨滴形象可作为雨滴生字游戏界面的核心图形。",
    },
    "session_20260522_212250:p09:illustration_1": {
        "acceptable": THINKING_CHILD,
        "best": ["kbpptx_7272685d9b237de64ecf"],
        "notes": "托腮思考的小学生课桌场景最接近思考课文内容。",
    },
    "session_20260522_212250:p12:illustration_2": {
        "acceptable": AUTUMN_LEAVES,
        "best": ["kbpptx_e449f63ab22560e5da73"],
        "notes": "秋叶/枫叶是核心对象，库中没有雨落枫叶特写。",
    },
    "session_20260522_212250:p12:illustration_3": {
        "acceptable": READING_CHILD,
        "best": ["kbpptx_102d16cf898718f57907"],
        "notes": "儿童阅读场景可接受，虽然缺少轻声朗读的明确动作。",
    },
    "session_20260522_212250:p14:illustration_2": {
        "acceptable": WRITING_CHILD,
        "best": ["kbpptx_a9561434c200a48289b5"],
        "notes": "伏案写字小学生最接近田字格本写字场景。",
    },
    "session_20260522_212304:p04:illustration_2": {
        "acceptable": RABBIT,
        "best": ["kbpptx_8a1e887e4080fbfd0d53"],
        "notes": "卡通小白兔形象可复用为兔子角色图。",
    },
    "session_20260522_212304:p04:illustration_3": {
        "acceptable": SQUIRREL,
        "best": ["kbpptx_02620466249ee5d6ef58"],
        "notes": "抱橡果卡通松鼠最适合作为松鼠角色图。",
    },
    "session_20260522_212304:p04:illustration_4": {
        "acceptable": ROOSTER,
        "best": ["kbpptx_9c61301d3cc5dfbf7095"],
        "notes": "白公鸡卡通插画可作为公鸡角色图。",
    },
    "session_20260522_212304:p04:illustration_5": {
        "acceptable": DUCK,
        "best": ["kbpptx_1d7e4e4968bf99b96268"],
        "notes": "水面黄色小鸭子最接近鸭子角色图。",
    },
    "session_20260522_212304:p13:illustration_2": {
        "acceptable": PARACHUTE,
        "best": ["kbpptx_9de31fd7f0a278dc4481"],
        "notes": "红白环形降落伞比结构图/步骤图更适合作为卡通降落伞图片。",
    },
    "session_20260523_010029:p01:background_1": {
        "acceptable": FOG_COAST,
        "best": ["kbpptx_24a2469294fb61195ac3"],
        "notes": "海滨城市与海岸景观最贴近城市海边远景，雾霾/雾景素材作为次优。",
    },
    "session_20260523_010029:p05:illustration_1": {
        "acceptable": ["kbpptx_24a2469294fb61195ac3"],
        "best": ["kbpptx_24a2469294fb61195ac3"],
        "notes": "海滨城市沙滩与海岸景观满足海岸主题。",
    },
    "session_20260523_010029:p05:illustration_3": {
        "acceptable": ["kbpptx_30faa887e62bc701374c"],
        "best": ["kbpptx_30faa887e62bc701374c"],
        "notes": "水上木桥插画与古文字“梁”完全匹配。",
    },
    "session_20260523_010029:p08:illustration_1": {
        "acceptable": [
            "kbpptx_f71fd2b5a529cc9eb8b7",
            "kbpptx_24a2469294fb61195ac3",
            "kbpptx_4ea97e9b9bd7e9f8d106",
        ],
        "best": ["kbpptx_f71fd2b5a529cc9eb8b7"],
        "notes": "雾中水面最接近雾中大海，海岸景观和山间雾景为次优。",
    },
    "session_20260523_010029:p09:illustration_1": {
        "acceptable": FOG_FOREST,
        "best": ["kbpptx_8b2ee5de48e121d115b5"],
        "notes": "雾气笼罩森林实景最接近雾中山林。",
    },
    "session_20260523_010029:p10:illustration_3": {
        "acceptable": WRITING_CHILD + ["kbpptx_0eae49c441f686f2e41b"],
        "best": ["kbpptx_4e11948812b7e73e3cd5"],
        "notes": "正确读写姿势标注图最接近坐端正握笔写字。",
    },
    "session_20260523_010029:p15:illustration_1": {
        "acceptable": ["kbpptx_24a2469294fb61195ac3"],
        "best": ["kbpptx_24a2469294fb61195ac3"],
        "notes": "清晰海滨城市/海岸景观可用于雾散后的海边城市。",
    },
    "session_20260523_010029:p18:illustration_1": {
        "acceptable": ["kbpptx_b9f1f1d86227869f7163"],
        "best": ["kbpptx_b9f1f1d86227869f7163"],
        "notes": "田字格中的“步”字匹配核心书写对象，但缺少笔顺提示。",
    },
    "session_20260523_010029:p18:illustration_2": {
        "acceptable": ["kbpptx_2b2b24c4d1de951cf607"],
        "best": ["kbpptx_2b2b24c4d1de951cf607"],
        "notes": "米字格中的“失”字匹配核心书写对象，但缺少笔顺提示。",
    },
    "session_20260523_012722:p02:illustration_1": {
        "acceptable": LENS_GENERAL,
        "best": ["kbpptx_8dc0022ce119b9cf7ca9"],
        "notes": "凸透镜折射/会聚光路图最贴近课程目录中的会聚示意。",
    },
    "session_20260523_012722:p04:illustration_1": {
        "acceptable": MAGNIFIER,
        "best": ["kbpptx_e453a775e9a1c615b1bc"],
        "notes": "放大镜工具素材可承载放大镜放大文字的生活现象。",
    },
    "session_20260523_012722:p06:illustration_1": {
        "acceptable": [
            "kbpptx_8dc0022ce119b9cf7ca9",
            "kbpptx_dc20ebf6e689c71b68d2",
            "kbpptx_d40f9a68eb43e949d339",
            "kbpptx_46a682b9cefb74a9981a",
        ],
        "best": ["kbpptx_8dc0022ce119b9cf7ca9"],
        "notes": "库中无太阳光白纸实验场景，凸透镜会聚/折射光路图可作为概念替代。",
    },
    "session_20260523_012722:p07:illustration_1": {
        "acceptable": LENS_GENERAL,
        "best": ["kbpptx_dc20ebf6e689c71b68d2"],
        "notes": "焦点、焦距光路示意图最贴近O/F/f主光轴核心概念。",
    },
    "session_20260523_012722:p08:illustration_1": {
        "acceptable": OPTICAL_BENCH,
        "best": ["kbpptx_2acba77b02a4d69a8873"],
        "notes": "光具座、蜡烛、凸透镜、光屏实验装置完全匹配。",
    },
    "session_20260523_012722:p09:illustration_1": {
        "acceptable": LENS_GENERAL,
        "best": ["kbpptx_46a682b9cefb74a9981a"],
        "notes": "库中无u>2f专图，凸透镜特殊光线光路图可作为通用光路替代。",
    },
    "session_20260523_012722:p09:illustration_2": {
        "acceptable": LENS_GENERAL,
        "best": ["kbpptx_46a682b9cefb74a9981a"],
        "notes": "库中无f<u<2f专图，凸透镜特殊光线光路图可作为通用光路替代。",
    },
    "session_20260523_012722:p09:illustration_3": {
        "acceptable": LENS_GENERAL,
        "best": ["kbpptx_46a682b9cefb74a9981a"],
        "notes": "库中无u<f虚像专图，凸透镜特殊光线光路图可作为通用光路替代。",
    },
    "session_20260523_012722:p14:illustration_1": {
        "acceptable": LENS_GENERAL,
        "best": ["kbpptx_dc20ebf6e689c71b68d2"],
        "notes": "库中无u=2f/u=f对比图，焦点焦距光路图可作为特殊点讲解替代。",
    },
    "session_20260523_012722:p17:illustration_1": {
        "acceptable": [
            "kbpptx_46a682b9cefb74a9981a",
            "kbpptx_d40f9a68eb43e949d339",
            "kbpptx_556bfc4903ceee3b6900",
            "kbpptx_3617a2df027e1ee95b3d",
            "kbpptx_8dc0022ce119b9cf7ca9",
            "kbpptx_dc20ebf6e689c71b68d2",
            "kbpptx_0d95c0b68820bb27c7a4",
        ],
        "best": ["kbpptx_46a682b9cefb74a9981a"],
        "notes": "凸透镜三条特殊光线光路图最适合绘制规范页。",
    },
    "session_20260523_012722:p21:illustration_1": {
        "acceptable": MAGNIFIER,
        "best": ["kbpptx_e453a775e9a1c615b1bc"],
        "notes": "放大镜素材可覆盖用凸透镜观察文字的实践部分。",
    },
}


def session_id_for(session_dir: str) -> str:
    return Path(session_dir).name


def iter_image_items(session_dir: str) -> list[dict[str, Any]]:
    session_path = REPO_ROOT / session_dir
    plan = json.loads((session_path / "plan.json").read_text(encoding="utf-8"))
    sid = session_id_for(session_dir)
    rows: list[dict[str, Any]] = []
    for page in plan.get("pages") or []:
        images = ((page.get("material_needs") or {}).get("images") or [])
        counts: dict[str, int] = {}
        for image in images:
            if image.get("source", "ai_generate") != "ai_generate":
                continue
            role = str(image.get("role") or "illustration")
            counts[role] = counts.get(role, 0) + 1
            slot_key = f"{role}_{counts[role]}"
            page_number = int(page.get("page_number") or 0)
            query_id = f"{sid}:p{page_number:02d}:{slot_key}"
            rows.append(
                {
                    "query_id": query_id,
                    "session": session_dir,
                    "page_number": page_number,
                    "page_title": page.get("title"),
                    "slot_key": slot_key,
                    "role": role,
                    "aspect_ratio": image.get("aspect_ratio"),
                    "query": image.get("query"),
                }
            )
    return rows


def library_asset_ids() -> set[str]:
    out: set[str] = set()
    for path in LIBRARY_INDEX_ROOT.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for asset in raw.get("assets") or []:
            if isinstance(asset, dict) and asset.get("asset_id"):
                out.add(str(asset["asset_id"]))
    return out


def build() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for session_dir in SESSIONS:
        for row in iter_image_items(session_dir):
            label = LABELS.get(row["query_id"], {})
            acceptable = list(label.get("acceptable") or [])
            best = list(label.get("best") or [])
            row.update(
                {
                    "acceptable_asset_ids": acceptable,
                    "best_asset_ids": best,
                    "should_reuse": bool(acceptable),
                    "notes": label.get(
                        "notes",
                        "No acceptable strict-index image based on the plan query and indexed asset fields.",
                    ),
                }
            )
            items.append(row)
    return {
        "version": 1,
        "kind": "ppt_ai_image_reuse_ground_truth",
        "scope": {
            "sessions": SESSIONS,
            "library_root": "materials_library_ppt",
            "library_indexes": [
                "materials_library_ppt/strict_reuse_indexes/C00_strict_text_problem_skip.json",
                "materials_library_ppt/strict_reuse_indexes/C00_strict_text_problem_skip.json",
                "materials_library_ppt/strict_reuse_indexes/C00_strict_text_problem_skip.json",
                "materials_library_ppt/strict_reuse_indexes/C01_irreplaceable_entity_event_action.json",
                "materials_library_ppt/strict_reuse_indexes/C02_generic_subject_object.json",
                "materials_library_ppt/strict_reuse_indexes/C03_scene_decor_container.json",
            ],
            "source_rule": "manual labels from plan.json and strict_reuse_indexes fields only",
            "excludes": [
                "reuse_replay_report.json",
                "materials/ai_image_reuse_debug*.json",
                "reuse_query_cache.json",
                "thinking.jsonl",
            ],
        },
        "items": items,
    }


def main() -> int:
    asset_ids = library_asset_ids()
    missing = sorted(
        asset_id
        for label in LABELS.values()
        for asset_id in [*(label.get("acceptable") or []), *(label.get("best") or [])]
        if asset_id not in asset_ids
    )
    if missing:
        raise SystemExit(f"labels reference missing asset ids: {', '.join(missing)}")
    bad_best = [
        query_id
        for query_id, label in LABELS.items()
        if not set(label.get("best") or []).issubset(set(label.get("acceptable") or []))
    ]
    if bad_best:
        raise SystemExit(f"best ids must be a subset of acceptable ids: {', '.join(bad_best)}")

    data = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    print(f"items={len(data['items'])} reusable={sum(1 for item in data['items'] if item['should_reuse'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
