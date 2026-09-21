"""Step 16 知识片段读取。"""

from __future__ import annotations

import json

from api.schemas import KnowledgeChunk
from config.settings import KNOWLEDGE_PATH


def load_knowledge_chunks() -> list[KnowledgeChunk]:
    """从 JSON 文件读取当前步知识片段。"""

    with KNOWLEDGE_PATH.open(encoding="utf-8") as file:
        return [KnowledgeChunk.model_validate(item) for item in json.load(file)]
