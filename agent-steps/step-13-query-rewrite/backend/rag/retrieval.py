"""Step 13 向量检索和低置信判断。"""

from __future__ import annotations

import math
import weakref

from api.schemas import KnowledgeChunk, KnowledgeHit, VectorRecord
from config.settings import LOW_CONFIDENCE_THRESHOLD, RETRIEVAL_SCORE_THRESHOLD, TOP_K
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
        # 查询改写只改变 query，知识 chunk 向量仍然应该复用同一份进程内缓存。
        _VECTOR_STORE_CACHE[client] = build_vector_store(embedding_client=client)
    return _VECTOR_STORE_CACHE[client]


def retrieve_knowledge(
    query: str,
    top_k: int = TOP_K,
    threshold: float = RETRIEVAL_SCORE_THRESHOLD,
    embedding_client: EmbeddingClient | None = None,
) -> list[KnowledgeHit]:
    """向量化查询文本，并按相似度召回 top_k 知识片段。"""

    query_embedding = embed_text(query, embedding_client)
    hits: list[KnowledgeHit] = []
    for record in get_vector_store(embedding_client=embedding_client):
        score = cosine_similarity(query_embedding, record.embedding)
        if score >= threshold:
            hits.append(KnowledgeHit(chunk=record.chunk, score=round(score, 3)))
    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]


def is_low_confidence(hits: list[KnowledgeHit]) -> bool:
    """根据最高分判断本轮检索是否低置信。"""

    if not hits:
        return True
    return hits[0].score < LOW_CONFIDENCE_THRESHOLD
