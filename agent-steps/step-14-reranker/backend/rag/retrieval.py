"""Step 14 向量候选召回和低置信判断。"""

from __future__ import annotations

import math
import weakref

from api.schemas import KnowledgeChunk, KnowledgeHit, VectorRecord
from config.settings import CANDIDATE_K, LOW_CONFIDENCE_THRESHOLD, RETRIEVAL_SCORE_THRESHOLD
from embeddings.client import DEFAULT_EMBEDDING_CLIENT, EmbeddingClient, embed_text, embed_texts
from rag.knowledge_base import load_knowledge_chunks


_VECTOR_STORE_CACHE: weakref.WeakKeyDictionary[EmbeddingClient, list[VectorRecord]] = weakref.WeakKeyDictionary()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """计算两个向量的 cosine similarity，并显式拒绝维度不一致。"""

    if len(left) != len(right):
        raise ValueError(f"向量维度不一致：left={len(left)}, right={len(right)}")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    dot = sum(left_value * right_value for left_value, right_value in zip(left, right))
    return dot / (left_norm * right_norm)


def chunk_embedding_text(chunk: KnowledgeChunk) -> str:
    """拼出用于 embedding 的 chunk 文本。"""

    return " ".join([chunk.title, chunk.section, " ".join(chunk.keywords), chunk.text])


def build_vector_store(
    chunks: list[KnowledgeChunk] | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> list[VectorRecord]:
    """把知识 chunk 批量向量化，构建进程内向量库。"""

    source_chunks = chunks if chunks is not None else load_knowledge_chunks()
    embeddings = embed_texts([chunk_embedding_text(chunk) for chunk in source_chunks], embedding_client)
    return [
        VectorRecord(chunk=chunk, embedding=embedding)
        for chunk, embedding in zip(source_chunks, embeddings)
    ]


def get_vector_store(embedding_client: EmbeddingClient | None = None) -> list[VectorRecord]:
    """按 embedding 客户端缓存知识向量库。"""

    client = embedding_client or DEFAULT_EMBEDDING_CLIENT
    if client not in _VECTOR_STORE_CACHE:
        # Reranker 只重排候选；初召回仍复用同一份向量库缓存。
        _VECTOR_STORE_CACHE[client] = build_vector_store(embedding_client=client)
    return _VECTOR_STORE_CACHE[client]


def retrieve_candidates(
    query: str,
    top_k: int = CANDIDATE_K,
    threshold: float = RETRIEVAL_SCORE_THRESHOLD,
    embedding_client: EmbeddingClient | None = None,
) -> list[KnowledgeHit]:
    """向量化查询文本，召回进入 reranker 的候选集。"""

    query_embedding = embed_text(query, embedding_client)
    hits: list[KnowledgeHit] = []
    for record in get_vector_store(embedding_client=embedding_client):
        vector_score = cosine_similarity(query_embedding, record.embedding)
        if vector_score < threshold:
            continue
        rounded = round(max(0.0, min(1.0, vector_score)), 3)
        hits.append(KnowledgeHit(chunk=record.chunk, score=rounded, vector_score=rounded))
    return sorted(hits, key=lambda hit: hit.vector_score, reverse=True)[:top_k]


def merge_candidates(*candidate_groups: list[KnowledgeHit]) -> list[KnowledgeHit]:
    """合并原始查询和改写查询召回的候选片段。"""

    merged: dict[str, KnowledgeHit] = {}
    for group in candidate_groups:
        for hit in group:
            current = merged.get(hit.chunk.chunk_id)
            if current is None or hit.vector_score > current.vector_score:
                merged[hit.chunk.chunk_id] = hit
    return sorted(merged.values(), key=lambda hit: hit.vector_score, reverse=True)[:CANDIDATE_K]


def is_low_confidence(hits: list[KnowledgeHit]) -> bool:
    """根据最高分判断本轮检索是否低置信。"""

    if not hits:
        return True
    return hits[0].score < LOW_CONFIDENCE_THRESHOLD
