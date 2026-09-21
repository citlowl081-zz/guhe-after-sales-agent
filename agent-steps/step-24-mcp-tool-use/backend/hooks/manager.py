"""Step 24：Hooks 治理层。工具前、工具后、错误和完成事件在这里统一记录。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.runtime_context import public_runtime_context


class HookManager:
    """工具生命周期治理点。

    项目重点：Step 24 里 MCP 只改变工具来源，Hooks 仍然守在 Tool Use
    执行链路外侧，负责脱敏、错误归一和完成摘要。
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
        event = HookEvent(
            hook_type="pre_tool_call",
            target_name=action.tool_name,
            action="validate_mcp_tool_arguments",
            result="blocked" if missing else "allowed",
            reason="MCP 提供工具 schema，pre_tool_call 仍负责统一校验参数和可信运行时身份。",
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
            action="sanitize_mcp_tool_observation",
            result="sanitized" if redacted or pollution_detected else "passed",
            reason="MCP 工具结果仍要经过 Observation 安全摘要，不能直接当 Prompt 规则使用。",
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
            action="normalize_mcp_tool_error",
            result="degraded",
            reason="MCP 只是工具来源，工具异常仍然由 Hooks 归一成降级信号。",
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
            action="summarize_mcp_tool_governance",
            result="completed",
            reason="本轮请求结束时输出公开治理摘要，证明 MCP 工具仍走 Tool Use 和 Hooks。",
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
