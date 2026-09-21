"""Step 12 RAG 质量检查和低置信判断。"""

from __future__ import annotations

import json

from api.schemas import KnowledgeHit, RagQualityCase, RagQualityCaseResult, RagQualitySummary
from config.settings import LOW_CONFIDENCE_THRESHOLD, QUALITY_CASES_PATH
from embeddings.client import EmbeddingClient
from rag.retrieval import retrieve_knowledge


def load_quality_cases() -> list[RagQualityCase]:
    """读取固定 RAG 质量用例。"""

    with QUALITY_CASES_PATH.open(encoding="utf-8") as file:
        return [RagQualityCase.model_validate(item) for item in json.load(file)]


def is_low_confidence(hits: list[KnowledgeHit]) -> bool:
    """根据最高分判断本轮命中是否足以支撑回答。"""

    if not hits:
        return True
    return hits[0].score < LOW_CONFIDENCE_THRESHOLD


def evaluate_quality_case(
    case: RagQualityCase,
    embedding_client: EmbeddingClient | None = None,
) -> RagQualityCaseResult:
    """运行一条质量用例，并计算 recall@k、precision@k 和兜底结果。"""

    hits = retrieve_knowledge(case.question, embedding_client=embedding_client)
    fallback = is_low_confidence(hits)
    retrieved_ids = [] if fallback else [hit.chunk.chunk_id for hit in hits]
    expected = set(case.expected_chunk_ids)
    retrieved = set(retrieved_ids)

    if case.must_fallback:
        return RagQualityCaseResult(
            case_id=case.case_id,
            retrieved_chunk_ids=retrieved_ids,
            expected_chunk_ids=case.expected_chunk_ids,
            recall_at_k=1.0 if fallback else 0.0,
            precision_at_k=1.0 if fallback else 0.0,
            fallback=fallback,
            passed=fallback and not retrieved_ids,
        )

    matched = expected & retrieved
    recall = len(matched) / max(len(expected), 1)
    precision = len(matched) / max(len(retrieved), 1)
    return RagQualityCaseResult(
        case_id=case.case_id,
        retrieved_chunk_ids=retrieved_ids,
        expected_chunk_ids=case.expected_chunk_ids,
        recall_at_k=round(recall, 3),
        precision_at_k=round(precision, 3),
        fallback=fallback,
        passed=recall > 0 and not fallback,
    )


def run_rag_quality_check(
    cases: list[RagQualityCase] | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> RagQualitySummary:
    """运行固定问题集，返回轻量质量汇总。"""

    quality_cases = cases if cases is not None else load_quality_cases()
    results = [evaluate_quality_case(case, embedding_client=embedding_client) for case in quality_cases]
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    average_recall = sum(result.recall_at_k for result in results) / max(total, 1)
    average_precision = sum(result.precision_at_k for result in results) / max(total, 1)
    return RagQualitySummary(
        total_cases=total,
        passed_cases=passed,
        average_recall_at_k=round(average_recall, 3),
        average_precision_at_k=round(average_precision, 3),
        results=results,
    )
