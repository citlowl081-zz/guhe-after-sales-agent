"""意图分类客户端层，封装规则优先和轻量模型兜底的结构化分类。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import json
import os
from typing import Any

import httpx
from pydantic import ValidationError

from api.schemas import Intent, IntentResult
from config.settings import api_key_is_missing, load_guhe_env


def build_classifier_messages(user_message: str) -> list[dict[str, str]]:
    """构造轻量分类模型 messages，只要求模型返回粗意图 JSON。"""

    allowed_intents = ", ".join(Intent.__args__)  # type: ignore[attr-defined]
    # 项目重点：分类器只输出粗意图 JSON，不允许顺手生成客服承诺或售后动作。
    return [
        {
            "role": "system",
            "content": (
                "你是古河商城客服 Agent 的轻量意图分类器。"
                "只输出 JSON，不要输出 Markdown。"
                "你只能判断用户消息的大类，不能执行工具、不能批准退款、不能承诺售后动作。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请把下面用户消息分成一个粗意图。\n"
                f"允许的 intent 只能是：{allowed_intents}\n"
                "输出 JSON 格式：{\"intent\": string, \"confidence\": number, \"explanation\": string}\n\n"
                f"用户消息：{user_message}"
            ),
        },
    ]


def parse_classifier_json(content: str) -> dict[str, Any] | None:
    """兼容分类模型直接返回 JSON 或包一层 ```json 代码块的情况。"""

    text = content.strip()
    if text.startswith("```"):
        # 模型偶尔会把 JSON 包进代码块；解析层负责兼容格式，但不放宽字段校验责任。
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def classify_intent_with_model(
    user_message: str,
    *,
    http_client: httpx.Client | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> IntentResult | None:
    """规则不确定时调用轻量分类模型补齐粗意图。"""

    load_guhe_env()
    if os.getenv("AGENT_GUHE_DISABLE_LLM") == "1" and http_client is None:
        return None
    resolved_api_key = api_key or os.getenv("AGENT_OPENAI_API_KEY")
    if api_key_is_missing(resolved_api_key):
        # 分类模型不可用时回到规则兜底，不因为环境缺失硬猜一个业务意图。
        return None

    resolved_base_url = (base_url or os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")).rstrip("/")
    resolved_model = model or os.getenv("AGENT_CLASSIFIER_MODEL") or os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")
    # 这里调用的是轻量分类模型，不是客服主回答模型；职责越窄，后续越容易评测。
    request_kwargs = {
        "headers": {"Authorization": f"Bearer {resolved_api_key}", "Content-Type": "application/json"},
        "json": {"model": resolved_model, "messages": build_classifier_messages(user_message)},
    }
    log_model_input(model=resolved_model, messages=request_kwargs["json"]["messages"], prompt_source=__file__)
    try:
        if http_client is not None:
            response = http_client.post(f"{resolved_base_url}/chat/completions", **request_kwargs)
        else:
            response = httpx.post(f"{resolved_base_url}/chat/completions", **request_kwargs, timeout=30)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        log_model_output(model=resolved_model, content=content)
        payload = parse_classifier_json(content)
        if payload is not None:
            return IntentResult(
                intent=payload.get("intent", "unknown"),
                source="classifier",
                confidence=float(payload.get("confidence", 0.7)),
                matched_keywords=[],
                explanation=str(payload.get("explanation") or "分类模型给出粗意图兜底。"),
            )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValidationError, ValueError):
        # 模型超时、服务失败或结构化输出不合法，都回到上层 rules_fallback/unknown。
        return None
    return None
