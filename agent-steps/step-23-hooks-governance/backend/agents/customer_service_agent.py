"""Step 23：Agent 编排层。每步只在这里串联已经长出的模块。"""

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


class Step23Agent:
    """Hooks 治理版 Agent。"""

    def __init__(self) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给 Agent 编排。"""
        load_guhe_env()
        hooks = HookManager()
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别实时业务意图", observe=True, intent=intent)
        risk_level = classify_risk(intent, request.user_message)

        if risk_level == "high":
            degradation = DegradationState(degraded=True, error_category="high_risk_write_blocked", fallback_message=fallback_answer("high_risk_write_blocked"))
            completion = hooks.on_completion(next_action="transfer_to_human", risk_level=risk_level, degradation=degradation)
            session_state = self._session_state(request, message_count, None, None, None, degradation, hooks, completion)
            return ChatResponse(
                session_id=request.session_id,
                answer=degradation.fallback_message or "",
                intent=intent,
                citations=[],
                tool_calls=[],
                hook_events=hooks.events,
                hook_completion=completion,
                next_action="transfer_to_human",
                risk_level=risk_level,
                needs_human_approval=True,
                degraded=True,
                reasoning_summary=["Hooks 可以记录高风险边界，但不能替代 HITL 审批。", "当前只返回转人工口径，不启动审批流程。"],
                session_state=session_state,
            )

        clarification = pre_tool_clarification(request, intent)
        action = None if clarification else plan_tool_action(request, intent)
        log_guhe_event("TOOL_PLANNED", "工具动作与前置澄清已确定", observe=True, tool_name=getattr(action, "tool_name", None), needs_clarification=clarification is not None)
        tool_result = None
        observation = None
        if action:
            spec = TOOL_SPECS[action.tool_name]
            hooks.pre_tool_call(action, request, spec)
            log_guhe_event("HOOK_PRE_TOOL", "pre_tool_call Hook 已完成参数与风险检查", observe=True, tool_name=action.tool_name)
            tool_result = execute_tool_action(action, request)
            observation = hooks.post_tool_call(build_observation(tool_result))
            log_guhe_event("HOOK_POST_TOOL", "post_tool_call Hook 已治理 Observation", observe=True, status=observation.status, next_action=observation.next_action)
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
        log_guhe_event("HOOK_COMPLETE", "on_completion Hook 已记录本轮完成状态", observe=True, next_action=next_action)
        session_state = self._session_state(request, message_count, clarification, action, observation, degradation, hooks, completion)
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=[],
            tool_calls=tool_calls,
            hook_events=hooks.events,
            hook_completion=completion,
            clarification=clarification,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=False,
            degraded=degradation.degraded,
            reasoning_summary=[
                "工具调用前由 pre_tool_call 统一校验参数和 Runtime Context。",
                "工具调用后由 post_tool_call 统一生成安全 Observation 摘要。",
                "异常交给 on_error 归一降级，本轮结束交给 on_completion 生成公开治理摘要。",
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
    ) -> dict[str, Any]:
        """取得当前会话的轻量状态，避免把临时记忆散落在处理流程里。"""
        return {
            "agent_version": "step-23-hooks-governance",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "tool_calling": {
                "tool_specs": [spec.model_dump() for spec in TOOL_SPECS.values()],
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
            "next_gap": "工具链路有了统一治理点；但工具定义仍写死在这一版客服 Agent 里，下一步要解释 MCP 为什么适合在 Tool Use 成熟后出现。",
        }

def health() -> dict[str, str]:
    """返回当前步程健康状态。"""
    return {"status": "ok", "step": "23"}

def capabilities() -> dict[str, Any]:
    """返回当前步程能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给 Agent 编排。"""
    return agent.chat(request)
