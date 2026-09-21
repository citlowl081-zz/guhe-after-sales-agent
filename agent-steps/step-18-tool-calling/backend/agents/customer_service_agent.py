"""Step 18：Agent 编排层。每一步只在这里串起已学到的模块能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, log_model_input, log_model_output, observe_chat

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from api.schemas import ChatRequest, ChatResponse, Citation, Intent, ToolAction, ToolCallRecord, ToolObservation
from models.llm_client import create_tool_calling_model, generate_stable_rag_answer
from rag.hybrid_retrieval import is_low_confidence, retrieve_knowledge
from rag.index_cache import get_knowledge_index
from rag.planning import classify_intent as classify_rag_intent
from rag.planning import pre_retrieval_plan
from rag.prompting import build_citations, render_rag_messages
from tools.contracts import TOOL_SPECS
from tools.langchain_tools import build_langchain_tools, tool_action_reason
from tools.planning import classify_intent, extract_order_id
from tools.runtime_context import public_runtime_context


def build_answer(intent: Intent, action: ToolAction | None, observation: ToolObservation | None) -> str:
    """根据澄清状态或工具结果组织用户可读回答。"""
    if action is None:
        if intent in {"order_query", "refund_status_query"}:
            return "我需要订单号才能查询订单、物流或退款进度。"
        return "我没有识别到需要调用的实时业务工具。"
    if observation is None:
        return "工具没有返回可用结果。"
    if observation.status == "success":
        return f"我通过工具查到：{observation.summary}"
    return f"这次工具没有查到可用事实：{observation.summary}"


def should_route_to_realtime_tool(intent: Intent, user_message: str) -> bool:
    """区分实时事实查询和稳定知识查询，避免 Tool Calling 吃掉 RAG 场景。"""
    if intent in {"order_query", "refund_status_query"}:
        return True
    if intent != "product_consult":
        return False
    realtime_product_terms = ["库存", "价格", "多少钱", "还有货", "有没有货"]
    stable_policy_terms = ["活动", "优惠", "券", "满减", "折扣", "叠加"]
    return any(term in user_message for term in realtime_product_terms) and not any(
        term in user_message for term in stable_policy_terms
    )


class Step18Agent:
    """基础 Tool Calling 版 Agent。"""

    def __init__(self) -> None:
        self._message_count_by_session: dict[str, int] = {}

    def _run_langchain_tool_calling(
        self,
        request: ChatRequest,
        intent: Intent,
    ) -> tuple[str, list[ToolCallRecord], dict[str, Any]]:
        """用 LangChain create_agent 执行本步的工具调用闭环。

        项目重点：LangChain 负责生成 AIMessage.tool_calls、执行 StructuredTool，
        并产生 ToolMessage。下面返回的 tool_calls 是调试后台观察格式，
        是从 LangChain 消息整理出来的，不是 LangChain 原生响应对象。
        """
        tools = build_langchain_tools(request)
        model = create_tool_calling_model()
        system_prompt = (
            "你是古河商城公司的客服 Agent。需要实时订单、物流、商品或退款事实时，"
            "必须优先调用给定工具确认事实。不要编造物流、库存或退款状态。"
            "工具执行前后都由后端校验参数和当前用户身份。"
        )
        langchain_agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
        )
        log_guhe_event("AGENT_CREATED", "LangChain Agent 已绑定本步工具", observe=True, available_tools=[tool.name for tool in tools])
        model_label = self._model_label(model)
        log_model_input(
            model=model_label,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": request.user_message}],
            prompt_source="Step18Agent._run_langchain_tool_calling",
        )
        result = langchain_agent.invoke(
            {"messages": [{"role": "user", "content": request.user_message}]},
            # 单轮工具调用只需要 用户消息 -> tool call -> observation -> 最终回答；仍设置上限防止循环。
            config={"recursion_limit": 4},
        )
        log_guhe_event("AGENT_INVOKED", "LangChain 工具调用循环执行完成", observe=True, message_count=len(result["messages"]))
        messages = result["messages"]
        final_answer = next(
            (str(message.content) for message in reversed(messages) if isinstance(message, AIMessage) and message.content),
            build_answer(intent, None, None),
        )
        log_model_output(model=model_label, content=final_answer)
        # 调试后台适配层：把 LangChain 原始消息整理成前端容易展示的 Action / Observation。
        # 生产系统可以选择记录 LangChain messages、Trace、审计日志或 LangSmith，不必照搬这个结构。
        tool_calls = self._tool_calls_from_langchain_messages(messages)
        log_guhe_event("TOOL_OBSERVED", "Action 与 Observation 已配对", observe=True, tool_names=[record.action.tool_name for record in tool_calls], call_count=len(tool_calls))
        langchain_state = {
            "model": model_label,
            "create_agent": True,
            "available_tools": [tool.name for tool in tools],
            "message_types": [message.__class__.__name__ for message in messages],
        }
        return final_answer, tool_calls, langchain_state

    @staticmethod
    def _model_label(model: BaseChatModel) -> str:
        """返回公开可展示的模型名称，不包含 Key 或私有配置。"""
        for attr in ("model_name", "model"):
            value = getattr(model, attr, None)
            if value:
                return str(value)
        return model.__class__.__name__

    def _tool_calls_from_langchain_messages(self, messages: list[BaseMessage]) -> list[ToolCallRecord]:
        """把 LangChain AIMessage / ToolMessage 转成本步调试后台的 tool_calls。

        这是项目观察台适配层：AIMessage.tool_calls 提供 action，ToolMessage.content
        提供 observation；本函数把它们配对成 ToolCallRecord，方便前端和学习者
        验证工具是否真的被调用。它不是生产系统使用 LangChain 的必需模板。
        """
        records: list[ToolCallRecord] = []
        observations_by_id: dict[str, ToolObservation] = {}
        for message in messages:
            if not isinstance(message, ToolMessage):
                continue
            try:
                observations_by_id[str(message.tool_call_id)] = ToolObservation.model_validate_json(str(message.content))
            except Exception:
                continue

        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for tool_call in message.tool_calls:
                tool_name = str(tool_call["name"])
                observation = observations_by_id.get(str(tool_call["id"]))
                if observation is None:
                    continue
                records.append(
                    ToolCallRecord(
                        action=ToolAction(
                            tool_name=tool_name,
                            arguments=dict(tool_call.get("args") or {}),
                            reason=tool_action_reason(tool_name),
                        ),
                        observation=observation,
                    )
                )
        return records

    def _run_stable_rag(self, request: ChatRequest) -> tuple[str, Intent, list[Citation], dict[str, Any]]:
        """稳定规则和政策问题继续沿用 Hybrid RAG，不交给实时工具猜。"""
        intent = classify_rag_intent(request.user_message)
        plan = pre_retrieval_plan(request, intent)
        index = get_knowledge_index()
        hits, retrieval_debug = retrieve_knowledge(plan, index)
        log_guhe_event("RAG_RETRIEVED", "稳定知识检索完成", observe=True, candidate_count=len(hits))
        reliable_hits = [] if is_low_confidence(hits) else hits
        citations = build_citations(reliable_hits)
        if reliable_hits:
            model_answer = generate_stable_rag_answer(render_rag_messages(request, plan, reliable_hits, index))
            answer = model_answer.answer
            model_answer_state = model_answer.model_dump()
        else:
            answer = "这个问题没有找到足够可靠的古河商城规则依据，不能直接给出结论。"
            model_answer_state = {
                "used_model": False,
                "model_name": None,
                "fallback_reason": "low_confidence_no_model_answer",
                "source": "stable_rag_guardrail",
            }
        rag_state = {
            "mode": "hybrid_rag_with_index_cache",
            "index_version": index.version,
            "plan": plan.model_dump(),
            "retrieved_count": len(hits),
            "citation_count": len(citations),
            "low_confidence": not bool(reliable_hits),
            "retrieval_debug": retrieval_debug,
            "model_answer": model_answer_state,
        }
        return answer, intent, citations, rag_state

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给项目 Agent 编排。"""
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别本轮意图", observe=True, intent=intent)
        rag_state: dict[str, Any] | None = None
        citations: list[Citation] = []
        if intent in {"order_query", "refund_status_query"} and not extract_order_id(request.user_message):
            answer = build_answer(intent, None, None)
            tool_calls = []
            langchain_state = {
                "create_agent": False,
                "skip_reason": "missing_required_order_id",
                "available_tools": [spec.name for spec in TOOL_SPECS.values()],
                "message_types": [],
            }
        else:
            should_use_realtime_tool = should_route_to_realtime_tool(intent, request.user_message)
            log_guhe_event("ROUTE_SELECTED", "已选择实时工具或稳定知识路径", observe=True, route="tool" if should_use_realtime_tool else "rag")
            if should_use_realtime_tool:
                answer, tool_calls, langchain_state = self._run_langchain_tool_calling(request, intent)
            else:
                answer, intent, citations, rag_state = self._run_stable_rag(request)
                tool_calls = []
                langchain_state = {
                    "create_agent": False,
                    "skip_reason": "stable_knowledge_routed_to_rag",
                    "available_tools": [spec.name for spec in TOOL_SPECS.values()],
                    "message_types": [],
                }
        planned_action = tool_calls[0].action if tool_calls else None
        observation = tool_calls[0].observation if tool_calls else None

        reasoning_summary = [
            "Agent 使用 LangChain create_agent 接收本轮候选工具。",
            "LangChain 只生成工具名和参数，当前用户身份仍来自 runtime_user_id。",
            "后端工具执行层完成参数校验、订单归属校验，并把结果作为 Observation 返回。",
            "tool_calls 是调试后台为了观察 Action / Observation 整理出的项目视图，不是 LangChain 原生格式。",
        ]
        session_state = {
            "agent_version": "step-18-tool-calling",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "tool_calling": {
                # 这些字段给调试后台展示本轮工具候选、动作和 Observation。
                # 它们帮助学习者观察链路，不代表用户端或生产 API 必须暴露同样结构。
                "available_tools": [spec.model_dump() for spec in TOOL_SPECS.values()],
                "planned_action": planned_action.model_dump() if planned_action else None,
                "observation": observation.model_dump() if observation else None,
                "langchain": langchain_state,
            },
            "rag": rag_state,
            "next_gap": "工具能执行了，但用户没给订单号或出现多个候选订单时，Agent 还缺少工具调用前澄清。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            tool_calls=tool_calls,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
