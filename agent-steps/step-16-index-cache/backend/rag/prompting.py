"""Step 16 Prompt 和 citations 构造。"""

from __future__ import annotations

from api.schemas import ChatRequest, Citation, KnowledgeHit, KnowledgeIndex, RetrievalPlan


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


def render_rag_messages(request: ChatRequest, plan: RetrievalPlan, hits: list[KnowledgeHit], index: KnowledgeIndex) -> list[dict[str, str]]:
    """把当前索引版本中的命中知识渲染成模型输入。"""

    evidence = "\n".join(
        f"[{index}] {hit.chunk.title} sources={','.join(hit.sources)} score={hit.score}\n{hit.chunk.text}"
        for index, hit in enumerate(hits, start=1)
    )
    return [
        {
            "role": "system",
            "content": (
                "你是古河商城公司的客服 Agent。只能依据当前索引版本里的稳定知识回答；"
                "实时订单、物流、库存和退款进度需要业务工具核验。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"索引版本：{index.version}\n"
                f"用户原话：{request.user_message}\n"
                f"pre-retrieval 场景：{plan.scene}\n"
                f"检索问题：{plan.rewritten_query}\n"
                f"Hybrid RAG 命中：\n{evidence}"
            ),
        },
    ]
