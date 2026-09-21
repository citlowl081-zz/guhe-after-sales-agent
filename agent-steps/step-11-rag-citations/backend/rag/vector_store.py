"""Step 10 向量库和相似度检索。"""

from __future__ import annotations

import math
import weakref

from api.schemas import KnowledgeChunk, KnowledgeHit, VectorRecord
from config.settings import SCORE_THRESHOLD, TOP_K
from embeddings.client import DEFAULT_EMBEDDING_CLIENT, EmbeddingClient, embed_text, embed_texts
from rag.knowledge_base import load_knowledge_chunks, query_asks_for_history, should_include_chunk_for_query


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


def build_vector_store(
    chunks: list[KnowledgeChunk] | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> list[VectorRecord]:
    """把知识 chunk 批量向量化，构建进程内向量库。"""

    source_chunks = chunks if chunks is not None else load_knowledge_chunks()
    embedding_texts = [
        " ".join([chunk.title, chunk.section, " ".join(chunk.keywords), chunk.text])
        for chunk in source_chunks
    ]
    embeddings = embed_texts(embedding_texts, embedding_client)
    return [
        VectorRecord(chunk=chunk, embedding=embedding)
        for chunk, embedding in zip(source_chunks, embeddings)
    ]


def get_vector_store(embedding_client: EmbeddingClient | None = None) -> list[VectorRecord]:
    """按 embedding 客户端缓存知识向量库。"""

    client = embedding_client or DEFAULT_EMBEDDING_CLIENT
    if client not in _VECTOR_STORE_CACHE:
        # 项目版先做进程内懒加载缓存，避免每轮请求都重新向量化整批知识。
        _VECTOR_STORE_CACHE[client] = build_vector_store(embedding_client=client)
    return _VECTOR_STORE_CACHE[client]


def retrieve_by_vector(
    query: str,
    top_k: int = TOP_K,
    threshold: float = SCORE_THRESHOLD,
    embedding_client: EmbeddingClient | None = None,
) -> list[KnowledgeHit]:
    """向量化用户问题，并按相似度召回 top_k 知识片段。"""

    query_embedding = embed_text(query, embedding_client)
    asks_for_history = query_asks_for_history(query)
    hits: list[KnowledgeHit] = []
    for record in get_vector_store(embedding_client=embedding_client):
        if not should_include_chunk_for_query(record.chunk, asks_for_history):
            continue
        score = cosine_similarity(query_embedding, record.embedding)
        if score >= threshold:
            hits.append(KnowledgeHit(chunk=record.chunk, score=round(score, 3)))
    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]
