"""Step 15 Prompt 和 citations 构造。"""

from __future__ import annotations

from api.schemas import ChatRequest, Citation, KnowledgeHit, RetrievalPlan


def build_citations(hits: list[KnowledgeHit]) -> list[Citation]:
    """把可靠命中转换成 citations。"""

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


def render_rag_messages(request: ChatRequest, plan: RetrievalPlan, hits: list[KnowledgeHit]) -> list[dict[str, str]]:
    """把 Hybrid RAG 最终命中渲染成模型输入。"""

    evidence = "\n".join(
        f"[{index}] {hit.chunk.title} sources={','.join(hit.sources)} score={hit.score}\n{hit.chunk.text}"
        for index, hit in enumerate(hits, start=1)
    )
    return [
        {
            "role": "system",
            "content": (
                "你是古河商城公司的客服 Agent。只能依据 Hybrid RAG 最终命中的知识回答稳定规则问题；"
                "实时订单、物流、库存和退款进度需要业务系统核验。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户原话：{request.user_message}\n"
                f"pre-retrieval 场景：{plan.scene}\n"
                f"检索问题：{plan.rewritten_query}\n"
                f"Hybrid RAG 命中：\n{evidence}"
            ),
        },
    ]
