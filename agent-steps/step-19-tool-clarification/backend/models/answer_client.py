"""Step 19：工具澄清和工具结果后的真实模型回答层。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import json
import os
from typing import Any

import httpx
from pydantic import BaseModel

from config.settings import _api_key_is_missing, load_guhe_env


class GroundedAnswerResult(BaseModel):
    answer: str
    used_model: bool = False
    model_name: str | None = None
    fallback_reason: str | None = None


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def compose_grounded_answer(
    *,
    user_message: str,
    deterministic_answer: str,
    facts: dict[str, Any],
    skip_model: bool = False,
    skip_reason: str | None = None,
) -> GroundedAnswerResult:
    """用真实模型基于澄清计划和 Observation 生成回答；需要澄清时保留确定性问题。"""
    load_guhe_env()
    if skip_model:
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason=skip_reason or "model_skipped")
    if os.getenv("AGENT_GUHE_DISABLE_LLM") == "1":
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason="test_model_disabled")

    api_key = os.getenv("AGENT_OPENAI_API_KEY")
    if _api_key_is_missing(api_key):
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason="model_config_missing")

    base_url = os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    model = os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")
    payload = {"user_message": user_message, "facts": _dump(facts), "fallback_answer": deterministic_answer}
    messages = [
        {
            "role": "system",
            "content": (
                "你是古河商城公司的客服 Agent。只能基于 ClarificationPlan、Tool Action 和 Observation 回复；"
                "不得绕过必填参数澄清，不得编造工具没有返回的事实。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    log_model_input(model=model, messages=messages, prompt_source=__file__)
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.2,
            },
            timeout=30,
        )
        response.raise_for_status()
        answer = log_model_output(model=model, content=response.json()["choices"][0]["message"]["content"].strip())
        if not answer:
            return GroundedAnswerResult(answer=deterministic_answer, model_name=model, fallback_reason="empty_model_answer")
        return GroundedAnswerResult(answer=answer, used_model=True, model_name=model)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        return GroundedAnswerResult(answer=deterministic_answer, model_name=model, fallback_reason=exc.__class__.__name__)
