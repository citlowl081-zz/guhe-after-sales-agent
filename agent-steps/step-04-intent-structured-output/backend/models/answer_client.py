"""Step 04：基于结构化意图的真实模型回答层。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import json
import os
from typing import Any

import httpx
from pydantic import BaseModel

from config.settings import api_key_is_missing, load_guhe_env


class GroundedAnswerResult(BaseModel):
    """记录最终回答是否由真实模型生成。"""

    answer: str
    used_model: bool = False
    model_name: str | None = None
    fallback_reason: str | None = None


def compose_grounded_answer(*, user_message: str, deterministic_answer: str, intent_result: Any) -> GroundedAnswerResult:
    """让模型基于结构化意图生成克制客服回复；失败时回退到项目边界话术。"""
    load_guhe_env()
    if os.getenv("AGENT_GUHE_DISABLE_LLM") == "1":
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason="test_model_disabled")

    api_key = os.getenv("AGENT_OPENAI_API_KEY")
    if api_key_is_missing(api_key):
        return GroundedAnswerResult(answer=deterministic_answer, fallback_reason="model_config_missing")

    base_url = os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    model = os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")
    payload = {
        "user_message": user_message,
        "intent_result": intent_result.model_dump() if hasattr(intent_result, "model_dump") else intent_result,
        "fallback_answer": deterministic_answer,
        "step_boundary": "当前只完成粗意图识别，不能承诺订单、规则、退款、赔偿或人工流转结果。",
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是古河商城公司的客服 Agent。只能根据结构化 intent_result 和项目边界回复；"
                "不得编造订单、物流、活动规则、退款资格、赔偿或人工流转结果。"
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
        answer = response.json()["choices"][0]["message"]["content"].strip()
        log_model_output(model=model, content=answer)
        if not answer:
            return GroundedAnswerResult(answer=deterministic_answer, model_name=model, fallback_reason="empty_model_answer")
        return GroundedAnswerResult(answer=answer, used_model=True, model_name=model)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        return GroundedAnswerResult(answer=deterministic_answer, model_name=model, fallback_reason=exc.__class__.__name__)
