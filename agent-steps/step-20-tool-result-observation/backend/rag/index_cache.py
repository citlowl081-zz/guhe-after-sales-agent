"""沿用前序稳定知识 RAG 的知识索引和检索缓存。

索引缓存的是知识结构，检索缓存只缓存稳定知识命中；最终回答和实时业务问题不能缓存。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from api.schemas import KnowledgeChunk, KnowledgeHit, KnowledgeIndex, RetrievalCacheEntry, RetrievalPlan
from config.settings import LOW_CONFIDENCE_THRESHOLD
from rag.knowledge_base import load_knowledge_chunks
from rag.planning import is_realtime_business_query, normalize_query


KNOWLEDGE_INDEX: KnowledgeIndex | None = None
RAG_RETRIEVAL_CACHE: dict[str, RetrievalCacheEntry] = {}


def build_knowledge_index(chunks: list[KnowledgeChunk]) -> KnowledgeIndex:
    """根据知识片段构建索引版本和倒排表。"""

    payload = json.dumps([chunk.model_dump() for chunk in chunks], ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    inverted: dict[str, list[str]] = {}
    for chunk in chunks:
        for keyword in chunk.keywords:
            inverted.setdefault(keyword, []).append(chunk.chunk_id)
    return KnowledgeIndex(
        version=f"idx-{fingerprint}",
        fingerprint=fingerprint,
        chunk_count=len(chunks),
        chunks_by_id={chunk.chunk_id: chunk for chunk in chunks},
        inverted_index=inverted,
    )


def get_knowledge_index() -> KnowledgeIndex:
    """懒加载当前进程内知识索引。"""

    global KNOWLEDGE_INDEX
    if KNOWLEDGE_INDEX is None:
        KNOWLEDGE_INDEX = build_knowledge_index(load_knowledge_chunks())
    return KNOWLEDGE_INDEX


def rebuild_knowledge_index(chunks: list[KnowledgeChunk] | None = None) -> KnowledgeIndex:
    """重建索引时清空 RAG 检索缓存，避免旧索引结果继续被复用。"""

    global KNOWLEDGE_INDEX
    source_chunks = chunks if chunks is not None else load_knowledge_chunks()
    KNOWLEDGE_INDEX = build_knowledge_index(source_chunks)
    RAG_RETRIEVAL_CACHE.clear()
    return KNOWLEDGE_INDEX


def reset_index_and_cache() -> None:
    """清空索引和检索缓存，方便测试或手动重建。"""

    global KNOWLEDGE_INDEX
    KNOWLEDGE_INDEX = None
    RAG_RETRIEVAL_CACHE.clear()


def cache_key_for(plan: RetrievalPlan, index: KnowledgeIndex) -> str:
    """为检索计划和索引版本生成缓存 key。"""

    payload = json.dumps(
        {
            "index_version": index.version,
            "scene": plan.scene,
            "normalized_query": normalize_query(plan.rewritten_query),
            "allowed_topics": sorted(plan.allowed_topics),
            "keyword_terms": sorted(plan.keyword_terms),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def cache_policy_for(plan: RetrievalPlan, hits: list[KnowledgeHit]) -> dict[str, Any]:
    """根据实时性和检索证据判断本轮结果能否缓存。"""

    realtime = is_realtime_business_query(plan.original_query)
    if realtime:
        cacheable = False
        reason = "实时订单、物流、库存或退款进度不能缓存为知识库答案。"
    elif not hits:
        cacheable = False
        reason = "尚未获得可验证的稳定知识命中，本轮不写入检索缓存。"
    else:
        top_hit_reliable = hits[0].score >= LOW_CONFIDENCE_THRESHOLD
        published_hits_only = all(hit.chunk.status in {"current", "reference"} for hit in hits)
        cacheable = top_hit_reliable and published_hits_only
        reason = (
            "检索结果通过置信度门，且只来自当前或参考状态的已发布知识，可以缓存。"
            if cacheable
            else "检索结果置信度不足或包含过期知识，本轮不写入检索缓存。"
        )
    return {
        "cacheable": cacheable,
        "scope": "retrieval_hits_only",
        "reason": reason,
        "evidence": {
            "top_topic": hits[0].chunk.topic if hits else None,
            "top_score": hits[0].score if hits else None,
            "knowledge_statuses": list(dict.fromkeys(hit.chunk.status for hit in hits)),
        },
    }
