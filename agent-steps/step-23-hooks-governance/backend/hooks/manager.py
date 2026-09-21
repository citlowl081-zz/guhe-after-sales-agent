"""Step 23：Hooks 治理层。工具前、工具后、错误和完成事件在这里统一记录。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.runtime_context import public_runtime_context


class HookManager:
    """工具生命周期治理点。

    项目重点：Hook 只管工具调用前后的统一治理、错误降级和公开安全摘要。
    它不会批准退款，不会启动人工审批，也不会保存完整 Trace。
    """

    _PHONE = re.compile(r"\b1[3-9]\d{9}\b")
    _EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
    _SECRET = re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*[\w.\-]{6,}")
    _POLLUTION_MARKERS = ("忽略规则", "跳过审批", "直接退款", "ignore previous", "override system")

    def __init__(self) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self.events: list[HookEvent] = []
        self.touched_tools: set[str] = set()
        self.redacted_count = 0
        self.degraded_count = 0
        self.risk_hit_count = 0
        self.tool_count = 0

    def pre_tool_call(self, action: ToolAction, request: ChatRequest, spec: ToolSpec) -> HookEvent:
        """工具执行前统一校验参数、可信上下文和高风险边界。"""
        self.tool_count += 1
        self.touched_tools.add(action.tool_name)
        missing = [field for field in spec.required if not action.arguments.get(field)]
        safe_arguments, redacted_args = self.redact(action.arguments)
        safe_page_context, redacted_context = self.redact(public_runtime_context(request))
        safe_message_preview, redacted_message = self.redact(request.user_message[:240])
        redacted = redacted_args or redacted_context or redacted_message
        if redacted:
            self.redacted_count += 1
        result = "blocked" if missing else "allowed"
        event = HookEvent(
            hook_type="pre_tool_call",
            target_name=action.tool_name,
            action="validate_arguments_and_runtime_context",
            result=result,
            reason="工具调用前统一校验必填参数，并只写入可信运行时身份和脱敏参数摘要。",
            safe_summary={
                "tool": action.tool_name,
                "required": spec.required,
                "missing": missing,
                "arguments": safe_arguments,
                "runtime_user_id": request.runtime_user_id,
                "page_context": safe_page_context,
                "message_preview": safe_message_preview,
                "read_only": spec.read_only,
                "risk_level": spec.risk_level,
            },
            redacted=redacted,
        )
        self.events.append(event)
        return event

    def post_tool_call(self, observation: Observation) -> Observation:
        """工具执行后统一压缩 Observation，避免原始返回直接进入回答。"""
        safe_summary, redacted_summary = self.redact(observation.summary)
        safe_facts, redacted_facts = self.redact(observation.facts)
        pollution_detected = self._detect_pollution(str(safe_summary))
        if pollution_detected:
            safe_summary = self._neutralize_pollution(str(safe_summary))
        redacted = redacted_summary or redacted_facts
        if redacted:
            self.redacted_count += 1
        event = HookEvent(
            hook_type="post_tool_call",
            target_name=observation.tool_name,
            action="sanitize_observation",
            result="sanitized" if redacted or pollution_detected else "passed",
            reason="工具结果回到 Agent 前统一生成安全 Observation 摘要，不把原始内部返回直接交给模型。",
            safe_summary={
                "tool": observation.tool_name,
                "status": observation.status,
                "observation_preview": str(safe_summary)[:180],
                "fact_keys": sorted(safe_facts) if isinstance(safe_facts, dict) else [],
                "omitted_fields": observation.omitted_fields,
                "pollution_detected": pollution_detected,
            },
            redacted=redacted,
        )
        self.events.append(event)
        return observation.model_copy(update={"summary": str(safe_summary), "facts": safe_facts})

    def on_error(self, tool_name: str, error_category: ErrorCategory, message: str, attempts: int) -> HookEvent:
        """把工具异常归一成可解释的降级事件，方便前端和测试观察。"""
        self.degraded_count += 1
        safe_message, redacted = self.redact(message)
        if redacted:
            self.redacted_count += 1
        event = HookEvent(
            hook_type="on_error",
            target_name=tool_name,
            action="normalize_error_for_degradation",
            result="degraded",
            reason="工具或模型异常被归一成公开可读的降级原因，不让客服链路跟着编事实。",
            safe_summary={"error_category": error_category, "attempts": attempts, "message": safe_message},
            redacted=redacted,
            degraded=True,
        )
        self.events.append(event)
        return event

    def on_completion(self, *, next_action: NextAction, risk_level: RiskLevel, degradation: DegradationState) -> HookCompletion:
        """在一轮请求结束时生成公开治理摘要，不暴露隐藏推理链。"""
        if risk_level == "high":
            self.risk_hit_count += 1
        safe_summary = {
            "next_action": next_action,
            "risk_level": risk_level,
            "degraded": degradation.degraded,
            "error_category": degradation.error_category,
            "touched_tools": sorted(self.touched_tools),
        }
        event = HookEvent(
            hook_type="on_completion",
            target_name="chat_request",
            action="summarize_tool_governance",
            result="completed",
            reason="本轮请求结束时输出公开治理摘要，供调试和测试观察，不输出隐藏推理链。",
            safe_summary=safe_summary,
            degraded=degradation.degraded,
        )
        self.events.append(event)
        return HookCompletion(
            hook_count=len(self.events),
            tool_count=self.tool_count,
            touched_tools=sorted(self.touched_tools),
            redacted_count=self.redacted_count,
            degraded_count=self.degraded_count,
            risk_hit_count=self.risk_hit_count,
            safe_summary=safe_summary,
        )

    def redact(self, value: Any) -> tuple[Any, bool]:
        """对外输出前统一脱敏，保护订单、用户和内部治理细节。"""
        if isinstance(value, dict):
            redacted = False
            safe: dict[str, Any] = {}
            for key, item in value.items():
                safe_item, item_redacted = self.redact(item)
                safe[key] = safe_item
                redacted = redacted or item_redacted
            return safe, redacted
        if isinstance(value, list):
            redacted = False
            safe_items = []
            for item in value:
                safe_item, item_redacted = self.redact(item)
                safe_items.append(safe_item)
                redacted = redacted or item_redacted
            return safe_items, redacted
        if not isinstance(value, str):
            return value, False
        safe = self._PHONE.sub("[phone-redacted]", value)
        safe = self._EMAIL.sub("[email-redacted]", safe)
        safe = self._SECRET.sub(lambda match: f"{match.group(1)}=[secret-redacted]", safe)
        return safe, safe != value

    def _detect_pollution(self, text: str) -> bool:
        """识别工具返回里可能污染回答边界的提示注入片段。"""
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in self._POLLUTION_MARKERS)

    def _neutralize_pollution(self, text: str) -> str:
        """把可疑提示注入内容替换成安全摘要，保留证据但不执行指令。"""
        cleaned = text
        for marker in self._POLLUTION_MARKERS:
            cleaned = re.sub(re.escape(marker), "[external-instruction-neutralized]", cleaned, flags=re.IGNORECASE)
        return cleaned
