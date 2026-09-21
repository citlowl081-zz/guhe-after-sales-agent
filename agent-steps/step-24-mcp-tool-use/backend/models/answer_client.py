"""真实大模型回答层。

模型只基于 Tool/RAG/Workflow 已确认的受控事实组织客服话术；失败时才回退到确定性话术。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import json
import os
from typing import Any

from pydantic import BaseModel

from config.settings import load_guhe_env


class GroundedAnswerResult(BaseModel):
    """最终回答生成结果，用于 session_state 展示模型是否真实参与。"""

    answer: str
    used_model: bool = False
    model_name: str | None = None
    fallback_reason: str | None = None


def _api_key_is_missing(api_key: str | None) -> bool:
    if not api_key:
        return True
    normalized = api_key.strip()
    return normalized in {"", "你的模型平台 Key", "your-api-key", "your_api_key", "YOUR_API_KEY", "sk-your-api-key", "sk-xxx", "替换成你的真实Key"}


def _model_name() -> str:
    return os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")


def _public_payload(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: _public_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_public_payload(item) for item in value]
    return value


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def compose_grounded_answer(
    *,
    user_message: str,
    deterministic_answer: str,
    facts: dict[str, Any] | None = None,
    citations: list[Any] | None = None,
    workflow: Any = None,
    risk_level: str | None = None,
    next_action: str | None = None,
    skip_model: bool = False,
    skip_reason: str | None = None,
) -> GroundedAnswerResult:
    """调用真实模型生成基于证据的客服话术；模型不可用时返回确定性兜底。"""
    load_guhe_env()
    if skip_model:
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason=skip_reason or "model_skipped_by_boundary")
    if os.getenv("AGENT_GUHE_DISABLE_LLM") == "1":
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason="test_model_disabled")

    api_key = os.getenv("AGENT_OPENAI_API_KEY")
    if _api_key_is_missing(api_key):
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason="model_config_missing")

    try:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=_model_name(),
            api_key=api_key,
            base_url=os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/"),
            temperature=0.2,
        )
        payload = {
            "user_message": user_message,
            "facts": _public_payload(facts or {}),
            "citations": _public_payload(citations or []),
            "workflow": _public_payload(workflow),
            "risk_level": risk_level,
            "next_action": next_action,
            "fallback_answer": deterministic_answer,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是古河商城公司的客服 Agent。只能根据 JSON 里的受控事实、引用和 workflow 状态回复。"
                    "不得编造订单、物流、库存、活动、退款或审批结果；高风险售后必须保留人工审批边界。"
                    "如果信息不足，要说明需要补充或转人工。回答要自然、简洁。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        log_model_input(model=_model_name(), messages=messages, prompt_source=__file__)
        response = model.invoke(messages)
        answer = _content_text(getattr(response, "content", response)).strip()
        log_model_output(model=_model_name(), content=answer)
        if not answer:
            return GroundedAnswerResult(answer=deterministic_answer, model_name=_model_name(), fallback_reason="empty_model_answer")
        return GroundedAnswerResult(answer=answer, used_model=True, model_name=_model_name())
    except Exception as exc:
        return GroundedAnswerResult(answer=deterministic_answer, model_name=_model_name(), fallback_reason=exc.__class__.__name__)


def compose_model_answer(observation: Any, user_message: str) -> str:
    """让真实模型基于安全 Observation 组织回答。"""
    if "模型抽风" in user_message:
        raise ModelServiceError("模型服务暂时不可用。")
    deterministic = (
        f"我通过只读工具查到：{observation.summary}"
        if getattr(observation, "status", None) == "success"
        else f"工具没有返回可用事实：{observation.summary}"
    )
    result = compose_grounded_answer(
        user_message=user_message,
        deterministic_answer=deterministic,
        facts={"observation": observation},
        risk_level=getattr(observation, "risk_level", None),
        next_action=getattr(observation, "next_action", None),
    )
    if not result.used_model and result.fallback_reason not in {"test_model_disabled"}:
        raise ModelServiceError(result.fallback_reason or "model_unavailable")
    return result.answer
