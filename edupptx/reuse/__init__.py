"""AI 图片素材复用层（Phase A 结构重构进行中）。

历史上整个复用层集中在 `edupptx/materials/ai_image_asset_db.py`（8896 行）。
本子包按职责把它逐块绞杀出来（自底向上：_util → store → build → retrieve →
decide → review → ingest → api），`ai_image_asset_db.py` 退化为 re-export shim
保持旧 import 路径可用，迁移期行为逐字中性。详见 docs/ARCHITECTURE.md。
"""
