"""Step 14 reranker 重排逻辑。

先用轻量规则模拟 reranker 的排序信号；配置商业 reranker 后再调用外部 `/rerank`。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_rerank_summary

import os

import httpx

from api.schemas import KnowledgeHit, RerankConfig, RerankOutcome
from config.settings import (
    RERANK_INSTRUCTION_DEFAULT,
    RERANK_MODEL_DEFAULT,
    RERANK_TIMEOUT_SECONDS,
    api_key_is_missing,
    env_flag_enabled,
    load_guhe_env,
)
from rag.query_rewrite import normalize_query


def explain_rerank_reasons(query: str, hit: KnowledgeHit) -> list[str]:
    """解释候选片段为什么被加权或降权。"""

    query_text = normalize_query(query)
    reasons: list[str] = []

    if hit.chunk.status == "current" and "当前" in query_text:
        reasons.append("当前规则加权")
    if hit.chunk.status == "expired":
        reasons.append("历史活动降权")
    if hit.chunk.topic == "promotion" and all(term in query_text for term in ["优惠券", "叠加"]):
        reasons.append("优惠叠加条件匹配")
    if hit.chunk.chunk_id == "promotion-current-audio-offer" and "结算页" in query_text:
        reasons.append("结算页约束匹配")
    if hit.chunk.topic != "promotion" and "活动" in query_text:
        reasons.append("非活动规则轻微降权")

    return reasons


def rerank_candidates_lightweight(query: str, candidates: list[KnowledgeHit]) -> list[KnowledgeHit]:
    """用项目内轻量规则重排候选片段。"""

    reranked: list[KnowledgeHit] = []
    for hit in candidates:
        score = hit.vector_score
        reasons = explain_rerank_reasons(query, hit)

        if "当前规则加权" in reasons:
            score += 0.22
        if "历史活动降权" in reasons:
            score -= 0.25
        if "优惠叠加条件匹配" in reasons:
            score += 0.12
        if "结算页约束匹配" in reasons:
            score += 0.08
        if "非活动规则轻微降权" in reasons:
            score -= 0.08

        final_score = round(max(0.0, min(1.0, score)), 3)
        reranked.append(
            hit.model_copy(
                update={
                    "score": final_score,
                    "rerank_score": final_score,
                    "rerank_reasons": reasons or ["保留向量初始分"],
                }
            )
        )
    return sorted(reranked, key=lambda hit: hit.score, reverse=True)


def build_commercial_rerank_config() -> RerankConfig | None:
    """读取商业 reranker 配置；未启用或缺 Key 时返回 None。"""

    load_guhe_env()
    if not env_flag_enabled("AGENT_RAG_RERANK_ENABLED", default=False):
        return None

    api_key = os.getenv("AGENT_RAG_RERANK_API_KEY") or os.getenv("AGENT_OPENAI_API_KEY")
    if api_key_is_missing(api_key):
        return None

    base_url = (
        os.getenv("AGENT_RAG_RERANK_BASE_URL")
        or os.getenv("AGENT_OPENAI_BASE_URL")
        or "https://api.siliconflow.cn/v1"
    ).rstrip("/")
    model = os.getenv("AGENT_RAG_RERANK_MODEL", RERANK_MODEL_DEFAULT)
    instruction = os.getenv("AGENT_RAG_RERANK_INSTRUCTION", RERANK_INSTRUCTION_DEFAULT)
    return RerankConfig(api_key=api_key or "", base_url=base_url, model=model, instruction=instruction)


def rerank_candidates_with_commercial_model(
    query: str,
    candidates: list[KnowledgeHit],
    config: RerankConfig,
) -> list[KnowledgeHit]:
    """调用商业 reranker 对候选片段精排。"""

    payload = {
        "model": config.model,
        "query": query,
        "documents": [hit.chunk.text for hit in candidates],
        "top_n": len(candidates),
        "return_documents": False,
    }
    if config.instruction:
        payload["instruction"] = config.instruction
    with httpx.Client(timeout=RERANK_TIMEOUT_SECONDS, trust_env=False) as client:
        response = client.post(
            f"{config.base_url}/rerank",
            headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    ranked: list[KnowledgeHit] = []
    for item in data.get("results", []):
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(candidates):
            continue

        raw_score = item.get("relevance_score", item.get("score", candidates[index].score))
        final_score = round(max(0.0, min(1.0, float(raw_score))), 3)
        reasons = explain_rerank_reasons(query, candidates[index]) or ["商业 reranker 精排"]
        ranked.append(
            candidates[index].model_copy(
                update={
                    "score": final_score,
                    "rerank_score": final_score,
                    "rerank_reasons": reasons,
                }
            )
        )
    return ranked


def rerank_candidates(query: str, candidates: list[KnowledgeHit]) -> RerankOutcome:
    """根据配置选择商业 reranker 或轻量 fallback。"""

    config = build_commercial_rerank_config()
    if config is None:
        ranked = rerank_candidates_lightweight(query, candidates)
        log_rerank_summary(model="guhe-lightweight", scores=[hit.score for hit in ranked])
        return RerankOutcome(hits=ranked, mode="lightweight")

    try:
        ranked = rerank_candidates_with_commercial_model(query, candidates, config)
        if ranked:
            log_rerank_summary(model=config.model, scores=[hit.score for hit in ranked])
            return RerankOutcome(hits=ranked, mode="commercial", model=config.model)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        return RerankOutcome(
            hits=rerank_candidates_lightweight(query, candidates),
            mode="commercial_fallback",
            model=config.model,
            error=exc.__class__.__name__,
        )

    return RerankOutcome(
        hits=rerank_candidates_lightweight(query, candidates),
        mode="commercial_fallback",
        model=config.model,
        error="empty_rerank_result",
    )
