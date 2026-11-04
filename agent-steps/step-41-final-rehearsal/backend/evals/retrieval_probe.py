"""检索专项基线。不调用对话模型，只比较纯向量和当前引用结果。"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[1]
AGENT_STEPS = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(AGENT_STEPS))

from rag.hybrid_retrieval import get_knowledge_index
from rag.knowledge import faq_knowledge_result, promotion_policy_result
from tools.planning import classify_intent

QUESTIONS_PATH = Path(__file__).with_name("retrieval_questions.yml")
TOP_K = 4
REFUND_POLICY = "refund_before_shipping"


def load_questions(path: Path = QUESTIONS_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def ranked_policy_ids(index, query: str, k: int = TOP_K) -> list[str]:
    pairs = index.vector_store.similarity_search_with_score(query, k=k)
    ordered: list[str] = []
    for document, _score in pairs:
        policy_id = str(document.metadata.get("policy_id") or "")
        if policy_id and policy_id not in ordered:
            ordered.append(policy_id)
    return ordered


def current_policy_ids(query: str) -> tuple[str, list[str]]:
    """按当前规则路由取最终会引用的政策，不生成客服话术。"""
    intent = classify_intent(query)
    if intent == "refund_request":
        return intent, [REFUND_POLICY]
    if intent in {"promotion_query", "product_query"} and _promotion_lookup(query, intent):
        result = promotion_policy_result("retrieval-probe", query)
        return intent, _policy_ids(result.citations)
    if intent == "faq_query":
        result = faq_knowledge_result("retrieval-probe", query)
        return intent, _policy_ids(result.citations)
    return intent, []


def _promotion_lookup(query: str, intent: str) -> bool:
    if intent == "promotion_query":
        return True
    return any(term in query for term in ("活动", "优惠", "满减", "会员"))


def _policy_ids(citations) -> list[str]:
    ordered: list[str] = []
    for citation in citations:
        policy_id = str((citation.metadata or {}).get("policy_id") or "")
        if policy_id and policy_id not in ordered:
            ordered.append(policy_id)
    return ordered


def _contains_all(found: list[str], expected: list[str]) -> bool:
    return all(policy_id in found for policy_id in expected)


def _exact(found: list[str], expected: list[str], limit: int | None = None) -> bool:
    window = found if limit is None else found[:limit]
    return set(window) == set(expected)


def score(path: Path = QUESTIONS_PATH) -> dict:
    payload = load_questions(path)
    index, _ = get_knowledge_index()
    retrieval_rows = []
    for item in payload["retrieval"]:
        expected = list(item["expected_policy_ids"])
        vector_ids = ranked_policy_ids(index, item["query"])
        intent, current_ids = current_policy_ids(item["query"])
        top_limit = 1 if len(expected) == 1 else 2
        retrieval_rows.append(
            {
                "id": item["id"],
                "category": item["category"],
                "query": item["query"],
                "expected": expected,
                "intent": intent,
                "vector": vector_ids,
                "current": current_ids,
                "vector_recall": _contains_all(vector_ids, expected),
                "current_recall": _contains_all(current_ids, expected),
                "vector_exact": _exact(vector_ids, expected, top_limit),
                "current_exact": _exact(current_ids, expected),
            }
        )
    abstain_rows = []
    for item in payload["abstain"]:
        vector_ids = ranked_policy_ids(index, item["query"], k=1)
        intent, current_ids = current_policy_ids(item["query"])
        abstain_rows.append(
            {
                "id": item["id"],
                "category": item["category"],
                "query": item["query"],
                "intent": intent,
                "vector_top1": vector_ids[:1],
                "current": current_ids,
                "vector_false_cite": bool(vector_ids),
                "current_false_cite": bool(current_ids),
            }
        )
    workflow_rows = []
    for item in payload["workflow"]:
        intent = classify_intent(item["query"])
        workflow_rows.append(
            {
                "id": item["id"],
                "query": item["query"],
                "expected_intent": item["expected_intent"],
                "actual_intent": intent,
                "passed": intent == item["expected_intent"],
            }
        )
    return {
        "embedder": index.embedding_mode,
        "chunk_count": len(index.documents),
        "top_k": TOP_K,
        "retrieval": retrieval_rows,
        "abstain": abstain_rows,
        "workflow": workflow_rows,
    }


def _rate(rows: list[dict], key: str) -> str:
    if not rows:
        return "0/0"
    hit = sum(1 for row in rows if row[key])
    return f"{hit}/{len(rows)}"


def summarize(report: dict) -> dict:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in report["retrieval"]:
        by_category[row["category"]].append(row)
    categories = []
    for name in by_category:
        rows = by_category[name]
        categories.append(
            {
                "category": name,
                "n": len(rows),
                "vector_recall_at_4": _rate(rows, "vector_recall"),
                "vector_top_exact": _rate(rows, "vector_exact"),
                "current_recall": _rate(rows, "current_recall"),
                "current_exact": _rate(rows, "current_exact"),
            }
        )
    all_rows = report["retrieval"]
    abstain = report["abstain"]
    workflow = report["workflow"]
    abstain_by_category: dict[str, list[dict]] = defaultdict(list)
    for row in abstain:
        abstain_by_category[row["category"]].append(row)
    abstain_categories = [
        {
            "category": name,
            "n": len(rows),
            "current_false_cite": _rate(rows, "current_false_cite"),
            "vector_false_cite": _rate(rows, "vector_false_cite"),
        }
        for name, rows in abstain_by_category.items()
    ]
    return {
        "embedder": report["embedder"],
        "chunk_count": report["chunk_count"],
        "top_k": report["top_k"],
        "retrieval_n": len(all_rows),
        "vector_recall_at_4": _rate(all_rows, "vector_recall"),
        "vector_top_exact": _rate(all_rows, "vector_exact"),
        "current_recall": _rate(all_rows, "current_recall"),
        "current_exact": _rate(all_rows, "current_exact"),
        "abstain_n": len(abstain),
        "vector_false_cite": _rate(abstain, "vector_false_cite"),
        "current_false_cite": _rate(abstain, "current_false_cite"),
        "abstain_by_category": abstain_categories,
        "workflow_n": len(workflow),
        "workflow_intent_match": _rate(workflow, "passed"),
        "by_category": categories,
        "retrieval_misses": [
            {
                "id": row["id"],
                "category": row["category"],
                "query": row["query"],
                "expected": row["expected"],
                "intent": row["intent"],
                "vector": row["vector"],
                "current": row["current"],
            }
            for row in all_rows
            if not row["current_exact"]
        ],
        "abstain_false_cites": [
            {
                "id": row["id"],
                "query": row["query"],
                "intent": row["intent"],
                "current": row["current"],
            }
            for row in abstain
            if row["current_false_cite"]
        ],
        "workflow_misses": [
            {
                "id": row["id"],
                "query": row["query"],
                "expected_intent": row["expected_intent"],
                "actual_intent": row["actual_intent"],
            }
            for row in workflow
            if not row["passed"]
        ],
    }


def main() -> None:
    summary = summarize(score())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
