"""Step 22：商品知识检索。Step 22 把实时工具事实和稳定知识库依据合成一个回答。"""

from __future__ import annotations

import json
from typing import Any

from api.schemas import *
from config.settings import KNOWLEDGE_PATH


def load_knowledge_chunks() -> list[KnowledgeChunk]:
    """执行 load_knowledge_chunks 对应的项目逻辑。"""
    with KNOWLEDGE_PATH.open(encoding="utf-8") as file:
        return [KnowledgeChunk.model_validate(item) for item in json.load(file)]

def retrieve_product_knowledge(user_message: str, sku: str | None) -> list[KnowledgeHit]:
    """执行 retrieve_product_knowledge 对应的项目逻辑。"""
    query = user_message.lower()
    if sku == "SKU-AUD-101":
        query += " anc 蓝牙 降噪 耳机 618 消费电子 会员价"
    hits: list[KnowledgeHit] = []
    for chunk in load_knowledge_chunks():
        if chunk.topic not in {"product", "promotion"}:
            continue
        matched = [keyword for keyword in chunk.keywords if keyword.lower() in query or keyword in user_message]
        if not matched:
            continue
        score = round(min(1.0, 0.35 + len(set(matched)) / 10), 3)
        hits.append(KnowledgeHit(chunk=chunk, score=score, matched_keywords=matched))
    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:2]

def build_citations(hits: list[KnowledgeHit]) -> list[Citation]:
    """执行 build_citations 对应的项目逻辑。"""
    return [
        Citation(
            citation_id=f"C{index}",
            source_title=hit.chunk.title,
            source_path=hit.chunk.source_path,
            chunk_id=hit.chunk.chunk_id,
            score=hit.score,
            snippet=hit.chunk.text,
        )
        for index, hit in enumerate(hits, start=1)
    ]

def build_product_answer(observation: Observation, citations: list[Citation]) -> str:
    """执行 build_product_answer 对应的项目逻辑。"""
    if observation.status != "success":
        return f"我没有拿到可靠商品事实：{observation.summary}"
    facts = observation.facts
    knowledge_summary = "；".join(citation.source_title for citation in citations)
    stock_line = "有现货" if facts["inventory"] > 0 else "当前无现货"
    price_line = f"标价 {facts['current_price']} 元"
    if facts.get("promotion_price"):
        price_line += f"，活动价 {facts['promotion_price']} 元"
    return (
        f"可以推荐 {facts['name']}。工具查到它当前{stock_line}，库存 {facts['inventory']} 件，{price_line}，"
        f"活动状态是{facts['activity']}。结合知识库里的{knowledge_summary}，它适合通勤、差旅和开放办公场景；"
        "如果你是金卡会员，可以看会员价，但会员价不能再叠加会员券或满减券，最终以结算页为准。"
    )
