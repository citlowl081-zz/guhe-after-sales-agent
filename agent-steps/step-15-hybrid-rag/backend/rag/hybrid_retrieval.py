"""Step 15 Hybrid RAG 检索。"""

from __future__ import annotations

import math
from typing import Any

from api.schemas import KnowledgeChunk, KnowledgeHit, RetrievalPlan
from config.settings import FINAL_TOP_K, KEYWORD_TOP_K, LOW_CONFIDENCE_THRESHOLD, VECTOR_TOP_K
from embeddings.client import DEFAULT_EMBEDDING_CLIENT, EmbeddingClient, embed_text, embed_texts
from rag.knowledge_base import load_knowledge_chunks
from rag.planning import normalize_query, topic_allowed


_VECTOR_STORE_CACHE: dict[int, list[tuple[KnowledgeChunk, list[float]]]] = {}


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """计算两个 embedding 向量的 cosine similarity。"""

    if len(left) != len(right):
        raise ValueError("embedding 维度不一致，无法计算向量相似度。")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def chunk_embedding_text(chunk: KnowledgeChunk) -> str:
    """拼出用于向量化的稳定知识文本。"""

    return f"{chunk.title}\n{chunk.section}\n关键词：{' '.join(chunk.keywords)}\n{chunk.text}"


def get_vector_store(embedding_client: EmbeddingClient | None = None) -> list[tuple[KnowledgeChunk, list[float]]]:
    """按 embedding 客户端缓存知识向量，避免每轮重复向量化。"""

    client = embedding_client or DEFAULT_EMBEDDING_CLIENT
    cache_key = id(client)
    if cache_key not in _VECTOR_STORE_CACHE:
        chunks = load_knowledge_chunks()
        embeddings = embed_texts([chunk_embedding_text(chunk) for chunk in chunks], client)
        _VECTOR_STORE_CACHE[cache_key] = list(zip(chunks, embeddings))
    return _VECTOR_STORE_CACHE[cache_key]


def vector_retrieve(plan: RetrievalPlan, embedding_client: EmbeddingClient | None = None) -> list[KnowledgeHit]:
    """用真实 embedding 向量召回语义相似片段。"""

    query_embedding = embed_text(normalize_query(plan.rewritten_query), embedding_client)
    hits: list[KnowledgeHit] = []
    for chunk, chunk_embedding in get_vector_store(embedding_client):
        if not topic_allowed(chunk, plan):
            continue
        vector_score = cosine_similarity(query_embedding, chunk_embedding)
        score = round(max(0.0, min(1.0, vector_score)), 3)
        if score <= 0:
            continue
        matched = [keyword for keyword in chunk.keywords if keyword.lower() in normalize_query(plan.rewritten_query)]
        hits.append(
            KnowledgeHit(
                chunk=chunk,
                score=score,
                vector_score=score,
                sources=["vector"],
                matched_keywords=matched,
            )
        )
    return sorted(hits, key=lambda hit: hit.vector_score, reverse=True)[:VECTOR_TOP_K]


def keyword_retrieve(plan: RetrievalPlan) -> list[KnowledgeHit]:
    """用关键词召回补足长尾精确词。"""

    terms = plan.keyword_terms
    rare_terms = {"赠品", "包装盒", "压坏", "结算页", "无理由", "会员价"}
    hits: list[KnowledgeHit] = []
    for chunk in load_knowledge_chunks():
        if not topic_allowed(chunk, plan):
            continue
        matched = [term for term in terms if term in chunk.keywords or term in chunk.text]
        if not matched:
            continue
        # 这里不是完整 BM25，只保留 BM25 的直觉：稀有关键词比泛词更能区分规则。
        # `/ 8` 是项目版归一化，把几个关键词累计命中的分数压回 0-1，方便和向量分数放在同一视图里观察。
        raw_score = sum(2.0 if term in rare_terms else 1.0 for term in set(matched))
        score = round(min(1.0, raw_score / 8), 3)
        hits.append(
            KnowledgeHit(
                chunk=chunk,
                score=score,
                keyword_score=score,
                sources=["keyword"],
                matched_keywords=matched,
            )
        )
    return sorted(hits, key=lambda hit: hit.keyword_score, reverse=True)[:KEYWORD_TOP_K]


def merge_hybrid_hits(plan: RetrievalPlan, vector_hits: list[KnowledgeHit], keyword_hits: list[KnowledgeHit]) -> list[KnowledgeHit]:
    """合并向量召回和关键词召回结果。"""

    merged: dict[str, KnowledgeHit] = {}
    for hit in [*vector_hits, *keyword_hits]:
        chunk_id = hit.chunk.chunk_id
        current = merged.get(chunk_id)
        if current is None:
            merged[chunk_id] = hit
            continue
        sources = list(dict.fromkeys([*current.sources, *hit.sources]))
        matched = list(dict.fromkeys([*current.matched_keywords, *hit.matched_keywords]))
        merged[chunk_id] = current.model_copy(
            update={
                "vector_score": max(current.vector_score, hit.vector_score),
                "keyword_score": max(current.keyword_score, hit.keyword_score),
                "sources": sources,
                "matched_keywords": matched,
            }
        )

    final_hits: list[KnowledgeHit] = []
    for hit in merged.values():
        if hit.chunk.status == "expired" and "当前" in plan.rewritten_query:
            continue
        # topic_allowed 已经过滤过主题，这个加权只用于把“命中本轮检索场景”显式写进分数和调试原因。
        route_boost = 0.08 if hit.chunk.topic in plan.allowed_topics else 0
        freshness_boost = 0.06 if hit.chunk.status == "current" else -0.1
        # Hybrid RAG 是互补召回：任一路有强证据都应留下，再叠加路由和有效期信号。
        score = round(max(0.0, min(1.0, max(hit.vector_score, hit.keyword_score) + route_boost + freshness_boost)), 3)
        reasons = [f"{source}召回" for source in hit.sources]
        if route_boost:
            reasons.append(f"{plan.scene}场景过滤命中")
        if hit.chunk.status == "expired":
            reasons.append("历史规则降权")
        final_hits.append(hit.model_copy(update={"score": score, "rerank_reasons": reasons}))
    return sorted(final_hits, key=lambda hit: hit.score, reverse=True)[:FINAL_TOP_K]


def retrieve_knowledge(plan: RetrievalPlan) -> tuple[list[KnowledgeHit], dict[str, Any]]:
    """执行 Hybrid RAG，并返回命中结果和调试信息。"""

    vector_hits = vector_retrieve(plan)
    keyword_hits = keyword_retrieve(plan)
    final_hits = merge_hybrid_hits(plan, vector_hits, keyword_hits)
    debug = {
        "vector_chunk_ids": [hit.chunk.chunk_id for hit in vector_hits],
        "keyword_chunk_ids": [hit.chunk.chunk_id for hit in keyword_hits],
        "source_scores": {
            hit.chunk.chunk_id: {"vector": hit.vector_score, "keyword": hit.keyword_score, "final": hit.score, "sources": hit.sources}
            for hit in final_hits
        },
    }
    return final_hits, debug


def is_low_confidence(hits: list[KnowledgeHit]) -> bool:
    """根据最高分判断本轮是否低置信。"""

    if not hits:
        return True
    return hits[0].score < LOW_CONFIDENCE_THRESHOLD
