"""Step 12 向量召回。

这一步故意把“候选入场线”和“低置信回答线”分开：先召回可能相关的片段，
再由质量判断决定是否足以回答。
"""

from __future__ import annotations

import math
import weakref

from api.schemas import KnowledgeChunk, KnowledgeHit, VectorRecord
from config.settings import RETRIEVAL_SCORE_THRESHOLD, TOP_K
from embeddings.client import DEFAULT_EMBEDDING_CLIENT, EmbeddingClient, embed_text, embed_texts
from rag.knowledge_base import load_knowledge_chunks, query_asks_for_history, should_include_chunk_for_query


_VECTOR_STORE_CACHE: weakref.WeakKeyDictionary[EmbeddingClient, list[VectorRecord]] = weakref.WeakKeyDictionary()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """计算两个向量的 cosine similarity。"""

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
    chunk_embeddings = embed_texts(
        [chunk_embedding_text(chunk) for chunk in source_chunks],
        embedding_client,
    )
    return [
        VectorRecord(chunk=chunk, embedding=chunk_embedding)
        for chunk, chunk_embedding in zip(source_chunks, chunk_embeddings)
    ]


def get_vector_store(embedding_client: EmbeddingClient | None = None) -> list[VectorRecord]:
    """按 embedding 客户端缓存知识向量库，避免每轮请求重建。"""

    client = embedding_client or DEFAULT_EMBEDDING_CLIENT
    if client not in _VECTOR_STORE_CACHE:
        # 项目版先做进程内懒加载缓存；生产里还要接入知识版本和模型版本失效机制。
        _VECTOR_STORE_CACHE[client] = build_vector_store(embedding_client=client)
    return _VECTOR_STORE_CACHE[client]


def retrieve_knowledge(
    query: str,
    top_k: int = TOP_K,
    threshold: float = RETRIEVAL_SCORE_THRESHOLD,
    embedding_client: EmbeddingClient | None = None,
) -> list[KnowledgeHit]:
    """召回候选知识片段，供后续低置信判断使用。"""

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
