"""Step 22：Agent 编排层。每一步只在这里串起已学到的模块能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from typing import Any

from api.schemas import *
from config.settings import load_guhe_env
from tools.runtime_context import *
from tools.contracts import *
from tools.planning import *
from tools.tool_runtime import *
from rag.product_knowledge import *
from models.answer_client import compose_grounded_answer


class Step22Agent:
    """Tool + RAG 商品咨询版 Agent。"""

    def __init__(self) -> None:
        """执行 __init__ 对应的项目逻辑。"""
        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给项目 Agent 编排。"""
        load_guhe_env()
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别商品咨询意图", observe=True, intent=intent)
        risk_level = classify_risk(intent, request.user_message)

        if risk_level == "high":
            session_state = self._session_state(request, message_count, None, None, [], risk_level)
            return ChatResponse(
                session_id=request.session_id,
                answer="退款、取消和补偿属于高风险售后动作，当前商品咨询版 Agent 不能自动处理，会建议转人工客服。",
                intent=intent,
                citations=[],
                tool_calls=[],
                next_action="transfer_to_human",
                risk_level=risk_level,
                needs_human_approval=True,
                degraded=False,
                reasoning_summary=["当前只做商品 Tool + RAG 联合回答。", "高风险售后动作留给后续工作流与人工审批项目。"],
                session_state=session_state,
            )

        action = plan_tool_action(request, intent)
        log_guhe_event("TOOL_PLANNED", "实时商品工具动作已生成", observe=True, tool_name=getattr(action, "tool_name", None))
        observation = execute_product_tool(action) if action else None
        if observation:
            log_guhe_event("TOOL_OBSERVATION", "库存与价格事实查询完成", observe=True, status=observation.status, observation_summary=observation.summary)
        sku = action.arguments["sku"] if action else None
        hits = retrieve_product_knowledge(request.user_message, sku)
        citations = build_citations(hits)
        log_guhe_event("RAG_RETRIEVED", "商品知识检索与引用完成", observe=True, hit_count=len(hits), citation_count=len(citations))
        tool_calls = [ToolCallRecord(action=action, observation=observation)] if action and observation else []

        if observation:
            deterministic_answer = build_product_answer(observation, citations)
            next_action: NextAction = observation.next_action
        else:
            deterministic_answer = "我需要更明确的商品信息，才能同时核验库存价格和商品知识。"
            next_action = "fallback_answer"

        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=deterministic_answer,
            facts={"tool_observation": observation},
            citations=citations,
            risk_level=risk_level,
            next_action=next_action,
            skip_model=observation is None,
            skip_reason="missing_product_fact",
        )
        answer = model_result.answer
        session_state = self._session_state(request, message_count, action, observation, citations, risk_level)
        session_state["model_answer"] = model_result.model_dump()
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            tool_calls=tool_calls,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=False,
            degraded=False,
            reasoning_summary=[
                "商品咨询先用工具查实时库存、价格和活动状态。",
                "再用 RAG 找稳定商品卖点和活动规则。",
                "最终回答同时标明实时事实和知识库依据，不进入高风险售后审批。",
            ],
            session_state=session_state,
        )

    def _session_state(
        self,
        request: ChatRequest,
        message_count: int,
        action: ToolAction | None,
        observation: Observation | None,
        citations: list[Citation],
        risk_level: RiskLevel,
    ) -> dict[str, Any]:
        """整理观察台需要展示的项目状态。"""
        return {
            "agent_version": "step-22-tool-rag-product-answer",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": {
                    "currentPage": (request.runtime_context or {}).get("currentPage") if isinstance(request.runtime_context, dict) else None,
                    "relatedProductId": (request.runtime_context or {}).get("relatedProductId") if isinstance(request.runtime_context, dict) else None,
                },
            },
            "tool_rag": {
                "mode": "product_tool_plus_rag",
                "tool_action": action.model_dump() if action else None,
                "tool_observation": observation.model_dump() if observation else None,
                "citation_chunk_ids": [citation.chunk_id for citation in citations],
                "answer_sources": ["tool", "rag"] if action and citations else ["rag"] if citations else [],
                "risk_level": risk_level,
            },
            "next_gap": "Agent 能查实时事实，也能联合 RAG 回答商品咨询；但工具越来越多，参数校验、错误处理、日志摘要和安全边界开始重复，下一幕要治理工具链路。",
        }

def health() -> dict[str, str]:
    """返回当前步程后端健康状态。"""
    return {"status": "ok", "step": "22"}

def capabilities() -> dict[str, Any]:
    """返回当前步程的能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给项目 Agent 编排。"""
    return agent.chat(request)
