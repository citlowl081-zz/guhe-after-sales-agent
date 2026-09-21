"""Step 25：按 RoutePlan 的知识域执行轻量真实检索并生成 citations。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from api.schemas import Citation, RoutePlan


KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "knowledge_chunks.json"
MIN_RETRIEVAL_SCORE = 0.5


class KnowledgeRecord(BaseModel):
    """本步从知识文件读取的可检索片段。"""

    chunk_id: str
    title: str
    source_path: str
    domain: str
    status: str
    keywords: list[str]
    text: str


def load_knowledge_records() -> list[KnowledgeRecord]:
    """从真实知识文件读取片段；依赖缺失或损坏时诚实降级为空知识。"""

    try:
        payload = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [KnowledgeRecord.model_validate(item) for item in payload]
    except (OSError, json.JSONDecodeError, TypeError, ValidationError):
        return []


def normalize_query(query: str) -> str:
    """归一化少量项目常见口语，保持原问题语义不变。"""

    return (
        query.lower()
        .replace("叠券", "叠加 优惠券")
        .replace("会员券", "优惠券")
        .replace("还能退", "退货")
        .replace("能退吗", "退货")
        .replace("不合适", "退货")
    )


def retrieve_knowledge(route_plan: RoutePlan) -> list[tuple[KnowledgeRecord, float]]:
    """按 knowledge_domains 过滤并用查询实际命中词计算检索分。"""

    if not route_plan.needs_rag or not route_plan.knowledge_domains:
        return []

    query = normalize_query(route_plan.rag_query)
    allowed_domains = set(route_plan.knowledge_domains)
    hits: list[tuple[KnowledgeRecord, float]] = []
    for record in load_knowledge_records():
        if record.domain not in allowed_domains:
            continue
        matched = {keyword for keyword in record.keywords if keyword.lower() in query}
        if not matched:
            continue
        status_adjustment = 0.05 if record.status == "current" else -0.2
        score = round(max(0.0, min(0.95, 0.45 + 0.1 * len(matched) + status_adjustment)), 3)
        if score >= MIN_RETRIEVAL_SCORE:
            hits.append((record, score))

    # 每个知识域只保留最可靠的一条，避免固定 top-k 把无关片段塞进回答。
    best_by_domain: dict[str, tuple[KnowledgeRecord, float]] = {}
    for record, score in sorted(hits, key=lambda item: item[1], reverse=True):
        best_by_domain.setdefault(record.domain, (record, score))
    return list(best_by_domain.values())


def build_citations(route_plan: RoutePlan) -> list[Citation]:
    """只根据本轮真实检索命中生成 Citation；未命中时诚实返回空列表。"""

    return [
        Citation(
            citation_id=f"C{index}",
            source_title=record.title,
            source_path=record.source_path,
            chunk_id=record.chunk_id,
            score=score,
            snippet=record.text,
        )
        for index, (record, score) in enumerate(retrieve_knowledge(route_plan), start=1)
    ]
