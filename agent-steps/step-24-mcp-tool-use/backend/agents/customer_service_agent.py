"""Step 24：Agent 编排层。每步只在这里串联已经长出的模块。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from typing import Any, Literal

from api.schemas import *
from config.settings import *
from tools.runtime_context import *
from integrations.ecommerce_client import *
from tools.contracts import *
from tools.planning import *
from tools.tool_runtime import *
from observability.observation import *
from degradation.fallbacks import *
from models.answer_client import *
from hooks.manager import HookManager
from mcp_catalog.catalog import MCP_CATALOG


class Step24Agent:
    """MCP 与 Tool Use 关系版 Agent。"""

    def __init__(self) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给 Agent 编排。"""
        load_guhe_env()
        hooks = HookManager()
        tool_specs = MCP_CATALOG.to_tool_specs()
        log_guhe_event("MCP_DISCOVERED", "已从 MCP Catalog 发现本步工具", observe=True, tool_names=list(tool_specs))
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别实时业务意图", observe=True, intent=intent)
        risk_level = classify_risk(intent, request.user_message)

        if risk_level == "high":
            mcp_context = MCP_CATALOG.binding_summary(None, risk_level)
            degradation = DegradationState(degraded=True, error_category="high_risk_write_blocked", fallback_message=fallback_answer("high_risk_write_blocked"))
            completion = hooks.on_completion(next_action="transfer_to_human", risk_level=risk_level, degradation=degradation)
            session_state = self._session_state(request, message_count, None, None, None, degradation, hooks, completion, mcp_context)
            return ChatResponse(
                session_id=request.session_id,
                answer=degradation.fallback_message or "",
                intent=intent,
                citations=[],
                tool_calls=[],
                hook_events=hooks.events,
                hook_completion=completion,
                mcp_context=mcp_context,
                next_action="transfer_to_human",
                risk_level=risk_level,
                needs_human_approval=True,
                degraded=True,
                reasoning_summary=["MCP 可以提供高风险边界资源和转人工 Prompt。", "它不能替代后续 HITL 审批，也不能把退款变成普通工具调用。"],
                session_state=session_state,
            )

        clarification = pre_tool_clarification(request, intent)
        action = None if clarification else plan_tool_action(request, intent, tool_specs)
        log_guhe_event("TOOL_PLANNED", "已按 MCP Schema 生成工具动作", observe=True, tool_name=getattr(action, "tool_name", None), needs_clarification=clarification is not None)
        mcp_context = MCP_CATALOG.binding_summary(action, risk_level)
        tool_result = None
        observation = None
        if action:
            spec = tool_specs[action.tool_name]
            hooks.pre_tool_call(action, request, spec)
            log_guhe_event("HOOK_PRE_TOOL", "调用前 Hook 已校验 MCP 工具动作", observe=True, tool_name=action.tool_name)
            tool_result = execute_tool_action(action, request, tool_specs)
            observation = hooks.post_tool_call(build_observation(tool_result))
            log_guhe_event("MCP_OBSERVATION", "MCP 工具结果已治理为 Observation", observe=True, status=observation.status, next_action=observation.next_action)
            if observation.status == "error":
                hooks.on_error(action.tool_name, observation.error_category, observation.summary, tool_result.attempts)

        degradation = DegradationState(degraded=False, error_category="none")
        answer = "我没有识别到需要调用的实时业务工具。"
        if clarification:
            answer = f"{clarification.message} " + "；".join(f"{candidate.value}（{candidate.hint}）" for candidate in clarification.candidates)
        elif observation:
            if observation.status == "error" and observation.error_category == "timeout":
                degradation = DegradationState(
                    degraded=True,
                    error_category="timeout",
                    retry_count=(tool_result.attempts - 1) if tool_result else 0,
                    fallback_message=fallback_answer("timeout"),
                )
                answer = degradation.fallback_message or ""
            else:
                try:
                    answer = compose_model_answer(observation, request.user_message)
                except ModelServiceError as error:
                    degradation = DegradationState(degraded=True, error_category="model_unavailable", fallback_message=fallback_answer("model_unavailable"))
                    hooks.on_error("__model_service__", "model_unavailable", str(error), 1)
                    answer = f"{degradation.fallback_message} {observation.summary}"

        next_action: NextAction = "ask_clarification" if clarification else observation.next_action if observation else "fallback_answer"
        if degradation.degraded and degradation.error_category in {"timeout", "model_unavailable"}:
            next_action = "fallback_answer"
        tool_calls = [ToolCallRecord(action=action, observation=observation, attempts=tool_result.attempts)] if action and observation and tool_result else []
        completion = hooks.on_completion(next_action=next_action, risk_level=risk_level, degradation=degradation)
        session_state = self._session_state(request, message_count, clarification, action, observation, degradation, hooks, completion, mcp_context)
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=[],
            tool_calls=tool_calls,
            hook_events=hooks.events,
            hook_completion=completion,
            mcp_context=mcp_context,
            clarification=clarification,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=False,
            degraded=degradation.degraded,
            reasoning_summary=[
                "MCP 目录提供工具说明、边界资源和 Observation Prompt。",
                "Tool Use 仍负责选择工具、填参数、执行工具和组织 Observation。",
                "Hooks 继续负责调用前后治理、异常降级和完成摘要。",
            ],
            session_state=session_state,
        )

    def _session_state(
        self,
        request: ChatRequest,
        message_count: int,
        clarification: ClarificationRequest | None,
        action: ToolAction | None,
        observation: Observation | None,
        degradation: DegradationState,
        hooks: HookManager,
        completion: HookCompletion,
        mcp_context: MCPBindingSummary,
    ) -> dict[str, Any]:
        """取得当前会话的轻量状态，避免把临时记忆散落在处理流程里。"""
        return {
            "agent_version": "step-24-mcp-tool-use",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "mcp": {
                "tool_source": mcp_context.tool_source,
                "available_tools": mcp_context.available_tools,
                "selected_tool": mcp_context.selected_tool,
                "resources": mcp_context.resources,
                "prompts": mcp_context.prompts,
                "boundary": mcp_context.boundary,
            },
            "tool_calling": {
                "tool_specs": [spec.model_dump() for spec in MCP_CATALOG.to_tool_specs().values()],
                "clarification": clarification.model_dump() if clarification else None,
                "planned_action": action.model_dump() if action else None,
                "observation": observation.model_dump() if observation else None,
            },
            "hooks": {
                "events": [event.model_dump() for event in hooks.events],
                "completion": completion.model_dump(),
                "safe_trace_summary": completion.safe_summary,
            },
            "degradation": degradation.model_dump(),
            "next_gap": "工具链路有了统一治理点，工具定义也能从标准化来源复用；但退款、补偿、取消订单这类动钱动作，仍然必须交给后续受控流程和人工审批。",
        }

def health() -> dict[str, str]:
    """返回当前步程健康状态。"""
    return {"status": "ok", "step": "24"}

def capabilities() -> dict[str, Any]:
    """返回当前步程能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给 Agent 编排。"""
    return agent.chat(request)
